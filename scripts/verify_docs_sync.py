#!/usr/bin/env python3
"""Fail if stale Cloudflare-Pages claims remain; pass on GitHub-Pages stack.

Stdlib only. Fixed vs plan draft:
- Bug fix: plan used Path(a.args_file if hasattr(a, "args_file") else a.file);
  simplified to Path(args.file) (argparse dest is `file`).
- Stale `creditflow-4nu.pages.dev` history line (decommission note) is allowed;
  only live-stack claims FAIL. Checked per-line with HISTORY_HINTS.
- `dash.cloudflare.com.*Pages.*Connect to Git` matched as full regex (not prefix
  substring), so Workers-AI links (dash.cloudflare.com/profile) don't false-positive.
- REQUIRED entries with " or " pass if ANY alternative is present; base path
  accepts single or double quotes.
"""
import argparse
import re
import sys
from pathlib import Path

STALE_PATTERNS = [
    r"creditflow-4nu\.pages\.dev",
    r"Cloudflare Pages \(frontend\)",
    r"dash\.cloudflare\.com.*Pages.*Connect to Git",
    r"wrangler direct upload",
]

# Lines containing any of these hints are history notes, not live-stack claims.
HISTORY_HINTS = (
    "decommission",
    "decommissioned",
    "l\u1ecbch s\u1eed",
    "L\u1ecbch s\u1eed",
    "history",
    "History",
    "kh\u00f4ng c\u00f2n trong stack",
    "c\u0169",
)

REQUIRED = {
    "tasks/current.md": [
        "imtarget05.github.io/CreditFlow",
        "deploy-frontend-pages",
    ],
    "docs/deployment.md": [
        "imtarget05.github.io/CreditFlow",
        "deploy-frontend-pages",
        "ghcr.io",
        "base: '/CreditFlow/' or /CreditFlow/",
    ],
}


def is_history_line(line: str) -> bool:
    return any(h in line for h in HISTORY_HINTS)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    args = ap.parse_args()
    text = Path(args.file).read_text(encoding="utf-8")
    fails: list[str] = []
    for pat in STALE_PATTERNS:
        rx = re.compile(pat)
        for line in text.splitlines():
            if rx.search(line):
                if pat.startswith(r"creditflow-4nu") and is_history_line(line):
                    continue
                fails.append(f"STALE: {pat} :: {line.strip()[:160]}")
                break
    for need in REQUIRED.get(args.file, []):
        alts = need.split(" or ")
        if not any(alt in text for alt in alts):
            fails.append(f"MISSING: {need}")
    if fails:
        print("\n".join(fails))
        sys.exit(1)
    print(f"PASS: {args.file}")
    sys.exit(0)


if __name__ == "__main__":
    main()
