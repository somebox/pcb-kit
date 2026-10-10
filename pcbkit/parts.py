"""Parts we keep using: LCSC numbers for the stock resistors and capacitors, by value and package.

All JLC Basic or Preferred (no extended-part fee) unless noted. Harvested from the projects' builder.py tables;
check stock with `pcbkit jlc --lcsc C25744 ...` or bomi before an order. Add a part here only once it has been
ordered and worked.
"""
FP = {
    "R0402": "Resistor_SMD:R_0402_1005Metric",
    "R0603": "Resistor_SMD:R_0603_1608Metric",
    "C0402": "Capacitor_SMD:C_0402_1005Metric",
    "C0603": "Capacitor_SMD:C_0603_1608Metric",
    "C0805": "Capacitor_SMD:C_0805_2012Metric",
    "C1206": "Capacitor_SMD:C_1206_3216Metric",
}

# 0402 resistors (1 %)
RES = {"27": "C25156", "33": "C25105", "1k": "C11702", "1.2k": "C25862", "4.7k": "C25900", "5.1k": "C25905",
       "10k": "C25744", "22k": "C25768", "100k": "C25741"}
# 0603 resistors, for hand-soldered sides that want nothing smaller than 0603
RES0603 = {"1k": "C21190", "10k": "C25804", "22k": "C31850", "100k": "C25803"}

# capacitors: value -> (LCSC, footprint key)
CAP = {"15pF": ("C1548", "C0402"), "100nF": ("C1525", "C0402"), "1uF": ("C52923", "C0402"), "4.7uF": ("C23733", "C0402"),
       "10uF": ("C19702", "C0603"), "22uF": ("C45783", "C0805"), "100uF": ("C15008", "C1206")}
CAP0603 = {"100nF": "C14663", "10uF": "C19702"}


def resistor(value, pkg="0402"):
    """(footprint, LCSC) for a stock resistor."""
    if pkg == "0603":
        return FP["R0603"], RES0603[value]
    return FP["R0402"], RES[value]


def capacitor(value, pkg=None):
    """(footprint, LCSC) for a stock capacitor; pkg="0603" forces 0603 where a 0603 part exists."""
    lcsc, fpkey = CAP[value]
    if pkg == "0603" and fpkey != "C0603":
        lcsc, fpkey = CAP0603[value], "C0603"
    return FP[fpkey], lcsc
