"""The comparison page: each board's old routing and the router's, from `pcbkit reroute`'s runs, on the same measures.

    pcbkit reroute page CONFIG.json

CONFIG: {"out": DIR, "name": ..., "eyebrow": ..., "lead": ..., "sumnote": ..., "notes": [{"state": "good"|"mixed"|"open",
         "label": ..., "title": ..., "text": ...}], "boards": [{"name": NAME, "title": ..., "sub": ..., "old": "who routed it
         first", "note": ...}]}
Writes DIR/svg/<board>-old.svg and <board>-new.svg, and DIR/index.html (page.html with the data)."""
import sys, json
from pathlib import Path
from . import WORK, draw

HERE = Path(__file__).resolve().parent

MEASURES = [("vias", "Vias"), ("mm", "Track, mm"), ("back_mm", "On B.Cu, mm"), ("corners90", "90° corners"), ("jogs", "Jogs"),
            ("tees", "T joins"), ("necks", "Narrow runs"), ("via_in_ic", "Vias in pin areas"), ("hug_mm", "Hugging pads, mm"),
            (("under_mm", "under_power_mm"), "Under ICs, mm"), ("detour_mm", "Detours, mm"), ("crossbacks", "Cross-backs")]


def errors(c):
    """DRC findings that matter on a finished board, by kind"""
    return {k: v for k, v in sorted(c.items()) if k not in ("via_dangling", "track_dangling", "starved_thermal", "isolated_copper")}


def main():
    cfg = json.load(open(sys.argv[1])); out = Path(cfg["out"]); (out / "svg").mkdir(parents=True, exist_ok=True)
    data = {k: cfg.get(k, "") for k in ("name", "eyebrow", "lead", "sumnote")}; data["notes"] = cfg.get("notes", []); data["boards"] = []
    for b in cfg["boards"]:
        run = WORK / "boards" / b["name"]; s = json.load(open(run / "summary.json"))
        olds = json.load(open(run / "job/hand.json")); news = json.load(open(run / "final.json"))
        box = draw.bbox([p for poly in olds["outline"] for p in poly[0]]); box = (box[0] - 0.5, box[1] - 0.5, box[2] + 0.5, box[3] + 0.5)
        for which, g in (("old", olds), ("new", news)):
            (out / "svg" / f"{b['name']}-{which}.svg").write_text(draw.svg(g, box, 12, labels=False, zones=False))
        L = s["lint"]
        val = lambda d, k: round(sum(d.get(x, 0) for x in k), 1) if isinstance(k, tuple) else d.get(k, 0)
        rows = [[label, val(L["old"], k), val(L["new"], k)] for k, label in MEASURES]
        data["boards"].append(dict(b, id=b["name"], nets=s["nets"], failed=s["failed"], rows=rows, secs=s["router_s"],
                                   drc=dict(old=errors(s["drc"]["old"]), new=errors(s["drc"]["new"])), aspect=(box[2] - box[0]) / (box[3] - box[1])))
    page = (HERE / "page.html").read_text()
    for k in ("name", "eyebrow"): page = page.replace(f"__{k.upper()}__", cfg[k])
    (out / "index.html").write_text(page.replace("__DATA__", json.dumps(data).replace("</", "<\\/")))
    print(f"{len(data['boards'])} boards -> {out}")


if __name__ == "__main__":
    main()
