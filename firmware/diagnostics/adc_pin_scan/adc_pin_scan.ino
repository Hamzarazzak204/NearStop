// Finds which analog pin the MQ-2's AO wire reaches: prints the voltage on every
// analog-capable Feather ESP32 V2 header pin twice a second. No Wi-Fi (so ADC2 pins work).
// "sensor V" = pin V x 1.5 (the 10k/20k divider), only meaningful on the MQ-2 pin.
struct P { int gpio; const char* label; };
const P PINS[] = {{26, "A0"}, {25, "A1"}, {34, "A2"}, {39, "A3"}, {36, "A4"}, {4, "A5"},
                  {37, "37"}, {35, "VBAT/35"}, {12, "12"}, {13, "13"}};

void setup() {
  Serial.begin(115200);
  delay(500);
  // INPUT_PULLDOWN: a floating pin drops to ~0 V; a pin something drives keeps its voltage.
  // (GPIO 34-39 have no internal pulls; they just read as INPUT.)
  for (auto& p : PINS) { pinMode(p.gpio, p.gpio >= 34 ? INPUT : INPUT_PULLDOWN); analogSetPinAttenuation(p.gpio, ADC_11db); }
  Serial.println("# ADC scan: pin=volts (sensor volts = x1.5 on the MQ-2 pin)");
}

void loop() {
  for (auto& p : PINS) {
    float v = analogReadMilliVolts(p.gpio) / 1000.0;
    Serial.printf("%s(GPIO%d)=%.2fV  ", p.label, p.gpio, v);
  }
  Serial.println();
  delay(500);
}
