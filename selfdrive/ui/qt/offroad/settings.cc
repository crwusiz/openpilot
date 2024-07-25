#include <cassert>
#include <cmath>
#include <string>
#include <tuple>
#include <vector>

#include <QDebug>

#include "common/watchdog.h"
#include "common/util.h"
#include "selfdrive/ui/qt/network/networking.h"
#include "selfdrive/ui/qt/offroad/settings.h"
#include "selfdrive/ui/qt/qt_window.h"
#include "selfdrive/ui/qt/widgets/prime.h"
#include "selfdrive/ui/qt/widgets/scrollview.h"
#include "selfdrive/ui/qt/widgets/ssh_keys.h"

#include <QComboBox>
#include <QAbstractItemView>
#include <QScroller>
#include <QListView>
#include <QListWidget>
#include <QProcess>

TogglesPanel::TogglesPanel(SettingsWindow *parent) : ListWidget(parent) {
  // param, title, desc, icon
  std::vector<std::tuple<QString, QString, QString, QString>> toggle_defs{
    {
      "OpenpilotEnabledToggle",
      tr("Enable openpilot"),
      tr("Use the openpilot system for adaptive cruise control and lane keep driver assistance. Your attention is required at all times to use this feature. Changing this setting takes effect when the car is powered off."),
      "../assets/offroad/icon_openpilot.png",
    },
    {
      "ExperimentalLongitudinalEnabled",
      tr("openpilot Longitudinal Control (Alpha)"),
      QString("<b>%1</b><br><br>%2")
      .arg(tr("WARNING: openpilot longitudinal control is in alpha for this car and will disable Automatic Emergency Braking (AEB)."))
      .arg(tr("On this car, openpilot defaults to the car's built-in ACC instead of openpilot's longitudinal control. "
              "Enable this to switch to openpilot longitudinal control. Enabling Experimental mode is recommended when enabling openpilot longitudinal control alpha.")),
      "../assets/offroad/icon_speed_limit.png",
    },
    {
      "ExperimentalMode",
      tr("Experimental Mode"),
      "",
      "../assets/img_experimental_white.svg",
    },
    {
      "DisengageOnAccelerator",
      tr("Disengage on Accelerator Pedal"),
      tr("When enabled, pressing the accelerator pedal will disengage openpilot."),
      "../assets/offroad/icon_disengage_on_accelerator.svg",
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
      "AlwaysOnDM",
      tr("Always-On Driver Monitoring"),
      tr("Enable driver monitoring even when openpilot is not engaged."),
      "../assets/offroad/icon_monitoring.png",
    },
    {
      "RecordFront",
      tr("Record and Upload Driver Camera"),
      tr("Upload data from the driver facing camera and help improve the driver monitoring algorithm."),
      "../assets/offroad/icon_monitoring.png",
    },
    {
      "IsMetric",
      tr("Use Metric System"),
      tr("Display speed in km/h instead of mph."),
      "../assets/offroad/icon_metric.png",
    },
  };


  std::vector<QString> longi_button_texts{tr("Aggressive"), tr("Standard"), tr("Relaxed")};
  long_personality_setting = new ButtonParamControl("LongitudinalPersonality", tr("Driving Personality"),
                                          tr("Standard is recommended. In aggressive mode, openpilot will follow lead cars closer and be more aggressive with the gas and brake. "
                                             "In relaxed mode openpilot will stay further away from lead cars. On supported cars, you can cycle through these personalities with "
                                             "your steering wheel distance button."),
                                          "../assets/offroad/icon_speed_limit.png",
                                          longi_button_texts);

  // set up uiState update for personality setting
  QObject::connect(uiState(), &UIState::uiUpdate, this, &TogglesPanel::updateState);

  for (auto &[param, title, desc, icon] : toggle_defs) {
    auto toggle = new ParamControl(param, title, desc, icon, this);

    bool locked = params.getBool((param + "Lock").toStdString());
    toggle->setEnabled(!locked);

    addItem(toggle);
    toggles[param.toStdString()] = toggle;

    // insert longitudinal personality after NDOG toggle
    if (param == "DisengageOnAccelerator") {
      addItem(long_personality_setting);
    }
  }

  // Toggles with confirmation dialogs
  toggles["ExperimentalMode"]->setActiveIcon("../assets/img_experimental.svg");
  toggles["ExperimentalMode"]->setConfirmation(true, true);
  toggles["ExperimentalLongitudinalEnabled"]->setConfirmation(true, false);

  connect(toggles["ExperimentalLongitudinalEnabled"], &ToggleControl::toggleFlipped, [=]() {
    updateToggles();
  });
}

void TogglesPanel::updateState(const UIState &s) {
  const SubMaster &sm = *(s.sm);

  if (sm.updated("controlsState")) {
    auto personality = sm["controlsState"].getControlsState().getPersonality();
    if (personality != s.scene.personality && s.scene.started && isVisible()) {
      long_personality_setting->setCheckedButton(static_cast<int>(personality));
    }
    uiState()->scene.personality = personality;
  }
}

void TogglesPanel::expandToggleDescription(const QString &param) {
  toggles[param.toStdString()]->showDescription();
}

void TogglesPanel::showEvent(QShowEvent *event) {
  updateToggles();
}

void TogglesPanel::updateToggles() {
  auto experimental_mode_toggle = toggles["ExperimentalMode"];
  auto op_long_toggle = toggles["ExperimentalLongitudinalEnabled"];
  const QString e2e_description = QString("%1<br>"
                                          "<h4>%2</h4><br>"
                                          "%3<br>"
                                          "<h4>%4</h4><br>"
                                          "%5<br>")
                                  .arg(tr("openpilot defaults to driving in <b>chill mode</b>. Experimental mode enables <b>alpha-level features</b> that aren't ready for chill mode. Experimental features are listed below:"))
                                  .arg(tr("End-to-End Longitudinal Control"))
                                  .arg(tr("Let the driving model control the gas and brakes. openpilot will drive as it thinks a human would, including stopping for red lights and stop signs. "
                                          "Since the driving model decides the speed to drive, the set speed will only act as an upper bound. This is an alpha quality feature; "
                                          "mistakes should be expected."))
                                  .arg(tr("New Driving Visualization"))
                                  .arg(tr("The driving visualization will transition to the road-facing wide-angle camera at low speeds to better show some turns. The Experimental mode logo will also be shown in the top right corner."));

  const bool is_release = params.getBool("IsReleaseBranch");
  auto cp_bytes = params.get("CarParamsPersistent");
  if (!cp_bytes.empty()) {
    AlignedBuffer aligned_buf;
    capnp::FlatArrayMessageReader cmsg(aligned_buf.align(cp_bytes.data(), cp_bytes.size()));
    cereal::CarParams::Reader CP = cmsg.getRoot<cereal::CarParams>();

    if (!CP.getExperimentalLongitudinalAvailable() || is_release) {
      params.remove("ExperimentalLongitudinalEnabled");
    }
    op_long_toggle->setVisible(CP.getExperimentalLongitudinalAvailable() && !is_release);
    if (hasLongitudinalControl(CP)) {
      // normal description and toggle
      experimental_mode_toggle->setEnabled(true);
      experimental_mode_toggle->setDescription(e2e_description);
      long_personality_setting->setEnabled(true);
    } else {
      // no long for now
      experimental_mode_toggle->setEnabled(false);
      long_personality_setting->setEnabled(false);
      params.remove("ExperimentalMode");

      const QString unavailable = tr("Experimental mode is currently unavailable on this car since the car's stock ACC is used for longitudinal control.");

      QString long_desc = unavailable + " " + \
                          tr("openpilot longitudinal control may come in a future update.");
      if (CP.getExperimentalLongitudinalAvailable()) {
        if (is_release) {
          long_desc = unavailable + " " + tr("An alpha version of openpilot longitudinal control can be tested, along with Experimental mode, on non-release branches.");
        } else {
          long_desc = tr("Enable the openpilot longitudinal control (alpha) toggle to allow Experimental mode.");
        }
      }
      experimental_mode_toggle->setDescription("<b>" + long_desc + "</b><br><br>" + e2e_description);
    }

    experimental_mode_toggle->refresh();
  } else {
    experimental_mode_toggle->setDescription(e2e_description);
    op_long_toggle->setVisible(false);
  }
}

DevicePanel::DevicePanel(SettingsWindow *parent) : ListWidget(parent) {
  setSpacing(7);
  addItem(new LabelControl(tr("Dongle ID"), getDongleId().value_or(tr("N/A"))));
  addItem(new LabelControl(tr("Serial"), params.get("HardwareSerial").c_str()));

  pair_device = new ButtonControl(tr("Pair Device"), tr("PAIR"),
                                  tr("Pair your device with comma connect (connect.comma.ai) and claim your comma prime offer."));
  connect(pair_device, &ButtonControl::clicked, [=]() {
    PairingPopup popup(this);
    popup.exec();
  });
  addItem(pair_device);

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
      params.remove("LiveTorqueParameters");
      emit closeSettings();
      QTimer::singleShot(1000, []() {
        Params().putBool("SoftRestartTriggered", true);
      });
    }
  });
  addItem(resetCalibBtn);

  auto retrainingBtn = new ButtonControl(tr("Review Training Guide"), tr("REVIEW"), tr("Review the rules, features, and limitations of openpilot"));
  connect(retrainingBtn, &ButtonControl::clicked, [=]() {
    if (ConfirmationDialog::confirm(tr("Are you sure you want to review the training guide?"), tr("Review"), this)) {
      emit reviewTrainingGuide();
    }
  });
  addItem(retrainingBtn);

  /*if (Hardware::TICI()) {
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
      params.put("LanguageSetting", langs[selection].toStdString());
      qApp->exit(18);
      watchdog_kick(0);
    }
  });
  addItem(translateBtn);

  QObject::connect(uiState(), &UIState::primeTypeChanged, [this] (PrimeType type) {
    pair_device->setVisible(type == PrimeType::UNPAIRED);
  });
  QObject::connect(uiState(), &UIState::offroadTransition, [=](bool offroad) {
    for (auto btn : findChildren<ButtonControl *>()) {
      if (btn != pair_device) {
        btn->setEnabled(offroad);
      }
    }
    resetCalibBtn->setEnabled(true);
    translateBtn->setEnabled(true);
  });

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
         "within 5° up or 9° down. openpilot is continuously calibrating, resetting is rarely required.");
  std::string calib_bytes = params.get("CalibrationParams");
  if (!calib_bytes.empty()) {
    try {
      AlignedBuffer aligned_buf;
      capnp::FlatArrayMessageReader cmsg(aligned_buf.align(calib_bytes.data(), calib_bytes.size()));
      auto calib = cmsg.getRoot<cereal::Event>().getLiveCalibration();
      if (calib.getCalStatus() != cereal::LiveCalibrationData::Status::UNCALIBRATED) {
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
        params.putBool("DoReboot", true);
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
        params.putBool("DoShutdown", true);
      }
    }
  } else {
    ConfirmationDialog::alert(tr("Disengage to Power Off"), this);
  }
}

void DevicePanel::showEvent(QShowEvent *event) {
  pair_device->setVisible(uiState()->primeType() == PrimeType::UNPAIRED);
  ListWidget::showEvent(event);
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
  setCurrentPanel(0);
}

void SettingsWindow::setCurrentPanel(int index, const QString &param) {
  panel_widget->setCurrentIndex(index);
  nav_btns->buttons()[index]->setChecked(true);
  if (!param.isEmpty()) {
    emit expandToggleDescription(param);
  }
}

SettingsWindow::SettingsWindow(QWidget *parent) : QFrame(parent) {

  // setup two main layouts
  sidebar_widget = new QWidget;
  QVBoxLayout *sidebar_layout = new QVBoxLayout(sidebar_widget);
  sidebar_layout->setMargin(0);
  panel_widget = new QStackedWidget();

  // close button
  QPushButton *close_btn = new QPushButton(tr("×"));
  close_btn->setStyleSheet(R"(
    QPushButton {
      font-size: 140px;
      padding-bottom: 20px;
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

  TogglesPanel *toggles = new TogglesPanel(this);
  QObject::connect(this, &SettingsWindow::expandToggleDescription, toggles, &TogglesPanel::expandToggleDescription);

  QList<QPair<QString, QWidget *>> panels = {
    {tr("Device"), device},
    {tr("Network"), new Networking(this)},
    {tr("Toggles"), toggles},
    {tr("Software"), new SoftwarePanel(this)},
    {tr("Community"), new CommunityPanel(this)},
  };

  nav_btns = new QButtonGroup(this);
  for (auto &[name, panel] : panels) {
    QPushButton *btn = new QPushButton(name);
    btn->setCheckable(true);
    btn->setChecked(nav_btns->buttons().size() == 0);
    btn->setStyleSheet(R"(
      QPushButton {
        color: grey;
        border: none;
        background: none;
        font-size: 60px;
        font-weight: 500;
      }
      QPushButton:checked {
        color: white;
      }
      QPushButton:pressed {
        color: #ADADAD;
      }
    )");
    btn->setSizePolicy(QSizePolicy::Preferred, QSizePolicy::Expanding);
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
    QStackedWidget, ScrollView {
      background-color: #292929;
      border-radius: 30px;
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

  QString selectedBranch = QString::fromStdString(Params().get("SelectedBranch"));
  QPushButton* selectBranch_btn = new QPushButton(selectedBranch.length() ? selectedBranch : tr("Select Branch"));
  selectBranch_btn->setObjectName("selectBranch_btn");
  connect(selectBranch_btn, &QPushButton::clicked, [=]() { main_layout->setCurrentWidget(selectBranch); });

  selectBranch = new SelectBranch(this);
  connect(selectBranch, &SelectBranch::backPress, [=]() { main_layout->setCurrentWidget(homeScreen); });
  connect(selectBranch, &SelectBranch::selectedBranch, [=]() {
     QString selected = QString::fromStdString(Params().get("SelectedBranch"));
     selectBranch_btn->setText(selectedBranch.length() ? selectedBranch : tr("Select Branch"));
     main_layout->setCurrentWidget(homeScreen);
  });
  main_layout->addWidget(selectBranch);

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
      font-size: 50px; margin: 0px; padding: 15px; border-width: 0; border-radius: 30px; color: #FFFFFF; background-color: #444444; }
    #back_btn:pressed { background-color: #3B3B3B; }
    #selectCar_btn, #selectManufacturer_btn {
      font-size: 50px; margin: 0px; padding: 15px; border-width: 0; border-radius: 30px; color: #FFFFFF; background-color: #2C2CE2; }
    #selectCar_btn:pressed, #selectManufacturer_btn:pressed { background-color: #2424FF; }
  )");

  bool is_canfd = params.getBool("IsCanfd");

  auto cleardtc_btn = new ButtonControl(tr("Clear DTC"), tr("RUN"));
  QObject::connect(cleardtc_btn, &ButtonControl::clicked, [=]() {
    if (ConfirmationDialog::confirm(tr("Clear DTC<br><br>Process?"), tr("Process"), this)) {
      QProcess::execute("/data/openpilot/scripts/cleardtc.sh");
    }
  });

  auto gitcheckout_btn = new ButtonControl(tr("Git Checkout"), tr("RUN"));
  QObject::connect(gitcheckout_btn, &ButtonControl::clicked, [=]() {
    if (ConfirmationDialog::confirm(tr("Git Checkout<br><br>Process?"), tr("Process"), this)) {
      QProcess::execute("/data/openpilot/scripts/checkout.sh");
    }
  });

  auto gitreset_btn = new ButtonControl(tr("Git Reset -1"), tr("RUN"));
  QObject::connect(gitreset_btn, &ButtonControl::clicked, [=]() {
    if (ConfirmationDialog::confirm(tr("Git Reset<br><br>Process?"), tr("Process"), this)) {
      QProcess::execute("/data/openpilot/scripts/gitreset.sh");
    }
  });

  auto gitpull_btn = new ButtonControl(tr("Git Fetch and Reset"), tr("RUN"));
  QObject::connect(gitpull_btn, &ButtonControl::clicked, [=]() {
    if (ConfirmationDialog::confirm(tr("Git Fetch and Reset<br><br>Process?"), tr("Process"), this)) {
      QProcess::execute("/data/openpilot/scripts/gitpull.sh");
    }
    const QString file_path = "/data/gitpull.log";
    if (QFile::exists(file_path)) {
      const std::string txt = util::read_file(file_path.toStdString());
      ConfirmationDialog::rich(QString::fromStdString(txt), this);
    }
  });

	auto tmux_error_log_btn = new ButtonControl(tr("tmux log"), tr("VIEW"));
	QObject::connect(tmux_error_log_btn, &ButtonControl::clicked, [=]() {
    const QString file_path = "/data/tmux_error.log";
    if (QFile::exists(file_path)) {
      const std::string txt = util::read_file(file_path.toStdString());
      ConfirmationDialog::rich(QString::fromStdString(txt), this);
    } else {
      const std::string txt = "log file not found";
      ConfirmationDialog::rich(QString::fromStdString(txt), this);
    }
	});

	auto tmux_error_log_upload_btn = new ButtonControl(tr("tmux log upload"), tr("RUN"));
  QObject::connect(tmux_error_log_upload_btn, &ButtonControl::clicked, [=]() {
    const QString file_path = "/data/tmux_error.log";
    if (QFile::exists(file_path)) {
      if (ConfirmationDialog::confirm(tr("tmux log upload<br><br>Process?"), tr("Process"), this)) {
        QProcess::execute("/data/openpilot/scripts/log_upload.sh");
      }
    } else {
      const std::string txt = "log file not found";
      ConfirmationDialog::rich(QString::fromStdString(txt), this);
    }
	});

	auto can_missing_error_log_btn = new ButtonControl(tr("can missing log"), tr("VIEW"));
  QObject::connect(can_missing_error_log_btn, &ButtonControl::clicked, [=]() {
    const QString file_path = "/data/can_missing.log";
    if (QFile::exists(file_path)) {
      const std::string txt = util::read_file(file_path.toStdString());
      ConfirmationDialog::rich(QString::fromStdString(txt), this);
    } else {
      const std::string txt = "log file not found";
      ConfirmationDialog::rich(QString::fromStdString(txt), this);
    }
  });

	auto can_timeout_error_log_btn = new ButtonControl(tr("can timeout log"), tr("VIEW"));
  QObject::connect(can_timeout_error_log_btn, &ButtonControl::clicked, [=]() {
    const QString file_path = "/data/can_timeout.log";
    if (QFile::exists(file_path)) {
      const std::string txt = util::read_file(file_path.toStdString());
      ConfirmationDialog::rich(QString::fromStdString(txt), this);
    } else {
      const std::string txt = "log file not found";
      ConfirmationDialog::rich(QString::fromStdString(txt), this);
    }
  });

  auto carstate_dump_upload_btn = new ButtonControl(tr("carState dump upload"), tr("RUN"));
  QObject::connect(carstate_dump_upload_btn, &ButtonControl::clicked, [=]() {
    if (ConfirmationDialog::confirm(tr("carState dump upload<br><br>Process?"), tr("Process"), this)) {
      QProcess::execute("/data/openpilot/scripts/carstate_upload.sh");
    }
  });

  auto carparams_dump_upload_btn = new ButtonControl(tr("carParams dump upload"), tr("RUN"));
  QObject::connect(carparams_dump_upload_btn, &ButtonControl::clicked, [=]() {
    if (ConfirmationDialog::confirm(tr("carParams dump upload<br><br>Process?"), tr("Process"), this)) {
      QProcess::execute("/data/openpilot/scripts/carparams_upload.sh");
    }
  });

  auto carcontrol_dump_upload_btn = new ButtonControl(tr("carControl dump upload"), tr("RUN"));
  QObject::connect(carcontrol_dump_upload_btn, &ButtonControl::clicked, [=]() {
    if (ConfirmationDialog::confirm(tr("carControl dump upload<br><br>Process?"), tr("Process"), this)) {
      QProcess::execute("/data/openpilot/scripts/carcontrol_upload.sh");
    }
  });

  auto controlsstate_dump_upload_btn = new ButtonControl(tr("controlsState dump upload"), tr("RUN"));
  QObject::connect(controlsstate_dump_upload_btn, &ButtonControl::clicked, [=]() {
    if (ConfirmationDialog::confirm(tr("controlsState dump upload<br><br>Process?"), tr("Process"), this)) {
      QProcess::execute("/data/openpilot/scripts/controlsstate_upload.sh");
    }
  });

  auto devicestate_dump_upload_btn = new ButtonControl(tr("deviceState dump upload"), tr("RUN"));
  QObject::connect(devicestate_dump_upload_btn, &ButtonControl::clicked, [=]() {
    if (ConfirmationDialog::confirm(tr("deviceState dump upload<br><br>Process?"), tr("Process"), this)) {
      QProcess::execute("/data/openpilot/scripts/devicestate_upload.sh");
    }
  });

  auto pandastates_dump_upload_btn = new ButtonControl(tr("pandaStates dump upload"), tr("RUN"));
  QObject::connect(pandastates_dump_upload_btn, &ButtonControl::clicked, [=]() {
    if (ConfirmationDialog::confirm(tr("pandaStates dump upload<br><br>Process?"), tr("Process"), this)) {
      QProcess::execute("/data/openpilot/scripts/pandastates_upload.sh");
    }
  });

  QHBoxLayout *buttonLayout = new QHBoxLayout;
  buttonLayout->addWidget(vertical_line());
  buttonLayout->addWidget(selectBranch_btn);
  buttonLayout->addWidget(vertical_line());
  buttonLayout->addWidget(cleardtc_btn);
  buttonLayout->addWidget(vertical_line());

  selectBranch_btn->setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Fixed);

  QHBoxLayout *buttonLayout1 = new QHBoxLayout;
  buttonLayout1->addWidget(vertical_line());
  buttonLayout1->addWidget(gitcheckout_btn);
  buttonLayout1->addWidget(vertical_line());
  buttonLayout1->addWidget(gitpull_btn);
  buttonLayout1->addWidget(vertical_line());

  QHBoxLayout *buttonLayout2 = new QHBoxLayout;
  buttonLayout2->addWidget(vertical_line());
  buttonLayout2->addWidget(tmux_error_log_btn);
  buttonLayout2->addWidget(vertical_line());
  buttonLayout2->addWidget(tmux_error_log_upload_btn);
  buttonLayout2->addWidget(vertical_line());

  QHBoxLayout *buttonLayout3 = new QHBoxLayout;
  buttonLayout3->addWidget(vertical_line());
  buttonLayout3->addWidget(can_missing_error_log_btn);
  buttonLayout3->addWidget(vertical_line());
  buttonLayout3->addWidget(can_timeout_error_log_btn);
  buttonLayout3->addWidget(vertical_line());

  communityLayout->addWidget(horizontal_line());
  communityLayout->addLayout(buttonLayout);
  communityLayout->addWidget(horizontal_line());
  communityLayout->addLayout(buttonLayout1);
  communityLayout->addWidget(horizontal_line());
  communityLayout->addLayout(buttonLayout2);
  communityLayout->addWidget(horizontal_line());
  communityLayout->addLayout(buttonLayout3);
  communityLayout->addWidget(horizontal_line());

  // add community toggle
  QList<ParamControl*> toggles;
  toggles.append(new ParamControl("PrebuiltEnable",
                                  tr("Prebuilt Enable"),
                                  tr("Create prebuilt file to speed bootup"),
                                  "../assets/offroad/icon_addon.png",
                                  this));
  toggles.append(new ParamControl("LoggerEnable",
                                  tr("Logger Enable"),
                                  tr("Turn off this option to reduce system load"),
                                  "../assets/offroad/icon_addon.png",
                                  this));
  if (is_canfd) {
    toggles.append(new ParamControl("IsHda2",
                                    tr("CANFD Car HDA2"),
                                    tr("Highway Drive Assist 2, turn it on."),
                                    "../assets/offroad/icon_long.png",
                                    this));
  } else {
    toggles.append(new ParamControl("SccOnBus2",
                                    tr("Scc on Bus 2"),
                                    tr("If Scc is on bus 2, turn it on."),
                                    "../assets/offroad/icon_long.png",
                                    this));
  }
  toggles.append(new ParamControl("DriverCameraHardwareMissing",
                                  tr("DriverCamera Hardware Missing"),
                                  tr("If there is a problem with the driver camera hardware, drive without the driver camera."),
                                  "../assets/img_driver_face_static_x.png",
                                  this));
  toggles.append(new ParamControl("DriverCameraOnReverse",
                                  tr("Driver Camera On Reverse"),
                                  tr("Displays the driver camera when in reverse."),
                                  "../assets/img_driver_face_static.png",
                                  this));
  toggles.append(new ParamControl("RadarTrackEnable",
                                  tr("Enable Radar Track use"),
                                  tr("Enable Radar Track use (disable AEB)"),
                                  "../assets/offroad/icon_warning.png",
                                  this));
  toggles.append(new ParamControl("DisengageOnBrake",
                                  tr("Disengage on Brake Pedal"),
                                  tr("When enabled, pressing the brake pedal will disengage openpilot."),
                                  "../assets/offroad/icon_disengage_on_accelerator.svg",
                                  this));
  for (ParamControl *toggle : toggles) {
    if (main_layout->count() != 0) {
    }
    communityLayout->addWidget(toggle);
  }

  auto pandaflash_btn = new ButtonControl(tr("Panda Flash"), tr("RUN"));
  QObject::connect(pandaflash_btn, &ButtonControl::clicked, [=]() {
    if (ConfirmationDialog::confirm(tr("Panda Flash<br><br>Process?"), tr("Process"), this)) {
      QProcess::execute("/data/openpilot/panda/board/flash.py");
    }
  });

  auto pandarecover_btn = new ButtonControl(tr("Panda Recover"), tr("RUN"));
  QObject::connect(pandarecover_btn, &ButtonControl::clicked, [=]() {
    if (ConfirmationDialog::confirm(tr("Panda Recover<br><br>Process?"), tr("Process"), this)) {
      QProcess::execute("/data/openpilot/panda/board/recover.py");
    }
  });

  auto scons_rebuild_btn = new ButtonControl(tr("Scons Rebuild"), tr("RUN"));
  QObject::connect(scons_rebuild_btn, &ButtonControl::clicked, [=]() {
    if (ConfirmationDialog::confirm(tr("Scons Rebuild<br><br>Process?"), tr("Process"), this)) {
      QProcess::execute("/data/openpilot/scripts/scons_rebuild.sh");
    }
  });

  QHBoxLayout *buttonLayout4 = new QHBoxLayout;
  buttonLayout4->addWidget(vertical_line());
  buttonLayout4->addWidget(pandaflash_btn);
  buttonLayout4->addWidget(vertical_line());
  buttonLayout4->addWidget(pandarecover_btn);
  buttonLayout4->addWidget(vertical_line());

  QHBoxLayout *buttonLayout5 = new QHBoxLayout;
  buttonLayout5->addWidget(vertical_line());
  buttonLayout5->addWidget(gitreset_btn);
  buttonLayout5->addWidget(vertical_line());
  buttonLayout5->addWidget(scons_rebuild_btn);
  buttonLayout5->addWidget(vertical_line());

  QHBoxLayout *buttonLayout6 = new QHBoxLayout;
  buttonLayout6->addWidget(vertical_line());
  buttonLayout6->addWidget(carstate_dump_upload_btn);
  buttonLayout6->addWidget(vertical_line());

  QHBoxLayout *buttonLayout7 = new QHBoxLayout;
  buttonLayout7->addWidget(vertical_line());
  buttonLayout7->addWidget(carparams_dump_upload_btn);
  buttonLayout7->addWidget(vertical_line());

  QHBoxLayout *buttonLayout8 = new QHBoxLayout;
  buttonLayout8->addWidget(vertical_line());
  buttonLayout8->addWidget(carcontrol_dump_upload_btn);
  buttonLayout8->addWidget(vertical_line());

  QHBoxLayout *buttonLayout9 = new QHBoxLayout;
  buttonLayout9->addWidget(vertical_line());
  buttonLayout9->addWidget(controlsstate_dump_upload_btn);
  buttonLayout9->addWidget(vertical_line());

  QHBoxLayout *buttonLayout10 = new QHBoxLayout;
  buttonLayout10->addWidget(vertical_line());
  buttonLayout10->addWidget(devicestate_dump_upload_btn);
  buttonLayout10->addWidget(vertical_line());

  QHBoxLayout *buttonLayout11 = new QHBoxLayout;
  buttonLayout11->addWidget(vertical_line());
  buttonLayout11->addWidget(pandastates_dump_upload_btn);
  buttonLayout11->addWidget(vertical_line());

  communityLayout->addWidget(horizontal_line());
  communityLayout->addLayout(buttonLayout4);
  communityLayout->addWidget(horizontal_line());
  communityLayout->addLayout(buttonLayout5);
  communityLayout->addWidget(horizontal_line());
  communityLayout->addLayout(buttonLayout6);
  communityLayout->addWidget(horizontal_line());
  communityLayout->addLayout(buttonLayout7);
  communityLayout->addWidget(horizontal_line());
  communityLayout->addLayout(buttonLayout8);
  communityLayout->addWidget(horizontal_line());
  communityLayout->addLayout(buttonLayout9);
  communityLayout->addWidget(horizontal_line());
  communityLayout->addLayout(buttonLayout10);
  communityLayout->addWidget(horizontal_line());
  communityLayout->addLayout(buttonLayout11);
  communityLayout->addWidget(horizontal_line());
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
  QStringList items = get_list("/data/params/crwusiz/CarList");
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
      qApp->exit(18);
      watchdog_kick(0);
    } else {
      Params().put("SelectedCar", list->currentItem()->text().toStdString());
      qApp->exit(18);
      watchdog_kick(0);
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

  QStringList items = {"HYUNDAI", "KIA", "GENESIS", "CHEVROLET"};
  list->addItems(items);
  list->setCurrentRow(0);
  QString selected = QString::fromStdString(Params().get("SelectedManufacturer"));

  int index = 1;
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
      qApp->exit(18);
      watchdog_kick(0);
    } else if (list->currentRow() == 1) {
      QProcess::execute("cp -f /data/params/crwusiz/CarList_Hyundai /data/params/crwusiz/CarList");
      Params().put("SelectedManufacturer", list->currentItem()->text().toStdString());
      qApp->exit(18);
      watchdog_kick(0);
    } else if (list->currentRow() == 2) {
      QProcess::execute("cp -f /data/params/crwusiz/CarList_Kia /data/params/crwusiz/CarList");
      Params().put("SelectedManufacturer", list->currentItem()->text().toStdString());
      qApp->exit(18);
      watchdog_kick(0);
    } else if (list->currentRow() == 3) {
      QProcess::execute("cp -f /data/params/crwusiz/CarList_Genesis /data/params/crwusiz/CarList");
      Params().put("SelectedManufacturer", list->currentItem()->text().toStdString());
      qApp->exit(18);
      watchdog_kick(0);
    } else if (list->currentRow() == 4) {
      QProcess::execute("cp -f /data/params/crwusiz/CarList_Gm /data/params/crwusiz/CarList");
      Params().put("SelectedManufacturer", list->currentItem()->text().toStdString());
      qApp->exit(18);
      watchdog_kick(0);
    /*} else if (list->currentRow() == 5) {
      QProcess::execute("cp -f /data/params/crwusiz/CarList_Toyota /data/params/crwusiz/CarList");
      Params().put("SelectedManufacturer", list->currentItem()->text().toStdString());
      qApp->exit(18);
      watchdog_kick(0);
    } else if (list->currentRow() == 6) {
      QProcess::execute("cp -f /data/params/crwusiz/CarList_Lexus /data/params/crwusiz/CarList");
      Params().put("SelectedManufacturer", list->currentItem()->text().toStdString());
      qApp->exit(18);
      watchdog_kick(0);
    } else if (list->currentRow() == 7) {
      QProcess::execute("cp -f /data/params/crwusiz/CarList_Honda /data/params/crwusiz/CarList");
      Params().put("SelectedManufacturer", list->currentItem()->text().toStdString());
      qApp->exit(18);
      watchdog_kick(0);
*/
    }
    emit selectedManufacturer();
    });

  main_layout->addWidget(list);
}

SelectBranch::SelectBranch(QWidget* parent): QWidget(parent) {
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
  list->addItem(tr("Branch Select not use"));
  QStringList items = get_list("/data/params/crwusiz/GitBranchList");
  list->addItems(items);
  list->setCurrentRow(0);
  QString selected = QString::fromStdString(Params().get("SelectedBranch"));

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
      Params().remove("SelectedBranch");
      qApp->exit(18);
      watchdog_kick(0);
    } else {
      Params().put("SelectedBranch", list->currentItem()->text().toStdString());
      qApp->exit(18);
      watchdog_kick(0);
    }
    emit selectedBranch();
    });
  main_layout->addWidget(list);
}
