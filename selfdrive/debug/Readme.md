Debugging issues:
------
<b> To print debug info for Panda and Harness issue:</b>
```
python selfdrive/debug/panda_debug.py
```
<b> To print Opentpilot live data:</b>
```
python selfdrive/debug/dump.py <category>
```
  - replace `<category>` with any one of these:

   `can`
   `controlsState`
   `carEvents`
   `carParams`
   `carControl`
   `carState`
   `liveParameters`
   `sensorEvents`
   `pandaState`
   `radarState`

   `driverMonitoringState`
   `roadCameraState`
   `driverCameraState`

   `deviceState`
   `androidLog`
   `managerState`
