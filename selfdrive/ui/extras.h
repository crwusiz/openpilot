#include <time.h>
#include <dirent.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <cmath>

#pragma once

#include <vector>

class ATextItem {
  public:
    std::string text;
    int alpha;

    ATextItem(const char* text, int alpha) {
      this->text = text;
      this->alpha = alpha;
    }
};

class AText {
  private:
    NVGcolor color;
    std::string font_name;

    std::vector<ATextItem> after_items;
    std::string last_text;

  public:

    AText(NVGcolor color, const char *font_name) {
      this->color = color;
      this->font_name = font_name;
    }

    void update(const UIState *s, float x, float y, const char *string, int size) {
      if(last_text != string) {
        after_items.insert(after_items.begin(), ATextItem(string, 255));
        last_text = string;
      }

      for(auto it = after_items.begin() + 1; it != after_items.end();)  {
        it->alpha -= 1000 / UI_FREQ;
        if(it->alpha <= 0)
          it = after_items.erase(it);
        else
          it++;
      }

      nvgTextAlign(s->vg, NVG_ALIGN_CENTER | NVG_ALIGN_BASELINE);

      for(auto it = after_items.rbegin(); it != after_items.rend(); ++it)  {
        NVGcolor color = this->color;
        color.a = it->alpha/255.f;
        ui_draw_text(s, x, y, it->text.c_str(), size, color, this->font_name.c_str());
      }
    }

    void ui_draw_text(const UIState *s, float x, float y, const char *string, float size, NVGcolor color, const char *font_name) {
      nvgFontFace(s->vg, font_name);
      nvgFontSize(s->vg, size);
      nvgFillColor(s->vg, color);
      nvgText(s->vg, x, y, string, NULL);
    }
};


static void ui_draw_extras_limit_speed(UIState *s) {
    const UIScene *scene = &s->scene;
    cereal::CarControl::SccSmoother::Reader scc_smoother = scene->car_control.getSccSmoother();
    int activeNDA = scc_smoother.getRoadLimitSpeedActive();
    int limit_speed = scc_smoother.getRoadLimitSpeed();
    int left_dist = scc_smoother.getRoadLimitSpeedLeftDist();

    if(activeNDA > 0) {
        int w = 120;
        int h = 54;
        int x = (s->fb_w + (bdr_s*2))/2 - w/2 - bdr_s;
        int y = 40 - bdr_s;

        const char* img = activeNDA == 1 ? "img_nda" : "img_hda";
        ui_draw_image(s, {x, y, w, h}, img, 1.f);
    }

    if(limit_speed > 10 && left_dist > 0) {
        int w = s->fb_w / 10;
        int h = s->fb_w / 10;
        int x = (bdr_s*2) + 200 + s->fb_w / 25;
        int y = 30;
        char str[32];

        nvgBeginPath(s->vg);
        nvgRoundedRect(s->vg, x, y, w, h, s->fb_w / 9);
        nvgStrokeColor(s->vg, nvgRGBA(255, 0, 0, 200));
        nvgStrokeWidth(s->vg, s->fb_w / 72);
        nvgStroke(s->vg);

        NVGcolor fillColor = nvgRGBA(0, 0, 0, 50);
        nvgFillColor(s->vg, fillColor);
        nvgFill(s->vg);

        nvgFillColor(s->vg, nvgRGBA(255, 255, 255, 250));

        nvgFontSize(s->vg, s->fb_w / 15);
        nvgFontFace(s->vg, "sans-bold");
        nvgTextAlign(s->vg, NVG_ALIGN_CENTER | NVG_ALIGN_MIDDLE);

        snprintf(str, sizeof(str), "%d", limit_speed);
        nvgText(s->vg, x+w/2, y+h/2, str, NULL);

        nvgFontSize(s->vg, s->fb_w / 18);

        if(left_dist >= 1000)
            snprintf(str, sizeof(str), "%.1fkm", left_dist / 1000.f);
        else
            snprintf(str, sizeof(str), "%dm", left_dist);

        nvgText(s->vg, x+w/2, y+h + 70, str, NULL);
    } else {
        auto controls_state = (*s->sm)["controlsState"].getControlsState();
        int sccStockCamAct = (int)controls_state.getSccStockCamAct();
        int sccStockCamStatus = (int)controls_state.getSccStockCamStatus();

        if(sccStockCamAct == 2 && sccStockCamStatus == 2) {
            int w = s->fb_w / 10;
            int h = s->fb_w / 10;
            int x = (bdr_s*2) + 200 + s->fb_w / 25;
            int y = 30;
            char str[32];

            nvgBeginPath(s->vg);
            nvgRoundedRect(s->vg, x, y, w, h, s->fb_w / 9);
            nvgStrokeColor(s->vg, nvgRGBA(255, 0, 0, 200));
            nvgStrokeWidth(s->vg, s->fb_w / 72);
            nvgStroke(s->vg);

            NVGcolor fillColor = nvgRGBA(0, 0, 0, 50);
            nvgFillColor(s->vg, fillColor);
            nvgFill(s->vg);

            nvgFillColor(s->vg, nvgRGBA(255, 255, 255, 250));

            nvgFontSize(s->vg, s->fb_w / 15);
            nvgFontFace(s->vg, "sans-bold");
            nvgTextAlign(s->vg, NVG_ALIGN_CENTER | NVG_ALIGN_MIDDLE);

            nvgText(s->vg, x+w/2, y+h/2, "CAM", NULL);
        }
    }
}

static void ui_draw_extras(UIState *s) {
    ui_draw_extras_limit_speed(s);
}