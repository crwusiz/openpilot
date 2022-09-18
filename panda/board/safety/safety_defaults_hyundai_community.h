extern bool lcan_bus1, fwd_bus1, fwd_bus2;
extern int lkas11_bus0_cnt, lcan_bus1_cnt, mdps12_chksum, mdps12_cnt, Last_StrColTq;

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
      lkas11_bus0_cnt = 10;
      if (fwd_bus2) {
        fwd_bus2 = false;
        puts("  forwarding disabled : LKAS11 on bus ["); puth(bus); puts("]\n");
      }
    }
    if (bus == 2) {
      if (lkas11_bus0_cnt > 0) {
        lkas11_bus0_cnt--;
      } else if (!fwd_bus2) {
        fwd_bus2 = true;
        puts("  forwarding enabled : LKAS11 on bus ["); puth(bus); puts("]\n");
      }
      if (lcan_bus1_cnt > 0) {
        lcan_bus1_cnt--;
      } else if (lcan_bus1) {
        lcan_bus1 = false;
        puts("  LCAN not on bus [1]\n");
      }
    }
  }

  // check if we have a LCAN on Bus1
  if (bus == 1 && (addr == 1296 || addr == 524)) {
    lcan_bus1_cnt = 500;
    if (fwd_bus1 || !lcan_bus1) {
      lcan_bus1 = true;
      fwd_bus1 = false;
      puts("  forwarding disabled : LCAN on bus ["); puth(bus); puts("]\n");
    }
  }
  // check if we have a EPS or SCC on Bus1
  if (bus == 1 && (addr == 593 || addr == 897 || addr == 1057)) {
    if (!fwd_bus1 && !lcan_bus1) {
      fwd_bus1 = true;
      puts("  forwarding enabled : EPS or SCC on bus ["); puth(bus); puts("]\n");
    }
  }
  if ((addr == 593) && (mdps12_chksum == -1)) {
    int new_chksum2 = 0;
    uint8_t dat[8];
    for (int i=0; i<8; i++) {
      dat[i] = GET_BYTE(to_push, i);
    }
    int chksum2 = dat[3];
    dat[3] = 0;
    for (int i=0; i<8; i++) {
      new_chksum2 += dat[i];
    }
    new_chksum2 %= 256;
    if (chksum2 == new_chksum2) {
      mdps12_chksum = 0;
    } else {
      mdps12_chksum = 1;
    }
  }
  return true;
}

// *** no output safety mode ***

static const addr_checks* nooutput_init(uint16_t param) {
  UNUSED(param);
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

  if (bus_num == 0 && (fwd_bus1 || fwd_bus2)) {
    if (fwd_bus1 && fwd_bus2) {
      bus_fwd = 12;
    } else {
      bus_fwd = fwd_bus2 ? 2 : 1;
    }
  }
  if (bus_num == 1 && fwd_bus1) {
    bus_fwd = fwd_bus2 ? 20 : 0;
  }
  if (bus_num == 2 && fwd_bus2) {
    bus_fwd = fwd_bus1 ? 10 : 0;
  }

  // Code for LKA/LFA/HDA anti-nagging.
  if (addr == 593 && bus_fwd != -1) {
    uint8_t dat[8];
    int new_chksum2 = 0;
    for (int i=0; i<8; i++) {
      dat[i] = GET_BYTE(to_fwd, i);
    }
    if (mdps12_cnt > 330) {
      int StrColTq = dat[0] | (dat[1] & 0x7) << 8;
      int OutTq = dat[6] >> 4 | dat[7] << 4;
      if (mdps12_cnt == 331) {
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
      if (!mdps12_chksum) {
        for (int i=0; i<8; i++) {
          new_chksum2 += dat[i];
        }
        new_chksum2 %= 256;
      } else if (mdps12_chksum) {
        uint8_t crc = 0xFFU;
        uint8_t poly = 0x1D;
        int i, j;
        for (i=0; i<8; i++) {
          if (i!=3) { //don't include CRC byte
            crc ^= dat[i];
            for (j=0; j<8; j++) {
              if ((crc & 0x80U) != 0U) {
                crc = (crc << 1) ^ poly;
              } else {
                crc <<= 1;
              }
            }
          }
        }
        crc ^= 0xFFU;
        crc %= 256;
        new_chksum2 = crc;
      }
      *RDLR |= new_chksum2 << 24;
    }
    mdps12_cnt += 1;
    mdps12_cnt %= 345;
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
