#include "selfdrive/ui/qt/sidebar.h"

#include <QMouseEvent>

#include "selfdrive/common/util.h"
#include "selfdrive/hardware/hw.h"
#include "selfdrive/ui/qt/util.h"

void Sidebar::drawMetric(QPainter &p, const QString &label, QColor c, int y) {
  const QRect rect = {30, y, 240, label.contains("\n") ? 124 : 100};

  p.setPen(Qt::NoPen);
  p.setBrush(QBrush(c));
  p.setClipRect(rect.x() + 6, rect.y(), 18, rect.height(), Qt::ClipOperation::ReplaceClip);
  p.drawRoundedRect(QRect(rect.x() + 6, rect.y() + 6, 100, rect.height() - 12), 10, 10);
  p.setClipping(false);

  QPen pen = QPen(QColor(0xff, 0xff, 0xff, 0x55));
  pen.setWidth(2);
  p.setPen(pen);
  p.setBrush(Qt::NoBrush);
  p.drawRoundedRect(rect, 20, 20);

  p.setPen(QColor(0xff, 0xff, 0xff));
  configFont(p, "Open Sans", 35, "Bold");
  const QRect r = QRect(rect.x() + 30, rect.y(), rect.width() - 40, rect.height());
  p.drawText(r, Qt::AlignCenter, label);
}

Sidebar::Sidebar(QWidget *parent) : QFrame(parent) {
  home_img = QImage("../assets/offroad/icon_openpilot.png").scaled(180, 180, Qt::KeepAspectRatio, Qt::SmoothTransformation);
  settings_img = QImage("../assets/images/button_settings.png").scaled(settings_btn.width(), settings_btn.height(), Qt::IgnoreAspectRatio, Qt::SmoothTransformation);

  connect(this, &Sidebar::valueChanged, [=] { update(); });

  setAttribute(Qt::WA_OpaquePaintEvent);
  setSizePolicy(QSizePolicy::Fixed, QSizePolicy::Expanding);
  setFixedWidth(300);
}

void Sidebar::mouseReleaseEvent(QMouseEvent *event) {
  if (settings_btn.contains(event->pos())) {
    emit openSettings();
  }
}

void Sidebar::updateState(const UIState &s) {
  auto &sm = *(s.sm);

  auto deviceState = sm["deviceState"].getDeviceState();
  setProperty("netType", network_type[deviceState.getNetworkType()]);
  int strength = (int)deviceState.getNetworkStrength();
  setProperty("netStrength", strength > 0 ? strength + 1 : 0);
  setProperty("wifiAddr", deviceState.getWifiIpAddress().cStr());

  ItemStatus connectStatus;
  auto last_ping = deviceState.getLastAthenaPingTime();
  if (last_ping == 0) {
    connectStatus = params.getBool("PrimeRedirected") ? ItemStatus{"NO\nPRIME", danger_color} : ItemStatus{"CONNECT\n오프라인", warning_color};
  } else {
    connectStatus = nanos_since_boot() - last_ping < 80e9 ? ItemStatus{"CONNECT\n온라인", good_color} : ItemStatus{"CONNECT\n오류", danger_color};
  }
  setProperty("connectStatus", QVariant::fromValue(connectStatus));

  //ItemStatus tempStatus = {"TEMP\nHIGH", danger_color};
  ItemStatus tempStatus = {"장치온도\nHIGH", danger_color};
  auto ts = deviceState.getThermalStatus();
  if (ts == cereal::DeviceState::ThermalStatus::GREEN) {
    tempStatus = {"장치온도\nGOOD", good_color};
  } else if (ts == cereal::DeviceState::ThermalStatus::YELLOW) {
    tempStatus = {"장치온도\nOK", warning_color};
  }
  setProperty("tempStatus", QVariant::fromValue(tempStatus));

  ItemStatus pandaStatus = {"차량\n연결됨", good_color};
  if (s.scene.pandaType == cereal::PandaState::PandaType::UNKNOWN) {
    pandaStatus = {"차량\n연결안됨", danger_color};
  } /*else if (s.scene.started && !sm["liveLocationKalman"].getLiveLocationKalman().getGpsOK()) {
    pandaStatus = {"GPS\nSEARCHING", warning_color};
  }*/
  setProperty("pandaStatus", QVariant::fromValue(pandaStatus));
  m_battery_img = s.scene.device_state.getBatteryStatus() == "Charging" ? 1 : 0;
  m_batteryPercent = s.scene.device_state.getBatteryPercent();
}

void Sidebar::paintEvent(QPaintEvent *event) {
  QPainter p(this);
  p.setPen(Qt::NoPen);
  p.setRenderHint(QPainter::Antialiasing);

  p.fillRect(rect(), QColor(57, 57, 57));

  // static imgs
  p.setOpacity(0.65);
  p.drawImage(settings_btn.x(), settings_btn.y(), settings_img);
  p.setOpacity(1.0);
  p.drawImage(60, 1080 - 180 - 40, home_img);

#ifndef QCOM
  // network
  int x = 58;
  const QColor gray(0x54, 0x54, 0x54);
  for (int i = 0; i < 5; ++i) {
    p.setBrush(i < net_strength ? Qt::white : gray);
    p.drawEllipse(x, 196, 27, 27);
    x += 37;
  }
#endif

#ifdef QCOM
  // battery status
  p.drawImage(68, 180, battery_imgs[m_battery_img]); // signal_imgs to battery_imgs
  configFont(p, "Open Sans", 32, "Bold");
  p.setPen(QColor(0x00, 0x00, 0x00));
  const QRect r = QRect(80, 193, 100, 50);
  char battery_str[5];
  snprintf(battery_str, sizeof(battery_str), "%d%%", m_batteryPercent);
  p.drawText(r, Qt::AlignCenter, battery_str);
#endif

  configFont(p, "Open Sans", 32, "Bold");
  p.setPen(QColor(0xff, 0xff, 0xff));
  const QRect r2 = QRect(0, 267, event->rect().width(), 50);

  if (net_type == network_type[cereal::DeviceState::NetworkType::WIFI])
    p.drawText(r2, Qt::AlignCenter, wifi_addr);
  else
    p.drawText(r2, Qt::AlignCenter, net_type);

  // metrics
  configFont(p, "Open Sans", 35, "Regular");
  drawMetric(p, temp_status.first, temp_status.second, 338);
  drawMetric(p, panda_status.first, panda_status.second, 496);
  drawMetric(p, connect_status.first, connect_status.second, 654);
}
