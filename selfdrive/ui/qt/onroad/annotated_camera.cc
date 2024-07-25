
#include "selfdrive/ui/qt/onroad/annotated_camera.h"

#include <QPainter>
#include <algorithm>
#include <cmath>

#include "common/swaglog.h"
#include "selfdrive/ui/qt/onroad/buttons.h"
#include "selfdrive/ui/qt/util.h"

#include <memory>
#include <sstream>

#include <QDebug>
#include <QMouseEvent>
#include <QString>

// Window that shows camera view and variety of info drawn on top
AnnotatedCameraWidget::AnnotatedCameraWidget(VisionStreamType type, QWidget* parent) : fps_filter(UI_FREQ, 3, 1. / UI_FREQ), CameraWidget("camerad", type, true, parent) {
  pm = std::make_unique<PubMaster, const std::initializer_list<const char *>>({"uiDebug"});

  main_layout = new QVBoxLayout(this);
  main_layout->setMargin(UI_BORDER_SIZE * 4);
  main_layout->setSpacing(20);

  experimental_btn = new ExperimentalButton(this);
  main_layout->addWidget(experimental_btn, 0, Qt::AlignTop | Qt::AlignRight);

  steer_img = loadPixmap("../assets/img_chffr_wheel.png", {img_size, img_size});
  gaspress_img = loadPixmap("../assets/offroad/icon_disengage_on_accelerator.svg", {img_size, img_size});
  dm_img = loadPixmap("../assets/img_driver_face.png", {img_size + 5, img_size + 5});

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

void AnnotatedCameraWidget::updateState(const UIState &s) {
  const int SET_SPEED_NA = 255;
  const SubMaster &sm = *(s.sm);
  const auto cs = sm["controlsState"].getControlsState();
  const auto ce = sm["carState"].getCarState();
  const auto cp = sm["carParams"].getCarParams();
  const auto ds = sm["deviceState"].getDeviceState();
  const auto ge = sm["gpsLocationExternal"].getGpsLocationExternal();
  const auto nd = sm["naviData"].getNaviData();
  const auto lo = sm["longitudinalPlan"].getLongitudinalPlan();

  const bool cs_alive = sm.alive("controlsState");

  // Handle older routes where vCruiseCluster is not set
  apply_speed = cs.getVCruise();
  cruise_speed = cs.getVCruiseCluster() == 0.0 ? cs.getVCruise() : cs.getVCruiseCluster();
  is_cruise_set = cruise_speed > 0 && (int)cruise_speed != SET_SPEED_NA && ce.getCruiseState().getSpeed();
  is_metric = s.scene.is_metric;

  if (is_cruise_set && !is_metric) {
    apply_speed *= KM_TO_MILE;
    cruise_speed *= KM_TO_MILE;
  }

  // Handle older routes where vEgoCluster is not set
  v_ego_cluster_seen = v_ego_cluster_seen || ce.getVEgoCluster() != 0.0;
  float v_ego = v_ego_cluster_seen ? ce.getVEgoCluster() : ce.getVEgo();
  speed = cs_alive ? std::max<float>(0.0, v_ego) : 0.0;
  speed *= is_metric ? MS_TO_KPH : MS_TO_MPH;

  speedUnit = is_metric ? tr("km/h") : tr("mph");
  hideBottomIcons = (cs.getAlertSize() != cereal::ControlsState::AlertSize::NONE);
  status = s.status;

  accel = ce.getAEgo();
  steeringPressed = ce.getSteeringPressed();
  isStandstill = ce.getStandstill();
  brake_state = ce.getBrakeLights();
  autohold_state = ce.getExState().getAutoHold();
  gas_pressed = ce.getGasPressed();
  left_blindspot = ce.getLeftBlindspot();
  right_blindspot = ce.getRightBlindspot();
  wifi_state = (int)ds.getNetworkStrength();
  gps_state = sm["liveLocationKalman"].getLiveLocationKalman().getGpsOK();
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
  left_on = ce.getLeftBlinker();
  right_on = ce.getRightBlinker();
  traffic_state = lo.getTrafficState();

  // update engageability/experimental mode button
  experimental_btn->updateState(s);

  // update DM icon
  auto dm_state = sm["driverMonitoringState"].getDriverMonitoringState();
  dmActive = dm_state.getIsActiveMode();
  rightHandDM = dm_state.getIsRHD();
  // DM icon transition
  dm_fade_state = std::clamp(dm_fade_state+0.2*(0.5-dmActive), 0.0, 1.0);
}

void AnnotatedCameraWidget::drawHud(QPainter &p) {
  p.save();

  // Header gradient
  QLinearGradient bg(0, UI_HEADER_HEIGHT - (UI_HEADER_HEIGHT / 2.5), 0, UI_HEADER_HEIGHT);
  bg.setColorAt(0, QColor::fromRgbF(0, 0, 0, 0.45));
  bg.setColorAt(1, QColor::fromRgbF(0, 0, 0, 0));
  p.fillRect(0, 0, width(), UI_HEADER_HEIGHT, bg);

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

  // current speed (upper center)
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
  drawTextColor(p, rect().center().x(), (UI_BORDER_SIZE * 24), speedStr, variableColor);
  p.setFont(InterFont(66));
  drawTextColor(p, rect().center().x(), (UI_BORDER_SIZE * 31), speedUnit, lightorangeColor());

  p.restore();

  int x,y,w,h = 0;
  QColor icon_bg = blackColor(100);

  // nda icon (upper center)
  if (nda_state > 0) {
    w = 120;
    h = 54;
    x = (width() + (UI_BORDER_SIZE * 2)) / 2 - (w / 2) - UI_BORDER_SIZE;
    y = UI_BORDER_SIZE * 4;
    p.drawPixmap(x, y, w, h, nda_state == 1 ? nda_img : hda_img);
  }

  // sign icon (upper right 3)
  if (traffic_state >= 0) {
    x = rect().right() - (btn_size / 2) - (UI_BORDER_SIZE * 2) - (btn_size * 2.1);
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
  x = rect().right() - (btn_size / 2) - (UI_BORDER_SIZE * 2) - (btn_size * 1.1);
  y = (btn_size / 2) + (UI_BORDER_SIZE * 20);
  drawIconRotate(p, QPoint(x, y), direction_img, icon_bg, gps_state ? 0.8 : 0.2, gpsBearing);

  // gps icon (upper right 3)
  x = rect().right() - (btn_size / 2) - (UI_BORDER_SIZE * 2) - (btn_size * 0.1);
  y = (btn_size / 2) + (UI_BORDER_SIZE * 20);
  drawIcon(p, QPoint(x, y), gps_img, icon_bg, gps_state ? 0.8 : 0.2);

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
  x = rect().right() - (btn_size / 2) - (UI_BORDER_SIZE * 2) - (btn_size * 1.1);
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

  x = rect().right() - 20;
  y = (UI_BORDER_SIZE * 2);

  p.setFont(InterFont(30));
  drawTextColorRight(p, x, y, infoGps, whiteColor(200));

  if (!hideBottomIcons) {
    // steer img (bottom 1 right)
    x = (btn_size / 2) + (UI_BORDER_SIZE * 2) + (btn_size);
    y = rect().bottom() - (UI_FOOTER_HEIGHT / 2);
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
    x = rect().right() - (btn_size / 2) - (UI_BORDER_SIZE * 2) - (btn_size * 2.1);
    drawIcon(p, QPoint(x, y), gaspress_img, icon_bg, gas_pressed ? 0.8 : 0.2);

    // brake and autohold icon (bottom right 3)
    x = rect().right() - (btn_size / 2) - (UI_BORDER_SIZE * 2) - (btn_size * 1.1);
    if (autohold_state >= 1) {
      drawIcon(p, QPoint(x, y), autohold_state > 1 ? autohold_warning_img : autohold_active_img, icon_bg, autohold_state ? 0.8 : 0.2);
    } else {
      drawIcon(p, QPoint(x, y), brake_img, icon_bg, brake_state ? 0.8 : 0.2);
    }

    // tpms (bottom right)
    w = 160;
    h = 208;
    x = rect().right() - w - (UI_BORDER_SIZE * 2);
    y = height() - h - (UI_BORDER_SIZE * 2);

    p.drawPixmap(x, y, w, h, tpms_img);

    p.setFont(InterFont(30, QFont::Bold));
    drawTextColor(p, x + 25, y + 56, get_tpms_text(fl), get_tpms_color(fl));
    drawTextColor(p, x + 133, y + 56, get_tpms_text(fr), get_tpms_color(fr));
    drawTextColor(p, x + 25, y + 171, get_tpms_text(rl), get_tpms_color(rl));
    drawTextColor(p, x + 133, y + 171, get_tpms_text(rr), get_tpms_color(rr));
  }

  // bottom left info
  QString infoText = QString("[%1]").arg(QString::fromStdString(params.get("CarName")));

  x = rect().left() + 20;
  y = rect().height() - 20;

  p.setFont(InterFont(30));
  drawTextColorLeft(p, x, y, infoText, whiteColor(200));

  // turnsignal
  static int blink_index = 0;
  static int blink_wait = 0;
  static double prev_ts = 0.0;

  if (blink_wait > 0) {
    blink_wait--;
    blink_index = 0;
  } else {
    const float img_alpha = 0.8f;
    const int center_x = width() / 2;
    const int draw_count = 8;
    w = 200;
    h = 200;
    y = (height() - h) / 2;

    x = center_x;
    if (left_on) {
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
    if (right_on) {
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

    if (left_on || right_on) {
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

// Window that shows camera view and variety of
// info drawn on top

void AnnotatedCameraWidget::drawText(QPainter &p, int x, int y, const QString &text, int alpha) {
  p.setOpacity(1.0);
  QRect real_rect = p.fontMetrics().boundingRect(text);
  real_rect.moveCenter({x, y - real_rect.height() / 2});
  p.setPen(QColor(0xff, 0xff, 0xff, alpha));
  p.drawText(real_rect.x(), real_rect.bottom(), text);
}

void AnnotatedCameraWidget::drawTextColor(QPainter &p, int x, int y, const QString &text, const QColor &color) {
  p.setOpacity(1.0);
  QRect real_rect = p.fontMetrics().boundingRect(text);
  real_rect.moveCenter({x, y - real_rect.height() / 2});
  p.setPen(color);
  p.drawText(real_rect.x(), real_rect.bottom(), text);
}

void AnnotatedCameraWidget::drawTextColorLeft(QPainter &p, int x, int y, const QString &text, const QColor &color) {
    p.setOpacity(1.0);
    QRect real_rect = p.fontMetrics().boundingRect(text);
    real_rect.moveLeft(x);
    real_rect.moveTop(y - real_rect.height() / 2);
    p.setPen(color);
    p.drawText(real_rect, text);
}

void AnnotatedCameraWidget::drawTextColorRight(QPainter &p, int x, int y, const QString &text, const QColor &color) {
    p.setOpacity(1.0);
    QRect real_rect = p.fontMetrics().boundingRect(text);
    real_rect.moveRight(x);
    real_rect.moveTop(y - real_rect.height() / 2);
    p.setPen(color);
    p.drawText(real_rect, text);
}

void AnnotatedCameraWidget::initializeGL() {
  CameraWidget::initializeGL();
  qInfo() << "OpenGL version:" << QString((const char*)glGetString(GL_VERSION));
  qInfo() << "OpenGL vendor:" << QString((const char*)glGetString(GL_VENDOR));
  qInfo() << "OpenGL renderer:" << QString((const char*)glGetString(GL_RENDERER));
  qInfo() << "OpenGL language version:" << QString((const char*)glGetString(GL_SHADING_LANGUAGE_VERSION));

  prev_draw_t = millis_since_boot();
  setBackgroundColor(bg_colors[STATUS_DISENGAGED]);
}

void AnnotatedCameraWidget::updateFrameMat() {
  CameraWidget::updateFrameMat();
  UIState *s = uiState();
  int w = width(), h = height();

  s->fb_w = w;
  s->fb_h = h;

  // Apply transformation such that video pixel coordinates match video
  // 1) Put (0, 0) in the middle of the video
  // 2) Apply same scaling as video
  // 3) Put (0, 0) in top left corner of video
  s->car_space_transform.reset();
  s->car_space_transform.translate(w / 2 - x_offset, h / 2 - y_offset)
      .scale(zoom, zoom)
      .translate(-intrinsic_matrix.v[2], -intrinsic_matrix.v[5]);
}

void AnnotatedCameraWidget::drawLaneLines(QPainter &painter, const UIState *s) {
  painter.save();

  const UIScene &scene = s->scene;
  SubMaster &sm = *(s->sm);

  // lanelines
  for (int i = 0; i < std::size(scene.lane_line_vertices); ++i) {
    painter.setBrush(QColor::fromRgbF(1.0, 1.0, 1.0, std::clamp<float>(scene.lane_line_probs[i], 0.0, 0.7)));
    painter.drawPolygon(scene.lane_line_vertices[i]);
  }

  // TODO: Fix empty spaces when curiving back on itself
  painter.setBrush(QColor::fromRgbF(1.0, 0.0, 0.0, 0.2));
  if (left_blindspot) painter.drawPolygon(scene.lane_barrier_vertices[0]);
  if (right_blindspot) painter.drawPolygon(scene.lane_barrier_vertices[1]);

  // road edges
  for (int i = 0; i < std::size(scene.road_edge_vertices); ++i) {
    painter.setBrush(QColor::fromRgbF(1.0, 0, 0, std::clamp<float>(1.0 - scene.road_edge_stds[i], 0.0, 1.0)));
    painter.drawPolygon(scene.road_edge_vertices[i]);
  }

  // paint path
  QLinearGradient bg(0, height(), 0, 0);
  //if (sm["controlsState"].getControlsState().getExperimentalMode()) {
  if (scene.engaged) {
    if (scene.steeringPressed) {
      // The user is applying torque to the steering wheel
      bg.setColorAt(0.0, steeringpressedColor(100));
      bg.setColorAt(0.5, steeringpressedColor(50));
      bg.setColorAt(1.0, steeringpressedColor(0));
    } else {
      // The first half of track_vertices are the points for the right side of the path
      const auto &acceleration = sm["modelV2"].getModelV2().getAcceleration().getX();
      const int max_len = std::min<int>(scene.track_vertices.length() / 2, acceleration.size());

      for (int i = 0; i < max_len; ++i) {
        // Some points are out of frame
        int track_idx = (scene.track_vertices.length() / 2) - i;  // flip idx to start from top
        if (scene.track_vertices[track_idx].y() < 0 || scene.track_vertices[track_idx].y() > height()) continue;

        // Flip so 0 is bottom of frame
        float lin_grad_point = (height() - scene.track_vertices[track_idx].y()) / height();

        // speed up: 120, slow down: 0
        float path_hue = fmax(fmin(60 + acceleration[i] * 35, 120), 0);
        // FIXME: painter.drawPolygon can be slow if hue is not rounded
        path_hue = int(path_hue * 100 + 0.5) / 100;

        float saturation = fmin(fabs(acceleration[i] * 1.5), 1);
        float lightness = util::map_val(saturation, 0.0f, 1.0f, 0.95f, 0.62f);  // lighter when grey
        float alpha = util::map_val(lin_grad_point, 0.75f / 2.f, 0.75f, 0.4f, 0.0f);  // matches previous alpha fade
        bg.setColorAt(lin_grad_point, QColor::fromHslF(path_hue / 360., saturation, lightness, alpha));

        // Skip a point, unless next is last
        i += (i + 2) < max_len ? 1 : 0;
      }
    }
  } else {
    //bg.setColorAt(0.0, QColor::fromHslF(148 / 360., 0.94, 0.51, 0.4));
    //bg.setColorAt(0.5, QColor::fromHslF(112 / 360., 1.0, 0.68, 0.35));
    //bg.setColorAt(1.0, QColor::fromHslF(112 / 360., 1.0, 0.68, 0.0));
    bg.setColorAt(0.0, whiteColor(100));
    bg.setColorAt(0.5, whiteColor(50));
    bg.setColorAt(1.0, whiteColor(0));
  }
  painter.setBrush(bg);
  painter.drawPolygon(scene.track_vertices);

  painter.restore();
}

void AnnotatedCameraWidget::drawDriverState(QPainter &painter, const UIState *s) {
  const UIScene &scene = s->scene;

  painter.save();

  // base icon
  int offset = (UI_BORDER_SIZE * 3) + (btn_size / 2);
  int x = rightHandDM ? width() - offset : offset;
  int y = height() - offset - (UI_BORDER_SIZE * 3);
  float opacity = dmActive ? 0.8 : 0.2;
  bool dm_missing = params.getBool("DriverCameraHardwareMissing");
  if (!dm_missing) {
    drawIcon(painter, QPoint(x, y), dm_img, blackColor(100), opacity);
  }

  // face
  QPointF face_kpts_draw[std::size(default_face_kpts_3d)];
  float kp;
  for (int i = 0; i < std::size(default_face_kpts_3d); ++i) {
    kp = (scene.face_kpts_draw[i].v[2] - 8) / 120 + 1.0;
    face_kpts_draw[i] = QPointF(scene.face_kpts_draw[i].v[0] * kp + x, scene.face_kpts_draw[i].v[1] * kp + y);
  }

  painter.setPen(QPen(QColor::fromRgbF(1.0, 1.0, 1.0, opacity), 5.2, Qt::SolidLine, Qt::RoundCap));
  painter.drawPolyline(face_kpts_draw, std::size(default_face_kpts_3d));

  // tracking arcs
  const int arc_l = 133;
  const float arc_t_default = 6.7;
  const float arc_t_extend = 12.0;
  QColor arc_color = QColor::fromRgbF(0.545 - 0.445 * s->engaged(),
                                      0.545 + 0.4 * s->engaged(),
                                      0.545 - 0.285 * s->engaged(),
                                      0.4 * (1.0 - dm_fade_state));
  float delta_x = -scene.driver_pose_sins[1] * arc_l / 2;
  float delta_y = -scene.driver_pose_sins[0] * arc_l / 2;
  painter.setPen(QPen(arc_color, arc_t_default+arc_t_extend*fmin(1.0, scene.driver_pose_diff[1] * 5.0), Qt::SolidLine, Qt::RoundCap));
  painter.drawArc(QRectF(std::fmin(x + delta_x, x), y - arc_l / 2, fabs(delta_x), arc_l), (scene.driver_pose_sins[1]>0 ? 90 : -90) * 16, 180 * 16);
  painter.setPen(QPen(arc_color, arc_t_default+arc_t_extend*fmin(1.0, scene.driver_pose_diff[0] * 5.0), Qt::SolidLine, Qt::RoundCap));
  painter.drawArc(QRectF(x - arc_l / 2, std::fmin(y + delta_y, y), arc_l, fabs(delta_y)), (scene.driver_pose_sins[0]>0 ? 0 : 180) * 16, 180 * 16);

  painter.restore();
}

void AnnotatedCameraWidget::drawLead(QPainter &painter, const cereal::RadarState::LeadData::Reader &lead_data, const QPointF &vd) {
  painter.save();
  const float speedBuff = 10.;
  const float leadBuff = 40.;
  const float d_rel = lead_data.getDRel();
  const float v_rel = lead_data.getVRel();

  float fillAlpha = 0;
  if (d_rel < leadBuff) {
    fillAlpha = 255 * (1.0 - (d_rel / leadBuff));
    if (v_rel < 0) {
      fillAlpha += 255 * (-1 * (v_rel / speedBuff));
    }
    fillAlpha = (int)(fmin(fillAlpha, 255));
  }

  float sz = std::clamp((25 * 30) / (d_rel / 3 + 30), 15.0f, 30.0f) * 2.35;
  float x = std::clamp((float)vd.x(), 0.f, width() - sz / 2);
  float y = std::fmin(height() - sz * .6, (float)vd.y());

  float g_xo = sz / 5;
  float g_yo = sz / 10;

  QPointF glow[] = {{x + (sz * 1.35) + g_xo, y + sz + g_yo}, {x, y - g_yo}, {x - (sz * 1.35) - g_xo, y + sz + g_yo}};
  painter.setBrush(pinkColor());
  painter.drawPolygon(glow, std::size(glow));

  // chevron
  QPointF chevron[] = {{x + (sz * 1.25), y + sz}, {x, y}, {x - (sz * 1.25), y + sz}};
  painter.setBrush(redColor(fillAlpha));
  painter.drawPolygon(chevron, std::size(chevron));

  // lead car radar distance and speed
  QString l_dist, l_speed;
  QColor d_color, v_color = whiteColor(150);

  if (d_rel < 5) {
    d_color = redColor(150);
  } else if (d_rel < 15) {
    d_color = orangeColor(150);
  } else {
    d_color = whiteColor(150);
  }
  l_dist = QString::asprintf("%.1f m", d_rel);

  if (v_rel < -4.4704) {
    v_color = redColor(150);
  } else if (v_rel < 0) {
    v_color = orangeColor(150);
  } else {
    v_color = pinkColor(150);
  }
  if (speedUnit == "mph") {
    l_speed = QString::asprintf("%.0f mph", speed + v_rel * 2.236936); // mph
  } else {
    l_speed = QString::asprintf("%.0f km/h", speed + v_rel * 3.6); // kph
  }
  painter.setFont(InterFont(35, QFont::Bold));
  drawTextColor(painter, x, y + sz / 1.5f + 70.0, l_dist, d_color);
  drawTextColor(painter, x, y + sz / 1.5f + 120.0, l_speed, v_color);

  painter.restore();
}

void AnnotatedCameraWidget::paintGL() {
  UIState *s = uiState();
  SubMaster &sm = *(s->sm);
  const double start_draw_t = millis_since_boot();
  const cereal::ModelDataV2::Reader &model = sm["modelV2"].getModelV2();

  // draw camera frame
  {
    std::lock_guard lk(frame_lock);

    if (frames.empty()) {
      if (skip_frame_count > 0) {
        skip_frame_count--;
        qDebug() << "skipping frame, not ready";
        return;
      }
    } else {
      // skip drawing up to this many frames if we're
      // missing camera frames. this smooths out the
      // transitions from the narrow and wide cameras
      skip_frame_count = 5;
    }

    // Wide or narrow cam dependent on speed
    bool has_wide_cam = available_streams.count(VISION_STREAM_WIDE_ROAD);
    if (has_wide_cam) {
      float v_ego = sm["carState"].getCarState().getVEgo();
      if ((v_ego < 10) || available_streams.size() == 1) {
        wide_cam_requested = true;
      } else if (v_ego > 15) {
        wide_cam_requested = false;
      }
      //wide_cam_requested = wide_cam_requested && sm["controlsState"].getControlsState().getExperimentalMode();
      // for replay of old routes, never go to widecam
      //wide_cam_requested = wide_cam_requested && s->scene.calibration_wide_valid;
    }

    if(s->scene.show_driver_camera) {
      CameraWidget::setStreamType(VISION_STREAM_DRIVER);
    } else {
      CameraWidget::setStreamType(wide_cam_requested ? VISION_STREAM_WIDE_ROAD : VISION_STREAM_ROAD);
    }

    s->scene.wide_cam = CameraWidget::getStreamType() == VISION_STREAM_WIDE_ROAD;
    if (s->scene.calibration_valid) {
      auto calib = s->scene.wide_cam ? s->scene.view_from_wide_calib : s->scene.view_from_calib;
      CameraWidget::updateCalibration(calib);
    } else {
      CameraWidget::updateCalibration(DEFAULT_CALIBRATION);
    }
    CameraWidget::setFrameId(model.getFrameId());
    CameraWidget::paintGL();
  }

  QPainter painter(this);
  painter.setRenderHint(QPainter::Antialiasing);
  painter.setPen(Qt::NoPen);

  if (s->scene.world_objects_visible && !s->scene.show_driver_camera) {
    update_model(s, model);
    drawLaneLines(painter, s);

    //if (s->scene.longitudinal_control && sm.rcv_frame("radarState") > s->scene.started_frame) {
    if (sm.rcv_frame("radarState") > s->scene.started_frame) {
      auto radar_state = sm["radarState"].getRadarState();
      update_leads(s, radar_state, model.getPosition());
      auto lead_one = radar_state.getLeadOne();
      auto lead_two = radar_state.getLeadTwo();
      if (lead_one.getStatus()) {
        drawLead(painter, lead_one, s->scene.lead_vertices[0]);
      }
      if (lead_two.getStatus() && (std::abs(lead_one.getDRel() - lead_two.getDRel()) > 3.0)) {
        drawLead(painter, lead_two, s->scene.lead_vertices[1]);
      }
    }
  }

  // DMoji
  if (!hideBottomIcons && (sm.rcv_frame("driverStateV2") > s->scene.started_frame)) {
    update_dmonitoring(s, sm["driverStateV2"].getDriverStateV2(), dm_fade_state, rightHandDM);
    drawDriverState(painter, s);
  }

  drawHud(painter);

  double cur_draw_t = millis_since_boot();
  double dt = cur_draw_t - prev_draw_t;
  double fps = fps_filter.update(1. / dt * 1000);
  if (fps < 15) {
    LOGW("slow frame rate: %.2f fps", fps);
  }
  prev_draw_t = cur_draw_t;

  // publish debug msg
  MessageBuilder msg;
  auto m = msg.initEvent().initUiDebug();
  m.setDrawTimeMillis(cur_draw_t - start_draw_t);
  pm->send("uiDebug", msg);
}

void AnnotatedCameraWidget::showEvent(QShowEvent *event) {
  CameraWidget::showEvent(event);

  ui_update_params(uiState());
  prev_draw_t = millis_since_boot();
}
