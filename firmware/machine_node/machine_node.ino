/*
 * NearStop machine node - REFERENCE sketch (compile-checked, not yet tested
 * on real HC-SR04 hardware). Merge into your own machine firmware, or use as is.
 *
 * Board: ESP32 (Arduino core 3.x), e.g. Adafruit Feather ESP32 V2.
 *
 * What NearStop needs from a machine node:
 *   1. Every ~200 ms send one UDP JSON report to SERVER_IP:SERVER_PORT:
 *        {"type":"machine","id":"GW","state":"slowed","dist_cm":87,
 *         "zone":"warning","sensor":"ok","warning_cm":150,"danger_cm":50}
 *      dist_cm = -1 when nothing is in range.
 *   2. Send from a FIXED local port (udp.begin(LOCAL_PORT)) and read commands
 *      that NearStop sends back to that port:
 *        {"type":"set_zones","id":"GW","warning_cm":180,"danger_cm":60}
 *        {"type":"command","id":"GW","cmd":"stop"}   stop|hold|lock|resume|slow
 *   3. Apply set_zones and echo the active values (warning_cm / danger_cm).
 *      Obey stop / hold / lock until "resume" (only NearStop's "Approve
 *      restart" sends resume).
 *
 * Serial Monitor: 115200 baud.
 */

#include <WiFi.h>
#include <WiFiUdp.h>
#include "config.h"

const unsigned long REPORT_MS = 200;        // 5 reports per second
const unsigned long RECONNECT_MS = 10000;
const unsigned long ECHO_TIMEOUT_US = 25000; // ~4.3 m: no echo = nothing in range

WiFiUDP udp;
IPAddress serverAddr;
bool serverAddrValid = false;

int warningCm = DEFAULT_WARNING_CM;
int dangerCm = DEFAULT_DANGER_CM;
String override = "";        // "stopped" / "holding" / "locked" after a NearStop command
unsigned long lastReport = 0, lastReconnect = 0;
int lastDist[3] = {-1, -1, -1};   // small median filter against single bad echoes
int distIdx = 0;

// ---------------------------------------------------------------------
// Hook for YOUR machine: called whenever the state changes.
// state: "running", "slowed", "stopped", "holding", "locked"
// ---------------------------------------------------------------------
void applyMachineState(const String& state) {
  Serial.printf("[MACHINE] -> %s\n", state.c_str());
  // TODO: drive your relay / motor driver / indicator light here.
}

// ---------------------------------------------------------------------
// HC-SR04
// ---------------------------------------------------------------------
int readDistanceCm() {
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(2);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);
  unsigned long us = pulseIn(ECHO_PIN, HIGH, ECHO_TIMEOUT_US);
  if (us == 0) return -1;                     // no echo: nothing in range
  int cm = (int)(us / 58);
  return (cm >= 2 && cm <= 400) ? cm : -1;
}

int medianOf3(int a, int b, int c) {
  if (a > b) { int t = a; a = b; b = t; }
  if (b > c) { int t = b; b = c; c = t; }
  if (a > b) { int t = a; a = b; b = t; }
  return b;
}

// ---------------------------------------------------------------------
// Tiny JSON helpers for NearStop's small command messages
// ---------------------------------------------------------------------
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

void handleCommands() {
  int size = udp.parsePacket();
  while (size > 0) {
    char buf[256];
    int n = udp.read(buf, sizeof(buf) - 1);
    buf[n > 0 ? n : 0] = '\0';
    char type[16] = "", cmd[16] = "";
    jsonString(buf, "type", type, sizeof(type));
    if (strcmp(type, "set_zones") == 0) {
      int w = warningCm, d = dangerCm;
      jsonInt(buf, "warning_cm", w);
      jsonInt(buf, "danger_cm", d);
      if (d > 0 && w > d && w <= 500) {
        warningCm = w;
        dangerCm = d;
        Serial.printf("[NEARSTOP] set_zones warning %d cm, danger %d cm\n", w, d);
      }
    } else if (strcmp(type, "command") == 0 && jsonString(buf, "cmd", cmd, sizeof(cmd))) {
      Serial.printf("[NEARSTOP] command %s\n", cmd);
      if (strcmp(cmd, "stop") == 0) override = "stopped";
      else if (strcmp(cmd, "hold") == 0) override = "holding";
      else if (strcmp(cmd, "lock") == 0) override = "locked";
      else if (strcmp(cmd, "resume") == 0) override = "";
      // "slow" needs nothing: the node already slows itself in the warning zone
    }
    size = udp.parsePacket();
  }
}

// ---------------------------------------------------------------------
// Wi-Fi
// ---------------------------------------------------------------------
void startWiFi() {
  Serial.printf("[WiFi] Connecting to \"%s\"...\n", WIFI_SSID);
  WiFi.disconnect();
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  lastReconnect = millis();
}

void onWiFiEvent(WiFiEvent_t event, WiFiEventInfo_t info) {
  if (event == ARDUINO_EVENT_WIFI_STA_GOT_IP) {
    Serial.printf("[WiFi] CONNECTED  IP %s  channel %d\n", WiFi.localIP().toString().c_str(), (int)WiFi.channel());
  } else if (event == ARDUINO_EVENT_WIFI_STA_DISCONNECTED) {
    Serial.printf("[WiFi] DISCONNECTED (reason %u)\n", info.wifi_sta_disconnected.reason);
  }
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  Serial.printf("\nNearStop machine node %s -> %s:%d (listening on %d)\n", DEVICE_ID, SERVER_IP, SERVER_PORT, LOCAL_PORT);

  serverAddrValid = serverAddr.fromString(SERVER_IP);
  if (!serverAddrValid) Serial.println("[CONFIG] ERROR: SERVER_IP is not a valid IP address");

  WiFi.onEvent(onWiFiEvent);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.setAutoReconnect(true);
  startWiFi();
  udp.begin(LOCAL_PORT);          // fixed port: NearStop's commands come back here
}

void loop() {
  unsigned long now = millis();
  bool connected = WiFi.status() == WL_CONNECTED;
  if (!connected && now - lastReconnect >= RECONNECT_MS) startWiFi();

  if (connected) handleCommands();

  if (now - lastReport >= REPORT_MS) {
    lastReport = now;
    lastDist[distIdx] = readDistanceCm();
    distIdx = (distIdx + 1) % 3;
    int dist = medianOf3(lastDist[0], lastDist[1], lastDist[2]);

    const char* zone = (dist < 0) ? "clear" : (dist <= dangerCm) ? "danger" : (dist <= warningCm) ? "warning" : "clear";
    String local = strcmp(zone, "danger") == 0 ? "stopped" : strcmp(zone, "warning") == 0 ? "slowed" : "running";
    String stateNow = override.length() ? override : local;

    static String lastState = "";
    if (stateNow != lastState) {
      lastState = stateNow;
      applyMachineState(stateNow);
    }

    if (connected && serverAddrValid) {
      char json[220];
      int len = snprintf(json, sizeof(json),
        "{\"type\":\"machine\",\"id\":\"%s\",\"state\":\"%s\",\"dist_cm\":%d,\"zone\":\"%s\","
        "\"sensor\":\"ok\",\"warning_cm\":%d,\"danger_cm\":%d}",
        DEVICE_ID, stateNow.c_str(), dist, zone, warningCm, dangerCm);
      udp.beginPacket(serverAddr, SERVER_PORT);   // same udp object -> sent from LOCAL_PORT
      udp.write((const uint8_t*)json, len);
      udp.endPacket();
    }
  }
  delay(5);
}
