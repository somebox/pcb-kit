"""What a released board carries (docs/CONVENTIONS.md), checked line by line.

  pcbkit standard [PCB ...] [--json]

For every PCB of the project that is released (or those named, by key, path or folder, as `pcbkit release` takes
them; a .kicad_pcb outside any project is read with the defaults), what a tool can check of the standard:

  check             level   finds
  version           error   no silkscreen text prints the version: ${REVISION}, which release fills from the title
                            block (a version typed in, which release restamps, is a note)
  version-print     error   v${REVISION} (prints vv1.0) and ${CURRENT_DATE} (the plot's day); never waived
  date              note    no ${ISSUE_DATE} on the silkscreen, or a date typed into a text release does not stamp
  title-block       error   neither the board nor its root schematic has a title; a sheet under the root without a
                            title block, or with one that has no title
  labels            note    a connector (J), jumper (JP), switch (SW), battery holder (BT) or LED with no silkscreen
                            text within label_distance that names it: a board text, or its own value shown; its
                            reference does not count. The text is on the part's side, or on either side of a
                            through-hole part
  test-pads         note    a rail without a test pad (a footprint TP*, TestPoint*, or with a test-point pad). The
                            rails are [standard.rails] for the board in pcbkit.toml, else the nets its schematic's
                            power symbols name, ground among them
  test-pad-labels   note    a test pad with no such text, whose value is not shown and whose reference no board
                            text names (a legend: "TP1 RUN", "GND TP8,12,13"). A number drawn as a polygon beside the
                            pad is not a text: a board with such badges waives the check and says so
  mounting          note    a mounting hole (every pad unplated, or a MountingHole* footprint) with neither a
                            courtyard nor a rule area over it; a board with no hole at all
  lcsc              error   a part the assembler places ([release] placed), not DNP, with no LCSC number: `pcbkit fab`
                            leaves it out of the BOM and the placement file
  extended          note    a placed JLC Extended part (konnect's parts cache) that BOM.md does not name

and, as lines to look at, what only a person can check: the board's name, voltages and polarity, what a jumper
position does, pin names, polarity marks, and how many findings `pcbkit sch-lint` has.

An error stops `pcbkit release` (its profile's checks include "standard" unless the project lists others); a note
never does. A prototype leaves a finding for a later revision with a waiver in pcbkit.toml, which must say why:

  [[standard.waive]]
  board = "node"                  # a PCB's key, or a glob on its path or folder; without it, every board
  check = "test-pads"
  items = ["+5V", "VBUS"]         # the references, nets or sheets it covers; without it, the whole check
  note  = "on J7 pins 2 and 3; pads in v1.3"

The report shows each waived finding with its note, and names a waiver that matches nothing any more. A waiver
without a note is an error. Exit status 1 when an error is left. The texts and title blocks are read from the files;
the geometry needs pcbnew.
"""
import argparse
import collections
import fnmatch
import json
import math
import os
import re
import sys

from .. import sexp
from ..release import versions as V

# check: (the standard's line it belongs to, whether a waiver may cover it)
CHECKS = collections.OrderedDict([
    ("version-print", ("name, version, date", False)),
    ("version", ("name, version, date", True)),
    ("date", ("name, version, date", True)),
    ("title-block", ("title blocks", True)),
    ("labels", ("labels", True)),
    ("test-pads", ("test pads", True)),
    ("test-pad-labels", ("test pads", True)),
    ("mounting", ("mounting", True)),
    ("lcsc", ("parts", True)),
    ("extended", ("parts", True)),
    ("waiver", ("waivers", False)),
])
LEVELS = ("error", "note", "waived", "ok", "look")
DEFAULTS = {"label_distance": 2.0, "bom": "", "rails": {}, "waive": []}
LABELLED = (("JP", "jumper"), ("J", "connector"), ("SW", "switch"), ("BT", "battery holder"), ("LED", "LED"))
TYPED_DATE = re.compile(r"(?<![\w.-])(?:(?:19|20)\d{2}-\d{2}(?:-\d{2})?|\d{1,2}[./]\d{1,2}[./](?:19|20)\d{2}"
                        r"|\d{1,2}[./](?:19|20)\d{2})(?![\w-])")
LOOK = (("name, version, date", "the board's name is on the silkscreen, once"),
        ("labels", "the voltage range and polarity at each power input; what each jumper position does and its "
                   "default; pin 1 and every pin's signal at each connector; buttons and LEDs named for what they do"),
        ("silkscreen", "every polarised part's mark is visible beside the part (text size and clearances are DRC's)"),
        ("documents", "PRODUCTION.md's status lines, briefing and bring-up list (pcbkit production --check)"))


def finding(check, level, text, item="", detail=""):
    return dict(check=check, level=level, item=item, detail=detail, text=text)


def ok(check, text):
    return dict(check=check, level="ok", item="", detail="", text=text)


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _natural(s):
    return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", s)]


# ---- from the files' text --------------------------------------------------------------------------------------------
def version_findings(text):
    """The version and date on the silkscreen, from a board file's text."""
    rev = V.title_rev(text)
    silk = [s for s, layer in V.plotted_texts(text) if layer.endswith(".SilkS")]
    wrong, typed = V.label_problems(text, rev)
    out = [finding("version-print", "error", w) for w in wrong]
    with_rev = [s for s in silk if "${REVISION}" in s]
    right = [s for s in with_rev if not re.search(r"(?i)v\$\{REVISION\}", s)]
    if right:
        out.append(ok("version", "the silkscreen prints the version: " + ", ".join(V._quote(s) for s in right[:3])))
    elif with_rev:
        pass                           # only as v${REVISION}: the error above says it
    elif typed:
        out.append(finding("version", "note", "the version is typed into the silkscreen (" + ", ".join(typed[:3])
                           + "): release restamps a text that reads the old label, ${REVISION} follows the title "
                           "block by itself"))
    else:
        out.append(finding("version", "error", "no silkscreen text prints the version: add one with ${REVISION} "
                           "(and ${ISSUE_DATE}), which release fills from the title block"))
    word = rf"(?<![\w.]){re.escape(rev)}(?![\w.])" if rev.strip() else None

    def stamped(s):                    # a text release rewrites: it carries the label, and its ISO dates go with it
        return bool(re.search(V.LABEL_IN_TEXT, s) or (word and re.search(word, s)))
    dated = [s for s in silk if "${ISSUE_DATE}" in s]
    loose = [s for s in silk if TYPED_DATE.search(s) and "${ISSUE_DATE}" not in s
             and not (stamped(s) and re.search(V.DATE, s))]
    for s in loose:
        out.append(finding("date", "note", "a date typed into the silkscreen, which release does not update: write "
                           "${ISSUE_DATE}", V._quote(s)))
    if dated:
        out.append(ok("date", "the silkscreen prints the date: " + ", ".join(V._quote(s) for s in dated[:3])))
    elif not loose and not any(stamped(s) and re.search(V.DATE, s) for s in silk):
        out.append(finding("date", "note", "no date on the silkscreen: add ${ISSUE_DATE}, the release's date"))
    return out


def title_findings(pcb_path):
    """A title on the board or its root schematic, and a title block with a title on every sheet under the root."""
    from ..release import freeze
    root = os.path.splitext(pcb_path)[0] + ".kicad_sch"
    board = V.title_field(_read(pcb_path), "title").strip()
    if not os.path.exists(root):
        return [ok("title-block", "the board has a title (it has no schematic)") if board else
                finding("title-block", "error", "no title in its title block", os.path.basename(pcb_path))]
    out = []
    if not board and not V.title_field(_read(root), "title").strip():
        # release gives each the other's title, so one of the two is enough
        out.append(finding("title-block", "error", "neither the board nor its root schematic has a title in its "
                           "title block", os.path.basename(pcb_path)))
    place, _, missing = freeze.sheets(root)
    base = os.path.dirname(os.path.abspath(pcb_path))
    sheets = sorted(p for p in place if os.path.realpath(p) != os.path.realpath(root))
    for p in sheets:
        text = _read(p)
        if not V.title_block(text):
            out.append(finding("title-block", "error", "no title block", os.path.relpath(p, base)))
        elif not V.title_field(text, "title").strip():
            out.append(finding("title-block", "error", "a title block without a title", os.path.relpath(p, base)))
    if not out:
        out.append(ok("title-block", f"a title on the board and on {len(sheets) + 1} schematic "
                                     f"sheet{'s' * bool(sheets)}"))
    return out


def schematic_rails(pcb_path):
    """The nets a board's schematic names with power symbols (references #PWR..., a PWR_FLAG aside), in the order
    found: its rails, ground among them."""
    from ..release import freeze
    root = os.path.splitext(pcb_path)[0] + ".kicad_sch"
    out = []
    if not os.path.exists(root):
        return out
    for p in freeze.sheets(root)[0]:
        tree = sexp.parse(_read(p))
        for s in sexp.find(tree if isinstance(tree, list) else [], "symbol"):
            lib = sexp.first(s, "lib_id")
            lib = str(lib[1]) if lib and len(lib) > 1 else ""
            ref, value = sexp.prop(s, "Reference") or "", sexp.prop(s, "Value") or ""
            if (ref.startswith("#PWR") or lib.startswith("power:")) and not lib.endswith("PWR_FLAG") \
                    and not ref.startswith("#FLG") and value and value not in out:
                out.append(value)
    return out


def sch_lint_line(pcb_path):
    """How many findings pcbkit sch-lint has on the board's schematic, as a line to look at; None without one."""
    from . import sch_lint
    root = os.path.splitext(pcb_path)[0] + ".kicad_sch"
    if not os.path.exists(root):
        return None
    try:
        n, sheets = collections.Counter(), sch_lint.sheets([root])
        for s in sheets:
            n.update(kind for kind, _, _ in sch_lint.lint(s)[0] if kind != "title-block")
    except Exception as e:             # a sheet it can't read is no reason to lose the rest of the report
        return f"pcbkit sch-lint could not read the schematic ({e})"
    if not n:
        return f"pcbkit sch-lint finds nothing on {len(sheets)} sheet{'s' * (len(sheets) > 1)}"
    return (f"pcbkit sch-lint has {sum(n.values())} findings on {len(sheets)} sheet{'s' * (len(sheets) > 1)} ("
            + ", ".join(f"{c} {k}" for k, c in n.most_common()) + "): each fixed or explained")


def find_bom(root, pcb_path, named=""):
    """The BOM.md a board's Extended parts are looked up in: the one named in pcbkit.toml, else the first found from
    the board's folder up to the project's root, then docs/BOM.md there."""
    if named:
        p = os.path.join(root, named)
        return p if os.path.exists(p) else None
    d, root = os.path.dirname(os.path.abspath(pcb_path)), os.path.abspath(root)
    while True:
        if os.path.exists(os.path.join(d, "BOM.md")):
            return os.path.join(d, "BOM.md")
        if d == root or os.path.dirname(d) == d or not d.startswith(root):
            break
        d = os.path.dirname(d)
    p = os.path.join(root, "docs", "BOM.md")
    return p if os.path.exists(p) else None


def extended_findings(parts, cache, bom_path, root=""):
    """The placed Extended parts and whether BOM.md names each. parts: {LCSC: (references, MPN)}; cache: an object
    whose get(LCSC) gives dict(extended=, mpn=) or None, with .db None when there is no parts cache."""
    if getattr(cache, "db", True) is None:
        return [finding("extended", "note", "no JLC parts cache (konnect's ~/.konnect/jlcpcb.db), so the Extended "
                        "parts are not known")]
    ext, unknown = [], []
    for lcsc, (refs, mpn) in sorted(parts.items(), key=lambda kv: _natural(kv[1][0][0]) if kv[1][0] else []):
        r = cache.get(lcsc)
        if r is None:
            unknown.append(lcsc)
        elif r["extended"]:
            ext.append((lcsc, refs, mpn or r.get("mpn") or ""))
    out = []
    bom = _read(bom_path) if bom_path else ""
    where = os.path.relpath(bom_path, root) if bom_path and root else bom_path or ""
    named = []

    def refs_of(refs):
        return ", ".join(refs[:4]) + (f" and {len(refs) - 4} more" if len(refs) > 4 else "")
    if ext and not bom_path:           # one finding for the project: there is nowhere to look each part up
        out.append(finding("extended", "note", f"{len(ext)} JLC Extended part{'s' * (len(ext) > 1)} placed, and the "
                           "project has no BOM.md to give each its reason and the fee it adds: "
                           + ", ".join(f"{lcsc} ({refs_of(refs)})" for lcsc, refs, _ in ext)))
        ext = []
    for lcsc, refs, mpn in ext:
        hit = re.search(rf"(?<![\w]){re.escape(lcsc)}(?!\w)", bom) or \
            (len(mpn) >= 4 and re.search(re.escape(mpn), bom, re.I))
        if hit:
            named.append(lcsc)
        else:
            out.append(finding("extended", "note", f"a JLC Extended part that {where} does not name: its reason, and "
                               "the fee it adds, go there", lcsc, refs_of(refs) + (f"; {mpn}" if mpn else "")))
    if unknown:
        out.append(finding("extended", "note", f"{len(unknown)} placed part{'s' * (len(unknown) > 1)} not in the JLC "
                           "parts cache, so not known to be Basic or Extended: " + ", ".join(unknown[:8])
                           + (" ..." if len(unknown) > 8 else "")))
    if not out:
        out.append(ok("extended", (f"{len(ext)} Extended part{'s' * (len(ext) != 1)}, each named in {where}"
                                   if ext else "no Extended part placed")))
    elif named:
        out.append(ok("extended", f"{len(named)} Extended part{'s' * (len(named) != 1)} named in {where}"))
    return out


# ---- waivers ---------------------------------------------------------------------------------------------------------
def waivers_for(waive, pcb):
    """The [[standard.waive]] entries that are about this PCB (its key, or a glob on its path or folder; all of them
    without a board)."""
    def about(w):
        b = w.get("board")
        return not b or b == pcb["key"] or fnmatch.fnmatch(pcb["path"], b) or fnmatch.fnmatch(pcb.get("folder", ""), b)
    return [w for w in waive if about(w)]


def _waiver_name(w):
    items = w.get("items") or []
    return f"{w.get('check', '?')}" + (f" ({', '.join(map(str, items))})" if items else "")


def apply_waivers(findings, waive):
    """The findings with those a waiver covers marked waived (level "waived", its note, and "was", the level it had),
    then what is wrong with the waivers themselves: one without a note or for a check that is none or can't be waived
    (errors), one that matches nothing any more (a note)."""
    out, good = [], []
    for w in waive:
        check, items = w.get("check", ""), w.get("items")
        if check not in CHECKS:
            out.append(finding("waiver", "error", f"a waiver names the check {check!r}, which is none of: "
                               + ", ".join(c for c, (_, can) in CHECKS.items() if can)))
        elif not CHECKS[check][1]:
            out.append(finding("waiver", "error", f"{check} cannot be waived: it is fixed", _waiver_name(w)))
        elif not str(w.get("note", "")).strip():
            out.append(finding("waiver", "error", "a waiver without a note: say what is left out and why",
                               _waiver_name(w)))
        elif items is not None and (not isinstance(items, list) or not all(isinstance(i, str) for i in items)):
            out.append(finding("waiver", "error", "a waiver's items are a list of references, nets or sheets",
                               _waiver_name(w)))
        else:
            good.append(w)
    used = collections.defaultdict(set)        # waiver's index -> the items it covered ("" for a whole check)
    done = []
    for f in findings:
        if f["level"] in ("error", "note"):
            for i, w in enumerate(good):
                if w["check"] == f["check"] and (not w.get("items") or f["item"] in w["items"]):
                    used[i].add(f["item"] if w.get("items") else "")
                    f = dict(f, level="waived", was=f["level"], note=str(w["note"]).strip())
                    break
        done.append(f)
    for i, w in enumerate(good):
        stale = [x for x in w["items"] if x not in used[i]] if w.get("items") else ([] if used[i] else [""])
        if stale:
            what = f"{w['check']}" + (f" ({', '.join(stale)})" if stale != [""] else "")
            out.append(finding("waiver", "note", "a waiver that matches nothing any more: remove it", what))
    return done + out


# ---- from the board, in pcbnew --------------------------------------------------------------------------------------
def _box(pcbnew, r):
    return (pcbnew.ToMM(r.GetLeft()), pcbnew.ToMM(r.GetTop()), pcbnew.ToMM(r.GetRight()), pcbnew.ToMM(r.GetBottom()))


def _merge(boxes):
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def _gap(a, b):
    return math.hypot(max(a[0] - b[2], b[0] - a[2], 0), max(a[1] - b[3], b[1] - a[3], 0))


def _text_box(pcbnew, t):
    """A text's box in mm: its strokes (what is printed), not the larger cell."""
    try:
        return _box(pcbnew, t.GetEffectiveTextShape().BBox())
    except Exception:                  # a text box, or a text pcbnew gives no shape for
        return _box(pcbnew, t.GetBoundingBox())


def silk_texts(pcbnew, board):
    """Every text the silkscreen prints: dicts with box (mm), text, side ("F" or "B"), kind (board, reference, value,
    field, or text for one drawn in a footprint) and owner (the footprint's reference, None for a board text)."""
    side = {pcbnew.F_SilkS: "F", pcbnew.B_SilkS: "B"}
    out = []

    def add(t, kind, owner):
        if t.GetLayer() in side and t.IsVisible() and t.GetText().strip():
            out.append(dict(box=_text_box(pcbnew, t), text=t.GetText(), side=side[t.GetLayer()], kind=kind, owner=owner))
    for d in board.GetDrawings():
        if d.GetClass() in ("PCB_TEXT", "PCB_TEXTBOX"):
            add(d, "board", None)
    for fp in board.GetFootprints():
        ref = fp.GetReference()
        for f in fp.GetFields():
            add(f, "reference" if f.IsReference() else "value" if f.IsValue() else "field", ref)
        for g in fp.GraphicalItems():
            if g.GetClass() in ("PCB_TEXT", "PCB_TEXTBOX"):
                add(g, "text", ref)
    return out


def part_box(pcbnew, fp):
    """Where a part is, in mm: its pads and courtyards; its drawings when it has neither."""
    boxes = [_box(pcbnew, p.GetBoundingBox()) for p in fp.Pads()]
    boxes += [_box(pcbnew, g.GetBoundingBox()) for g in fp.GraphicalItems()
              if g.GetClass() == "PCB_SHAPE" and g.GetLayer() in (pcbnew.F_CrtYd, pcbnew.B_CrtYd)]
    if not boxes:
        boxes = [_box(pcbnew, g.GetBoundingBox()) for g in fp.GraphicalItems() if g.GetClass() == "PCB_SHAPE"]
    if not boxes:
        p = fp.GetPosition()
        boxes = [(pcbnew.ToMM(p.x), pcbnew.ToMM(p.y)) * 2]
    return _merge(boxes)


def _name(fp):
    return str(fp.GetFPID().GetLibItemName())


def _prefix(ref):
    return re.match(r"[A-Za-z]*", ref).group(0).upper()


def labelled_kind(fp):
    """What a person reads a label for: "connector", "jumper", "switch", "battery holder" or "LED" by the part's
    reference (J, JP, SW, BT, LED; a D whose footprint or value says LED), else None."""
    p = _prefix(fp.GetReference())
    for prefix, kind in LABELLED:
        if p == prefix:
            return kind
    if p == "D" and (re.search(r"(?i)(?<![a-z])LED", _name(fp)) or re.search(r"(?i)(?<![a-z])LED(?![a-z])", fp.GetValue())):
        return "LED"
    return None


def is_test_pad(pcbnew, fp):
    return _prefix(fp.GetReference()) == "TP" or _name(fp).startswith("TestPoint") or \
        any(p.GetProperty() == pcbnew.PAD_PROP_TESTPOINT for p in fp.Pads())


def is_hole(pcbnew, fp):
    pads = list(fp.Pads())
    return bool(pads) and all(p.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH for p in pads) or "MountingHole" in _name(fp)


def _names_it(pcbnew, texts, fp, distance):
    """Whether a silkscreen text names the part: a board text within distance of it, on its side (either side of a
    through-hole part, which is read and wired from both), or its own value or another of its fields shown. A
    reference names nothing, and a mark drawn in the footprint (+, K, 1) is not a name."""
    ref, side = fp.GetReference(), "B" if fp.IsFlipped() else "F"
    through = any(p.GetAttribute() == pcbnew.PAD_ATTRIB_PTH for p in fp.Pads())
    box = part_box(pcbnew, fp)
    for t in texts:
        if t["kind"] in ("value", "field") and t["owner"] == ref:
            return True
        if t["kind"] == "board" and (through or t["side"] == side) and _gap(box, t["box"]) <= distance:
            return True
    return False


LIST = re.compile(r"([A-Za-z]+)(\d+(?:\s*[,/]\s*\d+)+)")


def legend_names(texts):
    """The references a board's silkscreen texts name, as a legend does: every word, and "TP8,12,13" as TP8, TP12 and
    TP13."""
    out = set()
    for t in texts:
        if t["kind"] == "board":
            out |= set(re.findall(r"[A-Za-z]+\d+", t["text"]))
            for m in LIST.finditer(t["text"]):
                out |= {m.group(1) + n for n in re.findall(r"\d+", m.group(2))}
    return out


def label_findings(pcbnew, board, texts, distance):
    out, n = [], collections.Counter()
    for fp in sorted(board.GetFootprints(), key=lambda f: _natural(f.GetReference())):
        kind = labelled_kind(fp)
        if not kind or is_test_pad(pcbnew, fp):
            continue
        n[kind] += 1
        if not _names_it(pcbnew, texts, fp, distance):
            out.append(finding("labels", "note", f"no silkscreen text within {distance:g} mm on its side that names it "
                               "(a reference names nothing)", fp.GetReference(), f"{kind}, {fp.GetValue()}"))
    if not out:
        out.append(ok("labels", (", ".join(f"{c} {k}{'s' * (c > 1) if k != 'switch' else 'es' * (c > 1)}"
                                           for k, c in n.items()) + f": a text within {distance:g} mm of each")
                      if n else "no connector, jumper, switch or LED to label"))
    return out


def test_pad_findings(pcbnew, board, texts, distance, rails, rails_from):
    """A test pad on every rail, and a name at every test pad. rails: the nets that need one; rails_from says where
    that list came from, for the report."""
    out, pads_on = [], collections.Counter()
    tps = [fp for fp in board.GetFootprints() if is_test_pad(pcbnew, fp)]
    nets = collections.defaultdict(list)       # net -> the test pads on it
    for fp in board.GetFootprints():
        for p in fp.Pads():
            if p.GetNetname():
                pads_on[p.GetNetname()] += 1
    for fp in tps:
        for net in sorted({p.GetNetname() for p in fp.Pads() if p.GetNetname()}):
            nets[net].append(fp.GetReference())
    on_board = [r for r in rails if pads_on[r]]
    if not rails:
        out.append(finding("test-pads", "note", "no rails known, so none is checked for a test pad: "
                           + rails_from + (" (test pads are on " + ", ".join(sorted(nets, key=_natural)) + ")"
                                           if nets else "")))
    else:
        bare = [r for r in on_board if r not in nets]
        for r in bare:
            out.append(finding("test-pads", "note", "a rail without a test pad", r))
        if not bare:
            out.append(ok("test-pads", f"a test pad on each of {len(on_board)} rail{'s' * (len(on_board) != 1)} ("
                                       + ", ".join(on_board) + f"; {rails_from})"))
    legend = legend_names(texts)
    shown = {t["owner"] for t in texts if t["kind"] == "reference"}
    unnamed = []
    for fp in sorted(tps, key=lambda f: _natural(f.GetReference())):
        ref = fp.GetReference()
        if not (ref in shown and ref in legend) and not _names_it(pcbnew, texts, fp, distance):
            net = next((p.GetNetname() for p in fp.Pads() if p.GetNetname()), "no net")
            unnamed.append(finding("test-pad-labels", "note", f"a test pad with no silkscreen text within {distance:g} "
                                   "mm on its side, no value shown and no legend naming it", ref, net))
    out += unnamed
    if tps and not unnamed:
        out.append(ok("test-pad-labels", f"{len(tps)} test pad{'s' * (len(tps) != 1)}, each named on the silkscreen"))
    return out


def mounting_findings(pcbnew, board):
    holes = sorted((fp for fp in board.GetFootprints() if is_hole(pcbnew, fp)), key=lambda f: _natural(f.GetReference()))
    if not holes:
        return [finding("mounting", "note", "no mounting hole (a footprint whose pads are all unplated, or a "
                        "MountingHole): LAYOUT.md says how the board is held")]
    areas = [z for z in board.Zones() if z.GetIsRuleArea()]
    out = []
    for fp in holes:
        yard = any(g.GetClass() == "PCB_SHAPE" and g.GetLayer() in (pcbnew.F_CrtYd, pcbnew.B_CrtYd)
                   for g in fp.GraphicalItems())
        at = fp.GetPosition()
        if not yard and not any(z.Outline().Contains(at) for z in areas):
            drill = max((pcbnew.ToMM(p.GetDrillSize().x) for p in fp.Pads()), default=0)
            out.append(finding("mounting", "note", "a hole with neither a courtyard nor a keep-out over it: nothing "
                               "keeps parts and copper clear of the screw head or standoff",
                               fp.GetReference() or "(no reference)", f"{drill:g} mm"))
    if not out:
        out.append(ok("mounting", f"{len(holes)} hole{'s' * (len(holes) != 1)}, each with a courtyard or a keep-out"))
    return out


def lcsc_findings(pcbnew, board, placed, assembly=True):
    """Parts the assembler places that have no LCSC number, and the placed parts by number: (findings, {LCSC:
    (references, MPN)})."""
    from ..release import jlc_fab
    if not assembly:
        return [ok("lcsc", "a bare board: nothing is placed")], {}
    is_placed = jlc_fab.selector(placed)
    parts, n = {}, 0
    for fp in sorted(board.GetFootprints(), key=lambda f: _natural(f.GetReference())):
        if fp.IsDNP() or fp.IsBoardOnly() or not is_placed(fp):
            continue
        lcsc = jlc_fab.field(fp, "LCSC")
        if lcsc:
            n += 1
            refs, _ = parts.setdefault(lcsc, ([], jlc_fab.field(fp, "MPN")))
            refs.append(fp.GetReference())
    out = [finding("lcsc", "error", "placed, but no LCSC number, so pcbkit fab leaves it out of the BOM and placement "
                   "file (give it one, or mark it 'Exclude from position files' if soldered by hand)",
                   fp.GetReference(), fp.GetValue())
           for fp in jlc_fab.unnumbered(board, is_placed)]
    if not out:
        out.append(ok("lcsc", f"{n} placed part{'s' * (n != 1)}, each with an LCSC number"))
    return out, parts


def board_findings(pcbnew, board, cfg, placed="not-excluded", assembly=True, rails=None, rails_from=""):
    """The checks that need the board in pcbnew: labels, test pads, mounting holes and LCSC numbers; and the placed
    parts by LCSC number, for the Extended parts."""
    texts = silk_texts(pcbnew, board)
    distance = float(cfg.get("label_distance", DEFAULTS["label_distance"]))
    lcsc, parts = lcsc_findings(pcbnew, board, placed, assembly)
    return (label_findings(pcbnew, board, texts, distance)
            + test_pad_findings(pcbnew, board, texts, distance, rails or [], rails_from)
            + mounting_findings(pcbnew, board) + lcsc), parts


# ---- the report -----------------------------------------------------------------------------------------------------
def check(project, pcb, cache=None):
    """Every finding for one PCB (a dict from Project.pcbs(), with abspath), waivers applied. Needs pcbnew."""
    import pcbnew
    cfg = dict(DEFAULTS, **project.cfg.get("standard", {}))
    path = pcb["abspath"]
    out = version_findings(_read(path)) + title_findings(path)
    rails = (cfg.get("rails") or {}).get(pcb["key"])
    if rails is not None:
        rails_from = "pcbkit.toml's [standard.rails]"
    else:
        rails = schematic_rails(path)
        rails_from = "the schematic's power symbols" if rails else \
            "the schematic names no net with a power symbol; list the rails in pcbkit.toml, [standard.rails]"
    board = pcbnew.LoadBoard(path)
    found, parts = board_findings(pcbnew, board, cfg, project.release["placed"], pcb["profile"]["assembly"],
                                  rails, rails_from)
    out += found
    if pcb["profile"]["assembly"]:
        if cache is None:
            from ..production import Cache
            cache = Cache()
        out += extended_findings(parts, cache, find_bom(project.root, path, cfg.get("bom", "")), project.root)
    out = apply_waivers(out, waivers_for(cfg.get("waive") or [], pcb))
    look = [dict(check="", level="look", item="", detail="", text=text, section=section) for section, text in LOOK]
    lint = sch_lint_line(path)
    if lint:
        look.append(dict(check="", level="look", item="", detail="", text=lint, section="schematic"))
    return out + look


def lines(findings):
    """The findings as the report says them: [(section, level, text)], one line for the items that share a check,
    a level, a text and a waiver's note, in the standard's order."""
    groups, order = {}, []
    for f in findings:
        key = (f["check"], f["level"], f["text"], f.get("note", ""))
        if key not in groups:
            groups[key] = []
            order.append((key, f))
        if f["item"]:
            groups[key].append(f["item"] + (f" ({f['detail']})" if f.get("detail") else ""))
    sections = list(dict.fromkeys(s for s, _ in CHECKS.values()))
    out = []
    for (check, level, text, note), f in order:
        section = CHECKS[check][0] if check in CHECKS else f.get("section", "")
        items = ", ".join(groups[(check, level, text, note)])
        said = (f"{note} (waives: {text}" + (f": {items})" if items else ")")) if level == "waived" else \
            text + (f": {items}" if items else "")
        out.append((sections.index(section) if section in sections else len(sections), LEVELS.index(level), len(out),
                    section, level, said))
    return [(section, level, said) for _, _, _, section, level, said in sorted(out)]


def count(findings):
    n = collections.Counter(f["level"] for f in findings)
    return {k: n[k] for k in ("error", "note", "waived")}


def summary(findings):
    n = count(findings)
    return ", ".join(f"{n[k]} {k}{'s' * (n[k] != 1 and k != 'waived')}" for k in ("error", "note", "waived") if n[k]) \
        or "nothing found"


def report(path, findings):
    out = [f"{path}: what a released board carries (docs/CONVENTIONS.md)"]
    last = None
    for section, level, said in lines(findings):
        if level == "look":
            continue
        out.append(f"  {section if section != last else '':20s} {level:7s} {said}")
        last = section
    for section, level, said in lines(findings):
        if level == "look":
            out.append(f"  {'to look at' if last != 'to look at' else '':20s} {section}: {said}")
            last = "to look at"
    out.append(f"  {summary(findings)}")
    return "\n".join(out)


def targets(project, names):
    """The PCBs to check: those named (in the project, or a .kicad_pcb outside it, read with the defaults), else
    every one the project releases."""
    from ..config import DEFAULT_PROFILE
    if not names:
        return [p for p in project.pcbs() if not p["excluded"] and p["profile"]["release"]]
    out = []
    for n in names:
        try:
            out.append(project.pcb(n))
        except SystemExit:
            if not (n.endswith(".kicad_pcb") and os.path.isfile(n)):
                raise
            out.append(dict(key=os.path.splitext(os.path.basename(n))[0], path=os.path.abspath(n),
                            folder=os.path.dirname(os.path.abspath(n)), sub="", profile=dict(DEFAULT_PROFILE),
                            excluded=False, outside=True))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pcbkit standard", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pcbs", nargs="*", help="PCBs by key, path or folder (default: every PCB the project releases)")
    ap.add_argument("--json", action="store_true", help="the findings as JSON, for pcbkit release and for CI")
    a = ap.parse_args(argv)
    from ..config import Project
    from ..kicad import ensure_pcbnew
    ensure_pcbnew()
    project = Project()
    results, failed = [], False
    for pcb in targets(project, a.pcbs):
        pcb["abspath"] = pcb["path"] if os.path.isabs(pcb["path"]) else os.path.join(project.root, pcb["path"])
        found = check(project, pcb)
        failed = failed or any(f["level"] == "error" for f in found)
        shown = project.rel(pcb["abspath"]) if not pcb.get("outside") else pcb["abspath"]
        results.append(dict(pcb=shown, key=pcb["key"], findings=found, **count(found)))
        if not a.json:
            print(report(shown, found))
    if a.json:
        print(json.dumps(results, indent=1))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
