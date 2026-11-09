/*
 * NearStop MOTOR machine node (Conveyor MA / Robot Arm GW): motor + HC-SR04,
 * USB or Wi-Fi. Board: Adafruit Feather ESP32 V2, core 3.x. Settings: config.h.
 *
 * Same code as firmware/hoist_node (team's tested Part 1 motor + interrupt-timed
 * echo), but with the NORMAL machine rules and these boards' pins:
 *   motor gate A1/GPIO25 (220R series, 10k gate->GND), HC-SR04 TRIG A0/GPIO26,
 *   ECHO -> 1k -> A2/GPIO34 with 2k(2.2k) A2->GND  (sensor wiring unchanged).
 *
 *   Running -> RUN_PCT    Slowed (someone within warning) -> SLOW_PCT
 *   Stopped (someone within danger; latched by NearStop) / Holding / Locked -> OFF instantly
 *   Sensor fault -> OFF.  NearStop commands latch until "resume" (Approve restart).
 *
 * Choose the machine at build time: -DDEVICE_ID=\"GW\" or \"MA\" (default in config.h).
 */

#include <WiFi.h>
#include <WiFiUdp.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include "driver/gpio.h"
#include "config.h"

// Self-check: read the motor pin's real level, so the Serial Monitor shows
// whether the PWM is actually leaving the board ("# [PWM] ...").
void reportMotorPin() {
  const int N = 4000;
  int hi = 0, edges = 0, prev = gpio_get_level((gpio_num_t)MOTOR_PIN);
  for (int i = 0; i < N; i++) {
    int v = gpio_get_level((gpio_num_t)MOTOR_PIN);
    hi += v;
    if (v != prev) edges++;
    prev = v;
  }
  Serial.printf("# [PWM] motor pin GPIO%d: high %.0f%% of the time, %d edges\n", MOTOR_PIN, 100.0f * hi / N, edges);
}

const int NEOPIXEL_PIN = 0;
const int NEOPIXEL_POWER = 2;

// ---------------- motor (team's Part 1) ----------------
const int PWM_FREQ = 20000;
const int PWM_BITS = 8;
const int MIN_SPIN = 25;
#ifndef MAX_PCT
#define MAX_PCT 100                    // motor rated V / supply V * 100 (5 V motor on 5 V)
#endif
int currentPct = 0, targetPct = 0;

void writeMotor(int pct) {
  pct = constrain(pct, 0, 100);
  ledcWrite(MOTOR_PIN, map(pct, 0, 100, 0, 255 * MAX_PCT / 100));
}
void stopMotorNow() { targetPct = 0; currentPct = 0; writeMotor(0); }

// ---------------- link timing ----------------
const unsigned long REPORT_MS = 200;
const unsigned long BRIDGE_TIMEOUT_MS = 3000;
const unsigned long WIFI_RETRY_MS = 20000;
const uint32_t PING_MS = 60;           // HC-SR04 needs >= 60 ms between pings
const int FAULTS_TO_ERROR = 3;         // failed pings in a row = sensor fault

WiFiUDP udp;
IPAddress serverAddr;
bool fixedServer = false, udpStarted = false;

int warningCm = DEFAULT_WARNING_CM;
int dangerCm = DEFAULT_DANGER_CM;
String override = "";                  // "stopped" / "holding" / "locked" from NearStop
String stateNow = "";
unsigned long lastReport = 0, lastBridgePing = 0, lastWifiTry = 0;
bool bridgeSeen = false;
char rxLine[256];
size_t rxLen = 0;

// ---------------- HC-SR04, interrupt timed (never blocks the loop) ----------------
volatile uint32_t riseUs = 0, pulseUs = 0;
volatile bool gotRise = false, gotPulse = false;
bool sensorFault = true;               // until the first good ping
int faultCount = 0;
int lastDist[3] = {-1, -1, -1};
int distIdx = 0;
int distCm = -1;                       // median of the last 3 (-1 = nothing in range)

void IRAM_ATTR echoISR() {
  uint32_t t = micros();
  if (digitalRead(ECHO_PIN)) { riseUs = t; gotRise = true; }
  else if (gotRise)          { pulseUs = t - riseUs; gotPulse = true; }
}

int medianOf3(int a, int b, int c) {
  if (a > b) { int t = a; a = b; b = t; }
  if (b > c) { int t = b; b = c; c = t; }
  if (a > b) { int t = a; a = b; b = t; }
  return b;
}

void sensorFailed(const char* why) {
  if (++faultCount >= FAULTS_TO_ERROR && !sensorFault) {
    sensorFault = true;
    if (PRESENCE_STOP) stopMotorNow();   // presence-free machines keep running (NearStop still shows the fault)
    Serial.printf("# !!! SENSOR FAULT (%s) - motor stopped. Check wiring/divider.\n", why);
  }
}

void gotDistance(int cm) {             // cm < 0 = nothing in range (> ~4 m)
  faultCount = 0;
  if (sensorFault) { sensorFault = false; Serial.println("# sensor OK"); }
  lastDist[distIdx] = cm;
  distIdx = (distIdx + 1) % 3;
  distCm = medianOf3(lastDist[0], lastDist[1], lastDist[2]);
}

void updateSensor(uint32_t now) {
  static uint32_t pingAt = 0;
  static bool waiting = false;
  if (!waiting && now - pingAt >= PING_MS) {
    gotRise = gotPulse = false;
    digitalWrite(TRIG_PIN, HIGH);
    delayMicroseconds(10);
    digitalWrite(TRIG_PIN, LOW);
    pingAt = now;
    waiting = true;
    return;
  }
  if (!waiting) return;
  if (gotPulse) {
    waiting = false;
    uint32_t us = pulseUs;
    int cm = (us >= 25000) ? -1 : (int)(us / 58);       // long pulse = no echo / out of range
    gotDistance((cm >= 2 && cm <= 400) ? cm : -1);
  } else if (!gotRise && now - pingAt > 20) {
    waiting = false;
    sensorFailed("no echo pulse at all");
  } else if (gotRise && now - pingAt > 100) {
    waiting = false;
    sensorFailed("echo stuck high");
  }
}

// ---------------- machine state -> motor ----------------
void applyMachineState(const String& state) {
  Serial.printf("# [MOTOR] -> %s\n", state.c_str());
  if (state == "running") targetPct = RUN_PCT;
  else if (state == "slowed") targetPct = SLOW_PCT;
  else stopMotorNow();                                   // holding / stopped / locked: instant
}

void driveMotor(uint32_t now) {
  bool mayRun = (!sensorFault || !PRESENCE_STOP) && (stateNow == "running" || stateNow == "slowed");
  if (!mayRun) { if (currentPct || targetPct) stopMotorNow(); return; }
  // Keep the target in step with the state, so the motor always comes back after
  // anything that stopped it (a sensor glitch, a released command).
  targetPct = (stateNow == "slowed") ? SLOW_PCT : RUN_PCT;
  static uint32_t lastStep = 0;                          // ramp 1% per 10 ms (starts only)
  if (currentPct != targetPct && now - lastStep >= 10) {
    lastStep = now;
    if (currentPct == 0 && targetPct > 0) currentPct = MIN_SPIN;
    else if (currentPct > targetPct) currentPct = targetPct;   // slowing down: immediately
    else currentPct += 1;
    writeMotor(currentPct);
  }
}

// ---------------- NearStop messages (USB or Wi-Fi) ----------------
bool jsonString(const char* json, const char* key, char* out, size_t size) {
  char pat[24];
  snprintf(pat, sizeof(pat), "\"%s\":\"", key);
  const char* s = strstr(json, pat);
  if (!s) return false;
  s += strlen(pat);
  const char* e = strchr(s, '"');
  if (!e || (size_t)(e - s) >= size) return false;
  memcpy(out, s, e - s);
  out[e - s] = '\0';
  return true;
}

bool jsonInt(const char* json, const char* key, int& out) {
  char pat[24];
  snprintf(pat, sizeof(pat), "\"%s\":", key);
  const char* s = strstr(json, pat);
  if (!s) return false;
  out = atoi(s + strlen(pat));
  return true;
}

void handleMessage(const char* buf, const char* via) {
  char type[16] = "", cmd[16] = "";
  jsonString(buf, "type", type, sizeof(type));
  if (strcmp(type, "bridge") == 0) {
    if (!bridgeSeen) Serial.println("# [LINK] USB bridge found - reporting over USB");
    bridgeSeen = true;
    lastBridgePing = millis();
  } else if (strcmp(type, "set_zones") == 0) {
    int w = warningCm, d = dangerCm;
    jsonInt(buf, "warning_cm", w);
    jsonInt(buf, "danger_cm", d);
    if (d > 0 && w > d && w <= 500) {
      warningCm = w;
      dangerCm = d;
      Serial.printf("# [NEARSTOP via %s] set_zones warning %d cm, danger %d cm\n", via, w, d);
    }
  } else if (strcmp(type, "command") == 0 && jsonString(buf, "cmd", cmd, sizeof(cmd))) {
    Serial.printf("# [NEARSTOP via %s] command %s\n", via, cmd);
    if (strcmp(cmd, "stop") == 0) override = "stopped";
    else if (strcmp(cmd, "hold") == 0) override = "holding";
    else if (strcmp(cmd, "lock") == 0) override = "locked";
    else if (strcmp(cmd, "resume") == 0) override = "";
    if (override.length()) stopMotorNow();               // act now, don't wait for the next report
  }
}

void readSerial() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (rxLen > 0) { rxLine[rxLen] = '\0'; handleMessage(rxLine, "USB"); rxLen = 0; }
    } else if (rxLen < sizeof(rxLine) - 1) {
      rxLine[rxLen++] = c;
    } else {
      rxLen = 0;
    }
  }
}

void readUdp() {
  if (!udpStarted) return;
  int size = udp.parsePacket();
  while (size > 0) {
    char buf[256];
    int n = udp.read(buf, sizeof(buf) - 1);
    buf[n > 0 ? n : 0] = '\0';
    // While the USB bridge is talking to us, only THAT NearStop is in charge:
    // ignore commands from any other NearStop answering our old Wi-Fi broadcasts.
    if (bridgeSeen && millis() - lastBridgePing < BRIDGE_TIMEOUT_MS) {
      Serial.println("# [LINK] ignored a Wi-Fi command (connected by USB)");
    } else {
      handleMessage(buf, "Wi-Fi");
    }
    size = udp.parsePacket();
  }
}

void startWifi() {
  Serial.printf("# [WiFi] connecting to \"%s\"...\n", WIFI_SSID);
  WiFi.disconnect();
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  lastWifiTry = millis();
}

void onWiFiEvent(WiFiEvent_t event, WiFiEventInfo_t info) {
  if (event == ARDUINO_EVENT_WIFI_STA_GOT_IP)
    Serial.printf("# [WiFi] CONNECTED  IP %s  signal %d dBm\n", WiFi.localIP().toString().c_str(), (int)WiFi.RSSI());
  else if (event == ARDUINO_EVENT_WIFI_STA_DISCONNECTED)
    Serial.printf("# [WiFi] disconnected (reason %u)\n", info.wifi_sta_disconnected.reason);
}

// ---------------- "Is Sam nearby?" by radio ----------------------------
// Sam's strap broadcasts "NSB:W1" 10 times a second (ESP-NOW). Heard within
// NEAR_MEMORY_MS = Sam is near THIS machine. No distance is needed: without
// antennas the radio only reaches a short way, so hearing it means nearby.
// Not heard = this machine reports "clear", whatever the ultrasonic sensor sees
// (walls, tables, other people are ignored). Presence never stops the motor;
// only NearStop's commands do (a fall near this machine, emergency, evacuation).
#ifndef ESPNOW_CHANNEL
#define ESPNOW_CHANNEL 6        // = the strap's channel and the Wi-Fi hotspot's channel
#endif
const uint32_t NEAR_MEMORY_MS = 3000;
volatile uint32_t beaconMs = 0;
volatile int beaconRssi = -127;
volatile uint32_t beaconCount = 0;
bool espnowOn = false, wifiParked = false;

void onBeacon(const esp_now_recv_info_t* info, const uint8_t* data, int len) {
  if (len < 5 || memcmp(data, "NSB:", 4) != 0) return;
  beaconMs = millis();
  beaconRssi = info->rx_ctrl->rssi;
  beaconCount++;
}

bool workerNear() { uint32_t t = beaconMs; return t != 0 && millis() - t < NEAR_MEMORY_MS; }

void startEspNow() {
  espnowOn = true;
  if (esp_now_init() != ESP_OK) { Serial.println("# [RADIO] ESP-NOW failed to start"); return; }
  esp_now_register_recv_cb(onBeacon);
  Serial.printf("# [RADIO] listening for Sam's strap on channel %d\n", (int)WiFi.channel());
}

// USB bridge connected and no Wi-Fi: stop Wi-Fi scanning (it hops channels and
// misses beacons) and park the radio on ESPNOW_CHANNEL; resume Wi-Fi when the bridge goes.
void manageRadio(bool usbLink, bool wifiUp) {
  if (usbLink && !wifiUp && !wifiParked) {
    WiFi.setAutoReconnect(false);
    WiFi.disconnect();
    esp_wifi_set_channel(ESPNOW_CHANNEL, WIFI_SECOND_CHAN_NONE);
    wifiParked = true;
    Serial.printf("# [RADIO] USB bridge: Wi-Fi parked, radio on channel %d\n", ESPNOW_CHANNEL);
  } else if (!usbLink && wifiParked) {
    wifiParked = false;
    WiFi.setAutoReconnect(true);
    startWifi();
  }
}

void setup() {
  ledcAttach(MOTOR_PIN, PWM_FREQ, PWM_BITS);   // motor OFF first
  gpio_input_enable((gpio_num_t)MOTOR_PIN);    // lets reportMotorPin() read the real output
  stopMotorNow();
  Serial.begin(115200);
  delay(300);
  pinMode(TRIG_PIN, OUTPUT);
  digitalWrite(TRIG_PIN, LOW);
  pinMode(ECHO_PIN, INPUT);                    // GPIO34 has no pull-ups; the 20k is the pull-down
  attachInterrupt(digitalPinToInterrupt(ECHO_PIN), echoISR, CHANGE);
  pinMode(LED_PIN, OUTPUT);
  pinMode(NEOPIXEL_POWER, OUTPUT);
  digitalWrite(NEOPIXEL_POWER, HIGH);
  Serial.printf("# NearStop MOTOR node %s ready (motor stopped until the sensor reads)\n", DEVICE_ID);

  fixedServer = strlen(SERVER_IP) > 0 && serverAddr.fromString(SERVER_IP);
  WiFi.onEvent(onWiFiEvent);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.setAutoReconnect(true);
  startWifi();
}

void loop() {
  uint32_t now = millis();
  bool wifiUp = WiFi.status() == WL_CONNECTED;
  if (wifiUp && !udpStarted) { udp.begin(LOCAL_PORT); udpStarted = true; }
  if (!wifiUp && !wifiParked && now - lastWifiTry >= WIFI_RETRY_MS) startWifi();

  readSerial();
  readUdp();
  updateSensor(now);

  bool usbLink = bridgeSeen && (millis() - lastBridgePing < BRIDGE_TIMEOUT_MS);   // fresh millis(): no underflow
  if (bridgeSeen && !usbLink) { bridgeSeen = false; Serial.println("# [LINK] USB bridge gone - reporting over Wi-Fi"); }
  manageRadio(usbLink, wifiUp);
  if (!espnowOn && (wifiUp || wifiParked || now > 20000)) startEspNow();

  // State: NearStop's latched command wins (fall lockout, emergency, evacuation).
  // With PRESENCE_STOP 0 someone in front does NOT slow or stop the motor;
  // the zone is still measured and reported so NearStop knows who is near.
  const char* zone = (distCm < 0) ? "clear" : (distCm <= dangerCm) ? "danger" : (distCm <= warningCm) ? "warning" : "clear";
  String local = (sensorFault && PRESENCE_STOP) ? "stopped"
               : !PRESENCE_STOP ? "running"
               : strcmp(zone, "danger") == 0 ? DANGER_STATE : strcmp(zone, "warning") == 0 ? "slowed" : "running";
  String next = override.length() ? override : local;
  if (next != stateNow) { stateNow = next; applyMachineState(stateNow); }
  driveMotor(now);

  if (now - lastReport >= REPORT_MS) {
    lastReport = now;
    const char* sensor = sensorFault ? "error" : "ok";
    // Only Sam counts: not heard by radio -> report "clear" (dist -1).
    bool near = workerNear();
    int repDist = (sensorFault || !near) ? -1 : distCm;
    const char* repZone = (repDist < 0) ? "clear" : zone;
    char json[300];
    int len = snprintf(json, sizeof(json),
      "{\"type\":\"machine\",\"id\":\"%s\",\"state\":\"%s\",\"dist_cm\":%d,\"zone\":\"%s\","
      "\"sensor\":\"%s\",\"warning_cm\":%d,\"danger_cm\":%d,\"motor_pct\":%d,"
      "\"worker_near\":%s,\"worker_rssi\":%d}",
      DEVICE_ID, stateNow.c_str(), repDist, repZone, sensor, warningCm, dangerCm, currentPct,
      near ? "true" : "false", near ? beaconRssi : -127);    if (usbLink) {
      Serial.println(json);
    } else if (wifiUp) {
      IPAddress dest = fixedServer ? serverAddr : WiFi.broadcastIP();
      udp.beginPacket(dest, SERVER_PORT);
      udp.write((const uint8_t*)json, len);
      udp.endPacket();
      Serial.print("#W ");
      Serial.println(json);
    } else {
      Serial.println(json);
    }
    // Lights
    if (sensorFault || stateNow == "slowed") rgbLedWrite(NEOPIXEL_PIN, 60, 40, 0);   // yellow
    else if (stateNow == "running")         rgbLedWrite(NEOPIXEL_PIN, 0, 60, 0);    // green
    else                                     rgbLedWrite(NEOPIXEL_PIN, 80, 0, 0);    // red
  }
  digitalWrite(LED_PIN, currentPct == 0);
  static uint32_t lastPwmReport = 0;
  if (now - lastPwmReport >= 2000) { lastPwmReport = now; reportMotorPin(); }
  delay(2);
}
