"""`pcbkit.schematic.block`: a block placed from the roles of its parts, its rails, and `frames`.

  python3 tests/test_schematic_block.py     # plain Python: no konnect, the sheet for frames() is written here

The parts come from the library of test_schematic_place.py: a resistor with pins 7.62 mm apart (it stands in for
every two-pin part) and a regulator with VIN on the left, SW on the right in the same row, and a pin below.
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pcbkit import schematic as sch  # noqa: E402
from pcbkit.checks import sch_lint  # noqa: E402
from test_schematic_place import LIB, R  # noqa: E402
from test_sch_lint import LIB as SHEET_LIB, part as placed  # noqa: E402

AT = (50.8, 50.8)


def REG(**nets):
    return dict(ref="U1", lib="t:REG", x=0, y=0, rot=0, pins={"1": nets.get("vin", "VIN"), "2": nets.get("low", "GND"), "3": "SW"})


def ends(c):
    return sch.pin(c, 1), sch.pin(c, 2)


class Lib(unittest.TestCase):
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


class Blocks(Lib):
    def test_the_path_makes_room_for_the_shunts_on_its_nodes(self):
        u, cin, d, rs = REG(), R("C1", "VIN", "GND"), R("D1", "SW", "GND"), R("R9", "SW", "OUT")
        b = sch.block("buck", AT, [(u, "1", "3"), rs], shunt={"VIN": [cin], "SW": [d]})
        self.assertEqual(ends(cin), ((50.8, 50.8), (50.8, 58.42)))          # the input capacitor first, hanging down
        self.assertEqual((sch.pin(u, 1), sch.pin(u, 3)), ((63.5, 50.8), (78.74, 50.8)))   # one place on
        self.assertEqual(ends(d), ((91.44, 50.8), (91.44, 58.42)))          # one place after SW
        self.assertEqual(ends(rs), ((104.14, 50.8), (111.76, 50.8)))        # and the path carries on a place later
        self.assertEqual([p["ref"] for p in b.parts], ["C1", "U1", "D1", "R9"])
        self.assertEqual(b.rails, [dict(net="VIN", pts=[(50.8, 50.8), (63.5, 50.8)], open="left"),
                                   dict(net="SW", pts=[(78.74, 50.8), (91.44, 50.8), (104.14, 50.8)], open=None)])
        self.assertEqual(b.box, (49.78, 48.26, 111.76, 60.96))        # the drawings and the pin ends

    def test_without_shunts_it_is_a_chain(self):
        a, c = R("R1", "IN", "N1"), R("R2", "N1", "OUT")
        b = sch.block("", AT, [a, c])
        self.assertEqual((ends(a), ends(c), b.rails), (((50.8, 50.8), (58.42, 50.8)), ((63.5, 50.8), (71.12, 50.8)), []))

    def test_to_ground_hangs_down_and_to_a_supply_up(self):
        down, up, said = R("R2", "OUT", "GND"), R("R3", "+3V3", "OUT"), R("R4", "OUT", "X")
        b = sch.block("", AT, [R("R1", "IN", "OUT")], shunt={"OUT": [down, up, (said, "up")]})
        self.assertEqual(ends(down), ((71.12, 50.8), (71.12, 58.42)))
        self.assertEqual(ends(up), ((71.12, 43.18), (71.12, 50.8)))         # above the same point
        self.assertEqual(ends(said), ((83.82, 50.8), (83.82, 43.18)))       # the next place up
        self.assertEqual(b.rails, [dict(net="OUT", pts=[(58.42, 50.8), (71.12, 50.8), (83.82, 50.8)], open="right")])

    def test_a_list_hangs_in_series(self):
        top, bottom = R("R8", "OUT", "FB"), R("R9", "GND", "FB")            # R9 is entered by its pin 2
        sch.block("", AT, [R("R1", "IN", "OUT")], shunt={"OUT": [[top, bottom]]})
        self.assertEqual(ends(top), ((71.12, 50.8), (71.12, 58.42)))
        self.assertEqual(ends(bottom), ((71.12, 71.12), (71.12, 63.5)))

    def test_a_path_that_runs_left(self):
        a, c = R("R1", "IN", "OUT"), R("C1", "OUT", "GND")
        b = sch.block("", AT, [a], shunt={"OUT": [c]}, toward="left")
        self.assertEqual((ends(a), ends(c)), (((50.8, 50.8), (43.18, 50.8)), ((30.48, 50.8), (30.48, 58.42))))
        self.assertEqual(b.rails[0]["open"], "left")

    def test_a_net_that_is_not_on_the_path_is_an_error(self):
        with self.assertRaisesRegex(AssertionError, "no path part enters or leaves on FB"):
            sch.block("", AT, [R("R1", "IN", "OUT")], shunt={"FB": [R("R2", "FB", "GND")]})
        with self.assertRaisesRegex(AssertionError, "R2 has no pin on OUT"):
            sch.block("", AT, [R("R1", "IN", "OUT")], shunt={"OUT": [R("R2", "FB", "GND")]})

    def test_a_tap_hangs_parts_from_a_pin_off_the_path(self):
        u, r, z = REG(low="G"), R("R5", "G", "GND"), R("D2", "VIN", "G")
        b = sch.block("", AT, [(u, "1", "3")], taps=[(u, "2", [r, z])])
        self.assertEqual(sch.pin(u, 2), (58.42, 60.96))                     # the pin points down
        self.assertEqual(ends(r), ((58.42, 68.58), (58.42, 76.2)))          # to ground: straight on, 7.62 mm out
        self.assertEqual(ends(z), ((66.04, 68.58), (58.42, 68.58)))         # the other one to the side
        self.assertEqual(b.rails, [dict(net="G", pts=[(58.42, 60.96), (58.42, 68.58)], open=None)])
        u, r = REG(low="G"), R("R5", "G", "X")
        b = sch.block("", AT, [(u, "1", "3")], taps=[(u, "2", [(r, "left")], 10.16)])
        self.assertEqual(ends(r), ((58.42, 71.12), (50.8, 71.12)))
        self.assertEqual(b.rails[0]["open"], "down")

    def test_across_a_part_that_lies_flat(self):
        r, c = R("R1", "IN", "OUT"), R("C2", "OUT", "IN")
        b = sch.block("", AT, [r], across=[(r, [c])])
        self.assertEqual(ends(c), ((58.42, 43.18), (50.8, 43.18)))          # above it, each pin over the pin on its net
        self.assertEqual([p["ref"] for p in b.parts], ["R1", "C2"])

    def test_across_a_part_that_hangs_takes_the_next_place(self):
        top, bottom, ff, cout = R("R8", "OUT", "FB"), R("R9", "FB", "GND"), R("C25", "FB", "OUT"), R("C9", "OUT", "GND")
        b = sch.block("", AT, [R("R1", "IN", "OUT")], shunt={"OUT": [[top, bottom], cout]}, across=[(top, [ff])])
        self.assertEqual(ends(top), ((71.12, 50.8), (71.12, 58.42)))
        self.assertEqual(ends(ff), ((83.82, 58.42), (83.82, 50.8)))         # beside it, turned so OUT is on the rail
        self.assertEqual(sch.pin(cout, 1), (96.52, 50.8))                   # and the next capacitor a place later
        self.assertEqual(sorted(b.rails[0]["pts"]), [(58.42, 50.8), (71.12, 50.8), (83.82, 50.8), (96.52, 50.8)])
        with self.assertRaisesRegex(AssertionError, "C1 is not on the two nets of R8"):
            sch.block("", AT, [R("R1", "IN", "OUT")], shunt={"OUT": [top]}, across=[(top, [R("C1", "OUT", "GND")])])


class Rails(Lib):
    def placed(self, parts):
        L = {(c["ref"], n): p + (0,) for c in parts for n, p in sch.pins(c).items()}
        return L, {(c["ref"], n): net for c in parts for n, net in c["pins"].items()}

    def test_a_supply_rail_runs_to_a_free_end_for_its_symbol(self):
        u, cin, d = REG(vin="+5V"), R("C1", "+5V", "GND"), R("D1", "SW", "GND")
        b = sch.block("buck", AT, [(u, "1", "3")], shunt={"+5V": [cin], "SW": [d]})
        supply, sw = sch._rails([b], *self.placed(b.parts))
        self.assertEqual(supply["wires"], [((45.72, 50.8), (50.8, 50.8)), ((50.8, 50.8), (63.5, 50.8))])
        self.assertEqual((supply["open"], sorted(supply["pins"])), ((45.72, 50.8), [("C1", "1"), ("U1", "1")]))
        self.assertEqual((sw["wires"], sw["open"]), ([((78.74, 50.8), (91.44, 50.8))], None))   # a signal stops at its last pin

    def test_a_rail_over_another_nets_pin_is_refused(self):
        b = sch.block("buck", AT, [R("R1", "IN", "OUT")], shunt={"OUT": [R("C1", "OUT", "GND"), R("C2", "OUT", "GND")]})
        stray = sch.put(R("R7", "A", "B"), "1", (76.2, 50.8), toward="up")
        with self.assertRaisesRegex(AssertionError, "block 'buck': the OUT rail runs over R7.1"):
            sch._rails([b], *self.placed(b.parts + [stray]))

    def test_the_name_goes_where_it_is_clear(self):
        segs = [((50.8, 50.8), (63.5, 50.8)), ((63.5, 50.8), (71.12, 50.8))]
        self.assertEqual(sch._name_spot("FB", segs, []), (52.07, 50.8, 0))                  # the longest stretch
        self.assertEqual(sch._name_spot("FB", segs, [(51, 48, 56, 50.5)]), (64.77, 50.8, 0))   # a value in the way there
        self.assertEqual(sch._name_spot("FB", [((50.8, 50.8), (50.8, 63.5))], []), (50.8, 62.23, 90))


class Frames(unittest.TestCase):
    def test_a_frame_takes_in_the_parts_their_fields_and_their_power_symbols(self):
        gnd = ('(symbol (lib_id "Device:R") (at 60 60 0) (unit 1) (property "Reference" "#PWR01" (at 60 60 0) (hide yes))'
               ' (property "Value" "GND" (at 60 66 0) (effects (font (size 1.27 1.27)))))')   # its pin 1 is on R2's pin 2
        text = '(kicad_sch (version 20260306) (generator "eeschema") (paper "A4") %s %s\n)\n' % (
            SHEET_LIB, " ".join([placed("R1", 50, 50), placed("R2", 60, 52.38), placed("R3", 120, 50), gnd]))
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.kicad_sch")
            with open(path, "w") as f:
                f.write(text)
            box, = sch.frames(path, [sch.Block('The "divider"', [dict(ref="R1"), dict(ref="R2")])])
            page = sch_lint.Sheet(path)
            with open(path) as f:
                out = f.read()
        self.assertEqual(box, (45.72, 43.18, 68.58, 69.85))                 # R3 stays outside, the GND symbol is inside
        self.assertTrue(all(abs(v / 1.27 - round(v / 1.27)) < 1e-6 for v in box))
        self.assertIn("(start 45.72 43.18)", out)
        self.assertIn("(stroke (width 0) (type dash))", out)
        self.assertEqual([t["at"] for t in page.texts if t["text"] == 'The "divider"'], [(45.72, 42.18)])
        self.assertEqual(len(page.symbols), 4)


if __name__ == "__main__":
    unittest.main()
