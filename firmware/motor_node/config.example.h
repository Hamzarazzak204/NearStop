// =====================================================================
//  config.example.h - copy to config.h (git-ignored) and fill in. settings for the NearStop MOTOR node (Conveyor MA / Robot Arm GW).
//  DEVICE_ID can also be set at build time (-DDEVICE_ID="GW").
// =====================================================================
#pragma once

// ---------------- Which machine is this? ------------------------------
// Must match backend/site_config.json: "GW" = Robot Arm, "MA" = Conveyor, "MB" = Hoist
#ifndef DEVICE_ID
#define DEVICE_ID "MA"            // "MA" = Conveyor, "GW" = Robot Arm
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

// ---------------- Conveyor / Robot Arm wiring --------------------------
// HC-SR04 as before: VCC -> USB pin, TRIG -> A0/GPIO26, ECHO -> 1k -> A2/GPIO34,
// 2k (2.2k) A2 -> GND.  Motor (new): A1/GPIO25 -> 220R -> MOSFET gate, 10k gate
// -> GND, source -> GND, drain -> motor -, motor + -> separate 5 V supply whose
// - is tied to the Feather GND, flyback diode across the motor (band to +).
#define MOTOR_PIN 25                // A1 (PWM 20 kHz)
#define TRIG_PIN  26                // A0
#define ECHO_PIN  34                // A2 (input only, via the divider)
#define LED_PIN   13
#define MAX_PCT   100               // 5 V motor on a 5 V supply
#define RUN_PCT   70                // motor speed when Running (0-100)
#define SLOW_PCT  45                // motor speed when Slowed (must still turn the motor)
// PRESENCE_STOP 0 (Conveyor, the default): someone or something in front never
//   slows or stops the motor; it keeps RUN_PCT. Only NearStop's commands (fall
//   lockout, emergency, evacuation) or a sensor fault stop it.
// PRESENCE_STOP 1 (Robot Arm): within the warning distance -> slowed, within the
//   danger distance -> stopped. Flash the Robot Arm with -DPRESENCE_STOP=1.
#ifndef PRESENCE_STOP
#define PRESENCE_STOP 0
#endif
#define DANGER_STATE "stopped"

// Default zone distances (cm). NearStop overrides them with set_zones.
#define DEFAULT_WARNING_CM 150
#define DEFAULT_DANGER_CM  50
