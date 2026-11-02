"""`pcbkit reroute`: a board's signals laid again, start to finish, in the order that proved itself (docs/ROUTER.md has the
why). The steps, each a process under the Python it needs:

    1. job      job.py          the board's signal routing taken out; what is fixed stays
    2. place    placeopt.py     small parts moved and turned so nets run direct (with "place" in the settings)
                moves.py        the moves applied; test pads listed as free come off for now
    3. route    nroute.py       the negotiation and its polish
    4. finish   tpplace.py      test pads put back where their nets ended up
                finish.py       routes written, DRC, one-sided vias out
                repair.py       what the routing left unconnected tied in; then finish.py again, up to three times
    5. review   lint.py         the board's old routing and the new one, on the same measures, and DRC of both

    pcbkit reroute SETTINGS.json [--from=STEP] [--until=STEP] [--out=DIR] [--bench] [--key=value ...]
    pcbkit reroute corpus [--boards=a,b] [--table] [--save] [--from=STEP] [--until=STEP] [--key=value ...]
    pcbkit reroute bench NAME [--boards=a,b] [--key=value ...]
    pcbkit reroute bench --table [NAME ...] | --lint NAME ...
    pcbkit reroute page CONFIG.json

SETTINGS: {"board": PATH.kicad_pcb, "name": NAME, "keep": [nets], "keep_w": 0.5, "lift": [nets], "clr": 0.2,
           "keepout": ["x0,y0,x1,y1"], "via_size": [0.45, 0.25], "ground": "GND", "refs": {"ic": ["U"], ...},
           "defaults": {...}, "nets": {"ADDR": {"clr": 0.15}}, "neg": {...}, "place": {placeopt.py's CONFIG},
           "route": {"workers": 4}, "free": [test pads]}
All but "board" are optional, and "board" is relative to the settings file. job.py takes clearance and via size from the
board, and "defaults" and "nets" go into the job over what it found. "keep" and "lift" default to the ground net. A
differential pair stays as it was drawn: name it in "keep". "refs" are reference prefixes, pcbkit_router's costs.py has
them. "route" goes to nroute.py as options, and --key=value goes there over it.

A run writes OUT/boards/NAME/ (OUT: --out, by default build/reroute in the project): base/ (job.py's), job/ (moves.py's: in,
full, hand, job.json), routes.json, final.kicad_pcb and .rpt, summary.json. A step that finished on these settings, this board
and this code is skipped, unless a step before it ran. --from=STEP does that step and those after it again, and takes those
before it as they stand; --until=STEP stops after it. --bench copies the job to the bench.

`corpus` runs bench/corpus.json's boards, each at its pinned commit, one after the other, and prints them against
bench/baseline.json; --table only prints, --save makes these results the baseline. `bench` runs nroute alone on those boards
side by side, for a change to the router (pcbkit_router's bench.py). `page` draws a comparison page (page.py). Both of these
files are in a checkout of pcb-kit, and the corpus needs its projects' clones.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from ..config import find_root
from ..kicad import KICAD_PYTHON, tools_dir
from .finish import drc

ROOT = Path(__file__).resolve().parents[2]      # the folder with pcbkit/ and pcbkit_router/
CORPUS, BASELINE = ROOT / "bench" / "corpus.json", ROOT / "bench" / "baseline.json"
STEPS = ["job", "place", "route", "finish", "review"]
QUIET = ("wxapp", "swig", "debug:", "traits", "no destructor found")
MEASURES = ("vias", "mm", "corners90", "jogs", "tees", "necks", "via_in_ic", "hug_mm", "under_mm", "detour_mm", "crossbacks")


def router_dir():
    return os.path.join(tools_dir(), "router")


def setup_router():
    """The router's own Python, under tools_dir(), from pcbkit_router/requirements.txt"""
    uv = shutil.which("uv")
    if not uv:
        sys.exit("pcbkit setup-router needs uv: https://docs.astral.sh/uv/")
    d = router_dir()
    shutil.rmtree(d, ignore_errors=True)
    subprocess.run([uv, "venv", "-q", "-p", "3.12", d], check=True)
    subprocess.run([uv, "pip", "install", "-q", "-p", os.path.join(d, "bin", "python"), "-r",
                    str(ROOT / "pcbkit_router" / "requirements.txt")], check=True)
    print(f"the router's Python is in {d}")


def router():
    py = os.path.join(router_dir(), "bin", "python")
    if not os.path.exists(py):
        sys.exit("The router's Python is not installed: run `pcbkit setup-router`")
    return py


def run(py, module, *a, log=None):
    """a step, as a process; with log, its output goes there as it comes"""
    cmd = [py, "-m", module, *map(str, a)]
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(filter(None, [str(ROOT), os.environ.get("PYTHONPATH")])))
    if log:
        with open(log, "w") as f:
            r = subprocess.run(cmd, env=env, stdout=f, stderr=subprocess.STDOUT)
        text = Path(log).read_text()
    else:
        r = subprocess.run(cmd, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        text = r.stdout
    text = "\n".join(l for l in text.splitlines() if not any(q in l.lower() for q in QUIET))
    if r.returncode:
        sys.exit(f"{module} failed:\n{text[-3000:]}")
    return text


def tail(text):
    """a tool's last line, after the warnings it gave on the way"""
    lines = text.splitlines()
    return "\n".join([l for l in lines[:-1] if l.startswith("!!")] + lines[-1:])


def read(path):
    with open(path) as fh:
        return json.load(fh)


def write(path, data):
    with open(path, "w") as fh:
        json.dump(data, fh, indent=1)


def area(polys):
    """mm2 of geom.py's [[outline, holes...], ...]"""
    def ring(r):
        return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(r, r[1:] + r[:1]))) / 2
    return sum(ring(p[0]) - sum(ring(h) for h in p[1:]) for p in polys)


def job_step(cfg, out):
    ground = cfg.get("ground", "GND")
    args = [cfg["board"], out / "base", "--keep=" + ",".join(cfg.get("keep", [ground])), f"--keep-w={cfg.get('keep_w', 0.5)}",
            "--lift=" + ",".join(cfg.get("lift", [ground])), f"--ground={ground}"]
    if "clr" in cfg:
        args.append(f"--clr={cfg['clr']}")
    if cfg.get("keepout"):
        args.append("--keepout=" + ";".join(cfg["keepout"]))
    print(tail(run(KICAD_PYTHON, "pcbkit.reroute.job", *args)), flush=True)
    jp = out / "base/job.json"
    job = read(jp)
    job["defaults"].update(cfg.get("defaults", {}))
    for k in ("via_size", "neg", "soft_pours", "refs"):
        if k in cfg:
            job[k] = cfg[k]
    over, known = cfg.get("nets", {}), set()
    for s in job["nets"]:
        for name in (s["net"], s["net"].split("/")[-1]):
            if name in over:
                s.update(over[name])
                known.add(name)
    if set(over) - known:
        sys.exit(f"settings name nets the job doesn't have: {sorted(set(over) - known)}")
    write(jp, job)
    run(KICAD_PYTHON, "pcbkit.reroute.geom", out / "base/in.kicad_pcb", out / "base/in.json")
    geom = read(out / "base/in.json")
    board = area(geom["outline"])
    for z in geom["items"]:           # with the signals off, a pour floods the room they had and walls everything in
        if z["kind"] == "zone" and z["layer"] in "FB" and z.get("name") not in job["soft_pours"] and area(z["polys"]) > 0.2 * board:
            print(f"!! pour {z.get('name')} ({z['net']}) covers {area(z['polys']) / board:.0%} of the board and will wall the "
                  f"signals in: add it to \"lift\" (and \"keep\" its net) or to \"soft_pours\"", flush=True)


def place_step(cfg, out):
    moves = out / "moves.json"
    if "place" in cfg:
        pc = out / "place.json"
        write(pc, dict(cfg["place"], clr=read(out / "base/job.json")["defaults"]["clr"], free=cfg.get("free", [])))
        text = run(router(), "pcbkit_router.placeopt", out / "base/in.json", out / "base/job.json", moves, pc, log=out / "place.log")
        print(tail(text), flush=True)
    else:
        write(moves, {"moves": {}, "free": cfg.get("free", [])})
    print(tail(run(KICAD_PYTHON, "pcbkit.reroute.moves", out / "base", out / "job", moves)), flush=True)


def route_step(out, opts):
    run(router(), "pcbkit_router.nroute", out / "job/in.json", out / "job/job.json", out / "routes.json", *opts, log=out / "nroute.log")
    lines = (out / "nroute.log").read_text().splitlines()
    print(next((l for l in reversed(lines) if l.startswith("== final")), "?"), flush=True)


def finish_step(out):
    D, job = out / "job", read(out / "job/job.json")
    clr, via = f"--clr={job['defaults']['clr']}", "--via=" + ",".join(map(str, job.get("via_size", [0.45, 0.25])))
    jobarg, tp = f"--job={D / 'job.json'}", out / "final-tp.json"
    run(KICAD_PYTHON, "pcbkit.reroute.geom", D / "full.kicad_pcb", D / "full.json")
    print(run(router(), "pcbkit_router.tpplace", D / "in.json", out / "routes.json", D / "full.json", D / "moves.json", tp, clr, via,
              jobarg), flush=True)
    last = None
    for fix in range(4):                       # up to three rounds of repairs, each written and checked, while they help
        text = run(KICAD_PYTHON, "pcbkit.reroute.finish", D / "full.kicad_pcb", tp, out / "final.kicad_pcb", via)
        print(tail(text), flush=True)
        left = (out / "final.rpt").read_text().count("[unconnected_items]")
        if fix == 3 or not left or left == last:
            break
        last = left
        run(KICAD_PYTHON, "pcbkit.reroute.geom", out / "final.kicad_pcb", out / "final.json")
        print(run(router(), "pcbkit_router.repair", out / "final.json", out / "final.rpt", tp, clr, via, jobarg), flush=True)
    run(KICAD_PYTHON, "pcbkit.reroute.geom", out / "final.kicad_pcb", out / "final.json")


def review_step(cfg, out, secs):
    D, B = out / "job", out / "base"
    run(KICAD_PYTHON, "pcbkit.reroute.geom", D / "hand.kicad_pcb", D / "hand.json")
    # each on the board it was laid on: the old routing on the parts where they were, the new one on the parts as moved
    print(run(router(), "pcbkit_router.lint", B / "job.json", B / "in.json", f"old={D / 'hand.json'}", f"--json={out / 'lint-old.json'}"), flush=True)
    print(run(router(), "pcbkit_router.lint", D / "job.json", D / "in.json", f"new={out / 'final.json'}", f"--json={out / 'lint-new.json'}"), flush=True)
    job, pours = read(D / "job.json"), ""
    if job.get("soft_pours"):
        text = run(router(), "pcbkit_router.pourcheck", D / "in.json", out / "final.json", ",".join(job["soft_pours"]))
        print(text, flush=True)
        pours = text.splitlines()[-1]
    routes, passes = read(out / "routes.json"), read(out / "routes-passes.json")
    s = dict(name=cfg["name"], nets=len(routes), failed=sum(r["failed"] for r in routes),
             drc=dict(old=drc(D / "hand.kicad_pcb")[0], new=drc(out / "final.kicad_pcb")[0]),
             lint={**read(out / "lint-old.json"), **read(out / "lint-new.json")}, pours=pours, router_s=passes["s"], step_s=secs)
    write(out / "summary.json", s)
    print(f"{s['failed']} failed; DRC old {s['drc']['old']}; new {s['drc']['new']}", flush=True)


def stamp(cfg, opt):
    """what a step's result rests on: the settings, the board's files and the router's code"""
    h = hashlib.sha256(json.dumps([cfg, opt], sort_keys=True).encode())
    base = os.path.splitext(cfg["board"])[0]
    files = [Path(f"{base}.{e}") for e in ("kicad_pcb", "kicad_pro", "kicad_dru")]
    for p in files + sorted((ROOT / "pcbkit_router").glob("*.py")) + sorted((ROOT / "pcbkit" / "reroute").glob("*.py")):
        if p.exists():
            h.update(p.read_bytes())
    return h.hexdigest()


def route_board(cfg, work, opt):
    """one board through the steps; opt: from, until, bench, and the rest goes to nroute"""
    opt = dict(opt)
    out = work / "boards" / cfg["name"]
    out.mkdir(parents=True, exist_ok=True)
    start = STEPS.index(opt.pop("from")) if "from" in opt else None
    bench = opt.pop("bench", None)
    stop = STEPS.index(opt.pop("until")) if "until" in opt else len(STEPS) - 1
    key, secs, ran = stamp(cfg, opt), {}, False     # after a step that ran, whatever followed it is stale
    for i, step in enumerate(STEPS[:stop + 1]):
        mark = out / f".done-{step}"                # written last: a step that failed left no mark
        if not ran and mark.exists() and (mark.read_text() == key if start is None else i < start):
            continue
        ran = True
        (out / "summary.json").unlink(missing_ok=True)
        for s in STEPS[i:]:
            (out / f".done-{s}").unlink(missing_ok=True)
        t0 = time.time()
        print(f"--- {step}", flush=True)
        if step == "job":
            job_step(cfg, out)
        elif step == "place":
            place_step(cfg, out)
        elif step == "route":
            route_step(out, [f"--{k}={v}" for k, v in {**cfg.get("route", {}), **opt}.items()])
        elif step == "finish":
            finish_step(out)
        else:
            review_step(cfg, out, secs)
        secs[step] = round(time.time() - t0)
        mark.write_text(key)
    if opt and "route" not in secs:
        sys.exit(f"the route step didn't run, so these did nothing: {sorted(opt)} (--from=route runs it)")
    if bench:
        inputs = work / "bench/inputs"
        inputs.mkdir(parents=True, exist_ok=True)
        shutil.copy(out / "job/in.json", inputs / f"{cfg['name']}.json")
        shutil.copy(out / "job/job.json", inputs / f"{cfg['name']}.job.json")
    print(f"-> {out}")


def corpus_boards(names=None):
    if not CORPUS.exists():
        sys.exit(f"no corpus at {CORPUS}: it comes with a checkout of pcb-kit")
    boards = read(CORPUS)
    unknown = set(names or []) - {b["name"] for b in boards}
    if unknown:
        sys.exit(f"not in the corpus: {sorted(unknown)}")
    return [b for b in boards if not names or b["name"] in names]


def fetch(entry, work):
    """A corpus board's settings, with the board written out at its pinned commit: the projects move on, and a change to the
    router is judged on the same boards each time"""
    cfg = {k: v for k, v in entry.items() if k not in ("repo", "commit")}
    d = work / "corpus" / entry["name"]
    d.mkdir(parents=True, exist_ok=True)
    base = os.path.splitext(entry["board"])[0]
    for ext in ("kicad_pcb", "kicad_pro", "kicad_dru"):
        r = subprocess.run(["git", "-C", os.path.expanduser(entry["repo"]), "show", f"{entry['commit']}:{base}.{ext}"], capture_output=True)
        if r.returncode == 0:
            (d / f"{os.path.basename(base)}.{ext}").write_bytes(r.stdout)
        elif ext == "kicad_pcb":
            sys.exit(f"{entry['name']}: {r.stderr.decode().strip()}")
    cfg["board"] = str(d / os.path.basename(entry["board"]))
    return cfg


def table(names, work, save=False):
    """the corpus's results, against the baseline; save makes them the baseline"""
    base = read(BASELINE) if BASELINE.exists() else {}
    rows = {}
    print(f"{'board':<20} {'nets':>4} {'failed':>6}  " + " ".join(f"{m[:8]:>8}" for m in MEASURES) + "  DRC")
    for n in names:
        p = work / "boards" / n / "summary.json"
        if not p.exists():
            print(f"{n:<20} no results")
            continue
        s = read(p)
        rows[n] = dict(nets=s["nets"], failed=s["failed"], drc=s["drc"]["new"], **{m: s["lint"]["new"].get(m, 0) for m in MEASURES})
        print(f"{n:<20} {s['nets']:>4} {s['failed']:>6}  " + " ".join(f"{rows[n][m]:>8}" for m in MEASURES) + f"  {s['drc']['new']}")
        if s.get("pours", "").endswith("cut off"):
            print(f"{'':<20} pours: {s['pours']}")
        changed = {k: (base[n][k], v) for k, v in rows[n].items() if n in base and base[n][k] != v}
        if changed:
            print(f"{'':<20} against the baseline: " + ", ".join(f"{k} {a} -> {b}" for k, (a, b) in changed.items()))
    if save:
        write(BASELINE, {**base, **rows})
        print(f"-> {BASELINE}")


def corpus(opt, work):
    names = [b for b in opt.pop("boards", "").split(",") if b] or None
    only_table, save = opt.pop("table", None), opt.pop("save", None)
    boards = corpus_boards(names)
    if not only_table:
        for entry in boards:
            print(f"=== {entry['name']}", flush=True)
            route_board(fetch(entry, work), work, opt)
    table([b["name"] for b in boards], work, save)


def bench(argv, work):
    if not argv or argv[0] not in ("--table", "--lint"):
        names = next((a.split("=", 1)[1].split(",") for a in argv if a.startswith("--boards=")), None)
        for entry in corpus_boards(names):                  # the marks skip what's still good
            route_board(fetch(entry, work), work, {"until": "place", "bench": "1"})
    print(run(router(), "pcbkit_router.bench", *argv), flush=True)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    args = [a for a in argv if not a.startswith("--")]
    if not args or "-h" in argv or "--help" in argv:
        return print(__doc__)
    opt = dict(a[2:].split("=", 1) if "=" in a else (a[2:], "1") for a in argv if a.startswith("--"))
    if argv[0] in ("corpus", "bench"):
        work = Path(opt.pop("out", ROOT / "build" / "reroute"))
    else:
        work = Path(opt.pop("out", Path(find_root(str(Path(args[-1]).resolve().parent))) / "build" / "reroute"))
    work = work.resolve()
    os.environ["REROUTE_WORK"] = str(work)      # pcbkit_router's bench.py and page.py read it
    if argv[0] == "corpus":
        return corpus(opt, work)
    if argv[0] == "bench":
        return bench([a for a in argv[1:] if not a.startswith("--out=")], work)
    if argv[0] == "page":
        return print(run(router(), "pcbkit_router.page", args[1]))
    cfg = read(args[0])
    cfg["board"] = str(Path(args[0]).resolve().parent / os.path.expanduser(cfg["board"]))
    cfg.setdefault("name", Path(args[0]).stem)
    route_board(cfg, work, opt)
