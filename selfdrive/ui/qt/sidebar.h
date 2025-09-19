#pragma once

#include <memory>

#include <QFrame>
#include <QMap>

#include "selfdrive/ui/ui.h"
#include "selfdrive/ui/qt/network/networking.h"

#include <QFileSystemWatcher>
#include <functional>
#include <QTimer>

typedef QPair<QPair<QString, QString>, QColor> ItemStatus;
Q_DECLARE_METATYPE(ItemStatus);

class Sidebar : public QFrame {
  Q_OBJECT
  Q_PROPERTY(ItemStatus connectStatus MEMBER connect_status NOTIFY valueChanged);
  Q_PROPERTY(ItemStatus pandaStatus MEMBER panda_status NOTIFY valueChanged);
  Q_PROPERTY(ItemStatus tempStatus MEMBER temp_status NOTIFY valueChanged);
  Q_PROPERTY(QString netType MEMBER net_type NOTIFY valueChanged);
  Q_PROPERTY(int netStrength MEMBER net_strength NOTIFY valueChanged);
  Q_PROPERTY(bool recordingAudio MEMBER recording_audio NOTIFY valueChanged);
  Q_PROPERTY(ItemStatus commitStatus MEMBER commit_status NOTIFY valueChanged);

public:
  explicit Sidebar(QWidget* parent = 0);
  ~Sidebar();

signals:
  void openSettings(int index = 0, const QString &param = "");
  void valueChanged();
  void commitCheckFinished(const QString &result);

public slots:
  void offroadTransition(bool offroad);
  void updateState(const UIState &s);

private slots:
  void handleCommitButtonPress();
  void startGitPullDetached();
  void onGitPullFileChanged();
  void checkGitPullStatus();
  void handleGitPullCompletion(int exitCode);
  void onGitPullFailed(const QString &reason);
  void onGitPullTimeout();

  void startCommitCheckDetached();
  void onCommitCheckFileChanged();
  void checkCommitCheckStatus();
  void onCommitCheckFailed(const QString &reason);

  void setupFileWatcher(const QString &filePath, std::function<void()> callback);
  void setupGitPullPollingTimer();
  void parseCommitCompareResult(const QString &output);
  void cleanupTimers();

  void setupWatchdogTimer();
  void kickWatchdog();
  void ensureWatchdogActive();

protected:
  void paintEvent(QPaintEvent *event) override;
  void mousePressEvent(QMouseEvent *event) override;
  void mouseReleaseEvent(QMouseEvent *event) override;

private:
  void drawMetric(QPainter &p, const QPair<QString, QString> &label, QColor c, int y);

  QPixmap home_img, flag_img, settings_img, mic_img, link_img, c3x_img;
  bool onroad, recording_audio, flag_pressed, settings_pressed, mic_indicator_pressed;
  bool commit_pressed, is_update_available;

  bool is_processing = false;
  static const int CHECK_INTERVAL_MS = 1000;
  static const int MAX_WAIT_TIME_MS = 120000;

  QTimer *commit_check_timer = nullptr;
  QTimer *git_pull_timer = nullptr;
  QTimer *watchdog_timer = nullptr;
  QFileSystemWatcher *file_watcher = nullptr;

  const QMap<cereal::DeviceState::NetworkType, QString> network_type = {
    {cereal::DeviceState::NetworkType::NONE, tr("--")},
    {cereal::DeviceState::NetworkType::WIFI, tr("Wi-Fi")},
    {cereal::DeviceState::NetworkType::ETHERNET, tr("ETH")},
    {cereal::DeviceState::NetworkType::CELL2_G, tr("2G")},
    {cereal::DeviceState::NetworkType::CELL3_G, tr("3G")},
    {cereal::DeviceState::NetworkType::CELL4_G, tr("LTE")},
    {cereal::DeviceState::NetworkType::CELL5_G, tr("5G")}
  };

  const QRect home_btn = QRect(60, 910, 180, 180);
  const QRect settings_btn = QRect(50, 35, 200, 117);
  const QRect mic_indicator_btn = QRect(158, 252, 75, 40);
  const QColor good_color = QColor(255, 255, 255);
  const QColor warning_color = QColor(218, 202, 37);
  const QColor danger_color = QColor(201, 34, 49);
  const QRect commit_btn = QRect(30, 812, 240, 126);

  ItemStatus connect_status, panda_status, temp_status;
  QString net_type = "--";
  int net_strength = 0;

  std::unique_ptr<PubMaster> pm;
  Networking *networking = nullptr;
  UIScene &scene;
  Params params;

  ItemStatus commit_status = {{tr("UPDATE"), tr("CHECK")}, warning_color};
};
