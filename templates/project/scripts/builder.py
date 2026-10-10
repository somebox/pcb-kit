"""Project glue for pcbkit.schematic: sheet scripts do `from builder import *` and call build(), R(), C(), P2(), ...

This module configures pcbkit.schematic for boards/<b>/ and then becomes it (sys.modules swap), so `builder.P`
and `builder.POWER` are the engine's own. Add the project's power nets below (name -> (symbol, value, pin rotation)).
"""
import os
import sys

try:
    import pcbkit  # noqa: F401
except ImportError:  # an interpreter pcbkit is not linked into: use the checkout
    sys.path.insert(0, os.path.expanduser(os.environ.get("PCBKIT_ROOT", "~/src/pcb-kit")))
from pcbkit import schematic as _m

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/"
_m.configure(ROOT + "boards/<b>/", power={})
_m.ROOT = ROOT
sys.modules[__name__] = _m
