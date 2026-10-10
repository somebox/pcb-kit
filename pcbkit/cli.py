"""pcbkit command line. `pcbkit <command> --help` for each one.

Checks:    drc, erc, nets, check, cleanup
Release:   previews, print, fab, panel, setup-kikit
Look-ups:  jlc, netview, info, link
Projects:  new, import-easyeda, outline
Commands that need pcbnew re-run themselves under KiCad's Python (see kicad.py).
"""
import sys

from . import __version__

COMMANDS = {
    "drc": ("pcbkit.checks.drc", "DRC summary through kicad-cli (--refill/--save/--full)"),
    "erc": ("pcbkit.checks.erc", "ERC summary through kicad-cli"),
    "nets": ("pcbkit.checks.netlist", "net sizes and members from a schematic"),
    "check": ("pcbkit.checks.route_check", "floating copper, stubs, loops, thin power feeds (needs pcbnew)"),
    "cleanup": ("pcbkit.checks.cleanup_tracks", "remove duplicate/zero-length/covered segments, merge collinear runs"),
    "previews": ("pcbkit.release.previews", "schematic PDF, copper PNGs, assembly map, 1:1 print, renders"),
    "print": ("pcbkit.release.plot_print", "printable 1:1 PDF, front then mirrored back (needs pcbnew)"),
    "fab": ("pcbkit.release.jlc_fab", "JLC Gerber zip, BOM and CPL (needs pcbnew)"),
    "panel": ("pcbkit.release.panel", "KiKit assembly panels, vcut and mousebite styles"),
    "setup-kikit": ("pcbkit.release.panel", "install the pinned KiKit into KiCad's Python (--target)"),
    "jlc": ("pcbkit.jlc", "search konnect's JLCPCB parts cache"),
    "link": ("pcbkit.kicad", "make this checkout importable from KiCad's Python and python3 (--remove to undo)"),
    "netview": ("pcbkit.netview", "SVG of a board region coloured by net (needs pcbnew)"),
    "outline": ("pcbkit.mech", "board outline, mounting holes and keep-outs from an enclosure JSON (needs pcbnew)"),
    "new": ("pcbkit.new_project", "scaffold a hardware project: docs, boards/<b> KiCad project, lib/, scripts/, bomi, git"),
    "import-easyeda": ("pcbkit.import_easyeda", "EasyEDA Std schematic + PCB exports -> linked KiCad project and library"),
}


def info():
    from . import kicad
    print("pcbkit", __version__)
    print("kicad-cli   ", kicad.KICAD_CLI)
    print("kicad python", kicad.KICAD_PYTHON, "(pcbnew importable here: %s)" % kicad.have_pcbnew())
    print("tools dir   ", kicad.tools_dir())
    print("python      ", sys.executable)


def usage():
    print(__doc__)
    for name, (_, desc) in COMMANDS.items():
        print(f"  {name:12} {desc}")
    print("  info         where KiCad and the tools are")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        usage()
        return
    cmd, rest = argv[0], argv[1:]
    if cmd == "info":
        return info()
    if cmd == "--version":
        return print(__version__)
    if cmd not in COMMANDS:
        usage()
        sys.exit(f"unknown command {cmd}")
    if cmd == "setup-kikit":
        from .release.panel import setup_kikit
        return setup_kikit()
    if cmd in ("link", "link-kicad"):
        from .kicad import link
        return link(remove="--remove" in rest)
    import importlib
    mod = importlib.import_module(COMMANDS[cmd][0])
    mod.main(rest)


if __name__ == "__main__":
    main()
