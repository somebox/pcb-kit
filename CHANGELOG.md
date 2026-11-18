# Changelog

What changed in each version of pcb-kit: the `pcbkit` package and CLI, the skills, the templates and the library.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/): before 1.0, a minor version (0.x) may change a command, a flag or a
skill's instructions, and a patch version (0.x.y) is for fixes only. How to cut a release is in AGENTS.md.

## [Unreleased]

### Added
- **`pcbkit overview` and the project-overview skill: the project on one page, a round at a time.** The overview
  page of 0.5.0's conventions had no way to be made. A round is now a small file, `docs/overview/r<N>.toml`, that
  an agent writes from the project's records: status, boards, topics, the decisions waiting for the user with
  their options and the one recommended, cost, supply, fab and orders, each naming its source. `pcbkit overview`
  makes `docs/overview.html` from the rounds: one drawing sheet with the rounds as its revision table, six status
  marks (done, in work, not started, needs attention, blocked, your decision) with one legend, and a viewer that
  turns each board by drag (frames from `kicad-cli pcb render`) and switches its layers (each on its own, stacked,
  from the front or mirrored from the back), with the project's own pictures as further tabs. No network, light and
  dark, phone width. `pcbkit overview new` starts the next round from the last, `note` adds a dated note that can
  change an item's mark and state, `--check` names a bad mark, a decision without a recommendation, a source the
  project doesn't have, an edited earlier round and a stale page, and `--single` writes a copy with its pictures
  inside. Each round's pictures are kept as they were in `docs/overview/img/`, a board unchanged between rounds
  once. `pcbkit previews`' pictures are unchanged; its SVG and render steps are now `layer_png` and `render_png`,
  with `turntable` beside them. pcbkit.toml takes `[overview]` (dir, page, frames, size, layer_width, base).

### Changed
- **Three corrections to the skills, from a review of all nine.** bom-cost: `bomi list --check` can report a cached
  stock figure (124 of a TPS259470A on 2026-11-16, when JLC and LCSC had none), so a part with thin stock is read
  on JLC's own part page before an order is called ready. visual-review said JLC can't plug a via in or within
  0.35 mm of a pad; that holds for solder-mask ink, and such a via can be filled with epoxy or copper and capped
  instead (JLC's "Via Covering" page, 2026-11-16). pcb-kit: how to generate a schematic sheet was one table cell
  of 2,900 characters and is now `skills/pcb-kit/schematic-sheets.md`, in headed sections with every instruction
  and example kept; the table says what it covers and links to it.
- **The README leads with pictures.** It opens with eleven boards from the projects, rendered alike, and its tour
  of what the kit does is a few lines and a picture each. New pictures: a schematic sheet drawn in blocks
  (siebenuhr's LED power), three overview and review pages built in the projects (Orbitchron, DodecaRGB, the
  spotlight's housing), the same project as `pcbkit overview` draws it, and a ratsnest before and after its small
  parts moved. The paragraphs it lost are in the skills, docs/ROUTER.md, docs/HARVEST.md and this file.
- **The project-overview skill has `pattern.md`, the pattern of a page built by hand.** What the three such pages
  share: where it stands at the top, what changed since the user last looked, a viewer to work, one block at a time,
  decisions sorted by whose move it is, a source on every card, one set of status chips, and the rounds kept.
- **JLC rotations for KiCad's Vishay PowerPAK 1212-8 footprints (2026-11-18).** Neither had a rule, so their CPL
  angle was KiCad's own. KiCad lays both out with pads 1-4 down the left; LCSC's footprints for seven single-FET
  parts (the WSD20L75DN33, C148391, and six Vishay parts) have them along the bottom, a quarter turn, so
  `^Vishay_PowerPAK_1212-8_Single` adds 270, as `^PowerPAK_SO-8_Single` does. The dual comes both ways: the SIS932EDN
  (C727405) and SIS903DN (C3279452) lie as the single, so `^Vishay_PowerPAK_1212-8_Dual` adds 270, and the
  SI7904BDN (C143037) lies as KiCad's, so a part rule gives it 0. All three rows are read from the footprints and
  not yet seen in JLC's viewer, so their Checked dates are empty. A board with a part on one of these footprints
  gets a new CPL, and so its next version from `pcbkit release`.

- **The skills and conventions gone through for consistency, size and scope.** What an agent will notice:
  - `docs/CONVENTIONS.md` has "What a released board carries", the standard a board meets before its release: its
    name, version, date and author on the silkscreen; the power input, jumpers, connectors, pins, buttons and LEDs
    labelled; every soldered part's reference; named test pads; mounting holes with keep-outs; fiducials; a
    schematic that `sch-lint` passes; Basic parts first, each Extended one with its reason. It says what a prototype
    may leave out, with a note.
  - "Depth and scope" in the same file: every job says its scope and its depth (a look, as written, deeper) and
    names each step it left out. The failure review gains a look, and schematic-only and one-block kinds; a quote
    is the short form of an order, and an order has none.
  - Orders: every placed part's stock is read live on JLC before the user signs off, with each questionable figure
    looked into, and again when they are about to pay on a later day. A cart changes only on the user's sign-off,
    put to them as a diff, as a PR is.
  - Corrected: `pcbkit <cmd> --help` runs `info`, `link`, `setup-kikit` and `setup-router` instead of printing;
    a rerun of `pcbkit outline` deletes more than it made; the bomi commands as they must be typed; the release
    folder and the placement checklist are named by `<label>`, not `v<version>`; a four-layer route takes 40
    minutes to 3 hours; JLC's placement viewer has one name.
  - The project template: DESIGN.md has numbered open questions (O1, ...) beside its decisions, and LAYOUT.md's
    silkscreen line names the board with its version and author. The skills call PRODUCTION.md's section the order
    checklist, as the template does, and a quote of released boards is a `draft` event in the order's order.toml, no
    longer also a row in a hand-kept table; a quote from an unreleased commit goes under PRODUCTION.md's Costs.
  - Shorter: the skills' descriptions (764 to 591 words), lessons that were told twice now stand once beside their
    check, and the rules that must not be missed are in bold at the top of the pcb-kit and failure-review skills.
  - What loads when a skill triggers went from 15,618 words to 13,178. Seven passages that are read in one situation
    moved, word for word, into files beside their skills: the failure review's `briefing.md` and `test-points.md`,
    pcb-kit's `kicad-links.md` and `route-script.md`, fab-order's `old-release.md`, project-overview's
    `pattern.md` and visual-review's `compare-example.md`. The pcb-kit skill's table names the command and points
    to the conventions or `--help` for what they already say.

## [0.5.0] - 2026-11-16

### Added
- **Schematic blocks placed by role: `pcbkit.schematic.block`, `build(blocks=...)` and `frames`.** A sheet script
  was a flat list of parts with coordinates, so nothing said which resistor is whose feedback divider, and the
  script's author did the arithmetic. `block(title, at, path=[...], shunt={net: [...]}, taps=[...], across=[...])`
  takes the parts by what they do: `path` is what the power or signal passes through, in a row; `shunt` is what
  hangs from a node of that row (to GND downwards, to a supply upwards, a list in series as a divider is), and the
  row makes room for it; `taps` hang parts from a pin that is not on the path; `across` puts a part in parallel
  with another. It sets x, y and rot only. `build(..., blocks=[...])` draws each node row as a rail: one wire
  through the pins, named once, or with one power symbol at its free end where every pin had its own; it stops with
  an error if a rail would run over another net's pin. `frames(sheet, blocks)` draws a dashed frame round each
  block on the built sheet, fields and power symbols included, with the title above it. On a copy of
  esp32-s3-ha-node's power sheet, four blocks (input, two bucks, the feedback network) gave the same 74 nets and a
  clean ERC with 48 power symbols where there were 60; a block on siebenuhr's LED power sheet left its nets the
  same too. Rows that run into each other are not prevented: `pcbkit sch-lint` shows them.
- **A page set out on purpose: `pcbkit.schematic.arrange`, `measure` and `fields`.** `arrange([[a, b], [c]])` puts
  blocks in rows from the top left and moves each as a whole: the frames of a row share a top and a height and
  stand one gap apart. A block's size is an estimate until the sheet has been built; `measure(sheet, blocks)` notes
  the real one, so building twice (arrange, build, fields, measure; then again) gives frames that are exactly the
  ones arranged. `fields(sheet)` puts the reference and value of every two-pin part level: beside a part that
  stands (to the left when the right is taken), above and below one that lies flat; a part with more pins is
  touched only when it was turned so its fields read up the page. `frames` now takes in everything that hangs
  on a block (the wires from its pins and the labels and power symbols on them) and prints the parts that are in
  no block. A block's row can start at a connector (`(j2, None, "2")`), a flag or test point can hang from a
  rail, `Block.add` takes in a part placed by hand, a signal between two path parts gets room for its name, and a
  part put across one that lies flat is wired to it. On the same copy of esp32-s3-ha-node's power sheet, with
  every part in one of six blocks: the same 74 nets, a clean ERC, 38 power symbols for 60, all but one of its
  small local nets wired where none was, and no text on text where `sch-lint` found six (it still finds a zener's value
  next to a gate resistor, and the feedback pin joined to its divider by a label).
- **Sense returns drawn as wires: `block(..., sense=[(u5, "5")])`.** A regulator's feedback pin and the divider
  that hangs from its output are one net with the output capacitors between them, so the pin and the divider each
  got a label. A pin named in `sense` has its net wired however far it has to go: where no straight, L or Z run is
  clear, `build` takes the shortest run round what lies between (a stub off the pin, a channel above or below the
  parts, a millimetre clear of every symbol and text) and names it there. `plan_wires(..., far={net})` is the
  same for a caller that plans its own wires. On the copy of esp32-s3-ha-node's power sheet the 3.3 V feedback
  wire runs under the output capacitors to the divider tap; all 10 small local nets are now drawn (none was), the 74 nets
  are the same and ERC is clean.

- **Title blocks on generated sheets: `pcbkit.schematic.title_block`, and `reset` keeps them.** A generated sheet
  had an empty title block: `reset` wrote the file without one. `title_block(sheet, about="Power: input, bucks")`
  copies the title, date, rev and company from the root schematic (`configure(..., root="board.kicad_sch")`), so
  every page carries the project's name and the release it belongs to, and writes what the sheet holds on the line
  above (comment 1). `author=` sets comment 2; otherwise the root's comment 2 and up carry over, so an author
  written on the root once is on every page. Keywords set a field outright. `reset` now keeps a sheet's title block
  as it keeps its uuid, so what `title_block` or a release stamped there survives a rebuild.
- **Less to write in a block script: `part`, `flag`, no `at`, and `arrange(page=...)`.** `part(ref, lib, val, fp,
  lcsc, pins, mpn=...)` makes any part without a place, and `flag(ref, net)` a PWR_FLAG that hangs up from a supply
  or down from GND; `block(title, path=...)` needs no origin when `arrange` follows. `arrange(rows, page="A3")`
  centres the blocks on the page and keeps them off the title block, where they started at the top left. The script
  of esp32-s3-ha-node's power sheet, rewritten with blocks on a copy of the project, has four distances where it
  had 41 x, y pairs; the sheet has the same 74 nets, a clean ERC and the project's title, rev and date on it.
- **Placing schematic parts by pin: `pcbkit.schematic.pin`, `put`, `step` and `chain`.** A sheet script gave every
  part an absolute x, y, so a part ended up wherever there was room, away from the pin it works with. `pin(c, n)` is
  where a component's pin ends on the sheet, read from the symbol library (KiCad's, or the project's through the
  sheet folder's sym-lib-table) before anything is placed; `put(c, n, point, toward="down")` turns the part so its
  body lies on that side of the pin and lands the pin on the point; `step(point, "right", 7.62)` is a point beside
  another; `chain(point, [f1, q5], toward="right")` sets parts in series, each entered by the pin that shares a
  net with the one before. Pins that line up are what `build(wire=...)` joins with a straight wire. The predicted
  positions match konnect's placement for all 889 pins of the esp32-s3-ha-node and siebenuhr sheets. Five `put`
  calls on a copy of ha-node's power sheet (the buck's boot capacitor, catch diode and inductor on the switch pin's
  row; the CC pull-downs on their pins) put those nets in reach of `build(wire=...)`, netlist unchanged.
- **`pcbkit.schematic.build(..., wire=25.4)` draws small local nets as wires.** Without it every pin of a local net
  gets a label, and a feedback divider or a boot capacitor reads as parts with no visible connection. With it, two
  pins of a net (2 to 6 pins, not power, hierarchical or global) within that many mm are joined by a straight, L or
  Z run when it is clear of every symbol, text, other net's pin and other wire, with a junction where three meet
  and the net's name once on the wire. Pins it can't reach keep their labels, so the netlist is unchanged: checked
  on regenerated copies of esp32-s3-ha-node (74 nets) and siebenuhr's power sheet (126 nets), both ERC clean. The
  ha-node power sheet goes from none of its 10 small local nets wired to most of them; siebenuhr's gains none,
  because its parts sit in rows away from their ICs: the wires follow the placement. Off by default, so a sheet
  regenerates as it was.
  `plan_wires()` is the geometry on its own.
- **`pcbkit sch-lint`: how a schematic reads, measured from the files.** ERC says a schematic is legal; this lists
  what makes it hard to read: two texts on top of each other, a text across another symbol, a wire through a text
  or through the middle of a symbol, a reference or value more than 5 mm from its part, a part off the page, a sheet
  with no title. And it counts each sheet's small local nets (two to four pins; not a supply, and no global or hierarchical
  label or sheet pin):
  wired, joined by labels between pins within 25 mm (`label-hop`: draw the wire) or further apart (`label-island`:
  the part sits away from what it works with). It reads the root and every sheet below it with pcbkit's parser, so
  it needs neither KiCad nor konnect; text sizes are estimated, and it exits 0 unless `--strict`. On the generated
  sheets of four projects most small local nets are joined by labels (siebenuhr's mcu sheet: 0 wired, 40 by label).
  The reading rules come from [modular-schematic-layout](https://github.com/Ausanx/modular-schematic-layout).

### Changed
- **`pcbkit.schematic.build` joins supply pins that sit side by side.** Two pins of one part on the same supply,
  2.54 mm apart and facing the same way (the GND and pad pins of a regulator), got a power symbol each, the values
  printed over each other. They now share one symbol through a short wire. The netlist is the same.
- **`pcbkit.schematic.put` turns a part by the edge its pin is on.** It took the rotation with most of the part
  behind the pin, which turned a tall connector on its side when the pin was near a corner.
- **`pcbkit.schematic.build` joins pins of one net that sit side by side.** Two pins of a part on one net, 2.54 mm
  apart and facing the same way (the doubled D+ and D- pins of a USB-C connector), got a label each, one above the
  other. The second now joins the first by a short wire and the net is named once, as supply pins already were.
  The netlist is the same; a sheet built without `wire=` changes here.
- **A net name on an upright wire reads level**, beside the wire, where `build(wire=...)` wrote it up the page.
- **More JLC rotations from the preview (2026-11-15).** KiCad's `TSOT-23-6` gets -90 like its SOT-23-6, which it is
  laid out as: it matched the never-checked `^TSOT-23` (+180), a quarter turn off for an LMR51625. Two part rules
  for parts whose JLC footprint lies a quarter turn from the part their package rule was checked on: the TPS2116DRLR
  (C3235557) -90 on SOT-583-8, and the BCR421UW6Q-7 (C154739) 180 on SOT-23-6. `^SOT-23` was seen right again and is
  dated 2026-11-15. Where a board's CPL changes (one of these parts on a KiCad footprint that the rules match),
  `pcbkit release` gives it its next version. A part on a footprint no package rule matches, such as one imported
  from EasyEDA, gets no turn from a part rule, so that board's CPL and version stay as they are.
- **The failure-review skill reviews its own fixes, and its checklist covers more.** In every project with a second
  review, a change accepted from the first caused a finding in the second (the sign controller's eFuse, dodeca's
  TPS2116, ESP Candle's VBUS). The skill now tests each recommendation like a finding before the briefing (step 7)
  and reviews what changed before the order (step 11). It describes the kinds of review the projects were already
  writing: the changes since a review, layout only, a design that was imported or is already built. The second
  pass is never skipped, quotes the datasheet's table row with its column, and measures a layout claim on the board
  file; a datasheet summary finds the section and is no longer evidence. The briefing gains a *Found at* rating
  (power-up, bench, field), evidence marks, the checks run at the commit, and sections for earlier findings,
  refuted claims, firmware duties, notes and stock, decisions and bench results. Findings are ordered by what to
  settle first, and their numbers carry on from one round to the next. A check left undone is an open check in the
  status line, and in the verdict when it could kill the board, not a line under "Not covered". The checklist gains small-part ratings, clocks, the analog chain, logic levels between supplies,
  connector footprints against the maker's drawing, layout returns and RF, the placement count, mechanics, and a
  table of contents. Three of its facts are corrected: KiCad's track calculator uses IPC-2221 (it said IPC-2152),
  the TPS2116's reverse-current threshold is 42 mV typical and 70 mV maximum, and an AHCT1G125 is not specified to
  isolate with its rail off. USB's inrush limits now have their numbers: 10 µF at attach, 100 µF after a PD
  contract. A briefing written to the old template still reads; a new one has one more column.
- **Four working rules, written into the conventions and the skills.** A change made during the release steps is
  the board's next release, a patch at least, and what is ordered is a release (files for a quote alone need
  none). The order comes last and once: before a cart is touched the user confirms the versions and that no design
  change is open (fab-order's ground rules; "Checks before an order", steps 8 and 9). After each major design round the user gets the project on one
  HTML page, `docs/overview.html`, with status, a viewer, a section per block, open decisions, cost and supply,
  and every round kept (docs/CONVENTIONS.md, "The overview page"; the kit has no template or generator for it
  yet). A prototype is reviewed as one: a risk the bench can hold (a named supply, limited handling, a marked
  polarity) is noted, on the board too, and left for the revision that warrants it (failure-review, "Prototype or
  product"; DESIGN.md says which the build is).

## [0.4.0] - 2026-11-15

Versioned releases and the production record, the negotiated router, courtyard checks, and board versions
as text variables with a changelog.

### Added
- **`pcbkit changelog`: a draft of a project's CHANGELOG.md from its boards.** For each board, what changed in words:
  the netlist and BOM as `pcbkit release` compares them, a part's fields on one line and parts that changed alike
  together ("R4, R5 value 10Ω -> 20Ω, LCSC C17415 -> C17544"), then the layout: parts moved, turned or put on the
  other side, board-only footprints, the tracks' count and length, vias, zones (added, removed, reshaped), the
  outline, the silkscreen texts (added, removed, moved), a part's own texts moved on it (references placed by hand)
  and a title or company that `${TITLE}` or `${COMPANY}` prints; nets by name, so KiCad renumbering them is no
  change. Against the last release's `kicad/` copy (or the board at
  its base commit, when that is the release's netlist and BOM), `--since` a board
  (an as-built copy in `archive/`) or a git revision, or, for a board never released, the commit where its title
  block first read the version it reads; `--all` adds each release against the one before. On espcandle-pcb it
  gives the 35 netlist and BOM changes of the top since the board as converted from v1.4.0 (`c79678c`) and the 68
  of the base since the v1.3.1 import that the project's CHANGELOG.md was checked against. It reads files only.
  `docs/CONVENTIONS.md` lays out the project's CHANGELOG.md: a section per version as released, an entry per change
  citing the decision behind it, checked against the draft; `pcbkit new` writes one.
- **`[release] date_format` in pcbkit.toml**: the title block's date as a strftime format, so `${ISSUE_DATE}` prints
  "11.2026" with `"%m.%Y"` (the default stays ISO). releases.toml keeps the date as a date; a release compared with
  later is stamped with the date its own `kicad/` copy carries, so its files still compare the same.
- **`pcbkit netview --detail` and `--compare`: layout options side by side.** `--detail` draws an outer layer the
  way assembly sees it: each pad's mask opening dashed green, its paste hatched (paste apertures drawn as pads with
  no copper too, as KiCad's QFN footprints have them), the numbers of the pads on coloured nets, and a red ring round
  any via hole that lands in paste. `--compare BOARD=TITLE` puts the same window of more boards on one sheet, after
  `--ref IMAGE=TITLE` images such as the datasheet's layout figure (`--ref-rotate` turns them to match the board):
  each net in one colour across every panel, a title over each, the numbers under it, one legend and a footnote.
  `--metrics NET,...` counts the through vias in the window per net, with their barrels' copper cross-section and
  their resistance in parallel through the board (18 um plating outside KiCad's drill, which is the finished hole;
  blind and micro vias are counted apart; a name is a net's full name or the last part of it, and nets that share
  only the last part get a line each); `--note` adds to the footnote, and on its own makes a one-panel sheet.
  `--colour NET=#rrggbb` sets one net's colour (here and in `--colours`, a full hierarchical name comes before its
  last part, and the legend names nets that share a last part in full), and `--fill` fills the zones in memory first. Drawn from the siebenuhr controller v3 study of U5's vias
  (2026-11-09), whose matplotlib picture it reproduces (`docs/images/via-options-tps55288.webp`) with the same counts;
  the study put the plating inside the drill, so its copper figures were 13 % (0.25 mm) to 17 % (0.2 mm) low, and
  the options compare the same way. On the sign controller it finds all nine vias in U4's exposed pad in
  its paste windows; on dodeca v1.3's BQ25890 the vias sit between them. Tests in `tests/test_netview.py`.
- **`pcbkit courtyards`: courtyards that don't describe their part.** DRC's placement checks trust each footprint's
  courtyard, so one that misses its pads or body lets a part be placed on it and DRC passes. On espcandle-pcb's base
  board, as imported from EasyEDA, the first layout put a FET, a capacitor and the buck IC under the EXT connector,
  whose courtyard sat 1 mm inside its body. It reports a footprint with no courtyard or an open one, a courtyard only
  on the other side from all its pads (a through-hole part's only on the side away from the one it is mounted on,
  where DRC would let a part go under it), pads or holes outside it, and the body outside it: the fab outline, or the
  silk for a footprint with no fab outline (dots under 1 mm across are marks, not body). Outlines are checked at
  points 0.05 mm apart, so an edge across a notch counts; arcs at their extremes, curves on the curve, holes as their
  rotated slots, a through-hole pad's copper on both sides. `--fix` redraws each as a rectangle in the footprint's
  own frame round pads, holes and body plus `--margin` (0.25 mm, as KiCad's own libraries round 0603s, SOTs and
  QFNs), one on each side the footprint has pads on, and saves the board or the `.pretty` library it was given. A
  sound courtyard on the other side stays when it is a keep-out beside the part's own (a standoff, a screw head);
  a part's only courtyard, on the other side, goes (EasyEDA draws a back-mounted header's on the top).
  `--easyeda PCB.json` first draws each part's body on F.Fab or B.Fab from the EasyEDA export it was imported from
  (layer 99, the component shape, which KiCad's importer drops), placed by fitting its pads onto the footprint's, so
  a part moved or turned since still gets its own (mirrored if it changed sides; with `--fix --ref`, only those parts); the check and the redraw then go by the body, not EasyEDA's silk,
  which stands well off it. On espcandle-pcb's base that takes an 0603's redrawn courtyard from 3.29 x 1.94 mm to
  2.70 x 1.42 mm and an 0402's from 2.40 x 1.50 to 1.94 x 1.06. `pcbkit check` gains a "courtyards" section with the
  same findings. It finds 31 of the 44 parts on that base board as imported (the headers, switches, connectors and
  test pads fixed there by hand, and passives whose courtyard was a sliver or turned against the pads, and the ESP32
  module's, which left out the antenna), and none on the siebenuhr, Orbitchron, esp32-s3-ha-node, sign and
  spotlight boards drawn from KiCad's libraries. Of KiCad 10's own 15,464 footprints it reports 142: connectors,
  card sockets and angled switches whose fab drawing shows a part over the board edge (73), card-edge connectors
  (25), keyboard switches' stabiliser holes (6), 17 courtyards that don't close (lines 0.05 mm short of their arcs,
  missing sides), pads just outside on slide potentiometers, terminal blocks and antennas, and a CuK slide switch
  drawn on the front whose only courtyard is on B.CrtYd. Tests in `tests/test_courtyards.py`.
- **`pcbkit reroute`, a negotiated router, experimental.** It takes a placed board whose power copper and ground are
  drawn and lays its signals again: small parts moved where that helps, every signal net routed together, test pads
  placed after the routing, repairs, and the old routing and the new one compared. It never writes your board. On six
  boards it left no failed connection, with vias from 633 to 480 and track from 9220 to 8287 mm; none of these routes
  has been built yet. It runs under its own Python, `pcbkit setup-router`, as the new `pcbkit_router` package, and
  `pcbkit.route`'s maze router stays for routing a net at a time from a script. New skill `routing`, method and
  limits in docs/ROUTER.md, the boards a change is judged on in bench/.
- **pcbkit runs on Linux**, as in KiCad's Docker images (`kicad/kicad:10.0.6-full`): `kicad-cli` comes from PATH,
  pcbnew from `/usr/bin/python3`, and the power symbols `import-easyeda` copies from `/usr/share/kicad/symbols`. On
  rgb-spotlight's boards `drc`, `erc` and `check` give the same results there as on macOS; DRC lists the same
  violations in a different order.
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
- **`pcbkit import-easyeda` gives the footprints their 3-D models.** KiCad's importer points each model at
  `${KIPRJMOD}/EASYEDA_MODELS/<title>.step` and writes no file there (EasyEDA's STEP address answers 404), so the 3-D
  viewer showed bare pads. The import now fetches each model's OBJ mesh from EasyEDA by the id the export records,
  converts it to VRML in the library's `3dmodels/` folder and points the footprints at it
  (`${KIPRJMOD}/../../lib/3dmodels/<title>.wrl` in the usual layout). EasyEDA's outline of a model isn't always on
  the model's origin (0.15-0.5 mm off on espcandle-pcb's USB-C socket, switches and JST SH), so each model is placed
  by it: the export's pads are fitted onto the footprint's, the model's box is centred on the outline, and its bottom
  goes to the export's height, using the model's box as KiCad scales and turns it (x, then y, then z, each the
  other way, as kicad-cli's VRML export shows): EasyEDA turns some models about x or y. A mesh whose size doesn't
  match the export's is scaled to it (a 2.0 mm header's was drawn in 0.1 in), and a part whose export gives the
  same model another size gets the rest of the scale in its footprint. A model already in `3dmodels/` is found by
  the id in its file, whatever its name, and not fetched again; one whose title another model's file has becomes
  `<title>_2.wrl`. A library footprint from an earlier import that still names KiCad's path gets the model
  its board copy got. `--no-fetch` downloads nothing: footprints get the models `3dmodels/` already has and the
  others keep KiCad's path, listed. On espcandle-pcb's two boards the offsets and turns are those its
  `scripts/models3d.py` worked out, within 0.0002 mm (102 board and library footprints); siebenuhr's controller,
  the spotlight, the HA protoboard and Orbitchron's LED board import with every model placed. KiCad's own Python
  finds no certificates on macOS, so the download uses the system's. Tests in `tests/test_import_easyeda.py`.
- **Each release folder holds a frozen copy of the board's KiCad project**, in `v<label>/kicad/`: the .kicad_pro,
  the root schematic and every sheet under it (subfolders kept; a sheet from outside the board's folder in
  `kicad/outside/`), the .kicad_pcb as released, the .kicad_dru and the library tables, but not the .kicad_prl, lock
  files, KiCad 10's Local History (`.history/`) or backups. A sheet path that wouldn't reach the copy's own sheet (an
  absolute one, one out of the folder, one through `${KIPRJMOD}`) is rewritten to, so nothing in the copy's
  schematic is read from the working project; sheets are looked for as KiCad does, beside the sheet that names
  them, then in the project's folder, with `${KIPRJMOD}` and other variables expanded. KiCad
  links a schematic to a board by file stem within one project, and the net classes and rules live in the
  .kicad_pro, so a second board file beside one schematic doesn't open as that version; the copy does, beside the
  working board or any other release. Footprints and symbols are embedded; the `${KIPRJMOD}/../../lib/...` paths in
  the library tables and 3-D models (and a model's bare relative path) are rewritten to reach the same files from
  the release folder's depth. MANIFEST.txt lists the copy's files and what it names and doesn't find (a missing
  model, an absolute path, a variable not set here), and the release's output counts them. The fab fingerprint and
  the comparison with a release's own files read only the fab files, so the copy changes neither. Checked in
  `tests/test_release.py`: the copy opens with `pcbnew.LoadBoard` and `kicad-cli` draws both sheets of its
  schematic. The project template's `.gitignore` gains `.history/`.

### Changed
- **The version and date on a board are KiCad's text variables, `${REVISION}` and `${ISSUE_DATE}`**, filled from the
  title block when the Gerbers are plotted (docs/CONVENTIONS.md, which also says when to use `label = "full"`).
  `pcbkit release` stamps the title block of the board, of the root schematic and now of every sheet under it in the
  board's folder that has one, so every page of schematic.pdf carries the version (espcandle-pcb's v1.5.0 release
  left its two sub-sheets at 1.3.1); the board and the root schematic take each other's title and company when one
  has none. It stamps the release's date even when the label is already right, and a board unchanged since its
  release gets that release's stamps where a file lacks them. It refuses a board whose silkscreen, copper or mask
  says `v${REVISION}` (prints vv1.0) or `${CURRENT_DATE}` (the plot's day, so no two runs make the same Gerbers), and
  says when a text has the version typed in; a board already released with one stays current, with a warning. The fab fingerprint, the masked source fingerprint and the silkscreen
  check after stamping were checked with variable texts: DRC measures them as KiCad fills them in. A title block
  without a rev or date gets one in the neutral copy the fab fingerprint is made from, as stamping gives it.
- **A first release needs `--version` when the title block names a version** (`v1.4`, `1.3.1`, `2.0`): boards of it
  may have been built, and the new board would go out under their label. It took the title block's version before,
  which on espcandle-pcb would have released the changed top as v1.4.0 again. The output says what git knows: the
  commit where the title block first read that version, and how many netlist and BOM changes there are since. A
  draft's rev (`0.1-draft`) or none is still taken as it is. A board without a title block goes by its root
  schematic's, and `pcbkit new` gives the blank board the schematic's title block (title, date, `0.1-draft`,
  company), so a new project's first release is v0.1 and `${TITLE}` prints its title; it was v1.0, from the
  board's missing one. `pcbkit new` also escapes a title or company with quotes in the schematic.
- **A mockup's line names its DRC errors**, by type and part: `mockup (drc 2: 2 hole_clearance (USBC1))`.
  docs/CONVENTIONS.md says how to accept one: a measured `.kicad_dru` rule naming the part and the value, not a
  DRC exclusion; the pcb-kit skill's courtyard advice says the same.
- **`pcbkit release` chooses the version from what changed: minor for the netlist or BOM, patch for the layout.**
  It bumped the patch for any change unless given `--minor` or `--major`. It now reads each board's netlist and BOM
  from the .kicad_pcb's S-expressions, so a board KiCad 6 or 7 saved, indented with spaces, reads the same (every
  footprint but board-only ones, by reference: footprint, value, LCSC and MPN fields, DNP
  and exclude-from-BOM flags; each pin's net) and keeps a fingerprint of them per version in releases.toml
  (`circuit`, also in MANIFEST.txt). A board whose netlist or BOM differs from its last release's gets the next
  minor version, even with the same fab output (an MPN that only PCBWay's BOM would show, a hand-soldered part's
  value); one whose only difference is its fab output gets a patch. A net renamed, or a net of one pin, connects
  nothing new and doesn't count. The output, `--dry-run`'s too, says which part and what changed, read against the
  last release's `kicad/` copy (`minor after 1.0.0: R1 value 10k -> 4.7k; net VIN gains R2.2`), and the release's
  note in releases.toml keeps it. `--patch` (new) and `--minor` override the choice; `--major` stays yours to give,
  for a new header pinout, outline, mounting holes or GPIO map; `--version` now says what changed too. A last release
  recorded without the fingerprint gets a patch, and the output says why; a board unchanged since such a release
  gets it recorded on the next run ("recorded v1.3's netlist/BOM fingerprint"), since its circuit is the
  release's. A source recorded as equivalent before its release had the fingerprint counts as that release only
  while its netlist and BOM are the release's, in `pcbkit release` and `pcbkit production` both: an MPN that no JLC
  file shows made it an equivalent then, and makes it a minor now. docs/CONVENTIONS.md has the rule, and that a first
  release, which adopts the title block's version, needs `--version` when the circuit changed since that version was
  built.
- **More JLC rotations from the preview (2026-11-08).** KiCad's `WSON-6-1EP_2x2mm_P0.65mm` gets 270 and
  `SOT-583-8` 180: neither had a rule, and the preview put a TVS diode's pin 1 a quarter turn off (it would have
  shorted VBUS) and a buck converter's half a turn. The 6.3x7.7 mm polymer capacitor C46550459 gets 0 in place of
  the `CP_Elec_6.3x7.7` rule's 180, which put its + on the - pad. VSON-8, TDSON-8-1, SOT-23-6 (with C7843 and
  C55266), SSOP and TSSOP were right and are dated 2026-11-08. The placement checklist now says the preview's hover
  reading counts from the drill/place origin with Y up, as checked on that quote, and drops its advice to subtract
  the outline's corner.
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
- **`pcbkit import-easyeda` merges into the library instead of rewriting it**, so several boards can share one
  `--lib`/`--lib-name`. It wrote `<nick>.kicad_sym` afresh on every run, and the second board's import took the first
  board's symbols away. A symbol already in the library with the same name and pins is used as it is (also one saved
  by KiCad's symbol editor, but not one derived from another or split into units), and so is a footprint with the same
  name and pads (places, sizes, shapes, angles, holes, copper offsets from the hole, pad types and layers, paste and
  mask margins, the copper outline of custom pads such as EasyEDA's polygon pads, and the back and inner-layer copper
  of a pad whose padstack gives those layers other copper; the pads in any order, and a top or bottom placement at any
  angle compares as the library footprint); one with the same name and other pins or pads makes the new one `name_2`
  (the schematic's Footprint field follows); every other symbol and footprint stays. Importing a board again leaves
  the library as it was, byte for byte, also one KiCad's symbol editor saved last: the library's text is kept and new
  symbols go in among the old ones, in name order. Library footprints no longer carry the sheet file of the board they
  were copied from, and one copied from a bottom-side through-hole part on a board of more than two copper layers
  keeps copper on every layer (KiCad's flip had cut it to that board's inner layers). A schematic part with no
  footprint on the PCB no longer stops the run: it is listed as schematic-only and gets an empty Footprint field (ERC
  gave `footprint_link_issues` for a footprint no library had; DRC parity still lists it as `missing_footprint`).
  Sheet files drop the " copy" EasyEDA gives a duplicated sheet and are named in lower case with `-` (`ESP32+USB copy`
  is the sheet "ESP32+USB" in `esp32-usb.kicad_sch`, no longer `esp32+usb_copy.kicad_sch`).

### Fixed
- **`pcbkit netview` shows vias inside a pad**, drawing them over the pads instead of under: dodeca v1.3's BQ25890
  had five vias in its exposed pad that the view hid. It finds footprints by their outline rather than their origin,
  so a mounting hole whose origin lies outside the window is drawn, clips zone fills to the window (a 15 mm view of
  dodeca's control board went from 668 kB of SVG to 85 kB), draws arc tracks as arcs, and skips a zone that has
  no fill on the layer.
- **`pcbkit drc` and `pcbkit erc` no longer report an earlier run's result.** They read `kicad-cli`'s report from a
  fixed path in the temp folder, so a run on a board that doesn't exist, a file that won't load, or a `kicad-cli`
  killed mid-run printed the previous board's summary and could exit 0. The report is now removed before each run.
  A non-zero exit from `kicad-cli`, or no readable report, stops the command with exit status 1 and `kicad-cli`'s
  message. That also covers `pcbkit release`'s checks and `import-easyeda`'s.
- **`pcbkit drc` no longer stops with an IndexError on a board with a split pour.** Its "split zones" line took each
  zone's name from between quotes, and KiCad 10.0.7 describes an unnamed zone without them (`Zone [GND] on F.Cu,
  priority 0`); such a zone is now listed by that description.
- **`pcbkit fab` and `pcbkit release` stop when PCBWay's files are wanted and konnect's parts cache is missing**
  (`~/.konnect/jlcpcb.db`). PCBWay's BOM takes each part's manufacturer, and a part number its footprint lacks, from
  the cache; without it the BOM came out incomplete, and `pcbkit release --dry-run` on a new machine reported every
  changed board as new fab output (would release) where, with the cache, they had the same fab output as their last
  release. `pcbkit fab --no-assembly`, a release without `pcbway` in `[release] fab`, and `--refresh-parts` don't
  need it.
- **`pcbkit release` no longer gives a board a new version because a newer KiCad refills its zones a little
  differently.** KiCad 10.0.7 moves some zone-fill vertices by a nanometre, or drops a few that lie within a couple
  of micrometres of the outline, so two of synth-blocks' boards whose copper hadn't changed no longer matched their
  releases' fab fingerprints. When the fingerprint differs, release now makes the files again from a copy stamped as
  the last release was, and compares them with that release's folder: zone fills (Gerber regions) may lie within
  0.005 mm of the release's, KiCad's own maximum error for zone fills, every point of their outlines and not only
  points sampled along them (each differing edge is halved until a bound on its distance holds for all of it);
  everything else must be the same, as the fingerprint sees it. A match is recorded as equivalent, its note saying how far the fills moved. Stored
  fingerprints keep their meaning, and a board whose release folder doesn't hold its files gets a new version, as
  before.
- **`pcbkit check` no longer reports a via in the middle of a track as floating copper.** It joined a via to a track
  only where one of the track's ends lay on the via, so a via stitched into a straight run, as a scripted wide feed
  lays them on both layers, was floating, and so was any track hanging off it. A via now joins every track of its net
  on a layer it is on wherever its ring touches the track (centre within half the track width plus the via's
  radius), measured to an arc's curve rather than its chord, as is a track end on another track or arc. Stubs are
  found from track ends as before. On esp32-s3-ha-node's board the six floating VIN and +5V_BUCK
  groups are gone, which KiCad's DRC already saw as connected, and nothing else changed there or on the other corpus
  boards. The first fixture boards for `check` are in `tests/test_route_check.py`.
- **`pcbkit import-easyeda` no longer leaves what took hand fixes on espcandle-pcb's two boards.** The copper-pour
  keep-outs KiCad's importer makes on User.4 from EasyEDA's pin outlines (layer-100 cutouts in LCSC's SOT-23, SC-70
  and SOD-123 footprints) are removed from the board and the library footprints: they were 11 and 16 DRC
  `items_not_allowed` errors. Edge.Cuts ends less than 0.05 mm apart are joined (a 0.0127 mm gap failed DRC with
  `invalid_outline`); only ends that meet no other end move, so short segments keep their length. Pads without a
  number and NPTH pegs (a switch's, a USB-C socket's) are no longer "pad has no schematic pin", and a pad the symbol
  lacks that is unconnected on the PCB too (USB-C shell pads) is listed instead of stopping the run. A loose pad with
  a net, such as a GND test pad, becomes a board-only footprint out of the BOM and placement files and is listed; only
  a PCB part with nets and no symbol still stops the run. Both boards now import without `--force`. Fixtures in
  `tests/test_import_easyeda.py`.
- **`pcbkit import-easyeda` redraws the courtyards KiCad's importer gets wrong**, as `pcbkit courtyards --fix`
  does, before the footprints go into the library, and lists them. A footprint the library already has with the
  same pads, put there by an earlier import, gets its courtyard redrawn in the library too (the rest of it stays as
  it is), so updating the board from the library doesn't bring the old one back. `--keep-courtyards` only lists
  them. Each part's body is drawn on F.Fab/B.Fab from EasyEDA's component shapes first, on the board and in a
  library footprint an earlier import left without one, so the courtyards go round the body rather than the silk.

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
