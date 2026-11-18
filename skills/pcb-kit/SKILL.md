---
name: pcb-kit
description: Shared KiCad workflow commands for the somebox hardware projects. Use when checking a board (DRC, ERC, routing hygiene, net topology), producing release files (previews, JLC Gerbers/BOM/CPL, KiKit panels), looking up JLC parts, or generating schematic sheets through konnect. Also the conventions for laying out and documenting a hardware project.
---

# pcb-kit

`pcbkit` is installed as a CLI (`uv tool install -e ~/src/pcb-kit`). `pcbkit --help` lists the commands, `pcbkit
<cmd> --help` the flags, for every command. Commands that need pcbnew re-run themselves under KiCad's Python; you
never choose the interpreter. pcbkit
runs kicad-cli from a background copy, so it shows no dock icon; a project script that runs kicad-cli takes it from
`pcbkit.kicad.KICAD_CLI` too, and a shell command from the path `pcbkit info` prints, not from
`/Applications/KiCad/...` or PATH.

## Rules

- Read the project's README, DESIGN.md and LAYOUT.md first; the status line at the top of each says where things stand.
- **Never regenerate a routed board from scratch.** Incremental edits only, keeping UUIDs.
- **Locked copper is deliberate. Leave it.**
- After any copper edit: `pcbkit cleanup`, `pcbkit drc --save`, `pcbkit check`, in that order.
- Before an order, follow "Checks before an order" in [docs/CONVENTIONS.md](../../docs/CONVENTIONS.md) and the
  project's PRODUCTION.md. A board is released against "What a released board carries", in the same file:
  `pcbkit standard` checks it, and `pcbkit release` refuses a board with one of its errors left. **Never waive a
  finding on your own.** A `[[standard.waive]]` entry is the user's decision; put the finding and a proposed note to
  them.
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
| Does the schematic read well? | `pcbkit sch-lint boards/x/x.kicad_sch`; `--help` lists what it finds. Then open the sheet in `docs/previews/<board>/schematic.pdf` and look: the check estimates text sizes |
| Generate a sheet that reads | `pcbkit.schematic`, from a project script: [schematic-sheets.md](schematic-sheets.md) |
| Is the schematic what we meant? | run the project's netlist assertions (built on `pcbkit.checks.netlist.Netlist`); `pcbkit nets SCH NET...` to look |
| Is the board clean? | `pcbkit drc BOARD --save` (refills zones and writes the board back; 0 errors, 0 unconnected, 0 parity) |
| Does the board carry what a released board must? | `pcbkit standard [PCB]`: errors for no `${REVISION}` on the silkscreen, a sheet without a title block, a placed part without an LCSC number; notes for a connector, jumper, switch or LED without a label, a rail without a test pad, a test pad without a name, a mounting hole without a courtyard or keep-out, an Extended part BOM.md does not name; then what only a person can check. Fix each, or ask the user whether to waive it with a note in pcbkit.toml (docs/CONVENTIONS.md, "What a released board carries") |
| Anything DRC can't see? | `pcbkit check BOARD` (floating copper, stubs, loops, thin power feeds, courtyards), `pcbkit cleanup BOARD --dry-run` (after a scripted route: `--dangling` too) |
| Can the courtyards be trusted for placement? (an imported board, a vendor's or a project library's footprints) | `pcbkit courtyards BOARD` (or `LIB.pretty`) before placing parts. `--fix` redraws them; on a board imported from EasyEDA add `--easyeda PCB.json` (`--help` says why) |
| A `courtyards_overlap` that DRC still reports | measure the gap between the two parts' pads and bodies. Under the assembler's minimum (JLC's "Minimum spacing for SMD components", 2026-09: 0.15-0.35 mm chip to chip, 0.2 chip to SOT, 0.4 chip to SOIC and SOT to SOT, 1.0 to a QFN) move one; over it, a `.kicad_dru` rule for the two parts, as for any accepted DRC error (below) |
| Where should this block go? Which output drives which net? | `pcbkit place-score BOARD --study scripts/place_study.py --search --svg out.svg --png out.png`; see the placement-study skill; route only the winner |
| Lay a board's signals again | `pcbkit reroute SETTINGS.json`; see the routing skill |
| Route a board by script | `pcbkit.route`, from a project script under KiCad's Python: [route-script.md](route-script.md) |
| Review a power stage | `pcbkit netview BOARD out.svg X0 Y0 X1 Y1 --layer B.Cu --png out.png`, then look at the PNG; to compare layout options, the visual-review skill |
| Update the docs' pictures | `pcbkit previews BOARD docs/previews/<board>` |
| Order files | `pcbkit release --dry-run` first: it says each changed board's next version and what changed. Then docs/CONVENTIONS.md, "Checks before an order", step 7: the stock read, `pcbkit release`, CHANGELOG.md, order.toml, `pcbkit production` |
| A release that needs the user's word | `--major` only when the user says the header pinout, outline, mounting holes or GPIO map changed. When a first release asks for `--version`, ask the user whether boards of that version were built (docs/CONVENTIONS.md, "Versions, releases and orders") |
| Panels; fab files without a version | `pcbkit panel BOARD build/panels/<board> --style both`; `pcbkit fab BOARD build/fab/<board>` |
| The version and date on the silkscreen | a board text with `${REVISION}` and `${ISSUE_DATE}`, never the version typed in; release refuses `v${REVISION}` and `${CURRENT_DATE}`. For `date_format` and `label` in pcbkit.toml: docs/CONVENTIONS.md, "Versions, releases and orders" |
| What changed since the last release, for CHANGELOG.md | `pcbkit changelog` drafts what changed (`--help` for `--since` and `--all`); add the why, as docs/CONVENTIONS.md, "Versions, releases and orders", lays out |
| A DRC error that is accepted (a vendor footprint as it was built) | measure it, then a rule in the board's `.kicad_dru` scoped to the part, not a DRC exclusion: docs/CONVENTIONS.md, "Checks before an order", step 3 |
| An earlier version's design, or two versions side by side | `releases/<board>/<label>/kicad/<board>.kicad_pro`, the release's frozen KiCad project: read only, since release folders are never rewritten (docs/CONVENTIONS.md, "Versions, releases and orders"). What changed since it: `pcbkit changelog BOARD --since <its .kicad_pcb>`. Any two boards' netlists and BOMs: `pcbkit.release.versions.circuit_changes(circuit(a), circuit(b))` |
| JLC's placement viewer, before the order is saved to the cart | the fab-order skill goes through the checklist beside the CPL (`PLACEMENT-<board>-<label>-jlcpcb.md`) in the viewer |
| What to buy for hand assembly (panel parts, connectors, cables) | the order's ORDER.md, "Parts for hand assembly". pcbkit.toml's `[[production.part]]` rules add to it (`pcbkit production --help`); after changing them, `pcbkit release --refresh-parts`, then `pcbkit production` |
| An order was placed, shipped, arrived or hit a problem (a stock shortage) | add an `[[event]]` to its order.toml (`pcbkit production --help` has the fields); boards and parts that arrived go in `production/stock.toml`; then `pcbkit production` and commit |
| Spares or a rework kit to buy with an order, or a board with its own fab settings | `[[extra]]` part, qty, buy, note in its order.toml; a board's own `fab = "..."` (a front panel's black mask); then `pcbkit production` |
| Silkscreen meant to print inside mask openings (numerals in a face panel's windows) | `keep_silk_in_mask = true` in the board's `[[release.profile]]` in pcbkit.toml (`pcbkit fab --keep-silk-in-mask` without a version), or the Gerbers lose it. Say so in the order remark too, or the fab house may clip it |
| Start a new project | `pcbkit new ~/Documents/Projects/<name> --board <b> --title "..."`, then write DESIGN.md (requirements, constraints, candidate parts with `pcbkit jlc` stock, decisions) before any schematic |
| Fit a board to an enclosure | the enclosure-fit skill (`pcbkit outline BOARD boards/<b>/enclosure.json`); record the numbers and their accuracy in LAYOUT.md |
| Bring an EasyEDA design into KiCad | `pcbkit import-easyeda SCH.json PCB.json boards/<b> --name <b> --lib lib --lib-name <project>` (see `--help`). Archive the JSON under `archive/vN-easyeda/`, record findings in DESIGN.md, then bomi (the bom-cost skill, "Commands"). A courtyard larger than its part stays, so look at a netview before placing tight |
| Which part did we use for this before? | `library/PARTS.md` in this kit; `pcbkit parts TEXT` shows its current stock |
| Find a part quickly | `pcbkit jlc --desc "%...%" --package "0402%" --basic`; for a researched BOM use bomi (the bom-cost skill) |
| Does a rail survive a load step, does reset trip? | `pcbkit spice deck.cir --gear-retry`; see the power-sim skill |
| Is it ready to order? What could fail once it's built? Where do test points go? | the failure-review skill: a briefing in `docs/reviews/`, and **no edits until the user has read it** |
| A major design round is done (a review and its decisions, a re-layout, a release, an order) | the project-overview skill (`pcbkit overview`): bring `docs/overview.html` up to date and show it to the user (docs/CONVENTIONS.md, "The overview page") |
| Put an order into the carts | last and once, after the user has signed off the order's diff: the fab-order skill. A change to a released board is its next version and the order's steps again |

## KiCad documentation links

When the user is to do or check something in KiCad themselves, or a result needs KiCad's own explanation (a DRC or
ERC finding, a rule, a setting, an export), give the two or three manual sections that bear on it as markdown links
in the reply: the section, not the whole manual. Take the links from [kicad-links.md](kicad-links.md): the anchors
that come up often, the manuals' address, and how to find one that is not listed. Never guess an anchor.
