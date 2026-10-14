import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from blog_place_collector.api import app


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_health_does_not_expose_secrets(self):
        response = self.client.get("/api/health")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(
            set(body["credentials"]),
            {"gemini", "kakao_rest", "naver_local"},
        )
        self.assertTrue(all(isinstance(value, bool) for value in body["credentials"].values()))

    def test_frontend_is_served_from_backend(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Placepick", response.text)
        self.assertIn('src="/assets/', response.text)

    def test_search_page_is_served_from_backend(self):
        response = self.client.get("/search")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Placepick", response.text)
        self.assertIn('src="/assets/', response.text)

    @patch("blog_place_collector.api.preview_collection")
    def test_preview_returns_candidates(self, preview_collection):
        preview_collection.return_value = {
            "post_count": 7,
            "candidates": [
                {
                    "name": "테스트 카페",
                    "mention_count": 3,
                    "sources": [],
                    "place": None,
                }
            ],
        }

        response = self.client.post(
            "/api/collections/preview",
            json={"keyword": "성수동 카페"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["post_count"], 7)
        self.assertEqual(response.json()["query"], "성수동 카페")

    @patch("blog_place_collector.api.search_collection")
    def test_search_returns_overview_mode(self, search_collection):
        search_collection.return_value = {
            "mode": "overview",
            "post_count": 35,
            "regions": [
                {
                    "name": "서면",
                    "mention_count": 12,
                    "categories": [{"name": "카페", "mention_count": 5}],
                }
            ],
        }

        response = self.client.post(
            "/api/collections/search",
            json={"keyword": "부산여행"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["mode"], "overview")
        self.assertEqual(response.json()["post_count"], 35)
        self.assertEqual(response.json()["query"], "부산여행")
        self.assertEqual(response.json()["regions"][0]["name"], "서면")

    @patch("blog_place_collector.api.search_collection")
    def test_search_returns_results_mode(self, search_collection):
        search_collection.return_value = {
            "mode": "results",
            "post_count": 35,
            "candidates": [
                {"name": "테스트 카페", "mention_count": 3, "sources": [], "place": None}
            ],
        }

        response = self.client.post(
            "/api/collections/search",
            json={"keyword": "성수동 브런치"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["mode"], "results")
        self.assertEqual(response.json()["candidates"][0]["name"], "테스트 카페")

    def test_preview_validates_keyword_length(self):
        response = self.client.post(
            "/api/collections/preview",
            json={"keyword": "가"},
        )

        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
