// Start-up only: create the shared services, then the features. No feature logic here.

#include "console_bridge.h"
#include "esp_app_desc.h"
#include "esp_event.h"
#include "esp_log.h"
#include "printer_link.h"

static const char *TAG = "m200_bridge";

void app_main(void)
{
    const esp_app_desc_t *app = esp_app_get_description();
    ESP_LOGI(TAG, "%s %s (ESP-IDF %s, built %s %s)", app->project_name, app->version,
             app->idf_ver, app->date, app->time);

    ESP_ERROR_CHECK(esp_event_loop_create_default());
    ESP_ERROR_CHECK(printer_link_start());
    ESP_ERROR_CHECK(console_bridge_start());  // temporary, milestone 2
}
