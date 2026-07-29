from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts import acquire as subject


OBSERVED_AT = "2026-07-29T07:53:38Z"
RETRIEVED_AT = "2026-07-29T08:00:00Z"


def receipt(
    request_url: str,
    *,
    final_url: str | None = None,
    media_type: str = "application/json",
) -> dict[str, object]:
    return {
        "request_url": request_url,
        "final_url": final_url or request_url,
        "retrieved_at": RETRIEVED_AT,
        "http_status": 200,
        "media_type": media_type,
        "byte_count": 10,
    }


def complete_envelope(schema: str, endpoint: str) -> dict[str, object]:
    request_receipt = {
        **receipt(endpoint),
        "sequence": 1,
        "pagination": {
            "kind": "single-response",
            "returned_count": 1,
            "terminal": True,
        },
    }
    return subject._completed_envelope(
        observed_at=OBSERVED_AT,
        receipts=[request_receipt],
        terminal_outcome={
            "status": "complete",
            "reason": "test-complete",
            "record_count": 1,
            "request_count": 1,
        },
        payload={
            "schema": schema,
            "total": 1,
            "results": [{"id": "test"}],
        },
    )


class FakeResponse:
    def __init__(
        self,
        payload: bytes,
        *,
        final_url: str,
        status: int = 200,
        media_type: str = "application/json; charset=utf-8",
    ) -> None:
        self._payload = payload
        self._final_url = final_url
        self.status = status
        self.headers = {
            "Content-Type": media_type,
            "Content-Length": str(len(payload)),
        }

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def geturl(self) -> str:
        return self._final_url

    def getcode(self) -> int:
        return self.status

    def read(self, amount: int) -> bytes:
        return self._payload[:amount]


class FakeOpener:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.request_urls: list[str] = []

    def open(
        self,
        request: object,
        *,
        timeout: int,
    ) -> FakeResponse:
        self.request_urls.append(request.full_url)
        self.timeout = timeout
        return self.response


class UrlSafetyTests(unittest.TestCase):
    def test_fetch_url_rejects_protocol_relative_and_sensitive_query(self) -> None:
        with self.assertRaises(subject.AcquisitionError):
            subject._validate_url("//www.gov.uk/api/search.json")
        with self.assertRaises(subject.AcquisitionError):
            subject._validate_url(
                "https://www.gov.uk/api/search.json?api%5Fkey=secret"
            )
        with self.assertRaises(subject.AcquisitionError):
            subject._validate_url(
                "https://www.gov.uk/api/search.json?X-Amz-Credential=secret"
            )

    def test_emitted_urls_are_source_allowlisted(self) -> None:
        with self.assertRaises(subject.AcquisitionError):
            subject._validate_govuk_link("//attacker.example/path")
        with self.assertRaises(subject.AcquisitionError):
            subject._validate_github_repository_url(
                "https://attacker.example/LandRegistry/repository"
            )
        with self.assertRaises(subject.AcquisitionError):
            subject._validate_github_repository_url(
                "https://github.com/LandRegistry/%2e%2e/attacker"
            )
        with self.assertRaises(subject.AcquisitionError):
            subject._validate_cddo_row_urls(
                {
                    "url": "https://businessgateway.landregistry.gov.uk/api",
                    "documentation": "https://attacker.example/docs",
                }
            )

    def test_wsdl_flag_is_not_misclassified_as_sensitive(self) -> None:
        subject._validate_cddo_row_urls(
            {
                "url": (
                    "https://businessgateway.landregistry.gov.uk/"
                    "b2b/BGSoapEngine/Example?wsdl"
                ),
                "documentation": (
                    "https://landregistry.github.io/bgtechdoc/services/example/"
                ),
            }
        )


class ReceiptTests(unittest.TestCase):
    def test_fetch_bytes_records_http_receipt(self) -> None:
        url = "https://www.gov.uk/api/search.json?count=0"
        response = FakeResponse(
            b'{"total": 0, "results": []}',
            final_url=url,
        )
        opener = FakeOpener(response)
        with mock.patch.object(subject, "_utc_now", return_value=RETRIEVED_AT):
            payload, request_receipt = subject._fetch_bytes(
                opener,
                url,
                accept="application/json",
            )
        self.assertEqual(payload, b'{"total": 0, "results": []}')
        self.assertEqual(
            request_receipt,
            {
                "request_url": url,
                "final_url": url,
                "retrieved_at": RETRIEVED_AT,
                "http_status": 200,
                "media_type": "application/json",
                "byte_count": len(payload),
            },
        )
        self.assertEqual(opener.timeout, subject.TIMEOUT_SECONDS)

    def test_govuk_envelope_has_offset_receipts_and_terminal_outcome(self) -> None:
        first_url = "https://www.gov.uk/api/search.json?page=first"
        second_url = "https://www.gov.uk/api/search.json?page=second"
        pages = [
            (
                {
                    "total": 3,
                    "results": [
                        {
                            "link": "/government/a",
                            "title": "A",
                            "content_store_document_type": "guidance",
                        },
                        {
                            "link": "/government/b",
                            "title": "B",
                            "content_store_document_type": "guidance",
                        },
                    ],
                },
                receipt(first_url),
            ),
            (
                {
                    "total": 3,
                    "results": [
                        {
                            "link": "/government/c",
                            "title": "C",
                            "content_store_document_type": "form",
                        }
                    ],
                },
                receipt(second_url),
            ),
        ]
        with (
            mock.patch.object(subject, "GOVUK_PAGE_SIZE", 2),
            mock.patch.object(subject, "_fetch_json", side_effect=pages),
        ):
            envelope = subject.acquire_govuk(mock.Mock(), OBSERVED_AT)
        self.assertEqual(envelope["schema"], "okf-hmlr-govuk-search-snapshot.v2")
        self.assertEqual(envelope["total"], 3)
        self.assertEqual(len(envelope["request_receipts"]), 2)
        self.assertFalse(
            envelope["request_receipts"][0]["pagination"]["terminal"]
        )
        self.assertTrue(
            envelope["request_receipts"][1]["pagination"]["terminal"]
        )
        self.assertEqual(
            envelope["terminal_outcome"],
            {
                "status": "complete",
                "reason": "declared-total-reconciled",
                "record_count": 3,
                "request_count": 2,
            },
        )

    def test_github_envelope_has_page_receipt_and_terminal_outcome(self) -> None:
        request_url = (
            "https://api.github.com/orgs/LandRegistry/repos?"
            "type=public&per_page=100&page=1"
        )
        repository = {
            "node_id": "R_test",
            "name": "example",
            "owner": {"login": "LandRegistry"},
            "private": False,
            "html_url": "https://github.com/LandRegistry/example",
        }
        with mock.patch.object(
            subject,
            "_fetch_json",
            return_value=([repository], receipt(request_url)),
        ):
            envelope = subject.acquire_github(mock.Mock(), OBSERVED_AT)
        self.assertEqual(
            envelope["schema"],
            "okf-hmlr-github-repositories-snapshot.v2",
        )
        self.assertEqual(envelope["total"], 1)
        self.assertEqual(
            envelope["request_receipts"][0]["pagination"],
            {
                "kind": "page",
                "page": 1,
                "requested_count": subject.GITHUB_PAGE_SIZE,
                "returned_count": 1,
                "terminal": True,
            },
        )
        self.assertEqual(
            envelope["terminal_outcome"],
            {
                "status": "complete",
                "reason": "short-page",
                "record_count": 1,
                "request_count": 1,
            },
        )

    def test_cddo_envelope_records_scan_and_filter_outcome(self) -> None:
        headers = [
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
        ]
        rows = [
            [
                "2026-01-01",
                "2026-01-02",
                "https://businessgateway.landregistry.gov.uk/example?wsdl",
                "Example",
                "Description",
                "https://landregistry.github.io/bgtechdoc/services/example/",
                "",
                "HM Land Registry",
                "England and Wales",
                "",
                "",
                "hm-land-registry",
            ],
            [
                "2026-01-01",
                "2026-01-02",
                "https://other.example/api",
                "Other",
                "Description",
                "",
                "",
                "Other",
                "",
                "",
                "",
                "other",
            ],
        ]
        csv_text = ",".join(headers) + "\n"
        csv_text += "\n".join(",".join(row) for row in rows) + "\n"
        request_receipt = receipt(
            subject.CDDO_CATALOGUE_URL,
            media_type="text/plain",
        )
        with mock.patch.object(
            subject,
            "_fetch_bytes",
            return_value=(csv_text.encode("utf-8"), request_receipt),
        ):
            envelope = subject.acquire_cddo(mock.Mock(), OBSERVED_AT)
        self.assertEqual(envelope["schema"], "okf-hmlr-cddo-api-catalogue-snapshot.v2")
        self.assertEqual(envelope["total"], 1)
        self.assertEqual(
            envelope["request_receipts"][0]["pagination"],
            {
                "kind": "single-response",
                "rows_scanned": 2,
                "returned_count": 1,
                "terminal": True,
            },
        )
        self.assertEqual(
            envelope["terminal_outcome"]["reason"],
            "single-response-provider-filter-complete",
        )


class ManifestTests(unittest.TestCase):
    def test_atomic_snapshot_manifest_carries_source_receipts(self) -> None:
        govuk = complete_envelope(
            "okf-hmlr-govuk-search-snapshot.v2",
            "https://www.gov.uk/api/search.json?count=1",
        )
        govuk["content_type_counts"] = {"guidance": 1}
        github = complete_envelope(
            "okf-hmlr-github-repositories-snapshot.v2",
            "https://api.github.com/orgs/LandRegistry/repos?per_page=1",
        )
        cddo = complete_envelope(
            "okf-hmlr-cddo-api-catalogue-snapshot.v2",
            subject.CDDO_CATALOGUE_URL,
        )
        with (
            tempfile.TemporaryDirectory(prefix="okf-hmlr-acquire-test-") as root,
            mock.patch.object(subject, "acquire_govuk", return_value=govuk),
            mock.patch.object(subject, "acquire_github", return_value=github),
            mock.patch.object(subject, "acquire_cddo", return_value=cddo),
        ):
            destination = Path(root) / "snapshot"
            subject.acquire(destination, OBSERVED_AT)
            manifest = json.loads((destination / "manifest.json").read_text())
            self.assertEqual(
                manifest["schema"],
                "okf-hmlr-metadata-snapshot.v2",
            )
            self.assertEqual("scripts/acquire.py", manifest["acquirer"]["name"])
            self.assertEqual("0.2", manifest["acquirer"]["version"])
            self.assertEqual(
                hashlib.sha256(Path(subject.__file__).read_bytes()).hexdigest(),
                manifest["acquirer"]["sha256"],
            )
            self.assertEqual(
                manifest["terminal_outcome"],
                {
                    "status": "complete",
                    "reason": "all-sources-complete",
                    "source_count": 3,
                    "record_count": 3,
                },
            )
            self.assertEqual(
                set(manifest["sources"]),
                {
                    "govuk_search",
                    "github_repositories",
                    "cddo_api_catalogue",
                },
            )
            for source_details in manifest["sources"].values():
                self.assertEqual(source_details["http_status"], 200)
                self.assertEqual(source_details["terminal_outcome"]["status"], "complete")
                self.assertEqual(len(source_details["request_receipts"]), 1)
            for file_details in manifest["files"]:
                payload = (destination / file_details["path"]).read_bytes()
                self.assertEqual(file_details["byte_count"], len(payload))
                self.assertEqual(
                    file_details["sha256"],
                    hashlib.sha256(payload).hexdigest(),
                )


if __name__ == "__main__":
    unittest.main()
