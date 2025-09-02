#include "selfdrive/ui/qt/sidebar.h"

#include <QMouseEvent>

#include "selfdrive/ui/qt/util.h"

#include <QProcess>
#include <QTimer>

void Sidebar::drawMetric(QPainter &p, const QPair<QString, QString> &label, QColor c, int y) {
  const QRect rect = {30, y, 240, 126};

  p.setPen(Qt::NoPen);
  p.setBrush(QBrush(c));
  p.setClipRect(rect.x() + 4, rect.y(), 18, rect.height(), Qt::ClipOperation::ReplaceClip);
  p.drawRoundedRect(QRect(rect.x() + 4, rect.y() + 4, 100, 118), 18, 18);
  p.setClipping(false);

  QPen pen = QPen(QColor(0xff, 0xff, 0xff, 0x55));
  pen.setWidth(2);
  p.setPen(pen);
  p.setBrush(Qt::NoBrush);
  p.drawRoundedRect(rect, 20, 20);

  p.setPen(QColor(0xff, 0xff, 0xff));
  p.setFont(InterFont(35, QFont::DemiBold));
  p.drawText(rect.adjusted(22, 0, 0, 0), Qt::AlignCenter, label.first + "\n" + label.second);
}

Sidebar::Sidebar(QWidget *parent) : QFrame(parent), onroad(false), flag_pressed(false), settings_pressed(false), mic_indicator_pressed(false), scene(uiState()->scene), commit_check_done(false) {
  home_img = loadPixmap("../assets/images/button_home.png", home_btn.size());
  flag_img = loadPixmap("../assets/images/button_flag.png", home_btn.size());
  settings_img = loadPixmap("../assets/images/button_settings.png", settings_btn.size(), Qt::IgnoreAspectRatio);
  mic_img = loadPixmap("../assets/icons/microphone.png", QSize(30, 30));
  link_img = loadPixmap("../assets/icons/link.png", QSize(60, 60));
  c3x_img = loadPixmap("../assets/icons/c3x.png", home_btn.size());

  connect(this, &Sidebar::valueChanged, [=] { update(); });

  setAttribute(Qt::WA_OpaquePaintEvent);
  setSizePolicy(QSizePolicy::Fixed, QSizePolicy::Expanding);
  setFixedWidth(300);

  QObject::connect(uiState(), &UIState::uiUpdate, this, &Sidebar::updateState);
  QObject::connect(this, &Sidebar::commitCheckFinished, this, &Sidebar::onCommitCheckFinished);

  pm = std::make_unique<PubMaster>(std::vector<const char*>{"bookmarkButton"});
}

void Sidebar::mousePressEvent(QMouseEvent *event) {
  if (onroad && home_btn.contains(event->pos())) {
    flag_pressed = true;
    update();
  } else if (settings_btn.contains(event->pos())) {
    settings_pressed = true;
    update();
  } else if (recording_audio && mic_indicator_btn.contains(event->pos())) {
    mic_indicator_pressed = true;
    update();
  } else if (commit_rect.contains(event->pos())) {
    commit_pressed = true;
    update();
  }
}

void Sidebar::mouseReleaseEvent(QMouseEvent *event) {
  if (flag_pressed || settings_pressed || mic_indicator_pressed || commit_pressed) {
    flag_pressed = settings_pressed = mic_indicator_pressed = commit_pressed = false;
    update();
  }
  if (onroad && home_btn.contains(event->pos())) {
    //MessageBuilder msg;
    //msg.initEvent().initBookmarkButton();
    //pm->send("bookmarkButton", msg);
    params.remove("CalibrationParams");
    params.remove("LiveTorqueParameters");
    params.remove("LiveParameters");
    params.remove("LiveParametersV2");
    params.remove("LiveDelay");
    params.putBool("OnroadCycleRequested", true);
  } else if (settings_btn.contains(event->pos())) {
    emit openSettings();
  } else if (recording_audio && mic_indicator_btn.contains(event->pos())) {
    emit openSettings(2, "RecordAudio");
  } else if (commit_rect.contains(event->pos())) {
    startCommitCheck();
  }
}

void Sidebar::offroadTransition(bool offroad) {
  onroad = !offroad;
  update();
}

void Sidebar::startCommitCheck() {
  if (commit_process) {
    return;
  }

  setProperty("commitStatus", QVariant::fromValue(ItemStatus{{tr("CHECKING..."), tr("")}, warning_color}));
  commit_process = new QProcess(this);
  connect(commit_process, QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished),
          this, &Sidebar::onCommitCheckFinished);
  commit_process->start("sh", QStringList{"/data/openpilot/scripts/commit_compare.sh"});
}

void Sidebar::onCommitCheckFinished(int exitCode, QProcess::ExitStatus exitStatus) {
  if (commit_process) {
    if (exitStatus == QProcess::NormalExit && exitCode == 0) {
      QString commit_compare_raw = QString(commit_process->readAllStandardOutput()).trimmed();
      QColor commit_color = warning_color;
      QString remote_commit = "--";
      QString local_commit = "--";

      if (!commit_compare_raw.isEmpty()) {
        QStringList parts = commit_compare_raw.split(" ");
        if (parts.size() >= 3) {
          local_commit = parts[0].remove("\"");
          remote_commit = parts[parts.size()-1].remove("\"");

          if (commit_compare_raw.contains("!=")) {
            commit_color = danger_color;
          } else if (commit_compare_raw.contains("==")) {
            commit_color = QColor(0x80, 0xd8, 0xa6);
          }
        }
      }
      ItemStatus newStatus = {{remote_commit, local_commit}, commit_color};
      setProperty("commitStatus", QVariant::fromValue(newStatus));
    } else {
      setProperty("commitStatus", QVariant::fromValue(ItemStatus{{tr("ERROR"), tr("CHECK")}, danger_color}));
    }
  }

  if (commit_process) {
    commit_process->deleteLater();
    commit_process = nullptr;
  }

  update();
}

void Sidebar::updateState(const UIState &s) {
  if (!isVisible()) return;

  auto &sm = *(s.sm);

  networking = networking ? networking : window()->findChild<Networking *>("");
  bool tethering_on = networking && networking->wifi->tethering_on;
  auto deviceState = sm["deviceState"].getDeviceState();
  setProperty("netType", tethering_on ? "Hotspot": network_type[deviceState.getNetworkType()]);
  int strength = tethering_on ? 4 : (int)deviceState.getNetworkStrength();
  setProperty("netStrength", strength > 0 ? strength + 1 : 0);

  if (strength > 0 && !commit_check_done) {
    QString commit_compare = QString("%1").arg(QString::fromStdString(params.get("CommitCompare")));
    if (commit_compare.isEmpty()) {
      startCommitCheck();
      commit_check_done = true;
    }
  }

  if (strength == 0) {
    commit_check_done = false;
  }

  QString commit_compare_raw = QString::fromStdString(params.get("CommitCompare"));
  QColor commit_color = warning_color;
  QString remote_commit = "--";
  QString local_commit = "--";

  if (!commit_compare_raw.isEmpty()) {
    QStringList parts = commit_compare_raw.split(" ");

    if (parts.size() >= 3) {
      local_commit = parts[0].remove("\"");
      remote_commit = parts[parts.size()-1].remove("\"");

      if (commit_compare_raw.contains("!=")) {
        commit_color = danger_color;
      } else if (commit_compare_raw.contains("==")) {
        commit_color = QColor(0x80, 0xd8, 0xa6);
      }
    }
  }

  ItemStatus commitStatus = {{remote_commit, local_commit}, commit_color};
  setProperty("commitStatus", QVariant::fromValue(commitStatus));

  ItemStatus connectStatus;
  auto last_ping = deviceState.getLastAthenaPingTime();
  if (last_ping == 0) {
    connectStatus = ItemStatus{{tr("CONNECT"), tr("OFFLINE")}, warning_color};
  } else {
    connectStatus = nanos_since_boot() - last_ping < 80e9
                        ? ItemStatus{{tr("CONNECT"), tr("ONLINE")}, good_color}
                        : ItemStatus{{tr("CONNECT"), tr("ERROR")}, danger_color};
  }
  setProperty("connectStatus", QVariant::fromValue(connectStatus));

  int maxTempC = deviceState.getMaxTempC();
  QString max_temp = QString::number(maxTempC) + "°C";
  //ItemStatus tempStatus = {{tr("TEMP"), tr("HIGH")}, danger_color};
  ItemStatus tempStatus = {{tr("TEMP"), max_temp}, danger_color};
  auto ts = deviceState.getThermalStatus();
  if (ts == cereal::DeviceState::ThermalStatus::GREEN) {
    //tempStatus = {{tr("TEMP"), tr("GOOD")}, good_color};
    tempStatus = {{tr("TEMP"), max_temp}, good_color};
  } else if (ts == cereal::DeviceState::ThermalStatus::YELLOW) {
    //tempStatus = {{tr("TEMP"), tr("OK")}, warning_color};
    tempStatus = {{tr("TEMP"), max_temp}, warning_color};
  }
  setProperty("tempStatus", QVariant::fromValue(tempStatus));

  ItemStatus pandaStatus = {{tr("VEHICLE"), tr("ONLINE")}, good_color};
  if (s.scene.pandaType == cereal::PandaState::PandaType::UNKNOWN) {
    pandaStatus = {{tr("NO"), tr("PANDA")}, danger_color};
  }
  setProperty("pandaStatus", QVariant::fromValue(pandaStatus));

  setProperty("recordingAudio", s.scene.recording_audio);
}

void Sidebar::paintEvent(QPaintEvent *event) {
  QPainter p(this);
  p.setPen(Qt::NoPen);
  p.setRenderHint(QPainter::Antialiasing);

  p.fillRect(rect(), QColor(57, 57, 57));

  QString c3x_position_raw = QString::fromStdString(params.get("DevicePosition"));
  QString c3x_position = c3x_position_raw.isEmpty() ? "--" : c3x_position_raw;

  // buttons
  p.setOpacity(settings_pressed ? 0.65 : 1.0);
  p.drawPixmap(settings_btn.x(), settings_btn.y(), settings_img);
  p.setOpacity(onroad && flag_pressed ? 0.65 : 1.0);
  //p.drawPixmap(home_btn.x(), home_btn.y(), onroad ? flag_img : home_img);
  if (recording_audio) {
    p.setBrush(danger_color);
    p.setOpacity(mic_indicator_pressed ? 0.65 : 1.0);
    p.drawRoundedRect(mic_indicator_btn, mic_indicator_btn.height() / 2, mic_indicator_btn.height() / 2);
    int icon_x = mic_indicator_btn.x() + (mic_indicator_btn.width() - mic_img.width()) / 2;
    int icon_y = mic_indicator_btn.y() + (mic_indicator_btn.height() - mic_img.height()) / 2;
    p.drawPixmap(icon_x, icon_y, mic_img);
  }

  p.drawPixmap(home_btn.x(), home_btn.y(), c3x_img);

  const QRect r3 = QRect(0, 1020, event->rect().width(), 40);

  p.setFont(InterFont(30, QFont::DemiBold));
  p.setPen(QColor(0xff, 0xff, 0xff));
  p.drawText(r3, Qt::AlignCenter, c3x_position);

  p.setOpacity(1.0);

  // network
  int x = 58;
  const QColor gray(0x54, 0x54, 0x54);
  for (int i = 0; i < 5; ++i) {
    p.setBrush(i < net_strength ? Qt::white : gray);
    p.drawEllipse(x, 196, 27, 27);
    x += 37;
  }

  p.setFont(InterFont(30));
  p.setPen(QColor(0xff, 0xff, 0xff));

  const QRect r = QRect(58, 237, width() - 100, 50);
  const QRect r2 = QRect(0, 237, event->rect().width(), 50);

  if (net_type == "Hotspot") {
    p.drawPixmap(r.x(), r.y() + (r.height() - link_img.height()) / 2, link_img);
  } else if (net_type == network_type[cereal::DeviceState::NetworkType::WIFI]) {
    p.drawText(r2, Qt::AlignCenter, uiState()->wifi->getIp4Address());
  } else {
    p.drawText(r, Qt::AlignLeft | Qt::AlignVCenter, net_type);
  }

  // metrics
  p.setFont(InterFont(35));
  drawMetric(p, temp_status.first, temp_status.second, 338 - 50);
  drawMetric(p, panda_status.first, panda_status.second, 496 - 50);
  drawMetric(p, connect_status.first, connect_status.second, 654 - 50);

  p.setOpacity(commit_pressed ? 0.65 : 1.0);
  drawMetric(p, commit_status.first, commit_status.second, 812 - 50);
  p.setOpacity(1.0);
}
