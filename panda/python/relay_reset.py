#!/usr/bin/env python
import time
from panda import Panda

p = Panda()

print("  Panda health")
print(p.health())

print("  relay OFF (NOOUTPUT)")
p.set_safety_mode(Panda.SAFETY_NOOUTPUT)
p.send_heartbeat()
time.sleep(5)

print("  relay ON (HYUNDAI_COMMUNITY)")
p.set_safety_mode(Panda.SAFETY_HYUNDAI_COMMUNITY)
p.send_heartbeat()
time.sleep(5)

print("  reset PANDA")
p.reset()
