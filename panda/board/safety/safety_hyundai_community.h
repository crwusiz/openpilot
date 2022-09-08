const SteeringLimits HYUNDAI_COMMUNITY_STEERING_LIMITS = {
  .max_steer = 384,
  .max_rt_delta = 112,
  .max_rt_interval = 250000,
  .max_rate_up = 3,
  .max_rate_down = 7,
  .driver_torque_allowance = 50,
  .driver_torque_factor = 2,
  .type = TorqueDriverLimited,
};

const int HYUNDAI_COMMUNITY_STANDSTILL_THRSLD = 30;  // ~1kph
const int HYUNDAI_COMMUNITY_MAX_ACCEL = 200;  // 1/100 m/s2
const int HYUNDAI_COMMUNITY_MIN_ACCEL = -350; // 1/100 m/s2

bool LCAN_bus1 = false;
bool fwd_bus1 = false;
bool fwd_bus2 = true;
int LKAS11_bus0_cnt = 0;
int LCAN_bus1_cnt = 0;
int MDPS12_checksum = -1;
int MDPS12_cnt = 0;
int Last_StrColTq = 0;

int LKAS11_op = 0;
int MDPS12_op = 0;
int CLU11_op = 0;
int SCC12_op = 0;
int SCC12_car = 0;
int EMS11_op = 0;
int MDPS_bus = -1;
int SCC_bus = -1;

const CanMsg HYUNDAI_COMMUNITY_TX_MSGS[] = {
  {593, 2, 8},                              // MDPS12, Bus 2
//  {897, 0, 8},                              // MDPS11, Bus 0
  {790, 1, 8},                              // EMS11, Bus 1
  {832, 0, 8}, {832, 1, 8},                 // LKAS11, Bus 0, 1
  {1056, 0, 8},                             // SCC11, Bus 0
  {1057, 0, 8},                             // SCC12, Bus 0
  {1290, 0, 8},                             // SCC13, Bus 0
  {905, 0, 8},                              // SCC14, Bus 0
  {909, 0, 8},                              // FCA11 Bus 0
  {1155, 0, 8},                             // FCA12 Bus 0
  {1157, 0, 4},                             // LFAHDA_MFC, Bus 0
  {1186, 0, 8},                             // FRT_RADAR11, Bus 0
  {1265, 0, 4}, {1265, 1, 4}, {1265, 2, 4}, // CLU11, Bus 0, 1, 2
//  {912, 0, 7}, {912,1, 7},                  // SPAS11, Bus 0, 1
//  {1268, 0, 8}, {1268,1, 8},                // SPAS12, Bus 0, 1
//  {2000, 0, 8},                             // radar UDS TX addr Bus 0 (for radar disable)
 };

// older hyundai models have less checks due to missing counters and checksums
AddrCheckStruct hyundai_community_addr_checks[] = {
  {.msg = {{608, 0, 8, .check_checksum = true, .max_counter = 3U, .expected_timestep = 10000U},                     // EMS16
           {881, 0, 8, .expected_timestep = 10000U}, { 0 }}},                                                       // E_EMS11
  {.msg = {{902, 0, 8, .expected_timestep = 20000U}, { 0 }, { 0 }}},                                              // WHL_SPD11
//  {.msg = {{916, 0, 8, .expected_timestep = 20000U}}},                                                            // TCS13
//  {.msg = {{902, 0, 8, .check_checksum = true, .max_counter = 15U, .expected_timestep = 10000U}, { 0 }, { 0 }}},    // WHL_SPD11
//  {.msg = {{1057, 0, 8, .check_checksum = true, .max_counter = 15U, .expected_timestep = 20000U}, { 0 }, { 0 }}},  // SCC12
//  {.msg = {{1265, 0, 4, .check_checksum = false, .max_counter = 15U, .expected_timestep = 20000U}, { 0 }, { 0 }}}, // CLU11
};

#define HYUNDAI_COMMUNITY_ADDR_CHECK_LEN (sizeof(hyundai_community_addr_checks) / sizeof(hyundai_community_addr_checks[0]))

addr_checks hyundai_community_rx_checks = {hyundai_community_addr_checks, HYUNDAI_COMMUNITY_ADDR_CHECK_LEN};

static uint32_t hyundai_community_get_checksum(CANPacket_t *to_push) {
  int addr = GET_ADDR(to_push);

  uint8_t chksum;
  if (addr == 608) {
    chksum = GET_BYTE(to_push, 7) & 0xFU;
  } else if (addr == 902) {
    chksum = ((GET_BYTE(to_push, 7) >> 6) << 2) | (GET_BYTE(to_push, 5) >> 6);
  } else if (addr == 916) {
    chksum = GET_BYTE(to_push, 6) & 0xFU;
  } else if (addr == 1057) {
    chksum = GET_BYTE(to_push, 7) >> 4;
  } else {
    chksum = 0;
  }
  return chksum;
}

static uint32_t hyundai_community_compute_checksum(CANPacket_t *to_push) {
  int addr = GET_ADDR(to_push);

  uint8_t chksum = 0;
  if (addr == 902) {
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
      if ((addr == 916) && (i == 7)) {
        continue; // exclude
      }
      uint8_t b = GET_BYTE(to_push, i);
      if (((addr == 608) && (i == 7)) || ((addr == 916) && (i == 6)) || ((addr == 1057) && (i == 7))) {
        b &= (addr == 1057) ? 0x0FU : 0xF0U; // remove checksum
      }
      chksum += (b % 16U) + (b / 16U);
    }
    chksum = (16U - (chksum %  16U)) % 16U;
  }
  return chksum;
}

static uint8_t hyundai_community_get_counter(CANPacket_t *to_push) {
  int addr = GET_ADDR(to_push);

  uint8_t cnt;
  if (addr == 608) {
    cnt = (GET_BYTE(to_push, 7) >> 4) & 0x3U;
  } else if (addr == 902) {
    cnt = ((GET_BYTE(to_push, 3) >> 6) << 2) | (GET_BYTE(to_push, 1) >> 6);
  } else if (addr == 916) {
    cnt = (GET_BYTE(to_push, 1) >> 5) & 0x7U;
  } else if (addr == 1057) {
    cnt = GET_BYTE(to_push, 7) & 0xFU;
  } else if (addr == 1265) {
    cnt = (GET_BYTE(to_push, 3) >> 4) & 0xFU;
  } else {
    cnt = 0;
  }
  return cnt;
}

static int hyundai_community_rx_hook(CANPacket_t *to_push) {
  int addr = GET_ADDR(to_push);
  int bus = GET_BUS(to_push);
  bool valid = addr_safety_check(to_push, &hyundai_community_rx_checks, hyundai_community_get_checksum,
                                 hyundai_community_compute_checksum, hyundai_community_get_counter);

  if (!valid) {
    puts("  CAN RX invalid: "); puth(addr); puts("\n");
  }
  if ((bus == 1) && LCAN_bus1) {
    valid = false;
  }

  // check if we have a LCAN on Bus1
  if ((bus == 1) && ((addr == 1296) || (addr == 524))) {
    LCAN_bus1_cnt = 500;
    if (fwd_bus1 || !LCAN_bus1) {
      LCAN_bus1 = true;
      fwd_bus1 = false;
      puts("  LCAN on bus [1] : forwarding disabled\n");
    }
  }

  // check if LKAS11 on Bus0
  if (addr == 832) {
    if ((bus == 0) && fwd_bus2) {
      fwd_bus2 = false;
      LKAS11_bus0_cnt = 20;
      puts("  LKAS11 on bus [0] : forwarding disabled\n");
    }
    if (bus == 2) {
      if (LKAS11_bus0_cnt > 0) {
        LKAS11_bus0_cnt--;
      } else if (!fwd_bus2) {
        fwd_bus2 = true;
        puts("  LKAS11 on bus [2] : forwarding enabled\n");
      }
      if (LCAN_bus1_cnt > 0) {
        LCAN_bus1_cnt--;
      } else if (LCAN_bus1) {
        LCAN_bus1 = false;
        puts("  LCAN not on bus [1]\n");
      }
    }
  }

  // check MDPS12 or MDPS11 on Bus
  if (((addr == 593) || (addr == 897)) && (MDPS_bus != bus)) {
    if ((bus != 1) || !LCAN_bus1) {
      MDPS_bus = bus;
      if (bus == 1) {
        puts("  MDPS on bus [1]\n");
        if (!fwd_bus1 && !LCAN_bus1) {
          fwd_bus1 = true;
          puts("  MDPS on bus [1] forwarding enabled\n");
        }
      }
    }
  }

  // check SCC11 or SCC12 on Bus
  if ((addr == 1056 || addr == 1057) && (SCC_bus != bus)) {
    if ((bus != 1) || !LCAN_bus1) {
      SCC_bus = bus;
      if (bus == 1) {
        puts("  SCC on bus [1]\n");
        if (!fwd_bus1) {
          fwd_bus1 = true;
          puts("  SCC on bus [1] forwarding enabled\n");
        }
      }
      if (bus == 2) {
        puts("  SCC on bus [2]\n");
      }
    }
  }

  // MDPS12
  if (valid) {
    if ((addr == 593) && (bus == MDPS_bus)) {
      int torque_driver_new = ((GET_BYTES_04(to_push) & 0x7ffU) * 0.79) - 808; // scale down new driver torque signal to match previous one
      // update array of samples
      update_sample(&torque_driver, torque_driver_new);
    }

    // for cars without long control ( SCC11 )
    if ((addr == 1056) && !SCC12_op) {
      // 2 bits: 13-14
      int cruise_engaged = GET_BYTES_04(to_push) & 0x1; // ACC main_on signal
      if (cruise_engaged && !cruise_engaged_prev) {
        controls_allowed = 1;
        puts("  SCC without long control : controls allowed\n");
      }
      if (!cruise_engaged) {
        if (controls_allowed) {
          puts("  SCC without long control : controls not allowed\n");
        }
        controls_allowed = 0;
      }
      cruise_engaged_prev = cruise_engaged;
    }

    // cruise control for car with SCC ( EMS16 )
    if ((addr == 608) && (bus == 0) && (SCC_bus == -1) && !SCC12_op) {
      // bit 25
      int cruise_engaged = (GET_BYTES_04(to_push) >> 25 & 0x1); // ACC main_on signal
      if (cruise_engaged && !cruise_engaged_prev) {
        controls_allowed = 1;
        puts("  non-SCC with long control : controls allowed\n");
      }
      if (!cruise_engaged) {
        if (controls_allowed) {
          puts("  non-SCC with long control : controls not allowed\n");
        }
        controls_allowed = 0;
      }
      cruise_engaged_prev = cruise_engaged;
    }

    // sample wheel speed, averaging opposite corners ( WHL_SPD11 )
    if (addr == 902) {
      int hyundai_speed = (GET_BYTES_04(to_push) & 0x3FFFU) + ((GET_BYTES_48(to_push) >> 16) & 0x3FFFU);  // FL + RR
      hyundai_speed /= 2;
      vehicle_moving = hyundai_speed > HYUNDAI_COMMUNITY_STANDSTILL_THRSLD;
    }
    generic_rx_checks((addr == 832) && (bus == 0)); // LKAS11
  }
  return valid;
}

static int hyundai_community_tx_hook(CANPacket_t *to_send, bool longitudinal_allowed) {
  UNUSED(longitudinal_allowed);

  int tx = 1;
  int addr = GET_ADDR(to_send);
  int bus = GET_BUS(to_send);

  if (!msg_allowed(to_send, HYUNDAI_COMMUNITY_TX_MSGS, sizeof(HYUNDAI_COMMUNITY_TX_MSGS)/sizeof(HYUNDAI_COMMUNITY_TX_MSGS[0]))) {
    tx = 0;
    puts("  CAN TX not allowed: "); puth(addr); puts(", "); puth(bus); puts("\n");
  }

  // LKA STEER: safety check
  if (addr == 832) {
    LKAS11_op = 20;
    int desired_torque = ((GET_BYTES_04(to_send) >> 16) & 0x7ffU) - 1024U;
    bool steer_req = GET_BIT(to_send, 27U) != 0U;

    if (steer_torque_cmd_checks(desired_torque, steer_req, HYUNDAI_COMMUNITY_STEERING_LIMITS)) {
      tx = 0;
    }
  }

  // FORCE CANCEL: safety check only relevant when spamming the cancel button.
  // ensuring that only the cancel button press is sent (VAL 4) when controls are off.
  // This avoids unintended engagements while still allowing resume spam
  // allow clu11 to be sent to MDPS if MDPS is not on bus0
  if ((addr == 1265) && !controls_allowed && (bus != MDPS_bus) && (MDPS_bus == 1)) {
    if ((GET_BYTES_04(to_send) & 0x7U) != 4) {
      tx = 0;
    }
  }

  if (addr == 593) {
    MDPS12_op = 20;
  }
  if ((addr == 1265) && (bus == 1)) {
    CLU11_op = 20;
  }  // only count mesage created for MDPS
  if (addr == 1057) {
    SCC12_op = 20;
    if (SCC12_car > 0) {
      SCC12_car -= 1;
    }
  }
  if (addr == 790) {
    EMS11_op = 20;
  }

  // 1 allows the message through
  return tx;
}

static int hyundai_community_fwd_hook(int bus_num, CANPacket_t *to_fwd) {
  int bus_fwd = -1;
  int addr = GET_ADDR(to_fwd);
  int fwd_to_bus1 = -1;
  if (fwd_bus1) {
    fwd_to_bus1 = 1;
  }

  // forward cam to ccan and viceversa, except lkas cmd
  if (fwd_bus2) {
    if (bus_num == 0) {
      if (!CLU11_op || (addr != 1265) || (MDPS_bus == 0)) {
        if (!MDPS12_op || (addr != 593)) {
          if (!EMS11_op || (addr != 790)) {
            bus_fwd = fwd_to_bus1 == 1 ? 12 : 2;
          } else {  // OP create EMS11 for MDPS
            bus_fwd = 2;
            EMS11_op -= 1;
          }
        } else {  // OP create MDPS for LKAS
          bus_fwd = fwd_to_bus1;
          MDPS12_op -= 1;
        }
      } else {  // OP create CLU12 for MDPS
        bus_fwd = 2;
        CLU11_op -= 1;
      }
    }
    if ((bus_num == 1) && fwd_bus1) {
      if (!MDPS12_op || (addr != 593)) {
        if (!SCC12_op || ((addr != 1056) && (addr != 1057) && (addr != 1290) && (addr != 905))) {
          bus_fwd = 20;
        } else {  // OP create SCC11 SCC12 SCC13 SCC14 for Car
          bus_fwd = 2;
          SCC12_op -= 1;
        }
      } else {  // OP create MDPS for LKAS
        bus_fwd = 0;
        MDPS12_op -= 1;
      }
    }
    if (bus_num == 2) {
      if ((!LKAS11_op || (addr != 832)) && (addr != 1157)) {
        if (!SCC12_op || ((addr != 1056) && (addr != 1057) && (addr != 1290) && (addr != 905))) {
          bus_fwd = fwd_to_bus1 == 1 ? 10 : 0;
        } else {  // OP create SCC12 for Car
          bus_fwd = fwd_to_bus1;
          SCC12_op -= 1;
        }
      } else if (MDPS_bus == 0) {  // OP create LKAS and LFA for Car
        bus_fwd = fwd_to_bus1;
        LKAS11_op -= 1;
      } else {  // OP create LKAS and LFA for Car and MDPS
        LKAS11_op -= 1;
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
  UNUSED(param);

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
