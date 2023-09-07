#pragma once

#include <memory>

#include <QPushButton>
#include <QStackedLayout>
#include <QWidget>

#include "common/util.h"
#include "selfdrive/ui/ui.h"
#include "selfdrive/ui/qt/widgets/cameraview.h"


//const int btn_size = 192;
//const int img_size = (btn_size / 4) * 3;
const int btn_size = 160;
const int img_size = (btn_size / 2) * 1.5;

// ***** onroad widgets *****
class OnroadAlerts : public QWidget {
  Q_OBJECT

public:
  OnroadAlerts(QWidget *parent = 0) : QWidget(parent) {}
  void updateAlert(const Alert &a);

protected:
  void paintEvent(QPaintEvent*) override;

private:
  QColor bg;
  Alert alert = {};
};

class ExperimentalButton : public QPushButton {
  Q_OBJECT

public:
  explicit ExperimentalButton(QWidget *parent = 0);
  void updateState(const UIState &s);

private:
  void paintEvent(QPaintEvent *event) override;
  void changeMode();

  Params params;
  QPixmap engage_img;
  QPixmap experimental_img;
  bool experimental_mode;
  bool engageable;
};


class MapSettingsButton : public QPushButton {
  Q_OBJECT

public:
  explicit MapSettingsButton(QWidget *parent = 0);

private:
  void paintEvent(QPaintEvent *event) override;

  QPixmap settings_img;
};

// container window for the NVG UI
class AnnotatedCameraWidget : public CameraWidget {
  Q_OBJECT

public:
  explicit AnnotatedCameraWidget(VisionStreamType type, QWidget* parent = 0);
  void updateState(const UIState &s);

  MapSettingsButton *map_settings_btn;

private:
  void drawText(QPainter &p, int x, int y, const QString &text, int alpha = 255);
  void drawTextColor(QPainter &p, int x, int y, const QString &text, const QColor &color);

  QVBoxLayout *main_layout;
  ExperimentalButton *experimental_btn;
  QPixmap long_img, steer_img;
  QPixmap gaspress_img;
  QPixmap dm_img;

  // crwusiz add
  QPixmap brake_img;
  QPixmap gap_img, gap1_img, gap2_img, gap3_img, gap4_img;
  QPixmap wifi_img, wifi_l_img, wifi_m_img, wifi_h_img, wifi_f_img;
  QPixmap gps_img, direction_img, tpms_img;
  QPixmap turnsignal_l_img, turnsignal_r_img;
  QPixmap traffic_go_img, traffic_stop_img, traffic_none_img;
  QPixmap lane_change_left_img, lane_change_right_img;

  // neokii add
  QPixmap autohold_warning_img;
  QPixmap autohold_active_img;
  QPixmap nda_img, hda_img, nda2_img, hda2_img;

  QString speedUnit;
  std::unique_ptr<PubMaster> pm;

  bool is_cruise_set = false;
  bool steeringPressed = false;
  bool dmActive = false;
  bool hideBottomIcons = false;
  bool brake_state = false;
  bool left_blindspot, right_blindspot = false;
  bool gps_state = false;
  bool longControl = false;
  bool left_on, right_on = false;
  bool v_ego_cluster_seen = false;
  bool wide_cam_requested = false;
  bool gas_pressed = false;
  bool isStandstill = false;
  bool rightHandDM = false;
  bool nav_enabled = false;

  int status = STATUS_DISENGAGED;
  int autohold_state = 0;
  int nda_state, isNda2 = 0;
  int wifi_state = 0;
  int gpsSatelliteCount = 0;
  int accel, gap_state = 0;
  int lateralControl = 0;
  int sccBus = 0;
  int camLimitSpeed, sectionLimitSpeed = 0;
  int camLimitSpeedLeftDist, sectionLeftDist = 0;
  int skip_frame_count = 0;
  int traffic_state = 0;

  float speed, apply_speed, cruise_speed;
  float gpsBearing, gpsVerticalAccuracy, gpsAltitude, gpsAccuracy = 0;
  float steerAngle, steerRatio = 0;
  float fl, fr, rl, rr = 0;
  float roadLimitSpeed, navLimitSpeed = 0;
  float latAccelFactor, friction, latAccelFactorRaw, frictionRaw = 0;
  float dm_fade_state = 1.0;

protected:
  void paintGL() override;
  void initializeGL() override;
  void showEvent(QShowEvent *event) override;
  void updateFrameMat() override;
  void drawLaneLines(QPainter &painter, const UIState *s);
  void drawLaneChangeIndicator(QPainter &painter, const UIState *s);
  void drawLead(QPainter &painter, const cereal::RadarState::LeadData::Reader &lead_data, const QPointF &vd);
  void drawHud(QPainter &p);
  void drawDriverState(QPainter &painter, const UIState *s);
  inline QColor whiteColor(int alpha = 255) { return QColor(255, 255, 255, alpha); }
  inline QColor yellowColor(int alpha = 255) { return QColor(255, 255, 0, alpha); }
  inline QColor redColor(int alpha = 255) { return QColor(255, 0, 0, alpha); }
  inline QColor blackColor(int alpha = 255) { return QColor(0, 0, 0, alpha); }
  inline QColor limeColor(int alpha = 255) { return QColor(120, 255, 120, alpha); }
  inline QColor orangeColor(int alpha = 255) { return QColor(255, 149, 0, alpha); }
  inline QColor lightorangeColor(int alpha = 255) { return QColor(255, 228, 191, alpha); }
  inline QColor engagedColor(int alpha = 255) { return QColor(23, 134, 68, alpha); }
  inline QColor warningColor(int alpha = 255) { return QColor(218, 111, 37, alpha); }
  inline QColor overrideColor(int alpha = 255) { return QColor(145, 155, 149, alpha); }
  inline QColor steeringpressedColor(int alpha = 255) { return QColor(0, 191, 255, alpha); }
  inline QColor greenColor(int alpha = 255) { return QColor(128, 216, 166, alpha); }
  inline QColor pinkColor(int alpha = 255) { return QColor(255, 191, 191, alpha); }

  double prev_draw_t = 0;
  FirstOrderFilter fps_filter;
};

// container for all onroad widgets
class OnroadWindow : public QWidget {
  Q_OBJECT

public:
  OnroadWindow(QWidget* parent = 0);
  bool isMapVisible() const { return map && map->isVisible(); }
  void showMapPanel(bool show) { if (map) map->setVisible(show); }

signals:
  void mapPanelRequested();

private:
  void paintEvent(QPaintEvent *event);
  void mousePressEvent(QMouseEvent* e) override;
  OnroadAlerts *alerts;
  AnnotatedCameraWidget *nvg;
  QColor bg = bg_colors[STATUS_DISENGAGED];
  QWidget *map = nullptr;
  QHBoxLayout* split;

private slots:
  void offroadTransition(bool offroad);
  void primeChanged(bool prime);
  void updateState(const UIState &s);
};
