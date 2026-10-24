"""The production record: every PCB's version, the orders and what is in stock, in one place (pcbkit.toml
[production]).

  pcbkit production [--check]

Reads what people keep (pcbkit never writes these):
- <dir>/orders/<name>/order.toml: an order: title, house, fab notes, notes; [[board]] pcb, version (pinned, quoted),
  pcbs, assembled, house, fab (what to tell the fab for this board only: a black mask for a front panel);
  [[extra]] part, qty, buy, note (bought with the order, not placed on a board: spares, a rework kit); [[event]]
  date, status (draft, submitted, in-production, shipped, received, problem, cancelled), and optionally pcb (one
  board or a list; default all), order_no, price, currency, note.
- <dir>/stock.toml: [[board]] pcb, version, bare, assembled, built, tested; [parts] name = count, a name being a
  hand-soldered part's part number (MPN without a "(...)" note, else its LCSC number) or a [[production.part]]
  rule's part.
and what pcbkit release writes: each PCB's releases.toml and <dir>/boards.toml (its last checks).

Writes:
- the generated section of the production page (PRODUCTION.md) between its pcbkit markers, the rest left as it is:
  boards in production, every PCB (its version in KiCad and released, state, parts cost, latest order, stock, how
  many the parts on hand build), parts the open orders need against stock, the orders, problems;
- <dir>/orders/<name>/ORDER.md for every order, and for each open one upload/<pcb>/: the files its fab house takes
  from the pinned release, the board's README and previews (upload/ is not committed).

What a board needs besides the parts the assembler places: its hand-soldered parts (not placed, in the BOM, with
an MPN or LCSC number), and what [[production.part]] rules add from a footprint's field. A board ordered bare
(assembled = 0) needs the placed parts by hand too. pcbkit release records these with each version, so an order
counts what the version it pins needs, not what the board needs now (`pcbkit release --refresh-parts` records them
again after the rules change).
  pcb = "boards/*"  field = "Panel"  match = '\\((B\\d+K)\\)'  part = ["9 mm pot {1}", "knob"]  buy = "Thonk"
({0} is the whole match, {1} the first group, in part and buy; part = "" for nothing.) The first rule that matches wins within its
group: rules with group = "cable" are matched apart from those without, so one connector can give a panel part and
a cable. replace = true drops the footprint's own hand part (a breakout's pot, already counted by the module it
serves). buy says where the part comes from; a hand part's LCSC number serves as its source.
Parts costs come from konnect's JLC parts cache (unit prices: an estimate, not a quote).

--check writes nothing and exits 1 on problems: a board whose KiCad version isn't its last release's, changes not
released, an order pinning a version that doesn't exist or lacks the files its house needs, malformed data, or a page
that is out of date.
"""
import argparse
import datetime
import fnmatch
import glob
import hashlib
import os
import re
import shutil
import sqlite3
import sys

from . import tomlw
from .config import Project
from .release import versions as V

STATUSES = ("draft", "submitted", "in-production", "shipped", "received", "problem", "cancelled")
CLOSED = ("received", "cancelled")
BEGIN, END = "<!-- pcbkit:production:begin -->", "<!-- pcbkit:production:end -->"
HOUSES = {"jlcpcb": ("JLCPCB", ["GERBER-*.zip"], ["BOM-*-jlcpcb.csv", "CPL-*-jlcpcb.csv"]),
          "pcbway": ("PCBWay", ["GERBER-*.zip"], ["BOM-*-pcbway.csv", "centroid-*-pcbway.csv"])}
NOTE = re.compile(r"\s+\([^)]*\)\s*$")       # "BCM847BS,115 (matched pair)", not "B3B-XH-A(LF)(SN)"
PICTURES = ("render-top.png", "pcb-front.png")  # a board's picture in ORDER.md: the first of its previews found


# ---- the board files, read as text (no KiCad needed) ----------------------------------------------------------------
def footprints(pcb_path):
    t = open(pcb_path, encoding="utf-8").read()
    out = []
    for m in re.finditer(r'\n\t\(footprint "([^"]*)"', t):
        fb = t[m.start():t.find("\n\t)", m.end())]
        attr = re.search(r"\n\t\t\(attr ([^)]*)\)", fb)
        flags = set(attr.group(1).split()) if attr else set()
        flags |= {k for k in ("dnp", "exclude_from_bom", "exclude_from_pos_files")
                  if re.search(rf"\n\t\t\({k} yes\)", fb)}
        out.append(dict(fields=dict(re.findall(r'\(property "([^"]*)" "((?:[^"\\]|\\.)*)"', fb)), flags=flags))
    return out


def placed_by(project, profile):
    if not profile["assembly"]:
        return lambda fp: False
    placed = project.release["placed"]
    m = re.match(r"^field:([^=]+)=(.*)$", placed or "")
    if m:
        return lambda fp: fp["fields"].get("LCSC") and fp["fields"].get(m.group(1)) == m.group(2) \
            and "dnp" not in fp["flags"]
    return lambda fp: fp["fields"].get("LCSC") and "dnp" not in fp["flags"] \
        and not fp["flags"] & {"exclude_from_bom", "exclude_from_pos_files"}


def part_name(f):
    """A part's name in the needs and in stock.toml: its MPN without a " (...)" note, else its LCSC number."""
    return NOTE.sub("", f.get("MPN", "")) or f.get("LCSC", "")


def board_parts(project, pcb):
    """What one PCB needs: placed {LCSC: n} (for costs) and placed_named {name: n} (for a bare board), hand {name: n},
    extra {name: n} from the part rules; where {name: source} for the parts that have one."""
    fps = footprints(os.path.join(project.root, pcb["path"]))
    is_placed = placed_by(project, pcb["profile"])
    rules = [r for r in project.production["part"] if fnmatch.fnmatch(pcb["path"], r.get("pcb", "*"))]
    groups = list(dict.fromkeys(r.get("group", "") for r in rules))
    placed, placed_named, hand, extra, where = {}, {}, {}, {}, {}

    def add(d, name, source=""):
        d[name] = d.get(name, 0) + 1
        if source:
            where.setdefault(name, source)
    for fp in fps:
        f = fp["fields"]
        if "dnp" in fp["flags"]:
            continue
        replaced = False
        for group in groups:                 # the first rule that matches wins, in each group
            for rule in (r for r in rules if r.get("group", "") == group):
                m = re.search(rule["match"], f.get(rule["field"], "")) if f.get(rule["field"]) else None
                if m:
                    names = rule["part"] if isinstance(rule["part"], list) else [rule["part"]]
                    groups_ = [m.group(0)] + list(m.groups())
                    for name in (n.format(*groups_) for n in names if n):
                        add(extra, name, rule.get("buy", "").format(*groups_))
                    replaced = replaced or rule.get("replace", False)
                    break
        lcsc = f"LCSC {f['LCSC']}" if f.get("LCSC") else ""
        if is_placed(fp):
            placed[f["LCSC"]] = placed.get(f["LCSC"], 0) + 1
            add(placed_named, part_name(f), lcsc)
        elif "exclude_from_bom" not in fp["flags"] and (f.get("MPN") or f.get("LCSC")) and not replaced:
            add(hand, part_name(f), lcsc)
    return dict(placed=placed, placed_named=placed_named, hand=hand, extra=extra, where=where)


def sources(project, pcbs):
    """Where each part comes from, across every PCB as it is now: a rule's buy, else a hand part's LCSC number."""
    out = {}
    for p in pcbs.values():
        for name, source in board_parts(project, p)["where"].items():
            out.setdefault(name, source)
    return out


def pinned_parts(project, p, version):
    """What a board of this version needs, as its release recorded it; (parts, recorded)."""
    r = next((r for r in p["releases"] if r["version"] == version), None)
    if r and r.get("parts"):
        return {k: dict(r["parts"].get(k, {})) for k in ("placed", "placed_named", "hand", "extra", "where")}, True
    return board_parts(project, p), False


class Cache:
    """konnect's JLC parts cache: unit price, Basic or Extended, stock."""

    def __init__(self):
        from .jlc import DB
        self.db = sqlite3.connect(DB) if os.path.exists(DB) else None
        self.date = datetime.date.fromtimestamp(os.path.getmtime(DB)) if self.db else None
        self.rows = {}

    def get(self, lcsc):
        if lcsc not in self.rows:
            r = self.db.execute("select Price, Library_Type, Stock, MFR_Part from components where LCSC = ?",
                                (lcsc,)).fetchone() if self.db else None
            self.rows[lcsc] = dict(price=float(r[0] or 0), extended=r[1] == "Extended", stock=int(r[2] or 0),
                                   mpn=r[3]) if r else None
        return self.rows[lcsc]

    def cost(self, placed):
        total, ext, missing = 0.0, [], []
        for lcsc, n in sorted(placed.items()):
            r = self.get(lcsc)
            if not r:
                missing.append(lcsc)
                continue
            total += n * r["price"]
            if r["extended"]:
                ext.append(lcsc)
        return dict(total=total, extended=ext, missing=missing)


# ---- the data -------------------------------------------------------------------------------------------------------
def version_of(v, where, problems):
    if not isinstance(v, str):
        problems.append(f"{where}: version {v!r} must be a quoted string (\"1.0.0\")")
        return str(v)
    try:
        V.parse(v)
    except ValueError as e:
        problems.append(f"{where}: {e}")
    return v


def load_orders(project, pcbs, problems):
    root = os.path.join(project.root, project.production["dir"], "orders")
    orders = []
    for name in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        path = os.path.join(root, name, "order.toml")
        if not os.path.exists(path):
            continue
        where = project.rel(path)
        try:
            o = tomlw.load(path)
        except Exception as e:  # noqa: BLE001 - a TOML error names the line
            problems.append(f"{where}: {e}")
            continue
        o["name"], o["dir"], o["path"] = name, os.path.dirname(path), where
        boards = o.get("board", [])
        if not boards:
            problems.append(f"{where}: no [[board]]")
        for b in boards:
            b["version"] = version_of(b.get("version", ""), f"{where} {b.get('pcb')}", problems)
            b.setdefault("house", o.get("house", "jlcpcb"))
            b.setdefault("pcbs", 0)
            b.setdefault("assembled", 0)
            b["events"] = []
            if b.get("pcb") not in pcbs:
                problems.append(f"{where}: no PCB {b.get('pcb')!r} in the project")
            elif not any(r["version"] == b["version"] for r in pcbs[b["pcb"]]["releases"]):
                problems.append(f"{where}: {b['pcb']} has no release {b['version']}")
            else:
                if pcbs[b["pcb"]]["last"]["version"] != b["version"]:
                    b["newer"] = pcbs[b["pcb"]]["last"]["version"]
                missing = release_files(pcbs[b["pcb"]], b["version"], b["house"], b["assembled"])[2]
                if missing:
                    problems.append(f"{where}: {b['pcb']} {b['version']} has no {', '.join(missing)} for "
                                    f"{b['house']}" + (", which assembles it" if b["assembled"] else ""))
            if b["house"] not in HOUSES:
                problems.append(f"{where}: {b.get('pcb')}: house {b['house']!r} is not one of {', '.join(HOUSES)}")
            if isinstance(b.get("fab"), str):
                b["fab"] = [b["fab"]]
        o.setdefault("extra", [])
        for x in o["extra"]:
            if not x.get("part") or not isinstance(x.get("qty"), int) or x["qty"] < 1:
                problems.append(f"{where}: [[extra]] {x.get('part')!r}: needs a part and a qty of 1 or more")
        o["extra"] = [x for x in o["extra"] if x.get("part") and isinstance(x.get("qty"), int) and x["qty"] > 0]
        events = sorted(o.get("event", []), key=lambda e: str(e.get("date", "")))
        for e in events:
            if e.get("status") not in STATUSES:
                problems.append(f"{where}: event {e.get('date')}: status {e.get('status')!r} is not one of "
                                f"{', '.join(STATUSES)}")
            if not isinstance(e.get("date"), datetime.date):
                problems.append(f"{where}: event {e.get('status')}: date {e.get('date')!r} is not a date (2026-10-20)")
            only = e.get("pcb")
            only = [only] if isinstance(only, str) else only
            for b in boards:
                if not only or b.get("pcb") in only:
                    b["events"].append(e)
        o["events"] = events
        if not events:
            problems.append(f"{where}: no [[event]] (a new order starts with status = \"draft\")")
        for b in boards:
            last = b["events"][-1] if b["events"] else {}
            b["status"], b["since"] = last.get("status", "draft"), last.get("date", "")
            b["note"] = last.get("note", "")
            b["own_note"] = b["note"] if last.get("pcb") else ""    # the tables leave a note for every board to Events
            for k in ("order_no", "price", "currency"):
                b[k] = next((e[k] for e in reversed(b["events"]) if k in e), "")
        o["open"] = any(b["status"] not in CLOSED for b in boards)
        orders.append(o)
    return orders


def load_stock(project, pcbs, problems):
    path = os.path.join(project.root, project.production["dir"], "stock.toml")
    if not os.path.exists(path):
        return dict(board=[], parts={})
    s = tomlw.load(path)
    for b in s.get("board", []):
        b["version"] = version_of(b.get("version", ""), f"{project.rel(path)} {b.get('pcb')}", problems)
        if b.get("pcb") not in pcbs:
            problems.append(f"{project.rel(path)}: no PCB {b.get('pcb')!r} in the project")
    s.setdefault("parts", {})
    s.setdefault("board", [])
    return s


# ---- the state of every PCB -----------------------------------------------------------------------------------------
def survey(project, problems, watch):
    state_path = os.path.join(project.root, project.production["dir"], "boards.toml")
    checked = tomlw.load(state_path).get("pcb", {}) if os.path.exists(state_path) else {}
    pcbs = {}
    for pcb in project.pcbs():
        key, path = pcb["key"], os.path.join(project.root, pcb["path"])
        rdir = project.release_dir(pcb)
        rel = V.releases(rdir)
        last = rel[-1] if rel else None
        on_board = V.title_rev(open(path, encoding="utf-8").read())
        chk = checked.get(key, {})
        p = dict(pcb, releases=rel, last=last, on_board=on_board, checked=chk, rdir=rdir)
        if pcb["excluded"]:
            p["state"] = "excluded"
        elif last:
            src = V.source_fingerprint(path, project.fab_settings(pcb["profile"]))
            p["state"] = "released"
            if src != last["source"] and src not in last.get("equivalent", []):
                p["state"] = "changed"
                problems.append(f"{pcb['path']}: changed since {last['label']}: run pcbkit release")
            if on_board != last["label"]:
                problems.append(f"{pcb['path']}: KiCad says {on_board or 'no version'}, the last release is "
                                f"{last['label']}: run pcbkit release")
            pdir = project.previews_dir(pcb)
            if project.release["previews"] and os.path.isdir(pdir):
                src_txt = os.path.join(pdir, "SOURCE.txt")
                if not os.path.exists(src_txt) or open(src_txt).read() != V.preview_source(path):
                    watch.append(f"{project.rel(pdir)}: previews of another version than the board's")
        elif chk.get("state") == "mockup":
            p["state"] = "mockup"
        elif chk:
            p["state"] = "checked" if chk.get("state") == "checked" else "not released"
        else:
            p["state"] = "not checked"
        pcbs[key] = p
    return pcbs


def mockup_why(chk):
    return ", ".join(f"{k} {chk[k]}" for k in ("erc", "drc", "unconnected", "parity") if chk.get(k))


# ---- the outputs ----------------------------------------------------------------------------------------------------
def money(x, cur=""):
    if x in ("", None):
        return ""
    return f"{x:.2f} {cur}".strip() if isinstance(x, (int, float)) else str(x)


def table(head, rows):
    if not rows:
        return "_none_\n"
    esc = lambda c: str(c).replace("|", "\\|").replace("\n", " ")  # noqa: E731
    return "".join("| " + " | ".join(esc(c) for c in r) + " |\n"
                   for r in [head, ["---"] * len(head)] + rows)


def link(frm, to, text):
    return f"[{text}]({os.path.relpath(to, frm)})"


def needs(parts, bare=False):
    """What finishing one board takes by hand: its hand parts and the rules' parts, and for a bare board (ordered
    without assembly) the parts the assembler would have placed."""
    out = {}
    for group in ("hand", "extra") + (("placed_named",) if bare else ()):
        for k, n in parts.get(group, {}).items():
            out[k] = out.get(k, 0) + n
    return out


def can_build(need, have):
    if not need:
        return None
    return min(have.get(k, 0) // n for k, n in need.items())


def stock_line(b):
    return ", ".join(f"{b.get(k, 0)} {k}" for k in ("bare", "assembled", "built", "tested") if b.get(k))


def release_files(p, version, house, assembled):
    """The release's folder, the files a fab house takes from it, and the patterns it has no file for."""
    r = next((r for r in p["releases"] if r["version"] == version), None)
    if not r or house not in HOUSES:
        return None, [], []
    vdir = os.path.join(p["rdir"], r["label"])
    _, base, asm = HOUSES[house]
    found = [(pat, sorted(glob.glob(os.path.join(vdir, pat)))) for pat in base + (asm if assembled else [])]
    return vdir, [f for _, fs in found for f in fs], [pat for pat, fs in found if not fs]


def previews_of(project, p, version):
    """The board's previews folder when its previews are of this version (their SOURCE.txt is the one the release
    recorded), else None."""
    rel = next((r for r in p["releases"] if r["version"] == version), None)
    src = os.path.join(project.previews_dir(p), "SOURCE.txt")
    if rel and os.path.exists(src) and hashlib.sha1(open(src).read().encode()).hexdigest() == rel.get("previews"):
        return os.path.dirname(src)
    return None


def hand_totals(boards):
    """{part: n} to finish these order lines by hand: each board's needs times the boards to build (the assembled ones,
    or every PCB of a bare order)."""
    total = {}
    for b in boards:
        if b["status"] == "cancelled" or not b.get("parts"):
            continue
        for k, n in needs(b["parts"], bare=not b["assembled"]).items():
            total[k] = total.get(k, 0) + n * (b["assembled"] or b["pcbs"])
    return total


def shopping(total, have, where):
    return [[k, n, have.get(k, 0), max(0, n - have.get(k, 0)) or "", where.get(k, "")] for k, n in sorted(total.items())]


def order_md(project, o, pcbs, cache, have, where):
    d = o["dir"]
    out = [f"# Order {o['name']}" + (f": {o['title']}" if o.get("title") else ""), "",
           "Generated by `pcbkit production` from [order.toml](order.toml); edit that, not this.", ""]
    rows = []
    for b in o["board"]:
        p = pcbs.get(b["pcb"])
        lab = V.label(b["version"], project.release["label"]) if _semver(b["version"]) else b["version"]
        vdir = release_files(p, b["version"], b["house"], b["assembled"])[0] if p else None
        rows.append([b["pcb"], link(d, vdir, lab) if vdir else lab + " (no release)", HOUSES.get(b["house"], [b["house"]])[0],
                     b["pcbs"], b["assembled"], b["status"] + (f" ({b['since']})" if b["since"] else ""),
                     b["order_no"], money(b["price"], b["currency"]), b["own_note"]])
    out += [table(["PCB", "version", "house", "PCBs", "assembled", "status", "order no.", "price", "note"], rows)]
    if o.get("fab"):
        out += ["## Tell the fab", ""] + [f"- {x}" for x in o["fab"]] + [""]
    if o.get("notes"):
        out += ["## Notes", ""] + [f"- {x}" for x in o["notes"]] + [""]
    out += ["## Upload", "",
            "Each board's files are in `upload/<pcb>/` (rebuilt by `pcbkit production` while the order is open, not "
            "committed), taken from the pinned release.", "",
            "- JLCPCB: PCB, the Gerber zip with the settings above; PCB Assembly, the BOM and CPL (`-jlcpcb`), the "
            "side the CPL places. Check the parts list, then the placement preview: rotations and polarity (diodes, "
            "LEDs, transistors, each IC's pin 1).",
            "- PCBWay: PCB, the same Gerber zip; assembly, the BOM and centroid (`-pcbway`). PCBWay buys the parts by "
            "manufacturer part number: check its quote's parts list.",
            "- Once placed: an `[[event]]` in order.toml (status `submitted`, `order_no`, `price`), then "
            "`pcbkit production`.", ""]
    for b in o["board"]:
        p = pcbs.get(b["pcb"])
        if not p:
            continue
        vdir, files, missing = release_files(p, b["version"], b["house"], b["assembled"])
        house = HOUSES.get(b["house"], [b["house"]])[0]
        out.append(f"### {b['pcb']} {b['version']} at {house}")
        out.append("")
        if not files or missing:
            out += [f"Not ready: {'no release of this version' if not vdir else 'no ' + ', '.join(missing)} "
                    "(Problems in PRODUCTION.md).", ""]
            continue
        pdir = previews_of(project, p, b["version"])
        pic = next((os.path.join(pdir, f) for f in PICTURES if pdir and os.path.exists(os.path.join(pdir, f))), None)
        if pic:
            out += [f'<img src="{os.path.relpath(pic, d)}" width="320" alt="{b["pcb"]} {b["version"]}">', ""]
        names = [os.path.basename(f) for f in files]
        out.append(f"- PCB: `{names[0]}`, {b['pcbs']} boards" + (f", {b['assembled']} assembled" if b["assembled"] else ""))
        for x in b.get("fab", []):
            out.append(f"- Tell the fab: {x}")
        if b["assembled"]:
            out.append(f"- Assembly: {', '.join(f'`{n}`' for n in names[1:])}")
            pp = b["parts"]
            c = cache.cost(pp["placed"])
            out.append(f"- Placed parts: {sum(pp['placed'].values())} ({len(pp['placed'])} part numbers), "
                       f"about {c['total']:.2f} USD a board, {len(c['extended'])} Extended"
                       + (f"; not in the parts cache: {', '.join(c['missing'])}" if c["missing"] else ""))
            short = [f"{lcsc} ({cache.get(lcsc)['stock']} left, needs {n * b['assembled']})"
                     for lcsc, n in pp["placed"].items() if b["status"] in ("draft", "problem")
                     and cache.get(lcsc) and cache.get(lcsc)["stock"] < n * b["assembled"]]
            if short:
                out.append(f"- Low at JLC (cache of {cache.date}): {', '.join(short)}")
        need = needs(b["parts"], bare=not b["assembled"])
        if need:
            out.append(f"- By hand, a board{' (bare: every part)' if not b['assembled'] and b['parts']['placed'] else ''}: "
                       + ", ".join(f"{n} × {k}" for k, n in sorted(need.items())))
        out.append("")
    total = hand_totals(o["board"])
    if total:
        out += ["## Parts for hand assembly", "",
                "Everything the order's boards need by hand, for the boards to build: the assembled ones, or every PCB "
                "of a bare line. On hand is stock.toml (shared by every order); where is the rule's `buy` in "
                "pcbkit.toml, or the part's LCSC number.", "",
                table(["part", "needed", "on hand", "to buy", "where"], shopping(total, have, where))]
    if o["extra"]:
        out += ["## Extras", "", "Bought with the order, not placed on a board: spares and rework parts. On hand is "
                "stock.toml.", "",
                table(["part", "qty", "on hand", "to buy", "where", "why"], [
                    [x["part"], x["qty"], have.get(x["part"], 0), max(0, x["qty"] - have.get(x["part"], 0)) or "",
                     x.get("buy", ""), x.get("note", "")] for x in o["extra"]])]
    if o["events"]:
        out += ["## Events", "", table(["date", "boards", "status", "order no.", "price", "note"], [
            [e.get("date", ""), ", ".join([e["pcb"]] if isinstance(e.get("pcb"), str) else e.get("pcb") or ["all"]),
             e.get("status", ""), e.get("order_no", ""), money(e.get("price", ""), e.get("currency", "")),
             e.get("note", "")] for e in o["events"]])]
    return "\n".join(out).rstrip() + "\n"


def _semver(v):
    return bool(V.SEMVER.match(str(v)))


def build_upload(project, o, pcbs):
    up = os.path.join(o["dir"], "upload")
    shutil.rmtree(up, ignore_errors=True)
    for b in o["board"]:
        p = pcbs.get(b["pcb"])
        if not p:
            continue
        vdir, files, missing = release_files(p, b["version"], b["house"], b["assembled"])
        if not files or missing:
            continue
        dst = os.path.join(up, b["pcb"])
        os.makedirs(dst)
        for f in files + [os.path.join(vdir, x) for x in ("MANIFEST.txt", "schematic.pdf", "board.pdf")]:
            if os.path.exists(f):
                shutil.copy2(f, dst)
        readme = os.path.join(project.root, p["folder"], "README.md")
        if os.path.exists(readme) and not p["sub"]:
            shutil.copy2(readme, dst)
        pdir = previews_of(project, p, b["version"])
        if pdir:
            os.makedirs(os.path.join(dst, "previews"))
            for f in os.listdir(pdir):
                if os.path.isfile(os.path.join(pdir, f)) and f != "SOURCE.txt":
                    shutil.copy2(os.path.join(pdir, f), os.path.join(dst, "previews"))


def section(project, pcbs, parts, orders, stock, cache, problems, watch, where):
    page_dir = os.path.dirname(os.path.join(project.root, project.production["page"]))
    have = stock["parts"]
    out = [BEGIN, "<!-- generated by pcbkit production from the boards' releases.toml, "
           f"{project.production['dir']}/orders/*/order.toml and {project.production['dir']}/stock.toml: "
           "edit those, then run it again -->", ""]
    # in production
    rows = []
    for o in orders:
        for b in o["board"]:
            if b["status"] in CLOSED:
                continue
            p = pcbs.get(b["pcb"])
            lab = V.label(b["version"], project.release["label"]) if _semver(b["version"]) else b["version"]
            rows.append([link(page_dir, os.path.join(o["dir"], "ORDER.md"), o["name"]), b["pcb"], lab,
                         HOUSES.get(b["house"], [b["house"]])[0], f"{b['pcbs']} / {b['assembled']}",
                         b["status"] + (f" {b['since']}" if b["since"] else ""), b["order_no"],
                         money(b["price"], b["currency"]), b["own_note"]])
    out += ["### In production", "", table(["order", "PCB", "version", "house", "PCBs / assembled", "status",
                                            "order no.", "price", "last note"], rows)]
    # every PCB
    rows = []
    for key, p in pcbs.items():
        last = p["last"]
        st = p["state"] + (f" ({mockup_why(p['checked'])})" if p["state"] == "mockup" else "")
        pp = parts.get(key)
        cost = ""
        if pp and pp["placed"]:
            c = cache.cost(pp["placed"])
            cost = f"{c['total']:.2f}" + (f", {len(c['extended'])} ext." if c["extended"] else "")
        latest = next(((o, b) for o in reversed(orders) for b in o["board"] if b["pcb"] == key), None)
        stocked = [b for b in stock["board"] if b.get("pcb") == key]
        need = needs(pp) if pp else {}
        n = can_build(need, have)
        rows.append([link(page_dir, os.path.join(project.root, p["folder"]), key) if not p["sub"] else key,
                     p["on_board"], f"{last['label']} {last['date']}" if last else "", st, cost,
                     f"{latest[0]['name']}: {latest[1]['status']}" if latest else "",
                     "; ".join(f"{b['version']}: {stock_line(b)}" for b in stocked if stock_line(b)),
                     "" if n is None else n])
    out += ["### Boards", "", table(["PCB", "in KiCad", "released", "state", "parts a board (USD)", "latest order",
                                     "in stock", "parts on hand build"], rows)]
    if cache.date:
        out.append(f"Parts costs: placed parts at unit prices from konnect's JLC parts cache of {cache.date}; "
                   "Extended parts add a setup fee each per order.\n")
    # parts the open orders need
    total = hand_totals([b for o in orders for b in o["board"] if b["status"] not in CLOSED])
    for x in (x for o in orders if o["open"] for x in o["extra"]):
        total[x["part"]] = total.get(x["part"], 0) + x["qty"]
    out += ["### Parts for the open orders", "",
            "To finish every board of the open orders by hand: the assembled ones, or every PCB of a bare order "
            "(with the parts the assembler would have placed), as the versions they pin need; and the orders' "
            "extras.", "",
            table(["part", "needed", "on hand", "to buy", "where"], shopping(total, have, where))]
    # orders
    rows = [[link(page_dir, os.path.join(o["dir"], "ORDER.md"), o["name"]), o.get("title", ""),
             ", ".join(sorted({b["status"] for b in o["board"]})),
             o["events"][-1]["date"] if o["events"] else ""] for o in reversed(orders)]
    out += ["### Orders", "", table(["order", "what", "status", "last event"], rows)]
    out += ["### Problems", ""] + ([f"- {x}" for x in problems] or ["_none_"]) + [""]
    if watch:
        out += ["### To watch", ""] + [f"- {x}" for x in watch] + [""]
    out.append(END)
    return "\n".join(out) + "\n"


def splice(page, sec):
    if BEGIN in page and END in page:
        return page[:page.index(BEGIN)] + sec.rstrip("\n") + page[page.index(END) + len(END):]
    return page.rstrip("\n") + "\n\n" + sec


def build(project=None, check=False):
    project = project or Project()
    problems, watch = [], []
    pcbs = survey(project, problems, watch)
    orders = load_orders(project, pcbs, problems)
    stock = load_stock(project, pcbs, problems)
    parts = {k: board_parts(project, p) for k, p in pcbs.items() if p["state"] != "excluded"}
    cache = Cache()
    for o in orders:
        for b in o["board"]:
            b["parts"] = None
            if b.get("pcb") in pcbs:
                b["parts"], recorded = pinned_parts(project, pcbs[b["pcb"]], b["version"])
                if not recorded and o["open"]:
                    watch.append(f"{o['name']} {b['pcb']} {b['version']}: its release has no record of the parts it "
                                 "needs; counted from the board as it is now")
            if b["status"] == "problem" and b["pcb"]:
                watch.append(f"{o['name']} {b['pcb']}: {b['note'] or 'a problem was reported'}")
            if b.get("newer") and b["status"] in ("draft", "problem"):
                watch.append(f"{o['name']} {b['pcb']}: pins {b['version']}, the latest release is {b['newer']}")
    where = sources(project, pcbs)
    for x in (x for o in orders for x in o["extra"] if x.get("buy")):
        where.setdefault(x["part"], x["buy"])
    outputs = {os.path.join(o["dir"], "ORDER.md"): order_md(project, o, pcbs, cache, stock["parts"], where)
               for o in orders}
    page = os.path.join(project.root, project.production["page"])
    old = open(page, encoding="utf-8").read() if os.path.exists(page) else "# Production\n"
    stale = [p for p, text in outputs.items() if not os.path.exists(p) or open(p, encoding="utf-8").read() != text]
    sec = section(project, pcbs, parts, orders, stock, cache, problems, watch, where)
    outputs[page] = splice(old, sec)
    if check:
        if outputs[page] != old:
            problems.append(f"{project.production['page']}: out of date: run pcbkit production")
        problems += [f"{project.rel(p)}: out of date: run pcbkit production" for p in stale]
        return problems, watch
    for path, text in outputs.items():
        old_text = open(path, encoding="utf-8").read() if os.path.exists(path) else None
        if text != old_text:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
    for o in orders:
        if o["open"]:
            build_upload(project, o, pcbs)
        else:
            shutil.rmtree(os.path.join(o["dir"], "upload"), ignore_errors=True)
    print(f"{project.production['page']}: {len(pcbs)} PCBs, {len(orders)} orders "
          f"({sum(o['open'] for o in orders)} open), {len(problems)} problems")
    for x in problems:
        print("  problem:", x)
    return problems, watch


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="write nothing; exit 1 on problems")
    a = ap.parse_args(argv)
    project = Project()
    if not project.production["enabled"]:
        raise SystemExit(f"the production record is off in {project.path} ([production] enabled = false)")
    problems, watch = build(project, a.check)
    if a.check:
        for x in problems:
            print("problem:", x)
        for x in watch:
            print("watch:", x)
        sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
