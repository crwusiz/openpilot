Debugging issues:
------
<b> To print debug info for Panda and Harness issue:</b>
```
python selfdrive/debug/panda_debug.py
```
<b> To print Opentpilot live data:</b>
```
python selfdrive/debug/dump.py <services>
```
  - replace `<services>` with any one of these:

services = {
  "sensorEvents"
  "gpsNMEA"
  "deviceState"
  "can"
  "controlsState"
  "pandaStates"
  "peripheralState"
  "radarState"
  "roadEncodeIdx"
  "liveTracks"
  "sendcan"
  "logMessage"
  "errorLogMessage"
  "liveCalibration"
  "androidLog"
  "carState"
  "carControl"
  "longitudinalPlan"
  "procLog"
  "gpsLocationExternal"
  "ubloxGnss"
  "qcomGnss"
  "gnssMeasurements"
  "clocks"
  "ubloxRaw"
  "liveLocationKalman"
  "liveParameters"
  "cameraOdometry"
  "lateralPlan"
  "thumbnail"
  "carEvents"
  "carParams"
  "roadCameraState"
  "driverCameraState"
  "driverEncodeIdx"
  "driverState"
  "driverMonitoringState"
  "wideRoadEncodeIdx"
  "wideRoadCameraState"
  "modelV2"
  "managerState"
  "uploaderState"
  "navInstruction"
  "navRoute"
  "navThumbnail"
  "qRoadEncodeIdx"
  "roadLimitSpeed"

  # debug
  "testJoystick"
  "roadEncodeData"
  "driverEncodeData"
  "wideRoadEncodeData"
  "qRoadEncodeData"
}