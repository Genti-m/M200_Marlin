// Start-up only: create the shared services, then the features. No feature logic here.

#include "console_bridge.h"
#include "esp_app_desc.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_ota_ops.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
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

// OTA rollback (CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE): a new image boots "pending verify".
// It is kept only once WiFi is back, so the next OTA can still reach the board. A crash or
// reset before that, or no WiFi within OTA_CONFIRM_S, boots the previous image again.
#define OTA_CONFIRM_S 120

static void ota_confirm_task(void *arg)
{
    for (int s = 0; s < OTA_CONFIRM_S; s++) {
        if (wifi_link_connected()) {
            esp_ota_mark_app_valid_cancel_rollback();
            ESP_LOGI(TAG, "new firmware confirmed (WiFi up)");
            vTaskDelete(NULL);
        }
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
    ESP_LOGE(TAG, "new firmware: no WiFi after %d s, rolling back", OTA_CONFIRM_S);
    esp_ota_mark_app_invalid_rollback_and_reboot();
    vTaskDelete(NULL);  // only reached if there is nothing to roll back to
}

static void ota_check_pending(void)
{
    esp_ota_img_states_t st;
    const esp_partition_t *run = esp_ota_get_running_partition();
    if (esp_ota_get_state_partition(run, &st) == ESP_OK && st == ESP_OTA_IMG_PENDING_VERIFY) {
        ESP_LOGW(TAG, "first boot after OTA on %s: waiting for WiFi to confirm it", run->label);
        xTaskCreate(ota_confirm_task, "ota_confirm", 3072, NULL, 3, NULL);
    }
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
    ota_check_pending();  // after everything started: an abort above means "not valid"
}
