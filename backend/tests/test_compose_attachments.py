import types
import unittest
from unittest.mock import patch

from routes import compose


class ComposeAttachmentValidationTest(unittest.TestCase):
    def test_prepare_attachment_paths_validates_ownership_then_provider_limit(self):
        account = types.SimpleNamespace(provider="gmail")
        with (
            patch("routes.compose.validate_attachment_paths", return_value=["/safe/a.txt"]) as validate,
            patch("routes.compose.check_attachment_total_size") as check_size,
        ):
            paths = compose.prepare_attachment_paths("user-1", account, ["/input/a.txt"])

        self.assertEqual(paths, ["/safe/a.txt"])
        validate.assert_called_once_with("user-1", ["/input/a.txt"])
        check_size.assert_called_once_with(["/safe/a.txt"], "gmail")


if __name__ == "__main__":
    unittest.main()
