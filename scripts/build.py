#!/usr/bin/env python3
"""Build the HM Land Registry metadata-only OKF Bundle.

The build is deliberately offline. Network acquisition is a separate,
reviewable step implemented by ``scripts/acquire.py``. This script consumes a
frozen public-metadata snapshot plus curated control records; it never calls an
HMLR service and never reads credentials.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urldefrag, urljoin, urlparse


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "bundle"
PUBLICATION_BASE = "https://chris-page-gov.github.io/okf-LandRegistry/"
BUILD_VERSION = "0.1.0"
RESEARCH_CUTOFF = "2026-07-29"
SHARD_SIZE = 250
GENERATED_MARKER = ".okf-generated"
SENSITIVE_QUERY_KEYS = {
    "access_token",
    "api-key",
    "api_key",
    "apikey",
    "auth",
    "authorization",
    "expires",
    "key",
    "password",
    "secret",
    "sig",
    "signature",
    "token",
    "x-amz-credential",
    "x-amz-signature",
}
PUBLIC_SOURCE_HOSTS = {
    "api.github.com",
    "businessgateway.landregistry.gov.uk",
    "customerhelp.landregistry.gov.uk",
    "digitalarchives.landregistry.gov.uk",
    "fee-calculator.landregistry.gov.uk",
    "github.com",
    "hmlandregistry.blog.gov.uk",
    "landregistry.data.gov.uk",
    "landregistry.github.io",
    "propertyalert.landregistry.gov.uk",
    "search-local-land-charges.service.gov.uk",
    "use-land-property-data.service.gov.uk",
    "www.data.gov.uk",
    "www.gov.uk",
    "www.legislation.gov.uk",
    "www.nationalarchives.gov.uk",
}
RIGHTS_BY_SOURCE_FAMILY = {
    "govuk-search": "RIGHT-GOVUK",
    "govuk-content": "RIGHT-GOVUK",
    "govuk-hmlr": "RIGHT-GOVUK",
    "blog": "RIGHT-GOVUK",
    "cross-government-data-catalogues": "RIGHT-GOVUK",
    "ulpd": "RIGHT-DATASETS",
    "ulpd-api": "RIGHT-DATASETS",
    "linked-data": "RIGHT-DATASETS",
    "business-gateway-docs": "RIGHT-RESTRICTED",
    "property-information": "RIGHT-RESTRICTED",
    "local-land-charges": "RIGHT-RESTRICTED",
    "portal": "RIGHT-RESTRICTED",
    "github": "RIGHT-GITHUB",
    "cddo-api-catalogue": "RIGHT-CDDO",
    "customer-help": "RIGHT-PERSONAL",
}
EVIDENCE_BY_SOURCE_FAMILY = {
    "govuk-search": ["EV-GOVUK-SEARCH", "EV-ACQUISITION-SNAPSHOT"],
    "govuk-content": ["EV-GOVUK-SEARCH"],
    "govuk-hmlr": ["EV-HMLR-ORG"],
    "blog": ["EV-BLOG"],
    "cross-government-data-catalogues": ["EV-INVENTORY", "EV-DCAT"],
    "ulpd": ["EV-ULPD"],
    "ulpd-api": ["EV-ULPD-API"],
    "linked-data": ["EV-LINKED-DATA"],
    "business-gateway-docs": ["EV-BG-DOCS"],
    "property-information": ["EV-PROPERTY-SERVICE"],
    "local-land-charges": ["EV-LLC-PROGRAMME", "EV-LLC-TERMS"],
    "portal": ["EV-PRO-GUIDANCE"],
    "github": ["EV-GITHUB", "EV-ACQUISITION-SNAPSHOT"],
    "cddo-api-catalogue": ["EV-CDDO", "EV-ACQUISITION-SNAPSHOT"],
    "customer-help": ["EV-CUSTOMER-HELP", "EV-PERSONAL-INFO"],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot-dir",
        type=Path,
        help="Frozen acquisition directory; defaults to the newest source/snapshots child.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Generated bundle directory (default: bundle).",
    )
    parser.add_argument(
        "--publication-base",
        default=PUBLICATION_BASE,
        help="Absolute Pages base URL ending in '/'.",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Replace an existing generated output directory.",
    )
    return parser.parse_args()


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def load_build_config() -> dict[str, Any]:
    path = ROOT / "source" / "build-config.json"
    config = load_json(path)
    required = ("generated_at", "status", "version")
    missing = [key for key in required if not clean_text(config.get(key))]
    if missing:
        raise ValueError(f"source/build-config.json lacks {', '.join(missing)}")
    allowed_statuses = {"reviewed-scaffold-not-approved", "released-poc"}
    if config["status"] not in allowed_statuses:
        raise ValueError(f"unsupported build status: {config['status']!r}")
    if config["status"] == "released-poc":
        if not clean_text(config.get("release_at")):
            raise ValueError("a released proof of concept requires release_at")
        if config.get("ai_generated_proof_of_concept") is not True:
            raise ValueError(
                "a released proof of concept requires the AI-generation disclosure"
            )
        profile = load_json(ROOT / "domain-profile" / "domain-profile.json")
        release_decisions = [
            row
            for row in profile.get("decisions", [])
            if row.get("id") == "DEC-RELEASE"
        ]
        if (
            profile.get("status") != "approved"
            or len(release_decisions) != 1
            or release_decisions[0].get("status") != "accepted"
        ):
            raise ValueError("released output requires an approved domain profile")
        requirements = load_json(ROOT / "governance" / "requirements.json")
        rights = load_json(ROOT / "governance" / "rights-review.json")
        if (
            requirements.get("release_approved") is not True
            or rights.get("release_approved") is not True
        ):
            raise ValueError("released output requires approved governance and rights")
    return config


def load_ai_model_usage(config: dict[str, Any]) -> dict[str, Any]:
    path = ROOT / "governance" / "ai-model-usage.json"
    ledger = load_json(path)
    if ledger.get("schema") != "okf-hmlr-ai-model-usage.v1":
        raise ValueError("AI model-usage ledger has an unsupported schema")
    if ledger.get("release_version") != config["version"]:
        raise ValueError("AI model-usage ledger and build version differ")

    scope = ledger.get("measurement_scope")
    if not isinstance(scope, dict) or not clean_text(scope.get("id")):
        raise ValueError("AI model-usage ledger lacks a measurement scope")
    pre_tracking = scope.get("pre_tracking_usage")
    if not isinstance(pre_tracking, dict) or pre_tracking.get("status") != "unavailable":
        raise ValueError("pre-tracking AI usage must remain explicitly unavailable")
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        if pre_tracking.get(key) is not None:
            raise ValueError("unavailable pre-tracking token counts must be null")

    sessions = ledger.get("model_sessions")
    if not isinstance(sessions, list) or not sessions:
        raise ValueError("AI model-usage ledger requires at least one model session")
    allowed_measurement_states = {
        "pending-candidate-freeze",
        "partially-measured",
        "measured",
        "unavailable",
    }
    for session in sessions:
        if not isinstance(session, dict) or not clean_text(session.get("id")):
            raise ValueError("AI model-usage session lacks an ID")
        if session.get("measurement_status") not in allowed_measurement_states:
            raise ValueError("AI model-usage session has an unsupported status")
        values = [
            session.get("measured_input_tokens"),
            session.get("measured_output_tokens"),
            session.get("measured_total_tokens"),
        ]
        if any(
            value is not None
            and (isinstance(value, bool) or not isinstance(value, int) or value < 0)
            for value in values
        ):
            raise ValueError("measured AI token counts must be null or non-negative integers")
        if all(value is not None for value in values) and values[0] + values[1] != values[2]:
            raise ValueError("measured AI input and output tokens do not equal total")

    costs = ledger.get("cost_accounting")
    if not isinstance(costs, dict):
        raise ValueError("AI model-usage ledger lacks cost accounting")
    subscription = costs.get("subscription_fee_allocation")
    if (
        not isinstance(subscription, dict)
        or subscription.get("status") != "unavailable"
        or subscription.get("amount") is not None
    ):
        raise ValueError("subscription allocation must be unavailable and null")
    separately_billed = costs.get("separately_billed_openai_api")
    amount = (
        separately_billed.get("amount")
        if isinstance(separately_billed, dict)
        else None
    )
    if (
        isinstance(amount, bool)
        or not isinstance(amount, (int, float))
        or amount < 0
        or not clean_text(separately_billed.get("scope"))
    ):
        raise ValueError("separately billed API cost requires a scoped non-negative amount")
    equivalent = costs.get("rate_card_equivalent")
    if (
        not isinstance(equivalent, dict)
        or equivalent.get("status") != "unavailable"
        or equivalent.get("amount") is not None
        or equivalent.get("rate_card_source") is not None
    ):
        raise ValueError("rate-card equivalent must be unavailable without a source")
    return ledger


def ai_usage_projection(
    ledger: dict[str, Any], source_path: Path
) -> dict[str, Any]:
    return {
        "schema": "okf-hmlr-ai-usage-projection.v1",
        "source": {
            "path": source_path.relative_to(ROOT).as_posix(),
            "sha256": sha256_file(source_path),
        },
        "ledger": ledger,
    }


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(value))


def write_compact_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )


def canonical_profile_sha256() -> str:
    profile = load_json(ROOT / "domain-profile" / "domain-profile.json")
    payload = (
        json.dumps(profile, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    return sha256_bytes(payload)


def profile_pack_root_sha256() -> str:
    text = (ROOT / "domain-profile" / "CHECKSUMS.sha256").read_text(encoding="utf-8")
    matches = re.findall(r"^# pack-root-sha256: ([0-9a-f]{64})$", text, re.MULTILINE)
    if len(matches) != 1:
        raise ValueError("domain profile pack checksum lacks one exact pack root")
    return matches[0]


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split())


def string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        values = value
    elif isinstance(value, tuple):
        values = list(value)
    else:
        values = [value]
    rendered = sorted(
        {clean_text(item) for item in values if clean_text(item)},
        key=lambda item: (item.casefold(), item),
    )
    by_casefold: dict[str, str] = {}
    for item in rendered:
        by_casefold.setdefault(item.casefold(), item)
    return [by_casefold[key] for key in sorted(by_casefold)]


def ordered_string_list(value: Any) -> list[str]:
    """Deduplicate ordered prose where the first item has display priority."""

    if value is None:
        return []
    values = value if isinstance(value, (list, tuple)) else [value]
    rendered: list[str] = []
    seen: set[str] = set()
    for item in values:
        cleaned = clean_text(item)
        key = cleaned.casefold()
        if cleaned and key not in seen:
            rendered.append(cleaned)
            seen.add(key)
    return rendered


def authority_tier(value: Any, source_family: str) -> str:
    tier = clean_text(value)
    if tier in {"A", "B", "C"}:
        return tier
    if tier in {"publisher-authoritative", "normative-authority"}:
        return "A"
    if tier == "official-reference":
        return "B" if source_family == "github" else "C"
    return "unassessed"


def stable_id(prefix: str, identity: str) -> str:
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}:{digest}"


def ensure_https(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"https"} or not parsed.netloc:
        raise ValueError(f"public record URL must be absolute HTTPS: {url!r}")
    if parsed.username or parsed.password:
        raise ValueError(f"credentials are forbidden in URLs: {url!r}")
    host = (parsed.hostname or "").casefold()
    if host not in PUBLIC_SOURCE_HOSTS:
        raise ValueError(f"public record host is outside the reviewed allowlist: {url!r}")
    query_keys = {key.casefold() for key, _value in parse_qsl(parsed.query)}
    sensitive = query_keys & SENSITIVE_QUERY_KEYS
    if sensitive:
        raise ValueError(
            f"sensitive query parameter(s) {sorted(sensitive)} are forbidden: {url!r}"
        )
    return urldefrag(url).url


def source_controls() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    source_path = ROOT / "source" / "source-register.json"
    rights_path = ROOT / "governance" / "rights-review.json"
    source_payload = load_json(source_path)
    rights_payload = load_json(rights_path)
    source_rows = source_payload.get("source_families")
    rights_rows = rights_payload.get("assessments")
    if not isinstance(source_rows, list) or not isinstance(rights_rows, list):
        raise ValueError("source and rights registers must contain arrays")
    sources = {clean_text(row.get("id")): row for row in source_rows}
    rights = {clean_text(row.get("id")): row for row in rights_rows}
    if "" in sources or len(sources) != len(source_rows):
        raise ValueError("source-register IDs must be non-empty and unique")
    if "" in rights or len(rights) != len(rights_rows):
        raise ValueError("rights assessment IDs must be non-empty and unique")
    if set(sources) != set(RIGHTS_BY_SOURCE_FAMILY):
        missing = sorted(set(sources) ^ set(RIGHTS_BY_SOURCE_FAMILY))
        raise ValueError(f"rights mapping and source register differ: {missing}")
    if set(sources) != set(EVIDENCE_BY_SOURCE_FAMILY):
        missing = sorted(set(sources) ^ set(EVIDENCE_BY_SOURCE_FAMILY))
        raise ValueError(f"evidence mapping and source register differ: {missing}")
    missing_rights = sorted(set(RIGHTS_BY_SOURCE_FAMILY.values()) - set(rights))
    if missing_rights:
        raise ValueError(f"rights assessments are missing: {missing_rights}")
    return sources, rights


def authority_role(tier: str) -> str:
    return {
        "A": "publisher-authoritative-source",
        "B": "official-operational-source",
        "C": "official-discovery-reference",
    }.get(tier, "unassessed-source")


def normal_record(record: dict[str, Any]) -> dict[str, Any]:
    required = ("id", "title", "url", "record_type", "source_family")
    missing = [key for key in required if not clean_text(record.get(key))]
    if missing:
        raise ValueError(f"record is missing {', '.join(missing)}: {record!r}")

    source_urls = string_list(record.get("source_urls"))
    canonical_url = ensure_https(clean_text(record["url"]))
    if canonical_url not in source_urls:
        source_urls.insert(0, canonical_url)
    source_urls = [ensure_https(url) for url in source_urls]
    equivalent_urls = [
        ensure_https(url) for url in string_list(record.get("equivalent_urls"))
    ]
    equivalent_urls = [
        url
        for url in equivalent_urls
        if url.rstrip("/") != canonical_url.rstrip("/")
    ]

    source_family = clean_text(record["source_family"])
    normalized = {
        "id": clean_text(record["id"]),
        "title": clean_text(record["title"]),
        "description": clean_text(record.get("description")),
        "url": canonical_url,
        "publisher": clean_text(record.get("publisher")) or "HM Land Registry",
        "authority_tier": authority_tier(record.get("authority_tier"), source_family),
        "record_type": clean_text(record["record_type"]),
        "source_family": source_family,
        "jurisdiction": clean_text(record.get("jurisdiction"))
        or "Source-specific; HM Land Registry normally covers England and Wales",
        "audience": string_list(record.get("audience")),
        "access_model": clean_text(record.get("access_model")) or "check-source",
        "authentication": clean_text(record.get("authentication")) or "check-source",
        "licence": clean_text(record.get("licence")) or "check-source",
        "cadence": clean_text(record.get("cadence")) or "not stated",
        "formats": string_list(record.get("formats")),
        "topics": string_list(record.get("topics")),
        "languages": string_list(record.get("language") or record.get("languages")),
        "curation": clean_text(record.get("curation")) or "source-native",
        "lifecycle_state": clean_text(record.get("lifecycle_state")) or "unknown",
        "publisher_last_updated": clean_text(record.get("publisher_last_updated")) or None,
        "observed_at": clean_text(record.get("observed_at"))
        or f"{RESEARCH_CUTOFF}T00:00:00Z",
        "caveats": ordered_string_list(record.get("caveats")),
        "source_urls": sorted(set(source_urls)),
        "equivalent_urls": sorted(set(equivalent_urls)),
    }
    return normalized


def normalize_govuk(item: dict[str, Any], observed_at: str) -> dict[str, Any]:
    link = clean_text(item.get("link"))
    if not link:
        raise ValueError("GOV.UK result lacks link")
    url = urljoin("https://www.gov.uk/", link)
    identity = clean_text(item.get("content_id")) or url
    content_type = (
        clean_text(item.get("content_store_document_type"))
        or clean_text(item.get("format"))
        or "govuk-content"
    )
    organisations = item.get("organisations") or []
    publisher = "HM Land Registry"
    if isinstance(organisations, list) and organisations:
        first = organisations[0]
        if isinstance(first, dict):
            publisher = clean_text(first.get("title")) or publisher
        elif isinstance(first, str):
            publisher = clean_text(first) or publisher
    caveats = [
        "Search metadata is a discovery record, not the full document or legal advice.",
        "Publisher modification time is not dataset release, registration or legal currency.",
    ]
    boundary_text = (
        f"{clean_text(item.get('title'))} {clean_text(item.get('description'))}"
    ).casefold()
    if "boundar" in boundary_text:
        caveats.insert(
            0,
            (
                "Most registered title plans show general boundaries; an exact "
                "or determined boundary requires the applicable official process "
                "and evidence. This metadata record is not a boundary conclusion."
            ),
        )
    return normal_record(
        {
            "id": f"govuk:{identity}",
            "title": item.get("title"),
            "description": item.get("description"),
            "url": url,
            "publisher": publisher,
            "authority_tier": "A",
            "record_type": content_type,
            "source_family": "govuk-search",
            "jurisdiction": "Source-specific; HM Land Registry normally covers England and Wales",
            "audience": [],
            "access_model": "public-web",
            "authentication": "none for this publication metadata",
            "licence": "check source-specific Crown copyright and reuse terms",
            "cadence": "source-specific",
            "formats": ["HTML"],
            "topics": [content_type.replace("_", " ")],
            "publisher_last_updated": item.get("public_timestamp"),
            "observed_at": observed_at,
            "caveats": caveats,
            "source_urls": [url],
        }
    )


def normalize_github(item: dict[str, Any], observed_at: str) -> dict[str, Any]:
    url = clean_text(item.get("html_url"))
    full_name = clean_text(item.get("full_name")) or url
    licence = item.get("license")
    if isinstance(licence, dict):
        licence_text = clean_text(licence.get("spdx_id") or licence.get("name"))
    else:
        licence_text = ""
    topics = string_list(item.get("topics"))
    language = clean_text(item.get("language"))
    if language:
        topics.append(language)
    if item.get("archived"):
        topics.append("archived")
    return normal_record(
        {
            "id": f"github:{full_name}",
            "title": item.get("name") or full_name,
            "description": item.get("description"),
            "url": url,
            "publisher": "HM Land Registry",
            "authority_tier": "B",
            "record_type": "software-repository",
            "source_family": "github",
            "jurisdiction": "technical source; jurisdiction is project-specific",
            "audience": ["developer"],
            "access_model": "public-repository",
            "authentication": "none for public metadata",
            "licence": licence_text or "not declared in repository metadata",
            "cadence": "event-driven",
            "formats": ["Git"],
            "topics": sorted(set(topics), key=str.casefold),
            "lifecycle_state": "archived" if item.get("archived") else "active",
            "publisher_last_updated": item.get("updated_at"),
            "observed_at": observed_at,
            "caveats": [
                "An official-organisation repository may be experimental, archived or superseded.",
                "Repository metadata is operational evidence, not HMLR policy or legal advice.",
            ],
            "source_urls": [url],
        }
    )


def normalize_cddo(item: dict[str, Any], observed_at: str) -> dict[str, Any]:
    url = clean_text(item.get("url"))
    name = clean_text(item.get("name")) or url
    documentation = clean_text(item.get("documentation"))
    source_urls = [url]
    if documentation.startswith("https://"):
        source_urls.append(documentation)
    return normal_record(
        {
            "id": stable_id("cddo-api", url or name),
            "title": name,
            "description": item.get("description"),
            "url": url,
            "publisher": "HM Land Registry",
            "authority_tier": "C",
            "record_type": "api-catalogue-record",
            "source_family": "cddo-api-catalogue",
            "jurisdiction": item.get("areaServed")
            or "Source-specific; HM Land Registry normally covers England and Wales",
            "audience": ["developer"],
            "access_model": "check publisher-operated contract",
            "authentication": "check publisher-operated contract",
            "licence": item.get("license") or "not stated",
            "cadence": "catalogue-maintained",
            "formats": ["API"],
            "topics": ["API", "discovery catalogue"],
            "publisher_last_updated": item.get("dateUpdated"),
            "observed_at": observed_at,
            "caveats": [
                "CDDO catalogue metadata is a discovery seed, not the operational API contract.",
                "Verify status, version, authentication and rights against publisher-operated documentation.",
            ],
            "source_urls": source_urls,
        }
    )


def newest_snapshot() -> Path | None:
    snapshots = ROOT / "source" / "snapshots"
    if not snapshots.exists():
        return None
    candidates = sorted(path for path in snapshots.iterdir() if path.is_dir())
    return candidates[-1] if candidates else None


def snapshot_records(snapshot_dir: Path | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if snapshot_dir is None:
        return [], {
            "snapshot_id": "curated-scaffold-only",
            "observed_at": f"{RESEARCH_CUTOFF}T00:00:00Z",
            "mode": "curated-scaffold-only",
            "source_manifest_sha256": None,
            "lanes": {},
            "files": [],
        }
    snapshot_dir = snapshot_dir.resolve()
    if not snapshot_dir.is_dir():
        raise ValueError(f"snapshot directory does not exist: {snapshot_dir}")
    snapshots_root = (ROOT / "source" / "snapshots").resolve()
    if snapshots_root not in snapshot_dir.parents:
        raise ValueError("snapshot directory must be under source/snapshots")
    manifest_path = snapshot_dir / "manifest.json"
    if not manifest_path.is_file():
        raise ValueError(f"snapshot manifest is missing: {manifest_path}")
    manifest = load_json(manifest_path)
    observed_at = clean_text(manifest.get("observed_at"))
    if not observed_at:
        raise ValueError("snapshot manifest lacks observed_at")

    records: list[dict[str, Any]] = []
    adapters = (
        ("govuk-search.json", "govuk_search", "govuk-search", normalize_govuk),
        (
            "github-repositories.json",
            "github_repositories",
            "github",
            normalize_github,
        ),
        (
            "cddo-api-catalogue.json",
            "cddo_api_catalogue",
            "cddo-api-catalogue",
            normalize_cddo,
        ),
    )
    file_rows = manifest.get("files")
    if not isinstance(file_rows, list):
        raise ValueError("snapshot manifest files must be an array")
    by_path = {clean_text(row.get("path")): row for row in file_rows}
    if "" in by_path or len(by_path) != len(file_rows):
        raise ValueError("snapshot manifest file paths must be non-empty and unique")
    expected_paths = {row[0] for row in adapters}
    if set(by_path) != expected_paths:
        raise ValueError(
            f"snapshot manifest file set differs: {sorted(set(by_path) ^ expected_paths)}"
        )
    totals = manifest.get("totals")
    if not isinstance(totals, dict):
        raise ValueError("snapshot manifest totals must be an object")
    manifest_sources = manifest.get("sources")
    if clean_text(manifest.get("schema")).endswith(".v2"):
        terminal = manifest.get("terminal_outcome")
        acquirer = manifest.get("acquirer")
        if (
            not isinstance(manifest_sources, dict)
            or set(manifest_sources) != {row[1] for row in adapters}
            or not isinstance(terminal, dict)
            or terminal.get("status") != "complete"
            or not isinstance(acquirer, dict)
            or acquirer.get("name") != "scripts/acquire.py"
            or acquirer.get("version") != "0.2"
            or acquirer.get("sha256")
            != sha256_file(ROOT / "scripts" / "acquire.py")
        ):
            raise ValueError(
                "v2 snapshot manifest lacks complete outcomes or exact acquirer provenance"
            )

    lanes: dict[str, dict[str, Any]] = {}
    validated_files: list[dict[str, Any]] = []
    for filename, total_key, source_family, adapter in adapters:
        path = snapshot_dir / filename
        if not path.is_file():
            raise ValueError(f"required frozen snapshot file is missing: {path}")
        if path.is_symlink():
            raise ValueError(f"snapshot files must not be symlinks: {path}")
        receipt = by_path[filename]
        expected_bytes = receipt.get("byte_count", receipt.get("bytes"))
        if expected_bytes != path.stat().st_size:
            raise ValueError(f"{filename} byte count differs from its manifest")
        expected_sha = clean_text(receipt.get("sha256"))
        actual_sha = sha256_file(path)
        if expected_sha != actual_sha:
            raise ValueError(f"{filename} SHA-256 differs from its manifest")
        payload = load_json(path)
        items = payload.get("results")
        if not isinstance(items, list):
            raise ValueError(f"{filename} results must be an array")
        declared_total = payload.get("total")
        if declared_total != len(items):
            raise ValueError(f"{filename} total does not match its results array")
        if receipt.get("record_count") != len(items):
            raise ValueError(f"{filename} record count differs from its manifest")
        if totals.get(total_key) != len(items):
            raise ValueError(f"{filename} total differs from manifest totals")
        if clean_text(payload.get("observed_at")) != observed_at:
            raise ValueError(f"{filename} observed_at differs from its manifest")
        if clean_text(manifest.get("schema")).endswith(".v2"):
            required_envelope = (
                "request_url",
                "final_url",
                "retrieved_at",
                "http_status",
                "media_type",
                "request_receipts",
                "terminal_outcome",
            )
            missing = [key for key in required_envelope if key not in payload]
            if missing:
                raise ValueError(f"{filename} lacks v2 provenance: {missing}")
            if payload["http_status"] != 200:
                raise ValueError(f"{filename} did not terminate with HTTP 200")
            if not isinstance(payload["request_receipts"], list) or not payload[
                "request_receipts"
            ]:
                raise ValueError(f"{filename} has no request receipts")
            terminal = payload["terminal_outcome"]
            if (
                not isinstance(terminal, dict)
                or terminal.get("status") != "complete"
                or terminal.get("record_count") != len(items)
            ):
                raise ValueError(f"{filename} lacks a reconciled terminal outcome")
            manifest_source = manifest_sources[total_key]
            for key in required_envelope:
                if manifest_source.get(key) != payload.get(key):
                    raise ValueError(
                        f"{filename} {key} differs from manifest source receipt"
                    )
        records.extend(adapter(item, observed_at) for item in items)
        terminal = payload.get("terminal_outcome")
        lanes[source_family] = {
            "expected": len(items),
            "acquired": len(items),
            "errors": 0,
            "terminal_outcome": terminal if isinstance(terminal, dict) else {
                "success": len(items),
                "error": 0,
            },
        }
        validated_files.append(
            {
                "path": f"source/snapshots/{snapshot_dir.name}/{filename}",
                "bytes": path.stat().st_size,
                "sha256": actual_sha,
                "record_count": len(items),
            }
        )
    if clean_text(manifest.get("schema")).endswith(".v2"):
        if manifest["terminal_outcome"].get("record_count") != len(records):
            raise ValueError("snapshot terminal total does not reconcile")
    return records, {
        "snapshot_id": clean_text(manifest.get("snapshot_id")) or snapshot_dir.name,
        "observed_at": observed_at,
        "mode": "frozen-public-metadata",
        "source_manifest_sha256": sha256_file(manifest_path),
        "manifest_path": f"source/snapshots/{snapshot_dir.name}/manifest.json",
        "lanes": lanes,
        "files": validated_files,
    }


def curated_records() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = ROOT / "source" / "curated-records.json"
    if not path.is_file():
        raise ValueError(f"curated source file is missing: {path}")
    payload = load_json(path)
    records = payload.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError("curated-records.json must contain a non-empty records array")
    normalized = [normal_record(record) for record in records]
    for record in normalized:
        record["curation"] = "reviewed"
    return normalized, {
        "sha256": sha256_file(path),
        "record_count": len(normalized),
        "observed_at": clean_text(payload.get("observed_at")),
    }


def govern_record(
    record: dict[str, Any],
    sources: dict[str, dict[str, Any]],
    rights: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    governed = dict(record)
    family_id = governed["source_family"]
    family = sources.get(family_id)
    if family is None:
        raise ValueError(f"record uses unknown source family: {family_id}")
    rights_ref = RIGHTS_BY_SOURCE_FAMILY[family_id]
    assessment = rights[rights_ref]
    governed.update(
        {
            "access_state": clean_text(family.get("access_state")) or "unknown",
            "rights_state": clean_text(family.get("rights_state")) or "unknown",
            "rights_ref": rights_ref,
            "authority_role": authority_role(governed["authority_tier"]),
            "derivation": (
                "reviewed-curated-metadata"
                if governed["curation"] == "reviewed"
                else "normalized-frozen-source-metadata"
            ),
            "source_native_ids": [governed["id"]],
            "source_families": [family_id],
            "evidence_refs": EVIDENCE_BY_SOURCE_FAMILY[family_id],
        }
    )
    if assessment.get("status") not in {"permitted", "conditional", "prohibited"}:
        raise ValueError(f"rights assessment {rights_ref} has an unsupported status")
    if governed["access_state"] == "unknown" or governed["rights_state"] == "unknown":
        raise ValueError(f"record rights fail closed: {governed['id']}")
    return governed


def merge_records(
    discovered: Iterable[dict[str, Any]], curated: Iterable[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    all_records = [*discovered, *curated]
    ids: set[str] = set()
    by_url: dict[str, list[dict[str, Any]]] = {}
    for record in all_records:
        if record["id"] in ids:
            raise ValueError(f"duplicate source-native record id: {record['id']}")
        ids.add(record["id"])
        by_url.setdefault(record["url"], []).append(record)

    records: list[dict[str, Any]] = []
    collisions: list[dict[str, Any]] = []
    for url, representations in sorted(by_url.items()):
        ordered = sorted(
            representations,
            key=lambda item: (
                item["curation"] != "reviewed",
                {"A": 0, "B": 1, "C": 2}.get(item["authority_tier"], 3),
                item["id"],
            ),
        )
        selected = dict(ordered[0])
        selected["source_urls"] = sorted(
            {value for item in ordered for value in item["source_urls"]}
        )
        selected["source_native_ids"] = sorted(item["id"] for item in ordered)
        selected["source_families"] = sorted(
            {item["source_family"] for item in ordered}
        )
        selected["evidence_refs"] = sorted(
            {value for item in ordered for value in item["evidence_refs"]}
        )
        selected["derivations"] = sorted({item["derivation"] for item in ordered})
        selected["representations"] = [
            {
                "id": item["id"],
                "source_family": item["source_family"],
                "curation": item["curation"],
                "observed_at": item["observed_at"],
                "rights_ref": item["rights_ref"],
                "derivation": item["derivation"],
                "lifecycle_state": item["lifecycle_state"],
                "evidence_refs": item["evidence_refs"],
            }
            for item in ordered
        ]
        records.append(selected)
        if len(ordered) > 1:
            collisions.append(
                {
                    "url": url,
                    "selected_id": selected["id"],
                    "selection_rule": (
                        "reviewed curation, then authority tier, then stable ID"
                    ),
                    "representation_ids": [item["id"] for item in ordered],
                    "representation_count": len(ordered),
                }
            )

    records = sorted(
        records,
        key=lambda item: (item["title"].casefold(), item["url"], item["id"]),
    )
    if len({record["id"] for record in records}) != len(records):
        raise ValueError("selected record IDs are not unique")
    reconciliation = {
        "schema": "okf-hmlr-reconciliation.v1",
        "input_representations": len(all_records),
        "retained_records": len(records),
        "canonical_url_collisions": len(collisions),
        "merged_representations": len(all_records) - len(records),
        "excluded_records": 0,
        "errors": 0,
        "collisions": collisions,
    }
    return records, reconciliation


def counter(records: list[dict[str, Any]], key: str) -> dict[str, int]:
    values = Counter(record[key] for record in records)
    return dict(sorted(values.items(), key=lambda item: item[0].casefold()))


def list_counter(records: list[dict[str, Any]], key: str) -> dict[str, int]:
    values: Counter[str] = Counter()
    for record in records:
        values.update(record.get(key, []))
    return dict(sorted(values.items(), key=lambda item: item[0].casefold()))


def make_descriptor(
    publication_base: str,
    snapshot: dict[str, Any],
    records: list[dict[str, Any]],
    curated: dict[str, Any],
    config: dict[str, Any],
    reconciliation: dict[str, Any],
) -> dict[str, Any]:
    publication_base = publication_base.rstrip("/") + "/"
    types = counter(records, "record_type")
    sources = counter(records, "source_family")
    return {
        "@context": "https://chris-page-gov.github.io/okf-explorer/profile/bundle-wiki/v1/context.jsonld",
        "@id": urljoin(publication_base, "okf-explorer.json"),
        "schema": "okf-explorer-large-corpus.v1",
        "kind": "okf-large-corpus",
        "okf_version": "0.2",
        "version": config["version"],
        "status": config["status"],
        "title": "HM Land Registry public-estate OKF",
        "description": (
            "Independent, metadata-only discovery bundle for HM Land Registry "
            "publications, services, datasets, APIs and official repositories."
        ),
        "publisher": "https://github.com/chris-page-gov/okf-LandRegistry",
        "observed_at": snapshot["observed_at"],
        "generated_at": config["generated_at"],
        "release_at": config.get("release_at"),
        "snapshot": snapshot["snapshot_id"],
        "core_conformance": "OKF v0.2 Markdown concept layer",
        "profile": "https://chris-page-gov.github.io/okf-explorer/profile/bundle-wiki/v1/",
        "semantic_descriptor": urljoin(publication_base, "okf-bundle.jsonld"),
        "repository": "https://github.com/chris-page-gov/okf-LandRegistry",
        "counts": {
            "records": len(records),
            "sources": len(sources),
            "record_types": len(types),
            "topics": len(list_counter(records, "topics")),
            "curated_records": curated["record_count"],
            "source_representations": reconciliation["input_representations"],
            "merged_representations": reconciliation["merged_representations"],
        },
        "entrypoints": {
            "okf_index": "index.md",
            "okf_log": "log.md",
            "data_manifest": "data/manifest.json",
            "catalogue": "data/catalogue.json",
            "catalogue_csv": "data/catalogue.csv",
            "catalogue_html": "catalogue-index.html",
            "search_manifest": "data/search/manifest.json",
            "coverage": "data/coverage.json",
            "provenance": "data/provenance.json",
            "rights": "data/rights.json",
            "ai_usage_and_cost": "data/ai-usage.json",
            "reconciliation": "data/reconciliation.json",
            "evaluation": "data/evaluation.json",
            "viewer": "https://chris-page-gov.github.io/okf-explorer/",
            "site": "./",
        },
        "scope": {
            "kind": "bounded-public-metadata-discovery",
            "metadata_only": True,
            "complete_for_govuk_hmlr_filter_at_snapshot": snapshot["mode"]
            == "frozen-public-metadata",
            "complete_hmlr_public_estate": False,
            "research_cutoff": RESEARCH_CUTOFF,
            "excludes": [
                "title-register and title-plan records",
                "bulk property, ownership, address, polygon and transaction rows",
                "authenticated, paid and user-submitted service content",
                "legal advice or determinations",
            ],
        },
        "authority": {
            "not_endorsed_by_source": True,
            "official_source_authority": "external live HM Land Registry and GOV.UK sources",
            "bundle_authority": "metadata normalization and this release only",
            "legal_advice": False,
        },
        "rights": {
            "status": "mixed-record-level",
            "record_level": True,
            "statement": (
                "Public accessibility is not treated as blanket permission. "
                "Consult each record and its official source for current terms."
            ),
        },
        "performance": {
            "startup_mode": "overview-first",
            "search": "compact client-side index",
            "full_record_hydration": "lazy bounded record shards",
            "relationship_hydration": "not applicable in scaffold",
        },
        "extensions": {
            "okf-hmlr-discovery.v1": {
                "mode": "metadata-only",
                "ai_generated_proof_of_concept": config.get(
                    "ai_generated_proof_of_concept", False
                ),
                "authenticated_calls_enabled": False,
                "personal_property_records_included": False,
                "record_level_rights": True,
            },
            "okf-pages-publication.v1": {
                "site": publication_base,
                "descriptor": urljoin(publication_base, "okf-explorer.json"),
            },
        },
        "vocabulary": {
            "record_singular": "HMLR discovery record",
            "record_plural": "HMLR discovery records",
            "publisher_singular": "source publisher",
            "publisher_plural": "source publishers",
            "resource_singular": "official source",
            "resource_plural": "official sources",
            "search_placeholder": "Search guidance, datasets, services, APIs and repositories",
        },
    }


def jsonld_projection(
    publication_base: str,
    snapshot: dict[str, Any],
    records: list[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any]:
    publication_base = publication_base.rstrip("/") + "/"
    graph = []
    type_map = {
        "dataset": "Dataset",
        "api": "WebAPI",
        "software-repository": "SoftwareSourceCode",
    }
    for record in records:
        schema_type = type_map.get(record["record_type"], "CreativeWork")
        graph.append(
            {
                "@id": record["url"],
                "@type": schema_type,
                "name": record["title"],
                "description": record["description"],
                "url": record["url"],
                "publisher": {"@type": "Organization", "name": record["publisher"]},
                "dateModified": record["publisher_last_updated"],
                "isPartOf": {"@id": urljoin(publication_base, "okf-bundle.jsonld")},
            }
        )
    return {
        "@context": {
            "@vocab": "https://schema.org/",
            "dcat": "http://www.w3.org/ns/dcat#",
            "prov": "http://www.w3.org/ns/prov#",
        },
        "@id": urljoin(publication_base, "okf-bundle.jsonld"),
        "@type": "DataCatalog",
        "name": "HM Land Registry public-estate OKF",
        "description": (
            "Independent metadata-only projection; source authority remains external."
        ),
        "dateModified": config["generated_at"],
        "temporalCoverage": snapshot["observed_at"],
        "dataset": [{"@id": record["url"]} for record in records],
        "@graph": graph,
    }


def csv_safe(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    if value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


def write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "id",
        "title",
        "description",
        "url",
        "publisher",
        "authority_tier",
        "record_type",
        "source_family",
        "source_families",
        "source_native_ids",
        "jurisdiction",
        "audience",
        "access_model",
        "access_state",
        "authentication",
        "licence",
        "rights_state",
        "rights_ref",
        "authority_role",
        "derivation",
        "lifecycle_state",
        "evidence_refs",
        "cadence",
        "formats",
        "topics",
        "languages",
        "curation",
        "publisher_last_updated",
        "observed_at",
        "caveats",
        "source_urls",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for record in records:
            row = dict(record)
            for key in (
                "audience",
                "formats",
                "topics",
                "languages",
                "caveats",
                "source_urls",
                "source_families",
                "source_native_ids",
                "evidence_refs",
            ):
                row[key] = " | ".join(record.get(key, []))
            writer.writerow({key: csv_safe(row.get(key)) for key in fields})


def concept_document(
    type_name: str,
    title: str,
    description: str,
    resource: str,
    generated_at: str,
    status: str,
    body: str,
) -> str:
    metadata = {
        "type": type_name,
        "title": title,
        "description": description,
        "resource": resource,
        "generated": {
            "by": "process:hmlr-okf-builder",
            "at": generated_at,
        },
        "status": status,
        "sources": [{"id": "official-source", "resource": resource}],
    }
    frontmatter = "\n".join(
        f"{key}: {json.dumps(value, ensure_ascii=False)}"
        for key, value in metadata.items()
    )
    return f"---\n{frontmatter}\n---\n\n{body.strip()}\n"


def write_control_concepts(
    output: Path, snapshot: dict[str, Any], config: dict[str, Any]
) -> None:
    concept_status = "released" if config["status"] == "released-poc" else "draft"
    index = """---
okf_version: "0.2"
---

# HM Land Registry public-estate OKF

This is the canonical Markdown control plane for an independent, metadata-only
discovery bundle. It helps people and software find authoritative HM Land
Registry sources; it is not HM Land Registry, a title register, an official
copy, legal advice, or a licence to use restricted data.

## Concepts

- [Scope and authority](concepts/scope-and-authority.md)
- [Sources and provenance](concepts/sources-and-provenance.md)
- [Rights, access and privacy](concepts/rights-access-privacy.md)
- [Evaluation contract](concepts/evaluation.md)
- [Generation log](log.md)

## Generated entrypoints

- [Explorer descriptor](okf-explorer.json)
- [Catalogue](data/catalogue.json)
- [CSV catalogue](data/catalogue.csv)
- [Static catalogue](catalogue-index.html)
- [Data manifest](data/manifest.json)
- [JSON-LD projection](okf-bundle.jsonld)
"""
    concepts = {
        "scope-and-authority.md": concept_document(
            "Scope and Authority",
            "Scope and authority",
            "The bounded jurisdiction, inclusions, exclusions and authority model.",
            "https://www.gov.uk/government/organisations/land-registry/about",
            config["generated_at"],
            concept_status,
            """# Scope and authority

This release is a bounded metadata discovery snapshot. HM Land Registry and
other named source publishers remain authoritative for their own material.
Normal HMLR jurisdiction is England and Wales; source-specific exceptions such
as the UK House Price Index must be read from the record and official source.

No title, ownership, address, polygon, transaction-row, user-submitted or
authenticated-service content is included. No completeness beyond the explicit
snapshot denominator is claimed.
""",
        ),
        "sources-and-provenance.md": concept_document(
            "Sources and Provenance",
            "Sources and provenance",
            "How official public metadata is observed, normalized and traced.",
            "https://www.gov.uk/government/organisations/land-registry",
            config["generated_at"],
            concept_status,
            f"""# Sources and provenance

The build used snapshot `{snapshot["snapshot_id"]}`, observed
`{snapshot["observed_at"]}`. Network acquisition is separate from this offline
build. Every record retains a canonical URL, source family, authority tier,
observation time and source URLs. Normalization does not transfer authority.
""",
        ),
        "rights-access-privacy.md": concept_document(
            "Rights Access and Privacy",
            "Rights, access and privacy",
            "Record-level rights, access constraints and privacy boundaries.",
            "https://www.gov.uk/government/publications/hm-land-registry-data/public-data",
            config["generated_at"],
            concept_status,
            """# Rights, access and privacy

Rights, fees, authentication and reuse constraints are record-level. “Public”
or “free” never implies Open Government Licence coverage. Bespoke HMLR
licences and third-party Ordnance Survey, GeoPlace or Royal Mail rights can
apply.

The acquisition boundary forbids credentials, signed download links, personal
property results, user uploads and production bulk records.
""",
        ),
        "evaluation.md": concept_document(
            "Evaluation Contract",
            "Evaluation contract",
            "Candidate questions, user journeys, metrics and hard-failure gates.",
            "https://www.gov.uk/search-property-information-land-registry",
            config["generated_at"],
            concept_status,
            f"""# Evaluation contract

The first-release suite contains 24 traceable questions and 12 static-site
journeys. Its release state is `{config["status"]}`. Independent acceptance
evidence remains external to the bundle to avoid self-referential digest
binding.
Hard failures include false exact-boundary claims, wrong rights or access,
catalogue dates presented as data currency, source-authority confusion,
restricted automation, unsupported completeness, inaccessible critical tasks,
and loss of Welsh-language distinctions.
""",
        ),
    }
    (output / "index.md").write_text(index, encoding="utf-8")
    (output / "log.md").write_text(
        "# HM Land Registry OKF generation log\n\n"
        f"## {config['generated_at'][:10]}\n\n"
        f"- Observed frozen public metadata snapshot `{snapshot['snapshot_id']}` "
        f"at `{snapshot['observed_at']}`.\n"
        "- Normalized only public discovery metadata; no authenticated, paid, "
        "personal or bulk source records were acquired.\n"
        f"- Generated `{config['status']}` provenance, rights, reconciliation, "
        "search shards and static Pages catalogue offline.\n",
        encoding="utf-8",
    )
    concept_dir = output / "concepts"
    concept_dir.mkdir(parents=True, exist_ok=True)
    for name, body in concepts.items():
        (concept_dir / name).write_text(body, encoding="utf-8")


def copy_pages(output: Path) -> None:
    pages = ROOT / "pages"
    if not pages.is_dir():
        raise ValueError("pages/ authored site is missing")
    for path in sorted(pages.rglob("*")):
        if path.is_dir():
            continue
        relative = path.relative_to(pages)
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)


def search_tokens(value: str, contract: dict[str, Any]) -> list[str]:
    normalized = "".join(
        character
        for character in unicodedata.normalize("NFKD", clean_text(value)).casefold()
        if not unicodedata.combining(character)
    )
    stopwords = {clean_text(word).casefold() for word in contract["stopwords"]}
    return sorted(
        {
            token
            for token in re.findall(contract["token_pattern"], normalized)
            if token not in stopwords
        }
    )


def record_field_text(record: dict[str, Any], fields: list[str]) -> str:
    values: list[str] = []
    for field in fields:
        value = record.get(field)
        if isinstance(value, list):
            values.extend(clean_text(item) for item in value)
        else:
            values.append(clean_text(value))
    return " ".join(value for value in values if value)


def write_search_and_shards(
    output: Path, records: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[Path]]:
    contract = load_json(ROOT / "pages" / "search-contract.json")
    if contract.get("schema") != "okf-hmlr-search-contract.v1":
        raise ValueError("unsupported pages/search-contract.json schema")
    required = ("token_pattern", "stopwords", "heading_fields", "body_fields", "weights")
    if any(key not in contract for key in required):
        raise ValueError("search contract is incomplete")

    compact_fields = [
        "id",
        "title",
        "url",
        "record_type",
        "source_family",
        "jurisdiction",
        "audience",
        "access_model",
        "authentication",
        "licence",
        "cadence",
        "formats",
        "topics",
        "languages",
        "source_urls",
        "equivalent_urls",
        "curation",
        "publisher_last_updated",
    ]
    index_records: list[dict[str, Any]] = []
    shard_files: list[Path] = []
    records_dir = output / "data" / "records"
    for offset in range(0, len(records), SHARD_SIZE):
        shard_number = offset // SHARD_SIZE
        shard_name = f"records-{shard_number:03d}.json"
        shard_records = records[offset : offset + SHARD_SIZE]
        shard_path = records_dir / shard_name
        write_json(
            shard_path,
            {
                "schema": "okf-hmlr-record-shard.v1",
                "shard": shard_number,
                "record_count": len(shard_records),
                "records": shard_records,
            },
        )
        shard_files.append(shard_path)
        for record in shard_records:
            compact = {field: record.get(field) for field in compact_fields}
            compact.update(
                {
                    "heading_tokens": search_tokens(
                        record_field_text(record, contract["heading_fields"]), contract
                    ),
                    "body_tokens": search_tokens(
                        record_field_text(record, contract["body_fields"]), contract
                    ),
                    "shard": shard_number,
                }
            )
            index_records.append(compact)

    index_path = output / "data" / "search" / "index.json"
    for record in index_records:
        record["heading_tokens"] = " ".join(record["heading_tokens"])
        record["body_tokens"] = " ".join(record["body_tokens"])
    write_compact_json(
        index_path,
        {
            "schema": "okf-hmlr-search-index.v1",
            "record_count": len(index_records),
            "records": index_records,
        },
    )
    records_manifest = {
        "schema": "okf-hmlr-record-shards.v1",
        "record_count": len(records),
        "shard_size": SHARD_SIZE,
        "shards": [
            {
                "id": index,
                "path": path.name,
                "record_count": min(SHARD_SIZE, len(records) - index * SHARD_SIZE),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for index, path in enumerate(shard_files)
        ],
    }
    write_json(records_dir / "manifest.json", records_manifest)
    search_manifest = {
        "schema": "okf-static-search.v2",
        "index": "index.json",
        "records_manifest": "../records/manifest.json",
        "contract": "../../search-contract.json",
        "record_count": len(records),
        "index_bytes": index_path.stat().st_size,
        "index_sha256": sha256_file(index_path),
        "fields": contract["body_fields"],
        "facets": {
            "record_type": counter(records, "record_type"),
            "source_family": counter(records, "source_family"),
            "access_model": counter(records, "access_model"),
            "topic": list_counter(records, "topics"),
        },
        "query_state": [
            "q",
            "filter.content_type",
            "filter.service",
            "filter.audience",
            "filter.access",
            "filter.format",
            "filter.geography",
            "filter.licence",
            "filter.language",
            "filter.update_frequency",
            "filter.topic",
            "sort",
        ],
    }
    write_json(output / "data" / "search" / "manifest.json", search_manifest)
    return search_manifest, shard_files


def write_static_catalogue(output: Path, records: list[dict[str, Any]]) -> None:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        first = record["title"][:1].upper()
        key = first if first.isalpha() else "0–9"
        grouped.setdefault(key, []).append(record)
    group_keys = sorted(grouped, key=lambda value: (value == "0–9", value))
    navigation = " ".join(
        f'<a href="#group-{html.escape(key)}">{html.escape(key)}</a>'
        for key in group_keys
    )
    sections: list[str] = []
    for key in group_keys:
        rows = "\n".join(
            "<li>"
            f'<a href="{html.escape(record["url"], quote=True)}">'
            f'{html.escape(record["title"])}</a>'
            f' <span>— {html.escape(record["record_type"])}; '
            f'{html.escape(record["source_family"])}; '
            f'{html.escape(record["authority_role"])}; '
            f'access: {html.escape(record["access_state"])}; '
            f'rights: {html.escape(record["rights_state"])}</span>'
            "</li>"
            for record in grouped[key]
        )
        sections.append(
            f'<section aria-labelledby="group-{html.escape(key)}">'
            f'<h2 id="group-{html.escape(key)}">{html.escape(key)}</h2>'
            f"<ul>{rows}</ul></section>"
        )
    document = f"""<!doctype html>
<html lang="en-GB">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="default-src 'self'; style-src 'self'; img-src 'self'; base-uri 'none'; form-action 'none'">
  <title>Static catalogue — HM Land Registry public-estate OKF</title>
  <link rel="stylesheet" href="./styles.css">
</head>
<body>
  <a class="skip-link" href="#main">Skip to main content</a>
  <header class="site-header"><a href="./">HM Land Registry public-estate OKF</a></header>
  <main id="main" class="shell prose">
    <h1>Static catalogue</h1>
    <p>{len(records):,} reviewed discovery records. This no-JavaScript index
    links to the external official or official-reference source for each record.</p>
    <nav aria-label="Catalogue letters">{navigation}</nav>
    {''.join(sections)}
  </main>
</body>
</html>
"""
    (output / "catalogue-index.html").write_text(document, encoding="utf-8")


def coverage_lanes(
    sources: dict[str, dict[str, Any]],
    snapshot: dict[str, Any],
    discovered: list[dict[str, Any]],
    curated: list[dict[str, Any]],
    records: list[dict[str, Any]],
    reconciliation: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    inputs = [*discovered, *curated]
    selected_ids = {record["id"] for record in records}
    discovered_counts = Counter(record["source_family"] for record in discovered)
    curated_counts = Counter(record["source_family"] for record in curated)
    retained_counts: Counter[str] = Counter()
    for record in records:
        retained_counts.update(record["source_families"])
    merged_counts = Counter(
        record["source_family"] for record in inputs if record["id"] not in selected_ids
    )
    rows: dict[str, dict[str, Any]] = {}
    for family_id, family in sorted(sources.items()):
        snapshot_lane = snapshot.get("lanes", {}).get(family_id, {})
        expected = snapshot_lane.get("expected", family.get("observed_denominator"))
        acquired = (
            discovered_counts[family_id]
            if snapshot_lane
            else curated_counts[family_id]
        )
        normalized = discovered_counts[family_id] + curated_counts[family_id]
        rows[family_id] = {
            "expected": expected,
            "acquired": acquired,
            "curated_inputs": curated_counts[family_id],
            "normalized": normalized,
            "retained": retained_counts[family_id],
            "collisions": merged_counts[family_id],
            "excluded": 0,
            "errors": snapshot_lane.get("errors", 0),
            "denominator_as_of": family.get("denominator_as_of"),
            "acquisition": family.get("acquisition"),
        }
    if sum(row["normalized"] for row in rows.values()) != reconciliation[
        "input_representations"
    ]:
        raise ValueError("coverage lanes do not reconcile to input representations")
    return rows


def governed_input_receipts(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    paths = [
        ROOT / "scripts" / "build.py",
        ROOT / "scripts" / "acquire.py",
        ROOT / "scripts" / "check_domain_profile.py",
        ROOT / "scripts" / "evaluate.py",
        ROOT / "schemas" / "domain-profile.schema.json",
        ROOT / "source" / "build-config.json",
        ROOT / "source" / "curated-records.json",
        ROOT / "source" / "source-register.json",
        ROOT / "pages" / "search-contract.json",
        ROOT / "evaluation" / "questions.json",
        ROOT / "evaluation" / "journeys.json",
        ROOT / "personas" / "personas-and-user-stories.json",
        ROOT / "governance" / "requirements.json",
        ROOT / "governance" / "ai-model-usage.json",
        ROOT / "governance" / "risk-register.json",
        ROOT / "governance" / "rights-review.json",
        ROOT / "governance" / "traceability.json",
        ROOT / "domain-profile" / "domain-profile.json",
        ROOT / "domain-profile" / "CHECKSUMS.sha256",
    ]
    paths.extend(path for path in sorted((ROOT / "pages").rglob("*")) if path.is_file())
    manifest_path = snapshot.get("manifest_path")
    if manifest_path:
        paths.append(ROOT / manifest_path)
    paths.extend(ROOT / row["path"] for row in snapshot.get("files", []))
    unique = sorted(set(path.resolve() for path in paths))
    receipts: list[dict[str, Any]] = []
    for path in unique:
        if not path.is_file() or ROOT not in path.parents:
            raise ValueError(f"governed input is missing or unsafe: {path}")
        receipts.append(
            {
                "path": path.relative_to(ROOT).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return receipts


def data_manifest(output: Path, records: list[dict[str, Any]]) -> dict[str, Any]:
    data_files = [
        path
        for path in sorted((output / "data").rglob("*"))
        if path.is_file() and path != output / "data" / "manifest.json"
    ]
    entries = []
    for path in data_files:
        entries.append(
            {
                "path": path.relative_to(output).as_posix(),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return {
        "schema": "okf-hmlr-data-manifest.v1",
        "record_count": len(records),
        "csv_formula_neutralization": "leading =, +, -, @, tab and carriage return are prefixed with an apostrophe",
        "files": entries,
    }


def write_checksums(output: Path) -> str:
    lines = []
    for path in sorted(output.rglob("*")):
        if not path.is_file() or path.name == "CHECKSUMS.sha256":
            continue
        lines.append(f"{sha256_file(path)}  {path.relative_to(output).as_posix()}")
    manifest = "\n".join(lines) + "\n"
    root_digest = sha256_bytes(manifest.encode("utf-8"))
    (output / "CHECKSUMS.sha256").write_text(
        manifest + f"# release-root-sha256: {root_digest}\n", encoding="utf-8"
    )
    return root_digest


def validate_output_target(output_dir: Path, replace: bool) -> None:
    if output_dir.name != "bundle":
        raise ValueError("generated output directory must be named 'bundle'")
    if output_dir == ROOT or ROOT not in output_dir.parents:
        raise ValueError("output directory must be a child of the repository")
    if output_dir.is_symlink():
        raise ValueError("output directory must not be a symlink")
    if not output_dir.exists():
        return
    if not replace:
        raise ValueError(f"output exists; pass --replace to regenerate: {output_dir}")
    marker = output_dir / GENERATED_MARKER
    descriptor = output_dir / "okf-explorer.json"
    receipt = output_dir / "build-receipt.json"
    recognized_legacy = False
    if descriptor.is_file() and receipt.is_file():
        try:
            recognized_legacy = (
                load_json(descriptor).get("schema") == "okf-explorer-large-corpus.v1"
                and load_json(receipt).get("schema") == "okf-hmlr-build-receipt.v1"
            )
        except (OSError, json.JSONDecodeError):
            recognized_legacy = False
    if not marker.is_file() and not recognized_legacy:
        raise ValueError(
            "refusing to replace an unmarked directory; expected a generated marker "
            "or recognized bundle descriptor and receipt"
        )


def build(
    snapshot_dir: Path | None,
    output_dir: Path,
    publication_base: str,
    replace: bool,
) -> dict[str, Any]:
    if not publication_base.startswith("https://") or not publication_base.endswith("/"):
        raise ValueError("publication base must be an absolute HTTPS URL ending in '/'")
    output_dir = output_dir.resolve()
    validate_output_target(output_dir, replace)

    config = load_build_config()
    ai_usage_path = ROOT / "governance" / "ai-model-usage.json"
    ai_usage = load_ai_model_usage(config)
    if config["version"] != BUILD_VERSION:
        raise ValueError("build config version and builder version differ")
    sources, rights = source_controls()
    discovered, snapshot = snapshot_records(snapshot_dir)
    curated, curated_meta = curated_records()
    discovered = [govern_record(record, sources, rights) for record in discovered]
    curated = [govern_record(record, sources, rights) for record in curated]
    records, reconciliation = merge_records(discovered, curated)
    if not records:
        raise ValueError("refusing to publish an empty catalogue")
    input_receipts = governed_input_receipts(snapshot)

    with tempfile.TemporaryDirectory(prefix=".okf-build-", dir=ROOT) as temp_name:
        staging = Path(temp_name) / "bundle"
        staging.mkdir(parents=True)
        copy_pages(staging)
        (staging / GENERATED_MARKER).write_text(
            "Generated by scripts/build.py; do not edit this directory by hand.\n",
            encoding="utf-8",
        )
        write_control_concepts(staging, snapshot, config)

        catalogue = {
            "schema": "okf-hmlr-catalogue.v2",
            "title": "HM Land Registry public-estate metadata catalogue",
            "status": config["status"],
            "research_cutoff": RESEARCH_CUTOFF,
            "observed_at": snapshot["observed_at"],
            "generated_at": config["generated_at"],
            "release_at": config.get("release_at"),
            "snapshot_id": snapshot["snapshot_id"],
            "record_count": len(records),
            "records": records,
        }
        write_json(staging / "data" / "catalogue.json", catalogue)
        write_csv(staging / "data" / "catalogue.csv", records)
        write_json(staging / "data" / "reconciliation.json", reconciliation)
        lanes = coverage_lanes(
            sources, snapshot, discovered, curated, records, reconciliation
        )
        coverage = {
            "schema": "okf-hmlr-coverage.v2",
            "snapshot": snapshot,
            "records": len(records),
            "input_representations": reconciliation["input_representations"],
            "merged_representations": reconciliation["merged_representations"],
            "lanes": lanes,
            "by_source_family": counter(records, "source_family"),
            "by_record_type": counter(records, "record_type"),
            "by_access_model": counter(records, "access_model"),
            "by_authority_tier": counter(records, "authority_tier"),
            "by_topic": list_counter(records, "topics"),
            "completeness_claim": (
                "Complete only for the exact frozen GOV.UK organisation-filter "
                "response, GitHub public-repository listing and provider-filtered "
                "CDDO rows when a frozen snapshot is present; not complete for the "
                "whole HMLR public estate."
            ),
        }
        write_json(staging / "data" / "coverage.json", coverage)
        provenance = {
            "schema": "okf-hmlr-provenance.v1",
            "observed_at": snapshot["observed_at"],
            "generated_at": config["generated_at"],
            "release_at": config.get("release_at"),
            "snapshot": snapshot,
            "source_register": {
                "path": "source/source-register.json",
                "sha256": sha256_file(ROOT / "source" / "source-register.json"),
            },
            "records": [
                {
                    "id": record["id"],
                    "url": record["url"],
                    "source_native_ids": record["source_native_ids"],
                    "source_families": record["source_families"],
                    "evidence_refs": record["evidence_refs"],
                    "representations": record["representations"],
                }
                for record in records
            ],
        }
        write_json(staging / "data" / "provenance.json", provenance)
        rights_governance = load_json(ROOT / "governance" / "rights-review.json")
        rights_projection = {
            "schema": "okf-hmlr-rights-projection.v1",
            "review_state": rights_governance["review_state"],
            "release_approved": rights_governance["release_approved"],
            "source": {
                "path": "governance/rights-review.json",
                "sha256": sha256_file(ROOT / "governance" / "rights-review.json"),
            },
            "assessments": list(rights.values()),
            "records": [
                {
                    "id": record["id"],
                    "access_state": record["access_state"],
                    "rights_state": record["rights_state"],
                    "rights_ref": record["rights_ref"],
                }
                for record in records
            ],
        }
        write_json(staging / "data" / "rights.json", rights_projection)
        write_json(
            staging / "data" / "ai-usage.json",
            ai_usage_projection(ai_usage, ai_usage_path),
        )
        evaluation = load_json(ROOT / "evaluation" / "questions.json")
        write_json(staging / "data" / "evaluation.json", evaluation)
        write_search_and_shards(staging, records)
        write_static_catalogue(staging, records)
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "evaluate.py"),
                "--bundle",
                str(staging),
                "--output",
                str(staging / "data" / "evaluation-report.json"),
                "--min-expected-source-success-at-k",
                "0",
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )

        descriptor = make_descriptor(
            publication_base,
            snapshot,
            records,
            curated_meta,
            config,
            reconciliation,
        )
        write_json(staging / "okf-explorer.json", descriptor)
        write_json(
            staging / "okf-bundle.jsonld",
            jsonld_projection(publication_base, snapshot, records, config),
        )
        write_json(staging / "data" / "manifest.json", data_manifest(staging, records))
        receipt = {
            "schema": "okf-hmlr-build-receipt.v1",
            "builder": f"scripts/build.py/{BUILD_VERSION}",
            "builder_sha256": sha256_file(ROOT / "scripts" / "build.py"),
            "network_access": False,
            "snapshot": snapshot,
            "curated_source": curated_meta,
            "observed_at": snapshot["observed_at"],
            "generated_at": config["generated_at"],
            "release_at": config.get("release_at"),
            "domain_profile_canonical_sha256": canonical_profile_sha256(),
            "domain_profile_pack_root_sha256": profile_pack_root_sha256(),
            "governed_inputs": input_receipts,
            "record_count": len(records),
            "status": config["status"],
        }
        write_json(staging / "build-receipt.json", receipt)
        root_digest = write_checksums(staging)

        if output_dir.exists():
            shutil.rmtree(output_dir)
        os.replace(staging, output_dir)
    return {
        "output": str(output_dir),
        "records": len(records),
        "snapshot": snapshot["snapshot_id"],
        "release_root_sha256": root_digest,
    }


def main() -> int:
    args = parse_args()
    snapshot_dir = args.snapshot_dir or newest_snapshot()
    result = build(
        snapshot_dir=snapshot_dir,
        output_dir=args.output_dir,
        publication_base=args.publication_base,
        replace=args.replace,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
