"""`pcbkit import-easyeda`: the corrections it makes to what KiCad's EasyEDA importer leaves.

  python3 tests/test_import_easyeda.py     # re-runs itself under KiCad's Python when pcbnew is not importable

The text cases (symbol library merge, keep-outs, sheet names, Footprint fields, 3-D models) run under any Python;
the board cases (pad checks, loose pads, outline gaps, a model on a turned and flipped footprint) draw small boards in
pcbnew. Nothing goes to the network: the 3-D model cases fetch a small OBJ mesh from a function in this file.
"""
import gzip
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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


# A 3-D model as EasyEDA serves it: an OBJ mesh with its materials inline, a 2.0 x 0.5 x 0.4 mm box whose centre
# is 0.5 mm off its origin in x, with its bottom 0.1 mm up. The last face counts its corners from the end.
OBJ = """newmtl body
Kd 0.1 0.2 0.3
Ks 0.5 0.5 0.5
d 0
endmtl
newmtl pins
Kd 0.8 0.8 0.8
d 0.5
endmtl
v -0.5 -0.25 0.1
v 1.5 -0.25 0.1
v 1.5 0.25 0.1
v -0.5 0.25 0.1
v -0.5 -0.25 0.5
v 1.5 -0.25 0.5
v 1.5 0.25 0.5
v -0.5 0.25 0.5
usemtl body
f 1/1/1 2/2/1 3/3/1 4/4/1
f 5 6 7 8
usemtl pins
f 1 2 6 5
f -5 -6 -2 -1
"""
BOX = ((-0.5, 1.5), (-0.25, 0.25), (0.1, 0.5))
MESH = ("BOX-SMD.wrl", BOX, 1.0)  # as fetch_models gives it: file name, box, the scale the file has
UUID = "0123456789abcdef0123456789abcdef"
LIB_PADS = {"1": (-1.0, 0.0), "2": (1.0, 0.0), "3": (0.0, 0.5)}  # the footprint's own: not in a line, a mirror shows


def rect(cx, cy, w, h):
    return [(cx - w / 2, cy - h / 2), (cx + w / 2, cy - h / 2), (cx + w / 2, cy + h / 2), (cx - w / 2, cy + h / 2)]


def placed_part(back=False, outline=(2.0, 0.5), z=-1.0, size=(2.0, 0.5)):
    """The part as EasyEDA's board has it (mm, y down): at (50, 20), with the outline of its model centred 0.2 mm
    right of and 0.1 mm below the pads' middle, as EasyEDA's outlines often are; on the bottom, seen mirrored."""
    m = -1 if back else 1
    pads = {k: (50 + m * x, 20 + y) for k, (x, y) in LIB_PADS.items()}
    return pads, back, dict(title="BOX-SMD", uuid=UUID, z=z, size=size, outline=rect(50 + m * 0.2, 20.1, *outline))


def board_text(*footprints):
    """A .kicad_pcb's text with footprints (reference, model path, rotation) as KiCad 10 saves them."""
    out = ['(kicad_pcb\n\t(version 20250114)\n\t(generator "pcbnew")']
    for ref, model, rot in footprints:
        out.append(f'\t(footprint "lib:{ref}"\n\t\t(layer "F.Cu")\n\t\t(property "Reference" "{ref}"\n'
                   f'\t\t\t(at 0 0 0)\n\t\t)\n\t\t(pad "1" smd rect\n\t\t\t(at -1 0)\n\t\t)\n'
                   f'\t\t(model "{model}"\n\t\t\t(offset\n'
                   f'\t\t\t\t(xyz 0.01 -0.2 0)\n\t\t\t)\n\t\t\t(scale\n\t\t\t\t(xyz 1 1 1)\n\t\t\t)\n\t\t\t(rotate\n'
                   f'\t\t\t\t(xyz -90 0 {rot})\n\t\t\t)\n\t\t)\n\t)')
    return "\n".join(out) + "\n)\n"


class Models3D(unittest.TestCase):
    """KiCad's importer points each model at ${KIPRJMOD}/EASYEDA_MODELS/<title>.step and writes no file there; the
    import fetches EasyEDA's OBJ mesh instead, converts it to VRML and places it by EasyEDA's outline."""

    def setUp(self):
        guard = mock.patch("urllib.request.urlopen", side_effect=AssertionError("the tests don't use the network"))
        guard.start()
        self.addCleanup(guard.stop)
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir))

    def test_the_export_names_each_parts_model_by_reference(self):
        U = 0.254
        node = {"gId": "gge9", "nodeName": "g", "nodeType": 1, "layerid": "19",
                "attrs": {"c_width": "7.874", "c_height": "1.9685", "c_rotation": "0,0,90", "z": "-3.937",
                          "c_origin": "200,80", "uuid": UUID, "c_etype": "outline3D", "title": "BOX-SMD",
                          "layerid": "19", "transform": "scale(1) translate(0, 0)", "id": "gge9"},
                "childNodes": [{"gId": "gge10", "nodeName": "polyline", "nodeType": 1,
                                "attrs": {"fill": "none", "points": "197 79.4 205 79.4 205 81.4 197 81.4 197 79.4"}}]}
        lib = ("LIB~200~80~package`BOX`3DModel`BOX-SMD`~0~~gge0~2~u~0~0~~yes~~#@$"
               "TEXT~P~195~70~0.6~0~0~3~~4.5~U1~M 1 1#@$"
               "PAD~RECT~196~80~3~3~1~GND~1~0~~0~gge1#@$PAD~RECT~204~80~3~3~1~VCC~2~0~~0~gge2#@$"
               "SVGNODE~" + json.dumps(node))
        bare = "LIB~0~0~package`TP`#@$TEXT~P~0~0~0.6~0~0~3~~4.5~TP1~M 1 1#@$PAD~ELLIPSE~0~0~4~4~2~~1~0~~0~gge5"
        path = self.dir / "pcb.json"
        path.write_text(json.dumps({"shape": [lib, bare]}))
        got = ie.easyeda_models(path)
        self.assertEqual(sorted(got), ["U1"])  # TP1 has no model
        pads, back, model = got["U1"]
        self.assertTrue(back)
        self.assertEqual(sorted(pads), ["1", "2"])
        self.assertAlmostEqual(pads["2"][0], 204 * U)
        self.assertEqual((model["title"], model["uuid"]), ("BOX-SMD", UUID))
        for a, b in zip((model["z"], *model["size"]), (-1.0, 2.0, 0.5)):
            self.assertAlmostEqual(a, b, places=4)
        self.assertEqual(len(model["outline"]), 5)
        self.assertAlmostEqual(model["outline"][1][0], 205 * U)

    def test_an_outline_drawn_in_mm_is_read_through_its_transform(self):
        # as siebenuhr's export has its tantalum: points in mm, scaled to EasyEDA units about the part's origin
        U = 0.254
        node = {"nodeName": "g", "attrs": {"c_width": "28.7402", "c_height": "16.9291", "z": "0", "uuid": UUID,
                                           "c_etype": "outline3D", "title": "CASE-E",
                                           "transform": "scale(3.937) translate(-2999.5147, -2584.933)"},
                "childNodes": [{"nodeName": "polyline", "attrs": {
                    "points": "4024.45 3462.91 4024.45 3467.21 4017.15 3467.21 4017.15 3462.91"}}]}
        lib = ("LIB~4020.8~3465.06~package`CASE-E`~0~~gge0~1~#@$TEXT~P~0~0~0.6~0~0~3~~4.5~C7~M 1 1#@$"
               "SVGNODE~" + json.dumps(node))
        path = self.dir / "pcb.json"
        path.write_text(json.dumps({"shape": [lib]}))
        xs, ys = zip(*ie.easyeda_models(path)["C7"][2]["outline"])
        self.assertAlmostEqual(max(xs) - min(xs), 7.3, places=3)  # the model's 7.3 x 4.3 mm, not a quarter of it
        self.assertAlmostEqual(max(ys) - min(ys), 4.3, places=3)
        self.assertAlmostEqual((max(xs) + min(xs)) / 2, 4020.8 * U, places=2)
        f = ie.svg_transform("translate(5, 6) rotate(90)")
        self.assertEqual([round(v, 9) + 0.0 for v in f], [0, 1, -1, 0, 5, 6])  # (1, 0) -> (5, 7)
        self.assertEqual(ie.svg_transform("matrix(2 0 0 2 1 1) scale(0.5)"), (1, 0, 0, 1, 1, 1))
        with self.assertRaises(ValueError):
            ie.svg_transform("skewX(10)")

    def test_a_path_closed_with_z_is_read_and_one_with_curves_or_relative_moves_is_not(self):
        self.assertEqual(ie._svg_points({"attrs": {"d": "M 0 0 L 2 0 L 2 1 L 0 1 z"}}),
                         [(0, 0), (2, 0), (2, 1), (0, 1)])
        for d in ("M 0 0 l 2 0 l 0 1 z", "M 0 0 C 1 1 2 2 3 3 Z"):  # relative: not where they say; curves
            with self.assertRaises(ValueError):
                ie._svg_points({"attrs": {"d": d}})
        # nor one such path beside a readable one: the rest isn't the whole outline
        with self.assertRaises(ValueError):
            ie._svg_points({"attrs": {}, "childNodes": [{"attrs": {"points": "0 0 2 0 2 1"}},
                                                        {"attrs": {"d": "M 2 1 A 1 1 0 0 1 4 1"}}]})

    def test_a_mesh_becomes_vrml_with_its_box_and_materials(self):
        wrl = ie.obj_to_wrl(OBJ, "BOX-SMD", UUID, (2.0, 0.5))
        self.assertTrue(wrl.startswith("#VRML V2.0 utf8\n"))
        self.assertEqual(ie.wrl_header(wrl), (UUID, BOX, 1.0))
        self.assertEqual(wrl.count("Shape {"), 2)  # one per material
        self.assertIn("diffuseColor 0.1 0.2 0.3", wrl)
        self.assertIn("transparency 0.5", wrl)
        self.assertIn("point [ -0.19685 -0.098425 0.03937, 0.59055", wrl)  # KiCad's VRML unit is 0.1 in
        self.assertIn("coordIndex [ 0,1,5,4,-1, 3,2,6,7,-1 ]", wrl)  # the second face counted from the end
        self.assertNotIn("# scaled", wrl)

    def test_a_vertex_no_face_uses_is_not_part_of_the_box(self):
        stray = OBJ.replace("usemtl body", "v 40 -30 9\nusemtl body")  # before the faces, so -5 etc. still count back
        stray = stray.replace("f -5 -6 -2 -1", "f -6 -7 -3 -2")
        wrl = ie.obj_to_wrl(stray, "BOX-SMD", UUID, (2.0, 0.5))
        self.assertEqual(ie.wrl_header(wrl), (UUID, BOX, 1.0))  # not scaled to it, nor boxed round it
        self.assertEqual(wrl.split("\n", 3)[3], ie.obj_to_wrl(OBJ, "BOX-SMD", UUID, (2.0, 0.5)).split("\n", 3)[3])

    def test_a_mesh_of_another_size_than_the_exports_is_scaled_to_it(self):
        inch = ie.obj_to_wrl(OBJ, "HDR", UUID, (2.0 * 2.54, 0.5 * 2.54))  # drawn in 0.1 in
        self.assertEqual(ie.wrl_header(inch)[1:], (tuple((a * 2.54, b * 2.54) for a, b in BOX), 2.54))
        self.assertIn("# scaled 2.54: the mesh was 0.50 x 2.00, the export says 1.27 x 5.08", inch)
        near = ie.obj_to_wrl(OBJ, "BOX-SMD", UUID, (2.03, 0.508))  # within 2 %: the mesh as it is
        self.assertEqual(ie.wrl_header(near)[2], 1.0)
        other = ie.obj_to_wrl(OBJ, "BIG", UUID, (3.0, 0.75))
        self.assertEqual(ie.wrl_header(other)[2], 1.5)
        with self.assertRaises(ValueError):
            ie.obj_to_wrl("# nothing\n", "X", UUID, (1, 1))

    def test_models_are_fetched_once_converted_and_named_by_title(self):
        asked = []

        def get(uuid):
            asked.append(uuid)
            return gzip.compress(OBJ.encode())  # EasyEDA gzips some
        other = "fedcba9876543210fedcba9876543210"
        models = {UUID: ("BOX-SMD", (2.0, 0.5)), other: ("BOX-SMD", (2.0, 0.5))}  # two models, one title
        meshes, fetched, failed = ie.fetch_models(models, self.dir / "3dmodels", get)
        self.assertEqual((sorted(asked), fetched, failed), (sorted(models), ["BOX-SMD", "BOX-SMD"], {}))
        self.assertEqual(meshes[UUID], ("BOX-SMD.wrl", BOX, 1.0))
        self.assertEqual(meshes[other][0], "BOX-SMD_2.wrl")  # its id sorts after
        self.assertEqual(ie.wrl_header((self.dir / "3dmodels" / "BOX-SMD_2.wrl").read_text())[0], other)
        again = ie.fetch_models({other: ("BOX-SMD", (2.0, 0.5))}, self.dir / "3dmodels", get)
        self.assertEqual((again[0], again[1], len(asked)), ({other: meshes[other]}, [], 2))  # the file is kept
        offline = ie.fetch_models({**models, "11112222333344445555666677778888": ("NEW", (1, 1))},
                                  self.dir / "3dmodels", None)
        self.assertEqual((sorted(offline[0]), offline[1], offline[2]), (sorted(models), [], {
            "11112222333344445555666677778888": ("NEW", "not in 3dmodels and not fetched")}))

    def test_a_model_kept_under_a_later_name_is_found_offline(self):
        other = "fedcba9876543210fedcba9876543210"
        get = lambda uuid: (_ for _ in ()).throw(OSError("timed out")) if uuid == UUID else OBJ.encode()
        meshes, _, failed = ie.fetch_models({UUID: ("BOX-SMD", (2.0, 0.5)), other: ("BOX-SMD", (2.0, 0.5))},
                                            self.dir / "3dmodels", get)
        self.assertEqual((meshes[other][0], failed), ("BOX-SMD_2.wrl", {UUID: ("BOX-SMD", "timed out")}))
        again = ie.fetch_models({other: ("BOX-SMD", (2.0, 0.5))}, self.dir / "3dmodels", None)
        self.assertEqual(again, ({other: meshes[other]}, [], {}))
        # a new model of that title takes neither name, whichever case the file system keeps
        (self.dir / "3dmodels" / "box-smd.wrl").write_text("#VRML V2.0 utf8\n")
        third = ie.fetch_models({"11112222333344445555666677778888": ("BOX-SMD", (2.0, 0.5))},
                                self.dir / "3dmodels", lambda uuid: OBJ.encode())
        self.assertEqual(third[0]["11112222333344445555666677778888"][0], "BOX-SMD_3.wrl")

    def test_cached_files_any_case_of_extension_and_damaged_headers(self):
        models = self.dir / "3dmodels"
        models.mkdir()
        good = ie.obj_to_wrl(OBJ, "BOX-SMD", UUID, (2.0, 0.5))
        (models / "BOX-SMD.WRL").write_text(good)
        (models / "OTHER.WRL").write_text("#VRML V2.0 utf8\n")  # not ours: its name stays taken, in any case
        cut = "fedcba9876543210fedcba9876543210"
        (models / "CUT.wrl").write_text(f"#VRML V2.0 utf8\n# CUT: EasyEDA model {cut}, converted\n"
                                        "# box mm x -0.5 1.5 y\n")  # a write cut short
        self.assertIsNone(ie.wrl_header((models / "CUT.wrl").read_text()))
        new = "11112222333344445555666677778888"
        wanted = {UUID: ("BOX-SMD", (2.0, 0.5)), cut: ("CUT", (2.0, 0.5)), new: ("OTHER", (2.0, 0.5))}
        meshes, _, failed = ie.fetch_models(wanted, models, None)
        self.assertEqual((meshes, sorted(failed)), ({UUID: ("BOX-SMD.WRL", BOX, 1.0)}, sorted([cut, new])))
        meshes, fetched, failed = ie.fetch_models(wanted, models, lambda uuid: OBJ.encode())
        self.assertEqual((meshes[cut][0], meshes[new][0], failed), ("CUT_2.wrl", "OTHER_2.wrl", {}))
        self.assertEqual((models / "OTHER.WRL").read_text(), "#VRML V2.0 utf8\n")
        self.assertEqual(sorted(p.name for p in models.iterdir() if p.name.endswith(".part")), [])

    def test_a_model_that_cant_be_fetched_is_listed_and_the_rest_go_on(self):
        def get(uuid):
            if uuid == UUID:
                raise OSError("HTTP Error 404: Not Found")
            return b"<html>not a mesh</html>"
        page = "fedcba9876543210fedcba9876543210"
        meshes, fetched, failed = ie.fetch_models(  # two of them with one title, each failing its own way
            {UUID: ("BOX-SMD", (1, 1)), page: ("BOX-SMD", (1, 1)), "../../etc": ("ODD", (1, 1))},
            self.dir / "3dmodels", get)
        self.assertEqual((meshes, fetched), ({}, []))
        self.assertEqual(failed[UUID], ("BOX-SMD", "HTTP Error 404: Not Found"))
        self.assertEqual(failed[page], ("BOX-SMD", "no mesh in the OBJ"))
        self.assertIn("not EasyEDA's", failed["../../etc"][1])  # never put in a URL
        self.assertFalse((self.dir / "3dmodels").exists())
        # the report gives each footprint its own model's reason
        lines = ie.model_lines(dict(fetch=True, missing=failed, waiting=[("D1", UUID), ("D2", page), ("D3", UUID)]),
                               "lib")
        self.assertEqual(lines, ["3-D models not fetched; their footprints keep KiCad's ${KIPRJMOD}/EASYEDA_MODELS "
                                 "path, which has no file: BOX-SMD (D1, D3): HTTP Error 404: Not Found; BOX-SMD (D2): "
                                 "no mesh in the OBJ"])
        offline = ie.model_lines(dict(fetch=False, missing=failed, waiting=[("D1", UUID), ("D2", page)]), "lib")
        self.assertEqual(offline, ["3-D models not in the library and not fetched (--no-fetch); their footprints keep "
                                   "KiCad's ${KIPRJMOD}/EASYEDA_MODELS path, which has no file: BOX-SMD (D1, D2)"])

    def test_an_outline_that_cant_be_read_is_said_by_reference_and_title(self):
        node = {"nodeName": "g", "attrs": {"c_width": "8", "c_height": "2", "z": "0", "uuid": UUID,
                                           "c_etype": "outline3D", "title": "ROUND"},
                "childNodes": [{"nodeName": "polyline", "attrs": {"points": "0 0 2 0"}},
                               {"nodeName": "path", "attrs": {"d": "M 0 0 A 1 1 0 0 1 2 0 Z"}}]}
        lib = ("LIB~0~0~package`ROUND`~0~~gge0~1~#@$TEXT~P~0~0~0.6~0~0~3~~4.5~X1~M 1 1#@$"
               "SVGNODE~" + json.dumps(node))
        path = self.dir / "pcb.json"
        path.write_text(json.dumps({"shape": [lib]}))
        ie.NOTES.clear()
        self.addCleanup(ie.NOTES.clear)
        self.assertEqual(ie.easyeda_models(path), {})
        self.assertEqual(list(ie.NOTES), ["X1: the outline of 3-D model ROUND not read (a path drawn with curves or "
                                          "relative moves); the model stays at KiCad's path"])

    def assertPlaced(self, got, offset, rot, scale=1.0, notes=(), turn_xy=(0, 0)):
        self.assertIsNotNone(got)
        for a, b in zip(got[0], offset):
            self.assertAlmostEqual(a, b, places=4)
        self.assertEqual(got[1], (*turn_xy, rot))
        self.assertAlmostEqual(got[2], scale, places=6)
        self.assertEqual(got[3], list(notes))

    def test_a_model_is_centred_on_easyedas_outline_with_its_bottom_at_easyedas_height(self):
        # the outline's centre is 0.2 mm right of the pads' and 0.1 mm down (y up: -0.1); the box's is 0.5 mm right of
        # the mesh's origin; its bottom 0.1 mm up goes to z -1.0
        self.assertPlaced(ie.model_placement(LIB_PADS, placed_part(), MESH, (0, 0, 0)), (-0.3, -0.1, -1.1), 0.0)
        self.assertPlaced(ie.model_placement(LIB_PADS, placed_part(), MESH, (0, 0, -360)), (-0.3, -0.1, -1.1), 0.0)
        # on the bottom of EasyEDA's board the part is seen mirrored: the same place on the footprint
        self.assertPlaced(ie.model_placement(LIB_PADS, placed_part(back=True), MESH, (0, 0, 0)),
                          (-0.3, -0.1, -1.1), 0.0)

    def test_a_turned_model_is_centred_after_the_turn(self):
        # the footprint's pads along y, and KiCad's importer turning the mesh by -90 to match: KiCad turns a model by
        # -rotation about z, then shifts it
        along_y = {k: (y, -x) for k, (x, y) in LIB_PADS.items()}
        self.assertPlaced(ie.model_placement(along_y, placed_part(), MESH, (0, 0, -90)), (0.1, -0.3, -1.1), -90.0)

    def test_a_model_turned_about_x_or_y_is_placed_by_its_turned_box(self):
        # x 90: the box's depth stands up, KiCad turning it by -90 about x: y from 0.1 to 0.5, z from -0.25 to 0.25
        self.assertPlaced(ie.model_placement(LIB_PADS, placed_part(outline=(2.0, 0.4)), MESH, (90, 0, 0)),
                          (-0.3, -0.4, -0.75), 0.0, turn_xy=(90, 0))
        # y 270, as EasyEDA's 14-segment displays have it: x from 0.1 to 0.5, z from -1.5 to 0.5
        self.assertPlaced(ie.model_placement(LIB_PADS, placed_part(outline=(0.4, 0.5)), MESH, (0, 270, 0)),
                          (-0.1, -0.1, 0.5), 0.0, turn_xy=(0, 270))
        self.assertEqual([round(c, 9) + 0.0 for c in ie.turned((1, 2, 3), (90, 0, 0))], [1, 3, -2])  # y' = z, z' = -y

    def test_a_model_is_scaled_for_the_size_this_export_gives_it(self):
        scaled = ["mesh scaled 2.54 to the export's size"]
        inch = dict(outline=(2.0 * 2.54, 0.5 * 2.54), size=(2.0 * 2.54, 0.5 * 2.54))
        # the file as converted (scale 1), another export saying the model is 2.54 times its size: the footprint
        # scales it, and the box is placed as scaled
        self.assertPlaced(ie.model_placement(LIB_PADS, placed_part(**inch), MESH, (0, 0, 0)),
                          (-1.07, -0.1, -1.254), 0.0, 2.54, scaled)
        # the file converted at 2.54: nothing more to scale, or back to 1 for an export at the mesh's own size
        big = ("HDR.wrl", tuple((a * 2.54, b * 2.54) for a, b in BOX), 2.54)
        self.assertPlaced(ie.model_placement(LIB_PADS, placed_part(**inch), big, (0, 0, 0)),
                          (-1.07, -0.1, -1.254), 0.0, 1.0, scaled)
        self.assertPlaced(ie.model_placement(LIB_PADS, placed_part(), big, (0, 0, 0)), (-0.3, -0.1, -1.1), 0.0,
                          1 / 2.54)

    def test_where_the_outline_and_the_model_differ_it_says_so(self):
        got = ie.model_placement(LIB_PADS, placed_part(outline=(3.0, 0.5)), MESH, (0, 0, 0))
        self.assertEqual(got[3], ["EasyEDA's outline is 3.00 x 0.50 mm, the model 2.00 x 0.50"])
        wider = {k: (2 * x, y) for k, (x, y) in LIB_PADS.items()}  # another land pattern
        self.assertIsNone(ie.model_placement(wider, placed_part(), MESH, (0, 0, 0)))

    def test_kicads_model_path_is_repointed_on_the_text(self):
        path = self.dir / "b.kicad_pcb"
        path.write_text(board_text(("U1", "${KIPRJMOD}/EASYEDA_MODELS/BOX-SMD.step", -90),
                                   ("U2", "${KIPRJMOD}/EASYEDA_MODELS/BOX-SMD.step", 0),
                                   ("C1", "${KICAD10_3DMODEL_DIR}/C.3dshapes/C.step", 0)))
        before = path.read_text()
        new = "${KIPRJMOD}/../../lib/3dmodels/BOX-SMD.wrl"
        plans = {"U1": (new, (0.1, -0.3, -1.1), (-90, 0, -90), 2.54), "C1": (new, (1, 1, 1), (0, 0, 0), 1.0)}
        self.assertEqual(ie.repoint_models(path, plans), ["U1"])  # C1's model is KiCad's own, U2 has no plan
        after = path.read_text()
        u1 = after[after.index('"lib:U1"'):after.index('"lib:U2"')]
        self.assertIn(f'(model "{new}"\n\t\t\t(offset\n\t\t\t\t(xyz 0.1 -0.3 -1.1)', u1)
        self.assertIn("(xyz 2.54 2.54 2.54)", u1)
        self.assertIn("(xyz -90 0 -90)", u1)
        self.assertEqual(after[after.index('"lib:U2"'):], before[before.index('"lib:U2"'):])
        self.assertEqual(ie.repoint_models(path, plans), [])  # done: nothing left to re-point

    def test_a_footprint_files_model_is_repointed(self):
        path = self.dir / "BOX.kicad_mod"
        # an earlier import's footprint whose model was tilted otherwise: it gets the board copy's model whole
        path.write_text('(footprint "BOX"\n\t(layer "F.Cu")\n\t(model "${KIPRJMOD}/EASYEDA_MODELS/BOX-SMD.step"\n'
                        "\t\t(scale\n\t\t\t(xyz 1 1 1)\n\t\t)\n\t\t(rotate\n\t\t\t(xyz 45 0 0)\n\t\t)\n\t)\n)\n")
        self.assertEqual(ie.repoint_models(path, {None: ("x.wrl", (0.25, 0, 0), (0, 0, 180), 1.0)}), [None])
        text = path.read_text()
        self.assertIn('(model "x.wrl"', text)
        self.assertIn("(offset (xyz 0.25 0 0))", text)  # added where the file had none
        self.assertIn("(xyz 0 0 180)", text)
        self.assertEqual(text.count("("), text.count(")"))

    def test_no_fetch_is_passed_on(self):
        got = {}
        with mock.patch.object(ie, "convert", lambda *a, **k: got.update(k)):
            ie.main(["s.json", "p.json", "out", "--name", "b", "--no-fetch"])
            self.assertFalse(got["fetch"])
            ie.main(["s.json", "p.json", "out", "--name", "b"])
            self.assertTrue(got["fetch"])


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

    def test_text_that_reads_like_a_pin_is_not_one(self):
        noisy = KICAD_SAVED_RES.replace('\t\t(symbol "RES_0_1"', '\t\t(property "Description" "wire it as (pin input '
                                        'line (at 1 2 0) (length 3))")\n\t\t(property "Note" "half a (pin output line '
                                        '(at 4 5 and an (extends \\"R\\")")\n\t\t(symbol "RES_0_1"', 1)
        new = ie.symbol_def("RES", part("R1", "RES", RES), "proj", "R0603")
        self.assertEqual(ie.symbol_pins(noisy), ie.symbol_pins(KICAD_SAVED_RES))
        self.assertTrue(ie.same_symbol(noisy, new))
        self.lib.write_text("(kicad_symbol_lib\n\t(version 20241209)\n\t" + noisy + "\n)\n")
        sym_of, stats = self.load(schematic(part("R1", "RES", RES)))
        self.assertEqual((sym_of, stats["kept"], stats["renamed"]), ({"R1": "RES"}, 1, set()))

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

    def test_a_library_saved_by_kicad_keeps_its_text(self):
        original = ('(kicad_symbol_lib\n\t(version 20241209)\n\t(generator "kicad_symbol_editor")\n'
                    '\t(generator_version "10.0")\n\t' + KICAD_SAVED_RES + "\n)\n")
        self.lib.write_text(original)
        self.load(schematic(part("R1", "RES", RES)))
        self.assertEqual(self.lib.read_text(), original)  # nothing new: not written at all
        self.load(schematic(part("R1", "RES", RES), part("D1", "LED", LED), part("U1", "TL", CHIP3)))
        text, lib = self.lib.read_text(), ie.read_symbol_lib(self.lib)
        self.assertEqual(list(lib), ["LED", "RES", "TL"])  # in name order, around the symbol that was there
        self.assertEqual(ie.block_end(text, 0), len(text.rstrip()))
        for name in ("LED", "TL"):
            text = text.replace("\t" + lib[name] + "\n", "", 1)
        self.assertEqual(text, original)  # and the rest is as it was

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

    def test_a_model_is_placed_in_the_footprints_own_frame_however_it_sits(self):
        pcbnew = self.pcbnew
        kicads = "${KIPRJMOD}/EASYEDA_MODELS/BOX-SMD.step"

        def model(fp, path=kicads):
            m = pcbnew.FP_3DMODEL()
            m.m_Filename = path
            fp.Add3DModel(m)
        fp = pcbnew.FOOTPRINT(self.board)
        fp.SetReference("U1")
        self.board.Add(fp)
        for num, (x, y) in sorted(LIB_PADS.items()):
            p = pcbnew.PAD(fp)
            p.SetNumber(num)
            p.SetAttribute(pcbnew.PAD_ATTRIB_SMD)
            p.SetLayerSet(pcbnew.PAD.SMDMask())
            p.SetSize(pcbnew.VECTOR2I(pcbnew.FromMM(0.4), pcbnew.FromMM(0.4)))
            p.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y)))
            fp.Add(p)
        model(fp)
        fp.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(30), pcbnew.FromMM(40)))
        fp.SetOrientationDegrees(30)
        fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_TOP_BOTTOM)  # on the bottom, as in EasyEDA
        own = ie.footprint_pads(pcbnew, fp, own=True)
        for k, (x, y) in LIB_PADS.items():
            self.assertAlmostEqual(own[k][0], x, places=5)
            self.assertAlmostEqual(own[k][1], y, places=5)
        stock = self.footprint("C1", [("1", "smd", None), ("2", "smd", None)])
        model(stock, "${KICAD10_3DMODEL_DIR}/C.3dshapes/C.step")
        other = self.footprint("U3", [("1", "smd", None), ("2", "smd", None), ("3", "smd", None)])
        model(other)  # another land pattern than the export's
        plans, notes, unfit, waiting = ie.model_plans(
            pcbnew, [fp, stock, other], {"U1": placed_part(back=True), "C1": placed_part(), "U3": placed_part()},
            {UUID: ("BOX-SMD.wrl", BOX, 1.0)}, "${KIPRJMOD}/../../lib/3dmodels/")
        self.assertEqual((sorted(plans), notes, unfit, waiting), (["U1"], [], [("U3", "BOX-SMD")], []))
        path, offset, rot, scale = plans["U1"]
        self.assertEqual((path, rot, scale), ("${KIPRJMOD}/../../lib/3dmodels/BOX-SMD.wrl", (0, 0, 0), 1.0))
        for a, b in zip(offset, (-0.3, -0.1, -1.1)):
            self.assertAlmostEqual(a, b, places=4)
        # no mesh for it: waiting, and only the footprints that have KiCad's path (not C1)
        self.assertEqual(ie.model_plans(pcbnew, [fp, stock], {"U1": placed_part(back=True), "C1": placed_part()}, {},
                                        ""), ({}, [], [], [("U1", UUID)]))

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

        def copper_on(*extra):  # pad 1 on the outer copper and the masks, and the extra (inner) layers
            def change(pcbnew, p):
                layers = pcbnew.LSET()
                for layer in (pcbnew.F_Cu, pcbnew.B_Cu, pcbnew.F_Mask, pcbnew.B_Mask) + extra:
                    layers.AddLayer(layer)
                p["1"].SetLayerSet(layers)
            return change
        self.assertFalse(variant(copper_on()))  # no inner copper is not copper on every layer
        self.assertFalse(variant(copper_on(pcbnew.In1_Cu)))
        self.assertTrue(ie.same_pads(sig, sig[::-1]))  # pads match in any order
        paste = lambda v: (lambda pcbnew, p: p["3"].SetLocalSolderPasteMargin(pcbnew.FromMM(v)))
        self.assertTrue(variant(paste(0)))  # an override of 0 is no override
        self.assertFalse(variant(paste(-0.1)))
        self.assertFalse(variant(lambda pcbnew, p: p["1"].SetLocalSolderMaskMargin(pcbnew.FromMM(0.1))))

        def back(w, h, shape=pcbnew.PAD_SHAPE_RECTANGLE, inner=(1.0, 0.8)):  # a front/inner/back padstack
            def change(pcbnew, p):
                p["1"].Padstack().SetMode(pcbnew.PADSTACK.MODE_FRONT_INNER_BACK)
                p["1"].SetShape(pcbnew.B_Cu, shape)
                p["1"].SetSize(pcbnew.B_Cu, mm(w, h))
                p["1"].SetShape(pcbnew.In1_Cu, pcbnew.PAD_SHAPE_OVAL)
                p["1"].SetSize(pcbnew.In1_Cu, mm(*inner))
            return change
        back(1.6, 1.0)(pcbnew, pads)
        sig = ie.footprint_signature(pcbnew, fp)
        for copper_layers in (2, 4):  # on four, a flip also cuts the through-hole pads' layers to the board's
            self.board.SetCopperLayerCount(copper_layers)
            for angle in (0, 33, 90):
                for flip in (False, True):
                    self.assertTrue(variant(angle=angle, flip=flip), (copper_layers, angle, flip))
        self.assertFalse(variant(back(2.0, 1.0)))
        self.assertFalse(variant(back(1.6, 1.0, pcbnew.PAD_SHAPE_OVAL)))
        self.assertFalse(variant(back(1.0, 1.6)))  # turned a quarter round
        self.assertFalse(variant(back(1.6, 1.0, inner=(1.2, 0.8))))  # other copper on the inner layers

        def bottom(pcbnew, p):
            layers = pcbnew.LSET()
            for layer in (pcbnew.B_Cu, pcbnew.B_Mask, pcbnew.B_Paste):
                layers.AddLayer(layer)
            p["3"].SetLayerSet(layers)
        self.assertFalse(variant(bottom))  # a pad on the far side of the footprint (an EasyEDA test point)
        self.assertFalse(variant(bottom, flip=True))

    def test_a_bottom_through_hole_part_on_four_layers_keeps_copper_on_every_layer(self):
        import json
        import subprocess
        pcbnew = self.pcbnew
        self.board.SetCopperLayerCount(4)
        fp = self.footprint("J1", [("1", "pth", None), ("2", "pth", None)])
        fp.SetFPID(pcbnew.LIB_ID("", "HDR"))
        fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_TOP_BOTTOM)  # KiCad cuts its pads' inner copper to In1, In2
        with tempfile.TemporaryDirectory() as d:
            pcb, pretty = os.path.join(d, "b.kicad_pcb"), os.path.join(d, "lib.pretty")
            kicad.save_board(self.board, pcb)  # headless: pcbnew.SaveBoard can abort without a wxApp
            run = ("import sys, json; from pcbkit.import_easyeda import export_footprints; "
                   "export_footprints(sys.argv[1], json.loads(sys.argv[2]), sys.argv[3])")
            subprocess.run([sys.executable, "-c", run, pcb, json.dumps([["J1", "HDR"]]), pretty],
                           check=True, capture_output=True, env=dict(os.environ, PYTHONPATH=ROOT))
            lib = pcbnew.FootprintLoad(pretty, "HDR")
        self.assertTrue(all(p.GetLayerSet().Contains(pcbnew.In30_Cu) for p in lib.Pads()))
        self.assertTrue(ie.same_pads(ie.footprint_signature(pcbnew, fp), ie.footprint_signature(pcbnew, lib)))

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
