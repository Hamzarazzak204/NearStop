// =====================================================================
//  config.example.h - copy to config.h (git-ignored) and fill in. settings for the NearStop HOIST node (motor + HC-SR04, USB or Wi-Fi).
//  DEVICE_ID can also be set at build time (-DDEVICE_ID="GW").
// =====================================================================
#pragma once

// ---------------- Which machine is this? ------------------------------
// Must match backend/site_config.json: "GW" = Robot Arm, "MA" = Conveyor, "MB" = Hoist
#ifndef DEVICE_ID
#define DEVICE_ID "MB"
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

// ---------------- Hoist board wiring (teammate's Part 1, hardware-tested) ----
// Motor: A0/GPIO26 -> 220R -> MOSFET gate, 10k gate -> GND, Zener across the motor.
// HC-SR04: VCC -> USB pin (5 V), GND -> GND, TRIG -> A1/GPIO25,
//          ECHO -> 10k -> A2/GPIO34, and A2 -> 20k -> GND (echo is 5 V!).
#define MOTOR_PIN 26                // A0 (PWM 20 kHz)
#define TRIG_PIN  25                // A1
#define ECHO_PIN  34                // A2 (input only, via the divider)
#define LED_PIN   13
#define RUN_PCT   44                // motor speed when Running: 44% of MAX_PCT 90% = 40% PWM duty
#define SLOW_PCT  30                // motor speed when Slowed
// 0 = someone in front does NOT stop the motor (only NearStop's fall lockout,
//     emergency or evacuation commands do). 1 = slow/hold on presence like other machines.
#define PRESENCE_STOP 0

// Default zone distances (cm). NearStop overrides them with set_zones.
#define DEFAULT_WARNING_CM 150
#define DEFAULT_DANGER_CM  50

// ---- Optional: SG90 servo instead of a DC motor (e.g. the MB built with a servo) ----
// Servo signal on MOTOR_PIN; for that board use MOTOR_PIN 25 (A1) and TRIG_PIN 26 (A0).
// #define SERVO_MOTOR 1       // 1 = servo, 0/absent = DC motor (team default)
// #define SERVO_360   0       // 0 = 180-degree SG90, 1 = continuous servo
// #define SERVO_SPEED 12.0f   // degrees per 20 ms at 100 %
// #define FAULTS_TO_ERROR 10  // more grace for sensor dropouts caused by servo current
