#!/usr/bin/env python
import time
from panda import Panda

p = Panda()

p.set_safety_mode(Panda.SAFETY_NOOUTPUT)
p.send_heartbeat()
print("relay OFF")
time.sleep(1)
p.set_safety_mode(Panda.SAFETY_HYUNDAI_COMMUNITY)
p.send_heartbeat()
print("relay ON")
time.sleep(1)