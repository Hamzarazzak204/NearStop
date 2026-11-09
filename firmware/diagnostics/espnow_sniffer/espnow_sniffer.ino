// ESP-NOW SNIFFER (diagnostic) - Adafruit Feather ESP32 V2
// Scans Wi-Fi channels 1-13 and reports ESP-NOW frames it hears, with the
// channel, sender MAC and signal strength. Use it to find out which channel
// (and which MAC) another board is really transmitting on.
// Open Serial Monitor at 115200.
#include <WiFi.h>
#include <esp_wifi.h>

#define DWELL_MS 1100   // time spent on each channel

volatile uint32_t espnowCount[14];
volatile uint32_t otherCount[14];
uint8_t  lastSrc[14][6];
int8_t   lastRssi[14];

void onFrame(void *buf, wifi_promiscuous_pkt_type_t type) {
  if (type != WIFI_PKT_MGMT) return;
  wifi_promiscuous_pkt_t *pkt = (wifi_promiscuous_pkt_t *)buf;
  const uint8_t *f = pkt->payload;
  int ch = pkt->rx_ctrl.channel;
  if (ch < 1 || ch > 13) return;

  // ESP-NOW frames are 802.11 "action" frames (0xD0) with category 127 and
  // Espressif's vendor OUI 18:FE:34 right after the 24-byte header.
  if (f[0] == 0xD0 && pkt->rx_ctrl.sig_len > 28 &&
      f[24] == 127 && f[25] == 0x18 && f[26] == 0xFE && f[27] == 0x34) {
    espnowCount[ch]++;
    memcpy(lastSrc[ch], f + 10, 6);
    lastRssi[ch] = pkt->rx_ctrl.rssi;
  } else {
    otherCount[ch]++;
  }
}

void setup() {
  Serial.begin(115200);
  delay(500);
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  wifi_promiscuous_filter_t filt = { .filter_mask = WIFI_PROMIS_FILTER_MASK_MGMT };
  esp_wifi_set_promiscuous_filter(&filt);
  esp_wifi_set_promiscuous_rx_cb(&onFrame);
  esp_wifi_set_promiscuous(true);
  Serial.println("SNIFFER ready: scanning channels 1-13 for ESP-NOW frames...");
}

void loop() {
  bool heardAny = false;
  for (int ch = 1; ch <= 13; ch++) {
    espnowCount[ch] = 0;
    otherCount[ch] = 0;
    esp_wifi_set_channel(ch, WIFI_SECOND_CHAN_NONE);
    delay(DWELL_MS);
    if (otherCount[ch] > 0) {
      Serial.printf("wifi  ch=%d other management frames=%lu (sniffer is working)\n",
                    ch, (unsigned long)otherCount[ch]);
    }
    if (espnowCount[ch] > 0) {
      heardAny = true;
      Serial.printf("ESPNOW ch=%d frames=%lu from %02X:%02X:%02X:%02X:%02X:%02X rssi=%d dBm\n",
                    ch, (unsigned long)espnowCount[ch],
                    lastSrc[ch][0], lastSrc[ch][1], lastSrc[ch][2],
                    lastSrc[ch][3], lastSrc[ch][4], lastSrc[ch][5], lastRssi[ch]);
    }
  }
  Serial.println(heardAny ? "--- sweep done ---" : "--- sweep done: no ESP-NOW frames heard on any channel ---");
}
