"""Board versions: semver in the data ("1.0.0"), a label on the board ("v1.0", or "v1.0.1" once there is a patch),
change fingerprints that ignore the version and date stamps, and stamping a new version into the KiCad files.

Where a version is written on a board:
- the title block's rev (and its date) in the .kicad_pcb and in the root .kicad_sch;
- any silkscreen text (gr_text) that carries the old label as a word, e.g. "v1.0" on a front tag or
  "<name> v1.0 2026-10-19" on the back; a date in such a text becomes the release date.
Each stamp must change exactly what it means to: the masked text before and after must be identical.
"""
import datetime
import glob
import hashlib
import io
import os
import re
import zipfile

from .. import tomlw

SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
REV = re.compile(r"^v?(\d+)(?:\.(\d+))?(?:\.(\d+))?")
LABEL_IN_TEXT = r"(?<![\w.])v\d+(?:\.\d+){1,2}(?![\w.])"
DATE = r"\d{4}-\d{2}-\d{2}"
TEXT = re.compile(r'(\(gr_text ")((?:[^"\\]|\\.)*)(")')
TITLE_BLOCK = re.compile(r"\n(\t*)\(title_block\n.*?\n\1\)", re.S)


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


def title_rev(text):
    m = TITLE_BLOCK.search(text)
    r = re.search(r'\(rev "([^"]*)"\)', m.group(0)) if m else None
    return r.group(1) if r else ""


# ---- fingerprints ---------------------------------------------------------------------------------------------------
def mask(text):
    """The file with its version and date stamps blanked: every title block's rev and date, and the version label and
    dates in any silkscreen text that carries a version."""
    def tb(m):
        return re.sub(r'\n\t*\((?:rev|date) "[^"]*"\)', "", m.group(0))

    def gr(m):
        s = m.group(2)
        if re.search(LABEL_IN_TEXT, s):
            s = re.sub(DATE, "#", re.sub(LABEL_IN_TEXT, "v#", s))
        return m.group(1) + s + m.group(3)
    return TEXT.sub(gr, TITLE_BLOCK.sub(tb, text))


def design_files(pcb):
    """The board and every schematic sheet beside it."""
    folder = os.path.dirname(pcb)
    return [pcb] + sorted(glob.glob(os.path.join(folder, "*.kicad_sch")))


def source_fingerprint(pcb):
    h = hashlib.sha1()
    for f in design_files(pcb):
        h.update(os.path.basename(f).encode() + b"\0")
        h.update(mask(open(f, encoding="utf-8").read()).encode())
    return h.hexdigest()


def preview_source(pcb):
    """What previews are made from: the board and every schematic sheet, a sha1 each (the stamps included, since the
    previews show them). A previews folder keeps it as SOURCE.txt."""
    return "".join(f"{hashlib.sha1(open(f, 'rb').read()).hexdigest()}  {os.path.basename(f)}\n"
                   for f in design_files(pcb))


def fab_fingerprint(out):
    """A fab output folder (pcbkit fab, JLC files) hashed without what changes from run to run: Gerber and drill
    header comments and attributes (dates, the project ID with its rev) and file names."""
    h = hashlib.sha1()
    for f in sorted(os.listdir(out)):
        p = os.path.join(out, f)
        if f.endswith(".zip"):
            with zipfile.ZipFile(p) as z:
                for n in sorted(z.namelist()):
                    lines = io.TextIOWrapper(z.open(n), encoding="utf-8", errors="replace").read().splitlines()
                    h.update(n.split("-")[-1].encode())
                    h.update("\n".join(l for l in lines if not l.startswith(("%TF", "G04", ";", "%TA"))).encode())
        elif f.endswith(".csv"):
            h.update(f.split("-")[0].encode() + open(p, "rb").read())
    return h.hexdigest()


# ---- stamping -------------------------------------------------------------------------------------------------------
def _stamp_title_block(text, new_label, date, path):
    m = TITLE_BLOCK.search(text)
    if not m:
        raise SystemExit(f"{path}: no title block to stamp")
    block, ind = m.group(0), m.group(1) + "\t"
    for key, val in (("date", date), ("rev", new_label)):
        if re.search(rf'\n\t*\({key} "[^"]*"\)', block):
            block, n = re.subn(rf'(\n\t*\({key} ")[^"]*("\))', rf"\g<1>{val}\g<2>", block)
            assert n == 1, f"{path}: {n} {key} fields in the title block"
        else:                          # KiCad's order: title, date, rev, company, comments
            after = (re.search(r'\n\t*\(date "[^"]*"\)', block) if key == "rev" else None) or \
                re.search(r'\n\t*\(title "[^"]*"\)', block)
            at = after.end() if after else block.index("\n", 1)
            block = block[:at] + f'\n{ind}({key} "{val}")' + block[at:]
    return text[:m.start()] + block + text[m.end():]


def stamp(pcb, old_label, new_label, date):
    """Write new_label (and the date) into the board and its root schematic. Returns the files changed."""
    changed = []
    root_sch = os.path.splitext(pcb)[0] + ".kicad_sch"
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
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(after)
            changed.append(path)
    return changed


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
