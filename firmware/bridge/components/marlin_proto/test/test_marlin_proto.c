// Host test for marlin_proto (no ESP-IDF needed). From this folder:
//   gcc -std=c11 -Wall -I../include ../marlin_proto.c test_marlin_proto.c -o test_marlin && ./test_marlin
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "marlin_proto.h"

static int failures = 0;
#define CHECK(cond)                                                \
    do {                                                           \
        if (!(cond)) {                                             \
            printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
            failures++;                                            \
        }                                                          \
    } while (0)
#define NEAR(a, b) (fabsf((a) - (b)) < 0.01f)

int main(void)
{
    marlin_reply_t r;

    CHECK(marlin_parse_line("ok", &r) == MARLIN_LINE_OK);
    CHECK(marlin_parse_line("ok\r", &r) == MARLIN_LINE_OK);
    CHECK(marlin_parse_line("ok N12 P15 B3", &r) == MARLIN_LINE_OK);
    CHECK(marlin_parse_line("okay", &r) == MARLIN_LINE_OTHER);

    CHECK(marlin_parse_line("ok T:215.3 /215.0 B:80.1 /80.0 @:64 B@:127", &r) == MARLIN_LINE_OK);
    CHECK(r.nozzle.valid && NEAR(r.nozzle.actual, 215.3f) && NEAR(r.nozzle.target, 215.0f));
    CHECK(r.bed.valid && NEAR(r.bed.actual, 80.1f) && NEAR(r.bed.target, 80.0f));

    CHECK(marlin_parse_line(" T:21.5 /0.0 B:20.1 /0.0 @:0 B@:0", &r) == MARLIN_LINE_TEMP);
    CHECK(NEAR(r.nozzle.actual, 21.5f) && NEAR(r.bed.actual, 20.1f));
    CHECK(marlin_parse_line("T:200.00 /200.00 B:60.00 /60.00", &r) == MARLIN_LINE_TEMP);

    CHECK(marlin_parse_line("busy: processing", &r) == MARLIN_LINE_BUSY);
    CHECK(marlin_parse_line("Error:Printer halted. kill() called!", &r) == MARLIN_LINE_ERROR);
    CHECK(marlin_parse_line("Resend: 12", &r) == MARLIN_LINE_RESEND && r.resend_line == 12);
    CHECK(marlin_parse_line("Resend:N7", &r) == MARLIN_LINE_RESEND && r.resend_line == 7);
    CHECK(marlin_parse_line("rs 5", &r) == MARLIN_LINE_RESEND && r.resend_line == 5);
    CHECK(marlin_parse_line("echo:busy: processing", &r) == MARLIN_LINE_BUSY);
    CHECK(marlin_parse_line("echo:busy: paused for user", &r) == MARLIN_LINE_BUSY);
    CHECK(marlin_parse_line("echo:Unknown command: \"FOO\"", &r) == MARLIN_LINE_ECHO);
    CHECK(marlin_parse_line("start", &r) == MARLIN_LINE_START);
    CHECK(marlin_parse_line("FIRMWARE_NAME:Marlin bugfix-2.1.x", &r) == MARLIN_LINE_OTHER);
    CHECK(marlin_parse_line("Cap:EMERGENCY_PARSER:1", &r) == MARLIN_LINE_OTHER);

    char buf[64];
    int len = marlin_format_numbered(buf, sizeof buf, 3, "M105");
    CHECK(len > 0 && strncmp(buf, "N3 M105*", 8) == 0);
    CHECK(marlin_checksum("N3 M105") == (uint8_t)atoi(buf + 8));
    CHECK(marlin_format_numbered(buf, 6, 3, "M105") == -1);

    printf(failures ? "%d FAILED\n" : "all passed\n", failures);
    return failures ? 1 : 0;
}
