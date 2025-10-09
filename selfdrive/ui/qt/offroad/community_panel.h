#pragma once

#include "selfdrive/ui/qt/offroad/settings.h"

class CommunityPanel : public QWidget {
  Q_OBJECT
public:
  explicit CommunityPanel(QWidget *parent = nullptr);

signals:
  void closeSettings();

private slots:
  void uploadRouteSegments(const QStringList& segmentPaths, const QString& scriptPath);

private:
  Params params;

  struct RouteInfo {
    QString routeName;
    QStringList segmentPaths;
    QDateTime lastModified;
    int segmentCount;
  };

  QStackedLayout* main_layout = nullptr;
  QWidget* homeScreen = nullptr;
  int currentCommunityIndex = 0;

  ListWidget* mainToggles;
  QWidget* funcWidget;
  QWidget* logWidget;

  QGridLayout* funcLayout;
  QGridLayout* logLayout;

  void togglesCommunity(int widgetIndex);
  void blueButtonStyle(QPushButton* button);
  void updateButtonStyles();
};
