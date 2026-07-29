#!/usr/bin/env python3
"""Run a deterministic lexical retrieval diagnostic over a generated bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path
from urllib.parse import urldefrag


ROOT = Path(__file__).resolve().parents[1]
SEARCH_CONTRACT = ROOT / "pages" / "search-contract.json"
SEARCH_INDEX_SCHEMA = "okf-hmlr-search-index.v1"
CATALOGUE_SCHEMA = "okf-hmlr-catalogue.v1"
ACCEPTANCE_REVIEW_SCHEMA = "okf-hmlr-evaluation-acceptance-review.v1"
RELEASE_ROOT_MARKER = "# release-root-sha256: "
SHA256 = re.compile(r"^[0-9a-f]{64}$")


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
    minimum_contract = contract.get("minimum_should_match", {})
    required_matches = 1
    if len(query_tokens) >= minimum_contract.get("apply_from_query_tokens", 3):
        required_matches = max(
            minimum_contract.get("minimum_matches", 2),
            math.ceil(
                len(query_tokens) * minimum_contract.get("ratio", 0.3)
            ),
        )
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
        matched_tokens = {
            token
            for token in query_tokens
            if token in heading_tokens or token in body_tokens
        }
        if len(matched_tokens) < required_matches:
            continue
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


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def bundle_release_root(bundle: Path) -> str:
    checksums = bundle / "CHECKSUMS.sha256"
    if not checksums.is_file():
        raise ValueError(f"bundle checksum manifest is missing: {checksums}")
    roots = [
        line.removeprefix(RELEASE_ROOT_MARKER)
        for line in checksums.read_text(encoding="utf-8").splitlines()
        if line.startswith(RELEASE_ROOT_MARKER)
    ]
    if len(roots) != 1 or SHA256.fullmatch(roots[0]) is None:
        raise ValueError("bundle checksum manifest lacks one valid release root")
    return roots[0]


def record_urls(record: dict) -> set[str]:
    values: list[str] = []
    if isinstance(record.get("url"), str):
        values.append(record["url"])
    equivalent_urls = record.get("equivalent_urls")
    if isinstance(equivalent_urls, list):
        values.extend(url for url in equivalent_urls if isinstance(url, str))
    return {canonical(url) for url in values}


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
            for field in (
                "id",
                "title",
                "url",
                "source_urls",
                "equivalent_urls",
                "heading_tokens",
                "body_tokens",
            ):
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
            if not isinstance(record["source_urls"], list) or any(
                not isinstance(url, str) for url in record["source_urls"]
            ):
                raise ValueError(
                    f"compact search index record {position} has invalid source routes"
                )
            if not isinstance(record["equivalent_urls"], list) or any(
                not isinstance(url, str) for url in record["equivalent_urls"]
            ):
                raise ValueError(
                    f"compact search index record {position} has invalid equivalent URLs"
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
    all_target_questions = 0
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
        all_expected_targets_at_k = bool(expected) and matched_at_k == expected
        successful_questions += int(expected_source_success_at_k)
        all_target_questions += int(all_expected_targets_at_k)
        matched_target_count += len(matched_at_k)
        expected_target_count += len(expected)
        reciprocal_ranks.append(0 if first_rank is None else 1 / first_rank)
        rows.append(
            {
                "question_id": question["id"],
                "query": question["query"],
                "first_expected_rank": first_rank,
                "expected_source_success_at_k": expected_source_success_at_k,
                "all_expected_targets_at_k": all_expected_targets_at_k,
                "expected_target_count": len(expected),
                "expected_target_ranks": {
                    url: target_ranks.get(url) for url in sorted(expected)
                },
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
        "all_expected_targets_success_at_k": (
            all_target_questions / count if count else 0
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


def validate_acceptance_review(
    review: dict,
    questions: list[dict],
    suite_sha256: str,
    release_root_sha256: str,
) -> dict:
    if review.get("schema") != ACCEPTANCE_REVIEW_SCHEMA:
        raise ValueError("unsupported evaluation acceptance-review schema")
    if review.get("status") != "pass":
        raise ValueError("evaluation acceptance review is not passed")
    if review.get("suite_sha256") != suite_sha256:
        raise ValueError("evaluation acceptance review does not match the question suite")
    if review.get("bundle_release_root_sha256") != release_root_sha256:
        raise ValueError("evaluation acceptance review does not match the bundle root")

    reviewer = review.get("reviewer")
    if not isinstance(reviewer, dict):
        raise ValueError("evaluation acceptance review lacks reviewer metadata")
    if reviewer.get("independent_of_retrieval_implementation") is not True:
        raise ValueError("evaluation reviewer independence is not recorded")
    for field in ("role", "kind", "reviewed_at"):
        if not isinstance(reviewer.get(field), str) or not reviewer[field].strip():
            raise ValueError(f"evaluation reviewer lacks {field}")

    expected_by_id = {
        question["id"]: set(question.get("hard_failure_ids", []))
        for question in questions
    }
    reviews = review.get("question_reviews")
    if not isinstance(reviews, list):
        raise ValueError("evaluation acceptance review lacks question reviews")
    review_by_id = {
        row.get("question_id"): row for row in reviews if isinstance(row, dict)
    }
    if set(review_by_id) != set(expected_by_id) or len(reviews) != len(review_by_id):
        raise ValueError("evaluation acceptance review question coverage is not exact")

    for question_id, hard_failure_ids in expected_by_id.items():
        row = review_by_id[question_id]
        for field in (
            "source_resolution",
            "expected_propositions_verified",
            "near_miss_rule_verified",
            "caveat_coverage",
        ):
            if row.get(field) is not True:
                raise ValueError(f"{question_id}: {field} is not passed")
        reviewed_ids = set(row.get("hard_failure_ids_reviewed", []))
        if reviewed_ids != hard_failure_ids:
            raise ValueError(f"{question_id}: hard-failure review coverage differs")
        if row.get("hard_failures_observed") != []:
            raise ValueError(f"{question_id}: a hard failure was observed")

    adversarial = review.get("held_out_adversarial")
    if not isinstance(adversarial, list) or len(adversarial) < 6:
        raise ValueError("evaluation acceptance review lacks a bounded adversarial pass")
    adversarial_ids = set()
    for row in adversarial:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            raise ValueError("invalid held-out adversarial review entry")
        adversarial_ids.add(row["id"])
        if row.get("status") != "pass" or row.get("new_critical_category") is not False:
            raise ValueError(f"{row['id']}: held-out adversarial review did not pass")
        if row.get("precision_acceptable") is not True:
            raise ValueError(
                f"{row['id']}: held-out adversarial precision is not accepted"
            )
        if row.get("safety_behavior_verified") is not True:
            raise ValueError(
                f"{row['id']}: held-out adversarial safety behavior is not verified"
            )
    if len(adversarial_ids) != len(adversarial):
        raise ValueError("duplicate held-out adversarial review ID")

    return {
        "reviewer": reviewer,
        "question_review_count": len(review_by_id),
        "held_out_adversarial_count": len(adversarial),
        "source_resolution_coverage": 1.0,
        "caveat_coverage": 1.0,
        "hard_failure_count": 0,
    }


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
    parser.add_argument(
        "--min-expected-target-recall-at-k",
        type=float,
        default=0.0,
        help="Minimum micro recall across distinct expected canonical target URLs.",
    )
    parser.add_argument(
        "--min-mrr",
        type=float,
        default=0.0,
        help="Minimum mean reciprocal rank of the first expected canonical source.",
    )
    parser.add_argument(
        "--min-all-expected-target-success-at-k",
        type=float,
        default=0.0,
        help=(
            "Minimum fraction of questions for which every declared expected "
            "canonical target or governed source alias is retrieved in the top k."
        ),
    )
    parser.add_argument(
        "--acceptance-review",
        type=Path,
        help=(
            "Digest-bound independent review required to evaluate hard failures "
            "and produce a formal G5 acceptance result."
        ),
    )
    args = parser.parse_args()

    questions_path = ROOT / "evaluation" / "questions.json"
    questions_bytes = questions_path.read_bytes()
    questions = json.loads(questions_bytes)
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
    metric_thresholds_met = (
        metrics["expected_source_success_at_k"]
        >= args.min_expected_source_success_at_k
        and metrics["expected_target_recall_at_k"]
        >= args.min_expected_target_recall_at_k
        and metrics["all_expected_targets_success_at_k"]
        >= args.min_all_expected_target_success_at_k
        and metrics["mean_reciprocal_rank"] >= args.min_mrr
    )
    acceptance = None
    if args.acceptance_review:
        try:
            review_bytes = args.acceptance_review.read_bytes()
            review = json.loads(review_bytes)
            acceptance = validate_acceptance_review(
                review,
                questions["questions"],
                sha256_bytes(questions_bytes),
                bundle_release_root(args.bundle),
            )
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise SystemExit(f"evaluation acceptance review failed closed: {exc}") from exc
        metrics.update(
            {
                "hard_failures_evaluated": True,
                "hard_failure_count": acceptance["hard_failure_count"],
                "source_resolution_coverage": acceptance[
                    "source_resolution_coverage"
                ],
                "caveat_coverage": acceptance["caveat_coverage"],
            }
        )
        acceptance["review_sha256"] = sha256_bytes(review_bytes)

    acceptance_met = metric_thresholds_met and acceptance is not None
    report = {
        "schema": (
            "okf-hmlr-evaluation-acceptance.v1"
            if acceptance is not None
            else "okf-hmlr-lexical-retrieval-diagnostic.v1"
        ),
        "status": (
            "pass"
            if acceptance_met
            else "fail"
            if acceptance is not None
            else "retrieval-diagnostic-not-independent-g5-acceptance"
        ),
        "g5_acceptance": (
            "pass"
            if acceptance_met
            else "fail"
            if acceptance is not None
            else "not-evaluated"
        ),
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
            "all_expected_targets_success_at_k": (
                "Fraction of questions retrieving every declared expected "
                "canonical target or governed source alias in the top k."
            ),
            "mean_reciprocal_rank": (
                "Mean reciprocal rank of the first expected canonical source URL."
            ),
        },
        "diagnostic_thresholds": {
            "expected_source_success_at_k": args.min_expected_source_success_at_k,
            "expected_target_recall_at_k": args.min_expected_target_recall_at_k,
            "mean_reciprocal_rank": args.min_mrr,
            "all_expected_targets_success_at_k": (
                args.min_all_expected_target_success_at_k
            ),
        },
        "diagnostic_threshold_met": metric_thresholds_met,
        "acceptance_review": acceptance,
        "limitations": (
            [
                "This deterministic baseline is a retrieval diagnostic only.",
                "It is not an independent G5 acceptance evaluation and cannot satisfy G5.",
                "Reviewed expected propositions are bounded acceptance expectations, not gold answers.",
                (
                    "Accessibility, display, provenance semantics and hard failures "
                    "require separate evaluation."
                ),
            ]
            if acceptance is None
            else [
                "This is an AI-assisted proof-of-concept evaluation, not participant research.",
                "The frozen snapshot and named canonical targets bound the result.",
                "Live publisher content can change after the recorded observation.",
            ]
        ),
        "results": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["metrics"], sort_keys=True))
    return 0 if (acceptance_met if acceptance is not None else metric_thresholds_met) else 1


if __name__ == "__main__":
    raise SystemExit(main())
