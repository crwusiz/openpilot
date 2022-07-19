#include "selfdrive/ui/qt/offroad/settings.h"

#include <cassert>
#include <cmath>
#include <string>

#include <QDebug>

#include "selfdrive/ui/qt/offroad/networking.h"

#ifdef ENABLE_MAPS
#include "selfdrive/ui/qt/maps/map_settings.h"
#endif

#include "common/params.h"
#include "common/watchdog.h"
#include "common/util.h"
#include "system/hardware/hw.h"
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
#include <QProcess>

TogglesPanel::TogglesPanel(SettingsWindow *parent) : ListWidget(parent) {
  // param, title, desc, icon
  std::vector<std::tuple<QString, QString, QString, QString>> toggles{
    {
      "OpenpilotEnabledToggle",
      tr("Enable openpilot"),
      tr("Use the openpilot system for adaptive cruise control and lane keep driver assistance. Your attention is required at all times to use this feature. Changing this setting takes effect when the car is powered off."),
      "../assets/offroad/icon_openpilot.png",
    },
    {
      "IsRHD",
      tr("Enable Right-Hand Drive"),
      tr("Allow openpilot to obey left-hand traffic conventions and perform driver monitoring on right driver seat."),
      "../assets/offroad/icon_openpilot_mirrored.png",
    },
    {
      "IsMetric",
      tr("Use Metric System"),
      tr("Display speed in km/h instead of mph."),
      "../assets/offroad/icon_metric.png",
    },
    {
      "IsLdwEnabled",
      tr("Enable Lane Departure Warnings"),
      tr("Receive alerts to steer back into the lane when your vehicle drifts over a detected lane line without a turn signal activated while driving over 31 mph (50 km/h)."),
      "../assets/offroad/icon_ldws.png",
    },
    {
     "AutoLaneChangeEnabled",
     tr("Enable AutoLaneChange"),
     tr("Operation of the turn signal at 60㎞/h speed will result in a short change of the vehicle"),
     "../assets/offroad/icon_lca.png",
    },
    {
      "RecordFront",
      tr("Record and Upload Driver Camera"),
      tr("Upload data from the driver facing camera and help improve the driver monitoring algorithm."),
      "../assets/offroad/icon_monitoring.png",
    },
    {
      "EndToEndToggle",
      tr("Disable use of lanelines"),
      tr("In this mode openpilot will ignore lanelines and just drive how it thinks a human would."),
      "../assets/offroad/icon_road.png",
    },
    {
      "DisengageOnAccelerator",
      tr("Disengage On Accelerator Pedal"),
      tr("When enabled, pressing the accelerator pedal will disengage openpilot."),
      "../assets/offroad/icon_disengage_on_accelerator.svg",
    },
  };

  Params params;

#ifdef ENABLE_MAPS
  if (!params.getBool("NavDisable")) {
    toggles.push_back({
      "NavSettingTime24h",
      tr("Show ETA in 24h format"),
      tr("Use 24h format instead of am/pm"),
      "../assets/offroad/icon_metric.png",
    }),
    toggles.push_back({
      "NavSettingLeftSide",
      tr("Show Map on Left Side of UI"),
      tr("Show map on left side when in split screen view."),
      "../assets/offroad/icon_road.png",
    });
  }
#endif

  if (params.getBool("DisableRadar_Allow")) {
    toggles.push_back({
      "DisableRadar",
      tr("openpilot Longitudinal Control"),
      tr("openpilot will disable the car's radar and will take over control of gas and brakes. Warning: this disables AEB!"),
      "../assets/offroad/icon_speed_limit.png",
    });
  }

  for (auto &[param, title, desc, icon] : toggles) {
    auto toggle = new ParamControl(param, title, desc, icon, this);
    bool locked = params.getBool((param + "Lock").toStdString());
    toggle->setEnabled(!locked);
    addItem(toggle);
  }
}

DevicePanel::DevicePanel(SettingsWindow *parent) : ListWidget(parent) {
  setSpacing(20);
  addItem(new LabelControl(tr("Dongle ID"), getDongleId().value_or(tr("N/A"))));
  addItem(new LabelControl(tr("Serial"), params.get("HardwareSerial").c_str()));

  // offroad-only buttons

  auto dcamBtn = new ButtonControl(tr("Driver Camera"), tr("PREVIEW"),
                                   tr("Preview the driver facing camera to ensure that driver monitoring has good visibility. (vehicle must be off)"));
  connect(dcamBtn, &ButtonControl::clicked, [=]() { emit showDriverView(); });
  addItem(dcamBtn);

  auto resetCalibBtn = new ButtonControl(tr("Reset Calibration"), tr("RESET"), "");
  connect(resetCalibBtn, &ButtonControl::showDescription, this, &DevicePanel::updateCalibDescription);
  connect(resetCalibBtn, &ButtonControl::clicked, [&]() {
    if (ConfirmationDialog::confirm(tr("Are you sure you want to reset calibration?"), this)) {
      params.remove("CalibrationParams");
    }
  });
  addItem(resetCalibBtn);

  if (!params.getBool("Passive")) {
    auto retrainingBtn = new ButtonControl(tr("Review Training Guide"), tr("REVIEW"), tr("Review the rules, features, and limitations of openpilot"));
    connect(retrainingBtn, &ButtonControl::clicked, [=]() {
      if (ConfirmationDialog::confirm(tr("Are you sure you want to review the training guide?"), this)) {
        emit reviewTrainingGuide();
      }
    });
    addItem(retrainingBtn);
  }

  if (Hardware::TICI()) {
    auto regulatoryBtn = new ButtonControl(tr("Regulatory"), tr("VIEW"), "");
    connect(regulatoryBtn, &ButtonControl::clicked, [=]() {
      const std::string txt = util::read_file("../assets/offroad/fcc.html");
      RichTextDialog::alert(QString::fromStdString(txt), this);
    });
    addItem(regulatoryBtn);
  }

  auto translateBtn = new ButtonControl(tr("Change Language"), tr("CHANGE"), "");
  connect(translateBtn, &ButtonControl::clicked, [=]() {
    QMap<QString, QString> langs = getSupportedLanguages();
    QString currentLang = QString::fromStdString(Params().get("LanguageSetting"));
    QString selection = MultiOptionDialog::getSelection(tr("Select a language"), langs.keys(), langs.key(currentLang), this);
    if (!selection.isEmpty()) {
      // put language setting, exit Qt UI, and trigger fast restart
      Params().put("LanguageSetting", langs[selection].toStdString());
      qApp->exit(18);
      watchdog_kick(0);
    }
  });
  addItem(translateBtn);

/*QObject::connect(uiState(), &UIState::offroadTransition, [=](bool offroad) {
    for (auto btn : findChildren<ButtonControl *>()) {
      btn->setEnabled(offroad);
    }
  });*/

  QHBoxLayout *reset_layout = new QHBoxLayout();
  reset_layout->setSpacing(30);

  // reset calibration button
  QPushButton *reset_calib_btn = new QPushButton("Reset Calibration, LiveParameters");
  reset_calib_btn->setObjectName("reset_calib_btn");
  reset_layout->addWidget(reset_calib_btn);
  QObject::connect(reset_calib_btn, &QPushButton::released, [=]() {
    if (ConfirmationDialog::confirm(tr("Are you sure you want to reset calibration and live params?"), this)) {
      Params().remove("CalibrationParams");
      Params().remove("LiveParameters");
      emit closeSettings();
      QTimer::singleShot(1000, []() {
        Params().putBool("SoftRestartTriggered", true);
      });
    }
  });

  reset_calib_btn->setStyleSheet(R"(
    QPushButton {
      height: 120px;
      border-radius: 15px;
      color: #000000;
      background-color: #FFCCFF;
    }
    QPushButton:pressed {
      background-color: #FFC2FF;
    }
  )");

  addItem(reset_layout);

  // power buttons
  QHBoxLayout *power_layout = new QHBoxLayout();
  power_layout->setSpacing(30);

  // softreset button
  QPushButton *restart_btn = new QPushButton(tr("Soft Restart"));
  restart_btn->setObjectName("restart_btn");
  power_layout->addWidget(restart_btn);
  QObject::connect(restart_btn, &QPushButton::released, [=]() {
    emit closeSettings();
    QTimer::singleShot(1000, []() {
      Params().putBool("SoftRestartTriggered", true);
    });
  });

  QPushButton *reboot_btn = new QPushButton(tr("Reboot"));
  reboot_btn->setObjectName("reboot_btn");
  power_layout->addWidget(reboot_btn);
  QObject::connect(reboot_btn, &QPushButton::clicked, this, &DevicePanel::reboot);

  QPushButton *poweroff_btn = new QPushButton(tr("Power Off"));
  poweroff_btn->setObjectName("poweroff_btn");
  power_layout->addWidget(poweroff_btn);
  QObject::connect(poweroff_btn, &QPushButton::clicked, this, &DevicePanel::poweroff);

  if (!Hardware::PC()) {
    connect(uiState(), &UIState::offroadTransition, poweroff_btn, &QPushButton::setVisible);
  }

  setStyleSheet(R"(
    #restart_btn { height: 120px; border-radius: 15px; background-color: #2C2CE2; }
    #restart_btn:pressed { background-color: #2424FF; }
    #reboot_btn { height: 120px; border-radius: 15px; background-color: #2CE22C; }
    #reboot_btn:pressed { background-color: #24FF24; }
    #poweroff_btn { height: 120px; border-radius: 15px; background-color: #E22C2C; }
    #poweroff_btn:pressed { background-color: #FF2424; }
  )");
  addItem(power_layout);
}

void DevicePanel::updateCalibDescription() {
  QString desc =
      tr("openpilot requires the device to be mounted within 4° left or right and "
         "within 5° up or 8° down. openpilot is continuously calibrating, resetting is rarely required.");
  std::string calib_bytes = Params().get("CalibrationParams");
  if (!calib_bytes.empty()) {
    try {
      AlignedBuffer aligned_buf;
      capnp::FlatArrayMessageReader cmsg(aligned_buf.align(calib_bytes.data(), calib_bytes.size()));
      auto calib = cmsg.getRoot<cereal::Event>().getLiveCalibration();
      if (calib.getCalStatus() != 0) {
        double pitch = calib.getRpyCalib()[1] * (180 / M_PI);
        double yaw = calib.getRpyCalib()[2] * (180 / M_PI);
        desc += tr(" Your device is pointed %1° %2 and %3° %4.")
                    .arg(QString::number(std::abs(pitch), 'g', 1), pitch > 0 ? tr("down") : tr("up"),
                         QString::number(std::abs(yaw), 'g', 1), yaw > 0 ? tr("left") : tr("right"));
      }
    } catch (kj::Exception) {
      qInfo() << "invalid CalibrationParams";
    }
  }
  qobject_cast<ButtonControl *>(sender())->setDescription(desc);
}

void DevicePanel::reboot() {
  if (!uiState()->engaged()) {
    if (ConfirmationDialog::confirm(tr("Are you sure you want to reboot?"), this)) {
      // Check engaged again in case it changed while the dialog was open
      if (!uiState()->engaged()) {
        Params().putBool("DoReboot", true);
      }
    }
  } else {
    ConfirmationDialog::alert(tr("Disengage to Reboot"), this);
  }
}

void DevicePanel::poweroff() {
  if (!uiState()->engaged()) {
    if (ConfirmationDialog::confirm(tr("Are you sure you want to power off?"), this)) {
      // Check engaged again in case it changed while the dialog was open
      if (!uiState()->engaged()) {
        Params().putBool("DoShutdown", true);
      }
    }
  } else {
    ConfirmationDialog::alert(tr("Disengage to Power Off"), this);
  }
}

SoftwarePanel::SoftwarePanel(QWidget* parent) : ListWidget(parent) {
  gitRemoteLbl = new LabelControl(tr("Git Remote"));
  gitBranchLbl = new LabelControl(tr("Git Branch"));
  gitCommitLbl = new LabelControl(tr("Git Commit"));
  osVersionLbl = new LabelControl(tr("OS Version"));
  versionLbl = new LabelControl(tr("Version"), "", QString::fromStdString(params.get("ReleaseNotes")).trimmed());
  lastUpdateLbl = new LabelControl(tr("Last Update Check"), "", tr("The last time openpilot successfully checked for an update. The updater only runs while the car is off."));
  updateBtn = new ButtonControl(tr("Check for Update"), "");
  connect(updateBtn, &ButtonControl::clicked, [=]() {
    if (params.getBool("IsOffroad")) {
      fs_watch->addPath(QString::fromStdString(params.getParamPath("LastUpdateTime")));
      fs_watch->addPath(QString::fromStdString(params.getParamPath("UpdateFailedCount")));
      updateBtn->setText(tr("CHECKING"));
      updateBtn->setEnabled(false);
    }
    std::system("pkill -1 -f selfdrive.updated");
  });


  auto uninstallBtn = new ButtonControl(tr("Uninstall %1").arg(getBrand()), tr("UNINSTALL"));
  connect(uninstallBtn, &ButtonControl::clicked, [&]() {
    if (ConfirmationDialog::confirm(tr("Are you sure you want to uninstall?"), this)) {
      params.putBool("DoUninstall", true);
    }
  });
  connect(uiState(), &UIState::offroadTransition, uninstallBtn, &QPushButton::setEnabled);

  //QWidget *widgets[] = {versionLbl, gitRemoteLbl, gitBranchLbl, gitCommitLbl, lastUpdateLbl, updateBtn, osVersionLbl, uninstallBtn};
  QWidget *widgets[] = {versionLbl, gitRemoteLbl, gitBranchLbl, gitCommitLbl, osVersionLbl, uninstallBtn};
  for (QWidget* w : widgets) {
    addItem(w);
  }

  fs_watch = new QFileSystemWatcher(this);
  QObject::connect(fs_watch, &QFileSystemWatcher::fileChanged, [=](const QString path) {
    if (path.contains("UpdateFailedCount") && std::atoi(params.get("UpdateFailedCount").c_str()) > 0) {
      lastUpdateLbl->setText(tr("failed to fetch update"));
      updateBtn->setText(tr("CHECK"));
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
  updateBtn->setText(tr("CHECK"));
  updateBtn->setEnabled(true);
  gitRemoteLbl->setText(QString::fromStdString(params.get("GitRemote").substr(19)));
  gitBranchLbl->setText(QString::fromStdString(params.get("GitBranch")));
  gitCommitLbl->setText(QString::fromStdString(params.get("GitCommit")).left(7));
  osVersionLbl->setText(QString::fromStdString(Hardware::get_os_version()).trimmed());
}

static QStringList get_list(const char* path) {
  QStringList stringList;
  QFile textFile(path);
  if (textFile.open(QIODevice::ReadOnly)) {
    QTextStream textStream(&textFile);
    while (true) {
      QString line = textStream.readLine();
      if (line.isNull()) {
        break;
      } else {
        stringList.append(line);
      }
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
  QPushButton *close_btn = new QPushButton(tr("Back"));
  close_btn->setStyleSheet(R"(
    QPushButton {
      font-size: 50px;
      font-weight: bold;
      margin: 0px;
      padding: 15px;
      border-width: 0;
      border-radius: 30px;
      color: #FFFFFF;
      background-color: #444444;
    }
    QPushButton:pressed {
      background-color: #3B3B3B;
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
    {tr("Device"), device},
    {tr("Network"), new Networking(this)},
    {tr("Toggles"), new TogglesPanel(this)},
    {tr("Software"), new SoftwarePanel(this)},
    {tr("Community"), new CommunityPanel(this)},
  };

#ifdef ENABLE_MAPS
  if (!params.getBool("NavDisable")) {
    auto map_panel = new MapPanel(this);
    panels.push_back({tr("Navigation"), map_panel});
    QObject::connect(map_panel, &MapPanel::closeSettings, this, &SettingsWindow::closeSettings);
  }
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

    const int lr_margin = name != tr("Network") ? 50 : 0;  // Network panel handles its own margins
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


// Community Panel
CommunityPanel::CommunityPanel(QWidget* parent) : QWidget(parent) {
  main_layout = new QStackedLayout(this);
  QWidget* homeScreen = new QWidget(this);
  QVBoxLayout* vlayout = new QVBoxLayout(homeScreen);
  vlayout->setContentsMargins(0, 20, 0, 20);

  QString selected = QString::fromStdString(Params().get("SelectedCar"));

  QPushButton* selectcar_btn = new QPushButton(selected.length() ? selected : tr("Select your car"));
  selectcar_btn->setObjectName("selectcar_btn");
  selectcar_btn->setStyleSheet("margin-right: 30px;");
  //selectcar_btn->setFixedSize(400, 100);
  connect(selectcar_btn, &QPushButton::clicked, [=]() { main_layout->setCurrentWidget(selectCar); });
  vlayout->addSpacing(10);
  vlayout->addWidget(selectcar_btn, 0, Qt::AlignRight);
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
     selectcar_btn->setText(selected.length() ? selected : tr("Select your car"));
     main_layout->setCurrentWidget(homeScreen);
  });
  main_layout->addWidget(selectCar);

  QPalette pal = palette();
  pal.setColor(QPalette::Background, QColor(0x29, 0x29, 0x29));
  setAutoFillBackground(true);
  setPalette(pal);

  setStyleSheet(R"(
    #back_btn {
      font-size: 50px;
      margin: 0px;
      padding: 15px;
      border-width: 0;
      border-radius: 30px;
      color: #FFFFFF;
      background-color: #444444;
    }
    #back_btn:pressed {
      background-color: #3B3B3B;
    }
    #selectcar_btn {
      font-size: 50px;
      margin: 0px;
      padding: 15px;
      border-width: 0;
      border-radius: 30px;
      color: #FFFFFF;
      background-color: #2C2CE2;
    }
    #selectcar_btn:pressed {
      background-color: #2424FF;
    }
  )");

  toggleLayout->addWidget(new LateralControlSelect());
  toggleLayout->addWidget(new MfcSelect());
  toggleLayout->addWidget(new AebSelect());
  toggleLayout->addWidget(new LongControlSelect());
  toggleLayout->addWidget(horizontal_line());

  QList<ParamControl*> toggles;
  toggles.append(new ParamControl("PutPrebuilt",
                                  tr("Prebuilt Enable"),
                                  tr("Create prebuilt files to speed bootup"),
                                  "../assets/offroad/icon_addon.png", this));
  toggles.append(new ParamControl("LoggerDisable",
                                  tr("Logger Disable"),
                                  tr("Disable Logger is Reduce system load"),
                                  "../assets/offroad/icon_addon.png", this));
  toggles.append(new ParamControl("NavDisable",
                                  tr("Navigation Disable"),
                                  tr("Navigation Function not use"),
                                  "../assets/offroad/icon_addon.png", this));
  toggles.append(new ParamControl("NewRadarInterface",
                                  tr("New radar interface Enable"),
                                  tr("Some newer car New radar interface"),
                                  "../assets/offroad/icon_road.png", this));
  for (ParamControl *toggle : toggles) {
    if (main_layout->count() != 0) {
    }
    toggleLayout->addWidget(toggle);
  }
  toggleLayout->addWidget(horizontal_line());

  auto gitpull_btn = new ButtonControl("Git Fetch and Reset", tr("RUN"));
  QObject::connect(gitpull_btn, &ButtonControl::clicked, [=]() {
    if (ConfirmationDialog::confirm(tr("Process?"), this)){
      QProcess::execute("/data/openpilot/scripts/gitpull.sh");
    }
  });
  toggleLayout->addWidget(gitpull_btn);

  auto pandaflash_btn = new ButtonControl("Panda Flash", tr("RUN"));
  QObject::connect(pandaflash_btn, &ButtonControl::clicked, [=]() {
    if (ConfirmationDialog::confirm(tr("Process?"), this)){
      QProcess::execute("/data/openpilot/panda/board/flash.sh");
    }
  });
  toggleLayout->addWidget(pandaflash_btn);

  auto pandaflashh7_btn = new ButtonControl("RED Panda Flash", tr("RUN"));
  QObject::connect(pandaflashh7_btn, &ButtonControl::clicked, [=]() {
    if (ConfirmationDialog::confirm(tr("Process?"), this)){
      QProcess::execute("/data/openpilot/panda/board/flash_h7.sh");
    }
  });
  toggleLayout->addWidget(pandaflashh7_btn);

  auto pandarecover_btn = new ButtonControl("Panda Recover", tr("RUN"));
  QObject::connect(pandarecover_btn, &ButtonControl::clicked, [=]() {
    if (ConfirmationDialog::confirm(tr("Process?"), this)){
      QProcess::execute("/data/openpilot/panda/board/recover.sh");
    }
  });
  toggleLayout->addWidget(pandarecover_btn);

  auto pandarecoverh7_btn = new ButtonControl("RED Panda Recover", tr("RUN"));
  QObject::connect(pandarecoverh7_btn, &ButtonControl::clicked, [=]() {
    if (ConfirmationDialog::confirm(tr("Process?"), this)){
      QProcess::execute("/data/openpilot/panda/board/recover_h7.sh");
    }
  });
  toggleLayout->addWidget(pandarecoverh7_btn);
}

SelectCar::SelectCar(QWidget* parent): QWidget(parent) {
  QVBoxLayout* main_layout = new QVBoxLayout(this);
  main_layout->setMargin(20);
  main_layout->setSpacing(20);

  // Back button
  QPushButton* back = new QPushButton(tr("Back"));
  back->setObjectName("back_btn");
  back->setFixedSize(300, 100);
  connect(back, &QPushButton::clicked, [=]() { emit backPress(); });
  main_layout->addWidget(back, 0, Qt::AlignLeft);
  QListWidget* list = new QListWidget(this);
  list->setStyleSheet("QListView {padding: 40px; background-color: #393939; border-radius: 15px; height: 140px;} QListView::item{height: 100px}");
  //list->setAttribute(Qt::WA_AcceptTouchEvents, true);
  QScroller::grabGesture(list->viewport(), QScroller::LeftMouseButtonGesture);
  list->setVerticalScrollMode(QAbstractItemView::ScrollPerPixel);
  list->addItem(tr("Select car not use"));
  QStringList items = get_list("/data/params/d/SupportedCars");
  list->addItems(items);
  list->setCurrentRow(0);
  QString selected = QString::fromStdString(Params().get("SelectedCar"));

  int index = 0;
  for (QString item : items) {
    if (selected == item) {
      list->setCurrentRow(index + 1);
      break;
    }
    index++;
  }

  QObject::connect(list, QOverload<QListWidgetItem*>::of(&QListWidget::itemClicked),
    [=](QListWidgetItem* item){

    if (list->currentRow() == 0) {
      Params().remove("SelectedCar");
    } else {
      Params().put("SelectedCar", list->currentItem()->text().toStdString());
    }
    emit selectedCar();
    });
  main_layout->addWidget(list);
}

//LateralControlSelect
LateralControlSelect::LateralControlSelect() : AbstractControl("LateralControl [√]", tr("LateralControl Select (Pid/Indi/Lqr/Torque)"), "../assets/offroad/icon_logic.png") {
  label.setAlignment(Qt::AlignVCenter|Qt::AlignRight);
  label.setStyleSheet("color: #e0e879");
  hlayout->addWidget(&label);

  btnminus.setStyleSheet(R"(
    padding: 0;
    border-radius: 50px;
    font-size: 45px;
    font-weight: 500;
    color: #E4E4E4;
    background-color: #393939;
  )");
  btnplus.setStyleSheet(R"(
    padding: 0;
    border-radius: 50px;
    font-size: 45px;
    font-weight: 500;
    color: #E4E4E4;
    background-color: #393939;
  )");
  btnminus.setFixedSize(120, 100);
  btnplus.setFixedSize(120, 100);

  hlayout->addWidget(&btnminus);
  hlayout->addWidget(&btnplus);

  QObject::connect(&btnminus, &QPushButton::released, [=]() {
    auto str = QString::fromStdString(Params().get("LateralControlSelect"));
    int latcontrol = str.toInt();
    latcontrol = latcontrol - 1;
    if (latcontrol <= 0 ) {
      latcontrol = 0;
    }
    QString latcontrols = QString::number(latcontrol);
    Params().put("LateralControlSelect", latcontrols.toStdString());
    refresh();
  });

  QObject::connect(&btnplus, &QPushButton::released, [=]() {
    auto str = QString::fromStdString(Params().get("LateralControlSelect"));
    int latcontrol = str.toInt();
    latcontrol = latcontrol + 1;
    if (latcontrol >= 3 ) {
      latcontrol = 3;
    }
    QString latcontrols = QString::number(latcontrol);
    Params().put("LateralControlSelect", latcontrols.toStdString());
    refresh();
  });
  refresh();
}

void LateralControlSelect::refresh() {
  QString latcontrol = QString::fromStdString(Params().get("LateralControlSelect"));
  if (latcontrol == "0") {
    label.setText(QString::fromStdString("Pid"));
  } else if (latcontrol == "1") {
    label.setText(QString::fromStdString("Indi"));
  } else if (latcontrol == "2") {
    label.setText(QString::fromStdString("Lqr"));
  } else if (latcontrol == "3") {
    label.setText(QString::fromStdString("Torque"));
  }
  btnminus.setText("◀");
  btnplus.setText("▶");
}

//MfcSelect
MfcSelect::MfcSelect() : AbstractControl("MFC [√]", tr("MFC Camera Select (Lkas/Ldws/Lfa)"), "../assets/offroad/icon_mfc.png") {
  label.setAlignment(Qt::AlignVCenter|Qt::AlignRight);
  label.setStyleSheet("color: #e0e879");
  hlayout->addWidget(&label);

  btnminus.setStyleSheet(R"(
    padding: 0;
    border-radius: 50px;
    font-size: 45px;
    font-weight: 500;
    color: #E4E4E4;
    background-color: #393939;
  )");
  btnplus.setStyleSheet(R"(
    padding: 0;
    border-radius: 50px;
    font-size: 45px;
    font-weight: 500;
    color: #E4E4E4;
    background-color: #393939;
  )");
  btnminus.setFixedSize(120, 100);
  btnplus.setFixedSize(120, 100);

  hlayout->addWidget(&btnminus);
  hlayout->addWidget(&btnplus);

  QObject::connect(&btnminus, &QPushButton::released, [=]() {
    auto str = QString::fromStdString(Params().get("MfcSelect"));
    int mfc = str.toInt();
    mfc = mfc - 1;
    if (mfc <= 0 ) {
      mfc = 0;
    }
    QString mfcs = QString::number(mfc);
    Params().put("MfcSelect", mfcs.toStdString());
    refresh();
  });

  QObject::connect(&btnplus, &QPushButton::released, [=]() {
    auto str = QString::fromStdString(Params().get("MfcSelect"));
    int mfc = str.toInt();
    mfc = mfc + 1;
    if (mfc >= 2 ) {
      mfc = 2;
    }
    QString mfcs = QString::number(mfc);
    Params().put("MfcSelect", mfcs.toStdString());
    refresh();
  });
  refresh();
}

void MfcSelect::refresh() {
  QString mfc = QString::fromStdString(Params().get("MfcSelect"));
  if (mfc == "0") {
    label.setText(QString::fromStdString("Lkas"));
  } else if (mfc == "1") {
    label.setText(QString::fromStdString("Ldws"));
  } else if (mfc == "2") {
    label.setText(QString::fromStdString("Lfa"));
  }
  btnminus.setText("◀");
  btnplus.setText("▶");
}

//AebSelect
AebSelect::AebSelect() : AbstractControl("AEB [√]", tr("AEB Signal Select (Scc12/Fca11)"), "../assets/offroad/icon_aeb.png") {
  label.setAlignment(Qt::AlignVCenter|Qt::AlignRight);
  label.setStyleSheet("color: #e0e879");
  hlayout->addWidget(&label);

  btnminus.setStyleSheet(R"(
    padding: 0;
    border-radius: 50px;
    font-size: 45px;
    font-weight: 500;
    color: #E4E4E4;
    background-color: #393939;
  )");
  btnplus.setStyleSheet(R"(
    padding: 0;
    border-radius: 50px;
    font-size: 45px;
    font-weight: 500;
    color: #E4E4E4;
    background-color: #393939;
  )");
  btnminus.setFixedSize(120, 100);
  btnplus.setFixedSize(120, 100);

  hlayout->addWidget(&btnminus);
  hlayout->addWidget(&btnplus);

  QObject::connect(&btnminus, &QPushButton::released, [=]() {
    auto str = QString::fromStdString(Params().get("AebSelect"));
    int aeb = str.toInt();
    aeb = aeb - 1;
    if (aeb <= 0 ) {
      aeb = 0;
    }
    QString aebs = QString::number(aeb);
    Params().put("AebSelect", aebs.toStdString());
    refresh();
  });

  QObject::connect(&btnplus, &QPushButton::released, [=]() {
    auto str = QString::fromStdString(Params().get("AebSelect"));
    int aeb = str.toInt();
    aeb = aeb + 1;
    if (aeb >= 1 ) {
      aeb = 1;
    }
    QString aebs = QString::number(aeb);
    Params().put("AebSelect", aebs.toStdString());
    refresh();
  });
  refresh();
}

void AebSelect::refresh() {
  QString aeb = QString::fromStdString(Params().get("AebSelect"));
  if (aeb == "0") {
    label.setText(QString::fromStdString("Scc12"));
  } else if (aeb == "1") {
    label.setText(QString::fromStdString("Fca11"));
  }
  btnminus.setText("◀");
  btnplus.setText("▶");
}

//LongControlSelect
LongControlSelect::LongControlSelect() : AbstractControl("LongControl [√]", tr("LongControl Select (Mad/Mad+Long)"), "../assets/offroad/icon_long.png") {
  label.setAlignment(Qt::AlignVCenter|Qt::AlignRight);
  label.setStyleSheet("color: #e0e879");
  hlayout->addWidget(&label);

  btnminus.setStyleSheet(R"(
    padding: 0;
    border-radius: 50px;
    font-size: 45px;
    font-weight: 500;
    color: #E4E4E4;
    background-color: #393939;
  )");
  btnplus.setStyleSheet(R"(
    padding: 0;
    border-radius: 50px;
    font-size: 45px;
    font-weight: 500;
    color: #E4E4E4;
    background-color: #393939;
  )");
  btnminus.setFixedSize(120, 100);
  btnplus.setFixedSize(120, 100);

  hlayout->addWidget(&btnminus);
  hlayout->addWidget(&btnplus);

  QObject::connect(&btnminus, &QPushButton::released, [=]() {
    auto str = QString::fromStdString(Params().get("LongControlSelect"));
    int longcontrol = str.toInt();
    longcontrol = longcontrol - 1;
    if (longcontrol <= 0 ) {
      longcontrol = 0;
    }
    QString longcontrols = QString::number(longcontrol);
    Params().put("LongControlSelect", longcontrols.toStdString());
    refresh();
  });

  QObject::connect(&btnplus, &QPushButton::released, [=]() {
    auto str = QString::fromStdString(Params().get("LongControlSelect"));
    int longcontrol = str.toInt();
    longcontrol = longcontrol + 1;
    if (longcontrol >= 1 ) {
      longcontrol = 1;
    }
    QString longcontrols = QString::number(longcontrol);
    Params().put("LongControlSelect", longcontrols.toStdString());
    refresh();
  });
  refresh();
}

void LongControlSelect::refresh() {
  QString longcontrol = QString::fromStdString(Params().get("LongControlSelect"));
  if (longcontrol == "0") {
    label.setText(QString::fromStdString("Mad"));
  } else if (longcontrol == "1") {
    label.setText(QString::fromStdString("Mad+Long"));
  }
  btnminus.setText("◀");
  btnplus.setText("▶");
}
