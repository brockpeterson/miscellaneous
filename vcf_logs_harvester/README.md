# vcf_logs_harvester

`vcf_logs_harvester.py` pulls log events out of **VMware Aria Operations for Logs** (formerly vRealize Log Insight) over its REST API. You give it a time window and what to match on, and it writes every matching event to the screen or a file. If a window holds more than one API page (20,000 events), it keeps querying until the whole window has been collected.

**What it does:**

- Signs in with a **Local**, **Active Directory**, or **vIDM** account, and always asks for the password (typing is hidden)
- Matches text in the log message (`--text DROP`) and, optionally, other fields (`--field appname=vpxd`)
- Looks up a short name or FQDN to its IP and matches that exact IP in the text (`--resolve server01`)
- Collects the whole window in 20,000-event pages, with no duplicates or gaps where one page ends and the next begins
- Signs in again automatically if the session runs out during a long pull
- Writes plain log lines (the default, with a blank line between events), JSON Lines, or CSV
- Summarizes what it found (`--summary`): similar lines grouped into patterns, top hosts, apps, users and IPs, security highlights, and the busiest period. This all runs locally, so nothing leaves your machine
- Also comes with a desktop window, `vcf_logs_harvester_gui.py`, for anyone who'd rather not use the command line (see [Desktop window (GUI)](#desktop-window-gui))

---

## Requirements

| Item | Notes |
|---|---|
| Python 3.8+ | `py --version` (Windows) or `python3 --version` (macOS/Linux). Nothing else to install: the script uses only Python's standard library |
| `tzdata` | **Windows only**, and only if you use `--tz` (or the GUI's Zone box) with a named time zone such as `America/Chicago` |
| Network access | TCP **9543** (HTTPS API) from your machine to the Aria Operations for Logs server |
| Account | A Local, AD, or vIDM user with permission to search logs |

### Install

Copy `vcf_logs_harvester.py` (and `vcf_logs_harvester_gui.py` if you want the window) into a folder. That's it.

**Windows (PowerShell)**, only if Python isn't installed yet:
```powershell
winget install Python.Python.3.12
py -m pip install tzdata          # optional: only for named time zones
```

**macOS**: the built-in `python3` runs the command-line script as is. The GUI needs a newer Python, installed with `brew install python python-tk` or from python.org (see [Why macOS needs a newer Python](#why-macos-needs-a-newer-python)).

---

## Quick start

```bash
python3 vcf_logs_harvester.py --host logs.example.com --user admin --provider Local \
    --text DROP --last 1h --insecure
```

It asks for the password, then prints each matching log line with a blank line between events:

```
Password for admin@logs.example.com:
Authenticated as admin via Local (session ttl 1800s)
Window: 2026-09-28T17:00:00.000Z -> 2026-09-28T18:00:00.000Z
Match: text CONTAINS 'DROP'
Server query: text CONTAINS 'DROP'
  page 1: 312 events from 2026-09-28T17:00:00.000Z
<log line>

<log line>
...
Done: 312 events written to stdout
```

Status messages go to **stderr** and log events go to **stdout**, so you can redirect the events without the status lines getting mixed in (`> out.log`).

> **PowerShell:** a line ending in `\` in these examples continues on the next line. In PowerShell, end the line with a backtick (`` ` ``) instead, or put the whole command on one line. Run the script with `py` instead of `python3`.

---

## Desktop window (GUI)

`vcf_logs_harvester_gui.py` runs the same search from a dark-mode window. Matched terms, the resolved host's IP, other IPs and timestamps are colour-highlighted in the results, and counters show events, pages and elapsed time. Keep it in the **same folder** as `vcf_logs_harvester.py`: it uses that script's search code, so results are identical.

**Windows**
```powershell
py vcf_logs_harvester_gui.py
```
You can also double-click the file. Renaming it to `vcf_logs_harvester_gui.pyw` opens it without a console window behind it.

**macOS**: the window needs a newer Python than the one built into macOS (see [Why macOS needs a newer Python](#why-macos-needs-a-newer-python)). Install one once:
```bash
brew install python python-tk        # Homebrew
# or: run the installer from https://www.python.org/downloads/macos/
```
Then run the window with either command:
```bash
python3 vcf_logs_harvester_gui.py       # switches to the newer Python by itself
python3.14 vcf_logs_harvester_gui.py    # or name the newer Python directly
```
To see which versions you have, type `python3.` and press Tab, or run `ls /opt/homebrew/bin/python3.*`.

**Linux**
```bash
python3 vcf_logs_harvester_gui.py
```

#### Why macOS needs a newer Python

On a Mac, `python3` normally runs Apple's built-in Python 3.9 (`/usr/bin/python3`). It runs the command-line script fine, but it comes with **Tk 8.5**, the old window toolkit that Apple no longer updates. With Tk 8.5 the window can open blank or black, hang, or refuse to close.

Installing Python from Homebrew or python.org adds a current version with **Tk 8.6 or 9.0**, but it doesn't take over the `python3` command. It's installed as `python3.14` (or whichever version), and `/usr/bin` usually comes first in your PATH, so `python3` still finds Apple's copy.

To avoid having to know this, the window checks its Tk version when it starts. If it was started with Apple's Python and a newer Python is installed (in `/opt/homebrew/bin`, `/usr/local/bin` or `/Library/Frameworks/Python.framework`), it restarts itself with that Python and prints which one it switched to. If none is installed, it prints how to install one.

To make `python3` itself run the newer version, add this line to `~/.zprofile` and open a new Terminal window:
```bash
export PATH="/opt/homebrew/bin:$PATH"
```
To check, run `python3 -c "import tkinter; print(tkinter.TkVersion)"`. It should print `8.6` or `9.0`.

**Using it:**

1. **Connection:** enter the server, username and password, and choose **Local**, **ActiveDirectory**, or **vIDM**. The AD domain box is available when you choose ActiveDirectory or vIDM.
2. **Search:** put one entry per line in each box:
   - **Text contains** does the same as `--text`
   - **Hosts** does the same as `--resolve` (names are looked up to their IP)
   - **Fields** does the same as `--field`, as `NAME=VALUE`
3. **Time window:** choose **Last** *n* minutes, hours, days or weeks, or **From** a start time **to** an end time.
4. Click **Run query**, or press **Enter** anywhere outside the multi-line boxes (inside them, Enter starts a new line). Results appear as they arrive, with a blank line between events. The **Activity** tab shows the same status messages as the command line. **Stop** cancels a running search.
5. Click **Save results** to write every result to a file. The file type you choose decides the format: `.log`/`.txt` for log lines, `.jsonl` for all fields, `.csv` for a spreadsheet.

The window shows at most the first 5,000 results so it stays responsive. **Save results** always writes all of them.

**Summary tab.** When a query finishes, the **Summary** tab shows the same analysis as `--summary`, and you can click any part of it to filter the results:

- Headline numbers: events, patterns, hosts, patterns seen once, and the busiest time slice.
- **Events over time:** a bar chart. Hover a bar to see its time range and count, or click it to show just those events.
- **Log patterns:** sort by *Most frequent* or *Rarest first*, and click a pattern to see every line in it.
- **Highlights:** failed logins, lockouts, firewall drops, errors, sudo use and logins. Click one to see its events.
- **Top:** choose Hosts, Apps, Users or IPs, and click a value to see its events.

While a filter is on, a banner above the results shows what's filtered (for example, *Highlight Failed logins · 34 of 185 events*), with a **Clear filter** button. **Save results** then saves only the filtered events.

**Hide form** collapses the input panels to give the results and summary more room. Click **Show form** to bring them back.

**Banner logo.** You can show a company logo (PNG, JPG or GIF) at the left of the banner, with the title beside it. There are three ways to set it:

1. **From the window:** click the **⋮** button at the top right of the banner (or right-click anywhere on the banner) and choose **Set banner logo…**. The window remembers it next time.
2. **For everyone who uses the script:** put a file named `banner_logo.png` (or `.jpg`/`.gif`) in the same folder as the script, for example in the repo. It's used automatically unless someone chooses a different logo or removes it.
3. **From the command line:** `python3 vcf_logs_harvester_gui.py --logo /path/to/logo.png`.

The logo is resized to fit a 240 × 56 pixel area (larger on high-DPI Windows displays), keeping its proportions and any transparency. Nothing needs installing to do this: the script uses Pillow if it's present, otherwise the built-in `sips` on macOS or .NET imaging through PowerShell on Windows. On Linux without Pillow, use a PNG. A transparent PNG at least 56 pixels tall gives the cleanest result.

If your logo is dark (black or navy text), it will be hard to see on the dark banner. Use **⋮ → Light background behind logo** to put it on a light rounded panel. **⋮ → Remove logo** takes it off, and it stays off.

The window remembers your server, username, search settings and logo choice between runs, in `.vcf_logs_harvester_gui.json` in your home folder. Resized logos are cached in `.vcf_logs_harvester_cache` there. **The password is never saved.**

**Requirements:** the same as the command-line script, plus Tkinter, which is Python's built-in window toolkit.
- **Windows:** the python.org installer and `winget` include Tkinter.
- **macOS:** Python from Homebrew (`brew install python python-tk`) or python.org. The Python built into macOS has an outdated Tk (see [Why macOS needs a newer Python](#why-macos-needs-a-newer-python)).
- **Linux:** `sudo apt install python3-tk` (Debian/Ubuntu) or `sudo dnf install python3-tkinter` (Fedora/RHEL).

---

## Options

### Connection and sign-in

| Flag | Required | Default | Description |
|---|---|---|---|
| `--host HOST` | yes | | FQDN or IP of the Aria Operations for Logs server (or its load balancer VIP) |
| `--port PORT` | | `9543` | API port |
| `--user USER` | yes | | Username to sign in with |
| `--provider` | | `Local` | `Local`, `ActiveDirectory`, or `vIDM` |
| `--domain DOMAIN` | | | AD domain. If set, and `--user` doesn't already include `@` or `\`, the script signs in as `user@domain` |
| `--insecure` | | off | Skip TLS certificate checks. Use it when the server has a self-signed certificate |

You are always prompted for the password. It isn't accepted as a flag or read from an environment variable.

### What to match

| Flag | Default | Description |
|---|---|---|
| `--text STRING` / `--contains STRING` | | Text the log message must contain. The two names do the same thing. Repeat to give several |
| `--match all\|any` | `all` | With several `--text` values: `all` requires every one, `any` requires at least one |
| `--operator OP` | `CONTAINS` | How `--text` values are matched: `CONTAINS`, `HAS`, `MATCHES_REGEX`, `NOT_CONTAINS` |
| `--resolve NAME` | | Short name or FQDN to look up to its IP address(es). The log message must contain one of those IPs. An IP address is used as is. Repeat to give several hosts (an event mentioning any of them counts) |
| `--field NAME=VALUE` | | A field that must contain a value, e.g. `appname=vpxd`, `hostname=esx01`. Repeat for several |

You must give at least one `--text` or `--resolve`.

### Time window

Use **either** `--last` **or** `--start`/`--end`.

| Flag | Description |
|---|---|
| `--last DURATION` | A window ending now: `30s`, `15m`, `12h`, `7d`, `2w` |
| `--start TIME` | Start of the window. Accepts `2026-09-28`, `2026-09-28 13:00`, `2026-09-28 13:00:00`, ISO 8601 (`2026-09-28T13:00:00-05:00`, `...Z`), or an epoch time in seconds or milliseconds |
| `--end TIME` | End of the window (same formats). Defaults to now |
| `--tz ZONE` | Time zone for `--start`/`--end` values that don't include one, e.g. `America/Chicago`, `UTC`. Defaults to this machine's local zone |

The window includes the start time and excludes the end time.

### Output

| Flag | Default | Description |
|---|---|---|
| `--out FILE` | stdout | Where to write events |
| `--format` | from `--out` extension | `text`, `jsonl`, or `csv` (see below) |

| Format | Chosen automatically when | Contents |
|---|---|---|
| `text` | stdout, or any extension other than those below | The log line only, with a blank line between events |
| `jsonl` | `--out` ends in `.json` or `.jsonl` | One JSON object per line with `text`, `timestamp`, and every field. Fields the server returns as a position in the text are filled in with their values |
| `csv` | `--out` ends in `.csv` | `timestamp`, `time_utc`, `text`, then one column per field seen |

To find the field names available for `--field`, run a short `.jsonl` pull and look at the `fields` list in each event.

### Summary

| Flag | Description |
|---|---|
| `--summary` | After the run, print a summary to stderr (so it never mixes with events written to stdout) |

The summary is worked out on your machine from the events that were pulled:

- **Log patterns.** Lines that differ only in the parts that vary (numbers, IPs, PIDs, IDs, timestamps) are grouped into one pattern with a count. For example, `crond: USER root pid <N> cmd /usr/lib/vmware/vsan/bin/vsanObserver.sh …  ×40`. Patterns **seen only once** are listed separately, since rare lines are often the interesting ones.
- **Highlights.** Built-in rules count failed logins, account lockouts, firewall drops and denies, errors and failures, sudo/su use, and successful logins, along with which hosts they came from.
- **Top hosts, apps, users and IPs.** Hosts and apps come from each event's fields, or from the syslog header when there are none. Users are picked up from phrases such as `USER root`, `for user root` and `for admin from`.
- **Busiest period.** Events are counted per time slice (1 s up to 1 day, chosen to fit the window). The slice with the most events is reported.

```
=== Summary: 185 events, 12 patterns, 5 hosts ===
Busiest 5s: 17 events starting 2026-09-30T16:05:00.000Z

Highlights:
       34  Failed logins  (1 host: esxi-a02)
       30  Firewall drops / denies  (1 host: fw01)
       ...
Top patterns:
       40  crond: USER root pid <N> cmd /usr/lib/vmware/vsan/bin/vsanObserver.sh ...
       30  kernel: [<N>] DROP IN=eth0 OUT=eth1 SRC=<IP> DST=<IP> PROTO=TCP DPT=<N>
       ...
```

### Tuning

| Flag | Default | Description |
|---|---|---|
| `--page-size N` | `20000` | Events per API request (20,000 is the maximum) |
| `--timeout MS` | `120000` | How long the server may spend on each query, in milliseconds. If a query doesn't finish in time, the script doubles this (up to 10 minutes) and retries |

---

## How matching works

The script matches on two levels:

1. **Server query.** The server only receives **one filter per field**. It treats several filters on the same field as "either one" instead of "all of them", so sending them all would return too much. When there's a choice, the script sends an IP from `--resolve` first, then a positive match such as `DROP`, and a `NOT_CONTAINS` filter last.
2. **Local check.** Every event the server returns is checked against **all** your filters before it's written out:
   - `--text` values are matched as **whole terms, case-insensitively** (`DROP` matches `drop` but not `DROPPED`). A `*` works as a wildcard (`DROP*` also matches `DROPPED`).
   - IPs from `--resolve` must match exactly (`10.1.1.1` doesn't match `10.1.1.10`).

The run shows both levels:
```
Match: text CONTAINS 'DROP' AND text CONTAINS '10.20.30.40'
Server query: text CONTAINS '10.20.30.40'
```

**How the options combine:**

| You give | An event is written when its text contains |
|---|---|
| `--text A --text B` | A **and** B |
| `--text A --text B --match any` | A **or** B |
| `--text A --resolve host1` | A **and** an IP of host1 |
| `--resolve host1 --resolve host2` | an IP of host1 **or** host2 |
| `--text A --text B --match any --resolve host1` | (A **or** B) **and** an IP of host1 |

`--field` filters are always required in addition to everything above.

**Name lookup:** `--resolve` uses the DNS and hosts file of the machine running the script, and prints what it found (`Resolved server01 -> 10.20.30.40`). If a name can't be resolved, the script stops before asking for the password.

---

## How paging works

The API returns at most 20,000 events per request. The script requests events oldest first. When a page comes back full, it asks for the next page starting from the timestamp of the last event it received. Events that share that timestamp would appear on both pages, so it skips the ones it already has. It stops when a page comes back with fewer events than the page size.

The one case it can't fully handle is more than 20,000 matching events at the **same millisecond**. It prints a warning, and some of those events may be missed.

---

## Examples

**Firewall drops for one server over the last 5 minutes**
```bash
python3 vcf_logs_harvester.py --host logs.example.com --user admin --provider Local \
    --text DROP --resolve server01.corp.example.com --last 5m --insecure
```

**vCenter sign-ins and sign-outs by Administrator, only the vpxd syslog lines**
```bash
python3 vcf_logs_harvester.py --host logs.example.com --user admin \
    --text Administrator --field appname=vpxd --last 24h --insecure --out admin.log
```

**AD account, a fixed window in Central time, saved to CSV**
```bash
python3 vcf_logs_harvester.py --host logs.example.com --user jsmith \
    --provider ActiveDirectory --domain corp.example.com \
    --text DROP --text DENY --match any \
    --start "2026-09-27 00:00" --end "2026-09-28 00:00" --tz America/Chicago \
    --out drops.csv
```

**Everything mentioning either of two hosts, as JSON Lines**
```bash
python3 vcf_logs_harvester.py --host logs.example.com --user admin \
    --resolve jumpbox01 --resolve jumpbox02 --last 1h --out jumpboxes.jsonl
```

**The same query in PowerShell**
```powershell
py vcf_logs_harvester.py --host logs.example.com --user admin --provider Local `
    --text DROP --resolve server01.corp.example.com --last 5m --insecure
```

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `error: unrecognized arguments` or `the following arguments are required: --contains` | You're running an older copy. Check that `--help` lists `--resolve` and `--text` |
| No password prompt appears (Windows Git Bash) | Run from PowerShell or cmd, or use `winpty python vcf_logs_harvester.py ...` |
| `TLS error ... (self-signed cert? add --insecure)` | The server's certificate isn't trusted on this machine. Add `--insecure` |
| `Can't reach https://host:9543/api/v2` | Check the `--host` value, DNS, and that port 9543 is open from this machine |
| `Authentication failed (401)` | Wrong password, or the wrong `--provider`. For AD, try `--user 'DOMAIN\user'` or `--user user@domain` |
| `Unknown time zone` (Windows) | `py -m pip install tzdata` |
| `Couldn't resolve 'name'` | This machine can't look up the name. Try the FQDN, or pass the IP to `--resolve` |
| `query incomplete ... retrying` | The server ran out of time on a query. The script retries with a longer timeout. If it keeps happening, use a shorter window or a larger `--timeout` |
| GUI: `No module named 'tkinter'` | Install Tkinter (see [Desktop window (GUI)](#desktop-window-gui)) |
| GUI opens a blank or black window, won't close, or prints `outdated Tk 8.5` (macOS) | No newer Python is installed, so it's running on Apple's built-in Python with the old Tk. Run `brew install python python-tk` (or use the python.org installer), then start the window again. See [Why macOS needs a newer Python](#why-macos-needs-a-newer-python) |
| `zsh: command not found: python3.14` (macOS) | That version isn't installed, or it's a different version. Type `python3.` and press Tab to see what you have, or just run `python3 vcf_logs_harvester_gui.py` and let it find the newer one |
| GUI: `vcf_logs_harvester.py must be in the same folder` | Put both `.py` files in the same folder |
| Fewer events than expected | Check the `Match` and `Server query` lines. `--text` matches whole terms, so use `DROP*` to also catch `DROPPED` |

---

## Notes

- **Read only:** the script only searches logs and never changes anything on the server.
- **Tested against:** the on-premises (appliance) API `/api/v2`. The SaaS version, Aria Operations for Logs Cloud, uses a different API and isn't supported.
- **API documentation:** the full reference is on the appliance at `https://<host>/rest-api`.
