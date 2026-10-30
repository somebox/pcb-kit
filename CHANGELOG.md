# Changelog

What changed in each version of pcb-kit: the `pcbkit` package and CLI, the skills, the templates and the library.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/): before 1.0, a minor version (0.x) may change a command, a flag or a
skill's instructions, and a patch version (0.x.y) is for fixes only. How to cut a release is in AGENTS.md.

## [Unreleased]

### Added
- **A checklist for JLC's placement preview, `PLACEMENT-<name>.md`, beside the BOM and CPL from `pcbkit fab`**, and
  in each release folder as `PLACEMENT-<board>-v<version>-jlcpcb.md` (copied to an order's `upload/`). It lists
  every placed part whose orientation matters (more than two pads, two pads that differ, or a silkscreen or fab mark
  on one of two pads), by side then reference: KiCad's angle, the correction and the CPL's angle, the rotation rule
  behind it and when that rule was last seen right in the preview, and where pad 1 and the part's centre are, in mm
  from the drill/place origin with Y up, the frame of the Gerbers and the CPL. A table by part comes first.
  Symmetric two-pad parts are left out. `pcbkit fab`'s summary counts the parts, and the ones under a rule never
  checked. `data/jlc/corrections.csv` gains a Checked column with the date each rule was seen right in JLC's
  preview (2026-10-26: SOT-23, SOT-23-5, SOT-23-6, SOT-363, SOP-4, SOIC, SSOP, TSSOP, the HRO USB-C, the 1.27 mm
  socket and the Bourns 3224W); the column is left out of the table's hash in the fab settings, so it changes no
  board's fingerprint.
- **A shopping list for hand assembly in each ORDER.md**, and a "where" column in PRODUCTION.md's parts table:
  every part the order's boards need by hand, with what is on hand, what to buy and where from. Part rules gain
  `buy` (where a part comes from; a hand part's LCSC number serves otherwise), `group` (rules in another group are
  matched apart, so one connector can give a panel part and a cable) and `replace` (the footprint's own part is
  dropped, also from a bare board's list, for a breakout's pot already counted by its module). Each shopping list
  takes where a part comes from from the releases its order pins, then the order's extras, then the boards as they
  are now. `pcbkit release --refresh-parts` records a release's hand parts again after the rules change, for boards
  unchanged since; a change with the same fab output counts only if every footprint's fields are the release's
  too, which each release now records.
- **ORDER.md shows each board**, its picture (render-top.png, else pcb-front.png, from previews of the pinned
  version), and takes two more keys in order.toml: a board's own `fab` notes (a front panel's black mask), and
  `[[extra]]` parts bought with the order but not placed on a board (spares, a rework kit: part, qty, buy, note),
  listed in ORDER.md's Extras and counted in PRODUCTION.md's parts for the open orders. The tables' note column
  shows only notes of events that name the board; notes for the whole order stay in Events.
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
- **`pcbkit fab --keep-silk-in-mask`, and `keep_silk_in_mask = true` in a board's pcbkit.toml profile**, for a
  board that prints silkscreen inside mask openings on purpose (white numerals in a black face panel's windows):
  its Gerbers keep that silkscreen instead of having the mask openings subtracted from it. `pcbkit release` passes it
  on and counts it among the board's fab settings, so turning it on is a change it compares; boards without it keep
  their fingerprints and give the same Gerbers as before. `pcbkit new`'s pcbkit.toml lists it.
- **The fab-order skill** puts an order into the user's JLC and LCSC carts, in their browser, and never pays. It
  covers choosing what JLC assembles (Standard or Economic, what to solder by hand), the PCB and assembly forms as
  they were in 2026-10, and the BOM page's traps. Those include lines with letter-suffixed designators, which come
  up unticked at quantity 0. It also covers checking the placement viewer against `PLACEMENT-<board>.md` (the
  **never** rules first, pad 1 and the centre by the viewer's hover, dating a rule's Checked column once it is seen
  right; for a release made before the checklist, one rebuilt from git beside it), the LCSC cart for hand assembly,
  and what to record. From Orbitchron's first v2 order, where the viewer
  caught a USB-C turned 180° and SOT-23 parts a quarter turn out.
- **`pcbkit fab` writes a `-back` BOM and CPL** beside `-front` and `-both`, for an order where JLC assembles only
  the back (Economic PCBA does one side) and the front is soldered by hand. They are the `-both` files' bottom rows,
  so a project script that cut them from `-both` can go; the summary gives their counts like the others', front and
  back adding up to both. `pcbkit release` takes the back pair for a board whose placed parts are all on the back
  (the same rows its `-both` pair had), and the fab fingerprint leaves the `-back` files out, so releases made
  before keep their fingerprints.

### Changed
- **JLC placement rotations corrected from JLC's own preview.** KiCad's SOT-23 now gets 180 (the table's -90 put
  every SOT-23 transistor and reference a quarter turn off its pads), SOT-363 0 (it was half a turn round), SOP-4
  270 like the other SOP and SOIC packages (at 0 a CPC1017N relay's leads lay across its pads) and the Bourns 3224W
  trimmer 180 (it had no rule, and its wiper landed on an end pad). The rotation table is part of an
  assembled board's fab settings, so `pcbkit release` compares the placement files again when the table changes and
  gives a board whose CPL changed its next version.
- **JLC rotations for one LCSC part, and SOT-23-5/6 by part.** A row in `data/jlc/corrections.csv` whose pattern is a
  bare LCSC number (`C7843`) is a rule for that part and outranks the package rule that matched its footprint; a
  footprint no package rule matches (imported from EasyEDA, already in JLC's orientation) gets no turn from it. JLC
  places from each part's EasyEDA footprint, and its SOT-23-5 and SOT-23-6 footprints come both ways (names ending
  -BL need -90, -BR 180). KiCad's SOT-23-5 now gets 180 (the old `^SOT-23` rule gave every SOT-23-5 -90, a quarter
  turn off for the TLV62569 and SN74LVC1G04), with C485147 at -90; SOT-23-6 keeps -90, with C7843 and C55266 at 180.
  The SOT-23 rules match only KiCad's own names, so a footprint imported from EasyEDA, already in JLC's orientation,
  gets no turn. The 1x08 1.27 mm SMD socket (`PinSocket_1x08_P1.27mm_Vertical_SMD_Pin1Right`) gets -90, and the HRO
  TYPE-C-31-M-12 USB-C (C165948) 0: the table's 180 put its contact row on the far side of its pads, on every board
  that has it.
- **`pcbkit fab` names the BOM rows JLC will leave unticked.** JLC's BOM page leaves a row whose designators end in a
  letter (J1B, J1C) unticked at quantity 0 ("There may be multiple types of parts"); the summary lists those LCSC
  numbers so they get ticked by hand. The " / " between differing values on such a row was not the cause.
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
- **The bom-cost skill has JLC's assembly fees** for Standard and Economic PCBA, from 2026-10-26 quotes. Under
  Standard, every unique part pays the same loading fee, Basic or Extended, so swapping an Extended part for a Basic
  one saves only the price difference. BOM.md gains an assembly estimate. A new rule says hand-soldering pays only
  when it takes a whole side off the order.

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
