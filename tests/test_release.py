"""pcbkit release on a fixture project: which part of the version a change bumps, and the KiCad copy in each release.

  python3 tests/test_release.py     # re-runs itself under KiCad's Python when pcbnew is not importable

The fixture is one board drawn in pcbnew (two resistors and a capacitor, a track, a 3-D model and a library table
that reach ../../lib/), a root schematic with a sheet in a subfolder, and a pcbkit.toml that releases it without
ERC or DRC (the fixture tests the versions, not the design) and without previews or production. It is released once
as v1.0; each case changes a copy of that project and runs pcbkit release on it.
"""
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pcbkit import kicad, sexp, tomlw  # noqa: E402
from pcbkit.release import freeze  # noqa: E402
from pcbkit.release import versions as V  # noqa: E402

if __name__ == "__main__" and not kicad.have_pcbnew() and not os.environ.get("PCBKIT_REEXEC"):
    env = dict(os.environ, PCBKIT_REEXEC="1")
    env["PYTHONPATH"] = ROOT + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    os.execve(kicad.KICAD_PYTHON, [kicad.KICAD_PYTHON, os.path.abspath(__file__), *sys.argv[1:]], env)


def read(*path):
    with open(os.path.join(*path), encoding="utf-8") as fh:
        return fh.read()


def write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def part(ref, value="10k", fp="R_0603", lcsc="C1", mpn="", flags=()):
    return [ref, fp, value, lcsc, mpn, list(flags)]


class CircuitChanges(unittest.TestCase):
    """What circuit_changes() says, and what the circuit fingerprint counts, on circuits written out by hand."""
    def setUp(self):
        self.old = dict(parts=[part("R1"), part("R3"), part("U2", "LDO", "SOT-23-5", "C5")],
                        nets={"VIN": [["R1", "1"], ["U2", "1"]], "X": [["R3", "1"], ["U2", "4"]],
                              "GND": [["R1", "2"], ["U2", "2"]]})

    def changed(self, parts=None, nets=None):
        return dict(parts=parts or self.old["parts"], nets=dict(self.old["nets"], **(nets or {})))

    def test_value_change(self):
        new = self.changed(parts=[part("R1"), part("R3", "4.7k"), part("U2", "LDO", "SOT-23-5", "C5")])
        self.assertEqual(V.circuit_changes(self.old, new), ["R3 value 10k -> 4.7k"])
        self.assertNotEqual(V.circuit_fingerprint(self.old), V.circuit_fingerprint(new))

    def test_fields_and_flags(self):
        new = self.changed(parts=[part("R1", lcsc="C2"), part("R3", flags=["dnp"]),
                                  part("U2", "LDO", "SOT-23-5", "C5", mpn="AP2112K")])
        self.assertEqual(V.circuit_changes(self.old, new),
                         ["R1 LCSC C1 -> C2", "R3 DNP", "U2 MPN (none) -> AP2112K"])

    def test_pin_moves_to_a_net(self):
        new = self.changed(nets={"X": [["R3", "1"], ["U2", "4"], ["U2", "5"]]})
        self.assertEqual(V.circuit_changes(self.old, new), ["net X gains U2.5"])
        new = self.changed(nets={"X": [["R3", "1"], ["U2", "5"]]})
        self.assertEqual(V.circuit_changes(self.old, new), ["net X gains U2.5 and loses U2.4"])

    def test_a_net_with_every_pin_replaced_is_said_once(self):
        new = self.changed(nets={"VIN": [["R3", "2"], ["U2", "3"]]})
        self.assertEqual(V.circuit_changes(self.old, new), ["net VIN gains R3.2, U2.3 and loses R1.1, U2.1"])

    def test_a_net_down_to_one_pin(self):
        new = self.changed(nets={"VIN": [["R1", "1"], ["U2", "1"], ["R3", "1"]], "X": [["U2", "4"]]})
        self.assertEqual(V.circuit_changes(self.old, new), ["net VIN gains R3.1", "net X loses R3.1"])
        self.assertEqual(V.circuit_changes(new, self.old), ["net VIN loses R3.1", "net X gains R3.1"])

    def test_parts_added_and_removed(self):
        new = dict(parts=self.old["parts"][1:] + [part("C9", "100n", "C_0603", "C14663")],
                   nets={"VIN": [["C9", "1"], ["U2", "1"]], "X": [["R3", "1"], ["U2", "4"]],
                         "GND": [["C9", "2"], ["U2", "2"]]})
        self.assertEqual(V.circuit_changes(self.old, new),
                         ["adds C9 (100n, C_0603, C14663)", "removes R1", "net VIN gains C9.1 and loses R1.1",
                          "net GND gains C9.2 and loses R1.2"])

    def test_a_renamed_net_is_the_same_circuit(self):
        nets = dict(self.old["nets"])
        nets["Y"] = nets.pop("X")
        new = dict(parts=self.old["parts"], nets=nets)
        self.assertEqual(V.circuit_fingerprint(self.old), V.circuit_fingerprint(new))
        self.assertEqual(V.circuit_changes(self.old, new), [])

    def test_a_net_of_one_pin_connects_nothing(self):
        new = self.changed(nets={"TP": [["U2", "5"]]})
        self.assertEqual(V.circuit_fingerprint(self.old), V.circuit_fingerprint(new))


KICAD7_PCB = """(kicad_pcb (version 20221018) (generator pcbnew)
  (net 0 "")
  (net 1 "VIN")
  (net 2 "GND")
  (footprint "demo:R_0603" (layer "F.Cu")
    (property "LCSC" "C25804")
    (attr smd)
    (fp_text reference "R1" (at 0 -1.5) (layer "F.SilkS"))
    (fp_text value "10k" (at 0 1.5) (layer "F.Fab"))
    (pad "1" smd rect (at -0.8 0) (size 0.8 0.9) (layers "F.Cu") (net 1 "VIN"))
    (pad "2" smd rect (at 0.8 0) (size 0.8 0.9) (layers "F.Cu") (net 2 "GND"))
  )
  (footprint "demo:C_0603" locked (layer "F.Cu")
    (attr smd exclude_from_bom)
    (fp_text reference "C1" (at 0 -1.5) (layer "F.SilkS"))
    (fp_text value "100n" (at 0 1.5) (layer "F.Fab"))
    (pad "1" smd rect (at -0.8 0) (size 0.8 0.9) (layers "F.Cu") (net 1 "VIN"))
    (pad "2" smd rect (at 0.8 0) (size 0.8 0.9) (layers "F.Cu") (net 0 ""))
    (pad "" np_thru_hole circle (at 0 0) (size 1 1) (drill 1) (layers "*.Cu"))
  )
  (footprint "demo:Logo" (layer "F.Cu")
    (attr board_only)
    (fp_text reference "G1" (at 0 0) (layer "F.SilkS"))
    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net 2 "GND"))
  )
)
"""


class BoardFile(unittest.TestCase):
    """circuit() reads the board's S-expressions, not KiCad 10's tab-indented lines."""
    def test_a_kicad_7_board_indented_with_spaces(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "old.kicad_pcb")
            write(path, KICAD7_PCB)
            c = V.circuit(path)
        self.assertEqual(c["parts"], [part("C1", "100n", "C_0603", "", flags=["exclude_from_bom"]),
                                      part("R1", lcsc="C25804")])
        self.assertEqual(c["nets"], {"GND": [["R1", "2"]], "VIN": [["C1", "1"], ["R1", "1"]]})

    def test_the_order_of_the_footprints_in_the_file_doesnt_count(self):
        """R1 and R01 sort alike by their numbers; the circuit is the same whichever comes first in the file."""
        def board(refs):
            fps = "".join(f'  (footprint "demo:R_0603" (layer "F.Cu")\n    (property "Reference" "{r}")\n'
                          f'    (property "Value" "{v}")\n    (pad "1" smd rect (net 1 "VIN"))\n  )\n'
                          for r, v in refs)
            return '(kicad_pcb (version 20221018)\n  (net 1 "VIN")\n' + fps + ")\n"
        with tempfile.TemporaryDirectory() as d:
            a, b = os.path.join(d, "a.kicad_pcb"), os.path.join(d, "b.kicad_pcb")
            write(a, board([("R1", "10k"), ("R01", "4.7k")]))
            write(b, board([("R01", "4.7k"), ("R1", "10k")]))
            self.assertEqual(V.circuit(a), V.circuit(b))
            self.assertEqual(V.circuit_fingerprint(V.circuit(a)), V.circuit_fingerprint(V.circuit(b)))


def region(*xy, d02=1):
    """A Gerber with one region (a zone fill) through these points, its first d02 of them moves (D02)."""
    pts = [f"X{x}Y{y}D0{2 if i < d02 else 1}*" for i, (x, y) in enumerate(xy)]
    return ["%FSLAX46Y46*%", "%MOMM*%", "G36*"] + pts + ["G37*", "M02*"]


class ZoneFills(unittest.TestCase):
    """gerber_gap: how far a refilled zone's outline lies from the release's, bounded over every point of it."""
    def test_a_dropped_vertex_on_a_straight_edge(self):
        a = region((0, 0), (10000, 1), (20000, 0), (20000, 20000), (0, 0))
        b = region((0, 0), (20000, 0), (20000, 20000), (0, 0))
        self.assertAlmostEqual(V.gerber_gap(a, b), 0.000001)

    def test_an_edge_moved_by_more_than_the_tolerance_between_sampled_points(self):
        """Every vertex and every point 5 um apart along both outlines is within 5 um of the other; the outlines
        themselves are 5.4 um apart in places, so this is a change."""
        a = region((0, 0), (16590, 1990), (16927, 2386), (17848, -1386), (20000, 0), (20000, 20000), (0, 0))
        b = region((0, 0), (17167, -4498), (20000, 0), (20000, 20000), (0, 0))
        self.assertIsNone(V.gerber_gap(a, b))
        self.assertIsNone(V.gerber_gap(b, a))

    def test_outlines_far_apart_are_rejected_at_their_first_point(self):
        """Two long outlines 1 mm apart: no distance matrix of one against the other is made first."""
        a = [(i * 1000, 0) for i in range(3000)]
        b = [(i * 1000, 1000000) for i in range(3000)]
        calls = [0]
        real = V._seg_distance

        def counted(*args):
            calls[0] += 1
            return real(*args)
        with mock.patch.object(V, "_seg_distance", counted):
            self.assertIsNone(V._polyline_gap(a, b, 5000))
        self.assertLessEqual(calls[0], len(b))

    def test_a_long_edge_just_inside_the_tolerance(self):
        a = region((0, 0), (1000000, 4900), (2000000, 0), (2000000, 20000), (0, 0))
        b = region((0, 0), (2000000, 0), (2000000, 20000), (0, 0))
        self.assertAlmostEqual(V.gerber_gap(a, b), 0.0049)


class Rewrite(unittest.TestCase):
    """Paths relative to the project, as the copy in releases/<board>/v<label>/kicad/ must write them."""
    def test_paths_reach_the_same_files(self):
        src, dst = "/p/boards/demo", "/p/releases/demo/v1.0/kicad"
        text = ('(uri "${KIPRJMOD}/../../lib/demo.pretty")\n(model "${KIPRJMOD}/../../lib/3d/r.step")\n'
                '(model "models/c.step")\n(model "${KICAD10_3DMODEL_DIR}/R.3dshapes/R.step")\n(model "/abs/x.step")')
        self.assertEqual(freeze._rewrite(text, src, dst, True),
                         '(uri "${KIPRJMOD}/../../../../lib/demo.pretty")\n'
                         '(model "${KIPRJMOD}/../../../../lib/3d/r.step")\n'
                         '(model "${KIPRJMOD}/../../../../boards/demo/models/c.step")\n'
                         '(model "${KICAD10_3DMODEL_DIR}/R.3dshapes/R.step")\n(model "/abs/x.step")')
        # a folder name with a quote in it is escaped, and an escaped one read as one string
        src_q = '/p/boards/de"mo'
        out = freeze._rewrite('(model "models/c.step")\n(model "${KIPRJMOD}/sub\\"dir/m.step")', src_q, dst, True)
        self.assertEqual(out, '(model "${KIPRJMOD}/../../../../boards/de\\"mo/models/c.step")\n'
                              '(model "${KIPRJMOD}/../../../../boards/de\\"mo/sub\\"dir/m.step")')
        self.assertEqual(sexp.parse("(x " + out + ")")[1][1], '${KIPRJMOD}/../../../../boards/de"mo/models/c.step')
        # only a board's 3-D models take a bare relative path as the project's
        self.assertIn('(model "models/c.step")', freeze._rewrite(text, src, dst, False))


def schematic(uuid, sheets=(), at=None):
    """A schematic with a sheet symbol per (sheet uuid, Sheetfile, page); at is its own sheet path (the sheet
    symbols' uuids from the root's down), None for the root."""
    out = (f'(kicad_sch (version 20250114) (generator "eeschema") (generator_version "10.0")\n\t(uuid "{uuid}")\n'
           '\t(paper "A4")\n\t(lib_symbols)\n')
    for i, (sid, name, page) in enumerate(sheets):
        out += (f'\t(sheet (at {20 + 50 * i} 50) (size 40 20) (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no)\n'
                f'\t\t(stroke (width 0.1524) (type solid)) (fill (color 0 0 0 0.0)) (uuid "{sid}")\n'
                f'\t\t(property "Sheetname" "S{page}" (at 50 49 0) (effects (font (size 1.27 1.27)) (justify left bottom)))\n'
                f'\t\t(property "Sheetfile" "{name}" (at 50 71 0) (effects (font (size 1.27 1.27)) (justify left top)))\n'
                f'\t\t(instances (project "demo" (path "{at or "/" + uuid}" (page "{page}")))))\n')
    if at is None:
        out += '\t(sheet_instances (path "/" (page "1")))\n'
    return out + "\t(embedded_fonts no)\n)\n"


def uid(n):
    return f"5ee7c0de-0000-4000-8000-{n:012d}"


class Sheets(unittest.TestCase):
    """Every sheet the copy shows is in the copy, and its schematic reads nothing from the working project."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.board = os.path.join(self.tmp.name, "p", "boards", "demo")
        os.makedirs(os.path.join(self.board, "sheets"))
        os.makedirs(os.path.join(self.tmp.name, "p", "boards", "shared"))
        write(os.path.join(self.board, "demo.kicad_pro"), "{}\n")
        root = [(uid(11), "sheets/power.kicad_sch", "2"),                       # relative, in the folder
                (uid(12), os.path.join(self.board, "sheets", "abs.kicad_sch"), "3"),   # absolute, in the folder
                (uid(13), "../shared/io.kicad_sch", "4"),                        # outside it, with a sheet of its own
                (uid(15), "${KIPRJMOD}/sheets/kp.kicad_sch", "6")]               # through ${KIPRJMOD}
        write(os.path.join(self.board, "demo.kicad_sch"), schematic(uid(1), root))
        write(os.path.join(self.board, "sheets", "power.kicad_sch"), schematic(uid(2), at=f"/{uid(1)}/{uid(11)}"))
        write(os.path.join(self.board, "sheets", "abs.kicad_sch"), schematic(uid(3), at=f"/{uid(1)}/{uid(12)}"))
        write(os.path.join(self.board, "sheets", "kp.kicad_sch"), schematic(uid(7), at=f"/{uid(1)}/{uid(15)}"))
        write(os.path.join(self.tmp.name, "p", "boards", "shared", "io.kicad_sch"),
              schematic(uid(4), [(uid(14), "leaf.kicad_sch", "5")], at=f"/{uid(1)}/{uid(13)}"))
        write(os.path.join(self.tmp.name, "p", "boards", "shared", "leaf.kicad_sch"),
              schematic(uid(5), at=f"/{uid(1)}/{uid(13)}/{uid(14)}"))

    def test_the_copy_holds_every_sheet(self):
        k = os.path.join(self.tmp.name, "kicad")
        written, lost = freeze.freeze(os.path.join(self.board, "demo.kicad_pcb"), k)
        self.assertEqual(sorted(written), ["demo.kicad_pro", "demo.kicad_sch", "outside/io.kicad_sch",
                                           "outside/leaf.kicad_sch", "sheets/abs.kicad_sch", "sheets/kp.kicad_sch",
                                           "sheets/power.kicad_sch"])
        self.assertEqual(lost, [])
        root = read(k, "demo.kicad_sch")
        for name in ('"sheets/power.kicad_sch"', '"sheets/abs.kicad_sch"', '"outside/io.kicad_sch"',
                     '"sheets/kp.kicad_sch"'):
            self.assertIn(f'(property "Sheetfile" {name}', root)
        self.assertNotIn("KIPRJMOD", root)
        self.assertIn('(property "Sheetfile" "leaf.kicad_sch"', read(k, "outside", "io.kicad_sch"))
        # with the working project gone, kicad-cli still draws all six sheets from the copy
        os.rename(os.path.join(self.tmp.name, "p"), os.path.join(self.tmp.name, "gone"))
        pdf = os.path.join(self.tmp.name, "copy.pdf")
        r = subprocess.run([kicad.KICAD_CLI, "sch", "export", "pdf", "-o", pdf, os.path.join(k, "demo.kicad_sch")],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(pdf, "rb") as fh:
            self.assertEqual(len(re.findall(rb"/Type\s*/Page[^s]", fh.read())), 6)

    def test_outside_names_the_project_has_are_skipped(self):
        """The board folder's own outside/io*.kicad_sch, unreferenced, aren't overwritten in the copy's names."""
        os.makedirs(os.path.join(self.board, "outside"))
        for n in ("io", "io-1", "io-2", "io-3", "io-4"):
            write(os.path.join(self.board, "outside", n + ".kicad_sch"), schematic(uid(9), at="/"))
        k = os.path.join(self.tmp.name, "kicad")
        written, lost = freeze.freeze(os.path.join(self.board, "demo.kicad_pcb"), k)
        self.assertIn("outside/io-5.kicad_sch", written)
        self.assertIn('(property "Sheetfile" "outside/io-5.kicad_sch"', read(k, "demo.kicad_sch"))
        self.assertEqual(lost, [])

    def test_a_sheet_with_a_quote_in_its_name(self):
        shared = os.path.join(self.tmp.name, "p", "boards", "shared")
        write(os.path.join(shared, 'q"t.kicad_sch'), schematic(uid(8), at="/"))
        root = read(self.board, "demo.kicad_sch").replace('"../shared/io.kicad_sch"', '"../shared/q\\"t.kicad_sch"')
        write(os.path.join(self.board, "demo.kicad_sch"), root)
        k = os.path.join(self.tmp.name, "kicad")
        written, lost = freeze.freeze(os.path.join(self.board, "demo.kicad_pcb"), k)
        self.assertIn('outside/q"t.kicad_sch', written)
        self.assertIn('(property "Sheetfile" "outside/q\\"t.kicad_sch"', read(k, "demo.kicad_sch"))
        self.assertEqual(lost, [])
        pdf = os.path.join(self.tmp.name, "q.pdf")
        r = subprocess.run([kicad.KICAD_CLI, "sch", "export", "pdf", "-o", pdf, os.path.join(k, "demo.kicad_sch")],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(pdf, "rb") as fh:
            self.assertEqual(len(re.findall(rb"/Type\s*/Page[^s]", fh.read())), 5)    # q"t has no leaf sheet

    def test_a_variable_in_the_middle_of_a_sheet_path(self):
        """KiCad expands ${VAR} anywhere in a Sheetfile; the copy names the copied sheet and needs no variable."""
        root = read(self.board, "demo.kicad_sch").replace('"${KIPRJMOD}/sheets/kp.kicad_sch"',
                                                          '"${KIPRJMOD}/sheets/${PCBKIT_TEST_SHEET}.kicad_sch"')
        write(os.path.join(self.board, "demo.kicad_sch"), root)
        k = os.path.join(self.tmp.name, "kicad")
        with mock.patch.dict(os.environ, {"PCBKIT_TEST_SHEET": "kp"}):
            written, lost = freeze.freeze(os.path.join(self.board, "demo.kicad_pcb"), k)
        self.assertIn("sheets/kp.kicad_sch", written)
        self.assertEqual(lost, [])
        self.assertIn('(property "Sheetfile" "sheets/kp.kicad_sch"', read(k, "demo.kicad_sch"))
        self.assertNotIn("PCBKIT_TEST_SHEET", read(k, "demo.kicad_sch"))
        # without the variable, and with the working project gone, the copy still draws all six sheets
        os.rename(os.path.join(self.tmp.name, "p"), os.path.join(self.tmp.name, "gone"))
        pdf = os.path.join(self.tmp.name, "v.pdf")
        r = subprocess.run([kicad.KICAD_CLI, "sch", "export", "pdf", "-o", pdf, os.path.join(k, "demo.kicad_sch")],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(pdf, "rb") as fh:
            self.assertEqual(len(re.findall(rb"/Type\s*/Page[^s]", fh.read())), 6)

    def test_the_project_files_paths_are_json(self):
        """The .kicad_pro is JSON: a path written as caf\\u00e9 is café, and comes back out as a string JSON reads."""
        doc = 'docs/café "draft".pdf'
        write(os.path.join(self.board, "demo.kicad_pro"),
              json.dumps({"text_variables": {"DOC": "${KIPRJMOD}/" + doc}}, indent=2) + "\n")   # \\u00e9, \\"
        k = os.path.join(self.tmp.name, "kicad")
        freeze.freeze(os.path.join(self.board, "demo.kicad_pcb"), k)
        with open(os.path.join(k, "demo.kicad_pro"), encoding="utf-8") as fh:
            got = json.load(fh)["text_variables"]["DOC"]
        self.assertEqual(got, "${KIPRJMOD}/" + os.path.relpath(os.path.join(self.board, doc), k).replace(os.sep, "/"))

    def test_a_missing_sheet_is_reported(self):
        os.remove(os.path.join(self.board, "sheets", "power.kicad_sch"))
        written, lost = freeze.freeze(os.path.join(self.board, "demo.kicad_pcb"), os.path.join(self.tmp.name, "k"))
        self.assertNotIn("sheets/power.kicad_sch", written)
        self.assertEqual(lost, [("demo.kicad_sch", "sheets/power.kicad_sch", "sheet file not found")])

    def test_the_parent_sheets_folder_comes_first(self):
        """As in KiCad: a nested sheet's file is looked for beside the sheet that names it, then in the project's."""
        write(os.path.join(self.board, "leaf.kicad_sch"), schematic(uid(6), at="/"))    # the project folder's, unused
        _, names, _ = freeze.sheets(os.path.join(self.board, "demo.kicad_sch"))
        self.assertEqual(names[os.path.join("outside", "io.kicad_sch")],
                         {"leaf.kicad_sch": os.path.join("outside", "leaf.kicad_sch")})


SCH_ROOT = """(kicad_sch (version 20250114) (generator "eeschema") (generator_version "10.0")
\t(uuid "1d2b7c0e-0000-4000-8000-000000000001")
\t(paper "A4")
\t(title_block (title "demo") (date "2026-11-01") (rev "v1.0"))
\t(lib_symbols)
\t(text "the demo board" (exclude_from_sim no) (at 20 20 0) (effects (font (size 1.27 1.27)) (justify left))
\t\t(uuid "1d2b7c0e-0000-4000-8000-000000000002"))
\t(sheet (at 50 50) (size 40 20) (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no)
\t\t(stroke (width 0.1524) (type solid)) (fill (color 0 0 0 0.0)) (uuid "1d2b7c0e-0000-4000-8000-000000000003")
\t\t(property "Sheetname" "Power" (at 50 49 0) (effects (font (size 1.27 1.27)) (justify left bottom)))
\t\t(property "Sheetfile" "sheets/power.kicad_sch" (at 50 71 0) (effects (font (size 1.27 1.27)) (justify left top)))
\t\t(instances (project "demo" (path "/1d2b7c0e-0000-4000-8000-000000000001" (page "2")))))
\t(sheet_instances (path "/" (page "1")))
\t(embedded_fonts no)
)
"""
SCH_POWER = """(kicad_sch (version 20250114) (generator "eeschema") (generator_version "10.0")
\t(uuid "1d2b7c0e-0000-4000-8000-000000000004")
\t(paper "A4")
\t(lib_symbols)
\t(text "the power sheet" (exclude_from_sim no) (at 20 20 0) (effects (font (size 1.27 1.27)) (justify left))
\t\t(uuid "1d2b7c0e-0000-4000-8000-000000000005"))
\t(embedded_fonts no)
)
"""
MODEL = "${KIPRJMOD}/../../lib/3dmodels/r0603.step"
MISSING = "${KIPRJMOD}/models/missing.step"


def draw_board(path):
    """Two resistors and a capacitor between VIN, OUT and GND, a track from R1 to R2, inside a 30 x 20 mm outline."""
    import pcbnew
    mm = pcbnew.FromMM
    b = pcbnew.BOARD()
    tb = b.GetTitleBlock()
    tb.SetTitle("demo")
    tb.SetRevision("v1.0")
    tb.SetDate("2026-11-01")
    b.SetTitleBlock(tb)
    nets = {}
    for n in ("VIN", "OUT", "GND"):
        nets[n] = pcbnew.NETINFO_ITEM(b, n)
        b.Add(nets[n])
    for x0, y0, x1, y1 in ((0, 0, 30, 0), (30, 0, 30, 20), (30, 20, 0, 20), (0, 20, 0, 0)):
        s = pcbnew.PCB_SHAPE(b)
        s.SetShape(pcbnew.SHAPE_T_SEGMENT)
        s.SetStart(pcbnew.VECTOR2I(mm(x0), mm(y0)))
        s.SetEnd(pcbnew.VECTOR2I(mm(x1), mm(y1)))
        s.SetLayer(pcbnew.Edge_Cuts)
        s.SetWidth(mm(0.1))
        b.Add(s)
    for ref, value, name, lcsc, x, pins, model in (("R1", "10k", "R_0603", "C25804", 8, ("VIN", "OUT"), MODEL),
                                                   ("R2", "10k", "R_0603", "C25804", 16, ("OUT", "GND"), MODEL),
                                                   ("C1", "100n", "C_0603", "C14663", 24, ("VIN", "GND"), MISSING)):
        fp = pcbnew.FOOTPRINT(b)
        fp.SetFPID(pcbnew.LIB_ID("demo", name))
        fp.SetReference(ref)
        fp.SetValue(value)
        fp.SetField("LCSC", lcsc)
        fp.SetAttributes(pcbnew.FP_SMD)
        fp.SetPosition(pcbnew.VECTOR2I(mm(x), mm(10)))
        for i, net in enumerate(pins):
            p = pcbnew.PAD(fp)
            p.SetNumber(str(i + 1))
            p.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
            p.SetLayerSet(pcbnew.PAD.SMDMask())
            p.SetShape(pcbnew.PAD_SHAPE_RECT)
            p.SetSize(pcbnew.VECTOR2I(mm(0.8), mm(0.9)))
            fp.Add(p)
            p.SetFPRelativePosition(pcbnew.VECTOR2I(mm(-0.8 if i == 0 else 0.8), 0))
            p.SetNet(nets[net])
        m = pcbnew.FP_3DMODEL()
        m.m_Filename = model
        fp.Add3DModel(m)
        b.Add(fp)
    t = pcbnew.PCB_TRACK(b)
    t.SetStart(pcbnew.VECTOR2I(mm(8.8), mm(10)))
    t.SetEnd(pcbnew.VECTOR2I(mm(15.2), mm(10)))
    t.SetWidth(mm(0.25))
    t.SetLayer(pcbnew.F_Cu)
    t.SetNet(nets["OUT"])
    b.Add(t)
    kicad.save_board(b, path)


def make_project(root):
    lib = os.path.join(root, "lib")
    os.makedirs(os.path.join(lib, "demo.pretty"))
    os.makedirs(os.path.join(lib, "3dmodels"))
    write(os.path.join(lib, "3dmodels", "r0603.step"), "ISO-10303-21;\nEND-ISO-10303-21;\n")
    write(os.path.join(lib, "demo.kicad_sym"), '(kicad_symbol_lib (version 20241209) (generator "test"))\n')
    with open(os.path.join(root, "pcbkit.toml"), "w") as fh:
        fh.write('[boards]\nfolders = ["boards/*"]\n\n[release]\nfab = ["jlcpcb"]\npreviews = false\n\n'
                 '[[release.profile]]\nmatch = "*"\nchecks = []\n\n[production]\nenabled = false\n')
    d = os.path.join(root, "boards", "demo")
    os.makedirs(os.path.join(d, "sheets"))
    draw_board(os.path.join(d, "demo.kicad_pcb"))
    write(os.path.join(d, "demo.kicad_pro"), json.dumps(
        {"meta": {"filename": "demo.kicad_pro", "version": 3},
         "net_settings": {"classes": [{"name": "Default", "track_width": 0.25, "clearance": 0.2}]}}))
    write(os.path.join(d, "demo.kicad_dru"), "(version 1)\n")
    write(os.path.join(d, "demo.kicad_sch"), SCH_ROOT)
    write(os.path.join(d, "sheets", "power.kicad_sch"), SCH_POWER)
    write(os.path.join(d, "fp-lib-table"), 
        '(fp_lib_table\n  (version 7)\n  (lib (name "demo")(type "KiCad")(uri "${KIPRJMOD}/../../lib/demo.pretty")'
        '(options "")(descr ""))\n)\n')
    write(os.path.join(d, "sym-lib-table"), 
        '(sym_lib_table\n  (version 7)\n  (lib (name "demo")(type "KiCad")(uri "${KIPRJMOD}/../../lib/demo.kicad_sym")'
        '(options "")(descr ""))\n)\n')
    # what the copy leaves out: window state, KiCad's lock files, its Local History and backups
    write(os.path.join(d, "demo.kicad_prl"), "{}\n")
    write(os.path.join(d, "~demo.kicad_sch.lck"), "{}\n")
    os.makedirs(os.path.join(d, ".history"))
    write(os.path.join(d, ".history", "demo.kicad_pcb"), "old\n")
    os.makedirs(os.path.join(d, "demo-backups"))
    return d


def release(root, *args):
    """Run pcbkit release in the project; its output."""
    from pcbkit.release import release as R
    cwd, out = os.getcwd(), io.StringIO()
    os.chdir(root)
    try:
        with contextlib.redirect_stdout(out):
            R.main(list(args))
    finally:
        os.chdir(cwd)
    return out.getvalue()


def edit(root, change):
    """Load the fixture board, change it in pcbnew, save it."""
    import pcbnew
    path = os.path.join(root, "boards", "demo", "demo.kicad_pcb")
    b = pcbnew.LoadBoard(path)
    change(b, pcbnew)
    kicad.save_board(b, path)


def value(ref, v):
    return lambda b, pcbnew: b.FindFootprintByReference(ref).SetValue(v)


def mpn(ref, v):
    """A hidden MPN field, which JLC's files don't carry."""
    def change(b, pcbnew):
        fp = b.FindFootprintByReference(ref)
        fp.SetField("MPN", v)
        next(f for f in fp.GetFields() if f.GetName() == "MPN").SetVisible(False)
    return change


def states(root):
    """What pcbkit production makes of each board: {key: state}, and its problems."""
    from pcbkit import production
    from pcbkit.config import Project
    cwd, problems = os.getcwd(), []
    os.chdir(root)
    try:
        pcbs = production.survey(Project(), problems, [])
    finally:
        os.chdir(cwd)
    return {k: p["state"] for k, p in pcbs.items()}, problems


def move_track(b, pcbnew):
    for t in b.GetTracks():
        t.Move(pcbnew.VECTOR2I(0, pcbnew.FromMM(0.1)))


def comment(b, pcbnew):
    """A note on Cmts.User, which no fab file has."""
    t = pcbnew.PCB_TEXT(b)
    t.SetText("a note")
    t.SetLayer(pcbnew.Cmts_User)
    t.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(5), pcbnew.FromMM(5)))
    b.Add(t)


def entries(root):
    return V.releases(os.path.join(root, "releases", "demo"))


@unittest.skipUnless(kicad.have_pcbnew(), "needs KiCad's Python")
class Release(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.base = os.path.join(cls.tmp.name, "base")
        make_project(cls.base)
        cls.first = release(cls.base)
        os.remove(os.path.join(cls.base, "boards", "demo", "~demo.kicad_sch.lck"))    # KiCad closed: stamping may go on

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def project(self, name):
        root = os.path.join(self.tmp.name, name)
        shutil.copytree(self.base, root, symlinks=True)
        return root

    def test_first_release_adopts_the_title_block_and_records_the_circuit(self):
        self.assertRegex(self.first, r"released v1\.0 \(1\.0\.0\): first release, its title block's v1\.0")
        (e,) = entries(self.base)
        c = V.circuit(os.path.join(self.base, "boards", "demo", "demo.kicad_pcb"))
        self.assertEqual(e["circuit"], V.circuit_fingerprint(c))
        self.assertEqual([r[:4] for r in c["parts"]], [["C1", "C_0603", "100n", "C14663"],
                                                        ["R1", "R_0603", "10k", "C25804"],
                                                        ["R2", "R_0603", "10k", "C25804"]])
        self.assertEqual(c["nets"]["OUT"], [["R1", "2"], ["R2", "1"]])

    def test_the_release_folder_holds_a_kicad_copy_that_opens(self):
        import pcbnew
        vdir = os.path.join(self.base, "releases", "demo", "v1.0")
        k = os.path.join(vdir, "kicad")
        found = sorted(os.path.relpath(os.path.join(d, f), k) for d, _, fs in os.walk(k) for f in fs)
        self.assertEqual(found, ["demo.kicad_dru", "demo.kicad_pcb", "demo.kicad_pro", "demo.kicad_sch",
                                 "fp-lib-table", os.path.join("sheets", "power.kicad_sch"), "sym-lib-table"])
        manifest = read(vdir, "MANIFEST.txt")
        self.assertIn("  kicad/sheets/power.kicad_sch\n", manifest)
        self.assertIn("netlist/BOM fingerprint: " + entries(self.base)[0]["circuit"], manifest)
        # the paths that reached ../../lib/ reach it from four levels down, and the one that never resolved is named
        pcb = read(k, "demo.kicad_pcb")
        self.assertIn('(model "${KIPRJMOD}/../../../../lib/3dmodels/r0603.step"', pcb)
        self.assertIn("${KIPRJMOD}/../../../../lib/demo.pretty", read(k, "fp-lib-table"))
        self.assertIn("${KIPRJMOD}/../../../../lib/demo.kicad_sym", read(k, "sym-lib-table"))
        missing = "${KIPRJMOD}/../../../../boards/demo/models/missing.step"
        self.assertEqual(freeze.unresolved(k), [("demo.kicad_pcb", missing, "not in the project either")])
        self.assertIn(f"demo.kicad_pcb: {missing} (not in the project either)", manifest)
        self.assertIn("kicad/ names 1 path it doesn't find (MANIFEST.txt)", self.first)
        # it opens in pcbnew as the board released, and kicad-cli draws its schematic, both sheets
        b = pcbnew.LoadBoard(os.path.join(k, "demo.kicad_pcb"))
        self.assertEqual(sorted(fp.GetReference() for fp in b.GetFootprints()), ["C1", "R1", "R2"])
        self.assertEqual(b.GetTitleBlock().GetRevision(), "v1.0")
        self.assertEqual(V.circuit(os.path.join(k, "demo.kicad_pcb")),
                         V.circuit(os.path.join(self.base, "boards", "demo", "demo.kicad_pcb")))
        pdf = os.path.join(self.tmp.name, "copy.pdf")
        r = subprocess.run([kicad.KICAD_CLI, "sch", "export", "pdf", "-o", pdf, os.path.join(k, "demo.kicad_sch")],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(pdf, "rb") as fh:
            data = fh.read()
        self.assertTrue(data.startswith(b"%PDF"))
        self.assertEqual(len(re.findall(rb"/Type\s*/Page[^s]", data)), 2)

    def test_a_board_indented_with_spaces_is_the_same_circuit(self):
        board = os.path.join(self.base, "boards", "demo", "demo.kicad_pcb")
        spaced = os.path.join(self.tmp.name, "spaced.kicad_pcb")
        write(spaced, re.sub(r"(?m)^\t+", lambda m: "  " * len(m.group(0)), read(board)))
        self.assertNotIn("\t", read(spaced))
        self.assertEqual(len(V.circuit(spaced)["parts"]), 3)
        self.assertEqual(V.circuit(spaced), V.circuit(board))

    def test_the_fab_fingerprint_and_the_release_comparison_ignore_the_copy(self):
        vdir = os.path.join(self.base, "releases", "demo", "v1.0")
        bare = os.path.join(self.tmp.name, "bare")
        shutil.copytree(vdir, bare)
        shutil.rmtree(os.path.join(bare, "kicad"))
        self.assertEqual(V.fab_fingerprint(vdir), V.fab_fingerprint(bare))
        # a changed board, a stored fab fingerprint that no longer matches: release compares the files themselves with
        # v1.0's folder, kicad/ in it, and finds them the same
        root = self.project("compare")
        rdir = os.path.join(root, "releases", "demo")
        rs = entries(root)
        rs[-1]["fab"] = "0" * 40
        tomlw.dump({"key": "demo", "release": rs}, os.path.join(rdir, "releases.toml"))
        edit(root, comment)
        out = release(root, "--dry-run")
        self.assertIn("current v1.0 (1.0.0): changed files, same fab output (the same as v1.0's files)", out)

    def test_an_unchanged_board_is_not_released(self):
        root = self.project("unchanged")
        out = release(root)
        self.assertRegex(out, r"boards/demo/demo\.kicad_pcb +current v1\.0 \(1\.0\.0\)\n")
        self.assertEqual(sorted(os.listdir(os.path.join(root, "releases", "demo"))), ["releases.toml", "v1.0"])

    def test_a_value_change_bumps_the_minor_version(self):
        root = self.project("value")
        edit(root, value("R1", "4.7k"))
        out = release(root, "--dry-run")
        self.assertIn("would release v1.1 (1.1.0): minor after 1.0.0: R1 value 10k -> 4.7k", out)
        self.assertIn("would release v1.0.1 (1.0.1): patch after 1.0.0 (--patch): R1 value 10k -> 4.7k",
                      release(root, "--dry-run", "--patch"))
        out = release(root)
        self.assertIn("released v1.1 (1.1.0): minor after 1.0.0: R1 value 10k -> 4.7k", out)
        old, new = entries(root)
        self.assertEqual(new["note"], "minor after 1.0.0: R1 value 10k -> 4.7k")
        self.assertNotEqual(old["circuit"], new["circuit"])
        # the two versions open side by side, each as it was released
        import pcbnew
        boards = [pcbnew.LoadBoard(os.path.join(root, "releases", "demo", v, "kicad", "demo.kicad_pcb"))
                  for v in ("v1.0", "v1.1")]
        self.assertEqual([b.FindFootprintByReference("R1").GetValue() for b in boards], ["10k", "4.7k"])
        self.assertEqual([b.GetTitleBlock().GetRevision() for b in boards], ["v1.0", "v1.1"])

    def test_a_moved_track_bumps_the_patch(self):
        root = self.project("track")
        edit(root, move_track)
        out = release(root, "--dry-run")
        self.assertIn("would release v1.0.1 (1.0.1): patch after 1.0.0: same netlist and BOM, new fab output", out)
        self.assertIn("would release v1.1 (1.1.0): minor after 1.0.0 (--minor): same netlist and BOM",
                      release(root, "--dry-run", "--minor"))

    def test_a_release_without_a_circuit_fingerprint(self):
        root = self.project("old")
        rdir = os.path.join(root, "releases", "demo")
        rs = entries(root)
        fp = rs[-1].pop("circuit")
        tomlw.dump({"key": "demo", "release": rs}, os.path.join(rdir, "releases.toml"))
        # a changed circuit can't be told: a patch, and why
        edit(root, value("R1", "4.7k"))
        self.assertIn("would release v1.0.1 (1.0.1): patch after 1.0.0: v1.0 has no netlist/BOM fingerprint",
                      release(root, "--dry-run"))
        # unchanged since that release, it gets the release's recorded
        board = os.path.join("boards", "demo", "demo.kicad_pcb")
        shutil.copy(os.path.join(self.base, board), os.path.join(root, board))
        self.assertIn("current v1.0 (1.0.0): would record v1.0's netlist/BOM fingerprint", release(root, "--dry-run"))
        self.assertIn("current v1.0 (1.0.0): recorded v1.0's netlist/BOM fingerprint", release(root))
        self.assertEqual(entries(root)[-1]["circuit"], fp)

    def test_an_equivalent_recorded_before_the_fingerprint_is_checked_against_it(self):
        root = self.project("equivalent")
        rdir = os.path.join(root, "releases", "demo")
        rs = entries(root)
        rs[-1].pop("circuit")
        tomlw.dump({"key": "demo", "release": rs}, os.path.join(rdir, "releases.toml"))
        board = os.path.join(root, "boards", "demo", "demo.kicad_pcb")
        with open(board, "rb") as fh:
            released = fh.read()
        # with no fingerprint to tell by, an MPN that no JLC file shows is the same fab output: an equivalent
        edit(root, mpn("R1", "RC0603FR-0710KL"))
        with open(board, "rb") as fh:
            with_mpn = fh.read()
        self.assertIn("same fab output: recorded as equivalent", release(root))
        # back to the release's own board, which records the fingerprint
        with open(board, "wb") as fh:
            fh.write(released)
        self.assertIn("recorded v1.0's netlist/BOM fingerprint", release(root))
        self.assertEqual(states(root)[0], {"demo": "released"})
        # the equivalent again: its circuit isn't the release's, so it is a change, a minor one
        with open(board, "wb") as fh:
            fh.write(with_mpn)
        self.assertIn("would release v1.1 (1.1.0): minor after 1.0.0: R1 MPN (none) -> RC0603FR-0710KL "
                      "(the fab output is the same)", release(root, "--dry-run"))
        state, problems = states(root)
        self.assertEqual(state, {"demo": "changed"})
        self.assertIn("boards/demo/demo.kicad_pcb: changed since v1.0: run pcbkit release", problems)


if __name__ == "__main__":
    unittest.main()
