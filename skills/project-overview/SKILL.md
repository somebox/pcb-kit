---
name: project-overview
description: "Make and update a hardware project's overview page, docs/overview.html: the whole project on one page for the user, with its status, a viewer of each board (turned by drag, or layer by layer), a few lines per block, the decisions waiting for the user with their options and a recommendation, cost, supply, fab and orders, and every design round kept. Use when a major design round is done (a review and its decisions, a re-layout, a release, an order), when the user asks where the project stands or asks for the overview, status or review page, and when a small change needs noting on that page. Not for PRODUCTION.md's generated overview (pcbkit production) or the one-sheet design picture (the visual-review skill)."
---

# The project's overview page

`docs/overview.html` is the project as the user reads it between other things: what state it is in, what waits for
them, what each board looks like, where the details are. `pcbkit overview` makes it from one small file per design
round, `docs/overview/r1.toml`, `r2.toml`, and so on. You write the round file; the command draws the boards, lays
out the page and keeps the earlier rounds as they were. This kit's `docs/CONVENTIONS.md`, "The overview page", says
what the page is for.

Worked example: the pages first built for Orbitchron (four rounds, three boards) and the message sign (two rounds,
three boards), 2026-11-17. Orbitchron's page came to 69 kB of HTML and 4.5 MB of pictures: about 2 MB for each
540-part board, since a board unchanged between rounds is drawn once. Rendering the three boards took 28 s. On the
sign's page, the day after its round, three notes turned the order from "in the cart, unpaid" to "controller waits
for RP2354As" (the part had sold out), with nothing in the round rewritten.

## The pattern

Three pages built by hand in their projects on 2026-11-17 show what such a page is, whatever makes it:
Orbitchron's `docs/overview.html` before its first order, DodecaRGB's control board v1.3 order review, and the RGB
spotlight's lamp housing review at round 7. This kit's README shows all three. What they share:

- **The top says where it stands, without scrolling.** A title that names the thing and the moment ("Control board
  v1.3, before the JLC order"), two or three sentences, the state in a few chips, three or four numbers (size,
  parts, cost per board, open findings) and a picture of the thing.
- **What changed since the user last looked comes next.** The spotlight's first panel is "Since the failure
  review": each number with its value at the review struck through beside it, then a table of part, then → now,
  and why.
- **A viewer the user can work.** Orbitchron's shows each board as a render, layer by layer or as an assembly map,
  front and back. The spotlight's turns the housing in 3-D with a button per round, so two rounds compare in place.
- **One block at a time.** DodecaRGB's has a tab per block: a map of where the block sits, close-ups of its copper,
  and that block's findings and decisions as cards.
- **Decisions sorted by whose move it is**: for the user, decided this round, still open, carried from an earlier
  round. Each has its options and the one recommended.
- **Every card cites its source**: the finding's or decision's number (F29, D30) as a link, and the file it is in.
- **One set of status chips**, explained once and used on every card.
- **Rounds stay.** Tabs along the top (Orbitchron) or a switch in the viewer (the spotlight); the page says which
  round it shows and when it was last updated.

`pcbkit overview` makes a page to this pattern from the round files. A page built by hand for one round's question
(an order checked line by line, a housing in 3-D) follows the same pattern.

## What is where

```
docs/overview.html         the page: generated, committed, never edited by hand
docs/overview/r<N>.toml    one file per round, which you write (the fields: round-file.md, beside this file)
docs/overview/img/         the rounds' pictures as they were when each round was first built, and index.toml
```

**A round file is the briefing, never the record.** It holds what the user needs on one page: a status line, the
boards, a few lines per topic, the open decisions with their options, the numbers that matter. Every item names
its `source` in the project, and the page links it. A decision the user takes goes into DESIGN.md as a numbered
decision, a price into PRODUCTION.md or BOM.md, an order event into its order.toml, as the other skills say. The
page then gets a note that cites it.

## Making a round

1. `pcbkit overview new` writes the next `r<N>.toml`. Round 1 is a skeleton with the project's boards. A later
   round starts as the one before stands now: the marks and states its notes left, the decisions still open,
   without the notes. A board released since then gets the version its `releases.toml` records.
2. Read what the project recorded since the last round, then write the round. Where each part comes from:

   | Part of the page | Read | Kept by |
   | --- | --- | --- |
   | status, what is next | the status line under each document's title (README, DESIGN, LAYOUT, PRODUCTION); HANDOVER.md where the project has one | the pcb-kit skill's rules |
   | boards: version, state | `releases/<board>/releases.toml`, PRODUCTION.md's generated overview, or the CHANGELOG where there are no releases | `pcbkit release`, `pcbkit production` |
   | what this round did, what was decided | DESIGN.md's decisions dated since the last round; the review's Decisions table; CHANGELOG.md | failure-review, pcb-kit |
   | topics | DESIGN.md's block designs, LAYOUT.md, FIRMWARE.md, the reviews' verdicts and bench lists | failure-review, power-sim, enclosure-fit |
   | decisions for the user | DESIGN.md's open questions, the newest review's "Questions for the user", HANDOVER.md | failure-review |
   | cost, supply | BOM.md (cost drivers, stock risks), PRODUCTION.md's Costs | bom-cost |
   | fab, orders | the order guide in PRODUCTION.md, `production/orders/*/order.toml`, the quotes | fab-order |
   | pictures of the product | `docs/previews/`: exploded views, sections, photos | visual-review |

3. `pcbkit overview --check` until it prints no `problem:` line. A problem is something wrong with a round file,
   and it stops the page: a mark that is not one of the six, a decision without two options and a recommendation,
   a source that is not in the project or does not hold the number named (`DESIGN.md D49`), an earlier round that
   changed. A `watch:` line asks for a look: a text over its limit, a board version that differs from the records.
   A `to build:` line (the round's pictures are not made yet, the page is not what the rounds say) is what the
   next step does: before the first build of a round the check ends with those, and exits 1.
4. `pcbkit overview` builds the page. It renders each board's turntable and layers the first time a round shows
   them (`kicad-cli`, `rsvg-convert`; `cwebp` from `brew install webp` keeps a board near 1 to 2 MB, and without it
   the pictures are JPEG and PNG and several times larger).
   - A new round draws a board again when its file or one of the project's own 3-D models it uses
     (`${KIPRJMOD}/...`) changed, and reuses the earlier pictures when neither did.
   - `--render` draws the newest round's again whatever is there: for a change the board file doesn't show (one
     of KiCad's own models, a KiCad upgrade, a render that came out wrong), or after the board changed while the
     round was being written. An earlier round keeps the pictures it had.
   - A `watch:` line names a layer or a side of the turntable that could not be drawn, and why (`rsvg-convert`
     missing, a render that failed). The viewer looks complete without it, so `--check` keeps saying it: put it
     right, then `--render`.
   - `--no-render` uses the stills `pcbkit previews` left. It refuses stills whose `SOURCE.txt` is of another
     version of the board, and says so on the page when they don't record which board they show.
5. Look at the page before the user does: open it, switch rounds, turn a board, switch its layers, read it at
   phone width and in the dark theme. What reads long is long.
6. Show it. `pcbkit overview --single build/overview.html` writes one file with its pictures inside, to send with
   the harness's file tool or to publish; the page in `docs/` links its pictures and needs its folder.
7. `pcbkit overview --check` once more. Now it must print no `problem:` and no `to build:` line and exit 0: the
   page is what the round files say. Then commit `docs/overview.html` and `docs/overview/` together.

## A new round, or a note

| What happened | Do |
| --- | --- |
| a review and its decisions, a re-layout, a release, an order placed or received, a bring-up | a new round |
| a price or a stock figure moved; a part was swapped in the cart; a decision was taken; a test came back | a note |
| a typo or a wrong number in the newest round, before the user has seen it | edit that round's file |
| anything in an earlier round, a note of it included | a new note on it (`--round N`); `--check` refuses an earlier round that was edited, and one whose notes were changed, removed or reordered |

```bash
pcbkit overview note "The RP2354A is gone: 0 at JLC and at LCSC." --on supply --mark blocked --state "RP2354A out of stock"
pcbkit overview note "Decided: u.FL (D26)." --on antenna --chose b
pcbkit overview note "Paid: JLC W20261118." --mark work --state "boards ordered"
```

A note is dated and stays under its item; the page lists the round's notes under its status as "Added since". The
item shows the mark and state of its latest note, so a note that changes the mark says the new state too. `--on` is
the id of a board, topic, decision or fact; without it the note is on the round's status. A note that takes a
decision names the option with `--chose` and cites the decision's number in DESIGN.md, where it was written first.
More than about eight notes on a round is a new round.

## The six marks

Every status on the page is one of six marks followed by the project's own word for the state. The page explains
them once, under its title.

| Mark | Means | The state beside it reads like |
| --- | --- | --- |
| `done` | finished and checked | released, reviewed, measured, received |
| `work` | someone is on it, the fab included | routed, in review, ordered, in production, waits for the bench |
| `todo` | not started | not started, waits for the boards |
| `attention` | it works or may work, with a risk or a check open | a bench test settles it, thin stock, one accepted weakness |
| `blocked` | it cannot go on until something else changes | out of stock, can't be paid yet, fails DRC |
| `decision` | it waits for the user | in the cart, unpaid; cleared to pay |

- The mark is the kind of state, the word is the state. "In the cart, unpaid" is `decision`, because the next move
  is the user's; "ordered" is `work`.
- An open decision needs no mark: it shows `decision` until a note chooses an option (`--chose`) or marks it
  `done`. One that has to wait can say so, in the round file or by a note (`--on antenna --mark blocked --state
  "waits for the range test"`): it stays among the decisions, and the next round starts with it as it stands.
- A decision that was taken stays taken. A later note may mark it `blocked` or `attention` (the part chosen is out
  of stock): that shows beside the choice and does not ask again. Only a note that marks it `decision` gives it
  back to the user, without the choice.
- Do not invent a seventh mark or use a mark as decoration. If two marks fit, take the one that tells the user
  whether they have to act.

## Keeping it brief

The page is read in a minute or it is not read. `--check` watches these, and the page shows why they are right:

- The round's `state` is a few words (30 characters); the `summary` two or three sentences (320).
- `changes`: the five to seven things this round did, a line each, citing the decisions (D41) and findings (F4).
- At most ten topics, each two to four lines (420 characters): what it is, where it stands, what is left. The
  reasoning stays in DESIGN.md and the review, behind the `source` link.
- A decision is a question, what waits on it, two to four options with their effect and cost, and the one you
  recommend with one line of why. A question without options is not ready for the page: work the options out
  first, in the review or DESIGN.md's open questions.
- A fact (cost, supply, fab, orders) is one number or phrase, a sentence, and the date it was taken.
- A note is a line or two (240 characters).

## The viewer

A `[[view]]` with `board = "<name>"` gives that board a tab: **Turn**, the board turning on its own axis, front and
back, by drag, arrow keys or the slider; and **Layers**, each copper, silkscreen and mask layer on its own, to
switch on and off, from the front or mirrored from the back, with zoom. A view with a `name` and
`[[view.picture]]` entries shows the project's own pictures: the product, an exploded stack, a section, a photo.
Make those with the visual-review skill and keep each under about 300 kB (`cwebp`, or `sips -Z 1600`), since every
round keeps its own copy.

## Rules

- Never edit `docs/overview.html` or `docs/overview/img/`: the round files are the page.
- An earlier round changes only by notes. What the page said on a date stays what it said.
- Nothing is on the page that is not in the project's documents first. If a fact has no source, write it where it
  belongs, then cite it.
- Words the user will act on are exact: cart lines, prices with their date, part numbers, versions as released.
- Check, build, check: `--check` before a build for what is wrong with the round, and after it, before the
  commit, where it must be silent but for watch lines. A `to build:` line then means the page was not rebuilt.
