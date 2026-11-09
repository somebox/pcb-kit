"""Board versions: semver in the data ("1.0.0"), a label on the board ("v1.0", or "v1.0.1" once there is a patch),
change fingerprints that ignore the version and date stamps, and stamping a new version into the KiCad files.

Where a version is written on a board:
- the title block's rev (and its date) in the .kicad_pcb and in the root .kicad_sch, whether KiCad wrote the block
  one field a line or a generator wrote it on one line; a file without one gets one after its (paper ...);
- any silkscreen text (gr_text) that carries the old label as a word, e.g. "v1.0" on a front tag or
  "<name> v1.0 2026-10-19" on the back; a date in such a text becomes the release date.
Each stamp must change exactly what it means to: the masked text before and after must be identical.

Fab output is compared by fab_fingerprint, a hash. When the hash differs, fab_gap compares the files themselves with a
release's: zone fills (Gerber regions) whose vertices lie within ZONE_TOLERANCE of the release's are the same copper,
since a KiCad that refills zones a little differently moves them by a nanometre or drops a vertex.

The circuit (circuit(), circuit_fingerprint(), circuit_changes()) is the board's netlist and BOM as the PCB has them:
what decides between a minor version (the circuit changed) and a patch (only the layout did).
"""
import csv
import datetime
import difflib
import glob
import hashlib
import io
import json
import math
import os
import re
import zipfile

from .. import tomlw

SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
REV = re.compile(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?")
LABEL_IN_TEXT = r"(?<![\w.])v\d+(?:\.\d+){1,2}(?![\w.])"
DATE = r"\d{4}-\d{2}-\d{2}"
TEXT = re.compile(r'(\(gr_text ")((?:[^"\\]|\\.)*)(")')
STR = r'"(?:[^"\\]|\\.)*"'
STAMP = re.compile(r'\s*\((?:rev|date) ' + STR + r'\)')
HEADER = ("%TF", "G04", ";", "%TA")    # Gerber and drill header comments and attributes: dates, the project ID and rev
ZONE_TOLERANCE = 0.005                 # mm: KiCad's own default maximum error for arcs and zone fills
VERTEX = re.compile(r"X(-?\d+)Y(-?\d+)D0([12])\*$")


def parse(v):
    m = SEMVER.match(str(v))
    if not m:
        raise ValueError(f"not a version (major.minor.patch, as a quoted string): {v!r}")
    return tuple(int(x) for x in m.groups())


def fmt(t):
    return "%d.%d.%d" % t


def label(v, style="short"):
    ma, mi, pa = parse(v)
    return f"v{ma}.{mi}" if style == "short" and pa == 0 else f"v{ma}.{mi}.{pa}"


def bump(v, part="patch"):
    ma, mi, pa = parse(v)
    return fmt((ma + 1, 0, 0) if part == "major" else (ma, mi + 1, 0) if part == "minor" else (ma, mi, pa + 1))


def adopt(rev):
    """The version a board's title-block rev reads as ("v1.0" -> 1.0.0, "v3" -> 3.0.0, "0.1-draft" -> 0.1.0)."""
    m = REV.match(rev.strip())
    return fmt(tuple(int(x or 0) for x in m.groups())) if m and rev.strip() else None


def title_block(text):
    """(start, end) of the file's title block, found by its parentheses (one field a line, or all on one line), or
    None."""
    m = re.search(r"\(title_block\b", text)
    if not m:
        return None
    depth, quoted, j = 0, False, m.start()
    while j < len(text):
        ch = text[j]
        if quoted:
            if ch == "\\":
                j += 1
            elif ch == '"':
                quoted = False
        elif ch == '"':
            quoted = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return m.start(), j + 1
        j += 1
    return None


def title_rev(text):
    b = title_block(text)
    r = re.search(r'\(rev "([^"]*)"\)', text[b[0]:b[1]]) if b else None
    return r.group(1) if r else ""


def _label_texts(text, label, date):
    """Every silkscreen text that carries a version: its labels and dates replaced (None: masked)."""
    def gr(m):
        s = m.group(2)
        if re.search(LABEL_IN_TEXT, s):
            s = re.sub(DATE, date or "#", re.sub(LABEL_IN_TEXT, label or "v#", s))
        return m.group(1) + s + m.group(3)
    return TEXT.sub(gr, text)


# ---- fingerprints ---------------------------------------------------------------------------------------------------
def mask(text):
    """The file with its version and date stamps blanked: the title block's rev and date (a block left empty counts as
    none), and the version label and dates in any silkscreen text that carries a version."""
    b = title_block(text)
    if b:
        s, e = b
        block = STAMP.sub("", text[s:e])
        if re.fullmatch(r"\(title_block\s*\)", block):
            line = text.rfind("\n", 0, s)
            s = line if line >= 0 and not text[line:b[0]].strip() else s
            block = ""
        text = text[:s] + block + text[e:]
    return _label_texts(text, None, None)


def neutral(text, label="v0.0", date="2000-01-01"):
    """The file with neutral version and date stamps, so that two versions of a board fab the same; or with a given
    label and date, as a release stamped it."""
    b = title_block(text)
    if b:
        block = re.sub(r'(\(rev )' + STR, lambda m: f'{m.group(1)}"{label}"', text[b[0]:b[1]])
        block = re.sub(r'(\(date )' + STR, lambda m: f'{m.group(1)}"{date}"', block)
        text = text[:b[0]] + block + text[b[1]:]
    return _label_texts(text, label, date)


def design_files(pcb):
    """The board and every schematic sheet beside it."""
    folder = os.path.dirname(pcb)
    return [pcb] + sorted(glob.glob(os.path.join(folder, "*.kicad_sch")))


def project_rules(pcb):
    """What a board's fab output depends on beside its own files: its custom rules (.kicad_dru), and the project file's
    design settings, net classes and text variables (not the window and plot state KiCad also keeps there)."""
    base, out = os.path.splitext(pcb)[0], ""
    if os.path.exists(base + ".kicad_dru"):
        out += open(base + ".kicad_dru", encoding="utf-8").read()
    if os.path.exists(base + ".kicad_pro"):
        try:
            pro = json.load(open(base + ".kicad_pro", encoding="utf-8"))
        except ValueError:
            pro = {}
        out += json.dumps({"design_settings": pro.get("board", {}).get("design_settings"),
                           "net_settings": pro.get("net_settings"), "text_variables": pro.get("text_variables")},
                          sort_keys=True)
    return out


def source_fingerprint(pcb, settings=""):
    """The design files with their stamps masked, the project's rules, and the fab settings the files are made with
    (Project.fab_settings): when it is unchanged, so is the fab output."""
    h = hashlib.sha1()
    for f in design_files(pcb):
        h.update(os.path.basename(f).encode() + b"\0")
        h.update(mask(open(f, encoding="utf-8").read()).encode())
    h.update(b"\0rules\0" + project_rules(pcb).encode() + b"\0settings\0" + settings.encode())
    return h.hexdigest()


def preview_source(pcb):
    """What previews are made from: the board and every schematic sheet, a sha1 each (the stamps included, since the
    previews show them). A previews folder keeps it as SOURCE.txt."""
    return "".join(f"{hashlib.sha1(open(f, 'rb').read()).hexdigest()}  {os.path.basename(f)}\n"
                   for f in design_files(pcb))


def _zip_files(path):
    """A Gerber zip's files in name order, each by its name after the last "-" (the layer, or PTH.drl), without the
    header comments and attributes that change from run to run."""
    with zipfile.ZipFile(path) as z:
        return [(n.split("-")[-1], [l for l in io.TextIOWrapper(z.open(n), encoding="utf-8", errors="replace")
                                    .read().splitlines() if not l.startswith(HEADER)])
                for n in sorted(z.namelist())]


def _csv_rows(path):
    """A BOM's, CPL's or centroid's rows; PCBWay's BOM without the description and package it takes from the parts
    cache."""
    f = os.path.basename(path)
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    drop = {i for i, c in enumerate(rows[0]) if c in ("Description", "Package")} \
        if rows and f.startswith("BOM-") and f.endswith("-pcbway.csv") else set()
    return [[c for i, c in enumerate(r) if i not in drop] for r in rows]


def fab_fingerprint(out):
    """A fab output folder (pcbkit fab, every house's files) hashed without what changes from run to run: Gerber and
    drill header comments and attributes (dates, the project ID with its rev), file names, and the description and
    package PCBWay's BOM takes from the parts cache. The -back BOM and CPL are left out: they are the -both files'
    bottom rows, and without them a release made before pcbkit fab wrote them keeps its fingerprint."""
    h = hashlib.sha1()
    for f in sorted(os.listdir(out)):
        p = os.path.join(out, f)
        if f.endswith("-back.csv"):
            continue
        if f.endswith(".zip"):
            for n, lines in _zip_files(p):
                h.update(n.encode())
                h.update("\n".join(lines).encode())
        elif f.endswith(".csv"):
            h.update(f.split("-")[0].encode() + b"\0" + "\n".join("\t".join(r) for r in _csv_rows(p)).encode())
    return h.hexdigest()


BOM_FIELDS = ("LCSC", "MPN")            # with the footprint and value: what a part is, as the BOM orders it
BOM_FLAGS = ("dnp", "exclude_from_bom")  # a part not fitted, or not bought with the board
NET = re.compile(r'\(net (?:\d+ )?"((?:[^"\\]|\\.)*)"\)')     # KiCad 10 writes (net "name"), earlier (net 3 "name")


def circuit(pcb):
    """The board's netlist and BOM as its .kicad_pcb has them (KiCad's layout of the file, one item a line): parts,
    one [reference, footprint name, value, LCSC, MPN, flags] row a footprint, in reference order, and nets,
    {name: sorted [reference, pad] pins}. Board-only footprints (logos, a test pad drawn on the board) are neither
    parts nor pins. A pad on no net, or on KiCad's unconnected-(...) net for a pin left open, is on none."""
    with open(pcb, encoding="utf-8") as fh:
        text = fh.read()
    parts, nets = [], {}
    for m in re.finditer(r'\n\t\(footprint "((?:[^"\\]|\\.)*)"', text):
        block = text[m.end():text.find("\n\t)", m.end())]
        children = block.split("\n\t\t(")
        props, flags = {}, set()
        for c in children:
            p = re.match(r'property "((?:[^"\\]|\\.)*)" "((?:[^"\\]|\\.)*)"', c)
            if p:
                props.setdefault(p.group(1), p.group(2))
            elif c.startswith("attr "):
                flags |= set(c[5:c.index(")")].split())
            elif re.match(r"(dnp|exclude_from_bom|board_only) yes\)", c):
                flags.add(c.split()[0])
        if "board_only" in flags:
            continue
        ref = props.get("Reference", "")
        parts.append([ref, m.group(1).split(":")[-1], props.get("Value", "")]
                     + [props.get(f, "").strip() for f in BOM_FIELDS] + [sorted(flags & set(BOM_FLAGS))])
        for c in children:
            pad = re.match(r'pad "((?:[^"\\]|\\.)*)"', c)
            net = NET.search(c) if pad and pad.group(1) else None
            if net and net.group(1) and not net.group(1).startswith("unconnected-("):
                nets.setdefault(net.group(1), set()).add((ref, pad.group(1)))
    return dict(parts=sorted(parts, key=lambda r: _natural(r[0])),
                nets={n: sorted([list(p) for p in v], key=lambda p: (_natural(p[0]), _natural(p[1])))
                      for n, v in sorted(nets.items())})


def _natural(s):
    return [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", s)]


def _connections(c):
    """The nets as sets of pins, without their names: a net renamed is the same circuit. A net of one pin connects
    nothing."""
    return [(n, frozenset(map(tuple, pins))) for n, pins in c["nets"].items() if len(pins) > 1]


def circuit_fingerprint(c):
    """A hash of circuit(): the parts and the connections between their pins, not the nets' names."""
    nets = sorted(sorted(pins) for _, pins in _connections(c))
    return hashlib.sha1(json.dumps({"parts": c["parts"], "nets": nets}, sort_keys=True).encode()).hexdigest()


def same_circuit(entry, pcb):
    """Whether the board's netlist and BOM are a release's (entry, from releases.toml), as far as it recorded them: a
    release recorded without the circuit fingerprint can't tell, and counts as the same."""
    return not entry.get("circuit") or circuit_fingerprint(circuit(pcb)) == entry["circuit"]


def _pins(ps, most=6):
    ps = sorted(ps, key=lambda p: (_natural(p[0]), _natural(p[1])))
    out = ", ".join(f"{r}.{n}" for r, n in ps[:most])
    return out + (f", ... ({len(ps)} pins)" if len(ps) > most else "")


def circuit_changes(old, new):
    """What changed from one circuit() to another, in words: parts added, removed or changed ("R3 value 10k -> 4.7k"),
    then the connections ("net VIN gains U2.5"), each net named as the new circuit names it. Empty when their
    fingerprints are the same."""
    out = []
    a, b = ({r[0]: r for r in c["parts"]} for c in (old, new))
    names = ("footprint", "value") + BOM_FIELDS
    for ref in sorted(set(a) | set(b), key=_natural):
        x, y = a.get(ref), b.get(ref)
        if not x:
            out.append(f"adds {ref} (" + ", ".join(v for v in [y[2], y[1]] + y[3:-1] if v) + ")")
        elif not y:
            out.append(f"removes {ref}")
        else:
            out += [f"{ref} {k} {u or '(none)'} -> {v or '(none)'}"
                    for k, u, v in zip(names, x[1:-1], y[1:-1]) if u != v]
            for f in BOM_FLAGS:
                if (f in x[-1]) != (f in y[-1]):
                    word = "DNP" if f == "dnp" else "out of the BOM"
                    out.append(f"{ref} {word}" if f in y[-1] else f"{ref} no longer {word}")
    was, now = _connections(old), _connections(new)
    gone = [(n, s) for n, s in was if s not in {s for _, s in now}]
    came = [(n, s) for n, s in now if s not in {s for _, s in was}]
    # each net that changed is matched with the old net it shares most pins with (the same name first, on a tie)
    pairs = sorted(((len(s & t), n == m, i, j) for i, (n, s) in enumerate(came) for j, (m, t) in enumerate(gone)
                    if s & t), reverse=True)
    matched, used = {}, set()
    for _, _, i, j in pairs:
        if i not in matched and j not in used:
            matched[i] = j
            used.add(j)
    def moved(name, s, t):
        what = [f"gains {_pins(s - t)}"] * bool(s - t) + [f"loses {_pins(t - s)}"] * bool(t - s)
        return f"net {name} " + " and ".join(what)
    for i, (n, s) in enumerate(came):
        if i in matched:
            m, t = gone[matched[i]]
            out.append(moved(n if n == m else f"{m} (now {n})", s, t))
        elif n in old["nets"]:                  # it had one pin, which connected nothing
            out.append(moved(n, s, set(map(tuple, old["nets"][n]))))
        else:
            out.append(f"adds net {n} ({_pins(s)})")
    for j, (m, t) in enumerate(gone):
        if j in used:
            continue
        if m in new["nets"]:                    # down to one pin
            out.append(moved(m, set(map(tuple, new["nets"][m])), t))
        else:
            out.append(f"removes net {m} ({_pins(t)})")
    if not out and circuit_fingerprint(old) != circuit_fingerprint(new):
        out.append("the netlist or BOM changed")
    return out


def _step(lines):
    """A Gerber's coordinate step in mm (%FSLAX46Y46*% with %MOMM*%: a nanometre), or None."""
    fs = [re.match(r"%FSLAX\d(\d)Y\d(\d)\*%$", l) for l in lines if l.startswith("%FS")]
    mo = [l for l in lines if l.startswith("%MO")]
    if len(fs) != 1 or not fs[0] or fs[0].group(1) != fs[0].group(2) or mo not in (["%MOMM*%"], ["%MOIN*%"]):
        return None
    return 10.0 ** -int(fs[0].group(1)) * (25.4 if mo == ["%MOIN*%"] else 1)


def _regions(lines):
    """A Gerber's lines outside its regions (G36* ... G37*, which KiCad draws zone fills with) and each region's
    lines, or None when a region isn't closed."""
    outside, regions, region = [], [], None
    for l in lines:
        if region is None:
            outside.append(l)
            if l == "G36*":
                region = []
        elif l == "G37*":
            outside.append(l)
            regions.append(region)
            region = None
        else:
            region.append(l)
    return None if region is not None else (outside, regions)


def _distance(p, line):
    """From a point to a polyline."""
    if len(line) == 1:
        return math.hypot(p[0] - line[0][0], p[1] - line[0][1])
    best = None
    for (ax, ay), (bx, by) in zip(line, line[1:]):
        dx, dy = bx - ax, by - ay
        t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / (dx * dx + dy * dy))) if dx or dy else 0.0
        d = math.hypot(p[0] - ax - t * dx, p[1] - ay - t * dy)
        best = d if best is None or d < best else best
    return best


def _polyline_gap(a, b, tol):
    """How far apart two polylines are, or None beyond tol: the furthest that points along either, at most tol apart,
    lie from the other (so that no point of either lies more than 1.5 tol from the other)."""
    worst, tol = 0.0, max(tol, 1)
    for line, other in ((a, b), (b, a)):
        points = line[:1]
        for (x0, y0), (x1, y1) in zip(line, line[1:]):
            n = max(1, int(math.ceil(math.hypot(x1 - x0, y1 - y0) / tol)))
            points += [(x0 + (x1 - x0) * i / n, y0 + (y1 - y0) * i / n) for i in range(1, n + 1)]
        for p in points:
            d = _distance(p, other)
            if d > tol:
                return None
            worst = max(worst, d)
    return worst


def _region_gap(a, b, tol):
    """How far region b's outline lies from region a's, in coordinate steps, when only its vertices differ (moved,
    added or dropped) and by no more than tol; else None. Each stretch that differs is compared between the vertices
    on either side of it, which are the same in both."""
    va, vb = [VERTEX.match(l) for l in a], [VERTEX.match(l) for l in b]

    def pts(ms):
        return [(int(m.group(1)), int(m.group(2))) for m in ms]
    worst = 0.0
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            continue
        old, new = va[i1:i2], vb[j1:j2]
        if not all(old) or not all(new):
            return None
        gap = None
        if len(old) == len(new) and all(x.group(3) == y.group(3) for x, y in zip(old, new)):
            # the same vertices moved: no point of the outline moves further than the furthest of them
            g = max(math.hypot(p[0] - q[0], p[1] - q[1]) for p, q in zip(pts(old), pts(new)))
            gap = g if g <= tol else None
        before, after = va[i1 - 1] if i1 else None, va[i2] if i2 < len(va) else None
        if gap is None and before and after and all(m.group(3) == "1" for m in old + new + [after]):
            gap = _polyline_gap(pts([before] + old + [after]), pts([before] + new + [after]), tol)
        if gap is None:
            return None
        worst = max(worst, gap)
    return worst


def gerber_gap(a, b, tol=ZONE_TOLERANCE):
    """How far apart two Gerbers' (or drill files') lines put copper, in mm: 0 when they are the same; the furthest a
    region's (zone fill's) outline moved when only region vertices differ, by no more than tol; else None."""
    if a == b:
        return 0.0
    step, ra, rb = _step(a), _regions(a), _regions(b)
    if not step or step != _step(b) or not ra or not rb or ra[0] != rb[0]:
        return None
    worst = 0.0
    for x, y in zip(ra[1], rb[1]):
        if x != y:
            g = _region_gap(x, y, tol / step)
            if g is None:
                return None
            worst = max(worst, g)
    return worst * step


def fab_gap(pairs, tol=ZONE_TOLERANCE):
    """Two fab outputs compared file by file, as fab_fingerprint sees them: pairs of Gerber zips or of CSVs. How far
    apart their copper is in mm (0: the same) when they differ only in their zone fills, by no more than tol; else
    None."""
    worst = 0.0
    for p, q in pairs:
        if p.endswith(".zip"):
            a, b = _zip_files(p), _zip_files(q)
            if [n for n, _ in a] != [n for n, _ in b]:
                return None
            for (_, x), (_, y) in zip(a, b):
                g = gerber_gap(x, y, tol)
                if g is None:
                    return None
                worst = max(worst, g)
        elif _csv_rows(p) != _csv_rows(q):
            return None
    return worst


# ---- stamping -------------------------------------------------------------------------------------------------------
def _stamp_title_block(text, new_label, date, path):
    b = title_block(text)
    if not b:                          # none yet (a blank board): one after (paper ...), as KiCad places it
        m = re.search(r"\n(\t*)\(paper [^\n]*", text)
        if not m:
            raise SystemExit(f"{path}: no title block, and no (paper ...) to put one after; nothing written")
        ind = m.group(1)
        return (text[:m.end()] + f'\n{ind}(title_block\n{ind}\t(date "{date}")\n{ind}\t(rev "{new_label}")\n{ind})'
                + text[m.end():])
    s, e = b
    block = text[s:e]
    close = re.search(r"\n(\t*)\)$", block)       # one field a line: the fields one tab in from the closing paren
    for key, val in (("date", date), ("rev", new_label)):
        pat = re.compile(r'(\(' + key + r' )' + STR + r'(\))')
        n = len(pat.findall(block))
        if n > 1:
            raise SystemExit(f"{path}: {n} {key} fields in the title block; nothing written")
        if n:
            block = pat.sub(lambda m: f'{m.group(1)}"{val}"{m.group(2)}', block)
            continue
        after = (re.search(r'\(date ' + STR + r'\)', block) if key == "rev" else None) or \
            re.search(r'\(title ' + STR + r'\)', block)              # KiCad's order: title, date, rev, company
        at = after.end() if after else len("(title_block")
        field = f'({key} "{val}")'
        block = block[:at] + (f"\n{close.group(1)}\t{field}" if close else " " + field) + block[at:]
    return text[:s] + block + text[e:]


def stamp(pcb, old_label, new_label, date):
    """Write new_label (and the date) into the board and its root schematic. Both files are checked before either is
    written: each may change only in its stamps. Returns the files changed."""
    root_sch = os.path.splitext(pcb)[0] + ".kicad_sch"
    edits = []
    for path in [pcb] + ([root_sch] if os.path.exists(root_sch) else []):
        before = open(path, encoding="utf-8").read()
        after = _stamp_title_block(before, new_label, date, path)
        if path == pcb and old_label:
            word = rf"(?<![\w.]){re.escape(old_label)}(?![\w.])"

            def gr(m):
                s = m.group(2)
                if re.search(word, s):
                    s = re.sub(DATE, date, re.sub(word, new_label, s))
                return m.group(1) + s + m.group(3)
            after = TEXT.sub(gr, after)
        if mask(after) != mask(before):
            raise SystemExit(f"{path}: stamping changed more than the version and date; nothing written")
        if after != before:
            edits.append((path, after))
    for path, after in edits:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(after)
    return [path for path, _ in edits]


def locked(pcb):
    """KiCad has the board's folder open (its lock files)."""
    return sorted(glob.glob(os.path.join(os.path.dirname(pcb), "~*.lck")))


# ---- releases.toml --------------------------------------------------------------------------------------------------
def releases(rdir):
    path = os.path.join(rdir, "releases.toml")
    return tomlw.load(path).get("release", []) if os.path.exists(path) else []


def current(rdir):
    r = releases(rdir)
    return r[-1] if r else None


def record(rdir, key, entry):
    path = os.path.join(rdir, "releases.toml")
    data = {"key": key, "release": releases(rdir) + [entry]}
    os.makedirs(rdir, exist_ok=True)
    tomlw.dump(data, path, f"Releases of {key}, written by pcbkit release: one per version, newest last.\n"
                           "Don't edit by hand; the fab files of each version are in v<label>/ beside this file.")


def update_last(rdir, key, **fields):
    rs = releases(rdir)
    rs[-1].update(fields)
    tomlw.dump({"key": key, "release": rs}, os.path.join(rdir, "releases.toml"),
               f"Releases of {key}, written by pcbkit release: one per version, newest last.\n"
               "Don't edit by hand; the fab files of each version are in v<label>/ beside this file.")


def today():
    return datetime.date.today()
