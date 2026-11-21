#include "gcode_guard.h"

#include <ctype.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>

static const char AXES[3] = {'X', 'Y', 'Z'};

int gcode_command_code(const char *line, char letter, const char **rest)
{
    while (*line == ' ') {
        line++;
    }
    if (toupper((unsigned char)*line) == 'N' && isdigit((unsigned char)line[1])) {  // line number
        line++;
        while (isdigit((unsigned char)*line)) {
            line++;
        }
        while (*line == ' ') {
            line++;
        }
    }
    if (toupper((unsigned char)*line) != letter) {
        return -1;
    }
    line++;
    while (*line == ' ') {  // Marlin skips them too: "M 104 S200" is M104
        line++;
    }
    if (!isdigit((unsigned char)*line)) {
        return -1;
    }
    int code = 0;
    while (isdigit((unsigned char)*line)) {
        code = code * 10 + (*line++ - '0');
    }
    if (*line == '.') {
        return -1;  // e.g. "G28.1"
    }
    if (rest) {
        *rest = line;
    }
    return code;
}

bool gcode_num_param(const char *rest, char letter, float *out)
{
    char prev = ' ';
    for (const char *p = rest; *p && *p != ';' && *p != '*'; prev = *p++) {
        if (toupper((unsigned char)*p) == letter && !isalpha((unsigned char)prev)) {
            char *end;
            float v = strtof(p + 1, &end);
            if (end != p + 1) {
                *out = v;
                return true;
            }
        }
    }
    return false;
}

bool gcode_heater_target(const char *rest, float *out)
{
    return gcode_num_param(rest, 'S', out) || gcode_num_param(rest, 'R', out);
}

int gcode_emergency_code(const char *line)
{
    int m = gcode_command_code(line, 'M', NULL);
    return m == 112 || m == 108 || m == 410 ? m : 0;
}

float gcode_machine(const gcode_axis_t *a, float l)
{
    return a->dir * (l - a->zero);
}

void gcode_unhome(gcode_motion_t *m)
{
    for (int i = 0; i < 3; i++) {
        m->ax[i].homed = false;
    }
    m->home_mask = 0;
}

const char *gcode_refusal(const gcode_motion_t *m, const float max_mm[3], const char *line,
                          char *buf, size_t buf_len)
{
    const char *rest = NULL;
    int g = gcode_command_code(line, 'G', &rest);
    int mc = gcode_command_code(line, 'M', g < 0 ? &rest : NULL);
    float target;

    // Laser build: heaters and sensors are removed (road_Change.md ch. 1). Marlin kill()s
    // (MINTEMP / heating failed) as soon as a heater target is above 0, so none ever goes out.
    // Target 0 (off) is harmless and still passes: soft STOP sends it.
    // "I<n>" heats to material preset n (HAS_PREHEAT) and wins over S: refused whatever S says.
    bool heater = mc == 104 || mc == 109 || mc == 140 || mc == 190;
    if (heater && (gcode_num_param(rest, 'I', &target) ||
                   (gcode_heater_target(rest, &target) && !(target <= 0)))) {  // also "S nan"
        return "Error:bridge: laser build, no heaters: heating refused (S0 / off works)";
    }
    if (mc == 303) {
        return "Error:bridge: laser build, no heaters: PID autotune refused";
    }
    // Probing preheats nozzle 180 / bed 30 first (PREHEAT_BEFORE_PROBING): MINTEMP kill.
    // G28 homes Z with the probe, so it does the same after moving to the bed center, and
    // the Zortrax G28 homes Z even for "G28 R0 X Y". So no G28 at all: the web Home seeks
    // the X/Y endstops with plain moves (road_Change.md ch. 3).
    if (g == 29 || g == 30 || mc == 48) {
        return "Error:bridge: laser build, probing refused (its preheat stops Marlin)";
    }
    if (g == 28) {
        return "Error:bridge: laser build, G28 refused (its Z preheat stops Marlin). Use the Home button";
    }
    // Homed axes stay within 0..max of the switch the bridge's home found (Marlin has no soft
    // limits: it is never homed). Arcs: only the end point is checked.
    if (g >= 0 && g <= 3) {
        for (int i = 0; i < 3; i++) {
            const gcode_axis_t *a = &m->ax[i];
            float v;
            if (!a->homed || !gcode_num_param(rest, AXES[i], &v)) {
                continue;
            }
            float m_to = gcode_machine(a, m->relative ? a->l + v : v);
            if (!(m_to >= -0.001f && m_to <= max_mm[i] + 0.001f)) {  // also refuses "Xnan"
                snprintf(buf, buf_len, "Error:bridge: %c would go to %.2f mm from home, limit 0..%g",
                         AXES[i], (double)m_to, (double)max_mm[i]);
                return buf;
            }
        }
    }
    return NULL;
}

void gcode_track(gcode_motion_t *m, const char *line, float z_dir)
{
    const char *rest = NULL;
    int g = gcode_command_code(line, 'G', &rest);
    if (g == 90 || g == 91) {
        m->relative = g == 91;
        return;
    }
    if (g != 92 && (g < 0 || g > 3)) {
        return;
    }
    for (int i = 0; i < 3; i++) {
        gcode_axis_t *a = &m->ax[i];
        float v;
        if (!gcode_num_param(rest, AXES[i], &v)) {
            continue;
        }
        if (g == 92 && (m->home_mask & (1u << i))) {  // end of a home: the switch is 0
            m->home_mask &= ~(1u << i);
            a->homed = true;
            a->dir = i == 2 ? -z_dir : 1;
            a->l = a->zero = v;
        } else if (!a->homed) {
            continue;
        } else if (g == 92) {  // same place, new number
            a->zero += v - a->l;
            a->l = v;
        } else {
            a->l = m->relative ? a->l + v : v;
        }
    }
}

// ---------------------------------------------------------------- laser

int gcode_laser_pwm(const gcode_laser_cfg_t *c, float s)
{
    if (!(s > 0) || !(c->s_max > 0) || !(c->cap > 0)) {
        return 0;
    }
    float share = s >= c->s_max ? 1.0f : s / c->s_max;  // GRBL clamps S above its max
    float cap = c->cap > 1 ? 1.0f : c->cap;
    long v = lroundf(share * cap * 255.0f);
    return v < 0 ? 0 : v > 255 ? 255 : (int)v;
}

static const char *const LASER_BAD_S = "Error:bridge: laser: S must be a number, 0 or more";

// An S word, if there is one: false + *bad when it is negative or NaN.
static bool laser_s(const char *rest, float *s, bool *bad)
{
    float v;
    if (!gcode_num_param(rest, 'S', &v)) {
        return false;
    }
    if (!(v >= 0)) {
        *bad = true;
        return false;
    }
    *s = v;
    return true;
}

const char *gcode_laser_plan(const gcode_laser_t *l, const gcode_laser_cfg_t *c, bool armed,
                             const char *line, gcode_laser_step_t *out)
{
    const char *rest = NULL;
    int g = gcode_command_code(line, 'G', &rest);
    int mc = g < 0 ? gcode_command_code(line, 'M', &rest) : -1;
    bool bad = false;
    float v;

    out->pre_pwm = -1;
    out->send = true;
    out->next = *l;
    gcode_laser_t *n = &out->next;

    if (mc == 3 || mc == 4) {
        laser_s(rest, &n->s, &bad);
        if (bad) {
            return LASER_BAD_S;
        }
        if (!armed && gcode_laser_pwm(c, n->s) > 0) {
            return "Error:bridge: laser not armed: arm it on the page first";
        }
        n->on = true;
        out->send = false;  // fires with the next G1/G2/G3
        return NULL;
    }
    if (mc == 5) {
        n->on = false;
        out->send = false;
        if (l->pwm != 0) {
            out->pre_pwm = n->pwm = 0;
        }
        return NULL;
    }
    if (mc == 106 || mc == 107) {
        float p = 0;
        gcode_num_param(rest, 'P', &p);  // no P: fan 0, as in Marlin
        if (!(p == (float)c->fan)) {
            return NULL;  // another fan
        }
        if (mc == 106 && !(gcode_num_param(rest, 'S', &v) && v <= 0)) {
            return "Error:bridge: that fan output is the laser: use M3 S<power> / M5, not M106";
        }
        n->on = false;
        n->pwm = 0;
        return NULL;
    }
    if (g < 0 && mc < 0) {
        // A lone "S500" (GRBL: S is modal) sets the power for the next moves.
        const char *p = line;
        while (*p == ' ') {
            p++;
        }
        if (toupper((unsigned char)*p) == 'S' && laser_s(p, &n->s, &bad)) {
            out->send = false;
        }
        return bad ? LASER_BAD_S : NULL;
    }

    int target = -1;
    if (g >= 0 && g <= 3) {
        laser_s(rest, &n->s, &bad);  // on G0 too: S is modal
        if (bad) {
            return LASER_BAD_S;
        }
        target = g == 0 || !armed || !n->on ? 0 : gcode_laser_pwm(c, n->s);
    } else if (g == 4) {
        target = 0;  // standing still (G4 S is seconds there, not power)
    }
    if (target >= 0 && target != l->pwm) {
        out->pre_pwm = n->pwm = target;
    }
    return NULL;
}

int gcode_laser_cost(const char *line)
{
    int g = gcode_command_code(line, 'G', NULL);
    return (g >= 0 && g <= 4) || gcode_command_code(line, 'M', NULL) == 5 ? 2 : 1;
}
