#include "selfdrive/ui/qt/offroad/settings.h"

#include <cassert>
#include <string>

#include <QDebug>

#ifndef QCOM
#include "selfdrive/ui/qt/offroad/networking.h"
#endif

#ifdef ENABLE_MAPS
#include "selfdrive/ui/qt/maps/map_settings.h"
#endif

#include "selfdrive/common/params.h"
#include "selfdrive/common/util.h"
#include "selfdrive/hardware/hw.h"
#include "selfdrive/ui/qt/widgets/controls.h"
#include "selfdrive/ui/qt/widgets/input.h"
#include "selfdrive/ui/qt/widgets/scrollview.h"
#include "selfdrive/ui/qt/widgets/ssh_keys.h"
#include "selfdrive/ui/qt/widgets/toggle.h"
#include "selfdrive/ui/ui.h"
#include "selfdrive/ui/qt/util.h"
#include "selfdrive/ui/qt/qt_window.h"

#include <QComboBox>
#include <QAbstractItemView>
#include <QScroller>
#include <QListView>
#include <QListWidget>

TogglesPanel::TogglesPanel(SettingsWindow *parent) : ListWidget(parent) {
  // param, title, desc, icon
  std::vector<std::tuple<QString, QString, QString, QString>> toggles{
    {
      "OpenpilotEnabledToggle",
      //"Enable openpilot",
      //"Use the openpilot system for adaptive cruise control and lane keep driver assistance. Your attention is required at all times to use this feature. Changing this setting takes effect when the car is powered off.",
      "오픈파일럿 사용",
      "오픈파일럿을 사용하여 조향보조 기능을 사용합니다. 항상 핸들을 잡고 도로를 주시하세요.",
      "../assets/offroad/icon_openpilot.png",
    },
/*
    {
      "IsRHD",
      "Enable Right-Hand Drive",
      "Allow openpilot to obey left-hand traffic conventions and perform driver monitoring on right driver seat.",
      "../assets/offroad/icon_openpilot_mirrored.png",
    },
*/
    {
      "IsMetric",
      //"Use Metric System",
      //"Display speed in km/h instead of mph.",
      "미터법 사용",
      "주행속도 표시를 ㎞/h로 변경합니다",
      "../assets/offroad/icon_metric.png",
    },
    {
      "CommunityFeaturesToggle",
      //"Enable Community Features",
      //"Use features, such as community supported hardware, from the open source community that are not maintained or supported by comma.ai and have not been confirmed to meet the standard safety model. Be extra cautious when using these features",
      "커뮤니티 기능 사용",
      "커뮤니티기능은 comma.ai에서 공식적으로 지원하는 기능이 아니므로 사용시 주의하세요.",
      "../assets/offroad/icon_discord.png",
    },
    {
      "IsLdwEnabled",
      //"Enable Lane Departure Warnings",
      //"Receive alerts to steer back into the lane when your vehicle drifts over a detected lane line without a turn signal activated while driving over 31mph (50kph).",
      "차선이탈 경보 사용",
      "40㎞/h 이상의 속도로 주행시 방향지시등 조작없이 차선을 이탈하면 차선이탈경보를 보냅니다. (오픈파일럿 비활성상태에서만 사용됩니다)",
      "../assets/offroad/icon_ldws.png",
    },
    {
     "AutoLaneChangeEnabled",
     //"Enable AutoLaneChange",
     //"Operation of the turn signal at 60㎞/h speed will result in a short change of the vehicle",
     "자동 차선변경 사용",
     "60㎞/h 이상의 속도로 주행시 방향지시등을 작동하면 잠시후 자동차선변경을 수행합니다. 안전한 사용을위해 후측방감지기능이 있는 차량만 사용하시기바랍니다.",
     "../assets/offroad/icon_lca.png",
    },
    {
      "UploadRaw",
      //"Upload Raw Logs",
      //"Upload full logs and full resolution video by default while on Wi-Fi. If not enabled, individual logs can be marked for upload at useradmin.comma.ai.",
      "주행로그 업로드 사용",
      "주행로그를 업로드합니다. [ connect.comma.ai/useradmin ] 에서 확인할수있습니다",
      "../assets/offroad/icon_network.png",
    },
/*
    {
      "RecordFront",
      "Record and Upload Driver Camera",
      "Upload data from the driver facing camera and help improve the driver monitoring algorithm.",
      "../assets/offroad/icon_monitoring.png",
    },
*/
    {
      "EndToEndToggle",
      "\U0001f96c Disable use of lanelines (Alpha) \U0001f96c",
      "In this mode openpilot will ignore lanelines and just drive how it thinks a human would.",
      "../assets/offroad/icon_road.png",
    },
#ifdef ENABLE_MAPS
    {
      "NavSettingTime24h",
      "Show ETA in 24h format",
      "Use 24h format instead of am/pm",
      "../assets/offroad/icon_metric.png",
    },
#endif

  };
  Params params;
  if (params.getBool("DisableRadar_Allow")) {
    toggles.push_back({
      "DisableRadar",
      "openpilot Longitudinal Control",
      "openpilot will disable the car's radar and will take over control of gas and brakes. Warning: this disables AEB!",
      "../assets/offroad/icon_speed_limit.png",
    });
  }

  for (auto &[param, title, desc, icon] : toggles) {
    auto toggle = new ParamControl(param, title, desc, icon, this);
    bool locked = params.getBool((param + "Lock").toStdString());
    toggle->setEnabled(!locked);
    //if (!locked) {
    //  connect(parent, &SettingsWindow::offroadTransition, toggle, &ParamControl::setEnabled);
    //}
    addItem(toggle);
  }
}

DevicePanel::DevicePanel(SettingsWindow *parent) : ListWidget(parent) {
  Params params = Params();

  setSpacing(20);
  addItem(new LabelControl("Dongle ID", getDongleId().value_or("N/A")));
  addItem(new LabelControl("Serial", params.get("HardwareSerial").c_str()));

  // offroad-only buttons
  //auto dcamBtn = new ButtonControl("Driver Camera", "PREVIEW",
  //                                 "Preview the driver facing camera to help optimize device mounting position for best driver monitoring experience. (vehicle must be off)");
  auto dcamBtn = new ButtonControl("운전자 모니터링 카메라 미리보기", "실행",
                                   "운전자 모니터링 카메라를 미리보고 최적의 장착위치를 찾아보세요.");
  connect(dcamBtn, &ButtonControl::clicked, [=]() { emit showDriverView(); });
  addItem(dcamBtn);

  //auto resetCalibBtn = new ButtonControl("Reset Calibration", "RESET", " ");
  auto resetCalibBtn = new ButtonControl("캘리브레이션 초기화", "실행", " ");
  connect(resetCalibBtn, &ButtonControl::showDescription, this, &DevicePanel::updateCalibDescription);
  connect(resetCalibBtn, &ButtonControl::clicked, [&]() {
    //if (ConfirmationDialog::confirm("Are you sure you want to reset calibration?", this)) {
    if (ConfirmationDialog::confirm("실행하시겠습니까?", this)) {
      params.remove("CalibrationParams");
    }
  });
  addItem(resetCalibBtn);

  if (!params.getBool("Passive")) {
    //auto retrainingBtn = new ButtonControl("Review Training Guide", "REVIEW", "Review the rules, features, and limitations of openpilot");
    auto retrainingBtn = new ButtonControl("트레이닝 가이드", "실행", "");
    connect(retrainingBtn, &ButtonControl::clicked, [=]() {
      //if (ConfirmationDialog::confirm("Are you sure you want to review the training guide?", this)) {
      if (ConfirmationDialog::confirm("실행하시겠습니까?", this)) {
        emit reviewTrainingGuide();
      }
    });
    addItem(retrainingBtn);
  }

  /*
  if (Hardware::TICI()) {
    //auto regulatoryBtn = new ButtonControl("Regulatory", "VIEW", "");
    auto regulatoryBtn = new ButtonControl("규제", "보기", "");
    connect(regulatoryBtn, &ButtonControl::clicked, [=]() {
      const std::string txt = util::read_file("../assets/offroad/fcc.html");
      RichTextDialog::alert(QString::fromStdString(txt), this);
    });
    addItem(regulatoryBtn);
  }
  */

  QObject::connect(parent, &SettingsWindow::offroadTransition, [=](bool offroad) {
    //for (auto btn : findChildren<ButtonControl *>()) {
    //  btn->setEnabled(offroad);
    //}
  });

  QHBoxLayout *reset_layout = new QHBoxLayout();
  reset_layout->setSpacing(30);

  // addfunc button
  const char* addfunc = "sh /data/openpilot/scripts/addfunc.sh";
  //QPushButton *addfuncbtn = new QPushButton("Add Func");
  QPushButton *addfuncbtn = new QPushButton("추가기능");
  addfuncbtn->setStyleSheet("height: 120px;border-radius: 15px;background-color: #393939;");
  reset_layout->addWidget(addfuncbtn);
  QObject::connect(addfuncbtn, &QPushButton::released, [=]() {
    //if (ConfirmationDialog::confirm("Process?", this)){
    if (ConfirmationDialog::confirm("실행하시겠습니까?", this)) {
      std::system(addfunc);
      emit closeSettings();
      QTimer::singleShot(1000, []() {
        Params().putBool("SoftRestartTriggered", true);
      });
    }
  });

  // reset calibration button
  //QPushButton *reset_calib_btn = new QPushButton("Reset Calibration,LiveParameters");
  QPushButton *reset_calib_btn = new QPushButton("Calibration,LiveParameters 리셋");
  reset_calib_btn->setStyleSheet("height: 120px;border-radius: 15px;background-color: #393939;");
  reset_layout->addWidget(reset_calib_btn);
  QObject::connect(reset_calib_btn, &QPushButton::released, [=]() {
    //if (ConfirmationDialog::confirm("Are you sure you want to reset calibration and live params?", this)) {
    if (ConfirmationDialog::confirm("실행하시겠습니까?", this)) {
      Params().remove("CalibrationParams");
      Params().remove("LiveParameters");
      emit closeSettings();
      QTimer::singleShot(1000, []() {
        Params().putBool("SoftRestartTriggered", true);
      });
    }
  });
  addItem(reset_layout);

  // power buttons
  QHBoxLayout *power_layout = new QHBoxLayout();
  power_layout->setSpacing(30);

  // softreset button
  //QPushButton *restart_openpilot_btn = new QPushButton("Soft Reset");
  QPushButton *restart_openpilot_btn = new QPushButton("재시작");
  restart_openpilot_btn->setObjectName("restart_openpilot_btn");
  power_layout->addWidget(restart_openpilot_btn);
  QObject::connect(restart_openpilot_btn, &QPushButton::released, [=]() {
    emit closeSettings();
    QTimer::singleShot(1000, []() {
      Params().putBool("SoftRestartTriggered", true);
    });
  });

  //QPushButton *reboot_btn = new QPushButton("Reboot");
  QPushButton *reboot_btn = new QPushButton("재부팅");
  reboot_btn->setObjectName("reboot_btn");
  power_layout->addWidget(reboot_btn);
  QObject::connect(reboot_btn, &QPushButton::clicked, this, &DevicePanel::reboot);

  //QPushButton *poweroff_btn = new QPushButton("Power Off");
  QPushButton *poweroff_btn = new QPushButton("종료");
  poweroff_btn->setObjectName("poweroff_btn");
  power_layout->addWidget(poweroff_btn);
  QObject::connect(poweroff_btn, &QPushButton::clicked, this, &DevicePanel::poweroff);

  setStyleSheet(R"(
    QPushButton {
      height: 120px;
      border-radius: 15px;
    }
    #restart_openpilot_btn { background-color: #2C2CE2; }
    #restart_openpilot_btn:pressed { background-color: #2424FF; }
    #reboot_btn { background-color: #2CE22C; }
    #reboot_btn:pressed { background-color: #24FF24; }
    #poweroff_btn { background-color: #E22C2C; }
    #poweroff_btn:pressed { background-color: #FF2424; }
  )");
  addItem(power_layout);
}

void DevicePanel::updateCalibDescription() {
  QString desc =
      //"openpilot requires the device to be mounted within 4° left or right and "
      //"within 5° up or down. openpilot is continuously calibrating, resetting is rarely required.";
      "범위 (pitch) ↕ 5˚ (yaw) ↔ 4˚이내";
  std::string calib_bytes = Params().get("CalibrationParams");
  if (!calib_bytes.empty()) {
    try {
      AlignedBuffer aligned_buf;
      capnp::FlatArrayMessageReader cmsg(aligned_buf.align(calib_bytes.data(), calib_bytes.size()));
      auto calib = cmsg.getRoot<cereal::Event>().getLiveCalibration();
      if (calib.getCalStatus() != 0) {
        double pitch = calib.getRpyCalib()[1] * (180 / M_PI);
        double yaw = calib.getRpyCalib()[2] * (180 / M_PI);
        //desc += QString(" Your device is pointed %1° %2 and %3° %4.")
        //           .arg(QString::number(std::abs(pitch), 'g', 1), pitch > 0 ? "up" : "down",
        //                QString::number(std::abs(yaw), 'g', 1), yaw > 0 ? "right" : "left");
        desc += QString("\n현재 캘리브레이션된 위치는 [ %2 %1° / %4 %3° ] 입니다.")
                   .arg(QString::number(std::abs(pitch), 'g', 1), pitch > 0 ? "↑" : "↓",
                        QString::number(std::abs(yaw), 'g', 1), yaw > 0 ? "→" : "←");
      }
    } catch (kj::Exception) {
      //qInfo() << "invalid CalibrationParams";
      qInfo() << "캘리브레이션 상태가 유효하지않습니다";
    }
  }
  qobject_cast<ButtonControl *>(sender())->setDescription(desc);
}

void DevicePanel::reboot() {
  if (QUIState::ui_state.status == UIStatus::STATUS_DISENGAGED) {
    //if (ConfirmationDialog::confirm("Are you sure you want to reboot?", this)) {
    if (ConfirmationDialog::confirm("실행하시겠습니까?", this)) {
      // Check engaged again in case it changed while the dialog was open
      if (QUIState::ui_state.status == UIStatus::STATUS_DISENGAGED) {
        Params().putBool("DoReboot", true);
      }
    }
  } else {
    //ConfirmationDialog::alert("Disengage to Reboot", this);
    ConfirmationDialog::alert("오픈파일럿 해제후 재부팅", this);
  }
}

void DevicePanel::poweroff() {
  if (QUIState::ui_state.status == UIStatus::STATUS_DISENGAGED) {
    //if (ConfirmationDialog::confirm("Are you sure you want to power off?", this)) {
    if (ConfirmationDialog::confirm("실행하시겠습니까?", this)) {
      // Check engaged again in case it changed while the dialog was open
      if (QUIState::ui_state.status == UIStatus::STATUS_DISENGAGED) {
        Params().putBool("DoShutdown", true);
      }
    }
  } else {
    //ConfirmationDialog::alert("Disengage to Power Off", this);
    ConfirmationDialog::alert("오픈파일럿 해제후 종료", this);
  }
}

SoftwarePanel::SoftwarePanel(QWidget* parent) : ListWidget(parent) {
  gitRemoteLbl = new LabelControl("Git Remote");
  gitBranchLbl = new LabelControl("Git Branch");
  gitCommitLbl = new LabelControl("Git Commit");
  osVersionLbl = new LabelControl("OS Version");
  versionLbl = new LabelControl("Version", "", QString::fromStdString(params.get("ReleaseNotes")).trimmed());
  lastUpdateLbl = new LabelControl("Last Update Check", "", "The last time openpilot successfully checked for an update. The updater only runs while the car is off.");
  updateBtn = new ButtonControl("Check for Update", "");
  connect(updateBtn, &ButtonControl::clicked, [=]() {
    if (params.getBool("IsOffroad")) {
      fs_watch->addPath(QString::fromStdString(params.getParamPath("LastUpdateTime")));
      fs_watch->addPath(QString::fromStdString(params.getParamPath("UpdateFailedCount")));
      updateBtn->setText("CHECKING");
      updateBtn->setEnabled(false);
    }
    std::system("pkill -1 -f selfdrive.updated");
  });


  auto uninstallBtn = new ButtonControl(getBrand() + "제거", "실행");
  connect(uninstallBtn, &ButtonControl::clicked, [&]() {
    if (ConfirmationDialog::confirm("실행하시겠습니까?", this)) {
      params.putBool("DoUninstall", true);
    }
  });
  connect(parent, SIGNAL(offroadTransition(bool)), uninstallBtn, SLOT(setEnabled(bool)));

  //QWidget *widgets[] = {versionLbl, gitRemoteLbl, gitBranchLbl, gitCommitLbl, lastUpdateLbl, updateBtn, osVersionLbl, uninstallBtn};
  QWidget *widgets[] = {versionLbl, gitRemoteLbl, gitBranchLbl, gitCommitLbl, osVersionLbl, uninstallBtn};
  for (QWidget* w : widgets) {
    addItem(w);
  }

  fs_watch = new QFileSystemWatcher(this);
  QObject::connect(fs_watch, &QFileSystemWatcher::fileChanged, [=](const QString path) {
    if (path.contains("UpdateFailedCount") && std::atoi(params.get("UpdateFailedCount").c_str()) > 0) {
      lastUpdateLbl->setText("failed to fetch update");
      updateBtn->setText("CHECK");
      updateBtn->setEnabled(true);
    } else if (path.contains("LastUpdateTime")) {
      updateLabels();
    }
  });
}

void SoftwarePanel::showEvent(QShowEvent *event) {
  updateLabels();
}

void SoftwarePanel::updateLabels() {
  QString lastUpdate = "";
  auto tm = params.get("LastUpdateTime");
  if (!tm.empty()) {
    lastUpdate = timeAgo(QDateTime::fromString(QString::fromStdString(tm + "Z"), Qt::ISODate));
  }

  versionLbl->setText(getBrandVersion());
  lastUpdateLbl->setText(lastUpdate);
  updateBtn->setText("CHECK");
  updateBtn->setEnabled(true);
  gitRemoteLbl->setText(QString::fromStdString(params.get("GitRemote").substr(0,19)));
  gitBranchLbl->setText(QString::fromStdString(params.get("GitBranch")));
  gitCommitLbl->setText(QString::fromStdString(params.get("GitCommit")).left(7));
  osVersionLbl->setText(QString::fromStdString(Hardware::get_os_version()).trimmed());
}

QWidget * network_panel(QWidget * parent) {
#ifdef QCOM
  QWidget *w = new QWidget(parent);
  QVBoxLayout *layout = new QVBoxLayout(w);
  layout->setContentsMargins(50, 0, 50, 0);

  ListWidget *list = new ListWidget();
  list->setSpacing(30);
  //auto wifiBtn = new ButtonControl("WiFi Settings", "OPEN");
  auto wifiBtn = new ButtonControl("\U0001f4f6 WiFi 설정", "열기");
  QObject::connect(wifiBtn, &ButtonControl::clicked, [=]() { HardwareEon::launch_wifi(); });
  list->addItem(wifiBtn);

  //auto tetheringBtn = new ButtonControl("Tethering Settings", "OPEN");
  auto tetheringBtn = new ButtonControl("\U0001f4f6 테더링 설정", "열기");
  QObject::connect(tetheringBtn, &ButtonControl::clicked, [=]() { HardwareEon::launch_tethering(); });
  list->addItem(tetheringBtn);

  //auto androidBtn = new ButtonControl("\U00002699 Android Setting", "OPEN");
  auto androidBtn = new ButtonControl("\U00002699 안드로이드 설정", "열기");
  QObject::connect(androidBtn, &ButtonControl::clicked, [=]() { HardwareEon::launch_setting(); });
  list->addItem(androidBtn);

  // SSH key management
  list->addItem(new SshToggle());
  list->addItem(new SshControl());
  list->addItem(horizontal_line());
  list->addItem(new LateralControlSelect());
  list->addItem(new MfcSelect());
  list->addItem(new LongControlSelect());
  list->addItem(horizontal_line());

  const char* gitpull = "sh /data/openpilot/scripts/gitpull.sh";
  //auto gitpullbtn = new ButtonControl("Git Fetch and Reset", "RUN");
  auto gitpullbtn = new ButtonControl("Git Fetch and Reset", "실행");
  QObject::connect(gitpullbtn, &ButtonControl::clicked, [=]() {
    //if (ConfirmationDialog::confirm("Process?", w)){
    if (ConfirmationDialog::confirm("실행하시겠습니까?", w)){
      std::system(gitpull);
      QTimer::singleShot(1000, []() { Hardware::reboot(); });
    }
  });
  list->addItem(gitpullbtn);

  const char* realdata_clear = "sh /data/openpilot/scripts/realdataclear.sh";
  //auto realdataclearbtn = new ButtonControl("Driving log Delete", "RUN");
  auto realdataclearbtn = new ButtonControl("주행로그 삭제", "실행");
  QObject::connect(realdataclearbtn, &ButtonControl::clicked, [=]() {
    //if (ConfirmationDialog::confirm("Process?", w)){
    if (ConfirmationDialog::confirm("실행하시겠습니까?", w)) {
      std::system(realdata_clear);
    }
  });
  list->addItem(realdataclearbtn);

  const char* panda_flash = "sh /data/openpilot/panda/board/flash.sh";
  //auto pandaflashbtn = new ButtonControl("Panda Firmware Flash", "RUN");
  auto pandaflashbtn = new ButtonControl("판다 펌웨어 플래싱", "실행");
  QObject::connect(pandaflashbtn, &ButtonControl::clicked, [=]() {
    //if (ConfirmationDialog::confirm("Process?", w)){
    if (ConfirmationDialog::confirm("실행하시겠습니까?", w)){
      std::system(panda_flash);
      QTimer::singleShot(1000, []() { Hardware::reboot(); });
    }
  });
  list->addItem(pandaflashbtn);

  const char* panda_recover = "sh /data/openpilot/panda/board/recover.sh";
  //auto pandarecoverbtn = new ButtonControl("Panda Firmware Recover", "RUN");
  auto pandarecoverbtn = new ButtonControl("판다 펌웨어 복구", "실행");
  QObject::connect(pandarecoverbtn, &ButtonControl::clicked, [=]() {
    //if (ConfirmationDialog::confirm("Process?", w)){
    if (ConfirmationDialog::confirm("실행하시겠습니까?", w)){
      std::system(panda_recover);
      QTimer::singleShot(1000, []() { Hardware::reboot(); });
    }
  });
  list->addItem(pandarecoverbtn);

  layout->addWidget(list);
  layout->addStretch(1);
#else
  Networking *w = new Networking(parent);
#endif
  return w;
}

static QStringList get_list(const char* path)
{
  QStringList stringList;
  QFile textFile(path);
  if(textFile.open(QIODevice::ReadOnly))
  {
      QTextStream textStream(&textFile);
      while (true)
      {
        QString line = textStream.readLine();
        if (line.isNull())
            break;
        else
            stringList.append(line);
      }
  }

  return stringList;
}

void SettingsWindow::showEvent(QShowEvent *event) {
  panel_widget->setCurrentIndex(0);
  nav_btns->buttons()[0]->setChecked(true);
}

SettingsWindow::SettingsWindow(QWidget *parent) : QFrame(parent) {

  // setup two main layouts
  sidebar_widget = new QWidget;
  QVBoxLayout *sidebar_layout = new QVBoxLayout(sidebar_widget);
  sidebar_layout->setMargin(0);
  panel_widget = new QStackedWidget();
  panel_widget->setStyleSheet(R"(
    border-radius: 30px;
    background-color: #292929;
  )");

  // close button
  QPushButton *close_btn = new QPushButton("◀ Back");
  close_btn->setStyleSheet(R"(
    QPushButton {
      font-size: 50px;
      font-weight: bold;
      margin: 0px;
      padding: 15px;
      border-width: 0;
      border-radius: 30px;
      color: #dddddd;
      background-color: #444444;
    }
  )");
  close_btn->setFixedSize(300, 110);
  sidebar_layout->addSpacing(10);
  sidebar_layout->addWidget(close_btn, 0, Qt::AlignRight);
  sidebar_layout->addSpacing(10);
  QObject::connect(close_btn, &QPushButton::clicked, this, &SettingsWindow::closeSettings);

  // setup panels
  DevicePanel *device = new DevicePanel(this);
  QObject::connect(device, &DevicePanel::reviewTrainingGuide, this, &SettingsWindow::reviewTrainingGuide);
  QObject::connect(device, &DevicePanel::showDriverView, this, &SettingsWindow::showDriverView);
  QObject::connect(device, &DevicePanel::closeSettings, this, &SettingsWindow::closeSettings);

  QList<QPair<QString, QWidget *>> panels = {
    //{"Device", device},
    //{"Network", network_panel(this)},
    //{"Toggles", new TogglesPanel(this)},
    //{"Software", new SoftwarePanel(this)},
    //{"Community", new CommunityPanel(this)},
    {"장치", device},
    {"설정", network_panel(this)},
    {"토글", new TogglesPanel(this)},
    {"정보", new SoftwarePanel(this)},
    {"커뮤니티", new CommunityPanel(this)},
  };

#ifdef ENABLE_MAPS
  auto map_panel = new MapPanel(this);
  panels.push_back({"Navigation", map_panel});
  QObject::connect(map_panel, &MapPanel::closeSettings, this, &SettingsWindow::closeSettings);
#endif

  const int padding = panels.size() > 3 ? 25 : 35;

  nav_btns = new QButtonGroup(this);
  for (auto &[name, panel] : panels) {
    QPushButton *btn = new QPushButton(name);
    btn->setCheckable(true);
    btn->setChecked(nav_btns->buttons().size() == 0);
    btn->setStyleSheet(QString(R"(
      QPushButton {
        color: grey;
        border: none;
        background: none;
        font-size: 60px;
        font-weight: 500;
        padding-top: %1px;
        padding-bottom: %1px;
      }
      QPushButton:checked {
        color: white;
      }
      QPushButton:pressed {
        color: #ADADAD;
      }
    )").arg(padding));

    nav_btns->addButton(btn);
    sidebar_layout->addWidget(btn, 0, Qt::AlignRight);

    const int lr_margin = name != "Network" ? 50 : 0;  // Network panel handles its own margins
    panel->setContentsMargins(lr_margin, 25, lr_margin, 25);

    ScrollView *panel_frame = new ScrollView(panel, this);
    panel_widget->addWidget(panel_frame);

    QObject::connect(btn, &QPushButton::clicked, [=, w = panel_frame]() {
      btn->setChecked(true);
      panel_widget->setCurrentWidget(w);
    });
  }
  sidebar_layout->setContentsMargins(50, 50, 100, 50);

  // main settings layout, sidebar + main panel
  QHBoxLayout *main_layout = new QHBoxLayout(this);

  sidebar_widget->setFixedWidth(500);
  main_layout->addWidget(sidebar_widget);
  main_layout->addWidget(panel_widget);

  setStyleSheet(R"(
    * {
      color: white;
      font-size: 50px;
    }
    SettingsWindow {
      background-color: black;
    }
  )");
}

void SettingsWindow::hideEvent(QHideEvent *event) {
#ifdef QCOM
  HardwareEon::close_activities();
#endif
}

// Community Panel
CommunityPanel::CommunityPanel(QWidget* parent) : QWidget(parent) {
  main_layout = new QStackedLayout(this);
  QWidget* homeScreen = new QWidget(this);
  QVBoxLayout* vlayout = new QVBoxLayout(homeScreen);
  vlayout->setContentsMargins(0, 20, 0, 20);

  QString selected = QString::fromStdString(Params().get("SelectedCar"));

  //QPushButton* selectCarBtn = new QPushButton(selected.length() ? selected : "Select your car");
  QPushButton* selectCarBtn = new QPushButton(selected.length() ? selected : "차량을 선택하세요");
  selectCarBtn->setObjectName("selectCarBtn");
  selectCarBtn->setStyleSheet("margin-right: 30px;");
  //selectCarBtn->setFixedSize(350, 100);
  connect(selectCarBtn, &QPushButton::clicked, [=]() { main_layout->setCurrentWidget(selectCar); });
  vlayout->addSpacing(10);
  vlayout->addWidget(selectCarBtn, 0, Qt::AlignRight);
  vlayout->addSpacing(10);

  homeWidget = new QWidget(this);
  QVBoxLayout* toggleLayout = new QVBoxLayout(homeWidget);
  homeWidget->setObjectName("homeWidget");

  ScrollView *scroller = new ScrollView(homeWidget, this);
  scroller->setVerticalScrollBarPolicy(Qt::ScrollBarAsNeeded);
  vlayout->addWidget(scroller, 1);

  main_layout->addWidget(homeScreen);

  selectCar = new SelectCar(this);
  connect(selectCar, &SelectCar::backPress, [=]() { main_layout->setCurrentWidget(homeScreen); });
  connect(selectCar, &SelectCar::selectedCar, [=]() {
     QString selected = QString::fromStdString(Params().get("SelectedCar"));
     selectCarBtn->setText(selected.length() ? selected : "Select your car");
     main_layout->setCurrentWidget(homeScreen);
  });
  main_layout->addWidget(selectCar);

  QPalette pal = palette();
  pal.setColor(QPalette::Background, QColor(0x29, 0x29, 0x29));
  setAutoFillBackground(true);
  setPalette(pal);

  setStyleSheet(R"(
    #back_btn, #selectCarBtn {
      font-size: 50px;
      margin: 0px;
      padding: 20px;
      border-width: 0;
      border-radius: 30px;
      color: #dddddd;
      background-color: #444444;
    }
  )");

  QList<ParamControl*> toggles;

  toggles.append(new ParamControl("PutPrebuilt", "Prebuilt Enable",
                                  //"Create prebuilt files to speed bootup",
                                  "Prebuilt 파일을 생성하며 부팅속도를 향상시킵니다.",
                                  "../assets/offroad/icon_addon.png", this));
#ifdef QCOM
  toggles.append(new ParamControl("ShutdowndDisable", "Shutdownd Disable",
                                  //"Disable Shutdownd",
                                  "Shutdownd (시동 off 5분) 자동종료를 사용하지않습니다. (batteryless 기종)",
                                  "../assets/offroad/icon_addon.png", this));
#endif
  toggles.append(new ParamControl("LoggerDisable", "Logger Disable",
                                  //"Disable Logger is Reduce system load",
                                  "Logger 프로세스를 종료하여 시스템 부하를 줄입니다.",
                                  "../assets/offroad/icon_addon.png", this));
  toggles.append(new ParamControl("SccSmootherSlowOnCurves", "Slow On Curves Enable",
                                  //"Slow On Curves Enable",
                                  "커브길에서 감속운행합니다.",
                                  "../assets/offroad/icon_road.png", this));
  toggles.append(new ParamControl("SccSmootherSyncGasPressed", "Sync set speed on gas pressed Enable",
                                  //"Sync set speed on gas pressed Enable",
                                  "가속페달 사용으로 올라간 속도를 SET 속도와 일치시킵니다.",
                                  "../assets/offroad/icon_road.png", this));
  toggles.append(new ParamControl("WarningOverSpeedLimit", "Warning when speed limit is exceeded.",
                                  "",
                                  "../assets/offroad/icon_road.png", this));
  toggles.append(new ParamControl("StockNaviDecelEnabled", "Stock Navi based deceleration Enable",
                                  //"Use the stock navi based deceleration for longcontrol",
                                  "Longcontrol 사용시 순정내비게이션의 속도감속정보를 사용합니다.",
                                  "../assets/offroad/icon_road.png", this));
  toggles.append(new ParamControl("NewRadarInterface", "New radar interface Enable",
                                  //"New radar interface Enable",
                                  "scc 레이더 배선개조없이 사용가능한 일부차종을 위한 옵션입니다",
                                  "../assets/offroad/icon_road.png", this));
  /*
  toggles.append(new ParamControl("KeepSteeringTurnSignals", "Keep steering while turn signals.",
                                  "",
                                  "../assets/offroad/icon_road.png", this));
  toggles.append(new ParamControl("DisableOpFcw", "Openpilot FCW Disable",
                                  "",
                                  "../assets/offroad/icon_road.png", this));
  toggles.append(new ParamControl("ShowDebugUI", "Show Debug UI Enable",
                                  "",
                                  "../assets/offroad/icon_shell.png", this));
  toggles.append(new ParamControl("DisableGps", "GPS Disable",
                                  //"If you're using a panda without GPS, activate the option",
                                  "Panda에 Gps가 장착되어있지않은 기기일경우 옵션을 활성화하세요.",
                                  "../assets/offroad/icon_addon.png", this));
  toggles.append(new ParamControl("UseClusterSpeed", "Use Cluster Speed",
                                  "Use cluster speed instead of wheel speed.",
                                  "../assets/offroad/icon_road.png", this));
  toggles.append(new ParamControl("LongControlEnabled", "Enable HKG Long Control",
                                  "warnings: it is beta, be careful!! Openpilot will control the speed of your car",
                                  "../assets/offroad/icon_road.png", this));
  toggles.append(new ParamControl("MadModeEnabled", "Enable HKG MAD mode",
                                  "Openpilot will engage when turn cruise control on",
                                  "../assets/offroad/icon_openpilot.png", this));
  */
  for(ParamControl *toggle : toggles) {
    if(main_layout->count() != 0) {
    }
    toggleLayout->addWidget(toggle);
  }
}

SelectCar::SelectCar(QWidget* parent): QWidget(parent) {

  QVBoxLayout* main_layout = new QVBoxLayout(this);
  main_layout->setMargin(20);
  main_layout->setSpacing(20);

  // Back button
  QPushButton* back = new QPushButton("Back");
  back->setObjectName("back_btn");
  back->setFixedSize(500, 100);
  connect(back, &QPushButton::clicked, [=]() { emit backPress(); });
  main_layout->addWidget(back, 0, Qt::AlignLeft);
  QListWidget* list = new QListWidget(this);
  list->setStyleSheet("QListView {padding: 40px; background-color: #393939; border-radius: 15px; height: 140px;} QListView::item{height: 100px}");
  //list->setAttribute(Qt::WA_AcceptTouchEvents, true);
  QScroller::grabGesture(list->viewport(), QScroller::LeftMouseButtonGesture);
  list->setVerticalScrollMode(QAbstractItemView::ScrollPerPixel);
  list->addItem("[ Not selected ]");
  QStringList items = get_list("/data/params/d/SupportedCars");
  list->addItems(items);
  list->setCurrentRow(0);
  QString selected = QString::fromStdString(Params().get("SelectedCar"));

  int index = 0;
  for(QString item : items) {
    if(selected == item) {
        list->setCurrentRow(index + 1);
        break;
    }
    index++;
  }

  QObject::connect(list, QOverload<QListWidgetItem*>::of(&QListWidget::itemClicked),
    [=](QListWidgetItem* item){

    if(list->currentRow() == 0)
        Params().remove("SelectedCar");
    else
        Params().put("SelectedCar", list->currentItem()->text().toStdString());

    emit selectedCar();
    });
  main_layout->addWidget(list);
}
