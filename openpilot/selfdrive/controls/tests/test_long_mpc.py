from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

import numpy as np

from opendbc.car.structs import car
from openpilot.cereal import log
from openpilot.selfdrive.addon.traffic_controller import TrafficStopPlan
from openpilot.selfdrive.controls.lib.longcontrol import LongCtrlState
from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib import long_mpc
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
from openpilot.selfdrive.modeld.constants import ModelConstants


def make_radar_lead(distance=20.0, speed=8.0, accel=0.0, prob=0.95):
  return SimpleNamespace(present=True, dRel=distance, vLead=speed, aLeadK=accel, aLeadTau=1.5, modelProb=prob)


class TestLongitudinalMpc(unittest.TestCase):
  def setUp(self):
    solver_class = self.enterContext(patch.object(long_mpc, 'AcadosOcpSolverCython'))
    self.solver = solver_class.return_value
    self.solver.solve.return_value = 0
    self.solver.get_stats.return_value = [0.001]
    self.solver.get.side_effect = lambda _, field: np.zeros(long_mpc.X_DIM if field == 'x' else 1)
    self.mpc = long_mpc.LongitudinalMpc()
    self.car_state = SimpleNamespace(vEgo=0.0, gasPressed=False, leftBlinker=False, steeringAngleDeg=0.0)
    self.no_lead = SimpleNamespace(present=False, modelProb=0.0)

  def test_radar_lead_extrapolates_decaying_acceleration(self):
    lead = make_radar_lead(speed=10.0, accel=-2.0)
    trajectory = self.mpc.process_lead(lead)

    expected_accel = lead.aLeadK * np.exp(-lead.aLeadTau * long_mpc.T_IDXS**2 / 2.0)
    expected_speed = lead.vLead + np.cumsum(long_mpc.T_DIFFS * expected_accel)
    expected_distance = lead.dRel + np.cumsum(long_mpc.T_DIFFS * expected_speed)
    np.testing.assert_allclose(trajectory[:, 0], expected_distance)
    np.testing.assert_allclose(trajectory[:, 1], expected_speed)

  def test_model_lead_predictions_do_not_change_radar_obstacle(self):
    radar_lead = make_radar_lead(speed=0.0)
    radar_state = SimpleNamespace(leadOne=radar_lead, leadTwo=self.no_lead)
    model = log.ModelDataV2.new_message()
    model_lead = model.init('leadsV3', 1)[0]
    model_lead.prob = 0.95
    model_lead.x = [20.0 + 5.0 * t for t in ModelConstants.LEAD_T_IDXS]
    model_lead.v = [5.0] * len(ModelConstants.LEAD_T_IDXS)

    self.mpc.update(model, radar_state, car_state=self.car_state)

    self.assertEqual(self.mpc.source, long_mpc.LongitudinalPlanSource.lead0)
    np.testing.assert_allclose(self.mpc.params[:, 2], radar_lead.dRel)

  def test_update_with_second_radar_lead_without_model_leads(self):
    radar_state = SimpleNamespace(leadOne=self.no_lead, leadTwo=make_radar_lead(speed=0.0))
    self.mpc.update(log.ModelDataV2.new_message(), radar_state, car_state=self.car_state)

    self.assertEqual(self.mpc.source, long_mpc.LongitudinalPlanSource.lead1)
    np.testing.assert_allclose(self.mpc.params[:, 2], radar_state.leadTwo.dRel)

  def test_planner_publishes_second_radar_lead_as_present(self):
    planner = LongitudinalPlanner(car.CarParams(openpilotLongitudinalControl=True), init_v=10.0)
    model = log.ModelDataV2.new_message()
    model.action.desiredAcceleration = 0.5
    radar = log.RadarState(leadTwo=log.RadarState.LeadData(dRel=20.0, present=True))
    states = {
      'carState': car.CarState(vEgo=10.0, vCruise=100.0),
      'controlsState': log.ControlsState(longControlState=LongCtrlState.pid),
      'modelV2': model,
      'radarState': radar,
      'selfdriveState': log.SelfdriveState(personality=log.LongitudinalPersonality.standard),
      'vehicleParameters': log.VehicleParameters(),
    }
    sm = MagicMock(logMonoTime={'modelV2': 0})
    sm.__getitem__.side_effect = states.__getitem__
    sm.all_checks.return_value = True
    pm = Mock()

    planner.update(sm)
    planner.publish(sm, pm)

    pm.send.assert_called_once()
    service, message = pm.send.call_args.args
    self.assertEqual(service, 'longitudinalPlan')
    self.assertTrue(message.longitudinalPlan.hasLead)
    self.assertEqual(message.longitudinalPlan.longitudinalPlanSource, long_mpc.LongitudinalPlanSource.lead1)

  def test_planner_scales_accel_boost_by_personality(self):
    personalities = (
      (log.LongitudinalPersonality.aggressive, 0.10),
      (log.LongitudinalPersonality.standard, 0.075),
      (log.LongitudinalPersonality.relaxed, 0.05),
      (log.LongitudinalPersonality.morerelaxed, 0.025),
    )
    cases = ((12.0, -0.5, 1.0), (7.5, -0.75, 0.25), (5.0, -0.5, 0.0), (12.0, -1.0, 0.0))
    for personality, max_boost in personalities:
      for speed, model_accel, scale in cases:
        with self.subTest(personality=personality, speed=speed, model_accel=model_accel):
          planner = LongitudinalPlanner(car.CarParams(openpilotLongitudinalControl=True), init_v=speed)
          model = log.ModelDataV2.new_message()
          model.action.desiredAcceleration = model_accel
          planner.update({
            'carState': car.CarState(vEgo=speed, vCruise=100.0),
            'controlsState': log.ControlsState(longControlState=LongCtrlState.pid),
            'modelV2': model,
            'radarState': log.RadarState(),
            'selfdriveState': log.SelfdriveState(personality=personality),
            'vehicleParameters': log.VehicleParameters(),
          })

          self.assertAlmostEqual(planner.output_a_target, model_accel + max_boost * scale)

  def test_traffic_stop_input_and_accel_floor(self):
    self.car_state.vEgo = 10.0
    self.mpc.set_cur_state(self.car_state.vEgo, 0.0)
    model = SimpleNamespace(leadsV3=[])
    radar_state = SimpleNamespace(leadOne=self.no_lead, leadTwo=self.no_lead)
    plan = TrafficStopPlan(raw_stop_distance=70.0, stop_distance=20.0, signal_stop_active=True)

    with patch.object(self.mpc.traffic_controller, 'update', return_value=plan) as update_traffic:
      self.mpc.update(model, radar_state, car_state=self.car_state)

      update_traffic.assert_called_once_with(self.car_state, model, radar_state, long_mpc.COMFORT_BRAKE)
      self.assertEqual(self.mpc.source, long_mpc.LongitudinalPlanSource.e2e)
      np.testing.assert_allclose(self.mpc.params[:, 0], -2.2)

      radar_state.leadOne = make_radar_lead(distance=10.0, speed=0.0)
      self.mpc.update(model, radar_state, car_state=self.car_state)

      self.assertEqual(self.mpc.source, long_mpc.LongitudinalPlanSource.lead0)
      np.testing.assert_allclose(self.mpc.params[:, 0], long_mpc.ACCEL_MIN)

  def test_failed_solution_is_not_used_for_fcw(self):
    radar_state = SimpleNamespace(leadOne=make_radar_lead(distance=0.0, speed=0.0), leadTwo=self.no_lead)
    model = SimpleNamespace(leadsV3=[])
    self.mpc.update(model, radar_state, car_state=self.car_state)
    self.assertEqual(self.mpc.crash_cnt, 1)

    self.solver.get.reset_mock()
    self.solver.solve.return_value = 1
    with patch.object(long_mpc.cloudlog, 'warning'):
      self.mpc.update(model, radar_state, car_state=self.car_state)

    self.solver.get.assert_not_called()
    self.assertEqual(self.mpc.crash_cnt, 0)
