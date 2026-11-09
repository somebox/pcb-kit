"""A frozen KiCad copy of a released board, in its release folder's kicad/ (pcbkit release writes it).

The copy is a KiCad project of its own: the .kicad_pro, the root schematic and every sheet under it, the .kicad_pcb,
the .kicad_dru and the project's library tables (fp-lib-table, sym-lib-table, design-block-lib-table), so any
released board opens in KiCad beside the working one or another release. A sheet from outside the board's folder
is copied too, into outside/, and a Sheetfile that wouldn't reach its sheet in the copy (an absolute path, a path
out of the folder) is made to reach the copied one: nothing in the copy's schematic is read from the working
project. Not the .kicad_prl (window state), lock files, KiCad 10's Local History (.history/) or backups.
Footprints and symbols are embedded in the board and the schematics; the library tables and 3-D models are not, so
every path relative to the project (${KIPRJMOD}/..., or a 3-D model's bare relative path) is rewritten to reach the
same file from the copy's folder. They point at the project's library as it is now, not as it was at the release. What the copy refers to and doesn't find (a missing
3-D model, an absolute path, a variable not set here) is listed, and MANIFEST.txt says so; KiCad's own library
variables (${KICAD10_3DMODEL_DIR}) are left to KiCad.
"""
import os
import re
import shutil

LIB_TABLES = ("fp-lib-table", "sym-lib-table", "design-block-lib-table")
PRJ = re.compile(r'"(?:\$\{KIPRJMOD\}|\$\(KIPRJMOD\))([/\\][^"]*)"')
MODEL = re.compile(r'(\(model ")((?:[^"\\]|\\.)*)(")')
URI = re.compile(r'\(uri "((?:[^"\\]|\\.)*)"\)')
VAR = re.compile(r"^\$[{(]([A-Za-z0-9_]+)[})]")
SHEETFILE = re.compile(r'\(property "Sheet ?file" "((?:[^"\\]|\\.)*)"')


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _expand(name, base):
    """A Sheetfile with its leading variable expanded as KiCad does: ${KIPRJMOD} (or $(KIPRJMOD)) is the project's
    folder, another variable its value here; one not set stays as written, and isn't found."""
    name = name.replace("\\", "/")
    m = VAR.match(name)
    if not m:
        return name
    value = base if m.group(1) == "KIPRJMOD" else os.environ.get(m.group(1))
    return name if value is None else value + "/" + name[m.end():].lstrip("/")


def sheets(root_sch):
    """The root schematic and every sheet file under it, each once: {its path: where it goes in the copy, relative
    to the copy's folder}; {a sheet's place in the copy: {each Sheetfile it names: that sheet's place}}; and the
    sheet files not found, as (the sheet naming it, the name). KiCad looks for a sheet file in the folder of the
    sheet that names it, then in the project's, as this does, after expanding a leading ${KIPRJMOD} or other
    variable. A sheet outside the project's folder (a shared one, or an absolute path) goes to the copy's outside/,
    so the copy holds every sheet it shows."""
    base = os.path.dirname(os.path.abspath(root_sch))
    root = os.path.abspath(root_sch)
    place, names, missing, todo = {root: os.path.basename(root)}, {}, [], [root]
    while todo:
        p = todo.pop(0)
        here = names[place[p]] = {}
        for name in SHEETFILE.findall(_read(p)):
            q = next((x for x in (os.path.normpath(os.path.join(d, _expand(name, base)))
                                  for d in (os.path.dirname(p), base)) if os.path.isfile(x)), "")
            if not q:
                missing.append((place[p], name))
                continue
            if q not in place:
                rel = os.path.relpath(q, base)
                if rel.startswith(".."):
                    stem, ext = os.path.splitext(os.path.basename(q))
                    rel = next(r for r in (os.path.join("outside", stem + (f"-{i}" if i else "") + ext)
                                           for i in range(len(place) + 1))
                               if r not in place.values() and not os.path.exists(os.path.join(base, r)))
                place[q] = rel
                todo.append(q)
            here[name] = place[q]
    return place, names, sorted(set(missing))


def _sheetfiles(text, here, names, copied):
    """A copied sheet's text with every Sheetfile that wouldn't find its sheet in the copy as written (an absolute
    path, one out of the project's folder, or through ${KIPRJMOD} or another variable) made relative to this sheet's
    folder in the copy."""
    folder = os.path.dirname(here)

    def found(name):
        return next((r for r in (os.path.normpath(os.path.join(d, name.replace("\\", "/"))) for d in (folder, ""))
                     if r in copied), None)

    def fix(m):
        name = m.group(1)
        target = names.get(name)
        if target is None or found(name) == os.path.normpath(target):
            return m.group(0)
        new = os.path.relpath(target, folder or ".").replace(os.sep, "/")
        return m.group(0)[:m.start(1) - m.start(0)] + new + '"'
    return SHEETFILE.sub(fix, text)


def _rewrite(text, src, dst, model_paths):
    """The text with every path relative to the project folder src made relative to dst, as ${KIPRJMOD}/...; with
    model_paths, a 3-D model's bare relative path too (KiCad looks for it in the project folder first)."""
    def to_dst(rel):
        return "${KIPRJMOD}/" + os.path.relpath(os.path.normpath(os.path.join(src, rel.lstrip("/\\"))),
                                                dst).replace(os.sep, "/")
    text = PRJ.sub(lambda m: '"' + to_dst(m.group(1)) + '"', text)
    if model_paths:
        text = MODEL.sub(lambda m: m.group(1) + (to_dst(m.group(2)) if m.group(2) and not os.path.isabs(m.group(2))
                                                 and not m.group(2).startswith(("$", "kicad-embed://"))
                                                 else m.group(2)) + m.group(3), text)
    return text


def _resolve(path, dst):
    """Where a path in the copy points, or why it can't be told: (absolute path or None, reason)."""
    m = VAR.match(path)
    if m:
        var = m.group(1)
        if var == "KIPRJMOD":
            return os.path.normpath(os.path.join(dst, path[m.end():].lstrip("/\\"))), ""
        if var.startswith("KICAD"):
            return None, ""                       # KiCad's own libraries: wherever KiCad is installed
        if var not in os.environ:
            return None, f"${{{var}}} is not set here"
        return os.path.normpath(os.environ[var] + path[m.end():]), ""
    if path.startswith("kicad-embed://"):
        return None, ""
    return (path if os.path.isabs(path) else os.path.normpath(os.path.join(dst, path))), ""


def unresolved(dst):
    """Every 3-D model and library the copy in dst names and doesn't find: (file, path, why)."""
    out = []
    for f in sorted(os.listdir(dst)):
        p = os.path.join(dst, f)
        if f.endswith(".kicad_pcb"):
            paths = [m.group(2) for m in MODEL.finditer(_read(p))]
        elif f in LIB_TABLES:
            paths = URI.findall(_read(p))
        else:
            continue
        for path in sorted(set(paths)):
            target, why = _resolve(path, dst)
            if why:
                out.append((f, path, why))
            elif target and not os.path.exists(target):
                out.append((f, path, "an absolute path, not found" if os.path.isabs(path) else
                            "not in the project either" if path.startswith(("${KIPRJMOD}", "$(KIPRJMOD)")) else
                            "not found"))
    return out


def freeze(pcb, dst):
    """Copy the board's KiCad project into dst (made here; it must not exist). Returns (the files written, relative
    to dst; what they name and don't find: (file, path, why), a missing sheet file included)."""
    src = os.path.dirname(os.path.abspath(pcb))
    stem = os.path.splitext(os.path.basename(pcb))[0]
    os.makedirs(dst)
    sch = os.path.join(src, stem + ".kicad_sch")
    place, names, missing = sheets(sch) if os.path.exists(sch) else ({}, {}, [])
    files = [(os.path.join(src, f), f) for f in (stem + ".kicad_pro", stem + ".kicad_pcb", stem + ".kicad_dru")] \
        + list(place.items()) + [(os.path.join(src, f), f) for f in LIB_TABLES]
    copied = {os.path.normpath(r) for r in place.values()}
    written = []
    for p, rel in files:
        if not os.path.isfile(p):
            continue
        out = os.path.join(dst, rel)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        if rel.endswith(".kicad_dru"):
            shutil.copy2(p, out)
        else:
            text = _read(p)
            if rel in names:                   # first, so that a sheet's ${KIPRJMOD} path reaches the copied sheet
                text = _sheetfiles(text, rel, names[rel], copied)
            text = _rewrite(text, src, dst, rel.endswith(".kicad_pcb"))   # ${KIPRJMOD} is the copy's folder
            with open(out, "w", encoding="utf-8") as fh:
                fh.write(text)
        written.append(rel.replace(os.sep, "/"))
    return written, [(here, name, "sheet file not found") for here, name in missing] + unresolved(dst)
