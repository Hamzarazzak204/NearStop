// =====================================================================
//  config.example.h - copy to config.h (git-ignored) and fill in. Settings for the NearStop machine node (USB or Wi-Fi).
//  DEVICE_ID can also be set at build time (-DDEVICE_ID="GW").
// =====================================================================
#pragma once

// ---------------- Which machine is this? ------------------------------
// Must match backend/site_config.json: "GW" = Robot Arm, "MA" = Conveyor, "MB" = Hoist
#ifndef DEVICE_ID
#define DEVICE_ID "MA"
#endif

// ---------------- Wi-Fi (used when no USB bridge is listening) ---------
// A 2.4 GHz network that the board AND the computer running NearStop are on.
// Not eduroam / campus Wi-Fi. iPhone hotspot names use a curly apostrophe,
// written as \xE2\x80\x99. Don't share this file with the real password in it.
#define WIFI_SSID     "YOUR_2.4GHZ_NETWORK"
#define WIFI_PASSWORD "YOUR_PASSWORD"

// Computer running NearStop. Leave "" to BROADCAST the reports on the local
// network: NearStop receives them wherever it runs and replies to the board,
// so the laptop's IP can change without re-flashing.
#define SERVER_IP     ""
#define SERVER_PORT   5005          // NearStop listens here
#define LOCAL_PORT    5006          // board sends from / listens on this port

// ---------------- HC-SR04 wiring (our boards) --------------------------
// VCC -> USB pin (5 V; present when powered by any USB source: laptop,
// charger or power bank), GND -> GND, TRIG -> A0, ECHO -> 1 kOhm -> A2,
// 2 kOhm (or 2.2 kOhm) from A2 to GND.
#define TRIG_PIN 26                 // A0
#define ECHO_PIN 34                 // A2 (input only, via the divider)
#define LED_PIN  13

// Default zone distances (cm). NearStop overrides them with set_zones.
#define DEFAULT_WARNING_CM 150
#define DEFAULT_DANGER_CM  50
