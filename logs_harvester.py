#!/usr/bin/env python3
"""
logs_harvester.py

Extract log events from VMware vRealize Log Insight / Aria Operations for Logs
(vRLI 8.18) via its REST API, filtered on whatever fields you specify.

=====================================================================
AUTH
=====================================================================
Provide credentials via CLI args or environment variables:
    VRLI_HOST       e.g. loginsight.example.com
    VRLI_USERNAME
    VRLI_PASSWORD
    VRLI_PROVIDER   Local (default), ActiveDirectory, or vIDM
    VRLI_DNS_SERVER
    VRLI_CA_CERT

=====================================================================
FILTERING ON FIELDS
=====================================================================
--filter is the core of this script. Repeatable; every instance is ANDed
together. Two forms:
    field=value     substring match (CONTAINS)
    field==value    exact match (EQ)

Field names can be:
    - a "static" field present on every event (hostname, source, appname, ...)
    - a content-pack EXTRACTED field, which must use its namespaced form:
          namespace:fieldname
      e.g. com.vmware.nsxt:vmw_nsxt_firewall_action
      and you MUST also pass --content-pack <namespace> for that namespace's
      fields to be included in the response at all (the API silently omits
      them otherwise).
    - "text" -- the special field name for the raw log message (full-text
      search). Used automatically if you don't pass any --filter at all.

IMPORTANT CAVEAT, learned the hard way: some content-pack extracted fields
are only ever computed by the LogInsight UI's live rendering, and the REST
API always returns them as null -- even when your filter correctly matches
against them, and regardless of --content-pack, --view, or API version.
There is no reliable way to predict this in advance; you have to check.
If a field you need always comes back null, use --extract instead (see below)
to pull the value straight out of the raw text with your own regex.

=====================================================================
CLIENT-SIDE EXTRACTION (--extract)
=====================================================================
For fields the API won't give you a value for, --extract pulls it out of
each event's raw text yourself, after fetching:
    --extract fieldname=regex
The regex should have one capture group (the value); with no group, the
whole match is used. Repeatable.

=====================================================================
OPTIONAL: RESOLVE A VM NAME TO AN IP, THEN FILTER BY IT
=====================================================================
--vm (shortname or FQDN) resolves to an IP via DNS, which is then used as
the value for one --filter-style constraint, on whichever field you name
with --ip-field (default: "text", i.e. full-text search for the IP).

=====================================================================
EXAMPLES
=====================================================================
    # Everything in the last hour, no filtering
    python3 logs_harvester.py --host vrli.example.com --username admin --hours 1

    # Filter on a static field
    python3 logs_harvester.py --host vrli.example.com --username admin \\
        --filter hostname==esxi01.corp.internal --hours 24

    # Filter on a content-pack extracted field (NSX firewall drops)
    python3 logs_harvester.py --host vrli.example.com --username admin \\
        --filter com.vmware.nsxt:vmw_nsxt_firewall_action=drop \\
        --content-pack com.vmware.nsxt --hours 48 \\
        --format csv -o drops.csv \\
        --columns timestamp,hostname,com.vmware.nsxt:vmw_nsxt_firewall_src,com.vmware.nsxt:vmw_nsxt_firewall_dst

    # A field that only ever comes back null via the API: filter on the raw
    # text pattern instead, then pull the value out client-side
    python3 logs_harvester.py --host vrli.example.com --username admin \\
        --filter text=sub=vpxLro \\
        --extract vmw_esxi_sub="sub=(\\S+)" \\
        --minutes 30

    # Built-in preset: NSX DFW packet logs, parsed into clean columns
    # (equivalent to manually passing all the --extract patterns above)
    python3 logs_harvester.py --host vrli.example.com --username admin \\
        --preset nsx-dfw --hours 24 --format csv -o dfw_events.csv

    # Same preset, narrowed to DROPs only (extra --filter added on top)
    python3 logs_harvester.py --host vrli.example.com --username admin \\
        --preset nsx-dfw --filter text="match DROP" --hours 24

    # Resolve a VM name to an IP, search for it in a specific field
    python3 logs_harvester.py --host vrli.example.com --username admin \\
        --vm myvm01 --domain corp.example.com --ip-field vmw_host \\
        --hours 6

    # Self-signed cert with no usable SAN: pin the cert, skip hostname check
    python3 logs_harvester.py --host vrli.example.com --username admin \\
        --ca-cert vrli.pem --ca-cert-no-hostname-check --hours 1
"""

import argparse
import csv
import getpass
import ipaddress
import json
import os
import re
import socket
import ssl
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter

try:
    import dns.resolver
    HAVE_DNSPYTHON = True
except ImportError:
    HAVE_DNSPYTHON = False


# =====================================================================
# TLS handling
# =====================================================================

class PinnedCANoHostnameAdapter(HTTPAdapter):
    """
    Verifies the server cert against a pinned CA/cert file, but skips
    hostname matching. Use when an appliance's cert has no usable SAN
    entries (common on VMware appliance default certs) -- safer than
    disabling verification entirely, since a connection is only trusted
    if the presented cert matches the pinned file.
    """
    def __init__(self, ca_cert_path, *args, **kwargs):
        self._ca_cert_path = ca_cert_path
        super().__init__(*args, **kwargs)

    def _build_context(self):
        context = ssl.create_default_context(cafile=self._ca_cert_path)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_REQUIRED
        return context

    def init_poolmanager(self, *args, **kwargs):
        kwargs["ssl_context"] = self._build_context()
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, *args, **kwargs):
        kwargs["ssl_context"] = self._build_context()
        return super().proxy_manager_for(*args, **kwargs)


def build_requests_session(ca_cert_path, skip_hostname_check):
    session = requests.Session()
    if ca_cert_path and skip_hostname_check:
        session.mount("https://", PinnedCANoHostnameAdapter(ca_cert_path))
    return session


# =====================================================================
# DNS resolution (optional --vm support)
# =====================================================================

def resolve_vm_to_ip(vm, domain_suffix=None, dns_server=None):
    candidates = [vm]
    if domain_suffix and "." not in vm:
        candidates.append(f"{vm}.{domain_suffix.lstrip('.')}")

    last_err = None
    if dns_server:
        if not HAVE_DNSPYTHON:
            raise RuntimeError(
                "--dns-server requires the 'dnspython' package. Install it with: pip install dnspython"
            )
        resolver = dns.resolver.Resolver(configure=False)
        resolver.nameservers = [dns_server]
        for candidate in candidates:
            try:
                answer = resolver.resolve(candidate, "A")
                ip = answer[0].to_text()
                print(f"Resolved '{candidate}' -> {ip} (via DNS server {dns_server})", file=sys.stderr)
                return ip
            except Exception as e:
                last_err = e
    else:
        for candidate in candidates:
            try:
                ip = socket.gethostbyname(candidate)
                print(f"Resolved '{candidate}' -> {ip} (via system resolver)", file=sys.stderr)
                return ip
            except socket.gaierror as e:
                last_err = e

    raise RuntimeError(f"Could not resolve '{vm}' (tried: {', '.join(candidates)}): {last_err}")


def resolve_ip_or_name(value, domain_suffix=None, dns_server=None):
    """Accept either a literal IP address (returned as-is) or a VM shortname/FQDN (resolved via DNS)."""
    try:
        ipaddress.ip_address(value)
        return value
    except ValueError:
        return resolve_vm_to_ip(value, domain_suffix, dns_server)


def ip_matches_field(field_value, target_ip):
    """
    True if target_ip matches the IP portion of an extracted 'ip/port' style field
    value (e.g. "192.168.1.10/62180"), or matches the whole value if there's no port.
    """
    if not field_value:
        return False
    ip_part = str(field_value).split("/")[0]
    return ip_part == target_ip


def apply_post_filters(events, post_filters):
    """
    Client-side, exact filtering on already-extracted/normalized event fields
    (unlike --filter, which relies on the server's CONTAINS/EQ, which we've
    observed can be unreliable -- e.g. tokenized/word-based matching letting
    unrelated events through on a multi-word value). post_filters: list of
    (field, operator, value) tuples, ANDed, same syntax as --filter.
    """
    def matches(evt, field, op, value):
        actual = evt.get(field)
        if actual is None:
            return False
        actual = str(actual)
        return actual == value if op == "EQ" else value in actual

    for field, op, value in post_filters:
        events = [e for e in events if matches(e, field, op, value)]
    return events


# =====================================================================
# Filter / extract argument parsing
# =====================================================================

def parse_filter_arg(raw):
    """'field=value' -> CONTAINS. 'field==value' -> EQ. Returns (field, operator, value)."""
    if "==" in raw:
        field, value = raw.split("==", 1)
        operator = "EQ"
    elif "=" in raw:
        field, value = raw.split("=", 1)
        operator = "CONTAINS"
    else:
        raise ValueError(f"Invalid --filter '{raw}': expected 'field=value' or 'field==value'")
    field, value = field.strip(), value.strip()
    if not field or not value:
        raise ValueError(f"Invalid --filter '{raw}': field and value must both be non-empty")
    return field, operator, value


def parse_extract_arg(raw):
    """'fieldname=regex' -> (fieldname, compiled_regex)."""
    if "=" not in raw:
        raise ValueError(f"Invalid --extract '{raw}': expected 'fieldname=regex'")
    name, pattern = raw.split("=", 1)
    name, pattern = name.strip(), pattern.strip()
    if not name or not pattern:
        raise ValueError(f"Invalid --extract '{raw}': fieldname and regex must both be non-empty")
    try:
        compiled = re.compile(pattern)
    except re.error as e:
        raise ValueError(f"Invalid regex in --extract '{raw}': {e}")
    return name, compiled


# =====================================================================
# Presets: built-in filter/extraction/column sets for common log formats.
# --preset applies these automatically; any --filter/--extract/--columns
# you also pass are added on top (and any --extract with a name matching
# a preset's own extraction overrides it, since yours is applied last).
# =====================================================================

PRESETS = {
    "nsx-dfw": {
        "description": "NSX Distributed Firewall packet logs (dfwpktlogs), e.g.: "
                        "'<ts> <host> dfwpktlogs: <ruleid> INET match <ACTION> <ctx> <DIR> <len> <proto> <src>-><dst> <flags>'",
        "filter_text": "dfwpktlogs",
        "extractions": [
            ("hostname", r"Z (\S+) "),
            ("ruleid", r"dfwpktlogs:\s*(\d+)"),
            ("action", r"match (\S+)"),
            ("direction", r"\b(IN|OUT|IN-OUT)\b"),
            ("protocol", r"\b(TCP|UDP|ICMP)\b"),
            ("source", r"(\d+\.\d+\.\d+\.\d+/\d+)->"),
            ("destination", r"->(\d+\.\d+\.\d+\.\d+/\d+)"),
        ],
        "default_columns": ["timestamp", "hostname", "action", "ruleid", "protocol", "source", "destination"],
    },
    "nsx-dfw-hfc": {
        "description": "NSX DFW packet logs, FIREWALL-PKTLOG tag variant, e.g.: "
                        "'<ts> <host> FIREWALL-PKTLOG[<pid>]: <flowid> INET match <ACTION> <ruleid> <DIR> <len> <proto> <src>/<sport>-><dst>/<dport>'",
        "filter_text": "FIREWALL-PKTLOG",
        "extractions": [
            ("esx_host_source", r"Z (\S+) "),
            ("text", r"(FIREWALL-PKTLOG\[\d+\]: \S+ INET match \S+)"),
            ("firewall_action", r"match (\S+)"),
            ("firewall_rule", r"match \S+ (\d+) (?:IN|OUT|IN-OUT)"),
            ("vm_source", r"(\d+\.\d+\.\d+\.\d+)/\d+->"),
            ("vm_source_port", r"\d+\.\d+\.\d+\.\d+/(\d+)->"),
            ("vm_destination", r"->(\d+\.\d+\.\d+\.\d+)/\d+"),
            ("vm_destination_port", r"->\d+\.\d+\.\d+\.\d+/(\d+)"),
        ],
        "default_columns": ["timestamp", "vm_source", "vm_source_port", "firewall_action", "firewall_rule",
                             "vm_destination", "vm_destination_port"],
    },
}


def parse_time_arg(value):
    if value is None:
        return None
    value = value.strip()
    if value.isdigit():
        num = int(value)
        return num if len(value) >= 13 else num * 1000
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


# =====================================================================
# Query construction
# =====================================================================

def build_constraint_path(start_ms, end_ms, filters):
    """
    Build the /field/OPERATOR%20value/... constraint path. filters is a
    list of (field, operator, value) tuples, ANDed together, plus the
    mandatory time-range constraint. safe=':' preserves namespaced
    content-pack field names (e.g. 'com.vmware.nsxt:vmw_nsxt_firewall_src').
    """
    parts = [f"timestamp/GT%20{start_ms}/timestamp/LT%20{end_ms}"]
    for field, op, value in filters:
        parts.append(f"{quote(field, safe=':')}/{op}%20{quote(value, safe='')}")
    return "/".join(parts)


# =====================================================================
# API calls
# =====================================================================

def authenticate(base_url, username, password, provider, verify, session):
    resp = session.post(
        f"{base_url}/api/v2/sessions",
        json={"username": username, "password": password, "provider": provider},
        verify=verify, timeout=30,
    )
    resp.raise_for_status()
    session_id = resp.json().get("sessionId")
    if not session_id:
        raise RuntimeError("Authentication succeeded but no sessionId returned")
    return session_id


def fetch_events(base_url, session_id, constraint_path, limit, verify, session,
                  content_packs=None, api_version="v1"):
    url = f"{base_url}/api/{api_version}/events/{constraint_path}?limit={limit}"
    for cp in (content_packs or []):
        url += f"&content-pack-fields={quote(cp, safe='')}"

    resp = session.get(url, headers={"Authorization": f"Bearer {session_id}"}, verify=verify, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    events = data.get("events", [])
    print(f"Fetched {len(events)} events (complete={data.get('complete', 'unknown')})", file=sys.stderr)
    return events


# =====================================================================
# Post-processing
# =====================================================================

def apply_extractions(events, extractions):
    for evt in events:
        text = evt.get("text", "") or ""
        for name, pattern in extractions:
            m = pattern.search(text)
            if m:
                evt[name] = m.group(1) if m.groups() else m.group(0)
    return events


def format_timestamp(epoch_ms):
    """Convert epoch milliseconds to ISO 8601 with millisecond precision, e.g. 2026-09-23T14:10:20.144Z."""
    if epoch_ms is None:
        return None
    try:
        dt = datetime.fromtimestamp(int(epoch_ms) / 1000, tz=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(epoch_ms) % 1000:03d}Z"
    except (ValueError, TypeError, OverflowError):
        return epoch_ms  # fall back to the raw value if it's ever not a normal epoch-ms number


def normalize_event(evt):
    """
    Flatten one event into a flat dict for output. Handles both API response
    shapes seen in practice: flat top-level keys, and/or a nested
    "fields": [{"name":.., "content":..}] list. Nested-list entries are merged
    FIRST, flat top-level keys SECOND, so flat keys win on any name collision --
    this matters because client-side --extract writes land as flat top-level
    keys on the event dict, and must not be clobbered by a stale/duplicate
    nested "fields" entry for the same name that some appliances also return.
    """
    result = {"timestamp": format_timestamp(evt.get("timestamp")), "text": evt.get("text")}
    for f in evt.get("fields", []):
        result[f.get("name")] = f.get("content")
    skip = {"timestamp", "text", "fields"}
    for k, v in evt.items():
        if k not in skip:
            result[k] = v
    return result


def print_boxed_header(header_row):
    """
    Print a bold, box-bordered header line to the terminal (ANSI escapes).
    Terminal-only decoration -- never used when writing to a file, since a
    real CSV file must stay plain, valid comma-separated text.
    """
    text = ",".join(header_row)
    width = len(text) + 2
    BOLD, RESET = "\033[1m", "\033[0m"
    print("┌" + "─" * width + "┐")
    print(f"│ {BOLD}{text}{RESET} │")
    print("└" + "─" * width + "┘")


def write_output(events, fmt, out_path, columns=None, blank_lines=False, uppercase_headers=False):
    normalized = [normalize_event(e) for e in events]
    out = open(out_path, "w", newline="" if fmt == "csv" else None) if out_path else sys.stdout
    to_terminal = out is sys.stdout

    try:
        if fmt == "json":
            if blank_lines:
                # Manually join array elements with a blank line between them.
                # Still fully valid JSON -- extra whitespace between elements
                # is insignificant, so this parses identically to compact output.
                parts = [json.dumps(row, indent=2) for row in normalized]
                out.write("[\n" + ",\n\n".join(parts) + "\n]")
            else:
                json.dump(normalized, out, indent=2)
            if out is sys.stdout:
                print()
        else:
            writer = csv.writer(out)
            if columns:
                header = [c.upper() for c in columns] if uppercase_headers else columns
                if to_terminal:
                    print_boxed_header(header)
                else:
                    writer.writerow(header)
                for row in normalized:
                    writer.writerow([row.get(k, "") for k in columns])
                    if blank_lines:
                        writer.writerow([])
            else:
                keys = sorted({k for row in normalized for k in row.keys()})
                ordered = [k for k in ("timestamp", "text") if k in keys] + \
                          [k for k in keys if k not in ("timestamp", "text")]
                header = [k.upper() for k in ordered] if uppercase_headers else ordered
                if to_terminal:
                    print_boxed_header(header)
                else:
                    writer.writerow(header)
                for row in normalized:
                    writer.writerow([row.get(k, "") for k in ordered])
                    if blank_lines:
                        writer.writerow([])
    finally:
        if out is not sys.stdout:
            out.close()
            print(f"Wrote {len(normalized)} events to {out_path}", file=sys.stderr)


# =====================================================================
# Main
# =====================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Extract vRLI 8.18 log events filtered by whatever fields you specify.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Connection / auth
    parser.add_argument("--host", default=os.environ.get("VRLI_HOST"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("VRLI_PORT", 9543)))
    parser.add_argument("--username", default=os.environ.get("VRLI_USERNAME"))
    parser.add_argument("--password", default=os.environ.get("VRLI_PASSWORD"))
    parser.add_argument("--provider", default=os.environ.get("VRLI_PROVIDER", "Local"),
                         help="Local (default), ActiveDirectory, or vIDM. Case-sensitive.")
    parser.add_argument("--api-version", default="v1", choices=["v1", "v2"])

    # TLS
    parser.add_argument("--insecure", action="store_true",
                         help="Skip TLS verification entirely. Prefer --ca-cert when possible.")
    parser.add_argument("--ca-cert", default=os.environ.get("VRLI_CA_CERT"),
                         help="CA/cert PEM file to verify against, instead of --insecure.")
    parser.add_argument("--ca-cert-no-hostname-check", action="store_true",
                         help="With --ca-cert: verify the pinned cert, but skip hostname matching. "
                              "Use when the appliance's cert has no usable SAN entries.")

    # Filtering
    parser.add_argument("--filter", dest="filters", action="append", default=[],
                         help="field=value (CONTAINS) or field==value (EQ). Repeatable, ANDed. "
                              "Use 'namespace:fieldname' for content-pack extracted fields. NOTE: the "
                              "server's CONTAINS appears to do word/token matching rather than exact "
                              "substring matching -- a multi-word value like 'match DROP' may match any "
                              "event containing either word, not the exact phrase. For precise matching "
                              "on a field's value (especially an --extract'd one), use --post-filter instead.")
    parser.add_argument("--post-filter", dest="post_filters", action="append", default=[],
                         help="field=value (substring) or field==value (exact). Same syntax as --filter, "
                              "but applied client-side, after fetching and after --extract, checking the "
                              "real Python string value with no server-side tokenization quirks. Repeatable, "
                              "ANDed. Use this for precise matching, e.g. --post-filter action==DROP.")
    parser.add_argument("--content-pack", dest="content_packs", action="append", default=[],
                         help="Content pack namespace to include fields from (e.g. 'com.vmware.nsxt'). "
                              "Required for namespaced --filter fields to match/appear at all. Repeatable.")
    parser.add_argument("--extract", dest="extractions", action="append", default=[],
                         help="fieldname=regex: pull a value out of each event's raw text client-side, "
                              "for fields the API always returns null for one capture group, or none for "
                              "the whole match. Repeatable.")
    parser.add_argument("--preset", choices=list(PRESETS.keys()), default=None,
                         help="Apply a built-in filter/extraction/column set for a known log format. "
                              "Available: " + "; ".join(f"{k} ({v['description']})" for k, v in PRESETS.items()) +
                              ". Any --filter/--extract/--columns you also pass are added on top; a --extract "
                              "with the same field name as the preset overrides it.")

    # Optional VM name -> IP convenience
    parser.add_argument("--vm", default=None, help="VM shortname or FQDN to resolve to an IP via DNS.")
    parser.add_argument("--domain", default=os.environ.get("VRLI_DNS_DOMAIN"),
                         help="Domain suffix to qualify a --vm/--src/--dst shortname with, if it doesn't "
                              "resolve as-is.")
    parser.add_argument("--dns-server", default=os.environ.get("VRLI_DNS_SERVER"),
                         help="DNS server to query directly for --vm/--src/--dst resolution "
                              "(requires dnspython).")
    parser.add_argument("--ip-field", default="text",
                         help="Field to search the resolved --vm IP in server-side (default: 'text', "
                              "full-text search). This is only a broad pre-filter to reduce what's fetched.")
    parser.add_argument("--vm-confirm-fields", default=None,
                         help="Comma-separated extracted field names to precisely confirm --vm's resolved IP "
                              "against, client-side, after extraction (OR logic -- keeps an event if the IP "
                              "matches ANY listed field). Use this with a preset that extracts separate "
                              "source/destination fields, e.g. --vm-confirm-fields vm_source,vm_destination "
                              "for --preset nsx-dfw-hfc. Without this, --vm relies solely on the server's "
                              "CONTAINS filter, which has been observed to false-positive (e.g. apparent "
                              "tokenization on IP octets) -- strongly recommended whenever available.")
    parser.add_argument("--src", default=None,
                         help="VM shortname, FQDN, or literal IP -- keep only events whose extracted "
                              "source field (see --src-field) matches it. Requires a preset or --extract "
                              "that produces that field. Applied after fetching/extraction, as a precise "
                              "client-side filter; a broad server-side pre-filter on the IP is also added "
                              "automatically to reduce what's fetched.")
    parser.add_argument("--src-field", default="source",
                         help="Extracted field name --src matches against (default: 'source'; use "
                              "'vm_source' for --preset nsx-dfw-hfc).")
    parser.add_argument("--dst", default=None,
                         help="Same as --src, but matches the destination field (see --dst-field).")
    parser.add_argument("--dst-field", default="destination",
                         help="Extracted field name --dst matches against (default: 'destination'; use "
                              "'vm_destination' for --preset nsx-dfw-hfc).")

    # Time range
    parser.add_argument("--hours", type=float, default=None)
    parser.add_argument("--days", type=float, default=None)
    parser.add_argument("--minutes", type=float, default=None)
    parser.add_argument("--start", default=None, help="ISO8601 or epoch ms/s")
    parser.add_argument("--end", default=None, help="ISO8601 or epoch ms/s (default: now)")

    # Output
    parser.add_argument("--limit", type=int, default=2000)
    parser.add_argument("--format", choices=["json", "csv"], default="json")
    parser.add_argument("--columns", default=None, help="Comma-separated output field order (CSV only).")
    parser.add_argument("--no-blank-lines", dest="blank_lines", action="store_false", default=True,
                         help="Don't insert a blank line between each event (blank lines are on by default).")
    parser.add_argument("--no-uppercase-headers", dest="uppercase_headers", action="store_false", default=True,
                         help="Print CSV column headers as-given instead of ALL CAPS "
                              "(uppercase headers are on by default).")
    parser.add_argument("-o", "--output", default=None)

    args = parser.parse_args()

    # --- validation ---
    if not args.host:
        parser.error("--host is required (or set VRLI_HOST)")
    if not args.username:
        parser.error("--username is required (or set VRLI_USERNAME)")
    if args.insecure and args.ca_cert:
        parser.error("--insecure and --ca-cert are mutually exclusive")
    if args.ca_cert_no_hostname_check and not args.ca_cert:
        parser.error("--ca-cert-no-hostname-check requires --ca-cert")
    time_opts = [args.hours is not None, args.days is not None, args.minutes is not None]
    if sum(time_opts) > 1:
        parser.error("--hours, --days, and --minutes are mutually exclusive")
    if any(time_opts) and (args.start or args.end):
        parser.error("--hours/--days/--minutes and --start/--end are mutually exclusive")

    try:
        filters = [parse_filter_arg(f) for f in args.filters]
    except ValueError as e:
        parser.error(str(e))
    try:
        extractions = [parse_extract_arg(e) for e in args.extractions]
    except ValueError as e:
        parser.error(str(e))
    try:
        post_filters = [parse_filter_arg(f) for f in args.post_filters]
    except ValueError as e:
        parser.error(str(e))

    # --- apply preset: preset filter/extractions go first, user's own are added
    #     on top and override any preset extraction with the same field name ---
    preset_columns = None
    if args.preset:
        preset = PRESETS[args.preset]
        filters = [("text", "CONTAINS", preset["filter_text"])] + filters
        extractions = [(name, re.compile(pat)) for name, pat in preset["extractions"]] + extractions
        preset_columns = preset["default_columns"]

    # --- optional VM -> IP, folded into filters ---
    vm_ip = None
    if args.vm:
        try:
            vm_ip = resolve_vm_to_ip(args.vm, args.domain, args.dns_server)
        except RuntimeError as e:
            print(f"DNS resolution failed: {e}", file=sys.stderr)
            sys.exit(1)
        filters.append((args.ip_field, "CONTAINS", vm_ip))

    vm_confirm_fields = [f.strip() for f in args.vm_confirm_fields.split(",")] if args.vm_confirm_fields else None

    # --- optional --src/--dst: resolve to IPs, add a broad server-side text
    #     pre-filter to narrow what's fetched, then do a precise client-side
    #     post-filter after extraction runs (since 'source'/'destination' are
    #     extracted fields, not real API fields the server can filter on) ---
    src_ip = dst_ip = None
    if args.src:
        try:
            src_ip = resolve_ip_or_name(args.src, args.domain, args.dns_server)
        except RuntimeError as e:
            print(f"--src resolution failed: {e}", file=sys.stderr)
            sys.exit(1)
        filters.append(("text", "CONTAINS", src_ip))
    if args.dst:
        try:
            dst_ip = resolve_ip_or_name(args.dst, args.domain, args.dns_server)
        except RuntimeError as e:
            print(f"--dst resolution failed: {e}", file=sys.stderr)
            sys.exit(1)
        filters.append(("text", "CONTAINS", dst_ip))

    # --- time window ---
    now = datetime.now(timezone.utc)
    if args.start or args.end:
        start_ms = parse_time_arg(args.start) if args.start else parse_time_arg((now - timedelta(hours=24)).isoformat())
        end_ms = parse_time_arg(args.end) if args.end else int(now.timestamp() * 1000)
    elif args.days is not None:
        start_ms = int((now - timedelta(days=args.days)).timestamp() * 1000)
        end_ms = int(now.timestamp() * 1000)
    elif args.minutes is not None:
        start_ms = int((now - timedelta(minutes=args.minutes)).timestamp() * 1000)
        end_ms = int(now.timestamp() * 1000)
    else:
        hours = args.hours if args.hours is not None else 24.0
        start_ms = int((now - timedelta(hours=hours)).timestamp() * 1000)
        end_ms = int(now.timestamp() * 1000)

    # --- TLS / session ---
    verify = args.ca_cert if args.ca_cert else (not args.insecure)
    session = build_requests_session(args.ca_cert, args.ca_cert_no_hostname_check)

    password = args.password or getpass.getpass(f"Password for {args.username}: ")
    base_url = f"https://{args.host}:{args.port}"

    try:
        session_id = authenticate(base_url, args.username, password, args.provider, verify, session)
    except requests.exceptions.RequestException as e:
        print(f"Authentication failed: {e}", file=sys.stderr)
        sys.exit(1)

    constraint_path = build_constraint_path(start_ms, end_ms, filters)

    try:
        events = fetch_events(base_url, session_id, constraint_path, args.limit, verify, session,
                               args.content_packs, args.api_version)
    except requests.exceptions.RequestException as e:
        print(f"Query failed: {e}", file=sys.stderr)
        sys.exit(1)

    if extractions:
        events = apply_extractions(events, extractions)

    if vm_ip and vm_confirm_fields:
        before = len(events)
        events = [e for e in events if any(ip_matches_field(e.get(f), vm_ip) for f in vm_confirm_fields)]
        print(f"--vm-confirm-fields ({','.join(vm_confirm_fields)}): {before} -> {len(events)} events",
              file=sys.stderr)

    if src_ip:
        before = len(events)
        events = [e for e in events if ip_matches_field(e.get(args.src_field), src_ip)]
        print(f"--src filter ({args.src_field}): {before} -> {len(events)} events", file=sys.stderr)
    if dst_ip:
        before = len(events)
        events = [e for e in events if ip_matches_field(e.get(args.dst_field), dst_ip)]
        print(f"--dst filter ({args.dst_field}): {before} -> {len(events)} events", file=sys.stderr)

    if post_filters:
        before = len(events)
        events = apply_post_filters(events, post_filters)
        print(f"--post-filter: {before} -> {len(events)} events", file=sys.stderr)

    columns = [c.strip() for c in args.columns.split(",")] if args.columns else preset_columns
    write_output(events, args.format, args.output, columns, args.blank_lines, args.uppercase_headers)


if __name__ == "__main__":
    main()
