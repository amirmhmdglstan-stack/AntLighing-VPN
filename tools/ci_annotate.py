#!/usr/bin/env python3
"""Print pytest junit-xml failures as GitHub Actions annotations.

The sandboxed agent cannot download raw Actions step logs, but it *can* read
check-run annotations through the (reachable) GitHub API.  This turns every
failed/errored test into an ``::error`` annotation so the failure detail is
visible to the agent and to humans in the Actions UI.

Usage:  python tools/ci_annotate.py <junit-xml>
Always exits 0 (it is a reporter, not a gate).
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        return 0
    path = argv[1]
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:  # noqa: BLE001
        print(f"::warning::could not parse junit report {path}: {exc}")
        return 0

    printed = 0
    for case in root.iter("testcase"):
        name = case.get("name", "?")
        cls = case.get("classname", "?")
        for kind in ("failure", "error"):
            for node in case.iter(kind):
                message = (node.get("message") or "").strip().replace("\n", " ")[:500]
                text = (node.text or "").strip().replace("\n", " | ")[:500]
                detail = message or text or f"{kind}"
                print(f"::error title={cls}::{name}: {detail}")
                printed += 1

    summary = root if root.tag == "testsuite" else None
    if root.tag == "testsuites":
        summary = None
    if summary is not None:
        print(
            f"::notice::pytest summary: tests={summary.get('tests')} "
            f"failures={summary.get('failures')} errors={summary.get('errors')} "
            f"skipped={summary.get('skipped')}"
        )
    print(f"ci_annotate: emitted {printed} failure annotation(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
