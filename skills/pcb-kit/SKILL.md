---
name: pcb-kit
description: Shared KiCad workflow commands for the somebox hardware projects. Use when checking a board (DRC, ERC, routing hygiene, net topology), producing release files (previews, JLC Gerbers/BOM/CPL, KiKit panels), looking up JLC parts, or generating schematic sheets through konnect. Also the conventions for laying out and documenting a hardware project.
---

# pcb-kit

`pcbkit` is installed as a CLI (`uv tool install -e ~/src/pcb-kit`). `pcbkit --help` lists the commands,
`pcbkit <cmd> --help` the flags. Commands that need pcbnew re-run themselves under KiCad's Python; you never choose
the interpreter.

## When to use what

| Situation | Command |
| --- | --- |
| Is the schematic legal? | `pcbkit erc boards/x/x.kicad_sch` |
| Is the schematic what we meant? | run the project's netlist assertions (built on `pcbkit.checks.netlist.Netlist`); `pcbkit nets SCH NET...` to look |
| Is the board clean? | `pcbkit drc BOARD --save` (refills zones and writes the board back; 0 errors, 0 unconnected, 0 parity) |
| Anything DRC can't see? | `pcbkit check BOARD` (floating copper, stubs, loops, thin power feeds), `pcbkit cleanup BOARD --dry-run` |
| Review a power stage | `pcbkit netview BOARD out.svg X0 Y0 X1 Y1 --layer B.Cu --png out.png`, then look at the PNG |
| Update the docs' pictures | `pcbkit previews BOARD docs/previews/<board>` |
| Order files | `pcbkit fab BOARD build/fab/<board>`; panels: `pcbkit panel BOARD build/panels/<board> --style both` |
| Start a new project | `pcbkit new ~/Documents/Projects/<name> --board <b> --title "..."`, then write DESIGN.md (requirements, constraints, candidate parts with `pcbkit jlc` stock, decisions) before any schematic |
| Bring an EasyEDA design into KiCad | `pcbkit import-easyeda SCH.json PCB.json boards/<b> --name <b> --lib lib --lib-name <project>`; archive the JSON under `archive/vN-easyeda/`, record findings in DESIGN.md, then `bomi init` + `bomi import kicad` in the board folder |
| Find a part quickly | `pcbkit jlc --desc "%...%" --package "0402%" --basic`; for a researched BOM use bomi |

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
