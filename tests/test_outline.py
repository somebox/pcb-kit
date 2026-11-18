"""`pcbkit outline` removes only what it made: its items are in a pcbkit:outline group, and a rerun replaces that.

  python3 tests/test_outline.py     # re-runs itself under KiCad's Python when pcbnew is not importable

The fixture is a board drawn in pcbnew with things a person put there: a line on Dwgs.User, a note on Cmts.User, a
mounting hole from KiCad's library and, once the board is outlined, a cut-out on Edge.Cuts, a keep-out of their own
and a hole footprint named like the command's. Before the items were marked, every run deleted all of them.
"""
import collections
import contextlib
import io
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pcbkit import kicad, mech, sexp  # noqa: E402

if __name__ == "__main__" and not kicad.have_pcbnew() and not os.environ.get("PCBKIT_REEXEC"):
    env = dict(os.environ, PCBKIT_REEXEC="1")
    env["PYTHONPATH"] = ROOT + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    os.execve(kicad.KICAD_PYTHON, [kicad.KICAD_PYTHON, os.path.abspath(__file__), *sys.argv[1:]], env)

SPEC = {"name": "a box", "notes": ["measured with calipers"],
        "board": {"width": 60, "height": 40, "corner_radius": 2},
        "mounts": [{"x": -25, "y": -15, "hole": 2.7, "boss": 6, "head": 5.5, "name": "H1"},
                   {"x": 25, "y": 15, "hole": 2.7, "name": "H2"}],
        "keepouts": [{"x": 0, "y": 0, "d": 7.5, "what": "lid boss"}],
        "drawings": [{"rect": [70, 50], "label": "lid"}, {"line": [-30, 22, 30, 22], "label": "wall"},
                     {"circle": [10, 5, 4], "label": "post"}]}
SMALLER = dict(SPEC, board={"width": 50, "height": 30, "corner_radius": 2}, mounts=SPEC["mounts"][:1], drawings=[])

BOARD = """(kicad_pcb (version 20241229) (generator "test")
\t(gr_line (start 0 0) (end 10 0) (stroke (width 0.1) (type default)) (layer "Edge.Cuts") (uuid "a-1"))
\t(gr_line (start 0 0) (end 10 0) (stroke (width 0.5) (type default)) (layer "User.1") (uuid "a-2"))
\t(gr_text "a (group) \\"here\\"" (at 1 1) (layer "Cmts.User") (uuid "a-3") (effects (font (size 1 1))))
\t(gr_line (start 5 5) (end 6 6) (layer "Dwgs.User") (uuid "mine-1"))
\t(gr_line (start 5 5) (end 6 6) (layer "F.SilkS") (uuid "silk-1"))
\t(footprint "MountingHole_2.7mm" (layer "F.Cu") (uuid "a-4") (at 1 1) (property "Reference" "H7" (at 0 0))
\t\t(pad "" np_thru_hole circle (at 0 0) (size 2.7 2.7) (drill 2.7) (layers "*.Cu" "*.Mask") (uuid "pad-1")))
\t(footprint "MountingHole:MountingHole_3.2mm_M3" (layer "F.Cu") (uuid "mine-2") (at 9 9)
\t\t(pad "" np_thru_hole circle (at 0 0) (size 3.2 3.2) (drill 3.2) (layers "*.Cu" "*.Mask") (uuid "a-1")))
\t(zone (net 0) (net_name "") (layer "F.Cu") (uuid "a-5") (name "keepout:lid boss") (keepout (tracks not_allowed)))
\t(zone (net 0) (net_name "") (layer "F.Cu") (uuid "mine-3") (name "keepout:mine") (keepout (tracks not_allowed)))
\t(dimension (type aligned) (layer "User.1") (uuid "a-6") (pts (xy 0 0) (xy 10 0))
\t\t(gr_text "10" (at 5 1) (layer "User.1") (uuid "a-7")))
\t(group "pcbkit:outline" (uuid "g-1") (members "a-1" "a-2" "a-3" "a-4" "a-5" "a-6"))
\t(group "silk marks" (uuid "g-2") (members "silk-1"))
)
"""


def uuids(text):
    """The uuids of a board text's top-level items, groups included."""
    return [mech.own_uuid(text[s:e]) for s, e, _ in mech.top_level_blocks(text) if "(uuid" in text[s:e]]


class Marks(unittest.TestCase):
    """What is whose, read from a board's text: plain Python, no KiCad."""
    def test_an_items_own_uuid_is_not_one_inside_it(self):
        self.assertEqual(uuids(BOARD), ["a-1", "a-2", "a-3", "mine-1", "silk-1", "a-4", "mine-2", "a-5", "mine-3", "a-6",
                                        "g-1", "g-2"])

    def test_the_groups_items_are_the_commands(self):
        own = mech.own_items(BOARD)
        self.assertEqual(sorted(mech.own_uuid(BOARD[s:e]) for s, e in own), ["a-1", "a-2", "a-3", "a-4", "a-5", "a-6", "g-1"])
        # the library hole's pad has the uuid of a member, a-1: the pad is not a top-level item, and the hole stays
        self.assertNotIn("mine-2", [mech.own_uuid(BOARD[s:e]) for s, e in own])

    def test_a_board_without_the_group_has_no_items_of_the_commands(self):
        self.assertIsNone(mech.own_items(BOARD.replace('"pcbkit:outline"', '"somebody else"')))

    def test_what_earlier_versions_removed(self):
        old = mech.legacy_items(BOARD)
        self.assertEqual(collections.Counter(x[2] for x in old),
                         {"lines on Edge.Cuts": 1, "lines on User.1": 1, "texts on Cmts.User": 1, "lines on Dwgs.User": 1,
                          "MountingHole_* footprints without a library": 1, "keepout:* zones": 2,
                          "dimensions on User.1": 1})
        self.assertEqual([x[3] for x in old if len(x) > 3], ["H7"])
        self.assertEqual(sorted(x[2] for x in mech.in_the_way(old)),
                         ["MountingHole_* footprints without a library", "keepout:* zones", "keepout:* zones",
                          "lines on Edge.Cuts"])

    def strip(self, text, **kw):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "b.kicad_pcb")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
            n = mech.strip_template(path, **kw)
            with open(path, encoding="utf-8") as fh:
                return n, fh.read()

    def test_a_rerun_removes_the_group_and_its_items_only(self):
        n, text = self.strip(BOARD)
        self.assertEqual(n, 6)         # its six items; the group goes too
        self.assertEqual(uuids(text), ["mine-1", "silk-1", "mine-2", "mine-3", "g-2"])
        sexp.parse(text)               # still one balanced form

    def test_an_unmarked_outline_stops_the_run_and_take_over_removes_as_before(self):
        unmarked = BOARD.replace('"pcbkit:outline"', '"somebody else"')
        with self.assertRaises(SystemExit) as stop:
            self.strip(unmarked)
        said = str(stop.exception)
        self.assertIn("nothing written", said)
        self.assertIn("1 MountingHole_* footprints without a library (H7), 2 keepout:* zones, 1 lines on Edge.Cuts.",
                      said)
        self.assertIn("--take-over removes", said)
        self.assertIn("1 lines on Dwgs.User", said)        # what it would also take
        n, text = self.strip(unmarked, take_over=True)
        self.assertEqual(n, 8)
        self.assertEqual(uuids(text), ["silk-1", "mine-2", "g-1", "g-2"])

    def test_take_over_is_refused_on_a_marked_board(self):
        with self.assertRaises(SystemExit) as stop:
            self.strip(BOARD, take_over=True)
        self.assertIn("nothing to take over", str(stop.exception))

    def test_drawings_alone_are_nobodys_outline(self):
        notes = "\n".join(l for l in BOARD.splitlines() if "mine-1" in l or "silk" in l or l.startswith(("(kicad", ")")))
        self.assertEqual(self.strip(notes + "\n"), (0, notes + "\n"))


@unittest.skipUnless(kicad.have_pcbnew(), "needs KiCad's Python")
class Outline(unittest.TestCase):
    def setUp(self):
        import pcbnew
        self.pcbnew = pcbnew
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "box.kicad_pcb")
        b = pcbnew.BOARD()
        self.hand_drawings(b)
        kicad.save_board(b, self.path)

    def tearDown(self):
        self.dir.cleanup()

    def mm(self, x, y):
        return self.pcbnew.VECTOR2I(self.pcbnew.FromMM(x), self.pcbnew.FromMM(y))

    def hand_drawings(self, b):
        """What a person drew before any outline: a line on Dwgs.User, a note on Cmts.User, a library hole."""
        pcbnew = self.pcbnew
        s = pcbnew.PCB_SHAPE(b)
        s.SetShape(pcbnew.SHAPE_T_SEGMENT); s.SetLayer(pcbnew.Dwgs_User); s.SetStart(self.mm(80, 80)); s.SetEnd(self.mm(120, 80))
        b.Add(s)
        t = pcbnew.PCB_TEXT(b)
        t.SetText("my own note"); t.SetLayer(pcbnew.Cmts_User); t.SetPosition(self.mm(100, 60))
        b.Add(t)
        b.Add(self.hole(b, "MH9", pcbnew.LIB_ID("MountingHole", "MountingHole_3.2mm_M3"), 100, 100))

    def hole(self, b, ref, fpid, x, y):
        pcbnew = self.pcbnew
        fp = pcbnew.FOOTPRINT(b)
        fp.SetReference(ref); fp.SetFPID(fpid)
        pad = pcbnew.PAD(fp)
        pad.SetAttribute(pcbnew.PAD_ATTRIB_NPTH); pad.SetShape(pcbnew.PAD_SHAPE_CIRCLE)
        pad.SetSize(self.mm(3.2, 3.2)); pad.SetDrillSize(self.mm(3.2, 3.2)); pad.SetLayerSet(pcbnew.PAD.UnplatedHoleMask())
        fp.Add(pad)
        fp.SetPosition(self.mm(x, y))
        return fp

    def later_by_hand(self):
        """Added once the board has its outline: a cut-out on Edge.Cuts, a keep-out and a hole named like the command's."""
        pcbnew = self.pcbnew
        b = pcbnew.LoadBoard(self.path)
        c = pcbnew.PCB_SHAPE(b)
        c.SetShape(pcbnew.SHAPE_T_CIRCLE); c.SetLayer(pcbnew.Edge_Cuts); c.SetCenter(self.mm(100, 100)); c.SetEnd(self.mm(103, 100))
        b.Add(c)
        z = pcbnew.ZONE(b)
        z.SetIsRuleArea(True); z.SetLayer(pcbnew.F_Cu); z.SetZoneName("keepout:mine")
        o = z.Outline(); o.NewOutline()
        for x, y in ((90, 90), (95, 90), (95, 95)):
            o.Append(self.mm(x, y))
        b.Add(z)
        b.Add(self.hole(b, "MH10", pcbnew.LIB_ID("", "MountingHole_3.2mm"), 110, 100))
        kicad.save_board(b, self.path)

    def outline(self, spec, **kw):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            mech.apply(self.path, spec, **kw)
        return out.getvalue()

    def read(self):
        with open(self.path, encoding="utf-8") as fh:
            return fh.read()

    def counts(self):
        """How many top-level items of each kind and layer the board has: {"gr_line Edge.Cuts": 4, ...}."""
        text, n = self.read(), collections.Counter()
        for s, e, head in mech.top_level_blocks(text):
            node = sexp.parse(text[s:e])
            if head == "footprint":
                n["footprint " + str(node[1])] += 1
            elif head == "zone":
                n["zone " + str(sexp.first(node, "name")[1])] += 1
            elif head == "group":
                n["group " + str(node[1])] += 1
            elif head.startswith("gr_") or head == "dimension":
                n[head + " " + str(sexp.first(node, "layer")[1])] += 1
        return n

    HAND = {"gr_line Dwgs.User": 1, "gr_text Cmts.User": 1, "footprint MountingHole:MountingHole_3.2mm_M3": 1}
    LATER = {"gr_circle Edge.Cuts": 1, "zone keepout:mine": 1, "footprint MountingHole_3.2mm": 1}

    def minus(self, counts, *others):
        counts = collections.Counter(counts)
        for o in others:
            counts.subtract(o)
        return {k: v for k, v in counts.items() if v}

    def test_a_first_run_draws_beside_what_is_there(self):
        said = self.outline(SPEC)
        n = self.counts()
        made = self.minus(n, self.HAND)
        self.assertEqual({k: made[k] for k in ("gr_line Edge.Cuts", "gr_arc Edge.Cuts", "footprint MountingHole_2.7mm",
                                               "zone keepout:lid boss", "zone keepout:screw head H1",
                                               "group pcbkit:outline")},
                         {"gr_line Edge.Cuts": 4, "gr_arc Edge.Cuts": 4, "footprint MountingHole_2.7mm": 2,
                          "zone keepout:lid boss": 1, "zone keepout:screw head H1": 1, "group pcbkit:outline": 1})
        self.assertEqual({k: n[k] for k in self.HAND}, {k: v + (k in made and made[k]) for k, v in self.HAND.items()})
        self.assertGreaterEqual(n["gr_line Dwgs.User"], 1 + 5)             # the lid's rectangle and the wall, and mine
        self.assertGreaterEqual(n["gr_text Cmts.User"], 1 + 2)             # the box's name and its note, and mine
        # every item it added is in the group, and nothing else is
        text = self.read()
        own = mech.own_items(text)
        self.assertEqual(len(own) - 1, sum(made.values()) - 1)             # the group itself aside
        self.assertIn("my own note", text)
        self.assertRegex(said, r"outline 60 x 40 mm, 2 mounting holes, 1 keep-outs, 3 reference drawings; "
                               r"\d+ items in the pcbkit:outline group\n$")

    def test_a_rerun_replaces_its_group_and_nothing_else(self):
        self.outline(SPEC)
        first = self.counts()
        self.later_by_hand()
        said = self.outline(SMALLER)
        n = self.counts()
        for k, v in dict(self.HAND, **self.LATER).items():                 # everything drawn by hand is still there
            self.assertGreaterEqual(n[k], v, k)
        self.assertEqual(n["gr_circle Edge.Cuts"], 1)                      # the cut-out
        self.assertEqual((n["gr_line Edge.Cuts"], n["gr_arc Edge.Cuts"]), (4, 4))      # one outline, not two
        self.assertEqual(n["footprint MountingHole_2.7mm"], 1)             # the smaller box has one mount
        self.assertEqual(n["footprint MountingHole_3.2mm"], 1)             # the hand-placed hole named like them
        self.assertEqual(n["zone keepout:mine"], 1)
        self.assertEqual(n["group pcbkit:outline"], 1)
        self.assertEqual(n["gr_line Dwgs.User"], 1)                        # the smaller box has no reference drawings
        self.assertIn("my own note", self.read())
        self.assertRegex(said, r"items in the pcbkit:outline group, %d of an earlier run removed\n$"
                         % (sum(self.minus(first, self.HAND).values()) - 1))      # the group itself aside
        # and again, with the first box: as after the first run, plus what was added by hand
        self.outline(SPEC)
        self.assertEqual(self.minus(self.counts(), first), self.LATER)

    def unmark(self):
        """The board as pcbkit 0.5 left it: outlined, its items in no group."""
        text = self.read()
        (s, e), = [(s, e) for s, e, head in mech.top_level_blocks(text)
                   if head == "group" and '"pcbkit:outline"' in text[s:e]]
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(text[:s] + text[e:])

    def test_a_board_outlined_before_stops_the_run_untouched(self):
        self.outline(SPEC)
        self.unmark()
        before = self.read()
        with self.assertRaises(SystemExit) as stop:
            self.outline(SMALLER)
        self.assertEqual(self.read(), before)
        self.assertIn("4 arcs on Edge.Cuts", str(stop.exception))
        self.assertIn("--take-over", str(stop.exception))

    def test_take_over_once_then_reruns_touch_the_group_only(self):
        self.outline(SPEC)
        self.unmark()
        self.outline(SMALLER, take_over=True)
        n = self.counts()
        self.assertEqual((n["gr_line Edge.Cuts"], n["gr_arc Edge.Cuts"], n["group pcbkit:outline"]), (4, 4, 1))
        self.assertEqual(n["footprint MountingHole_2.7mm"], 1)
        self.assertEqual(n["gr_line Dwgs.User"], 0)                        # as before: my line went with the old outline
        self.assertNotIn("my own note", self.read())
        self.assertEqual(n["footprint MountingHole:MountingHole_3.2mm_M3"], 1)     # a library hole never did
        self.later_by_hand()
        self.outline(SPEC)                                                 # from now on, only the group
        n = self.counts()
        for k, v in self.LATER.items():
            self.assertEqual(n[k], v, k)
        with self.assertRaises(SystemExit):
            self.outline(SPEC, take_over=True)

    def test_a_description_that_does_not_fit_leaves_the_board_as_it_was(self):
        self.outline(SPEC)
        before = self.read()
        bad = dict(SPEC, board={"width": 60, "height": 40, "cutouts": [{"x": 2, "y": 2, "boss": 7.5}]})
        with self.assertRaises(SystemExit):
            self.outline(bad)
        self.assertEqual(self.read(), before)

    def test_the_groups_items_load_as_one_group_in_kicad(self):
        self.outline(SPEC)
        b = self.pcbnew.LoadBoard(self.path)
        groups = [g for g in b.Groups() if g.GetName() == mech.GROUP]
        self.assertEqual(len(groups), 1)
        kinds = collections.Counter(i.GetClass() for i in groups[0].GetItems())
        self.assertEqual(kinds["FOOTPRINT"], 2)
        self.assertEqual(kinds["ZONE"], 2)
        self.assertGreaterEqual(kinds["PCB_DIM_ALIGNED"], 2)
        self.assertGreaterEqual(kinds["PCB_SHAPE"], 16)


if __name__ == "__main__":
    unittest.main()
