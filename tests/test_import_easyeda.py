"""`pcbkit import-easyeda`: the corrections it makes to what KiCad's EasyEDA importer leaves.

  python3 tests/test_import_easyeda.py     # re-runs itself under KiCad's Python when pcbnew is not importable

The text cases (symbol library merge, keep-outs, sheet names, Footprint fields) run under any Python; the board
cases (pad checks, loose pads, outline gaps) draw small boards in pcbnew.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pcbkit import kicad  # noqa: E402
from pcbkit import import_easyeda as ie  # noqa: E402

if __name__ == "__main__" and not kicad.have_pcbnew() and not os.environ.get("PCBKIT_REEXEC"):
    env = dict(os.environ, PCBKIT_REEXEC="1")
    env["PYTHONPATH"] = ROOT + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    os.execve(kicad.KICAD_PYTHON, [kicad.KICAD_PYTHON, os.path.abspath(__file__), *sys.argv[1:]], env)


def part(ref, name, pins, value=None, package="R0603"):
    """An EasyEDA schematic part as load_schematic gives it. pins: (number, name, x, y, dx, dy), in EasyEDA units
    from the part's origin, (dx, dy) from the pin's end to where it meets the body."""
    field = lambda text, dy: dict(x=0, y=dy, rot=0, size="", bold=False, text=text, vis=True, anchor="start")
    fields = {"P": field(ref, -10)}
    if value:
        fields["N"] = field(value, 10)
    return dict(x=0, y=0, rot="0", gfx=[], para={"name": name, "package": package, "Supplier Part": "C1"}, fields=fields,
                pins=[dict(num=num, name=pname, x=x, y=y, bx=x + dx, by=y + dy, length=abs(dx or dy))
                      for num, pname, x, y, dx, dy in pins])


def schematic(*parts):
    return {"sheets": [dict(parts=list(parts), wires=[], flags=[], nc=[], texts=[], images=[], rects=[], junctions=[],
                            bbox=dict(x=-50, y=-50, width=100, height=100), name="")]}


RES = [("1", "1", -10, 0, 5, 0), ("2", "2", 10, 0, -5, 0)]
CHIP3 = [("1", "A", -20, 0, 5, 0), ("2", "B", -20, 10, 5, 0), ("3", "C", 20, 0, -5, 0)]
CHIP4 = CHIP3 + [("4", "D", 20, 10, -5, 0)]
LED = [("1", "K", -10, 0, 5, 0), ("2", "A", 10, 0, -5, 0)]

# RES as KiCad's symbol editor saves it: what a library looks like once someone has edited it in KiCad.
KICAD_SAVED_RES = """(symbol "RES"
\t\t(pin_numbers
\t\t\t(hide yes)
\t\t)
\t\t(exclude_from_sim no)
\t\t(in_bom yes)
\t\t(on_board yes)
\t\t(property "Reference" "R"
\t\t\t(at 0 2.54 0)
\t\t\t(effects
\t\t\t\t(font
\t\t\t\t\t(size 1.27 1.27)
\t\t\t\t)
\t\t\t)
\t\t)
\t\t(symbol "RES_0_1"
\t\t\t(rectangle
\t\t\t\t(start -1.27 0.5)
\t\t\t\t(end 1.27 -0.5)
\t\t\t\t(stroke
\t\t\t\t\t(width 0.254)
\t\t\t\t\t(type default)
\t\t\t\t)
\t\t\t\t(fill
\t\t\t\t\t(type background)
\t\t\t\t)
\t\t\t)
\t\t)
\t\t(symbol "RES_1_1"
\t\t\t(pin passive line
\t\t\t\t(at 2.54 0 180)
\t\t\t\t(length 1.27)
\t\t\t\t(name "2"
\t\t\t\t\t(effects
\t\t\t\t\t\t(font
\t\t\t\t\t\t\t(size 1.016 1.016)
\t\t\t\t\t\t)
\t\t\t\t\t)
\t\t\t\t)
\t\t\t\t(number "2"
\t\t\t\t\t(effects
\t\t\t\t\t\t(font
\t\t\t\t\t\t\t(size 1.016 1.016)
\t\t\t\t\t\t)
\t\t\t\t\t)
\t\t\t\t)
\t\t\t)
\t\t\t(pin passive line
\t\t\t\t(at -2.54 0 0)
\t\t\t\t(length 1.27)
\t\t\t\t(name "1"
\t\t\t\t\t(effects
\t\t\t\t\t\t(font
\t\t\t\t\t\t\t(size 1.016 1.016)
\t\t\t\t\t\t)
\t\t\t\t\t)
\t\t\t\t)
\t\t\t\t(number "1"
\t\t\t\t\t(effects
\t\t\t\t\t\t(font
\t\t\t\t\t\t\t(size 1.016 1.016)
\t\t\t\t\t\t)
\t\t\t\t\t)
\t\t\t\t)
\t\t\t)
\t\t)
\t\t(embedded_fonts no)
\t)"""


class SymbolLibrary(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.lib = Path(self.dir.name) / "proj.kicad_sym"

    def tearDown(self):
        self.dir.cleanup()

    def load(self, sch):
        existing = ie.read_symbol_lib(self.lib)
        sym_of, defs, stats = ie.register_symbols(sch, "proj", existing)
        ie.write_symbol_lib(self.lib, existing, defs)
        return sym_of, stats

    def test_a_second_board_keeps_the_first_boards_symbols(self):
        self.load(schematic(part("R1", "RES", RES, "10k"), part("U1", "CHIP", CHIP3)))
        first = ie.read_symbol_lib(self.lib)
        sym_of, stats = self.load(schematic(part("R5", "RES", RES, "220"), part("U9", "CHIP", CHIP4),
                                            part("D1", "LED", LED)))
        final = ie.read_symbol_lib(self.lib)
        self.assertEqual(sym_of, {"R5": "RES", "U9": "CHIP_2", "D1": "LED"})
        self.assertEqual(sorted(final), ["CHIP", "CHIP_2", "LED", "RES"])
        self.assertEqual(final["RES"], first["RES"])    # same pins: the library's symbol, as it was
        self.assertEqual(final["CHIP"], first["CHIP"])  # other pins: left alone, the new one is CHIP_2
        self.assertEqual((stats["new"], stats["kept"], stats["renamed"]), (2, 1, {"CHIP"}))

    def test_importing_a_board_again_changes_nothing(self):
        sch = schematic(part("R1", "RES", RES), part("U1", "CHIP", CHIP3), part("U2", "CHIP", CHIP4))
        sym_of, _ = self.load(sch)
        text = self.lib.read_text()
        again, stats = self.load(sch)
        self.assertEqual(again, sym_of)
        self.assertEqual(self.lib.read_text(), text)
        self.assertEqual((stats["new"], stats["kept"], stats["renamed"]), (0, 3, set()))

    def test_a_symbol_saved_by_kicad_is_recognised_by_its_pins(self):
        new = ie.symbol_def("RES", part("R1", "RES", RES), "proj", "R0603")
        self.assertEqual(ie.symbol_pins(KICAD_SAVED_RES), ie.symbol_pins(new))
        self.assertTrue(ie.same_symbol(KICAD_SAVED_RES, new))
        self.assertFalse(ie.same_symbol(KICAD_SAVED_RES, ie.symbol_def("RES", part("U1", "RES", CHIP3), "proj", "")))
        self.assertFalse(ie.same_symbol('(symbol "RES" (extends "R"))', new))

    def test_a_symbol_split_into_units_is_not_reused(self):
        # the same two pins, pin 2 moved to a unit 2: the schematic places unit 1 only, so pin 2 would go missing
        i = KICAD_SAVED_RES.index("\t\t\t(pin passive line\n\t\t\t\t(at -2.54")
        split = (KICAD_SAVED_RES[:i] + '\t\t)\n\t\t(symbol "RES_1_1"\n' + KICAD_SAVED_RES[i:]).replace('"RES_1_1"', '"RES_2_1"', 1)
        new = ie.symbol_def("RES", part("R1", "RES", RES), "proj", "R0603")
        self.assertEqual(ie.symbol_pins(split), ie.symbol_pins(new))
        self.assertEqual(ie.symbol_units(split), {(0, 1), (1, 1), (2, 1)})
        self.assertFalse(ie.same_symbol(split, new))
        self.lib.write_text("(kicad_symbol_lib\n\t(version 20241209)\n\t" + split + "\n)\n")
        sym_of, stats = self.load(schematic(part("R1", "RES", RES)))
        self.assertEqual((sym_of, stats["renamed"]), ({"R1": "RES_2"}, {"RES"}))

    def test_a_name_with_a_quote_is_kept_on_a_second_import(self):
        sch = schematic(part("J1", 'Header 0.1"', RES))
        self.load(sch)
        text = self.lib.read_text()
        sym_of, stats = self.load(sch)
        self.assertEqual(sym_of, {"J1": 'Header_0.1"'})
        self.assertEqual(list(ie.read_symbol_lib(self.lib)), ['Header_0.1"'])
        self.assertEqual(self.lib.read_text(), text)
        self.assertEqual((stats["new"], stats["kept"]), (0, 1))

    def test_a_library_saved_by_kicad_is_read_and_kept(self):
        self.lib.write_text('(kicad_symbol_lib\n\t(version 20241209)\n\t(generator "kicad_symbol_editor")\n\t'
                            + KICAD_SAVED_RES + "\n)\n")
        sym_of, stats = self.load(schematic(part("R1", "RES", RES), part("D1", "LED", LED)))
        lib = ie.read_symbol_lib(self.lib)
        self.assertEqual(sym_of, {"R1": "RES", "D1": "LED"})
        self.assertEqual(lib["RES"], KICAD_SAVED_RES)
        self.assertEqual(ie.block_end(self.lib.read_text(), 0), len(self.lib.read_text().rstrip()))


BOARD = """(kicad_pcb
\t(version 20241229)
\t(footprint "SOT-23"
\t\t(layer "F.Cu")
\t\t(property "Reference" "Q1 (a name with a ( in it" (at 0 0 0) (layer "F.SilkS"))
\t\t(zone
\t\t\t(layer "User.4")
\t\t\t(uuid "pin-outline")
\t\t\t(keepout
\t\t\t\t(tracks allowed)
\t\t\t\t(copperpour not_allowed)
\t\t\t)
\t\t\t(placement
\t\t\t\t(enabled no)
\t\t\t)
\t\t\t(polygon (pts (xy 0 0) (xy 1 0) (xy 1 1)))
\t\t)
\t\t(zone
\t\t\t(layer "User.4")
\t\t\t(uuid "lead-shape")
\t\t\t(fill yes)
\t\t\t(polygon (pts (xy 0 0) (xy 1 0) (xy 1 1)))
\t\t)
\t\t(zone
\t\t\t(layers "F.Cu" "B.Cu")
\t\t\t(uuid "copper-keepout")
\t\t\t(name "keepout")
\t\t\t(keepout (copperpour not_allowed))
\t\t\t(polygon (pts (xy 0 0) (xy 1 0) (xy 1 1)))
\t\t)
\t)
\t(zone
\t\t(net 0)
\t\t(layers "User.3" "User.4")
\t\t(uuid "board-user-keepout")
\t\t(keepout (copperpour not_allowed))
\t\t(polygon (pts (xy 0 0) (xy 1 0) (xy 1 1)))
\t)
\t(zone
\t\t(net 0)
\t\t(layer "B.Cu")
\t\t(uuid "board-copper-keepout")
\t\t(keepout (copperpour not_allowed))
\t\t(polygon (pts (xy 0 0) (xy 1 0) (xy 1 1)))
\t)
)
"""


class Keepouts(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.dir.cleanup()

    def strip(self, name, text):
        path = Path(self.dir.name) / name
        path.write_text(text)
        n = ie.strip_user_keepouts(path)
        out = path.read_text()
        self.assertEqual(ie.block_end(out, 0), len(out.rstrip()))  # still one balanced form
        return n, out

    def test_user_layer_keepouts_go_from_board_and_footprints(self):
        n, out = self.strip("b.kicad_pcb", BOARD)
        self.assertEqual(n, 2)
        for gone in ("pin-outline", "board-user-keepout"):
            self.assertNotIn(gone, out)
        for kept in ("lead-shape", "copper-keepout", "board-copper-keepout", "a name with a ( in it"):
            self.assertIn(kept, out)

    def test_a_footprint_file(self):
        fp = BOARD[BOARD.index('(footprint "SOT-23"'):BOARD.index("\t(zone\n\t\t(net 0)\n\t\t(layers")].rstrip() + "\n"
        n, out = self.strip("SOT-23.kicad_mod", fp)
        self.assertEqual(n, 1)
        self.assertNotIn("pin-outline", out)
        self.assertIn("lead-shape", out)

    def test_nothing_to_strip_leaves_the_file_alone(self):
        n, out = self.strip("plain.kicad_pcb", '(kicad_pcb\n\t(version 20241229)\n)\n')
        self.assertEqual((n, out), (0, '(kicad_pcb\n\t(version 20241229)\n)\n'))


class Sheets(unittest.TestCase):
    def test_easyeda_copy_suffix_and_file_names(self):
        sheets = [{"name": n} for n in ("ESP32+USB copy", "POWER copy", "LEDs PWM top copy copy", "", "POWER", "a/b")]
        self.assertEqual(ie.sheet_files(sheets, "board"), [
            ("ESP32+USB", "esp32-usb.kicad_sch"), ("POWER", "power.kicad_sch"),
            ("LEDs PWM top", "leds-pwm-top.kicad_sch"), ("Sheet 4", "sheet-4.kicad_sch"),
            ("POWER 2", "power-2.kicad_sch"), ("a_b", "a_b.kicad_sch")])

    def test_no_sheet_file_takes_the_root_name(self):
        self.assertEqual(ie.sheet_files([{"name": "Board copy"}], "board"), [("Board", "board_2.kicad_sch")])


class FootprintFields(unittest.TestCase):
    """The schematic's Footprint fields: empty for a part the PCB doesn't have, renamed when the library had another
    footprint of that name."""
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        sch = schematic(part("R1", "RES", RES, "10k", "R0603"), part("R2", "RES", RES, "1k", "R0603"),
                        part("J1", "CONN", LED, "", "HDR-1X2"))
        self.sym_of, self.defs, _ = ie.register_symbols(sch, "proj")
        power = {k: (f'(symbol "power:{k}")', 0, 0) for k in ("GND", "VCC", "PWR_FLAG")}
        meta = dict(title="t", rev="", date="", company="")
        self.path = Path(self.dir.name) / "board.kicad_sch"
        self.links = {ref: dict(link, sheetfile="board.kicad_sch") for ref, link in ie.write_sheet(
            sch["sheets"][0], self.path, "root", "/root", (), self.sym_of, self.defs, power, "board", "proj", meta, [],
            (), pcb_refs={"R1", "R2"}).items()}

    def tearDown(self):
        self.dir.cleanup()

    def footprints(self):
        import re
        text = self.path.read_text()
        return {ref: re.search(r'\(uuid "' + link["uuid"] + r'"\)\n(?:  .*\n)*?  \(property "Footprint" "([^"]*)"',
                               text).group(1) for ref, link in self.links.items()}

    def test_a_schematic_only_part_has_no_footprint(self):
        self.assertEqual(self.footprints(), {"R1": "proj:R0603", "R2": "proj:R0603", "J1": ""})
        self.assertEqual(self.links["J1"]["package"], "HDR-1X2")

    def test_a_renamed_footprint_is_followed(self):
        ie.set_footprint_fields(self.dir.name, self.links, {"R2": "R0603_2"}, "proj")
        self.assertEqual(self.footprints(), {"R1": "proj:R0603", "R2": "proj:R0603_2", "J1": ""})


@unittest.skipUnless(kicad.have_pcbnew(), "needs KiCad's Python")
class Boards(unittest.TestCase):
    def setUp(self):
        import pcbnew
        self.pcbnew = pcbnew
        self.board = pcbnew.BOARD()
        self.nets = {}

    def net(self, name):
        if name not in self.nets:
            self.nets[name] = self.pcbnew.NETINFO_ITEM(self.board, name)
            self.board.Add(self.nets[name])
        return self.nets[name]

    def footprint(self, ref, pads):
        """pads: (number, kind, net or None) with kind smd, pth or npth."""
        pcbnew = self.pcbnew
        fp = pcbnew.FOOTPRINT(self.board)
        fp.SetReference(ref)
        self.board.Add(fp)
        for i, (num, kind, net) in enumerate(pads):
            p = pcbnew.PAD(fp)
            p.SetNumber(num)
            if kind == "smd":
                p.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
                p.SetLayerSet(pcbnew.PAD.SMDMask())
            else:
                p.SetAttribute(pcbnew.PAD_ATTRIB_NPTH if kind == "npth" else pcbnew.PAD_ATTRIB_PTH)
                p.SetLayerSet(pcbnew.PAD.UnplatedHoleMask() if kind == "npth" else pcbnew.PAD.PTHMask())
                p.SetDrillSize(pcbnew.VECTOR2I(pcbnew.FromMM(0.8), pcbnew.FromMM(0.8)))
            p.SetSize(pcbnew.VECTOR2I(pcbnew.FromMM(1.2), pcbnew.FromMM(1.2)))
            p.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(2 * i), 0))
            fp.Add(p)
            if net:
                p.SetNet(self.net(net))
        return fp

    def test_pegs_are_not_compared_and_unconnected_extra_pads_are_listed(self):
        fp = self.footprint("SW1", [("", "npth", None), ("", "npth", None), ("1", "smd", "BOOT"), ("2", "smd", "GND"),
                                    ("3", "pth", None), ("4", "smd", "GND")])
        pins = {("SW1", "1"): "BOOT", ("SW1", "2"): "GND"}
        pads, problems, unpinned = ie.pad_findings(self.pcbnew, fp, pins)
        self.assertEqual(sorted((p.GetNumber(), new or "") for p, new in pads),
                         [("1", "BOOT"), ("2", "GND"), ("3", ""), ("4", "")])
        self.assertEqual(problems, ["SW1 pad 4 has no schematic pin (on the PCB it is on GND)"])
        self.assertEqual(unpinned, ["3"])

    def test_a_loose_pad_with_a_net_is_board_only(self):
        fp = self.footprint("Pad_gge1", [("1", "smd", "GND")])
        self.assertEqual(ie.unlinked_footprint(self.pcbnew, fp, False), (None, ["GND"]))
        self.assertTrue(fp.IsBoardOnly() and fp.IsExcludedFromBOM() and fp.IsExcludedFromPosFiles())

    def test_a_wired_part_without_a_symbol_is_a_problem(self):
        fp = self.footprint("U7", [("1", "smd", "GND"), ("2", "smd", None)])
        problem, nets = ie.unlinked_footprint(self.pcbnew, fp, True)
        self.assertEqual((problem, nets), ("U7 on the PCB has no symbol (and carries nets)", ["GND"]))
        self.assertFalse(fp.IsBoardOnly())
        hole = self.footprint("H1", [("", "npth", None)])
        self.assertEqual(ie.unlinked_footprint(self.pcbnew, hole, True), (None, []))
        self.assertTrue(hole.IsBoardOnly())

    def test_a_footprint_signature_holds_what_a_fab_makes_and_not_where_it_is(self):
        pcbnew = self.pcbnew
        fp = self.footprint("J1", [("1", "pth", None), ("2", "pth", None), ("3", "smd", None)])
        mm = lambda w, h: pcbnew.VECTOR2I(pcbnew.FromMM(w), pcbnew.FromMM(h))
        pads = {p.GetNumber(): p for p in fp.Pads()}
        pads["3"].SetShape(pcbnew.PAD_SHAPE_RECTANGLE)
        pads["3"].SetSize(mm(1.2, 0.6))
        pads["2"].SetOffset(pcbnew.F_Cu, mm(0.2, 0.1))  # copper off-centre on its hole

        def outline(pad, *pts):
            pad.DeletePrimitivesList()
            pad.AddPrimitivePoly(pcbnew.F_Cu, pcbnew.VECTOR_VECTOR2I([mm(x, y) for x, y in pts]), 0, True)
        square = [(-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5)]
        ell = [(-0.5, -0.5), (0.5, -0.5), (0.5, 0), (0, 0), (0, 0.5), (-0.5, 0.5)]  # the same box, a corner cut away
        poly = pcbnew.PAD(fp)  # an EasyEDA polygon pad: a tiny anchor and the copper drawn round it
        poly.SetNumber("4")
        poly.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
        poly.SetLayerSet(pcbnew.PAD.SMDMask())
        poly.SetShape(pcbnew.PAD_SHAPE_CUSTOM)
        poly.SetAnchorPadShape(pcbnew.F_Cu, pcbnew.PAD_SHAPE_CIRCLE)
        poly.SetSize(mm(0.005, 0.005))
        poly.SetPosition(mm(6, 1))
        outline(poly, *ell)
        fp.Add(poly)
        sig = ie.footprint_signature(pcbnew, fp)

        def variant(change=None, angle=0, flip=False):
            c = pcbnew.FOOTPRINT(fp)
            pads = {p.GetNumber(): p for p in c.Pads()}
            if change:
                change(pcbnew, pads)
            c.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(30), pcbnew.FromMM(7)))
            c.SetOrientationDegrees(angle)
            if flip:
                c.Flip(c.GetPosition(), pcbnew.FLIP_DIRECTION_TOP_BOTTOM)
            return ie.same_pads(sig, ie.footprint_signature(pcbnew, c))

        for angle in (0, 33, 90, 180):
            for flip in (False, True):
                self.assertTrue(variant(angle=angle, flip=flip), (angle, flip))
        self.assertFalse(variant(lambda pcbnew, p: outline(p["4"], *square)))
        # the same five points joined another way: a notch on the top, then a notch on the right
        notch_top = [(-1, -1), (1, -1), (1, 1), (0.5, 0.5), (-1, 1)]
        notch_right = [(-1, -1), (1, -1), (0.5, 0.5), (1, 1), (-1, 1)]
        outline(poly, *notch_top)
        sig = ie.footprint_signature(pcbnew, fp)
        for flip in (False, True):
            self.assertTrue(variant(angle=33, flip=flip), flip)
            self.assertFalse(variant(lambda pcbnew, p: outline(p["4"], *notch_right), angle=33, flip=flip), flip)
        self.assertTrue(variant(lambda pcbnew, p: outline(p["4"], *notch_top[2:], *notch_top[:2])))  # another start
        outline(poly, *ell)
        sig = ie.footprint_signature(pcbnew, fp)
        self.assertFalse(variant(lambda pcbnew, p: p["1"].SetOffset(pcbnew.F_Cu, mm(0.3, 0))))
        turn = lambda deg: (lambda pcbnew, p: p["3"].SetFPRelativeOrientation(pcbnew.EDA_ANGLE(deg, pcbnew.DEGREES_T)))
        self.assertTrue(variant(turn(180)))   # a rectangle turned half round is the same pad
        self.assertTrue(variant(lambda pcbnew, p: (turn(90)(pcbnew, p), p["3"].SetSize(mm(0.6, 1.2)))))
        self.assertFalse(variant(turn(45)))
        self.assertFalse(variant(lambda pcbnew, p: p["1"].SetDrillSize(mm(1.0, 1.0))))
        self.assertFalse(variant(lambda pcbnew, p: p["2"].SetAttribute(pcbnew.PAD_ATTRIB_NPTH)))

        def back(w, h, shape=pcbnew.PAD_SHAPE_RECTANGLE):  # other copper on the back: a front/inner/back padstack
            def change(pcbnew, p):
                p["1"].Padstack().SetMode(pcbnew.PADSTACK.MODE_FRONT_INNER_BACK)
                p["1"].SetShape(pcbnew.B_Cu, shape)
                p["1"].SetSize(pcbnew.B_Cu, mm(w, h))
            return change
        back(1.6, 1.0)(pcbnew, pads)
        sig = ie.footprint_signature(pcbnew, fp)
        for angle in (0, 33, 90):
            for flip in (False, True):
                self.assertTrue(variant(angle=angle, flip=flip), (angle, flip))
        self.assertFalse(variant(back(2.0, 1.0)))
        self.assertFalse(variant(back(1.6, 1.0, pcbnew.PAD_SHAPE_OVAL)))
        self.assertFalse(variant(back(1.0, 1.6)))  # turned a quarter round

        def bottom(pcbnew, p):
            layers = pcbnew.LSET()
            for layer in (pcbnew.B_Cu, pcbnew.B_Mask, pcbnew.B_Paste):
                layers.AddLayer(layer)
            p["3"].SetLayerSet(layers)
        self.assertFalse(variant(bottom))  # a pad on the far side of the footprint (an EasyEDA test point)
        self.assertFalse(variant(bottom, flip=True))

    def edge(self, *pts, arc=False):
        pcbnew = self.pcbnew
        s = pcbnew.PCB_SHAPE(self.board)
        P = [pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y)) for x, y in pts]
        if arc:
            s.SetShape(pcbnew.SHAPE_T_ARC)
            s.SetArcGeometry(*P)
        else:
            s.SetShape(pcbnew.SHAPE_T_SEGMENT)
            s.SetStart(P[0])
            s.SetEnd(P[1])
        s.SetLayer(pcbnew.Edge_Cuts)
        s.SetWidth(pcbnew.FromMM(0.1))
        self.board.Add(s)
        return s

    def test_outline_gaps_close_and_short_segments_stay(self):
        from collections import Counter
        pcbnew = self.pcbnew
        self.edge((0, 0), (5, 0))
        short = self.edge((5, 0), (5.04, 0))                    # 0.04 mm long: its ends are not a gap
        self.edge((5.04, 0), (10, 0))
        arc = self.edge((10, 0), (12, 2.5), (10, 5), arc=True)
        top = self.edge((10.02, 5), (0, 5))                       # 0.02 mm from the arc's end
        self.edge((0.0127, 5), (0, 0))                            # 0.0127 mm from the line before
        far = self.edge((20, 0), (25, 0))
        self.edge((25.2, 0), (30, 0))                             # 0.2 mm: a real opening, left alone
        arc_end = arc.GetEnd()
        gaps = ie.close_outline(pcbnew, self.board)
        self.assertEqual(sorted(round(g[0], 4) for g in gaps), [0.0127, 0.02])
        self.assertEqual(arc.GetEnd(), arc_end)                   # the line moved, not the arc
        self.assertEqual(top.GetStart(), arc_end)
        self.assertAlmostEqual(pcbnew.ToMM(short.GetLength()), 0.04, places=6)
        self.assertEqual(pcbnew.ToMM(far.GetEnd().x), 25)
        ends = Counter((p.x, p.y) for s in self.board.GetDrawings() for p in (s.GetStart(), s.GetEnd())
                       if pcbnew.ToMM(p.x) < 15)
        self.assertTrue(all(c == 2 for c in ends.values()), ends)


if __name__ == "__main__":
    unittest.main()
