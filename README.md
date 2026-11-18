# pcb-kit

KiCad tooling and agent skills for designing, checking and ordering circuit boards with Claude Code. The design is
described in text, checked by scripts, and looked at in pictures at every step.

<img src="docs/images/boards.webp" width="880" alt="Eleven assembled circuit boards rendered in 3-D on a dark background: round, rectangular and many-sided boards with modules, connectors, LEDs and controllers">

Eleven boards from seven projects that use the kit, as they stood on 2026-11-17. Top: DodecaRGB's control board,
siebenuhr's controller, Orbitchron's driver and its 528-LED face. Middle: esp32-s3-ha-node, the ESP candle's top and
base, the spotlight's driver. Bottom: the spotlight's LED board, the message sign's controller and its 5 × 7 module.

- **`pcbkit`**, a Python package and CLI: checks, release files, schematics from scripts, net views, placement
  scoring, two routers, simulation runs, the project's overview page.
- **`skills/`**, ten agent skills in SKILL.md form, for Claude Code and pi.
- **`templates/`** and **`library/`**: the project skeleton, the conventions and the parts that worked.

The kit holds the engine. Each project keeps its own data (placement tables, net lists, design notes) and calls
`pcbkit`.

## What it does

### Draw the schematic from a script

A sheet is Python data. Each part is placed by what it does in its block: on the power path, hanging from a node,
or on another pin. `pcbkit.schematic` sets the blocks out in rows, frames them, draws small local nets as wires and
writes the sheet through konnect. `pcbkit sch-lint` then says how the result reads.

<img src="docs/images/schematic-blocks.webp" width="880" alt="A KiCad schematic sheet in six dashed frames, each with a title: a TPS55288 buck-boost stage, its output capacitors and shunt, a temperature sensor, an INA226 monitor, an over-voltage supervisor and the LED data output">

siebenuhr's LED power sheet, from `scripts/sch_led_power.py`. Two of its six blocks, and the lint's line for it:

```python
ntc = block("Converter temperature: NTC", path=[R44], shunt={"NTC_SENSE": [TH1, C44]})
ina = block("INA226 monitor", path=[R34, (U6, "10", "3"), R33],
            shunt={"INA_INP": [C38], "PWR_INT": [(R32, "up"), (TP7, "up")]})
```

```
led_power.kicad_sch: 66 parts, 88 wire segments; small local nets: 22 wired, 0 by label nearby, 2 by label apart
```

### Keep the whole project on one page

After each design round the user gets one page: where the project stands, a viewer of the boards, a few lines per
block, the decisions waiting for them with a recommendation, and what it costs. Earlier rounds stay on the page.

<img src="docs/images/overview-orbitchron.webp" width="880" alt="Orbitchron's overview page: round tabs and a legend of status marks at the top, a status panel with each board's version and state and what is next, and a viewer showing the 528-LED face with tabs for each board, render, layers and assembly map">

Orbitchron before its first order: three rounds along the top, one legend of status marks, every board as a
render, layer by layer, or as an assembly map.

<img src="docs/images/review-dodeca.webp" width="880" alt="DodecaRGB's order review page: the title, eight status chips, four numbers (size, parts, cost per board, open findings) and a 3-D render of the control board, above a row of section links">

<img src="docs/images/review-dodeca-areas.webp" width="880" alt="The Board areas section of the same page: a tab per block, a map of where the block sits on the board, two net-coloured close-ups, and the block's findings and decisions as cards with status chips">

DodecaRGB's control board before its order. The state is in chips and four numbers; each block then has its map,
its close-ups, its findings and its decisions.

<img src="docs/images/review-spotlight.webp" width="436" alt="The spotlight's lamp housing in a 3-D viewer with a button for each of seven rounds, sliders for the door shape and the numbers that follow from them"> <img src="docs/images/overview-generated.webp" width="436" alt="Orbitchron's page as pcbkit overview draws it: the rounds as a revision table, a legend of six marks, the status Cleared to pay with what this round did and what is next, and a dated note added since">

Left, the spotlight's housing at round 7: any of its seven rounds in 3-D, the door shape on sliders. Right,
Orbitchron's page as `pcbkit overview` draws it: the rounds as a revision table, six marks, notes added since.

The first three pages were built by hand in their projects. `pcbkit overview` makes one from a small file per
round, draws each board's turntable and layers, and keeps the earlier rounds as they were. The project-overview
skill has the pattern they share.

```bash
pcbkit overview new                 # the next round's file, started from the last one and the releases
pcbkit overview                     # docs/overview.html
pcbkit overview note "Paid: JLC W20261118." --mark work --state "boards ordered"
```

### Find what could fail before ordering

The failure-review skill checks each block against its datasheets and reference designs, lists what could go wrong
once the boards are back and how each failure would show on the bench, and settles the doubtful cases by
calculation or simulation. Each fix it recommends is tested like a finding, and the changes get a review of their
own before the order. The DodecaRGB page above is one such review, as its user read it.

### Look at the copper

`pcbkit netview` draws a region of one layer with every net in its own colour. `--compare` puts several versions
of the board side by side, beside the datasheet's own figure, with the numbers that decide between them.

<img src="docs/images/netview-power-mux.png" height="300" alt="Net-coloured view of a supply-mux region on the dodeca control board"> <img src="docs/images/copper-check.png" height="300" alt="Rasterised copper layer with two flagged items marked in blue and green">

<img src="docs/images/via-options-tps55288.webp" width="880" alt="Four panels of the TPS55288's pads: TI's layout figure with vias beside the strip pads, the board before with five via holes ringed red inside the strips, and two options with vias moved to the corners and beside the strips, numbers under each">

siebenuhr's controller: five open via holes under the paste of a buck-boost's strip pads, and the option that
moved them out while keeping 95 % of the via copper.

### Simulate the power path

`pcbkit spice` runs an ngspice deck with vendor models and returns its measurements. On the dodeca control board a
simulation found a supply-mux case where a firmware test would have misread good boards, which changed the part.

<img src="docs/images/sim-load-steps.png" width="880" alt="Six panels of simulated LED rail voltage and inductor current for load steps, overload and start-up">

### Place by the ratsnest, then route

A ratsnest score takes milliseconds where a full route takes most of an hour. `pcbkit place-score` counts
crossings, length and blocked via fields, and searches placements with them. `pcbkit reroute` then lays a board's
signals again: it moves small parts where that helps, routes every net together, and reports the old routing and
the new one on the same measures. On six boards it took vias from 633 to 480 and 90-degree corners from 248 to 49.
It is experimental: none of these routes has been built yet. [docs/ROUTER.md](docs/ROUTER.md) has the method.

<img src="docs/images/ratsnest-before-after.webp" width="880" alt="The ratsnest round a microcontroller before and after its small parts were moved: coloured lines per net, with fewer long crossings in the second panel">

siebenuhr's controller round its MCU: the ratsnest as placed, and after the small parts were moved.

### Fit a board to a box

Measure the enclosure, write the numbers into an `enclosure.json`, and `pcbkit outline` draws the outline, the
mounting holes and the keep-outs. `--fit-check` prints it 1:1 to cut out and screw into the box.

<img src="docs/images/enclosure-fit-check.png" height="440" alt="1:1 fit-check print: board outline notched around the lid bosses, mounting holes, part outlines, dimension lines"> <img src="docs/images/enclosure-fit-photo.jpg" height="440" alt="A fit-check print cut out and screwed into a project box, with a steel ruler and dial calipers measuring between two mounting screws">

### See the design before it exists

A placement mock-up drops stock footprints onto the outline, without nets, to show how full the board gets and
which edges the connectors need. One sheet can then carry a whole design for someone new to it.

<img src="docs/images/placement-mockup.webp" height="330" alt="3-D render of a placement mock-up: ESP32-S3 module at the top, screw terminals and USB-C on the right edge, JST connectors and side buttons on the left"> <img src="docs/images/overview-analog-voice.webp" height="330" alt="Overview sheet of the analog voice Eurorack module: front panel render in three-quarter view, back of the PCB with colour-coded sections, signal-flow block diagram, knob colour legend and a to-scale side view">

esp32-s3-ha-node's mock-up, and the one-sheet overview of synth-blocks' analog voice.

### Know what the board costs

bomi keeps the BOM in step with the schematic and refreshes stock and price from JLC and LCSC. A project's BOM.md
lists the cost drivers, the Extended-part fees and the parts whose stock is thin.
[library/PARTS.md](library/PARTS.md) holds the parts that built boards have proven, with the notes that made
them work.

<img src="docs/images/bom-cost-drivers.png" width="560" alt="BOM.md excerpt: cost drivers table with LCSC codes, unit cost at 1 and 100 boards, and notes on Extended-part fees">

### Check, release, order

```bash
pcbkit drc board.kicad_pcb          # DRC by type, net and footprint; erc, nets and sch-lint for the schematic
pcbkit check board.kicad_pcb        # what DRC misses: floating copper, stubs, loops, thin feeds, bad courtyards
pcbkit release                      # the next version for each changed board, its fab files in a release folder
pcbkit production                   # versions, orders, costs and stock in PRODUCTION.md
```

A release stamps the version into the board and its schematic, writes JLC's Gerbers, BOM and placement file with
rotations corrected, and a checklist of every part whose orientation matters, for JLC's placement viewer. The
fab-order skill then puts the order into the cart, in the user's browser, and never pays.

### Start or import a project

`pcbkit new` scaffolds a hardware repo with its documents, an empty KiCad project and a first commit.
`pcbkit import-easyeda` brings an EasyEDA schematic and board across, with symbols, footprints and 3-D models, and
runs ERC, DRC and parity on the result.

## What you need

The skills have the agent run tools on your machine, so they do their work in Claude Code or pi on a Mac that has:

- KiCad 10 in `/Applications/KiCad/KiCad.app`, or wherever `PCBKIT_KICAD_APP` points. `pcbkit` uses the app
  bundle's `kicad-cli` (through a background copy that shows no dock icon) and Python, so it runs on macOS only.
- The `pcbkit` CLI, installed with uv from a clone of this repository; see [Install](#install).
- For some of the work: [konnect](https://github.com/mixelpixx/Konnect) (schematic sheets, the JLCPCB parts cache),
  [bomi](https://github.com/somebox/bomi) (BOM, stock, prices, datasheets), ngspice (power simulation), and librsvg,
  poppler and ImageMagick (pictures and prints).

In claude.ai chat and Cowork the skills load, but those sessions have none of these tools, so there the skills can
only describe the steps.

## Using it from an agent

- **Claude Code:** the repo is its own plugin marketplace. Add it one of three ways, then
  `claude plugin install pcb-kit@pcb-kit`; the skills are `/pcb-kit:failure-review` and so on. Marketplace names are
  unique, so a machine has one of the three, not two.
  - `claude plugin marketplace add somebox/pcb-kit#v0.5.0` installs that release and stays on it. To move to a later
    one, `claude plugin marketplace remove pcb-kit`, then add and install again with the new tag.
  - `claude plugin marketplace add somebox/pcb-kit` follows `main`. A fresh install takes `main` as it is, which can
    include commits not released yet under the current version number. Claude Code keeps one copy per version, so
    `claude plugin update` changes it only when `plugin.json`'s version does, which is at each release's bump.
  - `claude plugin marketplace add ~/src/pcb-kit`, for working on the skills: the checkout loads in place, and edits
    apply after `/reload-plugins`.
- **pi:** `pi install /path-to/pcb-kit` (the `package.json` here declares the skills), or `pi --skill /path-to/pcb-kit/skills/pcb-kit`.
- **KiCad:** nothing to install; the commands drive `kicad-cli` and KiCad's Python, and konnect (the KiCad MCP server) is
  used from both agents and `pcbkit.schematic`.

## Skills

| Skill | Use it for |
| --- | --- |
| [pcb-kit](skills/pcb-kit/SKILL.md) | the command map, the project rules, KiCad Python gotchas |
| [visual-review](skills/visual-review/SKILL.md) | which picture answers which question, and how to read it before drawing conclusions |
| [enclosure-fit](skills/enclosure-fit/SKILL.md) | measuring a box, the outline JSON, the print-and-cut loop and what went wrong in it |
| [power-sim](skills/power-sim/SKILL.md) | one ngspice script per design question; averaged models when vendor models stall |
| [bom-cost](skills/bom-cost/SKILL.md) | the proven-parts catalog first, then bomi: stock checks, Extended-part fees, what goes into BOM.md |
| [placement-study](skills/placement-study/SKILL.md) | scoring placements by their ratsnest, the study file, searching, and routing only the winner |
| [routing](skills/routing/SKILL.md) | laying a board's signals again with `pcbkit reroute`: settings, reading the result, what to check before adopting it |
| [failure-review](skills/failure-review/SKILL.md) | before an order: datasheets and reference designs, failure modes and how to diagnose them, test points, a briefing before any change |
| [fab-order](skills/fab-order/SKILL.md) | the order itself, in the user's browser: what JLC assembles, the PCB and assembly forms, the BOM page's traps, checking every line of the placement checklist in the placement viewer against the release's placement checklist, the LCSC cart for hand assembly; never paying |
| [project-overview](skills/project-overview/SKILL.md) | the project on one page for the user, `docs/overview.html`: what goes into a round, a new round or a note, the six status marks, keeping it brief |

[docs/CONVENTIONS.md](docs/CONVENTIONS.md) is the project layout and writing style they all assume: dated status
lines, numbered decisions, generated files that say what made them.

## Tools it builds on

| Tool | Role |
| --- | --- |
| KiCad 10 (`kicad-cli`, pcbnew Python) | the design files, DRC/ERC, exports, renders |
| konnect | KiCad MCP server; `pcbkit.schematic` writes schematic sheets through it, and its JLCPCB cache backs `pcbkit jlc` |
| bomi | BOM, stock, prices, datasheet summaries |
| ngspice 47 | power-path simulation (`brew install ngspice`) |
| KiKit | panels, pinned to a known commit by `pcbkit setup-kikit` |
| KiCadRoutingTools | routing on siebenuhr, and a second set of checkers before every order |
| librsvg, poppler, ImageMagick | SVG to PNG, PDF crops and merges |

## Install

```bash
git clone https://github.com/somebox/pcb-kit ~/src/pcb-kit
uv tool install -e ~/src/pcb-kit     # gives you `pcbkit`
pcbkit info                          # confirms where KiCad is
pcbkit setup-kikit                   # only for panels: pins KiKit into ~/.local/share/pcbkit
pcbkit link                          # lets project scripts `import pcbkit` under KiCad's Python and python3
```

`link` writes one `pcbkit.pth` into the user site-packages of KiCad's Python and of the `python3` on PATH, so nothing
goes into an app bundle and it survives upgrades; `pcbkit link --remove` undoes it. Because `python3` can differ per
directory (asdf, direnv, venvs), project scripts also carry a four-line fallback to `~/src/pcb-kit` (or `PCBKIT_ROOT`);
see docs/CONVENTIONS.md.

Two interpreters are involved. `pcbkit` runs in uv's Python; the commands that need `pcbnew` re-execute themselves
under KiCad's bundled Python (3.9 on macOS), so the package code stays 3.9-compatible and dependency-free. Set
`PCBKIT_KICAD_APP` if KiCad is not at `/Applications/KiCad/KiCad.app`. Previews need `rsvg-convert` (librsvg),
`pdfunite` (poppler) and optionally `magick` (ImageMagick); `pcbkit spice` needs `ngspice`.

`kicad-cli` runs from a `KiCadCLI.app` under `~/.local/share/pcbkit/kicad-cli/` (`PCBKIT_TOOLS`): a copy of
`kicad-cli` signed ad hoc, with KiCad's Info.plist marked as a background app and links to KiCad's frameworks and
data. There is one for each KiCad and version, made on first use and never changed, so runs in parallel or with
another KiCad don't disturb each other; one whose KiCad has changed since is removed. Run from inside KiCad.app, every DRC, render or export would put KiCad's
icon in the dock for a moment; the copy's output is the same. `PCBKIT_KICAD_DOCK=1` runs KiCad's own `kicad-cli`.
A project script that runs `kicad-cli` can take it from `pcbkit.kicad.KICAD_CLI`, or `pcbkit info` prints it.

## What it runs and sends

The plugin is skills only: no hooks, MCP servers or scripts run when it is installed or loaded. The skills tell the
agent which commands to run, under Claude Code's usual permission prompts:

- `pcbkit` and `kicad-cli` read and write the project's files on your machine. `pcbkit` makes no network requests
  apart from `setup-kikit`, which installs KiKit with pip from GitHub at a pinned commit. `pcbkit new` makes a local
  git repository with a first commit and pushes nothing.
- konnect runs as an MCP server for schematic work. Its `download_jlcpcb_database` tool downloads the JLCPCB parts
  database (from bouni/kicad-jlcpcb-tools) into `~/.konnect/`, and `pcbkit jlc` and `pcbkit parts` read that copy
  offline.
- bomi looks parts up on jlcpcb.com (stock, price, details) and downloads datasheet PDFs from lcsc.com. Its
  datasheet summaries, which the failure-review skill uses to find a section before it reads the PDF, send the
  datasheet to OpenRouter with the key you set up in bomi; without a key, read the PDF alone.
- The failure-review skill has the agent search the web for datasheets, reference designs, application notes, open
  designs using a part and vendor forums. Power-sim projects download vendors' SPICE models with a script of their
  own.

## Commands

| Command | What it does |
| --- | --- |
| `pcbkit drc BOARD [--refill] [--save] [--full]` | DRC through kicad-cli: counts by type, every error, unconnected items by net and footprint. `--save` refills and writes the board back, which is how the projects keep their boards filled. Exit 1 on errors or unconnected items |
| `pcbkit erc SCH` | ERC summary, exit 1 on errors |
| `pcbkit nets SCH [NET ...]` | net sizes and members; `pcbkit.checks.netlist.Netlist` is the base for a project's topology assertions |
| `pcbkit sch-lint SCH... [--summary] [--only TYPE,...] [--json] [--strict]` | how a schematic reads, from the files (the root and its sheets; no KiCad needed): overlapping texts, text across another symbol, wires through text or a symbol, a reference or value far from its part, small local nets joined by labels (`label-hop` when the pins are within `--hop-distance`, `label-island` when further), parts off the page, sheets with no title; per sheet, how many small local nets are wired. Exit 0 unless `--strict` |
| `pcbkit check BOARD` | what DRC doesn't flag: floating copper, stubs with their full length, loops, pours reaching one pad, power pads fed only through thin track, courtyards as `pcbkit courtyards` |
| `pcbkit courtyards BOARD\|LIB.pretty [--fix] [--margin 0.25] [--ref REF] [--easyeda PCB.json]` | courtyards that don't describe the part: none or an open one, on the other side from the pads, pads outside it, the body (fab outline, or silk without one) outside it. `--fix` redraws each as a rectangle round pads and body plus the margin, on the pads' side, and saves. `--easyeda` first draws each part's body on fab from the EasyEDA export the board came from, so an imported board's courtyards go round the body, not the silk |
| `pcbkit cleanup BOARD [--dry-run] [--dangling]` | duplicate, zero-length and covered segments removed, collinear runs merged; `--dangling` also removes dead-end segments that no net needs; edits the file text by uuid |
| `pcbkit previews BOARD OUT` | schematic PDF, copper PNGs front and back, hand-assembly map, 1:1 printable PDF, plane views on 4-layer boards, 3-D renders |
| `pcbkit print BOARD OUT.pdf` | the 1:1 printable PDF alone |
| `pcbkit fab BOARD OUT [--houses jlcpcb,pcbway] [--placed field:NAME=VALUE] [--no-assembly] [--extra-layers L,..] [--keep-silk-in-mask] [--name N]` | JLC Gerber zip, BOM (one row per LCSC number) and CPL (front-only, back-only and both-sides variants), rotations corrected from the kicad-jlcpcb-tools table; `PLACEMENT-<name>.md`, the checklist for JLC's placement viewer (polarised parts, their rotation rules, pad 1 positions); PCBWay's BOM and centroid with `pcbway`. The silkscreen has the mask openings subtracted unless `--keep-silk-in-mask` (legends printed inside a face panel's mask windows) |
| `pcbkit release [PCB ..] [--patch\|--minor\|--major] [--version X.Y.Z] [--dry-run] [--refresh-parts] [--note TEXT]` | every board per `pcbkit.toml`: checks; a new version for each changed one (minor when its netlist or BOM changed, patch when only its layout did; `--patch` or `--minor` overrides, `--major` is yours to give; a first release whose title block names a version needs `--version`), stamped into its KiCad files (the title blocks, in `date_format`; the silkscreen prints them through `${REVISION}` and `${ISSUE_DATE}`); its fab files, PDFs, previews and a frozen copy of its KiCad project (`kicad/`) in its release folder; then `pcbkit production`. `--refresh-parts` records a release's hand parts again after the part rules changed |
| `pcbkit changelog [PCB ..] [--since REF\|PATH] [--all]` | what changed in each board, in words, for the project's CHANGELOG.md: the netlist and BOM, then the layout (parts moved, turned or flipped, tracks, vias, zones, outline, silkscreen texts), since its last release, since a board or git revision named, or, never released, since its title block first read its version; `--all` adds each release against the one before |
| `pcbkit production [--check]` | PRODUCTION.md's overview from the releases, `production/orders/*/order.toml` and `production/stock.toml`; each order's ORDER.md (with the parts to buy for hand assembly) and upload folder; `--check` exits 1 on problems |
| `pcbkit panel BOARD OUT [--style vcut\|mousebite\|both] [--rows 3] [--previews DIR] [--hide-refs REGEX]` | KiKit assembly panels with rails, tooling holes, fiducials, order-number placeholder, unique per-copy references; DRC on each |
| `pcbkit jlc --lcsc C25744` | SQL search over konnect's JLCPCB parts cache |
| `pcbkit parts [TEXT] [--low N]` | the proven-parts catalog (library/PARTS.md) with current stock and price; flags low stock |
| `pcbkit netview BOARD OUT.svg X0 Y0 X1 Y1 [--layer] [--detail] [--compare BOARD=TITLE] [--png]` | a region of one layer coloured by net, for reviewing power stages; `--detail` adds mask, paste and via holes in paste, `--compare` puts several boards side by side |
| `pcbkit place-score BOARD [--study S.py] [--planes GND,..] [--search] [--write P.json] [--svg OUT.svg] [--png]` | ratsnest score of a placement (side-aware crossings, length, zones, free pin/gate choices); with a study, coordinate descent over groups of parts; the ratsnest picture |
| `pcbkit spice DECK.cir [--vendor DIR] [--gear-retry] [--keep DIR]` | ngspice with PSpice compatibility in a scratch directory; prints `.meas` values and `wrdata` files, exit 1 when the run aborted |
| `pcbkit reroute SETTINGS.json [--from=STEP] [--until=STEP] [--out=DIR]` | a board's signals laid again by the negotiated router, the result in `build/reroute/`, never written to the board; `corpus`, `bench` and `page` for work on the router; `pcbkit setup-router` once |
| `pcbkit overview [new \| note TEXT --on ID] [--check] [--single [OUT]] [--render]` | the project on one page, `docs/overview.html`, from `docs/overview/r1.toml`, `r2.toml`, ...: status, a viewer that turns each board and switches its layers, topics, decisions with options, cost and orders, every round kept. `new` starts the next round from the last, `note` adds a dated note to one, `--check` names what is wrong, `--single` writes a copy with its pictures inside |
| `pcbkit new DIR --board NAME [--title ..] [--description ..]` | scaffold a project: README/DESIGN/LAYOUT/PRODUCTION/CHANGELOG, pcbkit.toml and .gitignore from the templates, `boards/NAME/` empty KiCad project with lib tables, `lib/`, `scripts/` (previews, fab, builder glue), bomi, first git commit |
| `pcbkit outline BOARD enclosure.json [--fit-check PDF] [--parts]` | board outline (rounded, notched or cut around bosses), NPTH mounting holes with standoff courtyards (and a screw-head keep-out with `"head"`), keep-out rule areas over bosses, reference geometry and notes, from a JSON measured off the enclosure; rerunnable. `--fit-check` adds the 1:1 print, `--parts` the placed parts on it |
| `pcbkit import-easyeda SCH.json PCB.json boards/<b> --name <b> --lib lib --lib-name <project> [--pin REF:NAME=NUM] [--no-fetch]` | EasyEDA Std exports to a linked KiCad project: converted schematic (every sheet; net ports and labels as global labels), symbols and footprints merged into `lib/` (boards can share it), PCB nets renamed, importer fixes (paste margins, User.4 keep-outs, outline gaps, loose pads board-only, bodies drawn on fab from EasyEDA's component shapes, courtyards that miss pads or the body redrawn, on the board and in the library; `--keep-courtyards` only lists them), 3-D models fetched from EasyEDA as VRML into `lib/3dmodels/` and placed by EasyEDA's outline (`--no-fetch`: only those already there), ERC/DRC/parity run |

Library use from a project script:

```python
from pcbkit import schematic as sch          # schematic sheets from Python data, through konnect;
                                             # sch.build(..., wire=25.4) draws small local nets as wires, not labels;
                                             # sch.pin/put/step/chain place a part by one of its pins;
                                             # sch.block places a whole block from its parts' roles (path, shunt,
                                             # taps, across), build(blocks=) draws its rails, sch.arrange sets the
                                             # blocks out in rows, sch.fields tidies references and values,
                                             # sch.frames frames each block, sch.title_block gives a sheet
                                             # the root's title, rev and date and a line on what it holds
from pcbkit.konnect import Konnect           # the MCP client itself
from pcbkit.parts import resistor, capacitor # stock R/C with LCSC numbers; parts.catalog() reads library/PARTS.md
from pcbkit.silk import Placer               # silkscreen label placement around an anchor box
from pcbkit.route import complete_nets      # maze router with time limits, plane vias, neck-down, track-end audit (KiCad's Python)
from pcbkit.placement import Study, Group, Zone, Perm, assign   # a placement study (see the placement-study skill)
from pcbkit.checks.netlist import Netlist    # netlist topology assertions
from pcbkit.spice import run                 # ngspice deck -> measurements, wrdata files, aborted flag
```

## Repository layout

```
pcbkit/            the package (checks/, release/, config.py, production.py, overview.py, tomlw.py, schematic.py, konnect.py, parts.py, silk.py, route.py, placement.py, netview.py, spice.py, import_easyeda.py, mech.py, new_project.py)
pcbkit/reroute/    `pcbkit reroute`: the flow and the KiCad side of the negotiated router
pcbkit_router/     the router itself, under its own Python (numba, numpy, scipy, pillow)
bench/             corpus.json (the boards a router change is judged on, each at a pinned commit), baseline.json (their results)
pcbkit/data/       jlc/corrections.csv (rotations, dated where JLC's preview showed them right), panel/ (KiKit presets, tabs plugin, post script), overview/page.html (the overview page's styles and script), templates/blank.kicad_pcb
skills/            SKILL.md folders, one per workflow (Claude Code plugin manifest in .claude-plugin/, pi manifest in package.json)
templates/project  README/DESIGN/LAYOUT/PRODUCTION/CHANGELOG skeletons, pcbkit.toml and .gitignore for a new hardware repo
library/           PARTS.md (parts that worked, with notes); shared symbols, footprints and blocks as they are harvested
docs/              CONVENTIONS.md (how a project is laid out and documented), HARVEST.md (where each piece came from), ROUTER.md (the router's method), images/
scripts/release.py version bump, check and tag; CHANGELOG.md lists each release, AGENTS.md says how to cut one
```

## Status

Harvested on 2026-10-10 from the rp2354-messages-sign, siebenuhr controller-v2, rgb-spotlight and dodeca-v2-hardware
repos; see [docs/HARVEST.md](docs/HARVEST.md) for where each piece came from and what is still in the projects.
siebenuhr, the message sign and the spotlight use the kit; dodeca still runs its own tools until v1.3 is ordered.
Since then: EasyEDA import, enclosure outlines and fit-check prints (2026-10-10/11), `pcbkit spice` (2026-10-11,
from the dodeca v1.3 simulations), the proven-parts catalog (2026-10-11), the router (2026-10-12), placement
scoring with the router's pad and hole fixes (2026-10-14, from Orbitchron), the router's time limits
(2026-10-17, for esp32-s3-ha-node), the failure-review skill (2026-10-18, from the dodeca and siebenuhr reviews), and the
negotiated router (2026-11-01, from siebenuhr's routing research), schematic sheets drawn in blocks and
`sch-lint` (2026-11-16, from siebenuhr's sheets), and the overview page (2026-11-17, from Orbitchron and the
message sign).
