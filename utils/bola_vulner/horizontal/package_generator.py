"""
包生成模块
负责生成资源包和依赖链包
"""
import logging
import os
import copy
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, List, Set, Optional
from .utils_helpers import flatten_list, parse_method_route_key, is_step_object
from utils.param_path import find_matching_path, param_matches

logger = logging.getLogger(__name__)

# LLM 控制配置（可通过环境变量调整）
try:
    _llm_max_retries = int(os.getenv('BOLASCAN_LLM_MAX_RETRIES', '3'))
except Exception:
    _llm_max_retries = 3
_llm_disabled = str(os.getenv('BOLASCAN_DISABLE_LLM', 'false')).lower() in ('1', 'true', 'yes', 'on')


class PackageGenerator:
    """资源包和依赖链包生成器"""
    
    def __init__(self, normalized_params, true_params, case_generation_results_packages,
                 gpt_reply, syn_prompt, jsontool, llm_dict):
        """
        初始化包生成器
        
        Args:
            normalized_params: 规范化的参数数据
            true_params: API文档数据
            case_generation_results_packages: 用例生成结果包
            gpt_reply: GPT回复对象
            syn_prompt: 提示词生成对象
            jsontool: JSON工具对象
            llm_dict: LLM字典配置
        """
        self.normalized_params = normalized_params
        self.true_params = true_params
        self.case_generation_results_packages = case_generation_results_packages
        self.gpt_reply = gpt_reply
        self.syn_prompt = syn_prompt
        self.jsontool = jsontool
        self.llm_dict = llm_dict
    
    def resource_package_generation(self, data_resource_id_result: Dict, 
                                   container_resource_id_result: Any) -> Dict[str, Any]:
        """
        生成容器类型id以及资源id，分别列一下
        输出结构与 dependency_chain_package_generation 期望保持一致：
        {
            "ou_id": [{"group_name": ["参数A", "参数B"]}, ...],
            "resource_id": [{"group_name": ["资源id_1", "资源id_2"]}, ...]
        }
        
        Args:
            data_resource_id_result: 资源识别结果
            container_resource_id_result: 容器资源识别结果
            
        Returns:
            包含资源和容器参数的包结构
        """
        def no_cross_resource_id(resource_packages):
            """使用LLM过滤跨组资源"""
            try:
                local_llm_dict = copy.deepcopy(self.llm_dict)
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
                        request_params_iter = flatten_list(request_params)
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
    
    def dependency_chain_package_generation(self, resource_id_dict: Dict) -> Dict[str, Any]:
        """
        首先区分好普通资源id和资源容器id,需要先构建好每个id验证的执行链
        不再局限于功能组匹配，遍历所有依赖链条，只要最后一个步骤包含指定资源id就纳入结果
        
        Args:
            resource_id_dict: 资源ID字典，包含ou_id和resource_id两类
            
        Returns:
            依赖链包结构
        """
        def _contains_resource_id(request_params, resource_id, step_data=None):
            """
            检查请求参数中是否包含指定的资源id
            只检查请求参数（路径参数、查询参数、请求体参数），不检查响应参数
            """
            if not isinstance(request_params, dict):
                return False
            
            parameters = request_params.get("parameters", {})
            
            # 检查请求路由模板中是否包含路径参数（如{postId}、{video_id}）
            if step_data and isinstance(step_data, dict):
                route = step_data.get("route", "")
                if f"{{{resource_id}}}" in route:
                    return True
            
            # 检查URL查询参数中是否包含资源id（仅匹配相同键名）
            url = parameters.get("url", "")
            if "?" in url:
                query_part = url.split("?", 1)[1]
                pairs = [seg.split("=", 1)[0] for seg in query_part.split("&")]
                if resource_id in pairs:
                    return True
            
            # 检查json请求体参数中是否包含资源id（仅匹配相同键名）
            json_data = parameters.get("json", {})
            if isinstance(json_data, dict):
                if resource_id in json_data or find_matching_path(json_data, [resource_id]):
                    return True
                    
            # 检查form表单参数中是否包含资源id（仅匹配相同键名）
            form_data = parameters.get("data", {})
            if isinstance(form_data, dict):
                if resource_id in form_data or find_matching_path(form_data, [resource_id]):
                    return True
            
            # 检查 params 查询参数字段中是否包含资源id（仅匹配键名）
            params_data = parameters.get("params", {})
            if isinstance(params_data, dict):
                if resource_id in params_data or any(param_matches(resource_id, key) for key in params_data.keys()):
                    return True
                        
            return False
        
        def _extract_api_steps_from_container(container):
            """递归提取一个步骤容器中的所有接口字典"""
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
        
        def _flatten_chain_steps(chain):
            """将链条按顶层数字步骤键展开，每个步骤得到其包含的所有接口"""
            step_keys = sorted([k for k in chain.keys() if k.isdigit()], key=lambda x: int(x))
            steps = []
            for step_key in step_keys:
                step_container = chain.get(step_key)
                step_apis = _extract_api_steps_from_container(step_container)
                steps.append(step_apis)
            return steps
        
        def _api_belongs_to_group(api_step, group_name):
            """检查接口是否属于指定功能组（基于 api_doc_data 的 "METHOD /route" 键）"""
            try:
                group_map = api_doc_data.get(group_name, {})
            except Exception:
                group_map = {}
            if not isinstance(group_map, dict) or not group_map:
                return False
            method = (api_step.get("method") or "").upper()
            route = api_step.get("route") or ""
            if not route:
                return False

            # 优先尝试标准化匹配：从文档键中解析出 (METHOD, ROUTE)
            def parse_key(key):
                if not isinstance(key, str):
                    return None, None, key
                s = key.strip()
                # 将不同分隔符统一为一个空格
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
        dependency_chain = self.case_generation_results_packages
        
        # 过滤参数集合，命中过滤名单的参数仅在自身功能组内搜索依赖链
        filtered_params_set = set(resource_id_dict.get("filterd_resource_id", [])) if isinstance(resource_id_dict, dict) else set()
  
        def _process_group_entry(kind, group_name, resource_ids):
            """处理单个功能组的依赖链生成"""
            local_result = {kind: {group_name: {}}}
            for resource_id in (resource_ids or []):
                local_result[kind][group_name].setdefault(resource_id, {"cross": [], "group": []})
                
                # 处理 cross 结构
                cross_groups_iter = dependency_chain.get("cross", {})
                for chain_group_name, chains in cross_groups_iter.items():
                    for chain in chains:
                        steps = _flatten_chain_steps(chain)
                        if kind == "ou_id":
                            # ou_id：只要链中任意步骤的接口请求参数包含该参数，就纳入结果
                            found_resource_id = any(
                                _contains_resource_id(api_step.get("request_params", {}), resource_id, api_step)
                                for step_apis in steps for api_step in step_apis
                            )
                            last_step_apis = steps[-1] if steps else []
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
                            last_step_apis = steps[-1] if steps else []
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
            futures = [executor.submit(_process_group_entry, kind, group_name, resource_ids) 
                      for (kind, group_name, resource_ids) in tasks]
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

