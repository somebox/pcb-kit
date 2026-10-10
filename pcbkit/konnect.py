"""Minimal stdio MCP client for konnect, the KiCad MCP server: call its tools from scripts.

    from pcbkit.konnect import Konnect
    k = Konnect(); k.load("sch_export")
    r = k.call("run_erc", {"schematic": path, "severity": "warning"})

Toolsets load on demand (`load`). Override the binary with KONNECT_BIN. Harvested from the projects' kmcp.py.
"""
import json
import os
import subprocess

BIN = os.environ.get("KONNECT_BIN", os.path.expanduser("~/Library/Application Support/konnect/bin/konnect"))


class Konnect:
    def __init__(self, binary=BIN):
        self.p = subprocess.Popen([binary], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  text=True, bufsize=1)
        self.i = 0
        self.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                "clientInfo": {"name": "pcbkit", "version": "0"}})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _send(self, m):
        self.p.stdin.write(json.dumps(m) + "\n")
        self.p.stdin.flush()

    def rpc(self, method, params):
        self.i += 1
        self._send({"jsonrpc": "2.0", "id": self.i, "method": method, "params": params})
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError("konnect exited")
            m = json.loads(line)
            if m.get("id") == self.i:
                if "error" in m:
                    raise RuntimeError(m["error"])
                return m["result"]

    def call(self, name, args):
        r = self.rpc("tools/call", {"name": name, "arguments": args})
        txt = "".join(c.get("text", "") for c in r.get("content", []))
        try:
            out = json.loads(txt)
        except Exception:
            out = txt
        if r.get("isError"):
            raise RuntimeError(f"{name}: {txt[:2000]}")
        return out

    def load(self, *names):
        return self.call("load_toolset", {"name": list(names)})
