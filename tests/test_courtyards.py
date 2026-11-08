"""`pcbkit courtyards`: courtyards that miss a footprint's pads or body, or sit on the other side, and the redraw.

  python3 tests/test_courtyards.py     # re-runs itself under KiCad's Python when pcbnew is not importable

Each case draws a footprint in pcbnew the way KiCad's EasyEDA importer left one on the ESP Candle base (2026-11):
a header's courtyard turned against its pins, a connector's drawn inside its silk body, a back-side test pad's on
F.CrtYd; and the way KiCad's own libraries draw them, which must pass.
"""
import json
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


class Board(unittest.TestCase):
    """Footprints drawn in pcbnew, on a board of their own."""
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


@unittest.skipUnless(kicad.have_pcbnew(), "needs KiCad's Python")
class Courtyards(Board):
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

        def part(courtyard):
            fp = self.footprint([(-1, 0), (1, 0)])
            self.rect(fp, pcbnew.F_SilkS, -1.8, -0.8, 1.8, 0.8)
            self.rect(fp, pcbnew.F_CrtYd, *courtyard)
            dot = pcbnew.PCB_SHAPE(fp, pcbnew.SHAPE_T_CIRCLE)  # ESP32-S3-MINI-1's pin-1 dot, 0.5 mm off its outline
            dot.SetCenter(self.mm(-3.0, 0))
            dot.SetEnd(self.mm(-2.85, 0))
            dot.SetLayer(pcbnew.F_SilkS)
            fp.Add(dot)
            return fp
        self.assertEqual(cy.findings(pcbnew, part((-2.05, -1.05, 2.05, 1.05))), [])  # the dot is no finding
        small = part((-1.0, -0.4, 1.0, 0.4))
        self.assertTrue(cy.findings(pcbnew, small))
        self.assertEqual(cy.redraw(pcbnew, small), "4.10 x 2.10 mm on F.CrtYd")  # the outline + 0.25, not the dot
        self.assertEqual(cy.findings(pcbnew, small), [])

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

    def test_a_negative_margin_is_refused(self):
        fp = self.footprint([(0, 0)])
        self.rect(fp, self.pcbnew.F_CrtYd, -0.2, -0.2, 0.2, 0.2)
        with self.assertRaises(ValueError):
            cy.redraw(self.pcbnew, fp, margin=-1.0)
        self.assertEqual(len(self.courtyard_shapes(fp)), 4)  # left as it was
        with self.assertRaises(SystemExit):
            cy.main(["board.kicad_pcb", "--fix", "--margin", "-1"])

    def test_redraw_puts_a_back_pads_courtyard_on_the_back(self):
        pcbnew = self.pcbnew
        fp = self.footprint([(0, 0)], side="B")
        self.rect(fp, pcbnew.F_CrtYd, -0.27, -0.27, 0.27, 0.27)
        self.assertEqual(cy.redraw(pcbnew, fp), "1.50 x 1.50 mm on B.CrtYd, F.CrtYd removed")
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
        pts = cy.shape_points(pcbnew, b, pcbnew.FromMM(cy.STEP))
        _, y0, _, y1 = self.extent(pts)
        self.assertAlmostEqual(y1, 5 * math.sqrt(3) / 6, places=2)  # 1.44 mm, not the control points' 5
        self.assertAlmostEqual(y0, -5 * math.sqrt(3) / 6, places=2)
        b.SetStart(self.mm(0, 0))  # fast at the start, still at the end: 0.15 mm apart if counted by its length
        b.SetBezierC1(self.mm(10, 0))
        b.SetBezierC2(self.mm(10, 0))
        b.SetEnd(self.mm(10, 0))
        for curve in (pts, cy.shape_points(pcbnew, b, pcbnew.FromMM(cy.STEP))):
            gaps = [math.hypot(q.x - p.x, q.y - p.y) for p, q in zip(curve, curve[1:])]
            self.assertLessEqual(max(gaps), pcbnew.FromMM(cy.STEP))  # evenly in t, never further apart than that

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

    def test_a_back_keep_out_hides_no_front_copper(self):
        pcbnew = self.pcbnew
        fp = self.header()
        self.rect(fp, pcbnew.F_CrtYd, -3.5, -1.35, 4.35, 1.35)  # misses pad 1's front copper by 0.3 mm
        self.rect(fp, pcbnew.B_CrtYd, -6, -3, 6, 3)  # a keep-out on the solder side, covering everything
        self.assertRegex("; ".join(cy.findings(pcbnew, fp)),
                         r"^pad 1 outside it by up to 0\.3\d mm; silk outline outside it by up to 0\.6\d mm$")

    def test_a_standoff_ring_needn_t_cover_the_pins(self):
        pcbnew = self.pcbnew
        fp = self.header()
        self.rect(fp, pcbnew.F_CrtYd, -4.35, -1.35, 4.35, 1.35)
        ring = pcbnew.PCB_SHAPE(fp, pcbnew.SHAPE_T_CIRCLE)  # round one pin only, on the back
        ring.SetCenter(self.mm(0, 3))
        ring.SetEnd(self.mm(1.5, 3))
        ring.SetLayer(pcbnew.B_CrtYd)
        ring.SetWidth(pcbnew.FromMM(0.05))
        fp.Add(ring)
        self.assertEqual(cy.findings(pcbnew, fp), [])

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
        self.rect(fp, pcbnew.F_CrtYd, -2.25, -0.75, 2.25, 0.75)  # drawn across it: right for the unrotated slot
        self.assertRegex("; ".join(cy.findings(pcbnew, fp)), r"^1 unnumbered hole outside it by up to 1\.2\d mm$")
        self.assertEqual(cy.redraw(pcbnew, fp), "1.50 x 4.50 mm on F.CrtYd")
        self.assertEqual(cy.findings(pcbnew, fp), [])

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

    def test_each_sides_body_against_its_own_courtyard(self):
        pcbnew = self.pcbnew
        fp = self.footprint([(-0.5, 0), (0.5, 0)], size=(0.5, 0.5))
        self.rect(fp, pcbnew.F_Fab, -2, -2, 2, 2)
        self.rect(fp, pcbnew.F_CrtYd, -1, -1, 1, 1)
        self.rect(fp, pcbnew.B_CrtYd, -3, -3, 3, 3)  # the back's would cover the front body
        self.assertRegex("; ".join(cy.findings(pcbnew, fp)), r"^fab outline outside it by up to 1\.4\d mm$")

    def test_a_back_pads_front_body_goes_in_its_redrawn_courtyard(self):
        pcbnew = self.pcbnew
        fp = self.footprint([(0, 0)], side="B")
        self.rect(fp, pcbnew.F_Fab, -2, -2, 2, 2)
        self.rect(fp, pcbnew.B_CrtYd, -0.27, -0.27, 0.27, 0.27)
        self.assertTrue(cy.findings(pcbnew, fp))
        self.assertEqual(cy.redraw(pcbnew, fp), "4.50 x 4.50 mm on B.CrtYd")
        self.assertEqual(cy.findings(pcbnew, fp), [])

    def test_a_through_hole_parts_keep_out_on_the_other_side_stays(self):
        pcbnew = self.pcbnew
        fp = self.header()
        self.rect(fp, pcbnew.F_CrtYd, -1.3, -4.3, 1.3, 4.3)  # wrong: turned against the pins
        ring = pcbnew.PCB_SHAPE(fp, pcbnew.SHAPE_T_CIRCLE)  # a standoff's keep-out on the solder side
        ring.SetCenter(self.mm(0, 0))
        ring.SetEnd(self.mm(6, 0))
        ring.SetLayer(pcbnew.B_CrtYd)
        ring.SetWidth(pcbnew.FromMM(0.05))
        fp.Add(ring)
        self.assertEqual(cy.redraw(pcbnew, fp), "8.70 x 2.70 mm on F.CrtYd, B.CrtYd kept")
        back = [g for g in self.courtyard_shapes(fp) if g.GetLayer() == pcbnew.B_CrtYd]
        self.assertEqual([(g.GetShape(), g.GetRadius()) for g in back], [(pcbnew.SHAPE_T_CIRCLE, pcbnew.FromMM(6))])
        self.assertEqual(cy.findings(pcbnew, fp), [])

    def test_an_smd_parts_keep_out_beside_its_own_courtyard_stays(self):
        pcbnew = self.pcbnew
        fp = self.footprint([(-1, 0), (1, 0)])
        self.rect(fp, pcbnew.F_CrtYd, -0.5, -0.5, 0.5, 0.5)  # too small: pads outside
        self.rect(fp, pcbnew.B_CrtYd, -4, -4, 4, 4)  # a keep-out on the back, beside it
        self.assertEqual(cy.redraw(pcbnew, fp), "3.50 x 1.50 mm on F.CrtYd, B.CrtYd kept")
        self.assertEqual(cy.findings(pcbnew, fp), [])

    def test_an_open_front_courtyard_keeps_the_keep_out_behind_it(self):
        pcbnew = self.pcbnew
        fp = self.footprint([(-1, 0), (1, 0)])
        self.rect(fp, pcbnew.F_CrtYd, -1.75, -0.75, 1.75, 0.75)
        fp.Remove(self.courtyard_shapes(fp)[0])  # its own, left open
        self.rect(fp, pcbnew.B_CrtYd, -4, -4, 4, 4)  # a keep-out on the back
        self.assertEqual(cy.findings(pcbnew, fp), ["F.CrtYd is not a closed outline",
                                                   "courtyard on B.CrtYd, pads on F.Cu"])  # the only closed one
        self.assertEqual(cy.redraw(pcbnew, fp), "3.50 x 1.50 mm on F.CrtYd, B.CrtYd kept")
        self.assertEqual(cy.findings(pcbnew, fp), [])

    def test_a_sound_front_survives_a_back_repair_under_the_checks_rules(self):
        pcbnew = self.pcbnew
        fp = self.header()  # through-hole, its own side the front
        land = list(fp.Pads())[3]  # pad 4's back copper larger than its front: inside B.CrtYd, not F.CrtYd
        land.Padstack().SetMode(pcbnew.PADSTACK.MODE_FRONT_INNER_BACK)
        land.SetShape(pcbnew.B_Cu, pcbnew.PAD_SHAPE_CIRCLE)
        land.SetSize(pcbnew.B_Cu, self.mm(4.0, 4.0))
        back = pcbnew.PAD(fp)  # and an SMD pad on the back, outside every courtyard
        back.SetNumber("5")
        back.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
        layers = pcbnew.LSET()
        layers.AddLayer(pcbnew.B_Cu)
        back.SetLayerSet(layers)
        back.SetSize(pcbnew.B_Cu, self.mm(1.0, 1.0))
        back.SetPosition(self.mm(0, 4))
        fp.Add(back)
        self.rect(fp, pcbnew.F_CrtYd, -4.6, -1.6, 4.6, 1.6)  # sound for the front, with room to spare
        self.rect(fp, pcbnew.B_CrtYd, -5.5, -2.5, 5.5, 2.5)  # holds the back land, not pad 5
        self.assertRegex("; ".join(cy.findings(pcbnew, fp)), r"^pad 5 outside it by up to 2\.\d\d mm$")
        self.assertEqual(cy.redraw(pcbnew, fp), "9.30 x 7.00 mm on B.CrtYd, F.CrtYd kept")
        self.assertEqual(len([g for g in self.courtyard_shapes(fp) if g.GetLayer() == pcbnew.F_CrtYd]), 4)
        self.assertEqual(cy.findings(pcbnew, fp), [])

    def test_only_the_faulty_side_is_redrawn(self):
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
        self.rect(fp, pcbnew.F_CrtYd, -3, -2, 3, 2)  # sound, with room to spare on purpose
        self.rect(fp, pcbnew.B_CrtYd, -0.2, 2.8, 0.2, 3.2)  # too small
        self.assertEqual(cy.redraw(pcbnew, fp), "1.50 x 1.50 mm on B.CrtYd, F.CrtYd kept")
        front = [g for g in self.courtyard_shapes(fp) if g.GetLayer() == pcbnew.F_CrtYd]
        self.assertEqual(len(front), 4)  # the four lines as drawn
        self.assertEqual(cy.findings(pcbnew, fp), [])

    def test_a_through_hole_parts_only_courtyard_on_the_far_side_is_reported_though_it_covers_all(self):
        pcbnew = self.pcbnew
        fp = self.header()
        self.rect(fp, pcbnew.B_CrtYd, -4.35, -1.35, 4.35, 1.35)  # right size, wrong side: nothing guards the front
        self.assertEqual(cy.findings(pcbnew, fp), ["courtyard on B.CrtYd, through-hole part on F.Cu's side"])
        self.assertEqual(cy.redraw(pcbnew, fp), "8.70 x 2.70 mm on F.CrtYd, B.CrtYd removed")
        self.assertEqual(cy.findings(pcbnew, fp), [])

    def test_a_through_hole_parts_only_courtyard_on_the_other_side_goes(self):
        pcbnew = self.pcbnew
        fp = self.header()  # mounted on its own side; EasyEDA drew a back-mounted header's courtyard on the top
        self.rect(fp, pcbnew.B_CrtYd, -1.3, -4.3, 1.3, 4.3)  # its only one, on the far side, turned against the pins
        self.assertEqual(cy.redraw(pcbnew, fp), "8.70 x 2.70 mm on F.CrtYd, B.CrtYd removed")
        self.assertEqual([g.GetLayer() for g in self.courtyard_shapes(fp)], [pcbnew.F_CrtYd])
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
        self.assertEqual(cy.redraw(pcbnew, fp), "1.50 x 1.50 mm on B.CrtYd, F.CrtYd kept")  # F was sound
        layers = [g.GetLayer() for g in self.courtyard_shapes(fp)]
        self.assertEqual((layers.count(pcbnew.F_CrtYd), layers.count(pcbnew.B_CrtYd)), (4, 1))
        self.assertEqual(cy.findings(pcbnew, fp), [])


@unittest.skipUnless(kicad.have_pcbnew(), "needs KiCad's Python")
class EasyedaBodies(Board):
    """The body outlines KiCad's EasyEDA importer drops (layer 99), drawn on fab by fitting the part's pads."""
    PADS = [(-0.8, 0), (0.8, 0), (0, 1.2)]  # not in a line: a mirror shows
    BODY = [(-1.2, -0.6), (1.2, -0.6), (1.2, 1.6), (-1.2, 1.6)]

    def fab_points(self, fp):
        pcbnew = self.pcbnew
        (g,) = [g for g in fp.GraphicalItems() if g.GetLayer() in (pcbnew.F_Fab, pcbnew.B_Fab)]
        o = g.GetPolyShape().Outline(0)
        return g.GetLayer(), [(pcbnew.ToMM(o.CPoint(k).x), pcbnew.ToMM(o.CPoint(k).y)) for k in range(o.PointCount())]

    def elsewhere(self, pts, turn=-20, at=(100.0, 200.0)):
        """The points as EasyEDA's board has them: turned and moved (the part was placed differently there)."""
        c, s = math.cos(math.radians(turn)), math.sin(math.radians(turn))
        return [(at[0] + x * c - y * s, at[1] + x * s + y * c) for x, y in pts]

    def assertNear(self, got, want):
        self.assertEqual(len(got), len(want))
        for (a, b), (c, d) in zip(got, want):
            self.assertAlmostEqual(a, c, places=3)
            self.assertAlmostEqual(b, d, places=3)

    def test_the_component_shape_layer_is_read_by_reference(self):
        from pcbkit.import_easyeda import easyeda_bodies
        lib = ("LIB~100~200~package`C0603`~0~~gge0~1~u~0~0~~yes~~#@$TEXT~P~95~190~0.6~0~0~3~~4.5~C7~M 1 1#@$"
               "PAD~RECT~90~200~3~3~1~GND~1~0~~0~gge1#@$PAD~RECT~110~200~3~3~1~VCC~2~0~~0~gge2#@$"
               "PAD~RECT~100~210~1~1~1~~~0~~0~gge3#@$"  # a peg: no number
               "SOLIDREGION~99~~M 85 196 L 115 196 L 115 204 L 85 204 Z ~solid~gge4~~~~0")
        bare = "LIB~0~0~package`TP`#@$TEXT~P~0~0~0.6~0~0~3~~4.5~TP1~M 1 1#@$PAD~ELLIPSE~0~0~4~4~2~~1~0~~0~gge5"
        under = lib.replace("~gge0~1~", "~gge7~2~").replace("~C7~", "~C8~")  # the same part on the bottom
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"shape": [lib, bare, under, "TRACK~1~1~~0 0 1 1~gge6~0"]}, f)
        try:
            got = easyeda_bodies(f.name)
        finally:
            os.unlink(f.name)
        self.assertEqual(sorted(got), ["C7", "C8"])  # TP1 draws no body
        self.assertEqual((got["C7"][2], got["C8"][2]), (False, True))
        pads, outlines, _ = got["C7"]
        self.assertEqual(sorted(pads), ["1", "2"])
        self.assertNear([pads["1"], pads["2"]], [(22.86, 50.8), (27.94, 50.8)])
        self.assertNear(outlines[0], [(21.59, 49.784), (29.21, 49.784), (29.21, 51.816), (21.59, 51.816)])

    def test_a_body_follows_a_part_moved_and_turned_since_the_import(self):
        from pcbkit.import_easyeda import add_body
        pcbnew = self.pcbnew
        fp = self.footprint(self.PADS, size=(0.6, 0.6))
        fp.SetPosition(self.mm(20, 10))
        fp.SetOrientationDegrees(37)
        src = self.elsewhere(self.PADS)
        self.assertTrue(add_body(pcbnew, fp, {str(i): p for i, p in enumerate(src, 1)}, [self.elsewhere(self.BODY)]))
        to_board = cy._frame(pcbnew, fp)[1]
        want = [to_board(pcbnew.FromMM(x), pcbnew.FromMM(y)) for x, y in self.BODY]
        layer, got = self.fab_points(fp)
        self.assertEqual(layer, pcbnew.F_Fab)
        self.assertNear(got, [(pcbnew.ToMM(p.x), pcbnew.ToMM(p.y)) for p in want])

    def test_a_part_on_the_back_gets_its_body_mirrored_on_b_fab(self):
        from pcbkit.import_easyeda import add_body
        pcbnew = self.pcbnew
        fp = self.footprint(self.PADS, size=(0.6, 0.6))
        fp.SetPosition(self.mm(20, 10))
        fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
        src = self.elsewhere(self.PADS)  # as the library draws it, from the top
        self.assertTrue(add_body(pcbnew, fp, {str(i): p for i, p in enumerate(src, 1)}, [self.elsewhere(self.BODY)]))
        layer, got = self.fab_points(fp)
        self.assertEqual(layer, pcbnew.B_Fab)
        self.assertNear(got, [(20 - x, 10 + y) for x, y in self.BODY])

    def test_a_two_pad_part_turned_over_since_gets_its_body_on_the_right_side_of_its_pads(self):
        from pcbkit.import_easyeda import draw_bodies
        pcbnew = self.pcbnew
        fp = self.footprint([(-1, 0), (1, 0)], size=(0.6, 0.6))  # pads in a line fit mirrored or not
        fp.SetPosition(self.mm(20, 10))
        fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)  # on the back now, on the top in the export
        body = [(-1.5, -0.5), (1.5, -0.5), (1.5, 2.0), (-1.5, 2.0)]  # off to one side of the pads
        found = {"U1": ({"1": (-1, 0), "2": (1, 0)}, [body], False)}
        self.assertEqual(draw_bodies(pcbnew, [fp], found), ["U1"])
        layer, got = self.fab_points(fp)
        self.assertEqual(layer, pcbnew.B_Fab)
        self.assertNear(got, [(20 - x, 10 + y) for x, y in body])  # unmirrored, it would land at 10 - y

    def test_a_targeted_fix_draws_bodies_only_on_the_parts_asked_for(self):
        pcbnew = self.pcbnew
        for ref, dx in (("U1", 0), ("U2", 10)):
            fp = self.footprint([(x + dx, y) for x, y in self.PADS], size=(0.6, 0.6))
            fp.SetReference(ref)
            self.rect(fp, pcbnew.F_CrtYd, dx - 2, -1, dx + 2, 2)  # sound: nothing to redraw
        lib = lambda ref, dx: (f"LIB~0~0~package`X`~0~~g{ref}~1#@$TEXT~P~0~0~0.6~0~0~3~~4.5~{ref}~M 1 1#@$"
                               + "#@$".join(f"PAD~RECT~{(x + dx) / 0.254}~{y / 0.254}~1~1~1~~{i}~0~~0~p{ref}{i}"
                                            for i, (x, y) in enumerate(self.PADS, 1))
                               + "#@$SOLIDREGION~99~~M " + " L ".join(f"{(x + dx) / 0.254} {y / 0.254}"
                                                                      for x, y in self.BODY) + " Z ~solid~b~~~~0")
        with tempfile.TemporaryDirectory() as t:
            board, export = os.path.join(t, "b.kicad_pcb"), os.path.join(t, "pcb.json")
            pcbnew.PCB_IO_MGR.Save(pcbnew.PCB_IO_MGR.KICAD_SEXP, board, self.board)
            with open(export, "w") as f:
                json.dump({"shape": [lib("U1", 0), lib("U2", 10)]}, f)
            cy.main([board, "--fix", "--easyeda", export, "--ref", "U1"])
            fab = {fp.GetReference(): [g for g in fp.GraphicalItems() if g.GetLayer() == pcbnew.F_Fab]
                   for fp in pcbnew.LoadBoard(board).GetFootprints()}
        self.assertEqual((len(fab["U1"]), len(fab["U2"])), (1, 0))

    def test_no_body_where_the_pads_differ_or_a_fab_drawing_is_there(self):
        from pcbkit.import_easyeda import add_body
        pcbnew = self.pcbnew
        fp = self.footprint(self.PADS, size=(0.6, 0.6))
        wider = {str(i): (x * 1.1, y) for i, (x, y) in enumerate(self.PADS, 1)}  # another land pattern
        self.assertFalse(add_body(pcbnew, fp, wider, [self.BODY]))
        self.assertFalse(add_body(pcbnew, fp, {"1": (0, 0)}, [self.BODY]))  # one pad fixes nothing
        two = {str(i): p for i, p in enumerate(self.PADS[:2], 1)}  # pads 1 and 2 line up; the footprint has a third
        self.assertFalse(add_body(pcbnew, fp, two, [self.BODY]))
        self.rect(fp, pcbnew.F_Fab, -1, -1, 1, 1)
        self.assertFalse(add_body(pcbnew, fp, {str(i): p for i, p in enumerate(self.PADS, 1)}, [self.BODY]))

    def test_the_redraw_goes_by_the_body_not_the_silk(self):
        from pcbkit.import_easyeda import add_body
        pcbnew = self.pcbnew
        fp = self.footprint([(-0.7, 0), (0.7, 0)], size=(0.8, 0.9))  # an EasyEDA 0603
        self.rect(fp, pcbnew.F_SilkS, -1.4, -0.72, 1.4, 0.72)  # its silk, well off the body
        self.rect(fp, pcbnew.F_CrtYd, -0.3, -1.2, 0.3, 1.2)  # turned against the pads
        self.assertEqual(cy.redraw(pcbnew, self.clone(fp)), "3.30 x 1.94 mm on F.CrtYd")  # round the silk
        body = [(-0.8, -0.4), (0.8, -0.4), (0.8, 0.4), (-0.8, 0.4)]
        self.assertTrue(add_body(pcbnew, fp, {"1": (-0.7, 0), "2": (0.7, 0)}, [body]))
        self.assertEqual(cy.redraw(pcbnew, fp), "2.70 x 1.40 mm on F.CrtYd")  # pads and body + 0.25 mm
        self.assertEqual(cy.findings(pcbnew, fp), [])

    def clone(self, fp):
        c = self.pcbnew.FOOTPRINT(fp)
        self.board.Add(c)
        return c


if __name__ == "__main__":
    unittest.main()
