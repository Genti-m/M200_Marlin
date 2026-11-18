idf_component_register(SRCS "web_server.c" "json_out.c" "json_in.c"
                       INCLUDE_DIRS "include"
                       PRIV_REQUIRES esp_http_server esp_timer app_update esp_app_format
                                     printer_link printer_state wifi_link)

# Embed the web UI (web/bridge-ui at the repo root), gzipped at build time.
if(NOT CMAKE_BUILD_EARLY_EXPANSION)
    get_filename_component(UI_DIR "${CMAKE_CURRENT_LIST_DIR}/../../../../web/bridge-ui" ABSOLUTE)
    idf_build_get_property(python PYTHON)
    foreach(name index.html style.css app.js)
        set(gz "${CMAKE_CURRENT_BINARY_DIR}/${name}.gz")
        add_custom_command(OUTPUT "${gz}"
                           COMMAND "${python}" "${CMAKE_CURRENT_LIST_DIR}/embed_gzip.py"
                                   "${UI_DIR}/${name}" "${gz}"
                           DEPENDS "${UI_DIR}/${name}" "${CMAKE_CURRENT_LIST_DIR}/embed_gzip.py"
                           VERBATIM)
        target_add_binary_data(${COMPONENT_LIB} "${gz}" BINARY DEPENDS "${gz}")
    endforeach()
endif()
