idf_component_register(SRCS "main.c" "console_bridge.c"
                       INCLUDE_DIRS "."
                       PRIV_REQUIRES printer_link esp_event esp_app_format
                                     esp_driver_usb_serial_jtag)
