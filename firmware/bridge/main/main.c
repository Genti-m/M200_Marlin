// Start-up only: create the shared services, then the features. No feature logic here.

#include "console_bridge.h"
#include "esp_app_desc.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "nvs_flash.h"
#include "printer_link.h"
#include "printer_state.h"
#include "web_server.h"
#include "wifi_link.h"

static const char *TAG = "m200_bridge";

static void init_nvs(void)
{
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_LOGW(TAG, "NVS erased (%s)", esp_err_to_name(err));
        ESP_ERROR_CHECK(nvs_flash_erase());
        err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(err);
}

void app_main(void)
{
    const esp_app_desc_t *app = esp_app_get_description();
    ESP_LOGI(TAG, "%s %s (ESP-IDF %s, built %s %s)", app->project_name, app->version,
             app->idf_ver, app->date, app->time);

    init_nvs();
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());

    ESP_ERROR_CHECK(printer_link_start());
    ESP_ERROR_CHECK(printer_state_start());
    ESP_ERROR_CHECK(console_bridge_start());  // USB console: G-code + "!" bridge commands
    ESP_ERROR_CHECK(wifi_link_start());
    ESP_ERROR_CHECK(web_server_start());
}
