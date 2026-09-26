"""
工具辅助函数模块

提供JSON序列化、时间格式化、进度跟踪等通用工具函数。
"""

import io
import base64
import sys
import time
import logging
from threading import Lock
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def make_json_serializable(obj: Any) -> Any:
    """
    递归处理对象，将bytes、BytesIO等类型转换为base64编码的字符串
    
    Args:
        obj: 待序列化的对象
        
    Returns:
        可JSON序列化的对象
    """
    # 处理 BytesIO 对象
    if isinstance(obj, io.BytesIO):
        obj.seek(0)  # 确保从头开始读取
        return base64.b64encode(obj.read()).decode('utf-8')
    elif isinstance(obj, bytes):
        return base64.b64encode(obj).decode('utf-8')
    elif isinstance(obj, dict):
        return {key: make_json_serializable(value) for key, value in obj.items()}
    elif isinstance(obj, list):
        return [make_json_serializable(item) for item in obj]
    elif isinstance(obj, tuple):
        return tuple(make_json_serializable(item) for item in obj)
    else:
        return obj


def format_duration(seconds: float) -> str:
    """
    将秒数格式化为易读的时间字符串
    
    Args:
        seconds: 秒数
        
    Returns:
        格式化的时间字符串，如 "1h 2m 3s"
    """
    try:
        seconds = int(seconds)
    except Exception:
        return "?"
    
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{m}m {s}s"
    else:
        h, remainder = divmod(seconds, 3600)
        m, s = divmod(remainder, 60)
        return f"{h}h {m}m {s}s"


def update_terminal_progress(completed: int, total: int, start_time: float, 
                              prefix: str = "", bar_length: int = 20) -> None:
    """
    更新终端进度条
    
    Args:
        completed: 已完成数量
        total: 总数量
        start_time: 开始时间戳
        prefix: 进度条前缀
        bar_length: 进度条长度
    """
    if total <= 0:
        return
        
    pct = int(100 * completed / total)
    filled = int(bar_length * completed / total)
    bar = "=" * filled + "-" * (bar_length - filled)
    
    now = time.time()
    elapsed = now - start_time
    eta = ((elapsed / completed) * (total - completed)) if completed > 0 else 0
    
    msg = f"{prefix}[{bar}] {pct}% {completed}/{total} | elapsed {format_duration(elapsed)} | eta {format_duration(eta)}"
    sys.stdout.write("\r" + msg)
    sys.stdout.flush()
    
    if completed == total:
        sys.stdout.write("\n")
        sys.stdout.flush()


def flatten_list(nested_list: List[Any]) -> List[Any]:
    """
    展平嵌套列表
    
    Args:
        nested_list: 嵌套列表
        
    Returns:
        展平后的列表
    """
    result = []
    for item in nested_list:
        if isinstance(item, list):
            result.extend(flatten_list(item))
        else:
            result.append(item)
    return result


def flatten_dict(d: Dict[str, Any], parent_key: str = '', sep: str = '.') -> Dict[str, Any]:
    """
    展平嵌套字典
    
    Args:
        d: 嵌套字典
        parent_key: 父键名
        sep: 键分隔符
        
    Returns:
        展平后的字典
    """
    items = []
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(flatten_dict(v, new_key, sep=sep).items())
        else:
            items.append((new_key, v))
    return dict(items)


def normalize_group_prefix(url: str) -> str:
    """
    规范化功能组前缀，移除域名部分
    
    Args:
        url: 完整URL或路径
        
    Returns:
        规范化后的路径
    """
    if not url:
        return ""
    
    # 移除协议和域名
    if "://" in url:
        url = url.split("://", 1)[1]
        if "/" in url:
            url = url.split("/", 1)[1]
        else:
            url = ""
    
    # 移除开头的斜杠
    url = url.lstrip("/")
    
    return url


def parse_method_route_key(key: str) -> tuple:
    """
    解析 "METHOD /route" 格式的键，返回 (method, route)
    
    Args:
        key: 如 "GET /api/users" 或 "POST:/api/items"
        
    Returns:
        (method, route) 元组，如 ("GET", "/api/users")
    """
    if not key or not isinstance(key, str):
        return (None, None)
    
    s = key.strip()
    # 统一不同分隔符
    s = s.replace("：", " ").replace(":", " ")
    parts = s.split(maxsplit=1)
    
    if len(parts) >= 2:
        return (parts[0].upper(), parts[1])
    elif len(parts) == 1:
        # 仅有路由的情况
        return (None, parts[0])
    return (None, None)


def is_step_object(obj: Any) -> bool:
    """
    判断对象是否是一个步骤对象（API调用步骤）
    
    步骤对象通常包含 route、method、request_params 等字段
    
    Args:
        obj: 待判断的对象
        
    Returns:
        True 如果是步骤对象，否则 False
    """
    if not isinstance(obj, dict):
        return False
    
    # 步骤对象的特征：包含 route 或 method，同时有 request_params
    has_route = "route" in obj
    has_method = "method" in obj
    has_request_params = "request_params" in obj
    
    return (has_route or has_method) and has_request_params


def extract_numeric_keys(d: Dict[str, Any]) -> List[str]:
    """
    从字典中提取所有数字键并按数字顺序排序
    
    Args:
        d: 输入字典
        
    Returns:
        排序后的数字键列表
    """
    if not isinstance(d, dict):
        return []
    
    numeric_keys = [k for k in d.keys() if isinstance(k, str) and k.isdigit()]
    return sorted(numeric_keys, key=lambda x: int(x))


class ProgressTracker:
    """
    线程安全的进度跟踪器
    """
    
    def __init__(self, total: int = 0, prefix: str = ""):
        """
        初始化进度跟踪器
        
        Args:
            total: 总任务数
            prefix: 进度条前缀
        """
        self.total = total
        self.completed = 0
        self.prefix = prefix
        self.start_time = time.time()
        self.lock = Lock()
        self.by_group: Dict[str, Dict[str, int]] = {}
    
    def increment(self, group_name: Optional[str] = None) -> int:
        """
        增加已完成计数
        
        Args:
            group_name: 可选的组名称，用于分组统计
            
        Returns:
            当前已完成数量
        """
        with self.lock:
            self.completed += 1
            
            if group_name:
                if group_name not in self.by_group:
                    self.by_group[group_name] = {"completed": 0, "total": 0}
                self.by_group[group_name]["completed"] += 1
            
            return self.completed
    
    def set_group_total(self, group_name: str, total: int) -> None:
        """
        设置特定组的总数
        
        Args:
            group_name: 组名称
            total: 该组的总任务数
        """
        with self.lock:
            if group_name not in self.by_group:
                self.by_group[group_name] = {"completed": 0, "total": 0}
            self.by_group[group_name]["total"] = total
    
    def update_display(self, bar_length: int = 20) -> None:
        """
        更新终端显示
        
        Args:
            bar_length: 进度条长度
        """
        update_terminal_progress(
            self.completed, 
            self.total, 
            self.start_time, 
            prefix=self.prefix,
            bar_length=bar_length
        )
    
    def get_progress(self) -> Dict[str, Any]:
        """
        获取当前进度信息
        
        Returns:
            包含进度信息的字典
        """
        with self.lock:
            elapsed = time.time() - self.start_time
            pct = (100 * self.completed / self.total) if self.total > 0 else 0
            
            return {
                "total": self.total,
                "completed": self.completed,
                "percentage": round(pct, 2),
                "elapsed": elapsed,
                "elapsed_formatted": format_duration(elapsed),
                "by_group": dict(self.by_group)
            }
    
    def reset(self, total: int = 0) -> None:
        """
        重置进度跟踪器
        
        Args:
            total: 新的总任务数
        """
        with self.lock:
            self.total = total
            self.completed = 0
            self.start_time = time.time()
            self.by_group = {}

