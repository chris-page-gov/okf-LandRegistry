from __future__ import annotations

import unittest

from scripts.create_release_metadata import action_pins, locked_packages


class ReleaseMetadataTests(unittest.TestCase):
    def test_dependency_lock_has_expected_bounded_package_set(self) -> None:
        packages = locked_packages(__import__("pathlib").Path("requirements-lock.txt"))
        self.assertEqual(
            {
                "attrs",
                "jsonschema",
                "jsonschema-specifications",
                "referencing",
                "rpds-py",
                "ruamel-yaml",
                "typing-extensions",
            },
            {name for name, _version in packages},
        )

    def test_pages_workflow_actions_are_sha_pinned(self) -> None:
        pins = action_pins(
            __import__("pathlib").Path(".github/workflows/pages.yml")
        )
        self.assertGreaterEqual(len(pins), 5)
        self.assertTrue(all(len(pin["commit"]) == 40 for pin in pins))


if __name__ == "__main__":
    unittest.main()
