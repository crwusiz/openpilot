#pragma once

#include <QPainter>
#include <QPushButton>

#include "selfdrive/loggerd/encoder.h"
#include "selfdrive/loggerd/logger.h"
#if defined(QCOM) || defined(QCOM2)
#include "selfdrive/loggerd/omx_encoder.h"
#define Encoder OmxEncoder
#else
#include "selfdrive/loggerd/raw_logger.h"
#define Encoder RawLogger
#endif

class ScreenRecoder : public QPushButton {
  Q_OBJECT

public:
  ScreenRecoder(QWidget *parent = 0);
  //void update(const Alert &a, const QColor &color);
public slots:
    void btnReleased(void);
    void btnPressed(void);

protected:
  void paintEvent(QPaintEvent*) override;

private:
    bool recording;
    Encoder* encoder;

    void initEncoder();

    void toggle();

};