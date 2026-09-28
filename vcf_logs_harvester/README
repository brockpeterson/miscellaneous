# vcf_logs_harvester

`vcf_logs_harvester.py` pulls log events out of **VMware Aria Operations for Logs** (formerly vRealize Log Insight) over its REST API. You give it a time window and what to match on, and it writes every matching event to the screen or a file. If a window holds more than one API page (20,000 events), it keeps querying until the whole window has been collected.

**What it does:**

- Signs in with a **Local**, **Active Directory**, or **vIDM** account, and always asks for the password (typing is hidden)
- Matches text in the log message (`--text DROP`) and, optionally, other fields (`--field appname=vpxd`)
- Looks up a short name or FQDN to its IP and matches that exact IP in the text (`--resolve server01`)
- Collects the whole window in 20,000-event pages, with no duplicates or gaps where one page ends and the next begins
- Signs in again automatically if the session runs out during a long pull
- Writes plain log lines (the default, with a blank line between events), JSON Lines, or CSV

---

## Requirements

| Item | Notes |
|---|---|
| Python 3.8+ | `py --version` (Windows) or `python3 --version` (macOS/Linux) |
| `requests` | Handles the calls to the server. Installs `urllib3` automatically. |
| `tzdata` | **Windows only**, and only if you use `--tz` with a named time zone such as `America/Chicago` |
| Network access | TCP **9543** (HTTPS API) from your machine to the Aria Operations for Logs server |
| Account | A Local, AD, or vIDM user with permission to search logs |

### Install

**Windows (PowerShell)**
```powershell
winget install Python.Python.3.12      # if Python isn't installed yet
py -m pip install requests tzdata
```

**macOS / Linux**
```bash
python3 -m pip install requests
```

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
| `NotOpenSSLWarning` (macOS) | This comes from macOS's built-in Python. It's harmless and the script hides it |
| Fewer events than expected | Check the `Match` and `Server query` lines. `--text` matches whole terms, so use `DROP*` to also catch `DROPPED` |

---

## Notes

- **Read only:** the script only searches logs and never changes anything on the server.
- **Tested against:** the on-premises (appliance) API `/api/v2`. The SaaS version, Aria Operations for Logs Cloud, uses a different API and isn't supported.
- **API documentation:** the full reference is on the appliance at `https://<host>/rest-api`.
