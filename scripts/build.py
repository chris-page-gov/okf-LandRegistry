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
import functools
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
BUILD_VERSION = "0.2.0"
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
RESTRICTED_BUSINESS_GATEWAY_HOST = "businessgateway.landregistry.gov.uk"
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
    required = ("generated_at", "publication_state", "status", "version")
    missing = [key for key in required if not clean_text(config.get(key))]
    if missing:
        raise ValueError(f"source/build-config.json lacks {', '.join(missing)}")
    allowed_statuses = {
        "reviewed-scaffold-not-approved",
        "ai-generated-proof-of-concept",
    }
    if config["status"] not in allowed_statuses:
        raise ValueError(f"unsupported build status: {config['status']!r}")
    allowed_publication_states = {"digest-bound-external-evidence"}
    if config["publication_state"] not in allowed_publication_states:
        raise ValueError(
            f"unsupported publication state: {config['publication_state']!r}"
        )
    if config["status"] == "ai-generated-proof-of-concept":
        if config.get("ai_generated_proof_of_concept") is not True:
            raise ValueError(
                "an AI-generated proof of concept requires the AI-generation disclosure"
            )
    if config.get("release_at") is not None:
        raise ValueError(
            "release_at must remain null in candidate bytes; exact publication "
            "approval and time belong in digest-bound external release evidence"
        )
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


@functools.lru_cache(maxsize=1)
def load_type_kind_crosswalk() -> tuple[dict[str, str], set[str]]:
    path = ROOT / "source" / "type-kind-crosswalk.json"
    payload = load_json(path)
    if payload.get("schema") != "okf-hmlr-type-kind-crosswalk.v1":
        raise ValueError("type-to-kind crosswalk has an unsupported schema")
    if payload.get("version") != BUILD_VERSION:
        raise ValueError("type-to-kind crosswalk and builder versions differ")
    allowed = payload.get("allowed_kinds")
    mapping = payload.get("mapping")
    if (
        not isinstance(allowed, list)
        or not allowed
        or len(allowed) != len(set(allowed))
        or not isinstance(mapping, dict)
        or not mapping
    ):
        raise ValueError("type-to-kind crosswalk is incomplete")
    allowed_set = set(allowed)
    unknown = set(mapping.values()) - allowed_set
    if unknown:
        raise ValueError(f"type-to-kind crosswalk uses unknown kinds: {sorted(unknown)}")
    return mapping, allowed_set


@functools.lru_cache(maxsize=1)
def load_publisher_registry() -> dict[str, str]:
    path = ROOT / "source" / "publisher-registry.json"
    payload = load_json(path)
    if payload.get("schema") != "okf-hmlr-publisher-registry.v1":
        raise ValueError("publisher registry has an unsupported schema")
    if payload.get("version") != BUILD_VERSION:
        raise ValueError("publisher registry and builder versions differ")
    rows = payload.get("publishers")
    if not isinstance(rows, list) or not rows:
        raise ValueError("publisher registry must contain publishers")
    registry: dict[str, str] = {}
    for row in rows:
        name = clean_text(row.get("name")) if isinstance(row, dict) else ""
        identifier = ensure_https(clean_text(row.get("id"))) if name else ""
        if not name or not identifier or name in registry:
            raise ValueError("publisher registry names must be non-empty and unique")
        registry[name] = identifier
    return registry


def load_composite_input_manifest(snapshot_dir: Path) -> dict[str, Any]:
    path = ROOT / "source" / f"input-manifest-v{BUILD_VERSION}.json"
    payload = load_json(path)
    if (
        payload.get("schema") != "okf-hmlr-composite-input-manifest.v1"
        or payload.get("version") != BUILD_VERSION
    ):
        raise ValueError("composite input manifest does not match the build version")
    rows = payload.get("inputs")
    if not isinstance(rows, list) or len(rows) < 2:
        raise ValueError("composite input manifest requires bounded source inputs")
    by_id = {clean_text(row.get("id")): row for row in rows if isinstance(row, dict)}
    if len(by_id) != len(rows) or "" in by_id:
        raise ValueError("composite input IDs must be non-empty and unique")
    snapshot_row = by_id.get("public-metadata-snapshot")
    if not snapshot_row:
        raise ValueError("composite input manifest lacks the acquisition snapshot")
    expected_snapshot_path = (
        snapshot_dir.resolve() / "manifest.json"
    ).relative_to(ROOT).as_posix()
    if snapshot_row.get("path") != expected_snapshot_path:
        raise ValueError("composite input manifest selects a different snapshot")
    for row in rows:
        input_path = ROOT / clean_text(row.get("path"))
        if not input_path.is_file() or input_path.is_symlink():
            raise ValueError(f"composite input is missing or unsafe: {input_path}")
        if clean_text(row.get("sha256")) != sha256_file(input_path):
            raise ValueError(f"composite input digest differs: {input_path}")
        if not clean_text(row.get("freshness_policy")):
            raise ValueError(f"composite input lacks a freshness policy: {input_path}")
    return payload


def authority_role(tier: str) -> str:
    return {
        "A": "publisher-authoritative-source",
        "B": "official-operational-source",
        "C": "official-discovery-reference",
    }.get(tier, "unassessed-source")


def governed_optional_text(value: Any, *, field: str) -> tuple[str | None, str]:
    rendered = clean_text(value)
    placeholder = rendered.casefold()
    placeholder_values = {
        "check-source",
        "not stated",
        "not declared in repository metadata",
        "source-specific",
        "source-specific; hm land registry normally covers england and wales",
        "technical source; jurisdiction is project-specific",
        "check source-specific crown copyright and reuse terms",
        "check publisher-operated contract",
    }
    if not rendered or placeholder in placeholder_values:
        return None, "unknown"
    if field == "jurisdiction" and placeholder == "not-applicable":
        return None, "not-applicable"
    return rendered, "stated"


def normalized_languages(value: Any) -> list[str]:
    aliases = {
        "cy": "cy",
        "cymraeg": "cy",
        "welsh": "cy",
        "en": "en",
        "english": "en",
    }
    normalized: set[str] = set()
    for item in string_list(value):
        key = item.casefold()
        if key not in aliases:
            raise ValueError(
                f"language value is not a governed BCP-47 value or alias: {item!r}"
            )
        normalized.add(aliases[key])
    return sorted(normalized)


def caveat_ids_for(record: dict[str, Any]) -> list[str]:
    """Bind visible prose caveats to the governed evaluation caveat vocabulary."""

    rendered = " ".join(
        [
            clean_text(record.get("title")),
            clean_text(record.get("description")),
            " ".join(ordered_string_list(record.get("caveats"))),
            clean_text(record.get("access_model")),
            clean_text(record.get("authentication")),
            clean_text(record.get("rights_ref")),
            clean_text(record.get("rights_state")),
        ]
    ).casefold()
    caveat_ids = {
        "CAV-BOUNDED-COVERAGE",
        "CAV-DATE-SEPARATION",
        "CAV-RIGHTS-AND-ACCESS",
        "CAV-SOURCE-AUTHORITY",
    }
    if (
        record.get("rights_ref") == "RIGHT-RESTRICTED"
        or "restricted" in rendered
        or "authenticat" in rendered
        or "business gateway" in rendered
        or "portal" in rendered
        or "paid" in rendered
    ):
        caveat_ids.add("CAV-NO-RESTRICTED-AUTOMATION")
    if any(
        token in rendered
        for token in (
            "boundar",
            "indicative polygon",
            "index polygon",
            "title plan",
        )
    ) or ("polygon" in rendered and "indicative" in rendered):
        caveat_ids.add("CAV-BOUNDARY-NOT-CONCLUSION")
    if "accessib" in rendered or "wcag" in rendered or "screen reader" in rendered:
        caveat_ids.add("CAV-ACCESSIBLE-JOURNEY")
    if (
        set(record.get("languages", [])) & {"cy", "en"}
        or "welsh" in rendered
        or "cymraeg" in rendered
    ):
        caveat_ids.add("CAV-LANGUAGE-DISTINCTION")
    return sorted(caveat_ids)


def record_id_for(source_family: str, source_native_id: str) -> str:
    identity = f"{source_family}\0{source_native_id}".encode("utf-8")
    return "hmlr-" + hashlib.sha256(identity).hexdigest()[:24]


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
    source_native_id = clean_text(record["id"])
    source_native_type = clean_text(record["record_type"])
    type_mapping, _allowed_kinds = load_type_kind_crosswalk()
    if source_native_type not in type_mapping:
        raise ValueError(
            f"source-native type is absent from the governed crosswalk: "
            f"{source_native_type!r}"
        )
    publisher = clean_text(record.get("publisher")) or "HM Land Registry"
    publisher_registry = load_publisher_registry()
    if publisher not in publisher_registry:
        raise ValueError(f"publisher is absent from the governed registry: {publisher!r}")
    jurisdiction, jurisdiction_state = governed_optional_text(
        record.get("jurisdiction"), field="jurisdiction"
    )
    licence, licence_state = governed_optional_text(
        record.get("licence"), field="licence"
    )
    cadence, cadence_state = governed_optional_text(
        record.get("cadence"), field="cadence"
    )
    languages = normalized_languages(record.get("language") or record.get("languages"))
    normalized = {
        "schema": "okf-hmlr-record.v2",
        "id": source_native_id,
        "source_native_id": source_native_id,
        "source_native_type": source_native_type,
        "canonical_source_url": canonical_url,
        "title": clean_text(record["title"]),
        "description": clean_text(record.get("description")),
        "url": canonical_url,
        "publisher": publisher,
        "publisher_id": publisher_registry[publisher],
        "authority_tier": authority_tier(record.get("authority_tier"), source_family),
        "record_type": source_native_type,
        "kind": type_mapping[source_native_type],
        "source_family": source_family,
        "jurisdiction": jurisdiction,
        "jurisdiction_state": jurisdiction_state,
        "audience": string_list(record.get("audience")),
        "access_model": governed_optional_text(
            record.get("access_model"), field="access_model"
        )[0],
        "authentication": governed_optional_text(
            record.get("authentication"), field="authentication"
        )[0],
        "licence": licence,
        "licence_state": licence_state,
        "cadence": cadence,
        "cadence_state": cadence_state,
        "formats": string_list(record.get("formats")),
        "topics": string_list(record.get("topics")),
        "languages": languages,
        "language_state": "stated" if languages else "unknown",
        "curation": clean_text(record.get("curation")) or "source-native",
        "lifecycle_state": clean_text(record.get("lifecycle_state")) or "unknown",
        "publisher_last_updated": clean_text(record.get("publisher_last_updated")) or None,
        "observed_at": clean_text(record.get("observed_at"))
        or f"{RESEARCH_CUTOFF}T00:00:00Z",
        "caveats": ordered_string_list(record.get("caveats")),
        "caveat_ids": [],
        "source_urls": sorted(set(source_urls)),
        "equivalent_urls": sorted(set(equivalent_urls)),
    }
    if clean_text(record.get("translation_group")):
        normalized["translation_group"] = clean_text(record["translation_group"])
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
    is_restricted_business_gateway = (
        urlparse(url).hostname or ""
    ).casefold() == RESTRICTED_BUSINESS_GATEWAY_HOST
    access_model = (
        "approved Business Gateway customer integration"
        if is_restricted_business_gateway
        else "check publisher-operated contract"
    )
    authentication = (
        "Business e-services approval and certificate-based access"
        if is_restricted_business_gateway
        else "check publisher-operated contract"
    )
    caveats = [
        "CDDO catalogue metadata is a discovery seed, not the operational API contract.",
        "Verify status, version, authentication and rights against publisher-operated documentation.",
    ]
    if is_restricted_business_gateway:
        caveats.insert(
            0,
            (
                "Restricted Business Gateway service: do not authenticate, call, "
                "search, monitor or automate it from this metadata record."
            ),
        )
        caveats.insert(
            1,
            (
                "A publicly visible endpoint or developer description does not "
                "establish anonymous access, zero price or open reuse rights."
            ),
        )
    description = (
        (
            "CDDO discovery record for an HM Land Registry Business Gateway "
            "product. Operation is restricted; use publisher-operated "
            "documentation for current access, authentication, fees and terms."
        )
        if is_restricted_business_gateway
        else item.get("description")
    )
    return normal_record(
        {
            "id": stable_id("cddo-api", url or name),
            "title": name,
            "description": description,
            "url": url,
            "publisher": "HM Land Registry",
            "authority_tier": "C",
            "record_type": "api-catalogue-record",
            "source_family": "cddo-api-catalogue",
            "jurisdiction": item.get("areaServed")
            or "Source-specific; HM Land Registry normally covers England and Wales",
            "audience": ["developer"],
            "access_model": access_model,
            "authentication": authentication,
            "licence": item.get("license") or "not stated",
            "cadence": "catalogue-maintained",
            "formats": ["API"],
            "topics": ["API", "discovery catalogue"],
            "publisher_last_updated": item.get("dateUpdated"),
            "observed_at": observed_at,
            "caveats": caveats,
            "source_urls": source_urls,
        }
    )


def content_observation_records(
    composite_manifest: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    row = next(
        (
            item
            for item in composite_manifest["inputs"]
            if item["id"] == "govuk-content-locale-translations"
        ),
        None,
    )
    if row is None:
        raise ValueError("composite input manifest lacks the Content API observation")
    path = ROOT / row["path"]
    payload = load_json(path)
    if payload.get("schema") != "okf-hmlr-govuk-content-observation.v1":
        raise ValueError("Content API observation has an unsupported schema")
    terminal = payload.get("terminal_outcome")
    observations = payload.get("observations")
    if (
        not isinstance(terminal, dict)
        or terminal.get("status") != "complete"
        or not isinstance(observations, list)
        or terminal.get("succeeded") != len(observations)
        or terminal.get("failed") != 0
    ):
        raise ValueError("Content API observation did not terminate successfully")
    records: dict[tuple[str, str], dict[str, Any]] = {}
    for observation in observations:
        metadata = observation.get("metadata")
        if not isinstance(metadata, dict):
            raise ValueError("Content API observation metadata must be an object")
        translations = metadata.get("available_translations")
        if not isinstance(translations, list) or not translations:
            raise ValueError("Content API observation lacks available translations")
        for translation in translations:
            if not isinstance(translation, dict):
                raise ValueError("Content API translation metadata must be an object")
            content_id = clean_text(translation.get("content_id"))
            locale = clean_text(translation.get("locale"))
            url = clean_text(translation.get("web_url"))
            if not content_id or locale not in {"en", "cy"} or not url:
                raise ValueError("Content API translation identity is incomplete")
            key = (content_id, locale)
            records[key] = normal_record(
                {
                    "id": f"govuk-content:{content_id}:{locale}",
                    "title": translation.get("title"),
                    "description": (
                        "GOV.UK Content API locale and translation metadata; "
                        "rendered publication content is intentionally excluded."
                    ),
                    "url": url,
                    "publisher": "HM Land Registry",
                    "authority_tier": "A",
                    "record_type": translation.get("document_type") or "guidance",
                    "source_family": "govuk-content",
                    "access_model": "public-web",
                    "authentication": "none for this publication metadata",
                    "formats": ["HTML"],
                    "topics": ["Welsh language", "translation"],
                    "languages": [locale],
                    "publisher_last_updated": translation.get("public_updated_at"),
                    "observed_at": payload.get("observed_at"),
                    "translation_group": content_id,
                    "caveats": [
                        "Locale and translation relationships come from bounded Content API metadata.",
                        "Rendered bodies, contacts, details and attachments are outside this bundle.",
                    ],
                    "source_urls": [url],
                }
            )
    return list(records.values()), {
        "path": row["path"],
        "sha256": row["sha256"],
        "observed_at": payload["observed_at"],
        "record_count": len(records),
    }


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
    host = (urlparse(governed["canonical_source_url"]).hostname or "").casefold()
    restricted_business_gateway = host == RESTRICTED_BUSINESS_GATEWAY_HOST
    rights_ref = (
        "RIGHT-RESTRICTED"
        if restricted_business_gateway
        else RIGHTS_BY_SOURCE_FAMILY[family_id]
    )
    assessment = rights[rights_ref]
    governed.update(
        {
            "access_state": (
                "approved-professional-users"
                if restricted_business_gateway
                else clean_text(family.get("access_state")) or "unknown"
            ),
            "rights_state": (
                "restricted-service"
                if restricted_business_gateway
                else clean_text(family.get("rights_state")) or "unknown"
            ),
            "rights_ref": rights_ref,
            "authority_role": authority_role(governed["authority_tier"]),
            "derivation": (
                "reviewed-curated-metadata"
                if governed["curation"] == "reviewed"
                else "normalized-frozen-source-metadata"
            ),
            "source_native_ids": [governed["source_native_id"]],
            "source_families": [family_id],
            "evidence_refs": sorted(
                set(EVIDENCE_BY_SOURCE_FAMILY[family_id])
                | ({"EV-BG-DOCS"} if restricted_business_gateway else set())
            ),
        }
    )
    governed["caveat_ids"] = caveat_ids_for(governed)
    if assessment.get("status") not in {"permitted", "conditional", "prohibited"}:
        raise ValueError(f"rights assessment {rights_ref} has an unsupported status")
    if governed["access_state"] == "unknown" or governed["rights_state"] == "unknown":
        raise ValueError(f"record rights fail closed: {governed['id']}")
    return governed


def validate_evaluation_caveat_bindings(records: list[dict[str, Any]]) -> None:
    payload = load_json(ROOT / "evaluation" / "questions.json")
    caveat_registry = {
        clean_text(row.get("id"))
        for row in payload.get("caveat_registry", [])
        if isinstance(row, dict)
    }
    if not caveat_registry or "" in caveat_registry:
        raise ValueError("evaluation caveat registry is missing or invalid")
    by_url: dict[str, dict[str, Any]] = {}
    for record in records:
        record_caveats = set(record.get("caveat_ids", []))
        if not record_caveats or not record_caveats <= caveat_registry:
            raise ValueError(
                f"record has invalid evaluation caveat bindings: {record['id']}"
            )
        for url in [record["url"], *record.get("equivalent_urls", [])]:
            by_url[url.rstrip("/")] = record
    for question in payload.get("questions", []):
        question_id = clean_text(question.get("id")) or "unknown"
        expected_records = []
        for source in question.get("expected_sources", []):
            url = clean_text(source.get("canonical_url")).rstrip("/")
            record = by_url.get(url)
            if record is None:
                raise ValueError(
                    f"{question_id}: expected source is absent from the candidate"
                )
            expected_records.append(record)
        runtime_url = clean_text(question.get("runtime_expected_source_url")).rstrip("/")
        if runtime_url not in by_url:
            raise ValueError(f"{question_id}: runtime source is absent from the candidate")
        required = set(question.get("required_caveat_ids", []))
        available = {
            caveat_id
            for record in expected_records
            for caveat_id in record["caveat_ids"]
        }
        if not required or not required <= available:
            raise ValueError(
                f"{question_id}: required caveats are not bound to expected records"
            )


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
        selected["languages"] = sorted(
            {value for item in ordered for value in item.get("languages", [])}
        )
        selected["language_state"] = (
            "stated" if selected["languages"] else "unknown"
        )
        translation_groups = {
            clean_text(item.get("translation_group"))
            for item in ordered
            if clean_text(item.get("translation_group"))
        }
        if len(translation_groups) > 1:
            raise ValueError(f"record has conflicting translation groups: {url}")
        if translation_groups:
            selected["translation_group"] = next(iter(translation_groups))
        selected["caveat_ids"] = sorted(
            {
                value
                for item in ordered
                for value in item.get("caveat_ids", [])
            }
        )
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
        selected_native_id = selected["source_native_id"]
        record_id = record_id_for(selected["source_family"], selected_native_id)
        selected["record_id"] = record_id
        selected["record_id_scheme"] = "sha256(source_family NUL source_native_id)-24"
        selected["id"] = record_id
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
    values = Counter(clean_text(record.get(key)) or "unknown" for record in records)
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
    explorer_projection: dict[str, Any],
) -> dict[str, Any]:
    publication_base = publication_base.rstrip("/") + "/"
    types = counter(records, "record_type")
    kinds = counter(records, "kind")
    sources = counter(records, "source_family")
    return {
        "@context": "https://chris-page-gov.github.io/okf-explorer/profile/bundle-wiki/v1/context.jsonld",
        "@id": urljoin(publication_base, "okf-explorer.json"),
        "schema": "okf-explorer-large-corpus.v1",
        "kind": "okf-large-corpus",
        "okf_version": "0.2",
        "version": config["version"],
        "status": config["status"],
        "publication_state": config["publication_state"],
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
        "data_plane_manifest_root_sha256": explorer_projection[
            "manifest_root_sha256"
        ],
        "core_conformance": "OKF v0.2 Markdown concept layer",
        "profile": "https://chris-page-gov.github.io/okf-explorer/profile/bundle-wiki/v1/",
        "semantic_descriptor": urljoin(publication_base, "okf-bundle.jsonld"),
        "semantic_serializations": {
            "canonical": {
                "format": "JSON-LD",
                "path": "okf-bundle.jsonld",
            },
            "reference_only": [
                {
                    "format": "YAML-LD",
                    "status": "deferred",
                    "reason": (
                        "Reference-only in the approved v0.2 profile; no "
                        "YAML-LD conformance artifact is claimed."
                    ),
                }
            ],
        },
        "repository": "https://github.com/chris-page-gov/okf-LandRegistry",
        "counts": {
            "records": len(records),
            "datasets": sum(record["kind"] == "dataset" for record in records),
            "resources": len(records),
            "publishers": explorer_projection["counts"]["publishers"],
            "relationships": explorer_projection["counts"]["relationships"],
            "sources": len(sources),
            "record_types": len(types),
            "kinds": len(kinds),
            "topics": len(list_counter(records, "topics")),
            "curated_records": curated["record_count"],
            "source_representations": reconciliation["input_representations"],
            "merged_representations": reconciliation["merged_representations"],
        },
        "entrypoints": {
            "okf_index": "index.md",
            "okf_log": "log.md",
            "data_manifest": explorer_projection["data_manifest"]["path"],
            "overview_index": explorer_projection["overview_index"]["path"],
            "analysis_overview": explorer_projection["analysis_overview"]["path"],
            "record_locator": explorer_projection["record_locator"]["path"],
            "relationship_adjacency": explorer_projection[
                "relationship_adjacency"
            ]["path"],
            "search_manifest": explorer_projection["search_manifest"]["path"],
            "catalogue": "data/catalogue.json",
            "catalogue_csv": "data/catalogue.csv",
            "catalogue_html": "catalogue-index.html",
            "inventory_manifest": "data/manifest.json",
            "coverage": "data/coverage.json",
            "provenance": "data/provenance.json",
            "rights": "data/rights.json",
            "ai_usage_and_cost": "data/ai-usage.json",
            "reconciliation": "data/reconciliation.json",
            "evaluation": "data/evaluation.json",
            "viewer": "https://chris-page-gov.github.io/okf-explorer/",
            "site": "./",
        },
        "entrypoint_integrity": {
            "data_manifest": explorer_projection["data_manifest"],
            "overview_index": explorer_projection["overview_index"],
            "analysis_overview": explorer_projection["analysis_overview"],
            "record_locator": explorer_projection["record_locator"],
            "relationship_adjacency": explorer_projection[
                "relationship_adjacency"
            ],
            "search_manifest": explorer_projection["search_manifest"],
        },
        "scope": {
            "kind": "bounded-public-metadata-discovery",
            "metadata_only": True,
            "complete_for_govuk_hmlr_filter_at_snapshot": snapshot["mode"]
            == "composite-frozen-public-metadata",
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
            "search": "bounded in-browser index over Explorer record chunks",
            "full_record_hydration": "integrity-bound chunks",
            "relationship_hydration": "integrity-bound deterministic chunks",
        },
        "extensions": {
            "okf-hmlr-discovery.v1": {
                "mode": "metadata-only",
                "ai_generated_proof_of_concept": config.get(
                    "ai_generated_proof_of_concept", False
                ),
                "release_authority": (
                    "Not asserted by bundle bytes; consult exact-digest "
                    "external release evidence."
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
    graph: list[dict[str, Any]] = []
    catalog_id = urljoin(publication_base, "okf-bundle.jsonld")
    record_refs: list[dict[str, str]] = []
    dataset_refs: list[dict[str, str]] = []
    publisher_nodes: dict[str, dict[str, Any]] = {}
    rights_nodes: dict[str, dict[str, Any]] = {}
    activity_nodes: dict[str, dict[str, Any]] = {}
    entity_types = {
        "dataset": ["dcat:Dataset", "schema:Dataset"],
        "service": ["dcat:DataService", "schema:Service"],
        "API": ["dcat:DataService", "schema:WebAPI"],
        "repository": ["schema:SoftwareSourceCode"],
        "statistics": ["schema:Dataset"],
        "guidance": ["schema:CreativeWork"],
        "form": ["schema:DigitalDocument"],
        "news": ["schema:NewsArticle"],
        "corporate": ["schema:CreativeWork"],
        "legislation": ["schema:Legislation"],
        "other": ["schema:CreativeWork"],
    }
    for record in records:
        record_node_id = urljoin(publication_base, f"records/{record['record_id']}")
        publisher_id = record["publisher_id"]
        rights_id = urljoin(publication_base, f"rights/{record['rights_ref']}")
        activity_id = urljoin(
            publication_base,
            "activities/"
            + record["source_family"]
            + "-"
            + hashlib.sha256(record["observed_at"].encode("utf-8")).hexdigest()[:12],
        )
        publisher_nodes[publisher_id] = {
            "@id": publisher_id,
            "@type": "schema:Organization",
            "schema:name": record["publisher"],
            "schema:url": publisher_id,
        }
        rights_nodes[rights_id] = {
            "@id": rights_id,
            "@type": "dcterms:RightsStatement",
            "dcterms:identifier": record["rights_ref"],
            "schema:name": record["rights_state"],
        }
        activity_nodes[activity_id] = {
            "@id": activity_id,
            "@type": "prov:Activity",
            "dcterms:identifier": record["source_family"],
            "prov:endedAtTime": record["observed_at"],
        }
        record_refs.append({"@id": record_node_id})
        if record["kind"] == "dataset":
            dataset_refs.append({"@id": record["canonical_source_url"]})
        graph.append(
            {
                "@id": record_node_id,
                "@type": "dcat:CatalogRecord",
                "dcterms:identifier": record["record_id"],
                "dcterms:source": [
                    {"@id": source_url} for source_url in record["source_urls"]
                ],
                "dcterms:rights": {"@id": rights_id},
                "foaf:primaryTopic": {"@id": record["canonical_source_url"]},
                "prov:wasGeneratedBy": {"@id": activity_id},
            }
        )
        entity: dict[str, Any] = {
            "@id": record["canonical_source_url"],
            "@type": entity_types[record["kind"]],
            "schema:name": record["title"],
            "schema:description": record["description"],
            "schema:url": record["canonical_source_url"],
            "dcterms:publisher": {"@id": publisher_id},
            "dcterms:rights": {"@id": rights_id},
            "dcterms:type": record["source_native_type"],
            "dcterms:language": record["languages"],
            "dcterms:isPartOf": {"@id": catalog_id},
            "prov:wasDerivedFrom": [
                {"@id": source_url} for source_url in record["source_urls"]
            ],
            "prov:wasGeneratedBy": {"@id": activity_id},
        }
        if record["publisher_last_updated"]:
            entity["dcterms:modified"] = record["publisher_last_updated"]
        if record["licence"] is not None:
            entity["dcterms:license"] = record["licence"]
        graph.append(
            entity
        )
    graph.extend(publisher_nodes[key] for key in sorted(publisher_nodes))
    graph.extend(rights_nodes[key] for key in sorted(rights_nodes))
    graph.extend(activity_nodes[key] for key in sorted(activity_nodes))
    return {
        "@context": urljoin(publication_base, "context.jsonld"),
        "@id": catalog_id,
        "@type": ["dcat:Catalog", "schema:DataCatalog"],
        "schema:name": "HM Land Registry public-estate OKF",
        "schema:description": (
            "Independent metadata-only projection; source authority remains external."
        ),
        "dcterms:modified": config["generated_at"],
        "dcterms:temporal": snapshot["observed_at"],
        "dcat:record": record_refs,
        "schema:dataset": dataset_refs,
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
        "schema",
        "id",
        "record_id",
        "record_id_scheme",
        "source_native_id",
        "source_native_type",
        "canonical_source_url",
        "title",
        "description",
        "url",
        "publisher",
        "publisher_id",
        "authority_tier",
        "record_type",
        "kind",
        "source_family",
        "source_families",
        "source_native_ids",
        "jurisdiction",
        "jurisdiction_state",
        "audience",
        "access_model",
        "access_state",
        "authentication",
        "licence",
        "licence_state",
        "rights_state",
        "rights_ref",
        "authority_role",
        "derivation",
        "lifecycle_state",
        "evidence_refs",
        "cadence",
        "cadence_state",
        "formats",
        "topics",
        "languages",
        "language_state",
        "curation",
        "publisher_last_updated",
        "observed_at",
        "caveats",
        "caveat_ids",
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
                "caveat_ids",
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
    concept_status = (
        "released"
        if config["status"] == "ai-generated-proof-of-concept"
        else "draft"
    )
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
        "one Explorer runtime search plane and a static Pages catalogue offline.\n",
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
        if relative.as_posix() in {"app.js", "search-contract.json"}:
            continue
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
        "schema": "okf-hmlr-site-search.v1",
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


def explorer_name(kind: str, source_identity: str) -> str:
    """Return a stable, URL-safe projection name without replacing source identity."""
    return f"{kind}-{sha256_bytes(source_identity.encode('utf-8'))[:24]}"


def explorer_reference(output: Path, path: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(output).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def explorer_facet_rows(values: Counter[str]) -> list[dict[str, Any]]:
    return [
        {"value": value, "count": count}
        for value, count in sorted(
            values.items(), key=lambda item: (-item[1], item[0].casefold())
        )
    ]


def explorer_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [clean_text(item) for item in value if clean_text(item)]
    text = clean_text(value)
    return [text] if text else []


def compact_canonical_json(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def explorer_worker_tokens(value: str) -> list[str]:
    normalized = "".join(
        character
        for character in unicodedata.normalize("NFKD", clean_text(value)).casefold()
        if not unicodedata.combining(character)
    )
    return sorted(
        {
            match.group(0).strip("._-")
            for match in re.finditer(r"[a-z0-9][a-z0-9._-]*", normalized)
            if len(match.group(0).strip("._-")) >= 2
        }
    )


def explorer_search_field_values(
    record: dict[str, Any], field: str
) -> list[str]:
    aliases = {
        "format": "formats",
        "geography": "geography",
        "language": "language",
        "topic": "topics",
    }
    value = record.get(aliases.get(field, field))
    if isinstance(value, list):
        return sorted({clean_text(item) for item in value if clean_text(item)})
    text = clean_text(value)
    return [text] if text else []


def write_explorer_search(
    output: Path,
    datasets: list[dict[str, Any]],
    facets_path: Path,
    dataset_references: list[dict[str, Any]],
    snapshot_id: str,
) -> dict[str, Any]:
    search_dir = output / "data" / "explorer" / "search"
    weights = {
        "title": 16,
        "publisher": 8,
        "description": 5,
        "caveats": 5,
        "topics": 4,
        "record_type": 4,
        "source": 3,
        "tags": 3,
        "url": 2,
    }
    masks = {
        "title": 1,
        "publisher": 2,
        "description": 4,
        "caveats": 4,
        "topics": 8,
        "record_type": 16,
        "source": 32,
        "tags": 64,
        "url": 128,
    }
    postings: dict[str, list[list[int]]] = {}
    document_frequency: Counter[str] = Counter()
    for dataset in datasets:
        ordinal = int(dataset["ordinal"])
        token_scores: dict[str, list[int]] = {}
        fields = {
            "title": clean_text(dataset.get("title")),
            "publisher": clean_text(dataset.get("publisher_title")),
            "description": clean_text(dataset.get("notes")),
            "caveats": " ".join(
                clean_text(value)
                for value in dataset.get("caveats", [])
                if clean_text(value)
            ),
            "topics": " ".join(dataset.get("topics", [])),
            "record_type": clean_text(dataset.get("record_type")),
            "source": " ".join(
                clean_text(dataset.get(key))
                for key in ("source_tier", "source_adapter", "authority_role")
            ),
            "tags": " ".join(dataset.get("tags", [])),
            "url": clean_text(dataset.get("url")),
        }
        for field, value in fields.items():
            for token in explorer_worker_tokens(value):
                score, mask = token_scores.get(token, [0, 0])
                token_scores[token] = [
                    score + weights[field],
                    mask | masks[field],
                ]
        for token, (score, mask) in token_scores.items():
            postings.setdefault(token, []).append([ordinal, score, mask])
            document_frequency[token] += 1

    postings_by_partition: dict[str, dict[str, list[list[int]]]] = {}
    lexicon_by_partition: dict[str, list[dict[str, Any]]] = {}
    logical_to_partition: dict[str, str] = {}
    for token in sorted(postings):
        logical = re.sub(r"[^a-z0-9]", "", token)[:2] or "_"
        partition = logical[:1] or "_"
        postings_path = (
            output / "data" / "explorer" / "search" / f"postings-{partition}.json"
        )
        logical_to_partition[logical] = partition
        postings_by_partition.setdefault(partition, {})[token] = postings[token]
        lexicon_by_partition.setdefault(partition, []).append(
            {
                "token": token,
                "df": document_frequency[token],
                "postings": postings_path.relative_to(output).as_posix(),
            }
        )

    lexicon_entrypoints: dict[str, str] = {}
    postings_entrypoints: list[str] = []
    shard_groups: dict[str, list[dict[str, Any]]] = {
        "lexicon": [],
        "postings": [],
        "result_docs": [],
        "filters": [],
        "support": [],
    }
    for partition in sorted(postings_by_partition):
        postings_path = search_dir / f"postings-{partition}.json"
        lexicon_path = search_dir / f"lexicon-{partition}.json"
        write_compact_json(
            postings_path,
            {"tokens": postings_by_partition[partition]},
        )
        write_compact_json(lexicon_path, lexicon_by_partition[partition])
        postings_entrypoints.append(postings_path.relative_to(output).as_posix())
        for logical, observed_partition in logical_to_partition.items():
            if observed_partition == partition:
                lexicon_entrypoints[logical] = lexicon_path.relative_to(
                    output
                ).as_posix()
        for group, path in (
            ("postings", postings_path),
            ("lexicon", lexicon_path),
        ):
            reference = explorer_reference(output, path)
            reference["snapshot"] = snapshot_id
            shard_groups[group].append(reference)

    result_doc_paths = [reference["path"] for reference in dataset_references]
    for reference in dataset_references:
        row = dict(reference)
        row["snapshot"] = snapshot_id
        shard_groups["result_docs"].append(row)

    filter_entrypoints: dict[str, str] = {}
    filter_keys = [
        "access",
        "access_state",
        "audience",
        "content_type",
        "format",
        "geography",
        "language",
        "licence",
        "lifecycle_state",
        "publisher",
        "kind",
        "record_type",
        "rights_state",
        "service",
        "source_family",
        "topic",
        "update_frequency",
    ]
    for key in filter_keys:
        values: dict[str, list[int]] = {}
        for dataset in datasets:
            ordinal = int(dataset["ordinal"])
            for value in explorer_search_field_values(dataset, key):
                values.setdefault(value, []).append(ordinal)
        path = search_dir / "filters" / f"{key}.json"
        write_compact_json(
            path,
            {
                "schema": "okf-static-filter-postings.v1",
                "key": key,
                "values": dict(sorted(values.items())),
            },
        )
        filter_entrypoints[key] = path.relative_to(output).as_posix()
        reference = explorer_reference(output, path)
        reference["snapshot"] = snapshot_id
        shard_groups["filters"].append(reference)

    doc_map_path = search_dir / "doc-map.json"
    sort_values_path = search_dir / "sort-values.json"
    write_compact_json(
        doc_map_path,
        {str(dataset["ordinal"]): dataset["open"] for dataset in datasets},
    )
    write_compact_json(
        sort_values_path,
        [
            [
                clean_text(dataset.get("timestamp")),
                clean_text(dataset.get("title")),
                None,
            ]
            for dataset in datasets
        ],
    )
    for path in (doc_map_path, sort_values_path):
        reference = explorer_reference(output, path)
        reference["snapshot"] = snapshot_id
        shard_groups["support"].append(reference)

    metadata = {
        "schema": "okf-search-shard-metadata.v1",
        "snapshot": snapshot_id,
        "shards": shard_groups,
    }
    metadata_path = search_dir / "shards.json"
    write_json(metadata_path, metadata)
    shard_manifest_sha256 = sha256_bytes(
        compact_canonical_json(metadata["shards"])
    )
    metadata_reference = explorer_reference(output, metadata_path)
    manifest = {
        "schema": "okf-static-search.v2",
        "snapshot": snapshot_id,
        "token_min_length": 2,
        "prefix_min_length": 3,
        "lexicon_shard_length": 2,
        "result_limit": 200,
        "result_doc_chunk_size": SHARD_SIZE,
        "weights": weights,
        "field_masks": masks,
        "counts": {
            "documents": len(datasets),
            "tokens": len(postings),
            "postings": sum(len(rows) for rows in postings.values()),
            "postings_shards": len(postings_entrypoints),
            "uncapped_postings": sum(document_frequency.values()),
            "max_postings_per_token": 50_000,
        },
        "entrypoints": {
            "lexicon": dict(sorted(lexicon_entrypoints.items())),
            "prefixes": {},
            "postings": postings_entrypoints,
            "result_docs": result_doc_paths,
            "facets": explorer_reference(output, facets_path),
            "doc_map": doc_map_path.relative_to(output).as_posix(),
            "filter_postings": filter_entrypoints,
            "sort_values": sort_values_path.relative_to(output).as_posix(),
        },
        "shard_metadata": metadata_reference,
        "shard_manifest_sha256": shard_manifest_sha256,
    }
    manifest_path = search_dir / "manifest.json"
    write_json(manifest_path, manifest)
    return explorer_reference(output, manifest_path)


def explorer_relationship_bucket(route: str) -> str:
    value = 0x811C9DC5
    for byte in route.encode("utf-8"):
        value ^= byte
        value = (value * 0x01000193) & 0xFFFFFFFF
    return f"{(value >> 24) & 0xFF:02x}"


def write_explorer_record_locator(
    output: Path,
    datasets: list[dict[str, Any]],
    dataset_references: list[dict[str, Any]],
    snapshot_id: str,
) -> dict[str, Any]:
    locator_dir = output / "data" / "explorer" / "locator"
    locations = {
        dataset["route"]: [
            int(dataset["ordinal"]) // SHARD_SIZE,
            int(dataset["ordinal"]) % SHARD_SIZE,
        ]
        for dataset in datasets
    }
    locations_path = locator_dir / "routes.json"
    write_compact_json(locations_path, locations)
    locations_reference = explorer_reference(output, locations_path)
    buckets = {
        explorer_relationship_bucket(route): locations_reference
        for route in sorted(locations)
    }
    locator = {
        "schema": "okf-record-locator-sharded.v1",
        "algorithm": "fnv1a32-prefix-2",
        "snapshot": snapshot_id,
        "records": len(datasets),
        "chunk_size": SHARD_SIZE,
        "record_chunks": dataset_references,
        "bucket_count": len(buckets),
        "buckets": dict(sorted(buckets.items())),
    }
    locator_path = locator_dir / "manifest.json"
    write_json(locator_path, locator)
    return explorer_reference(output, locator_path)


def write_explorer_relationship_adjacency(
    output: Path,
    relationships: list[dict[str, Any]],
    snapshot_id: str,
) -> dict[str, Any]:
    """Write bounded route-to-relationship buckets for targeted Explorer views."""
    adjacency_dir = output / "data" / "explorer" / "adjacency"
    by_route: dict[str, list[dict[str, Any]]] = {}
    for relationship in relationships:
        for route in {
            clean_text(relationship.get("source")),
            clean_text(relationship.get("target")),
        }:
            if route:
                by_route.setdefault(route, []).append(relationship)

    by_bucket: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for route, rows in sorted(by_route.items()):
        bucket = explorer_relationship_bucket(route)
        by_bucket.setdefault(bucket, {})[route] = rows

    buckets: dict[str, dict[str, Any]] = {}
    for bucket, rows in sorted(by_bucket.items()):
        path = adjacency_dir / f"{bucket}.json"
        write_compact_json(path, rows)
        buckets[bucket] = explorer_reference(output, path)

    manifest = {
        "schema": "okf-relationship-adjacency.v1",
        "algorithm": "fnv1a32-prefix-2",
        "snapshot": snapshot_id,
        "routes": len(by_route),
        "relationships": len(relationships),
        "buckets": buckets,
    }
    manifest_path = adjacency_dir / "manifest.json"
    write_json(manifest_path, manifest)
    return explorer_reference(output, manifest_path)


def write_explorer_projection(
    output: Path,
    records: list[dict[str, Any]],
    snapshot: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Write the pinned OKF Explorer large-corpus data-plane projection."""
    projection_dir = output / "data" / "explorer"
    dataset_rows: list[dict[str, Any]] = []
    resource_rows: list[dict[str, Any]] = []
    relationship_rows: list[dict[str, Any]] = []
    publisher_counts = Counter(clean_text(record["publisher_id"]) for record in records)
    publisher_titles = {
        clean_text(record["publisher_id"]): clean_text(record["publisher"])
        for record in records
    }
    publisher_names = {
        publisher: explorer_name("publisher", publisher)
        for publisher in sorted(publisher_counts, key=str.casefold)
    }
    if len(set(publisher_names.values())) != len(publisher_names):
        raise ValueError("Explorer publisher projection identity collision")

    dataset_names: dict[str, str] = {}
    for ordinal, record in enumerate(records):
        source_identity = clean_text(record["id"])
        name = explorer_name("record", source_identity)
        if name in dataset_names:
            raise ValueError(
                "Explorer record projection identity collision: "
                f"{source_identity} and {dataset_names[name]}"
            )
        dataset_names[name] = source_identity
        publisher_id = clean_text(record["publisher_id"])
        publisher_title = clean_text(record["publisher"])
        publisher_name = publisher_names[publisher_id]
        source_url = clean_text(record["url"])
        host = urlparse(source_url).hostname or ""
        resource_id = explorer_name("source", source_identity)
        dataset = dict(record)
        dataset.update(
            {
                "name": name,
                "route": f"dataset/{name}",
                "open": f"dataset/{name}",
                "ordinal": ordinal,
                "title": clean_text(record["title"]),
                "notes": clean_text(record.get("description")),
                "context_note": (
                    " ".join(
                        clean_text(value)
                        for value in record.get("caveats", [])
                        if clean_text(value)
                    )
                    + " Caveat controls: "
                    + ", ".join(record.get("caveat_ids", []))
                    + "."
                ),
                "publisher": publisher_name,
                "publisher_title": publisher_title,
                "publisher_id": publisher_id,
                "resource_count": 1,
                "resource_ids": [resource_id],
                "tags": explorer_list(record.get("topics")),
                "timestamp": clean_text(
                    record.get("publisher_last_updated")
                    or record.get("observed_at")
                ),
                "license_title": (
                    clean_text(record.get("licence"))
                    or "Not stated by the source."
                ),
                "license_basis": " / ".join(
                    value
                    for value in (
                        clean_text(record.get("rights_state")),
                        clean_text(record.get("rights_ref")),
                    )
                    if value
                ),
                "host": host,
                "url": source_url,
                "state": clean_text(record.get("lifecycle_state")),
                "type": clean_text(record.get("kind")),
                "kind": clean_text(record.get("kind")),
                "record_type": clean_text(record.get("kind")),
                "source_native_type": clean_text(record.get("source_native_type")),
                "source_tier": clean_text(record.get("authority_tier")),
                "source_adapter": clean_text(record.get("source_family")),
                "content_type": clean_text(record.get("kind")),
                "service": clean_text(record.get("source_family")),
                "access": (
                    clean_text(record.get("access_model"))
                    or "Not stated by the source."
                ),
                "geography": (
                    explorer_list(record.get("jurisdiction"))
                    or ["Not stated by the source."]
                ),
                "language": (
                    explorer_list(record.get("languages"))
                    or ["Not stated by the source."]
                ),
                "update_frequency": (
                    clean_text(record.get("cadence"))
                    or "Not stated by the source."
                ),
                "provenance": {
                    "source_native_ids": list(record.get("source_native_ids", [])),
                    "source_urls": list(record.get("source_urls", [])),
                    "evidence_refs": list(record.get("evidence_refs", [])),
                    "observed_at": clean_text(record.get("observed_at")),
                    "derivation": clean_text(record.get("derivation")),
                },
            }
        )
        dataset_rows.append(dataset)
        source_format = next(iter(record.get("formats", [])), "Web")
        resource_rows.append(
            {
                "id": resource_id,
                "dataset": name,
                "route": f"resource/{resource_id}",
                "name": "Recorded public source",
                "description": (
                    "Source route retained from the bounded metadata snapshot; "
                    "check the publisher for current content, access and terms."
                ),
                "format": source_format,
                "source_format": source_format,
                "host": host,
                "url": source_url,
                "position": 0,
                "state": clean_text(record.get("lifecycle_state")),
                "provenance": {
                    "record_id": source_identity,
                    "observed_at": clean_text(record.get("observed_at")),
                    "evidence_refs": list(record.get("evidence_refs", [])),
                },
            }
        )

    publisher_rows = [
        {
            "name": publisher_names[publisher_id],
            "route": f"publisher/{publisher_names[publisher_id]}",
            "title": publisher_titles[publisher_id],
            "url": publisher_id,
            "description": (
                "Stable publisher identity from the governed registry. Source "
                "authority remains with the linked official publisher."
            ),
            "dataset_count": publisher_counts[publisher_id],
            "resource_count": publisher_counts[publisher_id],
            "state": "observed",
        }
        for publisher_id in sorted(publisher_counts, key=str.casefold)
    ]

    translations: dict[str, list[dict[str, Any]]] = {}
    by_record_id = {record["record_id"]: record for record in records}
    for record in records:
        group = clean_text(record.get("translation_group"))
        if group:
            translations.setdefault(group, []).append(record)
    for group, members in sorted(translations.items()):
        english = next(
            (record for record in members if "en" in record.get("languages", [])),
            None,
        )
        if english is None:
            raise ValueError(f"translation group lacks an English record: {group}")
        target_name = explorer_name("record", english["record_id"])
        for translated in sorted(members, key=lambda item: item["record_id"]):
            if translated["record_id"] == english["record_id"]:
                continue
            source_name = explorer_name("record", translated["record_id"])
            relationship_rows.append(
                {
                    "source": f"dataset/{source_name}",
                    "target": f"dataset/{target_name}",
                    "kind": "translation of",
                    "predicate": "translation_of",
                    "authority": "observed",
                    "derivation": (
                        "GOV.UK Content API available_translations metadata "
                        f"for content identity {group}."
                    ),
                    "observed_at": clean_text(translated.get("observed_at")),
                    "evidence": list(translated.get("evidence_refs", [])),
                    "rights": clean_text(translated.get("rights_state")),
                }
            )

    facet_keys = [
        "access",
        "access_state",
        "audience",
        "content_type",
        "format",
        "geography",
        "language",
        "licence",
        "lifecycle_state",
        "publisher",
        "kind",
        "record_type",
        "rights_state",
        "service",
        "source_family",
        "topic",
        "update_frequency",
    ]
    facets = {
        key: explorer_facet_rows(
            Counter(
                value
                for dataset in dataset_rows
                for value in explorer_search_field_values(dataset, key)
            )
        )
        for key in facet_keys
    }
    counts = {
        "records": len(dataset_rows),
        "datasets": sum(record["kind"] == "dataset" for record in records),
        "resources": len(resource_rows),
        "publishers": len(publisher_rows),
        "relationships": len(relationship_rows),
    }
    notices = [
        "Independent AI-generated proof of concept; not an HM Land Registry service or endorsement.",
        "Metadata discovery only: not legal advice, proof of ownership, priority or an exact-boundary service.",
        "Public access is not treated as blanket open rights; check each record and its current source terms.",
        "Coverage is bounded to named, dated and reconciled source lanes, not the complete HMLR public estate.",
    ]
    overview = {
        "schema": "okf-explorer-overview.v1",
        "title": "HM Land Registry public-estate metadata overview",
        "generated_at": config["generated_at"],
        "snapshot": snapshot["snapshot_id"],
        "counts": counts,
        "facet_previews": {
            key: rows[:12] for key, rows in sorted(facets.items())
        },
        "format_counts": facets["format"][:20],
        "notices": notices,
    }
    analysis_overview = {
        "schema": "okf-explorer-analysis.v1",
        "generated_at": config["generated_at"],
        "snapshot": snapshot["snapshot_id"],
        "summary": {
            "title": overview["title"],
            "description": (
                "Overview-first metadata discovery across the governed HM Land "
                "Registry public-estate source lanes."
            ),
            "notices": notices,
        },
    }
    overview_path = projection_dir / "overview.json"
    analysis_overview_path = projection_dir / "analysis-overview.json"
    facets_path = projection_dir / "facets.json"
    write_json(overview_path, overview)
    write_json(analysis_overview_path, analysis_overview)
    write_json(facets_path, facets)

    chunk_sets = {
        "datasets": dataset_rows,
        "resources": resource_rows,
        "publishers": publisher_rows,
        "relationships": relationship_rows,
    }
    chunk_references: dict[str, list[dict[str, Any]]] = {}
    for kind, rows in chunk_sets.items():
        references = []
        for offset in range(0, len(rows), SHARD_SIZE):
            shard_number = offset // SHARD_SIZE
            path = projection_dir / f"{kind}-{shard_number:03d}.json"
            write_compact_json(path, rows[offset : offset + SHARD_SIZE])
            references.append(explorer_reference(output, path))
        chunk_references[kind] = references

    record_locator_reference = write_explorer_record_locator(
        output,
        dataset_rows,
        chunk_references["datasets"],
        snapshot["snapshot_id"],
    )
    relationship_adjacency_reference = write_explorer_relationship_adjacency(
        output,
        relationship_rows,
        snapshot["snapshot_id"],
    )
    search_manifest_reference = write_explorer_search(
        output,
        dataset_rows,
        facets_path,
        chunk_references["datasets"],
        snapshot["snapshot_id"],
    )
    governed_references = [
        explorer_reference(output, path)
        for path in sorted(projection_dir.rglob("*"))
        if path.is_file()
    ]
    manifest_root_sha256 = sha256_bytes(canonical_json(governed_references))
    manifest = {
        "schema": "okf-explorer-data-manifest.v1",
        "title": "HM Land Registry public-estate Explorer data plane",
        "generated_at": config["generated_at"],
        "snapshot": snapshot["snapshot_id"],
        "counts": counts,
        "indexes": {
            "overview": overview_path.relative_to(output).as_posix(),
            "analysis": analysis_overview_path.relative_to(output).as_posix(),
            "facets": facets_path.relative_to(output).as_posix(),
            "record_locator": record_locator_reference,
            "relationship_adjacency": relationship_adjacency_reference,
            "search": search_manifest_reference["path"],
        },
        "chunks": chunk_references,
        "integrity": {
            "algorithm": "sha256",
            "manifest_root_sha256": manifest_root_sha256,
            "scope": "canonical ordered references to overview, facets and chunks",
        },
        "performance": {
            "startup_mode": "overview-first",
            "full_index_max_records": len(dataset_rows),
            "chunk_size": SHARD_SIZE,
        },
    }
    manifest_path = projection_dir / "manifest.json"
    write_json(manifest_path, manifest)
    return {
        "counts": counts,
        "data_manifest": explorer_reference(output, manifest_path),
        "overview_index": explorer_reference(output, overview_path),
        "analysis_overview": explorer_reference(output, analysis_overview_path),
        "record_locator": record_locator_reference,
        "relationship_adjacency": relationship_adjacency_reference,
        "search_manifest": search_manifest_reference,
        "manifest_root_sha256": manifest_root_sha256,
    }


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
        ROOT / "scripts" / "observe_govuk_content.py",
        ROOT / "scripts" / "check_domain_profile.py",
        ROOT / "scripts" / "check_okf.py",
        ROOT / "scripts" / "change_impact.py",
        ROOT / "scripts" / "evaluate.py",
        ROOT / "schemas" / "artifact-dependency-graph.schema.json",
        ROOT / "schemas" / "domain-profile.schema.json",
        ROOT / "contracts" / "okf-explorer.consumer-lock.json",
        ROOT / "source" / "build-config.json",
        ROOT / "source" / "curated-records.json",
        ROOT / "source" / "type-kind-crosswalk.json",
        ROOT / "source" / "publisher-registry.json",
        ROOT / "source" / "jsonld-context.json",
        ROOT / "source" / "source-register.json",
        ROOT / "pages" / "search-contract.json",
        ROOT / "evaluation" / "questions.json",
        ROOT / "evaluation" / "journeys.json",
        ROOT / "evaluation" / "explorer-v0.2.0-journeys.json",
        ROOT / "evaluation" / "explorer-search-calibration-v0.2.0.json",
        ROOT / "personas" / "personas-and-user-stories.json",
        ROOT / "governance" / "requirements.json",
        ROOT / "governance" / "ai-model-usage.json",
        ROOT / "governance" / "artifact-dependency-graph.json",
        ROOT / "governance" / "risk-register.json",
        ROOT / "governance" / "rights-review.json",
        ROOT / "governance" / "traceability.json",
        ROOT / "domain-profile" / "domain-profile.json",
        ROOT / "domain-profile" / "CHECKSUMS.sha256",
    ]
    paths.extend(path for path in sorted((ROOT / "pages").rglob("*")) if path.is_file())
    paths.extend(
        path
        for path in sorted((ROOT / "evaluation").rglob("*"))
        if path.is_file() and path.name != "acceptance-review.json"
    )
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
    if snapshot_dir is None:
        raise ValueError("v0.2.0 requires the governed composite input manifest")
    composite_manifest = load_composite_input_manifest(snapshot_dir.resolve())
    content_records, content_meta = content_observation_records(composite_manifest)
    acquisition_snapshot = dict(snapshot)
    discovered.extend(content_records)
    snapshot.update(
        {
            "snapshot_id": "hmlr-public-metadata-v0.2.0",
            "observed_at": max(snapshot["observed_at"], content_meta["observed_at"]),
            "mode": "composite-frozen-public-metadata",
            "source_manifest_sha256": sha256_file(
                ROOT / "source" / f"input-manifest-v{BUILD_VERSION}.json"
            ),
            "manifest_path": f"source/input-manifest-v{BUILD_VERSION}.json",
            "acquisition_snapshot": acquisition_snapshot,
            "composite_inputs": composite_manifest["inputs"],
        }
    )
    snapshot["lanes"]["govuk-content"] = {
        "expected": content_meta["record_count"],
        "acquired": content_meta["record_count"],
        "errors": 0,
        "terminal_outcome": {
            "status": "complete",
            "record_count": content_meta["record_count"],
        },
    }
    snapshot["files"].append(
        {
            "path": content_meta["path"],
            "bytes": (ROOT / content_meta["path"]).stat().st_size,
            "sha256": content_meta["sha256"],
            "record_count": content_meta["record_count"],
        }
    )
    curated, curated_meta = curated_records()
    discovered = [govern_record(record, sources, rights) for record in discovered]
    curated = [govern_record(record, sources, rights) for record in curated]
    records, reconciliation = merge_records(discovered, curated)
    if not records:
        raise ValueError("refusing to publish an empty catalogue")
    validate_evaluation_caveat_bindings(records)
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
            "publication_state": config["publication_state"],
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
            "by_kind": counter(records, "kind"),
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
            "release_authority": rights_governance["release_authority"],
            "field_semantics": rights_governance["field_semantics"],
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
                    "caveat_ids": record["caveat_ids"],
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
        explorer_projection = write_explorer_projection(
            staging, records, snapshot, config
        )
        write_static_catalogue(staging, records)
        evaluation_process = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "evaluate.py"),
                "--bundle",
                str(staging),
                "--output",
                str(staging / "data" / "evaluation-report.json"),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if evaluation_process.returncode != 0:
            detail = (
                evaluation_process.stderr.strip()
                or evaluation_process.stdout.strip()
                or f"exit {evaluation_process.returncode}"
            )
            raise ValueError(f"evaluation failed: {detail}")

        descriptor = make_descriptor(
            publication_base,
            snapshot,
            records,
            curated_meta,
            config,
            reconciliation,
            explorer_projection,
        )
        write_json(staging / "okf-explorer.json", descriptor)
        write_json(
            staging / "context.jsonld",
            load_json(ROOT / "source" / "jsonld-context.json"),
        )
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
            "publication_state": config["publication_state"],
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
