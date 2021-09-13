
#include "selfdrive/ui/qt/screenrecorder/screenrecorder.h"
#include "selfdrive/ui/qt/util.h"
#include "selfdrive/hardware/hw.h"

ScreenRecoder::ScreenRecoder(QWidget *parent) : QPushButton(parent) {

    recording = false;

    const int size = 180;
    setFixedSize(size, size);
    setFocusPolicy(Qt::NoFocus);
    connect(this, SIGNAL(pressed()),this,SLOT(btnPressed()));
    connect(this, SIGNAL(released()),this,SLOT(btnReleased()));

    setText("R");
}

void ScreenRecoder::paintEvent(QPaintEvent *event) {

    QRect r = QRect(0, 0, width(), height());
    QColor bg = recording ? QColor::fromRgbF(1., 0, 0, 0.5) : QColor::fromRgbF(1., 1., 1., 0.3);
    QPainter p(this);

    p.setPen(Qt::NoPen);
    p.setCompositionMode(QPainter::CompositionMode_SourceOver);

    p.setBrush(QBrush(bg));
    p.drawEllipse(r);

    // text
    const QPoint c = r.center();
    p.setPen(QColor(0xff, 0xff, 0xff));
    p.setRenderHint(QPainter::TextAntialiasing);
    configFont(p, "Open Sans", 80, "SemiBold");
    p.drawText(r, Qt::AlignCenter, "R");
}

void ScreenRecoder::btnReleased(void) {
    toggle();
}

void ScreenRecoder::btnPressed(void) {
}

void ScreenRecoder::initEncoder(const char* filename) {

    const int bitrate = Hardware::TICI() ? 10000000 : 5000000;
    encoder = new Encoder(filename, width, height, 20, bitrate, false, false);
}

void ScreenRecoder::toggle() {
    recording = !recording;
    update();
}