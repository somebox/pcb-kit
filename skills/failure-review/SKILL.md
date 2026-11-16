---
name: failure-review
description: "Validate a board before it is ordered: check every block against its datasheets, reference designs and what earlier boards taught, list what could fail after the boards come back and how each failure would show up and be diagnosed, test the doubtful cases by calculation or simulation, plan test points and rework provisions, and write a briefing with evidence before anything is changed. Use when the user asks to validate or review a design, asks what might go wrong or whether a board is ready to order, when a board has changed since its review and the changes need checking, when planning bring-up or test points, and when a built board has failed and needs a cause."
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
- rp2354-messages-sign controller v3,
  `~/Documents/Projects/rp2354-messages-sign/docs/reviews/2026-11-16-controller.md`:
  a review of the changes since the first review, with the status of every earlier finding. The eFuse added after
  the first review had 1 µF ahead of it, and its input rang to 8–10 V in simulation.

**Read-only until the user has the briefing.** Gather, calculate and simulate, but don't edit the schematic, the
board or the design data the board is generated from. Changes follow the user's choice among the recommendations.

## Steps

1. **Scope and ground truth.** Board, revision and commit; which blocks are in scope; which kind of review this is
   (below). Read the status lines of README, DESIGN, LAYOUT and PRODUCTION, DESIGN's "what the last version taught
   us", earlier reviews, and this kit's `library/PARTS.md` notes for each part on the board. Ask what is already
   built and how those units behave: a fault they show is the first finding. List the boards that share a circuit
   with this one (a module and its controller, the same block in another project): a finding in a shared circuit
   names them in its action.
   Run `pcbkit erc`, `pcbkit drc`, the project's netlist assertions and `pcbkit nets SCH` yourself, at this commit,
   and note each result. A result that was handed over ("ERC is clean") is recalled, not checked. Review the netlist
   as exported, not the design notes' account of it: they drift.
2. **Gather references.** For every IC: the datasheet and its revision. The PDF is the source: in
   `docs/datasheets/`, in another project (`fd -I -e pdf -i <part> ~/Documents/Projects ~/src`), or from the maker.
   `bomi datasheet LCSC --summary` and the `.md` files beside the PDFs are summaries a model wrote: they find the
   section, and they are never the evidence (two were wrong in Orbitchron's review, one on which pad of an LED is
   the anode). Where the PDF can't be read and a rating rests on a distributor's listing, the finding says so.
   Then the vendor's evaluation-board guide (its schematic, layout and BOM are the reference design), the
   application notes the datasheet points to, and errata. Then designs like this one: earlier versions in
   `archive/`, other projects with the same LCSC code (`rg -l C3235557 -g '*.kicad_sch' ~/Documents/Projects ~/src`),
   open designs using the part (GitHub, "<part> kicad"), and the vendor's forum (TI E2E and the like), where other
   people's failures with the part are written up. Record each source with its document number, so a finding can
   cite the section.
3. **Check each block.** Pin by pin for every IC: each pin's net against the datasheet's pin table, strap and
   config pins at the level meant, nothing floating that needs a level. Then the values against the datasheet's
   equations and the reference design, and the layout against the datasheet's layout guidance (`pcbkit netview
   --fill` for each power stage, which fills the zones in memory and leaves the board file alone; see
   visual-review). [checklist.md](checklist.md) lists what to look at per kind of block.
   A deviation from the reference design is fine when the design notes say why; otherwise it is a finding. Write
   down what holds as well: the "verified OK" list tells the reader what was covered.
4. **List the failure modes.** For each block, go through the board's states: unpowered, power-up, each
   combination of sources, hot-plug and unplug, brown-out, a fault downstream, no firmware or firmware stuck, and
   months of use in the field. For each failure: the cause, the effect, how likely it is, how it would show up on
   the bench, what tells it apart from the failures that look the same, and when it would be found: at the first
   power-up, by a test on the bench list, or only in the field. A failure we couldn't tell from its neighbours is a
   finding of its own, and usually wants a test point.
5. **Test the doubtful ones.** Write the question and the pass/fail number first, then answer it the cheapest way:
   - worst case by calculation, with resistor tolerance, reference spread, the datasheet's min and max (not typ),
     MLCC capacitance at its DC bias, and temperature: a short script that prints a table, in `sim/` or a
     scratch directory;
   - simulation for anything with timing (load steps, start-up, hand-over, inrush): the power-sim skill;
   - topology from the netlist, written as a netlist assertion when it encodes a failure mode, so it stays fixed;
   - layout from a netview of the region and a measurement on the board file (a clearance, a length, the
     resistance of a routed net), and KiCadRoutingTools' checkers (this kit's `docs/CONVENTIONS.md`, "Checks before
     an order", step 5);
   - firmware duties from the firmware's code where it exists (siebenuhr's had no current cap and restored full
     brightness at boot).
   Mark each result with the briefing's evidence marks. Anything inferred, or recalled rather than looked up, says
   so until it is checked.
6. **Plan debug access** for each failure mode that matters: where it would be measured, and what would be done if
   it turns out real (below).
7. **Test each recommendation like a finding.** The user applies it as written, and a change is a new design:
   - what else hangs on the node or the part being changed, and what the old arrangement did that nobody wrote
     down;
   - a new part's own limits in every state of step 4: absolute maximum, thresholds with their hysteresis at the
     corners, start-up, what it does in a fault;
   - a new value at the tolerance corners, by the calculation or simulation that made the finding (synth-blocks:
     a proposed 4.7 kΩ failed 7 of 24 simulated corners where the circuit as drawn failed 1, and was withdrawn);
   - that it can be built: the part in stock under its LCSC code, its footprint, room on the board.
   A recommendation that can't be tested before the briefing reads "bench test before deciding".
8. **Verify before writing.** A separate pass tries to refute every finding, and it is never skipped. Where the
   harness runs sub-agents, the block checks of step 3 go to one agent each, with the block's datasheets and
   netlist excerpt. Another agent then gets the findings and the same sources, and attacks each finding. Without
   sub-agents, do the pass yourself, from the sources again and not from the finding's text.
   - Re-read every datasheet claim against the PDF and quote the table row with its column and test condition.
     Orbitchron's first round read the TLV62569's 3 A current limit as typical. It is the minimum, with no maximum
     given, and the second round's finding of an unlimited short followed from that.
   - Check a layout claim by measuring the board file, not from a picture or a zone setting (siebenuhr v3's F6
     misread "thermal reliefs for PTH only").
   - Attack the recommendations of step 7 and the "verified OK" claims as hard as the findings.
   - Keep what was refuted, with the source that refuted it, so it isn't raised again. Round 2 of the dodeca
     review corrected round 1 on when the charger sets BATFET_DIS.
   - If the design has moved since step 1, run the checks again, and the simulations the move touches.
9. **Write the briefing** (below) as `docs/reviews/<date>-<board>.md` in the project, send it to the user, and stop.
10. **After the user decides,** each accepted change becomes a numbered decision in DESIGN.md, then the edits,
    followed by `pcbkit erc`, the assertions, `pcbkit cleanup`, `pcbkit drc --save` and `pcbkit check`, and the
    simulations the change touches run again on the design as edited. The briefing gets a Decisions table, dated.
    Test points go into LAYOUT.md, the bench list into PRODUCTION.md's bring-up section. Carry each accepted
    change to the boards that share the circuit, or give the reason in the Decisions table: the sign's module kept
    the 33 Ω outputs that the controller had already changed to 100 Ω, until its own review found them.
11. **Review the changes before the order.** In every project with a second round, a change accepted in the first
    caused a finding in the second. Review what changed (below) at the commit that will be ordered, as a new dated
    briefing that links this one. If that leads to more changes to the board, they get the same.
12. **When the boards are built,** add what JLC's placement viewer and the bench found to the briefing, dated, and
    the parts' lessons to this kit's `library/PARTS.md`.

## Kinds of review

The steps describe a full review of a board that has had none. The others run the same steps over a smaller scope,
and the status line says which:

- **The changes since a review.** What changed since the reviewed commit (`pcbkit changelog --since <commit>` for
  the netlist, the BOM and the layout; `git diff` for the design notes), each decision as it was drawn, the layout
  around each change, and the earlier "verified OK" claims a change touches. Every earlier finding gets a status:
  fixed (by which decision), carried over, accepted, open. The earlier review holds for what isn't mentioned.
- **Layout only,** once the schematic has been reviewed: the layout part of step 3, the netviews, measurements and
  checkers of step 5, connectors and footprints against their drawings, fab and assembly.
- **A design that was imported, or is already built** (an EasyEDA import, a product with units in use): start from
  how the built units behave, and review the import as a change. Footprints, pin maps and rotations are checked
  against the built board, not taken over from the import.

## The briefing

The reader should be able to decide from the first screen. The reasoning stays in the design notes and sim results
it links to.

```markdown
# <Board> <rev>: failure review

Status (<date>): reviewed at <commit>; <full review | changes since <review> | layout only | imported or built
design>; covers <blocks>; not covered: <...>; open checks: <...>; shares circuits with: <boards>.
Checks at this commit: ERC <n>; DRC <errors, unconnected, parity>; assertions <n nets, n failures>; checkers <ran |
not run, why>. Second pass: <what tried to refute the findings>.
Evidence marks: *ds* datasheet text (document, section, column), *sim*, *calc*, *board* (netlist or layout,
measured), *meas* (measured on a built unit), *inferred*, *recalled* (not checked).

## Verdict
Three to five lines: ready to order or not, the findings to settle first, and any check still open that could
kill the board.

## Findings
| # | Finding | Effect | Likelihood | Found at | Evidence | How it shows up, how to tell | Action (cost) |
|---|---|---|---|---|---|---|---|
| F1 | ... | dead | likely | bench | *ds* TPS2116 SLVSFG1A §7.6.1; *sim* sim/results/<question>.txt | symptom; reading at TP3 | change / test point / firmware / bench test / accept |

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
- Each action names its cost: a pad, a part, a re-route, a firmware duty, or a bench test before deciding.
- "Not covered" is for what was left out of scope on purpose. A check that belongs to the review and wasn't done (a
  connector's footprint against its drawing, the placement viewer) is an open check: it goes into the status line,
  and into the verdict when it could kill the board. Orbitchron's first review listed connector mating under "Not
  covered"; the second called the board ready after four cheap changes, and a fit check the user asked for the next
  day found both board-to-board footprints mirrored.
- Once sent, the briefing is a record. Decisions and bench results are added below, dated, and a later round is a
  new dated file that links it. Nothing is renumbered or rewritten in place.

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

1. Write down the report as given and what is known of the unit: variant, firmware, supply, how long it ran. The
   document is `docs/reviews/<date>-<board>.md`, with the ranked hypotheses in place of the findings table.
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
  by reading the datasheet section again, which is why step 8 exists.
- **A fix that caused the next finding.** The sign controller's eFuse, added against a wrong adapter, sat ahead of
  the bulk capacitors: in simulation its input rings past the over-voltage threshold at plug-in, and the board can
  stay dark until the adapter is power-cycled. dodeca's TPS2116, swapped in by a decision (D20), cuts the Teensy
  when the 5 V rail sags with USB absent. ESP Candle v1.3.1's VBUS was damped by accident, by a switch contact in
  series with the bulk capacitor; a decision took that path away, and VBUS rang to as much as 8.6 V in simulation
  against the mux's 6 V. Steps 7 and 11 exist for these.
