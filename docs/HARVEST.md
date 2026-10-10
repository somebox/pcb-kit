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
| `pcbkit/sexp.py` | dodeca `tools/sexp.py` | small s-expression helpers used by the dodeca generators; kept for the next harvest |

## Still in the projects (candidates for the next pass)

- `scripts/route_strip.py` (sign and spotlight, 790 lines, diverged by 8 lines): LED-strip routing. Needs a diff and
  a decision on which is right before it moves.
- `scripts/gen_*_pcb.py`, `sch_*.py`, `mod_*.py`, `ctl_*.py`: board generators. Project data by nature, but they share
  a shape (placement tables, `build()` calls) that a `pcbkit.board` module could carry: the collision-checked spiral
  placement and zone/stackup setup in dodeca's `pcb_build.py`.
- dodeca `tools/pcb_build.py`, `pcb_update.py`, `power_stage.py` (engine parts), `plane_vias.py`, `maze_route.py`,
  `route.py` (Freerouting driver), `assemble.py` (schematic from `design/*.json`), `silkscreen.py`, `guide_images.py`.
- siebenuhr `scripts/enclosure_check.py`, `enclosure_heightmap.py`, `placement_plan.py`; spotlight `mech.py`,
  `set_outline.py`, `ratsnest.py`; sign `resilk.py`, `renumber_controller.py`, `build_matrix.py`.
- `scripts/make_fab.sh`: the per-project wrapper (DRC gate, then `fab` on the board and its panels). Becomes a few
  lines calling `pcbkit drc`, `pcbkit panel` and `pcbkit fab`.
