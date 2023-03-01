#include "safety_hyundai_common.h"

#define HYUNDAI_COMMUNITY_LIMITS(steer, rate_up, rate_down) { \
  .max_steer = (steer), \
  .max_rate_up = (rate_up), \
  .max_rate_down = (rate_down), \
  .max_rt_delta = 112, \
  .max_rt_interval = 250000, \
  .driver_torque_allowance = 50, \
  .driver_torque_factor = 2, \
  .type = TorqueDriverLimited, \
   /* the EPS faults when the steering angle is above a certain threshold for too long. to prevent this, */ \
   /* we allow setting CF_Lkas_ActToi bit to 0 while maintaining the requested torque value for two consecutive frames */ \
  .min_valid_request_frames = 89, \
  .max_invalid_request_frames = 2, \
  .min_valid_request_rt_interval = 810000,  /* 810ms; a ~10% buffer on cutting every 90 frames */ \
  .has_steer_req_tolerance = true, \
}

const SteeringLimits HYUNDAI_COMMUNITY_STEERING_LIMITS = HYUNDAI_COMMUNITY_LIMITS(409, 10, 10);

const LongitudinalLimits HYUNDAI_COMMUNITY_LONG_LIMITS = {
  .max_accel = 200,   // 1/100 m/s2
  .min_accel = -350,  // 1/100 m/s2
};

bool lcan_bus1, fwd_bus1 = false;
bool fwd_bus2 = true;
int lkas11_bus0_cnt, lcan_bus1_cnt, mdps12_cnt, Last_StrColTq = 0;
int lkas11_op, mdps12_op, clu11_op, scc12_op, ems11_op = 0;
int eps_bus, scc_bus, mdps12_chksum = -1;

const CanMsg HYUNDAI_COMMUNITY_TX_MSGS[] = {
  {593, 2, 8},    // MDPS12, Bus 2
  {790, 1, 8},    // EMS11, Bus 1
  {832, 0, 8},    // LKAS11, Bus 0
  {832, 1, 8},    // LKAS11, Bus 1
  {1056, 0, 8},   // SCC11, Bus 0
  {1057, 0, 8},   // SCC12, Bus 0
  {1290, 0, 8},   // SCC13, Bus 0
  {905, 0, 8},    // SCC14, Bus 0
  {909, 0, 8},    // FCA11 Bus 0
  {1155, 0, 8},   // FCA12 Bus 0
  {1157, 0, 4},   // LFAHDA_MFC, Bus 0
  {1186, 0, 8},   // FRT_RADAR11, Bus 0
  {1265, 0, 4},   // CLU11, Bus 0
  {1265, 1, 4},   // CLU11, Bus 1
  {1265, 2, 4},   // CLU11, Bus 2
//  {897, 0, 8},    // MDPS11, Bus 0
//  {912, 0, 7},    // SPAS11, Bus 0
//  {912, 1, 7},    // SPAS11, Bus 1
//  {1268, 0, 8},   // SPAS12, Bus 0
//  {1268, 1, 8},   // SPAS12, Bus 1
};

// older hyundai models have less checks due to missing counters and checksums
AddrCheckStruct hyundai_community_addr_checks[] = {
  {.msg = {{608, 0, 8, .check_checksum = true, .max_counter = 3U, .expected_timestep = 10000U},                    // EMS16
           {881, 0, 8, .expected_timestep = 10000U}, { 0 }}},                                                      // E_EMS11
  {.msg = {{902, 0, 8, .expected_timestep = 20000U}, { 0 }, { 0 }}},                                               // WHL_SPD11
  {.msg = {{916, 0, 8, .expected_timestep = 20000U}, { 0 }, { 0 }}},                                               // TCS13
  //{.msg = {{1057, 0, 8, .check_checksum = true, .max_counter = 15U, .expected_timestep = 20000U}, { 0 }, { 0 }}},  // SCC12
};
#define HYUNDAI_COMMUNITY_ADDR_CHECK_LEN (sizeof(hyundai_community_addr_checks) / sizeof(hyundai_community_addr_checks[0]))

addr_checks hyundai_community_rx_checks = {hyundai_community_addr_checks, HYUNDAI_COMMUNITY_ADDR_CHECK_LEN};

static uint8_t hyundai_community_get_counter(CANPacket_t *to_push) {
  int addr = GET_ADDR(to_push);

  uint8_t cnt;
  if (addr == 608) {  // EMS16
    cnt = (GET_BYTE(to_push, 7) >> 4) & 0x3U;
  } else if (addr == 902) {  // WHL_SPD11
    cnt = ((GET_BYTE(to_push, 3) >> 6) << 2) | (GET_BYTE(to_push, 1) >> 6);
  } else if (addr == 916) {  // TCS13
    cnt = (GET_BYTE(to_push, 1) >> 5) & 0x7U;
  } else if (addr == 1057) {  // SCC12
    cnt = GET_BYTE(to_push, 7) & 0xFU;
  } else if (addr == 1265) {  // CLU11
    cnt = (GET_BYTE(to_push, 3) >> 4) & 0xFU;
  } else {
    cnt = 0;
  }
  return cnt;
}

static uint32_t hyundai_community_get_checksum(CANPacket_t *to_push) {
  int addr = GET_ADDR(to_push);

  uint8_t chksum;
  if (addr == 608) {  // EMS16
    chksum = GET_BYTE(to_push, 7) & 0xFU;
  } else if (addr == 902) {  // WHL_SPD11
    chksum = ((GET_BYTE(to_push, 7) >> 6) << 2) | (GET_BYTE(to_push, 5) >> 6);
  } else if (addr == 916) {  // TCS13
    chksum = GET_BYTE(to_push, 6) & 0xFU;
  } else if (addr == 1057) {  // SCC12
    chksum = GET_BYTE(to_push, 7) >> 4;
  } else {
    chksum = 0;
  }
  return chksum;
}

static uint32_t hyundai_community_compute_checksum(CANPacket_t *to_push) {
  int addr = GET_ADDR(to_push);

  uint8_t chksum = 0;
  if (addr == 902) {  // WHL_SPD11
    // count the bits
    for (int i = 0; i < 8; i++) {
      uint8_t b = GET_BYTE(to_push, i);
      for (int j = 0; j < 8; j++) {
        uint8_t bit = 0;
        // exclude checksum and counter
        if (((i != 1) || (j < 6)) && ((i != 3) || (j < 6)) && ((i != 5) || (j < 6)) && ((i != 7) || (j < 6))) {
          bit = (b >> (uint8_t)j) & 1U;
        }
        chksum += bit;
      }
    }
    chksum = (chksum ^ 9U) & 15U;
  } else {
    // sum of nibbles
    for (int i = 0; i < 8; i++) {
      if ((addr == 916) && (i == 7)) {  // TCS13
        continue; // exclude
      }
      uint8_t b = GET_BYTE(to_push, i);
      if (((addr == 608) && (i == 7)) || ((addr == 916) && (i == 6)) || ((addr == 1057) && (i == 7))) {  // EMS16, TCS13, SCC12
        b &= (addr == 1057) ? 0x0FU : 0xF0U;  // SCC12 remove checksum
      }
      chksum += (b % 16U) + (b / 16U);
    }
    chksum = (16U - (chksum % 16U)) % 16U;
  }
  return chksum;
}

static int hyundai_community_rx_hook(CANPacket_t *to_push) {
  bool valid = addr_safety_check(to_push, &hyundai_community_rx_checks,
                                 hyundai_community_get_checksum, hyundai_community_compute_checksum,
                                 hyundai_community_get_counter);
  int bus = GET_BUS(to_push);
  int addr = GET_ADDR(to_push);

  bool is_mdps12_msg = (addr == 593);
  bool is_lkas11_msg = (addr == 832);
  bool is_scc11_msg = (addr == 1056);
  bool is_scc12_msg = (addr == 1057);
  bool is_clu11_msg = (addr == 1265);

  if (!valid) {
    print("  CAN RX invalid addr : ["); puth(addr); print("]\n");
  }

  if ((bus == 1) && lcan_bus1) {
    valid = false;
  }

  // check LCAN (RADAR_TRACK) on Bus [1]
  if ((bus == 1) && ((addr == 1296) || (addr == 524))) {
    lcan_bus1_cnt = 500;
    if (fwd_bus1 || !lcan_bus1) {
      lcan_bus1 = true;
      fwd_bus1 = false;
      print("  forwarding disabled : LCAN on bus ["); puth(bus); print("]\n");
    }
  }

  // check LKAS11 on Bus
  if (is_lkas11_msg) {
    if ((bus == 0) && fwd_bus2) {
      fwd_bus2 = false;
      lkas11_bus0_cnt = 20;
      print("  forwarding disabled : LKAS11 on bus ["); puth(bus); print("]\n");
    }
    if (bus == 2) {
      if (lkas11_bus0_cnt > 0) {
        lkas11_bus0_cnt--;
      } else if (!fwd_bus2) {
        fwd_bus2 = true;
        print("  forwarding enabled : LKAS11 on bus ["); puth(bus); print("]\n");
      }
      if (lcan_bus1_cnt > 0) {
        lcan_bus1_cnt--;
      } else if (lcan_bus1) {
        lcan_bus1 = false;
        print("  LCAN not on bus [1]\n");
      }
    }
  }

  // check MDPS12 on Bus
  if (is_mdps12_msg && (eps_bus != bus)) {
    if ((bus != 1) || !lcan_bus1) {
      eps_bus = bus;
      if (bus == 1) {
        print("  EPS on bus ["); puth(bus); print("]\n");
        if (!fwd_bus1 && !lcan_bus1) {
          fwd_bus1 = true;
          print("  forwarding enabled : EPS on bus ["); puth(bus); print("]\n");
        }
      }
    }
  }

  // check SCC11, SCC12 on Bus
  if ((is_scc11_msg || is_scc12_msg) && (scc_bus != bus)) {
    if ((bus != 1) || !lcan_bus1) {
      scc_bus = bus;
      if (bus == 1) {
        print("  scc on bus ["); puth(bus); print("]\n");
        if (!fwd_bus1) {
          fwd_bus1 = true;
          print("  forwarding enabled : SCC on bus ["); puth(bus); print("]\n");
        }
      }
      if (bus == 2) {
        print("  SCC on bus ["); puth(bus); print("]\n");
      }
    }
  }

  if (valid && is_scc11_msg && !scc12_op) {
    // 1 bits: 0
    int cruise_engaged = GET_BYTES_04(to_push) & 0x1U; // ACC main_on signal
    hyundai_common_cruise_state_check(cruise_engaged);
  }

  /*if (valid && (is_scc12_msg && (bus == 0)) || (bus == 2)) {
    // 2 bits: 13-14
    int cruise_engaged = (GET_BYTES_04(to_push) >> 13) & 0x3U;
    hyundai_common_cruise_state_check(cruise_engaged);
  }*/

  /*if (valid && (addr == 608) && (bus == 0) && (scc_bus == -1) && (!scc12_op)) {  // EMS16
    // bit 25
    int cruise_engaged = (GET_BYTES_04(to_push) >> 25 & 0x1); // ACC main_on signal
        hyundai_common_cruise_state_check(cruise_engaged);
  }*/

  if (valid && (bus == eps_bus)) {
    if (is_mdps12_msg) {
      int torque_driver_new = ((GET_BYTES_04(to_push) & 0x7ffU) * 0.79) - 808; // scale down new driver torque signal to match previous one
      // update array of samples
      update_sample(&torque_driver, torque_driver_new);
    }

    // ACC steering wheel buttons
    if (is_clu11_msg) {
      int cruise_button = GET_BYTE(to_push, 0) & 0x7U;
      int main_button = GET_BIT(to_push, 3U);
      hyundai_common_cruise_buttons_check(cruise_button, main_button);
    }

    // sample wheel speed, averaging opposite corners
    if (addr == 902) {  // WHL_SPD11
      uint32_t hyundai_speed = (GET_BYTES_04(to_push) & 0x3FFFU) + ((GET_BYTES_48(to_push) >> 16) & 0x3FFFU);  // FL + RR
      hyundai_speed /= 2;
      vehicle_moving = hyundai_speed > HYUNDAI_STANDSTILL_THRSLD;
    }

    bool stock_ecu_detected = (is_lkas11_msg);

    // If openpilot is controlling longitudinal we need to ensure the radar is turned off
    // Enforce by checking we don't see SCC12
    if (hyundai_longitudinal && (is_scc12_msg)) {
      stock_ecu_detected = true;
    }
    generic_rx_checks(stock_ecu_detected);
  }
  return valid;
}

static int hyundai_community_tx_hook(CANPacket_t *to_send) {
  int tx = 1;
  int addr = GET_ADDR(to_send);
  int bus = GET_BUS(to_send);

  bool is_mdps12_msg = (addr == 593);
  bool is_ems11_msg = (addr == 790);
  bool is_lkas11_msg = (addr == 832);
  bool is_scc12_msg = (addr == 1057);
  bool is_clu11_msg = (addr == 1265);

  if (!msg_allowed(to_send, HYUNDAI_COMMUNITY_TX_MSGS, sizeof(HYUNDAI_COMMUNITY_TX_MSGS) / sizeof(HYUNDAI_COMMUNITY_TX_MSGS[0]))) {
    tx = 0;
    print("  CAN TX not allowed: ["); puth(addr); print("], ["); puth(bus); print("]\n");
  }

  // FCA11: Block any potential actuation
  if (addr == 909) {
    int CR_VSM_DecCmd = GET_BYTE(to_send, 1);
    int FCA_CmdAct = GET_BIT(to_send, 20U);
    int CF_VSM_DecCmdAct = GET_BIT(to_send, 31U);

    if ((CR_VSM_DecCmd != 0) || (FCA_CmdAct != 0) || (CF_VSM_DecCmdAct != 0)) {
      tx = 0;
      print("violation[FCA11, 909]\n");
    }
  }

  // ACCEL: safety check
  if (is_scc12_msg) {
    int desired_accel_raw = (((GET_BYTE(to_send, 4) & 0x7U) << 8) | GET_BYTE(to_send, 3)) - 1023U;
    int desired_accel_val = ((GET_BYTE(to_send, 5) << 3) | (GET_BYTE(to_send, 4) >> 5)) - 1023U;
    int aeb_decel_cmd = GET_BYTE(to_send, 2);
    int aeb_req = GET_BIT(to_send, 54U);
    bool violation = false;

    violation |= longitudinal_accel_checks(desired_accel_raw, HYUNDAI_COMMUNITY_LONG_LIMITS);
    violation |= longitudinal_accel_checks(desired_accel_val, HYUNDAI_COMMUNITY_LONG_LIMITS);
    violation |= (aeb_decel_cmd != 0);
    violation |= (aeb_req != 0);

    if (violation) {
      tx = 0;
      print("violation[SCC12, 1057]\n");
    }
  }

  // LKA STEER: safety check
  if (is_lkas11_msg) {
    lkas11_op = 20;
    uint32_t ts = microsecond_timer_get();
    int desired_torque = ((GET_BYTES_04(to_send) >> 16) & 0x7ffU) - 1024U;
    bool violation = false;

    if (controls_allowed) {
      // *** global torque limit check ***
      violation |= max_limit_check(desired_torque, HYUNDAI_COMMUNITY_STEERING_LIMITS.max_steer, -HYUNDAI_COMMUNITY_STEERING_LIMITS.max_steer);

      // ready to blend in limits
      desired_torque_last = MAX(-HYUNDAI_STEERING_LIMITS.max_steer + 50, MIN(desired_torque, HYUNDAI_STEERING_LIMITS.max_steer - 50));
      rt_torque_last = desired_torque;
      ts_torque_check_last = ts;
    }

    // no torque if controls is not allowed
    if (!controls_allowed && (desired_torque != 0)) {
      violation = true;
      print("  lkas torque not allowed : controls not allowed!\n");
    }

    // reset to 0 if either controls is not allowed or there's a violation
    if (violation || !controls_allowed) {
      valid_steer_req_count = 0;
      invalid_steer_req_count = 0;
      desired_torque_last = 0;
      rt_torque_last = 0;
      ts_torque_check_last = ts;
      ts_steer_req_mismatch_last = ts;
    }

    if (violation) {
      tx = 0;
      print("violation[LKAS11, 832]\n");
      print("  lkas torque allowed : controls allowed!\n");
    }

    /*bool steer_req = GET_BIT(to_send, 27U) != 0U;
    const SteeringLimits limits = HYUNDAI_COMMUNITY_STEERING_LIMITS;
    //if (steer_torque_cmd_checks(desired_torque, steer_req, limits)) {
    if (steer_torque_cmd_checks(desired_torque, -1, limits)) {
      tx = 0;
      print("violation[LKAS11, 832]\n");
      print("  lkas torque allowed : controls allowed!\n");
    }*/
  }

  // UDS: Only tester present ("\x02\x3E\x80\x00\x00\x00\x00\x00") allowed on diagnostics address
  /*if (addr == 2000) {
    if ((GET_BYTES_04(to_send) != 0x00803E02U) || (GET_BYTES_48(to_send) != 0x0U)) {
      tx = 0;
    }
  }*/

  // BUTTONS: used for resume spamming and cruise cancellation
  /*if (is_clu11_msg && !hyundai_longitudinal) {
    int button = GET_BYTE(to_send, 0) & 0x7U;

    bool allowed_resume = (button == 1) && controls_allowed;
    bool allowed_set_decel = (button == 2) && controls_allowed;
    bool allowed_cancel = (button == 4) && cruise_engaged_prev;
    if (!(allowed_resume || allowed_set_decel || allowed_cancel)) {
      tx = 0;
    }
  }*/

  if (is_mdps12_msg) { mdps12_op = 20; }
  if (is_ems11_msg) { ems11_op = 20; }
  if (is_scc12_msg) { scc12_op = 20; }
  if (is_clu11_msg && (bus == 1)) { clu11_op = 20; }

  return tx;
}

static int hyundai_community_fwd_hook(int bus_num, CANPacket_t *to_fwd) {
  int bus_fwd = -1;
  int addr = GET_ADDR(to_fwd);
  int fwd_to_bus1 = -1;
  if (fwd_bus1) { fwd_to_bus1 = 1; }

  bool is_mdps12_msg = (addr == 593);
  bool is_ems11_msg = (addr == 790);
  bool is_lkas11_msg = (addr == 832);
  bool is_lfahda_msg = (addr == 1157);
  bool is_clu11_msg = (addr == 1265);
  bool is_scc_msg = (addr == 905) || (addr == 1056) || (addr == 1057) || (addr == 1290);

  // forward LKAS to CCAN
  if (fwd_bus2) {
    if (bus_num == 0) {
      if (!clu11_op || !is_clu11_msg || (eps_bus == 0)) {
        if (!mdps12_op || !is_mdps12_msg) {
          if (!ems11_op || !is_ems11_msg) {
            bus_fwd = fwd_to_bus1 == 1 ? 12 : 2;
          } else {  // OP create EMS11 for MDPS
            bus_fwd = 2;
            ems11_op -= 1;
          }
        } else {  // OP create MDPS12 for LKAS
          bus_fwd = fwd_to_bus1;
          mdps12_op -= 1;
        }
      } else {  // OP create CLU11 for MDPS
        bus_fwd = 2;
        clu11_op -= 1;
      }
    }
    if ((bus_num == 1) && fwd_bus1) {
      if (!mdps12_op || !is_mdps12_msg) {
        if (!scc12_op || !is_scc_msg) {
          bus_fwd = 20;
        } else {  // OP create SCC11 SCC12 SCC13 SCC14 for Car
          bus_fwd = 2;
          scc12_op -= 1;
        }
      } else {  // OP create MDPS for LKAS
        bus_fwd = 0;
        mdps12_op -= 1;
      }
    }
    if (bus_num == 2) {
      if ((!lkas11_op || !is_lkas11_msg) && !is_lfahda_msg) {
        if (!scc12_op || !is_scc_msg) {
          bus_fwd = fwd_to_bus1 == 1 ? 10 : 0;
        } else {  // OP create SCC11 SCC12 SCC13 SCC14 for Car
          bus_fwd = fwd_to_bus1;
          scc12_op -= 1;
        }
      } else if (eps_bus == 0) {  // OP create LKAS and LFA for Car
        bus_fwd = fwd_to_bus1;
        lkas11_op -= 1;
      } else {  // OP create LKAS and LFA for Car and MDPS
        lkas11_op -= 1;
      }
    }
  } else {
    if (bus_num == 0) {
      bus_fwd = fwd_to_bus1;
    }
    if ((bus_num == 1) && fwd_bus1) {
      bus_fwd = 0;
    }
  }
  return bus_fwd;
}

static const addr_checks* hyundai_community_init(uint16_t param) {
  hyundai_common_init(param);

  hyundai_community_rx_checks = (addr_checks) {hyundai_community_addr_checks, HYUNDAI_COMMUNITY_ADDR_CHECK_LEN};
  return &hyundai_community_rx_checks;
}

const safety_hooks hyundai_community_hooks = {
  .init = hyundai_community_init,
  .rx = hyundai_community_rx_hook,
  .tx = hyundai_community_tx_hook,
  .tx_lin = nooutput_tx_lin_hook,
  .fwd = hyundai_community_fwd_hook,
};