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
STAGE1_SEMANTIC_PROFILE_SHA256 = (
    "9c2d86c00217f46d508a81c6fa982df6d9664bbdd4b49d8eed0b6d2c415d056d"
)
STAGE1_SEMANTIC_PROFILE_PACK_ROOT_SHA256 = (
    "967877c5eaf5c6f7f3180543f5818a66b7fb7684af6ef530ce5448307338901c"
)
STAGE1_SEMANTIC_PROFILE_PACK_MEMBERS = (
    "decision-register.md",
    "domain-profile.json",
    "domain-profile.yaml",
    "domain-warmup-report.md",
    "evidence-register.jsonl",
    "traceability.json",
)
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
CLASS_ROUTE_REGISTRY_SCHEMA_PATH = (
    ROOT / "schemas" / "semantic-class-route-registry.schema.json"
)
CLASS_ROUTE_REGISTRY_SCHEMA_BUNDLE_PATH = (
    "data/semantic/schemas/semantic-class-route-registry.schema.json"
)
CLASS_ROUTE_REGISTRY_BUNDLE_PATH = (
    "data/semantic/class-route-registry.json"
)
CLASS_ROUTE_REGISTRY_SCHEMA_ID = (
    "https://chris-page-gov.github.io/okf-LandRegistry/"
    "schemas/semantic-class-route-registry.schema.json"
)
PREDICATE_REGISTRY_BUNDLE_PATH = "data/semantic/predicate-registry.json"
PREDICATE_REGISTRY_V2_PROFILE_URL = (
    "https://chris-page-gov.github.io/okf-explorer/profile/"
    "predicate-registry/v2/"
)
PREDICATE_REGISTRY_V2_LOCK_URL = (
    "https://chris-page-gov.github.io/okf-explorer/profile/"
    "predicate-registry/v2.lock.json"
)
PREDICATE_REGISTRY_V2_ROOT = (
    ROOT / "profiles" / "predicate-registry" / "v2"
)
PREDICATE_REGISTRY_V2_LOCK_PATH = (
    ROOT / "profiles" / "predicate-registry" / "v2.lock.json"
)
PREDICATE_REGISTRY_V2_SCHEMA_PATH = (
    PREDICATE_REGISTRY_V2_ROOT / "predicate-registry.schema.json"
)
PREDICATE_REGISTRY_V2_SCHEMA_BUNDLE_PATH = (
    "data/semantic/schemas/predicate-registry.v2.schema.json"
)
PREDICATE_REGISTRY_V2_SCHEMA_ID = (
    PREDICATE_REGISTRY_V2_PROFILE_URL + "predicate-registry.schema.json"
)
PREDICATE_REGISTRY_V2_SCHEMA_BYTES = 7_551
PREDICATE_REGISTRY_V2_SCHEMA_SHA256 = (
    "037151379a1ec0cbfe0666d41592585a891a63929f1fcf2845d1eb3de8dd5069"
)
PREDICATE_REGISTRY_V2_LOCK_SHA256 = (
    "3d1f7cdbb423628f3938e5aef299ae09013f56be515ff2155475c5325ffd0110"
)
PREDICATE_REGISTRY_V2_IDENTITY_SHA256 = (
    "75e444a35fdfe28fc111b6f0490cb8a0d569d20c1e4b62410174ead2608d86c6"
)
PREDICATE_REGISTRY_V2_MAX_BYTES = 16 * 1024 * 1024
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
EXPLORER_V062_COMMIT = "9430b3931f96bd9e6e06165c15b522742611f3e9"
EXPLORER_V062_GIT_TREE = "9d13ee9c2b174819feea2d732420674d4df5273b"
EXPLORER_V062_TAG_OBJECT = "43e53f36d869ba7ca2420990191a0834a969dcd2"
EXPLORER_V062_LARGE_CORPUS_SHA256 = (
    "a48f4bcb83ff80f7af42b1bc0247bfbca085976348a64d00833b00266b3adf65"
)
PREDICATE_REGISTRY_V2_SOURCE_COMMIT = (
    "839d4ba4c2d02abc6ef02b3ca1dcbf6a4008e7c8"
)
PREDICATE_REGISTRY_V2_SOURCE_TAG_OBJECT = (
    "b5918192b1e3969ca2b069a4d56b3d26884ea96c"
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
EXPLORER_V062_RICH_RELATIONSHIP_LIMITS = {
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


def compact_json(value: Any) -> bytes:
    """Return deterministic compact JSON with an explicit LF terminator."""
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
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
    """Load the exact rich-runtime ceilings admitted by Explorer v0.6.2."""
    lock = load_json(EXPLORER_CONSUMER_LOCK_PATH)
    consumer = lock.get("consumer", {})
    runtime = lock.get("rich_relationship_runtime", {})
    if (
        lock.get("schema") != "okf-explorer-consumer-lock.v1"
        or consumer.get("version") != "0.6.2"
        or consumer.get("release_tag") != "v0.6.2"
        or consumer.get("commit_sha") != EXPLORER_V062_COMMIT
        or consumer.get("git_tree") != EXPLORER_V062_GIT_TREE
        or consumer.get("annotated_tag_object_sha")
        != EXPLORER_V062_TAG_OBJECT
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
        != EXPLORER_V062_LARGE_CORPUS_SHA256
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
    if limits != EXPLORER_V062_RICH_RELATIONSHIP_LIMITS:
        raise ValueError(
            "Explorer consumer-lock limits differ from the executable "
            "Explorer v0.6.2 contract"
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


@functools.lru_cache(maxsize=1)
def validate_predicate_registry_profile_lock() -> dict[str, Any]:
    """Verify the exact local Predicate Registry v2 profile extension."""
    if sha256_file(PREDICATE_REGISTRY_V2_LOCK_PATH) != (
        PREDICATE_REGISTRY_V2_LOCK_SHA256
    ):
        raise ValueError("Predicate Registry v2 profile lock differs")
    lock = load_json(PREDICATE_REGISTRY_V2_LOCK_PATH)
    if (
        lock.get("schema") != "okf-profile-extension-lock.v1"
        or lock.get("profile") != PREDICATE_REGISTRY_V2_PROFILE_URL
        or lock.get("file_count") != 2
        or lock.get("identity", {}).get("algorithm") != "sha256"
        or lock.get("identity", {}).get("sha256")
        != PREDICATE_REGISTRY_V2_IDENTITY_SHA256
    ):
        raise ValueError(
            "Predicate Registry v2 profile lock identity is unsupported"
        )
    rows = lock.get("files")
    if not isinstance(rows, list) or len(rows) != lock["file_count"]:
        raise ValueError(
            "Predicate Registry v2 profile lock has an invalid inventory"
        )
    actual_names: list[str] = []
    for path in PREDICATE_REGISTRY_V2_ROOT.rglob("*"):
        if path.is_symlink():
            raise ValueError(
                "Predicate Registry v2 profile must not contain symlinks"
            )
        if path.is_file():
            actual_names.append(
                path.relative_to(PREDICATE_REGISTRY_V2_ROOT).as_posix()
            )
            if len(actual_names) > MAX_CAUSAL_INPUT_FILES:
                raise ValueError(
                    "Predicate Registry v2 profile exceeds its file-count ceiling"
                )
    actual_names.sort()
    declared_names = sorted(clean_text(row.get("path")) for row in rows)
    if actual_names != declared_names:
        raise ValueError(
            "Predicate Registry v2 profile inventory differs from its lock"
        )
    identity_lines: list[str] = []
    for row in sorted(rows, key=lambda item: clean_text(item.get("path"))):
        relative = clean_text(row.get("path"))
        expected_bytes = row.get("bytes")
        expected_sha256 = clean_text(row.get("sha256"))
        path = PREDICATE_REGISTRY_V2_ROOT / relative
        if (
            not isinstance(expected_bytes, int)
            or isinstance(expected_bytes, bool)
            or expected_bytes < 1
            or not path.is_file()
            or path.stat().st_size != expected_bytes
            or sha256_file(path) != expected_sha256
        ):
            raise ValueError(
                "Predicate Registry v2 profile file differs from its lock: "
                + relative
            )
        identity_lines.append(
            f"{relative}\t{expected_bytes}\t{expected_sha256}\n"
        )
    identity_sha256 = sha256_bytes("".join(identity_lines).encode("utf-8"))
    if identity_sha256 != PREDICATE_REGISTRY_V2_IDENTITY_SHA256:
        raise ValueError("Predicate Registry v2 aggregate identity differs")
    schema_row = next(
        (
            row
            for row in rows
            if row.get("path") == "predicate-registry.schema.json"
        ),
        None,
    )
    if (
        not isinstance(schema_row, dict)
        or schema_row.get("bytes") != PREDICATE_REGISTRY_V2_SCHEMA_BYTES
        or schema_row.get("sha256") != PREDICATE_REGISTRY_V2_SCHEMA_SHA256
    ):
        raise ValueError("Predicate Registry v2 schema lock differs")
    consumer_lock = load_json(EXPLORER_CONSUMER_LOCK_PATH)
    consumer_contract = consumer_lock.get("consumer", {}).get(
        "predicate_registry"
    )
    expected_consumer_contract = {
        "supported_schemas": [
            "okf-predicate-registry.v1",
            "okf-predicate-registry.v2",
        ],
        "required_projection_schema": "okf-predicate-registry.v2",
        "profile": PREDICATE_REGISTRY_V2_PROFILE_URL,
        "source_release": {
            "repository": "https://github.com/chris-page-gov/okf-explorer",
            "version": "0.6.1",
            "tag": "v0.6.1",
            "annotated_tag_object_sha": (
                PREDICATE_REGISTRY_V2_SOURCE_TAG_OBJECT
            ),
            "commit_sha": PREDICATE_REGISTRY_V2_SOURCE_COMMIT,
            "immutable_release_id": 368556872,
            "published_at": "2026-08-11T12:34:04Z",
        },
        "profile_lock": {
            "url": PREDICATE_REGISTRY_V2_LOCK_URL,
            "local_path": PREDICATE_REGISTRY_V2_LOCK_PATH.relative_to(
                ROOT
            ).as_posix(),
            "bytes": 744,
            "sha256": PREDICATE_REGISTRY_V2_LOCK_SHA256,
            "identity_sha256": PREDICATE_REGISTRY_V2_IDENTITY_SHA256,
        },
        "schema": {
            "url": PREDICATE_REGISTRY_V2_SCHEMA_ID,
            "local_path": PREDICATE_REGISTRY_V2_SCHEMA_PATH.relative_to(
                ROOT
            ).as_posix(),
            "bytes": PREDICATE_REGISTRY_V2_SCHEMA_BYTES,
            "sha256": PREDICATE_REGISTRY_V2_SCHEMA_SHA256,
        },
    }
    if consumer_contract != expected_consumer_contract:
        raise ValueError(
            "Explorer consumer lock has a different Predicate Registry v2 contract"
        )
    return {
        "status": "conformant",
        "profile": PREDICATE_REGISTRY_V2_PROFILE_URL,
        "file_count": len(rows),
        "identity_sha256": identity_sha256,
        "lock_path": PREDICATE_REGISTRY_V2_LOCK_PATH.relative_to(
            ROOT
        ).as_posix(),
        "lock_sha256": PREDICATE_REGISTRY_V2_LOCK_SHA256,
        "schema_path": PREDICATE_REGISTRY_V2_SCHEMA_PATH.relative_to(
            ROOT
        ).as_posix(),
        "schema_bytes": PREDICATE_REGISTRY_V2_SCHEMA_BYTES,
        "schema_sha256": PREDICATE_REGISTRY_V2_SCHEMA_SHA256,
        "network_resolution_allowed": False,
        "profile_source_version": "0.6.1",
        "profile_source_commit_sha": PREDICATE_REGISTRY_V2_SOURCE_COMMIT,
    }


@functools.lru_cache(maxsize=1)
def load_predicate_registry_v2_validator() -> Draft202012Validator:
    """Load the exact locked v2 schema without remote context resolution."""
    validate_predicate_registry_profile_lock()
    schema = load_json(PREDICATE_REGISTRY_V2_SCHEMA_PATH)
    if (
        schema.get("$id") != PREDICATE_REGISTRY_V2_SCHEMA_ID
        or schema.get("$schema") != SEMANTIC_ASSERTION_SCHEMA_DRAFT
    ):
        raise ValueError("Predicate Registry v2 schema identity differs")

    def references(value: Any) -> Iterable[str]:
        if isinstance(value, dict):
            reference = value.get("$ref")
            if isinstance(reference, str):
                yield reference
            for child in value.values():
                yield from references(child)
        elif isinstance(value, list):
            for child in value:
                yield from references(child)

    remote_references = sorted(
        reference
        for reference in references(schema)
        if not reference.startswith("#/")
    )
    if remote_references:
        raise ValueError(
            "Predicate Registry v2 schema contains remote references: "
            + ", ".join(remote_references)
        )
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


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
    path.write_bytes(compact_json(value))


def canonical_profile_sha256() -> str:
    profile = load_json(ROOT / "domain-profile" / "domain-profile.json")
    return sha256_bytes(compact_json(profile))


def profile_pack_root_sha256() -> str:
    profile_root = ROOT / "domain-profile"
    expected_inventory = sorted(
        ("CHECKSUMS.sha256", *STAGE1_SEMANTIC_PROFILE_PACK_MEMBERS)
    )
    if _ACTIVE_BUILD_INPUT_SNAPSHOT is not None:
        prefix = "domain-profile/"
        actual_inventory = sorted(
            relative.removeprefix(prefix)
            for relative in _ACTIVE_BUILD_INPUT_SNAPSHOT.files
            if relative.startswith(prefix)
        )
    else:
        actual_inventory = []
        for path in profile_root.iterdir():
            if path.is_symlink() or not path.is_file():
                raise ValueError(
                    "domain profile pack contains a non-regular entry: "
                    + path.name
                )
            actual_inventory.append(path.name)
        actual_inventory.sort()
    if actual_inventory != expected_inventory:
        raise ValueError(
            "domain profile pack inventory differs from its governed contract: "
            f"expected {expected_inventory!r}, observed {actual_inventory!r}"
        )

    checksum_lines: list[str] = []
    for member in STAGE1_SEMANTIC_PROFILE_PACK_MEMBERS:
        payload = repository_bytes(
            profile_root / member,
            maximum_bytes=MAX_CAUSAL_INPUT_FILE_BYTES,
            field=f"domain profile pack member {member}",
        )
        checksum_lines.append(f"{sha256_bytes(payload)}  {member}\n")
    checksum_material = "".join(checksum_lines)
    computed_root = sha256_bytes(checksum_material.encode("utf-8"))
    text = repository_text(
        profile_root / "CHECKSUMS.sha256",
        maximum_bytes=MAX_CAUSAL_INPUT_FILE_BYTES,
        field="domain profile checksums",
    )
    expected_text = (
        checksum_material + f"# pack-root-sha256: {computed_root}\n"
    )
    if text != expected_text:
        raise ValueError(
            "domain profile pack checksums do not exactly match every member"
        )
    if computed_root != STAGE1_SEMANTIC_PROFILE_PACK_ROOT_SHA256:
        raise ValueError(
            "domain profile pack root differs from the governed builder contract"
        )
    return computed_root


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
    if any(not isinstance(row, dict) for row in [*source_rows, *rights_rows]):
        raise ValueError("source and rights register rows must be objects")
    sources = {clean_text(row.get("id")): row for row in source_rows}
    rights = {clean_text(row.get("id")): row for row in rights_rows}
    if "" in sources or len(sources) != len(source_rows):
        raise ValueError("source-register IDs must be non-empty and unique")
    if "" in rights or len(rights) != len(rights_rows):
        raise ValueError("rights assessment IDs must be non-empty and unique")
    evidence_register_path = ROOT / "domain-profile" / "evidence-register.jsonl"
    evidence_ids = {
        clean_text(json.loads(line).get("id"))
        for line in repository_text(
            evidence_register_path,
            maximum_bytes=MAX_CAUSAL_INPUT_FILE_BYTES,
            field="domain-profile evidence register",
        ).splitlines()
        if line.strip()
    }
    if not evidence_ids or "" in evidence_ids:
        raise ValueError("domain-profile evidence register IDs are invalid")
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
    for family_id, family in sources.items():
        stage1_source_id = clean_text(family.get("stage1_source_id"))
        publisher_treatment = clean_text(family.get("publisher_treatment"))
        classification_policy = clean_text(family.get("classification_policy"))
        evidence_refs = family.get("evidence_refs")
        if (
            not stage1_source_id
            or publisher_treatment
            not in {
                "source-native-organisations",
                "matched-record-inheritance",
                "governed-source-record",
            }
            or classification_policy
            not in {"family-default-allowed", "per-record-required"}
            or not isinstance(evidence_refs, list)
            or not evidence_refs
            or len(evidence_refs) != len(set(evidence_refs))
            or any(not clean_text(value) for value in evidence_refs)
            or not set(evidence_refs) <= evidence_ids
        ):
            raise ValueError(
                f"source family {family_id} has invalid governed crosswalk fields"
            )
        rights_id = clean_text(family.get("primary_rights_ref"))
        if rights_id not in rights:
            raise ValueError(
                f"source family {family_id} has an unknown primary rights assessment"
            )
        if family_id not in rights[rights_id]["source_family_ids"]:
            raise ValueError(
                f"primary rights assessment {rights_id} does not cover "
                f"source family {family_id}"
            )
        overrides = family.get("rights_overrides", [])
        if not isinstance(overrides, list):
            raise ValueError(
                f"source family {family_id} rights overrides must be an array"
            )
        governed_hosts: set[str] = set()
        for override in overrides:
            if not isinstance(override, dict):
                raise ValueError(
                    f"source family {family_id} rights override must be an object"
                )
            host = clean_text(override.get("canonical_source_host"))
            override_rights_id = clean_text(override.get("primary_rights_ref"))
            access_state = clean_text(override.get("access_state"))
            rights_state = clean_text(override.get("rights_state"))
            additional_evidence_refs = override.get(
                "additional_evidence_refs", []
            )
            if (
                not host
                or host != host.casefold()
                or host not in PUBLIC_SOURCE_HOSTS
                or not re.fullmatch(
                    r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+"
                    r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?",
                    host,
                )
                or host in governed_hosts
                or override_rights_id not in rights
                or not access_state
                or not rights_state
                or not isinstance(additional_evidence_refs, list)
                or len(additional_evidence_refs)
                != len(set(additional_evidence_refs))
                or not set(additional_evidence_refs) <= evidence_ids
            ):
                raise ValueError(
                    f"source family {family_id} has an invalid rights override"
                )
            governed_hosts.add(host)
            if family_id not in rights[override_rights_id]["source_family_ids"]:
                raise ValueError(
                    f"override rights assessment {override_rights_id} does not "
                    f"cover source family {family_id}"
                )
    stage1_source_ids = [
        clean_text(family.get("stage1_source_id")) for family in sources.values()
    ]
    if len(stage1_source_ids) != len(set(stage1_source_ids)):
        raise ValueError("source-family Stage 1 IDs must be unique")
    stage1 = load_stage1_semantic_authority()
    stage1_sources = stage1["source_by_runtime_family"]
    if set(sources) != set(stage1_sources):
        raise ValueError("source register and Stage 1 source-family sets differ")
    for family_id, family in sources.items():
        stage1_source = stage1_sources[family_id]
        publisher_binding = stage1_source.get("publisher_binding")
        if (
            clean_text(family.get("stage1_source_id"))
            != clean_text(stage1_source.get("id"))
            or clean_text(family.get("primary_rights_ref"))
            != clean_text(stage1_source.get("rights_ref"))
            or sorted(family.get("evidence_refs", []))
            != sorted(stage1_source.get("evidence_refs", []))
            or not isinstance(publisher_binding, dict)
            or clean_text(family.get("publisher_treatment"))
            != clean_text(publisher_binding.get("strategy"))
        ):
            raise ValueError(
                f"source family {family_id} differs from its Stage 1 crosswalk"
            )
    return sources, rights


def governed_source_family_rights_policy(
    family: dict[str, Any], canonical_source_url: str
) -> dict[str, str]:
    """Select the exact governed family policy, including a host override."""
    host = (urlparse(canonical_source_url).hostname or "").casefold()
    matches = [
        override
        for override in family.get("rights_overrides", [])
        if clean_text(override.get("canonical_source_host")) == host
    ]
    if len(matches) > 1:
        raise ValueError(
            f"source family {clean_text(family.get('id'))} has ambiguous rights overrides"
        )
    selected = matches[0] if matches else family
    return {
        "primary_rights_ref": clean_text(selected.get("primary_rights_ref")),
        "access_state": clean_text(selected.get("access_state")) or "unknown",
        "rights_state": clean_text(selected.get("rights_state")) or "unknown",
        "canonical_source_host": (
            clean_text(selected.get("canonical_source_host")) if matches else ""
        ),
        "additional_evidence_refs": list(
            selected.get("additional_evidence_refs", [])
        ),
    }


@functools.lru_cache(maxsize=1)
def load_stage1_semantic_authority() -> dict[str, Any]:
    """Load and validate the Stage 1 tables that exclusively govern semantics."""
    profile_path = ROOT / "domain-profile" / "domain-profile.json"
    schema_path = ROOT / "schemas" / "domain-profile.schema.json"
    profile = load_json(profile_path)
    profile_identity = sha256_bytes(compact_canonical_json(profile))
    if profile_identity != STAGE1_SEMANTIC_PROFILE_SHA256:
        raise ValueError(
            "Stage 1 canonical semantic profile identity differs: "
            f"{profile_identity} != {STAGE1_SEMANTIC_PROFILE_SHA256}"
        )
    schema = load_json(schema_path)
    Draft202012Validator.check_schema(schema)
    failures = sorted(
        Draft202012Validator(
            schema, format_checker=FormatChecker()
        ).iter_errors(profile),
        key=lambda error: list(error.absolute_path),
    )
    if failures:
        detail = "; ".join(
            f"{error.json_path}: {error.message}" for error in failures[:12]
        )
        raise ValueError(f"domain profile failed its governed schema: {detail}")
    semantic_model = profile.get("semantic_model")
    if not isinstance(semantic_model, dict):
        raise ValueError("domain profile lacks a semantic model")
    authority = semantic_model.get("semantic_authority")
    if (
        not isinstance(authority, dict)
        or authority.get("schema")
        != "okf-landregistry-stage1-semantic-authority.v2"
    ):
        raise ValueError("domain profile lacks the Stage 1 semantic authority")

    def resolve_document_pointer(
        document: Any, pointer: str, *, field: str
    ) -> Any:
        if not pointer.startswith("#/") or "//" in pointer:
            raise ValueError(f"Stage 1 authority has an invalid {field}")
        value: Any = document
        for encoded in pointer[2:].split("/"):
            token = encoded.replace("~1", "/").replace("~0", "~")
            if isinstance(value, dict) and token in value:
                value = value[token]
                continue
            if isinstance(value, list) and token.isdigit():
                ordinal = int(token)
                if ordinal < len(value):
                    value = value[ordinal]
                    continue
            raise ValueError(
                f"Stage 1 authority pointer {field} does not resolve: {pointer}"
            )
        return value

    def resolve_pointer(field: str) -> Any:
        pointer = clean_text(authority.get(field))
        return resolve_document_pointer(profile, pointer, field=field)

    def declared_set(field: str, values: Iterable[str]) -> None:
        declaration = authority.get("declared_sets", {}).get(field)
        ordered = sorted(values)
        if (
            not isinstance(declaration, dict)
            or declaration.get("ordering")
            != "sorted-codepoint-compact-json"
            or declaration.get("count") != len(ordered)
            or clean_text(declaration.get("sha256"))
            != sha256_bytes(
                json.dumps(
                    ordered,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
        ):
            raise ValueError(f"Stage 1 declared set differs: {field}")

    def delegated_authorities() -> dict[str, dict[str, Any]]:
        rows = authority.get("delegated_authorities")
        if not isinstance(rows, list) or any(
            not isinstance(row, dict) for row in rows
        ):
            raise ValueError("Stage 1 delegated authorities are invalid")
        indexed = {clean_text(row.get("id")): row for row in rows}
        if "" in indexed or len(indexed) != len(rows):
            raise ValueError("Stage 1 delegated authority IDs collide")
        for identifier, delegation in indexed.items():
            relative_path = clean_text(delegation.get("path"))
            path = ROOT / relative_path
            if (
                not relative_path
                or Path(relative_path).is_absolute()
                or ".." in Path(relative_path).parts
                or not path.is_file()
                or path.is_symlink()
            ):
                raise ValueError(
                    f"Stage 1 delegated authority path is unsafe: {identifier}"
                )
            if sha256_file(path) != clean_text(delegation.get("sha256")):
                raise ValueError(
                    f"Stage 1 delegated authority digest differs: {identifier}"
                )
            document = load_json(path)
            if clean_text(document.get("schema")) != clean_text(
                delegation.get("schema_id")
            ):
                raise ValueError(
                    f"Stage 1 delegated authority schema differs: {identifier}"
                )
            delegated_version = clean_text(delegation.get("version"))
            document_version = clean_text(document.get("version"))
            if (
                delegated_version == "digest-pinned-unversioned"
                and document_version
            ) or (
                delegated_version != "digest-pinned-unversioned"
                and document_version != delegated_version
            ):
                raise ValueError(
                    f"Stage 1 delegated authority version differs: {identifier}"
                )
            record_rows = resolve_document_pointer(
                document,
                clean_text(delegation.get("record_pointer")),
                field=identifier + " record_pointer",
            )
            if (
                not isinstance(record_rows, list)
                or len(record_rows) != delegation.get("record_count")
            ):
                raise ValueError(
                    f"Stage 1 delegated authority count differs: {identifier}"
                )
        return indexed

    def unique_rows(rows: Any, field: str) -> dict[str, dict[str, Any]]:
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError(f"Stage 1 {field} must be an array of objects")
        indexed = {clean_text(row.get("id")): row for row in rows}
        if "" in indexed or len(indexed) != len(rows):
            raise ValueError(f"Stage 1 {field} IDs must be non-empty and unique")
        return indexed

    entity_type_rows = resolve_pointer("class_decisions_pointer")
    relationship_type_rows = resolve_pointer("relationship_decisions_pointer")
    relationship_plane_rows = resolve_pointer(
        "relationship_plane_decisions_pointer"
    )
    derivation_rule_rows = resolve_pointer("derivation_rule_decisions_pointer")
    source_native_rows = resolve_pointer(
        "source_native_class_decisions_pointer"
    )
    controlled_term_rows = resolve_pointer(
        "controlled_vocabulary_decisions_pointer"
    )
    jurisdiction_rows = resolve_pointer("jurisdiction_decisions_pointer")
    source_crosswalk_rows = resolve_pointer(
        "source_rights_evidence_crosswalk_pointer"
    )
    identity_scheme_rows = resolve_pointer("identity_decisions_pointer")
    rights_rows = resolve_pointer("rights_decisions_pointer")
    entity_types = unique_rows(entity_type_rows, "entity types")
    relationship_types = unique_rows(
        relationship_type_rows, "relationship types"
    )
    relationship_planes = unique_rows(
        relationship_plane_rows, "relationship planes"
    )
    derivation_rules = unique_rows(derivation_rule_rows, "derivation rules")
    if not isinstance(source_native_rows, list) or any(
        not isinstance(row, dict) for row in source_native_rows
    ):
        raise ValueError(
            "Stage 1 source-native class decisions must be an array of objects"
        )
    class_decisions_by_native_type = {
        clean_text(row.get("source_native_type")): row
        for row in source_native_rows
        if isinstance(row, dict)
    }
    if "" in class_decisions_by_native_type or len(
        class_decisions_by_native_type
    ) != len(source_native_rows):
        raise ValueError(
            "Stage 1 source-native class decision IDs must be non-empty and unique"
        )
    active_relationships = {
        clean_text(row.get("predicate_iri")): row
        for row in relationship_types.values()
        if row.get("implementation_state") == "active-emitted"
    }
    zero_relationships = {
        clean_text(row.get("predicate_iri")): row
        for row in relationship_types.values()
        if row.get("implementation_state") == "authorised-zero-evidence"
    }
    if (
        not active_relationships
        or set(active_relationships) & set(zero_relationships)
        or len(active_relationships) + len(zero_relationships)
        != len(relationship_types)
        or any(
            row.get("implementation_gap")
            != "planned/no-governed-endpoint-evidence"
            for row in zero_relationships.values()
        )
    ):
        raise ValueError("Stage 1 relationship implementation closure is invalid")
    relationship_planes_by_iri = {
        clean_text(row.get("iri")): row for row in relationship_planes.values()
    }
    relationship_planes_by_name = {
        clean_text(row.get("name")): row for row in relationship_planes.values()
    }
    if (
        len(relationship_planes) != 1
        or len(relationship_planes_by_iri) != len(relationship_planes)
        or len(relationship_planes_by_name) != len(relationship_planes)
    ):
        raise ValueError("Stage 1 relationship-plane closure is invalid")
    core_plane = relationship_planes.get("PLANE-CORE")
    expected_core_plane = {
        "iri": RICH_RELATIONSHIP_PLANE_IRI,
        "name": "core",
        "default": True,
        "lifecycle": "active",
        "implementation_state": "active-emitted",
        "assertion_statuses": ["normalized"],
    }
    if core_plane is None or any(
        core_plane.get(field) != expected
        for field, expected in expected_core_plane.items()
    ):
        raise ValueError("Stage 1 core relationship-plane declaration differs")

    derivation_rules_by_iri = {
        governed_absolute_http_iri(
            row.get("iri"), field=f"Stage 1 derivation rule {identifier} IRI"
        ): row
        for identifier, row in derivation_rules.items()
    }
    if len(derivation_rules_by_iri) != len(derivation_rules):
        raise ValueError("Stage 1 derivation-rule IRIs collide")
    active_relationship_ids = {
        clean_text(row.get("id")) for row in active_relationships.values()
    }
    covered_relationship_ids: list[str] = []
    source_observation_rules: list[dict[str, Any]] = []
    for identifier, rule in derivation_rules.items():
        if rule.get("implementation_state") != "active-emitted":
            raise ValueError(
                f"Stage 1 derivation rule is not active: {identifier}"
            )
        role = clean_text(rule.get("rule_role"))
        if role == "source-observation":
            source_observation_rules.append(rule)
            if "relationship_type_refs" in rule:
                raise ValueError(
                    "Stage 1 source-observation rule has relationship refs"
                )
            continue
        if role != "relationship-derivation":
            raise ValueError(
                f"Stage 1 derivation rule has an unknown role: {identifier}"
            )
        refs = rule.get("relationship_type_refs")
        if (
            not isinstance(refs, list)
            or not refs
            or any(
                not isinstance(ref, str) or ref not in active_relationship_ids
                for ref in refs
            )
        ):
            raise ValueError(
                f"Stage 1 derivation rule has invalid relationship refs: {identifier}"
            )
        covered_relationship_ids.extend(refs)
    if (
        len(source_observation_rules) != 1
        or set(covered_relationship_ids) != active_relationship_ids
        or any(
            count != 1
            for count in Counter(covered_relationship_ids).values()
        )
    ):
        raise ValueError("Stage 1 derivation-rule closure is invalid")
    controlled_terms = unique_rows(
        controlled_term_rows,
        "controlled vocabulary terms",
    )
    jurisdictions = unique_rows(
        jurisdiction_rows, "jurisdiction decisions"
    )
    source_rows = unique_rows(source_crosswalk_rows, "source rows")
    identity_schemes = unique_rows(identity_scheme_rows, "identity schemes")
    rights = unique_rows(rights_rows, "rights decisions")
    identity_families: dict[str, dict[str, Any]] = {}
    for scheme in identity_schemes.values():
        families = scheme.get("identity_families", [])
        if not isinstance(families, list) or any(
            not isinstance(family, dict) for family in families
        ):
            raise ValueError("Stage 1 identity families must be arrays of objects")
        for family in families:
            identifier = clean_text(family.get("id"))
            if not identifier or identifier in identity_families:
                raise ValueError("Stage 1 identity family IDs collide")
            identity_families[identifier] = family
    rule_family = identity_families.get("IDF-RULE")
    if (
        rule_family is None
        or rule_family.get("membership_policy") != "exact-closed-set"
        or rule_family.get("closed_member_iris_pointer")
        != "#/semantic_model/derivation_rules"
        or rule_family.get("closed_member_iri_field") != "iri"
    ):
        raise ValueError(
            "Stage 1 rule identity family does not bind its exact member set"
        )
    source_by_runtime_family: dict[str, dict[str, Any]] = {}
    for source in source_rows.values():
        source_families = source.get("source_families")
        if not isinstance(source_families, list) or len(source_families) != 1:
            raise ValueError("Stage 1 source rows require one runtime source family")
        runtime_family = clean_text(source_families[0])
        if not runtime_family or runtime_family in source_by_runtime_family:
            raise ValueError("Stage 1 runtime source-family crosswalk collides")
        source_by_runtime_family[runtime_family] = source
    if len(source_by_runtime_family) != 17:
        raise ValueError("Stage 1 source-family crosswalk must close 17 families")
    declared_set("entity_type_ids", entity_types)
    active_entity_types = {
        identifier: row
        for identifier, row in entity_types.items()
        if row.get("implementation_state") == "active-emitted"
    }
    zero_entity_types = {
        identifier: row
        for identifier, row in entity_types.items()
        if row.get("implementation_state") == "authorised-zero-evidence"
    }
    active_entity_classes = {
        class_iri
        for row in active_entity_types.values()
        for class_iri in row.get("class_iris", [])
    }
    zero_entity_classes = {
        class_iri
        for row in zero_entity_types.values()
        for class_iri in row.get("class_iris", [])
    }
    if active_entity_classes & zero_entity_classes:
        raise ValueError("Stage 1 active and zero-use entity classes overlap")
    expected_declared_sets = {
        "active_entity_class_iris",
        "active_entity_type_ids",
        "active_relationship_predicate_iris",
        "authorised_zero_entity_class_iris",
        "authorised_zero_entity_type_ids",
        "authorised_zero_relationship_predicate_iris",
        "entity_type_ids",
        "identity_family_ids",
        "derivation_rule_iris",
        "relationship_type_ids",
        "relationship_plane_iris",
        "rights_ids",
        "source_family_ids",
        "source_native_types",
    }
    if set(authority.get("declared_sets", {})) != expected_declared_sets:
        raise ValueError("Stage 1 declared-set inventory differs")
    declared_set("active_entity_type_ids", active_entity_types)
    declared_set("authorised_zero_entity_type_ids", zero_entity_types)
    declared_set("active_entity_class_iris", active_entity_classes)
    declared_set(
        "authorised_zero_entity_class_iris", zero_entity_classes
    )
    declared_set("relationship_type_ids", relationship_types)
    declared_set("relationship_plane_iris", relationship_planes_by_iri)
    declared_set("derivation_rule_iris", derivation_rules_by_iri)
    declared_set("active_relationship_predicate_iris", active_relationships)
    declared_set(
        "authorised_zero_relationship_predicate_iris", zero_relationships
    )
    declared_set(
        "source_native_types", class_decisions_by_native_type
    )
    declared_set("identity_family_ids", identity_families)
    declared_set("rights_ids", rights)
    declared_set("source_family_ids", source_by_runtime_family)
    delegations = delegated_authorities()
    publisher_delegation = delegations.get("AUTH-PUBLISHER-REGISTRY")
    publisher_path = (
        ROOT / "domain-profile" / clean_text(authority.get("publisher_registry_path"))
    ).resolve()
    if (
        publisher_delegation is None
        or publisher_path != (ROOT / publisher_delegation["path"]).resolve()
        or clean_text(authority.get("publisher_registry_class_field"))
        != "publishers[*].class_iris"
    ):
        raise ValueError("Stage 1 publisher authority delegation differs")
    return {
        "profile": profile,
        "profile_sha256": profile_identity,
        "entity_types": entity_types,
        "relationship_types": relationship_types,
        "relationship_planes": relationship_planes,
        "relationship_planes_by_iri": relationship_planes_by_iri,
        "relationship_planes_by_name": relationship_planes_by_name,
        "derivation_rules": derivation_rules,
        "derivation_rules_by_iri": derivation_rules_by_iri,
        "active_relationships": active_relationships,
        "zero_relationships": zero_relationships,
        "class_decisions_by_native_type": class_decisions_by_native_type,
        "controlled_terms": controlled_terms,
        "jurisdictions": jurisdictions,
        "source_rows": source_rows,
        "source_by_runtime_family": source_by_runtime_family,
        "identity_schemes": identity_schemes,
        "identity_families": identity_families,
        "rights": rights,
        "delegated_authorities": delegations,
    }


def stage1_entity_type_classes(entity_type_id: str) -> list[str]:
    """Return the exact absolute classes authorised for one governed type."""
    row = load_stage1_semantic_authority()["entity_types"].get(entity_type_id)
    if row is None or row.get("implementation_state") != "active-emitted":
        raise ValueError(f"Stage 1 entity type is not active: {entity_type_id}")
    class_iris = row.get("class_iris")
    if (
        not isinstance(class_iris, list)
        or not class_iris
        or len(class_iris) != len(set(class_iris))
        or any(
            not isinstance(class_iri, str)
            or not class_iri.startswith(("http://", "https://"))
            for class_iri in class_iris
        )
    ):
        raise ValueError(f"Stage 1 entity type has invalid classes: {entity_type_id}")
    for class_iri in class_iris:
        governed_absolute_http_iri(
            class_iri, field=f"Stage 1 {entity_type_id} class IRI"
        )
    return list(class_iris)


def stage1_authorised_zero_entity_type_classes(
    entity_type_id: str,
) -> list[str]:
    """Return exact classes for one governed zero-evidence entity type."""
    row = load_stage1_semantic_authority()["entity_types"].get(entity_type_id)
    if (
        row is None
        or row.get("implementation_state") != "authorised-zero-evidence"
    ):
        raise ValueError(
            f"Stage 1 entity type is not authorised zero-evidence: {entity_type_id}"
        )
    class_iris = row.get("class_iris")
    if (
        not isinstance(class_iris, list)
        or not class_iris
        or len(class_iris) != len(set(class_iris))
    ):
        raise ValueError(
            f"Stage 1 zero-evidence entity type has invalid classes: {entity_type_id}"
        )
    for class_iri in class_iris:
        governed_absolute_http_iri(
            class_iri,
            field=f"Stage 1 zero-evidence {entity_type_id} class IRI",
        )
    return list(class_iris)


def stage1_identity_family(
    family_id: str,
    *,
    expected_role: str | None = None,
) -> dict[str, Any]:
    """Resolve one exact active identity family from Stage 1 authority."""
    family = load_stage1_semantic_authority()["identity_families"].get(
        family_id
    )
    if (
        family is None
        or family.get("implementation_state") != "active-emitted"
        or not clean_text(family.get("iri_pattern"))
        or not clean_text(family.get("route_pattern"))
        or (
            expected_role is not None
            and clean_text(family.get("identity_role")) != expected_role
        )
    ):
        raise ValueError(f"Stage 1 identity family is not active: {family_id}")
    return family


def validate_stage1_identity(
    family_id: str,
    identity: Any,
    *,
    expected_role: str | None = None,
) -> str:
    """Validate an emitted identity against its exact Stage 1 family."""
    family = stage1_identity_family(
        family_id, expected_role=expected_role
    )
    iri = governed_absolute_http_iri(
        identity, field=f"Stage 1 {family_id} identity"
    )
    pattern = clean_text(family["iri_pattern"])
    if pattern.startswith(("http://", "https://")):
        pieces = re.split(r"(<[^<>]+>)", pattern)
        expression = "".join(
            r"[^/?#]+" if piece.startswith("<") else re.escape(piece)
            for piece in pieces
            if piece
        )
        if not re.fullmatch(expression, iri):
            raise ValueError(
                f"identity differs from Stage 1 family {family_id}: {iri}"
            )
        if (
            family_id == "IDF-RULE"
            and iri
            not in load_stage1_semantic_authority()["derivation_rules_by_iri"]
        ):
            raise ValueError(
                f"rule identity is not an exact Stage 1 member: {iri}"
            )
        return iri
    parsed = urlparse(iri)
    if family_id == "IDF-EXTERNAL-PUBLISHER":
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"www.gov.uk", "data.gov.uk"}
            or (
                parsed.hostname == "www.gov.uk"
                and not parsed.path.startswith("/government/organisations/")
            )
        ):
            raise ValueError("external publisher identity is outside Stage 1")
        return iri
    if family_id == "IDF-EXTERNAL-GITHUB-ORGANISATION":
        if (
            parsed.scheme != "https"
            or parsed.hostname != "github.com"
            or not re.fullmatch(r"/[^/]+", parsed.path)
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("GitHub organisation identity is outside Stage 1")
        return iri
    raise ValueError(
        f"Stage 1 identity family has no executable IRI pattern: {family_id}"
    )


def stage1_core_relationship_plane() -> dict[str, Any]:
    """Return the one active default relationship plane declared by Stage 1."""
    plane = load_stage1_semantic_authority()[
        "relationship_planes_by_name"
    ].get("core")
    if (
        plane is None
        or plane.get("implementation_state") != "active-emitted"
        or plane.get("lifecycle") != "active"
        or plane.get("default") is not True
        or clean_text(plane.get("iri")) != RICH_RELATIONSHIP_PLANE_IRI
    ):
        raise ValueError("Stage 1 core relationship plane is not active")
    return plane


def validate_stage1_assertion_plane(
    assertion_plane: Any,
    assertion_status: Any,
    *,
    plane_iri: Any | None = None,
) -> dict[str, Any]:
    """Validate an assertion's named plane, status and optional plane IRI."""
    plane = stage1_core_relationship_plane()
    if (
        clean_text(assertion_plane) != clean_text(plane.get("name"))
        or clean_text(assertion_status)
        not in plane.get("assertion_statuses", [])
        or (
            plane_iri is not None
            and clean_text(plane_iri) != clean_text(plane.get("iri"))
        )
    ):
        raise ValueError("relationship assertion plane/status is outside Stage 1")
    return plane


def validate_stage1_relationship_rule(
    predicate_iri: Any,
    rule_iri: Any,
) -> dict[str, Any]:
    """Bind one emitted predicate to its exact reviewed derivation rule."""
    stage1 = load_stage1_semantic_authority()
    predicate = governed_absolute_http_iri(
        predicate_iri, field="Stage 1 relationship predicate"
    )
    rule = validate_stage1_identity(
        "IDF-RULE", rule_iri, expected_role="runtime-control"
    )
    relationship = stage1["active_relationships"].get(predicate)
    derivation_rule = stage1["derivation_rules_by_iri"].get(rule)
    if (
        relationship is None
        or derivation_rule is None
        or derivation_rule.get("rule_role") != "relationship-derivation"
        or clean_text(relationship.get("id"))
        not in derivation_rule.get("relationship_type_refs", [])
    ):
        raise ValueError(
            "relationship predicate and derivation rule differ from Stage 1"
        )
    return derivation_rule


def validate_stage1_route(
    family_id: str,
    identity: Any,
    route: Any,
    *,
    stable_key: str | None = None,
) -> str:
    """Validate one route-bearing identity against its active Stage 1 family."""
    family = stage1_identity_family(family_id)
    governed_identity = validate_stage1_identity(family_id, identity)
    governed_route = clean_text(route)
    if (
        not governed_route
        or route != governed_route
        or governed_route.startswith("/")
        or ".." in Path(governed_route).parts
        or not re.fullmatch(r"[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9-]*", governed_route)
    ):
        raise ValueError(f"route is not canonical for Stage 1 family {family_id}")

    hash_route_kinds = {
        "IDF-CATALOGUE": "catalogue",
        "IDF-CATALOGUE-RECORD": "catalogue-record",
        "IDF-SOURCE-RESOURCE": "source",
        "IDF-RIGHTS": "rights",
        "IDF-OBSERVATION-ACTIVITY": "activity",
        "IDF-ASSERTION-ACTIVITY": "derivation-activity",
        "IDF-EVIDENCE-RESOURCE": "evidence-resource",
        "IDF-RULE": "rule",
        "IDF-EXTERNAL-PUBLISHER": "publisher",
        "IDF-EXTERNAL-LANGUAGE": "language",
        "IDF-LOCAL-AGENT": "publisher",
    }
    route_kind = hash_route_kinds.get(family_id)
    if route_kind is not None:
        expected_pattern = f"{route_kind}/{route_kind}-<sha256-prefix>"
        if clean_text(family.get("route_pattern")) != expected_pattern:
            raise ValueError(
                f"Stage 1 route pattern is not executable for {family_id}"
            )
        expected_route = semantic_route(route_kind, governed_identity)
    elif family_id in {
        "IDF-DISCOVERY-ENTITY",
        "IDF-EXTERNAL-GITHUB-ORGANISATION",
    }:
        governed_key = clean_text(stable_key)
        if not governed_key or stable_key != governed_key:
            raise ValueError(
                f"Stage 1 record route lacks its stable key: {family_id}"
            )
        if not clean_text(family.get("route_pattern")).startswith(
            "dataset/record-<sha256-prefix>"
        ):
            raise ValueError(
                f"Stage 1 route pattern is not executable for {family_id}"
            )
        expected_route = "dataset/" + explorer_name("record", governed_key)
    elif family_id == "IDF-JURISDICTION":
        matches = [
            decision
            for decision in stage1_jurisdiction_registry().values()
            if clean_text(decision.get("iri")) == governed_identity
        ]
        if len(matches) != 1:
            raise ValueError(
                "Stage 1 jurisdiction identity does not resolve exactly once"
            )
        expected_route = clean_text(matches[0].get("route"))
    else:
        raise ValueError(
            f"Stage 1 identity family has no independent Reader route: {family_id}"
        )
    if governed_route != expected_route:
        raise ValueError(
            f"route differs from Stage 1 family {family_id}: {governed_route}"
        )
    return governed_route


def stage1_native_class_decision(record: dict[str, Any]) -> dict[str, Any]:
    """Resolve one record through the exact source-native Stage 1 decision."""
    source_native_type = clean_text(record.get("source_native_type"))
    decision = load_stage1_semantic_authority()[
        "class_decisions_by_native_type"
    ].get(source_native_type)
    if decision is None:
        raise ValueError(
            "record source-native type lacks a Stage 1 class decision: "
            + source_native_type
        )
    if decision.get("implementation_state") != "active-emitted":
        raise ValueError(
            "record source-native type is not authorised for emission: "
            + source_native_type
        )
    class_iris = decision.get("class_iris")
    if (
        not isinstance(class_iris, list)
        or not class_iris
        or len(class_iris) != len(set(class_iris))
        or any(
            not isinstance(class_iri, str)
            or not class_iri.startswith(("http://", "https://"))
            for class_iri in class_iris
        )
    ):
        raise ValueError(
            "record source-native type has invalid governed classes: "
            + source_native_type
        )
    for class_iri in class_iris:
        governed_absolute_http_iri(
            class_iri,
            field=f"Stage 1 {source_native_type} class IRI",
        )
    return decision


def stage1_language_registry() -> dict[str, dict[str, Any]]:
    """Index exact governed language terms by every accepted source value."""
    registry: dict[str, dict[str, Any]] = {}
    for term in load_stage1_semantic_authority()["controlled_terms"].values():
        if term.get("implementation_state") != "active-emitted":
            continue
        source_values = term.get("source_values")
        if not isinstance(source_values, list) or not source_values:
            raise ValueError("active Stage 1 language term lacks source values")
        for source_value in source_values:
            key = clean_text(source_value)
            if not key or key in registry:
                raise ValueError("Stage 1 language source values collide")
            registry[key] = term
    if not registry:
        raise ValueError("Stage 1 language registry is empty")
    return registry


def stage1_jurisdiction_registry() -> dict[str, dict[str, Any]]:
    """Index exact governed jurisdiction decisions by source label."""
    registry: dict[str, dict[str, Any]] = {}
    for decision in load_stage1_semantic_authority()["jurisdictions"].values():
        if decision.get("implementation_state") != "active-emitted":
            continue
        source_values = decision.get("source_values")
        if not isinstance(source_values, list) or not source_values:
            raise ValueError("active Stage 1 jurisdiction lacks source values")
        for source_value in source_values:
            key = clean_text(source_value)
            if not key or key in registry:
                raise ValueError("Stage 1 jurisdiction source values collide")
            registry[key] = decision
    if not registry:
        raise ValueError("Stage 1 jurisdiction registry is empty")
    return registry


def validate_stage1_retained_native_type_closure(
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    """Require Stage 1 decisions to equal the post-reconciliation type set."""
    retained = {
        clean_text(record.get("source_native_type")) for record in records
    }
    if not retained or "" in retained:
        raise ValueError("retained records contain an empty source-native type")
    decisions = load_stage1_semantic_authority()[
        "class_decisions_by_native_type"
    ]
    governed = set(decisions)
    if retained != governed:
        raise ValueError(
            "Stage 1 source-native decisions differ from retained record types: "
            f"missing={sorted(retained - governed)!r}, "
            f"unused={sorted(governed - retained)!r}"
        )
    non_active = sorted(
        source_native_type
        for source_native_type, decision in decisions.items()
        if decision.get("implementation_state") != "active-emitted"
    )
    if non_active:
        raise ValueError(
            "retained source-native decisions are not active: "
            + ", ".join(non_active)
        )
    return {
        "retained_source_native_types": len(retained),
        "decision_set_sha256": sha256_bytes(canonical_json(sorted(retained))),
    }


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
def load_publisher_registry_entries() -> dict[str, dict[str, Any]]:
    path = ROOT / "source" / "publisher-registry.json"
    payload = load_json(path)
    if payload.get("schema") != "okf-hmlr-publisher-registry.v1":
        raise ValueError("publisher registry has an unsupported schema")
    if payload.get("version") != SOURCE_MODEL_VERSION:
        raise ValueError("publisher registry and builder versions differ")
    rows = payload.get("publishers")
    if not isinstance(rows, list) or not rows:
        raise ValueError("publisher registry must contain publishers")
    governed_event_timestamp(
        payload.get("observed_at"),
        "source/publisher-registry.json observed_at",
    )
    registry: dict[str, dict[str, Any]] = {}
    identifiers: set[str] = set()
    for row in rows:
        name = clean_text(row.get("name")) if isinstance(row, dict) else ""
        identifier = (
            canonical_https_url(row.get("id"), field="publisher identity")
            if name
            else ""
        )
        class_iris = row.get("class_iris") if isinstance(row, dict) else None
        if (
            not name
            or not identifier
            or name in registry
            or identifier in identifiers
            or not isinstance(class_iris, list)
            or not class_iris
            or class_iris != sorted(set(class_iris))
            or any(
                not isinstance(class_iri, str)
                or not class_iri.startswith(("http://", "https://"))
                for class_iri in class_iris
            )
        ):
            raise ValueError(
                "publisher registry names, identities and classes must be governed"
            )
        for class_iri in class_iris:
            governed_absolute_http_iri(
                class_iri, field="publisher registry class IRI"
            )
        local_agent_prefix = urljoin(PUBLICATION_BASE, "id/agent/")
        if identifier.startswith(local_agent_prefix):
            validate_stage1_identity(
                "IDF-LOCAL-AGENT",
                identifier,
                expected_role="project-derived",
            )
        else:
            validate_stage1_identity(
                "IDF-EXTERNAL-PUBLISHER",
                identifier,
                expected_role="source-native-external",
            )
        governed_publisher_classes = set(
            stage1_entity_type_classes("TYPE-PUBLISHER")
        )
        if not set(class_iris) <= governed_publisher_classes:
            raise ValueError(
                f"publisher identity has classes outside Stage 1: {identifier}"
            )
        registry[name] = {**row, "name": name, "id": identifier}
        identifiers.add(identifier)
    return registry


@functools.lru_cache(maxsize=1)
def load_publisher_registry() -> dict[str, str]:
    return {
        name: clean_text(row["id"])
        for name, row in load_publisher_registry_entries().items()
    }


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
    publisher_registry = load_publisher_registry()
    declared_publishers = record.get("publishers")
    if declared_publishers is None:
        explicit_publisher = clean_text(record.get("publisher"))
        if not explicit_publisher:
            raise ValueError("record publisher must be explicitly governed")
        publisher_names = [explicit_publisher]
    elif (
        not isinstance(declared_publishers, list)
        or not declared_publishers
        or any(not isinstance(value, str) for value in declared_publishers)
    ):
        raise ValueError("record publishers must be a non-empty array of names")
    else:
        publisher_names = [clean_text(value) for value in declared_publishers]
    if (
        any(not value for value in publisher_names)
        or len(publisher_names) != len(set(publisher_names))
    ):
        raise ValueError("record publisher names must be non-empty and unique")
    publisher = clean_text(record.get("publisher")) or publisher_names[0]
    if publisher != publisher_names[0]:
        raise ValueError("record primary publisher must be the first declared publisher")
    unknown_publishers = [
        value for value in publisher_names if value not in publisher_registry
    ]
    if unknown_publishers:
        raise ValueError(
            "publisher is absent from the governed registry: "
            f"{unknown_publishers[0]!r}"
        )
    publishers = [
        {"name": name, "id": publisher_registry[name]}
        for name in publisher_names
    ]
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
        "publishers": publishers,
        "publisher_treatment": (
            clean_text(record.get("publisher_treatment"))
            or "governed-source-record"
        ),
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
    if not isinstance(organisations, list):
        raise ValueError("GOV.UK result organisations must be an array")
    publisher_names: list[str] = []
    publisher_ids: set[str] = set()
    publisher_registry = load_publisher_registry()
    for ordinal, organisation in enumerate(organisations):
        if not isinstance(organisation, dict):
            raise ValueError(
                f"GOV.UK organisation {ordinal} must be an object"
            )
        title = clean_text(organisation.get("title"))
        slug = clean_text(organisation.get("slug"))
        organisation_link = clean_text(organisation.get("link"))
        content_id = clean_text(organisation.get("content_id"))
        expected_link = f"/government/organisations/{slug}" if slug else ""
        if (
            not title
            or not slug
            or organisation_link != expected_link
            or not re.fullmatch(
                r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                content_id,
            )
        ):
            raise ValueError(
                f"GOV.UK organisation {ordinal} has incomplete or unsafe identity"
            )
        publisher_id = ensure_https(urljoin("https://www.gov.uk/", organisation_link))
        if publisher_registry.get(title) != publisher_id:
            raise ValueError(
                "GOV.UK organisation does not match the governed publisher registry: "
                f"{title!r}"
            )
        if title in publisher_names or publisher_id in publisher_ids:
            raise ValueError("GOV.UK organisation identity collision")
        publisher_names.append(title)
        publisher_ids.add(publisher_id)
    if not publisher_names:
        raise ValueError("GOV.UK result lacks a governed organisation publisher")
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
            "publisher": publisher_names[0],
            "publishers": publisher_names,
            "publisher_treatment": "source-native-organisations",
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
            "publisher_treatment": "governed-source-record",
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
            "publisher_treatment": "governed-source-record",
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
            "publisher_treatment": "matched-record-inheritance",
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
    if clean_text(governed.get("publisher_treatment")) != clean_text(
        family.get("publisher_treatment")
    ):
        raise ValueError(
            f"record publisher treatment differs from source family: {family_id}"
        )
    family_policy = governed_source_family_rights_policy(
        family, governed["canonical_source_url"]
    )
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
        if family.get("classification_policy") != "family-default-allowed":
            raise ValueError(
                "source-family rights defaults are limited to frozen discovery "
                f"inputs: {family_id}"
            )
        access_state = family_policy["access_state"]
        rights_state = family_policy["rights_state"]
        rights_ref = family_policy["primary_rights_ref"]
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
                set(family["evidence_refs"])
                | set(family_policy["additional_evidence_refs"])
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
    publisher_sources_by_url = {
        url: [
            item
            for item in representations
            if item.get("publisher_treatment") != "matched-record-inheritance"
        ]
        for url, representations in by_url.items()
    }

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
        merged_publishers: list[dict[str, str]] = []
        publisher_names_by_id: dict[str, str] = {}
        publisher_source_items = [
            item
            for item in ordered
            if item.get("publisher_treatment") != "matched-record-inheritance"
        ]
        publisher_inheritance_items: list[dict[str, Any]] = []
        if not publisher_source_items:
            translation_groups = {
                clean_text(item.get("translation_group"))
                for item in ordered
                if clean_text(item.get("translation_group"))
            }
            if len(translation_groups) != 1:
                raise ValueError(
                    "matched-record publisher inheritance lacks one translation "
                    f"group: {url}"
                )
            translation_group = next(iter(translation_groups))
            inherited_candidates = [
                source
                for related in all_records
                if clean_text(related.get("translation_group"))
                == translation_group
                for source in publisher_sources_by_url.get(related["url"], [])
            ]
            inherited_by_identity = {
                (source["source_family"], source["source_native_id"]): source
                for source in inherited_candidates
            }
            publisher_inheritance_items = [
                inherited_by_identity[key] for key in sorted(inherited_by_identity)
            ]
            publisher_source_items = publisher_inheritance_items
            if not publisher_source_items:
                raise ValueError(
                    "matched-record publisher inheritance has no governed source: "
                    + url
                )
        for item in publisher_source_items:
            for publisher in item.get("publishers", []):
                publisher_name = clean_text(publisher.get("name"))
                publisher_id = clean_text(publisher.get("id"))
                if not publisher_name or not publisher_id:
                    raise ValueError(f"record has an incomplete publisher: {url}")
                existing_name = publisher_names_by_id.get(publisher_id)
                if existing_name and existing_name != publisher_name:
                    raise ValueError(f"record has a publisher identity collision: {url}")
                if existing_name is None:
                    publisher_names_by_id[publisher_id] = publisher_name
                    merged_publishers.append(
                        {"name": publisher_name, "id": publisher_id}
                    )
        if not merged_publishers:
            raise ValueError(f"record primary publisher is absent: {url}")
        selected["publisher"] = merged_publishers[0]["name"]
        selected["publisher_id"] = merged_publishers[0]["id"]
        selected["publishers"] = merged_publishers
        selected["publisher_inheritance"] = [
            {
                "id": item["source_native_id"],
                "source_family": item["source_family"],
                "translation_group": clean_text(
                    ordered[0].get("translation_group")
                ),
            }
            for item in publisher_inheritance_items
        ]
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
            "entities": explorer_projection["search_entities"]["path"],
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
            "entities": explorer_projection["search_entities"],
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


def governed_event_timestamp(value: Any, field: str) -> str:
    """Return a governed date or timestamp as a whole-second UTC timestamp."""
    rendered = clean_text(value)
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", rendered):
        rendered += "T00:00:00Z"
    timestamp = _required_utc_timestamp(rendered, field)
    return timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")


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
    for relative, label in (
        ("source/source-register.json", "source/source-register.json"),
        (
            "source/publisher-registry.json",
            "source/publisher-registry.json",
        ),
    ):
        document = load_json(ROOT / relative)
        if not isinstance(document, dict):
            raise ValueError(f"{label} is not an object for build chronology")
        chronology.extend(
            _document_chronology_times(
                document,
                document=label,
                fields=frozenset({"observed_at", "reviewed_at"}),
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
        publisher_match = re.fullmatch(r"publishers\[id=(https://.+)\]", source_field)
        source_document: dict[str, Any] = {}
        if curated_match or search_match or publisher_match:
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
        elif publisher_match:
            if relative != "source/publisher-registry.json":
                raise ValueError(
                    f"CPSV-AP publisher evidence source differs: {evidence_id}"
                )
            rows = source_document.get("publishers", [])
            matches = [
                row
                for row in rows
                if clean_text(row.get("id")) == publisher_match.group(1)
            ]
            locator_kind = "publisher-registry"
            locator_value = publisher_match.group(1)
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
        if publisher_match:
            source_url = value.get("id")
            if (
                claim == "public-organisation-identity"
                and "http://data.europa.eu/m8g/PublicOrganisation"
                not in value.get("class_iris", [])
            ):
                raise ValueError(
                    "CPSV-AP publisher evidence lacks the governed public-organisation "
                    f"class: {evidence_id}"
                )
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
    """Apply the governed identity policy without duplicating known agents."""
    decision = stage1_native_class_decision(record)
    identity_policy = clean_text(decision.get("identity_policy"))
    if identity_policy == "reuse-governed-external-iri":
        governed_identity = canonical_https_url(
            decision.get("identity_iri"),
            field=(
                "Stage 1 identity for "
                + clean_text(record.get("source_native_type"))
            ),
        )
        identity_source_field = clean_text(decision.get("identity_source_field"))
        if not identity_source_field:
            raise ValueError("reused Stage 1 identity lacks its source field")
        observed_identity = canonical_https_url(
            record.get(identity_source_field),
            field="record " + identity_source_field,
        )
        if observed_identity != governed_identity:
            raise ValueError(
                "record identity source field differs from its governed reused IRI: "
                + clean_text(record.get("record_id"))
            )
        family_id = (
            "IDF-EXTERNAL-GITHUB-ORGANISATION"
            if clean_text(record.get("source_native_type"))
            == "repository-organisation"
            else "IDF-EXTERNAL-PUBLISHER"
        )
        return validate_stage1_identity(
            family_id,
            governed_identity,
            expected_role="source-native-external",
        )
    if identity_policy != "derive-local-entity-iri":
        raise ValueError(
            "unsupported Stage 1 record identity policy: " + identity_policy
        )
    identity = urljoin(
        publication_base.rstrip("/") + "/",
        "id/entity/" + clean_text(record["record_id"]),
    )
    return validate_stage1_identity(
        "IDF-DISCOVERY-ENTITY",
        identity,
        expected_role="project-derived",
    )


def semantic_record_route(publication_base: str, record: dict[str, Any]) -> str:
    """Return the route for the governed record entity identity."""
    decision = stage1_native_class_decision(record)
    identity = semantic_record_iri(publication_base, record)
    if clean_text(decision.get("identity_policy")) == "reuse-governed-external-iri":
        if clean_text(record.get("source_native_type")) == "repository-organisation":
            family_id = "IDF-EXTERNAL-GITHUB-ORGANISATION"
            route = "dataset/" + explorer_name(
                "record", clean_text(record["record_id"])
            )
            return validate_stage1_route(
                family_id,
                identity,
                route,
                stable_key=clean_text(record["record_id"]),
            )
        return validate_stage1_route(
            "IDF-EXTERNAL-PUBLISHER",
            identity,
            semantic_route("publisher", identity),
        )
    route = "dataset/" + explorer_name("record", clean_text(record["record_id"]))
    return validate_stage1_route(
        "IDF-DISCOVERY-ENTITY",
        identity,
        route,
        stable_key=clean_text(record["record_id"]),
    )


def semantic_source_resource_iri(
    publication_base: str,
    record: dict[str, Any],
    source_url: str,
) -> str:
    """Mint one shared route-bearing identity per canonical source URL."""
    del record
    identity = semantic_web_iri(source_url)
    source_identity = urljoin(
        publication_base.rstrip("/") + "/",
        "id/source-resource/source-"
        + sha256_bytes(identity.encode("utf-8"))[:24],
    )
    return validate_stage1_identity(
        "IDF-SOURCE-RESOURCE",
        source_identity,
        expected_role="project-derived",
    )


def semantic_jurisdiction_iri(publication_base: str, jurisdiction: str) -> str:
    """Resolve only an exact Stage 1 jurisdiction identity."""
    del publication_base
    decision = stage1_jurisdiction_registry().get(clean_text(jurisdiction))
    if decision is None:
        raise ValueError(
            "jurisdiction lacks an exact Stage 1 decision: "
            + clean_text(jurisdiction)
        )
    return validate_stage1_identity(
        "IDF-JURISDICTION",
        decision.get("iri"),
        expected_role="project-derived",
    )


def semantic_jurisdiction_route(jurisdiction: str) -> str:
    """Return the exact route governed for one Stage 1 jurisdiction label."""
    decision = stage1_jurisdiction_registry().get(clean_text(jurisdiction))
    if decision is None or not clean_text(decision.get("route")):
        raise ValueError(
            "jurisdiction lacks an exact Stage 1 route: "
            + clean_text(jurisdiction)
        )
    return validate_stage1_route(
        "IDF-JURISDICTION",
        decision.get("iri"),
        decision.get("route"),
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
    *,
    validation_receipts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    publication_base = publication_base.rstrip("/") + "/"
    bundle_id = validate_stage1_identity(
        "IDF-BUNDLE",
        urljoin(publication_base, "id/bundle/hmlr-public-estate"),
        expected_role="project-derived",
    )
    bundle_types = sorted(stage1_entity_type_classes("TYPE-BUNDLE"))
    if len(bundle_types) != 1:
        raise ValueError("Stage 1 bundle descriptor must declare one exact class")
    catalog_id = validate_stage1_identity(
        "IDF-CATALOGUE",
        urljoin(publication_base, "id/catalogue/hmlr-public-estate"),
        expected_role="project-derived",
    )
    record_nodes: dict[str, dict[str, Any]] = {}
    publisher_nodes: dict[str, dict[str, Any]] = {}
    rights_nodes: dict[str, dict[str, Any]] = {}
    activity_nodes: dict[str, dict[str, Any]] = {}
    rule_nodes: dict[str, dict[str, Any]] = {}
    language_nodes: dict[str, dict[str, Any]] = {}
    jurisdiction_nodes: dict[str, dict[str, Any]] = {}
    source_nodes: dict[str, dict[str, Any]] = {}
    evidence_resource_nodes: dict[str, dict[str, Any]] = {}
    entity_nodes: dict[str, dict[str, Any]] = {}
    language_registry = stage1_language_registry()
    jurisdiction_registry = stage1_jurisdiction_registry()
    for language in language_registry.values():
        language_id = validate_stage1_identity(
            "IDF-EXTERNAL-LANGUAGE",
            language.get("iri"),
            expected_role="source-native-external",
        )
        language_nodes[language_id] = {
            "@id": language_id,
            "@type": [clean_text(language["class_iri"])],
            "route": validate_stage1_route(
                "IDF-EXTERNAL-LANGUAGE",
                language_id,
                semantic_route("language", language_id),
            ),
            "schema:name": clean_text(language["label"]),
        }
    for jurisdiction in jurisdiction_registry.values():
        jurisdiction_id = canonical_https_url(
            jurisdiction.get("iri"), field="Stage 1 jurisdiction identity"
        )
        jurisdiction_nodes[jurisdiction_id] = {
            "@id": jurisdiction_id,
            "@type": sorted(set(jurisdiction["class_iris"])),
            "route": semantic_jurisdiction_route(
                clean_text(jurisdiction["label"])
            ),
            "schema:name": clean_text(jurisdiction["label"]),
            "skos:prefLabel": {
                "@value": clean_text(jurisdiction["label"]),
                "@language": "en-GB",
            },
        }
    _source_families, governed_rights = source_controls()
    governed_publishers = load_publisher_registry_entries()
    for rights_ref, assessment in sorted(governed_rights.items()):
        rights_id = validate_stage1_identity(
            "IDF-RIGHTS",
            urljoin(publication_base, f"rights/{rights_ref}"),
            expected_role="project-derived",
        )
        rights_nodes[rights_id] = {
            "@id": rights_id,
            "@type": sorted(stage1_entity_type_classes("TYPE-RIGHTS-STATEMENT")),
            "route": validate_stage1_route(
                "IDF-RIGHTS",
                rights_id,
                semantic_route("rights", rights_id),
            ),
            "dcterms:identifier": rights_ref,
            "schema:name": clean_text(assessment.get("layer")),
            "dcterms:type": clean_text(assessment.get("status")),
        }
    for record in records:
        record_node_id = validate_stage1_identity(
            "IDF-CATALOGUE-RECORD",
            urljoin(publication_base, f"records/{record['record_id']}"),
            expected_role="project-derived",
        )
        for publisher in record["publishers"]:
            publisher_id = publisher["id"]
            publisher_name = publisher["name"]
            governed_publisher = governed_publishers.get(publisher_name)
            if (
                governed_publisher is None
                or clean_text(governed_publisher.get("id")) != publisher_id
            ):
                raise ValueError("publisher node is absent from its governed registry")
            publisher_types = sorted(set(governed_publisher["class_iris"]))
            publisher_family_id = (
                "IDF-LOCAL-AGENT"
                if publisher_id.startswith(urljoin(publication_base, "id/agent/"))
                else "IDF-EXTERNAL-PUBLISHER"
            )
            publisher_node: dict[str, Any] = {
                "@id": publisher_id,
                "@type": publisher_types,
                "route": validate_stage1_route(
                    publisher_family_id,
                    publisher_id,
                    semantic_route("publisher", publisher_id),
                ),
                "schema:name": publisher_name,
                "schema:url": publisher_id,
            }
            if "http://data.europa.eu/m8g/PublicOrganisation" in publisher_types:
                publisher_node.update(
                    {
                        "skos:prefLabel": {
                            "@value": publisher_name,
                            "@language": "en-GB",
                        },
                    }
                )
            existing_publisher = publisher_nodes.get(publisher_id)
            if existing_publisher is not None:
                if any(
                    existing_publisher.get(field) != value
                    for field, value in publisher_node.items()
                ):
                    raise ValueError(
                        "publisher node identity has conflicting labels"
                    )
            else:
                publisher_nodes[publisher_id] = publisher_node
        record_nodes[record_node_id] = {
            "@id": record_node_id,
            "@type": sorted(stage1_entity_type_classes("TYPE-CATALOGUE-RECORD")),
            "route": validate_stage1_route(
                "IDF-CATALOGUE-RECORD",
                record_node_id,
                semantic_route("catalogue-record", record_node_id),
            ),
            "dcterms:identifier": record["record_id"],
        }
        native_decision = stage1_native_class_decision(record)
        record_entity_types = sorted(set(native_decision["class_iris"]))
        if is_cpsv_public_service(record, cpsv_mappings):
            record_entity_types = sorted(
                {
                    *record_entity_types,
                    "http://purl.org/vocab/cpsv#PublicService",
                }
            )

        for language in record["languages"]:
            if language not in language_registry:
                raise ValueError(
                    f"record language lacks a Stage 1 vocabulary term: {language}"
                )

        entity_id = semantic_record_iri(publication_base, record)
        entity: dict[str, Any] = {
            "@id": entity_id,
            "@type": record_entity_types,
            "route": semantic_record_route(publication_base, record),
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
                if jurisdiction_id not in jurisdiction_nodes:
                    raise ValueError(
                        "record jurisdiction lacks its Stage 1 node: " + jurisdiction
                    )
        existing_identity = publisher_nodes.get(entity_id)
        if existing_identity is not None:
            if set(existing_identity["@type"]) != set(record_entity_types):
                raise ValueError(
                    "reused publisher identity has incompatible Stage 1 classes: "
                    + entity_id
                )
            if clean_text(existing_identity.get("route")) != clean_text(
                entity.get("route")
            ):
                raise ValueError(
                    "reused publisher identity has incompatible Explorer route: "
                    + entity_id
                )
            existing_identity.update(
                {
                    "schema:description": entity["schema:description"],
                    "schema:url": entity["schema:url"],
                    "dcterms:type": record["source_native_type"],
                }
            )
        elif entity_id in entity_nodes and entity_nodes[entity_id] != entity:
            raise ValueError("governed record identities collide: " + entity_id)
        else:
            entity_nodes[entity_id] = entity
        for source_url in record["source_urls"]:
            governed_url = semantic_web_iri(source_url)
            source_id = semantic_source_resource_iri(
                publication_base, record, governed_url
            )
            source_node = {
                "@id": source_id,
                "@type": sorted(stage1_entity_type_classes("TYPE-SOURCE-RESOURCE")),
                "route": validate_stage1_route(
                    "IDF-SOURCE-RESOURCE",
                    source_id,
                    semantic_route("source", source_id),
                ),
                "schema:name": "Canonical public source representation",
                "schema:url": governed_url,
                "dcterms:identifier": governed_url,
            }
            existing_source = source_nodes.get(source_id)
            if existing_source is not None and existing_source != source_node:
                raise ValueError("canonical source URL maps to conflicting identities")
            source_nodes[source_id] = source_node

    observation_rows_by_activity: dict[str, list[dict[str, Any]]] = {}
    for assertion in relationship_assertions:
        if clean_text(assertion.get("predicate", {}).get("@id")) != (
            GENERATED_BY_PREDICATE
        ):
            continue
        observation_rows_by_activity.setdefault(
            clean_text(assertion.get("target", {}).get("@id")), []
        ).append(assertion)
    for activity_id, assertions in sorted(observation_rows_by_activity.items()):
        observed_times = {
            clean_text(assertion.get("observed_at")) for assertion in assertions
        }
        input_sha256s = sorted(
            {
                clean_text(evidence.get("source_sha256"))
                for assertion in assertions
                for evidence in assertion.get("evidence", [])
                if clean_text(evidence.get("source_sha256"))
            }
        )
        if not activity_id or not observed_times or not input_sha256s:
            raise ValueError("source-observation activity inputs are incomplete")
        activity_nodes[activity_id] = {
            "@id": activity_id,
            "@type": sorted(
                stage1_entity_type_classes("TYPE-PROVENANCE-ACTIVITY")
            ),
            "route": validate_stage1_route(
                "IDF-OBSERVATION-ACTIVITY",
                activity_id,
                semantic_route("activity", activity_id),
            ),
            "schema:name": "Governed source observation",
            "dcterms:type": "source-observation",
            "prov:endedAtTime": (
                next(iter(observed_times))
                if len(observed_times) == 1
                else sorted(observed_times)
            ),
            "okf:inputSha256": input_sha256s,
        }

    derivation_activities = relationship_derivation_activity_registry(
        publication_base, relationship_assertions
    )
    for activity_id, activity in sorted(derivation_activities.items()):
        rule_id = clean_text(activity["rule_iri"])
        rule_nodes.setdefault(
            rule_id,
            {
                "@id": rule_id,
                "@type": sorted(stage1_entity_type_classes("TYPE-RULE")),
                "route": validate_stage1_route(
                    "IDF-RULE",
                    rule_id,
                    semantic_route("rule", rule_id),
                ),
                "schema:name": "Governed relationship projection rule",
                "dcterms:identifier": rule_id,
            },
        )
        activity_nodes[activity_id] = {
            "@id": activity_id,
            "@type": sorted(
                stage1_entity_type_classes("TYPE-DERIVATION-ACTIVITY")
            ),
            "route": validate_stage1_route(
                "IDF-ASSERTION-ACTIVITY",
                activity_id,
                semantic_route("derivation-activity", activity_id),
            ),
            "schema:name": "Governed relationship derivation",
            "dcterms:type": clean_text(activity["activity_kind"]),
            "prov:hadPlan": {"@id": rule_id},
            "prov:endedAtTime": config["generated_at"],
            "okf:inputSha256": list(activity["input_sha256s"]),
            "okf:toolSha256": clean_text(activity["tool"]["sha256"]),
            "schema:softwareVersion": clean_text(activity["build_version"]),
        }

    for assertion in relationship_assertions:
        evidence_rows = assertion.get("evidence")
        if not isinstance(evidence_rows, list) or not evidence_rows:
            raise ValueError(
                "semantic relationship assertion lacks evidence bindings"
            )
        for evidence in evidence_rows:
            if not isinstance(evidence, dict):
                raise ValueError(
                    "semantic relationship evidence binding is not an object"
                )
            resource_node = relationship_evidence_resource_node(
                publication_base, evidence
            )
            resource_id = resource_node["@id"]
            existing_resource = evidence_resource_nodes.get(resource_id)
            if (
                existing_resource is not None
                and existing_resource != resource_node
            ):
                raise ValueError(
                    "EvidenceResource identity has conflicting projections: "
                    + resource_id
                )
            evidence_resource_nodes[resource_id] = resource_node

    catalog_node = {
        "@id": catalog_id,
        "@type": sorted(stage1_entity_type_classes("TYPE-CATALOGUE")),
        "route": validate_stage1_route(
            "IDF-CATALOGUE",
            catalog_id,
            semantic_route("catalogue", catalog_id),
        ),
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
        rule_nodes,
        language_nodes,
        jurisdiction_nodes,
        source_nodes,
        evidence_resource_nodes,
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
    semantic_document = {
        "@context": urljoin(publication_base, "context.jsonld"),
        "@id": bundle_id,
        "@type": bundle_types[0],
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
    class_closure_receipt = validate_semantic_class_closure(
        semantic_document,
        records=records,
        cpsv_mappings=cpsv_mappings,
        publication_base=publication_base,
    )
    if validation_receipts is not None:
        validation_receipts["class_closure"] = class_closure_receipt
    return semantic_document


def validate_semantic_class_closure(
    semantic_document: dict[str, Any],
    *,
    records: list[dict[str, Any]],
    cpsv_mappings: dict[str, Any],
    publication_base: str,
) -> dict[str, Any]:
    """Prove exact Stage 1 class coverage, zero-use and record typing."""
    stage1 = load_stage1_semantic_authority()
    active_rows = {
        identifier: row
        for identifier, row in stage1["entity_types"].items()
        if row.get("implementation_state") == "active-emitted"
    }
    zero_rows = {
        identifier: row
        for identifier, row in stage1["entity_types"].items()
        if row.get("implementation_state") == "authorised-zero-evidence"
    }
    active_classes = {
        class_iri
        for row in active_rows.values()
        for class_iri in row.get("class_iris", [])
    }
    zero_classes = {
        class_iri
        for row in zero_rows.values()
        for class_iri in row.get("class_iris", [])
    }
    if not active_classes or active_classes & zero_classes:
        raise ValueError("Stage 1 active and zero-use class unions are invalid")

    emitted_classes: set[str] = set()

    def collect_types(value: Any) -> None:
        if isinstance(value, dict):
            raw_types = value.get("@type")
            node_types = raw_types if isinstance(raw_types, list) else [raw_types]
            for class_iri in node_types:
                if class_iri is None:
                    continue
                emitted_classes.add(
                    governed_absolute_http_iri(
                        class_iri, field="emitted semantic class IRI"
                    )
                )
            for nested in value.values():
                collect_types(nested)
        elif isinstance(value, list):
            for nested in value:
                collect_types(nested)

    collect_types(semantic_document)
    if emitted_classes != active_classes:
        raise ValueError(
            "emitted semantic class closure differs from active Stage 1: "
            + repr(sorted(emitted_classes ^ active_classes))
        )
    if emitted_classes & zero_classes:
        raise ValueError(
            "authorised-zero-evidence class was emitted: "
            + repr(sorted(emitted_classes & zero_classes))
        )

    graph = semantic_document.get("@graph")
    if not isinstance(graph, list):
        raise ValueError("semantic class closure requires an @graph array")
    nodes_by_id: dict[str, dict[str, Any]] = {}
    for node in graph:
        if not isinstance(node, dict) or not clean_text(node.get("@id")):
            continue
        identifier = clean_text(node["@id"])
        if identifier in nodes_by_id:
            raise ValueError("semantic graph identity is duplicated: " + identifier)
        nodes_by_id[identifier] = node

    expected_entity_types: dict[str, list[str]] = {}
    for record in records:
        identifier = semantic_record_iri(publication_base, record)
        expected = set(stage1_native_class_decision(record)["class_iris"])
        if is_cpsv_public_service(record, cpsv_mappings):
            expected.add("http://purl.org/vocab/cpsv#PublicService")
        ordered = sorted(expected)
        previous = expected_entity_types.get(identifier)
        if previous is not None and previous != ordered:
            raise ValueError(
                "records sharing a semantic identity have conflicting classes: "
                + identifier
            )
        expected_entity_types[identifier] = ordered
    for identifier, expected in expected_entity_types.items():
        node = nodes_by_id.get(identifier)
        if node is None or node.get("@type") != expected:
            raise ValueError(
                "source-native entity classes differ from Stage 1: " + identifier
            )

    return {
        "status": "conformant",
        "active_entity_types": len(active_rows),
        "authorised_zero_entity_types": len(zero_rows),
        "emitted_classes": len(emitted_classes),
        "record_entities_validated": len(expected_entity_types),
        "emitted_class_set_sha256": sha256_bytes(
            compact_canonical_json(sorted(emitted_classes))
        ),
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
    normalized = re.sub(
        r"[\u0300-\u036f]",
        "",
        unicodedata.normalize("NFKD", clean_text(value)).lower(),
    )
    stopwords = {clean_text(word).lower() for word in contract["stopwords"]}
    token_min_length = contract["token_min_length"]
    return sorted(
        {
            token
            for token in re.findall(contract["token_pattern"], normalized)
            if len(token) >= token_min_length and token not in stopwords
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
    required = (
        "token_pattern",
        "token_min_length",
        "stopwords",
        "heading_fields",
        "body_fields",
        "weights",
    )
    if any(key not in contract for key in required):
        raise ValueError("search contract is incomplete")
    explorer_query_policy(contract)

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


def governed_activity_identity(
    publication_base: str,
    *,
    activity_kind: str,
    rule_iri: str,
    input_sha256s: Iterable[str],
    coordinate: dict[str, str] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Mint an execution identity from its rule, tool and complete input set."""
    input_digests = sorted(set(input_sha256s))
    if (
        not clean_text(activity_kind)
        or not clean_text(rule_iri)
        or not input_digests
        or any(not re.fullmatch(r"[0-9a-f]{64}", value) for value in input_digests)
    ):
        raise ValueError("activity identity inputs are incomplete or invalid")
    validate_stage1_identity(
        "IDF-RULE", rule_iri, expected_role="runtime-control"
    )
    tool_sha256 = sha256_file(ROOT / "scripts" / "build.py")
    identity_basis = {
        "rule_iri": clean_text(rule_iri),
        "tool": {"path": "scripts/build.py", "sha256": tool_sha256},
        "input_sha256s": input_digests,
    }
    digest = sha256_bytes(compact_canonical_json(identity_basis))
    identity = {
        "activity_kind": clean_text(activity_kind),
        **identity_basis,
        # Coordinates are descriptive execution properties. They never alter
        # identity unless their exact source bytes are already among the
        # digest-bound inputs above.
        "coordinate": dict(sorted((coordinate or {}).items())),
        "build_version": BUILD_VERSION,
        "source_model_version": SOURCE_MODEL_VERSION,
    }
    safe_kind = re.sub(r"[^a-z0-9]+", "-", activity_kind.casefold()).strip("-")
    if activity_kind == "source-observation":
        family_id = "IDF-OBSERVATION-ACTIVITY"
        activity_path = "activities/"
        expected_role = "project-derived"
    elif activity_kind == "relationship-derivation":
        family_id = "IDF-ASSERTION-ACTIVITY"
        activity_path = "id/activity/"
        expected_role = "project-derived"
    else:
        raise ValueError(
            "activity kind lacks an exact Stage 1 identity family: "
            + activity_kind
        )
    activity_iri = urljoin(
        publication_base.rstrip("/") + "/",
        f"{activity_path}{safe_kind}-{digest[:24]}",
    )
    activity_iri = validate_stage1_identity(
        family_id,
        activity_iri,
        expected_role=expected_role,
    )
    return activity_iri, identity


def source_observation_activity_registry(
    publication_base: str,
    records: list[dict[str, Any]],
    record_bindings: dict[str, dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    """Return input-bound observation activities, independent of record order."""
    groups: dict[tuple[str, str], set[str]] = {}
    for record in records:
        record_id = clean_text(record.get("record_id"))
        binding = record_bindings.get(record_id)
        if binding is None:
            raise ValueError(f"source observation lacks a record binding: {record_id}")
        key = (
            clean_text(record.get("source_family")),
            clean_text(record.get("observed_at")),
        )
        groups.setdefault(key, set()).add(clean_text(binding.get("source_sha256")))
    rule_iri = urljoin(
        publication_base.rstrip("/") + "/",
        "id/rule/governed-source-observation-v1",
    )
    registry: dict[tuple[str, str], dict[str, Any]] = {}
    for (source_family, observed_at), input_digests in sorted(groups.items()):
        activity_iri, identity = governed_activity_identity(
            publication_base,
            activity_kind="source-observation",
            rule_iri=rule_iri,
            input_sha256s=input_digests,
            coordinate={
                "source_family": source_family,
                "observed_at": observed_at,
            },
        )
        registry[(source_family, observed_at)] = {
            "iri": activity_iri,
            "rule_iri": rule_iri,
            **identity,
        }
    return registry


def relationship_derivation_activity_registry(
    publication_base: str,
    assertions: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Mint one governed derivation execution per relationship rule."""
    assertions_by_rule: dict[str, list[dict[str, Any]]] = {}
    for assertion in assertions:
        rule_iri = clean_text(assertion.get("rule"))
        if not rule_iri or clean_text(assertion.get("derivation")) != rule_iri:
            raise ValueError("relationship assertion lacks one governed rule identity")
        assertions_by_rule.setdefault(rule_iri, []).append(assertion)

    profile_sha256 = load_stage1_semantic_authority()["profile_sha256"]
    cpsv_mapping_sha256 = sha256_file(ROOT / "source" / "cpsv-service-mappings.json")
    registry: dict[str, dict[str, Any]] = {}
    for rule_iri, rows in sorted(assertions_by_rule.items()):
        input_sha256s = {
            clean_text(evidence.get("source_sha256"))
            for row in rows
            for evidence in row.get("evidence", [])
            if clean_text(evidence.get("source_sha256"))
        }
        input_sha256s.add(profile_sha256)
        predicates = {
            clean_text(row.get("predicate", {}).get("@id")) for row in rows
        }
        for predicate_iri in predicates:
            validate_stage1_relationship_rule(predicate_iri, rule_iri)
        if predicates & {SPATIAL_PREDICATE, COMPETENT_AUTHORITY_PREDICATE}:
            input_sha256s.add(cpsv_mapping_sha256)
        activity_iri, identity = governed_activity_identity(
            publication_base,
            activity_kind="relationship-derivation",
            rule_iri=rule_iri,
            input_sha256s=input_sha256s,
            coordinate={
                "assertion_scopes": ",".join(
                    sorted({clean_text(row.get("assertion_scope")) for row in rows})
                ),
                "assertion_statuses": ",".join(
                    sorted({clean_text(row.get("assertion_status")) for row in rows})
                ),
            },
        )
        if activity_iri in registry:
            raise ValueError("relationship derivation activity identity collision")
        registry[activity_iri] = {
            "iri": activity_iri,
            "rule_iri": rule_iri,
            "assertion_count": len(rows),
            **identity,
        }
    return registry


def relationship_labels(predicate_iri: str) -> tuple[str, str]:
    """Return the governed preferred and inverse labels for one predicate."""
    governed = load_stage1_semantic_authority()["active_relationships"].get(
        predicate_iri
    )
    if governed is None:
        raise ValueError(
            f"relationship predicate is not governed: {predicate_iri}"
        )
    return clean_text(governed["label"]), clean_text(governed["inverse_label"])


def relationship_assertion_type_iris() -> list[str]:
    """Return expanded classes for every reified relationship assertion."""
    return sorted(stage1_entity_type_classes("TYPE-RELATIONSHIP-ASSERTION"))


def relationship_assertion_id(
    publication_base: str,
    source_iri: str,
    predicate_iri: str,
    target_iri: str,
    *,
    assertion_plane: str,
    assertion_status: str = "normalized",
    assertion_scope: str = "real-world",
) -> str:
    """Mint identity from the directed triple and its governed assertion plane."""
    assertion_hash = sha256_bytes(
        "\0".join(
            (
                source_iri,
                predicate_iri,
                target_iri,
                clean_text(assertion_status),
                clean_text(assertion_scope),
                clean_text(assertion_plane),
            )
        ).encode("utf-8")
    )[:24]
    identifier = urljoin(
        publication_base.rstrip("/") + "/",
        "id/assertion/" + assertion_hash,
    )
    return validate_stage1_identity(
        "IDF-ASSERTION",
        identifier,
        expected_role="project-derived",
    )


def relationship_evidence_resource_id(
    publication_base: str,
    evidence: dict[str, Any],
) -> str:
    """Mint reusable source/locator/value identity for an EvidenceResource."""
    identity_input = {
        "source_artifact": clean_text(evidence.get("source_artifact")),
        "source_sha256": clean_text(evidence.get("source_sha256")),
        "locator": clean_text(evidence.get("locator")),
        "source_value_sha256": clean_text(evidence.get("source_value_sha256")),
        "source_value_hash_canonicalization": clean_text(
            evidence.get("source_value_hash_canonicalization")
        ),
    }
    if (
        not identity_input["source_artifact"]
        or not identity_input["locator"]
        or any(
            not re.fullmatch(r"[0-9a-f]{64}", identity_input[field])
            for field in ("source_sha256", "source_value_sha256")
        )
        or not identity_input["source_value_hash_canonicalization"]
    ):
        raise ValueError("EvidenceResource identity inputs are incomplete")
    digest = sha256_bytes(compact_canonical_json(identity_input))[:32]
    identifier = urljoin(
        publication_base.rstrip("/") + "/",
        "id/evidence-resource/" + digest,
    )
    return validate_stage1_identity(
        "IDF-EVIDENCE-RESOURCE",
        identifier,
        expected_role="project-derived",
    )


def relationship_evidence_resource_node(
    publication_base: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    """Project one reusable, route-bearing EvidenceResource graph node."""
    identifier = relationship_evidence_resource_id(publication_base, evidence)
    source_artifact = clean_text(evidence.get("source_artifact"))
    locator = clean_text(evidence.get("locator"))
    declared_reference = evidence.get("okf:evidenceResource")
    if (
        not isinstance(declared_reference, dict)
        or clean_text(declared_reference.get("@id")) != identifier
        or set(declared_reference) != {"@id"}
    ):
        raise ValueError(
            "EvidenceBinding does not reference its exact EvidenceResource"
        )
    return {
        "@id": identifier,
        "@type": sorted(
            stage1_entity_type_classes("TYPE-EVIDENCE-RESOURCE")
        ),
        "route": validate_stage1_route(
            "IDF-EVIDENCE-RESOURCE",
            identifier,
            semantic_route("evidence-resource", identifier),
        ),
        "schema:name": f"Evidence resource: {source_artifact} — {locator}",
        "okf:sourceArtifact": source_artifact,
        "okf:sourceSha256": clean_text(evidence.get("source_sha256")),
        "okf:sourceLocator": locator,
        "okf:sourceValueSha256": clean_text(
            evidence.get("source_value_sha256")
        ),
        "okf:sourceValueHashCanonicalization": clean_text(
            evidence.get("source_value_hash_canonicalization")
        ),
    }


def relationship_evidence_id(
    publication_base: str,
    evidence: dict[str, Any],
    *,
    source_iri: str,
    predicate_iri: str,
    target_iri: str,
    assertion_plane: str,
    assertion_status: str,
    assertion_scope: str,
) -> str:
    """Mint an assertion-scoped EvidenceBinding occurrence identity."""
    identity_input = {
        "assertion": relationship_assertion_id(
            publication_base,
            source_iri,
            predicate_iri,
            target_iri,
            assertion_plane=assertion_plane,
            assertion_status=assertion_status,
            assertion_scope=assertion_scope,
        ),
        "evidence": {
            key: value for key, value in evidence.items() if key != "@id"
        },
    }
    evidence_hash = sha256_bytes(compact_canonical_json(identity_input))[:32]
    identifier = urljoin(
        publication_base.rstrip("/") + "/",
        "id/evidence/" + evidence_hash,
    )
    return validate_stage1_identity(
        "IDF-EVIDENCE-BINDING",
        identifier,
        expected_role="project-derived",
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
    assertion_plane: str,
    assertion_status: str,
    assertion_scope: str,
) -> dict[str, Any]:
    """Bind schema-safe evidence to a triple without emitting private fields."""
    if not clean_text(role):
        raise ValueError("relationship evidence role is absent")
    if role != "publisher-jurisdiction" and not clean_text(record_id):
        raise ValueError("relationship evidence governed record is absent")
    bound = copy.deepcopy(evidence)
    bound["@type"] = sorted(
        stage1_entity_type_classes("TYPE-EVIDENCE-BINDING")
    )
    bound["okf:bindingRole"] = role
    bound["okf:evidenceResource"] = {
        "@id": relationship_evidence_resource_id(publication_base, bound)
    }
    bound["@id"] = relationship_evidence_id(
        publication_base,
        bound,
        source_iri=source_iri,
        predicate_iri=predicate_iri,
        target_iri=target_iri,
        assertion_plane=assertion_plane,
        assertion_status=assertion_status,
        assertion_scope=assertion_scope,
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
    publication_base = (
        relationship_publication_base(assertions[0]["@id"])
        if assertions
        else PUBLICATION_BASE
    )
    record_ids_by_entity_iri: dict[str, list[str]] = {}
    for record_id, record in records_by_id.items():
        record_ids_by_entity_iri.setdefault(
            semantic_record_iri(publication_base, record), []
        ).append(record_id)
    observation_activities = (
        source_observation_activity_registry(
            publication_base,
            records or [],
            record_bindings,
        )
        if assertions and records is not None and record_bindings is not None
        else {}
    )
    evidence_ids: set[str] = set()
    evidence_resource_ids: set[str] = set()
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
        "rights-record-source": {RIGHTS_PREDICATE},
        "rights-family-policy": {RIGHTS_PREDICATE},
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
        "rights-record-source": "governed-record-rights-selection",
        "rights-family-policy": "governed-source-family-rights-policy",
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
            "governed-record-rights-selection": "rights-record-source",
            "governed-source-family-rights-policy": "rights-family-policy",
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

        def identifier(iri: str, prefix: str) -> str:
            value = iri.removeprefix(prefix) if iri.startswith(prefix) else ""
            if not re.fullmatch(r"hmlr-[0-9a-f]{24}", value):
                raise ValueError(
                    "relationship evidence endpoint does not encode a governed "
                    f"record: {location}"
                )
            return value

        def entity_identifier(iri: str) -> str:
            matches = record_ids_by_entity_iri.get(iri, [])
            if len(matches) != 1:
                raise ValueError(
                    "relationship evidence endpoint does not select exactly one "
                    f"governed record: {location}"
                )
            return matches[0]

        if predicate_iri in {
            CATALOGUE_RECORD_PREDICATE,
        }:
            return identifier(target_iri, record_prefix)
        if predicate_iri in {
            CATALOGUE_RESOURCE_PREDICATE,
            CATALOGUE_DATASET_PREDICATE,
        }:
            return entity_identifier(target_iri)
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
            return entity_identifier(source_iri)
        if predicate_iri in {RIGHTS_PREDICATE, GENERATED_BY_PREDICATE}:
            return (
                identifier(source_iri, record_prefix)
                if source_iri.startswith(record_prefix)
                else entity_identifier(source_iri)
            )
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
            "rights-record-source",
            "rights-family-policy",
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
                evidence_retrieved_at = expected_observed_at
                if role == "rights-family-policy":
                    evidence_retrieved_at = governed_event_timestamp(
                        source_document.get("reviewed_at")
                        if isinstance(source_document, dict)
                        else None,
                        "source/source-register.json reviewed_at",
                    )
                elif role == "rights-assessment":
                    evidence_retrieved_at = governed_event_timestamp(
                        source_document.get("reviewed_at")
                        if isinstance(source_document, dict)
                        else None,
                        "governance/rights-review.json reviewed_at",
                    )
                expected_evidence.update(
                    {
                        "url": governed_url,
                        "resource": governed_url,
                        "retrieved_at": evidence_retrieved_at,
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
                expected_evidence["retrieved_at"] = (
                    governed_event_timestamp(
                        source_document.get("reviewed_at")
                        if isinstance(source_document, dict)
                        else None,
                        "source/publisher-registry.json reviewed_at",
                    )
                    if role == "publisher-registry"
                    else expected_observed_at
                )
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
        record = records_by_id.get(record_id)
        entity_iri = (
            semantic_record_iri(base, record)
            if record is not None
            else urljoin(base, "id/entity/" + record_id)
        )
        catalogue_iri = urljoin(base, "id/catalogue/hmlr-public-estate")
        expected_endpoints: dict[str, tuple[set[str], set[str]]] = {
            CATALOGUE_RECORD_PREDICATE: ({catalogue_iri}, {record_iri}),
            CATALOGUE_RESOURCE_PREDICATE: ({catalogue_iri}, {entity_iri}),
            CATALOGUE_DATASET_PREDICATE: ({catalogue_iri}, {entity_iri}),
            PRIMARY_TOPIC_PREDICATE: ({record_iri}, {entity_iri}),
            SOURCE_PREDICATE: (
                {record_iri},
                {urljoin(base, "id/source-resource/source-")},
            ),
            DERIVED_FROM_PREDICATE: (
                {entity_iri},
                {urljoin(base, "id/source-resource/source-")},
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

        if records is not None and record is None:
            raise ValueError(
                f"relationship evidence binds an unknown record: {location}"
            )
        if record is None:
            return
        if (
            predicate_iri == CATALOGUE_DATASET_PREDICATE
            and "http://www.w3.org/ns/dcat#Dataset"
            not in stage1_native_class_decision(record)["class_iris"]
        ):
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
            activity = observation_activities.get(
                (
                    clean_text(record["source_family"]),
                    clean_text(record["observed_at"]),
                )
            )
            expected_target = clean_text(activity.get("iri")) if activity else ""
            if target_iri != expected_target:
                raise ValueError(
                    f"relationship provenance evidence has wrong target: {location}"
                )
        elif predicate_iri == LANGUAGE_PREDICATE:
            language_targets = stage1_language_registry()
            expected_targets = {
                clean_text(language_targets[language]["iri"])
                for language in record.get("languages", [])
                if language in language_targets
            }
            if target_iri not in expected_targets:
                raise ValueError(
                    f"relationship language evidence does not support its target: {location}"
                )
        elif predicate_iri == PUBLISHER_PREDICATE:
            expected_targets = {
                clean_text(publisher.get("id"))
                for publisher in record.get("publishers", [])
                if isinstance(publisher, dict)
            }
            if target_iri not in expected_targets:
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
            publication_base,
            source_iri,
            predicate_iri,
            target_iri,
            assertion_plane=clean_text(assertion.get("assertion_plane")),
            assertion_status=clean_text(assertion.get("assertion_status")),
            assertion_scope=clean_text(assertion.get("assertion_scope")),
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
            expected_binding_types = sorted(
                stage1_entity_type_classes("TYPE-EVIDENCE-BINDING")
            )
            if evidence.get("@type") != expected_binding_types:
                raise ValueError(
                    f"relationship evidence binding classes differ: {location}"
                )
            expected_resource_id = relationship_evidence_resource_id(
                publication_base, evidence
            )
            resource_reference = evidence.get("okf:evidenceResource")
            if (
                not isinstance(resource_reference, dict)
                or set(resource_reference) != {"@id"}
                or clean_text(resource_reference.get("@id"))
                != expected_resource_id
            ):
                raise ValueError(
                    "relationship evidence resource reference differs: "
                    + location
                )
            evidence_resource_ids.add(expected_resource_id)
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
                assertion_plane=clean_text(assertion.get("assertion_plane")),
                assertion_status=clean_text(assertion.get("assertion_status")),
                assertion_scope=clean_text(assertion.get("assertion_scope")),
            )
            if evidence_id != expected_evidence_id:
                raise ValueError(
                    f"relationship evidence ID is not deterministic: {location}"
                )
            evidence_type = clean_text(evidence.get("type"))
            role = evidence_role(predicate_iri, evidence_type)
            if clean_text(evidence.get("okf:bindingRole")) != role:
                raise ValueError(
                    f"relationship evidence binding role differs: {location}"
                )
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
                publisher_match = (
                    re.fullmatch(r"(.+)\.organisations\[(\d+)\]", field)
                    if role == "publisher-source"
                    else None
                )
                identity_value = (
                    resolve_source_field_value(artifact, publisher_match.group(1))
                    if publisher_match
                    else value
                )
                record_ids = _record_ids_from_source_value(
                    evidence, identity_value
                )
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
                and role
                in {
                    "record-projection",
                    "rights-record-source",
                    "publisher-source",
                }
                and record_id
            ):
                selected = record_bindings.get(record_id)
                if selected is None:
                    raise ValueError(
                        f"relationship evidence has no permitted record binding: {location}"
                    )
                candidates = [
                    selected,
                    *selected.get("representations", []),
                    *(
                        selected.get("publisher_representations", [])
                        if role == "publisher-source"
                        else []
                    ),
                ]
                publisher_match = (
                    re.fullmatch(r"(.+)\.organisations\[(\d+)\]", field)
                    if role == "publisher-source"
                    else None
                )
                if publisher_match:
                    parent_field = publisher_match.group(1)
                    exact_candidates = [
                        candidate
                        for candidate in candidates
                        if clean_text(candidate.get("record_id")) == source_locator
                        and clean_text(candidate.get("source_artifact")) == artifact
                        and clean_text(candidate.get("source_field")) == parent_field
                        and clean_text(candidate.get("source_sha256"))
                        == clean_text(evidence.get("source_sha256"))
                    ]
                else:
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
            if role == "publisher-source" and re.fullmatch(
                r"(.+)\.organisations\[(\d+)\]", field
            ):
                organisation = value if isinstance(value, dict) else {}
                organisation_name = clean_text(organisation.get("title"))
                organisation_iri = semantic_web_iri(
                    urljoin(
                        "https://www.gov.uk/",
                        clean_text(organisation.get("link")),
                    )
                )
                record = records_by_id.get(record_id)
                expected_publishers = {
                    (
                        clean_text(publisher.get("name")),
                        clean_text(publisher.get("id")),
                    )
                    for publisher in (record or {}).get("publishers", [])
                    if isinstance(publisher, dict)
                }
                if (
                    organisation_iri != target_iri
                    or (organisation_name, organisation_iri)
                    not in expected_publishers
                ):
                    raise ValueError(
                        "GOV.UK publisher declaration does not bind its record "
                        f"and target: {location}"
                    )
            if evidence_type == "governed-source-family-rights-policy":
                family = value if isinstance(value, dict) else {}
                record = records_by_id.get(record_id)
                if (
                    artifact != "source/source-register.json"
                    or not field.startswith("source_families[")
                    or record is None
                    or clean_text(family.get("id"))
                    != clean_text(record.get("source_family"))
                ):
                    raise ValueError(
                        "relationship family rights policy does not bind its "
                        f"governed record: {location}"
                    )
                if record.get("curation") != "reviewed":
                    selected_policy = governed_source_family_rights_policy(
                        family, clean_text(record.get("canonical_source_url"))
                    )
                    selected_rights_ref = target_iri.rsplit("/rights/", 1)[-1]
                    if selected_policy["primary_rights_ref"] != selected_rights_ref:
                        raise ValueError(
                            "relationship family rights policy does not select "
                            f"its target: {location}"
                        )
            if evidence_type == "governed-rights-assessment":
                rights_id = clean_text(value.get("id")) if isinstance(value, dict) else ""
                record = records_by_id.get(record_id)
                source_family_ids = (
                    value.get("source_family_ids", [])
                    if isinstance(value, dict)
                    else []
                )
                if (
                    not rights_id
                    or not target_iri.endswith("/rights/" + rights_id)
                    or (
                        record is not None
                        and clean_text(record.get("source_family"))
                        not in source_family_ids
                    )
                ):
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
                    != semantic_jurisdiction_iri(
                        publication_base, "England and Wales"
                    )
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
                target_record_ids = [
                    candidate_id
                    for candidate_id, candidate in records_by_id.items()
                    if semantic_record_iri(publication_base, candidate)
                    == target_iri
                ]
                target_record_id = (
                    target_record_ids[0] if len(target_record_ids) == 1 else ""
                )
                source_record = records_by_id.get(source_record_id)
                if (
                    not re.fullmatch(r"hmlr-[0-9a-f]{24}", target_record_id)
                    or source_locator != source_record_id
                    or source_record is None
                    or source_iri
                    != semantic_record_iri(publication_base, source_record)
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
            expected_roles = [
                "rights-record-source",
                "rights-family-policy",
                "rights-assessment",
            ]
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
        "evidence_resources_validated": len(evidence_resource_ids),
        "evidence_identity_set_sha256": set_digest(evidence_ids),
        "evidence_resource_identity_set_sha256": set_digest(
            evidence_resource_ids
        ),
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
        publisher_representation_bindings: list[dict[str, Any]] = []
        for inheritance in record.get("publisher_inheritance", []):
            inheritance_key = (
                clean_text(inheritance.get("source_family")),
                clean_text(inheritance.get("id")),
            )
            inheritance_binding = candidates.get(inheritance_key)
            if inheritance_binding is None:
                raise ValueError(
                    "publisher inheritance lacks an exact frozen source-row "
                    f"binding: {record_id} -> {inheritance_key}"
                )
            publisher_representation_bindings.append(inheritance_binding)
        bindings[record_id] = {
            **candidate,
            "record_id": record_id,
            "representations": representation_bindings,
            "publisher_representations": publisher_representation_bindings,
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
    evidence_retrieved_at: str | None = None,
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
            "retrieved_at": evidence_retrieved_at or observed_at,
        },
        source_iri=source_iri,
        predicate_iri=predicate_iri,
        target_iri=target_iri,
        role=evidence_role,
        record_id=record_id,
        assertion_plane="core",
        assertion_status="normalized",
        assertion_scope="real-world",
    )
    return {
        "@id": relationship_assertion_id(
            publication_base, *triple, assertion_plane="core"
        ),
        "@type": relationship_assertion_type_iris(),
        "source": {"@id": source_iri},
        "predicate": {"@id": predicate_iri},
        "target": {"@id": target_iri},
        "source_route": source_route,
        "target_route": target_route,
        "kind": preferred_label,
        "label": preferred_label,
        "inverse_label": inverse_label,
        "assertion_plane": "core",
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
    catalog_id = validate_stage1_identity(
        "IDF-CATALOGUE",
        urljoin(publication_base, "id/catalogue/hmlr-public-estate"),
        expected_role="project-derived",
    )
    catalog_route = validate_stage1_route(
        "IDF-CATALOGUE",
        catalog_id,
        semantic_route("catalogue", catalog_id),
    )
    rights_path = ROOT / "governance" / "rights-review.json"
    rights_relative = rights_path.relative_to(ROOT).as_posix()
    rights_sha256 = sha256_file(rights_path)
    rights_document = load_json(rights_path)
    rights_rows = rights_document.get("assessments", [])
    rights_reviewed_at = governed_event_timestamp(
        rights_document.get("reviewed_at"),
        "governance/rights-review.json reviewed_at",
    )
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
    source_register_observed_at = governed_event_timestamp(
        source_register.get("observed_at"),
        "source/source-register.json observed_at",
    )
    source_register_reviewed_at = governed_event_timestamp(
        source_register.get("reviewed_at"),
        "source/source-register.json reviewed_at",
    )
    source_family_bindings = {
        clean_text(row.get("id")): {
            "source_artifact": source_register_relative,
            "source_sha256": source_register_sha256,
            "source_field": f"source_families[{ordinal}]",
            "source_value": row,
        }
        for ordinal, row in enumerate(source_register.get("source_families", []))
        if isinstance(row, dict)
    }
    language_registry = stage1_language_registry()
    observation_activities = source_observation_activity_registry(
        publication_base, records, record_bindings
    )
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
        is_rights_assertion = predicate_iri == RIGHTS_PREDICATE
        rights_assessment_binding = evidence_binding if is_rights_assertion else None
        binding = (
            record_bindings.get(record_id)
            if is_rights_assertion
            else evidence_binding or record_bindings.get(record_id)
        )
        if binding is None:
            raise ValueError(f"relationship record lacks source binding: {record_id}")
        source_locator = clean_text(binding.get("record_id")) or None
        primary_evidence_type = (
            "governed-record-rights-selection"
            if is_rights_assertion
            else evidence_type
        )
        primary_evidence_role = (
            "rights-record-source"
            if is_rights_assertion
            else (
                "rights-assessment"
                if evidence_type == "governed-rights-assessment"
                else "record-projection"
            )
        )
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
            evidence_type=primary_evidence_type,
            evidence_role=primary_evidence_role,
            record_id=record_id,
        )
        if is_rights_assertion:
            family_binding = source_family_bindings.get(record["source_family"])
            if family_binding is None or rights_assessment_binding is None:
                raise ValueError(
                    f"rights assertion lacks governed family or assessment: {record_id}"
                )
            for governed_binding, governed_type, governed_role in (
                (
                    family_binding,
                    "governed-source-family-rights-policy",
                    "rights-family-policy",
                ),
                (
                    rights_assessment_binding,
                    "governed-rights-assessment",
                    "rights-assessment",
                ),
            ):
                governed_assertion = normalized_relationship_assertion(
                    publication_base,
                    source_iri=source_iri,
                    predicate_iri=predicate_iri,
                    target_iri=target_iri,
                    source_route=source_route,
                    target_route=target_route,
                    observed_at=clean_text(record["observed_at"]),
                    evidence_url=clean_text(record["canonical_source_url"]),
                    source_artifact=governed_binding["source_artifact"],
                    source_sha256=governed_binding["source_sha256"],
                    source_field=governed_binding["source_field"],
                    source_value=governed_binding["source_value"],
                    locator=governed_binding["source_field"],
                    evidence_retrieved_at=(
                        source_register_reviewed_at
                        if governed_role == "rights-family-policy"
                        else rights_reviewed_at
                    ),
                    evidence_type=governed_type,
                    evidence_role=governed_role,
                    record_id=record_id,
                )
                assertion["evidence"].extend(governed_assertion["evidence"])
        if is_rights_assertion and record.get("curation") == "reviewed":
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
        record_iri = validate_stage1_identity(
            "IDF-CATALOGUE-RECORD",
            urljoin(publication_base, "records/" + record_id),
            expected_role="project-derived",
        )
        entity_iri = semantic_record_iri(publication_base, record)
        record_route = validate_stage1_route(
            "IDF-CATALOGUE-RECORD",
            record_iri,
            semantic_route("catalogue-record", record_iri),
        )
        entity_route = semantic_record_route(publication_base, record)
        rights_rows_for_record: list[tuple[str, str, dict[str, Any]]] = []
        for rights_ref in [
            record["rights_ref"],
            *record.get("additional_rights_refs", []),
        ]:
            rights_iri = validate_stage1_identity(
                "IDF-RIGHTS",
                urljoin(publication_base, "rights/" + rights_ref),
                expected_role="project-derived",
            )
            rights_binding = rights_bindings.get(rights_ref)
            if rights_binding is None:
                raise ValueError(
                    f"record lacks an exact rights-review row: {record_id}"
                )
            rights_rows_for_record.append(
                (
                    rights_iri,
                    validate_stage1_route(
                        "IDF-RIGHTS",
                        rights_iri,
                        semantic_route("rights", rights_iri),
                    ),
                    rights_binding,
                )
            )
        observation_activity = observation_activities.get(
            (record["source_family"], record["observed_at"])
        )
        if observation_activity is None:
            raise ValueError(f"record lacks a governed observation activity: {record_id}")
        activity_iri = observation_activity["iri"]
        activity_route = validate_stage1_route(
            "IDF-OBSERVATION-ACTIVITY",
            activity_iri,
            semantic_route("activity", activity_iri),
        )

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
        if "http://www.w3.org/ns/dcat#Dataset" in stage1_native_class_decision(
            record
        )["class_iris"]:
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
            source_route = validate_stage1_route(
                "IDF-SOURCE-RESOURCE",
                source_iri,
                semantic_route("source", source_iri),
            )
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
            language_term = language_registry.get(language)
            if language_term is None:
                raise ValueError(
                    f"record language lacks a Stage 1 vocabulary term: {language}"
                )
            language_iri = validate_stage1_identity(
                "IDF-EXTERNAL-LANGUAGE",
                language_term.get("iri"),
                expected_role="source-native-external",
            )
            emit(
                record,
                LANGUAGE_PREDICATE,
                entity_iri,
                language_iri,
                entity_route,
                validate_stage1_route(
                    "IDF-EXTERNAL-LANGUAGE",
                    language_iri,
                    semantic_route("language", language_iri),
                ),
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
                    semantic_jurisdiction_route(jurisdiction),
                )

    hmlr_jurisdiction_iri = semantic_jurisdiction_iri(
        publication_base, "England and Wales"
    )
    jurisdiction_note = clean_text(source_register["jurisdiction_note"])
    assertions.append(
        normalized_relationship_assertion(
            publication_base,
            source_iri=HMLR_PUBLISHER_IRI,
            predicate_iri=SPATIAL_PREDICATE,
            target_iri=hmlr_jurisdiction_iri,
            source_route=validate_stage1_route(
                "IDF-EXTERNAL-PUBLISHER",
                HMLR_PUBLISHER_IRI,
                semantic_route("publisher", HMLR_PUBLISHER_IRI),
            ),
            target_route=semantic_jurisdiction_route("England and Wales"),
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
    registry_document = load_json(publisher_registry_path)
    registry_reviewed_at = governed_event_timestamp(
        registry_document.get("reviewed_at"),
        "source/publisher-registry.json reviewed_at",
    )
    registry_rows = registry_document.get("publishers", [])
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

    def exact_source_declaration(
        record: dict[str, Any],
        source_binding: dict[str, Any],
        publisher_name: str,
        publisher_iri: str,
    ) -> dict[str, Any]:
        declarations: list[dict[str, Any]] = []
        for candidate in [
            source_binding,
            *source_binding.get("representations", []),
            *source_binding.get("publisher_representations", []),
        ]:
            source_value = candidate.get("source_value")
            organisations = (
                source_value.get("organisations")
                if isinstance(source_value, dict)
                else None
            )
            if not isinstance(organisations, list):
                continue
            for ordinal, organisation in enumerate(organisations):
                if not isinstance(organisation, dict):
                    raise ValueError("GOV.UK publisher declaration is not an object")
                organisation_name = clean_text(organisation.get("title"))
                organisation_link = clean_text(organisation.get("link"))
                organisation_iri = semantic_web_iri(
                    urljoin("https://www.gov.uk/", organisation_link)
                )
                if (
                    organisation_name == publisher_name
                    and organisation_iri == publisher_iri
                ):
                    declarations.append(
                        {
                            "record_id": clean_text(candidate.get("record_id")),
                            "source_artifact": candidate["source_artifact"],
                            "source_sha256": candidate["source_sha256"],
                            "source_field": (
                                f"{candidate['source_field']}.organisations[{ordinal}]"
                            ),
                            "source_value": organisation,
                        }
                    )
        declaration_keys = {
            (
                declaration["record_id"],
                declaration["source_artifact"],
                declaration["source_field"],
            )
            for declaration in declarations
        }
        if len(declaration_keys) > 1:
            raise ValueError(
                "publisher has ambiguous frozen source declarations: "
                f"{record['record_id']} {publisher_name!r}"
            )
        if declarations:
            return declarations[0]
        if (
            publisher_name == clean_text(record.get("publisher"))
            and publisher_iri == clean_text(record.get("publisher_id"))
        ):
            return source_binding
        raise ValueError(
            "additional publisher lacks an exact frozen source declaration: "
            f"{record['record_id']} {publisher_name!r}"
        )

    assertions: list[dict[str, Any]] = []
    for record in sorted(records, key=lambda item: item["record_id"]):
        record_id = clean_text(record["record_id"])
        source_binding = record_bindings.get(record_id)
        if source_binding is None:
            raise ValueError(f"publisher lacks an exact source-row binding: {record_id}")
        declared_publishers = record.get("publishers")
        if not isinstance(declared_publishers, list) or not declared_publishers:
            raise ValueError(f"record has no governed publishers: {record_id}")
        seen_publisher_ids: set[str] = set()
        for publisher in declared_publishers:
            publisher_name = clean_text(publisher.get("name"))
            publisher_iri = clean_text(publisher.get("id"))
            if not publisher_name or not publisher_iri or publisher_iri in seen_publisher_ids:
                raise ValueError(f"record has colliding publisher identities: {record_id}")
            seen_publisher_ids.add(publisher_iri)
            registry_binding = registry_by_name.get(publisher_name)
            if (
                registry_binding is None
                or registry_binding.get("id") != publisher_iri
            ):
                raise ValueError(
                    "record publisher does not match the governed registry: "
                    f"{publisher_name!r}"
                )
            evidence_binding = exact_source_declaration(
                record,
                source_binding,
                publisher_name,
                publisher_iri,
            )
            source_iri = semantic_record_iri(publication_base, record)
            if source_iri == publisher_iri:
                # Reused governed agent identities are not self-published
                # discovery objects. The source declaration remains validated
                # above, but no semantically empty self-loop is emitted.
                continue
            official_source_url = semantic_web_iri(record["canonical_source_url"])
            triple = (source_iri, PUBLISHER_PREDICATE, publisher_iri)
            record_value_sha256 = sha256_bytes(
                compact_canonical_json(evidence_binding["source_value"])
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
                    "source_artifact": evidence_binding["source_artifact"],
                    "source_sha256": evidence_binding["source_sha256"],
                    "source_field": evidence_binding["source_field"],
                    "source_value_sha256": record_value_sha256,
                    "source_value_hash_canonicalization": (
                        CPSV_SOURCE_VALUE_CANONICALIZATION
                    ),
                    "locator": evidence_binding["source_field"],
                    "source_locator": clean_text(evidence_binding["record_id"]),
                    "normalization": rule_id,
                    "retrieved_at": clean_text(record.get("observed_at")),
                },
                source_iri=source_iri,
                predicate_iri=PUBLISHER_PREDICATE,
                target_iri=publisher_iri,
                role="publisher-source",
                record_id=record_id,
                assertion_plane="core",
                assertion_status="normalized",
                assertion_scope="real-world",
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
                    "retrieved_at": registry_reviewed_at,
                },
                source_iri=source_iri,
                predicate_iri=PUBLISHER_PREDICATE,
                target_iri=publisher_iri,
                role="publisher-registry",
                record_id=record_id,
                assertion_plane="core",
                assertion_status="normalized",
                assertion_scope="real-world",
            )
            preferred_label, inverse_label = relationship_labels(
                PUBLISHER_PREDICATE
            )
            assertions.append({
                "@id": relationship_assertion_id(
                    publication_base, *triple, assertion_plane="core"
                ),
                "@type": relationship_assertion_type_iris(),
                "source": {"@id": source_iri},
                "predicate": {"@id": PUBLISHER_PREDICATE},
                "target": {"@id": publisher_iri},
                "source_route": (
                    semantic_record_route(publication_base, record)
                ),
                "target_route": (
                    "publisher/" + explorer_name("publisher", publisher_iri)
                ),
                "kind": preferred_label,
                "label": preferred_label,
                "inverse_label": inverse_label,
                "assertion_plane": "core",
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
            })
    return assertions


def competent_authority_relationship_assertions(
    publication_base: str,
    records: list[dict[str, Any]],
    publisher_assertions: list[dict[str, Any]],
    cpsv_mappings: dict[str, Any],
) -> list[dict[str, Any]]:
    """Map evidenced HMLR public services to their CPSV-AP authority."""
    publication_base = publication_base.rstrip("/") + "/"
    publisher_targets_by_source: dict[str, set[str]] = {}
    for assertion in publisher_assertions:
        publisher_targets_by_source.setdefault(
            clean_text(assertion["source"]["@id"]), set()
        ).add(clean_text(assertion["target"]["@id"]))
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
        if HMLR_PUBLISHER_IRI not in publisher_targets_by_source.get(
            source_iri, set()
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
                    assertion_plane="core",
                    assertion_status="normalized",
                    assertion_scope="real-world",
                )
            )
        assertions.append(
            {
                "@id": relationship_assertion_id(
                    publication_base, *triple, assertion_plane="core"
                ),
                "@type": relationship_assertion_type_iris(),
                "source": {"@id": source_iri},
                "predicate": {"@id": COMPETENT_AUTHORITY_PREDICATE},
                "target": {"@id": HMLR_PUBLISHER_IRI},
                "source_route": (
                    semantic_record_route(publication_base, record)
                ),
                "target_route": (
                    "publisher/"
                    + explorer_name("publisher", HMLR_PUBLISHER_IRI)
                ),
                "kind": relationship_labels(COMPETENT_AUTHORITY_PREDICATE)[0],
                "label": relationship_labels(COMPETENT_AUTHORITY_PREDICATE)[0],
                "inverse_label": relationship_labels(
                    COMPETENT_AUTHORITY_PREDICATE
                )[1],
                "assertion_plane": "core",
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
            source_route = semantic_record_route(publication_base, translated)
            target_route = semantic_record_route(publication_base, english)
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
                assertion_plane="core",
                assertion_status="normalized",
                assertion_scope="real-world",
            )
            assertions.append(
                {
                    "@id": relationship_assertion_id(
                        publication_base, *triple, assertion_plane="core"
                    ),
                    "@type": relationship_assertion_type_iris(),
                    "source": {"@id": source_iri},
                    "predicate": {"@id": TRANSLATION_PREDICATE},
                    "target": {"@id": target_iri},
                    "source_route": source_route,
                    "target_route": target_route,
                    "kind": relationship_labels(TRANSLATION_PREDICATE)[0],
                    "label": relationship_labels(TRANSLATION_PREDICATE)[0],
                    "inverse_label": relationship_labels(
                        TRANSLATION_PREDICATE
                    )[1],
                    "assertion_plane": "core",
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
                    "review_status": (
                        "implementation-authorised-pending-release-review"
                    ),
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
    derivation_activities = relationship_derivation_activity_registry(
        publication_base, assertions
    )
    activity_by_rule = {
        activity["rule_iri"]: activity_iri
        for activity_iri, activity in derivation_activities.items()
    }
    for assertion in assertions:
        assertion["derivation_activity"] = activity_by_rule[assertion["rule"]]
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
                "assertion_plane",
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
        "@type": relationship_assertion_type_iris(),
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
    stage1 = load_stage1_semantic_authority()
    graph = semantic_document.get("@graph")
    if not isinstance(graph, list):
        raise ValueError("semantic document lacks an @graph array")
    semantic_rows = [
        node
        for node in graph
        if isinstance(node, dict)
        and (
            "https://chris-page-gov.github.io/okf-explorer/ns#RelationshipAssertion"
        )
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
    governed_predicates = frozenset(stage1["active_relationships"])
    if assertion_predicates != governed_predicates:
        raise ValueError(
            "semantic assertion predicates and the governed contract differ: "
            f"{sorted(assertion_predicates ^ governed_predicates)}"
        )
    governed_keys = {
        **{iri: iri for iri in governed_predicates},
        **{
            compact: iri
            for compact, iri in GOVERNED_COMPACT_PREDICATES.items()
            if iri in governed_predicates
        },
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
        rule_iri = clean_text(row.get("rule"))
        derivation_iri = clean_text(row.get("derivation"))
        activity_iri = clean_text(row.get("derivation_activity"))
        if not rule_iri or derivation_iri != rule_iri:
            raise ValueError(
                "semantic relationship derivation does not equal its rule: "
                + clean_text(row.get("@id"))
            )
        validate_stage1_relationship_rule(_predicate_iri, rule_iri)
        rule_node = nodes_by_id.get(rule_iri)
        activity_node = nodes_by_id.get(activity_iri)
        if (
            rule_node is None
            or set(
                rule_node.get("@type")
                if isinstance(rule_node.get("@type"), list)
                else [rule_node.get("@type")]
            )
            != set(stage1_entity_type_classes("TYPE-RULE"))
            or not clean_text(rule_node.get("route"))
        ):
            raise ValueError(
                "semantic relationship rule lacks its governed node and route: "
                + clean_text(row.get("@id"))
            )
        validate_stage1_assertion_plane(
            row.get("assertion_plane"), row.get("assertion_status")
        )
        if (
            not activity_iri
            or activity_node is None
            or set(
                activity_node.get("@type")
                if isinstance(activity_node.get("@type"), list)
                else [activity_node.get("@type")]
            )
            != set(stage1_entity_type_classes("TYPE-DERIVATION-ACTIVITY"))
            or clean_text(activity_node.get("prov:hadPlan", {}).get("@id"))
            != rule_iri
            or not clean_text(activity_node.get("route"))
        ):
            raise ValueError(
                "semantic relationship derivation lacks its input-bound activity: "
                + clean_text(row.get("@id"))
            )
        if _predicate_iri == GENERATED_BY_PREDICATE:
            endpoint_types = target_node.get("@type")
            endpoint_type_set = set(
                endpoint_types if isinstance(endpoint_types, list) else [endpoint_types]
            )
            if endpoint_type_set != set(
                stage1_entity_type_classes("TYPE-PROVENANCE-ACTIVITY")
            ):
                raise ValueError(
                    "wasGeneratedBy target is not a governed observation activity: "
                    + target_iri
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
    expected_resource_nodes: dict[str, dict[str, Any]] = {}
    for row in semantic_rows:
        publication_base = relationship_publication_base(
            clean_text(row.get("@id"))
        )
        for evidence in row.get("evidence", []):
            if not isinstance(evidence, dict):
                raise ValueError("semantic evidence binding is not an object")
            if evidence.get("@type") != sorted(
                stage1_entity_type_classes("TYPE-EVIDENCE-BINDING")
            ):
                raise ValueError(
                    "semantic evidence binding has wrong exact classes"
                )
            resource_node = relationship_evidence_resource_node(
                publication_base, evidence
            )
            resource_identifier = resource_node["@id"]
            existing = expected_resource_nodes.get(resource_identifier)
            if existing is not None and existing != resource_node:
                raise ValueError(
                    "semantic EvidenceResource has conflicting projections: "
                    + resource_identifier
                )
            expected_resource_nodes[resource_identifier] = resource_node
    actual_resource_nodes = {
        identifier: node
        for identifier, node in nodes_by_id.items()
        if (
            "https://chris-page-gov.github.io/okf-explorer/ns#EvidenceResource"
            in (
                node.get("@type", [])
                if isinstance(node.get("@type"), list)
                else [node.get("@type")]
            )
        )
    }
    if actual_resource_nodes != expected_resource_nodes:
        raise ValueError(
            "semantic EvidenceResource nodes do not exactly close evidence bindings"
        )
    evidence_resource_identity_digest = set_digest(expected_resource_nodes)
    return {
        "schema": "okf-hmlr-semantic-assertion-validation.v1",
        "status": "conformant",
        "schema_binding": schema_binding,
        "counts": {
            "semantic_assertions_validated": len(semantic_rows),
            "runtime_rows_mapped_and_validated": len(mapped_runtime_rows),
            "direct_triples_reconciled": len(direct_triples),
            "evidence_rows_validated": len(evidence_rows),
            "evidence_resources_validated": len(expected_resource_nodes),
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
        "evidence_resource_identity_set_sha256": (
            evidence_resource_identity_digest
        ),
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
        "active_emitted_predicates": coverage[
            "active_emitted_predicates"
        ],
        "authorised_zero_evidence_predicates": coverage[
            "authorised_zero_evidence_predicates"
        ],
        "direct_triples": counts["direct_triples_reconciled"],
        "governed_predicates": coverage["governed_predicates"],
        "predicate_assertions_emitted": sum(
            coverage["assertions_by_predicate"].values()
        ),
        "predicate_capabilities": coverage["predicate_capabilities"],
        "reified_assertions": counts["semantic_assertions_validated"],
        "relationship_chunks": runtime["chunks"],
        "relationship_runtime_routes": runtime["routes"],
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
        if "http://purl.org/vocab/cpsv#PublicService" in node_types(node)
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
    if organisation is None or (
        "http://data.europa.eu/m8g/PublicOrganisation"
        not in node_types(organisation)
    ):
        raise ValueError("HM Land Registry lacks the CPSV public-organisation type")
    if not organisation.get("skos:prefLabel") or not organisation.get(
        SPATIAL_PREDICATE
    ):
        raise ValueError(
            "HM Land Registry lacks CPSV public-organisation mandatory properties"
        )
    spatial_reference = organisation[SPATIAL_PREDICATE]
    if (
        not isinstance(spatial_reference, dict)
        or set(spatial_reference) != {"@id"}
    ):
        raise ValueError(
            "HM Land Registry spatial coverage is not one bounded IRI reference"
        )
    spatial_id = clean_text(spatial_reference.get("@id"))
    jurisdiction_decision = stage1_jurisdiction_registry().get(
        "England and Wales"
    )
    if jurisdiction_decision is None:
        raise ValueError(
            "Stage 1 lacks the governed England and Wales jurisdiction"
        )
    expected_spatial_id = semantic_jurisdiction_iri(
        publication_base, "England and Wales"
    )
    expected_spatial_classes = {
        governed_absolute_http_iri(
            class_iri,
            field="Stage 1 England and Wales jurisdiction class",
        )
        for class_iri in jurisdiction_decision.get("class_iris", [])
    }
    if not expected_spatial_classes:
        raise ValueError(
            "Stage 1 England and Wales jurisdiction lacks governed classes"
        )
    spatial_node = nodes.get(spatial_id)
    if spatial_id != expected_spatial_id or spatial_node is None:
        raise ValueError(
            "HM Land Registry spatial coverage differs from its governed "
            "England and Wales location"
        )
    if node_types(spatial_node) != expected_spatial_classes:
        raise ValueError(
            "HM Land Registry spatial coverage classes differ from the exact "
            "governed England and Wales location classes"
        )
    atu_classes = set(
        stage1_authorised_zero_entity_type_classes("TYPE-ATU-TYPE")
    )
    atu_nodes = sorted(
        identifier
        for identifier, node in nodes.items()
        if atu_classes & node_types(node)
    )
    if atu_nodes:
        raise ValueError(
            "CPSV-AP bounded projection emitted an administrative territorial "
            "unit despite its authorised-zero-evidence state: "
            + ", ".join(atu_nodes)
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
            "administrative_territorial_units": len(atu_nodes),
            "excluded_service_kind_records": len(excluded),
            "validation_failures": 0,
        },
        "bounded_projection_checks": {
            "identifier": "passed",
            "name": "passed",
            "description": "passed",
            "competent_authority": "passed",
            "public_organisation_preferred_label": "passed",
            "public_organisation_spatial_location": "passed-local-subset",
            "administrative_territorial_unit_boundary": "passed-zero-emitted",
        },
        "official_profile_boundaries": {
            "public_organisation_spatial_atu_range": (
                "not-claimed-authorised-zero-evidence"
            ),
            "cpsv_ap_shacl_conformance": "not-claimed",
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
    routes: set[str] = set()
    for node in graph:
        if not isinstance(node, dict) or not clean_text(node.get("route")):
            continue
        identity = clean_text(node.get("@id"))
        if not identity or identity in identities:
            raise ValueError(
                f"route-bearing semantic identity is missing or duplicated: {identity!r}"
            )
        route = clean_text(node.get("route"))
        if (
            node.get("route") != route
            or route.startswith("/")
            or ".." in Path(route).parts
            or not re.fullmatch(
                r"[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9-]*", route
            )
        ):
            raise ValueError(
                f"route-bearing semantic identity has an unsafe route: {identity}"
            )
        if route in routes:
            raise ValueError(
                f"semantic Reader route resolves more than one identity: {route}"
            )
        identities.add(identity)
        routes.add(route)
        raw_type = node.get("@type")
        node_types = raw_type if isinstance(raw_type, list) else [raw_type]
        title = clean_text(
            node.get("schema:name") or node.get("dcterms:identifier") or identity
        )
        entries.append(
            {
                "iri": identity,
                "route": route,
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


def governed_absolute_http_iri(value: Any, *, field: str) -> str:
    """Validate an ontology IRI without rewriting governed HTTP vocabulary IDs."""
    iri = clean_text(value)
    if not iri or iri != value or any(character.isspace() for character in iri):
        raise ValueError(f"{field} must be a canonical non-empty string")
    try:
        parsed = urlparse(iri)
        port = parsed.port
    except ValueError as exc:
        raise ValueError(f"{field} has an invalid authority or port") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or "@" in parsed.netloc
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ValueError(f"{field} must be credential-free absolute HTTP(S)")
    return iri


def semantic_class_route_registry(
    semantic_document: dict[str, Any],
    iri_registry: dict[str, Any],
    snapshot_id: str,
) -> dict[str, Any]:
    """Derive a delivery index from graph rdf:type facts and v1 routes."""
    graph = semantic_document.get("@graph")
    route_entries = iri_registry.get("entries")
    if not isinstance(graph, list) or not isinstance(route_entries, list):
        raise ValueError("class routing requires semantic graph and v1 routes")
    nodes = {
        clean_text(node.get("@id")): node
        for node in graph
        if isinstance(node, dict) and clean_text(node.get("@id"))
    }
    entries: list[dict[str, Any]] = []
    for route_entry in route_entries:
        if not isinstance(route_entry, dict):
            raise ValueError("v1 IRI-route registry contains a non-object entry")
        identity = governed_absolute_http_iri(
            route_entry.get("iri"), field="class-route identity"
        )
        route = clean_text(route_entry.get("route"))
        node = nodes.get(identity)
        if node is None or clean_text(node.get("route")) != route:
            raise ValueError(
                "class routing differs from the authoritative IRI-route entry: "
                + identity
            )
        raw_types = node.get("@type")
        node_types = raw_types if isinstance(raw_types, list) else [raw_types]
        class_iris = sorted(
            {
                governed_absolute_http_iri(
                    class_iri, field="class-route class IRI"
                )
                for class_iri in node_types
                if clean_text(class_iri)
            }
        )
        if not class_iris:
            raise ValueError("route-bearing semantic node has no class IRI: " + identity)
        entries.append(
            {"iri": identity, "route": route, "class_iris": class_iris}
        )
    entries.sort(key=lambda item: (item["iri"], item["route"]))
    if len(entries) != len(nodes_with_routes := {
        identity
        for identity, node in nodes.items()
        if clean_text(node.get("route"))
    }) or {entry["iri"] for entry in entries} != nodes_with_routes:
        raise ValueError("class routing does not cover every route-bearing graph node")
    source_plane_roots = {
        "semantic_graph_sha256": sha256_bytes(
            compact_canonical_json(semantic_document)
        ),
        "iri_route_registry_root_sha256": clean_text(
            iri_registry.get("root_sha256")
        ),
    }
    registry = {
        "schema": "okf-landregistry-class-route-registry.v1",
        "snapshot": snapshot_id,
        "entries": entries,
        "counts": {"entries": len(entries)},
        "source_plane_roots": source_plane_roots,
        "root_sha256": sha256_bytes(
            compact_canonical_json(
                {
                    "entries": entries,
                    "source_plane_roots": source_plane_roots,
                }
            )
        ),
    }
    schema = load_json(CLASS_ROUTE_REGISTRY_SCHEMA_PATH)
    if schema.get("$id") != CLASS_ROUTE_REGISTRY_SCHEMA_ID:
        raise ValueError("class-route schema identity differs from its LR contract")
    Draft202012Validator.check_schema(schema)
    failures = sorted(
        Draft202012Validator(
            schema, format_checker=FormatChecker()
        ).iter_errors(registry),
        key=lambda error: list(error.absolute_path),
    )
    if failures:
        detail = "; ".join(
            f"{error.json_path}: {error.message}" for error in failures[:12]
        )
        raise ValueError("class-route registry failed its LR schema: " + detail)
    return registry


def predicate_emission_counts(
    relationship_assertions: list[dict[str, Any]],
) -> Counter[str]:
    """Count exact predicate IRIs in the complete governed assertion plane."""
    counts: Counter[str] = Counter()
    for ordinal, assertion in enumerate(relationship_assertions):
        if not isinstance(assertion, dict):
            raise ValueError(f"semantic assertion {ordinal} must be an object")
        predicate = assertion.get("predicate")
        predicate_iri = (
            predicate.get("@id") if isinstance(predicate, dict) else predicate
        )
        governed = governed_absolute_http_iri(
            predicate_iri,
            field=f"semantic assertion {ordinal} predicate",
        )
        counts[governed] += 1
    return counts


def validate_predicate_registry_v2_document(
    registry: dict[str, Any],
    relationship_assertions: list[dict[str, Any]],
) -> None:
    """Validate structure, complete-root integrity and row reconciliation."""
    serialised_size = len(canonical_json(registry))
    if serialised_size > PREDICATE_REGISTRY_V2_MAX_BYTES:
        raise ValueError(
            "Predicate Registry v2 serialised JSON exceeds its 16 MiB ceiling"
        )
    failures = sorted(
        load_predicate_registry_v2_validator().iter_errors(registry),
        key=lambda error: list(error.absolute_path),
    )
    errors = [
        f"{error.json_path}: {error.message}" for error in failures[:12]
    ]
    predicates = registry.get("predicates")
    if not isinstance(predicates, list):
        if errors:
            raise ValueError(
                "Predicate Registry v2 failed its locked schema: "
                + "; ".join(errors)
            )
        raise ValueError("Predicate Registry v2 predicates must be an array")
    iris: list[str] = []
    declared_counts: dict[str, int] = {}
    state_counts: Counter[str] = Counter()
    for predicate in predicates:
        if not isinstance(predicate, dict):
            continue
        iri = clean_text(predicate.get("iri"))
        iris.append(iri)
        implementation = predicate.get("implementation")
        if not isinstance(implementation, dict):
            continue
        state = clean_text(implementation.get("state"))
        emitted = implementation.get("assertions_emitted")
        state_counts[state] += 1
        if isinstance(emitted, int) and not isinstance(emitted, bool):
            declared_counts[iri] = emitted
    if len(iris) != len(set(iris)):
        errors.append("predicate capability IRIs must be unique")
    if iris != sorted(iris):
        errors.append("predicate capabilities must be sorted by absolute IRI")
    counts = registry.get("counts")
    if isinstance(counts, dict):
        expected_counts = {
            "predicates": len(predicates),
            "active_emitted": state_counts["active-emitted"],
            "authorised_zero_evidence": state_counts[
                "authorised-zero-evidence"
            ],
            "assertions_emitted": sum(declared_counts.values()),
        }
        if counts != expected_counts:
            errors.append(
                "aggregate counts differ from the governed predicate material"
            )
    material = {
        key: value for key, value in registry.items() if key != "root_sha256"
    }
    if registry.get("root_sha256") != sha256_bytes(
        compact_canonical_json(material)
    ):
        errors.append(
            "root_sha256 does not bind the complete canonical registry material"
        )
    emitted_counts = predicate_emission_counts(relationship_assertions)
    undeclared = sorted(set(emitted_counts).difference(iris))
    if undeclared:
        errors.append(
            "emitted predicates are absent from the authorised capability set: "
            + ", ".join(undeclared)
        )
    for iri in sorted(set(iris)):
        if declared_counts.get(iri) != emitted_counts[iri]:
            errors.append(
                f"predicate {iri} declares {declared_counts.get(iri)!r} "
                f"assertions but {emitted_counts[iri]} were supplied"
            )
    if errors:
        raise ValueError(
            "Predicate Registry v2 is non-conformant: " + "; ".join(errors)
        )


def semantic_predicate_registry(
    relationship_assertions: list[dict[str, Any]],
    snapshot_id: str,
    generated_at: str,
) -> dict[str, Any]:
    """Build every governed capability with evidence-derived states."""
    stage1 = load_stage1_semantic_authority()
    capabilities = {
        clean_text(decision.get("predicate_iri")): decision
        for decision in stage1["relationship_types"].values()
    }
    if "" in capabilities or len(capabilities) != len(
        stage1["relationship_types"]
    ):
        raise ValueError("Stage 1 predicate capability IRIs collide")
    emitted_counts = predicate_emission_counts(relationship_assertions)
    undeclared = sorted(set(emitted_counts).difference(capabilities))
    if undeclared:
        raise ValueError(
            "emitted predicates are absent from the authorised capability set: "
            + ", ".join(undeclared)
        )
    predicates: list[dict[str, Any]] = []
    state_counts: Counter[str] = Counter()
    for iri, decision in sorted(capabilities.items()):
        domain = decision.get("domain_class_iris")
        range_ = decision.get("range_class_iris")
        evidence_policy = decision.get("registry_evidence_policy")
        if (
            not isinstance(domain, list)
            or not domain
            or not isinstance(range_, list)
            or not range_
            or not isinstance(evidence_policy, dict)
            or any(
                governed_absolute_http_iri(
                    class_iri, field=f"Stage 1 {iri} domain/range class"
                )
                != class_iri
                for class_iri in [*domain, *range_]
            )
        ):
            raise ValueError(
                "Stage 1 predicate registry fields are incomplete: " + iri
            )
        emitted = emitted_counts[iri]
        derived_state = (
            "active-emitted" if emitted else "authorised-zero-evidence"
        )
        if decision.get("implementation_state") != derived_state:
            raise ValueError(
                "Stage 1 predicate implementation state differs from complete "
                f"relationship evidence: {iri}"
            )
        state_counts[derived_state] += 1
        predicates.append(
            {
                "iri": iri,
                "preferred_label": clean_text(decision.get("label")),
                "inverse_label": clean_text(decision.get("inverse_label")),
                "description": clean_text(decision.get("description")),
                "domain": list(domain),
                "range": list(range_),
                "assertion_statuses": ["normalized"],
                "evidence_policy": copy.deepcopy(evidence_policy),
                "source_vocabulary": {
                    "iri": governed_absolute_http_iri(
                        decision.get("vocabulary_iri"),
                        field=f"Stage 1 {iri} vocabulary",
                    ),
                    "version": clean_text(decision.get("vocabulary_version")),
                },
                "status": "active",
                "implementation": {
                    "state": derived_state,
                    "assertions_emitted": emitted,
                },
            }
        )
    material = {
        "schema": "okf-predicate-registry.v2",
        "profile": PREDICATE_REGISTRY_V2_PROFILE_URL,
        "snapshot": snapshot_id,
        "generated_at": generated_at,
        "predicates": predicates,
        "counts": {
            "predicates": len(predicates),
            "active_emitted": state_counts["active-emitted"],
            "authorised_zero_evidence": state_counts[
                "authorised-zero-evidence"
            ],
            "assertions_emitted": sum(emitted_counts.values()),
        },
    }
    registry = {
        **material,
        "root_sha256": sha256_bytes(compact_canonical_json(material)),
    }
    validate_predicate_registry_v2_document(
        registry, relationship_assertions
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
    predicate_registry_schema_path: Path,
    shape_path: Path,
    class_route_schema_path: Path,
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
                "id": PREDICATE_REGISTRY_V2_SCHEMA_ID,
                "version": "2",
                **semantic_resource_reference(
                    output,
                    predicate_registry_schema_path,
                    "application/schema+json",
                ),
            },
            {
                "id": CLASS_ROUTE_REGISTRY_SCHEMA_ID,
                "version": "1.0.0",
                **semantic_resource_reference(
                    output,
                    class_route_schema_path,
                    "application/schema+json",
                ),
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
        "assertion_plane",
        "assertion_status",
        "assertion_scope",
        "authority",
        "derivation",
        "derivation_activity",
        "observed_at",
        "evidence",
        "rights",
    }
    identifiers: set[str] = set()
    evidence_identifiers: set[str] = set()
    evidence_resource_identifiers: set[str] = set()
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
        if identifier != relationship_assertion_id(
            publication_base,
            *triple,
            assertion_plane=clean_text(assertion.get("assertion_plane")),
            assertion_status=clean_text(assertion.get("assertion_status")),
            assertion_scope=clean_text(assertion.get("assertion_scope")),
        ):
            raise ValueError(
                "relationship assertion ID is not derived from its triple: "
                + identifier
            )
        if clean_text(assertion.get("assertion_plane")) != "core":
            raise ValueError(
                "relationship assertion does not declare the governed core plane: "
                + identifier
            )
        derivation = require_absolute_iri(assertion["derivation"], "derivation")
        rule = require_absolute_iri(assertion["rule"], "rule")
        require_absolute_iri(
            assertion["derivation_activity"], "derivation_activity"
        )
        if derivation != rule:
            raise ValueError(
                "relationship derivation and rule identities differ: " + identifier
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
            if item.get("@type") != sorted(
                stage1_entity_type_classes("TYPE-EVIDENCE-BINDING")
            ):
                raise ValueError(
                    "relationship evidence has wrong EvidenceBinding classes"
                )
            expected_resource_identifier = relationship_evidence_resource_id(
                publication_base, item
            )
            resource_reference = item.get("okf:evidenceResource")
            if (
                not isinstance(resource_reference, dict)
                or set(resource_reference) != {"@id"}
                or clean_text(resource_reference.get("@id"))
                != expected_resource_identifier
            ):
                raise ValueError(
                    "relationship evidence has wrong EvidenceResource reference"
                )
            evidence_resource_identifiers.add(expected_resource_identifier)
            if not clean_text(item.get("okf:bindingRole")):
                raise ValueError("relationship evidence lacks its binding role")
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
                assertion_plane=clean_text(assertion.get("assertion_plane")),
                assertion_status=clean_text(assertion.get("assertion_status")),
                assertion_scope=clean_text(assertion.get("assertion_scope")),
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


EXPLORER_QUERY_POLICY_SCHEMA = "okf-search-query-policy.v1"
EXPLORER_QUERY_TOKENISER = (
    "nfkd-lowercase-ascii-alphanumeric-component-v1"
)


def explorer_query_policy(contract: dict[str, Any]) -> dict[str, Any]:
    if contract.get("schema") != "okf-hmlr-search-contract.v1":
        raise ValueError(
            "search query-policy source schema must be "
            "okf-hmlr-search-contract.v1"
        )
    stopwords = contract.get("stopwords")
    if (
        not isinstance(stopwords, list)
        or any(
            not isinstance(token, str)
            or not token
            or len(token) > 32
            for token in stopwords
        )
        or len(stopwords) > 256
        or stopwords != sorted(set(stopwords))
        or any(
            token != token.lower()
            or re.fullmatch(r"[a-z0-9]+", token) is None
            for token in stopwords
        )
    ):
        raise ValueError(
            "search query-policy stopwords must contain at most 256 sorted "
            "unique lower-case ASCII alphanumeric components of at most 32 "
            "characters"
        )
    if contract.get("token_pattern") != r"[a-z0-9]+":
        raise ValueError(
            "search query-policy token_pattern must be exactly [a-z0-9]+"
        )
    token_min_length = contract.get("token_min_length")
    if (
        not isinstance(token_min_length, int)
        or isinstance(token_min_length, bool)
        or token_min_length != 2
    ):
        raise ValueError(
            "search query-policy token_min_length must be exactly 2"
        )
    minimum_should_match = contract.get("minimum_should_match")
    expected_minimum = {
        "apply_from_query_tokens": 3,
        "minimum_matches": 2,
        "ratio_numerator": 3,
        "ratio_denominator": 10,
    }
    if not isinstance(minimum_should_match, dict):
        raise ValueError(
            "search query-policy minimum_should_match must be an object"
        )
    if any(
        not isinstance(value, int) or isinstance(value, bool)
        for value in minimum_should_match.values()
    ):
        raise ValueError(
            "search query-policy minimum_should_match values must be integers"
        )
    ratio_numerator = minimum_should_match.get("ratio_numerator")
    ratio_denominator = minimum_should_match.get("ratio_denominator")
    if (
        ratio_numerator is None
        or ratio_denominator is None
        or ratio_numerator < 1
        or ratio_denominator < 1
        or ratio_numerator > ratio_denominator
    ):
        raise ValueError(
            "search query-policy ratio must use 1 <= numerator <= denominator"
        )
    if minimum_should_match != expected_minimum:
        raise ValueError(
            "search query-policy minimum_should_match differs from the "
            "settled Explorer contract"
        )
    return {
        "schema": EXPLORER_QUERY_POLICY_SCHEMA,
        "tokeniser": EXPLORER_QUERY_TOKENISER,
        "stopwords": list(stopwords),
        "minimum_should_match": dict(minimum_should_match),
    }


def explorer_worker_tokens(
    value: str,
    stopwords: set[str],
    token_min_length: int,
) -> list[str]:
    normalized = re.sub(
        r"[\u0300-\u036f]",
        "",
        unicodedata.normalize("NFKD", clean_text(value)).lower(),
    )
    return sorted(
        {
            token
            for token in re.findall(r"[a-z0-9]+", normalized)
            if len(token) >= token_min_length and token not in stopwords
        }
    )


def explorer_search_publishers(
    record: dict[str, Any],
) -> list[dict[str, str]]:
    """Return every governed publisher binding used by Explorer search."""
    primary_filter_value = clean_text(record.get("publisher"))
    primary_title = clean_text(record.get("publisher_title"))
    primary_id = clean_text(record.get("publisher_id"))
    declared_publishers = record.get("publishers")
    if declared_publishers is None:
        # Preserve the direct unit/API compatibility shape while requiring the
        # same primary publisher fields as the generated dataset projection.
        declared_publishers = [{"name": primary_title, "id": primary_id}]
    if not isinstance(declared_publishers, list) or not declared_publishers:
        raise ValueError("search record must declare at least one publisher")

    publishers: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for publisher in declared_publishers:
        if not isinstance(publisher, dict):
            raise ValueError("search publisher binding must be an object")
        title = clean_text(publisher.get("name"))
        publisher_id = clean_text(publisher.get("id"))
        if (
            not title
            or not publisher_id
            or publisher.get("name") != title
            or publisher.get("id") != publisher_id
        ):
            raise ValueError(
                "search publisher binding is incomplete or non-canonical"
            )
        if publisher_id in seen_ids:
            raise ValueError("search publisher binding identity collides")
        seen_ids.add(publisher_id)
        publishers.append(
            {
                "id": publisher_id,
                "title": title,
                "filter_value": explorer_name("publisher", publisher_id),
            }
        )

    primary = publishers[0]
    if (
        not primary_filter_value
        or not primary_title
        or not primary_id
        or primary["filter_value"] != primary_filter_value
        or primary["title"] != primary_title
        or primary["id"] != primary_id
    ):
        raise ValueError(
            "search primary publisher differs from its declared publisher list"
        )
    return publishers


def explorer_search_field_values(
    record: dict[str, Any], field: str
) -> list[str]:
    if field == "publisher":
        return [
            publisher["filter_value"]
            for publisher in explorer_search_publishers(record)
        ]
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
) -> dict[str, dict[str, Any]]:
    search_dir = output / "data" / "explorer" / "search"
    search_contract = load_json(ROOT / "pages" / "search-contract.json")
    query_policy = explorer_query_policy(search_contract)
    query_stopwords = set(query_policy["stopwords"])
    token_min_length = search_contract["token_min_length"]
    publishers_by_ordinal = {
        int(dataset["ordinal"]): explorer_search_publishers(dataset)
        for dataset in datasets
    }
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
            "publisher": " ".join(
                publisher["title"]
                for publisher in publishers_by_ordinal[ordinal]
            ),
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
            for token in explorer_worker_tokens(
                value,
                query_stopwords,
                token_min_length,
            ):
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

    governed_publishers = load_publisher_registry_entries()
    publisher_filter_counts = Counter(
        publisher["filter_value"]
        for dataset in datasets
        for publisher in publishers_by_ordinal[int(dataset["ordinal"])]
    )
    entity_rows_by_filter: dict[str, dict[str, Any]] = {}
    for dataset in datasets:
        for publisher in publishers_by_ordinal[int(dataset["ordinal"])]:
            filter_value = publisher["filter_value"]
            publisher_title = publisher["title"]
            publisher_id = publisher["id"]
            governed = governed_publishers.get(publisher_title)
            if (
                governed is None
                or clean_text(governed.get("id")) != publisher_id
                or filter_value != explorer_name("publisher", publisher_id)
            ):
                raise ValueError(
                    "search publisher entity differs from its governed registry"
                )
            entity = {
                "id": publisher_id,
                "label": publisher_title,
                "kind": "organisation",
                "filter_key": "publisher",
                "filter_value": filter_value,
                "count": publisher_filter_counts[filter_value],
                "route": f"publisher/{filter_value}",
            }
            existing = entity_rows_by_filter.get(filter_value)
            if existing is not None and existing != entity:
                raise ValueError("search publisher entity identity collides")
            entity_rows_by_filter[filter_value] = entity
    normalized_labels = [
        " ".join(row["label"].casefold().split())
        for row in entity_rows_by_filter.values()
    ]
    if len(normalized_labels) != len(set(normalized_labels)):
        raise ValueError("search publisher entity labels are ambiguous")
    entity_rows = sorted(
        entity_rows_by_filter.values(),
        key=lambda row: (row["label"].casefold(), row["id"]),
    )
    entities_path = search_dir / "entities.json"
    write_compact_json(
        entities_path,
        {
            "schema": "okf-static-search-entities.v1",
            "entities": entity_rows,
        },
    )
    entities_reference = explorer_reference(output, entities_path)
    entities_shard_reference = dict(entities_reference)
    entities_shard_reference["snapshot"] = snapshot_id
    shard_groups["support"].append(entities_shard_reference)

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
        "token_min_length": token_min_length,
        "prefix_min_length": 3,
        "lexicon_shard_length": 2,
        "result_limit": 200,
        "query_policy": query_policy,
        "result_doc_chunk_size": SHARD_SIZE,
        "weights": weights,
        "field_masks": masks,
        "counts": {
            "documents": len(datasets),
            "entities": len(entity_rows),
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
            "entities": entities_path.relative_to(output).as_posix(),
        },
        "shard_metadata": metadata_reference,
        "shard_manifest_sha256": shard_manifest_sha256,
    }
    manifest_path = search_dir / "manifest.json"
    write_json(manifest_path, manifest)
    return {
        "manifest": explorer_reference(output, manifest_path),
        "entities": entities_reference,
    }


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
    validate_stage1_assertion_plane(
        relationship.get("assertion_plane"),
        relationship.get("assertion_status"),
        plane_iri=plane_id,
    )
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
    if relationship["assertion_status"] == "normalized":
        authority["label"] = "Derived"
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
            "assertion": (
                "See source rights."
                if relationship["assertion_status"] == "normalized"
                else relationship["rights"]["assertion"]
            ),
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
    if (
        relationship["assertion_status"] != "normalized"
        and "review_status" in relationship
    ):
        row["review_status"] = copy.deepcopy(relationship["review_status"])
    if relationship["assertion_status"] == "model-derived":
        if "review_status" not in relationship:
            raise ValueError("model-derived rich runtime row lacks review status")
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
    """Project the strings retained by Explorer v0.6.2 for limit checks."""
    projected = copy.deepcopy(row)
    projected["lifecycle"] = lifecycle
    status = row["assertion_status"]
    if status != "inferred":
        projected.pop("rule", None)
        projected.pop("supporting_assertions", None)
    if status not in {"inferred", "model-derived"}:
        projected.pop("derivation_activity", None)
        projected.pop("confidence_score", None)
    for evidence_ordinal, evidence in enumerate(projected["evidence"]):
        if "source_value" in evidence and not isinstance(
            evidence["source_value"], str
        ):
            raise ValueError(
                "rich runtime row evidence source_value must be text for "
                "Explorer v0.6.2 projection: "
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
            f"{subject} is {measured}, exceeding locked Explorer v0.6.2 "
            f"{limit_name}={maximum}"
        )


def validate_rich_relationship_full_hydration_preflight(
    relationships: list[dict[str, Any]],
) -> dict[str, int | str]:
    """Measure the exact fresh source projection before reserving a swap slot."""

    limits = locked_rich_relationship_limits()
    plane_id = clean_text(stage1_core_relationship_plane().get("iri"))
    retained_text_units_total = 0
    retained_text_units_maximum = 0
    for relationship in relationships:
        projected = rich_runtime_reader_projection(
            rich_runtime_relationship(relationship, plane_id)
        )
        measured = retained_text_units(projected)
        retained_text_units_total += measured
        retained_text_units_maximum = max(
            retained_text_units_maximum,
            measured,
        )
        require_locked_maximum(
            measured,
            "maximum_rich_relationship_row_text_units",
            limits,
            f"rich relationship source row {relationship['id']} retained text",
        )
    require_locked_maximum(
        len(relationships),
        "maximum_relationship_rows",
        limits,
        "rich relationship source row total",
    )
    maximum = limits["maximum_rich_relationship_retained_text_units"]
    require_locked_maximum(
        retained_text_units_total,
        "maximum_rich_relationship_retained_text_units",
        limits,
        "rich relationship source full hydration retained text",
    )
    return {
        "schema": "okf-hmlr-rich-runtime-source-preflight.v1",
        "rows": len(relationships),
        "retained_text_units": retained_text_units_total,
        "maximum_row_retained_text_units": retained_text_units_maximum,
        "locked_maximum_retained_text_units": maximum,
        "remaining_retained_text_units": maximum - retained_text_units_total,
    }


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
            "version": "0.6.2",
            "commit_sha": EXPLORER_V062_COMMIT,
            "git_tree": EXPLORER_V062_GIT_TREE,
            "large_corpus_source_sha256": (
                EXPLORER_V062_LARGE_CORPUS_SHA256
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
    runtime_id = validate_stage1_identity(
        "IDF-SEMANTIC-RUNTIME",
        urljoin(publication_base, "id/semantic-runtime/relationships"),
        expected_role="runtime-control",
    )
    route_locator_id = validate_stage1_identity(
        "IDF-SEMANTIC-RUNTIME",
        urljoin(publication_base, "id/semantic-runtime/route-locator"),
        expected_role="runtime-control",
    )
    core_plane = stage1_core_relationship_plane()
    plane_id = clean_text(core_plane.get("iri"))
    plane_name = clean_text(core_plane.get("name"))
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
            f"data/semantic/runtime/{plane_name}/"
            f"relationships-{chunk_number:03d}.json.gz"
        )
        path = output / relative
        write_gzip_json(path, selected)
        reference = explorer_reference(output, path)
        chunk_id = validate_stage1_identity(
            "IDF-SEMANTIC-RUNTIME-CHUNK",
            urljoin(
                publication_base,
                f"id/semantic-runtime-chunk/{plane_name}-"
                f"{chunk_number:03d}-{reference['sha256'][:16]}",
            ),
            expected_role="runtime-control",
        )
        chunk_rows.append((relative, selected))
        chunk_metadata.append(
            {
                **reference,
                "id": chunk_id,
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
                        "name": plane_name,
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
        "@id": runtime_id,
        "schema": "okf-rich-relationship-runtime-manifest.v1",
        "snapshot": snapshot_id,
        "generated_at": generated_at,
        "semantic_manifest": "okf-bundle.yamlld",
        "assertion_contract": SEMANTIC_ASSERTION_SCHEMA_BUNDLE_PATH,
        "row_contract": RICH_RELATIONSHIP_ROW_SCHEMA_BUNDLE_PATH,
        "default_planes": [plane_name] if core_plane.get("default") else [],
        "route_locator": {
            "path": locator_reference["path"],
            "id": route_locator_id,
            "routes": len(incident),
            "buckets": len(bucket_metadata),
            "sha256": locator_reference["sha256"],
        },
        "planes": [
            {
                "id": plane_id,
                "name": plane_name,
                "active": core_plane.get("implementation_state")
                == "active-emitted",
                "lifecycle": clean_text(core_plane.get("lifecycle")),
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
    publisher_counts = Counter(
        clean_text(publisher["id"])
        for record in records
        for publisher in record["publishers"]
    )
    publisher_titles = {
        clean_text(publisher["id"]): clean_text(publisher["name"])
        for record in records
        for publisher in record["publishers"]
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
    search_projection = write_explorer_search(
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
            "search": search_projection["manifest"]["path"],
            "entities": search_projection["entities"]["path"],
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
        "search_manifest": search_projection["manifest"],
        "search_entities": search_projection["entities"],
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
  <main id="main" class="shell prose" tabindex="-1">
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
        validate_predicate_registry_profile_lock,
        load_predicate_registry_v2_validator,
        load_stage1_semantic_authority,
        load_curated_rights_access_classifications,
        load_type_kind_crosswalk,
        load_publisher_registry_entries,
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
    predicate_registry_profile_receipt = (
        validate_predicate_registry_profile_lock()
    )
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
    validate_stage1_retained_native_type_closure(records)
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
    rich_runtime_source_preflight = (
        validate_rich_relationship_full_hydration_preflight(relationship_rows)
    )
    input_receipts = governed_input_receipts(
        snapshot,
        build_input_snapshot=build_input_snapshot,
    )
    # Validate every source-governed semantic structure that does not depend on
    # staged output bytes before reserving the atomic publication swap slot.
    # Generated-file reconciliation remains inside the transaction.
    projection_validation_receipts: dict[str, Any] = {}
    semantic_document = jsonld_projection(
        publication_base,
        snapshot,
        records,
        relationship_assertions,
        cpsv_mappings,
        config,
        validation_receipts=projection_validation_receipts,
    )
    require_profile_conformance(
        semantic_document,
        "bundle.schema.json",
        "semantic bundle root",
    )
    semantic_validation = validate_semantic_relationship_planes(
        semantic_document, relationship_rows
    )
    semantic_validation["class_closure"] = projection_validation_receipts[
        "class_closure"
    ]
    semantic_validation["cpsv_ap"] = validate_cpsv_ap_projection(
        semantic_document,
        records,
        relationship_assertions,
        cpsv_mappings,
        cpsv_vendor_receipt,
        publication_base,
    )
    semantic_validation["context_alignment"] = (
        validate_semantic_context_alignment()
    )
    iri_registry = semantic_iri_route_registry(
        semantic_document, snapshot["snapshot_id"]
    )
    predicate_registry = semantic_predicate_registry(
        relationship_assertions,
        snapshot["snapshot_id"],
        config["generated_at"],
    )
    class_route_registry = semantic_class_route_registry(
        semantic_document, iri_registry, snapshot["snapshot_id"]
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
        # Keep the complete equivalent JSON-LD graph in ordinary Git and the
        # GitHub Pages source tree. Whitespace is not semantic, so this one
        # large serialisation uses the existing deterministic compact writer;
        # the parsed data model remains byte-for-byte comparable with YAML-LD.
        write_compact_json(staging / "okf-bundle.jsonld", semantic_document)
        write_yaml_ld(staging / "okf-bundle.yamlld", semantic_document)
        rich_runtime_validation = explorer_projection[
            "relationship_runtime_validation"
        ]
        generated_retained_text_units = rich_runtime_validation[
            "consumer_limits"
        ]["maxima"]["full_hydration_retained_text_units"]
        if generated_retained_text_units != rich_runtime_source_preflight[
            "retained_text_units"
        ]:
            raise ValueError(
                "fresh source and generated rich-runtime retained-text "
                "measurements differ"
            )
        rich_runtime_validation["source_preflight"] = (
            rich_runtime_source_preflight
        )
        semantic_validation["rich_relationship_runtime"] = (
            rich_runtime_validation
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
        class_route_registry_path = staging / CLASS_ROUTE_REGISTRY_BUNDLE_PATH
        class_route_schema_path = (
            staging / CLASS_ROUTE_REGISTRY_SCHEMA_BUNDLE_PATH
        )
        predicate_registry_path = staging / PREDICATE_REGISTRY_BUNDLE_PATH
        predicate_registry_schema_path = (
            staging / PREDICATE_REGISTRY_V2_SCHEMA_BUNDLE_PATH
        )
        copy_repository_input(
            CLASS_ROUTE_REGISTRY_SCHEMA_PATH, class_route_schema_path
        )
        copy_repository_input(
            PREDICATE_REGISTRY_V2_SCHEMA_PATH,
            predicate_registry_schema_path,
        )
        write_json(iri_registry_path, iri_registry)
        write_json(class_route_registry_path, class_route_registry)
        write_json(predicate_registry_path, predicate_registry)
        semantic_model_path = staging / SEMANTIC_MODEL_BUNDLE_PATH
        semantic_model = semantic_model_descriptor(
            staging,
            publication_base,
            local_context_path,
            canonical_context_path,
            iri_registry_path,
            predicate_registry_path,
            predicate_registry_schema_path,
            shape_path,
            class_route_schema_path,
            cpsv_context_path,
            cpsv_vocabulary_path,
            cpsv_shape_path,
        )
        write_json(semantic_model_path, semantic_model)
        semantic_validation["profile_validation"] = {
            "bundle": "conformant",
            "semantic_model": "conformant",
            "iri_route_registry": "conformant",
            "class_route_registry": "conformant",
            "predicate_registry_v2": predicate_registry_profile_receipt,
            "inference": "not-run",
        }
        semantic_validation["coverage"] = {
            "route_bearing_semantic_identities": iri_registry["counts"]["entries"],
            "predicate_capabilities": predicate_registry["counts"]["predicates"],
            "active_emitted_predicates": predicate_registry["counts"][
                "active_emitted"
            ],
            "authorised_zero_evidence_predicates": predicate_registry[
                "counts"
            ]["authorised_zero_evidence"],
            "governed_predicates": predicate_registry["counts"][
                "active_emitted"
            ],
            "assertions_by_predicate": {
                row["iri"]: row["implementation"]["assertions_emitted"]
                for row in predicate_registry["predicates"]
            },
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
            "class_route_registry": semantic_resource_reference(
                staging, class_route_registry_path, "application/json"
            ),
            "class_route_registry_schema": semantic_resource_reference(
                staging, class_route_schema_path, "application/schema+json"
            ),
            "predicate_registry": semantic_resource_reference(
                staging, predicate_registry_path, "application/json"
            ),
            "predicate_registry_schema": semantic_resource_reference(
                staging,
                predicate_registry_schema_path,
                "application/schema+json",
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
            "predicate_registry_schema": explorer_reference(
                staging, predicate_registry_schema_path
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
        descriptor["extensions"]["okf-landregistry-class-routing.v1"] = {
            "authority": (
                "Deterministic delivery index derived from semantic graph "
                "rdf:type facts and the frozen Bundle Wiki v1 IRI registry"
            ),
            "schema": explorer_reference(staging, class_route_schema_path),
            "registry": explorer_reference(staging, class_route_registry_path),
            "source_plane_roots": class_route_registry["source_plane_roots"],
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
            "predicate_registry_profile_lock": (
                predicate_registry_profile_receipt
            ),
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
