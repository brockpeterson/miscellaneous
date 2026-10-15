#!/usr/bin/env python3
"""
Browser-based front end for vcf_operations_metrics_picker.py.

Run:   python3 vcf_operations_metrics_gui.py
Then use the page that opens in your browser (http://127.0.0.1:<port>).

- Standard library only; nothing to install.
- Keep vcf_operations_metrics_picker.py in the same folder as this file.
- The page is only reachable from this computer (binds to 127.0.0.1).
- Passwords/tokens are used for the run and never written to disk.
Press Ctrl+C in the terminal to stop it.
"""
import base64
import glob
import io
import json
import os
import re
import secrets
import socket
import sys
import tempfile
import threading
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import vcf_operations_metrics_picker as core
except ImportError:
    sys.exit("vcf_operations_metrics_picker.py must be in the same folder as vcf_operations_metrics_gui.py")

SESSION = secrets.token_urlsafe(24)   # required on every API call (blocks other sites from driving this page)
PREVIEW_ROWS = 200
HERE = os.path.dirname(os.path.abspath(__file__))
LOGO_BASE = os.path.join(HERE, "vcf_operations_metrics_logo")   # saved next to the script, e.g. ...logo.png
LOGO_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif", "image/webp": "webp", "image/svg+xml": "svg"}
LOGO_MAX_BYTES = 2 * 1024 * 1024


def find_logo():
    for ext_mime in LOGO_TYPES.items():
        path = f"{LOGO_BASE}.{ext_mime[1]}"
        if os.path.exists(path):
            return path, ext_mime[0]
    return None, None


def remove_logo():
    for path in glob.glob(LOGO_BASE + ".*"):
        os.remove(path)


class Job:
    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.status = "idle"          # idle | running | done | error | cancelled
        self.log = []
        self.done = 0
        self.total = 0
        self.results = None
        self.info = None
        self.params = None
        self.error = None
        self.started = None
        self.finished = None
        self.cancel = False

    def add_log(self, msg):
        with self.lock:
            self.log.append(f"{datetime.now():%H:%M:%S}  {msg}")
            m = re.match(r"\[(\d+)/(\d+)\]", msg)
            if m:
                self.done, self.total = int(m.group(1)), int(m.group(2))
            m = re.match(r"Found (\d+) ", msg)
            if m:
                self.total = int(m.group(1))


JOB = Job()


def run_job(p):
    try:
        stats = [k.strip() for k in re.split(r"[,\n]", p.get("stat_keys", "")) if k.strip()] or core.DEFAULT_STATS
        interval = int(p.get("interval") or 5)
        if str(p.get("max_samples") or "").strip():
            max_samples = int(p["max_samples"])
        else:
            max_samples = max(1, core.parse_samplesize(str(p.get("sample_size") or "7d")) // interval)
        object_type = (p.get("object_type") or "HostSystem").strip()
        JOB.params = {**{k: v for k, v in p.items() if k not in ("password", "token")},
                      "stats": stats, "max_samples": max_samples, "interval": interval,
                      "object_type": object_type}
        JOB.add_log(f"Collecting {max_samples} samples per metric "
                    f"(~{core.span_text(max_samples, interval)} at {interval}-min interval).")

        client = core.OpsClient(p["server"].strip(), token=(p.get("token") or "").strip() or None,
                                verify=bool(p.get("verify_ssl")))
        if p.get("auth_mode") == "token":
            if not (p.get("token") or "").strip():
                raise RuntimeError("Enter an OpsToken.")
        else:
            if not p.get("user"):
                raise RuntimeError("Enter a username.")
            JOB.add_log(f"Logging in as {p['user']}...")
            client.login(p["user"], p.get("password", ""), (p.get("auth_source") or "").strip() or None)
            JOB.add_log("Logged in.")

        results = core.collect(client, object_type, stats, max_samples,
                               (p.get("name_filter") or "").strip() or None,
                               int(p.get("workers") or 4), progress=JOB.add_log,
                               should_stop=lambda: JOB.cancel)
        if not p.get("raw_keys") and results:
            JOB.add_log("Looking up metric names:")
            JOB.info = core.metric_info(client, results, object_type, stats, progress=JOB.add_log)
        JOB.results = results
        failed = [r["host"]["name"] for r in results if r.get("error")]
        if failed:
            JOB.add_log(f"{len(failed)} object(s) failed: {', '.join(failed)}")
        JOB.add_log("Done.")
        JOB.status = "done"
    except Exception as e:
        JOB.error = str(e)
        JOB.status = "cancelled" if str(e) == "Cancelled" else "error"
        JOB.add_log("Cancelled." if JOB.status == "cancelled" else f"ERROR: {e}")
    finally:
        JOB.finished = time.time()


def status_payload(since):
    with JOB.lock:
        out = {"status": JOB.status, "log": JOB.log[since:], "log_len": len(JOB.log),
               "done": JOB.done, "total": JOB.total, "error": JOB.error,
               "elapsed": round((JOB.finished or time.time()) - JOB.started, 1) if JOB.started else 0}
    if JOB.status == "done" and JOB.results is not None:
        p = JOB.params
        tz, tz_name = core.get_tz(p.get("timezone") or "America/Chicago")
        rows = []
        for r in JOB.results:
            for x in r["rows"]:
                if len(rows) >= PREVIEW_ROWS:
                    break
                rows.append([x["host"], datetime.fromtimestamp(x["timestamp"] / 1000, tz).strftime("%Y-%m-%d %H:%M"),
                             *[core.fmt_val(k, x[k]) for k in p["stats"]]])
        out["result"] = {
            "object_type": p["object_type"],
            "columns": ["Name", f"Time ({tz_name})", *[core.label_for(k, JOB.info) for k in p["stats"]]],
            "preview": rows,
            "total_rows": sum(len(r["rows"]) for r in JOB.results),
            "objects": [{"name": r["host"]["name"], "id": r["host"]["resourceId"],
                         "samples": len(r["rows"]), "error": r.get("error")} for r in JOB.results],
        }
    return out


def build_file(fmt):
    p = JOB.params
    base = f"{p['object_type']}_metrics_{datetime.now():%Y%m%d_%H%M}"
    tz = p.get("timezone") or "America/Chicago"
    if fmt == "xlsx":
        fd, path = tempfile.mkstemp(suffix=".xlsx"); os.close(fd)
        try:
            core.write_xlsx(path, JOB.results, p["stats"], tz, JOB.info)
            with open(path, "rb") as f:
                data = f.read()
        finally:
            os.remove(path)
        return data, base + ".xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if fmt == "json":
        fd, path = tempfile.mkstemp(suffix=".json"); os.close(fd)
        try:
            core.write_json(path, JOB.results, p["stats"],
                            core.build_meta(p["server"], p["object_type"], p["max_samples"], p["interval"]), JOB.info)
            with open(path, "rb") as f:
                data = f.read()
        finally:
            os.remove(path)
        return data, base + ".json", "application/json"
    if fmt == "csv":
        buf = io.StringIO()
        core.print_csv(JOB.results, p["stats"], tz, stream=buf, info=JOB.info)
        return buf.getvalue().encode("utf-8"), base + ".csv", "text/csv"
    raise ValueError("unknown format")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _authed(self, q=None):
        tok = self.headers.get("X-Session") or (q or {}).get("s", [None])[0]
        return secrets.compare_digest(tok or "", SESSION)

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/":
            return self._send(200, PAGE.replace("__SESSION__", SESSION)
                              .replace("__HAS_LOGO__", "true" if find_logo()[0] else "false")
                              .replace("__DEFAULT_STATS__", "\\n".join(core.DEFAULT_STATS)), "text/html; charset=utf-8")
        if u.path == "/logo":
            path, mime = find_logo()
            if not path:
                return self._send(404, {"error": "no logo"})
            with open(path, "rb") as fh:
                return self._send(200, fh.read(), mime,
                                  {"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
                                   "X-Content-Type-Options": "nosniff"})
        if not self._authed(q):
            return self._send(403, {"error": "forbidden"})
        if u.path == "/api/status":
            return self._send(200, status_payload(int(q.get("since", ["0"])[0])))
        if u.path == "/api/download":
            if JOB.status != "done":
                return self._send(409, {"error": "No finished run to download."})
            try:
                data, name, ctype = build_file(q.get("fmt", ["xlsx"])[0])
            except Exception as e:
                return self._send(500, {"error": str(e)})
            return self._send(200, data, ctype, {"Content-Disposition": f'attachment; filename="{name}"'})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._authed():
            return self._send(403, {"error": "forbidden"})
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/api/run":
            if JOB.status == "running":
                return self._send(409, {"error": "A run is already in progress."})
            if not (body.get("server") or "").strip():
                return self._send(400, {"error": "Enter the VCF Operations server."})
            try:
                if not str(body.get("max_samples") or "").strip():
                    core.parse_samplesize(str(body.get("sample_size") or "7d"))
            except Exception as e:
                return self._send(400, {"error": str(e)})
            JOB.reset()
            JOB.status, JOB.started = "running", time.time()
            threading.Thread(target=run_job, args=(body,), daemon=True).start()
            return self._send(200, {"ok": True})
        if self.path == "/api/logo":
            if body.get("remove"):
                remove_logo()
                return self._send(200, {"ok": True})
            m = re.match(r"^data:([\w/+.-]+);base64,(.+)$", body.get("data") or "", re.S)
            if not m or m.group(1) not in LOGO_TYPES:
                return self._send(400, {"error": "Use a PNG, JPG, GIF, WebP or SVG image."})
            data = base64.b64decode(m.group(2))
            if len(data) > LOGO_MAX_BYTES:
                return self._send(400, {"error": "Logo must be under 2 MB."})
            remove_logo()
            with open(f"{LOGO_BASE}.{LOGO_TYPES[m.group(1)]}", "wb") as fh:
                fh.write(data)
            return self._send(200, {"ok": True})
        if self.path == "/api/cancel":
            JOB.cancel = True
            return self._send(200, {"ok": True})
        self._send(404, {"error": "not found"})


def free_port(start=8710):
    for port in range(start, start + 50):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise SystemExit("No free port found.")


PAGE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>VCF Operations Metrics Picker</title>
<style>
:root{--go:#1f8a4c;--go-hover:#18733f;--bg:#f4f5f7;--panel:#fff;--ink:#1c2230;--muted:#667085;--line:#e3e6eb;--accent:#1d6fd8;--accent-ink:#fff;
--ok:#1a7f4b;--bad:#c4321c;--soft:#eef4fd;--mono:ui-monospace,SFMono-Regular,Menlo,monospace}
@media (prefers-color-scheme:dark){:root{--bg:#12151b;--panel:#1a1e26;--ink:#e6e9ef;--muted:#98a1b2;--line:#2a303b;
--accent:#4c94f0;--go:#2c9a5a;--go-hover:#25844d;--soft:#1f2a3a;--ok:#4cc38a;--bad:#ff7a63}}
*{box-sizing:border-box}[hidden]{display:none!important}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
header{position:relative;overflow:hidden;isolation:isolate;padding:30px 28px;color:#eef2ff;
background:radial-gradient(900px 260px at 12% 0%,rgba(99,102,241,.55),transparent 60%),
radial-gradient(700px 240px at 88% 100%,rgba(34,211,238,.35),transparent 60%),
linear-gradient(120deg,#0b1026 0%,#151a45 45%,#1d1250 75%,#0b2a3a 100%)}
header::after{content:"";position:absolute;inset:auto 0 0 0;height:1px;
background:linear-gradient(90deg,transparent,rgba(129,140,248,.9),rgba(34,211,238,.9),transparent)}
#net{position:absolute;inset:0;width:100%;height:100%;z-index:-1;opacity:.7}
.brand{display:flex;align-items:center;gap:16px;max-width:1500px;margin:0 auto}
.spark{flex:none;width:46px;height:46px;border-radius:12px;display:grid;place-items:center;
background:linear-gradient(140deg,rgba(129,140,248,.25),rgba(34,211,238,.18));border:1px solid rgba(165,180,252,.35);
box-shadow:0 0 24px rgba(99,102,241,.45),inset 0 0 12px rgba(34,211,238,.15)}
.spark svg{width:26px;height:26px;filter:drop-shadow(0 0 6px rgba(165,180,252,.8))}
header h1{margin:0;font-size:24px;line-height:1.2;font-weight:750;letter-spacing:-.01em;
background:linear-gradient(90deg,#ffffff 0%,#c7d2fe 40%,#67e8f9 100%);-webkit-background-clip:text;background-clip:text;color:transparent}
header p{margin:4px 0 0;font-size:14.5px;color:#b9c3f5;letter-spacing:.01em}
header p b{color:#67e8f9;font-weight:600}
.brand>div:nth-child(2){flex:1;min-width:0}
.logo{flex:none;position:relative;display:flex;align-items:center}
.logo img{display:block;max-height:52px;max-width:220px;object-fit:contain;padding:8px 12px;border-radius:10px;
background:rgba(255,255,255,.92);box-shadow:0 0 0 1px rgba(165,180,252,.35),0 0 22px rgba(99,102,241,.35)}
.addlogo{border:1px dashed rgba(165,180,252,.6);background:rgba(255,255,255,.06);color:#c7d2fe;border-radius:10px;
padding:10px 14px;font:inherit;font-size:13px;cursor:pointer;white-space:nowrap}
.addlogo:hover{background:rgba(255,255,255,.12);color:#fff;border-color:#a5b4fc}
.logotools{position:absolute;top:calc(100% + 6px);right:0;display:flex;gap:6px;opacity:0;pointer-events:none;transition:opacity .15s;z-index:2}
.logo:hover .logotools,.logo:focus-within .logotools{opacity:1;pointer-events:auto}
.logotools button{border:0;border-radius:6px;padding:4px 10px;font:inherit;font-size:12px;cursor:pointer;
background:rgba(15,20,50,.9);color:#e0e7ff;box-shadow:0 0 0 1px rgba(165,180,252,.4)}
.logotools button:hover{background:#1e2563}
@media (max-width:600px){.logo img{max-height:38px;max-width:120px}.addlogo{padding:8px 10px;font-size:12px}}
@media (prefers-reduced-motion:reduce){.spark svg{animation:none!important}}
@keyframes pulse{0%,100%{transform:scale(1);opacity:1}50%{transform:scale(.88);opacity:.8}}
.spark svg{animation:pulse 3.2s ease-in-out infinite}
main{display:grid;grid-template-columns:minmax(320px,400px) 1fr;gap:20px;padding:20px 24px;max-width:1500px;margin:0 auto}
@media (max-width:900px){main{grid-template-columns:1fr}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:18px}
h2{font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);margin:18px 0 10px;font-weight:600}
h2:first-child{margin-top:0}
label{display:block;font-size:13px;font-weight:550;margin:10px 0 4px}
.hint{font-weight:400;color:var(--muted);font-size:12px}
input,select,textarea{width:100%;padding:8px 10px;border:1px solid var(--line);border-radius:7px;background:var(--bg);color:var(--ink);font:inherit}
textarea{font-family:var(--mono);font-size:12.5px;min-height:96px;resize:vertical}
input:focus,select:focus,textarea:focus{outline:2px solid var(--accent);outline-offset:-1px;border-color:transparent}
.row{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.seg{display:flex;border:1px solid var(--line);border-radius:7px;overflow:hidden}
.seg button{flex:1;border:0;background:transparent;color:var(--muted);padding:7px;font:inherit;cursor:pointer}
.seg button.on{background:var(--soft);color:var(--accent);font-weight:600}
.check{display:flex;align-items:center;gap:8px;margin-top:10px;font-size:13px}.check input{width:auto}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}
.chip{border:1px solid var(--line);background:transparent;color:var(--ink);border-radius:999px;padding:3px 10px;font-size:12px;cursor:pointer}
.chip:hover{border-color:var(--accent);color:var(--accent)}
.btn{border:0;border-radius:8px;padding:10px 16px;font:inherit;font-weight:600;cursor:pointer}
.primary{background:var(--go);color:#fff;width:100%;margin-top:18px;padding:12px}.primary:hover:not(:disabled){background:var(--go-hover)}
.primary:disabled{opacity:.55;cursor:default}
.ghost{background:transparent;border:1px solid var(--line);color:var(--ink)}
.dl{background:var(--soft);color:var(--accent);border:1px solid transparent}.dl:hover{border-color:var(--accent)}
.right{display:flex;flex-direction:column;gap:20px;min-width:0}
.statusline{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.badge{font-size:12px;font-weight:600;padding:3px 10px;border-radius:999px;background:var(--soft);color:var(--muted)}
.badge.running{color:var(--accent)}.badge.done{color:var(--ok)}.badge.error,.badge.cancelled{color:var(--bad)}
.bar{height:6px;background:var(--line);border-radius:3px;overflow:hidden;margin:12px 0}
.bar div{height:100%;width:0;background:var(--accent);transition:width .3s}
pre{margin:0;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:10px 12px;height:190px;overflow:auto;
font:12px/1.5 var(--mono);color:var(--muted);white-space:pre-wrap}
.downloads{display:flex;gap:10px;flex-wrap:wrap}
.tablewrap{overflow:auto;max-height:420px;border:1px solid var(--line);border-radius:8px}
table{border-collapse:collapse;width:100%;font-size:12.5px}
th,td{padding:6px 10px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th{position:sticky;top:0;background:var(--panel);font-weight:600;color:var(--muted)}
th:nth-child(-n+2),td:nth-child(-n+2){text-align:left}
td{font-variant-numeric:tabular-nums}
.err{color:var(--bad)}.muted{color:var(--muted)}.empty{color:var(--muted);text-align:center;padding:40px 10px}
.pw{position:relative}.pw input{padding-right:58px}
.eye{position:absolute;right:4px;top:50%;transform:translateY(-50%);border:0;background:transparent;color:var(--accent);
font:inherit;font-size:12px;font-weight:600;padding:5px 8px;border-radius:5px;cursor:pointer}.eye:hover{background:var(--soft)}
.msg{color:var(--bad);font-size:13px;margin-top:10px;min-height:1em}
</style></head><body>
<header><canvas id="net" aria-hidden="true"></canvas>
<div class="brand">
  <div class="spark" aria-hidden="true"><svg viewBox="0 0 24 24"><defs><linearGradient id="sg" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0" stop-color="#ffffff"/><stop offset=".5" stop-color="#a5b4fc"/><stop offset="1" stop-color="#67e8f9"/></linearGradient></defs>
    <path fill="url(#sg)" d="M12 1.5c.5 4.9 2.6 7 7.5 7.5-4.9.5-7 2.6-7.5 7.5-.5-4.9-2.6-7-7.5-7.5 4.9-.5 7-2.6 7.5-7.5z"/>
    <path fill="#67e8f9" d="M19.5 14.5c.25 2.1 1.15 3 3.25 3.25-2.1.25-3 1.15-3.25 3.25-.25-2.1-1.15-3-3.25-3.25 2.1-.25 3-1.15 3.25-3.25z"/></svg></div>
  <div><h1>VCF Operations Metrics Picker</h1><p>Collect the metrics <b>you</b> want!</p></div>
  <div class="logo" id="logo">
    <button type="button" class="addlogo" id="addlogo">+ Add company logo</button>
    <img id="logoimg" alt="Company logo" hidden>
    <div class="logotools" id="logotools" hidden><button type="button" id="changelogo">Change</button><button type="button" id="removelogo">Remove</button></div>
    <input type="file" id="logofile" accept="image/png,image/jpeg,image/gif,image/webp,image/svg+xml" hidden>
  </div>
</div></header>
<main>
<form class="card" id="f" autocomplete="on">
  <h2>Connection</h2>
  <label>VCF Operations FQDN or IP</label>
  <input name="server" placeholder="vcf-ops.example.com or 192.168.135.113" required>
  <label>Sign in with</label>
  <div class="seg" id="authseg"><button type="button" data-v="user" class="on">Username</button><button type="button" data-v="token">OpsToken</button></div>
  <div id="userbox">
    <div class="row"><div><label>Username</label><input name="user" autocomplete="username" placeholder="admin"></div>
    <div><label>Password</label><div class="pw"><input name="password" type="password" autocomplete="current-password">
    <button type="button" class="eye" data-for="password" aria-label="Show password" title="Show password">Show</button></div></div></div>
    <label>Auth source <span class="hint">optional, for AD/vIDM users</span></label><input name="auth_source">
  </div>
  <div id="tokenbox" hidden><label>OpsToken</label><div class="pw"><input name="token" type="password" autocomplete="off">
    <button type="button" class="eye" data-for="token" aria-label="Show token" title="Show token">Show</button></div></div>
  <label class="check"><input type="checkbox" name="verify_ssl"> Verify SSL certificate</label>

  <h2>What to collect</h2>
  <label>Object type</label>
  <select name="object_type"><option value="VirtualMachine">VirtualMachine</option><option value="HostSystem" selected>HostSystem</option>
  <option value="ClusterComputeResource">ClusterComputeResource</option><option value="Datastore">Datastore</option></select>
  <label>Stat keys <span class="hint">one per line or comma-separated</span></label>
  <textarea name="stat_keys" spellcheck="false"></textarea>
  <div class="chips">
    <button type="button" class="chip" data-add="cpu|usage_average">+ cpu|usage_average</button>
    <button type="button" class="chip" data-add="mem|usage_average">+ mem|usage_average</button>
    <button type="button" class="chip" data-add="cpu|readyPct">+ cpu|readyPct</button>
    <button type="button" class="chip" id="resetstats">Reset to defaults</button>
  </div>
  <label class="check"><input type="checkbox" name="metric_names" checked> Use Operations metric names as column headers
    <span class="hint">(e.g. Memory|Active (KB))</span></label>
  <label>Name filter <span class="hint">optional, contains text</span></label>
  <input name="name_filter" placeholder="e.g. esx-prod">

  <h2>Time range</h2>
  <div class="row">
    <div><label>Sample size</label>
      <select name="sample_size"><option value="1h">Last hour</option><option value="6h">6 hours</option><option value="12h">12 hours</option>
      <option value="1d">1 day</option><option value="3d">3 days</option><option value="7d" selected>7 days</option>
      <option value="14d">14 days</option><option value="30d">30 days</option><option value="custom">Custom…</option></select></div>
    <div><label>Interval <span class="hint">minutes</span></label><input name="interval" type="number" min="1" value="5"></div>
  </div>
  <div id="custombox" hidden><label>Custom size <span class="hint">e.g. 45m, 2d, 10d, 3w</span></label><input name="custom_size" placeholder="10d"></div>
  <div class="hint" id="samplehint" style="margin-top:8px"></div>

  <details style="margin-top:14px"><summary class="muted" style="cursor:pointer">Advanced</summary>
    <div class="row"><div><label>Parallel requests</label><input name="workers" type="number" min="1" max="16" value="4"></div>
    <div><label>Time zone</label><input name="timezone"></div></div>
  </details>
  <button class="btn primary" id="run" type="submit">Collect metrics</button>
  <div class="msg" id="msg"></div>
</form>

<section class="right">
  <div class="card">
    <div class="statusline"><h2 style="margin:0">Progress</h2><span class="badge" id="badge">Idle</span>
      <span class="muted" id="counts"></span><span style="flex:1"></span>
      <button class="btn ghost" id="cancel" hidden>Cancel</button></div>
    <div class="bar"><div id="barfill"></div></div>
    <pre id="log">Fill in the form and click Collect metrics.</pre>
  </div>
  <div class="card" id="results" hidden>
    <div class="statusline" style="margin-bottom:14px"><h2 style="margin:0">Results</h2><span class="muted" id="rsummary"></span></div>
    <div class="downloads">
      <button class="btn dl" data-fmt="xlsx">Download Excel (.xlsx)</button>
      <button class="btn dl" data-fmt="json">Download JSON</button>
      <button class="btn dl" data-fmt="csv">Download CSV</button>
    </div>
    <h2>Objects</h2>
    <div class="tablewrap" style="max-height:220px"><table id="objtable"></table></div>
    <h2 id="previewtitle">Data preview</h2>
    <div class="tablewrap"><table id="preview"></table></div>
  </div>
</section>
</main>
<script>
const S="__SESSION__", DEFAULT_STATS="__DEFAULT_STATS__";
const f=document.getElementById("f"), $=id=>document.getElementById(id);
let authMode="user", since=0, timer=null;
const KEEP=["server","user","auth_source","object_type","stat_keys","name_filter","sample_size","custom_size","interval","workers","timezone"];

function load(){let saved={};try{saved=JSON.parse(localStorage.getItem("vcfops")||"{}")}catch(e){}
  f.stat_keys.value=DEFAULT_STATS; f.timezone.value=Intl.DateTimeFormat().resolvedOptions().timeZone||"America/Chicago";
  for(const k of KEEP) if(saved[k]!=null && f[k]) f[k].value=saved[k];
  if(!f.object_type.value) f.object_type.value="HostSystem";
  if(!f.sample_size.value) f.sample_size.value="7d";
  if(saved.verify_ssl) f.verify_ssl.checked=true;
  if(saved.metric_names===false) f.metric_names.checked=false;
  if(saved.authMode) setAuth(saved.authMode); updateSize();}
function save(){const o={authMode,verify_ssl:f.verify_ssl.checked,metric_names:f.metric_names.checked};for(const k of KEEP)o[k]=f[k].value;
  try{localStorage.setItem("vcfops",JSON.stringify(o))}catch(e){}}
function setAuth(v){authMode=v;document.querySelectorAll("#authseg button").forEach(b=>b.classList.toggle("on",b.dataset.v===v));
  $("userbox").hidden=v!=="user";$("tokenbox").hidden=v!=="token";}
document.querySelectorAll("#authseg button").forEach(b=>b.onclick=()=>setAuth(b.dataset.v));

function parseSize(v){const m=String(v).trim().toLowerCase().match(/^(\d*\.?\d+)\s*([mhdw]?)$/);if(!m)return null;
  return parseFloat(m[1])*({m:1,h:60,d:1440,w:10080}[m[2]||"d"]);}
function updateSize(){$("custombox").hidden=f.sample_size.value!=="custom";
  const v=f.sample_size.value==="custom"?f.custom_size.value:f.sample_size.value, mins=parseSize(v), iv=+f.interval.value||5;
  $("samplehint").textContent=mins?`${Math.max(1,Math.floor(mins/iv)).toLocaleString()} samples per metric per object (${Math.round(1440/iv)} per day)`:(v?"Use a number with m, h, d or w — e.g. 10d":"");}
["sample_size","custom_size","interval"].forEach(n=>f[n].addEventListener("input",updateSize));

document.querySelectorAll("[data-add]").forEach(b=>b.onclick=()=>{const cur=f.stat_keys.value.split(/[\n,]/).map(s=>s.trim()).filter(Boolean);
  if(!cur.includes(b.dataset.add))cur.push(b.dataset.add);f.stat_keys.value=cur.join("\n");});
$("resetstats").onclick=()=>f.stat_keys.value=DEFAULT_STATS;

async function api(path,opts={}){const r=await fetch(path,{...opts,headers:{"X-Session":S,"Content-Type":"application/json",...(opts.headers||{})}});
  const j=await r.json().catch(()=>({}));if(!r.ok)throw new Error(j.error||("HTTP "+r.status));return j;}

f.onsubmit=async e=>{e.preventDefault();$("msg").textContent="";save();
  const size=f.sample_size.value==="custom"?f.custom_size.value:f.sample_size.value;
  if(!parseSize(size)){$("msg").textContent="Enter a valid custom sample size, e.g. 10d.";return;}
  const body={server:f.server.value,auth_mode:authMode,user:f.user.value,password:f.password.value,auth_source:f.auth_source.value,
    token:f.token.value,verify_ssl:f.verify_ssl.checked,raw_keys:!f.metric_names.checked,object_type:f.object_type.value,stat_keys:f.stat_keys.value,
    name_filter:f.name_filter.value,sample_size:size,interval:f.interval.value,workers:f.workers.value,timezone:f.timezone.value};
  try{await api("/api/run",{method:"POST",body:JSON.stringify(body)});}catch(err){$("msg").textContent=err.message;return;}
  since=0;$("log").textContent="";$("results").hidden=true;$("run").disabled=true;$("cancel").hidden=false;poll();};
document.querySelectorAll(".eye").forEach(b=>b.onclick=()=>{const inp=f[b.dataset.for],show=inp.type==="password";
  inp.type=show?"text":"password";b.textContent=show?"Hide":"Show";
  const what=b.dataset.for==="token"?"token":"password";b.title=b.ariaLabel=(show?"Hide ":"Show ")+what;});
function hideSecrets(){document.querySelectorAll(".eye").forEach(b=>{f[b.dataset.for].type="password";b.textContent="Show";});}
$("cancel").onclick=()=>api("/api/cancel",{method:"POST"});

const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
async function poll(){clearTimeout(timer);let s;try{s=await api("/api/status?since="+since);}catch(e){timer=setTimeout(poll,1500);return;}
  if(s.log.length){const pre=$("log");pre.textContent+=(pre.textContent?"\n":"")+s.log.join("\n");pre.scrollTop=pre.scrollHeight;}
  since=s.log_len;const b=$("badge");b.className="badge "+s.status;
  b.textContent={idle:"Idle",running:"Running",done:"Complete",error:"Error",cancelled:"Cancelled"}[s.status];
  $("counts").textContent=s.total?`${s.done} / ${s.total} objects · ${s.elapsed}s`:(s.elapsed?`${s.elapsed}s`:"");
  $("barfill").style.width=s.status==="done"?"100%":s.total?(100*s.done/s.total)+"%":(s.status==="running"?"4%":"0");
  if(s.status==="running"){timer=setTimeout(poll,800);return;}
  $("run").disabled=false;$("cancel").hidden=true;f.password.value="";hideSecrets();
  if(s.status==="done"&&s.result)showResults(s.result);}

function showResults(r){$("results").hidden=false;const failed=r.objects.filter(o=>o.error).length;
  $("rsummary").textContent=`${r.objects.length} ${r.object_type} object(s) · ${r.total_rows.toLocaleString()} rows`+(failed?` · ${failed} failed`:"");
  $("objtable").innerHTML="<tr><th>Name</th><th>Resource ID</th><th>Samples</th><th style=\"text-align:left\">Status</th></tr>"+
    r.objects.map(o=>`<tr><td>${esc(o.name)}</td><td class="muted">${esc(o.id)}</td><td>${o.samples.toLocaleString()}</td>`+
    `<td class="${o.error?"err":""}" style="text-align:left">${o.error?esc(o.error):"OK"}</td></tr>`).join("");
  $("previewtitle").textContent=r.total_rows>r.preview.length?`Data preview (first ${r.preview.length} of ${r.total_rows.toLocaleString()} rows)`:"Data";
  $("preview").innerHTML=r.preview.length?"<tr>"+r.columns.map(c=>`<th>${esc(c)}</th>`).join("")+"</tr>"+
    r.preview.map(row=>"<tr>"+row.map(c=>`<td>${esc(c)}</td>`).join("")+"</tr>").join(""):
    `<tr><td class="empty">No data returned. Check the object type and stat keys.</td></tr>`;}

document.querySelectorAll("[data-fmt]").forEach(b=>b.onclick=()=>{location.href=`/api/download?fmt=${b.dataset.fmt}&s=${encodeURIComponent(S)}`;});
(function(){const c=$("net"),x=c.getContext("2d"),still=matchMedia("(prefers-reduced-motion: reduce)").matches;
  let W,H,dpr,nodes=[];
  function size(){dpr=window.devicePixelRatio||1;W=c.clientWidth;H=c.clientHeight;c.width=W*dpr;c.height=H*dpr;x.setTransform(dpr,0,0,dpr,0,0);
    const n=Math.round(Math.min(70,Math.max(18,W/22)));
    nodes=Array.from({length:n},()=>({x:Math.random()*W,y:Math.random()*H,vx:(Math.random()-.5)*.25,vy:(Math.random()-.5)*.25,r:Math.random()*1.6+.6}));}
  function draw(){x.clearRect(0,0,W,H);const L=110;
    for(let i=0;i<nodes.length;i++){const a=nodes[i];
      for(let j=i+1;j<nodes.length;j++){const b=nodes[j],d=Math.hypot(a.x-b.x,a.y-b.y);
        if(d<L){x.strokeStyle=`rgba(148,163,255,${(1-d/L)*.35})`;x.lineWidth=.7;x.beginPath();x.moveTo(a.x,a.y);x.lineTo(b.x,b.y);x.stroke();}}}
    for(const a of nodes){x.fillStyle="rgba(165,243,252,.85)";x.beginPath();x.arc(a.x,a.y,a.r,0,Math.PI*2);x.fill();
      if(!still){a.x+=a.vx;a.y+=a.vy;if(a.x<0||a.x>W)a.vx*=-1;if(a.y<0||a.y>H)a.vy*=-1;}}
    if(!still)requestAnimationFrame(draw);}
  size();draw();addEventListener("resize",()=>{size();if(still)draw();});})();
(function(){let has=__HAS_LOGO__;const img=$("logoimg"),add=$("addlogo"),tools=$("logotools"),file=$("logofile");
  function show(){img.hidden=!has;tools.hidden=!has;add.hidden=has;if(has)img.src="/logo?t="+Date.now();}
  img.onerror=()=>{has=false;show();};
  add.onclick=$("changelogo").onclick=()=>file.click();
  $("removelogo").onclick=async()=>{await api("/api/logo",{method:"POST",body:JSON.stringify({remove:true})});has=false;show();};
  function shrink(f){return new Promise((ok,bad)=>{const rd=new FileReader();rd.onerror=bad;rd.onload=()=>{
    if(f.type==="image/svg+xml"||f.type==="image/gif")return ok(rd.result);          // keep vector/animated as-is
    const im=new Image();im.onerror=()=>bad(new Error("Could not read that image."));im.onload=()=>{
      const k=Math.min(1,480/im.width,200/im.height),cv=document.createElement("canvas");
      cv.width=Math.round(im.width*k);cv.height=Math.round(im.height*k);cv.getContext("2d").drawImage(im,0,0,cv.width,cv.height);
      ok(cv.toDataURL("image/png"));};im.src=rd.result;};rd.readAsDataURL(f);});}
  file.onchange=async()=>{const f=file.files[0];file.value="";if(!f)return;
    try{await api("/api/logo",{method:"POST",body:JSON.stringify({data:await shrink(f)})});has=true;show();}
    catch(e){alert("Logo upload failed: "+e.message);}};
  show();})();
load();poll();
</script></body></html>"""


def main():
    port = int(os.environ.get("VCFOPS_GUI_PORT") or free_port())
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"VCF Operations Metrics Picker running at {url}")
    print("Press Ctrl+C to stop.")
    if not os.environ.get("VCFOPS_GUI_NO_BROWSER"):
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
