#!/usr/bin/env python3
"""Build the HM Land Registry metadata-only OKF Bundle.

The build is deliberately offline. Network acquisition is a separate,
reviewable step implemented by ``scripts/acquire.py``. This script consumes a
frozen public-metadata snapshot plus curated control records; it never calls an
HMLR service and never reads credentials.
"""

from __future__ import annotations

import argparse
import copy
import ctypes
import csv
import errno
import functools
import hashlib
import html
import io
import json
import os
import re
import selectors
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata
from collections import Counter
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import (
    parse_qsl,
    quote,
    unquote,
    urlencode,
    urljoin,
    urlparse,
    urlunparse,
)

try:
    import fcntl
except ImportError:  # pragma: no cover - publication fails closed off POSIX
    fcntl = None  # type: ignore[assignment]

from jsonschema import Draft202012Validator, FormatChecker
from ruamel.yaml import YAML

if __package__ in {None, ""}:
    # ``-I`` deliberately removes the script directory.  Reintroduce only this
    # reviewed repository scripts directory for the governed direct command.
    sys.path.insert(0, str(Path(__file__).resolve().parent))

if __package__:
    from scripts.change_impact import (
        ChangeImpactError,
        REQUIRED_BUILD_INPUT_PATTERNS,
        REQUIRED_GENERATED_ROOT_PATTERNS,
        normalise_dependency_pattern,
        validate_build_input_contract,
        validate_executable_causal_bootstrap,
    )
    from scripts.python_runtime_contract import (
        DETERMINISTIC_GZIP_CONTRACT,
        DETERMINISTIC_GZIP_GOLDEN_INPUT,
        DETERMINISTIC_GZIP_GOLDEN_SHA256,
        PythonRuntimeContractError,
        deterministic_gzip_bytes,
        observe_python_runtime,
        parse_hashed_requirements_lock,
    )
else:  # pragma: no cover - exercised by the direct build command
    from change_impact import (  # type: ignore[no-redef]
        ChangeImpactError,
        REQUIRED_BUILD_INPUT_PATTERNS,
        REQUIRED_GENERATED_ROOT_PATTERNS,
        normalise_dependency_pattern,
        validate_build_input_contract,
        validate_executable_causal_bootstrap,
    )
    from python_runtime_contract import (  # type: ignore[no-redef]
        DETERMINISTIC_GZIP_CONTRACT,
        DETERMINISTIC_GZIP_GOLDEN_INPUT,
        DETERMINISTIC_GZIP_GOLDEN_SHA256,
        PythonRuntimeContractError,
        deterministic_gzip_bytes,
        observe_python_runtime,
        parse_hashed_requirements_lock,
    )

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "bundle"
PUBLICATION_BASE = "https://chris-page-gov.github.io/okf-LandRegistry/"
BUILD_VERSION = "0.3.0"
SOURCE_MODEL_VERSION = "0.2.0"
RESEARCH_CUTOFF = "2026-07-29"
SHARD_SIZE = 250
GENERATED_MARKER = ".okf-generated"
TRANSLATION_PREDICATE = "https://schema.org/translationOfWork"
PUBLISHER_PREDICATE = "http://purl.org/dc/terms/publisher"
COMPETENT_AUTHORITY_PREDICATE = "http://data.europa.eu/m8g/hasCompetentAuthority"
CATALOGUE_RECORD_PREDICATE = "http://www.w3.org/ns/dcat#record"
CATALOGUE_RESOURCE_PREDICATE = "http://www.w3.org/ns/dcat#resource"
CATALOGUE_DATASET_PREDICATE = "http://www.w3.org/ns/dcat#dataset"
PRIMARY_TOPIC_PREDICATE = "http://xmlns.com/foaf/0.1/primaryTopic"
SOURCE_PREDICATE = "http://purl.org/dc/terms/source"
DERIVED_FROM_PREDICATE = "http://www.w3.org/ns/prov#wasDerivedFrom"
RIGHTS_PREDICATE = "http://purl.org/dc/terms/rights"
GENERATED_BY_PREDICATE = "http://www.w3.org/ns/prov#wasGeneratedBy"
LANGUAGE_PREDICATE = "http://purl.org/dc/terms/language"
SPATIAL_PREDICATE = "http://purl.org/dc/terms/spatial"
GOVERNED_RELATIONSHIP_PREDICATES = frozenset(
    {
        CATALOGUE_RECORD_PREDICATE,
        CATALOGUE_RESOURCE_PREDICATE,
        CATALOGUE_DATASET_PREDICATE,
        PRIMARY_TOPIC_PREDICATE,
        SOURCE_PREDICATE,
        DERIVED_FROM_PREDICATE,
        RIGHTS_PREDICATE,
        GENERATED_BY_PREDICATE,
        LANGUAGE_PREDICATE,
        PUBLISHER_PREDICATE,
        TRANSLATION_PREDICATE,
        COMPETENT_AUTHORITY_PREDICATE,
        SPATIAL_PREDICATE,
    }
)
GOVERNED_COMPACT_PREDICATES = {
    "dcat:record": CATALOGUE_RECORD_PREDICATE,
    "dcat:resource": CATALOGUE_RESOURCE_PREDICATE,
    "dcat:dataset": CATALOGUE_DATASET_PREDICATE,
    "foaf:primaryTopic": PRIMARY_TOPIC_PREDICATE,
    "dcterms:source": SOURCE_PREDICATE,
    "prov:wasDerivedFrom": DERIVED_FROM_PREDICATE,
    "dcterms:rights": RIGHTS_PREDICATE,
    "prov:wasGeneratedBy": GENERATED_BY_PREDICATE,
    "dcterms:language": LANGUAGE_PREDICATE,
    "dcterms:publisher": PUBLISHER_PREDICATE,
    "schema:translationOfWork": TRANSLATION_PREDICATE,
    "cv:hasCompetentAuthority": COMPETENT_AUTHORITY_PREDICATE,
    "dcterms:spatial": SPATIAL_PREDICATE,
}
BUNDLE_PROFILE_URL = (
    "https://chris-page-gov.github.io/okf-explorer/profile/bundle-wiki/v1/"
)
PROFILE_ROOT = ROOT / "profiles" / "bundle-wiki" / "v1"
PROFILE_LOCK_PATH = ROOT / "profiles" / "bundle-wiki" / "v1.vendor-lock.json"
SEMANTIC_CONTEXT_URL = BUNDLE_PROFILE_URL + "semantic-context.jsonld"
SEMANTIC_MODEL_BUNDLE_PATH = "data/semantic/model.json"
SEMANTIC_CONTEXT_BUNDLE_PATH = "data/semantic/semantic-context.jsonld"
IRI_ROUTE_REGISTRY_BUNDLE_PATH = "data/semantic/iri-route-registry.json"
PREDICATE_REGISTRY_BUNDLE_PATH = "data/semantic/predicate-registry.json"
CPSV_AP_VERSION = "3.2.0"
CPSV_AP_ROOT = ROOT / "standards" / "cpsv-ap" / CPSV_AP_VERSION
CPSV_AP_LOCK_PATH = ROOT / "standards" / "cpsv-ap" / "3.2.0.vendor-lock.json"
CPSV_AP_BUNDLE_ROOT = "data/semantic/standards/cpsv-ap/3.2.0"
CPSV_AP_CONTEXT_URL = (
    "https://semiceu.github.io/CPSV-AP/releases/3.2.0/context/cpsv-ap.jsonld"
)
CPSV_AP_VOCABULARY_URL = (
    "https://semiceu.github.io/CPSV-AP/releases/3.2.0/rdf/cpsv-ap.ttl"
)
CPSV_AP_SHACL_URL = (
    "https://semiceu.github.io/CPSV-AP/releases/3.2.0/shacl/cpsv-ap-SHACL.ttl"
)
HMLR_PUBLISHER_IRI = "https://www.gov.uk/government/organisations/land-registry"
CPSV_SERVICE_MAPPING_PATH = ROOT / "source" / "cpsv-service-mappings.json"
CPSV_SERVICE_MAPPING_BUNDLE_PATH = "data/semantic/cpsv-service-mappings.json"
CURATED_RIGHTS_ACCESS_PATH = ROOT / "source" / "curated-rights-access.json"
CURATED_RIGHTS_ACCESS_SCHEMA_PATH = (
    ROOT / "schemas" / "curated-rights-access.schema.json"
)
CURATED_RIGHTS_ACCESS_SCHEMA_ID = (
    "https://chris-page-gov.github.io/okf-LandRegistry/schemas/"
    "curated-rights-access.schema.json"
)
CPSV_MAPPING_RULE_IRI = (
    "https://chris-page-gov.github.io/okf-LandRegistry/id/rule/"
    "cpsv-ap-3.2.0-service-decision-v1"
)
CPSV_SOURCE_VALUE_CANONICALIZATION = (
    "sorted-key compact UTF-8 JSON with trailing newline"
)
RICH_RELATIONSHIP_RUNTIME_BUNDLE_PATH = "data/semantic/runtime-manifest.json"
RICH_RELATIONSHIP_ROW_SCHEMA_PATH = (
    ROOT / "schemas" / "relationship-runtime-row.schema.json"
)
RICH_RELATIONSHIP_ROW_SCHEMA_BUNDLE_PATH = (
    "data/semantic/relationship-runtime-row.schema.json"
)
RICH_RELATIONSHIP_PLANE_IRI = "urn:okf:hmlr:plane:core"
EXPLORER_CONSUMER_LOCK_PATH = (
    ROOT / "contracts" / "okf-explorer.consumer-lock.json"
)
EXPLORER_V060_COMMIT = "4bb7b92a64b7ba69bde9b1e86786217338cd166d"
EXPLORER_V060_LARGE_CORPUS_SHA256 = (
    "d5fdbeb7c5b586d2d0b6b576d997a391fab8f042ff1e826c551e29bb05d0f1b8"
)
RICH_RELATIONSHIP_LIMIT_NAMES = frozenset(
    {
        "maximum_json_bytes",
        "maximum_relationship_rows",
        "maximum_rich_relationship_route_chunks",
        "maximum_rich_relationship_route_rows",
        "maximum_rich_relationship_chunk_rows",
        "maximum_rich_relationship_chunk_bytes",
        "maximum_rich_relationship_decoded_chunk_bytes",
        "maximum_rich_relationship_hydration_compressed_bytes",
        "maximum_rich_relationship_retained_text_units",
        "maximum_rich_relationship_row_text_units",
        "maximum_rich_relationship_evidence_items",
        "maximum_rich_relationship_supporting_assertions",
        "maximum_rich_relationship_cached_chunks",
        "maximum_rich_relationship_planes",
        "maximum_rich_relationship_chunks",
    }
)
EXPLORER_V060_RICH_RELATIONSHIP_LIMITS = {
    "maximum_json_bytes": 67_108_864,
    "maximum_relationship_rows": 300_000,
    "maximum_rich_relationship_route_chunks": 64,
    "maximum_rich_relationship_route_rows": 100_000,
    "maximum_rich_relationship_chunk_rows": 50_000,
    "maximum_rich_relationship_chunk_bytes": 8_388_608,
    "maximum_rich_relationship_decoded_chunk_bytes": 67_108_864,
    "maximum_rich_relationship_hydration_compressed_bytes": 67_108_864,
    "maximum_rich_relationship_retained_text_units": 33_554_432,
    "maximum_rich_relationship_row_text_units": 32_768,
    "maximum_rich_relationship_evidence_items": 16,
    "maximum_rich_relationship_supporting_assertions": 128,
    "maximum_rich_relationship_cached_chunks": 16,
    "maximum_rich_relationship_planes": 16,
    "maximum_rich_relationship_chunks": 10_000,
}
SEMANTIC_ASSERTION_SCHEMA_PATH = ROOT / "schemas" / "semantic-assertion.schema.json"
SEMANTIC_ASSERTION_SCHEMA_BUNDLE_PATH = (
    "data/semantic/semantic-assertion.schema.json"
)
SEMANTIC_ASSERTION_VALIDATION_BUNDLE_PATH = "data/semantic/validation.json"
SEMANTIC_ASSERTION_SCHEMA_ID = (
    "https://chris-page-gov.github.io/okf-explorer/profile/"
    "bundle-wiki/v1/semantic-assertion.schema.json"
)
SEMANTIC_ASSERTION_SCHEMA_DRAFT = (
    "https://json-schema.org/draft/2020-12/schema"
)
SEMANTIC_ASSERTION_SCHEMA_BYTES = 7308
SEMANTIC_ASSERTION_SCHEMA_SHA256 = (
    "f69480328db4b64d678d9c50b6534d808000f7fb50a30e8cc9e3bf2facbcb8bc"
)
MAX_QUERY_FIELDS = 128
MAX_QUERY_PERCENT_DECODE_PASSES = 4
QUERY_UNSAFE_CHARACTERS = frozenset("\"'<>\\^`{|};")
SENSITIVE_QUERY_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "auth",
        "authorization",
        "aws_access_key_id",
        "awsaccesskeyid",
        "client_secret",
        "code",
        "credential",
        "credentials",
        "download_token",
        "expires",
        "google_access_id",
        "googleaccessid",
        "jwt",
        "key",
        "key_pair_id",
        "password",
        "saml_request",
        "saml_response",
        "samlresponse",
        "secret",
        "session",
        "session_id",
        "sessionid",
        "shared_access_signature",
        "sig",
        "signature",
        "token",
        "x_amz_credential",
        "x_amz_security_token",
        "x_amz_signature",
        "x_goog_credential",
        "x_goog_signature",
    }
)
SENSITIVE_QUERY_PREFIXES = ("oauth_", "x_amz_", "x_goog_")
SENSITIVE_QUERY_SUFFIXES = (
    "access_token",
    "api_key",
    "auth",
    "authorization",
    "credential",
    "credentials",
    "jwt",
    "password",
    "secret",
    "session",
    "session_id",
    "sig",
    "signature",
    "token",
)
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
    "cross-government-data-catalogues": "RIGHT-CATALOGUE",
    "fee-calculator": "RIGHT-FEE-CALCULATOR",
    "legislation": "RIGHT-LEGISLATION",
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
NON_CURATED_RIGHTS_DEFAULT_FAMILIES = frozenset(
    {"govuk-search", "govuk-content", "github", "cddo-api-catalogue"}
)
EVIDENCE_BY_SOURCE_FAMILY = {
    "govuk-search": ["EV-GOVUK-SEARCH", "EV-ACQUISITION-SNAPSHOT"],
    "govuk-content": ["EV-GOVUK-SEARCH"],
    "govuk-hmlr": ["EV-HMLR-ORG"],
    "blog": ["EV-BLOG"],
    "cross-government-data-catalogues": ["EV-INVENTORY", "EV-DCAT"],
    "fee-calculator": ["EV-INVENTORY"],
    "legislation": ["EV-INVENTORY"],
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

# Release builds are deliberately smaller than these ceilings.  The limits are
# executable safety contracts, not tuning suggestions: they prevent a damaged
# Git index or unexpectedly large source file from being materialised without
# bound before the candidate is even validated.
MAX_GIT_INVENTORY_BYTES = 16 * 1024 * 1024
MAX_GIT_DIAGNOSTIC_BYTES = 256 * 1024
MAX_GIT_INVENTORY_PATHS = 100_000
MAX_CAUSAL_INPUT_FILES = 4_096
MAX_CAUSAL_INPUT_FILE_BYTES = 64 * 1024 * 1024
MAX_CAUSAL_INPUT_TOTAL_BYTES = 256 * 1024 * 1024
MAX_JSON_INPUT_BYTES = 128 * 1024 * 1024
MAX_GENERATED_FILE_BYTES = 512 * 1024 * 1024
FILE_READ_CHUNK_BYTES = 1024 * 1024
GIT_COMMAND_TIMEOUT_SECONDS = 30
EVALUATOR_TIMEOUT_SECONDS = 60
MAX_EVALUATOR_OUTPUT_BYTES = 256 * 1024
MAX_EVALUATION_REPORT_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class FrozenBuildInput:
    """One index-bound causal input captured for a single build transaction."""

    relative_path: str
    payload: bytes
    sha256: str
    identity: tuple[int, int, int, int, int]
    index_mode: str
    index_oid: str


@dataclass
class BuildInputSnapshot:
    """Immutable bytes consumed by and receipted for one release build.

    A release build is a transaction over the exact stage-0 Git index.  Every
    causal input is read once through a no-follow descriptor, compared with its
    indexed Git blob, and retained in memory.  All later repository reads use
    these frozen bytes; a final verification detects worktree or index changes
    before the generated directory is installed.
    """

    repository_root: Path
    files: dict[str, FrozenBuildInput]
    index_entries: dict[str, tuple[str, str]]

    @classmethod
    def capture(cls, repository_root: Path) -> "BuildInputSnapshot":
        repository_root = repository_root.resolve()
        graph = load_artifact_dependency_graph()
        paths = dependency_graph_build_input_paths(
            graph,
            repository_root=repository_root,
        )
        if len(paths) > MAX_CAUSAL_INPUT_FILES:
            raise ValueError(
                "causal input inventory exceeds the executable file-count ceiling: "
                f"{len(paths)} > {MAX_CAUSAL_INPUT_FILES}"
            )
        indexed_paths = set(
            _git_eligible_repository_paths(repository_root, indexed_only=True)
        )
        unindexed = sorted(
            path.relative_to(repository_root).as_posix()
            for path in paths
            if path.relative_to(repository_root).as_posix() not in indexed_paths
        )
        if unindexed:
            raise ValueError(
                "every causal release input must be indexed before build: "
                + ", ".join(unindexed)
            )
        index_entries = _git_index_entries(repository_root)
        frozen: dict[str, FrozenBuildInput] = {}
        total_bytes = 0
        object_format = _git_object_format(repository_root)
        for path in paths:
            relative = path.relative_to(repository_root).as_posix()
            index_entry = index_entries.get(relative)
            if index_entry is None:
                raise ValueError(f"causal input is not indexed at stage 0: {relative}")
            index_mode, index_oid = index_entry
            payload, identity = _bounded_read_file(
                path,
                maximum_bytes=MAX_CAUSAL_INPUT_FILE_BYTES,
                field=f"causal input {relative}",
            )
            total_bytes += len(payload)
            if total_bytes > MAX_CAUSAL_INPUT_TOTAL_BYTES:
                raise ValueError(
                    "causal input inventory exceeds the executable aggregate-byte "
                    f"ceiling: {total_bytes} > {MAX_CAUSAL_INPUT_TOTAL_BYTES}"
                )
            observed_oid = _git_blob_oid(payload, object_format)
            if observed_oid != index_oid:
                raise ValueError(
                    "causal input worktree bytes differ from the stage-0 Git index: "
                    f"{relative}"
                )
            frozen[relative] = FrozenBuildInput(
                relative_path=relative,
                payload=payload,
                sha256=sha256_bytes(payload),
                identity=identity,
                index_mode=index_mode,
                index_oid=index_oid,
            )
        expected = set(index_entries) & {
            path.relative_to(repository_root).as_posix() for path in paths
        }
        if expected != set(frozen):  # defensive: paths and stage-0 index diverged
            raise ValueError("causal input snapshot and Git index selection differ")
        return cls(repository_root, frozen, index_entries)

    def _relative_path(self, path: Path) -> str | None:
        try:
            return path.absolute().relative_to(self.repository_root).as_posix()
        except ValueError:
            return None

    def input_for_path(self, path: Path) -> FrozenBuildInput | None:
        relative = self._relative_path(path)
        return self.files.get(relative) if relative is not None else None

    def read_repository_bytes(self, path: Path) -> bytes:
        relative = self._relative_path(path)
        if relative is None:
            raise ValueError(f"causal read is outside the repository: {path}")
        frozen = self.files.get(relative)
        if frozen is None:
            raise ValueError(
                "build attempted an undeclared causal repository read: " + relative
            )
        return frozen.payload

    def receipt_rows(self) -> list[dict[str, Any]]:
        return [
            {
                "path": relative,
                "bytes": len(frozen.payload),
                "sha256": frozen.sha256,
            }
            for relative, frozen in sorted(self.files.items())
        ]

    def verify_unchanged(self) -> None:
        """Fail if either the worktree or stage-0 index changed during build."""

        if _git_index_entries(self.repository_root) != self.index_entries:
            raise ValueError("Git index changed during the release build")
        for relative, frozen in sorted(self.files.items()):
            path = self.repository_root / PurePosixPath(relative)
            payload, identity = _bounded_read_file(
                path,
                maximum_bytes=MAX_CAUSAL_INPUT_FILE_BYTES,
                field=f"causal input {relative}",
            )
            if identity != frozen.identity or payload != frozen.payload:
                raise ValueError(
                    "causal input changed after it was captured: " + relative
                )


_ACTIVE_BUILD_INPUT_SNAPSHOT: BuildInputSnapshot | None = None


@contextmanager
def activate_build_input_snapshot(snapshot: BuildInputSnapshot) -> Iterable[None]:
    """Route repository reads through one immutable causal snapshot."""

    global _ACTIVE_BUILD_INPUT_SNAPSHOT
    if _ACTIVE_BUILD_INPUT_SNAPSHOT is not None:
        raise RuntimeError("a build input snapshot is already active")
    _ACTIVE_BUILD_INPUT_SNAPSHOT = snapshot
    try:
        yield
    finally:
        _ACTIVE_BUILD_INPUT_SNAPSHOT = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot-dir",
        type=Path,
        required=True,
        help="Exact frozen acquisition directory selected for this build.",
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
    parser.add_argument(
        "--previous-output",
        type=Path,
        help=(
            "Exact absolute, non-existent path outside the repository used as "
            "the candidate swap slot. Its existing parent must be on the output "
            "file system. Required with --replace when the output exists; after "
            "an atomic exchange it contains the complete previous bundle."
        ),
    )
    return parser.parse_args()


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _bounded_read_file(
    path: Path,
    *,
    maximum_bytes: int,
    field: str,
) -> tuple[bytes, tuple[int, int, int, int, int]]:
    """Read one regular file through a no-follow descriptor within a ceiling."""

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"cannot open {field}: {exc}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"{field} is not a regular file")
        if before.st_size > maximum_bytes:
            raise ValueError(
                f"{field} exceeds the {maximum_bytes}-byte ceiling: "
                f"{before.st_size}"
            )
        chunks: list[bytes] = []
        observed = 0
        while True:
            chunk = os.read(descriptor, min(FILE_READ_CHUNK_BYTES, maximum_bytes + 1))
            if not chunk:
                break
            observed += len(chunk)
            if observed > maximum_bytes:
                raise ValueError(f"{field} exceeds the {maximum_bytes}-byte ceiling")
            chunks.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    before_identity = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    after_identity = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    payload = b"".join(chunks)
    if before_identity != after_identity or len(payload) != before.st_size:
        raise ValueError(f"{field} changed while it was being read")
    return payload, before_identity


def _is_generated_or_staging_path(relative_path: str) -> bool:
    first = PurePosixPath(relative_path).parts[0] if relative_path else ""
    if first.startswith(".okf-build-"):
        return True
    generated = tuple(
        normalise_dependency_pattern(
            pattern,
            field="executable generated root",
            input_pattern=False,
        )
        for pattern in REQUIRED_GENERATED_ROOT_PATTERNS
    )
    return any(
        _dependency_pattern_matches(relative_path, pattern)
        for pattern in generated
    )


def repository_bytes(
    path: Path,
    *,
    maximum_bytes: int = MAX_GENERATED_FILE_BYTES,
    field: str | None = None,
) -> bytes:
    """Read a file, using captured bytes for every active causal input."""

    path = Path(path)
    snapshot = _ACTIVE_BUILD_INPUT_SNAPSHOT
    if snapshot is not None:
        frozen = snapshot.input_for_path(path)
        if frozen is not None:
            return frozen.payload
        relative = snapshot._relative_path(path)
        if relative is not None and not _is_generated_or_staging_path(relative):
            raise ValueError(
                "build attempted an undeclared causal repository read: " + relative
            )
    payload, _identity = _bounded_read_file(
        path,
        maximum_bytes=maximum_bytes,
        field=field or str(path),
    )
    return payload


def repository_text(
    path: Path,
    *,
    maximum_bytes: int = MAX_GENERATED_FILE_BYTES,
    field: str | None = None,
) -> str:
    try:
        return repository_bytes(
            path,
            maximum_bytes=maximum_bytes,
            field=field,
        ).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{field or path} is not valid UTF-8") from exc


def copy_repository_input(source: Path, destination: Path) -> None:
    """Copy an authored input from the immutable build snapshot."""

    payload = repository_bytes(
        source,
        maximum_bytes=MAX_CAUSAL_INPUT_FILE_BYTES,
        field=f"causal copy source {source}",
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)


def load_json(path: Path) -> Any:
    try:
        return json.loads(
            repository_text(
                path,
                maximum_bytes=MAX_JSON_INPUT_BYTES,
                field=f"JSON input {path}",
            )
        )
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON input {path}: {exc}") from exc


@functools.lru_cache(maxsize=1)
def locked_rich_relationship_limits() -> dict[str, int]:
    """Load the exact rich-runtime ceilings admitted by Explorer v0.6.0."""
    lock = load_json(EXPLORER_CONSUMER_LOCK_PATH)
    consumer = lock.get("consumer", {})
    runtime = lock.get("rich_relationship_runtime", {})
    if (
        lock.get("schema") != "okf-explorer-consumer-lock.v1"
        or consumer.get("version") != "0.6.0"
        or consumer.get("release_tag") != "v0.6.0"
        or consumer.get("commit_sha") != EXPLORER_V060_COMMIT
        or runtime.get("manifest_schema")
        != "okf-rich-relationship-runtime-manifest.v1"
        or runtime.get("row_schema") != "okf-relationship-runtime-row.v1"
        or runtime.get("route_locator_schema")
        != "okf-rich-relationship-route-locator.v1"
        or runtime.get("route_bucket_schema")
        != "okf-rich-relationship-route-locator-bucket.v1"
        or runtime.get("route_hash_algorithm")
        != "sha256-utf8-first-byte-hex"
        or runtime.get("content_encoding") != "gzip"
    ):
        raise ValueError(
            "Explorer rich relationship consumer-lock identity is unsupported"
        )
    source_digests = {
        row.get("path"): row.get("sha256")
        for row in consumer.get("contract_sources", [])
        if isinstance(row, dict)
    }
    if (
        source_digests.get("apps/okf-explorer/src/lib/sources/largeCorpus.ts")
        != EXPLORER_V060_LARGE_CORPUS_SHA256
    ):
        raise ValueError(
            "Explorer large-corpus runtime source identity is unsupported"
        )
    declared = lock.get("limits")
    if not isinstance(declared, dict):
        raise ValueError("Explorer consumer lock has no limits object")
    limits: dict[str, int] = {}
    for name in sorted(RICH_RELATIONSHIP_LIMIT_NAMES):
        value = declared.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(
                f"Explorer consumer lock limit {name!r} is absent or invalid"
            )
        limits[name] = value
    if limits != EXPLORER_V060_RICH_RELATIONSHIP_LIMITS:
        raise ValueError(
            "Explorer consumer-lock limits differ from the executable "
            "Explorer v0.6.0 contract"
        )
    if (
        limits["maximum_rich_relationship_cached_chunks"]
        > limits["maximum_rich_relationship_route_chunks"]
    ):
        raise ValueError(
            "Explorer rich relationship cache ceiling exceeds its route ceiling"
        )
    if (
        limits["maximum_rich_relationship_decoded_chunk_bytes"]
        != limits["maximum_json_bytes"]
    ):
        raise ValueError(
            "Explorer rich relationship decoded ceiling differs from its "
            "pinned JSON ceiling"
        )
    return limits


def load_pinned_semantic_assertion_schema(
) -> tuple[Draft202012Validator, dict[str, Any]]:
    """Load the exact final Explorer schema without remote resolution."""
    raw = repository_bytes(
        SEMANTIC_ASSERTION_SCHEMA_PATH,
        maximum_bytes=MAX_CAUSAL_INPUT_FILE_BYTES,
        field="semantic assertion schema",
    )
    digest = sha256_bytes(raw)
    if len(raw) != SEMANTIC_ASSERTION_SCHEMA_BYTES:
        raise ValueError(
            "semantic assertion schema byte count changed: "
            f"{len(raw)} != {SEMANTIC_ASSERTION_SCHEMA_BYTES}"
        )
    if digest != SEMANTIC_ASSERTION_SCHEMA_SHA256:
        raise ValueError(
            "semantic assertion schema digest changed: "
            f"{digest} != {SEMANTIC_ASSERTION_SCHEMA_SHA256}"
        )
    schema = json.loads(raw)
    if not isinstance(schema, dict):
        raise ValueError("semantic assertion schema must be a JSON object")
    if schema.get("$id") != SEMANTIC_ASSERTION_SCHEMA_ID:
        raise ValueError("semantic assertion schema identity changed")
    if schema.get("$schema") != SEMANTIC_ASSERTION_SCHEMA_DRAFT:
        raise ValueError("semantic assertion schema draft changed")

    def local_references(value: Any) -> Iterable[str]:
        if isinstance(value, dict):
            reference = value.get("$ref")
            if isinstance(reference, str):
                yield reference
            for child in value.values():
                yield from local_references(child)
        elif isinstance(value, list):
            for child in value:
                yield from local_references(child)

    remote_references = sorted(
        reference
        for reference in local_references(schema)
        if not reference.startswith("#/")
    )
    if remote_references:
        raise ValueError(
            "semantic assertion schema contains remote references: "
            + ", ".join(remote_references)
        )
    Draft202012Validator.check_schema(schema)
    binding = {
        "id": SEMANTIC_ASSERTION_SCHEMA_ID,
        "draft": SEMANTIC_ASSERTION_SCHEMA_DRAFT,
        "source_path": "schemas/semantic-assertion.schema.json",
        "bundle_path": SEMANTIC_ASSERTION_SCHEMA_BUNDLE_PATH,
        "bytes": len(raw),
        "sha256": digest,
        "network_resolution_allowed": False,
    }
    return (
        Draft202012Validator(schema, format_checker=FormatChecker()),
        binding,
    )


@functools.lru_cache(maxsize=1)
def validate_profile_vendor_lock() -> dict[str, Any]:
    """Verify every byte in the exact Explorer v0.6.0 semantic profile mirror."""
    lock = load_json(PROFILE_LOCK_PATH)
    release = lock.get("release", {})
    if (
        lock.get("schema") != "okf-profile-vendor-lock.v1"
        or lock.get("profile") != BUNDLE_PROFILE_URL
        or release.get("version") != "0.6.0"
        or release.get("commit")
        != "4bb7b92a64b7ba69bde9b1e86786217338cd166d"
    ):
        raise ValueError("Bundle Wiki profile vendor-lock identity is unsupported")
    rows = lock.get("files")
    if not isinstance(rows, list) or len(rows) != lock.get("file_count"):
        raise ValueError("Bundle Wiki profile vendor lock has an invalid inventory")
    actual_names: list[str] = []
    for path in PROFILE_ROOT.rglob("*"):
        if path.is_file() and not path.is_symlink():
            actual_names.append(path.relative_to(PROFILE_ROOT).as_posix())
            if len(actual_names) > MAX_CAUSAL_INPUT_FILES:
                raise ValueError(
                    "Bundle Wiki profile inventory exceeds its file-count ceiling"
                )
    actual_names.sort()
    declared_names = sorted(clean_text(row.get("path")) for row in rows)
    if actual_names != declared_names:
        raise ValueError("Bundle Wiki profile inventory differs from its vendor lock")
    if any(path.is_symlink() for path in PROFILE_ROOT.rglob("*")):
        raise ValueError("Bundle Wiki profile mirror must not contain symlinks")
    identity_lines: list[str] = []
    for row in sorted(rows, key=lambda item: clean_text(item.get("path"))):
        relative = clean_text(row.get("path"))
        path = PROFILE_ROOT / relative
        expected_bytes = row.get("bytes")
        expected_sha256 = clean_text(row.get("sha256"))
        if (
            not path.is_file()
            or path.stat().st_size != expected_bytes
            or sha256_file(path) != expected_sha256
        ):
            raise ValueError(
                f"Bundle Wiki profile file differs from its vendor lock: {relative}"
            )
        identity_lines.append(f"{relative}\t{expected_bytes}\t{expected_sha256}\n")
    identity_sha256 = sha256_bytes("".join(identity_lines).encode("utf-8"))
    if identity_sha256 != lock.get("identity", {}).get("sha256"):
        raise ValueError("Bundle Wiki profile aggregate identity differs")
    return {
        "status": "conformant",
        "version": release["version"],
        "release_commit": release["commit"],
        "file_count": len(rows),
        "identity_sha256": identity_sha256,
        "lock_sha256": sha256_file(PROFILE_LOCK_PATH),
    }


def load_profile_validator(name: str) -> Draft202012Validator:
    """Load one schema from the byte-exact vendored profile without networking."""
    validate_profile_vendor_lock()
    path = PROFILE_ROOT / name
    schema = load_json(path)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validate_semantic_context_alignment() -> dict[str, Any]:
    """Prove that every canonical assertion term retains its canonical mapping."""
    canonical_path = (
        ROOT
        / "profiles"
        / "bundle-wiki"
        / "v1"
        / "semantic-context.jsonld"
    )
    local_path = ROOT / "source" / "jsonld-context.json"
    canonical = load_json(canonical_path)["@context"]["assertions"]["@context"]
    local = load_json(local_path)["@context"]
    missing = sorted(set(canonical) - set(local))
    divergent = sorted(
        key for key in canonical if key in local and local[key] != canonical[key]
    )
    if missing or divergent:
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if divergent:
            details.append("divergent " + ", ".join(divergent))
        raise ValueError(
            "local semantic assertion context differs from the canonical profile: "
            + "; ".join(details)
        )
    return {
        "canonical_path": "profiles/bundle-wiki/v1/semantic-context.jsonld",
        "canonical_sha256": sha256_file(canonical_path),
        "local_path": "source/jsonld-context.json",
        "local_sha256": sha256_file(local_path),
        "canonical_terms_checked": len(canonical),
        "status": "conformant",
    }


def validate_cpsv_ap_vendor_lock() -> dict[str, Any]:
    """Verify the exact local CPSV-AP 3.2.0 context, vocabulary and SHACL set."""
    lock = load_json(CPSV_AP_LOCK_PATH)
    if (
        lock.get("schema") != "okf-external-standard-vendor-lock.v1"
        or lock.get("standard", {}).get("version") != CPSV_AP_VERSION
        or lock.get("release", {}).get("commit")
        != "ed1d6494afc7bc234ca21487cbaa89db409d5a61"
    ):
        raise ValueError("CPSV-AP vendor lock identity is unsupported")
    rows = lock.get("files")
    if not isinstance(rows, list) or len(rows) != 3:
        raise ValueError("CPSV-AP vendor lock must declare exactly three files")
    actual_names: list[str] = []
    for path in CPSV_AP_ROOT.iterdir():
        if path.is_file():
            actual_names.append(path.name)
            if len(actual_names) > MAX_CAUSAL_INPUT_FILES:
                raise ValueError("CPSV-AP inventory exceeds its file-count ceiling")
    actual_names.sort()
    declared_names = sorted(clean_text(row.get("path")) for row in rows)
    if actual_names != declared_names:
        raise ValueError("CPSV-AP vendored file inventory differs from its lock")
    identity_lines: list[str] = []
    for row in sorted(rows, key=lambda item: clean_text(item.get("path"))):
        relative = clean_text(row.get("path"))
        path = CPSV_AP_ROOT / relative
        expected_bytes = row.get("bytes")
        expected_sha256 = clean_text(row.get("sha256"))
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size != expected_bytes
            or sha256_file(path) != expected_sha256
        ):
            raise ValueError(f"CPSV-AP vendored file differs from its lock: {relative}")
        identity_lines.append(
            f"{relative}\t{expected_bytes}\t{expected_sha256}\n"
        )
    identity_sha256 = sha256_bytes("".join(identity_lines).encode("utf-8"))
    if identity_sha256 != lock.get("identity", {}).get("sha256"):
        raise ValueError("CPSV-AP aggregate vendored identity differs")
    load_json(CPSV_AP_ROOT / "cpsv-ap.jsonld")
    return {
        "status": "conformant",
        "version": CPSV_AP_VERSION,
        "release_commit": lock["release"]["commit"],
        "file_count": len(rows),
        "identity_sha256": identity_sha256,
        "lock_sha256": sha256_file(CPSV_AP_LOCK_PATH),
    }


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
    _required_utc_timestamp(
        config["generated_at"],
        "source/build-config.json generated_at",
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


def _sha256_live_regular_file(
    path: Path,
    *,
    maximum_bytes: int,
) -> str:
    """Incrementally hash a live regular file within an explicit ceiling."""
    path = Path(path)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"cannot open file for hashing {path}: {exc}") from exc
    digest = hashlib.sha256()
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"file to hash is not regular: {path}")
        if before.st_size > maximum_bytes:
            raise ValueError(
                f"file to hash exceeds the {maximum_bytes}-byte ceiling: {path}"
            )
        observed = 0
        while True:
            chunk = os.read(descriptor, FILE_READ_CHUNK_BYTES)
            if not chunk:
                break
            observed += len(chunk)
            if observed > maximum_bytes:
                raise ValueError(
                    f"file to hash exceeds the {maximum_bytes}-byte ceiling: {path}"
                )
            digest.update(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)

    def identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
        return (
            value.st_dev,
            value.st_ino,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )

    if identity(before) != identity(after) or observed != before.st_size:
        raise ValueError(f"file changed while it was being hashed: {path}")
    return digest.hexdigest()


def sha256_file(
    path: Path,
    *,
    maximum_bytes: int = MAX_GENERATED_FILE_BYTES,
) -> str:
    """Hash one file incrementally, or return its frozen causal digest."""

    path = Path(path)
    snapshot = _ACTIVE_BUILD_INPUT_SNAPSHOT
    if snapshot is not None:
        frozen = snapshot.input_for_path(path)
        if frozen is not None:
            return frozen.sha256
        relative = snapshot._relative_path(path)
        if relative is not None and not _is_generated_or_staging_path(relative):
            raise ValueError(
                "build attempted an undeclared causal repository read: " + relative
            )
    return _sha256_live_regular_file(path, maximum_bytes=maximum_bytes)


def _normalise_distribution_name(value: str) -> str:
    """Compatibility wrapper for focused tests; shared parsing is authoritative."""

    return re.sub(r"[-_.]+", "-", value).casefold()


def _locked_python_packages(lock_bytes: bytes) -> dict[str, tuple[str, str]]:
    try:
        packages = parse_hashed_requirements_lock(lock_bytes)
    except PythonRuntimeContractError as exc:
        raise ValueError(str(exc)) from exc
    return {
        package.normalised_name: (package.declared_name, package.version)
        for package in packages
    }


def python_runtime_receipt() -> dict[str, Any]:
    """Post-startup verify the shared runtime using immutable causal lock bytes."""

    lock_path = ROOT / "requirements-lock.txt"
    lock_bytes = repository_bytes(
        lock_path,
        maximum_bytes=MAX_CAUSAL_INPUT_FILE_BYTES,
        field="requirements-lock.txt",
    )
    try:
        return observe_python_runtime(ROOT, lock_bytes)
    except PythonRuntimeContractError as exc:
        raise ValueError(str(exc)) from exc


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(value))


def write_yaml_ld(path: Path, value: Any) -> None:
    """Write a deterministic YAML 1.2 serialisation of a JSON-LD document."""
    yaml = YAML()
    yaml.allow_duplicate_keys = False
    yaml.default_flow_style = False
    yaml.default_style = '"'
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.width = 4096
    yaml.representer.ignore_aliases = lambda _value: True
    stream = io.StringIO()
    yaml.dump(value, stream)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stream.getvalue(), encoding="utf-8", newline="\n")


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
    text = repository_text(
        ROOT / "domain-profile" / "CHECKSUMS.sha256",
        maximum_bytes=MAX_CAUSAL_INPUT_FILE_BYTES,
        field="domain profile checksums",
    )
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


def _normalise_query_key(value: str) -> str:
    """Split camel case, then collapse punctuation for credential matching."""
    camel_split = re.sub(
        r"(?<=[A-Z])(?=[A-Z][a-z])", "_", value.strip()
    )
    camel_split = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", camel_split)
    return re.sub(r"[^a-z0-9]+", "_", camel_split.casefold()).strip("_")


def _is_sensitive_query_key(value: str) -> bool:
    normalised = _normalise_query_key(value)
    return bool(normalised) and (
        normalised in SENSITIVE_QUERY_KEYS
        or normalised.startswith(SENSITIVE_QUERY_PREFIXES)
        or any(
            normalised.endswith("_" + suffix)
            for suffix in SENSITIVE_QUERY_SUFFIXES
        )
    )


def _query_component_variants(value: str, *, field: str) -> list[str]:
    """Expose bounded nested encodings for safety checks without rewriting data."""
    variants: list[str] = []
    current = value
    for pass_number in range(MAX_QUERY_PERCENT_DECODE_PASSES + 1):
        if current in variants:
            return variants
        variants.append(current)
        if not re.search(r"%[0-9A-Fa-f]{2}", current):
            return variants
        if pass_number == MAX_QUERY_PERCENT_DECODE_PASSES:
            raise ValueError(
                f"{field} contains excessively nested percent encoding"
            )
        try:
            current = unquote(current, encoding="utf-8", errors="strict")
        except UnicodeError as exc:
            raise ValueError(
                f"{field} contains invalid UTF-8 percent encoding"
            ) from exc
    return variants


def _validate_query_component(value: str, *, field: str) -> list[str]:
    variants = _query_component_variants(value, field=field)
    for variant in variants:
        if any(
            ord(character) < 0x20 or ord(character) == 0x7F
            for character in variant
        ):
            raise ValueError(f"{field} contains an encoded control character")
        unsafe = sorted(set(variant) & QUERY_UNSAFE_CHARACTERS)
        if unsafe:
            raise ValueError(
                f"{field} contains unsafe query delimiter(s) {unsafe}"
            )
    return variants


def _reject_embedded_sensitive_query(
    variants: list[str], *, field: str
) -> None:
    """Reject credentials hidden inside an encoded query-valued parameter."""
    for variant in variants:
        for index, segment in enumerate(re.split(r"[?&#;]", variant)):
            candidate, separator, _value = segment.partition("=")
            if (index > 0 or separator) and _is_sensitive_query_key(candidate):
                raise ValueError(
                    f"sensitive query parameter "
                    f"{_normalise_query_key(candidate)!r} is forbidden in {field}"
                )


def _canonical_query(query: str, *, field: str) -> str:
    """Parse once, reject ambiguous material and emit one ASCII query form."""
    if not query:
        return ""
    if ";" in query:
        raise ValueError(f"{field} contains an ambiguous semicolon query delimiter")
    try:
        pairs = parse_qsl(
            query,
            keep_blank_values=True,
            strict_parsing=False,
            encoding="utf-8",
            errors="strict",
            max_num_fields=MAX_QUERY_FIELDS,
        )
    except (UnicodeError, ValueError) as exc:
        raise ValueError(f"{field} contains an invalid or excessive query") from exc

    for key, value in pairs:
        key_variants = _validate_query_component(key, field=field)
        for variant in key_variants:
            if _is_sensitive_query_key(variant):
                raise ValueError(
                    f"sensitive query parameter "
                    f"{_normalise_query_key(variant)!r} is forbidden in {field}"
                )
        value_variants = _validate_query_component(value, field=field)
        _reject_embedded_sensitive_query(value_variants, field=field)

    return urlencode(
        pairs,
        doseq=False,
        safe="",
        encoding="utf-8",
        errors="strict",
        quote_via=quote,
    )


def _encode_cddo_path_template(url: str) -> str:
    """Encode reviewed CDDO path variables without accepting general URI templates."""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").casefold()
        port = parsed.port
    except ValueError as exc:
        raise ValueError("CDDO path template URL is malformed") from exc
    if (
        parsed.scheme != "https"
        or host != RESTRICTED_BUSINESS_GATEWAY_HOST
        or "@" in parsed.netloc
        or port is not None
        or "{" in parsed.query
        or "}" in parsed.query
        or "{" in parsed.fragment
        or "}" in parsed.fragment
    ):
        raise ValueError(
            "CDDO path templates are permitted only as path segments on the "
            "reviewed Business Gateway host"
        )
    encoded_segments: list[str] = []
    for segment in parsed.path.split("/"):
        if "{" not in segment and "}" not in segment:
            encoded_segments.append(segment)
            continue
        if not re.fullmatch(r"\{[A-Za-z][A-Za-z0-9_]*\}", segment):
            raise ValueError(
                "CDDO path template variable is not a whole safe segment"
            )
        encoded_segments.append("%7B" + segment[1:-1] + "%7D")
    return urlunparse(parsed._replace(path="/".join(encoded_segments)))


def canonical_https_url(
    value: Any,
    *,
    allowed_hosts: set[str] | frozenset[str] | None = None,
    allow_cddo_path_template: bool = False,
    field: str = "web URL",
) -> str:
    """Return a canonical HTTPS URL or reject unsafe input before projection."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    url = value
    if any(character.isspace() for character in url):
        raise ValueError(f"{field} contains literal whitespace")
    if any(ord(character) < 0x21 or ord(character) > 0x7E for character in url):
        raise ValueError(f"{field} contains non-canonical characters")
    if re.search(r"%(?![0-9A-Fa-f]{2})", url):
        raise ValueError(f"{field} contains a malformed percent escape")
    if allow_cddo_path_template and ("{" in url or "}" in url):
        url = _encode_cddo_path_template(url)
    unsafe = sorted(set(url) & set("\"'<>\\^`{|}"))
    if unsafe:
        raise ValueError(f"{field} contains unsafe delimiter(s) {unsafe}")
    try:
        parsed = urlparse(url)
        host = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{field} has an invalid authority or port") from exc
    if parsed.scheme != "https" or not parsed.netloc or not host:
        raise ValueError(f"{field} must be absolute HTTPS")
    if (
        "@" in parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError(f"credentials are forbidden in {field}")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError(f"{field} has an out-of-range port")
    authority = parsed.netloc.casefold()
    expected_authority = host.casefold()
    if port is not None:
        expected_authority += f":{port}"
    if authority != expected_authority:
        raise ValueError(f"{field} has a non-canonical authority")
    if allowed_hosts is not None and host.casefold() not in allowed_hosts:
        raise ValueError(
            f"{field} host {host.casefold()!r} is outside the reviewed allowlist"
        )
    query = _canonical_query(parsed.query, field=field)
    if parsed.fragment:
        _canonical_query(parsed.fragment, field=f"{field} fragment")
    return urlunparse(parsed._replace(query=query, fragment=""))


def ensure_https(url: Any, *, allow_cddo_path_template: bool = False) -> str:
    return canonical_https_url(
        url,
        allowed_hosts=PUBLIC_SOURCE_HOSTS,
        allow_cddo_path_template=allow_cddo_path_template,
        field="public record URL",
    )


def semantic_web_iri(url: str) -> str:
    """Validate a public source IRI against the pinned semantic contract."""
    return ensure_https(url)


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
    for rights_id, assessment in rights.items():
        family_ids = assessment.get("source_family_ids")
        if (
            not isinstance(family_ids, list)
            or len(family_ids) != len(set(family_ids))
            or any(not clean_text(value) for value in family_ids)
        ):
            raise ValueError(
                f"rights assessment {rights_id} has invalid source-family coverage"
            )
        unknown_families = sorted(set(family_ids) - set(sources))
        if unknown_families:
            raise ValueError(
                f"rights assessment {rights_id} covers unknown source families: "
                f"{unknown_families}"
            )
    for family_id, rights_id in RIGHTS_BY_SOURCE_FAMILY.items():
        if family_id not in rights[rights_id]["source_family_ids"]:
            raise ValueError(
                f"primary rights assessment {rights_id} does not cover "
                f"source family {family_id}"
            )
    return sources, rights


def _classification_error_path(error: Any) -> str:
    path = ".".join(str(value) for value in error.absolute_path)
    return path or "<root>"


def _validate_curated_classification_semantics(
    classification: dict[str, Any],
    source_record: dict[str, Any],
) -> None:
    """Reject access or rights labels contradicted by the exact authored row."""
    record_id = clean_text(source_record.get("id")) or "unknown"
    if clean_text(classification.get("source_native_id")) != record_id:
        raise ValueError(
            f"curated classification row does not bind its source-native ID: {record_id}"
        )
    scope = clean_text(classification.get("classification_scope"))
    access_state = clean_text(classification.get("access_state"))
    rights_state = clean_text(classification.get("rights_state"))
    rights_ref = clean_text(classification.get("rights_ref"))
    access_model = clean_text(source_record.get("access_model")).casefold()
    authentication = clean_text(source_record.get("authentication")).casefold()
    licence = clean_text(source_record.get("licence")).casefold()
    access_material = f"{access_model} {authentication}"
    rights_material = f"{licence} {access_model} {authentication}"

    if access_state == "public":
        if "none" not in authentication and scope != "help-metadata":
            raise ValueError(
                f"curated classification contradicts public access: {record_id}"
            )
    elif access_state == "public-with-conditions":
        if not any(value in authentication for value in ("none", "varies")):
            raise ValueError(
                f"curated classification contradicts conditional public access: "
                f"{record_id}"
            )
    elif access_state == "mixed":
        if not any(value in access_material for value in ("mixed", "varies")):
            raise ValueError(
                f"curated classification lacks mixed-access evidence: {record_id}"
            )
    elif access_state == "authenticated":
        if not any(
            value in access_material
            for value in ("account", "sign-in", "authenticated", "api key")
        ):
            raise ValueError(
                f"curated classification lacks authentication evidence: {record_id}"
            )
    elif access_state == "approved-professional-users":
        if not any(
            value in access_material
            for value in ("approved professional", "business e-services")
        ):
            raise ValueError(
                f"curated classification lacks professional-access evidence: "
                f"{record_id}"
            )
    elif access_state == "documentation-public-service-restricted":
        if "public documentation" not in access_model or not any(
            value in access_material
            for value in ("restricted", "approval", "certificate", "credential")
        ):
            raise ValueError(
                f"curated classification confuses documentation and service access: "
                f"{record_id}"
            )
    elif access_state in {
        "authenticated-and-paid",
        "authenticated-or-paid-by-licence",
    }:
        if not any(value in access_material for value in ("account", "sign-in")):
            raise ValueError(
                f"curated classification lacks account evidence: {record_id}"
            )
        if not any(
            value in rights_material
            for value in ("paid", "payment", "fee", "commercial")
        ):
            raise ValueError(
                f"curated classification lacks payment evidence: {record_id}"
            )

    if "free" in access_material and "paid" in access_state:
        raise ValueError(
            f"curated classification turns free access into paid access: {record_id}"
        )

    if rights_state == "open-with-conditions":
        if not any(
            value in licence for value in ("open government licence", "ogl v3.0")
        ):
            raise ValueError(
                f"curated classification infers open rights without evidence: "
                f"{record_id}"
            )
        if rights_ref not in {
            "RIGHT-GOVUK",
            "RIGHT-DATASETS",
            "RIGHT-LEGISLATION",
        }:
            raise ValueError(
                f"curated open-rights assessment is incompatible: {record_id}"
            )
    elif rights_state == "mixed":
        if rights_ref != "RIGHT-DATASETS" or not any(
            value in licence for value in ("dataset-specific", "common licence")
        ):
            raise ValueError(
                f"curated classification lacks mixed-rights evidence: {record_id}"
            )
    elif rights_state == "bespoke-licence":
        if rights_ref != "RIGHT-DATASETS" or "dataset-specific licence" not in licence:
            raise ValueError(
                f"curated classification lacks bespoke-licence evidence: {record_id}"
            )
    elif rights_state == "bespoke-or-paid":
        if rights_ref != "RIGHT-DATASETS" or not any(
            value in licence
            for value in ("bespoke", "direct use", "direct-use", "exploration", "commercial")
        ):
            raise ValueError(
                f"curated classification lacks bespoke or paid evidence: {record_id}"
            )
    elif rights_state == "restricted-service":
        if rights_ref not in {"RIGHT-RESTRICTED", "RIGHT-DATASETS"} or not any(
            value in rights_material
            for value in ("service", "portal", "operational", "api", "not an open")
        ):
            raise ValueError(
                f"curated classification lacks restricted-service evidence: "
                f"{record_id}"
            )
    elif rights_state == "restricted-personal-data":
        if rights_ref != "RIGHT-PERSONAL" or not any(
            value in rights_material
            for value in ("personal", "data-protection", "user-generated")
        ):
            raise ValueError(
                f"curated classification lacks personal-data evidence: {record_id}"
            )
    elif rights_state == "metadata-only":
        if rights_ref not in {
            "RIGHT-GITHUB",
            "RIGHT-CDDO",
            "RIGHT-CATALOGUE",
            "RIGHT-FEE-CALCULATOR",
            "RIGHT-RESTRICTED",
            "RIGHT-PERSONAL",
        }:
            raise ValueError(
                f"curated metadata-only assessment is incompatible: {record_id}"
            )
    elif rights_state == "conditional":
        if not licence:
            raise ValueError(
                f"curated conditional rights lack a licence position: {record_id}"
            )
    elif rights_state == "unknown":
        if (
            classification.get("classification_status")
            != "conservative-pending-specialist-review"
        ):
            raise ValueError(
                f"curated unknown rights lack a specialist-review flag: {record_id}"
            )

    if source_record.get("record_type") == "legislation":
        if (
            classification.get("source_family") != "legislation"
            or scope != "legislation-content"
            or rights_ref != "RIGHT-LEGISLATION"
        ):
            raise ValueError(
                f"legislation classification inherits a service family: {record_id}"
            )
    if source_record.get("source_family") == "cross-government-data-catalogues":
        if scope != "catalogue-metadata" or rights_ref != "RIGHT-CATALOGUE":
            raise ValueError(
                f"catalogue classification inherits publisher rights: {record_id}"
            )


def _index_exact_curated_classifications(
    source_rows: list[dict[str, Any]],
    classification_rows: list[dict[str, Any]],
    declared_record_count: Any,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Index two governed row sets only when their identities match exactly."""
    source_by_id: dict[str, dict[str, Any]] = {}
    for row in source_rows:
        source_id = clean_text(row.get("id")) if isinstance(row, dict) else ""
        if not source_id or source_id in source_by_id:
            raise ValueError("curated source-native IDs must be non-empty and unique")
        source_by_id[source_id] = row
    classification_by_id: dict[str, dict[str, Any]] = {}
    for row in classification_rows:
        source_id = clean_text(row.get("source_native_id"))
        if not source_id or source_id in classification_by_id:
            raise ValueError(
                "curated classification source-native IDs must be non-empty and unique"
            )
        classification_by_id[source_id] = row
    if declared_record_count != len(classification_rows):
        raise ValueError("curated classification record count does not reconcile")
    if set(source_by_id) != set(classification_by_id):
        missing = sorted(set(source_by_id) - set(classification_by_id))
        extra = sorted(set(classification_by_id) - set(source_by_id))
        raise ValueError(
            "curated classification coverage differs: "
            f"missing={missing}, extra={extra}"
        )
    return source_by_id, classification_by_id


@functools.lru_cache(maxsize=1)
def load_curated_rights_access_classifications(
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, Any],
]:
    """Validate and bind the exhaustive curated-record classification table."""
    schema = load_json(CURATED_RIGHTS_ACCESS_SCHEMA_PATH)
    if schema.get("$id") != CURATED_RIGHTS_ACCESS_SCHEMA_ID:
        raise ValueError("curated rights/access schema identity differs")
    Draft202012Validator.check_schema(schema)
    payload = load_json(CURATED_RIGHTS_ACCESS_PATH)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(
        validator.iter_errors(payload),
        key=lambda error: tuple(str(value) for value in error.absolute_path),
    )
    if errors:
        first = errors[0]
        raise ValueError(
            "curated rights/access classification schema failed at "
            f"{_classification_error_path(first)}: {first.message}"
        )
    if payload.get("research_cutoff") != RESEARCH_CUTOFF:
        raise ValueError("curated rights/access research cut-off differs")

    source_payload = load_json(ROOT / "source" / "curated-records.json")
    source_rows = source_payload.get("records")
    classification_rows = payload.get("classifications")
    if not isinstance(source_rows, list) or not isinstance(classification_rows, list):
        raise ValueError("curated source and classification rows must be arrays")
    source_by_id, classification_by_id = _index_exact_curated_classifications(
        source_rows,
        classification_rows,
        payload.get("record_count"),
    )

    classification_artifact = CURATED_RIGHTS_ACCESS_PATH.relative_to(ROOT).as_posix()
    classification_sha256 = sha256_file(CURATED_RIGHTS_ACCESS_PATH)
    bindings: dict[str, dict[str, Any]] = {}
    for source_id, classification in classification_by_id.items():
        source_record = source_by_id[source_id]
        if (
            classification.get("authored_source_family")
            != source_record.get("source_family")
            or classification.get("source_family") != source_record.get("source_family")
        ):
            raise ValueError(
                f"curated classification source family differs: {source_id}"
            )
        source_digest = sha256_bytes(compact_canonical_json(source_record))
        if classification.get("curated_record_sha256") != source_digest:
            raise ValueError(
                f"curated classification source-row digest differs: {source_id}"
            )
        evidence_url = ensure_https(classification.get("evidence_url"))
        governed_source_urls = {
            ensure_https(value)
            for value in [source_record.get("url"), *source_record.get("source_urls", [])]
            if value
        }
        if evidence_url not in governed_source_urls:
            raise ValueError(
                f"curated classification evidence URL is not source-bound: {source_id}"
            )
        additional_refs = classification.get("additional_rights_refs", [])
        if (
            classification["rights_ref"] in additional_refs
            or len(additional_refs) != len(set(additional_refs))
        ):
            raise ValueError(
                f"curated classification has invalid additional rights: {source_id}"
            )
        _validate_curated_classification_semantics(classification, source_record)
        source_field = f"classifications[source_native_id='{source_id}']"
        if resolve_source_field_value(classification_artifact, source_field) != classification:
            raise ValueError(
                f"curated classification locator does not round-trip: {source_id}"
            )
        bindings[source_id] = {
            "record_id": record_id_for(
                clean_text(classification["source_family"]), source_id
            ),
            "source_artifact": classification_artifact,
            "source_sha256": classification_sha256,
            "source_field": source_field,
            "source_value": classification,
            "source_urls": [evidence_url],
        }
    return classification_by_id, bindings, {
        "schema": payload["schema"],
        "path": classification_artifact,
        "sha256": classification_sha256,
        "schema_path": CURATED_RIGHTS_ACCESS_SCHEMA_PATH.relative_to(ROOT).as_posix(),
        "schema_sha256": sha256_file(CURATED_RIGHTS_ACCESS_SCHEMA_PATH),
        "record_count": len(classification_by_id),
        "coverage": "exhaustive-set-exact",
    }


@functools.lru_cache(maxsize=1)
def load_type_kind_crosswalk() -> tuple[dict[str, str], set[str]]:
    path = ROOT / "source" / "type-kind-crosswalk.json"
    payload = load_json(path)
    if payload.get("schema") != "okf-hmlr-type-kind-crosswalk.v1":
        raise ValueError("type-to-kind crosswalk has an unsupported schema")
    if payload.get("version") != SOURCE_MODEL_VERSION:
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
    if payload.get("version") != SOURCE_MODEL_VERSION:
        raise ValueError("publisher registry and builder versions differ")
    rows = payload.get("publishers")
    if not isinstance(rows, list) or not rows:
        raise ValueError("publisher registry must contain publishers")
    registry: dict[str, str] = {}
    for row in rows:
        name = clean_text(row.get("name")) if isinstance(row, dict) else ""
        identifier = ensure_https(row.get("id")) if name else ""
        if not name or not identifier or name in registry:
            raise ValueError("publisher registry names must be non-empty and unique")
        registry[name] = identifier
    return registry


def load_composite_input_manifest(snapshot_dir: Path) -> dict[str, Any]:
    path = ROOT / "source" / f"input-manifest-v{SOURCE_MODEL_VERSION}.json"
    payload = load_json(path)
    if (
        payload.get("schema") != "okf-hmlr-composite-input-manifest.v1"
        or payload.get("version") != SOURCE_MODEL_VERSION
    ):
        raise ValueError("composite input manifest does not match the frozen source model")
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

    source_family = clean_text(record["source_family"])
    allow_cddo_template = source_family == "cddo-api-catalogue"

    def governed_urls(value: Any) -> list[str]:
        values = value if isinstance(value, (list, tuple)) else [value]
        return [
            ensure_https(item, allow_cddo_path_template=allow_cddo_template)
            for item in values
            if item is not None and item != ""
        ]

    source_urls = governed_urls(record.get("source_urls"))
    canonical_url = ensure_https(
        record["url"], allow_cddo_path_template=allow_cddo_template
    )
    if canonical_url not in source_urls:
        source_urls.insert(0, canonical_url)
    equivalent_urls = governed_urls(record.get("equivalent_urls"))
    equivalent_urls = [
        url
        for url in equivalent_urls
        if url.rstrip("/") != canonical_url.rstrip("/")
    ]

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
    raw_url = item.get("url")
    url = ensure_https(raw_url, allow_cddo_path_template=True)
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
            "id": stable_id("cddo-api", clean_text(raw_url) or name),
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


def normalize_govuk_content_translation(
    translation: dict[str, Any], observed_at: str
) -> dict[str, Any]:
    """Normalise one exact Content API translation row."""
    content_id = clean_text(translation.get("content_id"))
    locale = clean_text(translation.get("locale"))
    url = clean_text(translation.get("web_url"))
    if not content_id or locale not in {"en", "cy"} or not url:
        raise ValueError("Content API translation identity is incomplete")
    return normal_record(
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
            "observed_at": observed_at,
            "translation_group": content_id,
            "caveats": [
                "Locale and translation relationships come from bounded Content API metadata.",
                "Rendered bodies, contacts, details and attachments are outside this bundle.",
            ],
            "source_urls": [url],
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
            key = (content_id, locale)
            records[key] = normalize_govuk_content_translation(
                translation,
                clean_text(payload.get("observed_at")),
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
    _classifications, _bindings, classification_meta = (
        load_curated_rights_access_classifications()
    )
    normalized = [normal_record(record) for record in records]
    for record in normalized:
        record["curation"] = "reviewed"
    return normalized, {
        "sha256": sha256_file(path),
        "record_count": len(normalized),
        "observed_at": clean_text(payload.get("observed_at")),
        "rights_access_classifications": classification_meta,
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
    classification: dict[str, Any] | None = None
    if governed["curation"] == "reviewed":
        classifications, _bindings, _receipt = (
            load_curated_rights_access_classifications()
        )
        classification = classifications.get(governed["source_native_id"])
        if classification is None:
            raise ValueError(
                "reviewed curated record lacks a rights/access classification: "
                f"{governed['source_native_id']}"
            )
        if classification["source_family"] != family_id:
            raise ValueError(
                "curated rights/access classification uses another source family: "
                f"{governed['source_native_id']}"
            )
        access_state = clean_text(classification["access_state"])
        rights_state = clean_text(classification["rights_state"])
        rights_ref = clean_text(classification["rights_ref"])
        additional_rights_refs = list(
            classification.get("additional_rights_refs", [])
        )
    else:
        if family_id not in NON_CURATED_RIGHTS_DEFAULT_FAMILIES:
            raise ValueError(
                "source-family rights defaults are limited to frozen discovery "
                f"inputs: {family_id}"
            )
        access_state = (
            "approved-professional-users"
            if restricted_business_gateway
            else clean_text(family.get("access_state")) or "unknown"
        )
        rights_state = (
            "restricted-service"
            if restricted_business_gateway
            else clean_text(family.get("rights_state")) or "unknown"
        )
        rights_ref = (
            "RIGHT-RESTRICTED"
            if restricted_business_gateway
            else RIGHTS_BY_SOURCE_FAMILY[family_id]
        )
        additional_rights_refs = []
    governed_rights_refs = [rights_ref, *additional_rights_refs]
    for governed_rights_ref in governed_rights_refs:
        assessment = rights.get(governed_rights_ref)
        if assessment is None:
            raise ValueError(
                f"record uses unknown rights assessment: {governed_rights_ref}"
            )
        if family_id not in assessment.get("source_family_ids", []):
            raise ValueError(
                f"rights assessment {governed_rights_ref} does not cover "
                f"record source family {family_id}"
            )
        if assessment.get("status") not in {
            "permitted",
            "conditional",
            "prohibited",
        }:
            raise ValueError(
                f"rights assessment {governed_rights_ref} has an unsupported status"
            )
    governed.update(
        {
            "access_state": access_state,
            "rights_state": rights_state,
            "rights_ref": rights_ref,
            "additional_rights_refs": additional_rights_refs,
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
    if classification is not None:
        governed.update(
            {
                "rights_access_scope": classification["classification_scope"],
                "metadata_page_access_state": classification[
                    "metadata_page_access_state"
                ],
                "rights_access_classification_status": classification[
                    "classification_status"
                ],
            }
        )
    governed["caveat_ids"] = caveat_ids_for(governed)
    if governed["access_state"] == "unknown" or governed["rights_state"] == "unknown":
        if (
            classification is None
            or classification.get("classification_status")
            != "conservative-pending-specialist-review"
        ):
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
        "semantic_descriptor": urljoin(publication_base, "okf-bundle.yamlld"),
        "semantic_serializations": {
            "canonical": {
                "format": "YAML-LD",
                "media_type": "application/ld+yaml",
                "path": "okf-bundle.yamlld",
            },
            "alternates": [
                {
                    "format": "JSON-LD",
                    "media_type": "application/ld+json",
                    "path": "okf-bundle.jsonld",
                }
            ],
            "graph_equivalence": (
                "Both files are deterministic serialisations of the same "
                "in-memory semantic graph."
            ),
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
            "relationship_runtime": explorer_projection[
                "relationship_runtime"
            ],
            "search_manifest": explorer_projection["search_manifest"]["path"],
            "catalogue": "data/catalogue.json",
            "catalogue_csv": "data/catalogue.csv",
            "catalogue_html": "catalogue-index.html",
            "inventory_manifest": "data/manifest.json",
            "coverage": "data/coverage.json",
            "provenance": "data/provenance.json",
            "rights": "data/rights.json",
            "semantic_model": SEMANTIC_MODEL_BUNDLE_PATH,
            "semantic_yaml_ld": "okf-bundle.yamlld",
            "semantic_json_ld": "okf-bundle.jsonld",
            "semantic_context": SEMANTIC_CONTEXT_BUNDLE_PATH,
            "iri_route_registry": IRI_ROUTE_REGISTRY_BUNDLE_PATH,
            "predicate_registry": PREDICATE_REGISTRY_BUNDLE_PATH,
            "semantic_validation": SEMANTIC_ASSERTION_VALIDATION_BUNDLE_PATH,
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
            "relationship_runtime": explorer_projection[
                "relationship_runtime"
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
            "bundle_authority": "metadata normalisation and this release only",
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


def _required_utc_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z",
        value,
    ):
        raise ValueError(f"{field} must be a whole-second UTC timestamp")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise ValueError(f"{field} is not a valid UTC timestamp") from exc


def _document_chronology_times(
    value: Any,
    *,
    document: str,
    fields: frozenset[str],
    location: str = "$",
) -> list[tuple[str, datetime]]:
    """Return strict governed event times without treating arbitrary prose as dates."""

    found: list[tuple[str, datetime]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child_location = f"{location}.{key}"
            if key in fields:
                found.append(
                    (
                        f"{document} {child_location}",
                        _required_utc_timestamp(
                            item,
                            f"{document} {child_location}",
                        ),
                    )
                )
            found.extend(
                _document_chronology_times(
                    item,
                    document=document,
                    fields=fields,
                    location=child_location,
                )
            )
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(
                _document_chronology_times(
                    item,
                    document=document,
                    fields=fields,
                    location=f"{location}[{index}]",
                )
            )
    return found


def validate_generated_at_chronology(
    config: dict[str, Any],
    snapshot: dict[str, Any],
    cpsv_mappings: dict[str, Any],
) -> dict[str, str]:
    """Prove that deterministic generation follows every governed source event."""

    generated_at = _required_utc_timestamp(
        config.get("generated_at"),
        "source/build-config.json generated_at",
    )
    profile = load_json(ROOT / "domain-profile" / "domain-profile.json")
    if not isinstance(profile, dict):
        raise ValueError("domain profile must be an object for build chronology")
    profile_evidence = profile.get("evidence")
    if not isinstance(profile_evidence, list) or not profile_evidence:
        raise ValueError("domain profile lacks governed evidence for build chronology")

    chronology: list[tuple[str, datetime]] = []
    chronology.extend(
        _document_chronology_times(
            {"observed_at": snapshot.get("observed_at")},
            document="selected snapshot",
            fields=frozenset({"observed_at"}),
        )
    )
    acquisition = snapshot.get("acquisition_snapshot")
    if isinstance(acquisition, dict):
        manifest_relative = acquisition.get("manifest_path")
    else:
        manifest_relative = snapshot.get("manifest_path")
    if not isinstance(manifest_relative, str) or not manifest_relative:
        raise ValueError("selected snapshot lacks a governed manifest path")
    manifest_path = ROOT / manifest_relative
    if (
        manifest_path.is_symlink()
        or not manifest_path.is_file()
        or ROOT not in manifest_path.resolve().parents
    ):
        raise ValueError("selected snapshot manifest is missing or unsafe")
    chronology.extend(
        _document_chronology_times(
            load_json(manifest_path),
            document="snapshot manifest",
            fields=frozenset({"observed_at", "retrieved_at"}),
        )
    )
    chronology.append(
        (
            "domain profile $.prepared_at",
            _required_utc_timestamp(
                profile.get("prepared_at"),
                "domain profile $.prepared_at",
            ),
        )
    )
    chronology.extend(
        _document_chronology_times(
            profile_evidence,
            document="domain-profile evidence",
            fields=frozenset(
                {"access_tested_at", "observed_at", "retrieved_at"}
            ),
        )
    )
    evidence_register_path = (
        ROOT / "domain-profile" / "evidence-register.jsonl"
    )
    if evidence_register_path.is_symlink() or not evidence_register_path.is_file():
        raise ValueError("domain-profile evidence register is missing or unsafe")
    try:
        evidence_register = [
            json.loads(line)
            for line in repository_text(
                evidence_register_path,
                maximum_bytes=MAX_CAUSAL_INPUT_FILE_BYTES,
                field="domain profile evidence register",
            ).splitlines()
            if line.strip()
        ]
    except json.JSONDecodeError as exc:
        raise ValueError("domain-profile evidence register is invalid JSONL") from exc
    if not evidence_register or any(
        not isinstance(row, dict) for row in evidence_register
    ):
        raise ValueError("domain-profile evidence register has no governed rows")
    chronology.extend(
        _document_chronology_times(
            evidence_register,
            document="domain-profile evidence register",
            fields=frozenset(
                {"access_tested_at", "observed_at", "retrieved_at"}
            ),
        )
    )
    cpsv_document = cpsv_mappings.get("document")
    if not isinstance(cpsv_document, dict):
        raise ValueError("CPSV-AP mapping lacks its governed source document")
    chronology.extend(
        _document_chronology_times(
            cpsv_document,
            document="CPSV-AP mapping",
            fields=frozenset({"observed_at", "retrieved_at", "reviewed_at"}),
        )
    )
    if not chronology:
        raise ValueError("build chronology has no governed source events")
    latest_label, latest_time = max(chronology, key=lambda row: row[1])
    if generated_at <= latest_time:
        raise ValueError(
            "source/build-config.json generated_at must be later than every "
            f"governed source event; latest is {latest_label} at "
            f"{latest_time.strftime('%Y-%m-%dT%H:%M:%SZ')}"
        )
    return {
        "generated_at": generated_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "latest_governed_event": latest_label,
        "latest_governed_event_at": latest_time.strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
    }


def load_cpsv_service_mappings(
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Load reviewed, source-bound terminal decisions for every service candidate."""
    payload = load_json(CPSV_SERVICE_MAPPING_PATH)
    candidate_rule = payload.get("candidate_rule", {})
    type_crosswalk_path = ROOT / "source" / "type-kind-crosswalk.json"
    if (
        payload.get("schema") != "okf-hmlr-cpsv-service-mappings.v1"
        or payload.get("version") != BUILD_VERSION
        or payload.get("research_cutoff") != RESEARCH_CUTOFF
        or candidate_rule.get("field") != "kind"
        or candidate_rule.get("value") != "service"
        or candidate_rule.get("type_crosswalk_sha256")
        != sha256_file(type_crosswalk_path)
    ):
        raise ValueError("CPSV-AP service mapping identity is unsupported")

    candidates = {
        clean_text(record["record_id"]): record
        for record in records
        if record.get("kind") == "service"
    }
    if candidate_rule.get("expected_count") != len(candidates):
        raise ValueError("CPSV-AP candidate count differs from the reviewed mapping")

    rights_payload = load_json(ROOT / "governance" / "rights-review.json")
    rights_rows = rights_payload.get("assessments", [])
    valid_rights = {
        row.get("id") for row in rights_rows if isinstance(row, dict) and row.get("id")
    }
    if not valid_rights:
        raise ValueError("CPSV-AP mapping cannot resolve governed rights IDs")

    evidence_rows = payload.get("evidence")
    if not isinstance(evidence_rows, list) or not evidence_rows:
        raise ValueError("CPSV-AP mapping requires source-bound evidence")
    evidence_by_id: dict[str, dict[str, Any]] = {}
    evidence_times: dict[str, tuple[datetime, datetime]] = {}
    evidence_locators: dict[str, tuple[str, str | None]] = {}
    allowed_claims = {
        "public-service-classification",
        "competent-authority-delivery",
        "public-organisation-identity",
        "exclusion",
    }
    required_evidence_fields = {
        "claim_supported",
        "id",
        "issuer",
        "locator",
        "normalization",
        "observed_at",
        "rationale",
        "resource",
        "retrieved_at",
        "review_status",
        "rights_ref",
        "rule_version",
        "source_artifact",
        "source_field",
        "source_sha256",
        "source_value_hash_canonicalization",
        "source_value_sha256",
        "url",
    }
    for evidence in evidence_rows:
        if not isinstance(evidence, dict):
            raise ValueError("CPSV-AP mapping evidence must be an object")
        missing = sorted(required_evidence_fields - set(evidence))
        if missing:
            raise ValueError(
                "CPSV-AP mapping evidence lacks required fields: "
                + ", ".join(missing)
            )
        evidence_id = clean_text(evidence.get("id"))
        if (
            not evidence_id
            or evidence.get("id") != evidence_id
            or evidence_id in evidence_by_id
        ):
            raise ValueError("CPSV-AP mapping evidence IDs must be unique and canonical")
        claim = evidence.get("claim_supported")
        if claim not in allowed_claims:
            raise ValueError(f"unsupported CPSV-AP evidence claim: {evidence_id}")
        if evidence.get("review_status") != "reviewed":
            raise ValueError(f"CPSV-AP evidence is not reviewed: {evidence_id}")
        if evidence.get("rule_version") != "1":
            raise ValueError(f"CPSV-AP evidence rule version differs: {evidence_id}")
        if evidence.get("normalization") != CPSV_MAPPING_RULE_IRI:
            raise ValueError(f"CPSV-AP evidence rule differs: {evidence_id}")
        if (
            evidence.get("source_value_hash_canonicalization")
            != CPSV_SOURCE_VALUE_CANONICALIZATION
        ):
            raise ValueError(
                f"CPSV-AP evidence canonicalisation differs: {evidence_id}"
            )
        if evidence.get("rights_ref") not in valid_rights:
            raise ValueError(f"CPSV-AP evidence rights are unknown: {evidence_id}")
        if not clean_text(evidence.get("rationale")):
            raise ValueError(f"CPSV-AP evidence lacks a rationale: {evidence_id}")
        for digest_field in ("source_sha256", "source_value_sha256"):
            if not re.fullmatch(r"[0-9a-f]{64}", str(evidence.get(digest_field))):
                raise ValueError(
                    f"CPSV-AP evidence has an invalid {digest_field}: {evidence_id}"
                )
        for url_field in ("issuer", "resource", "url"):
            raw_url = evidence.get(url_field)
            if semantic_web_iri(raw_url) != raw_url:
                raise ValueError(
                    f"CPSV-AP evidence {url_field} is not canonical: {evidence_id}"
                )
        observed_time = _required_utc_timestamp(
            evidence.get("observed_at"),
            f"CPSV-AP evidence observed_at ({evidence_id})",
        )
        retrieved_time = _required_utc_timestamp(
            evidence.get("retrieved_at"),
            f"CPSV-AP evidence retrieved_at ({evidence_id})",
        )
        if retrieved_time < observed_time:
            raise ValueError(f"CPSV-AP evidence predates observation: {evidence_id}")

        relative = evidence.get("source_artifact")
        if not isinstance(relative, str) or relative != clean_text(relative):
            raise ValueError(f"CPSV-AP evidence path is not canonical: {evidence_id}")
        source_path = (ROOT / relative).resolve()
        if ROOT not in source_path.parents or not source_path.is_file():
            raise ValueError(f"CPSV-AP evidence path is unsafe: {relative}")
        if source_path.is_symlink() or sha256_file(source_path) != evidence.get(
            "source_sha256"
        ):
            raise ValueError(f"CPSV-AP evidence artefact differs: {evidence_id}")

        source_field = evidence.get("source_field")
        if (
            not isinstance(source_field, str)
            or source_field != clean_text(source_field)
            or evidence.get("locator") != source_field
        ):
            raise ValueError(f"CPSV-AP evidence locator differs: {evidence_id}")
        curated_match = re.fullmatch(r"records\[id=(.+)\]", source_field)
        search_match = re.fullmatch(r"results\[link=(/.+)\]", source_field)
        register_match = re.fullmatch(r"line\[id=([A-Z0-9-]+)\]", source_field)
        source_document: dict[str, Any] = {}
        if curated_match or search_match:
            loaded = load_json(source_path)
            if not isinstance(loaded, dict):
                raise ValueError(f"CPSV-AP evidence source is not an object: {evidence_id}")
            source_document = loaded
        if curated_match:
            rows = source_document.get("records", [])
            matches = [
                row
                for row in rows
                if clean_text(row.get("id")) == curated_match.group(1)
            ]
            locator_kind = "curated"
            locator_value: str | None = curated_match.group(1)
        elif search_match:
            rows = source_document.get("results", [])
            matches = [
                row
                for row in rows
                if clean_text(row.get("link")) == search_match.group(1)
            ]
            locator_kind = "govuk-search"
            locator_value = search_match.group(1)
        elif register_match:
            parsed_lines = [
                json.loads(line)
                for line in repository_text(
                    source_path,
                    maximum_bytes=MAX_CAUSAL_INPUT_FILE_BYTES,
                    field=f"source register {source_path}",
                ).splitlines()
            ]
            matches = [
                row
                for row in parsed_lines
                if clean_text(row.get("id")) == register_match.group(1)
            ]
            locator_kind = "evidence-register"
            locator_value = register_match.group(1)
        else:
            raise ValueError(
                f"CPSV-AP evidence has an unsupported source locator: {source_field}"
            )
        if len(matches) != 1:
            raise ValueError(f"CPSV-AP evidence locator is not unique: {evidence_id}")
        value = matches[0]
        if sha256_bytes(compact_canonical_json(value)) != evidence.get(
            "source_value_sha256"
        ):
            raise ValueError(f"CPSV-AP evidence value differs: {evidence_id}")
        source_observed_at = value.get("observed_at") or source_document.get(
            "observed_at"
        )
        if source_observed_at and evidence.get("observed_at") != source_observed_at:
            raise ValueError(
                f"CPSV-AP evidence observation differs from its source: {evidence_id}"
            )
        if value.get("rights_ref") and evidence.get("rights_ref") != value.get(
            "rights_ref"
        ):
            raise ValueError(
                f"CPSV-AP evidence rights differ from its source: {evidence_id}"
            )
        source_url = value.get("location") or value.get("url")
        if search_match:
            source_url = urljoin("https://www.gov.uk/", search_match.group(1))
        if source_url:
            governed_source_url = ensure_https(source_url)
            if evidence.get("url") != governed_source_url or evidence.get(
                "resource"
            ) != governed_source_url:
                raise ValueError(
                    f"CPSV-AP evidence URL differs from its source: {evidence_id}"
                )

        record_id = evidence.get("record_id")
        if claim == "public-organisation-identity":
            if record_id is not None:
                raise ValueError(
                    f"CPSV-AP organisation evidence must not bind a record: {evidence_id}"
                )
        elif not isinstance(record_id, str) or record_id not in candidates:
            raise ValueError(
                f"CPSV-AP evidence does not bind a service candidate: {evidence_id}"
            )
        evidence_by_id[evidence_id] = evidence
        evidence_times[evidence_id] = (observed_time, retrieved_time)
        evidence_locators[evidence_id] = (locator_kind, locator_value)

    def evidence_refs(container: Any, field: str) -> list[str]:
        if not isinstance(container, dict):
            raise ValueError(f"{field} must be an object")
        refs = container.get("evidence_refs")
        if not isinstance(refs, list) or not refs:
            raise ValueError(f"{field} requires evidence references")
        if any(
            not isinstance(ref, str) or not ref or ref != clean_text(ref)
            for ref in refs
        ):
            raise ValueError(f"{field} has a non-canonical evidence reference")
        if len(refs) != len(set(refs)):
            raise ValueError(f"{field} repeats an evidence reference")
        unknown = sorted(set(refs) - set(evidence_by_id))
        if unknown:
            raise ValueError(f"{field} has unknown evidence: {', '.join(unknown)}")
        return refs

    def bind_record_evidence(
        refs: list[str],
        record: dict[str, Any],
        claim: str,
        field: str,
    ) -> None:
        record_id = record["record_id"]
        for ref in refs:
            evidence = evidence_by_id[ref]
            if evidence["claim_supported"] != claim:
                raise ValueError(f"{field} has the wrong evidence claim: {ref}")
            if evidence.get("record_id") != record_id:
                raise ValueError(f"{field} does not bind decision record: {ref}")
            applicable_rights = {
                clean_text(record.get("rights_ref")),
                *{
                    clean_text(rights_ref)
                    for rights_ref in record.get("additional_rights_refs", [])
                },
            }
            if evidence.get("rights_ref") not in applicable_rights:
                raise ValueError(f"{field} rights differ from the record: {ref}")
            if evidence["issuer"] != record["publisher_id"]:
                raise ValueError(f"{field} issuer differs from the record: {ref}")
            if evidence["observed_at"] != record["observed_at"]:
                raise ValueError(f"{field} observation differs from the record: {ref}")
            if (
                evidence["resource"] != record["canonical_source_url"]
                or evidence["url"] != record["canonical_source_url"]
            ):
                raise ValueError(f"{field} URL differs from the record: {ref}")
            locator_kind, locator_value = evidence_locators[ref]
            if locator_kind == "curated" and locator_value != record["source_native_id"]:
                raise ValueError(f"{field} source row differs from the record: {ref}")
            if locator_kind == "govuk-search":
                expected_path = urlparse(record["canonical_source_url"]).path
                if locator_value != expected_path:
                    raise ValueError(f"{field} source row differs from the record: {ref}")

    decisions = payload.get("decisions")
    if not isinstance(decisions, list) or len(decisions) != len(candidates):
        raise ValueError("CPSV-AP mapping needs one terminal decision per candidate")
    decisions_by_record: dict[str, dict[str, Any]] = {}
    decision_ids: set[str] = set()
    referenced_evidence: set[str] = set()
    allowed_decisions = {
        "mapped",
        "not-a-public-service",
        "insufficient-classification-evidence",
        "insufficient-competent-authority-evidence",
    }
    binding_fields = (
        "record_id",
        "source_native_id",
        "source_native_type",
        "title",
        "canonical_source_url",
        "publisher_id",
        "source_family",
        "observed_at",
    )
    for decision in decisions:
        if not isinstance(decision, dict):
            raise ValueError("CPSV-AP terminal decisions must be objects")
        decision_id = clean_text(decision.get("id"))
        if (
            not decision_id
            or decision.get("id") != decision_id
            or decision_id in decision_ids
        ):
            raise ValueError("CPSV-AP decision IDs must be unique and canonical")
        decision_ids.add(decision_id)
        record_id = clean_text(decision.get("record_id"))
        record = candidates.get(record_id)
        if (
            decision.get("record_id") != record_id
            or record is None
            or record_id in decisions_by_record
        ):
            raise ValueError("CPSV-AP decisions must cover unique service records")
        outcome = decision.get("decision")
        if outcome not in allowed_decisions:
            raise ValueError(f"unsupported CPSV-AP terminal decision: {outcome}")
        binding = decision.get("record_binding", {})
        expected_binding = {field: record.get(field) for field in binding_fields}
        if binding.get("fields") != expected_binding or binding.get(
            "value_sha256"
        ) != sha256_bytes(compact_canonical_json(expected_binding)):
            raise ValueError(f"CPSV-AP record binding differs: {record_id}")
        if decision.get("review_status") != "reviewed" or not clean_text(
            decision.get("reviewed_by_role")
        ):
            raise ValueError(f"CPSV-AP decision is not reviewed: {record_id}")
        reviewed_time = _required_utc_timestamp(
            decision.get("reviewed_at"),
            f"CPSV-AP decision reviewed_at ({decision_id})",
        )
        observed_time = _required_utc_timestamp(
            record.get("observed_at"),
            f"CPSV-AP record observed_at ({record_id})",
        )
        if reviewed_time < observed_time:
            raise ValueError(f"CPSV-AP decision predates its record: {record_id}")

        used_refs: list[str]
        if outcome == "mapped":
            classification = decision.get("service_classification", {})
            authority = decision.get("competent_authority", {})
            organisation_id = authority.get("organisation_id")
            if (
                classification.get("class_iri")
                != "http://purl.org/vocab/cpsv#PublicService"
                or not clean_text(classification.get("rationale"))
                or authority.get("predicate_iri")
                != COMPETENT_AUTHORITY_PREDICATE
                or organisation_id != HMLR_PUBLISHER_IRI
                or organisation_id != record["publisher_id"]
                or authority.get("responsibility_scope") != "delivery"
                or not clean_text(authority.get("rationale"))
            ):
                raise ValueError(f"CPSV-AP mapped decision is incomplete: {record_id}")
            class_refs = evidence_refs(
                classification,
                f"CPSV-AP service classification ({record_id})",
            )
            authority_refs = evidence_refs(
                authority,
                f"CPSV-AP competent authority ({record_id})",
            )
            bind_record_evidence(
                class_refs,
                record,
                "public-service-classification",
                f"CPSV-AP classification evidence ({record_id})",
            )
            delivery_refs = [
                ref
                for ref in authority_refs
                if evidence_by_id[ref]["claim_supported"]
                == "competent-authority-delivery"
            ]
            organisation_refs = [
                ref
                for ref in authority_refs
                if evidence_by_id[ref]["claim_supported"]
                == "public-organisation-identity"
            ]
            if (
                not delivery_refs
                or not organisation_refs
                or len(delivery_refs) + len(organisation_refs) != len(authority_refs)
            ):
                raise ValueError(
                    "CPSV-AP authority needs separate delivery and public-organisation "
                    f"evidence: {record_id}"
                )
            bind_record_evidence(
                delivery_refs,
                record,
                "competent-authority-delivery",
                f"CPSV-AP delivery evidence ({record_id})",
            )
            for ref in organisation_refs:
                evidence = evidence_by_id[ref]
                if (
                    evidence.get("record_id") is not None
                    or evidence["issuer"] != organisation_id
                    or evidence["resource"] != organisation_id
                    or evidence["url"] != organisation_id
                ):
                    raise ValueError(
                        "CPSV-AP organisation evidence differs from the declared "
                        f"authority: {ref}"
                    )
            used_refs = class_refs + authority_refs
        else:
            if not clean_text(decision.get("rationale")):
                raise ValueError(f"CPSV-AP exclusion lacks a rationale: {record_id}")
            exclusion_refs = evidence_refs(
                decision,
                f"CPSV-AP exclusion ({record_id})",
            )
            bind_record_evidence(
                exclusion_refs,
                record,
                "exclusion",
                f"CPSV-AP exclusion evidence ({record_id})",
            )
            used_refs = exclusion_refs
        if any(reviewed_time < evidence_times[ref][1] for ref in used_refs):
            raise ValueError(f"CPSV-AP decision predates its evidence: {record_id}")
        referenced_evidence.update(used_refs)
        decisions_by_record[record_id] = decision

    if set(decisions_by_record) != set(candidates):
        raise ValueError("CPSV-AP decisions and current candidates differ")
    unreferenced = sorted(set(evidence_by_id) - referenced_evidence)
    if unreferenced:
        raise ValueError(
            "CPSV-AP mapping contains unreferenced evidence: " + ", ".join(unreferenced)
        )
    mapped = {
        record_id: decision
        for record_id, decision in decisions_by_record.items()
        if decision["decision"] == "mapped"
    }
    receipt = {
        "schema": "okf-hmlr-cpsv-service-mapping-validation.v1",
        "status": "conformant",
        "path": CPSV_SERVICE_MAPPING_PATH.relative_to(ROOT).as_posix(),
        "sha256": sha256_file(CPSV_SERVICE_MAPPING_PATH),
        "candidate_count": len(candidates),
        "mapped_count": len(mapped),
        "excluded_count": len(candidates) - len(mapped),
        "evidence_count": len(evidence_by_id),
        "type_crosswalk_sha256": sha256_file(type_crosswalk_path),
    }
    return {
        "document": payload,
        "decisions_by_record": decisions_by_record,
        "mapped": mapped,
        "evidence_by_id": evidence_by_id,
        "receipt": receipt,
    }


def is_cpsv_public_service(
    record: dict[str, Any], mappings: dict[str, Any]
) -> bool:
    """Return only reviewed CPSV-AP mappings; source types never decide alone."""
    return clean_text(record.get("record_id")) in mappings["mapped"]


def semantic_record_iri(
    publication_base: str, record: dict[str, Any]
) -> str:
    """Mint a stable entity IRI without conflating a web page with its subject."""
    return urljoin(
        publication_base.rstrip("/") + "/",
        "id/entity/" + clean_text(record["record_id"]),
    )


def semantic_source_resource_iri(
    publication_base: str,
    record: dict[str, Any],
    source_url: str,
) -> str:
    """Mint a local, route-bearing identity for one record source representation."""
    identity = (
        clean_text(record["record_id"])
        + "\0"
        + semantic_web_iri(source_url)
    )
    return urljoin(
        publication_base.rstrip("/") + "/",
        "id/source-resource/"
        + clean_text(record["record_id"])
        + "-"
        + sha256_bytes(identity.encode("utf-8"))[:20],
    )


def semantic_jurisdiction_iri(publication_base: str, jurisdiction: str) -> str:
    """Mint the stable local jurisdiction identity used by CPSV projections."""
    return urljoin(
        publication_base.rstrip("/") + "/",
        "id/jurisdiction/"
        + hashlib.sha256(jurisdiction.encode("utf-8")).hexdigest()[:24],
    )


def semantic_route(kind: str, identity: str) -> str:
    """Return a deterministic, type-distinct Explorer focus route."""
    return kind + "/" + explorer_name(kind, identity)


def jsonld_projection(
    publication_base: str,
    snapshot: dict[str, Any],
    records: list[dict[str, Any]],
    relationship_assertions: list[dict[str, Any]],
    cpsv_mappings: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    publication_base = publication_base.rstrip("/") + "/"
    bundle_id = urljoin(publication_base, "id/bundle/hmlr-public-estate")
    catalog_id = urljoin(publication_base, "id/catalogue/hmlr-public-estate")
    record_nodes: dict[str, dict[str, Any]] = {}
    publisher_nodes: dict[str, dict[str, Any]] = {}
    rights_nodes: dict[str, dict[str, Any]] = {}
    activity_nodes: dict[str, dict[str, Any]] = {}
    language_nodes: dict[str, dict[str, Any]] = {}
    jurisdiction_nodes: dict[str, dict[str, Any]] = {}
    source_nodes: dict[str, dict[str, Any]] = {}
    entity_nodes: dict[str, dict[str, Any]] = {}
    entity_types = {
        "dataset": ["dcat:Dataset", "schema:Dataset"],
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
    language_registry = {
        "cy": (
            "http://publications.europa.eu/resource/authority/language/CYM",
            "Welsh",
        ),
        "en": (
            "http://publications.europa.eu/resource/authority/language/ENG",
            "English",
        ),
    }
    hmlr_jurisdiction_id = urljoin(
        publication_base, "id/jurisdiction/england-and-wales"
    )
    jurisdiction_nodes[hmlr_jurisdiction_id] = {
        "@id": hmlr_jurisdiction_id,
        "@type": [
            "dcterms:Location",
            "http://data.europa.eu/88u/dataset/atu-type",
        ],
        "route": semantic_route("jurisdiction", hmlr_jurisdiction_id),
        "schema:name": "England and Wales",
        "skos:prefLabel": {"@value": "England and Wales", "@language": "en-GB"},
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
        publisher_types = ["schema:Organization"]
        publisher_node: dict[str, Any] = {
            "@id": publisher_id,
            "@type": publisher_types,
            "route": semantic_route("publisher", publisher_id),
            "schema:name": record["publisher"],
            "schema:url": publisher_id,
        }
        if publisher_id == HMLR_PUBLISHER_IRI:
            publisher_types.append("cv:PublicOrganisation")
            publisher_node.update(
                {
                    "skos:prefLabel": {
                        "@value": record["publisher"],
                        "@language": "en-GB",
                    },
                }
            )
        publisher_nodes[publisher_id] = publisher_node
        rights_nodes[rights_id] = {
            "@id": rights_id,
            "@type": "dcterms:RightsStatement",
            "route": semantic_route("rights", rights_id),
            "dcterms:identifier": record["rights_ref"],
            "schema:name": record["rights_state"],
        }
        activity_nodes[activity_id] = {
            "@id": activity_id,
            "@type": "prov:Activity",
            "route": semantic_route("activity", activity_id),
            "dcterms:identifier": record["source_family"],
            "prov:endedAtTime": record["observed_at"],
        }
        record_nodes[record_node_id] = {
            "@id": record_node_id,
            "@type": "dcat:CatalogRecord",
            "route": semantic_route("catalogue-record", record_node_id),
            "dcterms:identifier": record["record_id"],
        }
        if record["kind"] == "service":
            if is_cpsv_public_service(record, cpsv_mappings):
                record_entity_types = ["cpsv:PublicService", "schema:Service"]
            elif record["source_native_type"] in {
                "licensed-data-service",
                "linked-data-service",
            }:
                record_entity_types = ["dcat:DataService", "schema:Service"]
            else:
                record_entity_types = ["schema:Service"]
        else:
            record_entity_types = entity_types[record["kind"]]

        language_refs: list[dict[str, str]] = []
        for language in record["languages"]:
            language_id, language_label = language_registry[language]
            language_nodes[language_id] = {
                "@id": language_id,
                "@type": "dcterms:LinguisticSystem",
                "route": semantic_route("language", language_id),
                "schema:name": language_label,
            }

        entity_id = semantic_record_iri(publication_base, record)
        entity: dict[str, Any] = {
            "@id": entity_id,
            "@type": record_entity_types,
            "route": "dataset/" + explorer_name("record", record["record_id"]),
            "schema:name": record["title"],
            "schema:description": record["description"],
            "schema:url": semantic_web_iri(record["canonical_source_url"]),
        }
        if not is_cpsv_public_service(record, cpsv_mappings):
            entity["dcterms:type"] = record["source_native_type"]
        if record["publisher_last_updated"]:
            entity["dcterms:modified"] = record["publisher_last_updated"]
        if record["licence"] is not None:
            entity["dcterms:license"] = record["licence"]
        if is_cpsv_public_service(record, cpsv_mappings):
            entity.update(
                {
                    "dcterms:identifier": record["record_id"],
                    "dcterms:title": {
                        "@value": record["title"],
                        "@language": "en-GB",
                    },
                    "dcterms:description": {
                        "@value": record["description"],
                        "@language": "en-GB",
                    },
                    "dcat:keyword": [
                        {"@value": topic, "@language": "en-GB"}
                        for topic in record["topics"]
                    ],
                }
            )
            jurisdiction = clean_text(record.get("jurisdiction"))
            if jurisdiction:
                jurisdiction_id = semantic_jurisdiction_iri(
                    publication_base, jurisdiction
                )
                jurisdiction_nodes[jurisdiction_id] = {
                    "@id": jurisdiction_id,
                    "@type": "dcterms:Location",
                    "route": semantic_route("jurisdiction", jurisdiction_id),
                    "schema:name": jurisdiction,
                    "skos:prefLabel": {
                        "@value": jurisdiction,
                        "@language": "en-GB",
                    },
                }
        entity_nodes[entity_id] = entity
        for source_url in record["source_urls"]:
            governed_url = semantic_web_iri(source_url)
            source_id = semantic_source_resource_iri(
                publication_base, record, governed_url
            )
            source_nodes[source_id] = {
                "@id": source_id,
                "@type": ["prov:Entity", "schema:CreativeWork"],
                "route": semantic_route("source", source_id),
                "schema:name": "Source representation for " + record["title"],
                "schema:url": governed_url,
                "dcterms:identifier": governed_url,
            }

    catalog_node = {
        "@id": catalog_id,
        "@type": ["dcat:Catalog", "schema:DataCatalog"],
        "route": semantic_route("catalogue", catalog_id),
        "schema:name": "HM Land Registry public-estate metadata catalogue",
        "schema:description": (
            "Independent metadata-only projection; source authority remains external."
        ),
        "dcterms:modified": config["generated_at"],
        "dcterms:temporal": snapshot["observed_at"],
    }
    all_nodes: dict[str, dict[str, Any]] = {catalog_id: catalog_node}
    for group in (
        record_nodes,
        entity_nodes,
        publisher_nodes,
        rights_nodes,
        activity_nodes,
        language_nodes,
        jurisdiction_nodes,
        source_nodes,
    ):
        overlap = set(all_nodes) & set(group)
        if overlap:
            raise ValueError(
                "semantic entity identity is reused across node classes: "
                + ", ".join(sorted(overlap))
            )
        all_nodes.update(group)

    routes: dict[str, str] = {}
    for identity, node in all_nodes.items():
        route = clean_text(node.get("route"))
        if not route or route in routes:
            raise ValueError(
                "semantic endpoint routes must be present and identity-distinct: "
                f"{identity} / {route}"
            )
        routes[route] = identity

    assertion_nodes: list[dict[str, Any]] = []
    for assertion in relationship_assertions:
        source_iri = assertion["source"]["@id"]
        target_iri = assertion["target"]["@id"]
        predicate_iri = assertion["predicate"]["@id"]
        source_node = all_nodes.get(source_iri)
        target_node = all_nodes.get(target_iri)
        if source_node is None or target_node is None:
            raise ValueError(
                "semantic relationship endpoint lacks an entity node: "
                f"{source_iri} -> {target_iri}"
            )
        if (
            clean_text(source_node.get("route")) != assertion["source_route"]
            or clean_text(target_node.get("route")) != assertion["target_route"]
        ):
            raise ValueError(
                "semantic relationship route differs from its endpoint node: "
                f"{source_iri} -> {target_iri}"
            )
        direct_target = {"@id": target_iri}
        existing = source_node.get(predicate_iri)
        if existing is None:
            source_node[predicate_iri] = direct_target
        elif isinstance(existing, list):
            if direct_target in existing:
                raise ValueError("duplicate direct semantic relationship triple")
            existing.append(direct_target)
        else:
            if existing == direct_target:
                raise ValueError("duplicate direct semantic relationship triple")
            source_node[predicate_iri] = [existing, direct_target]
        assertion_nodes.append(
            {
                **assertion,
                "rdf:subject": assertion["source"],
                "rdf:predicate": assertion["predicate"],
                "rdf:object": assertion["target"],
            }
        )
    graph = [all_nodes[key] for key in sorted(all_nodes)]
    graph.extend(assertion_nodes)
    return {
        "@context": urljoin(publication_base, "context.jsonld"),
        "@id": bundle_id,
        "@type": "okf:Bundle",
        "title": "HM Land Registry public-estate OKF",
        "description": (
            "Independent metadata-only semantic discovery bundle; source "
            "authority remains external."
        ),
        "version": config["version"],
        "status": "preview",
        "descriptor": {"@id": urljoin(publication_base, "okf-explorer.json")},
        "semanticDescriptor": {
            "@id": urljoin(publication_base, SEMANTIC_MODEL_BUNDLE_PATH)
        },
        "home": {"@id": publication_base},
        "profile": {"@id": BUNDLE_PROFILE_URL},
        "publisher": {"@id": "https://github.com/chris-page-gov"},
        "license": {
            "@id": (
                "https://github.com/chris-page-gov/okf-LandRegistry/"
                "blob/main/LICENSE.md"
            )
        },
        "okf_version": "0.2",
        "schema:name": "HM Land Registry public-estate OKF",
        "schema:description": (
            "Independent metadata-only projection; source authority remains external."
        ),
        "dcterms:modified": config["generated_at"],
        "dcterms:temporal": snapshot["observed_at"],
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
    # Candidate bytes cannot claim publication; release authority is external.
    concept_status = "draft"
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

This bundle candidate is a bounded metadata discovery snapshot. HM Land Registry and
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
            "How official public metadata is observed, normalised and traced.",
            "https://www.gov.uk/government/organisations/land-registry",
            config["generated_at"],
            concept_status,
            f"""# Sources and provenance

The build used snapshot `{snapshot["snapshot_id"]}`, observed
`{snapshot["observed_at"]}`. Network acquisition is separate from this offline
build. Every record retains a canonical URL, source family, authority tier,
observation time and source URLs. Normalisation does not transfer authority.
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
        "- Normalised only public discovery metadata; no authenticated, paid, "
        "personal or bulk source records were acquired.\n"
        f"- Generated `{config['status']}` provenance, rights, reconciliation, "
        "one Explorer runtime search plane and a static Pages catalogue offline.\n",
        encoding="utf-8",
    )
    concept_dir = output / "concepts"
    concept_dir.mkdir(parents=True, exist_ok=True)
    for name, body in concepts.items():
        (concept_dir / name).write_text(body, encoding="utf-8")


def copy_pages(output: Path, causal_input_paths: set[str]) -> None:
    """Copy only declared causal page inputs and reject worktree additions."""

    pages = ROOT / "pages"
    if pages.is_symlink() or not pages.is_dir():
        raise ValueError("pages/ authored site is missing")
    declared = {
        path.removeprefix("pages/")
        for path in causal_input_paths
        if path.startswith("pages/")
    }
    non_causal_legacy = {"app.js"}
    actual: set[str] = set()
    for directory, directory_names, file_names in os.walk(
        pages,
        topdown=True,
        followlinks=False,
    ):
        directory_path = Path(directory)
        for name in sorted(directory_names):
            path = directory_path / name
            if path.is_symlink():
                raise ValueError(
                    "pages/ authored site contains a symbolic-link directory: "
                    f"{path.relative_to(pages).as_posix()}"
                )
        for name in sorted(file_names):
            path = directory_path / name
            relative = path.relative_to(pages).as_posix()
            if path.is_symlink() or not path.is_file():
                raise ValueError(
                    "pages/ authored site contains an unsafe non-regular file: "
                    f"{relative}"
                )
            actual.add(relative)
            if len(actual) > MAX_CAUSAL_INPUT_FILES:
                raise ValueError("pages/ authored site exceeds its file-count ceiling")
    missing = declared - actual
    unexpected = actual - declared - non_causal_legacy
    if missing or unexpected:
        raise ValueError(
            "pages/ worktree inventory differs from causal build inputs: "
            f"missing={sorted(missing)!r}, "
            f"unexpected={sorted(unexpected)!r}"
        )
    for relative in sorted(declared):
        if relative == "search-contract.json":
            continue
        path = pages / PurePosixPath(relative)
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        copy_repository_input(path, destination)


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


def relationship_labels(predicate_iri: str) -> tuple[str, str]:
    """Return the governed preferred and inverse labels for one predicate."""
    labels = {
        CATALOGUE_RECORD_PREDICATE: ("has catalogue record", "is record in catalogue"),
        CATALOGUE_RESOURCE_PREDICATE: ("has resource", "is resource in catalogue"),
        CATALOGUE_DATASET_PREDICATE: ("has dataset", "is dataset in catalogue"),
        PRIMARY_TOPIC_PREDICATE: ("has primary topic", "is primary topic of"),
        SOURCE_PREDICATE: ("has source", "is source of"),
        DERIVED_FROM_PREDICATE: ("was derived from", "was source for"),
        RIGHTS_PREDICATE: ("has rights statement", "governs rights of"),
        GENERATED_BY_PREDICATE: ("was generated by", "generated"),
        LANGUAGE_PREDICATE: ("has language", "is language of"),
        PUBLISHER_PREDICATE: ("published by", "publishes"),
        TRANSLATION_PREDICATE: ("translation of", "has translation"),
        COMPETENT_AUTHORITY_PREDICATE: (
            "has competent authority",
            "is competent authority for",
        ),
        SPATIAL_PREDICATE: ("has spatial coverage", "is spatial coverage of"),
    }
    try:
        return labels[predicate_iri]
    except KeyError as error:
        raise ValueError(
            f"relationship predicate is not governed: {predicate_iri}"
        ) from error


def relationship_assertion_id(
    publication_base: str,
    source_iri: str,
    predicate_iri: str,
    target_iri: str,
) -> str:
    """Mint the assertion identity from the complete directed triple."""
    assertion_hash = sha256_bytes(
        "\0".join((source_iri, predicate_iri, target_iri)).encode("utf-8")
    )[:24]
    return urljoin(
        publication_base.rstrip("/") + "/",
        "id/assertion/" + assertion_hash,
    )


def relationship_evidence_id(
    publication_base: str,
    evidence: dict[str, Any],
    *,
    source_iri: str,
    predicate_iri: str,
    target_iri: str,
) -> str:
    """Bind schema-safe evidence bytes to the complete directed triple."""
    identity_input = {
        "source": source_iri,
        "predicate": predicate_iri,
        "target": target_iri,
        "evidence": {
            key: value for key, value in evidence.items() if key != "@id"
        },
    }
    evidence_hash = sha256_bytes(compact_canonical_json(identity_input))[:32]
    return urljoin(
        publication_base.rstrip("/") + "/",
        "id/evidence/" + evidence_hash,
    )


def bind_relationship_evidence(
    publication_base: str,
    evidence: dict[str, Any],
    *,
    source_iri: str,
    predicate_iri: str,
    target_iri: str,
    role: str,
    record_id: str | None = None,
) -> dict[str, Any]:
    """Bind schema-safe evidence to a triple without emitting private fields."""
    if not clean_text(role):
        raise ValueError("relationship evidence role is absent")
    if role != "publisher-jurisdiction" and not clean_text(record_id):
        raise ValueError("relationship evidence governed record is absent")
    bound = copy.deepcopy(evidence)
    bound["@id"] = relationship_evidence_id(
        publication_base,
        bound,
        source_iri=source_iri,
        predicate_iri=predicate_iri,
        target_iri=target_iri,
    )
    return bound


def relationship_publication_base(assertion_id: str) -> str:
    """Recover the publication base from one governed assertion identity."""
    marker = "id/assertion/"
    prefix, separator, suffix = clean_text(assertion_id).rpartition(marker)
    if not separator or not prefix or not re.fullmatch(r"[0-9a-f]{24}", suffix):
        raise ValueError(
            f"relationship assertion ID is not governed: {assertion_id}"
        )
    return prefix


def _source_field_segments(source_field: str) -> list[str]:
    """Split the supported locator grammar without splitting selector values."""
    rendered = clean_text(source_field)
    if not rendered or rendered != source_field:
        raise ValueError("source field locator is empty or non-canonical")
    segments: list[str] = []
    start = 0
    depth = 0
    for index, character in enumerate(rendered):
        if character == "[":
            depth += 1
            if depth != 1:
                raise ValueError(f"source field locator is nested: {rendered}")
        elif character == "]":
            depth -= 1
            if depth < 0:
                raise ValueError(f"source field locator is malformed: {rendered}")
        elif character == "." and depth == 0:
            segments.append(rendered[start:index])
            start = index + 1
    if depth != 0:
        raise ValueError(f"source field locator is malformed: {rendered}")
    segments.append(rendered[start:])
    if any(not segment for segment in segments):
        raise ValueError(f"source field locator has an empty segment: {rendered}")
    return segments


def _nested_source_value(value: Any, key: str) -> Any:
    selected = value
    for part in key.split("."):
        if not isinstance(selected, dict) or part not in selected:
            raise ValueError(f"source field selector key is absent: {key}")
        selected = selected[part]
    return selected


def _safe_source_artifact_path(source_artifact: str) -> Path:
    relative = Path(clean_text(source_artifact))
    if (
        not str(relative)
        or relative.is_absolute()
        or ".." in relative.parts
        or relative.as_posix() != clean_text(source_artifact)
    ):
        raise ValueError(f"source artefact path is unsafe: {source_artifact!r}")
    path = ROOT / relative
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"source artefact is missing or unsafe: {source_artifact}")
    return path


@functools.lru_cache(maxsize=32)
def _load_source_artifact_snapshot(
    source_artifact: str,
    device: int,
    inode: int,
    byte_count: int,
    modified_ns: int,
    changed_ns: int,
) -> tuple[str, Any]:
    """Read immutable source bytes once per observed filesystem identity."""
    path = _safe_source_artifact_path(source_artifact)
    before = path.stat()
    expected_identity = (
        device,
        inode,
        byte_count,
        modified_ns,
        changed_ns,
    )
    observed_identity = (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    if observed_identity != expected_identity:
        raise ValueError(f"source artefact changed before reading: {source_artifact}")
    raw = repository_bytes(
        path,
        maximum_bytes=MAX_CAUSAL_INPUT_FILE_BYTES,
        field=f"source artefact {source_artifact}",
    )
    after = path.stat()
    final_identity = (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if len(raw) != byte_count or final_identity != expected_identity:
        raise ValueError(f"source artefact changed while reading: {source_artifact}")
    if path.suffix == ".jsonl":
        rows: list[Any] = []
        for line_number, line in enumerate(
            raw.decode("utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise ValueError(
                    f"source artefact has invalid JSON on line {line_number}: "
                    f"{source_artifact}"
                ) from error
        value: Any = {"line": rows}
    else:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"source artefact has invalid JSON: {source_artifact}"
            ) from error
    return sha256_bytes(raw), value


def source_artifact_snapshot(source_artifact: str) -> tuple[str, Any]:
    path = _safe_source_artifact_path(source_artifact)
    stat = path.stat()
    return _load_source_artifact_snapshot(
        clean_text(source_artifact),
        stat.st_dev,
        stat.st_ino,
        stat.st_size,
        stat.st_mtime_ns,
        stat.st_ctime_ns,
    )


def resolve_source_field_value(
    source_artifact: str,
    source_field: str,
) -> Any:
    """Resolve one strict, unique field locator against a frozen local source."""
    _digest, value = source_artifact_snapshot(source_artifact)

    segment_pattern = re.compile(
        r"(?P<key>[A-Za-z_][A-Za-z0-9_-]*)"
        r"(?:\[(?P<selector>[^\[\]]+)\])?"
    )
    for segment in _source_field_segments(source_field):
        match = segment_pattern.fullmatch(segment)
        if match is None:
            raise ValueError(f"source field locator is unsupported: {source_field}")
        key = match.group("key")
        if not isinstance(value, dict) or key not in value:
            raise ValueError(f"source field path is absent: {source_field}")
        value = value[key]
        selector = match.group("selector")
        if selector is None:
            continue
        if not isinstance(value, list):
            raise ValueError(f"source field selector does not address an array: {source_field}")
        if selector.isdigit():
            ordinal = int(selector)
            if ordinal >= len(value):
                raise ValueError(f"source field array index is out of range: {source_field}")
            value = value[ordinal]
            continue
        if "=" not in selector:
            raise ValueError(f"source field selector is unsupported: {source_field}")
        selector_key, selector_value = selector.split("=", 1)
        if (
            len(selector_value) >= 2
            and selector_value[0] == selector_value[-1]
            and selector_value[0] in {"'", '"'}
        ):
            selector_value = selector_value[1:-1]
        matches = [
            item
            for item in value
            if isinstance(item, dict)
            and clean_text(_nested_source_value(item, selector_key))
            == selector_value
        ]
        if len(matches) != 1:
            raise ValueError(
                "source field selector is not unique: "
                f"{source_field} ({len(matches)} matches)"
            )
        value = matches[0]
    return value


def _normalized_records_from_source_value(
    evidence: dict[str, Any], value: Any
) -> list[dict[str, Any]]:
    """Recompute governed records from one exact extracted source row."""
    artifact = clean_text(evidence.get("source_artifact"))
    observed_at = clean_text(evidence.get("retrieved_at"))
    normalized: list[dict[str, Any]] = []
    if artifact.endswith("/govuk-search.json"):
        if isinstance(value, dict):
            normalized.append(normalize_govuk(value, observed_at))
    elif artifact.endswith("/github-repositories.json"):
        if isinstance(value, dict):
            normalized.append(normalize_github(value, observed_at))
    elif artifact.endswith("/cddo-api-catalogue.json"):
        if isinstance(value, dict):
            normalized.append(normalize_cddo(value, observed_at))
    elif artifact == "source/curated-records.json":
        if isinstance(value, dict):
            normalized.append(normal_record(value))
    elif artifact == "source/curated-rights-access.json":
        if isinstance(value, dict):
            normalized.append(
                {
                    "source_family": clean_text(value.get("source_family")),
                    "source_native_id": clean_text(value.get("source_native_id")),
                }
            )
    elif artifact.startswith("source/observations/"):
        candidates: list[Any]
        if (
            isinstance(value, dict)
            and isinstance(value.get("metadata"), dict)
            and isinstance(value["metadata"].get("available_translations"), list)
        ):
            candidates = value["metadata"]["available_translations"]
        else:
            candidates = [value]
        for candidate in candidates:
            if isinstance(candidate, dict):
                normalized.append(
                    normalize_govuk_content_translation(candidate, observed_at)
                )
    return normalized


def _record_ids_from_source_value(
    evidence: dict[str, Any], value: Any
) -> set[str]:
    """Recompute record identities from an exact extracted source row."""
    return {
        record_id_for(
            clean_text(record["source_family"]),
            clean_text(record["source_native_id"]),
        )
        for record in _normalized_records_from_source_value(evidence, value)
    }


def validate_relationship_evidence_bindings(
    assertions: list[dict[str, Any]],
    *,
    records: list[dict[str, Any]] | None = None,
    record_bindings: dict[str, dict[str, Any]] | None = None,
    cpsv_mappings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve every row and prove that it supports its exact directed claim."""
    records_by_id = {
        clean_text(record.get("record_id")): record
        for record in records or []
        if clean_text(record.get("record_id"))
    }
    evidence_ids: set[str] = set()
    evidence_rows_for_digest: list[str] = []
    allowed_predicates_by_role = {
        "record-projection": {
            CATALOGUE_RECORD_PREDICATE,
            CATALOGUE_RESOURCE_PREDICATE,
            CATALOGUE_DATASET_PREDICATE,
            PRIMARY_TOPIC_PREDICATE,
            SOURCE_PREDICATE,
            DERIVED_FROM_PREDICATE,
            GENERATED_BY_PREDICATE,
            LANGUAGE_PREDICATE,
            SPATIAL_PREDICATE,
        },
        "rights-assessment": {RIGHTS_PREDICATE},
        "curated-rights-classification": {RIGHTS_PREDICATE},
        "publisher-jurisdiction": {SPATIAL_PREDICATE},
        "publisher-source": {PUBLISHER_PREDICATE},
        "publisher-registry": {PUBLISHER_PREDICATE},
        "competent-authority-evidence": {COMPETENT_AUTHORITY_PREDICATE},
        "translation-observation": {TRANSLATION_PREDICATE},
    }
    expected_type_by_role = {
        "record-projection": "governed-normalisation-input",
        "rights-assessment": "governed-rights-assessment",
        "curated-rights-classification": (
            "governed-curated-rights-classification"
        ),
        "publisher-jurisdiction": "governed-jurisdiction-statement",
        "publisher-source": "frozen-source-metadata",
        "publisher-registry": "governed-identity-registry",
        "translation-observation": "official-metadata-observation",
    }

    def evidence_role(predicate_iri: str, evidence_type: str) -> str:
        if (
            predicate_iri == COMPETENT_AUTHORITY_PREDICATE
            and evidence_type
            in {"competent-authority-delivery", "public-organisation-identity"}
        ):
            return "competent-authority-evidence"
        roles_by_type = {
            "governed-normalisation-input": "record-projection",
            "governed-rights-assessment": "rights-assessment",
            "governed-curated-rights-classification": (
                "curated-rights-classification"
            ),
            "governed-jurisdiction-statement": "publisher-jurisdiction",
            "frozen-source-metadata": "publisher-source",
            "governed-identity-registry": "publisher-registry",
            "official-metadata-observation": "translation-observation",
        }
        role = roles_by_type.get(evidence_type, "")
        if predicate_iri not in allowed_predicates_by_role.get(role, set()):
            raise ValueError(
                "relationship evidence type does not support its predicate: "
                f"{evidence_type} -> {predicate_iri}"
            )
        return role

    def record_id_from_assertion(
        *,
        base: str,
        source_iri: str,
        predicate_iri: str,
        target_iri: str,
        role: str,
        location: str,
    ) -> str:
        if role == "publisher-jurisdiction":
            return ""

        record_prefix = urljoin(base, "records/")
        entity_prefix = urljoin(base, "id/entity/")

        def identifier(iri: str, prefix: str) -> str:
            value = iri.removeprefix(prefix) if iri.startswith(prefix) else ""
            if not re.fullmatch(r"hmlr-[0-9a-f]{24}", value):
                raise ValueError(
                    "relationship evidence endpoint does not encode a governed "
                    f"record: {location}"
                )
            return value

        if predicate_iri in {
            CATALOGUE_RECORD_PREDICATE,
        }:
            return identifier(target_iri, record_prefix)
        if predicate_iri in {
            CATALOGUE_RESOURCE_PREDICATE,
            CATALOGUE_DATASET_PREDICATE,
        }:
            return identifier(target_iri, entity_prefix)
        if predicate_iri in {PRIMARY_TOPIC_PREDICATE, SOURCE_PREDICATE}:
            return identifier(source_iri, record_prefix)
        if predicate_iri in {
            DERIVED_FROM_PREDICATE,
            LANGUAGE_PREDICATE,
            PUBLISHER_PREDICATE,
            COMPETENT_AUTHORITY_PREDICATE,
            TRANSLATION_PREDICATE,
            SPATIAL_PREDICATE,
        }:
            return identifier(source_iri, entity_prefix)
        if predicate_iri in {RIGHTS_PREDICATE, GENERATED_BY_PREDICATE}:
            prefix = record_prefix if source_iri.startswith(record_prefix) else entity_prefix
            return identifier(source_iri, prefix)
        raise ValueError(
            f"relationship evidence predicate has no record rule: {location}"
        )

    def assertion_iri(assertion: dict[str, Any], field: str) -> str:
        raw = assertion.get(field)
        return clean_text(raw.get("@id") if isinstance(raw, dict) else raw)

    def require_role_governed_values(
        *,
        assertion: dict[str, Any],
        evidence: dict[str, Any],
        role: str,
        base: str,
        record_id: str,
        source_iri: str,
        predicate_iri: str,
        target_iri: str,
        source_document: Any,
        source_value: Any,
        location: str,
    ) -> None:
        """Bind mutable presentation fields back to governed source values."""
        rights = assertion.get("rights")
        expected_rights_source = urljoin(base, "data/rights.json")
        if (
            not isinstance(rights, dict)
            or clean_text(rights.get("source")) != expected_rights_source
        ):
            raise ValueError(
                f"relationship rights source differs from its governed value: {location}"
            )

        expected_evidence: dict[str, str] = {}
        expected_observed_at = ""
        expected_authority_source = ""
        record = records_by_id.get(record_id)

        projection_rule = urljoin(
            base,
            "id/rule/governed-relationship-projection-"
            + sha256_bytes(predicate_iri.encode("utf-8"))[:16]
            + "-v1",
        )
        publisher_rule = urljoin(
            base, "id/rule/governed-publisher-registry-v1"
        )
        translation_rule = urljoin(
            base, "id/rule/govuk-content-available-translations-v1"
        )

        if role in {
            "record-projection",
            "rights-assessment",
            "curated-rights-classification",
        }:
            expected_evidence["normalization"] = projection_rule
            if record is not None:
                expected_observed_at = clean_text(record.get("observed_at"))
                if role == "curated-rights-classification":
                    governed_url = (
                        clean_text(source_value.get("evidence_url"))
                        if isinstance(source_value, dict)
                        else ""
                    )
                elif predicate_iri in {SOURCE_PREDICATE, DERIVED_FROM_PREDICATE}:
                    matching_urls = [
                        clean_text(url)
                        for url in record.get("source_urls", [])
                        if semantic_source_resource_iri(
                            base, record, clean_text(url)
                        )
                        == target_iri
                    ]
                    if len(matching_urls) != 1:
                        raise ValueError(
                            "relationship source target does not select one "
                            f"governed URL: {location}"
                        )
                    governed_url = matching_urls[0]
                else:
                    governed_url = clean_text(record.get("canonical_source_url"))
                expected_evidence.update(
                    {
                        "url": governed_url,
                        "resource": governed_url,
                        "retrieved_at": expected_observed_at,
                    }
                )
                expected_authority_source = (
                    clean_text(record.get("canonical_source_url"))
                    if role == "curated-rights-classification"
                    else governed_url
                )
            elif isinstance(source_document, dict) and role == "record-projection":
                expected_observed_at = clean_text(
                    source_document.get("observed_at")
                )
                if expected_observed_at:
                    expected_evidence["retrieved_at"] = expected_observed_at

        elif role in {"publisher-source", "publisher-registry"}:
            expected_evidence["normalization"] = publisher_rule
            if role == "publisher-registry":
                expected_evidence.update(
                    {"url": target_iri, "resource": target_iri}
                )
            if record is not None:
                governed_url = clean_text(record.get("canonical_source_url"))
                expected_observed_at = clean_text(record.get("observed_at"))
                expected_authority_source = governed_url
                expected_evidence["retrieved_at"] = expected_observed_at
                if role == "publisher-source":
                    expected_evidence.update(
                        {"url": governed_url, "resource": governed_url}
                    )
            elif isinstance(source_document, dict) and role == "publisher-source":
                expected_observed_at = clean_text(
                    source_document.get("observed_at")
                )
                if expected_observed_at:
                    expected_evidence["retrieved_at"] = expected_observed_at

        elif role == "publisher-jurisdiction":
            expected_observed_at = (
                clean_text(source_document.get("observed_at"))
                if isinstance(source_document, dict)
                else ""
            )
            expected_authority_source = HMLR_PUBLISHER_IRI
            expected_evidence.update(
                {
                    "url": HMLR_PUBLISHER_IRI,
                    "resource": HMLR_PUBLISHER_IRI,
                    "normalization": projection_rule,
                    "retrieved_at": expected_observed_at,
                }
            )

        elif role == "competent-authority-evidence":
            expected_authority_source = HMLR_PUBLISHER_IRI
            if record is not None:
                expected_observed_at = clean_text(record.get("observed_at"))

        elif role == "translation-observation":
            expected_observed_at = (
                clean_text(source_document.get("observed_at"))
                if isinstance(source_document, dict)
                else ""
            )
            governed_url = (
                clean_text(source_value.get("api_url"))
                if isinstance(source_value, dict)
                else ""
            )
            expected_authority_source = governed_url
            expected_evidence.update(
                {
                    "url": governed_url,
                    "resource": source_iri,
                    "normalization": translation_rule,
                    "retrieved_at": expected_observed_at,
                }
            )

        for field_name, expected in expected_evidence.items():
            if not expected or clean_text(evidence.get(field_name)) != expected:
                raise ValueError(
                    "relationship evidence "
                    f"{field_name} differs from its role-governed value: {location}"
                )
        if (
            expected_observed_at
            and clean_text(assertion.get("observed_at")) != expected_observed_at
        ):
            raise ValueError(
                "relationship assertion observed_at differs from its "
                f"role-governed value: {location}"
            )
        authority = assertion.get("authority")
        if (
            expected_authority_source
            and (
                not isinstance(authority, dict)
                or clean_text(authority.get("source"))
                != expected_authority_source
            )
        ):
            raise ValueError(
                "relationship authority source differs from its "
                f"role-governed value: {location}"
            )

    def require_record_endpoint_binding(
        *,
        base: str,
        record_id: str,
        source_iri: str,
        predicate_iri: str,
        target_iri: str,
        evidence: dict[str, Any],
        location: str,
    ) -> None:
        record_iri = urljoin(base, "records/" + record_id)
        entity_iri = urljoin(base, "id/entity/" + record_id)
        catalogue_iri = urljoin(base, "id/catalogue/hmlr-public-estate")
        expected_endpoints: dict[str, tuple[set[str], set[str]]] = {
            CATALOGUE_RECORD_PREDICATE: ({catalogue_iri}, {record_iri}),
            CATALOGUE_RESOURCE_PREDICATE: ({catalogue_iri}, {entity_iri}),
            CATALOGUE_DATASET_PREDICATE: ({catalogue_iri}, {entity_iri}),
            PRIMARY_TOPIC_PREDICATE: ({record_iri}, {entity_iri}),
            SOURCE_PREDICATE: (
                {record_iri},
                {urljoin(base, "id/source-resource/" + record_id + "-")},
            ),
            DERIVED_FROM_PREDICATE: (
                {entity_iri},
                {urljoin(base, "id/source-resource/" + record_id + "-")},
            ),
            RIGHTS_PREDICATE: ({record_iri, entity_iri}, {urljoin(base, "rights/")}),
            GENERATED_BY_PREDICATE: (
                {record_iri, entity_iri},
                {urljoin(base, "activities/")},
            ),
            LANGUAGE_PREDICATE: ({entity_iri}, set()),
            PUBLISHER_PREDICATE: ({entity_iri}, set()),
            COMPETENT_AUTHORITY_PREDICATE: ({entity_iri}, set()),
            TRANSLATION_PREDICATE: ({entity_iri}, set()),
            SPATIAL_PREDICATE: ({entity_iri}, {urljoin(base, "id/jurisdiction/")}),
        }
        endpoints = expected_endpoints.get(predicate_iri)
        if endpoints is None or source_iri not in endpoints[0]:
            raise ValueError(
                f"relationship evidence record does not bind its source endpoint: {location}"
            )
        target_rules = endpoints[1]
        if target_rules and not any(
            target_iri == value
            or (
                predicate_iri
                in {
                    SOURCE_PREDICATE,
                    DERIVED_FROM_PREDICATE,
                    RIGHTS_PREDICATE,
                    GENERATED_BY_PREDICATE,
                    SPATIAL_PREDICATE,
                }
                and target_iri.startswith(value)
            )
            for value in target_rules
        ):
            raise ValueError(
                f"relationship evidence record does not bind its target endpoint: {location}"
            )

        record = records_by_id.get(record_id)
        if records is not None and record is None:
            raise ValueError(
                f"relationship evidence binds an unknown record: {location}"
            )
        if record is None:
            return
        if predicate_iri == CATALOGUE_DATASET_PREDICATE and record.get("kind") != "dataset":
            raise ValueError(f"relationship dataset evidence has wrong semantics: {location}")
        if predicate_iri in {SOURCE_PREDICATE, DERIVED_FROM_PREDICATE}:
            evidence_url = clean_text(evidence.get("url"))
            if evidence_url not in record.get("source_urls", []):
                raise ValueError(
                    f"relationship source evidence does not support its URL: {location}"
                )
            expected_target = semantic_source_resource_iri(
                base, record, evidence_url
            )
            if target_iri != expected_target:
                raise ValueError(
                    f"relationship source evidence does not support its target: {location}"
                )
        elif predicate_iri == RIGHTS_PREDICATE:
            expected_targets = {
                urljoin(base, "rights/" + rights_ref)
                for rights_ref in [
                    clean_text(record["rights_ref"]),
                    *[
                        clean_text(value)
                        for value in record.get("additional_rights_refs", [])
                    ],
                ]
                if rights_ref
            }
            if target_iri not in expected_targets:
                raise ValueError(
                    f"relationship rights evidence does not support its record: {location}"
                )
        elif predicate_iri == GENERATED_BY_PREDICATE:
            expected_target = urljoin(
                base,
                "activities/"
                + clean_text(record["source_family"])
                + "-"
                + hashlib.sha256(
                    clean_text(record["observed_at"]).encode("utf-8")
                ).hexdigest()[:12],
            )
            if target_iri != expected_target:
                raise ValueError(
                    f"relationship provenance evidence has wrong target: {location}"
                )
        elif predicate_iri == LANGUAGE_PREDICATE:
            language_targets = {
                "cy": "http://publications.europa.eu/resource/authority/language/CYM",
                "en": "http://publications.europa.eu/resource/authority/language/ENG",
            }
            expected_targets = {
                language_targets[language]
                for language in record.get("languages", [])
                if language in language_targets
            }
            if target_iri not in expected_targets:
                raise ValueError(
                    f"relationship language evidence does not support its target: {location}"
                )
        elif predicate_iri == PUBLISHER_PREDICATE:
            if target_iri != clean_text(record.get("publisher_id")):
                raise ValueError(
                    f"relationship publisher evidence does not support its target: {location}"
                )
        elif predicate_iri == COMPETENT_AUTHORITY_PREDICATE:
            if target_iri != HMLR_PUBLISHER_IRI:
                raise ValueError(
                    f"relationship authority evidence does not support its target: {location}"
                )
        elif predicate_iri == SPATIAL_PREDICATE:
            jurisdiction = clean_text(record.get("jurisdiction"))
            expected_target = (
                semantic_jurisdiction_iri(base, jurisdiction)
                if jurisdiction
                else ""
            )
            if target_iri != expected_target:
                raise ValueError(
                    f"relationship spatial evidence does not support its target: {location}"
                )
            if cpsv_mappings is not None and record_id not in cpsv_mappings["mapped"]:
                raise ValueError(
                    f"relationship spatial evidence lacks a mapped service: {location}"
                )

    for assertion in assertions:
        assertion_id = clean_text(assertion.get("@id")) or "unknown"
        source_iri = assertion_iri(assertion, "source")
        predicate_iri = assertion_iri(assertion, "predicate")
        target_iri = assertion_iri(assertion, "target")
        publication_base = relationship_publication_base(assertion_id)
        expected_assertion_id = relationship_assertion_id(
            publication_base, source_iri, predicate_iri, target_iri
        )
        if assertion_id != expected_assertion_id:
            raise ValueError(
                f"relationship assertion ID does not derive from its triple: {assertion_id}"
            )
        evidence_rows = assertion.get("evidence")
        if not isinstance(evidence_rows, list) or not evidence_rows:
            raise ValueError(f"relationship evidence is absent: {assertion_id}")
        assertion_evidence_roles: list[str] = []
        for ordinal, evidence in enumerate(evidence_rows):
            location = f"{assertion_id}#{ordinal}"
            if not isinstance(evidence, dict):
                raise ValueError(f"relationship evidence is not an object: {assertion_id}")
            evidence_id = clean_text(evidence.get("@id"))
            if evidence_id in evidence_ids:
                raise ValueError(f"duplicate relationship evidence ID: {evidence_id}")
            evidence_ids.add(evidence_id)
            expected_evidence_id = relationship_evidence_id(
                publication_base,
                evidence,
                source_iri=source_iri,
                predicate_iri=predicate_iri,
                target_iri=target_iri,
            )
            if evidence_id != expected_evidence_id:
                raise ValueError(
                    f"relationship evidence ID is not deterministic: {location}"
                )
            evidence_type = clean_text(evidence.get("type"))
            role = evidence_role(predicate_iri, evidence_type)
            assertion_evidence_roles.append(role)
            expected_type = expected_type_by_role.get(role)
            if expected_type and evidence_type != expected_type:
                raise ValueError(
                    f"relationship evidence type does not support its role: {location}"
                )
            field = clean_text(evidence.get("source_field"))
            if clean_text(evidence.get("locator")) != field:
                raise ValueError(
                    f"relationship evidence locator drift: {location}"
                )
            if (
                evidence.get("source_value_hash_canonicalization")
                != CPSV_SOURCE_VALUE_CANONICALIZATION
            ):
                raise ValueError(
                    "relationship evidence canonicalisation differs: " + location
                )
            artifact = clean_text(evidence.get("source_artifact"))
            path = ROOT / artifact
            if (
                not artifact
                or Path(artifact).is_absolute()
                or ".." in Path(artifact).parts
                or not path.is_file()
                or path.is_symlink()
            ):
                raise ValueError(
                    f"relationship evidence artefact is unsafe: {location}"
                )
            source_digest, source_document = source_artifact_snapshot(artifact)
            if source_digest != clean_text(evidence.get("source_sha256")):
                raise ValueError(
                    f"relationship evidence artefact digest drift: {location}"
                )
            value = resolve_source_field_value(artifact, field)
            expected_value_digest = sha256_bytes(compact_canonical_json(value))
            if expected_value_digest != clean_text(
                evidence.get("source_value_sha256")
            ):
                raise ValueError(
                    "relationship evidence is not bound to its exact source value: "
                    + location
                )
            source_locator = clean_text(evidence.get("source_locator"))
            if source_locator:
                record_ids = _record_ids_from_source_value(evidence, value)
                if source_locator not in record_ids:
                    raise ValueError(
                        "relationship evidence row does not bind its record: "
                        + location
                    )
            record_id = record_id_from_assertion(
                base=publication_base,
                source_iri=source_iri,
                predicate_iri=predicate_iri,
                target_iri=target_iri,
                role=role,
                location=location,
            )
            if role not in {"publisher-jurisdiction"} and not record_id:
                raise ValueError(
                    f"relationship evidence lacks its governed record: {location}"
                )
            if record_id:
                require_record_endpoint_binding(
                    base=publication_base,
                    record_id=record_id,
                    source_iri=source_iri,
                    predicate_iri=predicate_iri,
                    target_iri=target_iri,
                    evidence=evidence,
                    location=location,
                )
            require_role_governed_values(
                assertion=assertion,
                evidence=evidence,
                role=role,
                base=publication_base,
                record_id=record_id,
                source_iri=source_iri,
                predicate_iri=predicate_iri,
                target_iri=target_iri,
                source_document=source_document,
                source_value=value,
                location=location,
            )

            if (
                record_bindings is not None
                and role in {"record-projection", "publisher-source"}
                and record_id
            ):
                selected = record_bindings.get(record_id)
                if selected is None:
                    raise ValueError(
                        f"relationship evidence has no permitted record binding: {location}"
                    )
                candidates = [selected, *selected.get("representations", [])]
                exact_candidates = [
                    candidate
                    for candidate in candidates
                    if clean_text(candidate.get("record_id")) == source_locator
                    and clean_text(candidate.get("source_artifact")) == artifact
                    and clean_text(candidate.get("source_field")) == field
                    and clean_text(candidate.get("source_sha256"))
                    == clean_text(evidence.get("source_sha256"))
                    and sha256_bytes(
                        compact_canonical_json(candidate.get("source_value"))
                    )
                    == expected_value_digest
                ]
                exact_candidate_keys = {
                    (
                        clean_text(candidate.get("record_id")),
                        clean_text(candidate.get("source_artifact")),
                        clean_text(candidate.get("source_field")),
                        clean_text(candidate.get("source_sha256")),
                    )
                    for candidate in exact_candidates
                }
                if len(exact_candidate_keys) != 1:
                    raise ValueError(
                        "relationship evidence is not a permitted merged "
                        f"representation: {location}"
                    )
                if predicate_iri in {SOURCE_PREDICATE, DERIVED_FROM_PREDICATE}:
                    if clean_text(evidence.get("url")) not in exact_candidates[0].get(
                        "source_urls", []
                    ):
                        raise ValueError(
                            "relationship evidence representation does not support "
                            f"its source URL: {location}"
                        )
            if evidence_type == "governed-rights-assessment":
                rights_id = clean_text(value.get("id")) if isinstance(value, dict) else ""
                if not rights_id or not target_iri.endswith("/rights/" + rights_id):
                    raise ValueError(
                        "relationship rights evidence row does not bind its target: "
                        + location
                    )
            if evidence_type == "governed-curated-rights-classification":
                classification = value if isinstance(value, dict) else {}
                record = records_by_id.get(record_id)
                classification_rights_refs = {
                    clean_text(classification.get("rights_ref")),
                    *{
                        clean_text(rights_ref)
                        for rights_ref in classification.get(
                            "additional_rights_refs", []
                        )
                    },
                }
                selected_rights_ref = target_iri.rsplit("/rights/", 1)[-1]
                expected_record_values = {
                    "source_native_id": clean_text(
                        record.get("source_native_id") if record else ""
                    ),
                    "source_family": clean_text(
                        record.get("source_family") if record else ""
                    ),
                    "access_state": clean_text(
                        record.get("access_state") if record else ""
                    ),
                    "rights_state": clean_text(
                        record.get("rights_state") if record else ""
                    ),
                    "classification_scope": clean_text(
                        record.get("rights_access_scope") if record else ""
                    ),
                    "metadata_page_access_state": clean_text(
                        record.get("metadata_page_access_state") if record else ""
                    ),
                    "classification_status": clean_text(
                        record.get("rights_access_classification_status")
                        if record
                        else ""
                    ),
                }
                if (
                    artifact != "source/curated-rights-access.json"
                    or source_locator != record_id
                    or selected_rights_ref not in classification_rights_refs
                    or clean_text(evidence.get("url"))
                    != clean_text(classification.get("evidence_url"))
                    or (
                        record is not None
                        and any(
                            clean_text(classification.get(key)) != expected
                            for key, expected in expected_record_values.items()
                        )
                    )
                ):
                    raise ValueError(
                        "relationship curated rights classification row does not "
                        f"bind its governed record and target: {location}"
                    )
            if evidence_type == "governed-identity-registry":
                publisher_id = (
                    clean_text(value.get("id")) if isinstance(value, dict) else ""
                )
                if not publisher_id or target_iri != publisher_id:
                    raise ValueError(
                        "relationship publisher registry row does not bind its target: "
                        + location
                    )
            if role == "publisher-jurisdiction":
                if (
                    source_iri != HMLR_PUBLISHER_IRI
                    or predicate_iri != SPATIAL_PREDICATE
                    or target_iri
                    != urljoin(publication_base, "id/jurisdiction/england-and-wales")
                    or clean_text(evidence.get("url")) != HMLR_PUBLISHER_IRI
                ):
                    raise ValueError(
                        f"publisher jurisdiction evidence has wrong semantics: {location}"
                    )
            if role == "competent-authority-evidence":
                evidence_ref = clean_text(evidence.get("value"))
                if (
                    not evidence_ref
                    or predicate_iri != COMPETENT_AUTHORITY_PREDICATE
                    or target_iri != HMLR_PUBLISHER_IRI
                    or clean_text(evidence.get("rule_id"))
                    != clean_text(assertion.get("rule"))
                ):
                    raise ValueError(
                        f"CPSV evidence does not bind its decision record: {location}"
                    )
                if cpsv_mappings is not None:
                    decision = cpsv_mappings["mapped"].get(record_id)
                    governed = cpsv_mappings["evidence_by_id"].get(evidence_ref)
                    if (
                        decision is None
                        or clean_text(decision.get("record_id")) != record_id
                        or not clean_text(decision.get("id"))
                        or evidence_ref
                        not in decision["competent_authority"]["evidence_refs"]
                        or governed is None
                    ):
                        raise ValueError(
                            f"CPSV evidence reference does not support its service: {location}"
                        )
                    expected_governed_fields = {
                        "type": governed["claim_supported"],
                        "url": governed["url"],
                        "resource": governed["resource"],
                        "source_artifact": governed["source_artifact"],
                        "source_sha256": governed["source_sha256"],
                        "source_field": governed["source_field"],
                        "source_value_sha256": governed["source_value_sha256"],
                        "source_value_hash_canonicalization": governed[
                            "source_value_hash_canonicalization"
                        ],
                        "locator": governed["locator"],
                        "normalization": governed["normalization"],
                        "rationale": governed["rationale"],
                        "retrieved_at": governed["retrieved_at"],
                    }
                    if any(
                        evidence.get(key) != expected
                        for key, expected in expected_governed_fields.items()
                    ):
                        raise ValueError(
                            f"CPSV evidence differs from its governed reference: {location}"
                        )
                    governed_record_id = clean_text(governed.get("record_id"))
                    if governed_record_id and source_locator != governed_record_id:
                        raise ValueError(
                            f"CPSV evidence source record differs: {location}"
                        )
            if role == "translation-observation":
                group = clean_text(evidence.get("value"))
                source_record_id = record_id
                target_prefix = urljoin(publication_base, "id/entity/")
                target_record_id = (
                    target_iri.removeprefix(target_prefix)
                    if target_iri.startswith(target_prefix)
                    else ""
                )
                if (
                    not re.fullmatch(r"hmlr-[0-9a-f]{24}", target_record_id)
                    or source_locator != source_record_id
                    or source_iri
                    != urljoin(publication_base, "id/entity/" + source_record_id)
                    or clean_text(evidence.get("resource")) != source_iri
                ):
                    raise ValueError(
                        f"translation evidence has wrong direction or locale: {location}"
                    )
                metadata = value.get("metadata") if isinstance(value, dict) else None
                translations = (
                    metadata.get("available_translations")
                    if isinstance(metadata, dict)
                    else None
                )
                if (
                    not group
                    or clean_text(metadata.get("content_id")) != group
                    or not isinstance(translations, list)
                ):
                    raise ValueError(
                        f"translation evidence does not bind its content group: {location}"
                    )
                observed_at = clean_text(evidence.get("retrieved_at"))
                source_locales: dict[str, str] = {}
                for translation in translations:
                    if not isinstance(translation, dict):
                        continue
                    normalized = normalize_govuk_content_translation(
                        translation, observed_at
                    )
                    translated_record_id = record_id_for(
                        clean_text(normalized["source_family"]),
                        clean_text(normalized["source_native_id"]),
                    )
                    source_locales[translated_record_id] = clean_text(
                        translation.get("locale")
                    )
                source_locale = source_locales.get(source_record_id, "")
                target_locale = source_locales.get(target_record_id, "")
                if source_locale == "en" or not source_locale or target_locale != "en":
                    raise ValueError(
                        f"translation evidence has wrong direction or locale: {location}"
                    )
                if records is not None:
                    source_record = records_by_id.get(source_record_id)
                    target_record = records_by_id.get(target_record_id)
                    if (
                        source_record is None
                        or target_record is None
                        or clean_text(source_record.get("translation_group")) != group
                        or clean_text(target_record.get("translation_group")) != group
                        or source_locale not in source_record.get("languages", [])
                        or target_locale not in target_record.get("languages", [])
                    ):
                        raise ValueError(
                            f"translation evidence differs from catalogue records: {location}"
                        )
            evidence_rows_for_digest.append(
                compact_canonical_json(evidence).decode("utf-8")
            )

        if predicate_iri == RIGHTS_PREDICATE and records is not None:
            record_id = record_id_from_assertion(
                base=publication_base,
                source_iri=source_iri,
                predicate_iri=predicate_iri,
                target_iri=target_iri,
                role="rights-assessment",
                location=assertion_id,
            )
            record = records_by_id.get(record_id)
            expected_roles = ["rights-assessment"]
            if record is not None and record.get("curation") == "reviewed":
                expected_roles.append("curated-rights-classification")
            if sorted(assertion_evidence_roles) != sorted(expected_roles):
                raise ValueError(
                    "relationship rights assertion evidence roles differ from "
                    f"its governed record: {assertion_id}"
                )

    def set_digest(values: Iterable[Any]) -> str:
        payload = json.dumps(
            sorted(values),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return sha256_bytes(payload)

    return {
        "status": "conformant",
        "assertions_validated": len(assertions),
        "evidence_rows_validated": len(evidence_rows_for_digest),
        "evidence_identity_set_sha256": set_digest(evidence_ids),
        "evidence_row_set_sha256": set_digest(evidence_rows_for_digest),
    }


def frozen_record_source_bindings(
    records: list[dict[str, Any]],
    composite_manifest: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Bind every selected catalogue record to one exact frozen source row."""
    snapshot_input = next(
        (
            row
            for row in composite_manifest["inputs"]
            if row["id"] == "public-metadata-snapshot"
        ),
        None,
    )
    content_input = next(
        (
            row
            for row in composite_manifest["inputs"]
            if row["id"] == "govuk-content-locale-translations"
        ),
        None,
    )
    if snapshot_input is None or content_input is None:
        raise ValueError("composite input lacks frozen record-binding sources")

    candidates: dict[tuple[str, str], dict[str, Any]] = {}

    def register(
        normalized: dict[str, Any],
        *,
        artifact: str,
        source_field: str,
        value: Any,
    ) -> None:
        key = (
            clean_text(normalized["source_family"]),
            clean_text(normalized["source_native_id"]),
        )
        if key in candidates:
            raise ValueError(f"duplicate frozen source-row identity: {key}")
        if resolve_source_field_value(artifact, source_field) != value:
            raise ValueError(f"frozen source-row locator does not round-trip: {key}")
        candidates[key] = {
            "record_id": record_id_for(*key),
            "source_artifact": artifact,
            "source_sha256": source_artifact_snapshot(artifact)[0],
            "source_field": source_field,
            "source_value": value,
            "source_urls": list(normalized.get("source_urls", [])),
        }

    snapshot_manifest_path = ROOT / clean_text(snapshot_input["path"])
    snapshot_manifest = load_json(snapshot_manifest_path)
    snapshot_observed_at = clean_text(snapshot_manifest.get("observed_at"))
    adapters = {
        "govuk-search.json": normalize_govuk,
        "github-repositories.json": normalize_github,
        "cddo-api-catalogue.json": normalize_cddo,
    }
    for filename, adapter in adapters.items():
        source_path = snapshot_manifest_path.parent / filename
        artifact = source_path.relative_to(ROOT).as_posix()
        payload = load_json(source_path)
        for ordinal, value in enumerate(payload.get("results", [])):
            if not isinstance(value, dict):
                raise ValueError(f"frozen source row is not an object: {artifact}")
            register(
                adapter(value, snapshot_observed_at),
                artifact=artifact,
                source_field=f"results[{ordinal}]",
                value=value,
            )

    content_path = ROOT / clean_text(content_input["path"])
    content_artifact = content_path.relative_to(ROOT).as_posix()
    content_payload = load_json(content_path)
    content_observed_at = clean_text(content_payload.get("observed_at"))
    for observation_ordinal, observation in enumerate(
        content_payload.get("observations", [])
    ):
        translations = observation.get("metadata", {}).get(
            "available_translations", []
        )
        for translation_ordinal, value in enumerate(translations):
            if not isinstance(value, dict):
                raise ValueError("Content API frozen source row is not an object")
            register(
                normalize_govuk_content_translation(value, content_observed_at),
                artifact=content_artifact,
                source_field=(
                    f"observations[{observation_ordinal}].metadata."
                    f"available_translations[{translation_ordinal}]"
                ),
                value=value,
            )

    curated_path = ROOT / "source" / "curated-records.json"
    curated_artifact = curated_path.relative_to(ROOT).as_posix()
    for ordinal, value in enumerate(load_json(curated_path).get("records", [])):
        if not isinstance(value, dict):
            raise ValueError("curated frozen source row is not an object")
        register(
            normal_record(value),
            artifact=curated_artifact,
            source_field=f"records[{ordinal}]",
            value=value,
        )

    bindings: dict[str, dict[str, Any]] = {}
    for record in records:
        record_id = clean_text(record["record_id"])
        key = (
            clean_text(record["source_family"]),
            clean_text(record["source_native_id"]),
        )
        candidate = candidates.get(key)
        if candidate is None:
            raise ValueError(f"record lacks an exact frozen source-row binding: {record_id}")
        expected_record_id = record_id_for(*key)
        if expected_record_id != record_id or record_id in bindings:
            raise ValueError(f"record source-row identity differs: {record_id}")
        representation_bindings: list[dict[str, Any]] = []
        for representation in record.get("representations", []):
            representation_key = (
                clean_text(representation.get("source_family")),
                clean_text(representation.get("id")),
            )
            representation_binding = candidates.get(representation_key)
            if representation_binding is None:
                raise ValueError(
                    "record representation lacks an exact frozen source-row "
                    f"binding: {record_id} -> {representation_key}"
                )
            representation_bindings.append(representation_binding)
        bindings[record_id] = {
            **candidate,
            "record_id": record_id,
            "representations": representation_bindings,
        }
    if len(bindings) != len(records):
        raise ValueError("frozen source-row binding coverage is incomplete")
    return bindings


def normalized_relationship_assertion(
    publication_base: str,
    *,
    source_iri: str,
    predicate_iri: str,
    target_iri: str,
    source_route: str,
    target_route: str,
    observed_at: str,
    evidence_url: str,
    source_artifact: str,
    source_sha256: str,
    source_field: str,
    source_value: Any,
    locator: str,
    source_locator: str | None = None,
    evidence_type: str = "governed-normalisation-input",
    evidence_role: str = "record-projection",
    record_id: str | None = None,
) -> dict[str, Any]:
    """Build one deterministic evidence-bearing normalised relationship."""
    if locator != source_field:
        raise ValueError("relationship evidence locator must equal source_field")
    publication_base = publication_base.rstrip("/") + "/"
    governed_evidence_url = semantic_web_iri(evidence_url)
    preferred_label, inverse_label = relationship_labels(predicate_iri)
    triple = (source_iri, predicate_iri, target_iri)
    predicate_hash = sha256_bytes(predicate_iri.encode("utf-8"))[:16]
    rule_id = urljoin(
        publication_base,
        "id/rule/governed-relationship-projection-" + predicate_hash + "-v1",
    )
    activity_id = urljoin(
        publication_base,
        "id/activity/governed-relationship-projection-" + predicate_hash,
    )
    evidence = bind_relationship_evidence(
        publication_base,
        {
            "type": evidence_type,
            "url": governed_evidence_url,
            "resource": governed_evidence_url,
            "source_artifact": source_artifact,
            "source_sha256": source_sha256,
            "source_field": source_field,
            "source_value_sha256": sha256_bytes(
                compact_canonical_json(source_value)
            ),
            "source_value_hash_canonicalization": (
                CPSV_SOURCE_VALUE_CANONICALIZATION
            ),
            "locator": locator,
            **(
                {"source_locator": source_locator}
                if source_locator is not None
                else {}
            ),
            "normalization": rule_id,
            "retrieved_at": observed_at,
        },
        source_iri=source_iri,
        predicate_iri=predicate_iri,
        target_iri=target_iri,
        role=evidence_role,
        record_id=record_id,
    )
    return {
        "@id": relationship_assertion_id(publication_base, *triple),
        "@type": ["rdf:Statement", "okf:RelationshipAssertion"],
        "source": {"@id": source_iri},
        "predicate": {"@id": predicate_iri},
        "target": {"@id": target_iri},
        "source_route": source_route,
        "target_route": target_route,
        "kind": preferred_label,
        "label": preferred_label,
        "inverse_label": inverse_label,
        "assertion_status": "normalized",
        "assertion_scope": "real-world",
        "authority": {
            "class": "derived",
            "label": (
                "Deterministically normalised from the governed frozen input "
                "and explicit projection rule"
            ),
            "source": governed_evidence_url,
        },
        "derivation": rule_id,
        "derivation_activity": activity_id,
        "rule": rule_id,
        "observed_at": observed_at,
        "review_status": "implementation-authorised-pending-release-review",
        "evidence": [evidence],
        "rights": {
            "source": urljoin(publication_base, "data/rights.json"),
            "assertion": (
                "Project-authored normalised metadata assertion; source "
                "metadata retains its recorded rights and exceptions."
            ),
        },
    }


def structural_relationship_assertions(
    publication_base: str,
    records: list[dict[str, Any]],
    cpsv_mappings: dict[str, Any],
    record_bindings: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build all governed catalogue, provenance and supporting-entity edges."""
    publication_base = publication_base.rstrip("/") + "/"
    catalog_id = urljoin(publication_base, "id/catalogue/hmlr-public-estate")
    catalog_route = semantic_route("catalogue", catalog_id)
    rights_path = ROOT / "governance" / "rights-review.json"
    rights_relative = rights_path.relative_to(ROOT).as_posix()
    rights_sha256 = sha256_file(rights_path)
    rights_rows = load_json(rights_path).get("assessments", [])
    rights_bindings = {
        clean_text(row.get("id")): {
            "source_artifact": rights_relative,
            "source_sha256": rights_sha256,
            "source_field": f"assessments[{ordinal}]",
            "source_value": row,
        }
        for ordinal, row in enumerate(rights_rows)
        if isinstance(row, dict)
    }
    classifications, classification_bindings, _classification_receipt = (
        load_curated_rights_access_classifications()
    )
    source_register_path = ROOT / "source" / "source-register.json"
    source_register = load_json(source_register_path)
    source_register_relative = source_register_path.relative_to(ROOT).as_posix()
    source_register_sha256 = sha256_file(source_register_path)
    language_registry = {
        "cy": "http://publications.europa.eu/resource/authority/language/CYM",
        "en": "http://publications.europa.eu/resource/authority/language/ENG",
    }
    assertions: list[dict[str, Any]] = []

    def emit(
        record: dict[str, Any],
        predicate_iri: str,
        source_iri: str,
        target_iri: str,
        source_route: str,
        target_route: str,
        *,
        evidence_url: str | None = None,
        evidence_binding: dict[str, Any] | None = None,
        evidence_type: str = "governed-normalisation-input",
    ) -> None:
        record_id = clean_text(record["record_id"])
        binding = evidence_binding or record_bindings.get(record_id)
        if binding is None:
            raise ValueError(f"relationship record lacks source binding: {record_id}")
        source_locator = clean_text(binding.get("record_id")) or None
        assertion = normalized_relationship_assertion(
            publication_base,
            source_iri=source_iri,
            predicate_iri=predicate_iri,
            target_iri=target_iri,
            source_route=source_route,
            target_route=target_route,
            observed_at=clean_text(record["observed_at"]),
            evidence_url=(
                evidence_url or clean_text(record["canonical_source_url"])
            ),
            source_artifact=binding["source_artifact"],
            source_sha256=binding["source_sha256"],
            source_field=binding["source_field"],
            source_value=binding["source_value"],
            locator=binding["source_field"],
            source_locator=source_locator,
            evidence_type=evidence_type,
            evidence_role=(
                "rights-assessment"
                if evidence_type == "governed-rights-assessment"
                else "record-projection"
            ),
            record_id=record_id,
        )
        if (
            predicate_iri == RIGHTS_PREDICATE
            and record.get("curation") == "reviewed"
        ):
            classification = classifications.get(record["source_native_id"])
            classification_binding = classification_bindings.get(
                record["source_native_id"]
            )
            if classification is None or classification_binding is None:
                raise ValueError(
                    "curated rights assertion lacks an exact classification row: "
                    f"{record_id}"
                )
            classification_assertion = normalized_relationship_assertion(
                publication_base,
                source_iri=source_iri,
                predicate_iri=predicate_iri,
                target_iri=target_iri,
                source_route=source_route,
                target_route=target_route,
                observed_at=clean_text(record["observed_at"]),
                evidence_url=clean_text(classification["evidence_url"]),
                source_artifact=classification_binding["source_artifact"],
                source_sha256=classification_binding["source_sha256"],
                source_field=classification_binding["source_field"],
                source_value=classification_binding["source_value"],
                locator=classification_binding["source_field"],
                source_locator=classification_binding["record_id"],
                evidence_type="governed-curated-rights-classification",
                evidence_role="curated-rights-classification",
                record_id=record_id,
            )
            assertion["evidence"].extend(classification_assertion["evidence"])
        assertions.append(assertion)

    def source_url_binding(
        record: dict[str, Any], source_url: str
    ) -> dict[str, Any]:
        record_id = clean_text(record["record_id"])
        selected = record_bindings.get(record_id)
        if selected is None:
            raise ValueError(f"source URL lacks a record binding: {record_id}")
        matches = [
            binding
            for binding in selected.get("representations", [])
            if source_url in binding.get("source_urls", [])
        ]
        if not matches:
            raise ValueError(
                f"source URL lacks an exact frozen source row: {record_id} {source_url}"
            )
        matches.sort(
            key=lambda binding: (
                binding.get("record_id") != record_id,
                clean_text(binding.get("source_artifact")),
                clean_text(binding.get("source_field")),
            )
        )
        return matches[0]

    for record in sorted(records, key=lambda item: item["record_id"]):
        record_id = clean_text(record["record_id"])
        record_iri = urljoin(publication_base, "records/" + record_id)
        entity_iri = semantic_record_iri(publication_base, record)
        record_route = semantic_route("catalogue-record", record_iri)
        entity_route = "dataset/" + explorer_name("record", record_id)
        rights_rows_for_record: list[tuple[str, str, dict[str, Any]]] = []
        for rights_ref in [
            record["rights_ref"],
            *record.get("additional_rights_refs", []),
        ]:
            rights_iri = urljoin(publication_base, "rights/" + rights_ref)
            rights_binding = rights_bindings.get(rights_ref)
            if rights_binding is None:
                raise ValueError(
                    f"record lacks an exact rights-review row: {record_id}"
                )
            rights_rows_for_record.append(
                (rights_iri, semantic_route("rights", rights_iri), rights_binding)
            )
        activity_iri = urljoin(
            publication_base,
            "activities/"
            + record["source_family"]
            + "-"
            + hashlib.sha256(record["observed_at"].encode("utf-8")).hexdigest()[:12],
        )
        activity_route = semantic_route("activity", activity_iri)

        emit(
            record,
            CATALOGUE_RECORD_PREDICATE,
            catalog_id,
            record_iri,
            catalog_route,
            record_route,
        )
        emit(
            record,
            CATALOGUE_RESOURCE_PREDICATE,
            catalog_id,
            entity_iri,
            catalog_route,
            entity_route,
        )
        if record["kind"] == "dataset":
            emit(
                record,
                CATALOGUE_DATASET_PREDICATE,
                catalog_id,
                entity_iri,
                catalog_route,
                entity_route,
            )
        emit(
            record,
            PRIMARY_TOPIC_PREDICATE,
            record_iri,
            entity_iri,
            record_route,
            entity_route,
        )
        for source_url in record["source_urls"]:
            governed_url = semantic_web_iri(source_url)
            source_iri = semantic_source_resource_iri(
                publication_base, record, governed_url
            )
            source_route = semantic_route("source", source_iri)
            emit(
                record,
                SOURCE_PREDICATE,
                record_iri,
                source_iri,
                record_route,
                source_route,
                evidence_url=governed_url,
                evidence_binding=source_url_binding(record, governed_url),
            )
            emit(
                record,
                DERIVED_FROM_PREDICATE,
                entity_iri,
                source_iri,
                entity_route,
                source_route,
                evidence_url=governed_url,
                evidence_binding=source_url_binding(record, governed_url),
            )
        for source_iri, source_route in (
            (record_iri, record_route),
            (entity_iri, entity_route),
        ):
            for rights_iri, rights_route, rights_binding in rights_rows_for_record:
                emit(
                    record,
                    RIGHTS_PREDICATE,
                    source_iri,
                    rights_iri,
                    source_route,
                    rights_route,
                    evidence_binding=rights_binding,
                    evidence_type="governed-rights-assessment",
                )
            emit(
                record,
                GENERATED_BY_PREDICATE,
                source_iri,
                activity_iri,
                source_route,
                activity_route,
            )
        for language in record["languages"]:
            language_iri = language_registry[language]
            emit(
                record,
                LANGUAGE_PREDICATE,
                entity_iri,
                language_iri,
                entity_route,
                semantic_route("language", language_iri),
            )
        if is_cpsv_public_service(record, cpsv_mappings):
            jurisdiction = clean_text(record.get("jurisdiction"))
            if jurisdiction:
                jurisdiction_iri = semantic_jurisdiction_iri(
                    publication_base, jurisdiction
                )
                emit(
                    record,
                    SPATIAL_PREDICATE,
                    entity_iri,
                    jurisdiction_iri,
                    entity_route,
                    semantic_route("jurisdiction", jurisdiction_iri),
                )

    hmlr_jurisdiction_iri = urljoin(
        publication_base, "id/jurisdiction/england-and-wales"
    )
    jurisdiction_note = clean_text(source_register["jurisdiction_note"])
    assertions.append(
        normalized_relationship_assertion(
            publication_base,
            source_iri=HMLR_PUBLISHER_IRI,
            predicate_iri=SPATIAL_PREDICATE,
            target_iri=hmlr_jurisdiction_iri,
            source_route=semantic_route("publisher", HMLR_PUBLISHER_IRI),
            target_route=semantic_route("jurisdiction", hmlr_jurisdiction_iri),
            observed_at=clean_text(source_register["observed_at"]),
            evidence_url=HMLR_PUBLISHER_IRI,
            source_artifact=source_register_relative,
            source_sha256=source_register_sha256,
            source_field="jurisdiction_note",
            source_value=jurisdiction_note,
            locator="jurisdiction_note",
            evidence_type="governed-jurisdiction-statement",
            evidence_role="publisher-jurisdiction",
        )
    )
    return assertions


def publisher_relationship_assertions(
    publication_base: str,
    records: list[dict[str, Any]],
    record_bindings: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Project every governed publisher mapping as an evidence-bearing edge."""
    publication_base = publication_base.rstrip("/") + "/"
    publisher_registry_path = ROOT / "source" / "publisher-registry.json"
    registry_sha256 = sha256_file(publisher_registry_path)
    registry_rows = load_json(publisher_registry_path).get("publishers", [])
    registry_by_name = {
        clean_text(row.get("name")): {
            "id": clean_text(row.get("id")),
            "source_artifact": "source/publisher-registry.json",
            "source_sha256": registry_sha256,
            "source_field": f"publishers[{ordinal}]",
            "source_value": row,
        }
        for ordinal, row in enumerate(registry_rows)
        if isinstance(row, dict)
    }
    activity_id = urljoin(
        publication_base,
        "id/activity/publisher-normalisation-" + registry_sha256[:16],
    )
    rule_id = urljoin(
        publication_base,
        "id/rule/governed-publisher-registry-v1",
    )

    assertions: list[dict[str, Any]] = []
    for record in sorted(records, key=lambda item: item["record_id"]):
        record_id = clean_text(record["record_id"])
        source_binding = record_bindings.get(record_id)
        if source_binding is None:
            raise ValueError(f"publisher lacks an exact source-row binding: {record_id}")

        publisher_name = clean_text(record.get("publisher"))
        publisher_iri = clean_text(record.get("publisher_id"))
        registry_binding = registry_by_name.get(publisher_name)
        if (
            registry_binding is None
            or registry_binding.get("id") != publisher_iri
        ):
            raise ValueError(
                "record publisher does not match the governed registry: "
                f"{publisher_name!r}"
        )
        source_iri = semantic_record_iri(publication_base, record)
        official_source_url = semantic_web_iri(record["canonical_source_url"])
        triple = (source_iri, PUBLISHER_PREDICATE, publisher_iri)
        record_value_sha256 = sha256_bytes(
            compact_canonical_json(source_binding["source_value"])
        )
        registry_value_sha256 = sha256_bytes(
            compact_canonical_json(registry_binding["source_value"])
        )
        record_evidence = bind_relationship_evidence(
            publication_base,
            {
                "type": "frozen-source-metadata",
                "url": official_source_url,
                "resource": official_source_url,
                "source_artifact": source_binding["source_artifact"],
                "source_sha256": source_binding["source_sha256"],
                "source_field": source_binding["source_field"],
                "source_value_sha256": record_value_sha256,
                "source_value_hash_canonicalization": (
                    CPSV_SOURCE_VALUE_CANONICALIZATION
                ),
                "locator": source_binding["source_field"],
                "source_locator": record_id,
                "normalization": rule_id,
                "retrieved_at": clean_text(record.get("observed_at")),
            },
            source_iri=source_iri,
            predicate_iri=PUBLISHER_PREDICATE,
            target_iri=publisher_iri,
            role="publisher-source",
            record_id=record_id,
        )
        registry_evidence = bind_relationship_evidence(
            publication_base,
            {
                "type": "governed-identity-registry",
                "url": publisher_iri,
                "resource": publisher_iri,
                "source_artifact": registry_binding["source_artifact"],
                "source_sha256": registry_binding["source_sha256"],
                "source_field": registry_binding["source_field"],
                "source_value_sha256": registry_value_sha256,
                "source_value_hash_canonicalization": (
                    CPSV_SOURCE_VALUE_CANONICALIZATION
                ),
                "locator": registry_binding["source_field"],
                "normalization": rule_id,
                "retrieved_at": clean_text(record.get("observed_at")),
            },
            source_iri=source_iri,
            predicate_iri=PUBLISHER_PREDICATE,
            target_iri=publisher_iri,
            role="publisher-registry",
            record_id=record_id,
        )
        assertions.append(
            {
                "@id": relationship_assertion_id(publication_base, *triple),
                "@type": ["rdf:Statement", "okf:RelationshipAssertion"],
                "source": {"@id": source_iri},
                "predicate": {"@id": PUBLISHER_PREDICATE},
                "target": {"@id": publisher_iri},
                "source_route": (
                    "dataset/" + explorer_name("record", record["record_id"])
                ),
                "target_route": (
                    "publisher/" + explorer_name("publisher", publisher_iri)
                ),
                "kind": "published by",
                "label": "published by",
                "inverse_label": "publishes",
                "assertion_status": "normalized",
                "assertion_scope": "real-world",
                "authority": {
                    "class": "derived",
                    "label": (
                        "Deterministically normalised from frozen source metadata "
                        "and the governed publisher registry"
                    ),
                    "source": official_source_url,
                },
                "derivation": rule_id,
                "derivation_activity": activity_id,
                "rule": rule_id,
                "observed_at": clean_text(record.get("observed_at")),
                "review_status": "implementation-authorised-pending-release-review",
                "evidence": [record_evidence, registry_evidence],
                "rights": {
                    "source": urljoin(publication_base, "data/rights.json"),
                    "assertion": (
                        "Project-authored normalised metadata assertion; the "
                        "frozen source metadata retains its recorded rights and "
                        "exceptions."
                    ),
                },
            }
        )
    return assertions


def competent_authority_relationship_assertions(
    publication_base: str,
    records: list[dict[str, Any]],
    publisher_assertions: list[dict[str, Any]],
    cpsv_mappings: dict[str, Any],
) -> list[dict[str, Any]]:
    """Map evidenced HMLR public services to their CPSV-AP authority."""
    publication_base = publication_base.rstrip("/") + "/"
    publishers_by_source = {
        clean_text(assertion["source"]["@id"]): assertion
        for assertion in publisher_assertions
    }
    rule_id = urljoin(
        publication_base,
        "id/rule/cpsv-ap-3.2.0-competent-authority-v1",
    )
    activity_id = urljoin(
        publication_base,
        "id/activity/cpsv-ap-3.2.0-public-service-projection",
    )
    assertions: list[dict[str, Any]] = []
    for record in sorted(records, key=lambda item: item["record_id"]):
        if not is_cpsv_public_service(record, cpsv_mappings):
            continue
        source_iri = semantic_record_iri(publication_base, record)
        decision = cpsv_mappings["mapped"][clean_text(record["record_id"])]
        publisher_assertion = publishers_by_source.get(source_iri)
        if (
            publisher_assertion is None
            or clean_text(publisher_assertion["target"]["@id"])
            != HMLR_PUBLISHER_IRI
        ):
            raise ValueError(
                "CPSV-AP public-service mapping lacks an evidenced HMLR publisher"
            )
        triple = (
            source_iri,
            COMPETENT_AUTHORITY_PREDICATE,
            HMLR_PUBLISHER_IRI,
        )
        evidence: list[dict[str, Any]] = []
        authority_refs = decision["competent_authority"]["evidence_refs"]
        for evidence_ref in authority_refs:
            governed = cpsv_mappings["evidence_by_id"][evidence_ref]
            evidence.append(
                bind_relationship_evidence(
                    publication_base,
                    {
                        "type": governed["claim_supported"],
                        "url": governed["url"],
                        "resource": governed["resource"],
                        "source_artifact": governed["source_artifact"],
                        "source_sha256": governed["source_sha256"],
                        "source_field": governed["source_field"],
                        "source_value_sha256": governed["source_value_sha256"],
                        "source_value_hash_canonicalization": governed[
                            "source_value_hash_canonicalization"
                        ],
                        "locator": governed["locator"],
                        **(
                            {"source_locator": governed["record_id"]}
                            if clean_text(governed.get("record_id"))
                            else {}
                        ),
                        "normalization": governed["normalization"],
                        "rule_id": rule_id,
                        "rationale": governed["rationale"],
                        "value": clean_text(evidence_ref),
                        "retrieved_at": governed["retrieved_at"],
                    },
                    source_iri=source_iri,
                    predicate_iri=COMPETENT_AUTHORITY_PREDICATE,
                    target_iri=HMLR_PUBLISHER_IRI,
                    role="competent-authority-evidence",
                    record_id=clean_text(record["record_id"]),
                )
            )
        assertions.append(
            {
                "@id": relationship_assertion_id(publication_base, *triple),
                "@type": ["rdf:Statement", "okf:RelationshipAssertion"],
                "source": {"@id": source_iri},
                "predicate": {"@id": COMPETENT_AUTHORITY_PREDICATE},
                "target": {"@id": HMLR_PUBLISHER_IRI},
                "source_route": (
                    "dataset/" + explorer_name("record", record["record_id"])
                ),
                "target_route": (
                    "publisher/"
                    + explorer_name("publisher", HMLR_PUBLISHER_IRI)
                ),
                "kind": "has competent authority",
                "label": "has competent authority",
                "inverse_label": "is competent authority for",
                "assertion_status": "normalized",
                "assertion_scope": "real-world",
                "authority": {
                    "class": "derived",
                    "label": (
                        "Normalised CPSV-AP 3.2.0 mapping from frozen HMLR "
                        "service and publisher metadata"
                    ),
                    "source": HMLR_PUBLISHER_IRI,
                },
                "derivation": rule_id,
                "derivation_activity": activity_id,
                "rule": rule_id,
                "observed_at": clean_text(record["observed_at"]),
                "review_status": (
                    "implementation-authorised-pending-release-review"
                ),
                "evidence": evidence,
                "rights": {
                    "source": urljoin(publication_base, "data/rights.json"),
                    "assertion": (
                        "Project-authored normalised interoperability mapping; "
                        "the official source metadata retains its recorded "
                        "rights and exceptions."
                    ),
                },
            }
        )
    return assertions


def translation_relationship_assertions(
    publication_base: str,
    records: list[dict[str, Any]],
    composite_manifest: dict[str, Any],
) -> list[dict[str, Any]]:
    """Normalise GOV.UK translation metadata into one governed assertion plane."""
    publication_base = publication_base.rstrip("/") + "/"
    observation_input = next(
        (
            row
            for row in composite_manifest["inputs"]
            if row["id"] == "govuk-content-locale-translations"
        ),
        None,
    )
    if observation_input is None:
        raise ValueError("composite input lacks GOV.UK translation observation")
    observation_path = ROOT / observation_input["path"]
    if sha256_file(observation_path) != observation_input["sha256"]:
        raise ValueError("GOV.UK translation observation digest mismatch")
    observation_document = load_json(observation_path)
    observations_by_group: dict[str, tuple[int, dict[str, Any]]] = {}
    for observation_ordinal, observation in enumerate(
        observation_document.get("observations", [])
    ):
        metadata = observation.get("metadata", {})
        group = clean_text(metadata.get("content_id"))
        translations = metadata.get("available_translations")
        if not group or not isinstance(translations, list) or not translations:
            raise ValueError("GOV.UK translation observation lacks governed metadata")
        if group in observations_by_group:
            raise ValueError(f"duplicate GOV.UK translation observation: {group}")
        observations_by_group[group] = (observation_ordinal, observation)

    groups: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        group = clean_text(record.get("translation_group"))
        if group:
            groups.setdefault(group, []).append(record)

    assertions: list[dict[str, Any]] = []
    triples: set[tuple[str, str, str]] = set()
    for group, members in sorted(groups.items()):
        observation_binding = observations_by_group.get(group)
        if observation_binding is None:
            raise ValueError(f"translation group lacks source observation: {group}")
        observation_ordinal, observation = observation_binding
        english_members = [
            record for record in members if "en" in record.get("languages", [])
        ]
        if len(english_members) != 1:
            raise ValueError(
                f"translation group requires exactly one English record: {group}"
            )
        english = english_members[0]
        metadata = observation["metadata"]
        translations = metadata["available_translations"]
        translation_rows: dict[str, dict[str, Any]] = {}
        for translation in translations:
            if not isinstance(translation, dict):
                raise ValueError(
                    f"translation group has a non-object member: {group}"
                )
            normalized = normalize_govuk_content_translation(
                translation,
                clean_text(observation_document["observed_at"]),
            )
            translation_record_id = record_id_for(
                clean_text(normalized["source_family"]),
                clean_text(normalized["source_native_id"]),
            )
            if translation_record_id in translation_rows:
                raise ValueError(
                    f"translation group repeats a locale record: {group}"
                )
            translation_rows[translation_record_id] = translation
        value_sha256 = sha256_bytes(compact_canonical_json(observation))
        source_field = f"observations[{observation_ordinal}]"
        activity_id = urljoin(
            publication_base,
            "id/activity/govuk-content-translation-normalization-"
            + observation_input["sha256"][:16],
        )
        for translated in sorted(members, key=lambda item: item["record_id"]):
            if translated["record_id"] == english["record_id"]:
                continue
            source_iri = semantic_record_iri(publication_base, translated)
            target_iri = semantic_record_iri(publication_base, english)
            triple = (source_iri, TRANSLATION_PREDICATE, target_iri)
            if triple in triples:
                raise ValueError(f"duplicate normalised translation triple: {triple}")
            triples.add(triple)
            source_translation = translation_rows.get(translated["record_id"])
            target_translation = translation_rows.get(english["record_id"])
            if source_translation is None or target_translation is None:
                raise ValueError(
                    f"translation records are not present in their source row: {group}"
                )
            source_locale = clean_text(source_translation.get("locale"))
            target_locale = clean_text(target_translation.get("locale"))
            if source_locale == "en" or target_locale != "en":
                raise ValueError(
                    f"translation direction is not non-English to English: {group}"
                )
            source_route = (
                "dataset/" + explorer_name("record", translated["record_id"])
            )
            target_route = "dataset/" + explorer_name("record", english["record_id"])
            evidence = bind_relationship_evidence(
                publication_base,
                {
                    "type": "official-metadata-observation",
                    "url": clean_text(observation["api_url"]),
                    "resource": source_iri,
                    "source_artifact": observation_input["path"],
                    "source_sha256": observation_input["sha256"],
                    "source_field": source_field,
                    "source_value_sha256": value_sha256,
                    "source_value_hash_canonicalization": (
                        CPSV_SOURCE_VALUE_CANONICALIZATION
                    ),
                    "locator": source_field,
                    "source_locator": translated["record_id"],
                    "value": group,
                    "normalization": urljoin(
                        publication_base,
                        "id/rule/govuk-content-available-translations-v1",
                    ),
                    "retrieved_at": clean_text(
                        observation_document["observed_at"]
                    ),
                },
                source_iri=source_iri,
                predicate_iri=TRANSLATION_PREDICATE,
                target_iri=target_iri,
                role="translation-observation",
                record_id=translated["record_id"],
            )
            assertions.append(
                {
                    "@id": relationship_assertion_id(publication_base, *triple),
                    "@type": ["rdf:Statement", "okf:RelationshipAssertion"],
                    "source": {"@id": source_iri},
                    "predicate": {"@id": TRANSLATION_PREDICATE},
                    "target": {"@id": target_iri},
                    "source_route": source_route,
                    "target_route": target_route,
                    "kind": "translation of",
                    "label": "translation of",
                    "inverse_label": "has translation",
                    "assertion_status": "normalized",
                    "assertion_scope": "real-world",
                    "authority": {
                        "class": "derived",
                        "label": (
                            "Deterministically normalised from official GOV.UK "
                            "Content API available_translations metadata"
                        ),
                        "source": clean_text(observation["api_url"]),
                    },
                    "derivation": urljoin(
                        publication_base,
                        "id/rule/govuk-content-available-translations-v1",
                    ),
                    "derivation_activity": activity_id,
                    "rule": urljoin(
                        publication_base,
                        "id/rule/govuk-content-available-translations-v1",
                    ),
                    "observed_at": clean_text(observation_document["observed_at"]),
                    "evidence": [evidence],
                    "rights": {
                        "source": urljoin(publication_base, "data/rights.json"),
                        "assertion": (
                            "Project-authored normalised metadata assertion; "
                            "the linked GOV.UK source metadata retains its "
                            "recorded rights and exceptions."
                        ),
                    },
                }
            )
    return assertions


def semantic_relationship_assertions(
    publication_base: str,
    records: list[dict[str, Any]],
    composite_manifest: dict[str, Any],
    cpsv_mappings: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build the complete governed relationship plane for navigable entities."""
    record_bindings = frozen_record_source_bindings(records, composite_manifest)
    publisher_assertions = publisher_relationship_assertions(
        publication_base, records, record_bindings
    )
    assertions = [
        *structural_relationship_assertions(
            publication_base, records, cpsv_mappings, record_bindings
        ),
        *publisher_assertions,
        *competent_authority_relationship_assertions(
            publication_base, records, publisher_assertions, cpsv_mappings
        ),
        *translation_relationship_assertions(
            publication_base, records, composite_manifest
        ),
    ]
    assertions.sort(key=lambda item: item["@id"])
    validate_relationship_assertions(assertions)
    validate_relationship_evidence_bindings(
        assertions,
        records=records,
        record_bindings=record_bindings,
        cpsv_mappings=cpsv_mappings,
    )
    return assertions


def runtime_relationship(assertion: dict[str, Any]) -> dict[str, Any]:
    """Project one semantic assertion into the Explorer route plane."""
    return {
        "schema": "okf-relationship-assertion.v2",
        "id": assertion["@id"],
        "source": assertion["source_route"],
        "target": assertion["target_route"],
        "source_iri": assertion["source"]["@id"],
        "target_iri": assertion["target"]["@id"],
        "predicate": assertion["predicate"]["@id"],
        **{
            key: assertion[key]
            for key in (
                "kind",
                "label",
                "inverse_label",
                "assertion_status",
                "assertion_scope",
                "authority",
                "derivation",
                "derivation_activity",
                "rule",
                "observed_at",
                "evidence",
                "rights",
            )
        },
        **{
            key: assertion[key]
            for key in (
                "confidence_score",
                "count",
                "review_status",
                "stale_after",
                "strength",
                "supporting_assertions",
            )
            if key in assertion
        },
    }


def runtime_relationship_as_semantic(row: dict[str, Any]) -> dict[str, Any]:
    """Map one Reader row back to the schema-governed assertion shape."""
    excluded = {
        "schema",
        "id",
        "source",
        "target",
        "source_iri",
        "target_iri",
    }
    mapped = {
        key: value for key, value in row.items() if key not in excluded
    }
    mapped.update(
        {
            "@id": row.get("id"),
            "@type": ["rdf:Statement", "okf:RelationshipAssertion"],
            "source": {"@id": row.get("source_iri")},
            "predicate": {"@id": row.get("predicate")},
            "target": {"@id": row.get("target_iri")},
            "source_route": row.get("source"),
            "target_route": row.get("target"),
        }
    )
    return mapped


def validate_semantic_relationship_planes(
    semantic_document: dict[str, Any],
    runtime_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Validate the emitted semantic/runtime planes and their exact parity."""
    validator, schema_binding = load_pinned_semantic_assertion_schema()
    graph = semantic_document.get("@graph")
    if not isinstance(graph, list):
        raise ValueError("semantic document lacks an @graph array")
    semantic_rows = [
        node
        for node in graph
        if isinstance(node, dict)
        and "okf:RelationshipAssertion"
        in (
            node.get("@type", [])
            if isinstance(node.get("@type", []), list)
            else [node.get("@type")]
        )
    ]
    mapped_runtime_rows = [
        runtime_relationship_as_semantic(row) for row in runtime_rows
    ]
    for plane, rows in (
        ("semantic", semantic_rows),
        ("runtime-mapped", mapped_runtime_rows),
    ):
        identifiers: set[str] = set()
        for ordinal, row in enumerate(rows):
            identifier = clean_text(row.get("@id"))
            if identifier in identifiers:
                raise ValueError(
                    f"duplicate {plane} relationship assertion ID: {identifier}"
                )
            identifiers.add(identifier)
            failures = sorted(
                validator.iter_errors(row),
                key=lambda error: "/".join(
                    str(part) for part in error.absolute_path
                ),
            )
            if failures:
                detail = "; ".join(
                    f"{error.json_path}: {error.message}"
                    for error in failures[:8]
                )
                raise ValueError(
                    f"{plane} assertion {identifier or ordinal!r} failed the "
                    f"pinned semantic schema: {detail}"
                )

    def assertion_core(row: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in row.items()
            if key not in {"rdf:subject", "rdf:predicate", "rdf:object"}
        }

    semantic_by_id = {
        clean_text(row.get("@id")): assertion_core(row)
        for row in semantic_rows
    }
    runtime_by_id = {
        clean_text(row.get("@id")): row for row in mapped_runtime_rows
    }
    if semantic_by_id != runtime_by_id:
        raise ValueError(
            "semantic and runtime-mapped relationship assertion sets differ"
        )

    def triple(row: dict[str, Any]) -> tuple[str, str, str]:
        return tuple(
            clean_text(row[field].get("@id"))
            for field in ("source", "predicate", "target")
        )  # type: ignore[return-value]

    semantic_triples = {triple(row) for row in semantic_rows}
    runtime_triples = {triple(row) for row in mapped_runtime_rows}
    nodes_by_id = {
        clean_text(node.get("@id")): node
        for node in graph
        if isinstance(node, dict) and clean_text(node.get("@id"))
    }
    assertion_predicates = {item[1] for item in semantic_triples}
    if assertion_predicates != GOVERNED_RELATIONSHIP_PREDICATES:
        raise ValueError(
            "semantic assertion predicates and the governed contract differ: "
            f"{sorted(assertion_predicates ^ GOVERNED_RELATIONSHIP_PREDICATES)}"
        )
    governed_keys = {
        **{iri: iri for iri in GOVERNED_RELATIONSHIP_PREDICATES},
        **GOVERNED_COMPACT_PREDICATES,
    }
    direct_occurrences: Counter[tuple[str, str, str]] = Counter()
    for source_iri, node in nodes_by_id.items():
        for predicate_key, predicate_iri in governed_keys.items():
            raw_targets = node.get(predicate_key)
            if raw_targets is None:
                continue
            if predicate_key != predicate_iri:
                raise ValueError(
                    "governed direct relationship was pre-injected with a "
                    f"compact predicate key: {predicate_key}"
                )
            targets = raw_targets if isinstance(raw_targets, list) else [raw_targets]
            for target in targets:
                if isinstance(target, dict) and clean_text(target.get("@id")):
                    target_iri = clean_text(target.get("@id"))
                    if target_iri not in nodes_by_id:
                        raise ValueError(
                            "governed direct relationship target lacks a node: "
                            f"{source_iri} -> {target_iri}"
                        )
                    direct_occurrences[
                        (source_iri, predicate_iri, target_iri)
                    ] += 1
    duplicate_direct = [
        triple for triple, count in direct_occurrences.items() if count != 1
    ]
    if duplicate_direct:
        raise ValueError(
            "governed direct relationship triple was emitted more than once: "
            f"{duplicate_direct[0]}"
        )
    direct_triples = set(direct_occurrences)
    for row in semantic_rows:
        source_iri, _predicate_iri, target_iri = triple(row)
        source_node = nodes_by_id.get(source_iri)
        target_node = nodes_by_id.get(target_iri)
        if source_node is None or target_node is None:
            raise ValueError(
                "semantic assertion endpoint lacks a graph node: "
                f"{source_iri} -> {target_iri}"
            )
        if (
            clean_text(source_node.get("route"))
            != clean_text(row.get("source_route"))
            or clean_text(target_node.get("route"))
            != clean_text(row.get("target_route"))
        ):
            raise ValueError(
                "semantic assertion route differs from its endpoint node: "
                f"{source_iri} -> {target_iri}"
            )
    if not (
        semantic_triples == runtime_triples == direct_triples
        and len(semantic_rows) == len(runtime_rows)
    ):
        raise ValueError(
            "direct, reified and runtime semantic relationship planes differ"
        )

    def set_digest(values: Iterable[Any]) -> str:
        payload = json.dumps(
            sorted(values),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return sha256_bytes(payload)

    identity_digest = set_digest(semantic_by_id)
    triple_digest = set_digest(semantic_triples)
    evidence_rows = [
        evidence
        for row in semantic_rows
        for evidence in row.get("evidence", [])
        if isinstance(evidence, dict)
    ]
    evidence_ids = [clean_text(row.get("@id")) for row in evidence_rows]
    if any(not identifier for identifier in evidence_ids) or len(evidence_ids) != len(
        set(evidence_ids)
    ):
        raise ValueError("emitted relationship evidence IDs are absent or duplicated")
    evidence_identity_digest = set_digest(evidence_ids)
    evidence_row_digest = set_digest(
        compact_canonical_json(row).decode("utf-8") for row in evidence_rows
    )
    return {
        "schema": "okf-hmlr-semantic-assertion-validation.v1",
        "status": "conformant",
        "schema_binding": schema_binding,
        "counts": {
            "semantic_assertions_validated": len(semantic_rows),
            "runtime_rows_mapped_and_validated": len(mapped_runtime_rows),
            "direct_triples_reconciled": len(direct_triples),
            "evidence_rows_validated": len(evidence_rows),
            "validation_failures": 0,
        },
        "parity": {
            "direct_reified_runtime": True,
            "identity": True,
            "routes": True,
        },
        "assertion_identity_set_sha256": identity_digest,
        "triple_set_sha256": triple_digest,
        "evidence_identity_set_sha256": evidence_identity_digest,
        "evidence_row_set_sha256": evidence_row_digest,
    }


def validate_semantic_contract_metrics(
    semantic_validation: dict[str, Any],
    semantic_contract: dict[str, Any] | None = None,
) -> dict[str, int]:
    """Require the authored semantic contract to match computed build metrics."""

    contract = (
        semantic_contract
        if semantic_contract is not None
        else load_json(ROOT / "okf.semantic.json")
    )
    try:
        expected = contract["semantic_layer"]["candidate_metrics"]
        counts = semantic_validation["counts"]
        coverage = semantic_validation["coverage"]
        runtime = semantic_validation["rich_relationship_runtime"]
    except (KeyError, TypeError) as exc:
        raise ValueError(
            "semantic contract or validation receipt lacks candidate metrics"
        ) from exc
    actual = {
        "direct_triples": counts["direct_triples_reconciled"],
        "governed_predicates": coverage["governed_predicates"],
        "reified_assertions": counts["semantic_assertions_validated"],
        "relationship_chunks": runtime["chunks"],
        "route_bearing_semantic_identities": coverage[
            "route_bearing_semantic_identities"
        ],
        "route_locator_buckets": runtime["buckets"],
        "runtime_rows": counts["runtime_rows_mapped_and_validated"],
    }
    if expected != actual:
        raise ValueError(
            "okf.semantic.json candidate metrics differ from the computed "
            f"semantic build: expected {expected!r}, computed {actual!r}"
        )
    return actual


def validate_cpsv_ap_projection(
    semantic_document: dict[str, Any],
    records: list[dict[str, Any]],
    relationship_assertions: list[dict[str, Any]],
    cpsv_mappings: dict[str, Any],
    vendor_receipt: dict[str, Any],
    publication_base: str,
) -> dict[str, Any]:
    """Validate the explicit, evidence-bounded CPSV-AP service projection."""
    graph = semantic_document.get("@graph")
    if not isinstance(graph, list):
        raise ValueError("semantic document lacks an @graph array")
    nodes = {
        clean_text(node.get("@id")): node
        for node in graph
        if isinstance(node, dict) and clean_text(node.get("@id"))
    }

    def node_types(node: dict[str, Any]) -> set[str]:
        value = node.get("@type")
        values = value if isinstance(value, list) else [value]
        return {clean_text(item) for item in values if clean_text(item)}

    expected_records = [
        record
        for record in records
        if is_cpsv_public_service(record, cpsv_mappings)
    ]
    expected_iris = {
        semantic_record_iri(publication_base, record)
        for record in expected_records
    }
    actual_iris = {
        identifier
        for identifier, node in nodes.items()
        if "cpsv:PublicService" in node_types(node)
    }
    if not expected_iris or actual_iris != expected_iris:
        raise ValueError(
            "CPSV-AP public-service selection differs from the governed mapping"
        )

    authority_assertions = {
        clean_text(assertion["source"]["@id"]): assertion
        for assertion in relationship_assertions
        if clean_text(assertion["predicate"]["@id"])
        == COMPETENT_AUTHORITY_PREDICATE
    }
    if set(authority_assertions) != expected_iris:
        raise ValueError(
            "CPSV-AP public services and competent-authority assertions differ"
        )
    for identifier in sorted(expected_iris):
        node = nodes[identifier]
        for field in (
            "dcterms:identifier",
            "dcterms:title",
            "dcterms:description",
        ):
            if not node.get(field):
                raise ValueError(
                    f"CPSV-AP public service lacks mandatory {field}: {identifier}"
                )
        for field in ("dcterms:title", "dcterms:description"):
            value = node[field]
            if not (
                isinstance(value, dict)
                and clean_text(value.get("@value"))
                and clean_text(value.get("@language")) == "en-GB"
            ):
                raise ValueError(
                    f"CPSV-AP public service lacks en-GB {field}: {identifier}"
                )
        if node.get(COMPETENT_AUTHORITY_PREDICATE) != {
            "@id": HMLR_PUBLISHER_IRI
        }:
            raise ValueError(
                "CPSV-AP public service lacks its direct competent-authority triple"
            )
        if clean_text(
            authority_assertions[identifier]["target"]["@id"]
        ) != HMLR_PUBLISHER_IRI:
            raise ValueError("CPSV-AP competent authority is not HM Land Registry")

    organisation = nodes.get(HMLR_PUBLISHER_IRI)
    if organisation is None or "cv:PublicOrganisation" not in node_types(
        organisation
    ):
        raise ValueError("HM Land Registry lacks the CPSV public-organisation type")
    if not organisation.get("skos:prefLabel") or not organisation.get(
        SPATIAL_PREDICATE
    ):
        raise ValueError(
            "HM Land Registry lacks CPSV public-organisation mandatory properties"
        )
    spatial_id = clean_text(organisation[SPATIAL_PREDICATE].get("@id"))
    spatial_node = nodes.get(spatial_id)
    if spatial_node is None or (
        "http://data.europa.eu/88u/dataset/atu-type"
        not in node_types(spatial_node)
    ):
        raise ValueError(
            "HM Land Registry spatial coverage lacks its bounded ATU projection"
        )

    excluded = [
        {
            "record_id": clean_text(record["record_id"]),
            "record_type": clean_text(record["source_native_type"]),
            "decision": cpsv_mappings["decisions_by_record"][
                clean_text(record["record_id"])
            ]["decision"],
            "reason": cpsv_mappings["decisions_by_record"][
                clean_text(record["record_id"])
            ]["rationale"],
            "evidence_refs": cpsv_mappings["decisions_by_record"][
                clean_text(record["record_id"])
            ]["evidence_refs"],
        }
        for record in records
        if record["kind"] == "service"
        and not is_cpsv_public_service(record, cpsv_mappings)
    ]
    return {
        "schema": "okf-hmlr-cpsv-ap-projection-validation.v1",
        "status": "passed",
        "standard": {
            "name": "Core Public Service Vocabulary Application Profile",
            "version": CPSV_AP_VERSION,
            "specification": (
                "https://semiceu.github.io/CPSV-AP/releases/3.2.0/"
            ),
            "vendored_identity_sha256": vendor_receipt["identity_sha256"],
        },
        "mapping": cpsv_mappings["receipt"],
        "mapping_scope": (
            "Selective metadata-level interoperability mapping for genuine "
            "HMLR public services; it is not a claim that every bundle entity "
            "is a public service or that the bundle is a complete ontology."
        ),
        "counts": {
            "public_services": len(expected_iris),
            "competent_authority_assertions": len(authority_assertions),
            "public_organisations": 1,
            "excluded_service_kind_records": len(excluded),
            "validation_failures": 0,
        },
        "mandatory_projection_checks": {
            "identifier": "passed",
            "name": "passed",
            "description": "passed",
            "competent_authority": "passed",
            "public_organisation_preferred_label": "passed",
            "public_organisation_spatial": "passed",
        },
        "official_shacl_execution": {
            "status": "not-run",
            "reason": (
                "The official CPSV-AP 3.2.0 SHACL file is vendored and "
                "digest-bound, but this build performs an explicit bounded "
                "projection check rather than claiming general SHACL conformance."
            ),
        },
        "excluded_records": excluded,
        "endorsement": "No EU, SEMIC or HM Land Registry endorsement is claimed.",
    }


def require_profile_conformance(
    value: dict[str, Any], schema_name: str, label: str
) -> None:
    """Validate one generated semantic control against the vendored profile."""
    validator = load_profile_validator(schema_name)
    failures = sorted(
        validator.iter_errors(value),
        key=lambda error: "/".join(str(part) for part in error.absolute_path),
    )
    if failures:
        detail = "; ".join(
            f"{error.json_path}: {error.message}" for error in failures[:12]
        )
        raise ValueError(f"{label} failed the pinned profile schema: {detail}")


def semantic_iri_route_registry(
    semantic_document: dict[str, Any], snapshot_id: str
) -> dict[str, Any]:
    """Map every route-bearing semantic identity to its explicit Explorer route."""
    graph = semantic_document.get("@graph")
    if not isinstance(graph, list):
        raise ValueError("semantic document lacks an @graph array")
    entries: list[dict[str, Any]] = []
    identities: set[str] = set()
    for node in graph:
        if not isinstance(node, dict) or not clean_text(node.get("route")):
            continue
        identity = clean_text(node.get("@id"))
        if not identity or identity in identities:
            raise ValueError(
                f"route-bearing semantic identity is missing or duplicated: {identity!r}"
            )
        identities.add(identity)
        raw_type = node.get("@type")
        node_types = raw_type if isinstance(raw_type, list) else [raw_type]
        title = clean_text(
            node.get("schema:name") or node.get("dcterms:identifier") or identity
        )
        entries.append(
            {
                "iri": identity,
                "route": clean_text(node["route"]),
                "kind": ", ".join(
                    sorted(clean_text(item) for item in node_types if clean_text(item))
                )
                or "semantic entity",
                "title": title,
            }
        )
    entries.sort(key=lambda item: (item["iri"], item["route"]))
    registry = {
        "schema": "okf-iri-route-registry.v1",
        "snapshot": snapshot_id,
        "entries": entries,
        "counts": {"entries": len(entries)},
        "root_sha256": sha256_bytes(canonical_json(entries)),
    }
    require_profile_conformance(
        registry,
        "iri-route-registry.schema.json",
        "semantic IRI-route registry",
    )
    return registry


def semantic_predicate_registry(
    relationship_assertions: list[dict[str, Any]],
    snapshot_id: str,
    generated_at: str,
) -> dict[str, Any]:
    """Describe every governed predicate emitted in the rich relationship plane."""
    rdfs_resource = "http://www.w3.org/2000/01/rdf-schema#Resource"
    specs = {
        CATALOGUE_RECORD_PREDICATE: (
            "Links the catalogue to every generated DCAT catalogue record.",
            ["http://www.w3.org/ns/dcat#Catalog"],
            ["http://www.w3.org/ns/dcat#CatalogRecord"],
            "http://www.w3.org/ns/dcat#",
            "DCAT 3",
        ),
        CATALOGUE_RESOURCE_PREDICATE: (
            "Links the catalogue to every governed discovery resource.",
            ["http://www.w3.org/ns/dcat#Catalog"],
            [rdfs_resource],
            "http://www.w3.org/ns/dcat#",
            "DCAT 3",
        ),
        CATALOGUE_DATASET_PREDICATE: (
            "Links the catalogue to resources explicitly classified as datasets.",
            ["http://www.w3.org/ns/dcat#Catalog"],
            ["http://www.w3.org/ns/dcat#Dataset"],
            "http://www.w3.org/ns/dcat#",
            "DCAT 3",
        ),
        PRIMARY_TOPIC_PREDICATE: (
            "Links each catalogue record to the resource that it describes.",
            ["http://www.w3.org/ns/dcat#CatalogRecord"],
            [rdfs_resource],
            "http://xmlns.com/foaf/0.1/",
            "living vocabulary bounded by this v0.3.0 registry",
        ),
        SOURCE_PREDICATE: (
            "Links a catalogue record to a local route-bearing source resource.",
            ["http://www.w3.org/ns/dcat#CatalogRecord"],
            ["http://www.w3.org/ns/prov#Entity"],
            "http://purl.org/dc/terms/",
            "DCMI Metadata Terms bounded by this v0.3.0 registry",
        ),
        DERIVED_FROM_PREDICATE: (
            "Links a discovery resource to each local source representation from which it was derived.",
            [rdfs_resource],
            ["http://www.w3.org/ns/prov#Entity"],
            "http://www.w3.org/ns/prov#",
            "PROV-O",
        ),
        RIGHTS_PREDICATE: (
            "Links catalogue records and resources to their governed rights assessment.",
            [rdfs_resource],
            ["http://purl.org/dc/terms/RightsStatement"],
            "http://purl.org/dc/terms/",
            "DCMI Metadata Terms bounded by this v0.3.0 registry",
        ),
        GENERATED_BY_PREDICATE: (
            "Links catalogue records and resources to their bounded observation activity.",
            [rdfs_resource],
            ["http://www.w3.org/ns/prov#Activity"],
            "http://www.w3.org/ns/prov#",
            "PROV-O",
        ),
        LANGUAGE_PREDICATE: (
            "Links a resource to each explicitly recorded language authority identity.",
            [rdfs_resource],
            ["http://purl.org/dc/terms/LinguisticSystem"],
            "http://purl.org/dc/terms/",
            "DCMI Metadata Terms bounded by this v0.3.0 registry",
        ),
        PUBLISHER_PREDICATE: (
            "Links a discovery resource to its governed publisher identity.",
            [rdfs_resource],
            ["http://xmlns.com/foaf/0.1/Agent"],
            "http://purl.org/dc/terms/",
            "DCMI Metadata Terms bounded by this v0.3.0 registry",
        ),
        TRANSLATION_PREDICATE: (
            "Links a translated GOV.UK work to its evidenced English work.",
            ["https://schema.org/CreativeWork"],
            ["https://schema.org/CreativeWork"],
            "https://schema.org/",
            "living vocabulary bounded by this v0.3.0 registry",
        ),
        COMPETENT_AUTHORITY_PREDICATE: (
            "Links each reviewed CPSV public service to its evidenced competent authority.",
            ["http://purl.org/vocab/cpsv#PublicService"],
            ["http://data.europa.eu/m8g/PublicOrganisation"],
            "https://semiceu.github.io/CPSV-AP/releases/3.2.0/",
            CPSV_AP_VERSION,
        ),
        SPATIAL_PREDICATE: (
            "Links a reviewed public service or organisation to explicit spatial coverage.",
            [rdfs_resource],
            ["http://purl.org/dc/terms/Location"],
            "http://purl.org/dc/terms/",
            "DCMI Metadata Terms bounded by this v0.3.0 registry",
        ),
    }
    definitions: dict[str, dict[str, Any]] = {}
    for iri, (description, domain, range_, vocabulary, version) in specs.items():
        preferred_label, inverse_label = relationship_labels(iri)
        definitions[iri] = {
            "iri": iri,
            "preferred_label": preferred_label,
            "inverse_label": inverse_label,
            "description": description,
            "domain": domain,
            "range": range_,
            "assertion_statuses": ["normalized"],
            "evidence_policy": {
                "minimum_fields": [
                    "source_artifact",
                    "source_field",
                    "source_sha256",
                    "source_value_sha256",
                ],
                "notes": (
                    "Requires a digest-bound governed input and deterministic "
                    "field-value binding."
                ),
            },
            "source_vocabulary": {"iri": vocabulary, "version": version},
            "status": "active",
        }
    if set(definitions) != GOVERNED_RELATIONSHIP_PREDICATES:
        raise ValueError("governed predicate definitions are incomplete")
    emitted = {
        clean_text(assertion.get("predicate", {}).get("@id"))
        for assertion in relationship_assertions
    }
    if emitted != set(definitions):
        raise ValueError(
            "emitted semantic predicates and the governed registry differ: "
            f"{sorted(emitted ^ set(definitions))}"
        )
    predicates = [definitions[iri] for iri in sorted(definitions)]
    registry = {
        "schema": "okf-predicate-registry.v1",
        "snapshot": snapshot_id,
        "generated_at": generated_at,
        "predicates": predicates,
        "counts": {"predicates": len(predicates)},
        "root_sha256": sha256_bytes(canonical_json(predicates)),
    }
    require_profile_conformance(
        registry,
        "predicate-registry.schema.json",
        "semantic predicate registry",
    )
    return registry


def semantic_resource_reference(
    output: Path, path: Path, media_type: str
) -> dict[str, str]:
    return {
        "path": path.relative_to(output).as_posix(),
        "sha256": sha256_file(path),
        "media_type": media_type,
    }


def semantic_model_descriptor(
    output: Path,
    publication_base: str,
    local_context_path: Path,
    canonical_context_path: Path,
    iri_registry_path: Path,
    predicate_registry_path: Path,
    shape_path: Path,
    cpsv_context_path: Path,
    cpsv_vocabulary_path: Path,
    cpsv_shape_path: Path,
) -> dict[str, Any]:
    """Describe the bounded semantic inputs without claiming browser inference."""
    model = {
        "schema": "okf-semantic-model.v1",
        "status": "experimental",
        "contexts": [
            {
                "url": publication_base.rstrip("/") + "/context.jsonld",
                **semantic_resource_reference(
                    output, local_context_path, "application/ld+json"
                ),
            },
            {
                "url": SEMANTIC_CONTEXT_URL,
                **semantic_resource_reference(
                    output, canonical_context_path, "application/ld+json"
                ),
            },
            {
                "url": CPSV_AP_CONTEXT_URL,
                **semantic_resource_reference(
                    output, cpsv_context_path, "application/ld+json"
                ),
            },
        ],
        "id_registry": semantic_resource_reference(
            output, iri_registry_path, "application/json"
        ),
        "predicate_registry": semantic_resource_reference(
            output, predicate_registry_path, "application/json"
        ),
        "vocabularies": [
            {
                "id": CPSV_AP_VOCABULARY_URL,
                "version": CPSV_AP_VERSION,
                **semantic_resource_reference(
                    output, cpsv_vocabulary_path, "text/turtle"
                ),
            }
        ],
        "shapes": [
            {
                "id": BUNDLE_PROFILE_URL + "shapes.ttl",
                "version": "0.6.0",
                **semantic_resource_reference(output, shape_path, "text/turtle"),
            },
            {
                "id": CPSV_AP_SHACL_URL,
                "version": CPSV_AP_VERSION,
                **semantic_resource_reference(
                    output, cpsv_shape_path, "text/turtle"
                ),
            },
        ],
        "inference": {
            "status": "not-run",
            "profile": (
                "Explicit governed assertions only; no materialised or "
                "unbounded browser inference."
            ),
        },
    }
    require_profile_conformance(
        model,
        "semantic-model.schema.json",
        "semantic-model descriptor",
    )
    return model


def validate_relationship_assertions(assertions: list[dict[str, Any]]) -> None:
    """Fail closed when an assertion cannot support graph and Reader projections."""
    required = {
        "@id",
        "@type",
        "source",
        "predicate",
        "target",
        "source_route",
        "target_route",
        "kind",
        "label",
        "inverse_label",
        "assertion_status",
        "assertion_scope",
        "authority",
        "derivation",
        "observed_at",
        "evidence",
        "rights",
    }
    identifiers: set[str] = set()
    evidence_identifiers: set[str] = set()
    triples: set[tuple[str, str, str]] = set()

    def require_absolute_iri(value: Any, field: str) -> str:
        iri = clean_text(value.get("@id") if isinstance(value, dict) else value)
        parsed = urlparse(iri)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"relationship {field} is not an absolute IRI: {iri}")
        if parsed.username or parsed.password:
            raise ValueError(f"relationship {field} contains credentials")
        return iri

    def require_canonical_web_url(value: Any, field: str) -> str:
        iri = require_absolute_iri(value, field)
        try:
            canonical = canonical_https_url(
                iri,
                field=f"relationship {field}",
            )
        except ValueError as error:
            raise ValueError(
                f"relationship {field} is not a credential-free canonical "
                "HTTPS URL"
            ) from error
        if canonical != iri:
            raise ValueError(
                f"relationship {field} is not a canonical HTTPS URL: {iri}"
            )
        return iri

    for assertion in assertions:
        missing = sorted(required - set(assertion))
        if missing:
            raise ValueError(
                "relationship assertion lacks required fields: " + ", ".join(missing)
            )
        identifier = require_absolute_iri(assertion["@id"], "@id")
        if identifier in identifiers:
            raise ValueError(f"duplicate relationship assertion ID: {identifier}")
        identifiers.add(identifier)
        triple = (
            require_absolute_iri(assertion["source"], "source"),
            require_absolute_iri(assertion["predicate"], "predicate"),
            require_absolute_iri(assertion["target"], "target"),
        )
        if triple in triples:
            raise ValueError(f"duplicate relationship assertion triple: {triple}")
        triples.add(triple)
        publication_base = relationship_publication_base(identifier)
        if identifier != relationship_assertion_id(publication_base, *triple):
            raise ValueError(
                "relationship assertion ID is not derived from its triple: "
                + identifier
            )
        for route_field in ("source_route", "target_route"):
            route = clean_text(assertion[route_field])
            parsed_route = urlparse(route)
            if (
                not route
                or route.startswith("/")
                or parsed_route.scheme
                or parsed_route.netloc
                or parsed_route.query
                or parsed_route.fragment
                or ".." in route.split("/")
            ):
                raise ValueError(
                    f"relationship {route_field} is not a safe local route: {route}"
                )
        status = assertion["assertion_status"]
        scope = assertion["assertion_scope"]
        authority = assertion["authority"]
        expected_authority = {
            "official": "official",
            "normalized": "derived",
            "inferred": "derived",
            "model-derived": "model-assisted",
        }
        if status not in expected_authority or scope not in {
            "real-world",
            "synthetic-fixture",
        }:
            raise ValueError("relationship assertion status or scope is unsupported")
        authority_class = clean_text(authority.get("class"))
        required_class = (
            "synthetic"
            if scope == "synthetic-fixture"
            else expected_authority[status]
        )
        if authority_class != required_class:
            raise ValueError("relationship assertion authority/status conflict")
        require_canonical_web_url(authority.get("source"), "authority.source")
        require_absolute_iri(assertion["derivation"], "derivation")
        if not clean_text(assertion.get("observed_at")):
            raise ValueError("relationship assertion lacks observation time")
        evidence = assertion["evidence"]
        if not isinstance(evidence, list) or not evidence:
            raise ValueError("relationship assertion lacks evidence")
        for item in evidence:
            evidence_identifier = require_absolute_iri(
                item.get("@id"), "evidence.@id"
            )
            if evidence_identifier in evidence_identifiers:
                raise ValueError(
                    f"duplicate relationship evidence ID: {evidence_identifier}"
                )
            evidence_identifiers.add(evidence_identifier)
            if evidence_identifier != relationship_evidence_id(
                publication_base,
                item,
                source_iri=triple[0],
                predicate_iri=triple[1],
                target_iri=triple[2],
            ):
                raise ValueError(
                    "relationship evidence ID is not deterministically derived"
                )
            require_canonical_web_url(item.get("url"), "evidence.url")
            require_canonical_web_url(item.get("resource"), "evidence.resource")
            for digest_field in ("source_sha256", "source_value_sha256"):
                if not re.fullmatch(r"[0-9a-f]{64}", clean_text(item.get(digest_field))):
                    raise ValueError(
                        f"relationship evidence lacks a valid {digest_field}"
                    )
            if not clean_text(item.get("source_field")) or not clean_text(
                item.get("retrieved_at")
            ):
                raise ValueError("relationship evidence lacks field provenance")
        rights = assertion["rights"]
        require_canonical_web_url(rights.get("source"), "rights.source")
        if not clean_text(rights.get("assertion")):
            raise ValueError("relationship assertion lacks a rights statement")


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


def compact_json_without_newline(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def write_gzip_json(path: Path, value: Any) -> None:
    """Write deterministic gzip-compressed compact JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(deterministic_gzip_bytes(compact_json_without_newline(value)))


def rich_runtime_relationship(
    relationship: dict[str, Any], plane_id: str
) -> dict[str, Any]:
    """Project one governed relationship into Explorer's bounded rich row."""
    assertion_id = relationship["id"]
    if not isinstance(assertion_id, str) or not assertion_id.isascii():
        raise ValueError(
            "rich relationship runtime assertion identifiers must be ASCII"
        )
    evidence_rows: list[dict[str, Any]] = []
    for evidence in relationship["evidence"]:
        projected_evidence = {
            field: copy.deepcopy(evidence[field])
            for field in (
                "@id",
                "type",
                "url",
                "source_field",
                "source_value_sha256",
                "retrieved_at",
            )
        }
        # Retain small, non-redundant explanatory fields. The authoritative
        # semantic graph retains the complete evidence envelope; the browser
        # row deliberately omits repeated artefact paths, digests, locators and
        # normalisation IRIs so full default-plane hydration remains bounded.
        for field in ("field_provenance", "rationale", "rule_id", "value"):
            if field in evidence:
                projected_evidence[field] = copy.deepcopy(evidence[field])
        if evidence.get("resource") != evidence["url"]:
            if "resource" in evidence:
                projected_evidence["resource"] = evidence["resource"]
        evidence_rows.append(projected_evidence)

    authority = copy.deepcopy(relationship["authority"])
    authority["label"] = {
        "official": "Official source assertion",
        "derived": "Deterministically normalised assertion",
        "model-assisted": "Model-assisted assertion",
        "synthetic": "Synthetic test assertion",
        "unclassified": "Unclassified assertion",
    }[authority["class"]]
    row = {
        "schema": "okf-relationship-runtime-row.v1",
        "id": assertion_id,
        "assertion_id": assertion_id,
        "source": relationship["source"],
        "target": relationship["target"],
        "source_route": relationship["source"],
        "target_route": relationship["target"],
        "source_iri": relationship["source_iri"],
        "target_iri": relationship["target_iri"],
        "predicate": relationship["predicate"],
        "predicate_iri": relationship["predicate"],
        "kind": relationship["kind"],
        "label": relationship["label"],
        "inverse_label": relationship["inverse_label"],
        "direction": "source-to-target",
        "assertion_status": relationship["assertion_status"],
        "assertion_scope": relationship["assertion_scope"],
        "authority": authority,
        "derivation": relationship["derivation"],
        "observed_at": relationship["observed_at"],
        "evidence": evidence_rows,
        "rights": {
            "source": relationship["rights"]["source"],
            "assertion": "Source rights and exceptions apply.",
        },
        "plane": plane_id,
        "active": True,
    }
    for field in (
        "confidence",
        "confidence_score",
        "count",
        "freshness",
        "official_legal_classification",
        "review_status",
        "stale_after",
        "strength",
        "support_profile",
        "supporting_assertions",
    ):
        if field in relationship:
            row[field] = copy.deepcopy(relationship[field])
    if relationship["assertion_status"] in {"inferred", "model-derived"}:
        if "derivation_activity" in relationship:
            row["derivation_activity"] = relationship["derivation_activity"]
    if relationship["assertion_status"] == "inferred" and "rule" in relationship:
        row["rule"] = relationship["rule"]
    return row


def order_rich_runtime_rows_for_route_locality(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep high-degree endpoint rows together without changing identities.

    Each row is anchored to its endpoint with the highest corpus-wide incident
    degree (then the code-point-smallest route on a tie). Sorting by that anchor
    keeps hub rows local while the assertion ID provides a stable final key.
    """
    route_degrees: Counter[str] = Counter()
    for row in rows:
        route_degrees.update({row["source"], row["target"]})

    def locality_key(row: dict[str, Any]) -> tuple[str, str]:
        endpoints = sorted({row["source"], row["target"]})
        anchor = min(
            endpoints,
            key=lambda route: (-route_degrees[route], route),
        )
        return anchor, row["id"]

    return sorted(rows, key=locality_key)


def utf16_text_units(value: str) -> int:
    """Return JavaScript String.length units for a valid JSON string."""
    return len(value.encode("utf-16-le")) // 2


def retained_text_units(value: Any) -> int:
    """Mirror Explorer's recursive retainedTextUnits projection helper."""
    if isinstance(value, str):
        return utf16_text_units(value)
    if isinstance(value, list):
        return sum(retained_text_units(item) for item in value)
    if isinstance(value, dict):
        return sum(retained_text_units(item) for item in value.values())
    return 0


def rich_runtime_reader_projection(
    row: dict[str, Any], lifecycle: str = "active"
) -> dict[str, Any]:
    """Project the strings retained by Explorer v0.6.0 for limit checks."""
    projected = copy.deepcopy(row)
    projected["lifecycle"] = lifecycle
    status = row["assertion_status"]
    if status != "inferred":
        projected.pop("rule", None)
        projected.pop("supporting_assertions", None)
    if status not in {"inferred", "model-derived"}:
        projected.pop("derivation_activity", None)
        projected.pop("confidence_score", None)
    if status != "model-derived":
        projected.pop("review_status", None)
    for evidence_ordinal, evidence in enumerate(projected["evidence"]):
        if "source_value" in evidence and not isinstance(
            evidence["source_value"], str
        ):
            raise ValueError(
                "rich runtime row evidence source_value must be text for "
                "Explorer v0.6.0 projection: "
                f"{row['id']} evidence {evidence_ordinal}"
            )
    return projected


def require_locked_maximum(
    measured: int,
    limit_name: str,
    limits: dict[str, int],
    subject: str,
) -> None:
    maximum = limits[limit_name]
    if measured > maximum:
        raise ValueError(
            f"{subject} is {measured}, exceeding locked Explorer v0.6.0 "
            f"{limit_name}={maximum}"
        )


def validate_rich_relationship_runtime_limits(
    rows: list[dict[str, Any]],
    chunk_measurements: list[dict[str, Any]],
    locator_bucket_measurements: list[dict[str, Any]],
    incident: dict[str, dict[str, set[str]]],
    limits: dict[str, int],
) -> dict[str, Any]:
    """Reconcile every statically applicable pinned rich-runtime ceiling."""
    require_locked_maximum(
        len(rows),
        "maximum_relationship_rows",
        limits,
        "rich relationship runtime row total",
    )
    require_locked_maximum(
        len(chunk_measurements),
        "maximum_rich_relationship_chunks",
        limits,
        "rich relationship runtime chunk total",
    )
    require_locked_maximum(
        1,
        "maximum_rich_relationship_planes",
        limits,
        "rich relationship runtime plane total",
    )

    row_retained_maximum = 0
    evidence_maximum = 0
    supporting_assertions_maximum = 0
    for row in rows:
        row_retained = retained_text_units(rich_runtime_reader_projection(row))
        evidence_items = len(row["evidence"])
        supporting_assertions = len(row.get("supporting_assertions", []))
        row_retained_maximum = max(row_retained_maximum, row_retained)
        evidence_maximum = max(evidence_maximum, evidence_items)
        supporting_assertions_maximum = max(
            supporting_assertions_maximum, supporting_assertions
        )
        require_locked_maximum(
            row_retained,
            "maximum_rich_relationship_row_text_units",
            limits,
            f"rich relationship runtime row {row['id']} retained text",
        )
        require_locked_maximum(
            evidence_items,
            "maximum_rich_relationship_evidence_items",
            limits,
            f"rich relationship runtime row {row['id']} evidence count",
        )
        require_locked_maximum(
            supporting_assertions,
            "maximum_rich_relationship_supporting_assertions",
            limits,
            (
                f"rich relationship runtime row {row['id']} supporting-"
                "assertion count"
            ),
        )

    chunks_by_path: dict[str, dict[str, Any]] = {}
    chunk_rows_maximum = 0
    chunk_compressed_bytes_maximum = 0
    chunk_decoded_bytes_maximum = 0
    chunk_retained_text_maximum = 0
    for chunk in chunk_measurements:
        relative = chunk["path"]
        if relative in chunks_by_path:
            raise ValueError(
                f"rich relationship runtime repeats chunk path {relative}"
            )
        chunks_by_path[relative] = chunk
        chunk_rows_maximum = max(chunk_rows_maximum, chunk["rows"])
        chunk_compressed_bytes_maximum = max(
            chunk_compressed_bytes_maximum, chunk["compressed_bytes"]
        )
        chunk_decoded_bytes_maximum = max(
            chunk_decoded_bytes_maximum, chunk["decoded_bytes"]
        )
        chunk_retained_text_maximum = max(
            chunk_retained_text_maximum, chunk["retained_text_units"]
        )
        require_locked_maximum(
            chunk["rows"],
            "maximum_rich_relationship_chunk_rows",
            limits,
            f"rich relationship runtime chunk {relative} row count",
        )
        require_locked_maximum(
            chunk["compressed_bytes"],
            "maximum_rich_relationship_chunk_bytes",
            limits,
            f"rich relationship runtime chunk {relative} compressed bytes",
        )
        require_locked_maximum(
            chunk["decoded_bytes"],
            "maximum_rich_relationship_decoded_chunk_bytes",
            limits,
            f"rich relationship runtime chunk {relative} decoded bytes",
        )
        require_locked_maximum(
            chunk["retained_text_units"],
            "maximum_rich_relationship_retained_text_units",
            limits,
            f"rich relationship runtime chunk {relative} retained text",
        )
    if sum(chunk["rows"] for chunk in chunk_measurements) != len(rows):
        raise ValueError(
            "rich relationship runtime chunk rows do not reconcile to total rows"
        )

    route_chunks_maximum = 0
    route_declared_rows_maximum = 0
    route_incident_rows_maximum = 0
    route_compressed_bytes_maximum = 0
    route_retained_text_maximum = 0
    for route, entry in sorted(incident.items()):
        selected = [chunks_by_path[path] for path in sorted(entry["chunks"])]
        selected_chunks = len(selected)
        declared_rows = sum(chunk["rows"] for chunk in selected)
        incident_rows = len(entry["assertion_ids"])
        compressed_bytes = sum(chunk["compressed_bytes"] for chunk in selected)
        route_retained = sum(
            chunk["retained_text_units"] for chunk in selected
        )
        route_chunks_maximum = max(route_chunks_maximum, selected_chunks)
        route_declared_rows_maximum = max(
            route_declared_rows_maximum, declared_rows
        )
        route_incident_rows_maximum = max(
            route_incident_rows_maximum, incident_rows
        )
        route_compressed_bytes_maximum = max(
            route_compressed_bytes_maximum, compressed_bytes
        )
        route_retained_text_maximum = max(
            route_retained_text_maximum, route_retained
        )
        require_locked_maximum(
            selected_chunks,
            "maximum_rich_relationship_route_chunks",
            limits,
            f"rich relationship route {route} selected chunk count",
        )
        require_locked_maximum(
            declared_rows,
            "maximum_rich_relationship_route_rows",
            limits,
            f"rich relationship route {route} declared shard rows",
        )
        require_locked_maximum(
            incident_rows,
            "maximum_rich_relationship_route_rows",
            limits,
            f"rich relationship route {route} incident assertion count",
        )
        require_locked_maximum(
            compressed_bytes,
            "maximum_rich_relationship_hydration_compressed_bytes",
            limits,
            f"rich relationship route {route} hydration compressed bytes",
        )
        require_locked_maximum(
            route_retained,
            "maximum_rich_relationship_retained_text_units",
            limits,
            f"rich relationship route {route} hydration retained text",
        )

    full_hydration_chunks = len(chunk_measurements)
    full_hydration_declared_rows = sum(
        chunk["rows"] for chunk in chunk_measurements
    )
    full_hydration_compressed_bytes = sum(
        chunk["compressed_bytes"] for chunk in chunk_measurements
    )
    full_hydration_retained_text_units = sum(
        chunk["retained_text_units"] for chunk in chunk_measurements
    )
    require_locked_maximum(
        full_hydration_declared_rows,
        "maximum_relationship_rows",
        limits,
        "rich relationship full hydration declared rows",
    )
    require_locked_maximum(
        full_hydration_compressed_bytes,
        "maximum_rich_relationship_hydration_compressed_bytes",
        limits,
        "rich relationship full hydration compressed bytes",
    )
    require_locked_maximum(
        full_hydration_retained_text_units,
        "maximum_rich_relationship_retained_text_units",
        limits,
        "rich relationship full hydration retained text",
    )

    locator_bucket_compressed_bytes_maximum = 0
    locator_bucket_decoded_bytes_maximum = 0
    for bucket in locator_bucket_measurements:
        relative = bucket["path"]
        locator_bucket_compressed_bytes_maximum = max(
            locator_bucket_compressed_bytes_maximum,
            bucket["compressed_bytes"],
        )
        locator_bucket_decoded_bytes_maximum = max(
            locator_bucket_decoded_bytes_maximum,
            bucket["decoded_bytes"],
        )
        require_locked_maximum(
            bucket["compressed_bytes"],
            "maximum_rich_relationship_chunk_bytes",
            limits,
            f"rich relationship route-locator bucket {relative} compressed bytes",
        )
        require_locked_maximum(
            bucket["decoded_bytes"],
            "maximum_rich_relationship_decoded_chunk_bytes",
            limits,
            f"rich relationship route-locator bucket {relative} decoded bytes",
        )

    return {
        "status": "passed",
        "consumer": {
            "version": "0.6.0",
            "commit_sha": EXPLORER_V060_COMMIT,
            "large_corpus_source_sha256": (
                EXPLORER_V060_LARGE_CORPUS_SHA256
            ),
        },
        "limits": dict(sorted(limits.items())),
        "maxima": {
            "row_retained_text_units": row_retained_maximum,
            "row_evidence_items": evidence_maximum,
            "row_supporting_assertions": supporting_assertions_maximum,
            "chunk_rows": chunk_rows_maximum,
            "chunk_compressed_bytes": chunk_compressed_bytes_maximum,
            "chunk_decoded_bytes": chunk_decoded_bytes_maximum,
            "chunk_retained_text_units": chunk_retained_text_maximum,
            "locator_bucket_compressed_bytes": (
                locator_bucket_compressed_bytes_maximum
            ),
            "locator_bucket_decoded_bytes": (
                locator_bucket_decoded_bytes_maximum
            ),
            "route_chunks": route_chunks_maximum,
            "route_declared_rows": route_declared_rows_maximum,
            "route_incident_rows": route_incident_rows_maximum,
            "route_compressed_bytes": route_compressed_bytes_maximum,
            "route_retained_text_units": route_retained_text_maximum,
            "full_hydration_chunks": full_hydration_chunks,
            "full_hydration_declared_rows": full_hydration_declared_rows,
            "full_hydration_compressed_bytes": (
                full_hydration_compressed_bytes
            ),
            "full_hydration_retained_text_units": (
                full_hydration_retained_text_units
            ),
            "total_chunks": len(chunk_measurements),
            "total_rows": len(rows),
            "total_planes": 1,
        },
        "cache_policy": {
            "maximum_cached_chunks": limits[
                "maximum_rich_relationship_cached_chunks"
            ],
            "interpretation": (
                "consumer eviction ceiling, not a producer route-chunk ceiling"
            ),
        },
    }


def write_rich_relationship_runtime(
    output: Path,
    relationships: list[dict[str, Any]],
    snapshot_id: str,
    generated_at: str,
    publication_base: str,
) -> dict[str, Any]:
    """Publish digest-bound rich rows plus a SHA-256 route locator."""
    publication_base = publication_base.rstrip("/") + "/"
    plane_id = RICH_RELATIONSHIP_PLANE_IRI
    rows = sorted(
        (rich_runtime_relationship(row, plane_id) for row in relationships),
        key=lambda item: item["id"],
    )
    row_schema = load_json(RICH_RELATIONSHIP_ROW_SCHEMA_PATH)
    Draft202012Validator.check_schema(row_schema)
    row_validator = Draft202012Validator(
        row_schema, format_checker=FormatChecker()
    )
    for ordinal, row in enumerate(rows):
        failures = sorted(
            row_validator.iter_errors(row),
            key=lambda error: "/".join(
                str(part) for part in error.absolute_path
            ),
        )
        if failures:
            detail = "; ".join(
                f"{error.json_path}: {error.message}" for error in failures[:8]
            )
            raise ValueError(f"rich runtime row {ordinal} is invalid: {detail}")
    rows = order_rich_runtime_rows_for_route_locality(rows)
    limits = locked_rich_relationship_limits()

    runtime_root = output / "data" / "semantic" / "runtime"
    chunk_rows: list[tuple[str, list[dict[str, Any]]]] = []
    chunk_metadata: list[dict[str, Any]] = []
    chunk_measurements: list[dict[str, Any]] = []
    for offset in range(0, len(rows), SHARD_SIZE):
        chunk_number = offset // SHARD_SIZE
        selected = rows[offset : offset + SHARD_SIZE]
        relative = (
            "data/semantic/runtime/core/"
            f"relationships-{chunk_number:03d}.json.gz"
        )
        path = output / relative
        write_gzip_json(path, selected)
        reference = explorer_reference(output, path)
        chunk_rows.append((relative, selected))
        chunk_metadata.append(
            {
                **reference,
                "id": urljoin(
                    publication_base,
                    "id/semantic-runtime-chunk/core-"
                    f"{chunk_number:03d}-{reference['sha256'][:16]}",
                ),
                "media_type": "application/json",
                "content_encoding": "gzip",
                "count": len(selected),
                "records": len(selected),
            }
        )
        chunk_measurements.append(
            {
                "path": relative,
                "rows": len(selected),
                "compressed_bytes": reference["bytes"],
                "decoded_bytes": len(compact_json_without_newline(selected)),
                "retained_text_units": sum(
                    retained_text_units(rich_runtime_reader_projection(row))
                    for row in selected
                ),
            }
        )

    incident: dict[str, dict[str, set[str]]] = {}
    for relative, selected in chunk_rows:
        for row in selected:
            for route in {row["source"], row["target"]}:
                entry = incident.setdefault(
                    route, {"assertion_ids": set(), "chunks": set()}
                )
                entry["assertion_ids"].add(row["id"])
                entry["chunks"].add(relative)

    bucket_rows: dict[str, list[dict[str, Any]]] = {}
    for route, entry in sorted(incident.items()):
        prefix = hashlib.sha256(route.encode("utf-8")).hexdigest()[:2]
        assertion_ids = sorted(entry["assertion_ids"])
        chunks = sorted(entry["chunks"])
        bucket_rows.setdefault(prefix, []).append(
            {
                "route": route,
                "chunks": chunks,
                "planes": [
                    {
                        "name": "core",
                        "assertions": len(assertion_ids),
                        "assertion_ids_sha256": sha256_bytes(
                            compact_json_without_newline(assertion_ids)
                        ),
                        "chunks": chunks,
                    }
                ],
            }
        )

    bucket_metadata: list[dict[str, Any]] = []
    locator_bucket_measurements: list[dict[str, Any]] = []
    chunk_reference_total = 0
    governed_paths = [output / relative for relative, _rows in chunk_rows]
    for prefix, routes in sorted(bucket_rows.items()):
        chunk_references = sum(len(row["chunks"]) for row in routes)
        bucket = {
            "schema": "okf-rich-relationship-route-locator-bucket.v1",
            "generated_at": generated_at,
            "hash_algorithm": "sha256-utf8-first-byte-hex",
            "bucket": prefix,
            "routes": routes,
            "counts": {
                "routes": len(routes),
                "chunk_references": chunk_references,
            },
        }
        relative = (
            "data/semantic/runtime/route-locator/"
            f"bucket-{prefix}.json.gz"
        )
        path = output / relative
        write_gzip_json(path, bucket)
        reference = explorer_reference(output, path)
        bucket_metadata.append(
            {
                "bucket": prefix,
                **reference,
                "content_encoding": "gzip",
                "routes": len(routes),
                "chunk_references": chunk_references,
            }
        )
        locator_bucket_measurements.append(
            {
                "path": relative,
                "compressed_bytes": reference["bytes"],
                "decoded_bytes": len(compact_json_without_newline(bucket)),
            }
        )
        chunk_reference_total += chunk_references
        governed_paths.append(path)

    limit_validation = validate_rich_relationship_runtime_limits(
        rows,
        chunk_measurements,
        locator_bucket_measurements,
        incident,
        limits,
    )

    locator = {
        "schema": "okf-rich-relationship-route-locator.v1",
        "generated_at": generated_at,
        "hash_algorithm": "sha256-utf8-first-byte-hex",
        "bucket_path_template": (
            "data/semantic/runtime/route-locator/bucket-{prefix}.json.gz"
        ),
        "buckets": bucket_metadata,
        "counts": {
            "routes": len(incident),
            "buckets": len(bucket_metadata),
            "chunk_references": chunk_reference_total,
        },
    }
    locator_path = runtime_root / "route-locator" / "manifest.json"
    write_json(locator_path, locator)
    locator_manifest_bytes = locator_path.stat().st_size
    require_locked_maximum(
        locator_manifest_bytes,
        "maximum_json_bytes",
        limits,
        "rich relationship route-locator manifest bytes",
    )
    limit_validation["maxima"]["locator_manifest_bytes"] = (
        locator_manifest_bytes
    )
    governed_paths.append(locator_path)
    locator_reference = explorer_reference(output, locator_path)

    runtime = {
        "@id": urljoin(publication_base, "id/semantic-runtime/relationships"),
        "schema": "okf-rich-relationship-runtime-manifest.v1",
        "snapshot": snapshot_id,
        "generated_at": generated_at,
        "semantic_manifest": "okf-bundle.yamlld",
        "assertion_contract": SEMANTIC_ASSERTION_SCHEMA_BUNDLE_PATH,
        "row_contract": RICH_RELATIONSHIP_ROW_SCHEMA_BUNDLE_PATH,
        "default_planes": ["core"],
        "route_locator": {
            "path": locator_reference["path"],
            "id": urljoin(
                publication_base, "id/semantic-runtime/route-locator"
            ),
            "routes": len(incident),
            "buckets": len(bucket_metadata),
            "sha256": locator_reference["sha256"],
        },
        "planes": [
            {
                "id": plane_id,
                "name": "core",
                "active": True,
                "lifecycle": "active",
                "authority_classes": sorted(
                    {clean_text(row["authority"]["class"]) for row in rows}
                ),
                "assertions": len(rows),
                "chunks": chunk_metadata,
            }
        ],
        "totals": {
            "active_assertions": len(rows),
            "historical_assertions": 0,
            "rejected_assertions": 0,
            "all_assertions": len(rows),
            "chunks": len(chunk_metadata),
        },
        "loading_policy": (
            "Load the active core plane by default. No historical or rejected "
            "plane is published in this candidate."
        ),
    }
    runtime_path = output / RICH_RELATIONSHIP_RUNTIME_BUNDLE_PATH
    write_json(runtime_path, runtime)
    runtime_manifest_bytes = runtime_path.stat().st_size
    require_locked_maximum(
        runtime_manifest_bytes,
        "maximum_json_bytes",
        limits,
        "rich relationship runtime manifest bytes",
    )
    limit_validation["maxima"]["runtime_manifest_bytes"] = (
        runtime_manifest_bytes
    )
    governed_paths.append(runtime_path)
    return {
        "reference": explorer_reference(output, runtime_path),
        "governed_paths": governed_paths,
        "validation": {
            "status": "passed",
            "rows": len(rows),
            "chunks": len(chunk_metadata),
            "routes": len(incident),
            "buckets": len(bucket_metadata),
            "default_planes": ["core"],
            "assertion_identity_set_sha256": sha256_bytes(
                compact_json_without_newline(sorted(row["id"] for row in rows))
            ),
            "consumer_limits": limit_validation,
        },
    }


def write_explorer_projection(
    output: Path,
    records: list[dict[str, Any]],
    relationship_rows: list[dict[str, Any]],
    snapshot: dict[str, Any],
    config: dict[str, Any],
    publication_base: str,
) -> dict[str, Any]:
    """Write the pinned OKF Explorer large-corpus data-plane projection."""
    projection_dir = output / "data" / "explorer"
    dataset_rows: list[dict[str, Any]] = []
    resource_rows: list[dict[str, Any]] = []
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

    primary_relationship_routes = {
        *(row["route"] for row in dataset_rows),
        *(row["route"] for row in publisher_rows),
    }
    semantic_route_kinds = {
        "activity",
        "catalogue",
        "catalogue-record",
        "jurisdiction",
        "language",
        "rights",
        "source",
    }
    for relationship in relationship_rows:
        for endpoint in (relationship["source"], relationship["target"]):
            route_kind = endpoint.partition("/")[0]
            if (
                endpoint not in primary_relationship_routes
                and route_kind not in semantic_route_kinds
            ):
                raise ValueError(
                    "relationship endpoint is neither a generated record route "
                    f"nor a governed semantic route: {endpoint}"
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
    rich_relationship_runtime = write_rich_relationship_runtime(
        output,
        relationship_rows,
        snapshot["snapshot_id"],
        config["generated_at"],
        publication_base,
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
    governed_references.extend(
        explorer_reference(output, path)
        for path in sorted(rich_relationship_runtime["governed_paths"])
    )
    governed_references.sort(key=lambda item: item["path"])
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
            "relationship_runtime": rich_relationship_runtime["reference"],
            "search": search_manifest_reference["path"],
        },
        "chunks": chunk_references,
        "integrity": {
            "algorithm": "sha256",
            "manifest_root_sha256": manifest_root_sha256,
            "scope": (
                "canonical ordered references to Explorer projection files and "
                "the bounded rich relationship runtime"
            ),
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
        "relationship_runtime": rich_relationship_runtime["reference"],
        "relationship_runtime_validation": rich_relationship_runtime["validation"],
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
  <meta http-equiv="Content-Security-Policy" content="default-src 'self'; style-src 'self'; img-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'">
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


def _dependency_pattern_matches(
    relative_path: str,
    pattern: tuple[str, bool],
) -> bool:
    literal, recursive = pattern
    return relative_path == literal or (
        recursive and relative_path.startswith(literal + "/")
    )


def _assert_repository_path_without_symlinks(
    repository_root: Path,
    relative_path: str,
    *,
    field: str,
) -> Path:
    candidate = repository_root
    for part in PurePosixPath(relative_path).parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise ValueError(f"{field} traverses a symbolic link: {relative_path}")
    return candidate


def _run_git_bounded(
    repository_root: Path,
    arguments: list[str],
    *,
    maximum_stdout_bytes: int = MAX_GIT_INVENTORY_BYTES,
) -> tuple[bytes, bytes]:
    """Run one read-only Git query without unbounded pipe accumulation."""

    command = ["git", "-C", str(repository_root), *arguments]
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        raise ValueError(f"cannot start governed Git query: {exc}") from exc
    if process.stdout is None or process.stderr is None:  # pragma: no cover
        process.kill()
        process.wait()
        raise ValueError("governed Git query did not expose bounded pipes")
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    limits = {
        "stdout": maximum_stdout_bytes,
        "stderr": MAX_GIT_DIAGNOSTIC_BYTES,
    }
    deadline = time.monotonic() + GIT_COMMAND_TIMEOUT_SECONDS
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("governed Git query exceeded its time ceiling")
            events = selector.select(remaining)
            if not events:
                raise ValueError("governed Git query exceeded its time ceiling")
            for key, _mask in events:
                chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                target = buffers[key.data]
                target.extend(chunk)
                if len(target) > limits[key.data]:
                    raise ValueError(
                        f"governed Git {key.data} exceeds its "
                        f"{limits[key.data]}-byte ceiling"
                    )
        returncode = process.wait(timeout=max(0.1, deadline - time.monotonic()))
    except (ValueError, subprocess.TimeoutExpired):
        process.kill()
        process.wait()
        raise
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()
    stdout = bytes(buffers["stdout"])
    stderr = bytes(buffers["stderr"])
    if returncode != 0:
        diagnostic = stderr.decode("utf-8", errors="replace").strip()
        raise ValueError(
            "governed Git query failed"
            + (f": {diagnostic}" if diagnostic else "")
        )
    return stdout, stderr


def _git_object_format(repository_root: Path) -> str:
    stdout, _stderr = _run_git_bounded(
        repository_root,
        ["rev-parse", "--show-object-format"],
        maximum_stdout_bytes=64,
    )
    try:
        object_format = stdout.decode("ascii").strip()
    except UnicodeDecodeError as exc:  # pragma: no cover - Git contract
        raise ValueError("Git object format is not ASCII") from exc
    if object_format not in {"sha1", "sha256"}:
        raise ValueError(f"unsupported Git object format: {object_format!r}")
    return object_format


def _git_blob_oid(payload: bytes, object_format: str) -> str:
    header = f"blob {len(payload)}\0".encode("ascii")
    if object_format == "sha1":
        return hashlib.sha1(header + payload, usedforsecurity=False).hexdigest()
    if object_format == "sha256":
        return hashlib.sha256(header + payload).hexdigest()
    raise ValueError(f"unsupported Git object format: {object_format!r}")


def _git_index_entries(repository_root: Path) -> dict[str, tuple[str, str]]:
    stdout, _stderr = _run_git_bounded(
        repository_root,
        ["ls-files", "--stage", "-z", "--"],
    )
    if stdout and not stdout.endswith(b"\0"):
        raise ValueError("Git index enumeration is not NUL-terminated")
    entries: dict[str, tuple[str, str]] = {}
    for index, raw_entry in enumerate(item for item in stdout.split(b"\0") if item):
        try:
            header, raw_path = raw_entry.split(b"\t", 1)
            mode, oid, stage_number = header.decode("ascii").split(" ")
            relative = raw_path.decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError(f"invalid Git index entry at ordinal {index}") from exc
        if stage_number != "0":
            raise ValueError(f"Git index contains an unmerged causal path: {relative}")
        if mode not in {"100644", "100755"}:
            raise ValueError(
                f"Git index causal path has unsupported mode {mode}: {relative}"
            )
        literal, recursive = normalise_dependency_pattern(
            relative,
            field=f"Git index path[{index}]",
            input_pattern=False,
        )
        if recursive or literal != relative:
            raise ValueError(f"Git index path is not literal: {relative!r}")
        if relative in entries:
            raise ValueError(f"Git index repeats path: {relative}")
        entries[relative] = (mode, oid)
        if len(entries) > MAX_GIT_INVENTORY_PATHS:
            raise ValueError(
                "Git index exceeds the executable path-count ceiling: "
                f"{len(entries)} > {MAX_GIT_INVENTORY_PATHS}"
            )
    return entries


def _git_eligible_repository_paths(
    repository_root: Path,
    *,
    indexed_only: bool = False,
) -> list[str]:
    """List bounded candidate paths, optionally requiring stage-0 indexing."""

    arguments = ["ls-files", "--cached"]
    if not indexed_only:
        arguments.extend(["--others", "--exclude-standard"])
    arguments.extend(["-z", "--"])
    stdout, _stderr = _run_git_bounded(repository_root, arguments)
    if stdout and not stdout.endswith(b"\0"):
        raise ValueError("Git input enumeration is not NUL-terminated")
    try:
        entries = [
            item.decode("utf-8")
            for item in stdout.split(b"\0")
            if item
        ]
    except UnicodeDecodeError as exc:
        raise ValueError("Git input paths must be valid UTF-8") from exc
    if len(entries) != len(set(entries)):
        raise ValueError("Git input enumeration contains duplicate paths")
    if len(entries) > MAX_GIT_INVENTORY_PATHS:
        raise ValueError(
            "Git input enumeration exceeds the executable path-count ceiling: "
            f"{len(entries)} > {MAX_GIT_INVENTORY_PATHS}"
        )
    for index, relative in enumerate(entries):
        literal, recursive = normalise_dependency_pattern(
            relative,
            field=f"Git input path[{index}]",
            input_pattern=False,
        )
        if recursive or literal != relative:
            raise ValueError(f"Git input path is not literal: {relative!r}")
    return sorted(entries)


def dependency_graph_governed_input_paths(
    graph: dict[str, Any],
    *,
    repository_root: Path = ROOT,
    indexed_only: bool = False,
) -> list[Path]:
    """Expand the dependency graph into its exact non-generated input files."""

    repository_root = repository_root.resolve()
    stages = graph.get("stages")
    generated_roots = graph.get("generated_roots")
    if not isinstance(stages, list) or not stages:
        raise ValueError("artefact dependency graph has no stages")
    if not isinstance(generated_roots, list) or not generated_roots:
        raise ValueError("artefact dependency graph has no generated roots")
    eligible_paths = _git_eligible_repository_paths(
        repository_root,
        indexed_only=indexed_only,
    )

    generated_patterns: list[tuple[str, bool]] = []
    for index, pattern in enumerate(generated_roots):
        generated_patterns.append(
            normalise_dependency_pattern(
                pattern,
                field=f"generated_roots[{index}]",
                input_pattern=False,
            )
        )
    for stage_index, stage in enumerate(stages):
        if not isinstance(stage, dict) or not clean_text(stage.get("id")):
            raise ValueError(
                f"artefact dependency graph stage {stage_index} is invalid"
            )
        outputs = stage.get("outputs")
        if not isinstance(outputs, list):
            raise ValueError(f"stage {stage['id']} outputs must be an array")
        for output_index, pattern in enumerate(outputs):
            generated_patterns.append(
                normalise_dependency_pattern(
                    pattern,
                    field=f"stage {stage['id']} outputs[{output_index}]",
                    input_pattern=False,
                )
            )

    governed: set[Path] = set()
    for stage in stages:
        stage_id = clean_text(stage["id"])
        for input_kind in ("inputs", "validation_inputs"):
            patterns = stage.get(input_kind)
            if not isinstance(patterns, list):
                raise ValueError(f"stage {stage_id} {input_kind} must be an array")
            for index, value in enumerate(patterns):
                field = f"stage {stage_id} {input_kind}[{index}]"
                literal, recursive = normalise_dependency_pattern(
                    value,
                    field=field,
                    input_pattern=True,
                )
                _assert_repository_path_without_symlinks(
                    repository_root,
                    literal,
                    field=field,
                )
                input_pattern = (literal, recursive)
                matched: list[Path] = []
                for relative in eligible_paths:
                    if not _dependency_pattern_matches(relative, input_pattern):
                        continue
                    path = _assert_repository_path_without_symlinks(
                        repository_root,
                        relative,
                        field=field,
                    )
                    if path.is_symlink() or not path.is_file():
                        raise ValueError(
                            f"{field} contains a missing or non-file input: {relative}"
                        )
                    matched.append(path)
                if not matched:
                    match_kind = "tree" if recursive else "file"
                    raise ValueError(
                        f"{field} has no eligible {match_kind} match: {value!r}"
                    )
                retained = [
                    path
                    for path in matched
                    if not any(
                        _dependency_pattern_matches(
                            path.relative_to(repository_root).as_posix(),
                            generated_pattern,
                        )
                        for generated_pattern in generated_patterns
                    )
                ]
                if not retained:
                    raise ValueError(
                        f"{field} has no governed file matches after generated "
                        f"outputs are removed: {value!r}"
                    )
                governed.update(retained)
    return sorted(governed, key=lambda path: path.relative_to(repository_root).as_posix())


def dependency_graph_build_input_paths(
    graph: dict[str, Any],
    *,
    repository_root: Path = ROOT,
    indexed_only: bool = False,
) -> list[Path]:
    """Expand exactly the schema-governed causal build-input role.

    Stage ``inputs`` and ``validation_inputs`` together define the protected
    candidate commit. They deliberately include assurance tooling and prose.
    Only the top-level ``build_inputs`` patterns belong in the deterministic
    build receipt, so route those patterns through the same fail-closed Git,
    path and generated-root expansion as the complete candidate inventory.
    """

    patterns = graph.get("build_inputs")
    if not isinstance(patterns, list) or not patterns:
        raise ValueError("artefact dependency graph has no build_inputs")
    build_graph = dict(graph)
    build_graph["stages"] = [
        {
            "id": "causal-build-inputs",
            "inputs": patterns,
            "validation_inputs": [],
            "outputs": [],
        }
    ]
    return dependency_graph_governed_input_paths(
        build_graph,
        repository_root=repository_root,
        indexed_only=indexed_only,
    )


def load_artifact_dependency_graph() -> dict[str, Any]:
    graph_path = ROOT / "governance" / "artifact-dependency-graph.json"
    schema_path = ROOT / "schemas" / "artifact-dependency-graph.schema.json"
    for path in (graph_path, schema_path):
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"dependency-graph control is missing or unsafe: {path}")
    graph = load_json(graph_path)
    schema = load_json(schema_path)
    if not isinstance(graph, dict) or not isinstance(schema, dict):
        raise ValueError("dependency-graph controls must be JSON objects")
    errors = sorted(
        Draft202012Validator(
            schema,
            format_checker=FormatChecker(),
        ).iter_errors(graph),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        locations = ", ".join(
            "/".join(str(part) for part in error.absolute_path) or "$"
            for error in errors[:5]
        )
        raise ValueError(f"artefact dependency graph fails its schema at {locations}")
    try:
        validate_build_input_contract(graph)
        validate_executable_causal_bootstrap(graph)
    except ChangeImpactError as exc:
        raise ValueError(
            "artefact dependency graph fails build-input validation: "
            f"{exc}"
        ) from exc
    return graph


def governed_input_receipts(
    snapshot: dict[str, Any],
    *,
    build_input_snapshot: BuildInputSnapshot | None = None,
) -> list[dict[str, Any]]:
    graph = load_artifact_dependency_graph()
    paths = (
        [
            ROOT / PurePosixPath(relative)
            for relative in sorted(build_input_snapshot.files)
        ]
        if build_input_snapshot is not None
        else dependency_graph_build_input_paths(graph)
    )
    relative_paths = {path.relative_to(ROOT).as_posix() for path in paths}
    required_selected_paths: set[str] = set()
    manifest_path = snapshot.get("manifest_path")
    if isinstance(manifest_path, str) and manifest_path:
        required_selected_paths.add(manifest_path)
    for row in snapshot.get("files", []):
        if isinstance(row, dict) and isinstance(row.get("path"), str):
            required_selected_paths.add(row["path"])
    composite = load_json(
        ROOT / "source" / f"input-manifest-v{SOURCE_MODEL_VERSION}.json"
    )
    for row in composite.get("inputs", []):
        if isinstance(row, dict) and isinstance(row.get("path"), str):
            required_selected_paths.add(row["path"])
    cpsv_mappings = load_json(CPSV_SERVICE_MAPPING_PATH)
    for row in cpsv_mappings.get("evidence", []):
        if isinstance(row, dict) and isinstance(row.get("source_artifact"), str):
            required_selected_paths.add(row["source_artifact"])
    missing_selected = sorted(required_selected_paths - relative_paths)
    if missing_selected:
        raise ValueError(
            "selected governed inputs are absent from the artefact dependency "
            "graph: " + ", ".join(missing_selected)
        )

    if build_input_snapshot is not None:
        return build_input_snapshot.receipt_rows()

    receipts: list[dict[str, Any]] = []
    for path in paths:
        relative = path.relative_to(ROOT).as_posix()
        checked = _assert_repository_path_without_symlinks(
            ROOT,
            relative,
            field="governed input receipt",
        )
        if checked != path or checked.is_symlink() or not checked.is_file():
            raise ValueError(f"governed input is missing or unsafe: {relative}")
        receipts.append(
            {
                "path": relative,
                "bytes": checked.stat().st_size,
                "sha256": sha256_file(checked),
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


DIRECTORY_OPEN_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
)
MACOS_RENAME_SWAP = 0x00000002
MACOS_RENAME_EXCL = 0x00000004
MACOS_RENAME_NOFOLLOW_ANY = 0x00000010
MACOS_RENAME_RESOLVE_BENEATH = 0x00000020
LINUX_RENAME_NOREPLACE = 1
LINUX_RENAME_EXCHANGE = 2
MAX_EXISTING_BUNDLE_CONTROL_BYTES = 4 * 1024 * 1024


def _lexical_absolute_path(path: Path) -> Path:
    """Return an absolute normalised path without following a symbolic link."""

    return Path(os.path.abspath(os.fspath(path)))


def _directory_identity(descriptor: int) -> tuple[int, int, int]:
    observed = os.fstat(descriptor)
    if not stat.S_ISDIR(observed.st_mode):
        raise ValueError("publication path is not a directory")
    return observed.st_dev, observed.st_ino, stat.S_IFMT(observed.st_mode)


def _open_directory_no_follow(path: Path, *, field: str) -> int:
    """Open every component of one absolute directory without following links."""

    path = _lexical_absolute_path(path)
    if not path.is_absolute():  # defensive: abspath should make this impossible
        raise ValueError(f"{field} must be absolute")
    descriptor = os.open(path.anchor, DIRECTORY_OPEN_FLAGS)
    try:
        for component in path.parts[1:]:
            if component in {"", ".", ".."}:
                raise ValueError(f"{field} contains an unsafe path component")
            next_descriptor = os.open(
                component,
                DIRECTORY_OPEN_FLAGS,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
        _directory_identity(descriptor)
        return descriptor
    except OSError as exc:
        os.close(descriptor)
        raise ValueError(
            f"cannot open {field} without following symbolic links: {exc}"
        ) from exc
    except BaseException:
        os.close(descriptor)
        raise


def _validate_publication_leaf(name: str, *, field: str) -> None:
    if (
        not name
        or name in {".", ".."}
        or "/" in name
        or unicodedata.normalize("NFC", name) != name
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in name)
    ):
        raise ValueError(f"{field} has an unsafe leaf name")


def _require_same_filesystem(
    output_parent_descriptor: int,
    staging_parent_descriptor: int,
) -> None:
    if (
        os.fstat(staging_parent_descriptor).st_dev
        != os.fstat(output_parent_descriptor).st_dev
    ):
        raise ValueError("--previous-output parent must be on the output file system")


def _read_regular_at(
    directory_descriptor: int,
    name: str,
    *,
    field: str,
    maximum_bytes: int = MAX_EXISTING_BUNDLE_CONTROL_BYTES,
) -> bytes:
    if not name or "/" in name or name in {".", ".."}:
        raise ValueError(f"{field} has an unsafe leaf name")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    descriptor = os.open(name, flags, dir_fd=directory_descriptor)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"{field} is not a regular file")
        if before.st_size > maximum_bytes:
            raise ValueError(
                f"{field} exceeds the {maximum_bytes}-byte ceiling"
            )
        chunks: list[bytes] = []
        observed = 0
        while True:
            chunk = os.read(
                descriptor,
                min(FILE_READ_CHUNK_BYTES, maximum_bytes + 1 - observed),
            )
            if not chunk:
                break
            chunks.append(chunk)
            observed += len(chunk)
            if observed > maximum_bytes:
                raise ValueError(
                    f"{field} exceeds the {maximum_bytes}-byte ceiling"
                )
        after = os.fstat(descriptor)
        before_identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        after_identity = (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        payload = b"".join(chunks)
        if before_identity != after_identity or len(payload) != before.st_size:
            raise ValueError(f"{field} changed while it was read")
        return payload
    finally:
        os.close(descriptor)


def _open_output_directory_at(parent_descriptor: int, name: str) -> int | None:
    try:
        descriptor = os.open(name, DIRECTORY_OPEN_FLAGS, dir_fd=parent_descriptor)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ValueError(
            "generated output must be an ordinary no-follow directory: "
            f"{exc}"
        ) from exc
    _directory_identity(descriptor)
    return descriptor


def _json_mapping_at(
    directory_descriptor: int,
    name: str,
    *,
    field: str,
) -> dict[str, Any] | None:
    try:
        payload = _read_regular_at(
            directory_descriptor,
            name,
            field=field,
        )
    except FileNotFoundError:
        return None
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _validate_open_generated_output(output_descriptor: int) -> None:
    try:
        _read_regular_at(
            output_descriptor,
            GENERATED_MARKER,
            field="existing generated marker",
        )
        return
    except FileNotFoundError:
        pass
    descriptor = _json_mapping_at(
        output_descriptor,
        "okf-explorer.json",
        field="existing bundle descriptor",
    )
    receipt = _json_mapping_at(
        output_descriptor,
        "build-receipt.json",
        field="existing build receipt",
    )
    recognized_legacy = (
        descriptor is not None
        and receipt is not None
        and descriptor.get("schema") == "okf-explorer-large-corpus.v1"
        and receipt.get("schema") == "okf-hmlr-build-receipt.v1"
    )
    if not recognized_legacy:
        raise ValueError(
            "refusing to replace an unmarked directory; expected a generated marker "
            "or recognised bundle descriptor and receipt"
        )


def _normalise_output_path(output_dir: Path) -> Path:
    output_dir = _lexical_absolute_path(output_dir)
    if output_dir.name != "bundle":
        raise ValueError("generated output directory must be named 'bundle'")
    if output_dir == ROOT or ROOT not in output_dir.parents:
        raise ValueError("output directory must be a child of the repository")
    return output_dir


def validate_output_target(output_dir: Path, replace: bool) -> bool:
    """Validate one target through no-follow descriptors.

    Publication repeats this validation while holding the parent descriptor and
    advisory lock. This standalone form remains useful to callers and tests but
    is not itself treated as publication authority.
    """

    if fcntl is None:
        raise ValueError(
            "atomic directory publication is unavailable on this platform; "
            "no delete-and-rename fallback is permitted"
        )
    output_dir = _normalise_output_path(output_dir)
    parent_descriptor = _open_directory_no_follow(
        output_dir.parent,
        field="generated output parent",
    )
    output_descriptor: int | None = None
    try:
        output_descriptor = _open_output_directory_at(
            parent_descriptor,
            output_dir.name,
        )
        if output_descriptor is None:
            return False
        if not replace:
            raise ValueError(
                f"output exists; pass --replace to regenerate: {output_dir}"
            )
        _validate_open_generated_output(output_descriptor)
        return True
    finally:
        if output_descriptor is not None:
            os.close(output_descriptor)
        os.close(parent_descriptor)


def _atomic_directory_rename(
    source_parent_descriptor: int,
    source_name: str,
    target_parent_descriptor: int,
    target_name: str,
    *,
    exchange: bool,
) -> None:
    """Use the platform's atomic exchange or no-replace directory primitive."""

    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform == "darwin":
        function = getattr(library, "renameatx_np", None)
        flag = MACOS_RENAME_SWAP if exchange else MACOS_RENAME_EXCL
        flag |= MACOS_RENAME_NOFOLLOW_ANY | MACOS_RENAME_RESOLVE_BENEATH
    elif sys.platform.startswith("linux"):
        function = getattr(library, "renameat2", None)
        flag = LINUX_RENAME_EXCHANGE if exchange else LINUX_RENAME_NOREPLACE
    else:
        function = None
        flag = 0
    if function is None:
        raise ValueError(
            "atomic directory publication is unavailable on this platform; "
            "no delete-and-rename fallback is permitted"
        )
    function.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    function.restype = ctypes.c_int
    result = function(
        source_parent_descriptor,
        os.fsencode(source_name),
        target_parent_descriptor,
        os.fsencode(target_name),
        flag,
    )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    operation = "exchange" if exchange else "no-replace publication"
    if error_number in {errno.EXDEV, errno.ENOTSUP, errno.EOPNOTSUPP}:
        raise ValueError(
            f"atomic directory {operation} is unsupported on this file system; "
            "the live bundle and candidate swap slot were retained"
        )
    if not exchange and error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise FileExistsError(
            error_number,
            "the output appeared during atomic no-replace publication",
            target_name,
        )
    raise OSError(error_number, os.strerror(error_number), target_name)


@dataclass
class BundlePublicationTransaction:
    output_dir: Path
    staging_path: Path
    output_parent_descriptor: int
    staging_parent_descriptor: int
    output_descriptor: int | None
    staging_descriptor: int
    output_identity: tuple[int, int, int] | None
    staging_identity: tuple[int, int, int]
    published_operation: str | None = None

    def _named_directory_identity(
        self,
        parent_descriptor: int,
        name: str,
    ) -> tuple[int, int, int] | None:
        descriptor = _open_output_directory_at(parent_descriptor, name)
        if descriptor is None:
            return None
        try:
            return _directory_identity(descriptor)
        finally:
            os.close(descriptor)

    def publish(self) -> dict[str, Any]:
        """Publish once while retaining every previous live bundle."""

        current_output = self._named_directory_identity(
            self.output_parent_descriptor,
            self.output_dir.name,
        )
        if current_output != self.output_identity:
            raise ValueError(
                "generated output identity changed before atomic publication"
            )
        current_staging = self._named_directory_identity(
            self.staging_parent_descriptor,
            self.staging_path.name,
        )
        if current_staging != self.staging_identity:
            raise ValueError(
                "candidate swap-slot identity changed before atomic publication"
            )

        exchange = self.output_identity is not None
        _atomic_directory_rename(
            self.staging_parent_descriptor,
            self.staging_path.name,
            self.output_parent_descriptor,
            self.output_dir.name,
            exchange=exchange,
        )
        # Record success immediately: errors below are post-publication and must
        # never trigger cleanup or an unsafe guessed rollback.
        self.published_operation = "exchange" if exchange else "no-replace"

        live_identity = self._named_directory_identity(
            self.output_parent_descriptor,
            self.output_dir.name,
        )
        if live_identity != self.staging_identity:
            raise RuntimeError(
                "atomic publication completed but the live bundle identity is "
                f"unexpected; inspect {self.output_dir} and {self.staging_path}"
            )
        previous_output: str | None = None
        if exchange:
            retained_identity = self._named_directory_identity(
                self.staging_parent_descriptor,
                self.staging_path.name,
            )
            if retained_identity != self.output_identity:
                raise RuntimeError(
                    "atomic publication completed but the retained previous bundle "
                    f"identity is unexpected; inspect {self.output_dir} and "
                    f"{self.staging_path}"
                )
            previous_output = str(self.staging_path)
        elif self._named_directory_identity(
            self.staging_parent_descriptor,
            self.staging_path.name,
        ) is not None:
            raise RuntimeError(
                "atomic no-replace publication completed but the candidate swap "
                f"slot still exists; inspect {self.output_dir} and {self.staging_path}"
            )

        os.fsync(self.output_parent_descriptor)
        if self.staging_parent_descriptor != self.output_parent_descriptor:
            os.fsync(self.staging_parent_descriptor)
        return {
            "publication_operation": self.published_operation,
            "previous_output": previous_output,
        }


@contextmanager
def bundle_publication_transaction(
    output_dir: Path,
    *,
    replace: bool,
    previous_output: Path | None,
    fallback_staging: Path,
) -> Iterable[BundlePublicationTransaction]:
    """Reserve and hold every namespace object used by one publication."""

    if fcntl is None:
        raise ValueError(
            "atomic directory publication is unavailable on this platform; "
            "no delete-and-rename fallback is permitted"
        )
    output_dir = _normalise_output_path(output_dir)
    output_parent_descriptor = _open_directory_no_follow(
        output_dir.parent,
        field="generated output parent",
    )
    output_descriptor: int | None = None
    staging_parent_descriptor: int | None = None
    staging_descriptor: int | None = None
    try:
        try:
            fcntl.flock(
                output_parent_descriptor,
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError as exc:
            raise ValueError(
                "another cooperating bundle publication holds the output-parent lock"
            ) from exc
        output_descriptor = _open_output_directory_at(
            output_parent_descriptor,
            output_dir.name,
        )
        if output_descriptor is not None:
            if not replace:
                raise ValueError(
                    f"output exists; pass --replace to regenerate: {output_dir}"
                )
            _validate_open_generated_output(output_descriptor)
            if previous_output is None:
                raise ValueError(
                    "replacing an existing bundle requires --previous-output so "
                    "the complete previous bundle is retained"
                )

        if previous_output is not None:
            if not previous_output.is_absolute():
                raise ValueError("--previous-output must be an absolute path")
            staging_path = _lexical_absolute_path(previous_output)
            if staging_path == ROOT or ROOT in staging_path.parents:
                raise ValueError("--previous-output must be outside the repository")
            _validate_publication_leaf(
                staging_path.name,
                field="--previous-output",
            )
            staging_parent_descriptor = _open_directory_no_follow(
                staging_path.parent,
                field="previous-output parent",
            )
            _require_same_filesystem(
                output_parent_descriptor,
                staging_parent_descriptor,
            )
            try:
                os.stat(
                    staging_path.name,
                    dir_fd=staging_parent_descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                pass
            else:
                raise ValueError(
                    "--previous-output must name an exact non-existent path"
                )
            try:
                os.mkdir(
                    staging_path.name,
                    mode=0o755,
                    dir_fd=staging_parent_descriptor,
                )
            except FileExistsError as exc:
                raise ValueError(
                    "--previous-output appeared while reserving the swap slot"
                ) from exc
        else:
            staging_path = _lexical_absolute_path(fallback_staging)
            staging_path.mkdir(parents=False)
            staging_parent_descriptor = _open_directory_no_follow(
                staging_path.parent,
                field="temporary candidate parent",
            )
        staging_descriptor = os.open(
            staging_path.name,
            DIRECTORY_OPEN_FLAGS,
            dir_fd=staging_parent_descriptor,
        )
        os.fchmod(staging_descriptor, 0o755)
        transaction = BundlePublicationTransaction(
            output_dir=output_dir,
            staging_path=staging_path,
            output_parent_descriptor=output_parent_descriptor,
            staging_parent_descriptor=staging_parent_descriptor,
            output_descriptor=output_descriptor,
            staging_descriptor=staging_descriptor,
            output_identity=(
                _directory_identity(output_descriptor)
                if output_descriptor is not None
                else None
            ),
            staging_identity=_directory_identity(staging_descriptor),
        )
        yield transaction
    finally:
        if staging_descriptor is not None:
            os.close(staging_descriptor)
        if output_descriptor is not None:
            os.close(output_descriptor)
        if (
            staging_parent_descriptor is not None
            and staging_parent_descriptor != output_parent_descriptor
        ):
            os.close(staging_parent_descriptor)
        os.close(output_parent_descriptor)


def _run_bounded_evaluator(
    command: list[str],
    *,
    working_directory: Path,
    report_path: Path,
) -> dict[str, Any]:
    """Run the frozen evaluator with bounded resources and no inherited secrets."""

    try:
        process = subprocess.Popen(
            command,
            cwd=working_directory,
            env={
                "LANG": "C",
                "LC_ALL": "C",
                "TZ": "UTC",
            },
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        raise ValueError(f"cannot start frozen evaluator: {exc}") from exc
    if process.stdout is None or process.stderr is None:  # pragma: no cover
        process.kill()
        process.wait()
        raise ValueError("frozen evaluator did not expose bounded output pipes")
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = time.monotonic() + EVALUATOR_TIMEOUT_SECONDS
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("frozen evaluator exceeded its time ceiling")
            events = selector.select(remaining)
            if not events:
                raise ValueError("frozen evaluator exceeded its time ceiling")
            for key, _mask in events:
                chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                target = buffers[key.data]
                target.extend(chunk)
                if len(target) > MAX_EVALUATOR_OUTPUT_BYTES:
                    raise ValueError(
                        f"frozen evaluator {key.data} exceeds its governed "
                        f"{MAX_EVALUATOR_OUTPUT_BYTES}-byte ceiling"
                    )
        try:
            returncode = process.wait(
                timeout=max(0.1, deadline - time.monotonic())
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError("frozen evaluator exceeded its time ceiling") from exc
    except BaseException:
        process.kill()
        process.wait()
        raise
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()

    stdout = bytes(buffers["stdout"])
    stderr = bytes(buffers["stderr"])
    if returncode != 0:
        detail = (
            stderr.decode("utf-8", errors="replace").strip()
            or stdout.decode("utf-8", errors="replace").strip()
            or f"exit {returncode}"
        )
        raise ValueError(f"evaluation failed: {detail}")
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(
                    f"frozen evaluation report repeats JSON key {key!r}"
                )
            result[key] = value
        return result

    def reject_non_finite(value: str) -> None:
        raise ValueError(
            "frozen evaluation report contains a non-finite JSON number: "
            f"{value}"
        )

    try:
        report_bytes, _identity = _bounded_read_file(
            report_path,
            maximum_bytes=MAX_EVALUATION_REPORT_BYTES,
            field="frozen evaluation report",
        )
        report = json.loads(
            report_bytes.decode("utf-8"),
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_non_finite,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"frozen evaluation report is invalid: {exc}") from exc
    if not isinstance(report, dict):
        raise ValueError("frozen evaluation report must be a JSON object")
    return report


def _validate_publication_request(publication_base: str) -> None:
    """Keep cheap caller errors ahead of the indexed release preflight."""

    config = load_build_config()
    requested = canonical_https_url(
        publication_base,
        field="build publication base",
    )
    governed = canonical_https_url(
        config.get("publication_base"),
        field="source/build-config.json publication_base",
    )
    if requested != publication_base or not publication_base.endswith("/"):
        raise ValueError(
            "build publication base must be canonical HTTPS and end in '/'"
        )
    if governed != config.get("publication_base") or not governed.endswith("/"):
        raise ValueError(
            "source/build-config.json publication_base must be canonical HTTPS "
            "and end in '/'"
        )
    if requested != governed:
        raise ValueError(
            "build publication base differs from governed "
            "source/build-config.json publication_base"
        )


def _clear_build_input_caches() -> None:
    """Prevent preflight reads being reused instead of frozen transaction bytes."""

    for function in (
        locked_rich_relationship_limits,
        validate_profile_vendor_lock,
        load_curated_rights_access_classifications,
        load_type_kind_crosswalk,
        load_publisher_registry,
        _load_source_artifact_snapshot,
    ):
        function.cache_clear()


def build_reproduction_invocation(
    executable_contract: str,
    snapshot_manifest_path: str,
    publication_base: str,
) -> list[str]:
    """Return the portable recipe without embedding an operational swap path."""

    return [
        executable_contract,
        "-I",
        "-B",
        "-X",
        "pycache_prefix=<private-empty-directory>",
        "scripts/build.py",
        "--snapshot-dir",
        Path(snapshot_manifest_path).parent.as_posix(),
        "--publication-base",
        publication_base,
        "--replace",
        "--previous-output",
        "<owner-selected-empty-same-filesystem-path>",
    ]


def build(
    snapshot_dir: Path | None,
    output_dir: Path,
    publication_base: str,
    replace: bool,
    previous_output: Path | None = None,
) -> dict[str, Any]:
    _validate_publication_request(publication_base)
    build_input_snapshot = BuildInputSnapshot.capture(ROOT)
    with activate_build_input_snapshot(build_input_snapshot):
        _clear_build_input_caches()
        return _build_from_snapshot(
            snapshot_dir=snapshot_dir,
            output_dir=output_dir,
            publication_base=publication_base,
            replace=replace,
            previous_output=previous_output,
            build_input_snapshot=build_input_snapshot,
        )


def _build_from_snapshot(
    snapshot_dir: Path | None,
    output_dir: Path,
    publication_base: str,
    replace: bool,
    previous_output: Path | None = None,
    *,
    build_input_snapshot: BuildInputSnapshot,
) -> dict[str, Any]:
    config = load_build_config()
    requested_publication_base = canonical_https_url(
        publication_base,
        field="build publication base",
    )
    governed_publication_base = canonical_https_url(
        config.get("publication_base"),
        field="source/build-config.json publication_base",
    )
    for label, value in (
        ("build publication base", requested_publication_base),
        (
            "source/build-config.json publication_base",
            governed_publication_base,
        ),
    ):
        parsed_publication_base = urlparse(value)
        expected_authority = (parsed_publication_base.hostname or "").casefold()
        if parsed_publication_base.port is not None:
            expected_authority += f":{parsed_publication_base.port}"
        if (
            parsed_publication_base.netloc != expected_authority
            or parsed_publication_base.query
            or parsed_publication_base.fragment
        ):
            raise ValueError(
                f"{label} must have a canonical authority and no query or fragment"
            )
    if requested_publication_base != publication_base or not publication_base.endswith(
        "/"
    ):
        raise ValueError(
            "build publication base must be canonical HTTPS and end in '/'"
        )
    if (
        governed_publication_base != config.get("publication_base")
        or not governed_publication_base.endswith("/")
    ):
        raise ValueError(
            "source/build-config.json publication_base must be canonical HTTPS "
            "and end in '/'"
        )
    if publication_base != governed_publication_base:
        raise ValueError(
            "build publication base differs from governed "
            "source/build-config.json publication_base"
        )
    output_dir = _normalise_output_path(output_dir)
    output_exists = validate_output_target(output_dir, replace)
    if output_exists and previous_output is None:
        raise ValueError(
            "replacing an existing bundle requires --previous-output so the "
            "complete previous bundle is retained"
        )

    profile_vendor_receipt = validate_profile_vendor_lock()
    cpsv_vendor_receipt = validate_cpsv_ap_vendor_lock()
    ai_usage_path = ROOT / "governance" / "ai-model-usage.json"
    ai_usage = load_ai_model_usage(config)
    if config["version"] != BUILD_VERSION:
        raise ValueError("build config version and builder version differ")
    build_runtime = python_runtime_receipt()
    sources, rights = source_controls()
    discovered, snapshot = snapshot_records(snapshot_dir)
    if snapshot_dir is None:
        raise ValueError("the governed composite input manifest is required")
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
                ROOT / "source" / f"input-manifest-v{SOURCE_MODEL_VERSION}.json"
            ),
            "manifest_path": f"source/input-manifest-v{SOURCE_MODEL_VERSION}.json",
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
            "bytes": len(
                repository_bytes(
                    ROOT / content_meta["path"],
                    maximum_bytes=MAX_CAUSAL_INPUT_FILE_BYTES,
                    field=f"content observation {content_meta['path']}",
                )
            ),
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
    cpsv_mappings = load_cpsv_service_mappings(records)
    build_chronology = validate_generated_at_chronology(
        config,
        snapshot,
        cpsv_mappings,
    )
    relationship_assertions = semantic_relationship_assertions(
        publication_base, records, composite_manifest, cpsv_mappings
    )
    relationship_rows = [
        runtime_relationship(assertion) for assertion in relationship_assertions
    ]
    input_receipts = governed_input_receipts(
        snapshot,
        build_input_snapshot=build_input_snapshot,
    )

    with (
        tempfile.TemporaryDirectory(prefix=".okf-build-", dir=ROOT) as temp_name,
        bundle_publication_transaction(
            output_dir,
            replace=replace,
            previous_output=previous_output,
            fallback_staging=Path(temp_name) / "bundle",
        ) as publication,
    ):
        staging = publication.staging_path
        evaluation_root = Path(temp_name) / "evaluation-input"
        for source in (
            ROOT / "scripts" / "evaluate.py",
            ROOT / "pages" / "search-contract.json",
            ROOT / "contracts" / "okf-explorer.consumer-lock.json",
            ROOT / "evaluation" / "questions.json",
        ):
            relative = source.relative_to(ROOT)
            copy_repository_input(
                source,
                evaluation_root / relative,
            )
        copy_pages(
            staging,
            {row["path"] for row in input_receipts},
        )
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
                    "additional_rights_refs": record.get(
                        "additional_rights_refs", []
                    ),
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
            staging,
            records,
            relationship_rows,
            snapshot,
            config,
            publication_base,
        )
        write_static_catalogue(staging, records)
        evaluation_pycache = Path(temp_name) / "evaluation-pycache"
        evaluation_pycache.mkdir(mode=0o700)
        evaluation_report_path = staging / "data" / "evaluation-report.json"
        _run_bounded_evaluator(
            [
                sys.executable,
                "-I",
                "-B",
                "-X",
                f"pycache_prefix={evaluation_pycache}",
                str(evaluation_root / "scripts" / "evaluate.py"),
                "--bundle",
                str(staging),
                "--output",
                str(evaluation_report_path),
            ],
            working_directory=evaluation_root,
            report_path=evaluation_report_path,
        )

        local_context_path = staging / "context.jsonld"
        write_json(
            local_context_path,
            load_json(ROOT / "source" / "jsonld-context.json"),
        )
        semantic_document = jsonld_projection(
            publication_base,
            snapshot,
            records,
            relationship_assertions,
            cpsv_mappings,
            config,
        )
        require_profile_conformance(
            semantic_document,
            "bundle.schema.json",
            "semantic bundle root",
        )
        write_json(staging / "okf-bundle.jsonld", semantic_document)
        write_yaml_ld(staging / "okf-bundle.yamlld", semantic_document)
        semantic_validation = validate_semantic_relationship_planes(
            semantic_document, relationship_rows
        )
        semantic_validation["cpsv_ap"] = validate_cpsv_ap_projection(
            semantic_document,
            records,
            relationship_assertions,
            cpsv_mappings,
            cpsv_vendor_receipt,
            publication_base,
        )
        semantic_validation["rich_relationship_runtime"] = explorer_projection[
            "relationship_runtime_validation"
        ]
        semantic_validation["context_alignment"] = (
            validate_semantic_context_alignment()
        )
        schema_bundle_path = staging / SEMANTIC_ASSERTION_SCHEMA_BUNDLE_PATH
        schema_bundle_path.parent.mkdir(parents=True, exist_ok=True)
        copy_repository_input(SEMANTIC_ASSERTION_SCHEMA_PATH, schema_bundle_path)
        runtime_row_schema_path = (
            staging / RICH_RELATIONSHIP_ROW_SCHEMA_BUNDLE_PATH
        )
        copy_repository_input(
            RICH_RELATIONSHIP_ROW_SCHEMA_PATH,
            runtime_row_schema_path,
        )
        canonical_context_path = staging / SEMANTIC_CONTEXT_BUNDLE_PATH
        copy_repository_input(
            ROOT
            / "profiles"
            / "bundle-wiki"
            / "v1"
            / "semantic-context.jsonld",
            canonical_context_path,
        )
        shape_path = staging / "data" / "semantic" / "profile-shapes.ttl"
        copy_repository_input(
            ROOT / "profiles" / "bundle-wiki" / "v1" / "shapes.ttl",
            shape_path,
        )
        cpsv_bundle_root = staging / CPSV_AP_BUNDLE_ROOT
        cpsv_bundle_root.mkdir(parents=True, exist_ok=True)
        cpsv_context_path = cpsv_bundle_root / "cpsv-ap.jsonld"
        cpsv_vocabulary_path = cpsv_bundle_root / "cpsv-ap.ttl"
        cpsv_shape_path = cpsv_bundle_root / "cpsv-ap-SHACL.ttl"
        for source, target in (
            (CPSV_AP_ROOT / "cpsv-ap.jsonld", cpsv_context_path),
            (CPSV_AP_ROOT / "cpsv-ap.ttl", cpsv_vocabulary_path),
            (CPSV_AP_ROOT / "cpsv-ap-SHACL.ttl", cpsv_shape_path),
        ):
            copy_repository_input(source, target)
        cpsv_lock_bundle_path = (
            staging
            / "data"
            / "semantic"
            / "standards"
            / "cpsv-ap"
            / "3.2.0.vendor-lock.json"
        )
        copy_repository_input(CPSV_AP_LOCK_PATH, cpsv_lock_bundle_path)
        cpsv_mapping_bundle_path = staging / CPSV_SERVICE_MAPPING_BUNDLE_PATH
        copy_repository_input(CPSV_SERVICE_MAPPING_PATH, cpsv_mapping_bundle_path)
        iri_registry_path = staging / IRI_ROUTE_REGISTRY_BUNDLE_PATH
        predicate_registry_path = staging / PREDICATE_REGISTRY_BUNDLE_PATH
        iri_registry = semantic_iri_route_registry(
            semantic_document, snapshot["snapshot_id"]
        )
        predicate_registry = semantic_predicate_registry(
            relationship_assertions,
            snapshot["snapshot_id"],
            config["generated_at"],
        )
        write_json(iri_registry_path, iri_registry)
        write_json(predicate_registry_path, predicate_registry)
        semantic_model_path = staging / SEMANTIC_MODEL_BUNDLE_PATH
        semantic_model = semantic_model_descriptor(
            staging,
            publication_base,
            local_context_path,
            canonical_context_path,
            iri_registry_path,
            predicate_registry_path,
            shape_path,
            cpsv_context_path,
            cpsv_vocabulary_path,
            cpsv_shape_path,
        )
        write_json(semantic_model_path, semantic_model)
        assertion_counts = Counter(
            clean_text(assertion["predicate"]["@id"])
            for assertion in relationship_assertions
        )
        semantic_validation["profile_validation"] = {
            "bundle": "conformant",
            "semantic_model": "conformant",
            "iri_route_registry": "conformant",
            "predicate_registry": "conformant",
            "inference": "not-run",
        }
        semantic_validation["coverage"] = {
            "route_bearing_semantic_identities": iri_registry["counts"]["entries"],
            "governed_predicates": predicate_registry["counts"]["predicates"],
            "assertions_by_predicate": dict(sorted(assertion_counts.items())),
        }
        semantic_validation["contract_metrics"] = validate_semantic_contract_metrics(
            semantic_validation
        )
        semantic_validation["resources"] = {
            "semantic_model": semantic_resource_reference(
                staging, semantic_model_path, "application/json"
            ),
            "iri_route_registry": semantic_resource_reference(
                staging, iri_registry_path, "application/json"
            ),
            "predicate_registry": semantic_resource_reference(
                staging, predicate_registry_path, "application/json"
            ),
            "canonical_context": semantic_resource_reference(
                staging, canonical_context_path, "application/ld+json"
            ),
            "profile_shapes": semantic_resource_reference(
                staging, shape_path, "text/turtle"
            ),
            "runtime_row_schema": semantic_resource_reference(
                staging, runtime_row_schema_path, "application/schema+json"
            ),
            "cpsv_ap_context": semantic_resource_reference(
                staging, cpsv_context_path, "application/ld+json"
            ),
            "cpsv_ap_vocabulary": semantic_resource_reference(
                staging, cpsv_vocabulary_path, "text/turtle"
            ),
            "cpsv_ap_shapes": semantic_resource_reference(
                staging, cpsv_shape_path, "text/turtle"
            ),
            "cpsv_ap_vendor_lock": semantic_resource_reference(
                staging, cpsv_lock_bundle_path, "application/json"
            ),
            "cpsv_service_mapping": semantic_resource_reference(
                staging, cpsv_mapping_bundle_path, "application/json"
            ),
        }
        semantic_validation_path = (
            staging / SEMANTIC_ASSERTION_VALIDATION_BUNDLE_PATH
        )
        write_json(semantic_validation_path, semantic_validation)
        descriptor = make_descriptor(
            publication_base,
            snapshot,
            records,
            curated_meta,
            config,
            reconciliation,
            explorer_projection,
        )
        descriptor["extensions"]["okf-bundle-wiki-semantic.v1"] = {
            "profile": (
                "https://chris-page-gov.github.io/okf-explorer/profile/"
                "bundle-wiki/v1/"
            ),
            "semantic_model": explorer_reference(staging, semantic_model_path),
            "semantic_context": explorer_reference(
                staging, canonical_context_path
            ),
            "iri_route_registry": explorer_reference(
                staging, iri_registry_path
            ),
            "predicate_registry": explorer_reference(
                staging, predicate_registry_path
            ),
            "profile_shapes": explorer_reference(staging, shape_path),
            "assertion_schema": explorer_reference(staging, schema_bundle_path),
            "runtime_row_schema": explorer_reference(
                staging, runtime_row_schema_path
            ),
            "validation": explorer_reference(staging, semantic_validation_path),
            "cpsv_ap": {
                "version": CPSV_AP_VERSION,
                "context": explorer_reference(staging, cpsv_context_path),
                "vocabulary": explorer_reference(
                    staging, cpsv_vocabulary_path
                ),
                "shapes": explorer_reference(staging, cpsv_shape_path),
                "vendor_lock": explorer_reference(
                    staging, cpsv_lock_bundle_path
                ),
                "service_mapping": explorer_reference(
                    staging, cpsv_mapping_bundle_path
                ),
                "official_shacl_execution": "not-run",
            },
        }
        write_json(staging / "okf-explorer.json", descriptor)
        write_json(staging / "data" / "manifest.json", data_manifest(staging, records))
        reproduction_invocation = build_reproduction_invocation(
            build_runtime["executable_contract"],
            snapshot["acquisition_snapshot"]["manifest_path"],
            publication_base,
        )
        receipt = {
            "schema": "okf-hmlr-build-receipt.v1",
            "builder": f"scripts/build.py/{BUILD_VERSION}",
            "builder_sha256": sha256_file(ROOT / "scripts" / "build.py"),
            "python_runtime": build_runtime,
            "reproduction_invocation": reproduction_invocation,
            "network_access": False,
            "snapshot": snapshot,
            "curated_source": curated_meta,
            "observed_at": snapshot["observed_at"],
            "generated_at": config["generated_at"],
            "build_chronology": build_chronology,
            "version": config["version"],
            "publication_base": publication_base,
            "release_at": config.get("release_at"),
            "domain_profile_canonical_sha256": canonical_profile_sha256(),
            "domain_profile_pack_root_sha256": profile_pack_root_sha256(),
            "governed_inputs": input_receipts,
            "record_count": len(records),
            "semantic_assertion_validation": semantic_validation,
            "bundle_profile_vendor_lock": profile_vendor_receipt,
            "cpsv_ap_vendor_lock": cpsv_vendor_receipt,
            "cpsv_service_mapping": cpsv_mappings["receipt"],
            "status": config["status"],
            "publication_state": config["publication_state"],
        }
        write_json(staging / "build-receipt.json", receipt)
        root_digest = write_checksums(staging)

        # This is deliberately the final check before the atomic namespace
        # operation. Outputs and receipts came from the frozen payloads;
        # index/worktree drift therefore aborts with the live bundle intact.
        build_input_snapshot.verify_unchanged()
        publication_result = publication.publish()
    return {
        "output": str(output_dir),
        "records": len(records),
        "snapshot": snapshot["snapshot_id"],
        "release_root_sha256": root_digest,
        **publication_result,
    }


def main() -> int:
    args = parse_args()
    result = build(
        snapshot_dir=args.snapshot_dir,
        output_dir=args.output_dir,
        publication_base=args.publication_base,
        replace=args.replace,
        previous_output=args.previous_output,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
