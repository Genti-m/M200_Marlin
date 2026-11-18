idf_component_register(SRCS "printer_link.c"
                       INCLUDE_DIRS "include"
                       REQUIRES marlin_proto esp_event
                       PRIV_REQUIRES esp_driver_uart esp_driver_gpio)
