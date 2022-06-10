# debug scripts

## [can_printer.py](can_printer.py)

```
usage: can_printer.py [-h] [--bus BUS] [--max_msg MAX_MSG] [--addr ADDR]

simple CAN data viewer

optional arguments:
  -h, --help         show this help message and exit
  --bus BUS          CAN bus to print out (default: 0)
  --max_msg MAX_MSG  max addr (default: None)
  --addr ADDR
```

## [dump.py](dump.py)

```
usage: dump.py [-h] [--pipe] [--raw] [--json] [--dump-json] [--no-print] [--addr ADDR] [--values VALUES] [socket [socket ...]]

Dump communcation sockets. See cereal/services.py for a complete list of available sockets.

positional arguments:
  socket           socket names to dump. defaults to all services defined in cereal

optional arguments:
  -h, --help       show this help message and exit
  --pipe
  --raw
  --json
  --dump-json
  --no-print
  --addr ADDR
  --values VALUES  values to monitor (instead of entire event)
```

```
To print debug info for Panda and Harness issue:
python selfdrive/debug/debug_console.py

To print Openpilot live data:
python selfdrive/debug/dump.py <services>

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
```