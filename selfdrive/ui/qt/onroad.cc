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

  if (getenv("MAP_RENDER_VIEW")) {
    CameraWidget *map_render = new CameraWidget("navd", VISION_STREAM_MAP, false, this);
    split->insertWidget(0, map_render);
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
    configFont(p, "Inter", 74, "SemiBold");
    p.drawText(r, Qt::AlignCenter, alert.text1);
  } else if (alert.size == cereal::ControlsState::AlertSize::MID) {
    configFont(p, "Inter", 88, "Bold");
    p.drawText(QRect(0, c.y() - 125, width(), 150), Qt::AlignHCenter | Qt::AlignTop, alert.text1);
    configFont(p, "Inter", 66, "Regular");
    p.drawText(QRect(0, c.y() + 21, width(), 90), Qt::AlignHCenter, alert.text2);
  } else if (alert.size == cereal::ControlsState::AlertSize::FULL) {
    bool l = alert.text1.length() > 15;
    configFont(p, "Inter", l ? 132 : 177, "Bold");
    p.drawText(QRect(0, r.y() + (l ? 240 : 270), width(), 600), Qt::AlignHCenter | Qt::TextWordWrap, alert.text1);
    configFont(p, "Inter", 88, "Regular");
    p.drawText(QRect(0, r.height() - (l ? 361 : 420), width(), 300), Qt::AlignHCenter | Qt::TextWordWrap, alert.text2);
  }
}


ExperimentalButton::ExperimentalButton(QWidget *parent) : QPushButton(parent) {
  //setVisible(false);
  setVisible(true);
  setFixedSize(btn_size, btn_size);
  setCheckable(true);

  params = Params();
  engage_img = loadPixmap("../assets/img_experimental_white.svg", {img_size, img_size});
  experimental_img = loadPixmap("../assets/img_experimental.svg", {img_size, img_size});

  QObject::connect(this, &QPushButton::toggled, [=](bool checked) {
    params.putBool("ExperimentalMode", checked);
  });
}

void ExperimentalButton::updateState(const UIState &s) {
  const SubMaster &sm = *(s.sm);

  // button is "visible" if engageable or enabled
  //const auto cs = sm["controlsState"].getControlsState();
  //setVisible(cs.getEngageable() || cs.getEnabled());

  // button is "checked" if experimental mode is enabled
  setChecked(sm["controlsState"].getControlsState().getExperimentalMode());

  // disable button when experimental mode is not available, or has not been confirmed for the first time
  const auto cp = sm["carParams"].getCarParams();
  const bool experimental_mode_available = cp.getExperimentalLongitudinalAvailable() ? params.getBool("ExperimentalLongitudinalEnabled") : cp.getOpenpilotLongitudinalControl();
  setEnabled(params.getBool("ExperimentalModeConfirmed") && experimental_mode_available);
}

void ExperimentalButton::paintEvent(QPaintEvent *event) {
  QPainter p(this);
  p.setRenderHint(QPainter::Antialiasing);

  QPoint center(btn_size / 2, btn_size / 2);
  QPixmap img = isChecked() ? experimental_img : engage_img;

  p.setOpacity(1.0);
  p.setPen(Qt::NoPen);
  //p.setBrush(QColor(0, 0, 0, 166));
  p.setBrush(QColor(0, 0, 0, 100)); // icon_bg
  p.drawEllipse(center, btn_size / 2, btn_size / 2);
  p.setOpacity(isDown() ? 0.8 : 1.0);
  //p.drawPixmap((btn_size - img_size) / 2, (btn_size - img_size) / 2, img);
  p.drawPixmap((btn_size - img.size().width()) / 2, (btn_size - img.size().height()) / 2, img);
}

AnnotatedCameraWidget::AnnotatedCameraWidget(VisionStreamType type, QWidget* parent) : fps_filter(UI_FREQ, 3, 1. / UI_FREQ), CameraWidget("camerad", type, true, parent) {
  pm = std::make_unique<PubMaster, const std::initializer_list<const char *>>({"uiDebug"});

  QVBoxLayout *main_layout  = new QVBoxLayout(this);
  main_layout->setMargin(bdr_s * 4);
  main_layout->setSpacing(20);

  experimental_btn = new ExperimentalButton(this);
  main_layout->addWidget(experimental_btn, 0, Qt::AlignTop | Qt::AlignRight);

  steer_img = loadPixmap("../assets/img_chffr_wheel.png", {img_size, img_size});
  lat_img = loadPixmap("../assets/offroad/icon_speed_limit.png", {img_size, img_size});
  gaspress_img = loadPixmap("../assets/offroad/icon_disengage_on_accelerator.svg", {img_size, img_size});
  dm_img = loadPixmap("../assets/img_driver_face.png", {img_size + 5, img_size + 5});

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
  setProperty("isStandstill", ce.getStandstill());
  setProperty("brake_state", ce.getBrakeLights());
  setProperty("autohold_state", ce.getAutoHold());
  setProperty("gas_pressed", ce.getGasPressed());
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
  setProperty("steerAngle", ce.getSteeringAngleDeg());
  setProperty("steerAngleOp", cc.getActuators().getSteeringAngleDeg());
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

  // update engageability/experimental mode button
  experimental_btn->updateState(s);

  // update DM icons at 2Hz
  if (sm.frame % (UI_FREQ / 2) == 0) {
    setProperty("dmActive", sm["driverMonitoringState"].getDriverMonitoringState().getIsActiveMode());
    setProperty("rightHandDM", sm["driverMonitoringState"].getDriverMonitoringState().getIsRHD());
  }

  // DM icon transition
  dm_fade_state = fmax(0.0, fmin(1.0, dm_fade_state+0.2*(0.5-(float)(dmActive))));
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
    leftDistStr.sprintf("%.1f km", left_dist / 1000.f);
  } else if (left_dist > 0) {
    leftDistStr.sprintf("%.0f m", left_dist);
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
  configFont(p, "Inter", 65, "Regular");
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
      cruiseMaxSpeed,
      {limit_speed + 5, limit_speed + 15, limit_speed + 25},
      {greenColor(), lightorangeColor(), pinkColor()}
    ));
  } else {
    p.setPen(greenColor());
  }
  configFont(p, "Inter", 35, "Bold");
  QRect max_rect = getTextRect(p, Qt::AlignCenter, "MAX");
  max_rect.moveCenter({max_speed_outer.center().x(), 0});
  max_rect.moveTop(max_speed_rect.top() + 25);
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
    configFont(p, "Inter", 65, "Regular");
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
        applyMaxSpeed,
        {limit_speed + 5, limit_speed + 15, limit_speed + 25},
        {greenColor(), lightorangeColor(), pinkColor()}
      ));
    } else {
      p.setPen(greenColor());
    }
    configFont(p, "Inter", 35, "Bold");
    QRect long_rect = getTextRect(p, Qt::AlignCenter, "LONG");
    long_rect.moveCenter({apply_speed_outer.center().x(), 0});
    long_rect.moveTop(max_speed_rect.top() + 25);
    p.drawText(long_rect, Qt::AlignCenter, tr("LONG"));
  }

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

    configFont(p, "Inter", 60, "Bold");
    QRect limit_rect = getTextRect(p, Qt::AlignCenter, limitSpeedStr);
    limit_rect.moveCenter(center);
    p.setPen(blackColor());
    p.drawText(limit_rect, Qt::AlignCenter, limitSpeedStr);

    configFont(p, "Inter", 50, "Bold");
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

    configFont(p, "Inter", 60, "Bold");
    QRect roadlimit_rect = getTextRect(p, Qt::AlignCenter, roadLimitSpeedStr);
    roadlimit_rect.moveCenter(center);
    p.setPen(blackColor());
    p.drawText(roadlimit_rect, Qt::AlignCenter, roadLimitSpeedStr);
  }

  // current speed (upper center)
  QString speedStr = QString::number(std::nearbyint(speed));
  QColor variableColor = whiteColor();

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

  configFont(p, "Inter", 176, "Bold");
  drawTextColor(p, rect().center().x(), 230, speedStr, variableColor);
  configFont(p, "Inter", 66, "Regular");
  drawTextColor(p, rect().center().x(), 310, speedUnit, lightorangeColor());

  int x,y,w,h = 0;
  QColor icon_bg = blackColor(100);
/*
  // e2e mode icon (upper right 1)
  if (uiState()->scene.experimental_mode) {
    long_img = experimental_img;
  } else {
    long_img = engage_img;
  }

  x = rect().right() - (btn_size / 2) - (bdr_s * 2);
  y = (btn_size / 2) + (bdr_s * 4);
  drawIcon(p, x, y, long_img, icon_bg, 1.0);
*/
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
  x = rect().right() - (btn_size / 2) - (bdr_s * 3) - (btn_size);
  y = (btn_size / 2) + (bdr_s * 4);
  drawIcon(p, x, y, wifi_img, icon_bg, wifi_state > 0 ? 1.0 : 0.2);

  // gps icon (upper right 3)
  x = rect().right() - (btn_size / 2) - (bdr_s * 3) - (btn_size * 2);
  y = (btn_size / 2) + (bdr_s * 4);
  drawIcon(p, x, y, gps_img, icon_bg, gps_state ? 1.0 : 0.2);

  // N direction icon (upper right 4)
  x = rect().right() - (btn_size / 2) - (bdr_s * 3) - (btn_size * 3);
  y = (btn_size / 2) + (bdr_s * 4);
  drawIconRotate(p, x, y, direction_img, icon_bg, gps_state ? 1.0 : 0.2, gpsBearing);

  // nda icon (upper center)
  if (nda_state > 0) {
    w = 120;
    h = 54;
    x = (width() + (bdr_s * 2)) / 2 - (w / 2) - bdr_s;
    y = 30 - bdr_s;
    p.drawPixmap(x, y, w, h, nda_state == 1 ? nda_img : hda_img);
  }

  // steer img (upper right down 1)
  x = rect().right() - (btn_size / 2) - (bdr_s * 3);
  y = (btn_size / 2) + (bdr_s * 20);
  drawIconRotate(p, x, y, steer_img, icon_bg, 1.0, steerAngle);

  QString sa_str, saop_str;
  QColor sa_color = limeColor(200);
  if (std::fabs(steerAngle) > 90) {
    sa_color = redColor(200);
  } else if (std::fabs(steerAngle) > 30) {
    sa_color = orangeColor(200);
  }
  sa_str.sprintf("%.0f °", steerAngle);
  saop_str.sprintf("%.0f °", steerAngleOp);
  configFont(p, "Inter", 35, "Bold");

  drawTextColor(p, x, y + 120, sa_str, sa_color);
  if (status != STATUS_DISENGAGED) {
    drawTextColor(p, x, y + 170, saop_str, sa_color);
  }

  // lat icon (upper right down 2)
  x = rect().right() - (btn_size / 2) - (bdr_s * 3) - (btn_size);
  y = (btn_size / 2) + (bdr_s * 20);
  drawIcon(p, x, y, lat_img, icon_bg, (status != STATUS_DISENGAGED) ? 1.0 : 0.2);

  // gaspress img (bottom right)
  x = rect().right() - (btn_size / 2) - (bdr_s * 2) - (btn_size * 1.5);
  y = rect().bottom() - (footer_h / 2) + (bdr_s *2);
  drawIcon(p, x, y, gaspress_img, icon_bg, gas_pressed ? 1.0 : 0.2);

  // dm icon (bottom 1eft 1)
  // AnnotatedCameraWidget::drawDriverState

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

  x = (btn_size / 2) + (bdr_s * 2) + btn_size;
  y = rect().bottom() - (footer_h / 2);
  drawIcon(p, x, y, gap_img, icon_bg, is_cruise_set ? 1.0 : 0.2);

  // brake icon (bottom left 2)
  x = (btn_size / 2) + (bdr_s * 2);
  y = rect().bottom() - (footer_h / 2) - (btn_size) - 10;
  drawIcon(p, x, y, brake_img, icon_bg, brake_state ? 1.0 : 0.2);

  // autohold icon (bottom right 2)
  x = (btn_size / 2) + (bdr_s * 2) + (btn_size);
  y = rect().bottom() - (footer_h / 2) - (btn_size) - 10;
  drawIcon(p, x, y, autohold_state > 1 ? autohold_warning_img : autohold_active_img, icon_bg, autohold_state ? 1.0 : 0.2);

  // bsd_l icon (bottom left 3)
  x = (btn_size / 2) + (bdr_s * 2);
  y = rect().bottom() - (footer_h / 2) - (btn_size * 2) - 20;
  drawIcon(p, x, y, bsd_l_img, icon_bg, left_blindspot ? 1.0 : 0.2);

  // bsd_r icon (bottom right 3)
  x = (btn_size / 2) + (bdr_s * 2) + (btn_size);
  y = rect().bottom() - (footer_h / 2) - (btn_size * 2) - 20;
  drawIcon(p, x, y, bsd_r_img, icon_bg, right_blindspot ? 1.0 : 0.2);

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

  x = rect().left() + btn_size * 3.0;
  y = rect().height() - 15;

  configFont(p, "Inter", 30, "Regular");
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

  x = rect().right() - (btn_size * 1.8);
  y = (bdr_s * 3);

  configFont(p, "Inter", 30, "Regular");
  drawTextColor(p, x, y, infoGps, whiteColor(200));

  // tpms (bottom right)
  w = 200;
  h = 260;
  x = rect().right() - w - (bdr_s * 2);
  y = height() - h - (bdr_s * 2);

  p.drawPixmap(x, y, w, h, tpms_img);

  configFont(p, "Inter", 35, "Bold");
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

// Window that shows camera view and variety of
// info drawn on top

void AnnotatedCameraWidget::drawIcon(QPainter &p, int x, int y, QPixmap &img, QBrush bg, float opacity) {
  p.setOpacity(1.0);  // bg dictates opacity of ellipse
  p.setPen(Qt::NoPen);
  p.setBrush(bg);
  p.drawEllipse(x - btn_size / 2, y - btn_size / 2, btn_size, btn_size);
  p.setOpacity(opacity);
  p.drawPixmap(x - img.size().width() / 2, y - img.size().height() / 2, img);
}

void AnnotatedCameraWidget::drawIconRotate(QPainter &p, int x, int y, QPixmap &img, QBrush bg, float opacity, float angle) {
  p.setOpacity(1.0);  // bg dictates opacity of ellipse
  p.setPen(Qt::NoPen);
  p.setBrush(bg);
  p.drawEllipse(x - btn_size / 2, y - btn_size / 2, btn_size, btn_size);
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

void AnnotatedCameraWidget::drawDriverState(QPainter &painter, const UIState *s) {
  const UIScene &scene = s->scene;

  painter.save();

  // base icon
  int x = rightHandDM ? rect().right() -  (btn_size - 24) / 2 - (bdr_s * 2) : (btn_size - 24) / 2 + (bdr_s * 2);
  int y = rect().bottom() - footer_h / 2;
  float opacity = dmActive ? 0.65 : 0.2;
  drawIcon(painter, x, y, dm_img, blackColor(0), opacity);

  // circle background
  painter.setOpacity(1.0);
  painter.setPen(Qt::NoPen);
  painter.setBrush(blackColor(70));
  painter.drawEllipse(x - btn_size / 2, y - btn_size / 2, btn_size, btn_size);

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
  QColor arc_color = QColor::fromRgbF(0.09, 0.945, 0.26, 0.4*(1.0-dm_fade_state)*(s->engaged()));
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
  l_dist.sprintf("%.1f m", d_rel);

  if (v_rel < -4.4704) {
    v_color = redColor(150);
  } else if (v_rel < 0) {
    v_color = orangeColor(150);
  } else {
    v_color = pinkColor(150);
  }
  if (speedUnit == "mph") {
    l_speed.sprintf("%.0f mph", speed + v_rel * 2.236936); // mph
  } else {
    l_speed.sprintf("%.0f km/h", speed + v_rel * 3.6); // kph
  }
  configFont(painter, "Inter", 35, "Bold");
  drawTextColor(painter, x, y + sz / 1.5f + 10, is_cruise_set ? "▲" : "", blackColor(200));
  drawTextColor(painter, x, y + sz / 1.5f + 70.0, l_dist, d_color);
  drawTextColor(painter, x, y + sz / 1.5f + 120.0, l_speed, v_color);

  painter.restore();
}

void AnnotatedCameraWidget::paintGL() {
  UIState *s = uiState();
  SubMaster &sm = *(s->sm);
  const double start_draw_t = millis_since_boot();
  const cereal::ModelDataV2::Reader &model = sm["modelV2"].getModelV2();
  const cereal::RadarState::Reader &radar_state = sm["radarState"].getRadarState();

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
        update_leads(s, radar_state, sm["modelV2"].getModelV2().getPosition());
      }
    }

    drawLaneLines(painter, s);
    auto lead_one = radar_state.getLeadOne();
    auto lead_two = radar_state.getLeadTwo();
    if (lead_one.getStatus()) {
      drawLead(painter, lead_one, s->scene.lead_vertices[0]);
    }
    if (lead_two.getStatus() && (std::abs(lead_one.getDRel() - lead_two.getDRel()) > 3.0)) {
      drawLead(painter, lead_two, s->scene.lead_vertices[1]);
    }
  }

  // DMoji
  if (sm.rcv_frame("driverStateV2") > s->scene.started_frame) {
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
