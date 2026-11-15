"""`pcbkit.schematic` placing by pin: `pins`, `put`, `step`, `chain`, on a symbol library written here.

  python3 tests/test_schematic_place.py     # plain Python: pin positions come from the library file, not konnect

The library has a resistor like KiCad's Device:R (pins 1 and 2 at +-3.81 mm on the y axis, pin 1 up), a part derived
from it, and a regulator with pins on three sides. It is reached through a sym-lib-table in the sheet folder, the
way a project's own library is. `pcbkit.schematic.pins` was checked against konnect's placement of 889 pins on
esp32-s3-ha-node and siebenuhr (2026-11-16); these cases hold the arithmetic.
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pcbkit import schematic as sch  # noqa: E402

LIB = """(kicad_symbol_lib (version 20241209) (generator "test")
\t(symbol "R"
\t\t(property "Reference" "R" (at 2.032 0 90))
\t\t(symbol "R_0_1" (rectangle (start -1.016 -2.54) (end 1.016 2.54)))
\t\t(symbol "R_1_1" (pin passive line (at 0 3.81 270) (length 1.27) (name "~") (number "1"))
\t\t\t(pin passive line (at 0 -3.81 90) (length 1.27) (name "~") (number "2")))
\t)
\t(symbol "R_Small"
\t\t(extends "R")
\t\t(property "Reference" "R" (at 2.032 0 90))
\t)
\t(symbol "REG"
\t\t(property "Value" "a (tricky \\"name\\")" (at 0 0 0))
\t\t(symbol "REG_0_1" (rectangle (start -5.08 5.08) (end 5.08 -5.08)))
\t\t(symbol "REG_1_1" (pin power_in line (at -7.62 2.54 0) (length 2.54) (name "VIN") (number "1"))
\t\t\t(pin power_in line (at 0 -7.62 90) (length 2.54) (name "GND") (number "2"))
\t\t\t(pin power_out line (at 7.62 2.54 180) (length 2.54) (name "SW") (number "3")))
\t)
)
"""


def R(ref, a, b, **kw):
    return dict(ref=ref, lib="t:R", x=0, y=0, rot=0, pins={"1": a, "2": b}, **kw)


class Place(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        with open(os.path.join(cls.dir.name, "t.kicad_sym"), "w") as f:
            f.write(LIB)
        with open(os.path.join(cls.dir.name, "sym-lib-table"), "w") as f:
            f.write('(sym_lib_table (version 7)\n (lib (name "t")(type "KiCad")(uri "${KIPRJMOD}/t.kicad_sym")(options "")(descr "")))\n')
        sch.configure(cls.dir.name)

    @classmethod
    def tearDownClass(cls):
        cls.dir.cleanup()

    def test_pins_follow_the_rotation(self):
        r = dict(R("R1", "A", "B"), x=50.8, y=50.8)
        self.assertEqual(sch.pins(r), {"1": (50.8, 46.99), "2": (50.8, 54.61)})      # pin 1 up
        self.assertEqual(sch.pins(dict(r, rot=90)), {"1": (46.99, 50.8), "2": (54.61, 50.8)})   # a quarter turn: pin 1 left
        self.assertEqual(sch.pin(dict(r, rot=180), 1), (50.8, 54.61))

    def test_pins_follow_the_mirror(self):
        u = dict(ref="U1", lib="t:REG", x=100, y=100, rot=0, pins={})
        self.assertEqual(sch.pins(u), {"1": (92.38, 97.46), "2": (100, 107.62), "3": (107.62, 97.46)})
        self.assertEqual(sch.pin(dict(u, mirror="y"), 1), (107.62, 97.46))   # left and right swapped
        self.assertEqual(sch.pin(dict(u, mirror="x"), 2), (100, 92.38))      # top and bottom swapped

    def test_a_derived_symbol_has_its_parents_pins(self):
        r = dict(ref="R1", lib="t:R_Small", x=0, y=0, pins={})
        self.assertEqual(sch.pins(r), {"1": (0, -3.81), "2": (0, 3.81)})

    def test_put_lands_the_pin_and_turns_the_body(self):
        for toward, pin2 in (("down", (100, 107.62)), ("up", (100, 92.38)), ("right", (107.62, 100)), ("left", (92.38, 100))):
            r = sch.put(R("R1", "A", "B"), "1", (100, 100), toward=toward)
            self.assertEqual((sch.pin(r, 1), sch.pin(r, 2)), ((100, 100), pin2), toward)
        r = sch.put(R("R1", "A", "B"), "2", (100, 100), toward="right")   # by the other pin: pin 1 ends up on the right
        self.assertEqual(sch.pin(r, 1), (107.62, 100))

    def test_put_without_toward_keeps_the_rotation(self):
        r = sch.put(dict(R("R1", "A", "B"), rot=90), "2", (100, 100))
        self.assertEqual((r["rot"], sch.pin(r, 1)), (90, (92.38, 100)))

    def test_put_turns_a_part_with_pins_on_three_sides(self):
        u = sch.put(dict(ref="U1", lib="t:REG", x=0, y=0, pins={}), "1", (50.8, 50.8), toward="right")
        self.assertEqual((u["rot"], sch.pin(u, 3)), (0, (66.04, 50.8)))

    def test_step(self):
        self.assertEqual(sch.step((10, 20), "right"), (15.08, 20))
        self.assertEqual(sch.step((10, 20), "up", 2.54), (10, 17.46))

    def test_chain_lines_parts_up_by_their_shared_nets(self):
        a, b, c = R("R1", "IN", "N1"), R("R2", "N2", "N1"), R("R3", "N2", "OUT")   # R2 is entered by its pin 2
        end = sch.chain((50.8, 50.8), [a, b, c], toward="right", gap=5.08)
        self.assertEqual((sch.pin(a, 1), sch.pin(a, 2)), ((50.8, 50.8), (58.42, 50.8)))
        self.assertEqual((sch.pin(b, 2), sch.pin(b, 1)), ((63.5, 50.8), (71.12, 50.8)))
        self.assertEqual((sch.pin(c, 1), end), ((76.2, 50.8), (83.82, 50.8)))
        # and the planner joins what the chain lined up
        nets = {"N1": [("R1", "2", sch.pin(a, 2)), ("R2", "2", sch.pin(b, 2))], "N2": [("R2", "1", sch.pin(b, 1)), ("R3", "1", sch.pin(c, 1))]}
        points = {p: n for n, pins in nets.items() for _, _, p in pins}
        plan = sch.plan_wires(nets, [], points, [], 25.4)
        self.assertEqual([len(plan[n]["wires"]) for n in ("N1", "N2")], [1, 1])

    def test_chain_through_a_part_with_more_pins(self):
        u = dict(ref="U1", lib="t:REG", x=0, y=0, pins={"1": "VIN", "2": "GND", "3": "SW"})
        end = sch.chain((50.8, 50.8), [R("R1", "IN", "VIN"), (u, "1", "3"), R("R2", "SW", "OUT")], toward="right")
        self.assertEqual(sch.pin(u, 1), (63.5, 50.8))
        self.assertEqual(sch.pin(u, 3), (78.74, 50.8))
        self.assertEqual(end, (91.44, 50.8))

    def test_an_unknown_symbol_or_library_says_which(self):
        with self.assertRaises(KeyError):
            sch.pins(dict(ref="X", lib="t:NOPE", x=0, y=0, pins={}))
        with self.assertRaises(FileNotFoundError):
            sch.pins(dict(ref="X", lib="no_such_library:R", x=0, y=0, pins={}))


if __name__ == "__main__":
    unittest.main()
