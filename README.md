# pcb-kit

Shared tooling for the somebox KiCad hardware projects: the scripts that every board needs, kept once instead of
copied into each repo. Engine here, data in the project: a board's placement tables, net lists, power-stage
geometry and documents stay in its own repo, and call these commands.

Three parts:

- **`pcbkit`**, a Python package with a CLI (checks, release files, net-list helpers, a schematic builder for konnect).
- **`skills/`**, agent skills in SKILL.md form, for Claude Code and pi.
- **`templates/`** and **`library/`**, the project skeleton, conventions and trusted parts.

## Install

```bash
uv tool install -e ~/src/pcb-kit     # gives you `pcbkit`
pcbkit info                          # confirms where KiCad is
pcbkit setup-kikit                   # only for panels: pins KiKit into ~/.local/share/pcbkit
```

Two interpreters are involved. `pcbkit` runs in uv's Python; the commands that need `pcbnew` re-execute themselves
under KiCad's bundled Python (3.9 on macOS), so the package code stays 3.9-compatible and dependency-free. Set
`PCBKIT_KICAD_APP` if KiCad is not at `/Applications/KiCad/KiCad.app`. Previews need `rsvg-convert` (librsvg),
`pdfunite` (poppler) and optionally `magick` (ImageMagick).

## Commands

| Command | What it does |
| --- | --- |
| `pcbkit drc BOARD [--refill] [--save] [--full]` | DRC through kicad-cli: counts by type, every error, unconnected items by net and footprint. `--save` refills and writes the board back, which is how the projects keep their boards filled. Exit 1 on errors or unconnected items |
| `pcbkit erc SCH` | ERC summary, exit 1 on errors |
| `pcbkit nets SCH [NET ...]` | net sizes and members; `pcbkit.checks.netlist.Netlist` is the base for a project's topology assertions |
| `pcbkit check BOARD` | what DRC doesn't flag: floating copper, stubs with their full length, loops, pours reaching one pad, power pads fed only through thin track |
| `pcbkit cleanup BOARD [--dry-run]` | duplicate, zero-length and covered segments removed, collinear runs merged; edits the file text by uuid |
| `pcbkit previews BOARD OUT` | schematic PDF, copper PNGs front and back, hand-assembly map, 1:1 printable PDF, plane views on 4-layer boards, 3-D renders |
| `pcbkit print BOARD OUT.pdf` | the 1:1 printable PDF alone |
| `pcbkit fab BOARD OUT` | JLC Gerber zip, BOM and CPL (front-only and both-sides variants), rotations corrected from the kicad-jlcpcb-tools table |
| `pcbkit panel BOARD OUT [--style vcut\|mousebite\|both] [--rows 3] [--previews DIR]` | KiKit assembly panels with rails, tooling holes, fiducials, order-number placeholder, unique per-copy references; DRC on each |
| `pcbkit jlc --lcsc C25744` | SQL search over konnect's JLCPCB parts cache |
| `pcbkit netview BOARD OUT.svg X0 Y0 X1 Y1 [--layer] [--png]` | a region of one layer coloured by net, for reviewing power stages |

Library use from a project script:

```python
from pcbkit import schematic as sch          # schematic sheets from Python data, through konnect
from pcbkit.konnect import Konnect           # the MCP client itself
from pcbkit.parts import resistor, capacitor # stock R/C with LCSC numbers
from pcbkit.silk import Placer               # silkscreen label placement around an anchor box
from pcbkit.checks.netlist import Netlist    # netlist topology assertions
```

## Repository layout

```
pcbkit/            the package (checks/, release/, schematic.py, konnect.py, parts.py, silk.py, netview.py)
pcbkit/data/       jlc/corrections.csv (rotations), panel/ (KiKit presets, tabs plugin, post script), templates/blank.kicad_pcb
skills/            SKILL.md folders, one per workflow (Claude Code plugin manifest in .claude-plugin/)
templates/project  README/DESIGN/LAYOUT/PRODUCTION skeletons and .gitignore for a new hardware repo
library/           shared symbols, footprints and design-pattern blocks (empty until the first ones are harvested)
docs/              CONVENTIONS.md (how a project is laid out and documented), HARVEST.md (where each piece came from)
```

## Using it from an agent

- **Claude Code:** `claude --plugin-dir ~/src/pcb-kit` or add this folder to a local marketplace; the skills in `skills/`
  are then invocable. A project can also symlink `skills/pcb-kit` into its own `.claude/skills/`.
- **pi:** `pi install /Users/foz/src/pcb-kit` (the `package.json` here declares the skills), or `pi --skill ~/src/pcb-kit/skills/pcb-kit`.
- **KiCad:** nothing to install; the commands drive `kicad-cli` and KiCad's Python, and konnect (the KiCad MCP server) is
  used from both agents and `pcbkit.schematic`.

## Status

Harvested on 2026-10-10 from the rp2354-messages-sign, siebenuhr controller-v2, rgb-spotlight and dodeca-v2-hardware
repos; see [docs/HARVEST.md](docs/HARVEST.md) for the provenance of each piece and what is still in the projects.
Nothing has been migrated yet: the projects still run their own copies until each one is switched over
(siebenuhr first).
