#!/usr/bin/env python3
"""
vcf_logs_harvester.py - Pull log events from VMware Aria Operations for Logs
(formerly vRealize Log Insight) by text match over a time window.

- Authenticates with Local or Active Directory (or vIDM) credentials
- Filters on one or more strings in the event text (e.g. "DROP"), plus
  optional field filters (e.g. appname=vpxd)
- Pages through results in 20,000-event chunks (ascending by timestamp)
  until the whole window is captured, de-duplicating at page boundaries
- Re-authenticates automatically if the session expires mid-run
- Writes raw log lines (default), JSON Lines, or CSV

Requires: Python 3.8+ only. No extra packages to install.

Examples
--------
  # Local account, last 24 hours, events containing DROP, raw log lines
  python vcf_logs_harvester.py --host li.example.com --user admin --provider Local \
      --contains DROP --last 24h --out drops.log

  # Only the vCenter syslog (vpxd) lines, not the vCenter-integration copies
  python vcf_logs_harvester.py --host li.example.com --user admin \
      --contains Administrator --field appname=vpxd --last 24h --out admin.log

  # Events mentioning a host, by its IP: the name is resolved to IP(s) first
  python vcf_logs_harvester.py --host li.example.com --user admin \
      --contains Administrator --resolve jumpbox01.corp.example.com --last 24h

  # Full events with every field, as JSON Lines
  python vcf_logs_harvester.py --host li.example.com --user admin \
      --contains DROP --last 24h --out drops.jsonl

  # AD account, explicit window, two strings (either one matches), CSV output
  python vcf_logs_harvester.py --host li.example.com --user jsmith --domain corp.example.com \
      --provider ActiveDirectory --contains DROP --contains DENY --match any \
      --start "2026-09-26 00:00" --end "2026-09-27 00:00" --tz America/Chicago \
      --out drops.csv --format csv

You are always prompted for your password when the script runs (input is hidden).
"""

import argparse
import csv
import getpass
import hashlib
import ipaddress
import json
import os
import re
import socket
import sys
import time
from datetime import datetime, timedelta, timezone
import ssl
import urllib.error
import urllib.request
from urllib.parse import quote, urlencode

MAX_LIMIT = 20000  # API maximum events per query

# Bumped whenever the GUI starts relying on something new in this file, so the
# GUI can tell when it's been paired with an older copy of this script.
CORE_API = 3       # 3 = harvest() + summarize() + built-in HTTPS (no requests)


# --------------------------------------------------------------------------- #
# Minimal HTTPS client (standard library only, so nothing needs installing)
# --------------------------------------------------------------------------- #
class RequestError(Exception):
    """Network-level failure: connection refused, timeout, DNS, TLS..."""


class TLSError(RequestError):
    """The server's certificate couldn't be verified."""


class Response:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self.text = body.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.text)


class HttpSession:
    def __init__(self, verify=True):
        self.headers = {"Accept": "application/json"}
        self.ctx = ssl.create_default_context()
        if not verify:
            self.ctx.check_hostname = False
            self.ctx.verify_mode = ssl.CERT_NONE

    def request(self, method, url, params=None, json_body=None, timeout=60):
        if params:
            url += "?" + urlencode(params)
        data = None
        headers = dict(self.headers)
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=self.ctx) as r:
                return Response(r.status, r.read())
        except urllib.error.HTTPError as e:      # 4xx/5xx still carry a body
            return Response(e.code, e.read() or b"")
        except urllib.error.URLError as e:
            if isinstance(e.reason, ssl.SSLError):
                raise TLSError(str(e.reason)) from None
            raise RequestError(str(e.reason)) from None
        except ssl.SSLError as e:
            raise TLSError(str(e)) from None
        except (socket.timeout, TimeoutError):
            raise RequestError(f"timed out after {timeout:.0f}s") from None
        except OSError as e:
            raise RequestError(str(e)) from None

    def get(self, url, params=None, timeout=60):
        return self.request("GET", url, params=params, timeout=timeout)

    def post(self, url, json=None, timeout=60):
        return self.request("POST", url, json_body=json, timeout=timeout)


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #
class AriaLogsClient:
    def __init__(self, host, port, username, password, provider, domain=None,
                 verify=True, timeout_ms=120000, retries=3):
        self.base = f"https://{host}:{port}/api/v2"
        self.username = username
        self.password = password
        self.provider = provider
        self.domain = domain
        self.verify = verify
        self.timeout_ms = timeout_ms
        self.retries = retries
        self.http = HttpSession(verify=verify)
        self.token = None

    # -- auth ---------------------------------------------------------------
    def _login_username(self):
        # AD / vIDM accounts are sent as user@domain unless already qualified
        if self.provider != "Local" and self.domain and "@" not in self.username \
                and "\\" not in self.username:
            return f"{self.username}@{self.domain}"
        return self.username

    def login(self):
        body = {"username": self._login_username(),
                "password": self.password,
                "provider": self.provider}
        try:
            r = self.http.post(f"{self.base}/sessions", json=body, timeout=60)
        except TLSError as e:
            raise SystemExit(f"TLS error connecting to {self.base} "
                             f"(self-signed cert? add --insecure): {e}")
        except RequestError as e:
            raise SystemExit(f"Can't reach {self.base}: {e}")
        if r.status_code != 200:
            raise SystemExit(f"Authentication failed ({r.status_code}): {r.text[:500]}")
        data = r.json()
        self.token = data["sessionId"]
        self.http.headers["Authorization"] = f"Bearer {self.token}"
        log(f"Authenticated as {body['username']} via {self.provider} "
            f"(session ttl {data.get('ttl', '?')}s)")

    # -- query --------------------------------------------------------------
    @staticmethod
    def _seg(field, op, value):
        return f"/{quote(field, safe='')}/{quote(f'{op}{value}', safe='')}"

    def build_path(self, constraints, start_ms, end_ms, start_inclusive=True):
        """constraints: list of (field, operator, value) tuples."""
        path = "/events"
        for field, op, val in constraints:
            path += self._seg(field, f"{op} ", val)
        path += self._seg("timestamp", ">=" if start_inclusive else ">", start_ms)
        path += self._seg("timestamp", "<", end_ms)
        return path

    def query(self, path, limit):
        params = {"limit": limit,
                  "timeout": self.timeout_ms,
                  "order-by-direction": "ASC"}
        timeout_ms = self.timeout_ms
        for attempt in range(1, self.retries + 1):
            params["timeout"] = timeout_ms
            try:
                r = self.http.get(self.base + path, params=params,
                                  timeout=timeout_ms / 1000 + 60)
            except RequestError as e:
                log(f"  request error ({e}); retry {attempt}/{self.retries}")
                time.sleep(2 * attempt)
                continue

            if r.status_code == 401:  # session expired -> re-auth and retry
                log("  session expired, re-authenticating")
                self.login()
                continue
            if r.status_code >= 500 or r.status_code == 429:
                log(f"  server returned {r.status_code}; retry {attempt}/{self.retries}")
                time.sleep(5 * attempt)
                continue
            if r.status_code != 200:
                raise SystemExit(f"Query failed ({r.status_code}): {r.text[:500]}")

            data = r.json()
            if data.get("complete", True) is False and attempt < self.retries:
                timeout_ms = min(timeout_ms * 2, 600000)
                log(f"  query incomplete (timed out server-side); "
                    f"retrying with timeout {timeout_ms} ms")
                continue
            if data.get("complete", True) is False:
                log("  WARNING: query still incomplete after retries; results for "
                    "this page may be partial. Try a narrower window or --timeout.")
            return data.get("events", [])
        raise SystemExit("Query failed after retries.")


# --------------------------------------------------------------------------- #
# Paging
# --------------------------------------------------------------------------- #
def event_key(ev):
    """Stable identity for de-duplication at page boundaries."""
    fields = {f.get("name"): f.get("content") for f in ev.get("fields", [])}
    raw = json.dumps([ev.get("timestamp"), ev.get("text"),
                      fields.get("hostname"), fields.get("source"),
                      fields.get("__li_source_path")], sort_keys=True, default=str)
    return hashlib.sha1(raw.encode()).hexdigest()


def pull_all(client, text_constraints, start_ms, end_ms, page_size):
    """Yield every matching event in [start_ms, end_ms), paging by timestamp."""
    cursor = start_ms
    inclusive = True
    boundary_keys = set()   # keys of events at the current cursor timestamp
    page = 0
    while cursor < end_ms:
        page += 1
        path = client.build_path(text_constraints, cursor, end_ms, inclusive)
        events = client.query(path, page_size)
        log(f"  page {page}: {len(events)} events from "
            f"{ms_to_iso(cursor)}{'' if inclusive else ' (exclusive)'}")
        if not events:
            break

        new = 0
        for ev in events:
            k = event_key(ev)
            if k in boundary_keys:
                continue
            new += 1
            yield ev

        if len(events) < page_size:
            break  # last page

        last_ts = int(events[-1]["timestamp"])
        if last_ts == cursor and new == 0:
            # A full page shares one timestamp and we have seen it all:
            # more than page_size events in a single millisecond. Skip past it.
            log(f"  WARNING: >{page_size} events at {ms_to_iso(cursor)}; "
                "some at that exact millisecond may be missed.")
            cursor, inclusive, boundary_keys = last_ts, False, set()
            continue

        if last_ts != cursor:
            boundary_keys = set()
        # remember everything at the last timestamp so the overlap is skipped
        boundary_keys |= {event_key(e) for e in events
                          if int(e["timestamp"]) == last_ts}
        cursor, inclusive = last_ts, True


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
def resolve_fields(ev):
    """Some fields come back as a position in the text (startPosition/length)
    instead of a value. Fill in 'content' for those from the event text."""
    text = ev.get("text") or ""
    for f in ev.get("fields", []):
        if "content" not in f and "startPosition" in f and "length" in f:
            s, n = int(f["startPosition"]), int(f["length"])
            f["content"] = text[s:s + n]
    return ev


def flatten(ev):
    row = {"timestamp": ev.get("timestamp"),
           "time_utc": ms_to_iso(ev.get("timestamp")),
           "text": ev.get("text")}
    for f in ev.get("fields", []):
        name = f.get("name")
        if name and name not in row:
            row[name] = f.get("content")
    return row


class Writer:
    def __init__(self, path, fmt):
        self.fmt = fmt
        self.fh = sys.stdout if path == "-" else open(path, "w", newline="",
                                                      encoding="utf-8")
        self.rows = []  # CSV buffers so the header covers every field seen
        self.count = 0

    def write(self, ev):
        resolve_fields(ev)
        if self.fmt == "text":
            # the raw log line exactly as stored, blank line between events
            if self.count:
                self.fh.write("\n")
            self.fh.write((ev.get("text") or "").rstrip("\r\n") + "\n")
            self.count += 1
        elif self.fmt == "jsonl":
            self.fh.write(json.dumps(ev, default=str) + "\n")
        else:
            self.rows.append(flatten(ev))

    def close(self):
        if self.fmt == "csv":
            cols = ["timestamp", "time_utc", "text"]
            for r in self.rows:
                for k in r:
                    if k not in cols:
                        cols.append(k)
            w = csv.DictWriter(self.fh, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(self.rows)
        if self.fh is not sys.stdout:
            self.fh.close()


# --------------------------------------------------------------------------- #
# Time helpers
# --------------------------------------------------------------------------- #
def log(msg):
    print(msg, file=sys.stderr, flush=True)


def ms_to_iso(ms):
    if ms is None:
        return ""
    return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc) \
        .strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def get_tz(name):
    if not name:
        return datetime.now().astimezone().tzinfo
    if name.upper() == "UTC":
        return timezone.utc
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        raise SystemExit(f"Unknown time zone: {name}")


def parse_time(value, tz):
    """Accept epoch ms, epoch s, or an ISO-ish date/time string."""
    v = value.strip()
    if re.fullmatch(r"\d{13}", v):
        return int(v)
    if re.fullmatch(r"\d{10}", v):
        return int(v) * 1000
    v = v.replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.fromisoformat(v) if fmt is None else datetime.strptime(v, fmt)
            break
        except ValueError:
            continue
    else:
        raise SystemExit(f"Can't parse time: {value}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return int(dt.timestamp() * 1000)


def parse_duration(value):
    m = re.fullmatch(r"(\d+)\s*([smhdw])", value.strip().lower())
    if not m:
        raise SystemExit(f"Bad --last value '{value}' (use e.g. 30m, 12h, 7d)")
    n, unit = int(m.group(1)), m.group(2)
    return timedelta(**{{"s": "seconds", "m": "minutes", "h": "hours",
                         "d": "days", "w": "weeks"}[unit]: n})


def resolve_name(name):
    """Resolve a short name or FQDN to its IP addresses (IPv4 first)."""
    try:
        ipaddress.ip_address(name)
        log(f"{name} is already an IP address")
        return [name]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(name, None)
    except socket.gaierror as e:
        raise SystemExit(f"Couldn't resolve '{name}' to an IP address: {e}")
    ips = []
    for family in (socket.AF_INET, socket.AF_INET6):
        for fam, *_rest, sockaddr in infos:
            ip = sockaddr[0]
            if fam == family and ip not in ips:
                ips.append(ip)
    if not ips:
        raise SystemExit(f"'{name}' resolved to no IP addresses")
    log(f"Resolved {name} -> {', '.join(ips)}")
    return ips


def ip_regex(ip):
    """Match the IP as a whole address, not as part of a longer one."""
    return re.compile(r"(?<![\w.:])" + re.escape(ip) + r"(?![\w:]|\.\d)", re.IGNORECASE)


def server_constraints(constraints, resolved_ips):
    """Pick one filter per field for the server query (it ORs same-field filters).
    Prefer an IP, then any positive match, over NOT_CONTAINS."""
    def rank(c):
        field, op, val = c
        if val in resolved_ips:
            return 0
        return 2 if op.startswith("NOT") else 1
    chosen = {}
    for c in constraints:
        f = c[0]
        if f not in chosen or rank(c) < rank(chosen[f]):
            chosen[f] = c
    return list(chosen.values())


def _term_regex(value):
    """Whole-term, case-insensitive match; '*' is a wildcard, like the server."""
    body = ".*?".join(re.escape(part) for part in value.split("*"))
    left = "" if value.startswith("*") else r"(?<!\w)"
    right = "" if value.endswith("*") else r"(?!\w)"
    return re.compile(left + body + right, re.IGNORECASE)


def constraint_check(constraint, resolved_ips):
    """Return a function(event) -> bool that re-checks one filter locally."""
    field, op, val = constraint

    def get(ev):
        if field == "text":
            return ev.get("text") or ""
        for f in ev.get("fields", []):
            if f.get("name") == field:
                return str(f.get("content", ""))
        return None

    if val in resolved_ips:
        rx = ip_regex(val)
    elif op == "MATCHES_REGEX":
        rx = re.compile(val, re.IGNORECASE)
    else:
        rx = _term_regex(val)

    if op.startswith("NOT"):
        return lambda ev: not rx.search(get(ev) or "")
    return lambda ev: (get(ev) is not None) and bool(rx.search(get(ev)))


def get_password(user, host):
    """Always prompt the user for their password (input is hidden)."""
    prompt = f"Password for {user}@{host}: "
    try:
        pw = getpass.getpass(prompt, stream=sys.stderr)
    except (EOFError, KeyboardInterrupt):
        raise SystemExit("\nNo password entered.")
    if not pw:
        raise SystemExit("No password entered.")
    return pw


# --------------------------------------------------------------------------- #
# Summary / analysis (runs locally; nothing leaves this machine)
# --------------------------------------------------------------------------- #
_HDR_TIME = re.compile(r"^(?:\d{4}-\d\d-\d\d[T ][\d:.]+(?:Z|[+-]\d\d:?\d\d)?|"
                       r"[A-Z][a-z]{2} +\d{1,2} \d\d:\d\d:\d\d)\s+")
_HDR_HOST_APP = re.compile(r"^(?P<host>[\w.\-]+)\s+(?P<app>[\w.\-/]+?)(?:\[\d+\])?:\s*")
# Parts of a message that vary, in priority order. Applied in one pass.
_MASKS = [
    ("UUID", r"(?i:\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b)"),
    ("MAC",  r"(?i:\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b)"),
    ("TIME", r"\d{4}-\d\d-\d\d[T ]\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:?\d\d)?"),
    ("TIME", r"\b\d{1,2}:\d\d:\d\d(?:\.\d+)?\b"),
    ("IP",   r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?(?![\d])"),
    ("HEX",  r"(?i:\b0x[0-9a-f]+\b|\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{8,}\b)"),
    ("N",    r"(?<![\w<])[-+]?\d+(?:\.\d+)?(?![\w>])"),
]
# the leading lookahead lets the regex engine skip most characters quickly
_MASK_RX = re.compile(r"(?=[0-9A-Fa-f+\-])(?:" +
                      "|".join(f"(?P<m{i}>{rx})" for i, (_, rx) in enumerate(_MASKS)) + ")")
_MASK_OUT = {f"m{i}": f"<{name}>" for i, (name, _) in enumerate(_MASKS)}
_USER_RES = [
    re.compile(r"\buser[= :]+['\"]?([A-Za-z0-9_.\\$-]+(?:@[A-Za-z][\w.-]*)?)", re.I),
    re.compile(r"\bfor (?:invalid user |illegal user )?([A-Za-z0-9_.\\$-]+) from\b", re.I),
]
_USER_FIELDS = ("vc_username", "user", "username", "usr")
_NOT_USERS = {"agent", "name", "id", "is", "session", "sessions", "login", "logged",
              "account", "unknown", "none", "null", "the", "a", "to", "from", "and"}
_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d])")

# (name, severity, regex). Keywords below are a cheap first check before the regex.
HIGHLIGHT_RULES = [
    ("Failed logins", "critical",
     r"failed password|authentication failure|auth(?:entication)? failed|login failed|"
     r"failed to (?:authenticate|log ?in)|cannot login|invalid user|illegal user|"
     r"BadUsernameSessionEvent|incorrect password|bad password"),
    ("Account lockouts", "critical", r"locked out|account (?:is |has been )?locked|lockout"),
    ("Firewall drops / denies", "warning", r"\b(?:DROP|DENY|DENIED|REJECT(?:ED)?|BLOCKED)\b"),
    ("Errors & failures", "warning",
     r"\b(?:error|errors|failed|failure|fatal|critical|panic|exception)\b"),
    ("Privilege use (sudo / su)", "info", r"\bsudo(?:\[\d+\])?:|\bsu(?:\[\d+\])?:|\bCOMMAND="),
    ("Successful logins", "info",
     r"accepted (?:password|publickey|keyboard-interactive)|UserLoginSessionEvent|"
     r"\blogged in\b|session opened for user"),
]
_HL_KEYWORDS = {
    "Failed logins": ("fail", "invalid user", "illegal user", "badusername", "incorrect password",
                      "bad password", "cannot login"),
    "Account lockouts": ("lock",),
    "Firewall drops / denies": ("drop", "deny", "denied", "reject", "blocked"),
    "Errors & failures": ("error", "fail", "fatal", "critical", "panic", "exception"),
    "Privilege use (sudo / su)": ("sudo", "su:", "su[", "command="),
    "Successful logins": ("accepted", "userloginsession", "logged in", "session opened"),
}
_HL = [(n, sev, re.compile(rx, re.I), _HL_KEYWORDS[n]) for n, sev, rx in HIGHLIGHT_RULES]
_BUCKETS_MS = [1000, 5000, 10000, 30000, 60000, 300000, 600000, 900000, 1800000,
               3600000, 3 * 3600000, 6 * 3600000, 12 * 3600000, 86400000, 7 * 86400000]


def _field(ev, name):
    for f in ev.get("fields", []):
        if f.get("name") == name and f.get("content"):
            return str(f["content"])
    return None


def split_header(text):
    """'<time> <host> <app>[pid]: msg' -> (host, app, msg). Parts may be None."""
    rest = _HDR_TIME.sub("", text, count=1)
    m = _HDR_HOST_APP.match(rest)
    if m:
        return m.group("host"), m.group("app"), rest[m.end():]
    return None, None, rest


def log_pattern(msg):
    """Replace the parts of a message that vary (IPs, numbers, ids...) with placeholders."""
    msg = _MASK_RX.sub(lambda m: _MASK_OUT[m.lastgroup], msg)
    return " ".join(msg.split())[:300]


def _users_in(ev, text):
    found = set()
    for name in _USER_FIELDS:
        v = _field(ev, name)
        if v:
            found.add(v)
    low = text.lower()
    if "user" not in low and " from " not in low:
        return found
    for rx in _USER_RES:
        for u in rx.findall(text):
            u = u.rstrip(".,;:)'\"")
            if u and u.lower() not in _NOT_USERS and not u.isdigit():
                found.add(u)
    return found


def summarize(events, start_ms=None, end_ms=None, max_buckets=60):
    """Group events into patterns and count hosts, apps, users, IPs, highlights
    and events over time. Each group keeps the indexes of its events."""
    from collections import defaultdict
    patterns, hosts, apps, users, ips = (defaultdict(list) for _ in range(5))
    highlights = {n: [] for n, _, _, _ in _HL}
    stamps = []
    for i, ev in enumerate(events):
        text = ev.get("text") or ""
        host, app, msg = split_header(text)
        host = _field(ev, "hostname") or host
        app = _field(ev, "appname") or app
        if host:
            hosts[host].append(i)
        if app:
            apps[app].append(i)
        patterns[((app + ": ") if app else "") + log_pattern(msg)].append(i)
        for u in _users_in(ev, text):
            users[u].append(i)
        for ip in set(_IPV4.findall(text)):
            ips[ip].append(i)
        low = text.lower()
        for name, _, rx, keys in _HL:
            if any(k in low for k in keys) and rx.search(text):
                highlights[name].append(i)
        stamps.append(int(ev.get("timestamp") or 0))

    lo = start_ms if start_ms is not None else (min(stamps) if stamps else 0)
    hi = end_ms if end_ms is not None else ((max(stamps) + 1) if stamps else 1)
    span = max(hi - lo, 1)
    step = next((b for b in _BUCKETS_MS if span / b <= max_buckets), _BUCKETS_MS[-1])
    lo = lo - lo % step
    n = max(1, -(-(hi - lo) // step))
    buckets = [[] for _ in range(n)]
    for i, t in enumerate(stamps):
        b = min(max((t - lo) // step, 0), n - 1)
        buckets[b].append(i)

    rank = lambda d: sorted(d.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    sev = {n: s for n, s, _, _ in _HL}
    hl = []
    for name, idx in highlights.items():
        if idx:
            hs = sorted({(_field(events[i], "hostname") or split_header(events[i].get("text") or "")[0] or "?")
                         for i in idx})
            hl.append({"name": name, "severity": sev[name], "idx": idx, "hosts": hs,
                       "sample": (events[idx[0]].get("text") or "")[:240]})
    return {"total": len(events), "start": lo, "end": lo + n * step, "bucket_ms": step,
            "buckets": buckets, "patterns": rank(patterns), "hosts": rank(hosts),
            "apps": rank(apps), "users": rank(users), "ips": rank(ips), "highlights": hl}


def format_summary(s, top=10):
    """Plain-text version of summarize() for the command line."""
    def dur(ms):
        for unit, size in (("d", 86400000), ("h", 3600000), ("m", 60000), ("s", 1000)):
            if ms >= size and ms % size == 0:
                return f"{ms // size}{unit}"
        return f"{ms}ms"
    out = [f"=== Summary: {s['total']:,} events, {len(s['patterns']):,} patterns, "
           f"{len(s['hosts']):,} hosts ==="]
    counts = [len(b) for b in s["buckets"]]
    if counts and max(counts):
        peak = counts.index(max(counts))
        out.append(f"Busiest {dur(s['bucket_ms'])}: {max(counts):,} events starting "
                   f"{ms_to_iso(s['start'] + peak * s['bucket_ms'])}")
    if s["highlights"]:
        out.append("\nHighlights:")
        for h in s["highlights"]:
            out.append(f"  {len(h['idx']):>7,}  {h['name']}  "
                       f"({len(h['hosts'])} host{'s' if len(h['hosts']) != 1 else ''}: "
                       f"{', '.join(h['hosts'][:5])}{' …' if len(h['hosts']) > 5 else ''})")
    out.append("\nTop patterns:")
    for p, idx in s["patterns"][:top]:
        out.append(f"  {len(idx):>7,}  {p[:150]}")
    rare = [p for p, idx in s["patterns"] if len(idx) == 1]
    if rare and len(s["patterns"]) > top:
        out.append(f"\nRare patterns (seen once): {len(rare):,}")
        for p in rare[:top]:
            out.append(f"        1  {p[:150]}")
    for title, key in (("Hosts", "hosts"), ("Apps", "apps"), ("Users", "users"), ("IPs", "ips")):
        if s[key]:
            out.append(f"\nTop {title.lower()}: " + ",  ".join(
                f"{k} ({len(v):,})" for k, v in s[key][:top]))
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# Search (shared by the command line and the GUI)
# --------------------------------------------------------------------------- #
def harvest(client, contains, match, operator, field_constraints, resolved_ips,
            start_ms, end_ms, page_size, on_event, should_stop=None):
    """Run the search and call on_event(ev) for every matching event.
    Returns the number of events delivered. should_stop() -> True cancels."""
    # AND = one query with every constraint; OR = one query per string, merged
    if not contains:
        query_sets = [[]]
    elif match == "all" or len(contains) == 1:
        query_sets = [[("text", operator, s) for s in contains]]
    else:
        query_sets = [[("text", operator, s)] for s in contains]

    # Resolved IPs: the text must also contain one of them (any IP of any name)
    ip_patterns = []
    if resolved_ips:
        query_sets = [qs + [("text", "CONTAINS", ip)]
                      for qs in query_sets for ip in resolved_ips]
        ip_patterns = [ip_regex(ip) for ip in resolved_ips]
    query_sets = [qs + list(field_constraints) for qs in query_sets]

    seen = set() if len(query_sets) > 1 else None
    total = 0
    for constraints in query_sets:
        # The server ORs filters on the same field, so send it only one per
        # field and check every filter here on each returned event.
        server = server_constraints(constraints, resolved_ips)
        checks = [constraint_check(c, resolved_ips) for c in constraints]
        log("Match: " + " AND ".join(f"{f} {op} '{v}'" for f, op, v in constraints))
        log("Server query: " + " AND ".join(f"{f} {op} '{v}'" for f, op, v in server))
        for ev in pull_all(client, server, start_ms, end_ms, page_size):
            if should_stop and should_stop():
                log("Stopped.")
                return total
            resolve_fields(ev)
            if not all(chk(ev) for chk in checks):
                continue
            # exact-IP check, so 192.168.1.1 doesn't also match 192.168.1.10
            if ip_patterns and not any(rx.search(ev.get("text") or "")
                                       for rx in ip_patterns):
                continue
            if seen is not None:
                k = event_key(ev)
                if k in seen:
                    continue
                seen.add(k)
            on_event(ev)
            total += 1
    return total


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    p = argparse.ArgumentParser(
        description="Pull Aria Operations for Logs events by text match.",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    p.add_argument("--host", required=True, help="Log Insight / Ops for Logs FQDN or IP")
    p.add_argument("--port", type=int, default=9543)
    p.add_argument("--user", required=True)
    p.add_argument("--provider", default="Local",
                   choices=["Local", "ActiveDirectory", "vIDM"],
                   help="Auth provider (default Local)")
    p.add_argument("--domain", help="AD domain, appended as user@domain if not already present")
    p.add_argument("--contains", "--text", dest="contains", action="append",
                   metavar="STRING",
                   help="String to match in the text field (--text works too); "
                        "repeat for several")
    p.add_argument("--resolve", action="append", default=[], metavar="NAME",
                   help="Short name or FQDN to resolve to its IP address(es) on this "
                        "machine; events must contain one of those IPs in the text. "
                        "Repeat for several names (an event matching any of them counts)")
    p.add_argument("--match", choices=["any", "all"], default="all",
                   help="With several --contains: 'all' = every string (default), "
                        "'any' = at least one")
    p.add_argument("--operator", default="CONTAINS",
                   choices=["CONTAINS", "HAS", "MATCHES_REGEX", "NOT_CONTAINS"],
                   help="Text operator (default CONTAINS)")
    p.add_argument("--field", action="append", default=[], metavar="NAME=VALUE",
                   help="Also require a field to contain a value, e.g. appname=vpxd "
                        "or hostname=alderstead; repeat for several")
    g = p.add_argument_group("time window (use --last OR --start/--end)")
    g.add_argument("--last", help="Relative window ending now, e.g. 30m, 24h, 7d")
    g.add_argument("--start", help="Start time: ISO date/time or epoch")
    g.add_argument("--end", help="End time (default now)")
    g.add_argument("--tz", help="Time zone for --start/--end without an offset "
                                "(default: this machine's local zone), e.g. America/Chicago")
    p.add_argument("--out", default="-", help="Output file (default stdout)")
    p.add_argument("--format", choices=["text", "jsonl", "csv"], default=None,
                   help="text = raw log lines only; jsonl = full event with all "
                        "fields; csv = one column per field. Default: csv for "
                        ".csv, jsonl for .json/.jsonl, otherwise text")
    p.add_argument("--summary", action="store_true",
                   help="After the run, print a summary (patterns, hosts, users, IPs, "
                        "highlights, busiest period) to stderr")
    p.add_argument("--page-size", type=int, default=MAX_LIMIT,
                   help=f"Events per request, max {MAX_LIMIT}")
    p.add_argument("--timeout", type=int, default=120000,
                   help="Server-side query timeout in ms (default 120000)")
    p.add_argument("--insecure", action="store_true",
                   help="Skip TLS certificate verification (self-signed certs)")
    args = p.parse_args()

    # time window
    tz = get_tz(args.tz)
    now_ms = int(time.time() * 1000)
    if args.last:
        if args.start:
            p.error("use --last or --start, not both")
        end_ms = now_ms
        start_ms = end_ms - int(parse_duration(args.last).total_seconds() * 1000)
    elif args.start:
        start_ms = parse_time(args.start, tz)
        end_ms = parse_time(args.end, tz) if args.end else now_ms
    else:
        p.error("give a time window with --last or --start")
    if start_ms >= end_ms:
        p.error("start must be before end")

    page_size = max(1, min(args.page_size, MAX_LIMIT))
    out_lower = args.out.lower()
    fmt = args.format or ("csv" if out_lower.endswith(".csv") else
                          "jsonl" if out_lower.endswith((".json", ".jsonl")) else
                          "text")

    field_constraints = []
    for spec in args.field:
        name, sep, value = spec.partition("=")
        if not sep or not name.strip() or not value.strip():
            p.error(f"--field needs NAME=VALUE, got '{spec}'")
        field_constraints.append((name.strip(), "CONTAINS", value.strip()))

    if not args.contains and not args.resolve:
        p.error("give at least one --contains or --resolve")

    # Resolve names to IPs up front, before asking for a password
    resolved_ips = []
    for name in args.resolve:
        for ip in resolve_name(name):
            if ip not in resolved_ips:
                resolved_ips.append(ip)

    password = get_password(args.user, args.host)

    client = AriaLogsClient(args.host, args.port, args.user, password, args.provider,
                            domain=args.domain, verify=not args.insecure,
                            timeout_ms=args.timeout)
    client.login()

    log(f"Window: {ms_to_iso(start_ms)} -> {ms_to_iso(end_ms)}")
    writer = Writer(args.out, fmt)
    collected = []

    def on_event(ev):
        writer.write(ev)
        if args.summary:
            collected.append(ev)
    try:
        total = harvest(client, args.contains or [], args.match, args.operator,
                        field_constraints, resolved_ips, start_ms, end_ms,
                        page_size, on_event)
    finally:
        writer.close()
    log(f"Done: {total} events written to {'stdout' if args.out == '-' else args.out}")
    if args.summary and collected:
        log("\n" + format_summary(summarize(collected, start_ms, end_ms)))


if __name__ == "__main__":
    main()
