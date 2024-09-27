#pragma once

#include <QPainter>
#include "selfdrive/ui/ui.h"

class HudRenderer : public QObject {
  Q_OBJECT

public:
  HudRenderer();
  void updateState(const UIState &s);
  void draw(QPainter &p, const QRect &surface_rect);

private:
  void drawSetSpeed(QPainter &p, const QRect &surface_rect);
  void drawCurrentSpeed(QPainter &p, const QRect &surface_rect);
  void drawText(QPainter &p, int x, int y, const QString &text, int alpha = 255);
  void drawTextColor(QPainter &p, int x, int y, const QString &text, const QColor &color);
  void drawTextColorLR(QPainter &p, int x, int y, const QString &text, const QColor &color, const QString &alignment);

  float speed = 0;
  float set_speed = 0;
  bool is_cruise_set = false;
  bool is_metric = false;
  bool v_ego_cluster_seen = false;
  int status = STATUS_DISENGAGED;

  // add
  QPixmap steer_img, gaspress_img, brake_img;
  QPixmap wifi_img, wifi_l_img, wifi_m_img, wifi_h_img, wifi_f_img, wifi_ok_img;
  QPixmap gps_img, direction_img, tpms_img;
  QPixmap turnsignal_l_img, turnsignal_r_img;
  QPixmap sign_go_img, sign_stop_img, sign_none_img;
  QPixmap lane_change_left_img, lane_change_right_img;
  QPixmap autohold_warning_img;
  QPixmap autohold_active_img;
  QPixmap nda_img, hda_img;

  QString leftDistStr, sa_str, sa_direction, altitudeStr, accuracyStr, infoGps;

  bool hideBottomIcons = false;
  bool longControl = false;
  bool brake_press, gas_press = false;
  bool left_blinker, right_blinker = false;

  int autohold_state, nda_state = 0;
  int wifi_state = 0;
  int gpsSatelliteCount = 0;
  int accel, sccBus = 0;
  int camLimitSpeed, sectionLimitSpeed = 0;
  int camLimitSpeedLeftDist, sectionLeftDist = 0;
  int traffic_state = 0;

  float apply_speed, cruise_speed;
  float gpsBearing, gpsVerticalAccuracy, gpsAltitude, gpsAccuracy = 0;
  float steerAngle = 0;
  float fl, fr, rl, rr = 0;
  float roadLimitSpeed, navLimitSpeed = 0;
  Params params;

protected:
  // add
  inline QColor redColor(int alpha = 255) { return QColor(201, 34, 49, alpha); }
  inline QColor whiteColor(int alpha = 255) { return QColor(255, 255, 255, alpha); }
  inline QColor blackColor(int alpha = 255) { return QColor(0, 0, 0, alpha); }
  inline QColor limeColor(int alpha = 255) { return QColor(120, 255, 120, alpha); }
  inline QColor orangeColor(int alpha = 255) { return QColor(255, 149, 0, alpha); }
  inline QColor lightorangeColor(int alpha = 255) { return QColor(255, 228, 191, alpha); }
  inline QColor overrideColor(int alpha = 255) { return QColor(145, 155, 149, alpha); }
  inline QColor greenColor(int alpha = 255) { return QColor(128, 216, 166, alpha); }
  inline QColor pinkColor(int alpha = 255) { return QColor(255, 191, 191, alpha); }
};
