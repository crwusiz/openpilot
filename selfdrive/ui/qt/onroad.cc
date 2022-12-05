#include "selfdrive/ui/qt/onroad.h"
#include "selfdrive/ui/qt/onroad.h"

#include <cmath>

#include <QDebug>
#include <QString>

#include "common/timing.h"
#include "selfdrive/ui/qt/util.h"
#ifdef ENABLE_MAPS
#include "selfdrive/ui/qt/maps/map.h"
#include "selfdrive/ui/qt/maps/map_helpers.h"
#endif

OnroadWindow::OnroadWindow(QWidget *parent) : QWidget(parent) {
  QVBoxLayout *main_layout  = new QVBoxLayout(this);
  main_layout->setMargin(bdr_s);
  QStackedLayout *stacked_layout = new QStackedLayout;
  stacked_layout->setStackingMode(QStackedLayout::StackAll);
  main_layout->addLayout(stacked_layout);

  nvg = new AnnotatedCameraWidget(VISION_STREAM_ROAD, this);

  QWidget * split_wrapper = new QWidget;
  split = new QHBoxLayout(split_wrapper);
  split->setContentsMargins(0, 0, 0, 0);
  split->setSpacing(0);
  split->addWidget(nvg);

  if (getenv("DUAL_CAMERA_VIEW")) {
    CameraWidget *arCam = new CameraWidget("camerad", VISION_STREAM_ROAD, true, this);
    split->insertWidget(0, arCam);
  }

  stacked_layout->addWidget(split_wrapper);

  alerts = new OnroadAlerts(this);
  alerts->setAttribute(Qt::WA_TransparentForMouseEvents, true);
  stacked_layout->addWidget(alerts);

  // setup stacking order
  alerts->raise();

  setAttribute(Qt::WA_OpaquePaintEvent);
  QObject::connect(uiState(), &UIState::uiUpdate, this, &OnroadWindow::updateState);
  QObject::connect(uiState(), &UIState::offroadTransition, this, &OnroadWindow::offroadTransition);
}

void OnroadWindow::updateState(const UIState &s) {
  QColor bgColor = bg_colors[s.status];
  Alert alert = Alert::get(*(s.sm), s.scene.started_frame);
  if (s.sm->updated("controlsState") || !alert.equal({})) {
    if (alert.type == "controlsUnresponsive") {
      bgColor = bg_colors[STATUS_ALERT];
    } else if (alert.type == "controlsUnresponsivePermanent") {
      bgColor = bg_colors[STATUS_DISENGAGED];
    }
    alerts->updateAlert(alert, bgColor);
  }

  if (s.scene.map_on_left) {
    split->setDirection(QBoxLayout::LeftToRight);
  } else {
    split->setDirection(QBoxLayout::RightToLeft);
  }

  nvg->updateState(s);

  if (bg != bgColor) {
    // repaint border
    bg = bgColor;
    update();
  }
}

void OnroadWindow::mousePressEvent(QMouseEvent* e) {
  if (map != nullptr) {
    bool sidebarVisible = geometry().x() > 0;
    map->setVisible(!sidebarVisible && !map->isVisible());
  }
  // propagation event to parent(HomeWindow)
  QWidget::mousePressEvent(e);
}

void OnroadWindow::offroadTransition(bool offroad) {
#ifdef ENABLE_MAPS
  if (!offroad) {
    if (map == nullptr && (uiState()->prime_type || !MAPBOX_TOKEN.isEmpty())) {
      MapWindow * m = new MapWindow(get_mapbox_settings());
      map = m;

      QObject::connect(uiState(), &UIState::offroadTransition, m, &MapWindow::offroadTransition);

      m->setFixedWidth(topWidget(this)->width() / 2);
      split->insertWidget(0, m);

      // Make map visible after adding to split
      m->offroadTransition(offroad);
    }
  }
#endif

  alerts->updateAlert({}, bg);
}

void OnroadWindow::paintEvent(QPaintEvent *event) {
  QPainter p(this);
  p.fillRect(rect(), QColor(bg.red(), bg.green(), bg.blue(), 255));
}

// ***** onroad widgets *****

// OnroadAlerts
void OnroadAlerts::updateAlert(const Alert &a, const QColor &color) {
  if (!alert.equal(a) || color != bg) {
    alert = a;
    bg = color;
    update();
  }
}

void OnroadAlerts::paintEvent(QPaintEvent *event) {
  if (alert.size == cereal::ControlsState::AlertSize::NONE) {
    return;
  }
  static std::map<cereal::ControlsState::AlertSize, const int> alert_sizes = {
    {cereal::ControlsState::AlertSize::SMALL, 271},
    {cereal::ControlsState::AlertSize::MID, 420},
    {cereal::ControlsState::AlertSize::FULL, height()},
  };
  int h = alert_sizes[alert.size];
  QRect r = QRect(0, height() - h, width(), h);

  QPainter p(this);

  // draw background + gradient
  p.setPen(Qt::NoPen);
  p.setCompositionMode(QPainter::CompositionMode_SourceOver);

  p.setBrush(QBrush(bg));
  p.drawRect(r);

  QLinearGradient g(0, r.y(), 0, r.bottom());
  g.setColorAt(0, QColor::fromRgbF(0, 0, 0, 0.05));
  g.setColorAt(1, QColor::fromRgbF(0, 0, 0, 0.35));

  p.setCompositionMode(QPainter::CompositionMode_DestinationOver);
  p.setBrush(QBrush(g));
  p.fillRect(r, g);
  p.setCompositionMode(QPainter::CompositionMode_SourceOver);

  // text
  const QPoint c = r.center();
  p.setPen(QColor(0xff, 0xff, 0xff));
  p.setRenderHint(QPainter::TextAntialiasing);
  if (alert.size == cereal::ControlsState::AlertSize::SMALL) {
    configFont(p, "Open Sans", 74, "SemiBold");
    p.drawText(r, Qt::AlignCenter, alert.text1);
  } else if (alert.size == cereal::ControlsState::AlertSize::MID) {
    configFont(p, "Open Sans", 88, "Bold");
    p.drawText(QRect(0, c.y() - 125, width(), 150), Qt::AlignHCenter | Qt::AlignTop, alert.text1);
    configFont(p, "Open Sans", 66, "Regular");
    p.drawText(QRect(0, c.y() + 21, width(), 90), Qt::AlignHCenter, alert.text2);
  } else if (alert.size == cereal::ControlsState::AlertSize::FULL) {
    bool l = alert.text1.length() > 15;
    configFont(p, "Open Sans", l ? 132 : 177, "Bold");
    p.drawText(QRect(0, r.y() + (l ? 240 : 270), width(), 600), Qt::AlignHCenter | Qt::TextWordWrap, alert.text1);
    configFont(p, "Open Sans", 88, "Regular");
    p.drawText(QRect(0, r.height() - (l ? 361 : 420), width(), 300), Qt::AlignHCenter | Qt::TextWordWrap, alert.text2);
  }
}


AnnotatedCameraWidget::AnnotatedCameraWidget(VisionStreamType type, QWidget* parent) : fps_filter(UI_FREQ, 3, 1. / UI_FREQ), CameraWidget("camerad", type, true, parent) {
  pm = std::make_unique<PubMaster, const std::initializer_list<const char *>>({"uiDebug"});

  steer_img = loadPixmap("../assets/img_chffr_wheel.png", {img_size, img_size});
  lat_img = loadPixmap("../assets/offroad/icon_speed_limit.png", {img_size, img_size});
  longitudinal_img = loadPixmap("../assets/offroad/icon_disengage_on_accelerator.svg", {img_size, img_size});
  dm_img = loadPixmap("../assets/img_driver_face.png", {img_size, img_size});
  experimental_img = loadPixmap("../assets/img_experimental.svg", {img_size, img_size });

  // crwusiz add
  brake_img = loadPixmap("../assets/img_brake_disc.png", {img_size, img_size});
  bsd_l_img = loadPixmap("../assets/img_bsd_l.png", {img_size, img_size});
  bsd_r_img = loadPixmap("../assets/img_bsd_r.png", {img_size, img_size});
  gps_img = loadPixmap("../assets/img_gps.png", {img_size, img_size});
  wifi_l_img = loadPixmap("../assets/offroad/icon_wifi_strength_low.svg", {img_size, img_size});
  wifi_m_img = loadPixmap("../assets/offroad/icon_wifi_strength_medium.svg", {img_size, img_size});
  wifi_h_img = loadPixmap("../assets/offroad/icon_wifi_strength_high.svg", {img_size, img_size});
  wifi_f_img = loadPixmap("../assets/offroad/icon_wifi_strength_full.svg", {img_size, img_size});
  direction_img = loadPixmap("../assets/img_direction.png", {img_size, img_size});
  gap1_img = loadPixmap("../assets/img_gap1.png", {img_size, img_size});
  gap2_img = loadPixmap("../assets/img_gap2.png", {img_size, img_size});
  gap3_img = loadPixmap("../assets/img_gap3.png", {img_size, img_size});
  gap4_img = loadPixmap("../assets/img_gap4.png", {img_size, img_size});
  turnsignal_l_img = loadPixmap("../assets/img_turnsignal_l.png", {img_size, img_size});
  turnsignal_r_img = loadPixmap("../assets/img_turnsignal_r.png", {img_size, img_size});
  tpms_img = loadPixmap("../assets/img_tpms.png");

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
    if (tpms < 5 || tpms > 60)
      return "─";
    char str[32];
    snprintf(str, sizeof(str), "%.0f", round(tpms));
    return QString(str);
}

void AnnotatedCameraWidget::updateState(const UIState &s) {
  const int SET_SPEED_NA = 255;
  const SubMaster &sm = *(s.sm);
  const auto cs = sm["controlsState"].getControlsState();
  const auto cc = sm["carControl"].getCarControl();
  const auto ce = sm["carState"].getCarState();
  const auto cp = sm["carParams"].getCarParams();
  const auto ds = sm["deviceState"].getDeviceState();
  const auto rs = sm["radarState"].getRadarState();
  const auto lp = sm["liveParameters"].getLiveParameters();
  const auto ge = sm["gpsLocationExternal"].getGpsLocationExternal();
  const auto ls = sm["roadLimitSpeed"].getRoadLimitSpeed();
  const auto tp = sm["liveTorqueParameters"].getLiveTorqueParameters();

  const bool cs_alive = sm.alive("controlsState");

  // Handle older routes where vCruiseCluster is not set
  float apply_speed = cs.getVCruise();
  float cruise_speed = cs.getVCruiseCluster() == 0.0 ? cs.getVCruise() : cs.getVCruiseCluster();
  bool cruise_set = cruise_speed > 0 && (int)cruise_speed != SET_SPEED_NA && ce.getCruiseState().getSpeed();

  if (cruise_set && !s.scene.is_metric) {
    apply_speed *= KM_TO_MILE;
    cruise_speed *= KM_TO_MILE;
  }

  // Handle older routes where vEgoCluster is not set
  float v_ego;
  if (ce.getVEgoCluster() == 0.0 && !v_ego_cluster_seen) {
    v_ego = ce.getVEgo();
  } else {
    v_ego = ce.getVEgoCluster();
    v_ego_cluster_seen = true;
  }
  float cur_speed = cs_alive ? std::max<float>(0.0, v_ego) : 0.0;
  cur_speed *= s.scene.is_metric ? MS_TO_KPH : MS_TO_MPH;

  setProperty("is_cruise_set", cruise_set);
  setProperty("speed", cur_speed);
  setProperty("applyMaxSpeed", apply_speed);
  setProperty("cruiseMaxSpeed", cruise_speed);
  setProperty("speedUnit", s.scene.is_metric ? tr("km/h") : tr("mph"));
  setProperty("accel", ce.getAEgo());
  setProperty("status", s.status);
  setProperty("steeringPressed", ce.getSteeringPressed());
  setProperty("dmActive", sm["driverMonitoringState"].getDriverMonitoringState().getIsActiveMode());
  setProperty("brake_state", ce.getBrakeLights());
  setProperty("autohold_state", ce.getAutoHold());
  setProperty("nda_state", ls.getActive());
  setProperty("left_blindspot", ce.getLeftBlindspot());
  setProperty("right_blindspot", ce.getRightBlindspot());
  setProperty("wifi_state", (int)ds.getNetworkStrength());
  setProperty("gps_state", sm["liveLocationKalman"].getLiveLocationKalman().getGpsOK());
  setProperty("gpsBearing", ge.getBearingDeg());
  setProperty("gpsVerticalAccuracy", ge.getVerticalAccuracy());
  setProperty("gpsAltitude", ge.getAltitude());
  setProperty("gpsAccuracy", ge.getAccuracy());
  setProperty("gpsSatelliteCount", s.scene.satelliteCount);
  setProperty("lead_d_rel", rs.getLeadOne().getDRel());
  setProperty("lead_v_rel", rs.getLeadOne().getVRel());
  setProperty("lead_status", rs.getLeadOne().getStatus());
  setProperty("angleSteers", ce.getSteeringAngleDeg());
  setProperty("steerAngleDesired", cc.getActuators().getSteeringAngleDeg());
  setProperty("longControl", cs.getLongControl());
  setProperty("gap_state", ce.getCruiseState().getGapAdjust());
  setProperty("lateralControl", cs.getLateralControlSelect());
  setProperty("steerRatio", lp.getSteerRatio());
  setProperty("epsBus", cp.getEpsBus());
  setProperty("sccBus", cp.getSccBus());
  setProperty("fl", ce.getTpms().getFl());
  setProperty("fr", ce.getTpms().getFr());
  setProperty("rl", ce.getTpms().getRl());
  setProperty("rr", ce.getTpms().getRr());
  setProperty("roadLimitSpeed", ls.getRoadLimitSpeed());
  setProperty("camLimitSpeed", ls.getCamLimitSpeed());
  setProperty("camLimitSpeedLeftDist", ls.getCamLimitSpeedLeftDist());
  setProperty("sectionLimitSpeed", ls.getSectionLimitSpeed());
  setProperty("sectionLeftDist", ls.getSectionLeftDist());
  setProperty("left_on", ce.getLeftBlinker());
  setProperty("right_on", ce.getRightBlinker());
  setProperty("latAccelFactor", cs.getLateralControlState().getTorqueState().getLatAccelFactor());
  setProperty("friction", cs.getLateralControlState().getTorqueState().getFriction());
  setProperty("latAccelFactorRaw", tp.getLatAccelFactorRaw());
  setProperty("frictionRaw", tp.getFrictionCoefficientRaw());
}

void AnnotatedCameraWidget::drawHud(QPainter &p) {
  p.save();

  // Header gradient
  QLinearGradient bg(0, header_h - (header_h / 2.5), 0, header_h);
  bg.setColorAt(0, QColor::fromRgbF(0, 0, 0, 0.45));
  bg.setColorAt(1, QColor::fromRgbF(0, 0, 0, 0));
  p.fillRect(0, 0, width(), header_h, bg);

  // max speed, apply speed, speed limit sign init
  float limit_speed = 0;
  float left_dist = 0;

  if (camLimitSpeed > 0 && camLimitSpeedLeftDist > 0) {
    limit_speed = camLimitSpeed;
    left_dist = camLimitSpeedLeftDist;
  } else if (sectionLimitSpeed > 0 && sectionLeftDist > 0) {
    limit_speed = sectionLimitSpeed;
    left_dist = sectionLeftDist;
  } else {
    limit_speed = roadLimitSpeed;
  }

  QString roadLimitSpeedStr, limitSpeedStr, leftDistStr;
  roadLimitSpeedStr.sprintf("%.0f", roadLimitSpeed);
  limitSpeedStr.sprintf("%.0f", limit_speed);

  if (left_dist >= 1000) {
    leftDistStr.sprintf("%.1fkm", left_dist / 1000.f);
  } else if (left_dist > 0) {
    leftDistStr.sprintf("%.0fm", left_dist);
  }

  int rect_width = !longControl ? 163 : 300;
  int rect_height = 188;

  QRect max_speed_rect(30, 30, rect_width, rect_height);
  p.setPen(Qt::NoPen);
  p.setBrush(blackColor(100));
  p.drawRoundedRect(max_speed_rect, 32, 32);
  //drawRoundedRect(p, max_speed_rect, top_radius, top_radius, bottom_radius, bottom_radius);

  // max speed (upper left 1)
  QString cruiseSpeedStr = QString::number(std::nearbyint(cruiseMaxSpeed));
  QRect max_speed_outer(max_speed_rect.left() + 10, max_speed_rect.top() + 10, 140, 168);
  p.setPen(QPen(whiteColor(200), 2));
  p.drawRoundedRect(max_speed_outer, 16, 16);

  if (limit_speed > 0 && status != STATUS_DISENGAGED && status != STATUS_OVERRIDE) {
    p.setPen(interpColor(
      cruiseMaxSpeed,
      {limit_speed + 5, limit_speed + 15, limit_speed + 25},
      {whiteColor(), orangeColor(), redColor()}
    ));
  } else {
    p.setPen(whiteColor());
  }
  configFont(p, "Open Sans", 65, "Regular");
  QRect speed_rect = getTextRect(p, Qt::AlignCenter, cruiseSpeedStr);
  speed_rect.moveCenter({max_speed_outer.center().x(), 0});
  speed_rect.moveTop(max_speed_rect.top() + 25);
  p.drawText(speed_rect, Qt::AlignCenter, is_cruise_set ? cruiseSpeedStr : "─");

  if (status == STATUS_DISENGAGED) {
    p.setPen(whiteColor());
  } else if (status == STATUS_OVERRIDE) {
    p.setPen(overrideColor());
  } else if (limit_speed > 0) {
    p.setPen(interpColor(
      cruiseMaxSpeed,
      {limit_speed + 5, limit_speed + 15, limit_speed + 25},
      {greenColor(), lightorangeColor(), pinkColor()}
    ));
  } else {
    p.setPen(greenColor());
  }
  configFont(p, "Open Sans", 35, "Bold");
  QRect max_rect = getTextRect(p, Qt::AlignCenter, "MAX");
  max_rect.moveCenter({max_speed_outer.center().x(), 0});
  max_rect.moveTop(max_speed_rect.top() + 115);
  p.drawText(max_rect, Qt::AlignCenter, tr("MAX"));

  // apply speed (upper left 2)
  if (longControl) {
    QString applySpeedStr = QString::number(std::nearbyint(applyMaxSpeed));
    QRect apply_speed_outer(max_speed_rect.right() - 150, max_speed_rect.top() + 10, 140, 168);
    p.setPen(QPen(whiteColor(200), 2));
    p.drawRoundedRect(apply_speed_outer, 16, 16);

    if (limit_speed > 0 && status != STATUS_DISENGAGED && status != STATUS_OVERRIDE) {
      p.setPen(interpColor(
        applyMaxSpeed,
        {limit_speed + 5, limit_speed + 15, limit_speed + 25},
        {whiteColor(), orangeColor(), redColor()}
      ));
    } else {
      p.setPen(whiteColor());
    }
    configFont(p, "Open Sans", 65, "Regular");
    QRect apply_rect = getTextRect(p, Qt::AlignCenter, applySpeedStr);
    apply_rect.moveCenter({apply_speed_outer.center().x(), 0});
    apply_rect.moveTop(max_speed_rect.top() + 25);
    p.drawText(apply_rect, Qt::AlignCenter, is_cruise_set ? applySpeedStr : "─");

    if (status == STATUS_DISENGAGED) {
      p.setPen(whiteColor());
    } else if (status == STATUS_OVERRIDE) {
      p.setPen(overrideColor());
    } else if (limit_speed > 0) {
      p.setPen(interpColor(
        applyMaxSpeed,
        {limit_speed + 5, limit_speed + 15, limit_speed + 25},
        {greenColor(), lightorangeColor(), pinkColor()}
      ));
    } else {
      p.setPen(greenColor());
    }
    configFont(p, "Open Sans", 35, "Bold");
    QRect long_rect = getTextRect(p, Qt::AlignCenter, "LONG");
    long_rect.moveCenter({apply_speed_outer.center().x(), 0});
    long_rect.moveTop(max_speed_rect.top() + 115);
    p.drawText(long_rect, Qt::AlignCenter, tr("LONG"));
  }

  // speedlimit sign
  if (limit_speed > 0 && left_dist > 0) {
    QPoint center(max_speed_rect.center().x(), speed_rect.top() + 280);
    p.setPen(Qt::NoPen);
    p.setBrush(whiteColor());
    p.drawEllipse(center, 92, 92);
    p.setBrush(redColor());
    p.drawEllipse(center, 86, 86);
    p.setBrush(whiteColor());
    p.drawEllipse(center, 66, 66);

    configFont(p, "Open Sans", 60, "Bold");
    QRect limit_rect = getTextRect(p, Qt::AlignCenter, limitSpeedStr);
    limit_rect.moveCenter(center);
    p.setPen(blackColor());
    p.drawText(limit_rect, Qt::AlignCenter, limitSpeedStr);

    configFont(p, "Open Sans", 50, "Bold");
    QRect left_rect = getTextRect(p, Qt::AlignCenter, leftDistStr);
    left_rect.moveCenter({max_speed_rect.center().x(), 0});
    left_rect.moveBottom(max_speed_rect.bottom() + 265);
    p.setPen(whiteColor());
    p.drawText(left_rect, Qt::AlignCenter, leftDistStr);
  } else if (roadLimitSpeed > 0 && roadLimitSpeed < 120) {
    QPoint center(max_speed_rect.center().x(), speed_rect.top() + 280);
    p.setPen(Qt::NoPen);
    p.setBrush(whiteColor());
    p.drawEllipse(center, 92, 92);
    p.setBrush(redColor());
    p.drawEllipse(center, 86, 86);
    p.setBrush(whiteColor());
    p.drawEllipse(center, 66, 66);

    configFont(p, "Open Sans", 60, "Bold");
    QRect roadlimit_rect = getTextRect(p, Qt::AlignCenter, roadLimitSpeedStr);
    roadlimit_rect.moveCenter(center);
    p.setPen(blackColor());
    p.drawText(roadlimit_rect, Qt::AlignCenter, roadLimitSpeedStr);
  }

  // current speed (upper center)
  QString speedStr = QString::number(std::nearbyint(speed));
  QColor variableColor = QColor(255, 255, 255, 255);

  if (accel > 0) {
    int a = (int)(255.f - (180.f * (accel/3.f)));
    a = std::min(a, 255);
    a = std::max(a, 80);
    variableColor = QColor(a, a, 255, 255);
  } else {
    int a = (int)(255.f - (255.f * (-accel/4.f)));
    a = std::min(a, 255);
    a = std::max(a, 60);
    variableColor = QColor(255, a, a, 255);
  }

  configFont(p, "Open Sans", 176, "Bold");
  drawTextColor(p, rect().center().x(), 230, speedStr, variableColor);
  configFont(p, "Open Sans", 66, "Regular");
  drawTextColor(p, rect().center().x(), 310, speedUnit, lightorangeColor());

  // engage-ability icon ( wheel ) (upper right 1)
  if (status == STATUS_ENGAGED && !steeringPressed) {
    wheel_img = uiState()->scene.experimental_mode ? experimental_img : lat_img;
  } else if (status == STATUS_OVERRIDE && !steeringPressed) {
    wheel_img = longitudinal_img;
  } else {
    wheel_img = steer_img;
  }

  int x,y,w,h = 0;
  x = rect().right() - radius / 2 - bdr_s * 2;
  y = radius / 2 + bdr_s * 4;
  if ((status == STATUS_ENGAGED || status == STATUS_OVERRIDE) && !steeringPressed) {
    drawIcon(p, x, y, wheel_img, blackColor(100), 1.0);
  } else {
    drawIconRotate(p, x, y, wheel_img, blackColor(100), 1.0, angleSteers);
  }

  if (wifi_state == 0) {
    wifi_img = wifi_f_img;
  } else if (wifi_state == 1) {
    wifi_img = wifi_l_img;
  } else if (wifi_state == 2) {
    wifi_img = wifi_m_img;
  } else if (wifi_state == 3) {
    wifi_img = wifi_h_img;
  } else if (wifi_state == 4) {
    wifi_img = wifi_f_img;
  }

  // wifi icon (upper right 2)
  x = rect().right() - (radius / 2) - (bdr_s * 2) - (radius);
  y = radius / 2 + (bdr_s * 4);
  drawIcon(p, x, y, wifi_img, blackColor(100), wifi_state > 0 ? 1.0 : 0.2);

  // gps icon (upper right 3)
  x = rect().right() - (radius / 2) - (bdr_s * 2) - (radius * 2);
  y = radius / 2 + (bdr_s * 4);
  drawIcon(p, x, y, gps_img, blackColor(100), gps_state ? 1.0 : 0.2);

  // N direction icon (upper right 4)
  x = rect().right() - (radius / 2) - (bdr_s * 2) - (radius * 3);
  y = radius / 2 + (bdr_s * 4);
  drawIconRotate(p, x, y, direction_img, blackColor(100), gps_state ? 1.0 : 0.2, gpsBearing);

  // nda icon (upper center)
  if (nda_state > 0) {
    w = 120;
    h = 54;
    x = (width() + (bdr_s * 2)) / 2 - (w / 2) - bdr_s;
    y = 30 - bdr_s;
    p.drawPixmap(x, y, w, h, nda_state == 1 ? nda_img : hda_img);
  }

  // Dev UI (right Side)
  x = rect().right() - radius - bdr_s * 5;
  y = bdr_s * 4 + 202;
  drawRightDevUi(p, x, y);

  // dm icon (bottom 1eft 1)
  x = radius / 2 + (bdr_s * 2);
  y = rect().bottom() - footer_h / 2;
  drawIcon(p, x, y, dm_img, blackColor(100), dmActive ? 1.0 : 0.2);

  // scc gap icon (bottom right 1)
  if (gap_state == 1) {
    gap_img = gap1_img;
  } else if (gap_state == 2) {
    gap_img = gap2_img;
  } else if (gap_state == 3) {
    gap_img = gap3_img;
  } else if (gap_state == 4) {
    gap_img = gap4_img;
  }

  x = radius / 2 + (bdr_s * 2) + radius;
  y = rect().bottom() - footer_h / 2;
  drawIcon(p, x, y, gap_img, blackColor(100), is_cruise_set ? 1.0 : 0.2);

  // brake icon (bottom left 2)
  x = radius / 2 + (bdr_s * 2);
  y = rect().bottom() - (footer_h / 2) - (radius) - 10;
  drawIcon(p, x, y, brake_img, blackColor(100), brake_state ? 1.0 : 0.2);

  // autohold icon (bottom right 2)
  x = radius / 2 + (bdr_s * 2) + (radius);
  y = rect().bottom() - (footer_h / 2) - (radius) - 10;
  drawIcon(p, x, y, autohold_state > 1 ? autohold_warning_img : autohold_active_img, blackColor(100), autohold_state ? 1.0 : 0.2);

  // bsd_l icon (bottom left 3)
  x = radius / 2 + (bdr_s * 2);
  y = rect().bottom() - (footer_h / 2) - (radius * 2) - 20;
  drawIcon(p, x, y, bsd_l_img, blackColor(100), left_blindspot ? 1.0 : 0.2);

  // bsd_r icon (bottom right 3)
  x = radius / 2 + (bdr_s * 2) + (radius);
  y = rect().bottom() - (footer_h / 2) - (radius * 2) - 20;
  drawIcon(p, x, y, bsd_r_img, blackColor(100), right_blindspot ? 1.0 : 0.2);

  // bottom info
  const char* lateral[] = {"Pid", "Indi", "Lqr", "Torque"};

  QString infoText;
  infoText.sprintf("EPS[%d] SCC[%d] SR[%.2f] [ %s ] [ (%.2f,%.2f) / (%.2f,%.2f) ]",
    epsBus, sccBus,
    steerRatio,
    lateral[lateralControl],
    latAccelFactor, friction,
    latAccelFactorRaw, frictionRaw
  );

  x = rect().left() + radius * 3.0;
  y = rect().height() - 15;

  configFont(p, "Open Sans", 30, "Regular");
  drawTextColor(p, x, y, infoText, whiteColor(200));

  // upper gps info
  if (gpsVerticalAccuracy == 0 || gpsVerticalAccuracy > 100)
    gpsAltitude = 999.9;

  if (gpsAccuracy > 100)
    gpsAccuracy = 99.9;
  else if (gpsAccuracy == 0)
    gpsAccuracy = 0;

  QString infoGps;
  infoGps.sprintf("GPS [ Alt(%.1f) Acc(%.1f) Sat(%d) ]",
    gpsAltitude, gpsAccuracy, gpsSatelliteCount
  );

  x = rect().right() - radius * 1.8;
  y = bdr_s * 3;

  configFont(p, "Open Sans", 30, "Regular");
  drawTextColor(p, x, y, infoGps, whiteColor(200));

  // tpms (bottom right)
  w = 200;
  h = 260;
  x = rect().right() - w - (bdr_s * 2);
  y = height() - h - (bdr_s * 2);

  p.drawPixmap(x, y, w, h, tpms_img);

  configFont(p, "Open Sans", 35, "Bold");
  drawTextColor(p, x + 32, y + 70, get_tpms_text(fl), get_tpms_color(fl));
  drawTextColor(p, x + 167, y + 70, get_tpms_text(fr), get_tpms_color(fr));
  drawTextColor(p, x + 32, y + 214, get_tpms_text(rl), get_tpms_color(rl));
  drawTextColor(p, x + 167, y + 214, get_tpms_text(rr), get_tpms_color(rr));

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
  p.setOpacity(1.);
}

int AnnotatedCameraWidget::devUiDrawElement(QPainter &p, int x, int y, const char* value, const char* label, const char* units, const QColor &color) {
  configFont(p, "Open Sans", 45, "SemiBold");
  drawTextColor(p, x + 92, y + 80, QString(value), color);
  configFont(p, "Open Sans", 28, "Regular");
  drawText(p, x + 92, y + 122, QString(label), 255);

  if (strlen(units) > 0) {
    p.save();
    p.translate(x + 173, y + 62);
    p.rotate(-90);
    drawText(p, 0, 0, QString(units), 255);
    p.restore();
  }
  return 110;
}

void AnnotatedCameraWidget::drawRightDevUi(QPainter &p, int x, int y) {
  int rh = 5;
  int ry = y;

  QColor valueColor = whiteColor();

  // Add Real Steering Angle, Unit: Degrees
  if (true) {
    char val_str[8];
    valueColor = limeColor();

    // Red if large steering angle, Orange if moderate steering angle
    if (std::fabs(angleSteers) > 90) {
      valueColor = redColor();
    } else if (std::fabs(angleSteers) > 30) {
      valueColor = orangeColor();
    }
    snprintf(val_str, sizeof(val_str), "%.0f%s", angleSteers , "°");

    //rh += devUiDrawElement(p, x, ry, val_str, "REAL STEER", "", valueColor);
    rh += devUiDrawElement(p, x, ry, val_str, "핸들 조향각", "", valueColor);
    ry = y + rh;
  }

  // Add Desired Steering Angle, Unit: Degrees
  if (status == STATUS_ENGAGED || STATUS_OVERRIDE) {
    char val_str[8];
    valueColor = limeColor();

    // Red if large steering angle, Orange if moderate steering angle
    if (std::fabs(angleSteers) > 90) {
      valueColor = redColor();
    } else if (std::fabs(angleSteers) > 30) {
      valueColor = orangeColor();
    }
    snprintf(val_str, sizeof(val_str), "%.0f%s", steerAngleDesired, "°");

    //rh += devUiDrawElement(p, x, ry, val_str, "DESIR STEER", "", valueColor);
    rh += devUiDrawElement(p, x, ry, val_str, "OP 조향각", "", valueColor);
    ry = y + rh;
  }

/*
  // Add Relative Distance to Primary Lead Car, Unit: Meters
  if (status == STATUS_ENGAGED || STATUS_OVERRIDE) {
    char val_str[8];
    char units_str[8];
    valueColor = whiteColor();

    if (lead_status) {
      // Red if very close, Orange if close
      if (lead_d_rel < 5) {
        valueColor = redColor();
      } else if (lead_d_rel < 15) {
        valueColor = orangeColor();
      }
      snprintf(val_str, sizeof(val_str), "%d", (int)lead_d_rel);
    } else {
      snprintf(val_str, sizeof(val_str), "─");
    }
    snprintf(units_str, sizeof(units_str), "m");

    //rh += devUiDrawElement(p, x, ry, val_str, "REL DIST", units_str, valueColor);
    rh += devUiDrawElement(p, x, ry, val_str, "거리차", units_str, valueColor);
    ry = y + rh;
  }

  // Add Relative Velocity vs Primary Lead Car, Unit: kph if metric, else mph
  if (status == STATUS_ENGAGED || STATUS_OVERRIDE) {
    char val_str[8];
    valueColor = whiteColor();

     if (lead_status) {
       // Red if approaching faster than 10mph, Orange if approaching (negative)
       if (lead_v_rel < -4.4704) {
         valueColor = redColor();
       } else if (lead_v_rel < 0) {
         valueColor = orangeColor();
       }

       if (speedUnit == "mph") {
         snprintf(val_str, sizeof(val_str), "%d", (int)(lead_v_rel * 2.236936)); //mph
       } else {
         snprintf(val_str, sizeof(val_str), "%d", (int)(lead_v_rel * 3.6)); //kph
       }
     } else {
       snprintf(val_str, sizeof(val_str), "─");
     }

    //rh += devUiDrawElement(p, x, ry, val_str, "REL SPEED", speedUnit.toStdString().c_str(), valueColor);
    rh += devUiDrawElement(p, x, ry, val_str, "속도차", speedUnit.toStdString().c_str(), valueColor);
    ry = y + rh;
  }
*/
  rh += 25;
  p.setBrush(blackColor(0));
  QRect ldu(x, y, 184, rh);
  p.drawRoundedRect(ldu, 20, 20);
}

// Window that shows camera view and variety of
// info drawn on top

void AnnotatedCameraWidget::drawIcon(QPainter &p, int x, int y, QPixmap &img, QBrush bg, float opacity) {
  p.setOpacity(1.0);  // bg dictates opacity of ellipse
  p.setPen(Qt::NoPen);
  p.setBrush(bg);
  p.drawEllipse(x - radius / 2, y - radius / 2, radius, radius);
  p.setOpacity(opacity);
  p.drawPixmap(x - img.size().width() / 2, y - img.size().height() / 2, img);
}

void AnnotatedCameraWidget::drawIconRotate(QPainter &p, int x, int y, QPixmap &img, QBrush bg, float opacity, float angle) {
  p.setOpacity(1.0);  // bg dictates opacity of ellipse
  p.setPen(Qt::NoPen);
  p.setBrush(bg);
  p.drawEllipse(x - radius / 2, y - radius / 2, radius, radius);
  p.setOpacity(opacity);
  p.save();
  p.translate(x, y);
  p.rotate(-angle);
  QRect r = img.rect();
  r.moveCenter(QPoint(0,0));
  p.drawPixmap(r, img);
  p.restore();
}

void AnnotatedCameraWidget::drawText(QPainter &p, int x, int y, const QString &text, int alpha) {
  p.setOpacity(1.0);  // bg dictates opacity of ellipse
  QRect real_rect = getTextRect(p, 0, text);
  real_rect.moveCenter({x, y - real_rect.height() / 2});
  p.setPen(QColor(0xff, 0xff, 0xff, alpha));
  p.drawText(real_rect.x(), real_rect.bottom(), text);
}

void AnnotatedCameraWidget::drawTextColor(QPainter &p, int x, int y, const QString &text, const QColor &color) {
  p.setOpacity(1.0);  // bg dictates opacity of ellipse
  QRect real_rect = getTextRect(p, 0, text);
  real_rect.moveCenter({x, y - real_rect.height() / 2});
  p.setPen(color);
  p.drawText(real_rect.x(), real_rect.bottom(), text);
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
  QLinearGradient bg(0, height(), 0, height() / 4);
  const auto &acceleration = sm["modelV2"].getModelV2().getAcceleration();
  float start_hue, end_hue, acceleration_future = 0;

  if (acceleration.getZ().size() > 16) {
    acceleration_future = acceleration.getX()[16];  // 2.5 seconds
  }
  start_hue = 60;
  // speed up: 120, slow down: 0
  end_hue = fmax(fmin(start_hue + acceleration_future * 45, 148), 0);

  // FIXME: painter.drawPolygon can be slow if hue is not rounded
  end_hue = int(end_hue * 100 + 0.5) / 100;

  if (scene.engaged) {
    if (scene.steeringPressed) {
      // The user is applying torque to the steering wheel
      bg.setColorAt(0.0, steeringpressedColor(100));
      bg.setColorAt(0.5, steeringpressedColor(50));
      bg.setColorAt(1.0, steeringpressedColor(0));
    } else if (scene.override) {
      bg.setColorAt(0.0, overrideColor(100));
      bg.setColorAt(0.5, overrideColor(50));
      bg.setColorAt(1.0, overrideColor(0));
    } else if (scene.experimental_mode) {
      bg.setColorAt(0.0, QColor::fromHslF(start_hue / 360., 0.97, 0.56, 0.4));
      bg.setColorAt(0.5, QColor::fromHslF(end_hue / 360., 1.0, 0.68, 0.35));
      bg.setColorAt(1.0, QColor::fromHslF(end_hue / 360., 1.0, 0.68, 0.0));
    } else {
      bg.setColorAt(0.0, QColor::fromHslF(148 / 360., 0.94, 0.51, 0.4));
      bg.setColorAt(0.5, QColor::fromHslF(112 / 360., 1.0, 0.68, 0.35));
      bg.setColorAt(1.0, QColor::fromHslF(112 / 360., 1.0, 0.68, 0.0));
    }
  } else {
    bg.setColorAt(0.0, whiteColor(100));
    bg.setColorAt(0.5, whiteColor(50));
    bg.setColorAt(1.0, whiteColor(0));
  }
  painter.setBrush(bg);
  painter.drawPolygon(scene.track_vertices);

  painter.restore();
}

void AnnotatedCameraWidget::drawLead(QPainter &painter, const cereal::ModelDataV2::LeadDataV3::Reader &lead_data, const QPointF &vd, bool is_radar) {
  painter.save();
  const float speedBuff = 10.;
  const float leadBuff = 40.;
  const float d_rel = lead_data.getX()[0];
  const float v_rel = lead_data.getV()[0];

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
  painter.setBrush(is_radar ? yellowColor() : pinkColor()); // vision : radar
  painter.drawPolygon(glow, std::size(glow));

  // chevron
  QPointF chevron[] = {{x + (sz * 1.25), y + sz}, {x, y}, {x - (sz * 1.25), y + sz}};
  painter.setBrush(redColor(fillAlpha));
  painter.drawPolygon(chevron, std::size(chevron));

  // radar & vision distance ajouatom
  float radar_dist = lead_status ? lead_d_rel : 0;
  float vision_dist = lead_data.getProb() > .5 ? d_rel : 0;

  QString l_dist, l_speed;
  QColor valueColor = whiteColor();
  l_dist.sprintf("%.1f m", lead_status ? radar_dist : vision_dist);

  if (is_radar) { // vision
    if (d_rel < 5) {
      valueColor = redColor(150);
    } else if (d_rel < 15) {
      valueColor = orangeColor(150);
    } else {
      valueColor = whiteColor(150);
    }
  } else { // radar
    if (lead_d_rel < 5) {
      valueColor = redColor(150);
    } else if (lead_d_rel < 15) {
      valueColor = orangeColor(150);
    } else {
      valueColor = whiteColor(150);
    }
  }
  configFont(painter, "Open Sans", 35, "Bold");
  drawTextColor(painter, x, y + sz / 1.5f + 70.0, l_dist, valueColor);

  if (radar_dist) {
    drawTextColor(painter, x, y + sz / 1.5f + 10, "R", blackColor(200));
    if (speedUnit == "mph") {
      l_speed.sprintf("%.0f mph", speed + lead_v_rel * 2.236936); // mph
    } else {
      l_speed.sprintf("%.0f km/h", speed + lead_v_rel * 3.6); // kph
    }
    if (lead_v_rel < -4.4704) {
      valueColor = redColor(150);
    } else if (lead_v_rel < 0) {
      valueColor = orangeColor(150);
    } else {
      valueColor = pinkColor(150);
    }
  } else if (vision_dist) {
     drawTextColor(painter, x, y + sz / 1.5f + 10, "V", blackColor(200));
    if (speedUnit == "mph") {
      l_speed.sprintf("%.0f mph", speed + v_rel * 2.236936); // mph
    } else {
      l_speed.sprintf("%.0f km/h", speed + v_rel * 3.6); // kph
    }
    if (v_rel < -4.4704) {
      valueColor = redColor(150);
    } else if (v_rel < 0) {
      valueColor = orangeColor(150);
    } else {
      valueColor = yellowColor(150);
    }
  }
  configFont(painter, "Open Sans", 35, "Bold");
  drawTextColor(painter, x, y + sz / 1.5f + 120.0, l_speed, valueColor);

  painter.restore();
}

void AnnotatedCameraWidget::paintGL() {
  UIState *s = uiState();
  SubMaster &sm = *(s->sm);
  const double start_draw_t = millis_since_boot();
  const cereal::ModelDataV2::Reader &model = sm["modelV2"].getModelV2();

  // draw camera frame
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
  float v_ego = sm["carState"].getCarState().getVEgo();
  if ((v_ego < 10) || s->wide_cam_only) {
    wide_cam_requested = true;
  } else if (v_ego > 15) {
    wide_cam_requested = false;
  }
  wide_cam_requested = wide_cam_requested && sm["controlsState"].getControlsState().getExperimentalMode();
  // TODO: also detect when ecam vision stream isn't available
  // for replay of old routes, never go to widecam
  wide_cam_requested = wide_cam_requested && s->scene.calibration_wide_valid;
  CameraWidget::setStreamType(wide_cam_requested ? VISION_STREAM_WIDE_ROAD : VISION_STREAM_ROAD);

  s->scene.wide_cam = CameraWidget::getStreamType() == VISION_STREAM_WIDE_ROAD;
  if (s->scene.calibration_valid) {
    auto calib = s->scene.wide_cam ? s->scene.view_from_wide_calib : s->scene.view_from_calib;
    CameraWidget::updateCalibration(calib);
  } else {
    CameraWidget::updateCalibration(DEFAULT_CALIBRATION);
  }
  CameraWidget::setFrameId(model.getFrameId());
  CameraWidget::paintGL();

  QPainter painter(this);
  painter.setRenderHint(QPainter::Antialiasing);
  painter.setPen(Qt::NoPen);

  if (s->worldObjectsVisible()) {
    if (sm.rcv_frame("modelV2") > s->scene.started_frame) {
      update_model(s, sm["modelV2"].getModelV2());
      if (sm.rcv_frame("radarState") > s->scene.started_frame) {
        update_leads(s, sm["radarState"].getRadarState(), sm["modelV2"].getModelV2().getPosition());
      }
    }

    drawLaneLines(painter, s);
    const auto leads = model.getLeadsV3();
    if (leads[0].getProb() > .5) {
      drawLead(painter, leads[0], s->scene.lead_vertices[0], s->scene.lead_radar[0]);
    }
    if (leads[1].getProb() > .5 && (std::abs(leads[1].getX()[0] - leads[0].getX()[0]) > 3.0)) {
      drawLead(painter, leads[1], s->scene.lead_vertices[1], s->scene.lead_radar[1]);
    }
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
