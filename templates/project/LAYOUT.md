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

Version and date: `${ISSUE_DATE} ${REVISION}` on the back, which KiCad fills from the title block when it plots the
Gerbers (pcbkit release stamps the title block).
