#include "cluster_h264_rgb.h"

namespace {

constexpr int kTile = 16;
constexpr int kShift = 20;
constexpr int kRound = 1 << (kShift - 1);

// BT.601 limited range, with the same 20-bit coefficients and chroma sampling
// as OpenCV's RGB -> I420 path. Keep output, including replicated padding,
// byte-identical to rotating/padding RGB before cvtColor and UV interleaving.
// Reference: opencv/modules/imgproc/src/color_yuv.simd.hpp (rgbToY/UV42x).
inline uint8_t luma(const uint8_t *pixel) {
  return static_cast<uint8_t>((269484 * pixel[0] + 528482 * pixel[1] + 102760 * pixel[2] +
                              (16 << kShift) + kRound) >> kShift);
}

inline void chroma(const uint8_t *pixel, uint8_t *uv) {
  const int r = pixel[0], g = pixel[1], b = pixel[2];
  uv[0] = static_cast<uint8_t>((-155188 * r - 305135 * g + 460324 * b + (128 << kShift) + kRound) >> kShift);
  uv[1] = static_cast<uint8_t>((460324 * r - 385875 * g - 74448 * b + (128 << kShift) + kRound) >> kShift);
}

template <bool Rotate180>
void convert(const uint8_t *rgb, int width, int height, size_t rgb_stride,
             uint8_t *nv12, int coded_width, int coded_height) {
  uint8_t *uv_plane = nv12 + static_cast<size_t>(coded_width) * coded_height;
  // A small tile keeps both the transposed RGB reads and portrait output in
  // cache. Work directly in 2x2 NV12 blocks, without full RGB/I420 scratch images.
  for (int tile_y = 0; tile_y < coded_height; tile_y += kTile) {
    for (int tile_x = 0; tile_x < coded_width; tile_x += kTile) {
      for (int y = tile_y; y < tile_y + kTile; y += 2) {
        int sx0 = y < width ? y : width - 1;
        int sx1 = y + 1 < width ? y + 1 : width - 1;
        if (Rotate180) {
          sx0 = width - 1 - sx0;
          sx1 = width - 1 - sx1;
        }
        uint8_t *y0 = nv12 + static_cast<size_t>(y) * coded_width + tile_x;
        uint8_t *y1 = y0 + coded_width;
        uint8_t *uv = uv_plane + static_cast<size_t>(y / 2) * coded_width + tile_x;
        for (int x = tile_x; x < tile_x + kTile; x += 2) {
          int sy0 = x < height ? x : height - 1;
          int sy1 = x + 1 < height ? x + 1 : height - 1;
          if (!Rotate180) {
            sy0 = height - 1 - sy0;
            sy1 = height - 1 - sy1;
          }
          const uint8_t *p00 = rgb + static_cast<size_t>(sy0) * rgb_stride + sx0 * 3;
          const uint8_t *p01 = rgb + static_cast<size_t>(sy1) * rgb_stride + sx0 * 3;
          const uint8_t *p10 = rgb + static_cast<size_t>(sy0) * rgb_stride + sx1 * 3;
          const uint8_t *p11 = rgb + static_cast<size_t>(sy1) * rgb_stride + sx1 * 3;
          y0[0] = luma(p00);
          y0[1] = luma(p01);
          y1[0] = luma(p10);
          y1[1] = luma(p11);
          // Chroma phase is defined AFTER rotation. Sampling the landscape's
          // even/even pixels first would shift color at one-pixel boundaries.
          chroma(p00, uv);
          y0 += 2;
          y1 += 2;
          uv += 2;
        }
      }
    }
  }
}

}  // namespace

extern "C" int cluster_h264_encoder_bridge_convert_rgb(
    const uint8_t *rgb, size_t rgb_size, int width, int height, size_t rgb_stride,
    int rotate_180, uint8_t *nv12, size_t nv12_size) {
  if (rgb == nullptr || nv12 == nullptr || width < 2 || width > 4096 || height < 2 || height > 4096 ||
      width % 2 != 0 || height % 2 != 0 || (rotate_180 != 0 && rotate_180 != 1)) return -1;
  const size_t row_bytes = static_cast<size_t>(width) * 3;
  // Bound row arithmetic as well as image dimensions before accessing pointers.
  if (rgb_stride < row_bytes || rgb_stride > 4096 * 4) return -1;
  const size_t input_size = static_cast<size_t>(height - 1) * rgb_stride + row_bytes;
  const int coded_width = (height + 15) / 16 * 16;
  const int coded_height = (width + 15) / 16 * 16;
  const size_t output_size = static_cast<size_t>(coded_width) * coded_height * 3 / 2;
  if (rgb_size < input_size || nv12_size < output_size) return -1;
  const uintptr_t input = reinterpret_cast<uintptr_t>(rgb), output = reinterpret_cast<uintptr_t>(nv12);
  if ((output >= input && output - input < input_size) || (input >= output && input - output < output_size)) return -1;

  if (rotate_180) {
    convert<true>(rgb, width, height, rgb_stride, nv12, coded_width, coded_height);
  } else {
    convert<false>(rgb, width, height, rgb_stride, nv12, coded_width, coded_height);
  }
  return 0;
}
