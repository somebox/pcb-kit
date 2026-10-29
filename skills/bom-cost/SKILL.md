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

- **Source and scope:** the sync command, the date the prices were taken, what is excluded (kit parts, PCB; the assembly fees have their own section).
- **Cost drivers:** the ten most expensive lines, with LCSC code, part, refs, quantity, cost per board at 1 and 100.
- **Extended parts:** count them. Under Economic PCBA each unique Extended part costs a loading fee per order (about
  $3) and Basic parts load free, so list the cheap swaps there: a Basic value that shifts a threshold by a
  negligible amount, one LED colour instead of four. Under Standard every unique part pays the same fee, Basic or
  Extended (below), so a swap saves only the difference in part price.
- **Stock risks:** any line under a few thousand in stock, with the nearest substitute and what changes in the
  circuit if it is used.
- **Assembly estimate:** the per-order fees for the PCBA type and sides the order will use (below). For a handful of
  boards they outweigh the parts, so the cost per set isn't the parts alone.

## What JLC's assembly costs

Read off Orbitchron's quotes, 2026-10-26, 5 boards (its PRODUCTION.md, Costs). Re-read them from a fresh quote
before relying on them.

| Fee | Standard PCBA | Economic PCBA |
| --- | --- | --- |
| Setup | $25.75 per side | $8.24 |
| Stencil | $8.27 per side | $1.55 |
| Parts loading | $1.55 per unique part, Basic or Extended alike | $3.09 per unique Extended part; Basic free |
| X-ray | $8.25 on the driver, which has a module and a WSON | - |
| Limits | adds 5 mm edge rails | one side; green mask only; no Standard-only parts (ESP32-S3-MINI-1); drills two 1.152 mm tooling holes into a board without them |

At 5 boards, Orbitchron's double-sided driver came to $182.25 of assembly. $122.79 of that was fees, and $53.32 parts.

## Rules

- Stock and price numbers carry a date. Re-run `bomi list --check` before an order and update BOM.md.
- A substitute that needs a circuit change (a divider, inverted logic) goes into DESIGN.md as a decision, not just BOM.md.
- Basic parts first for passives (`pcbkit.parts` has the stock R/C table); Extended only where the function needs it.
- Hand-soldering pays only when it takes a whole side off the assembly order: setup, stencil and that side's loading.
  Parts left off a side JLC still assembles save only their loading fee (fab-order skill, What JLC assembles).
