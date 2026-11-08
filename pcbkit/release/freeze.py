"""A frozen KiCad copy of a released board, in its release folder's kicad/ (pcbkit release writes it).

The copy is a KiCad project of its own: the .kicad_pro, the root schematic and every sheet under it, the .kicad_pcb,
the .kicad_dru and the project's library tables (fp-lib-table, sym-lib-table, design-block-lib-table), so any
released board opens in KiCad beside the working one or another release. Not the .kicad_prl (window state), lock
files, KiCad 10's Local History (.history/) or backups. Footprints and symbols are embedded in the board and the
schematics; the library tables and 3-D models are not, so every path relative to the project (${KIPRJMOD}/..., or a
3-D model's bare relative path) is rewritten to reach the same file from the copy's folder. They point at the
project's library as it is now, not as it was at the release. What the copy refers to and doesn't find (a missing
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


def sheets(root_sch):
    """The root schematic and every sheet file under it, each once, as paths relative to the root's folder; and the
    sheet files that aren't found there or lie outside it."""
    base = os.path.dirname(os.path.abspath(root_sch))
    found, missing, todo = [], [], [os.path.basename(root_sch)]
    while todo:
        rel = todo.pop(0)
        if rel in found:
            continue
        found.append(rel)
        text = _read(os.path.join(base, rel))
        for name in SHEETFILE.findall(text):
            name = name.replace("\\", "/")
            # relative to the project's folder, or failing that to the sheet that names it
            p = next((q for q in (os.path.normpath(os.path.join(d, name))
                                  for d in (base, os.path.dirname(os.path.join(base, rel)))) if os.path.isfile(q)), "")
            if not p or os.path.relpath(p, base).startswith(".."):
                missing.append(name)
            elif os.path.relpath(p, base) not in found:
                todo.append(os.path.relpath(p, base))
    return found, sorted(set(missing))


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
    to dst; what they name and don't find: (file, path, why), a sheet file outside the project's folder included)."""
    src = os.path.dirname(os.path.abspath(pcb))
    stem = os.path.splitext(os.path.basename(pcb))[0]
    os.makedirs(dst)
    sch = os.path.join(src, stem + ".kicad_sch")
    files, missing = sheets(sch) if os.path.exists(sch) else ([], [])
    files = [stem + ".kicad_pro", stem + ".kicad_pcb", stem + ".kicad_dru"] + files + list(LIB_TABLES)
    written = []
    for rel in files:
        p = os.path.join(src, rel)
        if not os.path.isfile(p):
            continue
        out = os.path.join(dst, rel)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        if rel.endswith(".kicad_dru"):
            shutil.copy2(p, out)
        else:
            text = _read(p)
            with open(out, "w", encoding="utf-8") as fh:     # ${KIPRJMOD} is the copy's folder, for every sheet
                fh.write(_rewrite(text, src, dst, rel.endswith(".kicad_pcb")))
        written.append(rel.replace(os.sep, "/"))
    return written, [(stem + ".kicad_sch", m, "sheet file not in the board's folder: not copied") for m in missing] \
        + unresolved(dst)
