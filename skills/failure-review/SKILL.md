---
name: failure-review
description: "Review a board for what could fail once it is built, before it is ordered, and write a briefing with evidence before anything is changed. Use when the user asks to validate or review a design, asks what might go wrong or whether a board is ready to order, when a board has changed since its review, when planning bring-up or test points, and when a built board has failed and needs a cause."
---

# Failure review

**Read-only until the user has the briefing.** Gather, calculate and simulate, but don't edit the schematic, the
board or the design data the board is generated from. Changes follow the user's choice among the recommendations.

ERC and DRC don't see a divider that sits on a threshold at the tolerance corners, an input that powers a chip whose
rail is off, or a charger that passes its input straight through in one of its modes. This review looks for those,
before the order, and plans how each would be found on the bench. A smaller scope: "Kinds of review". A built board
that has failed: "When a built board has failed".

[briefing.md](briefing.md) has the briefing's template and rules: the status line every review opens with (a look's
reply included), the evidence marks, how findings are rated and numbered, and what a sent briefing may still gain.
Read it before writing or adding to one: steps 5 and 9 to 12, a look, a failed board's document.
[test-points.md](test-points.md) is step 6.

Worked examples:
- dodeca control board v1.3, `~/src/dodeca-v2-hardware/hardware/control-board/v1.3/POWER_REVIEW.md`: two review
  rounds, simulations in `sim/`, a bench list.
- siebenuhr controller-v2, `~/Documents/Projects/siebenuhr/controller-v2/docs/failure-analysis-pd-shutdown.md`: a
  unit that failed in the field. Ranked hypotheses, each with the test that tells it apart, updated as results came in.
- rp2354-messages-sign controller v3,
  `~/Documents/Projects/rp2354-messages-sign/docs/reviews/2026-11-16-controller.md`:
  a review of the changes since the first review, with the status of every earlier finding.

## Steps

1. **Scope and ground truth.** Board, revision and commit; which blocks are in scope; which kind of review this is
   and at what depth (below). Read the status lines of README, DESIGN, LAYOUT and PRODUCTION, DESIGN's "what the
   last version taught us", earlier reviews, and this kit's `library/PARTS.md` notes for each part on the board. Ask
   what is already built and how those units behave: a fault they show is the first finding. Read in DESIGN.md's
   requirements what this build is, a prototype or a product, and what a prototype will be used with (below); where
   they don't say, ask. List the boards that share a circuit with this one (a module and its controller, the same
   block in another project): a finding in a shared circuit names them in its action. Boards reviewed together get a
   briefing each. What lies between them (mating connectors, the cable, the stack) is covered in one of them, and
   the others' status lines point to it. Run `pcbkit erc`, `pcbkit drc`, the project's netlist assertions and
   `pcbkit nets SCH` yourself, at this commit, and note each result. A result that was handed over ("ERC is clean")
   is recalled, not checked. Review the netlist as exported, not the design notes' account of it: they drift.
2. **Gather references.** For every IC: the datasheet and its revision. The PDF is the source: in
   `docs/datasheets/`, in another project (`fd -I -e pdf -i <part> ~/Documents/Projects ~/src`), or from the maker.
   `bomi datasheet LCSC --summary` and the `.md` files beside the PDFs are summaries a model wrote: they find the
   section, and they are **never the evidence** (two were wrong in Orbitchron's review, one on which pad of an LED is
   the anode). Where the PDF can't be read and a rating rests on a distributor's listing, the finding says so.
   Then the vendor's evaluation-board guide (its schematic, layout and BOM are the reference design), the
   application notes the datasheet points to, and errata. Then designs like this one: earlier versions in
   `archive/`, other projects with the same LCSC code (`rg -l C3235557 -g '*.kicad_sch' ~/Documents/Projects ~/src`),
   open designs using the part (GitHub, "<part> kicad"), and the vendor's forum (TI E2E and the like), where other
   people's failures with the part are written up. Record each source with its document number, so a finding can
   cite the section.
   The datasheet is read for every IC in scope. The search beyond it is for the parts a failure would hang on and
   the parts no built board has proven (`library/PARTS.md`); "Not covered" says for which parts it was not made.
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

   Mark each result with the briefing's evidence marks ([briefing.md](briefing.md)). Anything inferred, or recalled
   rather than looked up, says so until it is checked.
6. **Plan debug access** for each failure mode that matters: where it would be measured, and what would be done if
   it turns out real. [test-points.md](test-points.md) is this step's content: what to measure, isolate and
   override, and the pins that must not get a pad.
7. **Test each recommendation like a finding.** The user applies it as written, and a change is a new design:
   - what else hangs on the node or the part being changed, and what the old arrangement did that nobody wrote
     down;
   - a new part's own limits in every state of step 4: absolute maximum, thresholds with their hysteresis at the
     corners, start-up, what it does in a fault;
   - a new value at the tolerance corners, by the calculation or simulation that made the finding (synth-blocks:
     a proposed 4.7 kΩ failed 7 of 24 simulated corners where the circuit as drawn failed 1, and was withdrawn);
   - that it can be built: the part in stock under its LCSC code, its footprint, room on the board.
   A recommendation that can't be tested before the briefing reads "bench test before deciding".
8. **Verify before writing.** A separate pass tries to refute every finding. **It is never skipped.** Where the
   harness runs sub-agents and the review is more than a look, the block checks of step 3 go to one agent each, with
   the block's datasheets and netlist excerpt. Another agent then gets the findings and the same sources, and
   attacks each finding. Without sub-agents, do the pass yourself, from the sources again and not from the finding's
   text.
   - Re-read every datasheet claim against the PDF and quote the table row with its column and test condition.
     Orbitchron's first round read the TLV62569's 3 A current limit as typical. It is the minimum, with no maximum
     given, and the second round's finding of an unlimited short followed from that.
   - Check a layout claim by measuring the board file, not from a picture or a zone setting (siebenuhr v3's F6
     misread "thermal reliefs for PTH only").
   - Attack the recommendations of step 7 and the "verified OK" claims as hard as the findings.
   - Keep what was refuted, with the source that refuted it, so it isn't raised again. Round 2 of the dodeca
     review corrected round 1 on when the charger sets BATFET_DIS.
   - If the design has moved since step 1, run the checks again, and the simulations the move touches.
9. **Write the briefing** as `docs/reviews/<date>-<board>.md` in the project, from the template in
   [briefing.md](briefing.md) and by the rules under it. Send it to the user, and **stop**.
10. **After the user decides,** each accepted change becomes a numbered decision in DESIGN.md, then the edits,
    followed by `pcbkit erc`, the assertions, `pcbkit cleanup`, `pcbkit drc --save` and `pcbkit check`, and the
    simulations the change touches run again on the design as edited. The briefing gets a Decisions table, dated,
    below what was sent: nothing in a sent briefing is renumbered or rewritten. Test points go into LAYOUT.md, the
    bench list into PRODUCTION.md's bring-up section. Carry each accepted change to the boards that share the
    circuit, or give the reason in the Decisions table: the sign's module kept the 33 Ω outputs that the controller
    had already changed to 100 Ω, until its own review found them. A review and its decisions are a round of the
    project's overview page: bring it up to date with the project-overview skill.
11. **Review the changes before the order.** In every project with a second round, a change accepted in the first
    caused a finding in the second. Review what changed (below) at the commit that will be ordered, as a new dated
    briefing that links this one: `<date>-<board>-round2.md` when the date is the same, so no briefing is
    overwritten. If that leads to more changes to the board, they get the same.
12. **When the boards are built,** add what JLC's placement viewer and the bench found to the briefing, dated, and
    the parts' lessons to this kit's `library/PARTS.md`.

## Kinds of review

The steps describe a full review of a board that has had none. The others run the same steps over a smaller scope,
and the status line says which:

- **The changes since a review.** What changed since the reviewed commit (`pcbkit changelog --since <commit>` for
  the netlist, the BOM and the layout; `git diff <commit>` for the design notes), each decision as it was drawn,
  the layout around each change, and the earlier "verified OK" claims a change touches. Every earlier finding gets
  a status:
  fixed (by which decision), carried over, accepted, open. The earlier review holds for what isn't mentioned.
- **Layout only,** once the schematic has been reviewed: the layout part of step 3, the netviews, measurements and
  checkers of step 5, connectors and footprints against their drawings, fab and assembly.
- **A design that was imported, or is already built** (an EasyEDA import, a product with units in use): start from
  how the built units behave, and review the import as a change. Footprints, pin maps and rotations are checked
  against the built board, not taken over from the import.
- **Schematic only,** before the layout is drawn: the steps without the layout part of step 3 and the layout
  tests of step 5. DRC and the checkers read "not run: no layout"; a layout-only review follows before the order.
- **One block, or one question** (does this divider hold): the steps for that block and the nets that leave it.
  The rest of the board is "not covered".

Any kind can be a **look** or go **deeper** (this kit's `docs/CONVENTIONS.md`, "Depth and scope"); the status
line says which.

- **A look** is time-boxed: step 1's checks, steps 3 and 4 for the blocks named, from the datasheet PDFs (fetching
  one is not web research), and steps 7 and 8 on what it found; a recommendation that needs a simulation reads
  "bench test before deciding". Step 2's wider search, step 5's simulations and step 6 are listed under "not
  covered"; a doubt they would have settled is an open check. It never reads "ready to order". Without a finding it
  is a reply under the status line, in briefing.md's form; a finding makes it a briefing.
- **Deeper** adds what the user names: every threshold at its corners, a second refuting pass by a fresh agent,
  the boards that share a circuit.
- **Always:** step 1's checks run at this commit, a datasheet claim read from the PDF, steps 7 and 8 for every
  recommendation and finding, no edit before the briefing, and step 11 before an order.

## Prototype or product

A prototype is a few boards on the user's bench, with the user's supply. A product goes to other people. The
findings are the same for both; the actions are not.

- A prototype can rest on the bench where a product needs a part: the supply named exactly (its voltage, its
  current limit, the one adapter), handling limited (no hot-plug, the cable fitted before power), a polarity marked.
- Such a finding reads "accept for this build", with the condition written where it will be seen: on the
  silkscreen where there is room, in the bench list, in PRODUCTION.md's bring-up section.
- The improvement is noted for the revision that warrants it, in DESIGN.md and as a DNP footprint or a pad where
  that is free now. It is not built in yet: a protection part or a re-route against a risk the bench already holds
  costs more than the prototype is there to spend.
- Still changed on a prototype: what would kill the board in the use it is given, what would hide the thing the
  prototype is built to find out, and what costs nothing.

## When a built board has failed

The same steps, starting from the symptom and kept to the blocks the hypotheses touch; the siebenuhr analysis is the
model.

1. Write down the report as given and what is known of the unit: variant, firmware, supply, how long it ran. The
   document is `docs/reviews/<date>-<board>.md`, with the ranked hypotheses in place of the findings table.
2. Rank the hypotheses, each with what speaks for it, what speaks against it, and the test that separates it from
   the others.
3. Cheapest tests first: questions to the owner and firmware-only tests before the bench, unpowered resistance
   checks (rails to GND, against a good board) before power, a current-limited supply before a real one.
4. Update the document as results come in, dated. Keep a ruled-out hypothesis with the result that ruled it out.
   When the work stops before a cause is shown, the status line says which hypotheses are untested.

## What earlier reviews and bring-ups found

Most of it is now a check in [checklist.md](checklist.md) or a step above, with the board named there. Three are
not:

- **A detector that fired on good boards.** v1.2's uncut-Teensy detector also tripped on correctly cut boards and
  was bypassed with a bodge wire. v1.3's replacement was simulated in both states first, which is how its false
  result was found, and the part was changed before the order (SPEC D20).
- **Load beyond what the source agreed to.** siebenuhr asked its PD supply for 5 V, where a contract allows 3 A,
  and drove 476 LEDs (7.6–10.5 A at full brightness) with no firmware cap, for months, through an 0805 0 Ω link
  rated 2 A. Budget the current against the weakest source the board accepts.
- **A fix that caused the next finding** (steps 7 and 11). The sign controller's eFuse, added against a wrong
  adapter, sat ahead of the bulk capacitors, with 1 µF ahead of it: in simulation its input rings to 8–10 V at
  plug-in, past the over-voltage threshold, and the board can stay dark until the adapter is power-cycled.
  dodeca's TPS2116, swapped in by a decision (D20), cuts the Teensy when the 5 V rail sags with USB absent. ESP
  Candle v1.3.1's VBUS was damped by accident, by a switch contact in series with the bulk capacitor; a decision
  took that path away, and VBUS rang to as much as 8.6 V in simulation against the mux's 6 V.
