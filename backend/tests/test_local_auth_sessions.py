import os
import unittest
from unittest.mock import patch

from fastapi import Response

from config import SESSION_COOKIE_NAME
from services.security import clear_session_cookie, create_session_cookie, parse_session_cookie


class LocalAuthSessionTest(unittest.TestCase):
    def test_same_user_can_keep_two_independent_browser_sessions(self):
        with patch.dict(os.environ, {"FLYMAIL_SESSION_SECRET": "test-session-secret-1234567890"}, clear=False):
            with patch(
                "services.security.time.time",
                side_effect=[2_000_000_000, 2_000_000_001, 2_000_000_002, 2_000_000_002],
            ):
                first_cookie = create_session_cookie("user-1")
                second_cookie = create_session_cookie("user-1")
                first_payload = parse_session_cookie(first_cookie)
                second_payload = parse_session_cookie(second_cookie)

            self.assertNotEqual(first_cookie, second_cookie)
            self.assertEqual(first_payload["uid"], "user-1")
            self.assertEqual(second_payload["uid"], "user-1")

    def test_logging_out_one_browser_does_not_invalidate_another_cookie(self):
        with patch.dict(os.environ, {"FLYMAIL_SESSION_SECRET": "test-session-secret-1234567890"}, clear=False):
            other_browser_cookie = create_session_cookie("user-1")
            response = Response()

            clear_session_cookie(response)

            set_cookie = response.headers.get("set-cookie", "")
            self.assertIn(f"{SESSION_COOKIE_NAME}=", set_cookie)
            self.assertIn("Max-Age=0", set_cookie)
            self.assertEqual(parse_session_cookie(other_browser_cookie)["uid"], "user-1")


if __name__ == "__main__":
    unittest.main()
