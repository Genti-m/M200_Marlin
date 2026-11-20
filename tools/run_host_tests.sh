#!/bin/sh
# Build and run the PC-side unit tests of the pure-C components (no ESP-IDF needed).
# Needs a C compiler: CC=gcc (default), CC=clang, or CC="zig cc".  Usage:  sh tools/run_host_tests.sh
set -eu
cd "$(dirname "$0")/.."
# Windows: no gcc on PATH, but MSYS2's UCRT64 gcc installed (pacman -S mingw-w64-ucrt-x86_64-gcc)
if [ -z "${CC:-}" ] && ! command -v gcc >/dev/null 2>&1 && [ -x /c/msys64/ucrt64/bin/gcc.exe ]; then
    PATH="/c/msys64/ucrt64/bin:$PATH"
fi
CC="${CC:-gcc}"
OUT="${TMPDIR:-/tmp}/m200_host_tests"
mkdir -p "$OUT"
FLAGS="-std=c11 -Wall -Wextra -g -fsanitize=address,undefined"
case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) FLAGS="-std=c11 -Wall -Wextra -g" ;; esac

C=firmware/bridge/components

# shellcheck disable=SC2086
$CC $FLAGS -I$C/marlin_proto/include $C/marlin_proto/marlin_proto.c \
    $C/marlin_proto/test/test_marlin_proto.c -lm -o "$OUT/test_marlin_proto"
"$OUT/test_marlin_proto"

# shellcheck disable=SC2086
$CC $FLAGS -I$C/gcode_guard/include $C/gcode_guard/gcode_guard.c \
    $C/gcode_guard/test/test_gcode_guard.c -lm -o "$OUT/test_gcode_guard"
"$OUT/test_gcode_guard"
