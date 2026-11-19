from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from openpilot.cereal import log
from openpilot.selfdrive.addon.traffic_controller import TrafficStopPlan
from openpilot.selfdrive.controls.lib.longitudinal_mpc_lib import long_mpc


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

  def test_model_trajectory_is_anchored_to_radar(self):
    radar_lead = make_radar_lead(speed=10.0)
    model = log.ModelDataV2.new_message()
    model_lead = model.init('leadsV3', 1)[0]
    model_lead.x = (80.0 + 5.0 * long_mpc.LEAD_T_IDXS_MODEL).tolist()
    model_lead.v = (30.0 - long_mpc.LEAD_T_IDXS_MODEL).tolist()

    trajectory = self.mpc.process_lead(model_lead, radar_lead)

    np.testing.assert_allclose(trajectory[:, 0], radar_lead.dRel + 5.0 * long_mpc.T_IDXS)
    np.testing.assert_allclose(trajectory[:, 1], radar_lead.vLead - long_mpc.T_IDXS)

  def test_unusable_model_trajectory_falls_back_to_radar(self):
    radar_lead = make_radar_lead(accel=-1.0)
    radar_trajectory = np.column_stack(self.mpc.extrapolate_lead(radar_lead.dRel, radar_lead.vLead,
                                                                radar_lead.aLeadK, radar_lead.aLeadTau))
    model_x = 80.0 + 5.0 * long_mpc.LEAD_T_IDXS_MODEL
    model_v = 30.0 - long_mpc.LEAD_T_IDXS_MODEL
    unusable = (
      ('missing', None, 0.95),
      ('short position', SimpleNamespace(x=model_x[:-1], v=model_v), 0.95),
      ('short velocity', SimpleNamespace(x=model_x, v=model_v[:-1]), 0.95),
      ('nonfinite position', SimpleNamespace(x=np.r_[model_x[:-1], np.nan], v=model_v), 0.95),
      ('nonfinite velocity', SimpleNamespace(x=model_x, v=np.r_[model_v[:-1], np.inf]), 0.95),
      ('low confidence', SimpleNamespace(x=model_x, v=model_v), 0.5),
    )
    for case, model_lead, prob in unusable:
      with self.subTest(case=case):
        radar_lead.modelProb = prob
        np.testing.assert_allclose(self.mpc.process_lead(model_lead, radar_lead), radar_trajectory)

  def test_update_with_second_radar_lead_and_missing_model_slot(self):
    radar_state = SimpleNamespace(leadOne=self.no_lead, leadTwo=make_radar_lead(speed=0.0))

    for lead_count in (0, 1):
      with self.subTest(model_lead_count=lead_count):
        model = log.ModelDataV2.new_message()
        model.init('leadsV3', lead_count)
        self.mpc.update(model, radar_state, car_state=self.car_state)

        self.assertEqual(self.mpc.source, long_mpc.LongitudinalPlanSource.lead1)
        np.testing.assert_allclose(self.mpc.params[:, 2], radar_state.leadTwo.dRel)

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
