from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch

import numpy as np

from opendbc.car.structs import car
from openpilot.cereal import log
from openpilot.selfdrive.addon.traffic_controller import TrafficStopPlan
from openpilot.selfdrive.controls.lib.accel_boost import AccelBoost
from openpilot.selfdrive.controls.lib.longcontrol import LongCtrlState
from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib import long_mpc
from openpilot.selfdrive.controls.lib.longitudinal_planner import LongitudinalPlanner
from openpilot.selfdrive.modeld.constants import ModelConstants


def make_radar_lead(distance=20.0, speed=8.0, accel=0.0, prob=0.95):
  return SimpleNamespace(present=True, dRel=distance, vLead=speed, aLeadK=accel, aLeadTau=1.5, modelProb=prob)


def make_boost_inputs(personality=log.LongitudinalPersonality.aggressive, plan_ahead=2.0, model_accel=0.5):
  v_ego = 12.0
  model = log.ModelDataV2.new_message()
  model.position.x = [0.0, (v_ego + plan_ahead) * 10.0]
  model.position.t = [0.0, 10.0]
  model.action.desiredAcceleration = model_accel
  return {
    'carState': car.CarState(vEgo=v_ego, vCruise=100.0),
    'controlsState': log.ControlsState(longControlState=LongCtrlState.pid),
    'modelV2': model,
    'radarState': log.RadarState(),
    'selfdriveState': log.SelfdriveState(personality=personality),
    'vehicleParameters': log.VehicleParameters(),
  }


class TestAccelBoost(unittest.TestCase):
  def test_curve_drops_bias_immediately_and_bias_recovers_gradually(self):
    boost = AccelBoost(dt=0.05)
    sm = make_boost_inputs()
    for _ in range(60):
      output = boost.update(sm, 0.5, 0.0)
    self.assertAlmostEqual(boost.update(sm, 0.4, -0.1), 0.4)
    recovered = boost.update(sm, 0.5, 0.0)
    self.assertAlmostEqual(recovered, 0.5 + 0.1 * 0.5 + 0.15 * 0.05 / 1.05)
    self.assertLess(recovered, output)

  def test_invalid_trajectory_drops_boost_and_clears_filters(self):
    cases = (([], []), ([0.0, 140.0], []), ([0.0, 140.0], [0.0, 0.0]),
             ([0.0, float('nan')], [0.0, 10.0]), ([0.0, 140.0], [0.0, float('inf')]))
    for x, t in cases:
      with self.subTest(x=x, t=t):
        boost = AccelBoost(dt=0.05)
        sm = make_boost_inputs()
        first_output = boost.update(sm, 0.5, 0.0)
        self.assertGreater(boost.update(sm, 0.5, 0.0), first_output)
        sm['modelV2'].position.x = x
        sm['modelV2'].position.t = t
        self.assertEqual(boost.update(sm, 0.5, 0.0), 0.5)
        self.assertAlmostEqual(boost.update(make_boost_inputs(), 0.5, 0.0), first_output)


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

  def test_planner_can_use_second_radar_lead_without_a_primary_lead_indicator(self):
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
    self.assertFalse(message.longitudinalPlan.hasLead)
    self.assertEqual(message.longitudinalPlan.longitudinalPlanSource, long_mpc.LongitudinalPlanSource.lead1)

  def test_planner_scales_accel_boost_by_personality(self):
    personalities = (
      (log.LongitudinalPersonality.aggressive, 0.15, 0.10),
      (log.LongitudinalPersonality.standard, 0.10, 1 / 15),
      (log.LongitudinalPersonality.relaxed, 0.075, 0.05),
      (log.LongitudinalPersonality.morerelaxed, 0.05, 1 / 30),
    )
    cases = ((2.0, 0.5, 1.0), (0.25, 0.5, 0.5), (-0.5, 0.5, 0.0), (2.0, -0.5, 1.0))
    dt = 0.10
    self.enterContext(patch('openpilot.selfdrive.controls.lib.longitudinal_planner.get_accel_from_plan', return_value=1.0))
    self.enterContext(patch('openpilot.selfdrive.controls.lib.longitudinal_planner.get_cruise_accel', return_value=1.0))
    for personality, bias, gain in personalities:
      for plan_ahead, model_accel, gate in cases:
        with self.subTest(personality=personality, plan_ahead=plan_ahead, model_accel=model_accel):
          sm = make_boost_inputs(personality, plan_ahead, model_accel)
          planner = LongitudinalPlanner(car.CarParams(openpilotLongitudinalControl=True), init_v=12.0, dt=dt)
          planner.update(sm)

          expected_boost = gate * (bias * dt / (1.0 + dt) + gain * max(model_accel, 0.0))
          self.assertAlmostEqual(planner.output_a_target, model_accel + expected_boost)

  def test_planner_resets_boost_on_driver_input_or_stop_request(self):
    cases = (('carState', 'gasPressed'), ('carState', 'brakePressed'),
             ('controlsState', 'forceDecel'), ('modelV2', 'shouldStop'))
    for service, field in cases:
      with self.subTest(service=service, field=field):
        sm = make_boost_inputs(model_accel=-0.5)
        planner = LongitudinalPlanner(car.CarParams(openpilotLongitudinalControl=True), init_v=12.0)
        planner.update(sm)
        self.assertGreater(planner.output_a_target, -0.5)

        target = sm[service].action if service == 'modelV2' else sm[service]
        setattr(target, field, True)
        planner.update(sm)

        self.assertEqual(planner.output_a_target, -0.5)
        self.assertEqual(planner.accel_boost.bias_filter.x, 0.0)
        self.assertFalse(planner.accel_boost.plan_ahead_filter.initialized)

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
