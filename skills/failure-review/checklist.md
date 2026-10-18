# What to check, per kind of block

For step 3 of the failure review. Each line is a question to answer from the netlist, the datasheet or a
calculation; the answer goes into the briefing as a finding or under "verified OK". Examples name the board where
it mattered.

## Every IC

- Each pin's net against the datasheet's pin table, for the package suffix actually ordered. FET pinouts differ
  between SOT-23 parts; check the footprint's pad numbers against the datasheet's drawing.
- Exposed pad: on the right net, with vias, with paste.
- Strap, config and mode pins: at the level meant, through a resistor where a wire might have to override them.
  None floating unless the datasheet gives an internal pull (the CH224K has none on CFG2/CFG3; the CH224A does).
- Absolute maximum of each pin in every state, including with the chip unpowered while its neighbours are not.
- Decoupling as the datasheet asks, at the pins it names.
- The state at power-up before any firmware runs: enables, gates, outputs. A FET gate driven from a GPIO has a
  pull-down, so the load stays off while the pin floats at reset.

## Power input and protection

- The highest voltage each input can see: the wrong adapter, a reversed plug, ringing at hot-plug (ceramic input
  capacitors on a long cable can ring well above the supply; Linear Technology AN88).
- TVS standoff above the highest operating voltage, clamp below the downstream absolute maximum (dodeca v1.3 chose
  a 14 V part because a 12 V one would conduct inside the 14 V range).
- Reverse polarity: what conducts, and for how long the board survives it.
- Fuse and current limits in the right order: the upstream one must not open before the downstream limit acts.
- Inrush at attach against what the source tolerates (USB: bulk capacitance on VBUS).
- Two sources: what each sees when the other is present, absent, or higher. Back-feed into a PC, a charger or a
  shared bus (dodeca's uncut Teensy trace; siebenuhr's EXT_5V joined to USB VBUS by 0 Ω links).

## Regulators and converters

- Values from the datasheet's equations, at the tolerance corners: output voltage, current limit, UVLO and enable
  thresholds (dodeca v1.3's mux hand-over: 4.03 V nominal, 3.65–4.47 V at the corners).
- Output capacitance inside the stability range at its DC bias, and with the ESR the datasheet assumes.
- Inductor saturation current above the switch current limit, or the converter's peak current in a fault.
- Soft-start into the real load and its bulk capacitance, including a load that is already drawing at start-up
  (1,620 WS2812s draw a standing current while dark).
- Linear parts: (Vin − Vout) × I against the package's θJA (the SPX3819 burns about 0.4 W at 250 mA from 5 V).
- Layout against the datasheet's example: input-capacitor loop, switch node area, feedback divider at the IC and
  away from the switch node, Kelvin sense connections (`pcbkit netview` of the region).

## Batteries and chargers

- The power path in every mode (charging, supplementing, boost or OTG, ship mode, HIZ) and what each output sees in
  each (dodeca v1.2: VBUS straight to PMID while charging).
- TS or NTC network at the corners, from the reference that actually feeds it, in every mode (v1.2: REGN moved).
- Protection IC recovery: what it takes to come back from over-discharge (the XB7608AJ needs a charger).
- Register defaults, and what a watchdog reset clears (the BQ25890's clears EN_HIZ).
- Inrush when a FET connects the cells into the system capacitance.

## Muxes, load switches, eFuses

- Switch-over thresholds and hysteresis at the corners; the gap at hand-over against the load's hold-up.
- Reverse-current blocking: the threshold where it engages (TPS2116: 42 mV) and what flows below it.
- Leakage of a blocked input floating a node that something else reads (TPS2121: up to ±500 µA; dodeca added a bleed).
- Fault behaviour: latch-off or retry, and who clears it.

## Microcontroller or module

- Strapping pins at the right level at reset (ESP32-S3: GPIO0, 3, 45, 46), and nothing external that fights them.
- Pins the boot ROM drives, and pins that must float (Teensy 4.1 pins 24/25 on dodeca v1.3).
- Reset and enable timing against the supply's rise; brown-out threshold against the rail's worst dip.
- Programming and debug path reachable on the assembled board, in the enclosure.
- ADC inputs: the divider's source impedance against the ADC's sampling (dodeca's open question on 32 kΩ sources).

## Connectors and anything that leaves the board

- ESD protection and a series resistor on every line that runs to a cable or a user.
- Shorts to the neighbouring pin (USB-C CC sits next to VBUS), mis-mating, a reversed cable.
- The board unpowered while the far end is powered, and the other way round.
- Contact current rating against the real current.

## LED data and level shifting

- High level at the first LED against its VIH at its own supply (WS2812B: 0.7 × VDD, 3.5 V at 5 V).
- A buffer that isolates when its rail is off (LVC1T45, AHCT1G125) rather than one that is back-powered (HCT).
- Series resistor at the driver, ESD at the connector, for long cables.

## Buses

- I2C pull-ups: value against bus capacitance and rise time, on a rail that is up whenever the bus is used and that
  can't be fed through them (the CH224A is powered from VBUS).
- Address conflicts, including parts with a default address that moves with a strap.

## Current paths and heat

- Track and pour width for the current (KiCad's calculator, IPC-2152), via count at layer changes, and the ratings
  of 0 Ω links, jumpers, shunts and fuses in the path (siebenuhr's 0805 0 Ω at 2 A).
- Dissipation of each part that gets warm, what sits next to it, and the enclosure's air.

## Fab and assembly

- Polarity of diodes, LEDs and polarised capacitors on silk and in the CPL preview; rotations (PRODUCTION.md's
  checklist).
- BOM matched by LCSC code, not by value; parts JLC can't place, and hand-soldered parts listed for the builder.
- Stock of every part, and the substitute for the low ones with the circuit change it needs (bom-cost skill).

## Firmware duties

- Every hardware state that depends on firmware, and the state with no firmware, a crash, or a watchdog reset.
- List them in the briefing for whoever writes the firmware (dodeca v1.3 SPEC §11).
