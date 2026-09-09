# Third-party components and licences

AntLighting is built on top of several open-source projects. This document lists
each one, its licence, and exactly how AntLighting uses it. The application
source code itself is MIT-licensed (see `LICENSE`).

| Component | Licence | How AntLighting uses it |
| --- | --- | --- |
| [Xray-core](https://github.com/XTLS/Xray-core) | MPL-2.0 | The proxy engine. AntLighting downloads the official prebuilt binary (`Xray-windows-64.zip`) and runs it as a separate child process. It is **not** linked or statically bundled into the Python code; it ships next to the executable together with its own licence file. |
| [wintun.dll](https://www.wintun.net/) | Proprietary (redistributable, bundled by Xray) | Windows TUN driver used only in TUN (whole-device) mode. Bundled by the official Xray release zip and shipped alongside it. |
| [geoip.dat / geosite.dat](https://github.com/v2fly/domain-list-community) | MIT (data) | Optional routing databases used only for the "bypass private & local networks" rules, and only when present. |
| [PySide6 / Qt](https://www.qt.io/) | LGPL-3.0 (Qt) / GPL-2.0-or-later (PySide6 bindings) | The GUI toolkit (Qt Quick + QML). PyInstaller links the Qt shared libraries dynamically, so the Qt libraries remain replaceable, as required by the LGPL. |
| [Python](https://www.python.org/) | PSF | The runtime. |
| [PyInstaller](https://www.pyinstaller.org/) | GPL-2.0-with-bootloader-exception | Used only at build time to freeze the app; its bootloader exception permits the produced executable. |
| [pytest](https://pytest.org/) | MIT | Used only for testing; not shipped. |

## Important notes

* **No code was copied** from the unlicensed configuration-collector
  repositories that were surveyed during development (`miladtahanian/Config-Collector`,
  `V2RayRoot/V2RayConfig`, `HojjatSabzali/Xray-Config-Tester`). They have no licence,
  so AntLighting implements equivalent functionality independently. The share-link
  URI formats (`vmess://`, `vless://`, `trojan://`, `ss://`) are de-facto public
  standards, not copyrightable code.

* **Xray-core is MPL-2.0.** When you redistribute AntLighting with the bundled
  `xray.exe`, you are also redistributing MPL-2.0 code. The Xray binary's own
  `LICENSE` file is included in every distribution (`dist/AntLighting/LICENSE*`).
  The MPL's file-level copyleft is satisfied because the binary is a separate,
  unmodified file.

* **Qt is LGPL.** The distributed application links Qt dynamically. You may
  replace the Qt shared libraries in the `AntLighting` folder with your own
  build of the same Qt version to relink, which satisfies the LGPL's
  reverse-engineering requirement for debugging modifications.

* **wintun.dll** is redistributed under the terms set by its publisher and is
  bundled by the official Xray release. It is used only to create the virtual
  network adapter in TUN mode and is never loaded otherwise.

## Telemetry

AntLighting contains **no telemetry, no analytics and no silent uploads**. The
only network requests it makes are:

1. to the public configuration sources you configure (to fetch server lists),
2. to the proxy servers you choose to connect through, and
3. to the official Xray release page, only when you press "download core" or
   when the build workflow bundles the core.

None of these transmit browsing history, DNS queries, URLs you visit, or any
personal information.
