from dataclasses import dataclass
from typing import List, Dict, Any, Optional
import re


def is_valid_resource_id(value):
    """
    验证资源ID是否合法（数字或UUID格式）
    排除明显的路径片段如 'recent', 'all', 'latest'等
    """
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        # 排除明显的路径片段/关键字
        invalid_keywords = [
            'recent', 'all', 'latest', 'list', 'new', 'create', 'update', 
            'delete', 'edit', 'add', 'remove', 'index', 'home', 'first',
            'last', 'next', 'prev', 'previous', 'current', 'default'
        ]
        if value.lower() in invalid_keywords:
            return False
        # 检查是否为纯数字字符串
        if value.isdigit():
            return True
        # 检查是否为UUID格式
        uuid_pattern = r'^[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}$'
        if re.match(uuid_pattern, value):
            return True
        # 检查是否为短UUID格式（如 mwZTRFC9ZDZxw5YvgxLzHe）
        short_uuid_pattern = r'^[a-zA-Z0-9]{20,}$'
        if re.match(short_uuid_pattern, value):
            return True
    return False


@dataclass
class TestCase:
    """
    测试用例数据类
    
    字段说明（对齐取值关系文档）：
    - case_type: 测试类型 
        - "overprivilege": 普通越权测试（单位置）
        - "multi_param": 多位置one-hot测试
        - "container_boundary": 容器边界测试（同账号跨容器）
        - "missing": 缺失参数测试
        - "conflict": 参数冲突测试
    - param_name: 被测参数名
    - aliases: 参数别名列表
    - value_source: Target位置的参数值来源池 (A/B/C/D)
    - locations: 参数出现的位置列表
    - extra_params: 扩展参数
        - non_target_source: Non-target位置的参数值来源池 (默认C，容器边界用D)
        - comparison_source: 对照组的Pool来源 (默认A，容器边界用E)
        - target_position: one-hot测试中的target位置
        - position_mode: "single" 或 "multi"
        - last_step_type: 最后一步类型（query/update/delete等）
        - category: 参数类型（resource_id/ou_id）
    """
    case_type: str
    param_name: str
    aliases: List[str]
    value_source: str
    locations: List[str]
    extra_params: Dict[str, Any]


def build_test_cases(param_name: str, locations: List[str], aliases: List[str], last_step_type: str, category: str, include_extra_types: bool = True) -> List[TestCase]:
    """
    构建BOLA测试用例（跨账号越权）
    
    Pool取值规则（对齐取值关系文档）：
    - Query/List Query: 使用 Pool A (victim完整执行后的参数)
    - Update/Delete: 使用 Pool B (victim执行到最后一步前的参数)
    - Non-target位置: 使用 Pool C (attacker执行到最后一步前的参数)
    """
    locs = [l for l in (locations or []) if l in ("path", "query", "body", "header")]
    if not locs:
        locs = ["query"]
    single = len(locs) <= 1
    cases: List[TestCase] = []
    if single:
        if (last_step_type or "") in ("query", "list query"):
            cases.append(TestCase("overprivilege", param_name, aliases, "A", locs, {
                "position_mode": "single", 
                "last_step_type": last_step_type, 
                "category": category,
                "non_target_source": "C",  # 非target位置使用Pool C
                "comparison_source": "A"   # 对照组使用Pool A
            }))
        else:
            # resource_id 与 ou_id 的非 query 用例都应从 B 池取被测参数
            vsrc = "B" if category in ("resource_id", "ou_id") else "C"
            cases.append(TestCase("overprivilege", param_name, aliases, vsrc, locs, {
                "position_mode": "single", 
                "last_step_type": last_step_type, 
                "category": category,
                "non_target_source": "C",
                "comparison_source": "A"
            }))
        if include_extra_types:
            cases.append(TestCase("missing", param_name, aliases, "explicit", locs, {"remove": True, "position_mode": "single", "last_step_type": last_step_type, "category": category}))
            cases.append(TestCase("conflict", param_name, aliases, "explicit", locs, {"conflict": True, "position_mode": "single", "last_step_type": last_step_type, "category": category}))
    else:
        # multi-location: 为每个位置生成一个 test case，但传入所有位置信息
        for L in locs:
            if (last_step_type or "") in ("query", "list query"):
                cases.append(TestCase("multi_param", param_name, aliases, "A", locs, {
                    "target_position": L, 
                    "position_mode": "multi", 
                    "all_locations": locs,
                    "last_step_type": last_step_type, 
                    "category": category,
                    "non_target_source": "C",  # Non-target位置使用Pool C
                    "comparison_source": "A"   # 对照组使用Pool A
                }))
            else:
                # resource_id 与 ou_id 的非 query 用例都应从 B 池取被测参数
                vsrc = "B" if category in ("resource_id", "ou_id") else "C"
                cases.append(TestCase("multi_param", param_name, aliases, vsrc, locs, {
                    "target_position": L, 
                    "position_mode": "multi",
                    "all_locations": locs,
                    "last_step_type": last_step_type, 
                    "category": category,
                    "non_target_source": "C",
                    "comparison_source": "A"
                }))
    return cases


def build_container_boundary_test_cases(param_name: str, locations: List[str], aliases: List[str], 
                                         last_step_type: str, category: str) -> List[TestCase]:
    """
    构建容器边界测试用例（同账号跨容器）
    
    Pool取值规则（对齐取值关系文档）：
    - Target位置: 使用 Pool C (attacker容器A的参数)
    - Non-target位置: 使用 Pool D (attacker容器B的参数)
    - 对照组: 使用 Pool E (attacker容器B完整执行后的参数)
    
    测试目的：测试系统是否验证资源与容器的所属关系
    """
    locs = [l for l in (locations or []) if l in ("path", "query", "body", "header")]
    if not locs:
        locs = ["query"]
    single = len(locs) <= 1
    cases: List[TestCase] = []
    
    if single:
        # 单位置容器边界测试
        cases.append(TestCase("container_boundary", param_name, aliases, "C", locs, {
            "position_mode": "single",
            "last_step_type": last_step_type,
            "category": category,
            "non_target_source": "D",  # 非target位置使用Pool D (容器B)
            "comparison_source": "E"   # 对照组使用Pool E
        }))
    else:
        # 多位置容器边界测试（one-hot）
        for L in locs:
            cases.append(TestCase("container_boundary", param_name, aliases, "C", locs, {
                "target_position": L,
                "position_mode": "multi",
                "all_locations": locs,
                "last_step_type": last_step_type,
                "category": category,
                "non_target_source": "D",  # Non-target位置使用Pool D
                "comparison_source": "E"   # 对照组使用Pool E
            }))
    
    return cases


def _detect_param_locations(step_obj: Dict[str, Any], aliases: List[str]) -> List[str]:
    """
    动态检测参数在请求中的所有位置
    
    Args:
        step_obj: 步骤对象
        aliases: 参数别名列表
    
    Returns:
        List[str]: 参数出现的位置列表 ["path", "query", "body", "header"]
    """
    params = step_obj.get("request_params", {}).get("parameters", {})
    locations = []
    
    # 检查 path
    url = params.get("url", "")
    if isinstance(url, str):
        for alias in aliases:
            if f"{{{alias}}}" in url:
                locations.append("path")
                break
    
    # 检查 query
    query = params.get("params", {})
    if isinstance(query, dict):
        for alias in aliases:
            if alias in query:
                locations.append("query")
                break
    
    # 检查 body (json 或 data)
    body = params.get("json")
    if not body:
        body = params.get("data", {})
    if isinstance(body, dict):
        for alias in aliases:
            if alias in body:
                locations.append("body")
                break
    
    # 检查 header
    headers = params.get("headers", {})
    if isinstance(headers, dict):
        for alias in aliases:
            if alias in headers:
                locations.append("header")
                break
    
    return locations


def apply_test_case_to_req(step_obj: Dict[str, Any], test_case: Optional[TestCase], pools: Optional[Dict[str, Dict[str, Any]]]) -> Dict[str, Any]:
    import logging
    logger = logging.getLogger(__name__)
    
    if not test_case:
        return step_obj
    
    # 诊断日志：检查TestCase的完整性
    logger.info(f"[DEBUG-TestCase-Apply] case_type={test_case.case_type}, param={test_case.param_name}")
    logger.info(f"[DEBUG-TestCase-Apply] extra_params={test_case.extra_params}")
    if isinstance(test_case.extra_params, dict):
        logger.info(f"[DEBUG-TestCase-Apply] target_position={test_case.extra_params.get('target_position')}")
    
    aliases = list(test_case.aliases or [test_case.param_name])
    params = step_obj.get("request_params", {}).get("parameters", {})
    
    # 添加调试：显示原始 URL
    if test_case.param_name == "video_id":
        original_url = params.get("url", "")
        logger.info(f"[DEBUG-TC-URL] apply_test_case_to_req 收到的原始 URL: {original_url}")

    def _get_value_from_pool(pool_key: str) -> Optional[Any]:
        """从指定池获取值（支持Pool A/B/C/D/E/F）"""
        if pool_key not in ("A", "B", "C", "D", "E", "F"):
            return None
        pool = (pools or {}).get(pool_key, {}) if isinstance(pools, dict) else {}
        if not isinstance(pool, dict):
            return None
        
        # 添加详细的调试日志（特别是 video_id）
        if test_case.param_name == "video_id":
            logger.info(f"[DEBUG-TC-GET] ========== 查找 video_id from Pool {pool_key} ==========")
            logger.info(f"[DEBUG-TC-GET] 池类型: {type(pool)}")
            logger.info(f"[DEBUG-TC-GET] 池的键: {list(pool.keys()) if isinstance(pool, dict) else 'N/A'}")
            logger.info(f"[DEBUG-TC-GET] 别名列表: {aliases}")
            logger.info(f"[DEBUG-TC-GET] 池内容（前5项）: {dict(list(pool.items())[:5]) if isinstance(pool, dict) else 'N/A'}")
        
        for a in aliases:
            v = pool.get(a)
            if test_case.param_name == "video_id":
                logger.info(f"[DEBUG-TC-GET] pool.get('{a}') = {v} (type={type(v)})")
            if v not in (None, ""):
                logger.info(f"[DEBUG-GetValue] from pool_{pool_key}, alias={a}, value={v}")
                return v
        
        if test_case.param_name == "video_id":
            logger.info(f"[DEBUG-TC-GET] ⚠️ 未找到任何值，返回 None")
            logger.info(f"[DEBUG-TC-GET] =============================================")
        return None

    def _get_value() -> Optional[Any]:
        explicit = test_case.extra_params.get("explicit_value") if isinstance(test_case.extra_params, dict) else None
        if explicit not in (None, ""):
            logger.info(f"[DEBUG-GetValue] param={test_case.param_name}, using explicit value={explicit}")
            return explicit
        src = (test_case.value_source or "").upper()
        logger.info(f"[DEBUG-GetValue] param={test_case.param_name}, case_type={test_case.case_type}, value_source={src}, aliases={aliases}")
        val = _get_value_from_pool(src)
        if val is not None:
            logger.info(f"[DEBUG-GetValue] SUCCESS: param={test_case.param_name}, from pool_{src}, value={val}")
            return val
        if src == "B":
            fallback = _get_value_from_pool("A")
            if fallback is not None:
                logger.info(f"[DEBUG-GetValue] FALLBACK: param={test_case.param_name}, pool_B empty, use pool_A={fallback}")
                return fallback
        logger.info(f"[DEBUG-GetValue] FAILED: param={test_case.param_name}, no value found in pool_{src}, aliases={aliases}")
        return None

    value = _get_value()
    
    # One-hot策略：检查是否指定了target_position
    target_pos = test_case.extra_params.get("target_position") if isinstance(test_case.extra_params, dict) else None

    def _set(body: Dict[str, Any], key: str, val: Any):
        if isinstance(body, dict):
            body[key] = val

    def _remove(body: Dict[str, Any], key: str):
        if isinstance(body, dict) and key in body:
            body.pop(key, None)

    remove_flag = bool(test_case.extra_params.get("remove")) if isinstance(test_case.extra_params, dict) else False
    conflict_flag = bool(test_case.extra_params.get("conflict")) if isinstance(test_case.extra_params, dict) else False
    conflict_val = f"conflict_{test_case.param_name}" if conflict_flag else None

    import re

    def _repl_path(url_s: str, aliases: List[str], val: Any) -> str:
        if test_case.param_name == "video_id":
            logger.info(f"[DEBUG-TC-URL] _repl_path 输入: url={url_s}, aliases={aliases}, val={val}")
        
        def repl(m):
            name = m.group(1)
          
            if name in aliases and val not in (None, ""):
                # 验证值是否为合法的资源ID
                if is_valid_resource_id(val):
                    logger.info(f"[DEBUG-TC-URL] ✓ 替换: {name} -> {val}")
                    return str(val)
                else:
                    logger.info(f"[DEBUG-TC-URL] ✗ 值不是合法的资源ID: {val}")
            return m.group(0)
        
        result = re.sub(r"\{([A-Za-z0-9_]+)\}", repl, url_s)
        if test_case.param_name == "video_id":
            logger.info(f"[DEBUG-TC-URL] _repl_path 输出: {result}")
        return result

    def _apply_value_to_position(pos: str, val: Any, is_target: bool):
        """应用值到指定位置"""
        nonlocal params
        if val is None or (isinstance(val, str) and val == ""):
            return False
        
        applied = False
        if pos == "query":
            q = params.get("params", {}) if isinstance(params.get("params"), dict) else {}
            for a in aliases:
                if a in q:
                    if remove_flag and is_target:
                        _remove(q, a)
                    else:
                        _set(q, a, (conflict_val if conflict_flag and is_target else val))
                        applied = True
                        break
            params["params"] = q
        elif pos == "body":
            b = params.get("json", {}) if isinstance(params.get("json"), dict) else {}
            if not b:
                b = params.get("data", {}) if isinstance(params.get("data"), dict) else {}
            for a in aliases:
                if a in b:
                    if remove_flag and is_target:
                        _remove(b, a)
                    else:
                        _set(b, a, (conflict_val if conflict_flag and is_target else val))
                        applied = True
                        break
            if "json" in params:
                params["json"] = b
            else:
                params["data"] = b
        elif pos == "path":
            url_s = params.get("url", "")
            if isinstance(url_s, str) and url_s:
                if not (remove_flag and is_target):
                    # 🆕 增强诊断：在处理path之前记录详细信息
                    if test_case.param_name == "video_id":
                        logger.info(f"[DEBUG-Path-Apply] ========== 处理path位置 ==========")
                        logger.info(f"[DEBUG-Path-Apply] 当前URL: {url_s}")
                        logger.info(f"[DEBUG-Path-Apply] 将使用的val: {val} (type={type(val)}, is_None={val is None})")
                        logger.info(f"[DEBUG-Path-Apply] is_target: {is_target}")
                        logger.info(f"[DEBUG-Path-Apply] aliases: {aliases}")
                    
                    new_url = _repl_path(url_s, aliases, (conflict_val if conflict_flag and is_target else val))
                    
                    # 🆕 记录占位符替换结果
                    if test_case.param_name == "video_id":
                        logger.info(f"[DEBUG-Path-Apply] _repl_path结果: {new_url} (是否改变={new_url != url_s})")
                    
                    # 如果 _repl_path 无法替换（URL 中没有占位符），尝试替换路径中的数字默认值
                    if new_url == url_s and val not in (None, "") and is_valid_resource_id(val):
                        # URL 可能包含默认值（如 /videos/0），尝试智能替换最后一个路径段
                        parts = url_s.rsplit('/', 1)
                        if len(parts) == 2:
                            last_segment = parts[1]
                            # 🆕 记录智能替换条件检查
                            if test_case.param_name == "video_id":
                                logger.info(f"[DEBUG-Path-Apply] 智能替换检查: last_segment={last_segment}, isdigit={last_segment.isdigit()}")
                            # 如果最后一个路径段是数字（可能是默认值），替换它
                            if last_segment.isdigit():
                                new_url = f"{parts[0]}/{val}"
                                if test_case.param_name == "video_id":
                                    logger.info(f"[DEBUG-Path-Apply] ✓ 智能替换路径默认值: {last_segment} -> {val}")
                    else:
                        # 🆕 记录为什么没有进行智能替换
                        if test_case.param_name == "video_id" and new_url == url_s:
                            logger.info(f"[DEBUG-Path-Apply] ⚠️ 跳过智能替换: val={val}, is_None={val is None}, is_valid={is_valid_resource_id(val) if val not in (None, '') else False}")
                    
                    if test_case.param_name == "video_id":
                        logger.info(f"[DEBUG-Path-Apply] 最终URL: {new_url}")
                        logger.info(f"[DEBUG-Path-Apply] ==========================================")
                    
                    params["url"] = new_url
                    applied = True
        elif pos == "header":
            h = params.get("headers", {}) if isinstance(params.get("headers"), dict) else {}
            for a in aliases:
                if a in h:
                    if remove_flag and is_target:
                        _remove(h, a)
                    else:
                        _set(h, a, (conflict_val if conflict_flag and is_target else val))
                        applied = True
                        break
            params["headers"] = h
        return applied

    if target_pos:
        # One-hot模式：完整实现
        # 1. target位置：使用value_source池（如B池）
        # 2. 非target位置：使用non_target_source池（默认为C池）或F池（混合场景）
        
        # ✅ 修复：使用 TestCase.locations（构建时确定的所有位置），而不是动态检测
        # 动态检测可能失败，因为运行时某些参数可能还未填充到请求中
        all_positions = test_case.locations or []
        
        # 如果TestCase.locations为空，回退到动态检测
        if not all_positions:
            all_positions = _detect_param_locations(step_obj, aliases)
            logger.warning(f"[DEBUG-OneHot] ⚠️ TestCase.locations为空，使用动态检测: {all_positions}")
        
        # 最后的保底：至少包含target_pos
        if not all_positions:
            all_positions = [target_pos]
            logger.warning(f"[DEBUG-OneHot] ⚠️ 无法确定位置，仅使用target_pos: {all_positions}")
        
        logger.info(f"[DEBUG-OneHot] ========== ONE-HOT 模式 ==========")
        logger.info(f"[DEBUG-OneHot] 参数名: {test_case.param_name}")
        logger.info(f"[DEBUG-OneHot] target_pos: {target_pos}")
        logger.info(f"[DEBUG-OneHot] all_positions (from TestCase.locations): {all_positions}")
        logger.info(f"[DEBUG-OneHot] value_source: {test_case.value_source}")
        logger.info(f"[DEBUG-OneHot] target位置的值: {value} (type={type(value)})")
        
        # 获取非target位置使用的池（默认C池，混合场景可指定F池）
        non_target_source = test_case.extra_params.get("non_target_source", "C") if isinstance(test_case.extra_params, dict) else "C"
        logger.info(f"[DEBUG-OneHot] 非target位置池: {non_target_source}")
        
        # 🆕 增强：显示传入的pools参数
        if pools:
            logger.info(f"[DEBUG-OneHot] 传入的pools键: {list(pools.keys())}")
            if non_target_source in pools:
                non_target_pool = pools[non_target_source]
                logger.info(f"[DEBUG-OneHot] {non_target_source}池内容（前5项）: {dict(list(non_target_pool.items())[:5]) if isinstance(non_target_pool, dict) else type(non_target_pool)}")
        else:
            logger.warning(f"[DEBUG-OneHot] ⚠️ pools参数为None或空")
        
        non_target_value = _get_value_from_pool(non_target_source)
        logger.info(f"[DEBUG-OneHot] 从{non_target_source}池获取的非target值: {non_target_value} (type={type(non_target_value)})")
        
        for pos in all_positions:
            if pos == target_pos:
                # 被测位置：使用指定池（value_source，如B池）
                logger.info(f"[DEBUG-OneHot] → 处理target位置 [{pos}]: 使用{test_case.value_source}池的值={value}")
                _apply_value_to_position(pos, value, is_target=True)
            else:
                # 非被测位置：使用non_target_source池值
                if non_target_value is not None:
                    logger.info(f"[DEBUG-OneHot] → 处理非target位置 [{pos}]: 使用{non_target_source}池的值={non_target_value}")
                    _apply_value_to_position(pos, non_target_value, is_target=False)
                else:
                    logger.warning(f"[DEBUG-OneHot] ⚠️ 非target位置 [{pos}] 无法从{non_target_source}池获取值")
        
        logger.info(f"[DEBUG-OneHot] ===========================================")
    else:
        # 传统模式：修改所有检测到的位置（用于single position测试）
        positions = [l for l in (test_case.locations or []) if l in ("path", "query", "body", "header")]
        if not positions:
            positions = ["query"]
        
        for pos in positions:
            _apply_value_to_position(pos, value, is_target=True)

    step_obj["request_params"] = {"type": "request", "parameters": params}
    return step_obj