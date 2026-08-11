from __future__ import annotations

import hashlib
import gzip
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_authored_site_browser_quality.mjs"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2) + "\n").encode()


def compact_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def gzip_json_bytes(value: Any) -> bytes:
    return gzip.compress(compact_json_bytes(value), compresslevel=9, mtime=0)


def retained_text_units(value: Any) -> int:
    if isinstance(value, str):
        return len(value.encode("utf-16-le")) // 2
    if isinstance(value, list):
        return sum(retained_text_units(item) for item in value)
    if isinstance(value, dict):
        return sum(retained_text_units(item) for item in value.values())
    return 0


def write(path: Path, data: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode() if isinstance(data, str) else data)


def git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def initialise_repository(repository: Path) -> str:
    git(repository, "init", "--quiet")
    git(repository, "add", ".")
    git(
        repository,
        "-c",
        "user.name=OKF runner test",
        "-c",
        "user.email=runner-test@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "fixture",
    )
    return git(repository, "rev-parse", "HEAD")


class RunnerFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.candidate = root / "candidate"
        self.explorer = root / "explorer"
        self.output = root / "evidence.json"
        self.candidate.mkdir()
        self.explorer.mkdir()
        self.explorer_commit = self._build_explorer()
        self.candidate_commit, self.release_root = self._build_candidate()

    def _build_explorer(self) -> str:
        app = self.explorer / "apps" / "okf-explorer"
        write(self.explorer / ".gitignore", "node_modules/\n")
        app_package = {
            "name": "@okf/explorer",
            "version": "0.6.1",
            "type": "module",
        }
        write(app / "package.json", json_bytes(app_package))
        write(app / "pnpm-lock.yaml", "lockfileVersion: '9.0'\n")
        write(
            app / "src" / "lib" / "sources" / "largeCorpus.ts",
            "export const fixture = 'rich relationship limits';\n",
        )
        executable_paths = {
            "runner": (
                "apps/okf-explorer/scripts/"
                "run_external_bundle_acceptance.mjs"
            ),
            "wrapper": (
                "apps/okf-explorer/scripts/run_acceptance_invocation.mjs"
            ),
            "invocation_lock_module": (
                "apps/okf-explorer/scripts/acceptance_invocation_lock.mjs"
            ),
            "contract_module": (
                "apps/okf-explorer/scripts/"
                "external_bundle_acceptance_contract.mjs"
            ),
            "app_build_manifest_module": (
                "apps/okf-explorer/scripts/app_build_manifest.mjs"
            ),
            "deterministic_build_script": (
                "apps/okf-explorer/scripts/check_deterministic_build.mjs"
            ),
        }
        self.acceptance_executable_materials = {}
        for name, relative in executable_paths.items():
            payload = f"export const fixture = {name!r};\n".encode()
            write(self.explorer / relative, payload)
            self.acceptance_executable_materials[name] = {
                "path": relative,
                "bytes": len(payload),
                "sha256": sha256(payload),
            }
        write(app / "build" / "index.html", "<!doctype html><title>Explorer fixture</title>\n")
        material_bytes = (app / "build" / "index.html").read_bytes()
        materials = [
            {
                "path": "index.html",
                "bytes": len(material_bytes),
                "sha256": sha256(material_bytes),
            }
        ]
        build_manifest = {
            "schema": "okf-explorer-app-build-manifest.v1",
            "algorithm": "sha256-canonical-json-materials-v1",
            "file_count": 1,
            "tree_sha256": sha256(
                (json.dumps(materials, separators=(",", ":")) + "\n").encode()
            ),
            "materials": materials,
        }
        write(
            app / "build" / "okf-explorer-build-manifest.json",
            json_bytes(build_manifest),
        )
        commit = initialise_repository(self.explorer)

        # Dependencies are intentionally installed after the commit and ignored,
        # matching a clean pnpm checkout with a populated node_modules tree.
        packages = {
            "@playwright/test": "1.62.1",
            "@axe-core/playwright": "4.12.1",
        }
        for name, version in packages.items():
            package_root = app / "node_modules" / Path(name)
            write(
                package_root / "package.json",
                json_bytes({"name": name, "version": version, "main": "index.js"}),
            )
            write(package_root / "index.js", "module.exports = {};\n")
        self.app_package = app_package
        self.materials = materials
        self.build_manifest = build_manifest
        return commit

    def _build_candidate(self) -> tuple[str, str]:
        profile_root = self.candidate / "profiles" / "bundle-wiki" / "v1"
        source_vendor_path = (
            ROOT / "profiles" / "bundle-wiki" / "v1.vendor-lock.json"
        )
        vendor_lock = json.loads(source_vendor_path.read_text())
        aggregate = vendor_lock["identity"]["sha256"]
        for profile_row in vendor_lock["files"]:
            write(
                profile_root / profile_row["path"],
                (
                    ROOT / "profiles" / "bundle-wiki" / "v1"
                    / profile_row["path"]
                ).read_bytes(),
            )
        vendor_path = (
            self.candidate
            / "profiles"
            / "bundle-wiki"
            / "v1.vendor-lock.json"
        )
        write(vendor_path, source_vendor_path.read_bytes())

        predicate_lock_source = (
            ROOT / "profiles" / "predicate-registry" / "v2.lock.json"
        )
        predicate_lock = json.loads(predicate_lock_source.read_text())
        predicate_lock_path = (
            self.candidate
            / "profiles"
            / "predicate-registry"
            / "v2.lock.json"
        )
        write(predicate_lock_path, predicate_lock_source.read_bytes())
        for row in predicate_lock["files"]:
            write(
                self.candidate
                / "profiles"
                / "predicate-registry"
                / "v2"
                / row["path"],
                (
                    ROOT
                    / "profiles"
                    / "predicate-registry"
                    / "v2"
                    / row["path"]
                ).read_bytes(),
            )

        app = self.explorer / "apps" / "okf-explorer"
        contract_paths = [
            "apps/okf-explorer/package.json",
            "apps/okf-explorer/pnpm-lock.yaml",
            "apps/okf-explorer/src/lib/sources/largeCorpus.ts",
            *(
                material["path"]
                for material in self.acceptance_executable_materials.values()
            ),
        ]
        contract_sources = [
            {
                "path": relative,
                "sha256": sha256((self.explorer / relative).read_bytes()),
            }
            for relative in contract_paths
        ]
        build_manifest_bytes = (
            app / "build" / "okf-explorer-build-manifest.json"
        ).read_bytes()
        lock = {
            "schema": "okf-explorer-consumer-lock.v1",
            "consumer": {
                "name": "@okf/explorer",
                "repository": (
                    "https://github.com/chris-page-gov/okf-explorer"
                ),
                "release_tag": "v0.6.1",
                "version": "0.6.1",
                "commit_sha": self.explorer_commit,
                "annotated_tag_object_sha": "b" * 40,
                "immutable_release": {
                    "id": 1,
                    "immutable": True,
                    "published_at": "2026-08-11T12:34:04Z",
                    "url": (
                        "https://github.com/chris-page-gov/okf-explorer/"
                        "releases/tag/v0.6.1"
                    ),
                },
                "contract_sources": contract_sources,
                "acceptance_executable_materials": (
                    self.acceptance_executable_materials
                ),
                "executable_build": {
                    "algorithm": "sha256-canonical-json-materials-v1",
                    "files": 1,
                    "tree_sha256": self.build_manifest["tree_sha256"],
                    "build_manifest_sha256": sha256(build_manifest_bytes),
                    "index_sha256": self.materials[0]["sha256"],
                },
                "semantic_profile": {
                    "profile": vendor_lock["profile"],
                    "source_release": vendor_lock["release"],
                    "git_tree": vendor_lock["release"]["git_tree"],
                    "files": vendor_lock["file_count"],
                    "local_vendor_lock": "profiles/bundle-wiki/v1.vendor-lock.json",
                    "local_vendor_lock_sha256": sha256(vendor_path.read_bytes()),
                    "aggregate_identity_sha256": aggregate,
                },
                "predicate_registry": {
                    "supported_schemas": [
                        "okf-predicate-registry.v1",
                        "okf-predicate-registry.v2",
                    ],
                    "required_projection_schema": "okf-predicate-registry.v2",
                    "profile": predicate_lock["profile"],
                    "source_release": {
                        "repository": (
                            "https://github.com/chris-page-gov/okf-explorer"
                        ),
                        "version": "0.6.1",
                        "tag": "v0.6.1",
                        "annotated_tag_object_sha": "b" * 40,
                        "commit_sha": self.explorer_commit,
                        "immutable_release_id": 1,
                        "published_at": "2026-08-11T12:34:04Z",
                    },
                    "profile_lock": {
                        "url": (
                            "https://chris-page-gov.github.io/okf-explorer/"
                            "profile/predicate-registry/v2.lock.json"
                        ),
                        "local_path": (
                            "profiles/predicate-registry/v2.lock.json"
                        ),
                        "bytes": len(predicate_lock_path.read_bytes()),
                        "sha256": sha256(predicate_lock_path.read_bytes()),
                        "identity_sha256": predicate_lock["identity"]["sha256"],
                    },
                    "schema": {
                        "url": (
                            "https://chris-page-gov.github.io/okf-explorer/"
                            "profile/predicate-registry/v2/"
                            "predicate-registry.schema.json"
                        ),
                        "local_path": (
                            "profiles/predicate-registry/v2/"
                            "predicate-registry.schema.json"
                        ),
                        "bytes": predicate_lock["files"][1]["bytes"],
                        "sha256": predicate_lock["files"][1]["sha256"],
                    },
                },
            },
            "compatibility_window": {
                "policy": "exact-version-only",
                "minimum_version": "0.6.1",
                "maximum_version": "0.6.1",
            },
            "runtime_harness": {"browser": "chromium"},
            "rich_relationship_runtime": {
                "manifest_schema": "okf-rich-relationship-runtime-manifest.v1",
                "row_schema": "okf-relationship-runtime-row.v1",
                "route_locator_schema": (
                    "okf-rich-relationship-route-locator.v1"
                ),
                "route_bucket_schema": (
                    "okf-rich-relationship-route-locator-bucket.v1"
                ),
                "route_hash_algorithm": "sha256-utf8-first-byte-hex",
                "content_encoding": "gzip",
            },
            "limits": {
                "maximum_json_bytes": 67_108_864,
                "maximum_relationship_rows": 300_000,
                "maximum_rich_relationship_route_chunks": 64,
                "maximum_rich_relationship_route_rows": 100_000,
                "maximum_rich_relationship_chunk_rows": 50_000,
                "maximum_rich_relationship_chunk_bytes": 8_388_608,
                "maximum_rich_relationship_decoded_chunk_bytes": 67_108_864,
                "maximum_rich_relationship_hydration_compressed_bytes": (
                    67_108_864
                ),
                "maximum_rich_relationship_retained_text_units": 33_554_432,
                "maximum_rich_relationship_row_text_units": 32_768,
                "maximum_rich_relationship_evidence_items": 16,
                "maximum_rich_relationship_supporting_assertions": 128,
                "maximum_rich_relationship_cached_chunks": 16,
                "maximum_rich_relationship_planes": 16,
                "maximum_rich_relationship_chunks": 10_000,
            },
        }
        lock_path = (
            self.candidate / "contracts" / "okf-explorer.consumer-lock.json"
        )
        write(lock_path, json_bytes(lock))

        bundle = self.candidate / "bundle"
        plane_id = "https://example.invalid/id/semantic-plane/core"
        assertion_id = "https://example.invalid/id/assertion/fixture"
        row = {
            "schema": "okf-relationship-runtime-row.v1",
            "id": assertion_id,
            "assertion_id": assertion_id,
            "source": "dataset/fixture",
            "target": "source/fixture",
            "source_route": "dataset/fixture",
            "target_route": "source/fixture",
            "source_iri": "https://example.invalid/id/dataset/fixture",
            "target_iri": "https://example.invalid/id/source/fixture",
            "predicate": "http://purl.org/dc/terms/source",
            "predicate_iri": "http://purl.org/dc/terms/source",
            "kind": "has source",
            "label": "has source",
            "inverse_label": "is source for",
            "direction": "source-to-target",
            "assertion_status": "official",
            "assertion_scope": "real-world",
            "authority": {
                "class": "official",
                "label": "Fixture publisher",
                "source": "https://example.invalid/source",
            },
            "derivation": "https://example.invalid/id/rule/fixture",
            "observed_at": "2026-08-10T00:00:00Z",
            "evidence": [
                {
                    "@id": "https://example.invalid/id/evidence/fixture",
                    "type": "official-source-record",
                    "url": "https://example.invalid/source",
                    "source_field": "fixture",
                    "source_value_sha256": "b" * 64,
                    "retrieved_at": "2026-08-10T00:00:00Z",
                }
            ],
            "rights": {
                "source": "https://example.invalid/rights",
                "assertion": "Fixture rights statement.",
            },
            "plane": plane_id,
            "active": True,
        }
        chunk_path = (
            "data/semantic/runtime/core/relationships-000.json.gz"
        )
        chunk_bytes = gzip_json_bytes([row])
        write(bundle / chunk_path, chunk_bytes)
        chunk_reference = {
            "id": "https://example.invalid/id/runtime-chunk/core-000",
            "path": chunk_path,
            "media_type": "application/json",
            "content_encoding": "gzip",
            "bytes": len(chunk_bytes),
            "sha256": sha256(chunk_bytes),
            "count": 1,
            "records": 1,
        }
        routes = [row["source"], row["target"]]
        assertion_digest = sha256(compact_json_bytes([assertion_id]))
        routes_by_prefix: dict[str, list[dict[str, Any]]] = {}
        for route in routes:
            prefix = sha256(route.encode())[:2]
            routes_by_prefix.setdefault(prefix, []).append(
                {
                    "route": route,
                    "chunks": [chunk_path],
                    "planes": [
                        {
                            "name": "core",
                            "assertions": 1,
                            "assertion_ids_sha256": assertion_digest,
                            "chunks": [chunk_path],
                        }
                    ],
                }
            )
        bucket_metadata = []
        bucket_decoded_sizes = []
        bucket_compressed_sizes = []
        for prefix, bucket_routes in sorted(routes_by_prefix.items()):
            bucket_document = {
                "schema": (
                    "okf-rich-relationship-route-locator-bucket.v1"
                ),
                "generated_at": "2026-08-10T00:00:00Z",
                "hash_algorithm": "sha256-utf8-first-byte-hex",
                "bucket": prefix,
                "routes": bucket_routes,
                "counts": {
                    "routes": len(bucket_routes),
                    "chunk_references": len(bucket_routes),
                },
            }
            bucket_path = (
                "data/semantic/runtime/route-locator/"
                f"bucket-{prefix}.json.gz"
            )
            bucket_bytes = gzip_json_bytes(bucket_document)
            write(bundle / bucket_path, bucket_bytes)
            bucket_metadata.append(
                {
                    "bucket": prefix,
                    "path": bucket_path,
                    "bytes": len(bucket_bytes),
                    "sha256": sha256(bucket_bytes),
                    "content_encoding": "gzip",
                    "routes": len(bucket_routes),
                    "chunk_references": len(bucket_routes),
                }
            )
            bucket_decoded_sizes.append(len(compact_json_bytes(bucket_document)))
            bucket_compressed_sizes.append(len(bucket_bytes))
        locator = {
            "schema": "okf-rich-relationship-route-locator.v1",
            "generated_at": "2026-08-10T00:00:00Z",
            "hash_algorithm": "sha256-utf8-first-byte-hex",
            "bucket_path_template": (
                "data/semantic/runtime/route-locator/"
                "bucket-{prefix}.json.gz"
            ),
            "buckets": bucket_metadata,
            "counts": {
                "routes": len(routes),
                "buckets": len(bucket_metadata),
                "chunk_references": len(routes),
            },
        }
        locator_path = "data/semantic/runtime/route-locator/manifest.json"
        locator_bytes = json_bytes(locator)
        write(bundle / locator_path, locator_bytes)
        runtime = {
            "@id": "https://example.invalid/id/semantic-runtime/relationships",
            "schema": "okf-rich-relationship-runtime-manifest.v1",
            "snapshot": "fixture",
            "generated_at": "2026-08-10T00:00:00Z",
            "semantic_manifest": "okf-bundle.yamlld",
            "assertion_contract": "data/semantic/assertion.schema.json",
            "row_contract": "data/semantic/runtime-row.schema.json",
            "default_planes": ["core"],
            "route_locator": {
                "path": locator_path,
                "id": "https://example.invalid/id/semantic-runtime/locator",
                "routes": len(routes),
                "buckets": len(bucket_metadata),
                "sha256": sha256(locator_bytes),
            },
            "planes": [
                {
                    "id": plane_id,
                    "name": "core",
                    "active": True,
                    "lifecycle": "active",
                    "authority_classes": ["official"],
                    "assertions": 1,
                    "chunks": [chunk_reference],
                }
            ],
            "totals": {
                "active_assertions": 1,
                "historical_assertions": 0,
                "rejected_assertions": 0,
                "all_assertions": 1,
                "chunks": 1,
            },
            "loading_policy": "Load the active core plane by default.",
        }
        runtime_path = "data/semantic/runtime-manifest.json"
        runtime_bytes = json_bytes(runtime)
        write(bundle / runtime_path, runtime_bytes)
        runtime_reference = {
            "path": runtime_path,
            "bytes": len(runtime_bytes),
            "sha256": sha256(runtime_bytes),
        }
        row_retained_text = retained_text_units({**row, "lifecycle": "active"})
        maxima = {
            "row_retained_text_units": row_retained_text,
            "row_evidence_items": 1,
            "row_supporting_assertions": 0,
            "chunk_rows": 1,
            "chunk_compressed_bytes": len(chunk_bytes),
            "chunk_decoded_bytes": len(compact_json_bytes([row])),
            "chunk_retained_text_units": row_retained_text,
            "locator_bucket_compressed_bytes": max(bucket_compressed_sizes),
            "locator_bucket_decoded_bytes": max(bucket_decoded_sizes),
            "locator_manifest_bytes": len(locator_bytes),
            "runtime_manifest_bytes": len(runtime_bytes),
            "route_chunks": 1,
            "route_declared_rows": 1,
            "route_incident_rows": 1,
            "route_compressed_bytes": len(chunk_bytes),
            "route_retained_text_units": row_retained_text,
            "full_hydration_chunks": 1,
            "full_hydration_declared_rows": 1,
            "full_hydration_compressed_bytes": len(chunk_bytes),
            "full_hydration_retained_text_units": row_retained_text,
            "total_chunks": 1,
            "total_rows": 1,
            "total_planes": 1,
        }
        large_corpus_source = next(
            row
            for row in contract_sources
            if row["path"]
            == "apps/okf-explorer/src/lib/sources/largeCorpus.ts"
        )
        build_receipt = {
            "schema": "okf-hmlr-build-receipt.v1",
            "status": "ai-generated-proof-of-concept",
            "publication_state": "digest-bound-external-evidence",
            "release_at": None,
            "network_access": False,
            "record_count": 1,
            "semantic_assertion_validation": {
                "status": "conformant",
                "counts": {
                    "semantic_assertions_validated": 1,
                    "validation_failures": 0,
                },
                "rich_relationship_runtime": {
                    "status": "passed",
                    "rows": 1,
                    "chunks": 1,
                    "routes": len(routes),
                    "buckets": len(bucket_metadata),
                    "default_planes": ["core"],
                    "consumer_limits": {
                        "status": "passed",
                        "consumer": {
                            "version": "0.6.1",
                            "commit_sha": self.explorer_commit,
                            "large_corpus_source_sha256": (
                                large_corpus_source["sha256"]
                            ),
                        },
                        "limits": lock["limits"],
                        "maxima": maxima,
                        "cache_policy": {
                            "maximum_cached_chunks": 16,
                            "interpretation": (
                                "consumer eviction ceiling, not a producer "
                                "route-chunk ceiling"
                            ),
                        },
                    },
                },
            },
            "governed_inputs": [
                {
                    "path": "contracts/okf-explorer.consumer-lock.json",
                    "bytes": len(lock_path.read_bytes()),
                    "sha256": sha256(lock_path.read_bytes()),
                }
            ],
        }
        data_manifest_path = "data/explorer/manifest.json"
        data_manifest = {
            "schema": "okf-explorer-data-manifest.v1",
            "snapshot": "fixture",
            "chunks": {},
            "indexes": {
                "relationship_runtime": runtime_reference,
            },
        }
        data_manifest_bytes = json_bytes(data_manifest)
        write(bundle / data_manifest_path, data_manifest_bytes)
        data_manifest_reference = {
            "path": data_manifest_path,
            "bytes": len(data_manifest_bytes),
            "sha256": sha256(data_manifest_bytes),
        }
        descriptor = {
            "@id": "https://example.invalid/bundle/okf-explorer.json",
            "schema": "okf-explorer-large-corpus.v1",
            "kind": "okf-large-corpus",
            "version": "0.3.0",
            "snapshot": "fixture",
            "status": "ai-generated-proof-of-concept",
            "publication_state": "digest-bound-external-evidence",
            "release_at": None,
            "counts": {"records": 1, "relationships": 1},
            "entrypoints": {
                "data_manifest": data_manifest_path,
                "relationship_runtime": runtime_reference,
            },
            "entrypoint_integrity": {
                "data_manifest": data_manifest_reference,
                "relationship_runtime": runtime_reference,
            },
        }
        write(bundle / "build-receipt.json", json_bytes(build_receipt))
        write(bundle / "okf-explorer.json", json_bytes(descriptor))
        lines = []
        for artifact in sorted(path for path in bundle.rglob("*") if path.is_file()):
            data = artifact.read_bytes()
            lines.append(
                f"{sha256(data)}  {artifact.relative_to(bundle).as_posix()}"
            )
        manifest = "\n".join(lines) + "\n"
        release_root = sha256(manifest.encode())
        write(
            bundle / "CHECKSUMS.sha256",
            manifest + f"# release-root-sha256: {release_root}\n",
        )
        self.lock = lock
        self.build_receipt = build_receipt
        self.descriptor = descriptor
        self.data_manifest = data_manifest
        self.runtime = runtime
        self.row = row
        self.plane = runtime["planes"][0]
        return initialise_repository(self.candidate), release_root

    def rebind_runtime(self, runtime: dict[str, Any]) -> None:
        bundle = self.candidate / "bundle"
        runtime_bytes = json_bytes(runtime)
        write(bundle / "data/semantic/runtime-manifest.json", runtime_bytes)
        runtime_reference = {
            "path": "data/semantic/runtime-manifest.json",
            "bytes": len(runtime_bytes),
            "sha256": sha256(runtime_bytes),
        }
        data_manifest = json.loads(json.dumps(self.data_manifest))
        data_manifest["indexes"]["relationship_runtime"] = runtime_reference
        data_manifest_bytes = json_bytes(data_manifest)
        write(bundle / "data/explorer/manifest.json", data_manifest_bytes)
        data_manifest_reference = {
            "path": "data/explorer/manifest.json",
            "bytes": len(data_manifest_bytes),
            "sha256": sha256(data_manifest_bytes),
        }
        descriptor = json.loads(json.dumps(self.descriptor))
        descriptor["entrypoints"]["relationship_runtime"] = runtime_reference
        descriptor["entrypoint_integrity"][
            "relationship_runtime"
        ] = runtime_reference
        descriptor["entrypoint_integrity"][
            "data_manifest"
        ] = data_manifest_reference
        write(bundle / "okf-explorer.json", json_bytes(descriptor))
        self.runtime = runtime
        self.data_manifest = data_manifest
        self.descriptor = descriptor

    def reseal_candidate(self, message: str) -> None:
        bundle = self.candidate / "bundle"
        lines = []
        for artifact in sorted(path for path in bundle.rglob("*") if path.is_file()):
            if artifact.name == "CHECKSUMS.sha256":
                continue
            data = artifact.read_bytes()
            lines.append(
                f"{sha256(data)}  {artifact.relative_to(bundle).as_posix()}"
            )
        manifest = "\n".join(lines) + "\n"
        self.release_root = sha256(manifest.encode())
        write(
            bundle / "CHECKSUMS.sha256",
            manifest + f"# release-root-sha256: {self.release_root}\n",
        )
        git(self.candidate, "add", "bundle")
        git(
            self.candidate,
            "-c",
            "user.name=OKF runner test",
            "-c",
            "user.email=runner-test@example.invalid",
            "commit",
            "--quiet",
            "-m",
            message,
        )
        self.candidate_commit = git(self.candidate, "rev-parse", "HEAD")

    def command(self, *, output: Path | None = None) -> list[str]:
        return [
            "node",
            str(RUNNER),
            "--repository-root",
            str(self.candidate.resolve()),
            "--bundle-root",
            str((self.candidate / "bundle").resolve()),
            "--explorer-checkout",
            str(self.explorer.resolve()),
            "--candidate-commit",
            self.candidate_commit,
            "--release-root",
            self.release_root,
            "--output",
            str((output or self.output).resolve()),
            "--preflight-only",
        ]


def run_runtime_measurement(
    fixture: RunnerFixture,
    *,
    lock: dict[str, Any] | None = None,
    build_receipt: dict[str, Any] | None = None,
) -> subprocess.CompletedProcess[str]:
    supplied_lock = lock or fixture.lock
    supplied_receipt = build_receipt or fixture.build_receipt
    semantic_validation = supplied_receipt["semantic_assertion_validation"]
    script = f"""
      import {{
        measureRichRelationshipRuntime,
        resolveAdvertisedRichRelationshipRuntime,
        validateRichRuntimeBuildReceipt
      }} from {json.dumps(RUNNER.as_uri())};
      const lock = {json.dumps(supplied_lock)};
      const semanticValidation = {json.dumps(semantic_validation)};
      const contractSources = lock.consumer.contract_sources.map((row) => ({{
        ...row,
        bytes: 1
      }}));
      const validation = validateRichRuntimeBuildReceipt(
        lock,
        contractSources,
        semanticValidation,
        {{
          records: 1,
          relationships: semanticValidation.rich_relationship_runtime.rows
        }}
      );
      const advertised = await resolveAdvertisedRichRelationshipRuntime(
        {json.dumps(str((fixture.candidate / 'bundle').resolve()))},
        {json.dumps(fixture.descriptor)}
      );
      const observed = await measureRichRelationshipRuntime(
        {json.dumps(str((fixture.candidate / 'bundle').resolve()))},
        lock,
        validation,
        advertised
      );
      process.stdout.write(JSON.stringify(observed));
    """
    return subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        check=False,
        capture_output=True,
        text=True,
    )


def run_row_projection(
    row: dict[str, Any],
    plane: dict[str, Any],
) -> subprocess.CompletedProcess[str]:
    script = f"""
      import {{ projectRichRelationshipRow }} from {json.dumps(RUNNER.as_uri())};
      const projected = projectRichRelationshipRow(
        {json.dumps(row)},
        {json.dumps(plane)},
        "adversarial fixture row"
      );
      process.stdout.write(JSON.stringify(projected));
    """
    return subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        check=False,
        capture_output=True,
        text=True,
    )


def run_safe_relative_resource_path(value: str) -> subprocess.CompletedProcess[str]:
    script = f"""
      import {{ safeRelativeResourcePath }} from {json.dumps(RUNNER.as_uri())};
      process.stdout.write(safeRelativeResourcePath(
        {json.dumps(value)},
        "adversarial runtime resource path"
      ));
    """
    return subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        check=False,
        capture_output=True,
        text=True,
    )


class AuthoredSiteBrowserQualityRunnerTests(unittest.TestCase):
    def test_help_keeps_claims_and_explorer_receipts_separate(self) -> None:
        result = subprocess.run(
            ["node", str(RUNNER), "--help"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("Explorer product and search", result.stdout)
        self.assertIn("never a Land Registry G6 decision", result.stdout)
        self.assertIn("WCAG", result.stdout)

    def test_exact_preflight_records_bound_identity_without_g6_claim(self) -> None:
        with tempfile.TemporaryDirectory(prefix="okf-g6-runner-") as temporary:
            fixture = RunnerFixture(Path(temporary))
            result = subprocess.run(
                fixture.command(),
                check=True,
                capture_output=True,
                text=True,
            )
            evidence = json.loads(fixture.output.read_text())
            self.assertEqual(
                "okf-hmlr-authored-site-browser-quality-preflight.v1",
                evidence["schema"],
            )
            self.assertEqual(
                fixture.candidate_commit,
                evidence["candidate"]["candidate_commit_sha"],
            )
            self.assertEqual(
                fixture.release_root,
                evidence["candidate"]["release_root_sha256"],
            )
            self.assertFalse(evidence["candidate"]["source_dirty"])
            self.assertFalse(evidence["consumer"]["source_dirty"])
            self.assertEqual("v0.6.1", evidence["consumer"]["release_tag"])
            self.assertEqual(
                6,
                len(evidence["consumer"]["acceptance_executable_materials"]),
            )
            self.assertEqual(
                "okf-predicate-registry.v2",
                evidence["consumer"]["predicate_registry"]
                ["required_projection_schema"],
            )
            self.assertEqual(
                "0.6.0",
                evidence["consumer"]["semantic_profile"]
                ["source_release"]["version"],
            )
            self.assertEqual(
                "1.62.1", evidence["toolchain"]["playwright"]["version"]
            )
            self.assertEqual(
                "4.12.1",
                evidence["toolchain"]["axe_core_playwright"]["version"],
            )
            self.assertEqual(
                "not-made-by-runner",
                evidence["claim_boundary"]["land_registry_g6_decision"],
            )
            self.assertEqual(
                "not-run", evidence["terminal"]["browser_observations"]
            )
            self.assertEqual(
                "digest-bound-external-evidence",
                evidence["candidate"]["descriptor"]["publication_state"],
            )
            self.assertFalse(
                evidence["candidate"]["build_receipt"]["network_access"]
            )
            self.assertEqual(
                1,
                evidence["candidate"]["build_receipt"]
                ["semantic_assertions_validated"],
            )
            runtime = evidence["candidate"]["build_receipt"][
                "rich_relationship_runtime"
            ]
            self.assertEqual("passed", runtime["status"])
            self.assertEqual(
                fixture.explorer_commit,
                runtime["consumer"]["commit_sha"],
            )
            self.assertIn(
                "maximum_rich_relationship_decoded_chunk_bytes",
                runtime["limits"],
            )
            self.assertIn(
                "full_hydration_retained_text_units", runtime["maxima"]
            )
            self.assertNotIn('"pass"', fixture.output.read_text())
            self.assertIn(str(fixture.output.resolve()), result.stdout)

    def test_preflight_rejects_advertised_resource_binding_bypasses(self) -> None:
        descriptor_mutations = {
            "data-manifest entrypoint": lambda descriptor: descriptor[
                "entrypoints"
            ].update({"data_manifest": "data/explorer/other.json"}),
            "runtime digest": lambda descriptor: descriptor["entrypoints"][
                "relationship_runtime"
            ].update({"sha256": "f" * 64}),
        }
        for label, mutate in descriptor_mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory(
                prefix="okf-g6-runner-"
            ) as temporary:
                fixture = RunnerFixture(Path(temporary))
                descriptor = json.loads(json.dumps(fixture.descriptor))
                mutate(descriptor)
                write(
                    fixture.candidate / "bundle" / "okf-explorer.json",
                    json_bytes(descriptor),
                )
                fixture.reseal_candidate(f"alter {label}")
                result = subprocess.run(
                    fixture.command(),
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(1, result.returncode)
                self.assertIn(
                    "runner-failed-closed-before-evidence",
                    result.stderr,
                )

        with tempfile.TemporaryDirectory(prefix="okf-g6-runner-") as temporary:
            fixture = RunnerFixture(Path(temporary))
            bundle = fixture.candidate / "bundle"
            descriptor = json.loads(json.dumps(fixture.descriptor))
            data_manifest = json.loads(json.dumps(fixture.data_manifest))
            data_manifest["indexes"]["relationship_runtime"]["bytes"] += 1
            data_manifest_bytes = json_bytes(data_manifest)
            write(bundle / "data/explorer/manifest.json", data_manifest_bytes)
            descriptor["entrypoint_integrity"]["data_manifest"] = {
                "path": "data/explorer/manifest.json",
                "bytes": len(data_manifest_bytes),
                "sha256": sha256(data_manifest_bytes),
            }
            write(bundle / "okf-explorer.json", json_bytes(descriptor))
            fixture.reseal_candidate("alter runtime projection binding")
            result = subprocess.run(
                fixture.command(),
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(1, result.returncode)
            self.assertIn("descriptor and data-manifest", result.stderr)

        with tempfile.TemporaryDirectory(prefix="okf-g6-runner-") as temporary:
            fixture = RunnerFixture(Path(temporary))
            bundle = fixture.candidate / "bundle"
            descriptor = json.loads(json.dumps(fixture.descriptor))
            data_manifest = json.loads(json.dumps(fixture.data_manifest))
            runtime = json.loads(json.dumps(fixture.runtime))
            runtime["snapshot"] = "different-snapshot"
            runtime_bytes = json_bytes(runtime)
            write(bundle / "data/semantic/runtime-manifest.json", runtime_bytes)
            runtime_reference = {
                "path": "data/semantic/runtime-manifest.json",
                "bytes": len(runtime_bytes),
                "sha256": sha256(runtime_bytes),
            }
            descriptor["entrypoints"]["relationship_runtime"] = runtime_reference
            descriptor["entrypoint_integrity"][
                "relationship_runtime"
            ] = runtime_reference
            data_manifest["indexes"]["relationship_runtime"] = runtime_reference
            data_manifest_bytes = json_bytes(data_manifest)
            write(bundle / "data/explorer/manifest.json", data_manifest_bytes)
            descriptor["entrypoint_integrity"]["data_manifest"] = {
                "path": "data/explorer/manifest.json",
                "bytes": len(data_manifest_bytes),
                "sha256": sha256(data_manifest_bytes),
            }
            write(bundle / "okf-explorer.json", json_bytes(descriptor))
            fixture.reseal_candidate("alter reconciled runtime snapshot")
            result = subprocess.run(
                fixture.command(),
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(1, result.returncode)
            self.assertIn("snapshots differ", result.stderr)

    def test_runtime_row_normaliser_rejects_explorer_contract_bypasses(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="okf-g6-runner-") as temporary:
            fixture = RunnerFixture(Path(temporary))
            passing = run_row_projection(fixture.row, fixture.plane)
            self.assertEqual(0, passing.returncode, passing.stderr)

            reviewed = json.loads(json.dumps(fixture.row))
            reviewed["review_status"] = "independently-reviewed"
            reviewed_result = run_row_projection(reviewed, fixture.plane)
            self.assertEqual(0, reviewed_result.returncode, reviewed_result.stderr)
            self.assertEqual(
                json.loads(passing.stdout)["retained_text_units"]
                + len("independently-reviewed"),
                json.loads(reviewed_result.stdout)["retained_text_units"],
            )

            cases: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}

            row = json.loads(json.dumps(fixture.row))
            row["review_status"] = " reviewed"
            cases["unsafe optional review status"] = (row, fixture.plane)

            row = json.loads(json.dumps(fixture.row))
            row["id"] += "/café"
            row["assertion_id"] = row["id"]
            cases["non-ASCII assertion id"] = (row, fixture.plane)

            row = json.loads(json.dumps(fixture.row))
            row["source"] = "Dataset/fixture"
            row["source_route"] = row["source"]
            cases["unsafe route"] = (row, fixture.plane)

            row = json.loads(json.dumps(fixture.row))
            row["evidence"][0]["url"] = "https://user:pass@example.invalid/"
            cases["credential URL"] = (row, fixture.plane)

            row = json.loads(json.dumps(fixture.row))
            row["evidence"].append(json.loads(json.dumps(row["evidence"][0])))
            cases["duplicate evidence"] = (row, fixture.plane)

            row = json.loads(json.dumps(fixture.row))
            plane = json.loads(json.dumps(fixture.plane))
            plane["authority_classes"].append("derived")
            row["authority"]["class"] = "derived"
            cases["authority coherence"] = (row, plane)

            row = json.loads(json.dumps(fixture.row))
            plane = json.loads(json.dumps(fixture.plane))
            plane["authority_classes"].append("derived")
            row.update(
                {
                    "assertion_status": "inferred",
                    "authority": {
                        **row["authority"],
                        "class": "derived",
                    },
                    "rule": "https://example.invalid/id/rule/inference",
                    "derivation_activity": (
                        "https://example.invalid/id/activity/inference"
                    ),
                    "confidence_score": 0.5,
                    "supporting_assertions": [
                        "https://example.invalid/id/assertion/support",
                        "https://example.invalid/id/assertion/support",
                    ],
                }
            )
            cases["duplicate support"] = (row, plane)

            row = json.loads(json.dumps(fixture.row))
            plane = json.loads(json.dumps(fixture.plane))
            plane["authority_classes"].append("model-assisted")
            row.update(
                {
                    "assertion_status": "model-derived",
                    "authority": {
                        **row["authority"],
                        "class": "model-assisted",
                    },
                    "derivation_activity": (
                        "https://example.invalid/id/activity/model"
                    ),
                    "confidence_score": 1.1,
                    "review_status": "reviewed",
                }
            )
            cases["confidence range"] = (row, plane)

            row = json.loads(json.dumps(fixture.row))
            plane = json.loads(json.dumps(fixture.plane))
            plane["authority_classes"].append("model-assisted")
            row.update(
                {
                    "assertion_status": "model-derived",
                    "authority": {
                        **row["authority"],
                        "class": "model-assisted",
                    },
                    "derivation_activity": (
                        "https://example.invalid/id/activity/model"
                    ),
                    "confidence_score": 0.5,
                }
            )
            cases["missing model review status"] = (row, plane)

            for label, (row, plane) in cases.items():
                with self.subTest(label=label):
                    result = run_row_projection(row, plane)
                    self.assertNotEqual(0, result.returncode)

    def test_runtime_resource_paths_match_explorer_rejections(self) -> None:
        passing = run_safe_relative_resource_path(
            "data/semantic/runtime/core/relationships-000.json.gz"
        )
        self.assertEqual(0, passing.returncode, passing.stderr)
        unsafe = (
            " data/runtime.json",
            "data/runtime.json ",
            "data/runtime.json?download=1",
            "data/runtime.json#fragment",
            "C:relative",
            "data/%2e%2e/runtime.json",
            "data/%2e/runtime.json",
            "data/runtime%2fmanifest.json",
            "data/runtime%5cmanifest.json",
            "data/runtime%00manifest.json",
            "data/runtime%zzmanifest.json",
        )
        for value in unsafe:
            with self.subTest(value=value):
                rejected = run_safe_relative_resource_path(value)
                self.assertNotEqual(0, rejected.returncode)
                self.assertIn("is unsafe", rejected.stderr)

    def test_runtime_measurement_rejects_self_consistent_missing_endpoint(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="okf-g6-runner-") as temporary:
            fixture = RunnerFixture(Path(temporary))
            bundle = fixture.candidate / "bundle"
            locator_path = (
                bundle / "data/semantic/runtime/route-locator/manifest.json"
            )
            locator = json.loads(locator_path.read_text())
            omitted_prefix = sha256(b"source/fixture")[:2]
            locator["buckets"] = [
                bucket
                for bucket in locator["buckets"]
                if bucket["bucket"] != omitted_prefix
            ]
            locator["counts"] = {
                "routes": 1,
                "buckets": 1,
                "chunk_references": 1,
            }
            locator_bytes = json_bytes(locator)
            write(locator_path, locator_bytes)
            runtime = json.loads(json.dumps(fixture.runtime))
            runtime["route_locator"].update(
                {
                    "routes": 1,
                    "buckets": 1,
                    "sha256": sha256(locator_bytes),
                }
            )
            fixture.rebind_runtime(runtime)
            receipt = json.loads(json.dumps(fixture.build_receipt))
            receipt_runtime = receipt["semantic_assertion_validation"][
                "rich_relationship_runtime"
            ]
            receipt_runtime["routes"] = 1
            receipt_runtime["buckets"] = 1
            result = run_runtime_measurement(fixture, build_receipt=receipt)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("route total differs", result.stderr)

    def test_runtime_locator_route_and_bucket_counts_are_positive(self) -> None:
        for field in ("routes", "buckets"):
            with self.subTest(source="runtime", field=field), tempfile.TemporaryDirectory(
                prefix="okf-g6-runner-"
            ) as temporary:
                fixture = RunnerFixture(Path(temporary))
                runtime = json.loads(json.dumps(fixture.runtime))
                runtime["route_locator"][field] = 0
                fixture.rebind_runtime(runtime)
                result = run_runtime_measurement(fixture)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("positive safe integer", result.stderr)

            with self.subTest(source="receipt", field=field), tempfile.TemporaryDirectory(
                prefix="okf-g6-runner-"
            ) as temporary:
                fixture = RunnerFixture(Path(temporary))
                receipt = json.loads(json.dumps(fixture.build_receipt))
                receipt["semantic_assertion_validation"][
                    "rich_relationship_runtime"
                ][field] = 0
                result = run_runtime_measurement(fixture, build_receipt=receipt)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("positive safe integer", result.stderr)

    def test_runtime_receipt_counts_all_inactive_planes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="okf-g6-runner-") as temporary:
            fixture = RunnerFixture(Path(temporary))
            runtime = json.loads(json.dumps(fixture.runtime))
            runtime["planes"].append(
                {
                    "id": "https://example.invalid/id/semantic-plane/historical",
                    "name": "historical",
                    "active": False,
                    "lifecycle": "historical",
                    "authority_classes": ["official"],
                    "assertions": 0,
                    "chunks": [],
                }
            )
            fixture.rebind_runtime(runtime)
            receipt = json.loads(json.dumps(fixture.build_receipt))
            maxima = receipt["semantic_assertion_validation"][
                "rich_relationship_runtime"
            ]["consumer_limits"]["maxima"]
            maxima["total_planes"] = 2
            maxima["runtime_manifest_bytes"] = (
                fixture.candidate
                / "bundle/data/semantic/runtime-manifest.json"
            ).stat().st_size
            result = run_runtime_measurement(fixture, build_receipt=receipt)
            self.assertEqual(0, result.returncode, result.stderr)
            observed = json.loads(result.stdout)
            self.assertEqual([], observed["findings"])
            self.assertEqual(
                2,
                observed["measurement"]["observed_maxima"]["total_planes"],
            )

    def test_runtime_measurement_rejects_locator_and_global_identity_bypasses(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="okf-g6-runner-") as temporary:
            fixture = RunnerFixture(Path(temporary))
            bundle = fixture.candidate / "bundle"
            locator_path = bundle / "data/semantic/runtime/route-locator/manifest.json"
            locator = json.loads(locator_path.read_text())
            locator["counts"]["routes"] += 1
            locator_bytes = json_bytes(locator)
            write(locator_path, locator_bytes)
            runtime = json.loads(json.dumps(fixture.runtime))
            runtime["route_locator"]["sha256"] = sha256(locator_bytes)
            fixture.rebind_runtime(runtime)
            result = run_runtime_measurement(fixture)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("counts differ", result.stderr)

        with tempfile.TemporaryDirectory(prefix="okf-g6-runner-") as temporary:
            fixture = RunnerFixture(Path(temporary))
            bundle = fixture.candidate / "bundle"
            runtime = json.loads(json.dumps(fixture.runtime))
            original = runtime["planes"][0]["chunks"][0]
            duplicate = json.loads(json.dumps(original))
            duplicate["id"] += "-duplicate"
            duplicate["path"] = (
                "data/semantic/runtime/core/relationships-001.json.gz"
            )
            write(bundle / duplicate["path"], (bundle / original["path"]).read_bytes())
            runtime["planes"][0]["chunks"].append(duplicate)
            runtime["planes"][0]["assertions"] = 2
            runtime["totals"]["active_assertions"] = 2
            runtime["totals"]["all_assertions"] = 2
            runtime["totals"]["chunks"] = 2
            fixture.rebind_runtime(runtime)
            receipt = json.loads(json.dumps(fixture.build_receipt))
            receipt_runtime = receipt["semantic_assertion_validation"][
                "rich_relationship_runtime"
            ]
            receipt_runtime["rows"] = 2
            receipt_runtime["chunks"] = 2
            maxima = receipt_runtime["consumer_limits"]["maxima"]
            maxima["total_rows"] = 2
            maxima["total_chunks"] = 2
            result = run_runtime_measurement(fixture, build_receipt=receipt)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("across chunks", result.stderr)

    def test_preflight_rejects_runtime_receipt_identity_and_maximum_bypasses(
        self,
    ) -> None:
        mutations = {
            "source identity": lambda receipt: receipt[
                "semantic_assertion_validation"
            ]["rich_relationship_runtime"]["consumer_limits"]["consumer"].update(
                {"large_corpus_source_sha256": "f" * 64}
            ),
            "full hydration maximum": lambda receipt: receipt[
                "semantic_assertion_validation"
            ]["rich_relationship_runtime"]["consumer_limits"]["maxima"].pop(
                "full_hydration_retained_text_units"
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory(
                prefix="okf-g6-runner-"
            ) as temporary:
                fixture = RunnerFixture(Path(temporary))
                receipt = json.loads(json.dumps(fixture.build_receipt))
                mutate(receipt)
                write(
                    fixture.candidate / "bundle" / "build-receipt.json",
                    json_bytes(receipt),
                )
                fixture.reseal_candidate(f"alter {label}")
                result = subprocess.run(
                    fixture.command(),
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(1, result.returncode)
                self.assertFalse(fixture.output.exists())
                self.assertEqual(
                    "runner-failed-closed-before-evidence",
                    json.loads(result.stderr)["outcome"],
                )

    def test_runtime_measurement_separates_wire_and_decoded_byte_limits(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="okf-g6-runner-") as temporary:
            fixture = RunnerFixture(Path(temporary))
            maxima = fixture.build_receipt["semantic_assertion_validation"][
                "rich_relationship_runtime"
            ]["consumer_limits"]["maxima"]
            compressed = maxima["chunk_compressed_bytes"]
            decoded = maxima["chunk_decoded_bytes"]
            self.assertLess(compressed, decoded)
            result = run_runtime_measurement(fixture)
            self.assertEqual(0, result.returncode, result.stderr)
            observed = json.loads(result.stdout)
            self.assertEqual([], observed["findings"])
            self.assertEqual(
                compressed,
                observed["measurement"]["observed_maxima"]
                ["chunk_compressed_bytes"],
            )
            self.assertEqual(
                decoded,
                observed["measurement"]["observed_maxima"]
                ["chunk_decoded_bytes"],
            )

    def test_runtime_measurement_blocks_compressed_decoded_and_text_bypasses(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="okf-g6-runner-") as temporary:
            fixture = RunnerFixture(Path(temporary))
            for limit_name in (
                "maximum_rich_relationship_chunk_bytes",
                "maximum_json_bytes",
                "maximum_rich_relationship_retained_text_units",
            ):
                with self.subTest(limit_name=limit_name):
                    altered_lock = json.loads(json.dumps(fixture.lock))
                    altered_receipt = json.loads(json.dumps(fixture.build_receipt))
                    altered_lock["limits"][limit_name] -= 1
                    altered_receipt["semantic_assertion_validation"][
                        "rich_relationship_runtime"
                    ]["consumer_limits"]["limits"] = altered_lock["limits"]
                    result = run_runtime_measurement(
                        fixture,
                        lock=altered_lock,
                        build_receipt=altered_receipt,
                    )
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn("executable Explorer v0.6.1", result.stderr)

    def test_preflight_rejects_non_candidate_publication_posture(self) -> None:
        with tempfile.TemporaryDirectory(prefix="okf-g6-runner-") as temporary:
            fixture = RunnerFixture(Path(temporary))
            descriptor_path = fixture.candidate / "bundle" / "okf-explorer.json"
            descriptor = json.loads(descriptor_path.read_text())
            descriptor["publication_state"] = "published"
            write(descriptor_path, json_bytes(descriptor))
            fixture.reseal_candidate("alter publication posture")
            result = subprocess.run(
                fixture.command(),
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(1, result.returncode)
            self.assertFalse(fixture.output.exists())
            failure = json.loads(result.stderr)
            self.assertEqual(
                "runner-failed-closed-before-evidence", failure["outcome"]
            )
            self.assertIn("publication_state", failure["error"]["summary"])

    def test_preflight_fails_closed_for_dirty_explorer(self) -> None:
        with tempfile.TemporaryDirectory(prefix="okf-g6-runner-") as temporary:
            fixture = RunnerFixture(Path(temporary))
            package = fixture.explorer / "apps" / "okf-explorer" / "package.json"
            package.write_text(package.read_text() + "\n")
            result = subprocess.run(
                fixture.command(),
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(1, result.returncode)
            self.assertFalse(fixture.output.exists())
            failure = json.loads(result.stderr)
            self.assertEqual(
                "runner-failed-closed-before-evidence", failure["outcome"]
            )

    def test_preflight_fails_closed_for_tampered_checksummed_bytes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="okf-g6-runner-") as temporary:
            fixture = RunnerFixture(Path(temporary))
            descriptor = fixture.candidate / "bundle" / "okf-explorer.json"
            descriptor.write_text(descriptor.read_text().replace("fixture", "tampered"))
            git(fixture.candidate, "add", "bundle/okf-explorer.json")
            git(
                fixture.candidate,
                "-c",
                "user.name=OKF runner test",
                "-c",
                "user.email=runner-test@example.invalid",
                "commit",
                "--quiet",
                "-m",
                "tamper fixture",
            )
            fixture.candidate_commit = git(
                fixture.candidate, "rev-parse", "HEAD"
            )
            result = subprocess.run(
                fixture.command(),
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(1, result.returncode)
            self.assertFalse(fixture.output.exists())
            self.assertEqual(
                "runner-failed-closed-before-evidence",
                json.loads(result.stderr)["outcome"],
            )

    def test_credential_detection_handles_camel_case_without_false_positive(self) -> None:
        script = f"""
          import {{ credentialUrlFinding }} from {json.dumps(RUNNER.as_uri())};
          const result = [
            credentialUrlFinding('https://example.invalid/data?accessToken=secret'),
            credentialUrlFinding('https://example.invalid/data?signature_method=rsa'),
            credentialUrlFinding('https://user@example.invalid/data')
          ];
          process.stdout.write(JSON.stringify(result));
        """
        result = subprocess.run(
            ["node", "--input-type=module", "--eval", script],
            check=True,
            capture_output=True,
            text=True,
        )
        observed = json.loads(result.stdout)
        self.assertEqual("sensitive-query-key:accesstoken", observed[0])
        self.assertIsNone(observed[1])
        self.assertEqual("embedded-user-information", observed[2])

    def test_csp_parser_is_quote_aware_and_gzip_is_output_bounded(self) -> None:
        script = f"""
          import {{ gzipSync }} from 'node:zlib';
          import {{
            axeNode,
            boundedGunzip,
            cspBaseline,
            cspDirectives,
            htmlAttributes
          }} from {json.dumps(RUNNER.as_uri())};
          const tag = `<meta http-equiv="Content-Security-Policy" content="default-src 'self'; object-src 'none'; base-uri 'none'">`;
          const attributes = htmlAttributes(tag);
          const complete = cspBaseline(cspDirectives(attributes.content));
          const catalogue = cspBaseline(cspDirectives("default-src 'self'; style-src 'self'; base-uri 'none'"));
          let bounded = false;
          try {{
            boundedGunzip(gzipSync(Buffer.alloc(4096)), 1024, 'test member');
          }} catch (error) {{
            bounded = error.message.includes('exceeds 1024');
          }}
          let duplicateRejected = false;
          try {{
            htmlAttributes(`<meta content="first" CONTENT="second">`);
          }} catch (error) {{
            duplicateRejected = error.message.includes('repeats attribute content');
          }}
          const minimised = axeNode({{
            impact: 'serious',
            html: '<input value="secret-value">',
            target: ['#field'],
            failureSummary: 'secret failure detail',
            any: [{{
              id: 'fixture-check',
              impact: 'serious',
              message: 'secret check detail',
              data: {{ value: 'secret data' }},
              relatedNodes: [{{ target: ['#related'], html: '<span>secret related detail</span>' }}]
            }}],
            all: [],
            none: []
          }});
          process.stdout.write(JSON.stringify({{
            attributes,
            complete,
            catalogue,
            bounded,
            duplicateRejected,
            minimised
          }}));
        """
        result = subprocess.run(
            ["node", "--input-type=module", "--eval", script],
            check=True,
            capture_output=True,
            text=True,
        )
        observed = json.loads(result.stdout)
        self.assertEqual(
            "default-src 'self'; object-src 'none'; base-uri 'none'",
            observed["attributes"]["content"],
        )
        self.assertTrue(all(observed["complete"].values()))
        self.assertFalse(observed["catalogue"]["object_src_none"])
        self.assertTrue(observed["bounded"])
        self.assertTrue(observed["duplicateRejected"])
        self.assertNotIn("secret", json.dumps(observed["minimised"]))
        self.assertIn("html_evidence", observed["minimised"])



if __name__ == "__main__":
    unittest.main()
