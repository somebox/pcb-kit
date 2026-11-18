# Comparing layout options: a worked example

[via-options-tps55288.webp](../../docs/images/via-options-tps55288.webp): siebenuhr controller v3, 2026-11-09. U5 is
a TPS55288 buck-boost in TI's HotRod package, with three long strip pads under it. Five of the board's eight vias
there were open holes inside the strips, under paste: a 0.2 mm hole through the 1.6 mm board holds 0.050 mm³, almost
twice one strip's whole paste deposit (0.027 mm³). JLC can't plug a via with solder-mask ink when it is in a pad or
within 0.35 mm of one. It can fill such a via with epoxy or copper and cap it, which its page lists as the complex,
costly process, with epoxy filling free on boards of six layers or more
([Via Covering](https://jlcpcb.com/help/article/pcb-via-covering), read 2026-11-16). TI's figure and EVM put the vias
beside the strips. A search of the board for free spots (the via's copper 0.16 mm from other nets on every layer and
0.02 mm from every mask opening, holes 0.25 mm apart, on its own net's copper on both layers it joins) found three
of U5's corners and, with 0.007 mm to spare, TI's side spots. Option 2 uses both: no hole under paste, 95 % of the
VOUT via copper kept, PGND's doubled. The sheet also shows what the study's first picture left out, since it drew no
tracks: Option 1's copy kept the tracks that led to two of the vias it removed.

```bash
pcbkit netview before/controller.kicad_pcb u5-options.svg 147.55 126.7 152.05 131.2 --layer F.Cu --detail \
  --colour LED_VOUT=#e8892b --colour GND=#6b8fb8 --colour CONV_SW2=#c94a4a \
  --ref "ti-fig10-1.png=TI datasheet Fig. 10-1, turned to match" --ref-rotate 90 \
  --title "Before (e0afc51): 8 vias, 5 in the pads" \
  --compare "v1/controller.kicad_pcb=Option 1, corners: 5 vias, none in pads" \
  --compare "v2/controller.kicad_pcb=Option 2, corners + TI's side vias: 9 vias" \
  --metrics LED_VOUT,GND --note "Both options: DRC 0 errors, 0 unconnected." --png u5-options.png
```
