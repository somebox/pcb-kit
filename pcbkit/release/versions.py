"""Board versions: semver in the data ("1.0.0"), a label on the board ("v1.0", or "v1.0.1" once there is a patch),
change fingerprints that ignore the version and date stamps, and stamping a new version into the KiCad files.

Where a version is written on a board:
- the title block's rev (and its date) in the .kicad_pcb and in the root .kicad_sch, whether KiCad wrote the block
  one field a line or a generator wrote it on one line; a file without one gets one after its (paper ...);
- any silkscreen text (gr_text) that carries the old label as a word, e.g. "v1.0" on a front tag or
  "<name> v1.0 2026-10-19" on the back; a date in such a text becomes the release date.
Each stamp must change exactly what it means to: the masked text before and after must be identical.
"""
import csv
import datetime
import glob
import hashlib
import io
import json
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


def neutral(text):
    """The file with neutral version and date stamps, so that two versions of a board fab the same."""
    b = title_block(text)
    if b:
        block = re.sub(r'(\(rev )' + STR, r'\1"v0.0"', text[b[0]:b[1]])
        block = re.sub(r'(\(date )' + STR, r'\1"2000-01-01"', block)
        text = text[:b[0]] + block + text[b[1]:]
    return _label_texts(text, "v0.0", "2000-01-01")


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
            with zipfile.ZipFile(p) as z:
                for n in sorted(z.namelist()):
                    lines = io.TextIOWrapper(z.open(n), encoding="utf-8", errors="replace").read().splitlines()
                    h.update(n.split("-")[-1].encode())
                    h.update("\n".join(l for l in lines if not l.startswith(("%TF", "G04", ";", "%TA"))).encode())
        elif f.endswith(".csv"):
            rows = list(csv.reader(open(p, newline="", encoding="utf-8-sig")))
            drop = {i for i, c in enumerate(rows[0]) if c in ("Description", "Package")} \
                if rows and f.startswith("BOM-") and f.endswith("-pcbway.csv") else set()
            h.update(f.split("-")[0].encode() + b"\0" + "\n".join(
                "\t".join(c for i, c in enumerate(r) if i not in drop) for r in rows).encode())
    return h.hexdigest()


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
