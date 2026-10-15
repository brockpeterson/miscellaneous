#!/usr/bin/env python3
"""
Pull metrics from VCF Operations (Aria/vROps Suite API) for every object
of a given type (HostSystem by default) and export them as JSON, Excel (.xlsx), or both.

No third-party packages needed - uses only the Python standard library
(Python 3.9+). Nothing to pip install.

Metrics (default):
  mem|active_average, mem|consumed_average,
  cpu|capacity_usagepct_average, cpu|coreUtilization_average

Examples:
  # Log in with username/password (prompts for password), last 30 days, Excel
  python3 vcf_operations_metrics_picker.py --server 192.168.135.113 --user admin --sample_size 30d --format xlsx

  # Use an existing token from an env var, write both JSON and Excel
  export VCFOPS_TOKEN='xxxxxxxx'
  python3 vcf_operations_metrics_picker.py --server 192.168.135.113 --format both

  # Last 1 day (288 samples), last 12 hours (144), last 2 weeks (4032)
  python3 vcf_operations_metrics_picker.py --server 192.168.135.113 --user admin --sample_size 1d
  python3 vcf_operations_metrics_picker.py --server 192.168.135.113 --user admin --sample_size 12h
  python3 vcf_operations_metrics_picker.py --server 192.168.135.113 --user admin --sample_size 2w

  # Pick your own metrics (quote them - the | character means "pipe" to the shell)
  python3 vcf_operations_metrics_picker.py --server 192.168.135.113 --user admin --stat_keys "cpu|usage_average,mem|usage_average"

  # Print to the screen instead of a file
  python3 vcf_operations_metrics_picker.py --server 192.168.135.113 --user admin --sample_size 1h --format table
  python3 vcf_operations_metrics_picker.py --server 192.168.135.113 --user admin --format csv > hosts.csv
  python3 vcf_operations_metrics_picker.py --server 192.168.135.113 --user admin --format json --output - | jq .

  # Other object types (any resourceKind the Operations API accepts)
  python3 vcf_operations_metrics_picker.py --server 192.168.135.113 --user admin --object_type VirtualMachine
  python3 vcf_operations_metrics_picker.py --server 192.168.135.113 --user admin --object_type ClusterComputeResource

  # Only hosts whose name contains "esx-prod", 8 parallel requests
  python3 vcf_operations_metrics_picker.py --server 192.168.135.113 --user admin --name_filter esx-prod --workers 8
"""
import argparse
import getpass
import json
import math
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from xml.sax.saxutils import escape

DEFAULT_STATS = [
    "mem|active_average",
    "mem|consumed_average",
    "cpu|capacity_usagepct_average",
    "cpu|coreUtilization_average",
]
EXCEL_MAX_ROWS = 1_048_575  # Excel row limit minus header row


# --------------------------------------------------------------------------- API

class OpsClient:
    def __init__(self, server, token=None, verify=False, timeout=120, retries=3):
        if not server.startswith(("http://", "https://")):
            server = "https://" + server
        self.base = server.rstrip("/") + "/suite-api/api"
        self.timeout = timeout
        self.retries = retries
        self.ctx = ssl.create_default_context()
        if not verify:
            self.ctx.check_hostname = False
            self.ctx.verify_mode = ssl.CERT_NONE
        self.headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if token:
            self.headers["Authorization"] = f"OpsToken {token}"

    def _request(self, method, path, params=None, body=None):
        url = f"{self.base}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(1, self.retries + 1):
            req = urllib.request.Request(url, data=data, headers=self.headers, method=method)
            try:
                kw = {"timeout": self.timeout}
                if url.startswith("https"):
                    kw["context"] = self.ctx
                with urllib.request.urlopen(req, **kw) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < self.retries:
                    time.sleep(2 * attempt)
                    continue
                detail = e.read().decode("utf-8", "replace")[:300]
                raise RuntimeError(f"HTTP {e.code} on {method} {path}: {detail}") from None
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if attempt == self.retries:
                    raise RuntimeError(f"Connection failed for {path}: {e}") from None
                time.sleep(2 * attempt)

    def login(self, username, password, auth_source=None):
        body = {"username": username, "password": password}
        if auth_source:
            body["authSource"] = auth_source
        tok = self._request("POST", "/auth/token/acquire", body=body)["token"]
        self.headers["Authorization"] = f"OpsToken {tok}"

    def list_resources(self, resource_kind, page_size=1000):
        hosts, page = [], 0
        while True:
            data = self._request("GET", "/resources", {
                "resourceKind": resource_kind, "page": page,
                "pageSize": page_size, "_no_links": "true",
            })
            batch = data.get("resourceList", []) or []
            for res in batch:
                hosts.append({
                    "resourceId": res["identifier"],
                    "name": res.get("resourceKey", {}).get("name", res["identifier"]),
                    "adapterKind": res.get("resourceKey", {}).get("adapterKindKey"),
                })
            total = data.get("pageInfo", {}).get("totalCount", len(hosts))
            page += 1
            if len(hosts) >= total or not batch:
                return hosts

    def describe_stats(self, adapter_kind, resource_kind):
        """Metric catalogue for an object type: {stat key: {name, unit, description}}."""
        path = (f"/adapterkinds/{urllib.parse.quote(adapter_kind, safe='')}"
                f"/resourcekinds/{urllib.parse.quote(resource_kind, safe='')}/statkeys")
        data = self._request("GET", path, {"_no_links": "true"})
        out = {}
        for a in data.get("resourceTypeAttributes", []) or []:
            if a.get("key"):
                out[a["key"]] = {"name": a.get("name") or a["key"], "unit": a.get("unit") or "",
                                 "description": a.get("description") or ""}
        return out

    def host_stats(self, resource_id, stat_keys, max_samples):
        data = self._request("GET", "/resources/stats/latest", {
            "resourceId": resource_id, "statKey": stat_keys,
            "maxSamples": max_samples, "_no_links": "true",
        })
        series = {}
        for v in data.get("values", []) or []:
            for st in v.get("stat-list", {}).get("stat", []) or []:
                series[st["statKey"]["key"]] = dict(zip(st.get("timestamps", []), st.get("data", [])))
        return series


# ------------------------------------------------------------------------ shaping

def to_rows(host, series, stat_keys):
    """Merge per-metric series into one row per timestamp."""
    all_ts = sorted({ts for s in series.values() for ts in s})
    return [
        {"host": host["name"], "resourceId": host["resourceId"], "timestamp": ts,
         **{k: series.get(k, {}).get(ts) for k in stat_keys}}
        for ts in all_ts
    ]


def iso_utc(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")



# ----------------------------------------------------------------------- outputs

def log(msg):
    """Status messages go to stderr so stdout carries only data (safe to pipe/redirect)."""
    print(msg, file=sys.stderr, flush=True)


def get_tz(tz_name):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(tz_name), tz_name
    except Exception:
        log(f"Time zone '{tz_name}' not available; using UTC.")
        return timezone.utc, "UTC"


def fmt_val(key, v):
    if v is None:
        return ""
    return f"{v:.2f}" if key.startswith("cpu|") or isinstance(v, float) and not v.is_integer() else f"{v:,.0f}"


def print_csv(results, stat_keys, tz_name, stream=None, info=None):
    import csv
    tz, tz_name = get_tz(tz_name)
    w = csv.writer(stream or sys.stdout)
    w.writerow(["Name", "Resource ID", f"Time ({tz_name})", "Time (UTC)", *[label_for(k, info) for k in stat_keys]])
    for r in results:
        for x in r["rows"]:
            local = datetime.fromtimestamp(x["timestamp"] / 1000, tz).strftime("%Y-%m-%d %H:%M:%S")
            w.writerow([x["host"], x["resourceId"], local, iso_utc(x["timestamp"]),
                        *["" if x[k] is None else x[k] for k in stat_keys]])


def print_table(results, stat_keys, tz_name, info=None):
    tz, tz_name = get_tz(tz_name)
    headers = ["Name", f"Time ({tz_name})", *[label_for(k, info) for k in stat_keys]]
    rows = [[x["host"], datetime.fromtimestamp(x["timestamp"] / 1000, tz).strftime("%Y-%m-%d %H:%M"),
             *[fmt_val(k, x[k]) for k in stat_keys]]
            for r in results for x in r["rows"]]
    widths = [max(len(h), *(len(row[i]) for row in rows)) if rows else len(h) for i, h in enumerate(headers)]
    line = lambda cells: "  ".join(c.ljust(widths[i]) if i < 2 else c.rjust(widths[i]) for i, c in enumerate(cells))
    try:
        print(line(headers))
        print("  ".join("-" * w for w in widths))
        for row in rows:
            print(line(row))
    except BrokenPipeError:  # e.g. piped into head/less and closed early
        sys.stderr.close()


def write_json(path, results, stat_keys, meta, info=None):
    out = {
        **meta,
        "metrics": stat_keys,
        "metric_info": {k: {"name": (info or {}).get(k, {}).get("name", k),
                            "unit": (info or {}).get(k, {}).get("unit", ""),
                            "description": (info or {}).get(k, {}).get("description", "")}
                        for k in stat_keys},
        "resources": [
            {
                "name": r["host"]["name"],
                "resourceId": r["host"]["resourceId"],
                "error": r.get("error"),
                "samples": [
                    {"timestamp": row["timestamp"], "time_utc": iso_utc(row["timestamp"]),
                     **{k: row[k] for k in stat_keys}}
                    for row in r["rows"]
                ],
            }
            for r in results
        ],
    }
    if path is None:
        json.dump(out, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return
    with open(path, "w") as f:
        json.dump(out, f, indent=2)


class XlsxWriter:
    """Minimal streaming .xlsx writer (standard library only).

    Cell styles: 0 normal, 1 header, 2 date-time, 3 two decimals, 4 thousands.
    """
    STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<numFmts count="1"><numFmt numFmtId="164" formatCode="yyyy-mm-dd hh:mm"/></numFmts>
<fonts count="2"><font><sz val="10"/><name val="Arial"/></font><font><b/><sz val="10"/><name val="Arial"/></font></fonts>
<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>
<fill><patternFill patternType="solid"><fgColor rgb="FFD9E1F2"/><bgColor indexed="64"/></patternFill></fill></fills>
<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>
<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
<cellXfs count="5">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>
<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
<xf numFmtId="2" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
<xf numFmtId="3" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>
</cellXfs>
<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>"""

    def __init__(self, path):
        self.zf = zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED)
        self.sheets = []
        self.filters = []

    @staticmethod
    def col(n):
        s = ""
        while n:
            n, r = divmod(n - 1, 26)
            s = chr(65 + r) + s
        return s

    def add_sheet(self, name, headers, rows, styles, widths):
        """rows: iterable of lists; styles: per-column style id; widths: per-column width."""
        idx = len(self.sheets) + 1
        self.sheets.append(name[:31])
        last_col = self.col(len(headers))
        with self.zf.open(f"xl/worksheets/sheet{idx}.xml", "w") as f:
            w = lambda s: f.write(s.encode("utf-8"))
            w('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
              '<sheetViews><sheetView workbookViewId="0"><pane xSplit="2" ySplit="1" topLeftCell="C2" '
              'activePane="bottomRight" state="frozen"/></sheetView></sheetViews><cols>')
            for i, wd in enumerate(widths, 1):
                w(f'<col min="{i}" max="{i}" width="{wd}" customWidth="1"/>')
            w('</cols><sheetData>')
            w(self._row(1, headers, [1] * len(headers)))
            n = 1
            for n, row in enumerate(rows, 2):
                w(self._row(n, row, styles))
            ref = f"A1:{last_col}{max(n, 1)}"
            w(f'</sheetData><autoFilter ref="{ref}"/></worksheet>')
        abs_ref = re.sub(r"([A-Z]+)(\d+)", r"$\1$\2", ref)
        self.filters.append((name[:31], abs_ref))

    def _row(self, r, values, styles):
        out = [f'<row r="{r}">']
        for c, (v, st) in enumerate(zip(values, styles), 1):
            ref = f"{self.col(c)}{r}"
            if v is None or (isinstance(v, float) and math.isnan(v)):
                continue
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out.append(f'<c r="{ref}" s="{st}"><v>{v}</v></c>')
            else:
                out.append(f'<c r="{ref}" s="{st}" t="inlineStr"><is><t>{escape(str(v))}</t></is></c>')
        out.append("</row>")
        return "".join(out)

    def close(self):
        n = len(self.sheets)
        z = self.zf
        z.writestr("[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                      for i in range(1, n + 1)) + '</Types>')
        z.writestr("_rels/.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>')
        z.writestr("xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
            + "".join(f'<sheet name="{escape(s)}" sheetId="{i}" r:id="rId{i}"/>' for i, s in enumerate(self.sheets, 1))
            + '</sheets><definedNames>'
            + "".join(f'<definedName name="_xlnm._FilterDatabase" localSheetId="{i}" hidden="1">'
                      f"'{escape(nm)}'!{ref}</definedName>" for i, (nm, ref) in enumerate(self.filters))
            + '</definedNames></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
                      for i in range(1, n + 1))
            + f'<Relationship Id="rId{n + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            '</Relationships>')
        z.writestr("xl/styles.xml", self.STYLES)
        z.close()


def write_xlsx(path, results, stat_keys, tz_name, info=None):
    tz, tz_name = get_tz(tz_name)

    def excel_time(ms, zone):
        dt = datetime.fromtimestamp(ms / 1000, zone).replace(tzinfo=None)
        return (dt - datetime(1899, 12, 30)).total_seconds() / 86400

    metric_style = [3 if k.startswith("cpu|") else 4 for k in stat_keys]
    xw = XlsxWriter(path)

    # Data sheet(s): one row per host per timestamp
    d_headers = ["Name", "Resource ID", f"Time ({tz_name})", "Time (UTC)", *[label_for(k, info) for k in stat_keys]]
    d_styles = [0, 0, 2, 2, *metric_style]
    d_widths = [30, 38, 18, 18] + [min(40, max(16, len(h) + 2)) for h in d_headers[4:]]

    def data_rows():
        for r in results:
            for x in r["rows"]:
                yield [x["host"], x["resourceId"], excel_time(x["timestamp"], tz),
                       excel_time(x["timestamp"], timezone.utc), *[x[k] for k in stat_keys]]

    total = sum(len(r["rows"]) for r in results)
    if total <= EXCEL_MAX_ROWS:
        xw.add_sheet("Data", d_headers, data_rows(), d_styles, d_widths)
    else:
        it = data_rows()
        for i in range(1, math.ceil(total / EXCEL_MAX_ROWS) + 1):
            chunk = (row for _, row in zip(range(EXCEL_MAX_ROWS), it))
            xw.add_sheet(f"Data_{i}", d_headers, chunk, d_styles, d_widths)
    xw.close()


# ------------------------------------------------------------------ metric names

def metric_info(client, results, object_type, stat_keys, progress=None):
    """Look up the Operations display name and unit for each stat key.

    Returns {key: {"name", "unit", "description"}}. Keys Operations doesn't describe
    (or any lookup failure) fall back to the raw key, so this never breaks a run.
    """
    progress = progress or log
    adapter = next((r["host"].get("adapterKind") for r in results if r["host"].get("adapterKind")), None) or "VMWARE"
    try:
        catalogue = client.describe_stats(adapter, object_type)
    except Exception as e:
        progress(f"Could not look up metric names ({e}); using stat keys as column names.")
        catalogue = {}
    info = {}
    for k in stat_keys:
        meta = catalogue.get(k)
        if meta:
            info[k] = meta
            progress(f"  {k}  ->  {label_for(k, info)}")
        else:
            info[k] = {"name": k, "unit": "", "description": ""}
            if catalogue:
                progress(f"  {k}  ->  (no name found in Operations; check the stat key spelling)")
    return info


def label_for(key, info):
    """Column header for a stat key, e.g. 'Memory|Active (KB)'."""
    m = (info or {}).get(key)
    if not m or m["name"] == key:
        return key
    return f"{m['name']} ({m['unit']})" if m.get("unit") else m["name"]


# ---------------------------------------------------------------------- collect

def collect(client, object_type, stat_keys, max_samples, name_filter=None, workers=4,
            progress=log, should_stop=None):
    """List objects of object_type and pull stat_keys for each. Returns sorted results."""
    objs = client.list_resources(object_type)
    if name_filter:
        objs = [o for o in objs if name_filter.lower() in o["name"].lower()]
    objs.sort(key=lambda o: o["name"].lower())
    progress(f"Found {len(objs)} {object_type} object(s).")
    if not objs and not name_filter:
        progress("Check the object type spelling; resourceKind names are case-sensitive "
                 "(e.g. HostSystem, VirtualMachine, ClusterComputeResource).")
    results = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = {ex.submit(client.host_stats, o["resourceId"], stat_keys, max_samples): o for o in objs}
        for i, fut in enumerate(as_completed(futs), 1):
            o = futs[fut]
            if should_stop and should_stop():
                for f in futs:
                    f.cancel()
                raise RuntimeError("Cancelled")
            try:
                rows = to_rows(o, fut.result(), stat_keys)
                results.append({"host": o, "rows": rows})
                progress(f"[{i}/{len(objs)}] {o['name']}: {len(rows)} samples")
            except Exception as e:
                results.append({"host": o, "rows": [], "error": str(e)})
                progress(f"[{i}/{len(objs)}] {o['name']}: FAILED - {e}")
    results.sort(key=lambda r: r["host"]["name"].lower())
    return results


def build_meta(server, object_type, max_samples, interval):
    return {"server": server, "object_type": object_type,
            "collected_at": datetime.now().astimezone().isoformat(),
            "max_samples": max_samples, "interval_minutes": interval}


def span_text(max_samples, interval):
    mins = max_samples * interval
    return (f"{mins / 1440:g} day(s)" if mins >= 1440 else
            f"{mins / 60:g} hour(s)" if mins >= 60 else f"{mins} minute(s)")


# --------------------------------------------------------------------------- main

def parse_samplesize(value):
    """Convert '7d', '12h', '2w', '90m' or a bare number (days) into minutes."""
    v = value.strip().lower()
    units = {"m": 1, "h": 60, "d": 1440, "w": 10080}
    try:
        if v[-1] in units:
            qty, mult = float(v[:-1]), units[v[-1]]
        else:
            qty, mult = float(v), 1440  # bare number = days
    except (ValueError, IndexError):
        raise argparse.ArgumentTypeError(f"invalid sample_size '{value}' (use e.g. 7d, 12h, 2w, 1)")
    if qty <= 0:
        raise argparse.ArgumentTypeError("sample_size must be greater than 0")
    return int(qty * mult)


def main():
    p = argparse.ArgumentParser(description="Export VCF Operations host metrics to JSON/Excel.")
    p.add_argument("--server", required=True, help="VCF Operations FQDN or IP (e.g. 192.168.135.113)")
    p.add_argument("--token", default=os.environ.get("VCFOPS_TOKEN"),
                   help="OpsToken (or set VCFOPS_TOKEN). Omit to log in with --user.")
    p.add_argument("--user", help="Username to acquire a token (prompts for password)")
    p.add_argument("--auth_source", "--auth-source", dest="auth_source", help="Auth source for --user (e.g. a vIDM/AD source name)")
    p.add_argument("--format", choices=["json", "xlsx", "xls", "both", "table", "csv"], default="both",
                   help="Files: json, xlsx (xls also accepted), both. "
                        "Screen: table (readable columns) or csv (pipe into other tools). "
                        "Use --format json --output - to print JSON to the screen.")
    p.add_argument("--output", default=None,
                   help="Output file name without extension ('-' with --format json prints to screen)")
    p.add_argument("--stat_keys", "--stat-keys", "--stats", dest="stats", nargs="+", default=DEFAULT_STATS,
                   help="Metric keys to collect, space- or comma-separated "
                        "(default: mem|active_average mem|consumed_average "
                        "cpu|capacity_usagepct_average cpu|coreUtilization_average). Quote keys containing |")
    size = p.add_mutually_exclusive_group()
    size.add_argument("--sample_size", "--sample-size", "--samplesize", dest="sample_size",
                      type=parse_samplesize, default=None,
                      help="How much history to pull: e.g. 7d, 1d, 12h, 2w, or plain days (7). Default 7d")
    size.add_argument("--max_samples", "--max-samples", dest="max_samples", type=int, default=None,
                      help="Exact number of samples per metric (overrides --sample_size)")
    p.add_argument("--interval", type=int, default=5,
                   help="Collection interval in minutes used to convert --sample_size (default 5 = 288/day)")
    p.add_argument("--object_type", "--object-type", dest="object_type", default="HostSystem",
                   help="Operations resourceKind to collect, e.g. HostSystem (default), "
                        "VirtualMachine, ClusterComputeResource, Datastore. Case-sensitive.")
    p.add_argument("--name_filter", "--name-filter", dest="name_filter", help="Only objects whose name contains this text")
    p.add_argument("--workers", type=int, default=4, help="Parallel requests (keep modest)")
    p.add_argument("--raw_keys", "--raw-keys", dest="raw_keys", action="store_true",
                   help="Use stat keys (e.g. mem|active_average) as column names instead of "
                        "Operations metric names (e.g. Memory|Active (KB))")
    p.add_argument("--timezone", default="America/Chicago", help="Time zone for the local time column")
    p.add_argument("--verify_ssl", "--verify-ssl", dest="verify_ssl", action="store_true", help="Verify the server certificate")
    a = p.parse_args()

    a.stats = [k.strip() for item in a.stats for k in item.split(",") if k.strip()]
    if a.format == "xls":
        a.format = "xlsx"
    if a.max_samples is None:
        minutes = a.sample_size if a.sample_size is not None else 7 * 1440
        a.max_samples = max(1, minutes // a.interval)
    log(f"Collecting {a.max_samples} samples per metric (~{span_text(a.max_samples, a.interval)} "
        f"at {a.interval}-min interval).")

    client = OpsClient(a.server, token=a.token, verify=a.verify_ssl)
    if not a.token:
        if not a.user:
            sys.exit("Provide --token, set VCFOPS_TOKEN, or use --user.")
        try:
            client.login(a.user, getpass.getpass(f"Password for {a.user}: "), a.auth_source)
        except RuntimeError as e:
            sys.exit(f"Login failed: {e}")

    try:
        results = collect(client, a.object_type, a.stats, a.max_samples, a.name_filter, a.workers)
    except RuntimeError as e:
        sys.exit(f"Could not list {a.object_type} objects: {e}")
    if not results:
        return
    info = None
    if not a.raw_keys:
        log("Looking up metric names:")
        info = metric_info(client, results, a.object_type, a.stats)

    base = a.output or f"{a.object_type}_metrics_{datetime.now():%Y%m%d_%H%M}"
    meta = build_meta(a.server, a.object_type, a.max_samples, a.interval)
    if a.format == "table":
        print_table(results, a.stats, a.timezone, info)
    elif a.format == "csv":
        print_csv(results, a.stats, a.timezone, info=info)
    if a.format in ("json", "both"):
        if a.output == "-":
            write_json(None, results, a.stats, meta, info)
        else:
            write_json(base + ".json", results, a.stats, meta, info)
            log(f"Wrote {base}.json")
    if a.format in ("xlsx", "both"):
        write_xlsx(base + ".xlsx", results, a.stats, a.timezone, info)
        log(f"Wrote {base}.xlsx")

    failed = [r["host"]["name"] for r in results if r.get("error")]
    if failed:
        log(f"{len(failed)} object(s) failed: {', '.join(failed)}")


if __name__ == "__main__":
    main()
