"""Board versions: semver in the data ("1.0.0"), a label on the board ("v1.0", or "v1.0.1" once there is a patch),
change fingerprints that ignore the version and date stamps, and stamping a new version into the KiCad files.

Where a version is written on a board:
- the title block's rev (and its date, in the project's date format) in the .kicad_pcb, in the root .kicad_sch and
  in every sheet under it in the board's folder that has a title block of its own, whether KiCad wrote the block one
  field a line or a generator wrote it on one line. The board or root schematic without one gets one after its
  (paper ...); a sheet without one is left so. The board and the root schematic each get the other's title and
  company when they have none, so schematic.pdf names the board as well as its version.
- silkscreen that says ${REVISION} and ${ISSUE_DATE}: KiCad fills them from the title block when it plots, so the
  board text is never rewritten (the practice in docs/CONVENTIONS.md);
- any silkscreen text (gr_text) that carries the old label typed in as a word, e.g. "v1.0" on a front tag or
  "<name> v1.0 2026-10-19" on the back; an ISO date in such a text becomes the release date.
Each stamp must change exactly what it means to: the masked text before and after must be identical.
label_problems() finds what would print wrong: v${REVISION} (the rev is the label, v included) and ${CURRENT_DATE}
(the day of the plot, so no two runs make the same Gerbers).

Fab output is compared by fab_fingerprint, a hash. When the hash differs, fab_gap compares the files themselves with a
release's: zone fills (Gerber regions) whose outlines lie within ZONE_TOLERANCE of the release's, every point of them,
are the same copper, since a KiCad that refills zones a little differently moves a vertex by a nanometre or drops
one.

The circuit (circuit(), circuit_fingerprint(), circuit_changes()) is the board's netlist and BOM as the PCB has them:
what decides between a minor version (the circuit changed) and a patch (only the layout did). layout() and
layout_changes() say what moved, for a changelog (pcbkit changelog).
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
import subprocess
import zipfile

from .. import sexp, tomlw

SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
REV = re.compile(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?")
PLAIN_REV = re.compile(r"v?\d+(?:\.\d+){0,2}")   # a rev that is only a version ("v1.4", "1.3.1"), not "0.1-draft"
LABEL_IN_TEXT = r"(?<![\w.])v\d+(?:\.\d+){1,2}(?![\w.])"
DATE = r"\d{4}-\d{2}-\d{2}"
ISO_DATE = "%Y-%m-%d"
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


def names_version(rev):
    """Whether a title-block rev is a version alone ("v1.4", "1.3.1", "2.0"), as a built board's would be, rather than
    a draft's ("0.1-draft", "3.0-draft") or nothing."""
    return bool(PLAIN_REV.fullmatch(rev.strip()))


def date_text(day, fmt=ISO_DATE):
    """A date as the title block gets it: day (a date, or an ISO date's text) in the project's [release] date_format,
    a strftime format ("%Y-%m-%d", "%m.%Y")."""
    if not isinstance(day, datetime.date):
        day = datetime.date.fromisoformat(str(day))
    return day.strftime(fmt)


def check_date_format(fmt):
    """The format, or why it can't be one: it must name a year, and write nothing a KiCad string can't hold as is."""
    try:
        sample = date_text(datetime.date(2026, 11, 15), fmt)
    except (ValueError, TypeError) as e:
        raise SystemExit(f"[release] date_format = {fmt!r}: {e}")
    if "2026" not in sample and "26" not in sample:
        raise SystemExit(f"[release] date_format = {fmt!r} gives {sample!r}, without the year")
    if any(c in sample for c in '"\\\n'):
        raise SystemExit(f"[release] date_format = {fmt!r} gives {sample!r}: no quotes, backslashes or new lines")
    return fmt


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


def title_field(text, key):
    """A title-block field's text (title, date, rev, company), "" when there is none."""
    b = title_block(text)
    r = re.search(r'\(' + key + r' "((?:[^"\\]|\\.)*)"\)', text[b[0]:b[1]]) if b else None
    return re.sub(r"\\(.)", r"\1", r.group(1)) if r else ""


def title_rev(text):
    return title_field(text, "rev")


def _label_texts(text, label, date):
    """Every silkscreen text that carries a version: its labels and dates replaced (None: masked)."""
    def gr(m):
        s = m.group(2)
        if re.search(LABEL_IN_TEXT, s):
            s = re.sub(DATE, date or "#", re.sub(LABEL_IN_TEXT, label or "v#", s))
        return m.group(1) + s + m.group(3)
    return TEXT.sub(gr, text)


PLOTTED = re.compile(r"^(?:[FB]\.(?:SilkS|Cu|Mask)|In\d+\.Cu)$")   # layers whose texts are in the Gerbers


def _hidden(node):
    for c in node:
        if c == "hide" or (isinstance(c, list) and c[:2] == ["hide", "yes"]):
            return True
        if isinstance(c, list) and c and c[0] == "effects" and _hidden(c[1:]):
            return True
    return False


def plotted_texts(text):
    """Every text on a board that its Gerbers draw, as (the text as written, its layer): board texts and text boxes,
    and the shown texts and fields of footprints, on the silkscreen, copper or mask."""
    out = []

    def walk(node):
        for c in node:
            if not isinstance(c, list) or not c:
                continue
            head = c[0]
            if head in ("gr_text", "gr_text_box", "fp_text", "fp_text_box", "property"):
                s = c[2] if head in ("fp_text", "property") and len(c) > 2 else c[1] if len(c) > 1 else ""
                layer = next((str(x[1]) for x in c if isinstance(x, list) and len(x) > 1 and x[0] == "layer"), "")
                if isinstance(s, str) and PLOTTED.match(layer) and not _hidden(c):
                    out.append((str(s), layer))
            elif head in ("footprint", "module", "group"):
                walk(c)
    tree = sexp.parse(text)
    walk(tree if isinstance(tree, list) else [])
    return out


def _quote(s, most=60):
    s = s.replace("\\n", " / ").replace("\n", " / ")       # a new line as the file writes it, or as read
    return "'" + (s if len(s) <= most else s[:most - 3] + "...") + "'"


def label_problems(text, rev=""):
    """What a board's own texts would print wrong once stamped, and the texts with a version typed in: (errors,
    typed). An error stops a release: v${REVISION} prints "vv1.0", since the title block's rev is the label with its
    v; ${CURRENT_DATE} prints the day of the plot, so the release's Gerbers can't be made again and its fab
    fingerprint changes every day. typed are the texts that carry a version as written (the old label, or the
    title block's rev as a word), which ${REVISION} and ${ISSUE_DATE} would keep in step with the title block."""
    errors, typed = [], []
    word = rf"(?<![\w.]){re.escape(rev)}(?![\w.])" if rev.strip() else None
    for s, layer in plotted_texts(text):
        if re.search(r"(?i)v\$\{REVISION\}", s):
            errors.append(f"{layer} text {_quote(s)} would print vv: the title block's rev is the label, v "
                          "included; write ${REVISION}")
        if "${CURRENT_DATE}" in s:
            errors.append(f"{layer} text {_quote(s)} prints the day the Gerbers are made, so no two runs make the same "
                          "files and the release can't be compared with; write ${ISSUE_DATE}, the release's date")
        if layer.endswith(".SilkS") and (re.search(LABEL_IN_TEXT, s) or (word and re.search(word, s))):
            typed.append(_quote(s))
    return errors, typed


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


def neutral(text, label="v0.0", date="2000-01-01", text_date=None):
    """The file with neutral version and date stamps, so that two versions of a board fab the same; or with a given
    label and date, as a release stamped it (date as its title block has it, text_date the ISO date a silkscreen
    text with the label typed in got; date when not given). A title block without a rev or date gets one, as
    stamping gives it, so ${REVISION} and ${ISSUE_DATE} plot the same before a board's first stamp and after."""
    try:
        text = _stamp_title_block(text, label, date, "")
    except SystemExit:                 # two rev fields, or no title block and nowhere to put one: as it is
        b = title_block(text)
        if b:
            block = re.sub(r'(\(rev )' + STR, lambda m: f'{m.group(1)}"{label}"', text[b[0]:b[1]])
            block = re.sub(r'(\(date )' + STR, lambda m: f'{m.group(1)}"{date}"', block)
            text = text[:b[0]] + block + text[b[1]:]
    return _label_texts(text, label, text_date or date)


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
    """What previews are made from: the board and every schematic sheet beside it, then every other sheet under its
    root schematic (in a subfolder, or outside the folder), a sha1 each (the stamps included, since the previews show
    them). A previews folder keeps it as SOURCE.txt."""
    from . import freeze
    files = design_files(pcb)
    root = os.path.splitext(pcb)[0] + ".kicad_sch"
    beside = {os.path.abspath(f) for f in files}
    under = sorted(p for p in freeze.sheets(root)[0] if p not in beside) if os.path.exists(root) else []

    def sha(f):
        with open(f, "rb") as fh:
            return hashlib.sha1(fh.read()).hexdigest()
    return "".join(f"{sha(f)}  {os.path.basename(f)}\n" for f in files) + \
        "".join(f"{sha(f)}  {os.path.relpath(f, os.path.dirname(os.path.abspath(pcb)))}\n" for f in under)


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


def circuit(pcb, text=None):
    """The board's netlist and BOM as its .kicad_pcb has them (or text, the file's text, from git or a release), read
    as S-expressions (however the file is laid out: KiCad 8 and later indent with tabs, 6 and 7 with spaces): parts,
    one [reference, footprint name, value, LCSC, MPN, flags] row a footprint, in reference order, and nets, {name:
    sorted [reference, pad] pins}. Board-only footprints (logos, a test pad drawn on the board) are neither parts nor
    pins. A pad on no net, or on KiCad's unconnected-(...) net for a pin left open, is on none. KiCad 6 and 7 keep the
    reference and value as fp_text, and write a pad's net as (net 3 "name"), KiCad 10 as (net "name")."""
    if text is None:
        with open(pcb, encoding="utf-8") as fh:
            text = fh.read()
    tree = sexp.parse(text)
    parts, nets = [], {}
    for fp in tree[1:] if isinstance(tree, list) else []:
        if not isinstance(fp, list) or len(fp) < 2 or fp[0] not in ("footprint", "module"):
            continue
        props, flags, pads = {}, set(), []
        for c in fp[2:]:
            if not isinstance(c, list) or len(c) < 2:
                continue
            if c[0] == "property" and len(c) > 2:
                props.setdefault(str(c[1]), str(c[2]))
            elif c[0] == "fp_text" and len(c) > 2 and c[1] in ("reference", "value"):
                props.setdefault(str(c[1]).capitalize(), str(c[2]))
            elif c[0] == "attr":
                flags |= {str(x) for x in c[1:] if not isinstance(x, list)}
            elif c[0] in ("dnp", "exclude_from_bom", "board_only") and c[1] == "yes":
                flags.add(str(c[0]))
            elif c[0] == "pad":
                pads.append(c)
        if "board_only" in flags:
            continue
        ref = props.get("Reference", "")
        parts.append([ref, str(fp[1]).split(":")[-1], props.get("Value", "")]
                     + [props.get(f, "").strip() for f in BOM_FIELDS] + [sorted(flags & set(BOM_FLAGS))])
        for pad in pads:
            net = next((x for x in pad[2:] if isinstance(x, list) and len(x) > 1 and x[0] == "net"), None)
            name = str(net[-1]) if net else ""
            if str(pad[1]) and name and not name.startswith("unconnected-("):
                nets.setdefault(name, set()).add((ref, str(pad[1])))
    return dict(parts=sorted(parts, key=lambda r: (_natural(r[0]), r)),        # R1 and R01 sort alike: then by all
                nets={n: sorted([list(p) for p in v], key=lambda p: (_natural(p[0]), _natural(p[1]), p))
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
    # each net that changed is matched with the old net it shares most pins with (the same name first, on a tie), or
    # failing that with the old net of its name, even with every pin replaced: one change, said once
    pairs = sorted(((len(s & t), n == m, i, j) for i, (n, s) in enumerate(came) for j, (m, t) in enumerate(gone)
                    if s & t or n == m), reverse=True)
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


def _xy(node):
    return float(node[1]), float(node[2])


def _arc_length(a, m, b):
    """The length of the arc from a through m to b."""
    (ax, ay), (mx, my), (bx, by) = a, m, b
    d = 2 * (ax * (my - by) + mx * (by - ay) + bx * (ay - my))
    if abs(d) < 1e-12:                 # the three in a line
        return math.hypot(bx - ax, by - ay)
    sa, sm, sb = ax * ax + ay * ay, mx * mx + my * my, bx * bx + by * by
    ux = (sa * (my - by) + sm * (by - ay) + sb * (ay - my)) / d
    uy = (sa * (bx - mx) + sm * (ax - bx) + sb * (mx - ax)) / d
    t0, tm, t1 = (math.atan2(y - uy, x - ux) for x, y in (a, m, b))
    sweep = (t1 - t0) % (2 * math.pi)
    if (tm - t0) % (2 * math.pi) > sweep:      # m is on the other side: the arc goes the long way round
        sweep = 2 * math.pi - sweep
    return math.hypot(ax - ux, ay - uy) * sweep


def _child(node, key):
    return next((c for c in node if isinstance(c, list) and c and c[0] == key), None)


def _drawn(node, drop=()):
    """An item as drawn, to compare: the file's S-expression without its uuid and the children named in drop; a
    zone's (fill yes ...) without the yes, which says whether it was filled when saved."""
    return sexp.dump([[y for y in x if y != "yes"] if isinstance(x, list) and x[:1] == ["fill"] else x
                      for x in node if not (isinstance(x, list) and x and x[0] in ("uuid", "tstamp") + drop)])


def _masked(s):
    """A text with the version typed into it and its dates masked, as release stamps them; any other text as it is."""
    return re.sub(DATE, "#", re.sub(LABEL_IN_TEXT, "v#", s)) if re.search(LABEL_IN_TEXT, s) else s


def _net_named(node, codes):
    """A track or via with its (net 3) as (net "GND"): KiCad 6 to 9 number the nets anew when one is added."""
    return [[x[0], codes.get(str(x[1]), x[1])] if isinstance(x, list) and len(x) == 2 and x[0] == "net"
            and isinstance(x[1], sexp.Sym) else x for x in node]    # x[0] is the bare head, as KiCad 10 writes it


def _net_name(node, codes):
    """A zone's or pad's net by name: (net_name "GND"), (net "GND"), or a bare code through the board's net table; a
    quoted name that is a number ("5") is a name."""
    net = _child(node, "net_name") or _child(node, "net")
    v = net[-1] if net and len(net) > 1 else None
    return codes.get(str(v), "") if isinstance(v, sexp.Sym) else str(v) if v is not None else ""


def _in_frame(node, angle):
    """A footprint's pad or text with its angle in the footprint's frame: the file writes it turned with the part."""
    out = []
    for x in node:
        if isinstance(x, list) and x[:1] == ["at"] and len(x) >= 3:
            a = float(x[3]) if len(x) > 3 and not isinstance(x[3], list) and str(x[3]) not in ("unlocked",) else 0.0
            x = [x[0], x[1], x[2], sexp.Sym(f"{round((a - angle) % 360, 2):g}")] + x[4:]
        out.append(x)
    return out


def layout(pcb, text=None):
    """What a board's layout is made of, to say what changed (layout_changes): parts, {reference: (x, y, angle, side,
    board only)}; tracks and their length in mm (segments and arcs); vias; where the tracks and vias are (a hash, by
    net name); zones, ("<net> on <layers>", the zone as drawn: outline and settings, not its fill or net code); the
    outline (its Edge.Cuts drawings, without their uuids); the silkscreen texts, (the text with a version or ISO date
    typed into it masked, its place, layer and font); each footprint's shown silkscreen texts, as drawn in the
    footprint's frame; and the title and company, with which of them the board's own texts print."""
    if text is None:
        with open(pcb, encoding="utf-8") as fh:
            text = fh.read()
    tree = sexp.parse(text)
    out = dict(parts={}, tracks=0, length=0.0, vias=0, zones=[], outline=[], silk=[], fp_silk={}, pads={},
               title=(title_field(text, "title"), title_field(text, "company")),
               prints=sorted(k for k in ("TITLE", "COMPANY") if any("${%s}" % k in t for t, _ in plotted_texts(text))))
    codes = {str(x[1]): str(x[2]) for x in tree[1:] if isinstance(x, list) and len(x) > 2 and x[0] == "net"} \
        if isinstance(tree, list) else {}
    copper = []                        # where each track and via is, to tell routing moved from routing the same
    for c in tree[1:] if isinstance(tree, list) else []:
        if not isinstance(c, list) or not c:
            continue
        head, layer = c[0], _child(c, "layer")
        layer = str(layer[1]) if layer and len(layer) > 1 else ""
        if head in ("segment", "arc", "via"):
            copper.append(_drawn(_net_named(c, codes)))
        if head in ("footprint", "module"):
            ref = next((str(x[2]) for x in c if isinstance(x, list) and len(x) > 2 and x[0] in ("property", "fp_text")
                        and x[1] in ("Reference", "reference")), "")
            at = _child(c, "at") or ["at", 0, 0]
            key, n = ref, 1
            while key in out["parts"]:                # two footprints of one reference (or none)
                n += 1
                key = f"{ref}#{n}"
            board_only = (_child(c, "board_only") or [])[1:2] == ["yes"] or \
                "board_only" in [str(x) for x in (_child(c, "attr") or [])[1:]]
            out["parts"][key] = (round(float(at[1]), 3), round(float(at[2]), 3),
                                 round(float(at[3]) % 360 if len(at) > 3 else 0.0, 2),
                                 "back" if layer.startswith("B.") else "front", board_only)
            turn = float(at[3]) if len(at) > 3 else 0.0
            out["fp_silk"][key] = sorted(_masked(_drawn(_in_frame(x, turn))) for x in c if isinstance(x, list) and
                                         x[:1] in (["property"], ["fp_text"], ["fp_text_box"]) and not _hidden(x)
                                         and str((_child(x, "layer") or ["", ""])[1]).endswith(".SilkS"))
            # the pads and holes as the part has them, without their nets (the circuit says those)
            out["pads"][key] = (str(c[1]), sorted(_drawn(_in_frame(x, turn), ("net", "pinfunction", "pintype"))
                                                  for x in c if isinstance(x, list) and x[:1] == ["pad"]))
        elif head == "segment":
            out["tracks"] += 1
            out["length"] += math.hypot(*(p - q for p, q in zip(_xy(_child(c, "end")), _xy(_child(c, "start")))))
        elif head == "arc" and _child(c, "mid"):     # a track's arc (a drawn one is a gr_arc)
            out["tracks"] += 1
            out["length"] += _arc_length(*(_xy(_child(c, k)) for k in ("start", "mid", "end")))
        elif head == "via":
            out["vias"] += 1
        elif head == "zone":
            name = _net_name(c, codes)
            layers = _child(c, "layers") or _child(c, "layer") or []
            out["zones"].append((f"{name or 'no net'} on {', '.join(str(x) for x in layers[1:])}",
                                 _drawn(c, ("filled_polygon", "fill_segments", "net", "net_name"))))
        elif head.startswith("gr_") and layer == "Edge.Cuts":
            out["outline"].append(sexp.dump([x for x in c if not (isinstance(x, list) and x and x[0] in
                                                                   ("uuid", "tstamp"))]))
        elif head in ("gr_text", "gr_text_box") and layer.endswith(".SilkS") and len(c) > 1:
            out["silk"].append((_masked(str(c[1])), _drawn(c[:1] + c[2:])))
    out["length"] = round(out["length"], 1)
    out["copper"] = hashlib.sha1("\n".join(sorted(copper)).encode()).hexdigest()
    out["zones"].sort()
    out["outline"].sort()
    out["silk"].sort()
    return out


def _refs(refs, most=8):
    refs = sorted(refs, key=_natural)
    return ", ".join(refs[:most]) + (f" and {len(refs) - most} more" if len(refs) > most else "")


def layout_changes(old, new):
    """What changed from one layout() to another, in words: parts moved, turned or put on the other side, board-only
    footprints added or removed (circuit_changes says the rest), the tracks, vias and zones, the outline and the
    silkscreen texts."""
    out = []
    a, b = old["parts"], new["parts"]
    both = [r for r in b if r in a]
    moved = [r for r in both if a[r][:2] != b[r][:2]]
    turned = [r for r in both if a[r][2] != b[r][2] and a[r][3] == b[r][3]]
    sides = [r for r in both if a[r][3] != b[r][3]]
    if moved:
        out.append(f"moves {len(moved)} part{'s' * (len(moved) > 1)}: {_refs(moved)}")
    if turned:
        out.append("turns " + ", ".join(f"{r} {a[r][2]:g} -> {b[r][2]:g}" for r in sorted(turned, key=_natural)[:8])
                   + (f" and {len(turned) - 8} more" if len(turned) > 8 else ""))
    for side in ("back", "front"):
        flipped = [r for r in sides if b[r][3] == side]
        if flipped:
            out.append(f"puts {_refs(flipped)} on the {side}")
    added = [r for r in b if r not in a and b[r][4]]
    gone = [r for r in a if r not in b and a[r][4]]
    if added:
        out.append(f"adds the board-only {_refs(added)}")
    if gone:
        out.append(f"removes the board-only {_refs(gone)}")
    if (old["tracks"], old["length"]) != (new["tracks"], new["length"]):
        out.append(f"tracks {old['tracks']} -> {new['tracks']}, {old['length']:g} -> {new['length']:g} mm")
    if old["vias"] != new["vias"]:
        out.append(f"vias {old['vias']} -> {new['vias']}")
    if (old["tracks"], old["length"], old["vias"]) == (new["tracks"], new["length"], new["vias"]) and \
            old.get("copper") != new.get("copper"):
        out.append("tracks or vias moved (as many, as long)")
    for z, n, m, same in _by_name(old["zones"], new["zones"]):
        if m > n:
            out.append(f"adds a zone: {z}" + (f" (x{m - n})" if m - n > 1 else ""))
        elif n > m:
            out.append(f"removes a zone: {z}" + (f" (x{n - m})" if n - m > 1 else ""))
        elif not same:
            out.append(f"changes a zone: {z} (its outline or settings)")
    if old["outline"] != new["outline"]:
        out.append("the outline (Edge.Cuts) changed")
    silk = list(_by_name(old["silk"], new["silk"]))
    out += [f"silkscreen adds {_quote(t)}" + (f" (x{m - n})" if m - n > 1 else "") for t, n, m, _ in silk if m > n]
    out += [f"silkscreen removes {_quote(t)}" + (f" (x{n - m})" if n - m > 1 else "") for t, n, m, _ in silk if n > m]
    out += [f"silkscreen moves or restyles {_quote(t)}" for t, n, m, same in silk if n == m and not same]
    pa, pb = old.get("pads", {}), new.get("pads", {})
    pads = [r for r in pb if r in pa and pa[r][0] == pb[r][0] and pa[r][1] != pb[r][1]]   # the same footprint name
    if pads:
        out.append(f"changes the pads or holes of {len(pads)} part{'s' * (len(pads) > 1)}: {_refs(pads)}")
    fa, fb = old.get("fp_silk", {}), new.get("fp_silk", {})
    texts = [r for r in fb if r in fa and fa[r] != fb[r]]
    if texts:
        out.append(f"moves or restyles the silkscreen texts of {len(texts)} part{'s' * (len(texts) > 1)}: "
                   f"{_refs(texts)}")
    for i, k in enumerate(("TITLE", "COMPANY")):
        if k in new.get("prints", []) and old["title"][i] != new["title"][i]:
            out.append(f"silkscreen prints another {k.lower()} (${{{k}}}): {_quote(old['title'][i])} -> "
                       f"{_quote(new['title'][i])}")
    return out


def _by_name(old, new):
    """Items as (name, how drawn) pairs, by name: (name, how many before, how many now, drawn the same)."""
    for name in sorted({k for k, _ in old} | {k for k, _ in new}):
        a, b = sorted(d for k, d in old if k == name), sorted(d for k, d in new if k == name)
        yield name, len(a), len(b), a == b


# ---- git -------------------------------------------------------------------------------------------------------------
def git_text(pcb, ref):
    """The board's file as git has it at ref (a commit, tag or branch), or another file there given as REF:path
    (from the repository's top), or REF:./path (from the board's folder); None when git hasn't it."""
    folder, name = os.path.split(os.path.abspath(pcb))
    spec = ref if ":" in ref else f"{ref}:./{name}"
    r = subprocess.run(["git", "-C", folder, "show", spec], capture_output=True)
    return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else None


def rev_origin(pcb, rev):
    """The first commit whose copy of the board has its title block read rev: (short hash, ISO date), or None when
    the board isn't in git or that rev was never committed."""
    if not rev.strip():
        return None
    folder, name = os.path.split(os.path.abspath(pcb))
    r = subprocess.run(["git", "-C", folder, "log", "--reverse", "--format=%h %as", "-S", f'(rev "{_esc(rev)}")',
                        "--", name], capture_output=True, text=True)
    lines = r.stdout.split("\n") if r.returncode == 0 else []
    return tuple(lines[0].split(" ", 1)) if lines and lines[0].strip() else None


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


def _seg_distance(p, a, b):
    """From a point to a segment."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy))) if dx or dy else 0.0
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


PIECES = 200000                        # a comparison that would need more pieces than this is no match


def _polyline_gap(a, b, tol):
    """How far apart two polylines are, or None beyond tol: a bound on how far any point of either lies from the
    other, over every point of every segment, not only sampled ones. Each segment is halved until a bound holds for
    the whole piece: the furthest of its two ends from any one segment of the other (along a straight piece the
    distance to a segment is convex, so it is largest at an end), or the ends' distances and half the piece's length
    (a point moved by d is at most d further from the other). None when a point lies beyond tol, or when a piece
    one coordinate step long still can't be bounded within it. One segment at a time, its pieces depth first: the
    distances held are those of the pieces still to do, a few dozen at most, and the first point beyond tol ends it."""
    worst, tol, budget = 0.0, max(tol, 1), PIECES
    for line, other in ((a, b), (b, a)):
        segs = list(zip(other, other[1:])) or [(other[0], other[0])]

        def dists(p):
            return [_seg_distance(p, s, e) for s, e in segs]
        dq = None
        for p, q in list(zip(line, line[1:])) or [(line[0], line[0])]:
            dp = dq or dists(p)                # the last segment's end is this one's start
            if min(dp) > tol:
                return None
            dq = dists(q)
            todo = [(p, q, dp, dq)]
            while todo:
                p0, q0, d_p, d_q = todo.pop()
                d0, d1 = min(d_p), min(d_q)
                if d1 > tol:
                    return None
                length = math.hypot(q0[0] - p0[0], q0[1] - p0[1])
                bound = min(min(max(x, y) for x, y in zip(d_p, d_q)), (d0 + d1 + length) / 2)
                if bound <= tol:
                    worst = max(worst, bound)
                    continue
                budget -= 1
                if length <= 1 or budget < 0:
                    return None
                m = ((p0[0] + q0[0]) / 2, (p0[1] + q0[1]) / 2)
                dm = dists(m)
                todo += [(m, q0, dm, d_q), (p0, m, d_p, dm)]
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
FIELDS = ("title", "date", "rev", "company")      # KiCad's order in a title block; comments come after


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _esc(s):
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _stamp_title_block(text, new_label, date, path, fill=None):
    """The title block with its rev and date set (left alone when None), and the fields in fill (title, company) set
    where the block has none or an empty one; a file without a block gets one after its (paper ...)."""
    fields = [(k, v, True) for k, v in (("date", date), ("rev", new_label)) if v is not None]
    fields += [(k, v, False) for k, v in (fill or {}).items() if v]
    if not fields:
        return text
    b = title_block(text)
    if not b:                          # none yet (a blank board): one after (paper ...), as KiCad places it
        m = re.search(r"\n(\t*)\(paper [^\n]*", text)
        if not m:
            raise SystemExit(f"{path}: no title block, and no (paper ...) to put one after; nothing written")
        ind, vals = m.group(1), {k: v for k, v, _ in fields}
        body = "".join(f'\n{ind}\t({k} "{_esc(vals[k])}")' for k in FIELDS if k in vals)
        return text[:m.end()] + f"\n{ind}(title_block{body}\n{ind})" + text[m.end():]
    s, e = b
    block = text[s:e]
    close = re.search(r"\n(\t*)\)$", block)       # one field a line: the fields one tab in from the closing paren
    for key, val, always in fields:
        pat = re.compile(r'(\(' + key + r' )(' + STR + r')(\))')
        found = pat.findall(block)
        if len(found) > 1:
            raise SystemExit(f"{path}: {len(found)} {key} fields in the title block; nothing written")
        if found:
            if always or found[0][1] == '""':
                block = pat.sub(lambda m: f'{m.group(1)}"{_esc(val)}"{m.group(3)}', block)
            continue
        after = None                   # after the field KiCad writes before it, the nearest one there is
        for k in reversed(FIELDS[:FIELDS.index(key)]):
            after = re.search(r'\(' + k + r' ' + STR + r'\)', block)
            if after:
                break
        at = after.end() if after else len("(title_block")
        field = f'({key} "{_esc(val)}")'
        block = block[:at] + (f"\n{close.group(1)}\t{field}" if close else " " + field) + block[at:]
    return text[:s] + block + text[e:]


def sheets_to_stamp(pcb):
    """The root schematic's sheets in the board's folder (or below it) that have a title block of their own, which
    KiCad prints on their pages of schematic.pdf; not the root, nor a sheet from outside the folder, which other boards
    may share."""
    from . import freeze
    root = os.path.splitext(pcb)[0] + ".kicad_sch"
    if not os.path.exists(root):
        return []
    folder = os.path.realpath(os.path.dirname(os.path.abspath(pcb)))
    place, _, _ = freeze.sheets(root)
    return sorted(p for p in place if os.path.realpath(p) != os.path.realpath(root)
                  and not os.path.relpath(os.path.realpath(p), folder).startswith("..") and title_block(_read(p)))


def stamp(pcb, old_label, new_label, date, text_date=None, dry=False, fill=True):
    """Write new_label and the date (the title block's text, in the project's date format) into the board, its root
    schematic and the sheets under it that have a title block (sheets_to_stamp). The board and the root schematic
    each take the other's title and company where they have none (unless fill is False: a board already released,
    whose fingerprint they would change). A silkscreen text with the old label typed in gets
    the new one, and an ISO date in it text_date (the date's ISO text; date when not given). Every file is checked
    before any is written: each may change only in its stamps and the fields it took. Returns the files changed (with
    dry, the files it would change, writing none)."""
    text_date = text_date or date
    root_sch = os.path.splitext(pcb)[0] + ".kicad_sch"
    files = [pcb] + ([root_sch] if os.path.exists(root_sch) else [])
    texts = {p: _read(p) for p in files}
    fields = {}
    if fill and len(files) == 2:       # the title and company each has that the other hasn't
        for path, other in ((pcb, root_sch), (root_sch, pcb)):
            fields[path] = {k: title_field(texts[other], k) for k in ("title", "company")
                            if not title_field(texts[path], k).strip()}
    edits = []
    for path in files + sheets_to_stamp(pcb):
        before = texts[path] if path in texts else _read(path)
        base = _stamp_title_block(before, None, None, path, fields.get(path))
        after = _stamp_title_block(base, new_label, date, path)
        if path == pcb and old_label:
            word = rf"(?<![\w.]){re.escape(old_label)}(?![\w.])"

            def gr(m):
                s = m.group(2)
                if re.search(word, s):
                    s = re.sub(DATE, text_date, re.sub(word, new_label, s))
                return m.group(1) + s + m.group(3)
            after = TEXT.sub(gr, after)
        if mask(after) != mask(base):
            raise SystemExit(f"{path}: stamping changed more than the version and date; nothing written")
        if after != before:
            edits.append((path, after))
    for path, after in [] if dry else edits:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(after)
    return [path for path, _ in edits]


def locked(pcb, files=()):
    """KiCad has the board's folder open, or the folder of one of files (a sheet in a subfolder): their lock files."""
    dirs = {os.path.dirname(os.path.abspath(f)) for f in [pcb, *files]}
    return sorted(x for d in dirs for x in glob.glob(os.path.join(d, "~*.lck")))


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
