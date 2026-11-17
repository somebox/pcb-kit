# Generating a schematic sheet that reads

For "Generate a sheet that reads" in [SKILL.md](SKILL.md). A sheet script places and wires its parts with
`pcbkit.schematic` (`sch` below). The steps, in order: place the parts by pin or as blocks, put every part in a
block, give the sheet its title block, set the page out and build twice, lint, then render the sheet and look.

## Place parts by pin

In the sheet script, give the ICs and connectors their x, y and place the parts round them by pin, so the pins that
share a net line up. Pin positions come from the symbol libraries, before anything is placed.

- `sch.put(l1, "1", sch.step(sch.pin(u2, "8"), "right", 17.78), toward="right")` puts the inductor in line with the
  switch pin.
- `toward="down"` hangs a catch diode or a divider resistor from the row.
- `sch.chain(point, [f1, q5], toward="right")` sets parts in series.

## Describe a block and let `sch.block` place it

Better, for anything with a main path. First read the circuit and say for each part what it does: in the path,
hanging from a node of it, on some other pin, across another part. Then:

```python
b = sch.block("3.3 V buck", path=[(u5, "4", "3"), l2], shunt={"+5V": [c8], "+3V3": [c9, c19, [r8, r9]]},
              taps=[(q5, "4", [r21])], across=[(r8, [c25])])
```

- Input on the left, output on the right.
- Parts to GND hang down and parts to a supply up.
- A list is parts in series (a divider).
- `b.box` is where it ended up, for placing the next block.

## Put every part of the sheet in a block

- A row can start at a connector: `(j2, None, "2")`.
- `sch.flag("#FLG0101", "VIN")` is a `PWR_FLAG` that hangs from a rail like any part.
- `sch.part(ref, lib, val, fp, lcsc, pins)` makes a part without x, y.
- `b.add(part)` takes in one you placed by hand.
- `sense=[(u5, "5")]` has a feedback or sense pin wired to its node elsewhere in the block, round whatever lies
  between, instead of labelled.

## Title block

Then give the sheet its title block: `sch.title_block(sheet, about="Power: input, bucks, USB-C")`. It takes the
project's title, rev, date and company from the root schematic named in `configure(..., root="board.kicad_sch")`,
and `about` says what this sheet holds. Give `author=` where the root names none.

## Set the page out and build twice

Set the page out in rows and build twice, the second time by the measured sizes. `sch.frames` comes last.

```python
for again in (False, True):
    sch.arrange([[inp, buck5], [usb, buck3]], page="A3")
    sch.reset(sheet)
    sch.build(sheet, base, comps, hier, wire=25.4, blocks=B)
    sch.fields(sheet)
    sch.measure(sheet, B)
sch.frames(sheet, B)
```

- `build(blocks=...)` draws each node row as one wire with one name or one power symbol. It stops if a rail would
  cross another net's pin or a part.
- `fields` sets reference and value level beside each part.
- `frames` draws the frames `arrange` lined up and names any part left in no block.
- Call `build(..., wire=25.4)`, as above: small local nets (2 to 6 pins) are drawn as wires where a straight, L or
  Z run is clear, named once on the wire. The rest keep a label per pin, and the netlist is the same either way.

## Lint, render and look

- Then `pcbkit sch-lint`: a `label-island` is a part to move next to what it works with, and overlaps between two
  rows of a block mean a longer `pitch`, another side, or a second block.
- Render the sheet (`kicad-cli sch export pdf`, then a PNG) and look at it before calling it done.
