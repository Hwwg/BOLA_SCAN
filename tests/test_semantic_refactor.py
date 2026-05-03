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

from utils.param_path import (
    find_matching_path,
    identifier_names_in_path,
    nested_get,
    nested_set,
    occurrence_for,
    path_container_params,
)
from utils.dependency_cc.src.api_data_tag import ApiDataTagging


def test_path_parent_identifier_is_container_candidate():
    endpoint = "GET /orgs/{orgId}/projects/{projectId}"

    assert "orgId" in path_container_params(endpoint)
    assert "projectId" not in path_container_params(endpoint)


def test_body_response_structural_levels_count_nested_paths():
    assert occurrence_for("items[].id", "response", "GET /orders", "list query").structural_level == 2
    assert occurrence_for("data.user.id", "response", "GET /users/{id}", "query").structural_level == 3
    assert occurrence_for("id", "query", "GET /users", "query").structural_level == 1


def test_identifier_can_be_middle_segment_of_nested_path():
    assert identifier_names_in_path("authority.dataAuthorityId[].value") == ["dataAuthorityId"]
    assert identifier_names_in_path("items[].id") == ["id"]


def test_nested_body_value_lookup_and_update():
    body = {"items": [{"book_id": ""}], "name": "old"}
    path = find_matching_path(body, ["book_id"])

    assert path == "items[].book_id"
    assert nested_get(body, path) == ""
    assert nested_set(body, path, "1") is True
    assert body["items"][0]["book_id"] == "1"


def test_list_query_is_query_upgrade_not_mixed_type():
    tagger = object.__new__(ApiDataTagging)
    endpoint = "GET /api/v1/orders/list"
    api_info = {
        "request_parameters": {"page": {"type": "integer", "in": "query"}},
        "response_parameters": {"data[]": {"type": "object"}},
    }

    assert tagger._classify_api_type_by_rule(endpoint, api_info) == "list query"
    assert tagger._upgrade_query_type(endpoint, api_info, "query") == "list query"
    assert tagger._upgrade_query_type(endpoint, api_info, "add") == "add"


def test_detail_get_stays_plain_query():
    tagger = object.__new__(ApiDataTagging)
    endpoint = "GET /api/v1/orders/{orderId}"
    api_info = {
        "request_parameters": {"orderId": {"type": "string", "in": "path"}},
        "response_parameters": {"id": {"type": "string"}},
    }

    assert tagger._classify_api_type_by_rule(endpoint, api_info) == "query"
