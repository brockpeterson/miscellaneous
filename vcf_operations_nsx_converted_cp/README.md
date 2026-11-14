# CP to MP Converter

A command line tool that converts **VMware Aria Operations for Logs content packs** (`.vlcp`) into
**VCF Operations management packs** (`.pak`), so existing log content can be installed into VCF Operations.

## Which jar do I use?

Two jars are provided, built for different Java versions. Pick the one that matches (or is older than)
the Java you have installed — check yours with `java -version`.

| Jar | Requires |
|-----|----------|
| `cp-to-mp-convertor-jar-with-dependencies-jdk17.jar` | Java 17 or newer |
| `cp-to-mp-convertor-jar-with-dependencies-jdk25.jar` | Java 25 or newer |

If you're not sure which to pick, use the `jdk17` jar — it runs on any Java 17+ runtime, including 25.
The `jdk25` jar will **not** run on Java versions older than 25.

If the jar does not start and reports `UnsupportedClassVersionError`, your Java is older than the
jar requires — either install a newer JDK or switch to the `jdk17` jar.

## What gets converted

- Extracted fields
- Saved queries
- Alerts (alert and symptom definitions)
- Dashboards and widgets
- Agent groups
- Content pack icon, description and overview text

## Usage

Convert a single content pack:

```bash
java -jar cp-to-mp-convertor-jar-with-dependencies-jdk17.jar -i "My-Content.vlcp" -o output
```

Convert every content pack in a folder:

```bash
java -jar cp-to-mp-convertor-jar-with-dependencies-jdk17.jar -f "content-packs/" -o output
```

(Swap in the `jdk25` jar the same way if that's the one you're using.)

### Options

| Option | Value | Description |
|--------|-------|-------------|
| `-i` | file | Input `.vlcp` file. Either `-i` or `-f` is required. |
| `-f` | folder | Input folder. Non-`.vlcp` files are ignored. |
| `-o` | folder | Output folder. Created if missing. Defaults to the current directory. |
| `-n` | name | Override the content pack name used for the output. |
| `-force` | — | Convert even content packs that VCF Operations already ships (see below). |
| `-d` | — | Enable debug logging. |

## Output

For each converted content pack you get a folder named after the pack:

```
output/
└── My-Content/
    ├── My-Content.pak      ← install this in VCF Operations
    ├── My-Content.png
    ├── alertdefs.xml
    ├── symptomdefs.xml
    ├── logextractedfields.xml
    ├── logqueryconfigs.xml
    ├── logtemplates.json
    └── dashboard.json
```

The `.pak` file is the deliverable. The other files are the individual pieces of converted content that
went into it — useful for inspecting or troubleshooting what the converter produced.

## Installing the result

Add the generated `.pak` through the VCF Operations UI, the same way you would install any other
management pack.

Generated packs are **unsigned**, so the target VCF Operations instance must be configured to allow
installation of unsigned management packs.

## Good to know

- **Some packs are skipped on purpose.** VMware content packs whose equivalent already ships with
  VCF Operations (vSphere, vSAN, NSX, Aria Automation and similar) are skipped, and the tool logs
  why. Use `-force` if you want to convert them anyway.
- **`-n` and `-force` only apply to `-i`.** They are ignored when converting a whole folder with `-f`.
- **`-f` also looks in subfolders**, so pointing it at a large directory tree may convert more than
  you expect.
- **Re-running overwrites previous output** for the same pack without asking.
- **There is no `-h` / `--help`.** Run it with no arguments and it will tell you that `-i` or `-f` is
  required.
