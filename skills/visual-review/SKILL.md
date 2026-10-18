---
name: visual-review
description: "Make and read pictures of a KiCad board or design question: net-coloured region views, 3-D renders, placement mock-ups, ratsnest pictures, one-page design overviews, 1:1 prints, copper checks and simulation plots. Use when reviewing a power stage, a placement, an enclosure fit or a check result, or when the user would understand a finding faster from an image than from a table."
---

# Visual review

A picture is usually the quickest way to agree on what a board looks like and what is wrong with it. Make the
picture, look at it yourself (read the PNG), say what you see, then send it to the user if it helps them decide.

## Which picture for which question

| Question | Picture | How |
| --- | --- | --- |
| Is this power stage laid out the way the datasheet wants? Which net is which copper? | one layer of a region, coloured by net, pads labelled, courtyards dashed | `pcbkit netview BOARD out.svg X0 Y0 X1 Y1 --layer F.Cu --png out.png`; a `--colours nets.json` file keeps colours stable across reviews |
| How full is the board, where do connectors land? | 3-D render of the top | `kicad-cli pcb render --side top --quality high -o out.png BOARD`; for a placement study, place stock footprints first (see the esp32-s3-ha-node `scripts/mockup.py`) |
| Which placement tangles less? Where do nets cross? | the ratsnest, crossings circled | `pcbkit place-score BOARD [--study S.py] --svg out.svg --png out.png`; see the placement-study skill |
| Does it fit the box? | 1:1 print with the outline, holes, dimension lines and a 50 mm scale bar | `pcbkit outline BOARD enclosure.json --fit-check fit.pdf [--parts]`; the user prints, measures the scale bar, cuts and screws it into the box; a photo with a ruler and calipers in it records the result |
| How does the whole design fit together, for someone new to it? | one sheet: renders of the front and back beside a block diagram, each block in the same colour in all three, plus legends and notes | `kicad-cli pcb render` for the views (`--rotate` for a three-quarter view), composed as an SVG and rasterised with `rsvg-convert`; see synth-blocks' `boards/analog-voice/scripts/overview.py` |
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

## Gotchas

- `kicad-cli pcb export pdf` defaults to thin hairlines; use `--black-and-white` and thick copies of the outline for
  anything that gets printed and cut.
- Printers scale pages. Every 1:1 print carries a scale bar and dimension lines; ask the user to measure before cutting.
- Renders of mock-ups show stock 3-D models; a wrong model orientation is a footprint question, not a placement one.
- `kicad-cli pcb render` is orthographic, so board coordinates map into a rotated render by an affine map. To mark
  things on a render, find the map from the corners of a render of the bare board (3-D models stripped), as
  synth-blocks' overview does, instead of placing labels by eye.
