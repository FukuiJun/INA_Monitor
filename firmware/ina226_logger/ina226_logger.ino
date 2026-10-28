// M5Stack Unit INA226-1A -> Arduino -> シリアルにCSV出力
// 配線（Uno）: 黒=GND, 赤=5V, 黄=SDA(A4), 白=SCL(A5)

#include <Wire.h>

const uint8_t INA226_ADDR = 0x41;
const float SHUNT_OHM = 0.080;          // 1A版のシャント抵抗
const unsigned long INTERVAL_MS = 50;   // 出力間隔

const uint8_t REG_CONFIG = 0x00;
const uint8_t REG_SHUNT  = 0x01;
const uint8_t REG_BUS    = 0x02;
const uint8_t REG_MFG_ID = 0xFE;

// CONFIG: AVG=16回, VBUSCT=1.1ms, VSHCT=1.1ms, 連続測定(シャント+バス)
const uint16_t CONFIG_VALUE = 0x4000 | (0b010 << 9) | (0b100 << 6) | (0b100 << 3) | 0b111;

bool writeReg(uint8_t reg, uint16_t val) {
  Wire.beginTransmission(INA226_ADDR);
  Wire.write(reg);
  Wire.write((uint8_t)(val >> 8));
  Wire.write((uint8_t)(val & 0xFF));
  return Wire.endTransmission() == 0;
}

bool readReg(uint8_t reg, uint16_t &val) {
  Wire.beginTransmission(INA226_ADDR);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(INA226_ADDR, (uint8_t)2) != 2) return false;
  uint8_t hi = Wire.read();
  uint8_t lo = Wire.read();
  val = ((uint16_t)hi << 8) | lo;
  return true;
}

void scanI2C() {
  Serial.println(F("# I2C scan..."));
  uint8_t found = 0;
  for (uint8_t a = 1; a < 127; a++) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) {
      Serial.print(F("#  found 0x"));
      Serial.println(a, HEX);
      found++;
    }
  }
  if (found == 0) Serial.println(F("#  no device"));
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  Wire.begin();

  scanI2C();

  while (true) {
    uint16_t id = 0;
    bool ok = readReg(REG_MFG_ID, id);
    if (ok && id == 0x5449) break;
    Serial.print(F("# INA226 not found (ok="));
    Serial.print(ok);
    Serial.print(F(", id=0x"));
    Serial.print(id, HEX);
    Serial.println(F(") retry..."));
    delay(1000);
  }

  writeReg(REG_CONFIG, CONFIG_VALUE);
  Serial.println(F("time_ms,bus_V,shunt_uV,current_mA"));
}

void loop() {
  static unsigned long last = 0;
  unsigned long now = millis();
  if (now - last < INTERVAL_MS) return;
  last = now;

  uint16_t rawShunt, rawBus;
  if (!readReg(REG_SHUNT, rawShunt) || !readReg(REG_BUS, rawBus)) {
    Serial.println(F("# read error"));
    return;
  }

  float shunt_uV   = (int16_t)rawShunt * 2.5;           // LSB 2.5uV（符号付き）
  float bus_V      = rawBus * 1.25e-3;                  // LSB 1.25mV
  float current_mA = shunt_uV / SHUNT_OHM / 1000.0;     // I = V / R

  Serial.print(now);         Serial.print(',');
  Serial.print(bus_V, 4);    Serial.print(',');
  Serial.print(shunt_uV, 1); Serial.print(',');
  Serial.println(current_mA, 3);
}
