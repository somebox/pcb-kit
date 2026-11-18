# Production

Status (<date>): <what is released, ordered and received; what is next>.

How to order, what it costs, and the quotes and orders so far: the overview below, made from each order's order.toml.

## Order files

`pcbkit release` checks every board, gives a changed one its next version (stamped into its KiCad files) and writes
that version's fab files for JLCPCB and PCBWay into `releases/<board>/<label>/` (committed). An order is
`production/orders/<date>-<what>/order.toml`, pinning each board's version, with an event per step (draft,
submitted, shipped, received, problem); `pcbkit production` makes its ORDER.md and `upload/` folder, and the
overview at the end of this page. Settings: pcbkit.toml.

## Order checklist

- PCB: layers, thickness, stackup, mask, finish, order-number mark, panel settings.
- Assembly: service, side(s); the BOM page must say "Your BOM: JLCPCB Part #".
- Placement viewer: every line of "By part" in the release's `PLACEMENT-*-jlcpcb.md` on at least one part (lines
  marked **never** first), and the polarity and rotation of <parts that have bitten before>.

## Bring-up

What to do when the boards arrive, in order: inspection, unpowered resistance from each rail to GND, first power
from a current-limited supply, then the bench tests from the failure review (`docs/reviews/`), each with the test
point it is measured at and what a pass reads.

## Costs

## Overview

<!-- pcbkit:production:begin -->
Run `pcbkit production` to fill this in.
<!-- pcbkit:production:end -->
