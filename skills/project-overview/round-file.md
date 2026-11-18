# The round file

`docs/overview/r<N>.toml`, one per design round, read by `pcbkit overview`. `pcbkit overview new` writes the next
one; `pcbkit overview --check` says what is wrong with it. Texts take `` `code` ``, `**bold**` and
`[a link](DESIGN.md)`; a blank line starts a paragraph.

## The round

| Key | What it is |
| --- | --- |
| `date` | the day the round stood as written (`2026-11-16`, no quotes) |
| `title`, `about` | the project's name, and one sentence on what it is; the page's heading takes the newest round's |
| `label` | the round's name in the round switch, two to four words: "First order", "Review 2, release" |
| `mark`, `state` | the project's status: one of the six marks and a few words |
| `summary` | two or three sentences: where the project stands and why |
| `changes` | what this round did, a line each, citing decisions (D41) and findings (F4) |
| `next` | the next steps in order, the user's first |
| `links` | the documents behind the round, as sources (below) |

## Its tables

Each `[[...]]` may appear several times. An `id` is unique in the round: a note names its item by it, and the
page's links (`overview.html#r3-power`) use it. A board's is its PCB's file name as the project has it; every other
is lower-case letters, digits and hyphens.

| Table | Keys | Notes |
| --- | --- | --- |
| `[[board]]` | `id`, `version`, `mark`, `state`, `text`, `source` | `id` is the board's file name without `.kicad_pcb`, as it is: `controller_v2`, `Main` |
| `[[topic]]` | `id`, `title`, `mark`, `state`, `text`, `picture`, `source` | a block or a subject: power, the MCU, the case, firmware, bring-up. `picture` is a file of the project. A topic whose words changed since the round before is tagged "changed", a new id "new" |
| `[[decision]]` | `id`, `ref`, `question`, `text`, `blocks`, `recommend`, `why`, `source`; `mark`, `state` | `ref` is its number in the project (O9, Q3); `blocks` what waits on it; `recommend` the id of an option; `why` in a line. `mark` only when it is not the user's to take yet (`blocked`), and `state` when there is a word to go with it: "waits for the range test", "asked again: the case changed" |
| `[[decision.option]]` | `id`, `text`, `effect`, `cost` | two to four after each decision |
| `[[decided]]` | `ref`, `text`, `source` | a decision taken in this round, by its number in DESIGN.md |
| `[[fact]]` | `id`, `mark`, `state`, `value`, `text`, `date`, `source` | `id` is `cost`, `supply`, `fab` or `orders`; `value` the number or phrase; `date` when it was taken |
| `[[view]]` | `board`, `show` | a board's tab in the viewer; `show = ["turn"]` or `["layers"]` for one of the two |
| `[[view]]` | `name`, then `[[view.picture]]` with `file`, `label` | a tab of the project's own pictures |
| `[[note]]` | `date`, `on`, `mark`, `state`, `chose`, `text` | written by `pcbkit overview note`; `on` is an id (default: the round's status) |

**A source** is a file of the project, then what to look for in it: `"DESIGN.md D25"`, `"PRODUCTION.md Costs"`,
`"docs/reviews/2026-11-16-controller.md Controller review"`. A list gives several. The page links the file; a
document at the project's root is shown by its name and the place ("DESIGN.md D25"), a file deeper in by its label
("Controller review"). When the place is a number of the project's own (D25, O9, F4), `--check` looks for it in the
file. `[overview] base = "https://github.com/<user>/<repo>/blob/main"` in pcbkit.toml makes the links go there, where
Markdown is rendered, instead of to the files.

## A round, shortened

From the message sign's second round (2026-11-16):

```toml
date = 2026-11-16
title = "RP2354 LED message sign"
about = "Orange 0402 LED character modules on one bus, driven by an RP2354A controller that is also their power hub."
label = "First order"
mark = "decision"
state = "order in the cart, unpaid"
summary = """
5 controllers (v1.0.4) and 5 single 5×7 modules (v1.0.1) are in the JLC cart, the front assembled by JLC, and \
the parts to solder by hand are in an LCSC cart. Both boards were failure-reviewed, and every fix you chose \
is in."""
changes = [
  "Failure reviews of the 5×7 (its first) and of the controller since 10-21; you chose all their fixes (D16–D21)",
  "Every via drilled at 0.3 mm: JLC prices 0.25 mm as high precision (D23)",
  "U7 is TPS259472A after the TPS259470A ran out (D25); port PTCs MF-MSMF150 (D15)",
]
next = ["Pay JLC (Y177 + Y171) after a last stock check, and the LCSC cart.", "Route the 6×8's 44 signal connections by hand."]
links = ["HANDOVER.md", "docs/reviews/2026-11-16-controller.md Controller review"]

[[board]]
id = "controller"
version = "v1.0.4"
mark = "decision"
state = "in the cart, unpaid"
text = "Released, reviewed twice, DRC 0/0/0, ERC 0. JLC Y177, $125.55 for 5."
source = "releases/controller/releases.toml"

[[topic]]
id = "power"
title = "Power in and the ports"
mark = "attention"
state = "one weakness to decide"
text = """
Barrel in through an eFuse (U7, TPS259472A: clamps at 5.7 V, auto-retry), four fused module ports with \
presence sense, USB-C for flashing only. A hard short on one port darkens the whole sign, because U7 limits \
before the PTC trips (O4)."""
source = ["DESIGN.md D25", "docs/reviews/2026-11-16-controller.md Controller review"]

[[decision]]
id = "short"
ref = "O4"
question = "A hard port short darkens the whole sign: accept it?"
recommend = "accept"
why = "It damages nothing, and a prototype of five can hold it as a bench rule."
source = "DESIGN.md O4"

[[decision.option]]
id = "accept"
text = "Accept for this build"
effect = "per-port switching noted for the next revision"
cost = "none"

[[decision.option]]
id = "switch"
text = "Per-port switching now"
effect = "a v1.1 of the controller before ordering"
cost = "a new layout, review and quote"

[[decided]]
ref = "D25"
text = "Controller eFuse U7 becomes TPS259472A; R39–R41 and C42 are not fitted."
source = "DESIGN.md D25"

[[fact]]
id = "supply"
mark = "attention"
state = "thin, enough for 5"
value = "RP2354A: 237 at JLC"
text = "The 12 MHz crystal 268, U7 (TPS259472A) 7,467. Check all three again before paying."
date = 2026-11-16
source = "PRODUCTION.md Costs"

[[view]]
board = "controller"
```

A tab of the project's own pictures, from Orbitchron's fourth round:

```toml
[[view]]
name = "Stack and case"

[[view.picture]]
file = "docs/previews/stack-exploded.png"
label = "Exploded"

[[view.picture]]
file = "docs/previews/case-section.png"
label = "Case section"
```

## What the command keeps beside the rounds

`docs/overview/img/index.toml` says which pictures each round uses and holds a seal on each earlier round: the
hash of its file without the notes, and a hash of each note it had at the last build, so notes are added after
those and none of them is changed. Don't edit it. A board's pictures are in `img/<board>-<hash>/` under the hash
of its board file, so two rounds of an unchanged board share one set; a named picture is copied in as
`img/<hash>-<name>`, as it was that day. A build and `--check` stop when a picture a round was built with is no
longer there: restore `img/` as it was committed. If an earlier round has to be corrected after all, **with the
user's word**, delete its `seal` line (its `notes` line for a note); the next build seals it again.
