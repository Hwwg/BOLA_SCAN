import logging
# from scripts.api_doc import ApiDoc
from scripts.jsontools import JsonTools
import itertools
import json
from prompt.synthesis_prompt import SyntheticPrompt
from gptreply.gpt_con import GPTReply

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import copy
import sys,os

# 配置全局日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

"""
return {
            "parameters_extraction_results":parameters_extraction_results,
            "normalized_params":normalized_params,
            "normalized_params_process_data":normalized_params_process_data,
            "set_calculate_results_v1":set_calculate_results_v1,
            "set_calculate_results_v2":set_calculate_results_v2,
            "set_calculate_results_all":set_calculate_results_all,
            "set_calculate_results_v3":set_calculate_results_v3
        }
"""
class DependencyChain:
    def __init__(self,doc_data,model_name, params_dict,project_name) -> None:
        # logger.info(f"初始化DependencyChain，文档路径: {doc_path}, 模型: {model_name}")
        self.jsontools = JsonTools()
        self.api_fully_doc = doc_data
        self.gpt_reply = GPTReply(model_name)
        self.params_dict = params_dict
        self.project_name = project_name
        self.syn_prompt = SyntheticPrompt()
        self.initial_test_info_dict = {
        }
        # logger.info("DependencyChain初始化完成")

    # ========= 嵌套参数匹配工具（与 para_normalize 保持一致）=========
    def _normalize_response_field(self, field: str) -> str:
        """标准化响应/参数字段：小写+按'.'分割并清洗每个片段的'[]'。"""
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
        """基于'.'分割的包含式匹配：请求字段的最后一个token出现在候选字段token集合中即认为命中。"""
        if not isinstance(req_field, str):
            req_field = str(req_field)
        if not isinstance(resp_field, str):
            resp_field = str(resp_field)
        req_tokens = [p for p in req_field.strip().lower().split(".") if p]
        if not req_tokens:
            return False
        last = req_tokens[-1]
        if last.endswith("[]"):
            last = last[:-2]
        # 避免过短token导致误判
        if len(last) < 2:
            return False
        resp_norm = self._normalize_response_field(resp_field)
        resp_tokens = set([p for p in resp_norm.split(".") if p])
        return last in resp_tokens

    def _get_param_aliases(self, group_name, param_name):
        """获取参数的所有别名（包括自身）"""
        aliases = {param_name}
        
        # 从 params_dict 的 normalized_params_process_data 中获取别名配置
        all_params = self.params_dict.get("normalized_params_process_data", [])
        
        for item in all_params:
            if item.get("group") == group_name:
                for data in item.get("data", []):
                    params_config = data.get("parameters_name", {})
                    keep_param = params_config.get("keep_pra")
                    replace_params = params_config.get("replace_para", [])
                    
                    # 如果 param_name 是主参数，添加所有替换参数作为别名
                    if param_name == keep_param:
                        aliases.update(replace_params)
                    
                    # 如果 param_name 是替换参数之一，添加主参数作为别名
                    if param_name in replace_params:
                        aliases.add(keep_param)
                        aliases.update(replace_params)
        
        return list(aliases)

    def _param_in_list(self, param_name, fields, group_name=None) -> bool:
        """判断 param_name 是否命中给定字段列表（支持嵌套/模糊匹配/别名匹配）。"""
        if not fields:
            return False
        
        # 1. 直接匹配和嵌套匹配（原有逻辑）
        for f in fields:
            try:
                if param_name == f:
                    return True
                if self._response_matches_request(param_name, f):
                    return True
            except Exception:
                continue
        
        # 2. 别名匹配（新增）
        if group_name and self.params_dict:
            aliases = self._get_param_aliases(group_name, param_name)
            for alias in aliases:
                for f in fields:
                    try:
                        if alias == f or self._response_matches_request(alias, f):
                            return True
                    except Exception:
                        continue
        
        return False

    def find_location_in_routes(self,group_name,parameters_item,routes_packages_normalized):
        """
        基于routes_packages_normalized，查找parameters_item在group_name下的接口中的位置
        
        Args:
            group_name: 接口组名称
            parameters_item: 要查找的参数名
            routes_packages_normalized: 标准化的路由参数数据
            
        Returns:
            dict: {
                "request_location_path": [接口路径列表],
                "response_location_path": [接口路径列表]
            }
        """
        result = {
            "request_location_path": [],
            "response_location_path": []
        }
        
        # 检查group_name是否存在
        if group_name not in routes_packages_normalized:
            return result
            
        # 遍历该组下的所有接口
        group_routes = routes_packages_normalized[group_name]
        for route_info in group_routes:
            # 每个route_info是一个字典，包含一个接口的信息
            for route_path, route_data in route_info.items():
                # 检查参数是否在请求参数中（支持嵌套/模糊匹配/别名匹配）
                if self._param_in_list(parameters_item, route_data.get("request_para", []), group_name):
                    result["request_location_path"].append({route_path: route_data.get("type", "unknown")})
                    
                # 检查参数是否在响应参数中（支持嵌套/模糊匹配/别名匹配）
                if self._param_in_list(parameters_item, route_data.get("response_para", []), group_name):
                    result["response_location_path"].append({route_path: route_data.get("type", "unknown")})
                    
        return result

    def find_location_in_cross_routes(self,group_name,parameters_item,routes_packages_normalized):
        """
        跨功能组查找参数位置（支持嵌套/模糊匹配）
        """
        result = {
            "request_location_path": [],
            "response_location_path": []
        }
        
        # 检查parameters_extraction_results是否存在
        # if "parameters_extraction_results" not in routes_packages_normalized:
        #     return result
            
        # extraction_results = routes_packages_normalized["normalized_"]
        
        # 遍历所有功能组（不区分功能组）
        for current_group_name, group_routes in routes_packages_normalized.items():
                
            # 遍历该组下的所有接口
            for route_info in group_routes:
                # 每个route_info是一个字典，包含一个接口的信息
                for route_path, route_data in route_info.items():
                    # 检查参数是否在请求参数中（支持嵌套/模糊匹配/别名匹配）
                    if self._param_in_list(parameters_item, route_data.get("request_para", []), current_group_name):
                        # 获取参数位置信息
                        param_location = route_data.get("param_locations", {}).get(parameters_item, "unknown")
                        result["request_location_path"].append({route_path: route_data.get("type", "unknown")})
                        
                    # 检查参数是否在响应参数中（支持嵌套/模糊匹配/别名匹配）
                    if self._param_in_list(parameters_item, route_data.get("response_para", []), current_group_name):
                        result["response_location_path"].append({route_path: route_data.get("type", "unknown")})
                        
        return result

    def dependencychain_construction(self,para_set_type,routes_relation_item,group_name,parameters_item,routes_packages_normalized,is_cross):
        """
        (add*n)->add*m->query/delete/update
        """
        def routes_relation_by_parameters_nonadd():
            """
            routes_relation_item的格式是：{"request_location_path": [{"POST /identity/api/auth/v4.0/user/login-with-token": "query"}, {"POST /identity/api/auth/v2.7/user/login-with-token": "query"}], "response_location_path": [{"POST /identity/api/auth/login": "query"}, {"POST /identity/api/auth/v2.7/user/login-with-token": "query"}]}
            parameters_item的值就是一个字符串，例如"token"
            """
            # 首先确定好一个终点：从request_location_path中删选出type不是add类型的接口作为终点，然后从response_location_path中选择一个请求参数中没有该parameters_item的并且路由的类型是add的作为起点，最后中间的链条可以是一个parameters_item是请求参数，也是响应参数的，或者只是请求参数的add类型的接口，如果中间的路由有很多符合上述要求的，那么他们就合并为一个list,不区分执行的先后顺序
            
            # 记录list query与同组add接口的配对（二维数组，每个元素为[add_api, list_query_api]）
            list_query_pairs = []
            
            # 辅助函数：判断路由是否属于当前功能组
            def _in_current_group(route_path: str) -> bool:
                group_routes = routes_packages_normalized.get(group_name, [])
                for route_info in group_routes:
                    if route_path in route_info:
                            return True
                return False
            
            # 1. 确定终点：从request_location_path中筛选出type不是add类型的接口（仅当前功能组）
            endpoints = []
            for route_dict in routes_relation_item["request_location_path"]:
                for route_path, route_type in route_dict.items():
                    # if route_type != "add" and route_type != "list query" and (_in_current_group(route_path)^is_cross):
                    if route_type != "add" and route_type != "list query":
                        endpoints.append({route_path: route_type})
            
            # 2. 确定起点：从response_location_path中选择请求参数中没有该parameters_item且路由类型是add的（仅当前功能组）
            startpoints = []
            for route_dict in routes_relation_item["response_location_path"]:
                for route_path, route_type in route_dict.items():
                    # if route_type == "add" and _in_current_group(route_path):
                    if route_type == "add":
                        # 检查该路由的请求参数中是否没有parameters_item（支持嵌套/模糊匹配/别名匹配）
                        route_found = False
                        for route_info in routes_packages_normalized.get(group_name, []):
                            if route_path in route_info:
                                route_data = route_info[route_path]
                                if not self._param_in_list(parameters_item, route_data.get("request_para", []), group_name):
                                    # 去重：按“整体起点”去重。仅当与已有起点的“签名”完全一致时才视为重复
                                    existing_signatures = set()
                                    for sp in startpoints:
                                        if isinstance(sp, list):
                                            sig = tuple(next(iter(d)) for d in sp)
                                            existing_signatures.add(sig)
                                        elif isinstance(sp, dict):
                                            sig = (next(iter(sp)),)
                                            existing_signatures.add(sig)
                                    new_sig = (route_path,)
                                    if new_sig not in existing_signatures:
                                        startpoints.append({route_path: route_type})
                                route_found = True
                                break
            if not startpoints:
                """
                这个时候再引入listquery类型，来针对add类型没有响应参数
                """
                # 尝试从 response_location_path 找到当前组的 list query
                list_query_paths = []
                for route_dict2 in routes_relation_item.get("response_location_path", []):
                    for rp, rt in route_dict2.items():
                        # if rt == "list query" and _in_current_group(rp):
                        if rt == "list query":
                            list_query_paths.append(rp)
                # 对每个 list query，找到其所在功能组（当前组）的 add 接口，合并为一个 List[dict]
                normalized_params = self.params_dict.get("normalized_params", {})
                for lq_path in list_query_paths:
                    # 当前功能组的路由集合
                    group_routes = normalized_params.get(group_name, [])
                    # 收集同组 add 接口
                    add_api_paths = []
                    for route_info in group_routes:
                        for add_path, add_data in route_info.items():
                            if add_data.get("type") == "add":
                                add_api_paths.append(add_path)
                    # 如果该功能组中没有找到 add 类型的接口，则不添加
                    if not add_api_paths:
                        continue
                    # 去重保持顺序（仅去除同一个组合内的重复项）
                    seen = set()
                    add_api_paths = [p for p in add_api_paths if not (p in seen or seen.add(p))]
                    # 构建已存在“起点组合”的签名，仅按整体组合去重（不在组合内部做元素级去重）
                    existing_signatures = set()
                    for sp in startpoints:
                        if isinstance(sp, list):
                            sig = tuple(next(iter(d)) for d in sp)
                            existing_signatures.add(sig)
                        elif isinstance(sp, dict):
                            sig = (next(iter(sp)),)
                            existing_signatures.add(sig)
                    pair_list = [{p: "add"} for p in add_api_paths]
                    pair_list.append({lq_path: "list query"})
                    sig_new = tuple(next(iter(d)) for d in pair_list)
                    if sig_new not in existing_signatures:
                        startpoints.append(pair_list)

            # 3. 确定中间链条：parameters_item既是请求参数也是响应参数的，或者只是请求参数的add类型接口
            middle_chains = []
            # 从 startpoints 收集已存在的路由，避免重复
            existing_start_routes = set()
            for sp in startpoints:
                if isinstance(sp, list):
                    for d in sp:
                        existing_start_routes.add(next(iter(d)))
                elif isinstance(sp, dict):
                    existing_start_routes.add(next(iter(sp)))
            seen_mc = set()
            for route_dict in routes_relation_item["request_location_path"]:
                for route_path, route_type in route_dict.items():
                    if route_type == "add" and route_path not in existing_start_routes and route_path not in seen_mc:
                        middle_chains.append({route_path: route_type})
                        seen_mc.add(route_path)

            # 如果startpoints或endpoints为空，直接返回空
            if not startpoints or not endpoints:
                return {}

            return {
                "startpoints": startpoints,
                "middle_chains": middle_chains,
                "endpoints": endpoints
            }

        def routes_relation_by_parameters_add():
            """
            routes_relation_item的格式是：{"request_location_path": [{"POST /identity/api/auth/v4.0/user/login-with-token": "query"}, {"POST /identity/api/auth/v2.7/user/login-with-token": "query"}], "response_location_path": [{"POST /identity/api/auth/login": "query"}, {"POST /identity/api/auth/v2.7/user/login-with-token": "query"}]}
            parameters_item的值就是一个字符串，例如"token"
            """
            # 首先起点是request_location_path中类型为add的接口，中间阶段是request_location_path或者response_location_path的add类型接口，结尾是request_location_path或者response_location_path中的query类型接口

            # 1. 确定起点：从request_location_path中筛选出type是add类型的接口（仅限当前功能组）
            # 辅助函数：判断路由是否属于当前功能组
            def _in_current_group(route_path: str) -> bool:
                group_routes = routes_packages_normalized.get(group_name, [])
                for route_info in group_routes:
                    if route_path in route_info:
                        return True
                return False
            
            startpoints = []
            for route_dict in routes_relation_item["request_location_path"]:
                for route_path, route_type in route_dict.items():
                    # if route_type == "add" and (_in_current_group(route_path)^is_cross):
                    if route_type == "add":
                        # 去重：按“整体起点”去重。仅当与已有起点的“签名”完全一致时才视为重复
                        existing_signatures = set()
                        for sp in startpoints:
                            if isinstance(sp, list):
                                sig = tuple(next(iter(d)) for d in sp)
                                existing_signatures.add(sig)
                            elif isinstance(sp, dict):
                                sig = (next(iter(sp)),)
                                existing_signatures.add(sig)
                        new_sig = (route_path,)
                        if new_sig not in existing_signatures:
                            startpoints.append({route_path: route_type})

            # 2. 确定终点：从request_location_path和response_location_path中筛选出type是query类型的接口（仅限当前功能组）
            endpoints = []
            
            # 从request_location_path中找query类型（仅当前组）
            # for route_dict in routes_relation_item["request_location_path"]:
            #     for route_path, route_type in route_dict.items():
            #         # if route_type == "query" and _in_current_group(route_path):
            #         if route_type == "query" and _in_current_group(route_path):
            #             endpoints.append({route_path: route_type})
            
            # 从response_location_path中找query类型（仅当前组）
            for route_dict in routes_relation_item["response_location_path"]:
                for route_path, route_type in route_dict.items():
                    # if route_type == "query" and _in_current_group(route_path) and {route_path: route_type} not in endpoints:
                    if route_type == "query"  and {route_path: route_type} not in endpoints:
                        endpoints.append({route_path: route_type})

            # 3. 确定中间链条：从request_location_path和response_location_path中找add类型的接口
            middle_chains = []
            # 收集 startpoints 已存在路由，避免重复
            existing_start_routes = set()
            for sp in startpoints:
                if isinstance(sp, list):
                    for d in sp:
                        existing_start_routes.add(next(iter(d)))
                elif isinstance(sp, dict):
                    existing_start_routes.add(next(iter(sp)))
            seen_mc = set()
            
            # 从request_location_path中找add类型的接口作为中间链条
            for route_dict in routes_relation_item["request_location_path"]:
                for route_path, route_type in route_dict.items():
                    if route_type == "add" and route_path not in existing_start_routes and route_path not in seen_mc:
                        middle_chains.append({route_path: route_type})
                        seen_mc.add(route_path)
            
            # 从response_location_path中找add类型的接口作为中间链条
            for route_dict in routes_relation_item["response_location_path"]:
                for route_path, route_type in route_dict.items():
                    if route_type == "add" and route_path not in existing_start_routes and route_path not in seen_mc:
                        middle_chains.append({route_path: route_type})
                        seen_mc.add(route_path)
            
            # 如果startpoints或endpoints为空，直接返回空
            if not startpoints or not endpoints:
                return {}
        
            return {
                "startpoints": startpoints,
                "middle_chains": middle_chains, 
                "endpoints": endpoints
            }
        
        # def filterd_chain_propress(dependency_chain):

        
        # {"request_location_path": [{"POST /identity/api/auth/v4.0/user/login-with-token": "query"}, {"POST /identity/api/auth/v2.7/user/login-with-token": "query"}], "response_location_path": [{"POST /identity/api/auth/login": "query"}, {"POST /identity/api/auth/v2.7/user/login-with-token": "query"}]
        
        # 检查request_location_path中是否有add类型的接口
        if para_set_type == "A-(A-B)":
            has_add_type = False
            for route_dict in routes_relation_item["response_location_path"]:
                # 每个route_dict是一个字典，格式为 {"接口路径": "接口类型"}
                for route_path, route_type in route_dict.items():
                    if route_type == "add" or route_type == "list query":
                        has_add_type = True
                        break
                if has_add_type:
                    break
            
            # 如果没有add类型的接口，直接返回空
            if not has_add_type:
                # logger.info(f"No 'add' type found in request_location_path for parameter '{parameters_item}' in group '{group_name}'")
                return {}
            
            # 继续处理依赖链构建逻辑
            # logger.info(f"Found 'add' type in request_location_path for parameter '{parameters_item}' in group '{group_name}'")
            
            # 调用routes_relation_by_parameters函数构建依赖链
            dependency_chain = routes_relation_by_parameters_nonadd()
        elif para_set_type == "A-B-add":
            has_add_type = False
            for route_dict in routes_relation_item["request_location_path"]:
                # 每个route_dict是一个字典，格式为 {"接口路径": "接口类型"}
                for route_path, route_type in route_dict.items():
                    if route_type == "add" or route_type == "list query":
                        has_add_type = True
                        break
                if has_add_type:
                    break
            
            # 如果没有add类型的接口，直接返回空
            if not has_add_type:
                # logger.info(f"No 'add' type found in request_location_path for parameter '{parameters_item}' in group '{group_name}'")
                return {}
            dependency_chain = routes_relation_by_parameters_add()
        

        # 构建最终的依赖链结果
        result = {
            parameters_item: dependency_chain
        }
        
        # logger.info(f"{parameters_item}的依赖关系为："+json.dumps(result[parameters_item]))
        return result
    

    def dependency_construction_v1(self):
        """
        功能组内的接口依赖关系
        """
        parameters_sets = self.params_dict["set_calculate_results_all"]
        routes_packages_normalized = self.params_dict["normalized_params"]
        dependencychain_results = {}
        for group_name,group_data in parameters_sets.items():
            routes_relation = {}
            dependencychain_results[group_name] = []
            for parameters_item in group_data["A-(A-B)"]:
                # logger.info
                # pass
                routes_relation[parameters_item] = self.find_location_in_routes(group_name,parameters_item,routes_packages_normalized)
            
                tmp_dependencychain_results = self.dependencychain_construction("A-(A-B)",routes_relation[parameters_item],group_name,parameters_item,routes_packages_normalized,False)
                dependencychain_results[group_name].append(tmp_dependencychain_results)
                logger.info(f"{parameters_item}\r\n:"+str(tmp_dependencychain_results)+"\r\n")
                # print('a')
            for parameters_item in group_data["A-B-add"]:
                routes_relation[parameters_item] = self.find_location_in_routes(group_name,parameters_item,routes_packages_normalized)
                # logger.info(f"{parameters_item}这个参数的所在路由位置情况："+json.dumps(routes_relation[parameters_item]))
                tmp_dependencychain_results = self.dependencychain_construction("A-B-add",routes_relation[parameters_item],group_name,parameters_item,routes_packages_normalized,False)
                dependencychain_results[group_name].append(tmp_dependencychain_results)
                logger.info(f"A_B_add_{parameters_item}\r\n:"+str(tmp_dependencychain_results)+"\r\n")
                # logger.info("A-B-add"+str(dependencychain_results[group_name]))
        return dependencychain_results

    def dependency_construction_v2(self):

        parameters_sets = self.params_dict["set_calculate_results_v3"]
        # 修复：传入完整的params_dict而不是只传入normalized_params
        routes_packages_normalized = self.params_dict["normalized_params"]
        dependencychain_results = {}
        for group_name,group_data in parameters_sets.items():
            routes_relation = {}
            dependencychain_results[group_name] = []
            for parameters_item in group_data["A-(A-B)"]:
                # logger.info
                # pass
                routes_relation[parameters_item] = self.find_location_in_cross_routes(group_name,parameters_item,routes_packages_normalized)
                # logger.info(f"{parameters_item}这个参数的所在路由位置情况："+json.dumps(routes_relation[parameters_item]))
                tmp_dependencychain_results = self.dependencychain_construction("A-(A-B)",routes_relation[parameters_item],group_name,parameters_item,routes_packages_normalized,True)
                dependencychain_results[group_name].append(tmp_dependencychain_results)
                logger.info(f"A_B{parameters_item}\r\n:"+str(tmp_dependencychain_results)+"\r\n")
            for parameters_item in group_data["A-B-add"]:
                routes_relation[parameters_item] = self.find_location_in_cross_routes(group_name,parameters_item,routes_packages_normalized)
                # logger.info(f"{parameters_item}这个参数的所在路由位置情况："+json.dumps(routes_relation[parameters_item]))
                tmp_dependencychain_results = self.dependencychain_construction("A-B-add",routes_relation[parameters_item],group_name,parameters_item,routes_packages_normalized,True)
                dependencychain_results[group_name].append(tmp_dependencychain_results)
                logger.info(f"A_B_add_{parameters_item}\r\n:"+str(tmp_dependencychain_results)+"\r\n")
  
        
        return dependencychain_results
    

    def list_add_chain_parameters_extraction(self,chain_1,chain_2):
        pass
    

    def merged_dependencychain(self,chain_1,chain_2):
        def formated_chain_1():
            """
            将chain_1数据转换为执行顺序组合格式
            - 当 startpoints 的元素为 List[dict] 时，作为一个整体单元处理：
              先执行 add 类型（若多个则并联为列表），随后依序加入其他类型（如 list query）。
            - 该整体单元以顶层步骤 1 的形式加入，形成多重嵌套结构。
            """
            result = {}
            
            for group_name, group_data in chain_1.items():
                result[group_name] = []
                
                for item in group_data:
                    if not item:  # 跳过空字典
                        continue
                        
                    for param_name, param_data in item.items():
                        if not param_data:  # 跳过空的参数数据
                            continue
                            
                        # 获取startpoints（起始点）
                        startpoints = param_data.get('startpoints', [])
                        if not startpoints:
                            continue
                            
                        # 获取middle_chains（中间链）
                        middle_chains = param_data.get('middle_chains', [])
                        
                        # 获取endpoints（终点）
                        endpoints = param_data.get('endpoints', [])
                        if not endpoints:
                            continue
                        
                        # 预处理：去重 startpoints / middle_chains / endpoints，降低组合规模
                        deduped_startpoints = []
                        _seen_sp = set()
                        for sp in startpoints:
                            try:
                                _key = json.dumps(sp, sort_keys=True, ensure_ascii=False)
                            except Exception:
                                _key = str(sp)
                            if _key not in _seen_sp:
                                deduped_startpoints.append(sp)
                                _seen_sp.add(_key)
                        
                        unique_endpoints = []
                        _seen_end = set()
                        for endpoint in endpoints:
                            end_route = list(endpoint.keys())[0]
                            if end_route not in _seen_end:
                                unique_endpoints.append(endpoint)
                                _seen_end.add(end_route)
                        
                        unique_middle = []
                        _seen_mid = set()
                        for mid in middle_chains:
                            mid_route = list(mid.keys())[0]
                            if mid_route not in _seen_mid:
                                unique_middle.append(mid)
                                _seen_mid.add(mid_route)
                        
                        # middle_chains 的排列，数量过大时只使用原始顺序，避免阶乘爆炸
                        MAX_MIDDLE_PERM = 3
                        if unique_middle:
                            if len(unique_middle) <= MAX_MIDDLE_PERM:
                                middle_perms = list(itertools.permutations(unique_middle))
                            else:
                                middle_perms = [tuple(unique_middle)]
                        else:
                            middle_perms = [tuple([])]
                        
                        # 为每个startpoint生成组合
                        for start_item in deduped_startpoints:
                            # 为每个 endpoint 和每个 middle_chains 排列生成组合
                            for endpoint in unique_endpoints:
                                end_route = list(endpoint.keys())[0]
                                
                                for middle_perm in middle_perms:
                                    combination = {}
                                    step_no = 1
                                    
                                    # 支持 start_item 为 dict 或 List[dict]
                                    if isinstance(start_item, list):
                                        # 将 List[dict] 按类型拆分，add 优先且多个并联为列表，其后为其他类型顺序执行
                                        typed_items = []
                                        for d in start_item:
                                            route = list(d.keys())[0]
                                            rtype = d[route]
                                            typed_items.append((route, rtype))
                                        add_routes = [route for route, t in typed_items if t == "add"]
                                        other_routes = [route for route, t in typed_items if t != "add"]
                                        
                                        # 稳定排序，避免并联项顺序导致的重复
                                        add_routes = sorted(add_routes)
                                        
                                        start_unit = {}
                                        inner_idx = 1
                                        if add_routes:
                                            start_unit[str(inner_idx)] = add_routes if len(add_routes) > 1 else add_routes[0]
                                            inner_idx += 1
                                        for route in other_routes:
                                            start_unit[str(inner_idx)] = route
                                            inner_idx += 1
                                        
                                        # 将整体单元作为第一步
                                        combination[str(step_no)] = start_unit
                                        step_no += 1
                                    else:
                                        # 单独的起始路由沿用原逻辑
                                        route = list(start_item.keys())[0]
                                        combination[str(step_no)] = route
                                        step_no += 1
                                    
                                    # 添加 middle_chains
                                    for middle_item in middle_perm:
                                        middle_route = list(middle_item.keys())[0]
                                        combination[str(step_no)] = middle_route
                                        step_no += 1
                                    
                                    # 添加 endpoint（每个 endpoint 单独形成一条依赖链的末尾）
                                    combination[str(step_no)] = end_route
                                    
                                    result[group_name].append(combination)
            
            return result
        
        def formated_chain_2():
            """
            将chain_2数据转换为执行顺序组合格式
            - 当 startpoints 的元素为 List[dict] 时，作为一个整体单元处理：
              先执行 add 类型（若多个则并联为列表），随后依序加入其他类型（如 list query）。
            - 该整体单元以顶层步骤 1 的形式加入，形成多重嵌套结构。
            """
            result = {}
            
            for group_name, group_data in chain_2.items():
                result[group_name] = []
                
                for item in group_data:
                    if not item:  # 跳过空字典
                        continue
                    
                    for param_name, param_data in item.items():
                        if not param_data:  # 跳过空的参数数据
                            continue
                        
                        startpoints = param_data.get('startpoints', [])
                        middle_chains = param_data.get('middle_chains', [])
                        endpoints = param_data.get('endpoints', [])
                        
                        if not startpoints or not endpoints:
                            continue
                        
                        # 预处理：去重 startpoints / middle_chains / endpoints，降低组合规模
                        deduped_startpoints = []
                        _seen_sp = set()
                        for sp in startpoints:
                            try:
                                _key = json.dumps(sp, sort_keys=True, ensure_ascii=False)
                            except Exception:
                                _key = str(sp)
                            if _key not in _seen_sp:
                                deduped_startpoints.append(sp)
                                _seen_sp.add(_key)
                        
                        unique_endpoints = []
                        _seen_end = set()
                        for endpoint in endpoints:
                            end_route = list(endpoint.keys())[0]
                            if end_route not in _seen_end:
                                unique_endpoints.append(endpoint)
                                _seen_end.add(end_route)
                        
                        unique_middle = []
                        _seen_mid = set()
                        for mid in middle_chains:
                            mid_route = list(mid.keys())[0]
                            if mid_route not in _seen_mid:
                                unique_middle.append(mid)
                                _seen_mid.add(mid_route)
                        
                        # middle_chains 的排列，数量过大时只使用原始顺序，避免阶乘爆炸
                        MAX_MIDDLE_PERM = 3
                        if unique_middle:
                            if len(unique_middle) <= MAX_MIDDLE_PERM:
                                middle_perms = list(itertools.permutations(unique_middle))
                            else:
                                middle_perms = [tuple(unique_middle)]
                        else:
                            middle_perms = [tuple([])]
                        
                        for start_item in deduped_startpoints:
                            # endpoints 的处理保持一致（每个 endpoint 都生成组合）
                            for endpoint in unique_endpoints:
                                end_route = list(endpoint.keys())[0]
                                
                                for middle_perm in middle_perms:
                                    combination = {}
                                    step_no = 1
                                    
                                    # 支持 start_item 为 dict 或 List[dict]
                                    if isinstance(start_item, list):
                                        typed_items = []
                                        for d in start_item:
                                            route = list(d.keys())[0]
                                            rtype = d[route]
                                            typed_items.append((route, rtype))
                                        add_routes = [route for route, t in typed_items if t == "add"]
                                        other_routes = [route for route, t in typed_items if t != "add"]
                                        
                                        # 稳定排序，避免并联项顺序导致的重复
                                        add_routes = sorted(add_routes)
                                        
                                        start_unit = {}
                                        inner_idx = 1
                                        if add_routes:
                                            start_unit[str(inner_idx)] = add_routes if len(add_routes) > 1 else add_routes[0]
                                            inner_idx += 1
                                        for route in other_routes:
                                            start_unit[str(inner_idx)] = route
                                            inner_idx += 1
                                        
                                        # 将整体单元作为第一步
                                        combination[str(step_no)] = start_unit
                                        step_no += 1
                                    else:
                                        # 单独的起始路由沿用原逻辑
                                        route = list(start_item.keys())[0]
                                        combination[str(step_no)] = route
                                        step_no += 1
                                    
                                    # 添加 middle_chains
                                    for middle_item in middle_perm:
                                        middle_route = list(middle_item.keys())[0]
                                        combination[str(step_no)] = middle_route
                                        step_no += 1
                                    
                                    # 添加 endpoint
                                    combination[str(step_no)] = end_route
                                    
                                    result[group_name].append(combination)
            
            return result
        
        def remove_duplicates(chain_1, chain_2):
            filtered_chain_2 = {}
            
            for group_name in chain_2:
                filtered_chain_2[group_name] = []
                chain_1_combinations = set()
                
                # 将chain_1中的组合转换为可比较的字符串格式（支持嵌套结构）
                if group_name in chain_1:
                    for combo in chain_1[group_name]:
                        try:
                            combo_repr = json.dumps(combo, sort_keys=True, ensure_ascii=False)
                        except Exception:
                            combo_repr = str(sorted(combo.items()))
                        chain_1_combinations.add(combo_repr)
                
                # 过滤chain_2中的重复组合
                for combo in chain_2[group_name]:
                    try:
                        combo_repr = json.dumps(combo, sort_keys=True, ensure_ascii=False)
                    except Exception:
                        combo_repr = str(sorted(combo.items()))
                    if combo_repr not in chain_1_combinations:
                        filtered_chain_2[group_name].append(combo)
            
            return filtered_chain_2

        def merged_routes_in_chains(results_chain_1, results_chain_2_filtered):
            normalized_params = self.params_dict["normalized_params"]
            merged_results = {}
            
            # 合并两个结果集
            all_chains = {}
            for group_name in results_chain_1:
                all_chains[group_name] = results_chain_1[group_name] + results_chain_2_filtered.get(group_name, [])
            
            for group_name in results_chain_2_filtered:
                if group_name not in all_chains:
                    all_chains[group_name] = results_chain_2_filtered[group_name]
            
            # 处理每个组的依赖链
            for group_name, chains in all_chains.items():
                merged_results[group_name] = []
                
                for chain in chains:
                    if not chain:  # 跳过空字典
                        continue
                        
                    # 获取链中的接口序列
                    chain_items = list(chain.items())
                    if len(chain_items) <= 2:  # 如果链长度小于等于2，直接添加
                        merged_results[group_name].append(chain)
                        continue
                    
                    # 分析中间接口（除了第一个和最后一个）
                    middle_apis = []
                    for i in range(1, len(chain_items) - 1):
                        api_endpoint = chain_items[i][1]
                        middle_apis.append(api_endpoint)
                    
                    # 查找中间接口的请求参数
                    parallel_startpoints = set()
                    for api_endpoint in middle_apis:
                        # 在normalized_params中查找该接口的请求参数
                        for group_params in normalized_params.values():
                            for api_group in group_params:
                                if api_endpoint in api_group:
                                    request_params = api_group[api_endpoint].get('request_para', [])
                                    
                                    # 在chain_1和chain_2中查找这些参数的startpoints
                                    for param in request_params:
                                        startpoints = find_param_startpoints(param, chain_1, chain_2)
                                        parallel_startpoints.update(startpoints)
                    
                    # 创建新的链，将并联的startpoints添加到序号1的位置
                    new_chain = {}
                    if parallel_startpoints:
                        # 将原来序号1的接口和并联的startpoints合并，去除重复
                        original_first = chain_items[0][1]
                        all_first_apis = [original_first]
                        for startpoint in parallel_startpoints:
                            if startpoint not in all_first_apis:
                                all_first_apis.append(startpoint)
                        new_chain['1'] = all_first_apis
                        
                        # 添加其余接口
                        for i in range(1, len(chain_items)):
                            new_chain[str(i + 1)] = chain_items[i][1]
                    else:
                        new_chain = chain
                    
                    merged_results[group_name].append(new_chain)
            
            # 去除跨组重复的依赖链条
            deduplicated_results = {}
            seen_chains = set()
            
            for group_name, chains in merged_results.items():
                deduplicated_results[group_name] = []
                for chain in chains:
                    # 将链条转换为可哈希的字符串表示
                    chain_str = str(sorted(chain.items()))
                    if chain_str not in seen_chains:
                        seen_chains.add(chain_str)
                        deduplicated_results[group_name].append(chain)
            
            return deduplicated_results
        
        def find_param_startpoints(param_name, chain_1, chain_2):
            """在chain_1和chain_2中查找指定参数的startpoints接口，兼容 List[dict] 结构"""
            startpoints = set()
            
            # 搜索chain_1
            for group_name, group_data in chain_1.items():
                for item in group_data:
                    if param_name in item:
                        param_data = item[param_name]
                        if 'startpoints' in param_data:
                            for startpoint in param_data['startpoints']:
                                if isinstance(startpoint, list):
                                    for d in startpoint:
                                        for api_endpoint in d.keys():
                                            startpoints.add(api_endpoint)
                                elif isinstance(startpoint, dict):
                                    for api_endpoint in startpoint.keys():
                                        startpoints.add(api_endpoint)
            
            # 搜索chain_2
            for group_name, group_data in chain_2.items():
                for item in group_data:
                    if param_name in item:
                        param_data = item[param_name]
                        if 'startpoints' in param_data:
                            for startpoint in param_data['startpoints']:
                                if isinstance(startpoint, list):
                                    for d in startpoint:
                                        for api_endpoint in d.keys():
                                            startpoints.add(api_endpoint)
                                elif isinstance(startpoint, dict):
                                    for api_endpoint in startpoint.keys():
                                        startpoints.add(api_endpoint)
            
            return startpoints
        
        def remove_duplicates_v1(chan_data):
            # 针对 formated_chain_1 的去重：在各自的功能组内，按整条链组合进行去重
            filtered_chain_1 = {}
            for group_name, chains in chan_data.items():
                seen = set()
                filtered = []
                for combo in chains:
                    try:
                        combo_repr = json.dumps(combo, sort_keys=True, ensure_ascii=False)
                    except Exception:
                        combo_repr = str(sorted(combo.items()))
                    if combo_repr not in seen:
                        seen.add(combo_repr)
                        filtered.append(combo)
                filtered_chain_1[group_name] = filtered
            return filtered_chain_1

        def merge_step1_unit_variants(chains):
            """
            合并同一功能组内仅在顶层步骤 "1" 的嵌套字典中存在差异的链条：
            - 以嵌套 dict 的 "1"."1" (add 路由，可能为字符串或列表) 以及后续步骤(2..n)为分组键；
            - 将不同链条在嵌套 dict 中除 "1" 之外的其他路由(如 list/query)做并集，保持插入顺序；
            - 返回合并后的链条列表；对于不含嵌套 dict 的步骤1的链条，原样保留。
            """
            merged = []
            groups = {}  # key -> aggregator

            def _normalize_add(add_val):
                if isinstance(add_val, list):
                    return tuple(sorted(add_val))
                elif isinstance(add_val, str):
                    return (add_val,)
                return tuple()

            def _rest_part(chain):
                rest = {k: v for k, v in chain.items() if k != '1'}
                return rest

            def _rest_key(rest):
                try:
                    return json.dumps(rest, sort_keys=True, ensure_ascii=False)
                except Exception:
                    return str(sorted(rest.items()))

            # 先收集可合并的链条
            for chain in chains:
                step1 = chain.get('1')
                if isinstance(step1, dict):
                    add_val = step1.get('1')
                    norm_add = _normalize_add(add_val)
                    rest = _rest_part(chain)
                    key = (norm_add, _rest_key(rest))
                    if key not in groups:
                        groups[key] = {
                            'add_val': add_val,
                            'extras_order': [],  # 记录并集路由的插入顺序
                            'extras_seen': set(),
                            'rest': rest
                        }
                    # 收集除 "1" 外的其他路由，作为并集
                    for inner_k, inner_v in step1.items():
                        if inner_k == '1':
                            continue
                        # inner_v 多为字符串；稳妥起见支持列表
                        if isinstance(inner_v, list):
                            for route in inner_v:
                                if route not in groups[key]['extras_seen']:
                                    groups[key]['extras_seen'].add(route)
                                    groups[key]['extras_order'].append(route)
                        else:
                            route = inner_v
                            if route not in groups[key]['extras_seen']:
                                groups[key]['extras_seen'].add(route)
                                groups[key]['extras_order'].append(route)
                else:
                    # 不可合并的(步骤1非嵌套dict)直接保留
                    merged.append(chain)

            # 生成合并后的链条
            for aggr in groups.values():
                start_unit = {}
                # 保留原 "add" 值(字符串或列表)
                start_unit['1'] = aggr['add_val']
                inner_idx = 1
                for route in aggr['extras_order']:
                    inner_idx += 1
                    start_unit[str(inner_idx)] = route

                # 重新拼装完整链条
                new_chain = {'1': start_unit}
                # 后续步骤保持原顺序(按数字键排序)
                for k in sorted(aggr['rest'].keys(), key=lambda x: int(x)):
                    new_chain[k] = aggr['rest'][k]
                merged.append(new_chain)

            return merged

        def combine_isolate_chains(merged_cross):
            """
            将 API 文档(self.api_fully_doc)中未出现在 merged_cross 的接口，按所属功能组追加为 {"1": "<endpoint>"} 的链条项，
            直接补充到对应组的链表中（顺序以文档出现为准，避免重复）。
            """
            # 读取文档数据（兼容传入文件路径的情况）
            doc = self.api_fully_doc
            if isinstance(doc, str):
                try:
                    doc = self.jsontools.read_json(doc)
                except Exception:
                    doc = []
            
            # 从 normalized_params 构建 endpoint -> group 的映射
            normalized_params = self.params_dict.get("normalized_params", {})
            endpoint_to_group = {}
            for g, routes in normalized_params.items():
                for ri in routes:
                    for ep in ri.keys():
                        endpoint_to_group[ep] = g
            # 回退：从文档结构补充映射
            if isinstance(doc, list):
                for group_dict in doc:
                    if isinstance(group_dict, dict):
                        for group_name, routes in group_dict.items():
                            if isinstance(routes, dict):
                                for ep in routes.keys():
                                    endpoint_to_group.setdefault(ep, group_name)
            
            # 收集 merged_cross 中已出现过的所有接口（递归遍历链条结构）
            existing_eps = set()
            def collect_eps(val):
                if isinstance(val, str):
                    existing_eps.add(val)
                elif isinstance(val, list):
                    for item in val:
                        collect_eps(item)
                elif isinstance(val, dict):
                    for subv in val.values():
                        collect_eps(subv)
            for grp, chains in merged_cross.items():
                for chain in chains:
                    collect_eps(chain)
            
            # 按文档出现顺序收集每个功能组的接口列表
            doc_eps_by_group = {}
            seen_doc = set()
            if isinstance(doc, list):
                for group_dict in doc:
                    if not isinstance(group_dict, dict):
                        continue
                    for group_name, routes in group_dict.items():
                        if not isinstance(routes, dict):
                            continue
                        lst = doc_eps_by_group.setdefault(group_name, [])
                        for ep in routes.keys():
                            if ep not in seen_doc:
                                seen_doc.add(ep)
                                lst.append(ep)
            
            # 将未出现的文档接口追加到对应功能组的链表中，格式为 {"1": "<endpoint>"}
            for group_name, eps_list in doc_eps_by_group.items():
                # 初始化不存在的组
                if group_name not in merged_cross:
                    merged_cross[group_name] = []
                for ep in eps_list:
                    if ep not in existing_eps:
                        target_group = endpoint_to_group.get(ep, group_name)
                        if target_group not in merged_cross:
                            merged_cross[target_group] = []
                        merged_cross[target_group].append({"1": ep})
                        existing_eps.add(ep)
            
            # 返回占位字典，调用方会把 mutated merged_cross 放入 merged_results["cross"]
            return {}

        debug = False
        if debug == True:
            merged_cross = {}
            combine_isolate_chains(merged_cross)
            merged_results = {"cross": merged_cross}
            return merged_results
        # logger.info("debug")
        results_chain_1 = formated_chain_1()
        # self.jsontools.write_json("/Users/tlif3./zju_research/bolascan_v3/bolascan_v4/cache/newbee_mall/formated_chain_1.json", results_chain_1)
        results_chain_2 = formated_chain_2()
        # self.jsontools.write_json("/Users/tlif3./zju_research/bolascan_v3/bolascan_v4/cache/newbee_mall/formated_chain_2.json", results_chain_2)
        
        # 应用去重逻辑
        results_chain_1_filtered = remove_duplicates_v1(results_chain_1)
        results_chain_2_filtered = remove_duplicates_v1(results_chain_2)
        # 将去重后的 chain_1 写回相同路径，确保输出文件为去重结果
        # self.jsontools.write_json("/Users/tlif3./zju_research/bolascan_v3/bolascan_v4/cache/newbee_mall/formated_chain_1.json", results_chain_1_filtered)
        results_chain_2_filtered = remove_duplicates(results_chain_1_filtered, results_chain_2_filtered)

        # self.jsontools.write_json("/Users/tlif3./zju_research/bolascan_v3/bolascan_v4/cache/newbee_mall/formated_chain_3.json", results_chain_2_filtered)
        logger.info(results_chain_2_filtered)
        
        # 应用并联关系合并
        merged_results = {}
        # 按组名合并两个结果字典：同名组的链表进行拼接，不同组原样保留
        merged_cross = {}
        all_groups = set(list(results_chain_2_filtered.keys()) + list(results_chain_1_filtered.keys()))
        for group_name in all_groups:
            chains_2 = results_chain_2_filtered.get(group_name, [])
            chains_1 = results_chain_1_filtered.get(group_name, [])
            merged_cross[group_name] = merge_step1_unit_variants(chains_2 + chains_1)
        # if debug == "true":

        merged_results = combine_isolate_chains(merged_cross)
        merged_results["cross"] = merged_cross
        
        # merged_results["group"] = results_chain_1_filtered
        # merged_results = merged_routes_in_chains(results_chain_1, results_chain_2_filtered)
        
        # 返回合并后的结果
        return merged_results

    # def filterd_results_by_llm(self,merged_dependencychain):

    def dfs_dependency_chain(self,merged_results):
        normalized_params = self.params_dict.get("normalized_params", {})
        set_calc_v3 = self.params_dict.get("set_calculate_results_v3", {})
        # 汇总所有功能组的 A-(A-B) 参数集合（跨组）
        aab_params_all = set()
        for _, group_data in set_calc_v3.items():
            aab_params_all.update(group_data.get("A-(A-B)", []))

        # 根据 normalized_params 预构建 endpoint->group 映射，避免使用静态路径规则
        endpoint_to_group = {}
        for g, routes in normalized_params.items():
            for ri in routes:
                for ep in ri.keys():
                    endpoint_to_group[ep] = g

        def parse_group_from_endpoint(endpoint: str):
            # 直接使用 parameters_dict_all.json 中定义的功能组名称
            return endpoint_to_group.get(endpoint)
        
        def find_route_data(group: str, endpoint: str):
            for route_info in normalized_params.get(group, []):
                if endpoint in route_info:
                    return route_info[endpoint]
            return None
        
        def collect_request_params_for_endpoint(endpoint: str):
            group = parse_group_from_endpoint(endpoint)
            rd = find_route_data(group, endpoint) if group else None
            if rd:
                return rd.get("request_para", [])
            return []
        
        def find_response_endpoints_for_param(param: str):
            eps = []
            for g, routes in normalized_params.items():
                for ri in routes:
                    for ep, rd in ri.items():
                        resp = rd.get("response_para", [])
                        if self._param_in_list(param, resp, g):
                            eps.append((g, ep, rd.get("type", "unknown")))
            return eps
        
        def find_add_endpoints_in_group(group: str, exclude_param: str = None):
            """
            获取指定功能组中的所有 add 类型接口。
            如果指定了 exclude_param，则过滤掉请求参数中包含该参数的接口
            （因为这些接口本身需要该参数，不能作为该参数的起点）。
            """
            adds = []
            for ri in normalized_params.get(group, []):
                for ep, rd in ri.items():
                    if rd.get("type") == "add":
                        # 如果指定了 exclude_param，检查该接口是否需要该参数
                        if exclude_param:
                            req_params = rd.get("request_para", [])
                            if self._param_in_list(exclude_param, req_params, group):
                                # 该接口需要 exclude_param，跳过
                                continue
                        adds.append(ep)
            return adds
        
        def chain_has_combo(chain: dict, combo: dict):
            # 检查链中是否已存在相同组合，避免重复添加
            for k, v in chain.items():
                if isinstance(v, dict):
                    if v.get("1") == combo.get("1") and v.get("2") == combo.get("2"):
                        return True
                elif isinstance(v, list):
                    for d in v:
                        if isinstance(d, dict) and d.get("1") == combo.get("1") and d.get("2") == combo.get("2"):
                            return True
            return False
        
        def collect_chain_endpoints(chain: dict):
            eps = []
            for k in sorted(chain.keys(), key=lambda x: int(x) if str(x).isdigit() else 9999):
                v = chain[k]
                if isinstance(v, dict):
                    for subk, subv in v.items():
                        if isinstance(subv, str):
                            eps.append(subv)
                        elif isinstance(subv, list):
                            for ep in subv:
                                if isinstance(ep, str):
                                    eps.append(ep)
                elif isinstance(v, str):
                    eps.append(v)
                elif isinstance(v, list):
                    for d in v:
                        if isinstance(d, dict):
                            if "1" in d:
                                one = d["1"]
                                if isinstance(one, list):
                                    for ep in one:
                                        eps.append(ep)
                                elif isinstance(one, str):
                                    eps.append(one)
                            if "2" in d and isinstance(d["2"], str):
                                eps.append(d["2"])
            return eps

        # 新增：根据要前移/删除的端点，剪枝旧链条中对应的元素或组合
        def prune_chain_by_endpoints(chain: dict, endpoints_to_prune: set):
            def contains(val):
                if isinstance(val, str):
                    return val in endpoints_to_prune
                if isinstance(val, list):
                    for item in val:
                        if isinstance(item, str) and item in endpoints_to_prune:
                            return True
                        if isinstance(item, dict) and contains(item):
                            return True
                    return False
                if isinstance(val, dict):
                    for _, subv in val.items():
                        if contains(subv):
                            return True
                return False

            new_chain = {}
            for k in sorted(chain.keys(), key=lambda x: int(x) if str(x).isdigit() else 9999):
                v = chain[k]
                # 顶层为字符串或字典：若内部包含要前移的端点，则整体删除
                if isinstance(v, str):
                    if contains(v):
                        continue
                    new_chain[k] = v
                elif isinstance(v, dict):
                    if contains(v):
                        continue
                    new_chain[k] = v
                elif isinstance(v, list):
                    # 顶层为列表：过滤掉包含要前移端点的组合字典
                    filtered = []
                    for d in v:
                        if isinstance(d, dict) and contains(d):
                            continue
                        filtered.append(d)
                    if filtered:
                        new_chain[k] = filtered
                else:
                    new_chain[k] = v
            return new_chain

        # 新增：根据完整组合字典匹配，剪枝旧链条中已出现的相同组合
        def prune_chain_by_combo(chain: dict, combo: dict):
            def is_same_combo(d: dict) -> bool:
                return isinstance(d, dict) and d.get("1") == combo.get("1") and d.get("2") == combo.get("2")
            new_chain = {}
            for k in sorted(chain.keys(), key=lambda x: int(x) if str(x).isdigit() else 9999):
                v = chain[k]
                if isinstance(v, dict):
                    if is_same_combo(v):
                        continue
                    new_chain[k] = v
                elif isinstance(v, list):
                    filtered = []
                    for d in v:
                        if isinstance(d, dict) and is_same_combo(d):
                            continue
                        filtered.append(d)
                    if filtered:
                        new_chain[k] = filtered
                else:
                    new_chain[k] = v
            return new_chain
        
        def find_routes_by_llm(param):
            pass

        def augment_once(chain: dict):
            original_endpoints = collect_chain_endpoints(chain)
            new_combos = []
            combos_to_prune = []
            # 新增：组合签名集合，避免同一轮内重复加入相同组合；以及剪枝项签名集合，避免重复剪枝
            seen_signatures = set()
            combos_to_prune_sigs = set()
            def combo_signature(c: dict):
                v1 = c.get("1")
                v2 = c.get("2")
                if isinstance(v1, list):
                    s1 = tuple(sorted(v1))
                else:
                    s1 = v1
                return (s1, v2)
            # 先收集 original_endpoints 的所有请求参数，再统一筛选（仅收集字符串，扁平化嵌套）
            req_params_all = set()
            for ep in original_endpoints:
                req_params = collect_request_params_for_endpoint(ep)
                for p in (req_params or []):
                    if isinstance(p, str):
                        req_params_all.add(p)
                    elif isinstance(p, list):
                        for sub in p:
                            if isinstance(sub, str):
                                req_params_all.add(sub)
                    elif isinstance(p, dict):
                        name = p.get("name")
                        if isinstance(name, str):
                            req_params_all.add(name)

            # 只处理属于 A-(A-B) 且尚未访问过的参数
            target_params = [p for p in req_params_all if p in aab_params_all and p not in visited_params]

            for p in target_params:
                visited_params.add(p)
                resp_eps = find_response_endpoints_for_param(p)
                if not resp_eps:
                    # 分支占位：未找到匹配的接口，记录其相关功能组，稍后由你补充追加逻辑
                    if not hasattr(self, "pending_group_matches"):
                        self.pending_group_matches = {}
                    related_groups = set()
                    for g, routes in normalized_params.items():
                        for ri in routes:
                            for ep2, rd2 in ri.items():
                                if self._param_in_list(p, rd2.get("request_para", []), g):
                                    related_groups.add(g)
                    self.pending_group_matches[p] = list(related_groups)
                    continue
                for g, ep_resp, t in resp_eps:
                    if t == "add":
                        combo = {"1": ep_resp}
                        exists = chain_has_combo(chain, combo)
                        sig = combo_signature(combo)
                        if ep_resp not in visited_endpoints and sig not in seen_signatures:
                            visited_endpoints.add(ep_resp)
                            new_combos.append(combo)
                            seen_signatures.add(sig)
                            if exists and sig not in combos_to_prune_sigs:
                                combos_to_prune.append(combo)
                                combos_to_prune_sigs.add(sig)
                    elif t == "list query":
                        # 传入 p 参数，过滤掉那些需要 p 作为请求参数的接口
                        add_eps = find_add_endpoints_in_group(g, exclude_param=p)
                        if add_eps:
                            combo = {"1": add_eps if len(add_eps) > 1 else add_eps[0], "2": ep_resp}
                            exists = chain_has_combo(chain, combo)
                            sig = combo_signature(combo)
                            if sig not in seen_signatures:
                                for aep in add_eps:
                                    visited_endpoints.add(aep)
                                visited_endpoints.add(ep_resp)
                                new_combos.append(combo)
                                seen_signatures.add(sig)
                                if exists and sig not in combos_to_prune_sigs:
                                    combos_to_prune.append(combo)
                                    combos_to_prune_sigs.add(sig)
                        else:
                            # 分支占位：该功能组无 add 类型接口，记录以便稍后补充
                            if not hasattr(self, "pending_group_matches"):
                                self.pending_group_matches = {}
                            self.pending_group_matches[p] = [g]
                    
            if not new_combos:
                return chain, False
            
            # === 新增：对 new_combos 进行依赖排序 ===
            def extract_endpoints_from_combo(combo):
                """从 combo 结构中提取所有端点"""
                eps = []
                for k, v in combo.items():
                    if isinstance(v, str):
                        eps.append(v)
                    elif isinstance(v, list):
                        for item in v:
                            if isinstance(item, str):
                                eps.append(item)
                return eps
            
            def collect_response_params_for_endpoint(endpoint: str):
                """获取端点的响应参数"""
                group = parse_group_from_endpoint(endpoint)
                rd = find_route_data(group, endpoint) if group else None
                if rd:
                    resp = rd.get("response_para", [])
                    return resp
                # 调试：如果找不到数据，记录日志
                logger.info(f"[Combo-Debug] 未找到响应参数: endpoint={endpoint}, group={group}")
                return []
            
            def combo_depends_on(combo_a, combo_b):
                """检查 combo_a 是否依赖于 combo_b（即 A 的请求参数与 B 的响应参数有交集）"""
                eps_a = extract_endpoints_from_combo(combo_a)
                eps_b = extract_endpoints_from_combo(combo_b)
                
                # 收集 A 中所有端点的请求参数
                req_params_a = set()
                for ep in eps_a:
                    params = collect_request_params_for_endpoint(ep)
                    for p in (params or []):
                        param_name = p if isinstance(p, str) else p.get("name", "") if isinstance(p, dict) else ""
                        if param_name:
                            req_params_a.add(param_name.lower())
                
                # 收集 B 中所有端点的响应参数
                resp_params_b = set()
                for ep in eps_b:
                    params = collect_response_params_for_endpoint(ep)
                    for p in (params or []):
                        param_name = p if isinstance(p, str) else p.get("name", "") if isinstance(p, dict) else ""
                        if param_name:
                            resp_params_b.add(param_name.lower())
                
                # 当 A 的请求参数与 B 的响应参数有交集时，存在依赖
                intersection = req_params_a & resp_params_b
                if intersection:
                    logger.info(f"[Combo-Dependency] A请求:{req_params_a}, B响应:{resp_params_b}, 交集:{intersection}")
                return bool(intersection)
            
            def get_combo_complexity(combo):
                """计算 combo 的复杂度（请求参数数量 + 路径参数数量 * 10）"""
                import re
                eps = extract_endpoints_from_combo(combo)
                total_req_params = 0
                total_path_params = 0
                for ep in eps:
                    # 统计路径参数
                    path_params = re.findall(r'\{([^}]+)\}', ep)
                    total_path_params += len(path_params)
                    # 统计请求参数
                    params = collect_request_params_for_endpoint(ep)
                    total_req_params += len(params or [])
                # 路径参数权重更高（乘以10），因为有路径参数意味着需要依赖其他接口
                return total_req_params + total_path_params * 10
            
            def break_cycle_dependency(combo_a, combo_b):
                """当 A 和 B 互相依赖时，判断应该保留哪个方向的依赖。
                返回 True 表示保留 A->B 的依赖，False 表示不保留（应该是 B->A）"""
                complexity_a = get_combo_complexity(combo_a)
                complexity_b = get_combo_complexity(combo_b)
                # 复杂度高的依赖复杂度低的
                # 如果 A 复杂度更高，保留 A->B；如果 B 复杂度更高，不保留 A->B
                return complexity_a > complexity_b
            
            def topological_sort_combos(combos):
                """对 combos 进行拓扑排序，返回分层列表"""
                if len(combos) <= 1:
                    return [combos] if combos else []
                
                n = len(combos)
                
                # 第一步：检测所有依赖关系（包括双向）
                raw_deps = {}  # raw_deps[(i,j)] = True 表示 combo[i] 依赖 combo[j]
                for i in range(n):
                    for j in range(n):
                        if i != j and combo_depends_on(combos[i], combos[j]):
                            raw_deps[(i, j)] = True
                
                # 第二步：处理循环依赖，使用复杂度打破循环
                in_degree = [0] * n
                graph = [[] for _ in range(n)]
                dependencies_found = False
                
                for i in range(n):
                    for j in range(n):
                        if i != j and raw_deps.get((i, j), False):
                            # combo[i] 依赖 combo[j]
                            # 检查是否存在反向依赖（循环依赖）
                            if raw_deps.get((j, i), False):
                                # 存在循环依赖，使用复杂度打破
                                if break_cycle_dependency(combos[i], combos[j]):
                                    # 保留 i->j 依赖（i 复杂度更高，依赖 j）
                                    graph[j].append(i)
                                    in_degree[i] += 1
                                    dependencies_found = True
                                    logger.info(f"[Topo-Sort] 循环依赖打破: combo[{i}] 依赖于 combo[{j}] (复杂度 {get_combo_complexity(combos[i])} > {get_combo_complexity(combos[j])})")
                                # 否则不添加这个方向的依赖，等反向遍历时添加
                            else:
                                # 单向依赖，直接添加
                                graph[j].append(i)
                                in_degree[i] += 1
                                dependencies_found = True
                                logger.info(f"[Topo-Sort] 发现依赖: combo[{i}] 依赖于 combo[{j}]")
                
                if not dependencies_found and n > 1:
                    logger.info(f"[Topo-Sort] 未发现任何依赖关系，共 {n} 个 combos")
                
                # 第三步：分层拓扑排序（Kahn 算法）
                layers = []
                remaining = set(range(n))
                
                while remaining:
                    current_layer = [i for i in remaining if in_degree[i] == 0]
                    
                    if not current_layer:
                        # 仍有循环（多个节点互相依赖），按复杂度排序选择最简单的作为起点
                        remaining_list = list(remaining)
                        remaining_list.sort(key=lambda x: get_combo_complexity(combos[x]))
                        current_layer = [remaining_list[0]]
                        logger.info(f"[Topo-Sort] 强制打破循环，选择复杂度最低的 combo[{current_layer[0]}]")
                    
                    layers.append([combos[i] for i in current_layer])
                    
                    for i in current_layer:
                        remaining.remove(i)
                        for j in graph[i]:
                            if j in remaining:
                                in_degree[j] -= 1
                
                return layers
            
            # 对 new_combos 进行拓扑排序
            if len(new_combos) > 1:
                logger.info(f"[Combo-Sort] 排序前 new_combos 数量: {len(new_combos)}")
                for i, c in enumerate(new_combos):
                    logger.info(f"[Combo-Sort]   combo[{i}]: {c}")
            sorted_layers = topological_sort_combos(new_combos)
            if len(sorted_layers) > 1:
                logger.info(f"[Combo-Sort] 排序后层数: {len(sorted_layers)}")
                for i, layer in enumerate(sorted_layers):
                    logger.info(f"[Combo-Sort]   layer[{i}]: {layer}")
            # === 依赖排序结束 ===
            
            # 在重编号前，先把旧链条里已出现的相同组合删掉，实现"前移"
            pruned_chain = chain
            for c in combos_to_prune:
                pruned_chain = prune_chain_by_combo(pruned_chain, c)
            
            # 重编号：将排序后的层依次设置为顶层序号，原有顶层序号整体顺延
            new_chain = {}
            idx = 1
            for layer in sorted_layers:
                if len(layer) == 1:
                    new_chain[str(idx)] = layer[0]
                else:
                    new_chain[str(idx)] = layer
                idx += 1
            
            for k in sorted(pruned_chain.keys(), key=lambda x: int(x) if str(x).isdigit() else 9999):
                new_chain[str(idx)] = pruned_chain[k]
                idx += 1
            return new_chain, True
        
        # 递归增强：每次只处理新增参数，直到不再产生新的组合为止
        out = copy.deepcopy(merged_results)
        if "cross" not in out:
            return out
        for grp, chains in out["cross"].items():
            # if grp == "shop-cart":
            #     print("a")
            augmented_list = []
            for chain in chains:
                visited_params = set()
                current_chain = copy.deepcopy(chain)
                # 修复：用原链条中已存在的端点初始化 visited_endpoints，避免重复添加
                visited_endpoints = set(collect_chain_endpoints(current_chain))
                # 设置递归深度上限，防止极端情况下无限递归
                rec_limit = 5
                while rec_limit > 0:
                    rec_limit -= 1
                    current_chain, changed = augment_once(current_chain)
                    if not changed:
                        break
                augmented_list.append(current_chain)
            out["cross"][grp] = augmented_list
        return out
        
    # def chains_construction_results(self):
    #     """
    #     返回构建好的依赖链条
    #     """
    #     chain_1 = self.dependency_construction_v1()
    #     # self.jsontools.write_json("/Users/tlif3./zju_research/bolascan_v3/bolascan_v4/cache/chain_1.json", chain_1)
    #     chain_2 = self.dependency_construction_v2()
    #     # self.jsontools.write_json("/Users/tlif3./zju_research/bolascan_v3/bolascan_v4/cache/chain_2.json", chain_2)

    #     merged_results = self.merged_dependencychain(chain_1, chain_2)

    #     dfs_dependency_chain_results = self.dfs_dependency_chain(merged_results)
    #     dfs_dependency_chain_results = self.jsontools.read_json(f"/Users/tlif3./zju_research/bolascan_v3/bolascan_v4/cache/{self.project_name}/dependency_chains_results.json")
    #     remove_duplicated_chains_results = self.remove_duplicated_chains(dfs_dependency_chain_results)
    #     # self.jsontools.write_json("/Users/tlif3./zju_research/bolascan_v3/bolascan_v4/cache/newbee_mall/dfs_dependency_chain_results.json",dfs_dependency_chain_results)
    #     return remove_duplicated_chains_results

    
    def deduplicate_endpoints_within_chain(self, chain):
        """
        在单条依赖链内去除重复出现的端点
        
        策略：智能合并
        - 收集链中所有已出现的端点
        - 如果步骤N的端点已在步骤M(M<N)出现，则移除步骤N中的该端点
        - 如果移除后步骤N为空，则删除该步骤并重新编号
        
        Args:
            chain: 单条依赖链，格式如 {"1": "...", "2": [...], "3": "..."}
        
        Returns:
            deduplicated_chain: 去重后的链
        """
        if not isinstance(chain, dict):
            return chain
        
        seen_endpoints = set()
        deduped_chain = {}
        
        def collect_endpoints(value):
            """递归收集值中的所有端点"""
            endpoints = []
            if isinstance(value, str):
                endpoints.append(value)
            elif isinstance(value, list):
                for item in value:
                    endpoints.extend(collect_endpoints(item))
            elif isinstance(value, dict):
                for subval in value.values():
                    endpoints.extend(collect_endpoints(subval))
            return endpoints
        
        def remove_seen_endpoints(value, seen):
            """从值中移除已见过的端点"""
            if isinstance(value, str):
                return None if value in seen else value
            elif isinstance(value, list):
                filtered = []
                for item in value:
                    result = remove_seen_endpoints(item, seen)
                    if result is not None:
                        if isinstance(result, list):
                            filtered.extend(result)
                        else:
                            filtered.append(result)
                return filtered if filtered else None
            elif isinstance(value, dict):
                filtered_dict = {}
                for k, v in value.items():
                    result = remove_seen_endpoints(v, seen)
                    if result is not None:
                        filtered_dict[k] = result
                return filtered_dict if filtered_dict else None
            return value
        
        # 按步骤顺序处理
        sorted_keys = sorted(chain.keys(), key=lambda x: int(x) if x.isdigit() else 999)
        
        for step_key in sorted_keys:
            step_value = chain[step_key]
            
            # 移除已见过的端点
            cleaned_value = remove_seen_endpoints(step_value, seen_endpoints)
            
            # 如果清理后还有内容，保留该步骤
            if cleaned_value is not None:
                # 收集当前步骤的端点
                current_endpoints = collect_endpoints(step_value)
                seen_endpoints.update(current_endpoints)
                
                deduped_chain[step_key] = cleaned_value
        
        # 重新编号（如果有步骤被删除）
        if len(deduped_chain) != len(chain):
            renumbered = {}
            for idx, (k, v) in enumerate(sorted(deduped_chain.items(), key=lambda x: int(x[0]) if x[0].isdigit() else 999), 1):
                renumbered[str(idx)] = v
            return renumbered
        
        return deduped_chain
    
    def remove_duplicated_chains(self, dfs_chains):
        """
        对传入的 dfs_chains 进行去重，且不改变原有结构和格式。
        - 仅对包含“完整链条字典对象”的列表执行去重（保留首次出现顺序）。
        - 完整链条对象的定义：字典，且所有键为数字字符串（例如 "0","1",...）。
        - 同时在链条子列表（例如某一步骤下的列表）中，若多个链条对象在步骤"1"的内容完全一致，
          则将其按顺序合并为单条链：保留共同的步骤"1"，并将各自后续步骤依次追加为"2","3",...，避免重复。
        - 其他非链条列表、普通字典或原子类型保持原样。
        """
        total_removed = 0

        def _json_key(obj):
            try:
                return json.dumps(obj, sort_keys=True, ensure_ascii=False)
            except Exception:
                return repr(obj)

        def _is_chain_obj(obj):
            """判定是否为一条链条对象：字典且所有键都是数字字符串，且至少有一个键。"""
            return isinstance(obj, dict) and len(obj) > 0 and all(isinstance(k, str) and k.isdigit() for k in obj.keys())

        def _dedup_chain_list(lst):
            """对链条列表进行去重，保留首次出现顺序。非链条列表原样返回。"""
            if not isinstance(lst, list) or not lst:
                return lst
            if not any(_is_chain_obj(e) for e in lst):
                # 列表中不包含链条对象，直接返回
                return lst
            seen = set()
            result = []
            for item in lst:
                if _is_chain_obj(item):
                    # 仅对“链条对象”执行去重；其他类型保持原样（即使重复也不动）
                    key = _json_key(item)
                    if key not in seen:
                        seen.add(key)
                        result.append(item)
                    else:
                        # 统计重复项
                        nonlocal total_removed
                        total_removed += 1
                else:
                    result.append(item)
            return result

        def _merge_by_same_prefix(lst, prefix_key="1"):
            """在链条对象列表中，若多个对象在给定前缀步骤的值完全一致，则合并为单条链。
            例如多个对象都拥有相同的 "1": [...POST 列表...]，但其 "2" 不同，则合并为：
            {"1": [...], "2": objA["2"], "3": objB["2"], ...}
            保持首次出现的位置，合并后的顺序按原列表出现顺序追加，避免重复内容。
            """
            if not isinstance(lst, list) or not lst:
                return lst
            # 仅在列表包含链条对象且这些对象含有 prefix_key 时执行
            chain_indices = [i for i, e in enumerate(lst) if _is_chain_obj(e) and (prefix_key in e)]
            if not chain_indices:
                return lst

            # 分组：按前缀内容的签名
            groups = {}
            for idx in chain_indices:
                item = lst[idx]
                sig = _json_key(item[prefix_key])
                groups.setdefault(sig, []).append((idx, item))

            # 按首个出现位置的索引顺序处理分组，保持稳定性
            new_list = []
            used = set()
            # 构建一个索引到分组键的映射以快速查找
            idx_to_group_key = {idx: _json_key(item[prefix_key]) for idx, item in [(i, lst[i]) for i in chain_indices]}

            for i, elem in enumerate(lst):
                if i in used:
                    continue
                if _is_chain_obj(elem) and (prefix_key in elem):
                    gkey = idx_to_group_key.get(i)
                    members = groups.get(gkey, [])
                    if len(members) > 1:
                        # 合并该分组
                        merged = {prefix_key: elem[prefix_key]}
                        seen_vals = set()
                        step_id = 2

                        def _append_val(v):
                            nonlocal step_id
                            vsig = _json_key(v)
                            if vsig not in seen_vals:
                                seen_vals.add(vsig)
                                merged[str(step_id)] = v
                                step_id += 1

                        # 按出现顺序遍历分组成员，收集除前缀外的所有步骤值
                        for midx, mitem in sorted(members, key=lambda x: x[0]):
                            for k in sorted(mitem.keys(), key=lambda x: int(x) if (isinstance(x, str) and x.isdigit()) else 10**9):
                                if k == prefix_key:
                                    continue
                                _append_val(mitem[k])
                        # 标记该分组所有成员为已用，并统计删除数（合并为一个）
                        for midx, _ in members:
                            used.add(midx)
                        # 用合并后的对象替换首成员位置
                        new_list.append(merged)
                        nonlocal total_removed
                        total_removed += (len(members) - 1)
                    else:
                        used.add(i)
                        new_list.append(elem)
                else:
                    used.add(i)
                    new_list.append(elem)
            return new_list

        def _walk_and_dedup(node, parent_is_chain=False, parent_key=None):
            """递归遍历并在链条列表处执行去重与合并，保持原结构和格式。
            - 若当前节点是列表，且其父节点是“链条对象”且父键为数字字符串（例如步骤"1"下的列表），
              先按相同前缀合并，再做精确去重，然后递归处理其元素。
            - 其他列表仅做精确去重，递归处理。
            - 字典保持原键递归处理值。
            - 原子类型保持不变。
            """
            if isinstance(node, list):
                if parent_is_chain and isinstance(parent_key, str) and parent_key.isdigit():
                    merged = _merge_by_same_prefix(node, prefix_key="1")
                    deduped = _dedup_chain_list(merged)
                    # 新增：对每条链进行内部去重
                    deduped = [self.deduplicate_endpoints_within_chain(chain) for chain in deduped]
                    return [_walk_and_dedup(x, parent_is_chain=False, parent_key=None) for x in deduped]
                else:
                    deduped = _dedup_chain_list(node)
                    return [_walk_and_dedup(x, parent_is_chain=False, parent_key=None) for x in deduped]
            elif isinstance(node, dict):
                # 新增：如果是链条对象，先进行内部去重
                if _is_chain_obj(node):
                    node = self.deduplicate_endpoints_within_chain(node)
                return {k: _walk_and_dedup(v, parent_is_chain=_is_chain_obj(node), parent_key=k) for k, v in node.items()}
            else:
                return node

        processed = _walk_and_dedup(dfs_chains)
        try:
            logger.info(f"remove_duplicated_chains: 去重/合并了 {total_removed} 条链条或组合")
        except Exception:
            print(f"remove_duplicated_chains: 去重/合并了 {total_removed} 条链条或组合")
        return processed
    
    # def add_query_routs_data(self):
    #     pass

    
    def chains_construction_results(self):
        """
        返回构建好的依赖链条
        """
        import os
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
        cache_dir = os.path.join(project_root, 'cache', self.project_name)
        
        chain_1 = self.dependency_construction_v1()
        self.jsontools.write_json(os.path.join(cache_dir, "chain_1.json"), chain_1)
        chain_2 = self.dependency_construction_v2()
        self.jsontools.write_json(os.path.join(cache_dir, "chain_2.json"), chain_2)

        merged_results = self.merged_dependencychain(chain_1, chain_2)
        self.jsontools.write_json(os.path.join(cache_dir, "merged_results.json"), merged_results)

        dfs_dependency_chain_results = self.dfs_dependency_chain(merged_results)
        # dfs_dependency_chain_results = self.jsontools.read_json(os.path.join(cache_dir, "dependency_chains_results.json"))
        remove_duplicated_chains_results = self.remove_duplicated_chains(dfs_dependency_chain_results)

        # query_routes_additional_data = self.add_query_routs_data(remove_duplicated_chains_results)

        # llm_review_chains_results = self.llm_review_chains(remove_duplicated_chains_results)
        # self.jsontools.write_json(os.path.join(cache_dir, "dependency_chains_results.json"), dfs_dependency_chain_results)
        return remove_duplicated_chains_results


# 再多加一个：针对跨功能组下的依赖关系让LLM进行判断，如果不是的话，就直接删除了，可以直接放在foramted的时候
if __name__ == "__main__":
    import json
    import os
    project_name = "newbee_mall_plus"
    jsontools = JsonTools()
    
    # 获取项目根目录
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
    cache_dir = os.path.join(project_root, 'cache', project_name)
    
    api_doc_type_path = os.path.join(cache_dir, "api_doc_with_type.json")
    params_dict = jsontools.read_json(os.path.join(cache_dir, "parameters_dict_all.json"))

    dependencychain = DependencyChain(api_doc_type_path, "gpt-4o-mini", params_dict, project_name)
    jsontools.write_json(os.path.join(cache_dir, "dependency_chains_results.json"), dependencychain.chains_construction_results())



