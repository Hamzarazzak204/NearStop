// =====================================================================
//  BMP180: temperature + air pressure (standalone test)
//  For the strap (Adafruit Feather ESP32 V2). Serial Monitor at 115200.
//  NO library needed: it reads the chip's registers directly (works with clones too).
//  It replaces the DHT11. Note: the BMP180 measures temperature and air
//  pressure, NOT humidity.
//
//  WIRING (BMP180 board -> Feather V2). It shares the two I2C wires with the MPU-6050:
//    VIN (or VCC, or 3.3) -> 3V
//    GND -> GND
//    SDA -> SDA  (GPIO 22, same breadboard row as the MPU-6050's SDA)
//    SCL -> SCL  (GPIO 20, same breadboard row as the MPU-6050's SCL)
//  Its I2C address is 0x77. The MPU-6050 is 0x68, so they don't clash.
//  Keep it facing out, away from the skin and from the MQ-2 heater.
//
//  QUICK CHECKS:
//    - Breathe on it: the temperature goes up.
//    - Lift it from the floor to above your head (about 2 m): the pressure
//      drops by about 0.2 hPa.
//
//  FOR THE CLAUDE INTEGRATING THIS INTO strap/strap_main/strap_main.ino:
//    - Copy the block between "BMP180 MODULE: copy from here" and "copy to here".
//    - setup(): call bmpBegin(); after Wire.begin(SDA, SCL) and restartImu().
//    - loop():  call bmpUpdate(now); (it replaces readDht(now)).
//    - Results: bmpTempC (C) and bmpPressureHpa (hPa). NAN means no valid
//      reading, so send null, like the DHT11 code did.
//    - bmpUpdate() never waits. It starts a measurement and collects it on a
//      later loop, so each call is at most one short I2C transfer (well under
//      1 ms). Unlike the DHT11, it doesn't need to be skipped during a fall.
//    - Everything is named bmp... / BMP_..., so nothing clashes with the
//      MPU-6050's readReg()/writeReg().
//    - restartImu() calls Wire.end()/Wire.begin(). That's fine: the BMP180's
//      calibration is kept in this code's variables, and a failed read is retried.
// =====================================================================
#include <Arduino.h>
#include <Wire.h>

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

void setup() {
  Serial.begin(115200);
  delay(1500);
  Serial.println();
  Serial.println("=== BMP180 TEST: temperature + air pressure ===");
#if defined(NEOPIXEL_I2C_POWER)
  pinMode(NEOPIXEL_I2C_POWER, OUTPUT);   // Feather V2: powers the I2C pull-ups
  digitalWrite(NEOPIXEL_I2C_POWER, HIGH);
  delay(50);
#endif
  Wire.begin(SDA, SCL);
  if (bmpBegin()) Serial.println("BMP180 found at 0x77.");
  else Serial.println("BMP180 NOT found at 0x77. Check: VIN->3V, GND->GND, SDA->SDA, SCL->SCL. Retrying every 5 s...");
}

void loop() {
  unsigned long now = millis();
  bmpUpdate(now);

  static unsigned long lastPrint = 0;
  if (now - lastPrint >= 2000) {
    lastPrint = now;
    if (isnan(bmpTempC)) Serial.println("temperature --   pressure --");
    else Serial.printf("temperature %.1f C   pressure %.2f hPa\n", bmpTempC, bmpPressureHpa);
  }
  delay(2);
}
