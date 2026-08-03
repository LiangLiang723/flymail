"""User-entered password and credential fields have no FlyMail length limit."""

from __future__ import annotations

import unittest

from pydantic import ValidationError

from flymail.api.schemas.accounts import (
    CreateAccountRequest,
    SaveProxyRequest,
    UpdateCredentialRequest,
)
from flymail.api.schemas.auth import (
    CreateUserRequest,
    LoginRequest,
    PasswordChangeRequest,
    ResetPasswordRequest,
)
from flymail.api.schemas.backups import BackupPasswordRequest


class PasswordContractTests(unittest.TestCase):
    def test_password_and_credential_models_preserve_unbounded_exact_values(self):
        value = " x " + ("密" * 10_000)

        self.assertEqual(LoginRequest(username="user", password=value).password, value)
        changed = PasswordChangeRequest(
            current_password=value,
            new_password=value,
        )
        self.assertEqual(changed.current_password, value)
        self.assertEqual(changed.new_password, value)
        self.assertEqual(
            CreateUserRequest(username="created", password=value).password,
            value,
        )
        self.assertEqual(ResetPasswordRequest(new_password=value).new_password, value)

        account = CreateAccountRequest(
            provider_key="qq",
            email="user@example.com",
            credential_type="authorization_code",
            credential=value,
        )
        self.assertEqual(account.credential, value)
        self.assertEqual(
            UpdateCredentialRequest(
                credential_type="password",
                credential=value,
            ).credential,
            value,
        )
        self.assertEqual(
            SaveProxyRequest(
                scheme="http",
                host="proxy.example.com",
                port=8080,
                username="proxy-user",
                password=value,
            ).password,
            value,
        )
        self.assertEqual(BackupPasswordRequest(password=value).password, value)
        self.assertEqual(BackupPasswordRequest(password=" ").password, " ")

    def test_required_password_and_credential_fields_reject_only_empty_values(self):
        invalid_factories = (
            lambda: LoginRequest(username="user", password=""),
            lambda: PasswordChangeRequest(current_password="", new_password="x"),
            lambda: PasswordChangeRequest(current_password="x", new_password=""),
            lambda: CreateUserRequest(username="created", password=""),
            lambda: ResetPasswordRequest(new_password=""),
            lambda: CreateAccountRequest(
                provider_key="qq",
                email="user@example.com",
                credential_type="password",
                credential="",
            ),
            lambda: UpdateCredentialRequest(
                credential_type="password",
                credential="",
            ),
            lambda: BackupPasswordRequest(password=""),
        )
        for factory in invalid_factories:
            with self.subTest(factory=factory):
                with self.assertRaises(ValidationError):
                    factory()

        proxy = SaveProxyRequest(
            scheme="http",
            host="proxy.example.com",
            port=8080,
            username="",
            password="",
        )
        self.assertEqual(proxy.password, "")


if __name__ == "__main__":
    unittest.main()
