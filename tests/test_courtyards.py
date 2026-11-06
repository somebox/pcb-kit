"""`pcbkit courtyards`: courtyards that miss a footprint's pads or body, or sit on the other side, and the redraw.

  python3 tests/test_courtyards.py     # re-runs itself under KiCad's Python when pcbnew is not importable

Each case draws a footprint in pcbnew the way KiCad's EasyEDA importer left one on the ESP Candle base (2026-11):
a header's courtyard turned against its pins, a connector's drawn inside its silk body, a back-side test pad's on
F.CrtYd; and the way KiCad's own libraries draw them, which must pass.
"""
import math
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

from pcbkit.checks import courtyards as cy  # noqa: E402


@unittest.skipUnless(kicad.have_pcbnew(), "needs KiCad's Python")
class Courtyards(unittest.TestCase):
    def setUp(self):
        import pcbnew
        self.pcbnew = pcbnew
        self.board = pcbnew.BOARD()

    def mm(self, x, y):
        return self.pcbnew.VECTOR2I(self.pcbnew.FromMM(x), self.pcbnew.FromMM(y))

    def footprint(self, pads, kind="smd", size=(1.0, 1.0), side="F"):
        """A footprint at the origin, unrotated: pads [(x, y)] in mm, numbered from 1."""
        pcbnew = self.pcbnew
        fp = pcbnew.FOOTPRINT(self.board)
        fp.SetReference("U1")
        self.board.Add(fp)
        for i, (x, y) in enumerate(pads, 1):
            p = pcbnew.PAD(fp)
            p.SetNumber(str(i))
            if kind == "smd":
                p.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
                ls = pcbnew.LSET()
                for layer in ((pcbnew.F_Cu, pcbnew.F_Mask, pcbnew.F_Paste) if side == "F"
                              else (pcbnew.B_Cu, pcbnew.B_Mask, pcbnew.B_Paste)):
                    ls.AddLayer(layer)
                p.SetLayerSet(ls)
            else:
                p.SetAttribute(pcbnew.PAD_ATTRIB_PTH)
                p.SetLayerSet(pcbnew.PAD.PTHMask())
                p.SetDrillSize(self.mm(0.8, 0.8))
            p.SetShape(pcbnew.F_Cu, pcbnew.PAD_SHAPE_RECTANGLE)
            p.SetSize(pcbnew.F_Cu, self.mm(*size))
            p.SetPosition(self.mm(x, y))
            fp.Add(p)
        return fp

    def rect(self, fp, layer, x0, y0, x1, y1):
        """A closed outline as four lines, the way KiCad's libraries and importers draw a courtyard or a body."""
        pcbnew = self.pcbnew
        pts = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        for (ax, ay), (bx, by) in zip(pts, pts[1:] + pts[:1]):
            s = pcbnew.PCB_SHAPE(fp, pcbnew.SHAPE_T_SEGMENT)
            s.SetStart(self.mm(ax, ay))
            s.SetEnd(self.mm(bx, by))
            s.SetLayer(layer)
            s.SetWidth(self.pcbnew.FromMM(0.05))
            fp.Add(s)

    def header(self):
        """A 1x4 header on 2.00 mm, along x, with its silk body."""
        fp = self.footprint([(-3, 0), (-1, 0), (1, 0), (3, 0)], kind="pth", size=(1.6, 1.6))
        self.rect(fp, self.pcbnew.F_SilkS, -4.1, -1.1, 4.1, 1.1)
        return fp

    def courtyard_shapes(self, fp):
        return [g for g in fp.GraphicalItems()
                if g.GetLayer() in (self.pcbnew.F_CrtYd, self.pcbnew.B_CrtYd)]

    def test_a_library_style_footprint_passes(self):
        pcbnew = self.pcbnew
        fp = self.footprint([(-0.95, 1.1), (0, 1.1), (0.95, 1.1), (-0.95, -1.1), (0.95, -1.1)], size=(0.6, 1.1))
        self.rect(fp, pcbnew.F_Fab, -1.45, -0.8, 1.45, 0.8)
        self.rect(fp, pcbnew.F_CrtYd, -1.7, -1.9, 1.7, 1.9)
        # a pin-1 marker in silk stands off the body and out of the courtyard, as SOT-23's does: not the body
        self.rect(fp, pcbnew.F_SilkS, -2.2, 1.0, -1.9, 1.3)
        self.assertEqual(cy.findings(pcbnew, fp), [])

    def test_a_courtyard_turned_against_the_pins(self):
        fp = self.header()
        self.rect(fp, self.pcbnew.F_CrtYd, -1.3, -4.3, 1.3, 4.3)  # H3 on the ESP Candle base
        self.assertEqual(cy.findings(self.pcbnew, fp), ["pads 1, 2, 3, 4 outside it by up to 2.50 mm",
                                                        "silk outline outside it by up to 2.80 mm"])

    def test_a_courtyard_inside_the_silk_body(self):
        fp = self.footprint([(-1, 0), (1, 0)], kind="pth", size=(1.6, 1.6))
        self.rect(fp, self.pcbnew.F_SilkS, -3.0, -2.9, 3.0, 1.8)  # HDGC2001WV-2P: body 6.0 x 4.7
        self.rect(fp, self.pcbnew.F_CrtYd, -1.9, -1.0, 1.9, 1.0)
        self.assertEqual(cy.findings(self.pcbnew, fp), ["silk outline outside it by up to 2.20 mm"])  # corner to corner

    def test_silk_counts_as_the_body_only_beyond_half_a_millimetre(self):
        fp = self.footprint([(-1, 0), (1, 0)])
        self.rect(fp, self.pcbnew.F_SilkS, -1.8, -0.8, 1.8, 0.8)
        self.rect(fp, self.pcbnew.F_CrtYd, -1.6, -0.75, 1.6, 0.75)  # an EasyEDA pin-1 dot: 0.2 mm out
        self.assertEqual(cy.findings(self.pcbnew, fp), [])

    def test_a_silk_dot_off_the_body_is_a_mark(self):
        pcbnew = self.pcbnew
        fp = self.footprint([(-1, 0), (1, 0)])
        self.rect(fp, pcbnew.F_SilkS, -1.8, -0.8, 1.8, 0.8)
        self.rect(fp, pcbnew.F_CrtYd, -2.05, -1.05, 2.05, 1.05)
        dot = pcbnew.PCB_SHAPE(fp, pcbnew.SHAPE_T_CIRCLE)  # ESP32-S3-MINI-1's pin-1 dot, 0.5 mm off its outline
        dot.SetCenter(self.mm(-3.0, 0))
        dot.SetEnd(self.mm(-2.85, 0))
        dot.SetLayer(pcbnew.F_SilkS)
        fp.Add(dot)
        self.assertEqual(cy.findings(pcbnew, fp), [])
        self.assertEqual(cy.redraw(pcbnew, fp), "4.10 x 2.10 mm on F.CrtYd")  # the outline + 0.25, not the dot

    def test_a_back_pad_with_its_courtyard_on_the_front(self):
        fp = self.footprint([(0, 0)], side="B")
        self.rect(fp, self.pcbnew.F_CrtYd, -0.27, -0.27, 0.27, 0.27)  # TP1-TP11 on the ESP Candle base
        self.assertEqual(cy.findings(self.pcbnew, fp), ["courtyard on F.CrtYd, pads on B.Cu",
                                                        "pad 1 outside it by up to 0.33 mm"])  # its corner

    def test_no_courtyard_unless_board_only(self):
        fp = self.footprint([(0, 0)])
        self.assertEqual(cy.findings(self.pcbnew, fp), ["no courtyard"])
        fp.SetBoardOnly(True)  # a loose EasyEDA pad, out of the BOM and the placement files
        self.assertEqual(cy.findings(self.pcbnew, fp), [])

    def test_an_open_courtyard(self):
        fp = self.footprint([(0, 0)])
        self.rect(fp, self.pcbnew.F_CrtYd, -1, -1, 1, 1)
        fp.Remove(self.courtyard_shapes(fp)[0])
        self.assertEqual(cy.findings(self.pcbnew, fp), ["F.CrtYd is not a closed outline"])

    def test_redraw_round_pads_and_body_in_the_footprints_frame(self):
        pcbnew = self.pcbnew
        fp = self.header()
        self.rect(fp, pcbnew.F_CrtYd, -1.3, -4.3, 1.3, 4.3)
        fp.SetPosition(self.mm(100, 50))
        fp.SetOrientationDegrees(74)  # as H1 on the ESP Candle base
        self.assertEqual(cy.redraw(pcbnew, fp), "8.70 x 2.70 mm on F.CrtYd")  # silk 8.2 x 2.2, + 0.25 each side
        self.assertEqual(cy.findings(pcbnew, fp), [])
        self.assertEqual(len(self.courtyard_shapes(fp)), 1)

    def test_redraw_puts_a_back_pads_courtyard_on_the_back(self):
        pcbnew = self.pcbnew
        fp = self.footprint([(0, 0)], side="B")
        self.rect(fp, pcbnew.F_CrtYd, -0.27, -0.27, 0.27, 0.27)
        self.assertEqual(cy.redraw(pcbnew, fp), "1.50 x 1.50 mm on B.CrtYd")
        self.assertEqual([g.GetLayer() for g in self.courtyard_shapes(fp)], [pcbnew.B_CrtYd])
        self.assertEqual(cy.findings(pcbnew, fp), [])

    def test_redraw_a_flipped_rotated_part_and_save_it(self):
        pcbnew = self.pcbnew
        fp = self.footprint([(-1, 0), (1, 0)], size=(1.0, 1.2))
        self.rect(fp, pcbnew.F_CrtYd, -0.7, -2.6, 0.7, 2.6)  # C4 on the ESP Candle base: turned and too long
        fp.SetPosition(self.mm(20, 30))
        fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
        fp.SetOrientationDegrees(-124)
        self.assertTrue(cy.findings(pcbnew, fp))
        self.assertEqual(cy.redraw(pcbnew, fp), "3.50 x 1.70 mm on B.CrtYd")
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.kicad_pcb")
            kicad.save_board(self.board, path)
            b = pcbnew.LoadBoard(path)
            (back,) = list(b.GetFootprints())
            self.assertEqual(cy.findings(pcbnew, back), [])
            self.assertEqual(cy.board_findings(pcbnew, b), [])

    def poly(self, fp, layer, pts):
        """A closed outline as one polygon, pts in mm."""
        pcbnew = self.pcbnew
        s = pcbnew.PCB_SHAPE(fp, pcbnew.SHAPE_T_POLY)
        s.SetPolyPoints(pcbnew.VECTOR_VECTOR2I([self.mm(x, y) for x, y in pts]))
        s.SetLayer(layer)
        s.SetWidth(pcbnew.FromMM(0.05))
        fp.Add(s)
        return s

    def extent(self, pts):
        T = self.pcbnew.ToMM
        return (T(min(p.x for p in pts)), T(min(p.y for p in pts)), T(max(p.x for p in pts)), T(max(p.y for p in pts)))

    def test_an_arc_reaches_its_extremes(self):
        pcbnew = self.pcbnew
        fp = self.footprint([(0, 0)])
        arc = pcbnew.PCB_SHAPE(fp, pcbnew.SHAPE_T_ARC)  # radius 20 from 11.25 deg through 180 deg
        at = lambda deg: self.mm(20 * math.cos(math.radians(deg)), 20 * math.sin(math.radians(deg)))
        arc.SetArcGeometry(at(11.25), at(101.25), at(191.25))
        x0, y0, x1, y1 = self.extent(cy.shape_points(pcbnew, arc, pcbnew.FromMM(cy.STEP)))
        self.assertAlmostEqual(x0, -20, places=4)  # 180 deg, between samples
        self.assertAlmostEqual(y1, 20, places=4)   # 90 deg
        self.assertAlmostEqual(y0, 20 * math.sin(math.radians(-11.25)), places=4)

    def test_a_bezier_is_sampled_on_the_curve(self):
        pcbnew = self.pcbnew
        fp = self.footprint([(0, 0)])
        b = pcbnew.PCB_SHAPE(fp, pcbnew.SHAPE_T_BEZIER)
        b.SetStart(self.mm(0, 0))
        b.SetBezierC1(self.mm(3, 5))
        b.SetBezierC2(self.mm(6, -5))
        b.SetEnd(self.mm(9, 0))
        _, y0, _, y1 = self.extent(cy.shape_points(pcbnew, b, pcbnew.FromMM(cy.STEP)))
        self.assertAlmostEqual(y1, 5 * math.sqrt(3) / 6, places=2)  # 1.44 mm, not the control points' 5
        self.assertAlmostEqual(y0, -5 * math.sqrt(3) / 6, places=2)

    def test_a_through_hole_pads_back_copper_counts(self):
        pcbnew = self.pcbnew
        fp = self.footprint([(0, 0)], kind="pth", size=(1.6, 1.6))
        (p,) = list(fp.Pads())
        p.Padstack().SetMode(pcbnew.PADSTACK.MODE_FRONT_INNER_BACK)
        p.SetShape(pcbnew.B_Cu, pcbnew.PAD_SHAPE_CIRCLE)
        p.SetSize(pcbnew.B_Cu, self.mm(3.0, 3.0))
        self.rect(fp, pcbnew.F_CrtYd, -1.1, -1.1, 1.1, 1.1)  # round the front copper only
        self.assertEqual(cy.findings(pcbnew, fp), ["pad 1 outside it by up to 0.40 mm"])
        self.assertEqual(cy.redraw(pcbnew, fp), "3.50 x 3.50 mm on F.CrtYd")

    def test_a_rotated_slot_is_a_capsule_along_its_length(self):
        pcbnew = self.pcbnew
        fp = self.footprint([])
        p = pcbnew.PAD(fp)
        p.SetAttribute(pcbnew.PAD_ATTRIB_NPTH)
        p.SetLayerSet(pcbnew.PAD.UnplatedHoleMask())
        p.SetDrillShape(pcbnew.PAD_DRILL_SHAPE_OBLONG)
        p.SetDrillSize(self.mm(4.0, 1.0))
        p.SetShape(pcbnew.F_Cu, pcbnew.PAD_SHAPE_OVAL)
        p.SetSize(pcbnew.F_Cu, self.mm(4.0, 1.0))
        p.SetOrientationDegrees(90)  # its length along y
        fp.Add(p)
        self.rect(fp, pcbnew.F_CrtYd, -0.75, -2.25, 0.75, 2.25)
        self.assertEqual(cy.findings(pcbnew, fp), [])
        self.assertEqual(cy.redraw(pcbnew, fp), "1.50 x 4.50 mm on F.CrtYd")

    def test_a_pad_bridging_a_notch_in_the_courtyard(self):
        pcbnew = self.pcbnew
        fp = self.footprint([(0, 0)], size=(3.0, 1.0))
        # its four corners are inside; a notch 1 mm wide cuts into it from above, to the pad's middle
        self.poly(fp, pcbnew.F_CrtYd, [(-2, -1), (-0.5, -1), (-0.5, 0), (0.5, 0), (0.5, -1), (2, -1), (2, 1), (-2, 1)])
        # KiCad builds the courtyard 0.005 mm off the drawn line: 0.505 mm
        self.assertRegex("; ".join(cy.findings(pcbnew, fp)), r"^pad 1 outside it by up to 0\.5[01] mm$")

    def test_a_back_land_under_the_front_courtyard_passes(self):
        pcbnew = self.pcbnew
        fp = self.footprint([(-1, 0), (1, 0)])
        land = pcbnew.PAD(fp)  # the thermal land on the back of KiCad's ThermalVias packages: no B.CrtYd there
        land.SetNumber("3")
        land.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
        layers = pcbnew.LSET()
        layers.AddLayer(pcbnew.B_Cu)
        land.SetLayerSet(layers)
        land.SetSize(pcbnew.B_Cu, self.mm(1.0, 0.8))
        fp.Add(land)
        self.rect(fp, pcbnew.F_CrtYd, -1.75, -0.75, 1.75, 0.75)
        self.assertEqual(cy.findings(pcbnew, fp), [])

    def test_pads_on_both_sides_keep_a_courtyard_on_each(self):
        pcbnew = self.pcbnew
        fp = self.footprint([(-1, 0), (1, 0)])
        back = pcbnew.PAD(fp)
        back.SetNumber("3")
        back.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
        layers = pcbnew.LSET()
        layers.AddLayer(pcbnew.B_Cu)
        back.SetLayerSet(layers)
        back.SetSize(pcbnew.B_Cu, self.mm(1.0, 1.0))
        back.SetPosition(self.mm(0, 3))
        fp.Add(back)
        self.rect(fp, pcbnew.F_CrtYd, -1.75, -0.75, 1.75, 0.75)
        self.assertEqual(cy.findings(pcbnew, fp), ["pad 3 outside it by up to 2.75 mm"])  # no B.CrtYd: against F's
        self.assertEqual(cy.redraw(pcbnew, fp), "3.50 x 1.50 mm on F.CrtYd, 1.50 x 1.50 mm on B.CrtYd")
        self.assertEqual(sorted(g.GetLayer() for g in self.courtyard_shapes(fp)), sorted([pcbnew.F_CrtYd, pcbnew.B_CrtYd]))
        self.assertEqual(cy.findings(pcbnew, fp), [])


if __name__ == "__main__":
    unittest.main()
