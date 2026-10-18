---
name: failure-review
description: "Validate a board before it is ordered: check every block against its datasheets, reference designs and what earlier boards taught, list what could fail after the boards come back and how each failure would show up and be diagnosed, test the doubtful cases by calculation or simulation, plan test points and rework provisions, and write a briefing with evidence before anything is changed. Use when the user asks to validate or review a design, asks what might go wrong or whether a board is ready to order, when planning bring-up or test points, and when a built board has failed and needs a cause."
---

# Failure review

ERC and DRC say the design is legal and the copper matches the schematic. They don't see a divider that sits on a
threshold at the tolerance corners, an input that powers a chip whose rail is off, or a charger that passes its
input straight through in one of its modes. This review looks for those, before the order, and plans how each would
be found on the bench.

Worked examples:
- dodeca control board v1.3, `~/src/dodeca-v2-hardware/hardware/control-board/v1.3/POWER_REVIEW.md`: two review
  rounds, simulations in `sim/`, a bench list. A simulation found a test that would have read wrong on a good board,
  and the part was changed before the order (SPEC D20).
- siebenuhr controller-v2, `~/Documents/Projects/siebenuhr/controller-v2/docs/failure-analysis-pd-shutdown.md`: a
  unit that failed in the field. Ranked hypotheses, each with the test that tells it apart, updated as results came in.

**Read-only until the user has the briefing.** Gather, calculate and simulate, but don't edit the schematic, the
board or the design data the board is generated from. Changes follow the user's choice among the recommendations.

## Steps

1. **Scope and ground truth.** Board, revision and commit; which blocks are in scope. Read the status lines of
   README, DESIGN, LAYOUT and PRODUCTION, DESIGN's "what the last version taught us", earlier reviews, and the
   `library/PARTS.md` notes for each part on the board. Run `pcbkit erc`, `pcbkit drc`, the project's netlist
   assertions and `pcbkit nets SCH`. Review the netlist as exported, not the design notes' account of it: they drift.
2. **Gather references.** For every IC: the datasheet and its revision (`docs/datasheets/`, or `bomi datasheet
   LCSC --summary`), the vendor's evaluation-board guide (its schematic, layout and BOM are the reference design),
   the application notes the datasheet points to, and errata. Then designs like this one: earlier versions in
   `archive/`, other projects with the same LCSC code (`grep -l C3235557 ~/Documents/Projects/*/boards/*/*.kicad_sch
   ~/Documents/Projects/*/*/boards/*/*.kicad_sch`), open designs using the part (GitHub, "<part> kicad"), and the
   vendor's forum (TI E2E and the like), where other people's failures with the part are written up. Record each
   source with its document number, so a finding can cite the section.
3. **Check each block.** Pin by pin for every IC: each pin's net against the datasheet's pin table, strap and
   config pins at the level meant, nothing floating that needs a level. Then the values against the datasheet's
   equations and the reference design, and the layout against the datasheet's layout guidance (`pcbkit netview`
   for each power stage, see visual-review). [checklist.md](checklist.md) lists what to look at per kind of block.
   A deviation from the reference design is fine when the design notes say why; otherwise it is a finding. Write
   down what holds as well: the "verified OK" list tells the reader what was covered.
4. **List the failure modes.** For each block, go through the board's states: unpowered, power-up, each
   combination of sources, hot-plug and unplug, brown-out, a fault downstream, no firmware or firmware stuck, and
   months of use in the field. For each failure: the cause, the effect, how it would show up on the bench, and what
   tells it apart from the failures that look the same. A failure we couldn't tell from its neighbours is a finding
   of its own, and usually wants a test point.
5. **Test the doubtful ones.** Write the question and the pass/fail number first, then answer it the cheapest way:
   - worst case by calculation, with resistor tolerance, reference spread, the datasheet's min and max (not typ),
     MLCC capacitance at its DC bias, and temperature: a short script that prints a table, in `sim/` or a
     scratch directory;
   - simulation for anything with timing (load steps, start-up, hand-over, inrush): the power-sim skill;
   - topology from the netlist, written as a netlist assertion when it encodes a failure mode, so it stays fixed;
   - layout from a netview of the region, and KiCadRoutingTools' checkers;
   - firmware duties from the firmware's code where it exists (siebenuhr's had no current cap and restored full
     brightness at boot).
   Mark each result as checked (datasheet text, simulation, measurement) or inferred. Anything recalled rather than
   looked up says so until it is checked.
6. **Plan debug access** for each failure mode that matters: where it would be measured, and what would be done if
   it turns out real (below).
7. **Verify before writing.** Re-read every datasheet claim in the findings against the source text. Round 2 of
   the dodeca review corrected round 1 on when the charger sets BATFET_DIS. When the harness runs sub-agents, give
   each one a block with its datasheet and netlist excerpt, then have a separate pass try to refute each finding.
8. **Write the briefing** (below) as `docs/reviews/<date>-<board>.md` in the project, send it to the user, and stop.
9. **After the user decides,** each accepted change becomes a numbered decision in DESIGN.md, then the edits,
   followed by `pcbkit erc`, the assertions, `pcbkit cleanup`, `pcbkit drc --save` and `pcbkit check`. Test points go
   into LAYOUT.md, the bench list into PRODUCTION.md's bring-up section. When the boards are built, add what the
   bench found to the briefing, dated, and the parts' lessons to `library/PARTS.md`.

## The briefing

The reader should be able to decide from the first screen. The reasoning stays in the design notes and sim results
it links to.

```markdown
# <Board> <rev>: failure review

Status (<date>): reviewed at <commit>; covers <blocks>; not covered: <...>.

## Verdict
Three to five lines: ready to order or not, and the findings that should change first.

## Findings
| # | Finding | Effect | Likelihood | Evidence | How it shows up, how to tell | Action |
|---|---|---|---|---|---|---|
| F1 | ... | dead | likely | TPS2116 SLVSFG1A §7.6.1; sim/results/<question>.txt | symptom; reading at TP3 | change / test point / firmware / bench test / accept |

## Verified OK
What was checked and holds, with its source.

## Test points and rework provisions
| Net or part | Why | Where | Footprint |

## Bench list
The first power-up, then the tests that settle the open findings, in order.

## Questions for the user

## Not covered
```

- **Effect:** *dead* (no function, or damage), *function lost* (the rest works), *degraded* (out of spec, or works
  with a workaround), *nuisance*. **Likelihood:** *will*, *likely*, *possible*, *unlikely*.
- Order the findings by effect, then likelihood. A finding without evidence is a question and goes in that section.
- Each action names its cost: a pad, a part, a re-route, a firmware duty, or a bench test before deciding.

## Test points and rework provisions

The boards come back assembled from JLC, so isolating a block or trying another value means rework. Plan for it
while it costs a pad:

- **Measure.** A test pad on every rail, and on each signal a failure is diagnosed by: reset, enable, strap,
  power-good, fault, sense nodes, a switch node for the scope. A GND pad close to each, for a probe's ground
  spring. Pads at both ends of a high-current path measure its drop (siebenuhr v2.2's TP7 and TP14 across R17 and
  the shunt). The corpus uses `TestPoint:TestPoint_Pad_D1.0mm` (siebenuhr, bottom side) and `TestPoint_Pad_D1.5mm`
  (esp32-s3-ha-node); a `TestPoint_Loop_*` takes a scope's ground clip. Put them on the side that can be reached
  with the board in its enclosure, with the net name on silk.
- **Isolate.** A 0 Ω link (0603 or larger, so it can be lifted with an iron) or a bridged solder jumper
  (`Jumper:SolderJumper-2_P1.3mm_Bridged_*`) in series with each rail into a block, so a short can be found by
  elimination and a block's current measured. Keep its trace on an outer layer where a knife can reach.
- **Override.** Strap, enable and mode pins through a resistor to their level, not straight to a rail, so a wire
  can override them. siebenuhr's CH224K had CFG1 on a 0 Ω link to the connector's VBUS.
- **Options.** DNP footprints for the alternative a finding points to: dodeca v1.3 has R209 (DNP) for leakage on
  QON, JP601 (DNP) to let the INA226 alert cut the LED rail, and C402 to remove if the BATFET trips on wake.
- **Reach.** A spare GPIO on a pad near the circuit most likely to need firmware help. Any net under a QFN or BGA
  that might need a wire, brought out to a pad or via. The debug UART, boot and reset pins on pads. The I2C bus on
  a header, so the chips can be read without the product firmware: v1.2's charger fault was found by a register
  dump.
- **Pins with a role.** A pad on a pin that must float or that the ROM drives at boot is not neutral. dodeca v1.3
  removed v1.2's TP_SCL2/TP_SDA2 from Teensy pins 24/25 for that reason.

A pad costs nothing and a 0 Ω is a Basic part. Room and routing are the limits on a dense board; the briefing says
which provisions were left out and why.

## When a built board has failed

The same steps, starting from the symptom; the siebenuhr analysis is the model.

1. Write down the report as given and what is known of the unit: variant, firmware, supply, how long it ran.
2. Rank the hypotheses, each with what speaks for it, what speaks against it, and the test that separates it from
   the others.
3. Cheapest tests first: questions to the owner and firmware-only tests before the bench, unpowered resistance
   checks (rails to GND, against a good board) before power, a current-limited supply before a real one.
4. Update the document as results come in, dated. Keep a ruled-out hypothesis with the result that ruled it out.

## What earlier reviews and bring-ups found

- **A mode the typical application doesn't show.** dodeca v1.2's BQ25895 passed VBUS to PMID while charging, so
  more than 5.5 V on VIN reached the LEDs. Read how the power path works in every mode, not only the front page.
- **A divider on a threshold.** v1.2's TS network sat close to the charger's NTC fault thresholds, and REGN, which
  fed it, moved with the charger's mode: false NTC faults stopped charging, and it was bodged to a fixed resistor.
  Check thresholds at the corners, from the reference that actually feeds them.
- **A detector that fired on good boards.** v1.2's uncut-Teensy detector also tripped on correctly cut boards and
  was bypassed with a bodge wire, using C18's pad for GND. v1.3's replacement was simulated in both states first,
  which is how its false result was found.
- **Load beyond what the source agreed to.** siebenuhr asked its PD supply for 5 V, where a contract allows 3 A,
  and drove 476 LEDs (7.6–10.5 A at full brightness) with no firmware cap. Budget the current against the weakest
  source the board accepts.
- **A part above its rating for months.** siebenuhr's 0805 0 Ω link, rated 2 A, carried the whole LED current.
- **Unprotected pins at the connectors.** CC, VBUS, LED data and Qwiic with no ESD part or series resistor, on
  boards that get hot-plugged and run long cables.
- **Back-powering.** HCT inputs clamp to their VCC and power the buffer's rail through the input; I2C pull-ups to a
  rail that stays up while a chip on the bus is off (the CH224A runs from VBUS, so pull-ups to 3.3 V would feed it
  on DC power); two supplies joined by 0 Ω links (siebenuhr's EXT_5V and USB VBUS).
- **Inrush.** About 264 µF on siebenuhr's USB-C VBUS at attach; the BQ25890's BATFET connecting the cells into about
  83 µF of SYS on dodeca v1.3, against a 9 A trip. The second is on its bench list, with C402 as the fix.
- **The firmware, not the board.** v1.2's charger driver wrote 0 mA into the charge-current register. A register
  dump told it apart from a hardware fault; the board needs a way to make one.
- **The review's own errors.** Round 1 of the dodeca review had the BATFET_DIS behaviour wrong. Round 2 caught it
  by reading the datasheet section again, which is why step 7 exists.
