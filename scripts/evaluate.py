#!/usr/bin/env python3
"""Run a deterministic lexical retrieval diagnostic over a generated bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urldefrag


ROOT = Path(__file__).resolve().parents[1]
SEARCH_CONTRACT = ROOT / "pages" / "search-contract.json"
SEARCH_INDEX_SCHEMA = "okf-hmlr-search-index.v1"
CATALOGUE_SCHEMA = "okf-hmlr-catalogue.v1"


def tokens(value: str, contract: dict) -> set[str]:
    found = set(re.findall(contract["token_pattern"], value.casefold()))
    return found - set(contract["stopwords"])


def fields_text(record: dict, fields: list[str]) -> str:
    values: list[str] = []
    for field in fields:
        value = record.get(field)
        if isinstance(value, list):
            values.extend(str(item) for item in value)
        elif value is not None:
            values.append(str(value))
    return " ".join(values)


def rank(query: str, records: list[dict], contract: dict) -> list[dict]:
    query_tokens = tokens(query, contract)
    weights = contract["weights"]
    scored = []
    for record in records:
        if isinstance(record.get("heading_tokens"), (list, str)) and isinstance(
            record.get("body_tokens"), (list, str)
        ):
            heading_tokens = (
                set(record["heading_tokens"].split())
                if isinstance(record["heading_tokens"], str)
                else set(record["heading_tokens"])
            )
            body_tokens = (
                set(record["body_tokens"].split())
                if isinstance(record["body_tokens"], str)
                else set(record["body_tokens"])
            )
        else:
            heading_tokens = tokens(
                fields_text(record, contract["heading_fields"]), contract
            )
            body_tokens = tokens(
                fields_text(record, contract["body_fields"]), contract
            )
        score = sum(
            weights["heading"]
            if token in heading_tokens
            else weights["body"]
            if token in body_tokens
            else 0
            for token in query_tokens
        )
        if score and record.get("curation") == "reviewed":
            score += weights["reviewed_curation_bonus"]
        if score:
            scored.append((score, record["title"].casefold(), record))
    scored.sort(key=lambda item: (-item[0], item[1], item[2]["id"]))
    return [item[2] for item in scored]


def canonical(url: str) -> str:
    clean, _fragment = urldefrag(url)
    return clean.rstrip("/")


def record_urls(record: dict) -> set[str]:
    if not isinstance(record.get("url"), str):
        return set()
    return {canonical(record["url"])}


def load_records(bundle: Path) -> tuple[list[dict], dict]:
    """Prefer the generated compact search index, with catalogue compatibility."""

    index_path = bundle / "data" / "search" / "index.json"
    if index_path.is_file():
        payload = json.loads(index_path.read_text(encoding="utf-8"))
        if payload.get("schema") != SEARCH_INDEX_SCHEMA:
            raise ValueError(f"unsupported compact search index: {index_path}")
        records = payload.get("records")
        if not isinstance(records, list):
            raise ValueError(
                f"compact search index lacks a records array: {index_path}"
            )
        for position, record in enumerate(records):
            if not isinstance(record, dict):
                raise ValueError(
                    f"compact search index record {position} is not an object"
                )
            for field in ("id", "title", "url", "heading_tokens", "body_tokens"):
                if field not in record:
                    raise ValueError(
                        f"compact search index record {position} lacks {field}"
                    )
            if not isinstance(record["heading_tokens"], (list, str)) or not isinstance(
                record["body_tokens"], (list, str)
            ):
                raise ValueError(
                    f"compact search index record {position} has invalid token arrays"
                )
        if payload.get("record_count", len(records)) != len(records):
            raise ValueError("compact search index record count does not reconcile")
        return records, {
            "kind": "compact-search-index",
            "path": "data/search/index.json",
            "schema": payload["schema"],
            "snapshot_id": payload.get("snapshot_id"),
        }

    catalogue_path = bundle / "data" / "catalogue.json"
    payload = json.loads(catalogue_path.read_text(encoding="utf-8"))
    if payload.get("schema") != CATALOGUE_SCHEMA:
        raise ValueError(f"unsupported catalogue fallback: {catalogue_path}")
    records = payload.get("records")
    if not isinstance(records, list):
        raise ValueError(f"catalogue fallback lacks a records array: {catalogue_path}")
    if payload.get("record_count", len(records)) != len(records):
        raise ValueError("catalogue fallback record count does not reconcile")
    return records, {
        "kind": "catalogue-fallback",
        "path": "data/catalogue.json",
        "schema": payload["schema"],
        "snapshot_id": payload.get("snapshot_id"),
    }


def evaluate_questions(
    questions: list[dict], records: list[dict], contract: dict, k: int
) -> tuple[list[dict], dict]:
    rows = []
    reciprocal_ranks = []
    successful_questions = 0
    matched_target_count = 0
    expected_target_count = 0
    for question in questions:
        ranked = rank(question["query"], records, contract)
        expected = {
            canonical(source["canonical_url"])
            for source in question["expected_sources"]
        }
        target_ranks: dict[str, int] = {}
        for index, record in enumerate(ranked, start=1):
            for matched_url in expected & record_urls(record):
                target_ranks.setdefault(matched_url, index)

        first_rank = min(target_ranks.values(), default=None)
        matched_at_k = {
            url for url, target_rank in target_ranks.items() if target_rank <= k
        }
        expected_source_success_at_k = bool(matched_at_k)
        successful_questions += int(expected_source_success_at_k)
        matched_target_count += len(matched_at_k)
        expected_target_count += len(expected)
        reciprocal_ranks.append(0 if first_rank is None else 1 / first_rank)
        rows.append(
            {
                "question_id": question["id"],
                "query": question["query"],
                "first_expected_rank": first_rank,
                "expected_source_success_at_k": expected_source_success_at_k,
                "expected_target_count": len(expected),
                "matched_expected_targets": sorted(matched_at_k),
                "expected_target_recall_at_k": (
                    len(matched_at_k) / len(expected) if expected else 0
                ),
                "top_urls": [record["url"] for record in ranked[:k]],
            }
        )

    count = len(rows)
    metrics = {
        "expected_source_success_at_k": (
            successful_questions / count if count else 0
        ),
        "expected_target_recall_at_k": (
            matched_target_count / expected_target_count
            if expected_target_count
            else 0
        ),
        "mean_reciprocal_rank": sum(reciprocal_ranks) / count if count else 0,
        "hard_failures_evaluated": False,
    }
    return rows, metrics


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=ROOT / "bundle")
    parser.add_argument(
        "--output", type=Path, default=ROOT / "evaluation" / "latest-report.json"
    )
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument(
        "--min-expected-source-success-at-k",
        "--min-recall-at-k",
        dest="min_expected_source_success_at_k",
        type=float,
        default=0.50,
        help=(
            "Minimum question-level source success for this retrieval diagnostic; "
            "--min-recall-at-k is retained as a compatibility alias"
        ),
    )
    args = parser.parse_args()

    questions = json.loads((ROOT / "evaluation" / "questions.json").read_text())
    records, record_source = load_records(args.bundle)
    bundle_snapshot = record_source["snapshot_id"]
    catalogue_path = args.bundle / "data" / "catalogue.json"
    if bundle_snapshot is None and catalogue_path.is_file():
        bundle_snapshot = json.loads(
            catalogue_path.read_text(encoding="utf-8")
        ).get("snapshot_id")
    contract_bytes = SEARCH_CONTRACT.read_bytes()
    contract = json.loads(contract_bytes)
    if contract.get("schema") != "okf-hmlr-search-contract.v1":
        raise SystemExit("unsupported search contract")
    rows, metrics = evaluate_questions(
        questions["questions"], records, contract, args.k
    )
    threshold_met = (
        metrics["expected_source_success_at_k"]
        >= args.min_expected_source_success_at_k
    )
    report = {
        "schema": "okf-hmlr-lexical-retrieval-diagnostic.v1",
        "status": "retrieval-diagnostic-not-independent-g5-acceptance",
        "g5_acceptance": "not-evaluated",
        "bundle_snapshot": bundle_snapshot,
        "record_source": record_source,
        "question_count": len(rows),
        "k": args.k,
        "search_contract": "pages/search-contract.json",
        "search_contract_sha256": hashlib.sha256(contract_bytes).hexdigest(),
        "metrics": metrics,
        "metric_definitions": {
            "expected_source_success_at_k": (
                "Fraction of questions retrieving at least one distinct expected "
                "canonical source URL in the top k."
            ),
            "expected_target_recall_at_k": (
                "Fraction of all distinct expected canonical target URLs across "
                "the suite retrieved in the top k."
            ),
            "mean_reciprocal_rank": (
                "Mean reciprocal rank of the first expected canonical source URL."
            ),
        },
        "diagnostic_thresholds": {
            "expected_source_success_at_k": args.min_expected_source_success_at_k,
        },
        "diagnostic_threshold_met": threshold_met,
        "limitations": [
            "This deterministic baseline is a retrieval diagnostic only.",
            "It is not an independent G5 acceptance evaluation and cannot satisfy G5.",
            "Expected propositions remain first-release candidates, not gold answers.",
            (
                "Accessibility, display, provenance semantics and hard failures "
                "require separate evaluation."
            ),
        ],
        "results": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["metrics"], sort_keys=True))
    return 0 if threshold_met else 1


if __name__ == "__main__":
    raise SystemExit(main())
