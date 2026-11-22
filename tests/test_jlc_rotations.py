"""JLC placement rotations: which row of data/jlc/corrections.csv a footprint gets, and its correction.

  python3 tests/test_jlc_rotations.py

No pcbnew needed: `correction` only reads a footprint's reference, value and footprint name. The cases are parts JLC's
placement preview showed out on 2026-11-08, 2026-11-15 and 2026-11-16, and the rules that keep EasyEDA's footprints
(already in JLC's orientation) from being turned.
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

    def test_tsot23_6_turns_as_sot23_6(self):
        # KiCad's TSOT-23-6 has SOT-23-6's pad layout; the older ^TSOT-23 row (+180) put an LMR51625 a quarter turn out
        r = self.rule("TSOT-23-6", "C44991044", value="LMR51625YDDCR")
        self.assertEqual((r[0], r[1], r[4]), ("^TSOT-23-6(_HandSoldering)?$", -90, "footprint"))

    def test_tps2116_on_kicads_sot583(self):
        # JLC's footprint for the TPS2116DRLR lies a quarter turn from the buck the ^SOT-583-8$ row was checked on
        r = self.rule("SOT-583-8", "C3235557", value="TPS2116DRLR")
        self.assertEqual((r[0], r[1], r[4], r[5]), ("C3235557", -90, "LCSC", "^SOT-583-8$"))

    def test_bcr421uw6q_on_kicads_sot23_6(self):
        r = self.rule("SOT-23-6", "C154739", value="BCR421UW6Q-7")
        self.assertEqual((r[0], r[1], r[4], r[5]), ("C154739", 180, "LCSC", "^SOT-23-6(_Handsoldering)?$"))
        other = self.rule("SOT-23-6", "C0")
        self.assertEqual((other[0], other[1]), ("^SOT-23-6(_Handsoldering)?$", -90))

    def test_powerpak_1212_8_single_turns_as_powerpak_so8(self):
        # KiCad has pads 1-4 down the left and the drain on the right; LCSC's footprint for the WSD20L75DN33 has
        # pads 1-4 along the bottom, so it is KiCad's turned a quarter, as on PowerPAK SO-8
        r = self.rule("Vishay_PowerPAK_1212-8_Single", "C148391", ref="Q1", value="WSD20L75DN33")
        self.assertEqual((r[0], r[1], r[3], r[4]), ("^Vishay_PowerPAK_1212-8_Single", 270, "", "footprint"))

    def test_powerpak_1212_8_dual_comes_both_ways(self):
        # LCSC's SIS932EDN (C727405) has pads 1-4 along the bottom like the single; its SI7904BDN has them down the
        # left, as KiCad does
        r = self.rule("Vishay_PowerPAK_1212-8_Dual", "C727405", ref="Q2", value="SIS932EDN")
        self.assertEqual((r[0], r[1], r[4]), ("^Vishay_PowerPAK_1212-8_Dual", 270, "footprint"))
        si7904 = self.rule("Vishay_PowerPAK_1212-8_Dual", "C143037", ref="Q2", value="SI7904BDN")
        self.assertEqual((si7904[0], si7904[1], si7904[4], si7904[5]),
                         ("C143037", 0, "LCSC", "^Vishay_PowerPAK_1212-8_Dual"))

    def test_sc70_5_turns_a_quarter(self):
        # 2026-11-16: the never-checked ^SOT-353 (+180) put a 74AHCT1G125 (C350557) a quarter turn out
        r = self.rule("SOT-353_SC-70-5", "C350557", ref="U7", value="74AHCT1G125")
        self.assertEqual((r[0], r[1], r[4]), ("^SOT-353", -90, "footprint"))

    def test_hd2525_leds_by_colour(self):
        # JLC draws the green and blue XL-HD2525 a half turn from the red, though all three datasheets agree
        red = self.rule("LED_XINGLIGHT_HD2525", "C49237857", ref="LED1", value="RED")
        self.assertEqual((red[0], red[1], red[4]), ("^LED_XINGLIGHT_HD2525$", 0, "footprint"))
        for lcsc in ("C49237859", "C49237860"):
            r = self.rule("LED_XINGLIGHT_HD2525", lcsc, ref="LED2", value="GREEN")
            self.assertEqual((r[0], r[1], r[4], r[5]), (lcsc, 180, "LCSC", "^LED_XINGLIGHT_HD2525$"))

    def test_part_rule_needs_a_package_rule(self):
        # an LCSC row does nothing for a footprint no rule matches
        self.assertIsNone(self.rule("SOT-583-8_L2.1-W1.2-P0.50-LS1.6-BL", "C46550459"))

    def test_dates_are_iso_or_empty(self):
        for pattern, _, _, checked in self.table:
            with self.subTest(pattern=pattern):
                self.assertRegex(checked, r"^(\d{4}-\d{2}-\d{2})?$")

    def test_sj3524_jack_turns_a_quarter(self):
        # 2026-11-18: no rule matched, and KiCad's 180 left JLC's model a quarter turn out; "Rotate Right" put it right
        r = self.rule("Jack_3.5mm_CUI_SJ-3524-SMT_Horizontal", "C20182907", ref="J2", value="SJ-3524-SMT-TR")
        self.assertEqual((r[0], r[1], r[4]), ("^Jack_3.5mm_CUI_SJ-3524-SMT_Horizontal$", -90, "footprint"))

    def test_tqfn16_3x3_turns_a_quarter(self):
        # 2026-11-18, MAX98357A: KiCad's 270 put JLC's pin-1 dot on the left column's top pad; -90 puts it on pad 1.
        # The QFN row doesn't match a TQFN name; the thermal-via variant has the same pads
        for name in ("TQFN-16-1EP_3x3mm_P0.5mm_EP1.23x1.23mm", "TQFN-16-1EP_3x3mm_P0.5mm_EP1.23x1.23mm_ThermalVias"):
            with self.subTest(name=name):
                r = self.rule(name, "C910544", ref="U6", value="MAX98357AETE+T")
                self.assertEqual((r[0], r[1], r[4]), ("^TQFN-16-1EP_3x3mm_P0.5mm_EP1.23x1.23mm", -90, "footprint"))
        self.assertIsNone(self.rule("TQFN-16-1EP_3x3mm_P0.5mm_EP1.6x1.6mm", "C0", ref="U6"))


if __name__ == "__main__":
    unittest.main()
