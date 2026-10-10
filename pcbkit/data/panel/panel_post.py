"""KiKit post-processing script for the JLC panels (scripts/panel/jlc_vcut_3high.json).

1. Unique references. JLC rejects a panel BOM with repeated designators ("Repeated designator"), so every reference
   gets the number of its module copy, counted from the top: D1 becomes D1_1, D1_2, D1_3. BOM and CPL use these names.
   A reference printed on the silkscreen is first copied to plain text with its original name, and the field is
   hidden, so each module still prints "C102". Fab-layer references show the panel names.
2. Order number. Each module carries its own "JLCJLCJLCJLC" (for single-board orders). In a panel the rail placeholder
   takes over, so the copies inside the modules are removed. arg = rail width in mm: placeholders within it of the
   panel's top or bottom edge stay.
"""
import pcbnew

PLACEHOLDER = "JLCJLCJLCJLC"
SILK = (pcbnew.F_SilkS, pcbnew.B_SilkS)


def kikitPostprocess(panel, arg):
    b = panel.board
    # ---- references: <orig>_<copy>, copies numbered top to bottom ------------------------------------------
    boxes = sorted((s.bounds() for s in panel.substrates), key=lambda r: (r[1], r[0]))   # (minx, miny, maxx, maxy)
    for fp in b.GetFootprints():
        p = fp.GetPosition()
        copy = next((i + 1 for i, r in enumerate(boxes) if r[0] <= p.x <= r[2] and r[1] <= p.y <= r[3]), None)
        if copy is None:
            continue                                   # rail parts (fiducials, tooling holes)
        ref = fp.Reference()
        if ref.IsVisible() and ref.GetLayer() in SILK:
            t = pcbnew.PCB_TEXT(b)
            t.SetAttributes(ref)
            t.SetText(ref.GetText()); t.SetPosition(ref.GetPosition()); t.SetLayer(ref.GetLayer())
            b.Add(t)
            ref.SetVisible(False)
        ref.SetText(f"{ref.GetText()}_{copy}")
    # ---- order number: keep the rail placeholder only --------------------------------------------------------
    rail = pcbnew.FromMM(float(arg or 5))
    _, top, _, bottom = panel.boardSubstrate.bounds()     # the outline is only written when the panel is saved
    texts = [t for t in b.GetDrawings() if t.GetClass() == "PCB_TEXT" and t.GetText() == PLACEHOLDER]
    keep = [t for t in texts if 0 <= t.GetPosition().y - top < rail or 0 <= bottom - t.GetPosition().y < rail]
    if not keep:
        raise RuntimeError("panel_post.py: no order-number placeholder on the rails")
    for t in texts:
        if not any(t == k for k in keep):
            b.Remove(t)
