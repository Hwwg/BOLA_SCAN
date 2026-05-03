from venv import logger
from urllib3.util import response
from scripts.api_doc import ApiDoc
from scripts.jsontools import JsonTools

from prompt.synthesis_prompt import SyntheticPrompt
from gptreply.gpt_con import GPTReply

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import sys,os
import json
import re

# from utils.dependency_cc.src.case_generation_v2 import js

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

"""
Extract API doc data, tag each endpoint with CRUD type, and output.
Tagged results: self.api_doc
"""
class ApiDataTagging:
    VALID_API_TYPES = {"add", "delete", "update", "query", "list query"}
    API_TYPE_JUDGE_SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "endpoint": {"type": "string"},
                        "type": {
                            "type": "string",
                            "enum": ["add", "delete", "update", "query", "list query"],
                        },
                    },
                    "required": ["endpoint", "type"],
                },
            }
        },
        "required": ["results"],
    }

    def __init__(self,api_doc_path,model, grouping_strategy: str = 'tree_select', excludes=None) -> None:
        self.api_doc_tool = ApiDoc(api_doc_path,model, excludes=excludes or [])
        # 
        self.tag_results = {}
        # Use a smarter grouping strategy to aggregate CRUD for the same resource
        self.api_doc = self.api_doc_tool.api_function_tag(grouping_strategy)
        self.gpt_reply = GPTReply(model)
        self.jsontool = JsonTools()
        self.lock = threading.Lock()
        self.syn_prompt = SyntheticPrompt()
        self.initial_test_info_dict = {

        }
        try:
            self.min_coverage = float(os.getenv("BOLASCAN_API_TYPE_MIN_COVERAGE", "1.0"))
        except Exception:
            self.min_coverage = 1.0
        self.min_coverage = max(0.0, min(1.0, self.min_coverage))

    def _split_endpoint(self, endpoint):
        parts = str(endpoint or "").strip().split(None, 1)
        if len(parts) == 2:
            return parts[0].upper(), parts[1]
        return "", str(endpoint or "")

    def _endpoint_tokens(self, endpoint):
        _, path = self._split_endpoint(endpoint)
        text = re.sub(r"\{[^}]+\}", " ", path)
        return {t for t in re.split(r"[^A-Za-z0-9]+", text.lower()) if t}

    def _has_pagination_or_list_request(self, api_info):
        list_params = {
            "page", "pagesize", "page_size", "pagenumber", "pagenum",
            "limit", "offset", "size", "per_page", "perpage", "start", "count",
        }
        req = api_info.get("request_parameters", {}) if isinstance(api_info, dict) else {}
        if not isinstance(req, dict):
            return False
        for name in req.keys():
            normalized = str(name).replace("-", "_").lower()
            if normalized in list_params:
                return True
        return False

    def _has_array_response(self, api_info):
        resp = api_info.get("response_parameters", {}) if isinstance(api_info, dict) else {}
        if isinstance(resp, list):
            return True
        if not isinstance(resp, dict):
            return False
        t = str(resp.get("type", "")).lower()
        if t == "array":
            return True
        for name, spec in resp.items():
            lname = str(name).lower()
            # Only top-level collection fields indicate a list query. Nested arrays
            # such as comments[].id in a detail response should not upgrade it.
            if "." in lname:
                continue
            if "[]" in lname or lname in {"items", "data", "list", "records", "rows"}:
                if lname.endswith("[]") or isinstance(spec, list):
                    return True
                if isinstance(spec, dict) and (spec.get("type") == "array" or "items" in spec):
                    return True
        return False

    def _has_path_identifier(self, endpoint):
        _, path = self._split_endpoint(endpoint)
        return bool(re.search(r"\{[^}]+\}", path))

    def _upgrade_query_type(self, endpoint, api_info, base_type):
        if base_type != "query":
            return base_type
        tokens = self._endpoint_tokens(endpoint)
        if tokens & {"list", "search", "page", "pages", "index"}:
            return "list query"
        if self._has_pagination_or_list_request(api_info) or self._has_array_response(api_info):
            if self._has_path_identifier(endpoint) and not self._has_pagination_or_list_request(api_info):
                return "query"
            return "list query"
        return "query"

    def _classify_api_type_by_rule(self, endpoint, api_info):
        method, _ = self._split_endpoint(endpoint)
        tokens = self._endpoint_tokens(endpoint)
        if tokens & {"create", "add", "save", "new", "insert", "register", "signup"}:
            return "add"
        if tokens & {"delete", "remove", "del"}:
            return "delete"
        if tokens & {"update", "edit", "modify", "patch"}:
            return "update"
        if method == "DELETE":
            return "delete"
        if method in {"PUT", "PATCH"}:
            return "update"
        if method == "GET":
            return self._upgrade_query_type(endpoint, api_info, "query")
        if tokens & {"get", "detail", "details", "query", "find", "search", "list", "page", "pages", "index"}:
            return self._upgrade_query_type(endpoint, api_info, "query")
        return None

    def _extract_json_payload(self, reply):
        if isinstance(reply, dict):
            return reply
        if isinstance(reply, list):
            return reply
        raw_text = str(reply).strip()
        fenced_payload = self.jsontool.list_formatting(raw_text)
        text = fenced_payload.strip() if fenced_payload else raw_text
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            object_start = text.find("{")
            object_end = text.rfind("}")
            array_start = text.find("[")
            array_end = text.rfind("]")

            candidates = []
            if object_start != -1 and object_end != -1 and object_end > object_start:
                candidates.append(text[object_start : object_end + 1])
            if array_start != -1 and array_end != -1 and array_end > array_start:
                candidates.append(text[array_start : array_end + 1])

            for candidate in candidates:
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    continue
            raise

    def _normalize_api_type_results(self, payload):
        if not isinstance(payload, dict) or "results" not in payload:
            raise ValueError("LLM 返回结果不符合标准 schema：缺少顶层 results 数组")

        results = payload.get("results")
        if not isinstance(results, list):
            raise ValueError("LLM 返回结果不符合标准 schema：results 不是数组")

        normalized = {}
        for item in results:
            if not isinstance(item, dict):
                raise ValueError("LLM 返回结果不符合标准 schema：results 中存在非对象元素")
            endpoint = str(item.get("endpoint", "")).strip()
            api_type = str(item.get("type", "")).strip()
            if not endpoint:
                raise ValueError("LLM 返回结果不符合标准 schema：存在空 endpoint")
            if api_type not in self.VALID_API_TYPES:
                raise ValueError(f"LLM 返回结果包含非法 type: endpoint={endpoint} type={api_type}")
            if endpoint in normalized:
                raise ValueError(f"LLM 返回结果包含重复 endpoint: {endpoint}")
            normalized[endpoint] = api_type
        return normalized

    def _validate_api_type_coverage(self, group_name, analysis_result, expected_endpoints):
        if not isinstance(analysis_result, dict):
            raise ValueError(f"功能组 {group_name} 的分析结果不是对象")

        expected_set = {
            str(endpoint).strip()
            for endpoint in expected_endpoints
            if isinstance(endpoint, str) and endpoint.strip()
        }
        result_set = set(analysis_result.keys())

        unexpected = sorted(result_set - expected_set)
        if unexpected:
            preview = unexpected[:10]
            raise ValueError(f"功能组 {group_name} 返回了未请求的 endpoint: {preview}")

        missing = sorted(expected_set - result_set)
        expected_count = len(expected_set)
        actual_count = len(result_set)
        coverage = (actual_count / expected_count) if expected_count else 1.0
        if expected_count and coverage < self.min_coverage:
            preview = missing[:20]
            raise ValueError(
                f"功能组 {group_name} 覆盖率不足: {actual_count}/{expected_count}={coverage:.1%}; "
                f"missing={preview}"
            )
        return {
            "expected_count": expected_count,
            "actual_count": actual_count,
            "missing": missing,
            "coverage": coverage,
        }
    

    
    def api_function_tag(self, max_workers=3):
        """
        Analyze API types per functional group in parallel (add/delete/update/query).

        Args:
            max_workers: max thread count (default 3)

        Returns:
            list: analysis results
        """
        results = []
        
        # Collect all groups
        groups_to_analyze = []
        for group_item in self.api_doc:
            for group_name, apis in group_item.items():
                groups_to_analyze.append((group_name, apis))
        
        print(f"Start multithread analysis: {len(groups_to_analyze)} groups, {max_workers} threads")
        
        # Execute analysis tasks using thread pool
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit tasks
            future_to_group = {
                executor.submit(self._analyze_group, group_name, apis): group_name 
                for group_name, apis in groups_to_analyze
            }
            
            # Collect results
            for future in as_completed(future_to_group):
                group_name = future_to_group[future]
                try:
                    result = future.result()
                    results.append(result)
                    
                    # Thread-safe progress output
                    with self.lock:
                        if "error" in result:
                            print(f"Group {group_name} analysis failed: {result['error']}")
                        else:
                            print(f"Group {group_name} analysis succeeded, {len(result['analysis_result'])} endpoints")
                            
                except Exception as exc:
                    with self.lock:
                        print(f"Group {group_name} raised exception: {exc}")
                    results.append({
                        "group_name": group_name,
                        "analysis_result": {},
                        "error": str(exc)
                    })
        
        print(f"Multithread analysis completed. Successfully analyzed {len([r for r in results if 'error' not in r])} groups")
        self.tag_results = results
        # return results
    
    def _analyze_group(self, group_name, apis):
        """
        Analyze API types for a single group (thread-safe worker).

        Args:
            group_name: group name
            apis: API dict for the group

        Returns:
            dict containing group_name and analysis_result
        """
        with self.lock:
            print(f"Analyzing group: {group_name}")
        
        # Create per-thread local data to avoid shared state
        rule_result = {}
        unresolved_apis = {}
        for endpoint, api_info in apis.items():
            if not isinstance(endpoint, str) or not endpoint.strip():
                continue
            api_type = self._classify_api_type_by_rule(endpoint, api_info if isinstance(api_info, dict) else {})
            if api_type:
                rule_result[endpoint] = api_type
            else:
                unresolved_apis[endpoint] = api_info

        if not unresolved_apis:
            return {
                "group_name": group_name,
                "analysis_result": rule_result,
                "coverage": 1.0,
                "expected_count": len(rule_result),
                "actual_count": len(rule_result),
            }

        expected_endpoints = [
            endpoint
            for endpoint in unresolved_apis.keys()
            if isinstance(endpoint, str) and endpoint.strip()
        ]
        local_test_info_dict = {
            "api_data": {group_name: unresolved_apis},
            "expected_endpoints": json.dumps(expected_endpoints, ensure_ascii=False, indent=2),
            "retry_notice": "None",
        }
        
        # Call GPT to analyze the current group, with retries
        max_retries = 3
        last_error = None
        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    local_test_info_dict["retry_notice"] = (
                        "Previous output was invalid or incomplete.\n"
                        + f"Validation error: {last_error}\n"
                        + "Regenerate the full JSON object from scratch.\n"
                        + "You must return a standard JSON object with top-level `results`.\n"
                        + "Every endpoint in Expected Endpoints must appear exactly once.\n"
                        + "Do not omit endpoints. Do not invent extra endpoints."
                    )
                formatted_result = self.gpt_reply.getreply_json_schema(
                    self.syn_prompt.synthesis_prompt("api_function_type_judge", local_test_info_dict),
                    schema_name="api_function_type_judge",
                    schema=self.API_TYPE_JUDGE_SCHEMA,
                )
                formatted_result = self._normalize_api_type_results(formatted_result)
                for endpoint, api_type in list(formatted_result.items()):
                    api_info = unresolved_apis.get(endpoint, {})
                    formatted_result[endpoint] = self._upgrade_query_type(
                        endpoint,
                        api_info if isinstance(api_info, dict) else {},
                        api_type,
                    )
                coverage_info = self._validate_api_type_coverage(
                    group_name,
                    formatted_result,
                    expected_endpoints,
                )
                merged_result = dict(rule_result)
                merged_result.update(formatted_result)
                all_expected = list(apis.keys())
                merged_coverage_info = self._validate_api_type_coverage(
                    group_name,
                    merged_result,
                    all_expected,
                )
                
                result = {
                    "group_name": group_name,
                    "analysis_result": merged_result,
                    "coverage": merged_coverage_info["coverage"],
                    "expected_count": merged_coverage_info["expected_count"],
                    "actual_count": merged_coverage_info["actual_count"],
                }
                
                with self.lock:
                    print(f"Group {group_name} analysis completed")
                
                return result
                
            except Exception as e:
                last_error = str(e)
                with self.lock:
                    print(f"Error analyzing group {group_name} (attempt {attempt + 1}/{max_retries}): {str(e)}")
                if attempt == max_retries - 1:
                    # Last attempt failed; return error result
                    return {
                        "group_name": group_name,
                        "analysis_result": {},
                        "error": str(e)
                    }
    
    def api_taging_packages(self):
        """
        Merge API type analysis results into API documentation.

        Args:
            tag_results: API type analysis results, e.g.:
                [{'group_name': 'Identity / Auth', 'analysis_result': {'POST /api/...': 'add', ...}}, ...]
        """
        def api_parameters_fills(apis_data_add,all_apis):
            self.initial_test_info_dict["apis_data"] = str(all_apis)
            self.initial_test_info_dict["apis_data_add"] = str(apis_data_add)
            while True:
                try:
                    tmp_response_data = self.gpt_reply.getreply(
                        self.syn_prompt.synthesis_prompt("parameters_fills",self.initial_test_info_dict)
                        )
                    response_data = eval(self.jsontool.list_formatting(tmp_response_data))
                    break
                except Exception as e:
                    pass

            return response_data
        if not self.tag_results:
            print("Warning: no analysis results found, run api_function_tag() first")
            return self.api_doc
            
        # Iterate over analysis results
        for tag_result in self.tag_results:
            if 'error' in tag_result:
                print(f"Skip group with errors: {tag_result['group_name']}")
                continue
                
            group_name = tag_result['group_name']
            analysis_result = tag_result['analysis_result']
            
            print(f"Adding type tags for group {group_name}...")
            
            # Find corresponding group in API doc
            for group_item in self.api_doc:
                if group_name in group_item:
                    apis = group_item[group_name]
                    
                    # Add type field for each API
                    for api_path, api_type in analysis_result.items():
                        if api_path in apis:
                            # Ensure API entry is a dict
                            if isinstance(apis[api_path], dict):
                                apis[api_path]['type'] = api_type
                                # if api_type == "add":
                                #      apis[api_path].setdefault('response_parameters', {}).update(api_parameters_fills(apis[api_path], apis) or {})
                                print(f"  Added type for {api_path}: {api_type}")
                            else:
                                # If not dict, convert to dict format
                                apis[api_path] = {'type': api_type}
                                print(f"  Created and added type for {api_path}: {api_type}")
                        else:
                            print(f"  Warning: API {api_path} not found in API doc")
                    
                    break
            else:
                print(f"Warning: group {group_name} not found in API doc")
        
        print("Type tagging completed!")
        return self.api_doc
    
    def validate_and_correct_list_query_type(self, api_doc):
        """
        后处理验证：纠正 LLM 对 "list query" vs "query" 的错误分类。
        
        规则：
        1. 如果响应参数包含数组标记 [] → 保持 "list query"
        2. 如果请求参数包含分页参数 (page, pageSize, limit, offset 等) → 保持 "list query"
        3. 否则，"list query" 应该被纠正为 "query"
        """
        pagination_params = {'page', 'pagesize', 'limit', 'offset', 'size', 'pagenumber', 
                            'pagenum', 'per_page', 'perpage', 'start', 'count'}
        corrected_count = 0
        
        for group_item in api_doc:
            if not isinstance(group_item, dict):
                continue
            for group_name, apis in group_item.items():
                if not isinstance(apis, dict):
                    continue
                for api_path, api_info in apis.items():
                    if not isinstance(api_info, dict):
                        continue
                    
                    api_type = api_info.get('type')
                    if api_type != 'list query':
                        continue
                    
                    # 检查响应参数是否包含数组
                    resp_params = api_info.get('response_parameters', {})
                    has_array_response = False
                    if isinstance(resp_params, dict):
                        for param_name in resp_params.keys():
                            if '[]' in param_name or param_name.startswith('['):
                                has_array_response = True
                                break
                    
                    # 检查请求参数是否包含分页参数
                    req_params = api_info.get('request_parameters', {})
                    has_pagination = False
                    if isinstance(req_params, dict):
                        for param_name in req_params.keys():
                            if param_name.lower() in pagination_params:
                                has_pagination = True
                                break
                    has_list_endpoint = bool(self._endpoint_tokens(api_path) & {"list", "search", "page", "pages", "index"})
                    
                    # 如果既没有数组响应也没有分页参数，纠正为 "query"
                    if not has_array_response and not has_pagination and not has_list_endpoint:
                        api_info['type'] = 'query'
                        corrected_count += 1
                        print(f"  Corrected {api_path}: 'list query' -> 'query' (no array response, no pagination)")
        
        if corrected_count > 0:
            print(f"Type validation completed: corrected {corrected_count} 'list query' -> 'query'")
        else:
            print("Type validation completed: no corrections needed")
        
        return api_doc

    def complete_api_tagging_by_static_rules(self):
        """
        Tag every endpoint with deterministic method/path rules only.
        This is used for ablation experiments where API type classification
        should not call the LLM.
        """
        tagged_count = 0
        total_apis = 0
        for group_item in self.api_doc:
            if not isinstance(group_item, dict):
                continue
            for _, apis in group_item.items():
                if not isinstance(apis, dict):
                    continue
                for endpoint, api_info in apis.items():
                    total_apis += 1
                    if not isinstance(api_info, dict):
                        apis[endpoint] = api_info = {}
                    api_type = self._classify_api_type_by_rule(endpoint, api_info)
                    if not api_type:
                        method, _ = self._split_endpoint(endpoint)
                        if method == "POST":
                            api_type = "add"
                        elif method == "DELETE":
                            api_type = "delete"
                        elif method in {"PUT", "PATCH"}:
                            api_type = "update"
                        else:
                            api_type = "query"
                    api_info["type"] = self._upgrade_query_type(endpoint, api_info, api_type)
                    tagged_count += 1

        tagged_api_doc = self.validate_and_correct_list_query_type(self.api_doc)
        print(f"Static API type tagging completed: total APIs {total_apis}, tagged {tagged_count}")
        return tagged_api_doc

    def complete_api_tagging_process(self, max_workers=3):
        """
        Full API tagging flow: run type analysis and return JSON with type fields
        
        Args:
            max_workers: max thread count (default 3)
            
        Returns:
            list: full API doc data with type fields
        """
        print("Starting full API tagging flow...")
        
        
        print("Step 1: Run API function type analysis...")
        self.api_function_tag(max_workers)
        
        
        if not self.tag_results:
            print("Error: API type analysis failed, cannot continue")
            return self.api_doc
        
        
        successful_groups = [r for r in self.tag_results if 'error' not in r]
        failed_groups = [r for r in self.tag_results if 'error' in r]
        
        print(f"API type analysis done: success {len(successful_groups)} groups, failed {len(failed_groups)} groups")
        
        if failed_groups:
            print("Failed groups:")
            for failed in failed_groups:
                print(f"  - {failed['group_name']}: {failed.get('error', 'Unknown error')}")
            raise RuntimeError(
                "API type analysis failed for some groups: "
                + ", ".join(failed["group_name"] for failed in failed_groups)
            )
        
        
        print("Step 2: Merge type tags into API doc...")
        tagged_api_doc = self.api_taging_packages()
        
        print("Step 2.5: Validate and correct 'list query' classifications...")
        tagged_api_doc = self.validate_and_correct_list_query_type(tagged_api_doc)
        
        print("Step 3: Validate tagging results...")
        total_apis = 0
        tagged_apis = 0
        
        for group_item in tagged_api_doc:
            for group_name, apis in group_item.items():
                for api_path, api_info in apis.items():
                    total_apis += 1
                    if isinstance(api_info, dict) and 'type' in api_info:
                        tagged_apis += 1
        
        coverage = (tagged_apis / total_apis) if total_apis else 1.0
        print(f"Tagging stats: total APIs {total_apis}, tagged {tagged_apis}, rate {coverage*100:.1f}%")
        if total_apis and coverage < self.min_coverage:
            raise RuntimeError(
                f"API tagging coverage below threshold: tagged={tagged_apis} total={total_apis} "
                f"coverage={coverage:.1%} threshold={self.min_coverage:.1%}"
            )
        
        
        print("API tagging flow completed!")
        return tagged_api_doc
    
    def api_tag_results_review(self,api_doc_type_results):
        def match_by_llm(to_be_matched_api):
            self.initial_test_info_dict["to_be_matched"] = str(to_be_matched_api)

            while True:
                try:
                    match_results = self.gpt_reply.getreply(
                    self.syn_prompt.synthesis_prompt("api_matched_judgement", self.initial_test_info_dict)
                )
                    formated_results = eval(self.jsontool.list_formatting(match_results))
                    
                    break
                except Exception as e:
                    print(e)
                    pass

            return formated_results

        
        # 1) Groups without add-type APIs
        # 2) Groups with only add-type APIs (all endpoints are add)
        groups_without_add = []
        groups_only_add = []

        # api_doc_type_results matches the structure written to api_doc_with_type.json
        # [ { group_name: { "METHOD /path": { ..., "type": "add" | "update" | ... }, ... } }, ... ]
        for group_item in api_doc_type_results:
            if not isinstance(group_item, dict):
                continue
            for group_name, apis in group_item.items():
                if not isinstance(apis, dict):
                    continue

                total_apis = 0
                typed_count = 0
                add_count = 0

                for api_path, api_info in apis.items():
                    total_apis += 1
                    if isinstance(api_info, dict):
                        api_type = api_info.get("type")
                        if api_type is not None:
                            typed_count += 1
                            if api_type == "add":
                                add_count += 1

                # No add-type APIs
                if add_count == 0:
                    groups_without_add.append({
                        "group_name": group_name,
                        "apis": apis
                    })

                # Only add-type APIs: all endpoints must be tagged and type must be add
                if total_apis > 0 and typed_count == total_apis and add_count == typed_count:
                    groups_only_add.append({
                        "group_name": group_name,
                        "apis": apis
                    })
        to_be_matched_api = {
            "groups_without_add": groups_without_add,
            "groups_only_add": groups_only_add,
        }
        # print(to_be_matched_api)
        match_results = match_by_llm(to_be_matched_api)

        # Merge groups based on match_results
        # match_results may be [\"order\",\"saveorder\"] or [[\"order\",\"saveorder\"],[\"groupA\",\"groupB\"]]
        # Build name->apis mapping and preserve original order
        group_map = {}
        original_order = []
        for item in api_doc_type_results:
            if isinstance(item, dict):
                for name, apis in item.items():
                    group_map[name] = apis if isinstance(apis, dict) else {}
                    original_order.append(name)

        
        sets_to_merge = []
        if isinstance(match_results, list):
            if all(isinstance(x, str) for x in match_results):
                sets_to_merge = [match_results]
            elif all(isinstance(x, (list, tuple)) for x in match_results):
                sets_to_merge = [list(s) for s in match_results]

        
        for names in sets_to_merge:
            valid_names = [n for n in names if n in group_map]
            if len(valid_names) <= 1:
                continue
            primary = valid_names[0]
            merged_apis = {}
            for n in valid_names:
                apis_dict = group_map.get(n, {})
                for api_path, api_info in apis_dict.items():
                    if api_path not in merged_apis:
                        merged_apis[api_path] = api_info
            
            group_map[primary] = merged_apis
            for n in valid_names[1:]:
                if n in group_map:
                    del group_map[n]

        
        merged_api_doc_type_results = []
        seen = set()
        for name in original_order:
            if name in group_map and name not in seen:
                merged_api_doc_type_results.append({name: group_map[name]})
                seen.add(name)
        
        for name, apis in group_map.items():
            if name not in seen:
                merged_api_doc_type_results.append({name: apis})
                seen.add(name)

        return merged_api_doc_type_results
    
    def api_tags_review_v2(self,api_doc_type_results):
        def description_generated_by_llm(group_apis):
            
            local_info_dict = dict(self.initial_test_info_dict)
            local_info_dict["api_data"] = str(group_apis)
            while True:
                try:
                    match_results = self.gpt_reply.getreply(
                    self.syn_prompt.synthesis_prompt("api_description_generation", local_info_dict)
                )
                    formated_results = match_results
                    break
                except Exception as e:
                    print(e)
                    pass
            return formated_results
        
        def combine_similarity_group(api_data):
            self.initial_test_info_dict["api_description"] = str(api_data)
            while True:
                try:
                    match_results = self.gpt_reply.getreply(
                    self.syn_prompt.synthesis_prompt("api_group_similarity_combine", self.initial_test_info_dict)
                )
                    formated_results = eval(self.jsontool.list_formatting(match_results))
                    break
                except Exception as e:
                    print(e)
                    pass
            return formated_results

        def deal_with_isolated_api(api_doc_type_results):
            if not isinstance(api_doc_type_results, list):
                return api_doc_type_results
            groups_info = []
            for item in api_doc_type_results:
                if isinstance(item, dict):
                    for gname, apis in item.items():
                        groups_info.append((gname, apis if isinstance(apis, dict) else {}))
            groups_without_add = {}
            import re
            def _norm_names(name):
                s = str(name)
                s1 = re.sub(r"\[\]+$", "", s)
                last = s1.split(".")[-1] if s1 else s1
                last1 = re.sub(r"\[\]+$", "", last)
                last1 = re.sub(r"^\{(.+)\}$", r"\1", last1)
                names = {s1, last, last1}
                return {n for n in names if isinstance(n, str) and n}
            for gname, apis in groups_info:
                has_add = False
                for api_key, api_info in apis.items():
                    if isinstance(api_info, dict) and (api_info.get("type", "") == "add"):
                        has_add = True
                        break
                if not has_add:
                    params_set = set()
                    for api_key, api_info in apis.items():
                        if isinstance(api_info, dict):
                            reqp = api_info.get("request_parameters", {})
                            if isinstance(reqp, dict):
                                for pname in reqp.keys():
                                    for nn in _norm_names(pname):
                                        params_set.add(nn)
                    if params_set:
                        groups_without_add[gname] = params_set
            if not groups_without_add:
                return api_doc_type_results
            add_groups = {}
            for gname, apis in groups_info:
                add_endpoints = []
                for api_key, api_info in apis.items():
                    if not isinstance(api_info, dict):
                        continue
                    if api_info.get("type", "") != "add":
                        continue
                    resp = api_info.get("response_parameters", {})
                    resp_keys = set()
                    if isinstance(resp, dict):
                        for rk in resp.keys():
                            for nn in _norm_names(rk):
                                resp_keys.add(nn)
                    add_endpoints.append((api_key, resp_keys))
                if add_endpoints:
                    add_groups[gname] = add_endpoints
            if not add_groups:
                return api_doc_type_results
            updated = []
            for item in api_doc_type_results:
                if not isinstance(item, dict):
                    updated.append(item)
                    continue
                new_item = {}
                for gname, apis in item.items():
                    apis_dict = apis if isinstance(apis, dict) else {}
                    inherited = set()
                    if gname in add_groups:
                        for src_group, params_set in groups_without_add.items():
                            for pname in params_set:
                                for api_key, resp_keys in add_groups[gname]:
                                    if pname in resp_keys:
                                        inherited.add(pname)
                                        api_info = apis_dict.get(api_key)
                                        if isinstance(api_info, dict):
                                            lst = api_info.get("inherited_params")
                                            if not isinstance(lst, list):
                                                lst = []
                                            if pname not in lst:
                                                lst.append(pname)
                                            api_info["inherited_params"] = lst
                    if inherited:
                        meta = apis_dict.get("__inherited_params__")
                        if not isinstance(meta, list):
                            meta = []
                        for p in sorted(inherited):
                            if p not in meta:
                                meta.append(p)
                        apis_dict["__inherited_params__"] = meta
                    new_item[gname] = apis_dict
                updated.append(new_item)
            return updated

        api_doc_type_results = deal_with_isolated_api(api_doc_type_results)

        group_descprtion = {}
        
        groups_to_describe = []
        for group_apis in api_doc_type_results:
            if isinstance(group_apis, dict):
                for group_name, group_value in group_apis.items():
                    groups_to_describe.append((group_name, group_apis))
        max_workers = 3
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_group = {
                executor.submit(description_generated_by_llm, group_apis): group_name
                for group_name, group_apis in groups_to_describe
            }
            for future in as_completed(future_to_group):
                group_name = future_to_group[future]
                try:
                    desc = future.result()
                    group_descprtion[group_name] = desc
                except Exception as exc:
                    print(f"Failed to generate group description: {group_name}, {exc}")

        combine_results_list = combine_similarity_group(group_descprtion)

        # Merge groups based on combine_results_list
        # Build name->apis mapping and preserve original order
        group_map = {}
        original_order = []
        for item in api_doc_type_results:
            if isinstance(item, dict):
                for name, apis in item.items():
                    group_map[name] = apis if isinstance(apis, dict) else {}
                    original_order.append(name)

        
        sets_to_merge = []
        if isinstance(combine_results_list, list):
            if all(isinstance(x, str) for x in combine_results_list):
                sets_to_merge = [combine_results_list]
            elif all(isinstance(x, (list, tuple)) for x in combine_results_list):
                sets_to_merge = [list(s) for s in combine_results_list]

        
        for names in sets_to_merge:
            valid_names = [n for n in names if n in group_map]
            if len(valid_names) <= 1:
                continue
            primary = valid_names[0]
            merged_apis = {}
            for n in valid_names:
                apis_dict = group_map.get(n, {})
                for api_path, api_info in apis_dict.items():
                    if api_path not in merged_apis:
                        merged_apis[api_path] = api_info
            
            group_map[primary] = merged_apis
            for n in valid_names[1:]:
                if n in group_map:
                    del group_map[n]

        
        merged_api_doc_type_results = []
        seen = set()
        for name in original_order:
            if name in group_map and name not in seen:
                merged_api_doc_type_results.append({name: group_map[name]})
                seen.add(name)
        
        for name, apis in group_map.items():
            if name not in seen:
                merged_api_doc_type_results.append({name: apis})
                seen.add(name)

        return merged_api_doc_type_results

if __name__ == "__main__":
    import os
    jsontools = JsonTools()
    project_name = "mall"
    
    # 获取项目根目录
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
    cache_dir = os.path.join(project_root, 'cache', project_name)
    
    api_doc_path = os.path.join(cache_dir, "openapi_formated.json")
    model = "gpt-4o-mini"
    
    grouping_strategy = "resource_crud"
    api_data_tag = ApiDataTagging(api_doc_path, model, grouping_strategy)
    
    try:
        max_workers = int(os.getenv("BOLASCAN_LLM_MAX_WORKERS", "5"))
    except Exception:
        max_workers = 5
    api_doc_with_types = api_data_tag.complete_api_tagging_process(max_workers=max(1, max_workers))
    # api_doc_with_types = jsontools.read_json(os.path.join(cache_dir, "api_doc_with_type.json"))
    api_match_results = api_data_tag.api_tag_results_review(api_doc_with_types)
    
    output_path = os.path.join(cache_dir, "api_doc_with_type.json")
    jsontools.write_json(output_path, api_match_results)
    print(f"Full API type tagging data saved to: {output_path}")
