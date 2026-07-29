#!/usr/bin/env python3
"""Create SPDX and provenance metadata for an exact packaged candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.check_release_evidence import (
    CandidateIdentity,
    ReleaseEvidenceError,
    candidate_identity_from_repository,
    sha256_file,
)


ROOT = Path(__file__).resolve().parents[1]
COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
LOCKED_REQUIREMENT = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s\\]+)")
ACTION_PIN = re.compile(
    r"^\s*uses:\s*([^@\s]+)@([0-9a-f]{40})(?:\s+#\s*(.*))?$",
    re.MULTILINE,
)


def canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"


def locked_packages(path: Path) -> list[tuple[str, str]]:
    packages: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        match = LOCKED_REQUIREMENT.match(line)
        if match is None:
            continue
        name, version = match.groups()
        normalized = name.replace("_", "-").casefold()
        if normalized in seen:
            raise ReleaseEvidenceError(
                f"duplicate locked package name: {name!r}"
            )
        seen.add(normalized)
        packages.append((name, version))
    if not packages:
        raise ReleaseEvidenceError("dependency lock contains no packages")
    return sorted(packages, key=lambda row: row[0].casefold())


def action_pins(path: Path) -> list[dict[str, str]]:
    text = path.read_text(encoding="utf-8")
    pins = [
        {
            "action": action,
            "commit": commit,
            "version_comment": comment.strip() if comment else "",
        }
        for action, commit, comment in ACTION_PIN.findall(text)
    ]
    if not pins:
        raise ReleaseEvidenceError("workflow contains no SHA-pinned actions")
    return pins


def checked_candidate(commit: str) -> CandidateIdentity:
    if COMMIT_SHA.fullmatch(commit) is None:
        raise ReleaseEvidenceError(
            "candidate commit must be 40 lowercase hexadecimal characters"
        )
    result = subprocess.run(
        ["git", "rev-parse", "--verify", f"{commit}^{{commit}}"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or result.stdout.strip() != commit:
        raise ReleaseEvidenceError("candidate commit does not exist")
    return candidate_identity_from_repository(
        ROOT,
        checksums_path=Path("bundle/CHECKSUMS.sha256"),
        profile_checksums_path=Path("domain-profile/CHECKSUMS.sha256"),
        build_receipt_path=Path("bundle/build-receipt.json"),
        candidate_commit_sha=commit,
    )


def safe_output_directory(value: Path) -> Path:
    output = value.resolve()
    if ROOT not in output.parents or output.is_symlink():
        raise ReleaseEvidenceError(
            "metadata output must be a non-symlinked directory inside the repository"
        )
    current = ROOT
    for part in output.relative_to(ROOT).parts:
        current = current / part
        if current.is_symlink():
            raise ReleaseEvidenceError(
                "metadata output path contains a symbolic link"
            )
    output.mkdir(parents=True, exist_ok=True)
    return output


def create_metadata(
    *,
    candidate_commit: str,
    archive_receipt_path: Path,
    output_directory: Path,
) -> tuple[Path, Path]:
    candidate = checked_candidate(candidate_commit)
    config = json.loads(
        (ROOT / "source" / "build-config.json").read_text(encoding="utf-8")
    )
    if (
        config.get("status") != "ai-generated-proof-of-concept"
        or config.get("ai_generated_proof_of_concept") is not True
        or not isinstance(config.get("release_at"), str)
    ):
        raise ReleaseEvidenceError("build config is not a released AI PoC")

    archive_receipt = json.loads(
        archive_receipt_path.read_text(encoding="utf-8")
    )
    if (
        archive_receipt.get("schema") != "okf-hmlr-release-archive.v1"
        or archive_receipt.get("release_root_sha256")
        != candidate.release_root_sha256
    ):
        raise ReleaseEvidenceError(
            "archive receipt does not bind the exact release root"
        )
    archive_file = ROOT / str(archive_receipt.get("path", ""))
    if not archive_file.is_file() or sha256_file(archive_file) != archive_receipt.get(
        "sha256"
    ):
        raise ReleaseEvidenceError("release archive does not rehash")

    requirements_lock = ROOT / "requirements-lock.txt"
    packages = locked_packages(requirements_lock)
    workflow = ROOT / ".github" / "workflows" / "pages.yml"
    pins = action_pins(workflow)
    output = safe_output_directory(output_directory)
    created = config["release_at"]
    version = config["version"]

    project_spdx = "SPDXRef-Package-okf-landregistry"
    dependency_rows = []
    relationships = [
        {
            "spdxElementId": "SPDXRef-DOCUMENT",
            "relationshipType": "DESCRIBES",
            "relatedSpdxElement": project_spdx,
        }
    ]
    for name, package_version in packages:
        package_id = "SPDXRef-Package-" + re.sub(
            r"[^A-Za-z0-9.-]", "-", name
        )
        dependency_rows.append(
            {
                "SPDXID": package_id,
                "name": name,
                "versionInfo": package_version,
                "downloadLocation": (
                    f"https://pypi.org/project/{name}/{package_version}/"
                ),
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": "NOASSERTION",
                "copyrightText": "NOASSERTION",
                "externalRefs": [
                    {
                        "referenceCategory": "PACKAGE-MANAGER",
                        "referenceType": "purl",
                        "referenceLocator": (
                            f"pkg:pypi/{name.casefold()}@{package_version}"
                        ),
                    }
                ],
            }
        )
        relationships.append(
            {
                "spdxElementId": project_spdx,
                "relationshipType": "DEPENDS_ON",
                "relatedSpdxElement": package_id,
            }
        )

    sbom = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"okf-landregistry-{version}",
        "documentNamespace": (
            "https://chris-page-gov.github.io/okf-LandRegistry/"
            f"spdx/{candidate.release_root_sha256}"
        ),
        "creationInfo": {
            "created": created,
            "creators": ["Tool: scripts/create_release_metadata.py"],
            "licenseListVersion": "3.27",
        },
        "documentDescribes": [project_spdx],
        "packages": [
            {
                "SPDXID": project_spdx,
                "name": "okf-LandRegistry",
                "versionInfo": version,
                "downloadLocation": (
                    "https://github.com/chris-page-gov/okf-LandRegistry"
                ),
                "filesAnalyzed": False,
                "licenseConcluded": "NOASSERTION",
                "licenseDeclared": "NOASSERTION",
                "copyrightText": "Copyright 2026 Chris Page",
                "comment": (
                    "Mixed repository licensing is declared in LICENSE.md; "
                    "source metadata and linked material retain their own terms."
                ),
            },
            *dependency_rows,
        ],
        "relationships": relationships,
        "annotations": [
            {
                "annotationDate": created,
                "annotationType": "OTHER",
                "annotator": "Tool: scripts/create_release_metadata.py",
                "comment": (
                    "Dependency versions and permitted distribution hashes are "
                    f"bound by requirements-lock.txt SHA-256 {sha256_file(requirements_lock)}."
                ),
            }
        ],
    }

    build_receipt = json.loads(
        (ROOT / "bundle" / "build-receipt.json").read_text(encoding="utf-8")
    )
    provenance = {
        "schema": "okf-hmlr-release-provenance.v1",
        "version": version,
        "generated_at": created,
        "candidate": {
            "candidate_commit_sha": candidate.candidate_commit_sha,
            "release_root_sha256": candidate.release_root_sha256,
            "checksums_sha256": candidate.checksums_sha256,
            "profile_pack_root_sha256": candidate.profile_pack_root_sha256,
            "snapshot_manifest_sha256": candidate.snapshot_manifest_sha256,
        },
        "subject": {
            "bundle_checksums": {
                "path": "bundle/CHECKSUMS.sha256",
                "sha256": candidate.checksums_sha256,
            },
            "archive": archive_receipt,
        },
        "build": {
            "type": "offline-deterministic-static-bundle",
            "builder": {
                "path": "scripts/build.py",
                "sha256": sha256_file(ROOT / "scripts" / "build.py"),
            },
            "invocation": [
                "python",
                "scripts/build.py",
                "--replace",
            ],
            "python_target": "3.13",
            "network_access_during_build": False,
            "source_acquisition": (
                "Previously frozen bounded public-metadata snapshot; no live "
                "source access during the candidate build."
            ),
            "build_receipt_schema": build_receipt.get("schema"),
        },
        "materials": [
            {
                "path": "domain-profile/CHECKSUMS.sha256",
                "sha256": sha256_file(
                    ROOT / "domain-profile" / "CHECKSUMS.sha256"
                ),
            },
            {
                "path": build_receipt["snapshot"]["manifest_path"],
                "sha256": candidate.snapshot_manifest_sha256,
            },
            {
                "path": "requirements-lock.txt",
                "sha256": sha256_file(requirements_lock),
            },
            {
                "path": ".github/workflows/pages.yml",
                "sha256": sha256_file(workflow),
            },
            {
                "path": "pages/search-contract.json",
                "sha256": sha256_file(
                    ROOT / "pages" / "search-contract.json"
                ),
            },
            {
                "path": "evaluation/questions.json",
                "sha256": sha256_file(
                    ROOT / "evaluation" / "questions.json"
                ),
            },
        ],
        "workflow_actions": pins,
        "dependency_lock": {
            "path": "requirements-lock.txt",
            "sha256": sha256_file(requirements_lock),
            "package_count": len(packages),
            "hash_enforced_install": (
                "python -m pip install --require-hashes -r requirements-lock.txt"
            ),
        },
        "ai_disclosure": {
            "ai_generated_proof_of_concept": True,
            "human_accessibility_audit_completed": False,
        },
    }

    sbom_path = output / "sbom.spdx.json"
    provenance_path = output / "provenance.json"
    sbom_path.write_text(canonical_json(sbom), encoding="utf-8")
    provenance_path.write_text(canonical_json(provenance), encoding="utf-8")
    return sbom_path, provenance_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-commit-sha", required=True)
    parser.add_argument(
        "--archive-receipt",
        type=Path,
        default=Path("validation/evidence/release-archive.json"),
    )
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=Path("validation/evidence"),
    )
    args = parser.parse_args()
    try:
        sbom, provenance = create_metadata(
            candidate_commit=args.candidate_commit_sha,
            archive_receipt_path=(ROOT / args.archive_receipt).resolve(),
            output_directory=(ROOT / args.output_directory).resolve(),
        )
    except (
        KeyError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ReleaseEvidenceError,
    ) as exc:
        print(f"release metadata failed closed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "sbom": sbom.relative_to(ROOT).as_posix(),
                "provenance": provenance.relative_to(ROOT).as_posix(),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
