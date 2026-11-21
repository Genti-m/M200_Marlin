idf_component_register(SRCS "printer_state.c"
                       INCLUDE_DIRS "include"
                       REQUIRES printer_link esp_event
                       PRIV_REQUIRES gcode_guard esp_timer nvs_flash esp_driver_gpio)
