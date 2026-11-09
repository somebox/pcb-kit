"""`pcbkit netview --detail` and its comparison sheet: which via holes land in paste, and the numbers under a panel.

  python3 tests/test_netview.py     # re-runs itself under KiCad's Python when pcbnew is not importable

The cases are the two ways a via ends up in paste: inside a pad that carries its own paste (the strip pads of
siebenuhr controller v3's TPS55288, 2026-11-09), and inside a paste aperture drawn as a pad on the paste layer only,
over an exposed pad that has none (the windows of KiCad's QFN footprints).
"""
import math
import os
import re
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

from pcbkit import netview  # noqa: E402

WINDOW = (-3.0, -3.0, 3.0, 3.0)


class Numbers(unittest.TestCase):
    def test_via_resistance(self):
        """A 0.2 mm finished hole through 1.6 mm with 18 um plating outside it is 2.23 mOhm; 0.25 mm is 1.82."""
        self.assertAlmostEqual(netview.via_ohms(0.2, 1.6) * 1000, 2.232, places=3)
        self.assertAlmostEqual(netview.via_ohms(0.25, 1.6) * 1000, 1.816, places=3)
        self.assertAlmostEqual(netview.wall_area(0.2), math.pi * 0.218 * 0.018)

    def test_circle_against_a_polygon_with_a_hole(self):
        square = [(0, 0), (2, 0), (2, 2), (0, 2)]
        hole = [(0.5, 0.5), (1.5, 0.5), (1.5, 1.5), (0.5, 1.5)]
        polys = [(square, [hole])]
        self.assertTrue(netview.circle_hits(0.25, 1, 0.1, polys))     # in the copper
        self.assertFalse(netview.circle_hits(1, 1, 0.1, polys))       # in the hole, clear of its edge
        self.assertTrue(netview.circle_hits(1, 1, 0.6, polys))        # in the hole, over its edge
        self.assertTrue(netview.circle_hits(2.05, 1, 0.1, polys))     # outside, over the outline
        self.assertFalse(netview.circle_hits(2.2, 1, 0.1, polys))


@unittest.skipUnless(kicad.have_pcbnew(), "needs KiCad's Python")
class Detail(unittest.TestCase):
    def setUp(self):
        import pcbnew
        self.pcbnew = pcbnew
        self.board = pcbnew.BOARD()
        self.nets = {}

    def mm(self, x, y):
        return self.pcbnew.VECTOR2I(self.pcbnew.FromMM(x), self.pcbnew.FromMM(y))

    def net(self, name):
        if name not in self.nets:
            n = self.pcbnew.NETINFO_ITEM(self.board, name)
            self.board.Add(n)
            self.nets[name] = n
        return self.nets[name]

    def pad(self, fp, number, x, y, w, h, layers, net=None):
        pcbnew = self.pcbnew
        p = pcbnew.PAD(fp)
        p.SetNumber(number)
        p.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
        ls = pcbnew.LSET()
        for layer in layers:
            ls.AddLayer(layer)
        p.SetLayerSet(ls)
        p.SetShape(pcbnew.F_Cu, pcbnew.PAD_SHAPE_RECTANGLE)
        p.SetSize(pcbnew.F_Cu, self.mm(w, h))
        p.SetPosition(self.mm(x, y))
        fp.Add(p)
        if net:
            p.SetNet(self.net(net))
        return p

    def via(self, x, y, net, size=0.45, drill=0.2, blind=False):
        pcbnew = self.pcbnew
        v = pcbnew.PCB_VIA(self.board)
        v.SetPosition(self.mm(x, y))
        v.SetWidth(pcbnew.FromMM(size))
        v.SetDrill(pcbnew.FromMM(drill))
        v.SetViaType(pcbnew.VIATYPE_BLIND if blind else pcbnew.VIATYPE_THROUGH)
        v.SetLayerPair(pcbnew.F_Cu, pcbnew.In1_Cu if blind else pcbnew.B_Cu)
        self.board.Add(v)
        v.SetNet(self.net(net))
        return v

    def part(self):
        """A strip pad with its own paste, and an exposed pad whose paste is one aperture pad off its centre."""
        pcbnew = self.pcbnew
        fp = pcbnew.FOOTPRINT(self.board)
        fp.SetReference("U1")
        self.board.Add(fp)
        self.pad(fp, "1", -1.5, 0, 0.4, 1.4, (pcbnew.F_Cu, pcbnew.F_Mask, pcbnew.F_Paste), "VOUT")
        self.pad(fp, "2", 1.0, 0, 2.0, 2.0, (pcbnew.F_Cu, pcbnew.F_Mask), "GND")
        self.pad(fp, "", 0.5, -0.5, 0.8, 0.8, (pcbnew.F_Paste,))
        return fp

    def draw(self, detail=True):
        colour = netview.make_colour({**netview.COLOURS, "VOUT": "#e8892b"})
        return netview.draw(self.pcbnew, self.board, "F.Cu", WINDOW, colour, 100.0, detail)

    def test_vias_in_paste(self):
        self.part()
        self.via(-1.5, 0.3, "VOUT")        # in the strip, under its paste
        self.via(-2.2, 0.3, "VOUT")        # beside the strip
        self.via(0.5, -0.5, "GND")         # in the exposed pad's aperture
        self.via(1.5, 0.5, "GND")          # in the exposed pad, between apertures
        found = {(round(v[0], 2), round(v[1], 2)): v[5] for v in self.draw()["vias"]}
        self.assertEqual(found, {(-1.5, 0.3): True, (-2.2, 0.3): False, (0.5, -0.5): True, (1.5, 0.5): False})

    def test_plain_view_leaves_paste_out(self):
        self.part()
        self.via(-1.5, 0.3, "VOUT")
        view = self.draw(detail=False)
        self.assertEqual([v[5] for v in view["vias"]], [False])
        self.assertFalse(any("url(#paste)" in e for e in view["svg"]))

    def test_pad_numbers_on_coloured_nets_only(self):
        self.part()
        texts = [re.sub("<[^>]+>", "", e) for e in self.draw()["svg"] if "font-weight=\"bold\"" in e]
        self.assertEqual(sorted(t for t in texts if t), ["1", "2"])  # VOUT and GND have colours; the aperture none

    def test_a_pad_reaching_into_the_window_is_in_the_legend(self):
        """Its centre is outside the window, but its copper is drawn, so its net and number are listed and labelled."""
        fp = self.part()
        self.pad(fp, "3", WINDOW[0] - 0.5, 0, 2.0, 1.0, (self.pcbnew.F_Cu,), "VOUT")
        self.pad(fp, "4", WINDOW[0] - 1.5, 0, 0.5, 0.5, (self.pcbnew.F_Cu,), "VOUT")   # wholly outside
        view = self.draw()
        self.assertEqual(view["pads"]["VOUT"], {"U1": {"1", "3"}})
        texts = [re.sub("<[^>]+>", "", e) for e in view["svg"] if "font-weight=\"bold\"" in e]
        self.assertIn("3", texts)
        self.assertNotIn("4", texts)

    def test_an_arc_track_is_drawn_as_its_outline(self):
        """No corpus board has arc tracks; a chord would cut across whatever the arc goes round."""
        pcbnew = self.pcbnew
        a = pcbnew.PCB_ARC(self.board)
        a.SetStart(self.mm(-1, 0))
        a.SetMid(self.mm(0, -1))
        a.SetEnd(self.mm(1, 0))
        a.SetWidth(pcbnew.FromMM(0.2))
        a.SetLayer(pcbnew.F_Cu)
        self.board.Add(a)
        a.SetNet(self.net("VOUT"))
        svg = [e for e in self.draw()["svg"] if "#e8892b" in e]
        self.assertEqual(len(svg), 1)
        self.assertTrue(svg[0].startswith("<path"))
        ys = [float(p.split(",")[1]) for p in re.findall(r"[\d.]+,[\d.]+", svg[0])]
        self.assertLess(min(ys), (-1.0 - WINDOW[1]) * 100 + 1)       # reaches the arc's top at y = -1 mm, 100 px/mm

    def test_sheet_numbers(self):
        self.part()
        self.via(-1.5, 0.3, "VOUT")
        self.via(-2.2, 0.3, "VOUT", 0.5, 0.25)
        with tempfile.TemporaryDirectory() as d:
            board = os.path.join(d, "b.kicad_pcb")
            kicad.save_board(self.board, board)
            out = os.path.join(d, "sheet.svg")
            netview.sheet([(board, "Option")], out, *WINDOW, layer_name="F.Cu", colours={"VOUT": "#e8892b"},
                          detail=True, metrics=["VOUT", "GND"], note="DRC clean.")
            with open(out) as fh:
                text = fh.read()
        ohms = 1 / (1 / netview.via_ohms(0.2, 1.6) + 1 / netview.via_ohms(0.25, 1.6)) * 1000
        self.assertIn("holes under paste: 1", text)
        self.assertIn(f"VOUT: 2 vias, wall {netview.wall_area(0.2) + netview.wall_area(0.25):.3f} mm", text)
        self.assertIn(f"{ohms:.2f} mΩ", text)
        self.assertIn("GND: no vias", text)
        self.assertIn("VOUT (U1 1)", text)
        prose = " ".join(re.sub("<[^>]+>", " ", text).split())       # the footnote wraps over several lines
        self.assertIn("parallel through the 1.6 mm board. DRC clean.", prose)

    def test_sheet_leaves_blind_vias_out_of_the_copper(self):
        """A blind via spans part of the board: it is counted apart, not put in parallel with the through vias."""
        self.board.SetCopperLayerCount(4)
        self.part()
        self.via(-2.2, 0.3, "VOUT")
        self.via(-2.2, -0.5, "VOUT", blind=True)
        self.via(2.2, 0.3, "GND", blind=True)
        with tempfile.TemporaryDirectory() as d:
            board = os.path.join(d, "b.kicad_pcb")
            kicad.save_board(self.board, board)
            out = os.path.join(d, "sheet.svg")
            netview.sheet([(board, "Option")], out, *WINDOW, layer_name="F.Cu", detail=True,
                          metrics=["VOUT", "GND"])
            with open(out, encoding="utf-8") as fh:
                text = fh.read()
        self.assertIn(f"VOUT: 1 vias, wall {netview.wall_area(0.2):.3f} mm², "
                      f"{netview.via_ohms(0.2, 1.6) * 1000:.2f} mΩ, 1 blind/micro not counted", text)
        self.assertIn("GND: no through vias, 1 blind/micro not counted", text)

    def test_nets_sharing_a_last_name_part_are_not_in_parallel(self):
        """/a/VOUT and /b/VOUT are two nets: --metrics VOUT gives a line for each, at one via's resistance."""
        self.via(-2.2, 0.3, "/a/VOUT")
        self.via(2.2, 0.3, "/b/VOUT")
        self.via(0, 2.2, "/a/GND")
        with tempfile.TemporaryDirectory() as d:
            board = os.path.join(d, "b.kicad_pcb")
            kicad.save_board(self.board, board)
            out = os.path.join(d, "sheet.svg")
            netview.sheet([(board, "Option")], out, *WINDOW, layer_name="F.Cu", metrics=["VOUT", "GND", "/a/VOUT"])
            with open(out, encoding="utf-8") as fh:
                lines = re.findall(r">([^<>]*: [^<>]*)<", fh.read())
        one = f"1 vias, wall {netview.wall_area(0.2):.3f} mm², {netview.via_ohms(0.2, 1.6) * 1000:.2f} mΩ"
        self.assertEqual(lines, [f"/a/VOUT: {one}", f"/b/VOUT: {one}", f"GND: {one}", f"/a/VOUT: {one}"])

    def test_a_note_alone_makes_a_sheet(self):
        """--note asks for a footnote, which only a sheet has."""
        self.part()
        with tempfile.TemporaryDirectory() as d:
            board = os.path.join(d, "b.kicad_pcb")
            kicad.save_board(self.board, board)
            out = os.path.join(d, "view.svg")
            netview.main([board, out, *(str(v) for v in WINDOW), "--layer", "F.Cu", "--note", "DRC clean."])
            with open(out, encoding="utf-8") as fh:
                self.assertIn("DRC clean.", fh.read())


if __name__ == "__main__":
    unittest.main()
