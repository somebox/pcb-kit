# Changelog

What changed in each version of pcb-kit: the `pcbkit` package and CLI, the skills, the templates and the library.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/): before 1.0, a minor version (0.x) may change a command, a flag or a
skill's instructions, and a patch version (0.x.y) is for fixes only. How to cut a release is in AGENTS.md.

## [Unreleased]

## [0.3.0] - 2026-10-18

Routing, placement scoring and a failure review before each order.

### Added
- **`pcbkit.route`, a maze router for project scripts.** `complete_nets` joins each net's copper islands with A*
  across the copper layers, with plane and escape vias, neck-down onto small pins, GND stitching, snapping route
  ends to pad centres and an audit of every track end. Pads are obstacles by their real shape on each layer they
  reach, including pads at an angle and padstacks that differ by layer. `CONN_SECONDS` and `DEADLINE` cap the
  time per connection and per call. Harvested from the message sign, the spotlight and Orbitchron.
- **`pcbkit cleanup --dangling`** removes the dead ends a route leaves, one leaf at a time, so no net is split.
- **`pcbkit place-score` and the placement-study skill.** Score a placement by its ratsnest (crossings where two
  nets could meet on one layer, length, area over zones to keep clear, free pin and gate choices), search groups of
  parts through their candidate positions, and draw the ratsnest with the crossings circled.
- **The failure-review skill.** Before an order: each block against its datasheets and reference designs, the
  failure modes and how to diagnose them, test points and rework provisions, and a briefing for the user before
  any change. `checklist.md` lists what to check per kind of block.
- **`"head"` on a mounting hole** in `enclosure.json` adds a courtyard ring and an F.Cu keep-out under the screw head.
- **Releases.** This changelog, `scripts/release.py` (bump, check, tag) and a workflow that publishes a GitHub
  release when a `v*` tag is pushed.

### Changed
- **`pcbkit import-easyeda` converts every sheet** and turns EasyEDA net labels into global labels; before, only
  the first sheet came across and nets joined by label were split. The footprint saved into the library is picked
  from a stable placement, so a rerun writes the same files.
- **The order checks in CONVENTIONS start with a failure review**, and the PRODUCTION template has a Bring-up section.
- **The README** has sections on the router and the one-page design overview, and the fit-check photo.

## [0.2.0] - 2026-10-11

Projects can be started, imported and fitted to a box with the kit, and four more skills.

### Added
- **`pcbkit new`** scaffolds a hardware project from the templates: docs with status lines, an empty KiCad
  project with library tables, `scripts/`, bomi and a first commit.
- **`pcbkit import-easyeda`** turns EasyEDA Std schematic and PCB exports into a linked KiCad project, with symbols
  and footprints in the project library, and runs ERC, DRC and parity on the result.
- **`pcbkit outline`** draws a board outline, mounting holes and keep-outs from a measured `enclosure.json`;
  `--fit-check` writes a 1:1 PDF with dimension lines and a scale bar, `--parts` adds the placed parts.
- **`pcbkit spice`** runs an ngspice deck with PSpice compatibility, prints its measurements and flags runs that
  aborted (harvested from the dodeca v1.3 simulations).
- **`pcbkit link`** makes the checkout importable from KiCad's Python and the `python3` on PATH.
- **`library/PARTS.md` and `pcbkit parts`:** the parts proven on built boards, with their notes and current stock.
- **Skills:** visual-review, enclosure-fit, power-sim and bom-cost.

## [0.1.0] - 2026-10-10

First harvest of the scripts the sign, siebenuhr and spotlight repos had copied between them, and of dodeca's checks.

### Added
- **Checks:** `pcbkit drc`, `erc`, `nets`, `check` (floating copper, stubs, loops, thin power feeds) and `cleanup`.
- **Release files:** `pcbkit previews`, `print`, `fab` (JLC Gerbers, BOM and CPL with rotation corrections), `panel`
  (KiKit panels, pinned with `setup-kikit`).
- **Look-ups:** `pcbkit jlc` (konnect's JLCPCB cache) and `pcbkit netview` (a board region coloured by net).
- **Library modules:** `pcbkit.schematic` and `pcbkit.konnect` (schematic sheets through konnect), `pcbkit.parts`
  (stock R/C), `pcbkit.silk`, `pcbkit.checks.netlist`.
- **The pcb-kit skill**, the project templates, `docs/CONVENTIONS.md` and `docs/HARVEST.md`.
