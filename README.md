# AntLighting VPN 🐜⚡

**Extremely complex networking underneath. One button on top.**

AntLighting VPN is a Windows desktop application that gives you a working,
whole-device (or system-wide) proxy/VPN connection in a single click. You never
need to know what V2Ray, Xray, VLESS, Reality or a TUN device is. Open the app,
press **CONNECT**, and the app finds, tests, ranks and uses a healthy server for
you automatically.

It is a front-end for the mature, open-source **[Xray-core](https://github.com/XTLS/Xray-core)**
engine (part of the V2Ray/Xray ecosystem). AntLighting does **not** invent a new
protocol; it orchestrates an existing one.

> **Honesty first.** A public proxy does **not** make you anonymous. AntLighting
> is a censorship-circumvention / privacy-from-your-ISP tool, not an anonymity
> system. It sends no telemetry, uploads nothing silently, and only ever talks to
> the sources and servers *you* configure.

---

## Screenshots

The interface is rendered from vector art (QML), so it is resolution-independent
and themable. The mascot is a hand-drawn vector ant with three animated states:

| Offline (sleeping) | Connecting (awakening) | Connected (fighting) |
| --- | --- | --- |
| 🐜 asleep, gentle breathing | 🐜 waking up, concerned, lightning trails | 🐜 fighting pose, glowing lightning eyes, aura |

The mascot returns to sleep on disconnect and only animates while its state is
active, so it costs essentially no CPU at idle.

---

## What you can do

* **One-click connect.** The app collects public server lists, parses them, removes
  duplicates, validates them against the real engine, tests them, scores them and
  picks the best — automatically.
* **See every server.** Open the server sheet to see each server's country flag,
  name, live status, measured latency and reliability, and pick one yourself.
  The default is always `🌍 Server: Auto`.
* **Test on demand.** A **Test Servers** button re-probes the whole pool and
  re-ranks it. Tests are capped, batched and never hammer the network.
* **Read what's happening.** A diagnostics screen shows a human-readable summary,
  per-source fetch results, the application log (INFO/WARNING/ERROR/DEBUG) and the
  raw core log.
* **Tune it if you want.** An *Advanced Settings* window groups the options
  logically (Connection, Servers, Sources, Appearance) and explains every option
  with a tooltip.

## Requirements

* **Windows 10/11** (64-bit) for the packaged app. The code base is
  cross-platform Python; the TUN mode and the system-proxy registry integration
  are Windows-specific (they degrade gracefully elsewhere).
* **Python 3.10–3.12** to run from source.
* No admin rights are needed for the default (system-proxy) mode. TUN mode asks
  for elevation, and only then.

---

## Installation

### Option A — Download the prebuilt app (recommended)

1. Go to **Actions → "Build Windows app"** and either wait for a scheduled /
   tagged build or press **Run workflow**.
2. Download the `AntLighting-Windows-x64` artifact (a zip).
3. Unzip it anywhere (it is fully portable — no installer, no registry writes
   beyond the Windows proxy settings it manages).
4. Run `AntLighting.exe`. Press **CONNECT**.

On the first run, if `xray.exe` is not next to the app, a banner offers to
download the official Xray-core release for you (with checksum verification).

### Option B — Run from source

```powershell
git clone https://github.com/amirmhmdglstan-stack/AntLighing-VPN.git
cd AntLighing-VPN
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

`run.py` just inserts the repository root into `sys.path` and launches the app —
no installation needed.

You still need the Xray core. Either let the app download it on first run, or
place `xray.exe` (plus `geoip.dat`, `geosite.dat`, `wintun.dll`) in the data
directory's `core/` folder or next to `run.py`.

---

## How it works (the "complex underneath")

```
  ┌──────────────┐  ┌──────────────────┐  ┌──────────────────┐
  │ Public config│→ │ Collector: fetch, │→ │  Local config DB  │
  │   sources    │  │ extract, parse,   │  │  (identity hash,  │
  │ (GitHub,     │  │ normalise, dedupe,│  │   source, latency │
  │  Telegram,   │  │ validate          │  │   & success stats)│
  │  HTTP, file) │  └──────────────────┘  └────────┬─────────┘
  └──────────────┘                                  │
                                                    ▼
  ┌──────────────┐  ┌──────────────────┐  ┌──────────────────┐
  │  QML UI      │← │  AppController   │← │  Tester + Ranker  │
  │  (one button)│  │  (state machine) │  │  (reliability >   │
  └──────────────┘  └────────┬─────────┘  │   raw latency)    │
                             │            └──────────────────┘
                             ▼
  ┌──────────────────────────────────────────────────────────┐
  │ CoreController → spawns xray.exe, generates config,       │
  │ monitors for crashes, reads its log, shuts down cleanly   │
  └──────────────────────────────────────────────────────────┘
                             │
                             ▼
  ┌──────────────────────────────────────────────────────────┐
  │ Routing:  system-proxy mode (default, no elevation)  or   │
  │           TUN mode (whole-device, requires elevation)     │
  └──────────────────────────────────────────────────────────┘
```

### The pipeline, step by step

1. **Collect.** Each enabled `ConfigSource` (GitHub repo, Telegram channel, HTTP
   URL, local file) is fetched independently and concurrently. One dead source
   never breaks the others; failures are recorded and shown in diagnostics.
2. **Extract.** Share-links are pulled out of any surrounding text/HTML
   (`vmess://`, `vless://`, `trojan://`, `ss://`, `ssr://`, `hysteria2://`).
3. **Parse & normalise.** A defensive, lenient parser turns each link into a
   canonical `ServerConfig`. Malformed links are discarded safely and counted,
   never crashed on. Keys are case-normalised, transport aliases are mapped
   (`tcp`→`raw`, `websocket`→`ws`, …).
4. **Dedupe.** Each server has a stable *identity hash* over the fields that
   define it (scheme, address, port, credential, transport, security). The same
   server from two sources is stored once.
5. **Validate.** Only links the bundled engine can actually speak are kept for
   testing. Unsupported protocols (`ssr`, `tuic`) are parsed but clearly marked
   *unsupported* and never selected.
6. **Test.** A tester probes each server through the real core with hard
   timeouts, watchdogs and cancellation. Results (working / failed / timeout,
   latency, timestamp) are written to the DB.
7. **Rank.** A scoring model that **weights reliability above raw latency**. A
   server that is a few milliseconds faster but keeps dying loses to a slightly
   slower stable server. Stale results decay toward "unknown" over time.
8. **Select & connect.** The controller generates an Xray JSON config, spawns
   `xray.exe` as a child process, verifies the local endpoint answers, then
   applies routing.

### Routing modes

| Mode | Elevation | What it does |
| --- | --- | --- |
| **System proxy** (default) | none | Runs Xray with local `http`+`socks` inbounds and sets the Windows system proxy (HKCU). Affects browsers and well-behaved apps. |
| **TUN (whole device)** | admin | Runs Xray with a `tun` inbound + `wintun.dll`, capturing all traffic including non-proxy-aware apps. Uses `0.0.0.0/1 + 128.0.0.0/1` routing (never `0.0.0.0/0`, to avoid the TUN↔uplink routing loop) and pins the uplink route. |

Both modes **bypass private/LAN ranges** so your local network keeps working, and
both tear down cleanly on disconnect, crash or exit — never leaving your network
broken. If the core crashes while connected, the controller detects it and
cleans up routing before reporting the failure.

---

## Supported protocols & transports

Parsed & selectable (when the bundled core supports them): **VLESS, VMess,
Trojan, Shadowsocks, Hysteria2**, over transports `raw/tcp`, `ws`, `grpc`,
`httpupgrade`, `xhttp/splithttp`, `kcp`, `h2/http`, `quic`, with security
`none/tls/reality`.

Parsed but **not selectable** (marked *unsupported*, because the bundled Xray
build does not speak them): **SSR, TUIC**.

## Default configuration sources

These are *user-editable defaults*, not hard dependencies. Any of them can
vanish and the app keeps working.

| Source | Kind | Enabled by default |
| --- | --- | --- |
| [0xRadikal/Free-v2ray-Configs](https://github.com/0xRadikal/Free-v2ray-Configs) `verified/configs.txt` | GitHub | ✅ |
| same repo, `all/configs.txt` | GitHub | ❌ (large, mostly duplicates) |
| Public Telegram channels `@irconfig @IRAN_V2RAY1 @spdnet @V2ray_Alpha @V2rayNG3` | Telegram | ✅ |

You can add, remove, enable/disable sources in **⚙ Advanced Settings → Sources**,
or import a paste of your own share-links.

---

## Error UX

AntLighting never fails silently:

* `No working servers found.` with a **[Try Again]** button after a connect attempt.
* `Couldn't find a working server.` with a **[Test Again]** button when the pool is empty/untested.
* Core-failure recovery: a crashed core is detected, routing is cleaned, and the UI
  returns to a consistent offline state.
* Offline-friendly: when the network is down the app does **not** hammer sources;
  it reuses the last-known-good pool and re-tests intelligently.
* Invalid configs are discarded safely and counted in diagnostics, never executed.

## Security posture

* Public configs are **untrusted input**. They are parsed defensively, validated,
  and never treated as executable code. The app never runs a downloaded file.
* The core runs as a separate child process with no special privileges.
* Secrets (passwords, UUIDs, keys) are **redacted from all logs**.
* No telemetry, no analytics, no silent uploads (see `THIRD_PARTY_LICENSES.md`).
* TUN mode requests elevation **only** when you enable it.

---

## Development

### Layout

```
antlighting/
  app.py            bootstrap (GUI + --selftest)
  controller.py     Qt-free application state machine
  collector.py      fetch/parse/dedupe/validate pipeline + scheduler
  configs/          models, parser, naming, ranking
  core/             base, xray backend, manager, downloader, config_builder
  net/              elevation, system_proxy, tunnel
  sources/          base, github, telegram, localfile/http
  storage/          sqlite config db + settings
  testing/          server tester (probes, watchdog, cancellation)
  ui/               QML interface + Python↔QML bridge + theme.js + mascot
tests/              pytest suite (parser, ranking, core, builder, tester,
                    collector, controller, net/storage, ui bridge, qml lint)
tools/              validate_configs_against_xray.py (real-core validation)
packaging/          PyInstaller spec, icon generator, build runtime staging
.github/workflows/  CI (tests+lint) and Build Windows app
```

### Run the tests

```powershell
pip install -r requirements-dev.txt
python -m pytest -q
```

The suite is fully offline and headless. It includes a **QML lint gate** that
runs Qt's own `qmllint` over every QML file, and a **bridge test** that drives
the real Python↔QML boundary via `QCoreApplication`.

### The self-test

`python -m antlighting --selftest` (or `AntLighting.exe --selftest`) builds the
whole app, loads the QML, instantiates the bridge, runs the bundled core, prints
a report and exits 0 on success. The Windows build workflow runs this against
the **packaged** executable and fails the build if it doesn't report success.

### Real-core validation

`tools/validate_configs_against_xray.py` forks a real Xray core per config and
records whether the engine accepts the generated JSON. In this project's CI-free
development it validated **450/450** generated configs against Xray.

---

## Building the Windows app

The GitHub Actions workflow **`.github/workflows/build-windows.yml`** does the
whole thing and uploads a downloadable artifact:

1. installs Python + dependencies,
2. runs the test suite and the QML lint,
3. downloads the official `Xray-windows-64.zip` (and licences),
4. freezes the app with **PyInstaller** (`packaging/AntLighting.spec`),
5. runs the packaged `AntLighting.exe --selftest` and a 20 s windowed launch check,
6. zips `dist/AntLighting` into `AntLighting-Windows-x64.zip`,
7. uploads it as an artifact, and publishes a **GitHub release** on `v*` tags.

### Build it locally (beginner, step by step)

1. Install Python 3.11 from python.org (tick *Add to PATH*).
2. Open PowerShell in the repo folder and run:
   ```powershell
   pip install -r requirements-dev.txt
   ```
3. Fetch the Xray core (or let the workflow/app do it): download
   `Xray-windows-64.zip` from the [Xray releases](https://github.com/XTLS/Xray-core/releases)
   and copy `xray.exe`, `geoip.dat`, `geosite.dat`, `wintun.dll` into
   `packaging\runtime\`.
4. Build the icon (optional but recommended):
   ```powershell
   python packaging\make_icon.py
   ```
5. Freeze the app:
   ```powershell
   python -m PyInstaller packaging\AntLighting.spec --noconfirm --clean
   ```
6. Copy the core next to the exe and run it:
   ```powershell
   copy packaging\runtime\* dist\AntLighting\
   dist\AntLighting\AntLighting.exe
   ```

The result is a portable folder you can zip and share.

---

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `xray.exe not found` on first run | Let the banner download it, or place it in the data dir's `core/` folder. |
| Connect fails with *No working servers* | Press **Test Again**; check your internet; try enabling more sources in Settings → Sources. |
| Browsers don't use the VPN | Some apps ignore the system proxy. Switch to **TUN mode** (needs admin). |
| Local network / printer stops working | It shouldn't (private ranges are bypassed). If it does, disable and re-enable, then report it. |
| Leftover proxy after a crash | The app cleans up on exit; if a crash left the system proxy set, run the app once and disconnect, or clear it in Windows proxy settings. |

---

## Licence & third-party components

The application source is **MIT** (see `LICENSE`). AntLighting bundles/downloads
separately-licensed components, most importantly **Xray-core (MPL-2.0)** and
**wintun.dll**, and links **Qt (LGPL)**. Full details, including what each
licence requires of you when redistributing, are in
[`THIRD_PARTY_LICENSES.md`](THIRD_PARTY_LICENSES.md).

No code was copied from the unlicensed configuration-collector repositories that
were surveyed during development; equivalent functionality was implemented
independently. The share-link URI formats are de-facto public standards.
