"""Where KiCad is, and how to get into its Python.

Two interpreters are involved: the one pcbkit is installed in (uv's), and KiCad's bundled Python, the only one
with `pcbnew`. A command that needs pcbnew calls `ensure_pcbnew()` first; outside KiCad's Python it re-executes
the whole CLI under it, with this package on PYTHONPATH. Override the KiCad location with PCBKIT_KICAD_APP.
"""
import os
import subprocess
import sys

KICAD_APP = os.environ.get("PCBKIT_KICAD_APP", "/Applications/KiCad/KiCad.app")
KICAD_CLI = os.path.join(KICAD_APP, "Contents", "MacOS", "kicad-cli")
KICAD_PYTHON = os.path.join(KICAD_APP, "Contents", "Frameworks", "Python.framework", "Versions", "Current", "bin", "python3")
NOISE = ("wxApp", "swig/python", "Debug: Adding duplicate image handler")


def have_pcbnew():
    try:
        import pcbnew  # noqa: F401
        return True
    except ImportError:
        return False


def ensure_pcbnew():
    """Re-exec the current command under KiCad's Python when pcbnew is not importable here."""
    if have_pcbnew():
        return
    if os.environ.get("PCBKIT_REEXEC"):
        sys.exit(f"pcbnew is not importable even under {sys.executable}; check PCBKIT_KICAD_APP ({KICAD_APP})")
    if not os.path.exists(KICAD_PYTHON):
        sys.exit(f"KiCad's Python not found at {KICAD_PYTHON}; set PCBKIT_KICAD_APP")
    pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ)
    env["PYTHONPATH"] = pkg_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PCBKIT_REEXEC"] = "1"
    os.execve(KICAD_PYTHON, [KICAD_PYTHON, "-m", "pcbkit.cli", *sys.argv[1:]], env)


def cli(*args, check=True, capture=True):
    """Run kicad-cli. Returns the CompletedProcess; raises on failure when check is set."""
    return subprocess.run([KICAD_CLI, *args], check=check, capture_output=capture, text=True)


def quiet(text):
    """Drop the wxWidgets and SWIG chatter KiCad's Python prints on stderr."""
    return "\n".join(l for l in text.splitlines() if not any(n in l for n in NOISE))


def save_board(board, path):
    """Save headless. pcbnew.SaveBoard aborts without a wxApp; PCB_IO_MGR.Save does not."""
    import pcbnew
    pcbnew.PCB_IO_MGR.Save(pcbnew.PCB_IO_MGR.KICAD_SEXP, str(path), board)


def data_path(*parts):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", *parts)


def tools_dir():
    """Where pinned external tools (KiKit) are installed, shared by every project."""
    return os.environ.get("PCBKIT_TOOLS", os.path.expanduser("~/.local/share/pcbkit"))
