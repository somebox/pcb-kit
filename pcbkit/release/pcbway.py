"""PCBWay assembly files beside the JLC ones (pcbkit fab --houses jlcpcb,pcbway): PCBWay sources parts by manufacturer
and part number, so its BOM carries both, and its centroid wants KiCad's own rotations (JLC's CPL has JLC's rotation
corrections applied). The Gerber zip is the same for both houses.

- BOM-<name>-pcbway.csv: Item #, Designator, Qty, Manufacturer, Mfg Part #, Description, Value, Package, Type, LCSC,
  Notes. One row per part number. The part number is the footprint's MPN field without a trailing " (...)" note (the note goes
  to Notes), else the parts cache's MFR_Part; the manufacturer, description and package come from konnect's parts
  cache by LCSC number.
- centroid-<name>-pcbway.csv: Designator, Mid X, Mid Y, Layer, Rotation, Value, Package. The footprint's origin
  relative to the aux (drill/place) origin, Y up, in mm; the rotation as KiCad stores it, on either side.
Both are UTF-8 with a byte-order mark (the cache's descriptions carry Ω, ±, ℃). Check the parts and the placement
preview in PCBWay's quote before ordering.
"""
import csv
import os
import re
import sqlite3

from ..jlc import DB

NOTE = re.compile(r"\s+\(([^)]*)\)\s*$")       # "BCM847BS,115 (matched pair)", not "B3B-XH-A(LF)(SN)"


def _cache(lcscs):
    if not os.path.exists(DB):
        return {}
    db = sqlite3.connect(DB)
    out = {}
    for lcsc in lcscs:
        row = db.execute("select Manufacturer, MFR_Part, Package, Description from components where LCSC = ?",
                         (lcsc,)).fetchone()
        if row:
            out[lcsc] = row
    return out


def write(board, parts, out, name, field):
    """parts: the footprints the assembler places (any side); field(fp, key) reads a footprint field."""
    import pcbnew
    mm = pcbnew.ToMM
    cache = _cache({field(fp, "LCSC") for fp in parts if field(fp, "LCSC")})
    rows, missing = {}, []
    for fp in sorted(parts, key=lambda f: (re.sub(r"\d", "", f.GetReference()), int(re.sub(r"\D", "", f.GetReference()) or 0))):
        lcsc = field(fp, "LCSC")
        maker, cache_mpn, package, desc = cache.get(lcsc, ("", "", "", ""))
        mpn, note = field(fp, "MPN"), ""
        m = NOTE.search(mpn)
        if m:
            mpn, note = mpn[:m.start()].strip(), m.group(1)
        mpn = mpn or cache_mpn
        if not (maker and mpn):
            missing.append(fp.GetReference())
        smd = bool(fp.GetAttributes() & pcbnew.FP_SMD)
        row = rows.setdefault((maker, mpn or lcsc or fp.GetValue()), dict(
            refs=[], values=[], maker=maker, mpn=mpn, desc=desc, package=package or str(fp.GetFPID().GetLibItemName()),
            type="SMD" if smd else "THT", lcsc=lcsc, notes=[]))
        row["refs"].append(fp.GetReference())
        if fp.GetValue() not in row["values"]:
            row["values"].append(fp.GetValue())
        if note and note not in row["notes"]:
            row["notes"].append(note)
    bom = os.path.join(out, f"BOM-{name}-pcbway.csv")
    with open(bom, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["Item #", "Designator", "Qty", "Manufacturer", "Mfg Part #", "Description", "Value", "Package",
                    "Type", "LCSC", "Notes"])
        for i, r in enumerate(rows.values(), 1):
            w.writerow([i, ",".join(r["refs"]), len(r["refs"]), r["maker"], r["mpn"], r["desc"], " / ".join(r["values"]),
                        r["package"], r["type"], r["lcsc"], "; ".join(r["notes"])])
    origin = board.GetDesignSettings().GetAuxOrigin()
    cen = os.path.join(out, f"centroid-{name}-pcbway.csv")
    with open(cen, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation", "Value", "Package"])
        for fp in sorted(parts, key=lambda f: f.GetReference()):
            p = fp.GetPosition()
            w.writerow([fp.GetReference(), f"{mm(p.x - origin.x):.4f}", f"{-mm(p.y - origin.y):.4f}",
                        "Bottom" if fp.IsFlipped() else "Top", f"{fp.GetOrientation().AsDegrees() % 360:g}",
                        fp.GetValue(), str(fp.GetFPID().GetLibItemName())])
    print(f"pcbway: {len(parts)} placements, {len(rows)} BOM lines" +
          (f"; no manufacturer or part number for {', '.join(missing)}" if missing else ""))
    return bom, cen, missing
