#pragma once

#include <QButtonGroup>
#include <QFileSystemWatcher>
#include <QFrame>
#include <QLabel>
#include <QPushButton>
#include <QStackedWidget>
#include <QWidget>
#include <QStackedLayout>


#include "selfdrive/ui/qt/widgets/controls.h"

// ********** settings window + top-level panels **********
class SettingsWindow : public QFrame {
  Q_OBJECT

public:
  explicit SettingsWindow(QWidget *parent = 0);
  void setCurrentPanel(int index);

protected:
  void showEvent(QShowEvent *event) override;

signals:
  void closeSettings();
  void reviewTrainingGuide();
  void showDriverView();

private:
  QPushButton *sidebar_alert_widget;
  QWidget *sidebar_widget;
  QButtonGroup *nav_btns;
  QStackedWidget *panel_widget;
  Params params;
};


class DevicePanel : public ListWidget {
  Q_OBJECT

public:
  explicit DevicePanel(SettingsWindow *parent);

signals:
  void reviewTrainingGuide();
  void showDriverView();
  void closeSettings();

private slots:
  void poweroff();
  void reboot();
  void updateCalibDescription();

private:
  Params params;
};


class TogglesPanel : public ListWidget {
  Q_OBJECT

public:
  explicit TogglesPanel(SettingsWindow *parent);

private:
  Params params;
  std::map<std::string, ParamControl*> toggles;

  void updateToggles();
  void showEvent(QShowEvent *event) override;
};


class SoftwarePanel : public ListWidget {
  Q_OBJECT

public:
  explicit SoftwarePanel(QWidget* parent = nullptr);

private:
  void showEvent(QShowEvent *event) override;
  void updateLabels();
  void checkForUpdates();

  bool is_onroad = false;

  QLabel *onroadLbl;
  LabelControl *versionLbl;
  LabelControl *gitRemoteLbl;
  LabelControl *gitBranchLbl;
  LabelControl *gitCommitLbl;
  ButtonControl *installBtn;
  ButtonControl *downloadBtn;
  ButtonControl *targetBranchBtn;

  Params params;
  QFileSystemWatcher *fs_watch;
};

class SelectManufacturer : public QWidget {
  Q_OBJECT

public:
  explicit SelectManufacturer(QWidget* parent = 0);

private:

signals:
  void backPress();
  void selectedManufacturer();
};

class SelectCar : public QWidget {
  Q_OBJECT

public:
  explicit SelectCar(QWidget* parent = 0);

private:

signals:
  void backPress();
  void selectedCar();
};

class CommunityPanel : public QWidget {
  Q_OBJECT

private:
  QStackedLayout* main_layout = nullptr;
  QWidget* homeScreen = nullptr;
  SelectCar* selectCar = nullptr;
  SelectManufacturer* selectManufacturer = nullptr;
  QWidget* homeWidget;

public:
  explicit CommunityPanel(QWidget *parent = nullptr);
};

// LateralControlSelect
class LateralControlSelect : public AbstractControl {
  Q_OBJECT

public:
  LateralControlSelect();

private:
  QPushButton btnplus;
  QPushButton btnminus;
  QLabel label;

  void refresh();
};

// MfcSelect
class MfcSelect : public AbstractControl {
  Q_OBJECT

public:
  MfcSelect();

private:
  QPushButton btnplus;
  QPushButton btnminus;
  QLabel label;

  void refresh();
};

// AebSelect
class AebSelect : public AbstractControl {
  Q_OBJECT

public:
  AebSelect();

private:
  QPushButton btnplus;
  QPushButton btnminus;
  QLabel label;

  void refresh();
};

// SccCommandsSelect
class SccCommandsSelect : public AbstractControl {
  Q_OBJECT

public:
  SccCommandsSelect();

private:
  QPushButton btnplus;
  QPushButton btnminus;
  QLabel label;

  void refresh();
};
