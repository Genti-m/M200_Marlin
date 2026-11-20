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

int main(void)
{
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
