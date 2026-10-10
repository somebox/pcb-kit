#!/bin/bash
# Order files for one board: DRC gate, then Gerbers, BOM and CPL into build/fab/<board>/.
# Usage: scripts/make_fab.sh <board>
set -eo pipefail
cd "$(dirname "$0")/.."
b=${1:?usage: scripts/make_fab.sh <board>}
src=boards/$b/$b.kicad_pcb out=build/fab/$b
rm -rf "$out"; mkdir -p "$out"
pcbkit drc "$src" --refill --json "$out/drc.json" || { echo "not fabricating $b: fix DRC first" >&2; exit 1; }
pcbkit fab "$src" "$out/board"
