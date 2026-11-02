#!/usr/bin/env python3
"""
vcfops_pak_upgrade.py - Upgrade a VCF Operations (Aria Operations / vROps) cluster
using the CASA (Cluster and Slice Administration) API.

No third-party packages required - uses only the Python 3 standard library.

Workflow
  1. Record the current version.
  2. Take the cluster OFFLINE   POST /casa/sysadmin/cluster/online_state
  3. Upload the PAK             POST /casa/upgrade/cluster/pak/reserved/operation/upload
  4. Install the PAK            POST /casa/upgrade/cluster/pak/{pak_id}/operation/install
  5. Poll install status        GET  /casa/upgrade/cluster/pak/{pak_id}/status
     (nodes restart during this step, so connection errors are expected and tolerated)
  6. Wait for cluster ONLINE    GET  /casa/sysadmin/cluster/online_state
     (optionally bring it online if the upgrade leaves it offline)
  7. Report the new version     GET  /suite-api/api/versions/current

Progress output: state changes with elapsed time, per-node status, upload %/speed/ETA,
and a heartbeat line every --heartbeat seconds during quiet stretches.

Run it against the PRIMARY node (or the cluster VIP).

You are prompted for the admin password (input hidden); it is verified before
anything changes, with up to 3 attempts.

Example
  python3 vcfops_pak_upgrade.py --host ops01.lab.local --user admin \
      --pak ./vRealize_Operations_Manager_With_CP-9.0.x-to-9.0.y.pak --insecure
"""

import argparse
import base64
import getpass
import http.client
import io
import json
import os
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

# Errors that mean "couldn't talk to the server" (node restarting, timeout, TLS, DNS...)
NET_ERRORS = (urllib.error.URLError, http.client.HTTPException, OSError)


# ----------------------------------------------------------------------------- helpers

def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def die(msg, code=1):
    log(f"ERROR: {msg}")
    sys.exit(code)


def fmt_dur(seconds):
    seconds = int(max(seconds, 0))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s"


class Heartbeat:
    """Background thread that logs '<label> still running (elapsed X)' every N seconds.

    Used around long blocking calls (upload, synchronous offline/online requests)
    where the main thread can't print anything itself.
    """

    def __init__(self, label, every, extra=None):
        self.label, self.every, self.extra = label, every, extra
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)
        self.start = time.time()

    def _run(self):
        while not self._stop.wait(self.every):
            more = ""
            if self.extra:
                try:
                    more = f" - {self.extra()}"
                except Exception:  # never let the heartbeat kill the run
                    pass
            log(f"  ... {self.label} still running (elapsed {fmt_dur(time.time() - self.start)}){more}")

    def __enter__(self):
        if self.every > 0:
            self._t.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()


NODE_KEYS = ("slice_address", "node_address", "slice_name", "node_name", "hostname", "address", "ip_address")
NODE_STATE_KEYS = ("pak_state", "slice_pak_install_status", "node_pak_install_status",
                   "slice_online_state", "online_state", "install_status", "status", "state")


def node_states(obj, out=None):
    """Collect {node: state} from any nested dicts that look like per-node entries."""
    if out is None:
        out = {}
    if isinstance(obj, dict):
        # 8.x online_state layout groups nodes by state:
        #   {"slice_online_state": "ONLINE", "sliceAddresses": ["10.0.0.11", ...]}
        addrs = obj.get("sliceAddresses") or obj.get("slice_addresses")
        grp_state = next((obj[k] for k in NODE_STATE_KEYS if isinstance(obj.get(k), str) and obj[k]), None)
        if isinstance(addrs, list) and grp_state:
            for addr in addrs:
                if isinstance(addr, str) and addr:
                    out[addr] = grp_state
        node = next((obj[k] for k in NODE_KEYS if isinstance(obj.get(k), str) and obj[k]), None)
        state = next((obj[k] for k in NODE_STATE_KEYS if isinstance(obj.get(k), str) and obj[k]), None)
        if node and state:
            step = obj.get("pak_step") or obj.get("current_step") or obj.get("message")
            out[node] = f"{state}" + (f" ({step})" if isinstance(step, str) and step else "")
        for v in obj.values():
            node_states(v, out)
    elif isinstance(obj, list):
        for v in obj:
            node_states(v, out)
    return out


def find_key(obj, *names):
    """Depth-first search for the first matching key in nested JSON."""
    if isinstance(obj, dict):
        for n in names:
            if n in obj and not isinstance(obj[n], (dict, list)):
                return obj[n]
        for v in obj.values():
            r = find_key(v, *names)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = find_key(v, *names)
            if r is not None:
                return r
    return None


class Cancelled(Exception):
    """Raised when a caller (e.g. the GUI) asks a running operation to stop."""


class AuthError(RuntimeError):
    """Raised when the CASA API rejects the credentials (HTTP 401/403)."""


class Response:
    def __init__(self, status, text):
        self.status, self.text = status, text

    @property
    def ok(self):
        return 200 <= self.status < 300

    def json(self):
        return json.loads(self.text)  # raises ValueError on non-JSON


class MultipartFile:
    """File-like multipart/form-data body that streams the PAK from disk.

    http.client reads it in small blocks, so the file is never loaded into memory,
    and each read updates the byte counter used for progress reporting.
    """

    def __init__(self, path, field, extra_fields=None, cancel=None):
        self.cancel = cancel
        self.boundary = uuid.uuid4().hex
        fname = os.path.basename(path).replace('"', "")
        pre = b""
        for k, v in (extra_fields or {}).items():
            pre += (f"--{self.boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n"
                    f"{v}\r\n").encode()
        pre += (f"--{self.boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; "
                f"filename=\"{fname}\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode()
        post = f"\r\n--{self.boundary}--\r\n".encode()
        self._parts = [io.BytesIO(pre), open(path, "rb"), io.BytesIO(post)]
        self.len = len(pre) + os.path.getsize(path) + len(post)
        self.bytes_read = 0
        self.content_type = f"multipart/form-data; boundary={self.boundary}"

    def read(self, n=-1):
        if self.cancel is not None and self.cancel.is_set():
            raise Cancelled("upload cancelled")
        out = b""
        while self._parts and (n is None or n < 0 or len(out) < n):
            want = -1 if (n is None or n < 0) else n - len(out)
            chunk = self._parts[0].read(want)
            if not chunk:
                self._parts.pop(0).close()
                continue
            out += chunk
        self.bytes_read += len(out)
        return out

    def close(self):
        for p in self._parts:
            p.close()
        self._parts = []


class Ops:
    def __init__(self, host, user, password, ssl_ctx, timeout=60, debug=False, heartbeat=300):
        self.base = f"https://{host}"
        self.user, self.password = user, password
        self.basic = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
        self.opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl_ctx))
        self.timeout = timeout
        self.debug = debug
        self.heartbeat = heartbeat
        self._last_nodes = {}
        # live progress, read by the GUI (the CLI ignores these)
        self.cancel = threading.Event()
        self.upload_body = None
        self.upload_start = None
        self.last_cluster_state = None
        self.last_install_status = None

    def _sleep(self, seconds):
        """Sleep, but wake immediately (and stop) if cancelled."""
        if self.cancel.wait(seconds):
            raise Cancelled("stopped")

    # ---- low-level HTTP
    def _req(self, method, path, params=None, json_body=None, data=None, headers=None,
             timeout=None, auth="basic"):
        url = self.base + path + ("?" + urllib.parse.urlencode(params) if params else "")
        hdrs = {"Accept": "application/json"}
        if auth == "basic":
            hdrs["Authorization"] = self.basic
        if json_body is not None:
            data = json.dumps(json_body).encode()
            hdrs["Content-Type"] = "application/json"
        if data is None and method in ("POST", "PUT"):
            data = b""
        hdrs.update(headers or {})
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with self.opener.open(req, timeout=timeout or self.timeout) as r:
                resp = Response(r.status, r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:  # 4xx/5xx still carry a useful body
            resp = Response(e.code, e.read().decode("utf-8", "replace"))
        if self.debug:
            log(f"DEBUG {method} {path} -> {resp.status}: {resp.text[:1500]}")
        return resp

    def _report_nodes(self, data, label):
        """Print per-node lines whenever any node's state changes."""
        nodes = node_states(data)
        if nodes and nodes != self._last_nodes:
            for n in sorted(nodes):
                if self._last_nodes.get(n) != nodes[n]:
                    log(f"    {label} {n}: {nodes[n]}")
            self._last_nodes = nodes

    # ---- cluster state
    # Endpoints that report cluster state; their JSON layout differs between releases,
    # so each is tried in turn until one yields a recognisable state.
    STATE_ENDPOINTS = ("/casa/sysadmin/cluster/online_state",
                       "/casa/sysadmin/cluster/membership",
                       "/casa/view/cluster")
    STATE_KEYS = ("cluster_online_state", "online_state", "onlineState", "cluster_state",
                  "cluster_online_state_snapshot", "state")
    KNOWN_STATES = ("ONLINE", "OFFLINE", "GOING_ONLINE", "GOING_OFFLINE", "BRINGING_ONLINE",
                    "TAKING_OFFLINE", "INITIALIZING", "FAILED", "ERROR", "STARTING", "STOPPING")

    @classmethod
    def _parse_state(cls, data):
        # 1) a known key at the shallowest level that holds a recognisable state string
        val = find_key(data, *cls.STATE_KEYS)
        if isinstance(val, str) and val.upper() in cls.KNOWN_STATES:
            return val.upper()
        # 2) the response itself may just be the state string
        if isinstance(data, str) and data.upper() in cls.KNOWN_STATES:
            return data.upper()
        return None

    def online_state(self):
        last_data = None
        for path in self.STATE_ENDPOINTS:
            r = self._req("GET", path)
            if r.status in (401, 403):
                raise AuthError(f"HTTP {r.status}")
            if not r.ok:
                continue
            try:
                data = r.json()
            except ValueError:
                data = r.text.strip().strip('"')
            last_data = data
            st = self._parse_state(data)
            if st:
                return st, data
        if last_data is None:
            raise RuntimeError("no cluster state endpoint responded successfully")
        return "UNKNOWN", last_data

    def set_online_state(self, state, reason):
        body = {"online_state": state, "online_state_reason": reason}
        start = time.time()
        try:
            with Heartbeat(f"setting cluster {state}", self.heartbeat):
                r = self._req("POST", "/casa/sysadmin/cluster/online_state",
                              params={"async": "false"}, json_body=body, timeout=3600)
        except NET_ERRORS as e:
            # the request was sent; the state change carries on server-side, so just watch for it
            log(f"  request to set {state} got no response after {fmt_dur(time.time() - start)} "
                f"({e.__class__.__name__}) - continuing to watch the cluster state.")
            return
        if not r.ok:
            die(f"Setting cluster {state} failed: HTTP {r.status} {r.text}")

    def wait_state(self, wanted, timeout_s, interval=20):
        start = time.time()
        deadline = start + timeout_s
        last, last_beat = None, start
        self._last_nodes = {}
        while time.time() < deadline:
            data = None
            try:
                st, data = self.online_state()
            except (NET_ERRORS + (ValueError, RuntimeError)) as e:
                st = f"unreachable ({e.__class__.__name__})"
            self.last_cluster_state = st
            if st != last:
                log(f"  cluster state: {st} (elapsed {fmt_dur(time.time() - start)})")
                last, last_beat = st, time.time()
            if data is not None:
                self._report_nodes(data, "node")
            if st == wanted:
                return True
            if self.heartbeat > 0 and time.time() - last_beat >= self.heartbeat:
                log(f"  ... waiting for {wanted}: still {st} "
                    f"(elapsed {fmt_dur(time.time() - start)}, timeout in {fmt_dur(deadline - time.time())})")
                last_beat = time.time()
            self._sleep(interval)
        return False

    # ---- PAK handling
    def upload_pak(self, path, clobber):
        extra = {"pak_handling_advice": "CLOBBER"} if clobber else None
        body = MultipartFile(path, "contents", extra, cancel=self.cancel)
        size = os.path.getsize(path)
        log(f"Uploading {os.path.basename(path)} ({size / 1024**3:.2f} GiB) - this can take a while...")
        start = time.time()
        self.upload_body, self.upload_start = body, start
        stop = threading.Event()

        def rate_eta():
            done, total = body.bytes_read, body.len
            el = max(time.time() - start, 0.001)
            if done >= total:
                return "all bytes sent, waiting for server to verify the PAK"
            mbps = done / el / 1024**2
            eta = (total - done) / (done / el) if done else 0
            return f"{done * 100 // total}% at {mbps:.1f} MiB/s, ETA {fmt_dur(eta)}"

        def progress_printer():  # prints every 10% from a side thread
            last_pct = -10
            while not stop.wait(1):
                pct = body.bytes_read * 100 // body.len
                if pct >= last_pct + 10 and body.bytes_read < body.len:
                    last_pct = pct - pct % 10
                    log(f"  upload {rate_eta()}")
                elif body.bytes_read >= body.len and last_pct < 100:
                    last_pct = 100
                    log(f"  upload 100% sent in {fmt_dur(time.time() - start)}, "
                        f"waiting for server to verify the PAK...")

        printer = threading.Thread(target=progress_printer, daemon=True)
        printer.start()
        try:
            with Heartbeat("upload", self.heartbeat, rate_eta):
                r = self._req("POST", "/casa/upgrade/cluster/pak/reserved/operation/upload",
                              params=extra, data=body, timeout=3600,
                              headers={"Content-Type": body.content_type,
                                       "Content-Length": str(body.len)})
        except NET_ERRORS as e:
            die(f"Upload failed after {fmt_dur(time.time() - start)}: {e}")
        finally:
            stop.set()
            body.close()
        log(f"  upload request finished in {fmt_dur(time.time() - start)}")

        if not r.ok:
            die(f"Upload failed: HTTP {r.status} {r.text}")
        try:
            data = r.json()
        except ValueError:
            die(f"Upload returned non-JSON response: {r.text[:500]}")
        pak_id = find_key(data, "pak_id", "pakId")
        if not pak_id:
            die(f"Upload succeeded but no pak_id found in response: {json.dumps(data)}")
        log(f"Upload complete. PAK ID: {pak_id}")
        return pak_id

    def install_pak(self, pak_id):
        """Start the install. The server can take minutes to answer this request
        (it validates the PAK and kicks off the install first), and may stop answering
        entirely as services restart - in which case the install is most likely running,
        so we fall through to status monitoring instead of failing."""
        log("Requesting install (the server may take several minutes to respond)...")
        start = time.time()
        try:
            with Heartbeat("install request", self.heartbeat):
                r = self._req("POST", f"/casa/upgrade/cluster/pak/{pak_id}/operation/install",
                              headers={"Content-Type": "application/json"}, timeout=900)
        except NET_ERRORS as e:
            log(f"  install request got no response after {fmt_dur(time.time() - start)} "
                f"({e.__class__.__name__}). The install has most likely started - "
                f"switching to status monitoring.")
            return
        if not r.ok:
            die(f"Install request failed: HTTP {r.status} {r.text}")
        log(f"Install started (request took {fmt_dur(time.time() - start)}).")

    def pak_status(self, pak_id):
        r = self._req("GET", f"/casa/upgrade/cluster/pak/{pak_id}/status")
        if not r.ok:
            raise RuntimeError(f"HTTP {r.status}: {r.text[:300]}")
        data = r.json()
        st = find_key(data, "cluster_pak_install_status")
        return (str(st).upper() if st else "UNKNOWN"), data

    DONE_WORDS = ("COMPLETED", "COMPLETE", "SUCCESS", "SUCCEEDED", "DONE", "INSTALLED", "FINISHED")
    FAIL_WORDS = ("FAIL", "ERROR", "CANCEL", "ABORT")

    def wait_install(self, pak_id, timeout_s, before_version=None, interval=30, cross_check_every=150):
        """Poll the PAK status until the install finishes.

        Completion is detected two ways, whichever comes first:
          1. the PAK status reports a completed state, or
          2. (cross-check, every ~2.5 min) the cluster is ONLINE and reports a different
             version than before - covers releases whose status wording differs or whose
             status endpoint stops answering once the upgrade is done.
        """
        start = time.time()
        deadline = start + timeout_s
        last, last_beat, last_check = None, start, start
        raw_shown = False
        self._last_nodes = {}
        while time.time() < deadline:
            data = None
            try:
                st, data = self.pak_status(pak_id)
                step = find_key(data, "current_pak_activity", "pak_step", "message")
                detail = f"{st}" + (f" / {step}" if step else "")
                if st == "UNKNOWN" and not raw_shown:  # unfamiliar layout - show it once
                    log(f"  (unrecognised status response: {json.dumps(data)[:600]})")
                    raw_shown = True
            except (NET_ERRORS + (ValueError, RuntimeError)) as e:
                st, detail = "UNREACHABLE", f"unreachable ({e.__class__.__name__}) - nodes likely restarting"
            self.last_install_status = st
            if detail != last:
                log(f"  install status: {detail} (elapsed {fmt_dur(time.time() - start)})")
                last, last_beat = detail, time.time()
            if data is not None:
                self._report_nodes(data, "node")
            if self.heartbeat > 0 and time.time() - last_beat >= self.heartbeat:
                log(f"  ... install still {st} (elapsed {fmt_dur(time.time() - start)}, "
                    f"timeout in {fmt_dur(deadline - time.time())})")
                last_beat = time.time()

            if any(w in st for w in self.DONE_WORDS):
                return True
            if any(w in st for w in self.FAIL_WORDS):
                die(f"PAK install reported {st}. Check the admin UI (Software Update) and "
                    f"/storage/log/vcops/log/pakManager on the primary node.")

            # cross-check: is the cluster already back online on a new version?
            if before_version and before_version != "unknown" and time.time() - last_check >= cross_check_every:
                last_check = time.time()
                try:
                    cst, _ = self.online_state()
                except (NET_ERRORS + (ValueError, RuntimeError)):
                    cst = None
                if cst == "ONLINE":
                    now_ver = self.version()
                    if now_ver not in ("unknown", before_version):
                        log(f"  cluster is ONLINE and now reports {now_ver} - install is complete.")
                        return True
            self._sleep(interval)
        return False

    # ---- version
    def version(self):
        """Return a human-readable version string, trying suite-api then CASA."""
        try:
            tok = self._req("POST", "/suite-api/api/auth/token/acquire", auth=None,
                            json_body={"username": self.user, "password": self.password})
            if tok.ok:
                r = self._req("GET", "/suite-api/api/versions/current", auth=None,
                              headers={"Authorization": f"OpsToken {tok.json().get('token')}"})
            else:
                r = self._req("GET", "/suite-api/api/versions/current")
            if r.ok:
                v = r.json()
                name = (v.get("releaseName") or "").strip()
                build = v.get("buildNumber")
                # major/minor/minorMinor are the API version on some releases, not the
                # product version, so only use them when releaseName has no version in it
                if not any(ch.isdigit() for ch in name):
                    parts = [v.get(k) for k in ("major", "minor", "minorMinor")]
                    name = f"{name} {'.'.join(str(p) for p in parts if p is not None)}".strip()
                return f"{name} (build {build})" if build else name
        except (NET_ERRORS + (ValueError,)):
            pass
        try:
            r = self._req("GET", "/casa/deployment/cluster/info")
            if r.ok:
                v = find_key(r.json(), "product_version", "version", "build_number")
                if v:
                    return str(v)
        except (NET_ERRORS + (ValueError,)):
            pass
        return "unknown"


# ----------------------------------------------------------------------------- main

def main():
    p = argparse.ArgumentParser(description="Upgrade VCF Operations via the CASA API")
    p.add_argument("--host", required=True, help="Primary node FQDN/IP (or VIP)")
    p.add_argument("--user", default="admin")
    p.add_argument("--pak", help="Path to the .pak file to upload")
    p.add_argument("--pak-id", help="Skip upload and install an already-uploaded PAK ID")
    p.add_argument("--clobber", action="store_true",
                   help="Send pak_handling_advice=CLOBBER (overwrite a previously uploaded copy)")
    p.add_argument("--reason", default="PAK upgrade via CASA API", help="Offline reason text")
    p.add_argument("--skip-offline", action="store_true", help="Don't take the cluster offline first")
    p.add_argument("--bring-online", action="store_true",
                   help="If the cluster is still OFFLINE after the install, bring it online")
    p.add_argument("--offline-timeout", type=int, default=3600, help="seconds (default 3600)")
    p.add_argument("--install-timeout", type=int, default=4 * 3600, help="seconds (default 14400)")
    p.add_argument("--online-timeout", type=int, default=2 * 3600, help="seconds (default 7200)")
    p.add_argument("--insecure", action="store_true", help="Skip TLS certificate verification")
    p.add_argument("--ca-bundle", help="CA bundle path for TLS verification")
    p.add_argument("--yes", "-y", action="store_true", help="Don't ask for confirmation")
    p.add_argument("--heartbeat", type=int, default=300,
                   help="Seconds between 'still running' updates during quiet stretches (0 = off, default 300)")
    p.add_argument("--debug", action="store_true", help="Print raw API responses")
    p.add_argument("--monitor-only", action="store_true",
                   help="With --pak-id: don't offline/upload/install - just watch an install that is "
                        "already running, wait for ONLINE, and report the version")
    p.add_argument("--status-only", action="store_true",
                   help="Just show cluster state, nodes and version, then exit (no changes)")
    a = p.parse_args()

    if not a.pak and not a.pak_id and not a.status_only:
        p.error("one of --pak or --pak-id is required")
    if a.monitor_only and not a.pak_id:
        p.error("--monitor-only requires --pak-id (the PAK ID printed after upload)")
    if a.pak and not os.path.isfile(a.pak):
        p.error(f"PAK file not found: {a.pak}")

    if a.insecure:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    else:
        ctx = ssl.create_default_context(cafile=a.ca_bundle) if a.ca_bundle else ssl.create_default_context()

    # 1. prompt for the password (hidden) and verify it before doing anything
    max_tries = 3
    for attempt in range(1, max_tries + 1):
        try:
            password = getpass.getpass(f"Password for {a.user}@{a.host}: ")
        except EOFError:
            die("No password entered.", 2)
        if not password:
            log("Password cannot be empty.")
            continue
        ops = Ops(a.host, a.user, password, ctx, debug=a.debug, heartbeat=a.heartbeat)
        try:
            state, state_data = ops.online_state()
            break
        except AuthError:
            left = max_tries - attempt
            log(f"Login failed - incorrect user or password." + (f" {left} attempt(s) left." if left else ""))
        except RuntimeError as e:
            die(f"CASA API call failed ({e}). Check that {a.host} is the primary node.")
        except NET_ERRORS as e:
            hint = " (self-signed cert? add --insecure or --ca-bundle)" if "CERTIFICATE" in str(e).upper() else ""
            if not a.monitor_only or hint:
                die(f"Cannot reach {a.host}: {e}{hint}")
            # an install restarts services, so the API may be down for a while - keep trying
            log(f"Cluster API not reachable yet ({e.__class__.__name__}) - install likely restarting "
                f"services, retrying every 30s...")
            deadline = time.time() + a.install_timeout
            while True:
                time.sleep(30)
                try:
                    state, state_data = ops.online_state()
                    break
                except AuthError:
                    die("Login rejected once the API came back - check the password and re-run.")
                except (NET_ERRORS + (RuntimeError, ValueError)) as e2:
                    if time.time() > deadline:
                        die(f"Cluster API still unreachable after {fmt_dur(a.install_timeout)}: {e2}")
                    log(f"  ... still unreachable ({e2.__class__.__name__})")
            break
    else:
        die("Too many failed login attempts.")
    run_start = time.time()
    before = ops.version()
    log(f"Connected to {a.host}. Cluster state: {state}. Current version: {before}")
    ops._last_nodes = {}
    ops._report_nodes(state_data, "node")

    if a.status_only:
        if state == "UNKNOWN" or a.debug:
            log("Raw cluster state response:")
            print(json.dumps(state_data, indent=2) if not isinstance(state_data, str) else state_data)
        return

    if state == "UNKNOWN" and not a.monitor_only:
        die("Could not determine the cluster state, so stopping before making any changes. "
            "Re-run with --status-only --debug and check the raw responses.")

    if a.monitor_only:
        log(f"Monitor-only mode: watching install of {a.pak_id} (no changes will be made).")
    elif not a.yes:
        what = a.pak or a.pak_id
        ans = input(f"Take cluster OFFLINE and install {what}? Type 'yes' to continue: ")
        if ans.strip().lower() != "yes":
            die("Aborted by user.", 2)

    # 2. offline
    if not a.monitor_only and not a.skip_offline and state != "OFFLINE":
        log(f"Taking cluster offline (reason: {a.reason})...")
        ops.set_online_state("OFFLINE", a.reason)
        if not ops.wait_state("OFFLINE", a.offline_timeout):
            die("Cluster did not reach OFFLINE within the timeout.")
        log("Cluster is OFFLINE.")

    # 3. upload
    pak_id = a.pak_id or ops.upload_pak(a.pak, a.clobber)

    # 4/5. install + wait
    if not a.monitor_only:
        ops.install_pak(pak_id)
    started = time.time()
    if not ops.wait_install(pak_id, a.install_timeout, before_version=before):
        die("PAK install did not complete within the timeout. Check the admin UI.")
    log(f"PAK install COMPLETED in {fmt_dur(time.time() - started)}.")

    # 6. wait for online (the upgrade normally brings the cluster online on its own)
    log("Waiting for cluster to come back ONLINE...")
    online = ops.wait_state("ONLINE", 900 if a.bring_online else a.online_timeout)
    if not online and a.bring_online:
        log("Cluster still not online - requesting ONLINE...")
        ops.set_online_state("ONLINE", None)
        online = ops.wait_state("ONLINE", a.online_timeout)
    if not online:
        die("Cluster did not return ONLINE. Check the admin UI "
            "(if the install shows a 'Finish Installation' step, complete it there).")

    # 7. report
    after = ops.version()
    log("=" * 60)
    log("Upgrade finished. Cluster is ONLINE.")
    log(f"  Previous version: {before}")
    log(f"  New version:      {after}")
    log(f"  Total run time:   {fmt_dur(time.time() - run_start)}")
    log("=" * 60)
    if before == after and before != "unknown":
        log("WARNING: version string is unchanged - verify in the admin UI.")
        sys.exit(3)


DEBUG = "--debug" in sys.argv

if __name__ == "__main__":
    try:
        main()
    except NET_ERRORS as e:
        die(f"Lost contact with the cluster ({e.__class__.__name__}: {e}). The operation may still be "
            f"running on the cluster - check the admin UI. To resume watching an install, re-run with "
            f"--pak-id <PAK ID> --monitor-only.")
    except KeyboardInterrupt:
        die("Interrupted. The upgrade may still be running on the cluster - check the admin UI.", 130)
    except SystemExit:
        raise
    except Exception as e:  # last-resort guard: never dump a raw traceback on the user
        if DEBUG:
            import traceback
            traceback.print_exc()
        die(f"Unexpected error ({e.__class__.__name__}: {e}). The operation may still be running on "
            f"the cluster - check the admin UI. To resume watching an install, re-run with "
            f"--pak-id <PAK ID> --monitor-only. Add --debug for full details.")
