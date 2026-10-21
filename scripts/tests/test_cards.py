"""Unit tests 1-6 of design.md section 8 / plan.md section 2.3.

Run from the repository root:
    python -m unittest discover -s scripts/tests -t scripts -v

Every CLI-level test replays a scenario file through FixtureTransport with
--now 2026-10-21T02:43:00Z (= 21 Oct 2026, 08:13 IST) into a temporary
--out directory and a temporary --readme copy. Unsupported characters and the
forbidden first name are written as escapes so this file stays ASCII-clean.
"""

import contextlib
import copy
import html.parser
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path

import cardkit as ck
import refresh_cards as rc

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
REPO = HERE.parent.parent
NOW = "2026-10-21T02:43:00Z"
CARD_TIME = "21 Oct 2026, 08:13 IST"
MD = "\u00b7"
ELL = "\u2026"
B64_RE = re.compile(r"base64,[A-Za-z0-9+/=]+")
HEX_RE = re.compile(r"#[0-9a-fA-F]{6}")
BLOCK_RE = re.compile(r"<!-- card:(\w+):start -->\n(.*?)<!-- card:\1:end -->", re.S)

META = "https://sudish007.github.io/ahead/data/meta.json"
INDEX = "https://sudish007.github.io/ahead/data/digests/index.json"
DIGEST = "https://sudish007.github.io/ahead/data/digests/2026-10-20.json"
STATS = "https://sudish007.github.io/startup-radar/data/stats.json"
SOURCES = "https://sudish007.github.io/startup-radar/data/sources.json"
GRAPHQL = "https://api.github.com/graphql"
PRODUCT_URLS = [url for _, url in rc.PRODUCTS]

# Check-12 set A list (plan.md section 9 row 12). One character of each pattern is written as a \u escape so the
# repo-wide grep does not match this test file; the Python values are the exact patterns.
SET_A = ["\u003cscript", "\u003canimate", "\u003cset ", "\u0040keyframes", "filter\u003d", "filter\u003a",
         "\u003cfilter", "Gr\u0061dient", "\u003cimage", "url\u0028http", 'xlink\u003ahref="http']

# Design 3.2-3.5 example strings (research values replayed by success.json).
PRODUCTS_ALT = ("Live products, checked 21 Oct 2026, 08:13 IST from GitHub Actions: 6 of 6 reachable. "
                "KaatDo HTTP 200 in 313 ms; RishtaPatra HTTP 200 in 1,644 ms; BhojVerse HTTP 200 in 482 ms; "
                "Telusa HTTP 200 in 473 ms; Sri Sai Hospital HTTP 200 in 371 ms; Saubhagya Clinic HTTP 200 in 4,732 ms.")
PRODUCTS_CAPTION = "Checked 21 Oct 2026, 08:13 IST from GitHub Actions: 6 of 6 product sites reachable.[^probe]"
AHEAD_ALT = ("Ahead, data generated 20 Oct 2026, 22:12 IST: 893 items from 64 sources (43 ok, 21 empty, 0 failed). "
             "Brief of 2026-10-20: 15 entries, 4 min read. Top entry: DeepSeek just open-sourced its inference engine.")
AHEAD_CAPTION = ("Ahead data generated 20 Oct 2026, 22:12 IST: 893 items, 43 of 64 sources ok; "
                 "brief of 2026-10-20, 15 entries.[^systems]")
RADAR_ALT = ("Startup Radar, data generated 20 Oct 2026, 20:35 IST: 3,671 items in the 90-day feed, 252 in the last "
             "24 hours, 1,579 in the last 7 days; 18 of 19 enabled sources ok on their last run; not ok: "
             "launchingnext: HTTP 403.")
RADAR_CAPTION = ("Startup Radar data generated 20 Oct 2026, 20:35 IST: 3,671 items in the 90-day feed, "
                 "18 of 19 sources ok.[^systems]")
# Recorded calendar: 440 contributions, 53 weeks, 41 active days, 2025-10-21 to 2026-10-21.
CALENDAR_ALT = ("GitHub contributions from 2025-10-21 to 2026-10-21: 440 total, 41 days with at least one "
                "contribution. Refreshed 21 Oct 2026, 08:13 IST.")
CALENDAR_CAPTION = ("440 contributions between 2025-10-21 and 2026-10-21, 41 active days; "
                    "refreshed 21 Oct 2026, 08:13 IST.[^calendar]")


def success_records():
    return copy.deepcopy(json.loads((FIXTURES / "success.json").read_bytes().decode("ascii"))["http"])


def make_calendar(weeks, start, total=None):
    """Synthetic GraphQL response with `weeks` full weeks starting on the Sunday `start`."""
    day = start
    out = []
    count = 0
    for w in range(weeks):
        days = []
        for wd in range(7):
            level = ["NONE", "FIRST_QUARTILE", "SECOND_QUARTILE", "THIRD_QUARTILE", "FOURTH_QUARTILE"][(w + wd) % 5]
            n = 0 if level == "NONE" else (w + wd) % 5
            count += n
            days.append({"date": day.isoformat(), "contributionCount": n, "contributionLevel": level, "weekday": wd})
            day += timedelta(days=1)
        out.append({"contributionDays": days})
    calendar = {"totalContributions": total if total is not None else count, "weeks": out}
    return {"data": {"user": {"contributionsCollection": {"contributionCalendar": calendar}}}}


class AltParser(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.alts = []

    def handle_starttag(self, tag, attrs):
        if tag == "img":
            self.alts.append(dict(attrs).get("alt"))


def readme_blocks(text):
    """{card_id: (alt, caption)} parsed back from a rewritten README."""
    blocks = {}
    for card_id, body in BLOCK_RE.findall(text):
        if not body.strip():
            continue
        parser = AltParser()
        parser.feed(body)
        caption = [line for line in body.split("\n") if line.strip()][-1]
        blocks[card_id] = (parser.alts[0], caption)
    return blocks


def all_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from all_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from all_keys(v)


class Run:
    def __init__(self, code, out, err, out_dir, readme):
        self.code, self.out, self.err, self.out_dir, self.readme_path = code, out, err, out_dir, readme

    def svg(self, name):
        return (self.out_dir / f"{name}.svg").read_bytes().decode("utf-8")

    def svg_text(self, name):
        """SVG markup with the font payload stripped."""
        return B64_RE.sub("base64,", self.svg(name))

    @property
    def readme(self):
        return self.readme_path.read_bytes().decode("utf-8")

    @property
    def data(self):
        return json.loads((self.out_dir / "data.json").read_bytes().decode("ascii"))

    def title(self, name):
        root = ET.fromstring(self.svg(name).encode("utf-8"))
        return root.find("{http://www.w3.org/2000/svg}title").text

    def artefacts(self):
        texts = [self.out, self.readme]
        texts.append((self.out_dir / "data.json").read_bytes().decode("ascii"))
        for p in self.out_dir.glob("*.svg"):
            texts.append(B64_RE.sub("base64,", p.read_bytes().decode("utf-8")))
        return texts


class CardsTestCase(unittest.TestCase):
    def tmpdir(self):
        path = Path(tempfile.mkdtemp(prefix="cards-test-"))
        self.addCleanup(shutil.rmtree, path, ignore_errors=True)
        return path

    def run_cli(self, records=None, scenario_path=None, readme_text=None):
        tmp = self.tmpdir()
        if scenario_path is None:
            scenario_path = tmp / "scenario.json"
            scenario_path.write_bytes(json.dumps({"http": records}, ensure_ascii=True).encode("ascii"))
        readme = tmp / "README.md"
        if readme_text is None:
            readme_text = (REPO / "README.md").read_bytes().decode("utf-8")
        readme.write_bytes(readme_text.encode("utf-8"))
        out_dir = tmp / "cards"
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
            try:
                code = rc.main(["--fixtures", str(scenario_path), "--now", NOW, "--out", str(out_dir),
                                "--readme", str(readme)])
            except SystemExit as exc:
                code = exc.code
        return Run(code, buf_out.getvalue(), buf_err.getvalue(), out_dir, readme)

    def run_ok(self, records=None, scenario_path=None):
        run = self.run_cli(records, scenario_path)
        self.assertEqual(run.code, 0, run.err)
        return run

    def assert_captions_ok(self, run):
        for card_id, vm in run.data["cards"].items():
            self.assertRegex(vm["caption"], rc.CAPTION_RE, card_id)


# --- test 1: fit, glyphs_ok, screen, fit_width -------------------------------------------------


class TestFitAndScreen(unittest.TestCase):
    def test_fit_short_unchanged(self):
        title = "a" * 41
        self.assertEqual(ck.fit(title, 42), title)
        self.assertEqual(ck.fit("", 42), "")

    def test_fit_sentence_cuts_at_word_boundary(self):
        text = "one two three four five six seven eight nine ten eleven t"  # 57 chars
        text = text + "x" * (60 - len(text))
        self.assertEqual(len(text), 60)
        result = ck.fit(text, 42)
        i = text.rfind(" ", 21, 41)
        self.assertGreaterEqual(i, 21)
        self.assertEqual(result, text[:i].rstrip() + ELL)
        self.assertLessEqual(len(result), 42)
        self.assertTrue(result.endswith(ELL))

    def test_fit_single_token_hard_cut(self):
        text = "https://example.invalid/" + "a" * 36
        self.assertEqual(len(text), 60)
        result = ck.fit(text, 42)
        self.assertEqual(result, text[:41] + ELL)
        self.assertEqual(len(result), 42)

    def test_fit_early_space_only(self):
        text = "abc " + "x" * 70
        result = ck.fit(text, 42)
        self.assertNotEqual(result, "abc" + ELL)
        self.assertEqual(result, text[:41] + ELL)

    def test_fit_worked_examples(self):
        self.assertEqual(ck.fit("launchingnext: HTTP 403 for https://www.launchingnext.com/rss/", 55),
                         "launchingnext: HTTP 403 for" + ELL)
        self.assertEqual(len(ck.fit("launchingnext: HTTP 403 for https://www.launchingnext.com/rss/", 55)), 28)

    def test_glyphs_ok(self):
        for bad in ("\U0001F600", "\u4e2d", "\u0939", "\u2192"):
            self.assertFalse(ck.glyphs_ok("a" + bad), repr(bad))
        self.assertTrue(ck.glyphs_ok("\u2018q\u2019 \u201cq\u201d \u2013 \u2014 \" < & \u00b7 \u2026 caf\u00e9"))

    def test_screen(self):
        self.assertEqual(ck.screen("DeepSeek open-sourced it"), ("DeepSeek open-sourced it", None))
        self.assertEqual(ck.screen("hello \U0001F600"), (None, "unsupported characters"))
        self.assertEqual(ck.screen("N\u0065ha's pick"), (None, "filtered"))  # D12-3, case-insensitive
        self.assertEqual(ck.screen("about SN\u0045H today"), (None, "filtered"))
        self.assertEqual(ck.screen("fork of Sudish007/regist\u0065r"), (None, "filtered"))
        self.assertEqual(ck.screen("my is-a.d\u0065v page"), (None, "filtered"))
        self.assertEqual(ck.screen("The Gr\u0061dient weekly"), (None, "filtered"))  # set A pattern in a fetched string
        self.assertEqual(ck.screen("gradient descent, plain"), ("gradient descent, plain", None))

    def test_clean_strips_controls_and_soft_hyphen(self):
        self.assertEqual(ck.clean("soft\u00adhyphen"), "softhyphen")  # D12-7
        self.assertEqual(ck.clean("  a\x00b\x1f  c\x7f\x9f   d\t\ne "), "ab c d e")
        self.assertEqual(ck.text_width(ck.clean("soft\u00adhyphen"), 600, 36), ck.text_width("softhyphen", 600, 36))
        self.assertNotIn(0xAD, ck.INTER_ADV[600])

    def test_inter_advances(self):
        adv = ck.INTER_ADV[600]
        self.assertEqual(adv[ord("W")], 1020)
        self.assertEqual(adv[ord("M")], 922)
        self.assertEqual(adv[ord("A")], 728)
        self.assertEqual(adv[ord("i")], 262)
        self.assertEqual(adv[ord("l")], 262)
        self.assertEqual(adv[ord(" ")], 252)
        self.assertEqual(adv[0xB7], 319)
        self.assertEqual(adv[0x2026], 956)
        self.assertEqual(adv[ord("0")], 660)
        self.assertEqual(adv[ord("1")], 423)
        for weight in (500, 600, 700):
            missing = [cp for cp in sorted(ck.ALLOWED_CP) if cp not in ck.INTER_ADV[weight]]
            self.assertEqual(missing, [0xAD], weight)
        self.assertAlmostEqual(ck.text_width("W", 600, 36), 36.72)
        self.assertAlmostEqual(ck.text_width("AB", 600, 32, 2), (728 + 659) * 32 / 1000 + 4)

    def test_fit_width(self):
        wide = "W" * 42
        self.assertGreater(ck.text_width(wide, 600, 36), 1072)
        fitted = ck.fit_width(wide, 1072, 600, 36)
        self.assertTrue(fitted.endswith(ELL))
        self.assertLess(len(fitted), 42)
        self.assertLessEqual(ck.text_width(fitted, 600, 36), 1072)
        narrow = "i" * 60
        self.assertEqual(ck.fit_width(narrow, 1072, 600, 36), narrow)
        words = "DeepInfer: High-performance inference framework for DeepSeek models"
        fitted = ck.fit_width(words, 1072, 600, 36)
        self.assertTrue(fitted.endswith(ELL))
        self.assertFalse(fitted.endswith(" " + ELL))
        self.assertLessEqual(ck.text_width(fitted, 600, 36), 1072)
        self.assertEqual(fitted[:-1], words[:len(fitted) - 1].rstrip())  # cut at a word boundary


# --- test 2: fmt_ist and the IST scan -------------------------------------------------------------


class TestFmtIst(CardsTestCase):
    def test_conversions(self):
        self.assertEqual(ck.fmt_ist("2026-10-20T16:42:58Z"), "20 Oct 2026, 22:12 IST")
        self.assertEqual(ck.fmt_ist("2026-10-20T21:13:00Z"), "21 Oct 2026, 02:43 IST")
        self.assertEqual(ck.fmt_ist("2026-10-20T15:05:55.990Z"), "20 Oct 2026, 20:35 IST")
        self.assertEqual(ck.fmt_ist("2026-10-21T02:43:00Z"), CARD_TIME)
        self.assertEqual(ck.fmt_ist("2026-10-05T00:00:00"), "05 Oct 2026, 05:30 IST")  # naive = UTC, zero-padded day
        for bad in (None, "", "not-a-date", "garbage", 12345, {"a": 1}):
            self.assertEqual(ck.fmt_ist(bad), "time unknown", repr(bad))

    def test_no_double_ist_in_artefacts(self):
        records = success_records()
        records[META]["json"]["generatedAt"] = "garbage"
        for run in (self.run_ok(success_records()), self.run_ok(records)):
            for text in run.artefacts():
                self.assertNotIn("IST IST", text)
                self.assertNotIn("unknown IST", text)
        self.assertIn(f"data time unknown {MD} card {CARD_TIME}", run.svg_text("ahead-dark"))
        self.assertTrue(run.data["cards"]["ahead"]["alt"].startswith("Ahead, data generated time unknown:"))
        self.assertEqual(run.data["cards"]["ahead"]["data_time"], "time unknown")


# --- test 3: builders per scenario ------------------------------------------------------------------


class TestBuildersSuccess(CardsTestCase):
    def test_success_scenario(self):
        run = self.run_ok(scenario_path=FIXTURES / "success.json")
        cards = run.data["cards"]
        self.assertEqual(cards["products"]["alt"], PRODUCTS_ALT)
        self.assertEqual(cards["products"]["caption"], PRODUCTS_CAPTION)
        self.assertEqual(cards["ahead"]["alt"], AHEAD_ALT)
        self.assertEqual(cards["ahead"]["caption"], AHEAD_CAPTION)
        self.assertEqual(cards["radar"]["alt"], RADAR_ALT)
        self.assertEqual(cards["radar"]["caption"], RADAR_CAPTION)
        self.assertEqual(cards["calendar"]["alt"], CALENDAR_ALT)
        self.assertEqual(cards["calendar"]["caption"], CALENDAR_CAPTION)
        self.assert_captions_ok(run)
        products = run.svg_text("products-dark")
        self.assertIn('<tspan fill="#06b6d4">6 of 6</tspan> reachable', products)
        self.assertIn(f">HTTP 200 {MD} 313 ms</text>", products)
        self.assertIn(f">HTTP 200 {MD} 4,732 ms</text>", products)
        self.assertIn(f">card {CARD_TIME} {MD} GET from GitHub Actions</text>", products)
        self.assertEqual(products.count('<rect x="1116"'), 6)
        ahead = run.svg_text("ahead-light")
        self.assertIn('<tspan fill="#0e7490">893</tspan> items ' + MD + " 64 sources", ahead)
        self.assertIn(f">sources: 43 ok {MD} 21 empty {MD} 0 failed</text>", ahead)
        self.assertIn(f">Brief 2026-10-20 {MD} 15 entries {MD} 4 min read</text>", ahead)
        self.assertIn(">DeepSeek just open-sourced its inference engine</text>", ahead)
        self.assertIn(f">data 20 Oct 2026, 22:12 IST {MD} card {CARD_TIME}</text>", ahead)
        radar = run.svg_text("radar-dark")
        self.assertIn(f'<tspan fill="#06b6d4">3,671</tspan> items {MD} 90-day feed', radar)
        self.assertIn(f">252 last 24 h {MD} 1,579 last 7 d</text>", radar)
        self.assertIn(">sources: 18 of 19 enabled ok on last run</text>", radar)
        self.assertIn(">launchingnext: HTTP 403</text>", radar)
        self.assertIn(f">data 20 Oct 2026, 20:35 IST {MD} card {CARD_TIME}</text>", radar)
        calendar = run.svg_text("calendar-dark")
        self.assertIn('<tspan fill="#06b6d4">440</tspan> contributions', calendar)
        self.assertIn(">41 days with at least one contribution</text>", calendar)
        self.assertIn(f">2025-10-21 to 2026-10-21 {MD} card {CARD_TIME}</text>", calendar)
        self.assertEqual(calendar.count('rx="3"'), 366)  # one day cell per returned day
        for line in ("probe kaatdo.com 200 313ms ok", "probe sudish007.github.io/bhojverse-site 200 482ms ok",
                     "fetch ahead/meta.json 200 472ms", "graphql 200 weeks=53 total=440 active=41",
                     "svg products-dark ", "readme blocks=4 ok", f"stamp {CARD_TIME}"):
            self.assertIn(line, run.out)
        for line in run.out.splitlines():
            self.assertTrue(line.isascii(), line)

    def test_radar_live_error_fixture(self):
        run = self.run_ok(scenario_path=FIXTURES / "radar_live_error.json")
        fitted = "launchingnext: HTTP 403 for" + ELL
        self.assertIn(f">{fitted}</text>", run.svg_text("radar-light"))
        self.assertTrue(run.data["cards"]["radar"]["alt"].endswith(f"; not ok: {fitted}."))
        self.assertNotIn("launchingnext.com", run.data["cards"]["radar"]["alt"])
        self.assertEqual(run.data["cards"]["radar"]["caption"], RADAR_CAPTION)


class TestBuildersProducts(CardsTestCase):
    def test_failure_rows(self):
        records = success_records()
        records[PRODUCT_URLS[0]] = {"status": 200, "ms": 21304, "text": "ok"}
        records[PRODUCT_URLS[1]] = {"exception": "httperror:403", "ms": 120}
        records[PRODUCT_URLS[2]] = {"exception": "timeout", "ms": 20000}
        records[PRODUCT_URLS[3]] = {"exception": "gaierror", "ms": 30}
        records[PRODUCT_URLS[4]] = {"exception": "sslerror", "ms": 300}
        records[PRODUCT_URLS[5]] = {"exception": "connection", "ms": 10}
        run = self.run_ok(records)
        rows = run.data["cards"]["products"]["rows"]
        self.assertEqual([r["line"] for r in rows], [
            f"FAIL {MD} timeout 20 s", f"FAIL {MD} HTTP 403", f"FAIL {MD} timeout 20 s",
            f"FAIL {MD} DNS error", f"FAIL {MD} TLS error", f"FAIL {MD} connection error"])
        self.assertEqual(rows[0]["status"], 200)
        self.assertEqual(rows[0]["ms"], 21304)
        self.assertEqual(rows[1]["status"], 403)
        svg = run.svg_text("products-light")
        self.assertIn('<tspan fill="#0e7490">0 of 6</tspan> reachable', svg)
        self.assertEqual(svg.count('fill="none" stroke="#101218" stroke-width="3"'), 6)
        alt = run.data["cards"]["products"]["alt"]
        self.assertTrue(alt.startswith(f"Live products, checked {CARD_TIME} from GitHub Actions: 0 of 6 reachable. "))
        self.assertEqual(alt.count("failed ("), 6)
        self.assertIn("Saubhagya Clinic failed (connection error).", alt)
        self.assertEqual(run.data["cards"]["products"]["caption"],
                         f"Checked {CARD_TIME} from GitHub Actions: 0 of 6 product sites reachable.[^probe]")
        self.assertIn("probe kaatdo.com 200 21304ms FAIL timeout 20 s", run.out)
        self.assertIn("probe rishtapatra.com 403 ", run.out)
        self.assertIn("FAIL HTTP 403", run.out)
        self.assert_captions_ok(run)

    def test_read_timeout_and_partial(self):
        records = success_records()
        records[PRODUCT_URLS[5]] = {"exception": "readtimeout", "ms": 20000}
        run = self.run_ok(records)
        rows = run.data["cards"]["products"]["rows"]
        self.assertEqual(rows[5]["line"], f"FAIL {MD} timeout 20 s")
        self.assertEqual(run.data["cards"]["products"]["reachable"], 5)
        self.assertIn('<tspan fill="#06b6d4">5 of 6</tspan> reachable', run.svg_text("products-dark"))
        self.assertTrue(run.data["cards"]["products"]["alt"].endswith("Saubhagya Clinic failed (timeout 20 s)."))
        for row in rows:
            self.assertLessEqual(len(row["line"]), 23)


class TestBuildersAhead(CardsTestCase):
    def test_meta_failure(self):
        records = success_records()
        records[META] = {"exception": "httperror:503", "ms": 50}
        run = self.run_ok(records)
        vm = run.data["cards"]["ahead"]
        self.assertFalse(vm["ok"])
        self.assertEqual(vm["alt"], f"Ahead: data unavailable at {CARD_TIME} (HTTP 503).")
        self.assertEqual(vm["caption"], f"Ahead: data unavailable at {CARD_TIME}; the card shows the reason.[^systems]")
        svg = run.svg_text("ahead-dark")
        self.assertIn(">data unavailable</text>", svg)
        self.assertIn(">HTTP 503</text>", svg)
        self.assertIn(f">card {CARD_TIME}</text>", svg)
        body = svg.split("</style>")[1]
        self.assertNotIn("sources:", body)
        self.assertNotIn("TOP ENTRY", body)
        self.assertNotIn("Brief", body)
        self.assertIn("fetch ahead/meta.json 503 ", run.out)
        self.assert_captions_ok(run)

    def test_meta_missing_field(self):
        records = success_records()
        del records[META]["json"]["okCount"]
        run = self.run_ok(records)
        self.assertEqual(run.data["cards"]["ahead"]["alt"], f"Ahead: data unavailable at {CARD_TIME} (missing field okCount).")
        records = success_records()
        records[META]["json"]["itemCount"] = "893"
        run = self.run_ok(records)
        self.assertEqual(run.data["cards"]["ahead"]["reason"], "missing field itemCount")

    def test_brief_unavailable(self):
        records = success_records()
        records[INDEX] = {"exception": "httperror:404", "ms": 40}
        run = self.run_ok(records)
        vm = run.data["cards"]["ahead"]
        self.assertEqual(vm["state"], "brief unavailable")
        self.assertIn(">brief unavailable: HTTP 404</text>", run.svg_text("ahead-light"))
        self.assertNotIn("TOP ENTRY", run.svg_text("ahead-light"))
        self.assertTrue(vm["alt"].endswith("(43 ok, 21 empty, 0 failed). Brief unavailable (HTTP 404)."))
        self.assertTrue(vm["caption"].endswith("; brief unavailable, see the card.[^systems]"))
        self.assertEqual(vm["caption"], "Ahead data generated 20 Oct 2026, 22:12 IST: 893 items, 43 of 64 sources ok; "
                                        "brief unavailable, see the card.[^systems]")
        records = success_records()
        records[DIGEST] = {"status": 200, "ms": 10, "text": "<html>not json"}
        run = self.run_ok(records)
        self.assertIn(">brief unavailable: invalid JSON</text>", run.svg_text("ahead-light"))
        records = success_records()
        records[INDEX]["json"]["days"] = []
        run = self.run_ok(records)
        self.assertIn(">brief unavailable: missing field days</text>", run.svg_text("ahead-light"))
        records = success_records()
        del records[DIGEST]["json"]["sections"]
        run = self.run_ok(records)
        self.assertIn(">brief unavailable: missing field sections</text>", run.svg_text("ahead-light"))
        self.assert_captions_ok(run)

    def test_no_entries(self):
        records = success_records()
        for section in records[DIGEST]["json"]["sections"]:
            section["entries"] = []
        run = self.run_ok(records)
        vm = run.data["cards"]["ahead"]
        self.assertEqual(vm["state"], "no entries")
        self.assertEqual(vm["title_line"], "none in this brief")
        self.assertIn(">TOP ENTRY</text>", run.svg_text("ahead-dark"))
        self.assertIn(">none in this brief</text>", run.svg_text("ahead-dark"))
        self.assertTrue(vm["alt"].endswith("4 min read. No top entry in this brief."))
        self.assertEqual(vm["caption"], AHEAD_CAPTION)

    def test_title_unsupported_characters(self):
        records = success_records()
        records[DIGEST]["json"]["sections"][0]["entries"][0]["title"] = "Launch day \U0001F680 party"
        run = self.run_ok(records)
        vm = run.data["cards"]["ahead"]
        self.assertEqual(vm["state"], "title omitted")
        self.assertIn(">top entry omitted: unsupported characters</text>", run.svg_text("ahead-dark"))
        self.assertTrue(vm["alt"].endswith("Top entry omitted: unsupported characters."))
        raw = (run.out_dir / "data.json").read_bytes()
        self.assertTrue(raw.isascii())
        self.assertNotIn(b"Launch day", raw)
        self.assertNotIn(b"\\ud83d", raw.lower())

    def test_title_filtered_d12_3(self):
        records = success_records()
        records[DIGEST]["json"]["sections"][0]["entries"][0]["title"] = "N\u0065ha explains inference engines"
        run = self.run_ok(records)
        vm = run.data["cards"]["ahead"]
        self.assertEqual(vm["title_line"], "top entry omitted: filtered")
        self.assertIn(">top entry omitted: filtered</text>", run.svg_text("ahead-light"))
        self.assertTrue(vm["alt"].endswith("Top entry omitted: filtered."))
        self.assertNotIn("explains inference", (run.out_dir / "data.json").read_text(encoding="ascii"))
        self.assertNotIn("explains inference", run.readme)
        self.assertNotIn("explains inference", run.svg("ahead-light"))

    def test_first_titled_entry_d12_8(self):
        records = success_records()
        records[DIGEST]["json"]["sections"][0]["entries"] = [
            {"id": "a"}, {"id": "b", "title": ""}, {"id": "c", "title": 7}, {"id": "d", "title": "   "},
            {"id": "e", "title": "Fourth entry carries the title"}]
        run = self.run_ok(records)
        self.assertEqual(run.data["cards"]["ahead"]["title_line"], "Fourth entry carries the title")
        records[DIGEST]["json"]["sections"] = [{"key": "x", "entries": [{"id": "a"}, {"title": None}]},
                                               {"key": "y", "entries": "not a list"}]
        run = self.run_ok(records)
        self.assertEqual(run.data["cards"]["ahead"]["state"], "no entries")
        self.assertTrue(run.data["cards"]["ahead"]["alt"].endswith("No top entry in this brief."))

    def test_soft_hyphen_title_d12_7(self):
        records = success_records()
        records[DIGEST]["json"]["sections"][0]["entries"][0]["title"] = "Soft\u00adhyphen in\u00adside a title"
        run = self.run_ok(records)
        self.assertEqual(run.data["cards"]["ahead"]["title_line"], "Softhyphen inside a title")
        self.assertNotIn("\u00ad", run.svg("ahead-dark"))
        self.assertNotIn("\\u00ad", (run.out_dir / "data.json").read_text(encoding="ascii"))


class TestBuildersRadar(CardsTestCase):
    def test_stats_failure(self):
        records = success_records()
        records[STATS] = {"exception": "timeout", "ms": 20000}
        run = self.run_ok(records)
        vm = run.data["cards"]["radar"]
        self.assertEqual(vm["alt"], f"Startup Radar: data unavailable at {CARD_TIME} (timeout 20 s).")
        self.assertEqual(vm["caption"],
                         f"Startup Radar: data unavailable at {CARD_TIME}; the card shows the reason.[^systems]")
        svg = run.svg_text("radar-dark")
        self.assertIn(">data unavailable</text>", svg)
        self.assertIn(">timeout 20 s</text>", svg)
        self.assertIn(">STARTUP RADAR</text>", svg)
        self.assertIn(f">card {CARD_TIME}</text>", svg)
        self.assert_captions_ok(run)

    def test_source_health_unavailable(self):
        records = success_records()
        records[SOURCES] = {"status": 200, "ms": 12, "text": "{not json"}
        run = self.run_ok(records)
        vm = run.data["cards"]["radar"]
        self.assertEqual(vm["state"], "source health unavailable")
        self.assertIn(">source health unavailable: invalid JSON</text>", run.svg_text("radar-light"))
        self.assertNotIn("ENABLED SOURCES NOT OK", run.svg_text("radar-light"))
        self.assertTrue(vm["alt"].endswith("1,579 in the last 7 days; source health unavailable (invalid JSON)."))
        self.assertEqual(vm["caption"], "Startup Radar data generated 20 Oct 2026, 20:35 IST: 3,671 items in the "
                                        "90-day feed; source health unavailable, see the card.[^systems]")
        records = success_records()
        records[SOURCES]["json"] = {"nope": []}
        run = self.run_ok(records)
        self.assertIn(">source health unavailable: missing field sources</text>", run.svg_text("radar-light"))

    def test_not_yet_run_and_all_ok(self):
        records = success_records()
        sources = records[SOURCES]["json"]["sources"]
        for s in sources:
            if s["id"] == "launchingnext":
                s["lastError"] = None
            if s["id"] == "yc":
                s["lastRunAt"] = None
        run = self.run_ok(records)
        vm = run.data["cards"]["radar"]
        self.assertEqual(vm["not_ok_line"], "yc: not yet run")
        self.assertEqual(vm["ok_sources"], 18)
        self.assertTrue(vm["alt"].endswith("18 of 19 enabled sources ok on their last run; not ok: yc: not yet run."))
        for s in sources:
            if s["id"] == "yc":
                s["lastRunAt"] = "2026-10-20T15:05:57.000Z"
        run = self.run_ok(records)
        vm = run.data["cards"]["radar"]
        self.assertEqual(vm["not_ok_line"], "none")
        self.assertIn(">none</text>", run.svg_text("radar-dark"))
        self.assertNotIn("not ok:", vm["alt"])
        self.assertTrue(vm["alt"].endswith("19 of 19 enabled sources ok on their last run."))
        self.assertEqual(vm["caption"], "Startup Radar data generated 20 Oct 2026, 20:35 IST: 3,671 items in the "
                                        "90-day feed, 19 of 19 sources ok.[^systems]")

    def test_last_error_screening(self):
        records = success_records()
        sources = records[SOURCES]["json"]["sources"]
        for s in sources:
            if s["id"] == "launchingnext":
                s["lastError"] = "blocked by N\u0065ha"
            if s["id"] == "yc":
                s["lastError"] = "HTTP 500 \u4e2d"
            if s["id"] == "betalist":
                s["id"] = "\U0001F600"
                s["lastError"] = "HTTP 429"
        run = self.run_ok(records)
        line = run.data["cards"]["radar"]["not_ok_line"]
        self.assertEqual(line, ck.fit("unnamed source: HTTP 429; launchingnext: filtered; yc: unsupported characters", 55))
        self.assertTrue(line.endswith(ELL))
        self.assertLessEqual(len(line), 55)


class TestBuildersCalendar(CardsTestCase):
    def test_52_weeks(self):
        records = success_records()
        records[GRAPHQL]["json"] = make_calendar(52, date(2025, 10, 12))
        run = self.run_ok(records)
        svg = run.svg_text("calendar-dark")
        self.assertIn('width="16" height="16" rx="3"', svg)
        self.assertNotIn('width="15"', svg)
        for r, label in ((1, "M"), (3, "W"), (5, "F")):
            self.assertIn(f'<text x="64" y="{270 + r * 20 + 19}" font-family="\'JetBrains Mono\'', svg)
            self.assertIn(f'y="{270 + r * 20 + 19}" font-family="\'JetBrains Mono\',Consolas,monospace" '
                          f'font-weight="400" font-size="30" fill="#a3a8b8">{label}</text>', svg)
        self.assertIn('<text x="100" y="250"', svg)  # column 0 label
        self.assertIn(">Oct</text>", svg)
        self.assertNotIn(">Nov</text>", svg)  # month change at column 3 (< 4 columns from column 0)
        self.assertIn('<text x="260" y="250"', svg)  # Dec at column 8
        self.assertNotIn('<text x="1120" y="250"', svg)  # Oct 2026 starts at column 51: 1120 + 54 > 1136
        self.assertEqual(run.data["cards"]["calendar"]["first_date"], "2025-10-12")
        self.assertEqual(run.data["cards"]["calendar"]["last_date"], "2026-10-10")
        self.assertIn("graphql 200 weeks=52 ", run.out)
        self.assert_captions_ok(run)

    def test_54_weeks(self):
        records = success_records()
        records[GRAPHQL]["json"] = make_calendar(54, date(2025, 10, 5))
        run = self.run_ok(records)
        svg = run.svg_text("calendar-light")
        self.assertIn('width="15" height="15" rx="3"', svg)
        for r in (1, 3, 5):
            self.assertIn(f'<text x="64" y="{270 + r * 19 + 18.5}"', svg)
        self.assertNotIn('<text x="1107" y="250"', svg)  # column 53: 1107 + 54 > 1136
        self.assertIn(f'x="{100 + 53 * 19}" y="270" width="15"', svg)
        self.assertEqual(svg.count('rx="3"'), 54 * 7)
        for level in ck.LIGHT["levels"]:
            self.assertIn(f'<g fill="{level}">', svg)

    def test_graphql_errors(self):
        records = success_records()
        records[GRAPHQL]["json"] = {"data": None, "errors": [{"message": "Resource not accessible by integration"}]}
        run = self.run_ok(records)
        vm = run.data["cards"]["calendar"]
        self.assertEqual(vm["alt"], f"GitHub contributions: data unavailable at {CARD_TIME} "
                                    "(GraphQL error: Resource not accessible by integration).")
        self.assertEqual(vm["caption"],
                         f"GitHub contributions: data unavailable at {CARD_TIME}; the card shows the reason.[^calendar]")
        svg = run.svg_text("calendar-dark")
        self.assertIn(">GraphQL error: Resource not accessible by integration</text>", svg)
        self.assertIn(">data unavailable</text>", svg)
        self.assertNotIn("<g fill=", svg)
        self.assertIn(f">card {CARD_TIME}</text>", svg)
        self.assertIn("graphql 200 FAIL GraphQL error", run.out)
        long_message = "x" * 60
        records[GRAPHQL]["json"] = {"errors": [{"message": long_message}]}
        run = self.run_ok(records)
        self.assertEqual(run.data["cards"]["calendar"]["reason"], "GraphQL error: " + "x" * 40)
        records[GRAPHQL]["json"] = {"errors": [{"message": "N\u0065ha \U0001F600"}]}
        run = self.run_ok(records)
        self.assertEqual(run.data["cards"]["calendar"]["reason"], "GraphQL error: unsupported characters")
        records[GRAPHQL]["json"] = {"errors": [{"message": "blocked by N\u0065ha"}]}
        run = self.run_ok(records)
        self.assertEqual(run.data["cards"]["calendar"]["reason"], "GraphQL error: filtered")
        self.assert_captions_ok(run)

    def test_http_401_and_missing_fields(self):
        records = success_records()
        records[GRAPHQL] = {"exception": "httperror:401", "ms": 90}
        run = self.run_ok(records)
        self.assertEqual(run.data["cards"]["calendar"]["alt"],
                         f"GitHub contributions: data unavailable at {CARD_TIME} (HTTP 401).")
        self.assertIn("graphql 401 FAIL HTTP 401", run.out)
        records[GRAPHQL] = {"status": 200, "ms": 90, "json": {"data": {"user": {"contributionsCollection": {}}}}}
        run = self.run_ok(records)
        self.assertEqual(run.data["cards"]["calendar"]["reason"], "missing field contributionCalendar")
        records[GRAPHQL] = {"status": 200, "ms": 90, "json": {"data": {"user": None}}}
        run = self.run_ok(records)
        self.assertEqual(run.data["cards"]["calendar"]["reason"], "missing field contributionCalendar")
        cal = make_calendar(52, date(2025, 10, 12))
        del cal["data"]["user"]["contributionsCollection"]["contributionCalendar"]["totalContributions"]
        records[GRAPHQL] = {"status": 200, "ms": 90, "json": cal}
        run = self.run_ok(records)
        self.assertEqual(run.data["cards"]["calendar"]["reason"], "missing field totalContributions")
        cal = make_calendar(52, date(2025, 10, 12))
        cal["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"][3]["contributionDays"][2][
            "contributionLevel"] = "FIFTH"
        records[GRAPHQL] = {"status": 200, "ms": 90, "json": cal}
        run = self.run_ok(records)
        self.assertEqual(run.data["cards"]["calendar"]["reason"], "missing field contributionLevel")
        records[GRAPHQL] = {"exception": "timeout", "ms": 30000}
        run = self.run_ok(records)
        self.assertEqual(run.data["cards"]["calendar"]["reason"], "timeout 30 s")


# --- test 4: escaping and bounds ------------------------------------------------------------------------


class TestEscaping(CardsTestCase):
    def test_title_escaping(self):
        records = success_records()
        title = 'He said "hi" <b>bold</b> & more'
        records[DIGEST]["json"]["sections"][0]["entries"][0]["title"] = title
        run = self.run_ok(records)
        svg = run.svg("ahead-dark")
        self.assertIn(">He said &quot;hi&quot; &lt;b&gt;bold&lt;/b&gt; &amp; more</text>", svg)
        self.assertNotIn("<b>", svg)
        alt = run.data["cards"]["ahead"]["alt"]
        self.assertTrue(alt.endswith(f"Top entry: {title}."))
        self.assertEqual(run.title("ahead-dark"), alt)
        self.assertEqual(run.title("ahead-light"), alt)
        blocks = readme_blocks(run.readme)
        self.assertEqual(blocks["ahead"][0], alt)
        self.assertIn("&quot;hi&quot; &lt;b&gt;", run.readme)
        root = ET.fromstring(run.svg("ahead-dark").encode("utf-8"))
        self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")

    def test_long_last_error(self):
        records = success_records()
        for s in records[SOURCES]["json"]["sources"]:
            if s["id"] == "launchingnext":
                s["lastError"] = "E" * 500
        run = self.run_ok(records)
        fitted = ck.fit("launchingnext: " + "E" * 500, 55)
        self.assertEqual(len(fitted), 55)
        vm = run.data["cards"]["radar"]
        self.assertEqual(vm["not_ok_line"], fitted)
        self.assertIn(f">{fitted}</text>", run.svg_text("radar-dark"))
        self.assertTrue(vm["alt"].endswith(f"; not ok: {fitted}."))
        for text in (run.svg("radar-dark"), run.readme, (run.out_dir / "data.json").read_text(encoding="ascii")):
            self.assertNotIn("E" * 60, text)

    def test_wide_title_truncated_by_width(self):
        records = success_records()
        records[DIGEST]["json"]["sections"][0]["entries"][0]["title"] = "W" * 42
        run = self.run_ok(records)
        line = run.data["cards"]["ahead"]["title_line"]
        self.assertTrue(line.endswith(ELL))
        self.assertLess(len(line), 42)
        self.assertLessEqual(ck.text_width(line, 600, 36) + 64, 1136)
        records[DIGEST]["json"]["sections"][0]["entries"][0]["title"] = "DeepSeek just open-sourced its engine."
        run = self.run_ok(records)
        self.assertEqual(run.data["cards"]["ahead"]["title_line"], "DeepSeek just open-sourced its engine.")
        records[DIGEST]["json"]["sections"][0]["entries"][0]["title"] = (
            "DeepInfer: High-performance inference framework for DeepSeek models")
        run = self.run_ok(records)
        line = run.data["cards"]["ahead"]["title_line"]
        self.assertTrue(line.endswith(ELL))
        self.assertLessEqual(ck.text_width(line, 600, 36), 1072)
        self.assertTrue(run.data["cards"]["ahead"]["alt"].endswith(f"Top entry: {line}."))

    def test_width_assertion_exits_2(self):
        with self.assertRaises(SystemExit) as cm:
            with contextlib.redirect_stderr(io.StringIO()):
                rc.check_inter("W" * 40, 700, 72)
        self.assertEqual(cm.exception.code, 2)
        with self.assertRaises(SystemExit) as cm:
            with contextlib.redirect_stderr(io.StringIO()):
                rc.check_mono("x" * 56, 55)
        self.assertEqual(cm.exception.code, 2)


# --- test 5: SVG validity ---------------------------------------------------------------------------


class TestSvg(CardsTestCase):
    def failure_records(self):
        records = success_records()
        records[PRODUCT_URLS[1]] = {"exception": "httperror:403", "ms": 10}
        records[INDEX] = {"exception": "httperror:404", "ms": 10}
        records[STATS] = {"exception": "timeout", "ms": 20000}
        records[GRAPHQL] = {"exception": "httperror:401", "ms": 10}
        return records

    def test_every_svg_valid(self):
        for run in (self.run_ok(scenario_path=FIXTURES / "success.json"), self.run_ok(self.failure_records())):
            names = sorted(p.name for p in run.out_dir.glob("*.svg"))
            self.assertEqual(names, sorted(f"{c}-{t}.svg" for c in rc.CARDS for t in ("light", "dark")))
            for name in names:
                data = (run.out_dir / name).read_bytes()
                card_id = name.split("-")[0]
                self.assertEqual(ck.validate_svg(data, card_id), [], name)
                self.assertLess(len(data), 200000, name)
                root = ET.fromstring(data)
                self.assertEqual(root.get("width"), "1200")
                self.assertEqual(root.get("role"), "img")
                self.assertEqual(root.get("aria-labelledby"), "t")
                text = B64_RE.sub("base64,", data.decode("utf-8"))
                for m in re.finditer(r'font-size="([0-9.]+)"', text):
                    self.assertGreaterEqual(float(m.group(1)), 28)
                for m in re.finditer(r'(?:fill|stroke)="(#[0-9a-fA-F]+)"', text):
                    self.assertIn(m.group(1), ck.allowed_colours(card_id), name)
                    if card_id != "calendar":
                        self.assertIn(m.group(1), ck.allowed_colours("products"), name)
                self.assertIn("@font-face{font-family:'Inter';font-weight:300 900;src:url(data:font/woff2;base64,", text)
                self.assertIn("@font-face{font-family:'JetBrains Mono';font-weight:400 700;", text)
                self.assertIn(f'<rect x=".5" y=".5" width="1199" height="{int(root.get("height")) - 1}" rx="23.5" '
                              f'fill="none"', text)
            for card_id in rc.CARDS:
                light = HEX_RE.sub("#", run.svg(f"{card_id}-light"))
                dark = HEX_RE.sub("#", run.svg(f"{card_id}-dark"))
                self.assertEqual(light, dark, card_id)
                self.assertNotEqual(run.svg(f"{card_id}-light"), run.svg(f"{card_id}-dark"))

    def test_forbidden_list_and_validator(self):
        self.assertEqual(ck.FORBIDDEN_SVG, SET_A)
        good = ck.svg_card("dark", 480, "t", "d", [ck.text(64, 72, "x", font=ck.FONT_SANS, weight=600, size=32,
                                                             fill="#a3a8b8")], "ahead").encode("utf-8")
        self.assertEqual(ck.validate_svg(good, "ahead"), [])
        bad = good.replace(b"</svg>", b'<rect fill="#ff0000"/><text font-size="20">x</text>\x3cfilter id="f"/></svg>')
        problems = ck.validate_svg(bad, "ahead")
        self.assertTrue(any("forbidden pattern" in p for p in problems))
        self.assertTrue(any("font-size 20" in p for p in problems))
        self.assertTrue(any("#ff0000" in p for p in problems))
        self.assertTrue(any("xml parse" in p for p in ck.validate_svg(b"<svg>", "ahead")))
        self.assertTrue(any("size" in p for p in ck.validate_svg(b"<svg/>" + b" " * 200000, "ahead")))
        self.assertEqual(ck.validate_svg(good.replace(b"#a3a8b8", b"#0a4552"), "calendar"), [])
        self.assertNotEqual(ck.validate_svg(good.replace(b"#a3a8b8", b"#0a4552"), "ahead"), [])

    def test_letter_spacing_d12_9(self):
        run = self.run_ok(scenario_path=FIXTURES / "success.json")
        ahead = run.svg_text("ahead-dark")
        radar = run.svg_text("radar-dark")
        self.assertRegex(ahead, r'font-size="32" letter-spacing="2" fill="#a3a8b8">AHEAD \u00b7 AI BRIEF</text>')
        self.assertRegex(ahead, r'font-size="30" letter-spacing="1" fill="#a3a8b8">TOP ENTRY</text>')
        self.assertRegex(radar, r'font-size="30" letter-spacing="1" fill="#a3a8b8">ENABLED SOURCES NOT OK</text>')
        for name in ("products-dark", "calendar-dark"):
            self.assertRegex(run.svg_text(name), r'y="72" [^>]*letter-spacing="2"')

    def test_default_paths_from_other_cwd_d12_9(self):
        tmp = self.tmpdir()
        repo = tmp / "repo"
        (repo / "assets").mkdir(parents=True)
        shutil.copytree(REPO / "scripts", repo / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(REPO / "assets" / "fonts", repo / "assets" / "fonts")
        shutil.copy(REPO / "README.md", repo / "README.md")
        elsewhere = tmp / "elsewhere"
        elsewhere.mkdir()
        proc = subprocess.run(
            [sys.executable, str(repo / "scripts" / "refresh_cards.py"), "--fixtures",
             str(repo / "scripts" / "tests" / "fixtures" / "success.json"), "--now", NOW],
            cwd=str(elsewhere), capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(sorted(p.name for p in (repo / "assets" / "cards").glob("*")),
                         sorted([f"{c}-{t}.svg" for c in rc.CARDS for t in ("light", "dark")] + ["data.json"]))
        self.assertIn(PRODUCTS_CAPTION, (repo / "README.md").read_text(encoding="utf-8"))
        self.assertFalse((elsewhere / "assets").exists())
        self.assertIn("readme blocks=4 ok", proc.stdout)

    def test_font_hash_mismatch_exits_4(self):
        tmp = self.tmpdir()
        fonts = tmp / "assets" / "fonts"
        shutil.copytree(REPO / "assets" / "fonts", fonts)
        path = fonts / "inter-latin.woff2"
        data = bytearray(path.read_bytes())
        data[100] ^= 0xFF
        path.write_bytes(bytes(data))
        with self.assertRaises(SystemExit) as cm:
            with contextlib.redirect_stderr(io.StringIO()):
                ck.font_style(tmp)
        self.assertEqual(cm.exception.code, 4)
        style = ck.font_style(REPO)
        self.assertGreater(len(style), 106000)
        self.assertTrue(style.startswith("<style>\n@font-face{font-family:'Inter';font-weight:300 900;"))


# --- test 6: README rewrite and data.json audit -------------------------------------------------------


class TestReadme(CardsTestCase):
    def test_blocks_replaced_outside_identical(self):
        original = (REPO / "README.md").read_bytes().decode("utf-8")
        self.assertEqual(len(BLOCK_RE.findall(original)), 4)
        for _, body in BLOCK_RE.findall(original):
            self.assertEqual(body, "")
        run = self.run_ok(scenario_path=FIXTURES / "success.json")
        new = run.readme
        blocks = readme_blocks(new)
        self.assertEqual(sorted(blocks), sorted(rc.CARDS))
        self.assertEqual(blocks["products"], (PRODUCTS_ALT, PRODUCTS_CAPTION))
        self.assertEqual(blocks["calendar"], (CALENDAR_ALT, CALENDAR_CAPTION))
        emptied = BLOCK_RE.sub(lambda m: f"<!-- card:{m.group(1)}:start -->\n<!-- card:{m.group(1)}:end -->", new)
        self.assertEqual(emptied, original)
        self.assertEqual(new.count("<picture>"), 5)
        self.assertEqual(new.count('srcset="assets/cards/'), 4)
        self.assertNotIn("\r", new)
        self.assertLessEqual(len(new.encode("utf-8")), 15000)
        self.assertIn("\n<!-- card:products:start -->\n<picture>\n  <source media=\"(prefers-color-scheme: dark)\" "
                      "srcset=\"assets/cards/products-dark.svg\">\n  <img src=\"assets/cards/products-light.svg\" alt=\"",
                      new)
        self.assertIn(f'" width="100%">\n</picture>\n\n{PRODUCTS_CAPTION}\n<!-- card:products:end -->\n', new)

    def test_rerun_is_idempotent(self):
        run = self.run_ok(scenario_path=FIXTURES / "success.json")
        first = run.readme
        tmp = self.tmpdir()
        readme = tmp / "README.md"
        readme.write_bytes(first.encode("utf-8"))
        with contextlib.redirect_stdout(io.StringIO()):
            code = rc.main(["--fixtures", str(FIXTURES / "success.json"), "--now", NOW, "--out", str(tmp / "c"),
                            "--readme", str(readme)])
        self.assertEqual(code, 0)
        self.assertEqual(readme.read_bytes().decode("utf-8"), first)

    def assert_exit_2_nothing_written(self, run):
        self.assertEqual(run.code, 2)
        self.assertFalse(run.out_dir.exists() and any(run.out_dir.iterdir()))
        self.assertNotIn("readme blocks", run.out)
        self.assertNotIn("stamp ", run.out)

    def test_missing_marker_exit_2(self):
        original = (REPO / "README.md").read_bytes().decode("utf-8")
        broken = original.replace("<!-- card:radar:end -->\n", "")
        run = self.run_cli(scenario_path=FIXTURES / "success.json", readme_text=broken)
        self.assert_exit_2_nothing_written(run)
        self.assertEqual(run.readme, broken)
        self.assertIn("markers for radar", run.err)

    def test_duplicate_marker_exit_2(self):
        original = (REPO / "README.md").read_bytes().decode("utf-8")
        broken = original.replace("<!-- card:ahead:start -->\n", "<!-- card:ahead:start -->\n<!-- card:ahead:start -->\n")
        run = self.run_cli(scenario_path=FIXTURES / "success.json", readme_text=broken)
        self.assert_exit_2_nothing_written(run)
        self.assertEqual(run.readme, broken)

    def test_bad_caption_exit_2(self):
        with self.assertRaises(SystemExit) as cm:
            with contextlib.redirect_stderr(io.StringIO()):
                rc.block_for("ahead", "alt", "Caption with an ellipsis" + ELL + "[^systems]")
        self.assertEqual(cm.exception.code, 2)
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                rc.block_for("ahead", "alt", "Caption with <markup>[^systems]")
        block = rc.block_for("ahead", 'a "quoted" <alt> & more', "Fine caption, 1 of 2 (ok); see [^systems]")
        self.assertIn('alt="a &quot;quoted&quot; &lt;alt&gt; &amp; more"', block)
        for vm_caption in (PRODUCTS_CAPTION, AHEAD_CAPTION, RADAR_CAPTION, CALENDAR_CAPTION,
                           f"Ahead: data unavailable at {CARD_TIME}; the card shows the reason.[^systems]",
                           "Ahead data generated time unknown: 893 items, 43 of 64 sources ok; brief unavailable, "
                           "see the card.[^systems]"):
            self.assertRegex(vm_caption, rc.CAPTION_RE)

    def test_readme_over_15000_exit_2(self):
        original = (REPO / "README.md").read_bytes().decode("utf-8")
        padded = original + "\n<!-- " + "p" * 3600 + " -->\n"
        self.assertLess(len(padded.encode("utf-8")), 15000)
        run = self.run_cli(scenario_path=FIXTURES / "success.json", readme_text=padded)
        self.assert_exit_2_nothing_written(run)
        self.assertEqual(run.readme, padded)
        self.assertIn("15000", run.err)

    def test_data_json_audit(self):
        records = success_records()
        records[DIGEST]["json"]["sections"][0]["entries"][0]["title"] = "Rocket \U0001F680 launch marker"
        for s in records[SOURCES]["json"]["sources"]:
            if s["id"] == "launchingnext":
                s["lastError"] = "Z" * 500
        run = self.run_ok(records)
        raw = (run.out_dir / "data.json").read_bytes()
        self.assertTrue(raw.isascii())
        self.assertTrue(raw.endswith(b"\n"))
        self.assertNotIn(b"launch marker", raw)
        self.assertNotIn(b"Z" * 60, raw)
        data = json.loads(raw)
        keys = set(all_keys(data))
        for forbidden in ("body", "raw", "lastError", "weeks"):
            self.assertNotIn(forbidden, keys)
        self.assertEqual(sorted(data["cards"]), sorted(rc.CARDS))
        self.assertEqual(data["generated"], {"iso": "2026-10-21T02:43:00Z", "ist": CARD_TIME})
        blocks = readme_blocks(run.readme)
        for card_id in rc.CARDS:
            self.assertEqual(blocks[card_id], (data["cards"][card_id]["alt"], data["cards"][card_id]["caption"]))
            self.assertEqual(run.title(f"{card_id}-dark"), data["cards"][card_id]["alt"])
            self.assertLess(len(data["cards"][card_id]["alt"]), 400)

    def test_github_output_stamp(self):
        tmp = self.tmpdir()
        gh_out = tmp / "gh_output.txt"
        readme = tmp / "README.md"
        readme.write_bytes((REPO / "README.md").read_bytes())
        import os
        old = os.environ.get("GITHUB_OUTPUT")
        os.environ["GITHUB_OUTPUT"] = str(gh_out)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                code = rc.main(["--fixtures", str(FIXTURES / "success.json"), "--now", NOW, "--out", str(tmp / "c"),
                                "--readme", str(readme)])
        finally:
            if old is None:
                del os.environ["GITHUB_OUTPUT"]
            else:
                os.environ["GITHUB_OUTPUT"] = old
        self.assertEqual(code, 0)
        self.assertEqual(gh_out.read_text(encoding="utf-8"), f"stamp={CARD_TIME}\n")

    def test_fixture_files_are_ascii_json(self):
        for path in FIXTURES.glob("*.json"):
            raw = path.read_bytes()
            self.assertTrue(raw.isascii(), path.name)
            self.assertNotIn(b"\r", raw, path.name)
            json.loads(raw)


if __name__ == "__main__":
    unittest.main()
