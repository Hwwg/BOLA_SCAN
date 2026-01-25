from re import T
from venv import logger
from urllib3.util import response
from scripts.api_doc import ApiDoc
from scripts.jsontools import JsonTools

from prompt.synthesis_prompt import SyntheticPrompt
from gptreply.gpt_con import GPTReply

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import sys,os

# from utils.dependency_cc.src.case_generation_v2 import js

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

"""
Extract API doc data, tag each endpoint with CRUD type, and output.
Tagged results: self.api_doc
"""
class ApiDataTagging:
    def __init__(self,api_doc_path,model, grouping_strategy: str = 'auto') -> None:
        self.api_doc_tool = ApiDoc(api_doc_path,model)
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
        local_test_info_dict = {
            "api_data": {group_name: apis}
        }
        
        # Call GPT to analyze the current group, with retries
        max_retries = 3
        for attempt in range(max_retries):
            try:
                tmp_result = self.gpt_reply.getreply(
                    self.syn_prompt.synthesis_prompt("api_function_type_judge", local_test_info_dict)
                )
                formatted_result = eval(self.jsontool.list_formatting(tmp_result))
                
                result = {
                    "group_name": group_name,
                    "analysis_result": formatted_result
                }
                
                with self.lock:
                    print(f"Group {group_name} analysis completed")
                
                return result
                
            except Exception as e:
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
                    
                    # 如果既没有数组响应也没有分页参数，纠正为 "query"
                    if not has_array_response and not has_pagination:
                        api_info['type'] = 'query'
                        corrected_count += 1
                        print(f"  Corrected {api_path}: 'list query' -> 'query' (no array response, no pagination)")
        
        if corrected_count > 0:
            print(f"Type validation completed: corrected {corrected_count} 'list query' -> 'query'")
        else:
            print("Type validation completed: no corrections needed")
        
        return api_doc

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
        
        print(f"Tagging stats: total APIs {total_apis}, tagged {tagged_apis}, rate {tagged_apis/total_apis*100:.1f}%")
        
        
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
    jsontools = JsonTools()
    project_name = "mall"
    api_doc_path = f"/Users/tlif3./zju_research/bolascan_v3/bolascan_v4/cache/{project_name}/openapi_formated.json"
    model = "gpt-4o-mini"
    
    grouping_strategy = "resource_crud"
    api_data_tag = ApiDataTagging(api_doc_path, model, grouping_strategy)
    
    
    api_doc_with_types = api_data_tag.complete_api_tagging_process(max_workers=5)
    # api_doc_with_types = jsontools.read_json( "/Users/tlif3./zju_research/bolascan_v3/bolascan_v4/cache/newbee_mall/api_doc_with_type.json")
    api_match_results = api_data_tag.api_tag_results_review(api_doc_with_types)
    
    output_path = f"/Users/tlif3./zju_research/bolascan_v3/bolascan_v4/cache/{project_name}/api_doc_with_type.json"
    jsontools.write_json(output_path, api_match_results)
    print(f"Full API type tagging data saved to: {output_path}")