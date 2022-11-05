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
#include "selfdrive/ui/qt/widgets/input.h"

#include <QComboBox>
#include <QAbstractItemView>
#include <QScroller>
#include <QListView>
#include <QListWidget>
#include <QProcess>

TogglesPanel::TogglesPanel(SettingsWindow *parent) : ListWidget(parent) {
  // param, title, desc, icon, confirm
  std::vector<std::tuple<QString, QString, QString, QString, bool>> toggle_defs{
    {
      "OpenpilotEnabledToggle",
      tr("Enable openpilot"),
      tr("Use the openpilot system for adaptive cruise control and lane keep driver assistance. Your attention is required at all times to use this feature. Changing this setting takes effect when the car is powered off."),
      "../assets/offroad/icon_openpilot.png",
      false,
    },
    {
      "IsMetric",
      tr("Use Metric System"),
      tr("Display speed in km/h instead of mph."),
      "../assets/offroad/icon_metric.png",
      false,
    },
    {
      "IsLdwEnabled",
      tr("Enable Lane Departure Warnings"),
      tr("Receive alerts to steer back into the lane when your vehicle drifts over a detected lane line without a turn signal activated while driving over 31 mph (50 km/h)."),
      "../assets/offroad/icon_ldws.png",
      false,
    },
    {
      "AutoLaneChangeEnabled",
      tr("Enable AutoLaneChange"),
      tr("Operation of the turn signal at 60㎞/h speed will result in a short change of the vehicle"),
      "../assets/offroad/icon_lca.png",
      false,
    },
    {
      "RecordFront",
      tr("Record and Upload Driver Camera"),
      tr("Upload data from the driver facing camera and help improve the driver monitoring algorithm."),
      "../assets/offroad/icon_monitoring.png",
      false,
    },
    {
      "DisengageOnAccelerator",
      tr("Disengage On Accelerator Pedal"),
      tr("When enabled, pressing the accelerator pedal will disengage openpilot."),
      "../assets/offroad/icon_disengage_on_accelerator.svg",
      false,
    },
    {
      "EndToEndLong",
      tr("🌮 End-to-end longitudinal (extremely alpha) 🌮"),
      "",
      "../assets/offroad/icon_road.png",
      false,
    },
    {
      "ExperimentalLongitudinalEnabled",
      tr("Experimental openpilot longitudinal control"),
      tr("<b>WARNING: openpilot longitudinal control is experimental for this car and will disable AEB.</b>"),
      "../assets/offroad/icon_speed_limit.png",
      true,
    },
#ifdef ENABLE_MAPS
    {
      "NavSettingTime24h",
      tr("Show ETA in 24h Format"),
      tr("Use 24h format instead of am/pm"),
      "../assets/offroad/icon_metric.png",
      false,
    },
    {
      "NavSettingLeftSide",
      tr("Show Map on Left Side of UI"),
      tr("Show map on left side when in split screen view."),
      "../assets/offroad/icon_road.png",
      false,
    },
#endif

  };

  for (auto &[param, title, desc, icon, confirm] : toggle_defs) {
    auto toggle = new ParamControl(param, title, desc, icon, confirm, this);

    bool locked = params.getBool((param + "Lock").toStdString());
    toggle->setEnabled(!locked);
    addItem(toggle);
    toggles[param.toStdString()] = toggle;
  }

  connect(toggles["ExperimentalLongitudinalEnabled"], &ToggleControl::toggleFlipped, [=]() {
    updateToggles();
  });
}

void TogglesPanel::showEvent(QShowEvent *event) {
  updateToggles();
}

void TogglesPanel::updateToggles() {
  auto e2e_toggle = toggles["EndToEndLong"];
  auto op_long_toggle = toggles["ExperimentalLongitudinalEnabled"];
  const QString e2e_description = tr("Let the driving model control the gas and brakes. openpilot will drive as it thinks a human would. Super experimental.");

  auto cp_bytes = params.get("CarParamsPersistent");
  if (!cp_bytes.empty()) {
    AlignedBuffer aligned_buf;
    capnp::FlatArrayMessageReader cmsg(aligned_buf.align(cp_bytes.data(), cp_bytes.size()));
    cereal::CarParams::Reader CP = cmsg.getRoot<cereal::CarParams>();

    if (!CP.getExperimentalLongitudinalAvailable()) {
      params.remove("ExperimentalLongitudinalEnabled");
    }
    op_long_toggle->setVisible(CP.getExperimentalLongitudinalAvailable());

    const bool op_long = CP.getOpenpilotLongitudinalControl() && !CP.getExperimentalLongitudinalAvailable();
    const bool exp_long_enabled = CP.getExperimentalLongitudinalAvailable() && params.getBool("ExperimentalLongitudinalEnabled");
    if (op_long || exp_long_enabled) {
      // normal description and toggle
      e2e_toggle->setEnabled(true);
      e2e_toggle->setDescription(e2e_description);
    } else {
      // no long for now
      e2e_toggle->setEnabled(false);
      params.remove("EndToEndLong");

      const QString no_long = tr("openpilot longitudinal control is not currently available for this car.");
      const QString exp_long = tr("Enable experimental longitudinal control to enable this.");
      e2e_toggle->setDescription("<b>" + (CP.getExperimentalLongitudinalAvailable() ? exp_long : no_long) + "</b><br><br>" + e2e_description);
    }

    e2e_toggle->refresh();
  } else {
    e2e_toggle->setDescription(e2e_description);
    op_long_toggle->setVisible(false);
  }
}

DevicePanel::DevicePanel(SettingsWindow *parent) : ListWidget(parent) {
  setSpacing(7);
  addItem(new LabelControl(tr("Dongle ID"), getDongleId().value_or(tr("N/A"))));
  addItem(new LabelControl(tr("Serial"), params.get("HardwareSerial").c_str()));

  // offroad-only buttons

  auto dcamBtn = new ButtonControl(tr("Driver Camera"), tr("PREVIEW"),
                                   tr("Preview the driver facing camera to ensure that driver monitoring has good visibility. (vehicle must be off)"));
  connect(dcamBtn, &ButtonControl::clicked, [=]() { emit showDriverView(); });
  addItem(dcamBtn);

  auto resetCalibBtn = new ButtonControl(tr("Reset Calibration"), tr("RESET"), "");
  connect(resetCalibBtn, &ButtonControl::showDescriptionEvent, this, &DevicePanel::updateCalibDescription);
  connect(resetCalibBtn, &ButtonControl::clicked, [&]() {
    if (ConfirmationDialog::confirm(tr("Are you sure you want to reset calibration?"), tr("Reset"), this)) {
      params.remove("CalibrationParams");
    }
  });
  addItem(resetCalibBtn);

  if (!params.getBool("Passive")) {
    auto retrainingBtn = new ButtonControl(tr("Review Training Guide"), tr("REVIEW"), tr("Review the rules, features, and limitations of openpilot"));
    connect(retrainingBtn, &ButtonControl::clicked, [=]() {
      if (ConfirmationDialog::confirm(tr("Are you sure you want to review the training guide?"), tr("Review"), this)) {
        emit reviewTrainingGuide();
      }
    });
    addItem(retrainingBtn);
  }

/*  if (Hardware::TICI()) {
    auto regulatoryBtn = new ButtonControl(tr("Regulatory"), tr("VIEW"), "");
    connect(regulatoryBtn, &ButtonControl::clicked, [=]() {
      const std::string txt = util::read_file("../assets/offroad/fcc.html");
      ConfirmationDialog::rich(QString::fromStdString(txt), this);
    });
    addItem(regulatoryBtn);
  }*/

  auto translateBtn = new ButtonControl(tr("Change Language"), tr("CHANGE"), "");
  connect(translateBtn, &ButtonControl::clicked, [=]() {
    QMap<QString, QString> langs = getSupportedLanguages();
    QString selection = MultiOptionDialog::getSelection(tr("Select a language"), langs.keys(), langs.key(uiState()->language), this);
    if (!selection.isEmpty()) {
      // put language setting, exit Qt UI, and trigger fast restart
      Params().put("LanguageSetting", langs[selection].toStdString());
      qApp->exit(18);
      watchdog_kick(0);
    }
  });
  addItem(translateBtn);

  QObject::connect(uiState(), &UIState::offroadTransition, [=](bool offroad) {
    for (auto btn : findChildren<ButtonControl *>()) {
      btn->setEnabled(offroad);
    }
  });

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
    if (ConfirmationDialog::confirm(tr("Are you sure you want to reboot?"), tr("Reboot"), this)) {
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
    if (ConfirmationDialog::confirm(tr("Are you sure you want to power off?"), tr("Power Off"), this)) {
      // Check engaged again in case it changed while the dialog was open
      if (!uiState()->engaged()) {
        Params().putBool("DoShutdown", true);
      }
    }
  } else {
    ConfirmationDialog::alert(tr("Disengage to Power Off"), this);
  }
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
  QPushButton *close_btn = new QPushButton(tr("×"));
  close_btn->setStyleSheet(R"(
    QPushButton {
      font-size: 140px;
      padding-bottom: 20px;
      font-weight: bold;
      border 1px grey solid;
      border-radius: 100px;
      background-color: #292929;
      font-weight: 400;
    }
    QPushButton:pressed {
      background-color: #3B3B3B;
    }
  )");
  close_btn->setFixedSize(200, 200);
  sidebar_layout->addSpacing(45);
  sidebar_layout->addWidget(close_btn, 0, Qt::AlignCenter);
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
  homeScreen = new QWidget(this);
  QVBoxLayout* vlayout = new QVBoxLayout(homeScreen);
  vlayout->setContentsMargins(0, 20, 0, 20);

  homeWidget = new QWidget(this);
  QVBoxLayout* communityLayout = new QVBoxLayout(homeWidget);
  homeWidget->setObjectName("homeWidget");

  ScrollView *scroller = new ScrollView(homeWidget, this);
  scroller->setVerticalScrollBarPolicy(Qt::ScrollBarAsNeeded);

  main_layout->addWidget(homeScreen);

  QString selectedManufacturer = QString::fromStdString(Params().get("SelectedManufacturer"));
  QPushButton* selectManufacturer_btn = new QPushButton(selectedManufacturer.length() ? selectedManufacturer : tr("Select your Manufacturer"));
  selectManufacturer_btn->setObjectName("selectManufacturer_btn");
  connect(selectManufacturer_btn, &QPushButton::clicked, [=]() { main_layout->setCurrentWidget(selectManufacturer); });

  selectManufacturer = new SelectManufacturer(this);
  connect(selectManufacturer, &SelectManufacturer::backPress, [=]() { main_layout->setCurrentWidget(homeScreen); });
  connect(selectManufacturer, &SelectManufacturer::selectedManufacturer, [=]() {
     QString selected = QString::fromStdString(Params().get("SelectedManufacturer"));
     selectManufacturer_btn->setText(selectedManufacturer.length() ? selectedManufacturer : tr("Select your Manufacturer"));
     main_layout->setCurrentWidget(homeScreen);
  });
  main_layout->addWidget(selectManufacturer);

  QString selectedCar = QString::fromStdString(Params().get("SelectedCar"));
  QPushButton* selectCar_btn = new QPushButton(selectedCar.length() ? selectedCar : tr("Select your car"));
  selectCar_btn->setObjectName("selectCar_btn");
  connect(selectCar_btn, &QPushButton::clicked, [=]() { main_layout->setCurrentWidget(selectCar); });

  selectCar = new SelectCar(this);
  connect(selectCar, &SelectCar::backPress, [=]() { main_layout->setCurrentWidget(homeScreen); });
  connect(selectCar, &SelectCar::selectedCar, [=]() {
     QString selected = QString::fromStdString(Params().get("SelectedCar"));
     selectCar_btn->setText(selectedCar.length() ? selectedCar : tr("Select your car"));
     main_layout->setCurrentWidget(homeScreen);
  });
  main_layout->addWidget(selectCar);

  QHBoxLayout* layoutBtn = new QHBoxLayout(homeWidget);

  layoutBtn->addWidget(selectManufacturer_btn);
  layoutBtn->addSpacing(10);
  layoutBtn->addWidget(selectCar_btn);

  vlayout->addSpacing(10);
  vlayout->addLayout(layoutBtn, 0);
  vlayout->addSpacing(10);
  vlayout->addWidget(scroller, 1);

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
    #selectCar_btn, #selectManufacturer_btn {
      font-size: 50px;
      margin: 0px;
      padding: 15px;
      border-width: 0;
      border-radius: 30px;
      color: #FFFFFF;
      background-color: #2C2CE2;
    }
    #selectCar_btn:pressed, #selectManufacturer_btn:pressed {
      background-color: #2424FF;
    }
  )");

  auto gitpull_btn = new ButtonControl("Git Fetch and Reset", tr("RUN"));
  QObject::connect(gitpull_btn, &ButtonControl::clicked, [=]() {
    if (ConfirmationDialog::confirm(tr("Process?"), this)) {
      QProcess::execute("/data/openpilot/scripts/gitpull.sh");
    }
  });
  communityLayout->addWidget(gitpull_btn);

  auto restart_btn = new ButtonControl("Restart", tr("RUN"));
  QObject::connect(restart_btn, &ButtonControl::clicked, [=]() {
    if (ConfirmationDialog::confirm(tr("Process?"), this)) {
      QProcess::execute("/data/openpilot/scripts/restart.sh");
    }
  });
  communityLayout->addWidget(restart_btn);

  auto cleardtc_btn = new ButtonControl("Clear DTC", tr("RUN"));
  QObject::connect(cleardtc_btn, &ButtonControl::clicked, [=]() {
    if (ConfirmationDialog::confirm(tr("Process?"), this)) {
      QProcess::execute("/data/openpilot/scripts/cleardtc.sh");
    }
  });
  communityLayout->addWidget(cleardtc_btn);

  auto tmuxlog_btn = new ButtonControl("Tmux error log", tr("RUN"));
  QObject::connect(tmuxlog_btn, &ButtonControl::clicked, [=]() {
    const std::string txt = util::read_file("/data/tmux_error.log");
    ConfirmationDialog::rich(QString::fromStdString(txt), this);
  });
  communityLayout->addWidget(tmuxlog_btn);

  auto pandareset_btn = new ButtonControl("Panda Reset", tr("RUN"));
  QObject::connect(pandareset_btn, &ButtonControl::clicked, [=]() {
    if (ConfirmationDialog::confirm(tr("Process?"), this)) {
      QProcess::execute("/data/openpilot/scripts/relay_reset.sh");
    }
  });
  communityLayout->addWidget(pandareset_btn);

  auto pandaflash_btn = new ButtonControl("Panda Flash", tr("RUN"));
  QObject::connect(pandaflash_btn, &ButtonControl::clicked, [=]() {
    if (ConfirmationDialog::confirm(tr("Process?"), this)) {
      QProcess::execute("/data/openpilot/panda/board/flash.sh");
    }
  });
  communityLayout->addWidget(pandaflash_btn);

  auto pandarecover_btn = new ButtonControl("Panda Recover", tr("RUN"));
  QObject::connect(pandarecover_btn, &ButtonControl::clicked, [=]() {
    if (ConfirmationDialog::confirm(tr("Process?"), this)) {
      QProcess::execute("/data/openpilot/panda/board/recover.sh");
    }
  });
  communityLayout->addWidget(pandarecover_btn);

  communityLayout->addWidget(horizontal_line());
  communityLayout->addWidget(new LateralControlSelect());
  communityLayout->addWidget(new MfcSelect());
  communityLayout->addWidget(new AebSelect());
  communityLayout->addWidget(horizontal_line());

  // add community toggle
  QList<ParamControl*> toggles;
  toggles.append(new ParamControl("PutPrebuilt",
                                  tr("Prebuilt Enable"),
                                  tr("Create prebuilt files to speed bootup"),
                                  "../assets/offroad/icon_addon.png",
                                  false, this));
  toggles.append(new ParamControl("LoggerDisable",
                                  tr("Logger Disable"),
                                  tr("Disable Logger is Reduce system load"),
                                  "../assets/offroad/icon_addon.png",
                                  false, this));
  toggles.append(new ParamControl("NavDisable",
                                  tr("Navigation Disable"),
                                  tr("Navigation Function not use"),
                                  "../assets/offroad/icon_addon.png",
                                  false, this));
  toggles.append(new ParamControl("NewRadarInterface",
                                  tr("New radar interface Enable"),
                                  tr("Some newer car New radar interface"),
                                  "../assets/offroad/icon_road.png",
                                  false, this));
  for (ParamControl *toggle : toggles) {
    if (main_layout->count() != 0) {
    }
    communityLayout->addWidget(toggle);
  }
}

SelectCar::SelectCar(QWidget* parent): QWidget(parent) {
  QVBoxLayout* main_layout = new QVBoxLayout(this);
  main_layout->setMargin(20);
  main_layout->setSpacing(20);

  // Back button
  QPushButton* back = new QPushButton(tr("Back"));
  back->setObjectName("back_btn");
  back->setFixedSize(500, 100);
  connect(back, &QPushButton::clicked, [=]() { emit backPress(); });
  main_layout->addWidget(back, 0, Qt::AlignLeft);

  QListWidget* list = new QListWidget(this);
  list->setStyleSheet("QListView {padding: 40px; background-color: #393939; border-radius: 15px; height: 140px;} QListView::item{height: 100px}");
  QScroller::grabGesture(list->viewport(), QScroller::LeftMouseButtonGesture);
  list->setVerticalScrollMode(QAbstractItemView::ScrollPerPixel);
  list->addItem(tr("Select Car not use"));
  QStringList items = get_list("/data/params/d/CarList");
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

SelectManufacturer::SelectManufacturer(QWidget* parent): QWidget(parent) {
  QVBoxLayout* main_layout = new QVBoxLayout(this);
  main_layout->setMargin(20);
  main_layout->setSpacing(20);

  // Back button
  QPushButton* back = new QPushButton(tr("Back"));
  back->setObjectName("back_btn");
  back->setFixedSize(500, 100);
  connect(back, &QPushButton::clicked, [=]() { emit backPress(); });
  main_layout->addWidget(back, 0, Qt::AlignLeft);

  QListWidget* list = new QListWidget(this);
  list->setStyleSheet("QListView {padding: 40px; background-color: #393939; border-radius: 15px; height: 140px;} QListView::item{height: 100px}");
  QScroller::grabGesture(list->viewport(), QScroller::LeftMouseButtonGesture);
  list->setVerticalScrollMode(QAbstractItemView::ScrollPerPixel);
  list->addItem(tr("Select Manufacturer not use"));

  QStringList items = {"HYUNDAI", "KIA", "GENESIS", "GM", "TOYOTA", "LEXUS", "HONDA"};
  list->addItems(items);
  list->setCurrentRow(0);
  QString selected = QString::fromStdString(Params().get("SelectedManufacturer"));

  int index = 0;
  for(QString item : items) {
    if(selected == item) {
        list->setCurrentRow(index);
        break;
    }
    index++;
  }

  QObject::connect(list, QOverload<QListWidgetItem*>::of(&QListWidget::itemClicked),
    [=](QListWidgetItem* item){

    if (list->currentRow() == 0) {
      Params().remove("SelectedManufacturer");
    } else if (list->currentRow() == 1) {
      QProcess::execute("cp -f /data/params/d/CarList_Hyundai /data/params/d/CarList");
      Params().put("SelectedManufacturer", list->currentItem()->text().toStdString());
    } else if (list->currentRow() == 2) {
      QProcess::execute("cp -f /data/params/d/CarList_Kia /data/params/d/CarList");
      Params().put("SelectedManufacturer", list->currentItem()->text().toStdString());
    } else if (list->currentRow() == 3) {
      QProcess::execute("cp -f /data/params/d/CarList_Genesis /data/params/d/CarList");
      Params().put("SelectedManufacturer", list->currentItem()->text().toStdString());
    } else if (list->currentRow() == 4) {
      QProcess::execute("cp -f /data/params/d/CarList_Gm /data/params/d/CarList");
      Params().put("SelectedManufacturer", list->currentItem()->text().toStdString());
    } else if (list->currentRow() == 5) {
      QProcess::execute("cp -f /data/params/d/CarList_Toyota /data/params/d/CarList");
      Params().put("SelectedManufacturer", list->currentItem()->text().toStdString());
    } else if (list->currentRow() == 6) {
      QProcess::execute("cp -f /data/params/d/CarList_Lexus /data/params/d/CarList");
      Params().put("SelectedManufacturer", list->currentItem()->text().toStdString());
    } else if (list->currentRow() == 7) {
      QProcess::execute("cp -f /data/params/d/CarList_Honda /data/params/d/CarList");
      Params().put("SelectedManufacturer", list->currentItem()->text().toStdString());
    }
    emit selectedManufacturer();
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
MfcSelect::MfcSelect() : AbstractControl("MFC [√]", tr("MFC Camera Select (Auto/Ldws,Lkas/Lfa)"), "../assets/offroad/icon_mfc.png") {
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
    label.setText(QString::fromStdString("Auto"));
  } else if (mfc == "1") {
    label.setText(QString::fromStdString("Ldws,Lkas"));
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
