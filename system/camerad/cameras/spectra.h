#pragma once

#include <functional>
#include <memory>
#include <optional>
#include <utility>

#include "media/cam_req_mgr.h"

#include "common/util.h"
#include "system/camerad/cameras/tici.h"
#include "system/camerad/cameras/camera_common.h"
#include "system/camerad/sensors/sensor.h"

#define FRAME_BUF_COUNT 4

const int MIPI_SETTLE_CNT = 33;  // Calculated by camera_freqs.py

// For use with the Titan 170 ISP in the SDM845
// https://github.com/commaai/agnos-kernel-sdm845


// CSLDeviceType/CSLPacketOpcodesIFE from camx
// cam_packet_header.op_code = (device << 24) | (opcode);
#define CSLDeviceTypeImageSensor (0x1 << 24)
#define CSLDeviceTypeIFE         (0xF << 24)
#define OpcodesIFEInitialConfig  0x0
#define OpcodesIFEUpdate         0x1

std::optional<int32_t> device_acquire(int fd, int32_t session_handle, void *data, uint32_t num_resources=1);
int device_config(int fd, int32_t session_handle, int32_t dev_handle, uint64_t packet_handle);
int device_control(int fd, int op_code, int session_handle, int dev_handle);
int do_cam_control(int fd, int op_code, void *handle, int size);
void *alloc_w_mmu_hdl(int video0_fd, int len, uint32_t *handle, int align = 8, int flags = CAM_MEM_FLAG_KMD_ACCESS | CAM_MEM_FLAG_UMD_ACCESS | CAM_MEM_FLAG_CMD_BUF_TYPE,
                      int mmu_hdl = 0, int mmu_hdl2 = 0);
void release(int video0_fd, uint32_t handle);

class MemoryManager {
public:
  void init(int _video0_fd) { video0_fd = _video0_fd; }
  ~MemoryManager();

  template <class T>
  auto alloc(int len, uint32_t *handle) {
    return std::unique_ptr<T, std::function<void(void *)>>((T*)alloc_buf(len, handle), [this](void *ptr) { this->free(ptr); });
  }

private:
  void *alloc_buf(int len, uint32_t *handle);
  void free(void *ptr);

  std::mutex lock;
  std::map<void *, uint32_t> handle_lookup;
  std::map<void *, int> size_lookup;
  std::map<int, std::queue<void *> > cached_allocations;
  int video0_fd;
};

class SpectraMaster {
public:
  void init();

  unique_fd video0_fd;
  unique_fd cam_sync_fd;
  unique_fd isp_fd;
  int device_iommu = -1;
  int cdm_iommu = -1;
};

class SpectraCamera {
public:
  SpectraCamera(SpectraMaster *master, const CameraConfig &config);
  ~SpectraCamera();

  void camera_open(VisionIpcServer *v, cl_device_id device_id, cl_context ctx);
  void handle_camera_event(const cam_req_mgr_message *event_data);
  void camera_close();
  void camera_map_bufs();
  void config_ife(int io_mem_handle, int fence, int request_id, int buf0_idx);

  int clear_req_queue();
  void enqueue_buffer(int i, bool dp);
  void enqueue_req_multi(uint64_t start, int n, bool dp);

  int sensors_init();
  void sensors_start();
  void sensors_poke(int request_id);
  void sensors_i2c(const struct i2c_random_wr_payload* dat, int len, int op_code, bool data_word);

  bool openSensor();
  void configISP();
  void configCSIPHY();
  void linkDevices();

  // *** state ***

  bool open = false;
  bool enabled = true;
  CameraConfig cc;
  std::unique_ptr<const SensorInfo> sensor;

  unique_fd sensor_fd;
  unique_fd csiphy_fd;

  int32_t session_handle = -1;
  int32_t sensor_dev_handle = -1;
  int32_t isp_dev_handle = -1;
  int32_t csiphy_dev_handle = -1;

  int32_t link_handle = -1;

  const int buf0_size = 65624; // unclear what this is and how it's determined, for internal ISP use? it's just copied from an ioctl dump
  const int buf0_alignment = 0x20;

  int buf0_handle = 0;
  int buf_handle[FRAME_BUF_COUNT] = {};
  int sync_objs[FRAME_BUF_COUNT] = {};
  uint64_t request_ids[FRAME_BUF_COUNT] = {};
  uint64_t request_id_last = 0;
  uint64_t frame_id_last = 0;
  uint64_t idx_offset = 0;
  bool skipped = true;

  CameraBuf buf;
  MemoryManager mm;
  SpectraMaster *m;
};
