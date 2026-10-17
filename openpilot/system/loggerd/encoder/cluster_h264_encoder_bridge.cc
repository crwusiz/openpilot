#include "system/loggerd/encoder/cluster_h264_encoder_bridge.h"

#include <memory>
#include <new>
#include <stdexcept>
#include <string>
#include <utility>

#include "system/loggerd/encoder/cluster_h264_encoder.h"

struct ClusterH264EncoderBridge {
  ClusterH264EncoderConfig config;
  std::unique_ptr<ClusterH264Encoder> encoder;
  std::string last_error;
  bool valid_config = true;
};

namespace {

template <typename Action>
int checked(ClusterH264EncoderBridge *bridge, const Action &action) {
  if (bridge == nullptr) return -1;
  try {
    if (!bridge->valid_config) throw std::runtime_error(bridge->last_error);
    action();
    bridge->last_error.clear();
    return 0;
  } catch (const std::exception &e) {
    bridge->last_error = e.what();
  } catch (...) {
    bridge->last_error = "unknown cluster H264 encoder error";
  }
  return -1;
}

ClusterH264Encoder &opened(ClusterH264EncoderBridge *bridge) {
  if (bridge->encoder == nullptr) throw std::runtime_error("cluster H264 encoder is not open");
  return *bridge->encoder;
}

void configuring(ClusterH264EncoderBridge *bridge) {
  if (bridge->encoder != nullptr) throw std::runtime_error("cannot change configuration after encoder open");
}

ClusterH264PacketCallback packet_callback(cluster_h264_packet_callback callback, void *opaque) {
  return [callback, opaque](const ClusterH264PacketView &packet) {
    if (callback != nullptr) {
      callback(packet.data, packet.size, packet.flags, packet.timestamp_us,
               packet.codec_config ? 1 : 0, packet.keyframe ? 1 : 0, opaque);
    }
  };
}

}  // namespace

extern "C" {

ClusterH264EncoderBridge *cluster_h264_encoder_bridge_create(int width, int height, int fps, int bitrate, int gop,
                                                            const char *device_path, int input_format,
                                                            int unused_input_layout, int debug) {
  auto *bridge = new (std::nothrow) ClusterH264EncoderBridge();
  if (bridge == nullptr) return nullptr;
  checked(bridge, [&] {
    (void)unused_input_layout;
    if (input_format < 0 || input_format > 2) throw std::runtime_error("invalid NV12 input format");
    bridge->config.width = width;
    bridge->config.height = height;
    bridge->config.fps = fps;
    bridge->config.bitrate = bitrate;
    bridge->config.gop = gop;
    bridge->config.debug = debug != 0;
    if (device_path != nullptr && device_path[0] != '\0') bridge->config.device_path = device_path;
    bridge->config.input_format = ClusterH264InputFormat::NV12;
    // Validate before opening devices or allocating ION buffers.
    ClusterH264Encoder validated(bridge->config);
  });
  bridge->valid_config = bridge->last_error.empty();
  return bridge;
}

int cluster_h264_encoder_bridge_set_slice_max_bytes(ClusterH264EncoderBridge *bridge, int value) {
  return checked(bridge, [&] {
    configuring(bridge);
    if (value < 0) throw std::runtime_error("slice max bytes must be non-negative");
    bridge->config.slice_max_bytes = value;
  });
}

int cluster_h264_encoder_bridge_set_rate_control(ClusterH264EncoderBridge *bridge, int value) {
  return checked(bridge, [&] {
    configuring(bridge);
    if (value < 0 || value > 7) throw std::runtime_error("rate control must be between 0 and 7");
    bridge->config.rate_control = value;
  });
}

int cluster_h264_encoder_bridge_set_realtime_priority(ClusterH264EncoderBridge *bridge, int value) {
  return checked(bridge, [&] {
    configuring(bridge);
    bridge->config.realtime_priority = value != 0;
  });
}

int cluster_h264_encoder_bridge_open(ClusterH264EncoderBridge *bridge) {
  return checked(bridge, [&] {
    if (bridge->encoder != nullptr) return;
    auto encoder = std::make_unique<ClusterH264Encoder>(bridge->config);
    encoder->open();
    bridge->encoder = std::move(encoder);
  });
}

int cluster_h264_encoder_bridge_encode_nv12_packed(ClusterH264EncoderBridge *bridge, const uint8_t *data, size_t size,
                                                  uint64_t timestamp_us, cluster_h264_packet_callback callback, void *opaque) {
  return checked(bridge, [&] { opened(bridge).encode_nv12_packed(data, size, timestamp_us, packet_callback(callback, opaque)); });
}

int cluster_h264_encoder_bridge_encode_nv12_active(ClusterH264EncoderBridge *bridge, const uint8_t *data, size_t size,
                                                  uint64_t timestamp_us, cluster_h264_packet_callback callback, void *opaque) {
  return checked(bridge, [&] { opened(bridge).encode_nv12_active(data, size, timestamp_us, packet_callback(callback, opaque)); });
}

int cluster_h264_encoder_bridge_encode_nv12(ClusterH264EncoderBridge *bridge, const uint8_t *data, size_t size,
                                           uint64_t timestamp_us, cluster_h264_packet_callback callback, void *opaque) {
  return checked(bridge, [&] { opened(bridge).encode_nv12(data, size, timestamp_us, packet_callback(callback, opaque)); });
}

int cluster_h264_encoder_bridge_drain(ClusterH264EncoderBridge *bridge, int timeout_ms,
                                      cluster_h264_packet_callback callback, void *opaque) {
  return checked(bridge, [&] {
    if (timeout_ms < 0 || timeout_ms > 1000) throw std::runtime_error("drain timeout must be between 0 and 1000 ms");
    opened(bridge).drain(timeout_ms, packet_callback(callback, opaque));
  });
}

void cluster_h264_encoder_bridge_close(ClusterH264EncoderBridge *bridge) {
  if (bridge != nullptr) {
    checked(bridge, [&] { bridge->encoder.reset(); });
  }
}

void cluster_h264_encoder_bridge_destroy(ClusterH264EncoderBridge *bridge) {
  delete bridge;
}

const char *cluster_h264_encoder_bridge_last_error(ClusterH264EncoderBridge *bridge) {
  return bridge == nullptr ? "null cluster H264 bridge" : bridge->last_error.c_str();
}

const char *cluster_h264_encoder_bridge_input_format_name(ClusterH264EncoderBridge *bridge) {
  return bridge != nullptr && bridge->encoder != nullptr ? bridge->encoder->input_v4l_format_name().c_str() : "";
}

#define BRIDGE_SIZE_GETTER(name, expression) \
  size_t cluster_h264_encoder_bridge_##name(ClusterH264EncoderBridge *bridge) { \
    return bridge != nullptr && bridge->encoder != nullptr ? bridge->encoder->expression : 0; \
  }

BRIDGE_SIZE_GETTER(input_stride, input_stride())
BRIDGE_SIZE_GETTER(input_y_scanlines, input_y_scanlines())
BRIDGE_SIZE_GETTER(input_uv_scanlines, input_uv_scanlines())
BRIDGE_SIZE_GETTER(input_sizeimage, input_sizeimage())
BRIDGE_SIZE_GETTER(input_uv_offset, input_uv_offset())
BRIDGE_SIZE_GETTER(input_bytesused, input_bytesused())
BRIDGE_SIZE_GETTER(input_active_bytes, input_active_bytes())
BRIDGE_SIZE_GETTER(capture_sizeimage, capture_sizeimage())
BRIDGE_SIZE_GETTER(last_pre_poll_us, last_encode_timings().pre_poll_us)
BRIDGE_SIZE_GETTER(last_wait_input_us, last_encode_timings().wait_input_us)
BRIDGE_SIZE_GETTER(last_convert_us, last_encode_timings().convert_us)
BRIDGE_SIZE_GETTER(last_sync_us, last_encode_timings().sync_us)
BRIDGE_SIZE_GETTER(last_queue_us, last_encode_timings().queue_us)
BRIDGE_SIZE_GETTER(last_post_poll_us, last_encode_timings().post_poll_us)
BRIDGE_SIZE_GETTER(last_total_us, last_encode_timings().total_us)

#undef BRIDGE_SIZE_GETTER

}  // extern "C"
