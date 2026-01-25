from scripts.api_doc import ApiDoc
from scripts.jsontools import JsonTools

from prompt.synthesis_prompt import SyntheticPrompt
from gptreply.gpt_con import GPTReply

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import sys,os
import requests
import json
import base64
import copy
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from utils.bola_vulner.horizontal.testcase_matrix import TestCase as MatrixTestCase
from utils.bola_vulner.horizontal.testcase_matrix import build_test_cases as matrix_build_test_cases
from utils.bola_vulner.horizontal.testcase_matrix import build_container_boundary_test_cases as matrix_build_container_boundary_test_cases
from utils.bola_vulner.horizontal.testcase_matrix import apply_test_case_to_req as matrix_apply_test_case
from utils.bola_vulner.horizontal.testcase_matrix import is_valid_resource_id
from utils.dependency_cc.src.file_utils import deserialize_file_params

# 导入新的模块化组件
from .utils_helpers import make_json_serializable, format_duration, update_terminal_progress, ProgressTracker
from .resource_identifier import ResourceIdentifier
from .package_generator import PackageGenerator

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# LLM 控制配置（可通过环境变量调整）
try:
    _llm_max_retries = int(os.getenv('BOLASCAN_LLM_MAX_RETRIES', '3'))
except Exception:
    _llm_max_retries = 3
_llm_disabled = str(os.getenv('BOLASCAN_DISABLE_LLM', 'false')).lower() in ('1', 'true', 'yes', 'on')
_llm_fail_policy = str(os.getenv('BOLASCAN_LLM_FAIL_POLICY', 'lenient')).lower()  # 可选：'lenient' 或 'aggressive'

class HorizontalVuln:
    def __init__(self,model_name,param_dict,case_generation_results_packages,project_name,api_doc_data) -> None:
        # 统一规范 api_doc_data：原始文件可能为 list[dict]（每个元素是一个功能组的字典），此处归一化为 dict 以便后续 .get 等操作
        if isinstance(api_doc_data, list):
            merged = {}
            for item in api_doc_data:
                if isinstance(item, dict):
                    merged.update(item)
            self.true_params = merged
        elif isinstance(api_doc_data, dict):
            self.true_params = api_doc_data
        else:
            self.true_params = {}
        self.normalized_params = param_dict["normalized_params"]
        self.normalized_params_process_data = param_dict["normalized_params_process_data"]
        self.case_generation_results_pacakges = case_generation_results_packages
        self.jsontool = JsonTools()
        self.gpt_reply = GPTReply(model_name)
        self.syn_prompt = SyntheticPrompt()
        self.project_name = project_name
        self.llm_dict = {}
        
        # 构建参数映射索引（基于 parameters_dict_all.json）
        self.route_group_map = {}  # { "POST /api/videos": "group_name" }
        self.group_param_config = {}  # { "group_name": { "replace_para": ["id"], "keep_pra": "video_id" } }
        self._build_param_mapping_index()
        
        # 初始化模块化组件
        self.resource_identifier = ResourceIdentifier(
            self.true_params,
            self.normalized_params,
            self.case_generation_results_pacakges,
            self.gpt_reply,
            self.syn_prompt,
            self.jsontool,
            self.llm_dict
        )
        
        self.package_generator = PackageGenerator(
            self.normalized_params,
            self.true_params,
            self.case_generation_results_pacakges,
            self.gpt_reply,
            self.syn_prompt,
            self.jsontool,
            self.llm_dict
        )
        

    def build_test_cases(self, param_name: str, locations: List[str], aliases: List[str], last_step_type: str, category: str, include_extra_types: bool=True) -> List[MatrixTestCase]:
        """构建BOLA测试用例（跨账号越权）"""
        return matrix_build_test_cases(param_name, locations, aliases, last_step_type, category, include_extra_types)

    def build_container_boundary_test_cases(self, param_name: str, locations: List[str], aliases: List[str], last_step_type: str, category: str) -> List[MatrixTestCase]:
        """构建容器边界测试用例（同账号跨容器）"""
        return matrix_build_container_boundary_test_cases(param_name, locations, aliases, last_step_type, category)

    def apply_test_case_to_req(self, step_obj: Dict[str, Any], test_case: Optional[MatrixTestCase], pools: Optional[Dict[str, Dict[str, Any]]]) -> Dict[str, Any]:
        """应用测试用例到请求"""
        return matrix_apply_test_case(step_obj, test_case, pools)

    def _build_param_mapping_index(self):
        """
        从 normalized_params_process_data (parameters_dict_all.json) 构建参数映射索引
        构建两个索引：
        1. route_group_map: { "POST /api/videos": "group_name" }
        2. group_param_config: { "group_name": { "replace_para": ["id"], "keep_pra": "video_id" } }
        """
        try:
            proc_data = self.normalized_params_process_data
            if not isinstance(proc_data, list):
                logger.warning("[ParamIndex] normalized_params_process_data 格式不正确，预期为 list")
                return
            
            for group_item in proc_data:
                if not isinstance(group_item, dict):
                    continue
                
                group_name = group_item.get("group")
                if not group_name:
                    continue
                
                data_list = group_item.get("data", [])
                if not isinstance(data_list, list):
                    continue
                
                for data_item in data_list:
                    if not isinstance(data_item, dict):
                        continue
                    
                    # 获取路由列表
                    route_names = data_item.get("route_name", [])
                    if not isinstance(route_names, list):
                        continue
                    
                    # 获取参数配置
                    param_config = data_item.get("parameters_name", {})
                    if not isinstance(param_config, dict):
                        continue
                    
                    # 记录每个路由到组的映射
                    for route in route_names:
                        if isinstance(route, str):
                            self.route_group_map[route] = group_name
                    
                    # 记录该组的参数配置（支持一个组有多个配置规则）
                    if group_name not in self.group_param_config:
                        self.group_param_config[group_name] = []
                    self.group_param_config[group_name].append(param_config)
            
            logger.info(f"[ParamIndex] 成功构建参数映射索引：{len(self.route_group_map)} 个路由，{len(self.group_param_config)} 个功能组")
            
        except Exception as e:
            logger.error(f"[ParamIndex] 构建参数映射索引失败: {e}", exc_info=True)

    # ===== 容器参数映射：按 container_resource_divide_results.json 的 route(group)→param 集合精确定义 =====
    @staticmethod
    def _normalize_group_prefix(s: str) -> str:
        """将 group/route 前缀规范化为类似 'api/patient' 的形式（去掉 scheme/host 与前导斜杠）。"""
        try:
            from urllib.parse import urlsplit
            if not isinstance(s, str):
                return ""
            s = s.strip()
            if not s:
                return ""
            if s.startswith("http://") or s.startswith("https://"):
                sp = urlsplit(s)
                s = sp.path or ""
            # 去掉 query/fragment
            s = s.split("?", 1)[0].split("#", 1)[0]
            s = s.strip().lstrip("/")
            return s
        except Exception:
            try:
                return str(s).strip().lstrip("/")
            except Exception:
                return ""

    @classmethod
    def build_container_params_by_group(cls, container_resource_divide_results: Any) -> Dict[str, Dict[str, set]]:
        """
        输入格式（示例）：{
          \"ou_id\": [{\"api/patient\": [\"facility\", ...]}, ...],
          \"resource_id\": [{\"api/medical_problem\": [\"uuid\", ...]}, ...]
        }
        输出：{category: {group_prefix: set(params)}}，其中 params 为空串会被过滤。
        """
        out: Dict[str, Dict[str, set]] = {"ou_id": {}, "resource_id": {}}
        if not isinstance(container_resource_divide_results, dict):
            return out
        for cat in ("ou_id", "resource_id"):
            lst = container_resource_divide_results.get(cat, [])
            if not isinstance(lst, list):
                continue
            for item in lst:
                if not isinstance(item, dict):
                    continue
                for group_key, params in item.items():
                    g = cls._normalize_group_prefix(group_key)
                    if not g:
                        continue
                    if g not in out[cat]:
                        out[cat][g] = set()
                    if isinstance(params, list):
                        for p in params:
                            if isinstance(p, str):
                                p2 = p.strip()
                                if p2:
                                    out[cat][g].add(p2)
                    elif isinstance(params, str):
                        p2 = params.strip()
                        if p2:
                            out[cat][g].add(p2)
        return out

    def is_container_param(self, category: str, group_name: str, param_name: str, step_route: Optional[str]=None) -> bool:
        """
        基于 self.container_params_by_group（由 container_resource_divide_results.json 生成）判断某参数是否为“容器参数”。
        - 优先按 group_name 命中；否则回退到从 step_route 提取前缀命中。
        """
        try:
            m = getattr(self, "container_params_by_group", None)
            if not isinstance(m, dict):
                return False
            cat = (category or "").strip()
            if cat not in m:
                return False
            group_map = m.get(cat, {})
            if not isinstance(group_map, dict):
                return False
            g1 = self._normalize_group_prefix(group_name or "")
            if g1 and g1 in group_map and (param_name in group_map[g1]):
                return True
            # fallback：从具体 route 推断 group 前缀（取前两段，如 api/patient）
            r = self._normalize_group_prefix(step_route or "")
            if r:
                parts = [p for p in r.split("/") if p]
                if len(parts) >= 2:
                    g2 = "/".join(parts[:2])
                else:
                    g2 = r
                if g2 in group_map and (param_name in group_map[g2]):
                    return True
        except Exception:
            return False
        return False


    def data_resource(self):
        """
        计算A交B-（A-B）的参数，其中A是请求参数，B是响应参数
        返回按功能组划分的结果
        计算这个集合的结果是发现有多少资源id，但其中可能也是容器资源id
        使用多线程并行处理各个功能组
        
        注意：本方法已重构为模块化实现，委托给ResourceIdentifier类
        """
        return self.resource_identifier.data_resource()
    
    def _data_resource_legacy(self):
        """
        原始实现（已废弃，保留用于参考）
        """
        def _process_group(group_name, apis, parameters_data):
            """
            处理单个功能组的资源参数计算
            """
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
                    def _flatten(lst):
                        flat = []
                        for item in lst:
                            if isinstance(item, list):
                                flat.extend(_flatten(item))
                            else:
                                flat.append(item)
                        return flat
                    _flat_req = _flatten(request_params_obj)
                    request_params = set([x for x in _flat_req if isinstance(x, str)])
                else:
                    request_params = set()

                if isinstance(response_params_obj, dict):
                    response_params = set(response_params_obj.keys())
                elif isinstance(response_params_obj, list):
                    def _flatten(lst):
                        flat = []
                        for item in lst:
                            if isinstance(item, list):
                                flat.extend(_flatten(item))
                            else:
                                flat.append(item)
                        return flat
                    _flat_resp = _flatten(response_params_obj)
                    response_params = set([x for x in _flat_resp if isinstance(x, str)])
                else:
                    response_params = set()

                all_request_params.update(request_params)
                all_response_params.update(response_params)
            
            # 对该功能组进行集合运算：A∩B-(A-B)
            # A∩B：请求参数和响应参数的交集
            result_params = all_request_params & all_response_params
            print(f"功能组 {group_name} 的参数: {result_params}")

            # 为每个功能组创建临时字典，从execution_results.json中匹配参数值
            temp_dict = {}
            if result_params:
                for param_name in result_params:
                    # 从parameters_data中查找匹配的参数值
                    param_value = _find_parameter_value(parameters_data, group_name, param_name)
                    temp_dict[param_name] = {"example_value": param_value}

            # 使用大模型来判断，哪些是用户资源id
            # 创建线程本地的llm_dict副本
            local_llm_dict = self.llm_dict.copy()
            local_llm_dict["param_dict"] = str(temp_dict)
            # 仅保留路由数据中除 response_parameters 之外的部分
            def _strip_response_parameters(data):
                if isinstance(data, dict):
                    return {k: (_strip_response_parameters(v) if isinstance(v, (dict, list)) else v) for k, v in data.items() if k != 'response_parameters'}
                elif isinstance(data, list):
                    return [_strip_response_parameters(item) for item in data]
                else:
                    return data
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
            
            return group_name, result_params
        
        def _find_parameter_value(parameters_data, group_name, param_name):
            """
            从execution_results.json中查找指定功能组和参数名对应的参数值
            """
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

        result = {}

        group_para_data = self.true_params
        parameters_data = self.case_generation_results_pacakges
    
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
                    group_name, result_params = future.result()
                    if result_params:
                        result[group_name] = result_params
                    logger.info(f"功能组 {group_name} 处理完成")
                except Exception as e:
                    logger.error(f"功能组 {group_name} 处理失败: {str(e)}")
        
        return result

    
    def data_container_resource(self):
        """
        找出同时出现在多个功能组中的请求参数
        返回格式: {"参数名": [{"group_name": ["路由一", "路由二"]}]}
        
        注意：本方法已重构为模块化实现，委托给ResourceIdentifier类
        """
        return self.resource_identifier.data_container_resource()
    
    def _data_container_resource_legacy(self):
        """
        原始实现（已废弃，保留用于参考）
        """
        group_para_data = self.normalized_params
        param_info = {}  # 存储每个参数的详细信息
        
        # 遍历每个功能组
        for group_name, apis in group_para_data.items():
            # 收集该功能组下所有API的请求参数和对应的路由
            for api_dict in apis:
                for endpoint, params in api_dict.items():
                    request_params = params.get('request_para', [])
                    
                    # 展开嵌套 list，提取其中的项（仅统计字符串项）
                    if isinstance(request_params, list):
                        def _flatten(lst):
                            flat = []
                            for item in lst:
                                if isinstance(item, list):
                                    flat.extend(_flatten(item))
                                else:
                                    flat.append(item)
                            return flat
                        request_params_iter = _flatten(request_params)
                    else:
                        request_params_iter = request_params
                    
                    # 为每个请求参数记录其出现的功能组和路由
                    for param in request_params_iter:
                        # request_params 应该是字符串列表，不应该包含嵌套列表
                        # 如果 param 不是字符串类型，记录警告并跳过
                        if not isinstance(param, str):
                            logger.warning(f"发现非字符串类型的参数: {param} (类型: {type(param)}) 在 {endpoint}")
                            continue
                            
                        if param not in param_info:
                            param_info[param] = {}
                        
                        if group_name not in param_info[param]:
                            param_info[param][group_name] = []
                        
                        # 添加路由到该参数在该功能组的路由列表中
                        if endpoint not in param_info[param][group_name]:
                            param_info[param][group_name].append(endpoint)
        
        # 筛选出现在多个功能组中的参数，并按要求格式化输出
        container_resources = {}
        for param, group_routes in param_info.items():
            if len(group_routes) > 1:  # 出现在多个功能组中
                container_resources[param] = []
                for group_name, routes in group_routes.items():
                    container_resources[param].append({group_name: routes})
        
        # 使用线程局部副本，避免共享 self.llm_dict 被并发写入
        try:
            local_llm_dict = dict(self.llm_dict)
        except Exception:
            local_llm_dict = {}
        local_llm_dict["parameters_and_routes"] = str(container_resources)
        # logger.info(container_resources)
        if not _llm_disabled:
            _last_err = None
            container_resources_results = {}
            for _attempt in range(1, _llm_max_retries + 1):
                try:
                    tmp_container_id = self.gpt_reply.getreply(
                        self.syn_prompt.synthesis_prompt("container_resource_judgement", local_llm_dict)
                    )
                    container_resources_results = eval(self.jsontool.list_formatting(tmp_container_id))
                    break
                except Exception as e:
                    _last_err = e
                    logger.info(f"容器资源 LLM 异常（第{_attempt}/{_llm_max_retries}次）: {str(e)}")
            else:
                logger.warning("容器资源 LLM 判定失败，应用兜底: 空结果")
                container_resources_results = {}
        else:
            container_resources_results = {}

        return container_resources_results
    
    def resource_package_generation(self,data_resource_id_result,container_resource_id_result):
        """
        生成容器类型id以及资源id，分别列一下：
        输出结构与 dependency_chain_package_generation 期望保持一致：
        {
            "ou_id": [{"group_name": ["参数A", "参数B"]}, ...],
            "resource_id": [{"group_name": ["资源id_1", "资源id_2"]}, ...]
        }
        
        注意：本方法已重构为模块化实现，委托给PackageGenerator类
        """
        return self.package_generator.resource_package_generation(
            data_resource_id_result, 
            container_resource_id_result
        )
    
    def _resource_package_generation_legacy(self,data_resource_id_result,container_resource_id_result):
        """
        原始实现（已废弃，保留用于参考）
        """
        # 1) 基于 normalized_params 建立 参数->功能组 映射
        def no_cross_resource_id(resource_packages):
            # 使用线程局部副本，避免共享 self.llm_dict 被并发写入
            import copy as _copy
            try:
                local_llm_dict = _copy.deepcopy(self.llm_dict)
            except Exception:
                local_llm_dict = dict(self.llm_dict) if isinstance(self.llm_dict, dict) else {}
            local_llm_dict["resource_id"] = str(resource_packages)
            if not _llm_disabled:
                _last_err = None
                for _attempt in range(1, _llm_max_retries + 1):
                    try:
                        tmp_results_list = self.gpt_reply.getreply(
                            self.syn_prompt.synthesis_prompt(
                                "resources_item_filter", local_llm_dict
                            )
                        )
                        resource_packages["filterd_resource_id"] = eval(self.jsontool.list_formatting(tmp_results_list))
                        break
                    except Exception as e:
                        _last_err = e
                        logger.info(f"资源项过滤 LLM 异常（第{_attempt}/{_llm_max_retries}次）: {str(e)}")
                else:
                    logger.warning("资源项过滤 LLM 失败，跳过过滤兜底")
            return resource_packages

        group_para_data = self.normalized_params
        param_groups_map = {}
        for group_name, apis in group_para_data.items():
            for api_dict in apis:
                for endpoint, params in api_dict.items():
                    request_params = params.get('request_para', [])
                    # 展开嵌套 list，提取其中的字符串项
                    if isinstance(request_params, list):
                        def _flatten(lst):
                            flat = []
                            for item in lst:
                                if isinstance(item, list):
                                    flat.extend(_flatten(item))
                                else:
                                    flat.append(item)
                            return flat
                        request_params_iter = _flatten(request_params)
                    else:
                        request_params_iter = request_params
                    for p in request_params_iter:
                        if not isinstance(p, str):
                            logger.warning(f"发现非字符串类型的参数: {p} (类型: {type(p)}) 在 {endpoint}")
                            continue
                        if p not in param_groups_map:
                            param_groups_map[p] = set()
                        param_groups_map[p].add(group_name)

        # 2) 规范化 container_resource_id_result 为按功能组分组的 [{group_name: [params]}]
        ou_id_grouped_map = {}
        if isinstance(container_resource_id_result, list):
            for item in container_resource_id_result:
                if isinstance(item, dict):
                    # 已是 {group_name: [params]} 结构，合并到 map
                    for g, plist in item.items():
                        ou_id_grouped_map.setdefault(g, [])
                        for p in plist if isinstance(plist, list) else []:
                            if p not in ou_id_grouped_map[g]:
                                ou_id_grouped_map[g].append(p)
                elif isinstance(item, str):
                    # 是参数名，按出现的功能组分配
                    groups = param_groups_map.get(item, set())
                    for g in groups:
                        ou_id_grouped_map.setdefault(g, [])
                        if item not in ou_id_grouped_map[g]:
                            ou_id_grouped_map[g].append(item)
                else:
                    # 非预期类型，跳过
                    continue
        elif isinstance(container_resource_id_result, dict):
            # 可能是 {param: [{group_name: [routes]}]} 或其它形式，尽量提取 group_name
            for param, group_list in container_resource_id_result.items():
                if isinstance(group_list, list):
                    for gl in group_list:
                        if isinstance(gl, dict):
                            for g in gl.keys():
                                ou_id_grouped_map.setdefault(g, [])
                                if param not in ou_id_grouped_map[g]:
                                    ou_id_grouped_map[g].append(param)
                # 其它形式忽略
        else:
            # 非预期输入，初始化为空
            ou_id_grouped_map = {}

        # 转换为期望的列表结构
        ou_id_list = [{g: plist} for g, plist in ou_id_grouped_map.items()]

        # 3) 基于容器参数集合，过滤普通资源 id
        container_params_set = set()
        for plist in ou_id_grouped_map.values():
            for p in plist:
                container_params_set.add(p)

        resource_id_list = []
        for group_name, value in data_resource_id_result.items():
            # value 是列表，过滤掉已经在容器参数集合中的项
            if isinstance(value, list):
                filtered_value = [item for item in value if item not in container_params_set]
            else:
                # 容错：非列表则跳过过滤直接转换为空
                filtered_value = []
            if filtered_value:
                resource_id_list.append({group_name: filtered_value})

        container_resource_divide_result = {
            "ou_id": ou_id_list,
            "resource_id": resource_id_list
        }
        final_results = no_cross_resource_id(container_resource_divide_result)
        return final_results
    

    def dependency_chain_package_generation(self,resource_id_dict):
        """
        首先区分好普通资源id和资源容器id,需要先构建好每个id验证的执行链，也就是说所有将这个id列为请求参数的都需要校验。
        不再局限于功能组匹配，遍历所有依赖链条，只要最后一个步骤包含指定资源id就纳入结果。
        {
        "resource_id":[
        "group_name":[
            "参数1":{"1":{},"2":{}},
            
        ]
        ]
        }
        
        注意：本方法已重构为模块化实现，委托给PackageGenerator类
        """
        return self.package_generator.dependency_chain_package_generation(resource_id_dict)
    
    def _dependency_chain_package_generation_legacy(self,resource_id_dict):
        """
        原始实现（已废弃，保留用于参考）
        """
        def _contains_resource_id(request_params, resource_id, step_data=None):
            """
            检查请求参数中是否包含指定的资源id
            只检查请求参数（路径参数、查询参数、请求体参数），不检查响应参数
            当无法匹配时返回False，让调用方正确处理空结果
            """
            if not isinstance(request_params, dict):
                return False
            
            parameters = request_params.get("parameters", {})
            
            # 检查请求路由模板中是否包含路径参数（如{postId}、{video_id}）
            if step_data and isinstance(step_data, dict):
                route = step_data.get("route", "")
                # 仅匹配与 resource_id 完全一致的路径参数（例如 resource_id=="video_id" -> "{video_id}")
                if f"{{{resource_id}}}" in route:
                    return True
            
            # 检查URL查询参数中是否包含资源id（仅匹配相同键名）
            url = parameters.get("url", "")
            if "?" in url:
                query_part = url.split("?", 1)[1]
                # 以 & 或 字符串开头/结尾作为边界，避免子串误匹配
                pairs = [seg.split("=", 1)[0] for seg in query_part.split("&")]
                if resource_id in pairs:
                    return True
            
            # 检查json请求体参数中是否包含资源id（仅匹配相同键名）
            json_data = parameters.get("json", {})
            if isinstance(json_data, dict):
                if resource_id in json_data:
                    return True
                    
            # 检查form表单参数中是否包含资源id（仅匹配相同键名）
            form_data = parameters.get("data", {})
            if isinstance(form_data, dict):
                if resource_id in form_data:
                    return True
            
            # 新增：检查 params 查询参数字段中是否包含资源id（仅匹配键名）
            params_data = parameters.get("params", {})
            if isinstance(params_data, dict):
                if resource_id in params_data:
                    return True
                        
            return False
        # 新增：递归提取一个步骤容器中的所有接口字典（包含 请求参数/类型/请求路由/请求方式）
        def _extract_api_steps_from_container(container):
            api_steps = []
            if isinstance(container, dict):
                # 该字典是否是接口字典
                if "request_params" in container and ("route" in container or "method" in container):
                    api_steps.append(container)
                else:
                    for v in container.values():
                        api_steps.extend(_extract_api_steps_from_container(v))
            elif isinstance(container, list):
                for item in container:
                    api_steps.extend(_extract_api_steps_from_container(item))
            return api_steps
        
        # 新增：将链条按顶层数字步骤键展开，每个步骤得到其包含的所有接口
        def _flatten_chain_steps(chain):
            step_keys = sorted([k for k in chain.keys() if k.isdigit()], key=lambda x: int(x))
            steps = []
            for step_key in step_keys:
                step_container = chain.get(step_key)
                step_apis = _extract_api_steps_from_container(step_container)
                steps.append(step_apis)
            return steps
        
        # 新增：检查接口是否属于指定功能组（基于 api_doc_data 的 "METHOD /route" 键）
        def _api_belongs_to_group(api_step, group_name):
            try:
                group_map = api_doc_data.get(group_name, {})
            except Exception:
                group_map = {}
            if not isinstance(group_map, dict) or not group_map:
                return False
            method = (api_step.get("method") or api_step.get("method") or "").upper()
            route = api_step.get("route") or api_step.get("route") or ""
            if not route:
                return False

            # 优先尝试标准化匹配：从文档键中解析出 (METHOD, ROUTE)
            def parse_key(key):
                if not isinstance(key, str):
                    return None, None, key
                s = key.strip()
                # 将不同分隔符统一为一个空格：例如 "GET：/x", "GET:/x", "GET /x"
                s = s.replace("：", " ").replace(":", " ")
                parts = s.split()
                if len(parts) >= 2:
                    return parts[0].upper(), " ".join(parts[1:]), key
                # 仅路由的情况
                return None, s, key

            # 1) 完整匹配：方法+路由完全一致或键以路由结尾
            if method:
                iface_candidates = {f"{method} {route}", f"{method}:{route}", f"{method}：{route}"}
                for cand in iface_candidates:
                    if cand in group_map:
                        return True
                # 尝试在键中解析并比对
                for key in group_map.keys():
                    km, kr, _ = parse_key(key)
                    if km and kr and km == method and kr == route:
                        return True

            # 2) 路由匹配：忽略方法，仅按路由匹配
            for key in group_map.keys():
                km, kr, raw = parse_key(key)
                if kr and kr == route:
                    return True
                if isinstance(key, str) and key.endswith(route):
                    return True

            return False
        result = {}
        result["resource_id"] = {}
        result["ou_id"] = {}
        api_doc_data = self.true_params
        dependency_chain = self.case_generation_results_pacakges
        # 新增：过滤参数集合，命中过滤名单的参数仅在自身功能组内搜索依赖链
        filtered_params_set = set(resource_id_dict.get("filterd_resource_id", [])) if isinstance(resource_id_dict, dict) else set()
  
        # 并发化处理：将 resource_id 与 ou_id 两类任务统一并发执行
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import os

        def _process_group_entry(kind, group_name, resource_ids):
            local_result = {kind: {group_name: {}}}
            for resource_id in (resource_ids or []):
                local_result[kind][group_name].setdefault(resource_id, {"cross": [], "group": []})
                # 处理 cross 结构
                cross_groups_iter = dependency_chain.get("cross", {})
                # if group_name == "member/address":
                #     print("ok")
                for chain_group_name, chains in cross_groups_iter.items():
                    for chain in chains:
                        steps = _flatten_chain_steps(chain)
                        if kind == "ou_id":
                            # ou_id：只要链中任意步骤的接口请求参数包含该参数，就纳入结果
                            found_resource_id = any(
                                _contains_resource_id(api_step.get("request_params", {}), resource_id, api_step)
                                for step_apis in steps for api_step in step_apis
                            )
                            last_step_apis = steps[-1] if steps else []  # 供后续一致性使用
                        else:
                            # resource_id：沿用原逻辑，仅检查最后一步
                            last_step_apis = steps[-1] if steps else []
                            found_resource_id = any(
                                _contains_resource_id(api_step.get("request_params", {}), resource_id, api_step)
                                for api_step in last_step_apis
                            )
                        if found_resource_id:
                            # 对所有 resource_id 应用功能组过滤：最后一个接口必须属于该功能组
                            if kind != "ou_id":
                                belongs = any(_api_belongs_to_group(api_step, group_name) for api_step in last_step_apis)
                                if not belongs:
                                    continue
                            local_result[kind][group_name][resource_id]["cross"].append(chain)

                # 处理 group 结构
                group_groups_iter = dependency_chain.get("group", {})
                for chain_group_name, chains in group_groups_iter.items():
                    for chain in chains:
                        steps = _flatten_chain_steps(chain)
                        if kind == "ou_id":
                            # ou_id：只要链中任意步骤的接口请求参数包含该参数，就纳入结果
                            found_resource_id = any(
                                _contains_resource_id(api_step.get("request_params", {}), resource_id, api_step)
                                for step_apis in steps for api_step in step_apis
                            )
                            last_step_apis = steps[-1] if steps else []  # 供后续一致性使用
                        else:
                            # resource_id：沿用原逻辑，仅检查最后一步
                            last_step_apis = steps[-1] if steps else []
                            found_resource_id = any(
                                _contains_resource_id(api_step.get("request_params", {}), resource_id, api_step)
                                for api_step in last_step_apis
                            )
                        if found_resource_id:
                            # 对所有 resource_id 应用功能组过滤：最后一个接口必须属于该功能组
                            if kind != "ou_id":
                                belongs = any(_api_belongs_to_group(api_step, group_name) for api_step in last_step_apis)
                                if not belongs:
                                    continue
                            local_result[kind][group_name][resource_id]["group"].append(chain)
            return local_result

        # 构建任务列表
        tasks = []
        for group_dict in resource_id_dict.get("resource_id", []):
            if not group_dict:
                continue
            for group_name, resource_ids in group_dict.items():
                tasks.append(("resource_id", group_name, resource_ids))
        for group_dict in resource_id_dict.get("ou_id", []):
            if not group_dict:
                continue
            for group_name, resource_ids in group_dict.items():
                tasks.append(("ou_id", group_name, resource_ids))

        # 线程池大小：CPU核数的两倍，上限32
        max_workers = min(32, (os.cpu_count() or 4) * 2)

        # 并发执行并合并结果（线程安全地在主线程合并）
        result = {"resource_id": {}, "ou_id": {}}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_process_group_entry, kind, group_name, resource_ids) for (kind, group_name, resource_ids) in tasks]
            for future in as_completed(futures):
                partial = future.result()
                for kind, gmap in partial.items():
                    for gname, ridmap in gmap.items():
                        result[kind].setdefault(gname, {})
                        for rid, cg in ridmap.items():
                            if rid not in result[kind][gname]:
                                result[kind][gname][rid] = {"cross": [], "group": []}
                            # 防御性：若列表被意外替换为迭代器，强制转回列表
                            if not isinstance(result[kind][gname][rid]["cross"], list):
                                result[kind][gname][rid]["cross"] = list(result[kind][gname][rid]["cross"])
                            if not isinstance(result[kind][gname][rid]["group"], list):
                                result[kind][gname][rid]["group"] = list(result[kind][gname][rid]["group"])
                            result[kind][gname][rid]["cross"].extend(cg.get("cross", []))
                            result[kind][gname][rid]["group"].extend(cg.get("group", []))

        return result 

    
    def execution_packages(self, url ,authority_account, dependency_chain_request_packages,container_resource_divide_results):
        """
        执行依赖链包，按如下两轮策略输出：
        1) 首轮：data_account 独立完整执行整条依赖链，使用嵌套结构记录结果（不与第二轮共享响应池）；
        2) 次轮：
           - resource_id：data_account 执行除最后一个顶层顺序键外的所有步骤，test_account 执行最后一个顶层步骤；
           - ou_id：data_account 仅执行第一个顶层顺序键的所有步骤，其余由 test_account 执行；
        两轮的第二轮使用共享的响应参数池，且均保留嵌套结构（顶层数字键 -> 子步骤数字键或索引）。
        """
        import copy
        from urllib.parse import urlsplit, urlunsplit

        # 从容器拆分结果中构建“声明过的参数名集合”，用于限制跨账号迁移
        def _build_declared_param_set(container_divide):
            declared = set()
            try:
                def walk(obj):
                    if isinstance(obj, dict):
                        for _, v in obj.items():
                            walk(v)
                    elif isinstance(obj, list):
                        for item in obj:
                            walk(item)
                    else:
                        if isinstance(obj, str):
                            declared.add(obj)
                walk(container_divide)
            except Exception:
                pass
            return declared

        container_declared_params = _build_declared_param_set(container_resource_divide_results)

        def _parse_base(url_param):
            if not url_param:
                return ("http", "localhost")
            if url_param.startswith("http://") or url_param.startswith("https://"):
                sp = urlsplit(url_param)
                scheme = sp.scheme or "http"
                netloc = sp.netloc or (sp.path.strip("/") if sp.path else "")
                return (scheme, netloc)
            else:
                return ("http", url_param.strip("/"))

        def _replace_domain(original_url, base_scheme, base_netloc, route=None):
            if (not original_url) and route:
                return f"{base_scheme}://{base_netloc}{route}"
            if original_url and original_url.startswith("/"):
                return f"{base_scheme}://{base_netloc}{original_url}"
            sp = urlsplit(original_url or "")
            path = sp.path or (route or "")
            return urlunsplit((base_scheme, base_netloc, path, sp.query, sp.fragment))

        def _merge_headers(headers, auth_info):
            headers = headers.copy() if isinstance(headers, dict) else {}
            if isinstance(auth_info, dict):
                for k, v in auth_info.items():
                    headers[k] = v
            elif isinstance(auth_info, str) and auth_info:
                headers["Authorization"] = auth_info
            return headers

        def _flatten_response(resp):
            """
            平铺响应，提取所有叶子节点的key-value对
            
            排除错误相关字段，防止错误消息污染参数池（作为双重保险）
            """
            flat = {}
            # 错误相关字段黑名单（不提取这些字段到参数池）
            BLACKLIST_FIELDS = {'error', 'message', 'msg', 'error_message', 'exception', 'error_description'}
            
            def walk(obj):
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        # 跳过黑名单字段（防止错误消息被提取）
                        if k.lower() in BLACKLIST_FIELDS:
                            continue
                        if isinstance(v, (dict, list)):
                            walk(v)
                        else:
                            if v is not None:
                                flat[k] = v
                elif isinstance(obj, list):
                    for item in obj:
                        walk(item)
            walk(resp)
            return flat

        # 新增：从响应中提取优先值（add 优先级最高，其次 list query 的最后一项）
        def _extract_preferred_values(resp_json, step_obj):
            step_type = (step_obj or {}).get("type", "") or ""
            try:
                if isinstance(resp_json, dict):
                    # 对 list query：支持多种常见列表字段名，按优先级检查
                    if step_type.lower().strip() == "list query":
                        # 按优先级检查常见列表字段（data 优先级最高）
                        last_item = None
                        for list_key in ("data", "orders", "items", "list", "results", "records"):
                            if isinstance(resp_json.get(list_key), list) and resp_json.get(list_key):
                                last_item = resp_json[list_key][-1]
                                break
                        if isinstance(last_item, dict):
                            return _flatten_response(last_item), 2
                        else:
                            # 非 dict 的最后项仍旧平铺整个响应
                            return _flatten_response(resp_json), 2
                    # 对 add 类型，整体平铺，次高优先级
                    if step_type.lower().strip() == "add":
                        return _flatten_response(resp_json), 1
                    # 其他类型，默认优先级最低
                    return _flatten_response(resp_json), 0
                elif isinstance(resp_json, list) and resp_json:
                    # 若响应本身是列表，则取最后一项
                    last_item = resp_json[-1]
                    return _flatten_response(last_item if isinstance(last_item, dict) else resp_json), 0
            except Exception:
                pass
            return {}, 0

        # 新增：按优先级更新共享池，确保 add > list query(last) > default
        def _update_pool_with_response(pool_values, pool_priority, resp_json, step_obj, pool_metadata=None):
            """
            从响应中提取参数并更新资源池（分组存储），同时记录参数来源的元数据
            
            ⚠️ 重要：只从成功响应（status_code=200）中提取参数，避免错误消息污染参数池
            
            :param pool_values: 资源池值字典（分组结构：{group: {param: value}}）
            :param pool_priority: 资源池优先级字典（分组结构：{group: {param: priority}}）
            :param resp_json: 响应JSON
            :param step_obj: 步骤对象，包含请求路由等信息
            :param pool_metadata: 资源池元数据字典
            """
            if pool_metadata is None:
                pool_metadata = {}
            
            # 新增：检查执行状态，只有成功响应才提取参数
            exec_status = (step_obj or {}).get("execution_status", {})
            status_code = exec_status.get("status_code")
            status = exec_status.get("status", "")
            
            # 如果不是成功响应，跳过参数提取
            if status_code != 200 or status == "error":
                logger.info(f"[DEBUG-PoolSkip] 跳过非成功响应的参数提取: status_code={status_code}, status={status}")
                return
            
            # 获取当前API路由和所属组
            route = (step_obj or {}).get("route") if isinstance(step_obj, dict) else None
            api_route = f"{(step_obj or {}).get('method', '')} {route}" if route else None
            source_group = (_get_api_group(api_route) if api_route else None) or "_global"
            
            # 确保分组存在
            if source_group not in pool_values:
                pool_values[source_group] = {}
            if source_group not in pool_priority:
                pool_priority[source_group] = {}
            
            extracted, pri = _extract_preferred_values(resp_json, step_obj)
            for k, v in extracted.items():
                if v is None or v == "":
                    continue
                prev_pri = pool_priority[source_group].get(k, -1)
                if pri >= prev_pri:
                    pool_values[source_group][k] = v
                    pool_priority[source_group][k] = pri
                    # 记录元数据（仍使用扁平结构以保持兼容）
                    meta_key = f"{source_group}:{k}"
                    if meta_key not in pool_metadata or pri >= prev_pri:
                        pool_metadata[meta_key] = {
                            "source_api": api_route,
                            "source_group": source_group,
                            "source_field": k,
                            "source_location": "response"
                        }
                        logger.info(f"[DEBUG-PoolMeta-Resp] 记录响应参数元数据: {k}={v}, group={source_group}, api={api_route}")

        def _update_pool_with_request(pool_values, pool_priority, req_params, pri, step_obj=None, pool_metadata=None):
            """
            更新资源池（分组存储），并记录参数来源的元数据（来源API、来源组）
            :param pool_values: 资源池值字典（分组结构：{group: {param: value}}）
            :param pool_priority: 资源池优先级字典（分组结构：{group: {param: priority}}）
            :param req_params: 请求参数
            :param pri: 当前优先级
            :param step_obj: 步骤对象，包含请求路由等信息
            :param pool_metadata: 资源池元数据字典
            """
            if not isinstance(req_params, dict):
                return
            if pool_metadata is None:
                pool_metadata = {}
            
            # 获取当前API路由和所属组
            route = (step_obj or {}).get("route") if isinstance(step_obj, dict) else None
            api_route = f"{(step_obj or {}).get('method', '')} {route}" if route else None
            source_group = (_get_api_group(api_route) if api_route else None) or "_global"
            
            # 确保分组存在
            if source_group not in pool_values:
                pool_values[source_group] = {}
            if source_group not in pool_priority:
                pool_priority[source_group] = {}
            
            for body_key in ("params", "json", "data"):
                body = req_params.get(body_key, {})
                if isinstance(body, dict):
                    for k, v in body.items():
                        if v is None or v == "":
                            continue
                        prev_pri = pool_priority[source_group].get(k, -1)
                        if pri >= prev_pri:
                            pool_values[source_group][k] = v
                            pool_priority[source_group][k] = pri
                            # 记录元数据
                            meta_key = f"{source_group}:{k}"
                            if meta_key not in pool_metadata or pri >= prev_pri:
                                pool_metadata[meta_key] = {
                                    "source_api": api_route,
                                    "source_group": source_group,
                                    "source_field": k,
                                    "source_location": body_key
                                }
                                logger.info(f"[DEBUG-PoolMeta] 记录参数元数据: {k}={v}, group={source_group}, api={api_route}")
            
            # 解析路径参数，基于 请求路由 的占位符与实际 URL 对齐
            try:
                url_s = req_params.get("url")
                if isinstance(route, str) and route and isinstance(url_s, str) and url_s:
                    from urllib.parse import urlsplit
                    url_path = None
                    try:
                        url_path = urlsplit(url_s).path or url_s
                    except Exception:
                        url_path = url_s
                    route_parts = [p for p in route.split('/') if p != '']
                    url_parts = [p for p in url_path.split('/') if p != '']
                    if len(url_parts) >= len(route_parts):
                        for idx, rseg in enumerate(route_parts):
                            if rseg.startswith('{') and rseg.endswith('}'):
                                name = rseg[1:-1]
                                if idx < len(url_parts):
                                    val = url_parts[idx]
                                    # 验证值是否为合法资源ID，防止"recent"等路径片段被错误提取
                                    if val not in (None, "") and is_valid_resource_id(val):
                                        prev_pri = pool_priority[source_group].get(name, -1)
                                        if pri >= prev_pri:
                                            pool_values[source_group][name] = val
                                            pool_priority[source_group][name] = pri
                                            # 记录元数据
                                            meta_key = f"{source_group}:{name}"
                                            if meta_key not in pool_metadata or pri >= prev_pri:
                                                pool_metadata[meta_key] = {
                                                    "source_api": api_route,
                                                    "source_group": source_group,
                                                    "source_field": name,
                                                    "source_location": "path"
                                                }
                                            logger.info(f"[DEBUG-PoolExtract] 从URL提取path参数: {name}={val}, group={source_group}")
                                    else:
                                        logger.info(f"[DEBUG-PoolExtract] 跳过无效path参数值: {name}={val}")
            except Exception:
                pass

        def _is_grouped_pool(pool):
            """检测是否为分组池结构"""
            if not pool:
                return False
            first_key = next(iter(pool), None)
            return isinstance(pool.get(first_key), dict)
        
        def _get_value_from_grouped_pool(pool_values, aliases, target_group=None):
            """
            从分组池中获取值，实现分组优先查找逻辑
            :param pool_values: 分组池（{group: {param: value}}）
            :param aliases: 别名列表
            :param target_group: 目标组名称
            :return: 参数值或None
            """
            if not _is_grouped_pool(pool_values):
                # 旧结构，直接查找
                for a in aliases:
                    if a in pool_values and pool_values.get(a) not in (None, ""):
                        return pool_values.get(a)
                return None

            # 优先级1：精确组匹配
            if target_group and target_group in pool_values:
                group_pool = pool_values[target_group]
                for a in aliases:
                    if a in group_pool and group_pool.get(a) not in (None, ""):
                        logger.info(f"[DEBUG-GroupedPool] 精确组匹配: group={target_group}, alias={a}, value={group_pool.get(a)}")
                        return group_pool.get(a)
            
            # 优先级2：前缀匹配组（同一功能域）
            if target_group:
                for g in pool_values:
                    if g == "_global" or g is None:
                        continue
                    # 前缀匹配：g 是 target_group 的前缀，或反之
                    if isinstance(g, str) and (target_group.startswith(g) or g.startswith(target_group)):
                        group_pool = pool_values[g]
                        for a in aliases:
                            if a in group_pool and group_pool.get(a) not in (None, ""):
                                logger.info(f"[DEBUG-GroupedPool] 前缀组匹配: target={target_group}, matched_group={g}, alias={a}, value={group_pool.get(a)}")
                                return group_pool.get(a)
            
            # 优先级3：全局池
            if "_global" in pool_values:
                global_pool = pool_values["_global"]
                for a in aliases:
                    if a in global_pool and global_pool.get(a) not in (None, ""):
                        logger.info(f"[DEBUG-GroupedPool] 全局池匹配: alias={a}, value={global_pool.get(a)}")
                        return global_pool.get(a)
            
            # 优先级4：任意组匹配（最后的fallback，可能存在跨账号污染风险）
            # 仅在前面所有策略都失败时使用，并记录警告日志
            for g, group_pool in pool_values.items():
                if g in ("_global", target_group):  # 跳过已经尝试过的组
                    continue
                if isinstance(group_pool, dict):
                    for a in aliases:
                        if a in group_pool and group_pool.get(a) not in (None, ""):
                            logger.warning(f"[WARNING-GroupedPool] 跨组获取参数（可能存在污染）: target_group={target_group}, fallback_group={g}, alias={a}, value={group_pool.get(a)}")
                            return group_pool.get(a)
            
            logger.info(f"[DEBUG-GroupedPool] 未找到匹配值: target_group={target_group}, aliases={aliases}")
            
            return None

        def _get_value_from_pool(pool_values, aliases):
            """兼容性包装函数"""
            return _get_value_from_grouped_pool(pool_values, aliases, target_group=None)

        def _get_value_based_on_config(pool_values, target_param, target_group, api_route=None):
            """
            基于 parameters_dict_all.json 配置进行精确参数查找
            :param pool_values: 资源池（支持分组和扁平结构）
            :param target_param: 目标参数名，如 "video_id" 或 "id"
            :param target_group: 目标功能组名，如 "identity/api/v2/user/videos"
            :param api_route: API路由（可选），用于确定功能组
            :return: 参数值或None
            """
            try:
                # 1. 如果提供了 api_route，优先从 route_group_map 查找组
                if api_route and api_route in self.route_group_map:
                    target_group = self.route_group_map[api_route]
                    logger.info(f"[ConfigLookup] 从路由 {api_route} 确定功能组: {target_group}")
                
                if not target_group:
                    logger.info(f"[ConfigLookup] 未找到功能组，无法进行配置查找")
                    return None
                
                # 2. 获取目标组的参数配置列表
                config_list = self.group_param_config.get(target_group, [])
                if not config_list:
                    logger.info(f"[ConfigLookup] 组 {target_group} 没有参数配置")
                    return None
                
                # 3. 遍历所有配置规则，找到匹配的
                for config in config_list:
                    if not isinstance(config, dict):
                        continue
                    
                    keep_para = config.get("keep_pra")  # e.g., "video_id"
                    replace_paras = config.get("replace_para", [])
                    if not isinstance(replace_paras, (list, tuple)):
                        replace_paras = [replace_paras] if replace_paras else []
                    
                    # 确定要查找的候选别名列表
                    candidates = []
                    if target_param == keep_para:
                        # 查找 video_id，需要找 id
                        candidates.extend(replace_paras)
                        logger.info(f"[ConfigLookup] 目标参数 {target_param} 是 keep_pra，查找候选: {candidates}")
                    elif target_param in replace_paras:
                        # 查找 id，需要找 video_id
                        if keep_para:
                            candidates.append(keep_para)
                        logger.info(f"[ConfigLookup] 目标参数 {target_param} 在 replace_para 中，查找候选: {candidates}")
                    else:
                        # 当前配置不匹配，尝试下一个
                        continue
                    
                    # 4. 在同一组的池中查找这些候选参数
                    # 检查是否是分组池
                    if _is_grouped_pool(pool_values):
                        group_pool = pool_values.get(target_group, {})
                        if isinstance(group_pool, dict):
                            for candidate in candidates:
                                value = group_pool.get(candidate)
                                if value not in (None, "", 0):
                                    logger.info(f"[ConfigLookup] 在组 {target_group} 中找到 {candidate}={value}")
                                    return value
                    else:
                        # 扁平池，直接查找
                        for candidate in candidates:
                            value = pool_values.get(candidate)
                            if value not in (None, "", 0):
                                logger.info(f"[ConfigLookup] 在扁平池中找到 {candidate}={value}")
                                return value
                
                logger.info(f"[ConfigLookup] 目标参数 {target_param} 在所有配置中都未找到匹配")
                return None
                
            except Exception as e:
                logger.error(f"[ConfigLookup] 配置查找失败: {e}", exc_info=True)
                return None

        def _build_param_priority_order(aliases, target_param=None, group_name=None):
            """
            根据字段名特征动态计算优先级顺序
            
            通用规则（基于语义特征，无硬编码）：
            1. 与目标参数名完全匹配的优先级最高
            2. 与功能组名称相关的优先级更高
            3. 字段名越长（包含更多语义信息）优先级越高
            4. 包含下划线的优先于纯驼峰命名的
            5. 特定名称（如xxx_id）优先于通用名称（如id）
            
            Args:
                aliases: 参数别名列表
                target_param: 目标参数名（用于精确匹配）
                group_name: 功能组名称（用于语义相关性判断）
            
            Returns:
                List[str]: 按优先级排序的别名列表
            """
            def _specificity_score(alias):
                """
                计算参数名的"具体性"分数，分数越低优先级越高
                """
                name = alias.lower()
                score = 0
                
                # 维度1：与目标参数完全匹配（最高优先级）
                if target_param and name == target_param.lower():
                    score -= 1000
                
                # 维度2：与功能组名称的相关性
                if group_name:
                    group_normalized = group_name.lower().replace('/', '_').replace('-', '_')
                    # 提取功能组的最后一段（通常是资源名）
                    group_parts = [p for p in group_normalized.split('_') if p]
                    resource_name = group_parts[-1] if group_parts else ''
                    
                    # 如果参数名包含资源名，优先级更高
                    if resource_name and resource_name in name:
                        score -= 100
                
                # 维度3：字段名长度（越长越具体）
                # 长度越长，包含的语义信息越多，通常越具体
                score -= len(name)
                
                # 维度4：命名风格（下划线风格通常更规范）
                if '_' in name:
                    score -= 10  # 下划线命名优先
                
                # 维度5：避免纯通用标识符
                # 单字母或极短的通用名称优先级降低
                generic_identifiers = {'id', 'uuid', 'guid', 'code', 'key', 'no', 'num'}
                if name in generic_identifiers:
                    score += 50  # 通用标识符优先级较低
                
                return score
            
            # 按具体性分数排序（分数越低优先级越高）
            sorted_aliases = sorted(aliases, key=lambda a: (_specificity_score(a), a))
            return sorted_aliases

        def _get_value_with_fallback(pool_values, aliases, target_group=None, target_param=None):
            """
            按优先级顺序从池中获取参数值，支持多个候选值
            
            Args:
                pool_values: 参数池
                aliases: 参数别名列表
                target_group: 目标功能组
                target_param: 目标参数名（用于优先级计算）
            
            Returns:
                List[Tuple[str, Any]]: [(alias, value), ...] 按优先级排序的所有可用值
            """
            # 按优先级排序别名
            sorted_aliases = _build_param_priority_order(aliases, target_param=target_param, group_name=target_group)
            
            # 收集所有可用值
            available_values = []
            seen_values = set()  # 去重
            
            for alias in sorted_aliases:
                value = _get_value_from_grouped_pool(pool_values, [alias], target_group)
                if value is not None:
                    # 去重：相同值只保留第一次出现的（优先级更高）
                    value_str = str(value)
                    if value_str not in seen_values:
                        available_values.append((alias, value))
                        seen_values.add(value_str)
            
            return available_values

        def _get_alias_value(pool, key, group_name, param_name, pool_metadata=None):
            """
            获取别名参数值，使用以下优先级：
            1. 直接查找 key
            2. 基于 parameters_dict_all.json 配置的精确查找
            3. 基于别名的模糊查找（兜底）
            
            :param pool: 资源池（支持分组和扁平结构）
            :param key: 查找的键
            :param group_name: 功能组名称
            :param param_name: 参数名称  
            :param pool_metadata: 资源池元数据（当前版本中已不使用，保留参数兼容性）
            :return: 参数值或None
            """
            try:
                logger.info(f"[Alias] 查找参数: key={key}, group={group_name}, param={param_name}")
                
                # 1. 首先尝试直接查找
                if _is_grouped_pool(pool):
                    group_pool = pool.get(group_name, {})
                    if isinstance(group_pool, dict) and key in group_pool:
                        value = group_pool[key]
                        if value not in (None, "", 0):
                            logger.info(f"[Alias] 直接查找成功: {key}={value}")
                            return value
                else:
                    if key in pool:
                        value = pool[key]
                        if value not in (None, "", 0):
                            logger.info(f"[Alias] 直接查找成功（扁平池）: {key}={value}")
                            return value
                
                # 2. 基于配置的精确查找（核心优化）
                config_value = _get_value_based_on_config(pool, key, group_name)
                if config_value is not None:
                    logger.info(f"[Alias] 配置查找成功: {key}={config_value}")
                    return config_value
                
                # 3. 兜底：使用原有的别名查找逻辑
                aliases = _alias_params_for_group(group_name, param_name) if param_name else [key]
                if key not in aliases:
                    aliases = [key] + list(aliases)
                logger.info(f"[Alias] 尝试别名查找: aliases={aliases}")
                
                result = _get_value_from_grouped_pool(pool, aliases, target_group=group_name)
                if result is not None:
                    logger.info(f"[Alias] 别名查找成功: {key}={result}")
                    return result
                
                logger.info(f"[Alias] 所有查找方式均未找到: key={key}, group={group_name}")
            except Exception as e:
                logger.error(f"[Alias] 查找异常: {e}", exc_info=True)
            return None

        def _fill_params_from_pool_legacy(req_params, pool, step_obj, group_name=None, param_name=None, pool_metadata=None):
            """
            旧行为：允许用共享池覆盖同名字段（用于 data_account 构建上下文池）。
            支持分组池结构：{group: {param: value}}
            注意：严格参数级模式下（test_case != None）不再使用该函数。
            """
            # 初始化元数据字典
            if pool_metadata is None:
                pool_metadata = {}
            
            # 提取路由中的path参数名，这些参数不应出现在body中
            import re
            path_param_names = set()
            route = (step_obj or {}).get("route", "") if isinstance(step_obj, dict) else ""
            if isinstance(route, str):
                path_param_names = set(re.findall(r'\{([A-Za-z0-9_]+)\}', route))
            
            # 记录参数别名信息到元数据（用于后续重试）
            def _record_param_metadata(param_key):
                """记录参数的别名信息"""
                if param_key not in pool_metadata and group_name and param_name:
                    aliases = _alias_params_for_group(group_name, param_name)
                    if aliases:
                        pool_metadata[param_key] = {
                            'aliases': list(aliases),
                            'group': group_name,
                            'param': param_name
                        }
                        logger.debug(f"[Metadata] 记录参数元数据: {param_key} -> aliases={aliases}")
            
            for body_key in ("json", "params", "data"):
                if body_key in req_params and isinstance(req_params[body_key], dict):
                    body = req_params[body_key]
                    # 0) 先移除body中不应存在的path参数（防止生成阶段的错误数据）
                    for path_p in path_param_names:
                        if path_p in body:
                            logger.info(f"[DEBUG-CleanBody] 从body中移除path参数: {path_p}")
                            del body[path_p]
                    # 1) 覆盖已有字段：使用分组池查找
                    for k in list(body.keys()):
                        _record_param_metadata(k)  # 记录元数据
                        v_pool = _get_value_from_grouped_pool(pool, [k], target_group=group_name)
                        if v_pool is not None and not (isinstance(v_pool, str) and v_pool == ""):
                                body[k] = v_pool
                        else:
                            v_alias = _get_alias_value(pool, k, group_name, param_name, pool_metadata=pool_metadata)
                            if v_alias is not None and not (isinstance(v_alias, str) and v_alias == ""):
                                body[k] = v_alias
                    # 2) 若值为空则填入池值
                    for k, v in list(body.items()):
                        if (v is None) or (isinstance(v, str) and v == ""):
                            _record_param_metadata(k)  # 记录元数据
                            v_pool = _get_value_from_grouped_pool(pool, [k], target_group=group_name)
                            if v_pool is not None and not (isinstance(v_pool, str) and v_pool == ""):
                                    body[k] = v_pool
                            else:
                                v_alias = _get_alias_value(pool, k, group_name, param_name, pool_metadata=pool_metadata)
                                if v_alias is not None and not (isinstance(v_alias, str) and v_alias == ""):
                                    body[k] = v_alias
                    req_params[body_key] = body
            # 路径占位符替换
            if "url" in req_params and isinstance(req_params["url"], str):
                import re
                logger.info(f"[DEBUG-PathRepl] URL替换前: {req_params['url']}, pool_is_grouped={_is_grouped_pool(pool)}, group={group_name}, param={param_name}")
                def repl(m):
                    name = m.group(1)
                    _record_param_metadata(name)  # 记录路径参数元数据
                    logger.info(f"[DEBUG-PathRepl] 尝试替换占位符: {name}")
                    # 使用分组池查找
                    v = _get_value_from_grouped_pool(pool, [name], target_group=group_name)
                    if (v is None) or (isinstance(v, str) and v == ""):
                        logger.info(f"[DEBUG-PathRepl] 直接查找{name}失败，尝试别名")
                        v_alias = _get_alias_value(pool, name, group_name, param_name, pool_metadata=pool_metadata)
                        if v_alias is not None and is_valid_resource_id(v_alias):
                            logger.info(f"[DEBUG-PathRepl] 别名查找成功: {name} -> {v_alias}")
                            return str(v_alias)
                        # 新增：智能后缀匹配（order_id -> id, video_id -> id等）
                        if "_" in name:
                            suffix = name.split("_")[-1]  # 提取最后一段
                            suffix_val = _get_value_from_grouped_pool(pool, [suffix], target_group=group_name)
                            if suffix_val not in (None, "") and is_valid_resource_id(suffix_val):
                                logger.info(f"[DEBUG-PathRepl] 后缀匹配成功: {name} -> suffix={suffix}, value={suffix_val}")
                                return str(suffix_val)
                        logger.info(f"[DEBUG-PathRepl] 所有方法都失败，保留占位符{name}")
                        return m.group(0)
                    # 验证值是否为合法的资源ID
                    if is_valid_resource_id(v):
                        logger.info(f"[DEBUG-PathRepl] 直接查找成功: {name} -> {v}")
                    return str(v)
                    logger.info(f"[DEBUG-PathRepl] 值{v}未通过资源ID验证，保留占位符{name}")
                    return m.group(0)
                req_params["url"] = re.sub(r"\{([A-Za-z0-9_]+)\}", repl, req_params["url"])
                logger.info(f"[DEBUG-PathRepl] URL替换后: {req_params['url']}")
            return req_params

        def _prepare_pool_for_testcase(source_pool, source_metadata, param_aliases, target_group):
            """
            为 TestCase 准备池：从分组池中提取目标组的值，确保所有别名都已映射
            :param source_pool: 原始池（分组结构：{group: {param: value}}）
            :param source_metadata: 池的元数据（当前版本中已不需要，保留兼容性）
            :param param_aliases: 参数的别名列表（如 ['video_id', 'id']）
            :param target_group: 目标功能组
            :return: 准备好的扁平池（已映射别名，适用于 TestCase）
            """
            # 添加详细的调试日志
            logger.info(f"[DEBUG-PREP] ========== 开始准备池 ==========")
            logger.info(f"[DEBUG-PREP] 参数别名: {param_aliases}")
            logger.info(f"[DEBUG-PREP] 目标组: {target_group}")
            logger.info(f"[DEBUG-PREP] 输入池类型: {'分组池' if _is_grouped_pool(source_pool) else '扁平池'}")
            
            if _is_grouped_pool(source_pool):
                logger.info(f"[DEBUG-PREP] 分组池的组: {list(source_pool.keys())}")
                if target_group and target_group in source_pool:
                    logger.info(f"[DEBUG-PREP] 目标组 {target_group} 的内容: {source_pool[target_group]}")
                if "identity/api/v2/user/videos" in source_pool:
                    logger.info(f"[DEBUG-PREP] videos 组的内容: {source_pool['identity/api/v2/user/videos']}")
            
            prepared = {}
            
            # 如果是分组池，直接使用分组查找逻辑
            if _is_grouped_pool(source_pool):
                logger.info(f"[DEBUG-PreparePool] 使用分组池，target_group={target_group}")
                
                # 首先尝试使用配置映射查找主要的别名
                for alias in param_aliases:
                    if alias not in prepared or prepared.get(alias) in (None, ""):
                        # 先尝试配置映射
                        config_val = _get_value_based_on_config(source_pool, alias, target_group)
                        if config_val is not None and config_val != "" and config_val != 0:
                            prepared[alias] = config_val
                            logger.info(f"[DEBUG-PREP] ✓ 配置映射找到: {alias}={config_val}")
                            continue
                        
                        # 如果配置映射没找到，使用分组池查找
                        val = _get_value_from_grouped_pool(source_pool, [alias], target_group=target_group)
                        if val is not None and val != "":
                            prepared[alias] = val
                            logger.info(f"[DEBUG-PREP] ✓ 分组池找到: {alias}={val}")
                
                # 同时复制目标组的所有其他参数（补充 body 参数等）
                if target_group and target_group in source_pool:
                    for k, v in source_pool[target_group].items():
                        if k not in prepared and v not in (None, ""):
                            prepared[k] = v
                
                # 前缀匹配组的参数也考虑
                for g in source_pool:
                    if g == "_global" or g is None:
                        continue
                    if target_group and isinstance(g, str) and (target_group.startswith(g) or g.startswith(target_group)):
                        for k, v in source_pool[g].items():
                            if k not in prepared and v not in (None, ""):
                                prepared[k] = v
                
                # 全局参数作为最后补充
                if "_global" in source_pool:
                    for k, v in source_pool["_global"].items():
                        if k not in prepared and v not in (None, ""):
                            prepared[k] = v
            else:
                # 旧的扁平池结构，保持原有逻辑
                logger.info(f"[DEBUG-PreparePool] 使用扁平池（兼容模式）")
                for k, v in source_pool.items():
                    prepared[k] = v
                
                # 别名映射
                for target_alias in param_aliases:
                    if target_alias not in prepared or prepared.get(target_alias) in (None, ""):
                        for source_alias in param_aliases:
                            if source_alias == target_alias:
                                continue
                            if source_alias in source_pool and source_pool[source_alias] not in (None, ""):
                                prepared[target_alias] = source_pool[source_alias]
                                logger.info(f"[DEBUG-PreparePool] 映射别名: {target_alias} <- {source_alias}={source_pool[source_alias]}")
                                break
            
            logger.info(f"[DEBUG-PREP] ========== 准备池完成 ==========")
            logger.info(f"[DEBUG-PREP] 输出池的键: {list(prepared.keys())}")
            if "video_id" in param_aliases:
                logger.info(f"[DEBUG-PREP] video_id 的值: {prepared.get('video_id', 'NOT_FOUND')}")
                logger.info(f"[DEBUG-PREP] id 的值: {prepared.get('id', 'NOT_FOUND')}")
            logger.info(f"[DEBUG-PREP] 完整输出池: {prepared}")
            logger.info(f"[DEBUG-PREP] ==========================================")
            return prepared

        def _fill_params_from_c_strict(req_params, pool_c):
            """
            严格参数级：只用 C 池"补齐空值"，不覆盖非空字段，不新增字段。
            被测参数的改写由 apply_test_case_to_req 完成；这里仅负责让请求尽量可执行。
            """
            if not isinstance(req_params, dict) or not isinstance(pool_c, dict):
                return req_params
            for body_key in ("json", "params", "data"):
                body = req_params.get(body_key)
                if not isinstance(body, dict):
                    continue
                for k, v in list(body.items()):
                    if (v is None) or (isinstance(v, str) and v == ""):
                        pv = pool_c.get(k)
                        if pv not in (None, ""):
                            body[k] = pv
                req_params[body_key] = body
            # 路径占位符：仅当 C 中存在值时替换
            if isinstance(req_params.get("url"), str) and req_params.get("url"):
                import re
                def repl(m):
                    name = m.group(1)
                    v = pool_c.get(name)
                    if v not in (None, "") and is_valid_resource_id(v):
                        return str(v)
                    # 新增：智能后缀匹配（order_id -> id, video_id -> id等）
                    if "_" in name:
                        suffix = name.split("_")[-1]  # 提取最后一段
                        suffix_val = pool_c.get(suffix)
                        if suffix_val not in (None, "") and is_valid_resource_id(suffix_val):
                            return str(suffix_val)
                    return m.group(0)
                req_params["url"] = re.sub(r"\{([A-Za-z0-9_]+)\}", repl, req_params["url"])
            return req_params

        def _has_unresolved_placeholders(url_s: str) -> List[str]:
            try:
                import re
                return re.findall(r"\{([A-Za-z0-9_]+)\}", url_s or "")
            except Exception:
                return []

        def _resolve_test_case_value(test_case, pools):
            """用于严格模式：若用例需要从 A/B/C 取被测参数值但未取到，则标为不可执行。"""
            if not test_case:
                return None
            try:
                extra = test_case.extra_params if isinstance(getattr(test_case, "extra_params", None), dict) else {}
                if extra.get("remove"):
                    return "__remove__"
                explicit = extra.get("explicit_value")
                if explicit not in (None, ""):
                    return explicit
                src = (getattr(test_case, "value_source", "") or "").upper()
                aliases = list(getattr(test_case, "aliases", None) or [getattr(test_case, "param_name", "")])
                if src in ("A", "B", "C", "E", "F"):
                    pool = (pools or {}).get(src, {}) if isinstance(pools, dict) else {}
                    if isinstance(pool, dict):
                        for a in aliases:
                            v = pool.get(a)
                            if v not in (None, ""):
                                return v
            except Exception:
                return None
            return None

        # 新增：统一提取步骤对象，保证结构完整
        def _extract_step_obj(api_step):
            # 支持包裹结构：{"METHOD：/route": {实际步骤对象}}
            step = api_step if isinstance(api_step, dict) else {}
            title_key = None
            if isinstance(step, dict) and len(step) == 1:
                k = next(iter(step.keys()))
                v = step[k]
                if isinstance(v, dict):
                    title_key = k
                    step = v
            # 解析标题中的 请求方式 与 路由
            title_method = ""
            title_route = ""
            if isinstance(title_key, str) and "：" in title_key:
                try:
                    title_method, title_route = title_key.split("：", 1)
                except Exception:
                    title_method, title_route = "", ""
            # 读取请求参数块
            req_params_block = step.get("request_params", {})
            if isinstance(req_params_block, dict):
                params = req_params_block.get("parameters", req_params_block)
            else:
                params = {}
            if not isinstance(params, dict):
                params = {}
            # 规范化 headers 与主体
            headers = params.get("headers", {})
            if not isinstance(headers, dict):
                headers = {}
            params["headers"] = headers
            for key in ("json", "params", "data", "files"):
                v = params.get(key, {} if key != "files" else {})
                if key == "files":
                    if v is None or not isinstance(v, dict):
                        v = {}
                else:
                    if not isinstance(v, dict):
                        v = {}
                params[key] = v
            # 路由与方法优先级：显式字段 > 标题解析 > 参数内
            route = step.get("route") or title_route or params.get("url") or ""
            # method优先级：请求方式字段 > 标题解析 > 参数内（请求方式是权威来源）
            method = step.get("method") or title_method or params.get("method") or step.get("method") or ""
            # 写回 URL 与 method
            if "url" not in params or (isinstance(params.get("url"), str) and params.get("url", "") == ""):
                params["url"] = route
            # 始终用正确的method覆盖（请求方式字段是权威来源，避免原始包中的错误method）
            if method:
                params["method"] = method
            
            # 新增：检查是否有文件参数需要默认值（从 api_doc_data 中获取接口定义）
            try:
                if route and method and (not params.get("files") or params.get("files") == {}):
                    # 从 self.true_params 查找接口定义
                    api_keys_to_try = [
                        f"{method} {route}",
                        f"{method}：{route}",
                        f"{method}:{route}",
                        route
                    ]
                    has_file_param = False
                    for group_name, group_apis in self.true_params.items():
                        if not isinstance(group_apis, dict):
                            continue
                        for api_key_try in api_keys_to_try:
                            api_spec = group_apis.get(api_key_try)
                            if isinstance(api_spec, dict):
                                req_params = api_spec.get("request_parameters", {})
                                if isinstance(req_params, dict):
                                    for param_name, param_spec in req_params.items():
                                        if isinstance(param_spec, dict) and param_spec.get("type") == "file":
                                            has_file_param = True
                                            break
                                if has_file_param:
                                    break
                        if has_file_param:
                            break
                    
                    # 如果定义中有 formdata 类型的 file 参数，添加默认测试文件
                    if has_file_param:
                        import io
                        test_content = b"test file content for vulnerability scanning"
                        params["files"] = {
                            "file": ("test.txt", io.BytesIO(test_content), "text/plain")
                        }
            except Exception:
                pass
            
            return {
                "route": route,
                "method": method,
                "type": step.get("type", ""),
                "request_params": {"type": "request", "parameters": params}
            }

        # 新增：修正文件上传与 Content-Type 处理
        def _fix_files(req):
            """
            修正文件上传参数，确保格式正确
            - 移除显式的 Content-Type，让 requests 自动处理
            - 反序列化从 JSON 读取的 files 参数
            """
            files = req.get("files")
            if not isinstance(files, dict) or not files:
                return req
            
            # 当存在 files 参数时，移除所有 Content-Type，让 requests 自动设置
            headers = req.get("headers", {})
            headers_to_remove = []
            for hk in headers.keys():
                if hk.lower() == "content-type":
                    headers_to_remove.append(hk)
            for hk in headers_to_remove:
                headers.pop(hk)
                req["headers"] = headers
            
            # 检查是否需要反序列化（_serialize_request_params 标记）
            needs_deserialization = any(
                isinstance(v, dict) and v.get("_serialized")
                for v in files.values()
            )
            
            if needs_deserialization:
                # 使用 file_utils 反序列化
                try:
                    deserialized_files = deserialize_file_params(files)
                    # 检查是否成功反序列化（所有值都是 tuple）
                    if all(isinstance(v, tuple) for v in deserialized_files.values()):
                        req["files"] = deserialized_files
                        return req
                    else:
                        logger.warning("反序列化文件参数未完全成功，尝试降级处理")
                except Exception as e:
                    logger.warning(f"反序列化文件参数失败: {e}，尝试降级处理")
            
            # 兼容性处理：处理旧格式或其他格式
            new_files = {}
            import base64
            import io
            
            for k, v in files.items():
                # 情况1：已经是正确的 tuple 格式
                if isinstance(v, tuple) and len(v) >= 2:
                    new_files[k] = v
                    continue
                
                # 情况2：list 格式 (从 _make_json_serializable 序列化后的格式)
                if isinstance(v, list) and len(v) >= 2:
                    filename = v[0] if len(v) > 0 else "file"
                    content = v[1] if len(v) > 1 else ""
                    content_type = v[2] if len(v) > 2 else "application/octet-stream"
                    
                    # 尝试从 base64 解码
                    if isinstance(content, str):
                        try:
                            decoded = base64.b64decode(content)
                            file_obj = io.BytesIO(decoded)
                            new_files[k] = (filename, file_obj, content_type)
                            continue
                        except Exception:
                            file_obj = io.BytesIO(content.encode())
                            new_files[k] = (filename, file_obj, content_type)
                        continue
                
                # 情况3：dict 格式
                if isinstance(v, dict):
                    filename = v.get("filename", "file")
                    content = v.get("content")
                    content_type = v.get("content_type", "application/octet-stream")
                    
                    if isinstance(content, str):
                        try:
                            decoded = base64.b64decode(content)
                            new_files[k] = (filename, io.BytesIO(decoded), content_type)
                        except Exception:
                            new_files[k] = (filename, io.BytesIO(content.encode()), content_type)
                    elif isinstance(content, bytes):
                        new_files[k] = (filename, io.BytesIO(content), content_type)
                    else:
                        new_files[k] = (filename, io.BytesIO(b""), content_type)
                    continue
                
                # 情况4：bytes 或 str
                if isinstance(v, bytes):
                    new_files[k] = ("file", io.BytesIO(v), "application/octet-stream")
                elif isinstance(v, str):
                    try:
                        decoded = base64.b64decode(v)
                        new_files[k] = ("file", io.BytesIO(decoded), "application/octet-stream")
                    except Exception:
                        new_files[k] = ("file", io.BytesIO(v.encode()), "text/plain")
                else:
                    # 默认空文件
                    new_files[k] = ("file", io.BytesIO(b""), "application/octet-stream")
            
            req["files"] = new_files
            return req

        def _execute_api_step(api_step, base_scheme, base_netloc, auth_info, pool_values, pool_priority, session, group_name=None, param_name=None, test_case: Optional["HorizontalVuln.TestCase"]=None, pools: Optional[Dict[str, Dict[str, Any]]]=None, pool_metadata: Optional[Dict[str, Dict[str, Any]]]=None):
            # def llm_check_response_results(resp_json):
            #     """
            #     如何确定执行结果出错？
            #     """

            #     pass
            step_obj = _extract_step_obj(api_step)
            try:
                # 支持多TestCase串行应用（用于容器/资源关系一致性测试）
                if isinstance(test_case, (list, tuple)):
                    for tc in test_case:
                        step_obj = self.apply_test_case_to_req(step_obj, tc, pools)
                else:
                    step_obj = self.apply_test_case_to_req(step_obj, test_case, pools)
            except Exception:
                pass
            req = copy.deepcopy(step_obj.get("request_params", {}).get("parameters", {}))
            route = step_obj.get("route")
            req_url = req.get("url")
            # 调整策略：若路由含占位符且共享池缺值，则保留原始 URL，不再回退为占位符
            # 场景：params.url 已是具体值（如 /api/user/test），而 route 为占位符（/api/user/{username}）
            import re
            route_placeholders = re.findall(r"\{([A-Za-z0-9_]+)\}", route or "")
            def _can_satisfy(name):
                v = pool_values.get(name)
                if v not in (None, ""):
                    return True
                v_alias = _get_alias_value(pool_values, name, group_name, param_name, pool_metadata=pool_metadata)
                return v_alias is not None
            missing_keys = [k for k in route_placeholders if not _can_satisfy(k)]
            
            # 【关键修复】如果 test_case 存在，检查 req_url 是否已被 apply_test_case_to_req 正确替换
            # 如果 req_url 不再包含占位符，说明 apply_test_case_to_req 已用正确池的值替换，应保留该 URL
            if isinstance(route, str) and route:
                if test_case is not None and isinstance(req_url, str) and req_url:
                    # 检查 req_url 是否仍有未解析的占位符
                    req_url_placeholders = re.findall(r"\{([A-Za-z0-9_]+)\}", req_url)
                    if not req_url_placeholders:
                        # req_url 已被 apply_test_case_to_req 正确替换，保留它
                        req["url"] = _replace_domain(req_url, base_scheme, base_netloc, route=None)
                        logger.info(f"[DEBUG-URLFix] 保留 apply_test_case_to_req 替换后的URL: {req['url']}")
                    else:
                        # req_url 仍有占位符，回退到原逻辑
                        if route_placeholders and missing_keys:
                            req["url"] = _replace_domain(req_url, base_scheme, base_netloc, route=None)
                        else:
                            req["url"] = _replace_domain(None, base_scheme, base_netloc, route=route)
                elif route_placeholders and missing_keys and isinstance(req_url, str) and req_url:
                    # 保留原始 URL（含具体值），避免被替换回占位符
                    req["url"] = _replace_domain(req_url, base_scheme, base_netloc, route=None)
                else:
                    # 无占位符或共享池可满足占位符，使用 route 以便统一域名
                    req["url"] = _replace_domain(None, base_scheme, base_netloc, route=route)
            else:
                # 无 route 时，回退到原始 URL
                req["url"] = _replace_domain(req_url, base_scheme, base_netloc, route=None)
            # 始终用step_obj的请求方式覆盖（确保method与API定义一致）
            if step_obj.get("method"):
                req["method"] = step_obj.get("method")
            # 统一设置 headers，保留/规范化 Content-Type
            req["headers"] = _merge_headers(req.get("headers", {}), auth_info)
            # 严格参数级模式：仅用 C 池补齐空值；旧模式：允许共享池覆盖（用于构建上下文）
            if test_case is not None:
                # 若用例要求从 A/B/E/F 取被测参数，但未取到值，则标为不可执行（避免把 None 写进请求导致污染）
                try:
                    src = (getattr(test_case, "value_source", "") or "").upper()
                except Exception:
                    src = ""
                need_value = src in ("A", "B", "E", "F")
                if need_value:
                    tv = _resolve_test_case_value(test_case, pools)
                    if tv in (None, ""):
                        api_key = f"{step_obj.get('method')}:{step_obj.get('route')}"
                        return {
                            "route": step_obj.get("route"),
                            "method": step_obj.get("method"),
                            "type": step_obj.get("type"),
                            "request_params": {"type": "request", "parameters": req},
                            "response_params": {},
                            "execution_status": {
                                "api_key": api_key,
                                "status": "unexecutable",
                                "reason": f"target_value_missing_from_pool_{src}",
                                "request_url": req.get("url", ""),
                                "request_data": req.get("json") or req.get("params") or req.get("data") or {}
                            }
                        }
                req = _fill_params_from_c_strict(req, pool_values)
                # 若 URL 仍存在未替换占位符，标为不可执行
                missing = _has_unresolved_placeholders(req.get("url", ""))
                if missing:
                    api_key = f"{step_obj.get('method')}:{step_obj.get('route')}"
                    return {
                        "route": step_obj.get("route"),
                        "method": step_obj.get("method"),
                        "type": step_obj.get("type"),
                        "request_params": {"type": "request", "parameters": req},
                        "response_params": {},
                        "execution_status": {
                            "api_key": api_key,
                            "status": "unexecutable",
                            "reason": f"missing_path_params:{','.join(missing)}",
                            "request_url": req.get("url", ""),
                            "request_data": req.get("json") or req.get("params") or req.get("data") or {}
                        }
                    }
            else:
                # 覆盖/填充 body 与查询参数，应用共享池（用于 data_account 链条跑通并构建池）
                req = _fill_params_from_pool_legacy(req, pool_values, step_obj, group_name, param_name, pool_metadata=pool_metadata)
            # 修正文件上传与 Content-Type 边界处理
            _fix_files(req)

            try:
                timeout = req.pop('timeout', REQUEST_TIMEOUT)
                # 兼容 JSON 反序列化后的超时配置（list -> tuple）
                if isinstance(timeout, list) and len(timeout) == 2:
                    timeout = (timeout[0], timeout[1])
                response = session.request(**req, timeout=timeout)
                try:
                    resp_json = response.json()
                except Exception:
                    resp_json = {"text": response.text}
                api_key = f"{step_obj.get('method')}:{step_obj.get('route')}"
                def _path_params_for_req():
                    try:
                        from urllib.parse import urlsplit
                        route = step_obj.get("route", "")
                        url_s = req.get("url", "")
                        if not (isinstance(route, str) and route and isinstance(url_s, str) and url_s):
                            return {}
                        url_path = urlsplit(url_s).path or url_s
                        route_parts = [p for p in route.split('/') if p != '']
                        url_parts = [p for p in url_path.split('/') if p != '']
                        out = {}
                        if len(url_parts) >= len(route_parts):
                            for idx, rseg in enumerate(route_parts):
                                if rseg.startswith('{') and rseg.endswith('}') and idx < len(url_parts):
                                    name = rseg[1:-1]
                                    out[name] = url_parts[idx]
                        return out
                    except Exception:
                        return {}
                result = {
                    "route": step_obj.get("route"),
                    "method": step_obj.get("method"),
                    "type": step_obj.get("type"),
                    "request_params": {"type": "request", "parameters": req},
                    "response_params": {"type": "response", "parameters": resp_json if isinstance(resp_json, dict) else {"response": resp_json}},
                    "execution_status": {
                        "api_key": api_key,
                        "status": "success" if response.status_code < 400 else "error",
                        "status_code": response.status_code,
                        "request_url": req.get("url", ""),
                        "request_data": req.get("json") or req.get("params") or req.get("data") or _path_params_for_req()
                    }
                }
                if isinstance(resp_json, (dict, list)):
                    _update_pool_with_response(pool_values, pool_priority, resp_json, step_obj)
                return result
            except Exception as e:
                api_key = f"{step_obj.get('method')}:{step_obj.get('route')}"
                return {
                    "route": step_obj.get("route"),
                    "method": step_obj.get("method"),
                    "type": step_obj.get("type"),
                    "request_params": {"type": "request", "parameters": req},
                    "response_params": {},
                    "execution_status": {
                        "api_key": api_key,
                        "status": "error",
                        "reason": str(e),
                        "request_url": req.get("url", ""),
                        "request_data": req.get("json") or req.get("params") or req.get("data") or {}
                    }
                }

        def _iter_chain_steps_nested(chain_dict):
            # 生成 (顶层键, 子键/索引 或 None, api_step) 的序列，保持嵌套结构
            steps = []
            top_keys = sorted([k for k in chain_dict.keys() if k.isdigit()], key=lambda x: int(x))
            for tk in top_keys:
                val = chain_dict[tk]
                if isinstance(val, dict) and all(k.isdigit() for k in val.keys()):
                    sub_keys = sorted(val.keys(), key=lambda x: int(x))
                    for sk in sub_keys:
                        steps.append((tk, sk, val[sk]))
                elif isinstance(val, list):
                    for idx, item in enumerate(val):
                        steps.append((tk, str(idx), item))
                else:
                    steps.append((tk, None, val))
            return steps, top_keys

        def _record_nested(nested_results, top_key, sub_key, exec_result):
            if sub_key is None:
                nested_results[top_key] = exec_result
            else:
                if top_key not in nested_results or not isinstance(nested_results[top_key], dict):
                    nested_results[top_key] = {}
                nested_results[top_key][sub_key] = exec_result

        def _get_last_step_obj_from_results(account_results):
            if not isinstance(account_results, dict):
                return None
            step_numbers = [int(step) for step in account_results.keys() if str(step).isdigit()]
            if not step_numbers:
                return None
            last_step_num = str(max(step_numbers))
            last_step = account_results.get(last_step_num)
            if isinstance(last_step, dict) and any(str(k).isdigit() for k in last_step.keys()):
                sub_nums = [int(k) for k in last_step.keys() if str(k).isdigit()]
                if sub_nums:
                    last_sub = str(max(sub_nums))
                    return last_step.get(last_sub)
            return last_step

        def _extract_param_values_from_step(step_obj, aliases):
            vals = {"path": None, "query": None, "body": None, "header": None}
            if not isinstance(step_obj, dict):
                return vals
            params = step_obj.get("request_params", {}).get("parameters", {})
            if isinstance(params, dict):
                # query/body/header
                q = params.get("params", {})
                b = params.get("json", {}) if isinstance(params.get("json"), dict) else params.get("data", {})
                h = params.get("headers", {})
                if isinstance(q, dict):
                    for a in aliases:
                        if a in q:
                            vals["query"] = q.get(a)
                            break
                if isinstance(b, dict):
                    for a in aliases:
                        if a in b:
                            vals["body"] = b.get(a)
                            break
                if isinstance(h, dict):
                    for a in aliases:
                        if a in h:
                            vals["header"] = h.get(a)
                            break
                # path
                url_s = params.get("url")
                route = step_obj.get("route", "")
                if isinstance(url_s, str) and isinstance(route, str) and route:
                    from urllib.parse import urlsplit
                    try:
                        url_path = urlsplit(url_s).path or url_s
                    except Exception:
                        url_path = url_s
                    route_parts = [p for p in route.split('/') if p != '']
                    url_parts = [p for p in url_path.split('/') if p != '']
                    if len(url_parts) >= len(route_parts):
                        for idx, rseg in enumerate(route_parts):
                            if rseg.startswith('{') and rseg.endswith('}'):
                                name = rseg[1:-1]
                                if name in aliases and idx < len(url_parts):
                                    vals["path"] = url_parts[idx]
                                    break
            return vals

        def _extract_param_values_by_aliases(step_obj, aliases):
            """
            返回按别名聚合的取值与位置：{alias: {"position": pos, "value": val}}
            优先级：path > query > body > header
            """
            by_alias = {}
            if not isinstance(step_obj, dict):
                return by_alias
            params = step_obj.get("request_params", {}).get("parameters", {})
            if not isinstance(params, dict):
                return by_alias
            q = params.get("params", {})
            b = params.get("json", {}) if isinstance(params.get("json"), dict) else params.get("data", {})
            h = params.get("headers", {})
            if isinstance(q, dict):
                for a in aliases:
                    if a in q and a not in by_alias:
                        by_alias[a] = {"position": "query", "value": q.get(a)}
            if isinstance(b, dict):
                for a in aliases:
                    if a in b and a not in by_alias:
                        by_alias[a] = {"position": "body", "value": b.get(a)}
            if isinstance(h, dict):
                for a in aliases:
                    if a in h and a not in by_alias:
                        by_alias[a] = {"position": "header", "value": h.get(a)}
            url_s = params.get("url")
            route = step_obj.get("route", "")
            if isinstance(url_s, str) and isinstance(route, str) and route:
                from urllib.parse import urlsplit
                try:
                    url_path = urlsplit(url_s).path or url_s
                except Exception:
                    url_path = url_s
                route_parts = [p for p in route.split('/') if p != '']
                url_parts = [p for p in url_path.split('/') if p != '']
                if len(url_parts) >= len(route_parts):
                    for idx, rseg in enumerate(route_parts):
                        if rseg.startswith('{') and rseg.endswith('}'):
                            name = rseg[1:-1]
                            if name in aliases and idx < len(url_parts):
                                by_alias[name] = {"position": "path", "value": url_parts[idx]}
            return by_alias

        def _build_param_sources_by_aliases(values_by_alias, sources_by_position):
            by_alias_sources = {}
            if not isinstance(values_by_alias, dict):
                return by_alias_sources
            for a, info in values_by_alias.items():
                pos = (info or {}).get("position")
                if pos in sources_by_position:
                    by_alias_sources[a] = sources_by_position.get(pos)
            return by_alias_sources

        def _find_param_source_in_nested(nested, aliases):
            """
            返回参数值来源路径信息：
            {api_key, method, route, step_key, sub_key, position, param_name, value}
            """
            if not isinstance(nested, dict):
                return None
            def _iter_nested():
                for tk in sorted([k for k in nested.keys() if str(k).isdigit()], key=lambda x: int(x)):
                    node = nested.get(tk)
                    if isinstance(node, dict) and any(str(k).isdigit() for k in node.keys()):
                        for sk in sorted([k for k in node.keys() if str(k).isdigit()], key=lambda x: int(x)):
                            yield tk, sk, node.get(sk)
                    else:
                        yield tk, None, node
            for tk, sk, res in _iter_nested():
                if not isinstance(res, dict):
                    continue
                req = (res.get("request_params", {}) or {}).get("parameters", {}) if isinstance(res.get("request_params"), dict) else {}
                route = res.get("route", "") or ""
                method = res.get("method", "") or ""
                api_key = f"{method}:{route}"
                # path
                try:
                    url_s = req.get("url") if isinstance(req, dict) else ""
                    if isinstance(url_s, str) and isinstance(route, str) and route:
                        from urllib.parse import urlsplit
                        url_path = urlsplit(url_s).path or url_s
                        route_parts = [p for p in route.split('/') if p != '']
                        url_parts = [p for p in url_path.split('/') if p != '']
                        if len(url_parts) >= len(route_parts):
                            for idx, rseg in enumerate(route_parts):
                                if rseg.startswith('{') and rseg.endswith('}'):
                                    name = rseg[1:-1]
                                    if name in aliases and idx < len(url_parts):
                                        return {"api_key": api_key, "method": method, "route": route, "step_key": tk, "sub_key": sk, "position": "path", "param_name": name, "value": url_parts[idx]}
                except Exception:
                    pass
                # query/body/header
                if isinstance(req, dict):
                    q = req.get("params", {})
                    if isinstance(q, dict):
                        for a in aliases:
                            if a in q:
                                return {"api_key": api_key, "method": method, "route": route, "step_key": tk, "sub_key": sk, "position": "query", "param_name": a, "value": q.get(a)}
                    b = req.get("json", {}) if isinstance(req.get("json"), dict) else req.get("data", {})
                    if isinstance(b, dict):
                        for a in aliases:
                            if a in b:
                                return {"api_key": api_key, "method": method, "route": route, "step_key": tk, "sub_key": sk, "position": "body", "param_name": a, "value": b.get(a)}
                    h = req.get("headers", {})
                    if isinstance(h, dict):
                        for a in aliases:
                            if a in h:
                                return {"api_key": api_key, "method": method, "route": route, "step_key": tk, "sub_key": sk, "position": "header", "param_name": a, "value": h.get(a)}
            return None

        def _ensure_pool_has_alias_value(pool_values, aliases, value, target_group=None):
            """确保池中包含别名对应的值（支持分组池）"""
            if value in (None, ""):
                return
            if _is_grouped_pool(pool_values):
                # 分组池：写入到指定组或全局
                group = target_group or "_global"
                if group not in pool_values:
                    pool_values[group] = {}
                for a in aliases:
                    if pool_values[group].get(a) in (None, ""):
                        pool_values[group][a] = value
            else:
                # 扁平池
                for a in aliases:
                    if pool_values.get(a) in (None, ""):
                        pool_values[a] = value

        def _build_param_sources(test_case, locations):
            locs = [l for l in (locations or []) if l in ("path", "query", "body", "header")]
            if not locs:
                locs = ["query"]
            target_pos = None
            non_target_source = "C"
            if isinstance(getattr(test_case, "extra_params", None), dict):
                target_pos = test_case.extra_params.get("target_position")
                non_target_source = test_case.extra_params.get("non_target_source", "C")
            sources = {}
            for pos in locs:
                if target_pos:
                    sources[pos] = test_case.value_source if pos == target_pos else non_target_source
                else:
                    sources[pos] = test_case.value_source
            return sources

        def _find_param_value_in_nested(nested, aliases):
            if not isinstance(nested, dict):
                return None
            def _flat(obj):
                flat = {}
                def walk(o):
                    if isinstance(o, dict):
                        for k, v in o.items():
                            if isinstance(v, (dict, list)):
                                walk(v)
                            else:
                                flat[k] = v
                    elif isinstance(o, list):
                        for it in o:
                            walk(it)
                walk(obj)
                return flat
            for _, v in nested.items():
                if isinstance(v, dict):
                    for _, res in v.items():
                        try:
                            req = (res or {}).get("request_params", {}).get("parameters", {})
                            if isinstance(req, dict):
                                for body_key in ("params", "json", "data"):
                                    body = req.get(body_key, {})
                                    if isinstance(body, dict):
                                        for a in aliases:
                                            if a in body:
                                                val = body.get(a)
                                                if val not in (None, ""):
                                                    return val
                                # 路径参数提取：根据 请求路由 的占位符与实际 URL 位置映射抽取值
                                url_s = req.get("url")
                                route = (res or {}).get("route", "")
                                if isinstance(url_s, str) and isinstance(route, str) and route:
                                    from urllib.parse import urlsplit
                                    try:
                                        url_path = urlsplit(url_s).path or url_s
                                    except Exception:
                                        url_path = url_s
                                    route_parts = [p for p in route.split('/') if p != '']
                                    url_parts = [p for p in url_path.split('/') if p != '']
                                    if len(url_parts) >= len(route_parts):
                                        for idx, rseg in enumerate(route_parts):
                                            if rseg.startswith('{') and rseg.endswith('}'):
                                                name = rseg[1:-1]
                                                if name in aliases and idx < len(url_parts):
                                                    val = url_parts[idx]
                                                    if val not in (None, ""):
                                                        return val
                            resp = (res or {}).get("response_params", {}).get("parameters", {})
                            if isinstance(resp, dict):
                                flat = _flat(resp)
                                for a in aliases:
                                    if a in flat:
                                        val = flat.get(a)
                                        if val not in (None, ""):
                                            return val
                        except Exception:
                            pass
            return None

        # 查找同功能组内，使用 param_name 的 query 类型接口（优先当前参数名的链条）
        def _api_step_uses_param(api_step, param_name):
            try:
                step_obj = _extract_step_obj(api_step)
                params = step_obj.get("request_params", {}).get("parameters", {})
                for body_key in ("params", "json", "data"):
                    body = params.get(body_key, {})
                    if isinstance(body, dict) and (param_name in body):
                        return True
                route = step_obj.get("route", "") or ""
                if isinstance(route, str) and (("{" + param_name + "}") in route or (param_name in route)):
                    return True
            except Exception:
                pass
            return False

        # 获取API路由所属的功能组
        def _get_api_group(api_route):
            """
            根据 API 路由获取其所属的功能组
            :param api_route: API路由，如 "POST /identity/api/v2/user/videos"
            :return: 功能组名称，如 "identity/api/v2/user/videos"，未找到返回 None
            """
            try:
                proc = self.normalized_params_process_data
                if isinstance(proc, list):
                    for item in proc:
                        if not isinstance(item, dict):
                            continue
                        group_name = item.get("group")
                        if not group_name:
                            continue
                        for d in item.get("data", []) or []:
                            route_names = d.get("route_name", []) or []
                            if not isinstance(route_names, list):
                                route_names = [route_names] if route_names else []
                            # 检查当前 API 是否在该组的路由列表中
                            for route in route_names:
                                if route == api_route:
                                    return group_name
            except Exception as e:
                logger.warning(f"[DEBUG-GetApiGroup] 获取API功能组失败: {e}")
            return None
        
        # 获取参数所属的功能组
        def _get_param_group(param_name, api_route=None):
            """
            根据参数名和API路由获取参数所属的功能组
            :param param_name: 参数名称
            :param api_route: API路由（可选），如果提供，优先从该路由所属组查找
            :return: 功能组名称列表（一个参数可能属于多个组）
            """
            groups = []
            try:
                # 如果提供了 API 路由，先尝试从该路由所属组查找
                if api_route:
                    api_group = _get_api_group(api_route)
                    if api_group:
                        groups.append(api_group)
                        return groups
                
                # 否则遍历所有组，查找包含该参数的组
                proc = self.normalized_params_process_data
                if isinstance(proc, list):
                    for item in proc:
                        if not isinstance(item, dict):
                            continue
                        group_name = item.get("group")
                        if not group_name:
                            continue
                        for d in item.get("data", []) or []:
                            params_name = d.get("parameters_name", {}) or {}
                            keep_pra = params_name.get("keep_pra")
                            repl_list = params_name.get("replace_para", []) or []
                            if not isinstance(repl_list, list):
                                repl_list = [repl_list] if repl_list else []
                            # 检查参数是否在该组中
                            if param_name == keep_pra or param_name in repl_list:
                                if group_name not in groups:
                                    groups.append(group_name)
            except Exception as e:
                logger.warning(f"[DEBUG-GetParamGroup] 获取参数功能组失败: {e}")
            return groups

        # 别名参数集合：基于 normalized_params_process_data 获取 group 内参数的等价键（keep_pra 与 replace_para）
        def _alias_params_for_group(group_name, param_name):
            aliases = [param_name]
            try:
                proc = self.normalized_params_process_data
                if isinstance(proc, list):
                    for item in proc:
                        if not isinstance(item, dict):
                            continue
                        if item.get("group") != group_name:
                            continue
                        for d in item.get("data", []) or []:
                            params_name = d.get("parameters_name", {}) or {}
                            keep_pra = params_name.get("keep_pra")
                            repl_list = params_name.get("replace_para", []) or []
                            if not isinstance(repl_list, list):
                                repl_list = [repl_list] if repl_list else []
                            # p 是替换项，则返回 [keep_pra, p, 其它替换项]
                            if param_name in repl_list and keep_pra:
                                aliases = [keep_pra, param_name] + [x for x in repl_list if x != param_name]
                                return aliases
                            # p 是 keep_pra，则返回 [keep_pra] + 替换项
                            if param_name == keep_pra and repl_list:
                                aliases = [param_name] + list(repl_list)
                                return aliases
            except Exception:
                pass
            return aliases

        def _build_evidence_query(last_step_obj, param_name, group_name, target_param_value, data_auth, last_req_data):
            """
            构建并执行evidence查询，精确定位test_account操作的资源
            
            策略：
            1. 从test_account的最后一步提取目标参数值（如video_id=38）
            2. 优先使用query接口，传入精确参数值
            3. 如果query失败或不支持该参数，回退到list query
            4. 从list query结果中筛选包含目标参数值的项（考虑参数别名）
            5. 返回evidence结果或空字典
            
            Args:
                last_step_obj: test_account的最后一步对象
                param_name: 当前参数名（如video_id）
                group_name: 功能组名
                target_param_value: 目标参数值（从test_account操作中提取）
                data_auth: data_account的认证信息
                last_req_data: 最后一步的请求数据
                
            Returns:
                evidence_nested字典
            """
            evidence_nested = {}
            if target_param_value is None:
                logger.info(f"[Evidence] 跳过evidence查询: target_param_value为空")
                return evidence_nested
            
            logger.info(f"[Evidence] 开始构建evidence查询: param={param_name}, value={target_param_value}, group={group_name}")
            
            # 获取参数别名（考虑replace_para和keep_pra映射）
            aliases = _alias_params_for_group(group_name, param_name)
            logger.info(f"[Evidence] 参数别名: {aliases}")
            
            # 1. 尝试使用query接口
            try:
                query_api_step = _find_query_step_in_group("resource_id", group_name, param_name)
                if query_api_step is not None:
                    logger.info(f"[Evidence] 找到query接口: {query_api_step.get('route')}")
                    query_pool_values = {}
                    query_pool_priority = {}
                    
                    # 复用最后一步的其他参数
                    if isinstance(last_req_data, dict):
                        for k, v in last_req_data.items():
                            if k not in aliases:  # 不包括被测参数本身
                                query_pool_values[k] = v
                    
                    # 设置目标参数值（所有别名）
                    for alias in aliases:
                        query_pool_values[alias] = target_param_value
                        query_pool_priority[alias] = 10  # 最高优先级
                    
                    logger.info(f"[Evidence] 执行query查询，参数池: {query_pool_values}")
                    res_query = _execute_api_step(
                        query_api_step, 
                        base_scheme, 
                        base_netloc, 
                        data_auth, 
                        query_pool_values, 
                        query_pool_priority, 
                        data_session, 
                        group_name, 
                        param_name, 
                        test_case=None, 
                        pools=None
                    )
                    
                    # 检查query是否成功
                    status_code = (res_query.get("execution_status", {}) or {}).get("status_code")
                    if status_code == 200:
                        logger.info(f"[Evidence] query查询成功，返回evidence")
                        evidence_nested = {"1": res_query}
                        return evidence_nested
                    else:
                        logger.info(f"[Evidence] query查询失败(status={status_code})，尝试list query")
            except Exception as e:
                logger.info(f"[Evidence] query查询异常: {e}，尝试list query")
            
            # 2. 回退到list query
            try:
                list_query_step = _find_list_query_step_in_group("resource_id", group_name)
                if list_query_step is not None:
                    logger.info(f"[Evidence] 找到list query接口: {list_query_step.get('route')}")
                    list_pool_values = {}
                    list_pool_priority = {}
                    
                    # 复用最后一步的其他参数（除了被测参数）
                    if isinstance(last_req_data, dict):
                        for k, v in last_req_data.items():
                            if k not in aliases:
                                list_pool_values[k] = v
                    
                    logger.info(f"[Evidence] 执行list query查询")
                    res_list = _execute_api_step(
                        list_query_step,
                        base_scheme,
                        base_netloc,
                        data_auth,
                        list_pool_values,
                        list_pool_priority,
                        data_session,
                        group_name,
                        param_name,
                        test_case=None,
                        pools=None
                    )
                    
                    # 从list query结果中筛选包含目标参数值的项
                    status_code = (res_list.get("execution_status", {}) or {}).get("status_code")
                    if status_code == 200:
                        resp_params = (res_list.get("response_params", {}) or {}).get("parameters", {})
                        filtered_item = _filter_list_query_result(resp_params, aliases, target_param_value)
                        
                        if filtered_item:
                            logger.info(f"[Evidence] list query过滤成功，找到匹配项")
                            # 修改响应参数为过滤后的单项
                            res_list_filtered = copy.deepcopy(res_list)
                            res_list_filtered["response_params"]["parameters"] = filtered_item
                            evidence_nested = {"1": res_list_filtered}
                            return evidence_nested
                        else:
                            logger.info(f"[Evidence] list query过滤失败，未找到匹配项")
                    else:
                        logger.info(f"[Evidence] list query查询失败(status={status_code})")
            except Exception as e:
                logger.info(f"[Evidence] list query查询异常: {e}")
            
            logger.info(f"[Evidence] 所有查询方法都失败，返回空evidence")
            return evidence_nested
        
        def _find_list_query_step_in_group(category, group_name):
            """查找功能组中的list query接口"""
            try:
                group_map = self.true_params.get(group_name, {}) if isinstance(self.true_params, dict) else {}
                if isinstance(group_map, dict) and group_map:
                    for key, spec in group_map.items():
                        if not isinstance(spec, dict):
                            continue
                        tval = (spec.get("type", "") or "").strip().lower()
                        if tval == "list query":
                            method, route = _parse_method_route(key)
                            return {
                                "type": "list query",
                                "method": method.upper(),
                                "route": route,
                                "request_params": {
                                    "type": "request",
                                    "parameters": {
                                        "method": method.upper(),
                                        "url": route,
                                        "headers": {},
                                        "json": {},
                                        "params": {},
                                        "data": {},
                                        "files": {}
                                    }
                                }
                            }
            except Exception as e:
                logger.error(f"[Evidence] 查找list query失败: {e}")
            return None
        
        def _filter_list_query_result(resp_params, aliases, target_value):
            """
            从list query的响应中筛选包含目标参数值的项
            
            Args:
                resp_params: 响应参数字典
                aliases: 参数别名列表（如['video_id', 'id']）
                target_value: 目标值（如38）
                
            Returns:
                匹配的项（dict）或None
            """
            try:
                # 常见的列表字段名
                list_keys = ["data", "orders", "items", "list", "results", "records", "videos", "users"]
                
                for list_key in list_keys:
                    if list_key in resp_params and isinstance(resp_params[list_key], list):
                        items = resp_params[list_key]
                        logger.info(f"[Evidence] 在{list_key}中找到{len(items)}个项，开始过滤")
                        
                        for item in items:
                            if not isinstance(item, dict):
                                continue
                            
                            # 检查item中是否有任何别名字段匹配目标值
                            for alias in aliases:
                                if alias in item:
                                    item_value = item[alias]
                                    # 值比对（考虑类型转换）
                                    if str(item_value) == str(target_value):
                                        logger.info(f"[Evidence] 找到匹配项: {alias}={item_value}")
                                        return item
                        
                        logger.info(f"[Evidence] {list_key}中没有找到匹配项")
                        
                # 如果响应本身就是列表
                if isinstance(resp_params, list):
                    logger.info(f"[Evidence] 响应本身是列表，包含{len(resp_params)}个项")
                    for item in resp_params:
                        if not isinstance(item, dict):
                            continue
                        for alias in aliases:
                            if alias in item and str(item[alias]) == str(target_value):
                                logger.info(f"[Evidence] 找到匹配项: {alias}={item[alias]}")
                                return item
                                
            except Exception as e:
                logger.error(f"[Evidence] 过滤list query结果失败: {e}", exc_info=True)
            
            return None
        
        def _parse_method_route(key):
            """解析 'METHOD /route' 格式的键"""
            if not isinstance(key, str):
                return "", ""
            s = key.strip().replace("：", " ").replace(":", " ")
            parts = s.split()
            if len(parts) >= 2:
                return parts[0].upper(), " ".join(parts[1:])
            return "", s

        def _find_query_step_in_group(category, group_name, param_name):
            """
            在功能组（self.true_params）中优先按参数替换规则查找 query 类型接口：
            - 若参数存在别名映射（normalized_params_process_data 中 keep_pra/replace_para），优先使用 keep_pra（如 addressId），否则回退到原参数名（如 id）。
            - 命中后将该接口构造为统一的步骤对象结构，便于后续 _execute_api_step 执行。
            - 若组内未找到合适的 query 接口，最后回退到原有依赖链查找逻辑（cross/group）。
            """
            # 1) 构建参数优先列表（别名优先）
            def _param_priority_list(group, p):
                prefers = [p]
                try:
                    proc = self.normalized_params_process_data
                    if isinstance(proc, list):
                        for item in proc:
                            if not isinstance(item, dict):
                                continue
                            if item.get("group") != group:
                                continue
                            for d in item.get("data", []) or []:
                                params_name = d.get("parameters_name", {}) or {}
                                keep_pra = params_name.get("keep_pra")
                                repl_list = params_name.get("replace_para", []) or []
                                if not isinstance(repl_list, list):
                                    repl_list = [repl_list] if repl_list else []
                                # p 是替换项，则优先 keep_pra
                                if p in repl_list and keep_pra:
                                    prefers = [keep_pra] + [p] + [x for x in repl_list if x != p]
                                    return prefers
                                # p 本身就是 keep_pra，则后续考虑替换项
                                if p == keep_pra and repl_list:
                                    prefers = [p] + list(repl_list)
                                    return prefers
                except Exception:
                    pass
                return prefers

            # 2) 组内查找 query 接口，按优先参数筛选
            def _parse_method_route(key):
                if not isinstance(key, str):
                    return "", ""
                s = key.strip().replace("：", " ").replace(":", " ")
                parts = s.split()
                if len(parts) >= 2:
                    return parts[0].upper(), " ".join(parts[1:])
                return "", s

            def _build_step_from_doc(method, route, api_spec):
                # 统一构造步骤对象，方便 _execute_api_step 使用
                m = (method or "").upper()
                r = route or ""
                t = (api_spec or {}).get("type", "")
                return {
                    "type": t,
                    "method": m,
                    "route": r,
                    "request_params": {
                        "type": "request",
                        "parameters": {
                            "method": m,
                            "url": r,
                            "headers": {},
                            "json": {},
                            "params": {},
                            "data": {},
                            "files": {}
                        }
                    }
                }

            try:
                group_map = self.true_params.get(group_name, {}) if isinstance(self.true_params, dict) else {}
                if isinstance(group_map, dict) and group_map:
                    prefers = _param_priority_list(group_name, param_name)
                    # 先筛选所有 query 类型接口
                    query_entries = []
                    for key, spec in group_map.items():
                        if not isinstance(spec, dict):
                            continue
                        tval = (spec.get("type", "") or "").strip().lower()
                        if tval != "query":
                            continue
                        query_entries.append((key, spec))
                    # 依次按优先参数检查是否在请求参数或路由中出现
                    for pref in prefers:
                        for key, spec in query_entries:
                            method, route = _parse_method_route(key)
                            req_params = spec.get("request_parameters", spec.get("request_para", {})) or {}
                            # 参数名直接在请求参数中
                            has_in_request = isinstance(req_params, dict) and (pref in req_params)
                            # 或者出现在路径占位符中
                            in_route = isinstance(route, str) and (("{" + pref + "}") in route or (pref in route))
                            if has_in_request or in_route:
                                return _build_step_from_doc(method, route, spec)
                    # 若没有任何带优先参数的 query，退一步：随便选择一个 query 接口（维持原语义）
                    if query_entries:
                        key, spec = query_entries[0]
                        method, route = _parse_method_route(key)
                        return _build_step_from_doc(method, route, spec)
            except Exception:
                pass

            # 3) 回退：沿用旧的链内查找逻辑（保证兼容）
            try:
                groups_map = resource_dict if category == "resource_id" else ou_dict
                param_map = groups_map.get(group_name, {}) if isinstance(groups_map, dict) else {}
                cats = _normalize_categories(param_map.get(param_name, {}))
                for chain_type in ["group", "cross"]:
                    chains = cats.get(chain_type, [])
                    if not isinstance(chains, list):
                        continue
                    for chain in chains:
                        if not isinstance(chain, dict):
                            continue
                        steps_nested, _ = _iter_chain_steps_nested(chain)
                        for tk, sk, api_step in steps_nested:
                            try:
                                step_obj = _extract_step_obj(api_step)
                                tval = (step_obj.get("type", "") or "").strip().lower()
                                if tval == "query" and _api_step_uses_param(api_step, param_name):
                                    return api_step
                            except Exception:
                                continue
            except Exception:
                pass
            return None

        base_scheme, base_netloc = _parse_base(url)
        results = {}
        data_auth = authority_account.get("data_account", {}).get("auth", "")
        test_auth = authority_account.get("test_account", {}).get("auth", "")
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        REQUEST_TIMEOUT = (5, 30)
        is_test_domain = isinstance(base_netloc, str) and ("test" in base_netloc)
        if is_test_domain:
            adapter = HTTPAdapter(pool_connections=50, pool_maxsize=50, max_retries=0)
        else:
            try:
                retry = Retry(
                    total=3,
                    connect=3,
                    read=3,
                    backoff_factor=0.5,
                    status_forcelist=[500, 502, 503, 504, 429],
                    allowed_methods=["GET", "HEAD", "OPTIONS"]
                )
            except TypeError:
                retry = Retry(
                    total=3,
                    connect=3,
                    read=3,
                    backoff_factor=0.5,
                    status_forcelist=[500, 502, 503, 504, 429],
                    method_whitelist=["GET", "HEAD", "OPTIONS"]
                )
            adapter = HTTPAdapter(pool_connections=100, pool_maxsize=100, max_retries=retry)
        data_session = requests.Session()
        test_session = requests.Session()
        data_session.trust_env = False
        test_session.trust_env = False
        data_session.mount("http://", adapter)
        data_session.mount("https://", adapter)
        test_session.mount("http://", adapter)
        test_session.mount("https://", adapter)

        # 计算总链条数以用于进度统计
        def _normalize_categories(categories):
            if not isinstance(categories, dict):
                chains = categories if isinstance(categories, list) else []
                return {"cross": chains, "group": []}
            return categories

        resource_dict = dependency_chain_request_packages.get("resource_id", {}) if isinstance(dependency_chain_request_packages, dict) else {}
        ou_dict = dependency_chain_request_packages.get("ou_id", {}) if isinstance(dependency_chain_request_packages, dict) else {}

        def _count_total_chains(res_dict, ou_dict):
            total = 0
            for _, param_dict in res_dict.items():
                for _, categories in param_dict.items():
                    cats = _normalize_categories(categories)
                    for chain_type in ["cross", "group"]:
                        chains = cats.get(chain_type, [])
                        if isinstance(chains, list):
                            total += sum(1 for c in chains if isinstance(c, dict))
            for _, param_dict in ou_dict.items():
                for _, categories in param_dict.items():
                    cats = _normalize_categories(categories)
                    for chain_type in ["cross", "group"]:
                        chains = cats.get(chain_type, [])
                        if isinstance(chains, list):
                            total += sum(1 for c in chains if isinstance(c, dict))
            return total

        import os
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
        progress_path = os.path.join(project_root, 'cache', self.project_name, 'horizontal_results', 'execution_progress.json')
        total_chains = _count_total_chains(resource_dict, ou_dict)
        progress = {"total": total_chains, "completed": 0, "by_group": {}}
        progress_lock = __import__("threading").Lock()
        # 进度条：记录开始时间
        start_time = __import__("time").time()

        def _format_duration(seconds):
            try:
                seconds = int(seconds)
            except Exception:
                seconds = 0
            minutes, seconds = divmod(seconds, 60)
            hours, minutes = divmod(minutes, 60)
            if hours:
                return f"{hours}h {minutes}m {seconds}s"
            if minutes:
                return f"{minutes}m {seconds}s"
            return f"{seconds}s"

        def _update_terminal_progress(completed, total):
            try:
                width = 40
                total = max(1, int(total))
                completed = max(0, min(int(completed), total))
                pct = int(completed * 100 / total)
                filled = int(width * completed / total)
                bar = ("=" * max(0, filled - 1)) + (">" if filled > 0 else "") + ("." * (width - filled))
                now = __import__("time").time()
                elapsed = now - start_time
                eta = ((elapsed / completed) * (total - completed)) if completed > 0 else 0
                msg = f"[{bar}] {pct}% {completed}/{total} | elapsed { _format_duration(elapsed) } | eta { _format_duration(eta) }"
                sys = __import__("sys")
                sys.stdout.write("\r" + msg)
                sys.stdout.flush()
                if completed == total:
                    sys.stdout.write("\n")
                    sys.stdout.flush()
            except Exception:
                pass

        def _increment_progress(group_name):
            with progress_lock:
                progress["completed"] += 1
                progress["by_group"][group_name] = progress["by_group"].get(group_name, 0) + 1
                # 实时更新终端进度条
                try:
                    _update_terminal_progress(progress["completed"], progress["total"])
                except Exception:
                    pass
                # 每完成10%或全部时更新一次进度文件
                threshold = max(1, progress["total"] // 10) if progress["total"] > 0 else 1
                if (progress["completed"] % threshold == 0) or (progress["completed"] == progress["total"]):
                    try:
                        self.jsontool.write_json(progress_path, progress)
                    except Exception:
                        pass
                    try:
                        pct = (progress["completed"] * 100 // progress["total"]) if progress["total"] else 100
                        logger.info(f"依赖链执行进度: {progress['completed']}/{progress['total']} ({pct}%)")
                    except Exception:
                        pass

        # 封装单条 resource_id 链的执行逻辑
        def _execute_chain_resource(chain, param_name, group_name, test_case: Optional["HorizontalVuln.TestCase"]=None):
            """
            执行 resource_id 类型的依赖链测试
            
            Pool 定义（对齐取值关系文档）：
            - Pool A: data_account (victim) 完整执行后的参数值
            - Pool B: data_account (victim) 执行到最后一步前的参数值
            - Pool C: test_account (attacker) 执行到最后一步前的参数值
            """
            # ========== Pool A 构建：data_account 完整执行 ==========
            pool_A_values = {}
            pool_A_priority = {}
            pool_A_metadata = {}
            control_group_nested = {}  # 对照组执行结果
            steps_nested, top_keys = _iter_chain_steps_nested(chain)
            for tk, sk, api_step in steps_nested:
                res = _execute_api_step(api_step, base_scheme, base_netloc, data_auth, pool_A_values, pool_A_priority, data_session, group_name, param_name, test_case=None, pools=None, pool_metadata=pool_A_metadata)
                _record_nested(control_group_nested, tk, sk, res)
                rp = (res.get("request_params", {}) or {}).get("parameters", {})
                _update_pool_with_request(pool_A_values, pool_A_priority, rp, 3, step_obj=res, pool_metadata=pool_A_metadata)
                rsp = (res.get("response_params", {}) or {}).get("parameters", {})
                _update_pool_with_response(pool_A_values, pool_A_priority, rsp, res, pool_metadata=pool_A_metadata)
            # 调试：记录Pool A的内容
            logger.info(f"[DEBUG-Pool-A] param={param_name}, group={group_name}, pool_A_keys={list(pool_A_values.keys())}")
            if param_name in pool_A_values:
                logger.info(f"[DEBUG-Pool-A] {param_name}={pool_A_values[param_name]}, metadata={pool_A_metadata.get(param_name)}")
            
            # ========== Pool B/C 构建：执行到最后一步前 ==========
            pool_B_values = {}  # data_account 执行到最后一步前
            pool_B_priority = {}
            pool_B_metadata = {}
            pool_C_values = {}  # test_account 执行到最后一步前
            pool_C_priority = {}
            pool_C_metadata = {}
            test_group_nested = {}  # 实验组执行结果
            last_tk = top_keys[-1] if top_keys else None
            # 预执行（排除最后一个顶层键）以构建 Pool B 和 Pool C
            for tk, sk, api_step in steps_nested:
                if last_tk and tk == last_tk:
                    continue
                # Pool B 构建：data_account 预执行（仅用于构建池，不记录到实验组结果）
                r1 = _execute_api_step(api_step, base_scheme, base_netloc, data_auth, pool_B_values, pool_B_priority, data_session, group_name, param_name, test_case=None, pools=None, pool_metadata=pool_B_metadata)
                rp1 = (r1.get("request_params", {}) or {}).get("parameters", {})
                _update_pool_with_request(pool_B_values, pool_B_priority, rp1, 3, step_obj=r1, pool_metadata=pool_B_metadata)
                rsp1 = (r1.get("response_params", {}) or {}).get("parameters", {})
                _update_pool_with_response(pool_B_values, pool_B_priority, rsp1, r1, pool_metadata=pool_B_metadata)
                # 调试：记录 Pool B 的内容
                if param_name in pool_B_values:
                    logger.info(f"[DEBUG-Pool-B] {param_name}={pool_B_values[param_name]}, metadata={pool_B_metadata.get(param_name)}")
                # Pool C 构建：test_account 预执行（记录到实验组结果，构建池）
                # 注意：预执行阶段不应用 test_case
                r2 = _execute_api_step(api_step, base_scheme, base_netloc, test_auth, pool_C_values, pool_C_priority, test_session, group_name, param_name, test_case=None, pools=None, pool_metadata=pool_C_metadata)
                _record_nested(test_group_nested, tk, sk, r2)
                rp2 = (r2.get("request_params", {}) or {}).get("parameters", {})
                _update_pool_with_request(pool_C_values, pool_C_priority, rp2, 3, step_obj=r2, pool_metadata=pool_C_metadata)
                rsp2 = (r2.get("response_params", {}) or {}).get("parameters", {})
                _update_pool_with_response(pool_C_values, pool_C_priority, rsp2, r2, pool_metadata=pool_C_metadata)
                # 调试：记录 Pool C 的内容
                if param_name in pool_C_values:
                    logger.info(f"[DEBUG-Pool-C] {param_name}={pool_C_values[param_name]}, metadata={pool_C_metadata.get(param_name)}")
            # ========== 实验组执行：最后一个顶层键 ==========
            # 上下文来自 Pool C，被测参数由 TestCase 从 Pool A/B/C 取值并改写
            evidence_nested: Dict[str, Any] = {}
            last_modify_found = False
            last_param_value = None
            last_req_data = {}
            if last_tk:
                aliases = _alias_params_for_group(group_name, param_name)
                
                # 添加调试日志：显示原始池的内容
                if param_name == "video_id":
                    logger.info(f"[DEBUG-POOL-BUILD] ========== 构建 TestCase 池 ==========")
                    logger.info(f"[DEBUG-POOL-BUILD] 参数: {param_name}, 组: {group_name}")
                    logger.info(f"[DEBUG-POOL-BUILD] 别名: {aliases}")
                    logger.info(f"[DEBUG-POOL-BUILD] Pool B 类型: {'分组' if _is_grouped_pool(pool_B_values) else '扁平'}")
                    if _is_grouped_pool(pool_B_values):
                        logger.info(f"[DEBUG-POOL-BUILD] Pool B 的组: {list(pool_B_values.keys())}")
                        if group_name in pool_B_values:
                            logger.info(f"[DEBUG-POOL-BUILD] 目标组 {group_name} 内容: {pool_B_values[group_name]}")
                
                # 准备各个池（确保别名映射和组过滤）
                prepared_pool_a = _prepare_pool_for_testcase(pool_A_values, pool_A_metadata, aliases, group_name)
                prepared_pool_b = _prepare_pool_for_testcase(pool_B_values, pool_B_metadata, aliases, group_name)
                prepared_pool_c = _prepare_pool_for_testcase(pool_C_values, pool_C_metadata, aliases, group_name)
                pools_map = {"A": prepared_pool_a, "B": prepared_pool_b, "C": prepared_pool_c}
                
                # 显示准备后的池
                if param_name == "video_id":
                    logger.info(f"[DEBUG-POOL-BUILD] Pool B 准备后: {prepared_pool_b}")
                    logger.info(f"[DEBUG-POOL-BUILD] ======================================")
                # 若 Pool B 缺少被测参数，按 Pool A 来源路径回填（保证来源一致）
                try:
                    if (test_case is not None) and (getattr(test_case, "value_source", "").upper() == "B"):
                        if _get_value_from_grouped_pool(pool_B_values, aliases, target_group=group_name) in (None, ""):
                            a_src = _find_param_source_in_nested(control_group_nested, aliases)
                            if isinstance(a_src, dict) and a_src.get("value") not in (None, ""):
                                _ensure_pool_has_alias_value(pool_B_values, aliases, a_src.get("value"), target_group=group_name)
                except Exception:
                    pass
                for tk, sk, api_step in steps_nested:
                    if tk != last_tk:
                        continue
                    res_final = _execute_api_step(api_step, base_scheme, base_netloc, test_auth, pool_C_values, pool_C_priority, test_session, group_name, param_name, test_case=test_case, pools=pools_map, pool_metadata=pool_C_metadata)
                    _record_nested(test_group_nested, tk, sk, res_final)
                    try:
                        req_data = (res_final.get("execution_status", {}) or {}).get("request_data", {})
                        if isinstance(req_data, dict):
                            last_req_data = req_data.copy()
                            for a in aliases:
                                if req_data.get(a) not in (None, ""):
                                    last_param_value = req_data.get(a)
                                    break
                        tval = (res_final.get("type", "") or "").strip().lower()
                        if tval in ("update", "delete"):
                            last_modify_found = True
                            if last_param_value in (None, ""):
                                for a in aliases:
                                    v = pool_C_values.get(a)
                                    if v not in (None, ""):
                                        last_param_value = v
                                        break
                    except Exception:
                        pass
            # evidence：若最后步骤为 update/delete，使用新的精确evidence查询逻辑
            if last_modify_found and last_param_value is not None:
                evidence_nested = _build_evidence_query(
                    last_step_obj=None,  # 可以不传，因为我们已经有last_param_value
                    param_name=param_name,
                    group_name=group_name,
                    target_param_value=last_param_value,
                    data_auth=data_auth,
                    last_req_data=last_req_data
                )
            # 返回结果：对照组(data_account)和实验组(test_account)的执行结果
            return {"data_account": control_group_nested, "test_account": test_group_nested, "evidence": evidence_nested}

        # 封装单条 ou_id 链的执行逻辑
        def _execute_chain_ou(chain, param_name, group_name, test_case: Optional["HorizontalVuln.TestCase"]=None):
            """
            执行 ou_id (容器资源参数) 类型的依赖链测试
            
            Pool 定义（对齐取值关系文档）：
            - Pool A: data_account (victim) 完整执行后的参数值
            - Pool B: data_account (victim) 执行第一个顶层后的参数值（用于ou场景）
            - Pool C: test_account (attacker) 执行第一个顶层后的参数值
            - Pool D: test_account (attacker) 容器B，执行到最后一步前的参数值（容器边界测试）
            - Pool E: test_account (attacker) 容器B，完整执行后的参数值（容器边界对照组）
            """
            # ========== Pool A 构建：data_account 完整执行 ==========
            pool_A_values = {}
            pool_A_priority = {}
            pool_A_metadata = {}
            control_group_nested = {}  # 对照组执行结果
            steps_nested, top_keys = _iter_chain_steps_nested(chain)
            for tk, sk, api_step in steps_nested:
                res = _execute_api_step(api_step, base_scheme, base_netloc, data_auth, pool_A_values, pool_A_priority, data_session, group_name, param_name, test_case=None, pools=None, pool_metadata=pool_A_metadata)
                _record_nested(control_group_nested, tk, sk, res)
            
            # ========== Pool B/C 构建（第一个顶层）+ 后续步骤执行 ==========
            pool_B_after_first = {}  # data_account 第一个顶层后的池
            pool_B_after_first_priority = {}
            pool_B_after_first_metadata = {}
            pool_C_after_first = {}  # test_account 第一个顶层后的池
            pool_C_after_first_priority = {}
            pool_C_after_first_metadata = {}
            # 用于记录后续执行的池
            pool_after_all_values = {}
            pool_after_all_priority = {}
            pool_after_all_metadata = {}
            test_group_nested = {}  # 实验组执行结果
            steps_nested, top_keys = _iter_chain_steps_nested(chain)
            first_tk = top_keys[0] if top_keys else None
            last_tk = top_keys[-1] if top_keys else None
            # 执行第一个顶层键构建 Pool B/C
            pool_B_values = {}  # data_account 第一个顶层的池
            pool_B_priority = {}
            pool_B_metadata = {}
            pool_C_values = {}  # test_account 第一个顶层的池
            pool_C_priority = {}
            pool_C_metadata = {}
            if first_tk:
                for tk, sk, api_step in steps_nested:
                    if tk == first_tk:
                        # Pool B 构建
                        r1 = _execute_api_step(api_step, base_scheme, base_netloc, data_auth, pool_B_values, pool_B_priority, data_session, group_name, param_name, test_case=None, pools=None, pool_metadata=pool_B_metadata)
                        rp1 = (r1.get("request_params", {}) or {}).get("parameters", {})
                        _update_pool_with_request(pool_B_values, pool_B_priority, rp1, 3, step_obj=r1, pool_metadata=pool_B_metadata)
                        rsp1 = (r1.get("response_params", {}) or {}).get("parameters", {})
                        _update_pool_with_response(pool_B_values, pool_B_priority, rsp1, r1, pool_metadata=pool_B_metadata)
                        # Pool C 构建，不应用 test_case
                        r2f = _execute_api_step(api_step, base_scheme, base_netloc, test_auth, pool_C_values, pool_C_priority, test_session, group_name, param_name, test_case=None, pools=None, pool_metadata=pool_C_metadata)
                        _record_nested(test_group_nested, tk, sk, r2f)
                        rp2f = (r2f.get("request_params", {}) or {}).get("parameters", {})
                        _update_pool_with_request(pool_C_values, pool_C_priority, rp2f, 3, step_obj=r2f, pool_metadata=pool_C_metadata)
                        rsp2f = (r2f.get("response_params", {}) or {}).get("parameters", {})
                        _update_pool_with_response(pool_C_values, pool_C_priority, rsp2f, r2f, pool_metadata=pool_C_metadata)
            # ========== 实验组执行：排除第一个顶层键后的步骤 ==========
            last_req_data = {}
            for tk, sk, api_step in steps_nested:
                if first_tk and tk == first_tk:
                    continue
                # data_account 预执行（构建用于 evidence 回退的池）
                r_data = _execute_api_step(api_step, base_scheme, base_netloc, data_auth, pool_B_after_first, pool_B_after_first_priority, data_session, group_name, param_name, test_case=None, pools=None, pool_metadata=pool_B_after_first_metadata)
                rp_data = (r_data.get("request_params", {}) or {}).get("parameters", {})
                _update_pool_with_request(pool_B_after_first, pool_B_after_first_priority, rp_data, 3, step_obj=r_data, pool_metadata=pool_B_after_first_metadata)
                rsp_data = (r_data.get("response_params", {}) or {}).get("parameters", {})
                _update_pool_with_response(pool_B_after_first, pool_B_after_first_priority, rsp_data, r_data, pool_metadata=pool_B_after_first_metadata)
                # test_account 执行（记录到实验组结果；被测参数由 test_case 改写，其余参数来自 Pool C）
                # 准备各个池（确保别名映射和组过滤）
                ou_aliases = _alias_params_for_group(group_name, param_name)
                prepared_pool_a_ou = _prepare_pool_for_testcase(pool_A_values, pool_A_metadata, ou_aliases, group_name)
                prepared_pool_b_ou = _prepare_pool_for_testcase(pool_B_values, pool_B_metadata, ou_aliases, group_name)
                prepared_pool_c_ou = _prepare_pool_for_testcase(pool_C_values, pool_C_metadata, ou_aliases, group_name)
                pools_map = {"A": prepared_pool_a_ou, "B": prepared_pool_b_ou, "C": prepared_pool_c_ou}
                res_test = _execute_api_step(api_step, base_scheme, base_netloc, test_auth, pool_C_values, pool_C_priority, test_session, group_name, param_name, test_case=test_case, pools=pools_map, pool_metadata=pool_C_metadata)
                _record_nested(test_group_nested, tk, sk, res_test)
                rp_test = (res_test.get("request_params", {}) or {}).get("parameters", {})
                _update_pool_with_request(pool_C_after_first, pool_C_after_first_priority, rp_test, 3, step_obj=res_test, pool_metadata=pool_C_after_first_metadata)
                rsp_test = (res_test.get("response_params", {}) or {}).get("parameters", {})
                _update_pool_with_response(pool_C_after_first, pool_C_after_first_priority, rsp_test, res_test, pool_metadata=pool_C_after_first_metadata)
                # 若为最后顶层，记录其请求参数用于后续 query 复用其余一致参数
                if last_tk and tk == last_tk:
                    try:
                        req_data = res_test.get("execution_status", {}).get("request_data", {})
                        if isinstance(req_data, dict):
                            last_req_data = req_data.copy()
                    except Exception:
                        pass
            # ========== Evidence 查询 ==========
            evidence_nested: Dict[str, Any] = {}
            # 被测参数值优先级：last_req_data → Pool C → Pool B → 嵌套提取
            last_param_value = None
            aliases = _alias_params_for_group(group_name, param_name)
            if isinstance(last_req_data, dict):
                for a in aliases:
                    if last_req_data.get(a) not in (None, ""):
                        last_param_value = last_req_data.get(a)
                        break
            if last_param_value in (None, ""):
                for a in aliases:
                    if pool_C_after_first.get(a) not in (None, ""):
                        last_param_value = pool_C_after_first.get(a)
                        break
            if last_param_value in (None, ""):
                for a in aliases:
                    if pool_B_after_first.get(a) not in (None, ""):
                        last_param_value = pool_B_after_first.get(a)
                        break
            if last_param_value in (None, ""):
                try:
                    _v = _find_param_value_in_nested(control_group_nested, aliases)
                    if _v not in (None, ""):
                        last_param_value = _v
                except Exception:
                    pass
            
            if last_param_value is not None:
                evidence_nested = _build_evidence_query(
                    last_step_obj=None,
                    param_name=param_name,
                    group_name=group_name,
                    target_param_value=last_param_value,
                    data_auth=data_auth,
                    last_req_data=last_req_data
                )
            # 返回结果：对照组(data_account)和实验组(test_account)的执行结果
            return {"data_account": control_group_nested, "test_account": test_group_nested, "evidence": evidence_nested}

        def _execute_chain_container_boundary(chain, param_name, group_name, test_case: Optional["HorizontalVuln.TestCase"]=None):
            """
            执行容器边界测试（同账号跨容器访问）
            
            Pool 定义（对齐取值关系文档）：
            - Pool C: test_account (attacker) 容器A，执行到最后一步前的参数值
            - Pool D: test_account (attacker) 容器B，执行到最后一步前的参数值
            - Pool E: test_account (attacker) 容器B，完整执行后的参数值（对照组）
            
            测试目的：测试系统是否验证资源与容器的所属关系
            - 实验组：test_account使用Pool C(容器A) + Pool D(容器B资源)执行攻击
            - 对照组：test_account完整执行容器B依赖链（产生Pool E）
            - 判定：实验组响应 ≈ 对照组响应 → 容器边界漏洞
            """
            steps_nested, top_keys = _iter_chain_steps_nested(chain)
            last_tk = top_keys[-1] if top_keys else None
            
            # ========== Pool C 构建：test_account 容器A 执行到最后一步前 ==========
            pool_C_values = {}
            pool_C_priority = {}
            pool_C_metadata = {}
            for tk, sk, api_step in steps_nested:
                if last_tk and tk == last_tk:
                    continue
                res = _execute_api_step(api_step, base_scheme, base_netloc, test_auth, pool_C_values, pool_C_priority, test_session, group_name, param_name, test_case=None, pools=None, pool_metadata=pool_C_metadata)
                rp = (res.get("request_params", {}) or {}).get("parameters", {})
                _update_pool_with_request(pool_C_values, pool_C_priority, rp, 3, step_obj=res, pool_metadata=pool_C_metadata)
                rsp = (res.get("response_params", {}) or {}).get("parameters", {})
                _update_pool_with_response(pool_C_values, pool_C_priority, rsp, res, pool_metadata=pool_C_metadata)
            logger.info(f"[DEBUG-Container-Boundary] Pool C (容器A) 构建完成: {list(pool_C_values.keys())}")
            
            # ========== Pool D 构建：test_account 容器B 执行到最后一步前 ==========
            # 注意：这里需要使用不同的容器B的依赖链
            # 目前简化处理：使用同一链条但创建不同的资源
            pool_D_values = {}
            pool_D_priority = {}
            pool_D_metadata = {}
            for tk, sk, api_step in steps_nested:
                if last_tk and tk == last_tk:
                    continue
                res = _execute_api_step(api_step, base_scheme, base_netloc, test_auth, pool_D_values, pool_D_priority, test_session, group_name, param_name, test_case=None, pools=None, pool_metadata=pool_D_metadata)
                rp = (res.get("request_params", {}) or {}).get("parameters", {})
                _update_pool_with_request(pool_D_values, pool_D_priority, rp, 3, step_obj=res, pool_metadata=pool_D_metadata)
                rsp = (res.get("response_params", {}) or {}).get("parameters", {})
                _update_pool_with_response(pool_D_values, pool_D_priority, rsp, res, pool_metadata=pool_D_metadata)
            logger.info(f"[DEBUG-Container-Boundary] Pool D (容器B wo last) 构建完成: {list(pool_D_values.keys())}")
            
            # ========== Pool E 构建：test_account 容器B 完整执行（对照组） ==========
            pool_E_values = {}
            pool_E_priority = {}
            pool_E_metadata = {}
            control_group_nested = {}  # 对照组执行结果
            for tk, sk, api_step in steps_nested:
                res = _execute_api_step(api_step, base_scheme, base_netloc, test_auth, pool_E_values, pool_E_priority, test_session, group_name, param_name, test_case=None, pools=None, pool_metadata=pool_E_metadata)
                _record_nested(control_group_nested, tk, sk, res)
                rp = (res.get("request_params", {}) or {}).get("parameters", {})
                _update_pool_with_request(pool_E_values, pool_E_priority, rp, 3, step_obj=res, pool_metadata=pool_E_metadata)
                rsp = (res.get("response_params", {}) or {}).get("parameters", {})
                _update_pool_with_response(pool_E_values, pool_E_priority, rsp, res, pool_metadata=pool_E_metadata)
            logger.info(f"[DEBUG-Container-Boundary] Pool E (对照组) 构建完成: {list(pool_E_values.keys())}")
            
            # ========== 实验组执行：使用 Pool C + Pool D 执行攻击 ==========
            test_group_nested = {}
            evidence_nested: Dict[str, Any] = {}
            last_param_value = None
            last_req_data = {}
            
            if last_tk:
                aliases = _alias_params_for_group(group_name, param_name)
                
                # 准备各个池（确保别名映射和组过滤）
                prepared_pool_c = _prepare_pool_for_testcase(pool_C_values, pool_C_metadata, aliases, group_name)
                prepared_pool_d = _prepare_pool_for_testcase(pool_D_values, pool_D_metadata, aliases, group_name)
                prepared_pool_e = _prepare_pool_for_testcase(pool_E_values, pool_E_metadata, aliases, group_name)
                pools_map = {"C": prepared_pool_c, "D": prepared_pool_d, "E": prepared_pool_e}
                
                logger.info(f"[DEBUG-Container-Boundary] 实验组执行: Pool C={prepared_pool_c}, Pool D={prepared_pool_d}")
                
                for tk, sk, api_step in steps_nested:
                    if tk != last_tk:
                        continue
                    res_final = _execute_api_step(api_step, base_scheme, base_netloc, test_auth, pool_C_values, pool_C_priority, test_session, group_name, param_name, test_case=test_case, pools=pools_map, pool_metadata=pool_C_metadata)
                    _record_nested(test_group_nested, tk, sk, res_final)
                    try:
                        req_data = (res_final.get("execution_status", {}) or {}).get("request_data", {})
                        if isinstance(req_data, dict):
                            last_req_data = req_data.copy()
                            for a in aliases:
                                if req_data.get(a) not in (None, ""):
                                    last_param_value = req_data.get(a)
                                    break
                    except Exception:
                        pass
            
            # 返回结果：对照组和实验组的执行结果
            return {
                "control_group": control_group_nested,  # 对照组：test_account容器B完整执行
                "test_group": test_group_nested,        # 实验组：test_account使用Pool C+D攻击
                "evidence": evidence_nested,
                "pools": {
                    "C": pool_C_values,  # 容器A参数
                    "D": pool_D_values,  # 容器B参数（执行到最后一步前）
                    "E": pool_E_values   # 容器B参数（完整执行，对照组）
                }
            }

        def _detect_locations_global(chain_obj, p, group_name):
            locs = set()
            try:
                aliases = _alias_params_for_group(group_name, p)
                steps_nested, _ = _iter_chain_steps_nested(chain_obj)
                for tk, sk, api_step in steps_nested:
                    step_obj = _extract_step_obj(api_step)
                    params = step_obj.get("request_params", {}).get("parameters", {})
                    for body_key in ("json", "data", "params"):
                        body = params.get(body_key, {})
                        if isinstance(body, dict):
                            for alias in aliases:
                                if alias in body:
                                    locs.add("body" if body_key in ("json", "data") else "query")
                                    break
                    route = step_obj.get("route", "") or ""
                    if isinstance(route, str):
                        try:
                            import re
                            placeholders = re.findall(r"\{([A-Za-z0-9_]+)\}", route)
                        except Exception:
                            placeholders = []
                        for alias in aliases:
                            if alias in placeholders:
                                locs.add("path")
                                break
            except Exception:
                pass
            return list(locs)

        def _check_mixed_scenario(chain_obj, group_name):
            """
            检测链条最后一步是否同时包含 ou_id 与 resource_id 参数。
            返回 (ou_param, resource_param) 或 (None, None)
            """
            try:
                steps_nested, top_keys = _iter_chain_steps_nested(chain_obj)
                if not top_keys:
                    return (None, None)
                last_tk = top_keys[-1]
                last_step = None
                for tk, sk, api_step in steps_nested:
                    if tk == last_tk:
                        last_step = _extract_step_obj(api_step)
                        break
                if not last_step:
                    return (None, None)
                params = last_step.get("request_params", {}).get("parameters", {})
                all_params = set()
                for key in ("params", "json", "data"):
                    p = params.get(key, {})
                    if isinstance(p, dict):
                        all_params.update(p.keys())
                route = last_step.get("route", "") or ""
                if isinstance(route, str):
                    import re
                    placeholders = re.findall(r"\{([A-Za-z0-9_]+)\}", route)
                    all_params.update(placeholders)

                # 选择 group 前缀
                m = getattr(self, "container_params_by_group", {}) or {}
                g1 = self._normalize_group_prefix(group_name or "")
                g2 = ""
                if not (g1 and ((g1 in (m.get("ou_id", {}) or {})) or (g1 in (m.get("resource_id", {}) or {})))):
                    r = self._normalize_group_prefix(route or "")
                    if r:
                        parts = [p for p in r.split("/") if p]
                        g2 = "/".join(parts[:2]) if len(parts) >= 2 else r
                g = g1 if g1 else g2
                ou_set = (m.get("ou_id", {}) or {}).get(g, set())
                res_set = (m.get("resource_id", {}) or {}).get(g, set())

                ou_param = None
                res_param = None
                for p in list(all_params):
                    aliases = _alias_params_for_group(group_name, p)
                    if (ou_param is None) and any(a in ou_set for a in aliases):
                        ou_param = p
                    if (res_param is None) and any(a in res_set for a in aliases):
                        res_param = p
                return (ou_param, res_param)
            except Exception:
                return (None, None)

        def _execute_chain_mixed(chain, ou_param_name, resource_param_name, group_name):
            """
            混合场景：最后一步同时包含 ou_id 与 resource_id。
            one-hot：ou_id→C，resource_id→A/E，非target→F
            """
            # 1) 池A：data_account 完整执行
            pool_a_values = {}
            pool_a_priority = {}
            pool_a_metadata = {}  # 新增
            data_only_nested = {}
            steps_nested, top_keys = _iter_chain_steps_nested(chain)
            for tk, sk, api_step in steps_nested:
                res = _execute_api_step(api_step, base_scheme, base_netloc, data_auth, pool_a_values, pool_a_priority, data_session, group_name, resource_param_name, test_case=None, pools=None, pool_metadata=pool_a_metadata)
                _record_nested(data_only_nested, tk, sk, res)
                rp = (res.get("request_params", {}) or {}).get("parameters", {})
                _update_pool_with_request(pool_a_values, pool_a_priority, rp, 3, step_obj=res, pool_metadata=pool_a_metadata)
                rsp = (res.get("response_params", {}) or {}).get("parameters", {})
                _update_pool_with_response(pool_a_values, pool_a_priority, rsp, res, pool_metadata=pool_a_metadata)

            first_tk = top_keys[0] if top_keys else None
            last_tk = top_keys[-1] if top_keys else None

            # 2) 池B/C：data/test 仅第一步
            pool_b_values, pool_b_priority = {}, {}
            pool_b_metadata = {}  # 新增
            pool_c_values, pool_c_priority = {}, {}
            pool_c_metadata = {}  # 新增
            if first_tk:
                for tk, sk, api_step in steps_nested:
                    if tk == first_tk:
                        r_b = _execute_api_step(api_step, base_scheme, base_netloc, data_auth, pool_b_values, pool_b_priority, data_session, group_name, resource_param_name, test_case=None, pools=None, pool_metadata=pool_b_metadata)
                        rp_b = (r_b.get("request_params", {}) or {}).get("parameters", {})
                        _update_pool_with_request(pool_b_values, pool_b_priority, rp_b, 3, step_obj=r_b, pool_metadata=pool_b_metadata)
                        rsp_b = (r_b.get("response_params", {}) or {}).get("parameters", {})
                        _update_pool_with_response(pool_b_values, pool_b_priority, rsp_b, r_b, pool_metadata=pool_b_metadata)

                        r_c = _execute_api_step(api_step, base_scheme, base_netloc, test_auth, pool_c_values, pool_c_priority, test_session, group_name, resource_param_name, test_case=None, pools=None, pool_metadata=pool_c_metadata)
                        rp_c = (r_c.get("request_params", {}) or {}).get("parameters", {})
                        _update_pool_with_request(pool_c_values, pool_c_priority, rp_c, 3, step_obj=r_c, pool_metadata=pool_c_metadata)
                        rsp_c = (r_c.get("response_params", {}) or {}).get("parameters", {})
                        _update_pool_with_response(pool_c_values, pool_c_priority, rsp_c, r_c, pool_metadata=pool_c_metadata)
                        break

            # 3) 池E/F：data/test 除最后步外执行
            pool_e_values, pool_e_priority = {}, {}
            pool_e_metadata = {}  # 新增
            pool_f_values, pool_f_priority = {}, {}
            pool_f_metadata = {}  # 新增
            test_nested_base = {}
            for tk, sk, api_step in steps_nested:
                if last_tk and tk == last_tk:
                    continue
                r_e = _execute_api_step(api_step, base_scheme, base_netloc, data_auth, pool_e_values, pool_e_priority, data_session, group_name, resource_param_name, test_case=None, pools=None, pool_metadata=pool_e_metadata)
                rp_e = (r_e.get("request_params", {}) or {}).get("parameters", {})
                _update_pool_with_request(pool_e_values, pool_e_priority, rp_e, 3, step_obj=r_e, pool_metadata=pool_e_metadata)
                rsp_e = (r_e.get("response_params", {}) or {}).get("parameters", {})
                _update_pool_with_response(pool_e_values, pool_e_priority, rsp_e, r_e, pool_metadata=pool_e_metadata)

                r_f = _execute_api_step(api_step, base_scheme, base_netloc, test_auth, pool_f_values, pool_f_priority, test_session, group_name, resource_param_name, test_case=None, pools=None, pool_metadata=pool_f_metadata)
                _record_nested(test_nested_base, tk, sk, r_f)
                rp_f = (r_f.get("request_params", {}) or {}).get("parameters", {})
                _update_pool_with_request(pool_f_values, pool_f_priority, rp_f, 3, step_obj=r_f, pool_metadata=pool_f_metadata)
                rsp_f = (r_f.get("response_params", {}) or {}).get("parameters", {})
                _update_pool_with_response(pool_f_values, pool_f_priority, rsp_f, r_f, pool_metadata=pool_f_metadata)

            ou_aliases = _alias_params_for_group(group_name, ou_param_name)
            res_aliases = _alias_params_for_group(group_name, resource_param_name)
            # 准备各个池（确保别名映射和组过滤）
            # 注意：混合场景中，ou_id 和 resource_id 可能使用不同的别名，我们分别准备
            prepared_pool_a_mix = _prepare_pool_for_testcase(pool_a_values, pool_a_metadata, res_aliases, group_name)
            prepared_pool_b_mix = _prepare_pool_for_testcase(pool_b_values, pool_b_metadata, res_aliases, group_name)
            prepared_pool_c_mix = _prepare_pool_for_testcase(pool_c_values, pool_c_metadata, ou_aliases, group_name)
            prepared_pool_e_mix = _prepare_pool_for_testcase(pool_e_values, pool_e_metadata, res_aliases, group_name)
            prepared_pool_f_mix = _prepare_pool_for_testcase(pool_f_values, pool_f_metadata, ou_aliases + res_aliases, group_name)
            pools_map = {"A": prepared_pool_a_mix, "B": prepared_pool_b_mix, "C": prepared_pool_c_mix, "E": prepared_pool_e_mix, "F": prepared_pool_f_mix}
            ou_locs = _detect_locations_global(chain, ou_param_name, group_name)
            res_locs = _detect_locations_global(chain, resource_param_name, group_name)

            out_list = []
            if not last_tk:
                return out_list
            # 获取最后一步类型
            last_step_obj = None
            last_api_step = None
            for tk, sk, api_step in steps_nested:
                if tk == last_tk:
                    last_api_step = api_step
                    last_step_obj = _extract_step_obj(api_step)
                    break
            last_type = (last_step_obj.get("type", "") or "").strip().lower() if last_step_obj else ""
            res_value_source = "A" if last_type in ("query", "list query") else "E"

            def _set_alias_values(pool, aliases, val, pri_map=None, pri=7):
                if val in (None, ""):
                    return
                for a in aliases:
                    pool[a] = val
                    if pri_map is not None:
                        pri_map[a] = max(pri_map.get(a, -1), pri)

            def _build_ou_evidence(last_req_data):
                # 被测参数值优先级：last_req_data → C池 → B池 → 嵌套提取
                last_param_value = None
                aliases = _alias_params_for_group(group_name, ou_param_name)
                if isinstance(last_req_data, dict):
                    for a in aliases:
                        if last_req_data.get(a) not in (None, ""):
                            last_param_value = last_req_data.get(a)
                            break
                if last_param_value in (None, ""):
                    for a in aliases:
                        if pool_c_values.get(a) not in (None, ""):
                            last_param_value = pool_c_values.get(a)
                            break
                if last_param_value in (None, ""):
                    for a in aliases:
                        if pool_b_values.get(a) not in (None, ""):
                            last_param_value = pool_b_values.get(a)
                            break
                if last_param_value in (None, ""):
                    try:
                        _v = _find_param_value_in_nested(data_only_nested, aliases)
                        if _v not in (None, ""):
                            last_param_value = _v
                    except Exception:
                        pass
                
                if last_param_value is not None:
                    return _build_evidence_query(
                        last_step_obj=None,
                        param_name=ou_param_name,
                        group_name=group_name,
                        target_param_value=last_param_value,
                        data_auth=data_auth,
                        last_req_data=last_req_data
                    )
                return {}

            # ou_id one-hot
            if ou_locs:
                for target_pos in ou_locs:
                    exec_pool = dict(prepared_pool_f_mix)  # 使用已准备的扁平池
                    exec_pri = {}
                    # resource_id 固定为 A/E
                    res_val = _get_value_from_grouped_pool(pool_a_values if res_value_source == "A" else pool_e_values, res_aliases, target_group=group_name)
                    _set_alias_values(exec_pool, res_aliases, res_val, exec_pri, pri=8)
                    # 构造 ou_id TestCase（非target→C）
                    tc = MatrixTestCase(
                        case_type="multi_param" if len(ou_locs) > 1 else "overprivilege",
                        param_name=ou_param_name,
                        aliases=ou_aliases,
                        value_source="C",
                        locations=ou_locs if ou_locs else ["query"],
                        extra_params={
                            "target_position": target_pos,
                            "position_mode": "multi" if len(ou_locs) > 1 else "single",
                            "category": "ou_id",
                            "non_target_source": "C"
                        }
                    )
                    test_nested = copy.deepcopy(test_nested_base)
                    # 为 exec_pool 创建临时 metadata（基于 pool_f）
                    exec_metadata = dict(pool_f_metadata)
                    res_final = _execute_api_step(last_api_step, base_scheme, base_netloc, test_auth, exec_pool, exec_pri, test_session, group_name, ou_param_name, test_case=tc, pools=pools_map, pool_metadata=exec_metadata)
                    _record_nested(test_nested, last_tk, f"ou_{target_pos}", res_final)
                    last_req_data = (res_final.get("execution_status", {}) or {}).get("request_data", {})
                    evidence_nested = _build_ou_evidence(last_req_data)
                    res_final["test_meta"] = {
                        "category": "mixed",
                        "case_type": tc.case_type,
                        "param_name": ou_param_name,
                        "resource_param_name": resource_param_name,
                        "value_source": tc.value_source,
                        "target_position": target_pos,
                        "position_mode": tc.extra_params.get("position_mode", "single"),
                        "scenario": "mixed_ou_resource",
                        "tested_param_type": "ou_id",
                        "param_sources": {ou_param_name: _build_param_sources(tc, ou_locs)},
                        "param_values": {ou_param_name: _extract_param_values_from_step(res_final, ou_aliases)},
                        "param_alias_sources": {ou_param_name: _build_param_sources_by_aliases(_extract_param_values_by_aliases(res_final, ou_aliases), _build_param_sources(tc, ou_locs))},
                        "param_alias_values": {ou_param_name: _extract_param_values_by_aliases(res_final, ou_aliases)},
                        "param_source_path": {
                            "data_account": _find_param_source_in_nested(data_only_nested, ou_aliases),
                            "test_account": _find_param_source_in_nested(test_nested, ou_aliases)
                        }
                    }
                    out_list.append({"data_account": data_only_nested, "test_account": test_nested, "evidence": evidence_nested})

            # resource_id one-hot
            if res_locs:
                for target_pos in res_locs:
                    exec_pool = dict(prepared_pool_f_mix)  # 使用已准备的扁平池
                    exec_pri = {}
                    # ou_id 固定为 C
                    ou_val = _get_value_from_grouped_pool(pool_c_values, ou_aliases, target_group=group_name)
                    _set_alias_values(exec_pool, ou_aliases, ou_val, exec_pri, pri=7)
                    tc = MatrixTestCase(
                        case_type="multi_param" if len(res_locs) > 1 else "overprivilege",
                        param_name=resource_param_name,
                        aliases=res_aliases,
                        value_source=res_value_source,
                        locations=res_locs if res_locs else ["query"],
                        extra_params={
                            "target_position": target_pos,
                            "position_mode": "multi" if len(res_locs) > 1 else "single",
                            "category": "resource_id",
                            "non_target_source": "F"
                        }
                    )
                    test_nested = copy.deepcopy(test_nested_base)
                    # 为 exec_pool 创建临时 metadata（基于 pool_f）
                    exec_metadata = dict(pool_f_metadata)
                    res_final = _execute_api_step(last_api_step, base_scheme, base_netloc, test_auth, exec_pool, exec_pri, test_session, group_name, resource_param_name, test_case=tc, pools=pools_map, pool_metadata=exec_metadata)
                    _record_nested(test_nested, last_tk, f"res_{target_pos}", res_final)
                    last_req_data = (res_final.get("execution_status", {}) or {}).get("request_data", {})
                    evidence_nested = _build_ou_evidence(last_req_data)
                    res_final["test_meta"] = {
                        "category": "mixed",
                        "case_type": tc.case_type,
                        "param_name": resource_param_name,
                        "ou_param_name": ou_param_name,
                        "value_source": tc.value_source,
                        "target_position": target_pos,
                        "position_mode": tc.extra_params.get("position_mode", "single"),
                        "scenario": "mixed_ou_resource",
                        "tested_param_type": "resource_id",
                        "param_sources": {resource_param_name: _build_param_sources(tc, res_locs)},
                        "param_values": {resource_param_name: _extract_param_values_from_step(res_final, res_aliases)},
                        "param_alias_sources": {resource_param_name: _build_param_sources_by_aliases(_extract_param_values_by_aliases(res_final, res_aliases), _build_param_sources(tc, res_locs))},
                        "param_alias_values": {resource_param_name: _extract_param_values_by_aliases(res_final, res_aliases)},
                        "param_source_path": {
                            "data_account": _find_param_source_in_nested(data_only_nested, res_aliases),
                            "test_account": _find_param_source_in_nested(test_nested, res_aliases)
                        }
                    }
                    out_list.append({"data_account": data_only_nested, "test_account": test_nested, "evidence": evidence_nested})

            return out_list

        # 并发处理：按功能组维度拆分任务，主线程负责归并与进度记录
        from concurrent.futures import ThreadPoolExecutor, as_completed
        futures = []

        def _build_strategy_string(test_case, test_type: str) -> str:
            """
            构建策略描述字符串（对齐取值关系文档）
            
            Args:
                test_case: TestCase对象
                test_type: "BOLA" 或 "ContainerBoundary"
            
            Returns:
                格式化的策略字符串，如:
                - BOLA_SingleLoc_Query_Target:A
                - BOLA_OneHot_Update_Target:B_NonTarget:C
                - ContainerBoundary_SingleLoc_Target:C_NonTarget:D
            """
            position_mode = test_case.extra_params.get("position_mode", "single") if isinstance(test_case.extra_params, dict) else "single"
            last_step_type = (test_case.extra_params.get("last_step_type", "") if isinstance(test_case.extra_params, dict) else "").strip()
            # 首字母大写，处理特殊情况
            if last_step_type:
                last_step_type = last_step_type.replace("list query", "ListQuery").replace(" ", "").capitalize()
                if last_step_type.lower() == "listquery":
                    last_step_type = "ListQuery"
            value_source = test_case.value_source or "?"
            non_target_source = test_case.extra_params.get("non_target_source", "C") if isinstance(test_case.extra_params, dict) else "C"
            
            if position_mode == "single":
                loc_mode = "SingleLoc"
                return f"{test_type}_{loc_mode}_{last_step_type}_Target:{value_source}"
            else:
                loc_mode = "OneHot"
                return f"{test_type}_{loc_mode}_{last_step_type}_Target:{value_source}_NonTarget:{non_target_source}"

        def _process_resource_group(item):
            group_name, param_dict = item
            group_out = {}
            # 组内提升到“链级并发”：同一功能组下，每个参数名的链并发执行，链内步骤保持顺序
            import os
            from concurrent.futures import ThreadPoolExecutor, as_completed
            for param_name, categories in param_dict.items():
                group_out[param_name] = {"cross": [], "group": []}
                cats = _normalize_categories(categories)
                for chain_type, chains in cats.items():
                    if not isinstance(chains, list):
                        continue
                    # 过滤掉非字典的链条，保留索引以便按原有顺序写回
                    indexed_chains = [(idx, c) for idx, c in enumerate(chains) if isinstance(c, dict)]
                    if not indexed_chains:
                        continue
                    # 内层线程池大小：保守阈值以避免过载；可用 BOLASCAN_CHAIN_WORKERS 配置
                    try:
                        cpu = os.cpu_count() or 4
                    except Exception:
                        cpu = 4
                    try:
                        chain_cap = int(os.getenv('BOLASCAN_CHAIN_WORKERS', '24'))
                    except Exception:
                        chain_cap = 24
                    max_workers_inner = max(1, min(chain_cap, len(indexed_chains), cpu * 2))

                    # 执行单条链的包装，保证进度更新与异常捕获
                    def _detect_locations(chain_obj, p):
                        locs = set()
                        try:
                            # 获取参数的所有别名（包括自身）
                            aliases = _alias_params_for_group(group_name, p)
                            logger.info(f"[DEBUG-DetectLoc] param={p}, group={group_name}, aliases={aliases}")
                            
                            steps_nested, _ = _iter_chain_steps_nested(chain_obj)
                            for tk, sk, api_step in steps_nested:
                                step_obj = _extract_step_obj(api_step)
                                params = step_obj.get("request_params", {}).get("parameters", {})
                                
                                # 检查body/query中是否存在参数或其别名
                                for body_key in ("json", "data", "params"):
                                    body = params.get(body_key, {})
                                    if isinstance(body, dict):
                                        for alias in aliases:
                                            if alias in body:
                                                locs.add("body" if body_key in ("json", "data") else "query")
                                                logger.info(f"[DEBUG-DetectLoc] 在{body_key}中找到别名: {alias}")
                                                break
                                
                                # 检查path中是否存在参数或其别名（仅识别路由占位符，避免误匹配字符串子串）
                                route = step_obj.get("route", "") or ""
                                if isinstance(route, str):
                                    try:
                                        import re
                                        placeholders = re.findall(r"\{([A-Za-z0-9_]+)\}", route)
                                    except Exception:
                                        placeholders = []
                                    for alias in aliases:
                                        if alias in placeholders:
                                            locs.add("path")
                                            logger.info(f"[DEBUG-DetectLoc] 在path占位符中找到别名: {alias}")
                                            break
                        except Exception as e:
                            logger.info(f"[DEBUG-DetectLoc] Exception: {e}")
                            pass
                        
                        logger.info(f"[DEBUG-DetectLoc] param={p}, detected_locations={list(locs)}")
                        return list(locs)

                    def _last_step_type(chain_obj):
                        lt = None
                        try:
                            steps_nested, top_keys = _iter_chain_steps_nested(chain_obj)
                            last_tk = top_keys[-1] if top_keys else None
                            for tk, sk, api_step in steps_nested:
                                if last_tk and tk == last_tk:
                                    step_obj = _extract_step_obj(api_step)
                                    lt = (step_obj.get("type", "") or "").strip().lower()
                                    break
                        except Exception:
                            lt = None
                        return lt

                    def _build_cases_for_chain(chain_obj):
                        locs = _detect_locations(chain_obj, param_name)
                        last_t = _last_step_type(chain_obj)
                        aliases = _alias_params_for_group(group_name, param_name)
                        # 仅保留 BOLA 参数替换类用例（normal/overprivilege/multi_param）
                        return self.build_test_cases(param_name, locs, aliases, last_step_type=(last_t or ""), category="resource_id", include_extra_types=False)

                    results_buffer = [None] * len(chains)
                    with ThreadPoolExecutor(max_workers=max_workers_inner) as pool:
                        def _submit(idx, chain_obj):
                            try:
                                def _param_matches(p1, p2):
                                    if p1 == p2:
                                        return True
                                    try:
                                        a1 = _alias_params_for_group(group_name, p1)
                                        a2 = _alias_params_for_group(group_name, p2)
                                        return (p1 in a2) or (p2 in a1)
                                    except Exception:
                                        return False

                                out_list = []
                                # 混合场景检测：最后一步同时包含 ou_id 与 resource_id
                                ou_p, res_p = _check_mixed_scenario(chain_obj, group_name)
                                if ou_p and res_p and _param_matches(res_p, param_name):
                                    mixed_results = _execute_chain_mixed(chain_obj, ou_p, res_p, group_name)
                                    return idx, mixed_results
                                for tc in _build_cases_for_chain(chain_obj):
                                    # 诊断日志：检查TestCase对象
                                    logger.info(f"[DEBUG-TC-Exec] param={param_name}, case_type={tc.case_type}, extra_params={tc.extra_params}")
                                    if isinstance(tc.extra_params, dict):
                                        logger.info(f"[DEBUG-TC-Exec] target_position={tc.extra_params.get('target_position')}")
                                    
                                    res_exec = _execute_chain_resource(chain_obj, param_name, group_name, test_case=tc)
                                    # 记录参数来源与取值
                                    try:
                                        last_step_obj = _get_last_step_obj_from_results(res_exec.get("test_account", {}))
                                        aliases = _alias_params_for_group(group_name, param_name)
                                        values = _extract_param_values_from_step(last_step_obj, aliases)
                                        sources = _build_param_sources(tc, _detect_locations(chain_obj, param_name))
                                        alias_values = _extract_param_values_by_aliases(last_step_obj, aliases)
                                        alias_sources = _build_param_sources_by_aliases(alias_values, sources)
                                        source_path = {
                                            "data_account": _find_param_source_in_nested(res_exec.get("data_account", {}), aliases),
                                            "test_account": _find_param_source_in_nested(res_exec.get("test_account", {}), aliases)
                                        }
                                    except Exception:
                                        values = {}
                                        sources = {}
                                        alias_values = {}
                                        alias_sources = {}
                                        source_path = {}
                                    try:
                                        target_pos_value = tc.extra_params.get("target_position") if isinstance(tc.extra_params, dict) else None
                                        logger.info(f"[DEBUG-TC-Meta] Recording target_position={target_pos_value}")
                                        
                                        res_exec["test_meta"] = {
                                            "category": "resource_id",
                                            "case_type": tc.case_type,
                                            "value_source": tc.value_source,
                                            "position_mode": tc.extra_params.get("position_mode", "single"),
                                            "last_step_type": tc.extra_params.get("last_step_type", ""),
                                            "target_position": target_pos_value,
                                            "group_name": group_name,
                                            "param_name": param_name,
                                            "strategy": _build_strategy_string(tc, "BOLA"),
                                            "param_sources": sources,
                                            "param_values": values,
                                            "param_alias_sources": alias_sources,
                                            "param_alias_values": alias_values,
                                            "param_source_path": source_path
                                        }
                                    except Exception:
                                        pass
                                    out_list.append(res_exec)
                                return idx, out_list
                            except Exception as e:
                                return idx, {"error": f"{type(e).__name__}: {str(e)}"}
                            finally:
                                _increment_progress(group_name)
                        future_to_idx = {pool.submit(_submit, idx, c): idx for idx, c in indexed_chains}
                        for fut in as_completed(future_to_idx):
                            try:
                                idx, res = fut.result()
                            except Exception as e:
                                idx = future_to_idx[fut]
                                res = {"error": f"{type(e).__name__}: {str(e)}"}
                            # 写回到与原始链列表相同的位置，保持输出顺序稳定
                            results_buffer[idx] = res
                    # 仅追加非空结果，保持与原链顺序一致
                    for r in results_buffer:
                        if r is None:
                            continue
                        if isinstance(r, list):
                            for item in r:
                                group_out[param_name][chain_type].append(item)
                        else:
                            group_out[param_name][chain_type].append(r)
            return group_name, group_out
        def _process_ou_group(item):
            group_name, param_dict = item
            group_out = {}
            # 组内提升到“链级并发”：同一功能组下，每个参数名的链并发执行，链内步骤保持顺序
            import os
            from concurrent.futures import ThreadPoolExecutor, as_completed
            for param_name, categories in param_dict.items():
                group_out[param_name] = {"cross": [], "group": []}
                cats = _normalize_categories(categories)
                for chain_type, chains in cats.items():
                    if not isinstance(chains, list):
                        continue
                    indexed_chains = [(idx, c) for idx, c in enumerate(chains) if isinstance(c, dict)]
                    if not indexed_chains:
                        continue
                    # 内层线程池大小：保守阈值以避免过载；可用 BOLASCAN_CHAIN_WORKERS 配置
                    try:
                        cpu = os.cpu_count() or 4
                    except Exception:
                        cpu = 4
                    try:
                        chain_cap = int(os.getenv('BOLASCAN_CHAIN_WORKERS', '24'))
                    except Exception:
                        chain_cap = 24
                    max_workers_inner = max(1, min(chain_cap, len(indexed_chains), cpu * 2))

                    def _detect_locations(chain_obj, p):
                        locs = set()
                        try:
                            aliases = _alias_params_for_group(group_name, p)
                            steps_nested, _ = _iter_chain_steps_nested(chain_obj)
                            for tk, sk, api_step in steps_nested:
                                step_obj = _extract_step_obj(api_step)
                                params = step_obj.get("request_params", {}).get("parameters", {})
                                for body_key in ("json", "data", "params"):
                                    body = params.get(body_key, {})
                                    if isinstance(body, dict):
                                        for alias in aliases:
                                            if alias in body:
                                                locs.add("body" if body_key in ("json", "data") else "query")
                                                break
                                route = step_obj.get("route", "") or ""
                                if isinstance(route, str):
                                    try:
                                        import re
                                        placeholders = re.findall(r"\{([A-Za-z0-9_]+)\}", route)
                                    except Exception:
                                        placeholders = []
                                    for alias in aliases:
                                        if alias in placeholders:
                                            locs.add("path")
                                            break
                        except Exception:
                            pass
                        return list(locs)

                    def _last_step_type(chain_obj):
                        lt = None
                        try:
                            steps_nested, top_keys = _iter_chain_steps_nested(chain_obj)
                            last_tk = top_keys[-1] if top_keys else None
                            for tk, sk, api_step in steps_nested:
                                if last_tk and tk == last_tk:
                                    step_obj = _extract_step_obj(api_step)
                                    lt = (step_obj.get("type", "") or "").strip().lower()
                                    break
                        except Exception:
                            lt = None
                        return lt

                    def _build_cases_for_chain(chain_obj):
                        locs = _detect_locations(chain_obj, param_name)
                        last_t = _last_step_type(chain_obj)
                        aliases = _alias_params_for_group(group_name, param_name)
                        # 仅保留 BOLA 参数替换类用例（normal/overprivilege/multi_param）
                        return self.build_test_cases(param_name, locs, aliases, last_step_type=(last_t or ""), category="ou_id", include_extra_types=False)

                    def _run_one(idx, chain_obj):
                        try:
                            def _param_matches(p1, p2):
                                if p1 == p2:
                                    return True
                                try:
                                    a1 = _alias_params_for_group(group_name, p1)
                                    a2 = _alias_params_for_group(group_name, p2)
                                    return (p1 in a2) or (p2 in a1)
                                except Exception:
                                    return False

                            out_list = []
                            # 混合场景：由 resource 分支统一处理，ou_id 分支跳过
                            ou_p, res_p = _check_mixed_scenario(chain_obj, group_name)
                            if ou_p and res_p and _param_matches(ou_p, param_name):
                                return idx, []
                            
                            # ========== BOLA测试（跨账号越权） ==========
                            for tc in _build_cases_for_chain(chain_obj):
                                # 诊断日志：检查TestCase对象
                                logger.info(f"[DEBUG-TC-Exec-OU] param={param_name}, case_type={tc.case_type}, extra_params={tc.extra_params}")
                                if isinstance(tc.extra_params, dict):
                                    logger.info(f"[DEBUG-TC-Exec-OU] target_position={tc.extra_params.get('target_position')}")
                                
                                res_exec = _execute_chain_ou(chain_obj, param_name, group_name, test_case=tc)
                                # 记录参数来源与取值
                                try:
                                    last_step_obj = _get_last_step_obj_from_results(res_exec.get("test_account", {}))
                                    aliases = _alias_params_for_group(group_name, param_name)
                                    values = _extract_param_values_from_step(last_step_obj, aliases)
                                    sources = _build_param_sources(tc, _detect_locations(chain_obj, param_name))
                                    alias_values = _extract_param_values_by_aliases(last_step_obj, aliases)
                                    alias_sources = _build_param_sources_by_aliases(alias_values, sources)
                                    source_path = {
                                        "data_account": _find_param_source_in_nested(res_exec.get("data_account", {}), aliases),
                                        "test_account": _find_param_source_in_nested(res_exec.get("test_account", {}), aliases)
                                    }
                                except Exception:
                                    values = {}
                                    sources = {}
                                    alias_values = {}
                                    alias_sources = {}
                                    source_path = {}
                                try:
                                    target_pos_value = tc.extra_params.get("target_position") if isinstance(tc.extra_params, dict) else None
                                    logger.info(f"[DEBUG-TC-Meta-OU] Recording target_position={target_pos_value}")
                                    
                                    res_exec["test_meta"] = {
                                        "category": "ou_id",
                                        "case_type": tc.case_type,
                                        "value_source": tc.value_source,
                                        "position_mode": tc.extra_params.get("position_mode", "single"),
                                        "last_step_type": tc.extra_params.get("last_step_type", ""),
                                        "target_position": target_pos_value,
                                        "group_name": group_name,
                                        "param_name": param_name,
                                        "strategy": _build_strategy_string(tc, "BOLA"),
                                        "param_sources": sources,
                                        "param_values": values,
                                        "param_alias_sources": alias_sources,
                                        "param_alias_values": alias_values,
                                        "param_source_path": source_path
                                    }
                                except Exception:
                                    pass
                                out_list.append(res_exec)
                            
                            # ========== 容器边界测试（同账号跨容器） ==========
                            # 仅对容器参数(ou_id)执行容器边界测试
                            try:
                                # 获取参数位置
                                locations = _detect_locations_global(chain_obj, param_name, group_name)
                                aliases = _alias_params_for_group(group_name, param_name)
                                last_step_type = _last_step_type(chain_obj)
                                
                                # 构建容器边界测试用例
                                from utils.bola_vulner.horizontal.testcase_matrix import build_container_boundary_test_cases
                                container_boundary_cases = build_container_boundary_test_cases(
                                    param_name, locations, aliases, last_step_type, "ou_id"
                                )
                                
                                for cb_tc in container_boundary_cases:
                                    logger.info(f"[DEBUG-Container-Boundary] Executing for param={param_name}, case_type={cb_tc.case_type}")
                                    
                                    cb_res = _execute_chain_container_boundary(chain_obj, param_name, group_name, test_case=cb_tc)
                                    
                                    # 记录容器边界测试元数据
                                    cb_res["test_meta"] = {
                                        "category": "ou_id",
                                        "case_type": cb_tc.case_type,
                                        "value_source": cb_tc.value_source,
                                        "position_mode": cb_tc.extra_params.get("position_mode", "single"),
                                        "last_step_type": cb_tc.extra_params.get("last_step_type", ""),
                                        "target_position": cb_tc.extra_params.get("target_position"),
                                        "non_target_source": cb_tc.extra_params.get("non_target_source", "D"),
                                        "comparison_source": cb_tc.extra_params.get("comparison_source", "E"),
                                        "group_name": group_name,
                                        "param_name": param_name,
                                        "strategy": _build_strategy_string(cb_tc, "ContainerBoundary")
                                    }
                                    out_list.append(cb_res)
                            except Exception as e:
                                logger.warning(f"[DEBUG-Container-Boundary] Error: {e}")
                            
                            return idx, out_list
                        except Exception as e:
                            return idx, {"error": f"{type(e).__name__}: {str(e)}"}
                        finally:
                            _increment_progress(group_name)

                    results_buffer = [None] * len(chains)
                    with ThreadPoolExecutor(max_workers=max_workers_inner) as pool:
                        future_to_idx = {pool.submit(_run_one, idx, c): idx for idx, c in indexed_chains}
                        for fut in as_completed(future_to_idx):
                            try:
                                idx, res = fut.result()
                            except Exception as e:
                                idx = future_to_idx[fut]
                                res = {"error": f"{type(e).__name__}: {str(e)}"}
                            results_buffer[idx] = res
                    for r in results_buffer:
                        if r is None:
                            continue
                        if isinstance(r, list):
                            for item in r:
                                group_out[param_name][chain_type].append(item)
                        else:
                            group_out[param_name][chain_type].append(r)
            return group_name, group_out

        # 提交任务（功能组维度），保守并发阈值以避免过载；可用 BOLASCAN_GROUP_WORKERS 配置
        import os
        try:
            group_cap = int(os.getenv('BOLASCAN_GROUP_WORKERS', '12'))
        except Exception:
            group_cap = 12
        max_workers = max(1, min(group_cap, (os.cpu_count() or 4) * 2))
        # max_workers = 1
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_category = {}
            for gn, gd in resource_dict.items():
                fut = executor.submit(_process_resource_group, (gn, gd))
                futures.append(fut)
                future_category[fut] = "resource_id"
            for gn, gd in ou_dict.items():
                fut = executor.submit(_process_ou_group, (gn, gd))
                futures.append(fut)
                future_category[fut] = "ou_id"
            for fut in as_completed(futures):
                gn, gout = fut.result()
                category = future_category.get(fut, "resource_id")
                if category not in results:
                    results[category] = {}
                if gn not in results[category]:
                    results[category][gn] = {}
                # 归并该组的结果
                for pn, buckets in gout.items():
                    if pn not in results[category][gn]:
                        results[category][gn][pn] = {"cross": [], "group": []}
                    results[category][gn][pn]["cross"].extend(buckets.get("cross", []))
                    results[category][gn][pn]["group"].extend(buckets.get("group", []))

        # 最终确保进度文件写入
        try:
            self.jsontool.write_json(progress_path, progress)
        except Exception:
            pass

        return results
    
    def bola_vul_judgement(self, execution_results):
        """
        目前，执行得到了很多请求结果，接下来就比对，data_type下的依赖链的最后一个接口响应的参数名和test_type最后一个接口响应的参数名是否一致，并且值不为空即可，然后返回存在越权的接口以及参数名。
        适配新的结果结构：顶层包含 cross/group 两种类型。
        """
        import threading
        from concurrent.futures import ThreadPoolExecutor
        # llm_lock = threading.Lock()
        # 容器参数集合（由 horizontal_bola_workflow 缓存），用于决定检查步骤策略
        # container_param_set = getattr(self, "container_param_set", set())

        # === 判定阶段进度条初始化 ===
        import time
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
        progress_path = os.path.join(project_root, 'cache', self.project_name, 'horizontal_results', 'judgement_progress.json')
        progress_lock = threading.Lock()
        start_time = time.time()

        def _count_total_items(_exec):
            total = 0
            if not isinstance(_exec, dict):
                return 0
            def _count_group(gd):
                t = 0
                if not isinstance(gd, dict):
                    return 0
                for _pn, _bucket in gd.items():
                    if isinstance(_bucket, dict):
                        for _bk in ["cross", "group"]:
                            _cl = _bucket.get(_bk, [])
                            if isinstance(_cl, list):
                                t += len(_cl)
                    elif isinstance(_bucket, list):
                        t += len(_bucket)
                return t
            if ("resource_id" in _exec) or ("ou_id" in _exec):
                for _cat in ["resource_id", "ou_id"]:
                    _cd = _exec.get(_cat, {})
                    if isinstance(_cd, dict):
                        for _gn, _gd in _cd.items():
                            total += _count_group(_gd)
            else:
                for _gn, _gd in _exec.items():
                    total += _count_group(_gd)
            return total

        progress = {"total": _count_total_items(execution_results), "completed": 0, "by_group": {}}

        def _format_duration(seconds):
            try:
                seconds = int(seconds)
            except Exception:
                seconds = 0
            minutes, seconds = divmod(seconds, 60)
            hours, minutes = divmod(minutes, 60)
            if hours:
                return f"{hours}h {minutes}m {seconds}s"
            if minutes:
                return f"{minutes}m {seconds}s"
            return f"{seconds}s"

        def _update_terminal_progress(completed, total):
            try:
                width = 40
                total = max(1, int(total))
                completed = max(0, min(int(completed), total))
                pct = int(completed * 100 / total)
                filled = int(width * completed / total)
                bar = ("=" * max(0, filled - 1)) + (">" if filled > 0 else "") + ("." * (width - filled))
                now = time.time()
                elapsed = now - start_time
                eta = ((elapsed / completed) * (total - completed)) if completed > 0 else 0
                msg = f"[{bar}] {pct}% {completed}/{total} | elapsed { _format_duration(elapsed) } | eta { _format_duration(eta) }"
                sys.stdout.write("\r" + msg)
                sys.stdout.flush()
                if completed == total:
                    sys.stdout.write("\n")
                    sys.stdout.flush()
            except Exception:
                pass

        def _increment_progress(group_name):
            with progress_lock:
                progress["completed"] += 1
                if group_name:
                    progress["by_group"][group_name] = progress["by_group"].get(group_name, 0) + 1
                try:
                    _update_terminal_progress(progress["completed"], progress["total"])
                except Exception:
                    pass
                threshold = max(1, progress["total"] // 10) if progress["total"] > 0 else 1
                if (progress["completed"] % threshold == 0) or (progress["completed"] == progress["total"]):
                    try:
                        self.jsontool.write_json(progress_path, progress)
                    except Exception:
                        pass

        def _get_last_step(account_results):
            """获取最后执行的步骤"""
            if not account_results:
                return None
            # 获取所有步骤号并排序
            step_numbers = [int(step) for step in account_results.keys() if str(step).isdigit()]
            if not step_numbers:
                return None
            last_step_num = str(max(step_numbers))
            return account_results.get(last_step_num)

        def _get_prev_step(account_results):
            """获取倒数第二个步骤（若不存在则返回 None）"""
            if not account_results:
                return None
            step_numbers = sorted([int(step) for step in account_results.keys() if str(step).isdigit()])
            if not step_numbers or len(step_numbers) < 2:
                return None
            prev_step_num = str(step_numbers[-2])
            return account_results.get(prev_step_num)

        def _extract_response_params(step_data):
            """提取响应参数"""
            if not step_data:
                return {}
            response_params = step_data.get("response_params", {})
            if isinstance(response_params, dict):
                parameters = response_params.get("parameters", {})
                if isinstance(parameters, dict):
                    return parameters
            return {}

        def _extract_request_params(step_data):
            """提取请求参数"""
            if not step_data:
                return {}
            response_params = step_data.get("request_params", {})
            if isinstance(response_params, dict):
                parameters = response_params.get("parameters", {})
                if isinstance(parameters, dict):
                    # 删除headers键
                    if "headers" in parameters:
                        del parameters["headers"]
                    return parameters
            return {}

        def _get_api_type_for_step(step_data, group_name):
            """基于 true_params 的文档映射，推断指定步骤的接口类型（query/update/delete/add/list query 等）。"""
            try:
                group_map = self.true_params.get(group_name, {}) if isinstance(self.true_params, dict) else {}
            except Exception:
                group_map = {}
            if not isinstance(group_map, dict) or not step_data:
                return None
            method = (step_data.get("method") or step_data.get("method") or "").upper()
            route = step_data.get("route") or step_data.get("route") or ""

            def parse_key(key):
                if not isinstance(key, str):
                    return None, None, key
                s = key.strip()
                s = s.replace("：", " ").replace(":", " ")
                parts = s.split()
                if len(parts) >= 2:
                    return parts[0].upper(), " ".join(parts[1:]), key
                return None, s, key

            # 完整匹配优先
            if method and route:
                for k, v in group_map.items():
                    km, kr, _raw = parse_key(k)
                    if km and kr and km == method and kr == route:
                        return (v.get("type") or v.get("api_type") or v.get("api_type") or "").strip().lower()
                # 兼容以路由结尾的键
                for k, v in group_map.items():
                    if isinstance(k, str) and k.endswith(route):
                        return (v.get("type") or v.get("api_type") or v.get("api_type") or "").strip().lower()

            # 路由匹配（忽略方法）
            for k, v in group_map.items():
                km, kr, _raw = parse_key(k)
                if kr and kr == route:
                    return (v.get("type") or v.get("api_type") or v.get("api_type") or "").strip().lower()
            return None

        def _routes_match(ds, ts):
            try:
                dm = (ds or {}).get("method", "")
                tm = (ts or {}).get("method", "")
                dr = (ds or {}).get("route", "")
                tr = (ts or {}).get("route", "")
                return (isinstance(dm, str) and isinstance(tm, str) and dm == tm) and (isinstance(dr, str) and isinstance(tr, str) and dr == tr)
            except Exception:
                return False

        def _is_query_type(tp):
            t = (tp or "").strip().lower()
            return t in ("query", "list query")

        def _is_update_or_delete(tp):
            t = (tp or "").strip().lower()
            return t in ("update", "delete")

        def _generate_test_description(test_meta, api_key):
            """
            Convert test_meta metadata to natural language description
            
            Args:
                test_meta: Test metadata dictionary
                api_key: API endpoint identifier (e.g., "DELETE:/identity/api/v2/admin/videos/{video_id}")
                
            Returns:
                str: Natural language description in English
            """
            if not test_meta or not isinstance(test_meta, dict):
                return "No test description available."
            
            # Extract key information
            param_name = test_meta.get("param_name", "unknown_param")
            last_step_type = test_meta.get("last_step_type", "unknown_operation")
            case_type = test_meta.get("case_type", "unknown_strategy")
            
            # Extract actual value from param_alias_values
            param_alias_values = test_meta.get("param_alias_values", {})
            param_value = None
            if isinstance(param_alias_values, dict):
                param_info = param_alias_values.get(param_name, {})
                if isinstance(param_info, dict):
                    param_value = param_info.get("value")
            
            # If no value in param_alias_values, try param_values
            if param_value is None:
                param_values = test_meta.get("param_values", {})
                if isinstance(param_values, dict):
                    for pos in ["path", "query", "body", "header"]:
                        val = param_values.get(pos)
                        if val not in (None, ""):
                            param_value = val
                            break
            
            # If still no value, use placeholder
            if param_value is None:
                param_value = "unknown_value"
            
            # Operation type mapping (English)
            operation_map = {
                "delete": "deleted",
                "update": "modified",
                "query": "queried",
                "add": "created",
                "list query": "listed"
            }
            operation = operation_map.get(last_step_type.lower(), f"performed {last_step_type} operation on")
            
            # Test strategy description
            strategy_map = {
                "overprivilege": "Overprivilege Attack",
                "multi_param": "Multi-Parameter Injection",
                "container_boundary": "Container Boundary Attack"
            }
            strategy_desc = strategy_map.get(case_type, case_type)
            
            # Generate description based on test type
            if case_type == "multi_param":
                # Multi-Parameter (One-Hot) Injection 测试
                param_alias_sources = test_meta.get("param_alias_sources", {})
                param_alias_values = test_meta.get("param_alias_values", {})
                target_position = test_meta.get("target_position", "unknown")
                
                # 构建参数注入详情
                injection_details = []
                target_param_info = None
                target_value = None
                non_target_param_info = None
                non_target_value = None
                
                for alias, source in param_alias_sources.items():
                    value_info = param_alias_values.get(alias, {})
                    position = value_info.get("position", "unknown")
                    value = value_info.get("value")
                    
                    source_desc = {
                        "A": "Victim's (Pool A)",
                        "B": "Victim's (Pool B - pre-last-step)",
                        "C": "Attacker's (Pool C)"
                    }.get(source, source)
                    
                    detail = f"{alias}={value} in {position} (from {source_desc})"
                    injection_details.append(detail)
                    
                    # 识别目标参数和非目标参数
                    if source in ("A", "B"):  # Victim's value
                        target_param_info = f"{alias}={value} in {position}"
                        target_value = value
                    elif source == "C":  # Attacker's value
                        non_target_param_info = f"{alias}={value} in {position}"
                        non_target_value = value
                
                # 构造判断标准说明
                judgment_criteria = (
                    f"**CRITICAL JUDGMENT**: Check which value appears in the RESPONSE:\n"
                    f"  - If response contains the Victim's value ({target_value}), injection SUCCEEDED → BOLA Found\n"
                    f"  - If response contains the Attacker's own value ({non_target_value}), injection FAILED → No BOLA\n"
                )
                
                description = (
                    f"In this test ({strategy_desc} - One-Hot Injection), the Attacker injected "
                    f"the SAME parameter '{param_name}' with DIFFERENT values in MULTIPLE locations:\n"
                    f"  - Target (Victim's value): {target_param_info}\n"
                    f"  - Non-Target (Attacker's value): {non_target_param_info}\n\n"
                    f"{judgment_criteria}\n"
                    f"Endpoint: {api_key}\n"
                    f"Operation: {operation}\n"
                    f"All injected parameters: {', '.join(injection_details)}"
                )
            elif case_type == "container_boundary":
                # 容器边界测试描述
                non_target_source = test_meta.get("non_target_source", "D")
                comparison_source = test_meta.get("comparison_source", "E")
                description = (
                    f"In this test ({strategy_desc}), the Attacker attempted to access resources "
                    f"from Container B using Container A's context at endpoint {api_key}. "
                    f"The test used {param_name}={param_value} from Pool C (Container A) "
                    f"with other parameters from Pool {non_target_source} (Container B). "
                    f"This tests whether the system properly validates container-resource ownership."
                )
            else:
                # 常规BOLA测试描述
                description = (
                    f"In this test ({strategy_desc}), the Attacker used their own account credentials "
                    f"to access the endpoint {api_key} and {operation} the Victim's resource "
                    f"with {param_name}={param_value}."
                )
            
            return description

        def _compare_response_params(current_param_name,data_params, test_params, data_step, test_step, data_request_params, test_request_params, type_tag=None, evidence_data=None, test_meta=None):
            """比较响应参数"""
            # === 强 gating：不可执行/非200 直接短路（不进入 LLM） ===
            data_exec = (data_step or {}).get("execution_status", {}) if isinstance(data_step, dict) else {}
            test_exec = (test_step or {}).get("execution_status", {}) if isinstance(test_step, dict) else {}
            data_status = (data_exec or {}).get("status")
            test_status = (test_exec or {}).get("status")
            data_sc = (data_exec or {}).get("status_code")
            test_sc = (test_exec or {}).get("status_code")
            # 获取请求路由和方法
            data_route = data_step.get("route", "")
            data_method = data_step.get("method", "")
            data_route_type = data_step.get("type", "")
            # request_parameters
            api_key = f"{data_method}:{data_route}"
            if data_status == "unexecutable":
                return {api_key: {"conclusion": "Skipped / Not Executable", "reason": (data_exec or {}).get("reason", "data_account not executable")}}
            if test_status == "unexecutable":
                return {api_key: {"conclusion": "Skipped / Not Executable", "reason": (test_exec or {}).get("reason", "test_account not executable")}}
            if isinstance(test_sc, int) and test_sc != 200:
                return {api_key: {"conclusion": "BOLA Not Found", "reason": f"attacker_status_code={test_sc}"}}
            if isinstance(data_sc, int) and data_sc >= 400:
                return {api_key: {"conclusion": "BOLA Not Found", "reason": f"victim_status_code={data_sc}"}}
            if not data_params or not test_params:
                return None
            # 比较参数名是否一致
            data_param_names = set(data_params.keys())
            test_param_names = set(test_params.keys())
            # 检查参数名是否一致
            if data_param_names != test_param_names:
                return {
                    api_key: {
                        "conclusion": "BOLA Not Found",
                        "reason": "Response Parameter Mismatch"
                    }
                }
            # 检查test_account的值是否为空
            has_non_empty_values = False
            for _, param_value in test_params.items():
                if param_value is not None and param_value != "" and param_value != [] and param_value != {}:
                    has_non_empty_values = True
                    break
    
            # 生成自然语言描述
            test_description = _generate_test_description(test_meta, api_key)
            
            # 判断是否为容器边界测试
            is_container_boundary = (test_meta or {}).get("case_type") == "container_boundary"
            comparison_source = (test_meta or {}).get("comparison_source", "A")
    
            comparison_dict = {
                "test_description": test_description,
                "current_param_name":current_param_name,
                "routes_type": data_route_type,
                "route_name": api_key,
                "test_results": str({
                    "request_params": str(test_request_params),
                    "response_params": str(test_params)
                }),
                "data_results": str({
                    "request_params": str(data_request_params),
                    "response_params": str(data_params)
                }),
                "evidence_data": str(evidence_data),
                "test_meta": test_meta or {},
                "is_container_boundary": is_container_boundary,  # 标记是否为容器边界测试
                "comparison_source": comparison_source  # 对照组Pool来源
            }
            if has_non_empty_values:
                # 使用 self.llm_dict 的线程独立副本，避免共享状态导致的串行化或数据污染
                import copy as _copy
                try:
                    local_llm_dict = _copy.deepcopy(self.llm_dict)
                except Exception:
                    local_llm_dict = dict(self.llm_dict) if isinstance(self.llm_dict, dict) else {}
                local_llm_dict = comparison_dict
                bola_results = None
                bola_reason = ""
                if not _llm_disabled:
                    _last_err = None
                    for _attempt in range(1, _llm_max_retries + 1):
                        try:
                            # 根据测试类型选择合适的LLM prompt
                            case_type = (test_meta or {}).get("case_type", "")
                            if case_type == "container_boundary":
                                # 容器边界测试使用ou_id的判定逻辑（同一用户跨容器）
                                tmp_result_params = self.gpt_reply.getreply(
                                    self.syn_prompt.synthesis_prompt("ou_id_private_data_judgement", local_llm_dict)
                                )
                            elif str(type_tag).strip().lower() == "ou_id":
                                tmp_result_params = self.gpt_reply.getreply(
                                    self.syn_prompt.synthesis_prompt("ou_id_private_data_judgement", local_llm_dict)
                                )
                            else:
                                tmp_result_params = self.gpt_reply.getreply(
                                    self.syn_prompt.synthesis_prompt("resource_id_private_data_judgement", local_llm_dict)
                                )
                            final_reuslts = eval(self.jsontool.list_formatting(tmp_result_params))
                            res = str(final_reuslts.get("results", "")).upper()
                            if "YES" in res:
                                bola_results = "BOLA Found"
                                bola_reason = final_reuslts.get("reason", "")
                                break
                            elif "NO" in res:
                                bola_results = "BOLA Not Found (middle)"
                                bola_reason = final_reuslts.get("reason", "")
                                break
                            elif "UNSURE" in res:
                                bola_results = "BOLA Not Found (high)"
                                bola_reason = final_reuslts.get("reason", "")
                                break
                        except Exception as e:
                            _last_err = e
                            logger.info(f"LLM 判定异常（第{_attempt}/{_llm_max_retries}次）: {str(e)}")
                    if bola_results is None:
                        if _llm_fail_policy == 'aggressive':
                            bola_results = "Potential BOLA"
                            bola_reason = "LLM failed, applying aggressive fallback"
                        else:
                            bola_results = "BOLA Not Found (middle)"
                            bola_reason = "LLM failed, applying lenient fallback"
                else:
                    if _llm_fail_policy == 'aggressive':
                        bola_results = "Potential BOLA"
                        bola_reason = "LLM disabled, aggressive fallback"
                    else:
                        bola_results = "BOLA Not Found (middle)"
                        bola_reason = "LLM disabled, lenient fallback"
                # 当判定为“存在越权”时，追加记录 data/test 的接口信息与请求/响应的具体值
                out = {
                    api_key: {
                        "conclusion": bola_results,
                        "reason": bola_reason
                    }
                }
                try:
                    if isinstance(test_meta, dict):
                        out[api_key]["test_type"] = {
                            "category": test_meta.get("category"),
                            "case_type": test_meta.get("case_type"),
                            "position_mode": test_meta.get("position_mode"),
                            "value_source": test_meta.get("value_source"),
                            "strategy": test_meta.get("strategy"),
                            "group_name": test_meta.get("group_name"),
                            "param_name": test_meta.get("param_name"),
                            "target_position": test_meta.get("target_position"),  # one-hot测试的目标位置
                            "param_sources": test_meta.get("param_sources"),
                            "param_values": test_meta.get("param_values"),
                            "param_alias_sources": test_meta.get("param_alias_sources"),
                            "param_alias_values": test_meta.get("param_alias_values")
                        }
                except Exception:
                    pass
                if bola_results == "BOLA Found":
                    out[api_key]["api_info"] = {
                        "data": {
                            "method": data_step.get("method", ""),
                            "route": data_step.get("route", "")
                        },
                        "test": {
                            "method": test_step.get("method", ""),
                            "route": test_step.get("route", "")
                        }
                    }
                    out[api_key]["details"] = {
                        "data": {
                            "request_params": data_request_params,
                            "response_params": data_params
                        },
                        "test": {
                            "request_params": test_request_params,
                            "response_params": test_params
                        }
                    }
                return out
            else:
                return {
                    api_key: {
                        "conclusion": "Potential BOLA",
                        "reason": "Response parameters match but test_account values are empty"
                    }
                }

        def _aliases_for_group(group_name: str, param_name: str) -> List[str]:
            aliases = [param_name]
            try:
                proc = self.normalized_params_process_data
                if isinstance(proc, list):
                    for item in proc:
                        if not isinstance(item, dict):
                            continue
                        if item.get("group") != group_name:
                            continue
                        for d in item.get("data", []) or []:
                            params_name = d.get("parameters_name", {}) or {}
                            keep_pra = params_name.get("keep_pra")
                            repl_list = params_name.get("replace_para", []) or []
                            if not isinstance(repl_list, list):
                                repl_list = [repl_list] if repl_list else []
                            if param_name in repl_list and keep_pra:
                                return [keep_pra, param_name] + [x for x in repl_list if x != param_name]
                            if param_name == keep_pra and repl_list:
                                return [param_name] + list(repl_list)
            except Exception:
                pass
            return aliases

        def _is_container_param_for(group_name: str, param_name: str, type_tag: str) -> bool:
            """判断参数是否为容器参数（ou_id）。
            
            注意：只有在ou_id映射中的参数才是真正的容器参数。
            resource_id映射中的参数是普通资源标识符，不是容器参数。
            """
            try:
                # 只有type_tag为"ou_id"时，才可能是容器参数
                if type_tag != "ou_id":
                    return False
                aliases = _aliases_for_group(group_name, param_name)
                for a in aliases:
                    if self.is_container_param("ou_id", group_name, a):
                        return True
            except Exception:
                return False
            return False

        def _process_chain_list(chain_list, current_param_name, group_name, type_tag, is_container_param_flag: bool):
            """处理一个链条列表，输出去重后的判断结果列表（并行处理每个链条项）"""
            results = []
            if not isinstance(chain_list, list) or not chain_list:
                return results

            def _process_one(execution_dict, current_param_name):
                if not isinstance(execution_dict, dict):
                    return None

                # 支持容器边界测试的结果结构
                test_meta = execution_dict.get("test_meta", {})
                is_container_boundary_test = (test_meta or {}).get("case_type") == "container_boundary"
                
                if is_container_boundary_test:
                    # 容器边界测试：使用control_group和test_group
                    data_account_results = execution_dict.get("control_group", {})
                    test_account_results = execution_dict.get("test_group", {})
                else:
                    # 常规BOLA测试：使用data_account和test_account
                    data_account_results = execution_dict.get("data_account", {})
                    test_account_results = execution_dict.get("test_account", {})
                
                is_container_param = bool(is_container_param_flag)
                if not data_account_results or not test_account_results:
                    return None
                evidence_seq = execution_dict.get("evidence", {}) if isinstance(execution_dict.get("evidence"), dict) else {}
                test_meta = execution_dict.get("test_meta")
                if not is_container_param:
                    # 原逻辑：仅比较最后一步
                    data_last_step = _get_last_step(data_account_results)
                    test_last_step = _get_last_step(test_account_results)
                    if not data_last_step or not test_last_step:
                        return None
                    # 保障同一接口对比：请求方式与请求路由必须一致，不一致直接跳过
                    if not _routes_match(data_last_step, test_last_step):
                        return None
                    data_prev_step = _get_prev_step(data_account_results)
                    test_prev_step = _get_prev_step(test_account_results)
                    last_type = _get_api_type_for_step(test_last_step, group_name)
                    prev_type = _get_api_type_for_step(test_prev_step, group_name) if test_prev_step else None

                    # 资源参数规则：
                    # - 若最后一步为 query 且倒数第二步为 update/delete，则不比较最后的 query
                    # - 若最后一步为 query 且倒数第二步不是 update/delete，则证据设为空，比较最后的 query
                    if _is_query_type(last_type) and _is_update_or_delete(prev_type):
                        return None

                    evidence_data = evidence_seq if isinstance(evidence_seq, dict) else None
                    data_step_for_cmp = data_last_step
                    test_step_for_cmp = test_last_step

                    data_response_params = _extract_response_params(data_step_for_cmp)
                    test_response_params = _extract_response_params(test_step_for_cmp)
                    data_request_params = _extract_request_params(data_step_for_cmp)
                    test_request_params = _extract_request_params(test_step_for_cmp)
                    return _compare_response_params(
                        current_param_name,
                        data_response_params,
                        test_response_params,
                        data_step_for_cmp,
                        test_step_for_cmp,
                        data_request_params,
                        test_request_params,
                        type_tag=type_tag,
                        evidence_data=evidence_data,
                        test_meta=test_meta
                    )
                else:
                    # 容器参数：递归处理嵌套的步骤结构；每一层都跳过第一个序号，仅比较后续公共序号对应的真实接口项
                    def _numeric_keys(d):
                        if not isinstance(d, dict):
                            return []
                        return sorted([int(k) for k in d.keys() if str(k).isdigit()])

                    def _is_step_obj(d):
                        return isinstance(d, dict) and (
                            ("route" in d) or ("request_params" in d) or ("method" in d) or ("execution_status" in d)
                        )

                    def _yield_pairs(dr, tr):
                        pairs = []
                        common = sorted(set(_numeric_keys(dr)).intersection(_numeric_keys(tr)))
                        if not common:
                            return pairs
                        first_num = min(common)
                        # 先处理本层的第一个序号：如果是嵌套容器，递归进去并在其子层继续跳过该层的第一个序号
                        first_sid = str(first_num)
                        d_first = dr.get(first_sid)
                        t_first = tr.get(first_sid)
                        if isinstance(d_first, dict) and isinstance(t_first, dict) and not _is_step_obj(d_first):
                            pairs.extend(_yield_pairs(d_first, t_first))
                        # 再处理剩余公共序号：如果是接口对象直接加入，否则继续递归深入
                        for sid in [str(s) for s in common if s != first_num]:
                            ds = dr.get(sid)
                            ts = tr.get(sid)
                            if not isinstance(ds, dict) or not isinstance(ts, dict):
                                continue
                            if _is_step_obj(ds) and _is_step_obj(ts):
                                pairs.append((ds, ts))
                            else:
                                # 继续深入嵌套，按照相同规则处理
                                pairs.extend(_yield_pairs(ds, ts))
                        return pairs

                    comparison_results = []
                    # 计算链级别的 evidence_data：若最后一步为 query，则取其请求与响应；若倒数第二步为 update/delete，则最后一步不参与对比
                    data_last_step = _get_last_step(data_account_results)
                    test_last_step = _get_last_step(test_account_results)
                    data_prev_step = _get_prev_step(data_account_results)
                    test_prev_step = _get_prev_step(test_account_results)
                    last_type = _get_api_type_for_step(test_last_step, group_name)
                    prev_type = _get_api_type_for_step(test_prev_step, group_name) if test_prev_step else None

                    chain_evidence_data = evidence_seq if isinstance(evidence_seq, dict) else None
                    exclude_last = False
                    if _is_query_type(last_type):
                        if _is_update_or_delete(prev_type):
                            exclude_last = True
                    # if current_param_name =="userId":
                    #     print("a")
                    aliases = _aliases_for_group(group_name, current_param_name)
                    for data_step, test_step in _yield_pairs(data_account_results, test_account_results):
                        # 选择测试请求中包含当前参数的步骤（支持 body/query/path）
                        test_request_params = _extract_request_params(test_step)
                        body_json = test_request_params.get("json", {}) if isinstance(test_request_params.get("json", {}), dict) else {}
                        body_form = test_request_params.get("data", {}) if isinstance(test_request_params.get("data", {}), dict) else {}
                        query_params = test_request_params.get("params", {}) if isinstance(test_request_params.get("params", {}), dict) else {}
                        route = (test_step.get("route", "") or "")
                        has_body = any((a in body_json) or (a in body_form) for a in aliases)
                        has_query = any(a in query_params for a in aliases)
                        has_path = any((f"{{{a}}}" in route) or (f"/{a}" in route) for a in aliases)
                        if not (has_body or has_query or has_path):
                            continue
                        # 若需要排除最后的 query 步骤，则在此处跳过
                        if exclude_last and isinstance(data_last_step, dict) and isinstance(test_last_step, dict):
                            if data_step is data_last_step and test_step is test_last_step:
                                continue
                        # 保障同一接口对比：请求方式与请求路由必须一致
                        if not _routes_match(data_step, test_step):
                            continue
                        # 对匹配步骤进行比较
                        data_response_params = _extract_response_params(data_step)
                        test_response_params = _extract_response_params(test_step)
                        data_request_params = _extract_request_params(data_step)
                        cmp_res = _compare_response_params(
                            current_param_name,
                            data_response_params,
                            test_response_params,
                            data_step,
                            test_step,
                            data_request_params,
                            test_request_params,
                            type_tag=type_tag,
                            evidence_data=chain_evidence_data,
                            test_meta=test_meta
                        )
                        if cmp_res:
                            comparison_results.append(cmp_res)
                    return comparison_results or None

            # 并发线程上限可配置：默认 16，受 BOLASCAN_JUDGE_WORKERS 控制
            try:
                judge_cap = int(os.getenv('BOLASCAN_JUDGE_WORKERS', '16'))
            except Exception:
                judge_cap = 16
            max_workers = max(1, min(judge_cap, len(chain_list)))
            # max_workers = 1
            # 聚合所有链条的比较结果，按 api_key 收集
            results_by_api = {}
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(_process_one, execution_dict, current_param_name) for execution_dict in chain_list]
                for fut in as_completed(futures):
                    comparison_result = fut.result()
                    try:
                        _increment_progress(group_name)
                    except Exception:
                        pass
                    if not comparison_result:
                        continue
                    if isinstance(comparison_result, list):
                        for cmp_res in comparison_result:
                            if not isinstance(cmp_res, dict) or not cmp_res:
                                continue
                            api_key = next(iter(cmp_res.keys()))
                            entry = cmp_res.get(api_key, {})
                            if api_key not in results_by_api:
                                results_by_api[api_key] = []
                            results_by_api[api_key].append(entry)
                    elif isinstance(comparison_result, dict):
                        api_key = next(iter(comparison_result.keys()))
                        entry = comparison_result.get(api_key, {})
                        if api_key not in results_by_api:
                            results_by_api[api_key] = []
                        results_by_api[api_key].append(entry)

            # 优先级映射：BOLA Found > Potential BOLA > BOLA Not Found (middle) > BOLA Not Found
            priority_map = {
                "BOLA Found": 3,
                "Potential BOLA": 2,
                "BOLA Not Found (middle)": 1,
                "BOLA Not Found (high)": 0,
                "BOLA Not Found": 0,
                "Skipped / Not Executable": -1
            }

            # 对同一 api_key 的结果进行去重：仅保留"conclusion"不重复的条目
            for api_key, entries in results_by_api.items():
                # 选择最高优先级的主结果，并且每种"conclusion"仅保留一次
                def _entry_priority(e):
                    return priority_map.get(e.get("conclusion", ""), -1)

                kept_conclusions = set()
                best_entry = None
                best_pri = -1
                # 找到优先级最高的条目
                for e in entries:
                    pri = _entry_priority(e)
                    if pri > best_pri:
                        best_entry = e
                        best_pri = pri
                # 先加入最高优先级条目
                if best_entry is not None:
                    results.append({api_key: best_entry})
                    kc = best_entry.get("conclusion", "")
                    if kc:
                        kept_conclusions.add(kc)
                # 再加入剩余条目中"conclusion"未被加入过的（保证每个接口仅保留不同结论一次）
                for e in entries:
                    if best_entry is not None and e is best_entry:
                        continue
                    concl = e.get("conclusion", "")
                    if not concl or concl in kept_conclusions:
                        continue
                    kept_conclusions.add(concl)
                    results.append({api_key: e})

            return results

        result = {}
        # 并发处理每个功能组，避免共享写竞争，单线程归并到 result
        def _process_group(item, category_tag: str):
            group_name, group_data = item
            group_out = {}
            # 遍历每个参数名（例如 goodsId 等）
            for param_name, param_bucket in group_data.items():
                # 初始化输出结构，适配 cross/group 两类
                group_out[param_name] = {"cross": [], "group": []}
                type_tag = (category_tag or "resource_id")
                is_container = _is_container_param_for(group_name, param_name, type_tag)
                # 新结构：param_bucket 为 {"cross": [...], "group": [...]}；旧结构可能为 list
                if isinstance(param_bucket, dict):
                    for bucket_key in ["cross", "group"]:
                        chain_list = param_bucket.get(bucket_key, [])
                        if isinstance(chain_list, list):
                            # 传递 type_tag 以驱动后续 LLM 判定与 evidence_data 行为
                            group_out[param_name][bucket_key] = _process_chain_list(chain_list, param_name, group_name, type_tag, is_container)
                elif isinstance(param_bucket, list):
                    # 兼容旧结构，将其视为 cross
                    group_out[param_name]["cross"] = _process_chain_list(param_bucket, param_name, group_name, type_tag, is_container)
            return group_name, group_out

        if isinstance(execution_results, dict) and execution_results:
            # 顶层结构可能包含 resource_id/ou_id 分类；若存在则分别处理各类别的功能组
            has_category = ("resource_id" in execution_results) or ("ou_id" in execution_results)
            try:
                from concurrent.futures import ThreadPoolExecutor
            except Exception:
                ThreadPoolExecutor = None

            if has_category:
                for category in ["resource_id","ou_id"]:
                    cat_data = execution_results.get(category, {})
                    if not isinstance(cat_data, dict) or not cat_data:
                        continue
                    category_out = {}
                    if ThreadPoolExecutor:
                        try:
                            group_cap = int(os.getenv('BOLASCAN_GROUP_WORKERS', '8'))
                        except Exception:
                            group_cap = 8
                        max_workers = max(1, min(group_cap, len(cat_data)))
                        # max_workers = 1
                        with ThreadPoolExecutor(max_workers=max_workers) as executor:
                            futures = [executor.submit(_process_group, (gn, gd), category) for gn, gd in cat_data.items()]
                            for fut in as_completed(futures):
                                gn, gout = fut.result()
                                category_out[gn] = gout
                    else:
                        # 退化为串行
                        for gn, gd in cat_data.items():
                            gn2, gout2 = _process_group((gn, gd), category)
                            category_out[gn2] = gout2
                    result[category] = category_out
            else:
                # 旧结构：顶层直接为功能组
                if ThreadPoolExecutor:
                    try:
                        group_cap = int(os.getenv('BOLASCAN_GROUP_WORKERS', '8'))
                    except Exception:
                        group_cap = 8
                    max_workers = max(1, min(group_cap, len(execution_results)))
                    with ThreadPoolExecutor(max_workers=max_workers) as executor:
                        futures = [executor.submit(_process_group, (gn, gd), "resource_id") for gn, gd in execution_results.items()]
                        for fut in as_completed(futures):
                            gn, gout = fut.result()
                            result[gn] = gout
                else:
                    # 退化为串行
                    for gn, gd in execution_results.items():
                        gn2, gout2 = _process_group((gn, gd), "resource_id")
                        result[gn2] = gout2
        else:
            # 保持原有逻辑（若结构异常）
            for group_name, group_data in execution_results.items():
                result[group_name] = {}
                # 遍历每个参数名（例如 goodsId 等）
                for param_name, param_bucket in group_data.items():
                    # 初始化输出结构，适配 cross/group 两类
                    result[group_name][param_name] = {"cross": [], "group": []}
                    # 新结构：param_bucket 为 {"cross": [...], "group": [...]}；旧结构可能为 list
                    if isinstance(param_bucket, dict):
                        for bucket_key in ["cross", "group"]:
                            chain_list = param_bucket.get(bucket_key, [])
                            if isinstance(chain_list, list):
                                result[group_name][param_name][bucket_key] = _process_chain_list(chain_list, param_name, group_name, "resource_id", _is_container_param_for(group_name, param_name, "resource_id"))
                    elif isinstance(param_bucket, list):
                        # 兼容旧结构，将其视为 cross
                        result[group_name][param_name]["cross"] = _process_chain_list(param_bucket, param_name, group_name, "resource_id", _is_container_param_for(group_name, param_name, "resource_id"))
        try:
            self.jsontool.write_json(progress_path, progress)
        except Exception:
            pass
        return result


    
    def horizontal_bola_workflow(self,url,acount_dict):
        """
        BOLA水平权限测试的主工作流
        
        流程：
        1. 识别资源参数（data_resource）
        2. 识别容器参数（data_container_resource）
        3. 生成资源包（resource_package_generation）
        4. 生成依赖链包（dependency_chain_package_generation）
        5. 执行测试（execution_packages）
        6. 判断漏洞（bola_vul_judgement）
        """
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
        horizontal_results_dir = os.path.join(project_root, 'cache', self.project_name, 'horizontal_results')
        
        data_resource_id_result = self.data_resource()

        self.jsontool.write_json(os.path.join(horizontal_results_dir, "data_resource_id_result.json"), data_resource_id_result)
        container_reoust_id_result = self.data_container_resource()
        # print(container_reoust_id_result)
        # 提取并缓存容器参数名集合，供后续 bola_vul_judgement 使用
        try:
            if isinstance(container_reoust_id_result, dict):
                self.container_param_set = set(container_reoust_id_result.keys())
            elif isinstance(container_reoust_id_result, list):
                _ps = set()
                for item in container_reoust_id_result:
                    if isinstance(item, dict):
                        for _, params in item.items():
                            if isinstance(params, list):
                                for p in params:
                                    _ps.add(p)
                    elif isinstance(item, str):
                        _ps.add(item)
                self.container_param_set = _ps
            else:
                self.container_param_set = set()
        except Exception:
            self.container_param_set = set()
        self.jsontool.write_json(os.path.join(horizontal_results_dir, "container_reoust_id_result.json"), container_reoust_id_result)
        logger.info(f"container_reoust_id_result:{container_reoust_id_result}")
        # data_resource_id_result = self.jsontool.read_json(os.path.join(horizontal_results_dir, "data_resource_id_result.json"))
        # container_reoust_id_result = self.jsontool.read_json(os.path.join(horizontal_results_dir, "container_reoust_id_result.json"))
        
        container_resource_divide_results = self.resource_package_generation(data_resource_id_result,container_reoust_id_result)
        logger.info(f"container_resource_divide_results:{container_resource_divide_results}")
        self.jsontool.write_json(os.path.join(horizontal_results_dir, "container_resource_divide_results.json"), container_resource_divide_results)
        # 缓存容器参数映射：按 route(group前缀)→param 集合精确定义（供执行/判定使用）
        try:
            self.container_params_by_group = self.build_container_params_by_group(container_resource_divide_results)
        except Exception:
            self.container_params_by_group = {"ou_id": {}, "resource_id": {}}
        # container_resource_divide_results = self.jsontool.read_json(os.path.join(horizontal_results_dir, "container_resource_divide_results.json"))
        dependency_execution_reoutes_packages = self.dependency_chain_package_generation(container_resource_divide_results)
        # 序列化后再写入 JSON
        self.jsontool.write_json(
            os.path.join(horizontal_results_dir, "dependency_execution_reoutes_packages.json"),
            make_json_serializable(dependency_execution_reoutes_packages)
        )
        logger.info(f"dependency_execution_reoutes_packages finished")
        # dependency_execution_reoutes_packages = self.jsontool.read_json(os.path.join(horizontal_results_dir, "dependency_execution_reoutes_packages.json"))
        all_acount_execution_results = self.execution_packages(url,acount_dict,dependency_execution_reoutes_packages,container_resource_divide_results)
        logger.info(f"all_acount_execution_results finished")
        # 序列化后再写入 JSON（关键修复：避免 BytesIO 无法序列化）
        serializable_results = make_json_serializable(all_acount_execution_results)
        self.jsontool.write_json(
            os.path.join(horizontal_results_dir, "all_acount_execution_results.json"),
            serializable_results
        )

        # all_acount_execution_results = self.jsontool.read_json(os.path.join(horizontal_results_dir, "all_acount_execution_results.json"))

        vulnerability_results = self.bola_vul_judgement(serializable_results)
        # vulnerability_results = {}

        return make_json_serializable(vulnerability_results)
    

if __name__ == "__main__":
    jsontools = JsonTools()
    project_name = "crapi"
    
    # 获取项目根目录
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
    cache_dir = os.path.join(project_root, 'cache', project_name)
    
    case_generation_results_packages = jsontools.read_json(os.path.join(cache_dir, "create_request_data_packages_results.json"))
    params_dict = jsontools.read_json(os.path.join(cache_dir, "parameters_dict_all.json"))
    true_params = jsontools.read_json(os.path.join(cache_dir, "api_doc_with_type.json"))
    horiontest = HorizontalVuln("gpt-4o-mini", params_dict, case_generation_results_packages, project_name, true_params)
    
    # 配置测试参数（请根据实际情况修改）
    url = "http://your-target-app-url:port/"
    auth_type = {
        "test_account": {
            "auth": {
                "authorization": "Bearer <your-test-account-token>"
            }
        },
        "data_account": {
            "auth": {
                "authorization": "Bearer <your-data-account-token>"
            }
        }
    }
    jsontools.write_json(os.path.join(cache_dir, "bola_horizontal_results.json"), horiontest.horizontal_bola_workflow(url, auth_type))


    

# todo:对于ou_id来说，需要从add接口开始测试，要注意一下这点

# 都使用别人的也不太合适，应该是只有那个参数id用的是别人的，
