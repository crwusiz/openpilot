#pragma once

#include <memory>

#include "cereal/messaging/messaging.h"
#include "msgq/visionipc/visionipc_server.h"
#include "common/queue.h"
#include "common/util.h"


const int YUV_BUFFER_COUNT = 20;

typedef struct FrameMetadata {
  uint32_t frame_id;
  uint32_t request_id;

  // Timestamps
  uint64_t timestamp_sof;
  uint64_t timestamp_eof;

  float processing_time;
} FrameMetadata;

class SpectraCamera;
class CameraState;
class ImgProc;

class CameraBuf {
private:
  VisionIpcServer *vipc_server;
  ImgProc *imgproc = nullptr;
  VisionStreamType stream_type;
  int cur_buf_idx;
  SafeQueue<int> safe_queue;
  int frame_buf_count;

public:
  FrameMetadata cur_frame_data;
  VisionBuf *cur_yuv_buf;
  VisionBuf *cur_camera_buf;
  std::unique_ptr<VisionBuf[]> camera_bufs;
  std::unique_ptr<FrameMetadata[]> camera_bufs_metadata;
  int out_img_width, out_img_height;

  CameraBuf() = default;
  ~CameraBuf();
  void init(cl_device_id device_id, cl_context context, SpectraCamera *cam, VisionIpcServer * v, int frame_cnt, VisionStreamType type);
  bool acquire(int expo_time);
  void queue(size_t buf_idx);
};

void camerad_thread();
kj::Array<uint8_t> get_raw_frame_image(const CameraBuf *b);
float set_exposure_target(const CameraBuf *b, Rect ae_xywh, int x_skip, int y_skip);
void publish_thumbnail(PubMaster *pm, const CameraBuf *b);
int open_v4l_by_name_and_index(const char name[], int index = 0, int flags = O_RDWR | O_NONBLOCK);
