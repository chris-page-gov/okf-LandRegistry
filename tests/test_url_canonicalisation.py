from __future__ import annotations

import unittest

from scripts.build import canonical_https_url, ensure_https


class UrlQueryCanonicalisationTests(unittest.TestCase):
    def test_query_values_have_one_deterministic_ascii_encoding(self) -> None:
        raw = (
            "https://example.test/search?"
            "filters%5bpublisher%5d=HM+Land%20Registry&"
            "target=https%3a%2f%2fwww.gov.uk%2fa%3fx%3d1%26y%3d2&"
            "flag&duplicate=first&duplicate=second#section"
        )
        expected = (
            "https://example.test/search?"
            "filters%5Bpublisher%5D=HM%20Land%20Registry&"
            "target=https%3A%2F%2Fwww.gov.uk%2Fa%3Fx%3D1%26y%3D2&"
            "flag=&duplicate=first&duplicate=second"
        )
        canonical = canonical_https_url(raw)
        self.assertEqual(expected, canonical)
        self.assertEqual(canonical, canonical_https_url(canonical))

    def test_unicode_reserved_and_blank_values_are_canonicalised(self) -> None:
        self.assertEqual(
            (
                "https://example.test/search?"
                "q=%C2%A3%20value%2Fwith%3Freserved%3Dyes&empty="
            ),
            canonical_https_url(
                "https://example.test/search?"
                "q=%c2%a3+value%2fwith%3freserved%3dyes&empty="
            ),
        )

    def test_current_public_query_forms_are_canonicalised(self) -> None:
        self.assertEqual(
            (
                "https://www.data.gov.uk/search?"
                "filters%5Bpublisher%5D=HM%20Land%20Registry"
            ),
            ensure_https(
                "https://www.data.gov.uk/search?"
                "filters%5Bpublisher%5D=HM+Land+Registry"
            ),
        )
        self.assertEqual(
            (
                "https://businessgateway.landregistry.gov.uk/"
                "b2b/BGSoapEngine/Example?wsdl="
            ),
            ensure_https(
                "https://businessgateway.landregistry.gov.uk/"
                "b2b/BGSoapEngine/Example?wsdl"
            ),
        )

    def test_sensitive_keys_are_rejected_even_when_blank_or_encoded(self) -> None:
        queries = (
            "token",
            "TOKEN=",
            "api%5Fkey=",
            "client-secret=ignored",
            "safe=1&X-Amz-Algorithm=AWS4-HMAC-SHA256",
            "safe=1&X-Goog-Date=20260810T120000Z",
            "AWSAccessKeyId=",
            "pagination-token=opaque",
            "oauth_nonce=opaque",
            "SAMLResponse=opaque",
        )
        for query in queries:
            with self.subTest(query=query), self.assertRaisesRegex(
                ValueError, "sensitive query parameter"
            ):
                canonical_https_url(f"https://example.test/path?{query}")

    def test_camel_case_credential_keys_are_rejected_at_every_query_level(self) -> None:
        credential_keys = (
            "accessToken",
            "authToken",
            "clientSecret",
            "downloadToken",
            "idToken",
            "refreshToken",
            "sessionToken",
            "securityToken",
            "sharedAccessSignature",
            "keyPairId",
        )
        for key in credential_keys:
            with self.subTest(key=key, level="top"), self.assertRaisesRegex(
                ValueError, "sensitive query parameter"
            ):
                canonical_https_url(
                    f"https://example.test/path?{key}=not-a-real-secret"
                )
            with self.subTest(key=key, level="nested"), self.assertRaisesRegex(
                ValueError, "sensitive query parameter"
            ):
                canonical_https_url(
                    "https://example.test/path?safe="
                    f"nested%253F{key}%253Dnot-a-real-secret"
                )

    def test_nested_or_query_valued_credentials_are_rejected(self) -> None:
        queries = (
            "safe=https%3A%2F%2Fexample.test%2F%3Faccess_token%3Dsecret",
            "safe=prefix%26token%3Dsecret",
            "to%256Ben=secret",
        )
        for query in queries:
            with self.subTest(query=query), self.assertRaisesRegex(
                ValueError, "sensitive query parameter"
            ):
                canonical_https_url(f"https://example.test/path?{query}")

    def test_semicolon_evasions_are_rejected_at_every_encoding_depth(self) -> None:
        queries = (
            "safe=1;token=secret",
            "safe=1%3Btoken%3Dsecret",
            "safe=1%253Btoken%253Dsecret",
            "safe=one%3Btwo",
        )
        for query in queries:
            with self.subTest(query=query), self.assertRaisesRegex(
                ValueError, "semicolon|unsafe query delimiter"
            ):
                canonical_https_url(f"https://example.test/path?{query}")

    def test_encoded_controls_quotes_and_excessive_queries_are_rejected(self) -> None:
        rejected = (
            "https://example.test/path?q=%FF",
            "https://example.test/path?q=%0A",
            "https://example.test/path?q=%252522",
            "https://example.test/path#access_token=secret",
        )
        for url in rejected:
            with self.subTest(url=url), self.assertRaises(ValueError):
                canonical_https_url(url)

        excessive = "&".join(f"field{index}=value" for index in range(129))
        with self.assertRaisesRegex(ValueError, "invalid or excessive query"):
            canonical_https_url(f"https://example.test/path?{excessive}")

    def test_rejection_messages_do_not_echo_secret_values(self) -> None:
        for url in (
            "https://example.test/path?token=super-secret",
            "https://user:super-secret@example.test/path",
            "https://example.test:not-a-port/path?token=super-secret",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError) as caught:
                canonical_https_url(url)
            self.assertNotIn("super-secret", str(caught.exception))

    def test_similar_non_sensitive_keys_remain_available(self) -> None:
        self.assertEqual(
            (
                "https://example.test/path?"
                "monkey=value&tokenisation=public&signature_method=none"
            ),
            canonical_https_url(
                "https://example.test/path?"
                "monkey=value&tokenisation=public&signature_method=none"
            ),
        )


if __name__ == "__main__":
    unittest.main()
