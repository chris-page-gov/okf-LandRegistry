from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "observe_govuk_content", ROOT / "scripts" / "observe_govuk_content.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ObserveGovukContentTests(unittest.TestCase):
    def test_route_is_path_only_and_api_url_is_fixed(self) -> None:
        self.assertEqual(
            MODULE.api_url("/government/publications/example"),
            "https://www.gov.uk/api/content/government/publications/example",
        )
        for unsafe in (
            "https://example.test/a",
            "//example.test/a",
            "/a?token=secret",
            "/a/../b",
        ):
            with self.subTest(unsafe=unsafe), self.assertRaises(ValueError):
                MODULE.safe_base_path(unsafe)

    def test_projection_excludes_body_contacts_and_details(self) -> None:
        result = MODULE.project(
            {
                "base_path": "/english",
                "content_id": "english-id",
                "document_type": "guidance",
                "locale": "en",
                "title": "English",
                "body": "must not survive",
                "details": {"secret": "must not survive"},
                "links": {
                    "contacts": [{"title": "must not survive"}],
                    "available_translations": [
                        {
                            "base_path": "/welsh.cy",
                            "content_id": "welsh-id",
                            "document_type": "guidance",
                            "locale": "cy",
                            "title": "Welsh",
                        }
                    ],
                },
            }
        )
        self.assertEqual(result["locale"], "en")
        self.assertEqual(result["available_translations"][0]["locale"], "cy")
        self.assertNotIn("body", result)
        self.assertNotIn("details", result)
        self.assertNotIn("contacts", result)

    @mock.patch.object(MODULE, "read_bounded")
    def test_observation_records_digest_not_raw_payload(self, read_bounded: mock.Mock) -> None:
        raw = b'{"base_path":"/a","locale":"en","title":"A","links":{"available_translations":[]}}'
        read_bounded.return_value = (
            raw,
            "https://www.gov.uk/api/content/a",
            200,
        )
        result = MODULE.observe(["/a"], "2026-07-29T18:00:00Z")
        row = result["observations"][0]
        self.assertEqual(row["response_bytes"], len(raw))
        self.assertNotIn("raw_response", row)
        self.assertFalse(result["body_retained"])


if __name__ == "__main__":
    unittest.main()
