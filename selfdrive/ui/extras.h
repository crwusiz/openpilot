#include <time.h>
#include <dirent.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <cmath>

static void ui_draw_extras_limit_speed(UIState *s)
{
    const UIScene *scene = &s->scene;
    cereal::CarControl::SccSmoother::Reader scc_smoother = scene->car_control.getSccSmoother();
    int activeNDA = scc_smoother.getRoadLimitSpeedActive();
    int limit_speed = scc_smoother.getRoadLimitSpeed();
    int left_dist = scc_smoother.getRoadLimitSpeedLeftDist();

    if(activeNDA > 0)
    {
        int w = 120;
        int h = 54;
        int x = (s->fb_w + (bdr_s*2))/2 - w/2 - bdr_s;
        int y = 40 - bdr_s;

        const char* img = activeNDA == 1 ? "img_nda" : "img_hda";
        ui_draw_image(s, {x, y, w, h}, img, 1.f);
    }

    if(limit_speed > 10 && left_dist > 0)
    {
        int w = 200;
        int h = 200;
        int x = (bdr_s*2) + 300;
        int y = 80;
        char str[32];

        nvgBeginPath(s->vg);
        nvgRoundedRect(s->vg, x, y, w, h, 210);
        nvgStrokeColor(s->vg, nvgRGBA(255, 0, 0, 200));
        nvgStrokeWidth(s->vg, 30);
        nvgStroke(s->vg);

        NVGcolor fillColor = nvgRGBA(0, 0, 0, 50);
        nvgFillColor(s->vg, fillColor);
        nvgFill(s->vg);

        nvgFillColor(s->vg, nvgRGBA(255, 255, 255, 250));

        nvgFontSize(s->vg, 140);
        nvgFontFace(s->vg, "sans-bold");
        nvgTextAlign(s->vg, NVG_ALIGN_CENTER | NVG_ALIGN_MIDDLE);

        snprintf(str, sizeof(str), "%d", limit_speed);
        nvgText(s->vg, x+w/2, y+h/2, str, NULL);

        nvgFontSize(s->vg, 120);

        if(left_dist >= 1000)
            snprintf(str, sizeof(str), "%.1fkm", left_dist / 1000.f);
        else
            snprintf(str, sizeof(str), "%dm", left_dist);

        nvgText(s->vg, x+w/2, y+h + 70, str, NULL);
    }
    else
    {
        auto controls_state = (*s->sm)["controlsState"].getControlsState();
        int sccStockCamAct = (int)controls_state.getSccStockCamAct();
        int sccStockCamStatus = (int)controls_state.getSccStockCamStatus();

        if(sccStockCamAct == 2 && sccStockCamStatus == 2)
        {
            int w = 200;
            int h = 200;
            int x = (bdr_s*2) + 300;
            int y = 80;
            char str[32];

            nvgBeginPath(s->vg);
            nvgRoundedRect(s->vg, x, y, w, h, 210);
            nvgStrokeColor(s->vg, nvgRGBA(255, 0, 0, 200));
            nvgStrokeWidth(s->vg, 30);
            nvgStroke(s->vg);

            NVGcolor fillColor = nvgRGBA(0, 0, 0, 50);
            nvgFillColor(s->vg, fillColor);
            nvgFill(s->vg);

            nvgFillColor(s->vg, nvgRGBA(255, 255, 255, 250));

            nvgFontSize(s->vg, 140);
            nvgFontFace(s->vg, "sans-bold");
            nvgTextAlign(s->vg, NVG_ALIGN_CENTER | NVG_ALIGN_MIDDLE);

            nvgText(s->vg, x+w/2, y+h/2, "CAM", NULL);
        }
    }
}

static NVGcolor get_tpms_color(float tpms) {
    if(tpms < 5 || tpms > 60) // N/A
        return nvgRGBA(255, 255, 255, 200);
    if(tpms < 30)
        return nvgRGBA(255, 90, 90, 200);
    return nvgRGBA(255, 255, 255, 200);
}

static std::string get_tpms_text(float tpms) {
    if(tpms < 5 || tpms > 60)
        return "";

    char str[32];
    snprintf(str, sizeof(str), "%.0f", round(tpms));
    return std::string(str);
}

static void ui_draw_extras_tire_pressure(UIState *s)
{
    const UIScene *scene = &s->scene;
    auto car_state = (*s->sm)["carState"].getCarState();
    auto tpms = car_state.getTpms();

    const float fl = tpms.getFl();
    const float fr = tpms.getFr();
    const float rl = tpms.getRl();
    const float rr = tpms.getRr();

    const int w = 58;
    const int h = 126;
    int x = bdr_s + 80;
    int y = s->fb_h - bdr_s - h - 60;

    const int margin = 10;

    nvgBeginPath(s->vg);
    ui_draw_image(s, {x, y, w, h}, "tire_pressure", 0.8f);

    nvgFontSize(s->vg, 60);
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

static void ui_draw_extras(UIState *s)
{
    ui_draw_extras_limit_speed(s);
    ui_draw_extras_tire_pressure(s);
}