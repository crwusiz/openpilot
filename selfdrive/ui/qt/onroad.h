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
class NvgWindow : public CameraViewWidget {
  Q_OBJECT
  Q_PROPERTY(float speed MEMBER speed);
  Q_PROPERTY(QString speedUnit MEMBER speedUnit);
  Q_PROPERTY(QString applyMaxSpeed MEMBER applyMaxSpeed);
  Q_PROPERTY(QString cruiseMaxSpeed MEMBER cruiseMaxSpeed);

  Q_PROPERTY(bool is_cruise_set MEMBER is_cruise_set);
  Q_PROPERTY(bool steeringPressed MEMBER steeringPressed);
  Q_PROPERTY(bool dmActive MEMBER dmActive);
  Q_PROPERTY(bool brake_status MEMBER brake_status);
  Q_PROPERTY(bool bsd_l_status MEMBER bsd_l_status);
  Q_PROPERTY(bool bsd_r_status MEMBER bsd_r_status);
  Q_PROPERTY(bool gps_status MEMBER gps_status);
  Q_PROPERTY(bool longControl MEMBER longControl);
  Q_PROPERTY(bool left_on MEMBER left_on);
  Q_PROPERTY(bool right_on MEMBER right_on);

  Q_PROPERTY(int status MEMBER status);
  Q_PROPERTY(int lead_status MEMBER lead_status);
  Q_PROPERTY(int autohold_status MEMBER autohold_status);
  Q_PROPERTY(int nda_status MEMBER nda_status);
  Q_PROPERTY(int wifi_status MEMBER wifi_status);
  Q_PROPERTY(int gpsSatelliteCount MEMBER gpsSatelliteCount);
  Q_PROPERTY(int gap MEMBER gap);
  Q_PROPERTY(int autoTrGap MEMBER autoTrGap);
  Q_PROPERTY(int lateralcontrol MEMBER lateralcontrol);
  Q_PROPERTY(int mdpsBus MEMBER mdpsBus);
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

public:
  explicit NvgWindow(VisionStreamType type, QWidget* parent = 0);
  void updateState(const UIState &s);

private:
  void drawIcon(QPainter &p, int x, int y, QPixmap &img, QBrush bg, float opacity);
  void drawIconRotate(QPainter &p, int x, int y, QPixmap &img, QBrush bg, float opacity, float angle);
  void drawText(QPainter &p, int x, int y, const QString &text, int alpha = 255);
  void drawTextColor(QPainter &p, int x, int y, const QString &text, const QColor &color);
  void drawRightDevUi(QPainter &p, int x, int y);
  int devUiDrawElement(QPainter &p, int x, int y, const char* value, const char* label, const char* units, const QColor &color);

  QPixmap engage_img;
  QPixmap dm_img;

  // crwusiz add
  QPixmap brake_img;
  QPixmap bsd_l_img;
  QPixmap bsd_r_img;
  QPixmap gap_img;
  QPixmap gap1_img;
  QPixmap gap2_img;
  QPixmap gap3_img;
  QPixmap gap4_img;
  QPixmap gap_auto_img;
  QPixmap gps_img;
  QPixmap wifi_img;
  QPixmap direction_img;

  // neokii add
  QPixmap autohold_warning_img;
  QPixmap autohold_active_img;
  QPixmap nda_img;
  QPixmap hda_img;
  QPixmap tpms_img;
  QPixmap turnsignal_l_img;
  QPixmap turnsignal_r_img;

  const int radius = 160;
  const int img_size = (radius / 2) * 1.5;

  float speed;
  QString speedUnit;
  QString applyMaxSpeed;
  QString cruiseMaxSpeed;

  bool is_cruise_set = false;
  bool steeringPressed = false;
  bool dmActive = false;
  bool brake_status = false;
  bool bsd_l_status = false;
  bool bsd_r_status = false;
  bool gps_status = false;
  bool longControl = false;
  bool left_on = false;
  bool right_on = false;

  int status = STATUS_DISENGAGED;
  int lead_status;
  int autohold_status = 0;
  int nda_status = 0;
  int wifi_status = 0;
  int gpsSatelliteCount = 0;
  int gap = 0;
  int autoTrGap = 0;
  int lateralcontrol = 0;
  int mdpsBus = 0;
  int sccBus = 0;
  int camLimitSpeed = 0;
  int camLimitSpeedLeftDist = 0;
  int sectionLimitSpeed = 0;
  int sectionLeftDist = 0;
  int accel = 0;
  int x,y,w,h = 0;

  float lead_d_rel = 0;
  float lead_v_rel = 0;
  float gpsBearing = 0;
  float gpsVerticalAccuracy = 0;
  float gpsAltitude = 0;
  float gpsAccuracy = 0;
  float angleSteers = 0;
  float steerAngleDesired = 0;
  float steerRatio = 0;
  float fl,fr,rl,rr = 0;

protected:
  void paintGL() override;
  void initializeGL() override;
  void showEvent(QShowEvent *event) override;
  void updateFrameMat(int w, int h) override;
  void drawLaneLines(QPainter &painter, const UIState *s);
  void drawLead(QPainter &painter, const cereal::ModelDataV2::LeadDataV3::Reader &lead_data, const QPointF &vd, bool is_radar);
  void drawHud(QPainter &p);
  inline QColor whiteColor(int alpha = 255) { return QColor(255, 255, 255, alpha); }
  inline QColor yellowColor(int alpha = 255) { return QColor(255, 255, 0, alpha); }
  inline QColor longColor(int alpha = 255) { return QColor(255, 149, 0, alpha); }
  inline QColor unitColor(int alpha = 255) { return QColor(255, 228, 191, alpha); }
  inline QColor redColor(int alpha = 255) { return QColor(255, 0, 0, alpha); }
  inline QColor blackColor(int alpha = 255) { return QColor(0, 0, 0, alpha); }
  inline QColor limeColor(int alpha = 255) { return QColor(120, 255, 120, alpha); }
  inline QColor orangeColor(int alpha = 255) { return QColor(255, 188, 0, alpha); }
  inline QColor engagedColor(int alpha = 255) { return QColor(23, 134, 68, alpha); }
  inline QColor warningColor(int alpha = 255) { return QColor(218, 111, 37, alpha); }
  inline QColor overrideColor(int alpha = 255) { return QColor(145, 155, 149, alpha); }
  inline QColor overridehalfColor(int alpha = 255) { return QColor(72, 77, 74, alpha); }
  inline QColor steeringpressedColor(int alpha = 255) { return QColor(0, 191, 255, alpha); }
  inline QColor steeringpressedhalfColor(int alpha = 255) { return QColor(0, 191, 255, alpha); }
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
  NvgWindow *nvg;
  QColor bg = bg_colors[STATUS_DISENGAGED];
  QWidget *map = nullptr;
  QHBoxLayout* split;

private slots:
  void offroadTransition(bool offroad);
  void updateState(const UIState &s);
};
