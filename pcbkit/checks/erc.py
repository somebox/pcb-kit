"""ERC through kicad-cli, summarised.  pcbkit erc SCHEMATIC.kicad_sch [--errors-only]

Exit status 1 on errors, and when kicad-cli fails or writes no report. Harvested from the spotlight's erc.py (the
kicad-cli variant; the konnect one is `Konnect().call("run_erc", ...)`).
"""
import argparse
import collections
import os
import sys
import tempfile

from ..kicad import cli_report


def run(sch, out=None):
    out = out or os.path.join(tempfile.gettempdir(), "pcbkit_erc.json")
    d = cli_report(out, "sch", "erc", "--format", "json", "--severity-all", "-o", out, sch)
    return [x for s in d["sheets"] for x in s["violations"]]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("schematic")
    ap.add_argument("--errors-only", action="store_true")
    a = ap.parse_args(argv)
    v = run(a.schematic)
    print(dict(collections.Counter((x["severity"], x["type"]) for x in v)))
    for x in v:
        if a.errors_only and x["severity"] != "error":
            continue
        print(" ", x["severity"], x["type"], "|", x["description"][:90], "|", [i["description"][:60] for i in x["items"]])
    sys.exit(1 if any(x["severity"] == "error" for x in v) else 0)


if __name__ == "__main__":
    main()
