---
name: visual-review
description: "Make and read pictures of a KiCad board or design question: net-coloured region views, layout options side by side, 3-D renders, placement mock-ups, ratsnest pictures, one-page design overviews, 1:1 prints, copper checks and simulation plots. Use when reviewing a power stage, a placement, an enclosure fit or a check result, when choosing between layout options (where vias go under a part), or when the user would understand a finding faster from an image than from a table."
---

# Visual review

A picture is usually the quickest way to agree on what a board looks like and what is wrong with it. Make the
picture, look at it yourself (read the PNG), say what you see, then send it to the user if it helps them decide.

## Which picture for which question

| Question | Picture | How |
| --- | --- | --- |
| Is this power stage laid out the way the datasheet wants? Which net is which copper? | one layer of a region, coloured by net, pads labelled, courtyards dashed | `pcbkit netview BOARD out.svg X0 Y0 X1 Y1 --layer F.Cu --png out.png`; a `--colours nets.json` file keeps colours stable across reviews; `--detail` adds mask openings, paste and pad numbers and rings any via hole that lands in paste |
| Which of a few layout options? (a via pattern under a part, where a pour or a part goes) | the same window of each option on one sheet, after the datasheet's figure: each net one colour in every panel, the numbers that decide it under each panel | `pcbkit netview BOARD out.svg X0 Y0 X1 Y1 --layer F.Cu --detail --title "Now" --compare opt1.kicad_pcb="Option 1" --ref fig.png="Datasheet" --metrics VOUT,GND --png out.png`; see "Comparing layout options" |
| How full is the board, where do connectors land? | 3-D render of the top | `kicad-cli pcb render --side top --quality high -o out.png BOARD`; for a placement study, place stock footprints first (see the esp32-s3-ha-node `scripts/mockup.py`) |
| Which placement tangles less? Where do nets cross? | the ratsnest, crossings circled | `pcbkit place-score BOARD [--study S.py] --svg out.svg --png out.png`; see the placement-study skill |
| Does it fit the box? | 1:1 print with the outline, holes, dimension lines and a 50 mm scale bar | `pcbkit outline BOARD enclosure.json --fit-check fit.pdf [--parts]`; the user prints, measures the scale bar, cuts and screws it into the box; a photo with a ruler and calipers in it records the result |
| How does the whole design fit together, for someone new to it? | one sheet: renders of the front and back beside a block diagram, each block in the same colour in all three, plus legends and notes | `kicad-cli pcb render` for the views (`--rotate` for a three-quarter view), composed as an SVG and rasterised with `rsvg-convert`; see synth-blocks' `eurorack/voice/scripts/overview.py` |
| What does this DRC or check item actually refer to? | the copper around it, rasterised, with the flagged items marked | a netview of the area, or a small script that plots the layer and the item; crop tight |
| Does the rail hold up when the load steps? | voltage and inductor current against time, one panel per case | ngspice deck through `pcbkit spice`, `wrdata` to text, matplotlib (`uv run --with numpy --with matplotlib`) |
| What will the assembler see? | the hand-assembly map and the copper PNGs | `pcbkit previews BOARD docs/previews/<board>` |

## How to look

- Read the image after making it. Check scale and orientation first: board coordinates are top view, millimetres.
- Crop before concluding. `pdftoppm -r 250 -x .. -y .. -W .. -H ..` on a PDF, or a tighter netview window, beats
  squinting at a full board.
- Say what the picture shows in sentences: which part sits where, which gap is tight, which net crosses which.
  Numbers (gaps, edge use, coverage) come from the board file, not from pixels.
- Keep generated pictures in the project under `docs/previews/<board>/` with the command that made them, so they
  can be regenerated; send the ones the user needs to see with the file-sending tool of the harness.

## Comparing layout options

When there are a few ways to lay something out, build each one and show them side by side: the user chooses faster
from one sheet than from a table, and the sheet shows what a table hides.

- Each option is a real copy of the board with the change made, saved inside the project folder (a copy elsewhere
  can't resolve `${KIPRJMOD}` in fp-lib-table, and DRC adds library warnings), and run through `pcbkit drc`. Its
  DRC result goes in the footnote with `--note`.
- First the reference: the datasheet's layout figure or the EVM's, cropped to the same part and turned to the
  board's orientation (`--ref-rotate`). Then the board as it is, then each option. Every board panel has the same
  window and every net its own colour in all of them; `--colour NET=#rrggbb` when the defaults don't tell the nets
  apart.
- Each title says what the panel is and its count ("Option 2, corners + TI's side vias: 9 vias"; `\n` starts a
  new line). The numbers that decide it go under the panel, never over the copper: holes under paste, and per
  `--metrics` net its vias, their barrel copper and their resistance through the board. The footnote says how the
  numbers were made.
- Look at every panel before sending the sheet. A stub, a via off its pad or a number the picture contradicts is a
  finding about that option.

Worked example ([via-options-tps55288.webp](../../docs/images/via-options-tps55288.webp)), siebenuhr controller v3,
2026-11-09. U5 is a TPS55288 buck-boost in TI's HotRod package, with three long strip pads under it. Five of the
board's eight vias there were open holes inside the strips, under paste: a 0.2 mm barrel holds about 0.034 mm³,
more than one strip's whole paste deposit (0.027 mm³), and JLC can't plug a via in or within 0.35 mm of a pad. TI's
figure and EVM put the vias beside the strips. A search of the board for free spots (the via's copper 0.16 mm from
other nets on every layer and 0.02 mm from every mask opening, holes 0.25 mm apart, on its own net's copper on both
layers it joins) found three of U5's corners and, with 0.007 mm to spare, TI's side spots. Option 2 uses both: no
hole under paste, the VOUT via copper within 3 %, PGND's doubled. The sheet also shows what the study's first
picture left out, since it drew no tracks: Option 1's copy kept the tracks that led to two of the vias it removed.

```bash
pcbkit netview before/controller.kicad_pcb u5-options.svg 147.55 126.7 152.05 131.2 --layer F.Cu --detail \
  --colour LED_VOUT=#e8892b --colour GND=#6b8fb8 --colour CONV_SW2=#c94a4a \
  --ref "ti-fig10-1.png=TI datasheet Fig. 10-1, turned to match" --ref-rotate 90 \
  --title "Before (e0afc51): 8 vias, 5 in the pads" \
  --compare "v1/controller.kicad_pcb=Option 1, corners: 5 vias, none in pads" \
  --compare "v2/controller.kicad_pcb=Option 2, corners + TI's side vias: 9 vias" \
  --metrics LED_VOUT,GND --note "Both options: DRC 0 errors, 0 unconnected." --png u5-options.png
```

## Gotchas

- `kicad-cli pcb export pdf` defaults to thin hairlines; use `--black-and-white` and thick copies of the outline for
  anything that gets printed and cut.
- Printers scale pages. Every 1:1 print carries a scale bar and dimension lines; ask the user to measure before cutting.
- Renders of mock-ups show stock 3-D models; a wrong model orientation is a footprint question, not a placement one.
- `kicad-cli pcb render` is orthographic, so board coordinates map into a rotated render by an affine map. To mark
  things on a render, find the map from the corners of a render of the bare board (3-D models stripped), as
  synth-blocks' overview does, instead of placing labels by eye.
