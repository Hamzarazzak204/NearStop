/*
 * NAT Basestation node firmware - Draft 1
 * Board: Adafruit Feather ESP32 V2   Core: ESP32 Arduino core 3.x
 *
 * ONE firmware for every board; config.h says which board this is.
 *
 *   Every board:  joins Wi-Fi, sends a UDP heartbeat to the Pi every 1 s.
 *   Worker (W1):  broadcasts a small ESP-NOW "hello" every 500 ms.
 *   Anchors (GW, MA, MB):  receive W1's hello, read its signal strength
 *                 (RSSI) and forward it to the Pi as a UDP "rssi_measurement".
 *
 * ESP-NOW and Wi-Fi share one radio, so ESP-NOW automatically uses the
 * Wi-Fi network's channel. All boards must be on the SAME network.
 *
 * Red LED (pin 13): fast blink = no Wi-Fi; short blink = hello sent (worker)
 *                   or hello received (anchor).
 * Serial Monitor: 115200 baud.
 */

#include <WiFi.h>
#include <WiFiUdp.h>
#include <esp_now.h>
#include <esp_wifi.h>
#include "config.h"

// ---------------- Timing ----------------
const unsigned long HEARTBEAT_MS    = 1000;   // UDP heartbeat to the Pi
const unsigned long HELLO_MS        = 500;    // worker ESP-NOW broadcast
const unsigned long RECONNECT_MS    = 10000;  // retry Wi-Fi this often while down
const unsigned long STATUS_PRINT_MS = 10000;

#ifdef LED_BUILTIN
const int LED_PIN = LED_BUILTIN;
#else
const int LED_PIN = 13;
#endif

const bool IS_WORKER = (strcmp(DEVICE_ROLE, "worker") == 0);
const uint8_t BROADCAST_ADDR[6] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};

WiFiUDP udp;
IPAddress piAddress;
bool piAddressValid = false;
bool espNowReady = false;

// One received ESP-NOW packet, copied out of the callback.
struct RxItem {
  uint32_t receivedMs;
  int8_t rssi;
  uint8_t len;
  char data[96];
};
QueueHandle_t rxQueue = nullptr;
volatile uint32_t rxDropped = 0;   // packets lost because the queue was full

unsigned long lastHeartbeat = 0, lastHello = 0, lastReconnect = 0;
unsigned long lastStatus = 0, lastLedToggle = 0, ledOffAt = 0;
uint32_t heartbeatSeq = 0, helloSeq = 0;
uint32_t helloSent = 0, helloSendErrors = 0;     // worker
uint32_t helloReceived = 0, rssiForwarded = 0;   // anchor
uint32_t udpFailures = 0;

// ---------------------------------------------------------------------
// ESP-NOW receive callback (core 3.x signature). Runs in the Wi-Fi task:
// keep it SHORT - copy the data into the queue and return.
// ---------------------------------------------------------------------
void onEspNowReceive(const esp_now_recv_info_t* info, const uint8_t* data, int len) {
  if (rxQueue == nullptr || len <= 0) return;
  RxItem item;
  item.receivedMs = millis();
  item.rssi = info->rx_ctrl->rssi;
  item.len = (uint8_t)min(len, (int)sizeof(item.data) - 1);
  memcpy(item.data, data, item.len);
  item.data[item.len] = '\0';
  if (xQueueSend(rxQueue, &item, 0) != pdTRUE) rxDropped = rxDropped + 1;
}

// ---------------------------------------------------------------------
// Tiny JSON helpers for our own small messages (no library needed).
// ---------------------------------------------------------------------
bool jsonGetString(const char* json, const char* key, char* out, size_t outSize) {
  char pattern[24];
  snprintf(pattern, sizeof(pattern), "\"%s\":\"", key);
  const char* start = strstr(json, pattern);
  if (!start) return false;
  start += strlen(pattern);
  const char* end = strchr(start, '"');
  if (!end || (size_t)(end - start) >= outSize) return false;
  memcpy(out, start, end - start);
  out[end - start] = '\0';
  return true;
}

bool jsonGetUInt(const char* json, const char* key, uint32_t& out) {
  char pattern[24];
  snprintf(pattern, sizeof(pattern), "\"%s\":", key);
  const char* start = strstr(json, pattern);
  if (!start) return false;
  out = strtoul(start + strlen(pattern), nullptr, 10);
  return true;
}

// ---------------------------------------------------------------------
// UDP to the Pi
// ---------------------------------------------------------------------
bool sendUdp(const char* json, int len) {
  if (WiFi.status() != WL_CONNECTED || !piAddressValid) return false;
  bool ok = udp.beginPacket(piAddress, PI_UDP_PORT) &&
            udp.write((const uint8_t*)json, len) == (size_t)len &&
            udp.endPacket();
  if (!ok) udpFailures++;
  return ok;
}

void sendHeartbeat() {
  heartbeatSeq++;
  char json[300];
  int len = snprintf(json, sizeof(json),
    "{\"type\":\"heartbeat\",\"id\":\"%s\",\"role\":\"%s\",\"seq\":%lu,"
    "\"uptime_ms\":%lu,\"wifi_rssi\":%d,\"channel\":%d,"
    "\"ip\":\"%s\",\"mac\":\"%s\",\"espnow\":%s,"
    "\"hello_sent\":%lu,\"hello_received\":%lu,\"rx_dropped\":%lu}",
    DEVICE_ID, DEVICE_ROLE, (unsigned long)heartbeatSeq,
    (unsigned long)millis(), (int)WiFi.RSSI(), (int)WiFi.channel(),
    WiFi.localIP().toString().c_str(), WiFi.macAddress().c_str(),
    espNowReady ? "true" : "false",
    (unsigned long)helloSent, (unsigned long)helloReceived, (unsigned long)rxDropped);
  if (len > 0 && len < (int)sizeof(json)) sendUdp(json, len);
}

// ---------------------------------------------------------------------
// Worker: broadcast hello over ESP-NOW
// ---------------------------------------------------------------------
void sendHello() {
  helloSeq++;
  char msg[96];
  int len = snprintf(msg, sizeof(msg),
                     "{\"type\":\"hello\",\"id\":\"%s\",\"role\":\"worker\",\"seq\":%lu}",
                     DEVICE_ID, (unsigned long)helloSeq);
  // Broadcasts are never acknowledged: ESP_OK only means "handed to the radio",
  // not that any anchor received it.
  esp_err_t err = esp_now_send(BROADCAST_ADDR, (const uint8_t*)msg, len);
  if (err == ESP_OK) {
    helloSent++;
    blinkLed();
  } else {
    helloSendErrors++;
    if (helloSendErrors % 20 == 1) Serial.printf("[ESP-NOW] send error %d\n", err);
  }
}

// ---------------------------------------------------------------------
// Anchor: handle received hellos (called from loop, NOT the callback)
// ---------------------------------------------------------------------
void processReceived() {
  RxItem item;
  while (xQueueReceive(rxQueue, &item, 0) == pdTRUE) {
    char type[12], workerId[17];
    uint32_t seq = 0;
    if (!jsonGetString(item.data, "type", type, sizeof(type)) || strcmp(type, "hello") != 0) continue;
    if (!jsonGetString(item.data, "id", workerId, sizeof(workerId))) continue;
    jsonGetUInt(item.data, "seq", seq);
    helloReceived++;
    blinkLed();

    char json[200];
    int len = snprintf(json, sizeof(json),
      "{\"type\":\"rssi_measurement\",\"anchor_id\":\"%s\",\"worker_id\":\"%s\","
      "\"rssi\":%d,\"seq\":%lu,\"timestamp\":%lu}",
      DEVICE_ID, workerId, (int)item.rssi, (unsigned long)seq, (unsigned long)item.receivedMs);
    bool sent = len > 0 && len < (int)sizeof(json) && sendUdp(json, len);
    if (sent) rssiForwarded++;
    Serial.printf("[ESP-NOW] hello from %s seq %lu  RSSI %d dBm  -> Pi %s\n",
                  workerId, (unsigned long)seq, (int)item.rssi, sent ? "sent" : "NOT sent (no Wi-Fi)");
  }
}

// ---------------------------------------------------------------------
// Wi-Fi + ESP-NOW setup
// ---------------------------------------------------------------------
void onWiFiEvent(WiFiEvent_t event, WiFiEventInfo_t info) {
  if (event == ARDUINO_EVENT_WIFI_STA_GOT_IP) {
    Serial.printf("[WiFi] CONNECTED  IP %s  channel %d  signal %d dBm\n",
                  WiFi.localIP().toString().c_str(), (int)WiFi.channel(), (int)WiFi.RSSI());
    Serial.printf("[WiFi] ESP-NOW now runs on channel %d (same as the Wi-Fi network)\n",
                  (int)WiFi.channel());
  } else if (event == ARDUINO_EVENT_WIFI_STA_DISCONNECTED) {
    uint8_t reason = info.wifi_sta_disconnected.reason;
    Serial.printf("[WiFi] DISCONNECTED (reason %u)%s\n", reason,
                  reason == 201 ? " - network not found" :
                  (reason == 15 || reason == 202 || reason == 204) ? " - wrong password?" : "");
  }
}

void startWiFi() {
  Serial.printf("[WiFi] Connecting to \"%s\"...\n", WIFI_SSID);
  WiFi.disconnect();
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  lastReconnect = millis();
}

void startEspNow() {
  if (esp_now_init() != ESP_OK) {
    Serial.println("[ESP-NOW] ERROR: init failed");
    return;
  }
  if (IS_WORKER) {
    // Peer with channel 0 = "use the current Wi-Fi channel"
    esp_now_peer_info_t peer = {};
    memcpy(peer.peer_addr, BROADCAST_ADDR, 6);
    peer.channel = 0;
    peer.ifidx = WIFI_IF_STA;
    peer.encrypt = false;
    if (esp_now_add_peer(&peer) != ESP_OK) {
      Serial.println("[ESP-NOW] ERROR: could not add broadcast peer");
      return;
    }
  } else {
    rxQueue = xQueueCreate(16, sizeof(RxItem));
    esp_now_register_recv_cb(onEspNowReceive);
  }
  espNowReady = true;
  Serial.printf("[ESP-NOW] ready as %s\n", IS_WORKER ? "WORKER (broadcasting hello every 500 ms)"
                                                     : "ANCHOR (listening for worker hellos)");
}

void blinkLed() {
  digitalWrite(LED_PIN, HIGH);
  ledOffAt = millis() + 30;
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  pinMode(LED_PIN, OUTPUT);

  Serial.println();
  Serial.println("========================================");
  Serial.println(" NAT Basestation node - Draft 1");
  Serial.printf (" Device ID : %s\n", DEVICE_ID);
  Serial.printf (" Role      : %s\n", DEVICE_ROLE);
  Serial.printf (" Pi target : %s:%d (UDP)\n", PI_IP, PI_UDP_PORT);
  Serial.println("========================================");

  piAddressValid = piAddress.fromString(PI_IP);
  if (!piAddressValid) Serial.printf("[CONFIG] ERROR: PI_IP \"%s\" is not a valid IP\n", PI_IP);

  WiFi.onEvent(onWiFiEvent);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);          // radio always on: needed to hear ESP-NOW reliably
  WiFi.setAutoReconnect(true);
  startWiFi();
  Serial.printf("[WiFi] MAC address: %s\n", WiFi.macAddress().c_str());

  startEspNow();
}

void loop() {
  unsigned long now = millis();
  bool connected = (WiFi.status() == WL_CONNECTED);

  if (!connected && now - lastReconnect >= RECONNECT_MS) {
    Serial.println("[WiFi] Still not connected, retrying...");
    startWiFi();
  }

  if (connected && now - lastHeartbeat >= HEARTBEAT_MS) {
    lastHeartbeat = now;
    sendHeartbeat();
  }

  if (espNowReady) {
    // Only broadcast once on the network's channel, so anchors can hear it
    if (IS_WORKER && connected && now - lastHello >= HELLO_MS) {
      lastHello = now;
      sendHello();
    }
    if (!IS_WORKER) processReceived();
  }

  // LED: fast blink without Wi-Fi, otherwise short blinks on ESP-NOW activity
  if (!connected) {
    if (now - lastLedToggle >= 150) {
      lastLedToggle = now;
      digitalWrite(LED_PIN, !digitalRead(LED_PIN));
    }
  } else if (ledOffAt && (long)(now - ledOffAt) >= 0) {
    digitalWrite(LED_PIN, LOW);
    ledOffAt = 0;
  }

  if (now - lastStatus >= STATUS_PRINT_MS) {
    lastStatus = now;
    if (IS_WORKER) {
      Serial.printf("[STATUS] %s | Wi-Fi %s ch %d | hellos sent %lu (errors %lu) | UDP fails %lu\n",
                    DEVICE_ID, connected ? "OK" : "DOWN", (int)WiFi.channel(),
                    (unsigned long)helloSent, (unsigned long)helloSendErrors, (unsigned long)udpFailures);
    } else {
      Serial.printf("[STATUS] %s | Wi-Fi %s ch %d | hellos received %lu | forwarded %lu | queue drops %lu | UDP fails %lu\n",
                    DEVICE_ID, connected ? "OK" : "DOWN", (int)WiFi.channel(),
                    (unsigned long)helloReceived, (unsigned long)rssiForwarded,
                    (unsigned long)rxDropped, (unsigned long)udpFailures);
    }
  }

  delay(5);
}
