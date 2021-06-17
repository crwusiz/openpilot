#include "settings.h"

#include <cassert>
#include <string>

#ifndef QCOM
#include "selfdrive/ui/qt/offroad/networking.h"
#endif
#include "selfdrive/common/params.h"
#include "selfdrive/common/util.h"
#include "selfdrive/hardware/hw.h"
#include "selfdrive/ui/qt/widgets/controls.h"
#include "selfdrive/ui/qt/widgets/input.h"
#include "selfdrive/ui/qt/widgets/offroad_alerts.h"
#include "selfdrive/ui/qt/widgets/scrollview.h"
#include "selfdrive/ui/qt/widgets/ssh_keys.h"
#include "selfdrive/ui/qt/widgets/toggle.h"
#include "selfdrive/ui/ui.h"
#include "selfdrive/ui/qt/util.h"

TogglesPanel::TogglesPanel(QWidget *parent) : QWidget(parent) {
  QVBoxLayout *toggles_list = new QVBoxLayout();

  QList<ParamControl*> toggles;

  toggles.append(new ParamControl("OpenpilotEnabledToggle",
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
                                  "미터법 사용",
                                  "주행속도 표시를 km/h로 변경합니다",
                                  "../assets/offroad/icon_metric.png",
                                  this));
  toggles.append(new ParamControl("CommunityFeaturesToggle",
                                  "커뮤니티 기능 사용",
                                  "커뮤니티기능은 comma.ai에서 공식적으로 지원하는 기능이 아니므로 사용시 주의하세요.",
                                  "../assets/offroad/icon_discord.png",
                                  this));
  toggles.append(new ParamControl("IsLdwEnabled",
                                  "차선이탈 경보 사용",
                                  "40km 이상의 속도로 주행시 방향지시등 조작없이 차선을 이탈하면 차선이탈경보를 보냅니다. (오픈파일럿 비활성상태에서만 사용됩니다)",
                                  "../assets/offroad/icon_ldws.png",
                                  this));
  toggles.append(new ParamControl("AutoLaneChangeEnabled",
                                  "자동 차선변경 사용",
                                  "60km 이상의 속도로 주행시 방향지시등을 작동하면 3초후에 차선변경을 수행합니다. 안전한 사용을위해 후측방감지기능이 있는 차량만 사용하시기바랍니다.",
                                  "../assets/offroad/icon_lca.png",
                                  this));
/*
  toggles.append(new ParamControl("UploadRaw",
                                  "Upload Raw Logs",
                                  "Upload full logs and full resolution video by default while on WiFi. If not enabled, individual logs can be marked for upload at my.comma.ai/useradmin.",
                                  "../assets/offroad/icon_network.png",
                                  this));

  ParamControl *record_toggle = new ParamControl("RecordFront",
                                                 "Record and Upload Driver Camera",
                                                "Upload data from the driver facing camera and help improve the driver monitoring algorithm.",
                                                "../assets/offroad/icon_monitoring.png",
                                                this);
  toggles.append(record_toggle);
  toggles.append(new ParamControl("EndToEndToggle",
                                   "\U0001f96c Disable use of lanelines (Alpha) \U0001f96c",
                                   "In this mode openpilot will ignore lanelines and just drive how it thinks a human would.",
                                   "../assets/offroad/icon_road.png",
                                   this));

  if (Hardware::TICI()) {
    toggles.append(new ParamControl("EnableWideCamera",
                                    "Enable use of Wide Angle Camera",
                                    "Use wide angle camera for driving and ui.",
                                    "../assets/offroad/icon_openpilot.png",
                                    this));
    QObject::connect(toggles.back(), &ToggleControl::toggleFlipped, [=](bool state) {
      Params().remove("CalibrationParams");
    });

    toggles.append(new ParamControl("EnableLteOnroad",
                                    "Enable LTE while onroad",
                                    "",
                                    "../assets/offroad/icon_network.png",
                                    this));
  }
  bool record_lock = Params().getBool("RecordFrontLock");
  record_toggle->setEnabled(!record_lock);
*/
  for(ParamControl *toggle : toggles){
    if(toggles_list->count() != 0){
      toggles_list->addWidget(horizontal_line());
    }
    toggles_list->addWidget(toggle);
  }

  setLayout(toggles_list);
}

DevicePanel::DevicePanel(QWidget* parent) : QWidget(parent) {
  QVBoxLayout *device_layout = new QVBoxLayout;
  Params params = Params();

  QString dongle = QString::fromStdString(params.get("DongleId", false));
  device_layout->addWidget(new LabelControl("동글 ID", dongle));
/*
  device_layout->addWidget(horizontal_line());

  QString serial = QString::fromStdString(params.get("HardwareSerial", false));
  device_layout->addWidget(new LabelControl("Serial", serial));
*/
  // offroad-only buttons
  QList<ButtonControl*> offroad_btns;

  offroad_btns.append(new ButtonControl("운전자 모니터링 카메라 미리보기", "실행",
                                        "운전자 모니터링 카메라를 미리보고 최적의 장착위치를 찾아보세요.",
                                        [=]() { emit showDriverView(); }, "", this));

  QString resetCalibDesc = "장치장착시 ↕ (pitch) 5˚이내 / ↔ (yaw) 4˚이내에 장착해야 합니다.";
  ButtonControl *resetCalibBtn = new ButtonControl("캘리브레이션 초기화", "실행", resetCalibDesc, [=]() {
    if (ConfirmationDialog::confirm("실행하시겠습니까?", this)) {
      Params().remove("CalibrationParams");
    }
  }, "", this);
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
          desc += QString("\n현재 장착된 위치는 [ %1° %2 / %3° %4 ] 입니다.")
                                .arg(QString::number(std::abs(pitch), 'g', 1), pitch > 0 ? "↑" : "↓",
                                     QString::number(std::abs(yaw), 'g', 1), yaw > 0 ? "→" : "←");
        }
      } catch (kj::Exception) {
        qInfo() << "invalid CalibrationParams";
      }
    }
    resetCalibBtn->setDescription(desc);
  });
  offroad_btns.append(resetCalibBtn);

  offroad_btns.append(new ButtonControl("트레이닝 가이드", "실행", "", [=]() {
    if (ConfirmationDialog::confirm("실행하시겠습니까?", this)) {
      Params().remove("CompletedTrainingVersion");
      emit reviewTrainingGuide();
    }
  }, "", this));

  QString brand = params.getBool("Passive") ? "대시캠" : "오픈파일럿";
  offroad_btns.append(new ButtonControl(brand + " 제거", "실행", "", [=]() {
    if (ConfirmationDialog::confirm("실행하시겠습니까?", this)) {
      Params().putBool("DoUninstall", true);
    }
  }, "", this));

  for(auto &btn : offroad_btns){
    device_layout->addWidget(horizontal_line());
    QObject::connect(parent, SIGNAL(offroadTransition(bool)), btn, SLOT(setEnabled(bool)));
    device_layout->addWidget(btn);
  }

  // power buttons
  QHBoxLayout *power_layout = new QHBoxLayout();
  power_layout->setSpacing(30);

  QPushButton *reboot_btn = new QPushButton("재부팅");
  reboot_btn->setStyleSheet("color: white;"
                            "background-color: #2CE22C;");
  power_layout->addWidget(reboot_btn);
  QObject::connect(reboot_btn, &QPushButton::released, [=]() {
    if (ConfirmationDialog::confirm("재부팅하시겠습니까?", this)) {
      Hardware::reboot();
    }
  });

  QPushButton *poweroff_btn = new QPushButton("종료");
  poweroff_btn->setStyleSheet("background-color: #E22C2C;");
  power_layout->addWidget(poweroff_btn);
  QObject::connect(poweroff_btn, &QPushButton::released, [=]() {
    if (ConfirmationDialog::confirm("종료하시겠습니까?", this)) {
      Hardware::poweroff();
    }
  });

  device_layout->addLayout(power_layout);

  setLayout(device_layout);
  setStyleSheet(R"(
    QPushButton {
      font-weight: 500;
      padding: 0;
      height: 120px;
      border-radius: 15px;
      background-color: #393939;
    }
  )");
}

SoftwarePanel::SoftwarePanel(QWidget* parent) : QFrame(parent) {
  QVBoxLayout *main_layout = new QVBoxLayout(this);
  setLayout(main_layout);
  setStyleSheet(R"(QLabel {font-size: 50px;})");

  fs_watch = new QFileSystemWatcher(this);
  QObject::connect(fs_watch, &QFileSystemWatcher::fileChanged, [=](const QString path) {
    int update_failed_count = Params().get<int>("UpdateFailedCount").value_or(0);
    if (path.contains("UpdateFailedCount") && update_failed_count > 0) {
      lastUpdateTimeLbl->setText("failed to fetch update");
      updateButton->setText("CHECK");
      updateButton->setEnabled(true);
    } else if (path.contains("LastUpdateTime")) {
      updateLabels();
    }
  });
}

void SoftwarePanel::showEvent(QShowEvent *event) {
  updateLabels();
}

void SoftwarePanel::updateLabels() {
  Params params = Params();
  std::string brand = params.getBool("Passive") ? "대시캠" : "오픈파일럿";
  QList<QPair<QString, std::string>> dev_params = {
    {"버전", "v" + params.get("Version")},
    {"Git Remote", params.get("GitRemote").substr(19)},
    {"Git Branch", params.get("GitBranch")},
    {"Git Commit", params.get("GitCommit").substr(0, 7)},
    {"판다 펌웨어", params.get("PandaFirmwareHex")},
    {"NEOS 버전", Hardware::get_os_version()},
  };

  QString version = QString::fromStdString("v" + params.get("Version")).trimmed();
/*
  QString lastUpdateTime = "";

  std::string last_update_param = params.get("LastUpdateTime");
  if (!last_update_param.empty()){
    QDateTime lastUpdateDate = QDateTime::fromString(QString::fromStdString(last_update_param + "Z"), Qt::ISODate);
    lastUpdateTime = timeAgo(lastUpdateDate);
  }

  if (labels.size() < dev_params.size()) {
    versionLbl = new LabelControl("버전", version, QString::fromStdString(params.get("릴리즈 노트")).trimmed());
    layout()->addWidget(versionLbl);
    layout()->addWidget(horizontal_line());

    lastUpdateTimeLbl = new LabelControl("최종 업데이트", lastUpdateTime, "The last time openpilot successfully checked for an update. The updater only runs while the car is off.");
    layout()->addWidget(lastUpdateTimeLbl);
    layout()->addWidget(horizontal_line());

    updateButton = new ButtonControl("업데이트 확인", "확인", "", [=]() {
      Params params = Params();
      if (params.getBool("IsOffroad")) {
        fs_watch->addPath(QString::fromStdString(params.getParamsPath()) + "/d/LastUpdateTime");
        fs_watch->addPath(QString::fromStdString(params.getParamsPath()) + "/d/UpdateFailedCount");
        updateButton->setText("확인중");
        updateButton->setEnabled(false);
      }
      std::system("pkill -1 -f selfdrive.updated");
    }, "", this);
    layout()->addWidget(updateButton);
    layout()->addWidget(horizontal_line());
  } else {
    versionLbl->setText(version);
    lastUpdateTimeLbl->setText(lastUpdateTime);
    updateButton->setText("확인");
    updateButton->setEnabled(true);
  }
*/
  for (int i = 0; i < dev_params.size(); i++) {
    const auto &[name, value] = dev_params[i];
    QString val = QString::fromStdString(value).trimmed();
    if (labels.size() > i) {
      labels[i]->setText(val);
    } else {
      labels.push_back(new LabelControl(name, val));
      layout()->addWidget(labels[i]);
      if (i < (dev_params.size() - 1)) {
        layout()->addWidget(horizontal_line());
      }
    }
  }
}

QWidget * network_panel(QWidget * parent) {
  QVBoxLayout *layout = new QVBoxLayout;
  layout->setSpacing(30);
#ifdef QCOM
  // wifi + tethering buttons
  layout->addWidget(new ButtonControl("\U0001f4f6 WiFi 설정", "열기", "",
                                      [=]() { HardwareEon::launch_wifi(); }));
  // Android Setting
  layout->addWidget(new ButtonControl("\U00002699 안드로이드 설정", "열기", "",
                                      [=]() { HardwareEon::launch_setting(); }));
  layout->addWidget(horizontal_line());
#endif
/*
  layout->addWidget(new ButtonControl("Tethering Settings", "OPEN", "",
                                      [=]() { HardwareEon::launch_tethering(); }));
  layout->addWidget(horizontal_line());
*/
  // SSH key management
  layout->addWidget(new SshToggle());
  layout->addWidget(new SshControl());
  layout->addWidget(horizontal_line());
  layout->addWidget(new LateralControlSelect());
  layout->addWidget(new MfcSelect());
  layout->addWidget(new LongControlSelect());
  layout->addWidget(horizontal_line());
  layout->addWidget(new PrebuiltToggle());
  layout->addWidget(new ShutdowndToggle());
  layout->addWidget(new LoggerToggle());
  layout->addWidget(new GpsToggle());
  layout->addWidget(new UiTpmsToggle());  
  layout->addWidget(horizontal_line());
  const char* gitpull = "/data/openpilot/gitpull.sh ''";
  layout->addWidget(new ButtonControl("Git Pull", "실행", "사용중인 브랜치의 최근 수정된 내용으로 변경됩니다.", [=]() {
                                        if (ConfirmationDialog::confirm("실행하시겠습니까?")){
                                          std::system(gitpull);
                                        }
                                      }));
  const char* addfunc = "/data/openpilot/addfunc.sh ''";
  layout->addWidget(new ButtonControl("추가 기능", "실행", "추가기능을 적용합니다.", [=]() {
                                        if (ConfirmationDialog::confirm("실행하시겠습니까?")) {
                                          std::system(addfunc);
                                        }
                                      }));
  const char* panda_flash = "/data/openpilot/panda/board/flash.sh ''";
  layout->addWidget(new ButtonControl("판다 펌웨어 플래싱", "실행", "판다 펌웨어 플래싱을 실행합니다.", [=]() {
                                        if (ConfirmationDialog::confirm("실행하시겠습니까?")) {
                                          std::system(panda_flash);
                                        }
                                      }));
  const char* panda_recover = "/data/openpilot/panda/board/recover.sh ''";
  layout->addWidget(new ButtonControl("판다 펌웨어 복구", "실행", "판다 펌웨어 복구를 실행합니다.", [=]() {
                                        if (ConfirmationDialog::confirm("실행하시겠습니까?")) {
                                          std::system(panda_recover);
                                        }
                                      }));
  const char* cal_ok = "cp -f /data/openpilot/installer/fonts/CalibrationParams /data/params/d/";
  layout->addWidget(new ButtonControl("켈리브레이션 강제 활성화", "실행", "인게이지 테스트를위해 캘리브레이션을 강제 활성화 합니다",
                                      [=]() { 
                                        if (ConfirmationDialog::confirm("실행하시겠습니까?")) {
                                          std::system(cal_ok);
                                        }
                                      }));  
  layout->addStretch(1);

  QWidget *w = new QWidget;
  w->setLayout(layout);
  return w;
}

void SettingsWindow::showEvent(QShowEvent *event) {
  if (layout()) {
    panel_widget->setCurrentIndex(0);
    nav_btns->buttons()[0]->setChecked(true);
    return;
  }
}

SettingsWindow::SettingsWindow(QWidget *parent) : QFrame(parent) {
  // setup two main layouts
  QVBoxLayout *sidebar_layout = new QVBoxLayout();
  sidebar_layout->setMargin(0);
  panel_widget = new QStackedWidget();
  panel_widget->setStyleSheet(R"(
    border-radius: 30px;
    background-color: #292929;
  )");

  // close button
  QPushButton *close_btn = new QPushButton("◀");
  close_btn->setStyleSheet(R"(
    font-size: 90px;
    font-weight: bold;
    border 1px grey solid;
    border-radius: 100px;
    background-color: #292929;
  )");
  close_btn->setFixedSize(200, 200);
  sidebar_layout->addSpacing(45);
  sidebar_layout->addWidget(close_btn, 0, Qt::AlignCenter);
  QObject::connect(close_btn, &QPushButton::released, this, &SettingsWindow::closeSettings);

  // setup panels
  DevicePanel *device = new DevicePanel(this);
  QObject::connect(device, &DevicePanel::reviewTrainingGuide, this, &SettingsWindow::reviewTrainingGuide);
  QObject::connect(device, &DevicePanel::showDriverView, this, &SettingsWindow::showDriverView);

  QPair<QString, QWidget *> panels[] = {
    {"장치", device},
    {"설정", network_panel(this)},
    {"토글", new TogglesPanel(this)},
    {"정보", new SoftwarePanel()},
  };

  sidebar_layout->addSpacing(45);
  nav_btns = new QButtonGroup();
  for (auto &[name, panel] : panels) {
    QPushButton *btn = new QPushButton(name);
    btn->setCheckable(true);
    btn->setChecked(nav_btns->buttons().size() == 0);
    btn->setStyleSheet(R"(
      QPushButton {
        color: grey;
        border: none;
        background: none;
        font-size: 65px;
        font-weight: 500;
        padding-top: 35px;
        padding-bottom: 35px;
      }
      QPushButton:checked {
        color: white;
      }
    )");

    nav_btns->addButton(btn);
    sidebar_layout->addWidget(btn, 0, Qt::AlignRight);

    panel->setContentsMargins(50, 25, 50, 25);

    ScrollView *panel_frame = new ScrollView(panel, this);
    panel_widget->addWidget(panel_frame);

    QObject::connect(btn, &QPushButton::released, [=, w = panel_frame]() {
      panel_widget->setCurrentWidget(w);
    });
  }
  sidebar_layout->setContentsMargins(50, 50, 100, 50);

  // main settings layout, sidebar + main panel
  QHBoxLayout *settings_layout = new QHBoxLayout();

  sidebar_widget = new QWidget;
  sidebar_widget->setLayout(sidebar_layout);
  sidebar_widget->setFixedWidth(500);
  settings_layout->addWidget(sidebar_widget);
  settings_layout->addWidget(panel_widget);

  setLayout(settings_layout);
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

void SettingsWindow::hideEvent(QHideEvent *event){
#ifdef QCOM
  HardwareEon::close_activities();
#endif

  // TODO: this should be handled by the Dialog classes
  QList<QWidget*> children = findChildren<QWidget *>();
  for(auto &w : children){
    if(w->metaObject()->superClass()->className() == QString("QDialog")){
      w->close();
    }
  }
}
