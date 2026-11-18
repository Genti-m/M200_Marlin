# ESP-IDF project for the ESP32-S3 bridge in the M200 base.
cmake_minimum_required(VERSION 3.22)

# shared components (config, WiFi, OTA) used by several firmwares
set(EXTRA_COMPONENT_DIRS "${CMAKE_CURRENT_LIST_DIR}/../shared/components")

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(m200_bridge)
