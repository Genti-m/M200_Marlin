// API token, private to web_server (api_token.c). Public part: web_server.h.
#pragma once

#include <stdbool.h>
#include <stdint.h>

void web_token_load(void);                // from NVS, at web_server_start()
bool web_token_check(const char *given);  // constant time; false when no token is set
uint32_t web_token_generation(void);      // changes whenever the token does
