---
name: placement-study
description: "Compare and search component placements by their ratsnest before routing: score crossings, length, via-field zones and free pin or gate choices in milliseconds per candidate, draw the ratsnest for review, and route only the winner. Use when a full route is slow (minutes to an hour), when deciding where a block of parts should sit, which side or angle a connector takes, or which register output or GPIO drives which net, and when reviewing a placement before routing."
---

# Placement study

Routing to find out whether a placement works is slow: a full A* route of a dense board can take most of an hour,
and a small change moves which net fails. A ratsnest score takes milliseconds, so compare placements by it and route
only the winner. On Orbitchron's driver board the first scored placement cut the unconnected items of the full
route from 7 to 3 (2026-10-14).

## Steps

1. Generate a placed, unrouted board with the project's own generator (routed boards work too: tracks are ignored).
2. Look at it: `pcbkit place-score BOARD --planes GND,VLED --svg out.svg --png out.png`, then read the PNG. Edges are
   coloured by side (front red, back blue, both purple), crossings are circled, courtyards of back parts dashed.
3. Write the study in the project, e.g. `scripts/place_study.py` (below): which footprints move together, where
   they may go, the zones to keep clear, the free choices.
4. `pcbkit place-score BOARD --study scripts/place_study.py` scores the current placement;
   `--search` runs coordinate descent over the groups and prints the best and the next few.
5. Look at the best one's picture (`--svg`), and at the runners-up: a near tie is a judgement call (heat, assembly,
   the enclosure) the score cannot make.
6. `--write design/placement.json`; the project's generator reads it, so the schematic (for pin and gate choices)
   and the board follow. Regenerate both from the project's scripts; the kit never writes the board.
7. Route the winner. Record the score, the route result (unconnected items, DRC) and the date in LAYOUT.md.

## The study file

```python
from pcbkit.placement import Study, Group, Zone, Perm, grid, assign
import layout                                            # the project's own placement code, if it has one

STUDY = Study(
    planes={"GND", "VLED"},                              # plane nets: their pads only need a via
    groups=[
        # rigid: (dx, dy, rot) from where the board has it, rotating about the first footprint
        Group("buck", ["U4", "L2", "C25"], grid(dx=(-6, 6, 1), dy=(-6, 6, 1), rot=(0, 90, 180, 270), r=8)),
        # the project's code places it: place(parameter) -> {ref: (x, y, orientation)}; home = current parameter
        Group("qwiic", ["J2", "R12", "R13"], range(40, 145, 5), place=layout.qwiic, home=90),
    ],
    zones=[Zone(via_field_polygon, weight=2.0, side="B", name="via field"),   # mm^2 x weight
           Zone(keep_clear_polygon, forbid=True)],                            # any overlap is invalid
    perms=[Perm("gates", gates, nets=["ROW0_G", "ROW1_G"])],                  # free choices, solved per candidate
    weights=dict(cross=10.0, length=0.2))


def gates(ctx):
    """Which register output drives which FET gate: assign() finds the best one-to-one choice."""
    fet = {r: next(p for p in ctx.pads[f"ROW{r}_G"] if p[3].startswith("Q")) for r in range(2)}
    outs = {q: ctx.pad("U5", pin) for q, pin in (("QA", "15"), ("QB", "1"))}
    return assign(range(2), outs, lambda r, q: [ctx.edge(f"ROW{r}_G", outs[q], fet[r])], ctx)
```

- Pads are `(x, y, side, ref, number)`; nets can be named in full (`/Rows/ROW2_G`) or by their last part.
- `assign()` is exact (branch and bound) up to 9 slots and greedy with swaps above that.
- Several perms are solved in the study's order, each seeing the edges the ones before it chose (a crossing
  between two choices is priced once, in the later one): put the choice with the fewest options first.
- Rule areas on the board that keep footprints out are forbidden zones automatically, and the board outline is the
  copper limit (`edge_clr`, 0.3 mm; a connector body may overhang, its pads may not).

## Reading the score

- **Crossings** count only where two nets' ratsnest edges could meet on one layer: front-only against back-only
  never does; through-hole pads and side-changing edges meet everything. `--any-side` counts them all (a 2-layer
  board routed mostly on one side).
- The total is relative: compare candidates of one study, not boards. Crossings dominate at the default weights
  (one crossing = 50 mm of ratsnest).
- A fan-out from a chip to a header crosses itself in the ratsnest however it is placed; that cost is constant and
  does not steer the search. Judge it in the picture, not by the number.

## Gotchas

- Plane nets left in swamp the score with long edges that the plane makes free.
- Courtyard lines touching is fine; overlapping area is not. Through-hole pads block both sides.
- An invalid start (the board or `--start`) is repaired before the descent: each group that breaks a rule takes
  its best valid candidate. If one has none, the search stops and says which rule.
- The search is coordinate descent: it can stop in a local minimum. Start it from a few hand-made placements
  (`--start other.json`) when groups interact strongly.
- A free choice changes the netlist: regenerate the schematic from the written JSON before routing, then run the
  project's netlist assertions.
