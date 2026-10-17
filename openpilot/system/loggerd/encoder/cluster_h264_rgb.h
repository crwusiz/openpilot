#pragma once

#include <stddef.h>
#include <stdint.h>

#if defined(_WIN32)
#define CLUSTER_RGB_EXPORT __declspec(dllexport)
#else
#define CLUSTER_RGB_EXPORT
#endif

#ifdef __cplusplus
extern "C" {
#endif

// RGB24 landscape -> clockwise portrait NV12 (counterclockwise for rotate_180).
// Replicate the right/bottom portrait edges to multiples of 16. No allocation,
// hardware state, worker threads, or retained pointers. Input/output cannot
// overlap. Returns -1 without writing output if any buffer/dimension is invalid.
CLUSTER_RGB_EXPORT int cluster_h264_encoder_bridge_convert_rgb(
    const uint8_t *rgb, size_t rgb_size, int width, int height, size_t rgb_stride,
    int rotate_180, uint8_t *nv12, size_t nv12_size);

#ifdef __cplusplus
}
#endif
