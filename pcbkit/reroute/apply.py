"""Routes written into a board.

    python -m pcbkit.reroute.apply IN.kicad_pcb ROUTES.json [...] OUT.kicad_pcb [--via=0.45,0.25]        (KiCad's Python)
"""
import json
import sys

from ..kicad import save_board

OFF = 100.0


def main(argv=None):
    import pcbnew
    argv = sys.argv[1:] if argv is None else argv
    args = [a for a in argv if not a.startswith("--")]
    size, drill = map(float, next((a[6:] for a in argv if a.startswith("--via=")), "0.45,0.25").split(","))
    mm = pcbnew.FromMM

    def at(x, y):
        return pcbnew.VECTOR2I(mm(x + OFF), mm(y + OFF))

    b = pcbnew.LoadBoard(args[0])
    layer = {"F": b.GetLayerID("F.Cu"), "B": b.GetLayerID("B.Cu")}
    nets = {n.GetNetname(): n for n in b.GetNetsByName().values()}
    segments = vias = 0
    for path in args[1:-1]:
        with open(path) as fh:
            routes = json.load(fh)
        for r in routes:
            net = nets[r["net"]]
            for t in r["tracks"]:
                for (x0, y0), (x1, y1) in zip(t["pts"], t["pts"][1:]):
                    s = pcbnew.PCB_TRACK(b)
                    s.SetLayer(layer[t["layer"]])
                    s.SetNet(net)
                    s.SetWidth(mm(t["w"]))
                    s.SetStart(at(x0, y0))
                    s.SetEnd(at(x1, y1))
                    b.Add(s)
                    segments += 1
            for x, y in r["vias"]:
                v = pcbnew.PCB_VIA(b)
                v.SetPosition(at(x, y))
                v.SetNet(net)
                v.SetWidth(mm(size))
                v.SetDrill(mm(drill))
                v.SetLayerPair(layer["F"], layer["B"])
                b.Add(v)
                vias += 1
    save_board(b, args[-1])
    print(f"{segments} segments, {vias} vias -> {args[-1]}")


if __name__ == "__main__":
    main()
