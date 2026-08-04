#!/usr/bin/env python3
"""Capture bounded GOV.UK Content API locale and translation metadata.

The observation intentionally excludes body, contact and detail payloads. It
stores only the fields needed to evidence language and translation links.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse


ROOT = Path(__file__).resolve().parents[1]
API_ORIGIN = "https://www.gov.uk"
MAX_RESPONSE_BYTES = 2_000_000
TIMEOUT_SECONDS = 20
DEFAULT_PATHS = (
    "/government/publications/land-registry-welsh-glossary-of-legal-terms",
)


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observed-at", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--path",
        action="append",
        dest="paths",
        help="Allowlisted public GOV.UK content base path; repeat as needed.",
    )
    return parser.parse_args()


def safe_base_path(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError(f"content route must be a path only: {value!r}")
    if not value.startswith("/") or "/../" in f"{value}/" or "\x00" in value:
        raise ValueError(f"unsafe content route: {value!r}")
    return value.rstrip("/") or "/"


def api_url(base_path: str) -> str:
    return API_ORIGIN + "/api/content" + quote(safe_base_path(base_path), safe="/")


def read_bounded(url: str) -> tuple[bytes, str, int]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "okf-landregistry-content-observer/0.2.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            final_url = response.geturl()
            status = response.status
            payload = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"Content API returned HTTP {error.code} for {url}") from error
    if len(payload) > MAX_RESPONSE_BYTES:
        raise RuntimeError(f"Content API response exceeded {MAX_RESPONSE_BYTES} bytes")
    if status != 200:
        raise RuntimeError(f"Content API returned HTTP {status} for {url}")
    if final_url != url:
        raise RuntimeError(f"Content API redirected outside the exact request: {final_url}")
    return payload, final_url, status


def safe_link(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: row.get(key)
        for key in (
            "base_path",
            "content_id",
            "document_type",
            "locale",
            "public_updated_at",
            "schema_name",
            "title",
        )
        if row.get(key) is not None
    }


def project(payload: dict[str, Any]) -> dict[str, Any]:
    links = payload.get("links")
    translations = links.get("available_translations", []) if isinstance(links, dict) else []
    if not isinstance(translations, list):
        raise ValueError("available_translations must be an array")
    projected = safe_link(payload)
    base_path = projected.get("base_path")
    if not isinstance(base_path, str):
        raise ValueError("Content API payload lacks base_path")
    projected["web_url"] = API_ORIGIN + safe_base_path(base_path)
    projected["withdrawn"] = bool(payload.get("withdrawn_notice"))
    projected["available_translations"] = [
        {
            **safe_link(row),
            "web_url": API_ORIGIN + safe_base_path(row["base_path"]),
        }
        for row in translations
        if isinstance(row, dict) and isinstance(row.get("base_path"), str)
    ]
    return projected


def observe(paths: list[str], observed_at: str) -> dict[str, Any]:
    observations: list[dict[str, Any]] = []
    for path in sorted({safe_base_path(value) for value in paths}):
        url = api_url(path)
        raw, final_url, status = read_bounded(url)
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("Content API response must be an object")
        observations.append(
            {
                "api_url": url,
                "final_url": final_url,
                "http_status": status,
                "response_bytes": len(raw),
                "response_sha256": hashlib.sha256(raw).hexdigest(),
                "metadata": project(parsed),
            }
        )
    return {
        "schema": "okf-hmlr-govuk-content-observation.v1",
        "observed_at": observed_at,
        "scope": "locale-and-available-translations-only",
        "body_retained": False,
        "contacts_retained": False,
        "details_retained": False,
        "observations": observations,
        "terminal_outcome": {
            "status": "complete",
            "requested": len(paths),
            "succeeded": len(observations),
            "failed": 0,
        },
    }


def main() -> int:
    arguments = parse_args()
    output = arguments.output.resolve()
    if output.exists():
        raise SystemExit(f"refusing to overwrite existing observation: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = observe(arguments.paths or list(DEFAULT_PATHS), arguments.observed_at)
    output.write_bytes(canonical_json(payload))
    print(
        json.dumps(
            {
                "output": str(output),
                "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                "observations": len(payload["observations"]),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
