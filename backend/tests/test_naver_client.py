import logging
import threading
import time
import unittest
from unittest.mock import patch

import requests

from blog_place_collector import service
from blog_place_collector.clients import naver


def _response(status, json_body=None, headers=None):
    response = requests.Response()
    response.status_code = status
    response.url = "https://openapi.naver.com/v1/search/local.json"
    response.headers.update(headers or {})
    response._content = (b'{"items": []}' if json_body is None else json_body.encode("utf-8"))
    response.request = requests.Request("GET", response.url).prepare()
    return response


class FakeClock:
    def __init__(self):
        self.now = 100.0
        self.lock = threading.Lock()

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        with self.lock:
            self.now += seconds


class RateLimiterTestCase(unittest.TestCase):
    def test_spaces_calls_by_interval(self):
        clock = FakeClock()
        limiter = naver.RateLimiter(8, clock=clock, sleep=lambda s: None)
        # 시계를 멈춘 채 연속 호출하면 다음 허용 시각이 1/8초씩 밀려야 한다.
        for _ in range(5):
            limiter.acquire()
        self.assertAlmostEqual(limiter._next_allowed, 100.0 + 5 * 0.125)

    def test_no_wait_when_idle(self):
        clock = FakeClock()
        sleeps = []
        limiter = naver.RateLimiter(8, clock=clock, sleep=sleeps.append)
        limiter.acquire()
        clock.now += 1.0
        limiter.acquire()
        self.assertEqual(sleeps, [])

    def test_real_threads_respect_rate(self):
        limiter = naver.RateLimiter(20)
        stamps = []
        lock = threading.Lock()

        def worker():
            for _ in range(3):
                limiter.acquire()
                with lock:
                    stamps.append(time.monotonic())

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        stamps.sort()
        self.assertEqual(len(stamps), 24)
        # 24건을 20건/초로 → 처음과 끝 사이가 최소 23 * 0.05초.
        self.assertGreaterEqual(stamps[-1] - stamps[0], 23 * 0.05 - 0.02)
        # 어떤 1초 창에도 20건을 넘지 않는다(간격이 정확히 1/20초라 21번째가 딱 +1.0초에
        # 걸리므로, 스탬프 지터를 감안해 0.95초 창으로 본다).
        for i, s in enumerate(stamps):
            in_window = sum(1 for t in stamps[i:] if t - s < 0.95)
            self.assertLessEqual(in_window, 20)

    def test_penalize_pushes_next_slot(self):
        clock = FakeClock()
        limiter = naver.RateLimiter(8, clock=clock, sleep=lambda s: None)
        limiter.penalize(1.0)
        self.assertAlmostEqual(limiter._next_allowed, 101.0)


class RetryTestCase(unittest.TestCase):
    def setUp(self):
        # 재시도 대기는 limiter.penalize → 다음 acquire의 sleep으로 걸리므로 sleep을 기록만 한다.
        self.sleeps = []
        limiter = naver.RateLimiter(1000, sleep=self.sleeps.append)
        patcher = patch.object(naver, "_rate_limiter", limiter)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_retries_after_429_then_succeeds(self):
        ok = _response(200, '{"items": [{"title": "<b>A</b>", "category": "c", "roadAddress": "r", "address": "a"}]}')
        limited = _response(429, headers={"Retry-After": "0.5"})
        with patch.object(naver.requests, "get", side_effect=[limited, limited, ok]) as get:
            with self.assertLogs("blog_place_collector.clients.naver", level=logging.WARNING) as logs:
                hits = naver.naver_local_search("A")
        self.assertEqual(get.call_count, 3)
        self.assertEqual(hits[0]["title"], "A")
        self.assertEqual(len(logs.records), 2)
        self.assertIn("429", logs.output[0])
        # Retry-After 0.5초만큼 다음 호출이 밀려야 한다.
        self.assertTrue(any(s >= 0.45 for s in self.sleeps), self.sleeps)

    def test_gives_up_after_max_retries(self):
        limited = _response(429)
        with patch.object(naver.requests, "get", return_value=limited) as get:
            with self.assertLogs("blog_place_collector.clients.naver", level=logging.WARNING):
                with self.assertRaises(requests.HTTPError):
                    naver.naver_blog_search("성수동 카페", target_count=10)
        self.assertEqual(get.call_count, naver.MAX_RATE_LIMIT_RETRIES + 1)

    def test_non_429_error_is_not_retried(self):
        with patch.object(naver.requests, "get", return_value=_response(500)) as get:
            with self.assertRaises(requests.HTTPError):
                naver.naver_local_search("A")
        self.assertEqual(get.call_count, 1)

    def test_wait_from_rate_limit_reset_header(self):
        reset_ms = (time.time() + 0.6) * 1000
        wait = naver._retry_wait_seconds(_response(429, headers={"x-rate-limit-reset": str(int(reset_ms))}))
        self.assertTrue(naver.MIN_RETRY_WAIT <= wait <= 0.7, wait)

    def test_wait_defaults_and_is_clamped(self):
        self.assertEqual(naver._retry_wait_seconds(_response(429)), naver.DEFAULT_RETRY_WAIT)
        self.assertEqual(
            naver._retry_wait_seconds(_response(429, headers={"Retry-After": "60"})), naver.MAX_RETRY_WAIT
        )

    def test_blog_search_passes_start(self):
        body = '{"items": [' + ",".join('{"title": "t", "description": "d", "link": "l"}' for _ in range(100)) + "]}"
        with patch.object(naver.requests, "get", return_value=_response(200, body)) as get:
            posts = naver.naver_blog_search("k", target_count=200, start=101)
        self.assertEqual(len(posts), 200)
        starts = [call.kwargs["params"]["start"] for call in get.call_args_list]
        self.assertEqual(starts, [101, 201])


class IncrementalCollectionTestCase(unittest.TestCase):
    def test_tiers_fetch_only_new_posts(self):
        all_posts = [{"title": f"t{i}", "contents": "c", "url": f"u{i}"} for i in range(300)]
        calls = []

        def fake_blog_search(keyword, target_count, start=1):
            calls.append((start, target_count))
            return all_posts[start - 1 : start - 1 + target_count]

        with patch.object(service, "naver_blog_search", side_effect=fake_blog_search), patch.object(
            service, "extract_regions", return_value=[]
        ), patch.object(service, "extract_business_names", return_value=[]), patch.object(
            service, "_verified_candidates", return_value=[]
        ):
            post_count, posts, _candidates, _regions = service._collect_candidates("k")

        self.assertEqual(calls, [(1, 100), (101, 100), (201, 100)])
        self.assertEqual(post_count, 300)
        self.assertEqual(posts, all_posts)

    def test_stops_requesting_when_exhausted(self):
        all_posts = [{"title": f"t{i}", "contents": "c", "url": f"u{i}"} for i in range(60)]
        calls = []

        def fake_blog_search(keyword, target_count, start=1):
            calls.append((start, target_count))
            return all_posts[start - 1 : start - 1 + target_count]

        with patch.object(service, "naver_blog_search", side_effect=fake_blog_search), patch.object(
            service, "extract_regions", return_value=[]
        ), patch.object(service, "extract_business_names", return_value=[]), patch.object(
            service, "_verified_candidates", return_value=[]
        ):
            post_count, posts, _c, _r = service._collect_candidates("k")

        self.assertEqual(calls, [(1, 100)])
        self.assertEqual(post_count, 60)


class VerifyCacheTestCase(unittest.TestCase):
    def test_names_resolved_in_earlier_tier_are_not_resolved_again(self):
        resolved = []

        def fake_resolve(name, regions, radius, failures=None):
            resolved.append(name)
            return {"key": name, "display1": name, "x": 0.0, "y": 0.0}

        post = {"title": "t", "url": "u"}
        cache = {}
        with patch.object(service, "_resolve_place", side_effect=fake_resolve):
            first = service._verified_candidates(
                [{"name": "A", "post": post}, {"name": "B", "post": post}], [], 5000, cache=cache
            )
            second = service._verified_candidates(
                [{"name": "A", "post": post}, {"name": "B", "post": post}, {"name": "A", "post": post},
                 {"name": "C", "post": post}],
                [],
                5000,
                cache=cache,
            )

        self.assertEqual(sorted(resolved), ["A", "B", "C"])
        self.assertEqual(len(first), 2)
        self.assertEqual([(c["name"], c["mention_count"]) for c in second], [("A", 2), ("B", 1), ("C", 1)])


if __name__ == "__main__":
    unittest.main()
