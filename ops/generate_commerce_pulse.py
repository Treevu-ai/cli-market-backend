#!/usr/bin/env python3
"""Generate Agentic Commerce Pulse reports for publishing.

Usage:
  python3 ops/generate_commerce_pulse.py
  python3 ops/generate_commerce_pulse.py -c PE -c MX --markdown
  python3 ops/generate_commerce_pulse.py --all-latam --save
  python3 ops/generate_commerce_pulse.py --llm   # optional OpenAI polish
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from content_paths import metrics_dir  # noqa: E402
from market_pulse import generate_commerce_pulse  # noqa: E402
from monday import fetch_data  # noqa: E402

LATAM_DEFAULT = ("PE", "MX", "CL", "CO", "AR", "BR")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Agentic Commerce Pulse reports")
    parser.add_argument("-c", "--country", action="append", dest="countries", help="ISO country code")
    parser.add_argument("--all-latam", action="store_true", help="Generate for default LatAm set")
    parser.add_argument("-d", "--days", type=int, default=7)
    parser.add_argument("--lang", choices=("es", "en"), default="es")
    parser.add_argument("--save", action="store_true", help="Write markdown to metrics/")
    parser.add_argument("--markdown", action="store_true", help="Print markdown to stdout")
    parser.add_argument("--json", action="store_true", help="Print JSON to stdout")
    parser.add_argument("--llm", action="store_true", help="Optional LLM narrative (MARKET_PULSE_LLM=1)")
    args = parser.parse_args()

    countries = args.countries or (list(LATAM_DEFAULT) if args.all_latam else ["PE"])
    dashboard = fetch_data()
    outputs: list[dict] = []

    for cc in countries:
        pulse = generate_commerce_pulse(
            country=cc,
            days=args.days,
            lang=args.lang,
            dashboard=dashboard,
            llm=args.llm,
        )
        outputs.append(pulse)
        if args.save:
            metrics_dir().mkdir(parents=True, exist_ok=True)
            week = pulse.get("week", "unknown")
            path = metrics_dir() / f"commerce-pulse-{cc}-{week}.md"
            path.write_text(pulse.get("markdown", ""), encoding="utf-8")
            print(f"written: {path}")

    if args.markdown:
        for pulse in outputs:
            print(pulse.get("markdown", ""))
            print("\n---\n")
    elif args.json:
        slim = [{k: v for k, v in p.items() if k != "brief"} for p in outputs]
        print(json.dumps(slim if len(slim) > 1 else slim[0], indent=2, ensure_ascii=False))
    elif not args.save:
        for pulse in outputs:
            print(
                f"{pulse.get('country')} · {pulse.get('week')} · "
                f"publishable={pulse.get('publishable')} · {pulse.get('headline')}"
            )
            for h in pulse.get("executive_highlights") or []:
                print(f"  - {h}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
