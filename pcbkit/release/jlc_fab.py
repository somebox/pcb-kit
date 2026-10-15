"""JLCPCB fabrication and assembly files for a board or panel, matching kicad-jlcpcb-tools' output.

  pcbkit fab BOARD.kicad_pcb OUT_DIR

Writes into OUT_DIR:
- GERBER-<name>.zip: copper, mask, paste and silk layers plus Edge.Cuts (which carries a panel's V-cut lines), and the
  PTH/NPTH Excellon drill files. Gerbers and drills use the drill/place (aux) origin. Zones are refilled first.
- BOM-<name>-front.csv and CPL-<name>-front.csv: JLC assembles the front only and the back is hand-soldered.
- BOM-<name>-both.csv and CPL-<name>-both.csv: JLC assembles both sides.

A part goes into the BOM and CPL if it has an LCSC number and is neither DNP nor excluded from BOM / position files.
That leaves out KiKit's fiducials and tooling holes. Both files list the same parts, so JLC finds a placement for every
BOM line. A hand-soldered part with an LCSC number, marked "Exclude from position files" on the board, stays in KiCad's
BOM but leaves both files; the summary counts it as left out for hand assembly.
- BOM rows are grouped by value, footprint and LCSC number. A group whose designator list would exceed JLC's
  2048-character cell limit (a panel's LEDs) is split over several rows of the same part.
- CPL positions are the centre of the part's pads, relative to the aux origin, with Y up.
- CPL rotation is KiCad's, with (180 - angle) on the bottom, plus the correction from data/jlc/corrections.csv.
  That file is kicad-jlcpcb-tools' table (originally matthewlai/JLCKicadTools). A rule matches the reference, value
  or footprint name; anchored matches are tried first.
Check rotations and polarity in JLC's placement preview before ordering. Harvested from the sign's jlc_fab.py.
"""
import argparse
import csv
import math
import os
import re
import shutil
import subprocess
import tempfile
import zipfile

from ..kicad import KICAD_CLI, data_path, ensure_pcbnew

DESIGNATOR_MAX = 2000                  # JLC's BOM limit is 2048 characters per Designator cell


def load_corrections():
    with open(data_path("jlc", "corrections.csv"), newline="") as fh:
        return [(r["Pattern"], int(float(r["Rotation"])), (float(r["Offset X"]), float(r["Offset Y"])))
                for r in csv.DictReader(fh)]


def correction(fp, corrections):
    for value in (fp.GetReference(), fp.GetValue(), str(fp.GetFPID().GetLibItemName())):
        for anchored in (True, False):
            for rx, rot, off in corrections:
                if re.search(f"(?:{rx})$" if anchored else rx, value):
                    return rot, off
    return None


def field(fp, key):
    return fp.GetFieldText(key).strip() if fp.HasField(key) else ""


def gerbers(src, out, name, n_cu):
    layers = ["F.Cu"] + [f"In{i}.Cu" for i in range(1, n_cu - 1)] + ["B.Cu", "F.SilkS", "B.SilkS", "F.Mask", "B.Mask",
                                                                       "F.Paste", "B.Paste", "Edge.Cuts"]
    tmp = tempfile.mkdtemp()
    try:
        run = lambda *a: subprocess.run([KICAD_CLI, "pcb", "export", *a], check=True, capture_output=True)
        run("gerbers", "--layers", ",".join(layers), "--subtract-soldermask", "--no-protel-ext", "--use-drill-file-origin",
            "--check-zones", "-o", tmp + "/", src)
        run("drill", "--format", "excellon", "--excellon-units", "mm", "--excellon-separate-th", "--drill-origin", "plot",
            "-o", tmp + "/", src)
        gz = os.path.join(out, f"GERBER-{name}.zip")
        files = sorted(f for f in os.listdir(tmp) if f.endswith((".gbr", ".drl")))
        with zipfile.ZipFile(gz, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
            for f in files:
                z.write(os.path.join(tmp, f), f)
        return gz, len(files)
    finally:
        shutil.rmtree(tmp)


def fab(src, out):
    ensure_pcbnew()
    import pcbnew
    mm = pcbnew.ToMM
    name = os.path.splitext(os.path.basename(src))[0]
    os.makedirs(out, exist_ok=True)
    b = pcbnew.LoadBoard(src)
    gz, n_files = gerbers(src, out, name, b.GetCopperLayerCount())
    corrections = load_corrections()

    def placement(fp):
        pads = list(fp.Pads())
        box = pads[0].GetBoundingBox()
        for p in pads[1:]:
            box.Merge(p.GetBoundingBox())
        c, o = box.GetCenter(), b.GetDesignSettings().GetAuxOrigin()
        x, y = mm(c.x - o.x), mm(c.y - o.y)
        bottom = fp.IsFlipped()
        rot = fp.GetOrientation().AsDegrees()
        if bottom:
            rot = (180 - rot) % 360
        corr = correction(fp, corrections)
        if corr:
            rot = (rot + corr[0]) % 360
            dx, dy = corr[1]
            if dx or dy:
                a = math.radians(rot - corr[0])
                ox, oy = dx * math.cos(a) + dy * math.sin(a), -dx * math.sin(a) + dy * math.cos(a)
                x, y = x + (-ox if bottom else ox), y + oy
        return x, -y, rot % 360, "bottom" if bottom else "top"

    parts = [fp for fp in sorted(b.GetFootprints(), key=lambda f: f.GetReference()) if field(fp, "LCSC") and not fp.IsDNP()]
    for variant, sides in (("front", {"top"}), ("both", {"top", "bottom"})):
        sel = [fp for fp in parts if ("bottom" if fp.IsFlipped() else "top") in sides]
        # one list for both files, so every BOM line has a CPL placement; hand-soldered parts leave both
        placed = [fp for fp in sel if not fp.IsExcludedFromBOM() and not fp.IsExcludedFromPosFiles()]
        groups = {}
        for fp in placed:
            groups.setdefault((fp.GetValue(), str(fp.GetFPID().GetLibItemName()), field(fp, "LCSC")), []).append(fp.GetReference())
        with open(os.path.join(out, f"BOM-{name}-{variant}.csv"), "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            # JLC's own column name: with "LCSC" its BOM tool ignored the numbers and matched by value ("Select by System")
            w.writerow(["Comment", "Designator", "Footprint", "JLCPCB Part #", "Quantity"])
            for (val, fpn, lcsc), refs in sorted(groups.items(), key=lambda kv: kv[1][0]):
                chunk = []
                for r in refs + [None]:
                    if r is None or len(",".join(chunk + [r])) > DESIGNATOR_MAX:
                        w.writerow([val, ",".join(chunk), fpn, lcsc, len(chunk)])
                        chunk = []
                    if r is not None:
                        chunk.append(r)
        with open(os.path.join(out, f"CPL-{name}-{variant}.csv"), "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["Designator", "Val", "Package", "Mid X", "Mid Y", "Rotation", "Layer"])
            for fp in placed:
                x, y, rot, layer = placement(fp)
                w.writerow([fp.GetReference(), fp.GetValue(), str(fp.GetFPID().GetLibItemName()),
                            f"{x:.4f}", f"{y:.4f}", f"{rot:g}", layer])
        hand = len(sel) - len(placed)
        print(f"{variant:5s}: {len(placed)} placements, {len(groups)} BOM lines ({len({g[2] for g in groups})} distinct LCSC "
              f"parts)" + (f", {hand} left out for hand assembly" if hand else ""))
    print(f"{gz}: {n_files} files")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("board")
    ap.add_argument("out")
    a = ap.parse_args(argv)
    fab(a.board, a.out)


if __name__ == "__main__":
    main()
