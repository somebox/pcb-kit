# CI and automated checks for pcb-kit and its projects

Status 2026-10-30: a proposal. No workflow exists yet; `kicad.py` has Linux paths and the KiCad 10.0.6 container
has been tried locally on rgb-spotlight (see "Tried locally"). From a review of takzen/pcb-inspector,
azonenberg/pcb-checklist and aklofas/kicad-happy's GitHub Action, a sweep of the homelib library, a web survey of
KiCad CI tooling, and a look at the six corpus projects.

## Where things stand

- **pcb-kit** has one workflow, `release.yml`, which checks that the four version strings agree. There are no tests.
  Changes are checked by hand on the corpus boards, old pcbkit against new (as in the review of PR 4).
- **The projects** have no CI. Each has its own check scripts (`netcheck.py`, `check_netlist.py`,
  `check_driver.py`) run by hand, and docs/CONVENTIONS.md's "Checks before an order" is a seven-step manual list.
- **pcbkit ran on macOS only.** `pcbkit/kicad.py` took `kicad-cli` and the Python with `pcbnew` from inside
  `KiCad.app`. `background_app()` already skipped the dock-icon copy off macOS, so Linux support was two paths, now
  in `kicad.py`: `kicad-cli` from PATH, and `/usr/bin/python3`, the only one that can import the image's `pcbnew`.
  `ensure_pcbnew()` already re-runs a command under that interpreter.
- **Every corpus board is a KiCad 10 file** (`version 20260206`, 11 boards across six projects). KiCad 9 cannot open
  them, so CI needs KiCad 10. This Mac has 10.0.6.
- **The corpus is mostly private.** rgb-spotlight and pcb-kit are public; the sign, dodeca, Orbitchron and siebenuhr
  repos are private and esp32-s3-ha-node has no remote. pcb-kit's own CI can only use public boards or fixtures
  committed to it, so the corpus regression stays a local step.
- **No project uses an absolute library or 3-D model path** (every `fp-lib-table`, `sym-lib-table` and model path
  goes through `${KIPRJMOD}` or KiCad's own variables), so the boards would open the same on a CI machine.
- **An import check would already catch something:** under Python 3.9 every module in `pcbkit/` imports without
  KiCad except `pcbkit/silk.py`, which imports `pcbnew` at module level (line 13), against the AGENTS.md rule.

## The three repositories

### kicad-happy's GitHub Action

A composite action around pure-Python S-expression parsers: it needs no KiCad, only Python 3.10 and, optionally,
ngspice. It finds the project's schematic and board, runs about 65 schematic detectors (ESD on connector lines,
decoupling, pull-ups on open-drain buses, LED resistors, crystal load caps, power sequencing, USB/CAN/SPI bus
checks, sourcing gaps), a PCB analysis, EMC and thermal scores and optional SPICE runs on recognised subcircuits.
It writes a markdown report, sets a commit status, and a second action upserts the report as a PR comment.

What is worth taking from it:

- **`diff-base: true`** reports only what changed against the PR's base branch: parts added or removed, signal
  values that moved, EMC findings new or resolved. On a board under review this is the part people read.
- **Fork-safe pattern:** one workflow analyses with a read-only token and uploads the report; a second, triggered
  by `workflow_run`, posts the comment with write access.
- **Chaining an agent:** `anthropics/claude-code-action` reads the deterministic report and the datasheets and
  writes the review, told not to re-run the analysis. Their estimate is $1-3 per PR for a quick review and $5-15 for
  a thorough one.
- **Their own CI** (`.github/workflows/ci.yml`): `py_compile` on two Pythons; a SKILL.md frontmatter check (a
  description over Codex's 1024-character cap once shipped and was rejected on load); `--help` on every script; a
  test harness pinned to a commit (565 PR-gate tests in about 30 s); schema-contract tests on the JSON outputs; and a
  determinism guard that runs each analyser twice and compares the JSON. Before each tag, a corpus of 5,857 public
  KiCad projects runs on the old and new versions and every changed result must be accounted for (VALIDATION.md).

How it fits: it can run unchanged on a project repo as a free second opinion, with no KiCad to install. Its
findings are heuristics and how noisy they are on our boards is unknown, so it starts as an advisory check on one
project. Its `fab_release_gate.py` overlaps with `pcbkit release`.

### pcb-inspector

Three layers: `kicad-cli` ERC, DRC and schematic parity; five geometric rules computed from the board; and an
opt-in vision model reviewing raytraced renders of each side. Findings are CRITICAL, WARNING or SUGGESTION, and
`fail_on` sets which fails the run. A `.pcb-inspector.yaml` beside the project turns rules off or changes their
thresholds.

The geometric rules are the ones `pcbkit check` doesn't have:

- **Decoupling distance** (`HEUR-DEC-001`): from each IC supply pin to its nearest bypass capacitor, adding the
  board thickness when the capacitor is on the other side. Supply pins are found by schematic pin name as well as
  net name.
- **Power trace width** against the net's own class, and against a power-rail guideline.
- **Differential pair skew**, critical only for pairs named as a fast interface.
- **Switching loop area**, with the switch node found from an `SW`/`LX` pin or the inductor topology, never from the
  net name alone.
- **Ground reference**: the share of each signal segment over a ground plane on the adjacent layer, from the
  board's stack order.

What is worth taking from its CI:

- **Installing KiCad 10 on `ubuntu-latest`** from `ppa:kicad/kicad-10.0-releases` with `--no-install-recommends`
  (the recommends pull in about 3 GB of libraries), then `kicad-cli version`.
- **A missing tool fails the run.** Without `kicad-cli` the DRC layer is reported as "not run", and
  `--require-kicad-cli`, which the action passes, makes that an error. Their self-test once passed while checking
  nothing: it pointed at the repository root, which holds no board.
- **The self-test asserts what was found, not only that the step failed.** It runs the action on a deliberately
  flawed board (as a KiCad 8 file and a KiCad 10 file), expects the step to fail, then reads the JSON report and
  asserts that DRC and the heuristics both ran and that DRC and decoupling findings are CRITICAL.
- **Golden boards** in `tests/golden_samples/`: a clean board that must score 100 and flawed ones whose every
  planted defect must be reported.

How it fits: a usable CI tool in its own right, but with pydantic, shapely, typer and mcp it can't live inside
`pcbkit`, which takes no third-party packages. The decoupling and ground-reference rules are worth writing into
`pcbkit check` with our own data; the testing approach is worth copying as it is.

### pcb-checklist

About 110 sign-off items, schematic and layout, each one learned from a mistake; a checklist, not a tool (BSD-3).
Sorted by what could check it:

| | Items |
| --- | --- |
| **Already automated by pcbkit** | ERC clean; DRC clean; schematic parity; courtyards not overlapping (DRC); refill zones before export and export Gerbers and drills together (`pcbkit fab`, `release`); 1:1 paper print (`pcbkit print`); visual check of the CAM files (`pcbkit previews`); fiducials on panels (`pcbkit panel`); rev marking on the board (`pcbkit release` stamps it) |
| **Automatable from the files** | thermal/exposed pads on a net; test pad on every power rail; ESD part on every signal leaving the board; pull-ups on open-drain buses; strap pins with a defined level; crystal load caps present; capacitor voltage rating against its rail; board design rules at or above the fab's capability; bottom silkscreen mirrored; silkscreen text size within fab limits; solder mask opening on every SMT pad; no uncapped vias in pads; no paste on test points; mounting-hole clearance |
| **Needs a datasheet, an agent or a person** | pinouts against datasheets; derating curves; power budget per rail; sequencing; regulator stability with the chosen output cap; errata; connector mechanical standards; analog separation |

The third row is what the failure-review skill does; the second is new checks (below).

## Running KiCad 10 in CI

From the web survey; versions as of 2026-10-30.

- **The official image**, `kicad/kicad` on Docker Hub and `ghcr.io/kicad/kicad`, has a tag for every 10.0 patch
  (10.0.7 is the newest, 2026-10-18; there is no 10.0.3). The plain tag is about 812 MB compressed, `-full` about
  1.45 GB with the 3-D models, which renders need (`-full` unpacks to 7.5 GB). Both carry the stock symbols and
  footprints. amd64 only: on this Mac it runs under emulation. KiCad describes the images as being for `kicad-cli`,
  not the GUI.
- **Python in the image** is Debian trixie's 3.13.5, with KiCad built with scripting on. `_pcbnew.so` is built for
  that interpreter, so neither `setup-python`'s Python nor a venv can import it. `/usr/bin/python3` imports
  `pcbnew` 10.0.6 from `/usr/lib/python3/dist-packages` with no `PYTHONPATH` set (tried). pcbkit's 3.9 floor still
  holds; it just runs on 3.13 there.
- **Users:** the image runs as `kicad` (uid 1000, `HOME=/home/kicad`), whose global library tables are already in
  `~/.config/kicad/10.0`. Run as root (open-bt-rig-interface passes `--user root`) there are none: on rgb-spotlight
  DRC then adds 3 to 39 `lib_footprint_issues` warnings and ERC up to 125 `lib_symbol_issues` and
  `footprint_link_issues` warnings. Exit codes don't change, since they are warnings, but the reports do. Copying
  `/usr/share/kicad/template/{sym,fp}-lib-table` into `~/.config/kicad/10.0` first (as OpenHornet does) gives
  output identical to the `kicad` user's (tried).
- **The PPA** (`ppa:kicad/kicad-10.0-releases`, as pcb-inspector and OpenHornet use) is a patch ahead of the image
  (10.0.8 on Ubuntu 22.04 and 24.04), with the libraries as separate packages. Whether `--no-install-recommends`
  still gives a working `pcbnew` is unconfirmed. The image, pinned to the Mac's patch release, is the better choice:
  the same KiCad on both makes CI and local results comparable.
- **`kicad-cli` exit codes:** 0 success, 1 bad arguments, 3 a file that won't load, 5 violations, 6 a jobset step
  failed. DRC and ERC exit 0 with violations unless `--exit-code-violations` is given. pcbkit reads the JSON
  itself, so it doesn't depend on that, but a missing or empty report has to count as a failure: one project had an
  out-of-memory kill of `kicad-cli` pass as a clean DRC because the report file existed beforehand. pcbkit had the
  same bug, now fixed (see "Tried locally").
- **New in 10.0:** `pcb drc --refill-zones --save-board` (what `pcbkit drc --save` uses), `pcb export stats` (JSON),
  `pcb upgrade` and `sch upgrade`. `fp upgrade` and `sym upgrade` exist in both 9 and 10. Loading a file with
  `upgrade` is a cheap format check, exiting 3 when it fails. Jobsets (`kicad-cli jobset run`, `.kicad_jobset`, since
  9.0) can run ERC, DRC and exports as one step. 10.0.4, 10.0.5 and 10.0.7 each fixed a CLI crash (jobsets, a zone
  fill without a DRC engine, unknown job types).
- **KiCad 11 plans to remove the SWIG bindings** that `import pcbnew` uses ("The current plan is to remove the SWIG
  bindings in KiCad 11.0", dev-docs.kicad.org). KiCad 9 and 10's IPC API only talks to a running KiCad and can't
  plot or export; headless use arrives in 11. Every pcbnew command in pcbkit is affected, so this matters more in the
  long run than CI does. Tests on fixture boards would show what breaks when 11 arrives.
- **Diffs between base and head:** `sksat/kicadiff` (MIT, v0.2.0, needs `kicad-cli` 9+) runs ERC and DRC on both
  revisions, fails on new violations only, and writes a PR report and an HTML viewer; OpenHornet runs it on KiCad 10,
  advisory, with the comment posted from a separate `workflow_run` job. `Cameronrlewis/kicad-review` is a reusable
  workflow on `kicad/kicad:10.0.6-full` pinned by digest that fails only on ERC, DRC and parity errors a change
  introduced. KiRI showed blank output on KiCad 10 boards (issue #142); a fix was pushed in June but is unconfirmed.
- **KiBot** (v1.9.1, action tag `v2_k10`, image on KiCad 10.0.5) does ERC, DRC, zone-fill checks and every kind of
  output, including a `diff` PDF and a KiRI-style page. Its KiCad 10 support is partial (renders through
  `kicad-cli`, variants partly), and it overlaps with `pcbkit release`. Of KiBot, only the diff outputs are worth
  borrowing.
- **Library lint:** KiCad's KLC checkers (`check_symbol.py`, `check_footprint.py`, now in `kicad-library-tools`)
  need no KiCad and take file paths, exiting 0 clean, 2 warnings, 3 errors. The symbol checker only accepts KiCad 10
  symbol files (`version 20251024`; run `kicad-cli sym upgrade` first). Many rules are about KiCad's own library
  (names, 3-D paths), so on a project's `lib/` they need `-r` to choose rules, and stay advisory.
- **No maintained parser reads KiCad 10 files without KiCad:** kiutils and kicad-skip stopped before KiCad 8. Tier-0
  checks that read board or schematic files use pcbkit's own `sexp.py`, as kicad-happy does with its own parser.
  gerbonara reads Gerber and Excellon but needs Python 3.12 and has no validate command.
- **Nothing maintained checks a JLC BOM against its CPL** (designators, quantities, rotations); one browser-only tool
  exists. pcbkit writes both, so its own test is the place for it.

### Tried locally

On 2026-10-30, `kicad/kicad:10.0.6-full` (digest `sha256:e36e29b5f4a6…`) under Docker Desktop on this Mac
(amd64 emulated on Apple Silicon), against KiCad 10.0.6 on macOS. pcb-kit was mounted read-only and run as
`PYTHONPATH=/pcb-kit python3 -m pcbkit.cli`, on a copy of rgb-spotlight's working tree. The boards were `led`,
`driver`, and `broken`, a copy of `driver` with planted defects: a 31 mm +5V track cut, C1 pushed onto C2, and a
0.1 mm dead-end track. The commands were `drc --full`, `drc --refill --full`, `erc` and `check`.

- **Same results.** Eleven of the twelve pcbkit summaries are byte-identical, and so are the raw `kicad-cli` ERC
  reports and DRC reports of the clean boards. The exception is `broken`'s DRC: the same nine violations and one
  unconnected item, in a different order. So a test that pins DRC output has to sort it first. The defects all
  showed up on both platforms: a short, a clearance error, overlapping courtyards, a track width error, two solder
  mask bridges, a dangling track and via, silk overlap, the unconnected +5V, and the stub in `check`.
- **Time under emulation:** about 10 s per DRC (3 s on macOS), 8 s per ERC (1.5 s), and 1 s per `check` (0.5 s).
  That is about 90 s for the twelve runs. GitHub's runners are amd64, so this emulation cost goes away there; their
  real speed wasn't measured.
- **pcbkit bug, stale reports (fixed):** `drc` and `erc` wrote `kicad-cli`'s report to a fixed path in the temp dir
  and read it back without checking that this run wrote it. After a good run, `pcbkit drc boards/nope/nope.kicad_pcb`
  (no such file) printed the previous board's clean summary and exited 0. That happened on macOS and in the
  container. In CI, where one container checks several boards, a crash or a bad path would have passed as the
  previous board's result.
  - **The fix:** `kicad.cli_report()` removes the report first, and stops with `kicad-cli`'s message on a non-zero
    exit or an unreadable report.
  - **Failure cases tested:** a missing file, a truncated board or schematic, a `kicad-cli` killed by signal 9, one
    that exits 0 with no report, and one that writes half a report. Before the fix the first four passed as clean;
    after it, all fail.
  - **No change on good runs:** `drc --full` and `erc` give byte-identical output and exit codes before and after on
    all 11 corpus boards and 10 schematics, and so do `release`'s checks on three projects and the twelve container
    runs above.
  - **Still possible:** two runs at once with the default report path can still read each other's report.
- **`pcbkit check` always exits 0,** even when it reports the planted stub. For CI it needs an exit status, or the
  test has to read its output.
- **Not tried yet:** the plain (non-`-full`) image, `fab`/`release`/renders in the container, and whether Linux
  `fab_fingerprint`s match macOS ones.

## What CI can run

Three tiers by cost: **files only** (seconds, no KiCad), **KiCad 10** (a container, a few minutes), and **agent
review** (dollars, on request).

### pcb-kit's own CI

Files only:

1. Every module imports under Python 3.9, and under a current Python; `compileall` on both. Fix `silk.py` first.
2. `scripts/release.py check` (exists).
3. Skill and plugin lint: each SKILL.md's frontmatter (name, description length), `claude plugin validate --strict`,
   and a byte scan of images under the plugin folder for shell tokens (the plugin directory flagged
   `docs/images/sim-load-steps.png` for the bytes `IWr`).
4. `pcbkit --help` and `pcbkit <command> --help` for every command.

With KiCad 10:

5. **Fixture boards with known defects**, as pcb-inspector does: a small KiCad 10 board per finding `pcbkit check`
   makes (floating copper, a stub, a loop, a pour reaching one pad, a power pad fed only through thin track) and per
   `pcbkit cleanup` case (duplicate, zero-length, covered and dangling segments), each with the findings expected,
   and a clean board that must report nothing. The test reads the output and asserts the findings, not just the exit
   code.
6. **`pcbkit fab` on a fixture:** the BOM and CPL list the same designators, rotations come from the corrections
   table, the Gerber zip holds the expected layers, and `fab_fingerprint` is the same on two runs. Release decides a
   board changed by that fingerprint, so a run-to-run difference would bump versions for nothing.
7. **DRC, ERC and `pcbkit check` on rgb-spotlight's two boards**, the public corpus, with the counts pinned.

These run in `kicad/kicad:10.0.6-full`, the Mac's patch release, moved up together with it.

Local, before each tag:

8. **Corpus regression:** a script that runs `drc`, `check`, `fab` and `place-score` on every corpus board with the
   last release and the working tree, and diffs the results. It is the old-against-new method already used by hand,
   written down and added to the release steps in AGENTS.md. A self-hosted runner on this Mac could run it, but
   pcb-kit is public and a runner there would run code from pull requests, so it stays a manual step.

Fingerprints from Linux `kicad-cli` may not match macOS ones byte for byte, so releases stay on the Mac; CI checks,
it doesn't release.

### A reusable workflow for the projects

pcb-kit publishes one reusable workflow (or composite action) that a project calls in a few lines, pinned to a
pcb-kit tag, and runs on pull requests that touch `boards/`, `lib/` or `scripts/`:

Files only:

- **Release folders are never rewritten:** fail if a PR modifies or deletes anything under a released `v*/` folder.
  CONVENTIONS.md states the rule; `git diff --name-status` checks it.
- **Documents:** a dated status line under each document's title; decision numbers (D1, D2, ...) unique and in order.
- **Library paths relative** (true today in all six projects; this keeps it so).
- **BOM completeness** from the schematic fields: every placed part has an LCSC number or is DNP or excluded from the
  BOM, and every symbol has a footprint.
- **kicad-happy, advisory** (`continue-on-error`), with `diff-base: true`, on one project first.

With KiCad 10:

- `pcbkit erc`; `pcbkit drc` with parity on the board as committed; zones up to date (refill and compare, since the
  projects keep their boards filled); `pcbkit check`; `pcbkit cleanup --dry-run` reporting nothing; the project's
  netlist assertions; `pcbkit production --check`; `pcbkit release --dry-run`, which says which boards changed.
- **Warnings with reasons in data:** today LAYOUT.md explains each DRC warning in prose. A `[checks]` table in
  `pcbkit.toml` listing each accepted warning (type, items, reason) lets CI fail on a new one, as pcb-inspector's
  per-rule overrides and kicad-happy's `.kicad-happy.json` do.
- **What changed against the base branch:** `sksat/kicadiff` (pinned) for new ERC and DRC violations and a visual
  diff, rather than writing our own. Our own short comment adds what only pcbkit knows: `check` findings, BOM rows,
  nets added or removed, which boards `release --dry-run` would bump. The comment is posted from a separate
  `workflow_run` job, as kicad-happy and OpenHornet do.
- **KLC checkers on `lib/`**, advisory, with the naming and 3-D path rules turned off.

Agent review, on request (a label or an `@claude` comment), not on every push:

- `claude-code-action` with this plugin installed, running the visual-review or failure-review skill against the
  KiCad tier's report rather than re-running it. kicad-happy's estimate of $1-15 per PR is the order of cost.

## New checks to write

In `pcbkit check`, or a new `pcbkit lint`, reading the board, the schematic and `pcbkit.toml`:

| Check | From | Needs |
| --- | --- | --- |
| A test pad on every power rail (a TP footprint or a pad marked as a test point) | pcb-checklist; failure-review's "Measure" | pcbnew |
| Exposed and thermal pads on a net | pcb-checklist | pcbnew |
| ESD part on every signal net that reaches a connector | kicad-happy `audit_esd_protection`; siebenuhr's unprotected CC, VBUS and LED data | netlist |
| Pull-ups on open-drain buses; strap and enable pins at a defined level | kicad-happy `validate_pullups`; failure-review's "Pins with a role" | netlist |
| Decoupling distance per IC supply pin, other side counted | pcb-inspector `HEUR-DEC-001` | pcbnew |
| Ground reference under signal tracks on the adjacent layer | pcb-inspector `HEUR-GND-001` | pcbnew |
| Board design rules at or above the fab's capability (track, gap, drill, annular ring, hole spacing, silk) | pcb-checklist "within manufacturer's capability" | `.kicad_pro`, a fab profile in `pcbkit/data/` |
| Bottom silkscreen text mirrored; the version label on the silkscreen matches releases.toml | pcb-checklist; `pcbkit release` stamps | pcbnew |
| Capacitor voltage rating against the rail it sits on | pcb-checklist; kicad-happy derating | a voltage field on capacitors |
| Every polarised placed part listed in `PLACEMENT-<board>.md` | the fab-order skill | fab output |
| BOM and CPL list the same designators, with matching quantities | nothing maintained does it | fab output |

## Decisions for you

- **How to run KiCad in CI:** the Linux container (needs the `kicad.py` change, free on public repos, uses Actions
  minutes on private ones) or a self-hosted runner on this Mac (runs pcbkit as it is, but only safe for private repos).
- **Whether projects get CI at all**, or only pcb-kit; the projects are where the checks would catch board mistakes.
- **Whether to trial kicad-happy** as an advisory check on one project.
- **When to start on KiCad 11's IPC API.** Not a CI question, but the fixture-board tests are what would make that
  move safe.

## Suggested order

1. pcb-kit, files-only job: imports under 3.9, compile, release check, skill lint, `--help` smoke. Fix `silk.py`
   (the stale report in `drc` and `erc` is fixed).
2. A KiCad 10 job running DRC, ERC and `check` on rgb-spotlight's boards (the `kicad.py` Linux paths are in; the
   container works locally), with `check` given an exit status.
3. Fixture boards for `check` and `cleanup`, and the `fab` consistency and determinism test.
4. The corpus regression script, added to the release steps.
5. The reusable project workflow, tried on rgb-spotlight; kicad-happy beside it as advisory.
6. The new checks, starting with test pads on power rails and exposed pads on a net.
7. Agent review on request.

## Sources

Repositories reviewed (cloned 2026-10-30):

- [aklofas/kicad-happy](https://github.com/aklofas/kicad-happy) v2.3.2: `github-action.md`, `action.yml`,
  `action/entrypoint.sh`, `action/examples/`, `.github/workflows/ci.yml`, `VALIDATION.md`,
  `skills/kicad/scripts/fab_release_gate.py`. MIT.
- [takzen/pcb-inspector](https://github.com/takzen/pcb-inspector) v0.1.0: README, `.github/actions/pcb-inspector/action.yml`,
  `.github/workflows/pcb-inspector.yml`, `rules.yaml`, `tests/test_golden_samples.py`. MIT.
- [azonenberg/pcb-checklist](https://github.com/azonenberg/pcb-checklist): both checklists. BSD-3.

From the homelib library (55 searches; the library has little on KiCad CI, and nothing on KiBot, KiRI or
pcb-inspector):

- `aklofas-kicad-happy-b889b78f`: kicad-happy.
- `faradworks-pinscope-a70fe7b1`: [Pinscope](https://github.com/Faradworks/Pinscope), LLM schematic review against
  datasheets with page citations; BOM roll-up and capacitor derating computed without the model.
- `reddit-the-front-page-of-the-internet-81d8e1d1`:
  [r/PrintedCircuitBoard schematic review tips](https://www.reddit.com/r/PrintedCircuitBoard/wiki/schematic_review_tips).
- `https-github-com-snhobbs-kicad-testpoints-pcm-6d326cab`: [kicad-testpoints-pcm](https://github.com/snhobbs/kicad-testpoints-pcm),
  test-point reports from pads marked "test point" in KiCad, which the test-pad check can use.
- Already in use: `drandyhaas-kicadroutingtools-bf029fb7` (KiCadRoutingTools' checkers),
  `bouni-kicad-jlcpcb-tools-b793740f` (the rotation table).

From the web survey (primary sources where possible; the two most consequential claims, the KiCad 11 SWIG removal and
the image tags and sizes, were checked again directly):

- KiCad images: [Docker Hub tags](https://hub.docker.com/r/kicad/kicad/tags),
  [kicad-cli-docker](https://gitlab.com/kicad/kicad-ci/kicad-cli-docker) (`Dockerfile.10.0-stable`),
  [kicad.org/download/docker](https://www.kicad.org/download/docker/);
  [PPA](https://launchpad.net/~kicad/+archive/ubuntu/kicad-10.0-releases).
- `kicad-cli`: [10.0 CLI docs](https://docs.kicad.org/10.0/en/cli/cli.html); `include/cli/exit_codes.h` and
  `resources/schemas/drc.v1.json` in [kicad-source-mirror](https://github.com/KiCad/kicad-source-mirror) (10.0);
  release notes for [10.0.4](https://www.kicad.org/blog/2026/06/KiCad-10.0.4-Release/),
  [10.0.5](https://www.kicad.org/blog/2026/07/KiCad-10.0.5-Release/),
  [10.0.7](https://www.kicad.org/blog/2026/10/KiCad-10.0.7-Release/); the report-file gotcha in
  [rjwalters/kicad-tools#6724](https://github.com/rjwalters/kicad-tools/issues/6724).
- SWIG removal and the IPC API: [pcbnew bindings](https://dev-docs.kicad.org/en/apis-and-binding/pcbnew/),
  [IPC API for add-on developers](https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/for-addon-developers/).
- Diffs: [sksat/kicadiff](https://github.com/sksat/kicadiff),
  [Cameronrlewis/kicad-review](https://github.com/Cameronrlewis/kicad-review),
  [leoheck/kiri#142](https://github.com/leoheck/kiri/issues/142).
- KiBot: [v1.9.1](https://github.com/INTI-CMNB/KiBot/releases/tag/v1.9.1),
  [preflights](https://kibot.readthedocs.io/en/latest/configuration.html).
- Library lint: [kicad-library-tools](https://gitlab.com/kicad/libraries/kicad-library-tools).
- Workflows from projects: [OpenHornet ecad-review.yml](https://github.com/jrsteensen/OpenHornet/blob/master/.github/workflows/ecad-review.yml),
  [open-bt-rig-interface kicad-checks.yml](https://github.com/Reid-n0rc/open-bt-rig-interface/blob/dev/.github/workflows/kicad-checks.yml),
  [KDT_Hierarchical_KiBot ci.yaml](https://github.com/nguyen-v/KDT_Hierarchical_KiBot/blob/main/.github/workflows/ci.yaml).
- Parsers: [kiutils#113](https://github.com/mvnmgrx/kiutils/issues/113) (no KiCad 8+),
  [gerbonara](https://gerbolyze.gitlab.io/gerbonara/).
- Pinscope is AGPL-3.0, takes PADS or EDIF netlists and has a web UI but no CLI or Action, so it isn't a CI tool yet.
