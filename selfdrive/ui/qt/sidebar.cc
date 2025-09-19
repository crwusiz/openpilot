#include "selfdrive/ui/qt/sidebar.h"

#include <QMouseEvent>

#include "selfdrive/ui/qt/util.h"

#include <QProcess>
#include <QTimer>
#include <QFile>
#include <QTextStream>
#include <QFileSystemWatcher>
#include <QDir>
#include <QFileInfo>

#include "common/watchdog.h"

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

Sidebar::Sidebar(QWidget *parent)
  : QFrame(parent),
    onroad(false),
    flag_pressed(false),
    settings_pressed(false),
    mic_indicator_pressed(false),
    scene(uiState()->scene),
    is_update_available(false),
    is_processing(false) {

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

  setupWatchdogTimer();

  startCommitCheckDetached();

  pm = std::make_unique<PubMaster>(std::vector<const char*>{"bookmarkButton"});
}

Sidebar::~Sidebar() {
  if (watchdog_timer) {
    watchdog_timer->stop();
    watchdog_timer->deleteLater();
  }

  cleanupTimers();
  if (file_watcher) {
    file_watcher->deleteLater();
  }
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
  } else if (commit_btn.contains(event->pos())) {
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
  } else if (commit_btn.contains(event->pos())) {
    handleCommitButtonPress();
  }
}

void Sidebar::handleCommitButtonPress() {
  if (is_processing) {
    qWarning() << "Script execution already in progress, ignoring click";
    ItemStatus processingStatus = {{tr("BUSY"), tr("WAIT")}, warning_color};
    setProperty("commitStatus", QVariant::fromValue(processingStatus));
    update();
    return;
  }

  if (is_update_available) {
    startGitPullDetached();
  } else {
    startCommitCheckDetached();
  }
}

void Sidebar::startGitPullDetached() {
  is_processing = true;

  ItemStatus processingStatus = {{tr("git pull"), tr("STARTING")}, warning_color};
  setProperty("commitStatus", QVariant::fromValue(processingStatus));
  update();

  cleanupTimers();
  ensureWatchdogActive();

  QFile::remove("/data/gitpull_exit_code.log");

  ItemStatus progressStatus = {{tr("git pull"), tr("Progress ")}, warning_color};
  setProperty("commitStatus", QVariant::fromValue(progressStatus));
  update();

  bool started = QProcess::startDetached("sh",
                                       QStringList{"/data/openpilot/scripts/gitpull.sh"});

  if (!started) {
    qCritical() << "Failed to start git pull script";
    onGitPullFailed(tr("FAILED TO START"));
    return;
  }

  qDebug() << "Git pull script started successfully (detached)";

  setupFileWatcher("/data/gitpull_exit_code.log",
                   [this](){ this->onGitPullFileChanged(); });

  if (!git_pull_timer) {
    git_pull_timer = new QTimer(this);
    git_pull_timer->setInterval(1000);
    connect(git_pull_timer, &QTimer::timeout, this, &Sidebar::checkGitPullStatus);
  }

  git_pull_timer->start();
}

void Sidebar::checkGitPullStatus() {
  if (!is_processing) {
    if (git_pull_timer) {
      git_pull_timer->stop();
    }
    return;
  }

  kickWatchdog();

  static int dots = 0;
  dots = (dots + 1) % 4;
  QString dotStr = QString(".").repeated(dots);
  ItemStatus progressStatus = {{tr("git pull"), tr("RUNNING") + dotStr}, warning_color};
  setProperty("commitStatus", QVariant::fromValue(progressStatus));
  update();

  onGitPullFileChanged();
}

void Sidebar::onGitPullFileChanged() {
  QFile file("/data/gitpull_exit_code.log");
  if (!file.exists()) {
    return;
  }

  if (git_pull_timer) {
    git_pull_timer->stop();
  }

  if (file.open(QIODevice::ReadOnly | QIODevice::Text)) {
    QTextStream in(&file);
    QString exitCodeStr = in.readLine().trimmed();
    file.close();

    bool ok;
    //int exitCode = exitCodeStr.toInt(&ok);

    if (!ok) {
      qWarning() << "Invalid exit code format:" << exitCodeStr;
      onGitPullFailed(tr("INVALID EXIT CODE"));
    }

    file.remove();
  } else {
    qWarning() << "Could not read git pull exit code file";
    onGitPullFailed(tr("FILE READ ERROR"));
  }
}

void Sidebar::onGitPullFailed(const QString &reason) {
  is_processing = false;
  cleanupTimers();

  qCritical() << "Git pull failed:" << reason;
  ItemStatus failStatus = {{tr("git pull"), reason}, danger_color};
  setProperty("commitStatus", QVariant::fromValue(failStatus));
  update();
}

void Sidebar::startCommitCheckDetached() {
  if (is_processing) {
    qDebug() << "Already processing, skipping commit check";
    return;
  }

  is_processing = true;
  cleanupTimers();

  ItemStatus checkingStatus = {{tr("commit"), tr("CHECKING")}, warning_color};
  setProperty("commitStatus", QVariant::fromValue(checkingStatus));
  update();

  QFile::remove("/data/commit_check_exit_code.log");

  bool started = QProcess::startDetached("sh",
                                       QStringList{"/data/openpilot/scripts/commit_compare.sh"});

  if (!started) {
    qCritical() << "Failed to start commit check script";
    onCommitCheckFailed(tr("FAILED TO START"));
    return;
  }

  setupFileWatcher("/data/commit_check_exit_code.log",
                   [this](){ this->onCommitCheckFileChanged(); });

  if (!commit_check_timer) {
    commit_check_timer = new QTimer(this);
    commit_check_timer->setInterval(1000);
    connect(commit_check_timer, &QTimer::timeout, this, &Sidebar::checkCommitCheckStatus);
  }
  commit_check_timer->start();

  QTimer::singleShot(15000, this, [this]() {
    if (is_processing && commit_check_timer && commit_check_timer->isActive()) {
      onCommitCheckFailed(tr("TIMEOUT"));
    }
  });
}

void Sidebar::checkCommitCheckStatus() {
  if (!is_processing) {
    if (commit_check_timer) {
      commit_check_timer->stop();
    }
    return;
  }

  kickWatchdog();

  static int dots = 0;
  dots = (dots + 1) % 4;
  QString dotStr = QString(".").repeated(dots);
  ItemStatus progressStatus = {{tr("commit"), tr("CHECKING") + dotStr}, warning_color};
  setProperty("commitStatus", QVariant::fromValue(progressStatus));
  update();

  onCommitCheckFileChanged();
}

void Sidebar::onCommitCheckFileChanged() {
  QFile file("/data/commit_check_exit_code.log");
  if (!file.exists()) {
    return;
  }

  if (commit_check_timer) {
    commit_check_timer->stop();
  }

  is_processing = false;
  cleanupTimers();

  if (file.open(QIODevice::ReadOnly | QIODevice::Text)) {
    QTextStream in(&file);
    QString exitCodeStr = in.readLine().trimmed();
    file.close();
    file.remove();

    bool ok;
    int exitCode = exitCodeStr.toInt(&ok);

    if (ok && exitCode == 0) {
      QString output = QString::fromStdString(params.get("CommitCompare"));
      parseCommitCompareResult(output);
    } else {
      qWarning() << "Commit check failed with exit code:" << exitCodeStr;
      onCommitCheckFailed(tr("CHECK FAILED"));
    }
  } else {
    onCommitCheckFailed(tr("FILE READ ERROR"));
  }

  update();
}

void Sidebar::onCommitCheckFailed(const QString &reason) {
  is_processing = false;
  cleanupTimers();

  qWarning() << "Commit check failed:" << reason;
  ItemStatus errorStatus = {{tr("CHECK"), reason}, danger_color};
  setProperty("commitStatus", QVariant::fromValue(errorStatus));
  is_update_available = false;
  update();
}

void Sidebar::setupFileWatcher(const QString &filePath, std::function<void()> callback) {
  if (!file_watcher) {
    file_watcher = new QFileSystemWatcher(this);
  }

  if (!file_watcher->files().isEmpty()) {
    file_watcher->removePaths(file_watcher->files());
  }
  if (!file_watcher->directories().isEmpty()) {
    file_watcher->removePaths(file_watcher->directories());
  }

  QString dirPath = QFileInfo(filePath).absolutePath();
  if (QDir(dirPath).exists()) {
    file_watcher->addPath(dirPath);

    disconnect(file_watcher, &QFileSystemWatcher::directoryChanged, nullptr, nullptr);
    connect(file_watcher, &QFileSystemWatcher::directoryChanged,
            this, [filePath, callback](const QString &path) {
      Q_UNUSED(path);
      if (QFile::exists(filePath)) {
        callback();
      }
    });
  }
}

void Sidebar::parseCommitCompareResult(const QString &output) {
  QString trimmed_output = output.trimmed();
  if (trimmed_output.isEmpty()) {
    onCommitCheckFailed(tr("EMPTY RESULT"));
    return;
  }

  if (trimmed_output.startsWith('"') && trimmed_output.endsWith('"')) {
    trimmed_output = trimmed_output.mid(1, trimmed_output.length() - 2);
  }

  QStringList parts;
  QString operator_symbol;

  if (trimmed_output.contains(" == ")) {
    parts = trimmed_output.split(" == ");
    operator_symbol = "==";
  } else if (trimmed_output.contains(" != ")) {
    parts = trimmed_output.split(" != ");
    operator_symbol = "!=";
  } else {
    onCommitCheckFailed(tr("PARSE ERROR"));
    return;
  }

  if (parts.size() == 2) {
    QString local_commit = parts[0].trimmed().remove('"');
    QString remote_commit = parts[1].trimmed().remove('"');

    if (operator_symbol == "==") {
      commit_status = {{tr("UP TO DATE"), local_commit}, QColor(0x80, 0xd8, 0xa6)};
      is_update_available = false;
    } else {
      commit_status = {{local_commit, remote_commit}, danger_color};
      is_update_available = true;
    }
  } else {
    onCommitCheckFailed(tr("INVALID FORMAT"));
    return;
  }

  setProperty("commitStatus", QVariant::fromValue(commit_status));
}

void Sidebar::cleanupTimers() {
  if (commit_check_timer) {
    commit_check_timer->stop();
  }

  if (git_pull_timer) {
    git_pull_timer->stop();
  }

  if (file_watcher) {
    if (!file_watcher->files().isEmpty()) {
      file_watcher->removePaths(file_watcher->files());
    }
    if (!file_watcher->directories().isEmpty()) {
      file_watcher->removePaths(file_watcher->directories());
    }
  }
}

void Sidebar::setupWatchdogTimer() {
  if (!watchdog_timer) {
    watchdog_timer = new QTimer(this);
    watchdog_timer->setInterval(3000);
    connect(watchdog_timer, &QTimer::timeout, this, &Sidebar::kickWatchdog);
  }
  watchdog_timer->start();
  qDebug() << "Watchdog timer started (5 second interval)";
}

void Sidebar::kickWatchdog() {
  uint64_t current_time = nanos_since_boot();

  if (!watchdog_kick(current_time)) {
    qWarning() << "Failed to kick watchdog at" << current_time;
  }
}

void Sidebar::ensureWatchdogActive() {
  if (!watchdog_timer || !watchdog_timer->isActive()) {
    qWarning() << "Watchdog timer was inactive, restarting...";
    setupWatchdogTimer();
  }

  kickWatchdog();
}

void Sidebar::offroadTransition(bool offroad) {
  onroad = !offroad;
  update();
}

void Sidebar::updateState(const UIState &s) {
  if (!isVisible()) {
    return;
  }

  auto &sm = *(s.sm);

  networking = networking ? networking : window()->findChild<Networking *>("");
  bool tethering_on = networking && networking->wifi->tethering_on;
  auto deviceState = sm["deviceState"].getDeviceState();
  setProperty("netType", tethering_on ? "Hotspot": network_type[deviceState.getNetworkType()]);
  int strength = tethering_on ? 4 : (int)deviceState.getNetworkStrength();
  setProperty("netStrength", strength > 0 ? strength + 1 : 0);

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
