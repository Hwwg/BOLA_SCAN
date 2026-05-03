import os
import sys
import types

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

if "openai" not in sys.modules:
    openai_stub = types.ModuleType("openai")
    openai_stub.OpenAI = object
    sys.modules["openai"] = openai_stub

from scripts.api_doc import ApiDoc


class StubApiDoc(ApiDoc):
    def __init__(self, level_outputs):
        self.level_outputs = level_outputs
        self.api_doc_path = "/tmp/test_project/postman.json"
        self.excludes = []
        self.gpt_reply = None
        self.syn_prompt = None
        self.jsontools = None

    def _select_tree_keywords(self, normalized_full_map, claimed_prefixes, common_prefix, current_level):
        return self.level_outputs.get(current_level, {"start_level": current_level, "keywords": []})


def test_keyword_expands_to_plus_one_groups():
    doc = StubApiDoc(
        {
            1: {"start_level": 1, "keywords": ["user"]},
        }
    )

    all_apis = {
        "POST /api/v2/user/create": {},
        "POST /api/v2/user/add": {},
        "GET /api/v2/user/small/list": {},
        "GET /api/v2/user/video": {},
        "POST /api/v2/user/video/create": {},
        "POST /api/v2/user/video/update": {},
        "POST /api/v2/admin/video/update": {},
    }

    grouped = doc.group_existing_apis(all_apis)

    assert set(grouped.keys()) == {
        "user/create",
        "user/add",
        "user/video",
        "user",
        "other",
    }
    assert set(grouped["user/create"].keys()) == {"POST /api/v2/user/create"}
    assert set(grouped["user/add"].keys()) == {"POST /api/v2/user/add"}
    assert set(grouped["user/video"].keys()) == {
        "GET /api/v2/user/video",
        "POST /api/v2/user/video/create",
        "POST /api/v2/user/video/update",
    }
    assert set(grouped["user"].keys()) == {"GET /api/v2/user/small/list"}
    assert set(grouped["other"].keys()) == {"POST /api/v2/admin/video/update"}


def test_validate_keywords_result_rejects_extra_fields_and_wrong_level():
    doc = StubApiDoc({})

    try:
        doc._validate_keywords_result(
            {"start_level": 1, "keywords": ["user"], "reason": "debug"},
            current_level=1,
            valid_level_keywords={"user"},
        )
        assert False, "expected extra field to be rejected"
    except ValueError as exc:
        assert "额外字段" in str(exc)

    try:
        doc._validate_keywords_result(
            {"start_level": 2, "keywords": ["user"]},
            current_level=1,
            valid_level_keywords={"user"},
        )
        assert False, "expected wrong level to be rejected"
    except ValueError as exc:
        assert "不等于" in str(exc)


def test_keyword_with_singleton_plus_one_groups_is_still_kept():
    doc = StubApiDoc(
        {
            1: {"start_level": 1, "keywords": ["auth"]},
        }
    )

    all_apis = {
        "POST /api/v2/auth/signup": {},
        "POST /api/v2/auth/forget-password": {},
        "GET /api/v2/user/profile": {},
    }

    grouped = doc.group_existing_apis(all_apis)

    assert set(grouped.keys()) == {"auth/signup", "auth/forget-password", "other"}
    assert set(grouped["auth/signup"].keys()) == {"POST /api/v2/auth/signup"}
    assert set(grouped["auth/forget-password"].keys()) == {"POST /api/v2/auth/forget-password"}
    assert set(grouped["other"].keys()) == {"GET /api/v2/user/profile"}

def test_filter_supported_prefixes_is_passthrough():
    doc = StubApiDoc({})

    normalized_full_map = {
        "a": ["vehicle", "vehicles"],
        "b": ["vehicle", "add_vehicle"],
        "c": ["vehicle", "{vehicleId}", "location"],
        "d": ["vehicle", "resend_email"],
    }
    prefixes = [
        ("vehicle", "vehicles"),
        ("vehicle", "add_vehicle"),
        ("vehicle", "{vehicleId}"),
        ("vehicle", "resend_email"),
    ]

    filtered = doc._filter_supported_prefixes(normalized_full_map, prefixes)
    assert filtered == prefixes


def test_keyword_with_multiple_singleton_plus_one_groups_is_still_valid():
    doc = StubApiDoc(
        {
            1: {"start_level": 1, "keywords": ["vehicle"]},
        }
    )

    all_apis = {
        "GET /api/v2/vehicle/vehicles": {},
        "POST /api/v2/vehicle/add_vehicle": {},
        "GET /api/v2/vehicle/{vehicleId}/location": {},
        "POST /api/v2/vehicle/resend_email": {},
        "GET /api/v2/admin/stats": {},
    }

    grouped = doc.group_existing_apis(all_apis)

    assert set(grouped.keys()) == {
        "vehicle/vehicles",
        "vehicle/add_vehicle",
        "vehicle",
        "vehicle/resend_email",
        "other",
    }
    assert set(grouped["vehicle"].keys()) == {"GET /api/v2/vehicle/{vehicleId}/location"}
    assert set(grouped["other"].keys()) == {"GET /api/v2/admin/stats"}


def test_invalid_keyword_round_skips_to_shortest_minus_one_level():
    doc = StubApiDoc(
        {}
    )

    active_map = {
        "a": ["api", "v1", "test"],
        "b": ["api", "v2", "test3"],
    }

    assert doc._next_level_after_empty_stage(active_map, 1, 5) == 2
