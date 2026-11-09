// =====================================================================
//  STRAP SENSOR CHECK (diagnostics): are the BMP180, MPU-6050, MQ-2 and the
//  "I'm OK" button wired where firmware/worker_strap expects them?
//  Prints every second. Board: Adafruit Feather ESP32 V2. Serial Monitor 115200.
//  No libraries needed. Re-upload firmware/worker_strap afterwards.
//
//   I2C bus (SDA 22 / SCL 20): MPU-6050 at 0x68, BMP180 at 0x77
//   MQ-2   AO -> 10k -> GPIO 34, GPIO 34 -> 20k -> GND, VCC -> USB (5 V)
//   Button GPIO 14 to GND
//  For live BMP180 readings use firmware/diagnostics/bmp180_test.
// =====================================================================
#include <Wire.h>

const int GAS_PIN = 34, BUTTON_PIN = 14;

// Reads one register; returns -1 if nobody answers at that address
int readReg(uint8_t addr, uint8_t reg) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return -1;
  if (Wire.requestFrom((int)addr, 1) != 1) return -1;
  return Wire.read();
}

void setup() {
  Serial.begin(115200);
  delay(1500);
  pinMode(2, OUTPUT);              // Feather V2: powers the NeoPixel and the I2C pull-ups
  digitalWrite(2, HIGH);
  delay(50);
  Wire.begin(SDA, SCL);
  analogSetPinAttenuation(GAS_PIN, ADC_11db);
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  Serial.println("\n=== STRAP SENSOR CHECK ===");
}

void loop() {
  // 1) I2C: list everything that answers, then check the two chips we expect
  Serial.print("I2C devices:");
  int found = 0;
  for (uint8_t a = 1; a < 127; a++) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) { Serial.printf(" 0x%02X", a); found++; }
  }
  Serial.println(found ? "" : " NONE (check SDA/SCL, 3V, GND)");

  int mpuId = readReg(0x68, 0x75);
  Serial.printf("MPU-6050: %s\n", mpuId < 0 ? "NOT FOUND at 0x68"
                : (mpuId == 0x68 || mpuId == 0x72 || mpuId == 0x70) ? "OK" : "answers, unusual ID (clone?)");

  int bmpId = readReg(0x77, 0xD0);
  if (bmpId < 0) Serial.println("BMP180  : NOT FOUND at 0x77 - check VIN->3V, GND, SDA->SDA, SCL->SCL");
  else if (bmpId == 0x55) Serial.println("BMP180  : OK (ID 0x55)");
  else if (bmpId == 0x58) Serial.println("BMP180  : the chip is a BMP280 (ID 0x58), not a BMP180 - the strap code won't read it");
  else if (bmpId == 0x60) Serial.println("BMP180  : the chip is a BME280 (ID 0x60), not a BMP180 - the strap code won't read it");
  else Serial.printf("BMP180  : something at 0x77 with ID 0x%02X (expected 0x55)\n", bmpId);

  // 2) MQ-2 pin: spread over 50 samples (a floating pin wanders, a driven one is steady)
  int lo = 99999, hi = 0;
  long sum = 0;
  for (int i = 0; i < 50; i++) {
    int mv = analogReadMilliVolts(GAS_PIN);
    lo = min(lo, mv); hi = max(hi, mv); sum += mv;
    delay(2);
  }
  int avg = sum / 50;
  Serial.printf("MQ-2    : GPIO34 avg %d mV (min %d, max %d) -> sensor %.2f V%s\n", avg, lo, hi, avg * 1.5 / 1000.0,
                avg > 3000 ? "  <- at the TOP: AO may reach the pin WITHOUT the divider (unplug and check!)"
                : avg < 200 ? "  <- ~0 V: MQ-2 not powered (VCC -> USB 5 V), GND or AO not connected"
                : (hi - lo > 300 ? "  <- jumping around: pin probably floating" : ""));

  // 3) Button
  Serial.printf("Button  : GPIO14 %s\n\n", digitalRead(BUTTON_PIN) == LOW ? "PRESSED (LOW)" : "released (HIGH)");
  delay(1000);
}
