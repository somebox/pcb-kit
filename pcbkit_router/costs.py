"""The router's numbers in one place: the raster, the cost of each kind of step, and the negotiation's settings. Costs
are in millimetres of track. A job overrides them per net (its "defaults", a net's own entry) or per run ("neg", or
--key=value on nroute.py); docs/ROUTER.md says where each one came from."""

RES = 0.025                 # raster cell, mm
STEP = 2                    # lattice pitch in cells (0.05 mm)
UNIT = 1.0 / (RES * STEP)   # lattice units per mm
VIA_SIZE = (0.45, 0.25)     # diameter and drill, mm; a job's "via_size" replaces it
GROUND = "GND"              # a job's "ground" replaces it
REFS = dict(ic=["U"], mount=["H"], holes=["Hole"], test=["TP"])    # reference prefixes; a job's "refs" replaces each

DEF = dict(w=0.2, neck=0.16, clr=0.16, edge=0.3, hole_clr=0.25,
           via=4.0, via_s=1.5,          # via: mm of track, plus via_s x the jogging (mm) since the last via
           diag=1.2, diag_s=0.5,        # 45 degrees: (diag + diag_s * s) per mm, s = jogging since the last via
           wrong=1.5, wrong_s=0.8,      # against the layer's direction: (wrong + wrong_s * s) per mm
           bend=0.5, half=2.0,          # bend: mm per change of direction; s halves every `half` mm along the layer
           turn=3.0,                    # a 90-degree turn costs bend x turn, 135 degrees bend x turn^2 (1: all alike)
           budget_mm=8.0, retries=3,    # via budget: one per budget_mm of ideal length; re-routes with 2x via cost
           hw=1.0,                      # heuristic weight: above 1 the search gets greedy and B.Cu runs wobble
           neck_r=0.5, free_r=0.9, margin=4.0, max_exp=40_000_000, mount_keep=2.8, layers="FB", via_gap=0.7,
           centre=3.0, via_pad_gap=0.1, own_via_gap=0.6,
           pin_via=15.0,                # a via inside an IC's pin area
           pin_site=1.5,                # less for a via in line with one of the net's pads, just past its end
           under=8.0,                   # x length, on the far side of an IC or under its belly; job.py sets 3 with inner layers
           join=1.0,                    # arriving on a track instead of on a pad or via
           entry=3.0,                   # x length, round a pad and off its centre lines
           neck_k=6.0,                  # x length of a step that has to be narrow
           neck_min=0.5)                # a narrow run is at least this long
TRUE = dict(wrong=1.0, wrong_s=0.0, diag=1.0, diag_s=0.0, via_s=0.0)    # true length: no layer direction, no jogging cost
PIN_MARGIN, HALO = 0.25, 0.4    # the pin area: the pads' box plus this; pad entry: this far round a pad
BELLY = 0.9                     # an IC's belly: its pads' box less this

NEG = dict(iters=45, pf0=0.5, pf_mul=1.5, hf=1.0,    # present cost pf0 x pf_mul^pass, history hf per overlap
           via_k=10.0,                  # a via's present cost, x a step's
           pour=5.0, pour_via=50.0, pour_clr=0.2,    # into a soft pour, per step and per via
           workers=10, stall=5,         # stalled: no tenth less overlap in `stall` passes
           widen=3, widen_mm=4.0,       # in conflict `widen` passes in a row: its window grows
           expand=3, expand_mm=3.0,     # at a stall, up to `expand` times, the nets this close to the overlaps go too
           fade=20,                     # the layers' directions fade to true length over this many passes
           refine=15, branches="7,10,13",     # refining: up to `refine` passes, from these passes' present cost
           shortcut=2, shortcut_min=1.0, shortcut_gain=0.5,
           retee=1, tee_join=4.0, tee_gain=2.0,
           smooth=1,
           detour=1, detour_free=4.0, detour_tries=3, detour_k=0.1)
