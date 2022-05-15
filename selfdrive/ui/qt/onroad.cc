#include "selfdrive/ui/qt/onroad.h"

#include <cmath>

#include <QDebug>
#include <QString>

#include "selfdrive/common/timing.h"
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

  QStackedLayout *road_view_layout = new QStackedLayout;
  road_view_layout->setStackingMode(QStackedLayout::StackAll);
  nvg = new NvgWindow(VISION_STREAM_RGB_ROAD, this);
  road_view_layout->addWidget(nvg);
  hud = new OnroadHud(this);
  road_view_layout->addWidget(hud);

  QWidget * split_wrapper = new QWidget;
  split = new QHBoxLayout(split_wrapper);
  split->setContentsMargins(0, 0, 0, 0);
  split->setSpacing(0);
  split->addLayout(road_view_layout);

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

  hud->updateState(s);

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
      split->addWidget(m, 0, Qt::AlignRight);

      // Make map visible after adding to split
      m->offroadTransition(offroad);
    }
  }
#endif

  alerts->updateAlert({}, bg);

  // update stream type
  bool wide_cam = Hardware::TICI() && Params().getBool("EnableWideCamera");
  nvg->setStreamType(wide_cam ? VISION_STREAM_RGB_WIDE_ROAD : VISION_STREAM_RGB_ROAD);
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

// OnroadHud ( wirelessnet2 init )
OnroadHud::OnroadHud(QWidget *parent) : QWidget(parent) {
  engage_img = loadPixmap("../assets/img_chffr_wheel.png", {img_size, img_size});
  dm_img = loadPixmap("../assets/img_driver_face.png", {img_size, img_size});

  // crwusiz add
  brake_img = loadPixmap("../assets/img_brake_disc.png", {img_size, img_size});
  bsd_l_img = loadPixmap("../assets/img_bsd_l.png", {img_size, img_size});
  bsd_r_img = loadPixmap("../assets/img_bsd_r.png", {img_size, img_size});
  gps_img = loadPixmap("../assets/img_gps.png", {img_size, img_size});
  wifi_img = loadPixmap("../assets/img_wifi.png", {img_size, img_size});
  direction_img = loadPixmap("../assets/img_direction.png", {img_size, img_size});
  gap1_img = loadPixmap("../assets/img_gap1.png", {img_size, img_size});
  gap2_img = loadPixmap("../assets/img_gap2.png", {img_size, img_size});
  gap3_img = loadPixmap("../assets/img_gap3.png", {img_size, img_size});
  gap4_img = loadPixmap("../assets/img_gap4.png", {img_size, img_size});
  gap_auto_img = loadPixmap("../assets/img_gap_auto.png", {img_size, img_size});

  // neokii add
  autohold_warning_img = loadPixmap("../assets/img_autohold_warning.png", {img_size, img_size});
  autohold_active_img = loadPixmap("../assets/img_autohold_active.png", {img_size, img_size});
  turnsignal_l_img = loadPixmap("../assets/img_turnsignal_l.png", {img_size, img_size});
  turnsignal_r_img = loadPixmap("../assets/img_turnsignal_r.png", {img_size, img_size});
  nda_img = loadPixmap("../assets/img_nda.png");
  hda_img = loadPixmap("../assets/img_hda.png");
  tpms_img = loadPixmap("../assets/img_tpms.png");

  connect(this, &OnroadHud::valueChanged, [=] { update(); });
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

void OnroadHud::updateState(const UIState &s) {
  const SubMaster &sm = *(s.sm);
  const auto cs = sm["controlsState"].getControlsState();
  const auto cc = sm["carControl"].getCarControl();
  const auto ce = sm["carState"].getCarState();
  const auto cp = sm["carParams"].getCarParams();
  const auto ds = sm["deviceState"].getDeviceState();
  const auto rs = sm["radarState"].getRadarState();
  const auto lp = sm["liveParameters"].getLiveParameters();
  const auto ge = sm["gpsLocationExternal"].getGpsLocationExternal();
  auto ls = sm["roadLimitSpeed"].getRoadLimitSpeed();

  float cur_speed = std::max(0.0, ce.getVEgo() * (s.scene.is_metric ? MS_TO_KPH : MS_TO_MPH));
  float applyMaxSpeed = cc.getSccSmoother().getApplyMaxSpeed();
  float cruiseMaxSpeed = cc.getSccSmoother().getCruiseMaxSpeed();
  bool cruise_set = (cruiseMaxSpeed > 0 && cruiseMaxSpeed < 255);

  if (cruise_set && !s.scene.is_metric) {
    applyMaxSpeed *= KM_TO_MILE;
    cruiseMaxSpeed *= KM_TO_MILE;
  }

  setProperty("is_cruise_set", cruise_set);
  setProperty("speed", QString::number(std::nearbyint(cur_speed)));
  setProperty("applyMaxSpeed", QString::number(std::nearbyint(applyMaxSpeed)));
  setProperty("cruiseMaxSpeed", QString::number(std::nearbyint(cruiseMaxSpeed)));
  setProperty("speedUnit", s.scene.is_metric ? "km/h" : "mph");
  setProperty("status", s.status);
  setProperty("steeringPressed", ce.getSteeringPressed());
  setProperty("dmActive", sm["driverMonitoringState"].getDriverMonitoringState().getIsActiveMode());
  setProperty("brake_status", ce.getBrakeLights() || ce.getBrakePressed());
  setProperty("autohold_status", ce.getAutoHold());
  setProperty("nda_status", ls.getActive());
  setProperty("bsd_l_status", ce.getLeftBlindspot());
  setProperty("bsd_r_status", ce.getRightBlindspot());
  setProperty("wifi_status", (int)ds.getNetworkStrength() > 0);
  setProperty("gps_status", sm["liveLocationKalman"].getLiveLocationKalman().getGpsOK());
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
  setProperty("longControl", cc.getSccSmoother().getLongControl());
  setProperty("gap", ce.getCruiseGap());
  setProperty("autoTrGap", cc.getSccSmoother().getAutoTrGap());
  setProperty("lateralcontrol_status", cs.getLateralControlSelect());
  setProperty("steerRatio", lp.getSteerRatio());
  setProperty("mdpsBus", cp.getMdpsBus());
  setProperty("sccBus", cp.getSccBus());
  setProperty("fl", ce.getTpms().getFl());
  setProperty("fr", ce.getTpms().getFr());
  setProperty("rl", ce.getTpms().getRl());
  setProperty("rr", ce.getTpms().getRr());
  setProperty("camLimitSpeed", ls.getCamLimitSpeed());
  setProperty("camLimitSpeedLeftDist", ls.getCamLimitSpeedLeftDist());
  setProperty("sectionLimitSpeed", ls.getSectionLimitSpeed());
  setProperty("sectionLeftDist", ls.getSectionLeftDist());
  setProperty("left_on", ce.getLeftBlinker());
  setProperty("right_on", ce.getRightBlinker());
}

void OnroadHud::paintEvent(QPaintEvent *event) {
  QPainter p(this);
  p.setRenderHint(QPainter::Antialiasing);

  // Header gradient
  QLinearGradient bg(0, header_h - (header_h / 2.5), 0, header_h);
  bg.setColorAt(0, QColor::fromRgbF(0, 0, 0, 0.45));
  bg.setColorAt(1, QColor::fromRgbF(0, 0, 0, 0));
  p.fillRect(0, 0, width(), header_h, bg);

  // maxspeed (upper left 1)
  QRect rc(30, 30, 184, 202);
  p.setPen(QPen(QColor(0xff, 0xff, 0xff, 100), 10));
  p.setBrush(QColor(0, 0, 0, 100));
  p.drawRoundedRect(rc, 20, 20);
  p.setPen(Qt::NoPen);

  if (is_cruise_set) {
    configFont(p, "Open Sans", 55, "Bold");
    drawTextColor(p, rc.center().x(), 100, applyMaxSpeed, yellowColor());
    configFont(p, "Open Sans", 76, "Bold");
    drawTextColor(p, rc.center().x(), 195, cruiseMaxSpeed, whiteColor());
  } else {
    if (longControl) {
      configFont(p, "Open Sans", 55, "sans-semibold");
      drawTextColor(p, rc.center().x(), 100, "OP", yellowColor());
    } else {
      configFont(p, "Open Sans", 55, "sans-semibold");
      drawTextColor(p, rc.center().x(), 100, "SET", yellowColor());
    }
    configFont(p, "Open Sans", 76, "sans-semibold");
    drawTextColor(p, rc.center().x(), 195, "──", whiteColor());
  }

  // current speed (upper center)
  configFont(p, "Open Sans", 176, "Bold");
  drawTextColor(p, rect().center().x(), 230, speed, whiteColor());
  configFont(p, "Open Sans", 66, "Regular");
  drawTextColor(p, rect().center().x(), 310, speedUnit, yellowColor());

  // engage-ability icon ( wheel ) (upper right 1)
  QColor wheelbgColor = blackColor(70);

  if (status == STATUS_ENGAGED && !steeringPressed) {
    wheelbgColor = engagedColor(200);
  } else if (status == STATUS_OVERRIDE && !steeringPressed) {
    wheelbgColor = overrideColor(200);
  } else if (status == STATUS_WARNING) {
    wheelbgColor = warningColor(200);
  } else if (steeringPressed) {
    wheelbgColor = steeringpressedColor(200);
  }

  x = rect().right() - radius / 2 - bdr_s * 2;
  y = radius / 2 + bdr_s * 4;
  drawIconRotate(p, x, y, engage_img, wheelbgColor, 1.0, angleSteers);

  // wifi icon (upper right 2)
  x = rect().right() - (radius / 2) - (bdr_s * 2) - (radius);
  y = radius / 2 + (bdr_s * 4);
  drawIcon(p, x, y, wifi_img, blackColor(70), wifi_status ? 1.0 : 0.2);
  p.setOpacity(1.0);

  // gps icon (upper right 3)
  x = rect().right() - (radius / 2) - (bdr_s * 2) - (radius * 2);
  y = radius / 2 + (bdr_s * 4);
  drawIcon(p, x, y, gps_img, blackColor(70), gps_status ? 1.0 : 0.2);
  p.setOpacity(1.0);

  // N direction icon (upper right 4)
  x = rect().right() - (radius / 2) - (bdr_s * 2) - (radius * 3);
  y = radius / 2 + (bdr_s * 4);
  drawIconRotate(p, x, y, direction_img, blackColor(70), gps_status ? 1.0 : 0.2, gpsBearing);
  p.setOpacity(1.0);

  // nda icon (upper center)
  if (nda_status > 0) {
    w = 120;
    h = 54;
    x = (width() + (bdr_s * 2)) / 2 - (w / 2) - bdr_s;
    y = 30 - bdr_s;
    p.drawPixmap(x, y, w, h, nda_status == 1 ? nda_img : hda_img);
  }

  // Dev UI (right Side)
  x = rect().right() - radius - bdr_s * 5;
  y = bdr_s * 4 + rc.height();
  drawRightDevUi(p, x, y);
  p.setOpacity(1.0);

  // dm icon (bottom 1eft 1)
  x = radius / 2 + (bdr_s * 2);
  y = rect().bottom() - footer_h / 2;
  drawIcon(p, x, y, dm_img, blackColor(70), dmActive ? 1.0 : 0.2);
  p.setOpacity(1.0);

  // scc gap icon (bottom right 1)
  if (gap == 1) {
    gap_img = gap1_img;
  } else if (gap == 2) {
    gap_img = gap2_img;
  } else if (gap == 3) {
    gap_img = gap3_img;
  } else if (gap == 4 && !longControl) {
    gap_img = gap4_img;
  } else if ((gap == 4 || gap == autoTrGap) && longControl) {
    gap_img = gap_auto_img;
  }

  x = radius / 2 + (bdr_s * 2) + radius;
  y = rect().bottom() - footer_h / 2;
  drawIcon(p, x, y, gap_img, blackColor(70), is_cruise_set ? 1.0 : 0.2);
  p.setOpacity(1.0);

  // brake icon (bottom left 2)
  x = radius / 2 + (bdr_s * 2);
  y = rect().bottom() - (footer_h / 2) - (radius) - 10;
  drawIcon(p, x, y, brake_img, blackColor(70), brake_status ? 1.0 : 0.2);
  p.setOpacity(1.0);

  // autohold icon (bottom right 2)
  x = radius / 2 + (bdr_s * 2) + (radius);
  y = rect().bottom() - (footer_h / 2) - (radius) - 10;
  drawIcon(p, x, y, autohold_status > 1 ? autohold_warning_img : autohold_active_img, blackColor(70), autohold_status ? 1.0 : 0.2);
  p.setOpacity(1.0);

  // bsd_l icon (bottom left 3)
  x = radius / 2 + (bdr_s * 2);
  y = rect().bottom() - (footer_h / 2) - (radius * 2) - 20;
  drawIcon(p, x, y, bsd_l_img, blackColor(70), bsd_l_status ? 1.0 : 0.2);
  p.setOpacity(1.0);

  // bsd_r icon (bottom right 3)
  x = radius / 2 + (bdr_s * 2) + (radius);
  y = rect().bottom() - (footer_h / 2) - (radius * 2) - 20;
  drawIcon(p, x, y, bsd_r_img, blackColor(70), bsd_r_status ? 1.0 : 0.2);
  p.setOpacity(1.0);

  // bottom info
  const char* lateral[] = {"Pid", "Indi", "Lqr", "Torque"};

  QString infoText;
  infoText.sprintf("[ %s ] SR[%.2f] MDPS[%d] SCC[%d]",
    lateral[lateralcontrol_status], steerRatio, mdpsBus, sccBus
  );

  x = rect().left() + radius * 1.8;
  y = rect().height() - 15;

  configFont(p, "Open Sans", 30, "Regular");
  drawTextColor(p, x, y, infoText, whiteColor(200));
  p.setOpacity(1.0);

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
  p.setOpacity(1.0);

  // tpms (bottom right)
  w = 200;
  h = 260;
  x = rect().right() - w - (bdr_s * 2);
  y = height() - h - (bdr_s * 2);

  p.setOpacity(0.8);
  p.drawPixmap(x, y, w, h, tpms_img);

  configFont(p, "Open Sans", 35, "Bold");
  drawTextColor(p, x + 32, y + 70, get_tpms_text(fl), get_tpms_color(fl));
  drawTextColor(p, x + 167, y + 70, get_tpms_text(fr), get_tpms_color(fr));
  drawTextColor(p, x + 32, y + 214, get_tpms_text(rl), get_tpms_color(rl));
  drawTextColor(p, x + 167, y + 214, get_tpms_text(rr), get_tpms_color(rr));
  p.setOpacity(1.0);

  // speedlimit (upper left 2)
  int limit_speed = 0;
  int left_dist = 0;

  if (camLimitSpeed > 0 && camLimitSpeedLeftDist > 0) {
    limit_speed = camLimitSpeed;
    left_dist = camLimitSpeedLeftDist;
  }
  else if (sectionLimitSpeed > 0 && sectionLeftDist > 0) {
    limit_speed = sectionLimitSpeed;
    left_dist = sectionLeftDist;
  }

  if (limit_speed > 10 && limit_speed < 130) {
    x = radius * 1.5 + (bdr_s * 2);
    y = 30;

    p.setPen(Qt::NoPen);
    p.setBrush(QBrush(redColor()));
    QRect rect = QRect(x, y, 200, 200);
    p.drawEllipse(rect);
    p.setBrush(QBrush(whiteColor()));

    const int tickness = 20;
    rect.adjust(tickness, tickness, -tickness, -tickness);
    p.drawEllipse(rect);

    QString str_limit_speed, str_left_dist;
    str_limit_speed.sprintf("%d", limit_speed);

    if (left_dist >= 1000)
      str_left_dist.sprintf("%.1fkm", left_dist / 1000.f);
    else if (left_dist > 0)
      str_left_dist.sprintf("%dm", left_dist);

    configFont(p, "Open Sans", 75, "Bold");
    p.setPen(blackColor(230));
    p.drawText(rect, Qt::AlignCenter, str_limit_speed);

    if (str_left_dist.length() > 0) {
      configFont(p, "Open Sans", 60, "Bold");
      rect.translate(0, 145);
      rect.adjust(-30, 0, 30, 0);
      p.setPen(whiteColor(230));
      p.drawText(rect, Qt::AlignCenter, str_left_dist);
    }
  }
  p.setOpacity(1.0);

  // turnsignal
  static int blink_index = 0;
  static int blink_wait = 0;
  static double prev_ts = 0.0;

  if (blink_wait > 0) {
    blink_wait--;
    blink_index = 0;
  } else {
    const float img_alpha = 0.8f;
    const int fb_w = width() / 2 - 200;
    const int center_x = width() / 2;
    const int w = fb_w / 20;
    const int h = 220;
    const int gap = fb_w / 20;
    const int margin = (int)(fb_w / 3.8f);
    const int base_y = (height() - h) / 2;
    const int draw_count = 6;

    x = center_x;
    y = base_y;

    if (left_on) {
      for (int i = 0; i < draw_count; i++) {
        float alpha = img_alpha;
        int d = std::abs(blink_index - i);
        if (d > 0)
          alpha /= d * 2;

        p.setOpacity(alpha);
        float factor = (float)draw_count / (i + draw_count);
        p.drawPixmap(x - w - margin, y, w * factor, h, turnsignal_l_img);
        x -= gap + w;
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
        float factor = (float)draw_count / (i + draw_count);
        p.drawPixmap(x + margin, y, w * factor, h, turnsignal_r_img);
        x += gap + w;
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

int OnroadHud::devUiDrawElement(QPainter &p, int x, int y, const char* value, const char* label, const char* units, const QColor &color) {
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

void OnroadHud::drawRightDevUi(QPainter &p, int x, int y) {
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
    snprintf(val_str, sizeof(val_str), "%.0f%s%s", angleSteers , "°", "");

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
    snprintf(val_str, sizeof(val_str), "%.0f%s%s", steerAngleDesired, "°", "");

    //rh += devUiDrawElement(p, x, ry, val_str, "DESIR STEER", "", valueColor);
    rh += devUiDrawElement(p, x, ry, val_str, "OP 조향각", "", valueColor);
    ry = y + rh;
  }

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

  rh += 25;
  p.setBrush(blackColor(0));
  QRect ldu(x, y, 184, rh);
  p.drawRoundedRect(ldu, 20, 20);
}

//-------------------------------------------------------------------------------------------
void OnroadHud::drawIcon(QPainter &p, int x, int y, QPixmap &img, QBrush bg, float opacity) {
  p.setPen(Qt::NoPen);
  p.setBrush(bg);
  p.drawEllipse(x - radius / 2, y - radius / 2, radius, radius);
  p.setOpacity(opacity);
  p.drawPixmap(x - img_size / 2, y - img_size / 2, img);
}

void OnroadHud::drawIconRotate(QPainter &p, int x, int y, QPixmap &img, QBrush bg, float opacity, float angle) {
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

void OnroadHud::drawText(QPainter &p, int x, int y, const QString &text, int alpha) {
  QFontMetrics fm(p.font());
  QRect init_rect = fm.boundingRect(text);
  QRect real_rect = fm.boundingRect(init_rect, 0, text);
  real_rect.moveCenter({x, y - real_rect.height() / 2});

  p.setPen(QColor(0xff, 0xff, 0xff, alpha));
  p.drawText(real_rect.x(), real_rect.bottom(), text);
}

void OnroadHud::drawTextColor(QPainter &p, int x, int y, const QString &text, const QColor &color) {
  QFontMetrics fm(p.font());
  QRect init_rect = fm.boundingRect(text);
  QRect real_rect = fm.boundingRect(init_rect, 0, text);
  real_rect.moveCenter({x, y - real_rect.height() / 2});
  p.setPen(color);
  p.drawText(real_rect.x(), real_rect.bottom(), text);
}

//-------------------------------------------------------------------------------------------
// NvgWindow

NvgWindow::NvgWindow(VisionStreamType type, QWidget* parent) : fps_filter(UI_FREQ, 3, 1. / UI_FREQ), CameraViewWidget("camerad", type, true, parent) {

}

void NvgWindow::initializeGL() {
  CameraViewWidget::initializeGL();
  qInfo() << "OpenGL version:" << QString((const char*)glGetString(GL_VERSION));
  qInfo() << "OpenGL vendor:" << QString((const char*)glGetString(GL_VENDOR));
  qInfo() << "OpenGL renderer:" << QString((const char*)glGetString(GL_RENDERER));
  qInfo() << "OpenGL language version:" << QString((const char*)glGetString(GL_SHADING_LANGUAGE_VERSION));

  prev_draw_t = millis_since_boot();
  setBackgroundColor(bg_colors[STATUS_DISENGAGED]);
}

void NvgWindow::updateFrameMat(int w, int h) {
  CameraViewWidget::updateFrameMat(w, h);

  UIState *s = uiState();
  s->fb_w = w;
  s->fb_h = h;
  auto intrinsic_matrix = s->wide_camera ? ecam_intrinsic_matrix : fcam_intrinsic_matrix;
  float zoom = ZOOM / intrinsic_matrix.v[0];
  if (s->wide_camera) {
    zoom *= 0.5;
  }
  // Apply transformation such that video pixel coordinates match video
  // 1) Put (0, 0) in the middle of the video
  // 2) Apply same scaling as video
  // 3) Put (0, 0) in top left corner of video
  s->car_space_transform.reset();
  s->car_space_transform.translate(w / 2, h / 2 + y_offset)
      .scale(zoom, zoom)
      .translate(-intrinsic_matrix.v[2], -intrinsic_matrix.v[5]);
}

void NvgWindow::drawLaneLines(QPainter &painter, const UIState *s) {
  const UIScene &scene = s->scene;
  // lanelines
  for (int i = 0; i < std::size(scene.lane_line_vertices); ++i) {
    painter.setBrush(QColor::fromRgbF(1.0, 1.0, 1.0, std::clamp<float>(scene.lane_line_probs[i], 0.0, 0.7)));
    painter.drawPolygon(scene.lane_line_vertices[i].v, scene.lane_line_vertices[i].cnt);
  }

  // road edges
  for (int i = 0; i < std::size(scene.road_edge_vertices); ++i) {
    painter.setBrush(QColor::fromRgbF(1.0, 0, 0, std::clamp<float>(1.0 - scene.road_edge_stds[i], 0.0, 1.0)));
    painter.drawPolygon(scene.road_edge_vertices[i].v, scene.road_edge_vertices[i].cnt);
  }

  // paint path
  QLinearGradient bg(0, height(), 0, height() / 4);
  // wirelessnet2's rainbow barf path
  if (scene.engaged && !scene.end_to_end) {
    // openpilot is not disengaged
    if (scene.steeringPressed) {
      // The user is applying torque to the steering wheel
      bg.setColorAt(0, steeringpressedColor(200));
      bg.setColorAt(1, QColor(0, 95, 128, 50));
    } else if (scene.override) {
      bg.setColorAt(0, overrideColor(200));
      bg.setColorAt(1, QColor(72, 77, 74, 50));
    } else {
      // Draw colored track
      int torqueScale = (int)std::fabs(510 * (float)scene.output_scale);
      int red_lvl = std::fmin(255, torqueScale);
      int green_lvl = std::fmin(255, 510 - torqueScale);
      bg.setColorAt(0, QColor(red_lvl, green_lvl, 0, 200));
      bg.setColorAt(1, QColor((int)(0.5 * red_lvl), (int)(0.5 * green_lvl), 0, 50));
    }
  } else if (scene.engaged && scene.end_to_end) {
    const auto &orientation = (*s->sm)["modelV2"].getModelV2().getOrientation();
    float orientation_future = 0;
    if (orientation.getZ().size() > 16) {
      orientation_future = std::abs(orientation.getZ()[16]);  // 2.5 seconds
    }
    // straight: 112, in turns: 70
    float curve_hue = fmax(70, 112 - (orientation_future * 420));
    // FIXME: painter.drawPolygon can be slow if hue is not rounded
    curve_hue = int(curve_hue * 100 + 0.5) / 100;

    if (scene.steeringPressed) {
      // The user is applying torque to the steering wheel
      bg.setColorAt(0, steeringpressedColor(200));
      bg.setColorAt(1, QColor(0, 95, 128, 50));
    } else if (scene.override) {
      bg.setColorAt(0, overrideColor(200));
      bg.setColorAt(1, QColor(72, 77, 74, 50));
    } else {
      bg.setColorAt(0.0, QColor::fromHslF(148 / 360., 0.94, 0.51, 0.4));
      bg.setColorAt(0.75 / 1.5, QColor::fromHslF(curve_hue / 360., 1.0, 0.68, 0.35));
      bg.setColorAt(1.0, QColor::fromHslF(curve_hue / 360., 1.0, 0.68, 0.0));
    }
  } else {
    bg.setColorAt(0, whiteColor(200));
    bg.setColorAt(1, whiteColor(0));
  }
  painter.setBrush(bg);
  painter.drawPolygon(scene.track_vertices.v, scene.track_vertices.cnt);
}

void NvgWindow::drawLead(QPainter &painter, const cereal::ModelDataV2::LeadDataV3::Reader &lead_data, const QPointF &vd, bool is_radar) {
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
  painter.setBrush(is_radar ? light_orangeColor() : golden_yellowColor());
  painter.drawPolygon(glow, std::size(glow));

  // chevron
  QPointF chevron[] = {{x + (sz * 1.25), y + sz}, {x, y}, {x - (sz * 1.25), y + sz}};
  painter.setBrush(redColor(fillAlpha));
  painter.drawPolygon(chevron, std::size(chevron));
}

void NvgWindow::paintGL() {
  CameraViewWidget::paintGL();

  UIState *s = uiState();
  if (s->worldObjectsVisible()) {
    QPainter painter(this);
    painter.setRenderHint(QPainter::Antialiasing);
    painter.setPen(Qt::NoPen);

    drawLaneLines(painter, s);

    auto leads = (*s->sm)["modelV2"].getModelV2().getLeadsV3();
    if (leads[0].getProb() > .5) {
      drawLead(painter, leads[0], s->scene.lead_vertices[0], s->scene.lead_radar[0]);
    }
    if (leads[1].getProb() > .5 && (std::abs(leads[1].getX()[0] - leads[0].getX()[0]) > 3.0)) {
      drawLead(painter, leads[1], s->scene.lead_vertices[1], s->scene.lead_radar[1]);
    }
  }

  double cur_draw_t = millis_since_boot();
  double dt = cur_draw_t - prev_draw_t;
  double fps = fps_filter.update(1. / dt * 1000);
  if (fps < 15) {
    LOGW("slow frame rate: %.2f fps", fps);
  }
  prev_draw_t = cur_draw_t;
}

void NvgWindow::showEvent(QShowEvent *event) {
  CameraViewWidget::showEvent(event);

  ui_update_params(uiState());
  prev_draw_t = millis_since_boot();
}
