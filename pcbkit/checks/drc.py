"""DRC through kicad-cli, summarised: counts by type, every error, unconnected items by net and footprint.

  pcbkit drc BOARD.kicad_pcb [--refill] [--save] [--parity] [--json OUT] [--full]

--refill fills the zones first (what the fab sees); --save writes the filled board back, which is how the
projects keep their boards filled; --parity checks the PCB against the schematic. Exit status 1 on errors or
unconnected items, and when kicad-cli fails or writes no report. Merged from the projects' drcsum.py and dodeca's
unrouted.py.
"""
import argparse
import collections
import os
import re
import sys
import tempfile

from ..kicad import cli_report


def run(board, refill=False, save=False, parity=True, out=None):
    out = out or os.path.join(tempfile.gettempdir(), "pcbkit_drc.json")
    args = ["pcb", "drc", "--format", "json", "-o", out]
    if refill:
        args.append("--refill-zones")
    if save:
        args.append("--save-board")
    if parity:
        args.append("--schematic-parity")
    return cli_report(out, *args, board)


def summarise(d, full=False):
    v = d["violations"]
    errors = [x for x in v if x["severity"] == "error"]
    print("errors:", dict(collections.Counter(x["type"] for x in errors)) or 0)
    print("warnings:", dict(collections.Counter(x["type"] for x in v if x["severity"] == "warning")) or 0)
    par = d.get("schematic_parity", [])
    print("parity:", dict(collections.Counter(x["type"] for x in par)) or 0)
    for x in errors + par + ([x for x in v if x["severity"] == "warning"] if full else []):
        print(" ", x["severity"], x["type"], "|", x["description"][:90], "|", [i["description"][:70] for i in x["items"]])
    u = d["unconnected_items"]
    print(f"unconnected: {len(u)}")
    if u:
        nets, refs = collections.Counter(), collections.Counter()
        for item in u:
            m = re.search(r"\[([^\]]+)\]", item["items"][0]["description"])
            nets[m.group(1) if m else "?"] += 1
            for it in item["items"]:
                m = re.search(r" of (\S+)", it["description"])
                if m:
                    refs[m.group(1)] += 1
        split = sorted({i["items"][0]["description"].split("'")[1] for i in u
                        if len(i["items"]) == 2 and all(x["description"].startswith("Zone") for x in i["items"])
                        and i["items"][0]["description"] == i["items"][1]["description"]})
        if split:
            print("  split zones (a track cut a zone in two):", ", ".join(split))
        print("  by net:", ", ".join(f"{n} {c}" for n, c in nets.most_common()))
        print("  by footprint:", ", ".join(f"{n} {c}" for n, c in refs.most_common(25)))
    return not errors and not u and not par


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("board")
    ap.add_argument("--refill", action="store_true")
    ap.add_argument("--save", action="store_true", help="save the board after refilling (implies --refill)")
    ap.add_argument("--no-parity", action="store_true")
    ap.add_argument("--json", help="keep the JSON report here")
    ap.add_argument("--full", action="store_true", help="list warnings too")
    a = ap.parse_args(argv)
    d = run(a.board, refill=a.refill or a.save, save=a.save, parity=not a.no_parity, out=a.json)
    sys.exit(0 if summarise(d, a.full) else 1)


if __name__ == "__main__":
    main()
