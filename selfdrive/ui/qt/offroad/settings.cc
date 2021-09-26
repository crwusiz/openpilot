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

TogglesPanel::TogglesPanel(QWidget *parent) : QWidget(parent) {
  QVBoxLayout *main_layout = new QVBoxLayout(this);
  main_layout->setSpacing(20);
  QList<ParamControl*> toggles;

  toggles.append(new ParamControl("OpenpilotEnabledToggle",
                                  //"Enable openpilot",
                                  //"Use the openpilot system for adaptive cruise control and lane keep driver assistance. Your attention is required at all times to use this feature. Changing this setting takes effect when the car is powered off.",
                                  "오픈파일럿 사용",
                                  "오픈파일럿을 사용하여 조향보조 기능을 사용합니다. 항상 핸들을 잡고 도로를 주시하세요.",
                                  "../assets/offroad/icon_openpilot.png",
                                  this));
/*
  toggles.append(new ParamControl("IsRHD",
                                  "Enable Right-Hand Drive",
                                  "Allow openpilot to obey left-hand traffic conventions and perform driver monitoring on right driver seat.",
                                  "../assets/offroad/icon_openpilot_mirrored.png",
                                  this));
*/
  toggles.append(new ParamControl("IsMetric",
                                  //"Use Metric System",
                                  //"Display speed in km/h instead of mp/h.",
                                  "미터법 사용",
                                  "주행속도 표시를 ㎞/h로 변경합니다",
                                  "../assets/offroad/icon_metric.png",
                                  this));
  toggles.append(new ParamControl("CommunityFeaturesToggle",
                                  //"Enable Community Features",
                                  //"Use features from the open source community that are not maintained or supported by comma.ai and have not been confirmed to meet the standard safety model. These features include community supported cars and community supported hardware. Be extra cautious when using these features",
                                  "커뮤니티 기능 사용",
                                  "커뮤니티기능은 comma.ai에서 공식적으로 지원하는 기능이 아니므로 사용시 주의하세요.",
                                  "../assets/offroad/icon_discord.png",
                                  this));
  toggles.append(new ParamControl("IsLdwEnabled",
                                  //"Enable Lane Departure Warnings",
                                  //"Receive alerts to steer back into the lane when your vehicle drifts over a detected lane line without a turn signal activated while driving over 31mph (50kph).",
                                  "차선이탈 경보 사용",
                                  "40㎞/h 이상의 속도로 주행시 방향지시등 조작없이 차선을 이탈하면 차선이탈경보를 보냅니다. (오픈파일럿 비활성상태에서만 사용됩니다)",
                                  "../assets/offroad/icon_ldws.png",
                                  this));
  toggles.append(new ParamControl("AutoLaneChangeEnabled",
                                  //"Enable AutoLaneChange",
                                  //"Operation of the turn signal at 60㎞/h speed will result in a short change of the vehicle",
                                  "자동 차선변경 사용",
                                  "60㎞/h 이상의 속도로 주행시 방향지시등을 작동하면 잠시후 자동차선변경을 수행합니다. 안전한 사용을위해 후측방감지기능이 있는 차량만 사용하시기바랍니다.",
                                  "../assets/offroad/icon_lca.png",
                                  this));
  toggles.append(new ParamControl("UploadRaw",
                                  //"Upload Raw Logs",
                                  //"Upload full logs at [ connect.comma.ai/useradmin ]",
                                  "주행로그 업로드",
                                  "주행로그를 업로드합니다. [ connect.comma.ai/useradmin ] 에서 확인할수있습니다",
                                  "../assets/offroad/icon_network.png",
                                  this));
  toggles.append(new ParamControl("EndToEndToggle",
                                  "\U0001f96c Disable use of lanelines (Alpha) \U0001f96c",
                                  "In this mode openpilot will ignore lanelines and just drive how it thinks a human would.",
                                  "../assets/offroad/icon_road.png",
                                  this));
/*
  ParamControl *record_toggle = new ParamControl("RecordFront",
                                                 "Record and Upload Driver Camera",
                                                 "Upload data from the driver facing camera and help improve the driver monitoring algorithm.",
                                                 "../assets/offroad/icon_monitoring.png",
                                                 this);
  toggles.append(record_toggle);

#ifdef ENABLE_MAPS
  toggles.append(new ParamControl("NavSettingTime24h",
                                  "Show ETA in 24h format",
                                  "Use 24h format instead of am/pm",
                                  "../assets/offroad/icon_metric.png",
                                  this));
#endif

  bool record_lock = Params().getBool("RecordFrontLock");
  record_toggle->setEnabled(!record_lock);
*/

  for(ParamControl *toggle : toggles) {
    if(main_layout->count() != 0) {
    }
    main_layout->addWidget(toggle);
  }
  main_layout->addWidget(horizontal_line());
  main_layout->addWidget(new LabelControl(" ▼  커뮤니티 추가기능 토글", ""));
  main_layout->addWidget(new ParamControl("PutPrebuilt", "Prebuilt Enable",
                                  //"Create prebuilt files to speed bootup",
                                  "Prebuilt 파일을 생성하며 부팅속도를 향상시킵니다.",
                                  "../assets/offroad/icon_addon.png", this));
  main_layout->addWidget(new ParamControl("DisableShutdownd", "Shutdownd Disable",
                                  //"Disable Shutdownd",
                                  "Shutdownd (시동 off 5분) 자동종료를 사용하지않습니다. (batteryless 기종)",
                                  "../assets/offroad/icon_addon.png", this));
  main_layout->addWidget(new ParamControl("DisableLogger", "Logger Disable",
                                  //"Disable Logger is Reduce system load",
                                  "Logger 프로세스를 종료하여 시스템 부하를 줄입니다.",
                                  "../assets/offroad/icon_addon.png", this));
  main_layout->addWidget(new ParamControl("DisableGps", "GPS Disable",
                                  //"If you're using a panda without GPS, activate the option",
                                  "Panda에 Gps가 장착되어있지않은 기기일경우 옵션을 활성화하세요.",
                                  "../assets/offroad/icon_addon.png", this));
  main_layout->addWidget(new ParamControl("UiTpms", "Ui Tpms Enable",
                                  //"Ui Tpms Enable (HKG only)",
                                  "Ui에 Tpms 정보를 표시합니다 (HKG only)",
                                  "../assets/offroad/icon_addon.png", this));
}

DevicePanel::DevicePanel(QWidget* parent) : QWidget(parent) {
  QVBoxLayout *main_layout = new QVBoxLayout(this);
  main_layout->setSpacing(20);
  Params params = Params();
  main_layout->addWidget(new LabelControl("Dongle ID", getDongleId().value_or("N/A")));
/*
  main_layout->addWidget(horizontal_line());

  QString serial = QString::fromStdString(params.get("HardwareSerial", false));
  main_layout->addWidget(new LabelControl("Serial", serial));
*/
  // offroad-only buttons

  //auto dcamBtn = new ButtonControl("Driver Camera", "PREVIEW", "Preview the driver facing camera to help optimize device mounting position for best driver monitoring experience. (vehicle must be off)");
  auto dcamBtn = new ButtonControl("운전자 모니터링 카메라 미리보기", "실행", "운전자 모니터링 카메라를 미리보고 최적의 장착위치를 찾아보세요.");
  connect(dcamBtn, &ButtonControl::clicked, [=]() { emit showDriverView(); });

  QString resetCalibDesc = "범위 (pitch) ↕ 5˚ (yaw) ↔ 4˚이내";
  //auto resetCalibBtn = new ButtonControl("Reset Calibration", "RESET", resetCalibDesc);
  auto resetCalibBtn = new ButtonControl("캘리브레이션 초기화", "실행", resetCalibDesc);
  connect(resetCalibBtn, &ButtonControl::clicked, [=]() {
    //if (ConfirmationDialog::confirm("Process?", this)) {
    if (ConfirmationDialog::confirm("실행하시겠습니까?", this)) {
      Params().remove("CalibrationParams");
    }
  });
  connect(resetCalibBtn, &ButtonControl::showDescription, [=]() {
    QString desc = resetCalibDesc;
    std::string calib_bytes = Params().get("CalibrationParams");
    if (!calib_bytes.empty()) {
      try {
        AlignedBuffer aligned_buf;
        capnp::FlatArrayMessageReader cmsg(aligned_buf.align(calib_bytes.data(), calib_bytes.size()));
        auto calib = cmsg.getRoot<cereal::Event>().getLiveCalibration();
        if (calib.getCalStatus() != 0) {
          double pitch = calib.getRpyCalib()[1] * (180 / M_PI);
          double yaw = calib.getRpyCalib()[2] * (180 / M_PI);
          //desc += QString("\nThe current calibration location is [ %2 %1° / %4 %3° ]")
          desc += QString("\n현재 캘리브레이션된 위치는 [ %2 %1° / %4 %3° ] 입니다.")
                                .arg(QString::number(std::abs(pitch), 'g', 1), pitch > 0 ? "↑" : "↓",
                                     QString::number(std::abs(yaw), 'g', 1), yaw > 0 ? "→" : "←");
        }
      } catch (kj::Exception) {
        //qInfo() << "invalid CalibrationParams";
        qInfo() << "캘리브레이션 상태가 유효하지않습니다";
      }
    }
    resetCalibBtn->setDescription(desc);
  });

  ButtonControl *retrainingBtn = nullptr;
  if (!params.getBool("Passive")) {
    //retrainingBtn = new ButtonControl("Review Training Guide", "REVIEW", "Review the rules, features, and limitations of openpilot");
    retrainingBtn = new ButtonControl("트레이닝 가이드", "실행", "");
    connect(retrainingBtn, &ButtonControl::clicked, [=]() {
      //if (ConfirmationDialog::confirm("Process?", this)) {
      if (ConfirmationDialog::confirm("실행하시겠습니까?", this)) {
        Params().remove("CompletedTrainingVersion");
        emit reviewTrainingGuide();
      }
    });
  }

  ButtonControl *regulatoryBtn = nullptr;
  if (Hardware::TICI()) {
    regulatoryBtn = new ButtonControl("Regulatory", "VIEW", "");
    connect(regulatoryBtn, &ButtonControl::clicked, [=]() {
      const std::string txt = util::read_file(ASSET_PATH.toStdString() + "/offroad/fcc.html");
      RichTextDialog::alert(QString::fromStdString(txt), this);
    });
  }

  for (auto btn : {dcamBtn, retrainingBtn, regulatoryBtn, resetCalibBtn}) {
    if (btn) {
      connect(parent, SIGNAL(offroadTransition(bool)), btn, SLOT(setEnabled(bool)));
      main_layout->addWidget(btn);
    }
  }
  QHBoxLayout *reset_layout = new QHBoxLayout();
  reset_layout->setSpacing(30);

  // addfunc button
  const char* addfunc = "cp -f /data/openpilot/installer/fonts/driver_monitor.py /data/openpilot/selfdrive/monitoring";
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
  QPushButton *reset_calib_btn = new QPushButton("Calibration,LiveParameters 리셋");
  reset_calib_btn->setStyleSheet("height: 120px;border-radius: 15px;background-color: #393939;");
  reset_layout->addWidget(reset_calib_btn);
  QObject::connect(reset_calib_btn, &QPushButton::released, [=]() {
    //if (ConfirmationDialog::confirm("Process?", this)) {
    if (ConfirmationDialog::confirm("실행하시겠습니까?", this)) {
      Params().remove("CalibrationParams");
      Params().remove("LiveParameters");
      emit closeSettings();
      QTimer::singleShot(1000, []() {
        Params().putBool("SoftRestartTriggered", true);
      });
    }
  });
  main_layout->addLayout(reset_layout);

  // power buttons
  QHBoxLayout *power_layout = new QHBoxLayout();
  power_layout->setSpacing(30);

  // softreset button
  QPushButton *restart_openpilot_btn = new QPushButton("재시작");
  restart_openpilot_btn->setStyleSheet("height: 120px;border-radius: 15px;background-color: #393939;");
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
  QObject::connect(reboot_btn, &QPushButton::clicked, [=]() {
    //if (ConfirmationDialog::confirm("Process?", this)) {
    if (ConfirmationDialog::confirm("실행하시겠습니까?", this)) {
      Hardware::reboot();
    }
  });

  //QPushButton *poweroff_btn = new QPushButton("Power Off");
  QPushButton *poweroff_btn = new QPushButton("종료");
  poweroff_btn->setObjectName("poweroff_btn");
  power_layout->addWidget(poweroff_btn);
  QObject::connect(poweroff_btn, &QPushButton::clicked, [=]() {
    //if (ConfirmationDialog::confirm("Process?", this)) {
    if (ConfirmationDialog::confirm("실행하시겠습니까?", this)) {
      Hardware::poweroff();
    }
  });

  setStyleSheet(R"(
    QPushButton {
      height: 120px;
      border-radius: 15px;
    }
    #reboot_btn { background-color: #2CE22C; }
    #reboot_btn:pressed { background-color: #4a4a4a; }
    #poweroff_btn { background-color: #E22C2C; }
    #poweroff_btn:pressed { background-color: #FF2424; }
  )");
  main_layout->addLayout(power_layout);
}

SoftwarePanel::SoftwarePanel(QWidget* parent) : QWidget(parent) {
  gitRemoteLbl = new LabelControl("Git Remote");
  gitBranchLbl = new LabelControl("Git Branch");
  gitCommitLbl = new LabelControl("Git Commit");
  osVersionLbl = new LabelControl("NEOS Version");
  versionLbl = new LabelControl("Version");
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

  QVBoxLayout *main_layout = new QVBoxLayout(this);
  main_layout->setSpacing(20);
  QWidget *widgets[] = {versionLbl, gitRemoteLbl, gitBranchLbl, gitCommitLbl, osVersionLbl};
  for (int i = 0; i < std::size(widgets); ++i) {
    main_layout->addWidget(widgets[i]);
  }

  auto uninstallBtn = new ButtonControl(getBrand() + " 제거", "실행");
  connect(uninstallBtn, &ButtonControl::clicked, [=]() {
    if (ConfirmationDialog::confirm("실행하시겠습니까?", this)) {
      Params().putBool("DoUninstall", true);
    }
  });
  connect(parent, SIGNAL(offroadTransition(bool)), uninstallBtn, SLOT(setEnabled(bool)));
  main_layout->addWidget(uninstallBtn);

  fs_watch = new QFileSystemWatcher(this);
  QObject::connect(fs_watch, &QFileSystemWatcher::fileChanged, [=](const QString path) {
    int update_failed_count = params.get<int>("UpdateFailedCount").value_or(0);
    if (path.contains("UpdateFailedCount") && update_failed_count > 0) {
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
  gitRemoteLbl->setText(QString::fromStdString(params.get("GitRemote").substr(19)));
  gitBranchLbl->setText(QString::fromStdString(params.get("GitBranch")));
  gitCommitLbl->setText(QString::fromStdString(params.get("GitCommit")).left(7));
  osVersionLbl->setText(QString::fromStdString(Hardware::get_os_version()).trimmed());
}

QStringList get_list(const char* path){
  QStringList stringList;
  QFile textFile(path);
  if(textFile.open(QIODevice::ReadOnly)){
      QTextStream textStream(&textFile);
      while (true){
        QString line = textStream.readLine();
        if (line.isNull())
            break;
        else
            stringList.append(line);
            }
  }
  return stringList;
}

QWidget * network_panel(QWidget * parent) {
#ifdef QCOM
  QWidget *w = new QWidget(parent);
  QVBoxLayout *layout = new QVBoxLayout(w);
  layout->setSpacing(20);

  //auto wifiBtn = new ButtonControl("WiFi Settings", "OPEN");
  auto wifiBtn = new ButtonControl("\U0001f4f6 WiFi 설정", "열기");
  QObject::connect(wifiBtn, &ButtonControl::clicked, [=]() { HardwareEon::launch_wifi(); });
  layout->addWidget(wifiBtn);

  //auto tetheringBtn = new ButtonControl("Tethering Settings", "OPEN");
  auto tetheringBtn = new ButtonControl("\U0001f4f6 테더링 설정", "열기");
  QObject::connect(tetheringBtn, &ButtonControl::clicked, [=]() { HardwareEon::launch_tethering(); });
  layout->addWidget(tetheringBtn);

  //auto androidBtn = new ButtonControl("\U00002699 Android Setting", "OPEN");
  auto androidBtn = new ButtonControl("\U00002699 안드로이드 설정", "열기");
  QObject::connect(androidBtn, &ButtonControl::clicked, [=]() { HardwareEon::launch_setting(); });
  layout->addWidget(androidBtn);

  layout->addWidget(horizontal_line());

  // SSH key management
  layout->addWidget(new SshToggle());
  layout->addWidget(new SshControl());
  layout->addWidget(horizontal_line());
  layout->addWidget(new LateralControlSelect());
  layout->addWidget(new MfcSelect());
  layout->addWidget(new LongControlSelect());

 // Car Force Recognition for neokii
  QComboBox* supported_cars = new QComboBox();
  supported_cars->setStyleSheet(R"(
  QComboBox {
    background-color: #393939;
    border-radius: 15px;
    padding-left: 40px
    height: 140px;
    }
    )");
  QListView* list = new QListView(supported_cars);
  list->setStyleSheet("QListView {padding: 40px; background-color: #393939; border-radius: 15px; height: 140px;} QListView::item{height: 100px}");
  supported_cars->setView(list);
  QScroller::grabGesture(supported_cars->view()->viewport(),QScroller::LeftMouseButtonGesture);
  supported_cars->view()->setVerticalScrollMode(QAbstractItemView::ScrollPerPixel);
  supported_cars->setMinimumSize(200, 120);

  //supported_cars->addItem("「  Car Force Recognition  」");
  supported_cars->addItem("「  차량 강제 인식  」");
  supported_cars->addItems(get_list("/data/params/d/SupportedCars"));
  supported_cars->setCurrentText(QString(Params().get("SelectedCar").c_str()));
  QObject::connect(supported_cars, QOverload<int>::of(&QComboBox::currentIndexChanged),
    [=](int index){
        if(supported_cars->currentIndex() == 0)
            Params().remove("SelectedCar");
        else
            Params().put("SelectedCar", supported_cars->currentText().toStdString());
    });
  layout->addWidget(supported_cars);
  layout->addWidget(horizontal_line());

  const char* gitpull = "sh /data/openpilot/gitpull.sh";
  //auto gitpullbtn = new ButtonControl("Git Fetch and Reset", "RUN");
  auto gitpullbtn = new ButtonControl("Git Fetch and Reset", "실행");
  QObject::connect(gitpullbtn, &ButtonControl::clicked, [=]() {
    //if (ConfirmationDialog::confirm("Process?", w)){
    if (ConfirmationDialog::confirm("실행하시겠습니까?", w)){
      std::system(gitpull);
      QTimer::singleShot(1000, []() { Hardware::reboot(); });
    }
  });
  layout->addWidget(gitpullbtn);

  const char* realdata_clear = "rm -rf /sdcard/realdata/*";
  //auto realdataclearbtn = new ButtonControl("Driving log Delete", "RUN");
  auto realdataclearbtn = new ButtonControl("주행로그 삭제", "실행");
  QObject::connect(realdataclearbtn, &ButtonControl::clicked, [=]() {
    //if (ConfirmationDialog::confirm("Process?", w)){
    if (ConfirmationDialog::confirm("실행하시겠습니까?", w)) {
      std::system(realdata_clear);
    }
  });
  layout->addWidget(realdataclearbtn);

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
  layout->addWidget(pandaflashbtn);

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
  layout->addWidget(pandarecoverbtn);

  layout->addStretch(1);
#else
  Networking *w = new Networking(parent);
#endif
  return w;
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
    {"장치", device},
    //{"Network", network_panel(this)},
    {"설정", network_panel(this)},
    //{"Toggles", new TogglesPanel(this)},
    {"토글", new TogglesPanel(this)},
    //{"Software", new SoftwarePanel(this)},
    {"정보", new SoftwarePanel(this)},
  };

#ifdef ENABLE_MAPS
  auto map_panel = new MapPanel(this);
  panels.push_back({"Navigation", map_panel});
  QObject::connect(map_panel, &MapPanel::closeSettings, this, &SettingsWindow::closeSettings);
#endif

  const int padding = panels.size() > 3 ? 25 : 35;

  nav_btns = new QButtonGroup();
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
