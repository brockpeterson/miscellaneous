# VCF Operations Metrics Picker

Collect the metrics you want from **VCF Operations** (formerly Aria Operations / vRealize Operations) for every object of a given type (hosts, VMs, clusters, or datastores) and export them to **Excel, JSON, or CSV**.

The tool comes in two parts:

| File | What it is |
|---|---|
| `vcf_operations_metrics_picker.py` | Command-line tool that does the collection and export |
| `vcf_operations_metrics_gui.py` | Browser-based GUI on top of the command-line tool |

Keep both files in the same folder.

---

## Requirements

- **Python 3.9 or newer.** Check with `python3 --version`.
- **Nothing to install.** Both scripts use only the Python standard library, so there is no `pip install` and no virtual environment. That also means they work on networks that block PyPI.
- Network access from your machine to the VCF Operations node over HTTPS (port 443).
- A VCF Operations account, or an existing OpsToken, with read access to the objects you want to collect.

---

## Quick start

### GUI

```bash
python3 vcf_operations_metrics_gui.py
```

Your browser opens to the Metrics Picker. Then:

1. Enter the **VCF Operations FQDN or IP**.
2. Choose **Username** (and enter the password) or **OpsToken**. Use **Show** to reveal what you typed.
3. Pick an **Object type**, adjust the **Stat keys** if needed, and choose a **Sample size**.
4. Click **Collect metrics**.
5. When the run finishes, review the results and click **Download Excel**, **Download JSON**, or **Download CSV**.

Press **Ctrl+C** in the terminal to stop the GUI.

### Command line

```bash
python3 vcf_operations_metrics_picker.py --server vcf-ops.example.com --user admin --format xlsx
```

You'll be prompted for the password. This collects the default four metrics for every `HostSystem` over the last 7 days and writes an Excel file.

---

## Using the GUI

### Form fields

| Field | Description |
|---|---|
| **VCF Operations FQDN or IP** | For example `vcf-ops.example.com` or `192.168.135.113` |
| **Sign in with** | **Username** + **Password** (+ optional **Auth source** for AD/vIDM users), or an **OpsToken** |
| **Verify SSL certificate** | Leave unchecked for self-signed certificates |
| **Object type** | `VirtualMachine`, `HostSystem`, `ClusterComputeResource`, or `Datastore` |
| **Stat keys** | One per line or comma-separated. Quick-add buttons and **Reset to defaults** are below the box. |
| **Use Operations metric names as column headers** | On by default. Shows `Memory\|Active (KB)` instead of `mem\|active_average`. See [Metric names](#metric-names). |
| **Name filter** | Optional. Only collects objects whose name contains this text. |
| **Sample size** | 1 hour to 30 days, or **Custom…** (for example `45m`, `10d`, `3w`). The sample count is shown below the field. |
| **Interval** | Collection interval in minutes (default 5). Used to convert the sample size into a sample count. |
| **Advanced → Parallel requests** | How many objects are fetched at once (default 4) |
| **Advanced → Time zone** | Time zone for the local-time column. Defaults to your computer's time zone. |

### Results

- **Progress:** a live log and progress bar. **Cancel** stops a run.
- **Objects table:** each object with its sample count and status (OK, or the error if it failed).
- **Data preview:** the first 200 rows.
- **Downloads:** Excel (`.xlsx`), JSON, or CSV. File names look like `HostSystem_metrics_20261015_1203.xlsx`.

### Company logo

Click **+ Add company logo** in the banner to upload a PNG, JPG, GIF, WebP, or SVG image (up to 2 MB). Hover over the logo to **Change** or **Remove** it.

The logo is saved next to the scripts as `vcf_operations_metrics_logo.<ext>`, so it stays after you restart and travels with the folder if you share it.

### What the GUI remembers

Your server, username, object type, stat keys, and other settings are remembered in your browser between sessions.

**Passwords and tokens are never saved.** They're used for the current run only and cleared afterward.

---

## Using the command line

### Options

| Option | Description | Default |
|---|---|---|
| `--server` | VCF Operations FQDN or IP **(required)** | none |
| `--user` | Username. You'll be prompted for the password. | none |
| `--token` | Existing OpsToken (or set the `VCFOPS_TOKEN` environment variable) | none |
| `--auth_source` | Auth source for `--user` (AD/vIDM source name) | none (local account) |
| `--object_type` | resourceKind to collect, for example `HostSystem`, `VirtualMachine`, `ClusterComputeResource`, `Datastore`. Case-sensitive. | `HostSystem` |
| `--stat_keys` | Metrics to collect, space- or comma-separated. **Quote them** (see below). | 4 default metrics |
| `--sample_size` | How much history: `30m`, `12h`, `1d`, `7d`, `2w`, or a plain number of days | `7d` |
| `--max_samples` | Exact number of samples per metric. Use instead of `--sample_size`. | none |
| `--interval` | Collection interval in minutes | `5` |
| `--name_filter` | Only objects whose name contains this text | none |
| `--format` | `xlsx` (or `xls`), `json`, `both`, `table`, or `csv` | `both` |
| `--output` | Output file name without extension. With `--format json`, use `-` to print to the screen. | auto-named |
| `--workers` | Parallel requests | `4` |
| `--raw_keys` | Use stat keys as column headers instead of Operations metric names | off |
| `--timezone` | Time zone for the local-time column | `America/Chicago` |
| `--verify_ssl` | Verify the server's SSL certificate | off |

Hyphenated versions of every multi-word option also work (for example `--object-type` or `--sample-size`). Run `python3 vcf_operations_metrics_picker.py -h` for built-in help.

### Default metrics

```
mem|active_average
mem|consumed_average
cpu|capacity_usagepct_average
cpu|coreUtilization_average
```

### Sample size

At the default 5-minute interval there are **288 samples per day**:

| `--sample_size` | Samples per metric |
|---|---|
| `1h` | 12 |
| `12h` | 144 |
| `1d` | 288 |
| `7d` | 2,016 |
| `14d` / `2w` | 4,032 |
| `30d` | 8,640 |

### Examples

```bash
# Last 30 days of host metrics to Excel
python3 vcf_operations_metrics_picker.py --server vcf-ops.example.com --user admin --sample_size 30d --format xlsx

# Virtual machines, last day, both Excel and JSON
python3 vcf_operations_metrics_picker.py --server vcf-ops.example.com --user admin --object_type VirtualMachine --sample_size 1d

# Your own choice of metrics
python3 vcf_operations_metrics_picker.py --server vcf-ops.example.com --user admin \
  --stat_keys "cpu|usage_average,mem|usage_average,cpu|readyPct"

# Only hosts with "esx-prod" in the name
python3 vcf_operations_metrics_picker.py --server vcf-ops.example.com --user admin --name_filter esx-prod

# Use a token instead of a password
export VCFOPS_TOKEN='your-ops-token'
python3 vcf_operations_metrics_picker.py --server vcf-ops.example.com --format xlsx

# Custom file name
python3 vcf_operations_metrics_picker.py --server vcf-ops.example.com --user admin --output weekly_hosts
```

### Printing to the screen

```bash
# Readable table (best with a short sample size)
python3 vcf_operations_metrics_picker.py --server vcf-ops.example.com --user admin --sample_size 1h --format table

# CSV, redirected to a file or piped into other tools
python3 vcf_operations_metrics_picker.py --server vcf-ops.example.com --user admin --format csv > metrics.csv

# JSON to the screen
python3 vcf_operations_metrics_picker.py --server vcf-ops.example.com --user admin --format json --output - | jq .
```

Progress messages go to stderr, so piped or redirected output contains only data.

> **Always quote stat keys.** The `|` character means "pipe" to the shell, so
> `--stat_keys cpu|usage_average` breaks, while `--stat_keys "cpu|usage_average"` works.

---

## Metric names

By default, the tool looks up each stat key's **display name and unit** in VCF Operations and uses them as column headers:

| Stat key | Column header |
|---|---|
| `mem\|active_average` | Memory\|Active (KB) |
| `mem\|consumed_average` | Memory\|Consumed (KB) |
| `cpu\|capacity_usagepct_average` | CPU\|Usage (%) |
| `cpu\|coreUtilization_average` | CPU\|Core Utilization (%) |

*(Names and units shown are examples; they come from your VCF Operations instance.)*

- The mapping is printed in the CLI output and the GUI log for every run.
- If a stat key isn't found, it keeps its raw key as the header, and the log suggests checking the spelling.
- If the lookup fails entirely, the run continues using raw keys.
- To keep raw stat keys as headers, use `--raw_keys` on the CLI, or untick **Use Operations metric names as column headers** in the GUI.
- In JSON, samples stay keyed by stat key, and a `metric_info` section maps each key to its `name`, `unit`, and `description`.

The names come from `GET /suite-api/api/adapterkinds/{adapterKind}/resourcekinds/{objectType}/statkeys`.

---

## Output formats

### Excel (`.xlsx`)

There is a single **Data** sheet with one row per object per sample:

| Name | Resource ID | Time (local) | Time (UTC) | Memory\|Active (KB) | Memory\|Consumed (KB) | … |
|---|---|---|---|---|---|---|

The header row is frozen and has filters. CPU values are shown with two decimals and memory values with thousands separators.

Excel has a limit of about 1,048,000 rows per sheet. Bigger runs are split automatically across **Data_1**, **Data_2**, and so on. At 30 days, that happens at roughly 120 objects.

### JSON

```json
{
  "server": "vcf-ops.example.com",
  "object_type": "HostSystem",
  "collected_at": "2026-10-15T12:03:00-05:00",
  "max_samples": 2016,
  "interval_minutes": 5,
  "metrics": ["mem|active_average", "..."],
  "metric_info": {
    "mem|active_average": { "name": "Memory|Active", "unit": "KB", "description": "..." }
  },
  "resources": [
    {
      "name": "esx-01.example.com",
      "resourceId": "0c522357-f2de-4605-ab16-e2b60c2eb8e7",
      "error": null,
      "samples": [
        { "timestamp": 1791469875397, "time_utc": "2026-10-08T14:31:15Z",
          "mem|active_average": 15062190, "mem|consumed_average": 177240832,
          "cpu|capacity_usagepct_average": 9.52, "cpu|coreUtilization_average": 9.94 }
      ]
    }
  ]
}
```

### CSV

The same columns as the Excel Data sheet, as plain comma-separated text.

### A note on units

Values are exactly what VCF Operations returns, with no conversion. The unit for each metric is shown in its column header (for example **KB** or **%**) and in the JSON `metric_info`.

---

## How it works

The tool uses the same VCF Operations Suite API calls you'd make with `curl`:

1. `GET /suite-api/api/resources?resourceKind=<object type>` pages through every object of the chosen type.
2. `GET /suite-api/api/resources/stats/latest?resourceId=<id>&statKey=…&maxSamples=<n>` runs once per object.
3. `GET /suite-api/api/adapterkinds/<adapter>/resourcekinds/<object type>/statkeys` looks up metric names and units. This is skipped with `--raw_keys`.

With `--user`, it first calls `POST /suite-api/api/auth/token/acquire` to get an OpsToken.

If one object fails, the run continues. Failed objects are listed at the end, shown in the GUI's Objects table, and recorded in the JSON `error` field.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `Found 0 objects` | Object types are **case-sensitive**: use `VirtualMachine`, not `virtualmachine`. Also check `--name_filter`. |
| Empty metric columns | That metric doesn't exist for this object type, or has no data. Check the stat key in the VCF Operations UI under **Metrics**. |
| "no name found in Operations" in the log | The stat key isn't in Operations' catalogue for that object type. It's usually a typo. |
| `Login failed: HTTP 401` | Wrong username or password, or you need `--auth_source` (or the **Auth source** field) for AD/vIDM accounts. |
| SSL or certificate errors | Leave **Verify SSL** off (the default) for self-signed certificates. |
| `Connection failed` | Check that you can reach the server on port 443, for example by opening `https://<server>/suite-api/` in a browser. |
| `vcf_operations_metrics_picker.py must be in the same folder` | Put both `.py` files in the same folder. |
| Pasted command doesn't work | Make sure the stat keys are in quotes (see above). |
| GUI doesn't open a browser | Copy the `http://127.0.0.1:<port>/` address printed in the terminal into your browser. |
| Runs are slow with many objects | Raise **Parallel requests** / `--workers` a little (for example to 8). Keep it modest so you don't overload the VCF Operations node. |

---

## Security notes

- The GUI listens only on **127.0.0.1**, so it's reachable only from your own computer.
- Each GUI launch uses a random session key, so other websites can't trigger runs or downloads.
- Passwords and tokens are kept in memory for the current run only. They are never written to disk or saved in the browser.
- SSL verification is off by default, to match common self-signed VCF Operations deployments. Use `--verify_ssl` (or the checkbox) when your certificate is trusted.
