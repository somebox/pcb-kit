"""`pcbkit reroute`, the flow and the KiCad side of the negotiated router: flow.py runs the steps, and geom.py, job.py,
moves.py, apply.py and finish.py each run on their own under KiCad's Python, one board per process (a second LoadBoard
in a process comes back unusable). The router itself is pcbkit_router, under its own Python; docs/ROUTER.md has the method.
"""
