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

    def test_a_row_can_start_at_a_source(self):
        u, r = REG(), R("R1", "SW", "OUT")
        b = sch.block("", AT, [(u, None, "3"), r])
        self.assertEqual((sch.pin(u, 3), sch.pin(u, 1)), ((50.8, 50.8), (35.56, 50.8)))   # the part lies before its pin
        self.assertEqual(ends(r), ((58.42, 50.8), (66.04, 50.8)))           # after room for the name SW
        self.assertEqual(b.rails, [])

    def test_a_signal_between_two_path_parts_gets_room_for_its_name(self):
        a, c = R("R1", "IN", "A_LONG_NET_NAME"), R("R2", "A_LONG_NET_NAME", "OUT")
        sch.block("", AT, [a, c])
        self.assertEqual(round(sch.pin(c, 1)[0] - sch.pin(a, 2)[0], 2), 22.86)   # 15 capitals and a margin, on the 2.54 grid
        a, c = R("R1", "IN", "+5V"), R("R2", "+5V", "OUT")
        sch.block("", AT, [a, c])
        self.assertEqual(round(sch.pin(c, 1)[0] - sch.pin(a, 2)[0], 2), 5.08)   # a supply has a symbol, not a name

    def test_a_one_pin_part_hangs_too(self):
        flag = dict(ref="#FLG01", lib="t:R", x=0, y=0, rot=0, pins={"1": "+5V"})
        b = sch.block("", AT, [R("R1", "IN", "+5V")], shunt={"+5V": [R("C1", "+5V", "GND"), flag]})
        self.assertEqual(sch.pin(flag, 1), (71.12, 50.8))                   # over the capacitor: a supply goes up
        self.assertEqual(sch.pin(flag, 2), (71.12, 43.18))
        self.assertEqual(b.rails[0]["pts"], [(58.42, 50.8), (71.12, 50.8)])

    def test_a_sense_pin_names_the_net_to_wire_however_far(self):
        u = REG(low="FB")
        b = sch.block("", AT, [(u, "1", "3")], shunt={"SW": [[R("R8", "SW", "FB"), R("R9", "FB", "GND")]]}, sense=[(u, 2)])
        self.assertEqual((b.far, sch.block("", AT, [(REG(), "1", "3")]).far), ({"FB"}, set()))

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
        self.assertEqual(b.rails, [dict(net="IN", pts=[(50.8, 50.8), (50.8, 43.18)], open=None),      # joined pin to pin
                                   dict(net="OUT", pts=[(58.42, 50.8), (58.42, 43.18)], open=None)])

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
        self.assertEqual(supply["wires"], [((43.18, 50.8), (50.8, 50.8)), ((50.8, 50.8), (63.5, 50.8))])
        self.assertEqual((supply["open"], sorted(supply["pins"])), ((43.18, 50.8), [("C1", "1"), ("U1", "1")]))
        self.assertEqual((sw["wires"], sw["open"]), ([((78.74, 50.8), (91.44, 50.8))], None))   # a signal stops at its last pin

    def test_a_rail_over_another_nets_pin_is_refused(self):
        b = sch.block("buck", AT, [R("R1", "IN", "OUT")], shunt={"OUT": [R("C1", "OUT", "GND"), R("C2", "OUT", "GND")]})
        stray = sch.put(R("R7", "A", "B"), "1", (76.2, 50.8), toward="up")
        with self.assertRaisesRegex(AssertionError, "block 'buck': the OUT rail runs over R7.1"):
            sch._rails([b], *self.placed(b.parts + [stray]))

    def test_a_rail_through_a_part_is_refused(self):
        b = sch.block("buck", AT, [R("R1", "IN", "OUT")], shunt={"OUT": [R("C1", "OUT", "GND"), R("C2", "OUT", "GND")]})
        stray = dict(R("R7", "A", "B"), x=77.47, y=50.8)                    # upright, its middle on the rail
        L, nets = self.placed(b.parts + [stray])
        with self.assertRaisesRegex(AssertionError, "block 'buck': the OUT rail runs through R7"):
            sch._rails([b], L, nets, b.parts + [stray])

    def test_a_wire_of_the_callers_that_meets_a_rail_is_refused(self):
        rail = dict(net="OUT", wires=[((50.8, 50.8), (76.2, 50.8))], at=[(50.8, 50.8), (63.5, 50.8), (76.2, 50.8)])
        sch._rails_clear([rail], [(63.5, 50.8, 63.5, 40.64), (10.16, 10.16, 20.32, 10.16)])   # from a pin of the rail; elsewhere
        for w in ((58.42, 40.64, 58.42, 50.8), (58.42, 45.72, 58.42, 55.88)):                # ends on the rail; crosses it
            with self.assertRaisesRegex(AssertionError, "the OUT rail meets the wire"):
                sch._rails_clear([rail], [w])

    def test_the_name_goes_where_it_is_clear(self):
        segs = [((50.8, 50.8), (63.5, 50.8)), ((63.5, 50.8), (71.12, 50.8))]
        self.assertEqual(sch._name_spot("FB", segs, []), (52.07, 50.8, 0))                  # the longest stretch
        self.assertEqual(sch._name_spot("FB", segs, [(51, 48, 56, 50.5)]), (59.69, 50.8, 0))   # a value in the way: the far end
        self.assertEqual(sch._name_spot("FB", segs, [(51, 48, 63, 50.5)]), (64.77, 50.8, 0))   # all along it: the next stretch
        self.assertEqual(sch._name_spot("FB", [((50.8, 50.8), (51.3, 50.8))], []), (51.3, 50.8, 0))    # never past a short one
        self.assertEqual(sch._name_spot("FB", [((50.8, 50.8), (50.8, 51.3))], []), (50.8, 50.8, 0))

    def test_two_pins_are_joined_only_when_nothing_lies_between(self):
        a, b = (50.8, 50.8), (53.34, 50.8)
        self.assertTrue(sch._free(a, b, {a, b, (60.96, 50.8)}, []))
        self.assertFalse(sch._free(a, b, {a, b, (52.07, 50.8)}, []))                      # another pin on the way
        self.assertFalse(sch._free(a, b, {a, b}, [(52.07, 45.72, 52.07, 55.88)]))         # a wire across
        self.assertTrue(sch._free(a, b, {a, b}, [(50.8, 50.8, 50.8, 45.72)]))             # a wire that ends on one of them

    def test_a_derived_symbol_in_a_directory_library(self):
        lib = os.path.join(self.dir.name, "d.kicad_symdir")
        os.makedirs(lib, exist_ok=True)
        for name, body in (("R", LIB[LIB.index('\t(symbol "R"'):LIB.index('\t(symbol "R_Small"')]),
                           ("R_Kid", '\t(symbol "R_Kid"\n\t\t(extends "R")\n\t)\n')):
            with open(os.path.join(lib, name + ".kicad_sym"), "w") as f:
                f.write('(kicad_symbol_lib (version 20241209) (generator "test")\n' + body + ")\n")
        with open(os.path.join(self.dir.name, "sym-lib-table"), "a") as f:
            f.write('(sym_lib_table (lib (name "d")(type "KiCad")(uri "${KIPRJMOD}/d.kicad_symdir")(options "")(descr "")))\n')
        table = os.path.join(self.dir.name, "sym-lib-table")
        both = '(sym_lib_table (version 7)\n (lib (name "t")(type "KiCad")(uri "${KIPRJMOD}/t.kicad_sym")(options "")(descr ""))\n' \
               ' (lib (name "d")(type "KiCad")(uri "${KIPRJMOD}/d.kicad_symdir")(options "")(descr "")))\n'
        with open(table, "w") as f:
            f.write(both)
        self.assertEqual(sch.pins(dict(ref="R1", lib="d:R_Kid", x=0, y=0, pins={})), {"1": (0, -3.81), "2": (0, 3.81)})
        with self.assertRaises(KeyError):
            sch.pins(dict(ref="R1", lib="d:NOPE", x=0, y=0, pins={}))

    def test_a_block_measures_its_own_unit_of_a_part(self):
        import types
        sym = lambda unit, x: dict(ref="U1", unit=unit, power=False, pins=[("1", (x, 50.8))], body=(x, 48.26, round(x + 5.08, 2), 53.34))  # noqa: E731
        sh = types.SimpleNamespace(symbols=[sym(1, 50.8), sym(2, 150.0)], wires=[], texts=[], path="t")
        one = types.SimpleNamespace(parts=[dict(ref="U1", unit=1)])
        two = types.SimpleNamespace(parts=[dict(ref="U1", unit=2)])
        self.assertEqual(sch._content(sh, one), (50.8, 48.26, 55.88, 53.34))
        self.assertEqual(sch._content(sh, two), (150.0, 48.26, 155.08, 53.34))
        self.assertEqual(sch._name_spot("FB", [((50.8, 50.8), (50.8, 63.5))], []), (50.8, 62.23, 0))   # level, beside the wire


class Arrange(Lib):
    def blocks(self):
        one = sch.block("one", AT, [R("R1", "IN", "OUT")], shunt={"OUT": [R("C1", "OUT", "GND")]})
        two = sch.block("two", AT, [R("R2", "IN", "OUT")], shunt={"OUT": [[R("R3", "OUT", "FB"), R("R4", "FB", "GND")]]})
        return one, two, sch.block("three", AT, [R("R5", "A", "B")])

    def test_rows_of_frames_with_one_height_and_one_gap(self):
        one, two, three = self.blocks()
        box = sch.arrange([[one, two], [three]], origin=(25.4, 25.4), gap=7.62)
        self.assertEqual((one.frame[0], one.frame[1], two.frame[1], three.frame[0]), (25.4, 25.4, 25.4, 25.4))
        self.assertEqual(one.frame[3], two.frame[3])                         # the taller block sets the row
        self.assertEqual(round(two.frame[0] - one.frame[2], 2), 7.62)
        self.assertEqual(round(three.frame[1] - one.frame[3], 2), 12.7)      # the gap and room for a title
        self.assertEqual(box, (25.4, 25.4, two.frame[2], three.frame[3]))
        for b in (one, two, three):                                          # each block inside its frame, on the grid
            e, f = b.extent, b.frame
            self.assertTrue(f[0] + 2.54 <= e[0] and f[1] + 2.54 <= e[1] and e[2] <= f[2] - 2.54 and e[3] <= f[3] - 2.54, (e, f))
            self.assertTrue(all(abs(v / 1.27 - round(v / 1.27)) < 1e-6 for c in b.parts for v in (c["x"], c["y"])))
        self.assertEqual(one.rails[0]["pts"][0], sch.pin(one.parts[0], 2))   # the rails moved with the parts

    def test_a_page_centres_the_lot_and_keeps_off_the_title_block(self):
        one, two, three = self.blocks()
        x0, y0, x1, y1 = sch.arrange([[one, two], [three]], page="A3")
        self.assertLess(abs((x0 + x1) / 2 - 210), 1.27)                      # the middle of a 420 mm page, on the grid
        self.assertLess(abs((y0 - 5.08 + y1) / 2 - 148.5), 1.27)             # titles and all
        self.assertEqual((one.frame[0], one.frame[1]), (x0, y0))
        wide = [sch.block("b%d" % i, path=[R("R%d" % i, "A%d" % i, "B%d" % i)]) for i in range(60)]
        rows = [wide[i:i + 12] for i in range(0, 60, 12)]                    # a lot that reaches into the corner
        box = sch.arrange(rows, page="A4")
        self.assertTrue(box[2] > 297 - sch.TITLE_BLOCK[0] and box[3] <= 210 - sch.TITLE_BLOCK[1] + 0.64, box)

    def test_a_block_needs_no_place_and_a_part_none_either(self):
        r, f = sch.part("R1", "t:R", "10k", pins={"1": "IN", "2": "OUT"}, mpn="X"), sch.flag("#FLG01", "OUT")
        g = sch.flag("#FLG02", "GND")
        self.assertEqual((r["fields"], f["lib"], f["pins"]), ({"MPN": "X"}, "power:PWR_FLAG", {"1": "OUT"}))
        b = sch.block("b", path=[r], shunt={"OUT": [R("R2", "OUT", "GND")]})
        self.assertEqual(sch.pin(r, 1), (0, 0))
        self.assertEqual(len(b.parts), 2)

    def test_the_extent_holds_fields_and_symbols_until_it_is_measured(self):
        one, _, _ = self.blocks()
        b, e = one.box, one.extent
        self.assertTrue(e[0] <= b[0] and e[1] < b[1] and e[2] > b[2] and e[3] > b[3], (b, e))
        one.measured = (-1.0, -2.0, 30.0, 12.0)                              # from the block's top left corner
        self.assertEqual(one.extent, (b[0] - 1.0, b[1] - 2.0, b[0] + 30.0, b[1] + 12.0))
        one.move(2.54, 0)
        self.assertEqual(one.extent[0], round(b[0] + 1.54, 2))


class Fields(unittest.TestCase):
    def tidy(self, *items):
        text = '(kicad_sch (version 20260306) (generator "eeschema") (paper "A4") %s %s\n)\n' % (SHEET_LIB, " ".join(items))
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.kicad_sch")
            with open(path, "w") as f:
                f.write(text)
            n = sch.fields(path)
            page = sch_lint.Sheet(path)
        return n, {(t["owner"]["ref"], t["field"]): tuple(round(v, 2) for v in t["box"]) for t in page.texts if t["kind"] == "field"}

    def test_beside_a_part_that_stands(self):
        for rot in (0, 180):                                                 # either way up: the text starts at the body
            n, box = self.tidy(placed("R1", 50, 50, rot=rot, ref_at=(40, 40), val_at=(60, 60)))
            ref, val = box[("R1", "Reference")], box[("R1", "Value")]
            self.assertEqual((n, ref[0], val[0]), (1, 52.02, 52.02), rot)    # 1 mm right of the body, one under the other
            self.assertEqual((round((ref[1] + ref[3]) / 2, 2), round((val[1] + val[3]) / 2, 2)), (48.73, 51.27), rot)
            self.assertTrue(ref[2] - ref[0] > ref[3] - ref[1], rot)          # level

    def test_to_the_left_when_the_right_is_taken(self):
        _, box = self.tidy(placed("R1", 50, 50), placed("R2", 54, 50))       # R2's body where R1's fields would go
        self.assertEqual(box[("R1", "Reference")][2], 47.98)                 # ends 1 mm left of the body

    def test_a_one_letter_value_is_set_level_too(self):
        from pcbkit.checks.sch_lint import transform
        for rot in (0, 90, 180, 270):                                       # "0": narrower than it is high
            x, y, ang, hj = sch._field_at(transform(rot, None), 10.0, 5.0, 1.0, 1.27, "left")
            self.assertEqual((x, ang in (0, 90)), (10.0, True))
        self.assertEqual(sch._field_at(transform(0, None), 10.0, 5.0, 1.0, 1.27, "left")[2:], (0, "left"))
        self.assertEqual(sch._field_at(transform(90, None), 10.0, 5.0, 1.0, 1.27, "left")[2], 90)   # turned with the part, kept level

    def test_above_and_below_a_part_that_lies_flat(self):
        _, box = self.tidy(placed("R1", 50, 50, rot=90))
        ref, val = box[("R1", "Reference")], box[("R1", "Value")]
        self.assertEqual((round((ref[0] + ref[2]) / 2, 2), round((val[0] + val[2]) / 2, 2)), (50.0, 50.0))   # centred on it
        self.assertTrue(ref[3] < 48.98 < 51.02 < val[1])                     # clear of the body, one each side
        self.assertTrue(ref[2] - ref[0] > ref[3] - ref[1])


class TitleBlock(unittest.TestCase):
    ROOT_TB = '(title_block\n\t\t(title "Lamp \\"two\\"")\n\t\t(date "2026-11-16")\n\t\t(rev "v1.2")\n\t\t(company "Maker")\n' \
              '\t\t(comment 1 "Overview")\n\t\t(comment 2 "A. Author")\n\t)'

    def sheets(self, d, child_tb=""):
        head = '(kicad_sch\n\t(version 20260306)\n\t(generator "eeschema")\n\t(uuid "%s")\n\t(paper "A3")\n%s\t(lib_symbols)\n)\n'
        with open(os.path.join(d, "root.kicad_sch"), "w") as f:
            f.write(head % ("11111111-1111-1111-1111-111111111111", "\t" + self.ROOT_TB + "\n"))
        with open(os.path.join(d, "child.kicad_sch"), "w") as f:
            f.write(head % ("22222222-2222-2222-2222-222222222222", child_tb))
        sch.configure(d, root="root.kicad_sch")
        return sch_lint.Sheet(os.path.join(d, "child.kicad_sch"))

    def test_a_sheet_takes_the_roots_title_rev_and_date_and_says_what_it_holds(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(self.sheets(d).title, "")
            got = sch.title_block("child.kicad_sch", about="Power: input and bucks")
            self.assertEqual(got, dict(title='Lamp "two"', date="2026-11-16", rev="v1.2", company="Maker",
                                       comment1="Power: input and bucks", comment2="A. Author"))
            text = open(os.path.join(d, "child.kicad_sch")).read()
            self.assertEqual(sch_lint.Sheet(os.path.join(d, "child.kicad_sch")).title, 'Lamp "two"')
            self.assertEqual(text.count("(title_block"), 1)
            sch.title_block("child.kicad_sch", author="B. Other", rev="v2.0", comment3="draft")   # again: replaced, not added
            text = open(os.path.join(d, "child.kicad_sch")).read()
            self.assertEqual(text.count("(title_block"), 1)
            for want in ('(rev "v2.0")', '(comment 1 "Power: input and bucks")', '(comment 2 "B. Other")', '(comment 3 "draft")'):
                self.assertIn(want, text)

    def test_reset_keeps_the_title_block_and_the_uuid(self):
        with tempfile.TemporaryDirectory() as d:
            self.sheets(d)
            sch.title_block("child.kicad_sch", about="Power")
            sch.reset("child.kicad_sch", paper="A4")
            text = open(os.path.join(d, "child.kicad_sch")).read()
            self.assertIn('(uuid "22222222-2222-2222-2222-222222222222")', text)
            self.assertIn('(paper "A4")\n\t(title_block\n\t\t(title "Lamp \\"two\\"")', text)
            self.assertIn('(comment 2 "A. Author")\n\t)\n\t(lib_symbols)', text)

    def test_without_a_root_only_what_is_given_is_written(self):
        with tempfile.TemporaryDirectory() as d:
            self.sheets(d)
            sch.ROOT_SHEET = None
            self.assertEqual(sch.title_block("child.kicad_sch", title="Solo", about="one sheet"), dict(title="Solo", comment1="one sheet"))
            with self.assertRaises(AssertionError):
                sch.title_block("child.kicad_sch", author_name="x")


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
            b = sch.Block('The "divider"', [dict(ref="R1"), dict(ref="R2")])
            self.assertEqual(tuple(round(v, 2) for v in sch._content(sch_lint.Sheet(path), b)), (48.98, 46.19, 65.49, 66.64))   # to R2's value and the GND symbol's
            box, = sch.frames(path, [b])
            page = sch_lint.Sheet(path)
            with open(path) as f:
                out = f.read()
        self.assertEqual(box, (45.72, 43.18, 68.58, 69.85))                 # R3 stays outside, the GND symbol is inside
        self.assertTrue(all(abs(v / 1.27 - round(v / 1.27)) < 1e-6 for v in box))
        self.assertIn("(start 45.72 43.18)", out)
        self.assertIn("(stroke (width 0) (type dash))", out)
        self.assertEqual([t["at"] for t in page.texts if t["text"] == 'The "divider"'], [(45.72, 42.18)])
        self.assertEqual(len(page.symbols), 4)

    def test_the_frame_arrange_gave_is_kept(self):
        text = '(kicad_sch (version 20260306) (generator "eeschema") (paper "A4") %s %s\n)\n' % (SHEET_LIB, placed("R1", 50, 50))
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.kicad_sch")
            with open(path, "w") as f:
                f.write(text)
            b = sch.Block("R", [dict(ref="R1")])
            b.frame = (25.4, 25.4, 76.2, 76.2)
            self.assertEqual(sch.frames(path, [b]), [(25.4, 25.4, 76.2, 76.2)])


if __name__ == "__main__":
    unittest.main()
