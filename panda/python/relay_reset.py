#!/usr/bin/env python
import time
from panda import Panda

p = Panda()

print("  Panda health\n")
print(p.health())

#print("  relay OFF (SAFETY_NOOUTPUT)\n")
#p.set_safety_mode(Panda.SAFETY_NOOUTPUT)
#p.send_heartbeat()
#time.sleep(2)

#print("  relay ON (SAFETY_HYUNDAI_COMMUNITY)\n")
#p.set_safety_mode(Panda.SAFETY_HYUNDAI_COMMUNITY)
#p.send_heartbeat()
#time.sleep(2)

print("  reset PANDA\n")
p.reset()
