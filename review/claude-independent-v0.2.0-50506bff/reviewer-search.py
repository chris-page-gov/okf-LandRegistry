#!/usr/bin/env python3
"""Run reviewer-owned queries against the frozen candidate diagnostic index."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "candidate" / "scripts"))

import evaluate  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", action="append", required=True)
    parser.add_argument("--k", type=int, default=10)
    arguments = parser.parse_args()
    if arguments.k < 1 or arguments.k > 50:
        raise SystemExit("--k must be between 1 and 50")

    bundle = ROOT / "candidate" / "bundle"
    contract = json.loads(
        (ROOT / "candidate" / "pages" / "search-contract.json").read_text(
            encoding="utf-8"
        )
    )
    records, source = evaluate.load_records(bundle)
    results = []
    for query in arguments.query:
        ranked = evaluate.rank(query, records, contract)[: arguments.k]
        results.append(
            {
                "query": query,
                "k": arguments.k,
                "result_count": len(ranked),
                "results": [
                    {
                        "rank": rank,
                        "id": record["id"],
                        "title": record["title"],
                        "url": record["url"],
                        "kind": record.get("kind"),
                        "source_family": record.get("source_family"),
                        "authority_role": record.get("authority_role"),
                        "access_state": record.get("access_state"),
                        "licence_state": record.get("licence_state"),
                        "caveat_ids": record.get("caveat_ids", []),
                        "caveats": record.get("caveats", []),
                    }
                    for rank, record in enumerate(ranked, start=1)
                ],
            }
        )
    print(
        json.dumps(
            {
                "schema": "okf-hmlr-reviewer-search-results.v2",
                "warning": (
                    "This is the deterministic diagnostic baseline, not the "
                    "locked Explorer worker. Use it only for reviewer-owned "
                    "held-out exploration and inspect full record evidence."
                ),
                "bundle_release_root_sha256": evaluate.bundle_release_root(bundle),
                "record_source": source,
                "queries": results,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
