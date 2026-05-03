import sys
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
import json
import logging
import hashlib
import re

# 配置日志
logger = logging.getLogger(__name__)

# 添加项目根目录到Python路径
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, '..', '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# 使用绝对导入
from scripts.jsontools import JsonTools
from prompt.synthesis_prompt import SyntheticPrompt
from gptreply.gpt_con import GPTReply
from utils.param_path import (
    any_param_matches,
    base_param_name as shared_base_param_name,
    compact_name as shared_compact_name,
    param_matches,
)

"""
self.api_fully_doc:指的已经是打上标的doc了

"""
class ParaNormalize:
    def __init__(self,doc_data,model_name) -> None:
        self.jsontools = JsonTools()
        self.api_fully_doc = doc_data
        self.gpt_reply = GPTReply(model_name)
        self.syn_prompt = SyntheticPrompt()
        self.initial_test_info_dict = {
        }
        # 初始化归一化结果缓存
        self._normalization_cache = {}

    def parameters_extraction(self, include_path_params=False):
        """
        按功能组和API接口提取请求参数和响应参数，返回嵌套字典格式
        格式: {"功能组A": [{"路由1": {"request_para": [...], "response_para": [...]}}, {"路由2": {...}}]}
        
        Args:
            include_path_params (bool): 是否包含路由中{}包裹的路径参数
        """
        params_by_group = {}
        
        for group in self.api_fully_doc:
            # 获取功能组名称
            group_name = list(group.keys())[0]
            group_data = group[group_name]
            
            # 初始化该功能组的API列表
            group_apis = []
            
            # 遍历功能组中的每个API接口
            for api_endpoint, api_data in group_data.items():
                if isinstance(api_data, list):
                    agg_req = {}
                    agg_resp = {}
                    agg_type = None
                    for it in api_data:
                        if isinstance(it, dict):
                            req = it.get('request_parameters', {})
                            if isinstance(req, dict):
                                for k, v in req.items():
                                    agg_req[k] = v
                            resp = it.get('response_parameters', {})
                            if isinstance(resp, dict):
                                for k, v in resp.items():
                                    agg_resp[k] = v
                            t = it.get('type')
                            if isinstance(t, str) and t:
                                agg_type = agg_type or t
                    api_data = {
                        'request_parameters': agg_req,
                        'response_parameters': agg_resp,
                        'type': agg_type or 'unknown'
                    }
                # 使用集合避免重复参数（例如路径参数与request_parameters重复的情况）
                api_request_params_set = set()
                api_response_params_set = set()
                param_locations = {}  # 存储参数位置信息: {参数名: 位置}
                
                # 如果启用路径参数提取，从路由中提取{}包裹的参数
                if include_path_params:
                    path_params = self.extract_path_parameters(api_endpoint)
                    for param_name in path_params:
                        api_request_params_set.add(param_name)
                        # 若后续在request_parameters中出现同名参数，将以其in信息为准
                        if param_name not in param_locations:
                            param_locations[param_name] = "path"
                
                # 提取请求参数名称和位置信息
                if 'request_parameters' in api_data:
                    for param_name, param_info in api_data['request_parameters'].items():
                        api_request_params_set.add(param_name)
                        # 确定参数位置，优先以request_parameters中的in为准
                        if "in" in param_info:
                            param_locations[param_name] = param_info["in"]  # path, query, body 等
                        else:
                            # 如果没有明确指定位置，保持已有的（例如path）或默认为 body
                            param_locations[param_name] = param_locations.get(param_name, "body")
                
                # 提取响应参数名称
                if 'response_parameters' in api_data:
                    for param_name in api_data['response_parameters'].keys():
                        api_response_params_set.add(param_name)
                
                # 构建当前API的参数字典（排序后输出）
                api_dict = {
                    api_endpoint: {
                        "request_para": sorted(list(api_request_params_set)),
                        "response_para": sorted(list(api_response_params_set)),
                        "type": api_data.get('type', 'unknown'),
                        "param_locations": param_locations  # 添加参数位置信息
                    }
                }
                
                group_apis.append(api_dict)
            
            # 将该功能组的API列表添加到结果字典中
            params_by_group[group_name] = group_apis
            
        return params_by_group

    
    def parameters_normalization(self, params_by_group_data):
        """
        根据每个接口的参数进行标准化处理（多线程版本）
        """
        # 存储每次输出的数据
        stored_outputs = []
        generic_identifier_denylist = {
            "message", "status", "token", "code", "key", "serial", "number", "amount",
            "title", "content", "description", "result", "data"
        }

        def base_param_name(field):
            if not isinstance(field, str):
                field = str(field)
            return field.strip().split(".")[-1].replace("[]", "")

        def to_snake_case(name):
            name = base_param_name(name)
            if not name:
                return ""
            s1 = re.sub(r'(.)([A-Z][a-z]+)', r'\1_\2', name)
            s2 = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', s1)
            return s2.replace("-", "_").lower()

        def expand_param_variants(name):
            base = base_param_name(name)
            snake = to_snake_case(base)
            compact = snake.replace("_", "")
            parts = [p for p in snake.split("_") if p]
            camel = parts[0] + "".join(p.capitalize() for p in parts[1:]) if parts else snake
            return {v for v in {base, snake, compact, camel} if v}

        def same_param_family(left, right):
            return bool(expand_param_variants(left) & expand_param_variants(right))

        def should_keep_generic_replace(candidate):
            snake = to_snake_case(candidate)
            if not snake:
                return False
            if snake in generic_identifier_denylist:
                return False
            return True

        def sanitize_mapping_result(results_check):
            """
            清洗 LLM 输出：
            1. replace_para 只保留与 keep_pra 同名风格变体，或可信的通用 ID。
            2. 移除 message/status/token/code/number 等高歧义字段。
            """
            if not isinstance(results_check, list):
                return results_check

            sanitized = []
            trusted_generic_ids = {"id", "identifier", "uuid", "guid"}

            for item in results_check:
                if not isinstance(item, dict):
                    sanitized.append(item)
                    continue

                params_info = item.get("parameters_name", {}) or {}
                keep_param = params_info.get("keep_pra")
                replace_list = params_info.get("replace_para", [])

                if not keep_param:
                    sanitized.append(item)
                    continue

                if not isinstance(replace_list, list):
                    replace_list = [replace_list] if replace_list else []

                filtered_replace = []
                seen = set()
                for candidate in replace_list:
                    if not isinstance(candidate, str):
                        continue

                    candidate_base = base_param_name(candidate)
                    candidate_snake = to_snake_case(candidate_base)

                    if same_param_family(keep_param, candidate):
                        keep_candidate = True
                    elif candidate_snake in trusted_generic_ids:
                        keep_candidate = True
                    else:
                        keep_candidate = False

                    if not keep_candidate:
                        continue

                    if candidate not in seen:
                        filtered_replace.append(candidate)
                        seen.add(candidate)

                sanitized_item = copy.deepcopy(item)
                sanitized_item["parameters_name"] = {
                    "keep_pra": keep_param,
                    "replace_para": filtered_replace
                }
                sanitized.append(sanitized_item)

            return sanitized

        def check_api_in_doc(result,params_data):
            """
            校验 result 中的 route_name 是否都存在于 params_data 里。
            返回未匹配到的路由列表，若列表为空表示全部匹配。
            """
            available_routes = set()
            for api_dict in params_data:
                for api_route in api_dict.keys():
                    available_routes.add(api_route)
            unmatched = []
            if isinstance(result, list):
                for item in result:
                    if isinstance(item, dict):
                        route_list = item.get("route_name", [])
                        if isinstance(route_list, list):
                            for route in route_list:
                                if route not in available_routes:
                                    unmatched.append(route)
            return unmatched

        def reasonable_check(params_data, results_check):

            """
            校验生成结果的合理性：
            当 keep_pra 出现在 result 指定的 route_name 列表中、且该路由的类型为 add 的响应参数（response_para）里时，
            认为不合理，返回 False，并打印出现冲突的路由与参数信息。

            参数:
                params_data: 本功能组提取到的路由参数数据，形如：
                    [
                        {"GET /companyAddress/list": {"request_para": [...], "response_para": [...], "type": "query", "param_locations": {}}},
                        ...
                    ]
                results_check: LLM 生成的归一化规则 result，形如：
                    [
                        {
                            "route_name": ["POST /admin/delete/{id}", ...],
                            "parameters_name": {"replace_para": ["id"], "keep_pra": "adminId"}
                        }
                    ]
            返回:
                bool: 合理则 True；若发现 keep_pra 出现在 add 类型的响应参数中，则返回 False。
            """
            # 建立路由索引，便于快速查找
            route_index = {}
            for api_dict in params_data or []:
                if not isinstance(api_dict, dict):
                    continue
                for route, route_info in api_dict.items():
                    route_index[route] = route_info

            # 非列表或空，视作可通过
            if not isinstance(results_check, list):
                return True

            conflicts = []

            for item in results_check:
                if not isinstance(item, dict):
                    continue
                routes = item.get("route_name", []) or []
                params_info = item.get("parameters_name", {}) or {}
                keep_param = params_info.get("keep_pra")
                if not keep_param:
                    continue

                for route in routes:
                    route_data = route_index.get(route)
                    if not route_data:
                        continue
                    # 仅检查类型为 add 的路由
                    if route_data.get("type") != "add":
                        continue
                    resp_fields = route_data.get("response_para", []) or []

                    # 使用与全局一致的嵌套/模糊匹配规则进行判断
                    try:
                        in_resp = self._request_in_response(keep_param, resp_fields)
                    except Exception:
                        in_resp = keep_param in resp_fields

                    if in_resp:
                        conflicts.append({
                            "route": route,
                            "param": keep_param
                        })

            if conflicts:
                # 打印详细的冲突信息，包含哪个参数在哪个路由的响应体中出现
                print({"keep_param_in_add_response": conflicts})
                return False,conflicts

            return True,""

        def detect_reverse_mapping(results_check):
            """
            检测反向映射：keep_pra 是通用名（id），replace_para 是具体名（order_id 等）
            这种情况违反了"保留具体，替换通用"的原则。
            返回：(is_reversed, corrected_results, reversed_items)
            """
            reversed_items = []
            corrected_results = []
            
            if not isinstance(results_check, list):
                return False, results_check, []
            
            for item in results_check:
                if not isinstance(item, dict):
                    corrected_results.append(item)
                    continue
                
                params_info = item.get("parameters_name", {})
                if not isinstance(params_info, dict):
                    corrected_results.append(item)
                    continue
                    
                keep_param = params_info.get("keep_pra")
                replace_list = params_info.get("replace_para", [])
                
                if not isinstance(replace_list, list):
                    replace_list = [replace_list] if replace_list else []
                
                # 检测反向映射的特征
                is_reverse = False
                
                # 特征1: keep_pra 是单纯的 "id" 或 "identifier"
                if keep_param in ("id", "identifier", "ID"):
                    # 检查 replace_para 中是否有更具体的名称（包含下划线或前缀）
                    specific_params = []
                    for p in replace_list:
                        if isinstance(p, str):
                            # 提取不带嵌套路径的参数名
                            base_param = p.split(".")[-1].split("[")[0]
                            if "_id" in base_param or "_ID" in base_param:
                                specific_params.append(p)
                    
                    if specific_params:
                        is_reverse = True
                        # 自动纠正：选择第一个具体名称作为 keep_pra
                        # 如果有多个，优先选择与路由相关的
                        route_names = item.get("route_name", [])
                        best_keep = specific_params[0]
                        
                        # 尝试从路由中提取资源类型，选择最匹配的参数
                        if isinstance(route_names, list) and route_names:
                            for route in route_names:
                                if isinstance(route, str):
                                    route_lower = route.lower()
                                    for sp in specific_params:
                                        sp_base = sp.split(".")[-1].split("[")[0].lower()
                                        sp_prefix = sp_base.replace("_id", "").replace("_ID", "")
                                        if sp_prefix in route_lower:
                                            best_keep = sp
                                            break
                        
                        # 构建新的 replace_para：包含原来的 keep_pra (id) 和其他不是 best_keep 的参数
                        new_replace = [keep_param]  # 原来的 id
                        for p in replace_list:
                            if p != best_keep:
                                new_replace.append(p)
                        
                        corrected_item = item.copy()
                        corrected_item["parameters_name"] = {
                            "keep_pra": best_keep,
                            "replace_para": new_replace
                        }
                        corrected_results.append(corrected_item)
                        reversed_items.append({
                            "route_name": item.get("route_name", []),
                            "original_keep": keep_param,
                            "original_replace": replace_list,
                            "corrected_keep": best_keep,
                            "corrected_replace": new_replace
                        })
                        continue
                
                corrected_results.append(item)
            
            return len(reversed_items) > 0, corrected_results, reversed_items

        def detect_ambiguity(params_data, results):
            """
            检测歧义场景：
            1. 同一功能组存在多个 xxx_id 都可能需要映射到 id
            2. keep_pra 在不同路由类型（add/query/update）中语义不一致
            返回：ambiguous_cases 列表
            """
            ambiguous_cases = []
            
            if not isinstance(params_data, list) or not isinstance(results, list):
                return ambiguous_cases
            
            # 统计功能组中所有包含 _id 的参数
            specific_id_params = set()
            generic_id_params = set()  # 通用id参数（如 id, identifier）
            
            for api_item in params_data:
                if not isinstance(api_item, dict):
                    continue
                for route_key, route_info in api_item.items():
                    if not isinstance(route_info, dict):
                        continue
                    
                    # 收集请求参数和响应参数
                    all_params = []
                    if "request_para" in route_info and isinstance(route_info["request_para"], list):
                        all_params.extend(route_info["request_para"])
                    if "response_para" in route_info and isinstance(route_info["response_para"], list):
                        all_params.extend(route_info["response_para"])
                    
                    for param in all_params:
                        if not isinstance(param, str):
                            continue
                        # 提取基础参数名（去掉嵌套路径）
                        base_param = param.split(".")[-1].split("[")[0]
                        
                        if base_param.endswith("_id") or base_param.endswith("_ID"):
                            specific_id_params.add(base_param)
                        elif base_param.lower() in ("id", "identifier"):
                            generic_id_params.add(base_param)
            
            # 场景1：如果有多个 xxx_id，检查是否都可能映射到同一个 id
            if len(specific_id_params) > 1 and len(generic_id_params) > 0:
                # 检查当前的映射结果
                for item in results:
                    if not isinstance(item, dict):
                        continue
                    params_info = item.get("parameters_name", {})
                    if not isinstance(params_info, dict):
                        continue
                    
                    keep_pra = params_info.get("keep_pra", "")
                    replace_para = params_info.get("replace_para", [])
                    
                    # 如果 keep_pra 是一个特定的 xxx_id
                    if keep_pra in specific_id_params:
                        # 检查是否还有其他 xxx_id 没有被处理
                        other_ids = specific_id_params - {keep_pra}
                        if other_ids:
                            # 检查这些 other_ids 是否也出现在相关路由中
                            route_names = item.get("route_name", [])
                            if isinstance(route_names, list):
                                for other_id in other_ids:
                                    # 检查 other_id 是否在某些路由的参数中
                                    other_id_used = False
                                    for api_item in params_data:
                                        if not isinstance(api_item, dict):
                                            continue
                                        for route_key, route_info in api_item.items():
                                            if not isinstance(route_info, dict):
                                                continue
                                            all_params = []
                                            if "request_para" in route_info:
                                                all_params.extend(route_info.get("request_para", []))
                                            if "response_para" in route_info:
                                                all_params.extend(route_info.get("response_para", []))
                                            if other_id in all_params or any(other_id in str(p) for p in all_params):
                                                other_id_used = True
                                                break
                                        if other_id_used:
                                            break
                                    
                                    if other_id_used:
                                        ambiguous_cases.append({
                                            "type": "multiple_specific_ids",
                                            "current_keep": keep_pra,
                                            "alternative": other_id,
                                            "affected_routes": route_names,
                                            "message": f"存在多个特定ID参数 ({keep_pra}, {other_id})，当前选择了 {keep_pra}"
                                        })
            
            # 场景2：检查 keep_pra 是否来自 add 类型的路由（这通常不合理）
            for item in results:
                if not isinstance(item, dict):
                    continue
                params_info = item.get("parameters_name", {})
                if not isinstance(params_info, dict):
                    continue
                
                keep_pra = params_info.get("keep_pra", "")
                route_names = item.get("route_name", [])
                
                # 检查 keep_pra 是否主要来自 add 类型的响应参数
                add_route_count = 0
                for api_item in params_data:
                    if not isinstance(api_item, dict):
                        continue
                    for route_key, route_info in api_item.items():
                        if not isinstance(route_info, dict):
                            continue
                        if route_info.get("type", "").lower() in ("add", "create"):
                            response_params = route_info.get("response_para", [])
                            if keep_pra in response_params or any(keep_pra in str(p) for p in response_params):
                                add_route_count += 1
                
                if add_route_count > 0:
                    ambiguous_cases.append({
                        "type": "keep_from_add_route",
                        "keep_pra": keep_pra,
                        "affected_routes": route_names,
                        "message": f"keep_pra '{keep_pra}' 来自 {add_route_count} 个 add 类型路由的响应，这可能不合理"
                    })
            
            return ambiguous_cases

        def calculate_confidence(params_data, param_name, group_name):
            """
            计算参数名的置信度评分：
            - 在路由路径中出现次数
            - 在请求参数中出现次数（请求参数权重更高）
            - 与功能组名称的相关性
            - 避免选择来自 add 类型路由的响应参数
            """
            score = 0.0
            
            if not isinstance(params_data, list):
                return score
            
            # 提取参数名的基础部分（去掉 _id 后缀）
            param_base = param_name.replace("_id", "").replace("_ID", "").lower()
            
            # 1. 检查与功能组名称的相关性（权重：+20）
            if group_name and isinstance(group_name, str):
                group_lower = group_name.lower()
                if param_base in group_lower or group_lower in param_base:
                    score += 20.0
            
            # 2. 统计在路由路径中出现的次数（每次 +5）
            route_occurrence = 0
            # 3. 统计在请求参数中出现的次数（每次 +10）
            request_occurrence = 0
            # 4. 统计在响应参数中出现的次数（每次 +3）
            response_occurrence = 0
            # 5. 检查是否主要来自 add 类型路由的响应（惩罚）
            add_response_count = 0
            
            for api_item in params_data:
                if not isinstance(api_item, dict):
                    continue
                for route_key, route_info in api_item.items():
                    if not isinstance(route_key, str) or not isinstance(route_info, dict):
                        continue
                    
                    # 检查路由路径中是否包含参数名
                    if param_base in route_key.lower():
                        route_occurrence += 1
                    
                    # 检查请求参数
                    request_params = route_info.get("request_para", [])
                    if isinstance(request_params, list):
                        for req_param in request_params:
                            if isinstance(req_param, str):
                                req_base = req_param.split(".")[-1].split("[")[0]
                                if req_base == param_name or param_name in req_param:
                                    request_occurrence += 1
                    
                    # 检查响应参数
                    response_params = route_info.get("response_para", [])
                    if isinstance(response_params, list):
                        for resp_param in response_params:
                            if isinstance(resp_param, str):
                                resp_base = resp_param.split(".")[-1].split("[")[0]
                                if resp_base == param_name or param_name in resp_param:
                                    response_occurrence += 1
                                    # 如果来自 add 类型，记录
                                    if route_info.get("type", "").lower() in ("add", "create"):
                                        add_response_count += 1
            
            score += route_occurrence * 5.0
            score += request_occurrence * 10.0
            score += response_occurrence * 3.0
            
            # 如果主要来自 add 类型的响应，降低分数
            if add_response_count > 0 and add_response_count >= response_occurrence * 0.7:
                score -= 15.0
            
            return score

        def generate_candidate_mappings(params_data, ambiguous_cases, group_name):
            """
            为每个歧义场景生成多个候选映射方案
            返回：候选方案列表，每个方案包含 keep_pra, replace_para, confidence
            """
            if not ambiguous_cases:
                return []
            
            candidates = []
            processed_params = set()  # 避免重复处理相同的参数组合
            
            for case in ambiguous_cases:
                if case["type"] == "multiple_specific_ids":
                    current_keep = case.get("current_keep")
                    alternative = case.get("alternative")
                    
                    # 为当前选择和备选都生成候选方案
                    for candidate_keep in [current_keep, alternative]:
                        if not candidate_keep or candidate_keep in processed_params:
                            continue
                        
                        processed_params.add(candidate_keep)
                        
                        # 计算置信度
                        confidence = calculate_confidence(params_data, candidate_keep, group_name)
                        
                        candidate = {
                            "keep_pra": candidate_keep,
                            "replace_para": ["id"],  # 统一替换通用 id
                            "confidence": confidence,
                            "reason": f"参数 {candidate_keep} 在功能组中的置信度评分: {confidence:.2f}"
                        }
                        candidates.append(candidate)
                
                elif case["type"] == "keep_from_add_route":
                    # 这种情况可能需要重新评估，但暂时不生成额外候选
                    keep_pra = case.get("keep_pra")
                    if keep_pra:
                        logger.info(f"警告：keep_pra '{keep_pra}' 主要来自 add 类型路由，建议人工检查")
            
            # 按置信度排序
            candidates.sort(key=lambda x: x["confidence"], reverse=True)
            
            return candidates

        
            
            # 原实现：return unmatched_keywords
            # 现已替换为返回未匹配到的具体路由名称列表
        
        def process_group(group_name, params_data):
            """
            处理单个功能组的参数标准化
            """
            # 生成缓存键（基于功能组名和参数数据的哈希）
            import hashlib
            params_str = json.dumps(params_data, sort_keys=True, ensure_ascii=False)
            cache_key = f"{group_name}_{hashlib.md5(params_str.encode()).hexdigest()}"
            
            # 检查缓存
            if hasattr(self, '_normalization_cache') and cache_key in self._normalization_cache:
                logger.info(f"功能组 {group_name}: 使用缓存结果")
                cached_result = self._normalization_cache[cache_key]
                stored_outputs.append({"group": group_name, "data": cached_result, "cached": True})
                return group_name, params_data
            
            # 创建线程本地的initial_test_info_dict副本，避免线程间冲突
            local_test_info_dict = copy.deepcopy(self.initial_test_info_dict)
            local_test_info_dict["params_data"] = params_data
            local_test_info_dict["params_name"] = group_name
            local_test_info_dict["false_reason"] = ""
            reuslts_reasonale = True
            while True:
                try:
                    while reuslts_reasonale:
                        try:
                            logger.info(f"功能组 {group_name}: 开始LLM参数归一化分析")
                            tmp_result = self.gpt_reply.getreply(
                                self.syn_prompt.synthesis_prompt("parameter_normalization", local_test_info_dict)
                            )
                            result = eval(self.jsontools.list_formatting(tmp_result))
                            result = sanitize_mapping_result(result)
                            
                            # 新增：检测并纠正反向映射
                            is_reversed, corrected_result, reversed_items = detect_reverse_mapping(result)
                            if is_reversed:
                                logger.warning(f"功能组 {group_name} 检测到反向映射，已自动纠正：")
                                for item in reversed_items:
                                    logger.warning(f"  路由: {item['route_name']}")
                                    logger.warning(f"    原始映射: keep_pra='{item['original_keep']}', replace_para={item['original_replace']}")
                                    logger.warning(f"    纠正后: keep_pra='{item['corrected_keep']}', replace_para={item['corrected_replace']}")
                                result = corrected_result
                            
                            # 新增：检测歧义场景
                            ambiguous_cases = detect_ambiguity(params_data, result)
                            if ambiguous_cases:
                                logger.info(f"功能组 {group_name} 检测到潜在歧义：")
                                for case in ambiguous_cases:
                                    logger.info(f"  类型: {case['type']}, 详情: {case['message']}")
                                
                                # 生成候选映射方案
                                candidates = generate_candidate_mappings(params_data, ambiguous_cases, group_name)
                                if candidates:
                                    logger.info(f"功能组 {group_name} 生成了 {len(candidates)} 个候选映射方案：")
                                    for idx, candidate in enumerate(candidates, 1):
                                        logger.info(f"  候选 {idx}: keep_pra='{candidate['keep_pra']}', "
                                                   f"confidence={candidate['confidence']:.2f}, "
                                                   f"原因={candidate['reason']}")
                                    
                                    # 如果最高置信度的候选与当前结果不同，提示可能需要调整
                                    best_candidate = candidates[0]
                                    current_keep = None
                                    if isinstance(result, list) and len(result) > 0:
                                        for item in result:
                                            if isinstance(item, dict):
                                                current_keep = item.get("parameters_name", {}).get("keep_pra")
                                                break
                                    
                                    if current_keep and best_candidate["keep_pra"] != current_keep:
                                        logger.warning(f"建议：当前使用 '{current_keep}'，但置信度最高的是 '{best_candidate['keep_pra']}' "
                                                      f"(置信度差: {best_candidate['confidence']:.2f})")
                            
                            reuslts_reasonale,reason = reasonable_check(params_data,result)
                            local_test_info_dict["false_reason"] = reason
                            # print(result)
                            break
                        except Exception:
                            pass
                    # print(result)
                    # 存储输出数据
                    stored_outputs.append({"group": group_name,  "data": result})
                    
                    # 检查是否有无法匹配的关键字（添加重试次数限制和去重）
                    unmatched_keywords = check_api_in_doc(result,params_data)
                    retry_count = 0
                    max_retries = 3  # 最多重试3次
                    seen_unmatched = set()  # 记录已处理过的未匹配路由
                    
                    while unmatched_keywords and retry_count < max_retries:
                        # 将未匹配关键字转换为可哈希的字符串用于去重
                        unmatched_sig = str(sorted(unmatched_keywords))
                        if unmatched_sig in seen_unmatched:
                            logger.warning(f"功能组 {group_name}: 检测到重复的未匹配路由，停止重试")
                            break
                        seen_unmatched.add(unmatched_sig)
                        retry_count += 1
                        
                        logger.info(f"功能组 {group_name}: 第{retry_count}次尝试修复未匹配路由（共{len(unmatched_keywords)}个）")
                        
                        while True:
                            try:
                                self.initial_test_info_dict["update_para"] = unmatched_keywords
                                self.initial_test_info_dict["params_data"] = params_data
                                self.initial_test_info_dict["original_data"] = result
                                tmp_update_results = self.gpt_reply.getreply(
                                    self.syn_prompt.synthesis_prompt("parameter_update", self.initial_test_info_dict)
                                )
                                update_results = eval(self.jsontools.list_formatting(tmp_update_results))
                                break  # 跳出while循环，继续处理
                            except Exception as e:
                                continue  # 重新生成
                        result =  update_results
                        # print("new result",result)
                        # 存储更新后的输出数据
                        stored_outputs.append({"group": group_name, "type": "updated_result", "data": result})
                        unmatched_keywords = check_api_in_doc(result,params_data)
                    
                    if unmatched_keywords and retry_count >= max_retries:
                        logger.warning(f"功能组 {group_name}: 达到最大重试次数，仍有{len(unmatched_keywords)}个未匹配路由")
                    
                    # 按新的 result 结构执行参数替换
                    for change in result:
                        if not isinstance(change, dict):
                            continue
                        route_list = change.get("route_name", [])
                        params_info = change.get("parameters_name", {})
                        replace_list = params_info.get("replace_para", [])
                        keep_param = params_info.get("keep_pra")
                        if not keep_param or not isinstance(replace_list, list) or not isinstance(route_list, list):
                            continue
                        
                        # 遍历该功能组下的所有API
                        for api_dict in params_data:
                            for api_name, api_params in api_dict.items():
                                # 仅在指定路由中执行替换（精确匹配）
                                if api_name in route_list:
                                    req_params = api_params.get("request_para", [])
                                    resp_params = api_params.get("response_para", [])
                                    param_locations = api_params.get("param_locations", {})
                                    
                                    # 对 replace_list 中的每个参数分别判断并替换
                                    for rp in list(replace_list):
                                        if not isinstance(rp, str):
                                            continue
                                        # 约束 (1)：keep 与 replace 不得同时出现在响应参数中
                                        both_in_resp = (keep_param in resp_params) and (rp in resp_params)
                                        if both_in_resp:
                                            # 不替换该路由下的该参数
                                            continue
                                        # 约束 (2)：keep 与 replace 不得同时以同一种请求方式出现（query/path/body）
                                        both_in_req = (keep_param in req_params) and (rp in req_params)
                                        same_location = False
                                        if both_in_req:
                                            loc_keep = param_locations.get(keep_param)
                                            loc_rp = param_locations.get(rp)
                                            if loc_keep and loc_rp and (loc_keep == loc_rp):
                                                if loc_keep in ("query", "path", "body"):
                                                    same_location = True
                                        if same_location:
                                            # 不替换该路由下的该参数
                                            continue
                                        
                                        # 执行替换：request
                                        if rp in req_params:
                                            req_params.remove(rp)
                                            if keep_param not in req_params:
                                                req_params.append(keep_param)
                                            # 继承位置信息
                                            if keep_param not in param_locations and rp in param_locations:
                                                param_locations[keep_param] = param_locations[rp]
                                            # 移除已不用的位置信息
                                            if rp not in req_params:
                                                if rp in param_locations:
                                                    del param_locations[rp]
                                        
                                        # 执行替换：response
                                        if (not both_in_resp) and (rp in resp_params):
                                            resp_params.remove(rp)
                                            if keep_param not in resp_params:
                                                resp_params.append(keep_param)
                                        
                                        # 重新排序并回写
                                        api_params["request_para"] = sorted(req_params)
                                        api_params["response_para"] = sorted(resp_params)
                                        api_params["param_locations"] = param_locations
                    
                    # 保存到缓存
                    if not hasattr(self, '_normalization_cache'):
                        self._normalization_cache = {}
                    self._normalization_cache[cache_key] = result
                    logger.info(f"功能组 {group_name}: 结果已缓存")
                    
                    break  # 成功处理后跳出while循环
                except Exception as e:
                    continue
            
            return group_name, params_data
        
        # 使用线程池执行多线程处理
        max_workers = min(len(params_by_group_data), 5)  # 限制最大线程数为4
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # 提交所有任务
            future_to_group = {
                executor.submit(process_group, group_name, params_data): group_name 
                for group_name, params_data in params_by_group_data.items()
            }
            
            # 等待所有任务完成并收集结果
            for future in as_completed(future_to_group):
                group_name = future_to_group[future]
                try:
                    result_group_name, result_params_data = future.result()
                    # 更新原始数据
                    params_by_group_data[result_group_name] = result_params_data
                except Exception as e:
                    pass
        
        # 添加type信息到normalized_params
        self._add_type_info_to_normalized_params(params_by_group_data)
        
        return params_by_group_data, stored_outputs
    
    def _add_type_info_to_normalized_params(self, normalized_params):
        """
        从api_doc_with_types.json中读取type信息并添加到normalized_params中
        """
        # 使用已经加载的API文档数据
        api_doc_with_types = self.api_fully_doc
        
        # 创建API路径到type的映射
        api_type_map = {}
        for doc_item in api_doc_with_types:
            for group_name, group_data in doc_item.items():
                for api_path, api_data in group_data.items():
                    if isinstance(api_data, dict) and "type" in api_data:
                        api_type_map[api_path] = api_data["type"]
        
        # 遍历每个功能组
        for group_name, group_apis in normalized_params.items():
            # 遍历该功能组下的每个API字典
            for api_dict in group_apis:
                # 遍历API字典中的每个API
                for api_path, api_info in api_dict.items():
                    # 在api_type_map中查找对应的API type
                    if api_path in api_type_map:
                        # 添加type信息
                        api_info["type"] = api_type_map[api_path]
                    else:
                        # 如果找不到，设置为unknown
                        api_info["type"] = "unknown"


    # ========= 新增：请求-响应参数模糊匹配工具 =========
    def _base_param_name(self, field: str) -> str:
        """提取字段的基础参数名，去掉嵌套路径和数组标记。"""
        if not isinstance(field, str):
            field = str(field)
        return field.strip().split(".")[-1].replace("[]", "")

    def _to_snake_case(self, name: str) -> str:
        """将 camelCase / PascalCase / kebab-case 统一转换为 snake_case。"""
        if not isinstance(name, str):
            name = str(name)
        name = self._base_param_name(name)
        if not name:
            return ""
        s1 = re.sub(r'(.)([A-Z][a-z]+)', r'\1_\2', name)
        s2 = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', s1)
        return s2.replace("-", "_").lower()

    def _compact_name(self, name: str) -> str:
        """去掉分隔符后的紧凑形式。"""
        return self._to_snake_case(name).replace("_", "")

    def _extract_resource_anchor(self, name: str) -> str:
        """
        从参数名里提取资源锚点。
        例如:
        - order_id -> order
        - orderId -> order
        - postId -> post
        - items[].id -> items
        """
        snake = self._to_snake_case(name)
        if not snake:
            return ""
        generic_suffixes = (
            "_id", "_ids", "_uuid", "_guid", "_code", "_name",
            "_key", "_slug", "_token"
        )
        for suffix in generic_suffixes:
            if snake.endswith(suffix) and len(snake) > len(suffix):
                return snake[:-len(suffix)]
        if snake in {"id", "uuid", "guid", "identifier", "code", "name"}:
            return ""
        return snake

    def _normalize_anchor_token(self, token: str) -> str:
        """对资源锚点做轻量单复数归一化。"""
        if not token:
            return ""
        token = token.lower()
        if token.endswith("ies") and len(token) > 3:
            return token[:-3] + "y"
        if token.endswith("ses") and len(token) > 3:
            return token[:-2]
        if token.endswith("s") and not token.endswith("ss") and len(token) > 3:
            return token[:-1]
        return token

    def _anchors_match(self, left: str, right: str) -> bool:
        """判断两个资源锚点是否表达同一资源。"""
        left_norm = self._normalize_anchor_token(left)
        right_norm = self._normalize_anchor_token(right)
        if not left_norm or not right_norm:
            return False
        return left_norm == right_norm

    def _normalize_response_field(self, field: str) -> str:
        """标准化响应参数字段：小写+按'.'分割并清洗每个片段的'[]'，不依赖固定前缀。"""
        if not isinstance(field, str):
            field = str(field)
        parts = [p for p in field.strip().lower().split(".") if p]
        cleaned = []
        for p in parts:
            if p.endswith("[]"):
                p = p[:-2]
            cleaned.append(p)
        return ".".join(cleaned)

    def _response_matches_request(self, req_field: str, resp_field: str) -> bool:
        """
        语义交集匹配：
        1. 命名风格变体匹配: order_id <-> orderId <-> orderid
        2. 传统 token 匹配: id 命中 items[].id
        3. 资源锚点匹配: postId 命中 posts[].id
        """
        try:
            if param_matches(req_field, resp_field):
                return True
        except Exception:
            pass
        if not isinstance(req_field, str):
            req_field = str(req_field)
        if not isinstance(resp_field, str):
            resp_field = str(resp_field)

        req_base = self._base_param_name(req_field)
        resp_base = self._base_param_name(resp_field)

        # 1) 优先做命名风格统一后的等价判断
        if self._compact_name(req_base) and self._compact_name(req_base) == self._compact_name(resp_base):
            return True

        # 2) 传统最后 token 命中
        req_tokens = [p for p in req_field.strip().lower().split(".") if p]
        if not req_tokens:
            return False
        last = req_tokens[-1]
        if last.endswith("[]"):
            last = last[:-2]
        # 避免过短token导致的误判（例如 'id' 可以保留，若需要更严格可提升阈值）
        if len(last) < 2:
            return False
        # 获取响应字段的token集合
        resp_norm = self._normalize_response_field(resp_field)
        resp_tokens = [p for p in resp_norm.split(".") if p]
        resp_token_set = set(resp_tokens)
        if last in resp_token_set:
            return True

        # 3) 资源锚点匹配：xxxId / xxx_id 命中 items[].id / data.xxx.id
        req_anchor = self._extract_resource_anchor(req_base)
        if req_anchor:
            resp_last = resp_tokens[-1] if resp_tokens else ""
            if resp_last in {"id", "uuid", "guid", "identifier", "code", "name"}:
                for token in resp_tokens[:-1]:
                    if self._anchors_match(req_anchor, token):
                        return True

        return False

    def _request_in_response(self, req_field: str, response_fields) -> bool:
        """判断请求参数在响应参数集合中是否命中（使用模糊匹配）。"""
        try:
            return any_param_matches(req_field, response_fields)
        except Exception:
            pass
        for resp in response_fields:
            if self._response_matches_request(req_field, resp):
                return True
        return False
    # ========= 新增结束 =========

    # 扁平化工具：将可能嵌套的参数列表展开为一维字符串列表
    def _flatten_params(self, values):
        flat = []
        def _rec(v):
            if v is None:
                return
            if isinstance(v, (list, tuple, set)):
                for x in v:
                    _rec(x)
            else:
                # 统一转为字符串，保证可哈希
                flat.append(str(v))
        _rec(values)
        return flat

    def set_generation_by_group(self, group_para_data):
        """
        按照功能组，将请求参数设为集合A，响应参数设为集合B，然后输出A-B和A-(A-B)两个集合
        
        Args:
            group_para_data (dict): 按功能组分组的参数数据
                新格式: {"功能组名": [{"路由1": {"request_para": [...], "response_para": [...]}}, ...]}
        
        Returns:
            dict: 每个功能组的集合运算结果
                格式: {"功能组名": {"A-B": [...], "A-(A-B)": [...]}}
        """
        result = {}
        
        for group_name, api_list in group_para_data.items():
            # 汇总该功能组下所有API的请求参数和响应参数
            all_request_params = set()
            all_response_params = set()
            
            for api_dict in api_list:
                for api_name, api_params in api_dict.items():
                    req_vals = self._flatten_params(api_params.get("request_para", []))
                    resp_vals = self._flatten_params(api_params.get("response_para", []))
                    all_request_params.update(req_vals)
                    all_response_params.update(resp_vals)
            
            # 将请求参数设为集合A，响应参数设为集合B
            set_A = all_request_params
            set_B = all_response_params
            
            # 使用模糊匹配：若响应参数去掉前缀"data."后等于请求参数，则认为命中
            matched_in_B = {req for req in set_A if self._request_in_response(req, set_B)}
            
            # 计算A-B（请求参数中未在响应参数中命中的参数）
            A_minus_B = set_A - matched_in_B
            
            # 计算A-(A-B)，即请求参数与响应参数的“交集”（按模糊匹配命中）
            A_minus_A_minus_B = matched_in_B
            
            # 将结果存储为排序后的列表
            result[group_name] = {
                "A":sorted(list(set_A)),
                "B":sorted(list(set_B)),
                "A-B": sorted(list(A_minus_B)),
                "A-(A-B)": sorted(list(A_minus_A_minus_B))
                # "A":set_A,
                # "B":set_B
                
            }
            
        return result

    def set_generation_by_group_and_type(self, set_result_1, normalized_params):
        """
        针对A-B的参数，筛选出参数出现在add类型的接口中的参数
        
        Args:
            set_result_1: set_generation_by_group的输出结果
            normalized_params: 归一化后的参数
        
        Returns:
            dict: 筛选后的结果，格式与set_result_1相同，但A-B只包含出现在add类型接口中的参数
        """
        result = {}
        
        # 遍历每个功能组
        for group_name, group_data in set_result_1.items():
            result[group_name] = {
                'A-B': group_data['A-B'],  # 原有的A-B保持不变
                'A-(A-B)': group_data['A-(A-B)'],  # A-(A-B)保持不变
                'A-B-add': []  # 新增集合：A-B参数中出现在add类型接口且不在A-(A-B)中的参数
            }
            # 获取当前功能组的A-B参数和A-(A-B)参数
            ab_params = group_data['A-B']
            exclude_params = set(group_data['A-(A-B)'])
            
            # 筛选A-B参数中出现在add类型接口中且不在A-(A-B)集合中的参数
            for param in ab_params:
                # 首先检查参数是否在A-(A-B)集合中，如果在则跳过
                if param in exclude_params:
                    continue
                    
                # 检查该参数是否出现在add类型的接口中
                param_in_add_interface = False
                
                # 遍历当前功能组的所有接口
                if group_name in normalized_params:
                    for api_dict in normalized_params[group_name]:
                        # api_dict是一个字典，键是接口路径，值包含参数信息
                        for api_path, api_params in api_dict.items():
                            # 从文档中获取接口类型（兼容字典/列表两种结构）
                            api_type = ''
                            for group_dict in self.api_fully_doc:
                                if group_name not in group_dict:
                                    continue
                                group_apis = group_dict[group_name]
                                if isinstance(group_apis, dict):
                                    val = group_apis.get(api_path)
                                    if isinstance(val, dict):
                                        api_type = val.get('type', '') or api_type
                                    elif isinstance(val, list):
                                        for it in val:
                                            if isinstance(it, dict):
                                                api_type = it.get('type', '') or api_type
                                    if api_type:
                                        break
                                elif isinstance(group_apis, list):
                                    for it in group_apis:
                                        if isinstance(it, dict) and api_path in it:
                                            v = it[api_path]
                                            if isinstance(v, dict):
                                                api_type = v.get('type', '') or api_type
                                            elif isinstance(v, list):
                                                for vv in v:
                                                    if isinstance(vv, dict):
                                                        api_type = vv.get('type', '') or api_type
                                            if api_type:
                                                break
                                    if api_type:
                                        break
                            
                            # 如果是add类型接口，检查参数是否在其请求或响应参数中
                            if api_type == 'add':
                                # 使用扁平化，避免嵌套列表导致匹配失败
                                request_params = self._flatten_params(api_params.get('request_para', []))
                                response_params = self._flatten_params(api_params.get('response_para', []))
                                
                                if param in request_params or self._request_in_response(param, response_params):
                                    param_in_add_interface = True
                                    # 立即添加到A-B-add集合（避免重复）
                                    if param not in result[group_name]['A-B-add']:
                                        result[group_name]['A-B-add'].append(param)
                                    break
                        if param_in_add_interface:
                            break
        
        return result

    def extract_path_parameters(self, api_endpoint):
        """
        从API路由中提取路径参数（{}包裹的内容）
        例如: /api/user/{user_id}/profile/{profile_id} -> ["user_id", "profile_id"]
        """
        import re
        path_params = re.findall(r'\{([^}]+)\}', api_endpoint)
        return path_params

    def merged_parameters_reslts(self,set_result_1,set_result):
        merged_result = {}
        for group_name in set_result_1.keys():
            merged_result[group_name] = {
                'A-B': set_result_1[group_name]['A-B'],
                'A-(A-B)': set_result_1[group_name]['A-(A-B)'],
                'A-B-add': set_result[group_name]['A-B-add']
            }
        return merged_result

    def set_generation_by_group_and_type_stride(self,normalized_params):
        """
        直接处理整个normalized_params，结合set_generation_by_group和set_generation_by_group_and_type的功能
        
        Args:
            normalized_params: 归一化后的参数数据
                格式: {"功能组名": [{"路由1": {"request_para": [...], "response_para": [...]}}, ...]}
        
        Returns:
            dict: 每个功能组的集合运算结果和筛选结果
                格式: {"功能组名": {"A-B": [...], "A-(A-B)": [...], "A-B-add": [...]}}
        """
        result = {}
        
        # 遍历每个功能组
        for group_name, api_list in normalized_params.items():
            # 汇总该功能组下所有API的请求参数和响应参数
            all_request_params = set()
            all_response_params = set()
            
            for api_dict in api_list:
                for api_name, api_params in api_dict.items():
                    req_vals = self._flatten_params(api_params.get("request_para", []))
                    resp_vals = self._flatten_params(api_params.get("response_para", []))
                    all_request_params.update(req_vals)
                    all_response_params.update(resp_vals)
            
            # 将请求参数设为集合A，响应参数设为集合B
            set_A = all_request_params
            set_B = all_response_params
            
            # 使用模糊匹配：若响应参数去掉前缀"data."后等于请求参数，则认为命中
            matched_in_B = {req for req in set_A if self._request_in_response(req, set_B)}
            
            # 计算A-B（请求参数中未在响应参数中命中的参数）
            A_minus_B = set_A - matched_in_B
            
            # 计算A-(A-B)，即请求参数和响应参数的交集（按模糊匹配命中）
            A_minus_A_minus_B = matched_in_B
            
            # 初始化结果
            result[group_name] = {
                'A-B': sorted(list(A_minus_B)),
                'A-(A-B)': sorted(list(A_minus_A_minus_B)),
                'A-B-add': []  # 新增集合：A-B参数中出现在add类型接口且不在A-(A-B)中的参数
            }
            
            # 筛选A-B参数中出现在add类型接口中且不在A-(A-B)集合中的参数
            exclude_params = set(A_minus_A_minus_B)
            
            for param in A_minus_B:
                # 首先检查参数是否在A-(A-B)集合中，如果在则跳过
                if param in exclude_params:
                    continue
                
                # 检查该参数是否出现在add类型的接口中
                param_in_add_interface = False
                
                # 遍历当前功能组的所有接口
                for api_dict in api_list:
                    # api_dict是一个字典，键是接口路径，值包含参数信息
                    for api_path, api_params in api_dict.items():
                        # 从文档中获取接口类型（兼容字典/列表两种结构）
                        api_type = ''
                        for group_dict in self.api_fully_doc:
                            if group_name not in group_dict:
                                continue
                            group_apis = group_dict[group_name]
                            if isinstance(group_apis, dict):
                                val = group_apis.get(api_path)
                                if isinstance(val, dict):
                                    api_type = val.get('type', '') or api_type
                                elif isinstance(val, list):
                                    for it in val:
                                        if isinstance(it, dict):
                                            api_type = it.get('type', '') or api_type
                                if api_type:
                                    break
                            elif isinstance(group_apis, list):
                                for it in group_apis:
                                    if isinstance(it, dict) and api_path in it:
                                        v = it[api_path]
                                        if isinstance(v, dict):
                                            api_type = v.get('type', '') or api_type
                                        elif isinstance(v, list):
                                            for vv in v:
                                                if isinstance(vv, dict):
                                                    api_type = vv.get('type', '') or api_type
                                        if api_type:
                                            break
                                if api_type:
                                    break
                        
                        # 如果是add类型接口，检查参数是否在其请求或响应参数中
                        if api_type == 'add':
                            request_params = api_params.get('request_para', [])
                            response_params = api_params.get('response_para', [])
                            
                            if param in request_params or self._request_in_response(param, response_params):
                                param_in_add_interface = True
                                # 立即添加到A-B-add集合（避免重复）
                                if param not in result[group_name]['A-B-add']:
                                    result[group_name]['A-B-add'].append(param)
                                break
                    if param_in_add_interface:
                        break
        
        return result

    def parameters_results_packages(self, use_llm_mapping=True):
        """
        使用这个方法，就可以输出这个阶段所需要获取的所有数据
        """
        # todo: 需要确定这种path类型的数据提取是否适用于所有文档
        parameters_extraction_results = self.parameters_extraction(include_path_params=True)

        # normalized_params 是进行替换后的参数，normalized_params_process_data 是后续用于参考的参数。
        # 消融实验可关闭 LLM 参数映射，直接使用原始请求/响应参数进入后续集合计算。
        if use_llm_mapping:
            normalized_params, normalized_params_process_data = self.parameters_normalization(parameters_extraction_results)
        else:
            normalized_params = copy.deepcopy(parameters_extraction_results)
            self._add_type_info_to_normalized_params(normalized_params)
            normalized_params_process_data = []

        # 这里的v1指的是功能组内的纯集合运算、这里的v2指的是加上add类型的
        set_calculate_results_v1 = self.set_generation_by_group(normalized_params)
        set_calculate_results_v2 = self.set_generation_by_group_and_type(
            set_calculate_results_v1,
            normalized_params
            )

        set_calculate_results_all = self.merged_parameters_reslts(
            set_calculate_results_v1,
            set_calculate_results_v2
            )
        
        # 这里指的是跨功能组的参数集合
        set_calculate_results_v3 = self.set_generation_by_group_and_type_stride(
            normalized_params
        )

        return {
            "parameters_extraction_results":parameters_extraction_results,
            "normalized_params":normalized_params,
            "normalized_params_process_data":normalized_params_process_data,
            "set_calculate_results_v1":set_calculate_results_v1,
            "set_calculate_results_v2":set_calculate_results_v2,
            "set_calculate_results_all":set_calculate_results_all,
            "set_calculate_results_v3":set_calculate_results_v3
        }




if __name__ == "__main__":
    import os
    project_name = "mall"
    jsontools = JsonTools()
    
    # 获取项目根目录
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
    cache_dir = os.path.join(project_root, 'cache', project_name)
    
    api_doc_type_path = jsontools.read_json(os.path.join(cache_dir, "api_doc_with_type.json"))
    paranor_tool = ParaNormalize(api_doc_type_path, "gpt-4o-mini")
    parameters_extraction_results = paranor_tool.parameters_extraction(include_path_params=True)

    # normalized_params是进行替换后的参数，normalized_params_process_data是后续用于参考的参数
    results = paranor_tool.parameters_results_packages()
    jsontools.write_json(os.path.join(cache_dir, "parameters_dict_all.json"), results)


        
