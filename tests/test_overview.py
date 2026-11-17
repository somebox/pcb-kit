"""`pcbkit overview`: the project's page from its round files, on a project written here as text.

  python3 tests/test_overview.py     # plain Python: nothing here needs KiCad

The project has one board, a DESIGN.md with a decision and an open question, and a picture. A fake renderer stands
in for kicad-cli, so the tests cover what the page says and what is kept, not how a board is drawn.
"""
import copy
import datetime
import contextlib
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import warnings
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from pcbkit import cli, overview, production, tomlw  # noqa: E402
from pcbkit.config import Project  # noqa: E402
from pcbkit.release import versions  # noqa: E402

PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa75\x81\x84\x00\x00\x00\x00IEND\xaeB`\x82")
D = datetime.date


def write(path, text, mode="w"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode) as fh:
        fh.write(text)


def base():
    """A round that has one of everything."""
    return dict(
        date=D(2026, 11, 16), title="Demo sign", about="A sign.", label="First order", mark="decision",
        state="in the cart, unpaid", summary="Both boards are released.", changes=["Vias at 0.3 mm (D1)"],
        next=["Pay the order."], links=["DESIGN.md"],
        board=[dict(id="demo", version="v1.0", mark="done", state="released", text="5 in the cart.", source="DESIGN.md D1")],
        topic=[dict(id="power", title="Power", mark="work", state="waits for the bench", text="An eFuse, then a buck.",
                    picture="docs/pic.png", source=["DESIGN.md D1", "docs/pic.png The picture"])],
        decision=[dict(id="antenna", ref="O2", question="Which antenna?", blocks="the case", recommend="a",
                       why="It is measured.", source="DESIGN.md O2",
                       option=[dict(id="a", text="On the board", effect="no change", cost="none"),
                               dict(id="b", text="A u.FL one", effect="a cable", cost="$0.60")])],
        decided=[dict(ref="D1", text="Vias at 0.3 mm.", source="DESIGN.md D1")],
        fact=[dict(id="cost", mark="done", state="quoted", value="$20 a set", text="At 5.", date=D(2026, 11, 16), source="DESIGN.md")],
        view=[dict(board="demo"), dict(name="Product", picture=[dict(file="docs/pic.png", label="Front")])])


def fake_renderer(calls):
    def render(pcb, dest, cfg):
        calls.append(pcb)
        for name in ("turn-top-00.webp", "turn-top-01.webp", "turn-bottom-00.webp", "layer-F_Cu.webp", "layer-Edge_Cuts.webp"):
            write(os.path.join(dest, name), PNG, "wb")
        return dict(made="test", mm=[10.0, 5.0], turn=dict(top=["turn-top-00.webp", "turn-top-01.webp"], bottom=["turn-bottom-00.webp"]),
                    layers=[dict(id="F.Cu", file="layer-F_Cu.webp"), dict(id="Edge.Cuts", file="layer-Edge_Cuts.webp")])
    return render


def layers_only(pcb, dest, cfg):
    for name in ("layer-F_Mask.webp", "layer-F_Cu.webp", "layer-B_Cu.webp", "layer-Edge_Cuts.webp"):
        write(os.path.join(dest, name), PNG, "wb")
    return dict(made="test", layers=[dict(id="F.Mask", file="layer-F_Mask.webp"), dict(id="F.Cu", file="layer-F_Cu.webp"),
                                     dict(id="B.Cu", file="layer-B_Cu.webp"), dict(id="Edge.Cuts", file="layer-Edge_Cuts.webp")])


class Case(unittest.TestCase):
    def setUp(self):
        warnings.filterwarnings("ignore", "unclosed file", ResourceWarning)    # production.survey reads boards so
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self.tmp.name)
        write(os.path.join(self.root, "pcbkit.toml"), "")
        write(os.path.join(self.root, "README.md"), "# Demo sign\n\nStatus (2026-11-16): ordered.\n")
        write(os.path.join(self.root, "DESIGN.md"), "| D1 | 2026-11-16 | Vias at 0.3 mm | cost |\n\n- **O2 Antenna.** On the board, or u.FL?\n")
        write(os.path.join(self.root, "boards", "demo", "demo.kicad_pcb"),
              '(kicad_pcb (version 20260206) (title_block (title "Demo") (rev "v1.0")) (footprint "R_0402"))\n')
        write(os.path.join(self.root, "docs", "pic.png"), PNG, "wb")
        self.project = Project(self.root)
        self.P = overview.paths(self.project)
        self.calls, self.said = [], []

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, n, rnd):
        write(os.path.join(self.P["dir"], "r%d.toml" % n), overview.write_round(rnd))

    def build(self, **kw):
        self.said = []
        return overview.build(self.project, say=self.said.append, renderer=fake_renderer(self.calls), **kw)

    def check(self):
        rounds = overview.load_rounds(self.P["dir"])
        return overview.check_rounds(rounds, self.P, self.project, overview.load_index(self.P))

    def page(self):
        with open(self.P["page"], encoding="utf-8") as fh:
            return fh.read()

    def problems(self, change):
        """The problems of the base round after CHANGE has edited it."""
        rnd = base()
        change(rnd)
        self.put(1, rnd)
        return "\n".join(self.check()[0])


class Rounds(Case):
    def test_order_is_by_number(self):
        for n in (1, 2, 10, 9):
            self.put(n, base())
        self.assertEqual([r["_n"] for r in overview.load_rounds(self.P["dir"])], [1, 2, 9, 10])

    def test_a_gap_is_a_problem(self):
        self.put(1, base())
        self.put(3, base())
        self.assertIn("no gaps", "\n".join(self.check()[0]))

    def test_the_base_round_is_clean(self):
        self.put(1, base())
        problems, watch = self.check()
        self.assertEqual((problems, watch), ([], []))

    def test_what_stops_a_build(self):
        cases = [
            (lambda r: r.update(date="2026-11-16"), "is not a date"),
            (lambda r: r.update(label=""), "label is missing"),
            (lambda r: r.update(mark="ok"), "mark 'ok' is not one of"),
            (lambda r: r["topic"][0].update(mark="waiting"), "mark 'waiting'"),
            (lambda r: r["topic"][0].update(id="Power"), "lower-case"),
            (lambda r: r["topic"][0].update(id="demo"), "used twice"),
            (lambda r: r["topic"][0].update(text=""), "needs a title and a text"),
            (lambda r: r["fact"][0].update(id="price"), "is not one of cost"),
            (lambda r: r["fact"][0].pop("date"), "the date it was taken"),
            (lambda r: r["decision"][0].update(option=r["decision"][0]["option"][:1]), "two or more options"),
            (lambda r: r["decision"][0].update(recommend="c"), "needs recommend"),
            (lambda r: r["decision"][0].update(why=""), "needs recommend"),
            (lambda r: r["topic"][0].update(source="NOTES.md"), "no NOTES.md in the project"),
            (lambda r: r["decided"][0].update(source="DESIGN.md D9"), "DESIGN.md has no D9"),
            (lambda r: r["topic"][0].update(picture="docs/none.png"), "docs/none.png: not in the project"),
            (lambda r: r["view"][0].update(board="other"), "no board 'other'"),
            (lambda r: r["view"][1].pop("picture"), "a view is a board"),
            (lambda r: r.update(note=[dict(date=D(2026, 11, 17), on="nothing", text="x")]), "which is no id in this round"),
            (lambda r: r.update(note=[dict(date=D(2026, 11, 17), on="antenna", chose="c", text="x")]), "not one of its options"),
            (lambda r: r.update(note=[dict(on="power", text="x")]), "needs a date and a text"),
            (lambda r: r.update(note=[dict(date=D(2026, 11, 17), on="power", mark="fine", text="x")]), "mark 'fine'"),
        ]
        for change, want in cases:
            with self.subTest(want=want):
                self.assertIn(want, self.problems(change))

    def test_a_problem_writes_no_page(self):
        self.put(1, dict(base(), mark="ok"))
        self.assertEqual(self.build(), 1)
        self.assertFalse(os.path.exists(self.P["page"]))
        self.assertTrue(any(line.startswith("problem: ") for line in self.said))

    def test_what_only_wants_a_look(self):
        cases = [
            (lambda r: r.update(summary="word " * 80), "summary is 399 characters"),
            (lambda r: r["topic"][0].update(text="word " * 100), "topic 'power' is 499 characters"),
            (lambda r: r.update(state="a state that goes on and on for far too long"), "is long"),
            (lambda r: r.update(topic=[dict(r["topic"][0], id="t%d" % i) for i in range(11)]), "11 topics"),
            (lambda r: r.update(next=["step"] * 6), "6 next steps"),
            (lambda r: r.update(changes=["change"] * 8), "8 changes listed"),
            (lambda r: r["topic"][0].pop("source"), "names no source"),
            (lambda r: r.update(mark="done"), "marked done with 1 decision(s) open"),
            (lambda r: r["board"][0].update(version="v0.9"), "is v0.9 here and v1.0 in boards/demo/demo.kicad_pcb"),
            (lambda r: r.update(note=[dict(date=D(2026, 11, 17), on="power", text="word " * 60)]), "a note is a line or two"),
            (lambda r: r.update(note=[dict(date=D(2026, 11, 17), on="power", text="n")] * 9), "9 notes"),
        ]
        for change, want in cases:
            with self.subTest(want=want):
                rnd = base()
                change(rnd)
                self.put(1, rnd)
                problems, watch = self.check()
                self.assertEqual(problems, [])
                self.assertIn(want, "\n".join(watch))


class Standing(Case):
    """An item's mark and state after its notes."""

    def rnd(self, *notes):
        r = base()
        r["note"] = [dict(dict(date=D(2026, 11, 17)), **n) for n in notes]
        return r

    def test_without_notes_it_is_its_own(self):
        self.assertEqual(overview.effective(base(), "power"), ("work", "", "waits for the bench"))
        self.assertEqual(overview.effective(base(), "status"), ("decision", "", "in the cart, unpaid"))

    def test_the_latest_note_with_a_mark_wins(self):
        r = self.rnd(dict(on="power", mark="attention", state="rings at 8 V", text="a"), dict(on="power", text="b"),
                     dict(on="power", date=D(2026, 11, 19), mark="done", state="damped", text="c"))
        self.assertEqual(overview.effective(r, "power"), ("done", "", "damped"))

    def test_a_new_mark_without_a_state_drops_the_old_word(self):
        r = self.rnd(dict(on="status", mark="blocked", text="No stock."))
        self.assertEqual(overview.effective(r, "status"), ("blocked", "", ""))

    def test_a_decision_is_the_users_until_a_note_closes_it(self):
        self.assertEqual(overview.effective(base(), "antenna")[0], "decision")
        self.assertEqual(overview.effective(self.rnd(dict(on="antenna", text="Thinking.")), "antenna")[0], "decision")
        self.assertEqual(overview.effective(self.rnd(dict(on="antenna", chose="b", text="Decided (D2).")), "antenna")[:2], ("done", "b"))
        self.assertEqual(overview.effective(self.rnd(dict(on="antenna", mark="done", text="Dropped.")), "antenna")[:2], ("done", ""))

    def test_a_decision_that_waits_is_still_open(self):
        waiting = dict(on="antenna", mark="blocked", state="waits for the range test", text="Measure first.")
        r = self.rnd(waiting)
        self.assertEqual(overview.effective(r, "antenna"), ("blocked", "", "waits for the range test"))
        self.assertEqual([d["id"] for d in overview.open_decisions(r)], ["antenna"])
        for mark in ("work", "attention", "todo"):
            self.assertEqual(len(overview.open_decisions(self.rnd(dict(on="antenna", mark=mark, text="Later.")))), 1)
        self.assertEqual(overview.open_decisions(self.rnd(waiting, dict(on="antenna", date=D(2026, 11, 20), chose="b", text="D2."))), [])
        self.put(1, r)
        self.assertEqual(self.build(), 0, self.said)
        page = self.page()
        self.assertIn("1 decision for you", page)
        decision = page[page.index('id="r1-antenna"'):page.index("</article>", page.index('id="r1-antenna"'))]
        self.assertIn('<article class="decision" id="r1-antenna">', page)
        self.assertIn('<span class="sr">Blocked: </span>waits for the range test', decision)

    def test_a_choice_stands_whatever_a_later_note_marks_it(self):
        r = self.rnd(dict(on="antenna", chose="b", text="Decided: u.FL (D2)."),
                     dict(on="antenna", date=D(2026, 11, 20), mark="blocked", state="u.FL out of stock", text="None at LCSC."))
        self.assertEqual(overview.effective(r, "antenna"), ("blocked", "b", "u.FL out of stock"))
        self.assertTrue(overview.decided(r, "antenna"))
        self.assertEqual(overview.open_decisions(r), [])                # taken: not a question any more
        self.put(1, r)
        self.assertEqual(self.build(), 0, self.said)
        page = self.page()
        self.assertNotIn("decision for you", page)
        decision = page[page.index('id="r1-antenna"') - 40:page.index("</article>", page.index('id="r1-antenna"'))]
        self.assertIn('class="decision closed"', decision)
        self.assertIn('<span class="flag chosen">Chosen</span>A u.FL one', decision)
        self.assertIn('<span class="sr">Blocked: </span>u.FL out of stock', decision)   # what stands in the way of doing it
        rnd = tomlw.load(overview.new_round(self.project, self.P, today=D(2026, 12, 1)))
        self.assertNotIn("decision", rnd)                              # and the next round does not ask it again

    def test_a_decision_in_a_round_file_can_say_what_it_waits_for_but_not_that_it_is_done(self):
        r = base()
        r["decision"][0].update(mark="blocked", state="waits for the range test")
        self.put(1, r)
        self.assertEqual(self.check(), ([], []))
        self.assertEqual(overview.effective(r, "antenna"), ("blocked", "", "waits for the range test"))
        self.assertIn("goes under [[decided]]", self.problems(lambda r: r["decision"][0].update(mark="done")))
        self.assertIn("mark 'later'", self.problems(lambda r: r["decision"][0].update(mark="later")))

    def test_the_page_counts_what_is_still_open(self):
        self.put(1, self.rnd(dict(on="antenna", chose="b", text="Decided (D2).")))
        self.assertEqual(self.build(), 0)
        page = self.page()
        self.assertNotIn("for you</span></span></a>", page)
        self.assertIn('class="decision closed"', page)
        self.assertIn('<span class="flag chosen">Chosen</span>A u.FL one', page)


class BoardNames(Case):
    """A board's id is its PCB's file name, whatever the project called it."""

    def rnd(self, name):
        pcb = os.path.join(self.root, "boards", "demo", "demo.kicad_pcb")
        os.rename(pcb, os.path.join(self.root, "boards", "demo", name + ".kicad_pcb"))
        r = base()
        r["board"][0]["id"] = r["view"][0]["board"] = name
        r["note"] = [dict(date=D(2026, 11, 17), on=name, mark="work", state="ordered", text="Paid.")]
        return r

    def named(self, name, link):
        self.put(1, self.rnd(name))
        self.assertEqual(self.check(), ([], []))
        self.assertEqual(self.build(), 0, self.said)
        page = self.page()
        self.assertIn('<tr id="r1-%s"><th scope="row">%s</th>' % (link, name), page)
        self.assertIn('<a href="#r1-%s">%s</a>' % (link, name), page)
        stored = tomlw.load(self.P["index"])["round"]["r1"]["sets"][name]
        self.assertRegex(stored, r"^%s-[0-9a-f]{8}$" % re.escape(link))
        self.assertNotIn(" ", page[page.index('"frames":["'):page.index('"frames":["') + 60])

    def test_an_underscore(self):
        self.named("controller_v2", "controller_v2")

    def test_a_capital(self):
        self.named("Main", "Main")

    def test_a_space(self):
        self.named("LED board", "LED-board")

    def test_a_round_started_for_such_a_project_can_be_finished(self):
        os.rename(os.path.join(self.root, "boards", "demo", "demo.kicad_pcb"), os.path.join(self.root, "boards", "demo", "Main_v2.kicad_pcb"))
        path = overview.new_round(self.project, self.P, today=D(2026, 11, 17))
        rnd = tomlw.load(path)
        self.assertEqual(rnd["board"][0]["id"], "Main_v2")
        rnd.update(label="Start", state="started", summary="A start.")
        write(path, overview.write_round(rnd))
        self.assertEqual(self.check()[0], [])

    def test_a_round_started_for_a_board_in_a_folder_with_a_space_can_be_finished(self):
        # pcbkit overview new names an unreleased board's file as its source: "boards/LED board/LED board.kicad_pcb"
        os.rename(os.path.join(self.root, "boards", "demo"), os.path.join(self.root, "boards", "LED board"))
        os.rename(os.path.join(self.root, "boards", "LED board", "demo.kicad_pcb"), os.path.join(self.root, "boards", "LED board", "LED board.kicad_pcb"))
        path = overview.new_round(self.project, self.P, today=D(2026, 11, 17))
        rnd = tomlw.load(path)
        self.assertEqual(rnd["board"][0]["source"], "boards/LED board/LED board.kicad_pcb")
        rnd.update(label="Start", state="started", summary="A start.")
        write(path, overview.write_round(rnd))
        self.assertEqual(self.check()[0], [])
        self.assertEqual(self.build(), 0, self.said)
        self.assertIn('<a href="../boards/LED%20board/LED%20board.kicad_pcb">boards/LED board/LED board.kicad_pcb</a>', self.page())

    def test_a_sources_file_is_found_whatever_spaces_its_path_has(self):
        write(os.path.join(self.root, "docs", "Bench notes.md"), "F4 measured.\n")
        parts = lambda text: overview.source_parts(text, self.root)     # noqa: E731
        self.assertEqual(parts("DESIGN.md D1"), ("DESIGN.md", "", "D1"))
        self.assertEqual(parts("DESIGN.md#decisions D1"), ("DESIGN.md", "decisions", "D1"))
        self.assertEqual(parts("docs/Bench notes.md F4"), ("docs/Bench notes.md", "", "F4"))
        self.assertEqual(parts("docs/Bench notes.md The bench, day 2"), ("docs/Bench notes.md", "", "The bench, day 2"))
        self.assertEqual(parts("NOTES.md of some kind"), ("NOTES.md", "", "of some kind"))    # not in the project: as before
        self.assertEqual(overview.source_parts("docs/Bench notes.md F4"), ("docs/Bench", "", "notes.md F4"))
        r = base()
        r["topic"][0]["source"] = "docs/Bench notes.md F4"
        self.put(1, r)
        self.assertEqual(self.check()[0], [])
        r["topic"][0]["source"] = "docs/Bench notes.md F9"             # and the number is still looked for in that file
        self.put(1, r)
        self.assertIn("docs/Bench notes.md has no F9", "\n".join(self.check()[0]))

    def test_topics_and_decisions_keep_the_slug_rule_and_the_pages_own_anchors(self):
        self.assertIn("lower-case", self.problems(lambda r: r["topic"][0].update(id="Power_in")))
        self.assertIn("which the page already uses", self.problems(lambda r: r["topic"][0].update(id="topics")))
        self.assertIn("the PCB's file name", self.problems(lambda r: r["board"][0].update(id=" demo")))


class Orders(unittest.TestCase):
    """An order in a line, from its boards' own states (production.load_orders)."""

    def order(self, *states, events=()):
        boards = [dict(pcb="b%d" % i, status=s, events=list(events)) for i, s in enumerate(states)]
        return dict(name="2026-11-16-first", board=boards, events=list(events))

    def test_one_board_arrived_and_one_is_still_being_made(self):
        got = overview.order_summary(self.order("received", "in-production"))
        self.assertEqual((got["value"], got["mark"]), ("2026-11-16-first: b0 received, b1 in-production", "work"))

    def test_the_order_is_done_when_every_board_is(self):
        self.assertEqual(overview.order_summary(self.order("received", "received"))["mark"], "done")
        self.assertEqual(overview.order_summary(self.order("received", "cancelled"))["mark"], "done")
        self.assertEqual(overview.order_summary(self.order("cancelled", "cancelled"))["mark"], "todo")
        self.assertEqual(overview.order_summary(self.order("shipped", "shipped"))["value"], "2026-11-16-first: shipped")

    def test_a_problem_on_one_board_is_the_orders(self):
        self.assertEqual(overview.order_summary(self.order("received", "problem"))["mark"], "attention")

    def test_a_price_for_the_whole_order_counts_once_and_the_date_is_the_latest(self):
        whole = dict(date=D(2026, 11, 16), status="submitted", price=202.02, currency="USD")
        later = dict(date=D(2026, 11, 20), status="shipped")
        got = overview.order_summary(self.order("shipped", "shipped", events=[whole, later]))
        self.assertEqual((got["value"], got["date"]), ("2026-11-16-first: shipped, 202.02 USD", D(2026, 11, 20)))
        a = dict(date=D(2026, 11, 16), status="draft", price=125.55, currency="USD")
        b = dict(date=D(2026, 11, 16), status="draft", price=76.47, currency="USD")
        o = dict(name="o", events=[a, b], board=[dict(pcb="x", status="draft", events=[a]), dict(pcb="y", status="draft", events=[b])])
        self.assertEqual(overview.order_summary(o)["value"], "o: draft, 202.02 USD")

    def test_a_currency_corrected_without_a_price_applies_to_the_price(self):
        quoted = dict(date=D(2026, 11, 16), status="draft", price=100.0, currency="USD")
        corrected = dict(date=D(2026, 11, 18), status="draft", currency="EUR")
        o = dict(name="o", events=[quoted, corrected],
                 board=[dict(pcb="x", status="draft", currency="EUR", events=[quoted, corrected])])    # as load_orders resolves it
        self.assertEqual(overview.order_summary(o)["value"], "o: draft, 100.00 EUR")
        del o["board"][0]["currency"]                                  # and from the events alone
        self.assertEqual(overview.order_summary(o)["value"], "o: draft, 100.00 EUR")

    def test_a_price_corrected_without_a_currency_keeps_the_one_it_had(self):
        quoted = dict(date=D(2026, 11, 16), status="draft", price=125.55, currency="USD")
        corrected = dict(date=D(2026, 11, 18), status="submitted", price=131.20)
        o = dict(name="o", events=[quoted, corrected],
                 board=[dict(pcb="x", status="submitted", currency="USD", events=[quoted, corrected])])    # as load_orders resolves it
        self.assertEqual(overview.order_summary(o)["value"], "o: submitted, 131.20 USD")
        del o["board"][0]["currency"]                                  # and from the events alone
        self.assertEqual(overview.order_summary(o)["value"], "o: submitted, 131.20 USD")

    def test_amounts_in_different_currencies_are_not_added(self):
        a = dict(date=D(2026, 11, 16), status="draft", price=125.55, currency="USD")
        b = dict(date=D(2026, 11, 16), status="draft", price=68.00, currency="EUR")
        c = dict(date=D(2026, 11, 16), status="draft", price=10.00, currency="USD")
        boards = [dict(pcb=n, status="draft", events=[e]) for n, e in (("x", a), ("y", b), ("z", c))]
        self.assertEqual(overview.order_summary(dict(name="o", events=[a, b, c], board=boards))["value"],
                         "o: draft, 135.55 USD + 68.00 EUR")
        bare = dict(date=D(2026, 11, 16), status="draft", price=76.47)                # no currency anywhere: not taken for USD
        boards = [dict(pcb="x", status="draft", events=[a]), dict(pcb="y", status="draft", events=[bare])]
        self.assertEqual(overview.order_summary(dict(name="o", events=[a, bare], board=boards))["value"],
                         "o: draft, 125.55 USD + 76.47")


class OrdersFromTheRecords(Case):
    def test_an_event_about_one_board_does_not_speak_for_the_order(self):
        write(os.path.join(self.root, "boards", "other", "other.kicad_pcb"), '(kicad_pcb (version 20260206) (footprint "R_0402"))\n')
        write(os.path.join(self.root, "production", "orders", "2026-11-16-first", "order.toml"),
              'title = "First"\n\n[[board]]\npcb = "demo"\nversion = "1.0.0"\npcbs = 5\n\n[[board]]\npcb = "other"\nversion = "1.0.0"\npcbs = 5\n\n'
              '[[event]]\ndate = 2026-11-16\nstatus = "submitted"\n\n[[event]]\ndate = 2026-11-30\nstatus = "received"\npcb = "demo"\n')
        orders = overview.records(Project(self.root))["orders"]
        self.assertEqual([(o["value"], o["mark"], o["date"]) for o in orders],
                         [("2026-11-16-first: demo received, other submitted", "work", D(2026, 11, 30))])
        self.assertEqual(orders[0]["source"], "production/orders/2026-11-16-first/order.toml")


class RecordsThatDoNotRead(Case):
    """Records that are there and can't be read say nothing about a board: that is said, not taken for 'released'."""

    def setUp(self):
        super().setUp()
        os.makedirs(os.path.join(self.root, "releases", "demo"))
        tomlw.dump(dict(key="demo", release=[dict(version="1.1.0", label="v1.1", date=D(2026, 11, 20), source="x", note="n")]),
                   os.path.join(self.root, "releases", "demo", "releases.toml"))
        write(os.path.join(self.root, "production", "boards.toml"), "[pcb.demo\nstate = released\n")     # not TOML

    def test_the_board_has_its_last_version_and_no_state(self):
        rec = overview.records(self.project)
        self.assertEqual(rec["board"], [dict(id="demo", version="v1.1", source="releases/demo/releases.toml")])
        self.assertEqual(len(rec["problems"]), 1)
        self.assertIn("could not be read", rec["problems"][0])
        self.assertIn("no board's state is known", rec["problems"][0])

    def test_a_new_round_says_so_and_leaves_the_state_to_be_written(self):
        said = []
        rnd = tomlw.load(overview.new_round(self.project, self.P, today=D(2026, 12, 1), say=said.append))
        self.assertEqual([(b["id"], b["version"], b["mark"], b["state"]) for b in rnd["board"]], [("demo", "v1.1", "todo", "")])
        self.assertTrue(said and said[0].startswith("watch: the project's release and production records could not be read"), said)

    def test_a_check_says_so(self):
        self.put(1, base())
        problems, watch = self.check()
        self.assertEqual(problems, [])
        self.assertIn("could not be read", "\n".join(watch))

    def test_an_order_that_does_not_parse_is_said_and_the_others_are_kept(self):
        os.remove(os.path.join(self.root, "production", "boards.toml"))
        good = ('title = "First"\n\n[[board]]\npcb = "demo"\nversion = "1.1.0"\npcbs = 5\n\n'
                '[[event]]\ndate = 2026-11-16\nstatus = "submitted"\n')
        write(os.path.join(self.root, "production", "orders", "2026-11-16-first", "order.toml"), good)
        write(os.path.join(self.root, "production", "orders", "2026-11-20-second", "order.toml"), good.replace('"submitted"', "submitted"))
        rec = overview.records(self.project)
        self.assertEqual([o["value"] for o in rec["orders"]], ["2026-11-16-first: submitted"])
        self.assertEqual(len(rec["problems"]), 1, rec["problems"])        # the first order's own remarks are production's
        self.assertIn("an order could not be read and is left out (production/orders/2026-11-20-second/order.toml:", rec["problems"][0])
        self.put(1, base())
        self.assertIn("2026-11-20-second/order.toml", "\n".join(self.check()[1]))
        os.remove(os.path.join(self.P["dir"], "r1.toml"))
        said = []
        rnd = tomlw.load(overview.new_round(self.project, self.P, today=D(2026, 12, 1), say=said.append))
        self.assertEqual([f["value"] for f in rnd["fact"]], ["2026-11-16-first: submitted"])
        self.assertIn("2026-11-20-second/order.toml", "\n".join(said))

    def test_records_that_read_still_tell_a_changed_board_from_a_released_one(self):
        os.remove(os.path.join(self.root, "production", "boards.toml"))
        rec = overview.records(self.project)
        self.assertEqual(rec["problems"], [])
        self.assertEqual((rec["board"][0]["state"], rec["board"][0]["mark"]), ("changed since v1.1", "work"))


class Tags(Case):
    def test_against_the_round_before(self):
        one = base()
        one["topic"] += [dict(one["topic"][0], id="mcu", title="MCU"), dict(one["topic"][0], id="case", title="Case")]
        two = copy.deepcopy(one)
        two["topic"][0]["text"] = "An eFuse,   then a buck."          # the same words
        two["topic"][1]["state"] = "reviewed"
        two["topic"][2] = dict(two["topic"][2], id="firmware", title="Firmware")
        self.assertEqual(overview.tags(two, one), ({"mcu": "changed", "firmware": "new"}, ["Case"]))

    def test_none_without_topics_to_compare_with(self):
        self.assertEqual(overview.tags(base(), None), ({}, []))
        self.assertEqual(overview.tags(base(), dict(base(), topic=[])), ({}, []))


class ThePage(Case):
    def two_rounds(self):
        one = dict(base(), date=D(2026, 10, 27), label="Rework", mark="work", state="routed")
        two = base()
        two["topic"][0].update(text="An eFuse, a damper, then a buck.")
        two["note"] = [dict(date=D(2026, 11, 17), on="power", mark="attention", state="rings at 8 V", text="The input rings (`sim/ring.txt`)."),
                       dict(date=D(2026, 11, 18), on="status", text="Paid.")]
        self.put(1, one)
        self.put(2, two)
        self.assertEqual(self.build(), 0, self.said)
        return self.page()

    def test_every_round_is_there_and_the_newest_is_shown(self):
        page = self.two_rounds()
        self.assertEqual(re.findall(r'<section class="round" id="(r\d+)"', page), ["r2", "r1"])
        self.assertEqual(re.findall(r'<a href="#(r\d)" data-round="r\d"( aria-current="true")?>', page), [("r1", ""), ("r2", ' aria-current="true"')])
        self.assertIn('const D = document, newest = "r2";', page)
        self.assertIn("Round 1 of 2, as it stood on 2026-10-27", page)
        self.assertEqual(page.count("as it stood on"), 1)

    def test_notes_sit_under_their_item_and_in_the_list_of_additions(self):
        page = self.two_rounds()
        topic = page[page.index('id="r2-power"'):page.index("</article>", page.index('id="r2-power"'))]
        self.assertIn('<p class="note"><time>2026-11-17</time> The input rings (<code>sim/ring.txt</code>).</p>', topic)
        self.assertIn("mk-attention", topic)                           # the mark its note left
        self.assertIn("rings at 8 V", topic)
        self.assertNotIn("waits for the bench", topic)
        added = page[page.index("Added since 2026-11-16"):page.index("</ul>", page.index("Added since 2026-11-16"))]
        self.assertIn('<a href="#r2-power">Power</a>', added)
        self.assertIn('<a href="#r2-status">Status</a>', added)
        self.assertLess(added.index("2026-11-17"), added.index("2026-11-18"))
        self.assertNotIn("Added since", page[page.index('id="r1"'):])

    def test_a_changed_topic_is_tagged(self):
        self.assertIn('<h4>Power <span class="tag changed">changed</span></h4>', self.two_rounds())

    def test_text_is_escaped_and_marked_up(self):
        r = base()
        r["summary"] = "<script>alert(1)</script> & **bold** `code` [web](https://example.org/a?b=1&c=2) [doc](DESIGN.md) [bad](javascript:alert(1))"
        self.put(1, r)
        self.assertEqual(self.build(), 0, self.said)
        page = self.page()
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt; &amp; <b>bold</b> <code>code</code>", page)
        self.assertIn('<a href="https://example.org/a?b=1&amp;c=2">web</a>', page)
        self.assertIn('<a href="../DESIGN.md">doc</a>', page)
        self.assertNotIn("javascript:", page)
        self.assertNotIn("<script>alert", page)

    def test_sources_link_the_projects_files_from_where_the_page_is(self):
        self.put(1, base())
        self.assertEqual(self.build(), 0, self.said)
        page = self.page()
        self.assertIn('<a href="../DESIGN.md">DESIGN.md D1</a>', page)
        self.assertIn('<a href="pic.png">The picture</a>', page)       # a file deeper in goes by its label
        self.assertIn('src="overview/img/', page)

    def test_one_legend_and_every_status_through_the_same_marks(self):
        self.put(1, base())
        self.assertEqual(self.build(), 0, self.said)
        page = self.page()
        self.assertEqual(page.count('<ul class="legend"'), 1)
        legend = page[page.index('<ul class="legend"'):page.index("</ul>", page.index('<ul class="legend"'))]
        self.assertEqual(re.findall(r"mk mk-(\w+)", legend), [m for m, _ in overview.MARKS])
        for mark in set(re.findall(r'class="mk mk-(\w+)', page)):
            self.assertIn(mark, overview.MARK)
        self.assertIn('<span class="sr">Done: </span>released', page)

    def test_nothing_comes_from_the_network_and_nothing_from_the_clock(self):
        self.put(1, base())
        self.assertEqual(self.build(), 0, self.said)
        first = self.page()
        self.assertNotRegex(first, r"https?://")
        self.assertEqual(self.build(), 0, self.said)
        self.assertEqual(self.page(), first)

    def test_the_single_page_holds_its_pictures(self):
        self.two_rounds()
        one = os.path.join(self.root, "build", "send", "one.html")     # a folder that is not there yet
        self.assertEqual(self.build(single=one), 0, self.said)
        with open(one, encoding="utf-8") as fh:
            page = fh.read()
        self.assertNotRegex(page, r'src="(?!data:)')
        self.assertNotRegex(page, r'"(frames|src)":\s*\[?"(?!data:)')
        self.assertIn("data:image/webp;base64,", page)
        self.assertNotIn('href="../DESIGN.md"', page)                  # the file travels alone: no links into the project
        self.assertIn("DESIGN.md D1", page)
        newest, earlier = page[page.index('id="r2"'):page.index('id="r1"')], page[page.index('id="r1"'):]
        self.assertIn('"kind":"turn"', newest)
        self.assertNotIn('"kind":"turn"', earlier)                     # one picture a view for an earlier round
        self.assertNotIn('"kind":"layers"', earlier)

    def test_an_earlier_round_of_the_single_page_keeps_a_board_that_has_layers_only(self):
        self.put(1, base())
        self.put(2, base())
        self.said = []
        self.assertEqual(overview.build(self.project, say=self.said.append, renderer=layers_only,
                                        single=os.path.join(self.root, "one.html")), 0, self.said)
        with open(os.path.join(self.root, "one.html"), encoding="utf-8") as fh:
            page = fh.read()
        newest, earlier = page[page.index('id="r2"'):page.index('id="r1"')], page[page.index('id="r1"'):]
        self.assertEqual(re.findall(r'"id":"([\w.]+)"', newest[:newest.index("</script>")]), ["F.Mask", "F.Cu", "B.Cu", "Edge.Cuts"])
        data = earlier[earlier.index('<script type="application/json">'):earlier.index("</script>")]
        self.assertIn('"name":"demo"', data)                           # the board is still a tab of the viewer
        self.assertEqual(re.findall(r'"id":"([\w.]+)"', data), ["F.Cu", "Edge.Cuts"])   # the front as it opens
        self.assertNotRegex(data, r'"src":"(?!data:)')

    def test_a_base_url_links_the_sources_there(self):
        write(os.path.join(self.root, "pcbkit.toml"), '[overview]\nbase = "https://example.org/repo/blob/main"\n')
        self.project = Project(self.root)
        self.put(1, base())
        self.assertEqual(self.build(), 0, self.said)
        self.assertIn('<a href="https://example.org/repo/blob/main/DESIGN.md">DESIGN.md D1</a>', self.page())


PLOT = ('<?xml version="1.0" standalone="no"?>\n<svg width="10mm" height="5mm" viewBox="0 0 10 5">\n'
        '<g style="fill:#000000; fill-opacity:1.0000;stroke:#000000; stroke-opacity:1.0000;">\n%s</g>\n</svg>\n')
PAD = '<g style="fill:%s; stroke:%s;">\n<circle cx="2" cy="2" r="0.2" />\n</g>\n'      # one small pad: a few bytes of plot
SIX = ('(kicad_pcb (version 20260206) (layers (0 "F.Cu" signal) (4 "In1.Cu" signal) (6 "In2.Cu" signal) (8 "In3.Cu" signal)'
       ' (10 "In4.Cu" power) (2 "B.Cu" signal) (5 "F.SilkS" user "F.Silkscreen") (1 "F.Mask" user) (3 "B.Mask" user)'
       ' (25 "Edge.Cuts" user)) (gr_line (layer "In9.Cu")))\n')


class Layers(Case):
    """Which layers a board's viewer gets, and which of them are left out."""

    def test_every_copper_layer_the_board_has_and_both_masks(self):
        two = '(kicad_pcb (layers (0 "F.Cu" signal) (2 "B.Cu" signal) (1 "F.Mask" user)))'
        self.assertEqual([layer for layer, _ in overview.board_layers(two)],
                         ["F.Silkscreen", "F.Mask", "F.Cu", "B.Cu", "B.Mask", "B.Silkscreen", "Edge.Cuts"])
        self.assertEqual(overview.board_layers(SIX)[2:8],
                         [("F.Cu", "front copper"), ("In1.Cu", "inner 1"), ("In2.Cu", "inner 2"), ("In3.Cu", "inner 3"),
                          ("In4.Cu", "inner 4"), ("B.Cu", "back copper")])

    def test_a_plot_is_empty_when_it_draws_nothing_not_when_it_is_small(self):
        self.assertEqual(overview.plot_facts(PLOT % ""), (False, None))
        self.assertEqual(overview.plot_facts(PLOT % (PAD % ("#02FFEE", "#02FFEE"))), (True, "#02ffee"))
        self.assertEqual(overview.plot_facts(PLOT % (PAD % ("#000000", "#000000"))), (True, "#000000"))
        holes = (PAD % ("#FFFFFF", "#FFFFFF")) * 5                     # a plane with more drill holes than shapes of its own
        self.assertEqual(overview.plot_facts(PLOT % (PAD % ("#7FC87F", "#7FC87F") + holes)), (True, "#7fc87f"))

    def render(self, board, plots):
        """render_set on a board written here, with kicad-cli's part played: each layer's plot is PLOTS' or empty."""
        from pcbkit.release import previews
        pcb = os.path.join(self.root, "boards", "demo", "demo.kicad_pcb")
        write(pcb, board)
        dest = os.path.join(self.root, "set")
        os.makedirs(dest)

        def layer_png(pcb, layers, png, width=3600, mirror=False, tmp=None, background="white"):
            write(png, PNG, "wb")                                      # every picture is the same few bytes
            write(os.path.join(tmp, os.path.splitext(os.path.basename(png))[0] + ".svg"), PLOT % plots.get(layers, ""))
            return (10.0, 5.0)

        with mock.patch.object(previews, "layer_png", layer_png), mock.patch.object(previews, "turntable", lambda *a, **k: {}), \
                mock.patch.object(previews, "webp", lambda png: None):
            return overview.render_set(pcb, dest, self.project.cfg["overview"]), dest

    def test_a_sparse_layer_stays_and_an_empty_one_goes(self):
        plots = {"F.Cu": PAD % ("#C83434", "#C83434"), "In3.Cu": PAD % ("#4FCBCB", "#4FCBCB"), "In4.Cu": PAD % ("#DB628B", "#DB628B"),
                 "B.Mask": PAD % ("#02FFEE", "#02FFEE"), "Edge.Cuts": PAD % ("#D0D2CD", "#D0D2CD")}
        man, dest = self.render(SIX, plots)
        self.assertEqual([(x["id"], x["name"], x["colour"]) for x in man["layers"]],
                         [("F.Cu", "front copper", "#c83434"), ("In3.Cu", "inner 3", "#4fcbcb"), ("In4.Cu", "inner 4", "#db628b"),
                          ("B.Mask", "back mask openings", "#02ffee"), ("Edge.Cuts", "outline", "#d0d2cd")])
        self.assertEqual(sorted(os.listdir(dest)), sorted(x["file"] for x in man["layers"]))   # the empty ones' pictures are gone
        self.assertEqual(man["mm"], [10.0, 5.0])

    def test_a_board_that_is_only_its_outline_still_has_a_picture(self):
        man, _ = self.render(SIX, {"Edge.Cuts": PAD % ("#D0D2CD", "#D0D2CD")})
        self.assertEqual([x["id"] for x in man["layers"]], ["Edge.Cuts"])

    def drawn(self, turn, layer_png):
        """render_set on the fixture's board (it has a part, so a turntable is due), with what kicad-cli would do given."""
        from pcbkit.release import previews
        dest = os.path.join(self.root, "set")
        os.makedirs(dest)
        with mock.patch.object(previews, "layer_png", layer_png), mock.patch.object(previews, "turntable", lambda *a, **k: turn), \
                mock.patch.object(previews, "webp", lambda png: None):
            return overview.render_set(os.path.join(self.root, "boards", "demo", "demo.kicad_pcb"), dest, self.project.cfg["overview"])

    def test_what_could_not_be_drawn_is_listed_with_the_reason(self):
        def plot(pcb, layers, png, width=3600, mirror=False, tmp=None, background="white"):
            if layers == "B.Cu":
                raise subprocess.CalledProcessError(1, ["/Applications/KiCad/kicad-cli", "pcb", "export", "svg"])
            if layers in ("F.Mask", "B.Mask"):
                raise FileNotFoundError(2, "No such file or directory", "rsvg-convert")
            write(png, PNG, "wb")
            write(os.path.join(tmp, os.path.splitext(os.path.basename(png))[0] + ".svg"), PLOT % (PAD % ("#C83434", "#C83434")))
            return (10.0, 5.0)

        man = self.drawn(dict(top=["turn-top-00.webp"]), plot)
        self.assertEqual(man["failed"], ["the back of the turntable (kicad-cli pcb render failed)",
                                         "layers F.Mask, B.Mask (rsvg-convert is not installed)", "layer B.Cu (kicad-cli failed)"])
        self.assertEqual([x["id"] for x in man["layers"]], ["F.Silkscreen", "F.Cu", "B.Silkscreen", "Edge.Cuts"])

    def test_nothing_failed_is_not_listed(self):
        def plot(pcb, layers, png, width=3600, mirror=False, tmp=None, background="white"):
            write(png, PNG, "wb")
            write(os.path.join(tmp, os.path.splitext(os.path.basename(png))[0] + ".svg"), PLOT % "")    # empty is not failed
            return (10.0, 5.0)

        man = self.drawn(dict(top=["a.webp"], bottom=["b.webp"]), plot)
        self.assertNotIn("failed", man)
        self.assertNotIn("layers", man)

    def test_no_turntable_at_all_and_no_tool_for_the_layers(self):
        def plot(pcb, layers, png, width=3600, mirror=False, tmp=None, background="white"):
            raise FileNotFoundError(2, "No such file or directory", "rsvg-convert")

        man = self.drawn({}, plot)
        self.assertEqual(man["failed"][0], "the turntable (kicad-cli pcb render failed)")
        self.assertEqual(man["failed"][1], "layers F.Silkscreen, F.Mask, F.Cu, B.Cu, B.Mask, B.Silkscreen, Edge.Cuts (rsvg-convert is not installed)")
        self.assertNotIn("turn", man)

    def test_the_build_and_the_check_say_what_a_viewer_is_without(self):
        def partly(pcb, dest, cfg):
            man = fake_renderer(self.calls)(pcb, dest, cfg)
            del man["turn"]["bottom"]
            return dict(man, failed=["the back of the turntable (kicad-cli pcb render failed)"])
        self.put(1, base())
        self.assertEqual(overview.build(self.project, say=self.said.append, renderer=partly), 0, self.said)
        self.assertIn("watch: r1: the viewer of demo is without the back of the turntable (kicad-cli pcb render failed)", self.said)
        self.assertEqual(self.build(check=True), 0)                    # a watch line, not a problem: the page is the rounds'
        self.assertEqual(self.said, ["watch: r1.toml: the viewer of demo is without the back of the turntable (kicad-cli pcb render "
                                     "failed); once that is put right, pcbkit overview --render"])
        self.assertEqual(self.build(refresh=True), 0, self.said)       # drawn again in full: nothing left to say
        self.assertEqual(self.build(check=True), 0)
        self.assertEqual(self.said, [])

    def test_a_renderer_that_stops_is_said_and_the_stills_stand_in(self):
        def stops(pcb, dest, cfg):
            raise SystemExit("kicad-cli: not found")
        write(os.path.join(self.root, "docs", "previews", "demo", "render-top.png"), PNG, "wb")
        self.put(1, base())
        self.assertEqual(overview.build(self.project, say=self.said.append, renderer=stops), 0, self.said)
        self.assertIn("watch: r1: demo was not rendered (kicad-cli: not found)", self.said)
        self.assertIn('"kind":"pics","name":"Pictures"', self.page())

    def test_the_viewers_key_is_the_sets_own_and_an_older_set_still_reads(self):
        def six(pcb, dest, cfg):
            for name in ("layer-In4_Cu.webp", "layer-B_Mask.webp", "layer-F_Cu.webp"):
                write(os.path.join(dest, name), PNG, "wb")
            return dict(made="test", layers=[dict(id="F.Cu", file="layer-F_Cu.webp"),                    # as sets were written before
                                             dict(id="In4.Cu", name="inner 4", colour="#db628b", file="layer-In4_Cu.webp"),
                                             dict(id="B.Mask", name="back mask openings", colour="#123456", file="layer-B_Mask.webp")])
        self.put(1, base())
        self.assertEqual(overview.build(self.project, say=self.said.append, renderer=six), 0, self.said)
        page = self.page()
        self.assertIn('{"id":"F.Cu","name":"front copper","colour":"#c83434"', page)
        self.assertIn('{"id":"In4.Cu","name":"inner 4","colour":"#db628b"', page)
        self.assertIn('{"id":"B.Mask","name":"back mask openings","colour":"#123456"', page)


class Pictures(Case):
    def index(self):
        return tomlw.load(self.P["index"])["round"]

    def test_a_named_picture_is_kept_as_it_was(self):
        self.put(1, base())
        self.assertEqual(self.build(), 0, self.said)
        name = self.index()["r1"]["pictures"]["docs/pic.png"]
        write(os.path.join(self.root, "docs", "pic.png"), PNG + b"later", "wb")
        self.assertEqual(self.build(), 0, self.said)
        self.assertEqual(self.index()["r1"]["pictures"]["docs/pic.png"], name)
        with open(os.path.join(self.P["img"], name), "rb") as fh:
            self.assertEqual(fh.read(), PNG)

    def test_an_unchanged_board_is_rendered_once(self):
        self.put(1, base())
        self.assertEqual(self.build(), 0, self.said)
        self.put(2, base())
        self.assertEqual(self.build(), 0, self.said)
        self.assertEqual(len(self.calls), 1)
        sets = self.index()
        self.assertEqual(sets["r1"]["sets"], sets["r2"]["sets"])

    def test_a_3d_model_of_the_projects_own_that_changed_draws_the_board_again(self):
        pcb = os.path.join(self.root, "boards", "demo", "demo.kicad_pcb")
        model = os.path.join(self.root, "lib", "3dmodels", "conn.step")
        write(pcb, '(kicad_pcb (version 20260206) (footprint "J" (model "${KIPRJMOD}/../../lib/3dmodels/conn.step")'
                   ' (model "${KICAD10_3DMODEL_DIR}/R.step")))\n')
        write(model, "ISO-10303-21; as drawn first\n")
        self.assertTrue(overview.models_print(pcb))
        self.put(1, base())
        self.assertEqual(self.build(), 0, self.said)
        self.put(2, base())
        self.assertEqual(self.build(), 0, self.said)                   # neither changed: one set for both rounds
        self.assertEqual(len(self.calls), 1)
        write(model, "ISO-10303-21; turned the right way round\n")     # the board file still names it the same
        self.put(3, base())
        self.assertEqual(self.build(), 0, self.said)
        sets = self.index()
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(sets["r1"]["sets"], sets["r2"]["sets"])
        self.assertNotEqual(sets["r3"]["sets"]["demo"], sets["r2"]["sets"]["demo"])

    def test_models_are_found_however_the_board_names_them(self):
        folder = os.path.join(self.root, "boards", "demo")
        pcb = os.path.join(folder, "demo.kicad_pcb")
        names = {"${KIPRJMOD}/a.step": "a.step", "$(KIPRJMOD)/b.step": "b.step", "models/c.wrl": os.path.join("models", "c.wrl"),
                 r"${KIPRJMOD}/odd \"name\".step": 'odd "name".step', os.path.join(self.root, "lib", "d.step"): os.path.join(self.root, "lib", "d.step")}
        board = '(kicad_pcb (version 20260206) (footprint "J" %s (model "${KICAD10_3DMODEL_DIR}/R.step") (model "kicad-embed://e.step")))\n'
        write(pcb, board % " ".join('(model "%s")' % n for n in names))
        for rel in names.values():
            write(os.path.join(folder, rel), "ISO-10303-21; first\n")
        from pcbkit.release import freeze
        self.assertEqual(sorted(os.path.basename(p) for _, p in freeze.model_files(pcb)), ["a.step", "b.step", "c.wrl", "d.step", 'odd "name".step'])
        first = overview.models_print(pcb)
        for rel in names.values():                                     # each of them changes what a render shows
            with self.subTest(model=rel):
                write(os.path.join(folder, rel), "ISO-10303-21; changed\n")
                self.assertNotEqual(overview.models_print(pcb), first)
                write(os.path.join(folder, rel), "ISO-10303-21; first\n")
                self.assertEqual(overview.models_print(pcb), first)

    def test_a_board_without_models_of_the_project_is_named_as_before(self):
        pcb = os.path.join(self.root, "boards", "demo", "demo.kicad_pcb")
        self.assertEqual(overview.models_print(pcb), "")
        write(pcb, open(pcb).read().replace('(footprint "R_0402")', '(footprint "R_0402" (model "${KICAD10_3DMODEL_DIR}/R.step")'
                                                                    ' (model "${KIPRJMOD}/gone.step"))'))
        self.assertEqual(overview.models_print(pcb), "")               # KiCad's own, and one that is not there

    def test_a_changed_board_gets_its_own_set_and_the_earlier_round_keeps_its(self):
        self.put(1, base())
        self.assertEqual(self.build(), 0, self.said)
        pcb = os.path.join(self.root, "boards", "demo", "demo.kicad_pcb")
        write(pcb, open(pcb).read().replace("R_0402", "R_0603"))
        self.put(2, base())
        self.assertEqual(self.build(), 0, self.said)
        sets = self.index()
        self.assertEqual(len(self.calls), 2)
        self.assertNotEqual(sets["r1"]["sets"]["demo"], sets["r2"]["sets"]["demo"])
        for name in (sets["r1"]["sets"]["demo"], sets["r2"]["sets"]["demo"]):
            self.assertTrue(os.path.exists(os.path.join(self.P["img"], name, "set.json")))

    def test_render_again_replaces_the_newest_rounds_set_and_drops_the_old_one(self):
        self.put(1, base())
        self.assertEqual(self.build(), 0, self.said)
        old = self.index()["r1"]["sets"]["demo"]
        pcb = os.path.join(self.root, "boards", "demo", "demo.kicad_pcb")
        write(pcb, open(pcb).read().replace("R_0402", "R_0603"))
        self.assertEqual(self.build(), 0, self.said)
        self.assertEqual(self.index()["r1"]["sets"]["demo"], old)       # kept until asked
        self.assertEqual(self.build(refresh=True), 0, self.said)
        new = self.index()["r1"]["sets"]["demo"]
        self.assertNotEqual(new, old)
        self.assertFalse(os.path.exists(os.path.join(self.P["img"], old)))

    def test_render_again_draws_an_unchanged_board_again(self):
        self.put(1, base())
        self.assertEqual(self.build(), 0, self.said)
        first = self.index()["r1"]["sets"]["demo"]
        self.assertEqual(self.build(refresh=True), 0, self.said)       # a 3-D model changed, say: the board file did not
        self.assertEqual(len(self.calls), 2)
        # drawn beside the set that was there, which goes once the page that shows the new one is written
        self.assertEqual(self.index()["r1"]["sets"]["demo"], first + "-2")
        self.assertEqual(sorted(n for n in os.listdir(self.P["img"]) if n.startswith("demo-")), [first + "-2"])
        self.assertIn(first + "-2/turn-top-00.webp", self.page())

    def test_render_again_leaves_the_set_an_earlier_round_uses(self):
        self.put(1, base())
        self.assertEqual(self.build(), 0, self.said)
        self.put(2, base())
        self.assertEqual(self.build(), 0, self.said)
        shared = self.index()["r1"]["sets"]["demo"]
        marker = os.path.join(self.P["img"], shared, "turn-top-00.webp")
        write(marker, PNG + b"as round 1 saw it", "wb")
        self.assertEqual(self.build(refresh=True), 0, self.said)
        sets = self.index()
        self.assertEqual(sets["r1"]["sets"]["demo"], shared)
        self.assertEqual(sets["r2"]["sets"]["demo"], shared + "-2")
        with open(marker, "rb") as fh:
            self.assertEqual(fh.read(), PNG + b"as round 1 saw it")
        self.assertEqual(self.build(refresh=True), 0, self.said)       # and again: beside both, then round 2's old one goes
        self.assertEqual(self.index()["r2"]["sets"]["demo"], shared + "-3")
        self.assertEqual(sorted(n for n in os.listdir(self.P["img"]) if n.startswith("demo-")), [shared, shared + "-3"])
        self.assertEqual(len(self.calls), 3)

    def test_a_new_round_takes_the_set_that_was_drawn_again_not_the_first(self):
        self.put(1, base())
        self.put(2, base())
        self.assertEqual(self.build(), 0, self.said)
        shared = self.index()["r1"]["sets"]["demo"]
        self.assertEqual(self.build(refresh=True), 0, self.said)       # round 2's pictures put right: a KiCad model, say
        self.assertEqual(self.index()["r2"]["sets"]["demo"], shared + "-2")
        self.put(3, base())                                            # the board file is still the same
        self.assertEqual(self.build(), 0, self.said)
        sets = self.index()
        self.assertEqual([sets[r]["sets"]["demo"] for r in ("r1", "r2", "r3")], [shared, shared + "-2", shared + "-2"])
        self.assertEqual(len(self.calls), 2)                           # nothing was drawn for round 3

    def test_a_build_that_fails_removes_nothing_the_page_on_disk_shows(self):
        other = os.path.join(self.root, "boards", "other", "other.kicad_pcb")
        write(other, '(kicad_pcb (version 20260206) (footprint "R_0402"))\n')
        self.project = Project(self.root)
        rnd = base()
        rnd["view"].append(dict(board="other"))
        self.put(1, rnd)
        self.assertEqual(self.build(), 0, self.said)
        with open(self.P["index"], encoding="utf-8") as fh:
            index = fh.read()
        page, was = self.page(), self.index()["r1"]["sets"]
        shown_now = os.path.join(self.P["img"], was["demo"], "turn-top-00.webp")
        write(shown_now, PNG + b"what the page on disk shows", "wb")
        write(other, open(other).read().replace("R_0402", "R_0603"))   # one board changes and can't be drawn again
        good = fake_renderer(self.calls)
        broken = lambda pcb, dest, cfg: {} if pcb == other else good(pcb, dest, cfg)     # noqa: E731
        self.said = []
        self.assertEqual(overview.build(self.project, say=self.said.append, renderer=broken, refresh=True), 1)
        self.assertIn("no pictures of other", "\n".join(self.said))
        with open(self.P["index"], encoding="utf-8") as fh:
            self.assertEqual(fh.read(), index)
        self.assertEqual(self.page(), page)
        for name in was.values():                                      # what the page on disk links is all still there
            self.assertTrue(os.path.exists(os.path.join(self.P["img"], name, "set.json")), name)
        with open(shown_now, "rb") as fh:                              # and as it was: the unchanged board, drawn again,
            self.assertEqual(fh.read(), PNG + b"what the page on disk shows")    # went beside its set, not over it
        self.assertEqual(self.build(refresh=True), 0, self.said)       # and once a build is written, the unused goes
        now = self.index()["r1"]["sets"]
        self.assertEqual(sorted(n for n in os.listdir(self.P["img"]) if os.path.isdir(os.path.join(self.P["img"], n))),
                         sorted(now.values()))
        self.assertFalse(set(was.values()) & set(now.values()))

    def test_pictures_gone_from_the_store_stop_the_build_and_the_check(self):
        self.put(1, base())
        self.put(2, base())
        self.assertEqual(self.build(), 0, self.said)
        page, sets = self.page(), self.index()["r1"]
        frame = os.path.join(self.P["img"], sets["sets"]["demo"], "turn-top-01.webp")
        copy = os.path.join(self.P["img"], sets["pictures"]["docs/pic.png"])
        os.remove(frame)
        os.remove(copy)
        for kw in (dict(), dict(check=True)):
            with self.subTest(kw=kw):
                self.assertEqual(self.build(**kw), 1)
                said = "\n".join(self.said)
                self.assertIn("its pictures of demo, lacks 1 of its 5 files (turn-top-01.webp)", said)
                self.assertIn("its copy of docs/pic.png, is missing", said)
                self.assertIn("restore docs/overview/img as it was committed", said)
                self.assertEqual(self.page(), page)                    # no page was written without them
        os.remove(os.path.join(self.P["img"], sets["sets"]["demo"], "set.json"))
        self.assertEqual(self.build(), 1)
        self.assertIn("is missing or has no set.json", "\n".join(self.said))

    def test_the_newest_rounds_missing_pictures_can_be_drawn_again(self):
        self.put(1, base())
        self.assertEqual(self.build(), 0, self.said)
        sets = self.index()["r1"]
        shutil.rmtree(os.path.join(self.P["img"], sets["sets"]["demo"]))
        os.remove(os.path.join(self.P["img"], sets["pictures"]["docs/pic.png"]))
        self.assertEqual(self.build(), 1)
        self.assertEqual(self.build(refresh=True), 0, self.said)
        self.assertEqual(self.build(check=True), 0, self.said)

    def test_stills_stand_in_for_a_view_that_asked_for_what_is_not_there(self):
        write(os.path.join(self.root, "docs", "previews", "demo", "render-top.png"), PNG, "wb")
        for show in (["turn"], ["layers"]):
            with self.subTest(show=show):
                rnd = base()
                rnd["view"][0]["show"] = show
                self.put(1, rnd)
                self.assertEqual(self.build(render=False, refresh=True), 0, self.said)
                self.assertIn('{"name":"demo","note":"Stills from pcbkit previews, not checked against the board file",'
                              '"modes":[{"kind":"pics","name":"Pictures"', self.page())

    def test_without_a_renderer_the_previews_stills_stand_in(self):
        write(os.path.join(self.root, "docs", "previews", "demo", "render-top.png"), PNG, "wb")
        self.put(1, base())
        self.assertEqual(self.build(render=False), 0, self.said)
        self.assertEqual(self.calls, [])
        page = self.page()
        self.assertIn('"kind":"pics","name":"Pictures","pics":[{"label":"Front"', page)
        # they don't say which board they are of: said once when they are taken, and on the page
        self.assertIn("don't say which version of the board they show", "\n".join(self.said))
        self.assertIn("Stills from pcbkit previews, not checked against the board file", page)

    def stills(self, content, source=None):
        d = os.path.join(self.root, "docs", "previews", "demo")
        write(os.path.join(d, "render-top.png"), content, "wb")
        if source is not None:
            write(os.path.join(d, "SOURCE.txt"), source)

    def test_stills_that_are_of_this_board_need_no_remark(self):
        pcb = os.path.join(self.root, "boards", "demo", "demo.kicad_pcb")
        self.stills(PNG, versions.preview_source(pcb))
        self.put(1, base())
        self.assertEqual(self.build(render=False), 0, self.said)
        self.assertNotIn("watch:", "\n".join(self.said))
        self.assertNotIn("not checked against the board file", self.page())

    def test_stills_of_another_version_of_the_board_are_refused(self):
        self.stills(PNG, "0000000000000000000000000000000000000000  demo.kicad_pcb\n")
        self.put(1, base())
        self.assertEqual(self.build(render=False), 1)
        self.assertIn("are of another version of the board", "\n".join(self.said))
        self.assertFalse(os.path.exists(self.P["page"]))

    def test_other_stills_later_leave_an_earlier_rounds_stills_alone(self):
        self.stills(PNG + b"first")
        self.put(1, base())
        self.assertEqual(self.build(render=False), 0, self.said)
        one = self.index()["r1"]["sets"]["demo"]
        self.stills(PNG + b"second")                                   # the previews were made again; the board file is the same
        self.put(2, base())
        self.assertEqual(self.build(render=False), 0, self.said)
        sets = self.index()
        self.assertEqual(sets["r1"]["sets"]["demo"], one)
        self.assertNotEqual(sets["r2"]["sets"]["demo"], one)
        for rnd, content in (("r1", b"first"), ("r2", b"second")):
            with open(os.path.join(self.P["img"], sets[rnd]["sets"]["demo"], "render-top.png"), "rb") as fh:
                self.assertEqual(fh.read(), PNG + content)

    def test_the_same_stills_are_one_set(self):
        self.stills(PNG)
        self.put(1, base())
        self.put(2, base())
        self.assertEqual(self.build(render=False), 0, self.said)
        sets = self.index()
        self.assertEqual(sets["r1"]["sets"], sets["r2"]["sets"])

    def test_no_pictures_at_all_is_a_problem(self):
        self.put(1, base())
        self.assertEqual(self.build(render=False), 1)
        self.assertIn("no pictures of demo", "\n".join(self.said))


class NewRound(Case):
    def test_round_one_is_a_skeleton_with_the_boards(self):
        path = overview.new_round(self.project, self.P, today=D(2026, 11, 17))
        self.assertTrue(path.endswith(os.path.join("docs", "overview", "r1.toml")))
        rnd = tomlw.load(path)
        self.assertEqual((rnd["date"], rnd["title"]), (D(2026, 11, 17), "Demo sign"))
        self.assertEqual([(b["id"], b["version"]) for b in rnd["board"]], [("demo", "v1.0")])
        self.assertEqual(rnd["view"], [dict(board="demo")])
        self.assertIn("label is missing", "\n".join(self.check()[0]))  # it says what is still to write

    def test_the_next_round_carries_what_stands(self):
        one = base()
        one["topic"].append(dict(one["topic"][0], id="mcu", title="MCU"))
        one["decision"].append(dict(one["decision"][0], id="finish", question="Which finish?"))
        one["note"] = [dict(date=D(2026, 11, 17), on="power", mark="done", state="measured", text="Fine."),
                       dict(date=D(2026, 11, 17), on="antenna", chose="b", text="Decided (D2)."),
                       dict(date=D(2026, 11, 18), on="status", mark="work", state="boards ordered", text="Paid.")]
        self.put(1, one)
        self.assertEqual(self.build(), 0, self.said)
        rnd = tomlw.load(overview.new_round(self.project, self.P, today=D(2026, 12, 1)))
        self.assertEqual((rnd["date"], rnd["label"], rnd["mark"], rnd["state"]), (D(2026, 12, 1), "", "work", "boards ordered"))
        self.assertEqual([(t["id"], t["mark"], t["state"]) for t in rnd["topic"]],
                         [("power", "done", "measured"), ("mcu", "work", "waits for the bench")])
        self.assertEqual([d["id"] for d in rnd["decision"]], ["finish"])   # the decided one has gone
        self.assertNotIn("mark", rnd["decision"][0])
        self.assertNotIn("note", rnd)
        self.assertNotIn("decided", rnd)
        self.assertEqual(rnd["view"], one["view"])

    def test_the_next_round_keeps_a_decision_that_waits(self):
        one = base()
        one["decision"] += [dict(one["decision"][0], id="finish", question="Which finish?"),
                            dict(one["decision"][0], id="case", question="Which case?")]
        one["note"] = [dict(date=D(2026, 11, 17), on="antenna", mark="blocked", state="waits for the range test", text="Measure first."),
                       dict(date=D(2026, 11, 17), on="finish", mark="attention", text="The quote is late."),
                       dict(date=D(2026, 11, 18), on="case", mark="done", text="Dropped: no case.")]
        self.put(1, one)
        self.assertEqual(self.build(), 0, self.said)
        path = overview.new_round(self.project, self.P, today=D(2026, 12, 1))
        rnd = tomlw.load(path)
        self.assertEqual([(d["id"], d.get("mark"), d.get("state")) for d in rnd["decision"]],
                         [("antenna", "blocked", "waits for the range test"), ("finish", "attention", None)])
        rnd.update(label="Next", state="waiting", summary="Two decisions wait.")
        write(path, overview.write_round(rnd))
        self.assertEqual(self.check()[0], [])
        self.assertEqual([d["id"] for d in overview.open_decisions(overview.load_rounds(self.P["dir"])[1])], ["antenna", "finish"])

    def test_the_records_bring_a_released_board_up_to_date(self):
        os.makedirs(os.path.join(self.root, "releases", "demo"))
        tomlw.dump(dict(key="demo", release=[dict(version="1.1.0", label="v1.1", date=D(2026, 11, 20), source="x", note="n")]),
                   os.path.join(self.root, "releases", "demo", "releases.toml"))
        self.put(1, base())
        self.assertEqual(self.build(), 0, self.said)
        rnd = tomlw.load(overview.new_round(self.project, self.P, today=D(2026, 12, 1)))
        self.assertEqual((rnd["board"][0]["version"], rnd["board"][0]["source"]), ("v1.1", "releases/demo/releases.toml"))

    def test_what_is_written_reads_back_the_same(self):
        r = base()
        r["summary"] = 'He said "no"; C:\\path, a tab\there, ünïcode 5×7 → Ω. ' + "A long sentence that has to be wrapped over lines. " * 6
        r["topic"][0]["text"] = "One paragraph.\n\nAnother, with  two spaces and a trailing quote\""
        r["next"] = ["a \"quoted\" step", "b"]
        self.put(1, r)
        back = {k: v for k, v in overview.load_rounds(self.P["dir"])[0].items() if not k.startswith("_")}
        self.assertEqual(back, r)
        with open(os.path.join(self.P["dir"], "r1.toml"), encoding="utf-8") as fh:
            self.assertLess(max(len(line) for line in fh), 125)


class Seal(Case):
    def test_an_earlier_round_only_gains_notes(self):
        self.put(1, base())
        self.put(2, base())
        self.assertEqual(self.build(), 0, self.said)
        one = base()
        one["note"] = [dict(date=D(2026, 11, 20), on="power", text="Measured.")]
        self.put(1, one)
        self.assertEqual(self.check()[0], [])
        one["summary"] = "Rewritten later."
        self.put(1, one)
        self.assertIn("changed after the next round began", "\n".join(self.check()[0]))

    def test_an_earlier_rounds_notes_are_added_to_and_not_changed(self):
        chose_b = dict(date=D(2026, 11, 17), on="antenna", chose="b", text="Decided: u.FL (D2).")
        paid = dict(date=D(2026, 11, 18), on="status", text="Paid.")
        one = dict(base(), note=[chose_b, paid])
        self.put(1, one)
        self.put(2, base())
        self.assertEqual(self.build(), 0, self.said)
        changed = [("changed", [dict(chose_b, chose="a"), paid], "note 1 of the 2"),      # history rewritten: it chose b
                   ("reworded", [chose_b, dict(paid, text="Paid, late.")], "note 2 of the 2"),
                   ("removed", [paid], "note 1 of the 2"),
                   ("all removed", [], "note 1 of the 2"),
                   ("moved", [paid, chose_b], "note 1 of the 2")]
        for what, notes, want in changed:
            with self.subTest(what=what):
                self.put(1, dict(base(), note=notes))
                problems = "\n".join(self.check()[0])
                self.assertIn(want, problems)
                self.assertIn("added to, never edited", problems)
        added = dict(date=D(2026, 11, 20), on="antenna", text="Correction: the cable is 150 mm, not 100.")
        self.put(1, dict(base(), note=[chose_b, paid, added]))
        self.assertEqual(self.check()[0], [])
        self.assertEqual(self.build(), 0, self.said)                   # and the build seals the one that was added
        self.put(1, dict(base(), note=[chose_b, paid, dict(added, text="Correction: 120 mm.")]))
        self.assertIn("note 3 of the 3", "\n".join(self.check()[0]))

    def test_a_round_sealed_before_notes_were_kept_is_sealed_with_them_at_the_next_build(self):
        note = dict(date=D(2026, 11, 17), on="status", text="Paid.")
        self.put(1, dict(base(), note=[note]))
        self.put(2, base())
        self.assertEqual(self.build(), 0, self.said)
        index = tomlw.load(self.P["index"])
        del index["round"]["r1"]["notes"]                              # as an index written before this was
        overview.save_index(self.P, index)
        self.put(1, dict(base(), note=[dict(note, text="Paid twice.")]))
        self.assertEqual(self.check()[0], [])                          # nothing to compare with yet
        self.assertEqual(self.build(), 0, self.said)
        self.put(1, dict(base(), note=[note]))
        self.assertIn("note 1 of the 1", "\n".join(self.check()[0]))

    def test_the_newest_round_is_still_open(self):
        self.put(1, base())
        self.assertEqual(self.build(), 0, self.said)
        self.put(1, dict(base(), summary="Better words."))
        self.assertEqual(self.build(), 0, self.said)
        self.assertIn("Better words.", self.page())

    def test_starting_a_round_seals_the_one_before(self):
        self.put(1, base())
        overview.new_round(self.project, self.P, today=D(2026, 12, 1))
        self.assertIn("seal", tomlw.load(self.P["index"])["round"]["r1"])


class Note(Case):
    def test_it_is_appended_to_the_newest_round(self):
        path = os.path.join(self.P["dir"], "r2.toml")
        self.put(1, base())
        self.put(2, base())
        with open(path, "rb+") as fh:                                  # a file a person left without its last newline
            fh.seek(-1, os.SEEK_END)
            fh.truncate()
        with open(path, encoding="utf-8") as fh:
            before = fh.read()
        overview.add_note(self.P, 'Decided: u.FL ("D2").', on="antenna", chose="b", today=D(2026, 11, 18))
        with open(path, encoding="utf-8") as fh:
            after = fh.read()
        self.assertTrue(after.startswith(before + "\n"))
        self.assertEqual(tomlw.load(path)["note"], [dict(date=D(2026, 11, 18), on="antenna", chose="b", text='Decided: u.FL ("D2").')])
        self.assertNotIn("note", tomlw.load(os.path.join(self.P["dir"], "r1.toml")))

    def test_it_can_go_to_an_earlier_round_with_a_mark_and_a_state(self):
        self.put(1, base())
        self.put(2, base())
        overview.add_note(self.P, "Measured.", on="power", mark="done", state="measured", round_n=1, today=D(2026, 11, 18))
        self.assertEqual(overview.effective(overview.load_rounds(self.P["dir"])[0], "power"), ("done", "", "measured"))

    def test_a_note_that_fits_nothing_changes_nothing(self):
        path = os.path.join(self.P["dir"], "r1.toml")
        self.put(1, base())
        with open(path, encoding="utf-8") as fh:
            before = fh.read()
        for kw in (dict(on="nothing"), dict(on="power", mark="fine"), dict(on="power", chose="a"), dict(on="antenna", chose="c"), dict(round_n=4)):
            with self.subTest(kw=kw), self.assertRaises(SystemExit):
                overview.add_note(self.P, "x", today=D(2026, 11, 18), **kw)
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), before)


class Check(Case):
    def main(self, *args):
        """The command as typed, in this project; (exit code, what it said on stderr)."""
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            try:
                overview.main(list(args) + ["--project", self.root])
                code = 0
            except SystemExit as e:
                code = e.code
        return code, err.getvalue()

    def test_check_is_refused_with_anything_that_writes(self):
        self.put(1, base())
        self.assertEqual(self.build(), 0, self.said)
        path = os.path.join(self.P["dir"], "r1.toml")
        with open(path, encoding="utf-8") as fh:
            before = fh.read()
        index = open(self.P["index"], encoding="utf-8").read()
        for args in (("note", "Paid.", "--check"), ("new", "--check"), ("--check", "--render"), ("--check", "--single")):
            with self.subTest(args=args):
                code, said = self.main(*args)
                self.assertEqual(code, 2)
                self.assertIn("--check only reads", said)
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), before)                        # no note was added
        self.assertEqual(sorted(f for f in os.listdir(self.P["dir"]) if f.endswith(".toml")), ["r1.toml"])   # and no round
        self.assertEqual(open(self.P["index"], encoding="utf-8").read(), index)
        self.assertEqual(self.main("--check")[0], 0)

    def test_check_does_not_go_looking_for_kicad(self):
        # pcbkit.kicad finds KiCad as it is imported, and on macOS may make its background copy of kicad-cli: writing
        # files and running commands. A released board's fingerprint and the page's template both used to import it.
        os.makedirs(os.path.join(self.root, "releases", "demo"))
        tomlw.dump(dict(key="demo", release=[dict(version="1.0.0", label="v1.0", date=D(2026, 11, 16), source="x", note="n")]),
                   os.path.join(self.root, "releases", "demo", "releases.toml"))
        self.put(1, base())
        self.assertEqual(self.build(), 0, self.said)
        code = ("import sys; sys.path.insert(0, %r)\n"
                "from pcbkit import overview, config\n"
                "fab = config.Project(%r).fab_settings(dict(config.DEFAULT_PROFILE))\n"
                "rc = overview.build(config.Project(%r), check=True, say=lambda line: None)\n"
                "sys.exit(3 if 'pcbkit.kicad' in sys.modules else 4 if 'rotations' not in fab else rc)\n" % (ROOT, self.root, self.root))
        self.assertEqual(subprocess.run([sys.executable, "-c", code]).returncode, 0)

    def test_the_page_must_be_what_the_rounds_say(self):
        self.put(1, base())
        self.assertEqual(self.build(check=True), 1)
        self.assertIn("its pictures are not made yet", "\n".join(self.said))
        self.assertEqual(self.build(), 0, self.said)
        self.assertEqual(self.build(check=True), 0, self.said)
        overview.add_note(self.P, "Paid.", today=D(2026, 11, 18))
        self.assertEqual(self.build(check=True), 1)
        self.assertIn("docs/overview.html is not what the rounds say", "\n".join(self.said))

    def test_a_check_tells_what_is_wrong_with_a_round_from_what_a_build_does(self):
        # the skill's order: check the round, build, check again. Before the first build only the build is left
        self.put(1, dict(base(), mark="ok"))
        self.assertEqual(self.build(check=True), 1)
        self.assertEqual([line.split(":")[0] for line in self.said], ["problem"])       # the round first: nothing about building yet
        self.put(1, base())
        self.assertEqual(self.build(check=True), 1)
        self.assertEqual(self.said, ["to build: r1: its pictures are not made yet (pcbkit overview)"])
        self.assertEqual(self.build(), 0, self.said)
        self.assertEqual(self.build(check=True), 0)
        self.assertEqual(self.said, [])                                # and after the build the check is silent
        overview.add_note(self.P, "Paid.", today=D(2026, 11, 18))
        self.assertEqual(self.build(check=True), 1)
        self.assertEqual(self.said, ["to build: docs/overview.html is not what the rounds say (pcbkit overview)"])

    def test_check_writes_nothing(self):
        self.put(1, base())
        self.build(check=True)
        self.assertFalse(os.path.exists(self.P["page"]))
        self.assertFalse(os.path.exists(self.P["img"]))


class Wiring(unittest.TestCase):
    def test_every_state_the_records_use_has_a_mark(self):
        self.assertEqual(set(overview.ORDER_MARK), set(production.STATUSES))
        with open(production.__file__, encoding="utf-8") as fh:
            states = set(re.findall(r'p\["state"\] = "([^"]+)"', fh.read()))
        self.assertTrue(states)
        self.assertEqual(states - {"excluded"} - set(overview.BOARD_MARK), set())
        for table in (overview.ORDER_MARK, overview.BOARD_MARK):
            self.assertLessEqual(set(table.values()), set(overview.MARK))

    def test_the_command_is_registered(self):
        self.assertEqual(cli.COMMANDS["overview"][0], "pcbkit.overview")

    def test_the_template_is_package_data(self):
        from pcbkit.kicad import data_path
        with open(data_path("overview", "page.html"), encoding="utf-8") as fh:
            page = fh.read()
        for token in ("__TITLE__", "__ABOUT__", "__NAV__", "__LEGEND__", "__ROUNDS__", "__NEWEST__"):
            self.assertIn(token, page)
        for mark, _ in overview.MARKS:
            self.assertIn('id="g-%s"' % mark, page)
            self.assertIn(".mk-%s " % mark, page)
        self.assertIn("ArrowUp: [0, 1]", page)                         # a zoomed view moves by the arrow keys too
        self.assertIn("use the arrow keys to move", page)


if __name__ == "__main__":
    unittest.main()
