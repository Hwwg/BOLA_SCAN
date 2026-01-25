
from scripts.api_doc import ApiDoc
from scripts.jsontools import JsonTools
from prompt.synthesis_prompt import SyntheticPrompt
from gptreply.gpt_con import GPTReply

import requests
import itertools
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
import sys,os
import urllib.parse
import json
import io

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


def _make_json_serializable(obj, seen=None):
    """
    递归处理对象，确保所有内容都可 JSON 序列化
    - 处理 BytesIO/StringIO 对象
    - 检测并打破循环引用
    - 处理嵌套字典、列表、元组
    """
    import base64
    
    if seen is None:
        seen = set()
    
    # 检测循环引用
    obj_id = id(obj)
    if obj_id in seen:
        return "<Circular Reference>"
    
    # 处理 IO 对象
    if isinstance(obj, io.BytesIO):
        obj.seek(0)
        return base64.b64encode(obj.read()).decode('utf-8')
    elif isinstance(obj, io.StringIO):
        return obj.getvalue()
    elif isinstance(obj, bytes):
        return base64.b64encode(obj).decode('utf-8')
    
    # 处理容器类型时标记为已访问
    if isinstance(obj, (dict, list, tuple)):
        seen.add(obj_id)
    
    try:
        if isinstance(obj, dict):
            return {key: _make_json_serializable(value, seen) for key, value in obj.items()}
        elif isinstance(obj, list):
            return [_make_json_serializable(item, seen) for item in obj]
        elif isinstance(obj, tuple):
            return tuple(_make_json_serializable(item, seen) for item in obj)
        else:
            return obj
    finally:
        # 清理标记
        if isinstance(obj, (dict, list, tuple)):
            seen.discard(obj_id)


def _serialize_request_params(req_params):
    """
    将请求参数中的不可序列化对象转换为可序列化的格式
    支持文件对象的base64编码，确保后续发包测试时能够恢复
    """
    import base64
    
    serialized_params = {}
    for key, value in req_params.items():
        if key == 'files' and isinstance(value, dict):
            serialized_files = {}
            for file_key, file_value in value.items():
                if isinstance(file_value, tuple) and len(file_value) >= 2:
                    filename, file_obj, *rest = file_value
                    if isinstance(file_obj, io.BytesIO):
                        # 获取文件内容并进行base64编码
                        file_content = file_obj.getvalue()
                        encoded_content = base64.b64encode(file_content).decode('utf-8')
                        serialized_files[file_key] = {
                            "filename": filename,
                            "type": "BytesIO",
                            "size": len(file_content),
                            "content_type": rest[0] if rest else "application/octet-stream",
                            "content_base64": encoded_content,
                            "_serialized": True  # 标记为已序列化
                        }
                    elif isinstance(file_obj, str):
                        # 如果是字符串，可能是文件路径或内容
                        serialized_files[file_key] = {
                            "filename": filename,
                            "type": "string",
                            "content": file_obj,
                            "content_type": rest[0] if rest else "text/plain",
                            "_serialized": True
                        }
                    else:
                        # 其他类型尝试转换为字符串
                        serialized_files[file_key] = {
                            "filename": filename,
                            "type": "other",
                            "content": str(file_obj),
                            "content_type": rest[0] if rest else "text/plain",
                            "_serialized": True
                        }
                elif isinstance(file_value, io.BytesIO):
                    # 直接的BytesIO对象
                    file_content = file_value.getvalue()
                    encoded_content = base64.b64encode(file_content).decode('utf-8')
                    serialized_files[file_key] = {
                        "filename": f"{file_key}.bin",
                        "type": "BytesIO",
                        "size": len(file_content),
                        "content_type": "application/octet-stream",
                        "content_base64": encoded_content,
                        "_serialized": True
                    }
                else:
                    # 其他情况转换为字符串
                    serialized_files[file_key] = {
                        "filename": f"{file_key}.txt",
                        "type": "string",
                        "content": str(file_value),
                        "content_type": "text/plain",
                        "_serialized": True
                    }
            serialized_params[key] = serialized_files
        elif isinstance(value, (io.BytesIO, io.StringIO)):
            # 处理直接的IO对象
            if isinstance(value, io.BytesIO):
                file_content = value.getvalue()
                encoded_content = base64.b64encode(file_content).decode('utf-8')
                serialized_params[key] = {
                    "type": "BytesIO",
                    "size": len(file_content),
                    "content_base64": encoded_content,
                    "_serialized": True
                }
            else:  # StringIO
                serialized_params[key] = {
                    "type": "StringIO",
                    "content": value.getvalue(),
                    "_serialized": True
                }
        else:
            serialized_params[key] = value
    return serialized_params


class CaseGeneration:
    def __init__(self, 
    case_file,
    model_name,
    doc_data,
    dependency_chain_data,
    params_dict,
    project_name,
    debug=False
    ):
        self.jsontools = JsonTools()
        self.case_file = self.jsontools.read_json(case_file)
        self.api_doc = doc_data
        self.dependency_chain = dependency_chain_data
        self.params_dict = params_dict
        self.project_name = project_name
        self.gpt_reply = GPTReply(model_name)
        self.syn_prompt = SyntheticPrompt()
        self.initial_test_info_dict = {
        }
    def case_hadling_from_click_data_initial(self):
        """
        按功能组:接口:参数的层级关系提取HTTP请求的query参数和body参数
        """
        def _extract_json_params(data, extracted_params, prefix):
            """
            递归提取JSON数据中的参数，使用点分隔符展开嵌套对象
            """
            if isinstance(data, dict):
                for key, value in data.items():
                    param_key = key if prefix == "" else f"{prefix}.{key}"
                    if isinstance(value, dict):
                        # 对于嵌套对象，递归提取
                        _extract_json_params(value, extracted_params, param_key)
                    elif isinstance(value, list):
                        # 对于数组，提取第一个元素的参数
                        if value and isinstance(value[0], dict):
                            _extract_json_params(value[0], extracted_params, param_key)
                        elif value:
                            if param_key not in extracted_params:
                                extracted_params[param_key] = value[0]
                    else:
                        # 对于基本类型，直接存储
                        if param_key not in extracted_params:
                            extracted_params[param_key] = value
            elif isinstance(data, list) and data:
                # 对于数组，提取第一个元素的参数
                if isinstance(data[0], dict):
                    _extract_json_params(data[0], extracted_params, prefix)
                else:
                    array_param_key = "array_item" if prefix == "" else f"{prefix}.array_item"
                    if array_param_key not in extracted_params:
                        extracted_params[array_param_key] = data[0]

        def _route_matches(route, endpoint_route):
            """
            检查路由是否匹配，支持路径参数
            """
            # 直接匹配
            if route == endpoint_route:
                return True

            # 处理路径参数匹配，如 /identity/api/v2/user/videos/123 匹配 /identity/api/v2/user/videos/{video_id}
            route_parts = route.split('/')
            endpoint_parts = endpoint_route.split('/')

            if len(route_parts) != len(endpoint_parts):
                return False

            for route_part, endpoint_part in zip(route_parts, endpoint_parts):
                # 如果endpoint部分是路径参数（用{}包围），则跳过比较
                if endpoint_part.startswith('{') and endpoint_part.endswith('}'):
                    continue
                # 否则必须完全匹配
                if route_part != endpoint_part:
                    return False

            return True

        def _get_functional_group_by_route(route):
            """
            根据API路由获取对应的功能组，动态从api_doc_with_types.json读取
            """
            try:
                # 读取api_doc_with_types.json文件
                api_doc_data = self.api_doc

                # 遍历所有功能组，查找匹配的路由
                for group_obj in api_doc_data:
                    for functional_group, endpoints in group_obj.items():
                        for endpoint_key in endpoints.keys():
                            # 提取路由部分（去掉HTTP方法）
                            endpoint_route = endpoint_key.split(' ', 1)[1] if ' ' in endpoint_key else endpoint_key

                            # 检查路由是否匹配
                            if _route_matches(route, endpoint_route):
                                return functional_group

                return 'Other'
            except Exception as e:
                print(f"Error reading api_doc_with_types.json: {e}")
                return 'Other'

        # 用于存储按功能组分组的参数: {功能组: {接口: {参数}}}
        functional_groups_params = {}

        for case in self.case_file["allRequests"]:
            # 处理请求和响应类型的数据
            if case.get("type") not in ["request", "response"]:
                continue

            # 获取API路由和HTTP方法
            api_route = ""
            http_method = case.get("method", "GET").upper()

            if "url" in case:
                parsed_url = urllib.parse.urlparse(case["url"])
                api_route = parsed_url.path

                # 如果路由为空，跳过
                if not api_route:
                    continue

                # 获取功能组
                functional_group = _get_functional_group_by_route(api_route)

                # 构建完整的接口标识 (HTTP方法 + 路由)
                full_api_key = f"{http_method} {api_route}"

                # 初始化功能组和接口的参数字典
                if functional_group not in functional_groups_params:
                    functional_groups_params[functional_group] = {}
                if full_api_key not in functional_groups_params[functional_group]:
                    functional_groups_params[functional_group][full_api_key] = {}

                # 提取URL中的query参数
                if parsed_url.query:
                    query_params = urllib.parse.parse_qs(parsed_url.query)
                    for param_name, param_values in query_params.items():
                        if param_name not in functional_groups_params[functional_group][full_api_key]:
                            # 取第一个值
                            functional_groups_params[functional_group][full_api_key][param_name] = param_values[
                                0] if param_values else ""

            # 如果没有获取到路由，跳过后续处理
            if not api_route:
                continue

            # 提取body中的参数
            if "body" in case and case["body"]:
                try:
                    # 尝试解析JSON格式的body
                    body_data = json.loads(case["body"])
                    _extract_json_params(body_data, functional_groups_params[functional_group][full_api_key], "")
                except json.JSONDecodeError:
                    # 如果不是JSON格式，尝试解析form-data格式
                    if "application/x-www-form-urlencoded" in case.get("headers", {}).get("content-type", ""):
                        try:
                            form_params = urllib.parse.parse_qs(case["body"])
                            for param_name, param_values in form_params.items():
                                if param_name not in functional_groups_params[functional_group][full_api_key]:
                                    functional_groups_params[functional_group][full_api_key][param_name] = param_values[
                                        0] if param_values else ""
                        except Exception:
                            pass

            # 提取响应数据中的jsonData参数
            if "jsonData" in case and case["jsonData"]:
                _extract_json_params(case["jsonData"], functional_groups_params[functional_group][full_api_key], "")

        return functional_groups_params
    
    def case_hadling_from_click_data(self,case_hadling_from_click_data_resutls):
        """
        按功能组:接口:参数的层级关系提取HTTP请求的query参数和body参数
        """
        def _extract_json_params(data, extracted_params, prefix):
            """
            递归提取JSON数据中的参数，使用点分隔符展开嵌套对象
            """
            if isinstance(data, dict):
                for key, value in data.items():
                    param_key = key if prefix == "" else f"{prefix}.{key}"
                    if isinstance(value, dict):
                        # 对于嵌套对象，递归提取
                        _extract_json_params(value, extracted_params, param_key)
                    elif isinstance(value, list):
                        # 对于数组，提取第一个元素的参数
                        if value and isinstance(value[0], dict):
                            _extract_json_params(value[0], extracted_params, param_key)
                        elif value:
                            if param_key not in extracted_params:
                                extracted_params[param_key] = value[0]
                    else:
                        # 对于基本类型，直接存储
                        if param_key not in extracted_params:
                            extracted_params[param_key] = value
            elif isinstance(data, list) and data:
                # 对于数组，提取第一个元素的参数
                if isinstance(data[0], dict):
                    _extract_json_params(data[0], extracted_params, prefix)
                else:
                    array_param_key = "array_item" if prefix == "" else f"{prefix}.array_item"
                    if array_param_key not in extracted_params:
                        extracted_params[array_param_key] = data[0]
        
        def _route_matches(route, endpoint_route):
            """
            检查路由是否匹配，支持路径参数
            """
            # 直接匹配
            if route == endpoint_route:
                return True
            
            # 处理路径参数匹配，如 /identity/api/v2/user/videos/123 匹配 /identity/api/v2/user/videos/{video_id}
            route_parts = route.split('/')
            endpoint_parts = endpoint_route.split('/')
            
            if len(route_parts) != len(endpoint_parts):
                return False
            
            for route_part, endpoint_part in zip(route_parts, endpoint_parts):
                # 如果endpoint部分是路径参数（用{}包围），则跳过比较
                if endpoint_part.startswith('{') and endpoint_part.endswith('}'):
                    continue
                # 否则必须完全匹配
                if route_part != endpoint_part:
                    return False
            
            return True
        
        def _get_functional_group_by_route(route):
            """
            根据API路由获取对应的功能组，动态从api_doc_with_types.json读取
            """
            try:
                # 读取api_doc_with_types.json文件
                api_doc_data = self.api_doc
                
                # 遍历所有功能组，查找匹配的路由
                for group_obj in api_doc_data:
                    for functional_group, endpoints in group_obj.items():
                        for endpoint_key in endpoints.keys():
                            # 提取路由部分（去掉HTTP方法）
                            endpoint_route = endpoint_key.split(' ', 1)[1] if ' ' in endpoint_key else endpoint_key
                            
                            # 检查路由是否匹配
                            if _route_matches(route, endpoint_route):
                                return functional_group
                
                return 'Other'
            except Exception as e:
                print(f"Error reading api_doc_with_types.json: {e}")
                return 'Other'
                
        def _is_valid_path_param_value(value):
            """验证路径参数值是否合法（数字或UUID格式），排除明显的路径关键字"""
            if isinstance(value, (int, float)):
                return True
            if isinstance(value, str):
                # 排除明显的路径片段/关键字
                invalid_keywords = [
                    'recent', 'all', 'latest', 'list', 'new', 'create', 'update', 
                    'delete', 'edit', 'add', 'remove', 'index', 'home', 'first',
                    'last', 'next', 'prev', 'previous', 'current', 'default',
                    'me', 'self', 'admin', 'user', 'api', 'v1', 'v2', 'v3'
                ]
                if value.lower() in invalid_keywords:
                    return False
                # 纯数字
                if value.isdigit():
                    return True
                # UUID 格式（简化检查）
                import re
                uuid_pattern = re.compile(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')
                if uuid_pattern.match(value):
                    return True
                # 包含数字的混合字符串（如 order_123, item-456）也可能是有效ID
                if re.search(r'\d', value) and len(value) <= 50:
                    return True
            return False

        def _extract_path_params_from_doc(actual_route, http_method):
            """
            基于 API 文档中的占位符（{param}）与点击采集到的实际路由，提取路径参数键值对。
            仅当文档路由与实际路由匹配时才提取；当方法不匹配则跳过。
            """
            try:
                api_doc_data = self.api_doc
                actual_parts_cache = actual_route.strip('/').split('/')
                for group_obj in api_doc_data:
                    for _fg, endpoints in group_obj.items():
                        for endpoint_key in endpoints.keys():
                            if ' ' in endpoint_key:
                                method, endpoint_route = endpoint_key.split(' ', 1)
                            else:
                                method, endpoint_route = None, endpoint_key
                            if method and method.upper() != http_method:
                                continue
                            if _route_matches(actual_route, endpoint_route):
                                mapping = {}
                                ep_parts = endpoint_route.strip('/').split('/')
                                for ap, ep in zip(actual_parts_cache, ep_parts):
                                    ep = ep.strip()
                                    if ep.startswith('{') and ep.endswith('}'):
                                        name = ep[1:-1].strip()
                                        # 【关键修复】验证路径参数值是否合法，跳过无效值（如 recent, all 等）
                                        if name and _is_valid_path_param_value(ap):
                                            mapping[name] = ap
                                return mapping
            except Exception:
                pass
            return {}
                
        # 用于存储按功能组分组的参数: {功能组: {接口: {参数}}}
        functional_groups_params = {}
        
        # 若传入的是已分组或“平铺”的点击参数结果，统一进行路径参数补全并按文档重组功能组
        if isinstance(case_hadling_from_click_data_resutls, dict) and all(isinstance(v, dict) for v in case_hadling_from_click_data_resutls.values()):
            try:
                # 收集所有接口键（无论是顶层还是嵌套在功能组下）
                flat_endpoints = {}
                for top_key, inner_map in case_hadling_from_click_data_resutls.items():
                    if isinstance(top_key, str) and ' ' in top_key:
                        # 顶层就是接口键
                        method, route = top_key.split(' ', 1)
                        params_map = inner_map if isinstance(inner_map, dict) else {}
                        merged = dict(params_map)
                        path_params = _extract_path_params_from_doc(route, method.upper())
                        for pname, pval in (path_params or {}).items():
                            if pname not in merged or merged[pname] in (None, ""):
                                merged[pname] = pval
                        flat_endpoints[top_key] = merged
                    elif isinstance(inner_map, dict):
                        # 顶层是功能组（如 Other），其内部为接口键
                        for full_api_key, params_map in inner_map.items():
                            if isinstance(full_api_key, str):
                                method, route = full_api_key.split(' ', 1) if ' ' in full_api_key else ('', full_api_key)
                                merged = dict(params_map) if isinstance(params_map, dict) else {}
                                path_params = _extract_path_params_from_doc(route, method.upper() if method else '')
                                for pname, pval in (path_params or {}).items():
                                    if pname not in merged or merged[pname] in (None, ""):
                                        merged[pname] = pval
                                flat_endpoints[full_api_key] = merged
                
                # 基于 API 文档重新计算功能组映射
                grouped = {}
                for full_api_key, params_map in flat_endpoints.items():
                    method, route = full_api_key.split(' ', 1) if ' ' in full_api_key else ('', full_api_key)
                    fg = _get_functional_group_by_route(route)
                    if fg not in grouped:
                        grouped[fg] = {}
                    grouped[fg][full_api_key] = params_map
                return grouped
            except Exception:
                # 若补全过程失败，返回原始结构
                return case_hadling_from_click_data_resutls

        # 否则从传入的原始点击数据中提取（优先使用参数中的数据源）
        if isinstance(case_hadling_from_click_data_resutls, dict) and "allRequests" in case_hadling_from_click_data_resutls:
            raw_requests = case_hadling_from_click_data_resutls.get("allRequests", [])
        elif isinstance(case_hadling_from_click_data_resutls, list):
            raw_requests = case_hadling_from_click_data_resutls
        else:
            raw_requests = self.case_file.get("allRequests", [])
        
        for case in raw_requests:
            # 处理请求和响应类型的数据
            if case.get("type") not in ["request", "response"]:
                continue
            
            # 获取API路由和HTTP方法
            api_route = ""
            http_method = case.get("method", "GET").upper()
            
            if "url" in case:
                parsed_url = urllib.parse.urlparse(case["url"])
                api_route = parsed_url.path
                
                # 如果路由为空，跳过
                if not api_route:
                    continue
                
                # 获取功能组
                functional_group = _get_functional_group_by_route(api_route)
                
                # 构建完整的接口标识 (HTTP方法 + 路由)
                full_api_key = f"{http_method} {api_route}"
                
                # 初始化功能组和接口的参数字典
                if functional_group not in functional_groups_params:
                    functional_groups_params[functional_group] = {}
                if full_api_key not in functional_groups_params[functional_group]:
                    functional_groups_params[functional_group][full_api_key] = {}
                
                # 提取URL中的query参数
                if parsed_url.query:
                    query_params = urllib.parse.parse_qs(parsed_url.query)
                    for param_name, param_values in query_params.items():
                        if param_name not in functional_groups_params[functional_group][full_api_key]:
                            # 取第一个值
                            functional_groups_params[functional_group][full_api_key][param_name] = param_values[0] if param_values else ""
                
                # 提取路径参数（基于 API 文档）
                path_params = _extract_path_params_from_doc(api_route, http_method)
                if isinstance(path_params, dict):
                    for pname, pval in path_params.items():
                        if pname not in functional_groups_params[functional_group][full_api_key]:
                            functional_groups_params[functional_group][full_api_key][pname] = pval
            
            # 如果没有获取到路由，跳过后续处理
            if not api_route:
                continue
            
            # 提取body中的参数
            if "body" in case and case["body"]:
                try:
                    # 尝试解析JSON格式的body
                    body_data = json.loads(case["body"])
                    _extract_json_params(body_data, functional_groups_params[functional_group][full_api_key], "")
                except json.JSONDecodeError:
                    # 如果不是JSON格式，尝试解析form-data格式
                    if "application/x-www-form-urlencoded" in case.get("headers", {}).get("content-type", ""):
                        try:
                            form_params = urllib.parse.parse_qs(case["body"])
                            for param_name, param_values in form_params.items():
                                if param_name not in functional_groups_params[functional_group][full_api_key]:
                                    functional_groups_params[functional_group][full_api_key][param_name] = param_values[0] if param_values else ""
                        except Exception:
                            pass
            
            # 提取响应数据中的jsonData参数
            if "jsonData" in case and case["jsonData"]:
                _extract_json_params(case["jsonData"], functional_groups_params[functional_group][full_api_key], "")
        
        return functional_groups_params

    def dependency_chain_with_parameters(self):
        merged_chains = self.dependency_chain
        # 当未传入依赖链或为空时，回退到默认缓存路径读取
        # if not isinstance(merged_chains, dict) or not merged_chains:
        #     try:
        #         fallback_path = "/Users/tlif3./zju_research/bolascan_v3/bolascan_v4/cache/newbee_mall/dependency_chains_results.json"
        #         merged_chains = self.jsontools.read_json(fallback_path)
        #     except Exception:
        #         merged_chains = {}
        api_doc = self.api_doc
        
        # 构建API文档的快速查找字典（适配多层嵌套的文档结构）
        api_lookup = {}
        
        for category in api_doc:
            if isinstance(category, dict):
                for category_name, apis in category.items():
                    if isinstance(apis, dict):
                        for api_path, api_info in apis.items():
                            # 拷贝一份，避免后续意外修改原对象
                            api_lookup[api_path] = dict(api_info) if isinstance(api_info, dict) else {"raw": api_info}
                            api_lookup[api_path]["functional_group"] = category_name
        
        # 生成参数的默认值（递归支持 object/array 的嵌套）
        def default_value_by_type(param_info):
            t = None
            if isinstance(param_info, dict):
                t = param_info.get("type")
                # 兼容 schema 场景
                if not t and isinstance(param_info.get("schema"), dict):
                    return default_value_by_type(param_info["schema"])
            
            if t == "string":
                return ""
            if t in ("integer", "number", "long", "float", "double"):
                return 0
            if t == "boolean":
                return False
            if t == "array":
                items = param_info.get("items") if isinstance(param_info, dict) else None
                item_default = default_value_by_type(items) if items else ""
                return [item_default]
            if t == "object":
                # 根据 properties 递归生成嵌套对象
                props = {}
                if isinstance(param_info, dict):
                    properties = param_info.get("properties", {})
                    if isinstance(properties, dict) and properties:
                        for pname, pinfo in properties.items():
                            props[pname] = default_value_by_type(pinfo)
                    else:
                        ap = param_info.get("additionalProperties")
                        if ap:
                            props["key"] = default_value_by_type(ap)
                return props
            # 未知类型，兜底为字符串
            return ""
        
        # 将请求参数按位置分组，并保留嵌套结构
        def build_params_grouped(request_params):
            grouped = {}

            import re

            def parse_segments(name: str):
                segs = []
                parts = [seg for seg in str(name).split(".") if seg]
                for p in parts:
                    m = re.match(r'^([^\[\]]+)(?:\[(\d*)\])?$', p)
                    if m:
                        key = m.group(1)
                        bracket = m.group(2)
                        if bracket is None:
                            segs.append({"key": key})
                        elif bracket == "":
                            # items[] 无索引，表示数组容器
                            segs.append({"key": key, "is_array": True})
                        else:
                            # items[0] 带明确索引
                            try:
                                segs.append({"key": key, "index": int(bracket)})
                            except Exception:
                                segs.append({"key": key, "is_array": True})
                    else:
                        segs.append({"key": p})
                return segs

            def set_nested_with_arrays(target: dict, segments: list, value,):
                cur = target
                for i, seg in enumerate(segments):
                    is_last = (i == len(segments) - 1)
                    key = seg["key"]
                    is_array = seg.get("is_array", False)
                    has_index = ("index" in seg)

                    if is_array or has_index:
                        # 需要确保为数组
                        if key not in cur or not isinstance(cur.get(key), list):
                            cur[key] = []
                        idx = seg.get("index", 0)
                        while len(cur[key]) <= idx:
                            # 预创建元素，若是最后一段且值是原始类型，则用 None 占位，后续直接赋值
                            pre = {} if (not is_last or isinstance(value, (dict, list))) else None
                            cur[key].append(pre)
                        if is_last:
                            # 叶子为数组容器（如 cartItemIds[]）且值为原始类型 -> 直接设置为 [value]
                            if is_array and len(segments) == 1 and not isinstance(value, (dict, list)):
                                cur[key] = [value]
                            else:
                                # 叶子为具体索引元素或数组元素下的字段 -> 设置该索引的值
                                cur_elem = cur[key][idx]
                                if isinstance(value, (dict, list)):
                                    cur[key][idx] = value
                                else:
                                    # 如果元素是字典占位，且叶子就是该元素本身（例如 items[0]），则直接赋值；
                                    # 若还有下层字段，解析时不会走到这里，因此这里直接覆盖索引元素。
                                    cur[key][idx] = value
                        else:
                            # 中间段数组，下降到指定索引元素
                            if cur[key][idx] is None or not isinstance(cur[key][idx], dict):
                                cur[key][idx] = {}
                            cur = cur[key][idx]
                    else:
                        # 普通对象键路径
                        if is_last:
                            cur[key] = value
                        else:
                            if key not in cur or not isinstance(cur[key], dict):
                                cur[key] = {}
                            cur = cur[key]

            if isinstance(request_params, dict):
                for param_name, param_info in request_params.items():
                    # 参数位置与默认值
                    if not isinstance(param_info, dict):
                        location = "body"
                        value = ""
                    else:
                        location = param_info.get("in", "body")
                        value = default_value_by_type(param_info)

                    if location not in grouped:
                        grouped[location] = {}

                    # 解析支持：点号嵌套 + 中括号数组（items[]/items[0]）
                    name_str = param_name if isinstance(param_name, str) else str(param_name)
                    segments = parse_segments(name_str)
                    set_nested_with_arrays(grouped[location], segments, value)
            return grouped
        
        # 适配多层嵌套的依赖链结构，统一生成带参数的链
        result = {}
        
        def process_categories(categories_dict, bucket):
            # categories_dict: { category_name: [chains...] }
             for category_name, chains in categories_dict.items():
                 if not isinstance(chains, list):
                     continue
                 for chain in chains:
                     chain_with_params = {}
                     
                     # 统一为步骤字典
                     if not isinstance(chain, dict):
                         chain_steps = {"1": chain}
                     else:
                         chain_steps = chain
                     
                     # 按数字字符串排序，保证步骤顺序
                     def sort_key(k):
                         return int(k) if str(k).isdigit() else str(k)
                     
                     for step_key in sorted(chain_steps.keys(), key=sort_key):
                         step_value = chain_steps[step_key]
                         
                         # 将该步骤构造成嵌套的编号字典，不再拍平成列表
                         step_map = {}
                         
                         def build_api_request(api):
                             # 构建请求体格式
                             try:
                                 method, route = api.split()[0], api.split()[1]
                             except Exception:
                                 method, route = "UNKNOWN", str(api)
                             api_key = f"{method}：{route}"
                             api_request = {
                                 api_key: {
                                     "route": route,
                                     "method": method,
                                     "type": api_lookup.get(f"{method} {route}", {}).get("type", "unknown"),
                                     "request_params": []
                                 }
                             }
                             
                             # 提取并分组请求参数（嵌套支持）
                             request_params = api_lookup.get(f"{method} {route}", {}).get("request_parameters", {})
                             grouped = build_params_grouped(request_params)
                             for location, params in grouped.items():
                                 if params:
                                     api_request[api_key]["request_params"].append({
                                         "type": location,
                                         "parameters": params
                                     })
                             return api_request
                         
                         if isinstance(step_value, str):
                             # 单接口步骤：直接使用 API 映射（不强制内层编号）
                             chain_with_params[step_key] = build_api_request(step_value)
                         elif isinstance(step_value, list):
                             # 多接口步骤可能是字符串或字典的列表，需要分别处理
                             idx = 1
                             for item in step_value:
                                 if isinstance(item, str):
                                     step_map[str(idx)] = build_api_request(item)
                                     idx += 1
                                 elif isinstance(item, dict):
                                     # 列表中的字典：遍历其内部编号，逐一展开为同级编号
                                     for nk in sorted(item.keys(), key=sort_key):
                                         api_inner = item[nk]
                                         if isinstance(api_inner, list):
                                             # 内部仍是列表，继续逐一展开
                                             for api in api_inner:
                                                 step_map[str(idx)] = build_api_request(api)
                                                 idx += 1
                                         else:
                                             step_map[str(idx)] = build_api_request(api_inner)
                                             idx += 1
                                 else:
                                     # 非预期类型，跳过
                                     continue
                             chain_with_params[step_key] = step_map
                         elif isinstance(step_value, dict):
                             # 已有内层编号的步骤：保留编号
                             for nk in sorted(step_value.keys(), key=sort_key):
                                 api_val = step_value[nk]
                                 if isinstance(api_val, list):
                                     # 若内层是列表，为其子编号 1..m
                                     sub_map = {}
                                     for j, api in enumerate(api_val, start=1):
                                         sub_map[str(j)] = build_api_request(api)
                                     step_map[str(nk)] = sub_map
                                 else:
                                     step_map[str(nk)] = build_api_request(api_val)
                             chain_with_params[step_key] = step_map
                         else:
                             # 非预期结构，跳过
                             continue
                     
                     # 如果链中有接口，按原始分类名（category_name）归纳，保留 cross 下的组名
                     if chain_with_params:
                         if category_name:
                             if category_name not in bucket:
                                 bucket[category_name] = []
                             bucket[category_name].append(chain_with_params)
                         else:
                             # 如果没有分类名，尝试按第一个接口所属功能组归类；否则归入 unknown
                             first_api_identifier = None
                             for sk in sorted(chain_with_params.keys(), key=sort_key):
                                 step_apis = chain_with_params[sk]
                                 # 兼容多层嵌套，提取第一个接口键
                                 def extract_first_api_key(obj):
                                     if isinstance(obj, dict):
                                         for k, v in obj.items():
                                             if isinstance(k, str) and '：' in k:
                                                 return k
                                             res = extract_first_api_key(v)
                                             if res:
                                                 return res
                                     return None
                                 api_key_str = extract_first_api_key(step_apis)
                                 if api_key_str:
                                     try:
                                         method = api_key_str.split('：')[0]
                                         route = api_key_str.split('：')[1]
                                         api_identifier = f"{method} {route}"
                                         if api_identifier in api_lookup:
                                             first_api_identifier = api_identifier
                                             break
                                     except Exception:
                                         pass
                             if first_api_identifier and first_api_identifier in api_lookup:
                                 fg = api_lookup[first_api_identifier].get('functional_group')
                                 if fg:
                                     if fg not in bucket:
                                         bucket[fg] = []
                                     bucket[fg].append(chain_with_params)
                                 else:
                                     if 'unknown' not in bucket:
                                         bucket['unknown'] = []
                                     bucket['unknown'].append(chain_with_params)
                             else:
                                 if 'unknown' not in bucket:
                                     bucket['unknown'] = []
                                 bucket['unknown'].append(chain_with_params)

        # 处理依赖链顶层可能的额外嵌套（例如 cross / group -> {category_name: chains}）
        if isinstance(merged_chains, dict):
            for top_key, top_val in merged_chains.items():
                sub_result = {}
                if isinstance(top_val, dict):
                    # 二级字典：{category_name: [chains...]}
                    process_categories(top_val, sub_result)
                elif isinstance(top_val, list):
                    # 一级直接是分类：{category_name: [chains...]}
                    process_categories({top_key: top_val}, sub_result)
                result[top_key] = sub_result
        
        return result
    
    def add_type_api_packages(self,dependency_chain_with_para_dict,case_hadling_from_click_data_dict):
        """
        按照新的依赖链嵌套结构（保留嵌套的步骤字典与列表）为每个 API 的请求参数进行赋值，
        并保持与输入的 dependency_chain_with_para_dict 相同的嵌套格式输出。
        支持顶层桶结构（cross/group/cross）与直接功能组结构两种形式。
        """
        # 读取相关数据
        api_results = dependency_chain_with_para_dict
        param_values = case_hadling_from_click_data_dict

        def _normalize_key(k: str) -> str:
            try:
                return "".join(ch.lower() for ch in str(k) if ch.isalnum())
            except Exception:
                return str(k).lower()

        def _find_param_in_functional_group(param_name, group_params):
            """在功能组的所有接口参数中查找指定参数值，支持点号展开匹配（大小写不敏感）。"""
            if not isinstance(group_params, dict):
                return None
            pn = _normalize_key(param_name)
            for _, route_params in group_params.items():
                if not isinstance(route_params, dict):
                    continue
                # 直接匹配
                for k, v in route_params.items():
                    if _normalize_key(k) == pn:
                        return v
                # 点号展开匹配（如 posts.title 匹配 title）
                for key, value in route_params.items():
                    if isinstance(key, str) and '.' in key:
                        if _normalize_key(key.split('.')[-1]) == pn:
                            return value
            return None

        def _enrich_api_data(api_data, group_params):
            """为单个 API 数据的请求参数填充值（保持原结构）。"""
            if not isinstance(api_data, dict):
                return api_data
            params = api_data.get("request_params", [])
            enriched_params = []
            if isinstance(params, list):
                for param_group in params:
                    if not isinstance(param_group, dict):
                        enriched_params.append(param_group)
                        continue
                    ptype = param_group.get("type")
                    pmap = param_group.get("parameters", {})
                    enriched_map = {}
                    if isinstance(pmap, dict):
                        for pname, pval in pmap.items():
                            found = _find_param_in_functional_group(pname, group_params)
                            enriched_map[pname] = pval if (found is None) else found
                    enriched_params.append({"type": ptype, "parameters": enriched_map})
            else:
                enriched_params = params
            # 保持原字段，并补全响应参数字段
            api_data["request_params"] = enriched_params
            if "response_params" not in api_data:
                api_data["response_params"] = []
            return api_data

        def enrich_node(node, group_params):
            """递归为链条节点赋值，保留原有嵌套结构。"""
            # 列表：可能是 API 映射列表或嵌套结构列表
            if isinstance(node, list):
                out = []
                for item in node:
                    if isinstance(item, dict):
                        out.append(enrich_node(item, group_params))
                    else:
                        out.append(item)
                return out
            # 字典：可能是步骤字典或 API 键映射
            if isinstance(node, dict):
                values = list(node.values())
                # 判断是否为叶子层：api_key -> api_data
                is_leaf_map = (len(values) > 0) and all(
                    isinstance(v, dict) and (
                        ("route" in v) or ("method" in v) or ("request_params" in v) or ("type" in v)
                    ) for v in values
                )
                if is_leaf_map:
                    enriched_map = {}
                    for api_key, api_data in node.items():
                        enriched_map[api_key] = _enrich_api_data(api_data, group_params)
                    return enriched_map
                # 非叶子：继续递归处理步骤嵌套
                nested = {}
                # 按数字顺序排序键（若可），保持步骤序
                def sort_key(k):
                    # 统一返回可比较的键：数字键优先，其次按字典序
                    if isinstance(k, int) or (isinstance(k, str) and k.isdigit()):
                        try:
                            return (0, int(k))
                        except Exception:
                            return (0, 0)
                    return (1, str(k))
                for k in sorted(node.keys(), key=sort_key):
                    nested[k] = enrich_node(node[k], group_params)
                return nested
            # 其他类型（字符串等），直接返回
            return node

        result = {}
        # 兼容两种输入结构：
        # 1) 顶层包含 'cross'/'group'/'cross' 的分桶结构：{"cross": {fg: [...]}, "group": {fg: [...]}}
        # 2) 直接按功能组归纳的结构：{fg: [...]}
        if isinstance(api_results, dict) and ("cross" in api_results or "group" in api_results or "cross" in api_results):
            for bucket_key, groups_dict in api_results.items():
                if not isinstance(groups_dict, dict):
                    continue
                result[bucket_key] = {}
                for functional_group, chains in groups_dict.items():
                    group_params = param_values.get(functional_group, {})
                    if isinstance(chains, list):
                        result[bucket_key][functional_group] = [enrich_node(chain, group_params) for chain in chains]
                    else:
                        result[bucket_key][functional_group] = enrich_node(chains, group_params)
        else:
            for functional_group, chains in api_results.items():
                group_params = param_values.get(functional_group, {})
                if isinstance(chains, list):
                    result[functional_group] = [enrich_node(chain, group_params) for chain in chains]
                else:
                    result[functional_group] = enrich_node(chains, group_params)

        return result

    def add_parameters_from_click_data(self,add_type_api_packages_results,case_hadling_from_click_data_resutls):
        """
        遍历 add_type_api_packages_results，为每个接口的请求参数在 http-requests.json 中找到同名参数并赋值，
        返回赋值后的 add_type_api_packages_results（结构保持一致）。
        """
        # 从点击采集的 http-requests.json 中提取各功能组的参数映射
        click_params_by_group = self.case_hadling_from_click_data(case_hadling_from_click_data_resutls)

        def _normalize_key(k: str) -> str:
            try:
                return "".join(ch.lower() for ch in str(k) if ch.isalnum())
            except Exception:
                return str(k).lower()

        def _find_param_in_group(param_name, group_params):
            """在功能组提取的所有接口参数中查找指定参数名的值，支持点号展开的最后一段匹配（大小写不敏感）。"""
            if not isinstance(group_params, dict):
                return None
            pn = _normalize_key(param_name)
            for _, route_params in group_params.items():
                if not isinstance(route_params, dict):
                    continue
                # 直接匹配
                for k, v in route_params.items():
                    if _normalize_key(k) == pn:
                        return v
                # 点号展开匹配（如 posts.title 匹配 title）
                for k, v in route_params.items():
                    if isinstance(k, str) and '.' in k:
                        if _normalize_key(k.split('.')[-1]) == pn:
                            return v
            return None

        def _collect_values_by_group(param_name):
            """汇总所有功能组中该参数名的取值集合（去除 None 与空串，大小写不敏感）。"""
            values_by_group = {}
            if not isinstance(click_params_by_group, dict):
                return values_by_group
            pn = _normalize_key(param_name)
            for group_name, route_map in click_params_by_group.items():
                if not isinstance(route_map, dict):
                    continue
                vals = []
                for _, route_params in route_map.items():
                    if not isinstance(route_params, dict):
                        continue
                    # 直接键匹配
                    for k, v in route_params.items():
                        if _normalize_key(k) == pn:
                            if v is not None and not (isinstance(v, str) and v.strip() == ""):
                                vals.append(v)
                    # 点号展开匹配（最后一段）
                    for k, v in route_params.items():
                        if not isinstance(k, str):
                            continue
                        if '.' in k and _normalize_key(k.split('.')[-1]) == pn:
                            if v is not None and not (isinstance(v, str) and v.strip() == ""):
                                vals.append(v)
                if vals:
                    values_by_group[group_name] = set(vals)
            return values_by_group

        def _find_common_value_across_groups(param_name):
            """
            全局兜底：当当前功能组未命中时，从所有功能组的该键值中寻找可用值。
            规则：
            - 若仅一个功能组包含该键：
              * 若该组仅有一个值，取之；
              * 若该组有多个值，取其中一个稳定值（按字符串化排序的第一个）。
            - 若多个功能组包含该键：
              * 若所有组的值完全一致（并集大小为 1），取该值；
              * 否则，选择在至少两个不同功能组中同时出现的值（出现次数降序，字典序稳定）。
              * 若不存在跨组同时出现的值，则不取（返回 None）。
            """
            values_by_group = _collect_values_by_group(param_name)
            if not values_by_group:
                return None
            if len(values_by_group) == 1:
                only_vals = next(iter(values_by_group.values()))
                if not only_vals:
                    return None
                # 单组情况下选择一个稳定值（保持确定性）
                try:
                    return sorted(list(only_vals), key=lambda x: (str(x))) [0]
                except Exception:
                    return next(iter(only_vals))

            # 多组：若并集只有一个值，视为一致
            all_vals = set()
            for s in values_by_group.values():
                all_vals.update(s)
            if len(all_vals) == 1:
                return next(iter(all_vals))

            # 计算值在不同功能组中的出现次数
            value_counts = {}
            for s in values_by_group.values():
                for v in s:
                    value_counts[v] = value_counts.get(v, 0) + 1

            # 候选：至少两个不同功能组出现过的值
            candidates = [v for v, c in value_counts.items() if c >= 2]
            if not candidates:
                return None
            # 选择出现次数最高的值，次数相同则按字符串稳定排序
            candidates.sort(key=lambda v: (-value_counts[v], str(v)))
            return candidates[0]

        def _enrich_api_data(api_data, group_params):
            """为单个 API 数据的请求参数填充值（保持原结构）。"""
            if not isinstance(api_data, dict):
                return api_data
            # 兼容外层映射：自动解包单项接口映射，如 {"METHOD：/route": { ... }}
            if "request_params" not in api_data:
                try:
                    if isinstance(api_data, dict) and len(api_data) == 1:
                        inner = next(iter(api_data.values()))
                        if isinstance(inner, dict) and ("request_params" in inner or "method" in inner or "route" in inner):
                            api_data = inner
                except Exception:
                    pass
            # if api_data.get("route", "") == "/api/v1/seckill/orders/{seckillSuccessId}/{userId}/{seckillSecretKey}":
            #     print("ok")
            params = api_data.get("request_params", [])
            enriched_params = []
            if isinstance(params, list):
                for param_group in params:
                    if not isinstance(param_group, dict):
                        enriched_params.append(param_group)
                        continue
                    ptype = param_group.get("type")
                    pmap = param_group.get("parameters", {})
                    enriched_map = {}
                    if isinstance(pmap, dict):
                        for pname, pval in pmap.items():
                            found = _find_param_in_group(pname, group_params)
                            if found is None:
                                found = _find_common_value_across_groups(pname)
                            new_val = pval if (found is None) else found
                            try:
                                if isinstance(pval, list):
                                    if not isinstance(new_val, list):
                                        new_val = [new_val]
                                    if pval:
                                        elem = pval[0]
                                        if isinstance(elem, int):
                                            def _to_int(x):
                                                try:
                                                    return int(x)
                                                except Exception:
                                                    return x
                                            new_val = [_to_int(x) for x in new_val]
                                        elif isinstance(elem, float):
                                            def _to_float(x):
                                                try:
                                                    return float(x)
                                                except Exception:
                                                    return x
                                            new_val = [_to_float(x) for x in new_val]
                                        elif isinstance(elem, bool):
                                            def _to_bool(x):
                                                try:
                                                    if isinstance(x, str):
                                                        return x.strip().lower() in ("1","true","yes","on")
                                                    return bool(x)
                                                except Exception:
                                                    return x
                                            new_val = [_to_bool(x) for x in new_val]
                                else:
                                    if isinstance(pval, int) and isinstance(new_val, str):
                                        try:
                                            if new_val.strip().isdigit():
                                                new_val = int(new_val)
                                        except Exception:
                                            pass
                                    elif isinstance(pval, float) and isinstance(new_val, str):
                                        try:
                                            new_val = float(new_val)
                                        except Exception:
                                            pass
                                    elif isinstance(pval, bool) and isinstance(new_val, str):
                                        try:
                                            new_val = new_val.strip().lower() in ("1","true","yes","on")
                                        except Exception:
                                            pass
                            except Exception:
                                pass
                            enriched_map[pname] = new_val
                    enriched_params.append({"type": ptype, "parameters": enriched_map})
            else:
                enriched_params = params
            api_data["request_params"] = enriched_params
            return api_data

        result = {}
        src = add_type_api_packages_results

        # 兼容两种输入结构：
        # 1) 顶层包含 'cross'/'group' 的分桶结构：{"cross": {fg: [...]}, "group": {fg: [...]}}
        # 2) 直接按功能组归纳的结构：{fg: [...]}
        if isinstance(src, dict) and ("cross" in src or "group" in src or "cross" in src):
            for bucket_key, groups in src.items():
                if not isinstance(groups, dict):
                    continue
                result[bucket_key] = {}
                for functional_group, chains in groups.items():
                    group_params = click_params_by_group.get(functional_group, {})
                    enriched_group_chains = []
                    # if functional_group == "seckill/time":
                    #     print("ok")
                    if isinstance(chains, list):
                        for chain in chains:
                            if not isinstance(chain, dict):
                                enriched_group_chains.append(chain)
                                continue
                            enriched_chain = {}
                            for step_key, apis in chain.items():
                                # 处理单个 API 或 API 列表
                                if isinstance(apis, list):
                                    enriched_apis = []
                                    for api_info in apis:
                                        if not isinstance(api_info, dict):
                                            enriched_apis.append(api_info)
                                            continue
                                        for api_key, api_data in api_info.items():
                                            enriched_apis.append({api_key: _enrich_api_data(api_data, group_params)})
                                    enriched_chain[step_key] = enriched_apis
                                elif isinstance(apis, dict):
                                    enriched_api_map = {}
                                    for api_key, api_data in apis.items():
                                        enriched_api_map[api_key] = _enrich_api_data(api_data, group_params)
                                    enriched_chain[step_key] = enriched_api_map
                                else:
                                    enriched_chain[step_key] = apis
                            enriched_group_chains.append(enriched_chain)
                    result[bucket_key][functional_group] = enriched_group_chains
        else:
            for functional_group, chains in src.items():
                group_params = click_params_by_group.get(functional_group, {})
                enriched_group_chains = []
                if isinstance(chains, list):
                    for chain in chains:
                        if not isinstance(chain, dict):
                            enriched_group_chains.append(chain)
                            continue
                        enriched_chain = {}
                        for step_key, apis in chain.items():
                            if isinstance(apis, list):
                                enriched_apis = []
                                for api_info in apis:
                                    if not isinstance(api_info, dict):
                                        enriched_apis.append(api_info)
                                        continue
                                    for api_key, api_data in api_info.items():
                                        enriched_apis.append({api_key: _enrich_api_data(api_data, group_params)})
                                enriched_chain[step_key] = enriched_apis
                            elif isinstance(apis, dict):
                                enriched_api_map = {}
                                for api_key, api_data in apis.items():
                                    enriched_api_map[api_key] = _enrich_api_data(api_data, group_params)
                                enriched_chain[step_key] = enriched_api_map
                            else:
                                enriched_chain[step_key] = apis
                        enriched_group_chains.append(enriched_chain)
                result[functional_group] = enriched_group_chains

        return result
    
    def create_request_data_packages(self,case_pacakges_results):
        """
        将 add_type_api_packages_results.assigned.json 的数据格式，转换为 case_generation_results.json 的请求包格式。
        - 输入: case_pacakges_results（通常为 add_type_api_packages_results.assigned.json 已赋值的结构，可能是嵌套的字典/列表混合）
        - 输出: 结构对齐 case_generation_results.json：在保留原有嵌套结构的前提下，将叶子层的 API 数据转换为请求对象
        """
        src = case_pacakges_results

        # 提取点击采集中的 allRequests，供补全 url 与 headers
        click_all_requests = []
        try:
            click_all_requests = self.case_file.get("allRequests", []) if isinstance(self.case_file, dict) else []
        except Exception:
            click_all_requests = []

        # === 请求对象缓存：相同接口数据复用已构造的请求包，减少重复计算 ===
        import json, copy, threading
        request_obj_cache = {}
        cache_lock = threading.Lock()

        def _api_signature(api_data):
            """为 API 数据生成稳定签名，用于缓存命中。包含方法、路由、类型与规范化的请求参数结构。"""
            try:
                route = api_data.get("route", "")
                method = str(api_data.get("method", "GET")).upper()
                api_type = api_data.get("type", "")
                params_groups = api_data.get("request_params", [])
                normalized = []
                if isinstance(params_groups, list):
                    for grp in params_groups:
                        if not isinstance(grp, dict):
                            continue
                        ptype = grp.get("type")
                        params = grp.get("parameters", {})
                        norm_params = {}
                        if isinstance(params, dict):
                            for k in sorted(params.keys()):
                                v = params[k]
                                # 列表按顺序保留，其他直接记录
                                if isinstance(v, list):
                                    try:
                                        norm_params[k] = list(v)
                                    except Exception:
                                        norm_params[k] = v
                                else:
                                    norm_params[k] = v
                        normalized.append({"type": ptype, "parameters": norm_params})
                sign_obj = {"route": route, "method": method, "type": api_type, "params": normalized}
                return json.dumps(sign_obj, ensure_ascii=False, sort_keys=True)
            except Exception:
                return str(api_data)

        def _route_matches(route, endpoint_route):
            """支持路径参数匹配：/a/b/{id} 与 /a/b/123 视为匹配。"""
            if route == endpoint_route:
                return True
            route_parts = route.split('/')
            endpoint_parts = endpoint_route.split('/')
            if len(route_parts) != len(endpoint_parts):
                return False
            for rp, ep in zip(route_parts, endpoint_parts):
                if ep.startswith('{') and ep.endswith('}'):
                    continue
                if rp != ep:
                    return False
            return True

        def _replace_path_params(route, path_params):
            """将 /api/v1/goods/{goodsId} 中的占位符用 path_params 替换。未找到或值为空则保留占位符。"""
            if not isinstance(route, str):
                return route
            if not isinstance(path_params, dict):
                return route
            replaced = route
            for k, v in path_params.items():
                try:
                    # 跳过空值替换，保留占位符
                    if v is None:
                        continue
                    if isinstance(v, (list, dict)) and len(v) == 0:
                        continue
                    if isinstance(v, str) and v == "":
                        continue
                    replaced = replaced.replace("{" + str(k) + "}", str(v))
                except Exception:
                    pass
            return replaced

        def _find_url_and_headers(method, route, path_params):
            """在点击采集数据中查找匹配此方法与路由的完整 URL 与 headers。若存在路径参数，占位符会被替换后再匹配。"""
            method = (method or "").upper()
            route_with_vals = _replace_path_params(route, path_params)
            best_url = None
            best_headers = {}
            # 统计同前两级路径的域名出现次数，便于兜底选择
            domain_counter = {}

            def _prefix(path):
                parts = [p for p in path.split('/') if p]
                if len(parts) >= 2:
                    return "/" + parts[0] + "/" + parts[1]
                elif len(parts) == 1:
                    return "/" + parts[0]
                return "/"

            route_prefix = _prefix(route)

            for req in click_all_requests:
                try:
                    url = req.get("url")
                    m = (req.get("method") or "").upper()
                    if not url or m != method:
                        continue
                    parsed = urllib.parse.urlparse(url)
                    path = parsed.path
                    # 计数域名
                    domain = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else None
                    if domain:
                        domain_counter[domain] = domain_counter.get(domain, 0) + 1
                    # 优先精确匹配（替换后的路径）
                    if route_with_vals and _route_matches(path, route_with_vals):
                        best_url = url
                        best_headers = req.get("headers", {}) or {}
                        break
                    # 次优：原始占位符路径与实际路径前两级前缀匹配
                    if route_prefix and path.startswith(route_prefix):
                        best_url = url if best_url is None else best_url
                        if not best_headers:
                            best_headers = req.get("headers", {}) or {}
                except Exception:
                    continue

            # 兜底：若仍无 URL，则拼接出现次数最多的域名 + 替换后的路径
            if not best_url:
                if domain_counter:
                    candidate_domain = max(domain_counter.items(), key=lambda x: x[1])[0]
                    if route_with_vals and candidate_domain:
                        best_url = candidate_domain.rstrip('/') + route_with_vals
            return best_url, best_headers

        # === 引入LLM参数生成：当无法提取到参数值时，使用API文档与LLM补全 ===
        def _get_param_definition_from_api_doc(route_path, request_method, param_name):
            """
            从 self.api_doc 中根据请求路由和方法提取参数的完整定义
            与 case_generation.py 中的实现保持一致的返回格式
            """
            try:
                api_doc_data = self.api_doc
                endpoint_key = f"{request_method} {route_path}"
                for group_obj in api_doc_data:
                    for functional_group, endpoints in group_obj.items():
                        if endpoint_key in endpoints:
                            endpoint_data = endpoints[endpoint_key]
                            request_params = endpoint_data.get("request_parameters", {})
                            if param_name in request_params:
                                return request_params[param_name]
                return None
            except Exception as e:
                print(f"Error extracting param definition: {e}")
                return None

        def generation_parameters_from_llm(req_params, api_data, missing_params):
            """
            参考 case_generation.py 的实现：当 missing_params 非空时，
            构造提示并调用 LLM 生成可用的请求参数包。
            """
            param_type = {}
            for param_name in missing_params:
                param_type[param_name] = _get_param_definition_from_api_doc(
                    api_data.get("route"),
                    api_data.get("method"),
                    param_name
                )
            tmp_llm_dict = {}
            tmp_llm_dict = {
                "route_path": api_data.get("route"),
                "request_package": req_params,
                "parameters_name": str(missing_params),
                "parameters_type": str(param_type)
            }
            while True:
                try:
                    tmp_value = self.gpt_reply.getreply(self.syn_prompt.synthesis_prompt("parameter_generation", tmp_llm_dict))
                    script_code = self.jsontools.python_formatting(tmp_value)
                    local_vars = {}
                    exec(script_code, {}, local_vars)
                    if 'parameters_generator' in local_vars:
                        new_req_params = local_vars['parameters_generator'](req_params)
                        return new_req_params
                    else:
                        return req_params
                except Exception as e:
                    print(str(e))
                    continue
            return req_params

        def _build_request_obj(api_data):
            """将单个 API 数据转换为 case_generation_results 的请求对象。"""
            if not isinstance(api_data, dict):
                return api_data

            # 先尝试缓存命中，避免重复构造
            sig = _api_signature(api_data)
            try:
                with cache_lock:
                    cached = request_obj_cache.get(sig)
                if cached is not None:
                    return copy.deepcopy(cached)
            except Exception:
                pass

            route = api_data.get("route", "")
            method = str(api_data.get("method", "GET")).upper()
            api_type = api_data.get("type", "unknown")
            params_groups = api_data.get("request_params", [])

            # 分类参数
            path_params = {}
            body_params = {}
            query_params = {}
            form_params = {}
            files_params = {}
            missing_params = set()
            def _maybe_mark_missing(val, name):
                try:
                    if val == "" or val == 0 or val is None:
                        missing_params.add(name)
                except Exception:
                    pass

            if isinstance(params_groups, list):
                for grp in params_groups:
                    if not isinstance(grp, dict):
                        continue
                    ptype = grp.get("type")
                    pmap = grp.get("parameters", {}) if isinstance(grp.get("parameters"), dict) else {}
                    if ptype == "path":
                        for k, v in pmap.items():
                            path_params[k] = v
                            # 注意：path参数不应被LLM填充到body，因此不纳入missing_params
                    elif ptype in ("query", "params"):
                        for k, v in pmap.items():
                            query_params[k] = v
                            _maybe_mark_missing(v, k)
                    elif ptype in ("form", "formData"):
                        for k, v in pmap.items():
                            form_params[k] = v
                            _maybe_mark_missing(v, k)
                    elif ptype == "files":
                        for k, v in pmap.items():
                            files_params[k] = v
                            # 文件缺失不纳入通用判定
                    elif ptype == "body" or ptype is None:
                        for k, v in pmap.items():
                            if method == "GET":
                                query_params[k] = v
                            else:
                                body_params[k] = v
                            _maybe_mark_missing(v, k)

            # 路径参数替换
            resolved_route = _replace_path_params(route, path_params)
            # 仅检索 headers，URL 固定为 demo 域名 + 当前路由
            _, headers = _find_url_and_headers(method, resolved_route, path_params)
            if not isinstance(headers, dict):
                headers = {}
            full_url = f"http://demo{resolved_route}"

            req_params = {
                "method": method,
                "url": full_url,
                "headers": headers,
                "timeout": (5, 30),  # (connect_timeout, read_timeout)
            }
            if body_params:
                req_params["json"] = body_params
            if query_params:
                req_params["params"] = query_params
            if form_params:
                req_params["data"] = form_params
            if files_params:
                req_params["files"] = files_params

            # 当无法提取到参数值时，调用LLM进行补全
            if len(missing_params) > 0:
                try:
                    req_params = generation_parameters_from_llm(req_params, api_data, list(missing_params))
                except Exception as _:
                    pass

            # 根据请求负载设置/更新 content-type
            content_type = None
            if req_params.get("files"):
                content_type = "multipart/form-data"
            elif req_params.get("data"):
                content_type = "application/x-www-form-urlencoded"
            elif req_params.get("json"):
                content_type = "application/json;charset=UTF-8"
            if content_type:
                req_params.setdefault("headers", {})
                req_params["headers"]["content-type"] = content_type

            serialized_req = _serialize_request_params(req_params)

            # 选择用于展示的 request_data（与真实请求包同步）
            display_request_data = {}
            if req_params.get("json") is not None:
                display_request_data = req_params.get("json")
            elif req_params.get("data") is not None:
                display_request_data = req_params.get("data")
            elif req_params.get("files") is not None:
                display_request_data = req_params.get("files")
            elif req_params.get("params") is not None:
                display_request_data = req_params.get("params")

            request_obj = {
                "route": route,
                "method": method,
                "type": api_type,
                "request_params": {
                    "type": "request",
                    "parameters": serialized_req
                },
                "response_params": {
                    "type": "response",
                    "parameters": {}
                },
                "execution_status": {
                    "api_key": f"{method}：{route}",
                    "status": "not_executed",
                    "status_code": 0,
                    "request_url": full_url,
                    "request_data": display_request_data
                }
            }
            # 写入缓存并返回副本，避免外部修改影响缓存体
            try:
                with cache_lock:
                    request_obj_cache[sig] = request_obj
                return copy.deepcopy(request_obj)
            except Exception:
                return request_obj

        def convert_node(node):
            """递归转换链条节点：保留原有嵌套结构；在每个功能组内对叶子层接口并行转换。"""
            # 列表：按项并行转换（遇到复杂嵌套时自动降级串行）
            if isinstance(node, list):
                try:
                    from concurrent.futures import ThreadPoolExecutor
                    import os
                    items = list(node)
                    max_workers = min(len(items), (os.cpu_count() or 4))
                    if max_workers <= 1:
                        out = []
                        for item in items:
                            if isinstance(item, (dict, list)):
                                out.append(convert_node(item))
                            else:
                                out.append(item)
                        return out
                    def _conv_item(it):
                        return convert_node(it) if isinstance(it, (dict, list)) else it
                    with ThreadPoolExecutor(max_workers=max_workers) as executor:
                        return list(executor.map(_conv_item, items))
                except Exception:
                    out = []
                    for item in node:
                        if isinstance(item, (dict, list)):
                            out.append(convert_node(item))
                        else:
                            out.append(item)
                    return out
            # 字典：可能是步骤字典或 API 键映射
            if isinstance(node, dict):
                # 辅助判定：是否为 API 数据字典
                def is_api_data_dict(d):
                    return isinstance(d, dict) and ("route" in d) and (("method" in d) or ("type" in d))
                # 辅助判定：是否为包裹层（内部包含一个或多个 API 数据字典，如 {"POST：/xx": {..}, "request_params": []}）
                def is_api_wrapper_dict(d):
                    return isinstance(d, dict) and any(is_api_data_dict(v) for v in d.values())

                values = list(node.values())
                # 判断是否为叶子层：api_key -> api_data（严格要求每个 value 都是 API 数据字典）
                is_leaf_map = (len(values) > 0) and all(is_api_data_dict(v) for v in values)
                if is_leaf_map:
                    # 叶子层接口并行转换（仅并行计算，字典写入在主线程）
                    try:
                        from concurrent.futures import ThreadPoolExecutor
                        import os
                        items = list(node.items())
                        max_workers = min(len(items), (os.cpu_count() or 4))
                        if max_workers <= 1:
                            return {api_key: _build_request_obj(api_data) for api_key, api_data in items}
                        with ThreadPoolExecutor(max_workers=max_workers) as executor:
                            futures = {executor.submit(_build_request_obj, api_data): api_key for api_key, api_data in items}
                            converted_map = {}
                            for fut, api_key in [(f, futures[f]) for f in futures]:
                                converted_map[api_key] = fut.result()
                            return converted_map
                    except Exception:
                        converted_map = {}
                        for api_key, api_data in node.items():
                            converted_map[api_key] = _build_request_obj(api_data)
                        return converted_map

                # 若是包裹层（混合了 API 数据与其他键），仅提取并转换其中的 API 数据，忽略非 API 键（如"request_params"占位）
                if is_api_wrapper_dict(node):
                    try:
                        from concurrent.futures import ThreadPoolExecutor
                        import os
                        items = [(api_key, api_data) for api_key, api_data in node.items() if is_api_data_dict(api_data)]
                        max_workers = min(len(items), (os.cpu_count() or 4))
                        if max_workers <= 1:
                            return {api_key: _build_request_obj(api_data) for api_key, api_data in items}
                        with ThreadPoolExecutor(max_workers=max_workers) as executor:
                            futures = {executor.submit(_build_request_obj, api_data): api_key for api_key, api_data in items}
                            converted_map = {}
                            for fut, api_key in [(f, futures[f]) for f in futures]:
                                converted_map[api_key] = fut.result()
                            return converted_map
                    except Exception:
                        converted_map = {}
                        for api_key, api_data in node.items():
                            if is_api_data_dict(api_data):
                                converted_map[api_key] = _build_request_obj(api_data)
                        return converted_map

                # 非叶子：继续递归处理步骤嵌套
                nested = {}
                # 按数字顺序排序键（若可），保持步骤序
                def sort_key(k):
                    # 统一返回可比较的键：数字键优先，其次按字典序
                    if isinstance(k, int) or (isinstance(k, str) and k.isdigit()):
                        try:
                            return (0, int(k))
                        except Exception:
                            return (0, 0)
                    return (1, str(k))
                for k in sorted(node.keys(), key=sort_key):
                    nested[k] = convert_node(node[k])
                return nested
            # 其他类型（字符串等），直接返回
            return node

        def _parallel_convert_list(chains):
            """并行转换链节点列表，使用线程池并在异常时回退到串行，确保线程安全（仅对局部列表并行，字典写入在主线程）。"""
            try:
                from concurrent.futures import ThreadPoolExecutor
                import os
                if not chains:
                    return []
                max_workers = min(len(chains), (os.cpu_count() or 4))
                if max_workers <= 1:
                    return [convert_node(chain) for chain in chains]
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    return list(executor.map(convert_node, chains))
            except Exception:
                # 任何异常均回退到串行处理，避免并发引入不稳定
                return [convert_node(chain) for chain in chains]

        # 每个功能组的转换（组内列表并行 + 非列表递归）
        def _convert_group(functional_group, chains):
            try:
                if isinstance(chains, list):
                    return _parallel_convert_list(chains)
                return convert_node(chains)
            except Exception:
                return convert_node(chains)

        def _parallel_convert_groups(groups_dict):
            """按功能组并行转换，保持原有结构键不变。"""
            try:
                from concurrent.futures import ThreadPoolExecutor, as_completed
                import os
                if not isinstance(groups_dict, dict) or not groups_dict:
                    return {}
                max_workers = min(len(groups_dict), (os.cpu_count() or 4))
                if max_workers <= 1:
                    return {fg: _convert_group(fg, chains) for fg, chains in groups_dict.items()}
                results = {}
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    future_map = {executor.submit(_convert_group, fg, chains): fg for fg, chains in groups_dict.items()}
                    for future in as_completed(future_map):
                        fg = future_map[future]
                        try:
                            results[fg] = future.result()
                        except Exception:
                            results[fg] = _convert_group(fg, groups_dict.get(fg))
                return results
            except Exception:
                return {fg: _convert_group(fg, chains) for fg, chains in groups_dict.items()}

        result = {}
        # 兼容顶层包含 cross/group 的分桶结构与直接功能组结构
        if isinstance(src, dict) and any(k in src for k in ("cross", "group")):
            for bucket_key, groups in src.items():
                if not isinstance(groups, dict):
                    continue
                result[bucket_key] = _parallel_convert_groups(groups)
        else:
            result = _parallel_convert_groups(src if isinstance(src, dict) else {})
        return result
    
    

    def case_generation_main_workflow(self):
        """
        测试用例打包输出的主要流程
        """
        case_hadling_from_click_data_resutls = self.case_hadling_from_click_data_initial()
        self.jsontools.write_json(f"/Users/tlif3./zju_research/bolascan_v3/bolascan_v4/cache/{self.project_name}/case_hadling_from_click_data_resutls.json",case_hadling_from_click_data_resutls)
        # case_hadling_from_click_data_resutls = self.jsontools.read_json(f"/Users/tlif3./zju_research/bolascan_v3/bolascan_v4/cache/{self.project_name}/case_hadling_from_click_data_resutls.json")

        dependency_chain_with_parameters_results = self.dependency_chain_with_parameters()
        self.jsontools.write_json(f"/Users/tlif3./zju_research/bolascan_v3/bolascan_v4/cache/{self.project_name}/dependency_chain_with_parameters_results.json",dependency_chain_with_parameters_results)

        add_type_api_packages_results = self.add_type_api_packages(dependency_chain_with_parameters_results,case_hadling_from_click_data_resutls)
        self.jsontools.write_json(f"/Users/tlif3./zju_research/bolascan_v3/bolascan_v4/cache/{self.project_name}/add_type_api_packages_results.json",add_type_api_packages_results)

        add_parameters_from_click_data = self.add_parameters_from_click_data(add_type_api_packages_results,case_hadling_from_click_data_resutls)
        self.jsontools.write_json(f"/Users/tlif3./zju_research/bolascan_v3/bolascan_v4/cache/{self.project_name}/add_parameters_from_click_data.json",add_parameters_from_click_data)

        create_request_data_packages_results = self.create_request_data_packages(add_parameters_from_click_data)
        
        # 序列化包含文件对象的请求参数，确保可以保存到JSON（递归处理所有深层嵌套和循环引用）
        serialized_results = _make_json_serializable(create_request_data_packages_results)
        
        self.jsontools.write_json(f"/Users/tlif3./zju_research/bolascan_v3/bolascan_v4/cache/{self.project_name}/create_request_data_packages_results.json", serialized_results)
        logger.info("create_request_data_packages_results successful")

        # wheel_execution_packages_results = self.wheel_exectuion_pacakges_create(url,data_account,create_request_data_packages_results)
        # 将执行后的结果写到缓存，便于查看响应
        # self.jsontools.write_json("/Users/tlif3./zju_research/bolascan_v3/bolascan_v4/cache/newbee_mall/wheel_execution_packages_results.json", wheel_execution_packages_results)


        # case_generation_results = self.wheel_running_packages(url,data_account,add_parameters_from_click_data)

        return create_request_data_packages_results



if __name__ == "__main__":
    # 一次性入口：读取 add_type_api_packages_results.json 与 http-requests.json，
    # 使用 wheel_running_packages 为所有接口的请求参数赋值，并输出到缓存目录。
    try:
        import os
        from scripts.jsontools import JsonTools
        project_name = "gin_vue_admin"

        js = JsonTools()
        # 固定项目路径与文件位置（用户提供的绝对路径）
        api_doc_path = f"/Users/tlif3./zju_research/bolascan_v3/bolascan_v4/cache/{project_name}/api_doc_with_type.json"
        case_file_path = f"/Users/tlif3./zju_research/bolascan_v3/bolascan_v4/automated_click/{project_name}/http-requests.json"
        # add_type_path = "/Users/tlif3./zju_research/bolascan_v3/bolascan_v4/cache/mall/add_type_api_packages_results.json"
        chain_path = f"/Users/tlif3./zju_research/bolascan_v3/bolascan_v4/cache/{project_name}/dependency_chains_results.json"
        output_path = f"/Users/tlif3./zju_research/bolascan_v3/bolascan_v4/cache/{project_name}/case_execution_packages.json"

        # 读取所需数据
        api_doc_data = js.read_json(api_doc_path)
        # add_type_api_packages_results = js.read_json(add_type_path)
        dependency_chain_data = js.read_json(chain_path)

        # 初始化 CaseGeneration（dependency_chain_data 与 params_dict 在本任务中不参与）
        cg = CaseGeneration(
            case_file=case_file_path,
            model_name="gpt-4o-mini",
            doc_data=api_doc_data,
            dependency_chain_data=dependency_chain_data,
            params_dict={},
            debug=False,
            project_name=project_name
        )
        data_account = {"token":"test123"}

        _ = cg.case_generation_main_workflow()
        print(f"已生成依赖链参数结果: /Users/tlif3./zju_research/bolascan_v3/bolascan_v4/cache/{project_name}/dependency_chain_with_parameters_results.json")
    except Exception as e:
        print(f"执行失败: {e}")
