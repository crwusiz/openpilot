#!/usr/bin/bash

cd /data/openpilot && scons -c;
rm /data/openpilot/.sconsign.dblite;
rm -rf /tmp/scons_cache;

echo -n "0" > /data/params/d/PrebuiltEnable
sudo rm -f prebuilt

exec /data/openpilot/scripts/restart.sh