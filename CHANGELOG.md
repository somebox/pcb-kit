# Changelog

What changed in each version of pcb-kit: the `pcbkit` package and CLI, the skills, the templates and the library.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/): before 1.0, a minor version (0.x) may change a command, a flag or a
skill's instructions, and a patch version (0.x.y) is for fixes only. How to cut a release is in AGENTS.md.

## [Unreleased]

### Added
- **Routing discipline in `pcbkit.route`, all opt-in.** `P["DIR"] = {layer: "h" | "v"}` gives each layer a preferred
  direction, priced by `P["DIR_COST"]` (along, across, diagonal; default 1, 4, 2), so on a board with plane layers
  F.Cu and B.Cu cross instead of wandering round chips; `complete_nets` and `tie_pads` take it from P, and the A*
  heuristic follows it (faster runs). `P["ORDER"] = "longest"` makes `complete_nets` route every connection of the
  plan longest first, across nets. `fanout(board, ref, ...)` gives a QFN, QFP, SOIC or TSSOP dog-bone vias out from
  each signal pad, in two staggered rows (`dist`, `dist + stagger` beyond the pad edge), with a 45-degree jog where
  a via has to sit to the side; it skips power nets (`skip_nets`), exposed pads and pads already wired. Without the
  new parameters the router gives the same copper as before.
- **`pcbkit release`: a version for every board that changed, stamped into its KiCad files.** Each run checks
  every board (ERC, DRC, and no unconnected items or parity findings of any severity; a board that fails is reported
  as a mockup), compares its fab output for every fab house with its last release, and gives a changed board the next
  version: v1.0 becomes v1.0.1, or the next
  minor or major with `--minor`/`--major`. The version and date go into the title blocks and the silkscreen texts
  that carried the old label, refused while KiCad has the board open and checked by DRC afterwards. Its Gerbers,
  BOMs, placement files, PDFs and a manifest go into a release folder that is never rewritten, with releases.toml
  beside it, with what a board of that version needs by hand; previews are rebuilt. A KiCad save or a schematic note
  that leaves the fab output alone is recorded as equivalent, not a new version; a change to the custom rules, the
  net classes or the fab settings is compared. With releases turned off it only checks.
- **`pcbkit production`: one overview of versions, orders and stock.** It reads each order's order.toml (pinned
  versions; dated events with status, order number, price, note) and stock.toml, and writes PRODUCTION.md's
  generated section between its markers: boards in production, every board's version in KiCad and released, parts
  cost from the parts cache, latest order and stock, the parts the open orders need against what is on hand (as the
  versions they pin need them; every part for a board ordered bare), and problems such as a board changed since its
  release or an order whose release lacks the files its fab house needs. Each order gets an ORDER.md and an upload folder. `--check`
  exits 1 on problems.
- **`pcbkit.toml`, a project's settings** for both: where boards, releases, previews and the production record live,
  per-board profiles (checks, Gerbers only, extra layers), which parts the assembler places, rules for the panel
  parts a board needs, and switches to turn releases or the production record off. `pcbkit new` writes one.
- **The pcb-kit skill offers links to the KiCad 10 manual** for what the user should review or do in KiCad: the
  DRC and ERC checks, Board Setup, custom rules, zones, fab outputs, title blocks, kicad-cli.
- **PCBWay files from `pcbkit fab --houses jlcpcb,pcbway`:** a BOM with manufacturer and part number (from the MPN
  field or the parts cache) and a centroid file with KiCad's own rotations, beside the JLC files.
- **A license and an icon for the plugin listing.** The repo carries the MIT LICENSE that pyproject.toml already
  named; `.claude-plugin/plugin.json` gives the license, homepage and repository, and the plugin has a listing icon.

### Changed
- **`kicad-cli` shows no dock icon.** Run from inside KiCad.app, macOS gave every DRC, ERC, render and export
  KiCad's icon in the dock for a moment. pcbkit now runs it from a `KiCadCLI.app` under
  `~/.local/share/pcbkit/kicad-cli/`: a copy of `kicad-cli` signed ad hoc, KiCad's Info.plist marked as a
  background app, and links to KiCad's frameworks and data. There is one for each KiCad and version, made on first
  use and never changed, so parallel runs and a second KiCad don't disturb each other. DRC reports, renders, Gerbers
  and BOMs match KiCad's own `kicad-cli` (checked on the sign, spotlight, dodeca and synth-blocks boards).
  `PCBKIT_KICAD_DOCK=1` runs KiCad's own. `KICAD_CLI` names the copy; `KICAD_APP` and `KICAD_PYTHON` are still
  KiCad's.
- **`pcbkit fab` lists each LCSC number once in the BOM**, the values joined, since JLC showed a part split over two
  rows as missing. `--placed field:NAME=VALUE` picks the parts the assembler places, `--no-assembly` writes Gerbers
  only, `--extra-layers` adds layers to them and `--name` sets the file names.
- **The README is written for people outside the somebox projects too.** It says what the skills need (macOS,
  KiCad 10, the `pcbkit` CLI from a clone, konnect, bomi, ngspice) and what they run and send, including bomi's
  datasheet summaries through OpenRouter. The plugin's short description says the same in one line.

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
- **`pcbkit panel --hide-refs REGEX`** leaves matching references off the printable panel PDF (with `--previews`),
  as `pcbkit previews` already did.
- **Releases.** This changelog, `scripts/release.py` (bump, check, tag) and a workflow that publishes a GitHub
  release when a `v*` tag is pushed.
- **A Claude Code marketplace.** `.claude-plugin/marketplace.json` makes the repo installable with
  `claude plugin marketplace add somebox/pcb-kit` and `claude plugin install pcb-kit@pcb-kit`.

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
