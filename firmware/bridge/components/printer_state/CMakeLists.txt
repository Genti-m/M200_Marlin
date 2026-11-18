idf_component_register(SRCS "printer_state.c"
                       INCLUDE_DIRS "include"
                       REQUIRES printer_link esp_event
                       PRIV_REQUIRES esp_timer)
