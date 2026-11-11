"""JLC placement rotations: which row of data/jlc/corrections.csv a footprint gets, and its correction.

  python3 tests/test_jlc_rotations.py

No pcbnew needed: `correction` only reads a footprint's reference, value and footprint name. The cases are parts JLC's
placement preview showed out on 2026-11-08, and the rules that keep EasyEDA's footprints (already in JLC's
orientation) from being turned.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pcbkit.release.jlc_fab import correction, load_corrections  # noqa: E402


class Footprint:
    def __init__(self, ref, value, name):
        self.ref, self.value, self.name = ref, value, name

    def GetReference(self):
        return self.ref

    def GetValue(self):
        return self.value

    def GetFPID(self):
        name = self.name
        return type("FPID", (), {"GetLibItemName": lambda self: name})()


class Corrections(unittest.TestCase):
    table = load_corrections()

    def rule(self, name, lcsc="", ref="U1", value="part"):
        return correction(Footprint(ref, value, name), self.table, lcsc)

    def test_wson6_2x2_turns_a_quarter(self):
        r = self.rule("WSON-6-1EP_2x2mm_P0.65mm_EP1x1.6mm", "C49383415", ref="D1", value="TVS2200")
        self.assertEqual((r[0], r[1], r[4]), ("^WSON-6-1EP_2x2mm_P0.65mm", 270, "footprint"))

    def test_sot583_turns_half(self):
        r = self.rule("SOT-583-8", "C3200405", value="TPS62933DRLR")
        self.assertEqual((r[0], r[1]), ("^SOT-583-8$", 180))

    def test_easyeda_sot583_is_not_turned(self):
        self.assertIsNone(self.rule("SOT-583-8_L2.1-W1.2-P0.50-LS1.6-BL", "C3235557"))

    def test_polymer_cap_stands_in_for_its_package_rule(self):
        r = self.rule("CP_Elec_6.3x7.7", "C46550459", ref="C35", value="470uF 10V")
        self.assertEqual((r[0], r[1], r[4], r[5]), ("C46550459", 0, "LCSC", "^CP_Elec_6.3x7.7"))
        other = self.rule("CP_Elec_6.3x7.7", "C0", ref="C35", value="470uF 10V")
        self.assertEqual((other[0], other[1]), ("^CP_Elec_6.3x7.7", 180))

    def test_part_rule_needs_a_package_rule(self):
        # an LCSC row does nothing for a footprint no rule matches
        self.assertIsNone(self.rule("SOT-583-8_L2.1-W1.2-P0.50-LS1.6-BL", "C46550459"))

    def test_dates_are_iso_or_empty(self):
        for pattern, _, _, checked in self.table:
            with self.subTest(pattern=pattern):
                self.assertRegex(checked, r"^(\d{4}-\d{2}-\d{2})?$")


if __name__ == "__main__":
    unittest.main()
