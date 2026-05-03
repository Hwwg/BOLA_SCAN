"""
资源识别模块
负责识别API中的资源参数和容器参数
"""
import logging
import os
import random
from dataclasses import asdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, Set, List, Optional
from .utils_helpers import flatten_list
from utils.param_path import (
    ParameterOccurrence,
    any_param_matches,
    identifier_names_in_path,
    is_identifier_name,
    occurrence_for,
    path_container_params,
    parse_path_identifier_order,
)

logger = logging.getLogger(__name__)

# LLM 控制配置（可通过环境变量调整）
try:
    _llm_max_retries = int(os.getenv('BOLASCAN_LLM_MAX_RETRIES', '3'))
except Exception:
    _llm_max_retries = 3
_llm_disabled = str(os.getenv('BOLASCAN_DISABLE_LLM', 'false')).lower() in ('1', 'true', 'yes', 'on')


class ResourceIdentifier:
    """资源参数识别器"""
    
    def __init__(self, true_params, normalized_params, case_generation_results_packages, 
                 gpt_reply, syn_prompt, jsontool, llm_dict):
        """
        初始化资源识别器
        
        Args:
            true_params: API文档数据
            normalized_params: 规范化的参数数据
            case_generation_results_packages: 用例生成结果包
            gpt_reply: GPT回复对象
            syn_prompt: 提示词生成对象
            jsontool: JSON工具对象
            llm_dict: LLM字典配置
        """
        self.true_params = true_params
        self.normalized_params = normalized_params
        self.case_generation_results_packages = case_generation_results_packages
        self.gpt_reply = gpt_reply
        self.syn_prompt = syn_prompt
        self.jsontool = jsontool
        self.llm_dict = llm_dict
        self._last_oip_result: Dict[str, Any] | None = None
        self._last_occurrence_result: Dict[str, Any] | None = None

    def _iter_groups(self):
        if isinstance(self.true_params, dict):
            return list(self.true_params.items())
        if isinstance(self.true_params, list):
            groups_iter = []
            for group_item in self.true_params:
                if isinstance(group_item, dict):
                    groups_iter.extend(group_item.items())
            return groups_iter
        return []

    def build_oip_candidates(self, include_llm: bool = True) -> Dict[str, Any]:
        """Build the OIP set as rule-based identifiers union LLM semantic additions."""
        by_group: Dict[str, set[str]] = {}
        rule_by_group: Dict[str, set[str]] = {}
        llm_by_group: Dict[str, set[str]] = {}
        candidate_meta_by_group: Dict[str, Dict[str, Any]] = {}

        for group_name, apis in self._iter_groups():
            if isinstance(apis, dict):
                iterable_items = list(apis.items())
            elif isinstance(apis, list):
                iterable_items = []
                for api_dict in apis:
                    if isinstance(api_dict, dict):
                        iterable_items.extend(api_dict.items())
            else:
                iterable_items = []

            request_params_all: set[str] = set()
            response_params_all: set[str] = set()
            for endpoint, params in iterable_items:
                request_params_all.update(self._extract_request_param_names(params))
                response_params_all.update(self._extract_response_param_names(params))
                for path_param, _ in parse_path_identifier_order(endpoint):
                    request_params_all.add(path_param)

            rule_oips = {p for p in request_params_all if is_identifier_name(p)}
            for param in request_params_all | response_params_all:
                rule_oips.update(identifier_names_in_path(param))
            for req_param in request_params_all:
                if any_param_matches(req_param, response_params_all) and is_identifier_name(req_param):
                    rule_oips.add(req_param)

            semantic_candidates = sorted(
                p for p in (request_params_all & response_params_all)
                if p not in rule_oips
            )
            candidate_meta = {
                p: {
                    "example_value": None,
                    "sample_api": self._find_sample_api_for_param(apis, p),
                }
                for p in semantic_candidates
            }
            candidate_meta_by_group[group_name] = candidate_meta

            llm_selected: set[str] = set()
            if include_llm and semantic_candidates and not _llm_disabled:
                local_llm_dict = self.llm_dict.copy()
                local_llm_dict["param_dict"] = str({p: {"example_value": None} for p in semantic_candidates})
                local_llm_dict["routes_data"] = str(apis)
                try:
                    tmp_result_params = self.gpt_reply.getreply(
                        self.syn_prompt.synthesis_prompt("resource_id_judgement", local_llm_dict)
                    )
                    logger.info("功能组 %s 的 OIP 语义补充结果: %s", group_name, tmp_result_params)
                    llm_selected = set(self._parse_llm_param_list(eval(self.jsontool.list_formatting(tmp_result_params))))
                    # LLM can supplement semantic identifiers, but do not allow
                    # obvious payload/status/config fields into the OIP set.
                    deny = {
                        "amount", "count", "quantity", "number", "password", "name", "title",
                        "content", "message", "status", "state", "token", "code", "key",
                        "coupon_code", "conversion_params", "problem_details", "pincode",
                    }
                    llm_selected = {p for p in llm_selected if p not in deny}
                except Exception as exc:
                    logger.info("功能组 %s 的 OIP 语义补充失败: %s", group_name, exc)

            merged = set(rule_oips) | llm_selected
            by_group[group_name] = merged
            rule_by_group[group_name] = set(rule_oips)
            llm_by_group[group_name] = llm_selected

        result = {
            "by_group": {g: sorted(v) for g, v in by_group.items() if v},
            "rule_by_group": {g: sorted(v) for g, v in rule_by_group.items() if v},
            "llm_by_group": {g: sorted(v) for g, v in llm_by_group.items() if v},
            "all": sorted({p for values in by_group.values() for p in values}),
        }
        self._last_oip_result = result
        return result

    def _extract_request_param_names(self, params: Any) -> Set[str]:
        """统一提取请求参数名，兼容新旧结构。"""
        request_params_obj = params.get('request_parameters', params.get('request_para', {})) if isinstance(params, dict) else {}
        if isinstance(request_params_obj, dict):
            return {key for key in request_params_obj.keys() if isinstance(key, str)}
        if isinstance(request_params_obj, list):
            return {item for item in flatten_list(request_params_obj) if isinstance(item, str)}
        return set()

    def _extract_response_param_names(self, params: Any) -> Set[str]:
        response_params_obj = params.get('response_parameters', params.get('response_para', {})) if isinstance(params, dict) else {}
        if isinstance(response_params_obj, dict):
            return {key for key in response_params_obj.keys() if isinstance(key, str)}
        if isinstance(response_params_obj, list):
            return {item for item in flatten_list(response_params_obj) if isinstance(item, str)}
        return set()

    def _collect_identifier_occurrences(self) -> Dict[str, List[ParameterOccurrence]]:
        occurrences: Dict[str, List[ParameterOccurrence]] = {}

        if isinstance(self.true_params, dict):
            groups_iter = list(self.true_params.items())
        elif isinstance(self.true_params, list):
            groups_iter = []
            for group_item in self.true_params:
                if isinstance(group_item, dict):
                    groups_iter.extend(group_item.items())
        else:
            groups_iter = []

        for group_name, apis in groups_iter:
            if isinstance(apis, dict):
                iterable_items = list(apis.items())
            elif isinstance(apis, list):
                iterable_items = []
                for api_dict in apis:
                    if isinstance(api_dict, dict):
                        iterable_items.extend(api_dict.items())
            else:
                iterable_items = []

            for endpoint, api_data in iterable_items:
                if not isinstance(api_data, dict):
                    continue
                api_type = api_data.get("type", "")

                for path_param in path_container_params(endpoint) | {p for p, _ in parse_path_identifier_order(endpoint)}:
                    for identifier_name in identifier_names_in_path(path_param):
                        occurrences.setdefault(identifier_name, []).append(
                            occurrence_for(path_param, "path", endpoint, api_type, identifier_name)
                        )

                req_obj = api_data.get("request_parameters", api_data.get("request_para", {}))
                if isinstance(req_obj, dict):
                    for pname, pinfo in req_obj.items():
                        loc = pinfo.get("in", "body") if isinstance(pinfo, dict) else "body"
                        for identifier_name in identifier_names_in_path(pname):
                            occurrences.setdefault(identifier_name, []).append(
                                occurrence_for(pname, loc, endpoint, api_type, identifier_name)
                            )

                resp_obj = api_data.get("response_parameters", api_data.get("response_para", {}))
                if isinstance(resp_obj, dict):
                    for pname in resp_obj.keys():
                        for identifier_name in identifier_names_in_path(pname):
                            occurrences.setdefault(identifier_name, []).append(
                                occurrence_for(pname, "response", endpoint, api_type, identifier_name)
                            )
                elif isinstance(resp_obj, list):
                    for pname in flatten_list(resp_obj):
                        if not isinstance(pname, str):
                            continue
                        for identifier_name in identifier_names_in_path(pname):
                            occurrences.setdefault(identifier_name, []).append(
                                occurrence_for(pname, "response", endpoint, api_type, identifier_name)
                            )
        return occurrences

    def _coip_candidates_by_hierarchy(self, oip_set: Optional[Set[str]] = None) -> Set[str]:
        occurrences = self._collect_identifier_occurrences()
        coip = set()
        for param, occs in occurrences.items():
            if oip_set is not None and param not in oip_set:
                continue
            for occ in occs:
                if occ.location in {"body", "response"} and occ.structural_level > 1:
                    coip.add(param)
                elif occ.location == "path" and param in path_container_params(occ.endpoint):
                    coip.add(param)
        return coip

    def build_identifier_hierarchy_report(self, oip_set: Optional[Set[str]] = None) -> Dict[str, Any]:
        occurrences = self._collect_identifier_occurrences()
        report = {}
        for param, occs in sorted(occurrences.items()):
            if oip_set is not None and param not in oip_set:
                continue
            report[param] = [asdict(occ) for occ in occs]
        self._last_occurrence_result = report
        return report

    def _find_sample_api_for_param(self, apis: Any, param_name: str) -> Dict[str, Any]:
        """为参数找一个示例 API，供二次补判使用。"""
        candidates: List[Dict[str, Any]] = []
        iterable_items = []
        if isinstance(apis, dict):
            iterable_items = list(apis.items())
        elif isinstance(apis, list):
            for api_dict in apis:
                if isinstance(api_dict, dict):
                    iterable_items.extend(api_dict.items())

        for endpoint, params in iterable_items:
            request_params = sorted(self._extract_request_param_names(params))
            if param_name in request_params:
                candidates.append(
                    {
                        "endpoint": endpoint,
                        "request_params": request_params,
                        "type": params.get("type", "") if isinstance(params, dict) else "",
                    }
                )
        return random.choice(candidates) if candidates else {}

    def _parse_llm_param_list(self, payload: Any) -> List[str]:
        """尽量把 LLM 输出收敛为字符串列表。"""
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, str)]
        if isinstance(payload, dict):
            for value in payload.values():
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, str)]
        return []

    def _recheck_missing_resource_id_params(
        self,
        result: Dict[str, Any],
        candidate_meta_by_group: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        """对首轮未命中的 id 后缀参数做一次补判。"""
        if _llm_disabled:
            return result

        for group_name, candidate_meta in candidate_meta_by_group.items():
            selected = set(self._parse_llm_param_list(result.get(group_name, [])))
            missing = [param for param in candidate_meta.keys() if param.lower().endswith("id") and param not in selected]
            if not missing:
                continue

            local_llm_dict = dict(self.llm_dict)
            local_llm_dict["group_name"] = group_name
            local_llm_dict["missing_candidates"] = str(
                [
                    {
                        "param_name": param,
                        "example_value": candidate_meta[param].get("example_value"),
                        "sample_api": candidate_meta[param].get("sample_api", {}),
                    }
                    for param in missing
                ]
            )
            try:
                retry_reply = self.gpt_reply.getreply(
                    self.syn_prompt.synthesis_prompt("resource_id_recheck", local_llm_dict)
                )
                retry_selected = self._parse_llm_param_list(eval(self.jsontool.list_formatting(retry_reply)))
            except Exception as exc:
                logger.info("功能组 %s 的资源参数补判失败: %s", group_name, exc)
                retry_selected = []

            if retry_selected:
                merged = sorted(selected | set(retry_selected))
                result[group_name] = merged
                logger.info("功能组 %s 的资源参数补判追加结果: %s", group_name, retry_selected)
        return result

    def _recheck_missing_container_params(
        self,
        selected_params: Any,
        candidate_meta: Dict[str, Any],
    ) -> List[str]:
        """对首轮未命中的容器候选参数做一次补判。"""
        selected = set(self._parse_llm_param_list(selected_params))
        missing = [param for param in candidate_meta.keys() if param not in selected]
        if _llm_disabled or not missing:
            return sorted(selected)

        local_llm_dict = dict(self.llm_dict)
        local_llm_dict["missing_candidates"] = str(
            [
                {
                    "param_name": param,
                    "sample_api": candidate_meta[param].get("sample_api", {}),
                    "groups": candidate_meta[param].get("groups", []),
                }
                for param in missing
            ]
        )
        try:
            retry_reply = self.gpt_reply.getreply(
                self.syn_prompt.synthesis_prompt("container_resource_recheck", local_llm_dict)
            )
            retry_selected = self._parse_llm_param_list(eval(self.jsontool.list_formatting(retry_reply)))
        except Exception as exc:
            logger.info("容器参数补判失败: %s", exc)
            retry_selected = []

        allowed = set(candidate_meta.keys())
        retry_selected = [p for p in retry_selected if p in allowed]
        merged = sorted(selected | set(retry_selected))
        if retry_selected:
            logger.info("容器参数补判追加结果: %s", retry_selected)
        return merged
    
    def data_resource(self) -> Dict[str, Any]:
        """
        计算A交B-（A-B）的参数，其中A是请求参数，B是响应参数
        返回按功能组划分的结果
        计算这个集合的结果是发现有多少资源id，但其中可能也是容器资源id
        使用多线程并行处理各个功能组
        
        Returns:
            按功能组划分的资源参数识别结果
        """
        oip_result = self.build_oip_candidates(include_llm=True)
        return oip_result.get("by_group", {})

        def _process_group(group_name, apis, parameters_data):
            """处理单个功能组的资源参数计算"""
            # A集合：该功能组所有API的请求参数
            all_request_params = set()
            # B集合：该功能组所有API的响应参数
            all_response_params = set()
            
            # 收集该功能组下所有API的请求参数和响应参数
            # 适配新格式(api_doc_with_type.json 为 dict)与旧格式(为 list[dict])
            if isinstance(apis, dict):
                iterable_items = apis.items()
            elif isinstance(apis, list):
                iterable_items = []
                for api_dict in apis:
                    if isinstance(api_dict, dict):
                        iterable_items.extend(api_dict.items())
            else:
                iterable_items = []

            for endpoint, params in iterable_items:
                # 同时兼容新旧键名：新为 request_parameters/response_parameters，旧为 request_para/response_para
                request_params_obj = params.get('request_parameters', params.get('request_para', {}))
                response_params_obj = params.get('response_parameters', params.get('response_para', {}))

                # 归一化为集合
                if isinstance(request_params_obj, dict):
                    request_params = set(request_params_obj.keys())
                elif isinstance(request_params_obj, list):
                    _flat_req = flatten_list(request_params_obj)
                    request_params = set([x for x in _flat_req if isinstance(x, str)])
                else:
                    request_params = set()

                if isinstance(response_params_obj, dict):
                    response_params = set(response_params_obj.keys())
                elif isinstance(response_params_obj, list):
                    _flat_resp = flatten_list(response_params_obj)
                    response_params = set([x for x in _flat_resp if isinstance(x, str)])
                else:
                    response_params = set()

                all_request_params.update(request_params)
                all_response_params.update(response_params)
            
            # 对该功能组进行集合运算：A∩B-(A-B)
            # A∩B：请求参数和响应参数的交集
            intersection_params = all_request_params & all_response_params
            rule_identifier_params = {
                param for param in all_request_params
                if isinstance(param, str) and is_identifier_name(param)
            }
            for req_param in all_request_params:
                if isinstance(req_param, str) and any_param_matches(req_param, all_response_params):
                    rule_identifier_params.add(req_param)
            intersection_params.update(rule_identifier_params)
            print(f"功能组 {group_name} 的参数: {intersection_params}")

            # 为每个功能组创建临时字典，从execution_results.json中匹配参数值
            temp_dict = {}
            candidate_meta = {}
            if intersection_params:
                for param_name in intersection_params:
                    # 从parameters_data中查找匹配的参数值
                    param_value = _find_parameter_value(parameters_data, group_name, param_name)
                    temp_dict[param_name] = {"example_value": param_value}
                    candidate_meta[param_name] = {
                        "example_value": param_value,
                        "sample_api": self._find_sample_api_for_param(apis, param_name),
                    }

            # 使用大模型来判断，哪些是用户资源id
            # 创建线程本地的llm_dict副本
            local_llm_dict = self.llm_dict.copy()
            local_llm_dict["param_dict"] = str(temp_dict)
            # 仅保留路由数据中除 response_parameters 之外的部分
            local_llm_dict["routes_data"] = str(_strip_response_parameters(apis))
            
            if not _llm_disabled:
                _last_err = None
                for _attempt in range(1, _llm_max_retries + 1):
                    try:
                        tmp_result_params = self.gpt_reply.getreply(
                            self.syn_prompt.synthesis_prompt("resource_id_judgement", local_llm_dict)
                        )
                        logger.info(f"功能组 {group_name} 的大模型结果: {tmp_result_params}")
                        result_params = eval(self.jsontool.list_formatting(tmp_result_params))
                        break
                    except Exception as e:
                        _last_err = e
                        logger.info(f"功能组 {group_name} 的LLM异常（第{_attempt}/{_llm_max_retries}次）: {str(e)}")
                else:
                    result_params = {"resource_id": [], "ou_id": []}
                    logger.warning(f"功能组 {group_name} LLM 判定失败，应用兜底: {result_params}")
            else:
                result_params = {"resource_id": [], "ou_id": []}

            if rule_identifier_params:
                if isinstance(result_params, list):
                    result_params = sorted(set(result_params) | set(rule_identifier_params))
                elif isinstance(result_params, dict):
                    current = set(self._parse_llm_param_list(result_params))
                    current.update(rule_identifier_params)
                    result_params = sorted(current)
                else:
                    result_params = sorted(rule_identifier_params)
            
            return group_name, result_params, candidate_meta
        
        def _find_parameter_value(parameters_data, group_name, param_name):
            """从execution_results.json中查找指定功能组和参数名对应的参数值"""
            if group_name not in parameters_data:
                return None
                
            group_data = parameters_data[group_name]
            
            # 遍历该功能组下的所有API执行结果
            for api_group in group_data:
                for api_id, api_info in api_group.items():
                    # 检查请求参数
                    if "request_params" in api_info and "parameters" in api_info["request_params"]:
                        request_params = api_info["request_params"]["parameters"]
                        
                        # 检查json参数（POST/PUT请求的body参数）
                        if "json" in request_params and isinstance(request_params["json"], dict):
                            if param_name in request_params["json"]:
                                return request_params["json"][param_name]
                        
                        # 检查URL查询参数（GET请求的query参数）
                        if "url" in request_params:
                            url = request_params["url"]
                            # 检查URL中的查询参数
                            if '?' in url:
                                query_part = url.split('?')[1]
                                query_params = {}
                                for param in query_part.split('&'):
                                    if '=' in param:
                                        key, value = param.split('=', 1)
                                        query_params[key] = value
                                if param_name in query_params:
                                    return query_params[param_name]
                            
                            # 检查URL路径参数（从实际请求URL中提取）
                            if param_name.lower() in url.lower():
                                # 尝试从URL中提取参数值
                                url_parts = url.split('/')
                                for part in url_parts:
                                    if part and not part.startswith('http') and not part.isdigit() == False:
                                        return part
                    
                    # 检查响应参数
                    if "response_params" in api_info and "parameters" in api_info["response_params"]:
                        response_params = api_info["response_params"]["parameters"]
                        if isinstance(response_params, dict) and param_name in response_params:
                            return response_params[param_name]
            
            return None

        def _strip_response_parameters(data):
            """递归去除数据中的 response_parameters 字段"""
            if isinstance(data, dict):
                return {k: (_strip_response_parameters(v) if isinstance(v, (dict, list)) else v) 
                        for k, v in data.items() if k != 'response_parameters'}
            elif isinstance(data, list):
                return [_strip_response_parameters(item) for item in data]
            else:
                return data

        result = {}
        candidate_meta_by_group = {}
        group_para_data = self.true_params
        parameters_data = self.case_generation_results_packages
    
        # 使用ThreadPoolExecutor进行多线程处理（适配 dict 与 list[dict] 的输入）
        if isinstance(group_para_data, dict):
            groups_iter = list(group_para_data.items())
        elif isinstance(group_para_data, list):
            groups_iter = []
            for group_item in group_para_data:
                if isinstance(group_item, dict):
                    for group_name, apis in group_item.items():
                        groups_iter.append((group_name, apis))
        else:
            groups_iter = []

        with ThreadPoolExecutor(max_workers=max(1, min(len(groups_iter), 8))) as executor:
            # 提交所有任务
            future_to_group = {
                executor.submit(_process_group, group_name, apis, parameters_data): group_name
                for group_name, apis in groups_iter
            }
            
            # 收集结果
            for future in as_completed(future_to_group):
                group_name = future_to_group[future]
                try:
                    group_name, result_params, candidate_meta = future.result()
                    if result_params:
                        result[group_name] = result_params
                    candidate_meta_by_group[group_name] = candidate_meta
                    logger.info(f"功能组 {group_name} 处理完成")
                except Exception as e:
                    logger.error(f"功能组 {group_name} 处理失败: {str(e)}")

        return self._recheck_missing_resource_id_params(result, candidate_meta_by_group)

    def data_container_resource(self) -> Dict[str, Any]:
        """
        从 OIP 集合中识别 COIP。

        Workflow:
        1. rule OIP + LLM semantic additions -> OIP set.
        2. collect OIP hierarchy positions from endpoint path/request/response.
        3. hierarchy level > 1 or path ancestor relation -> COIP.
        4. LLM only rechecks remaining OIPs; it cannot introduce non-OIP params.
        
        Returns:
            容器资源参数识别结果
        """
        oip_result = self._last_oip_result or self.build_oip_candidates(include_llm=True)
        oip_set = set(oip_result.get("all", []))
        hierarchy_report = self.build_identifier_hierarchy_report(oip_set=oip_set)
        hierarchy_coip = self._coip_candidates_by_hierarchy(oip_set=oip_set)

        candidate_meta = {}
        for param in sorted(oip_set - hierarchy_coip):
            occs = hierarchy_report.get(param, [])
            if not occs:
                continue
            sample = occs[0]
            candidate_meta[param] = {
                "groups": sorted({self._group_for_endpoint(o.get("endpoint", "")) for o in occs}),
                "sample_api": {
                    "endpoint": sample.get("endpoint", ""),
                    "location": sample.get("location", ""),
                    "canonical_path": sample.get("canonical_path", ""),
                    "structural_level": sample.get("structural_level", 1),
                    "resource_level": sample.get("resource_level", 1),
                },
            }

        selected = set(hierarchy_coip)
        selected = set(self._recheck_missing_container_params(sorted(selected), candidate_meta))
        return sorted(selected & oip_set)

    def _group_for_endpoint(self, endpoint: str) -> str:
        for group_name, apis in self._iter_groups():
            if isinstance(apis, dict) and endpoint in apis:
                return group_name
            if isinstance(apis, list):
                for api_dict in apis:
                    if isinstance(api_dict, dict) and endpoint in api_dict:
                        return group_name
        return ""
