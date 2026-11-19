# Pure C, no ESP-IDF dependencies: builds on the PC for host tests (see test/).
idf_component_register(SRCS "gcode_guard.c"
                       INCLUDE_DIRS "include")
