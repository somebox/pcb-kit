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

    def test_an_svg_reference_is_embedded_as_svg(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "fig.svg")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>')
            self.assertTrue(netview._image_href(path).startswith("data:image/svg+xml;base64,"))

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

    def test_a_custom_pads_paste_shrinks_with_its_margin_ratio(self):
        """A 2 x 0.5 mm custom pad on a 0.4 x 0.2 mm anchor at -50 % paste: margins -0.2 and -0.1 mm, and KiCad's
        plotter grows the whole shape by the first, to 1.6 x 0.1 mm. Resizing the anchor alone left it 2 x 0.5."""
        pcbnew = self.pcbnew
        fp = pcbnew.FOOTPRINT(self.board)
        fp.SetReference("U1")
        self.board.Add(fp)
        p = self.pad(fp, "1", 0, 0, 0.4, 0.2, (pcbnew.F_Cu, pcbnew.F_Mask, pcbnew.F_Paste), "VOUT")
        p.SetShape(pcbnew.F_Cu, pcbnew.PAD_SHAPE_CUSTOM)
        p.SetAnchorPadShape(pcbnew.F_Cu, pcbnew.PAD_SHAPE_RECTANGLE)
        strip = pcbnew.SHAPE_POLY_SET()
        strip.NewOutline()
        for x, y in ((-1, -0.25), (1, -0.25), (1, 0.25), (-1, 0.25)):
            strip.Append(pcbnew.FromMM(x), pcbnew.FromMM(y))
        p.AddPrimitivePoly(pcbnew.F_Cu, strip, 0, True)
        p.SetLocalSolderPasteMarginRatio(-0.5)
        self.via(0.9, 0, "VOUT", 0.2, 0.1)      # the hole spans x 0.85-0.95: in the copper, past the paste
        self.via(0.5, 0, "VOUT", 0.2, 0.1)      # in both
        found = {round(v[0], 2): v[5] for v in self.draw()["vias"]}
        self.assertEqual(found, {0.9: False, 0.5: True})

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

    def test_a_pad_number_never_hides_a_via_hole(self):
        """A 0.3 mm pad with a 0.25 mm via hole at its middle: every spot on it covers the hole, so no label."""
        pcbnew = self.pcbnew
        fp = pcbnew.FOOTPRINT(self.board)
        fp.SetReference("U1")
        self.board.Add(fp)
        self.pad(fp, "7", 0, 0, 0.3, 0.3, (pcbnew.F_Cu, pcbnew.F_Mask, pcbnew.F_Paste), "VOUT")
        self.via(0, 0, "VOUT", 0.3, 0.25)
        view = self.draw()
        self.assertEqual([v[5] for v in view["vias"]], [True])
        self.assertFalse(any("font-weight=\"bold\"" in e for e in view["svg"]))
        self.assertEqual(view["pads"], {"VOUT": {"U1": {"7"}}})       # still listed in the legend

    def test_a_round_pad_whose_box_alone_reaches_the_window_is_left_out(self):
        """A 2 mm round pad at (1, 1): its box covers the window's corner at (0.1, 0.1), its copper doesn't."""
        pcbnew = self.pcbnew
        fp = pcbnew.FOOTPRINT(self.board)
        fp.SetReference("U1")
        self.board.Add(fp)
        p = self.pad(fp, "1", 1.0, 1.0, 2.0, 2.0, (pcbnew.F_Cu,), "VOUT")
        p.SetShape(pcbnew.F_Cu, pcbnew.PAD_SHAPE_CIRCLE)
        colour = netview.make_colour({**netview.COLOURS, "VOUT": "#e8892b"})
        view = netview.draw(pcbnew, self.board, "F.Cu", (-0.1, -0.1, 0.1, 0.1), colour, 100.0, True)
        self.assertEqual(view["pads"], {})
        self.assertFalse(any("font-weight=\"bold\"" in e for e in view["svg"]))

    def test_nets_whose_copper_stays_outside_the_window_are_left_out(self):
        """A via, a diagonal track and a zone just outside the window: drawn past its edge, never in the legend."""
        pcbnew = self.pcbnew
        self.via(WINDOW[2] + 0.3, 0, "/a/NEAR_VIA")                  # 0.45 mm across: its copper ends 0.075 mm out
        self.via(WINDOW[2] + 0.1, 1, "/a/EDGE_VIA")                  # reaches 0.125 mm into the window
        t = pcbnew.PCB_TRACK(self.board)                             # its box covers the corner; it passes outside
        t.SetStart(self.mm(WINDOW[2] - 0.4, WINDOW[1] - 1.0))
        t.SetEnd(self.mm(WINDOW[2] + 1.0, WINDOW[1] + 0.4))
        t.SetWidth(pcbnew.FromMM(0.2))
        t.SetLayer(pcbnew.F_Cu)
        self.board.Add(t)
        t.SetNet(self.net("/a/NEAR_TRACK"))
        z = pcbnew.ZONE(self.board)
        z.SetLayer(pcbnew.F_Cu)
        z.Outline().NewOutline()
        for x, y in ((-5, -5), (WINDOW[0] - 0.5, -5), (WINDOW[0] - 0.5, 5), (-5, 5)):
            z.Outline().Append(pcbnew.FromMM(x), pcbnew.FromMM(y))
        self.board.Add(z)
        z.SetNet(self.net("/a/NEAR_ZONE"))
        pcbnew.ZONE_FILLER(self.board).Fill(self.board.Zones())
        self.assertTrue(z.HasFilledPolysForLayer(pcbnew.F_Cu))
        self.assertEqual(self.draw()["nets"], {"/a/EDGE_VIA"})

    def test_a_long_legend_entry_wraps_inside_the_sheet(self):
        """GND on 16 capacitors' pads makes an entry longer than a one-panel sheet is wide."""
        pcbnew = self.pcbnew
        for i in range(16):
            fp = pcbnew.FOOTPRINT(self.board)
            fp.SetReference(f"C{i + 1}")
            self.board.Add(fp)
            self.pad(fp, "2", -2.5 + (i % 8) * 0.7, -1 + (i // 8) * 2, 0.4, 0.4, (pcbnew.F_Cu,), "GND")
        with tempfile.TemporaryDirectory() as d:
            board = os.path.join(d, "b.kicad_pcb")
            kicad.save_board(self.board, board)
            out = os.path.join(d, "sheet.svg")
            netview.sheet([(board, "Option")], out, *WINDOW, layer_name="F.Cu", note="x")
            with open(out, encoding="utf-8") as fh:
                text = fh.read()
        width = float(re.search(r'<svg [^>]*width="([\d.]+)"', text).group(1))
        legend = re.findall(r'<text x="([\d.]+)" y="[\d.]+" font-size="14"[^>]*>([^<]*)</text>', text)
        self.assertGreater(len(legend), 1)
        for x, line in legend:
            self.assertLessEqual(float(x) + 7.4 * len(line), width - 24, line)
        refs = sorted(f"C{i + 1}" for i in range(16))
        self.assertEqual(" ".join(line for _x, line in legend), "GND (" + "; ".join(f"{r} 2" for r in refs) + ")")

    def test_long_titles_and_numbers_wrap_inside_their_panel(self):
        """A long title, and two nets sharing VOUT under long hierarchical names, stay inside a one-panel sheet."""
        long_a = "/power/stage_one/buck_boost_converter/output_filter/VOUT"
        self.via(-2.2, 0.3, long_a)
        self.via(2.2, 0.3, long_a.replace("one", "two"))
        title = "Option 3, corners and the side vias TI's figure shows, moved 0.1 mm out from the strips: 9 vias"
        with tempfile.TemporaryDirectory() as d:
            board = os.path.join(d, "b.kicad_pcb")
            kicad.save_board(self.board, board)
            out = os.path.join(d, "sheet.svg")
            netview.sheet([(board, title)], out, *WINDOW, layer_name="F.Cu", metrics=["VOUT"])
            with open(out, encoding="utf-8") as fh:
                text = fh.read()
        width = float(re.search(r'<svg [^>]*width="([\d.]+)"', text).group(1))
        lines = re.findall(r'<text x="([\d.]+)" y="[\d.]+" font-size="(1[45])"[^>]*>([^<]*)</text>', text)
        for x, size, line in lines:
            self.assertLessEqual(float(x) + {"14": 7.4, "15": 8.0}[size] * len(line), width - 24, line)
        prose = " ".join(line for _x, _s, line in lines)
        self.assertIn(title, prose)
        self.assertIn(long_a + ": 1 vias,", prose)

    def test_sheet_on_a_renamed_outer_layer_counts_paste(self):
        """A board may call F.Cu "Top"; the sheet still counts holes under paste and shows paste in the legend."""
        self.board.SetLayerName(self.pcbnew.F_Cu, "Top")
        self.part()
        self.via(-1.5, 0.3, "VOUT")
        with tempfile.TemporaryDirectory() as d:
            board = os.path.join(d, "b.kicad_pcb")
            kicad.save_board(self.board, board)
            out = os.path.join(d, "sheet.svg")
            netview.sheet([(board, "Option")], out, *WINDOW, layer_name="Top", detail=True, note="x")
            with open(out, encoding="utf-8") as fh:
                text = fh.read()
        self.assertIn("holes under paste: 1", text)
        self.assertIn("solder paste", text)

    def test_sheet_says_each_boards_thickness(self):
        self.via(-2.2, 0.3, "VOUT")
        with tempfile.TemporaryDirectory() as d:
            boards = []
            for name, thick in (("a", 1.6), ("b", 1.2)):
                self.board.GetDesignSettings().SetBoardThickness(self.pcbnew.FromMM(thick))
                boards.append((os.path.join(d, name + ".kicad_pcb"), name))
                kicad.save_board(self.board, boards[-1][0])
            out = os.path.join(d, "sheet.svg")
            netview.sheet(boards, out, *WINDOW, layer_name="F.Cu", metrics=["VOUT"])
            with open(out, encoding="utf-8") as fh:
                text = fh.read()
        prose = " ".join(re.sub("<[^>]+>", " ", text).split())
        self.assertIn("through each board, board by board 1.6, 1.2 mm thick.", prose)
        self.assertIn(f"{netview.via_ohms(0.2, 1.2) * 1000:.2f} mΩ", text)

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

    def test_cols_below_one_is_refused(self):
        with self.assertRaises(SystemExit), open(os.devnull, "w") as quiet:
            stderr, sys.stderr = sys.stderr, quiet
            try:
                netview.main(["b.kicad_pcb", "out.svg", "0", "0", "6", "6", "--title", "Option", "--cols", "-1"])
            finally:
                sys.stderr = stderr


if __name__ == "__main__":
    unittest.main()
