#include "selfdrive/ui/qt/onroad/hud.h"

#include <cmath>
#include <QDateTime>

#include "selfdrive/ui/qt/util.h"
#include "selfdrive/ui/qt/onroad/buttons.h"

constexpr int SET_SPEED_NA = 255;
constexpr int BLINKER_DRAW_COUNT = 8;

HudRenderer::HudRenderer() {
  steer_img = loadPixmap("../assets/icons/steer.png", {img_size, img_size});
  gaspress_img = loadPixmap("../assets/icons/disengage_on_accelerator.svg", {img_size, img_size});

  // crwusiz add
  brake_img = loadPixmap("../assets/icons/brake_disc.png", {img_size, img_size});
  gps_img = loadPixmap("../assets/icons/gps.png", {img_size, img_size});
  wifi_l_img = loadPixmap("../assets/icons/wifi_strength_low.svg", {img_size, img_size});
  wifi_m_img = loadPixmap("../assets/icons/wifi_strength_medium.svg", {img_size, img_size});
  wifi_h_img = loadPixmap("../assets/icons/wifi_strength_high.svg", {img_size, img_size});
  wifi_f_img = loadPixmap("../assets/icons/wifi_strength_full.svg", {img_size, img_size});
  wifi_ok_img = loadPixmap("../assets/icons/wifi.png", {img_size, img_size});
  direction_img = loadPixmap("../assets/icons/direction.png", {img_size, img_size});
  turnsignal_l_img = loadPixmap("../assets/icons/turnsignal_l.png", {img_size, img_size});
  turnsignal_r_img = loadPixmap("../assets/icons/turnsignal_r.png", {img_size, img_size});
  tpms_img = loadPixmap("../assets/icons/tpms.png");
  traffic_off_img = loadPixmap("../assets/icons/traffic_off.png", {img_size, img_size});
  traffic_green_img = loadPixmap("../assets/icons/traffic_green.png", {img_size, img_size});
  traffic_red_img = loadPixmap("../assets/icons/traffic_red.png", {img_size, img_size});
  lka_on_img = loadPixmap("../assets/icons/lka_on.png", {img_size, img_size});
  lka_off_img = loadPixmap("../assets/icons/lka_off.png", {img_size, img_size});
  dist1_img = loadPixmap("../assets/icons/dist1.png", {img_size, img_size});
  dist2_img = loadPixmap("../assets/icons/dist2.png", {img_size, img_size});
  dist3_img = loadPixmap("../assets/icons/dist3.png", {img_size, img_size});
  dist4_img = loadPixmap("../assets/icons/dist4.png", {img_size, img_size});

  // neokii add
  autohold_warning_img = loadPixmap("../assets/icons/autohold_warning.png", {img_size, img_size});
  autohold_active_img = loadPixmap("../assets/icons/autohold_active.png", {img_size, img_size});
  speed_bump_img = loadPixmap("../assets/icons/speed_bump.png");
  school_zone_img = loadPixmap("../assets/icons/school_zone.png");
  speed_camera_img = loadPixmap("../assets/icons/speed_camera.png");
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
  if (sm.rcv_frame("carState") < s.scene.started_frame) {
    is_cruise_set = false;
    set_speed = SET_SPEED_NA;
    speed = 0.0;
    return;
  }

  const auto ce = sm["carState"].getCarState();
  const auto cc = sm["carControl"].getCarControl();
  const auto cp = sm["carParams"].getCarParams();
  const auto ds = sm["deviceState"].getDeviceState();
  const auto ge = sm["gpsLocationExternal"].getGpsLocationExternal();
  const auto nd = sm["naviData"].getNaviData();
  const auto lo = sm["longitudinalPlan"].getLongitudinalPlan();
  const auto lp = sm["liveParameters"].getLiveParameters();

  // Handle older routes where vCruiseCluster is not set
  apply_speed = ce.getVCruise();
  cruise_speed = ce.getVCruiseCluster();
  is_cruise_set = cruise_speed > 0 && (int)cruise_speed != SET_SPEED_NA && ce.getCruiseState().getSpeed();
  is_cruise_available = set_speed != -1;

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
  fl = ce.getExState().getTpms().getFl();
  fr = ce.getExState().getTpms().getFr();
  rl = ce.getExState().getTpms().getRl();
  rr = ce.getExState().getTpms().getRr();
  navLimitSpeed = ce.getExState().getNavLimitSpeed();
  stockLimitSpeed = ce.getSpeedLimit();
  stockSpeedLimitDistance = ce.getSpeedLimitDistance();
  road_signs = ce.getExState().getRoadSigns();
  nda_state = nd.getActive();
  roadLimitSpeed = nd.getRoadLimitSpeed();
  camLimitSpeed = nd.getCamLimitSpeed();
  camLimitSpeedLeftDist = nd.getCamLimitSpeedLeftDist();
  sectionLimitSpeed = nd.getSectionLimitSpeed();
  sectionLeftDist = nd.getSectionLeftDist();
  traffic_state = lo.getTrafficState();
  lka_state = ce.getCruiseState().getAvailable();
  lat_active = cc.getLatActive();
  steerAngleTarget = cc.getActuators().getSteeringAngleDeg();
  steer_torque = ce.getSteeringTorque();
  curvature = cc.getActuators().getCurvature();
  steer_ratio = lp.getSteerRatio();
}

void HudRenderer::draw(QPainter &p, const QRect &surface_rect) {
  p.save();

  // Draw header gradient
  QLinearGradient bg(0, UI_HEADER_HEIGHT - (UI_HEADER_HEIGHT / 2.5), 0, UI_HEADER_HEIGHT);
  bg.setColorAt(0, QColor::fromRgbF(0, 0, 0, 0.45));
  bg.setColorAt(1, QColor::fromRgbF(0, 0, 0, 0));
  p.fillRect(0, 0, surface_rect.width(), UI_HEADER_HEIGHT, bg);

  if (is_cruise_available) {
    drawSetSpeed(p, surface_rect);
  }
  drawCurrentSpeed(p, surface_rect);

  p.restore();

  int x,y,w,h = 0;
  QColor icon_bg = blackColor(100);

  // traffic icon
  w = 77;
  h = 154;
  x = 205;
  y = (UI_BORDER_SIZE * 2.6);
  if (traffic_state == 1) {
    p.drawPixmap(x, y, w, h, traffic_red_img);
  } else if (traffic_state == 2) {
    p.drawPixmap(x, y, w, h, traffic_green_img);
  } else {
    p.drawPixmap(x, y, w, h, traffic_off_img);
  }

  // road signs icon
  w = 150;
  h = 150;
  x = 440;
  y = (UI_BORDER_SIZE * 3.5);
  //if (road_signs == 1) {
  //  p.drawPixmap(x, y, w, h, speed_bump_img);
  if (road_signs == 1) {
    p.drawPixmap(x, y, w, h, school_zone_img);
  } else if (camLimitSpeed > 0 && camLimitSpeedLeftDist > 0) {
    p.drawPixmap(x, y, w, h, speed_camera_img);
  }

  // upper left info
  x = surface_rect.left() + 20;
  y = (UI_BORDER_SIZE);

  QString carName = QString("%1").arg(QString::fromStdString(params.get("CarName")));

  drawTextColor(p, x, y, 30, carName, whiteColor(200), "L");

  // NDA State
  x = surface_rect.left() + 400;
  if (nda_state > 0) {
    QString ndaText = "NDA";
    int ndaTextWidth = p.fontMetrics().horizontalAdvance(ndaText);
    int ndaTextHeight = p.fontMetrics().height();
    p.setPen(Qt::NoPen);
    p.setBrush(blackColor(200));
    p.drawRoundedRect(x - 10, y - 20, ndaTextWidth + 20, ndaTextHeight + 10, 15, 15);
    drawTextColor(p, x, y, 30, ndaText, limeColor(200), "L");
    x += ndaTextWidth + 24;
  } else if (stockLimitSpeed > 0) {
    QString stockText = "Stock";
    int stockTextWidth = p.fontMetrics().horizontalAdvance(stockText);
    int stockTextHeight = p.fontMetrics().height();
    p.setPen(Qt::NoPen);
    p.setBrush(blackColor(200));
    p.drawRoundedRect(x - 10, y - 20, stockTextWidth + 20, stockTextHeight + 10, 15, 15);
    drawTextColor(p, x, y, 30, stockText, limeColor(200), "L");
    x += stockTextWidth + 24;
  }

  // CameraScc Setting
  if (params.getBool("CameraSccEnable")) {
    QString cameraSccText = "CamScc";
    int cameraSccTextWidth = p.fontMetrics().horizontalAdvance(cameraSccText);
    int cameraSccTextHeight = p.fontMetrics().height();
    p.setPen(Qt::NoPen);
    p.setBrush(blackColor(200));
    p.drawRoundedRect(x - 10, y - 20, cameraSccTextWidth + 20, cameraSccTextHeight + 10, 15, 15);
    drawTextColor(p, x, y, 30, cameraSccText, limeColor(200), "L");
    x += cameraSccTextWidth + 24;
  }

  // HDA2 Setting
  if (params.getBool("IsHda2")) {
    QString hda2Text = "HDA2";
    int hda2TextWidth = p.fontMetrics().horizontalAdvance(hda2Text);
    int hda2TextHeight = p.fontMetrics().height();
    p.setPen(Qt::NoPen);
    p.setBrush(blackColor(200));
    p.drawRoundedRect(x - 10, y - 20, hda2TextWidth + 20, hda2TextHeight + 10, 15, 15);
    drawTextColor(p, x, y, 30, hda2Text, limeColor(200), "L");
    x += hda2TextWidth + 24;
  }

  // LongitudinalControl Setting
  if (params.getBool("AlphaLongitudinalEnabled")) {
    QString longControlText = "Long";
    int longControlTextWidth = p.fontMetrics().horizontalAdvance(longControlText);
    int longControlTextHeight = p.fontMetrics().height();
    p.setPen(Qt::NoPen);
    p.setBrush(blackColor(200));
    p.drawRoundedRect(x - 10, y - 20, longControlTextWidth + 20, longControlTextHeight + 10, 15, 15);
    drawTextColor(p, x, y, 30, longControlText, limeColor(200), "L");
    x += longControlTextWidth + 24;
  }

  // RadarTrack
  if (params.getBool("RadarTrackEnable")) {
    QString radarTrackText = "Radar";
    int radarTrackTextWidth = p.fontMetrics().horizontalAdvance(radarTrackText);
    int radarTrackTextHeight = p.fontMetrics().height();
    p.setPen(Qt::NoPen);
    p.setBrush(blackColor(200));
    p.drawRoundedRect(x - 10, y - 20, radarTrackTextWidth + 20, radarTrackTextHeight + 10, 15, 15);
    drawTextColor(p, x, y, 30, radarTrackText, limeColor(200), "L");
    x += radarTrackTextWidth + 24;
  }

  // N direction icon
  x = surface_rect.right() - (btn_size / 2) - (UI_BORDER_SIZE * 2) - (btn_size * 3);
  y = (btn_size / 2) + (UI_BORDER_SIZE * 2);
  drawIcon(p, QPoint(x, y), direction_img, icon_bg, gpsSatelliteCount != 0 ? 0.8 : 0.2, gpsBearing);

  // gps icon
  x = surface_rect.right() - (btn_size / 2) - (UI_BORDER_SIZE * 2) - (btn_size * 2);
  y = (btn_size / 2) + (UI_BORDER_SIZE * 2);
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

  // wifi icon
  x = surface_rect.right() - (btn_size / 2) - (UI_BORDER_SIZE * 2) - (btn_size * 1);
  y = (btn_size / 2) + (UI_BORDER_SIZE * 2);
  drawIcon(p, QPoint(x, y), wifi_img, icon_bg, wifi_state > 0 ? 0.8 : 0.2);

  // upper right info
  altitudeStr = (gpsVerticalAccuracy == 0 || gpsVerticalAccuracy > 100) ? "--" : QString::asprintf("%.1f m", gpsAltitude);
  accuracyStr = (gpsAccuracy == 0 || gpsAccuracy > 100) ? "--" : QString::asprintf("%.1f m", gpsAccuracy);
  infoGps = (gpsSatelliteCount == 0) ? "🛰️ No Gps Signal" : QString::asprintf("🛰️ Alt(%s) Acc(%s) Sat(%d)",
                                                                              altitudeStr.toStdString().c_str(),
                                                                              accuracyStr.toStdString().c_str(),
                                                                              gpsSatelliteCount
                                                                              );

  x = surface_rect.right() - 20;
  y = (UI_BORDER_SIZE);

  drawTextColor(p, x, y, 30, infoGps, whiteColor(200), "R");

  if (!hideBottomIcons) {
    // lka icon
    x = (btn_size / 2) + (UI_BORDER_SIZE * 1.5) + (btn_size);
    y = surface_rect.bottom() - (UI_FOOTER_HEIGHT / 2);

    QPoint iconCenter(x, y);  // Icon center point
    drawIconGradient(p, iconCenter, steer_img, icon_bg, 0.8, steerAngle);

    QColor sa_color = getColorForAngle(steerAngle);
    QColor sat_color = getColorForAngle(steerAngleTarget);

    QString sa_str = QString::asprintf("R %.1f °", std::abs(steerAngle));
    QString sat_str = QString::asprintf("T %.1f °", std::abs(steerAngleTarget));

    QRect textRect = p.fontMetrics().boundingRect(sa_str);
    int textX = iconCenter.x() - textRect.width() / 2;
    int textY = iconCenter.y() + btn_size / 2 + 20;

    QRect textRect2 = p.fontMetrics().boundingRect(sat_str);
    int textX2 = iconCenter.x() - textRect2.width() / 2;
    int textY2 = iconCenter.y() + btn_size / 2 + 50;

    drawTextColor(p, textX, textY, 30, sa_str, sa_color, "L");
    drawTextColor(p, textX2, textY2, 30, sat_str, sat_color, "L");

    // steer img
    x = (btn_size / 2) + (UI_BORDER_SIZE * 1.5) + (btn_size * 2);

    if (lat_active) {
      drawIcon(p, QPoint(x, y), lka_on_img, icon_bg, lka_state ? 0.8 : 0.2);
    } else {
      drawIcon(p, QPoint(x, y), lka_off_img, icon_bg, lka_state ? 0.8 : 0.2);
    }

    // gaspress icon
    x = surface_rect.right() - (btn_size / 2) - (UI_BORDER_SIZE * 2) - (btn_size * 2);
    drawIcon(p, QPoint(x, y), gaspress_img, icon_bg, gas_press ? 0.8 : 0.2);

    // brake and autohold icon
    x = surface_rect.right() - (btn_size / 2) - (UI_BORDER_SIZE * 2) - (btn_size * 1);
    if (autohold_state >= 1) {
      drawIcon(p, QPoint(x, y), autohold_state > 1 ? autohold_warning_img : autohold_active_img, icon_bg, autohold_state ? 0.8 : 0.2);
    } else {
      drawIcon(p, QPoint(x, y), brake_img, icon_bg, brake_press ? 0.8 : 0.2);
    }

    // distance
    w = 100;
    h = 250;
    x = surface_rect.right() - (UI_BORDER_SIZE * 2) - w * 1.3;
    y = surface_rect.height() - (UI_BORDER_SIZE * 2) - h * 1.8;

    p.setOpacity(0.8);
    if (params.get("LongitudinalPersonality") == "0") {
      p.drawPixmap(x, y, w, h, dist1_img);
    } else if (params.get("LongitudinalPersonality") == "1") {
      p.drawPixmap(x, y, w, h, dist2_img);
    } else if (params.get("LongitudinalPersonality") == "2") {
      p.drawPixmap(x, y, w, h, dist3_img);
    } else {
      p.drawPixmap(x, y, w, h, dist4_img);
    }

    // tpms
    w = 160;
    h = 208;
    x = surface_rect.right() - w - (UI_BORDER_SIZE * 2);
    y = surface_rect.height() - h - (UI_BORDER_SIZE * 2);

    p.drawPixmap(x, y, w, h, tpms_img);
    p.setOpacity(1.0);

    drawTextColor(p, x + 25, y + 56, 30, get_tpms_text(fl), get_tpms_color(fl));
    drawTextColor(p, x + 133, y + 56, 30, get_tpms_text(fr), get_tpms_color(fr));
    drawTextColor(p, x + 25, y + 171, 30, get_tpms_text(rl), get_tpms_color(rl));
    drawTextColor(p, x + 133, y + 171, 30, get_tpms_text(rr), get_tpms_color(rr));
  }

  // bottom left infoDate
  QString infoDate = QString("%1").arg(QDateTime::currentDateTime().toString("yyyy-MM-dd"));

  x = surface_rect.left() + 20;
  y = surface_rect.height() - 20;

  drawTextColor(p, x, y, 30, infoDate, whiteColor(200), "L");

  // bottom left info
  QString steer_info =  QString::asprintf("SteerRatio(%.1f) Torque(%.1f) Curvature(%.3f)",
                                          steer_ratio,
                                          std::abs(steer_torque),
                                          std::abs(curvature)
                                          );

  x = surface_rect.left() + 400;
  y = surface_rect.height() - 20;

  drawTextColor(p, x, y, 30, steer_info, whiteColor(200), "L");

  // bottom right info
  QString current_description = QString("%1").arg(QString::fromStdString(params.get("UpdaterCurrentDescription")));

  x = surface_rect.right() - 20;
  y = surface_rect.height() - 20;

  drawTextColor(p, x, y, 30, current_description, whiteColor(200), "R");

  // turnsignal
  double BLINK_PERIOD_MS = 900.0;

  if (blink_wait > 0) {
    blink_wait--;
    blink_index = 0;
  } else {
    if (left_blinker) {
      draw_blinker(p, surface_rect, true, turnsignal_l_img);
    }

    if (right_blinker) {
      draw_blinker(p, surface_rect, false, turnsignal_r_img);
    }

    if (left_blinker || right_blinker) {
      double now = millis_since_boot();
      if (now - prev_blink_time > BLINK_PERIOD_MS / UI_FREQ) {
        prev_blink_time = now;
        blink_index++;
      }
      if (blink_index >= BLINKER_DRAW_COUNT) {
        blink_index = BLINKER_DRAW_COUNT - 1;
        blink_wait = UI_FREQ / 4;
      }
    } else {
      blink_index = 0;
    }
  }
  p.setOpacity(1.0);
}

void HudRenderer::drawSetSpeed(QPainter &p, const QRect &surface_rect) {
  // max speed, speed limit sign
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
  } else if (stockLimitSpeed > 0) {
    limit_speed = stockLimitSpeed;
    left_dist = stockSpeedLimitDistance;
  } else if (navLimitSpeed > 0) {
    limit_speed = navLimitSpeed;
  }


  QString roadLimitSpeedStr = QString::number(roadLimitSpeed, 'f', 0);
  QString limitSpeedStr = QString::number(limit_speed, 'f', 0);

  if (left_dist >= 1000) {
    leftDistStr = QString::asprintf("%.1f km", left_dist / 1000.0);
  } else if (left_dist > 0) {
    leftDistStr = QString::asprintf("%.0f m", left_dist);
  }

  QRect speed_box(30, 45, 260, 170);

  p.setPen(Qt::NoPen);
  p.setBrush(blackColor(100));
  p.drawRoundedRect(speed_box, 15, 15);

  QColor speedColor = whiteColor(200);
  if (limit_speed > 0 && status != STATUS_DISENGAGED && status != STATUS_OVERRIDE) {
    speedColor = interpColor(
      cruise_speed,
      {limit_speed + 5, limit_speed + 15, limit_speed + 25},
      {whiteColor(200), orangeColor(200), redColor(200)}
    );
  }

  // max speed
  QRect max_speed_box(speed_box.left() + 5, speed_box.top() + 5, 160, 160);
  p.setPen(QPen(whiteColor(200), 2));
  p.drawRoundedRect(max_speed_box, 15, 15);

  int max_text_y = max_speed_box.top() + max_speed_box.height() / 4;
  int max_speed_y = max_speed_box.top() + (max_speed_box.height() / 4 * 3);

  maxSpeedStr = QString::number(std::nearbyint(cruise_speed));

  drawTextColor(p, max_speed_box.center().x(), max_text_y, 30, "MAX", whiteColor(200));
  drawTextColor(p, max_speed_box.center().x(), max_speed_y, 60, is_cruise_set ? maxSpeedStr : "─", speedColor);

  // set speed
  if (nda_state > 0 || stockLimitSpeed > 0) {
    QRect set_speed_box(speed_box.left() + 5, speed_box.bottom() + 5, 160, 160);
    p.setPen(QPen(whiteColor(200), 2));
    p.drawRoundedRect(set_speed_box, 15, 15);

    int set_text_y = set_speed_box.top() + set_speed_box.height() / 4;
    int set_speed_y = set_speed_box.top() + (set_speed_box.height() / 4 * 3);

    applySpeedStr = QString::number(std::nearbyint(apply_speed));

    drawTextColor(p, set_speed_box.center().x(), set_text_y, 30, "SET", whiteColor(200));
    drawTextColor(p, set_speed_box.center().x(), set_speed_y, 60, is_cruise_set ? applySpeedStr : "─", speedColor);
  }

  // traffic box
  QRect traffic_box(max_speed_box.right() + 5, speed_box.top() + 5, 85, 160);
  p.setPen(QPen(whiteColor(200), 2));
  p.drawRoundedRect(traffic_box, 15, 15);

  // speedlimit sign
  if (limit_speed > 0) {
    QPoint center(speed_box.right() + 80, speed_box.center().y());
    const QList<QPair<int, QColor>> circles = {
      {72, whiteColor()},
      {70, redColor()},
      {54, whiteColor()}
    };

    p.setPen(Qt::NoPen);
    for (const auto& [radius, color] : circles) {
      p.setBrush(color);
      p.drawEllipse(center, radius, radius);
    }

    if (limit_speed > 0 && left_dist > 0) {
      drawTextCenter(p, center, 50, limitSpeedStr, blackColor(200));
      drawTextCenter(p, {speed_box.right() + 80, speed_box.bottom()}, 40, leftDistStr, whiteColor(200));
    } else if (roadLimitSpeed > 0 && roadLimitSpeed < 120) {
      drawTextCenter(p, center, 50, roadLimitSpeedStr, blackColor(200));
    } else if (limit_speed > 0) {
      drawTextCenter(p, center, 50, limitSpeedStr, blackColor(200));
    }
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

  drawTextColor(p, surface_rect.center().x(), 200, 180, speedStr, variableColor);
  drawTextColor(p, surface_rect.center().x(), 250, 40, is_metric ? tr("km/h") : tr("mph"), lightorangeColor());
}

void HudRenderer::drawText(QPainter &p, int x, int y, const QString &text, int alpha) {
  QRect real_rect = p.fontMetrics().boundingRect(text);
  real_rect.moveCenter({x, y - real_rect.height() / 2});
  p.setPen(QColor(0xff, 0xff, 0xff, alpha));
  p.drawText(real_rect.x(), real_rect.bottom(), text);
}

void HudRenderer::drawTextColor(QPainter &p, int x, int y, int fontSize, const QString &text, const QColor &color, const QString &alignment) {
  p.setOpacity(1.0);

  QFont font = InterFont(fontSize);
  font.setBold(true);
  p.setFont(font);

  QRect real_rect = p.fontMetrics().boundingRect(text);
  real_rect.moveTop(y - real_rect.height() / 2);
  p.setPen(color);

  if (alignment == "L") {
    real_rect.moveLeft(x);
    p.drawText(real_rect, text);
  } else if (alignment == "R") {
    real_rect.moveRight(x);
    p.drawText(real_rect, text);
  } else {
    real_rect.moveCenter({x, y - real_rect.height() / 2});
    p.drawText(real_rect.x(), real_rect.bottom(), text);
  }

  font.setBold(false);
  p.setFont(InterFont(fontSize));
}

void HudRenderer::drawTextCenter(QPainter &p, const QPoint &center, int fontSize, const QString &text, const QColor &color) {
  QFont font = InterFont(fontSize);
  font.setBold(true);
  p.setFont(font);

  QFontMetrics fm(p.font());
  QRect init_rect = fm.boundingRect(text);
  QRect rect = fm.boundingRect(init_rect, Qt::AlignCenter, text);
  rect.moveCenter(center);
  p.setPen(color);
  p.drawText(rect, Qt::AlignCenter, text);

  font.setBold(false);
  p.setFont(InterFont(fontSize));
}

QColor HudRenderer::interpColor(float xv, std::vector<float> xp, std::vector<QColor> fp) {
  assert(xp.size() == fp.size());

  int N = xp.size();
  int hi = 0;

  while (hi < N and xv > xp[hi]) hi++;
  int low = hi - 1;

  if (hi == N && xv > xp[low]) {
    return fp[fp.size() - 1];
  } else if (hi == 0){
    return fp[0];
  } else {
    return QColor(
      (xv - xp[low]) * (fp[hi].red() - fp[low].red()) / (xp[hi] - xp[low]) + fp[low].red(),
      (xv - xp[low]) * (fp[hi].green() - fp[low].green()) / (xp[hi] - xp[low]) + fp[low].green(),
      (xv - xp[low]) * (fp[hi].blue() - fp[low].blue()) / (xp[hi] - xp[low]) + fp[low].blue(),
      (xv - xp[low]) * (fp[hi].alpha() - fp[low].alpha()) / (xp[hi] - xp[low]) + fp[low].alpha());
  }
}

void HudRenderer::draw_blinker(QPainter& p, const QRect& surface_rect, bool is_left, const QPixmap& blinker_img) {
  float BLINKER_IMG_ALPHA = 0.8f;
  int BLINKER_WIDTH = 200;
  int BLINKER_HEIGHT = 200;

  const int center_x = surface_rect.width() / 2;
  const int y = (surface_rect.height() - BLINKER_HEIGHT) / 2;
  int x = center_x;
  int direction = is_left ? -1 : 1;

  if (is_left) {
    x -= BLINKER_WIDTH;
  }

  for (int i = 0; i < BLINKER_DRAW_COUNT; ++i) {
    float alpha = BLINKER_IMG_ALPHA;
    int distance = std::abs(blink_index - i);
    if (distance > 0) {
      alpha /= (distance * 2);
    }
    p.setOpacity(alpha);
    p.drawPixmap(x, y, BLINKER_WIDTH, BLINKER_HEIGHT, blinker_img);
    x += direction * BLINKER_WIDTH * 0.6;
  }
}
