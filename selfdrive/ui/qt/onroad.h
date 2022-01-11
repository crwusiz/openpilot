#pragma once

#include <QStackedLayout>
#include <QWidget>

#include "selfdrive/ui/qt/widgets/cameraview.h"
#include "selfdrive/ui/ui.h"

// ***** onroad widgets *****
// wirelessnet2 add

class OnroadHud : public QWidget {
  Q_OBJECT
  Q_PROPERTY(QString speed MEMBER speed NOTIFY valueChanged);
  Q_PROPERTY(QString speedUnit MEMBER speedUnit NOTIFY valueChanged);
  Q_PROPERTY(QString maxSpeed MEMBER maxSpeed NOTIFY valueChanged);
  Q_PROPERTY(bool is_cruise_set MEMBER is_cruise_set NOTIFY valueChanged);
  Q_PROPERTY(bool engageable MEMBER engageable NOTIFY valueChanged);
  Q_PROPERTY(bool dmActive MEMBER dmActive NOTIFY valueChanged);
  Q_PROPERTY(bool hideDM MEMBER hideDM NOTIFY valueChanged);
  Q_PROPERTY(int status MEMBER status NOTIFY valueChanged);
  Q_PROPERTY(bool brake_stat MEMBER brake_stat NOTIFY valueChanged);

  Q_PROPERTY(int autohold_stat MEMBER autohold_stat NOTIFY valueChanged);
  Q_PROPERTY(bool bsd_l_stat MEMBER bsd_l_stat NOTIFY valueChanged);
  Q_PROPERTY(bool bsd_r_stat MEMBER bsd_r_stat NOTIFY valueChanged);
  Q_PROPERTY(int wifi_stat MEMBER wifi_stat NOTIFY valueChanged);
  Q_PROPERTY(bool gps_stat MEMBER gps_stat NOTIFY valueChanged);

  Q_PROPERTY(int lead_status MEMBER lead_status NOTIFY valueChanged);
  Q_PROPERTY(float lead_d_rel MEMBER lead_d_rel NOTIFY valueChanged);
  Q_PROPERTY(float lead_v_rel MEMBER lead_v_rel NOTIFY valueChanged);
  Q_PROPERTY(float angleSteers MEMBER angleSteers NOTIFY valueChanged);
  Q_PROPERTY(float steerAngleDesired MEMBER steerAngleDesired NOTIFY valueChanged);

public:
  explicit OnroadHud(QWidget *parent);
  void updateState(const UIState &s);

private:
  void drawIcon(QPainter &p, int x, int y, QPixmap &img, QBrush bg, float opacity);
  void drawText(QPainter &p, int x, int y, const QString &text, int alpha = 255);
  void paintEvent(QPaintEvent *event) override;
  void drawLeftDevUi(QPainter &p, int x, int y);
  int devUiDrawElement(QPainter &p, int x, int y, const char* value, const char* label, const char* units, QColor &color);
  void drawColoredText(QPainter &p, int x, int y, const QString &text, QColor &color);

  QPixmap engage_img;
  QPixmap dm_img;

  // wirelessnet2 add
  QPixmap brake_img;

  // crwusiz add
  QPixmap bsd_l_img;
  QPixmap bsd_r_img;
  QPixmap gps_img;
  QPixmap wifi_img;

  // neokii
  QPixmap autohold_warning_img;
  QPixmap autohold_active_img;

  const int radius = 160;
  const int img_size = (radius / 2) * 1.5;
  QString speed;
  QString speedUnit;
  QString maxSpeed;
  bool is_cruise_set = false;
  bool engageable = false;
  bool dmActive = false;
  bool hideDM = false;
  bool brake_stat = false;
  int status = STATUS_DISENGAGED;

  int autohold_stat = 0;
  bool bsd_l_stat = false;
  bool bsd_r_stat = false;
  int wifi_stat = 0;
  bool gps_stat = false;

  int lead_status;
  float lead_d_rel = 0;
  float lead_v_rel = 0;
  float angleSteers = 0;
  float steerAngleDesired = 0;
  float cpuTemp = 0;

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

  // neokii add start
  void drawIcon(QPainter &p, int x, int y, QPixmap &img, QBrush bg, float opacity);
  void drawText(QPainter &p, int x, int y, const QString &text, int alpha = 255);
  void drawTextFlag(QPainter &p, int x, int y, int flags, const QString &text, const QColor& color);
  void drawTextColor(QPainter &p, int x, int y, const QString &text, QColor& color);
  void paintEvent(QPaintEvent *event) override;

  const int radius = 192;
  const int img_size = (radius / 2) * 1.5;

  // neokii
  QPixmap nda_img;
  QPixmap hda_img;
  QPixmap turnsignal_l_img;
  QPixmap turnsignal_r_img;
  QPixmap tire_pressure_img;

  void drawMaxSpeed(QPainter &p);
  void drawSpeed(QPainter &p);
  void drawIcons(QPainter &p);
  void drawSpeedLimit(QPainter &p);
  void drawTurnSignals(QPainter &p);
  void drawHud(QPainter &p);
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

signals:
  void updateStateSignal(const UIState &s);
  void offroadTransitionSignal(bool offroad);

private slots:
  void offroadTransition(bool offroad);
  void updateState(const UIState &s);
};
