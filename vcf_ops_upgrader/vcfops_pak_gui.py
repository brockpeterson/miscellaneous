#!/usr/bin/env python3
"""
vcfops_pak_gui.py - VCF Operations Upgrader: local web GUI for vcfops_pak_upgrade.py

Starts a small web server on THIS machine only (127.0.0.1) and opens the UI in
your browser. The upgrade itself is done by vcfops_pak_upgrade.py, which must be
in the same folder. Standard library only - nothing to install.

    python3 vcfops_pak_gui.py            # opens http://127.0.0.1:8765
    python3 vcfops_pak_gui.py --port 9000 --no-browser

Leave this terminal window open while the upgrade runs; Ctrl-C quits the GUI
(an install already started on the cluster keeps going).
"""

import argparse
import json
import os
import secrets
import ssl
import sys
import threading
import time
import traceback
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    import vcfops_pak_upgrade as up
except ImportError:
    sys.exit("vcfops_pak_upgrade.py must be in the same folder as vcfops_pak_gui.py")


class UpgradeError(Exception):
    pass


# ----------------------------------------------------------------------------- shared state

LOCK = threading.RLock()
TOKEN = secrets.token_urlsafe(24)
STEP_DEFS = [("connect", "Connect"), ("offline", "Take offline"), ("upload", "Upload PAK"),
             ("install", "Install"), ("online", "Back online"), ("verify", "Verify version")]


def fresh_state():
    return {
        "phase": "setup",          # setup | ready | running | done | error | cancelled
        "busy": None,              # short text while a side job runs (e.g. bringing online)
        "conn": None,              # {host,user,state,version,nodes,reachable}
        "opts": None,
        "steps": [],
        "started": None, "ended": None,
        "before": None, "after": None,
        "pak_id": None, "pak_name": None, "pak_size": None,
        "error": None,
        "log": [],
    }


S = fresh_state()
OPS = {"ops": None}


def gui_log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with LOCK:
        S["log"].append(line)
        if len(S["log"]) > 5000:
            del S["log"][:1000]


def gui_die(msg, code=1):
    gui_log(f"ERROR: {msg}")
    raise UpgradeError(msg)


# the upgrade module logs and fails through these two functions; route them to the GUI
up.log = gui_log
up.die = gui_die


# ----------------------------------------------------------------------------- helpers

def step(sid):
    for s in S["steps"]:
        if s["id"] == sid:
            return s
    raise KeyError(sid)


def begin(sid):
    with LOCK:
        st = step(sid)
        st.update(status="active", start=time.time())


def finish(sid, status="done", detail=None):
    with LOCK:
        st = step(sid)
        st["status"] = status
        st["end"] = time.time()
        if st.get("start") is None:
            st["start"] = st["end"]
        if detail:
            st["detail"] = detail


def active_step():
    for s in S["steps"]:
        if s["status"] == "active":
            return s["id"]
    return None


def make_ctx(insecure, ca_bundle):
    if insecure:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return ssl.create_default_context(cafile=ca_bundle) if ca_bundle else ssl.create_default_context()


def live_upload(ops):
    body = ops.upload_body if ops else None
    if not body:
        return None
    el = max(time.time() - (ops.upload_start or time.time()), 0.001)
    sent, total = body.bytes_read, body.len
    rate = sent / el
    return {"sent": sent, "total": total, "pct": (sent * 100.0 / total) if total else 0,
            "rate": rate / 1024 ** 2, "eta": (total - sent) / rate if rate and sent < total else 0,
            "verifying": sent >= total}


def assistant(snap, live):
    """Plain-language narration of what's happening, built from the current state."""
    ph, conn = snap["phase"], snap["conn"] or {}
    host = conn.get("host", "the cluster")
    if snap["busy"]:
        return snap["busy"]
    if ph == "setup":
        return ("Enter the primary node's address and admin password, then test the connection. "
                "Nothing on the cluster changes until you press the start button.")
    if ph == "ready":
        if not conn.get("reachable", True):
            return ("I couldn't reach the cluster. That's fine if an install is restarting it: choose "
                    "\"Monitor an install already running\" and I'll keep trying until it answers.")
        return (f"Connected. {host} is {conn.get('state', '?').lower()} on {conn.get('version')}. "
                "Pick the PAK and options. When you start, the cluster goes offline and stays offline "
                "until the install finishes.")
    if ph == "done":
        return (f"All done. {host} is online and reporting {snap['after']}. Worth doing next: check that "
                "cloud proxies and management packs show compatible versions, then delete the "
                "pre-upgrade snapshots once you're happy. Snapshots slow the VM the longer they stay.")
    if ph == "cancelled":
        return ("Stopped. If the cluster is still offline, use \"Bring cluster online\" below, or start "
                "again from setup. If an install had already started, it keeps running on the cluster: "
                "reconnect and choose \"Monitor an install already running\"" +
                (f" with PAK ID {snap['pak_id']}." if snap["pak_id"] else "."))
    if ph == "error":
        extra = (f" If an install had started, reconnect and monitor PAK ID {snap['pak_id']}."
                 if snap["pak_id"] else "")
        return f"Something went wrong: {snap['error']} Check the admin UI before doing anything else.{extra}"
    a = active_step() or ""
    if a == "offline":
        return "Taking the cluster offline. This usually takes a minute or two."
    if a == "upload" and live:
        if live["verifying"]:
            return ("Every byte is sent. The server is checking the PAK now, which can take a few "
                    "minutes, so this will look quiet for a bit.")
        return (f"Uploading at {live['rate']:.1f} MiB/s, about {up.fmt_dur(live['eta'])} left. When the "
                "last byte is sent, the server checks the PAK before the install can start.")
    if a == "install":
        if snap.get("install_status") == "UNREACHABLE":
            return ("The cluster stopped answering. That's normal: the install restarts the node's "
                    "services. I'm checking every 30 seconds and will pick up as soon as it's back. "
                    "Leave \"Bring Cluster Online\" alone in the admin UI.")
        return ("The install is running on the cluster. Nodes upgrade one at a time and services "
                "restart along the way, so expect gaps where the cluster doesn't answer.")
    if a == "online":
        return "The install finished. Waiting for the cluster to come back online on its own."
    if a == "verify":
        return "Cluster is back. Reading the new version."
    return "Working…"


def snapshot():
    with LOCK:
        snap = {k: v for k, v in S.items() if k != "log"}
        snap["steps"] = [dict(s) for s in S["steps"]]
        snap["log"] = S["log"][-400:]
    ops = OPS["ops"]
    live = live_upload(ops) if snap["phase"] == "running" and active_step() == "upload" else None
    snap["upload"] = live
    snap["cluster_state"] = (ops.last_cluster_state if ops and ops.last_cluster_state
                             else (snap["conn"] or {}).get("state"))
    snap["nodes"] = (ops._last_nodes if ops and ops._last_nodes else (snap["conn"] or {}).get("nodes", {}))
    snap["install_status"] = ops.last_install_status if ops else None
    snap["assistant"] = assistant(snap, live)
    snap["now"] = time.time()
    snap.pop("opts", None)
    return snap


# ----------------------------------------------------------------------------- actions

def do_connect(p):
    with LOCK:
        if S["phase"] == "running":
            return 409, {"error": "An upgrade is running."}
    host = (p.get("host") or "").strip()
    user = (p.get("user") or "admin").strip()
    pw = p.get("password") or ""
    if not host or not pw:
        return 400, {"error": "Enter the host and password."}
    try:
        ctx = make_ctx(bool(p.get("insecure")), (p.get("ca_bundle") or "").strip() or None)
    except (OSError, ssl.SSLError) as e:
        return 400, {"error": f"Can't load the CA bundle: {e}"}
    ops = up.Ops(host, user, pw, ctx, heartbeat=300)
    reachable = True
    try:
        state, data = ops.online_state()
    except up.AuthError:
        return 401, {"error": "Login failed: incorrect user or password."}
    except RuntimeError as e:
        return 502, {"error": f"The CASA API answered with an error ({e}). Is this the primary node?"}
    except up.NET_ERRORS as e:
        if "CERTIFICATE" in str(e).upper():
            return 502, {"error": "Certificate check failed. Tick \"Skip TLS certificate check\" or give a CA bundle."}
        if not p.get("monitor"):
            return 502, {"error": f"Can't reach {host}: {e}"}
        state, data, reachable = "UNREACHABLE", None, False
    version = ops.version() if reachable else "unknown"
    nodes = up.node_states(data) if data is not None else {}
    with LOCK:
        OPS["ops"] = ops
        S.update(fresh_state())
        S["phase"] = "ready"
        S["conn"] = {"host": host, "user": user, "state": state, "version": version,
                     "nodes": nodes, "reachable": reachable}
    gui_log(f"Connected to {host}. Cluster state: {state}. Version: {version}" if reachable else
            f"{host} not reachable yet - monitor mode will keep trying.")
    if reachable and state == "UNKNOWN":
        gui_log(f"Unrecognised cluster state response: {json.dumps(data)[:600]}")
    return 200, snapshot()


def do_start(p):
    with LOCK:
        if S["phase"] != "ready" or not OPS["ops"]:
            return 409, {"error": "Connect first."}
        if S["conn"]["state"] == "UNKNOWN" and not p.get("monitor"):
            return 409, {"error": "The cluster state couldn't be read, so I won't take it offline. See the log."}
    monitor = bool(p.get("monitor"))
    pak = os.path.expanduser((p.get("pak") or "").strip())
    pak_id = (p.get("pak_id") or "").strip()
    if monitor and not pak_id:
        return 400, {"error": "Enter the PAK ID of the running install."}
    if not monitor and not os.path.isfile(pak):
        return 400, {"error": f"PAK file not found: {pak or '(none chosen)'}"}

    def mins(key, default):
        try:
            return max(1, int(float(p.get(key) or default))) * 60
        except ValueError:
            return default * 60

    opts = {"monitor": monitor, "pak": pak, "pak_id": pak_id, "clobber": bool(p.get("clobber")),
            "bring_online": bool(p.get("bring_online")),
            "heartbeat": int(p.get("heartbeat") or 300),
            "t_offline": mins("t_offline", 60), "t_install": mins("t_install", 240),
            "t_online": mins("t_online", 120)}
    with LOCK:
        S.update(phase="running", opts=opts, started=time.time(), ended=None, error=None,
                 before=S["conn"]["version"], pak_id=pak_id or None,
                 pak_name=os.path.basename(pak) if not monitor else None,
                 pak_size=os.path.getsize(pak) if not monitor else None,
                 steps=[{"id": i, "label": l, "status": "pending", "start": None, "end": None,
                         "detail": None} for i, l in STEP_DEFS])
    threading.Thread(target=runner, args=(opts,), daemon=True).start()
    return 200, snapshot()


def runner(o):
    ops = OPS["ops"]
    ops.cancel.clear()
    ops.heartbeat = o["heartbeat"]
    ops.upload_body = None
    ops.last_install_status = None
    before = S["before"]
    try:
        finish("connect")
        if o["monitor"]:
            finish("offline", "skipped", "monitor only")
            finish("upload", "skipped", "monitor only")
            pak_id = o["pak_id"]
            gui_log(f"Monitor-only: watching install of {pak_id}")
        else:
            begin("offline")
            if S["conn"]["state"] != "OFFLINE":
                gui_log("Taking cluster offline...")
                ops.set_online_state("OFFLINE", "PAK upgrade via GUI")
                if not ops.wait_state("OFFLINE", o["t_offline"]):
                    raise UpgradeError("The cluster didn't reach OFFLINE in time.")
            finish("offline")
            begin("upload")
            pak_id = ops.upload_pak(o["pak"], o["clobber"])
            with LOCK:
                S["pak_id"] = pak_id
            sz = S["pak_size"] or 0
            el = time.time() - step("upload")["start"]
            finish("upload", detail=f"{sz / 1024 ** 3:.2f} GiB, avg {sz / max(el, 1) / 1024 ** 2:.1f} MiB/s")

        begin("install")
        if not o["monitor"]:
            ops.install_pak(pak_id)
        if not ops.wait_install(pak_id, o["t_install"], before_version=before):
            raise UpgradeError("The install didn't finish within the time limit.")
        finish("install")

        begin("online")
        online = ops.wait_state("ONLINE", 900 if o["bring_online"] else o["t_online"])
        if not online and o["bring_online"]:
            gui_log("Cluster still offline - requesting ONLINE...")
            ops.set_online_state("ONLINE", None)
            online = ops.wait_state("ONLINE", o["t_online"])
        if not online:
            raise UpgradeError("The cluster didn't come back online. If the admin UI shows "
                               "\"Finish Installation\", complete it there.")
        finish("online")

        begin("verify")
        after = ops.version()
        finish("verify", detail=after)
        with LOCK:
            S.update(phase="done", after=after, ended=time.time())
        gui_log(f"Upgrade finished. Previous: {before}  New: {after}")
        if after == before and before != "unknown":
            gui_log("WARNING: version unchanged - verify in the admin UI.")
    except up.Cancelled:
        with LOCK:
            a = active_step()
            if a:
                finish(a, "stopped")
            S.update(phase="cancelled", ended=time.time())
        gui_log("Stopped by user.")
    except UpgradeError as e:
        with LOCK:
            a = active_step()
            if a:
                finish(a, "error")
            S.update(phase="error", error=str(e), ended=time.time())
    except Exception as e:  # never leave the UI spinning
        traceback.print_exc()
        with LOCK:
            a = active_step()
            if a:
                finish(a, "error")
            S.update(phase="error", error=f"Unexpected error ({e.__class__.__name__}: {e}).",
                     ended=time.time())
        gui_log(f"ERROR: unexpected {e.__class__.__name__}: {e}")


def do_cancel(_p):
    ops = OPS["ops"]
    if S["phase"] == "running" and ops:
        ops.cancel.set()
        gui_log("Stop requested...")
    return 200, snapshot()


def do_bring_online(_p):
    ops = OPS["ops"]
    if not ops or S["phase"] == "running" or S["busy"]:
        return 409, {"error": "Not available right now."}

    def job():
        with LOCK:
            S["busy"] = "Bringing the cluster online. This can take several minutes."
        try:
            ops.cancel.clear()
            ops.set_online_state("ONLINE", None)
            ok = ops.wait_state("ONLINE", 3600)
            gui_log("Cluster is ONLINE." if ok else "Cluster did not come online - check the admin UI.")
            with LOCK:
                if ok and S["conn"]:
                    S["conn"]["state"] = "ONLINE"
        except (UpgradeError, up.Cancelled):
            pass
        except Exception as e:
            gui_log(f"ERROR: {e.__class__.__name__}: {e}")
        finally:
            with LOCK:
                S["busy"] = None

    threading.Thread(target=job, daemon=True).start()
    return 200, snapshot()


def do_reset(_p):
    with LOCK:
        if S["phase"] == "running" or S["busy"]:
            return 409, {"error": "Something is still running."}
        S.update(fresh_state())
        OPS["ops"] = None
    return 200, snapshot()


def do_ls(qs):
    path = os.path.expanduser((qs.get("path") or [HERE])[0] or HERE)
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        return 400, {"error": f"Not a folder: {path}"}
    entries = []
    try:
        for name in sorted(os.listdir(path), key=str.lower):
            if name.startswith("."):
                continue
            full = os.path.join(path, name)
            try:
                if os.path.isdir(full):
                    entries.append({"name": name, "type": "dir"})
                elif name.lower().endswith(".pak"):
                    entries.append({"name": name, "type": "pak", "size": os.path.getsize(full)})
            except OSError:
                continue
    except PermissionError:
        return 403, {"error": f"No permission to read {path}"}
    return 200, {"path": path, "parent": os.path.dirname(path), "entries": entries}


# ----------------------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "VCFOpsUpgrader"

    def log_message(self, *a):
        pass

    def _host_ok(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else (json.dumps(body) if ctype == "application/json"
                                                     else body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(data)

    def _authed(self):
        return self._host_ok() and secrets.compare_digest(self.headers.get("X-Token", ""), TOKEN)

    def do_GET(self):
        if not self._host_ok():
            return self._send(403, {"error": "forbidden"})
        u = urllib.parse.urlparse(self.path)
        if u.path == "/":
            return self._send(200, PAGE.replace("__TOKEN__", TOKEN).replace("__HERE__", json.dumps(HERE)),
                              "text/html")
        if not self._authed():
            return self._send(403, {"error": "forbidden"})
        if u.path == "/api/state":
            return self._send(200, snapshot())
        if u.path == "/api/log":
            with LOCK:
                txt = "\n".join(S["log"]) + "\n"
            return self._send(200, txt, "text/plain")
        if u.path == "/api/ls":
            code, body = do_ls(urllib.parse.parse_qs(u.query))
            return self._send(code, body)
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._authed():
            return self._send(403, {"error": "forbidden"})
        n = int(self.headers.get("Content-Length") or 0)
        try:
            p = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._send(400, {"error": "bad json"})
        route = {"/api/connect": do_connect, "/api/start": do_start, "/api/cancel": do_cancel,
                 "/api/bring_online": do_bring_online, "/api/reset": do_reset}.get(self.path)
        if not route:
            return self._send(404, {"error": "not found"})
        try:
            code, body = route(p)
        except Exception as e:
            traceback.print_exc()
            code, body = 500, {"error": f"{e.__class__.__name__}: {e}"}
        return self._send(code, body)


def main():
    ap = argparse.ArgumentParser(description="VCF Operations Upgrader - local GUI for PAK upgrades")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="Don't open a browser automatically")
    a = ap.parse_args()
    srv = None
    for port in range(a.port, a.port + 20):
        try:
            srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            continue
    if not srv:
        sys.exit(f"No free port between {a.port} and {a.port + 19}.")
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    print(f"VCF Operations Upgrader running at {url}  (Ctrl-C to quit)", flush=True)
    if not a.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        if S["phase"] == "running":
            print("\nQuitting. Anything already started on the cluster keeps running - check the admin UI.")
        else:
            print("\nBye.")


# ----------------------------------------------------------------------------- page

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>VCF Operations Upgrader</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=Inter:wght@400;500;600&family=Space+Grotesk:wght@500;600;700&display=swap" rel="stylesheet">
<style>
:root{--bg:#0A0D14;--card:#111723;--line:#222B3A;--line2:#1E2633;--text:#E6EAF0;--muted:#93A0B2;--cyan:#4CC9F0;--violet:#8B7CF6;--vio-t:#A99DF8;--green:#3DDC97;--amber:#F5B84C;--red:#FF7A7A}
*{box-sizing:border-box}
body{margin:0;background:radial-gradient(1200px 500px at 85% -10%,rgba(139,124,246,.10),transparent 60%),var(--bg);color:var(--text);font:15px/1.5 Inter,system-ui,sans-serif;min-height:100vh}
a{color:var(--cyan)}a:hover{color:#8EDCF7}
h1,h2{font-family:'Space Grotesk',sans-serif;letter-spacing:-.01em;margin:0}
h1{font-size:28px;font-weight:600}h2{font-size:18px;font-weight:600}
.mono{font-family:'IBM Plex Mono',monospace}
.muted{color:var(--muted)}
header{background:rgba(10,13,20,.85);border-bottom:1px solid var(--line2);padding:14px 24px;display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:12px;position:sticky;top:0;backdrop-filter:blur(6px);z-index:5}
.brand{display:flex;align-items:center;gap:12px}
.logo{width:34px;height:34px;border-radius:10px;display:flex;align-items:center;justify-content:center;background:linear-gradient(135deg,rgba(76,201,240,.25),rgba(139,124,246,.25));border:1px solid #3A4A66;box-shadow:0 0 18px rgba(76,201,240,.25)}
.brand b{font-family:'Space Grotesk',sans-serif;font-size:18px;font-weight:600}
main{max-width:1200px;margin:0 auto;padding:32px 24px 48px;display:flex;flex-direction:column;gap:24px}
.row{display:flex;flex-wrap:wrap;gap:24px;align-items:flex-start}
.card{background:linear-gradient(180deg,rgba(255,255,255,.025),rgba(255,255,255,0) 40%),var(--card);border:1px solid var(--line);border-radius:14px;padding:24px;display:flex;flex-direction:column;gap:16px;min-width:0}
.grow{flex:999 1 560px}.side{flex:1 1 300px}.half{flex:1 1 420px}
label.f{font-size:13px;font-weight:500}
.field{display:flex;flex-direction:column;gap:6px}
input[type=text],input[type=password],input[type=number],select{font:inherit;font-size:14px;padding:10px 12px;min-height:44px;border:1px solid #2E3A4D;border-radius:8px;background:var(--bg);color:var(--text);width:100%}
input:focus,select:focus,button:focus-visible,a:focus-visible{outline:2px solid var(--cyan);outline-offset:2px}
.check{display:flex;align-items:center;gap:10px;font-size:14px;min-height:40px}
.check input{width:18px;height:18px;margin:0;accent-color:var(--cyan)}
.grid2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
.grid3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}
.grid4{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px}
button,.btn{font:inherit;font-weight:500;font-size:14px;min-height:44px;padding:0 18px;border-radius:8px;border:1px solid #2E3A4D;background:transparent;color:var(--text);cursor:pointer;display:inline-flex;align-items:center;justify-content:center;gap:8px;text-decoration:none}
button:hover,.btn:hover{border-color:#44546E}
button.outline{border-color:var(--cyan);color:var(--cyan)}
button.primary{border:0;font-weight:600;font-size:15px;min-height:48px;padding:0 22px;color:var(--bg);background:linear-gradient(90deg,var(--cyan),var(--violet));box-shadow:0 0 28px rgba(76,201,240,.35)}
button:disabled{opacity:.4;cursor:not-allowed;box-shadow:none}
.ok{border:1px solid #1F5C45;background:rgba(61,220,151,.07);border-radius:10px;padding:16px;display:flex;flex-direction:column;gap:12px}
.err{border:1px solid #6B2B2B;background:rgba(255,122,122,.07);color:#FFB3B3;border-radius:10px;padding:12px 14px;font-size:14px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;color:var(--muted);font-weight:500;padding:6px 8px 6px 0}
td{padding:6px 8px 6px 0;border-top:1px solid var(--line2)}
.assist{display:flex;flex-wrap:wrap;gap:14px;align-items:flex-start;padding:16px 18px;border-radius:14px;background:linear-gradient(90deg,rgba(76,201,240,.08),rgba(139,124,246,.10));border:1px solid #2F3B57;box-shadow:0 0 40px rgba(139,124,246,.08)}
.spark{flex:none;width:36px;height:36px;border-radius:10px;display:flex;align-items:center;justify-content:center;background:linear-gradient(135deg,var(--cyan),var(--violet));box-shadow:0 0 18px rgba(139,124,246,.45)}
.assist .lbl{font-size:12px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--vio-t)}
.assist p{margin:4px 0 0;color:#D6DDE6}
.pill{flex:none;display:inline-flex;align-items:center;gap:8px;padding:6px 12px;border-radius:999px;font-size:13px;font-weight:500}
.pill.amber{background:rgba(245,184,76,.10);border:1px solid #5A4520;color:#F5C46A}
.dot{width:8px;height:8px;border-radius:4px;display:inline-block}
ol.steps{list-style:none;margin:0;padding:16px 20px;background:var(--card);border:1px solid var(--line);border-radius:14px;display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:12px}
ol.steps li{display:flex;flex-direction:column;gap:6px;min-width:0}
.bar{height:4px;border-radius:2px;background:#273142}
.st-done .bar{background:var(--green)}.st-skipped .bar{background:#3A4456}
.st-active .bar{background:linear-gradient(90deg,var(--cyan),var(--violet));box-shadow:0 0 10px rgba(76,201,240,.5);animation:pulse 1.6s ease-in-out infinite}
.st-error .bar{background:var(--red)}.st-stopped .bar{background:var(--amber)}
.steps .t{font-size:13px;font-weight:600}.steps .d{font-size:12px;color:var(--muted)}
.st-pending .t{color:var(--muted);font-weight:500}.st-active .t,.st-active .d{color:var(--cyan)}
@keyframes pulse{50%{opacity:.55}}
.prog{height:14px;border-radius:7px;background:#1C2433;overflow:hidden}
.prog>div{height:100%;border-radius:7px;background:linear-gradient(90deg,var(--cyan),var(--violet));box-shadow:0 0 16px rgba(76,201,240,.6);transition:width .8s}
.prog.ind>div{width:100%;background:repeating-linear-gradient(135deg,var(--cyan) 0 16px,var(--violet) 16px 32px);background-size:45px 45px;animation:slide 1.2s linear infinite}
@keyframes slide{to{background-position:45px 0}}
.stat .k{font-size:12px;color:var(--muted)}.stat .v{font-size:20px;font-weight:600}.stat .s{font-size:12px;color:var(--muted)}
.log{background:#070A10;border:1px solid var(--line2);border-radius:12px;overflow:hidden}
.log .hd{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:10px 16px;border-bottom:1px solid #1A2230}
.log pre{margin:0;padding:14px 16px;font:12.5px/1.7 'IBM Plex Mono',monospace;color:#B8C4D3;white-space:pre-wrap;max-height:320px;overflow:auto}
.foot{display:flex;flex-wrap:wrap;align-items:center;justify-content:flex-end;gap:12px}
.drop{display:flex;align-items:center;gap:12px;border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.seg{display:flex;gap:4px;padding:4px;border:1px solid var(--line);border-radius:10px;background:var(--bg)}
.seg button{flex:1;border:0;min-height:38px;font-size:13px}
.seg button[aria-pressed=true]{background:#1C2433;color:var(--text);box-shadow:inset 0 0 0 1px #3A4A66}
.hero{display:flex;flex-direction:column;align-items:center;text-align:center;gap:12px}
.vers{display:flex;flex-wrap:wrap;justify-content:center;align-items:stretch;gap:16px}
.ver{flex:1 1 260px;max-width:380px;padding:20px 24px;border-radius:14px;background:var(--card);border:1px solid var(--line)}
.ver .n{font-family:'Space Grotesk',sans-serif;font-size:34px;font-weight:600;overflow-wrap:anywhere}
dialog{background:var(--card);color:var(--text);border:1px solid var(--line);border-radius:14px;padding:0;width:min(640px,92vw)}
dialog::backdrop{background:rgba(0,0,0,.6)}
.fl{max-height:360px;overflow:auto;border-top:1px solid var(--line2)}
.fl button{width:100%;justify-content:flex-start;border:0;border-bottom:1px solid var(--line2);border-radius:0;min-height:44px;font-weight:400}
.fl button:hover{background:#151C2A}
.hidden{display:none!important}
@media (max-width:720px){.grid4,.grid3{grid-template-columns:repeat(2,minmax(0,1fr))}ol.steps{grid-template-columns:repeat(3,minmax(0,1fr))}}
</style>
</head>
<body>
<header>
  <div class="brand">
    <span class="logo"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#4CC9F0" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3v12"/><path d="M7 10l5 5 5-5"/><path d="M4 20h16"/></svg></span>
    <b>VCF Operations Upgrader</b>
  </div>
  <span id="who" class="muted mono" style="font-size:13px">Not connected</span>
</header>

<main>
  <section class="assist" aria-live="polite">
    <span class="spark"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#0A0D14" stroke-width="1.8" stroke-linejoin="round" aria-hidden="true"><path d="M11 3l1.9 5.6L18.5 10.5l-5.6 1.9L11 18l-1.9-5.6L3.5 10.5l5.6-1.9z"/><path d="M18.5 15l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z"/></svg></span>
    <div style="flex:1 1 420px;min-width:0"><div class="lbl">Assistant</div><p id="assist">Loading…</p></div>
    <span id="pill" class="pill amber hidden"><span class="dot" style="background:var(--amber);box-shadow:0 0 10px var(--amber)"></span><span id="pilltxt"></span></span>
  </section>

  <!-- ============ SETUP ============ -->
  <div id="v-setup">
    <div class="row">
      <section class="card half">
        <h2>1. Target cluster</h2>
        <div class="field"><label class="f" for="host">Primary node or VIP</label><input id="host" type="text" class="mono" placeholder="ops01.example.com" autocomplete="off"></div>
        <div class="grid2">
          <div class="field"><label class="f" for="user">Username</label><input id="user" type="text" value="admin" autocomplete="username"></div>
          <div class="field"><label class="f" for="pw">Password</label>
            <div style="position:relative"><input id="pw" type="password" autocomplete="current-password" style="padding-right:52px">
              <button type="button" id="pw-eye" aria-label="Show password" aria-pressed="false" title="Show password" style="position:absolute;right:2px;top:50%;transform:translateY(-50%);width:44px;min-height:40px;padding:0;border:0;background:transparent;color:var(--muted)">
                <svg id="eye-on" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>
                <svg id="eye-off" class="hidden" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 3l18 18"/><path d="M10.6 5.1A10.8 10.8 0 0 1 12 5c6.4 0 10 7 10 7a17.6 17.6 0 0 1-3.2 4.1M6.6 6.6C3.7 8.4 2 12 2 12s3.6 7 10 7a9.8 9.8 0 0 0 5.4-1.6"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/></svg>
              </button></div></div>
        </div>
        <label class="check"><input id="insecure" type="checkbox" checked>Skip TLS certificate check (self-signed cert)</label>
        <details><summary class="muted" style="cursor:pointer;font-size:14px">CA bundle (instead of skipping the check)</summary>
          <div class="field" style="margin-top:8px"><label class="f" for="ca">Path to CA bundle (.pem)</label><input id="ca" type="text" class="mono"></div></details>
        <div style="display:flex;flex-wrap:wrap;gap:12px;align-items:center">
          <button id="btn-connect" class="outline" type="button">Test connection</button>
          <span class="muted" style="font-size:13px">Read-only. Nothing changes on the cluster.</span>
        </div>
        <div id="conn-err" class="err hidden" role="alert"></div>
        <div id="conn-ok" class="ok hidden"></div>
      </section>

      <section class="card half">
        <h2>2. What to do</h2>
        <div class="seg" role="group" aria-label="Mode">
          <button type="button" id="m-up" aria-pressed="true">Upgrade with a PAK</button>
          <button type="button" id="m-mon" aria-pressed="false">Monitor an install already running</button>
        </div>
        <div id="up-opts" style="display:flex;flex-direction:column;gap:14px">
          <div class="field"><label class="f" for="pak">PAK file on this computer</label>
            <div style="display:flex;gap:8px"><input id="pak" type="text" class="mono" readonly placeholder="No file chosen" aria-describedby="pak-hint" style="cursor:pointer"><button type="button" id="btn-browse">Browse…</button></div>
            <span id="pak-hint" class="muted" style="font-size:13px">Choose the upgrade .pak with Browse.</span></div>
          <fieldset style="border:0;padding:0;margin:0;display:flex;flex-direction:column;gap:2px">
            <legend class="f" style="font-size:13px;font-weight:500;padding:0;margin-bottom:4px">Options</legend>
            <label class="check"><input id="clobber" type="checkbox">Overwrite a previously uploaded copy</label>
            <label class="check"><input id="bring" type="checkbox" checked>Bring the cluster online if the upgrade leaves it offline</label>
          </fieldset>
        </div>
        <div id="mon-opts" class="hidden field"><label class="f" for="pakid">PAK ID of the running install</label><input id="pakid" type="text" class="mono" placeholder="vRealizeOperationsManagerEnterprise-…"><span class="muted" style="font-size:13px">Shown after the upload finished, or on the admin UI's Software Update page.</span></div>
        <details><summary class="muted" style="cursor:pointer;font-size:14px">Advanced: timeouts and heartbeat</summary>
          <div class="grid2" style="margin-top:10px">
            <div class="field"><label class="f" for="t-off">Offline timeout (min)</label><input id="t-off" type="number" min="1" value="60"></div>
            <div class="field"><label class="f" for="t-inst">Install timeout (min)</label><input id="t-inst" type="number" min="1" value="240"></div>
            <div class="field"><label class="f" for="t-on">Back-online timeout (min)</label><input id="t-on" type="number" min="1" value="120"></div>
            <div class="field"><label class="f" for="hb">Log heartbeat</label><select id="hb"><option value="60">Every minute</option><option value="300" selected>Every 5 minutes</option><option value="0">Off</option></select></div>
          </div></details>
      </section>
    </div>

    <section class="card" style="flex-direction:row;flex-wrap:wrap;align-items:center;justify-content:space-between;margin-top:24px">
      <div style="flex:1 1 420px;min-width:0;display:flex;flex-direction:column;gap:4px">
        <div id="warn-line" style="display:flex;align-items:center;gap:8px;color:var(--amber);font-weight:600;font-size:14px"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3l10 18H2z"/><path d="M12 10v5"/><path d="M12 18h.01"/></svg>The cluster goes offline for the whole upgrade</div>
        <label class="check" id="snap-row"><input id="snap" type="checkbox">I've taken snapshots of every node</label>
        <div id="start-err" class="err hidden" role="alert"></div>
      </div>
      <button id="btn-start" class="primary" type="button" disabled>Take offline &amp; upgrade</button>
    </section>
  </div>

  <!-- ============ RUN ============ -->
  <div id="v-run" class="hidden" style="display:flex;flex-direction:column;gap:24px">
    <div style="display:flex;flex-wrap:wrap;align-items:flex-end;justify-content:space-between;gap:12px">
      <div><h1 id="run-title">Upgrading</h1><p id="run-sub" class="muted" style="margin:4px 0 0"></p></div>
      <div style="text-align:right"><div class="muted" style="font-size:12px">Elapsed</div><div id="elapsed" class="mono" style="font-size:20px;font-weight:500">0m00s</div></div>
    </div>
    <ol id="steps" class="steps" aria-label="Upgrade steps"></ol>
    <div class="row">
      <section class="card grow" id="main-card"></section>
      <section class="card side">
        <h2>Cluster</h2>
        <div style="display:flex;align-items:center;gap:8px"><span id="cl-dot" class="dot" style="width:10px;height:10px;border-radius:5px"></span><b id="cl-state">-</b></div>
        <table><thead><tr><th>Node</th><th>State</th></tr></thead><tbody id="nodes"></tbody></table>
      </section>
    </div>
  </div>

  <!-- ============ DONE ============ -->
  <div id="v-done" class="hidden" style="display:flex;flex-direction:column;gap:24px">
    <section class="hero">
      <span style="width:64px;height:64px;border-radius:32px;display:flex;align-items:center;justify-content:center;background:rgba(61,220,151,.12);border:1px solid #2A7A5A;box-shadow:0 0 40px rgba(61,220,151,.35)"><svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#3DDC97" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12l5 5 9-10"/></svg></span>
      <h1 style="font-size:34px">Upgrade complete</h1>
      <p class="muted" style="margin:0;font-size:16px">The cluster is back online on the new version.</p>
    </section>
    <section class="vers" aria-label="Version change">
      <div class="ver"><div id="d-before-lbl" class="muted" style="font-size:12px;font-weight:600;letter-spacing:.08em;text-transform:uppercase">Before</div><div id="d-before" style="color:var(--muted)"></div></div>
      <div style="display:flex;align-items:center"><svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#8B7CF6" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14"/><path d="M13 6l6 6-6 6"/></svg></div>
      <div class="ver" style="background:linear-gradient(135deg,rgba(76,201,240,.10),rgba(139,124,246,.12)),var(--card);border-color:#3A4A66;box-shadow:0 0 36px rgba(76,201,240,.18)"><div style="font-size:12px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--vio-t)">Now running</div><div id="d-after"></div></div>
    </section>
    <section class="card"><div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap"><h2>Timeline</h2><span class="muted" style="font-size:13px">Total <span id="d-total" class="mono" style="color:var(--text)"></span></span></div><ol id="timeline" style="list-style:none;margin:0;padding:0"></ol></section>
  </div>

  <section class="log hidden" id="logbox">
    <div class="hd"><h2 style="font-size:14px;font-weight:500;color:#E6EBF0;font-family:Inter,sans-serif">Activity log</h2><a href="#" id="save-log" style="font-size:13px">Save log</a></div>
    <pre id="log"></pre>
  </section>

  <div class="foot" id="foot"></div>
</main>

<dialog id="picker" aria-labelledby="pk-title">
  <div style="padding:18px 20px;display:flex;flex-direction:column;gap:10px">
    <h2 id="pk-title">Choose a PAK file</h2>
    <div style="display:flex;flex-wrap:wrap;gap:8px" role="group" aria-label="Go to folder">
      <button type="button" class="pk-go" data-go="~">Home</button>
      <button type="button" class="pk-go" data-go="~/Downloads">Downloads</button>
      <button type="button" class="pk-go" data-go="~/Desktop">Desktop</button>
      <button type="button" class="pk-go" data-go="__app__">App folder</button>
    </div>
    <div id="pk-path" class="mono muted" style="font-size:12px;overflow-wrap:anywhere"></div>
  </div>
  <div id="pk-list" class="fl"></div>
  <div style="padding:12px 20px;display:flex;justify-content:flex-end"><button type="button" id="pk-close">Cancel</button></div>
</dialog>

<script>
const TOKEN = "__TOKEN__", HERE = __HERE__;
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const dur = s => { s = Math.max(0, Math.floor(s||0)); const h=Math.floor(s/3600), m=Math.floor(s%3600/60), x=s%60; return h ? `${h}h${String(m).padStart(2,"0")}m${String(x).padStart(2,"0")}s` : `${m}m${String(x).padStart(2,"0")}s`; };
const gib = b => (b/1073741824).toFixed(2);
async function api(path, body) {
  const r = await fetch(path, body === undefined ? {headers:{"X-Token":TOKEN}} :
    {method:"POST", headers:{"X-Token":TOKEN,"Content-Type":"application/json"}, body:JSON.stringify(body)});
  let j = {}; try { j = await r.json(); } catch (e) {}
  if (!r.ok) throw new Error(j.error || `HTTP ${r.status}`);
  return j;
}

let mode = "up", S = null, lastLogLen = -1, offset = 0;

function setMode(m) {
  mode = m;
  $("m-up").setAttribute("aria-pressed", m === "up"); $("m-mon").setAttribute("aria-pressed", m === "mon");
  $("up-opts").classList.toggle("hidden", m !== "up"); $("mon-opts").classList.toggle("hidden", m !== "mon");
  $("snap-row").classList.toggle("hidden", m !== "up"); $("warn-line").classList.toggle("hidden", m !== "up");
  $("btn-start").innerHTML = m === "up" ? "Take offline &amp; upgrade" : "Start monitoring";
  updateStart();
}
$("pw-eye").onclick = () => {
  const show = $("pw").type === "password";
  $("pw").type = show ? "text" : "password";
  const b = $("pw-eye"); b.setAttribute("aria-pressed", show); b.setAttribute("aria-label", show ? "Hide password" : "Show password"); b.title = b.getAttribute("aria-label");
  $("eye-on").classList.toggle("hidden", show); $("eye-off").classList.toggle("hidden", !show);
};
$("m-up").onclick = () => setMode("up"); $("m-mon").onclick = () => setMode("mon");

function updateStart() {
  const ready = S && S.phase === "ready";
  const ok = mode === "up" ? (ready && S.conn.reachable && $("pak").value.trim() && $("snap").checked) : (ready && $("pakid").value.trim());
  $("btn-start").disabled = !ok;
}
["pak","pakid","snap"].forEach(id => $(id).addEventListener("input", updateStart));
$("snap").addEventListener("change", updateStart);

$("btn-connect").onclick = async () => {
  const b = $("btn-connect"); b.disabled = true; b.textContent = "Connecting…";
  $("conn-err").classList.add("hidden"); $("conn-ok").classList.add("hidden");
  try {
    S = await api("/api/connect", {host:$("host").value, user:$("user").value, password:$("pw").value,
      insecure:$("insecure").checked, ca_bundle:$("ca").value, monitor: mode === "mon"});
    render();
  } catch (e) { $("conn-err").textContent = e.message; $("conn-err").classList.remove("hidden"); }
  b.disabled = false; b.textContent = "Test connection";
};

$("btn-start").onclick = async () => {
  $("start-err").classList.add("hidden");
  const msg = mode === "up"
    ? `Take ${S.conn.host} OFFLINE and install\n${$("pak").value.split("/").pop()}?`
    : `Watch the install of ${$("pakid").value} on ${S.conn.host}? Nothing will be changed.`;
  if (!confirm(msg)) return;
  try {
    S = await api("/api/start", {monitor: mode === "mon", pak:$("pak").value, pak_id:$("pakid").value,
      clobber:$("clobber").checked, bring_online:$("bring").checked, heartbeat:$("hb").value,
      t_offline:$("t-off").value, t_install:$("t-inst").value, t_online:$("t-on").value});
    render();
  } catch (e) { $("start-err").textContent = e.message; $("start-err").classList.remove("hidden"); }
};

// ---- file picker
async function openDir(path) {
  try {
    const d = await api("/api/ls?path=" + encodeURIComponent(path));
    $("pk-path").textContent = d.path;
    const items = [`<button type="button" data-dir="${esc(d.parent)}">⟵&nbsp; Up one folder</button>`];
    d.entries.forEach(e => items.push(e.type === "dir"
      ? `<button type="button" data-dir="${esc(d.path + "/" + e.name)}"><span class="muted">▸</span>&nbsp; ${esc(e.name)}</button>`
      : `<button type="button" data-pak="${esc(d.path + "/" + e.name)}" data-size="${gib(e.size)}"><span style="color:var(--cyan)">◆</span>&nbsp; <span class="mono" style="font-size:13px">${esc(e.name)}</span><span class="muted" style="margin-left:auto">${gib(e.size)} GiB</span></button>`));
    if (!d.entries.some(e => e.type === "pak")) items.push(`<div class="muted" style="padding:12px 20px;font-size:13px">No .pak files in this folder.</div>`);
    $("pk-list").innerHTML = items.join("");
  } catch (e) { $("pk-list").innerHTML = `<div class="err" style="margin:12px 20px">${esc(e.message)}</div>`; }
}
$("pk-list").onclick = ev => {
  const b = ev.target.closest("button"); if (!b) return;
  if (b.dataset.dir) openDir(b.dataset.dir);
  if (b.dataset.pak) { $("pak").value = b.dataset.pak; $("pak-hint").textContent = b.dataset.size ? `Selected · ${b.dataset.size} GiB` : "Selected"; $("picker").close(); updateStart(); }
};
$("btn-browse").onclick = () => { const v = $("pak").value.trim(); openDir(v ? v.replace(/\/[^/]*$/, "") || HERE : HERE); $("picker").showModal(); };
$("pk-close").onclick = () => $("picker").close();
$("pak").onclick = () => $("btn-browse").click();
document.querySelectorAll(".pk-go").forEach(b => b.onclick = () => openDir(b.dataset.go === "__app__" ? HERE : b.dataset.go));

$("save-log").onclick = async e => {
  e.preventDefault();
  const r = await fetch("/api/log", {headers:{"X-Token":TOKEN}}); const t = await r.text();
  const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([t], {type:"text/plain"}));
  a.download = `vcf-ops-upgrader-${new Date().toISOString().slice(0,19).replace(/[:T]/g,"-")}.log`; a.click();
};

// ---- rendering
function nodesRows(nodes) {
  const ks = Object.keys(nodes || {});
  if (!ks.length) return `<tr><td colspan="2" class="muted">Not reported</td></tr>`;
  return ks.sort().map(k => `<tr><td class="mono">${esc(k)}</td><td>${esc(nodes[k])}</td></tr>`).join("");
}
function stateColor(st) { st = (st||"").toUpperCase();
  return st === "ONLINE" ? "var(--green)" : st.startsWith("UNREACH") || st === "UNKNOWN" ? "var(--amber)" : "#7D8AA0"; }
const nice = s => { s = String(s||"-"); return s.charAt(0) + s.slice(1).toLowerCase().replace(/_/g," "); };

function renderConnOk() {
  const c = S.conn;
  if (!c) { $("conn-ok").classList.add("hidden"); return; }
  $("conn-ok").innerHTML = c.reachable ? `
    <div style="display:flex;align-items:center;gap:8px;color:var(--green);font-weight:600"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12l5 5 9-10"/></svg>Connected</div>
    <div class="grid2" style="font-size:14px"><div><div class="muted" style="font-size:12px">Cluster state</div><b>${esc(nice(c.state))}</b></div><div><div class="muted" style="font-size:12px">Current version</div><b>${esc(c.version)}</b></div></div>
    <table><thead><tr><th>Node</th><th>State</th></tr></thead><tbody>${nodesRows(c.nodes)}</tbody></table>`
    : `<div style="color:var(--amber);font-weight:600">Not reachable yet. Monitoring will keep trying.</div>`;
  $("conn-ok").classList.remove("hidden");
}

function renderSteps() {
  $("steps").innerHTML = S.steps.map(s => {
    let d = {pending:"Waiting", done:"Done", skipped:"Skipped", error:"Failed", stopped:"Stopped", active:"In progress"}[s.status];
    if (s.status === "done" && s.start && s.end && s.id !== "connect") d += " · " + dur(s.end - s.start);
    if (s.status === "active" && s.id === "upload" && S.upload) d = S.upload.verifying ? "Checking PAK" : Math.floor(S.upload.pct) + "%";
    return `<li class="st-${s.status}" ${s.status === "active" ? 'aria-current="step"' : ""}><div class="bar"></div><span class="t">${esc(s.label)}</span><span class="d">${esc(d)}</span></li>`;
  }).join("");
}

function renderMain() {
  const a = (S.steps.find(s => s.status === "active") || {}).id, mc = $("main-card");
  if (S.phase === "error" || S.phase === "cancelled") {
    mc.innerHTML = `<h2>${S.phase === "error" ? "Stopped with an error" : "Stopped"}</h2>
      ${S.error ? `<div class="err">${esc(S.error)}</div>` : ""}
      ${S.pak_id ? `<div class="muted" style="font-size:14px">PAK ID <span class="mono" style="color:var(--text)">${esc(S.pak_id)}</span></div>` : ""}
      <div class="muted" style="font-size:14px">Check the cluster's admin UI before doing anything else.</div>`; return;
  }
  if (a === "upload" && S.upload) {
    const u = S.upload;
    mc.innerHTML = `<div style="display:flex;flex-wrap:wrap;justify-content:space-between;align-items:baseline;gap:8px"><h2>${u.verifying ? "Server is checking the PAK" : "Uploading PAK"}</h2><span class="mono muted" style="font-size:13px;overflow-wrap:anywhere">${esc(S.pak_name)}</span></div>
      <div class="prog ${u.verifying ? "ind" : ""}" role="progressbar" aria-label="Upload progress" aria-valuenow="${Math.floor(u.pct)}" aria-valuemin="0" aria-valuemax="100"><div style="width:${u.pct.toFixed(1)}%"></div></div>
      <div class="grid4">
        <div class="stat"><div class="k">Sent</div><div class="v">${Math.floor(u.pct)}%</div><div class="s">${gib(u.sent)} of ${gib(u.total)} GiB</div></div>
        <div class="stat"><div class="k">Speed</div><div class="v">${u.rate.toFixed(1)} MiB/s</div></div>
        <div class="stat"><div class="k">Time left</div><div class="v">${u.verifying ? "-" : dur(u.eta)}</div></div>
        <div class="stat"><div class="k">Then</div><div class="v" style="font-size:15px;font-weight:500">Server checks the PAK</div></div></div>`; return;
  }
  if (a === "install") {
    const ist = S.install_status || "Starting";
    mc.innerHTML = `<div style="display:flex;flex-wrap:wrap;justify-content:space-between;align-items:baseline;gap:8px"><h2>Installing</h2><span class="mono muted" style="font-size:13px;overflow-wrap:anywhere">${esc(S.pak_id || "")}</span></div>
      <div class="prog ind" role="progressbar" aria-label="Install in progress"><div></div></div>
      <div class="grid3">
        <div class="stat"><div class="k">Install status</div><div class="v" style="font-size:18px">${esc(nice(ist))}</div><div class="s">${ist === "UNREACHABLE" ? "nodes restarting" : "latest answer"}</div></div>
        <div class="stat"><div class="k">Install time</div><div class="v" style="font-size:18px">${dur(S.now - (S.steps.find(s=>s.id==="install").start||S.now))}</div></div>
        <div class="stat"><div class="k">Gives up after</div><div class="v" style="font-size:18px">${dur(($("t-inst").value||240)*60)}</div></div></div>`; return;
  }
  const titles = {connect:"Connecting", offline:"Taking the cluster offline", online:"Waiting for the cluster to come back online", verify:"Reading the new version"};
  mc.innerHTML = `<h2>${titles[a] || "Working"}</h2><div class="prog ind"><div></div></div>
    <div class="muted" style="font-size:14px">Cluster state: <b style="color:var(--text)">${esc(nice(S.cluster_state))}</b></div>`;
}

function renderFoot() {
  const f = $("foot"), a = (S.steps.find(s => s.status === "active") || {}).id;
  if (S.phase === "running") {
    f.innerHTML = a === "upload"
      ? `<span class="muted" style="font-size:13px">Cancelling stops the upload. The cluster stays offline until you bring it back online.</span><button type="button" id="b-cancel">Cancel upload</button>`
      : `<span class="muted" style="font-size:13px">${a === "install" || a === "online" ? "The install runs on the cluster. Stopping here only stops watching." : "Stopping ends this run after the current step."}</span><button type="button" id="b-cancel">Stop watching</button>`;
    $("b-cancel").onclick = async () => { if (confirm(a === "upload" ? "Cancel the upload?" : "Stop watching?")) S = await api("/api/cancel", {}); };
  } else if (S.phase === "error" || S.phase === "cancelled") {
    f.innerHTML = `<button type="button" id="b-online" ${S.busy ? "disabled" : ""}>Bring cluster online</button><button type="button" class="outline" id="b-reset">Back to setup</button>`;
    $("b-online").onclick = async () => { if (confirm("Ask the cluster to go ONLINE?")) S = await api("/api/bring_online", {}); render(); };
    $("b-reset").onclick = async () => { S = await api("/api/reset", {}); lastLogLen = -1; render(); };
  } else if (S.phase === "done") {
    f.innerHTML = `<a class="btn" target="_blank" rel="noopener" href="https://${esc(S.conn.host)}/admin">Open admin UI</a><button type="button" class="primary" id="b-again">Start another upgrade</button>`;
    $("b-again").onclick = async () => { S = await api("/api/reset", {}); lastLogLen = -1; render(); };
  } else f.innerHTML = "";
}

function verHTML(v) { // "VMware Aria Operations 8.18.7 (build 25423537)" -> big number, small name/build
  const m = String(v || "-").match(/^(.*?)\s*(\d+(?:\.\d+)+)\s*(\(.*\))?\s*$/);
  if (!m) return `<div class="n">${esc(v || "-")}</div>`;
  return `<div class="n">${esc(m[2])}</div><div class="mono" style="font-size:13px;color:#B8C4D3">${esc([m[1], m[3]].filter(Boolean).join(" "))}</div>`;
}
function renderDone() {
  $("d-before-lbl").textContent = S.pak_name ? "Before" : "When monitoring started";
  $("d-before").innerHTML = verHTML(S.before); $("d-after").innerHTML = verHTML(S.after);
  $("d-total").textContent = dur((S.ended || S.now) - S.started);
  const label = {connect:"Connected", offline:"Took cluster offline", upload:"Uploaded PAK", install:"Installed", online:"Cluster back online", verify:"Version confirmed"};
  $("timeline").innerHTML = S.steps.filter(s => s.id !== "connect").map(s => `<li style="display:flex;justify-content:space-between;gap:12px;padding:10px 0;border-top:1px solid var(--line2)"><span style="display:flex;align-items:center;gap:10px"><span class="dot" style="background:${s.status === "done" ? "var(--green)" : "#3A4456"}"></span>${esc(label[s.id])}${s.detail && s.id !== "verify" ? ` <span class="muted">(${esc(s.detail)})</span>` : ""}</span><span class="mono" style="color:#B8C4D3">${s.status === "skipped" ? "skipped" : dur((s.end||0) - (s.start||0))}</span></li>`).join("");
}

function render() {
  if (!S) return;
  const ph = S.phase, inRun = ["running","error","cancelled"].includes(ph);
  $("who").textContent = S.conn ? `${S.conn.user}@${S.conn.host}` : "Not connected";
  $("assist").textContent = S.assistant;
  const reconnect = ph === "running" && (S.install_status === "UNREACHABLE" || String(S.cluster_state).startsWith("unreachable"));
  $("pill").classList.toggle("hidden", !reconnect); $("pilltxt").textContent = "Reconnecting · checking every 30s";
  $("v-setup").classList.toggle("hidden", !(ph === "setup" || ph === "ready"));
  $("v-run").classList.toggle("hidden", !inRun); $("v-done").classList.toggle("hidden", ph !== "done");
  $("logbox").classList.toggle("hidden", !(S.log && S.log.length));
  if (ph === "setup" || ph === "ready") { renderConnOk(); updateStart(); }
  if (inRun) {
    $("run-title").textContent = S.pak_name ? "Upgrading " + S.conn.host : "Watching install on " + S.conn.host;
    $("run-sub").textContent = "From " + (S.before || "?") + (S.pak_name ? " · " + S.pak_name : " · PAK ID " + (S.pak_id || ""));
    $("elapsed").textContent = dur((S.ended || S.now) - S.started);
    renderSteps(); renderMain();
    $("cl-state").textContent = nice(S.cluster_state); $("cl-dot").style.background = stateColor(S.cluster_state);
    $("nodes").innerHTML = nodesRows(S.nodes);
  }
  if (ph === "done") renderDone();
  renderFoot();
  if (S.log.length !== lastLogLen || S.log[S.log.length-1] !== $("log").dataset.last) {
    const pre = $("log"), atBottom = pre.scrollHeight - pre.scrollTop - pre.clientHeight < 40;
    pre.textContent = S.log.join("\n"); pre.dataset.last = S.log[S.log.length-1] || "";
    if (atBottom || lastLogLen < 0) pre.scrollTop = pre.scrollHeight;
    lastLogLen = S.log.length;
  }
}

async function poll() {
  try { S = await api("/api/state"); render(); }
  catch (e) { $("assist").textContent = "Lost contact with the GUI server. Is the terminal window still open?"; }
  setTimeout(poll, 1000);
}
poll();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
