// A0 (GPIO26) PWM test for the Hoist motor driver (NearStop diagnostics).
// Drives the motor PWM (20 kHz, 8-bit) IGNORING the ultrasonic sensor, and reads
// the pin's real pad level to prove A0 is switching and at what duty.
// KEEP HANDS AWAY FROM THE MOTOR while it runs. Upload the hoist firmware again after.
// Result line: "A0TEST motor ON: high 53.9% of the time, 322 edges (~20.0 kHz)".
#ifndef TEST_PCT
#define TEST_PCT 60          // 0-100 of MAX_PCT; the duty printed is what the MOSFET gate sees
#endif
#ifndef TEST_SECONDS
#define TEST_SECONDS 120
#endif
#include "driver/gpio.h"
#ifndef TEST_PIN
#define TEST_PIN 26          // 26 = A0 (Hoist motor), 25 = A1 (Conveyor / Robot Arm motor)
#endif
const int PIN = TEST_PIN;
#ifndef MAX_PCT
#define MAX_PCT 90           // Hoist 90; Conveyor / Robot Arm (5 V motor) 100
#endif

void measure(const char* label) {
  const int N = 40000;
  int hi = 0, edges = 0, prev = gpio_get_level((gpio_num_t)PIN);
  uint32_t t0 = micros();
  for (int i = 0; i < N; i++) {
    int v = gpio_get_level((gpio_num_t)PIN);
    hi += v;
    if (v != prev) edges++;
    prev = v;
  }
  uint32_t us = micros() - t0;
  float freqKHz = (edges / 2.0f) / (us / 1000.0f);
  Serial.printf("A0TEST %s: high %.1f%% of the time, %d edges in %lu us (~%.1f kHz)\n",
                label, 100.0f * hi / N, edges, (unsigned long)us, freqKHz);
}

void setup() {
  Serial.begin(115200);
  ledcAttach(PIN, 20000, 8);
  ledcWrite(PIN, 0);
  gpio_input_enable((gpio_num_t)PIN);     // let us read the pad while LEDC drives it
  delay(1500);
  measure("motor OFF (duty 0)");
  int duty = map(TEST_PCT, 0, 100, 0, 255 * MAX_PCT / 100);
  Serial.printf("A0TEST motor ON: %d%% -> PWM duty %d/255 (expect ~%.0f%% high) for %d s\n", TEST_PCT, duty, 100.0 * duty / 255, TEST_SECONDS);
  ledcWrite(PIN, duty);
  for (int i = 0; i < TEST_SECONDS / 10; i++) { delay(10000); measure("motor ON"); }
  ledcWrite(PIN, 0);
  delay(200);
  measure("motor OFF again");
  Serial.println("A0TEST done");
}
void loop() { delay(1000); }

