# Parts that worked

Status (2026-10-11): first collection, from the candle, rgb-spotlight, siebenuhr, the esphome dimmer switch, the
ESP32 HA protoboard, motion-sound and dodeca. Stock figures come from konnect's JLC parts cache dated 2026-09-29;
`pcbkit parts` prints the current numbers from the cache. Check with bomi before an order.

A part goes in the first section once it has been on a board that was built and worked. Parts that are in a design
but not built yet go in the second section, with the project that will prove them. The stock resistors and
capacitors live in `pcbkit/parts.py`.

## Built and working

### Level shifting and LED data

| Part | LCSC | Package | Stock | Use |
| --- | --- | --- | --- | --- |
| SN74LVC1T45DBVR | C7843 | SOT-23-6 | 408k | one LED data line, 3.3 V to 5 V |
| SN74LVC1T45DCKR | C9382 | SC-70-6 | 41k | same part, smaller package |
| SN74AHCT1G125DBVR | C7484 | SOT-23-5 | 50k | buffer and repeater for long WS2812 chains |
| SN74AHCT1G125DCKR | C350557 | SC-70-5 | 18k | same part, smaller package |
| 74HCT125D,653 | C5962 | SOIC-14 | 87k | four channels: SPI to 5 V devices, several LED lines |

- **SN74LVC1T45** (siebenuhr v2, dodeca v1.2 ×4, the dimmer switch, the HA protoboard): VCCA on 3.3 V, VCCB on the
  LED's 5 V, DIR tied to VCCA for A to B. Both ports go high-impedance when either supply is at 0 V, so an unpowered
  LED rail doesn't get fed through the data line. siebenuhr v3 adds 100 Ω in series and a 7 V ESD diode at the
  connector. The SOT-23-6 version costs a third of the SC-70-6 and has ten times the stock.
- **SN74AHCT1G125** (rgb-spotlight driver; dodeca v1.3's top pick, one per connector): runs on 5 V only, and its TTL
  input threshold (2.0 V) takes 3.3 V logic directly. The inputs tolerate 5.5 V and have no clamp to VCC, so nothing
  back-powers when the buffer's rail is off. Used as a repeater in panels with many WS2812s in series.
- **74HCT125** (HA protoboard SPI buffer with a 3.3/5 V jumper; the 74HCT125N in DIP on esp32-cyberclock): four
  buffers for the price of one package. HCT inputs clamp to VCC, so if the buffer's supply is off while the ESP32
  drives the inputs, the chip and its rail get powered through them. Use it with an always-on VCC, or use AHCT parts.

### LED drivers and switches

| Part | LCSC | Package | Stock | Use |
| --- | --- | --- | --- | --- |
| BCR421UFD-7 | C168950 | UDFN-6 2×2 | 0 | linear constant-current driver, white LEDs |
| BCR421UW6Q-7 | C154739 | SOT-26 | 8k | same driver in a leaded package, in stock |
| PAM2804AAB010 | C71463 | TSOT-25 | 25k | buck constant-current driver, UV LED |
| WPN4020H4R7MT | C98363 | 4×4 mm | 62k | 4.7 µH inductor for the PAM2804 |
| AO3402 | C14385 | SOT-23 | 127k | low-side switch for small LEDs and PWM loads |
| B5819W SL | C8598 | SOD-123 | 496k | flyback diode, Basic part |

- **BCR421** (ESP32 candle 1.4.0, the white COB LED channels): needs only the current-set resistor, and dims by PWM
  on EN. It is linear, so the difference between supply and LED forward voltage burns in the part; keep the supply
  close to the LED's Vf. Diodes rates it at 350 mA without an external transistor. The UDFN version the candle used
  is out of stock; the SOT-26 BCR421UW6Q is the drop-in for a new layout.
- **PAM2804** (ESP32 candle, the 365 nm UV LED at about 300 mA): regulates 0.1 V across the sense resistor, so
  0.33 Ω gives 300 mA. PWM on EN. Runs from 2.5–6 V, so 5 V is fine. Use the 4.7 µH inductor: an earlier
  revision drove the white LEDs from PAM2804s with 2.2 µH, and the review of its heat problems found that too
  small for the 1.5 MHz switching.
- **AO3402 with B5819W** (ESP32 candle red LEDs at 35 mA each; the HA protoboard's PWM channels): a 4 A logic-level
  N-FET that switches fully from a 3.3 V GPIO, with a 100 Ω gate resistor and a 10 kΩ pull-down. The B5819W goes
  across inductive loads (motors, solenoids, LED strips on long cables). GOODWORK's AO3402 (C2938369) is a third of
  the price with more stock.

### Power for small ESP32 boards

| Part | LCSC | Package | Stock | Use |
| --- | --- | --- | --- | --- |
| LMR50410XDBVR | C2841056 | SOT-23-6 | 33k | 1 A buck, 4–36 V in, for the 5 V rail |
| SPX3819M5-L-3-3/TR | C9055 | SOT-23-5 | 11k | 500 mA LDO, 3.3 V for the ESP32 |

- **LMR50410 and SPX3819** (ESP32 candle base): a small buck for 5 V and an LDO from it for 3.3 V. Fine for boards
  that drive a few LEDs. The LDO burns (5 − 3.3 V) × current, about 0.4 W at 250 mA in a SOT-23-5. The dimmer switch
  review found that too tight for its load and replaced the LDO with a 3.3 V buck. TECH PUBLIC's SPX3819 (C20617301)
  has four times MaxLinear's stock at a third of the price.

### USB-C power delivery

| Part | LCSC | Package | Stock | Use |
| --- | --- | --- | --- | --- |
| CH224K | C970725 | ESSOP-10 | 0 | USB-PD sink, voltage set by CFG pins |

- **CH224K** (siebenuhr v2, the dimmer switch; wagiminator's USB-PD decoy in `~/src/Power-Boards` is the reference):
  VDD through 1 kΩ with 1 µF, VBUS sense through 10 kΩ. The requested voltage is set by the CFG pins.
- **siebenuhr's failure analysis** (2026-10-04) is the lesson. With CFG1 tied to VBUS it asks for 5 V, and a 5 V
  contract allows at most 3 A. The LEDs drew far more at full brightness, and nothing in the firmware capped it.
  After six months the board no longer works on any PD supply; a damaged CH224K or CC front end is the leading
  suspect, with repeated overcurrent resets on unprotected CC and VBUS pins as the likely stress. Ask for the
  highest voltage the board can use, cap LED power in firmware, and protect CC and VBUS.
- **Out of stock at JLC.** siebenuhr v3 uses the CH224A (below). The CH224A has an I2C interface and is powered from
  VBUS, so the pull-ups on its I2C lines must not go to the 3.3 V rail: that would back-feed it when only DC power
  is present.

### Audio

| Part | LCSC | Package | Stock | Use |
| --- | --- | --- | --- | --- |
| MAX98357AETE+T | C910544 | TQFN-16 3×3 | 29k | I2S mono class-D amplifier, 3.2 W into 4 Ω |

- **MAX98357** (motion-sound, as a breakout module on perfboard with ESPHome): takes I2S directly, with no MCLK.
  With SD and GAIN unconnected it plays (L+R)/2 at 9 dB. The outputs are bridge-tied: connect the speaker across
  them, never to ground, 4 Ω or more. Keep DIN off the ESP32's strapping pins (GPIO15 on the classic ESP32). On a
  PCB, the bare chip's exposed pad needs vias to ground.

## Designed in, not built yet

| Part | LCSC | Package | Stock | Use | Proven by |
| --- | --- | --- | --- | --- | --- |
| CH224A | C42459160 | ESSOP-10 | 19k | USB-PD sink with I2C, replaces the CH224K | siebenuhr v3 |
| TPS55288RPMR | C2864583 | VQFN-26 | 5k | buck-boost LED rail with I2C control | siebenuhr v3 |
| TPS2116DRLR | C3235557 | SOT-583 | 86k | two-input supply mux, priority mode | dodeca control board v1.3 |
| TPS5430DDAR | C9864 | SOIC-8-EP | 364k | 3 A buck from 7–36 V, Basic part | the dimmer switch, esp32-s3-ha-node |

## Lessons across these

- Two of the parts that worked (CH224K, BCR421UFD) went out of stock between builds. Run `pcbkit parts` or
  `bomi list --check` when a design starts, not only when it is ordered.
- Where TI sells the same logic part in SOT-23 and SC-70, the SOT-23 version has usually had more stock at a lower
  price, and it is easier to hand-solder.
- For anything that drives a connector, check what happens when one side is unpowered: LVC1T45 and AHCT parts
  isolate, HCT parts back-power.
