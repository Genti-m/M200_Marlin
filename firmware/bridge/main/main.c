// Milestone 1: prove the toolchain, flashing and monitor work.
// Prints chip info once, then a heartbeat every second.

#include <inttypes.h>
#include <stdio.h>

#include "esp_chip_info.h"
#include "esp_flash.h"
#include "esp_idf_version.h"
#include "esp_log.h"
#include "esp_system.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "m200_bridge";

void app_main(void)
{
    esp_chip_info_t chip;
    esp_chip_info(&chip);

    uint32_t flash_size = 0;
    esp_flash_get_size(NULL, &flash_size);

    ESP_LOGI(TAG, "M200 bridge, hello world");
    ESP_LOGI(TAG, "ESP-IDF %s", esp_get_idf_version());
    ESP_LOGI(TAG, "%s rev v%d.%d, %d cores, WiFi%s%s, %" PRIu32 " MB flash",
             CONFIG_IDF_TARGET, chip.revision / 100, chip.revision % 100, chip.cores,
             (chip.features & CHIP_FEATURE_BT) ? "/BT" : "",
             (chip.features & CHIP_FEATURE_BLE) ? "/BLE" : "",
             flash_size / (1024 * 1024));
    ESP_LOGI(TAG, "free heap: %" PRIu32 " bytes", esp_get_free_heap_size());

    for (uint32_t n = 0;; n++) {
        ESP_LOGI(TAG, "alive %" PRIu32, n);
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
}
