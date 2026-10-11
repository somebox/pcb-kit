---
name: power-sim
description: Simulate a board's power path in ngspice (load steps, start-up, switchover, brown-out and reset margins) to choose parts and check design notes. Use when a question is "does the rail stay above X when Y happens", "does the reset/supervisor trip", or "which of these parts behaves better", and a hand calculation is not convincing.
---

# Power simulation with ngspice

The point is to answer one design question per script, with a number and a plot, and to write the answer back into
the design notes. The dodeca control board v1.3 (`~/src/dodeca-v2-hardware/hardware/control-board/v1.3/sim/`) is the
worked example: boost load steps, a supply-mux trace test that found a false result, and an options sweep that
picked the replacement part.

## Setup

```bash
brew install ngspice                 # KiCad ships the library only, not the CLI
pcbkit spice deck.cir --gear-retry   # runs in a scratch dir with -D ngbehavior=psa, prints .meas values
```

From a project script: `from pcbkit.spice import run`; `run(text, sim_dir=..., vendor=..., gear_retry=True)` returns
measurements, ngspice's output, the `wrdata` files and an `aborted` flag. Keep vendor models out of git
(`sim/vendor/`, fetched by a script) and the results in `sim/results/` as text and PNG.

## Way of working

1. Write the question and the pass/fail number first, in the design note it belongs to.
2. Pick the model: the vendor's transient model if it runs, otherwise an **averaged model** built from the vendor's
   control constants (error amp, current-sense gain, clamps) plus the board's real L, C and ESR. Parts without
   models become stand-ins (a current sink for a charger, R and C for a microcontroller's input).
3. One script per question, printing a table; a sweep over the tolerances or the options when choosing.
4. Plot the cases side by side (voltage solid, inductor or load current dotted on a second axis).
5. Write the result into the design note with the plot link, and add the simulated deviation to the tolerance
   analysis for worst cases. Say what the model leaves out (switching ripple, temperature).

## Parts with no model

A controller with a simple control law can be written from its datasheet. Example: rgb-spotlight `sim/channel.py`, a PT4115 hysteretic LED buck.
- **Switch:** use ngspice's `SW` element. Its `VT`/`VH` give the comparator hysteresis (e.g. on below 85 mV, off above 115 mV across Rs).
- **Control:** drive it from a B-source of the sense voltage, gated by the enable/DIM pin with `u()`.
- **Delay:** add an RC on the control node for the comparator delay; state it as an assumption.
- **Around it:** the board's real L with DCR, a fitted diode model (`IS`, `N`, `RS` from two datasheet VF points), the caps at their DC-bias capacitance, and the supply cable.
- **Loads:** an LED is an ideal diode + knee voltage + series R, at the datasheet VF.
- **Checks:** in-loop powers (`let p = v(a,b)*i(vsense)`, then `meas avg`) should add up to the input power. If they don't, the model is wrong.

## What has gone wrong before

- Switch-level TI models (TPS61178, TPS2121) stall within nanoseconds ("timestep too small"). Averaged models fix it.
- LM66200 runs only from a powered operating point; ramps from 0 V make it chatter. A floating input needs a bleeder.
- TPS2116 started with both supplies up can latch either channel: plug the second supply in after start-up.
- An aborted run still prints `meas = 0`. Trust `aborted`, give the operating point time to settle before measuring.
- B-source brace expansion: `{a - b} * x` becomes `a - b*x`; write `({a - b}) * x`.
- Instance names from an included file can't be reused in the deck.
