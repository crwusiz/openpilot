#pragma once

#include <QStackedLayout>
#include <QWidget>

#include "selfdrive/ui/qt/widgets/cameraview.h"
#include "selfdrive/ui/ui.h"

#ifdef QCOM2
#include <QTimer>
#include "selfdrive/ui/qt/screenrecorder/screenrecorder.h"
#endif


// ***** onroad widgets *****

class OnroadHud : public QWidget {
  Q_OBJECT

public:
  explicit OnroadHud(QWidget *parent);
  void updateState(const UIState &s);

private:
  void drawIcon(QPainter &p, int x, int y, QPixmap &img, QBrush bg, float opacity);
  void drawText(QPainter &p, int x, int y, const QString &text, int alpha = 255);
  void drawTextWithColor(QPainter &p, int x, int y, const QString &text, QColor& color);
  void paintEvent(QPaintEvent *event) override;

  const int radius = 192;
  const int img_size = (radius / 2) * 1.5;

  // neokii
  QPixmap ic_brake;
  QPixmap ic_autohold_warning;
  QPixmap ic_autohold_active;
  QPixmap ic_nda;
  QPixmap ic_hda;
  QPixmap ic_tire_pressure;
  QPixmap ic_turn_signal_l;
  QPixmap ic_turn_signal_r;
  QPixmap ic_satellite;

  inline QColor redColor(int alpha = 255) { return QColor(201, 34, 49, alpha); }
  void drawLaneLines(QPainter &painter, const UIScene &scene);
  void drawLead(QPainter &painter, const cereal::ModelDataV2::LeadDataV3::Reader &lead_data, const QPointF &vd, bool is_radar);

  void drawText2(QPainter &p, int x, int y, int flags, const QString &text, const QColor& color);

  void drawMaxSpeed(QPainter &p, UIState& s);
  void drawSpeed(QPainter &p, UIState& s);
  void drawBottomIcons(QPainter &p, UIState& s);
  void drawSpeedLimit(QPainter &p, UIState& s);
  void drawTurnSignals(QPainter &p, UIState& s);
  void drawGpsStatus(QPainter &p, UIState& s);
  void drawDebugText(QPainter &p, UIState& s);

public:
  void drawCommunity(QPainter &p, UIState& s);

signals:
  void valueChanged();
};

class OnroadAlerts : public QWidget {
  Q_OBJECT

public:
  OnroadAlerts(QWidget *parent = 0) : QWidget(parent) {};
  void updateAlert(const Alert &a, const QColor &color);

protected:
  void paintEvent(QPaintEvent*) override;

private:
  QColor bg;
  Alert alert = {};
};

// container window for the NVG UI
class NvgWindow : public CameraViewWidget {
  Q_OBJECT

public:
  explicit NvgWindow(VisionStreamType type, QWidget* parent = 0) : CameraViewWidget("camerad", type, true, parent) {}
  OnroadHud *hud;
  void paintGL() override;
protected:
  void initializeGL() override;
  void showEvent(QShowEvent *event) override;
  void updateFrameMat(int w, int h) override;
  //void drawLaneLines(QPainter &painter, const UIScene &scene);
  //void drawLead(QPainter &painter, const cereal::ModelDataV2::LeadDataV3::Reader &lead_data, const QPointF &vd);
  inline QColor redColor(int alpha = 255) { return QColor(201, 34, 49, alpha); }
  double prev_draw_t = 0;
};

// container for all onroad widgets
class OnroadWindow : public QWidget {
  Q_OBJECT

public:
  OnroadWindow(QWidget* parent = 0);
  bool isMapVisible() const { return map && map->isVisible(); }

protected:
  void mousePressEvent(QMouseEvent* e) override;
  void mouseReleaseEvent(QMouseEvent* e) override;

private:
  void paintEvent(QPaintEvent *event);
  OnroadHud *hud;
  OnroadAlerts *alerts;
  NvgWindow *nvg;
  QColor bg = bg_colors[STATUS_DISENGAGED];
  QWidget *map = nullptr;
  QHBoxLayout* split;

  // neokii
#ifdef QCOM2
private:
  ScreenRecoder* recorder;
  std::shared_ptr<QTimer> record_timer;
  QPoint startPos;
#endif

signals:
  void updateStateSignal(const UIState &s);
  void offroadTransitionSignal(bool offroad);

private slots:
  void offroadTransition(bool offroad);
  void updateState(const UIState &s);
};
