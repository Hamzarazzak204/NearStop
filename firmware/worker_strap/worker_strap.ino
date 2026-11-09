// =====================================================================
//  WORKER STRAP for NearStop: fall detection + danger zones + lockout
//  Board: Adafruit Feather ESP32 V2 (choose "Adafruit Feather ESP32 V2").
//  Library: Adafruit NeoPixel. Serial Monitor at 115200.
//  Settings: config.h (copy config.example.h).
//
//  Based on the team's strap/strap_main.ino (natHacks demo). The fall detector,
//  IMU auto-recovery and zone colours are unchanged.
//  FALLS: every confirmed fall is sent to NearStop (since 2026-11-08), with the
//  zone the worker was in. A fall in the RED zone (< 50 cm in the last 4 s) locks
//  out that machine; a fall anywhere else still raises an alert and a check-in.
//
//  RADIO PRESENCE (anchors): the strap broadcasts "NSB:W1" over ESP-NOW 10x a second
//  on ESPNOW_CHANNEL. Each machine (motor_node / hoist_node) reports whether it hears
//  it and how strong (worker_rssi). Only Sam counts as "near" a machine, not objects.
//
//  What's different for NearStop:
//   * Distances come from NearStop: it sends {"type":"proximity",...} with every
//     machine's ultrasonic reading ~5 times a second. ESP-NOW DistancePackets
//     from machines are still accepted too.
//   * Status (1/s) and fall alerts go to NearStop (port 5005), over the USB cable
//     when "NearStop USB Bridge" is running, otherwise over Wi-Fi. NearStop
//     answers "ACK <id>" and LOCKS OUT the machine(s) the worker fell near.
//   * Red LED (pin 13) is ON while the worker is close to an anchor/machine
//     (YELLOW or RED zone), OFF otherwise.
//   * ESP-NOW starts after Wi-Fi connects (starting it while Wi-Fi is still
//     joining stalls the connection on core 3.x), or after 15 s without Wi-Fi.
//
//  NeoPixel: RED / YELLOW / GREEN = zone, BLUE = no machine data,
//            fast RED-WHITE = fall alert waiting for NearStop to confirm.
//
//  From the team's nathacks-demo strap (strap_main.ino + tools/gas_test):
//   * "I'M OK" button (GPIO 14 to GND): sends {"type":"worker_ok",...} until
//     NearStop answers "OKACK <id>". It answers an open check-in; it never
//     cancels a fall lockout. Quick green flashes = press registered.
//   * BMP180 (I2C 0x77, shares SDA/SCL with the MPU-6050): temperature + air
//     pressure every 2 s. Never waits (each call is one short I2C transfer).
//     null after 3 failed reads. The read is split so loop() waits only ~5 ms.
//   * MQ-2 (GPIO 34 via divider): 5 s warm-up, learns clean air for 3 s (follows the heater for 3 min), then
//     GREEN / YELLOW / RED from the sensor resistance vs clean air (<= 60 % /
//     <= 35 %). Relative to clean air: NOT ppm or %LEL.
//   They ride in the status message: temp_c, pressure_hpa, gas_mv,
//   gas_ratio, gas_level ("warming" | "green" | "yellow" | "red").
//
//  Serial Monitor test commands (type, then Enter):
//    2 40   -> fake machine 2 (Conveyor) at 40 cm      f -> fake a fall (5.0 g)
//    i      -> fake a hard hit (impact) without a confirmed fall
//    ok     -> fake an "I'm OK" button press (also stops the fall buzzer)
//  Fall buzzer (GPIO 27 -> MOSFET): beeps after every confirmed fall until "I'M OK".
//    b      -> MQ-2: re-learn clean air      s -> MQ-2: skip the warm-up (testing)
// =====================================================================
#include <Wire.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include <esp_arduino_version.h>
#include <Adafruit_NeoPixel.h>
#include "config.h"

// Strap extras (team's nathacks-demo strap): defaults for an older config.h
#ifndef ENV_SENSORS
#define ENV_SENSORS 1        // BMP180 + MQ-2 fitted
#endif
#ifndef MQ2_PIN
#define MQ2_PIN 34           // MQ-2 AO through the 10k/20k divider (A2). Must be GPIO 32-39 (Wi-Fi)
#endif
#ifndef MQ2_DIVIDER
#define MQ2_DIVIDER 1.5
#endif
#ifndef MQ2_WARMUP_S
#define MQ2_WARMUP_S 5       // FAST: 5 s heater warm-up, 3 s to learn clean air (was 120 + 10 s).
                             // While the heater keeps settling (MQ2_SETTLE_S) the clean-air value follows it.
#endif
#ifndef BUTTON_PIN
#define BUTTON_PIN 14        // "I'm OK" button to GND
#endif
#ifndef FLASH_LED_PIN
#define FLASH_LED_PIN -1     // KY-034 "7-colour flashing LED" module (CNT5 board) S pin; -1 = none.
                             // It cycles colours by itself: we can only switch it on/off.
#endif
#ifndef RGB_LED
#define RGB_LED 1            // 1 = 4-leg RGB LED on RGB_R/G/B_PIN: OFF normally, flashes colours after a fall
#endif
#ifndef RGB_R_PIN
#define RGB_R_PIN 33         // red leg   -> 220 ohm -> GPIO 33
#endif
#ifndef RGB_G_PIN
#define RGB_G_PIN 32         // green leg -> 47 ohm  -> GPIO 32
#endif
#ifndef RGB_B_PIN
#define RGB_B_PIN 15         // blue leg  -> 47 ohm  -> GPIO 15
#endif
#ifndef RGB_COMMON_ANODE
#define RGB_COMMON_ANODE 0   // 0 = common CATHODE (long leg to GND), 1 = common ANODE (long leg to 3V)
#endif
#ifndef BUTTON_LATCHING
#define BUTTON_LATCHING 1    // 1 = latching (push-on/push-off) switch: EVERY flip = "I'm OK"
                             // 0 = momentary push-button: each press = "I'm OK"
#endif
#ifndef BUZZER_PIN
#define BUZZER_PIN 27        // MOSFET gate of the fall buzzer (-1 = no buzzer)
#endif
#ifndef BUZZER_HZ
#define BUZZER_HZ 2048       // CEM-1203 magnetic buzzer: needs a tone, loudest at 2048 Hz
#endif
#ifndef BUZZER_ACTIVE
#define BUZZER_ACTIVE 0      // 1 = ACTIVE buzzer / piezo siren (makes its own tone: just switch it on)
#endif
#ifndef BUZZER_SIREN
#define BUZZER_SIREN 1       // 1 = continuous siren sweep 1.5-3.5 kHz (speaker / passive buzzer), 0 = fixed beeps
#endif
#ifndef BUZZER_VOICE
#define BUZZER_VOICE 1       // 1 = speaker says "Are you okay?" after a fall (overrides the siren)
#endif
#include "voice_are_you_ok.h"   // made by tools/make_voice.py

// --- Machines: NearStop IDs in strap order (machine 1, 2, 3) ---
const char* MACHINE_NAMES[4] = {"", "GW", "MA", "MB"};   // 1 = Robot Arm, 2 = Conveyor, 3 = Hoist

// --- Zones (scaled 1:6 from real heavy-equipment proximity systems) ---
const float RED_CM    = 50;     // closer than this = RED (danger)
const float YELLOW_CM = 150;    // closer than this = YELLOW (caution)
const float HYST_CM   = 10;     // move 10 cm back out before the colour relaxes
const unsigned long STALE_MS = 1000;       // forget a machine not heard for 1 s
const unsigned long RED_MEMORY_MS = 4000;  // a fall counts as "in the red zone" if you were red in the last 4 s

// --- Fall detection (strap on upper arm/shoulder; tune with real data) ---
// 2026-11-09 (later): MORE sensitive again, as asked. Shorter/softer drops now count;
// the stillness check after the hit still rejects hammering, jumps and walking.
const float FREEFALL_G       = 0.90;   // was 0.85 (a partial free fall counts)
const unsigned long FREEFALL_MIN_MS = 15;   // was 25
const float IMPACT_G         = 1.8;    // was 2.5
const float HARD_IMPACT_G    = 2.5;    // was 3.5 (hit with no free fall before it)
const float SEVERE_G         = 6.0;
const unsigned long IMPACT_WINDOW_MS = 1500;  // was 1000 (longer tumble before the hit)
const unsigned long SETTLE_MS = 300;
const unsigned long STILL_MS  = 800;   // was 1000 (fall confirmed sooner)
const float STILL_STD_G       = 0.30;  // was 0.20 (a dazed worker who moves a little still counts)

// --- Alert sending / links ---
const unsigned long ALERT_RESEND_MS  = 500;
const unsigned long ALERT_GIVE_UP_MS = 60000;
const unsigned long STATUS_EVERY_MS  = 1000;    // over USB
const unsigned long STATUS_EVERY_WIFI_MS = 500;  // over Wi-Fi: twice as often, packets get lost (no antenna)
const unsigned long BRIDGE_TIMEOUT_MS = 3000;   // no USB-bridge ping this long -> use Wi-Fi
const unsigned long ESPNOW_FALLBACK_MS = 60000; // start ESP-NOW (beacon) anyway if Wi-Fi isn't up by then.
                                                // Starting it while Wi-Fi is still joining stalls the join.
const int NEAR_LED_PIN = 13;                    // red LED: ON when close to an anchor

// ============================ HARDWARE ===============================
Adafruit_NeoPixel pixel(1, PIN_NEOPIXEL, NEO_GRB + NEO_KHZ800);
const uint8_t MPU = 0x68;
WiFiUDP udp;
IPAddress nearstopIp;
bool nearstopIpFixed = false;
bool espnowStarted = false;
unsigned long lastBridgeMs = 0;

bool bridgeActive() { return lastBridgeMs != 0 && millis() - lastBridgeMs < BRIDGE_TIMEOUT_MS; }

// ======================= MESSAGES FROM MACHINES ======================
typedef struct __attribute__((packed)) {
  uint8_t  magic;        // always 0xD1
  uint8_t  machine_id;   // 1, 2 or 3
  uint16_t distance_cm;
} DistancePacket;
const uint8_t DIST_MAGIC = 0xD1;

volatile float machineCm[4] = {0, 0, 0, 0};
volatile unsigned long machineSeenMs[4] = {0, 0, 0, 0};
volatile unsigned long packetsFrom[4] = {0, 0, 0, 0};
volatile unsigned long unknownPackets = 0;
volatile int lastUnknownLen = 0;
uint8_t knownMac[4][6];
bool macUsed[4] = {false, false, false, false};
unsigned long proximityMsgs = 0;

int idForMac(const uint8_t* mac) {
  for (int i = 1; i <= 3; i++) if (macUsed[i] && memcmp(knownMac[i], mac, 6) == 0) return i;
  for (int i = 1; i <= 3; i++) if (!macUsed[i]) { memcpy(knownMac[i], mac, 6); macUsed[i] = true; return i; }
  return 0;
}

// Understands DistancePacket, or a plain number (float / int / 2-byte / text like "45" or "M2:45")
void handlePacket(const uint8_t* mac, const uint8_t* data, int len) {
  int id = 0;
  float cm = -1;
  if (len == (int)sizeof(DistancePacket) && data[0] == DIST_MAGIC) {
    DistancePacket p;
    memcpy(&p, data, sizeof(p));
    id = p.machine_id;
    cm = p.distance_cm;
  } else {
    id = idForMac(mac);
    if (len == 4) {
      float f; int32_t n;
      memcpy(&f, data, 4);
      memcpy(&n, data, 4);
      if (f >= 1 && f < 1000) cm = f;
      else if (n > 0 && n < 1000) cm = n;
    } else if (len == 2) {
      uint16_t u;
      memcpy(&u, data, 2);
      if (u < 1000) cm = u;
    } else if (len > 0 && len < 20) {
      char buf[20];
      memcpy(buf, data, len);
      buf[len] = 0;
      char* p = strrchr(buf, ':');
      if (!p) p = strrchr(buf, '=');
      float f = atof(p ? p + 1 : buf);
      if (f > 0 && f < 1000) cm = f;
    }
  }
  if (id >= 1 && id <= 3 && cm >= 0) {
    machineCm[id] = cm;
    machineSeenMs[id] = millis();
    packetsFrom[id]++;
  } else {
    unknownPackets++;
    lastUnknownLen = len;
  }
}

#if ESP_ARDUINO_VERSION_MAJOR >= 3
void onReceive(const esp_now_recv_info_t* info, const uint8_t* data, int len) { handlePacket(info->src_addr, data, len); }
#else
void onReceive(const uint8_t* mac, const uint8_t* data, int len) { handlePacket(mac, data, len); }
#endif

// ============================== ZONES ================================
int zone = -1;              // -1 = no data, 0 = green, 1 = yellow, 2 = red
int closestId = 0;
float closestCm = 0;
unsigned long lastRedMs = 0;
int lastRedMachine = 0;
float lastRedCm = 0;

void onMachineDistance(int machineId, float cm) {   // Serial test input and NearStop proximity
  if (machineId < 1 || machineId > 3) return;
  machineCm[machineId] = cm;
  machineSeenMs[machineId] = millis();
}

void updateZone() {
  float closest = 99999;
  int cid = 0;
  for (int i = 1; i <= 3; i++) {
    unsigned long seen = machineSeenMs[i];
    if (seen != 0 && millis() - seen < STALE_MS && machineCm[i] < closest) {
      closest = machineCm[i];
      cid = i;
    }
  }
  closestId = cid;
  closestCm = closest;

  int newZone;
  if (cid == 0) newZone = -1;
  else if (closest < RED_CM || (zone == 2 && closest < RED_CM + HYST_CM)) newZone = 2;
  else if (closest < YELLOW_CM || (zone >= 1 && closest < YELLOW_CM + HYST_CM)) newZone = 1;
  else newZone = 0;

  if (newZone != zone) {
    zone = newZone;
    if (zone == 2)      Serial.printf(">>> ZONE RED     (%s, %.0f cm)\n", MACHINE_NAMES[cid], closest);
    else if (zone == 1) Serial.printf(">>> ZONE YELLOW  (%s, %.0f cm)\n", MACHINE_NAMES[cid], closest);
    else if (zone == 0) Serial.printf(">>> ZONE GREEN   (%s, %.0f cm)\n", MACHINE_NAMES[cid], closest);
    else                Serial.println(">>> NO DATA      (no machine reading for 1 s)");
  }
  if (zone == 2) {
    lastRedMs = millis();
    lastRedMachine = cid;
    lastRedCm = closest;
  }
}

bool wasInRedRecently() {
  return lastRedMs != 0 && millis() - lastRedMs < RED_MEMORY_MS;
}

// =========================== FALL DETECTOR ===========================
void onImpactDetected(float g);   // defined in the NearStop link section below

enum FallPhase { PH_IDLE, PH_FALLING, PH_SETTLING, PH_STILL_CHECK };
FallPhase phase = PH_IDLE;
unsigned long phaseStart = 0, freefallStart = 0, impactMs = 0;
bool inFreefall = false;
float peakG = 0;
int stillN = 0;
double stillSum = 0, stillSumSq = 0;
float lastStillStd = 0;

void startImpact(unsigned long now, float g) {
  phase = PH_SETTLING;
  impactMs = now;
  peakG = g;
  onImpactDetected(g);   // tell NearStop right away (the fall itself is confirmed ~1.3 s later)
}

bool fallUpdate(unsigned long now, float g) {
  switch (phase) {
    case PH_IDLE:
      if (g < FREEFALL_G) {
        if (!inFreefall) { inFreefall = true; freefallStart = now; }
        else if (now - freefallStart >= FREEFALL_MIN_MS) { phase = PH_FALLING; phaseStart = now; }
      } else {
        inFreefall = false;
        if (g >= HARD_IMPACT_G) startImpact(now, g);
      }
      break;
    case PH_FALLING:
      if (g >= IMPACT_G) startImpact(now, g);
      else if (now - phaseStart > IMPACT_WINDOW_MS) { phase = PH_IDLE; inFreefall = false; }
      break;
    case PH_SETTLING:
      if (g > peakG) peakG = g;
      if (now - impactMs >= SETTLE_MS) {
        phase = PH_STILL_CHECK;
        phaseStart = now;
        stillN = 0; stillSum = 0; stillSumSq = 0;
      }
      break;
    case PH_STILL_CHECK:
      stillN++;
      stillSum += g;
      stillSumSq += (double)g * g;
      if (now - phaseStart >= STILL_MS) {
        double mean = stillSum / stillN;
        double var = stillSumSq / stillN - mean * mean;
        lastStillStd = var > 0 ? sqrt(var) : 0;
        phase = PH_IDLE;
        inFreefall = false;
        if (lastStillStd <= STILL_STD_G) return true;  // FALL CONFIRMED
        Serial.printf("(hit %.1f g but kept moving: not a fall)\n", peakG);
      }
      break;
  }
  return false;
}

// =========================== NEARSTOP LINK ===========================
bool alertPending = false;
unsigned long alertId = 0, alertStartMs = 0, lastAlertSendMs = 0;
char alertJson[300];
unsigned long fallsTotal = 0, alertsSent = 0, fallsNotRed = 0;
uint32_t bootId = 0;

void buzzerStart();   // fall alarm (defined with the "I'm OK" button below)
extern unsigned long imuOkMs;
extern bool buzzerAlarm;        // fall alarm active (until "I'm OK")   // last valid motion reading (defined with the IMU code)
extern bool okPending;
extern unsigned long okId, okConfirmed;
void serviceOk();

bool linkUp() { return bridgeActive() || WiFi.status() == WL_CONNECTED; }

// NearStop's address, learned from its replies. Wi-Fi broadcast is sent once with no
// retries (lossy with no antenna); a direct (unicast) packet is retried by Wi-Fi.
IPAddress learnedIp;
unsigned long learnedMs = 0;
const unsigned long LEARNED_IP_FOR_MS = 15000;   // no reply this long -> broadcast again to find it

// One message to NearStop: a JSON line over USB (the bridge forwards it), else UDP over Wi-Fi.
void sendUdp(const char* text) {
  if (bridgeActive()) { Serial.println(text); return; }
  if (WiFi.status() != WL_CONNECTED) return;
  bool learned = learnedMs != 0 && millis() - learnedMs < LEARNED_IP_FOR_MS;
  IPAddress dest = nearstopIpFixed ? nearstopIp : learned ? learnedIp : WiFi.broadcastIP();
  udp.beginPacket(dest, NEARSTOP_PORT);           // sent from MY_PORT, so replies come back here
  udp.write((const uint8_t*)text, strlen(text));
  udp.endPacket();
}

// Reads the number after "KEY": in a JSON text (or returns false)
bool jsonNumber(const char* json, const char* key, long& out) {
  char pat[24];
  snprintf(pat, sizeof(pat), "\"%s\":", key);
  const char* s = strstr(json, pat);
  if (!s) return false;
  s += strlen(pat);
  if (*s != '-' && (*s < '0' || *s > '9')) return false;
  out = strtol(s, nullptr, 10);
  return true;
}

// Text from NearStop (UDP) or from the USB bridge (Serial)
void handleNearStopText(const char* buf) {
  if (strncmp(buf, "OKACK", 5) == 0) {                 // NearStop got "I'm OK" <id>
    unsigned long ackId = strtoul(buf + 5, nullptr, 10);
    if (okPending && ackId == okId) {
      okPending = false;
      okConfirmed++;
      Serial.printf("    NearStop CONFIRMED \"I'm OK\" #%lu\n", okId);
    }
    return;
  }
  if (strncmp(buf, "ACK", 3) == 0) {
    unsigned long ackId = strtoul(buf + 3, nullptr, 10);
    if (alertPending && ackId == alertId) {
      alertPending = false;
      Serial.printf("    NearStop CONFIRMED fall alert #%lu (%.1f s after the fall)\n", alertId, (millis() - alertStartMs) / 1000.0);
    }
    return;
  }
  if (strstr(buf, "\"type\":\"bridge\"")) {
    if (!bridgeActive()) Serial.println("[LINK] NearStop USB bridge found - reporting over USB");
    lastBridgeMs = millis();
    return;
  }
  if (strstr(buf, "\"type\":\"proximity\"")) {
    // {"type":"proximity","worker":"W1","machines":{"GW":40,"MA":-1,"MB":-1}}  (-1 = nothing seen)
    const char* m = strstr(buf, "\"machines\"");
    if (!m) return;
    proximityMsgs++;
    for (int i = 1; i <= 3; i++) {
      long cm;
      if (jsonNumber(m, MACHINE_NAMES[i], cm) && cm >= 0) onMachineDistance(i, (float)cm);
    }
  }
}

// A hard hit while CLOSE to a machine (red zone): send an instant "impact" so a
// machine like the Hoist can stop before the fall is confirmed. This does not
// change the fall rule: only a confirmed fall sends a fall_alert.
void onImpactDetected(float g) {
  static unsigned long lastImpactMs = 0;
  if (!wasInRedRecently() || (lastImpactMs != 0 && millis() - lastImpactMs < 1000)) return;
  lastImpactMs = millis();
  char buf[200];
  snprintf(buf, sizeof(buf),
           "{\"type\":\"impact\",\"worker\":\"%s\",\"strap\":%d,\"g\":%.1f,\"machine\":%d,\"machine_id\":\"%s\",\"distance_cm\":%.0f}",
           WORKER_ID, STRAP_ID, g, lastRedMachine, MACHINE_NAMES[lastRedMachine], lastRedCm);
  sendUdp(buf);
  if (!bridgeActive()) sendUdp(buf);   // Wi-Fi: send twice in case one packet is lost
  Serial.printf("    impact %.1f g near %s -> NearStop (possible fall)\n", g, MACHINE_NAMES[lastRedMachine]);
}

extern unsigned long lastAlertMs;

// EVERY confirmed fall is sent to NearStop (changed 2026-11-08: before, only
// falls in the red zone were sent). "zone" says where the worker was:
//   red    = within 50 cm of a machine in the last 4 s -> NearStop locks it out
//   yellow / green = near / within range of a machine  -> NearStop's own distance rules decide
//   none   = no machine nearby                          -> alert + check-in only
void onFallConfirmed(float peak) {
  fallsTotal++;
  bool red = wasInRedRecently();
  if (!red) fallsNotRed++;
  const char* zn = red ? "red" : zone == 1 ? "yellow" : zone == 0 ? "green" : "none";
  int mId = red ? lastRedMachine : closestId;           // 0 = no machine
  float mCm = red ? lastRedCm : (closestId ? closestCm : -1.0f);
  alertId++;
  alertsSent++;
  snprintf(alertJson, sizeof(alertJson),
           "{\"type\":\"fall_alert\",\"worker\":\"%s\",\"strap\":%d,\"boot\":%lu,\"id\":%lu,\"peak_g\":%.1f,"
           "\"severity\":\"%s\",\"zone\":\"%s\",\"machine\":%d,\"machine_id\":\"%s\",\"distance_cm\":%.0f,\"uptime_ms\":%lu}",
           WORKER_ID, STRAP_ID, (unsigned long)bootId, alertId, peak, peak >= SEVERE_G ? "severe" : "fall",
           zn, mId, MACHINE_NAMES[mId], mCm, (unsigned long)millis());
  alertPending = true;
  alertStartMs = millis();
  lastAlertMs = alertStartMs;
  lastAlertSendMs = 0;
  Serial.println();
  Serial.printf("*** FALL DETECTED: peak %.1f g ***\n", peak);
  if (red) Serial.printf("    In the RED zone (%s, %.0f cm) -> ALERT #%lu to NearStop (machine will be locked out)\n",
                         MACHINE_NAMES[mId], mCm, alertId);
  else if (mId) Serial.printf("    %s zone (%s, %.0f cm) -> ALERT #%lu to NearStop\n", zn, MACHINE_NAMES[mId], mCm, alertId);
  else Serial.printf("    Not near a machine -> ALERT #%lu to NearStop (check-in, no lockout)\n", alertId);
  if (!linkUp()) Serial.println("    (no link to NearStop yet: will send as soon as there is one)");
  buzzerStart();   // beeps until the worker presses "I'M OK"
}

void serviceAlert() {
  int size = udp.parsePacket();
  while (size > 0) {
    char buf[300];
    int n = udp.read((uint8_t*)buf, sizeof(buf) - 1);
    buf[n > 0 ? n : 0] = 0;
    // A reply from NearStop (proximity, ACK, OKACK): remember where it is and send there directly.
    // Stick to ONE hub: if a second NearStop runs on the same network, ignore it while ours is alive
    // (otherwise the strap flips between them and each sees it half the time).
    bool fromHub = strncmp(buf, "ACK", 3) == 0 || strncmp(buf, "OKACK", 5) == 0 || strstr(buf, "\"type\":\"proximity\"");
    IPAddress from = udp.remoteIP();
    bool locked = nearstopIpFixed || (learnedMs != 0 && millis() - learnedMs < LEARNED_IP_FOR_MS);
    IPAddress ours = nearstopIpFixed ? nearstopIp : learnedIp;
    if (fromHub && locked && from != ours) {
      static IPAddress warnedIp;
      if (from != warnedIp) {
        Serial.printf("[LINK] another NearStop at %s is IGNORED (using %s). Close the other one, or set NEARSTOP_IP.\n",
                      from.toString().c_str(), ours.toString().c_str());
        warnedIp = from;
      }
      size = udp.parsePacket();
      continue;
    }
    if (fromHub) {
      if (!locked || from != learnedIp)
        Serial.printf("[LINK] NearStop is at %s - sending to it directly\n", from.toString().c_str());
      learnedIp = from;
      learnedMs = millis();
    }
    handleNearStopText(buf);
    size = udp.parsePacket();
  }
  serviceOk();
  if (!alertPending) return;
  if (millis() - alertStartMs > ALERT_GIVE_UP_MS) {
    alertPending = false;
    Serial.printf("    Gave up on alert #%lu: no ACK from NearStop in 60 s. Is NearStop running?\n", alertId);
    return;
  }
  if (millis() - lastAlertSendMs >= ALERT_RESEND_MS && linkUp()) {
    lastAlertSendMs = millis();
    sendUdp(alertJson);
  }
}

// ========================= "I'M OK" BUTTON ===========================
// From the team's strap_main.ino. One leg to BUTTON_PIN, the other to GND.
// A tap sends {"type":"worker_ok",...}, resent every 0.5 s until NearStop
// answers "OKACK <id>" (gives up after 30 s). It answers a check-in; it does
// not cancel a fall alert or a lockout (the supervisor still approves restart).
const unsigned long OK_RESEND_MS  = 500;
const unsigned long OK_GIVE_UP_MS = 30000;
const unsigned long OK_LINKS_ALERT_MS = 300000;  // a tap within 5 min of a fall alert answers it
bool okPending = false;
unsigned long okId = 0, okStartMs = 0, lastOkSendMs = 0, okFeedbackUntil = 0, okSent = 0, okConfirmed = 0;
unsigned long lastAlertMs = 0;                   // when the most recent fall alert was made
char okJson[240];

// ============================ FALL BUZZER ============================
// Magnetic buzzer (CEM-1203) through a MOSFET: GPIO 27 -> 220R -> gate, 10k gate
// -> GND. After a confirmed fall it beeps (0.25 s on / 0.25 s off) until the
// worker presses "I'M OK" (or types "ok"), so people nearby can find the worker.
// Loudness: 50 % duty is the loudest a square wave gets; the pattern sounds 80 %
// of the time (0.6 s on / 0.15 s off). Serial "sweep" plays 1800-3000 Hz to find
// this buzzer's loudest tone; "hz 2400" tries one (then set BUZZER_HZ in config.h).
const unsigned long BUZZ_ON_MS = 600, BUZZ_OFF_MS = 150;
bool buzzerAlarm = false;
uint32_t buzzerHz = BUZZER_HZ;

void buzzerTone(bool on) {
  if (BUZZER_PIN < 0) return;
  if (BUZZER_ACTIVE) digitalWrite(BUZZER_PIN, on ? HIGH : LOW);   // active buzzer: steady on
  else ledcWrite(BUZZER_PIN, on ? 128 : 0);   // 50 % duty = loudest tone, 0 = silent
}

void buzzerSetHz(uint32_t hz) {
  if (BUZZER_PIN < 0 || BUZZER_ACTIVE || hz < 200 || hz > 10000) return;
  buzzerHz = hz;
  ledcChangeFrequency(BUZZER_PIN, hz, 8);
}

// Test: play one frequency for ~1 s (blocks; testing only)
void buzzerTry(uint32_t hz) {
  buzzerSetHz(hz);
  Serial.printf("BUZZER: %lu Hz\n", (unsigned long)hz);
  buzzerTone(true);
  delay(900);
  buzzerTone(false);
  delay(300);
}

void buzzerSetup() {
  if (BUZZER_PIN < 0) return;
  if (BUZZER_ACTIVE) { pinMode(BUZZER_PIN, OUTPUT); buzzerTone(false); return; }
  if (!ledcAttach(BUZZER_PIN, BUZZER_HZ, 8)) Serial.printf("BUZZER: could not set up the tone on GPIO %d\n", BUZZER_PIN);
  buzzerTone(false);
}

void buzzerStart() {
  if (!buzzerAlarm) Serial.println("    BUZZER ON - beeping until the worker presses \"I'M OK\"");
  buzzerAlarm = true;
}

// Siren: sweeps 1.5 -> 3.5 kHz and back every 1.2 s, no gaps. A sweep always passes the
// speaker's loudest (resonant) frequency and the 2-4 kHz band the ear hears best.
const uint32_t SIREN_LOW_HZ = 1500, SIREN_HIGH_HZ = 3500;
const unsigned long SIREN_PERIOD_MS = 1200;

// Voice: "Are you okay?" played as PWM audio on the speaker pin. The PWM runs at
// 78 kHz (inaudible); its duty follows the 8 kHz recording, and the speaker turns
// that into sound. Blocks ~1 s per phrase (the fall is already confirmed); the
// "I'm OK" button cuts it short.
const uint32_t VOICE_PWM_HZ = 78125;           // 8-bit PWM carrier
const unsigned long VOICE_GAP_MS = 500;        // pause between phrases

void rgbFallFlash(unsigned long now);   // defined with the LIGHT code below

bool voicePlay() {
  if (BUZZER_PIN < 0 || BUZZER_ACTIVE) return true;
  // NOTE: this blocks ~1 s, so the main loop (and updateLight) doesn't run meanwhile:
  // the RGB fall blink is driven from inside the sample loop below, or it would freeze.
  ledcChangeFrequency(BUZZER_PIN, VOICE_PWM_HZ, 8);
  const unsigned long period = 1000000UL / VOICE_RATE;
  unsigned long t0 = micros();
  bool done = true;
  // Stop when the switch CHANGES position (works for a latching switch left in either
  // position, and for a push-button). The loop then registers it as "I'm OK".
  bool startLevel = digitalRead(BUTTON_PIN) == LOW;
  for (uint32_t i = 0; i < VOICE_LEN; i++) {
    while (micros() - t0 < i * period) {}
    ledcWrite(BUZZER_PIN, VOICE_DATA[i]);
    if ((i & 127) == 0) {                         // every 16 ms
      if (RGB_LED && buzzerAlarm) rgbFallFlash(millis());   // keep the red blink going while speaking
      if ((digitalRead(BUTTON_PIN) == LOW) != startLevel) { done = false; break; }
    }
  }
  ledcWrite(BUZZER_PIN, 0);
  ledcChangeFrequency(BUZZER_PIN, buzzerHz, 8);
  return done;
}

void buzzerService(unsigned long now) {
  static bool was = false;
  if (BUZZER_VOICE && !BUZZER_ACTIVE && BUZZER_PIN >= 0) {
    static unsigned long nextAt = 0;
    if (!buzzerAlarm) { nextAt = 0; return; }
    if (nextAt != 0 && (long)(now - nextAt) < 0) return;
    voicePlay();
    nextAt = millis() + VOICE_GAP_MS;
    return;
  }
  if (BUZZER_SIREN && !BUZZER_ACTIVE && BUZZER_PIN >= 0) {
    if (!buzzerAlarm) {
      if (was) { buzzerTone(false); was = false; }
      return;
    }
    static unsigned long lastStep = 0;
    if (was && now - lastStep < 15) return;          // retune every 15 ms
    lastStep = now;
    unsigned long u = now % SIREN_PERIOD_MS, half = SIREN_PERIOD_MS / 2;
    unsigned long frac = u < half ? u : SIREN_PERIOD_MS - u;   // up, then down
    uint32_t hz = SIREN_LOW_HZ + (uint32_t)((SIREN_HIGH_HZ - SIREN_LOW_HZ) * frac / half);
    ledcChangeFrequency(BUZZER_PIN, hz, 8);
    buzzerTone(true);
    was = true;
    return;
  }
  bool on = buzzerAlarm && (now % (BUZZ_ON_MS + BUZZ_OFF_MS)) < BUZZ_ON_MS;
  if (on != was) { buzzerTone(on); was = on; }
}

void onWorkerOk() {
  unsigned long now = millis();
  if (buzzerAlarm) {
    buzzerAlarm = false;
    buzzerTone(false);
    Serial.println("    BUZZER OFF (worker pressed \"I'M OK\")");
  }
  okId++;
  okSent++;
  bool linked = alertId > 0 && lastAlertMs != 0 && now - lastAlertMs < OK_LINKS_ALERT_MS;
  unsigned long forAlert = linked ? alertId : 0;
  unsigned long secsAfter = linked ? (now - lastAlertMs) / 1000 : 0;
  snprintf(okJson, sizeof(okJson),
           "{\"type\":\"worker_ok\",\"worker\":\"%s\",\"strap\":%d,\"boot\":%lu,\"id\":%lu,\"for_alert\":%lu,"
           "\"secs_after_alert\":%lu,\"uptime_ms\":%lu}",
           WORKER_ID, STRAP_ID, (unsigned long)bootId, okId, forAlert, secsAfter, now);
  okPending = true;
  okStartMs = now;
  lastOkSendMs = 0;              // send right away
  okFeedbackUntil = now + 1500;  // quick green flashes
  Serial.println();
  if (linked) Serial.printf("*** WORKER PRESSED \"I'M OK\" (#%lu), %lu s after fall alert #%lu -> NearStop\n", okId, secsAfter, forAlert);
  else        Serial.printf("*** WORKER PRESSED \"I'M OK\" (#%lu) -> NearStop\n", okId);
}

// Debounced: one message per press, however long it's held, at most one a second
// Debounced (30 ms). Momentary button: one "I'm OK" per press, however long it's held.
// Latching switch: it stays in whichever position it was left, so EVERY change of
// position (on -> off or off -> on) is one "I'm OK"; its resting position means nothing.
// The position at power-on is taken as the starting point (no message at boot).
bool buttonStable = false;   // debounced level (true = pin LOW)

void handleButton(unsigned long now) {
  static bool lastRaw = false, started = false;
  static unsigned long changedAt = 0, lastTrigger = 0;
  bool raw = digitalRead(BUTTON_PIN) == LOW;   // pressed / switched on = connected to GND
  if (!started) { started = true; lastRaw = buttonStable = raw; return; }
  if (raw != lastRaw) { lastRaw = raw; changedAt = now; }
  if (raw != buttonStable && now - changedAt >= 30) {
    buttonStable = raw;
    bool counts = BUTTON_LATCHING ? true : buttonStable;   // latching: any flip; momentary: press only
    if (counts && now - lastTrigger > 1000) {
      lastTrigger = now;
      onWorkerOk();
    }
  }
}

void serviceOk() {
  if (!okPending) return;
  if (millis() - okStartMs > OK_GIVE_UP_MS) {
    okPending = false;
    Serial.printf("    Gave up on \"I'm OK\" #%lu: no OKACK from NearStop in 30 s.\n", okId);
    return;
  }
  if (millis() - lastOkSendMs >= OK_RESEND_MS && linkUp()) {
    lastOkSendMs = millis();
    sendUdp(okJson);
  }
}

// ========================= ENVIRONMENT SENSORS =======================
// BMP180 temperature + air pressure (team's bmp180_test.ino) and the MQ-2 gas
// module (from tools/gas_test). Monitoring only: they never change the fall rule.
// BMP180 wiring: VIN -> 3V, GND -> GND, SDA -> SDA (22), SCL -> SCL (20), address
// 0x77 (the MPU-6050 is 0x68). restartImu() re-starts the I2C bus: that's fine,
// the BMP180's calibration stays in these variables and a failed read is retried.
// =================== BMP180 MODULE: copy from here ===================
const uint8_t BMP_ADDR = 0x77;
const uint8_t BMP_OSS = 3;                  // oversampling 0-3. 3 = least noise (the chip takes 25.5 ms)
const unsigned long BMP_EVERY_MS = 2000;    // one reading every 2 s (monitoring only)
const unsigned long BMP_RETRY_MS = 5000;    // if the BMP180 is missing, look for it again every 5 s
const unsigned long BMP_PRESS_WAIT_MS[4] = {6, 9, 15, 27};  // datasheet: 4.5 / 7.5 / 13.5 / 25.5 ms, plus margin

float bmpTempC = NAN, bmpPressureHpa = NAN;  // NAN = no valid reading
bool bmpFound = false;
int bmpFails = 0;

// Factory calibration, read from the chip once (every chip is different)
int16_t bmpAC1, bmpAC2, bmpAC3, bmpB1, bmpB2, bmpMB, bmpMC, bmpMD;
uint16_t bmpAC4, bmpAC5, bmpAC6;

enum BmpStep { BMP_IDLE, BMP_WAIT_TEMP, BMP_WAIT_PRESS };
BmpStep bmpStep = BMP_IDLE;
unsigned long bmpStepMs = 0, bmpLastStartMs = 0, bmpLastTryMs = 0;
bool bmpEverRead = false, bmpLostMsgShown = false;
int32_t bmpUT = 0;

bool bmpWrite8(uint8_t reg, uint8_t val) {
  Wire.beginTransmission(BMP_ADDR);
  Wire.write(reg);
  Wire.write(val);
  return Wire.endTransmission() == 0;
}

bool bmpReadBytes(uint8_t reg, uint8_t* out, int n) {
  Wire.beginTransmission(BMP_ADDR);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;   // no answer at 0x77
  if (Wire.requestFrom((int)BMP_ADDR, n) != n) return false;
  for (int i = 0; i < n; i++) out[i] = (uint8_t)Wire.read();
  return true;
}

// Finds the chip and reads its calibration. Returns true if it's ready.
bool bmpBegin() {
  bmpFound = false;
  bmpStep = BMP_IDLE;
  uint8_t id = 0;
  if (!bmpReadBytes(0xD0, &id, 1)) return false;
  if (id != 0x55) {
    if (id == 0x58) Serial.println("BMP180: the chip at 0x77 says it's a BMP280, not a BMP180. This code won't read it.");
    else if (id == 0x60) Serial.println("BMP180: the chip at 0x77 says it's a BME280, not a BMP180. This code won't read it.");
    else Serial.printf("BMP180: something answered at 0x77 but its ID is 0x%02X, not 0x55\n", id);
    return false;
  }
  uint8_t c[22];
  if (!bmpReadBytes(0xAA, c, 22)) return false;
  for (int i = 0; i < 22; i += 2) {
    uint16_t w = ((uint16_t)c[i] << 8) | c[i + 1];
    if (w == 0x0000 || w == 0xFFFF) {   // datasheet: no calibration word is ever 0 or 0xFFFF
      Serial.println("BMP180: calibration data looks wrong (loose wire?)");
      return false;
    }
  }
  bmpAC1 = (int16_t)((c[0] << 8) | c[1]);
  bmpAC2 = (int16_t)((c[2] << 8) | c[3]);
  bmpAC3 = (int16_t)((c[4] << 8) | c[5]);
  bmpAC4 = (uint16_t)((c[6] << 8) | c[7]);
  bmpAC5 = (uint16_t)((c[8] << 8) | c[9]);
  bmpAC6 = (uint16_t)((c[10] << 8) | c[11]);
  bmpB1 = (int16_t)((c[12] << 8) | c[13]);
  bmpB2 = (int16_t)((c[14] << 8) | c[15]);
  bmpMB = (int16_t)((c[16] << 8) | c[17]);
  bmpMC = (int16_t)((c[18] << 8) | c[19]);
  bmpMD = (int16_t)((c[20] << 8) | c[21]);
  bmpFound = true;
  bmpFails = 0;
  return true;
}

// Bosch datasheet formulas: raw readings -> temperature (0.1 C steps) and pressure (Pa).
// Returns false if the numbers can't be right (it would otherwise divide by zero and crash).
bool bmpCompute(int32_t ut, int32_t up, uint8_t oss, int32_t* temp10, int32_t* pressurePa) {
  int32_t x1 = ((ut - (int32_t)bmpAC6) * (int32_t)bmpAC5) >> 15;
  if (x1 + (int32_t)bmpMD == 0) return false;
  int32_t x2 = ((int32_t)bmpMC << 11) / (x1 + (int32_t)bmpMD);
  int32_t b5 = x1 + x2;
  *temp10 = (b5 + 8) >> 4;

  int32_t b6 = b5 - 4000;
  x1 = ((int32_t)bmpB2 * ((b6 * b6) >> 12)) >> 11;
  x2 = ((int32_t)bmpAC2 * b6) >> 11;
  int32_t x3 = x1 + x2;
  int32_t b3 = ((((int32_t)bmpAC1 * 4 + x3) << oss) + 2) / 4;
  x1 = ((int32_t)bmpAC3 * b6) >> 13;
  x2 = ((int32_t)bmpB1 * ((b6 * b6) >> 12)) >> 16;
  x3 = ((x1 + x2) + 2) >> 2;
  uint32_t b4 = ((uint32_t)bmpAC4 * (uint32_t)(x3 + 32768)) >> 15;
  if (b4 == 0) return false;
  uint32_t b7 = ((uint32_t)up - (uint32_t)b3) * (uint32_t)(50000UL >> oss);
  int32_t p = (b7 < 0x80000000UL) ? (int32_t)((b7 * 2) / b4) : (int32_t)((b7 / b4) * 2);
  x1 = (p >> 8) * (p >> 8);
  x1 = (x1 * 3038) >> 16;
  x2 = (-7357 * p) >> 16;
  *pressurePa = p + ((x1 + x2 + 3791) >> 4);
  return true;
}

void bmpFailed(const char* what) {
  bmpStep = BMP_IDLE;
  bmpFails++;
  if (bmpFails >= 3) {
    bmpTempC = NAN;
    bmpPressureHpa = NAN;
    bmpFound = false;   // look for it again every 5 s
    if (!bmpLostMsgShown) {
      Serial.printf("BMP180: %s 3 times in a row - check its wires (SDA, SCL, 3V, GND)\n", what);
      bmpLostMsgShown = true;
    }
  }
}

// Call every loop. Never waits: each call does at most one short I2C transfer.
void bmpUpdate(unsigned long now) {
  if (!bmpFound) {
    if (now - bmpLastTryMs < BMP_RETRY_MS) return;
    bmpLastTryMs = now;
    if (!bmpBegin()) return;
    Serial.println(bmpEverRead ? "BMP180: found again" : "BMP180: found at 0x77");
  }
  switch (bmpStep) {
    case BMP_IDLE:
      if (bmpEverRead && now - bmpLastStartMs < BMP_EVERY_MS) return;
      if (!bmpWrite8(0xF4, 0x2E)) { bmpFailed("no reply"); return; }   // start a temperature reading
      bmpLastStartMs = now;
      bmpStepMs = now;
      bmpStep = BMP_WAIT_TEMP;
      break;

    case BMP_WAIT_TEMP: {
      if (now - bmpStepMs < 6) return;   // datasheet: 4.5 ms
      uint8_t d[2];
      if (!bmpReadBytes(0xF6, d, 2)) { bmpFailed("no reply"); return; }
      bmpUT = ((int32_t)d[0] << 8) | d[1];
      if (!bmpWrite8(0xF4, (uint8_t)(0x34 + (BMP_OSS << 6)))) { bmpFailed("no reply"); return; }  // start pressure
      bmpStepMs = now;
      bmpStep = BMP_WAIT_PRESS;
      break;
    }

    case BMP_WAIT_PRESS: {
      if (now - bmpStepMs < BMP_PRESS_WAIT_MS[BMP_OSS]) return;
      uint8_t d[3];
      if (!bmpReadBytes(0xF6, d, 3)) { bmpFailed("no reply"); return; }
      int32_t up = (((int32_t)d[0] << 16) | ((int32_t)d[1] << 8) | d[2]) >> (8 - BMP_OSS);
      int32_t t10 = 0, pa = 0;
      bool ok = bmpCompute(bmpUT, up, BMP_OSS, &t10, &pa);
      bmpStep = BMP_IDLE;
      if (!ok || t10 < -400 || t10 > 850 || pa < 30000 || pa > 110000) {   // outside what the chip can measure
        bmpFailed("impossible readings (loose wire?)");
        return;
      }
      if (bmpLostMsgShown) Serial.println("BMP180: readings are back");
      bmpLostMsgShown = false;
      bmpFails = 0;
      bmpEverRead = true;
      bmpTempC = t10 / 10.0f;
      bmpPressureHpa = pa / 100.0f;
      break;
    }
  }
}
// =================== BMP180 MODULE: copy to here =====================


// MQ-2: levels from the sensor resistance compared with clean air (1.00 = clean;
// flammable gas makes it drop). Relative to clean air: NOT ppm or %LEL.
const float GAS_SUPPLY_V = 5.0;
const unsigned long GAS_BASELINE_MS = 3000;    // after warm-up, average 3 s of clean air (was 10 s)
// The heater is still settling for the first couple of minutes, so the clean-air value
// tracks small changes quickly then (a big drop = real gas still alarms), slowly after.
const unsigned long MQ2_SETTLE_S = 180;
const float GAS_FAST_FOLLOW = 0.01;            // per 100 ms sample (~10 s time constant)
const float GAS_FAST_FOLLOW_MIN_RATIO = 0.75;  // only follow changes smaller than this
const float GAS_YELLOW_RATIO = 0.60;
const float GAS_RED_RATIO    = 0.35;
const float GAS_HYST         = 0.10;
const unsigned long GAS_CONFIRM_MS = 1000;     // a higher level must hold 1 s
const unsigned long GAS_RELAX_MS   = 3000;     // a lower level must hold 3 s
// Sensor fault: after warm-up the output must be between these (sensor volts). The
// ESP32 can't read below ~0.14 V on the pin (= 0.21 V here), so "0 V" shows as 0.21 V.
const float GAS_MIN_V = 0.30;                  // below: MQ-2 not powered / AO or GND not connected
const float GAS_MAX_V = 4.60;                  // above: pin at its maximum (divider missing?)
bool gasFault = false, gasBadNow = false;
unsigned long gasBadSince = 0;
enum GasLevel { GAS_WARMING = -1, GAS_GREEN = 0, GAS_YELLOW = 1, GAS_RED = 2 };
GasLevel gasLevel = GAS_WARMING, gasPending = GAS_GREEN;
float gasVolts = 0, gasBaseVolts = 0, gasRatio = 1.0;
bool gasBaselineDone = false;
unsigned long gasBaseStartMs = 0, gasPendingSince = 0;
double gasBaseSum = 0;
int gasBaseN = 0;

const char* gasLevelName(int l) {   // int, not GasLevel: Arduino puts its prototype above the enum
  switch (l) {
    case GAS_GREEN:  return "green";
    case GAS_YELLOW: return "yellow";
    case GAS_RED:    return "red";
    default:         return "warming";
  }
}

#if ENV_SENSORS
// Sensor resistance is proportional to (Vsupply - Vout) / Vout; the module's load
// resistor cancels out because we only compare with clean air.
float gasResistanceFactor(float v) {
  if (v < 0.01) v = 0.01;
  if (v > GAS_SUPPLY_V - 0.01) v = GAS_SUPPLY_V - 0.01;
  return (GAS_SUPPLY_V - v) / v;
}

void gasStartBaseline(unsigned long now) {
  gasBaselineDone = false;
  gasBaseStartMs = now;
  gasBaseSum = 0;
  gasBaseN = 0;
  gasLevel = GAS_WARMING;
  Serial.printf("GAS: learning clean air for %lu s - keep gas sources away...\n", GAS_BASELINE_MS / 1000);
}

void gasService(unsigned long now) {   // 10 samples a second
  static unsigned long lastSample = 0;
  if (now - lastSample < 100) return;
  lastSample = now;
  float v = analogReadMilliVolts(MQ2_PIN) / 1000.0 * MQ2_DIVIDER;
  gasVolts = (gasVolts == 0) ? v : gasVolts + 0.2 * (v - gasVolts);   // smooth ADC noise

  // Wiring check (after warm-up): ~0 V or pinned at the top for 3 s = sensor fault.
  // Never report a dead sensor as "clean air".
  if ((long)(now - gasBaseStartMs) >= 0) {
    bool bad = gasVolts < GAS_MIN_V || gasVolts > GAS_MAX_V;
    if (bad != gasBadNow) { gasBadNow = bad; gasBadSince = now; }
    if (!gasFault && bad && now - gasBadSince >= 3000) {
      gasFault = true;
      Serial.printf("GAS: SENSOR FAULT - MQ-2 reads %.2f V. %s\n", gasVolts, gasVolts < GAS_MIN_V
                    ? "~0 V: check MQ-2 VCC -> USB pin (5 V, board on USB), GND -> Feather GND, AO -> 10k -> GPIO 34."
                    : "At the maximum: check the 10k/20k divider (AO must not go straight to GPIO 34).");
    } else if (gasFault && !bad && now - gasBadSince >= 3000) {
      gasFault = false;
      Serial.println("GAS: sensor reading again - warming up and re-learning clean air");
      gasStartBaseline(now + (unsigned long)MQ2_WARMUP_S * 1000UL);
    }
    if (gasFault) return;
  }

  if (!gasBaselineDone) {                       // warm-up, then learn clean air
    if ((long)(now - gasBaseStartMs) < 0) return;
    gasBaseSum += gasVolts;
    gasBaseN++;
    if (now - gasBaseStartMs >= GAS_BASELINE_MS) {
      gasBaseVolts = gasBaseSum / gasBaseN;
      gasBaselineDone = true;
      gasLevel = gasPending = GAS_GREEN;
      Serial.printf("GAS: clean air = %.2f V. Monitoring.\n", gasBaseVolts);
      if (gasBaseVolts < 0.10) Serial.println("GAS: WARNING clean-air reading is ~0 V. Check VCC (5 V), AO and the divider.");
      if (gasBaseVolts > 3.5)  Serial.println("GAS: WARNING clean-air reading is very high. Gas/alcohol nearby? Type b in clean air.");
    }
    return;
  }

  gasRatio = gasResistanceFactor(gasVolts) / gasResistanceFactor(gasBaseVolts);
  GasLevel target;
  if (gasRatio <= GAS_RED_RATIO || (gasLevel == GAS_RED && gasRatio <= GAS_RED_RATIO + GAS_HYST)) target = GAS_RED;
  else if (gasRatio <= GAS_YELLOW_RATIO || (gasLevel >= GAS_YELLOW && gasRatio <= GAS_YELLOW_RATIO + GAS_HYST)) target = GAS_YELLOW;
  else target = GAS_GREEN;
  if (target != gasPending) { gasPending = target; gasPendingSince = now; }
  unsigned long needed = (target > gasLevel) ? GAS_CONFIRM_MS : GAS_RELAX_MS;
  if (target != gasLevel && now - gasPendingSince >= needed) {
    gasLevel = target;
    Serial.printf(">>> GAS %s  (sensor %.2f V, resistance %.0f%% of clean air)\n",
                  gasLevelName(gasLevel), gasVolts, gasRatio * 100);
  }
  if (now < MQ2_SETTLE_S * 1000UL && gasLevel != GAS_RED && gasRatio > GAS_FAST_FOLLOW_MIN_RATIO && gasRatio < 1.0 / GAS_FAST_FOLLOW_MIN_RATIO)
    gasBaseVolts += GAS_FAST_FOLLOW * (gasVolts - gasBaseVolts);    // heater still settling: follow it quickly
  else if (gasLevel == GAS_GREEN && gasRatio > 0.9) gasBaseVolts += 0.0005 * (gasVolts - gasBaseVolts);   // follow slow drift
}

void envService() {
  unsigned long now = millis();
  bmpUpdate(now);
  gasService(now);
}

void envSetup() {
  analogSetPinAttenuation(MQ2_PIN, ADC_11db);   // 0 - ~3.1 V on the pin
  gasBaseStartMs = millis() + (unsigned long)MQ2_WARMUP_S * 1000UL;
  if (bmpBegin()) Serial.println("BMP180 found at 0x77 (temperature + air pressure)");
  else Serial.println("BMP180 NOT found at 0x77 - check VIN->3V, GND, SDA, SCL. Retrying every 5 s");
  Serial.printf("Environment: BMP180 on I2C, MQ-2 on GPIO %d (warming up %d s, then learns clean air)\n",
                MQ2_PIN, MQ2_WARMUP_S);
}
#else
void envService() {}
void envSetup() {}
void gasStartBaseline(unsigned long) {}
#endif

// ',"temp_c":23.0,"pressure_hpa":1013.25,"gas_mv":612,"gas_ratio":0.97,"gas_level":"green","gas_warm":true'
void envJson(char* out, size_t n) {
  if (!ENV_SENSORS) { out[0] = 0; return; }
  char t[12], h[14], ratio[12];
  if (isnan(bmpTempC)) strcpy(t, "null"); else snprintf(t, sizeof(t), "%.1f", bmpTempC);
  if (isnan(bmpPressureHpa)) strcpy(h, "null"); else snprintf(h, sizeof(h), "%.2f", bmpPressureHpa);
  if (gasBaselineDone && !gasFault) snprintf(ratio, sizeof(ratio), "%.2f", gasRatio); else strcpy(ratio, "null");
  snprintf(out, n, ",\"temp_c\":%s,\"pressure_hpa\":%s,\"gas_mv\":%ld,\"gas_ratio\":%s,\"gas_level\":\"%s\",\"gas_warm\":%s",
           t, h, lround(gasVolts * 1000), ratio, gasFault ? "fault" : gasLevelName(gasLevel),
           gasBaselineDone ? "true" : "false");
}

void sendStatus(float g) {
  static unsigned long last = 0;
  if (millis() - last < (bridgeActive() ? STATUS_EVERY_MS : STATUS_EVERY_WIFI_MS)) return;
  last = millis();
  char env[150];
  envJson(env, sizeof(env));
  char buf[440];
  const char* zn = zone == 2 ? "red" : zone == 1 ? "yellow" : zone == 0 ? "green" : "none";
  snprintf(buf, sizeof(buf),
           "{\"type\":\"status\",\"worker\":\"%s\",\"strap\":%d,\"zone\":\"%s\",\"machine\":%d,\"machine_id\":\"%s\","
           "\"distance_cm\":%.0f,\"g\":%.2f,\"imu\":%s,\"near_led\":%s,\"alert_pending\":%s,\"falls\":%lu%s}",
           WORKER_ID, STRAP_ID, zn, closestId, MACHINE_NAMES[closestId], closestId ? closestCm : -1.0f, g,
           (imuOkMs != 0 && millis() - imuOkMs < 1000) ? "true" : "false",
           zone >= 1 ? "true" : "false", alertPending ? "true" : "false", fallsTotal, env);
  sendUdp(buf);
}

// ============================== LIGHT ================================
// External RGB LED: one PWM channel per colour (5 kHz, 8-bit). Same colours as the NeoPixel,
// at full brightness so it shows through the enclosure.
void rgbSetup() {
  if (!RGB_LED) return;
  ledcAttach(RGB_R_PIN, 5000, 8);
  ledcAttach(RGB_G_PIN, 5000, 8);
  ledcAttach(RGB_B_PIN, 5000, 8);
}

// Colours the RGB LED cycles through during a fall alarm (changes every 150 ms)
const uint8_t FALL_COLOURS[][3] = {{255, 0, 0}, {0, 0, 255}, {255, 255, 255}, {0, 255, 0}, {255, 0, 255}, {255, 90, 0}};

void rgbWrite(uint8_t r, uint8_t g, uint8_t b) {
  if (RGB_COMMON_ANODE) { r = 255 - r; g = 255 - g; b = 255 - b; }
  ledcWrite(RGB_R_PIN, r);
  ledcWrite(RGB_G_PIN, g);
  ledcWrite(RGB_B_PIN, b);
}

// After a fall: BLINKING RED (on 150 ms / off 150 ms) until "I'm OK".
const unsigned long RGB_BLINK_MS = 150;
void rgbFallFlash(unsigned long now) {
  if ((now / RGB_BLINK_MS) % 2) rgbWrite(255, 0, 0); else rgbWrite(0, 0, 0);
}

void rgbShow(uint32_t c) {
  if (!RGB_LED) return;
  // Enclosure light: OFF normally; solid RED from a confirmed fall until "I'm OK".
  if (buzzerAlarm) rgbFallFlash(millis()); else rgbWrite(0, 0, 0);
  return;
  uint8_t r = (c >> 16) & 0xFF, g = (c >> 8) & 0xFF, b = c & 0xFF;
  if (r == 255 && g == 160 && b == 0) g = 70;   // yellow: discrete LEDs' green is much brighter
  if (RGB_COMMON_ANODE) { r = 255 - r; g = 255 - g; b = 255 - b; }
  ledcWrite(RGB_R_PIN, r);
  ledcWrite(RGB_G_PIN, g);
  ledcWrite(RGB_B_PIN, b);
}

void updateLight() {
  uint32_t c;
  if (millis() < okFeedbackUntil) {
    c = ((millis() / 100) % 2) ? pixel.Color(0, 255, 0) : 0;   // quick green flashes = "I'm OK" registered
  } else if (alertPending) {
    c = ((millis() / 125) % 2) ? pixel.Color(255, 0, 0) : pixel.Color(255, 255, 255);
  } else if (zone == 2) c = pixel.Color(255, 0, 0);
  else if (zone == 1)   c = pixel.Color(255, 160, 0);
  else if (zone == 0)   c = pixel.Color(0, 255, 0);
  else                  c = pixel.Color(0, 0, 255);
  pixel.setPixelColor(0, c);
  pixel.show();
  rgbShow(c);
  if (FLASH_LED_PIN >= 0) {
    // Enclosure light (KY-034): ONLY after a fall. On (it cycles its colours by itself)
    // from the confirmed fall until the worker answers "I'm OK" (same as the voice alarm);
    // off the rest of the time.
    digitalWrite(FLASH_LED_PIN, buzzerAlarm ? HIGH : LOW);
  }
  digitalWrite(NEAR_LED_PIN, zone >= 1 ? HIGH : LOW);   // close to an anchor -> LED on
}

// =============================== IMU =================================
void writeReg(uint8_t reg, uint8_t val) {
  Wire.beginTransmission(MPU);
  Wire.write(reg);
  Wire.write(val);
  Wire.endTransmission();
}

uint8_t readReg(uint8_t reg) {
  Wire.beginTransmission(MPU);
  Wire.write(reg);
  Wire.endTransmission(false);
  Wire.requestFrom((int)MPU, 1);
  return Wire.available() ? Wire.read() : 0xFF;
}

unsigned long imuRestartMs = 0;
unsigned long imuOkMs = 0;        // last valid motion reading (status "imu": false after 1 s without)   // readings right after a restart are garbage: ignored for IMU_SETTLE_MS
const unsigned long IMU_SETTLE_MS = 500;

void restartImu() {
  imuRestartMs = millis();
  Wire.end();
  delay(10);
  Wire.begin(SDA, SCL);
  writeReg(0x6B, 0x80);   // full reset
  delay(100);
  writeReg(0x6B, 0x00);   // wake up
  delay(100);
  writeReg(0x1C, 0x18);   // +/-16 g
  writeReg(0x1A, 0x03);   // smoothing ~44 Hz
}

int imuRestarts = 0;

float readTotalG() {
  static unsigned long lastCheck = 0;
  if (millis() - lastCheck > 1000) {
    lastCheck = millis();
    uint8_t pm = readReg(0x6B);
    if (pm == 0xFF || (pm & 0x40)) {
      imuRestarts++;
      Serial.printf("IMU had reset (loose wire?) - restarting it  [#%d]\n", imuRestarts);
      restartImu();
    }
  }
  Wire.beginTransmission(MPU);
  Wire.write(0x3B);
  Wire.endTransmission(false);
  Wire.requestFrom((int)MPU, 6);
  if (Wire.available() < 6) return -1;
  int16_t rx = (Wire.read() << 8) | Wire.read();
  int16_t ry = (Wire.read() << 8) | Wire.read();
  int16_t rz = (Wire.read() << 8) | Wire.read();

  static int sameCount = 0;
  static int16_t px = 0, py = 0, pz = 0;
  if (rx == px && ry == py && rz == pz) sameCount++; else sameCount = 0;
  px = rx; py = ry; pz = rz;
  if (sameCount >= 50) {
    imuRestarts++;
    Serial.printf("IMU frozen - restarting it  [#%d]\n", imuRestarts);
    restartImu();
    sameCount = 0;
  }
  // A sensor that lost power/its wire reads all zeros (impossible: gravity is always ~1 g),
  // and right after a restart it can read nonsense: never feed those to the fall detector.
  if (rx == 0 && ry == 0 && rz == 0) return -1;
  if (imuRestartMs != 0 && millis() - imuRestartMs < IMU_SETTLE_MS) return -1;
  float ax = rx / 2048.0, ay = ry / 2048.0, az = rz / 2048.0;
  return sqrt(ax * ax + ay * ay + az * az);
}

// ======================= SERIAL (bridge + tests) =====================
void handleSerial() {
  static char line[300];
  static int len = 0;
  while (Serial.available()) {
    char ch = (char)Serial.read();
    if (ch == '\n' || ch == '\r') {
      if (len == 0) continue;
      line[len] = 0;
      len = 0;
      if (line[0] == '{' || strncmp(line, "ACK", 3) == 0 || strncmp(line, "OKACK", 5) == 0) {
        handleNearStopText(line);                 // from the NearStop USB bridge
      } else if ((line[0] == 'o' || line[0] == 'O') && (line[1] == 'k' || line[1] == 'K') && line[2] == 0) {
        Serial.println("TEST: fake \"I'm OK\" button press");
        onWorkerOk();
      } else if (strcmp(line, "light") == 0) {
        if (RGB_LED) {
          Serial.println("TEST: RGB LED red for 3 s (what it shows after a fall)");
          for (unsigned long t0 = millis(); millis() - t0 < 3000; ) { rgbFallFlash(millis()); delay(10); }
          rgbWrite(0, 0, 0);
          Serial.println("TEST: light OFF");
        } else if (FLASH_LED_PIN < 0) Serial.println("TEST: no light configured (FLASH_LED_PIN -1)");
        else {
          Serial.printf("TEST: light ON for 3 s (GPIO %d HIGH) - does the LED cycle colours?\n", FLASH_LED_PIN);
          digitalWrite(FLASH_LED_PIN, HIGH);
          delay(3000);
          digitalWrite(FLASH_LED_PIN, LOW);
          Serial.println("TEST: light OFF");
        }
      } else if (strcmp(line, "say") == 0) {
        Serial.println("TEST: saying \"Are you okay?\"");
        voicePlay();
      } else if (strcmp(line, "sweep") == 0) {
        Serial.println("BUZZER sweep: listen for the loudest, then set BUZZER_HZ in config.h");
        for (uint32_t hz = 1800; hz <= 3000; hz += 100) buzzerTry(hz);
        buzzerSetHz(BUZZER_HZ);
        Serial.printf("BUZZER sweep done (back to %d Hz)\n", BUZZER_HZ);
      } else if (strncmp(line, "hz ", 3) == 0) {
        buzzerTry(strtoul(line + 3, nullptr, 10));   // stays at this tone until reboot
      } else if ((line[0] == 'b' || line[0] == 'B') && line[1] == 0) {
        gasStartBaseline(millis());
      } else if ((line[0] == 's' || line[0] == 'S') && line[1] == 0) {
        Serial.println("GAS: skipping the warm-up (testing only)");
        gasStartBaseline(millis());
      } else if (line[0] == 'f' || line[0] == 'F') {
        Serial.println("TEST: fake fall, 5.0 g");
        onFallConfirmed(5.0);
      } else if (line[0] == 'i' || line[0] == 'I') {
        Serial.println("TEST: fake impact, 5.0 g (fall not confirmed)");
        onImpactDetected(5.0);
      } else {
        int id = 0, cm = 0;
        if (sscanf(line, "%d %d", &id, &cm) == 2) onMachineDistance(id, cm);
      }
    } else if (len < (int)sizeof(line) - 1) {
      line[len++] = ch;
    } else {
      len = 0;   // line too long: drop it
    }
  }
}

// ---- "I'm here" radio beacon ---------------------------------------------
// Machines listen for this to know Sam is NEAR them (no distance needed): with
// no antennas the radio only carries a short way, so hearing it = nearby.
const uint8_t BEACON_PEER[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
const unsigned long BEACON_MS = 100;            // 10 beacons a second
bool beaconReady = false;

void startEspNow() {
  if (esp_now_init() != ESP_OK) { Serial.println("ESP-NOW failed to start (beacon + machine radio off)"); espnowStarted = true; return; }
  esp_now_register_recv_cb(onReceive);
  esp_now_peer_info_t peer = {};
  memcpy(peer.peer_addr, BEACON_PEER, 6);
  peer.channel = 0;                              // = the radio's current channel
  peer.ifidx = WIFI_IF_STA;
  peer.encrypt = false;
  beaconReady = esp_now_add_peer(&peer) == ESP_OK;
  Serial.printf("ESP-NOW on (channel %d): sending the 'Sam is here' beacon %s\n",
                WiFi.channel(), beaconReady ? "10x a second" : "FAILED");
  espnowStarted = true;
}

void sendBeacon(unsigned long now) {
  static unsigned long last = 0;
  if (!beaconReady || now - last < BEACON_MS) return;
  last = now;
  char msg[12];
  int len = snprintf(msg, sizeof(msg), "NSB:%s", WORKER_ID);   // e.g. "NSB:W1"
  esp_now_send(BEACON_PEER, (const uint8_t*)msg, len);
}

// While the USB bridge is connected and Wi-Fi isn't, stop Wi-Fi from scanning
// channels (scanning would make machines miss the beacon); park on ESPNOW_CHANNEL.
bool wifiParked = false;
void manageRadio(bool connected) {
  if (bridgeActive() && !connected && !wifiParked) {
    WiFi.setAutoReconnect(false);
    WiFi.disconnect();
    esp_wifi_set_channel(ESPNOW_CHANNEL, WIFI_SECOND_CHAN_NONE);
    wifiParked = true;
    Serial.printf("[LINK] USB bridge: Wi-Fi parked, radio fixed on channel %d for the beacon\n", ESPNOW_CHANNEL);
  } else if (!bridgeActive() && wifiParked) {
    wifiParked = false;
    WiFi.setAutoReconnect(true);
    WiFi.begin(WIFI_NAME, WIFI_PASSWORD);
    Serial.println("[LINK] USB bridge gone: reconnecting Wi-Fi");
  }
}

// =============================== SETUP ===============================
void setup() {
  Serial.begin(115200);
  delay(1500);
  Serial.println();
  Serial.println("=== WORKER STRAP for NearStop: every fall -> alert; fall in red zone -> lock out machine ===");
  bootId = esp_random();

  pinMode(2, OUTPUT);       // Feather V2: power for the NeoPixel and I2C pull-ups
  digitalWrite(2, HIGH);
  pinMode(NEAR_LED_PIN, OUTPUT);
  pinMode(BUTTON_PIN, INPUT_PULLUP);   // "I'm OK" button to GND: LOW when pressed
  buzzerSetup();
  delay(50);
  pixel.begin();
  pixel.setBrightness(40);
  rgbSetup();
  if (FLASH_LED_PIN >= 0) { pinMode(FLASH_LED_PIN, OUTPUT); digitalWrite(FLASH_LED_PIN, LOW); }
  updateLight();

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  esp_wifi_set_channel(ESPNOW_CHANNEL, WIFI_SECOND_CHAN_NONE);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_NAME, WIFI_PASSWORD);
  nearstopIpFixed = strlen(NEARSTOP_IP) > 0 && nearstopIp.fromString(NEARSTOP_IP);
  udp.begin(MY_PORT);

  Serial.print("THIS STRAP'S MAC ADDRESS: ");
  Serial.println(WiFi.macAddress());
  Serial.printf("Worker %s. Connecting to Wi-Fi \"%s\" (NearStop on port %d)...\n", WORKER_ID, WIFI_NAME, NEARSTOP_PORT);

  Wire.begin(SDA, SCL);
  uint8_t who = readReg(0x75);
  Serial.printf("IMU chip ID 0x%02X %s\n", who, (who == 0xFF || who == 0x00) ? "- NOT ANSWERING, check wiring" : "- OK");
  restartImu();
  envSetup();

  Serial.println("Test: type  2 40  (fake Conveyor at 40 cm), or  f  (fake fall). Then Enter.");
  Serial.println("-------------------------------------------------------------------------");
}

// =============================== LOOP ================================
float lastG = 1.0;

void loop() {
  unsigned long now = millis();
  handleSerial();

  static bool wasConnected = false;
  bool connected = WiFi.status() == WL_CONNECTED;
  if (connected && !wasConnected) {
    Serial.printf("Wi-Fi CONNECTED: my IP %s, channel %d\n", WiFi.localIP().toString().c_str(), WiFi.channel());
    if (WiFi.channel() != ESPNOW_CHANNEL)
      Serial.printf("!!! Wi-Fi is on channel %d but ESPNOW_CHANNEL is %d. Set ESPNOW_CHANNEL %d in config.h.\n",
                    WiFi.channel(), ESPNOW_CHANNEL, WiFi.channel());
  }
  if (!connected && wasConnected) Serial.println("Wi-Fi LOST - reconnecting...");
  wasConnected = connected;
  manageRadio(connected);
  if (!espnowStarted && (connected || wifiParked || now > ESPNOW_FALLBACK_MS)) startEspNow();
  sendBeacon(now);   // "Sam is here" for the machines (anchors) to hear, with its signal strength

  float g = readTotalG();
  static unsigned long lastGoodG = 0;
  if (g >= 0) {
    // Coming back after a sensor dropout: start the fall detector fresh, so a jump
    // between garbage and real readings can't look like a fall.
    if (lastGoodG != 0 && now - lastGoodG > 200) { phase = PH_IDLE; inFreefall = false; }
    lastGoodG = now;
    imuOkMs = now;
    lastG = g;
    if (fallUpdate(now, g)) onFallConfirmed(peakG);
  }

  handleButton(now);
  buzzerService(now);
  updateZone();
  envService();
  serviceAlert();
  sendStatus(lastG);
  updateLight();

  static unsigned long lastPrint = 0;
  if (now - lastPrint >= 1000) {
    lastPrint = now;
    const char* zn = zone == 2 ? "RED" : zone == 1 ? "YELLOW" : zone == 0 ? "GREEN" : "NO DATA";
    if (imuOkMs != 0 && now - imuOkMs < 1000) Serial.printf("g %.2f | zone %-7s | LED %s |", lastG, zn, zone >= 1 ? "ON " : "off");
    else Serial.printf("g -- (MOTION SENSOR NOT ANSWERING: fall detection OFF) | zone %-7s | LED %s |", zn, zone >= 1 ? "ON " : "off");
    for (int i = 1; i <= 3; i++) {
      unsigned long seen = machineSeenMs[i];
      if (seen != 0 && now - seen < STALE_MS) Serial.printf(" %s %4.0fcm", MACHINE_NAMES[i], (float)machineCm[i]);
      else Serial.printf(" %s   --  ", MACHINE_NAMES[i]);
    }
    Serial.printf(" | link %s | falls %lu, alerts %lu", bridgeActive() ? "USB" : connected ? "WiFi" : "--",
                  fallsTotal, alertsSent);
    if (ENV_SENSORS) {
      if (isnan(bmpTempC)) Serial.print(" | T/P --");
      else Serial.printf(" | %.1fC %.1fhPa", bmpTempC, bmpPressureHpa);
      if (gasFault) Serial.printf(" | gas SENSOR FAULT %.2fV", gasVolts);
      else if (gasBaselineDone) Serial.printf(" | gas %s %.2fV %.0f%%", gasLevelName(gasLevel), gasVolts, gasRatio * 100);
      else Serial.printf(" | gas warming %.2fV", gasVolts);
    }
    if (unknownPackets) Serial.printf(" | %lu radio msgs NOT understood (len %d)", (unsigned long)unknownPackets, lastUnknownLen);
    Serial.println();
  }

  delay(10);  // ~100 readings per second
}
