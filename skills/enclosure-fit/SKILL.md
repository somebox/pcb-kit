---
name: enclosure-fit
description: Turn an off-the-shelf project box into a board outline, mounting holes and keep-outs, and check the fit with paper before ordering. Use when a board has to fit an enclosure, when the user sends a photo or drawing of a box, or when a placement needs checking against the box.
---

# Fitting a board to an enclosure

Worked example: `~/Documents/Projects/esp32-s3-ha-node` (`boards/node/enclosure.json`, LAYOUT.md, `docs/fit-check*.pdf`).

## Loop

1. **Measure.** Best: the manufacturer's drawing, confirmed with calipers. Next: a photo straight down with a ruler in
   the frame; scale from the millimetre ticks and expect about 0.5 mm accuracy on flat features and over 1 mm
   error on tall ones (bosses, standoffs) from parallax.
2. **Describe it** in `boards/<b>/enclosure.json`: board size and corner radius, mounting holes (hole and boss
   diameter), keep-outs (lid bosses), cut-outs around bosses (clearance, fillet), and reference drawings (lid
   inside, plateau, connector zones). Write where each number came from.
3. **Apply:** `pcbkit outline boards/<b>/<b>.kicad_pcb boards/<b>/enclosure.json --fit-check docs/fit-check.pdf`.
   Rerunnable; it replaces only what it made.
4. **Print and cut.** The user prints at 100 %, measures the 50 mm scale bar and a dimension line, cuts along the
   thick outline and drives the real screws through the holes. Their findings go back into the JSON.
5. After placement, `--parts` puts the part outlines on the print for a last check of connectors against the walls.

## Lessons

- The first "doesn't fit" was the printer scaling the page. Hence the scale bar and dimension lines on every print.
- Board-edge notches around lid bosses: straight in, an arc at boss radius plus clearance, straight out, and a fillet
  of at least the router radius (1 mm for a 2 mm cutter) on every inside corner.
- Keep the cut at least a few millimetres from a mounting hole, or the self-tapping screw splits the corner.
- Note the free height (standoff to lid) early; it decides terminal blocks, electrolytics and bottom-side parts.
