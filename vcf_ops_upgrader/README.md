# VCF Operations Upgrader

Upgrade a **VMware Cloud Foundation (VCF) Operations / Aria Operations** cluster from start to finish using the CASA (Cluster and Slice Administration) API. You can run it as a command-line script or through a local web GUI. Either way, it:

1. Takes the cluster **offline**
2. **Uploads** the upgrade `.pak` file
3. **Installs** it
4. Waits for the cluster to come back **online**
5. Reports the **new version**

It shows progress the whole way through (upload speed and time left, install status, per-node state, heartbeat lines during quiet stretches). It also copes with the long silences and dropped connections that happen while nodes restart mid-upgrade.

Pure Python standard library: **no `pip install`, no virtual environment**.

---

## Contents

| File | What it is |
|---|---|
| `vcfops_pak_upgrade.py` | The upgrade engine and command-line tool |
| `vcfops_pak_gui.py` | **VCF Operations Upgrader** GUI: a local web app that runs the engine behind a dark-themed UI |

Keep both files in the same folder. The GUI imports the script.

---

## Requirements

- Python **3.7 or later** (tested with 3.13 and 3.14, including Homebrew Python on macOS)
- Network access from your machine to the cluster's **primary node** (or VIP) on HTTPS/443
- The cluster's **admin** account
- The upgrade `.pak` file for your target version, downloaded from the Broadcom Support Portal

> **Take snapshots of every node before upgrading.** The cluster is offline for the whole upgrade.

---

## Quick start: GUI

```bash
python3 vcfops_pak_gui.py
```

Your browser opens the app at `http://127.0.0.1:8765`. Leave the terminal window open while it runs.

1. **Target cluster:** enter the primary node's address, username and password (the eye button shows the password), then **Test connection**. This step only reads from the cluster and shows its state, version and nodes.
2. **What to do:** choose **Upgrade with a PAK** and use **Browse…** to pick the `.pak` file (with Home / Downloads / Desktop shortcuts), or **Monitor an install already running** with its PAK ID.
3. Tick **I've taken snapshots of every node**, then **Take offline & upgrade**.

While it runs you get a step bar, live upload progress, install status, per-node state, an activity log you can save, and an **Assistant** panel that explains in plain language what's happening and what's normal. When it finishes, it shows the old and new versions and a timeline of how long each step took.

| GUI option | Default | Meaning |
|---|---|---|
| `--port N` | `8765` | Port to listen on. It tries the next free port if this one is taken. |
| `--no-browser` | off | Don't open a browser automatically. |

---

## Quick start: command line

```bash
python3 vcfops_pak_upgrade.py --host ops01.example.com \
  --pak ./vRealize_Operations_Manager_With_CP-8.14.x-to-8.18.7.25423537.pak --insecure
```

You're prompted for the admin password (hidden, up to 3 attempts). The script then shows the current state and version and asks you to type `yes` before anything changes.

### Common tasks

```bash
# Read-only health check: state, nodes, version. Changes nothing.
python3 vcfops_pak_upgrade.py --host ops01.example.com --insecure --status-only

# Same, plus the raw API responses (for troubleshooting)
python3 vcfops_pak_upgrade.py --host ops01.example.com --insecure --status-only --debug

# Resume watching an install that's already running (e.g. after closing the terminal)
python3 vcfops_pak_upgrade.py --host ops01.example.com --insecure --monitor-only \
  --pak-id vRealizeOperationsManagerEnterprise-818725423537
```

### All options

| Option | Default | Meaning |
|---|---|---|
| `--host` | *(required)* | Primary node FQDN/IP, or the cluster VIP |
| `--user` | `admin` | Admin username (the password is always prompted for) |
| `--pak PATH` | | `.pak` file to upload and install |
| `--pak-id ID` | | Skip the upload and install a PAK that's already uploaded |
| `--monitor-only` | off | With `--pak-id`: only watch a running install, wait for ONLINE, report the version. Changes nothing. |
| `--status-only` | off | Show state, nodes and version, then exit. Changes nothing. |
| `--clobber` | off | Overwrite a previously uploaded copy of the PAK (`pak_handling_advice=CLOBBER`) |
| `--skip-offline` | off | Don't take the cluster offline first |
| `--bring-online` | off | If the cluster is still OFFLINE after the install, bring it online |
| `--reason TEXT` | `PAK upgrade via CASA API` | Offline reason recorded on the cluster |
| `--offline-timeout S` | `3600` | Seconds to wait for OFFLINE |
| `--install-timeout S` | `14400` | Seconds to wait for the install to finish |
| `--online-timeout S` | `7200` | Seconds to wait for ONLINE after the install |
| `--heartbeat S` | `300` | Seconds between "still running" lines during quiet stretches (`0` = off) |
| `--insecure` | off | Skip TLS certificate verification (self-signed certs) |
| `--ca-bundle PATH` | | CA bundle to verify the cluster's certificate instead |
| `--yes`, `-y` | off | Don't ask for the `yes` confirmation |
| `--debug` | off | Print raw API responses, and full tracebacks on unexpected errors |

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Error (message explains what to check) |
| `2` | Aborted by the user, or no password entered |
| `3` | Finished, but the reported version didn't change: verify in the admin UI |
| `130` | Interrupted with Ctrl-C (an install already started keeps running on the cluster) |

### Example output

```
Connected to 192.168.135.106. Cluster state: ONLINE. Current version: VMware Aria Operations 8.18.3 (build 24521408)
    node 192.168.135.106: ONLINE
Taking cluster offline (reason: PAK upgrade via CASA API)...
  cluster state: OFFLINE (elapsed 0m01s)
Uploading vRealize_Operations_Manager_With_CP-8.14.x-to-8.18.7.25423537.pak (4.90 GiB) - this can take a while...
  upload 10% at 2.0 MiB/s, ETA 37m13s
  ... upload still running (elapsed 5m00s) - 11% at 2.0 MiB/s, ETA 36m40s
  upload 100% sent in 33m01s, waiting for server to verify the PAK...
Upload complete. PAK ID: vRealizeOperationsManagerEnterprise-818725423537
Requesting install (the server may take several minutes to respond)...
  install status: CANDIDATE (elapsed 0m00s)
  install status: unreachable (RemoteDisconnected) - nodes likely restarting
  install status: COMPLETED
Waiting for cluster to come back ONLINE...
  cluster state: ONLINE
============================================================
Upgrade finished. Cluster is ONLINE.
  Previous version: VMware Aria Operations 8.18.3 (build 24521408)
  New version:      VMware Aria Operations 8.18.7 (build 25423537)
============================================================
```

---

## How it handles a real upgrade

Upgrades have long silent stretches and dropped connections. The tool expects them rather than failing:

- **Slow server replies:** the install request can take minutes to answer. The tool waits up to 15 minutes, and if there's still no reply it assumes the install started and switches to watching its status.
- **Nodes restarting:** connection errors while nodes restart are shown as `unreachable … nodes likely restarting`, and the tool keeps checking.
- **Knowing when it's finished:** an install counts as finished when the status reports completion (`COMPLETED`, `SUCCESS`, `DONE`, …), **or** when the cluster is back ONLINE on a different version. The second check catches releases where the status call stops answering after the upgrade.
- **Unreadable state:** if the cluster state can't be read, the tool **refuses to take the cluster offline** and tells you to run `--status-only --debug`.
- **Different response layouts:** release-to-release differences in the API's responses are handled by trying several known field names and fallback endpoints.
- **No raw Python errors:** any unexpected problem becomes a one-line message, with the `--monitor-only` command to resume if an install had started.

---

## API endpoints used

All CASA calls use HTTP Basic auth with the admin account.

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/casa/sysadmin/cluster/online_state` | Cluster and per-node state (main check) |
| GET | `/casa/sysadmin/cluster/membership` | Backup state check |
| GET | `/casa/view/cluster` | Second backup state check |
| POST | `/casa/sysadmin/cluster/online_state?async=false` | Take offline / bring online: `{"online_state": "OFFLINE" or "ONLINE", "online_state_reason": "..."}` |
| POST | `/casa/upgrade/cluster/pak/reserved/operation/upload` | Upload the PAK (multipart field `contents`). Returns the `pak_id`. |
| POST | `/casa/upgrade/cluster/pak/{pak_id}/operation/install` | Start the install |
| GET | `/casa/upgrade/cluster/pak/{pak_id}/status` | Install progress (`cluster_pak_install_status`, per node) |
| GET | `/casa/deployment/cluster/info` | Backup version check |
| POST | `/suite-api/api/auth/token/acquire` | Token for the version check |
| GET | `/suite-api/api/versions/current` | Current version and build |

Your cluster serves the full CASA reference at `https://<node>/casa/api-guide.html`. The membership and view endpoints aren't in that guide (they come from community write-ups), which is why they're only backups.

---

## Security notes

- **Passwords:** always entered at a prompt (CLI) or in the form (GUI). Never taken from command-line arguments or environment variables, never written to disk or logs.
- **GUI listens locally only:** it binds to `127.0.0.1`, so other machines can't reach it.
- **Requests from other websites are blocked:** the GUI checks the `Host` header to block DNS-rebinding attacks, and every API call needs a random per-session token embedded in the page.
- **TLS:** `--insecure` / "Skip TLS certificate check" turns off certificate verification. Prefer `--ca-bundle` where you can.

---

## Troubleshooting

| Symptom | What to do |
|---|---|
| `Cluster state: UNKNOWN` | Run `--status-only --debug` and check the raw response. Open an issue with it (remove anything sensitive). |
| `certificate verify failed` | Add `--insecure` (or tick the GUI box), or point `--ca-bundle` at your CA's certificate. |
| `Login failed` | Check the admin password; the account may be locked after repeated failures. |
| Upload is slow | Upload speed is limited by the link from your machine to the node. Run the tool from a machine closer to the cluster. |
| Closed the terminal mid-install | The install keeps running on the cluster. Resume with `--monitor-only --pak-id <ID>`, or in the GUI choose **Monitor an install already running**. The PAK ID is in the log and on the admin UI's Software Update page. |
| Admin UI shows "Installation in progress" with Bring Cluster Online greyed out | Normal. Don't force it online; the upgrade brings it back itself. |
| Cluster doesn't return ONLINE | If the admin UI shows **Finish Installation**, complete it there. |

---

## Tested

- VMware Aria Operations **8.18.3 → 8.18.7**, single node (primary), macOS with Homebrew Python 3.14

Results on other versions, multi-node clusters and VCF Operations 9.x are welcome as issues or PRs.

---

## Disclaimer

This is a community tool, **not affiliated with or supported by Broadcom or VMware**. It uses the cluster's own CASA API, including some endpoints that aren't in the official guide. Test in a lab first, take snapshots before every upgrade, and follow the official upgrade documentation and release notes for your version. Use at your own risk.
