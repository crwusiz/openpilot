#pragma once

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct ClusterH264EncoderBridge ClusterH264EncoderBridge;
typedef void (*cluster_h264_packet_callback)(const uint8_t *data, size_t size, uint32_t flags,
                                             uint64_t timestamp_us, int codec_config, int keyframe, void *opaque);

// One worker owns a handle. None of these calls, including close/destroy, may
// overlap. Packet callbacks run synchronously and their data expires on return;
// copy output before returning and perform potentially slow USB writes later.
// encode queues input, it does not promise an output packet for that same call.
// Poll drain until the submitted timestamp arrives, with a caller-side deadline.
ClusterH264EncoderBridge *cluster_h264_encoder_bridge_create(int width, int height, int fps, int bitrate, int gop,
                                                            const char *device_path, int input_format,
                                                            int unused_input_layout, int debug);
int cluster_h264_encoder_bridge_set_slice_max_bytes(ClusterH264EncoderBridge *bridge, int value);
int cluster_h264_encoder_bridge_set_rate_control(ClusterH264EncoderBridge *bridge, int value);
int cluster_h264_encoder_bridge_set_realtime_priority(ClusterH264EncoderBridge *bridge, int value);
int cluster_h264_encoder_bridge_open(ClusterH264EncoderBridge *bridge);

// packed: contiguous Y[width*height] followed by UV[width*height/2].
// active: Venus-aligned Y+UV, using the layout getters below.
// nv12: the complete Venus allocation, including padding/extra storage.
// Input is copied during the call; no caller-owned pointer is retained.
int cluster_h264_encoder_bridge_encode_nv12_packed(ClusterH264EncoderBridge *bridge, const uint8_t *data, size_t size,
                                                  uint64_t timestamp_us, cluster_h264_packet_callback callback, void *opaque);
int cluster_h264_encoder_bridge_encode_nv12_active(ClusterH264EncoderBridge *bridge, const uint8_t *data, size_t size,
                                                  uint64_t timestamp_us, cluster_h264_packet_callback callback, void *opaque);
int cluster_h264_encoder_bridge_encode_nv12(ClusterH264EncoderBridge *bridge, const uint8_t *data, size_t size,
                                           uint64_t timestamp_us, cluster_h264_packet_callback callback, void *opaque);
int cluster_h264_encoder_bridge_drain(ClusterH264EncoderBridge *bridge, int timeout_ms,
                                      cluster_h264_packet_callback callback, void *opaque);
void cluster_h264_encoder_bridge_close(ClusterH264EncoderBridge *bridge);
void cluster_h264_encoder_bridge_destroy(ClusterH264EncoderBridge *bridge);
const char *cluster_h264_encoder_bridge_last_error(ClusterH264EncoderBridge *bridge);
const char *cluster_h264_encoder_bridge_input_format_name(ClusterH264EncoderBridge *bridge);
size_t cluster_h264_encoder_bridge_input_stride(ClusterH264EncoderBridge *bridge);
size_t cluster_h264_encoder_bridge_input_y_scanlines(ClusterH264EncoderBridge *bridge);
size_t cluster_h264_encoder_bridge_input_uv_scanlines(ClusterH264EncoderBridge *bridge);
size_t cluster_h264_encoder_bridge_input_sizeimage(ClusterH264EncoderBridge *bridge);
size_t cluster_h264_encoder_bridge_input_uv_offset(ClusterH264EncoderBridge *bridge);
size_t cluster_h264_encoder_bridge_input_bytesused(ClusterH264EncoderBridge *bridge);
size_t cluster_h264_encoder_bridge_input_active_bytes(ClusterH264EncoderBridge *bridge);
size_t cluster_h264_encoder_bridge_capture_sizeimage(ClusterH264EncoderBridge *bridge);
size_t cluster_h264_encoder_bridge_last_pre_poll_us(ClusterH264EncoderBridge *bridge);
size_t cluster_h264_encoder_bridge_last_wait_input_us(ClusterH264EncoderBridge *bridge);
size_t cluster_h264_encoder_bridge_last_convert_us(ClusterH264EncoderBridge *bridge);
size_t cluster_h264_encoder_bridge_last_sync_us(ClusterH264EncoderBridge *bridge);
size_t cluster_h264_encoder_bridge_last_queue_us(ClusterH264EncoderBridge *bridge);
size_t cluster_h264_encoder_bridge_last_post_poll_us(ClusterH264EncoderBridge *bridge);
size_t cluster_h264_encoder_bridge_last_total_us(ClusterH264EncoderBridge *bridge);

#ifdef __cplusplus
}  // extern "C"
#endif
