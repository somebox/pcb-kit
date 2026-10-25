"""Where KiCad is, and how to get into its Python.

Two interpreters are involved: the one pcbkit is installed in (uv's), and KiCad's bundled Python, the only one
with `pcbnew`. A command that needs pcbnew calls `ensure_pcbnew()` first; outside KiCad's Python it re-executes
the whole CLI under it, with this package on PYTHONPATH. Override the KiCad location with PCBKIT_KICAD_APP.

kicad-cli runs from a background copy of it, KiCadCLI.app under tools_dir()/kicad-cli/, so it shows no dock icon:
from inside KiCad.app, macOS gives every run of it (DRC, ERC, renders, exports) the app's icon. The copy is
kicad-cli signed again ad hoc, KiCad's Info.plist with its own bundle id and LSUIElement set, and links to KiCad's
Frameworks, PlugIns, Resources and SharedSupport; its DRC reports and renders match KiCad's byte for byte (KiCad
10.0.6). There is one copy for each KiCad and version, made on first use and never changed, so a KICAD_CLI that a
process holds stays valid while others make copies of another KiCad or a newer version; a copy whose KiCad has
changed since is removed. PCBKIT_KICAD_DOCK=1 runs KiCad's own kicad-cli.
"""
import hashlib
import os
import plistlib
import shutil
import subprocess
import sys

NOISE = ("wxApp", "swig/python", "Debug: Adding duplicate image handler")
SOURCE, STAMP = "PcbkitKiCad", "PcbkitStamp"      # in the background copy's Info.plist: the KiCad it was made from


def tools_dir():
    """Where pinned external tools (KiKit) are installed, shared by every project."""
    return os.environ.get("PCBKIT_TOOLS", os.path.expanduser("~/.local/share/pcbkit"))


def _plist(app):
    with open(os.path.join(app, "Contents", "Info.plist"), "rb") as fh:
        return plistlib.load(fh)


def _stamp(kicad):
    """What a background copy of kicad must match: KiCad's version and its kicad-cli."""
    st = os.stat(os.path.join(kicad, "Contents", "MacOS", "kicad-cli"))
    return "%s %d %d" % (_plist(kicad).get("CFBundleVersion", "?"), st.st_size, int(st.st_mtime))


def _make_background(kicad, stamp, dest):
    """Build the copy beside dest and publish it with one rename, so dest is either absent or complete."""
    info = _plist(kicad)
    info.update(CFBundleIdentifier=info["CFBundleIdentifier"] + ".cli", CFBundleExecutable="kicad-cli",
                LSUIElement=True)
    info[SOURCE], info[STAMP] = kicad, stamp
    tmp = dest + ".new-%d" % os.getpid()
    try:
        os.makedirs(os.path.join(tmp, "Contents", "MacOS"))
        for d in ("Frameworks", "PlugIns", "Resources", "SharedSupport"):
            os.symlink(os.path.join(kicad, "Contents", d), os.path.join(tmp, "Contents", d))
        exe = os.path.join(tmp, "Contents", "MacOS", "kicad-cli")
        shutil.copy2(os.path.join(kicad, "Contents", "MacOS", "kicad-cli"), exe)
        with open(os.path.join(tmp, "Contents", "Info.plist"), "wb") as fh:
            plistlib.dump(info, fh)
        # KiCad's signature covers its own Info.plist: the copy is signed again, ad hoc
        subprocess.run(["codesign", "--force", "--sign", "-", exe], check=True, capture_output=True)
        subprocess.run([exe, "version"], check=True, capture_output=True)
        try:
            os.rename(tmp, dest)
        except OSError:
            if not os.path.exists(dest):            # else another process published the same copy first
                raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    _prune(dest)


def _prune(keep):
    """Remove the copies whose KiCad has changed or gone: their kicad-cli no longer matches the frameworks they link."""
    root = os.path.dirname(os.path.dirname(keep))
    for key in os.listdir(root):
        app = os.path.join(root, key, "KiCadCLI.app")
        if app == keep or not os.path.exists(app):  # no KiCadCLI.app yet: a copy still being made
            continue
        try:
            info = _plist(app)
            current = _stamp(info[SOURCE]) == info[STAMP]
        except (OSError, ValueError, KeyError):
            current = False
        if not current:
            shutil.rmtree(os.path.join(root, key), ignore_errors=True)


def _kicad(app):
    """KiCad itself, also when given its background copy (a project's scripts may export that as PCBKIT_KICAD_APP)."""
    app = os.path.realpath(app)
    try:
        return _plist(app).get(SOURCE, app)
    except (OSError, ValueError):
        return app


def background_app(kicad):
    """kicad's background copy, made on first use; kicad itself if it can't be, or with PCBKIT_KICAD_DOCK=1."""
    if sys.platform != "darwin" or os.environ.get("PCBKIT_KICAD_DOCK") == "1":
        return kicad
    try:
        stamp = _stamp(kicad)
        key = hashlib.sha1(("%s\n%s" % (kicad, stamp)).encode()).hexdigest()[:12]
        dest = os.path.join(tools_dir(), "kicad-cli", key, "KiCadCLI.app")
        if not os.path.exists(dest):
            _make_background(kicad, stamp, dest)
        return dest
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as e:
        print(f"pcbkit: KiCad's own kicad-cli, with its dock icon ({e})", file=sys.stderr)
        return kicad


KICAD_APP = _kicad(os.environ.get("PCBKIT_KICAD_APP", "/Applications/KiCad/KiCad.app"))
if "PCBKIT_KICAD_APP" in os.environ:
    # KiCad itself, not a copy named there: background_app() may remove that copy, and ensure_pcbnew()'s re-run and
    # every other child would inherit the gone path
    os.environ["PCBKIT_KICAD_APP"] = KICAD_APP
KICAD_CLI = os.path.join(background_app(KICAD_APP), "Contents", "MacOS", "kicad-cli")
KICAD_PYTHON = os.path.join(KICAD_APP, "Contents", "Frameworks", "Python.framework", "Versions", "Current", "bin", "python3")


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


def _site_dir(python):
    """Where a .pth is read by this interpreter: its user site, or its own site-packages for a venv (user site off)."""
    r = subprocess.run([python, "-c", "import site; print(site.ENABLE_USER_SITE); print(site.getusersitepackages()); "
                        "print(site.getsitepackages()[0])"], capture_output=True, text=True, check=True)
    enabled, user, own = r.stdout.strip().splitlines()
    return user if enabled == "True" else own


def link(remove=False):
    """Make this checkout importable from KiCad's Python and from the `python3` on PATH, by writing a pcbkit.pth into
    each one's user site-packages, or a venv's own site-packages when its user site is off (nothing goes into an app
    bundle or a managed install; survives upgrades).
    Project scripts run under either interpreter: pcbnew ones under KiCad's, konnect/netlist ones under python3."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pythons = [("KiCad's Python", KICAD_PYTHON)]
    default = shutil.which("python3")
    if default and os.path.realpath(default) != os.path.realpath(KICAD_PYTHON):
        pythons.append(("python3 on PATH", default))
    for label, py in pythons:
        if not os.path.exists(py):
            print(f"{label}: not found at {py}")
            continue
        pth = os.path.join(_site_dir(py), "pcbkit.pth")
        if remove:
            if os.path.exists(pth):
                os.remove(pth)
            print(f"{label}: removed {pth}")
            continue
        os.makedirs(os.path.dirname(pth), exist_ok=True)
        with open(pth, "w") as fh:
            fh.write(root + "\n")
        r = subprocess.run([py, "-c", "import pcbkit; print(pcbkit.__file__)"], capture_output=True, text=True, cwd="/")
        ok = r.stdout.strip()
        print(f"{label} ({py}): {pth}" + (f" -> imports {ok}" if ok else " -> FAILED: " + r.stderr.strip()[-200:]))


link_kicad = link  # old name
