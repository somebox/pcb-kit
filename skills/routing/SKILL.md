---
name: routing
description: "Re-route a KiCad board's signals with the negotiated router, `pcbkit reroute`: small parts moved where that helps, every signal net routed together, test pads placed after the routing, repairs, and the old routing and the new one compared. Use when a board with its power copper and ground drawn needs its signals routed or routed again, when an autorouted or hand-routed board has detours, tangles or too many vias, or when comparing two routings of a board. Not for laying power copper, and not for routing a net at a time from a script (pcbkit.route)."
---

# Routing a board

`pcbkit reroute` takes a placed board and lays its signals again, whoever routed it first, and leaves the result beside the
project's board for you to compare: it never writes the board. It is experimental: six boards routed and judged by DRC and
by eye, none built yet, so say so when you present a result. [docs/ROUTER.md](../../docs/ROUTER.md) has the method, every
setting and the limits.

## Steps

1. **Check that it fits.** The router lays F.Cu and B.Cu only, and needs the power copper and ground already on the board.
   A board with signals on inner layers keeps those nets as they are. A board with no copper yet needs its power laid
   first, by the project's own script or by hand.
2. **Set up once:** `pcbkit setup-router` (needs `uv`) installs the router's own Python under `pcbkit info`'s tools dir.
3. **Write the settings** in the project, e.g. `scripts/reroute-<board>.json`. Start small: name the board, and add each
   differential pair to `keep`.
   ```json
   {"board": "../boards/driver/driver.kicad_pcb", "keep": ["GND", "USB_DP", "USB_DN"]}
   ```
4. **Stop after the job first:** `pcbkit reroute scripts/reroute-driver.json --until=job`. Read what it prints: what was
   kept, how many nets it will route, and any `!! pour` warning. A pour that floods the signals' room goes in `lift`, with
   its net in `keep`. Then run it without `--until`: a two-layer board takes minutes, a four-layer one about an hour.
5. **Read the result:** `build/reroute/boards/<name>/summary.json` has failed connections, DRC of both routings, and the
   measures of each under `lint`. Compare `lint.old` with `lint.new`, not the new numbers alone.
6. **Look at it.** Numbers don't show a bad route: `pcbkit netview` on the regions round the main IC, or `pcbkit reroute page`
   for old and new side by side. Ask whether you would redo it.
7. **Add placement when the routing is close:** `"place": {"movable": ["R*", "C*"], "fixed": [...], "limits": {...}}`, with
   every decoupling capacitor in `fixed` or `limits` (the search knows nets, not loop areas), and test pads that may move in
   `free`.
8. **Adopting a result is the user's call**, on a branch, with KiCad closed: `final.kicad_pcb` replaces the board, then
   `pcbkit drc --refill --save`, `pcbkit check`, and a note in LAYOUT.md with the date, the settings and the numbers. The
   settings file stays in the project, so the routing can be run again.

## Reading the output

- **Failed connections** in `nroute.log`'s last lines, by net. The worker count acts like a seed, so try another
  (`"route": {"workers": 4}` in the settings, and keep it there: the same count repeats the same routes). A net that fails on
  every run usually has a pocket no via fits in: keep it as drawn.
- **`repair.py`'s lines** in the run's output say what it joined after routing. `no spot` means a pad that needs a hand fix.
  `a piece of N mm2 joined by a via` on a power plane means one via now carries that piece: check the current.
- **DRC counts** come without silkscreen kinds. `via_diameter` and `drill_out_of_range` mean a class wants a bigger via than the job's one
  size.
- **The measures:** `vias`, `mm`, `back_mm` (track on B.Cu), `corners90`, `jogs`, `tees` (joins away from pads), `necks` (narrow
  runs), `via_in_ic` (vias in pin areas), `hug_mm` (track beside other nets' pads), `under_mm` (under ICs), `detour_mm`,
  `crossbacks`.

## Gotchas

- A differential pair routed as two nets stalls the negotiation: always `keep` it.
- A kept pour above 20 % of the board walls the signals in: the flow warns, and `lift` and `keep` are the fix.
- A two-layer board with a solid ground pour on the back gets more back-layer track than a person would lay: watch `back_mm`,
  and look at what happened to the pour.
- A change to the router itself is judged on the corpus, not on one board: see "Changing the router" in docs/ROUTER.md.
