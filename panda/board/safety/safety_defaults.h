bool Lcan_bus1 = false;
bool Fwd_bus1 = false;
bool Fwd_obd = false;
bool Fwd_bus2 = true;
int OBD_cnt = 20;
int LKAS11_bus0_cnt = 0;
int Lcan_bus1_cnt = 0;
int MDPS12_checksum = -1;
int MDPS12_cnt = 0;
int Last_StrColTq = 0;

const addr_checks default_rx_checks = {
  .check = NULL,
  .len = 0,
};

int default_rx_hook(CANPacket_t *to_push) {
  int bus = GET_BUS(to_push);
  int addr = GET_ADDR(to_push);

  // LKAS11
  if (addr == 832) {
    if (bus == 0) {
      LKAS11_bus0_cnt = 10;
      if (Fwd_bus2) {
        Fwd_bus2 = false;
        puts("  LKAS11 on bus0: forwarding disabled\n");
      }
    }
    if (bus == 2) {
      if (LKAS11_bus0_cnt > 0) {
        LKAS11_bus0_cnt--;
      } else if (!Fwd_bus2) {
        Fwd_bus2 = true;
        puts("  LKAS11 on bus2: forwarding enabled\n");
      }
      if (OBD_cnt == 20) {
        puts("  LKAS11 on bus2: forwarding enabled\n");
      }
      if (Lcan_bus1_cnt > 0) {
        Lcan_bus1_cnt--;
      } else if (Lcan_bus1) {
        Lcan_bus1 = false;
        puts("  Lcan not on bus1\n");
      }
      // set CAN2 mode to normal if int_cnt expaired
      if (OBD_cnt == 11 && !Fwd_bus1 && current_board->has_obd) {
        current_board->set_can_mode(CAN_MODE_OBD_CAN2);
        puts("  checking bus1: setting CAN_MODE_OBD_CAN2\n");
      }
      if (OBD_cnt == 1 && !Fwd_obd && !Fwd_bus1 && current_board->has_obd) {
        current_board->set_can_mode(CAN_MODE_NORMAL);
        puts("  OBD2 CAN empty: setting CAN_MODE_NORMAL\n");
      }
      if (OBD_cnt > 0) {
        OBD_cnt--;
      }
    }
  }

  // check if we have a LCAN on Bus1
  if (bus == 1 && (addr == 1296 || addr == 524)) {
    Lcan_bus1_cnt = 500;
    if (Fwd_bus1 || !Lcan_bus1) {
      Lcan_bus1 = true;
      Fwd_bus1 = false;
      puts("  LCAN on bus1: forwarding disabled\n");
    }
  }
  // check if we have a MDPS or SCC on Bus1
  if (bus == 1 && (addr == 593 || addr == 897 || addr == 1057)) {
    if (!Fwd_bus1 && OBD_cnt > 1 && OBD_cnt < 11 && current_board->has_obd) {
      Fwd_obd = true;
      OBD_cnt = 0;
      puts("  MDPS or SCC on OBD2 CAN: setting can mode obd\n");
    } else if (!Fwd_bus1 && !Lcan_bus1) {
      Fwd_bus1 = true;
      puts("  MDPS or SCC on bus1: forwarding enabled\n");
    }
  }
  if ((addr == 593) && (MDPS12_checksum == -1)){
    int New_Chksum2 = 0;
    uint8_t dat[8];
    for (int i=0; i<8; i++) {
      dat[i] = GET_BYTE(to_push, i);
    }
    int Chksum2 = dat[3];
    dat[3] = 0;
    for (int i=0; i<8; i++) {
      New_Chksum2 += dat[i];
    }
    New_Chksum2 %= 256;
    if (Chksum2 == New_Chksum2) {
      MDPS12_checksum = 0;
    } else {
      MDPS12_checksum = 1;
    }
  }
  return true;
}

// *** no output safety mode ***

static const addr_checks* nooutput_init(uint16_t param) {
  UNUSED(param);
  if (current_board->has_obd && Fwd_obd) {
    current_board->set_can_mode(CAN_MODE_OBD_CAN2);
    puts("setting CAN_MODE_OBD_CAN2\n");
  }
  return &default_rx_checks;
}

static int nooutput_tx_hook(CANPacket_t *to_send, bool longitudinal_allowed) {
  UNUSED(to_send);
  UNUSED(longitudinal_allowed);
  return false;
}

static int nooutput_tx_lin_hook(int lin_num, uint8_t *data, int len) {
  UNUSED(lin_num);
  UNUSED(data);
  UNUSED(len);
  return false;
}

static int default_fwd_hook(int bus_num, CANPacket_t *to_fwd) {
  int addr = GET_ADDR(to_fwd);
  int bus_fwd = -1;

  if (bus_num == 0 && (Fwd_bus1 || Fwd_bus2 || Fwd_obd)) {
    if ((Fwd_bus1 || Fwd_obd) && Fwd_bus2) {
    bus_fwd = 12;
    } else {
      bus_fwd = Fwd_bus2 ? 2 : 1;
    }
  }
  if (bus_num == 1 && (Fwd_bus1 || Fwd_obd)) {
    bus_fwd = Fwd_bus2 ? 20 : 0;
  }
  if (bus_num == 2 && Fwd_bus2) {
    bus_fwd = (Fwd_bus1 || Fwd_obd) ? 10 : 0;
  }
    // Code for LKA/LFA/HDA anti-nagging.
  if (addr == 593 && bus_fwd != -1) {
    uint8_t dat[8];
    int New_Chksum2 = 0;
    for (int i=0; i<8; i++) {
      dat[i] = GET_BYTE(to_fwd, i);
    }
    if (MDPS12_cnt > 330) {
      int StrColTq = dat[0] | (dat[1] & 0x7) << 8;
      int OutTq = dat[6] >> 4 | dat[7] << 4;
      if (MDPS12_cnt == 331) {
        StrColTq -= 164;
      } else {
        StrColTq = Last_StrColTq + 34;
      }
      OutTq = 2058;
      dat[0] = StrColTq & 0xFF;
      dat[1] &= 0xF8;
      dat[1] |= StrColTq >> 8;
      dat[6] &= 0xF;
      dat[6] |= (OutTq & 0xF) << 4;
      dat[7] = OutTq >> 4;

      uint32_t* RDLR = (uint32_t*)&(to_fwd->data[0]);
      uint32_t* RDHR = (uint32_t*)&(to_fwd->data[4]);

      *RDLR &= 0xFFF800;
      *RDLR |= StrColTq;
      *RDHR &= 0xFFFFF;
      *RDHR |= OutTq << 20;

      Last_StrColTq = StrColTq;
      dat[3] = 0;
      if (!MDPS12_checksum) {
        for (int i=0; i<8; i++) {
          New_Chksum2 += dat[i];
        }
        New_Chksum2 %= 256;
      } else if (MDPS12_checksum) {
        uint8_t crc = 0xFF;
        uint8_t poly = 0x1D;
        int i, j;
        for (i=0; i<8; i++){
          if (i!=3){ //don't include CRC byte
            crc ^= dat[i];
            for (j=0; j<8; j++) {
              if ((crc & 0x80) != 0U) {
                crc = (crc << 1) ^ poly;
              } else {
                crc <<= 1;
              }
            }
          }
        }
        crc ^= 0xFF;
        crc %= 256;
        New_Chksum2 = crc;
      }
      *RDLR |= New_Chksum2 << 24;
    }
    MDPS12_cnt += 1;
    MDPS12_cnt %= 345;
  }
  return bus_fwd;
}

const safety_hooks nooutput_hooks = {
  .init = nooutput_init,
  .rx = default_rx_hook,
  .tx = nooutput_tx_hook,
  .tx_lin = nooutput_tx_lin_hook,
  .fwd = default_fwd_hook,
};

// *** all output safety mode ***

// Enables passthrough mode where relay is open and bus 0 gets forwarded to bus 2 and vice versa
const uint16_t ALLOUTPUT_PARAM_PASSTHROUGH = 1;
bool alloutput_passthrough = false;

static const addr_checks* alloutput_init(uint16_t param) {
  controls_allowed = true;
  alloutput_passthrough = GET_FLAG(param, ALLOUTPUT_PARAM_PASSTHROUGH);
  if (current_board->has_obd && Fwd_obd) {
    current_board->set_can_mode(CAN_MODE_OBD_CAN2);
    puts("  setting CAN_MODE_OBD_CAN2\n");
  }
  return &default_rx_checks;
}

static int alloutput_tx_hook(CANPacket_t *to_send, bool longitudinal_allowed) {
  UNUSED(to_send);
  UNUSED(longitudinal_allowed);
  return true;
}

static int alloutput_tx_lin_hook(int lin_num, uint8_t *data, int len) {
  UNUSED(lin_num);
  UNUSED(data);
  UNUSED(len);
  return true;
}

static int alloutput_fwd_hook(int bus_num, CANPacket_t *to_fwd) {
  UNUSED(to_fwd);
  int bus_fwd = -1;

  if (alloutput_passthrough) {
    if (bus_num == 0) {
      bus_fwd = 2;
    }
    if (bus_num == 2) {
      bus_fwd = 0;
    }
  }

  return bus_fwd;
}

const safety_hooks alloutput_hooks = {
  .init = alloutput_init,
  .rx = default_rx_hook,
  .tx = alloutput_tx_hook,
  .tx_lin = alloutput_tx_lin_hook,
  .fwd = alloutput_fwd_hook,
};
