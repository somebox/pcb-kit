"""`pcbkit sch-lint`: what it finds on sheets written here as text, and what it leaves alone.

  python3 tests/test_sch_lint.py     # plain Python: the check reads the file, it needs no KiCad

Each sheet holds a few resistors (a body 2 x 5 mm, pins at +-3.81 mm, like KiCad's Device:R) placed the way the
projects' generated sheets place them: wired pin to pin, joined by a label on each pin, or with a field pushed
onto a neighbour.
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pcbkit.checks import sch_lint  # noqa: E402

LIB = """(lib_symbols (symbol "Device:R"
  (symbol "R_0_1" (rectangle (start -1.016 -2.54) (end 1.016 2.54)))
  (symbol "R_1_1" (pin passive line (at 0 3.81 270) (length 1.27) (name "~") (number "1"))
                  (pin passive line (at 0 -3.81 90) (length 1.27) (name "~") (number "2")))))"""


def part(ref, x, y, rot=0, ref_at=None, val_at=None, val="10k"):
    """A resistor; unrotated, pin 1 is at (x, y - 3.81) and pin 2 at (x, y + 3.81)."""
    rx, ry = ref_at or (x + 2.54, y - 1.27)
    vx, vy = val_at or (x + 2.54, y + 1.27)
    return ('(symbol (lib_id "Device:R") (at %g %g %d) (unit 1)'
            ' (property "Reference" "%s" (at %g %g 0) (effects (font (size 1.27 1.27)) (justify left)))'
            ' (property "Value" "%s" (at %g %g 0) (effects (font (size 1.27 1.27)) (justify left)))'
            ' (property "Footprint" "R_0402" (at %g %g 0) (hide yes) (effects (font (size 1.27 1.27)))))'
            % (x, y, rot, ref, rx, ry, val, vx, vy, x, y))


def label(name, x, y, kind="label"):
    return '(%s "%s" (at %g %g 0) (effects (font (size 1.27 1.27)) (justify left bottom)))' % (kind, name, x, y)


def wire(x1, y1, x2, y2):
    return "(wire (pts (xy %g %g) (xy %g %g)))" % (x1, y1, x2, y2)


def lint(*items, title="Test", paper='"A4"', extra_lib="", **kw):
    tb = '(title_block (title "%s"))' % title if title else ""
    text = '(kicad_sch (version 20260306) (generator "eeschema") (paper %s) %s %s %s)' % (paper, tb, LIB[:-1] + extra_lib + ")", " ".join(items))
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "t.kicad_sch")
        with open(path, "w") as f:
            f.write(text)
        found, stats = sch_lint.lint(sch_lint.Sheet(path), **kw)
    return [t for t, _, _ in found], stats, found


class Nets(unittest.TestCase):
    def test_wired_pin_to_pin_is_clean(self):
        kinds, stats, _ = lint(part("R1", 50, 50), part("R2", 50, 70), wire(50, 53.81, 50, 66.19))
        self.assertEqual(kinds, [])
        self.assertEqual((stats["wired"], stats["label_hop"], stats["label_island"]), (1, 0, 0))

    def test_labels_between_near_pins_are_a_hop(self):
        kinds, stats, found = lint(part("R1", 50, 50), part("R2", 60, 50), label("FB", 50, 53.81), label("FB", 60, 53.81))
        self.assertEqual(kinds, ["label-hop"])
        self.assertIn("R1.2 and R2.2 are 10.0 mm apart", found[0][2])
        self.assertEqual(stats["label_hop"], 1)

    def test_labels_between_far_pins_are_an_island(self):
        kinds, _, _ = lint(part("R1", 50, 50), part("R2", 150, 50), label("FB", 50, 53.81), label("FB", 150, 53.81))
        self.assertEqual(kinds, ["label-island"])

    def test_a_net_that_leaves_the_sheet_keeps_its_labels(self):
        kinds, stats, _ = lint(part("R1", 50, 50), part("R2", 60, 50), label("SDA", 50, 53.81),
                               label("SDA", 60, 53.81, "hierarchical_label"))
        self.assertEqual(kinds, [])
        self.assertEqual(stats["local_nets"], 0)

    def test_a_net_that_goes_into_a_child_sheet_keeps_its_labels(self):
        child = '(sheet (at 100 40) (size 20 20) (property "Sheetname" "Child") (pin "SDA_IN" input (at 100 53.81 180)))'
        kinds, stats, _ = lint(part("R1", 50, 50), part("R2", 60, 50), label("SDA", 50, 53.81), label("SDA", 60, 53.81),
                               wire(60, 53.81, 100, 53.81), child)
        self.assertEqual((kinds, stats["local_nets"]), ([], 0))

    def test_a_local_name_on_a_wire_that_leaves_the_sheet_keeps_its_labels(self):
        # FB names two pieces; one of them also carries the hierarchical label SENSE
        kinds, stats, _ = lint(part("R1", 50, 50), part("R2", 60, 50), label("FB", 50, 53.81), label("FB", 60, 53.81),
                               wire(60, 53.81, 70, 53.81), label("SENSE", 70, 53.81, "hierarchical_label"))
        self.assertEqual((kinds, stats["local_nets"]), ([], 0))

    def test_a_supply_is_not_a_local_net(self):
        gnd = ('(symbol (lib_id "power:GND") (at 55 60 0) (unit 1) (property "Reference" "#PWR01" (at 55 60 0) (hide yes))'
               ' (property "Value" "GND" (at 55 63 0) (effects (font (size 1.27 1.27)))))')
        lib = '(symbol "power:GND" (symbol "GND_1_1" (pin power_in line (at 0 0 270) (length 0) (name "~") (number "1"))))'
        both = [part("R1", 50, 50), part("R2", 60, 50), wire(50, 53.81, 60, 53.81), wire(55, 53.81, 55, 60), gnd]
        _, stats, _ = lint(*both, extra_lib=lib)
        self.assertEqual((stats["wired"], stats["local_nets"]), (0, 0))
        _, stats, _ = lint(*both[:3])                                       # the same two pins with no supply on them
        self.assertEqual(stats["wired"], 1)

    def test_an_arc_is_bounded_where_it_bulges(self):
        pts = sch_lint._arc_points((1, 0), (0, 1), (-1, 0))                  # the upper half of a circle
        self.assertEqual((min(p[1] for p in pts), max(p[1] for p in pts)), (0, 1))
        pts = sch_lint._arc_points((0.866, 0.5), (-0.5, 0.866), (-0.866, -0.5))   # 30 to 210 degrees, through 120
        self.assertEqual((max(p[1] for p in pts), min(p[0] for p in pts), max(p[0] for p in pts)), (1.0, -1.0, 0.866))
        self.assertEqual(len(sch_lint._arc_points((0, 0), (1, 0), (2, 0))), 3)

    def test_a_fan_out_keeps_its_labels(self):
        parts = [part("R%d" % i, 40 + 10 * i, 50) for i in range(6)]
        kinds, _, _ = lint(*parts, *[label("EN", 40 + 10 * i, 53.81) for i in range(6)])
        self.assertEqual(kinds, [])

    def test_a_rotated_part_is_wired_at_its_turned_pins(self):
        # turned 90, the pins lie on the x axis, 3.81 mm either side of the part
        kinds, stats, _ = lint(part("R1", 50, 50, rot=90, ref_at=(48, 46), val_at=(48, 54)),
                               part("R2", 70, 50, rot=90, ref_at=(68, 46), val_at=(68, 54)),
                               wire(53.81, 50, 66.19, 50))
        self.assertEqual(kinds, [])
        self.assertEqual(stats["wired"], 1)


class Texts(unittest.TestCase):
    def test_value_on_a_neighbours_reference(self):
        kinds, _, found = lint(part("R1", 50, 50), part("R2", 60, 50, ref_at=(52.8, 51.27)))
        self.assertIn("text-overlap", kinds)
        self.assertTrue(any("R1 value '10k' over R2 reference 'R2'" in m for _, _, m in found), found)

    def test_value_inside_its_own_body_is_kicads_resistor(self):
        kinds, _, _ = lint(part("R1", 50, 50, val_at=(49.5, 50)))
        self.assertEqual(kinds, [])

    def test_text_across_another_symbol(self):
        kinds, _, _ = lint(part("R1", 50, 50), part("R2", 56, 50, ref_at=(49.5, 50), val_at=(58.5, 51.27)))
        self.assertIn("text-on-symbol", kinds)

    def test_reference_far_from_its_symbol(self):
        kinds, _, found = lint(part("R1", 50, 50, ref_at=(80, 50)))
        self.assertEqual(kinds, ["field-far"])

    def test_wire_through_a_text_and_through_a_symbol(self):
        kinds, _, _ = lint(part("R1", 50, 50), wire(45, 50, 60, 50))
        self.assertIn("wire-on-symbol", kinds)
        kinds, _, _ = lint(part("R1", 50, 50), wire(52, 48.73, 60, 48.73))
        self.assertEqual(kinds, ["text-on-wire"])

    def test_a_label_sits_on_its_wire(self):
        kinds, _, _ = lint(part("R1", 50, 50), wire(50, 53.81, 70, 53.81), label("OUT", 55, 53.81))
        self.assertEqual(kinds, [])

    def test_a_portrait_page_is_its_size_turned(self):
        kinds, _, _ = lint(part("R1", 250, 50), paper='"A4" portrait')      # 210 wide
        self.assertEqual(sorted(set(kinds)), ["off-page"])
        kinds, _, _ = lint(part("R1", 100, 250), paper='"A4" portrait')     # 297 high
        self.assertEqual(kinds, [])

    def test_off_page_and_no_title(self):
        kinds, _, _ = lint(part("R1", 400, 50), title="")
        self.assertEqual(sorted(set(kinds)), ["off-page", "title-block"])


if __name__ == "__main__":
    unittest.main()
