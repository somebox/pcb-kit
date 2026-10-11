"""Parts we keep using: LCSC numbers for the stock resistors and capacitors, by value and package.

All JLC Basic or Preferred (no extended-part fee) unless noted. Harvested from the projects' builder.py tables;
check stock with `pcbkit jlc --lcsc C25744 ...` or bomi before an order. Add a part here only once it has been
ordered and worked.

The catalog of proven ICs, drivers and connectors is library/PARTS.md (written for people, with the notes);
`pcbkit parts [TEXT]` lists its parts with the current stock from the JLC parts cache.
"""
import os
import re
import sys

CATALOG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "library", "PARTS.md")
FP = {
    "R0402": "Resistor_SMD:R_0402_1005Metric",
    "R0603": "Resistor_SMD:R_0603_1608Metric",
    "C0402": "Capacitor_SMD:C_0402_1005Metric",
    "C0603": "Capacitor_SMD:C_0603_1608Metric",
    "C0805": "Capacitor_SMD:C_0805_2012Metric",
    "C1206": "Capacitor_SMD:C_1206_3216Metric",
}

# 0402 resistors (1 %)
RES = {"27": "C25156", "33": "C25105", "1k": "C11702", "1.2k": "C25862", "4.7k": "C25900", "5.1k": "C25905",
       "10k": "C25744", "22k": "C25768", "100k": "C25741"}
# 0603 resistors, for hand-soldered sides that want nothing smaller than 0603
RES0603 = {"1k": "C21190", "10k": "C25804", "22k": "C31850", "100k": "C25803"}

# capacitors: value -> (LCSC, footprint key)
CAP = {"15pF": ("C1548", "C0402"), "100nF": ("C1525", "C0402"), "1uF": ("C52923", "C0402"), "4.7uF": ("C23733", "C0402"),
       "10uF": ("C19702", "C0603"), "22uF": ("C45783", "C0805"), "100uF": ("C15008", "C1206")}
CAP0603 = {"100nF": "C14663", "10uF": "C19702"}


def resistor(value, pkg="0402"):
    """(footprint, LCSC) for a stock resistor."""
    if pkg == "0603":
        return FP["R0603"], RES0603[value]
    return FP["R0402"], RES[value]


def capacitor(value, pkg=None):
    """(footprint, LCSC) for a stock capacitor; pkg="0603" forces 0603 where a 0603 part exists."""
    lcsc, fpkey = CAP[value]
    if pkg == "0603" and fpkey != "C0603":
        lcsc, fpkey = CAP0603[value], "C0603"
    return FP[fpkey], lcsc


def catalog(path=CATALOG):
    """Parts from the tables in library/PARTS.md: dicts with section, part, lcsc, package, use."""
    rows, section = [], ""
    with open(path) as f:
        for line in f:
            if line.startswith("## "):
                section = line[3:].strip()
            cells = [c.strip() for c in line.strip().strip("|").split("|")] if line.startswith("|") else []
            if len(cells) >= 5 and re.fullmatch(r"C\d{3,9}", cells[1]):
                rows.append(dict(section=section, part=cells[0], lcsc=cells[1], package=cells[2], use=cells[4]))
    return rows


def main(argv=None):
    """pcbkit parts [TEXT] [--low N]: the catalog with current stock; TEXT filters by part or use."""
    import argparse
    import sqlite3
    from .jlc import DB
    ap = argparse.ArgumentParser(prog="pcbkit parts", description=main.__doc__)
    ap.add_argument("text", nargs="?", help="filter on part number or use (case-insensitive)")
    ap.add_argument("--low", type=int, default=5000, help="flag stock below this (default 5000)")
    a = ap.parse_args(argv)
    rows = [r for r in catalog() if not a.text or a.text.lower() in (r["part"] + " " + r["use"]).lower()]
    live = {}
    if os.path.exists(DB):
        db = sqlite3.connect(DB)
        q = "select LCSC, Stock, Library_Type, Price from components where LCSC in (%s)" % ",".join("?" * len(rows))
        live = {r[0]: r[1:] for r in db.execute(q, [r["lcsc"] for r in rows])} if rows else {}
        import time
        print("stock from %s (cache of %s)" % (DB, time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(DB)))))
    else:
        print("no parts cache at %s; showing the catalog only" % DB)
    section = None
    for r in rows:
        if r["section"] != section:
            section = r["section"]
            print("\n" + section)
        stock, kind, price = live.get(r["lcsc"], (None, "", None))
        flag = "  LOW" if stock is not None and stock < a.low else ("  not in cache" if live and stock is None else "")
        print("  %-20s %-10s %-12s %8s %-9s %-7s %s%s" % (r["part"], r["lcsc"], r["package"],
              "-" if stock is None else stock, kind, "" if price is None else "$%.3f" % price, r["use"], flag))


if __name__ == "__main__":
    main(sys.argv[1:])
