# Where each piece came from

Inventory taken 2026-10-10. "same" means the copies in the sign (rp2354-messages-sign), siebenuhr (controller-v2)
and spotlight (rgb-spotlight) repos were byte-identical; the kit version is the merge of the variants.

| Kit | Source | Notes |
| --- | --- | --- |
| `pcbkit/konnect.py` | `scripts/kmcp.py` (same in all three) | binary overridable with `KONNECT_BIN` |
| `pcbkit/schematic.py` | `scripts/builder.py` (three variants) | merged: sign's base, siebenuhr's `P2`, `glob` labels and `mark_dnp`, spotlight's `unit` and reference dedupe. `P` and `POWER` set with `configure()` instead of editing the module |
| `pcbkit/parts.py` | the `RES`/`CAP` tables inside `builder.py` | now a module; `library/parts.md` should grow from it |
| `pcbkit/checks/drc.py` | `scripts/drcsum.py` (same) + dodeca `tools/unrouted.py` | one command: summary, errors, unconnected by net and footprint, split zones |
| `pcbkit/checks/erc.py` | spotlight `scripts/erc.py` (kicad-cli) | the sign/siebenuhr `erc.py` used konnect's `run_erc`; same information |
| `pcbkit/checks/netlist.py` | `scripts/netcheck.py` (sign/siebenuhr same, spotlight richer) | the parser and the `expect`/`same` helpers; each project's assertions stay in the project |
| `pcbkit/checks/route_check.py` | dodeca `tools/route_check.py` | power classes default to classes with track width >= 0.5 mm |
| `pcbkit/checks/cleanup_tracks.py` | dodeca `tools/cleanup_tracks.py` | board path required |
| `pcbkit/silk.py` | `scripts/silk_place.py` (same) | unchanged |
| `pcbkit/netview.py` | dodeca `tools/netview.py` | dodeca's net colour table replaced by a generic one plus `--colours` |
| `pcbkit/release/previews.py` | `scripts/make_previews.sh` (sign/spotlight, differ only in board list and render zoom) | Python; zoom and render size are flags |
| `pcbkit/release/plot_print.py` | `scripts/plot_print.py` (same) | LED-reference hiding is now `--hide-refs REGEX` |
| `pcbkit/release/jlc_fab.py` | sign `scripts/jlc_fab.py` + `scripts/jlc/corrections.csv` | unchanged logic; corrections table in `pcbkit/data/jlc/` |
| `pcbkit/release/panel.py` | sign `scripts/make_panel.sh` + `setup_tools.sh` | KiKit pinned to the same commit, installed once under `~/.local/share/pcbkit`; presets, `edge_tabs.py` and `panel_post.py` in `pcbkit/data/panel/`, referenced by absolute path through a generated override preset; rows is a flag |
| `pcbkit/data/templates/blank.kicad_pcb` | `scripts/blank.kicad_pcb` (same) | |
| `pcbkit/import_easyeda.py` | dodeca `hardware/panels/tools/easyeda_to_kicad.py` | generalised: symbols from any EasyEDA part (one per name + pin geometry, rotations baked in), power symbols named after the flag, PWR_FLAGs, loose pads become board-only, `--pin` for symbol typos, `--force` to write despite connectivity differences, library paths as flags. First used on the ESP32 HA protoboard (2026-10-10) |
| `pcbkit/sexp.py` | dodeca `tools/sexp.py` | small s-expression helpers used by the dodeca generators; kept for the next harvest |
| `library/PARTS.md`, `pcbkit parts` | design notes and BOMs of the candle (ESP32Candle, espcandle-demo), rgb-spotlight, siebenuhr controller-v2, esphome-dimmer-switch, esp32-ha-protoboard, motion-sound, dodeca v1.2/v1.3 parts research | the user's list of parts that worked (2026-10-11), each checked against the project that used it and the parts cache; notes cite the project |
| `pcbkit/spice.py` | dodeca `hardware/control-board/v1.3/sim/spice.py` | same idea (scratch directory, `-D ngbehavior=psa`, @SIM@/@VENDOR@ placeholders, measurements and wrdata files); adds an `aborted` flag from ngspice's own messages, an optional gear-integration retry and the `pcbkit spice` CLI. The decks and averaged models stay in dodeca |

## Migration status

| Project | State |
| --- | --- |
| siebenuhr controller-v2 | migrated 2026-10-10 (commit `scripts: use pcb-kit for the shared tooling`): `scripts/builder.py` is a 15-line glue module, the copied scripts are gone, README lists the pcbkit commands. Its own routing chain (`scripts/route/`, built on KiCadRoutingTools) is untouched |
| rp2354-messages-sign | migrated 2026-10-10 (commit `scripts: use pcb-kit for the shared tooling`): `builder.py` is glue that becomes `pcbkit.schematic` (its module/archive scripts set `builder.P`), the three `make_*.sh` wrappers call `pcbkit previews/panel/fab`, `netcheck.py` sits on the kit's parser. Kept: `char_variants.py`, the generators, `route_strip.py`, `route_controller.py`, `resilk.py`, `renumber_controller.py`, `check_char_netlist.py` |
| rgb-spotlight | migrated 2026-10-10, committed in that repo as `05d2b1d`. `builder.py` is glue that becomes `pcbkit.schematic` (power symbols GND, +5V, +12V), `netcheck.py` keeps its driver-board assertions on the kit's parser, `set_outline.py` and the two `make_*_pcb.sh` use `pcbkit drc`, `make_previews.sh` wraps `pcbkit previews`. 2026-10-11: `mech.strip_outline_file` uses `pcbkit.mech.top_level_blocks`; `scripts/make_fab.sh` from `templates/project`; LAYOUT.md and PRODUCTION.md per CONVENTIONS; `pcbkit check` findings fixed or explained (open 12 V ring, net-class width); `sim/channel.py` on `pcbkit.spice` (behavioural PT4115 channel). Kept: `mech.py` (outline presets), `make_lib.py`, `ratsnest.py`, `route_driver.py`, `route_strip.py`, the generators |
| dodeca-v2-hardware | not yet; v1.3 stays as is until it is ordered |

## Still in the projects (candidates for the next pass)

- `scripts/route_strip.py` (sign and spotlight, 790 lines, diverged by 8 lines): LED-strip routing. Needs a diff and
  a decision on which is right before it moves.
- `scripts/gen_*_pcb.py`, `sch_*.py`, `mod_*.py`, `ctl_*.py`: board generators. Project data by nature, but they share
  a shape (placement tables, `build()` calls) that a `pcbkit.board` module could carry: the collision-checked spiral
  placement and zone/stackup setup in dodeca's `pcb_build.py`.
- dodeca `tools/pcb_build.py`, `pcb_update.py`, `power_stage.py` (engine parts), `plane_vias.py`, `maze_route.py`,
  `route.py` (Freerouting driver), `assemble.py` (schematic from `design/*.json`), `silkscreen.py`, `guide_images.py`.
- `scripts/make_fab.sh` is still per project (board list, which boards get panels); the DRC gate and the exports are the kit's.
- siebenuhr `scripts/route/` (`route_board.sh` drives KiCadRoutingTools: power copper, guards, gap router, cleanup, GND fill) is the
  first project chain on that router and the model for a `pcbkit route` wrapper; `enclosure_check.py`, `enclosure_heightmap.py`, `placement_plan.py`; spotlight `mech.py`,
  `set_outline.py`, `ratsnest.py`; sign `resilk.py`, `renumber_controller.py`, `build_matrix.py`.
