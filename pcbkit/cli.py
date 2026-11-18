"""pcbkit command line. `pcbkit <command> --help` for each one.

Checks:    drc, erc, sch-lint, nets, check, courtyards, cleanup
Release:   release, changelog, production, previews, print, fab, panel, setup-kikit
Look-ups:  jlc, parts, netview, info, link
Analysis:  spice, place-score
Routing:   reroute, setup-router
Projects:  new, import-easyeda, outline, overview
Commands that need pcbnew re-run themselves under KiCad's Python (see kicad.py).
"""
import sys

from . import __version__

# name: (module, what it does) and, where the module's entry point is not main(), that function's name. Every entry
# point reads its own arguments before it does anything, so --help and -h only ever print (tests/test_cli.py).
COMMANDS = {
    "drc": ("pcbkit.checks.drc", "DRC summary through kicad-cli (--refill/--save/--full)"),
    "erc": ("pcbkit.checks.erc", "ERC summary through kicad-cli"),
    "sch-lint": ("pcbkit.checks.sch_lint", "how a schematic reads: overlapping text, stray fields, nets joined by labels"),
    "nets": ("pcbkit.checks.netlist", "net sizes and members from a schematic"),
    "check": ("pcbkit.checks.route_check", "floating copper, stubs, loops, thin power feeds (needs pcbnew)"),
    "courtyards": ("pcbkit.checks.courtyards", "courtyards that miss pads or the body, or sit on the other side (--fix redraws)"),
    "cleanup": ("pcbkit.checks.cleanup_tracks", "remove duplicate/zero-length/covered (--dangling: dead-end) segments, merge runs"),
    "release": ("pcbkit.release.release", "version, stamp and fab every changed board (pcbkit.toml), then production"),
    "changelog": ("pcbkit.release.changelog", "draft CHANGELOG.md: what changed in each board since its last release (--since)"),
    "production": ("pcbkit.production", "the production page: versions, orders, stock (--check)"),
    "previews": ("pcbkit.release.previews", "schematic PDF, copper PNGs, assembly map, 1:1 print, renders"),
    "print": ("pcbkit.release.plot_print", "printable 1:1 PDF, front then mirrored back (needs pcbnew)"),
    "fab": ("pcbkit.release.jlc_fab", "JLC Gerber zip, BOM and CPL (needs pcbnew)"),
    "panel": ("pcbkit.release.panel", "KiKit assembly panels, vcut and mousebite styles"),
    "setup-kikit": ("pcbkit.release.panel", "install the pinned KiKit under ~/.local/share/pcbkit/kikit (PCBKIT_TOOLS), "
                                            "for pcbkit panel", "setup_main"),
    "jlc": ("pcbkit.jlc", "search konnect's JLCPCB parts cache"),
    "parts": ("pcbkit.parts", "the proven-parts catalog (library/PARTS.md) with current stock"),
    "link": ("pcbkit.kicad", "make this checkout importable from KiCad's Python and python3 (--remove to undo)",
             "link_main"),
    "netview": ("pcbkit.netview", "SVG of a board region coloured by net (needs pcbnew)"),
    "spice": ("pcbkit.spice", "run an ngspice deck (PSpice-compatible), print its measurements, flag aborted runs"),
    "place-score": ("pcbkit.placement", "score and search placements by their ratsnest, draw it (needs pcbnew)"),
    "reroute": ("pcbkit.reroute.flow", "lay a board's signals again: placement, negotiated routing, polish, repairs, review"),
    "setup-router": ("pcbkit.reroute.flow", "install the router's own Python (numba, numpy, scipy) with uv",
                     "setup_main"),
    "outline": ("pcbkit.mech", "board outline, mounting holes and keep-outs from an enclosure JSON (needs pcbnew)"),
    "overview": ("pcbkit.overview", "the project on one page: docs/overview.html from a file per design round (new, note, --check)"),
    "new": ("pcbkit.new_project", "scaffold a hardware project: docs, boards/<b> KiCad project, lib/, scripts/, bomi, git"),
    "import-easyeda": ("pcbkit.import_easyeda", "EasyEDA Std schematic + PCB exports -> linked KiCad project and library"),
    "info": ("pcbkit.cli", "where KiCad and the tools are", "info_main"),
}


def info():
    from . import kicad
    print("pcbkit", __version__)
    print("kicad-cli   ", kicad.KICAD_CLI)
    print("kicad python", kicad.KICAD_PYTHON, "(pcbnew importable here: %s)" % kicad.have_pcbnew())
    print("tools dir   ", kicad.tools_dir())
    print("python      ", sys.executable)


def info_main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="pcbkit info", description="Print pcbkit's version and where it finds kicad-cli, "
                                 "KiCad's Python, its tools folder and the Python it runs under. Takes no options.")
    ap.parse_args(argv)
    info()


def usage():
    print(__doc__)
    for name, entry in COMMANDS.items():
        print(f"  {name:12} {entry[1]}")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        usage()
        return
    cmd, rest = argv[0], argv[1:]
    if cmd == "--version":
        return print(__version__)
    if cmd not in COMMANDS:
        usage()
        sys.exit(f"unknown command {cmd}")
    import importlib
    entry = COMMANDS[cmd]
    mod = importlib.import_module(entry[0])
    getattr(mod, entry[2] if len(entry) > 2 else "main")(rest)


if __name__ == "__main__":
    main()
