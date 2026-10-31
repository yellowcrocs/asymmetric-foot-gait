/*
  FootGait - ESP32-WROOM per-foot force node
  ------------------------------------------
  Reads 4x FSR402 through 3.3 V dividers into ADC1 pins,
  streams CSV frames over WiFi/UDP to the laptop logger,
  and listens for "ACK:<id>:<seq>" replies.

  Build the same sketch for either foot, selecting the id:

      arduino-cli compile --fqbn esp32:esp32:esp32 \
          --build-property 'compiler.cpp.extra_flags=-DDEVICE_ID=\"R\"' \
          firmware/foot_node

  Copy secrets.example.h to secrets.h first (wifi credentials are not
  committed).

  Frame sent (one UDP datagram per sample):
      seq,id,millis,fsr0,fsr1,fsr2,fsr3
  Example:
      42,R,15123,120,450,380,900

  The laptop logger must:
      - bind UDP 4210
      - reply "ACK:<id>:<seq>" unicast to the sender (board) port 4211

  FSR wiring (repeat for each of the 4 sensors):
      3V3 ---[FSR402]---+--- GPIOxx (ADC)
                        |
                      [10k]
                        |
                       GND

  Pins (ADC1 only - ADC2 is unusable while WiFi is on):
      fsr0 heel      -> GPIO34 (ADC1_CH6)
      fsr1 ball_in   -> GPIO35 (ADC1_CH7)
      fsr2 ball_out  -> GPIO32 (ADC1_CH4)
      fsr3 toes      -> GPIO33 (ADC1_CH5)

  Power: powerbank 5 V -> VIN/5V pin, common GND. Divider top to 3V3.
*/

#include <WiFi.h>
#include <WiFiUdp.h>

#include "secrets.h"

#ifndef DEVICE_ID
#error Compile with -DDEVICE_ID=\"L\" or -DDEVICE_ID=\"R\"
#endif

// Network
const uint16_t LAPTOP_PORT = 4210;        // logger listens here
const uint16_t LOCAL_PORT  = 4211;        // board listens here for ACKs
const bool USE_BROADCAST   = true;        // set false if the AP blocks broadcast
IPAddress LAPTOP_IP(192, 168, 1, 100);    // only used when USE_BROADCAST = false

// Sampling
const uint32_t SAMPLE_HZ    = 50;                 // frames per second
const uint32_t SAMPLE_DT_US = 1000000UL / SAMPLE_HZ;
const uint8_t  ADC_AVG      = 4;                  // averages per channel

// ======================= FSR PINS ============================
#define FSR_COUNT 4
const uint8_t FSR_PIN[FSR_COUNT] = { 34, 35, 32, 33 };
const char*   FSR_NAME[FSR_COUNT] = { "heel", "ball_in", "ball_out", "toes" };

// ======================= GLOBALS =============================
WiFiUDP udp;

uint32_t seq        = 0;
uint32_t sentCount  = 0;
uint32_t ackCount   = 0;
uint32_t lastAckSeq = 0;
uint32_t lastStatMs = 0;
uint32_t lastReconnectMs = 0;
uint32_t nextSampleUs = 0;
bool     wifiUp = false;

IPAddress laptopIP;
bool      haveLaptop = false;
uint32_t  lastAckMs = 0;

// ======================= HELPERS =============================
int readFsr(uint8_t pin) {
  uint32_t acc = 0;
  for (uint8_t i = 0; i < ADC_AVG; i++) {
    acc += analogRead(pin);
    delayMicroseconds(30);
  }
  return (int)(acc / ADC_AVG);
}

void connectWiFi() {
  Serial.printf("[WiFi] connecting to %s", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.setTxPower(WIFI_POWER_19_5dBm);
  WiFi.begin(WIFI_SSID, WIFI_PASS);

  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 15000) {
    delay(500);
    Serial.print(".");
  }
  if (WiFi.status() == WL_CONNECTED) {
    wifiUp = true;
    Serial.printf("\n[WiFi] OK  IP=%s\n", WiFi.localIP().toString().c_str());
  } else {
    wifiUp = false;
    Serial.println("\n[WiFi] NOT connected (will retry)");
  }
}

void maintainWiFi() {
  if (WiFi.status() == WL_CONNECTED) {
    wifiUp = true;
    return;
  }
  wifiUp = false;
  uint32_t now = millis();
  if (now - lastReconnectMs < 5000) return;
  lastReconnectMs = now;
  Serial.println("[WiFi] lost - reconnecting");
  WiFi.disconnect();
  WiFi.begin(WIFI_SSID, WIFI_PASS);
}

void handleAck() {
  while (udp.parsePacket() > 0) {
    char buf[64];
    int len = udp.read(buf, sizeof(buf) - 1);
    if (len <= 0) continue;
    buf[len] = '\0';

    IPAddress src = udp.remoteIP();

    char ackId[16];
    unsigned long ackSeq = 0;
    if (sscanf(buf, "ACK:%15[^:]:%lu", ackId, &ackSeq) != 2) continue;
    if (strcmp(ackId, DEVICE_ID) != 0) continue;

    // Never trust an ACK for a frame we have not sent, or an old one.
    if (ackSeq == 0 || ackSeq > seq) continue;
    if (ackSeq <= lastAckSeq) continue;

    // While unicast is active, only the registered laptop may re-register.
    bool unicastActive = haveLaptop && (millis() - lastAckMs < 3000);
    if (unicastActive && src != laptopIP) continue;

    laptopIP = src;
    haveLaptop = true;
    lastAckMs = millis();
    lastAckSeq = (uint32_t)ackSeq;
    ackCount++;
  }
}

void sendSample() {
  int v[FSR_COUNT];
  for (uint8_t i = 0; i < FSR_COUNT; i++) v[i] = readFsr(FSR_PIN[i]);

  seq++;
  char buf[96];
  int n = snprintf(buf, sizeof(buf), "%lu,%s,%lu,%d,%d,%d,%d",
                   (unsigned long)seq, DEVICE_ID, (unsigned long)millis(),
                   v[0], v[1], v[2], v[3]);
  if (n <= 0) return;
  if (n >= (int)sizeof(buf)) n = sizeof(buf) - 1;

  bool useUnicast = haveLaptop && (millis() - lastAckMs < 3000);
  if (useUnicast) {
    udp.beginPacket(laptopIP, LAPTOP_PORT);
  } else if (USE_BROADCAST) {
    udp.beginPacket(IPAddress(255, 255, 255, 255), LAPTOP_PORT);
  } else {
    udp.beginPacket(LAPTOP_IP, LAPTOP_PORT);
  }
  udp.write((const uint8_t*)buf, n);
  udp.endPacket();
  sentCount++;
}

void printStats() {
  uint32_t now = millis();
  if (now - lastStatMs < 1000) return;
  lastStatMs = now;

  uint32_t loss = 0;
  if (sentCount > ackCount) loss = (sentCount - ackCount) * 100UL / sentCount;
  Serial.printf("[%s] sent=%lu acked=%lu loss=%lu%% lastAck=%lu wifi=%d uc=%d rssi=%d\n",
                DEVICE_ID, (unsigned long)sentCount, (unsigned long)ackCount,
                (unsigned long)loss, (unsigned long)lastAckSeq, wifiUp ? 1 : 0,
                haveLaptop ? 1 : 0, WiFi.RSSI());
}

// ======================= SETUP / LOOP ========================
void setup() {
  Serial.begin(115200);
  delay(100);

  analogReadResolution(12);
  analogSetAttenuation(ADC_11db);   // ~0..3.1 V usable

  Serial.println();
  Serial.printf("[BOOT] FootGait node id=%s, %lu Hz, %d FSRs\n",
                DEVICE_ID, (unsigned long)SAMPLE_HZ, FSR_COUNT);
  Serial.printf("[BOOT] pins: %s=%d %s=%d %s=%d %s=%d\n",
                FSR_NAME[0], FSR_PIN[0], FSR_NAME[1], FSR_PIN[1],
                FSR_NAME[2], FSR_PIN[2], FSR_NAME[3], FSR_PIN[3]);

  connectWiFi();

  if (!udp.begin(LOCAL_PORT)) {
    Serial.printf("[UDP] failed to bind port %u\n", LOCAL_PORT);
  } else {
    Serial.printf("[UDP] listening on %u, sending to laptop:%u\n",
                  LOCAL_PORT, LAPTOP_PORT);
  }

  nextSampleUs = micros();
  lastStatMs = millis();
}

void loop() {
  maintainWiFi();
  handleAck();

  if (!wifiUp) {
    delay(5);
    return;
  }

  uint32_t now = micros();
  if ((int32_t)(now - nextSampleUs) >= 0) {
    nextSampleUs += SAMPLE_DT_US;
    // If we fell badly behind, resync instead of bursting.
    if ((int32_t)(now - nextSampleUs) > (int32_t)SAMPLE_DT_US) {
      nextSampleUs = now + SAMPLE_DT_US;
    }
    sendSample();
  }

  printStats();
}
