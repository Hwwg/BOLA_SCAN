"""
资源识别模块
负责识别API中的资源参数和容器参数
"""
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, Set, List, Optional
from .utils_helpers import flatten_list

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
    
    def data_resource(self) -> Dict[str, Any]:
        """
        计算A交B-（A-B）的参数，其中A是请求参数，B是响应参数
        返回按功能组划分的结果
        计算这个集合的结果是发现有多少资源id，但其中可能也是容器资源id
        使用多线程并行处理各个功能组
        
        Returns:
            按功能组划分的资源参数识别结果
        """
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
                    group_name, result_params = future.result()
                    if result_params:
                        result[group_name] = result_params
                    logger.info(f"功能组 {group_name} 处理完成")
                except Exception as e:
                    logger.error(f"功能组 {group_name} 处理失败: {str(e)}")
        
        return result

    def data_container_resource(self) -> Dict[str, Any]:
        """
        找出同时出现在多个功能组中的请求参数
        返回格式: {"参数名": [{"group_name": ["路由一", "路由二"]}]}
        
        Returns:
            容器资源参数识别结果
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
                        request_params_iter = flatten_list(request_params)
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


