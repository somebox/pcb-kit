# Test points and rework provisions

The boards come back assembled from JLC, so isolating a block or trying another value means rework. Plan for it
while it costs a pad:

- **Measure.** A test pad on every rail, and on each signal a failure is diagnosed by: reset, enable, strap,
  power-good, fault, sense nodes, a switch node for the scope. A GND pad close to each, for a probe's ground
  spring. Pads at both ends of a high-current path measure its drop (siebenuhr v2.2's TP7 and TP14 across R17 and
  the shunt). The projects use `TestPoint:TestPoint_Pad_D1.0mm` (siebenuhr, bottom side) and `TestPoint_Pad_D1.5mm`
  (esp32-s3-ha-node); a `TestPoint_Loop_*` takes a scope's ground clip. Put them on the side that can be reached
  with the board in its enclosure, with the net name on silk.
- **Isolate.** A 0 Ω link (0603 or larger, so it can be lifted with an iron) or a bridged solder jumper
  (`Jumper:SolderJumper-2_P1.3mm_Bridged_*`) in series with each rail into a block, so a short can be found by
  elimination and a block's current measured. Keep its trace on an outer layer where a knife can reach.
- **Override.** Strap, enable and mode pins through a resistor to their level, not straight to a rail, so a wire
  can override them. siebenuhr's CH224K had CFG1 on a 0 Ω link to the connector's VBUS.
- **Options.** DNP footprints for the alternative a finding points to: dodeca v1.3 has R209 (DNP) for leakage on
  QON, JP601 (DNP) to let the INA226 alert cut the LED rail, and C402 to remove if the BATFET trips on wake.
- **Reach.** A spare GPIO on a pad near the circuit most likely to need firmware help. Any net under a QFN or BGA
  that might need a wire, brought out to a pad or via. The debug UART, boot and reset pins on pads. The I2C bus on
  a header, so the chips can be read without the product firmware: v1.2's charger driver wrote 0 mA into the
  charge-current register, and a register dump told that apart from a hardware fault.
- **Pins with a role.** A pad on a pin that must float or that the ROM drives at boot is not neutral. dodeca v1.3
  removed v1.2's TP_SCL2/TP_SDA2 from Teensy pins 24/25 for that reason.

A pad costs nothing and a 0 Ω is a Basic part. Room and routing are the limits on a dense board; the briefing says
which provisions were left out and why.
