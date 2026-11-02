"""Compiled A* for netroute.py: state = (layer, jogged, last move, y, x); moves are the 8 lattice steps on a layer and a via.

Costs are in lattice units (one step along the layer's direction = 1). Off-direction track gets dearer the further it
goes: each state carries `acc`, the off-direction track (mm) laid since the last via, which raises the cost of the
next diagonal or wrong-way step and of the next via. Along the layer's direction acc decays (half-life set by the
caller), at a via it resets. `jogged` (acc > 0) is part of the state, so a clean path and a jogged one to the same
node don't shadow each other; acc itself is carried with the best path to a state (not exact, close enough).
Within `free` (near this net's pads) every direction costs its length and nothing accumulates.
A via needs KV steps on the current layer since the previous via (no via-jogs).
Negotiation (nroute.py): `pen` adds a cost per step on entering a node (per layer, times the step's length)
and `penv` per via, for other nets' copper, history, pours and the routing rules."""
import numpy as np
from numba import njit

DX = np.array([0, 0, 1, -1, 1, 1, -1, -1], np.int64)
DY = np.array([1, -1, 0, 0, 1, -1, 1, -1], np.int64)
SQ2 = np.float32(1.4142135)
STEP_MM = np.float32(0.05)


@njit(cache=True)
def _push(hk, hv, n, k, v):
    if n >= hk.shape[0]:
        nk = np.empty(hk.shape[0] * 2, hk.dtype); nk[:n] = hk[:n]; hk = nk
        nv = np.empty(hv.shape[0] * 2, hv.dtype); nv[:n] = hv[:n]; hv = nv
    i = n; hk[i] = k; hv[i] = v
    while i > 0:
        p = (i - 1) >> 1
        if hk[p] <= hk[i]: break
        hk[p], hk[i] = hk[i], hk[p]; hv[p], hv[i] = hv[i], hv[p]; i = p
    return hk, hv, n + 1


@njit(cache=True)
def _pop(hk, hv, n):
    k, v = hk[0], hv[0]; n -= 1
    hk[0] = hk[n]; hv[0] = hv[n]; i = 0
    while True:
        l = 2 * i + 1; r = l + 1; m = i
        if l < n and hk[l] < hk[m]: m = l
        if r < n and hk[r] < hk[m]: m = r
        if m == i: break
        hk[m], hk[i] = hk[i], hk[m]; hv[m], hv[i] = hv[i], hv[m]; i = m
    return k, v, n


_BUF = [None]


def search(ft, fv, tgt, hd, starts, free, cost, kv, max_exp, tcost, pen, penv):
    """the search, on state arrays kept between calls: allocating ~1 GB for every connection of a board-wide net
    took longer than the search itself"""
    N = 2 * 2 * 9 * fv.shape[0] * fv.shape[1]
    b = _BUF[0]
    if b is None or b[0].size < N:
        _BUF[0] = None
        b = _BUF[0] = (np.full(N, np.inf, np.float32), np.zeros(N, np.float32), np.full(N, -1, np.int8),
                       np.zeros(N, np.int8), np.zeros(N, np.int8), np.zeros(N, np.bool_))
    return _search(ft, fv, tgt, hd, starts, free, cost, kv, max_exp, tcost, pen, penv, *b)


@njit(cache=True)
def _search(ft, fv, tgt, hd, starts, free, cost, kv, max_exp, tcost, pen, penv, g, acc, pm, pd, pb, closed):
    """ft, tgt: (2, H, W) bool; fv, free: (H, W) bool; hd: (H, W) float32 heuristic; starts: (n, 3) int64
    (layer, y, x); cost: (diag base, diag slope, wrong base, wrong slope, via base, via slope, bend, decay, turn), slopes
    per mm of acc, decay per step along the layer's direction, a 45-degree turn costs bend, 90 degrees bend x turn and
    135 or more bend x turn^2; pen: (2, H, W), penv: (H, W) float32, extra cost per
    unit step into a node and per via. F (layer 0) runs vertical, B (layer 1) horizontal.
    Returns the path as (m, 3) int64 (layer, y, x), empty when none."""
    H, W = fv.shape
    db, ds, wb, ws, vb, vs, bend, decay, turn = cost[0], cost[1], cost[2], cost[3], cost[4], cost[5], cost[6], cost[7], cost[8]
    jog = ds > 0 or ws > 0 or vs > 0             # no slopes (true length): jogging costs nothing, so no jogged states
    N = 2 * 2 * 9 * H * W
    hk = np.empty(1 << 20, np.float32); hv = np.empty(1 << 20, np.int64); n = 0
    tk = np.empty(1 << 20, np.int64); nt = 0           # states touched, reset before returning
    for i in range(starts.shape[0]):
        li, y, x = starts[i, 0], starts[i, 1], starts[i, 2]
        s = (((li * 2) * 9 + 8) * H + y) * W + x
        if g[s] == np.inf: tk, nt = _touch(tk, nt, s)
        g[s] = tcost[y, x]; pm[s] = -2               # leaving off-centre costs as much as arriving off-centre
        hk, hv, n = _push(hk, hv, n, g[s] + hd[y, x], s)
    exp = 0; goal = -1
    while n > 0:
        f, s, n = _pop(hk, hv, n)
        if s >= N:                                   # a goal entry: arrival cost includes the off-centre cost
            goal = s - N; break
        if closed[s]: continue
        closed[s] = True
        x = s % W; t = s // W; y = t % H; t //= H; d = t % 9; t //= 9; b = t % 2; li = t // 2
        exp += 1
        if exp > max_exp: break
        gs = g[s]; a = acc[s]
        for k in range(8):
            nx = x + DX[k]; ny = y + DY[k]
            if nx < 0 or ny < 0 or nx >= W or ny >= H or not ft[li, ny, nx]: continue
            if k >= 4 and not (ft[li, y, nx] or ft[li, ny, x]): continue
            along = (li == 0 and k < 2) or (li == 1 and (k == 2 or k == 3))
            if free[y, x] or free[ny, nx]:
                c = SQ2 if k >= 4 else np.float32(1.0); na = a
            elif along:
                c = np.float32(1.0); na = a * decay
            elif k >= 4:
                c = SQ2 * (db + ds * a); na = a + SQ2 * STEP_MM
            else:
                c = wb + ws * a; na = a + STEP_MM
            if d < 8 and d != k:
                dot = DX[d] * DX[k] + DY[d] * DY[k]
                c += bend if dot > 0 else (bend * turn if dot == 0 else bend * turn * turn)
            c += pen[li, ny, nx] * (SQ2 if k >= 4 else np.float32(1.0))
            if na < 0.02 or not jog: na = np.float32(0.0)
            nb = 1 if na > 0 else 0
            c += gs
            ns = (((li * 2 + nb) * 9 + k) * H + ny) * W + nx
            if c < g[ns] and not closed[ns]:
                if g[ns] == np.inf: tk, nt = _touch(tk, nt, ns)
                g[ns] = c; acc[ns] = na; pm[ns] = k; pd[ns] = d; pb[ns] = b
                hk, hv, n = _push(hk, hv, n, c + hd[ny, nx], ns)
                if tgt[li, ny, nx]:
                    hk, hv, n = _push(hk, hv, n, c + tcost[ny, nx], ns + N)
        if fv[y, x] and ft[1 - li, y, x]:
            ok = True; steps = 0; t2 = s
            while steps < kv:                      # walk back: no via within kv steps
                m = pm[t2]
                if m == -2: break
                if m == 8: ok = False; break
                tx = t2 % W; tt = t2 // W; ty = tt % H; tt //= H; tt //= 9; tl = tt // 2
                t2 = (((tl * 2 + pb[t2]) * 9 + pd[t2]) * H + ty - DY[m]) * W + tx - DX[m]
                steps += 1
            if ok:
                ns = ((((1 - li) * 2) * 9 + 8) * H + y) * W + x
                c = gs + vb + vs * a + penv[y, x]
                if c < g[ns] and not closed[ns]:
                    if g[ns] == np.inf: tk, nt = _touch(tk, nt, ns)
                    g[ns] = c; acc[ns] = 0.0; pm[ns] = 8; pd[ns] = d; pb[ns] = b
                    hk, hv, n = _push(hk, hv, n, c + hd[y, x], ns)
                    if tgt[1 - li, y, x]:
                        hk, hv, n = _push(hk, hv, n, c + tcost[y, x], ns + N)
    if goal < 0:
        _reset(tk, nt, g, acc, pm, pd, pb, closed)
        return np.empty((0, 3), np.int64)
    out = []
    s = goal
    while True:
        x = s % W; t = s // W; y = t % H; t //= H; t //= 9; li = t // 2
        out.append((li, y, x))
        m = pm[s]
        if m == -2: break
        if m == 8:
            s = ((((1 - li) * 2 + pb[s]) * 9 + pd[s]) * H + y) * W + x
        else:
            s = (((li * 2 + pb[s]) * 9 + pd[s]) * H + y - DY[m]) * W + x - DX[m]
    res = np.empty((len(out), 3), np.int64)
    for i in range(len(out)):
        j = len(out) - 1 - i
        res[i, 0] = out[j][0]; res[i, 1] = out[j][1]; res[i, 2] = out[j][2]
    _reset(tk, nt, g, acc, pm, pd, pb, closed)
    return res


@njit(cache=True)
def _touch(tk, nt, s):
    if nt >= tk.shape[0]:
        nk = np.empty(tk.shape[0] * 2, tk.dtype); nk[:nt] = tk[:nt]; tk = nk
    tk[nt] = s
    return tk, nt + 1


@njit(cache=True)
def _reset(tk, nt, g, acc, pm, pd, pb, closed):
    for i in range(nt):
        s = tk[i]; g[s] = np.inf; acc[s] = 0.0; pm[s] = -1; pd[s] = 0; pb[s] = 0; closed[s] = False
