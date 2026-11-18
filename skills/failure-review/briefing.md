# The briefing

The reader should be able to decide from the first screen. The reasoning stays in the design notes and sim results
it links to.

```markdown
# <Board> <rev>: failure review

Status (<date>): reviewed at <commit>; <full review | changes since <review> | schematic only | layout only |
one block | imported or built design>, <a look | as written | deeper: what was added>; <prototype, used with
<supply, handling> | product>; covers <blocks>; not covered: <blocks, and steps left out with why>; open checks:
<...>; shares circuits with: <boards>.
Checks at this commit: ERC <n>; DRC <errors, unconnected, parity>; assertions <n nets, n failures>;
KiCadRoutingTools' checkers <ran | not run, why>. Second pass: <what tried to refute the findings>.
Evidence marks: *ds* datasheet text (document, section, column), *sim*, *calc*, *board* (netlist or layout,
measured), *meas* (measured on a built unit), *inferred*, *recalled* (not checked).

## Verdict
Three to five lines: ready to order or not, the findings to settle first, and any check still open that could
kill the board.

## Findings
| # | Finding | Effect | Likelihood | Found at | Evidence | How it shows up, how to tell | Action (cost) |
|---|---|---|---|---|---|---|---|
| F1 | ... | dead | likely | bench | *ds* TPS2116 SLVSFG1A §7.6.1; *sim* sim/results/<question>.txt | symptom; reading at TP3 | change / test point / firmware / bench test / accept for this build |

## Earlier findings
In a review of changes: each finding of the earlier review, and whether it is fixed (by which decision), carried
over, accepted or open.

## Verified OK
What was checked and holds, with its source.

## Refuted
What the second pass removed, and the source that removed it.

## Firmware duties
Each state of the hardware that depends on firmware, as a requirement for whoever writes it.

## Test points and rework provisions
| Net or part | Why | Where | Footprint |

## Bench list
The first power-up, then the tests that settle the open findings, in order. For each: where to probe, the reading
that passes and the one that fails, and what to lift or cut to isolate the block.

## Notes, stock and tools
What isn't a failure mode: design notes that disagree with the board, parts low in stock, a tool that misled.

## Questions for the user

## Not covered

## Decisions (<date>)
Added after the user decides: | Finding | Decision | State |

## Bench results (<date>)
Added when the boards are built.
```

- **Effect:** *dead* (no function, or damage), *function lost* (the rest works), *degraded* (out of spec, or works
  with a workaround), *nuisance*. **Likelihood:** *will*, *likely*, *possible*, *unlikely*. **Found at:**
  *power-up* (the first power-up shows it), *bench* (a test on the bench list shows it), *field* (only time, a rare
  event or a part at its tolerance corner shows it).
- Order the findings by what to settle first. Weigh effect and likelihood together: a *function lost* that *will*
  happen goes above a *dead* that is *unlikely*. Of two alike, the one found only in the field goes first. A
  finding without evidence is a question and goes under "Questions for the user".
- Number the findings F1, F2, … and carry on from the last number in a later round, so a number means one finding
  for the life of the board. Use no other prefix: R6 would read as a resistor.
- Each action names its cost: a pad, a part, a re-route, a firmware duty, or a bench test before deciding. On a
  prototype the cheapest action that holds is often a condition written down (SKILL.md, "Prototype or product").
- "Not covered" is for what was left out on purpose: blocks out of scope, and the steps a look leaves out. A check
  that belongs to the review and wasn't done (a connector's footprint against its drawing, the placement viewer) is
  an open check: it goes into the status line, and into the verdict when it could kill the board. Orbitchron's first
  review listed connector mating under "Not covered", and both board-to-board footprints turned out mirrored
  (checklist.md, "Connectors and anything that leaves the board").
- Once sent, the briefing is a record. Decisions and bench results are added below, dated, and a later round is a
  new dated file that links it. Nothing is renumbered or rewritten in place.
