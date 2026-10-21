"""Read and write the small TOML files pcbkit keeps (pcbkit.toml, releases.toml, production data).

Reading uses the standard library's tomllib (Python 3.11+) or tomli, which KiCad's bundled Python ships; writing
covers what pcbkit writes itself: tables, arrays of tables, strings, numbers, booleans, dates and lists of those.
Files a person edits (pcbkit.toml, order.toml, stock.toml) are only ever read.
"""
import datetime
import re

try:
    import tomllib
except ImportError:                    # Python 3.9-3.10 (KiCad's): tomli has the same API
    try:
        import tomli as tomllib
    except ImportError:
        tomllib = None

BARE = re.compile(r"^[A-Za-z0-9_-]+$")


def load(path):
    if tomllib is None:
        raise SystemExit("reading TOML needs Python 3.11+ or the tomli package (KiCad's Python ships it)")
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def _key(k):
    return k if BARE.match(k) else '"' + k.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, (datetime.date, datetime.datetime)):
        return v.isoformat()
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_value(x) for x in v) + "]"
    raise TypeError(f"can't write {type(v).__name__} to TOML: {v!r}")


def _is_table_list(v):
    return isinstance(v, list) and v and all(isinstance(x, dict) for x in v)


def _emit(out, prefix, d):
    plain = [(k, v) for k, v in d.items() if not isinstance(v, dict) and not _is_table_list(v) and v is not None]
    for k, v in plain:
        out.append(f"{_key(k)} = {_value(v)}")
    for k, v in d.items():
        name = prefix + [_key(k)]
        if isinstance(v, dict):
            if not v or any(not isinstance(x, dict) and not _is_table_list(x) for x in v.values()):
                out.append("")
                out.append("[" + ".".join(name) + "]")
            _emit(out, name, v)
        elif _is_table_list(v):
            for item in v:
                out.append("")
                out.append("[[" + ".".join(name) + "]]")
                _emit(out, name, item)


def dumps(d, header=""):
    out = [f"# {line}" if line else "#" for line in header.splitlines()]
    _emit(out, [], d)
    return "\n".join(out).lstrip("\n") + "\n"


def dump(d, path, header=""):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(dumps(d, header))
