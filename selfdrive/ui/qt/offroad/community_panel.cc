#include <QComboBox>
#include <QAbstractItemView>
#include <QScroller>
#include <QListView>
#include <QListWidget>
#include <QProcess>
#include <QDir>
#include <QFileInfoList>
#include <QStringListModel>

#include "common/watchdog.h"
#include "common/util.h"

#include "selfdrive/ui/qt/offroad/community_panel.h"
#include "selfdrive/ui/qt/widgets/controls.h"
#include "selfdrive/ui/qt/widgets/scrollview.h"

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

// Community Panel
CommunityPanel::CommunityPanel(QWidget* parent) : QWidget(parent) {
  main_layout = new QStackedLayout(this);
  homeScreen = new QWidget(this);
  QVBoxLayout* communityLayout = new QVBoxLayout(homeScreen);

  // selectedManufacturer
  QString selectedManufacturer = QString::fromStdString(Params().get("SelectedManufacturer"));
  QPushButton* selectManufacturer_btn = new QPushButton(selectedManufacturer.length() ? selectedManufacturer : tr("Select your Manufacturer"));
  selectManufacturer_btn->setObjectName("selectManufacturer_btn");
  blueButtonStyle(selectManufacturer_btn);

  connect(selectManufacturer_btn, &QPushButton::clicked, [=]() {
    QStringList manufacturers = {"[ Not Selected ]", "HYUNDAI", "KIA", "GENESIS"};
    QString selectedOption = MultiOptionDialog::getSelection(tr("Select your Manufacturer"), manufacturers,
                                                            selectedManufacturer.isEmpty() ? manufacturers[0] : selectedManufacturer,
                                                            this);
    if (!selectedOption.isEmpty()) {
      if (selectedOption == "[ Not Selected ]") {
        Params().remove("SelectedManufacturer");
        qApp->exit(18);
        watchdog_kick(0);
      } else {
        QString carListFile;
        if (selectedOption == "HYUNDAI") {
          carListFile = "/data/params/crwusiz/CarList_Hyundai";
          qApp->exit(18);
          watchdog_kick(0);
        } else if (selectedOption == "KIA") {
          carListFile = "/data/params/crwusiz/CarList_Kia";
          qApp->exit(18);
          watchdog_kick(0);
        } else if (selectedOption == "GENESIS") {
          carListFile = "/data/params/crwusiz/CarList_Genesis";
          qApp->exit(18);
          watchdog_kick(0);
        }

        if (!carListFile.isEmpty()) {
          QProcess::execute("cp -f " + carListFile + " /data/params/crwusiz/CarList");
        }

        Params().put("SelectedManufacturer", selectedOption.toStdString());
        ConfirmationDialog::alert(selectedOption, this);
      }
    }
    selectManufacturer_btn->setText(selectedManufacturer.length() ? selectedManufacturer : tr("Select your Manufacturer"));
  });

  // selectedCar
  QString selectedCar = QString::fromStdString(Params().get("SelectedCar"));
  QPushButton* selectCar_btn = new QPushButton(selectedCar.length() ? selectedCar : tr("Select your car"));
  selectCar_btn->setObjectName("selectCar_btn");
  blueButtonStyle(selectCar_btn);

  connect(selectCar_btn, &QPushButton::clicked, [=]() {
    QStringList cars = {"[ Not Selected ]"};
    QStringList items = get_list("/data/params/crwusiz/CarList");
    cars.append(items);
    QString selectedOption = MultiOptionDialog::getSelection(tr("Select your car"), cars,
                                                            selectedCar.isEmpty() ? cars[0] : selectedCar,
                                                            this);
    if (!selectedOption.isEmpty()) {
      if (selectedOption == "[ Not Selected ]") {
        Params().remove("SelectedCar");
        qApp->exit(18);
        watchdog_kick(0);
      } else {
        Params().put("SelectedCar", selectedOption.toStdString());
        qApp->exit(18);
        watchdog_kick(0);
        ConfirmationDialog::alert(selectedOption, this);
      }
    }
    selectCar_btn->setText(selectedCar.length() ? selectedCar : tr("Select your car"));
  });

  // selectedBranch
  QString selectedBranch = QString::fromStdString(Params().get("SelectedBranch"));
  QPushButton* selectBranch_btn = new QPushButton(selectedBranch.length() ? selectedBranch : tr("Select Branch"));
  selectBranch_btn->setObjectName("selectBranch_btn");
  blueButtonStyle(selectBranch_btn);

  connect(selectBranch_btn, &QPushButton::clicked, [=]() {
    QStringList branches = {"[ Not Selected ]"};
    QStringList items = get_list("/data/params/crwusiz/GitBranchList");
    branches.append(items);
    QString selectedOption = MultiOptionDialog::getSelection(tr("Select Branch"), branches,
                                                            selectedBranch.isEmpty() ? branches[0] : selectedBranch,
                                                            this);
    if (!selectedOption.isEmpty()) {
      if (selectedOption == "[ Not Selected ]") {
        Params().remove("SelectedBranch");
        qApp->exit(18);
        watchdog_kick(0);
      } else {
        Params().put("SelectedBranch", selectedOption.toStdString());
        qApp->exit(18);
        watchdog_kick(0);
        ConfirmationDialog::alert(selectedOption, this);
      }
    }
    selectBranch_btn->setText(selectedBranch.length() ? selectedBranch : tr("Select Branch"));
  });

  QPushButton* toggle_btn = new QPushButton(tr("Toggle"));
  toggle_btn->setObjectName("toggle_btn");
  QObject::connect(toggle_btn, &QPushButton::clicked, this, [this]() {
    this->currentCommunityIndex = 0;
    this->togglesCommunity(0);
    updateButtonStyles();
  });

  QPushButton* func_btn = new QPushButton(tr("Function"));
  func_btn->setObjectName("func_btn");
  QObject::connect(func_btn, &QPushButton::clicked, this, [this]() {
    this->currentCommunityIndex = 1;
    this->togglesCommunity(1);
    updateButtonStyles();
  });

  QPushButton* log_btn = new QPushButton(tr("Log"));
  log_btn->setObjectName("log_btn");
  QObject::connect(log_btn, &QPushButton::clicked, this, [this]() {
    this->currentCommunityIndex = 2;
    this->togglesCommunity(2);
    updateButtonStyles();
  });

  updateButtonStyles();

  QHBoxLayout* row1Layout = new QHBoxLayout();
  row1Layout->setSpacing(30);
  row1Layout->addWidget(selectManufacturer_btn);
  row1Layout->addWidget(selectCar_btn);
  row1Layout->addWidget(selectBranch_btn);

  QHBoxLayout* row2Layout = new QHBoxLayout();
  row2Layout->setSpacing(30);
  row2Layout->addWidget(toggle_btn);
  row2Layout->addWidget(func_btn);
  row2Layout->addWidget(log_btn);

  QVBoxLayout* mainLayout = new QVBoxLayout();
  mainLayout->setSpacing(30);
  mainLayout->addLayout(row1Layout);
  mainLayout->addLayout(row2Layout);

  communityLayout->addLayout(mainLayout, 0);

  QWidget* toggles = new QWidget();
  QVBoxLayout* toggles_layout = new QVBoxLayout(toggles);

  // main toggle
  mainToggles = new ListWidget(this);
  mainToggles->addItem(new ParamControl("PcmCruiseEnable", tr("PcmCruise"), tr("Change the openpilot cruise engagement. use the PcmCruise method"),
                                        "../assets/icons/lat.png", this));
  mainToggles->addItem(new ParamControl("CruiseStateControl", tr("Cruise State Controls"), tr("Openpilot controls cruise on/off, set speed"),
                                        "../assets/icons/lat.png", this));
  mainToggles->addItem(new ParamControl("IsHda2", tr("CANFD Car HDA2"), tr("Highway Drive Assist 2, turn it on"),
                                        "../assets/icons/hda.png", this));
  mainToggles->addItem(new ParamControl("CameraSccEnable", tr("CameraSCC"), tr("HDA1 CameraSCC CAR, HDA2 Connect the ADAS ECAN line to CAMERA modify, turn it on"),
                                        "../assets/icons/hda.png", this));
  mainToggles->addItem(new ParamControl("DriverCameraOnReverse", tr("Driver Camera On Reverse"), tr("Displays the driver camera when in reverse"),
                                        "../assets/icons/driver_face_static.png", this));
  mainToggles->addItem(new ParamControl("DriverCameraHardwareMissing", tr("DriverCamera Hardware Missing"), tr("If there is a problem with the driver camera hardware, drive without the driver camera"),
                                        "../assets/icons/driver_face_static_x.png", this));
  mainToggles->addItem(new ParamControl("PrebuiltEnable", tr("Prebuilt Enable"), tr("Create prebuilt file to speed bootup"),
                                        "../assets/icons/prebuilt.png", this));
  mainToggles->addItem(new ParamControl("LoggerEnable", tr("Logger Enable"), tr("Turn off this option to reduce system load"),
                                        "../assets/icons/logger.png", this));
  mainToggles->addItem(new ParamControl("RadarTrackEnable", tr("Enable Radar Track use"), tr("Enable Radar Track use (disable AEB)"),
                                        "../assets/icons/warning.png", this));

  // func
  QPushButton* gitpull_btn = new QPushButton("Git Pull");
  gitpull_btn->setObjectName("gitpull_btn");
  QObject::connect(gitpull_btn, &QPushButton::clicked, this, [this]() {
    if (ConfirmationDialog::confirm(tr("Git Fetch and Reset<br><br>Process?"), tr("Process"), this)) {
      QProcess::startDetached("/data/openpilot/scripts/gitpull.sh");
    }
    const QString file_path = "/data/check_network.log";
    if (QFile::exists(file_path)) {
      ConfirmationDialog::alert(tr("Please Check Network Connection"), this);
    }
  });

  QPushButton* cleardtc_btn = new QPushButton(tr("Clear DTC"));
  cleardtc_btn->setObjectName("cleardtc_btn");
  QObject::connect(cleardtc_btn, &QPushButton::clicked, this, [this]() {
    if (ConfirmationDialog::confirm(tr("Clear DTC<br><br>Process?"), tr("Process"), this)) {
      QProcess::startDetached("/data/openpilot/scripts/cleardtc.sh");
    }
  });

  QPushButton* gitcheckout_btn = new QPushButton("Git Checkout");
  gitcheckout_btn->setObjectName("gitcheckout_btn");
  QObject::connect(gitcheckout_btn, &QPushButton::clicked, this, [this]() {
    if (ConfirmationDialog::confirm(tr("Git Checkout<br><br>Process?"), tr("Process"), this)) {
      QProcess::startDetached("/data/openpilot/scripts/checkout.sh");
    }
  });

  QPushButton* gitreset_btn = new QPushButton(tr("Git Reset -1"));
  gitreset_btn->setObjectName("gitreset_btn");
  QObject::connect(gitreset_btn, &QPushButton::clicked, this, [this]() {
    if (ConfirmationDialog::confirm(tr("Git Reset<br><br>Process?"), tr("Process"), this)) {
      QProcess::startDetached("/data/openpilot/scripts/reset.sh");
    }
  });

  QPushButton* pandaflash_btn = new QPushButton(tr("Panda Flash"));
  pandaflash_btn->setObjectName("pandaflash_btn");
  QObject::connect(pandaflash_btn, &QPushButton::clicked, this, [this]() {
    if (ConfirmationDialog::confirm(tr("Panda Flash<br><br>Process?"), tr("Process"), this)) {
      QProcess::startDetached("/data/openpilot/panda/board/flash.py");
    }
  });

  QPushButton* pandarecover_btn = new QPushButton(tr("Panda Recover"));
  pandarecover_btn->setObjectName("pandarecover_btn");
  QObject::connect(pandarecover_btn, &QPushButton::clicked, this, [this]() {
    if (ConfirmationDialog::confirm(tr("Panda Recover<br><br>Process?"), tr("Process"), this)) {
      QProcess::startDetached("/data/openpilot/panda/board/recover.py");
    }
  });

  QPushButton* scons_rebuild_btn = new QPushButton(tr("Scons Rebuild"));
  scons_rebuild_btn->setObjectName("scons_rebuild_btn");
  QObject::connect(scons_rebuild_btn, &QPushButton::clicked, this, [this]() {
    if (ConfirmationDialog::confirm(tr("Scons Rebuild<br><br>Process?"), tr("Process"), this)) {
      QProcess::startDetached("/data/openpilot/scripts/scons_rebuild.sh");
    }
  });

  QPushButton* cameraview_btn = new QPushButton(tr("Camera View"));
  cameraview_btn->setObjectName("cameraview_btn");
  QObject::connect(cameraview_btn, &QPushButton::clicked, this, []() {
    QProcess::startDetached("/data/openpilot/selfdrive/ui/watch3.py");
  });

  QString buttonStyle = R"(
    QPushButton {
      height: 120px;
      border-radius: 15px;
      background-color: #393939;
    }
    QPushButton:pressed {
      background-color: #4a4a4a;
    }
  )";

  funcWidget = new QWidget(this);
  funcLayout = new QGridLayout(funcWidget);
  funcLayout->setSpacing(20);

  funcLayout->addWidget(gitpull_btn, 0, 0);
  funcLayout->addWidget(gitcheckout_btn, 0, 1);
  funcLayout->addWidget(gitreset_btn, 1, 0);
  funcLayout->addWidget(scons_rebuild_btn, 1, 1);
  funcLayout->addWidget(pandaflash_btn, 2, 0);
  funcLayout->addWidget(pandarecover_btn, 2, 1);
  funcLayout->addWidget(cameraview_btn, 3, 0);
  funcLayout->addWidget(cleardtc_btn, 3, 1);

  funcWidget->setStyleSheet(buttonStyle);

  QPushButton* can_missing_error_log_btn = new QPushButton(tr("can missing log View"));
  can_missing_error_log_btn->setObjectName("can_missing_error_log_btn");
  QObject::connect(can_missing_error_log_btn, &QPushButton::clicked, this, [this]() {
    const QString file_path = "/data/can_missing.log";
    if (QFile::exists(file_path)) {
      const std::string txt = util::read_file(file_path.toStdString());
      ConfirmationDialog::rich(QString::fromStdString(txt), this);
    } else {
      ConfirmationDialog::alert(tr("log file not found"), this);
    }
  });

  QPushButton* can_timeout_error_log_btn = new QPushButton(tr("can timeout log View"));
  can_timeout_error_log_btn->setObjectName("can_timeout_error_log_btn");
  QObject::connect(can_timeout_error_log_btn, &QPushButton::clicked, this, [this]() {
    const QString file_path = "/data/can_timeout.log";
    if (QFile::exists(file_path)) {
      const std::string txt = util::read_file(file_path.toStdString());
      ConfirmationDialog::rich(QString::fromStdString(txt), this);
    } else {
      ConfirmationDialog::alert(tr("log file not found"), this);
    }
  });

  QPushButton* tmux_error_log_btn = new QPushButton(tr("tmux log View"));
  tmux_error_log_btn->setObjectName("tmux_error_log_btn");
  QObject::connect(tmux_error_log_btn, &QPushButton::clicked, this, [this]() {
    const QString file_path = "/data/tmux_error.log";
    if (QFile::exists(file_path)) {
      const std::string txt = util::read_file(file_path.toStdString());
      ConfirmationDialog::rich(QString::fromStdString(txt), this);
    } else {
      ConfirmationDialog::alert(tr("log file not found"), this);
    }
  });

  QPushButton* tmux_console_btn = new QPushButton(tr("tmux console View"));
  tmux_console_btn->setObjectName("tmux_console_btn");
  QObject::connect(tmux_console_btn, &QPushButton::clicked, this, [this]() {
    QProcess process;
    QStringList arguments;
    arguments << "capture-pane" << "-p" << "-t" << "0" << "-S" << "-250";
    process.start("tmux", arguments);
    process.waitForFinished();
    QString output = process.readAllStandardOutput();
    ConfirmationDialog::rich(output, this);
  });

  QPushButton* tmux_error_log_upload_btn = new QPushButton(tr("tmux log Upload"));
  tmux_error_log_upload_btn->setObjectName("tmux_error_log_upload_btn");
  QObject::connect(tmux_error_log_upload_btn, &QPushButton::clicked, this, [this]() {
    const QString file_path = "/data/tmux_error.log";
    if (QFile::exists(file_path)) {
      if (ConfirmationDialog::confirm(tr("tmux log upload<br><br>Process?"), tr("Process"), this)) {
        QProcess::startDetached("/data/openpilot/scripts/log_upload.sh tmux_error.log");
    }
    } else {
      ConfirmationDialog::alert(tr("log file not found"), this);
    }
  });

  QPushButton* tmux_console_upload_btn = new QPushButton(tr("tmux console Upload"));
  tmux_console_upload_btn->setObjectName("tmux_console_upload_btn");
  QObject::connect(tmux_console_upload_btn, &QPushButton::clicked, this, [this]() {
    int exitCode = QProcess::startDetached("sh", QStringList() << "-c" << "tmux capture-pane -p -t 0 -S -250 > /data/tmux_console.log");
    if (exitCode == 0) {
      if (ConfirmationDialog::confirm(tr("tmux console log upload<br><br>Process?"), tr("Process"), this)) {
        QProcess::startDetached("/data/openpilot/scripts/log_upload.sh tmux_console.log");
      }
    } else {
      ConfirmationDialog::alert(tr("log file not found"), this);
    }
  });

  QPushButton* carparams_dump_upload_btn = new QPushButton(tr("carParams dump Upload"));
  carparams_dump_upload_btn->setObjectName("carparams_dump_upload_btn");
  QObject::connect(carparams_dump_upload_btn, &QPushButton::clicked, this, [this]() {
    if (ConfirmationDialog::confirm(tr("carParams dump upload<br><br>Process?"), tr("Process"), this)) {
      QProcess::startDetached("/data/openpilot/scripts/dump_upload.sh carParams");
    }
  });

  // realdata upload btn
  QString targetPath = "/data/media/0/realdata";
  QString scriptPath = "/data/openpilot/scripts/realdata_upload.sh";

  struct RouteInfo {
    QString routeName;
    QStringList segmentPaths;
    QDateTime lastModified;
    int segmentCount;
  };

  QPushButton* realdate_upload_btn = new QPushButton(tr("Realdata Routes Upload"));
  connect(realdate_upload_btn, &QPushButton::clicked, [=]() {
    QDir dir(targetPath);
    if (!dir.exists()) {
      ConfirmationDialog::alert(tr("Path does not exist"), this);
      return;
    }

    QFileInfoList fileInfoList = dir.entryInfoList(QDir::Dirs | QDir::NoDotAndDotDot);
    fileInfoList.erase(std::remove_if(fileInfoList.begin(), fileInfoList.end(),
                                     [](const QFileInfo& info) { return info.fileName() == "boot"; }),
                       fileInfoList.end());

    QMap<QString, RouteInfo> routeMap;

    for (const QFileInfo &fileInfo : fileInfoList) {
      QString folderName = fileInfo.fileName();

      QStringList parts = folderName.split("--");
      if (parts.size() >= 3) {
        QString routeName = parts[0] + "--" + parts[1];

        if (!routeMap.contains(routeName)) {
          RouteInfo routeInfo;
          routeInfo.routeName = routeName;
          routeInfo.segmentCount = 0;
          routeInfo.lastModified = fileInfo.lastModified();
          routeMap[routeName] = routeInfo;
        }

        routeMap[routeName].segmentPaths.append(fileInfo.absoluteFilePath());
        routeMap[routeName].segmentCount++;

        if (fileInfo.lastModified() > routeMap[routeName].lastModified) {
          routeMap[routeName].lastModified = fileInfo.lastModified();
        }
      }
    }

    if (routeMap.isEmpty()) {
      ConfirmationDialog::alert(tr("Routes do not exist"), this);
      return;
    }

    QList<RouteInfo> sortedRoutes = routeMap.values();
    std::sort(sortedRoutes.begin(), sortedRoutes.end(),
              [](const RouteInfo& a, const RouteInfo& b) {
                  return a.lastModified > b.lastModified;
              });

    QStringList routeDisplayNames;
    QMap<QString, RouteInfo> displayToRoute;

    for (const RouteInfo &route : sortedRoutes) {
      QString displayName = QString("%1 (%2 segments)")
                           .arg(route.routeName)
                           .arg(route.segmentCount);
      routeDisplayNames.append(displayName);
      displayToRoute[displayName] = route;
    }

    QString selectedRoute = MultiOptionDialog::getSelection(
      tr("Select Route to Upload"),
      routeDisplayNames, "", this);

    if (!selectedRoute.isEmpty()) {
      RouteInfo selectedRouteInfo = displayToRoute[selectedRoute];

      if (ConfirmationDialog::confirm(
        tr("Upload route: %1\nSegments: %2\nAre you sure?")
        .arg(selectedRouteInfo.routeName)
        .arg(selectedRouteInfo.segmentCount),
        tr("Upload"), this)) {

        this->uploadRouteSegments(selectedRouteInfo.segmentPaths, scriptPath);
        emit closeSettings();
      }
    }
  });

  logWidget = new QWidget(this);
  logLayout = new QGridLayout(logWidget);
  logLayout->setSpacing(20);

  logLayout->addWidget(tmux_error_log_btn, 0, 0);
  logLayout->addWidget(tmux_error_log_upload_btn, 0, 1);
  logLayout->addWidget(tmux_console_btn, 1, 0);
  logLayout->addWidget(tmux_console_upload_btn, 1, 1);
  logLayout->addWidget(can_missing_error_log_btn, 2, 0);
  logLayout->addWidget(can_timeout_error_log_btn, 2, 1);
  logLayout->addWidget(carparams_dump_upload_btn, 3, 0);
  logLayout->addWidget(realdate_upload_btn, 3, 1);

  logWidget->setStyleSheet(buttonStyle);

  toggles_layout->addWidget(mainToggles);
  toggles_layout->addWidget(funcWidget);
  toggles_layout->addWidget(logWidget);

  ScrollView* toggles_view = new ScrollView(toggles, this);
  communityLayout->addWidget(toggles_view, 1);

  homeScreen->setLayout(communityLayout);
  main_layout->addWidget(homeScreen);
  main_layout->setCurrentWidget(homeScreen);

  togglesCommunity(0);
}

void CommunityPanel::uploadRouteSegments(const QStringList& segmentPaths, const QString& scriptPath) {
  QProcess* uploadProcess = new QProcess(this);

  connect(uploadProcess, QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished),
              [=](int exitCode, QProcess::ExitStatus exitStatus) {
              uploadProcess->deleteLater();
              if (exitCode == 0) {
                ConfirmationDialog::alert(tr("Upload completed successfully"), this);
              } else {
                ConfirmationDialog::alert(tr("Upload failed"), this);
              }
          });

  QStringList arguments;
  for (const QString& path : segmentPaths) {
    arguments << QString("\"%1\"").arg(path);
  }

  QString command = QString("%1 %2").arg(scriptPath, arguments.join(" "));
  uploadProcess->start("sh", QStringList() << "-c" << command);
}

void CommunityPanel::togglesCommunity(int widgetIndex) {
  currentCommunityIndex = widgetIndex;
  mainToggles->setVisible(widgetIndex == 0);
  funcWidget->setVisible(widgetIndex == 1);
  logWidget->setVisible(widgetIndex == 2);
}

void CommunityPanel::blueButtonStyle(QPushButton* button) {
  button->setStyleSheet(R"(
    QPushButton {
      height: 120px; border-radius: 15px; background-color: #2C2CE2;
    }
    QPushButton:pressed {
      background-color: #2424FF;
    }
  )");
}

void CommunityPanel::updateButtonStyles() {
  QString styleSheet = R"(
    QPushButton {
      height: 120px; border-radius: 15px; background-color: #393939;
    }
    QPushButton:pressed {
      background-color: #4a4a4a;
    }
  )";

  switch (currentCommunityIndex) {
  case 0:
    styleSheet += "#toggle_btn { background-color: #33ab4c; }";
    break;
  case 1:
    styleSheet += "#func_btn { background-color: #33ab4c; }";
    break;
  case 2:
    styleSheet += "#log_btn { background-color: #33ab4c; }";
    break;
  }

  setStyleSheet(styleSheet);
}
