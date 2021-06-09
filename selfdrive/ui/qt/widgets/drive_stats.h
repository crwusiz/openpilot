#pragma once

#include <QLabel>

class DriveStats : public QWidget {
  Q_OBJECT

public:
  explicit DriveStats(QWidget* parent = 0);

private:
  bool metric;
  struct StatsLabels {
    QLabel *routes, *hours, *distance;
  } all_;

private slots:
  void parseResponse(const QString &response);
};
