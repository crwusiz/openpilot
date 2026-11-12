#pragma once

#include <atomic>
#include <cstdint>
#include <mutex>
#include <string>
#include <vector>


#define TIMEOUT 0
#define SPI_BUF_SIZE 4096


class PandaSpiHandle {
public:
  std::string hw_serial;
  std::atomic<bool> connected = true;
  std::atomic<bool> comms_healthy = true;

  PandaSpiHandle(std::string serial);
  ~PandaSpiHandle();

  int control_write(uint8_t request, uint16_t param1, uint16_t param2, unsigned int timeout=TIMEOUT);
  int control_read(uint8_t request, uint16_t param1, uint16_t param2, unsigned char *data, uint16_t length, unsigned int timeout=TIMEOUT);
  int bulk_write(unsigned char endpoint, unsigned char* data, int length, unsigned int timeout=TIMEOUT);
  int bulk_read(unsigned char endpoint, unsigned char* data, int length, unsigned int timeout=TIMEOUT);
  void cleanup();

  static std::vector<std::string> list();

private:
  int spi_fd = -1;
  uint8_t tx_buf[SPI_BUF_SIZE];
  uint8_t rx_buf[SPI_BUF_SIZE];
  inline static std::recursive_mutex hw_lock;

  struct __attribute__((packed)) spi_header {
    uint8_t endpoint;
    uint32_t id;
    uint16_t tx_len;
    uint16_t rx_len;
  };
  struct __attribute__((packed)) spi_resp_header {
    uint8_t status;
    uint32_t id;
    uint16_t len;
  };

  // Per request, owned by the calling thread; never used for transport decisions.
  struct SpiDiagnostics {
    uint64_t lock_wait_ns = 0;
    uint64_t lock_wait_max_ns = 0;
    uint64_t last_lock_wait_ns = 0;
    uint64_t transfer_ns = 0;
    uint64_t ioctl_ns = 0;
    uint64_t ioctl_max_ns = 0;
    uint32_t attempts = 0;
    uint32_t ioctls = 0;
    uint32_t ioctl_errors = 0;
    uint32_t polls = 0;
    uint32_t invalid_responses = 0;
    uint32_t stale_responses = 0;
    uint32_t expired_at_lock = 0;
  };

  int spi_transfer(uint8_t endpoint, uint32_t id, uint8_t *tx_data, uint16_t tx_len, uint8_t *rx_data, uint16_t max_rx_len,
                   double deadline, SpiDiagnostics &diagnostics);
  int spi_transfer_retry(uint8_t endpoint, uint8_t *tx_data, uint16_t tx_len, uint8_t *rx_data, uint16_t max_rx_len, unsigned int timeout);
  int lltransfer(struct spi_ioc_transfer &t);

  spi_header header;
  uint32_t xfer_count = 0;
};
