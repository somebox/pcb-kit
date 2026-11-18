# Routing a board by script: `pcbkit.route`

A project script under KiCad's Python. Worked example: esp32-s3-ha-node `scripts/route_pcb.py`.

- Fine-pitch chips (0.4 to 0.65 mm) first get `fanout(board, ref, P, area, track, via, skip_nets=(power and plane
  nets))`: staggered dog-bone vias out from each signal pad.
- Then `pcbkit.route.complete_nets(board, P, area, plan, track, via, outline=..)` with `plan = [(net regex,
  [layers], width)]` and `QUICK`, `CONN_SECONDS` and `DEADLINE` in `P` so a run has an end; then
  `snap_to_pad_centres`, `audit_track_ends`, `pcbkit cleanup --dangling`, `pcbkit drc --save`.
- With only the outer layers for tracks, `P["DIR"] = {F_Cu: "h", B_Cu: "v"}` holds each to one direction (`DIR_COST`
  along, across, diagonal; default 1, 4, 2) and `P["ORDER"] = "longest"` routes the longest connections first; both
  are off unless set.
