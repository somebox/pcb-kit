"""Netlist topology checks: ERC proves the wiring is legal, this proves it is the wiring you meant.

The kit exports the netlist and gives you the nets; the assertions are the project's, since they encode its own
failure modes (see DESIGN.md of each board). A project's check script looks like:

    from pcbkit.checks.netlist import Netlist
    n = Netlist("boards/driver/driver.kicad_sch")        # or a .net file
    n.same(("U2", "5"), ("R1", "1"), "Rs from VIN")
    n.expect(n.pin[("U2", "5")] == "+12V", "VIN on +12V")
    n.expect(n.nodes("CSN") == 3, "CSN node has only Rs, CSN, J3 +")
    n.no_single_node_nets()
    n.finish()                                           # prints the tally, exits 1 on failures

`pcbkit nets SCHEMATIC [NET ...]` prints net sizes and members for a quick look. Net names lose their sheet path
(`/MCU/SDA` -> `SDA`) unless keep_paths is set. Harvested from the projects' netcheck.py scripts.
"""
import argparse
import os
import re
import sys
import tempfile

from ..kicad import cli


class Netlist:
    def __init__(self, source, keep_paths=False):
        if source.endswith(".kicad_sch"):
            net = os.path.join(tempfile.gettempdir(), "pcbkit_netlist.net")
            cli("sch", "export", "netlist", "--format", "kicadsexpr", "-o", net, source)
            source = net
        s = open(source).read()
        s = s[s.index("(nets"):]
        self.nets = {}
        for blk in re.split(r"\n\t\t\(net\n", s)[1:]:
            name = re.search(r'\(name "([^"]*)"\)', blk).group(1)
            if not keep_paths:
                name = name.split("/")[-1]
            self.nets[name] = set(re.findall(r'\(ref "([^"]+)"\)\s*\(pin "([^"]+)"\)', blk))
        self.pin = {p: n for n, ps in self.nets.items() for p in ps}
        self.fails = []

    def nodes(self, net):
        return len(self.nets.get(net, ()))

    def expect(self, cond, msg):
        print(("ok   " if cond else "FAIL ") + msg)
        if not cond:
            self.fails.append(msg)
        return cond

    def same(self, a, b, msg):
        return self.expect(self.pin.get(a) is not None and self.pin.get(a) == self.pin.get(b),
                           f"{msg}: {a}={self.pin.get(a)} {b}={self.pin.get(b)}")

    def no_single_node_nets(self):
        single = [n for n, v in self.nets.items() if len(v) == 1 and not n.startswith("unconnected")]
        return self.expect(not single, f"no single-node nets {single}")

    def finish(self):
        print(f"\n{len(self.nets)} nets, {len(self.fails)} failures")
        sys.exit(1 if self.fails else 0)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Print net sizes (and members of small nets) from a schematic or .net file")
    ap.add_argument("source")
    ap.add_argument("nets", nargs="*", help="nets to show; default: all, largest first")
    ap.add_argument("--keep-paths", action="store_true")
    ap.add_argument("--members", type=int, default=14, help="list members for nets up to this size")
    a = ap.parse_args(argv)
    n = Netlist(a.source, a.keep_paths)
    names = a.nets or sorted(n.nets, key=lambda k: -len(n.nets[k]))
    print("nets:", len(n.nets))
    for name in names:
        v = n.nets.get(name)
        size = len(v) if v is not None else None
        print(f"{name:24} {size!s:>5}", sorted(v) if v and len(v) <= a.members else "")
    single = [k for k, v in n.nets.items() if len(v) == 1 and not k.startswith("unconnected")]
    if single:
        print("single-node nets:", single)


if __name__ == "__main__":
    main()
