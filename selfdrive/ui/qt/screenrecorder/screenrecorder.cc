#include <CL/cl.h>
#include <algorithm>
#include <time.h>
#include <dirent.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <sys/time.h>

#include "libyuv.h"
#include "selfdrive/camerad/transforms/rgb_to_yuv.h"
#include "selfdrive/common/clutil.h"

#include "selfdrive/ui/qt/screenrecorder/screenrecorder.h"
#include "selfdrive/ui/qt/util.h"
#include "selfdrive/ui/ui.h"
#include "selfdrive/hardware/hw.h"

static long long milliseconds(void) {
    struct timeval tv;
    gettimeofday(&tv,NULL);
    return (((long long)tv.tv_sec)*1000)+(tv.tv_usec/1000);
}

ScreenRecoder::ScreenRecoder(QWidget *parent) : QPushButton(parent) {

    recording = false;
    started = 0;

    const int size = 190;
    setFixedSize(size, size);
    setFocusPolicy(Qt::NoFocus);
    connect(this, SIGNAL(pressed()),this,SLOT(btnPressed()));
    connect(this, SIGNAL(released()),this,SLOT(btnReleased()));

    setText("R");

    const int bitrate = Hardware::TICI() ? 4*1024*1024 : 3*1024*1024;

    std::string path = "/data/media/0/videos";
    src_width = 2160;
    src_height = 1080;

    if(Hardware::EON()) {
        path = "/storage/emulated/0/videos";
        src_width = 1920;
    }

    src_width -= bdr_s * 2;
    src_height -= bdr_s * 2;

    dst_height = 720;
    dst_width = src_width * dst_height / src_height;
    if(dst_width % 2 != 0)
        dst_width += 1;

    rgb_buffer = std::make_unique<uint8_t[]>(src_width*src_height*4);
    rgb_scale_buffer = std::make_unique<uint8_t[]>(dst_width*dst_height*4);

    encoder = std::make_unique<OmxEncoder>(path.c_str(), dst_width, dst_height, 20, bitrate, false, false);

    soundStart.setSource(QUrl::fromLocalFile("../assets/sounds/start_record.wav"));
    soundStop.setSource(QUrl::fromLocalFile("../assets/sounds/stop_record.wav"));

    soundStart.setVolume(0.5f);
    soundStop.setVolume(0.5f);
}

ScreenRecoder::~ScreenRecoder() {
  stop(false);
}

void ScreenRecoder::paintEvent(QPaintEvent *event) {

    QRect r = QRect(0, 0, width(), height());
    r -= QMargins(2, 2, 2, 2);
    QColor bg = recording ? QColor::fromRgbF(1., 0, 0, 0.7) : QColor::fromRgbF(0, 0, 0, 0.3);
    QPainter p(this);

    //p.setPen(Qt::NoPen);
    p.setPen(QPen(QColor::fromRgbF(1., 1., 1., 0.7), 4, Qt::SolidLine, Qt::FlatCap));
    p.setCompositionMode(QPainter::CompositionMode_SourceOver);

    p.setBrush(QBrush(bg));
    p.drawEllipse(r);

    // text
    const QPoint c = r.center();
    p.setPen(QColor(0xff, 0xff, 0xff));
    p.setRenderHint(QPainter::TextAntialiasing);
    configFont(p, "Open Sans", 100, "SemiBold");
    p.drawText(r, Qt::AlignCenter, "R");
}

void ScreenRecoder::btnReleased(void) {
    toggle();
}

void ScreenRecoder::btnPressed(void) {
}

void ScreenRecoder::openEncoder(const char* filename) {
    encoder->encoder_open(filename);
}

void ScreenRecoder::closeEncoder() {
    if(encoder)
        encoder->encoder_close();
}

void ScreenRecoder::toggle() {

    if(!recording)
        start(true);
    else
        stop(true);
}

void ScreenRecoder::start(bool sound) {

    if(recording)
      return;

    char filename[64];
    time_t t = time(NULL);
    struct tm tm = *localtime(&t);
    snprintf(filename,sizeof(filename),"%04d%02d%02d-%02d%02d%02d.mp4", tm.tm_year + 1900, tm.tm_mon + 1, tm.tm_mday, tm.tm_hour, tm.tm_min, tm.tm_sec);

    openEncoder(filename);
    recording = true;
    update();

    started = milliseconds();

    if(sound)
        soundStart.play();
}

void ScreenRecoder::stop(bool sound) {

    if(recording) {
      closeEncoder();
      recording = false;
      update();

      if(sound)
          soundStop.play();
    }
}

void ScreenRecoder::ui_draw(UIState *s, int w, int h) {

    if(recording && encoder) {

        if(milliseconds() - started > 1000*60*3) {
            stop(false);
            start(false);
            return;
        }

        int src_w = std::min(src_width, w);
        int src_h = std::min(src_height, h);

        glReadPixels(0, 0, src_w, src_h, GL_RGBA, GL_UNSIGNED_BYTE, rgb_buffer.get());

        libyuv::ARGBScale(rgb_buffer.get(), src_w*4,
              src_w, src_h,
              rgb_scale_buffer.get(), dst_width*4,
              dst_width, dst_height,
              libyuv::kFilterBilinear);

        const uint8_t* src = rgb_scale_buffer.get();
        const uint8_t* dst = rgb_buffer.get();

        int bytes_per_line = dst_width*4;
        for (int i = 0; i < dst_height; i++) {
            memcpy((void*)(dst + bytes_per_line * i),
                   (void*)(src + bytes_per_line * (dst_height - i - 1)),
                   bytes_per_line);
        }

        encoder->encode_frame_rgba(dst, dst_width, dst_height, (uint64_t)nanos_since_boot());
    }
}