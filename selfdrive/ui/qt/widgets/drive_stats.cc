#include "selfdrive/ui/qt/widgets/drive_stats.h"

#include <QDebug>
#include <QGridLayout>
#include <QJsonObject>
#include <QVBoxLayout>

#include "common/params.h"
#include "selfdrive/ui/qt/request_repeater.h"
#include "selfdrive/ui/qt/util.h"

static QLabel* newLabel(const QString& text, const QString &type) {
  QLabel* label = new QLabel(text);
  label->setProperty("type", type);
  return label;
}

DriveStats::DriveStats(QWidget* parent) : QFrame(parent) {
  metric_ = Params().getBool("IsMetric");

  QVBoxLayout* main_layout = new QVBoxLayout(this);
  main_layout->setContentsMargins(50, 50, 50, 60);

  auto add_stats_layouts = [=](const QString &title, StatsLabels& labels) {
    QGridLayout* grid_layout = new QGridLayout;
    grid_layout->setVerticalSpacing(20);
    grid_layout->setContentsMargins(0, 10, 0, 10);

    grid_layout->addWidget(newLabel(tr("Drive Stats"), "title"), 0, 0, 1, 3, Qt::AlignCenter);
    grid_layout->addWidget(new QLabel("────────────────────────────────"), 3, 0, 1, 3, Qt::AlignCenter);

    grid_layout->addWidget(labels.routes = newLabel("0", "number"), 4, 0, Qt::AlignCenter);
    grid_layout->addWidget(labels.hours = newLabel("0", "number"), 4, 1, Qt::AlignCenter);
    grid_layout->addWidget(labels.distance = newLabel("0", "number"), 4, 2, Qt::AlignCenter);

    grid_layout->addWidget(newLabel(tr("Drives"), "unit"), 6, 0, Qt::AlignCenter);
    grid_layout->addWidget(newLabel(tr("Hours"), "unit"), 6, 1, Qt::AlignCenter);
    grid_layout->addWidget(labels.distance_unit = newLabel(getDistanceUnit(), "unit"), 6, 2, Qt::AlignCenter);

    grid_layout->addWidget(new QLabel("────────────────────────────────"), 8, 0, 1, 3, Qt::AlignCenter);

    grid_layout->addWidget(new QLabel("━ crwusiz branch ━"), 9, 0, 1, 3, Qt::AlignCenter);

    grid_layout->addWidget(new QLabel("「 Easy Driving \U0001f60b 」"), 11, 0, 1, 3, Qt::AlignCenter);
    main_layout->addLayout(grid_layout);
  };

  add_stats_layouts(tr("ALL TIME"), all_);
  main_layout->addStretch();

  if (auto dongleId = getDongleId()) {
    QString url = CommaApi::BASE_URL + "/v1.1/devices/" + *dongleId + "/stats";
    RequestRepeater* repeater = new RequestRepeater(this, url, "ApiCache_DriveStats", 30);
    QObject::connect(repeater, &RequestRepeater::requestDone, this, &DriveStats::parseResponse);
  }

  setStyleSheet(R"(
    DriveStats {
      background-color: #333333;
      border-radius: 10px;
    }

    QLabel[type="title"] { font-size: 51px; font-weight: 500; }
    QLabel[type="number"] { font-size: 78px; font-weight: 500; }
    QLabel[type="unit"] { font-size: 40px; font-weight: 300; color: #A0A0A0; }
    QLabel {font-size: 40px; font-weight: 500;}
  )");
}

void DriveStats::updateStats() {
  auto update = [=](const QJsonObject& obj, StatsLabels& labels) {
    labels.routes->setText(QString::number((int)obj["routes"].toDouble()));
    labels.distance->setText(QString::number(int(obj["distance"].toDouble() * (metric_ ? MILE_TO_KM : 1))));
    labels.distance_unit->setText(getDistanceUnit());
    labels.hours->setText(QString::number((int)(obj["minutes"].toDouble() / 60)));
  };

  QJsonObject json = stats_.object();
  update(json["all"].toObject(), all_);
  //update(json["week"].toObject(), week_);
}

void DriveStats::parseResponse(const QString& response, bool success) {
  if (!success) return;

  QJsonDocument doc = QJsonDocument::fromJson(response.trimmed().toUtf8());
  if (doc.isNull()) {
    qDebug() << "JSON Parse failed on getting past drives statistics";
    return;
  }
  stats_ = doc;
  updateStats();
}

void DriveStats::showEvent(QShowEvent* event) {
  bool metric = Params().getBool("IsMetric");
  if (metric_ != metric) {
    metric_ = metric;
    updateStats();
  }
}
