---
name: pcb-kit
description: Shared KiCad workflow commands for the somebox hardware projects. Use when checking a board (DRC, ERC, routing hygiene, net topology), producing release files (previews, JLC Gerbers/BOM/CPL, KiKit panels), looking up JLC parts, or generating schematic sheets through konnect. Also the conventions for laying out and documenting a hardware project.
---

# pcb-kit

`pcbkit` is installed as a CLI (`uv tool install -e ~/src/pcb-kit`). `pcbkit --help` lists the commands, `pcbkit
<cmd> --help` the flags (not for `info`, `link`, `setup-kikit` or `setup-router`: those four act at once, `--help`
or not). Commands that need pcbnew re-run themselves under KiCad's Python; you never choose the interpreter. pcbkit
runs kicad-cli from a background copy, so it shows no dock icon; a project script that runs kicad-cli takes it from
`pcbkit.kicad.KICAD_CLI` too, and a shell command from the path `pcbkit info` prints, not from
`/Applications/KiCad/...` or PATH.

## Rules

- Read the project's README, DESIGN.md and LAYOUT.md first; the status line at the top of each says where things stand.
- **Never regenerate a routed board from scratch.** Incremental edits only, keeping UUIDs.
- **Locked copper is deliberate. Leave it.**
- After any copper edit: `pcbkit cleanup`, `pcbkit drc --save`, `pcbkit check`, in that order.
- Before an order, follow "Checks before an order" in [docs/CONVENTIONS.md](../../docs/CONVENTIONS.md) and the
  project's PRODUCTION.md. A board is released against "What a released board carries", in the same file.
- Depth and scope: the same file, "Depth and scope". A row of the table below is the smallest pass for its
  question. Say a job's scope and depth before starting, and name each step left out when reporting.
- Project-specific data (placement tables, net lists, power-stage geometry) belongs in the project, not in the kit.
- Before a script changes a board through pcbnew, read "Scripting rules" in `docs/CONVENTIONS.md`: removing an item
  or saving the usual way breaks the run. A `.kicad_dru` syntax error disables every custom rule, so run DRC after
  each edit of it.

## When to use what

| Situation | Command |
| --- | --- |
| Is the schematic legal? | `pcbkit erc boards/x/x.kicad_sch` |
| Does the schematic read well? | `pcbkit sch-lint boards/x/x.kicad_sch` (every sheet below it): texts on top of each other or on another symbol, wires through text, a reference or value far from its part, and small local nets (2 to 4 pins, not leaving the sheet) joined by labels where a wire would show the circuit. `--summary` for the counts per sheet. Then open the sheet in `docs/previews/<board>/schematic.pdf` and look: the check estimates text sizes |
| Generate a sheet that reads | Follow [schematic-sheets.md](schematic-sheets.md): each call with its example and what it draws, from placing the parts as blocks to the render to look at |
| Is the schematic what we meant? | run the project's netlist assertions (built on `pcbkit.checks.netlist.Netlist`); `pcbkit nets SCH NET...` to look |
| Is the board clean? | `pcbkit drc BOARD --save` (refills zones and writes the board back; 0 errors, 0 unconnected, 0 parity) |
| Anything DRC can't see? | `pcbkit check BOARD` (floating copper, stubs, loops, thin power feeds, courtyards), `pcbkit cleanup BOARD --dry-run` (after a scripted route: `--dangling` too) |
| Can the courtyards be trusted for placement? (an imported board, a vendor's or a project library's footprints) | `pcbkit courtyards BOARD` or `LIB.pretty`: none, on the other side from the pads, pads or body outside; `--fix` redraws them round pads and body + 0.25 mm; on a board imported from EasyEDA add `--easyeda PCB.json`, or the body is EasyEDA's silk, drawn well off it. Run it before placing parts: DRC's courtyard checks are only as good as the courtyards |
| A `courtyards_overlap` that DRC still reports | measure the gap between the two parts' pads and bodies; under the assembler's minimum (JLC's "Minimum spacing for SMD components", 2026-09: 0.15-0.35 mm chip to chip, 0.2 chip to SOT, 0.4 chip to SOIC and SOT to SOT, 1.0 to a QFN) move one, over it a `.kicad_dru` rule for the two parts with the gap in its name, as for any accepted DRC error (below) |
| Where should this block go? Which output drives which net? | `pcbkit place-score BOARD --study scripts/place_study.py --search --svg out.svg --png out.png`; see the placement-study skill; route only the winner |
| Lay a board's signals again | `pcbkit reroute SETTINGS.json`: its power copper and ground stay, every signal net is routed together, and the result lands beside the board; see the routing skill |
| Route a board by script | a project script under KiCad's Python: `pcbkit.route.complete_nets(board, P, area, plan, track, via, outline=..)` with `plan = [(net regex, [layers], width)]` and `QUICK`, `CONN_SECONDS` and `DEADLINE` in `P` so a run has an end; then `snap_to_pad_centres`, `audit_track_ends`, `pcbkit cleanup --dangling`, `pcbkit drc --save`. Worked example: esp32-s3-ha-node `scripts/route_pcb.py`. Fine-pitch chips (0.4 to 0.65 mm) first get `fanout(board, ref, P, area, track, via, skip_nets=(power and plane nets))`: staggered dog-bone vias out from each signal pad. With only the outer layers for tracks, `P["DIR"] = {F_Cu: "h", B_Cu: "v"}` holds each to one direction (`DIR_COST` along, across, diagonal; default 1, 4, 2) and `P["ORDER"] = "longest"` routes the longest connections first; both are off unless set |
| Review a power stage | `pcbkit netview BOARD out.svg X0 Y0 X1 Y1 --layer B.Cu --png out.png`, then look at the PNG; `--detail` for mask, paste and via holes in paste; to choose between layout options, `--compare` their boards on one sheet (see the visual-review skill) |
| Update the docs' pictures | `pcbkit previews BOARD docs/previews/<board>` |
| Order files | `pcbkit release --dry-run` first: it says each changed board's next version and what changed (minor when the netlist or BOM changed, patch when only the layout did), and a `mockup` line names the DRC errors that stop it. Then `pcbkit release` (the version stamped into the board, its JLC and PCBWay files in its release folder), the version's section in the project's CHANGELOG.md, an order in `production/orders/<date>-<what>/order.toml` pinning the versions, and `pcbkit production`; upload from that order's `upload/` |
| A release that needs the user's word | `--major` only when the user says the header pinout, outline, mounting holes or GPIO map changed. A first release whose title block names a version needs `--version`: ask the user whether boards of that version were built, with what the output says git knows, and give the next version if they were and the board changed since |
| Panels; fab files without a version | `pcbkit panel BOARD build/panels/<board> --style both`; `pcbkit fab BOARD build/fab/<board>` |
| The version and date on the silkscreen | a board text with `${REVISION}` and `${ISSUE_DATE}` (`${ISSUE_DATE} ${REVISION}`), which KiCad fills from the title block `pcbkit release` stamps; never the version typed in, never `v${REVISION}` (prints vv1.0) or `${CURRENT_DATE}` (the day of the plot): release refuses both. `[release] date_format = "%m.%Y"` in pcbkit.toml for 11.2026, `label = "full"` for v1.4.0 instead of v1.4 (docs/CONVENTIONS.md says when) |
| What changed since the last release, for CHANGELOG.md | `pcbkit changelog` drafts it from the boards: the netlist and BOM changes, then the layout's, since each board's last release; `--since archive/<as-built>.kicad_pcb` or a git commit for a board never released (without it, the commit where its title block first read its version); `--all` for each release against the one before. Write the entries with the why, citing DESIGN.md's decisions (D12) and review findings (F27), one section per version as docs/CONVENTIONS.md lays out, and say they were checked against the draft |
| A DRC error that is accepted (a vendor footprint as it was built) | measure it, then a rule in the board's `.kicad_dru` scoped to the part (`A.memberOfFootprint('USBC1')`), its limit just under the measured value, its name and comment saying the part, the value and why; not a DRC exclusion. docs/CONVENTIONS.md, "Checks before an order", has ESP Candle's |
| An earlier version's design, or two versions side by side | `releases/<board>/<label>/kicad/<board>.kicad_pro` (`{folder}/fab/<label>/kicad/` in a series): the release's frozen KiCad project, which opens on its own; read only, since release folders are never rewritten. Its MANIFEST.txt lists any 3-D model or library it doesn't find. What changed since a version: `pcbkit changelog BOARD --since releases/<board>/<label>/kicad/<board>.kicad_pcb`; between each release and the one before, `--all`; any two boards' netlists and BOMs, `pcbkit.release.versions.circuit_changes(circuit(a), circuit(b))` in Python |
| JLC's placement viewer, before the order is saved to the cart | the checklist beside the CPL, `PLACEMENT-<board>-<label>-jlcpcb.md` in the order's `upload/<board>/` (`PLACEMENT-<board>.md` from `pcbkit fab`): every part whose orientation matters, with its rotation rule and when that rule was last seen right. The fab-order skill goes through it in the viewer |
| What to buy for hand assembly (panel parts, connectors, cables) | the order's ORDER.md, "Parts for hand assembly": its hand parts plus what pcbkit.toml's `[[production.part]]` rules add (`group` to give a connector both a part and a cable, `replace` for a part counted elsewhere, `buy` for where it comes from); after changing the rules, `pcbkit release --refresh-parts`, then `pcbkit production` |
| An order was placed, shipped, arrived or hit a problem (a stock shortage) | add an `[[event]]` to its order.toml (date, status, `pcb` when it is one board, `order_no`, `price`, `note`); boards and parts that arrived go in `production/stock.toml`; then `pcbkit production` and commit, so git has the history |
| Spares or a rework kit to buy with an order, or a board with its own fab settings | `[[extra]]` part, qty, buy, note in its order.toml; a board's own `fab = "..."` (a front panel's black mask); then `pcbkit production` |
| Silkscreen meant to print inside mask openings (numerals in a face panel's windows) | `keep_silk_in_mask = true` in the board's `[[release.profile]]` in pcbkit.toml, so `pcbkit release` keeps it (`pcbkit fab --keep-silk-in-mask` without a version); otherwise the Gerbers lose it. Say so in the order remark too, or the fab house may clip it |
| Start a new project | `pcbkit new ~/Documents/Projects/<name> --board <b> --title "..."`, then write DESIGN.md (requirements, constraints, candidate parts with `pcbkit jlc` stock, decisions) before any schematic |
| Fit a board to an enclosure | the enclosure-fit skill: measure the box (calipers, or a photo against a ruler), write `boards/<b>/enclosure.json`, then `pcbkit outline boards/<b>/<b>.kicad_pcb boards/<b>/enclosure.json`; record the numbers and their accuracy in LAYOUT.md |
| Bring an EasyEDA design into KiCad | `pcbkit import-easyeda SCH.json PCB.json boards/<b> --name <b> --lib lib --lib-name <project>` (every board of the project into the one library); archive the JSON under `archive/vN-easyeda/`, record findings in DESIGN.md, then `bomi init --name <b>` + `bomi import kicad <b>.kicad_sch` in the board folder. The import redraws the courtyards that miss pads or the body and lists them; one larger than the part stays, so look at a netview before placing tight. It fetches each part's 3-D model from EasyEDA into `lib/3dmodels/` and lists any it couldn't fetch or place (`--no-fetch` when offline) |
| Which part did we use for this before? | `library/PARTS.md` in this kit; `pcbkit parts TEXT` shows its current stock |
| Find a part quickly | `pcbkit jlc --desc "%...%" --package "0402%" --basic`; for a researched BOM use bomi (the bom-cost skill) |
| Does a rail survive a load step, does reset trip? | `pcbkit spice deck.cir --gear-retry`; see the power-sim skill |
| Is it ready to order? What could fail once it's built? Where do test points go? | the failure-review skill: a briefing in `docs/reviews/`, and **no edits until the user has read it** |
| A major design round is done (a review and its decisions, a re-layout, a release, an order) | the project-overview skill (`pcbkit overview`): bring `docs/overview.html` up to date and show it to the user (docs/CONVENTIONS.md, "The overview page") |
| Put an order into the carts | last and once, after the user has signed off the order's diff (versions, cart lines, prices, stock read that day, no design change open): the fab-order skill. A change to a released board is its next version (a patch at least) and the order's steps again |

## KiCad documentation links

When the user is to do or check something in KiCad themselves, or a result needs KiCad's own explanation (a DRC or
ERC finding, a rule, a setting, an export), give the two or three manual sections that bear on it as markdown links in
the reply: the section, not the whole manual. The KiCad 10 manuals are at
`https://docs.kicad.org/10.0/en/<manual>/<manual>.html#<anchor>`; anchors that come up often:

| Topic | Link |
| --- | --- |
| DRC: what each check means | [List of DRC checks](https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html#list-of-drc-checks), [schematic parity](https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html#schematic_parity_drc_checks) |
| Board Setup: rules, net classes, severities | [constraints](https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html#board-setup-constraints), [net classes](https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html#board-setup-net-classes), [violation severity](https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html#board-setup-violation-severity) |
| Custom rules (`.kicad_dru`) | [custom design rules](https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html#custom_rules) |
| Zones and fills | [working with zones](https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html#working-with-zones) |
| Fab outputs | [Gerber and fabrication output](https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html#gerber-and-fabrication-output), [drill files](https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html#drill-files), [position files](https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html#position-files) |
| Title block, text variables | [title block](https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html#sheet-title-block), [text variables](https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html#board-setup-text-variables) |
| ERC and the pin conflict map | [ERC](https://docs.kicad.org/10.0/en/eeschema/eeschema.html#erc), [severities and pin map](https://docs.kicad.org/10.0/en/eeschema/eeschema.html#erc_violation_severity_and_pin_conflicts_map) |
| Symbol fields, hierarchy | [symbol fields table](https://docs.kicad.org/10.0/en/eeschema/eeschema.html#symbol-fields-table), [hierarchical schematics](https://docs.kicad.org/10.0/en/eeschema/eeschema.html#hierarchical-schematics) |
| What pcbkit runs through kicad-cli | [pcb drc](https://docs.kicad.org/10.0/en/cli/cli.html#pcb_drc), [sch erc](https://docs.kicad.org/10.0/en/cli/cli.html#schematic_erc), [gerbers](https://docs.kicad.org/10.0/en/cli/cli.html#pcb_export_gerber), [render](https://docs.kicad.org/10.0/en/cli/cli.html#pcb_render) |

For a topic not listed, find the section in the manual's contents (fetch the page and look for its `id="..."`)
rather than guessing an anchor.
