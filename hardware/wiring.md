# FootGait — Per-Foot Wiring (ESP32-WROOM)

Rendered one-foot diagram: `wiring.png` (regenerate with
`python make_wiring_diagram.py`).

Applies to **both feet**. The circuit is identical; only the firmware
`DEVICE_ID` differs (`"L"` vs `"R"`). Sensor *placement* is mirrored
(left/right), but the electrical channel order stays anatomical:
**heel → ball-inner → ball-outer → toes**.

---

## Bill of materials (per foot)

| Part | Qty | Notes |
|---|---|---|
| ESP32-WROOM-32 devkit | 1 | one per foot |
| FSR402 | 4 | heel, ball-inner, ball-outer, toes |
| 10 kΩ resistor | 4 | pulldown, one per FSR |
| Jumper wires | — | keep short |
| Powerbank 5 V | shared or 1/foot | feeds `VIN` / 5V pin |

Total resistors for both feet: **8 × 10 kΩ**.

---

## Voltage divider (repeat 4×, one per FSR)

```
   3V3 ●───────[ FSR402 ]───────●───────────────► GPIOxx  (ADC1)
                                │
                             [ 10 kΩ ]
                                │
                               GND ●
```

- More force → FSR resistance drops → node voltage **rises**.
- No load → node ≈ 0 V. Full press → node approaches 3.3 V.
- The ESP32 ADC reads 0–4095 (12-bit). With 11 dB attenuation the usable
  range is ≈ 0–3.1 V, so expect the very top of the scale to clip.
- **Never** put 5 V on an ADC pin. The divider top must be **3V3**.

---

## Pin map (same for left and right foot)

| FSR position | FSR top | ADC node (divider) | ESP32 pin | ADC channel |
|---|---|---|---|---|
| heel | 3V3 | GPIO34 | **GPIO34** | ADC1_CH6 |
| ball-inner | 3V3 | GPIO35 | **GPIO35** | ADC1_CH7 |
| ball-outer | 3V3 | GPIO32 | **GPIO32** | ADC1_CH4 |
| toes | 3V3 | GPIO33 | **GPIO33** | ADC1_CH5 |
| 10 kΩ bottoms | — | — | **GND** | — |

All four are **ADC1**, which keeps working while WiFi is on.
(`GPIO34/35/39/36` are input-only — fine for ADC.)

---

## Power

```
Powerbank 5 V (+) ──► ESP32  VIN (or 5V pin)
Powerbank 5 V (−) ──► ESP32  GND
ESP32 3V3         ──► top of all four FSR dividers
ESP32 GND         ──► bottom of all four 10 kΩ resistors
```

- The devkit's onboard regulator makes the 3.3 V rail — do not feed 3.3 V
  into `VIN`.
- If each foot has its own powerbank, the two grounds are **not** tied
  together (they don't need to be — the link is WiFi).
- Some powerbanks auto-cut at low current. ESP32 + WiFi usually draws
  enough to stay awake; if one drops out, add a small dummy load or keep
  the serial monitor connected during bring-up.

---

## Rail distribution (mental check)

```
ESP32 3V3 ──┬── FSR(heel) ──────► GPIO34 ──[10k]── GND
            ├── FSR(ball-in) ───► GPIO35 ──[10k]── GND
            ├── FSR(ball-out) ──► GPIO32 ──[10k]── GND
            └── FSR(toes) ──────► GPIO33 ──[10k]── GND
```

Every ADC node sees exactly one FSR to 3V3 and one 10 kΩ to GND.

---

## Placement (4 sensors per foot)

```
        toes
          ▲
     [3]  •        <- toes
   [1] •  • [2]    <- ball-inner (1), ball-outer (2)
          │
        [0] •      <- heel
```

- **0 = heel**, **1 = ball-inner**, **2 = ball-outer**, **3 = toes**.
- Mirror left/right physically; keep the same channel numbers so the
  analyzer and heatmap stay consistent.

---

## Gotchas

1. **ADC2 is unusable when WiFi is enabled** — that's why only ADC1 pins
   are used above.
2. **ESP32 ADC is noisy and slightly nonlinear** near the rails. For
   absolute force, calibrate each FSR (no-load + known weight) at runtime.
3. **FSR402 saturates** under high force; it is better for *timing*
   (heel strike, toe-off) than for precise pressure.
4. Keep the divider wires short and share a solid GND; long leads pick up
   noise and make the already-noisy ADC worse.
5. Do not use GPIO0/2/12/15 for the dividers (boot-strapping pins).
