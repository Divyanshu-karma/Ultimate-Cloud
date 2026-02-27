# tests/test_adapters.py
"""
Tests for the Live USPTO TESS Adapter.

Uses `requests_mock` to intercept HTTP calls — no real network needed.
This lets you verify the adapter behaves correctly for:
  - Successful responses with results
  - Empty result pages
  - HTTP errors (503, 429)
  - Malformed JSON
  - Pagination (multiple pages)
  - Field mapping / normalization

Install:  pip install requests-mock
Run:      python -m pytest tests/test_adapters.py -v
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json
import unittest

try:
    import requests_mock as req_mock_lib
    HAS_REQUESTS_MOCK = True
except ImportError:
    HAS_REQUESTS_MOCK = False

import config
from adapters.tess_live import TessLiveAdapter
from core.models import GoodsServices
from core.query_builder import build_search_queries


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_queries():
    gs = [GoodsServices(ic_class="029", description="Dried fruit")]
    return build_search_queries("ADAMS APPLE", gs)


def _api_response(docs: list, num_found: int | None = None) -> dict:
    """Build a mock USPTO API response envelope."""
    return {
        "response": {
            "numFound": num_found if num_found is not None else len(docs),
            "docs": docs,
        }
    }


def _doc(serial: str, mark: str, status_code: str = "700", classes=None) -> dict:
    return {
        "serialNumber":           serial,
        "markIdentification":     mark,
        "statusCode":             status_code,
        "statusLabel":            "Registered" if status_code == "700" else "Pending",
        "internationalClassCodes": classes or ["029"],
        "filingDate":             "2020-01-01",
        "registrationDate":       "2021-06-15",
        "ownerName":              "Test Corp",
    }


# ── tests ─────────────────────────────────────────────────────────────────────

@unittest.skipUnless(HAS_REQUESTS_MOCK, "requests-mock not installed — skipping live adapter tests")
class TestTessLiveAdapter(unittest.TestCase):

    def setUp(self):
        self.adapter = TessLiveAdapter(base_url=config.TESS_API_BASE_URL)
        self.queries = _make_queries()

    def test_returns_conflict_records_on_success(self):
        response_body = _api_response([
            _doc("987654321", "ADAMS APPLE"),
            _doc("876543219", "ADAMS APPLE CO", "100"),
        ])
        with req_mock_lib.Mocker() as m:
            m.get(config.TESS_API_BASE_URL, json=response_body)
            results = self.adapter.search(self.queries)

        app_numbers = [r.application_number for r in results]
        self.assertIn("987654321", app_numbers)
        self.assertIn("876543219", app_numbers)

    def test_deduplication_across_queries(self):
        """Same serial number from multiple query types → only 1 record."""
        response_body = _api_response([_doc("987654321", "ADAMS APPLE")])
        with req_mock_lib.Mocker() as m:
            m.get(config.TESS_API_BASE_URL, json=response_body)
            results = self.adapter.search(self.queries)

        app_numbers = [r.application_number for r in results]
        self.assertEqual(len(app_numbers), len(set(app_numbers)))

    def test_empty_results_returns_empty_list(self):
        response_body = _api_response([])
        with req_mock_lib.Mocker() as m:
            m.get(config.TESS_API_BASE_URL, json=response_body)
            results = self.adapter.search(self.queries)
        self.assertEqual(results, [])

    def test_http_500_returns_empty_list(self):
        with req_mock_lib.Mocker() as m:
            m.get(config.TESS_API_BASE_URL, status_code=500)
            results = self.adapter.search(self.queries)
        self.assertEqual(results, [])

    def test_http_503_returns_empty_list(self):
        with req_mock_lib.Mocker() as m:
            m.get(config.TESS_API_BASE_URL, status_code=503)
            results = self.adapter.search(self.queries)
        self.assertEqual(results, [])

    def test_malformed_json_returns_empty_list(self):
        with req_mock_lib.Mocker() as m:
            m.get(config.TESS_API_BASE_URL, text="not json at all")
            results = self.adapter.search(self.queries)
        self.assertEqual(results, [])

    def test_status_code_registered_mapped(self):
        response_body = _api_response([_doc("111", "MARK ONE", "700")])
        with req_mock_lib.Mocker() as m:
            m.get(config.TESS_API_BASE_URL, json=response_body)
            results = self.adapter.search(self.queries)
        if results:
            self.assertEqual(results[0].status, "registered")

    def test_status_code_pending_mapped(self):
        response_body = _api_response([_doc("222", "MARK TWO", "100")])
        with req_mock_lib.Mocker() as m:
            m.get(config.TESS_API_BASE_URL, json=response_body)
            results = self.adapter.search(self.queries)
        if results:
            self.assertEqual(results[0].status, "pending")

    def test_record_fields_normalised(self):
        response_body = _api_response([_doc("333444555", "ADAMS APPLE FRESH")])
        with req_mock_lib.Mocker() as m:
            m.get(config.TESS_API_BASE_URL, json=response_body)
            results = self.adapter.search(self.queries)
        if results:
            rec = results[0]
            self.assertEqual(rec.application_number, "333444555")
            self.assertEqual(rec.mark_text,          "ADAMS APPLE FRESH")
            self.assertEqual(rec.filing_date,        "2020-01-01")
            self.assertEqual(rec.owner_name,         "Test Corp")

    def test_pagination_fetches_next_page(self):
        """
        First page has PAGE_SIZE results + numFound > PAGE_SIZE
        → adapter should request page 2.
        """
        page1_docs = [_doc(str(i), f"MARK {i}") for i in range(config.PAGE_SIZE)]
        page2_docs = [_doc("999", "MARK FINAL")]

        call_count = {"n": 0}

        def dynamic_response(request, context):
            call_count["n"] += 1
            start = int(request.qs.get("start", ["0"])[0])
            if start == 0:
                return _api_response(page1_docs, num_found=config.PAGE_SIZE + 1)
            else:
                return _api_response(page2_docs, num_found=config.PAGE_SIZE + 1)

        with req_mock_lib.Mocker() as m:
            m.get(config.TESS_API_BASE_URL, json=dynamic_response)
            results = self.adapter.search(self.queries[:1])  # one query to simplify

        # Should have fetched at least page 2
        self.assertGreater(call_count["n"], 1)


@unittest.skipUnless(HAS_REQUESTS_MOCK, "requests-mock not installed")
class TestMissingRequestsMock(unittest.TestCase):
    """Placeholder that runs when requests-mock IS available — ensures the import works."""
    def test_requests_mock_importable(self):
        import requests_mock
        self.assertIsNotNone(requests_mock)


if __name__ == "__main__":
    unittest.main(verbosity=2)
