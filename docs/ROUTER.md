# The router

`pcbkit reroute` lays a board's signals again: a negotiated router for small boards with a microcontroller, its passives
and a few connectors. It takes a placed board whose power copper and ground are already drawn, moves small parts where
that helps, routes every signal net together, puts the test pads back where their nets ended up, repairs what is left,
and reports the old routing and the new one on the same measures. It never writes your board: the result is a new
`final.kicad_pcb` under `build/reroute/`.

**Status (2026-11-01): experimental.** It has routed six boards with no failed connection, and they beat their old
routing on most counts (see [Results](#results)). Those boards were judged by DRC and by eye. None has been built yet.

```bash
pcbkit setup-router                       # once: its own Python, with numba, numpy, scipy and pillow
pcbkit reroute scripts/reroute-driver.json
```

The research behind every choice below, phase by phase, is in siebenuhr controller-v2's
`docs/design/routing-plan-mcu-first.md`, with the pipeline it ran at the tag `router-research-2026-11` (a private repo).

## The flow, in order

The order is the result, more than any single step. Each step is there because the one after it works better for it.

| # | Step | Code | What it does |
|---|---|---|---|
| 1 | Job | `pcbkit/reroute/job.py` | Takes the signals off a routed board. Power trunks, kept nets, ground and rule areas stay; each net keeps its width. |
| 2 | Placement | `pcbkit_router/placeopt.py`, `pcbkit/reroute/moves.py` | Moves and turns small parts so nets run direct. Test pads come off for now. |
| 3 | Negotiation | `pcbkit_router/nroute.py` | Every net at once, other nets' copper a rising cost instead of a wall. |
| 4 | Branches | `pcbkit_router/nroute.py` | Refining, settling and legalising from three starting points; the best goes on. |
| 5 | Polish | `pcbkit_router/polish.py` | One net at a time, the others walls: tightening, shortcuts, joins at pads, smoothing, exact clearance. |
| 6 | Finish | `pcbkit_router/tpplace.py`, `pcbkit/reroute/finish.py`, `pcbkit_router/repair.py` | Test pads back on their nets, routes written, DRC, repairs. |
| 7 | Review | `pcbkit_router/lint.py` | The faults below, counted on the old routing and the new one. |

**Placement before routing.** A route can only be as direct as the parts allow. Routing every net alone on the fixed
copper takes about a second a net, so thousands of moves can be scored by what they ask of the nets; crossings count at
4 mm each, so a move that untangles nets pays even if no net needed it. On siebenuhr v2 this took same-layer crossings
from 67 to 43, moving 15 parts and turning 8.

**Test pads last.** A test pad needs nothing but a spot on its net, so it shouldn't shape the routing. They come off
before routing and go back where their nets ended up.

**Negotiation, not a sequence.** Routing nets one by one left 22 failed connections on the siebenuhr v3 controller where
negotiating them together left 4. No net is routed "first", so no net gets the good lane by accident of order.

**Directions fade.** F.Cu vertical and B.Cu horizontal organise lanes while nets compete, and leave staircases once they
don't. Fading to true length over 20 passes won on the bench: true length from the start gave the fewest vias but left
failures; 10 passes also cost failures, and 30 was no better than 20.

**At a stall, move the neighbours.** A net that settled beside a conflict never moves out of its way, because a pass only
routes nets in conflict. Routing the nets near a stalled conflict again is what got every connection of that controller
routed. Every local repair that froze the other nets failed for the same reason: the room a spot lacks is the room its
neighbours need.

**Refine on true length, from three starting points.** A second negotiation on true length took the bench from 4 failed
connections, 385 vias and 5351 mm to 2, 366 and 5266, and settled conflicts the first passes had stalled on. Which pass it
starts from matters by luck of the board, so it runs from three and keeps the best. A second round cost a connection.

**Detours pay their way.** A net sent round an area only pays for length, so nothing moved the others out of its way.
While refining, a net far from direct has its present cost capped at what it would save, so it can afford to cross a net
in its way, and that net, at full cost, gives way. On siebenuhr v2: 56 vias against 58, 809 mm against 835, jogs 11 against
16. Only length counts here: charging vias as well marked 27 of 39 nets as detours, and the negotiation lost its grip.

**Polish comes last and never moves two nets.** Tightening, shortcuts and smoothing see one net with the others as walls,
so they can't undo the negotiation; each keeps its change only if the net's pads stay as joined as they were. The last
clearance check follows the same rule: a repair that would lose a connection is put back, because an open is worse than a
gap a hair short.

**Track under an IC depends on the stackup.** On two layers the far side of an IC is the ground return, and a strong cost
(8) keeps it clear: on siebenuhr v2 it took RESET off the module's edge. With inner layers a plane does that job, and the
same cost only forces detours: on v3 it meant 399 vias and 127 mm of detours where 3 gave 367 and 40. `job.py` picks by
layer count.

## What a good route looks like

The costs came from a person's eye, not from a score. Blind, on a whole board, they preferred the router's routing with
these rules to their own hand routing, because it had "the most sensible routes", without long detours or clusters. The
rules are in `netroute.py`'s docstring and their numbers in `costs.py`; `lint.py` counts each fault:

- No vias in an IC's pin area; a pin's via sits just outside the row, in line with the pin.
- Nothing on the far side under an IC, or under its belly: that belongs to the ground pour. A track along a module's edge
  reads as a fault.
- Joins at pads and vias, not in the middle of a track.
- A narrow run only right at a pad, for at least 0.5 mm, and it changes width once. A track rounds a pad's corner at full
  width.
- The last run into a small pad comes in straight and centred.
- No 90-degree corners or zig-zags.
- A net that goes round an area when something could move to let it through is a detour.
- Small parts by an IC don't block its top-layer tracks, and test pads go where routing is easy.

Whole-board order counts for more than close-ups: small changes to a rule are hard to see, and the question to ask of a
result is whether you would redo it.

## Tried and dropped

What these had in common: they repair one spot with every other net frozen. The numbers are in the research notes.

- Untangling cross-backs by negotiating a detour with the nets it crossed (kept 0 of 5).
- Polish, a local re-route round each failed connection (never beat the routes there).
- Push shortcuts, a shortcut through nets that are then routed round it (1 of 42 kept).
- A global router's corridors as a guide (no gain, and a worse fit for dense regions).
- Partial re-routes keeping a conflicted net's clear pieces (no better, not faster).
- Planning ground vias after routing instead of before (3 % gain, but 14 to 17 pads lost their via spot against 6).
- Routing a differential pair as two nets: it stalled the negotiation and failed. A pair stays as it was drawn.
- Rewinding to the best pass: the last pass was always the best.
- Overlap area as the placement score: a tie-break splitting two nets scored 500 mm though nothing had to give way.

## Settings for a board

Settings go in a JSON file in the project; only `"board"` is needed, relative to that file. Clearances, via size and each
net's width come from the board.

```json
{"board": "../boards/node/node.kicad_pcb",
 "keep": ["GND", "USB_DN", "USB_DP"],
 "keep_w": 0.5,
 "nets": {"ADDR": {"clr": 0.15}},
 "place": {"movable": ["R*", "C*"], "fixed": ["C7"], "limits": {"C4": 1.0}},
 "free": ["TP1", "TP2"]}
```

- `keep`: nets that stay as drawn; the ground net by default. Always a differential pair. Tracks of `keep_w` mm or wider
  stay, locked tracks and vias stay, and so does any net with a track on an inner layer, since the router lays F.Cu and
  B.Cu only. A kept track is joined into its net like a pad, so a power trunk stays in the path. Keep a net too when the
  router can't reach it: a pocket whose only way in is a via tighter than the router's margins.
- `lift`: nets whose pours are left out of the router's board; the ground net by default. A big pour of another net would
  flood the room the signals had and wall them in, and the flow warns of any pour over 20 % of the board: lift it, and
  keep its net.
- `nets`: per-net overrides. `clr` lower at a fine-pitch part whose pins can't be reached at the board's clearance.
- `place`: omit it and no part moves. A decoupling capacitor belongs in `fixed` or `limits`: the search knows nets, not
  loop areas.
- `free`: test pads that may go wherever their net ends up, on the side they are on. A locked or through-hole one stays.
- `ground`: the ground net, `GND` by default.
- `refs`: reference prefixes, for a board that doesn't use the usual ones: `ic` (`U`), `mount` (`H`), `holes` (`Hole`),
  `test` (`TP`).
- `route`: options for `nroute.py`, kept with the board so a run can be repeated. `pcbkit_router/costs.py` has the keys.
- `route.workers` (10 by default) also changes the routes, not just the speed. Nets on one worker are routed in turn and
  see each other; nets on different workers don't. It acts like a seed, with no best value: going from 3 or 4 workers to 10
  took the spotlight from 23 vias to 29 and the sign from no failed net to one, and took v2 from 63 vias to 57. It's a set
  number, not the machine's core count, so a run repeats on another machine. A board with a failed net is worth trying at
  another count.

A run skips a step that finished on the same settings, board files and router code, so change the settings and run it
again. `--from=route` re-runs from a step and takes the steps before it as they stand, `--until=job` stops after one, and
any `--key=value` goes to `nroute.py`, over `route`.

## Limits

- The board must already have its power copper and ground. `job.py` keeps what is there; nothing lays it for a board that
  has never been routed.
- Anything drawn on a solder-mask layer keeps the tracks under it and is taken as its bounding box, whatever its shape.
- Two routing layers, F.Cu and B.Cu. Inner planes are joined through vias; inner signal layers aren't used, and In3 and
  beyond aren't read at all.
- Both outer layers cost the same. A two-layer board that wants its back as a solid ground pour gets more track there than
  a person would lay; `lint.py` counts it as `back_mm`.
- One via size per job, so a board with a bigger via for its power class gets warnings there.
- Ground pours aren't seen while routing, so a track can close off a ground pad. `repair.py` ties those in afterwards, and
  says when it finds no room.
- New vias can cut an inner plane, and nothing charges them for it. `repair.py` rejoins a stranded piece with one via, which
  makes DRC happy but may not carry the current: read its output on a power plane.
- Differential pairs aren't routed as pairs, and nothing is length-matched.
- The placement search knows nets, not current loops, thermal paths or assembly.
- A moved part takes the kept ground tracks on its pads with it. If one of those joined another part's pad, the pour or
  `repair.py` has to reach that pad, and DRC says if neither did. A part with a locked ground track doesn't move.
- A soft pour's protected path stops at 2 mm wide, so a wider path can lose more than a fifth; `pourcheck.py` flags it.
- A free test pad with no spot stays where it was, unconnected, and DRC reports it. If the routing went over that place the
  run stops: the router never saw the pad there, so it has to route again with the pad out of `free`.

## Results

The six boards of the corpus, each board's old routing against the router's, with no part moved except on siebenuhr v2,
at the worker counts in `bench/corpus.json` (3 or 4). The two-layer boards route in minutes, the four-layer ones in about
an hour on 10 cores.

| Board | Layers, nets | First routed by | Vias | Track, mm | 90° corners | Detours, mm | Cross-backs |
|---|---|---|---|---|---|---|---|
| Spotlight driver | 2, 23 | maze router | 33 → 23 | 436 → 417 | 10 → 3 | 22 → 12 | 2 → 0 |
| ESP32-S3 HA node | 2, 59 | maze router | 59 → 36 | 1054 → 1040 | 11 → 9 | 44 → 50 | 0 → 2 |
| Siebenuhr v2 | 2, 37 | hand | 74 → 63 | 993 → 844 | 0 → 0 | 48 → 5 | 1 → 0 |
| Orbitchron driver | 4, 100 | maze router | 77 → 70 | 1327 → 1194 | 146 → 5 | 79 → 41 | 6 → 1 |
| Sign controller | 4, 97 | maze router | 269 → 154 | 2761 → 2526 | 28 → 17 | 96 → 62 | 22 → 2 |
| Dodeca v1.3 | 4, 105 | Freerouting | 124 → 134 | 2657 → 2267 | 53 → 14 | 131 → 19 | 8 → 1 |

In all: vias 636 to 480, track 9228 to 8287 mm, 90-degree corners 248 to 48, narrow runs 167 to 16, vias in pin areas 65 to
24, detours 419 to 188 mm, cross-backs 39 to 6. DRC is clean on the spotlight, Orbitchron and the sign, the HA node keeps its
old findings, and v2 is better than it was.

What got worse: track hugging pads (184 to 242 mm), T joins on the HA node (1 to 7), back-layer track on the HA node and the
sign, and 10 more vias on dodeca, where one ground pad closed off by a 3V3 track needs a hand fix and 36 via-size warnings
remain.

## Changing the router

A single build moves by a failed connection or two with any small change, so a change is judged on several boards.

```bash
pcbkit reroute bench try-1 --boards=spotlight-driver,ha-node --fade=15    # nroute alone, side by side
pcbkit reroute bench --table try-1
pcbkit reroute bench --lint try-1
pcbkit reroute corpus                                                     # the whole flow, against bench/baseline.json
pcbkit reroute corpus --table --save                                      # these results become the baseline
```

`pcbkit reroute corpus` runs again after any change to the router's code, and an interrupted run picks up where it stopped.
`bench/corpus.json` pins each board to a commit of its project, so the boards stay the same while the projects move on; it
reads them from your local clones. It pins each board's `workers` too, because a run repeats exactly only for a given count
(see Settings). `pcbkit reroute bench` doesn't read that setting, so give every run the same `--workers`. Most of the corpus
is in private repos, so no board geometry is kept here, and `bench/baseline.json` holds only counts.

The split to keep: `pcbkit_router` never imports `pcbnew` or `pcbkit`, and everything KiCad knows reaches it through
`geom.py`'s JSON. The KiCad side stays in `pcbkit/reroute/`, under the rules for `pcbkit/`.

## To do

**Speed.** Nothing here has been profiled yet, so start by profiling one board (the spotlight driver takes about 8 minutes)
to see the split between A*, distance transforms and the rest. Then, in this order:

1. Limit each worker to one thread (`OMP_NUM_THREADS`, `OPENBLAS_NUM_THREADS`, `NUMBA_NUM_THREADS`): load reached 297 on 10
   cores with six boards running, where about 60 workers were expected.
2. Update distance transforms only in the window round a changed net, and compute them less often per pad in `repair.py`
   and `tpplace.py`.
3. Search less: tighter windows, a better A* heuristic (`hw` above 1 makes B.Cu runs wobble).
4. Run the three branches only when the first result isn't clean.
5. Skip nets whose neighbourhood hasn't changed in polish and repeat passes.

Items 1 and 2 shouldn't change routes: prove them byte-identical on the corpus. Items 3 to 5 can, so judge them on the
bench. A GPU (Metal) could only help the array work, not A*, so consider it only if profiling shows distance transforms
taking a large share.
