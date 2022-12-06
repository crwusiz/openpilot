#pragma once

#include <QStackedLayout>
#include <QWidget>

#include "common/util.h"
#include "selfdrive/ui/qt/widgets/cameraview.h"
#include "selfdrive/ui/ui.h"


// ***** onroad widgets *****
class OnroadAlerts : public QWidget {
  Q_OBJECT

public:
  OnroadAlerts(QWidget *parent = 0) : QWidget(parent) {};
  void updateAlert(const Alert &a, const QColor &color);

protected:
  void paintEvent(QPaintEvent*) override;

private:
  QColor bg;
  Alert alert = {};
};

// container window for the NVG UI
class AnnotatedCameraWidget : public CameraWidget {
  Q_OBJECT
  Q_PROPERTY(float speed MEMBER speed);
  Q_PROPERTY(QString speedUnit MEMBER speedUnit);
  Q_PROPERTY(float applyMaxSpeed MEMBER applyMaxSpeed);
  Q_PROPERTY(float cruiseMaxSpeed MEMBER cruiseMaxSpeed);

  Q_PROPERTY(bool is_cruise_set MEMBER is_cruise_set);
  Q_PROPERTY(bool steeringPressed MEMBER steeringPressed);
  Q_PROPERTY(bool dmActive MEMBER dmActive);
  Q_PROPERTY(bool brake_state MEMBER brake_state);
  Q_PROPERTY(bool left_blindspot MEMBER left_blindspot);
  Q_PROPERTY(bool right_blindspot MEMBER right_blindspot);
  Q_PROPERTY(bool gps_state MEMBER gps_state);
  Q_PROPERTY(bool longControl MEMBER longControl);
  Q_PROPERTY(bool left_on MEMBER left_on);
  Q_PROPERTY(bool right_on MEMBER right_on);

  Q_PROPERTY(int status MEMBER status);
  Q_PROPERTY(int lead_status MEMBER lead_status);
  Q_PROPERTY(int autohold_state MEMBER autohold_state);
  Q_PROPERTY(int nda_state MEMBER nda_state);
  Q_PROPERTY(int wifi_state MEMBER wifi_state);
  Q_PROPERTY(int gpsSatelliteCount MEMBER gpsSatelliteCount);
  Q_PROPERTY(int gap_state MEMBER gap_state);
  Q_PROPERTY(int lateralControl MEMBER lateralControl);
  Q_PROPERTY(int epsBus MEMBER epsBus);
  Q_PROPERTY(int sccBus MEMBER sccBus);
  Q_PROPERTY(int camLimitSpeed MEMBER camLimitSpeed);
  Q_PROPERTY(int camLimitSpeedLeftDist MEMBER camLimitSpeedLeftDist);
  Q_PROPERTY(int sectionLimitSpeed MEMBER sectionLimitSpeed);
  Q_PROPERTY(int sectionLeftDist MEMBER sectionLeftDist);
  Q_PROPERTY(int accel MEMBER accel);

  Q_PROPERTY(float lead_d_rel MEMBER lead_d_rel);
  Q_PROPERTY(float lead_v_rel MEMBER lead_v_rel);
  Q_PROPERTY(float gpsBearing MEMBER gpsBearing);
  Q_PROPERTY(float gpsVerticalAccuracy MEMBER gpsVerticalAccuracy);
  Q_PROPERTY(float gpsAltitude MEMBER gpsAltitude);
  Q_PROPERTY(float gpsAccuracy MEMBER gpsAccuracy);
  Q_PROPERTY(float angleSteers MEMBER angleSteers);
  Q_PROPERTY(float steerAngleDesired MEMBER steerAngleDesired);
  Q_PROPERTY(float steerRatio MEMBER steerRatio);
  Q_PROPERTY(float fl MEMBER fl);
  Q_PROPERTY(float fr MEMBER fr);
  Q_PROPERTY(float rl MEMBER rl);
  Q_PROPERTY(float rr MEMBER rr);
  Q_PROPERTY(float roadLimitSpeed MEMBER roadLimitSpeed);
  Q_PROPERTY(float latAccelFactor MEMBER latAccelFactor);
  Q_PROPERTY(float friction MEMBER friction);
  Q_PROPERTY(float latAccelFactorRaw MEMBER latAccelFactorRaw);
  Q_PROPERTY(float frictionRaw MEMBER frictionRaw);
  Q_PROPERTY(float cpuTemp MEMBER cpuTemp);

public:
  explicit AnnotatedCameraWidget(VisionStreamType type, QWidget* parent = 0);
  void updateState(const UIState &s);

private:
  void drawIcon(QPainter &p, int x, int y, QPixmap &img, QBrush bg, float opacity);
  void drawIconRotate(QPainter &p, int x, int y, QPixmap &img, QBrush bg, float opacity, float angle);
  void drawText(QPainter &p, int x, int y, const QString &text, int alpha = 255);
  void drawTextColor(QPainter &p, int x, int y, const QString &text, const QColor &color);
  void drawRightDevUi(QPainter &p, int x, int y);
  int devUiDrawElement(QPainter &p, int x, int y, const char* value, const char* label, const char* units, const QColor &color);

  QPixmap wheel_img, steer_img;
  QPixmap lat_img, longitudinal_img;
  QPixmap dm_img;
  QPixmap experimental_img;

  // crwusiz add
  QPixmap brake_img, bsd_l_img, bsd_r_img;
  QPixmap gap_img, gap1_img, gap2_img, gap3_img, gap4_img;
  QPixmap wifi_img, wifi_l_img, wifi_m_img, wifi_h_img, wifi_f_img;
  QPixmap gps_img, direction_img, tpms_img;
  QPixmap turnsignal_l_img, turnsignal_r_img;

  // neokii add
  QPixmap autohold_warning_img;
  QPixmap autohold_active_img;
  QPixmap nda_img, hda_img;

  const int radius = 160;
  const int img_size = (radius / 2) * 1.5;

  QString speedUnit;
  std::unique_ptr<PubMaster> pm;

  bool is_cruise_set = false;
  bool steeringPressed = false;
  bool dmActive = false;
  bool brake_state = false;
  bool left_blindspot, right_blindspot = false;
  bool gps_state = false;
  bool longControl = false;
  bool left_on, right_on = false;
  bool v_ego_cluster_seen = false;
  bool wide_cam_requested = false;

  int status = STATUS_DISENGAGED;
  int lead_status;
  int autohold_state = 0;
  int nda_state = 0;
  int wifi_state = 0;
  int gpsSatelliteCount = 0;
  int accel, gap_state = 0;
  int lateralControl = 0;
  int epsBus, sccBus = 0;
  int camLimitSpeed ,camLimitSpeedLeftDist = 0;
  int sectionLimitSpeed, sectionLeftDist = 0;
  int skip_frame_count = 0;

  float speed, applyMaxSpeed, cruiseMaxSpeed;
  float lead_d_rel, lead_v_rel = 0;
  float gpsBearing, gpsVerticalAccuracy, gpsAltitude, gpsAccuracy = 0;
  float angleSteers, steerAngleDesired ,steerRatio = 0;
  float fl, fr, rl, rr = 0;
  float roadLimitSpeed = 0;
  float latAccelFactor, friction, latAccelFactorRaw, frictionRaw = 0;
  float cpuTemp = 0;

protected:
  void paintGL() override;
  void initializeGL() override;
  void showEvent(QShowEvent *event) override;
  void updateFrameMat() override;
  void drawLaneLines(QPainter &painter, const UIState *s);
  void drawLead(QPainter &painter, const cereal::ModelDataV2::LeadDataV3::Reader &lead_data, const QPointF &vd, bool is_radar);
  void drawHud(QPainter &p);
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
  void updateState(const UIState &s);
};
