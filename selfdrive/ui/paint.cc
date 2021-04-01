#include "ui.hpp"

#include <assert.h>
#include "common/util.h"
#include "common/timing.h"
#include <algorithm>

#define NANOVG_GLES3_IMPLEMENTATION
#include "nanovg_gl.h"
#include "nanovg_gl_utils.h"
#include "paint.hpp"
#include "sidebar.hpp"
#include "extras.h"

#ifdef QCOM2
const int vwp_w = 2160;
#else
const int vwp_w = 1920;
#endif
const int vwp_h = 1080;

const int bdr_is = 30;
const int box_x = sbr_w+bdr_s;
const int box_y = bdr_s;
const int box_w = vwp_w-sbr_w-(bdr_s*2);
const int box_h = vwp_h-(bdr_s*2);


// TODO: this is also hardcoded in common/transformations/camera.py
// TODO: choose based on frame input size
#ifdef QCOM2
const float y_offset = 150.0;
const float zoom = 1.1;
#else
const float y_offset = 0.0;
const float zoom = 2.35;
#endif

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

static void draw_lead(UIState *s, int idx) {
  // Draw lead car indicator
  const auto &lead = s->scene.lead_data[idx];
  auto [x, y] = s->scene.lead_vertices[idx];

  float fillAlpha = 0;
  float speedBuff = 10.;
  float leadBuff = 40.;
  float d_rel = lead.getDRel();
  float v_rel = lead.getVRel();
  if (d_rel < leadBuff) {
    fillAlpha = 255*(1.0-(d_rel/leadBuff));
    if (v_rel < 0) {
      fillAlpha += 255*(-1*(v_rel/speedBuff));
    }
    fillAlpha = (int)(fmin(fillAlpha, 255));
  }

  float sz = std::clamp((25 * 30) / (d_rel / 3 + 30), 15.0f, 30.0f) * zoom;
  x = std::clamp(x, 0.f, s->viz_rect.right() - sz / 2);
  y = std::fmin(s->viz_rect.bottom() - sz * .6, y);

  NVGcolor color = COLOR_YELLOW;
  if(lead.getRadar())
    color = nvgRGBA(112, 128, 255, 255);

  draw_chevron(s, x, y, sz, nvgRGBA(201, 34, 49, fillAlpha), color);
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

static void draw_frame(UIState *s) {
  mat4 *out_mat;
  if (s->scene.driver_view) {
    glBindVertexArray(s->frame_vao[1]);
    out_mat = &s->front_frame_mat;
  } else {
    glBindVertexArray(s->frame_vao[0]);
    out_mat = &s->rear_frame_mat;
  }
  glActiveTexture(GL_TEXTURE0);

  if (s->last_frame) {
    glBindTexture(GL_TEXTURE_2D, s->texture[s->last_frame->idx]->frame_tex);
#ifndef QCOM
    // this is handled in ion on QCOM
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB, s->last_frame->width, s->last_frame->height,
                 0, GL_RGB, GL_UNSIGNED_BYTE, s->last_frame->addr);
#endif
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
  NVGpaint track_bg;
  if (!scene.end_to_end) {
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
    track_bg = nvgLinearGradient(s->vg, s->fb_w, s->fb_h, s->fb_w, s->fb_h * .4,
                                          COLOR_WHITE_ALPHA(180), COLOR_WHITE_ALPHA(0));
  } else {
    track_bg = nvgLinearGradient(s->vg, s->fb_w, s->fb_h, s->fb_w, s->fb_h * .4,
                                          COLOR_RED_ALPHA(180), COLOR_RED_ALPHA(0));
  }
  // paint path
  ui_draw_line(s, scene.track_vertices, nullptr, &track_bg);
}

// Draw all world space objects.
static void ui_draw_world(UIState *s) {
  const UIScene *scene = &s->scene;
  // Don't draw on top of sidebar
  nvgScissor(s->vg, s->viz_rect.x, s->viz_rect.y, s->viz_rect.w, s->viz_rect.h);

  // Draw lane edges and vision/mpc tracks
  ui_draw_vision_lane_lines(s);

  // Draw lead indicators if openpilot is handling longitudinal
  //if (s->scene.longitudinal_control) {
    if (scene->lead_data[0].getStatus()) {
      draw_lead(s, 0);
    }
    if (scene->lead_data[1].getStatus() && (std::abs(scene->lead_data[0].getDRel() - scene->lead_data[1].getDRel()) > 3.0)) {
      draw_lead(s, 1);
    }
  //}
  
  nvgResetScissor(s->vg);
}

static int bb_ui_draw_measure(UIState *s,  const char* bb_value, const char* bb_uom, const char* bb_label,
    int bb_x, int bb_y, int bb_uom_dx,
    NVGcolor bb_valueColor, NVGcolor bb_labelColor, NVGcolor bb_uomColor,
    int bb_valueFontSize, int bb_labelFontSize, int bb_uomFontSize )  {
  //const UIScene *scene = &s->scene;
  nvgTextAlign(s->vg, NVG_ALIGN_CENTER | NVG_ALIGN_BASELINE);
  int dx = 0;
  if (strlen(bb_uom) > 0) {
    dx = (int)(bb_uomFontSize*2.5/2);
   }
  //print value
  nvgFontFace(s->vg, "sans-semibold");
  nvgFontSize(s->vg, bb_valueFontSize*2.5);
  nvgFillColor(s->vg, bb_valueColor);
  nvgText(s->vg, bb_x-dx/2, bb_y+ (int)(bb_valueFontSize*2.5)+5, bb_value, NULL);
  //print label
  nvgFontFace(s->vg, "sans-regular");
  nvgFontSize(s->vg, bb_labelFontSize*2.5);
  nvgFillColor(s->vg, bb_labelColor);
  nvgText(s->vg, bb_x, bb_y + (int)(bb_valueFontSize*2.5)+5 + (int)(bb_labelFontSize*2.5)+5, bb_label, NULL);
  //print uom
  if (strlen(bb_uom) > 0) {
      nvgSave(s->vg);
    int rx =bb_x + bb_uom_dx + bb_valueFontSize -3;
    int ry = bb_y + (int)(bb_valueFontSize*2.5/2)+25;
    nvgTranslate(s->vg,rx,ry);
    nvgRotate(s->vg, -1.5708); //-90deg in radians
    nvgFontFace(s->vg, "sans-regular");
    nvgFontSize(s->vg, (int)(bb_uomFontSize*2.5));
    nvgFillColor(s->vg, bb_uomColor);
    nvgText(s->vg, 0, 0, bb_uom, NULL);
    nvgRestore(s->vg);
  }
  return (int)((bb_valueFontSize + bb_labelFontSize)*2.5) + 5;
}

static void bb_ui_draw_measures_left(UIState *s, int bb_x, int bb_y, int bb_w ) {
  const UIScene *scene = &s->scene;
  int bb_rx = bb_x + (int)(bb_w/2);
  int bb_ry = bb_y;
  int bb_h = 5;
  NVGcolor lab_color = nvgRGBA(255, 255, 255, 200);
  NVGcolor uom_color = nvgRGBA(255, 255, 255, 200);
  int value_fontSize=30;
  int label_fontSize=15;
  int uom_fontSize = 15;
  int bb_uom_dx =  (int)(bb_w /2 - uom_fontSize*2.5) ;

  //add visual radar relative distance
  if (UI_FEATURE_LEFT_REL_DIST) {
    char val_str[16];
    char uom_str[6];
    NVGcolor val_color = nvgRGBA(255, 255, 255, 200);
    if (scene->lead_data[0].getStatus()) {
      //show RED if less than 5 meters
      //show orange if less than 15 meters
      if((int)(scene->lead_data[0].getDRel()) < 15) {
        val_color = nvgRGBA(255, 188, 3, 200);
      }
      if((int)(scene->lead_data[0].getDRel()) < 5) {
        val_color = nvgRGBA(255, 0, 0, 200);
      }
      // lead car relative distance is always in meters
      snprintf(val_str, sizeof(val_str), "%d", (int)scene->lead_data[0].getDRel());
    } else {
       snprintf(val_str, sizeof(val_str), "-");
    }
    snprintf(uom_str, sizeof(uom_str), "m   ");
    bb_h +=bb_ui_draw_measure(s,  val_str, uom_str, "REL DIST",
        bb_rx, bb_ry, bb_uom_dx,
        val_color, lab_color, uom_color,
        value_fontSize, label_fontSize, uom_fontSize );
    bb_ry = bb_y + bb_h;
  }

  //add visual radar relative speed
  if (UI_FEATURE_LEFT_REL_SPEED) {
    char val_str[16];
    char uom_str[6];
    NVGcolor val_color = nvgRGBA(255, 255, 255, 200);
    if (scene->lead_data[0].getStatus()) {
      //show Orange if negative speed (approaching)
      //show Orange if negative speed faster than 5mph (approaching fast)
      if((int)(scene->lead_data[0].getVRel()) < 0) {
        val_color = nvgRGBA(255, 188, 3, 200);
      }
      if((int)(scene->lead_data[0].getVRel()) < -5) {
        val_color = nvgRGBA(255, 0, 0, 200);
      }
      // lead car relative speed is always in meters
      if (s->scene.is_metric) {
         snprintf(val_str, sizeof(val_str), "%d", (int)(scene->lead_data[0].getVRel() * 3.6 + 0.5));
      } else {
         snprintf(val_str, sizeof(val_str), "%d", (int)(scene->lead_data[0].getVRel() * 2.2374144 + 0.5));
      }
    } else {
       snprintf(val_str, sizeof(val_str), "-");
    }
    if (s->scene.is_metric) {
      snprintf(uom_str, sizeof(uom_str), "km/h");;
    } else {
      snprintf(uom_str, sizeof(uom_str), "mph");
    }
    bb_h +=bb_ui_draw_measure(s,  val_str, uom_str, "REL SPEED",
        bb_rx, bb_ry, bb_uom_dx,
        val_color, lab_color, uom_color,
        value_fontSize, label_fontSize, uom_fontSize );
    bb_ry = bb_y + bb_h;
  }

  //add  steering angle
  if (UI_FEATURE_LEFT_REAL_STEER) {
    char val_str[16];
    char uom_str[6];
    NVGcolor val_color = nvgRGBA(0, 255, 0, 200);
      //show Orange if more than 30 degrees
      //show red if  more than 50 degrees

      float angleSteers = scene->controls_state.getAngleSteers();

      if(((int)(angleSteers) < -30) || ((int)(angleSteers) > 30)) {
        val_color = nvgRGBA(255, 175, 3, 200);
      }
      if(((int)(angleSteers) < -55) || ((int)(angleSteers) > 55)) {
        val_color = nvgRGBA(255, 0, 0, 200);
      }
      // steering is in degrees
      snprintf(val_str, sizeof(val_str), "%.1f°", angleSteers);

      snprintf(uom_str, sizeof(uom_str), "");
    bb_h +=bb_ui_draw_measure(s,  val_str, uom_str, "REAL STEER",
        bb_rx, bb_ry, bb_uom_dx,
        val_color, lab_color, uom_color,
        value_fontSize, label_fontSize, uom_fontSize );
    bb_ry = bb_y + bb_h;
  }

  //add  desired steering angle
  if (UI_FEATURE_LEFT_DESIRED_STEER) {
    char val_str[16];
    char uom_str[6];
    NVGcolor val_color = nvgRGBA(255, 255, 255, 200);
    if (scene->controls_state.getEnabled()) {
      //show Orange if more than 6 degrees
      //show red if  more than 12 degrees

      float steeringAngleDesiredDeg  = scene->controls_state.getSteeringAngleDesiredDeg ();

      if(((int)(steeringAngleDesiredDeg ) < -30) || ((int)(steeringAngleDesiredDeg ) > 30)) {
        val_color = nvgRGBA(255, 255, 255, 200);
      }
      if(((int)(steeringAngleDesiredDeg ) < -50) || ((int)(steeringAngleDesiredDeg ) > 50)) {
        val_color = nvgRGBA(255, 255, 255, 200);
      }
      // steering is in degrees
      snprintf(val_str, sizeof(val_str), "%.1f°", steeringAngleDesiredDeg );
    } else {
       snprintf(val_str, sizeof(val_str), "-");
    }
      snprintf(uom_str, sizeof(uom_str), "");
    bb_h +=bb_ui_draw_measure(s,  val_str, uom_str, "DESIR STEER",
        bb_rx, bb_ry, bb_uom_dx,
        val_color, lab_color, uom_color,
        value_fontSize, label_fontSize, uom_fontSize );
    bb_ry = bb_y + bb_h;
  }


  //finally draw the frame
  bb_h += 40;
  nvgBeginPath(s->vg);
    nvgRoundedRect(s->vg, bb_x, bb_y, bb_w, bb_h, 20);
    nvgStrokeColor(s->vg, nvgRGBA(255,255,255,80));
    nvgStrokeWidth(s->vg, 6);
    nvgStroke(s->vg);
}

static void bb_ui_draw_measures_right(UIState *s, int bb_x, int bb_y, int bb_w ) {
  const UIScene *scene = &s->scene;
  int bb_rx = bb_x + (int)(bb_w/2);
  int bb_ry = bb_y;
  int bb_h = 5;
  NVGcolor lab_color = nvgRGBA(255, 255, 255, 200);
  NVGcolor uom_color = nvgRGBA(255, 255, 255, 200);
  int value_fontSize=30;
  int label_fontSize=15;
  int uom_fontSize = 15;
  int bb_uom_dx =  (int)(bb_w /2 - uom_fontSize*2.5) ;

  // add CPU temperature
  if (UI_FEATURE_RIGHT_CPU_TEMP) {
        char val_str[16];
    char uom_str[6];
    NVGcolor val_color = nvgRGBA(255, 255, 255, 200);

    float cpuTemp = 0;
    auto cpuList = scene->deviceState.getCpuTempC();

    if(cpuList.size() > 0)
    {
        for(int i = 0; i < cpuList.size(); i++)
            cpuTemp += cpuList[i];

        cpuTemp /= cpuList.size();
    }

      if(cpuTemp > 80.f) {
        val_color = nvgRGBA(255, 188, 3, 200);
      }
      if(cpuTemp > 92.f) {
        val_color = nvgRGBA(255, 0, 0, 200);
      }
      // temp is alway in C * 10
      snprintf(val_str, sizeof(val_str), "%.1f°", cpuTemp);
      snprintf(uom_str, sizeof(uom_str), "");
    bb_h +=bb_ui_draw_measure(s,  val_str, uom_str, "CPU TEMP",
        bb_rx, bb_ry, bb_uom_dx,
        val_color, lab_color, uom_color,
        value_fontSize, label_fontSize, uom_fontSize );
    bb_ry = bb_y + bb_h;
  }

   // add battery temperature
  if (UI_FEATURE_RIGHT_BATTERY_TEMP) {
    char val_str[16];
    char uom_str[6];
    NVGcolor val_color = nvgRGBA(255, 255, 255, 200);

    float batteryTemp = scene->deviceState.getBatteryTempC();

    if(batteryTemp > 40.f) {
      val_color = nvgRGBA(255, 188, 3, 200);
    }
    if(batteryTemp > 50.f) {
      val_color = nvgRGBA(255, 0, 0, 200);
    }
    // temp is alway in C * 1000
    snprintf(val_str, sizeof(val_str), "%.1f°", batteryTemp);
    snprintf(uom_str, sizeof(uom_str), "");
    bb_h +=bb_ui_draw_measure(s,  val_str, uom_str, "BAT TEMP",
        bb_rx, bb_ry, bb_uom_dx,
        val_color, lab_color, uom_color,
        value_fontSize, label_fontSize, uom_fontSize );
    bb_ry = bb_y + bb_h;
  }

  // add battery level
    if(UI_FEATURE_RIGHT_BATTERY_LEVEL) {
    char val_str[16];
    char uom_str[6];
    char bat_lvl[4] = "";
    NVGcolor val_color = nvgRGBA(255, 255, 255, 200);

    int batteryPercent = scene->deviceState.getBatteryPercent();

    snprintf(val_str, sizeof(val_str), "%d%%", batteryPercent);
    snprintf(uom_str, sizeof(uom_str), "");
    bb_h +=bb_ui_draw_measure(s,  val_str, uom_str, "BAT LVL",
        bb_rx, bb_ry, bb_uom_dx,
        val_color, lab_color, uom_color,
        value_fontSize, label_fontSize, uom_fontSize );
    bb_ry = bb_y + bb_h;
  }

  // add panda GPS altitude
  if (UI_FEATURE_RIGHT_GPS_ALTITUDE) {
    char val_str[16];
    char uom_str[3];
    NVGcolor val_color = nvgRGBA(255, 255, 255, 200);

    snprintf(val_str, sizeof(val_str), "%.1f", s->scene.gps_ext.getAltitude());
    snprintf(uom_str, sizeof(uom_str), "m");
    bb_h +=bb_ui_draw_measure(s,  val_str, uom_str, "ALTITUDE",
        bb_rx, bb_ry, bb_uom_dx,
        val_color, lab_color, uom_color,
        value_fontSize, label_fontSize, uom_fontSize );
    bb_ry = bb_y + bb_h;
  }

  // add panda GPS accuracy
  if (UI_FEATURE_RIGHT_GPS_ACCURACY) {
    char val_str[16];
    char uom_str[3];

    auto gps_ext = s->scene.gps_ext;
    float verticalAccuracy = gps_ext.getVerticalAccuracy();
    float gpsAltitude = gps_ext.getAltitude();
    float gpsAccuracy = gps_ext.getAccuracy();

    if(verticalAccuracy == 0 || verticalAccuracy > 100)
        gpsAltitude = 99.99;

    if (gpsAccuracy > 100)
      gpsAccuracy = 99.99;
    else if (gpsAccuracy == 0)
      gpsAccuracy = 99.8;

    NVGcolor val_color = nvgRGBA(255, 255, 255, 200);
    if(gpsAccuracy > 1.0) {
         val_color = nvgRGBA(255, 188, 3, 200);
      }
      if(gpsAccuracy > 2.0) {
         val_color = nvgRGBA(255, 80, 80, 200);
      }

    snprintf(val_str, sizeof(val_str), "%.2f", gpsAccuracy);
    snprintf(uom_str, sizeof(uom_str), "m");
    bb_h +=bb_ui_draw_measure(s,  val_str, uom_str, "GPS PREC",
        bb_rx, bb_ry, bb_uom_dx,
        val_color, lab_color, uom_color,
        value_fontSize, label_fontSize, uom_fontSize );
    bb_ry = bb_y + bb_h;
  }

  // add panda GPS satellite
  if (UI_FEATURE_RIGHT_GPS_SATELLITE) {
    char val_str[16];
    char uom_str[3];
    NVGcolor val_color = nvgRGBA(255, 255, 255, 200);

    if(s->scene.satelliteCount < 6)
         val_color = nvgRGBA(255, 80, 80, 200);

    snprintf(val_str, sizeof(val_str), "%d", s->scene.satelliteCount > 0 ? s->scene.satelliteCount : 0);
    snprintf(uom_str, sizeof(uom_str), "");
    bb_h +=bb_ui_draw_measure(s,  val_str, uom_str, "SATELLITE",
        bb_rx, bb_ry, bb_uom_dx,
        val_color, lab_color, uom_color,
        value_fontSize, label_fontSize, uom_fontSize );
    bb_ry = bb_y + bb_h;
  }

  //finally draw the frame
  bb_h += 40;
  nvgBeginPath(s->vg);
  nvgRoundedRect(s->vg, bb_x, bb_y, bb_w, bb_h, 20);
  nvgStrokeColor(s->vg, nvgRGBA(255,255,255,80));
  nvgStrokeWidth(s->vg, 6);
  nvgStroke(s->vg);
}

static void bb_ui_draw_basic_info(UIState *s)
{
    const UIScene *scene = &s->scene;
    char str[1024];
    std::string sccLogMessage = "";

    if(s->show_debug_ui)
    {
        cereal::CarControl::SccSmoother::Reader scc_smoother = scene->car_control.getSccSmoother();
        sccLogMessage = std::string(scc_smoother.getLogMessage());
    }

    int mdps_bus = scene->car_params.getMdpsBus();
    int scc_bus = scene->car_params.getSccBus();

    snprintf(str, sizeof(str), "SR(%.2f) SRC(%.2f) SAD(%.2f) AO(%.2f/%.2f) MDPS(%d) SCC(%d)%s%s", scene->controls_state.getSteerRatio(),
                                                        scene->controls_state.getSteerRateCost(),
                                                        scene->controls_state.getSteerActuatorDelay(),
                                                        scene->live_params.getAngleOffsetDeg(),
                                                        scene->live_params.getAngleOffsetAverageDeg(),
                                                        mdps_bus, scc_bus,
                                                        sccLogMessage.size() > 0 ? ", " : "",
                                                        sccLogMessage.c_str()
                                                        );

    int x = s->viz_rect.x + (bdr_s * 2);
    int y = s->viz_rect.bottom() - 24;

    nvgTextAlign(s->vg, NVG_ALIGN_LEFT | NVG_ALIGN_MIDDLE);

    ui_draw_text(s, x, y, str, 20 * 2.5, COLOR_WHITE_ALPHA(200), "sans-semibold");
}

static void bb_ui_draw_debug(UIState *s)
{
    const UIScene *scene = &s->scene;
    char str[1024];

    int y = 80;
    const int height = 60;

    nvgTextAlign(s->vg, NVG_ALIGN_CENTER | NVG_ALIGN_BASELINE);

    const int text_x = s->viz_rect.centerX() + s->viz_rect.w * 10 / 55;

    float applyAccel = scene->controls_state.getApplyAccel();
    float fusedAccel = scene->controls_state.getFusedAccel();
    float leadDist = scene->controls_state.getLeadDist();

    float aReqValue = scene->controls_state.getAReqValue();
    float aReqValueMin = scene->controls_state.getAReqValueMin();
    float aReqValueMax = scene->controls_state.getAReqValueMax();

    int longControlState = (int)scene->controls_state.getLongControlState();
    float vPid = scene->controls_state.getVPid();
    float upAccelCmd = scene->controls_state.getUpAccelCmd();
    float uiAccelCmd = scene->controls_state.getUiAccelCmd();
    float ufAccelCmd = scene->controls_state.getUfAccelCmd();
    float gas = scene->car_control.getActuators().getGas();
    float brake = scene->car_control.getActuators().getBrake();

    const char* long_state[] = {"off", "pid", "stopping", "starting"};

    const NVGcolor textColor = COLOR_WHITE;

    y += height;
    snprintf(str, sizeof(str), "State: %s", long_state[longControlState]);
    ui_draw_text(s, text_x, y, str, 22 * 2.5, textColor, "sans-regular");

    y += height;
    snprintf(str, sizeof(str), "vPid: %.3f(%.1f)", vPid, vPid * 3.6f);
    ui_draw_text(s, text_x, y, str, 22 * 2.5, textColor, "sans-regular");

    y += height;
    snprintf(str, sizeof(str), "P: %.3f", upAccelCmd);
    ui_draw_text(s, text_x, y, str, 22 * 2.5, textColor, "sans-regular");

    y += height;
    snprintf(str, sizeof(str), "I: %.3f", uiAccelCmd);
    ui_draw_text(s, text_x, y, str, 22 * 2.5, textColor, "sans-regular");

    y += height;
    snprintf(str, sizeof(str), "F: %.3f", ufAccelCmd);
    ui_draw_text(s, text_x, y, str, 22 * 2.5, textColor, "sans-regular");

    y += height;
    snprintf(str, sizeof(str), "Gas: %.3f, Brake: %.3f", gas, brake);
    ui_draw_text(s, text_x, y, str, 22 * 2.5, textColor, "sans-regular");

    y += height;
    snprintf(str, sizeof(str), "LeadDist: %.3f", leadDist);
    ui_draw_text(s, text_x, y, str, 22 * 2.5, textColor, "sans-regular");

    y += height;
    snprintf(str, sizeof(str), "Accel: %.3f/%.3f/%.3f", applyAccel, fusedAccel, aReqValue);
    ui_draw_text(s, text_x, y, str, 22 * 2.5, textColor, "sans-regular");

    y += height;
    snprintf(str, sizeof(str), "%.3f (%.3f/%.3f)", aReqValue, aReqValueMin, aReqValueMax);
    ui_draw_text(s, text_x, y, str, 22 * 2.5, textColor, "sans-regular");
}


static void bb_ui_draw_UI(UIState *s)
{
  const int bb_dml_w = 180;
  const int bb_dml_x = (s->viz_rect.x + (bdr_is * 2));
  const int bb_dml_y = (box_y + (bdr_is * 1.5)) + UI_FEATURE_LEFT_Y;

  const int bb_dmr_w = 180;
  const int bb_dmr_x = s->viz_rect.x + s->viz_rect.w - bb_dmr_w - (bdr_is * 2);
  const int bb_dmr_y = (box_y + (bdr_is * 1.5)) + UI_FEATURE_RIGHT_Y;

#if UI_FEATURE_LEFT
  bb_ui_draw_measures_left(s, bb_dml_x, bb_dml_y, bb_dml_w);
#endif

#if UI_FEATURE_RIGHT
  bb_ui_draw_measures_right(s, bb_dmr_x, bb_dmr_y, bb_dmr_w);
#endif

  bb_ui_draw_basic_info(s);

  if(s->show_debug_ui)
    bb_ui_draw_debug(s);
}

static void ui_draw_vision_brake(UIState *s) {
  const UIScene *scene = &s->scene;
  const int brake_size = 96;
  const int brake_x = (s->viz_rect.x + (brake_size * 4) + (bdr_is * 4));
  const int brake_y = (s->viz_rect.bottom() - footer_h + ((footer_h - brake_size) / 2));
  const int brake_img_size = (brake_size * 1.5);
  const int brake_img_x = (brake_x - (brake_img_size / 2));
  const int brake_img_y = (brake_y - (brake_size / 4));

  bool brake_valid = scene->car_state.getBrakeLights();
  float brake_img_alpha = brake_valid ? 1.0f : 0.15f;
  float brake_bg_alpha = brake_valid ? 0.3f : 0.1f;
  NVGcolor brake_bg = nvgRGBA(0, 0, 0, (255 * brake_bg_alpha));

  NVGpaint brake_img = nvgImagePattern(s->vg, brake_img_x, brake_img_y,
    brake_img_size, brake_img_size, 0, s->images["brake"], brake_img_alpha);

  nvgBeginPath(s->vg);
  nvgCircle(s->vg, brake_x, (brake_y + (bdr_is * 1.5)), brake_size);
  nvgFillColor(s->vg, brake_bg);
  nvgFill(s->vg);

  nvgBeginPath(s->vg);
  nvgRect(s->vg, brake_img_x, brake_img_y, brake_img_size, brake_img_size);
  nvgFillPaint(s->vg, brake_img);
  nvgFill(s->vg);
}

static void ui_draw_vision_maxspeed(UIState *s) {

  // scc smoother
  cereal::CarControl::SccSmoother::Reader scc_smoother = s->scene.car_control.getSccSmoother();
  bool longControl = scc_smoother.getLongControl();
  float cruiseVirtualMaxSpeed = scc_smoother.getCruiseVirtualMaxSpeed();
  float cruiseRealMaxSpeed = scc_smoother.getCruiseRealMaxSpeed();

  bool is_cruise_set = (cruiseRealMaxSpeed > 0 && cruiseRealMaxSpeed < 255);

  const Rect rect = {s->viz_rect.x + (bdr_s * 2), int(s->viz_rect.y + (bdr_s * 1.5)), 184, 202};
  ui_fill_rect(s->vg, rect, COLOR_BLACK_ALPHA(100), 30.);
  ui_draw_rect(s->vg, rect, COLOR_WHITE_ALPHA(100), 10, 20.);

  nvgTextAlign(s->vg, NVG_ALIGN_CENTER | NVG_ALIGN_BASELINE);
  const int text_x = rect.centerX();

  if (is_cruise_set)
  {
    char str[256];

    if(!longControl && scc_smoother.getState() == 0)
    {
        ui_draw_text(s, text_x, 148, "STOCK", 25 * 2.5, COLOR_WHITE, "sans-semibold");
    }
    else
    {
        snprintf(str, sizeof(str), "%d", (int)(cruiseVirtualMaxSpeed + 0.5));
        ui_draw_text(s, text_x, 148, str, 33 * 2.5, COLOR_WHITE, "sans-semibold");
    }

    snprintf(str, sizeof(str), "%d", (int)(cruiseRealMaxSpeed + 0.5));
    ui_draw_text(s, text_x, 242, str, 48 * 2.5, COLOR_WHITE, "sans-bold");
  }
  else
  {
    if(!longControl && scc_smoother.getState() == 0)
        ui_draw_text(s, text_x, 148, "STOCK", 25 * 2.5, COLOR_WHITE, "sans-semibold");
    else
    {
        if(longControl)
            ui_draw_text(s, text_x, 148, "OP", 25 * 2.5, COLOR_WHITE_ALPHA(100), "sans-semibold");
        else
            ui_draw_text(s, text_x, 148, "SCC", 25 * 2.5, COLOR_WHITE_ALPHA(100), "sans-semibold");
    }

    ui_draw_text(s, text_x, 242, "N/A", 42 * 2.5, COLOR_WHITE_ALPHA(100), "sans-semibold");
  }

}

static void ui_draw_vision_speed(UIState *s) {
  const float speed = std::max(0.0, s->scene.controls_state.getCluSpeedMs() * (s->scene.is_metric ? 3.6 : 2.2369363));
  const std::string speed_str = std::to_string((int)std::nearbyint(speed));
  nvgTextAlign(s->vg, NVG_ALIGN_CENTER | NVG_ALIGN_BASELINE);
  ui_draw_text(s, s->viz_rect.centerX(), 240, speed_str.c_str(), 96 * 2.5, COLOR_WHITE, "sans-bold");
  ui_draw_text(s, s->viz_rect.centerX(), 320, s->scene.is_metric ? "km/h" : "mph", 36 * 2.5, COLOR_WHITE_ALPHA(200), "sans-regular");
}

static void ui_draw_vision_event(UIState *s) {
  if (s->scene.controls_state.getEngageable()) {
    // draw steering wheel
    const int radius = 96;
    const int center_x = s->viz_rect.right() - radius - bdr_s * 2;
    const int center_y = s->viz_rect.y + radius  + (bdr_s * 1.5);
    ui_draw_circle_image(s, center_x, center_y, radius, "wheel", bg_colors[s->status], 1.0f);
  }
}

static void ui_draw_vision_face(UIState *s) {
  const int radius = 96;
  const int center_x = s->viz_rect.x + radius + (bdr_s * 2);
  const int center_y = s->viz_rect.bottom() - footer_h / 2;
  ui_draw_circle_image(s, center_x, center_y, radius, "driver_face", s->scene.dmonitoring_state.getIsActiveMode());
}

static void ui_draw_driver_view(UIState *s) {
  s->sidebar_collapsed = true;
  const bool is_rhd = s->scene.is_rhd;
  const int width = 4 * s->viz_rect.h / 3;
  const Rect rect = {s->viz_rect.centerX() - width / 2, s->viz_rect.y, width, s->viz_rect.h};  // x, y, w, h
  const Rect valid_rect = {is_rhd ? rect.right() - rect.h / 2 : rect.x, rect.y, rect.h / 2, rect.h};

  // blackout
  const int blackout_x_r = valid_rect.right();
#ifndef QCOM2
  const int blackout_w_r = rect.right() - valid_rect.right();
  const int blackout_x_l = rect.x;
#else
  const int blackout_w_r = s->viz_rect.right() - valid_rect.right();
  const int blackout_x_l = s->viz_rect.x;
#endif
  const int blackout_w_l = valid_rect.x - blackout_x_l;
  ui_fill_rect(s->vg, {blackout_x_l, rect.y, blackout_w_l, rect.h}, COLOR_BLACK_ALPHA(144));
  ui_fill_rect(s->vg, {blackout_x_r, rect.y, blackout_w_r, rect.h}, COLOR_BLACK_ALPHA(144));

  const bool face_detected = s->scene.driver_state.getFaceProb() > 0.4;
  if (face_detected) {
    auto fxy_list = s->scene.driver_state.getFacePosition();
    float face_x = fxy_list[0];
    float face_y = fxy_list[1];
    int fbox_x = valid_rect.centerX() + (is_rhd ? face_x : -face_x) * valid_rect.w;
    int fbox_y = valid_rect.centerY() + face_y * valid_rect.h;

    float alpha = 0.2;
    if (face_x = std::abs(face_x), face_y = std::abs(face_y); face_x <= 0.35 && face_y <= 0.4)
      alpha = 0.8 - (face_x > face_y ? face_x : face_y) * 0.6 / 0.375;

    const int box_size = 0.6 * rect.h / 2;
    ui_draw_rect(s->vg, {fbox_x - box_size / 2, fbox_y - box_size / 2, box_size, box_size}, nvgRGBAf(1.0, 1.0, 1.0, alpha), 10, 35.);
  }

  // draw face icon
  const int face_radius = 85;
  const int center_x = is_rhd ? rect.right() - face_radius - bdr_s * 2 : rect.x + face_radius + bdr_s * 2;
  const int center_y = rect.bottom() - face_radius - bdr_s * 2.5;
  ui_draw_circle_image(s, center_x, center_y, face_radius, "driver_face", face_detected);
}

static void ui_draw_vision_header(UIState *s) {
  NVGpaint gradient = nvgLinearGradient(s->vg, s->viz_rect.x,
                        s->viz_rect.y+(header_h-(header_h/2.5)),
                        s->viz_rect.x, s->viz_rect.y+header_h,
                        nvgRGBAf(0,0,0,0.45), nvgRGBAf(0,0,0,0));

  ui_fill_rect(s->vg, {s->viz_rect.x, s->viz_rect.y, s->viz_rect.w, header_h}, gradient);

  ui_draw_vision_maxspeed(s);
  ui_draw_vision_speed(s);
  //ui_draw_vision_event(s);
  bb_ui_draw_UI(s);
  ui_draw_extras(s);
}

static void ui_draw_vision_footer(UIState *s) {
  ui_draw_vision_face(s);

#if UI_FEATURE_BRAKE
  ui_draw_vision_brake(s);
#endif
}

static float get_alert_alpha(float blink_rate) {
  return 0.375 * cos((millis_since_boot() / 1000) * 2 * M_PI * blink_rate) + 0.625;
}

static void ui_draw_vision_alert(UIState *s) {
  static std::map<cereal::ControlsState::AlertSize, const int> alert_size_map = {
      {cereal::ControlsState::AlertSize::SMALL, 241},
      {cereal::ControlsState::AlertSize::MID, 390},
      {cereal::ControlsState::AlertSize::FULL, s->fb_h}};
  const UIScene *scene = &s->scene;
  bool longAlert1 = scene->alert_text1.length() > 15;

  NVGcolor color = bg_colors[s->status];
  color.a *= get_alert_alpha(scene->alert_blinking_rate);
  const int alr_h = alert_size_map[scene->alert_size] + bdr_s;
  const Rect rect = {.x = s->viz_rect.x - bdr_s,
                     .y = s->fb_h - alr_h,
                     .w = s->viz_rect.w + (bdr_s * 2),
                     .h = alr_h};

  ui_fill_rect(s->vg, rect, color);
  ui_fill_rect(s->vg, rect, nvgLinearGradient(s->vg, rect.x, rect.y, rect.x, rect.bottom(), 
                                            nvgRGBAf(0.0, 0.0, 0.0, 0.05), nvgRGBAf(0.0, 0.0, 0.0, 0.35)));

  nvgFillColor(s->vg, COLOR_WHITE);
  nvgTextAlign(s->vg, NVG_ALIGN_CENTER | NVG_ALIGN_BASELINE);

  if (scene->alert_size == cereal::ControlsState::AlertSize::SMALL) {
    ui_draw_text(s, rect.centerX(), rect.centerY() + 15, scene->alert_text1.c_str(), 40*2.5, COLOR_WHITE, "sans-semibold");
  } else if (scene->alert_size == cereal::ControlsState::AlertSize::MID) {
    ui_draw_text(s, rect.centerX(), rect.centerY() - 45, scene->alert_text1.c_str(), 48*2.5, COLOR_WHITE, "sans-bold");
    ui_draw_text(s, rect.centerX(), rect.centerY() + 75, scene->alert_text2.c_str(), 36*2.5, COLOR_WHITE, "sans-regular");
  } else if (scene->alert_size == cereal::ControlsState::AlertSize::FULL) {
    nvgFontSize(s->vg, (longAlert1?72:96)*2.5);
    nvgFontFace(s->vg, "sans-bold");
    nvgTextAlign(s->vg, NVG_ALIGN_CENTER | NVG_ALIGN_MIDDLE);
    nvgTextBox(s->vg, rect.x, rect.y+(longAlert1?360:420), rect.w-60, scene->alert_text1.c_str(), NULL);
    nvgFontSize(s->vg, 48*2.5);
    nvgFontFace(s->vg,  "sans-regular");
    nvgTextAlign(s->vg, NVG_ALIGN_CENTER | NVG_ALIGN_BOTTOM);
    nvgTextBox(s->vg, rect.x, rect.h-(longAlert1?300:360), rect.w-60, scene->alert_text2.c_str(), NULL);
  }
}

static void ui_draw_vision_frame(UIState *s) {
  // Draw video frames
  glEnable(GL_SCISSOR_TEST);
  glViewport(s->video_rect.x, s->video_rect.y, s->video_rect.w, s->video_rect.h);
  glScissor(s->viz_rect.x, s->viz_rect.y, s->viz_rect.w, s->viz_rect.h);
  draw_frame(s);
  glDisable(GL_SCISSOR_TEST);

  glViewport(0, 0, s->fb_w, s->fb_h);
}

static void ui_draw_vision(UIState *s) {
  const UIScene *scene = &s->scene;
  if (!scene->driver_view) {
    // Draw augmented elements
    if (scene->world_objects_visible) {
      ui_draw_world(s);
    }
    // Set Speed, Current Speed, Status/Events
    ui_draw_vision_header(s);
    if (scene->alert_size == cereal::ControlsState::AlertSize::NONE) {
      ui_draw_vision_footer(s);
    }
  } else {
    ui_draw_driver_view(s);
  }
}

static void ui_draw_background(UIState *s) {
  const NVGcolor color = bg_colors[s->status];
  glClearColor(color.r, color.g, color.b, 1.0);
  glClear(GL_STENCIL_BUFFER_BIT | GL_COLOR_BUFFER_BIT);
}

void ui_draw(UIState *s) {
  s->viz_rect = Rect{bdr_s, bdr_s, s->fb_w - 2 * bdr_s, s->fb_h - 2 * bdr_s};
  if (!s->sidebar_collapsed) {
    s->viz_rect.x += sbr_w;
    s->viz_rect.w -= sbr_w;
  }

  const bool draw_alerts = s->scene.started;
  const bool draw_vision = draw_alerts && s->vipc_client->connected;

  // GL drawing functions
  ui_draw_background(s);
  if (draw_vision) {
    ui_draw_vision_frame(s);
  }
  glEnable(GL_BLEND);
  glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA);
  glViewport(0, 0, s->fb_w, s->fb_h);

  // NVG drawing functions - should be no GL inside NVG frame
  nvgBeginFrame(s->vg, s->fb_w, s->fb_h, 1.0f);
  ui_draw_sidebar(s);
  if (draw_vision) {
    ui_draw_vision(s);
  }

  if (draw_alerts && s->scene.alert_size != cereal::ControlsState::AlertSize::NONE) {
    ui_draw_vision_alert(s);
  }

  if (s->scene.driver_view && !s->vipc_client->connected) {
    nvgTextAlign(s->vg, NVG_ALIGN_CENTER | NVG_ALIGN_MIDDLE);
    ui_draw_text(s, s->viz_rect.centerX(), s->viz_rect.centerY(), "Please wait for camera to start", 40 * 2.5, COLOR_WHITE, "sans-bold");
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
  "}\n";

static const mat4 device_transform = {{
  1.0,  0.0, 0.0, 0.0,
  0.0,  1.0, 0.0, 0.0,
  0.0,  0.0, 1.0, 0.0,
  0.0,  0.0, 0.0, 1.0,
}};

static const float driver_view_ratio = 1.333;
#ifndef QCOM2
// frame from 4/3 to 16/9 display
static const mat4 driver_view_transform = {{
  driver_view_ratio*(1080-2*bdr_s)/(1920-2*bdr_s),  0.0, 0.0, 0.0,
  0.0,  1.0, 0.0, 0.0,
  0.0,  0.0, 1.0, 0.0,
  0.0,  0.0, 0.0, 1.0,
}};
#else
// from dmonitoring.cc
static const int full_width_tici = 1928;
static const int full_height_tici = 1208;
static const int adapt_width_tici = 668;
static const int crop_x_offset = 32;
static const int crop_y_offset = -196;
static const float yscale = full_height_tici * driver_view_ratio / adapt_width_tici;
static const float xscale = yscale*(1080-2*bdr_s)/(2160-2*bdr_s)*full_width_tici/full_height_tici;

static const mat4 driver_view_transform = {{
  xscale,  0.0, 0.0, xscale*crop_x_offset/full_width_tici*2,
  0.0,  yscale, 0.0, yscale*crop_y_offset/full_height_tici*2,
  0.0,  0.0, 1.0, 0.0,
  0.0,  0.0, 0.0, 1.0,
}};
#endif

void ui_nvg_init(UIState *s) {
  // init drawing
#ifdef QCOM
  // on QCOM, we enable MSAA
  s->vg = nvgCreate(0);
#else
  s->vg = nvgCreate(NVG_ANTIALIAS | NVG_STENCIL_STROKES | NVG_DEBUG);
#endif
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
      {"trafficSign_turn", "../assets/img_trafficSign_turn.png"},
      {"driver_face", "../assets/img_driver_face.png"},
      {"button_settings", "../assets/images/button_settings.png"},
      {"button_home", "../assets/images/button_home.png"},
      {"battery", "../assets/images/battery.png"},
      {"battery_charging", "../assets/images/battery_charging.png"},
      {"network_0", "../assets/images/network_0.png"},
      {"network_1", "../assets/images/network_1.png"},
      {"network_2", "../assets/images/network_2.png"},
      {"network_3", "../assets/images/network_3.png"},
      {"network_4", "../assets/images/network_4.png"},
      {"network_5", "../assets/images/network_5.png"},
	  {"brake", "../assets/img_brake_disc.png"},
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

  for (int i = 0; i < 2; i++) {
    float x1, x2, y1, y2;
    if (i == 1) {
      // flip horizontally so it looks like a mirror
      x1 = 0.0;
      x2 = 1.0;
      y1 = 1.0;
      y2 = 0.0;
    } else {
      x1 = 1.0;
      x2 = 0.0;
      y1 = 1.0;
      y2 = 0.0;
    }
    const uint8_t frame_indicies[] = {0, 1, 2, 0, 2, 3};
    const float frame_coords[4][4] = {
      {-1.0, -1.0, x2, y1}, //bl
      {-1.0,  1.0, x2, y2}, //tl
      { 1.0,  1.0, x1, y2}, //tr
      { 1.0, -1.0, x1, y1}, //br
    };

    glGenVertexArrays(1, &s->frame_vao[i]);
    glBindVertexArray(s->frame_vao[i]);
    glGenBuffers(1, &s->frame_vbo[i]);
    glBindBuffer(GL_ARRAY_BUFFER, s->frame_vbo[i]);
    glBufferData(GL_ARRAY_BUFFER, sizeof(frame_coords), frame_coords, GL_STATIC_DRAW);
    glEnableVertexAttribArray(frame_pos_loc);
    glVertexAttribPointer(frame_pos_loc, 2, GL_FLOAT, GL_FALSE,
                          sizeof(frame_coords[0]), (const void *)0);
    glEnableVertexAttribArray(frame_texcoord_loc);
    glVertexAttribPointer(frame_texcoord_loc, 2, GL_FLOAT, GL_FALSE,
                          sizeof(frame_coords[0]), (const void *)(sizeof(float) * 2));
    glGenBuffers(1, &s->frame_ibo[i]);
    glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, s->frame_ibo[i]);
    glBufferData(GL_ELEMENT_ARRAY_BUFFER, sizeof(frame_indicies), frame_indicies, GL_STATIC_DRAW);
    glBindBuffer(GL_ARRAY_BUFFER, 0);
    glBindVertexArray(0);
  }

  s->video_rect = Rect{bdr_s, bdr_s, s->fb_w - 2 * bdr_s, s->fb_h - 2 * bdr_s};
  float zx = zoom * 2 * fcam_intrinsic_matrix.v[2] / s->video_rect.w;
  float zy = zoom * 2 * fcam_intrinsic_matrix.v[5] / s->video_rect.h;

  const mat4 frame_transform = {{
    zx, 0.0, 0.0, 0.0,
    0.0, zy, 0.0, -y_offset / s->video_rect.h * 2,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
  }};

  s->front_frame_mat = matmul(device_transform, driver_view_transform);
  s->rear_frame_mat = matmul(device_transform, frame_transform);

  // Apply transformation such that video pixel coordinates match video
  // 1) Put (0, 0) in the middle of the video
  nvgTranslate(s->vg, s->video_rect.x + s->video_rect.w / 2, s->video_rect.y + s->video_rect.h / 2 + y_offset);

  // 2) Apply same scaling as video
  nvgScale(s->vg, zoom, zoom);

  // 3) Put (0, 0) in top left corner of video
  nvgTranslate(s->vg, -fcam_intrinsic_matrix.v[2], -fcam_intrinsic_matrix.v[5]);

  nvgCurrentTransform(s->vg, s->car_space_transform);
  nvgResetTransform(s->vg);
}
