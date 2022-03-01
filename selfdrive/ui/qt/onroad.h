#pragma once

#include <QStackedLayout>
#include <QWidget>

#include "selfdrive/ui/qt/widgets/cameraview.h"
#include "selfdrive/ui/ui.h"

// ***** onroad widgets ***** ( wirelessnet2 init )
class OnroadHud : public QWidget {
  Q_OBJECT
  Q_PROPERTY(QString speed MEMBER speed NOTIFY valueChanged);
  Q_PROPERTY(QString speedUnit MEMBER speedUnit NOTIFY valueChanged);
  Q_PROPERTY(QString applyMaxSpeed MEMBER applyMaxSpeed NOTIFY valueChanged);
  Q_PROPERTY(QString cruiseMaxSpeed MEMBER cruiseMaxSpeed NOTIFY valueChanged);

  Q_PROPERTY(bool is_cruise_set MEMBER is_cruise_set NOTIFY valueChanged);
  Q_PROPERTY(bool engageable MEMBER engageable NOTIFY valueChanged);
  Q_PROPERTY(bool steeringPressed MEMBER steeringPressed NOTIFY valueChanged);
  Q_PROPERTY(bool dmActive MEMBER dmActive NOTIFY valueChanged);
  Q_PROPERTY(bool brake_status MEMBER brake_status NOTIFY valueChanged);
  Q_PROPERTY(bool bsd_l_status MEMBER bsd_l_status NOTIFY valueChanged);
  Q_PROPERTY(bool bsd_r_status MEMBER bsd_r_status NOTIFY valueChanged);
  Q_PROPERTY(bool gps_status MEMBER gps_status NOTIFY valueChanged);
  Q_PROPERTY(bool longControl MEMBER longControl NOTIFY valueChanged);
  Q_PROPERTY(bool left_on MEMBER left_on NOTIFY valueChanged);
  Q_PROPERTY(bool right_on MEMBER right_on NOTIFY valueChanged);

  Q_PROPERTY(int status MEMBER status NOTIFY valueChanged);
  Q_PROPERTY(int lead_status MEMBER lead_status NOTIFY valueChanged);
  Q_PROPERTY(int autohold_status MEMBER autohold_status NOTIFY valueChanged);
  Q_PROPERTY(int nda_status MEMBER nda_status NOTIFY valueChanged);
  Q_PROPERTY(int wifi_status MEMBER wifi_status NOTIFY valueChanged);
  Q_PROPERTY(int gpsSatelliteCount MEMBER gpsSatelliteCount NOTIFY valueChanged);
  Q_PROPERTY(int gap MEMBER gap NOTIFY valueChanged);
  Q_PROPERTY(int autoTrGap MEMBER autoTrGap NOTIFY valueChanged);
  Q_PROPERTY(int lateralcontrol_status MEMBER lateralcontrol_status NOTIFY valueChanged);
  Q_PROPERTY(int mdpsBus MEMBER mdpsBus NOTIFY valueChanged);
  Q_PROPERTY(int sccBus MEMBER sccBus NOTIFY valueChanged);
  Q_PROPERTY(int camLimitSpeed MEMBER camLimitSpeed NOTIFY valueChanged);
  Q_PROPERTY(int camLimitSpeedLeftDist MEMBER camLimitSpeedLeftDist NOTIFY valueChanged);
  Q_PROPERTY(int sectionLimitSpeed MEMBER sectionLimitSpeed NOTIFY valueChanged);
  Q_PROPERTY(int sectionLeftDist MEMBER sectionLeftDist NOTIFY valueChanged);
  
  Q_PROPERTY(float lead_d_rel MEMBER lead_d_rel NOTIFY valueChanged);
  Q_PROPERTY(float lead_v_rel MEMBER lead_v_rel NOTIFY valueChanged);
  Q_PROPERTY(float gpsBearing MEMBER gpsBearing NOTIFY valueChanged);
  Q_PROPERTY(float gpsVerticalAccuracy MEMBER gpsVerticalAccuracy NOTIFY valueChanged);
  Q_PROPERTY(float gpsAltitude MEMBER gpsAltitude NOTIFY valueChanged);
  Q_PROPERTY(float gpsAccuracy MEMBER gpsAccuracy NOTIFY valueChanged);
  Q_PROPERTY(float angleSteers MEMBER angleSteers NOTIFY valueChanged);
  Q_PROPERTY(float steerAngleDesired MEMBER steerAngleDesired NOTIFY valueChanged);
  Q_PROPERTY(float steerRatio MEMBER steerRatio NOTIFY valueChanged);
  Q_PROPERTY(float fl MEMBER fl NOTIFY valueChanged);
  Q_PROPERTY(float fr MEMBER fr NOTIFY valueChanged);
  Q_PROPERTY(float rl MEMBER rl NOTIFY valueChanged);
  Q_PROPERTY(float rr MEMBER rr NOTIFY valueChanged);
  
public:
  explicit OnroadHud(QWidget *parent);
  void updateState(const UIState &s);

private:
  void drawIcon(QPainter &p, int x, int y, QPixmap &img, QBrush bg, float opacity);
  void drawIconRotate(QPainter &p, int x, int y, QPixmap &img, QBrush bg, float opacity, float angle);
  void drawText(QPainter &p, int x, int y, const QString &text, int alpha = 255);
  void drawTextColor(QPainter &p, int x, int y, const QString &text, QColor &color);
  void drawTpms(QPainter &p, int x, int y, const QString &text, const QColor &color);
  void paintEvent(QPaintEvent *event) override;
  void drawRightDevUi(QPainter &p, int x, int y);
  int devUiDrawElement(QPainter &p, int x, int y, const char* value, const char* label, const char* units, QColor &color);

  QPixmap engage_img;
  QPixmap dm_img;

  // crwusiz add
  QPixmap brake_img;
  QPixmap bsd_l_img;
  QPixmap bsd_r_img;
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

  QString speed;
  QString speedUnit;
  QString applyMaxSpeed;
  QString cruiseMaxSpeed;

  bool is_cruise_set = false;
  bool engageable = false;
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
  int lateralcontrol_status = 0;
  int mdpsBus = 0;
  int sccBus = 0;
  int camLimitSpeed = 0;
  int camLimitSpeedLeftDist = 0;
  int sectionLimitSpeed = 0;
  int sectionLeftDist = 0;
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

signals:
  void valueChanged();
};

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

public:
  explicit NvgWindow(VisionStreamType type, QWidget* parent = 0) : CameraViewWidget("camerad", type, true, parent) {}

protected:
  void paintGL() override;
  void initializeGL() override;
  void showEvent(QShowEvent *event) override;
  void updateFrameMat(int w, int h) override;
  void drawLaneLines(QPainter &painter, const UIScene &scene);
  void drawLead(QPainter &painter, const cereal::ModelDataV2::LeadDataV3::Reader &lead_data, const QPointF &vd, bool is_radar);
  inline QColor redColor(int alpha = 255) { return QColor(201, 34, 49, alpha); }
  double prev_draw_t = 0;
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
  OnroadHud *hud;
  OnroadAlerts *alerts;
  NvgWindow *nvg;
  QColor bg = bg_colors[STATUS_DISENGAGED];
  QWidget *map = nullptr;
  QHBoxLayout* split;

private slots:
  void offroadTransition(bool offroad);
  void updateState(const UIState &s);
};
