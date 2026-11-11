"""Fixture boards for `pcbkit check`, built in pcbnew.

  python3 tests/test_route_check.py     # re-runs itself under KiCad's Python when pcbnew is not importable

Each case draws a small two-layer board, saves it with a .kicad_pro beside it, and asserts the findings.
"""
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pcbkit import kicad  # noqa: E402

if __name__ == "__main__" and not kicad.have_pcbnew() and not os.environ.get("PCBKIT_REEXEC"):
    env = dict(os.environ, PCBKIT_REEXEC="1")
    env["PYTHONPATH"] = ROOT + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    os.execve(kicad.KICAD_PYTHON, [kicad.KICAD_PYTHON, os.path.abspath(__file__), *sys.argv[1:]], env)


@unittest.skipUnless(kicad.have_pcbnew(), "needs KiCad's Python")
class FloatingCopper(unittest.TestCase):
    def setUp(self):
        import pcbnew
        self.pcbnew = pcbnew
        self.dir = tempfile.TemporaryDirectory()
        self.board = pcbnew.BOARD()
        self.net = pcbnew.NETINFO_ITEM(self.board, "N1")
        self.board.Add(self.net)

    def tearDown(self):
        self.dir.cleanup()

    def pad(self, x, y):
        pcbnew = self.pcbnew
        fp = pcbnew.FOOTPRINT(self.board)
        fp.SetReference("P%d" % (len(self.board.GetFootprints()) + 1))
        p = pcbnew.PAD(fp)
        p.SetNumber("1")
        p.SetAttribute(pcbnew.PAD_ATTRIB_PTH)
        p.SetLayerSet(pcbnew.PAD.PTHMask())
        p.SetShape(pcbnew.PAD_SHAPE_CIRCLE)
        p.SetSize(pcbnew.VECTOR2I(pcbnew.FromMM(1.6), pcbnew.FromMM(1.6)))
        p.SetDrillSize(pcbnew.VECTOR2I(pcbnew.FromMM(0.8), pcbnew.FromMM(0.8)))
        fp.Add(p)
        fp.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y)))
        p.SetNet(self.net)
        self.board.Add(fp)

    def track(self, layer, x0, y0, x1, y1, width=1.0):
        pcbnew = self.pcbnew
        t = pcbnew.PCB_TRACK(self.board)
        t.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(x0), pcbnew.FromMM(y0)))
        t.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(x1), pcbnew.FromMM(y1)))
        t.SetWidth(pcbnew.FromMM(width))
        t.SetLayer(layer)
        t.SetNet(self.net)
        self.board.Add(t)

    def arc(self, layer, x0, y0, xm, ym, x1, y1, width=1.0):
        pcbnew = self.pcbnew
        a = pcbnew.PCB_ARC(self.board)
        a.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(x0), pcbnew.FromMM(y0)))
        a.SetMid(pcbnew.VECTOR2I(pcbnew.FromMM(xm), pcbnew.FromMM(ym)))
        a.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(x1), pcbnew.FromMM(y1)))
        a.SetWidth(pcbnew.FromMM(width))
        a.SetLayer(layer)
        a.SetNet(self.net)
        self.board.Add(a)

    def via(self, x, y, size=0.6):
        pcbnew = self.pcbnew
        v = pcbnew.PCB_VIA(self.board)
        v.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y)))
        v.SetWidth(pcbnew.FromMM(size))
        v.SetDrill(pcbnew.FromMM(0.3))
        v.SetNet(self.net)
        self.board.Add(v)

    def check(self):
        from pcbkit.checks import route_check
        path = os.path.join(self.dir.name, "fixture.kicad_pcb")
        self.board.Save(path)
        with open(os.path.join(self.dir.name, "fixture.kicad_pro"), "w") as fh:
            json.dump({"net_settings": {"classes": [{"name": "Default", "track_width": 0.25}]}}, fh)
        return route_check.check(path)

    def test_via_in_the_middle_of_tracks(self):
        """Vias stitched into straight runs on both layers, as a scripted wide feed lays them: none floats."""
        F, B = self.pcbnew.F_Cu, self.pcbnew.B_Cu
        self.pad(0, 0)
        self.pad(20, 0)
        self.track(F, 0, 0, 20, 0)
        self.via(5, 0)                   # mid-segment on F.Cu, where the B.Cu run starts and ends
        self.via(15, 0)
        self.track(B, 5, 0, 15, 0)
        self.via(10, 0.3)                # mid-segment on both layers, off the centre line, its ring on the 1 mm track
        report = self.check()
        self.assertEqual(report.get("floating", []), [])
        self.assertEqual(report.get("stubs", []), [])

    def test_via_beside_a_track_still_floats(self):
        """A via whose ring clears the track is not joined to it."""
        F = self.pcbnew.F_Cu
        self.pad(0, 0)
        self.pad(20, 0)
        self.track(F, 0, 0, 20, 0)
        self.via(10, 1.0)                # 0.5 mm half-width + 0.3 mm ring < 1.0 mm
        report = self.check()
        self.assertEqual(report.get("floating"), ["N1: 1 via(s) at (10.00, 1.00)"])

    def test_stub_from_a_mid_segment_via(self):
        """A dead end hanging off a mid-segment via is a stub, measured from the via, not floating copper."""
        F, B = self.pcbnew.F_Cu, self.pcbnew.B_Cu
        self.pad(0, 0)
        self.pad(20, 0)
        self.track(F, 0, 0, 20, 0)
        self.via(10, 0)
        self.track(B, 10, 0, 10, 4)
        report = self.check()
        self.assertEqual(report.get("floating", []), [])
        self.assertEqual(report.get("stubs"), ["N1: dead end at (10.00, 4.00) on B.Cu, 1 segment(s) 4.0 mm"])

    def test_vias_and_an_arc(self):
        """A via on an arc's curve is joined to it; one at the middle of its chord, 5 mm from any copper, floats."""
        F = self.pcbnew.F_Cu
        self.pad(0, 0)
        self.pad(10, 0)
        self.arc(F, 0, 0, 5, -5, 10, 0)          # a half circle round (5, 0)
        self.via(5, -5)
        self.via(5, 0)
        report = self.check()
        self.assertEqual(report.get("floating"), ["N1: 1 via(s) at (5.00, 0.00)"])

    def test_a_track_ending_on_an_arc(self):
        """A track that ends on an arc's curve is joined to it; one that ends on its chord touches nothing."""
        F = self.pcbnew.F_Cu
        self.pad(0, 0)
        self.pad(10, 0)
        self.arc(F, 0, 0, 5, -5, 10, 0)
        self.track(F, 5, -5, 5, -8)             # from the curve to a pad
        self.pad(5, -8)
        self.track(F, 5, 0, 5, 3)               # from the chord, touching nothing at (5, 0)
        report = self.check()
        self.assertEqual(report.get("floating"), ["N1: 1 track(s) 3.0 mm on ['F.Cu'] at (5.00, 0.00)"])
        self.assertFalse([x for x in report.get("stubs", []) if "(5.00, -5.00)" in x], report.get("stubs"))


if __name__ == "__main__":
    unittest.main()
