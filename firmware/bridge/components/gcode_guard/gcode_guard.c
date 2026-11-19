#include "gcode_guard.h"

#include <ctype.h>
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
    if (toupper((unsigned char)*line) != letter || !isdigit((unsigned char)line[1])) {
        return -1;
    }
    int code = 0;
    line++;
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
    if ((mc == 104 || mc == 109 || mc == 140 || mc == 190) && gcode_heater_target(rest, &target) &&
        !(target <= 0)) {  // also refuses "S nan"
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
