"""Router bench: nroute on a fixed set of boards side by side, so that a way of routing is judged on more than one board
(one build moves by a failed connection or two with any small change). A change to the router is judged here.

    pcbkit reroute bench NAME [--boards=a,b,c] [--key=value ...]    nroute with these options on each board ({board} in an
                                                                    option is the board's name); every corpus board by default
    pcbkit reroute bench --table [NAME ...]                         the results so far, all runs by default
    pcbkit reroute bench --lint NAME ...                            the faults counted (lint.py)

Boards are WORK/bench/inputs/<board>.json, the geometry of the board before routing (geom.py), with the job beside it
as <board>.job.json; `pcbkit reroute --bench` puts a board there, and `pcbkit reroute bench` the corpus's. A run writes WORK/bench/<NAME>/<board>.json (routes),
<board>-passes.json, <board>.log and run.json (options, wall and CPU seconds, the CPU of nroute's workers included)."""
import sys, os, json, time, subprocess, re

from . import WORK

BENCH = WORK / "bench"; INPUTS = BENCH / "inputs"


def boards():
    return sorted(p.name[:-len(".job.json")] for p in INPUTS.glob("*.job.json"))


def run(name, names, args):
    out = BENCH / name; out.mkdir(parents=True, exist_ok=True)
    procs, t0 = {}, time.time()
    for b in names:
        for f in (out / f"{b}.json", out / f"{b}-passes.json"): f.unlink(missing_ok=True)   # a crash shows no old results
        log = open(out / f"{b}.log", "w")
        procs[b] = (subprocess.Popen([sys.executable, "-m", "pcbkit_router.nroute", str(INPUTS / f"{b}.json"), str(INPUTS / f"{b}.job.json"),
                                      str(out / f"{b}.json"), *(x.replace("{board}", b) for x in args)],
                                     stdout=log, stderr=subprocess.STDOUT), time.time())
    meta = {"args": args, "boards": {}}
    left = dict(procs)
    while left:
        pid, status, ru = os.wait4(-1, 0)
        b = next((k for k, (p, _) in left.items() if p.pid == pid), None)
        if b is None: continue
        p, t = left.pop(b)
        meta["boards"][b] = dict(status=status, wall=round(time.time() - t), cpu=round(ru.ru_utime + ru.ru_stime))
        print(f"{name}/{b}: {'done' if status == 0 else f'FAILED, status {status}, see its log'}, {meta['boards'][b]['wall']} s", flush=True)
    meta["wall"] = round(time.time() - t0)
    json.dump(meta, open(out / "run.json", "w"), indent=1)
    table([name])


def row(name, b):
    d = BENCH / name; pf = d / f"{b}-passes.json"
    if not pf.exists(): return None
    p = json.load(open(pf)); meta = json.load(open(d / "run.json")) if (d / "run.json").exists() else {"boards": {}}
    passes = [e for e in p["passes"] if isinstance(e.get("pass_"), int)]
    legal = next((e for e in p["passes"] if e.get("pass_") == "legal"), {})
    final = next((e for e in p["passes"] if e.get("pass_") == "final"), {})
    log = open(d / f"{b}.log").read() if (d / f"{b}.log").exists() else ""
    over = sum(1 for v in p["nets"].values() if v.get("excess"))
    failed = [f"{n}" + (f"x{v['failed']}" if v["failed"] > 1 else "") for n, v in p["nets"].items() if v["failed"]]
    m = meta["boards"].get(b, {})
    return dict(failed=final.get("failed"), vias=final.get("vias"), mm=final.get("mm"), over=over,
                legal=legal.get("failed"), conf=passes[-1]["conflicted"] if passes else None, passes=len(passes),
                stalls=len(re.findall(r"^== stalled", log, re.M)), wall=m.get("wall", p.get("s")), cpu=m.get("cpu"),
                which=",".join(failed))


def table(names=None):
    names = names or sorted((d.name for d in BENCH.iterdir() if d.is_dir() and d.name != "inputs"),
                            key=lambda n: (BENCH / n).stat().st_mtime)
    print(f"{'run':<18} {'board':<9} {'failed':>6} {'vias':>5} {'mm':>6} {'over':>4} {'legal':>5} {'passes':>6} "
          f"{'stalls':>6} {'wall':>6} {'cpu':>6}  failed nets / options")
    for n in names:
        meta = json.load(open(BENCH / n / "run.json")) if (BENCH / n / "run.json").exists() else {}
        tot = [0, 0, 0]
        for b in sorted(f.name[:-len("-passes.json")] for f in (BENCH / n).glob("*-passes.json")):
            r = row(n, b)
            if r is None: continue
            tot = [tot[0] + (r["failed"] or 0), tot[1] + (r["vias"] or 0), tot[2] + (r["mm"] or 0)]
            print(f"{n:<18} {b:<9} {r['failed']!s:>6} {r['vias']!s:>5} {r['mm']!s:>6} {r['over']:>4} {r['legal']!s:>5} "
                  f"{r['passes']:>6} {r['stalls']:>6} {r['wall']!s:>6} {r['cpu']!s:>6}  {r['which']}")
        print(f"{n:<18} {'all':<9} {tot[0]:>6} {tot[1]:>5} {tot[2]:>6}  {' '.join(meta.get('args', []))}")


def lint_table(names):
    """lint.py's counts per run and board"""
    from . import lint as L
    res = {}
    for n in names:
        for f in sorted((BENCH / n).glob("*-passes.json")):
            b = f.name[:-len("-passes.json")]
            res[f"{n} {b}"] = L.lint(json.load(open(INPUTS / f"{b}.job.json")), json.load(open(INPUTS / f"{b}.json")),
                                     json.load(open(BENCH / n / f"{b}.json")))
    L.show(res, "run, board")


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a or a[0] == "--table": table(a[1:] or None)
    elif a[0] == "--lint": lint_table(a[1:])
    else:
        names = next((x.split("=", 1)[1].split(",") for x in a if x.startswith("--boards=")), boards())
        run(a[0], names, [x for x in a[1:] if not x.startswith("--boards=")])
