#!/bin/sh
# Regenerate docs/previews/<b>/ with `pcbkit previews`: schematic PDF, copper PNGs, assembly map, 1:1 board.pdf, renders.
# Usage: scripts/make_previews.sh   (NO_RENDER=1 skips the 3-D renders)
set -e
cd "$(dirname "$0")/.."
pcbkit previews boards/<b>/<b>.kicad_pcb docs/previews/<b> --zoom 1.0 ${NO_RENDER:+--no-render}
