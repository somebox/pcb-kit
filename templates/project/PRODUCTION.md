# Production

How to order, what it costs, and a dated log of quotes and orders (newest first).

## Order files

`scripts/make_fab.sh <board>` writes `build/fab/<board>/` (git-ignored; regenerate before each order).

## Order checklist

- PCB: layers, thickness, stackup, panel settings.
- Assembly: service, side(s); the BOM page must say "Your BOM: JLCPCB Part #".
- Placement preview: polarity and rotation of <parts that have bitten before>.

## Bring-up

What to do when the boards arrive, in order: inspection, unpowered resistance from each rail to GND, first power
from a current-limited supply, then the bench tests from the failure review (`docs/reviews/`), each with the test
point it is measured at and what a pass reads.

## Costs
