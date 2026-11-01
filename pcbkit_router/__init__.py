"""The router's own code, which runs under the router's Python: numba, numpy, scipy and pillow, on Python 3.12, installed by
`pcbkit setup-router`. Each step is a module run with `python -m pcbkit_router.<step>`, and `pcbkit reroute` runs them in
order. Nothing here imports pcbnew or pcbkit: all a board has to say reaches it as pcbkit.reroute.geom's JSON. The flow and
the KiCad side are in pcbkit/reroute/, and docs/ROUTER.md has the method."""
import os
from pathlib import Path

WORK = Path(os.environ.get("REROUTE_WORK", "build/reroute"))
