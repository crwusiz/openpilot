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
  void drawTextColor(QPainter &p, int x, int y, int fontSize, const QString &text, const QColor &color, const QString &alignment = "C");
  void drawTextCenter(QPainter &p, const QPoint &center, int fontSize, const QString &text, const QColor &color);
  void draw_blinker(QPainter& p, const QRect& surface_rect, bool is_left, const QPixmap& blinker_img);
  QColor interpColor(float xv, std::vector<float> xp, std::vector<QColor> fp);

  float speed = 0;
  float set_speed = 0;
  bool is_cruise_set = false;
  bool is_cruise_available = true;
  bool is_metric = false;
  bool v_ego_cluster_seen = false;
  int status = STATUS_DISENGAGED;

  // add
  QPixmap steer_img, gaspress_img, brake_img;
  QPixmap wifi_img, wifi_l_img, wifi_m_img, wifi_h_img, wifi_f_img, wifi_ok_img;
  QPixmap gps_img, direction_img, tpms_img;
  QPixmap turnsignal_l_img, turnsignal_r_img;
  QPixmap traffic_off_img, traffic_green_img, traffic_red_img;
  QPixmap lka_on_img, lka_off_img;
  QPixmap dist1_img, dist2_img, dist3_img, dist4_img;
  QPixmap autohold_warning_img, autohold_active_img;
  QPixmap speed_bump_img, school_zone_img, speed_camera_img;

  QString leftDistStr, altitudeStr, accuracyStr, infoGps;
  QString maxSpeedStr, applySpeedStr;

  bool hideBottomIcons = false;
  bool longControl = false;
  bool brake_press, gas_press = false;
  bool left_blinker, right_blinker = false;
  bool lat_active, lka_state = false;

  int accel = 0;
  int gpsSatelliteCount = 0;
  int camLimitSpeed, sectionLimitSpeed = 0;
  int camLimitSpeedLeftDist, sectionLeftDist = 0;
  int wifi_state, traffic_state = 0;
  int blink_index, blink_wait = 0;
  int autohold_state, nda_state = 0;
  int road_signs, stockSpeedLimitDistance = 0;

  float apply_speed, cruise_speed;
  float gpsBearing, gpsVerticalAccuracy, gpsAltitude, gpsAccuracy = 0;
  float steerAngle, steerAngleTarget = 0;
  float fl, fr, rl, rr = 0;
  float roadLimitSpeed, navLimitSpeed, stockLimitSpeed = 0;
  float steer_torque, curvature, steer_ratio = 0;

  double prev_blink_time = 0.0;

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
  inline QColor darkRedColor(int alpha = 255) { return QColor(139, 0, 0, alpha); }
  inline QColor getColorForAngle(double angle) {
    if (std::abs(angle) > 360) {
      return darkRedColor(200);
    } else if (std::abs(angle) > 240) {
      return redColor(200);
    } else if (std::abs(angle) > 120) {
      return orangeColor(200);
    }
    return limeColor(200);
  }
};
