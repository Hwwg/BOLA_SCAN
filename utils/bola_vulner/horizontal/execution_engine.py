"""
执行引擎模块
负责执行测试用例、管理API请求和参数池

注意：本模块包含execution_packages方法，该方法非常复杂（约2700行），
包含大量嵌套的内部函数，用于处理依赖链执行、参数池管理、测试用例应用等。
"""
import logging
import requests
import threading
import time
import sys
import os
import io
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, List, Optional, Set
from urllib.parse import urlsplit, urlunsplit
import copy
import re

from .testcase_matrix import is_valid_resource_id
from .utils_helpers import ProgressTracker, format_duration, update_terminal_progress

logger = logging.getLogger(__name__)


class ExecutionEngine:
    """
    执行引擎类
    负责执行依赖链包，管理API请求和响应参数池
    """
    
    def __init__(self, route_group_map, group_param_config, container_params_by_group, 
                 true_params, jsontool):
        """
        初始化执行引擎
        
        Args:
            route_group_map: 路由到功能组的映射
            group_param_config: 功能组参数配置
            container_params_by_group: 按组分类的容器参数
            true_params: API文档数据
            jsontool: JSON工具对象
        """
        self.route_group_map = route_group_map
        self.group_param_config = group_param_config
        self.container_params_by_group = container_params_by_group
        self.true_params = true_params
        self.jsontool = jsontool
    
    def is_container_param(self, category: str, group_name: str, param_name: str, 
                          step_route: Optional[str] = None) -> bool:
        """
        判断参数是否为容器参数
        
        Args:
            category: 类别（ou_id或resource_id）
            group_name: 功能组名称
            param_name: 参数名称
            step_route: 步骤路由（可选）
            
        Returns:
            是否为容器参数
        """
        try:
            m = self.container_params_by_group
            if not isinstance(m, dict):
                return False
            cat = (category or "").strip()
            if cat not in m:
                return False
            group_map = m.get(cat, {})
            if not isinstance(group_map, dict):
                return False
            
            # 规范化组名
            from .utils_helpers import normalize_group_prefix
            g1 = normalize_group_prefix(group_name or "")
            if g1 and g1 in group_map and (param_name in group_map[g1]):
                return True
            
            # fallback：从具体 route 推断 group 前缀
            r = normalize_group_prefix(step_route or "")
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
    
    def execution_packages(self, url: str, authority_account: Dict, 
                          dependency_chain_request_packages: Dict,
                          container_resource_divide_results: Dict) -> Dict[str, Any]:
        """
        执行依赖链包
        
        由于该方法非常复杂（约2700行代码），包含大量嵌套的内部函数，
        建议保持原有结构或在单独的重构阶段处理。
        
        该方法的主要功能：
        1. 执行两轮测试：首轮用data_account完整执行，次轮用test_account执行部分步骤
        2. 管理参数池，支持分组存储和优先级
        3. 应用测试用例并记录执行结果
        4. 处理resource_id和ou_id两种类型的参数测试
        
        Args:
            url: 目标URL
            authority_account: 认证账户信息
            dependency_chain_request_packages: 依赖链请求包
            container_resource_divide_results: 容器资源划分结果
            
        Returns:
            执行结果字典，包含每个功能组和参数的测试结果
        """
        # 注意：该方法的完整实现非常长（约2700行），包含大量嵌套函数
        # 为了代码可维护性，暂时保留原有实现在主文件中
        # 在重构阶段，该方法将被完整迁移到此类中
        
        # 这里提供一个占位符实现，实际执行逻辑保留在原horizontal_vuln.py中
        raise NotImplementedError(
            "该方法的完整实现保留在horizontal_vuln.py中。"
            "请使用HorizontalVuln类的execution_packages方法。"
        )


# 以下是从原文件中提取的关键辅助函数，供独立使用

def parse_base_url(url_param: str) -> tuple:
    """解析基础URL，返回(scheme, netloc)"""
    if not url_param:
        return ("http", "localhost")
    if url_param.startswith("http://") or url_param.startswith("https://"):
        sp = urlsplit(url_param)
        scheme = sp.scheme or "http"
        netloc = sp.netloc or (sp.path.strip("/") if sp.path else "")
        return (scheme, netloc)
    else:
        return ("http", url_param.strip("/"))


def replace_domain(original_url: str, base_scheme: str, base_netloc: str, 
                  route: Optional[str] = None) -> str:
    """替换URL中的域名"""
    if (not original_url) and route:
        return f"{base_scheme}://{base_netloc}{route}"
    if original_url and original_url.startswith("/"):
        return f"{base_scheme}://{base_netloc}{original_url}"
    sp = urlsplit(original_url or "")
    path = sp.path or (route or "")
    return urlunsplit((base_scheme, base_netloc, path, sp.query, sp.fragment))


def merge_headers(headers: Dict, auth_info: Any) -> Dict:
    """合并请求头和认证信息"""
    headers = headers.copy() if isinstance(headers, dict) else {}
    if isinstance(auth_info, dict):
        for k, v in auth_info.items():
            headers[k] = v
    elif isinstance(auth_info, str) and auth_info:
        headers["Authorization"] = auth_info
    return headers


def flatten_response(resp: Any) -> Dict[str, Any]:
    """展平响应JSON为一维字典"""
    flat = {}
    def walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
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


