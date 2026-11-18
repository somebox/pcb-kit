"""The command line itself: `--help` and `-h` print help for every command and do nothing else.

  python3 tests/test_cli.py     # plain Python: no command is run, so it needs no KiCad

`pcbkit info`, `link`, `setup-kikit` and `setup-router` used to be handed to their functions before any argument
was read, so asking for help reinstalled KiKit or the router from the network, or rewrote the pcbkit.pth files. Each
case here calls the CLI with everything that starts a process, removes, creates or writes a file made to fail.
"""
import contextlib
import io
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.dont_write_bytecode = True         # importing a command's module must not write either

from pcbkit import cli, kicad  # noqa: E402  (kicad: it finds KiCad as it loads, before anything is forbidden)

FORBIDDEN = ("subprocess.run", "subprocess.Popen", "subprocess.call", "subprocess.check_call",
             "subprocess.check_output", "os.execve", "os.execv", "os.system", "os.remove", "os.unlink", "os.rename",
             "os.replace", "os.makedirs", "os.mkdir", "os.symlink", "shutil.rmtree", "shutil.copy", "shutil.copy2",
             "shutil.copytree", "shutil.move")
_open = open


def reading_open(file, mode="r", *args, **kwargs):
    if any(c in mode for c in "wax+"):
        raise AssertionError("open(%r, %r)" % (file, mode))
    return _open(file, mode, *args, **kwargs)


def pcbkit(*argv):
    """Run the CLI where nothing may be started, removed or written: (exit status, stdout, stderr)."""
    out, err, code = io.StringIO(), io.StringIO(), 0
    with contextlib.ExitStack() as stack:
        for target in FORBIDDEN:
            stack.enter_context(mock.patch(target, side_effect=AssertionError(target + " called")))
        stack.enter_context(mock.patch("builtins.open", reading_open))
        stack.enter_context(contextlib.redirect_stdout(out))
        stack.enter_context(contextlib.redirect_stderr(err))
        try:
            cli.main(list(argv))
        except SystemExit as e:
            code = 0 if e.code is None else e.code
    return code, out.getvalue(), err.getvalue()


class Help(unittest.TestCase):
    def test_every_command_prints_its_help_and_does_nothing(self):
        for name in cli.COMMANDS:
            for flag in ("--help", "-h"):
                with self.subTest(command=name, flag=flag):
                    code, out, err = pcbkit(name, flag)
                    self.assertEqual(code, 0, err)
                    self.assertGreater(len(out.strip()), 40, "no help text")

    def test_the_four_that_ran_instead(self):
        for name in ("info", "link", "setup-kikit", "setup-router"):
            for flag in ("--help", "-h"):
                with self.subTest(command=name, flag=flag):
                    code, out, _ = pcbkit(name, flag)
                    self.assertEqual(code, 0)
                    self.assertIn("usage: pcbkit " + name, out)

    def test_an_argument_they_do_not_take_is_refused_before_anything_runs(self):
        for argv in (("setup-kikit", "--target", "x"), ("setup-router", "now"), ("info", "--all"), ("link", "--undo")):
            with self.subTest(argv=argv):
                code, _, err = pcbkit(*argv)
                self.assertEqual(code, 2)
                self.assertIn("unrecognized arguments", err)

    def test_link_reads_remove(self):
        for argv, remove in ((("link",), False), (("link", "--remove"), True)):
            with mock.patch.object(kicad, "link") as link:
                self.assertEqual(pcbkit(*argv)[0], 0)
            link.assert_called_once_with(remove=remove)

    def test_setup_commands_run_without_arguments(self):
        from pcbkit.release import panel
        from pcbkit.reroute import flow
        with mock.patch.object(panel, "setup_kikit") as kikit, mock.patch.object(flow, "setup_router") as router:
            self.assertEqual(pcbkit("setup-kikit")[0], 0)
            self.assertEqual(pcbkit("setup-router")[0], 0)
        kikit.assert_called_once_with()
        router.assert_called_once_with()

    def test_the_command_list(self):
        for argv in ((), ("--help",), ("-h",)):
            code, out, _ = pcbkit(*argv)
            self.assertEqual(code, 0)
            for name in cli.COMMANDS:
                self.assertRegex(out, r"(?m)^  %s +\S" % name)

    def test_an_unknown_command_fails(self):
        code, out, _ = pcbkit("frobnicate", "--help")
        self.assertEqual(code, "unknown command frobnicate")
        self.assertIn("  drc ", out)

    def test_version(self):
        from pcbkit import __version__
        self.assertEqual(pcbkit("--version"), (0, __version__ + "\n", ""))

    def test_every_entry_point_exists(self):
        import importlib
        for name, entry in cli.COMMANDS.items():
            with self.subTest(command=name):
                mod = importlib.import_module(entry[0])
                self.assertTrue(callable(getattr(mod, entry[2] if len(entry) > 2 else "main", None)))


if __name__ == "__main__":
    unittest.main()
