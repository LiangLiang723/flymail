from __future__ import annotations

import hashlib
import unittest

from cryptography.exceptions import InvalidSignature, InvalidTag

from flymail.infrastructure.security.credentials import CredentialCipher, EncryptedValue
from flymail.infrastructure.security.passwords import hash_password, verify_password
from flymail.infrastructure.security.sessions import (
    new_session_token,
    sign_session_cookie,
    verify_session_cookie,
)


class PasswordSecurityTests(unittest.TestCase):
    def test_same_password_uses_random_salt_and_both_hashes_verify(self):
        first = hash_password("correct horse battery staple")
        second = hash_password("correct horse battery staple")

        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("scrypt$v=1$n=32768$r=8$p=1$"))
        self.assertTrue(verify_password("correct horse battery staple", first))
        self.assertTrue(verify_password("correct horse battery staple", second))

    def test_wrong_or_malformed_password_hash_fails_without_exception(self):
        encoded = hash_password("administrator-import-password")

        self.assertFalse(verify_password("wrong-password", encoded))
        for malformed in ("", "not-a-hash", encoded.replace("n=32768", "n=999999999"), encoded + "$extra"):
            with self.subTest(malformed=malformed[:40]):
                self.assertFalse(verify_password("administrator-import-password", malformed))

    def test_hashing_rejects_empty_password_but_accepts_short_nonempty_import_value(self):
        with self.assertRaisesRegex(ValueError, "non-empty"):
            hash_password("")
        encoded = hash_password("x")
        self.assertTrue(verify_password("x", encoded))


class CredentialEncryptionTests(unittest.TestCase):
    def test_same_plaintext_has_random_nonce_and_round_trips(self):
        cipher = CredentialCipher.from_master_secret("master-secret-value-for-tests", key_version=7)

        first = cipher.encrypt("acc_primary", b"oauth-refresh-token")
        second = cipher.encrypt("acc_primary", b"oauth-refresh-token")

        self.assertNotEqual(first.nonce_b64, second.nonce_b64)
        self.assertNotEqual(first.ciphertext_b64, second.ciphertext_b64)
        self.assertEqual(cipher.decrypt("acc_primary", first), b"oauth-refresh-token")
        self.assertEqual(first.algorithm, "AES-256-GCM")
        self.assertEqual(first.key_version, 7)

    def test_ciphertext_is_bound_to_account_and_master_secret(self):
        value = CredentialCipher.from_master_secret("first-master-secret-value").encrypt(
            "acc_primary", b"mail-password"
        )

        with self.assertRaises(InvalidTag):
            CredentialCipher.from_master_secret("first-master-secret-value").decrypt("acc_other", value)
        with self.assertRaises(InvalidTag):
            CredentialCipher.from_master_secret("second-master-secret-value").decrypt("acc_primary", value)

    def test_key_version_mismatch_and_invalid_algorithm_are_rejected(self):
        cipher = CredentialCipher.from_master_secret("master-secret-value-for-tests", key_version=2)
        value = cipher.encrypt("acc_primary", b"mail-password")

        with self.assertRaisesRegex(ValueError, "key version"):
            CredentialCipher.from_master_secret("master-secret-value-for-tests", key_version=3).decrypt(
                "acc_primary", value
            )
        altered = EncryptedValue(
            algorithm="AES-128-CBC",
            key_version=value.key_version,
            nonce_b64=value.nonce_b64,
            ciphertext_b64=value.ciphertext_b64,
        )
        with self.assertRaisesRegex(ValueError, "algorithm"):
            cipher.decrypt("acc_primary", altered)

    def test_log_safe_representations_do_not_include_plaintext_or_ciphertext(self):
        plaintext = b"top-secret-mail-credential"
        cipher = CredentialCipher.from_master_secret("master-secret-value-for-tests")
        value = cipher.encrypt("acc_primary", plaintext)

        self.assertNotIn(plaintext.decode(), repr(cipher))
        self.assertNotIn(plaintext.decode(), repr(value))
        self.assertNotIn(value.ciphertext_b64, repr(value))
        self.assertIn("AES-256-GCM", repr(value))

    def test_short_master_secret_and_empty_account_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "at least 16"):
            CredentialCipher.from_master_secret("short")
        cipher = CredentialCipher.from_master_secret("master-secret-value-for-tests")
        with self.assertRaisesRegex(ValueError, "account_id"):
            cipher.encrypt("", b"credential")


class SessionSecurityTests(unittest.TestCase):
    def test_session_token_returns_random_raw_value_and_sha256_hash_only(self):
        raw_first, hash_first = new_session_token()
        raw_second, hash_second = new_session_token()

        self.assertNotEqual(raw_first, raw_second)
        self.assertNotEqual(hash_first, hash_second)
        self.assertNotEqual(raw_first, hash_first)
        self.assertEqual(hash_first, hashlib.sha256(raw_first.encode("ascii")).hexdigest())
        self.assertEqual(len(hash_first), 64)

    def test_signed_cookie_round_trips_and_rejects_tampering(self):
        secret = b"session-cookie-secret-value"
        cookie = sign_session_cookie("ses_0123456789abcdef", secret)

        self.assertEqual(verify_session_cookie(cookie, secret), "ses_0123456789abcdef")
        with self.assertRaises(InvalidSignature):
            verify_session_cookie(cookie + "x", secret)
        with self.assertRaises(InvalidSignature):
            verify_session_cookie(cookie, b"different-session-secret")

    def test_cookie_and_credential_keys_are_separate_and_short_secret_is_rejected(self):
        secret = b"shared-master-secret-value"
        cookie = sign_session_cookie("ses_primary", secret)
        credential = CredentialCipher.from_master_secret(secret.decode()).encrypt("acc_primary", b"same-input")

        self.assertNotIn(credential.ciphertext_b64, cookie)
        with self.assertRaisesRegex(ValueError, "at least 16"):
            sign_session_cookie("ses_primary", b"short")
        with self.assertRaisesRegex(ValueError, "session_id"):
            sign_session_cookie("", secret)


if __name__ == "__main__":
    unittest.main()
