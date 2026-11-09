/*
 * NearStop machine node - USB *or* Wi-Fi
 * Board: Adafruit Feather ESP32 V2   Core: ESP32 Arduino core 3.x
 *
 * Reports to NearStop whenever the board has power:
 *   - Plugged into the laptop running "NearStop USB Bridge": reports go over the
 *     USB cable (reliable, no antenna needed). The bridge sends a {"type":"bridge"}
 *     line every second; while those arrive the board uses USB.
 *   - Any other power (charger, power bank, or laptop without the bridge): reports
 *     go over Wi-Fi as UDP to NearStop (broadcast by default, see config.h).
 * Same messages as firmware/machine_node, so NearStop needs no changes:
 *     {"type":"machine","id":"MA","state":"slowed","dist_cm":87,"zone":"warning",
 *      "sensor":"ok","warning_cm":150,"danger_cm":50}
 * Commands come back over whichever path the reports use:
 *     {"type":"set_zones",...}  {"type":"command","cmd":"stop|hold|lock|resume|slow"}
 *
 * NOTE: our boards are the external-antenna version (PICO-MINI-02U). Without an
 * antenna, Wi-Fi only works very close to the hotspot. Fit a 2.4 GHz u.FL
 * antenna for real use.
 *
 * Serial (115200): JSON report lines in USB mode; lines starting with "#" are
 * human-readable status ("#W {...}" = a report that went over Wi-Fi).
 * Red LED: off = running, blinking = slowed, on = stopped/holding/locked;
 *          a quick double-flash every 2 s = no link to NearStop.
 */

#include <WiFi.h>
#include <WiFiUdp.h>
#include "config.h"

const unsigned long REPORT_MS = 200;          // 5 reports per second
const unsigned long ECHO_TIMEOUT_US = 25000;  // ~4.3 m: no echo = nothing in range
const unsigned long SENSOR_STUCK_MS = 3000;   // no echo at all this long -> "no_reading"
const unsigned long BRIDGE_TIMEOUT_MS = 3000; // no bridge ping this long -> use Wi-Fi
const unsigned long WIFI_RETRY_MS = 20000;    // restart a stuck Wi-Fi attempt this often

WiFiUDP udp;
IPAddress serverAddr;
bool fixedServer = false;
bool udpStarted = false;

int warningCm = DEFAULT_WARNING_CM;
int dangerCm = DEFAULT_DANGER_CM;
String override = "";
String stateNow = "";
unsigned long lastReport = 0, lastEcho = 0, lastBridgePing = 0, lastWifiTry = 0;
bool bridgeSeen = false;
int lastDist[3] = {-1, -1, -1};
int distIdx = 0;
char rxLine[256];
size_t rxLen = 0;
const char* linkNow = "none";

// ---------------------------------------------------------------------
// Hook for YOUR machine: called whenever the state changes.
// ---------------------------------------------------------------------
void applyMachineState(const String& state) {
  Serial.printf("# [MACHINE] -> %s\n", state.c_str());
  // TODO: drive the machine's relay / motor driver / indicator light here.
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
  if (us == 0) return -1;
  lastEcho = millis();
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
// Messages from NearStop / the bridge (same handling for USB and Wi-Fi)
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

void handleMessage(const char* buf, const char* via) {
  char type[16] = "", cmd[16] = "";
  jsonString(buf, "type", type, sizeof(type));
  if (strcmp(type, "bridge") == 0) {            // USB bridge is listening
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

// ---------------------------------------------------------------------
// Wi-Fi (always kept connecting in the background, so switching is instant)
// ---------------------------------------------------------------------
void startWifi() {
  Serial.printf("# [WiFi] connecting to \"%s\"...\n", WIFI_SSID);
  WiFi.disconnect();
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  lastWifiTry = millis();
}

void onWiFiEvent(WiFiEvent_t event, WiFiEventInfo_t info) {
  if (event == ARDUINO_EVENT_WIFI_STA_GOT_IP) {
    Serial.printf("# [WiFi] CONNECTED  IP %s  signal %d dBm\n", WiFi.localIP().toString().c_str(), (int)WiFi.RSSI());
  } else if (event == ARDUINO_EVENT_WIFI_STA_DISCONNECTED) {
    Serial.printf("# [WiFi] disconnected (reason %u)\n", info.wifi_sta_disconnected.reason);
  }
}

void updateLed(unsigned long now) {
  bool noLink = strcmp(linkNow, "none") == 0;
  if (noLink && (now % 2000) < 300) {            // double-flash: no link to NearStop
    digitalWrite(LED_PIN, ((now % 2000) / 75) % 2 == 0);
    return;
  }
  if (stateNow == "running") digitalWrite(LED_PIN, LOW);
  else if (stateNow == "slowed") digitalWrite(LED_PIN, (now / 250) % 2);
  else digitalWrite(LED_PIN, HIGH);
}

void setup() {
  Serial.begin(115200);
  delay(500);
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(TRIG_PIN, LOW);
  lastEcho = millis();
  Serial.printf("# NearStop machine node %s (USB or Wi-Fi) ready\n", DEVICE_ID);

  fixedServer = strlen(SERVER_IP) > 0 && serverAddr.fromString(SERVER_IP);
  WiFi.onEvent(onWiFiEvent);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.setAutoReconnect(true);
  startWifi();
}

void loop() {
  unsigned long now = millis();
  bool wifiUp = WiFi.status() == WL_CONNECTED;
  if (wifiUp && !udpStarted) { udp.begin(LOCAL_PORT); udpStarted = true; }
  if (!wifiUp && now - lastWifiTry >= WIFI_RETRY_MS) startWifi();

  readSerial();
  readUdp();

  bool usbLink = bridgeSeen && (millis() - lastBridgePing < BRIDGE_TIMEOUT_MS);   // fresh millis(): no underflow
  if (bridgeSeen && !usbLink) {
    bridgeSeen = false;
    Serial.println("# [LINK] USB bridge gone - reporting over Wi-Fi");
  }

  if (now - lastReport >= REPORT_MS) {
    lastReport = now;
    lastDist[distIdx] = readDistanceCm();
    distIdx = (distIdx + 1) % 3;
    int dist = medianOf3(lastDist[0], lastDist[1], lastDist[2]);

    const char* zone = (dist < 0) ? "clear" : (dist <= dangerCm) ? "danger" : (dist <= warningCm) ? "warning" : "clear";
    String local = strcmp(zone, "danger") == 0 ? "stopped" : strcmp(zone, "warning") == 0 ? "slowed" : "running";
    String next = override.length() ? override : local;
    if (next != stateNow) { stateNow = next; applyMachineState(stateNow); }
    const char* sensor = (millis() - lastEcho > SENSOR_STUCK_MS) ? "no_reading" : "ok";

    char json[220];
    int len = snprintf(json, sizeof(json),
      "{\"type\":\"machine\",\"id\":\"%s\",\"state\":\"%s\",\"dist_cm\":%d,\"zone\":\"%s\","
      "\"sensor\":\"%s\",\"warning_cm\":%d,\"danger_cm\":%d}",
      DEVICE_ID, stateNow.c_str(), dist, zone, sensor, warningCm, dangerCm);

    if (usbLink) {
      linkNow = "usb";
      Serial.println(json);                       // the bridge forwards this line
    } else if (wifiUp) {
      linkNow = "wifi";
      IPAddress dest = fixedServer ? serverAddr : WiFi.broadcastIP();
      udp.beginPacket(dest, SERVER_PORT);         // same udp object -> sent from LOCAL_PORT
      udp.write((const uint8_t*)json, len);
      udp.endPacket();
      Serial.print("#W ");                        // for the Serial Monitor only
      Serial.println(json);
    } else {
      linkNow = "none";
      Serial.println(json);                       // nobody listening yet; still useful in the Serial Monitor
    }
  }
  updateLed(now);
  delay(2);
}
