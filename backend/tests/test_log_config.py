import io
import logging
import unittest
from unittest.mock import patch

import requests
from fastapi.testclient import TestClient

from blog_place_collector import log_config
from blog_place_collector.api import app

FAKE_KEY = "AIzaFAKEKEY1234567890"


def _http_error(status, url, body="", headers=None):
    response = requests.Response()
    response.status_code = status
    response.url = url
    response._content = body.encode("utf-8")
    response.headers.update(headers or {})
    response.request = requests.Request("POST", url).prepare()
    return requests.HTTPError(f"{status} Client Error: Too Many Requests for url: {url}", response=response)


class LogConfigTestCase(unittest.TestCase):
    def setUp(self):
        self.stream = io.StringIO()
        self.handler = logging.StreamHandler(self.stream)
        self.handler.setFormatter(log_config.RedactingFormatter("%(levelname)s %(name)s: %(message)s"))
        self.logger = logging.getLogger(log_config.APP_LOGGER_NAME)
        self.logger.addHandler(self.handler)

    def tearDown(self):
        self.logger.removeHandler(self.handler)

    def test_describe_http_error_strips_query_and_includes_rate_limit_info(self):
        error = _http_error(
            429,
            f"https://generativelanguage.googleapis.com/v1beta/models/m:generateContent?key={FAKE_KEY}",
            body='{"error": {"code": 429, "status": "RESOURCE_EXHAUSTED"}}',
            headers={"Retry-After": "30", "Content-Type": "application/json"},
        )

        summary = log_config.describe_http_error(error)

        self.assertNotIn(FAKE_KEY, summary)
        self.assertNotIn("?", summary.split(" status=")[0])
        self.assertIn("service=gemini", summary)
        self.assertIn("status=429", summary)
        self.assertIn("Retry-After", summary)
        self.assertIn("RESOURCE_EXHAUSTED", summary)

    @patch("blog_place_collector.api.search_collection")
    def test_search_error_log_never_contains_api_key(self, search_collection):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/m:generateContent?key={FAKE_KEY}"
        search_collection.side_effect = _http_error(429, url, body=f"echo {url}")

        with patch.object(log_config.config, "GEMINI_API_KEY", FAKE_KEY):
            response = TestClient(app).post("/api/collections/search", json={"keyword": "부산여행"})

        self.assertEqual(response.status_code, 502)
        output = self.stream.getvalue()
        self.assertIn("status=429", output)
        self.assertIn("Traceback", output)
        self.assertNotIn(FAKE_KEY, output)

    def test_redact_removes_known_secrets_and_auth_headers(self):
        with patch.object(log_config.config, "KAKAO_REST_API_KEY", "kakaosecretvalue"):
            text = log_config.redact("Authorization: KakaoAK kakaosecretvalue and kakaosecretvalue")
        self.assertNotIn("kakaosecretvalue", text)


if __name__ == "__main__":
    unittest.main()
