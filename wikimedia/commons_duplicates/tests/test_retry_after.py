"""Tests for Retry-After parsing, backoff, throttling and the cache.

Every HTTP interaction here is mocked. No request ever reaches Wikimedia Commons.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import requests

import commons_duplicate_finder as finder


class FakeResponse:
    """Minimal stand-in for requests.Response."""

    def __init__(
        self, status_code: int = 200, payload: object | None = None, headers: dict | None = None
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self._payload = payload if payload is not None else {"batchcomplete": True}

    def json(self) -> object:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    """Replays a scripted list of responses or exceptions, recording each call."""

    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = list(outcomes)
        self.headers: dict[str, str] = {}
        self.calls: list[dict] = []

    def get(self, url: str, params: dict, timeout: tuple) -> FakeResponse:
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def build_client(outcomes: list[object], **kwargs) -> tuple[finder.CommonsApiClient, FakeSession, list[float]]:
    session = FakeSession(outcomes)
    slept: list[float] = []
    policy = finder.RetryPolicy(
        max_retries=kwargs.pop("max_retries", 5),
        initial_backoff=kwargs.pop("initial_backoff", 2.0),
        max_backoff=kwargs.pop("max_backoff", 60.0),
        max_retry_after=kwargs.pop("max_retry_after", 300.0),
    )
    client = finder.CommonsApiClient(
        user_agent="test-agent",
        request_delay=kwargs.pop("request_delay", 0.0),
        retry_policy=policy,
        cache=kwargs.pop("cache", None),
        session=session,
        sleep=slept.append,
    )
    return client, session, slept


class ParseRetryAfterTests(unittest.TestCase):
    def test_reads_a_plain_number_of_seconds(self) -> None:
        self.assertEqual(finder.parse_retry_after("12"), 12.0)
        self.assertEqual(finder.parse_retry_after(" 2.5 "), 2.5)

    def test_reads_an_http_date(self) -> None:
        now = datetime(2024, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
        header = (now + timedelta(seconds=30)).strftime("%a, %d %b %Y %H:%M:%S GMT")
        self.assertAlmostEqual(finder.parse_retry_after(header, now=now), 30.0, places=3)

    def test_an_http_date_in_the_past_becomes_zero(self) -> None:
        now = datetime(2024, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
        header = (now - timedelta(seconds=30)).strftime("%a, %d %b %Y %H:%M:%S GMT")
        self.assertEqual(finder.parse_retry_after(header, now=now), 0.0)

    def test_a_negative_number_becomes_zero(self) -> None:
        self.assertEqual(finder.parse_retry_after("-5"), 0.0)

    def test_missing_or_invalid_headers_return_none(self) -> None:
        self.assertIsNone(finder.parse_retry_after(None))
        self.assertIsNone(finder.parse_retry_after(""))
        self.assertIsNone(finder.parse_retry_after("   "))
        self.assertIsNone(finder.parse_retry_after("soon"))


class BackoffDelayTests(unittest.TestCase):
    def test_grows_exponentially_and_stays_bounded(self) -> None:
        with mock.patch.object(finder.random, "uniform", return_value=0.0):
            delays = [finder.backoff_delay(attempt, 2.0, 60.0) for attempt in range(7)]
        self.assertEqual(delays[:5], [2.0, 4.0, 8.0, 16.0, 32.0])
        self.assertTrue(all(delay <= 60.0 for delay in delays))

    def test_adds_a_jitter_on_top_of_the_base_delay(self) -> None:
        with mock.patch.object(finder.random, "uniform", return_value=0.75):
            self.assertEqual(finder.backoff_delay(0, 2.0, 60.0), 2.75)


class RetryLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.object(finder.random, "uniform", return_value=0.0)
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_http_429_waits_for_the_retry_after_header(self) -> None:
        client, session, slept = build_client(
            [FakeResponse(429, headers={"Retry-After": "12"}), FakeResponse(200, {"ok": True})]
        )
        self.assertEqual(client.get({"action": "query"}), {"ok": True})
        self.assertEqual(slept, [12.0])
        self.assertEqual(len(session.calls), 2)

    def test_http_429_without_a_usable_header_falls_back_to_backoff(self) -> None:
        client, _, slept = build_client(
            [FakeResponse(429, headers={"Retry-After": "later"}), FakeResponse(200, {"ok": True})]
        )
        client.get({"action": "query"})
        self.assertEqual(slept, [2.0])

    def test_an_excessive_retry_after_aborts_instead_of_sleeping(self) -> None:
        client, _, slept = build_client(
            [FakeResponse(429, headers={"Retry-After": "9999"})], max_retry_after=300.0
        )
        with self.assertRaises(finder.CommonsApiError):
            client.get({"action": "query"})
        self.assertEqual(slept, [])

    def test_temporary_server_errors_are_retried_with_backoff(self) -> None:
        client, session, slept = build_client(
            [FakeResponse(503), FakeResponse(500), FakeResponse(200, {"ok": True})]
        )
        self.assertEqual(client.get({"action": "query"}), {"ok": True})
        self.assertEqual(slept, [2.0, 4.0])
        self.assertEqual(len(session.calls), 3)

    def test_timeouts_and_connection_resets_are_retried(self) -> None:
        client, session, slept = build_client(
            [
                requests.ConnectTimeout("connect timed out"),
                requests.ConnectionError("connection reset by peer"),
                FakeResponse(200, {"ok": True}),
            ]
        )
        self.assertEqual(client.get({"action": "query"}), {"ok": True})
        self.assertEqual(len(session.calls), 3)
        self.assertEqual(slept, [2.0, 4.0])

    def test_an_incomplete_response_body_is_retried(self) -> None:
        client, _, _ = build_client(
            [FakeResponse(200, ValueError("unterminated JSON")), FakeResponse(200, {"ok": True})]
        )
        self.assertEqual(client.get({"action": "query"}), {"ok": True})

    def test_permanent_client_errors_are_not_retried(self) -> None:
        for status in (400, 403, 404):
            with self.subTest(status=status):
                client, session, slept = build_client([FakeResponse(status)])
                with self.assertRaises(finder.CommonsApiError):
                    client.get({"action": "query"})
                self.assertEqual(len(session.calls), 1)
                self.assertEqual(slept, [])

    def test_retries_stop_at_the_configured_maximum(self) -> None:
        client, session, _ = build_client([FakeResponse(503)] * 10, max_retries=3)
        with self.assertRaises(finder.CommonsApiError):
            client.get({"action": "query"})
        self.assertEqual(len(session.calls), 4)

    def test_an_api_error_object_is_raised_and_not_retried(self) -> None:
        client, session, _ = build_client(
            [FakeResponse(200, {"error": {"code": "baduser", "info": "Invalid user."}})]
        )
        with self.assertRaisesRegex(finder.CommonsApiError, "baduser"):
            client.get({"action": "query"})
        self.assertEqual(len(session.calls), 1)


class ThrottlingTests(unittest.TestCase):
    def test_the_first_request_is_not_delayed_but_later_ones_are(self) -> None:
        client, _, slept = build_client(
            [FakeResponse(200, {"ok": 1}), FakeResponse(200, {"ok": 2})], request_delay=1.5
        )
        with mock.patch.object(finder.time, "monotonic", side_effect=[0.0, 0.1, 0.1]):
            client.get({"action": "query", "a": "1"})
            client.get({"action": "query", "a": "2"})
        self.assertEqual(len(slept), 1)
        self.assertAlmostEqual(slept[0], 1.4, places=6)

    def test_every_attempt_counts_as_a_request(self) -> None:
        client, _, _ = build_client([FakeResponse(503), FakeResponse(200, {"ok": True})])
        with mock.patch.object(finder.random, "uniform", return_value=0.0):
            client.get({"action": "query"})
        self.assertEqual(client.request_count, 2)

    def test_the_user_agent_header_is_set_on_the_session(self) -> None:
        client, session, _ = build_client([])
        self.assertEqual(session.headers["User-Agent"], "test-agent")

    def test_the_user_agent_encodes_the_requesting_username(self) -> None:
        agent = finder.build_user_agent("Example User")
        self.assertIn("CommonsExifDuplicateFinder/", agent)
        self.assertIn("https://commons.wikimedia.org/wiki/User:Example_User", agent)
        self.assertIn("Zag%C5%82oba", finder.build_user_agent("Zagłoba"))


class ContinuationTests(unittest.TestCase):
    def test_continuation_parameters_are_carried_into_the_next_request(self) -> None:
        client, session, _ = build_client(
            [
                FakeResponse(200, {"query": {"allimages": []}, "continue": {"aicontinue": "X|1"}}),
                FakeResponse(200, {"query": {"allimages": []}}),
            ]
        )
        pages = list(client.iterate_query({"action": "query", "list": "allimages"}))
        self.assertEqual(len(pages), 2)
        self.assertNotIn("aicontinue", session.calls[0]["params"])
        self.assertEqual(session.calls[1]["params"]["aicontinue"], "X|1")


class CacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp())

    def test_a_successful_response_is_replayed_without_a_second_request(self) -> None:
        cache = finder.ResponseCache(self.directory)
        params = {"action": "query", "list": "allimages"}
        first, session, _ = build_client([FakeResponse(200, {"ok": True})], cache=cache)
        self.assertEqual(first.get(params), {"ok": True})

        second, other_session, _ = build_client([], cache=cache)
        self.assertEqual(second.get(params), {"ok": True})
        self.assertEqual(other_session.calls, [])
        self.assertEqual(second.request_count, 0)
        self.assertEqual(second.cache_hits, 1)

    def test_different_parameters_use_different_cache_entries(self) -> None:
        cache = finder.ResponseCache(self.directory)
        self.assertNotEqual(cache.cache_key({"a": "1"}), cache.cache_key({"a": "2"}))
        self.assertEqual(cache.cache_key({"a": "1", "b": "2"}), cache.cache_key({"b": "2", "a": "1"}))

    def test_failed_requests_are_never_stored(self) -> None:
        cache = finder.ResponseCache(self.directory)
        params = {"action": "query"}
        client, _, _ = build_client([FakeResponse(404)], cache=cache)
        with self.assertRaises(finder.CommonsApiError):
            client.get(params)
        self.assertIsNone(cache.read(params))

    def test_an_unreadable_cache_entry_is_ignored(self) -> None:
        cache = finder.ResponseCache(self.directory)
        params = {"action": "query"}
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / f"{cache.cache_key(params)}.json").write_text("{ not json", encoding="utf-8")
        self.assertIsNone(cache.read(params))


if __name__ == "__main__":
    unittest.main()
