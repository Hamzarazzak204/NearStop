// =====================================================================
//  config.example.h - copy to config.h and fill in. config.h is git-ignored
//  (the repo is public: never commit a real Wi-Fi password).
// =====================================================================
#pragma once

// Which worker this strap is (must match backend/site_config.json "workers")
#define WORKER_ID "W1"
#define STRAP_ID  1

// 2.4 GHz network that the strap AND the laptop running NearStop are on.
// iPhone hotspot names use a curly apostrophe: "Name\xE2\x80\x99s iPhone".
#define WIFI_NAME     "YOUR_2.4GHZ_NETWORK"
#define WIFI_PASSWORD "YOUR_PASSWORD"

// NearStop: "" = broadcast on the network (works whatever the laptop's IP is),
// or the laptop's IP.
#define NEARSTOP_IP   ""
#define NEARSTOP_PORT 5005
#define MY_PORT       4211    // the strap listens here for NearStop's replies

// Wi-Fi and ESP-NOW share one radio: this MUST equal the Wi-Fi network's
// channel (iPhone hotspot: usually 6). Machines sending ESP-NOW use it too.
#define ESPNOW_CHANNEL 6

// Strap extras (team's nathacks-demo strap). Set ENV_SENSORS 0 if the BMP180 /
// MQ-2 are not wired. BMP180 (temperature + pressure) is on I2C: SDA/SCL, 3V, GND.
#define ENV_SENSORS  1
#define MQ2_PIN      34     // MQ-2 AO -> 10k -> GPIO 34 (A2), GPIO 34 -> 20k -> GND. VCC -> USB (5 V)
#define MQ2_DIVIDER  1.5    // (10k + 20k) / 20k
#define MQ2_WARMUP_S 5      // fast: 5 s heater warm-up, then 3 s learning clean air (was 120 s)
#define BUTTON_PIN   14     // "I'm OK" button: one leg here, the other to GND
#define BUTTON_LATCHING 1  // 1 = latching SPDT switch (every flip = I'm OK), 0 = momentary push-button
#define FLASH_LED_PIN -1    // KY-034 7-colour flashing LED module on this pin (-1 = none)
#define RGB_LED      1      // 4-leg RGB LED: off normally, flashes colours after a fall (0 = none)
#define RGB_R_PIN    33     // red   leg -> 220 ohm -> GPIO 33
#define RGB_G_PIN    32     // green leg -> 47 ohm  -> GPIO 32
#define RGB_B_PIN    15     // blue  leg -> 47 ohm  -> GPIO 15
#define RGB_COMMON_ANODE 0  // 0 = common cathode (long leg -> GND), 1 = common anode (long leg -> 3V)
#define BUZZER_PIN   27     // fall buzzer MOSFET gate (220R to gate, 10k gate->GND); -1 = none
#define BUZZER_HZ    2048   // CEM-1203 magnetic buzzer needs a tone (2048 Hz = loudest)
#define BUZZER_ACTIVE 0     // 1 = louder ACTIVE buzzer / piezo siren on the same MOSFET (steady on)
