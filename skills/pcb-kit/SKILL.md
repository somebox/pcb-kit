---
name: pcb-kit
description: Shared KiCad workflow commands for the somebox hardware projects. Use when checking a board (DRC, ERC, routing hygiene, net topology), producing release files (previews, JLC Gerbers/BOM/CPL, KiKit panels), looking up JLC parts, or generating schematic sheets through konnect. Also the conventions for laying out and documenting a hardware project.
---

# pcb-kit

`pcbkit` is installed as a CLI (`uv tool install -e ~/src/pcb-kit`). `pcbkit --help` lists the commands,
`pcbkit <cmd> --help` the flags. Commands that need pcbnew re-run themselves under KiCad's Python; you never choose
the interpreter. pcbkit runs kicad-cli from a background copy, so it shows no dock icon; a project script that runs
kicad-cli takes it from `pcbkit.kicad.KICAD_CLI` too, not from `/Applications/KiCad/...`.

## When to use what

| Situation | Command |
| --- | --- |
| Is the schematic legal? | `pcbkit erc boards/x/x.kicad_sch` |
| Is the schematic what we meant? | run the project's netlist assertions (built on `pcbkit.checks.netlist.Netlist`); `pcbkit nets SCH NET...` to look |
| Is the board clean? | `pcbkit drc BOARD --save` (refills zones and writes the board back; 0 errors, 0 unconnected, 0 parity) |
| Anything DRC can't see? | `pcbkit check BOARD` (floating copper, stubs, loops, thin power feeds), `pcbkit cleanup BOARD --dry-run` (after a scripted route: `--dangling` too) |
| Where should this block go? Which output drives which net? | `pcbkit place-score BOARD --study scripts/place_study.py --search --svg out.svg --png out.png`; see the placement-study skill; route only the winner |
| Route a board by script | a project script under KiCad's Python: `pcbkit.route.complete_nets(board, P, area, plan, track, via, outline=..)` with `plan = [(net regex, [layers], width)]` and `QUICK`, `CONN_SECONDS` and `DEADLINE` in `P` so a run has an end; then `snap_to_pad_centres`, `audit_track_ends`, `pcbkit cleanup --dangling`, `pcbkit drc --save`. Worked example: esp32-s3-ha-node `scripts/route_pcb.py`. Fine-pitch chips (0.4 to 0.65 mm) first get `fanout(board, ref, P, area, track, via, skip_nets=(power and plane nets))`: staggered dog-bone vias out from each signal pad. With only the outer layers for tracks, `P["DIR"] = {F_Cu: "h", B_Cu: "v"}` holds each to one direction (`DIR_COST` along, across, diagonal; default 1, 4, 2) and `P["ORDER"] = "longest"` routes the longest connections first; both are off unless set |
| Review a power stage | `pcbkit netview BOARD out.svg X0 Y0 X1 Y1 --layer B.Cu --png out.png`, then look at the PNG |
| Update the docs' pictures | `pcbkit previews BOARD docs/previews/<board>` |
| Order files | `pcbkit release` (every changed board gets its next version, stamped into it, and its JLC and PCBWay files in its release folder; `--dry-run` first), then an order in `production/orders/<date>-<what>/order.toml` pinning the versions and `pcbkit production`; upload from that order's `upload/`. Panels: `pcbkit panel BOARD build/panels/<board> --style both`. Files without a version: `pcbkit fab BOARD build/fab/<board>` |
| What to buy for hand assembly (panel parts, connectors, cables) | the order's ORDER.md, "Parts for hand assembly": its hand parts plus what pcbkit.toml's `[[production.part]]` rules add (`group` to give a connector both a part and a cable, `replace` for a part counted elsewhere, `buy` for where it comes from); after changing the rules, `pcbkit release --refresh-parts`, then `pcbkit production` |
| An order was placed, shipped, arrived or hit a problem (a stock shortage) | add an `[[event]]` to its order.toml (date, status, `pcb` when it is one board, `order_no`, `price`, `note`); boards and parts that arrived go in `production/stock.toml`; then `pcbkit production` and commit, so git has the history |
| Spares or a rework kit to buy with an order, or a board with its own fab settings | `[[extra]]` part, qty, buy, note in its order.toml; a board's own `fab = "..."` (a front panel's black mask); then `pcbkit production` |
| Start a new project | `pcbkit new ~/Documents/Projects/<name> --board <b> --title "..."`, then write DESIGN.md (requirements, constraints, candidate parts with `pcbkit jlc` stock, decisions) before any schematic |
| Fit a board to an enclosure | measure the box (calipers, or a photo against a ruler), write `boards/<b>/enclosure.json`, then `pcbkit outline boards/<b>/<b>.kicad_pcb boards/<b>/enclosure.json`; record the numbers and their accuracy in LAYOUT.md |
| Bring an EasyEDA design into KiCad | `pcbkit import-easyeda SCH.json PCB.json boards/<b> --name <b> --lib lib --lib-name <project>`; archive the JSON under `archive/vN-easyeda/`, record findings in DESIGN.md, then `bomi init` + `bomi import kicad` in the board folder |
| Which part did we use for this before? | `library/PARTS.md` in this kit; `pcbkit parts TEXT` shows its current stock |
| Find a part quickly | `pcbkit jlc --desc "%...%" --package "0402%" --basic`; for a researched BOM use bomi |
| Does a rail survive a load step, does reset trip? | `pcbkit spice deck.cir --gear-retry`; see the power-sim skill |
| Is it ready to order? What could fail once it's built? Where do test points go? | the failure-review skill: references, failure modes, tests, a briefing in `docs/reviews/`; no edits until the user has read it |

Related skills in this kit: **visual-review** (which picture answers which question), **power-sim** (ngspice),
**bom-cost** (bomi, stock, Extended fees), **enclosure-fit** (box to outline to paper test), **placement-study**
(score placements by their ratsnest, route only the winner), **failure-review** (what could fail after fab, how
we'd find it, test points).

## KiCad documentation links

When the user is to do or check something in KiCad themselves, or a result needs KiCad's own explanation (a DRC or
ERC finding, a rule, a setting, an export), offer the manual sections they should review as markdown links in the
reply, so they can open them from the chat. Link the section, not the whole manual; two or three that bear on the
task, not a list. The KiCad 10 manuals are at `https://docs.kicad.org/10.0/en/<manual>/<manual>.html#<anchor>`;
anchors that come up often:

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

## Rules

- Read the project's README, DESIGN.md and LAYOUT.md first; the status line at the top of each says where things stand.
- Never regenerate a routed board from scratch. Incremental edits only, keeping UUIDs.
- Locked copper is deliberate. Leave it.
- After any copper edit: `pcbkit cleanup`, `pcbkit drc --save`, `pcbkit check`, in that order.
- Before an order, follow the checklist in `docs/CONVENTIONS.md` of this kit and the project's PRODUCTION.md.
- Project-specific data (placement tables, net lists, power-stage geometry) belongs in the project, not in the kit.
  If you find yourself adding a board's names to the kit, stop and put them in the project.
- KiCad Python gotchas: `BOARD.Remove()` on tracks corrupts the bindings mid-run (edit the file text by uuid);
  `pcbnew.SaveBoard` aborts headless (use `PCB_IO_MGR.Save`); a `.kicad_dru` syntax error disables all custom rules.
