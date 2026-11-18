// Temporary (milestone 2): G-code console on the USB monitor.
// Typed lines go to the printer, printer lines are printed as "< ...".
#pragma once

#include "esp_err.h"

esp_err_t console_bridge_start(void);
