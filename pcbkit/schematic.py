"""Build a schematic sheet from Python data through konnect.

    from pcbkit import schematic as sch
    sch.configure("boards/controller/", power={"VLED": ("power:VCC", "VLED", 90)})
    comps = [sch.R("R1", "10k", 50, 40, "SDA", "+3V3"), sch.C("C1", "100nF", 60, 40, "+3V3"), ...]
    sch.build("mcu.kicad_sch", base=100, comps=comps, hier={"SDA": "bidirectional"})

A project keeps a `scripts/builder.py` that configures this module and then *becomes* it, so legacy scripts that do
`import builder; builder.P = ...` and `from builder import *` keep working:

    from pcbkit import schematic as _m
    _m.configure(ROOT + "boards/controller/", power={...}); _m.ROOT = ROOT
    sys.modules[__name__] = _m

A component is a dict: ref, lib, val, fp, x, y, rot, lcsc, fields, pins ({pin: net}), optional unit/mirror.
A pin's net is a power symbol (in POWER), "NC", a hierarchical label (in `hier`), a global label (in `glob`), or a
local net, which is wired with batch_connect_to_net. Power symbols are placed once per pin position and numbered
from `base`. Harvested from the projects' builder.py; the sign, siebenuhr and spotlight variants are merged.
"""
import os
import re

from .konnect import Konnect  # noqa: F401  (re-exported: sheet scripts open their own client for extra calls)
from .parts import FP, capacitor, resistor

# footprint names, re-exported for sheet scripts that build parts by hand
R0402, R0603, C0402, C0603, C0805, C1206 = (FP[k] for k in ("R0402", "R0603", "C0402", "C0603", "C0805", "C1206"))

P = ""  # project (sheet) directory with trailing slash; set with configure(), or assign P directly
# net -> (symbol, value override, native rotation of the symbol's pin)
POWER = {"GND": ("power:GND", None, 270), "+3V3": ("power:+3V3", None, 90), "+5V": ("power:+5V", None, 90),
         "+12V": ("power:+12V", None, 90), "VBUS": ("power:VBUS", None, 90)}


def configure(project_dir, power=None):
    """Set the sheet directory and add project power nets, e.g. {"VIN": ("power:VCC", "VIN", 90)}."""
    global P
    P = project_dir if project_dir.endswith("/") else project_dir + "/"
    if power:
        POWER.update(power)


def sheet_path(sheet):
    """P + sheet, or the sheet itself when it is already an absolute path."""
    return sheet if os.path.isabs(sheet) else P + sheet


def R(ref, val, x, y, p1, p2, rot=0, pkg="0402"):
    fp, lcsc = resistor(val, pkg)
    return dict(ref=ref, lib="Device:R", val=val, fp=fp, x=x, y=y, rot=rot, lcsc=lcsc, pins={"1": p1, "2": p2})


def C(ref, val, x, y, p1, p2="GND", rot=0, pkg=None):
    fp, lcsc = capacitor(val, pkg)
    return dict(ref=ref, lib="Device:C", val=val, fp=fp, x=x, y=y, rot=rot, lcsc=lcsc, pins={"1": p1, "2": p2})


def P2(ref, lib, val, fp, lcsc, x, y, p1, p2, rot=0, fields=None, mpn=None):
    """Generic 2-pin part (R, C, L, D, ferrite). fields: extra symbol fields; mpn sets the MPN field."""
    f = dict(fields or {})
    if mpn:
        f["MPN"] = mpn
    return dict(ref=ref, lib=lib, val=val, fp=fp, x=x, y=y, rot=rot, lcsc=lcsc, fields=f, pins={"1": p1, "2": p2})


def build(sheet, base, comps, hier, wires=(), junctions=(), extra_power=(), skip=(), glob=None):
    """Place comps on P+sheet, wire their pins, annotate LCSC and other fields. Returns pin locations."""
    glob = glob or {}
    S = sheet_path(sheet)
    k = Konnect()
    k.load("sch_batch", "sch_components", "sch_wiring")
    place = []
    for c in comps:
        e = {"lib_id": c["lib"], "reference": c["ref"], "x": c["x"], "y": c["y"], "rotation": c.get("rot", 0)}
        if c.get("val"):
            e["value"] = c["val"]
        if c.get("fp"):
            e["footprint"] = c["fp"]
        if c.get("mirror"):
            e["mirror"] = c["mirror"]
        if c.get("unit"):
            e["unit"] = c["unit"]
        place.append(e)
    r = k.call("batch_place_components", {"schematic": S, "components": place})
    assert not r["errors"] and not r["failures"], r
    locs = k.call("batch_get_schematic_pin_locations", {"schematic": S, "references": sorted({c["ref"] for c in comps})})
    L = {}
    for comp in locs["components"]:
        for p in comp["pins"]:
            L[(comp["reference"], p["number"])] = (round(p["x"], 2), round(p["y"], 2), p["orientation_degrees"])
    pw, nc, local, seen, hier_done, n = [], [], {}, set(), set(), [0]

    def pwr(net, x, y, th):
        if (x, y) in seen:
            return
        seen.add((x, y))
        lib, val, nat = POWER[net]
        n[0] += 1
        e = {"lib_id": lib, "reference": f"#PWR{base + n[0]:04d}", "x": x, "y": y, "rotation": (th - nat) % 360}
        if val:
            e["value"] = val
        pw.append(e)

    for c in comps:
        for pin, net in c["pins"].items():
            if (c["ref"], pin) in skip or net is None:
                continue
            x, y, th = L[(c["ref"], pin)]
            if net == "NC":
                if (x, y) not in seen:
                    seen.add((x, y))
                    nc.append({"x": x, "y": y})
            elif net in POWER:
                pwr(net, x, y, th)
            elif net in hier and net not in hier_done and (x, y) not in seen:
                seen.add((x, y))
                hier_done.add(net)
                k.call("add_schematic_net_label", {"schematic": S, "net": net, "label_type": "hierarchical_label",
                                                   "shape": hier[net], "x": x, "y": y, "rotation": th})
            elif net in glob:
                if (x, y) in seen:
                    continue
                seen.add((x, y))
                k.call("add_schematic_net_label", {"schematic": S, "net": net, "label_type": "global_label",
                                                   "shape": glob[net], "x": x, "y": y, "rotation": th})
            else:
                if (x, y) in seen:
                    continue
                seen.add((x, y))
                local.setdefault(net, []).append({"reference": c["ref"], "pin_number": pin})
    for net, x, y, th in extra_power:
        pwr(net, x, y, th)
    if pw:
        r = k.call("batch_place_components", {"schematic": S, "components": pw})
        assert not r["errors"] and not r["failures"], r
    if nc:
        k.call("batch_add_no_connect", {"schematic": S, "positions": nc})
    for net, pins in local.items():
        k.call("batch_connect_to_net", {"schematic": S, "net_name": net, "pins": pins})
    if wires:
        k.call("batch_add_wire", {"schematic": S, "wires": [dict(x1=a, y1=b, x2=c_, y2=d) for a, b, c_, d in wires]})
    if junctions:
        k.call("batch_add_junction", {"schematic": S, "positions": [dict(x=a, y=b) for a, b in junctions]})
    done = set()
    for c in comps:
        if c["ref"].startswith("#") or c["ref"] in done:
            continue
        done.add(c["ref"])
        f = {"LCSC": c.get("lcsc", "")}
        f.update(c.get("fields", {}))
        for key, val in f.items():
            k.call("add_component_annotation", {"schematic": S, "reference": c["ref"], "key": key, "value": val})
    print(sheet, "placed", len(comps), "global", len(glob), "power", len(pw), "nc", len(nc), "local nets", len(local),
          "hier", len(hier_done), "missing hier", set(hier) - hier_done)
    return L


def reset(sheet, paper="A3"):
    """Blank a child sheet in place, keeping its own uuid so hierarchy links survive."""
    S = sheet_path(sheet)
    txt = open(S).read()
    uuid = re.search(r'\(uuid "?([0-9a-f-]+)"?\)', txt).group(1)
    open(S, "w").write(f'(kicad_sch\n\t(version 20260306)\n\t(generator "eeschema")\n\t(generator_version "10.0")\n'
                       f'\t(uuid "{uuid}")\n\t(paper "{paper}")\n\t(lib_symbols)\n\t(embedded_fonts no)\n)\n')


def labels(sheet, items):
    """Plain net labels: items = [(net, x, y, rotation), ...]."""
    k = Konnect()
    k.load("sch_wiring")
    for net, x, y, rot in items:
        k.call("add_schematic_net_label", {"schematic": sheet_path(sheet), "net": net, "label_type": "net_label",
                                           "x": x, "y": y, "rotation": rot})


def mark_dnp(sheet, refs):
    """Set (dnp yes) and (in_bom no) on placed symbols by reference (text edit; run after build).
    Patches the symbol header between "(symbol" and its Reference property, so it works however the
    symbol's closing parens are laid out (add_component_annotation leaves '))' on the last field's line)."""
    S = sheet_path(sheet)
    s = open(S).read()
    for ref in refs:
        i = s.index(f'(property "Reference" "{ref}"')
        a = s.rindex("\n\t(symbol\n", 0, i)
        head = s[a:i].replace("(in_bom yes)", "(in_bom no)").replace("(dnp no)", "(dnp yes)")
        assert "(dnp yes)" in head and "(in_bom no)" in head, f"mark_dnp: {ref}"
        s = s[:a] + head + s[i:]
    open(S, "w").write(s)
