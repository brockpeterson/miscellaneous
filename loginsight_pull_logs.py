#!/usr/bin/env python3
"""
loginsight_pull_logs.py

Pull log events for a given IP address from VMware Aria Operations for Logs
(formerly vRealize Log Insight) via its REST API.

Auth:
    Provide credentials via CLI args or environment variables:
        LOGINSIGHT_HOST      e.g. loginsight.example.com
        LOGINSIGHT_USERNAME
        LOGINSIGHT_PASSWORD
        LOGINSIGHT_PROVIDER  (default: Local)

Examples:
    # Last 24h, IP anywhere in the message text, save to file
    python3 loginsight_pull_logs.py --ip 10.1.2.3 --hours 24 -o out.json

    # Explicit time range (ISO 8601 or epoch ms), CSV output
    python3 loginsight_pull_logs.py --ip 10.1.2.3 \
        --start 2026-08-20T00:00:00Z --end 2026-08-27T00:00:00Z \
        --format csv -o out.csv

    # Search a specific field instead of full text (e.g. a parsed "source" field)
    python3 loginsight_pull_logs.py --ip 10.1.2.3 --field source_ip

    # Add extra field filters, e.g. narrow to a specific event_type (CONTAINS match)
    python3 loginsight_pull_logs.py --ip 10.1.2.3 --filter event_type=VOB

    # Exact match instead of CONTAINS, use ==
    python3 loginsight_pull_logs.py --ip 10.1.2.3 --filter event_type==esx.problem.vmfs.pdl

    # Multiple filters (ANDed together)
    python3 loginsight_pull_logs.py --ip 10.1.2.3 \
        --filter event_type==esx.problem.vmfs.pdl --filter hostname=esxi01

    # No --ip/--vm at all -- filter purely by field(s), e.g. all PDL events, any host
    python3 loginsight_pull_logs.py --filter event_type==esx.problem.vmfs.pdl --days 7

    # Pull all matching events, then reverse-resolve any IPs found in them to hostnames
    python3 loginsight_pull_logs.py --filter event_type==esx.problem.vmfs.pdl --days 7 \
        --resolve-ips --dns-server 10.0.0.53

    # Resolve a VM shortname or FQDN to an IP via DNS, then query LogInsight
    python3 loginsight_pull_logs.py --vm myvm01 --domain corp.example.com --hours 12

    # FQDN already fully qualified, no --domain needed
    python3 loginsight_pull_logs.py --vm myvm01.corp.example.com

    # Resolve via a specific DNS server instead of the system resolver
    python3 loginsight_pull_logs.py --vm myvm01 --domain corp.example.com \
        --dns-server 10.0.0.53

    # Look back 7 days instead of hours
    python3 loginsight_pull_logs.py --ip 10.1.2.3 --days 7

    # Look back 30 minutes
    python3 loginsight_pull_logs.py --ip 10.1.2.3 --minutes 30
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
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter

try:
    import dns.resolver  # from dnspython, only needed if --dns-server is used
    import dns.reversename
    HAVE_DNSPYTHON = True
except ImportError:
    HAVE_DNSPYTHON = False


IP_REGEX = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

DEFAULT_PAGE_LIMIT = 2000  # events per page (API allows up to a max, commonly 20000, but keep sane)


def parse_time_arg(value: str) -> int:
    """Accept ISO8601 (e.g. 2026-08-27T00:00:00Z) or epoch milliseconds; return epoch ms."""
    if value is None:
        return None
    value = value.strip()
    if value.isdigit():
        # assume already epoch ms if long, epoch s if short
        num = int(value)
        return num if len(value) >= 13 else num * 1000
    # ISO 8601
    v = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def resolve_vm_to_ip(vm: str, domain_suffix: str = None, dns_server: str = None) -> str:
    """
    Resolve a VM shortname or FQDN to an IPv4 address via DNS.

    If dns_server is given, query that server directly (requires dnspython).
    Otherwise, use the system resolver (socket.gethostbyname).

    Resolution order:
      1. Try the name exactly as given (handles FQDNs, or shortnames if the
         resolver's search domain already covers them).
      2. If that fails and a domain suffix was provided, try name.suffix.

    Raises RuntimeError if resolution fails entirely.
    """
    candidates = [vm]
    if domain_suffix and "." not in vm:
        candidates.append(f"{vm}.{domain_suffix.lstrip('.')}")

    last_err = None

    if dns_server:
        if not HAVE_DNSPYTHON:
            raise RuntimeError(
                "--dns-server requires the 'dnspython' package. Install it with: "
                "pip install dnspython"
            )
        resolver = dns.resolver.Resolver(configure=False)
        resolver.nameservers = [dns_server]
        for candidate in candidates:
            try:
                answer = resolver.resolve(candidate, "A")
                ip = answer[0].to_text()
                print(f"Resolved '{candidate}' -> {ip} (via DNS server {dns_server})", file=sys.stderr)
                return ip
            except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer,
                    dns.resolver.NoNameservers, dns.exception.Timeout) as e:
                last_err = e
                continue
    else:
        for candidate in candidates:
            try:
                ip = socket.gethostbyname(candidate)
                print(f"Resolved '{candidate}' -> {ip} (via system resolver)", file=sys.stderr)
                return ip
            except socket.gaierror as e:
                last_err = e
                continue

    raise RuntimeError(
        f"Could not resolve '{vm}' to an IP (tried: {', '.join(candidates)}): {last_err}"
    )


def extract_ips(text: str) -> list:
    """Find all IPv4-looking substrings in text and return the valid, unique ones (order preserved)."""
    if not text:
        return []
    seen = []
    for candidate in IP_REGEX.findall(text):
        try:
            ipaddress.IPv4Address(candidate)
        except ValueError:
            continue
        if candidate not in seen:
            seen.append(candidate)
    return seen


def reverse_resolve_ip(ip: str, dns_server: str, cache: dict, timeout: float = 5.0) -> str:
    """
    Reverse-resolve an IP to a hostname (PTR lookup), with caching.
    Returns the hostname on success, or None if resolution fails or times out.
    """
    if ip in cache:
        return cache[ip]

    hostname = None
    if dns_server:
        if not HAVE_DNSPYTHON:
            raise RuntimeError(
                "--dns-server requires the 'dnspython' package. Install it with: "
                "pip install dnspython"
            )
        resolver = dns.resolver.Resolver(configure=False)
        resolver.nameservers = [dns_server]
        resolver.timeout = timeout
        resolver.lifetime = timeout
        try:
            rev_name = dns.reversename.from_address(ip)
            answer = resolver.resolve(rev_name, "PTR")
            hostname = str(answer[0]).rstrip(".")
        except Exception:
            hostname = None
    else:
        # socket has no per-call timeout param; temporarily set the global default
        old_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(timeout)
        try:
            hostname = socket.gethostbyaddr(ip)[0]
        except (socket.herror, socket.gaierror, socket.timeout):
            hostname = None
        finally:
            socket.setdefaulttimeout(old_timeout)

    cache[ip] = hostname
    return hostname


def enrich_events_with_hostnames(events: list, dns_server: str, timeout: float = 5.0) -> list:
    """
    Scan each event's text/fields for IPs, reverse-resolve them, and attach a
    'resolved_hosts' field summarizing ip -> hostname mappings found in that event.
    Unresolvable IPs are still listed (marked as unresolved) rather than silently dropped,
    so failures are visible instead of looking like nothing was attempted.
    """
    cache = {}
    for evt in events:
        haystack_parts = [evt.get("text", "") or ""]
        for f in evt.get("fields", []):
            haystack_parts.append(str(f.get("content", "")))
        haystack = " ".join(haystack_parts)

        ips = extract_ips(haystack)
        if not ips:
            continue

        mappings = []
        for ip in ips:
            hostname = reverse_resolve_ip(ip, dns_server, cache, timeout)
            if hostname:
                mappings.append(f"{ip} -> {hostname}")
            else:
                mappings.append(f"{ip} -> (no PTR record)")
        evt["resolved_hosts"] = "; ".join(mappings)

    resolved_count = sum(1 for v in cache.values() if v)
    print(f"Resolved {resolved_count}/{len(cache)} unique IPs to hostnames", file=sys.stderr)

    return events


class _PinnedCANoHostnameAdapter(HTTPAdapter):
    """
    A requests HTTPAdapter that verifies the server cert against a specific
    pinned CA/cert file, but skips hostname matching.

    Use this when an appliance's cert has no usable SAN entries (common on
    VMware appliance default certs), so standard hostname verification always
    fails even though the cert itself is legitimate and known. This is safer
    than disabling verification entirely (--insecure): a connection is only
    trusted if the presented cert chains to/matches the pinned file, an
    attacker without that exact cert/key still can't intercept the connection.
    """
    def __init__(self, ca_cert_path, *args, **kwargs):
        self._ca_cert_path = ca_cert_path
        super().__init__(*args, **kwargs)

    def init_poolmanager(self, *args, **kwargs):
        context = ssl.create_default_context(cafile=self._ca_cert_path)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_REQUIRED
        kwargs["ssl_context"] = context
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, *args, **kwargs):
        context = ssl.create_default_context(cafile=self._ca_cert_path)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_REQUIRED
        kwargs["ssl_context"] = context
        return super().proxy_manager_for(*args, **kwargs)


def build_requests_session(ca_cert_path: str, skip_hostname_check: bool) -> requests.Session:
    """
    Build a requests.Session configured with the right TLS verification behavior.
    If skip_hostname_check is True, mounts the pinned-CA-without-hostname-check
    adapter on https:// so all requests through this session use it automatically.
    """
    session = requests.Session()
    if ca_cert_path and skip_hostname_check:
        session.mount("https://", _PinnedCANoHostnameAdapter(ca_cert_path))
    return session


def build_session(host: str, port: int, username: str, password: str, provider: str,
                   verify_ssl, session: requests.Session = None) -> tuple:
    """Authenticate and return (session_id, base_url). Uses `session` if given, else plain requests."""
    base_url = f"https://{host}:{port}"
    auth_url = f"{base_url}/api/v2/sessions"
    payload = {"username": username, "password": password, "provider": provider}
    requester = session if session is not None else requests
    # verify is ignored by our custom adapter (it pins its own ssl_context), but
    # still needs to be passed for the plain-requests path.
    resp = requester.post(auth_url, json=payload, verify=verify_ssl, timeout=30)
    resp.raise_for_status()
    session_id = resp.json().get("sessionId")
    if not session_id:
        raise RuntimeError("Authentication succeeded but no sessionId returned")
    return session_id, base_url


def parse_filter_arg(raw: str) -> tuple:
    """
    Parse a --filter argument into (field, operator, value).

    Syntax:
        field=value    -> CONTAINS match (substring)
        field==value   -> EQ match (exact)

    Field and value are used as-is (before URL-encoding, which happens later).
    """
    if "==" in raw:
        field, value = raw.split("==", 1)
        operator = "EQ"
    elif "=" in raw:
        field, value = raw.split("=", 1)
        operator = "CONTAINS"
    else:
        raise ValueError(
            f"Invalid --filter '{raw}': expected 'field=value' (CONTAINS) or 'field==value' (EQ)"
        )
    field = field.strip()
    value = value.strip()
    if not field or not value:
        raise ValueError(f"Invalid --filter '{raw}': field and value must both be non-empty")
    return field, operator, value


def build_constraint_path(ip: str, field: str, start_ms: int, end_ms: int, extra_filters: list = None) -> str:
    """
    Build the LogInsight query constraint path segment.
    Constraints are chained as /field/OPERATOR value pairs.

    ip: optional. If None, no IP constraint is added (rely on extra_filters instead).
    extra_filters: list of (field, operator, value) tuples, ANDed onto the query
    (e.g. from --filter event_type==esx.problem.vmfs.pdl).
    """
    parts = []

    if ip:
        if field:
            # Search a specific parsed field for exact/contains match on the IP
            parts.append(f"{quote(field, safe='')}/CONTAINS%20{quote(ip, safe='')}")
        else:
            # Full-text search across the raw message text
            parts.append(f"text/CONTAINS%20{quote(ip, safe='')}")

    parts.append(f"timestamp/GT%20{start_ms}/timestamp/LT%20{end_ms}")

    for f, op, v in (extra_filters or []):
        parts.append(f"{quote(f, safe='')}/{op}%20{quote(v, safe='')}")

    return "/".join(parts)


def fetch_events(base_url: str, session_id: str, constraint_path: str, limit: int, verify_ssl,
                  session: requests.Session = None) -> list:
    """Fetch events, paginating via the 'timestamp' cursor since LogInsight doesn't support offset paging well."""
    headers = {"Authorization": f"Bearer {session_id}"}
    all_events = []
    url = f"{base_url}/api/v1/events/{constraint_path}?limit={limit}"
    requester = session if session is not None else requests

    resp = requester.get(url, headers=headers, verify=verify_ssl, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    events = data.get("events", [])
    all_events.extend(events)

    print(f"Fetched {len(events)} events "
          f"(complete={data.get('complete', 'unknown')})", file=sys.stderr)

    return all_events


def normalize_event(evt: dict) -> dict:
    """
    Flatten a LogInsight event into a simple dict for CSV/JSON output.

    Handles two API response shapes:
      1. Classic nested: {"text": ..., "fields": [{"name": "hostname", "content": "..."}]}
      2. Flat top-level: {"text": ..., "hostname": "...", "vmw_host": "...", ...}
    """
    result = {
        "timestamp": evt.get("timestamp"),
        "text": evt.get("text"),
    }

    # Flat top-level fields (skip internal/structural keys)
    skip_keys = {"timestamp", "text", "fields", "resolved_hosts"}
    for k, v in evt.items():
        if k not in skip_keys:
            result[k] = v

    # Classic nested fields list, if present, takes precedence over flat keys
    for f in evt.get("fields", []):
        result[f.get("name")] = f.get("content")

    if "resolved_hosts" in evt:
        result["resolved_hosts"] = evt["resolved_hosts"]
    return result


def write_output(events: list, fmt: str, out_path: str, columns: list = None):
    normalized = [normalize_event(e) for e in events]

    if out_path is None:
        if fmt == "json":
            print(json.dumps(normalized, indent=2))
        else:
            writer = csv.writer(sys.stdout)
            _write_csv(writer, normalized, columns)
        return

    if fmt == "json":
        with open(out_path, "w") as f:
            json.dump(normalized, f, indent=2)
    else:
        with open(out_path, "w", newline="") as f:
            writer = csv.writer(f)
            _write_csv(writer, normalized, columns)

    print(f"Wrote {len(normalized)} events to {out_path}", file=sys.stderr)


def _write_csv(writer, normalized: list, columns: list = None):
    if columns:
        # Use exactly the requested columns, in the requested order.
        # Any column not present in a given row is left blank.
        writer.writerow(columns)
        for row in normalized:
            writer.writerow([row.get(k, "") for k in columns])
        return

    if not normalized:
        writer.writerow(["timestamp", "text"])
        return
    keys = sorted({k for row in normalized for k in row.keys()})
    # keep timestamp/text first if present
    ordered = [k for k in ("timestamp", "text") if k in keys] + [k for k in keys if k not in ("timestamp", "text")]
    writer.writerow(ordered)
    for row in normalized:
        writer.writerow([row.get(k, "") for k in ordered])


def main():
    parser = argparse.ArgumentParser(description="Pull LogInsight logs for a given IP address.")
    parser.add_argument("--host", default=os.environ.get("LOGINSIGHT_HOST"),
                         help="LogInsight hostname/IP (env: LOGINSIGHT_HOST)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("LOGINSIGHT_PORT", 9543)),
                         help="LogInsight API port (default: 9543, env: LOGINSIGHT_PORT)")
    parser.add_argument("--username", default=os.environ.get("LOGINSIGHT_USERNAME"),
                         help="Username (env: LOGINSIGHT_USERNAME)")
    parser.add_argument("--password", default=os.environ.get("LOGINSIGHT_PASSWORD"),
                         help="Password (env: LOGINSIGHT_PASSWORD). Prompts if omitted.")
    parser.add_argument("--provider", default=os.environ.get("LOGINSIGHT_PROVIDER", "Local"),
                         help="Auth provider, e.g. Local, ActiveDirectory (default: Local)")
    parser.add_argument("--ip", default=None, help="IP address to search for (optional)")
    parser.add_argument("--vm", default=None,
                         help="VM shortname or FQDN to resolve to an IP via DNS (optional, alternative to --ip)")
    parser.add_argument("--domain", default=os.environ.get("LOGINSIGHT_DNS_DOMAIN"),
                         help="Domain suffix to append when --vm is a shortname that doesn't resolve as-is "
                              "(env: LOGINSIGHT_DNS_DOMAIN)")
    parser.add_argument("--dns-server", default=os.environ.get("LOGINSIGHT_DNS_SERVER"),
                         help="DNS server IP to query directly instead of the system resolver, "
                              "used for --vm resolution and/or --resolve-ips "
                              "(requires 'dnspython'; env: LOGINSIGHT_DNS_SERVER)")
    parser.add_argument("--field", default=None,
                         help="Specific field name to match the IP against (default: full-text search)")
    parser.add_argument("--filter", dest="filters", action="append", default=[],
                         help="Additional field filter, ANDed onto the query. Format: field=value "
                              "(CONTAINS) or field==value (EQ). Repeatable, e.g. "
                              "--filter event_type==esx.problem.vmfs.pdl --filter hostname=esxi01")
    parser.add_argument("--hours", type=float, default=None,
                         help="Look back this many hours from now (mutually exclusive with --minutes/--days/--start/--end)")
    parser.add_argument("--days", type=float, default=None,
                         help="Look back this many days from now (mutually exclusive with --minutes/--hours/--start/--end)")
    parser.add_argument("--minutes", type=float, default=None,
                         help="Look back this many minutes from now (mutually exclusive with --hours/--days/--start/--end)")
    parser.add_argument("--start", default=None, help="Start time, ISO8601 or epoch ms/s")
    parser.add_argument("--end", default=None, help="End time, ISO8601 or epoch ms/s (default: now)")
    parser.add_argument("--limit", type=int, default=DEFAULT_PAGE_LIMIT, help="Max events to fetch")
    parser.add_argument("--format", choices=["json", "csv"], default="json", help="Output format")
    parser.add_argument("--columns", default=None,
                         help="Comma-separated list of field names to output, in order (CSV only). "
                              "Defaults to all fields, alphabetized. Example: "
                              "--columns hostname,vmw_nsxt_firewall_action,vmw_nsxt_firewall_ruleid,"
                              "vmw_nsxt_firewall_protocol,vmw_nsxt_firewall_src,vmw_nsxt_firewall_dst,"
                              "vmw_nsxt_firewall_dst_port")
    parser.add_argument("-o", "--output", default=None, help="Output file path (default: stdout)")
    parser.add_argument("--insecure", action="store_true",
                         help="Skip TLS certificate verification entirely (self-signed certs). "
                              "Prefer --ca-cert when possible, since --insecure disables all cert checking.")
    parser.add_argument("--ca-cert", default=os.environ.get("LOGINSIGHT_CA_CERT"),
                         help="Path to a CA certificate (PEM file) to verify the appliance's TLS cert against, "
                              "instead of using --insecure (env: LOGINSIGHT_CA_CERT)")
    parser.add_argument("--ca-cert-no-hostname-check", action="store_true",
                         help="With --ca-cert: verify the presented cert matches the pinned file, but skip "
                              "hostname matching. Use this when the appliance's cert has no usable SAN entries "
                              "(common on VMware appliance default certs), which otherwise always fails "
                              "standard hostname verification. Safer than --insecure.")
    parser.add_argument("--resolve-ips", action="store_true",
                         help="After fetching events, find IPv4 addresses in each event's text/fields "
                              "and reverse-resolve them to hostnames (PTR lookup), attaching the results "
                              "as a 'resolved_hosts' field. Uses --dns-server if given, else the system resolver.")
    parser.add_argument("--resolve-timeout", type=float, default=5.0,
                         help="Per-lookup timeout in seconds for --resolve-ips (default: 5.0)")

    args = parser.parse_args()

    if not args.host:
        parser.error("--host is required (or set LOGINSIGHT_HOST)")
    if not args.username:
        parser.error("--username is required (or set LOGINSIGHT_USERNAME)")
    if args.ip and args.vm:
        parser.error("--ip and --vm are mutually exclusive; pass one or the other")
    if args.dns_server and not args.vm and not args.resolve_ips:
        parser.error("--dns-server only applies when using --vm or --resolve-ips")
    if args.insecure and args.ca_cert:
        parser.error("--insecure and --ca-cert are mutually exclusive; pass one or the other")
    if args.ca_cert_no_hostname_check and not args.ca_cert:
        parser.error("--ca-cert-no-hostname-check requires --ca-cert")
    if args.resolve_timeout is not None and args.resolve_timeout <= 0:
        parser.error("--resolve-timeout must be a positive number")
    if args.hours is not None and args.days is not None:
        parser.error("--hours and --days are mutually exclusive; pass one or the other")
    if args.minutes is not None and (args.hours is not None or args.days is not None):
        parser.error("--minutes is mutually exclusive with --hours/--days; pass only one")
    if (args.hours is not None or args.days is not None or args.minutes is not None) and (args.start or args.end):
        parser.error("--minutes/--hours/--days and --start/--end are mutually exclusive")

    try:
        extra_filters = [parse_filter_arg(f) for f in args.filters]
    except ValueError as e:
        parser.error(str(e))

    if args.vm:
        try:
            target_ip = resolve_vm_to_ip(args.vm, args.domain, args.dns_server)
        except RuntimeError as e:
            print(f"DNS resolution failed: {e}", file=sys.stderr)
            sys.exit(1)
    else:
        target_ip = args.ip  # may be None if neither --ip nor --vm was given

    password = args.password or getpass.getpass(f"Password for {args.username}: ")

    now = datetime.now(timezone.utc)
    if args.start or args.end:
        start_ms = parse_time_arg(args.start) if args.start else parse_time_arg(
            (now - timedelta(hours=24)).isoformat())
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

    if args.ca_cert:
        verify_ssl = args.ca_cert  # requests accepts a CA bundle/cert file path here
    else:
        verify_ssl = not args.insecure

    http_session = build_requests_session(args.ca_cert, args.ca_cert_no_hostname_check)

    try:
        session_id, base_url = build_session(args.host, args.port, args.username, password, args.provider,
                                              verify_ssl, http_session)
    except requests.exceptions.RequestException as e:
        print(f"Authentication failed: {e}", file=sys.stderr)
        sys.exit(1)

    constraint_path = build_constraint_path(target_ip, args.field, start_ms, end_ms, extra_filters)

    try:
        events = fetch_events(base_url, session_id, constraint_path, args.limit, verify_ssl, http_session)
    except requests.exceptions.RequestException as e:
        print(f"Query failed: {e}", file=sys.stderr)
        sys.exit(1)

    if args.resolve_ips:
        print("Reverse-resolving IPs found in events...", file=sys.stderr)
        try:
            events = enrich_events_with_hostnames(events, args.dns_server, args.resolve_timeout)
        except RuntimeError as e:
            print(f"IP resolution failed: {e}", file=sys.stderr)
            sys.exit(1)

    columns = [c.strip() for c in args.columns.split(",")] if args.columns else None
    write_output(events, args.format, args.output, columns)


if __name__ == "__main__":
    main()
