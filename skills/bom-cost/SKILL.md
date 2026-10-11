---
name: bom-cost
description: Keep a board's BOM, stock and cost honest with bomi and the JLC parts cache. Use when choosing parts, checking stock before an order, finding what drives the cost, or writing BOM.md for a project.
---

# BOM, stock and cost

Two tools: `pcbkit jlc` for quick look-ups in konnect's JLCPCB cache (no network), and **bomi** for the project BOM
(a `.bomi/` project per board folder, synced from the KiCad schematic, refreshed from the JLC/LCSC API).

## Start from what worked

`library/PARTS.md` in the kit lists the parts that have been on built boards, with their circuit notes and the
lessons from them. Check it before searching, and run `pcbkit parts` for their current stock. When a new part has
been built and works, add it there with the project and what to watch for; parts in a design that isn't built yet go
in its second section.

## Commands

```bash
pcbkit jlc --desc "%buck%" --package "SOT-23%" --basic     # first look, local cache
bomi init                                                   # once, in the board folder
bomi import kicad boards/<b>/<b>.kicad_sch --replace        # sync LCSC numbers from the schematic
bomi list --check                                           # refresh stock and price, flag problems
bomi info U201                                              # one part, by designator or LCSC code
bomi compare C2913202 C2913201                              # candidates side by side
bomi datasheet C9864 --summary                              # PDF plus a markdown summary for docs/datasheets/
```

## BOM.md

Write it from bomi's output, dated, in the board's project. The sections that have earned their place:

- **Source and scope:** the sync command, the date the prices were taken, what is excluded (kit parts, PCB, assembly fees).
- **Cost drivers:** the ten most expensive lines, with LCSC code, part, refs, quantity, cost per board at 1 and 100.
- **Extended parts:** JLC charges a fee per unique Extended part per order (about $3). Count them, and list the
  cheap swaps: a Basic value that shifts a threshold by a negligible amount, one LED colour instead of four.
- **Stock risks:** any line under a few thousand in stock, with the nearest substitute and what changes in the
  circuit if it is used.

## Rules

- Stock and price numbers carry a date. Re-run `bomi list --check` before an order and update BOM.md.
- A substitute that needs a circuit change (a divider, inverted logic) goes into DESIGN.md as a decision, not just BOM.md.
- Basic parts first for passives (`pcbkit.parts` has the stock R/C table); Extended only where the function needs it.
