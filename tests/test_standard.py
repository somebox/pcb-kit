"""`pcbkit standard`: what it finds against "What a released board carries", and how a waiver covers a finding.

  python3 tests/test_standard.py     # re-runs itself under KiCad's Python when pcbnew is not importable

The texts, title blocks, rails, Extended parts and waivers are read from files written here, with no KiCad. The
labels, test pads, mounting holes, fiducials, references and LCSC numbers are checked on boards drawn in pcbnew: a
connector with its name beside it or on the wrong side, a test pad with a text, a value or a legend, a hole with and
without a keep-out, fiducials too few, too near the edge or on one line, a part whose reference is hidden, a part the
assembler places without an LCSC number.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pcbkit import kicad  # noqa: E402
from pcbkit.checks import standard as S  # noqa: E402

if __name__ == "__main__" and not kicad.have_pcbnew() and not os.environ.get("PCBKIT_REEXEC"):
    env = dict(os.environ, PCBKIT_REEXEC="1")
    env["PYTHONPATH"] = ROOT + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    os.execve(kicad.KICAD_PYTHON, [kicad.KICAD_PYTHON, os.path.abspath(__file__), *sys.argv[1:]], env)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def board(*texts, rev="v1.0", title="Demo"):
    """A board file's text with silkscreen texts: (text, layer) pairs, or a text for B.SilkS."""
    out = f'(kicad_pcb (version 20241229)\n\t(title_block (title "{title}") (rev "{rev}") (date "2026-11-01"))\n'
    for t in texts:
        s, layer = t if isinstance(t, tuple) else (t, "B.SilkS")
        out += f'\t(gr_text "{s}" (at 10 10) (layer "{layer}") (uuid "u") (effects (font (size 1 1))))\n'
    return out + ")\n"


def sheet(title=None, sheets=(), symbols=()):
    """A schematic sheet: with a title block when title is not None, naming the sheet files given, with power symbols
    (reference, lib_id, value)."""
    out = '(kicad_sch (version 20250114)\n\t(paper "A4")\n'
    if title is not None:
        out += f'\t(title_block (title "{title}") (rev "v1.0"))\n'
    out += "\t(lib_symbols)\n"
    for ref, lib, value in symbols:
        out += (f'\t(symbol (lib_id "{lib}") (at 50 50 0) (unit 1)\n\t\t(property "Reference" "{ref}" (at 0 0 0))\n'
                f'\t\t(property "Value" "{value}" (at 0 0 0)))\n')
    for name in sheets:
        out += (f'\t(sheet (at 20 50) (size 40 20)\n\t\t(property "Sheetname" "S" (at 0 0 0))\n'
                f'\t\t(property "Sheetfile" "{name}" (at 0 0 0)))\n')
    return out + ")\n"


def found(findings, check=None):
    """(check, level, item) of the findings that are not ok lines."""
    return [(f["check"], f["level"], f["item"]) for f in findings
            if f["level"] != "ok" and (check is None or f["check"] == check)]


class VersionAndDate(unittest.TestCase):
    def test_the_variables_are_what_the_standard_asks_for(self):
        self.assertEqual(found(S.version_findings(board("Demo\\n${REVISION} ${ISSUE_DATE}"))), [])

    def test_a_board_without_a_version_text(self):
        out = S.version_findings(board("Demo", "GND"))
        self.assertEqual(found(out), [("version", "error", ""), ("date", "note", "")])
        self.assertIn("${REVISION}", out[0]["text"])

    def test_a_text_on_copper_or_a_hidden_one_is_not_the_silkscreen(self):
        self.assertEqual(found(S.version_findings(board(("${REVISION} ${ISSUE_DATE}", "F.Cu")))),
                         [("version", "error", ""), ("date", "note", "")])
        hidden = board("${REVISION} ${ISSUE_DATE}").replace('(layer "B.SilkS")', '(layer "B.SilkS") (hide yes)')
        self.assertEqual(found(S.version_findings(hidden), "version"), [("version", "error", "")])

    def test_what_prints_wrong_is_an_error_of_its_own(self):
        out = S.version_findings(board("DRIVER v${REVISION}", "${CURRENT_DATE}"))
        self.assertEqual(found(out), [("version-print", "error", ""), ("version-print", "error", ""), ("date", "note", "")])
        self.assertIn("would print vv", out[0]["text"])
        self.assertEqual(len(out), 3)             # and no line saying the version is printed: it isn't, rightly
        self.assertEqual([f["level"] for f in S.version_findings(board("v${REVISION}", "${REVISION} ${ISSUE_DATE}"))],
                         ["error", "ok", "ok"])

    def test_a_version_typed_in_is_restamped_and_only_noted(self):
        out = S.version_findings(board("Demo v1.0 2026-11-01"))
        self.assertEqual(found(out), [("version", "note", "")])       # its ISO date is restamped with it
        self.assertIn("'Demo v1.0 2026-11-01'", out[0]["text"])

    def test_a_date_typed_into_another_text_is_never_updated(self):
        out = S.version_findings(board("control board ${REVISION}", "2026-10  J.Seitz"))
        self.assertEqual(found(out), [("date", "note", "'2026-10  J.Seitz'")])
        for s in ("11.2026", "15.11.2026", "2026-11-15"):
            self.assertEqual(found(S.version_findings(board("${REVISION}", s))), [("date", "note", f"'{s}'")], s)

    def test_a_part_number_or_a_voltage_is_not_a_date(self):
        for s in ("BAT 1  18650", "VIN 5-13V", "TLV62569", "1:GND 2:3V3 3:TX 4:RX", "JP1 USB->BUS, open by default"):
            self.assertEqual(found(S.version_findings(board("${REVISION} ${ISSUE_DATE}", s))), [], s)


class TitleBlocks(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.pcb = os.path.join(self.dir.name, "demo.kicad_pcb")
        self.root = os.path.join(self.dir.name, "demo.kicad_sch")

    def tearDown(self):
        self.dir.cleanup()

    def sheets(self, **files):
        for name, text in files.items():
            write(os.path.join(self.dir.name, name.replace("__", os.sep) + ".kicad_sch"), text)

    def test_every_sheet_with_a_title(self):
        write(self.pcb, board())
        self.sheets(demo=sheet("Demo", ["power.kicad_sch", "sub/mcu.kicad_sch"]), power=sheet("Demo"), sub__mcu=sheet("Demo"))
        out = S.title_findings(self.pcb)
        self.assertEqual(found(out), [])
        self.assertEqual(out[0]["text"], "a title on the board and on 3 schematic sheets")

    def test_a_sheet_without_a_title_block_or_with_an_empty_one(self):
        write(self.pcb, board())
        self.sheets(demo=sheet("Demo", ["power.kicad_sch", "sub/mcu.kicad_sch", "io.kicad_sch"]), power=sheet(),
                    sub__mcu=sheet(""), io=sheet("Demo"))
        out = S.title_findings(self.pcb)
        self.assertEqual(found(out), [("title-block", "error", "power.kicad_sch"),
                                      ("title-block", "error", os.path.join("sub", "mcu.kicad_sch"))])
        self.assertEqual([f["text"] for f in out], ["no title block", "a title block without a title"])

    def test_one_title_between_the_board_and_its_root_is_enough(self):
        """Release gives each the other's title; with neither, schematic.pdf and the board are nameless."""
        write(self.pcb, board(title=""))
        self.sheets(demo=sheet("Demo"))
        self.assertEqual(found(S.title_findings(self.pcb)), [])
        self.sheets(demo=sheet(""))
        self.assertEqual(found(S.title_findings(self.pcb)), [("title-block", "error", "demo.kicad_pcb")])

    def test_a_board_without_a_schematic(self):
        write(self.pcb, board())
        self.assertEqual(found(S.title_findings(self.pcb)), [])
        write(self.pcb, board(title=""))
        self.assertEqual(found(S.title_findings(self.pcb)), [("title-block", "error", "demo.kicad_pcb")])

    def test_the_rails_are_the_nets_the_power_symbols_name(self):
        write(self.pcb, board())
        self.sheets(demo=sheet("Demo", ["power.kicad_sch"], [("#PWR01", "power:GND", "GND"), ("#FLG01", "power:PWR_FLAG", "PWR_FLAG"),
                                                             ("R1", "Device:R", "10k")]),
                    power=sheet("Demo", symbols=[("#PWR02", "power:+3V3", "+3V3"), ("#PWR03", "power:GND", "GND"),
                                                 ("#PWR04", "demo:VSYS", "VSYS")]))
        self.assertEqual(S.schematic_rails(self.pcb), ["GND", "+3V3", "VSYS"])
        os.remove(self.root)
        self.assertEqual(S.schematic_rails(self.pcb), [])


class Cache:
    """A stand-in for konnect's JLC parts cache."""
    def __init__(self, rows, db=True):
        self.rows, self.db = rows, db

    def get(self, lcsc):
        return self.rows.get(lcsc)


class Extended(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.bom = os.path.join(self.dir.name, "BOM.md")
        self.parts = {"C1": (["R1", "R2"], ""), "C7": (["U1"], "TPS259470A"), "C9": (["U2"], ""), "C11": (["D1"], "")}
        self.cache = Cache({"C1": dict(extended=False, mpn="0603WAF1002T5E"), "C7": dict(extended=True, mpn="TPS259470ARPWR"),
                            "C9": dict(extended=True, mpn="AP2112K-3.3TRG1"), "C11": dict(extended=True, mpn="SS34")})

    def tearDown(self):
        self.dir.cleanup()

    def test_each_extended_part_is_looked_up_in_bom_md(self):
        write(self.bom, "| U1 | eFuse | C7 | Extended: the only one with a 5 A limit, $3 |\n"
                        "| U2 | LDO AP2112K-3.3TRG1 | the Basic one drops too much |\n| D1 | C110 | not this part |\n")
        out = S.extended_findings(self.parts, self.cache, self.bom, self.dir.name)
        self.assertEqual(found(out), [("extended", "note", "C11")])    # C110 does not name C11
        self.assertEqual(out[0]["detail"], "D1; SS34")
        many = dict(self.parts, C11=(["D1", "D2", "D3", "D4", "D5", "D6"], ""))
        self.assertEqual(S.extended_findings(many, self.cache, self.bom, self.dir.name)[0]["detail"],
                         "D1, D2, D3, D4 and 2 more; SS34")
        self.assertIn("BOM.md does not name", out[0]["text"])
        self.assertEqual(out[-1]["text"], "2 Extended parts named in BOM.md")

    def test_without_a_bom_md_there_is_one_finding_for_the_project(self):
        out = S.extended_findings(self.parts, self.cache, None)
        self.assertEqual(found(out), [("extended", "note", "")])
        self.assertEqual(out[0]["text"], "3 JLC Extended parts placed, and the project has no BOM.md to give each its "
                                         "reason and the fee it adds: C11 (D1), C7 (U1), C9 (U2)")       # by reference

    def test_without_the_parts_cache_it_says_so(self):
        out = S.extended_findings(self.parts, Cache({}, db=None), self.bom)
        self.assertEqual(found(out), [("extended", "note", "")])
        self.assertIn("no JLC parts cache", out[0]["text"])

    def test_a_part_the_cache_does_not_have(self):
        out = S.extended_findings({"C1": (["R1"], ""), "C999": (["U9"], "")}, self.cache, None)
        self.assertEqual(found(out), [("extended", "note", "")])
        self.assertIn("1 placed part not in the JLC parts cache", out[0]["text"])

    def test_nothing_extended(self):
        out = S.extended_findings({"C1": (["R1"], "")}, self.cache, None)
        self.assertEqual([(f["level"], f["text"]) for f in out], [("ok", "no Extended part placed")])

    def test_the_bom_md_nearest_the_board(self):
        root = self.dir.name
        pcb = os.path.join(root, "hw", "v1", "kicad", "demo.kicad_pcb")
        write(pcb, "")
        self.assertIsNone(S.find_bom(root, pcb))
        write(os.path.join(root, "docs", "BOM.md"), "")
        self.assertEqual(S.find_bom(root, pcb), os.path.join(root, "docs", "BOM.md"))
        write(self.bom, "")
        self.assertEqual(S.find_bom(root, pcb), self.bom)
        write(os.path.join(root, "hw", "v1", "BOM.md"), "")
        self.assertEqual(S.find_bom(root, pcb), os.path.join(root, "hw", "v1", "BOM.md"))
        self.assertEqual(S.find_bom(root, pcb, "BOM.md"), self.bom)
        self.assertIsNone(S.find_bom(root, pcb, "parts/BOM.md"))


class Waivers(unittest.TestCase):
    def setUp(self):
        self.found = [S.finding("test-pads", "note", "a rail without a test pad", r) for r in ("+5V", "VBUS", "VIN")] + \
                     [S.finding("title-block", "error", "no title block", "io.kicad_sch"), S.ok("mounting", "4 holes")]
        self.pcb = dict(key="node", path="boards/node/node.kicad_pcb", folder="boards/node")

    def levels(self, waive):
        return [(f["check"], f["level"], f["item"]) for f in S.apply_waivers(self.found, waive)]

    def test_a_waiver_covers_the_items_it_names_and_shows_its_note(self):
        w = dict(check="test-pads", items=["+5V", "VBUS"], note="on J7 pins 2 and 3; pads in v1.3")
        out = S.apply_waivers(self.found, [w])
        self.assertEqual([(f["check"], f["level"], f["item"]) for f in out],
                         [("test-pads", "waived", "+5V"), ("test-pads", "waived", "VBUS"), ("test-pads", "note", "VIN"),
                          ("title-block", "error", "io.kicad_sch"), ("mounting", "ok", "")])
        self.assertEqual((out[0]["note"], out[0]["was"]), ("on J7 pins 2 and 3; pads in v1.3", "note"))
        self.assertEqual(S.count(out), dict(error=1, note=1, waived=2))

    def test_without_items_it_covers_the_whole_check_and_an_error_too(self):
        self.assertEqual(self.levels([dict(check="title-block", note="generated sheets; titles in v1.1")])[3],
                         ("title-block", "waived", "io.kicad_sch"))
        self.assertEqual([x[1] for x in self.levels([dict(check="test-pads", note="rails on the headers")])[:3]], ["waived"] * 3)

    def test_a_waiver_without_a_note_waives_nothing_and_is_an_error(self):
        for note in ({}, dict(note=""), dict(note="  ")):
            out = self.levels([dict(check="title-block", **note)])
            self.assertEqual(out[3], ("title-block", "error", "io.kicad_sch"))
            self.assertEqual(out[-1], ("waiver", "error", "title-block"))

    def test_a_waiver_that_matches_nothing_any_more_is_named(self):
        out = self.levels([dict(check="test-pads", items=["+5V", "+3V3"], note="x"), dict(check="labels", note="y")])
        self.assertEqual(out[-2:], [("waiver", "note", "test-pads (+3V3)"), ("waiver", "note", "labels")])

    def test_a_check_that_is_none_or_cannot_be_waived(self):
        out = S.apply_waivers(self.found + [S.finding("version-print", "error", "would print vv")],
                              [dict(check="testpads", note="x"), dict(check="version-print", note="x")])
        self.assertEqual([(f["check"], f["level"]) for f in out][-3:],
                         [("version-print", "error"), ("waiver", "error"), ("waiver", "error")])
        self.assertIn("'testpads', which is none of: version, date, title-block", out[-2]["text"])
        self.assertIn("cannot be waived", out[-1]["text"])

    def test_which_board_a_waiver_is_about(self):
        ws = [dict(board="node", check="a"), dict(board="boards/*", check="b"), dict(board="boards/node/*.kicad_pcb", check="c"),
              dict(check="d"), dict(board="controller", check="e"), dict(board="hardware/*", check="f")]
        self.assertEqual([w["check"] for w in S.waivers_for(ws, self.pcb)], ["a", "b", "c", "d"])

    def test_the_report(self):
        out = S.apply_waivers(self.found, [dict(check="test-pads", items=["+5V", "VBUS"], note="on J7; pads in v1.3")])
        self.assertEqual(S.report("boards/node/node.kicad_pcb", out).splitlines(), [
            "boards/node/node.kicad_pcb: what a released board carries (docs/CONVENTIONS.md)",
            "  title blocks         error   no title block: io.kicad_sch",
            "  test pads            note    a rail without a test pad: VIN",
            "                       waived  on J7; pads in v1.3 (waives: a rail without a test pad: +5V, VBUS)",
            "  mounting             ok      4 holes",
            "  1 error, 1 note, 2 waived"])
        self.assertEqual(S.summary([S.ok("mounting", "4 holes")]), "nothing found")

    def test_a_line_names_twenty_items_and_counts_the_rest(self):
        many = [S.finding("references", "note", "not on the silkscreen", "R%d" % i) for i in range(1, 26)]
        ((section, level, said),) = S.lines(many)
        self.assertEqual((section, level), ("silkscreen", "note"))
        self.assertTrue(said.startswith("not on the silkscreen: R1, R2, "), said)
        self.assertTrue(said.endswith("R19, R20 and 5 more"), said)
        self.assertEqual(S.count(many)["note"], 25)                   # every one is counted, and in --json


@unittest.skipUnless(kicad.have_pcbnew(), "needs KiCad's Python")
class Board(unittest.TestCase):
    """The checks on a board drawn in pcbnew."""
    def setUp(self):
        import pcbnew
        self.pcbnew = pcbnew
        self.b = pcbnew.BOARD()
        self.nets = {}

    def at(self, x, y):
        return self.pcbnew.VECTOR2I(self.pcbnew.FromMM(x), self.pcbnew.FromMM(y))

    def net(self, name):
        if name not in self.nets:
            self.nets[name] = self.pcbnew.NETINFO_ITEM(self.b, name)
            self.b.Add(self.nets[name])
        return self.nets[name]

    def part(self, ref, x, y, name="R_0603", value="10k", kind="smd", nets=("",), lcsc="C1", back=False, show=(),
             yard=False, **flags):
        """A footprint with a pad per net at 1.6 mm pitch; kind: smd, tht or npth; show: the fields on the silkscreen."""
        pcbnew = self.pcbnew
        fp = pcbnew.FOOTPRINT(self.b)
        fp.SetFPID(pcbnew.LIB_ID("demo", name))
        fp.SetReference(ref)
        fp.SetValue(value)
        if lcsc:
            fp.SetField("LCSC", lcsc)
        for f in fp.GetFields():
            f.SetVisible(f.GetName() in show)
            f.SetLayer(pcbnew.F_SilkS)
        for i, n in enumerate(nets):
            p = pcbnew.PAD(fp)
            p.SetNumber("" if kind == "npth" else str(i + 1))
            p.SetAttribute({"smd": pcbnew.PAD_ATTRIB_SMD, "tht": pcbnew.PAD_ATTRIB_PTH, "npth": pcbnew.PAD_ATTRIB_NPTH}[kind])
            p.SetLayerSet({"smd": pcbnew.PAD.SMDMask(), "tht": pcbnew.PAD.PTHMask(), "npth": pcbnew.PAD.UnplatedHoleMask()}[kind])
            p.SetShape(pcbnew.PAD_SHAPE_CIRCLE)
            p.SetSize(self.at(1, 1))
            if kind != "smd":
                p.SetDrillSize(self.at(0.8, 0.8) if kind == "tht" else self.at(1, 1))
            fp.Add(p)
            p.SetFPRelativePosition(self.at(1.6 * i, 0))
            if n:
                p.SetNet(self.net(n))
        if yard:
            c = pcbnew.PCB_SHAPE(fp)
            c.SetShape(pcbnew.SHAPE_T_CIRCLE); c.SetLayer(pcbnew.F_CrtYd); c.SetCenter(self.at(0, 0)); c.SetEnd(self.at(2, 0))
            fp.Add(c)
        fp.SetExcludedFromBOM(bool(flags.get("no_bom")))
        fp.SetExcludedFromPosFiles(bool(flags.get("no_pos")))
        fp.SetDNP(bool(flags.get("dnp")))
        fp.SetBoardOnly(bool(flags.get("board_only")))
        self.b.Add(fp)
        fp.SetPosition(self.at(x, y))
        if back:
            fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
        return fp

    def text(self, s, x, y, back=False, layer=None):
        """A 1 mm board text centred at x, y: its strokes are about 0.8 mm a character wide and 1 mm high."""
        pcbnew = self.pcbnew
        t = pcbnew.PCB_TEXT(self.b)
        t.SetText(s)
        t.SetLayer(layer if layer is not None else pcbnew.B_SilkS if back else pcbnew.F_SilkS)
        t.SetTextSize(self.at(1, 1))
        t.SetTextThickness(pcbnew.FromMM(0.15))
        t.SetMirrored(back)
        t.SetPosition(self.at(x, y))
        self.b.Add(t)

    def keep_out(self, x, y, r=3):
        pcbnew = self.pcbnew
        z = pcbnew.ZONE(self.b)
        z.SetIsRuleArea(True)
        z.SetLayer(pcbnew.F_Cu)
        o = z.Outline()
        o.NewOutline()
        for dx, dy in ((-r, -r), (r, -r), (r, r), (-r, r)):
            o.Append(self.at(x + dx, y + dy))
        self.b.Add(z)

    def labels(self, distance=2.0):
        return found(S.label_findings(self.pcbnew, self.b, S.silk_texts(self.pcbnew, self.b), distance))

    def test_a_name_beside_the_part_on_its_side(self):
        self.part("J1", 10, 10, "USB_C", "USB-C")
        self.text("USB", 10, 12)                  # its strokes end about 1 mm from the pad
        self.part("SW1", 30, 10, "SW_Push", "BOOT")
        self.text("BOOT", 30, 15)                 # 4 mm away
        self.part("JP1", 50, 10, "SolderJumper", "VUSB")
        self.text("CUT", 50, 12, back=True)       # beside it, on the other side of the board
        self.assertEqual(self.labels(), [("labels", "note", "JP1"), ("labels", "note", "SW1")])    # by reference
        self.assertEqual(self.labels(5.0), [("labels", "note", "JP1")])       # [standard] label_distance

    def test_a_through_hole_part_is_read_from_both_sides(self):
        self.part("J1", 10, 10, "PinHeader_1x02", "UART", kind="tht", nets=("TX", "RX"))
        self.text("TX RX", 10.8, 12, back=True)
        self.part("J2", 30, 10, "PinHeader_1x02", "I2C", kind="tht", nets=("SDA", "SCL"), back=True)
        self.text("SDA SCL", 30, 12)
        self.assertEqual(self.labels(), [])

    def test_a_reference_names_nothing_and_a_value_shown_does(self):
        self.part("J1", 10, 10, "JST_PH", "PORT0", show=("Reference",))
        self.part("J2", 30, 10, "JST_PH", "PORT1", show=("Reference", "Value"))
        self.assertEqual(self.labels(), [("labels", "note", "J1")])

    def test_which_parts_want_a_label(self):
        self.part("D1", 10, 10, "LED_0603_1608Metric", "PWR")
        self.part("D2", 20, 10, "D_SOD-123", "SS34")
        self.part("D3", 30, 10, "D_0603", "LED red")
        self.part("LED4", 40, 10, "XL-1608", "STATUS")
        self.part("BT1", 50, 10, "BatteryHolder", "18650")
        self.part("R1", 60, 10)
        self.part("TP1", 70, 10, "TestPoint_Pad_D1.0mm", "VIN", nets=("VIN",))
        self.part("U1", 80, 10, "SOT-23", "LED driver")
        out = S.label_findings(self.pcbnew, self.b, [], 2.0)
        self.assertEqual([(f["item"], f["detail"]) for f in out],
                         [("BT1", "battery holder, 18650"), ("D1", "LED, PWR"), ("D3", "LED, LED red"), ("LED4", "LED, STATUS")])

    def pads(self, rails, where="pcbkit.toml's [standard.rails]", distance=2.0):
        texts = S.silk_texts(self.pcbnew, self.b)
        return S.test_pad_findings(self.pcbnew, self.b, texts, distance, rails, where)

    def test_a_test_pad_on_every_rail(self):
        self.part("U1", 10, 10, "SOT-23", "LDO", nets=("VIN", "GND", "+3V3"))
        self.part("TP1", 30, 10, "TestPoint_Pad_D1.0mm", "3V3", nets=("+3V3",), show=("Value",))
        self.part("X9", 40, 10, "TestPoint_Pad_D1.0mm", "GND", nets=("GND",), show=("Value",))    # by its footprint
        out = self.pads(["GND", "+3V3", "VIN", "VBAT"])               # VBAT is in the schematic, not on this board
        self.assertEqual(found(out), [("test-pads", "note", "VIN")])
        p = self.part("P1", 50, 10, "Pad", "VIN", nets=("VIN",), show=("Value",))
        p.Pads()[0].SetProperty(self.pcbnew.PAD_PROP_TESTPOINT)       # by its pad's fabrication property
        out = self.pads(["GND", "+3V3", "VIN", "VBAT"])
        self.assertEqual(found(out), [])
        self.assertEqual(out[0]["text"], "a test pad on each of 3 rails (GND, +3V3, VIN; pcbkit.toml's [standard.rails])")

    def test_no_rails_known_is_said(self):
        self.part("TP1", 30, 10, "TestPoint_Pad_D1.0mm", "SYS", nets=("SYS",), show=("Value",))
        out = self.pads([], "the schematic names no net with a power symbol")
        self.assertEqual(found(out), [("test-pads", "note", "")])
        self.assertIn("no rails known", out[0]["text"])
        self.assertIn("(test pads are on SYS)", out[0]["text"])

    def test_a_test_pad_is_named_by_a_text_its_value_or_a_legend(self):
        self.part("TP1", 10, 10, "TestPoint_Pad_D1.0mm", "VIN", nets=("VIN",))
        self.text("VIN", 10, 12)
        self.part("TP2", 20, 10, "TestPoint_Pad_D1.0mm", "GND", nets=("GND",), show=("Value",))
        self.part("TP3", 30, 10, "TestPoint_Pad_D1.0mm", "RUN", nets=("RUN",), show=("Reference",))
        self.part("TP8", 40, 10, "TestPoint_Pad_D1.0mm", "GND", nets=("GND",), show=("Reference",))
        self.part("TP12", 50, 10, "TestPoint_Pad_D1.0mm", "GND", nets=("GND",), show=("Reference",))
        self.text("TP3  RUN\nGND  TP8,12", 80, 30)
        self.part("TP4", 60, 10, "TestPoint_Pad_D1.0mm", "EN", nets=("EN",), show=("Reference",))    # in no legend
        self.part("TP5", 70, 10, "TestPoint_Pad_D1.0mm", "IO4", nets=("IO4",))
        self.text("IO4", 70, 12, back=True)                           # on the other side
        self.part("TP13", 90, 10, "TestPoint_Pad_D1.0mm", "X", nets=("X",))    # named in the legend, but shows no reference
        out = self.pads(["GND"])
        self.assertEqual(found(out, "test-pad-labels"), [("test-pad-labels", "note", "TP4"), ("test-pad-labels", "note", "TP5"),
                                                         ("test-pad-labels", "note", "TP13")])
        self.assertEqual([f["detail"] for f in out if f["check"] == "test-pad-labels"], ["EN", "IO4", "X"])

    def test_a_mounting_hole_wants_a_courtyard_or_a_keep_out(self):
        self.assertEqual(found(S.mounting_findings(self.pcbnew, self.b)), [("mounting", "note", "")])
        self.part("H1", 10, 10, "MountingHole_3.2mm_M3", "M3", kind="npth", lcsc="", yard=True)
        self.part("H2", 30, 10, "Hole_gge1", "M3", kind="npth", lcsc="")
        self.part("H3", 50, 10, "Hole_gge2", "M3", kind="npth", lcsc="")
        self.keep_out(50, 10)
        self.part("H4", 70, 10, "MountingHole_3.2mm_M3_Pad", "M3", kind="tht", nets=("GND",), lcsc="")
        self.part("J1", 90, 10, "PinHeader", "X", kind="tht")         # a part with a hole, not a mounting hole
        out = S.mounting_findings(self.pcbnew, self.b)
        self.assertEqual(found(out), [("mounting", "note", "H2"), ("mounting", "note", "H4")])
        self.assertEqual(out[0]["detail"], "1 mm")

    def outline(self, w=60, h=40, cut_out=None):
        """A w x h mm outline from the origin; cut_out: (x, y, r) of a round hole in the board."""
        pcbnew = self.pcbnew
        for x0, y0, x1, y1 in ((0, 0, w, 0), (w, 0, w, h), (w, h, 0, h), (0, h, 0, 0)):
            e = pcbnew.PCB_SHAPE(self.b)
            e.SetShape(pcbnew.SHAPE_T_SEGMENT); e.SetLayer(pcbnew.Edge_Cuts); e.SetStart(self.at(x0, y0)); e.SetEnd(self.at(x1, y1))
            self.b.Add(e)
        if cut_out:
            x, y, r = cut_out
            c = pcbnew.PCB_SHAPE(self.b)
            c.SetShape(pcbnew.SHAPE_T_CIRCLE); c.SetLayer(pcbnew.Edge_Cuts); c.SetCenter(self.at(x, y)); c.SetEnd(self.at(x + r, y))
            self.b.Add(c)

    def fiducial(self, ref, x, y, back=False, name="Fiducial_1mm_Mask2mm"):
        return self.part(ref, x, y, name, "Fiducial", lcsc="", back=back, board_only=True, no_bom=True, no_pos=True)

    def fiducials(self, placed="not-excluded", assembly=True):
        return S.fiducial_findings(self.pcbnew, self.b, placed, assembly)

    def test_three_fiducials_on_each_side_the_assembler_places_parts_on(self):
        self.outline()
        self.part("R1", 20, 20)
        self.part("R2", 30, 20)
        self.part("C1", 40, 20, back=True)
        out = self.fiducials()
        self.assertEqual(found(out), [("fiducials", "note", "front"), ("fiducials", "note", "back")])
        self.assertEqual([f["detail"] for f in out], ["none; 2 placed parts", "none; 1 placed part"])
        for i, (x, y) in enumerate(((5, 5), (55, 5), (5, 35))):
            self.fiducial("FID%d" % (i + 1), x, y)
        self.fiducial("FID4", 5, 5, back=True)
        out = self.fiducials()
        self.assertEqual(found(out), [("fiducials", "note", "back")])
        self.assertEqual(out[0]["detail"], "1; 1 placed part")
        self.fiducial("FID5", 55, 5, back=True)
        self.fiducial("FID6", 55, 35, back=True)
        out = self.fiducials()
        self.assertEqual(found(out), [])
        self.assertEqual(out[0]["text"], "3 on the front and 3 on the back, each 4.35 mm or more from the edge")

    def test_a_side_where_nothing_is_placed_needs_none(self):
        self.outline()
        self.part("R1", 20, 20)
        self.part("J1", 40, 20, "PinHeader", "X", kind="tht", back=True, no_pos=True)     # soldered by hand
        self.part("R9", 30, 20, back=True, dnp=True)
        self.part("J2", 50, 20, "JST_PH", "X", lcsc="", back=True)        # no LCSC number: in no placement file
        self.assertEqual(found(self.fiducials()), [("fiducials", "note", "front")])
        self.assertEqual([f["level"] for f in self.fiducials(assembly=False)], ["ok"])      # a bare board
        self.assertEqual(found(self.fiducials("field:Assembly=jlc")), [])                  # nothing is the assembler's
        self.assertIn("no part is placed", self.fiducials("field:Assembly=jlc")[0]["text"])

    def test_a_fiducial_too_near_the_edge_or_a_cut_out(self):
        self.outline(cut_out=(30, 20, 3))
        self.part("R1", 20, 30)
        self.fiducial("FID1", 4.35, 4.35)             # exactly as far as the standard asks
        self.fiducial("FID2", 57, 5)                  # 3 mm from the right edge
        self.fiducial("FID3", 30, 26)                 # 3 mm from the cut-out
        out = self.fiducials()
        self.assertEqual([(f["item"], f["detail"]) for f in out if f["level"] != "ok"],
                         [("FID2", "3.00 mm"), ("FID3", "3.00 mm")])
        self.assertIn("under 4.35 mm from the board's edge", out[0]["text"])

    def test_three_fiducials_on_one_line(self):
        self.outline()
        self.part("R1", 20, 20)
        for i, x in enumerate((5, 30, 55)):
            self.fiducial("FID%d" % (i + 1), x, 5)
        out = self.fiducials()
        self.assertEqual(found(out), [("fiducials", "note", "front")])
        self.assertIn("lie on one line", out[0]["text"])

    def test_what_a_fiducial_is_and_a_board_without_an_outline(self):
        self.part("R1", 20, 20)
        self.fiducial("FID1", 5, 5)                                          # by its footprint's name
        self.part("FID2", 55, 5, "Dot", "x", lcsc="", board_only=True)       # by its reference
        p = self.part("M1", 5, 35, "Dot", "x", lcsc="", board_only=True)     # by its pad's fabrication property
        p.Pads()[0].SetProperty(self.pcbnew.PAD_PROP_FIDUCIAL_GLBL)
        local = self.part("U1", 30, 20, "BGA", "x")                          # a part's own fiducial pad is not one
        local.Pads()[0].SetProperty(self.pcbnew.PAD_PROP_FIDUCIAL_LOCAL)
        out = self.fiducials()
        self.assertEqual(found(out), [])
        self.assertEqual(out[0]["text"], "3 on the front (no closed outline to measure their distance from)")

    def references(self):
        return S.reference_findings(self.pcbnew, self.b, S.silk_texts(self.pcbnew, self.b))

    def test_every_soldered_parts_reference_is_on_the_silkscreen(self):
        self.part("R1", 10, 10, show=("Reference",))
        for i in range(5, 9):
            self.part("R%d" % i, 10 * i, 30, show=("Reference",))
        self.part("R2", 20, 10)                                              # hidden
        fab = self.part("R3", 30, 10, show=("Reference",))                   # shown, but on the fab layer
        fab.Reference().SetLayer(self.pcbnew.F_Fab)
        self.part("J1", 40, 10, "PinHeader", "X", kind="tht", no_pos=True)   # soldered by hand: a soldered part
        # none of these is a soldered part
        self.part("TP1", 50, 10, "TestPoint_Pad_D1.0mm", "X", nets=("X",))
        self.part("H1", 60, 10, "MountingHole_3.2mm_M3", "M3", kind="npth", lcsc="")
        self.fiducial("FID1", 70, 10)
        self.part("R4", 80, 10, dnp=True)
        self.part("JP1", 90, 10, "SolderJumper", "X", lcsc="", no_bom=True)
        self.part("LOGO", 100, 10, "Logo", "", lcsc="", board_only=True)
        out = self.references()
        self.assertEqual(found(out), [("references", "note", "J1"), ("references", "note", "R2"), ("references", "note", "R3")])
        for ref in ("J1", "R2", "R3"):
            self.b.FindFootprintByReference(ref).Reference().SetVisible(True)
        fab.Reference().SetLayer(self.pcbnew.F_SilkS)
        out = self.references()
        self.assertEqual([(f["level"], f["text"]) for f in out],
                         [("ok", "8 soldered parts, each with its reference on the silkscreen")])

    def test_references_left_off_most_of_a_board_are_one_finding(self):
        for i in range(1, 6):
            self.part("R%d" % i, 10 * i, 10, show=("Reference",) if i < 3 else ())
        out = self.references()
        self.assertEqual(found(out), [("references", "note", "")])
        self.assertTrue(out[0]["text"].startswith("3 of 5 soldered parts have no reference on the silkscreen"), out)
        self.part("R6", 60, 10, show=("Reference",))                       # now half: each is named
        self.assertEqual(found(self.references()), [("references", "note", "R%d" % i) for i in (3, 4, 5)])

    def lcsc(self, placed="not-excluded", assembly=True):
        return S.lcsc_findings(self.pcbnew, self.b, placed, assembly)

    def test_a_placed_part_without_an_lcsc_number(self):
        self.part("R1", 10, 10, lcsc="C25804")
        self.part("R2", 20, 10, lcsc="C25804")
        self.part("J1", 30, 10, "JST_PH", "PORT", lcsc="")
        self.part("J2", 40, 10, "JST_PH", "PORT", lcsc="", no_pos=True)       # soldered by hand: marked so
        self.part("TP1", 50, 10, "TestPoint", "X", lcsc="", no_bom=True, no_pos=True)
        self.part("R3", 60, 10, lcsc="", dnp=True)
        self.part("LOGO", 70, 10, "Logo", "", lcsc="", board_only=True)
        out, parts = self.lcsc()
        self.assertEqual(found(out), [("lcsc", "error", "J1")])
        self.assertIn("Exclude from position files", out[0]["text"])
        self.assertEqual(parts, {"C25804": (["R1", "R2"], "")})
        self.assertEqual([f["level"] for f in self.lcsc(assembly=False)[0]], ["ok"])      # a bare board places nothing

    def test_placed_by_a_field(self):
        a = self.part("R1", 10, 10, lcsc="")
        a.SetField("Assembly", "jlc")
        self.part("J1", 30, 10, "JST_PH", "PORT", lcsc="")             # not the assembler's
        self.assertEqual(found(self.lcsc("field:Assembly=jlc")[0]), [("lcsc", "error", "R1")])

    def test_the_summary_of_pcbkit_fab_names_them(self):
        from pcbkit.release import jlc_fab
        self.part("R1", 10, 10, lcsc="C25804")
        self.part("J10", 30, 10, "JST_PH", "PORT", lcsc="")
        self.part("J2", 40, 10, "JST_PH", "PORT", lcsc="")
        self.assertEqual([fp.GetReference() for fp in jlc_fab.unnumbered(self.b, jlc_fab.selector(None))], ["J2", "J10"])


@unittest.skipUnless(kicad.have_pcbnew(), "needs KiCad's Python")
class Command(unittest.TestCase):
    """pcbkit standard on a project: the findings together, a waiver from pcbkit.toml, the exit status."""
    TOML = '[boards]\nfolders = ["boards/*"]\n'

    def setUp(self):
        import pcbnew
        self.dir = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.dir.name)
        d = os.path.join(self.root, "boards", "demo")
        os.makedirs(d)
        b = pcbnew.BOARD()
        tb = b.GetTitleBlock()
        tb.SetTitle("Demo")
        tb.SetRevision("v1.0")
        b.SetTitleBlock(tb)
        t = pcbnew.PCB_TEXT(b)
        t.SetText("Demo ${REVISION} ${ISSUE_DATE}")
        t.SetLayer(pcbnew.F_SilkS)
        b.Add(t)
        fp = pcbnew.FOOTPRINT(b)
        fp.SetFPID(pcbnew.LIB_ID("demo", "R_0603"))
        fp.SetReference("R1")
        fp.SetValue("10k")
        for f in fp.GetFields():
            f.SetVisible(False)
        p = pcbnew.PAD(fp)
        p.SetNumber("1")
        p.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
        p.SetLayerSet(pcbnew.PAD.SMDMask())
        p.SetSize(pcbnew.VECTOR2I(pcbnew.FromMM(1), pcbnew.FromMM(1)))
        fp.Add(p)
        b.Add(fp)
        kicad.save_board(b, os.path.join(d, "demo.kicad_pcb"))
        write(os.path.join(d, "demo.kicad_sch"), sheet("Demo", ["power.kicad_sch"]))
        write(os.path.join(d, "power.kicad_sch"), sheet())
        write(os.path.join(self.root, "pcbkit.toml"), self.TOML)

    def tearDown(self):
        self.dir.cleanup()

    def standard(self, *args):
        """Run the command in the project: (exit status, stdout)."""
        from pcbkit.release import versions  # noqa: F401
        cwd, out, code = os.getcwd(), io.StringIO(), 0
        os.chdir(self.root)
        try:
            with contextlib.redirect_stdout(out):
                S.main(list(args))
        except SystemExit as e:
            code = e.code or 0
        finally:
            os.chdir(cwd)
        return code, out.getvalue()

    def test_errors_give_exit_status_1_and_each_line_of_the_standard_is_said(self):
        code, out = self.standard()
        self.assertEqual(code, 1)
        said = out.splitlines()
        self.assertEqual(said[0], "boards/demo/demo.kicad_pcb: what a released board carries (docs/CONVENTIONS.md)")
        self.assertIn("  name, version, date  ok      the silkscreen prints the version: 'Demo ${REVISION} ${ISSUE_DATE}'", said)
        self.assertIn("  title blocks         error   no title block: power.kicad_sch", said)
        self.assertTrue(any(l.startswith("  parts                error   placed, but no LCSC number, so pcbkit fab leaves it out")
                            and l.endswith(": R1 (10k)") for l in said), out)
        self.assertTrue(any("to look at" in l and "the board's name" in l for l in said), out)
        self.assertRegex(said[-1], r"^  2 errors, \d+ notes?$")

    def test_a_waiver_in_pcbkit_toml_and_json(self):
        write(os.path.join(self.root, "pcbkit.toml"), self.TOML + '\n[[standard.waive]]\nboard = "demo"\n'
              'check = "title-block"\nnote = "generated sheet; a title in v1.1"\n\n[[standard.waive]]\ncheck = "lcsc"\n'
              'items = ["R1"]\nnote = "a 0 ohm link fitted by hand"\n\n[[standard.waive]]\nboard = "other"\n'
              'check = "mounting"\nnote = "not this board"\n')
        code, out = self.standard("demo", "--json")
        self.assertEqual(code, 0)
        (r,) = json.loads(out)
        self.assertEqual((r["pcb"], r["key"], r["error"], r["waived"]), ("boards/demo/demo.kicad_pcb", "demo", 0, 2))
        waived = [(f["check"], f["item"], f["note"], f["was"]) for f in r["findings"] if f["level"] == "waived"]
        self.assertEqual(waived, [("title-block", "power.kicad_sch", "generated sheet; a title in v1.1", "error"),
                                  ("lcsc", "R1", "a 0 ohm link fitted by hand", "error")])
        self.assertIn(("mounting", "note"), [(f["check"], f["level"]) for f in r["findings"]])    # not waived here
        code, out = self.standard("boards/demo")
        self.assertIn("waived  generated sheet; a title in v1.1 (waives: no title block: power.kicad_sch)", out)

    def test_a_board_outside_any_project_is_read_with_the_defaults(self):
        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        path = os.path.join(os.path.realpath(other.name), "loose.kicad_pcb")
        with open(os.path.join(self.root, "boards", "demo", "demo.kicad_pcb"), encoding="utf-8") as fh:
            write(path, fh.read())
        code, out = self.standard(path)
        self.assertEqual(code, 1)
        self.assertTrue(out.startswith(path + ": what a released board carries"), out)
        self.assertIn("ok      the board has a title (it has no schematic)", out)


if __name__ == "__main__":
    unittest.main()
