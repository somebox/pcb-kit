# What to check, per kind of block

For step 3 of the failure review. Each line is a question to answer from the netlist, the datasheet or a
calculation; the answer goes into the briefing as a finding, a firmware duty or a line under "verified OK".
Examples name the board where it mattered. Only the sections for the blocks in scope
apply. A question that applies and was not answered is an open check.

## Every IC

- Each pin's net against the datasheet's pin table, for the package suffix actually ordered. FET pinouts differ
  between SOT-23 parts; check the footprint's pad numbers against the datasheet's drawing.
- Each number with its column (min, typ or max) and its test condition. A limit read as typical may be the
  minimum (Orbitchron: the TLV62569's 3 A current limit, with no maximum given).
- Exposed pad: on the right net, with vias, with paste.
- Strap, config and mode pins: at the level meant, through a resistor where a wire might have to override them.
  None floating unless the datasheet gives an internal pull (the CH224K has none on CFG2/CFG3, the CH224A has
  pull-ups: WCH's CH224 datasheet V2.0, §7.1).
- Absolute maximum of each pin in every state, including with the chip unpowered while its neighbours are not.
- Decoupling as the datasheet asks, at the pins it names.
- The state at power-up before any firmware runs: enables, gates, outputs. A FET switched from a GPIO is held off
  by a resistor from gate to source while the pin floats at reset: a pull-down for a low-side N-FET, a pull-up to
  the source rail for a high-side P-FET (a pull-down there turns it on). dodeca v1.3's USB gate does both: R113 holds
  P-FET Q101's gate at its source, R114 holds its N-FET driver Q102 off.
- A chip with registers: the defaults at power-up, and what a reset or a watchdog puts back.
- Errata for the silicon revision that will be placed.

## Small parts: ratings and stress

- Resistor power in normal use and in the fault it sits in (its far end shorted, or on the wrong rail), and its
  voltage rating.
- Capacitor voltage with margin. A common rule (kicad-happy's default): a ceramic at no more than half its rated
  voltage, an electrolytic at no more than 80 %. The capacitance left at DC bias goes into every calculation.
- Electrolytic and polymer capacitors: ripple current, and life at the temperature of the part beside them.
- Diodes and LEDs: average and peak current, reverse voltage. A TVS: the energy of the event against its pulse
  rating. One that can't absorb it fails short (the sign controller's, with a 9–19 V adapter in its 5 V jack);
  then what interrupts the current, a fuse or the source's own limit, and what the board is left with if nothing
  does.
- PTC fuses: hold and trip current at the enclosure's temperature, the time to trip at the fault current, and
  what limits first (the sign controller's PTC is guaranteed to trip within 5 s only at 8 A; the adapter's own
  protection acts before it, and the whole sign goes dark).
- FETs: VGS and VDS in every state, and RDS(on) from the column for the gate voltage actually there.

## Power input and protection

- The highest voltage each input can see: the wrong adapter, a reversed plug, ringing at hot-plug (ceramic input
  capacitors on a long cable can ring well above the supply; Linear Technology AN88).
- TVS standoff above the highest operating voltage, clamp below the downstream absolute maximum (dodeca v1.3 chose
  a 14 V part because a 12 V one would conduct inside the 14 V range).
- Reverse polarity: what conducts, and for how long the board survives it.
- Fuse and current limits in the right order: the upstream one must not open before the downstream limit acts.
- Inrush at attach against what the source tolerates. USB: no more than 10 µF on VBUS at attach (Type-C
  specification R2.0, Table 4-3) and 100 µF once a PD contract stands (cSnkBulkPd); more needs an inrush limiter
  (siebenuhr had about 264 µF).
- A protection part's own input at hot-plug: the capacitance ahead of it, its over-voltage threshold and
  hysteresis against the ring and against the source's idle voltage (the sign controller's eFuse).
- Two sources: what each sees when the other is present, absent, or higher. Back-feed into a PC, a charger or a
  shared bus (dodeca's uncut Teensy trace; siebenuhr's EXT_5V joined to USB VBUS by 0 Ω links).

## Regulators and converters

- Values from the datasheet's equations, at the tolerance corners: output voltage, current limit, UVLO and enable
  thresholds (dodeca v1.3's mux hand-over: 4.03 V nominal, 3.65–4.47 V at the corners).
- Output capacitance inside the stability range at its DC bias, and with the ESR the datasheet assumes.
- Inductor saturation current above the switch current limit, or the converter's peak current in a fault.
- What a shorted output draws, for how long, and through which tracks and contacts.
- Soft-start into the real load and its bulk capacitance, including a load that is already drawing at start-up
  (1,620 WS2812s draw a standing current while dark).
- Linear parts: (Vin − Vout) × I against the package's θJA (the SPX3819 burns about 0.4 W at 250 mA from 5 V).
- Layout against the datasheet's example: input-capacitor loop, switch node area, feedback divider at the IC and
  away from the switch node, Kelvin sense connections (`pcbkit netview` of the region).

## Batteries and chargers

- The power path in every mode (charging, supplementing, boost or OTG, ship mode, HIZ) and what each output sees in
  each, not only on the datasheet's front page (dodeca v1.2: the BQ25895 passed VBUS straight to PMID while
  charging, and more than 5.5 V on VIN reached the LEDs).
- TS or NTC network at the corners, from the reference that actually feeds it, in every mode (v1.2: REGN moved
  with the charger's mode, false NTC faults stopped charging, and it was bodged to a fixed resistor).
- Protection IC recovery: what it takes to come back from over-discharge (the XB7608AJ needs a charger).
- Register defaults, and what a watchdog reset clears (the BQ25890's clears EN_HIZ).
- Inrush when a FET connects the cells into the system capacitance (dodeca v1.3: the BQ25890's BATFET into about
  83 µF of SYS, against a 9 A trip; on its bench list, with C402 as the fix).

## Muxes, load switches, eFuses

- Switch-over thresholds and hysteresis at the corners; the gap at hand-over against the load's hold-up.
- Which input wins in every combination of the two, including one dead or sagging (dodeca's TPS2116 in priority
  mode switches to its second input whenever the priority pin falls below the reference, even when that input is
  dead).
- Reverse-current blocking: the threshold where it engages (TPS2116: 42 mV typical, 70 mV maximum) and what flows
  below it.
- Leakage of a blocked input floating a node that something else reads (TPS2121: up to ±500 µA; dodeca added a
  bleed).
- Fault behaviour: latch-off or retry, and who clears it.

## Microcontroller or module

- Strapping pins at the right level at reset (ESP32-S3: GPIO0, 3, 45, 46), and nothing external that fights them.
- Pins the boot ROM drives, and pins that must float (Teensy 4.1 pins 24/25 on dodeca v1.3).
- Reset and enable timing against the supply's rise; brown-out threshold against the rail's worst dip.
- Programming and debug path reachable on the assembled board, in the enclosure.
- ADC inputs: the divider's source impedance against the ADC's sampling (dodeca's open question on 32 kΩ sources).

## Clocks and crystals

- Load capacitors for the crystal's specified load: the two in series, plus the stray capacitance of pins and
  tracks.
- The crystal's ESR and drive level inside what the oscillator's datasheet asks.
- Start-up time against the moment something needs the clock; an external clock's level against the input's.
- The crystal close to the chip's pins, ground beneath it, no other signal between the two.

## Analog signal chain

- An op-amp or buffer into its real load: output swing at the load current (synth-blocks: a panel LED on a jack
  made the envelope buffer clip), and stability with a cable's capacitance on the output.
- A comparator or open-collector output inside its specified sink current (synth-blocks: an LM393 sinking 5.3 mA,
  where its VOL is given only up to 4 mA).
- Input common-mode range, and what the output does outside it.
- Leakage and bias current into a high-impedance node: the error they make, and what a probe does there.
- The reference: its load, its start-up, and whether it moves with the rail.

## Connectors and anything that leaves the board

- ESD protection and a series resistor on every line that runs to a cable or a user (earlier boards had neither on
  CC, VBUS, LED data and Qwiic, and get hot-plugged on long cables).
- Shorts to the neighbouring pin (USB-C CC sits next to VBUS), mis-mating, a reversed cable.
- The board unpowered while the far end is powered, and the other way round.
- Contact current rating against the real current.
- The footprint against the maker's drawing, not the library's name for it: pin 1, the view the drawing shows (top
  or bottom), the stagger of the rows, and whether the part on the mating board is the mirror. Overlay the two
  boards by script or print them 1:1 (Orbitchron: both board-to-board footprints mirrored the parts, found by a fit
  check after the review had called the board ready).
- A cable's pinning end to end, for the cable that will be used: pin n here against pin n there.
- USB data: D+ and D− as a pair, the ESD part at the connector, the shell's connection chosen on purpose.

## Logic levels and level shifting

- Every signal that crosses from one supply to another: the driver's lowest high level at its load against the
  receiver's VIH, and its highest low level against VIL, at the corners of both supplies.
- High level at the first LED against its VIH at its own supply (WS2812B: 0.7 × VDD, 3.5 V at 5 V).
- With its rail off, a buffer isolates only if the datasheet gives an Ioff figure (LVC1T45). An HCT input clamps
  to its VCC and back-powers the rail. An AHCT1G125 has no input clamp to VCC, but TI gives no Ioff figure for it,
  its 5.5 V input tolerance is stated for a valid VCC only, and its output clamps to VCC: a live line on the output
  feeds the rail.
- Overshoot into the receiver on a short cable, against the receiver's limit above its supply, and the clamp
  current when the receiver is unpowered (the sign's 33 Ω outputs: 3.3–4.1 V at inputs allowed VDD + 0.2 V, and
  43–57 mA into the clamps against the driver's 50 mA).
- Series resistor at the driver, ESD at the connector, for long cables.
- Where a shifter or an RC sits in the path: rise time and delay against the set-up and hold of what it clocks.

## Buses

- I2C pull-ups: value against bus capacitance and rise time, on a rail that is up whenever the bus is used and that
  can't be fed through them (the CH224A is powered from VBUS).
- Address conflicts, including parts with a default address that moves with a strap.

## Current paths and heat

- Track and pour width for the current (KiCad's calculator, which uses IPC-2221's formulas; say which standard and
  temperature rise a width rests on), via count at layer changes, and the ratings of 0 Ω links, jumpers, shunts
  and fuses in the path (siebenuhr's 0805 0 Ω at 2 A).
- Dissipation of each part that gets warm, what sits next to it, and the enclosure's air.

## Layout: returns, coupling and RF

- Each decoupling capacitor and each IC ground pin with a via to the plane at its pad (siebenuhr v3 gave U301's
  ground pin and C303 a via of their own).
- The plane unbroken under a clock or a fast edge: no slot, via row or track across the return path.
- Under a switch node, the copper the regulator's layout guidance asks for: some keep the plane whole there, some
  cut it away (the LM2734's: a continuous ground plane "except under the switching node island"). No switch node
  routed on the plane's layer (the sign's module ran its through 3.95 mm of 0.15 mm track on the ground-plane
  layer).
- The ESD part between the connector and what it protects, the line running through its pad, its ground short.
- No via in or touching an SMD pad unless it is filled (Orbitchron moved three out of socket pads).
- The antenna's keep-out on every layer, on a board stacked beside it, and against the enclosure's metal
  (Orbitchron added one on its LED board for the driver board's module).
- EMC as far as a review reaches: the loop of each switching current, a filter or series resistor on each line
  that leaves on a cable, edges no faster than the signal needs. Emissions are not measured here, and the briefing
  says so under "Not covered".

## Fab and assembly

- Polarity of diodes, LEDs and polarised capacitors on silk and in JLC's placement viewer; rotations (PRODUCTION.md's
  checklist).
- Rotation rules never yet checked in JLC's viewer for a package on this board: the lines marked **never** in the
  `PLACEMENT-<board>.md` that `pcbkit fab` writes (rgb-spotlight: `^SOT-89` and `^SOT-353`; a PT4115 turned 180°
  has 12 V on a 6 V pin).
- The placement count: the CPL's rows against the parts the schematic places. A part with no LCSC field is left
  out without a word (Orbitchron's LED board came out with no LEDs: the field had not reached the 528 of them).
- BOM matched by LCSC code, not by value; parts JLC can't place, and hand-soldered parts listed for the builder.
- Stock of every part, and the substitute for the low ones with the circuit change it needs (bom-cost skill). bomi's
  figure can be a cached one, so read a scarce part on JLC's page. Every placed part is read on the day of the
  sign-off (this kit's `docs/CONVENTIONS.md`, "Checks before an order", step 7).

## Mechanics and set-up

- The board against this kit's `docs/CONVENTIONS.md`, "What a released board carries": its name, version and
  date; the power input, jumpers, connectors, pins, buttons and LEDs labelled; test pads named; mounting holes
  with their keep-outs. What a prototype leaves out is noted in LAYOUT.md.
- Mated boards: the stack height, what sits under and over each connector, the parts that face each other.
- Screw heads, stand-offs and the enclosure's ribs over copper and parts (the enclosure-fit skill has the paper
  check).
- What the user sets by hand: each jumper and switch in every position, every solder jumper bridged, and none.

## Firmware duties

- Every hardware state that depends on firmware, and the state with no firmware, a crash, or a watchdog reset.
- List them in the briefing for whoever writes the firmware (dodeca v1.3 SPEC §11).
