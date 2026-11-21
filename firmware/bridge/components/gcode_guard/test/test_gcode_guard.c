// Host test for gcode_guard (no ESP-IDF needed). From the repo root:  sh tools/run_host_tests.sh
// or from this folder:
//   gcc -std=c11 -Wall -I../include ../gcode_guard.c test_gcode_guard.c -lm -o test_guard && ./test_guard
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "gcode_guard.h"

static int failures = 0;
#define CHECK(cond)                                                \
    do {                                                           \
        if (!(cond)) {                                             \
            printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
            failures++;                                            \
        }                                                          \
    } while (0)
#define NEAR(a, b) (fabsf((a) - (b)) < 0.01f)

static const float MAX[3] = {170, 170, 170};
static char buf[96];

static const char *refuse(const gcode_motion_t *m, const char *line)
{
    return gcode_refusal(m, MAX, line, buf, sizeof(buf));
}

// The bridge's home: a G92 with the axis in home_mask, as printer_state's home tasks send it.
static void home_all(gcode_motion_t *m, float z_dir)
{
    memset(m, 0, sizeof(*m));
    m->home_mask = 0x7;
    gcode_track(m, "G92 X0 Y0 Z0", z_dir);
}

static void test_command_code(void)
{
    const char *rest = NULL;
    CHECK(gcode_command_code("G28", 'G', &rest) == 28);
    CHECK(gcode_command_code("  g1 X10", 'G', &rest) == 1 && strcmp(rest, " X10") == 0);
    CHECK(gcode_command_code("N12 G28 X", 'G', NULL) == 28);
    CHECK(gcode_command_code("N12G28X", 'G', &rest) == 28 && strcmp(rest, "X") == 0);
    CHECK(gcode_command_code("M104S200", 'M', &rest) == 104 && strcmp(rest, "S200") == 0);
    CHECK(gcode_command_code("G28.1", 'G', NULL) == -1);
    CHECK(gcode_command_code("M104", 'G', NULL) == -1);
    CHECK(gcode_command_code("G", 'G', NULL) == -1);
    CHECK(gcode_command_code("", 'G', NULL) == -1);
    CHECK(gcode_command_code("N", 'G', NULL) == -1);
    CHECK(gcode_command_code("M 104 S200", 'M', &rest) == 104 && strcmp(rest, " S200") == 0);
    CHECK(gcode_command_code("N5 G  28", 'G', NULL) == 28);
    CHECK(gcode_command_code("G ", 'G', NULL) == -1);
    CHECK(gcode_command_code("G X10", 'G', NULL) == -1);
}

static void test_num_param(void)
{
    float v = 0;
    CHECK(gcode_num_param(" X10 Y20", 'Y', &v) && NEAR(v, 20));
    CHECK(gcode_num_param("X10Y200", 'Y', &v) && NEAR(v, 200));
    CHECK(gcode_num_param(" x-5.5", 'X', &v) && NEAR(v, -5.5f));
    CHECK(!gcode_num_param(" X10 ; Y20", 'Y', &v));   // comment
    CHECK(!gcode_num_param(" X10*57", 'Y', &v));      // checksum
    CHECK(!gcode_num_param(" X", 'X', &v));           // no number
    CHECK(!gcode_num_param(" AX10", 'X', &v));        // X inside another word
    CHECK(gcode_heater_target(" R200", &v) && NEAR(v, 200));
}

static void test_emergency_code(void)
{
    CHECK(gcode_emergency_code("M112") == 112);
    CHECK(gcode_emergency_code("  m410") == 410);
    CHECK(gcode_emergency_code("M 108") == 108);
    CHECK(gcode_emergency_code("N5 M 112") == 112);
    CHECK(gcode_emergency_code("M112 ; stop") == 112);
    CHECK(gcode_emergency_code("M1120") == 0);
    CHECK(gcode_emergency_code("M11") == 0);
    CHECK(gcode_emergency_code("G112") == 0);
    CHECK(gcode_emergency_code("M112.1") == 0);
    CHECK(gcode_emergency_code("") == 0);
}

static void test_heat_refused(void)
{
    gcode_motion_t m = {0};
    CHECK(refuse(&m, "M104 S200") != NULL);
    CHECK(refuse(&m, "M104S200") != NULL);
    CHECK(refuse(&m, "N5M104S200") != NULL);
    CHECK(refuse(&m, "M109 R180") != NULL);
    CHECK(refuse(&m, "M140 S30") != NULL);
    CHECK(refuse(&m, "m190 s60") != NULL);
    CHECK(refuse(&m, "M104 Snan") != NULL);
    CHECK(refuse(&m, "M303 E0 S200 C8") != NULL);
    // space between letter and number: Marlin reads these as M104 / M140
    CHECK(refuse(&m, "M 104 S200") != NULL);
    CHECK(refuse(&m, "M  140 S30") != NULL);
    CHECK(refuse(&m, "N7 M 109 R180") != NULL);
    // material preset (I wins over S in Marlin)
    CHECK(refuse(&m, "M104 I0") != NULL);
    CHECK(refuse(&m, "M140 I1") != NULL);
    CHECK(refuse(&m, "M190 S0 I0") != NULL);
    CHECK(refuse(&m, "M109I0") != NULL);
    // off still passes: soft STOP sends it
    CHECK(refuse(&m, "M104 S0") == NULL);
    CHECK(refuse(&m, "M140 S0") == NULL);
    CHECK(refuse(&m, "M104") == NULL);  // no target: Marlin keeps the current one (0)
    CHECK(refuse(&m, "M105") == NULL);
}

static void test_probing_refused(void)
{
    gcode_motion_t m = {0};
    CHECK(refuse(&m, "G28") != NULL);
    CHECK(refuse(&m, "G28 X Y") != NULL);
    CHECK(refuse(&m, "G28X") != NULL);
    CHECK(refuse(&m, "N12G28") != NULL);
    CHECK(refuse(&m, "g28 r0 x y") != NULL);
    CHECK(refuse(&m, "G29") != NULL);
    CHECK(refuse(&m, "G30") != NULL);
    CHECK(refuse(&m, "M48") != NULL);
    CHECK(refuse(&m, "G 28") != NULL);
    CHECK(refuse(&m, "G 29") != NULL);
    CHECK(refuse(&m, "M 48") != NULL);
    CHECK(refuse(&m, "G280") == NULL);  // a different code
    CHECK(refuse(&m, "M119") == NULL);
}

static void test_limits(void)
{
    gcode_motion_t m;
    home_all(&m, -1);
    CHECK(m.ax[0].homed && m.ax[1].homed && m.ax[2].homed && m.home_mask == 0);

    CHECK(refuse(&m, "G1 X170 Y0") == NULL);
    CHECK(refuse(&m, "G1 X170.5") != NULL);
    CHECK(refuse(&m, "G1 X-1") != NULL);
    CHECK(refuse(&m, "G1X10Y200") != NULL);   // second word without spaces
    CHECK(refuse(&m, "G0 Xnan") != NULL);
    CHECK(refuse(&m, "G2 X200 Y10 I5 J5") != NULL);  // arc: end point checked
    CHECK(refuse(&m, "G1 X10 ; X999") == NULL);      // comment ignored

    // relative mode: limits count from the tracked position
    gcode_track(&m, "G1 X100", -1);
    gcode_track(&m, "G91", -1);
    CHECK(m.relative);
    CHECK(refuse(&m, "G1 X70") == NULL);
    CHECK(refuse(&m, "G1 X71") != NULL);
    CHECK(refuse(&m, "G1 X-100") == NULL);
    CHECK(refuse(&m, "G1 X-101") != NULL);
    gcode_track(&m, "G1 X50", -1);
    CHECK(NEAR(m.ax[0].l, 150));
    gcode_track(&m, "G90", -1);
    CHECK(!m.relative);

    // a G92 after the home moves Marlin's numbers, not the limits
    gcode_track(&m, "G92 X0", -1);  // at machine 150
    CHECK(NEAR(gcode_machine(&m.ax[0], m.ax[0].l), 150));
    CHECK(refuse(&m, "G1 X20") == NULL);    // machine 170
    CHECK(refuse(&m, "G1 X21") != NULL);    // machine 171
    CHECK(refuse(&m, "G1 X-150") == NULL);  // machine 0
    CHECK(refuse(&m, "G1 X-151") != NULL);
}

static void test_not_homed(void)
{
    gcode_motion_t m = {0};
    CHECK(refuse(&m, "G1 X999") == NULL);  // "move without homing": no limits
    gcode_track(&m, "G1 X999", -1);
    CHECK(!m.ax[0].homed);

    home_all(&m, -1);
    gcode_unhome(&m);
    CHECK(!m.ax[0].homed && !m.ax[1].homed && !m.ax[2].homed);
    CHECK(refuse(&m, "G1 X999") == NULL);

    // a plain G92 (no home running) doesn't mark anything homed
    memset(&m, 0, sizeof(m));
    gcode_track(&m, "G92 X0 Y0 Z0", -1);
    CHECK(!m.ax[0].homed && !m.ax[1].homed && !m.ax[2].homed);
}

static void test_z_dir(void)
{
    // z_dir +1: the switch is at +Z, Marlin's numbers shrink away from it
    gcode_motion_t m;
    home_all(&m, 1);
    CHECK(NEAR(m.ax[2].dir, -1) && NEAR(m.ax[0].dir, 1));
    CHECK(refuse(&m, "G1 Z-10") == NULL);   // machine 10
    CHECK(refuse(&m, "G1 Z10") != NULL);    // machine -10: past the switch
    CHECK(refuse(&m, "G1 Z-170") == NULL);
    CHECK(refuse(&m, "G1 Z-171") != NULL);

    // z_dir -1 (default): Marlin's numbers grow away from the switch
    home_all(&m, -1);
    CHECK(NEAR(m.ax[2].dir, 1));
    CHECK(refuse(&m, "G1 Z10") == NULL);
    CHECK(refuse(&m, "G1 Z-1") != NULL);
}

static void test_partial_home(void)
{
    // home Z only: X Y stay unhomed and unlimited
    gcode_motion_t m = {0};
    m.home_mask = 1u << 2;
    gcode_track(&m, "G92 Z0", -1);
    CHECK(!m.ax[0].homed && !m.ax[1].homed && m.ax[2].homed);
    CHECK(refuse(&m, "G1 X999 Z10") == NULL);
    CHECK(refuse(&m, "G1 X999 Z999") != NULL);
}

// ---------------------------------------------------------------- laser

static const gcode_laser_cfg_t LCFG = {.fan = 0, .s_max = 1000, .cap = 1};

// Run one line through the planner and keep the new state (as printer_state does once queued).
static const char *lz(gcode_laser_t *l, const gcode_laser_cfg_t *c, bool armed, const char *line,
                      gcode_laser_step_t *st)
{
    const char *why = gcode_laser_plan(l, c, armed, line, st);
    if (!why) {
        *l = st->next;
    }
    return why;
}

static void test_laser_pwm(void)
{
    CHECK(gcode_laser_pwm(&LCFG, 0) == 0);
    CHECK(gcode_laser_pwm(&LCFG, 1000) == 255);
    CHECK(gcode_laser_pwm(&LCFG, 5000) == 255);  // clamped like GRBL
    CHECK(gcode_laser_pwm(&LCFG, 500) == 128);
    CHECK(gcode_laser_pwm(&LCFG, -1) == 0);
    CHECK(gcode_laser_pwm(&LCFG, NAN) == 0);
    gcode_laser_cfg_t c = {.fan = 1, .s_max = 255, .cap = 0.5f};
    CHECK(gcode_laser_pwm(&c, 255) == 128);  // cap 50 %
    CHECK(gcode_laser_pwm(&c, 51) == 26);
}

static void test_laser_grbl_mode(void)
{
    gcode_laser_t l = {0};
    gcode_laser_step_t st;

    // M3 only sets the power: nothing is sent, the laser fires with the next G1
    CHECK(lz(&l, &LCFG, true, "M3 S500", &st) == NULL && !st.send && st.pre_pwm == -1 && l.on);
    CHECK(lz(&l, &LCFG, true, "G0 X10 Y10", &st) == NULL && st.send && st.pre_pwm == -1);
    CHECK(lz(&l, &LCFG, true, "G1 X20 F600", &st) == NULL && st.send && st.pre_pwm == 128);
    CHECK(lz(&l, &LCFG, true, "G1 X30", &st) == NULL && st.pre_pwm == -1);  // same power: no line
    // inline S changes it, the new S stays (modal)
    CHECK(lz(&l, &LCFG, true, "G1 X40 S1000", &st) == NULL && st.pre_pwm == 255 && NEAR(l.s, 1000));
    CHECK(lz(&l, &LCFG, true, "G1X50S250", &st) == NULL && st.pre_pwm == 64);
    // G0 travel and G4 dwell: off; the next G1 fires again
    CHECK(lz(&l, &LCFG, true, "G0 X0", &st) == NULL && st.pre_pwm == 0 && l.pwm == 0);
    CHECK(lz(&l, &LCFG, true, "G1 X5", &st) == NULL && st.pre_pwm == 64);
    CHECK(lz(&l, &LCFG, true, "G4 P200", &st) == NULL && st.pre_pwm == 0);
    CHECK(lz(&l, &LCFG, true, "G4 S1", &st) == NULL && st.pre_pwm == -1 && NEAR(l.s, 250));  // S = seconds
    // a lone S word: new power, not sent
    CHECK(lz(&l, &LCFG, true, "S750", &st) == NULL && !st.send && NEAR(l.s, 750));
    CHECK(lz(&l, &LCFG, true, "G2 X10 Y10 I5 J0", &st) == NULL && st.pre_pwm == 191);
    // M5: off, nothing sent but the off line
    CHECK(lz(&l, &LCFG, true, "M5", &st) == NULL && !st.send && st.pre_pwm == 0 && !l.on);
    CHECK(lz(&l, &LCFG, true, "G1 X0", &st) == NULL && st.pre_pwm == -1);  // off: G1 doesn't fire
    CHECK(lz(&l, &LCFG, true, "M5", &st) == NULL && st.pre_pwm == -1);     // already off
    // M4 = M3; M3 without S keeps the last S
    CHECK(lz(&l, &LCFG, true, "M4", &st) == NULL && l.on);
    CHECK(lz(&l, &LCFG, true, "N7 G1 X1", &st) == NULL && st.pre_pwm == 191);
    // other lines don't touch the laser
    CHECK(lz(&l, &LCFG, true, "M114", &st) == NULL && st.send && st.pre_pwm == -1 && l.pwm == 191);
}

static void test_laser_refusals(void)
{
    gcode_laser_t l = {0};
    gcode_laser_step_t st;
    // not armed: M3 with power refused, M3 S0 / M5 fine, G1 S never fires
    CHECK(lz(&l, &LCFG, false, "M3 S100", &st) != NULL);
    CHECK(lz(&l, &LCFG, false, "M3", &st) == NULL);  // S still 0
    CHECK(lz(&l, &LCFG, false, "G1 X10 S900", &st) == NULL && st.pre_pwm == -1 && l.pwm == 0);
    CHECK(lz(&l, &LCFG, false, "M3", &st) != NULL);  // S is 900 now
    CHECK(lz(&l, &LCFG, false, "M5", &st) == NULL);
    // disarmed while on: G1 sends off
    l = (gcode_laser_t){.on = true, .s = 500, .pwm = 128};
    CHECK(lz(&l, &LCFG, false, "G1 X10", &st) == NULL && st.pre_pwm == 0);
    // bad S
    CHECK(gcode_laser_plan(&l, &LCFG, true, "M3 S-5", &st) != NULL);
    CHECK(gcode_laser_plan(&l, &LCFG, true, "G1 X1 Snan", &st) != NULL);
    CHECK(gcode_laser_plan(&l, &LCFG, true, "S-1", &st) != NULL);
    // M106 on the laser's fan (no P = fan 0): only off passes
    l = (gcode_laser_t){.on = true, .s = 500, .pwm = 128};
    CHECK(gcode_laser_plan(&l, &LCFG, true, "M106 S255", &st) != NULL);
    CHECK(gcode_laser_plan(&l, &LCFG, true, "M106", &st) != NULL);  // no S = full speed
    CHECK(gcode_laser_plan(&l, &LCFG, true, "M106 P0 S1", &st) != NULL);
    CHECK(lz(&l, &LCFG, true, "M106 S0", &st) == NULL && st.send && l.pwm == 0 && !l.on);
    l.pwm = 50;
    CHECK(lz(&l, &LCFG, true, "M107", &st) == NULL && st.send && l.pwm == 0);
    // another fan passes, also full speed
    CHECK(gcode_laser_plan(&l, &LCFG, true, "M106 P1", &st) == NULL && st.send);
    gcode_laser_cfg_t c1 = LCFG;
    c1.fan = 1;
    CHECK(gcode_laser_plan(&l, &c1, true, "M106 S255", &st) == NULL);       // fan 0: not the laser
    CHECK(gcode_laser_plan(&l, &c1, true, "M106 P1 S255", &st) != NULL);
}

static void test_laser_cost(void)
{
    CHECK(gcode_laser_cost("G1 X10") == 2);
    CHECK(gcode_laser_cost("G0X1") == 2);
    CHECK(gcode_laser_cost("G4 P100") == 2);
    CHECK(gcode_laser_cost("M5") == 2);
    CHECK(gcode_laser_cost("M114") == 1);
    CHECK(gcode_laser_cost("G90") == 1);
    CHECK(gcode_laser_cost("M3 S100") == 1);
}

int main(void)
{
    test_laser_pwm();
    test_laser_grbl_mode();
    test_laser_refusals();
    test_laser_cost();
    test_command_code();
    test_num_param();
    test_emergency_code();
    test_heat_refused();
    test_probing_refused();
    test_limits();
    test_not_homed();
    test_z_dir();
    test_partial_home();
    if (failures) {
        printf("%d failure(s)\n", failures);
        return 1;
    }
    printf("gcode_guard: all tests passed\n");
    return 0;
}
