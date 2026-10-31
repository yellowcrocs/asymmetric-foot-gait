/*
  FootGait serial calibration reader
  ----------------------------------
  Prints the four raw FSR channels over USB serial at 10 Hz:

      millis,fsr0,fsr1,fsr2,fsr3

  Pins (same for both feet):
      fsr0 heel      -> GPIO34
      fsr1 ball_in   -> GPIO35
      fsr2 ball_out  -> GPIO32
      fsr3 toes      -> GPIO33

  Use this only for calibration. After recording the raw values, reflash
  firmware/right_foot (or firmware/left_foot) to restore the UDP stream.
*/

#include <Arduino.h>

const uint8_t FSR_PINS[4] = {34, 35, 32, 33};
const uint8_t ADC_AVG = 8;
const uint32_t PRINT_MS = 100;

int readAvg(uint8_t pin) {
  uint32_t acc = 0;
  for (uint8_t i = 0; i < ADC_AVG; i++) {
    acc += analogRead(pin);
    delayMicroseconds(50);
  }
  return (int)(acc / ADC_AVG);
}

void setup() {
  Serial.begin(115200);
  delay(200);
  analogReadResolution(12);
  analogSetAttenuation(ADC_11db);
  Serial.println("# FootGait serial calibration");
  Serial.println("# pins: heel=34 ball_in=35 ball_out=32 toes=33");
  Serial.println("# millis,fsr0,fsr1,fsr2,fsr3");
}

void loop() {
  static uint32_t last = 0;
  uint32_t now = millis();
  if (now - last < PRINT_MS) return;
  last = now;

  int v[4];
  for (uint8_t i = 0; i < 4; i++) v[i] = readAvg(FSR_PINS[i]);

  Serial.printf("%lu,%d,%d,%d,%d\n",
                (unsigned long)now, v[0], v[1], v[2], v[3]);
}
