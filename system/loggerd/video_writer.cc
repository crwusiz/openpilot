#pragma clang diagnostic ignored "-Wdeprecated-declarations"
#include <cassert>

#include "system/loggerd/video_writer.h"
#include "common/swaglog.h"
#include "common/util.h"

VideoWriter::VideoWriter(const char *path, const char *filename, bool remuxing, int width, int height, int fps, cereal::EncodeIndex::Type codec)
  : remuxing(remuxing) {
  vid_path = util::string_format("%s/%s", path, filename);
  lock_path = util::string_format("%s/%s.lock", path, filename);

  int lock_fd = HANDLE_EINTR(open(lock_path.c_str(), O_RDWR | O_CREAT, 0664));
  assert(lock_fd >= 0);
  close(lock_fd);

  LOGD("encoder_open %s remuxing:%d", this->vid_path.c_str(), this->remuxing);
  if (this->remuxing) {
    bool raw = (codec == cereal::EncodeIndex::Type::BIG_BOX_LOSSLESS);
    avformat_alloc_output_context2(&this->ofmt_ctx, NULL, raw ? "matroska" : NULL, this->vid_path.c_str());
    assert(this->ofmt_ctx);

    // set codec correctly. needed?
    assert(codec != cereal::EncodeIndex::Type::FULL_H_E_V_C);
    const AVCodec *avcodec = avcodec_find_encoder(raw ? AV_CODEC_ID_FFVHUFF : AV_CODEC_ID_H264);
    assert(avcodec);

    this->codec_ctx = avcodec_alloc_context3(avcodec);
    assert(this->codec_ctx);
    this->codec_ctx->width = width;
    this->codec_ctx->height = height;
    this->codec_ctx->pix_fmt = AV_PIX_FMT_YUV420P;
    this->codec_ctx->time_base = (AVRational){ 1, fps };

    if (codec == cereal::EncodeIndex::Type::BIG_BOX_LOSSLESS) {
      // without this, there's just noise
      int err = avcodec_open2(this->codec_ctx, avcodec, NULL);
      assert(err >= 0);
    }

    this->out_stream = avformat_new_stream(this->ofmt_ctx, raw ? avcodec : NULL);
    assert(this->out_stream);

    int err = avio_open(&this->ofmt_ctx->pb, this->vid_path.c_str(), AVIO_FLAG_WRITE);
    assert(err >= 0);

  } else {
    this->of = util::safe_fopen(this->vid_path.c_str(), "wb");
    assert(this->of);
  }
}

void VideoWriter::write(uint8_t *data, int len, long long timestamp, bool codecconfig, bool keyframe) {
  if (of && data) {
    size_t written = util::safe_fwrite(data, 1, len, of);
    if (written != len) {
      LOGE("failed to write file.errno=%d", errno);
    }
  }

  if (remuxing) {
    if (codecconfig) {
      if (len > 0) {
        codec_ctx->extradata = (uint8_t*)av_mallocz(len + AV_INPUT_BUFFER_PADDING_SIZE);
        codec_ctx->extradata_size = len;
        memcpy(codec_ctx->extradata, data, len);
      }
      int err = avcodec_parameters_from_context(out_stream->codecpar, codec_ctx);
      assert(err >= 0);
      err = avformat_write_header(ofmt_ctx, NULL);
      assert(err >= 0);
    } else {
      // input timestamps are in microseconds
      AVRational in_timebase = {1, 1000000};

      AVPacket pkt;
      av_init_packet(&pkt);
      pkt.data = data;
      pkt.size = len;

      enum AVRounding rnd = static_cast<enum AVRounding>(AV_ROUND_NEAR_INF|AV_ROUND_PASS_MINMAX);
      pkt.pts = pkt.dts = av_rescale_q_rnd(timestamp, in_timebase, ofmt_ctx->streams[0]->time_base, rnd);
      pkt.duration = av_rescale_q(50*1000, in_timebase, ofmt_ctx->streams[0]->time_base);

      if (keyframe) {
        pkt.flags |= AV_PKT_FLAG_KEY;
      }

      // TODO: can use av_write_frame for non raw?
      int err = av_interleaved_write_frame(ofmt_ctx, &pkt);
      if (err < 0) { LOGW("ts encoder write issue len: %d ts: %lld", len, timestamp); }

      av_packet_unref(&pkt);
    }
  }
}

VideoWriter::~VideoWriter() {
  if (this->remuxing) {
    int err = av_write_trailer(this->ofmt_ctx);
    if (err != 0) LOGE("av_write_trailer failed %d", err);
    avcodec_free_context(&this->codec_ctx);
    err = avio_closep(&this->ofmt_ctx->pb);
    if (err != 0) LOGE("avio_closep failed %d", err);
    avformat_free_context(this->ofmt_ctx);
  } else {
    util::safe_fflush(this->of);
    fclose(this->of);
    this->of = nullptr;
  }
  unlink(this->lock_path.c_str());
}
