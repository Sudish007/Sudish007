"""Generate the four live cards, their README blocks and data.json.

Usage: python scripts/refresh_cards.py [--fixtures PATH] [--now ISO8601] [--out DIR] [--readme PATH]

Defaults (D12-9): --out assets/cards and --readme README.md, both relative to
the repository root, so the script works from any working directory.
Environment: GITHUB_TOKEN (GraphQL bearer, optional), GITHUB_LOGIN (default
Sudish007), GITHUB_OUTPUT (if set, `stamp=<IST time>` is appended).

Exit codes: 0 ok (source failures are rendered as failure states), 2 README
markers / caption / width assertion, 3 SVG invalid or oversize, 4 font hash
mismatch. One ASCII log line per operation on stdout.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cardkit as ck

REPO_ROOT = Path(__file__).resolve().parent.parent
MIDDOT = ck.MIDDOT

CARDS = ["products", "ahead", "radar", "calendar"]

# website fields of portfolio/assets/config.js:60,73,86,99,112,125 (config order)
PRODUCTS = [
    ("KaatDo", "https://kaatdo.com"),
    ("RishtaPatra", "https://rishtapatra.com"),
    ("BhojVerse", "https://sudish007.github.io/bhojverse-site/"),
    ("Telusa", "https://sudish007.github.io/telusa-privacy/"),
    ("Sri Sai Hospital", "https://sudish007.github.io/srisai-website/"),
    ("Saubhagya Clinic", "https://drsavitak.netlify.app/"),
]
AHEAD_META = "https://sudish007.github.io/ahead/data/meta.json"
AHEAD_INDEX = "https://sudish007.github.io/ahead/data/digests/index.json"
AHEAD_DIGEST = "https://sudish007.github.io/ahead/data/digests/{date}.json"
RADAR_STATS = "https://sudish007.github.io/startup-radar/data/stats.json"
RADAR_SOURCES = "https://sudish007.github.io/startup-radar/data/sources.json"
GRAPHQL_URL = "https://api.github.com/graphql"
GRAPHQL_QUERY = (
    "query($login:String!,$from:DateTime!,$to:DateTime!){ user(login:$login){ "
    "contributionsCollection(from:$from,to:$to){ contributionCalendar{ totalContributions "
    "weeks{ contributionDays{ date contributionCount contributionLevel weekday }}}}}}"
)

# Placeholder until FEAT-002 records the verification browser's navigator.userAgent (plan.md D-6).
PROBE_UA = "Mozilla/5.0 (compatible; Sudish007-profile-cards/1.0; +https://github.com/Sudish007/Sudish007)"
JSON_UA = "Sudish007-profile-cards/1.0 (+https://github.com/Sudish007/Sudish007)"

PROBE_TIMEOUT = 20
JSON_TIMEOUT = 20
GRAPHQL_TIMEOUT = 30
BODY_LIMIT = 65536
README_MAX_BYTES = 15000
CONTENT_RIGHT = 1136
CONTENT_LEFT = 64
CONTENT_WIDTH = CONTENT_RIGHT - CONTENT_LEFT  # 1072

CAPTION_RE = re.compile(r"^[A-Za-z0-9 ,.:;()/_\[\]^-]+$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LEVELS = {"NONE": 0, "FIRST_QUARTILE": 1, "SECOND_QUARTILE": 2, "THIRD_QUARTILE": 3, "FOURTH_QUARTILE": 4}

# Mono character budgets over the 1,072-unit content width (advances are exactly 0.6 em).
MONO_BUDGET = {36: 49, 32: 55, 30: 59}
STATUS_BUDGET = 23  # products status strings drawn from x 600


def log(line: str) -> None:
    print(line, flush=True)


def fail(code: int, message: str) -> None:
    print(f"error: {message}", file=sys.stderr, flush=True)
    raise SystemExit(code)


# --- transport -------------------------------------------------------------------


class Response:
    __slots__ = ("status", "ms", "body")

    def __init__(self, status: int, ms: int, body: bytes):
        self.status = status
        self.ms = ms
        self.body = body


class FetchError(Exception):
    """A failed fetch with a reason from the bounded vocabulary."""

    def __init__(self, reason: str, status=None, ms: int = 0):
        super().__init__(reason)
        self.reason = reason
        self.status = status
        self.ms = ms


def _mapped(call, timeout: int) -> Response:
    """Run `call()` and map exceptions in the design 3.2 order; measures elapsed ms."""
    t0 = time.monotonic()

    def elapsed() -> int:
        return round((time.monotonic() - t0) * 1000)

    try:
        status, body = call()
    except urllib.error.HTTPError as exc:
        raise FetchError(f"HTTP {exc.code}", status=exc.code, ms=elapsed())
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, TimeoutError):
            text = f"timeout {timeout} s"
        elif isinstance(reason, socket.gaierror):
            text = "DNS error"
        elif isinstance(reason, ssl.SSLError):
            text = "TLS error"
        else:
            text = "connection error"
        raise FetchError(text, ms=elapsed())
    except TimeoutError:
        raise FetchError(f"timeout {timeout} s", ms=elapsed())
    except ssl.SSLError:
        raise FetchError("TLS error", ms=elapsed())
    except (OSError, http.client.HTTPException):
        raise FetchError("connection error", ms=elapsed())
    return Response(status, elapsed(), body)


class LiveTransport:
    def get(self, url: str, timeout: int, headers: dict, data: bytes | None = None) -> Response:
        req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")

        def call():
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read(BODY_LIMIT)

        return _mapped(call, timeout)


class FixtureTransport:
    """Replays a scenario file: {"base": "other.json" (optional), "http": {url: record}}.

    record = {"status": 200, "ms": 313, "json": {...}} or {"status", "ms", "text": "..."}
    or {"exception": "timeout" | "readtimeout" | "gaierror" | "sslerror" | "connection"
    | "httperror:<code>", "ms": n}; the real exception classes are raised so the
    mapping in _mapped() is exercised. A URL absent from the file is a test bug (KeyError).
    """

    def __init__(self, path):
        self.records = self._load(Path(path))

    @classmethod
    def _load(cls, path: Path) -> dict:
        doc = json.loads(path.read_bytes().decode("utf-8"))
        records = {}
        if doc.get("base"):
            records.update(cls._load(path.parent / doc["base"]))
        records.update(doc.get("http", {}))
        return records

    def get(self, url: str, timeout: int, headers: dict, data: bytes | None = None) -> Response:
        rec = self.records[url]
        ms = int(rec.get("ms", 0))

        def call():
            exc = rec.get("exception")
            if exc is not None:
                if exc == "timeout":
                    raise urllib.error.URLError(TimeoutError("timed out"))
                if exc == "readtimeout":
                    raise TimeoutError("The read operation timed out")
                if exc == "gaierror":
                    raise urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))
                if exc == "sslerror":
                    raise urllib.error.URLError(ssl.SSLError(1, "certificate verify failed"))
                if exc == "connection":
                    raise ConnectionResetError(104, "Connection reset by peer")
                if exc.startswith("httperror:"):
                    code = int(exc.split(":", 1)[1])
                    raise urllib.error.HTTPError(url, code, "fixture", {}, None)
                raise KeyError(f"unknown fixture exception {exc}")
            if "json" in rec:
                body = json.dumps(rec["json"]).encode("utf-8")
            else:
                body = str(rec.get("text", "")).encode("utf-8")
            return int(rec.get("status", 200)), body

        resp = _mapped(call, timeout)
        resp.ms = ms
        return resp


# --- fetchers -----------------------------------------------------------------------


def probe(t, url: str) -> dict:
    headers = {"User-Agent": PROBE_UA, "Accept": "text/html,*/*;q=0.8"}
    try:
        r = t.get(url, PROBE_TIMEOUT, headers)
    except FetchError as exc:
        return {"ok": False, "status": exc.status, "ms": exc.ms, "reason": exc.reason}
    if r.ms > PROBE_TIMEOUT * 1000:
        return {"ok": False, "status": r.status, "ms": r.ms, "reason": f"timeout {PROBE_TIMEOUT} s"}
    if not 200 <= r.status <= 399:
        return {"ok": False, "status": r.status, "ms": r.ms, "reason": f"HTTP {r.status}"}
    return {"ok": True, "status": r.status, "ms": r.ms, "reason": None}


def fetch_json(t, url: str, label: str):
    headers = {"User-Agent": JSON_UA, "Accept": "application/json"}
    try:
        r = t.get(url, JSON_TIMEOUT, headers)
    except FetchError as exc:
        log(f"fetch {label} {exc.status or '-'} {exc.ms}ms FAIL {exc.reason}")
        raise
    try:
        obj = json.loads(r.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        log(f"fetch {label} {r.status} {r.ms}ms FAIL invalid JSON")
        raise FetchError("invalid JSON", status=r.status, ms=r.ms)
    log(f"fetch {label} {r.status} {r.ms}ms")
    return obj


def need(obj, name: str, kinds=None):
    """Field access with the design's `missing field <name>` reason (bools are not ints)."""
    if not isinstance(obj, dict) or name not in obj:
        raise FetchError(f"missing field {name}")
    value = obj[name]
    if kinds is not None and (isinstance(value, bool) or not isinstance(value, kinds)):
        raise FetchError(f"missing field {name}")
    return value


def graphql(t, token, login: str, now: datetime) -> dict:
    """POST the contributionCalendar query; returns the calendar dict or raises FetchError."""
    utc = now.astimezone(timezone.utc)
    variables = {
        "login": login,
        "from": (utc - timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "to": utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    body = json.dumps({"query": GRAPHQL_QUERY, "variables": variables}).encode("utf-8")
    headers = {"User-Agent": JSON_UA, "Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"bearer {token}"
    r = t.get(GRAPHQL_URL, GRAPHQL_TIMEOUT, headers, data=body)
    if r.status != 200:
        raise FetchError(f"HTTP {r.status}", status=r.status, ms=r.ms)
    try:
        doc = json.loads(r.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise FetchError("invalid JSON", status=r.status, ms=r.ms)
    if isinstance(doc, dict) and doc.get("errors"):
        first = doc["errors"][0] if isinstance(doc["errors"], list) and doc["errors"] else {}
        message = first.get("message", "") if isinstance(first, dict) else ""
        screened, why = ck.screen(message)
        text = screened[:40].rstrip() if screened else why
        raise FetchError(f"GraphQL error: {text or 'unknown'}", status=r.status, ms=r.ms)
    try:
        calendar = need(need(need(need(doc, "data", dict), "user", dict), "contributionsCollection", dict),
                        "contributionCalendar", dict)
    except FetchError:
        raise FetchError("missing field contributionCalendar", status=r.status, ms=r.ms)
    return calendar, r.status


# --- builders (view models; strings exactly as drawn) -----------------------------------


def failure_vm(card_name: str, reason: str, card_time: str, footnote: str) -> dict:
    return {
        "ok": False,
        "state": "data unavailable",
        "reason": reason,
        "card_time": card_time,
        "headline": "data unavailable",
        "footer": f"card {card_time}",
        "alt": f"{card_name}: data unavailable at {card_time} ({reason}).",
        "caption": f"{card_name}: data unavailable at {card_time}; the card shows the reason.[{footnote}]",
    }


def build_products(t, now: datetime) -> dict:
    card_time = ck.fmt_ist(now)
    rows = []
    for name, url in PRODUCTS:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname + parts.path.rstrip("/")  # constant label, e.g. sudish007.github.io/bhojverse-site
        r = probe(t, url)
        if r["ok"]:
            line = f"HTTP {r['status']} {MIDDOT} {ck.fmt_int(r['ms'])} ms"
            clause = f"HTTP {r['status']} in {ck.fmt_int(r['ms'])} ms"
            log(f"probe {host} {r['status']} {r['ms']}ms ok")
        else:
            line = f"FAIL {MIDDOT} {r['reason']}"
            clause = f"failed ({r['reason']})"
            log(f"probe {host} {r['status'] if r['status'] is not None else '-'} {r['ms']}ms FAIL {r['reason']}")
        rows.append({"name": name, "url": url, "ok": r["ok"], "status": r["status"], "ms": r["ms"],
                     "reason": r["reason"], "line": line, "clause": clause})
    ok = sum(1 for row in rows if row["ok"])
    n = len(PRODUCTS)
    return {
        "ok": True,
        "state": "full",
        "card_time": card_time,
        "reachable": ok,
        "total": n,
        "headline_num": f"{ok} of {n}",
        "headline_rest": " reachable",
        "rows": rows,
        "footer": f"card {card_time} {MIDDOT} GET from GitHub Actions",
        "alt": (f"Live products, checked {card_time} from GitHub Actions: {ok} of {n} reachable. "
                + "; ".join(f"{row['name']} {row['clause']}" for row in rows) + "."),
        "caption": f"Checked {card_time} from GitHub Actions: {ok} of {n} product sites reachable.[^probe]",
    }


def _first_title(sections) -> str | None:
    """D12-8: the first entry in section order whose title is a non-empty string."""
    for section in sections:
        entries = section.get("entries") if isinstance(section, dict) else None
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict):
                title = entry.get("title")
                if isinstance(title, str) and title.strip():
                    return title
    return None


def build_ahead(t, now: datetime) -> dict:
    card_time = ck.fmt_ist(now)
    try:
        meta = fetch_json(t, AHEAD_META, "ahead/meta.json")
        generated = need(meta, "generatedAt")
        item_count = need(meta, "itemCount", int)
        sources = need(meta, "sources", (dict, list))
        ok_count = need(meta, "okCount", int)
        empty_count = need(meta, "emptyCount", int)
        failed_count = need(meta, "failedCount", int)
    except FetchError as exc:
        return failure_vm("Ahead", exc.reason, card_time, "^systems")
    data_time = ck.fmt_ist(generated)
    n_sources = len(sources)
    vm = {
        "ok": True,
        "card_time": card_time,
        "data_iso": generated if isinstance(generated, str) else None,
        "data_time": data_time,
        "item_count": item_count,
        "source_count": n_sources,
        "ok_count": ok_count,
        "empty_count": empty_count,
        "failed_count": failed_count,
        "headline_num": ck.fmt_int(item_count),
        "headline_rest": f" items {MIDDOT} {ck.fmt_int(n_sources)} sources",
        "sources_line": f"sources: {ck.fmt_int(ok_count)} ok {MIDDOT} {ck.fmt_int(empty_count)} empty {MIDDOT} "
                        f"{ck.fmt_int(failed_count)} failed",
        "footer": f"data {data_time} {MIDDOT} card {card_time}",
    }
    alt_head = (f"Ahead, data generated {data_time}: {ck.fmt_int(item_count)} items from {ck.fmt_int(n_sources)} "
                f"sources ({ck.fmt_int(ok_count)} ok, {ck.fmt_int(empty_count)} empty, {ck.fmt_int(failed_count)} failed).")
    cap_head = (f"Ahead data generated {data_time}: {ck.fmt_int(item_count)} items, {ck.fmt_int(ok_count)} of "
                f"{ck.fmt_int(n_sources)} sources ok;")
    try:
        index = fetch_json(t, AHEAD_INDEX, "ahead/digests/index.json")
        days = need(index, "days", list)
        valid = [d for d in days if isinstance(d, dict) and isinstance(d.get("date"), str)
                 and ISO_DATE_RE.match(d["date"])]
        if not valid:
            raise FetchError("missing field days")
        day = max(valid, key=lambda d: d["date"])
        date = day["date"]
        entry_count = need(day, "entryCount", int)
        reading = need(day, "readingMinutes", int)
        digest = fetch_json(t, AHEAD_DIGEST.format(date=date), f"ahead/digests/{date}.json")
        sections = need(digest, "sections", list)
    except FetchError as exc:
        vm.update({
            "state": "brief unavailable",
            "reason": exc.reason,
            "brief_line": f"brief unavailable: {exc.reason}",
            "alt": f"{alt_head} Brief unavailable ({exc.reason}).",
            "caption": f"{cap_head} brief unavailable, see the card.[^systems]",
        })
        return vm
    title_raw = _first_title(sections)
    if title_raw is None:
        state, title_line, last = "no entries", "none in this brief", "No top entry in this brief."
    else:
        screened, why = ck.screen(title_raw)
        if screened is None:
            state, title_line, last = "title omitted", f"top entry omitted: {why}", f"Top entry omitted: {why}."
        else:
            state = "full"
            title_line = ck.fit_width(screened, CONTENT_WIDTH, 600, 36)
            last = f"Top entry: {title_line}."
    vm.update({
        "state": state,
        "brief_date": date,
        "entry_count": entry_count,
        "reading_minutes": reading,
        "brief_line": f"Brief {date} {MIDDOT} {ck.fmt_int(entry_count)} entries {MIDDOT} {ck.fmt_int(reading)} min read",
        "label": "TOP ENTRY",
        "title_line": title_line,
        "alt": (f"{alt_head} Brief of {date}: {ck.fmt_int(entry_count)} entries, {ck.fmt_int(reading)} min read. {last}"),
        "caption": f"{cap_head} brief of {date}, {ck.fmt_int(entry_count)} entries.[^systems]",
    })
    return vm


def _source_id(source: dict) -> str:
    sid = source.get("id")
    if not isinstance(sid, str) or not sid.strip():
        return "unnamed source"
    screened, _ = ck.screen(sid)
    return screened if screened else "unnamed source"


def build_radar(t, now: datetime) -> dict:
    card_time = ck.fmt_ist(now)
    try:
        stats = fetch_json(t, RADAR_STATS, "startup-radar/stats.json")
        items = need(stats, "items", int)
        last24h = need(stats, "last24h", int)
        last7d = need(stats, "last7d", int)
        generated = need(stats, "generatedAt")
    except FetchError as exc:
        return failure_vm("Startup Radar", exc.reason, card_time, "^systems")
    data_time = ck.fmt_ist(generated)
    vm = {
        "ok": True,
        "card_time": card_time,
        "data_iso": generated if isinstance(generated, str) else None,
        "data_time": data_time,
        "items": items,
        "last24h": last24h,
        "last7d": last7d,
        "headline_num": ck.fmt_int(items),
        "headline_rest": f" items {MIDDOT} 90-day feed",
        "recent_line": f"{ck.fmt_int(last24h)} last 24 h {MIDDOT} {ck.fmt_int(last7d)} last 7 d",
        "footer": f"data {data_time} {MIDDOT} card {card_time}",
    }
    alt_head = (f"Startup Radar, data generated {data_time}: {ck.fmt_int(items)} items in the 90-day feed, "
                f"{ck.fmt_int(last24h)} in the last 24 hours, {ck.fmt_int(last7d)} in the last 7 days;")
    try:
        doc = fetch_json(t, RADAR_SOURCES, "startup-radar/sources.json")
        sources = need(doc, "sources", list)
    except FetchError as exc:
        vm.update({
            "state": "source health unavailable",
            "reason": exc.reason,
            "health_line": f"source health unavailable: {exc.reason}",
            "alt": f"{alt_head} source health unavailable ({exc.reason}).",
            "caption": (f"Startup Radar data generated {data_time}: {ck.fmt_int(items)} items in the 90-day feed; "
                        f"source health unavailable, see the card.[^systems]"),
        })
        return vm
    enabled = ok = 0
    not_ok = []
    for source in sources:
        if not isinstance(source, dict) or source.get("enabled") is not True:
            continue
        enabled += 1
        last_run = source.get("lastRunAt")
        last_error = source.get("lastError")
        if last_run is not None and (last_error is None or last_error == ""):
            ok += 1
            continue
        sid = _source_id(source)
        if last_run is None:
            reason = "not yet run"
        else:
            screened, why = ck.screen(last_error)
            reason = screened if screened is not None else why
        not_ok.append(f"{sid}: {reason}")
    not_ok_line = ck.fit("; ".join(not_ok), MONO_BUDGET[32]) if not_ok else "none"
    vm.update({
        "state": "full",
        "enabled": enabled,
        "ok_sources": ok,
        "not_ok_count": len(not_ok),
        "sources_line": f"sources: {ck.fmt_int(ok)} of {ck.fmt_int(enabled)} enabled ok on last run",
        "label": "ENABLED SOURCES NOT OK",
        "not_ok_line": not_ok_line,
        "alt": (f"{alt_head} {ck.fmt_int(ok)} of {ck.fmt_int(enabled)} enabled sources ok on their last run"
                + (f"; not ok: {not_ok_line}" if not_ok else "") + "."),
        "caption": (f"Startup Radar data generated {data_time}: {ck.fmt_int(items)} items in the 90-day feed, "
                    f"{ck.fmt_int(ok)} of {ck.fmt_int(enabled)} sources ok.[^systems]"),
    })
    return vm


def build_calendar(t, now: datetime, token, login: str) -> dict:
    card_time = ck.fmt_ist(now)
    status = None
    try:
        calendar, status = graphql(t, token, login, now)
        total = need(calendar, "totalContributions", int)
        weeks_raw = need(calendar, "weeks", list)
        if not weeks_raw:
            raise FetchError("missing field weeks")
        weeks = []
        for week in weeks_raw:
            days_raw = need(week, "contributionDays", list)
            days = []
            for day in days_raw:
                date = need(day, "date", str)
                if not ISO_DATE_RE.match(date):
                    raise FetchError("missing field date")
                count = need(day, "contributionCount", int)
                level_name = need(day, "contributionLevel", str)
                if level_name not in LEVELS:
                    raise FetchError("missing field contributionLevel")
                weekday = need(day, "weekday", int)
                if not 0 <= weekday <= 6:
                    raise FetchError("missing field weekday")
                days.append({"date": date, "count": count, "level": LEVELS[level_name], "weekday": weekday})
            weeks.append(days)
        all_days = [d for week in weeks for d in week]
        if not all_days:
            raise FetchError("missing field contributionDays")
    except FetchError as exc:
        shown = status if status is not None else (exc.status if exc.status is not None else "-")
        # the GraphQL message is a dynamic string and never reaches the log
        reason = "GraphQL error" if exc.reason.startswith("GraphQL error") else exc.reason
        log(f"graphql {shown} FAIL {reason}")
        return failure_vm("GitHub contributions", exc.reason, card_time, "^calendar")
    active = sum(1 for d in all_days if d["count"] > 0)
    first_date, last_date = all_days[0]["date"], all_days[-1]["date"]
    log(f"graphql {status} weeks={len(weeks)} total={total} active={active}")
    return {
        "ok": True,
        "state": "full",
        "card_time": card_time,
        "total": total,
        "active_days": active,
        "weeks_count": len(weeks),
        "first_date": first_date,
        "last_date": last_date,
        "weeks": weeks,
        "headline_num": ck.fmt_int(total),
        "headline_rest": " contributions",
        "active_line": f"{ck.fmt_int(active)} days with at least one contribution",
        "footer": f"{first_date} to {last_date} {MIDDOT} card {card_time}",
        "alt": (f"GitHub contributions from {first_date} to {last_date}: {ck.fmt_int(total)} total, "
                f"{ck.fmt_int(active)} days with at least one contribution. Refreshed {card_time}."),
        "caption": (f"{ck.fmt_int(total)} contributions between {first_date} and {last_date}, "
                    f"{ck.fmt_int(active)} active days; refreshed {card_time}.[^calendar]"),
    }


# --- renderers -------------------------------------------------------------------------


def check_inter(s: str, weight: int, size: int, letter_spacing: float = 0) -> None:
    width = ck.text_width(s, weight, size, letter_spacing)
    if width + CONTENT_LEFT > CONTENT_RIGHT:
        fail(2, f"inter line too wide ({width:.0f} units at {weight}/{size})")


def check_mono(s: str, max_chars: int) -> None:
    if len(s) > max_chars:
        fail(2, f"mono line too long ({len(s)} > {max_chars} chars)")


def el_kicker(t: dict, s: str) -> str:
    check_inter(s, 600, 32, 2)
    return ck.text(64, 72, s, font=ck.FONT_SANS, weight=600, size=32, fill=t["secondary"], letter_spacing=2)


def el_headline(t: dict, size: int, num: str, rest: str) -> str:
    check_inter(num + rest, 700, size)
    return ck.text(64, 150, None, font=ck.FONT_SANS, weight=700, size=size, fill=t["primary"],
                   tspans=[(num, t["accent"]), (rest, None)])


def el_inter(t: dict, y, s: str, weight: int, size: int, fill: str, letter_spacing=None) -> str:
    check_inter(s, weight, size, letter_spacing or 0)
    return ck.text(64, y, s, font=ck.FONT_SANS, weight=weight, size=size, fill=fill, letter_spacing=letter_spacing)


def el_label(t: dict, y, s: str) -> str:
    """Small uppercase label: Inter 600 30 secondary, letter-spacing 1 (D12-9)."""
    return el_inter(t, y, s, 600, 30, t["secondary"], letter_spacing=1)


def el_mono(t: dict, y, s: str, size: int, fill: str, x=64, max_chars=None) -> str:
    check_mono(s, max_chars if max_chars is not None else MONO_BUDGET[size])
    return ck.text(x, y, s, font=ck.FONT_MONO, weight=400, size=size, fill=fill)


def el_footer(t: dict, height: int, s: str) -> str:
    return el_mono(t, height - 40, s, 30, t["secondary"])


def render_failure(t: dict, height: int, kicker: str, vm: dict) -> list:
    return [
        el_kicker(t, kicker),
        el_inter(t, 150, vm["headline"], 700, 64, t["primary"]),
        el_mono(t, 212, vm["reason"], 32, t["primary"]),
        el_footer(t, height, vm["footer"]),
    ]


def products_height() -> int:
    return 600 - 58 * (6 - len(PRODUCTS))


def render_products(vm: dict, theme: str, style=None) -> str:
    t = ck.THEMES[theme]
    height = products_height()
    body = [el_kicker(t, "LIVE PRODUCTS"), el_headline(t, 72, vm["headline_num"], vm["headline_rest"])]
    for i, row in enumerate(vm["rows"]):
        y = 230 + 58 * i
        name_width = ck.text_width(row["name"], 600, 36)
        if name_width + CONTENT_LEFT > 600:
            fail(2, f"product name too wide ({name_width:.0f} units)")
        body.append(ck.text(64, y, row["name"], font=ck.FONT_SANS, weight=600, size=36, fill=t["primary"]))
        colour = t["accent"] if row["ok"] else t["primary"]
        body.append(el_mono(t, y, row["line"], 36, colour, x=600, max_chars=STATUS_BUDGET))
        if row["ok"]:
            body.append(f'<rect x="1116" y="{y - 18}" width="20" height="20" fill="{t["accent"]}"/>')
        else:
            body.append(f'<rect x="1116" y="{y - 18}" width="20" height="20" fill="none" '
                        f'stroke="{t["primary"]}" stroke-width="3"/>')
    body.append(el_footer(t, height, vm["footer"]))
    desc = "Sources: " + " ".join(url for _, url in PRODUCTS)
    return ck.svg_card(theme, height, vm["alt"], desc, body, "products", style=style)


def render_ahead(vm: dict, theme: str, style=None) -> str:
    t = ck.THEMES[theme]
    height = 480
    kicker = f"AHEAD {MIDDOT} AI BRIEF"
    desc = f"Sources: {AHEAD_META} {AHEAD_INDEX} {AHEAD_DIGEST}"
    if not vm["ok"]:
        return ck.svg_card(theme, height, vm["alt"], desc, render_failure(t, height, kicker, vm), "ahead", style=style)
    body = [
        el_kicker(t, kicker),
        el_headline(t, 64, vm["headline_num"], vm["headline_rest"]),
        el_mono(t, 212, vm["sources_line"], 32, t["secondary"]),
    ]
    if vm["state"] == "brief unavailable":
        body.append(el_mono(t, 326, vm["brief_line"], 32, t["primary"]))
    else:
        body.append(el_inter(t, 276, vm["brief_line"], 500, 36, t["primary"]))
        body.append(el_label(t, 326, vm["label"]))
        body.append(el_inter(t, 372, vm["title_line"], 600, 36, t["primary"]))
    body.append(el_footer(t, height, vm["footer"]))
    return ck.svg_card(theme, height, vm["alt"], desc, body, "ahead", style=style)


def render_radar(vm: dict, theme: str, style=None) -> str:
    t = ck.THEMES[theme]
    height = 480
    kicker = "STARTUP RADAR"
    desc = f"Sources: {RADAR_STATS} {RADAR_SOURCES}"
    if not vm["ok"]:
        return ck.svg_card(theme, height, vm["alt"], desc, render_failure(t, height, kicker, vm), "radar", style=style)
    body = [
        el_kicker(t, kicker),
        el_headline(t, 64, vm["headline_num"], vm["headline_rest"]),
        el_mono(t, 212, vm["recent_line"], 32, t["secondary"]),
    ]
    if vm["state"] == "source health unavailable":
        body.append(el_mono(t, 326, vm["health_line"], 32, t["primary"]))
    else:
        body.append(el_inter(t, 276, vm["sources_line"], 500, 36, t["primary"]))
        body.append(el_label(t, 326, vm["label"]))
        body.append(el_mono(t, 372, vm["not_ok_line"], 32, t["primary"]))
    body.append(el_footer(t, height, vm["footer"]))
    return ck.svg_card(theme, height, vm["alt"], desc, body, "radar", style=style)


def calendar_geometry(cols: int) -> dict:
    cell = (1040 // cols) - 4
    pitch = cell + 4
    return {"cols": cols, "cell": cell, "pitch": pitch, "x0": 100, "y0": 270}


def month_labels(weeks, geo: dict) -> list:
    """(column, label) pairs per design 3.5: column 0, then month changes >= 4 columns apart, inside x 1136."""
    labels = []
    last_drawn = None
    prev_month = None
    for i, week in enumerate(weeks):
        month = int(week[0]["date"][5:7])
        draw = i == 0 or month != prev_month
        prev_month = month
        if not draw:
            continue
        x = geo["x0"] + i * geo["pitch"]
        if last_drawn is not None and i - last_drawn < 4:
            continue
        if x + 54 > CONTENT_RIGHT:
            continue
        labels.append((i, ck.MONTHS[month - 1]))
        last_drawn = i
    return labels


def render_calendar(vm: dict, theme: str, style=None) -> str:
    t = ck.THEMES[theme]
    height = 500
    kicker = f"GITHUB CONTRIBUTIONS {MIDDOT} LAST 12 MONTHS"
    desc = f"Source: {GRAPHQL_URL} contributionCalendar"
    if not vm["ok"]:
        return ck.svg_card(theme, height, vm["alt"], desc, render_failure(t, height, kicker, vm), "calendar",
                           style=style)
    weeks = vm["weeks"]
    geo = calendar_geometry(len(weeks))
    cell, pitch, x0, y0 = geo["cell"], geo["pitch"], geo["x0"], geo["y0"]
    if y0 + 7 * pitch > height - 60 or cell < 4:
        fail(2, f"calendar grid does not fit ({len(weeks)} columns)")
    body = [
        el_kicker(t, kicker),
        el_headline(t, 72, vm["headline_num"], vm["headline_rest"]),
        el_inter(t, 200, vm["active_line"], 500, 36, t["secondary"]),
    ]
    for col, label in month_labels(weeks, geo):
        body.append(el_mono(t, 250, label, 30, t["secondary"], x=x0 + col * pitch, max_chars=3))
    for r, label in ((1, "M"), (3, "W"), (5, "F")):
        body.append(el_mono(t, y0 + r * pitch + cell / 2 + 11, label, 30, t["secondary"], max_chars=1))
    groups = {level: [] for level in range(5)}
    for i, week in enumerate(weeks):
        x = x0 + i * pitch
        for day in week:
            y = y0 + day["weekday"] * pitch
            groups[day["level"]].append(f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" rx="3"/>')
    for level in range(5):
        if groups[level]:
            body.append(f'<g fill="{t["levels"][level]}">' + "".join(groups[level]) + "</g>")
    body.append(el_footer(t, height, vm["footer"]))
    return ck.svg_card(theme, height, vm["alt"], desc, body, "calendar", style=style)


RENDERERS = {
    "products": render_products,
    "ahead": render_ahead,
    "radar": render_radar,
    "calendar": render_calendar,
}


# --- README and data.json --------------------------------------------------------------------


def block_for(card_id: str, alt: str, caption: str) -> str:
    if not CAPTION_RE.match(caption):
        fail(2, f"caption for {card_id} has a character outside the allowed set")
    return (
        "<picture>\n"
        f'  <source media="(prefers-color-scheme: dark)" srcset="assets/cards/{card_id}-dark.svg">\n'
        f'  <img src="assets/cards/{card_id}-light.svg" alt="{ck.esc_attr(alt)}" width="100%">\n'
        "</picture>\n"
        "\n"
        f"{caption}"
    )


def rewrite_readme(text: str, blocks: dict) -> str:
    """Replace everything strictly between each card's marker lines; exit 2 on bad markers or size."""
    lines = text.split("\n")
    for card_id, block in blocks.items():
        start, end = f"<!-- card:{card_id}:start -->", f"<!-- card:{card_id}:end -->"
        starts = [i for i, line in enumerate(lines) if line.strip() == start]
        ends = [i for i, line in enumerate(lines) if line.strip() == end]
        if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
            fail(2, f"README markers for {card_id} missing, duplicated or out of order")
        lines[starts[0] + 1:ends[0]] = block.split("\n")
    new_text = "\n".join(lines)
    size = len(new_text.encode("utf-8"))
    if size >= README_MAX_BYTES:  # design R13: README < 15,000 bytes
        fail(2, f"README would be {size} bytes (limit 15000)")
    return new_text


def strip_private(vm: dict) -> dict:
    """View model for data.json: the weeks grid is dropped (counts and dates stay)."""
    return {k: v for k, v in vm.items() if k != "weeks"}


# --- CLI ----------------------------------------------------------------------------------------


def parse_now(value) -> datetime:
    if not value:
        return datetime.now(timezone.utc).replace(microsecond=0)
    d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def parse_args(argv):
    p = argparse.ArgumentParser(description="Generate the profile README live cards.")
    p.add_argument("--fixtures", help="scenario file replayed instead of the network")
    p.add_argument("--now", help="fixed clock, ISO 8601 (default: current UTC time)")
    p.add_argument("--out", help="output directory (default: <repo>/assets/cards)")
    p.add_argument("--readme", help="README to rewrite (default: <repo>/README.md)")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    now = parse_now(args.now)
    out_dir = Path(args.out) if args.out else REPO_ROOT / "assets" / "cards"
    readme_path = Path(args.readme) if args.readme else REPO_ROOT / "README.md"
    style = ck.font_style(REPO_ROOT)  # exit 4 on hash mismatch, before any network call
    transport = FixtureTransport(args.fixtures) if args.fixtures else LiveTransport()
    token = os.environ.get("GITHUB_TOKEN")
    login = os.environ.get("GITHUB_LOGIN", "Sudish007")

    vms = {
        "products": build_products(transport, now),
        "ahead": build_ahead(transport, now),
        "radar": build_radar(transport, now),
        "calendar": build_calendar(transport, now, token, login),
    }

    svgs = {}
    for card_id in CARDS:
        for theme in ("light", "dark"):
            data = RENDERERS[card_id](vms[card_id], theme, style).encode("utf-8")
            problems = ck.validate_svg(data, card_id)
            if problems:
                for problem in problems:
                    print(f"svg {card_id}-{theme} invalid: {problem}", file=sys.stderr)
                raise SystemExit(3)
            svgs[f"{card_id}-{theme}.svg"] = data
            log(f"svg {card_id}-{theme} {len(data)}B ok")

    blocks = {card_id: block_for(card_id, vms[card_id]["alt"], vms[card_id]["caption"]) for card_id in CARDS}
    readme_text = readme_path.read_bytes().decode("utf-8")
    new_readme = rewrite_readme(readme_text, blocks)

    stamp = ck.fmt_ist(now)
    payload = {
        "generated": {"iso": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "ist": stamp},
        "cards": {card_id: strip_private(vm) for card_id, vm in vms.items()},
    }
    data_json = json.dumps(payload, ensure_ascii=True, indent=1, sort_keys=True) + "\n"

    out_dir.mkdir(parents=True, exist_ok=True)
    for name, data in svgs.items():
        (out_dir / name).write_bytes(data)
    (out_dir / "data.json").write_bytes(data_json.encode("ascii"))
    readme_path.write_bytes(new_readme.encode("utf-8"))
    log(f"readme blocks={len(blocks)} ok")

    gh_output = os.environ.get("GITHUB_OUTPUT")
    if gh_output:
        with open(gh_output, "a", encoding="utf-8") as fh:
            fh.write(f"stamp={stamp}\n")
    log(f"stamp {stamp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
