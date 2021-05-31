import os
import math

import cereal.messaging as messaging
from common.numpy_fast import clip, interp
from selfdrive.swaglog import cloudlog
from common.realtime import sec_since_boot
from selfdrive.controls.lib.radar_helpers import _LEAD_ACCEL_TAU
from selfdrive.controls.lib.longitudinal_mpc import libmpc_py
from selfdrive.controls.lib.drive_helpers import MPC_COST_LONG
from selfdrive.config import Conversions as CV

LOG_MPC = os.environ.get('LOG_MPC', False)

CRUISE_GAP_BP = [1., 2., 3., 4.]
CRUISE_GAP_V = [1.2, 1.5, 2.1, 2.73]

AUTO_TR_BP = [40.*CV.KPH_TO_MS, 60.*CV.KPH_TO_MS, 80.*CV.KPH_TO_MS, 100.*CV.KPH_TO_MS, 130.*CV.KPH_TO_MS]
AUTO_TR_V = [1.2, 1.35, 1.6, 2., 2.7]

AUTO_TR_ENABLED = True
AUTO_TR_CRUISE_GAP = 1

class LongitudinalMpc():
  def __init__(self, mpc_id):
    self.mpc_id = mpc_id

    self.setup_mpc()
    self.v_mpc = 0.0
    self.v_mpc_future = 0.0
    self.a_mpc = 0.0
    self.v_cruise = 0.0
    self.prev_lead_status = False
    self.prev_lead_x = 0.0
    self.new_lead = False

    self.last_cloudlog_t = 0.0
    self.n_its = 0
    self.duration = 0

    self.cruise_gap = 0
    self.auto_tr = AUTO_TR_ENABLED

  def publish(self, pm):
    if LOG_MPC:
      qp_iterations = max(0, self.n_its)
      dat = messaging.new_message('liveLongitudinalMpc')
      dat.liveLongitudinalMpc.xEgo = list(self.mpc_solution[0].x_ego)
      dat.liveLongitudinalMpc.vEgo = list(self.mpc_solution[0].v_ego)
      dat.liveLongitudinalMpc.aEgo = list(self.mpc_solution[0].a_ego)
      dat.liveLongitudinalMpc.xLead = list(self.mpc_solution[0].x_l)
      dat.liveLongitudinalMpc.vLead = list(self.mpc_solution[0].v_l)
      dat.liveLongitudinalMpc.cost = self.mpc_solution[0].cost
      dat.liveLongitudinalMpc.aLeadTau = self.a_lead_tau
      dat.liveLongitudinalMpc.qpIterations = qp_iterations
      dat.liveLongitudinalMpc.mpcId = self.mpc_id
      dat.liveLongitudinalMpc.calculationTime = self.duration
      pm.send('liveLongitudinalMpc', dat)

  def setup_mpc(self):
    ffi, self.libmpc = libmpc_py.get_libmpc(self.mpc_id)
    self.libmpc.init(MPC_COST_LONG.TTC, MPC_COST_LONG.DISTANCE,
                     MPC_COST_LONG.ACCELERATION, MPC_COST_LONG.JERK)

    self.mpc_solution = ffi.new("log_t *")
    self.cur_state = ffi.new("state_t *")
    self.cur_state[0].v_ego = 0
    self.cur_state[0].a_ego = 0
    self.a_lead_tau = _LEAD_ACCEL_TAU

  def set_cur_state(self, v, a):
    self.cur_state[0].v_ego = v
    self.cur_state[0].a_ego = a

  def update(self, CS, lead):
    v_ego = CS.vEgo

    # Setup current mpc state
    self.cur_state[0].x_ego = 0.0

    if lead is not None and lead.status:
      x_lead = lead.dRel
      v_lead = max(0.0, lead.vLead)
      a_lead = lead.aLeadK

      dist_cost = interp(lead.dRel, [4., 20.], [MPC_COST_LONG.DISTANCE/2., MPC_COST_LONG.DISTANCE])
      dist_cost = interp(v_ego, [60.*CV.KPH_TO_MS, 80.*CV.KPH_TO_MS], [dist_cost, MPC_COST_LONG.DISTANCE])
      self.libmpc.set_weights(MPC_COST_LONG.TTC, dist_cost, MPC_COST_LONG.ACCELERATION, MPC_COST_LONG.JERK)

      if (v_lead < 0.1 or -a_lead / 2.0 > v_lead):
        v_lead = 0.0
        a_lead = 0.0

      self.a_lead_tau = lead.aLeadTau
      self.new_lead = False
      if not self.prev_lead_status or abs(x_lead - self.prev_lead_x) > 2.5:
        self.libmpc.init_with_simulation(self.v_mpc, x_lead, v_lead, a_lead, self.a_lead_tau)
        self.new_lead = True

      self.prev_lead_status = True
      self.prev_lead_x = x_lead
      self.cur_state[0].x_l = x_lead
      self.cur_state[0].v_l = v_lead
    else:
      self.prev_lead_status = False
      # Fake a fast lead car, so mpc keeps running
      self.cur_state[0].x_l = 50.0
      self.cur_state[0].v_l = v_ego + 10.0
      a_lead = 0.0
      self.a_lead_tau = _LEAD_ACCEL_TAU

      self.libmpc.set_weights(MPC_COST_LONG.TTC, MPC_COST_LONG.DISTANCE, MPC_COST_LONG.ACCELERATION, MPC_COST_LONG.JERK)

    # Calculate mpc
    t = sec_since_boot()

    cruise_gap = int(clip(CS.cruiseGap, 1., 4.))

    if self.auto_tr and cruise_gap == AUTO_TR_CRUISE_GAP:
      TR = interp(v_ego, AUTO_TR_BP, AUTO_TR_V)
    else:
      TR = interp(float(cruise_gap), CRUISE_GAP_BP, CRUISE_GAP_V)

    self.n_its = self.libmpc.run_mpc(self.cur_state, self.mpc_solution, self.a_lead_tau, a_lead, TR)
    self.duration = int((sec_since_boot() - t) * 1e9)

    # Get solution. MPC timestep is 0.2 s, so interpolation to 0.05 s is needed
    self.v_mpc = self.mpc_solution[0].v_ego[1]
    self.a_mpc = self.mpc_solution[0].a_ego[1]
    self.v_mpc_future = self.mpc_solution[0].v_ego[10]

    # Reset if NaN or goes through lead car
    crashing = any(lead - ego < -50 for (lead, ego) in zip(self.mpc_solution[0].x_l, self.mpc_solution[0].x_ego))
    nans = any(math.isnan(x) for x in self.mpc_solution[0].v_ego)
    backwards = min(self.mpc_solution[0].v_ego) < -0.01

    if ((backwards or crashing) and self.prev_lead_status) or nans:
      if t > self.last_cloudlog_t + 5.0:
        self.last_cloudlog_t = t
        cloudlog.warning("Longitudinal mpc %d reset - backwards: %s crashing: %s nan: %s" % (
                          self.mpc_id, backwards, crashing, nans))

      self.libmpc.init(MPC_COST_LONG.TTC, MPC_COST_LONG.DISTANCE,
                       MPC_COST_LONG.ACCELERATION, MPC_COST_LONG.JERK)
      self.cur_state[0].v_ego = v_ego
      self.cur_state[0].a_ego = 0.0
      self.v_mpc = v_ego
      self.a_mpc = CS.aEgo
      self.prev_lead_status = False
