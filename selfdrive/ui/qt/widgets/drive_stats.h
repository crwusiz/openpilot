#pragma once

#include <QJsonDocument>
#include <QLabel>

class DriveStats : public QFrame {
  Q_OBJECT

public:
  explicit DriveStats(QWidget* parent = 0);

private:
  void showEvent(QShowEvent *event) override;
  void updateStats();
  inline QString getDistanceUnit() const { return metric_ ? "㎞" : "MILES"; }

  bool metric_;
  QJsonDocument stats_;
  struct StatsLabels {
    QLabel *routes, *hours, *distance, *distance_unit;
  } all_;

private slots:
  void parseResponse(const QString &response);
};
