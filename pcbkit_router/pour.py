"""Pour geometry for nroute.py and pourcheck.py.

Between two terminals (pads or vias the pour joins) a pour carries current through its narrowest point on the
widest path: the largest width b such that the copper at least b wide, with the terminals, still joins them. The
spine is that path at 80 % of b: while it stays copper the pour keeps most of its width between the two, so a
signal may notch the pour but never sever it or narrow it by more than a fifth. Rasters are bool arrays at `res` mm per cell."""
import itertools
import numpy as np
from numba import njit
from scipy import ndimage


def width_map(cu, terms, res):
    """local width (mm) of the copper, terminals counted as wide"""
    w = ndimage.distance_transform_edt(cu) * res * 2
    for m in terms: w[m] = 1e3
    return w


def joined(m, a, b):
    lab, _ = ndimage.label(m, structure=np.ones((3, 3)))
    return bool((set(np.unique(lab[a & m])) - {0}) & (set(np.unique(lab[b & m])) - {0}))


def bottleneck(w, a, b, hi=20.0):
    if not joined(w > 0, a, b): return 0.0
    lo = 0.0
    for _ in range(14):
        mid = (lo + hi) / 2
        if joined(w >= mid, a, b): lo = mid
        else: hi = mid
    return lo


def widths(cu, terms, res):
    """{(name, name): width} for every pair of (name, mask) terminals"""
    w = width_map(cu, [m for _, m in terms], res)
    return {(a, b): round(bottleneck(w, ma, mb), 2) for (a, ma), (b, mb) in itertools.combinations(terms, 2)}


def spines(cu, terms, res, keep_w=0.8, max_w=2.0):
    """the cells to keep: the widest path, at keep_w of its own width (at most max_w), along a maximum spanning tree
    of the terminals: a notch may take the rest (pourcheck.py flags a pair that lost more than 20 %)"""
    w = width_map(cu, [m for _, m in terms], res)
    pairs = sorted(((bottleneck(w, ma, mb), i, j) for (i, (_, ma)), (j, (_, mb)) in
                    itertools.combinations(enumerate(terms), 2)), reverse=True)
    parent = list(range(len(terms)))
    def find(a):
        while parent[a] != a: a = parent[a]
        return a
    keep = np.zeros(cu.shape, bool); edges = []
    for bn, i, j in pairs:
        if bn <= 0 or find(i) == find(j): continue
        parent[find(i)] = find(j)
        if bn >= 10: continue                    # the terminals touch: nothing to keep
        path = _bfs(w >= bn - 1e-6, terms[i][1], terms[j][1])
        if not path.any(): continue
        r = min(bn * keep_w, max_w) / 2 / res
        keep |= (ndimage.distance_transform_edt(~path) <= r + 0.5) & cu
        edges.append((terms[i][0], terms[j][0], round(bn, 2)))
    return keep, edges


@njit(cache=True)
def _bfs_core(m, src, dst):
    H, W = m.shape
    prev = np.full(H * W, -1, np.int64); seen = np.zeros(H * W, np.bool_)
    q = np.empty(H * W, np.int64); qh = 0; qt = 0
    for y in range(H):
        for x in range(W):
            if src[y, x] and m[y, x]:
                seen[y * W + x] = True; q[qt] = y * W + x; qt += 1
    end = -1
    while qh < qt:
        s = q[qh]; qh += 1; y = s // W; x = s % W
        if dst[y, x]: end = s; break
        for dy in range(-1, 2):
            for dx in range(-1, 2):
                ny = y + dy; nx = x + dx
                if (dy or dx) and 0 <= ny < H and 0 <= nx < W and m[ny, nx] and not seen[ny * W + nx]:
                    seen[ny * W + nx] = True; prev[ny * W + nx] = s; q[qt] = ny * W + nx; qt += 1
    out = np.zeros((H, W), np.bool_)
    while end >= 0:
        out[end // W, end % W] = True; end = prev[end]
    return out


def _bfs(m, a, b):
    """cells of a shortest 8-connected path through m from a cell of a to a cell of b (none: all False)"""
    return _bfs_core(m, a, b)
