import base64
import json
import unittest

from app.converter import (
    AUTH_KEY,
    auth_file_to_token,
    auth_file_to_tokens,
    cliproxy_filename,
    decode_jwt_payload,
    load_sso_list,
    merge_auth_payload,
    token_to_auth_entry,
    token_to_cliproxy_entry,
)


def fake_jwt(payload):
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJub25lIn0.{encoded}.signature"


class ConverterTests(unittest.TestCase):
    def test_load_sso_list_supports_comments_and_email_formats(self):
        values = load_sso_list("# comment\nfoo@example.com----cookie-a\nfoo@example.com----password----cookie-b\ncookie-c")
        self.assertEqual(values, [("cookie-a", "foo@example.com"), ("cookie-b", "foo@example.com"), ("cookie-c", "")])

    def test_decode_jwt_payload(self):
        token = fake_jwt({"sub": "user-1", "email": "u@example.com"})
        self.assertEqual(decode_jwt_payload(token)["sub"], "user-1")
        self.assertEqual(decode_jwt_payload("opaque"), {})

    def test_token_formats(self):
        token = {
            "access_token": fake_jwt({"sub": "user-1", "iat": 100, "exp": 200}),
            "refresh_token": "refresh",
            "expires_in": 100,
            "id_token": fake_jwt({"email": "u@example.com"}),
        }
        key, grok = token_to_auth_entry(token, email="u@example.com")
        filename, cliproxy = token_to_cliproxy_entry(token, email="u@example.com")
        self.assertEqual(key, AUTH_KEY)
        self.assertEqual(grok["user_id"], "user-1")
        self.assertEqual(filename, "xai-u@example.com.json")
        self.assertEqual(cliproxy["type"], "xai")
        self.assertEqual(cliproxy["sub"], "user-1")

    def test_auth_file_to_token_supports_nested_and_flat(self):
        token = fake_jwt({"sub": "user-1", "iat": 100, "exp": 200})
        nested = {"https://auth.x.ai::client": {"key": token, "refresh_token": "refresh", "email": "u@example.com"}}
        flat = {"type": "xai", "access_token": token, "refresh_token": "refresh", "email": "u@example.com"}
        self.assertEqual(auth_file_to_token(nested)[1], "u@example.com")
        self.assertEqual(auth_file_to_token(flat)[1], "u@example.com")

    def test_nested_auth_document_keeps_all_accounts(self):
        first = fake_jwt({"sub": "user-1"})
        second = fake_jwt({"sub": "user-2"})
        nested = {
            "issuer::user-1": {"key": first, "refresh_token": "r1"},
            "issuer::user-2": {"key": second, "refresh_token": "r2"},
        }
        entries = auth_file_to_tokens(nested)
        self.assertEqual(len(entries), 2)
        self.assertEqual({entry[0]["access_token"] for entry in entries}, {first, second})

    def test_merge_uses_user_id_to_avoid_overwrite(self):
        _, first = token_to_auth_entry({"access_token": fake_jwt({"sub": "a"})})
        _, second = token_to_auth_entry({"access_token": fake_jwt({"sub": "b"})})
        merged = merge_auth_payload({}, first)
        merged = merge_auth_payload(merged, second)
        self.assertEqual(len(merged), 2)
        self.assertIn(f"{AUTH_KEY}::a", merged)
        self.assertIn(f"{AUTH_KEY}::b", merged)

    def test_merge_keeps_opaque_tokens_separate(self):
        _, first = token_to_auth_entry({"access_token": "opaque-a"})
        _, second = token_to_auth_entry({"access_token": "opaque-b"})
        merged = merge_auth_payload({}, first)
        merged = merge_auth_payload(merged, second)
        self.assertEqual(len(merged), 2)

    def test_filename_is_safe(self):
        self.assertEqual(cliproxy_filename("../a:b"), "xai-_a_b.json")
        self.assertTrue(cliproxy_filename().startswith("xai-anon_"))


if __name__ == "__main__":
    unittest.main()
