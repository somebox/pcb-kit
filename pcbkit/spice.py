"""Run an ngspice deck and collect its measurements.

  pcbkit spice DECK.cir [--vendor DIR] [--timeout 90] [--gear-retry] [--keep DIR]

The deck runs in a scratch directory with PSpice compatibility on (`-D ngbehavior=psa`), which TI's and most
vendors' models need. Decks may write @SIM@ for the deck's own directory and @VENDOR@ for the vendor model
directory, so `.include` lines work from any checkout. Prints every `meas`/`print` value, and the files the deck
wrote with `wrdata`; exit status 1 when the run was aborted (timeout, "timestep too small", or no measurements).

From a project script:

    from pcbkit.spice import run
    r = run(deck_text, sim_dir="sim", vendor="sim/vendor", gear_retry=True)
    if r.aborted: ...
    r.meas["vout_min"], r.files["steps.txt"]

What the dodeca v1.3 sims taught (docs in that repo's sim/README.md):
- A failed run still prints `meas = 0` lines; trust `aborted`, not the numbers.
- Switch-level vendor models often stall in ngspice; an averaged model built from the vendor's control constants
  is usually the faster route. Retrying with gear integration rescues some switchover stalls.
- In a B-source, `{a - b} * x` expands without brackets: write `({a - b}) * x`.
- Instance names defined in an included file can't be reused in the deck.

Needs `ngspice` on PATH (`brew install ngspice`); KiCad ships only the library, not the CLI.
Harvested from dodeca-v2-hardware hardware/control-board/v1.3/sim/spice.py.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

ABORT_MARKERS = ("timestep too small", "aborted", "simulation interrupted", "doanalyses: TRAN:  Timestep too small")


class Result:
    def __init__(self, meas, out, files, aborted, reason=""):
        self.meas, self.out, self.files, self.aborted, self.reason = meas, out, files, aborted, reason

    def __repr__(self):
        return "Result(%d measurements%s)" % (len(self.meas), ", aborted: " + self.reason if self.aborted else "")


def _with_gear(deck):
    lines = deck.splitlines()
    return "\n".join(lines[:1] + [".options method=gear"] + lines[1:]) + "\n"


def _once(deck, timeout, keep):
    d = tempfile.mkdtemp(prefix="pcbkit-spice-")
    try:
        path = os.path.join(d, "deck.cir")
        with open(path, "w") as f:
            f.write(deck)
        try:
            res = subprocess.run(["ngspice", "-D", "ngbehavior=psa", "-b", path],
                                 cwd=d, capture_output=True, text=True, timeout=timeout)
            out = res.stdout + res.stderr
        except subprocess.TimeoutExpired:
            return Result({}, "", {}, True, "timeout after %d s" % timeout)
        files = {}
        for n in os.listdir(d):
            if n.endswith((".txt", ".csv")):
                with open(os.path.join(d, n)) as f:
                    files[n] = f.read()
        if keep:
            os.makedirs(keep, exist_ok=True)
            for n in os.listdir(d):
                shutil.copy(os.path.join(d, n), keep)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    meas = {}
    for k, v in re.findall(r"^\s*([\w()]+)\s*=\s*([-+\d.eE]+)", out, re.M):
        try:
            meas[k.lower()] = float(v)
        except ValueError:
            pass
    low = out.lower()
    hit = next((m for m in ABORT_MARKERS if m.lower() in low), "")
    aborted = bool(hit) or not meas
    return Result(meas, out, files, aborted, hit or ("no measurements" if not meas else ""))


def run(deck, sim_dir=None, vendor=None, timeout=90, gear_retry=False, keep=None):
    """Run one deck (text). sim_dir/vendor replace @SIM@/@VENDOR@. With gear_retry, an aborted run is tried once
    more with `.options method=gear`. Returns a Result with meas (name -> float), out, files and aborted."""
    if shutil.which("ngspice") is None:
        raise SystemExit("ngspice not found on PATH (brew install ngspice)")
    sim_dir = os.path.abspath(sim_dir or os.getcwd())
    deck = deck.replace("@SIM@", sim_dir).replace("@VENDOR@", os.path.abspath(vendor or os.path.join(sim_dir, "vendor")))
    r = _once(deck, timeout, keep)
    if r.aborted and gear_retry:
        g = _once(_with_gear(deck), timeout, keep)
        g.reason = (g.reason + " (gear retry)").strip() if g.aborted else "first run aborted (%s); gear retry ok" % r.reason
        return g
    return r


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pcbkit spice", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("deck")
    ap.add_argument("--vendor", help="vendor model directory for @VENDOR@ (default: <deck dir>/vendor)")
    ap.add_argument("--timeout", type=int, default=90)
    ap.add_argument("--gear-retry", action="store_true", help="retry an aborted run with gear integration")
    ap.add_argument("--keep", help="copy the scratch directory (deck, wrdata files) here")
    ap.add_argument("--log", action="store_true", help="print ngspice's output")
    a = ap.parse_args(argv)
    with open(a.deck) as f:
        deck = f.read()
    r = run(deck, sim_dir=os.path.dirname(os.path.abspath(a.deck)), vendor=a.vendor, timeout=a.timeout,
            gear_retry=a.gear_retry, keep=a.keep)
    if a.log:
        print(r.out)
    for k, v in r.meas.items():
        print("%-24s %.6g" % (k, v))
    for n in r.files:
        print("wrote", n, "(%d lines)" % r.files[n].count("\n"))
    if r.aborted:
        sys.exit("aborted: " + r.reason)
    if r.reason:
        print(r.reason)


if __name__ == "__main__":
    main()
