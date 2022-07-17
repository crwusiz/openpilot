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

protected:
  void hideEvent(QHideEvent *event) override;
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
};


class SoftwarePanel : public ListWidget {
  Q_OBJECT

public:
  explicit SoftwarePanel(QWidget* parent = nullptr);

private:
  void showEvent(QShowEvent *event) override;
  void updateLabels();

  LabelControl *gitRemoteLbl;
  LabelControl *gitBranchLbl;
  LabelControl *gitCommitLbl;
  LabelControl *osVersionLbl;
  LabelControl *versionLbl;
  LabelControl *lastUpdateLbl;
  ButtonControl *updateBtn;

  Params params;
  QFileSystemWatcher *fs_watch;
};


class C2NetworkPanel: public QWidget {
  Q_OBJECT

public:
  explicit C2NetworkPanel(QWidget* parent = nullptr);

private:
  void showEvent(QShowEvent *event) override;
  QString getIPAddress();
  LabelControl *ipaddress;
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

// LongControlSelect
class LongControlSelect : public AbstractControl {
  Q_OBJECT

public:
  LongControlSelect();

private:
  QPushButton btnplus;
  QPushButton btnminus;
  QLabel label;

  void refresh();
};