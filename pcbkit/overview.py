"""The project on one page for the user: docs/overview.html, made from one small file per design round.

    pcbkit overview                 build the page from docs/overview/r1.toml, r2.toml, ... (renders missing pictures)
    pcbkit overview new             start the next round: the last one carried forward, boards from the records
    pcbkit overview note "TEXT" [--on ID] [--mark MARK] [--state "WORDS"] [--chose OPTION] [--round N]
                                    add a dated note to a round (the newest), then build
    pcbkit overview --check         what is wrong with the rounds, what a build has still to do, and watch lines;
                                    writes nothing, so it goes with none of the others; exit 0 when nothing is left
    pcbkit overview --single [OUT]  also a copy with its pictures inside, to send or publish

A round file is the briefing, not the record: the status, the boards, a few lines per topic, the decisions waiting for
the user with their options and the one recommended, cost, supply, fab and orders in a line each, and which pictures
the viewer shows. Every fact names its source in the project. A new round is a new file. An earlier round only gains
[[note]] entries: a small change is a dated note under its item, and the item shows the mark and state its latest
note gave it.

Pictures are kept as they were when their round was first built, in docs/overview/img/ (index.toml there says which
round uses which): a board's turntable and layers under the hash of the board file and of the project's own 3-D
models it uses, so a board that did not change costs nothing in the next round. `--render` draws the newest round's
again, whatever is there, and leaves a set an earlier round uses as it is. Where nothing can be rendered
(`--no-render`, no KiCad) the stills of `pcbkit previews` stand in, if they are of this board (their SOURCE.txt) or
don't say. The page links the pictures; `--single` puts them inside the file (all of the newest round's, one a view
for the earlier ones).

The fields are listed in skills/project-overview/round-file.md; docs/CONVENTIONS.md, "The overview page", says what
the page is for. `[overview]` in pcbkit.toml: dir, page, frames, size, layer_width, base (a URL the sources link to).
"""
import argparse
import base64
import collections
import datetime
import hashlib
import html
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.parse

from . import tomlw
from .config import Project, data_path

MARKS = (("done", "Done"), ("work", "In work"), ("todo", "Not started"), ("attention", "Needs attention"),
         ("blocked", "Blocked"), ("decision", "Your decision"))
MARK = dict(MARKS)
# what the records call a board's state and an order's status, as marks (production.py: survey, STATUSES)
BOARD_MARK = {"released": "done", "changed": "work", "mockup": "attention", "checked": "work", "not released": "work",
              "not checked": "todo"}
ORDER_MARK = {"draft": "work", "submitted": "work", "in-production": "work", "shipped": "work", "received": "done",
              "problem": "attention", "cancelled": "todo"}
FACTS = (("cost", "Cost"), ("supply", "Supply"), ("fab", "Fab"), ("orders", "Orders"))
# the viewer's layers outside the copper, front then back: (layer, what it is). board_layers() puts the copper between
FRONT = (("F.Silkscreen", "front silk"), ("F.Mask", "front mask openings"))
BACK = (("B.Mask", "back mask openings"), ("B.Silkscreen", "back silk"), ("Edge.Cuts", "outline"))
# a layer's colour in the viewer's key when its plot doesn't give one (KiCad's default theme), and for sets made
# before the plot was asked
COLOURS = {"F.Silkscreen": "#f2eda1", "F.Mask": "#d864ff", "F.Cu": "#c83434", "In1.Cu": "#7fc880", "In2.Cu": "#cd7d2d",
           "B.Cu": "#4d7fc5", "B.Mask": "#02ffee", "B.Silkscreen": "#e8b3a7", "Edge.Cuts": "#d0d1ce"}
DRAWN = re.compile(r"<(path|circle|rect|polyline|polygon|line|ellipse|text|image|use)\b")   # what a plot draws with
STILLS = (("render-top.png", "Front"), ("render-bottom.png", "Back"))      # pcbkit previews', when nothing renders
LIMITS = dict(summary=320, topic=420, topics=10, note=240, state=30, next=5, options=4, notes=8, changes=7)
RECIPE = 4                         # part of a picture set's name: raise it when the pictures are made differently
ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
RESERVED = ("status", "viewer", "decisions", "topics", "decided", "facts")     # the page's own anchors in a round
ROUND = re.compile(r"^r(\d+)\.toml$")
RECORD_ID = re.compile(r"^[A-Z]{1,3}\d+$")
INLINE = re.compile(r"`([^`]+)`|\*\*([^*]+)\*\*|\[([^\]]+)\]\(([^)\s]+)\)")
MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".svg": "image/svg+xml",
        ".gif": "image/gif"}
ORDER = {"": ("date", "title", "about", "label", "mark", "state", "summary", "changes", "next", "links"),
         "board": ("id", "version", "mark", "state", "text", "source"),
         "topic": ("id", "title", "mark", "state", "text", "picture", "source"),
         "decision": ("id", "ref", "question", "mark", "state", "text", "blocks", "recommend", "why", "source"),
         "decision.option": ("id", "text", "effect", "cost"),
         "decided": ("ref", "text", "source"),
         "fact": ("id", "mark", "state", "value", "text", "date", "source"),
         "view": ("board", "show", "name"), "view.picture": ("file", "label"),
         "note": ("date", "on", "mark", "state", "chose", "text")}
TABLES = ("board", "topic", "decision", "decided", "fact", "view", "note")
esc = html.escape


# ---- the round files -------------------------------------------------------------------------------------------------
def paths(project, dir_=None, out=None):
    cfg = project.cfg["overview"]
    d = os.path.abspath(dir_) if dir_ else os.path.join(project.root, cfg["dir"])
    return dict(root=project.root, dir=d, img=os.path.join(d, "img"), index=os.path.join(d, "img", "index.toml"),
                page=os.path.abspath(out) if out else os.path.join(project.root, cfg["page"]))


def load_rounds(dir_):
    """Every round, oldest first: the file's tables, with _n, _key ("r3") and _path."""
    found = sorted((int(m.group(1)), f) for f in (os.listdir(dir_) if os.path.isdir(dir_) else [])
                   for m in [ROUND.match(f)] if m)
    rounds = []
    for n, f in found:
        try:
            r = tomlw.load(os.path.join(dir_, f))
        except ValueError as e:                                    # a TOML error names the line
            raise SystemExit(f"{os.path.join(dir_, f)}: {e}") from None
        r.update(_n=n, _key="r%d" % n, _path=os.path.join(dir_, f))
        rounds.append(r)
    return rounds


def items(rnd):
    """What a note can be about: {id: (kind, item)}; "status" is the round itself."""
    out = {"status": ("status", rnd)}
    for kind in ("board", "topic", "decision", "fact"):
        for it in rnd.get(kind, []):
            if isinstance(it, dict) and isinstance(it.get("id"), str):
                out.setdefault(it["id"], (kind, it))
    return out


def notes_on(rnd, id_):
    return sorted((n for n in rnd.get("note", []) if n.get("on", "status") == id_), key=lambda n: str(n.get("date", "")))


def standing(rnd, id_):
    """An item as it stands after its notes, in the order they were made: (mark, chosen option id or "", state,
    whether a decision is taken). The mark is its own, then that of its latest note that has one; the state word
    likewise, and a note that changes the mark without giving a state leaves the mark's own name, since the old
    word no longer describes it. A decision needs no mark of its own: it is the user's (decision) unless it says
    what it waits for (blocked, in work). It is taken once a note chooses an option or marks it done, and stays
    taken whatever a later note marks it (blocked: the part chosen is out of stock), which is about carrying it
    out. Only a note that marks it decision again gives it back to the user: open, with no choice."""
    kind, it = items(rnd)[id_]
    mark, chose, state = it.get("mark", "decision" if kind == "decision" else "todo"), "", squeeze(it.get("state"))
    taken = False
    for n in notes_on(rnd, id_):
        was = mark
        if n.get("chose"):
            chose, mark, taken = n["chose"], "done", True
        if n.get("mark"):
            mark = n["mark"]
            if mark == "done":
                taken = True
            elif mark == "decision":
                chose, taken = "", False
        if n.get("state"):
            state = squeeze(n["state"])
        elif mark != was:
            state = ""
    return mark, chose, state, taken


def effective(rnd, id_):
    """An item's mark, chosen option and state as they stand (standing)."""
    return standing(rnd, id_)[:3]


def decided(rnd, id_):
    """Whether a decision is taken as it stands (standing)."""
    return standing(rnd, id_)[3]


def open_decisions(rnd):
    """The decisions of a round that are not taken yet. One that a note marked blocked or in work (waiting for a
    measurement) without choosing is as open as before, and is carried into the next round."""
    its = items(rnd)
    return [d for d in rnd.get("decision", []) if d.get("id") in its and not decided(rnd, d["id"])]


def squeeze(v):
    return " ".join(str(v or "").split())


def safe(name):
    """A name as part of a file or folder name in the picture store."""
    return re.sub(r"[^A-Za-z0-9._-]", "-", name)


def anchor(id_):
    """An item's id as the page links it (overview.html#r3-<anchor>). Topics, decisions and facts have ids that are
    anchors already; a board's id is its PCB's file name as the project has it (controller_v2, Main, "my board")."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", str(id_)).strip("-")


def tags(rnd, prev):
    """Against the round before: ({topic id: "new" | "changed"}, [titles of the topics it no longer lists])."""
    if prev is None or not prev.get("topic"):        # nothing to compare with: every topic would read "new"
        return {}, []
    was = {t.get("id"): t for t in prev.get("topic", [])}
    now = {t.get("id") for t in rnd.get("topic", [])}
    out = {}
    for t in rnd.get("topic", []):
        old = was.get(t.get("id"))
        if old is None:
            out[t["id"]] = "new"
        elif any(squeeze(t.get(k)) != squeeze(old.get(k)) for k in ("title", "state", "text", "mark")):
            out[t["id"]] = "changed"
    return out, [t.get("title", t.get("id")) for t in prev.get("topic", []) if t.get("id") not in now]


def fingerprint(rnd):
    """A round without its notes, as a hash: what must not change once the next round has begun."""
    body = {k: v for k, v in rnd.items() if not k.startswith("_") and k != "note"}
    return hashlib.sha1(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def note_prints(rnd):
    """A hash of each of a round's notes, in the file's order. The index keeps them for an earlier round, so its
    notes can be added to and not changed, removed or put in another order: what a note said, it said."""
    return [hashlib.sha1(json.dumps(n, sort_keys=True, default=str).encode()).hexdigest()[:16] for n in rnd.get("note", [])]


def seal(index, rnd):
    """Seal an earlier round as it stands: its file without the notes, once, and the notes it has now."""
    e = entry(index, rnd["_key"])
    e.setdefault("seal", fingerprint(rnd))
    e["notes"] = note_prints(rnd)


def source_parts(source, root=None):
    """"DESIGN.md#5a O9" -> ("DESIGN.md", "5a", "O9"): the file, a place in it, what to look for there. A path may
    have spaces in it ("boards/LED board/LED board.kicad_pcb", as pcbkit overview new writes a board's): with the
    project's ROOT the file is the longest beginning of the text that is in the project, and only without one, or
    when none is, the text up to its first space."""
    text = str(source).strip()
    if root and not re.match(r"^https?://", text):
        for i in reversed([i for i, c in enumerate(text) if c == " "] + [len(text)]):
            path, _, frag = text[:i].partition("#")
            if path and " " not in frag and os.path.exists(os.path.join(root, path)):     # a place in a file is one word
                return path, frag, text[i:].strip()
    first, _, label = text.partition(" ")
    path, _, frag = first.partition("#")
    return path, frag, label.strip()


def sources(v):
    """An item's source or sources (a text, or a list of them) as a list."""
    return [x for x in ([v] if isinstance(v, str) else v or []) if squeeze(x)]


def pictures_of(rnd):
    """Every picture a round names by path (topics' and the views' own)."""
    out = [t["picture"] for t in rnd.get("topic", []) if t.get("picture")]
    for v in rnd.get("view", []):
        out += [p["file"] for p in v.get("picture", []) if p.get("file")]
    return out


def load_index(P):
    idx = tomlw.load(P["index"]) if os.path.exists(P["index"]) else {}
    idx.setdefault("round", {})
    return idx


def entry(index, key):
    e = index["round"].setdefault(key, {})
    e.setdefault("sets", {})
    e.setdefault("pictures", {})
    return e


def check_rounds(rounds, P, project, index):
    """(problems, watch): what stops the page being built, and what only wants a look."""
    problems, watch = [], []
    if [r["_n"] for r in rounds] != list(range(1, len(rounds) + 1)):
        problems.append(f"{P['dir']}: the rounds are {', '.join(r['_key'] for r in rounds)}; they count from r1 with no gaps")
    boards = {p["key"] for p in project.pcbs()}
    for rnd in rounds:
        where, newest = os.path.basename(rnd["_path"]), rnd is rounds[-1]
        bad = lambda text, where=where: problems.append(f"{where}: {text}")      # noqa: E731
        late = lambda text, where=where: watch.append(f"{where}: {text}")        # noqa: E731
        if not isinstance(rnd.get("date"), datetime.date):
            bad(f"date {rnd.get('date')!r} is not a date (date = 2026-11-17, no quotes)")
        for k in ("title", "label", "state", "summary"):
            if not squeeze(rnd.get(k)):
                bad(f"{k} is missing")
        marked = [("the round", rnd)] + [(f"{k} {it.get('id')!r}", it) for k in ("board", "topic", "fact")
                                           for it in rnd.get(k, [])]
        marked += [(f"decision {d.get('id')!r}", d) for d in rnd.get("decision", []) if "mark" in d]
        for what, it in marked + [(f"a note on {n.get('on', 'status')!r}", n) for n in rnd.get("note", []) if "mark" in n]:
            if it.get("mark") not in MARK:
                bad(f"{what}: mark {it.get('mark')!r} is not one of {', '.join(MARK)}")
        seen, links = set(), set(RESERVED)
        for kind in ("board", "topic", "decision", "fact"):
            for it in rnd.get(kind, []):
                i = it.get("id")
                if kind == "board" and (not isinstance(i, str) or i != i.strip() or not anchor(i)):
                    bad(f"board id {i!r}: the PCB's file name without .kicad_pcb")
                elif kind != "board" and (not isinstance(i, str) or not ID.match(i)):
                    bad(f"{kind} id {i!r}: lower-case letters, digits and hyphens")
                elif i in seen:
                    bad(f"id {i!r} is used twice (a note names its item by id)")
                elif anchor(i) in links:
                    bad(f"id {i!r} is linked as #{rnd['_key']}-{anchor(i)}, which the page already uses: another id")
                else:
                    links.add(anchor(i))
                seen.add(i)
        for s in rnd.get("fact", []):
            if s.get("id") not in dict(FACTS):
                bad(f"fact {s.get('id')!r} is not one of {', '.join(k for k, _ in FACTS)}")
            if not squeeze(s.get("value")) or not isinstance(s.get("date"), datetime.date):
                bad(f"fact {s.get('id')!r} needs a value and the date it was taken (date = 2026-11-17)")
        for t in rnd.get("topic", []):
            if not squeeze(t.get("title")) or not squeeze(t.get("text")):
                bad(f"topic {t.get('id')!r} needs a title and a text")
        for d in rnd.get("decision", []):
            opts = [o.get("id") for o in d.get("option", [])]
            if not squeeze(d.get("question")):
                bad(f"decision {d.get('id')!r} has no question")
            if len(opts) < 2 or len(set(opts)) != len(opts) or not all(isinstance(o, str) and o for o in opts):
                bad(f"decision {d.get('id')!r} needs two or more options, each with its own id")
            if d.get("mark") == "done":
                bad(f"decision {d.get('id')!r} is marked done: one that was taken goes under [[decided]], by its number")
            if d.get("recommend") not in opts or not squeeze(d.get("why")):
                bad(f"decision {d.get('id')!r} needs recommend (one of its options' ids) and why")
            if len(opts) > LIMITS["options"]:
                late(f"decision {d.get('id')!r} has {len(opts)} options; {LIMITS['options']} can be compared at a glance")
        its = items(rnd)
        for n in rnd.get("note", []):
            on = n.get("on", "status")
            if not isinstance(n.get("date"), datetime.date) or not squeeze(n.get("text")):
                bad(f"a note on {on!r} needs a date and a text")
            if on not in its:
                bad(f"a note is on {on!r}, which is no id in this round ({', '.join(sorted(its))})")
            elif n.get("chose") and (its[on][0] != "decision"
                                     or n["chose"] not in [o.get("id") for o in its[on][1].get("option", [])]):
                bad(f"a note on {on!r} chose {n['chose']!r}, which is not one of its options")
            if len(squeeze(n.get("state"))) > LIMITS["state"]:
                late(f"a note on {on!r}: state {squeeze(n.get('state'))!r} is long; a state is a few words beside its mark")
            if len(squeeze(n.get("text"))) > LIMITS["note"]:
                late(f"a note on {on!r} is {len(squeeze(n.get('text')))} characters; a note is a line or two ({LIMITS['note']})")
        named = [src for src in sources(rnd.get("links"))]
        for kind in ("board", "topic", "decision", "decided", "fact"):
            for it in rnd.get(kind, []):
                if not sources(it.get("source")) and kind != "board":
                    late(f"{kind} {it.get('id', it.get('ref'))!r} names no source")
                named += sources(it.get("source"))
        for src in named:
            path, _, label = source_parts(src, P["root"])
            full = os.path.join(P["root"], path)
            if re.match(r"^https?://", path):
                continue
            if not os.path.exists(full):
                bad(f"source {src!r}: no {path} in the project")
            elif RECORD_ID.match(label) and os.path.isfile(full):
                with open(full, encoding="utf-8", errors="replace") as fh:
                    if not re.search(r"\b%s\b" % re.escape(label), fh.read()):
                        bad(f"source {src!r}: {path} has no {label}")
        stored = entry(index, rnd["_key"])["pictures"] if rnd["_key"] in index["round"] else {}
        for pic in pictures_of(rnd):
            if pic not in stored and not os.path.exists(os.path.join(P["root"], pic)):
                bad(f"picture {pic}: not in the project")
        for v in rnd.get("view", []):
            if v.get("board"):
                if v["board"] not in boards and v["board"] not in index["round"].get(rnd["_key"], {}).get("sets", {}):
                    bad(f"view: no board {v['board']!r} in the project ({', '.join(sorted(boards))})")
            elif not v.get("name") or not v.get("picture"):
                bad("a view is a board (board = \"name\") or has a name and [[view.picture]] entries")
        sealed = index["round"].get(rnd["_key"], {})
        if sealed.get("seal") and sealed["seal"] != fingerprint(rnd):
            bad("changed after the next round began; an earlier round only gains notes (pcbkit overview note --round "
                f"{rnd['_n']}). If the change is meant, delete its seal line in {os.path.relpath(P['index'], P['root'])}")
        kept, now = sealed.get("notes"), note_prints(rnd)
        if sealed.get("seal") and kept is not None and now[:len(kept)] != kept:
            first = next((i for i, k in enumerate(kept) if i >= len(now) or now[i] != k), 0) + 1
            bad(f"note {first} of the {len(kept)} it had when it was last built was changed, removed or moved; an earlier "
                "round's notes are added to, never edited: say the correction in a new note. If the change is meant, "
                f"delete its notes line in {os.path.relpath(P['index'], P['root'])}")
        # brevity: the page is read in a minute or it is not read
        for k, text, limit in [("summary", rnd.get("summary"), LIMITS["summary"])] + [
                (f"topic {t.get('id')!r}", t.get("text"), LIMITS["topic"]) for t in rnd.get("topic", [])]:
            if len(squeeze(text)) > limit:
                late(f"{k} is {len(squeeze(text))} characters; keep it under {limit} and link the rest")
        for what, it in marked:
            if len(squeeze(it.get("state"))) > LIMITS["state"]:
                late(f"{what}: state {squeeze(it.get('state'))!r} is long; a state is a few words beside its mark")
        if len(rnd.get("topic", [])) > LIMITS["topics"]:
            late(f"{len(rnd['topic'])} topics; more than {LIMITS['topics']} and nobody reads them all")
        if len(rnd.get("changes", [])) > LIMITS["changes"]:
            late(f"{len(rnd['changes'])} changes listed; say the {LIMITS['changes']} that matter and link the changelog")
        if len(rnd.get("next", [])) > LIMITS["next"]:
            late(f"{len(rnd['next'])} next steps; name the first {LIMITS['next']}")
        if len(rnd.get("note", [])) > LIMITS["notes"]:
            late(f"{len(rnd['note'])} notes; that much change is a new round (pcbkit overview new)")
        if newest:
            open_ = [d["id"] for d in open_decisions(rnd)]
            if open_ and effective(rnd, "status")[0] == "done":
                late(f"the round is marked done with {len(open_)} decision(s) open ({', '.join(open_)})")
            for b in [v["board"] for v in rnd.get("view", []) if v.get("board")]:
                for line in set_of(P, index, rnd, b)[1].get("failed", []):
                    late(f"the viewer of {b} is without {line}; once that is put right, pcbkit overview --render")
            known = records(project)
            watch.extend(known["problems"])
            rec = {b["id"]: b for b in known["board"]}
            for b in rnd.get("board", []):
                r = rec.get(b.get("id"))
                if r and r.get("version") and b.get("version") and r["version"] != b["version"]:
                    late(f"board {b['id']!r} is {b['version']} here and {r['version']} in {r.get('source', 'the records')}")
    return problems, watch


# ---- what the project records ---------------------------------------------------------------------------------------
def records(project):
    """Boards and orders as the project's own records have them, for a new round and for --check:
    {"board": [{id, version, state, mark, source}], "orders": [{value, mark, date, source}], "problems": [...]}.
    A board with no release and no entry in boards.toml gets no state: the records don't know it, the writer does.
    Records that are there and can't be read (a boards.toml or order.toml that doesn't parse) are a problem to
    say: a board then has the version of its last release and no state, since whether it changed is not known."""
    from .release import versions as V
    boards, orders, pcbs, problems = [], [], {}, []
    try:
        from . import production
        pcbs = production.survey(project, [], [])
    except (Exception, SystemExit) as err:      # noqa: BLE001 - whatever stops the survey, the states are unknown
        problems.append(f"the project's release and production records could not be read ({squeeze(err)[:200]}): "
                        "no board's state is known from them")
    for pcb in project.pcbs():
        if pcb["excluded"]:
            continue
        p = pcbs.get(pcb["key"], {})
        rel = p.get("last") or V.current(project.release_dir(pcb))
        b = dict(id=pcb["key"])
        if rel:
            b.update(version=rel["label"], source=project.rel(os.path.join(project.release_dir(pcb), "releases.toml")))
            state = p.get("state")                     # released, or changed since: only the survey can tell
            if state:
                b.update(state=state if state != "changed" else f"changed since {rel['label']}",
                         mark=BOARD_MARK.get(state, "work"))
        else:
            with open(os.path.join(project.root, pcb["path"]), encoding="utf-8", errors="replace") as fh:
                rev = V.title_rev(fh.read())
            if rev:
                b.update(version=rev, source=pcb["path"])
            if p.get("checked"):
                b.update(state=p["state"], mark=BOARD_MARK.get(p["state"], "work"),
                         source=project.rel(os.path.join(project.root, project.production["dir"], "boards.toml")))
        boards.append(b)
    try:
        from . import production
        said = []
        loaded = production.load_orders(project, pcbs, said)
        orders = [dict(order_summary(o), source=o["path"]) for o in loaded]
        # an order.toml that doesn't parse is skipped there with a line in its problems; what it says of the orders
        # that did load is production's to report (pcbkit production --check), not a gap in this page
        problems += [f"an order could not be read and is left out ({squeeze(line)[:240]})" for line in said
                     if not any(line.startswith(o["path"]) for o in loaded)]
    except (Exception, SystemExit) as err:      # noqa: BLE001
        problems.append(f"the project's orders could not be read ({squeeze(err)[:200]})")
    return dict(board=boards, orders=orders, problems=problems)


def order_summary(order):
    """An order as production.load_orders read it, in a line: {value, mark, date}. Its boards each have their own
    status (an event can be about one of them), so the order is done only when every board has arrived or was
    cancelled, needs attention while any has a problem, and is in work otherwise; boards that differ are named."""
    boards = order.get("board", [])
    states = [b.get("status", "draft") for b in boards] or ["draft"]
    if len(set(states)) == 1:
        said = states[0]
    else:
        said = ", ".join(f"{b.get('pcb')} {b.get('status', 'draft')}" for b in boards)
    if "problem" in states:
        mark = "attention"
    elif all(s in ("received", "cancelled") for s in states):
        mark = "done" if "received" in states else ORDER_MARK["cancelled"]
    else:
        mark = "work"
    # each board's latest price, in the board's currency as load_orders resolves it: the latest event that names
    # one, whichever came later, so a price corrected without a currency keeps it and a currency corrected without
    # a price applies; an event about several boards counts once; amounts in different currencies are not added
    priced, totals = {}, {}
    for b in boards:
        events = b.get("events", [])
        e = next((e for e in reversed(events) if isinstance(e.get("price"), (int, float))), None)
        if e is not None and id(e) not in priced:
            cur = b.get("currency") or next((x["currency"] for x in reversed(events) if x.get("currency")), "")
            priced[id(e)] = (e["price"], cur)
    for amount, cur in priced.values():
        totals[cur] = totals.get(cur, 0) + amount
    money = " + ".join(f"{amount:.2f} {cur}".rstrip() for cur, amount in totals.items() if amount)
    dates = [e["date"] for e in order.get("events", []) if isinstance(e.get("date"), datetime.date)]
    return dict(value=f"{order['name']}: {said}" + (f", {money}" if money else ""), mark=mark,
                date=max(dates) if dates else None)


# ---- writing a round file -------------------------------------------------------------------------------------------
def _tstr(s, wrap=True):
    e = s.replace("\\", "\\\\").replace('"', '\\"').replace("\t", "\\t").replace("\r", "")
    if not wrap or ("\n" not in e and len(e) <= 108):
        return '"%s"' % e.replace("\n", "\\n")
    paras = []
    for para in e.split("\n"):
        lines, line = [], ""
        for i, word in enumerate(para.split(" ")):
            if i and word and len(line) + len(word) > 108:
                lines.append(line + " \\")           # the space stays; TOML drops the line end and what follows it
                line = word
            else:
                line += (" " if i else "") + word
        paras.append("\n".join(lines + [line]))
    return '"""\n' + "\n".join(paras) + '"""'


def _tval(v):
    if isinstance(v, str):
        return _tstr(v)
    if isinstance(v, (list, tuple)):
        parts = [_tstr(x, wrap=False) if isinstance(x, str) else _tval(x) for x in v]
        one = "[" + ", ".join(parts) + "]"
        return one if len(one) <= 100 else "[\n" + "".join("  %s,\n" % x for x in parts) + "]"
    return tomlw._value(v)


def _table(out, name, d):
    order = ORDER.get(name, ())
    keys = [k for k in order if k in d] + [k for k in d if k not in order and not k.startswith("_")]
    for k in keys:
        v = d[k]
        if not (isinstance(v, list) and v and all(isinstance(x, dict) for x in v)) and v is not None:
            out.append(f"{k} = {_tval(v)}")
    for k in keys:
        v = d[k]
        if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
            for x in v:
                sub = f"{name}.{k}" if name else k
                out += ["", f"[[{sub}]]"]
                _table(out, sub, x)


def write_round(rnd, header=""):
    """A round as TOML text, in the order the page shows it; long texts on several lines."""
    out = [f"# {line}".rstrip() for line in header.splitlines()]
    top = {k: v for k, v in rnd.items() if k not in TABLES}
    _table(out, "", top)
    for k in TABLES:
        for x in rnd.get(k, []):
            out += ["", f"[[{k}]]"]
            _table(out, k, x)
    extra = {k: v for k, v in rnd.items() if k not in TABLES and isinstance(v, list) and v and isinstance(v[0], dict)}
    assert not extra, f"tables the page doesn't know: {', '.join(extra)}"
    return "\n".join(out) + "\n"


HEADER = """{rel}: round {n} of the project's overview page (pcbkit overview; the project-overview skill).
The briefing for the user, made from what the project records; every fact names its source there.
Marks: {marks}.
Once the user has seen this round it changes only by notes: pcbkit overview note "..." --on <id>."""
SKELETON = """
# A few lines per block or topic (power, the MCU, the enclosure, firmware):
# [[topic]]
# id = "power"
# title = "Power"
# mark = "done"
# state = "simulated"
# text = "Two to four lines: what it is, where it stands, what is left."
# source = "DESIGN.md"
#
# What waits for the user, with the options side by side and the one recommended:
# [[decision]]
# id = "antenna"
# ref = "O9"
# question = "..."
# blocks = "the order"
# recommend = "a"
# why = "..."
# source = "DESIGN.md O9"
# [[decision.option]]
# id = "a"
# text = "..."
# effect = "..."
# cost = "..."
#
# What was decided in this round, by its number in DESIGN.md:
# [[decided]]
# ref = "D12"
# text = "..."
# source = "DESIGN.md D12"
#
# Cost, supply, fab and orders, a line each, with the date the number was taken:
# [[fact]]
# id = "cost"
# mark = "done"
# value = "$20 a set at 5"
# text = "..."
# date = {date}
# source = "BOM.md"
#
# The project's own pictures, as one more tab of the viewer:
# [[view]]
# name = "Product"
# [[view.picture]]
# file = "docs/previews/hero.png"
# label = "Assembled"
"""


def project_title(root):
    try:
        with open(os.path.join(root, "README.md"), encoding="utf-8") as fh:
            m = re.search(r"^# +(.+)$", fh.read(), re.M)
    except OSError:
        m = None
    return m.group(1).strip() if m else os.path.basename(root)


def new_round(project, P, today=None, say=print):
    """Write the next round's file and return its path. Round 1 is a skeleton with the boards the project has; a
    later one is the round before as it stands now (the marks and states its notes left, the decisions still open),
    without the notes and the decided list. A board the records have at another version gets theirs, and its state."""
    today = today or datetime.date.today()
    rounds = load_rounds(P["dir"])
    rec = records(project)
    for line in rec["problems"]:
        say("watch: " + line)
    n = len(rounds) + 1
    path = os.path.join(P["dir"], "r%d.toml" % n)
    if os.path.exists(path):
        raise SystemExit(f"{path} exists")
    head = HEADER.format(rel=shown(path, P["root"]), n=n, marks=", ".join(MARK))
    if not rounds:
        rnd = dict(date=today, title=project_title(P["root"]), about="", label="", mark="work", state="", summary="",
                   next=[], board=[dict(dict(mark="todo", state=""), **b) for b in rec["board"]],
                   view=[dict(board=b["id"]) for b in rec["board"]])
        if rec["orders"]:
            rnd["fact"] = [dict(o, id="orders", text="") for o in rec["orders"][-1:]]
        text = write_round(rnd, head) + SKELETON.format(date=today.isoformat())
    else:
        last = rounds[-1]
        rnd = {k: v for k, v in last.items() if not k.startswith("_") and k not in ("note", "decided")}
        rnd.update(date=today, label="", mark=effective(last, "status")[0], state=effective(last, "status")[2])
        for kind in ("board", "topic", "fact"):
            now = {it["id"]: effective(last, it["id"]) for it in last.get(kind, [])}
            rnd[kind] = [dict(it, mark=now[it["id"]][0], state=now[it["id"]][2]) for it in last.get(kind, [])]
        rnd["decision"] = []
        for d in open_decisions(last):                    # as it stands: still the user's, or waiting on something
            mark, _, state = effective(last, d["id"])
            d = {k: v for k, v in d.items() if k not in ("mark", "state")}
            if mark != "decision":
                d.update(mark=mark, **({"state": state} if state else {}))
            rnd["decision"].append(d)
        known = {b["id"]: b for b in rnd["board"]}
        for b in rec["board"]:
            if b["id"] not in known:
                rnd["board"].append(dict(dict(mark="todo", state=""), **b))
            elif b.get("state") and b.get("version") != known[b["id"]].get("version"):
                known[b["id"]].update(b)               # another version than the page had: the records' state for it
        rnd = {k: v for k, v in rnd.items() if v != []}
        text = write_round(rnd, head)
        index = load_index(P)
        seal(index, last)
        save_index(P, index)
    os.makedirs(P["dir"], exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def add_note(P, text, on="status", mark=None, chose=None, round_n=None, today=None, state=None):
    """Append a dated note to a round's file (the newest when none is named). The file is put back as it was when
    the note doesn't fit its round. Returns the file's path."""
    rounds = load_rounds(P["dir"])
    rnd = next((r for r in rounds if r["_n"] == round_n), None) if round_n else (rounds[-1] if rounds else None)
    if rnd is None:
        raise SystemExit(f"no round {round_n or ''} in {P['dir']}".replace("  ", " "))
    its = items(rnd)
    if on not in its:
        raise SystemExit(f"--on {on}: no such id in {rnd['_key']} ({', '.join(sorted(its))})")
    if mark and mark not in MARK:
        raise SystemExit(f"--mark {mark}: one of {', '.join(MARK)}")
    if chose and (its[on][0] != "decision" or chose not in [o.get("id") for o in its[on][1].get("option", [])]):
        raise SystemExit(f"--chose {chose}: {on} has no such option")
    note = dict(date=today or datetime.date.today(), on=on)
    if mark:
        note["mark"] = mark
    if state:
        note["state"] = state
    if chose:
        note["chose"] = chose
    note["text"] = text
    with open(rnd["_path"], encoding="utf-8") as fh:
        before = fh.read()
    out = ["", "[[note]]"]
    _table(out, "note", note)
    with open(rnd["_path"], "w", encoding="utf-8") as fh:
        fh.write(before.rstrip("\n") + "\n" + "\n".join(out) + "\n")
    try:
        again = tomlw.load(rnd["_path"])
        assert again.get("note", [])[-1]["text"] == text
    except Exception as e:      # noqa: BLE001 - whatever went wrong, the round file is the user's record
        with open(rnd["_path"], "w", encoding="utf-8") as fh:
            fh.write(before)
        raise SystemExit(f"{rnd['_path']}: the note did not fit the file ({e}); nothing was changed") from None
    return rnd["_path"]


# ---- pictures --------------------------------------------------------------------------------------------------------
def save_index(P, index):
    os.makedirs(P["img"], exist_ok=True)
    tomlw.dump(index, P["index"], "Written by pcbkit overview: which pictures each round uses, as they were when the round\n"
                                  "was first built, and a seal on each earlier round (its file without the notes, and\n"
                                  "the notes it had at the last build, which later ones are added to).")


def sha(path, extra=""):
    h = hashlib.sha1(extra.encode())
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:8]


def models_print(pcb):
    """A hash of the 3-D model files a board uses that are not KiCad's own, "" when it uses none: the project's
    (${KIPRJMOD}/... or $(KIPRJMOD)/..., a bare relative path) and any other file it names, as the release freeze
    resolves them. A render shows them and the board file only names them, so a model put right changes the picture
    and not the board. KiCad's own library models are not followed: they change with KiCad, and --render is for that."""
    from .release import freeze as F
    h, found = hashlib.sha1(), False
    for name, path in F.model_files(pcb):
        found = True
        h.update(name.encode())
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    return h.hexdigest() if found else ""


def board_layers(text):
    """The layers of a board for the viewer, from its file, front to back: [(layer, what it is)]. Both silkscreens
    and masks, the outline, and every copper layer the board has, however many are inside."""
    inner = sorted({int(n) for n in re.findall(r'\(\d+ "In(\d+)\.Cu"', text)})
    copper = [("F.Cu", "front copper")] + [("In%d.Cu" % n, "inner %d" % n) for n in inner] + [("B.Cu", "back copper")]
    return list(FRONT) + copper + list(BACK)


def plot_facts(svg):
    """What kicad-cli's SVG of one layer says about it: (whether anything is drawn, the colour it is drawn in).
    A layer with nothing on it is a file with no shapes, whatever its picture weighs; one small pad is a shape."""
    seen = collections.Counter(c.lower() for c in re.findall(r"#[0-9A-Fa-f]{6}", svg))
    black, white = seen.pop("#000000", 0), seen.pop("#ffffff", 0)    # black opens every plot; white is its drill holes
    colour = seen.most_common(1)[0][0] if seen else "#000000" if black > 2 else "#ffffff" if white else None
    return bool(DRAWN.search(svg)), colour


def render_set(pcb, dest, cfg):
    """A board's pictures into DEST: the turntable of both sides, and each layer on its own on a clear background,
    all in one frame so they stack; set.json says what is there. A layer is left out only when its plot draws
    nothing. What could not be drawn (a side of the turntable, layers whose plot or picture failed) is listed under
    "failed" with the reason, for the build and --check to say: the viewer is without it and looks complete."""
    from .release import previews as V
    w, h = (int(v) for v in str(cfg["size"]).lower().split("x"))
    with open(pcb, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    man, failed, why = dict(made="pcbkit overview (kicad-cli pcb render, kicad-cli pcb export svg)"), [], {}
    turn = V.turntable(pcb, dest, int(cfg["frames"]), (w, h)) if "(footprint " in text else {}
    if turn:
        man["turn"] = turn
    if "(footprint " in text and len(turn) < 2:
        which = "the turntable" if not turn else "the %s of the turntable" % ("back" if "top" in turn else "front")
        failed.append(f"{which} (kicad-cli pcb render failed)")
    layers, tmp = [], tempfile.mkdtemp()
    try:
        for lid, what in board_layers(text):
            stem = "layer-%s" % lid.replace(".", "_")
            png = os.path.join(dest, stem + ".png")
            try:
                mm = V.layer_png(pcb, lid, png, int(cfg["layer_width"]), background=None, tmp=tmp)
                with open(os.path.join(tmp, stem + ".svg"), encoding="utf-8", errors="replace") as fh:
                    drawn, colour = plot_facts(fh.read())
            except Exception as err:      # noqa: BLE001 - no rsvg-convert, or a layer kicad-cli won't plot
                tool = os.path.basename(str(getattr(err, "filename", None) or (getattr(err, "cmd", None) or [""])[0]))
                reason = (f"{tool} is not installed" if isinstance(err, FileNotFoundError) and tool else
                          f"{tool} failed" if tool else squeeze(err)[:120] or type(err).__name__)
                why.setdefault(reason, []).append(lid)
                continue
            man["mm"] = [round(v, 1) for v in mm] if mm else man.get("mm")
            if not drawn:
                os.remove(png)
                continue
            made = V.webp(png) or png
            layers.append(dict(id=lid, name=what, colour=colour or COLOURS.get(lid, "#9aa39e"), file=os.path.basename(made)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if layers:
        man["layers"] = layers
    failed += ["layer%s %s (%s)" % ("s" if len(ids) > 1 else "", ", ".join(ids), reason) for reason, ids in why.items()]
    if failed:
        man["failed"] = failed
    return man


def publish(tmp, dest, man):
    """A finished set: its set.json written, then moved into place in one step."""
    with open(os.path.join(tmp, "set.json"), "w", encoding="utf-8") as fh:
        json.dump(man, fh, indent=1, sort_keys=True)
    shutil.rmtree(dest, ignore_errors=True)
    os.rename(tmp, dest)


def stills_set(P, project, info, pcb):
    """What `pcbkit previews` left of a board, as a set of stills, for when nothing can be rendered:
    (set name, problem, watch). The set is named by what the stills hold, so other stills later are another set and
    a round keeps the ones it was given. Previews that say which board they are of (SOURCE.txt, written by pcbkit
    release) must be of this one; previews that don't say are used, and the page says they were not checked."""
    from .release import versions as V
    b, pdir = info["key"], project.previews_dir(info)
    found = [(f, label) for f, label in STILLS if os.path.isfile(os.path.join(pdir, f))]
    if not found:
        return None, (f"no pictures of {b}: it was not rendered, and {project.rel(pdir)} has no render-top.png "
                      "(pcbkit previews)"), None
    checked, watch = False, None
    if os.path.isfile(os.path.join(pdir, "SOURCE.txt")):
        try:
            with open(os.path.join(pdir, "SOURCE.txt"), encoding="utf-8") as fh:
                checked = fh.read() == V.preview_source(pcb)
        except (Exception, SystemExit):      # noqa: BLE001 - a board whose sheets can't be read can't be compared
            checked = None
        if checked is False:
            return None, (f"no pictures of {b}: it was not rendered, and the previews in {project.rel(pdir)} are of "
                          "another version of the board (their SOURCE.txt): pcbkit previews, or let pcbkit overview render"), None
    if not checked:
        watch = (f"the pictures of {b} are the stills in {project.rel(pdir)}, which don't say which version of the "
                 "board they show (no SOURCE.txt): look that they are of this one")
    h = hashlib.sha1(b"checked" if checked else b"")
    for f, label in found:
        with open(os.path.join(pdir, f), "rb") as fh:
            h.update(label.encode() + fh.read())
    name = "%s-%s-stills" % (safe(b), h.hexdigest()[:8])
    dest = os.path.join(P["img"], name)
    if not os.path.exists(os.path.join(dest, "set.json")):
        tmp = dest + ".new-%d" % os.getpid()
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp)
        for f, _ in found:
            shutil.copyfile(os.path.join(pdir, f), os.path.join(tmp, f))
        publish(tmp, dest, dict(made="pcbkit previews", checked=bool(checked),
                                stills=[dict(label=label, file=f) for f, label in found]))
    return name, None, watch


def latest_set(P, index, board, base, but=None):
    """The newest set of this very board file that a round is recorded with: BASE, or one drawn again beside it
    (base-2, ...) by --render. A later round takes that one, so pictures put right are the ones that go on, and
    the rounds before keep what they had. None when there is none."""
    fits = re.compile(re.escape(base) + r"(-\d+)?$")
    for key in sorted(index["round"], key=lambda k: int(k[1:]) if k[1:].isdigit() else 0, reverse=True):
        name = index["round"][key].get("sets", {}).get(board)
        if key != but and name and fits.match(name) and os.path.exists(os.path.join(P["img"], name, "set.json")):
            return name
    return base if os.path.exists(os.path.join(P["img"], base, "set.json")) else None


def freeze(P, rounds, project, index, render=True, refresh=False, renderer=render_set):
    """Give every round its pictures, once: named ones are copied into the store under their hash, a board's set is
    rendered under the hash of the board file and of the project's own 3-D models it uses (or found there, when
    neither has changed since another round).
    REFRESH does the newest round's again, whatever is there, as a set with a name of its own beside the one it
    had. Nothing in the store is replaced or removed here, so a build that fails leaves the page on disk with all
    its pictures (prune, once the build is written, removes what no round uses). Returns (problems, watch)."""
    problems, watch, cfg = [], [], project.cfg["overview"]
    pcbs = {p["key"]: p for p in project.pcbs()}
    os.makedirs(P["img"], exist_ok=True)
    for rnd in rounds:
        e = entry(index, rnd["_key"])
        again = refresh and rnd is rounds[-1] and "seal" not in e
        for pic in pictures_of(rnd):
            src = os.path.join(P["root"], pic)
            if pic in e["pictures"] and not again:
                continue
            if not os.path.isfile(src):
                if pic not in e["pictures"]:
                    problems.append(f"{rnd['_key']}: picture {pic}: not in the project")
                continue
            name = "%s-%s" % (sha(src), safe(os.path.basename(pic)))
            if not os.path.exists(os.path.join(P["img"], name)):
                shutil.copyfile(src, os.path.join(P["img"], name))
            e["pictures"][pic] = name
        for v in rnd.get("view", []):
            b = v.get("board")
            if not b or (b in e["sets"] and not again) or b not in pcbs:
                continue
            pcb = os.path.join(P["root"], pcbs[b]["path"])
            recipe = "%s %s %s %s" % (RECIPE, cfg["frames"], cfg["size"], cfg["layer_width"])
            base = "%s-%s" % (safe(b), sha(pcb, (recipe + " " + models_print(pcb)).strip()))
            have = latest_set(P, index, b, base, but=rnd["_key"])
            name = None
            if render and (again or not have):
                tmp = os.path.join(P["img"], base + ".new-%d" % os.getpid())
                shutil.rmtree(tmp, ignore_errors=True)
                os.makedirs(tmp)
                man = {}
                try:
                    man = renderer(pcb, tmp, cfg) or {}
                except (Exception, SystemExit) as err:      # noqa: BLE001 - no KiCad here: the stills will do
                    watch.append(f"{rnd['_key']}: {b} was not rendered ({squeeze(err)[:200]})")
                watch += [f"{rnd['_key']}: the viewer of {b} is without {line}" for line in man.get("failed", [])]
                if man.get("turn") or man.get("layers"):
                    name, n = base, 2
                    while os.path.exists(os.path.join(P["img"], name)):
                        name, n = "%s-%d" % (base, n), n + 1     # beside the set that is there: an earlier round, or
                    publish(tmp, os.path.join(P["img"], name), man)  # the page on disk until this build is written, shows it
                else:
                    shutil.rmtree(tmp, ignore_errors=True)
            if name is None and have:
                name = have                                      # drawn before, from this very board file
            if name is None:
                name, problem, note = stills_set(P, project, pcbs[b], pcb)
                if problem:
                    problems.append(f"{rnd['_key']}: {problem}")
                    continue
                if note:
                    watch.append(f"{rnd['_key']}: {note}")
            e["sets"][b] = name
    return problems, watch


def prune(P, index):
    """Remove the sets and pictures of the store that no round uses any more. Only for after the index and the page
    are written: until then the page on disk may still show what a failed build was about to replace."""
    used = {n for e in index["round"].values() for n in list(e.get("sets", {}).values()) + list(e.get("pictures", {}).values())}
    for name in os.listdir(P["img"]) if os.path.isdir(P["img"]) else []:
        full = os.path.join(P["img"], name)
        ours = re.match(r"^([0-9a-f]{8}-.+|.+-[0-9a-f]{8}(-\d+)?(-stills)?(\.new-\d+)?)$", name)
        if ours and name not in used:
            shutil.rmtree(full) if os.path.isdir(full) else os.remove(full)


def missing(P, rounds, index):
    """Problems: pictures a round was built with that are no longer in the store. A page made without them would
    lose a board from its viewer or show blanks, and say nothing."""
    out, where = [], shown(P["img"], P["root"])
    for rnd in rounds:
        e = index["round"].get(rnd["_key"], {})
        for pic in pictures_of(rnd):
            name = e.get("pictures", {}).get(pic)
            if name and not os.path.isfile(os.path.join(P["img"], name)):
                out.append(f"{rnd['_key']}: {where}/{name}, its copy of {pic}, is missing")
        for b in [v["board"] for v in rnd.get("view", []) if v.get("board")]:
            name = e.get("sets", {}).get(b)
            if not name:
                continue
            _, man = set_of(P, index, rnd, b)
            files = [f for side in man.get("turn", {}).values() for f in side]
            files += [x["file"] for x in man.get("layers", []) + man.get("stills", [])]
            gone = [f for f in files if not os.path.isfile(os.path.join(P["img"], name, f))]
            if not man or gone:
                out.append(f"{rnd['_key']}: {where}/{name}, its pictures of {b}, " + (
                    f"lacks {len(gone)} of its {len(files)} files ({', '.join(gone[:3])}{', ...' if len(gone) > 3 else ''})"
                    if man else "is missing or has no set.json"))
    if out:
        out.append(f"restore {where} as it was committed; the newest round's pictures can be drawn again with "
                   "pcbkit overview --render")
    return out


def set_of(P, index, rnd, board):
    name = index["round"].get(rnd["_key"], {}).get("sets", {}).get(board)
    path = os.path.join(P["img"], name, "set.json") if name else ""
    if not name or not os.path.exists(path):
        return None, {}
    with open(path, encoding="utf-8") as fh:
        return name, json.load(fh)


# ---- the page --------------------------------------------------------------------------------------------------------
class Page:
    """Where the page is and how it reaches the project's files and its pictures."""

    def __init__(self, P, index, single=False, base=""):
        self.P, self.index, self.single, self.base = P, index, single, base
        self.dir = os.path.dirname(P["page"])
        self.bytes = {}                                             # round key -> picture bytes it shows

    def href(self, target):
        """A link to a file of the project, or to the web; None for what the page can't or won't link."""
        if re.match(r"^https?://", target):
            return target
        if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", target) or target.startswith(("/", "#")):
            return None
        path, _, frag = target.partition("#")
        if self.base:
            url = self.base.rstrip("/") + "/" + path
        elif self.single:
            return None
        else:
            url = urllib.parse.quote(os.path.relpath(os.path.join(self.P["root"], path), self.dir).replace(os.sep, "/"))
        return url + ("#" + frag if frag else "")

    def src(self, stored, key):
        """A picture of the store as an img src: a relative link, or the picture itself in a --single page."""
        full = os.path.join(self.P["img"], stored)
        try:
            size = os.path.getsize(full)
        except OSError:
            return ""
        self.bytes[key] = self.bytes.get(key, 0) + size
        if not self.single:
            return urllib.parse.quote(os.path.relpath(full, self.dir).replace(os.sep, "/"))
        with open(full, "rb") as fh:
            return "data:%s;base64,%s" % (MIME.get(os.path.splitext(full)[1].lower(), "application/octet-stream"),
                                          base64.b64encode(fh.read()).decode())

    def inline(self, text):
        out, pos = [], 0
        for m in INLINE.finditer(text):
            out.append(esc(text[pos:m.start()]))
            if m.group(1):
                out.append("<code>%s</code>" % esc(m.group(1)))
            elif m.group(2):
                out.append("<b>%s</b>" % esc(m.group(2)))
            else:
                href = self.href(m.group(4))
                out.append('<a href="%s">%s</a>' % (esc(href), esc(m.group(3))) if href else esc(m.group(3)))
            pos = m.end()
        return "".join(out) + esc(text[pos:])

    def text(self, text, cls=""):
        paras = [p for p in re.split(r"\n\s*\n", str(text or "").strip()) if p.strip()]
        return "".join("<p%s>%s</p>" % (' class="%s"' % cls if cls else "", self.inline(squeeze(p))) for p in paras)

    def source(self, source):
        """Where the project records it: each as the file's name and what to look for there, linked."""
        out = []
        for src in sources(source):
            path, frag, label = source_parts(src, self.P["root"])
            # a document at the project's root by its name and the place in it; a file deeper in by its label
            deep = "/" in path or re.match(r"^https?://", path)
            shown = esc(label if deep and label else path + (" " + label if label else ""))
            href = self.href(path + ("#" + frag if frag else ""))
            out.append('<a href="%s">%s</a>' % (esc(href), shown) if href else shown)
        return '<span class="src">%s</span>' % ", ".join(out) if out else ""


def mark_html(mark, word="", cls=""):
    """Every status on the page: the mark's sign, then the record's own word for the state (the mark's name when
    there is none). A screen reader hears the mark's name first."""
    name = MARK[mark]
    said = "" if squeeze(word).lower() in ("", name.lower()) else '<span class="sr">%s: </span>' % esc(name)
    return ('<span class="mk mk-%s%s"><svg class="g" viewBox="0 0 16 16" aria-hidden="true"><use href="#g-%s"/></svg>'
            '<span>%s%s</span></span>') % (mark, " " + cls if cls else "", mark, said, esc(squeeze(word) or name))


def iso(d):
    return d.isoformat() if isinstance(d, datetime.date) else str(d or "")


def items_html(pg, lines):
    return "".join("<li>%s</li>" % pg.inline(squeeze(x)) for x in lines)


def notes_html(pg, rnd, id_):
    """An item's notes, under it, oldest first. The mark a note left is the one the item shows above."""
    return "".join('<p class="note"><time>%s</time> %s</p>' % (iso(n.get("date")), pg.inline(squeeze(n.get("text"))))
                   for n in notes_on(rnd, id_))


def viewer_html(pg, rnd, newest):
    """The viewer's tabs as data, and the first picture for a reader without scripts."""
    key, full = rnd["_key"], newest or not pg.single        # a --single page keeps one picture a view of earlier rounds
    tabs = []
    for v in rnd.get("view", []):
        modes = []
        if v.get("board"):
            _, man = set_of(pg.P, pg.index, rnd, v["board"])
            name = pg.index["round"].get(key, {}).get("sets", {}).get(v["board"], "")
            show = v.get("show") or ["turn", "layers", "stills"]
            where = lambda f, name=name: pg.src(name + "/" + f, key)     # noqa: E731
            if man.get("turn") and "turn" in show:
                turn = man["turn"]
                if full:
                    modes.append(dict(kind="turn", name="Turn", sides=[dict(name="Front" if s == "top" else "Back",
                                                                             frames=[where(f) for f in turn[s]])
                                                                        for s in ("top", "bottom") if s in turn]))
                else:
                    first = next(iter(turn.values()))
                    modes.append(dict(kind="pics", name="Picture",
                                      pics=[dict(label=v["board"], src=where(first[len(first) // 8]))]))
            if man.get("layers") and "layers" in show and (full or not modes):
                # all of them, or for an earlier round of a single page that has no turntable, the front as it opens
                front = [x for x in man["layers"] if x["id"] in ("F.Cu", "F.Silkscreen", "Edge.Cuts")]
                kept = man["layers"] if full else front if len(front) > 1 else man["layers"][:2]
                what = dict(board_layers(""))
                modes.append(dict(kind="layers", name="Layers", layers=[
                    dict(id=x["id"], name=x.get("name") or what.get(x["id"], x["id"]),
                         colour=x.get("colour") or COLOURS.get(x["id"], "#9aa39e"), src=where(x["file"]))
                    for x in kept]))
            if man.get("stills") and ("stills" in show or not modes):     # also when nothing that was asked for is there
                pics = man["stills"] if full else man["stills"][:1]
                modes.append(dict(kind="pics", name="Pictures",
                                  pics=[dict(label=x["label"], src=where(x["file"])) for x in pics]))
            mm = man.get("mm")
            note = "%g × %g mm" % tuple(mm) if mm else ""
            if man.get("stills") and not man.get("checked"):
                note = "Stills from pcbkit previews, not checked against the board file"
            tab = dict(name=v.get("name") or v["board"], note=note, modes=modes)
        else:
            stored = pg.index["round"].get(key, {}).get("pictures", {})
            pics = [dict(label=p.get("label") or os.path.basename(p["file"]), src=pg.src(stored[p["file"]], key))
                    for p in v.get("picture", []) if p.get("file") in stored]
            tab = dict(name=v.get("name", ""), note="",
                       modes=[dict(kind="pics", name="Pictures", pics=pics if full else pics[:1])])
        tab["modes"] = [m for m in tab["modes"] if m.get("pics") or m.get("sides") or m.get("layers")]
        if tab["modes"]:
            tabs.append(tab)
    if not tabs:
        return ""
    m0 = tabs[0]["modes"][0]
    first = (m0["sides"][0]["frames"][len(m0["sides"][0]["frames"]) // 8] if m0["kind"] == "turn"
             else m0["pics"][0]["src"] if m0["kind"] == "pics" else m0["layers"][0]["src"])
    data = json.dumps(tabs, separators=(",", ":")).replace("</", "<\\/")
    return ('<div class="viewer" id="%s-viewer"><div class="vtabs"></div><div class="vmodes"></div>'
            '<div class="stagebox"><div class="stage" tabindex="0"><div class="pan"><div class="flip">'
            '<img src="%s" alt="%s" draggable="false"></div></div></div></div>'
            '<div class="vctl"></div><div class="vkey"></div><p class="vcap"></p>'
            '<script type="application/json">%s</script></div>') % (key, esc(first), esc(tabs[0]["name"]), data)


def round_html(pg, rnd, prev, rounds):
    key, newest = rnd["_key"], rnd is rounds[-1]
    tag, gone = tags(rnd, prev)
    its = items(rnd)
    out = ['<section class="round" id="%s" data-label="%s">' % (key, esc(squeeze(rnd.get("label"))))]
    if not newest:
        out.append('<p class="earlier">Round %d of %d, as it stood on %s with its later notes. '
                   '<a href="#%s">Show the newest round</a></p>'
                   % (rnd["_n"], len(rounds), iso(rnd.get("date")), rounds[-1]["_key"]))
    # status
    mark, _, state = effective(rnd, "status")
    open_ = open_decisions(rnd)
    out.append('<div class="status" id="%s-status"><div class="st-head"><h2>%s</h2><p class="st-round">Round %d, %s: %s</p></div>'
               % (key, mark_html(mark, state, "big"), rnd["_n"], iso(rnd.get("date")), esc(squeeze(rnd.get("label")))))
    out.append('<div class="st-body"><div class="st-sum">%s' % pg.text(rnd.get("summary")))
    if rnd.get("changes"):
        out.append('<h3>In this round</h3><ul class="changes">%s</ul>' % items_html(pg, rnd["changes"]))
    out.append(pg.source(rnd.get("links")))
    if open_:
        out.append('<p class="st-open"><a href="#%s-decisions">%s</a></p>' % (key, mark_html(
            "decision", "%d decision%s for you" % (len(open_), "" if len(open_) == 1 else "s"))))
    out.append("</div>")
    if rnd.get("next"):
        out.append('<div class="st-next"><h3>Next</h3><ol>%s</ol></div>' % items_html(pg, rnd["next"]))
    out.append("</div></div>")
    notes = sorted(rnd.get("note", []), key=lambda n: str(n.get("date", "")))
    if notes:
        rows = []
        for n in notes:
            on = n.get("on", "status")
            kind, it = its.get(on, ("status", rnd))
            what = "Status" if kind == "status" else squeeze(it.get("title") or it.get("ref") or dict(FACTS).get(on) or on)
            left = mark_html(n["mark"], n.get("state"), "small") + " " if n.get("mark") else ""
            rows.append('<li><time>%s</time> <a href="#%s-%s">%s</a> %s%s</li>' % (
                iso(n.get("date")), key, anchor(on), esc(what), left, pg.inline(squeeze(n.get("text")))))
        out.append('<div class="updates"><h3>Added since %s</h3><ul>%s</ul></div>' % (iso(rnd.get("date")), "".join(rows)))
    # viewer and boards
    viewer = viewer_html(pg, rnd, newest)
    boards = []
    for b in rnd.get("board", []):
        m, _, state = effective(rnd, b["id"])
        boards.append('<tr id="%s-%s"><th scope="row">%s</th><td class="ver">%s</td><td>%s%s%s%s</td></tr>' % (
            key, anchor(b["id"]), esc(b["id"]), esc(squeeze(b.get("version"))), mark_html(m, state),
            pg.text(b.get("text"), "sub"), notes_html(pg, rnd, b["id"]), pg.source(b.get("source"))))
    if viewer or boards:
        out.append('<div class="look%s">%s' % ("" if viewer and boards else " one", viewer))
        if boards:
            out.append('<div class="boards"><h3>Boards</h3><table><thead><tr><th scope="col">Board</th>'
                       '<th scope="col">Version</th><th scope="col">Status</th></tr></thead><tbody>%s</tbody></table></div>'
                       % "".join(boards))
        out.append("</div>")
    # decisions
    if rnd.get("decision"):
        out.append('<div class="block" id="%s-decisions"><h3>Decisions for you</h3>' % key)
        for d in rnd["decision"]:
            m, chose, state = effective(rnd, d["id"])
            head = mark_html(m, "decided" if m == "done" else state or ("open" if m == "decision" else ""))
            ref = '<span class="ref">%s</span> ' % esc(d["ref"]) if d.get("ref") else ""
            out.append('<article class="decision%s" id="%s-%s"><header><h4>%s%s</h4>%s</header>' % (
                " closed" if decided(rnd, d["id"]) else "", key, d["id"], ref, pg.inline(squeeze(d.get("question"))), head))
            if d.get("text"):
                out.append(pg.text(d["text"]))
            if d.get("blocks"):
                out.append('<p class="blocks">Waiting on this: %s</p>' % pg.inline(squeeze(d["blocks"])))
            out.append('<div class="options">')
            for o in d.get("option", []):
                rec, cho = o.get("id") == d.get("recommend"), o.get("id") == chose
                flag = ('<span class="flag chosen">Chosen</span>' if cho
                        else '<span class="flag">Recommended</span>' if rec else "")
                out.append('<div class="option%s%s"><h5>%s%s</h5><dl>%s%s</dl></div>' % (
                    " rec" if rec else "", " cho" if cho else "", flag, pg.inline(squeeze(o.get("text"))),
                    "<dt>Effect</dt><dd>%s</dd>" % pg.inline(squeeze(o["effect"])) if o.get("effect") else "",
                    "<dt>Cost</dt><dd>%s</dd>" % pg.inline(squeeze(o["cost"])) if o.get("cost") else ""))
            out.append("</div>")
            if d.get("why"):
                out.append('<p class="why"><b>Why that one:</b> %s</p>' % pg.inline(squeeze(d["why"])))
            out.append(notes_html(pg, rnd, d["id"]) + pg.source(d.get("source")) + "</article>")
        out.append("</div>")
    # topics
    if rnd.get("topic") or gone:
        out.append('<div class="block" id="%s-topics"><h3>Topics</h3><div class="topics">' % key)
        stored = pg.index["round"].get(key, {}).get("pictures", {})
        for t in rnd.get("topic", []):
            m, _, state = effective(rnd, t["id"])
            flag = '<span class="tag %s">%s</span>' % (tag[t["id"]], tag[t["id"]]) if t["id"] in tag else ""
            pic = ""
            if t.get("picture") in stored and (newest or not pg.single):
                pic = '<img class="tpic" src="%s" alt="%s" loading="lazy">' % (
                    esc(pg.src(stored[t["picture"]], key)), esc(squeeze(t.get("title"))))
            out.append('<article class="topic" id="%s-%s"><header><h4>%s %s</h4>%s</header>'
                       '<div class="tbody">%s%s%s%s</div></article>' % (
                           key, t["id"], esc(squeeze(t.get("title"))), flag, mark_html(m, state), pg.text(t.get("text")), pic,
                           notes_html(pg, rnd, t["id"]), pg.source(t.get("source"))))
        out.append("</div>")
        if gone:
            out.append('<p class="gone">No longer listed since round %d: %s.</p>' % (prev["_n"], esc(", ".join(gone))))
        out.append("</div>")
    if rnd.get("decided"):
        rows = ['<li><span class="ref">%s</span> %s %s</li>' % (
            esc(squeeze(d.get("ref"))), pg.inline(squeeze(d.get("text"))), pg.source(d.get("source"))) for d in rnd["decided"]]
        out.append('<div class="block" id="%s-decided"><h3>Decided in this round</h3><ul class="decided">%s</ul></div>'
                   % (key, "".join(rows)))
    if rnd.get("fact"):
        out.append('<div class="block" id="%s-facts"><h3>Cost, supply and fab</h3><div class="facts">' % key)
        for sid, title in FACTS:
            for s in [s for s in rnd["fact"] if s.get("id") == sid]:
                m, _, state = effective(rnd, sid)
                out.append('<div class="fact" id="%s-%s"><h4>%s</h4><p class="val">%s</p>%s%s%s'
                           '<p class="asof">%s as of %s</p></div>' % (
                               key, sid, title, pg.inline(squeeze(s.get("value"))), mark_html(m, state),
                               pg.text(s.get("text"), "sub"), notes_html(pg, rnd, sid), pg.source(s.get("source")),
                               iso(s.get("date"))))
        out.append("</div></div>")
    out.append("</section>")
    return "".join(out)


def page_html(rounds, P, index, single=False, base=""):
    """(the page, {round key: picture bytes}). Nothing in it depends on when it is made: the same files give the
    same page, so --check can compare."""
    pg = Page(P, index, single, base)
    newest = rounds[-1]
    nav = "".join('<a href="#%s" data-round="%s"%s><b>%d</b><span>%s</span><time>%s</time></a>' % (
        r["_key"], r["_key"], ' aria-current="true"' if r is newest else "", r["_n"], esc(squeeze(r.get("label"))),
        iso(r.get("date"))) for r in rounds)
    legend = "".join("<li>%s</li>" % mark_html(m) for m, _ in MARKS)
    body = "".join(round_html(pg, r, rounds[i - 1] if i else None, rounds) for i, r in reversed(list(enumerate(rounds))))
    with open(data_path("overview", "page.html"), encoding="utf-8") as fh:
        page = fh.read()
    for k, v in (("__TITLE__", esc(squeeze(newest.get("title")))), ("__ABOUT__", pg.inline(squeeze(newest.get("about")))),
                 ("__NAV__", nav), ("__LEGEND__", legend), ("__NEWEST__", newest["_key"]), ("__ROUNDS__", body)):
        page = page.replace(k, v)
    return page, pg.bytes


def shown(path, root):
    """A path as the user reads it: within the project, else whole."""
    rel = os.path.relpath(path, root)
    return path if rel.startswith("..") else rel


def size(n):
    return "%.1f MB" % (n / 1e6) if n >= 1e6 else "%d kB" % round(n / 1e3)


def build(project=None, dir_=None, out=None, single=None, render=True, refresh=False, check=False, say=print,
          renderer=render_set):
    """Build the page; or, with CHECK, say what is wrong with the rounds (problem), what only a build can settle
    (to build: pictures not made yet, a page that is not the rounds') and what wants a look (watch), writing
    nothing. Returns 1 when there are problems, or when a check found something left to build."""
    project = project if isinstance(project, Project) else Project(project)
    P = paths(project, dir_, out)
    rounds = load_rounds(P["dir"])
    if not rounds:
        raise SystemExit(f"no rounds in {P['dir']}: pcbkit overview new")
    index = load_index(P)
    problems, watch = check_rounds(rounds, P, project, index)
    base, todo = project.cfg["overview"]["base"], []
    if check:
        for rnd in rounds:
            e = index["round"].get(rnd["_key"], {})
            if any(p not in e.get("pictures", {}) for p in pictures_of(rnd)) or any(
                    v.get("board") and v["board"] not in e.get("sets", {}) for v in rnd.get("view", [])):
                todo.append(f"{rnd['_key']}: its pictures are not made yet")
        problems += missing(P, rounds, index)
        if not problems and not todo:
            page, _ = page_html(rounds, P, index, base=base)
            if not os.path.exists(P["page"]) or open(P["page"], encoding="utf-8").read() != page:
                todo.append(f"{shown(P['page'], P['root'])} is not what the rounds say")
    else:
        if not problems:
            stopped, seen = freeze(P, rounds, project, index, render, refresh, renderer)
            problems += stopped or missing(P, rounds, index)
            watch += seen
        if not problems:
            for rnd in rounds[:-1]:
                seal(index, rnd)
            save_index(P, index)
            page, used = page_html(rounds, P, index, base=base)
            os.makedirs(os.path.dirname(P["page"]), exist_ok=True)
            with open(P["page"], "w", encoding="utf-8") as fh:
                fh.write(page)
            prune(P, index)
            say(f"{shown(P['page'], P['root'])}: {len(rounds)} round(s), {size(len(page.encode()))}; pictures "
                + ", ".join(f"{r['_key']} {size(used.get(r['_key'], 0))}" for r in rounds))
            if single is not None:
                one = os.path.abspath(single) if single else os.path.splitext(P["page"])[0] + "-single.html"
                page, _ = page_html(rounds, dict(P, page=one), index, single=True, base=base)
                os.makedirs(os.path.dirname(one), exist_ok=True)
                with open(one, "w", encoding="utf-8") as fh:
                    fh.write(page)
                say(f"{one}: {size(len(page.encode()))}, pictures inside")
    for line in watch:
        say("watch: " + line)
    for line in problems:
        say("problem: " + line)
    for line in todo if not problems else []:           # once the rounds are sound, what is left is the build
        say("to build: " + line + " (pcbkit overview)")
    return 1 if problems or todo else 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pcbkit overview", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", nargs="?", default="build", choices=("build", "new", "note"))
    ap.add_argument("text", nargs="?", help="note: what to add")
    ap.add_argument("--on", default="status", help="note: the id of the board, topic, decision or fact it is about")
    ap.add_argument("--mark", help="note: the item's mark from now on (%s)" % ", ".join(MARK))
    ap.add_argument("--state", help="note: the item's state word from now on, beside its mark")
    ap.add_argument("--chose", help="note: the option of the decision that was chosen")
    ap.add_argument("--round", type=int, help="note: the round it belongs to (default: the newest)")
    ap.add_argument("--check", action="store_true", help="problems and watch lines only; exit 1 on a problem")
    ap.add_argument("--single", nargs="?", const="", metavar="OUT", help="also a copy with the pictures inside (default: "
                    "beside the page, -single.html)")
    ap.add_argument("--render", action="store_true", help="make the newest round's pictures again")
    ap.add_argument("--no-render", action="store_true", help="render nothing: use what pcbkit previews left")
    ap.add_argument("--project", help="the project's folder (default: the one this folder is in)")
    ap.add_argument("--dir", help="where the round files are (default: docs/overview)")
    ap.add_argument("--out", help="the page to write (default: docs/overview.html)")
    a = ap.parse_args(argv)
    if a.check and (a.action != "build" or a.render or a.single is not None):
        ap.error("--check only reads: it goes without new, note, --render and --single")
    project = Project(a.project)
    P = paths(project, a.dir, a.out)
    if a.action == "new":
        path = new_round(project, P)
        print(f"{shown(path, P['root'])}: write the round, then pcbkit overview --check and pcbkit overview")
        return
    if a.action == "note":
        if not a.text:
            ap.error("note needs its text: pcbkit overview note \"...\" --on ID")
        add_note(P, a.text, a.on, a.mark, a.chose, a.round, state=a.state)
    sys.exit(build(project, a.dir, a.out, a.single, not a.no_render, a.render, a.check))


if __name__ == "__main__":
    main()
