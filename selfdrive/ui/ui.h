#pragma once

#include <eigen3/Eigen/Dense>
#include <memory>
#include <string>

#include <QTimer>
#include <QColor>
#include <QFuture>
#include <QPolygonF>

#include "cereal/messaging/messaging.h"
#include "common/mat.h"
#include "common/params.h"
#include "common/util.h"
#include "system/hardware/hw.h"
#include "selfdrive/ui/qt/prime_state.h"

#include "selfdrive/ui/qt/network/wifi_manager.h"

const int UI_BORDER_SIZE = 10;
const int UI_HEADER_HEIGHT = 420;
const int UI_FOOTER_HEIGHT = 280;

const int UI_FREQ = 20; // Hz
const int BACKLIGHT_OFFROAD = 50;

const float MIN_DRAW_DISTANCE = 10.0;
const float MAX_DRAW_DISTANCE = 100.0;
const Eigen::Matrix3f VIEW_FROM_DEVICE = (Eigen::Matrix3f() <<
  0.0, 1.0, 0.0,
  0.0, 0.0, 1.0,
  1.0, 0.0, 0.0).finished();

const Eigen::Matrix3f FCAM_INTRINSIC_MATRIX = (Eigen::Matrix3f() <<
  2648.0, 0.0, 1928.0 / 2,
  0.0, 2648.0, 1208.0 / 2,
  0.0, 0.0, 1.0).finished();

// tici ecam focal probably wrong? magnification is not consistent across frame
// Need to retrain model before this can be changed
const Eigen::Matrix3f ECAM_INTRINSIC_MATRIX = (Eigen::Matrix3f() <<
  567.0, 0.0, 1928.0 / 2,
  0.0, 567.0, 1208.0 / 2,
  0.0, 0.0, 1.0).finished();

typedef enum UIStatus {
  STATUS_DISENGAGED,
  STATUS_OVERRIDE,
  STATUS_ENGAGED,
} UIStatus;

const QColor bg_colors [] = {
  [STATUS_DISENGAGED] = QColor(0x17, 0x33, 0x49, 0x64),
  [STATUS_OVERRIDE] = QColor(0x91, 0x9b, 0x95, 0x64),
  [STATUS_ENGAGED] = QColor(0x17, 0x86, 0x44, 0x64),
};

typedef struct UIScene {
  Eigen::Matrix3f view_from_calib = VIEW_FROM_DEVICE;
  Eigen::Matrix3f view_from_wide_calib = VIEW_FROM_DEVICE;
  cereal::PandaState::PandaType pandaType;

  // ui add
  bool steeringPressed, engaged, override;
  int satelliteCount;

  // modelV2
  float lane_line_probs[4];
  float road_edge_stds[2];
  QPolygonF track_vertices;
  QPolygonF lane_line_vertices[4];
  QPolygonF road_edge_vertices[2];
  QPolygonF lane_barrier_vertices[2];

  // lead
  QPointF lead_vertices[2];

  cereal::LongitudinalPersonality personality;

  float light_sensor = -1;
  bool started, ignition, is_metric, longitudinal_control;
  bool world_objects_visible = false;
  uint64_t started_frame;

  // FrogPilot
  bool driver_camera;
  bool show_driver_camera;

} UIScene;

class UIState : public QObject {
  Q_OBJECT

public:
  UIState(QObject* parent = 0);
  void updateStatus();
  inline bool engaged() const {
    return scene.started && (*sm)["selfdriveState"].getSelfdriveState().getEnabled();
  }

  std::unique_ptr<SubMaster> sm;
  UIStatus status;
  UIScene scene = {};

  QString language;
  PrimeState *prime_state;

  Eigen::Matrix3f car_space_transform = Eigen::Matrix3f::Zero();
  QRectF clip_region;

  WifiManager *wifi = nullptr;

signals:
  void uiUpdate(const UIState &s);
  void offroadTransition(bool offroad);

private slots:
  void update();

private:
  QTimer *timer;
  bool started_prev = false;
};

UIState *uiState();

// device management class
class Device : public QObject {
  Q_OBJECT

public:
  Device(QObject *parent = 0);
  bool isAwake() { return awake; }
  void setOffroadBrightness(int brightness) {
    offroad_brightness = std::clamp(brightness, 0, 100);
  }

private:
  bool awake = false;
  int interactive_timeout = 0;
  bool ignition_on = false;

  int offroad_brightness = BACKLIGHT_OFFROAD;
  int last_brightness = 0;
  FirstOrderFilter brightness_filter;
  QFuture<void> brightness_future;

  void updateBrightness(const UIState &s);
  void updateWakefulness(const UIState &s);
  void setAwake(bool on);

signals:
  void displayPowerChanged(bool on);
  void interactiveTimeout();

public slots:
  void resetInteractiveTimeout(int timeout = -1);
  void update(const UIState &s);
};

Device *device();

void ui_update_params(UIState *s);
int get_path_length_idx(const cereal::XYZTData::Reader &line, const float path_height);
void update_model(UIState *s,
                  const cereal::ModelDataV2::Reader &model);
void update_leads(UIState *s, const cereal::RadarState::Reader &radar_state, const cereal::XYZTData::Reader &line);
void update_line_data(const UIState *s, const cereal::XYZTData::Reader &line,
                      float y_off, float z_off, QPolygonF *pvd, int max_idx, bool allow_invert);
