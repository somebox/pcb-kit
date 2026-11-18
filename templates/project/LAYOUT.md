# <Project> PCB layout

Status (<date>): <placed / routed / DRC state>.

## Floor plan

## Stackup and rules

## Scripted copper

## Routing

## Checks

```bash
pcbkit cleanup boards/<b>/<b>.kicad_pcb
pcbkit drc boards/<b>/<b>.kicad_pcb --save
pcbkit check boards/<b>/<b>.kicad_pcb
```

## Silkscreen

Name, version and date: `<Board> ${REVISION} ${ISSUE_DATE}` on the back. KiCad fills the variables from the title
block when it plots the Gerbers (`pcbkit release` stamps the title block).

What else the board carries: pcb-kit's `docs/CONVENTIONS.md`, "What a released board carries". Left out, and why:
<nothing>.
