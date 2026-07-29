#!/usr/bin/env python3
"""Acquire bounded public metadata for the HM Land Registry OKF bundle.

This program deliberately acquires discovery metadata only. It does not sign in,
send credentials, follow dataset download links, call production property
services, or retrieve title, ownership, address, polygon, transaction, search,
application, or other property records.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any
import urllib.error
import urllib.parse
import urllib.request


USER_AGENT = (
    "okf-LandRegistry-metadata-acquirer/0.2 "
    "(+https://github.com/chris-page-gov/okf-LandRegistry)"
)
TIMEOUT_SECONDS = 30
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
GOVUK_PAGE_SIZE = 500
GOVUK_MAX_RECORDS = 10_000
GITHUB_PAGE_SIZE = 100
GITHUB_MAX_PAGES = 10
GITHUB_MAX_REPOSITORIES = 1_000
CDDO_MAX_ROWS = 20_000

GOVUK_SEARCH_URL = "https://www.gov.uk/api/search.json"
GITHUB_REPOSITORIES_URL = "https://api.github.com/orgs/LandRegistry/repos"
CDDO_CATALOGUE_URL = (
    "https://raw.githubusercontent.com/co-cddo/api-catalogue/"
    "main/data/catalogue.csv"
)

ALLOWED_HOSTS = frozenset(
    {
        "www.gov.uk",
        "api.github.com",
        "raw.githubusercontent.com",
    }
)
EMITTED_HOST_ALLOWLISTS = {
    "govuk": frozenset({"www.gov.uk"}),
    "github": frozenset({"github.com"}),
    "cddo": frozenset(
        {
            "businessgateway.landregistry.gov.uk",
            "landregistry.github.io",
        }
    ),
}
SENSITIVE_QUERY_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "auth",
        "authorization",
        "client_secret",
        "code",
        "credential",
        "credentials",
        "jwt",
        "key",
        "password",
        "secret",
        "session",
        "sig",
        "signature",
        "token",
    }
)
SENSITIVE_QUERY_PREFIXES = (
    "x_amz_",
    "x_goog_",
)


class AcquisitionError(RuntimeError):
    """Raised when a source response fails a safety or completeness check."""


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Allow redirects only between the small HTTPS host allowlist."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        _validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _validate_url(url: str) -> None:
    if not isinstance(url, str) or not url:
        raise AcquisitionError("refusing missing or non-string URL")
    if url.startswith(("//", "\\\\")):
        raise AcquisitionError(f"refusing protocol-relative URL: {url}")
    if "\\" in url:
        raise AcquisitionError(f"refusing URL containing a backslash: {url}")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in url):
        raise AcquisitionError("refusing URL containing a control character")
    try:
        parsed = urllib.parse.urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise AcquisitionError(f"refusing malformed URL: {url}") from exc
    if parsed.scheme != "https":
        raise AcquisitionError(f"refusing non-HTTPS URL: {url}")
    if not parsed.netloc or not hostname:
        raise AcquisitionError(f"refusing URL without a host: {url}")
    if hostname.casefold() not in ALLOWED_HOSTS:
        raise AcquisitionError(f"refusing URL outside host allowlist: {url}")
    if port not in (None, 443):
        raise AcquisitionError(f"refusing URL with non-standard port: {url}")
    if parsed.username or parsed.password:
        raise AcquisitionError("refusing URL containing credentials")
    _reject_sensitive_query_keys(url)


def _normalized_query_key(key: str) -> str:
    normalized = "".join(
        character if character.isalnum() else "_"
        for character in key.strip().casefold()
    )
    while "__" in normalized:
        normalized = normalized.replace("__", "_")
    return normalized.strip("_")


def _reject_sensitive_query_keys(url: str) -> None:
    try:
        query = urllib.parse.urlsplit(url).query
    except ValueError as exc:
        raise AcquisitionError("refusing malformed URL query") from exc
    if not query:
        return
    for key, _ in urllib.parse.parse_qsl(
        query,
        keep_blank_values=True,
        strict_parsing=False,
    ):
        normalized = _normalized_query_key(key)
        if normalized in SENSITIVE_QUERY_KEYS or normalized.startswith(
            SENSITIVE_QUERY_PREFIXES
        ):
            raise AcquisitionError(
                f"refusing URL containing sensitive query key: {key!r}"
            )


def _validate_emitted_url(
    url: str,
    *,
    allowed_hosts: frozenset[str],
    context: str,
) -> urllib.parse.SplitResult:
    if not isinstance(url, str) or not url:
        raise AcquisitionError(f"{context} is missing a URL")
    if url.startswith(("//", "\\\\")):
        raise AcquisitionError(f"{context} contains a protocol-relative URL")
    if "\\" in url:
        raise AcquisitionError(f"{context} contains a URL with a backslash")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in url):
        raise AcquisitionError(f"{context} contains a URL control character")
    try:
        parsed = urllib.parse.urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise AcquisitionError(f"{context} contains a malformed URL") from exc
    if parsed.scheme != "https" or not parsed.netloc or not hostname:
        raise AcquisitionError(f"{context} URL must be absolute HTTPS")
    if hostname.casefold() not in allowed_hosts:
        raise AcquisitionError(
            f"{context} URL host {hostname!r} is outside its emitted-host allowlist"
        )
    if port not in (None, 443):
        raise AcquisitionError(f"{context} URL uses a non-standard port")
    if parsed.username or parsed.password:
        raise AcquisitionError(f"{context} URL contains credentials")
    _reject_sensitive_query_keys(url)
    return parsed


def _validate_govuk_link(link: Any) -> str:
    if not isinstance(link, str) or not link.startswith("/"):
        raise AcquisitionError("GOV.UK Search returned a missing or unsafe result link")
    if link.startswith("//") or "\\" in link:
        raise AcquisitionError("GOV.UK Search returned a protocol-relative result link")
    absolute = urllib.parse.urljoin("https://www.gov.uk/", link)
    parsed = _validate_emitted_url(
        absolute,
        allowed_hosts=EMITTED_HOST_ALLOWLISTS["govuk"],
        context="GOV.UK Search result",
    )
    if parsed.path in {"", "/"}:
        raise AcquisitionError("GOV.UK Search returned an empty result path")
    return absolute


def _validate_github_repository_url(url: Any) -> None:
    parsed = _validate_emitted_url(
        url,
        allowed_hosts=EMITTED_HOST_ALLOWLISTS["github"],
        context="GitHub repository",
    )
    decoded_path = urllib.parse.unquote(parsed.path)
    path_parts = [part for part in decoded_path.split("/") if part]
    if (
        len(path_parts) < 2
        or path_parts[0].casefold() != "landregistry"
        or any(part in {".", ".."} for part in path_parts)
    ):
        raise AcquisitionError(
            "GitHub repository URL is outside the LandRegistry organisation"
        )


def _validate_cddo_row_urls(row: dict[str, str]) -> None:
    _validate_emitted_url(
        row.get("url", ""),
        allowed_hosts=EMITTED_HOST_ALLOWLISTS["cddo"],
        context="CDDO API endpoint",
    )
    documentation = row.get("documentation", "").strip()
    if documentation:
        _validate_emitted_url(
            documentation,
            allowed_hosts=EMITTED_HOST_ALLOWLISTS["cddo"],
            context="CDDO documentation",
        )


def _reject_unsafe_embedded_urls(value: Any, *, context: str) -> None:
    """Reject credential-like or protocol-relative URLs in raw source objects.

    Source APIs contain URL fields that are retained as opaque raw metadata.
    Only the publisher-facing fields validated above become navigable bundle
    URLs, but no raw object may smuggle a protocol-relative or signed URL into a
    public snapshot.
    """

    if isinstance(value, dict):
        for key, item in value.items():
            _reject_unsafe_embedded_urls(item, context=f"{context}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _reject_unsafe_embedded_urls(item, context=f"{context}[{index}]")
        return
    if not isinstance(value, str):
        return
    stripped = value.strip()
    if stripped.startswith(("//", "\\\\")):
        raise AcquisitionError(f"{context} contains a protocol-relative URL")
    if stripped.startswith(("https://", "http://")):
        _reject_sensitive_query_keys(stripped)


def _utc_now() -> str:
    return (
        dt.datetime.now(dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(SafeRedirectHandler())


def _fetch_bytes(
    opener: urllib.request.OpenerDirector,
    url: str,
    *,
    accept: str,
) -> tuple[bytes, dict[str, Any]]:
    _validate_url(url)
    request = urllib.request.Request(
        url,
        headers={
            "Accept": accept,
            "Accept-Encoding": "identity",
            "User-Agent": USER_AGENT,
        },
        method="GET",
    )
    try:
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            final_url = response.geturl()
            _validate_url(final_url)
            status = getattr(response, "status", None)
            if status is None:
                status = response.getcode()
            if not isinstance(status, int) or not 200 <= status < 300:
                raise AcquisitionError(
                    f"unexpected HTTP status from {url}: {status!r}"
                )
            content_type = response.headers.get("Content-Type", "") or ""
            media_type = content_type.split(";", 1)[0].strip().casefold()
            if not media_type:
                raise AcquisitionError(f"response from {url} lacks a media type")
            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    declared_size = int(content_length)
                except ValueError as exc:
                    raise AcquisitionError(
                        f"invalid Content-Length from {url}: {content_length!r}"
                    ) from exc
                if declared_size > MAX_RESPONSE_BYTES:
                    raise AcquisitionError(
                        f"response from {url} exceeds {MAX_RESPONSE_BYTES} bytes"
                    )
            payload = response.read(MAX_RESPONSE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AcquisitionError(f"request failed for {url}: {exc}") from exc
    if len(payload) > MAX_RESPONSE_BYTES:
        raise AcquisitionError(
            f"response from {url} exceeds {MAX_RESPONSE_BYTES} bytes"
        )
    return payload, {
        "request_url": url,
        "final_url": final_url,
        "retrieved_at": _utc_now(),
        "http_status": status,
        "media_type": media_type,
        "byte_count": len(payload),
    }


def _fetch_json(
    opener: urllib.request.OpenerDirector,
    url: str,
) -> tuple[Any, dict[str, Any]]:
    payload, receipt = _fetch_bytes(
        opener,
        url,
        accept="application/json",
    )
    if receipt["media_type"] not in {
        "application/json",
        "application/vnd.github+json",
    }:
        raise AcquisitionError(
            f"unexpected JSON media type from {url}: {receipt['media_type']}"
        )
    try:
        return json.loads(payload.decode("utf-8")), receipt
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AcquisitionError(f"invalid JSON from {url}: {exc}") from exc


def _govuk_content_type(result: dict[str, Any]) -> str:
    for key in (
        "content_store_document_type",
        "document_type",
        "format",
    ):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "unknown"


def _completed_envelope(
    *,
    observed_at: str,
    receipts: list[dict[str, Any]],
    terminal_outcome: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    if not receipts:
        raise AcquisitionError("cannot complete a source envelope without a receipt")
    terminal_receipt = receipts[-1]
    if terminal_outcome.get("status") != "complete":
        raise AcquisitionError("refusing to publish a non-complete source outcome")
    return {
        "observed_at": observed_at,
        "retrieved_at": terminal_receipt["retrieved_at"],
        "request_url": terminal_receipt["request_url"],
        "final_url": terminal_receipt["final_url"],
        "http_status": terminal_receipt["http_status"],
        "media_type": terminal_receipt["media_type"],
        "request_receipts": receipts,
        "terminal_outcome": terminal_outcome,
        **payload,
    }


def acquire_govuk(
    opener: urllib.request.OpenerDirector,
    observed_at: str,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    expected_total: int | None = None
    start = 0

    while expected_total is None or start < expected_total:
        query = urllib.parse.urlencode(
            {
                "filter_organisations": "land-registry",
                "count": GOVUK_PAGE_SIZE,
                "start": start,
                "order": "-public_timestamp",
            }
        )
        url = f"{GOVUK_SEARCH_URL}?{query}"
        page, receipt = _fetch_json(opener, url)
        if not isinstance(page, dict):
            raise AcquisitionError("GOV.UK Search response is not an object")
        total = page.get("total")
        page_results = page.get("results")
        if not isinstance(total, int) or total < 0:
            raise AcquisitionError("GOV.UK Search total is not a non-negative integer")
        if total > GOVUK_MAX_RECORDS:
            raise AcquisitionError(
                f"GOV.UK Search total {total} exceeds cap {GOVUK_MAX_RECORDS}"
            )
        if expected_total is None:
            expected_total = total
        elif total != expected_total:
            raise AcquisitionError(
                "GOV.UK Search total changed during acquisition; retry a fresh snapshot"
            )
        if not isinstance(page_results, list) or not all(
            isinstance(item, dict) for item in page_results
        ):
            raise AcquisitionError("GOV.UK Search results are not an object array")
        if len(page_results) > GOVUK_PAGE_SIZE:
            raise AcquisitionError("GOV.UK Search returned an oversized page")
        if not page_results and start < expected_total:
            raise AcquisitionError("GOV.UK Search pagination ended before the total")
        for result_index, result in enumerate(page_results):
            _reject_unsafe_embedded_urls(
                result,
                context=(
                    f"GOV.UK Search page {len(receipts) + 1} "
                    f"result {result_index}"
                ),
            )
            _validate_govuk_link(result.get("link"))
        page_end = start + len(page_results)
        receipt["sequence"] = len(receipts) + 1
        receipt["pagination"] = {
            "kind": "offset",
            "start": start,
            "requested_count": GOVUK_PAGE_SIZE,
            "returned_count": len(page_results),
            "declared_total": total,
            "terminal": page_end >= total,
        }
        receipts.append(receipt)
        results.extend(page_results)
        start += len(page_results)
        if len(results) > expected_total:
            raise AcquisitionError("GOV.UK Search returned more rows than its total")

    if expected_total is None or len(results) != expected_total:
        raise AcquisitionError("GOV.UK Search acquisition did not match its total")

    links = [result["link"] for result in results]
    if len(set(links)) != len(links):
        raise AcquisitionError("GOV.UK Search returned duplicate result links")

    counts: dict[str, int] = {}
    for result in results:
        key = _govuk_content_type(result)
        counts[key] = counts.get(key, 0) + 1

    return _completed_envelope(
        observed_at=observed_at,
        receipts=receipts,
        terminal_outcome={
            "status": "complete",
            "reason": "declared-total-reconciled",
            "record_count": expected_total,
            "request_count": len(receipts),
        },
        payload={
            "schema": "okf-hmlr-govuk-search-snapshot.v2",
            "total": expected_total,
            "results": results,
            "content_type_counts": dict(sorted(counts.items())),
        },
    )


def acquire_github(
    opener: urllib.request.OpenerDirector,
    observed_at: str,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    terminal_reason: str | None = None

    for page_number in range(1, GITHUB_MAX_PAGES + 1):
        query = urllib.parse.urlencode(
            {
                "type": "public",
                "sort": "full_name",
                "direction": "asc",
                "per_page": GITHUB_PAGE_SIZE,
                "page": page_number,
            }
        )
        url = f"{GITHUB_REPOSITORIES_URL}?{query}"
        page, receipt = _fetch_json(opener, url)
        if not isinstance(page, list) or not all(
            isinstance(item, dict) for item in page
        ):
            raise AcquisitionError("GitHub repositories response is not an object array")
        if len(page) > GITHUB_PAGE_SIZE:
            raise AcquisitionError("GitHub returned an oversized repositories page")
        for repository_index, repository in enumerate(page):
            _reject_unsafe_embedded_urls(
                repository,
                context=(
                    f"GitHub repositories page {page_number} "
                    f"result {repository_index}"
                ),
            )
            owner = repository.get("owner")
            if (
                not isinstance(owner, dict)
                or str(owner.get("login", "")).casefold() != "landregistry"
                or repository.get("private") is not False
            ):
                raise AcquisitionError(
                    "GitHub returned a private repository or unexpected owner"
                )
            _validate_github_repository_url(repository.get("html_url"))
        terminal = len(page) < GITHUB_PAGE_SIZE
        receipt["sequence"] = len(receipts) + 1
        receipt["pagination"] = {
            "kind": "page",
            "page": page_number,
            "requested_count": GITHUB_PAGE_SIZE,
            "returned_count": len(page),
            "terminal": terminal,
        }
        receipts.append(receipt)
        results.extend(page)
        if len(results) > GITHUB_MAX_REPOSITORIES:
            raise AcquisitionError(
                f"GitHub repository count exceeds cap {GITHUB_MAX_REPOSITORIES}"
            )
        if terminal:
            terminal_reason = "short-page"
            break
    else:
        raise AcquisitionError(
            "GitHub pagination reached its page cap without a terminal page"
        )

    node_ids = [repository.get("node_id") for repository in results]
    if any(not isinstance(node_id, str) or not node_id for node_id in node_ids):
        raise AcquisitionError("GitHub returned a repository without a node_id")
    if len(set(node_ids)) != len(node_ids):
        raise AcquisitionError("GitHub returned duplicate repository node_ids")

    if terminal_reason is None:
        raise AcquisitionError("GitHub acquisition lacks a terminal outcome")
    return _completed_envelope(
        observed_at=observed_at,
        receipts=receipts,
        terminal_outcome={
            "status": "complete",
            "reason": terminal_reason,
            "record_count": len(results),
            "request_count": len(receipts),
        },
        payload={
            "schema": "okf-hmlr-github-repositories-snapshot.v2",
            "total": len(results),
            "results": results,
        },
    )


def acquire_cddo(
    opener: urllib.request.OpenerDirector,
    observed_at: str,
) -> dict[str, Any]:
    payload, receipt = _fetch_bytes(
        opener,
        CDDO_CATALOGUE_URL,
        accept="text/csv",
    )
    if receipt["media_type"] not in {
        "application/csv",
        "text/csv",
        "text/plain",
    }:
        raise AcquisitionError(
            "unexpected CDDO API Catalogue media type: "
            f"{receipt['media_type']}"
        )
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AcquisitionError("CDDO API Catalogue is not UTF-8") from exc
    reader = csv.DictReader(io.StringIO(text))
    required_fields = {
        "dateAdded",
        "dateUpdated",
        "url",
        "name",
        "description",
        "documentation",
        "license",
        "maintainer",
        "areaServed",
        "startDate",
        "endDate",
        "provider",
    }
    fieldnames = set(reader.fieldnames or [])
    if not required_fields.issubset(fieldnames):
        missing = sorted(required_fields - fieldnames)
        raise AcquisitionError(
            f"CDDO API Catalogue is missing required columns: {missing}"
        )

    all_rows: list[dict[str, str]] = []
    rows_scanned = 0
    for row_number, row in enumerate(reader, start=1):
        rows_scanned = row_number
        if row_number > CDDO_MAX_ROWS:
            raise AcquisitionError(
                f"CDDO API Catalogue exceeds cap {CDDO_MAX_ROWS} rows"
            )
        normalized = {key: value or "" for key, value in row.items() if key is not None}
        if normalized.get("provider", "").strip().casefold() == "hm-land-registry":
            _reject_unsafe_embedded_urls(
                normalized,
                context=f"CDDO API Catalogue row {row_number}",
            )
            _validate_cddo_row_urls(normalized)
            all_rows.append(normalized)

    if not all_rows:
        raise AcquisitionError(
            "CDDO API Catalogue returned no hm-land-registry provider rows"
        )
    all_rows.sort(
        key=lambda row: (
            row.get("name", "").casefold(),
            row.get("url", ""),
            row.get("dateUpdated", ""),
        )
    )

    receipt["sequence"] = 1
    receipt["pagination"] = {
        "kind": "single-response",
        "rows_scanned": rows_scanned,
        "returned_count": len(all_rows),
        "terminal": True,
    }
    return _completed_envelope(
        observed_at=observed_at,
        receipts=[receipt],
        terminal_outcome={
            "status": "complete",
            "reason": "single-response-provider-filter-complete",
            "record_count": len(all_rows),
            "request_count": 1,
            "rows_scanned": rows_scanned,
        },
        payload={
            "schema": "okf-hmlr-cddo-api-catalogue-snapshot.v2",
            "total": len(all_rows),
            "results": all_rows,
        },
    )


def _canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _write_atomic(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _file_entry(path: Path, record_count: int) -> dict[str, Any]:
    payload = path.read_bytes()
    return {
        "path": path.name,
        "byte_count": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "record_count": record_count,
    }


def _manifest_source_details(envelope: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "schema",
        "retrieved_at",
        "request_url",
        "final_url",
        "http_status",
        "media_type",
        "request_receipts",
        "terminal_outcome",
        "total",
    )
    missing = [key for key in keys if key not in envelope]
    if missing:
        raise AcquisitionError(
            f"source envelope lacks manifest provenance fields: {missing}"
        )
    return {key: envelope[key] for key in keys}


def _normalize_observed_at(value: str) -> str:
    candidate = value.strip()
    if len(candidate) == 10:
        candidate = f"{candidate}T00:00:00Z"
    parse_value = candidate[:-1] + "+00:00" if candidate.endswith("Z") else candidate
    try:
        parsed = dt.datetime.fromisoformat(parse_value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "--observed-at must be an ISO 8601 date or timezone-aware datetime"
        ) from exc
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError(
            "--observed-at datetime must include a timezone"
        )
    utc = parsed.astimezone(dt.timezone.utc).replace(microsecond=0)
    return utc.isoformat().replace("+00:00", "Z")


def acquire(output_dir: Path, observed_at: str) -> None:
    destination = output_dir.expanduser().resolve()
    if destination.exists():
        raise AcquisitionError(
            f"output directory already exists; refusing to overwrite: {destination}"
        )
    parent = destination.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{destination.name}.staging-",
            dir=parent,
        )
    )
    try:
        opener = _opener()
        govuk = acquire_govuk(opener, observed_at)
        github = acquire_github(opener, observed_at)
        cddo = acquire_cddo(opener, observed_at)

        files_and_counts = (
            ("govuk-search.json", govuk, govuk["total"]),
            ("github-repositories.json", github, github["total"]),
            ("cddo-api-catalogue.json", cddo, cddo["total"]),
        )
        for filename, envelope, _ in files_and_counts:
            _write_atomic(staging / filename, _canonical_json_bytes(envelope))

        entries = [
            _file_entry(staging / filename, record_count)
            for filename, _, record_count in files_and_counts
        ]
        source_details = {
            "govuk_search": _manifest_source_details(govuk),
            "github_repositories": _manifest_source_details(github),
            "cddo_api_catalogue": _manifest_source_details(cddo),
        }
        manifest = {
            "schema": "okf-hmlr-metadata-snapshot.v2",
            "observed_at": observed_at,
            "retrieved_at": max(
                details["retrieved_at"] for details in source_details.values()
            ),
            "acquirer": {
                "name": "scripts/acquire.py",
                "version": "0.2",
                "sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            },
            "acquisition_policy": {
                "scope": "Public discovery metadata only",
                "authentication": "No credentials, cookies, API keys, or sign-in",
                "production_property_records": False,
                "dataset_downloads": False,
                "page_bodies_and_attachments": False,
                "fail_closed": True,
                "protocol_relative_urls_rejected": True,
                "sensitive_query_keys_rejected": True,
                "emitted_host_allowlists": {
                    source: sorted(hosts)
                    for source, hosts in EMITTED_HOST_ALLOWLISTS.items()
                },
                "timeout_seconds": TIMEOUT_SECONDS,
                "maximum_response_bytes": MAX_RESPONSE_BYTES,
                "user_agent": USER_AGENT,
                "source_endpoints": [
                    GOVUK_SEARCH_URL,
                    GITHUB_REPOSITORIES_URL,
                    CDDO_CATALOGUE_URL,
                ],
                "record_caps": {
                    "govuk_search": GOVUK_MAX_RECORDS,
                    "github_repositories": GITHUB_MAX_REPOSITORIES,
                    "cddo_catalogue_rows_scanned": CDDO_MAX_ROWS,
                },
            },
            "files": entries,
            "sources": source_details,
            "terminal_outcome": {
                "status": "complete",
                "reason": "all-sources-complete",
                "source_count": len(source_details),
                "record_count": sum(
                    details["total"] for details in source_details.values()
                ),
            },
            "totals": {
                "govuk_search": govuk["total"],
                "github_repositories": github["total"],
                "cddo_api_catalogue": cddo["total"],
            },
        }
        _write_atomic(staging / "manifest.json", _canonical_json_bytes(manifest))
        os.replace(staging, destination)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Acquire a bounded, metadata-only HM Land Registry source snapshot. "
            "The output directory must not already exist."
        )
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="New directory to publish atomically after every source succeeds",
    )
    parser.add_argument(
        "--observed-at",
        type=_normalize_observed_at,
        required=True,
        help="ISO 8601 date or timezone-aware snapshot timestamp",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        acquire(args.output_dir, args.observed_at)
    except AcquisitionError as exc:
        raise SystemExit(f"acquisition failed closed: {exc}") from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
