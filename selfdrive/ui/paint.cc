#include "selfdrive/ui/paint.h"

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

#include "selfdrive/common/util.h"
#include "selfdrive/hardware/hw.h"
#include "selfdrive/ui/ui.h"
#include "selfdrive/ui/extras.h"


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
  ui_draw_circle_image(s, center_x, center_y, radius, image, COLOR_BLACK_ALPHA(255 * bg_alpha), img_alpha);
}

static void draw_lead(UIState *s, const cereal::RadarState::LeadData::Reader &lead_data, const vertex_data &vd) {
  // Draw lead car indicator
  auto [x, y] = vd;

  float fillAlpha = 0;
  float speedBuff = 10.;
  float leadBuff = 40.;
  float d_rel = lead_data.getDRel();
  float v_rel = lead_data.getVRel();
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
  draw_chevron(s, x, y, sz, COLOR_RED_ALPHA(fillAlpha), COLOR_YELLOW);
}

static float lock_on_rotation[] = {0.f, 0.2f*NVG_PI, 0.4f*NVG_PI, 0.6f*NVG_PI, 0.7f*NVG_PI, 0.5f*NVG_PI, 0.4f*NVG_PI, 0.3f*NVG_PI, 0.15f*NVG_PI};

static float lock_on_scale[] = {1.f, 1.1f, 1.2f, 1.1f, 1.f, 0.9f, 0.8f, 0.9f};

static void draw_lead_custom(UIState *s, const cereal::RadarState::LeadData::Reader &lead_data, const vertex_data &vd) {
    auto [x, y] = vd;
    float d_rel = lead_data.getDRel();
    auto intrinsic_matrix = s->wide_camera ? ecam_intrinsic_matrix : fcam_intrinsic_matrix;
    float zoom = ZOOM / intrinsic_matrix.v[0];
    float sz = std::clamp((25 * 30) / (d_rel / 3 + 30), 15.0f, 30.0f) * zoom;
    x = std::clamp(x, 0.f, s->fb_w - sz / 2);

    if (d_rel < 30) {
      const float c = 0.7f;
      float r = d_rel * ((1.f - c) / 30.f) + c;
      if (r > 0.f)
        y = y * r;
    }

    y = std::fmin(s->fb_h - sz * .6, y);
    y = std::fmin(s->fb_h * 0.8f, y);

    float bg_alpha = 1.0f;
    float img_alpha = 1.0f;
    NVGcolor bg_color = COLOR_BLACK_ALPHA(255 * bg_alpha);

    const char* image = lead_data.getRadar() ? "custom_lead_radar" : "custom_lead_vision";

    if (s->sm->frame % 2 == 0) {
        s->lock_on_anim_index++;
    }

    int img_size = 80;
    if (d_rel < 100) {
        img_size = (int)(-2/5 * d_rel + 120);
    }

    nvgSave(s->vg);
    nvgTranslate(s->vg, x, y);
    //nvgRotate(s->vg, lock_on_rotation[s->lock_on_anim_index % 9]);
    float scale = lock_on_scale[s->lock_on_anim_index % 8];
    nvgScale(s->vg, scale, scale);
    ui_draw_image(s, {-(img_size / 2), -(img_size / 2), img_size, img_size}, image, img_alpha);
    nvgRestore(s->vg);
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
  int steerOverride = scene.car_state.getSteeringPressed();
  NVGpaint track_bg;
  if (scene.controls_state.getEnabled()) {
  // Draw colored track
    if (steerOverride) {
      track_bg = nvgLinearGradient(s->vg, s->fb_w, s->fb_h, s->fb_w, s->fb_h*.4,
                                   COLOR_ENGAGEABLE, COLOR_ENGAGEABLE_ALPHA(120));
    } else if (scene.end_to_end) {
      track_bg = nvgLinearGradient(s->vg, s->fb_w, s->fb_h, s->fb_w, s->fb_h*.4,
                                   COLOR_RED, COLOR_RED_ALPHA(120));
    } else {
      // color track with output scale
      int torque_scale = (int)fabs(510*(float)scene.output_scale);
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
    auto lead_one = (*s->sm)["radarState"].getRadarState().getLeadOne();
    auto lead_two = (*s->sm)["radarState"].getRadarState().getLeadTwo();
    if (lead_one.getStatus()) {
      draw_lead(s, lead_one, s->scene.lead_vertices[0]);
    }
    if (lead_two.getStatus() && (std::abs(lead_one.getDRel() - lead_two.getDRel()) > 3.0)) {
      draw_lead(s, lead_two, s->scene.lead_vertices[1]);
    }

    auto radar_state = (*s->sm)["radarState"].getRadarState();
    auto lead_radar = radar_state.getLeadOne();

    if (lead_radar.getStatus() && lead_radar.getRadar()) {
       draw_lead_custom(s, lead_radar, s->scene.lead_vertices_radar[0]);
    }
  //}
  nvgResetScissor(s->vg);
}

static void ui_draw_bottom_info(UIState *s) {
    const UIScene *scene = &s->scene;
    char str[1024];
    auto controls_state = (*s->sm)["controlsState"].getControlsState();
    auto car_params = (*s->sm)["carParams"].getCarParams();
    auto car_state = (*s->sm)["carState"].getCarState();

    int longControlState = (int)controls_state.getLongControlState();
    const char* long_state[] = {"Off", "Pid", "Stopping", "Starting"};
    int lateralControlState = controls_state.getLateralControlSelect();
    const char* lateral_state[] = {"Pid", "Indi", "Lqr"};
    auto gps_ext = s->scene.gps_ext;
    float verticalAccuracy = gps_ext.getVerticalAccuracy();
    float gpsAltitude = gps_ext.getAltitude();
    float gpsAccuracy = gps_ext.getAccuracy();
    int gpsSatelliteCount = s->scene.satelliteCount;

    if(verticalAccuracy == 0 || verticalAccuracy > 100)
        gpsAltitude = 99.99;

    if (gpsAccuracy > 100)
      gpsAccuracy = 99.99;
    else if (gpsAccuracy == 0)
      gpsAccuracy = 99.8;

    snprintf(str, sizeof(str),
    "[ %s ] SR[%.2f] MDPS[%d] SCC[%d] LongControl[ %s ] GPS[ Alt(%.1f) Acc(%.1f) Sat(%d) ]",
    lateral_state[lateralControlState],
    controls_state.getSteerRatio(),
    car_params.getMdpsBus(), car_params.getSccBus(),
    long_state[longControlState],
    gpsAltitude,
    gpsAccuracy,
    gpsSatelliteCount
    );

    int x = bdr_s * 2;
    int y = s->fb_h - 24;

    nvgTextAlign(s->vg, NVG_ALIGN_LEFT | NVG_ALIGN_MIDDLE);
    ui_draw_text(s, x, y, str, 40, COLOR_WHITE_ALPHA(200), "sans-bold");
}

static void ui_draw_vision_maxspeed(UIState *s) {
  // scc smoother
  cereal::CarControl::SccSmoother::Reader scc_smoother = s->scene.car_control.getSccSmoother();
  bool longControl = scc_smoother.getLongControl();
  // kph
  float applyMaxSpeed = scc_smoother.getApplyMaxSpeed();
  float cruiseMaxSpeed = scc_smoother.getCruiseMaxSpeed();
  bool is_cruise_set = (cruiseMaxSpeed > 0 && cruiseMaxSpeed < 255);

  const Rect rect = {bdr_s * 2, int(bdr_s * 1.5), 184, 202};
  ui_fill_rect(s->vg, rect, COLOR_BLACK_ALPHA(100), 30.);
  ui_draw_rect(s->vg, rect, COLOR_WHITE_ALPHA(100), 10, 20.);

  nvgTextAlign(s->vg, NVG_ALIGN_CENTER | NVG_ALIGN_BASELINE);
  const int text_x = rect.centerX();

  if (is_cruise_set) {
    char str[256];
    if (s->scene.is_metric)
        snprintf(str, sizeof(str), "%d", (int)(applyMaxSpeed + 0.5));
    else
        snprintf(str, sizeof(str), "%d", (int)(applyMaxSpeed*KM_TO_MILE + 0.5));
    ui_draw_text(s, text_x, 100, str, 30 * 2.5, COLOR_YELLOW, "sans-semibold");

    if (s->scene.is_metric)
        snprintf(str, sizeof(str), "%d", (int)(cruiseMaxSpeed + 0.5));
    else
        snprintf(str, sizeof(str), "%d", (int)(cruiseMaxSpeed*KM_TO_MILE + 0.5));
    ui_draw_text(s, text_x, 195, str, 45 * 2.5, COLOR_WHITE, "sans-bold");
  } else {
    if (longControl)
        ui_draw_text(s, text_x, 100, "OP", 25 * 2.5, COLOR_YELLOW_ALPHA(100), "sans-semibold");
    else
        ui_draw_text(s, text_x, 100, "SET", 25 * 2.5, COLOR_YELLOW_ALPHA(100), "sans-semibold");
    ui_draw_text(s, text_x, 195, "-", 45 * 2.5, COLOR_WHITE_ALPHA(100), "sans-semibold");
  }
}

static void ui_draw_vision_speed(UIState *s) {
  const float speed = std::max(0.0, (*s->sm)["carState"].getCarState().getVEgo() * (s->scene.is_metric ?  MS_TO_KPH : MS_TO_MPH));
  const std::string speed_str = std::to_string((int)std::nearbyint(speed));
  nvgTextAlign(s->vg, NVG_ALIGN_CENTER | NVG_ALIGN_BASELINE);

  if (s->fb_w > 1500) {
    ui_draw_text(s, s->fb_w/2, 220, speed_str.c_str(), 200, COLOR_WHITE, "sans-bold");
    ui_draw_text(s, s->fb_w/2, 300, s->scene.is_metric ? "㎞/h" : "mph", 36 * 2.5, COLOR_YELLOW_ALPHA(200), "sans-regular");
  } else {
    ui_draw_text(s, s->fb_w/2, 180, speed_str.c_str(), 150, COLOR_WHITE, "sans-bold");
    ui_draw_text(s, s->fb_w/2, 230, s->scene.is_metric ? "㎞/h" : "mph", 25 * 2.5, COLOR_YELLOW_ALPHA(200), "sans-regular");
  }

  // turning blinker sequential crwusiz / mod by arne-fork Togo
  const int blinker_w = 250;
  const int blinker_x = s->fb_w/2 - blinker_w/2;
  const int pos_add = 50;
  bool is_warning = (s->status == STATUS_WARNING);

  if (s->scene.leftBlinker || s->scene.rightBlinker) {
    s->scene.blinkingrate -= 5;
    if (s->scene.blinkingrate < 0) s->scene.blinkingrate = 120;

    float progress = (120 - s->scene.blinkingrate) / 120.0;
    float offset = progress * (6.4 - 1.0) + 1.0;
    if (offset < 1.0) offset = 1.0;
    if (offset > 6.4) offset = 6.4;

    float alpha = 1.0;
    if (progress < 0.25) alpha = progress / 0.25;
    if (progress > 0.75) alpha = 1.0 - ((progress - 0.75) / 0.25);

    if (s->scene.leftBlinker) {
      nvgBeginPath(s->vg);
      nvgMoveTo(s->vg, blinker_x - (pos_add*offset)              , 100);
      nvgLineTo(s->vg, blinker_x - (pos_add*offset) - blinker_w/4, 100);
      nvgLineTo(s->vg, blinker_x - (pos_add*offset) - blinker_w/2, 200);
      nvgLineTo(s->vg, blinker_x - (pos_add*offset) - blinker_w/4, 300);
      nvgLineTo(s->vg, blinker_x - (pos_add*offset)              , 300);
      nvgLineTo(s->vg, blinker_x - (pos_add*offset) - blinker_w/4, 200);
      nvgClosePath(s->vg);
      if (is_warning) {
        nvgFillColor(s->vg, COLOR_WARNING_ALPHA(200 * alpha));
      } else {
        nvgFillColor(s->vg, COLOR_ENGAGED_ALPHA(200 * alpha));
      }
      nvgFill(s->vg);
    }
    if (s->scene.rightBlinker) {
      nvgBeginPath(s->vg);
      nvgMoveTo(s->vg, blinker_x + (pos_add*offset)               + blinker_w, 100);
      nvgLineTo(s->vg, blinker_x + (pos_add*offset) + blinker_w/4 + blinker_w, 100);
      nvgLineTo(s->vg, blinker_x + (pos_add*offset) + blinker_w/2 + blinker_w, 200);
      nvgLineTo(s->vg, blinker_x + (pos_add*offset) + blinker_w/4 + blinker_w, 300);
      nvgLineTo(s->vg, blinker_x + (pos_add*offset)               + blinker_w, 300);
      nvgLineTo(s->vg, blinker_x + (pos_add*offset) + blinker_w/4 + blinker_w, 200);
      nvgClosePath(s->vg);
      if (is_warning) {
        nvgFillColor(s->vg, COLOR_WARNING_ALPHA(200 * alpha));
      } else {
        nvgFillColor(s->vg, COLOR_ENGAGED_ALPHA(200 * alpha));
      }
      nvgFill(s->vg);
    }
  }
}

static void ui_draw_vision_event(UIState *s) {
  // draw steering wheel, bdr_s=10,
  const UIScene &scene = s->scene;
  const int radius = 96;
  const int bg_wheel_x = s->fb_w - radius - (bdr_s*3);
  const int bg_wheel_y = radius + (bdr_s*3);
  const int img_wheel_size = radius + 54; // wheel_size = 150
  const int img_wheel_x = bg_wheel_x - (img_wheel_size/2);
  const int img_wheel_y = bg_wheel_y - (img_wheel_size/2);
  float angleSteers = scene.car_state.getSteeringAngleDeg();
  const float img_rotation = angleSteers/180*3.141592;
  int steerOverride = scene.car_state.getSteeringPressed();
  bool is_engaged = (s->status == STATUS_ENGAGED) && ! steerOverride;
  bool is_warning = (s->status == STATUS_WARNING);
  if (is_engaged || is_warning) {
    nvgBeginPath(s->vg);
    nvgCircle(s->vg, bg_wheel_x, bg_wheel_y, radius); // circle_size = 96
    if (is_engaged) {
      nvgFillColor(s->vg, COLOR_ENGAGED_ALPHA(200));
    } else if (is_warning) {
      nvgFillColor(s->vg, COLOR_WARNING_ALPHA(200));
    } else {
      nvgFillColor(s->vg, COLOR_ENGAGEABLE_ALPHA(200));
    }
    nvgFill(s->vg);
  }
  nvgSave(s->vg);
  nvgTranslate(s->vg, bg_wheel_x, bg_wheel_y);
  nvgRotate(s->vg,-img_rotation);
  nvgBeginPath(s->vg);
  NVGpaint imgPaint = nvgImagePattern(s->vg, img_wheel_x - bg_wheel_x, img_wheel_y - bg_wheel_y, img_wheel_size, img_wheel_size, 0,  s->images["wheel"], 1.0f);
  nvgRect(s->vg, img_wheel_x - bg_wheel_x, img_wheel_y - bg_wheel_y, img_wheel_size, img_wheel_size);
  nvgFillPaint(s->vg, imgPaint);
  nvgFill(s->vg);
  nvgRestore(s->vg);
}

// gps icon upper right
static void ui_draw_gps(UIState *s) {
  const int radius = 60;
  const int center_x = s->fb_w - (radius*7);
  const int center_y = radius + 40;
  auto gps_state = (*s->sm)["liveLocationKalman"].getLiveLocationKalman();
  if (gps_state.getGpsOK()) {
    ui_draw_circle_image(s, center_x, center_y, radius, "gps", COLOR_BLACK_ALPHA(30), 1.0f);
  } else {
    ui_draw_circle_image(s, center_x, center_y, radius, "gps", COLOR_BLACK_ALPHA(10), 0.15f);
  }
}

// wifi icon upper right 2
static void ui_draw_wifi(UIState *s) {
  const int radius = 60;
  const int center_x = s->fb_w - (radius*5);
  const int center_y = radius + 40;
  auto device_state = (*s->sm)["deviceState"].getDeviceState();
  if ((int)device_state.getNetworkStrength() > 0) {
    ui_draw_circle_image(s, center_x, center_y, radius, "wifi", COLOR_BLACK_ALPHA(30), 1.0f);
  } else {
    ui_draw_circle_image(s, center_x, center_y, radius, "wifi", COLOR_BLACK_ALPHA(10), 0.15f);
  }
}

// face icon bottom left
static void ui_draw_vision_face(UIState *s) {
  const int radius = 85;
  const int center_x = radius + (bdr_s * 2);
  const int center_y = s->fb_h - (footer_h/2) + 20;
  ui_draw_circle_image(s, center_x, center_y, radius, "driver_face", s->scene.dm_active);
}

// scc_gap bottom left + radius
static void ui_draw_scc_gap(UIState *s) {
  const int radius = 85;
  const int center_x = radius + (bdr_s*2) + (radius*2);
  const int center_y = s->fb_h - (footer_h/2) + 20;

  int gap = s->scene.car_state.getCruiseGap();
  auto scc_smoother = s->scene.car_control.getSccSmoother();
  bool longControl = scc_smoother.getLongControl();
  int autoTrGap = scc_smoother.getAutoTrGap();

  NVGcolor color_bg = COLOR_BLACK_ALPHA(255 * 0.1f);
  nvgBeginPath(s->vg);
  nvgCircle(s->vg, center_x, center_y, radius);
  nvgFillColor(s->vg, color_bg);
  nvgFill(s->vg);
  NVGcolor textColor = COLOR_WHITE_ALPHA(200);
  float textSize = 25.f;
  char str[64];
  if (gap <= 0) {
    snprintf(str, sizeof(str), "-");
  } else if (longControl && gap == autoTrGap) {
    snprintf(str, sizeof(str), "AUTO");
    textColor = COLOR_LIME_ALPHA(200);
  } else {
    snprintf(str, sizeof(str), "%d", (int)gap);
    textColor = COLOR_LIME_ALPHA(200);
    textSize = 30.f;
  }
  nvgTextAlign(s->vg, NVG_ALIGN_CENTER | NVG_ALIGN_MIDDLE);
  ui_draw_text(s, center_x, center_y-36, "GAP", textSize * 1.6f, COLOR_WHITE_ALPHA(200), "sans-bold");
  ui_draw_text(s, center_x, center_y+22, str, textSize * 2.5f, textColor, "sans-bold");
}

// brake icon bottom left 2
static void ui_draw_brake(UIState *s) {
  const int radius = 85;
  const int center_x = radius + (bdr_s*2);
  const int center_y = s->fb_h - (footer_h/2) - (radius*2) + 20;
  ui_draw_circle_image(s, center_x, center_y, radius, "brake_disc", s->scene.car_state.getBrakeLights());
}

// autohold icon bottom left 2 + radius
static void ui_draw_autohold(UIState *s) {
  int autohold = s->scene.car_state.getAutoHold();
  if (autohold < 0)
    return;
  const int radius = 85;
  const int center_x = radius + (bdr_s*2) + (radius*2);
  const int center_y = s->fb_h - (footer_h/2) - (radius*2) + 20;
  ui_draw_circle_image(s, center_x, center_y, radius, autohold > 1 ? "autohold_warning" : "autohold_active", s->scene.car_state.getAutoHold());
}

// bsd left icon bottom left 3
static void ui_draw_bsd_left(UIState *s) {
  const int radius = 85;
  const int center_x = radius + (bdr_s*2);
  const int center_y = s->fb_h - (footer_h/2) - (radius*4) + 20;
  ui_draw_circle_image(s, center_x, center_y, radius, "bsd_l", s->scene.car_state.getLeftBlindspot());
}

// bsd right icon bottom left 3 + radius
static void ui_draw_bsd_right(UIState *s) {
  const int radius = 85;
  const int center_x = radius + (bdr_s*2) + (radius*2);
  const int center_y = s->fb_h - (footer_h/2) - (radius*4) + 20;
  ui_draw_circle_image(s, center_x, center_y, radius, "bsd_r", s->scene.car_state.getRightBlindspot());
}

static void ui_draw_vision_header(UIState *s) {
  NVGpaint gradient = nvgLinearGradient(s->vg, 0, header_h - (header_h / 2.5), 0, header_h,
                                        nvgRGBAf(0, 0, 0, 0.45), nvgRGBAf(0, 0, 0, 0));
  ui_fill_rect(s->vg, {0, 0, s->fb_w , header_h}, gradient);
  ui_draw_vision_maxspeed(s);
  ui_draw_vision_speed(s);
  ui_draw_vision_event(s);
}

// tpms from neokii
static NVGcolor get_tpms_color(float tpms) {
    if (tpms < 5 || tpms > 60) // N/A
        return COLOR_WHITE_ALPHA(200);
    if (tpms < 31 || tpms > 42)
        return COLOR_RED_ALPHA(200);
    return COLOR_WHITE_ALPHA(200);
}

static std::string get_tpms_text(float tpms) {
    if (tpms < 5 || tpms > 60)
        return "";
    char str[32];
    snprintf(str, sizeof(str), "%.0f", round(tpms));
    return std::string(str);
}

static void ui_draw_tpms(UIState *s) {
    const UIScene &scene = s->scene;
    auto tpms = scene.car_state.getTpms();
    const float fl = tpms.getFl();
    const float fr = tpms.getFr();
    const float rl = tpms.getRl();
    const float rr = tpms.getRr();
    int margin = 10;
    int x = s->fb_w - 170;
    int y = 850;
    int w = 66;
    int h = 146;
    ui_draw_image(s, {x, y, w, h}, "tire_pressure", 0.8f);

    nvgFontSize(s->vg, 50);
    nvgFontFace(s->vg, "sans-semibold");

    nvgTextAlign(s->vg, NVG_ALIGN_RIGHT);
    nvgFillColor(s->vg, get_tpms_color(fl));
    nvgText(s->vg, x-margin, y+45, get_tpms_text(fl).c_str(), NULL);

    nvgTextAlign(s->vg, NVG_ALIGN_LEFT);
    nvgFillColor(s->vg, get_tpms_color(fr));
    nvgText(s->vg, x+w+margin, y+45, get_tpms_text(fr).c_str(), NULL);

    nvgTextAlign(s->vg, NVG_ALIGN_RIGHT);
    nvgFillColor(s->vg, get_tpms_color(rl));
    nvgText(s->vg, x-margin, y+h-15, get_tpms_text(rl).c_str(), NULL);

    nvgTextAlign(s->vg, NVG_ALIGN_LEFT);
    nvgFillColor(s->vg, get_tpms_color(rr));
    nvgText(s->vg, x+w+margin, y+h-15, get_tpms_text(rr).c_str(), NULL);
}

//START: functions added for the display of various items

static int ui_draw_measure(UIState *s, const char* value, const char* label, int x, int y, NVGcolor valueColor, NVGcolor labelColor, int valueFontSize, int labelFontSize) {
  nvgTextAlign(s->vg, NVG_ALIGN_CENTER | NVG_ALIGN_BASELINE);

  //print value
  nvgFontFace(s->vg, "sans-semibold");
  nvgFontSize(s->vg, valueFontSize);
  nvgFillColor(s->vg, valueColor);
  nvgText(s->vg, x, y + (int)(valueFontSize), value, NULL);

  //print label
  nvgFontFace(s->vg, "sans-regular");
  nvgFontSize(s->vg, labelFontSize);
  nvgFillColor(s->vg, labelColor);
  nvgText(s->vg, x, y + (int)(valueFontSize) + (int)(labelFontSize), label, NULL);

  return (int)((valueFontSize + labelFontSize));
}

static void ui_draw_measures_right(UIState *s, int x, int y, int w) {
  const UIScene &scene = s->scene;
  int rx = x + (int)(w/2);
  int ry = y;
  int h = 5;
  NVGcolor lab_color = COLOR_WHITE_ALPHA(200);
  int value_fontSize=60;
  int label_fontSize=40;
  bool is_enabled = scene.controls_state.getEnabled();

  //add CPU temperature average
  if (true) {
    char val_str[16];
    char val_add[4] = "℃";
    NVGcolor val_color = COLOR_LIME_ALPHA(200);

    auto cpuList = scene.device_state.getCpuTempC();
    float cpuTemp = 0;

    if (cpuList.size() > 0) {
        for(int i = 0; i < cpuList.size(); i++)
            cpuTemp += cpuList[i];
        cpuTemp /= cpuList.size();
    }

    // Orange Color if more than 70℃ / Red Color if more than 80℃
    if ((int)(cpuTemp) >= 70) { val_color = COLOR_WARNING_ALPHA(200); }
    if ((int)(cpuTemp) >= 80) { val_color = COLOR_RED_ALPHA(200); }

    snprintf(val_str, sizeof(val_str), "%.1f", cpuTemp);
    strcat(val_str, val_add);
    h += ui_draw_measure(s, val_str, "CPU 온도", rx, ry, val_color, lab_color, value_fontSize, label_fontSize);
    ry = y + h;
  }

  //add steering angle degree
  if (true) {
    char val_str[16];
    char val_add[4] = "˚";
    NVGcolor val_color = COLOR_LIME_ALPHA(200);

    float angleSteers = scene.car_state.getSteeringAngleDeg();

    // Orange color if more than 30˚ / Red color if more than 90˚
    if (((int)(angleSteers) < -30) || ((int)(angleSteers) > 30)) { val_color = COLOR_WARNING_ALPHA(200); }
    if (((int)(angleSteers) < -90) || ((int)(angleSteers) > 90)) { val_color = COLOR_RED_ALPHA(200); }

    snprintf(val_str, sizeof(val_str), "%.1f", angleSteers);
    strcat(val_str, val_add);
    h += ui_draw_measure(s, val_str, "핸들 조향각", rx, ry, val_color, lab_color, value_fontSize, label_fontSize);
    ry = y + h;
  }

  //add desired steering angle degree
  if (is_enabled) {
    char val_str[16];
    char val_add[4] = "˚";
    NVGcolor val_color = COLOR_LIME_ALPHA(200);

    auto actuators = scene.car_control.getActuators();
    float steeringAngleDeg  = actuators.getSteeringAngleDeg();

    // Orange color if more than 30˚ / Red color if more than 90˚
    if (((int)(steeringAngleDeg) < -30) || ((int)(steeringAngleDeg) > 30)) { val_color = COLOR_WARNING_ALPHA(200); }
    if (((int)(steeringAngleDeg) < -90) || ((int)(steeringAngleDeg) > 90)) { val_color = COLOR_RED_ALPHA(200); }

    snprintf(val_str, sizeof(val_str), "%.1f", steeringAngleDeg);
    strcat(val_str, val_add);
    h += ui_draw_measure(s, val_str, "OP 조향각", rx, ry, val_color, lab_color, value_fontSize, label_fontSize);
    ry = y + h;
  }

  //add visual radar relative distance
  if (is_enabled) {
    char val_str[16];
    char val_add[4] = "ｍ";
    NVGcolor val_color = COLOR_WHITE_ALPHA(200);

    auto lead_radar = (*s->sm)["radarState"].getRadarState().getLeadOne();
    float radar_dist = lead_radar.getStatus() && lead_radar.getRadar() ? lead_radar.getDRel() : 0;

    // Orange Color if less than 15ｍ / Red Color if less than 5ｍ
    if (lead_radar.getStatus()) {
      if (radar_dist < 15) { val_color = COLOR_WARNING_ALPHA(200); }
      if (radar_dist < 5) { val_color = COLOR_RED_ALPHA(200); }
      snprintf(val_str, sizeof(val_str), "%.1f", radar_dist);
    } else {
      snprintf(val_str, sizeof(val_str), "-");
    }

    strcat(val_str, val_add);
    h += ui_draw_measure(s, val_str, "Radar 거리", rx, ry, val_color, lab_color, value_fontSize, label_fontSize);
    ry = y + h;
  }


  //add visual vision relative distance
  if (is_enabled) {
    char val_str[16];
    char val_add[4] = "ｍ";
    NVGcolor val_color = COLOR_WHITE_ALPHA(200);

    auto lead_vision = (*s->sm)["modelV2"].getModelV2().getLeadsV3()[0];
    float vision_dist = lead_vision.getProb() > .5 ? (lead_vision.getX()[0] - 1.5) : 0;

    // Orange Color if less than 15ｍ / Red Color if less than 5ｍ
    if (lead_vision.getProb()) {
      if (vision_dist < 15) { val_color = COLOR_WARNING_ALPHA(200); }
      if (vision_dist < 5) { val_color = COLOR_RED_ALPHA(200); }
      snprintf(val_str, sizeof(val_str), "%.1f", vision_dist);
    } else {
      snprintf(val_str, sizeof(val_str), "-");
    }

    strcat(val_str, val_add);
    h += ui_draw_measure(s, val_str, "Vision 거리", rx, ry, val_color, lab_color, value_fontSize, label_fontSize);
    ry = y + h;
  }

/*
  //add visual radar relative speed
  if (is_enabled) {
    char val_str[16];
    char val_add[4] = "㎞";
    NVGcolor val_color = COLOR_WHITE_ALPHA(200);

    auto radar_state = (*s->sm)["radarState"].getRadarState();
    auto lead_one = radar_state.getLeadOne();

    // Orange Color if negative speed / Red Color if positive speed
    if (lead_one.getStatus()) {
      if ((int)(lead_one.getVRel()) < 0) { val_color = COLOR_WARNING; }
      if ((int)(lead_one.getVRel()) < -5) { val_color = COLOR_RED_ALPHA(200); }
      snprintf(val_str, sizeof(val_str), "%d", (int)(lead_one.getVRel() * 3.6 + 0.5));
    } else {
      snprintf(val_str, sizeof(val_str), "-");
    }

    strcat(val_str, val_add);
    h +=ui_draw_measure(s, val_str, "속도차", rx, ry, val_color, lab_color, value_fontSize, label_fontSize);
    ry = y + h;
  }
*/

  //finally draw the frame
  h += 20;
  nvgBeginPath(s->vg);
  nvgRoundedRect(s->vg, x, y, w, h, 20);
  nvgStrokeColor(s->vg, COLOR_WHITE_ALPHA(80));
  nvgStrokeWidth(s->vg, 6);
  nvgStroke(s->vg);
}

static void ui_draw_right_info(UIState *s) {
  const int right_w = 180;
  const int right_x = s->fb_w - right_w * 1.25;
  const int right_y = right_w * 1.5;
  ui_draw_measures_right(s, right_x, right_y, right_w);
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
  ui_draw_vision_face(s);

  // ui info add
  ui_draw_scc_gap(s);
  ui_draw_brake(s);
  ui_draw_autohold(s);
  ui_draw_bsd_left(s);
  ui_draw_bsd_right(s);
  ui_draw_wifi(s);
  ui_draw_gps(s);
  ui_draw_tpms(s);
  ui_draw_extras(s);
  ui_draw_right_info(s);
  ui_draw_bottom_info(s);
}

void ui_draw(UIState *s, int w, int h) {
  // Update intrinsics matrix after possible wide camera toggle change
  if (s->fb_w != w || s->fb_h != h) {
    ui_resize(s, w, h);
  }
  glEnable(GL_BLEND);
  glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);
  nvgBeginFrame(s->vg, s->fb_w, s->fb_h, 1.0f);
  ui_draw_vision(s);
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

void ui_nvg_init(UIState *s) {
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
    {"wifi", "../assets/img_wifi.png"},
    {"autohold_warning", "../assets/img_autohold_warning.png"},
    {"autohold_active", "../assets/img_autohold_active.png"},
    {"tire_pressure", "../assets/img_tire_pressure.png"},
    {"img_nda", "../assets/img_nda.png"},
    {"img_hda", "../assets/img_hda.png"},
    {"custom_lead_vision", "../assets/custom_lead_vision.png"},
    {"custom_lead_radar", "../assets/custom_lead_radar.png"},
  };
  for (auto [name, file] : images) {
    s->images[name] = nvgCreateImage(s->vg, file, 1);
    assert(s->images[name] != 0);
  }
}

void ui_resize(UIState *s, int width, int height) {
  s->fb_w = width;
  s->fb_h = height;

  auto intrinsic_matrix = s->wide_camera ? ecam_intrinsic_matrix : fcam_intrinsic_matrix;
  float zoom = ZOOM / intrinsic_matrix.v[0];
  if (s->wide_camera) {
    zoom *= 0.5;
  }

  // Apply transformation such that video pixel coordinates match video
  // 1) Put (0, 0) in the middle of the video
  // 2) Apply same scaling as video
  // 3) Put (0, 0) in top left corner of video
  s->car_space_transform.reset();
  s->car_space_transform.translate(width / 2, height / 2 + y_offset)
      .scale(zoom, zoom)
      .translate(-intrinsic_matrix.v[2], -intrinsic_matrix.v[5]);
}
