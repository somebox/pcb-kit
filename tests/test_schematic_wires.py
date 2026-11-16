"""`pcbkit.schematic.plan_wires`: which small local nets `build(wire=...)` draws, and where it gives up.

  python3 tests/test_schematic_wires.py     # plain Python: the planner is geometry, it needs no KiCad or konnect

Pins are given as points on a 1.27 mm grid, the way konnect reports them after placement.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pcbkit.schematic import plan_wires  # noqa: E402


def plan(nets, boxes=(), others=(), segments=(), reach=25.4, far=()):
    points = {p: None for p in others}
    for name, pins in nets.items():
        points.update({p: name for _, _, p in pins})
    return plan_wires(nets, list(boxes), points, list(segments), reach, far=far)


A, B = ("R1", "2", (50.8, 50.8)), ("R2", "1", (63.5, 50.8))


class Plan(unittest.TestCase):
    def test_two_pins_in_line_get_one_wire_and_the_name(self):
        r = plan({"FB": [A, B]})["FB"]
        self.assertEqual(r["wires"], [((50.8, 50.8), (63.5, 50.8))])
        self.assertEqual((r["loose"], r["junctions"]), ([], []))
        self.assertEqual(r["label"], (52.07, 50.8, 0))

    def test_an_l_goes_round_a_symbol(self):
        c = ("R2", "1", (63.5, 60.96))
        # a body on the corner of the first L (63.5, 50.8): the other L, by (50.8, 60.96), is taken
        r = plan({"FB": [A, c]}, boxes=[(61, 48, 66, 55)])["FB"]
        self.assertEqual(r["wires"], [((50.8, 50.8), (50.8, 60.96)), ((50.8, 60.96), (63.5, 60.96))])

    def test_another_nets_pin_on_the_way_keeps_the_labels(self):
        r = plan({"FB": [A, B]}, others=[(57.15, 50.8)])["FB"]
        self.assertEqual(r["wires"], [])
        self.assertEqual(r["loose"], [("R1", "2"), ("R2", "1")])
        self.assertIsNone(r["label"])

    def test_a_wire_already_there_is_not_crossed(self):
        r = plan({"FB": [A, B]}, segments=[((57.15, 40), (57.15, 60))])["FB"]
        self.assertEqual(r["wires"], [])

    def test_out_of_reach_keeps_the_labels(self):
        r = plan({"FB": [A, ("R2", "1", (150, 50.8))]})["FB"]
        self.assertEqual((r["wires"], len(r["loose"])), ([], 2))

    def test_a_sense_net_is_wired_however_far(self):
        far = ("R2", "1", (152.4, 50.8))
        r = plan({"FB": [A, far]}, far={"FB"})["FB"]
        self.assertEqual((r["wires"], r["loose"]), ([((50.8, 50.8), (152.4, 50.8))], []))

    def test_a_sense_net_goes_round_what_lies_between(self):
        far, parts = ("R2", "1", (152.4, 50.8)), (90, 30, 110, 60.96)        # parts on the straight line, more of them above it
        r = plan({"FB": [A, far]}, boxes=[parts], far={"FB"})["FB"]
        pts = sorted({q for seg in r["wires"] for q in seg})
        self.assertEqual(r["loose"], [])
        self.assertEqual(pts, [(50.8, 50.8), (50.8, 63.5), (152.4, 50.8), (152.4, 63.5)])   # down, under it, up: the shortest way
        self.assertEqual(r["label"], (52.07, 63.5, 0))                       # named on the long stretch
        self.assertEqual(plan({"FB": [A, far]}, boxes=[parts])["FB"]["wires"], [])   # not without the role

    def test_a_detour_keeps_a_millimetre_from_what_it_passes(self):
        far, low = ("R2", "1", (152.4, 50.8)), (90, 20, 110, 62.8)           # its lower edge 0.7 mm above the first clear channel
        r = plan({"FB": [A, far]}, boxes=[low], far={"FB"})["FB"]
        self.assertEqual(max(q[1] for seg in r["wires"] for q in seg), 66.04)

    def test_a_third_pin_on_the_node_gets_a_junction(self):
        c = ("C1", "1", (57.15, 58.42))
        r = plan({"FB": [A, B, c]}, reach=40)["FB"]
        self.assertEqual(r["loose"], [])
        self.assertEqual(len(r["wires"]), 3)
        self.assertEqual(len(r["junctions"]), 1)

    def test_two_wired_groups_keep_a_label_on_every_pin(self):
        far = [("R3", "1", (150, 50.8)), ("R4", "1", (160.02, 50.8))]
        r = plan({"FB": [A, B] + far})["FB"]
        self.assertEqual(len(r["wires"]), 2)
        self.assertEqual(len(r["loose"]), 4)
        self.assertIsNone(r["label"])

    def test_a_wide_fan_out_keeps_its_labels(self):
        pins = [("R%d" % i, "1", (50.8 + 5.08 * i, 50.8)) for i in range(8)]
        self.assertEqual(plan({"EN": pins})["EN"]["wires"], [])

    def test_a_later_net_does_not_cross_an_earlier_one(self):
        nets = {"A": [A, B], "B": [("R5", "1", (57.15, 45.72)), ("R6", "1", (57.15, 55.88))]}
        r = plan(nets)
        self.assertEqual(len(r["A"]["wires"]), 1)
        self.assertEqual(r["B"]["wires"], [])


if __name__ == "__main__":
    unittest.main()
