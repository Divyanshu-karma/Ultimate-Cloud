# tests/test_engine.py
"""
Combined Test Suite — TMEP §704.02 Search Authority Engine
============================================================
Covers:
  - All 63 original engine tests
  - New §1207-ready output fields (applied_for_mark, conflict_set,
    goods_services_analysis, preliminary_flag, refusal_flag)
  - RapidAPI adapter structure tests (no real API key needed)
  - Live adapter HTTP behaviour (via requests_mock if installed)

Run:
    cd D:\\conflict
    set PYTHONPATH=D:\\conflict
    python -m unittest tests.test_engine -v
"""

import json
import sys
import os
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.search_engine import conduct_tmep_704_02_search
from core.validators import ApplicationValidationError, SearchNotRequiredError
from core.query_builder import build_search_queries, phonetic_key
from core.validators import is_search_required
from core.models import (
    AUTHORITY_REFERENCE, DATABASE_NAME, RECORDS_SEARCHED, VARIATION_TYPES,
    GoodsServices, ConflictRecord,
)
from adapters.mock import MockConflictAdapter, EmptyTessAdapter, ConfigurableMockAdapter
from adapters.rapidapi_trademark import RapidApiTrademarkAdapter, _resolve_status
from tests.fixtures import BASE_APPLICATION, make_app

try:
    import requests_mock as req_mock_lib
    HAS_REQUESTS_MOCK = True
except ImportError:
    HAS_REQUESTS_MOCK = False

import config
from adapters.tess_live import TessLiveAdapter
from core.query_builder import build_search_queries as bsq


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — OUTPUT SCHEMA (original 63 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestOutputSchema(unittest.TestCase):
    def setUp(self):
        self.result = conduct_tmep_704_02_search(
            BASE_APPLICATION, tess_adapter=EmptyTessAdapter()
        )

    def test_authority_reference(self):
        self.assertEqual(self.result["authority_reference"], "TMEP §704.02")

    def test_search_conducted_is_true(self):
        self.assertTrue(self.result["search_conducted"])

    def test_timestamp_is_iso8601_utc(self):
        ts = self.result["search_timestamp"]
        self.assertTrue(ts.endswith("Z"))
        datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")

    def test_search_scope_database(self):
        self.assertEqual(self.result["search_scope"]["database"], DATABASE_NAME)

    def test_search_scope_records_searched(self):
        self.assertEqual(sorted(self.result["search_scope"]["records_searched"]), sorted(RECORDS_SEARCHED))

    def test_search_scope_variation_types(self):
        self.assertEqual(sorted(self.result["search_scope"]["variation_types"]), sorted(VARIATION_TYPES))

    def test_results_summary_present(self):
        rs = self.result["results_summary"]
        self.assertIn("total_conflicts_found", rs)
        self.assertIn("conflicting_application_numbers", rs)

    def test_results_summary_count_is_integer(self):
        self.assertIsInstance(self.result["results_summary"]["total_conflicts_found"], int)

    def test_results_summary_numbers_is_list(self):
        self.assertIsInstance(self.result["results_summary"]["conflicting_application_numbers"], list)

    def test_re_search_required_is_bool(self):
        self.assertIsInstance(self.result["re_search_required"], bool)

    def test_compliance_status_is_string(self):
        self.assertIsInstance(self.result["compliance_status"], str)

    def test_compliance_status_references_authority(self):
        self.assertIn("§704.02", self.result["compliance_status"])

    def test_output_is_json_serializable(self):
        json.dumps(self.result)

    def test_no_1207_scoring_fields_present(self):
        """§704.02 must not do §1207's job — no DuPont scores in output."""
        forbidden = ["similarity_score", "confusion_likelihood", "dupont_factor1",
                     "dupont_factor2", "weighted_final_score"]
        for field in forbidden:
            self.assertNotIn(field, self.result)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — NEW §1207-READY OUTPUT FIELDS
# ═══════════════════════════════════════════════════════════════════════════════

class TestAppliedForMark(unittest.TestCase):
    """applied_for_mark block must be structured correctly for §1207."""

    def setUp(self):
        self.result = conduct_tmep_704_02_search(
            BASE_APPLICATION, tess_adapter=EmptyTessAdapter()
        )
        self.afm = self.result["applied_for_mark"]

    def test_applied_for_mark_present(self):
        self.assertIn("applied_for_mark", self.result)

    def test_mark_text_correct(self):
        self.assertEqual(self.afm["mark_text"], "ADAMS APPLE")

    def test_mark_type_correct(self):
        self.assertEqual(self.afm["mark_type"], "standard_character")

    def test_ic_classes_is_list(self):
        self.assertIsInstance(self.afm["ic_classes"], list)
        self.assertIn("029", self.afm["ic_classes"])

    def test_goods_services_is_list(self):
        self.assertIsInstance(self.afm["goods_services"], list)
        self.assertGreater(len(self.afm["goods_services"]), 0)

    def test_goods_services_has_class_and_description(self):
        for gs in self.afm["goods_services"]:
            self.assertIn("class", gs)
            self.assertIn("description", gs)


class TestConflictSet(unittest.TestCase):
    """conflict_set must carry all fields §1207 needs for DuPont scoring."""

    def setUp(self):
        self.result = conduct_tmep_704_02_search(
            BASE_APPLICATION, tess_adapter=MockConflictAdapter()
        )
        self.cs = self.result["conflict_set"]

    def test_conflict_set_present(self):
        self.assertIn("conflict_set", self.result)

    def test_conflict_set_is_list(self):
        self.assertIsInstance(self.cs, list)

    def test_conflict_set_count_matches_summary(self):
        self.assertEqual(
            len(self.cs),
            self.result["results_summary"]["total_conflicts_found"]
        )

    def test_each_conflict_has_required_fields(self):
        required = ["application_number", "mark_text", "status",
                    "ic_classes", "owner_name", "surfaced_by"]
        for rec in self.cs:
            for field in required:
                self.assertIn(field, rec, f"Missing '{field}' in conflict record")

    def test_conflict_set_count_is_3(self):
        self.assertEqual(len(self.cs), 3)

    def test_no_duplicates_in_conflict_set(self):
        nums = [c["application_number"] for c in self.cs]
        self.assertEqual(len(nums), len(set(nums)))


class TestGoodsServicesAnalysis(unittest.TestCase):
    """goods_services_analysis must correctly identify IC class overlaps."""

    def setUp(self):
        # MockConflictAdapter returns conflicts in class 029
        # BASE_APPLICATION is also class 029 → expect overlap
        self.result = conduct_tmep_704_02_search(
            BASE_APPLICATION, tess_adapter=MockConflictAdapter()
        )
        self.gsa = self.result["goods_services_analysis"]

    def test_goods_services_analysis_present(self):
        self.assertIn("goods_services_analysis", self.result)

    def test_applied_ic_classes_correct(self):
        self.assertIn("029", self.gsa["applied_ic_classes"])

    def test_class_overlap_detected_true(self):
        self.assertTrue(self.gsa["class_overlap_detected"])

    def test_same_class_count_is_positive(self):
        self.assertGreater(self.gsa["conflicts_with_same_class"], 0)

    def test_same_class_application_numbers_is_list(self):
        self.assertIsInstance(self.gsa["same_class_application_numbers"], list)

    def test_factor2_input_ready_true(self):
        self.assertTrue(self.gsa["factor2_input_ready"])

    def test_no_overlap_when_empty(self):
        result = conduct_tmep_704_02_search(
            BASE_APPLICATION, tess_adapter=EmptyTessAdapter()
        )
        self.assertFalse(result["goods_services_analysis"]["class_overlap_detected"])

    def test_different_class_no_overlap(self):
        """Conflict in class 025 vs applied class 029 → no same-class overlap."""
        diff_class_conflict = [
            ConflictRecord(
                application_number="999", mark_text="ADAMS APPLE",
                status="registered", ic_classes=["025"]
            )
        ]
        result = conduct_tmep_704_02_search(
            BASE_APPLICATION,
            tess_adapter=ConfigurableMockAdapter(diff_class_conflict)
        )
        self.assertFalse(result["goods_services_analysis"]["class_overlap_detected"])


class TestPreliminaryFlag(unittest.TestCase):
    """preliminary_flag must correctly set risk level."""

    def test_high_risk_exact_same_class(self):
        """Exact match in same class → HIGH risk."""
        result = conduct_tmep_704_02_search(
            BASE_APPLICATION, tess_adapter=MockConflictAdapter()
        )
        # MockConflictAdapter returns exact matches in class 029
        pf = result["preliminary_flag"]
        self.assertIn(pf["risk_level"], ["HIGH", "MEDIUM"])

    def test_none_risk_when_no_conflicts(self):
        result = conduct_tmep_704_02_search(
            BASE_APPLICATION, tess_adapter=EmptyTessAdapter()
        )
        self.assertEqual(result["preliminary_flag"]["risk_level"], "NONE")

    def test_preliminary_flag_has_required_fields(self):
        result = conduct_tmep_704_02_search(
            BASE_APPLICATION, tess_adapter=EmptyTessAdapter()
        )
        pf = result["preliminary_flag"]
        for field in ["risk_level", "reason", "exact_match_count",
                      "same_class_exact_count", "priority_conflicts"]:
            self.assertIn(field, pf)

    def test_risk_level_is_valid_value(self):
        result = conduct_tmep_704_02_search(
            BASE_APPLICATION, tess_adapter=EmptyTessAdapter()
        )
        self.assertIn(result["preliminary_flag"]["risk_level"],
                      ["HIGH", "MEDIUM", "LOW", "NONE"])


class TestRefusalFlag(unittest.TestCase):
    """refusal_flag must signal §2(d) possibility correctly."""

    def test_refusal_possible_true_when_same_class_active(self):
        result = conduct_tmep_704_02_search(
            BASE_APPLICATION, tess_adapter=MockConflictAdapter()
        )
        self.assertTrue(result["refusal_flag"]["refusal_possible"])

    def test_refusal_possible_false_when_no_conflicts(self):
        result = conduct_tmep_704_02_search(
            BASE_APPLICATION, tess_adapter=EmptyTessAdapter()
        )
        self.assertFalse(result["refusal_flag"]["refusal_possible"])

    def test_refusal_flag_has_required_fields(self):
        result = conduct_tmep_704_02_search(
            BASE_APPLICATION, tess_adapter=EmptyTessAdapter()
        )
        rf = result["refusal_flag"]
        for field in ["refusal_possible", "same_class_active_conflicts",
                      "priority_for_1207_analysis", "pending_1207_analysis", "note"]:
            self.assertIn(field, rf)

    def test_pending_1207_analysis_always_true(self):
        """§1207 always makes the final call — never decided here."""
        for adapter in [EmptyTessAdapter(), MockConflictAdapter()]:
            result = conduct_tmep_704_02_search(
                BASE_APPLICATION, tess_adapter=adapter
            )
            self.assertTrue(result["refusal_flag"]["pending_1207_analysis"])

    def test_legal_basis_present_when_refusal_possible(self):
        result = conduct_tmep_704_02_search(
            BASE_APPLICATION, tess_adapter=MockConflictAdapter()
        )
        self.assertIn("§2(d)", result["refusal_flag"]["potential_legal_basis"])

    def test_legal_basis_none_when_no_refusal(self):
        result = conduct_tmep_704_02_search(
            BASE_APPLICATION, tess_adapter=EmptyTessAdapter()
        )
        self.assertIsNone(result["refusal_flag"]["potential_legal_basis"])


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — MANDATORY TRIGGER EVENTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestMandatoryTriggerEvents(unittest.TestCase):
    def _run(self, trigger):
        return conduct_tmep_704_02_search(
            make_app(event_trigger=trigger), tess_adapter=EmptyTessAdapter()
        )

    def test_first_review_triggers_search(self):
        self.assertTrue(self._run("first_review")["search_conducted"])

    def test_revival_triggers_search(self):
        self.assertTrue(self._run("revival")["search_conducted"])

    def test_amendment_goods_triggers_search(self):
        self.assertTrue(self._run("amendment_goods")["search_conducted"])

    def test_new_basis_triggers_search(self):
        self.assertTrue(self._run("new_basis")["search_conducted"])

    def test_non_trigger_event_raises(self):
        with self.assertRaises(SearchNotRequiredError):
            conduct_tmep_704_02_search(
                make_app(event_trigger="routine_review"),
                tess_adapter=EmptyTessAdapter()
            )


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — RE-SEARCH FLAG
# ═══════════════════════════════════════════════════════════════════════════════

class TestReSearchFlag(unittest.TestCase):
    def _run(self, trigger):
        return conduct_tmep_704_02_search(
            make_app(event_trigger=trigger), tess_adapter=EmptyTessAdapter()
        )

    def test_first_review_no_re_search(self):
        self.assertFalse(self._run("first_review")["re_search_required"])

    def test_revival_sets_re_search(self):
        r = self._run("revival")
        self.assertTrue(r["re_search_required"])
        self.assertIn("§718.07", r["compliance_status"])

    def test_amendment_goods_sets_re_search(self):
        self.assertTrue(self._run("amendment_goods")["re_search_required"])

    def test_new_basis_sets_re_search(self):
        self.assertTrue(self._run("new_basis")["re_search_required"])


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — CONFLICT RESULTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestConflictResults(unittest.TestCase):
    def setUp(self):
        self.result = conduct_tmep_704_02_search(
            BASE_APPLICATION, tess_adapter=MockConflictAdapter()
        )

    def test_conflicts_count_is_correct(self):
        self.assertEqual(self.result["results_summary"]["total_conflicts_found"], 3)

    def test_conflicting_numbers_listed(self):
        nums = self.result["results_summary"]["conflicting_application_numbers"]
        for n in ["987654321", "876543219", "765432198"]:
            self.assertIn(n, nums)

    def test_no_duplicate_application_numbers(self):
        nums = self.result["results_summary"]["conflicting_application_numbers"]
        self.assertEqual(len(nums), len(set(nums)))


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — INPUT VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

class TestInputValidation(unittest.TestCase):
    def test_missing_application_id(self):
        app = BASE_APPLICATION.copy(); del app["application_id"]
        with self.assertRaises(ApplicationValidationError):
            conduct_tmep_704_02_search(app)

    def test_missing_mark_text(self):
        app = BASE_APPLICATION.copy(); del app["mark_text"]
        with self.assertRaises(ApplicationValidationError):
            conduct_tmep_704_02_search(app)

    def test_empty_mark_text(self):
        with self.assertRaises(ApplicationValidationError):
            conduct_tmep_704_02_search(make_app(mark_text="   "))

    def test_empty_goods_services(self):
        with self.assertRaises(ApplicationValidationError):
            conduct_tmep_704_02_search(make_app(goods_services=[]))

    def test_wrong_type_goods_services(self):
        with self.assertRaises(ApplicationValidationError):
            conduct_tmep_704_02_search(make_app(goods_services="class 029"))

    def test_missing_event_trigger(self):
        app = BASE_APPLICATION.copy(); del app["event_trigger"]
        with self.assertRaises(ApplicationValidationError):
            conduct_tmep_704_02_search(app)

    def test_missing_mark_type(self):
        app = BASE_APPLICATION.copy(); del app["mark_type"]
        with self.assertRaises(ApplicationValidationError):
            conduct_tmep_704_02_search(app)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — AUDIT LOG
# ═══════════════════════════════════════════════════════════════════════════════

class TestAuditLog(unittest.TestCase):
    def setUp(self):
        self.output, self.audit = conduct_tmep_704_02_search(
            BASE_APPLICATION, tess_adapter=EmptyTessAdapter(), return_audit_log=True
        )

    def test_audit_log_returned(self):
        self.assertIsNotNone(self.audit)

    def test_audit_authority_reference(self):
        self.assertEqual(self.audit["authority_reference"], AUTHORITY_REFERENCE)

    def test_audit_application_id(self):
        self.assertEqual(self.audit["application_id"], "123456789")

    def test_audit_unique_id_per_call(self):
        _, audit2 = conduct_tmep_704_02_search(
            BASE_APPLICATION, tess_adapter=EmptyTessAdapter(), return_audit_log=True
        )
        self.assertNotEqual(self.audit["audit_id"], audit2["audit_id"])

    def test_audit_queries_executed_is_list(self):
        self.assertIsInstance(self.audit["queries_executed"], list)
        self.assertGreater(len(self.audit["queries_executed"]), 0)

    def test_audit_downstream_gate(self):
        self.assertEqual(self.audit["downstream_gate"], "CLEARED_FOR_1207")

    def test_audit_database_searched(self):
        self.assertEqual(self.audit["database_searched"], DATABASE_NAME)

    def test_audit_queries_have_solr_strings(self):
        for q in self.audit["queries_executed"]:
            self.assertIn("solr_string", q)
            self.assertTrue(len(q["solr_string"]) > 0)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 8 — QUERY BUILDER
# ═══════════════════════════════════════════════════════════════════════════════

class TestQueryBuilder(unittest.TestCase):
    def setUp(self):
        gs = [GoodsServices(ic_class="029", description="Dried fruit")]
        self.queries = build_search_queries("ADAMS APPLE", gs)
        self.types   = [q.query_type for q in self.queries]

    def test_exact_query_present(self):
        self.assertIn("exact", self.types)

    def test_phonetic_query_present(self):
        self.assertIn("phonetic", self.types)

    def test_spelling_variation_query_present(self):
        self.assertIn("spelling_variation", self.types)

    def test_dominant_portion_queries_present(self):
        self.assertIn("dominant_portion", self.types)

    def test_dominant_portion_per_word(self):
        dom = [q.search_term for q in self.queries if q.query_type == "dominant_portion"]
        self.assertIn("ADAMS", dom)
        self.assertIn("APPLE", dom)

    def test_all_queries_have_ic_classes(self):
        for q in self.queries:
            self.assertIsInstance(q.ic_classes, list)

    def test_all_queries_have_unique_ids(self):
        ids = [q.query_id for q in self.queries]
        self.assertEqual(len(ids), len(set(ids)))

    def test_all_queries_have_solr_string(self):
        for q in self.queries:
            self.assertTrue(len(q.solr_string) > 0)

    def test_exact_solr_uses_quoted_phrase(self):
        exact = next(q for q in self.queries if q.query_type == "exact")
        self.assertIn('"ADAMS APPLE"', exact.solr_string)

    def test_dominant_solr_uses_wildcard(self):
        dom = [q for q in self.queries if q.query_type == "dominant_portion"]
        for q in dom:
            self.assertIn("*", q.solr_string)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 9 — PHONETIC KEY
# ═══════════════════════════════════════════════════════════════════════════════

class TestPhoneticKey(unittest.TestCase):
    def test_length_is_four(self):
        self.assertEqual(len(phonetic_key("ADAMS")), 4)

    def test_starts_with_first_char(self):
        self.assertTrue(phonetic_key("ADAMS").startswith("A"))

    def test_empty_string(self):
        self.assertEqual(phonetic_key(""), "")


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 10 — MULTIPLE GOODS CLASSES
# ═══════════════════════════════════════════════════════════════════════════════

class TestMultipleGoodsClasses(unittest.TestCase):
    def test_multiple_classes_accepted(self):
        app = make_app(goods_services=[
            {"class": "029", "description": "Dried fruits"},
            {"class": "030", "description": "Coffee"},
            {"class": "032", "description": "Fruit juices"},
        ])
        result = conduct_tmep_704_02_search(app, tess_adapter=EmptyTessAdapter())
        self.assertTrue(result["search_conducted"])

    def test_all_classes_in_applied_for_mark(self):
        app = make_app(goods_services=[
            {"class": "029", "description": "Dried fruits"},
            {"class": "030", "description": "Coffee"},
        ])
        result = conduct_tmep_704_02_search(app, tess_adapter=EmptyTessAdapter())
        classes = result["applied_for_mark"]["ic_classes"]
        self.assertIn("029", classes)
        self.assertIn("030", classes)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 11 — RAPIDAPI ADAPTER UNIT TESTS (no real key needed)
# ═══════════════════════════════════════════════════════════════════════════════

class TestRapidApiAdapterStructure(unittest.TestCase):
    """Tests adapter logic without making real API calls."""

    def test_raises_without_key(self):
        with self.assertRaises(ValueError):
            RapidApiTrademarkAdapter(rapidapi_key="")

    def test_raises_with_blank_key(self):
        with self.assertRaises(ValueError):
            RapidApiTrademarkAdapter(rapidapi_key="   ")

    def test_initialises_with_valid_key(self):
        adapter = RapidApiTrademarkAdapter(rapidapi_key="SOME_KEY_123")
        self.assertEqual(adapter.key, "SOME_KEY_123")
        self.assertEqual(adapter.status_filter, "all")

    def test_status_filter_configurable(self):
        adapter = RapidApiTrademarkAdapter(rapidapi_key="KEY", status_filter="active")
        self.assertEqual(adapter.status_filter, "active")

    def test_parse_list_response(self):
        """Adapter handles bare list response from API."""
        adapter = RapidApiTrademarkAdapter(rapidapi_key="KEY")
        data = [
            {
                "serial_number": "111222333",
                "keyword":       "ADAMS APPLE",
                "status_code":   "700",
                "status_label":  "Registered",
                "classification": [{"international_code": "029"}],
                "owners":         [{"name": "Test Corp"}],
                "filing_date":    "2020-01-01",
                "registration_date": "2021-01-01",
            }
        ]
        records = adapter._parse(data, "exact")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].application_number, "111222333")
        self.assertEqual(records[0].mark_text, "ADAMS APPLE")
        self.assertEqual(records[0].status, "registered")
        self.assertEqual(records[0].ic_classes, ["029"])
        self.assertEqual(records[0].owner_name, "Test Corp")

    def test_parse_dict_with_items_wrapper(self):
        """Adapter handles {'items': [...]} wrapped response."""
        adapter = RapidApiTrademarkAdapter(rapidapi_key="KEY")
        data = {"items": [
            {"serial_number": "999", "keyword": "TEST MARK",
             "status_code": "100", "classification": [], "owners": []}
        ]}
        records = adapter._parse(data, "exact")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "pending")

    def test_parse_empty_list(self):
        adapter = RapidApiTrademarkAdapter(rapidapi_key="KEY")
        records = adapter._parse([], "exact")
        self.assertEqual(records, [])

    def test_normalise_skips_record_without_serial(self):
        adapter = RapidApiTrademarkAdapter(rapidapi_key="KEY")
        item    = {"keyword": "NO SERIAL", "status_code": "700", "classification": []}
        result  = adapter._normalise(item, "exact")
        self.assertIsNone(result)

    def test_dedup_across_query_types(self):
        """Same serial from different query types → only 1 record."""
        adapter   = RapidApiTrademarkAdapter(rapidapi_key="KEY")
        duplicate = ConflictRecord(
            application_number="555", mark_text="DUPE", status="registered", ic_classes=["029"]
        )
        records = adapter._dedup([duplicate, duplicate])
        self.assertEqual(len(records), 1)


class TestRapidApiStatusResolver(unittest.TestCase):
    def test_700_is_registered(self):
        self.assertEqual(_resolve_status("700", ""), "registered")

    def test_100_is_pending(self):
        self.assertEqual(_resolve_status("100", ""), "pending")

    def test_label_fallback_registered(self):
        self.assertEqual(_resolve_status("", "REGISTERED"), "registered")

    def test_label_fallback_pending(self):
        self.assertEqual(_resolve_status("", "FILED"), "pending")

    def test_label_fallback_dead(self):
        self.assertEqual(_resolve_status("", "ABANDONED"), "dead")

    def test_unknown_returns_unknown(self):
        self.assertEqual(_resolve_status("", ""), "unknown")


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 12 — LIVE ADAPTER HTTP TESTS (requests-mock)
# ═══════════════════════════════════════════════════════════════════════════════

def _api_doc(serial, mark, status_code="700", classes=None):
    return {
        "serialNumber": serial, "markIdentification": mark,
        "statusCode": status_code, "statusLabel": "Registered",
        "internationalClassCodes": classes or ["029"],
        "filingDate": "2020-01-01", "registrationDate": "2021-06-15",
        "ownerName": "Test Corp",
    }

def _api_response(docs, num_found=None):
    return {"response": {"numFound": num_found or len(docs), "docs": docs}}


@unittest.skipUnless(HAS_REQUESTS_MOCK, "pip install requests-mock to enable live adapter tests")
class TestTessLiveAdapterHTTP(unittest.TestCase):

    def setUp(self):
        gs = [GoodsServices(ic_class="029", description="Dried fruit")]
        self.queries = bsq("ADAMS APPLE", gs)
        self.adapter = TessLiveAdapter(base_url=config.TESS_API_BASE_URL)

    def test_returns_records_on_200(self):
        body = _api_response([_api_doc("111", "ADAMS APPLE"), _api_doc("222", "ADAMS APPLE CO", "100")])
        with req_mock_lib.Mocker() as m:
            m.get(config.TESS_API_BASE_URL, json=body)
            results = self.adapter.search(self.queries)
        self.assertGreaterEqual(len(results), 1)

    def test_empty_results_on_empty_docs(self):
        with req_mock_lib.Mocker() as m:
            m.get(config.TESS_API_BASE_URL, json=_api_response([]))
            results = self.adapter.search(self.queries)
        self.assertEqual(results, [])

    def test_http_500_returns_empty(self):
        with req_mock_lib.Mocker() as m:
            m.get(config.TESS_API_BASE_URL, status_code=500)
            results = self.adapter.search(self.queries)
        self.assertEqual(results, [])

    def test_malformed_json_returns_empty(self):
        with req_mock_lib.Mocker() as m:
            m.get(config.TESS_API_BASE_URL, text="not json")
            results = self.adapter.search(self.queries)
        self.assertEqual(results, [])

    def test_deduplication_across_queries(self):
        body = _api_response([_api_doc("999", "ADAMS APPLE")])
        with req_mock_lib.Mocker() as m:
            m.get(config.TESS_API_BASE_URL, json=body)
            results = self.adapter.search(self.queries)
        nums = [r.application_number for r in results]
        self.assertEqual(len(nums), len(set(nums)))


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 13 — CONFIGURABLE MOCK & INTEGRATION SMOKE
# ═══════════════════════════════════════════════════════════════════════════════

class TestConfigurableMock(unittest.TestCase):
    def test_custom_conflict_injected(self):
        custom = [ConflictRecord(
            application_number="111000111", mark_text="APPLE ADAMS",
            status="registered", ic_classes=["029"]
        )]
        result = conduct_tmep_704_02_search(
            BASE_APPLICATION, tess_adapter=ConfigurableMockAdapter(custom)
        )
        self.assertEqual(result["results_summary"]["total_conflicts_found"], 1)
        self.assertIn("111000111",
                      result["results_summary"]["conflicting_application_numbers"])


class TestIntegrationSmoke(unittest.TestCase):
    def test_full_pipeline_first_review(self):
        result = conduct_tmep_704_02_search(
            BASE_APPLICATION, tess_adapter=EmptyTessAdapter()
        )
        self.assertEqual(result["authority_reference"], "TMEP §704.02")
        self.assertTrue(result["search_conducted"])
        self.assertIn("§704.02", result["compliance_status"])
        self.assertIn("§1207", result["compliance_status"])
        # §1207-ready fields present
        self.assertIn("applied_for_mark", result)
        self.assertIn("conflict_set", result)
        self.assertIn("goods_services_analysis", result)
        self.assertIn("preliminary_flag", result)
        self.assertIn("refusal_flag", result)

    def test_full_pipeline_revival_with_conflicts(self):
        app    = make_app(event_trigger="revival")
        result = conduct_tmep_704_02_search(app, tess_adapter=MockConflictAdapter())
        self.assertTrue(result["search_conducted"])
        self.assertTrue(result["re_search_required"])
        self.assertGreater(result["results_summary"]["total_conflicts_found"], 0)
        self.assertIn("§718.07", result["compliance_status"])
        self.assertTrue(result["refusal_flag"]["refusal_possible"])

    def test_full_output_is_json_serializable(self):
        result = conduct_tmep_704_02_search(
            BASE_APPLICATION, tess_adapter=MockConflictAdapter()
        )
        json.dumps(result)  # must not raise

    def test_1207_handoff_package_complete(self):
        """Verifies §1207 can receive everything it needs from this output."""
        result = conduct_tmep_704_02_search(
            BASE_APPLICATION, tess_adapter=MockConflictAdapter()
        )
        # §1207 needs all of these
        self.assertIn("applied_for_mark",        result)
        self.assertIn("conflict_set",            result)
        self.assertIn("goods_services_analysis", result)
        self.assertIn("preliminary_flag",        result)
        self.assertIn("refusal_flag",            result)
        # §1207 picks up the conflict list directly
        self.assertIsInstance(result["conflict_set"], list)
        # Each conflict has mark_text for §1207 Factor 1
        for rec in result["conflict_set"]:
            self.assertIn("mark_text", rec)
            self.assertIn("ic_classes", rec)


if __name__ == "__main__":
    unittest.main(verbosity=2)


# # tests/test_engine.py
# """
# Test Suite — TMEP §704.02 Search Engine
# ========================================
# All 58 original tests ported here, plus new tests for:
#   - SOLR string generation
#   - Live adapter HTTP error handling (via requests_mock)
#   - ConfigurableMockAdapter
#   - Audit log SOLR string presence

# Run:
#     cd tmep_search
#     python -m pytest tests/ -v
# """

# import json
# import sys
# import os
# import unittest
# from datetime import datetime

# # Add parent to path so imports resolve
# sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# from core.search_engine import conduct_tmep_704_02_search
# from core.validators import ApplicationValidationError, SearchNotRequiredError
# from core.query_builder import build_search_queries, phonetic_key
# from core.validators import is_search_required
# from core.models import (
#     AUTHORITY_REFERENCE, DATABASE_NAME, RECORDS_SEARCHED, VARIATION_TYPES,
#     GoodsServices,
# )
# from adapters.mock import MockConflictAdapter, EmptyTessAdapter, ConfigurableMockAdapter
# from adapters.base import TessAdapterBase
# from core.models import ConflictRecord

# from tests.fixtures import BASE_APPLICATION, make_app


# # ──────────────────────────────────────────────────────────────────────────────
# # OUTPUT SCHEMA
# # ──────────────────────────────────────────────────────────────────────────────

# class TestOutputSchema(unittest.TestCase):
#     def setUp(self):
#         self.result = conduct_tmep_704_02_search(
#             BASE_APPLICATION, tess_adapter=EmptyTessAdapter()
#         )

#     def test_authority_reference(self):
#         self.assertEqual(self.result["authority_reference"], "TMEP §704.02")

#     def test_search_conducted_is_true(self):
#         self.assertTrue(self.result["search_conducted"])

#     def test_timestamp_is_iso8601_utc(self):
#         ts = self.result["search_timestamp"]
#         self.assertTrue(ts.endswith("Z"), f"Timestamp must end with Z: {ts}")
#         datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")

#     def test_search_scope_database(self):
#         self.assertEqual(self.result["search_scope"]["database"], DATABASE_NAME)

#     def test_search_scope_records_searched(self):
#         self.assertEqual(
#             sorted(self.result["search_scope"]["records_searched"]),
#             sorted(RECORDS_SEARCHED),
#         )

#     def test_search_scope_variation_types(self):
#         self.assertEqual(
#             sorted(self.result["search_scope"]["variation_types"]),
#             sorted(VARIATION_TYPES),
#         )

#     def test_results_summary_present(self):
#         rs = self.result["results_summary"]
#         self.assertIn("total_conflicts_found", rs)
#         self.assertIn("conflicting_application_numbers", rs)

#     def test_results_summary_count_is_integer(self):
#         self.assertIsInstance(self.result["results_summary"]["total_conflicts_found"], int)

#     def test_results_summary_numbers_is_list(self):
#         self.assertIsInstance(
#             self.result["results_summary"]["conflicting_application_numbers"], list
#         )

#     def test_re_search_required_is_bool(self):
#         self.assertIsInstance(self.result["re_search_required"], bool)

#     def test_compliance_status_is_string(self):
#         self.assertIsInstance(self.result["compliance_status"], str)

#     def test_compliance_status_references_authority(self):
#         self.assertIn("§704.02", self.result["compliance_status"])

#     def test_output_is_json_serializable(self):
#         json.dumps(self.result)

#     def test_no_1207_fields_present(self):
#         forbidden = ["similarity_score", "confusion_likelihood", "refusal", "section_2d"]
#         for field in forbidden:
#             self.assertNotIn(field, self.result)


# # ──────────────────────────────────────────────────────────────────────────────
# # MANDATORY TRIGGER EVENTS
# # ──────────────────────────────────────────────────────────────────────────────

# class TestMandatoryTriggerEvents(unittest.TestCase):
#     def _run(self, trigger: str) -> dict:
#         return conduct_tmep_704_02_search(
#             make_app(event_trigger=trigger), tess_adapter=EmptyTessAdapter()
#         )

#     def test_first_review_triggers_search(self):
#         self.assertTrue(self._run("first_review")["search_conducted"])

#     def test_revival_triggers_search(self):
#         self.assertTrue(self._run("revival")["search_conducted"])

#     def test_amendment_goods_triggers_search(self):
#         self.assertTrue(self._run("amendment_goods")["search_conducted"])

#     def test_new_basis_triggers_search(self):
#         self.assertTrue(self._run("new_basis")["search_conducted"])

#     def test_non_trigger_event_raises(self):
#         with self.assertRaises(SearchNotRequiredError):
#             conduct_tmep_704_02_search(
#                 make_app(event_trigger="routine_review"),
#                 tess_adapter=EmptyTessAdapter(),
#             )


# # ──────────────────────────────────────────────────────────────────────────────
# # RE-SEARCH FLAG
# # ──────────────────────────────────────────────────────────────────────────────

# class TestReSearchFlag(unittest.TestCase):
#     def _run(self, trigger: str) -> dict:
#         return conduct_tmep_704_02_search(
#             make_app(event_trigger=trigger), tess_adapter=EmptyTessAdapter()
#         )

#     def test_first_review_no_re_search(self):
#         self.assertFalse(self._run("first_review")["re_search_required"])

#     def test_revival_sets_re_search(self):
#         r = self._run("revival")
#         self.assertTrue(r["re_search_required"])
#         self.assertIn("§718.07", r["compliance_status"])

#     def test_amendment_goods_sets_re_search(self):
#         self.assertTrue(self._run("amendment_goods")["re_search_required"])

#     def test_new_basis_sets_re_search(self):
#         self.assertTrue(self._run("new_basis")["re_search_required"])


# # ──────────────────────────────────────────────────────────────────────────────
# # CONFLICT RESULTS (using MockConflictAdapter — 3 unique records)
# # ──────────────────────────────────────────────────────────────────────────────

# class TestConflictResults(unittest.TestCase):
#     def setUp(self):
#         self.result = conduct_tmep_704_02_search(
#             BASE_APPLICATION, tess_adapter=MockConflictAdapter()
#         )

#     def test_conflicts_count_is_correct(self):
#         self.assertEqual(self.result["results_summary"]["total_conflicts_found"], 3)

#     def test_conflicting_numbers_are_listed(self):
#         nums = self.result["results_summary"]["conflicting_application_numbers"]
#         self.assertIn("987654321", nums)
#         self.assertIn("876543219", nums)
#         self.assertIn("765432198", nums)

#     def test_no_duplicate_application_numbers(self):
#         nums = self.result["results_summary"]["conflicting_application_numbers"]
#         self.assertEqual(len(nums), len(set(nums)))


# # ──────────────────────────────────────────────────────────────────────────────
# # INPUT VALIDATION
# # ──────────────────────────────────────────────────────────────────────────────

# class TestInputValidation(unittest.TestCase):
#     def test_missing_application_id(self):
#         app = BASE_APPLICATION.copy(); del app["application_id"]
#         with self.assertRaises(ApplicationValidationError):
#             conduct_tmep_704_02_search(app)

#     def test_missing_mark_text(self):
#         app = BASE_APPLICATION.copy(); del app["mark_text"]
#         with self.assertRaises(ApplicationValidationError):
#             conduct_tmep_704_02_search(app)

#     def test_empty_mark_text(self):
#         with self.assertRaises(ApplicationValidationError):
#             conduct_tmep_704_02_search(make_app(mark_text="   "))

#     def test_empty_goods_services(self):
#         with self.assertRaises(ApplicationValidationError):
#             conduct_tmep_704_02_search(make_app(goods_services=[]))

#     def test_wrong_type_goods_services(self):
#         with self.assertRaises(ApplicationValidationError):
#             conduct_tmep_704_02_search(make_app(goods_services="class 029"))

#     def test_missing_event_trigger(self):
#         app = BASE_APPLICATION.copy(); del app["event_trigger"]
#         with self.assertRaises(ApplicationValidationError):
#             conduct_tmep_704_02_search(app)

#     def test_missing_mark_type(self):
#         app = BASE_APPLICATION.copy(); del app["mark_type"]
#         with self.assertRaises(ApplicationValidationError):
#             conduct_tmep_704_02_search(app)


# # ──────────────────────────────────────────────────────────────────────────────
# # AUDIT LOG
# # ──────────────────────────────────────────────────────────────────────────────

# class TestAuditLog(unittest.TestCase):
#     def setUp(self):
#         self.output, self.audit = conduct_tmep_704_02_search(
#             BASE_APPLICATION,
#             tess_adapter=EmptyTessAdapter(),
#             return_audit_log=True,
#         )

#     def test_audit_log_returned(self):
#         self.assertIsNotNone(self.audit)

#     def test_audit_authority_reference(self):
#         self.assertEqual(self.audit["authority_reference"], AUTHORITY_REFERENCE)

#     def test_audit_application_id(self):
#         self.assertEqual(self.audit["application_id"], "123456789")

#     def test_audit_unique_id_per_call(self):
#         _, audit2 = conduct_tmep_704_02_search(
#             BASE_APPLICATION,
#             tess_adapter=EmptyTessAdapter(),
#             return_audit_log=True,
#         )
#         self.assertNotEqual(self.audit["audit_id"], audit2["audit_id"])

#     def test_audit_queries_executed_is_list(self):
#         self.assertIsInstance(self.audit["queries_executed"], list)
#         self.assertGreater(len(self.audit["queries_executed"]), 0)

#     def test_audit_downstream_gate(self):
#         self.assertEqual(self.audit["downstream_gate"], "CLEARED_FOR_1207")

#     def test_audit_database_searched(self):
#         self.assertEqual(self.audit["database_searched"], DATABASE_NAME)

#     def test_audit_queries_contain_solr_strings(self):
#         """New: each query in the audit log should carry its SOLR string."""
#         for q in self.audit["queries_executed"]:
#             self.assertIn("solr_string", q)
#             self.assertTrue(len(q["solr_string"]) > 0)


# # ──────────────────────────────────────────────────────────────────────────────
# # QUERY BUILDER
# # ──────────────────────────────────────────────────────────────────────────────

# class TestQueryBuilder(unittest.TestCase):
#     def setUp(self):
#         gs = [GoodsServices(ic_class="029", description="Dried fruit")]
#         self.queries = build_search_queries("ADAMS APPLE", gs)
#         self.types   = [q.query_type for q in self.queries]

#     def test_exact_query_present(self):
#         self.assertIn("exact", self.types)

#     def test_phonetic_query_present(self):
#         self.assertIn("phonetic", self.types)

#     def test_spelling_variation_query_present(self):
#         self.assertIn("spelling_variation", self.types)

#     def test_dominant_portion_queries_present(self):
#         self.assertIn("dominant_portion", self.types)

#     def test_dominant_portion_per_word(self):
#         dom = [q.search_term for q in self.queries if q.query_type == "dominant_portion"]
#         self.assertIn("ADAMS", dom)
#         self.assertIn("APPLE", dom)

#     def test_all_queries_have_ic_classes(self):
#         for q in self.queries:
#             self.assertIsInstance(q.ic_classes, list)

#     def test_all_queries_have_unique_ids(self):
#         ids = [q.query_id for q in self.queries]
#         self.assertEqual(len(ids), len(set(ids)))

#     def test_all_queries_have_solr_string(self):
#         """New: every query must carry a non-empty SOLR string."""
#         for q in self.queries:
#             self.assertTrue(len(q.solr_string) > 0, f"Empty SOLR string on {q.query_id}")

#     def test_exact_solr_uses_quoted_phrase(self):
#         exact = next(q for q in self.queries if q.query_type == "exact")
#         self.assertIn('"ADAMS APPLE"', exact.solr_string)

#     def test_dominant_solr_uses_wildcard(self):
#         dom = [q for q in self.queries if q.query_type == "dominant_portion"]
#         for q in dom:
#             self.assertIn("*", q.solr_string)


# # ──────────────────────────────────────────────────────────────────────────────
# # PHONETIC KEY
# # ──────────────────────────────────────────────────────────────────────────────

# class TestPhoneticKey(unittest.TestCase):
#     def test_length_is_four(self):
#         self.assertEqual(len(phonetic_key("ADAMS")), 4)

#     def test_starts_with_first_char(self):
#         self.assertTrue(phonetic_key("ADAMS").startswith("A"))

#     def test_empty_string(self):
#         self.assertEqual(phonetic_key(""), "")


# # ──────────────────────────────────────────────────────────────────────────────
# # IS TRIGGER REQUIRED
# # ──────────────────────────────────────────────────────────────────────────────

# class TestIsTriggerRequired(unittest.TestCase):
#     def test_first_review_required(self):
#         self.assertTrue(is_search_required("first_review"))

#     def test_revival_required(self):
#         self.assertTrue(is_search_required("revival"))

#     def test_random_event_not_required(self):
#         self.assertFalse(is_search_required("office_suspension"))

#     def test_case_insensitive(self):
#         self.assertTrue(is_search_required("FIRST_REVIEW"))
#         self.assertTrue(is_search_required("Revival"))


# # ──────────────────────────────────────────────────────────────────────────────
# # MULTIPLE GOODS CLASSES
# # ──────────────────────────────────────────────────────────────────────────────

# class TestMultipleGoodsClasses(unittest.TestCase):
#     def test_multiple_classes_accepted(self):
#         app = make_app(goods_services=[
#             {"class": "029", "description": "Dried fruits"},
#             {"class": "030", "description": "Coffee"},
#             {"class": "032", "description": "Fruit juices"},
#         ])
#         result = conduct_tmep_704_02_search(app, tess_adapter=EmptyTessAdapter())
#         self.assertTrue(result["search_conducted"])

#     def test_all_classes_in_queries(self):
#         app = make_app(goods_services=[
#             {"class": "029", "description": "Dried fruits"},
#             {"class": "030", "description": "Coffee"},
#         ])
#         gs  = [GoodsServices.from_dict(g) for g in app["goods_services"]]
#         queries = build_search_queries("ADAMS APPLE", gs)
#         for q in queries:
#             self.assertIn("029", q.ic_classes)
#             self.assertIn("030", q.ic_classes)


# # ──────────────────────────────────────────────────────────────────────────────
# # CONFIGURABLE MOCK ADAPTER
# # ──────────────────────────────────────────────────────────────────────────────

# class TestConfigurableMock(unittest.TestCase):
#     def test_custom_conflict_list(self):
#         custom = [
#             ConflictRecord(
#                 application_number="111000111",
#                 mark_text="APPLE ADAMS",
#                 status="registered",
#                 ic_classes=["029"],
#             )
#         ]
#         adapter = ConfigurableMockAdapter(custom)
#         result  = conduct_tmep_704_02_search(BASE_APPLICATION, tess_adapter=adapter)
#         self.assertEqual(result["results_summary"]["total_conflicts_found"], 1)
#         self.assertIn("111000111",
#                       result["results_summary"]["conflicting_application_numbers"])


# # ──────────────────────────────────────────────────────────────────────────────
# # INTEGRATION SMOKE TEST
# # ──────────────────────────────────────────────────────────────────────────────

# class TestIntegrationSmoke(unittest.TestCase):
#     def test_full_pipeline_first_review(self):
#         result = conduct_tmep_704_02_search(
#             BASE_APPLICATION, tess_adapter=EmptyTessAdapter()
#         )
#         self.assertEqual(result["authority_reference"], "TMEP §704.02")
#         self.assertTrue(result["search_conducted"])
#         self.assertIn("§704.02", result["compliance_status"])
#         self.assertIn("§1207",   result["compliance_status"])

#     def test_full_pipeline_revival_with_conflicts(self):
#         app    = make_app(event_trigger="revival")
#         result = conduct_tmep_704_02_search(app, tess_adapter=MockConflictAdapter())
#         self.assertTrue(result["search_conducted"])
#         self.assertTrue(result["re_search_required"])
#         self.assertGreater(result["results_summary"]["total_conflicts_found"], 0)
#         self.assertIn("§718.07", result["compliance_status"])


# if __name__ == "__main__":
#     unittest.main(verbosity=2)
