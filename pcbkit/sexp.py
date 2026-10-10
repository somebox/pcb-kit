"""Minimal S-expression reader/writer for KiCad files (schematics, symbol libraries)."""
import re

_TOKEN = re.compile(r'\s*(?:(\()|(\))|("(?:[^"\\]|\\.)*")|([^\s()"]+))')


class Sym(str):
    """Bare (unquoted) atom."""


def parse(text):
    stack, cur = [], []
    for m in _TOKEN.finditer(text):
        lp, rp, qs, atom = m.groups()
        if lp:
            stack.append(cur)
            cur = []
        elif rp:
            done = cur
            cur = stack.pop()
            cur.append(done)
        elif qs is not None:
            cur.append(qs[1:-1].replace('\\"', '"').replace('\\\\', '\\'))
        elif atom is not None:
            cur.append(Sym(atom))
    assert not stack, "unbalanced parentheses"
    return cur[0] if len(cur) == 1 else cur


def _atom(x):
    if isinstance(x, Sym):
        return str(x)
    if isinstance(x, (int, float)):
        return f"{x:g}" if isinstance(x, float) else str(x)
    return '"' + str(x).replace('\\', '\\\\').replace('"', '\\"') + '"'


def dump(node, indent=0):
    if not isinstance(node, list):
        return _atom(node)
    if all(not isinstance(c, list) for c in node):
        return "(" + " ".join(_atom(c) for c in node) + ")"
    pad = "\t" * (indent + 1)
    out, seen_list = "(", False
    for i, c in enumerate(node):
        if isinstance(c, list):
            out += "\n" + pad + dump(c, indent + 1)
            seen_list = True
        elif seen_list:
            out += "\n" + pad + _atom(c)
        else:
            out += ("" if i == 0 else " ") + _atom(c)
    return out + "\n" + "\t" * indent + ")"


def find(node, key):
    """Direct children lists whose head is `key`."""
    return [c for c in node if isinstance(c, list) and c and c[0] == key]


def first(node, key):
    r = find(node, key)
    return r[0] if r else None


def prop(node, name):
    for p in find(node, "property"):
        if p[1] == name:
            return p[2]
    return None
