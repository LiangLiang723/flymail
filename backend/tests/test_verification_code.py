import importlib
import unittest


class VerificationCodeTests(unittest.TestCase):
    def extract(self, *, subject="", body_text="", body_html=""):
        try:
            module = importlib.import_module("services.verification_code")
        except ModuleNotFoundError:
            self.fail("services.verification_code is not implemented")
        extractor = getattr(module, "extract_verification_code", None)
        self.assertTrue(callable(extractor), "extract_verification_code is not implemented")
        return extractor(subject=subject, body_text=body_text, body_html=body_html)

    def test_extracts_code_before_chinese_keyword_in_subject(self):
        self.assertEqual(
            self.extract(subject="24681357 是您的验证码【请注意，请勿泄露】"),
            "24681357",
        )

    def test_extracts_atlassian_chinese_verification_code_subject(self):
        self.assertEqual(
            self.extract(
                subject="24681357 是您的验证代码【请注意，请勿泄露】"
            ),
            "24681357",
        )

    def test_extracts_atlassian_identity_verification_body(self):
        self.assertEqual(
            self.extract(
                body_text=(
                    "测试用户，您好。\n"
                    "作为额外的安全防护层，您需要验证自己的身份。\n"
                    "请输入以下代码：\n\n24681357"
                )
            ),
            "24681357",
        )

    def test_extracts_and_normalizes_grouped_code_after_keyword(self):
        self.assertEqual(
            self.extract(subject="登录验证", body_text="您的验证码是 123-456，请在 10 分钟内使用。"),
            "123456",
        )

    def test_extracts_code_from_plain_text_body(self):
        self.assertEqual(
            self.extract(body_text="Your verification code is 654321. Do not share it."),
            "654321",
        )

    def test_extracts_code_from_html_body(self):
        self.assertEqual(
            self.extract(body_html="<html><body><p>Security code: <strong>778899</strong></p></body></html>"),
            "778899",
        )

    def test_explicit_english_code_contexts_still_extract(self):
        self.assertEqual(self.extract(body_text="Your verification code is 654321."), "654321")
        self.assertEqual(self.extract(body_text="Security code: 778899"), "778899")
        self.assertEqual(self.extract(body_text="OTP 445566"), "445566")
        self.assertEqual(self.extract(body_text="Use passcode 334455 to sign in."), "334455")

    def test_chinese_identity_verification_requires_code_prompt(self):
        self.assertEqual(
            self.extract(body_text="为了验证自己的身份，请检查请求 24681357 的状态。"),
            "",
        )
        self.assertEqual(
            self.extract(
                body_text=(
                    "作为额外的安全防护层，您需要验证自己的身份。\n"
                    "请输入以下代码：\n\n24681357"
                )
            ),
            "24681357",
        )

    def test_subject_has_priority_over_body(self):
        self.assertEqual(
            self.extract(
                subject="验证码：112233",
                body_text="Your verification code is 445566.",
            ),
            "112233",
        )

    def test_ignores_order_number_without_verification_context(self):
        self.assertEqual(self.extract(subject="订单 123456 已发货"), "")

    def test_ignores_jira_ticket_number_near_bare_verification(self):
        samples = (
            "Verification details for request IH831-4575 were updated.",
            "The verification workflow references IH831-4303 in this notification.",
            "Verification status changed for IH831-4590.",
        )
        for body_text in samples:
            with self.subTest(body_text=body_text):
                self.assertEqual(self.extract(body_text=body_text), "")

    def test_ignores_bare_verification_request_numbers(self):
        self.assertEqual(
            self.extract(body_text="Your verification request 123456 is being processed."),
            "",
        )
        self.assertEqual(
            self.extract(body_text="Verification completed for request 654321."),
            "",
        )

    def test_ignores_date_and_order_number_without_verification_context(self):
        self.assertEqual(
            self.extract(body_text="日期 2026-08-11，订单号 123456，物流单号 998877。"),
            "",
        )

    def test_prefers_code_nearest_verification_context_over_order_number(self):
        self.assertEqual(
            self.extract(body_text="订单号 123456，您的验证码是 654321，请勿转发。"),
            "654321",
        )

    def test_ignores_date_near_verification_word(self):
        self.assertEqual(
            self.extract(body_text="Your verification request expires on 2026-08-11."),
            "",
        )

    def test_ignores_phone_fragment_near_verification_word(self):
        self.assertEqual(
            self.extract(body_text="Verification support phone: 400-123-4567. Contact us for help."),
            "",
        )

    def test_message_schema_exposes_verification_code(self):
        from schemas import MessageItem

        self.assertIn("verification_code", MessageItem.model_fields)
        field = MessageItem.model_fields["verification_code"]
        self.assertEqual(field.default, "")


if __name__ == "__main__":
    unittest.main()
