#include "selfdrive/ui/qt/onroad/hud.h"

#include <cmath>

#include "selfdrive/ui/qt/util.h"
#include "selfdrive/ui/qt/onroad/buttons.h"

constexpr int SET_SPEED_NA = 255;

HudRenderer::HudRenderer() {
  steer_img = loadPixmap("../assets/img_chffr_wheel.png", {img_size, img_size});
  gaspress_img = loadPixmap("../assets/offroad/icon_disengage_on_accelerator.svg", {img_size, img_size});

  // crwusiz add
  brake_img = loadPixmap("../assets/img_brake_disc.png", {img_size, img_size});
  gps_img = loadPixmap("../assets/img_gps.png", {img_size, img_size});
  wifi_l_img = loadPixmap("../assets/offroad/icon_wifi_strength_low.svg", {img_size, img_size});
  wifi_m_img = loadPixmap("../assets/offroad/icon_wifi_strength_medium.svg", {img_size, img_size});
  wifi_h_img = loadPixmap("../assets/offroad/icon_wifi_strength_high.svg", {img_size, img_size});
  wifi_f_img = loadPixmap("../assets/offroad/icon_wifi_strength_full.svg", {img_size, img_size});
  wifi_ok_img = loadPixmap("../assets/img_wifi.png", {img_size, img_size});
  direction_img = loadPixmap("../assets/img_direction.png", {img_size, img_size});
  turnsignal_l_img = loadPixmap("../assets/img_turnsignal_l.png", {img_size, img_size});
  turnsignal_r_img = loadPixmap("../assets/img_turnsignal_r.png", {img_size, img_size});
  tpms_img = loadPixmap("../assets/img_tpms.png");
  sign_none_img = loadPixmap("../assets/img_sign_none.png", {img_size, img_size});
  sign_go_img = loadPixmap("../assets/img_sign_go.png", {img_size, img_size});
  sign_stop_img = loadPixmap("../assets/img_sign_stop.png", {img_size, img_size});

  lane_change_left_img = loadPixmap("../assets/lane_change_left.png");
  lane_change_right_img = loadPixmap("../assets/lane_change_right.png");

  // neokii add
  autohold_warning_img = loadPixmap("../assets/img_autohold_warning.png", {img_size, img_size});
  autohold_active_img = loadPixmap("../assets/img_autohold_active.png", {img_size, img_size});
  nda_img = loadPixmap("../assets/img_nda.png");
  hda_img = loadPixmap("../assets/img_hda.png");
}

static const QColor get_tpms_color(float tpms) {
  if (tpms < 5 || tpms > 60)
    return QColor(0, 0, 0, 255); // black color
  if (tpms < 31)
    return QColor(255, 0, 0, 255); // red color
  return QColor(0, 0, 0, 255);
}

static const QString get_tpms_text(float tpms) {
  if (tpms < 5 || tpms > 60) {
    return "─";
  } else {
    int rounded_tpms = qRound(tpms);
    return QString::number(rounded_tpms);
  }
}

void HudRenderer::updateState(const UIState &s) {
  is_metric = s.scene.is_metric;
  status = s.status;

  const SubMaster &sm = *(s.sm);
  if (!sm.alive("carState")) {
    is_cruise_set = false;
    set_speed = SET_SPEED_NA;
    speed = 0.0;
    return;
  }

  const auto ce = sm["carState"].getCarState();
  const auto cp = sm["carParams"].getCarParams();
  const auto ds = sm["deviceState"].getDeviceState();
  const auto ge = sm["gpsLocationExternal"].getGpsLocationExternal();
  const auto nd = sm["naviData"].getNaviData();
  const auto lo = sm["longitudinalPlan"].getLongitudinalPlan();

  // Handle older routes where vCruiseCluster is not set
  apply_speed = ce.getVCruise();
  cruise_speed = ce.getVCruiseCluster();
  is_cruise_set = cruise_speed > 0 && (int)cruise_speed != SET_SPEED_NA && ce.getCruiseState().getSpeed();

  if (is_cruise_set && !is_metric) {
    apply_speed *= KM_TO_MILE;
    cruise_speed *= KM_TO_MILE;
  }

  // Handle older routes where vEgoCluster is not set
  v_ego_cluster_seen = v_ego_cluster_seen || ce.getVEgoCluster() != 0.0;
  float v_ego = v_ego_cluster_seen ? ce.getVEgoCluster() : ce.getVEgo();
  speed = std::max<float>(0.0f, v_ego * (is_metric ? MS_TO_KPH : MS_TO_MPH));

  hideBottomIcons = (sm["selfdriveState"].getSelfdriveState().getAlertSize() != cereal::SelfdriveState::AlertSize::NONE);
  accel = ce.getAEgo();
  brake_press = ce.getBrakeLights();
  autohold_state = ce.getExState().getAutoHold();
  gas_press = ce.getGasPressed();
  left_blinker = ce.getLeftBlinker();
  right_blinker = ce.getRightBlinker();
  wifi_state = (int)ds.getNetworkStrength();
  gpsBearing = ge.getBearingDeg();
  gpsVerticalAccuracy = ge.getVerticalAccuracy();
  gpsAltitude = ge.getAltitude();
  gpsAccuracy = ge.getHorizontalAccuracy();
  gpsSatelliteCount = s.scene.satelliteCount;
  steerAngle = ce.getSteeringAngleDeg();
  longControl = cp.getOpenpilotLongitudinalControl();
  sccBus = cp.getSccBus();
  fl = ce.getExState().getTpms().getFl();
  fr = ce.getExState().getTpms().getFr();
  rl = ce.getExState().getTpms().getRl();
  rr = ce.getExState().getTpms().getRr();
  navLimitSpeed = ce.getExState().getNavLimitSpeed();
  nda_state = nd.getActive();
  roadLimitSpeed = nd.getRoadLimitSpeed();
  camLimitSpeed = nd.getCamLimitSpeed();
  camLimitSpeedLeftDist = nd.getCamLimitSpeedLeftDist();
  sectionLimitSpeed = nd.getSectionLimitSpeed();
  sectionLeftDist = nd.getSectionLeftDist();
  traffic_state = lo.getTrafficState();
}

void HudRenderer::draw(QPainter &p, const QRect &surface_rect) {
  p.save();

  // Draw header gradient
  QLinearGradient bg(0, UI_HEADER_HEIGHT - (UI_HEADER_HEIGHT / 2.5), 0, UI_HEADER_HEIGHT);
  bg.setColorAt(0, QColor::fromRgbF(0, 0, 0, 0.45));
  bg.setColorAt(1, QColor::fromRgbF(0, 0, 0, 0));
  p.fillRect(0, 0, surface_rect.width(), UI_HEADER_HEIGHT, bg);

  drawSetSpeed(p, surface_rect);
  drawCurrentSpeed(p, surface_rect);

  p.restore();

  int x,y,w,h = 0;
  QColor icon_bg = blackColor(100);

  // nda icon (upper center)
  if (nda_state > 0) {
    w = 120;
    h = 54;
    x = (surface_rect.width() + (UI_BORDER_SIZE * 2)) / 2 - (w / 2) - UI_BORDER_SIZE;
    y = UI_BORDER_SIZE * 3;
    p.drawPixmap(x, y, w, h, nda_state == 1 ? nda_img : hda_img);
  }

  // sign icon (upper right 3)
  if (traffic_state >= 0) {
    x = surface_rect.right() - (btn_size / 2) - (UI_BORDER_SIZE * 2) - (btn_size * 2.1);
    y = (btn_size / 2) + (UI_BORDER_SIZE * 4);
    if (traffic_state == 1 && is_cruise_set) {
      drawIcon(p, QPoint(x, y), sign_stop_img, icon_bg, 0.8);
    } else if (traffic_state == 2 && is_cruise_set) {
      drawIcon(p, QPoint(x, y), sign_go_img, icon_bg, 0.8);
    } else {
      drawIcon(p, QPoint(x, y), sign_none_img, icon_bg, 0.2);
    }
  }

  // N direction icon (upper right 4)
  x = surface_rect.right() - (btn_size / 2) - (UI_BORDER_SIZE * 2) - (btn_size * 1.1);
  y = (btn_size / 2) + (UI_BORDER_SIZE * 20);
  drawIconRotate(p, QPoint(x, y), direction_img, icon_bg, gpsSatelliteCount != 0 ? 0.8 : 0.2, gpsBearing);

  // gps icon (upper right 3)
  x = surface_rect.right() - (btn_size / 2) - (UI_BORDER_SIZE * 2) - (btn_size * 0.1);
  y = (btn_size / 2) + (UI_BORDER_SIZE * 20);
  drawIcon(p, QPoint(x, y), gps_img, icon_bg, gpsSatelliteCount != 0 ? 0.8 : 0.2);

  if (wifi_state == 1) {
    wifi_img = wifi_l_img;
  } else if (wifi_state == 2) {
    wifi_img = wifi_m_img;
  } else if (wifi_state == 3) {
    wifi_img = wifi_h_img;
  } else {
    wifi_img = wifi_f_img;
  }

  // wifi icon (upper right 2)
  x = surface_rect.right() - (btn_size / 2) - (UI_BORDER_SIZE * 2) - (btn_size * 1.1);
  y = (btn_size / 2) + (UI_BORDER_SIZE * 4);
  drawIcon(p, QPoint(x, y), wifi_img, icon_bg, wifi_state > 0 ? 0.8 : 0.2);

  // upper gps info
  if (gpsVerticalAccuracy == 0 || gpsVerticalAccuracy > 100) {
    altitudeStr = "--";
  } else {
    altitudeStr = QString::asprintf("%.1f m", gpsAltitude);
  }

  if (gpsAccuracy == 0 || gpsAccuracy > 100) {
    accuracyStr = "--";
  } else {
    accuracyStr = QString::asprintf("%.1f m", gpsAccuracy);
  }

  if (gpsSatelliteCount == 0) {
    infoGps = "🛰️[ No Gps Signal ]";
  } else {
    infoGps = QString::asprintf("🛰️[ Alt(%s) Acc(%s) Sat(%d) ]",
                                altitudeStr.toStdString().c_str(),
                                accuracyStr.toStdString().c_str(),
                                gpsSatelliteCount);
  }

  x = surface_rect.right() - 20;
  y = (UI_BORDER_SIZE * 2);

  p.setFont(InterFont(30));
  drawTextColorLR(p, x, y, infoGps, whiteColor(200), "R");

  if (!hideBottomIcons) {
    // steer img (bottom 1 right)
    x = (btn_size / 2) + (UI_BORDER_SIZE * 2) + (btn_size);
    y = surface_rect.bottom() - (UI_FOOTER_HEIGHT / 2);
    drawIconRotate(p, QPoint(x, y), steer_img, icon_bg, 0.8, steerAngle);

    QColor sa_color = limeColor(200);
    if (std::fabs(steerAngle) > 90) {
      sa_color = redColor(200);
    } else if (std::fabs(steerAngle) > 30) {
      sa_color = orangeColor(200);
    }

    if (steerAngle > 0) {
      sa_direction = QString("◀");
    } else if (steerAngle < 0) {
      sa_direction = QString("▶");
    } else {
      sa_direction = QString("●");
    }

    sa_str = QString::asprintf("%.0f °", steerAngle);
    p.setFont(InterFont(30, QFont::Bold));
    drawTextColor(p, x - 30, y + 95, sa_str, sa_color);
    drawTextColor(p, x + 30, y + 95, sa_direction, whiteColor(200));

    // gaspress img (bottom right 2)
    x = surface_rect.right() - (btn_size / 2) - (UI_BORDER_SIZE * 2) - (btn_size * 2.1);
    drawIcon(p, QPoint(x, y), gaspress_img, icon_bg, gas_press ? 0.8 : 0.2);

    // brake and autohold icon (bottom right 3)
    x = surface_rect.right() - (btn_size / 2) - (UI_BORDER_SIZE * 2) - (btn_size * 1.1);
    if (autohold_state >= 1) {
      drawIcon(p, QPoint(x, y), autohold_state > 1 ? autohold_warning_img : autohold_active_img, icon_bg, autohold_state ? 0.8 : 0.2);
    } else {
      drawIcon(p, QPoint(x, y), brake_img, icon_bg, brake_press ? 0.8 : 0.2);
    }

    // tpms (bottom right)
    w = 160;
    h = 208;
    x = surface_rect.right() - w - (UI_BORDER_SIZE * 2);
    y = surface_rect.height() - h - (UI_BORDER_SIZE * 2);

    p.drawPixmap(x, y, w, h, tpms_img);

    p.setFont(InterFont(30, QFont::Bold));
    drawTextColor(p, x + 25, y + 56, get_tpms_text(fl), get_tpms_color(fl));
    drawTextColor(p, x + 133, y + 56, get_tpms_text(fr), get_tpms_color(fr));
    drawTextColor(p, x + 25, y + 171, get_tpms_text(rl), get_tpms_color(rl));
    drawTextColor(p, x + 133, y + 171, get_tpms_text(rr), get_tpms_color(rr));
  }

  // bottom left info
  QString infoText = QString("[%1]").arg(QString::fromStdString(params.get("CarName")));

  x = surface_rect.left() + 20;
  y = surface_rect.height() - 20;

  p.setFont(InterFont(30));
  drawTextColorLR(p, x, y, infoText, whiteColor(200), "L");

  // turnsignal
  static int blink_index = 0;
  static int blink_wait = 0;
  static double prev_ts = 0.0;

  if (blink_wait > 0) {
    blink_wait--;
    blink_index = 0;
  } else {
    const float img_alpha = 0.8f;
    const int center_x = surface_rect.width() / 2;
    const int draw_count = 8;
    w = 200;
    h = 200;
    y = (surface_rect.height() - h) / 2;

    x = center_x;
    if (left_blinker) {
      for (int i = 0; i < draw_count; i++) {
        float alpha = img_alpha;
        int d = std::abs(blink_index - i);
        if (d > 0)
          alpha /= d * 2;
        p.setOpacity(alpha);
        p.drawPixmap(x - w, y, w, h, turnsignal_l_img);
        x -= w * 0.6;
      }
    }

    x = center_x;
    if (right_blinker) {
      for (int i = 0; i < draw_count; i++) {
        float alpha = img_alpha;
        int d = std::abs(blink_index - i);
        if (d > 0)
          alpha /= d * 2;
        p.setOpacity(alpha);
        p.drawPixmap(x, y, w, h, turnsignal_r_img);
        x += w * 0.6;
      }
    }

    if (left_blinker || right_blinker) {
      double now = millis_since_boot();
      if (now - prev_ts > 900 / UI_FREQ) {
        prev_ts = now;
        blink_index++;
      }
      if (blink_index >= draw_count) {
        blink_index = draw_count - 1;
        blink_wait = UI_FREQ / 4;
      }
    } else {
      blink_index = 0;
    }
  }
  p.setOpacity(1.0);
}

void HudRenderer::drawSetSpeed(QPainter &p, const QRect &surface_rect) {
  // max speed, apply speed, speed limit sign init
  float limit_speed = 0;
  float left_dist = 0;

  if (nda_state > 0) {
    if (camLimitSpeed > 0 && camLimitSpeedLeftDist > 0) {
      limit_speed = camLimitSpeed;
      left_dist = camLimitSpeedLeftDist;
    } else if (sectionLimitSpeed > 0 && sectionLeftDist > 0) {
      limit_speed = sectionLimitSpeed;
      left_dist = sectionLeftDist;
    } else {
      limit_speed = roadLimitSpeed;
    }
  } else {
    limit_speed = navLimitSpeed;
  }

  QString roadLimitSpeedStr = QString::number(roadLimitSpeed, 'f', 0);
  QString limitSpeedStr = QString::number(limit_speed, 'f', 0);

  if (left_dist >= 1000) {
    leftDistStr = QString::asprintf("%.1f km", left_dist / 1000.0);
  } else if (left_dist > 0) {
    leftDistStr = QString::asprintf("%.0f m", left_dist);
  }

  int rect_width = 300;
  int rect_height = 188;

  QRect max_speed_rect(30, 30, rect_width, rect_height);
  p.setPen(Qt::NoPen);
  p.setBrush(blackColor(100));
  p.drawRoundedRect(max_speed_rect, 32, 32);
  //drawRoundedRect(p, max_speed_rect, top_radius, top_radius, bottom_radius, bottom_radius);

  // max speed (upper left 1)
  QString cruiseSpeedStr = QString::number(std::nearbyint(cruise_speed));
  QRect max_speed_outer(max_speed_rect.left() + 10, max_speed_rect.top() + 10, 140, 168);
  p.setPen(QPen(whiteColor(200), 2));
  p.drawRoundedRect(max_speed_outer, 16, 16);

  if (limit_speed > 0 && status != STATUS_DISENGAGED && status != STATUS_OVERRIDE) {
    p.setPen(interpColor(
      cruise_speed,
      {limit_speed + 5, limit_speed + 15, limit_speed + 25},
      {whiteColor(), orangeColor(), redColor()}
    ));
  } else {
    p.setPen(whiteColor());
  }
  p.setFont(InterFont(65));
  QRect speed_rect = getTextRect(p, Qt::AlignCenter, cruiseSpeedStr);
  speed_rect.moveCenter({max_speed_outer.center().x(), 0});
  speed_rect.moveTop(max_speed_rect.top() + 90);
  p.drawText(speed_rect, Qt::AlignCenter, is_cruise_set ? cruiseSpeedStr : "─");

  if (status == STATUS_DISENGAGED) {
    p.setPen(whiteColor());
  } else if (status == STATUS_OVERRIDE) {
    p.setPen(overrideColor());
  } else if (limit_speed > 0) {
    p.setPen(interpColor(
      cruise_speed,
      {limit_speed + 5, limit_speed + 15, limit_speed + 25},
      {greenColor(), lightorangeColor(), pinkColor()}
    ));
  } else {
    p.setPen(greenColor());
  }
  p.setFont(InterFont(35, QFont::Bold));
  QRect max_rect = getTextRect(p, Qt::AlignCenter, "MAX");
  max_rect.moveCenter({max_speed_outer.center().x(), 0});
  max_rect.moveTop(max_speed_rect.top() + 25);
  p.drawText(max_rect, Qt::AlignCenter, tr("MAX"));

  // apply speed (upper left 2)
  QString applySpeedStr = QString::number(std::nearbyint(apply_speed));
  QRect apply_speed_outer(max_speed_rect.right() - 150, max_speed_rect.top() + 10, 140, 168);
  p.setPen(QPen(whiteColor(200), 2));
  p.drawRoundedRect(apply_speed_outer, 16, 16);

  if (limit_speed > 0 && status != STATUS_DISENGAGED && status != STATUS_OVERRIDE) {
    p.setPen(interpColor(
      apply_speed,
      {limit_speed + 5, limit_speed + 15, limit_speed + 25},
      {whiteColor(), orangeColor(), redColor()}
    ));
  } else {
    p.setPen(whiteColor());
  }
  p.setFont(InterFont(65));
  QRect apply_rect = getTextRect(p, Qt::AlignCenter, applySpeedStr);
  apply_rect.moveCenter({apply_speed_outer.center().x(), 0});
  apply_rect.moveTop(max_speed_rect.top() + 90);
  p.drawText(apply_rect, Qt::AlignCenter, is_cruise_set ? applySpeedStr : "─");

  if (status == STATUS_DISENGAGED) {
    p.setPen(whiteColor());
  } else if (status == STATUS_OVERRIDE) {
    p.setPen(overrideColor());
  } else if (limit_speed > 0) {
    p.setPen(interpColor(
      apply_speed,
      {limit_speed + 5, limit_speed + 15, limit_speed + 25},
      {greenColor(), lightorangeColor(), pinkColor()}
    ));
  } else {
    p.setPen(greenColor());
  }
  p.setFont(InterFont(35, QFont::Bold));
  QRect long_rect = getTextRect(p, Qt::AlignCenter, "SET");
  long_rect.moveCenter({apply_speed_outer.center().x(), 0});
  long_rect.moveTop(max_speed_rect.top() + 25);
  p.drawText(long_rect, Qt::AlignCenter, tr("SET"));

  // speedlimit sign
  if (limit_speed > 0 && left_dist > 0) {
    QPoint center(max_speed_rect.center().x(), max_rect.top() + 280);
    p.setPen(Qt::NoPen);
    p.setBrush(whiteColor());
    p.drawEllipse(center, 92, 92);
    p.setBrush(redColor());
    p.drawEllipse(center, 86, 86);
    p.setBrush(whiteColor());
    p.drawEllipse(center, 66, 66);

    p.setFont(InterFont(60, QFont::Bold));
    QRect limit_rect = getTextRect(p, Qt::AlignCenter, limitSpeedStr);
    limit_rect.moveCenter(center);
    p.setPen(blackColor());
    p.drawText(limit_rect, Qt::AlignCenter, limitSpeedStr);

    p.setFont(InterFont(50, QFont::Bold));
    QRect left_rect = getTextRect(p, Qt::AlignCenter, leftDistStr);
    left_rect.moveCenter({max_speed_rect.center().x(), 0});
    left_rect.moveBottom(max_speed_rect.bottom() + 265);
    p.setPen(whiteColor());
    p.drawText(left_rect, Qt::AlignCenter, leftDistStr);
  } else if (roadLimitSpeed > 0 && roadLimitSpeed < 120) {
    QPoint center(max_speed_rect.center().x(), max_rect.top() + 280);
    p.setPen(Qt::NoPen);
    p.setBrush(whiteColor());
    p.drawEllipse(center, 92, 92);
    p.setBrush(redColor());
    p.drawEllipse(center, 86, 86);
    p.setBrush(whiteColor());
    p.drawEllipse(center, 66, 66);

    p.setFont(InterFont(60, QFont::Bold));
    QRect roadlimit_rect = getTextRect(p, Qt::AlignCenter, roadLimitSpeedStr);
    roadlimit_rect.moveCenter(center);
    p.setPen(blackColor());
    p.drawText(roadlimit_rect, Qt::AlignCenter, roadLimitSpeedStr);
  } else if (limit_speed > 0) {
    QPoint center(max_speed_rect.center().x(), max_rect.top() + 280);
    p.setPen(Qt::NoPen);
    p.setBrush(whiteColor());
    p.drawEllipse(center, 92, 92);
    p.setBrush(redColor());
    p.drawEllipse(center, 86, 86);
    p.setBrush(whiteColor());
    p.drawEllipse(center, 66, 66);

    p.setFont(InterFont(60, QFont::Bold));
    QRect limit_rect = getTextRect(p, Qt::AlignCenter, limitSpeedStr);
    limit_rect.moveCenter(center);
    p.setPen(blackColor());
    p.drawText(limit_rect, Qt::AlignCenter, limitSpeedStr);
  }
}

void HudRenderer::drawCurrentSpeed(QPainter &p, const QRect &surface_rect) {
  QString speedStr = QString::number(std::nearbyint(speed));
  QColor variableColor = whiteColor();

  if (accel > 0) {
    int a = (int)(255.f - (180.f * (accel/3.f)));
    a = std::min(a, 255);
    a = std::max(a, 80);
    variableColor = QColor(a, 255, a, 200);
  } else {
    int a = (int)(255.f - (255.f * (-accel/4.f)));
    a = std::min(a, 255);
    a = std::max(a, 60);
    variableColor = QColor(255, a, a, 200);
  }

  p.setFont(InterFont(176, QFont::Bold));
  drawTextColor(p, surface_rect.center().x(), 220, speedStr, variableColor);

  p.setFont(InterFont(66));
  drawTextColor(p, surface_rect.center().x(), 290, is_metric ? tr("km/h") : tr("mph"), lightorangeColor());
}

void HudRenderer::drawText(QPainter &p, int x, int y, const QString &text, int alpha) {
  QRect real_rect = p.fontMetrics().boundingRect(text);
  real_rect.moveCenter({x, y - real_rect.height() / 2});
  p.setPen(QColor(0xff, 0xff, 0xff, alpha));
  p.drawText(real_rect.x(), real_rect.bottom(), text);
}

void HudRenderer::drawTextColor(QPainter &p, int x, int y, const QString &text, const QColor &color) {
  p.setOpacity(1.0);
  QRect real_rect = p.fontMetrics().boundingRect(text);
  real_rect.moveCenter({x, y - real_rect.height() / 2});
  p.setPen(color);
  p.drawText(real_rect.x(), real_rect.bottom(), text);
}

void HudRenderer::drawTextColorLR(QPainter &p, int x, int y, const QString &text, const QColor &color, const QString &alignment) {
  p.setOpacity(1.0);
  QRect real_rect = p.fontMetrics().boundingRect(text);

  if (alignment == "L") {
    real_rect.moveLeft(x);
  } else if (alignment == "R") {
    real_rect.moveRight(x);
  }

  real_rect.moveTop(y - real_rect.height() / 2);
  p.setPen(color);
  p.drawText(real_rect, text);
}
