#include "selfdrive/ui/paint.h"

#include <algorithm>
#include <cassert>

#ifdef __APPLE__
#include <OpenGL/gl3.h>
#define NANOVG_GL3_IMPLEMENTATION
#define nvgCreate nvgCreateGL3
#else
#include <GLES3/gl3.h>
#define NANOVG_GLES3_IMPLEMENTATION
#define nvgCreate nvgCreateGLES3
#endif

#define NANOVG_GLES3_IMPLEMENTATION
#include <nanovg_gl.h>
#include <nanovg_gl_utils.h>

#include "selfdrive/common/timing.h"
#include "selfdrive/common/util.h"
#include "selfdrive/hardware/hw.h"

#include "selfdrive/ui/ui.h"

static void ui_draw_text(const UIState *s, float x, float y, const char *string, float size, NVGcolor color, const char *font_name) {
  nvgFontFace(s->vg, font_name);
  nvgFontSize(s->vg, size);
  nvgFillColor(s->vg, color);
  nvgText(s->vg, x, y, string, NULL);
}

static void draw_chevron(UIState *s, float x, float y, float sz, NVGcolor fillColor, NVGcolor glowColor) {
  // glow
  float g_xo = sz/5;
  float g_yo = sz/10;
  nvgBeginPath(s->vg);
  nvgMoveTo(s->vg, x+(sz*1.35)+g_xo, y+sz+g_yo);
  nvgLineTo(s->vg, x, y-g_xo);
  nvgLineTo(s->vg, x-(sz*1.35)-g_xo, y+sz+g_yo);
  nvgClosePath(s->vg);
  nvgFillColor(s->vg, glowColor);
  nvgFill(s->vg);

  // chevron
  nvgBeginPath(s->vg);
  nvgMoveTo(s->vg, x+(sz*1.25), y+sz);
  nvgLineTo(s->vg, x, y);
  nvgLineTo(s->vg, x-(sz*1.25), y+sz);
  nvgClosePath(s->vg);
  nvgFillColor(s->vg, fillColor);
  nvgFill(s->vg);
}

static void ui_draw_circle_image(const UIState *s, int center_x, int center_y, int radius, const char *image, NVGcolor color, float img_alpha) {
  nvgBeginPath(s->vg);
  nvgCircle(s->vg, center_x, center_y, radius);
  nvgFillColor(s->vg, color);
  nvgFill(s->vg);
  const int img_size = radius * 1.5;
  ui_draw_image(s, {center_x - (img_size / 2), center_y - (img_size / 2), img_size, img_size}, image, img_alpha);
}

static void ui_draw_circle_image(const UIState *s, int center_x, int center_y, int radius, const char *image, bool active) {
  float bg_alpha = active ? 0.3f : 0.1f;
  float img_alpha = active ? 1.0f : 0.15f;
  ui_draw_circle_image(s, center_x, center_y, radius, image, nvgRGBA(0, 0, 0, (255 * bg_alpha)), img_alpha);
}

static void draw_lead(UIState *s, const cereal::ModelDataV2::LeadDataV3::Reader &lead_data, const vertex_data &vd) {
  // Draw lead car indicator
  auto [x, y] = vd;

  float fillAlpha = 0;
  float speedBuff = 10.;
  float leadBuff = 40.;
  float d_rel = lead_data.getX()[0];
  float v_rel = lead_data.getV()[0];
  if (d_rel < leadBuff) {
    fillAlpha = 255*(1.0-(d_rel/leadBuff));
    if (v_rel < 0) {
      fillAlpha += 255*(-1*(v_rel/speedBuff));
    }
    fillAlpha = (int)(fmin(fillAlpha, 255));
  }

  float sz = std::clamp((25 * 30) / (d_rel / 3 + 30), 15.0f, 30.0f) * 2.35;
  x = std::clamp(x, 0.f, s->fb_w - sz / 2);
  y = std::fmin(s->fb_h - sz * .6, y);
  draw_chevron(s, x, y, sz, nvgRGBA(201, 34, 49, fillAlpha), COLOR_YELLOW);
}

static void ui_draw_line(UIState *s, const line_vertices_data &vd, NVGcolor *color, NVGpaint *paint) {
  if (vd.cnt == 0) return;

  const vertex_data *v = &vd.v[0];
  nvgBeginPath(s->vg);
  nvgMoveTo(s->vg, v[0].x, v[0].y);
  for (int i = 1; i < vd.cnt; i++) {
    nvgLineTo(s->vg, v[i].x, v[i].y);
  }
  nvgClosePath(s->vg);
  if (color) {
    nvgFillColor(s->vg, *color);
  } else if (paint) {
    nvgFillPaint(s->vg, *paint);
  }
  nvgFill(s->vg);
}

static void draw_vision_frame(UIState *s) {
  glBindVertexArray(s->frame_vao);
  mat4 *out_mat = &s->rear_frame_mat;
  glActiveTexture(GL_TEXTURE0);

  if (s->last_frame) {
    glBindTexture(GL_TEXTURE_2D, s->texture[s->last_frame->idx]->frame_tex);
    if (!Hardware::EON()) {
      // this is handled in ion on QCOM
      glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB, s->last_frame->width, s->last_frame->height,
                   0, GL_RGB, GL_UNSIGNED_BYTE, s->last_frame->addr);
    }
  }

  glUseProgram(s->gl_shader->prog);
  glUniform1i(s->gl_shader->getUniformLocation("uTexture"), 0);
  glUniformMatrix4fv(s->gl_shader->getUniformLocation("uTransform"), 1, GL_TRUE, out_mat->v);

  assert(glGetError() == GL_NO_ERROR);
  glEnableVertexAttribArray(0);
  glDrawElements(GL_TRIANGLES, 6, GL_UNSIGNED_BYTE, (const void *)0);
  glDisableVertexAttribArray(0);
  glBindVertexArray(0);
}

static void ui_draw_vision_lane_lines(UIState *s) {
  const UIScene &scene = s->scene;
  // paint lanelines
  for (int i = 0; i < std::size(scene.lane_line_vertices); i++) {
    NVGcolor color = nvgRGBAf(1.0, 1.0, 1.0, scene.lane_line_probs[i]);
    ui_draw_line(s, scene.lane_line_vertices[i], &color, nullptr);
  }

  // paint road edges
  for (int i = 0; i < std::size(scene.road_edge_vertices); i++) {
    NVGcolor color = nvgRGBAf(1.0, 0.0, 0.0, std::clamp<float>(1.0 - scene.road_edge_stds[i], 0.0, 1.0));
    ui_draw_line(s, scene.road_edge_vertices[i], &color, nullptr);
  }

  // paint path
  int steerOverride = s->scene.car_state.getSteeringPressed();
  NVGpaint track_bg;
  if (s->scene.controls_state.getEnabled()) {
  // Draw colored track
    if (steerOverride) {
      track_bg = nvgLinearGradient(s->vg, s->fb_w, s->fb_h, s->fb_w, s->fb_h*.4,
                                   COLOR_ENGAGEABLE, COLOR_ENGAGEABLE_ALPHA(120));
    } else if (scene.end_to_end) {
      track_bg = nvgLinearGradient(s->vg, s->fb_w, s->fb_h, s->fb_w, s->fb_h*.4,
                                   COLOR_RED, COLOR_RED_ALPHA(120));
    } else {
      // color track with output scale
      int torque_scale = (int)fabs(510*(float)s->scene.output_scale);
      int red_lvl = fmin(255, torque_scale);
      int green_lvl = fmin(255, 510-torque_scale);
      track_bg = nvgLinearGradient(s->vg, s->fb_w, s->fb_h, s->fb_w, s->fb_h*.4,
        nvgRGBA(          red_lvl,            green_lvl,  0, 255),
        nvgRGBA((int)(0.5*red_lvl), (int)(0.5*green_lvl), 0, 50));
    }
  } else {
    // Draw white vision track
    track_bg = nvgLinearGradient(s->vg, s->fb_w, s->fb_h, s->fb_w, s->fb_h*.4,
                                 COLOR_WHITE, COLOR_WHITE_ALPHA(120));
  }
  // paint path
  ui_draw_line(s, scene.track_vertices, nullptr, &track_bg);
}

// Draw all world space objects.
static void ui_draw_world(UIState *s) {
  nvgScissor(s->vg, 0, 0, s->fb_w, s->fb_h);

  // Draw lane edges and vision/mpc tracks
  ui_draw_vision_lane_lines(s);

  // Draw lead indicators if openpilot is handling longitudinal
  //if (s->scene.longitudinal_control) {
    auto lead_one = (*s->sm)["modelV2"].getModelV2().getLeadsV3()[0];
    auto lead_two = (*s->sm)["modelV2"].getModelV2().getLeadsV3()[1];
    if (lead_one.getProb() > .5) {
      draw_lead(s, lead_one, s->scene.lead_vertices[0]);
    }
    if (lead_two.getProb() > .5 && (std::abs(lead_one.getX()[0] - lead_two.getX()[0]) > 3.0)) {
      draw_lead(s, lead_two, s->scene.lead_vertices[1]);
    }
  //}
  nvgResetScissor(s->vg);
}

static void ui_draw_vision_maxspeed(UIState *s) {
  const int SET_SPEED_NA = 255;
  float maxspeed = (*s->sm)["controlsState"].getControlsState().getVCruise();
  const bool is_cruise_set = maxspeed != 0 && maxspeed != SET_SPEED_NA;
  if (is_cruise_set && !s->scene.is_metric) { maxspeed *= 0.6225; }

  const Rect rect = {bdr_s * 2, int(bdr_s * 1.5), 184, 202};
  ui_fill_rect(s->vg, rect, COLOR_BLACK_ALPHA(100), 30.);
  ui_draw_rect(s->vg, rect, COLOR_WHITE_ALPHA(100), 10, 20.);

  nvgTextAlign(s->vg, NVG_ALIGN_CENTER | NVG_ALIGN_BASELINE);
  ui_draw_text(s, rect.centerX(), 90, "SET", 45, COLOR_WHITE_ALPHA(is_cruise_set ? 200 : 100), "sans-regular");
  if (is_cruise_set) {
    const std::string maxspeed_str = std::to_string((int)std::nearbyint(maxspeed));
    ui_draw_text(s, rect.centerX(), 190, maxspeed_str.c_str(), 45 * 2.5, COLOR_WHITE, "sans-bold");
  } else {
    ui_draw_text(s, rect.centerX(), 190, "-", 42 * 2.5, COLOR_WHITE_ALPHA(100), "sans-semibold");
  }
}

static void ui_draw_vision_speed(UIState *s) {
  const float speed = std::max(0.0, (*s->sm)["carState"].getCarState().getVEgo() * (s->scene.is_metric ? 3.6 : 2.2369363));
  const std::string speed_str = std::to_string((int)std::nearbyint(speed));
  nvgTextAlign(s->vg, NVG_ALIGN_CENTER | NVG_ALIGN_BASELINE);
  ui_draw_text(s, s->fb_w/2, 240, speed_str.c_str(), 100 * 2.5, COLOR_WHITE, "sans-bold");
  ui_draw_text(s, s->fb_w/2, 320, s->scene.is_metric ? "㎞/h" : "mph", 36 * 2.5, COLOR_YELLOW_ALPHA(200), "sans-regular");

  // turning blinker sequential crwusiz / mod by arne-fork Togo
  const int blinker_w = 280;
  const int blinker_x = s->fb_w/2 - 140;
  const int pos_add = 50;
  bool is_warning = (s->status == STATUS_WARNING);
  
  if(s->scene.leftBlinker || s->scene.rightBlinker) {
    s->scene.blinker_blinkingrate -= 5;
    if(s->scene.blinker_blinkingrate < 0) s->scene.blinker_blinkingrate = 120;

    float progress = (120 - s->scene.blinker_blinkingrate) / 120.0;
    float offset = progress * (6.4 - 1.0) + 1.0;
    if (offset < 1.0) offset = 1.0;
    if (offset > 6.4) offset = 6.4;

    float alpha = 1.0;
    if (progress < 0.25) alpha = progress / 0.25;
    if (progress > 0.75) alpha = 1.0 - ((progress - 0.75) / 0.25);

    if(s->scene.leftBlinker) {
      nvgBeginPath(s->vg);
      nvgMoveTo(s->vg, blinker_x - (pos_add*offset)                    ,(header_h/4.2));
      nvgLineTo(s->vg, blinker_x - (pos_add*offset) - (blinker_w/2),(header_h/2.1));
      nvgLineTo(s->vg, blinker_x - (pos_add*offset)                    ,(header_h/1.4));
      nvgClosePath(s->vg);
      if (is_warning) {
        nvgFillColor(s->vg, COLOR_WARNING_ALPHA(180 * alpha));
      } else {
        nvgFillColor(s->vg, COLOR_ENGAGED_ALPHA(180 * alpha));
      }
      nvgFill(s->vg);
    }
    if(s->scene.rightBlinker) {
      nvgBeginPath(s->vg);
      nvgMoveTo(s->vg, blinker_x + (pos_add*offset) + blinker_w      ,(header_h/4.2));
      nvgLineTo(s->vg, blinker_x + (pos_add*offset) + (blinker_w*1.5),(header_h/2.1));
      nvgLineTo(s->vg, blinker_x + (pos_add*offset) + blinker_w      ,(header_h/1.4));
      nvgClosePath(s->vg);
      if (is_warning) {
        nvgFillColor(s->vg, COLOR_WARNING_ALPHA(180 * alpha));
      } else {
        nvgFillColor(s->vg, COLOR_ENGAGED_ALPHA(180 * alpha));
      }
      nvgFill(s->vg);
    }
  }
}

static void ui_draw_vision_event(UIState *s) {
  // draw steering wheel, bdr_s=10,
  float angleSteers = s->scene.car_state.getSteeringAngleDeg();
  int steerOverride = s->scene.car_state.getSteeringPressed();
  const int radius = 96;
  const int center_x = s->fb_w - radius;
  const int center_y = radius;
  const int bg_wheel_x = center_x - (bdr_s*3);
  const int bg_wheel_y = center_y + (bdr_s*3);
  const int img_wheel_size = radius + 54; // wheel_size = 150
  const int img_wheel_x = bg_wheel_x - (img_wheel_size/2);
  const int img_wheel_y = bg_wheel_y - (img_wheel_size/2);
  const float img_rotation = angleSteers/180*3.141592;
  bool is_engaged = (s->status == STATUS_ENGAGED) && ! steerOverride;
  bool is_warning = (s->status == STATUS_WARNING);
  bool is_engageable = s->scene.controls_state.getEngageable();
  if (is_engaged || is_warning || is_engageable) {
    nvgBeginPath(s->vg);
    nvgCircle(s->vg, bg_wheel_x, bg_wheel_y, radius); // circle_size = 96
    if (is_engaged) {
      nvgFillColor(s->vg, COLOR_ENGAGED_ALPHA(120));
    } else if (is_warning) {
      nvgFillColor(s->vg, COLOR_WARNING_ALPHA(120));
    } else if (is_engageable) {
      nvgFillColor(s->vg, COLOR_ENGAGEABLE_ALPHA(120));
    }
    nvgFill(s->vg);
  }
  nvgSave(s->vg);
  nvgTranslate(s->vg, bg_wheel_x, bg_wheel_y);
  nvgRotate(s->vg,-img_rotation);
  nvgBeginPath(s->vg);
  NVGpaint imgPaint = nvgImagePattern(s->vg, img_wheel_x - bg_wheel_x, img_wheel_y - bg_wheel_y, img_wheel_size, img_wheel_size, 0, s->images["wheel"], 1.0f);
  nvgRect(s->vg, img_wheel_x - bg_wheel_x, img_wheel_y - bg_wheel_y, img_wheel_size, img_wheel_size);
  nvgFillPaint(s->vg, imgPaint);
  nvgFill(s->vg);
  nvgRestore(s->vg);
}

static void ui_draw_gps(UIState *s) {
  const int radius = 60;
  const int gps_x = s->fb_w - (radius*5);
  const int gps_y = radius + 40;
  auto gps_state = (*s->sm)["liveLocationKalman"].getLiveLocationKalman();
  if (gps_state.getGpsOK()) {
    ui_draw_circle_image(s, gps_x, gps_y, radius, "gps", COLOR_BLACK_ALPHA(30), 1.0f);
  } else {
    ui_draw_circle_image(s, gps_x, gps_y, radius, "gps", COLOR_BLACK_ALPHA(10), 0.15f);
  }
}

static void ui_draw_vision_face(UIState *s) {
  const int radius = 85;
  const int center_x = radius + (bdr_s*2);
  const int center_y = s->fb_h - (footer_h/2);
  ui_draw_circle_image(s, center_x, center_y, radius, "driver_face", s->scene.dm_active);
}

static void ui_draw_brake(UIState *s) {
  const int radius = 85;
  const int brake_x = radius + (bdr_s*2) + (radius*2);
  const int brake_y = s->fb_h - (footer_h/2);
  ui_draw_circle_image(s, brake_x, brake_y, radius, "brake_disc", s->scene.car_state.getBrakeLights());
}

static void ui_draw_autohold(UIState *s) {
  int autohold = s->scene.car_state.getAutoHold();
  if (autohold < 0)
    return;
  const int radius = 85;
  const int autohold_x = (radius*2) + (bdr_s*2) + (radius*3);
  const int autohold_y = s->fb_h - (footer_h/2);
  if (autohold > 1) {
    ui_draw_circle_image(s, autohold_x, autohold_y, radius, "autohold_warning", s->scene.car_state.getAutoHold());
  } else {
    ui_draw_circle_image(s, autohold_x, autohold_y, radius, "autohold_active", s->scene.car_state.getAutoHold());
  }
}

static void ui_draw_bsd_left(UIState *s) {
  const int radius = 85;
  const int bsd_x = radius + (bdr_s*2);
  const int bsd_y = s->fb_h - (footer_h/2) - (radius*2);
  ui_draw_circle_image(s, bsd_x, bsd_y, radius, "bsd_l", s->scene.car_state.getLeftBlindspot());
}

static void ui_draw_bsd_right(UIState *s) {
  const int radius = 85;
  const int bsd_x = radius + (bdr_s*2) + (radius*2);
  const int bsd_y = s->fb_h - (footer_h/2) - (radius*2);
  ui_draw_circle_image(s, bsd_x, bsd_y, radius, "bsd_r", s->scene.car_state.getRightBlindspot());
}

static void ui_draw_vision_header(UIState *s) {
  NVGpaint gradient = nvgLinearGradient(s->vg, 0, header_h - (header_h / 2.5), 0, header_h, nvgRGBAf(0, 0, 0, 0.45), nvgRGBAf(0, 0, 0, 0));
  ui_fill_rect(s->vg, {0, 0, s->fb_w , header_h}, gradient);
  ui_draw_vision_maxspeed(s);
  ui_draw_vision_speed(s);
  ui_draw_vision_event(s);
}

static void ui_draw_tpms(UIState *s) {
  int tpms_x = s->fb_w - 270;
  int tpms_y = 875;
  int tpms_w = 240;
  int tpms_h = 160;
  char tpmsFl[32];
  char tpmsFr[32];
  char tpmsRl[32];
  char tpmsRr[32];

  // Draw Border
  const Rect rect = {tpms_x, tpms_y, tpms_w, tpms_h};
  ui_draw_rect(s->vg, rect, COLOR_WHITE_ALPHA(80), 5, 20);
  nvgTextAlign(s->vg, NVG_ALIGN_CENTER | NVG_ALIGN_BASELINE);
  const int pos_x = tpms_x + (tpms_w/2);
  const int pos_y = 985;
  const int pos_add = 50;
  const int fontsize = 60;

  ui_draw_text(s, pos_x, pos_y+pos_add, "TPMS (psi)", fontsize-20, COLOR_WHITE_ALPHA(200), "sans-regular");
  snprintf(tpmsFl, sizeof(tpmsFl), "%.0f", s->scene.tpmsFl);
  snprintf(tpmsFr, sizeof(tpmsFr), "%.0f", s->scene.tpmsFr);
  snprintf(tpmsRl, sizeof(tpmsRl), "%.0f", s->scene.tpmsRl);
  snprintf(tpmsRr, sizeof(tpmsRr), "%.0f", s->scene.tpmsRr);

  if (s->scene.tpmsFl < 34) {
    ui_draw_text(s, pos_x - pos_add, pos_y-pos_add, tpmsFl, fontsize, COLOR_RED_ALPHA(200), "sans-bold");
  } else if (s->scene.tpmsFl > 50) {
    ui_draw_text(s, pos_x - pos_add, pos_y-pos_add, "-", fontsize, COLOR_WHITE_ALPHA(200), "sans-semibold");
  } else {
    ui_draw_text(s, pos_x - pos_add, pos_y-pos_add, tpmsFl, fontsize, COLOR_WHITE_ALPHA(200), "sans-semibold");
  }
  if (s->scene.tpmsFr < 34) {
    ui_draw_text(s, pos_x + pos_add, pos_y-pos_add, tpmsFr, fontsize, COLOR_RED_ALPHA(200), "sans-bold");
  } else if (s->scene.tpmsFr > 50) {
    ui_draw_text(s, pos_x + pos_add, pos_y-pos_add, "-", fontsize, COLOR_WHITE_ALPHA(200), "sans-semibold");
  } else {
    ui_draw_text(s, pos_x + pos_add, pos_y-pos_add, tpmsFr, fontsize, COLOR_WHITE_ALPHA(200), "sans-semibold");
  }
  if (s->scene.tpmsRl < 34) {
    ui_draw_text(s, pos_x - pos_add, pos_y, tpmsRl, fontsize, COLOR_RED_ALPHA(200), "sans-bold");
  } else if (s->scene.tpmsRl > 50) {
    ui_draw_text(s, pos_x - pos_add, pos_y, "-", fontsize, COLOR_WHITE_ALPHA(200), "sans-semibold");
  } else {
    ui_draw_text(s, pos_x - pos_add, pos_y, tpmsRl, fontsize, COLOR_WHITE_ALPHA(200), "sans-semibold");
  }
  if (s->scene.tpmsRr < 34) {
    ui_draw_text(s, pos_x + pos_add, pos_y, tpmsRr, fontsize, COLOR_RED_ALPHA(200), "sans-bold");
  } else if (s->scene.tpmsRr > 50) {
    ui_draw_text(s, pos_x + pos_add, pos_y, "-", fontsize, COLOR_WHITE_ALPHA(200), "sans-semibold");
  } else {
    ui_draw_text(s, pos_x + pos_add, pos_y, tpmsRr, fontsize, COLOR_WHITE_ALPHA(200), "sans-semibold");
  }
}

//START: functions added for the display of various items

static int bb_ui_draw_measure(UIState *s, const char* value, const char* label, int bb_x, int bb_y,
                              NVGcolor valueColor, NVGcolor labelColor, int valueFontSize, int labelFontSize) {
  nvgTextAlign(s->vg, NVG_ALIGN_CENTER | NVG_ALIGN_BASELINE);

  //print value
  nvgFontFace(s->vg, "sans-semibold");
  nvgFontSize(s->vg, valueFontSize);
  nvgFillColor(s->vg, valueColor);
  nvgText(s->vg, bb_x, bb_y + (int)(valueFontSize), value, NULL);

  //print label
  nvgFontFace(s->vg, "sans-regular");
  nvgFontSize(s->vg, labelFontSize);
  nvgFillColor(s->vg, labelColor);
  nvgText(s->vg, bb_x, bb_y + (int)(valueFontSize) + (int)(labelFontSize), label, NULL);

  return (int)((valueFontSize + labelFontSize));
}

static void bb_ui_draw_measures_right(UIState *s, int bb_x, int bb_y, int bb_w) {
  const UIScene *scene = &s->scene;
  int bb_rx = bb_x + (int)(bb_w/2);
  int bb_ry = bb_y;
  int bb_h = 5;
  NVGcolor lab_color = COLOR_WHITE_ALPHA(200);
  int value_fontSize=65;
  int label_fontSize=35;

  //add CPU temperature average
  if (true) {
    char val_str[16];
    char val_add[4] = "℃";
    NVGcolor val_color = COLOR_ENGAGED;
      if((int)((scene->cpuTempAvg)) >= 70) {
        val_color = COLOR_WARNING;
      } //show Orange if more than 70℃
      if((int)((scene->cpuTempAvg)) >= 80) {
        val_color = COLOR_RED_ALPHA(200);
      } //show Red if more than 80℃
    snprintf(val_str, sizeof(val_str), "%.0f", (round((scene->cpuTempAvg))));
    strcat(val_str, val_add);
    bb_h += bb_ui_draw_measure(s, val_str, "CPU 온도", bb_rx, bb_ry, val_color, lab_color, value_fontSize, label_fontSize);
    bb_ry = bb_y + bb_h;
  }

  //add visual radar relative distance
  if (true) {
    auto radar_state = (*s->sm)["radarState"].getRadarState();
    auto lead_one = radar_state.getLeadOne();
    char val_str[16];
    char val_add[4] = "ｍ";
    NVGcolor val_color = COLOR_WHITE_ALPHA(200);
    if (lead_one.getStatus()) {
      if((int)(lead_one.getDRel()) < 15) {
        val_color = COLOR_WARNING;
      } //show Orange if less than 15ｍ
      if((int)(lead_one.getDRel()) < 5) {
        val_color = COLOR_RED_ALPHA(200);
      } //show Red if less than 5ｍ
      snprintf(val_str, sizeof(val_str), "%.0f", lead_one.getDRel());
    } else {
      snprintf(val_str, sizeof(val_str), "-");
    }
    strcat(val_str, val_add);
    bb_h += bb_ui_draw_measure(s, val_str, "거리차", bb_rx, bb_ry, val_color, lab_color, value_fontSize, label_fontSize);
    bb_ry = bb_y + bb_h;
  }

  //add visual radar relative speed
  if (true) {
    auto radar_state = (*s->sm)["radarState"].getRadarState();
    auto lead_one = radar_state.getLeadOne();
    char val_str[16];
    char val_add[4] = "㎞";
    NVGcolor val_color = COLOR_WHITE_ALPHA(200);
    if (lead_one.getStatus()) {
      if((int)(lead_one.getVRel()) < 0) {
        val_color = COLOR_WARNING;
      } //show Orange if negative speed
      if((int)(lead_one.getVRel()) < -5) {
        val_color = COLOR_RED_ALPHA(200);
      } //show Red if positive speed
      snprintf(val_str, sizeof(val_str), "%d", (int)(lead_one.getVRel() * 3.6 + 0.5));
    } else {
      snprintf(val_str, sizeof(val_str), "-");
    }
    strcat(val_str, val_add);
    bb_h +=bb_ui_draw_measure(s, val_str, "속도차", bb_rx, bb_ry, val_color, lab_color, value_fontSize, label_fontSize);
    bb_ry = bb_y + bb_h;
  }

  //add steering angle degree
  if (true) {
    float angleSteers = s->scene.car_state.getSteeringAngleDeg();
    char val_str[16];
    char val_add[4] = "˚";
    NVGcolor val_color = COLOR_ENGAGED;
    if(((int)(angleSteers) < -30) || ((int)(angleSteers) > 30)) {
      val_color = COLOR_WARNING;
    } //show Orange if more than 30 degree
    if(((int)(angleSteers) < -90) || ((int)(angleSteers) > 90)) {
      val_color = COLOR_RED_ALPHA(200);
    } //show Red if more than 90 degree
    snprintf(val_str, sizeof(val_str), "%.1f",(angleSteers));
    strcat(val_str, val_add);
    bb_h += bb_ui_draw_measure(s, val_str, "핸들 조향각", bb_rx, bb_ry, val_color, lab_color, value_fontSize, label_fontSize);
    bb_ry = bb_y + bb_h;
  }

  //add desired steering angle degree
  if (true) {
    auto carControl = (*s->sm)["carControl"].getCarControl();
    auto actuators = carControl.getActuators();
    float steeringAngleDeg  = actuators.getSteeringAngleDeg();
    char val_str[16];
    char val_add[4] = "˚";
    NVGcolor val_color = COLOR_ENGAGED;
    if (scene->controls_state.getEnabled()) {
      if(((int)(steeringAngleDeg) < -30) || ((int)(steeringAngleDeg) > 30)) {
        val_color = COLOR_WARNING;
      } //show Orange if more than 30 degree
      if(((int)(steeringAngleDeg) < -90) || ((int)(steeringAngleDeg) > 90)) {
        val_color = COLOR_RED_ALPHA(200);
      } //show Red if more than 90 degree
      snprintf(val_str, sizeof(val_str), "%.1f",(steeringAngleDeg));
    } else {
      snprintf(val_str, sizeof(val_str), "-");
    }
    strcat(val_str, val_add);
    bb_h += bb_ui_draw_measure(s, val_str, "OP 조향각", bb_rx, bb_ry, val_color, lab_color, value_fontSize, label_fontSize);
    bb_ry = bb_y + bb_h;
  }

  //add LateralControl
  if (true) {
    char val_str[16];
    NVGcolor val_color = COLOR_WHITE_ALPHA(200);
    if (s->scene.lateralControlSelect == 0) {
      snprintf(val_str, sizeof(val_str), "PID");
    } else if (s->scene.lateralControlSelect == 1) {
      snprintf(val_str, sizeof(val_str), "INDI");
    } else if (s->scene.lateralControlSelect == 2) {
      snprintf(val_str, sizeof(val_str), "LQR");
    }
    bb_h += bb_ui_draw_measure(s, val_str, "조향로직", bb_rx, bb_ry, val_color, lab_color, value_fontSize, label_fontSize);
    bb_ry = bb_y + bb_h;
  }

  //finally draw the frame
  bb_h += 20;
  nvgBeginPath(s->vg);
  nvgRoundedRect(s->vg, bb_x, bb_y, bb_w, bb_h, 20);
  nvgStrokeColor(s->vg, COLOR_WHITE_ALPHA(80));
  nvgStrokeWidth(s->vg, 6);
  nvgStroke(s->vg);
}

static void bb_ui_draw_UI(UIState *s) {
  const int bb_dmr_w = 180;
  const int bb_dmr_x = s->fb_w - bb_dmr_w - (bdr_s*4.5);
  const int bb_dmr_y = (bdr_s*4.5) + 190;
  bb_ui_draw_measures_right(s, bb_dmr_x, bb_dmr_y, bb_dmr_w);
}

//END: functions added for the display of various items

static void ui_draw_vision(UIState *s) {
  const UIScene *scene = &s->scene;
  // Draw augmented elements
  if (scene->world_objects_visible) {
    ui_draw_world(s);
  }
  // Set Speed, Current Speed, Status/Events
  ui_draw_vision_header(s);
  if ((*s->sm)["controlsState"].getControlsState().getAlertSize() == cereal::ControlsState::AlertSize::NONE) {
    ui_draw_vision_face(s);
    ui_draw_brake(s);
    ui_draw_autohold(s);
    ui_draw_bsd_left(s);
    ui_draw_bsd_right(s);
    ui_draw_gps(s);
    if (s->scene.ui_tpms){
      ui_draw_tpms(s);
    }
    bb_ui_draw_UI(s);
  }
}

void ui_draw(UIState *s, int w, int h) {
  const bool draw_vision = s->scene.started && s->vipc_client->connected;

  glViewport(0, 0, s->fb_w, s->fb_h);
  if (draw_vision) {
    draw_vision_frame(s);
  }
  glEnable(GL_BLEND);
  glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);
  // NVG drawing functions - should be no GL inside NVG frame
  nvgBeginFrame(s->vg, s->fb_w, s->fb_h, 1.0f);
  if (draw_vision) {
    ui_draw_vision(s);
  }
  nvgEndFrame(s->vg);
  glDisable(GL_BLEND);
}

void ui_draw_image(const UIState *s, const Rect &r, const char *name, float alpha) {
  nvgBeginPath(s->vg);
  NVGpaint imgPaint = nvgImagePattern(s->vg, r.x, r.y, r.w, r.h, 0, s->images.at(name), alpha);
  nvgRect(s->vg, r.x, r.y, r.w, r.h);
  nvgFillPaint(s->vg, imgPaint);
  nvgFill(s->vg);
}

void ui_draw_rect(NVGcontext *vg, const Rect &r, NVGcolor color, int width, float radius) {
  nvgBeginPath(vg);
  radius > 0 ? nvgRoundedRect(vg, r.x, r.y, r.w, r.h, radius) : nvgRect(vg, r.x, r.y, r.w, r.h);
  nvgStrokeColor(vg, color);
  nvgStrokeWidth(vg, width);
  nvgStroke(vg);
}

static inline void fill_rect(NVGcontext *vg, const Rect &r, const NVGcolor *color, const NVGpaint *paint, float radius) {
  nvgBeginPath(vg);
  radius > 0 ? nvgRoundedRect(vg, r.x, r.y, r.w, r.h, radius) : nvgRect(vg, r.x, r.y, r.w, r.h);
  if (color) nvgFillColor(vg, *color);
  if (paint) nvgFillPaint(vg, *paint);
  nvgFill(vg);
}
void ui_fill_rect(NVGcontext *vg, const Rect &r, const NVGcolor &color, float radius) {
  fill_rect(vg, r, &color, nullptr, radius);
}
void ui_fill_rect(NVGcontext *vg, const Rect &r, const NVGpaint &paint, float radius) {
  fill_rect(vg, r, nullptr, &paint, radius);
}

static const char frame_vertex_shader[] =
#ifdef NANOVG_GL3_IMPLEMENTATION
  "#version 150 core\n"
#else
  "#version 300 es\n"
#endif
  "in vec4 aPosition;\n"
  "in vec4 aTexCoord;\n"
  "uniform mat4 uTransform;\n"
  "out vec4 vTexCoord;\n"
  "void main() {\n"
  "  gl_Position = uTransform * aPosition;\n"
  "  vTexCoord = aTexCoord;\n"
  "}\n";

static const char frame_fragment_shader[] =
#ifdef NANOVG_GL3_IMPLEMENTATION
  "#version 150 core\n"
#else
  "#version 300 es\n"
#endif
  "precision mediump float;\n"
  "uniform sampler2D uTexture;\n"
  "in vec4 vTexCoord;\n"
  "out vec4 colorOut;\n"
  "void main() {\n"
  "  colorOut = texture(uTexture, vTexCoord.xy);\n"
#ifdef QCOM
  "  vec3 dz = vec3(0.0627f, 0.0627f, 0.0627f);\n"
  "  colorOut.rgb = ((vec3(1.0f, 1.0f, 1.0f) - dz) * colorOut.rgb / vec3(1.0f, 1.0f, 1.0f)) + dz;\n"
#endif
  "}\n";

static const mat4 device_transform = {{
  1.0,  0.0, 0.0, 0.0,
  0.0,  1.0, 0.0, 0.0,
  0.0,  0.0, 1.0, 0.0,
  0.0,  0.0, 0.0, 1.0,
}};

void ui_nvg_init(UIState *s) {
  // init drawing

  // on EON, we enable MSAA
  s->vg = Hardware::EON() ? nvgCreate(0) : nvgCreate(NVG_ANTIALIAS | NVG_STENCIL_STROKES | NVG_DEBUG);
  assert(s->vg);

  // init fonts
  std::pair<const char *, const char *> fonts[] = {
      {"sans-regular", "../assets/fonts/opensans_regular.ttf"},
      {"sans-semibold", "../assets/fonts/opensans_semibold.ttf"},
      {"sans-bold", "../assets/fonts/opensans_bold.ttf"},
  };
  for (auto [name, file] : fonts) {
    int font_id = nvgCreateFont(s->vg, name, file);
    assert(font_id >= 0);
  }

  // init images
  std::vector<std::pair<const char *, const char *>> images = {
    {"wheel", "../assets/img_chffr_wheel.png"},
    {"driver_face", "../assets/img_driver_face.png"},
    {"brake_disc", "../assets/img_brake_disc.png"},
    {"bsd_l", "../assets/img_bsd_l.png"},
    {"bsd_r", "../assets/img_bsd_r.png"},
    {"gps", "../assets/img_gps.png"},
    {"autohold_warning", "../assets/img_autohold_warning.png"},
    {"autohold_active", "../assets/img_autohold_active.png"},
  };
  for (auto [name, file] : images) {
    s->images[name] = nvgCreateImage(s->vg, file, 1);
    assert(s->images[name] != 0);
  }

  // init gl
  s->gl_shader = std::make_unique<GLShader>(frame_vertex_shader, frame_fragment_shader);
  GLint frame_pos_loc = glGetAttribLocation(s->gl_shader->prog, "aPosition");
  GLint frame_texcoord_loc = glGetAttribLocation(s->gl_shader->prog, "aTexCoord");

  glViewport(0, 0, s->fb_w, s->fb_h);

  glDisable(GL_DEPTH_TEST);

  assert(glGetError() == GL_NO_ERROR);

  float x1 = 1.0, x2 = 0.0, y1 = 1.0, y2 = 0.0;
  const uint8_t frame_indicies[] = {0, 1, 2, 0, 2, 3};
  const float frame_coords[4][4] = {
    {-1.0, -1.0, x2, y1}, //bl
    {-1.0,  1.0, x2, y2}, //tl
    { 1.0,  1.0, x1, y2}, //tr
    { 1.0, -1.0, x1, y1}, //br
  };

  glGenVertexArrays(1, &s->frame_vao);
  glBindVertexArray(s->frame_vao);
  glGenBuffers(1, &s->frame_vbo);
  glBindBuffer(GL_ARRAY_BUFFER, s->frame_vbo);
  glBufferData(GL_ARRAY_BUFFER, sizeof(frame_coords), frame_coords, GL_STATIC_DRAW);
  glEnableVertexAttribArray(frame_pos_loc);
  glVertexAttribPointer(frame_pos_loc, 2, GL_FLOAT, GL_FALSE,
                        sizeof(frame_coords[0]), (const void *)0);
  glEnableVertexAttribArray(frame_texcoord_loc);
  glVertexAttribPointer(frame_texcoord_loc, 2, GL_FLOAT, GL_FALSE,
                        sizeof(frame_coords[0]), (const void *)(sizeof(float) * 2));
  glGenBuffers(1, &s->frame_ibo);
  glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, s->frame_ibo);
  glBufferData(GL_ELEMENT_ARRAY_BUFFER, sizeof(frame_indicies), frame_indicies, GL_STATIC_DRAW);
  glBindBuffer(GL_ARRAY_BUFFER, 0);
  glBindVertexArray(0);

  ui_resize(s, s->fb_w, s->fb_h);
}

void ui_resize(UIState *s, int width, int height) {
  s->fb_w = width;
  s->fb_h = height;

  auto intrinsic_matrix = s->wide_camera ? ecam_intrinsic_matrix : fcam_intrinsic_matrix;

  float zoom = ZOOM / intrinsic_matrix.v[0];

  if (s->wide_camera) {
    zoom *= 0.5;
  }

  float zx = zoom * 2 * intrinsic_matrix.v[2] / width;
  float zy = zoom * 2 * intrinsic_matrix.v[5] / height;

  const mat4 frame_transform = {{
    zx, 0.0, 0.0, 0.0,
    0.0, zy, 0.0, -y_offset / height * 2,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
  }};

  s->rear_frame_mat = matmul(device_transform, frame_transform);

  // Apply transformation such that video pixel coordinates match video
  // 1) Put (0, 0) in the middle of the video
  nvgTranslate(s->vg, width / 2, height / 2 + y_offset);
  // 2) Apply same scaling as video
  nvgScale(s->vg, zoom, zoom);
  // 3) Put (0, 0) in top left corner of video
  nvgTranslate(s->vg, -intrinsic_matrix.v[2], -intrinsic_matrix.v[5]);

  nvgCurrentTransform(s->vg, s->car_space_transform);
  nvgResetTransform(s->vg);
}
