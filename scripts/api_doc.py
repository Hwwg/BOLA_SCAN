import json
import os
import re
import sys
from typing import Any, Dict, Union

from prompt.synthesis_prompt import SyntheticPrompt
from gptreply.gpt_con import GPTReply


# 添加项目根目录和scripts目录到Python路径，确保可导入本地模块
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
if current_dir not in sys.path:
    sys.path.append(current_dir)
if project_root not in sys.path:
    sys.path.append(project_root)

try:
    from jsontools import JsonTools
except ModuleNotFoundError:
    from scripts.jsontools import JsonTools

class ApiDoc:
    PARAM_SEGMENT_RE = re.compile(r"^\{[^{}]+\}$")
    VERSION_SEGMENT_RE = re.compile(r"^v\d+(?:\.\d+)*$", re.IGNORECASE)
    NOISE_SEGMENTS = {
        "api",
        "app-api",
        "manage-api",
        "openapi",
        "rest",
        "rest-api",
    }
    GENERIC_SEGMENT_TOKENS = {
        "add", "admin", "all", "auth", "by", "change", "check", "comment", "comments",
        "confirm", "convert", "create", "dashboard", "debug", "default", "delete", "detail",
        "download", "edit", "email", "export", "file", "files", "forgot", "get", "id",
        "import", "info", "internal", "list", "login", "logout", "manage", "me", "misc",
        "new", "password", "ping", "profile", "query", "register", "remove", "report",
        "reset", "save", "search", "select", "send", "signin", "signup", "status",
        "temp", "test", "token", "update", "upload", "verify", "view",
    }
    TREE_SELECT_SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "anchors": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "level": {"type": "integer"},
                        "keyword": {"type": "string"},
                    },
                    "required": ["level", "keyword"],
                },
            }
        },
        "required": ["anchors"],
    }

    def __init__(self, api_doc_path, model,excludes=None):
        self.api_doc_path = api_doc_path
        self.jsontools = JsonTools()
        self.api_doc = self.jsontools.read_json(api_doc_path)
        # 允许通过配置排除掉部分路径（前缀匹配），例如 ['/apis/defaults']
        self.excludes = excludes or []
        self.gpt_reply = GPTReply(model)
        # self.jsontool = JsonTools()
        # self.lock = threading.Lock()
        self.syn_prompt = SyntheticPrompt()
    
    def api_function_tag(
        self,
        grouping_strategy: str = 'tree_select',
        min_group_size: int = 2,
        max_anchor_depth: int | None = None,
    ):
        """
        从postman collection中提取API信息并转换为指定格式
        分组策略（grouping_strategy）可选：
        - 'first_segment': 使用路径第一个段（兼容原始行为）
        - 'first_two': 使用前两个路径段
        - 'semantic_first': 采用数据驱动的公共前缀检测后，选取第一个非占位段
        - 'resource_crud'：采用数据驱动的公共前缀检测后，选取前两个非占位段并聚合（默认）
        - 'auto'：自动选择分组策略（通过 LLM 推断），仅在明确需要时使用
        注：默认使用 'resource_crud'；仅当传入 'auto' 时才触发 LLM 选择。该实现不再依赖任何静态字符串名单（如动作词/ID词表/同义词表），仅基于路径分布的统计检测公共前缀。
        """
        result = []
        
        def extract_apis_from_items(items, group_name=""):
            apis = {}
            
            for item in items:
                if 'item' in item:
                    # 递归处理子组
                    sub_apis = extract_apis_from_items(item['item'], item['name'])
                    apis.update(sub_apis)
                elif 'request' in item:
                    # 处理API请求
                    request = item['request']
                    method = request.get('method', 'GET')
                    
                    # 构建路径
                    if 'url' in request and 'path' in request['url']:
                        path_parts = request['url']['path']
                        # 排除指定前缀的路径，例如 /apis/defaults
                        if self._is_excluded(path_parts):
                            continue
                        # 将:param格式转换为{param}格式，例如: ":id" -> "{id}"
                        normalized_path = '/'.join(['{' + part[1:] + '}' if isinstance(part, str) and part.startswith(':') else part for part in path_parts])
                        api_path = f"/{normalized_path}"
                    else:
                        continue
                    
                    api_key = f"{method} {api_path}"
                    
                    # 提取请求参数
                    request_params = {}
                    
                    # 处理路径参数
                    if 'url' in request and 'variable' in request['url']:
                        for var in request['url']['variable']:
                            if 'key' in var:
                                key = var['key']
                                request_params[key] = {
                                    'in': 'path',
                                    'type': 'string',
                                    'required': True,
                                    'description': var.get('description', '')
                                }
                    
                    # 处理查询参数
                    if 'url' in request and 'query' in request['url']:
                        for query in request['url']['query']:
                            if 'key' in query and not query.get('disabled', False):
                                param_type = self._get_type_from_value(query.get('value', '<string>'))
                                request_params[query['key']] = {
                                    "type": param_type,
                                    "example": query.get('value', ''),
                                    "required": False,
                                    "in": "params"
                                }
                    
                    # 处理请求体参数（支持嵌套与数组，与响应参数类似的扁平化表示）
                    if 'body' in request and request['body']:
                        body = request['body']
                        if body.get('mode') == 'raw' and 'raw' in body:
                            try:
                                import json as json_lib
                                body_data = json_lib.loads(body['raw'])

                                def extract_request_fields(data, prefix=""):
                                    fields = {}
                                    if isinstance(data, dict):
                                        for key, value in data.items():
                                            field_key = f"{prefix}{key}" if prefix else key
                                            if isinstance(value, dict):
                                                # 嵌套对象，使用点号分隔
                                                fields.update(extract_request_fields(value, f"{field_key}."))
                                            elif isinstance(value, list):
                                                # 处理数组（支持多层嵌套）
                                                if value:
                                                    first = value[0]
                                                    if isinstance(first, dict):
                                                        # 数组对象，使用[]表示，然后继续下钻
                                                        fields.update(extract_request_fields(first, f"{field_key}[]."))
                                                    elif isinstance(first, list):
                                                        # 多维数组，增加一层[]后继续处理
                                                        fields.update(extract_request_fields(first, f"{field_key}[][]."))
                                                    else:
                                                        # 基础类型数组，生成 field[]
                                                        param_type = self._get_type_from_value(first)
                                                        fields[f"{field_key}[]"] = {
                                                            "type": param_type,
                                                            "example": str(first).strip("<>"),
                                                            "required": True,
                                                            "in": "body"
                                                        }
                                                else:
                                                    # 空数组，类型不明，默认string数组
                                                    fields[f"{field_key}[]"] = {
                                                        "type": "string",
                                                        "required": True,
                                                        "in": "body"
                                                    }
                                            else:
                                                # 基础类型
                                                param_type = self._get_type_from_value(value)
                                                fields[field_key] = {
                                                    "type": param_type,
                                                    "example": str(value).strip("<>"),
                                                    "required": True,
                                                    "in": "body"
                                                }
                                    elif isinstance(data, list):
                                        # 顶层为数组（支持多维数组）
                                        if data:
                                            first = data[0]
                                            if isinstance(first, dict):
                                                # 顶层数组对象 -> 使用 []. 前缀下钻
                                                fields.update(extract_request_fields(first, f"[]."))
                                            elif isinstance(first, list):
                                                # 顶层多维数组 -> 使用 [][] 前缀继续下钻
                                                fields.update(extract_request_fields(first, f"[][]."))
                                            else:
                                                # 顶层基础类型数组 -> 生成 []
                                                param_type = self._get_type_from_value(first)
                                                fields[f"[]"] = {
                                                    "type": param_type,
                                                    "example": str(first).strip("<>"),
                                                    "required": True,
                                                    "in": "body"
                                                }
                                        else:
                                            # 顶层空数组，默认 string 数组
                                            fields[f"[]"] = {
                                                "type": "string",
                                                "required": True,
                                                "in": "body"
                                            }
                                    else:
                                        # 根如果不是对象/数组，作为整体body的一个值
                                        param_type = self._get_type_from_value(data)
                                        fields[f"body"] = {
                                            "type": param_type,
                                            "example": str(data).strip("<>"),
                                            "required": True,
                                            "in": "body"
                                        }
                                    return fields

                                request_params.update(extract_request_fields(body_data))
                            except Exception:
                                pass
                    
                        # 新增：处理 formdata 格式（multipart/form-data）
                        elif body.get('mode') == 'formdata' and 'formdata' in body:
                            for form_field in body['formdata']:
                                if not isinstance(form_field, dict):
                                    continue
                                key = form_field.get('key')
                                field_type = form_field.get('type', 'text')
                                if not key or form_field.get('disabled'):
                                    continue
                                
                                if field_type == 'file':
                                    # 文件类型参数
                                    desc_content = ''
                                    if isinstance(form_field.get('description'), dict):
                                        desc_content = form_field.get('description', {}).get('content', '')
                                    request_params[key] = {
                                        "type": "file",
                                        "required": 'Required' in desc_content or 'required' in desc_content.lower(),
                                        "in": "formdata"
                                    }
                                else:
                                    # 文本类型参数
                                    param_type = self._get_type_from_value(form_field.get('value', '<string>'))
                                    desc_content = ''
                                    if isinstance(form_field.get('description'), dict):
                                        desc_content = form_field.get('description', {}).get('content', '')
                                    request_params[key] = {
                                        "type": param_type,
                                        "example": form_field.get('value', ''),
                                        "required": 'Required' in desc_content or 'required' in desc_content.lower(),
                                        "in": "formdata"
                                    }
                    
                    # 提取响应参数
                    response_params = {}
                    if 'response' in item and item['response']:
                        for response in item['response']:
                            if 'body' in response and response['body']:
                                try:
                                    import json as json_lib
                                    response_data = json_lib.loads(response['body'])
                                    status_code = str(response.get('code', 200))
                                    content_type = self._get_content_type(response.get('header', []))
                                    
                                    def extract_response_fields(data, prefix=""):
                                        fields = {}
                                        
                                        def add_basic(key, value):
                                            param_type = self._get_type_from_value(value)
                                            fields[key] = {
                                                "type": param_type,
                                                "status_code": status_code,
                                                "content_type": content_type
                                            }
                                        
                                        def expand_list(prefix_key, arr):
                                            # 每一层数组统一追加一次 []
                                            if arr:
                                                first = arr[0]
                                                base = f"{prefix_key}[]" if prefix_key else "[]"
                                                if isinstance(first, dict):
                                                    # 数组元素为对象，继续下钻并追加 '.'
                                                    nested = extract_response_fields(first, f"{base}.")
                                                    fields.update(nested)
                                                elif isinstance(first, list):
                                                    # 数组元素仍为数组，递归但不加 '.'，下一层会再追加 []
                                                    nested = extract_response_fields(first, base)
                                                    fields.update(nested)
                                                else:
                                                    # 基础类型数组，直接记录 base[] 的类型
                                                    add_basic(base, first)
                                            else:
                                                base = f"{prefix_key}[]" if prefix_key else "[]"
                                                fields[base] = {
                                                    "type": "string",
                                                    "status_code": status_code,
                                                    "content_type": content_type
                                                }
                                            return fields
                                        if isinstance(data, dict):
                                            for key, value in data.items():
                                                base = f"{prefix}{key}" if prefix else key
                                                if isinstance(value, dict):
                                                    nested_fields = extract_response_fields(value, f"{base}.")
                                                    fields.update(nested_fields)
                                                elif isinstance(value, list):
                                                    expand_list(base, value)
                                                else:
                                                    add_basic(base, value)
                                        elif isinstance(data, list):
                                            expand_list(prefix, data)
                                        
                                        return fields
                                    
                                    response_fields = extract_response_fields(response_data)
                                    response_params.update(response_fields)
                                except:
                                    pass
                    
                    apis[api_key] = {
                        "request_parameters": request_params,
                        "response_parameters": response_params
                    }
            
            return apis
        
        # 处理顶级items
        all_apis = extract_apis_from_items(self.api_doc['item'])
        if isinstance(grouping_strategy, str) and grouping_strategy.lower() in {"none", "no_group", "no-group", "flat", "all"}:
            return [{"all_apis": all_apis}]
        grouped_apis = self._group_apis_by_tree_select(all_apis)
        return [{group_name: apis} for group_name, apis in grouped_apis.items()]

        # 数据驱动的公共前缀检测（不使用任何静态字符串名单）
        # 1) 统计所有接口路径的首段分布，若某个首段占比超过阈值，则视为公共前缀
        # 2) 在具有该首段的路径中，进一步统计第二段分布，若某个第二段占比超过阈值，则也视为公共前缀
        # 3) 计算分组锚点时跳过这些公共前缀；只取非占位的真实段
        from collections import Counter
        prefix_threshold = 0.6  # 可按需调整分界比例
        all_paths = []
        for k in all_apis.keys():
            p = k.split(' ', 1)[1] if ' ' in k else k
            segs = [s for s in p.split('/') if s]
            if segs:
                all_paths.append(segs)

        skip_first = None
        skip_second = None
        if all_paths:
            first_counts = Counter(seg[0].lower() for seg in all_paths if seg)
            total_paths = len(all_paths)
            if first_counts:
                top_first, top_first_count = first_counts.most_common(1)[0]
                if total_paths and (top_first_count / total_paths) >= prefix_threshold:
                    skip_first = top_first
                    # 统计在首段为该值的路径集合中的第二段分布
                    scoped = [seg for seg in all_paths if len(seg) >= 2 and seg[0].lower() == skip_first]
                    if scoped:
                        second_counts = Counter(seg[1].lower() for seg in scoped)
                        top_second, top_second_count = second_counts.most_common(1)[0]
                        if (top_second_count / len(scoped)) >= prefix_threshold:
                            skip_second = top_second
        
        # 根据策略确定功能组名（统一实现，删除静态字符串匹配）
        def _determine_group_name(api_key: str, strategy: str, max_segments: int = 2) -> str:
            # 从 api_key 提取路径部分
            path = api_key.split(' ', 1)[1] if ' ' in api_key else api_key
            parts = [p for p in path.split('/') if p]
            if not parts:
                return 'Default'

            s = (strategy or '').lower()

            # 简单策略：首段/前两段
            if s in ('first_segment', 'first', 'segment', '1'):
                return parts[0]
            if s in ('first_two', 'two', '2'):
                return '/'.join(parts[:2]) if len(parts) >= 2 else parts[0]
            # 结构化辅助：识别路径变量占位符（非静态词表）
            def is_param(seg: str) -> bool:
                return isinstance(seg, str) and seg.startswith('{') and seg.endswith('}')

            # 数据驱动锚点计算：跳过公共前缀，提取前 1/2 个非占位段
            def compute_anchor_dynamic(max_segments=2):
                anchors = []
                idx = 0
                # 跳过动态检测到的第一级公共前缀
                if skip_first and idx < len(parts) and parts[0].lower() == skip_first:
                    idx += 1
                # 静态跳过：常见服务前缀
                if idx < len(parts) and parts[idx].lower() in ("api", "manage-api", "admin", "service"):
                    idx += 1
                # 跳过动态检测到的第二级公共前缀
                if skip_second and idx < len(parts) and parts[idx].lower() == skip_second:
                    idx += 1
                # 静态跳过：版本段 v1/v2...
                if idx < len(parts):
                    seg_low = parts[idx].lower()
                    if seg_low.startswith('v') and seg_low[1:].isdigit():
                        idx += 1
                # 采样锚点段（不包含占位符）
                while idx < len(parts) and len(anchors) < max_segments:
                    seg = parts[idx]
                    if is_param(seg):
                        idx += 1
                        continue
                    anchors.append(seg.strip().lower())
                    idx += 1
                return '/'.join(anchors) if anchors else None

            if s in ('semantic_first', 'semantic'):
                anchor = compute_anchor_dynamic(max_segments=1)
                if anchor:
                    return anchor
                # 兜底：取第一个非占位段，否则首段
                for seg in parts:
                    if not is_param(seg):
                        return seg.strip().lower()
                return parts[0]

            # 默认采用资源锚点（相当于 resource_crud），或未知策略时回退
            anchor = compute_anchor_dynamic(max_segments=max(1, int(max_segments or 2)))
            if anchor:
                return anchor
            return parts[0]
        
        def api_split_strategy(all_apis) -> Union[str, Dict[str, Any]]:
            # 给 LLM 一个“统计摘要 + 少量样本”，避免把所有接口 keys 全塞进去导致超长/不稳定
            keys = list(all_apis.keys())
            sample_n = 200
            sample_keys = keys[:sample_n]

            def _extract_path(api_key: str) -> str:
                return api_key.split(' ', 1)[1] if ' ' in api_key else api_key

            first_segs = []
            first_two = []
            for k in keys:
                p = _extract_path(k)
                segs = [s for s in p.split('/') if s]
                if not segs:
                    continue
                first_segs.append(segs[0].lower())
                if len(segs) >= 2:
                    first_two.append(f"{segs[0].lower()}/{segs[1].lower()}")

            from collections import Counter
            top_first = Counter(first_segs).most_common(10)
            top_first_two = Counter(first_two).most_common(10)

            stats = {
                "total_apis": len(keys),
                "top_first_segments": top_first,
                "top_first_two_segments": top_first_two,
                "sample_size": min(sample_n, len(sample_keys)),
            }

            tmp_dict = {
                "api_data": json.dumps({"stats": stats, "samples": sample_keys}, ensure_ascii=False)
            }
            while True:
                try:
                    tmp_results = self.gpt_reply.getreply(
                        self.syn_prompt.synthesis_prompt("api_group_strategy", tmp_dict)
                    )
                    resutls = self.jsontools.list_formatting(tmp_results)
                    resutls = (resutls or "").strip()
                    # 新版 prompt：期望返回 JSON 规则对象
                    if resutls.startswith("{") and resutls.endswith("}"):
                        try:
                            plan = json.loads(resutls)
                            if isinstance(plan, dict):
                                strat = str(plan.get("strategy", "")).strip()
                                if strat in {"first_segment", "first_two", "resource_crud", "adaptive"}:
                                    return plan
                        except Exception:
                            pass

                    # 旧版兼容：返回单个策略名
                    if resutls not in {"first_segment", "first_two", "resource_crud", "adaptive", "semantic_first"}:
                        resutls = "adaptive"
                    break
                except:
                    pass
            return resutls
        
        # 按组织结构分组
        grouped_apis = {}
        # 仅当显式请求自动选择时，才通过 LLM 推断策略
        if isinstance(grouping_strategy, str) and grouping_strategy.lower() == 'auto':
            plan = api_split_strategy(all_apis)
            # auto 新行为：允许 LLM 返回切片规则（JSON）
            if isinstance(plan, dict):
                grouping_strategy = str(plan.get("strategy", "adaptive")).strip() or "adaptive"
                try:
                    max_anchor_depth = int(plan.get("max_anchor_depth", max_anchor_depth))
                except Exception:
                    pass
                try:
                    min_group_size = int(plan.get("min_group_size", min_group_size))
                except Exception:
                    pass
            else:
                grouping_strategy = plan

        # 自适应：未指定 max_anchor_depth 时按当前 API 路径的最大深度计算，不使用固定默认值。
        if isinstance(grouping_strategy, str) and grouping_strategy.lower() == 'adaptive':
            keys = list(all_apis.keys())
            if max_anchor_depth is None:
                max_d = max(
                    1,
                    max(
                        (
                            len([seg for seg in (api_key.split(" ", 1)[1] if " " in api_key else api_key).split("/") if seg])
                            for api_key in keys
                        ),
                        default=1,
                    ),
                )
            else:
                max_d = max(1, int(max_anchor_depth))
            min_sz = max(1, int(min_group_size or 2))
            # 策略：先用更细粒度（max_anchor_depth）尽可能多分组；再把小组（size < min_group_size）按父前缀逐级合并
            grouping_strategy = f"resource_crud@{max_d}"

        for api_key, api_data in all_apis.items():
            # 支持 resource_crud@k 这种内部策略
            strat = grouping_strategy
            max_seg = 2
            if isinstance(strat, str) and strat.startswith("resource_crud@"):
                try:
                    max_seg = int(strat.split("@", 1)[1])
                except Exception:
                    max_seg = 2
                strat = "resource_crud"
            group_name = _determine_group_name(api_key, strat, max_segments=max_seg)
            if group_name not in grouped_apis:
                grouped_apis[group_name] = {}
            grouped_apis[group_name][api_key] = api_data

        # adaptive 的“后处理合并”：避免出现单接口组（或小于 min_group_size 的组），但尽量保留细分
        if isinstance(grouping_strategy, str) and grouping_strategy.lower().startswith("resource_crud@"):
            min_sz = max(1, int(min_group_size or 2))

            def _parent_group_name(gname: str):
                if not isinstance(gname, str):
                    return None
                if "/" not in gname:
                    return None
                return "/".join(gname.split("/")[:-1])

            def _find_existing_ancestor(gname: str):
                cand = _parent_group_name(gname)
                while cand:
                    if cand in grouped_apis:
                        return cand
                    cand = _parent_group_name(cand)
                return None

            def _largest_group_excluding(exclude: str):
                best = None
                best_sz = -1
                for gn, apis in grouped_apis.items():
                    if gn == exclude:
                        continue
                    sz = len(apis)
                    if sz > best_sz:
                        best = gn
                        best_sz = sz
                return best

            changed = True
            while changed:
                changed = False
                small_groups = [gn for gn, apis in grouped_apis.items() if len(apis) < min_sz]
                if not small_groups:
                    break
                for gn in sorted(small_groups):
                    if gn not in grouped_apis:
                        continue
                    apis = grouped_apis.get(gn) or {}
                    if len(apis) >= min_sz:
                        continue
                    target = _find_existing_ancestor(gn)
                    if not target:
                        target = _largest_group_excluding(gn)
                    if not target or target == gn:
                        continue
                    # merge
                    grouped_apis[target].update(apis)
                    del grouped_apis[gn]
                    changed = True
        
        # 转换为最终格式
        for group_name, apis in grouped_apis.items():
            result.append({group_name: apis})
        
        return result

    def _parse_api_path(self, api_key: str) -> list[str]:
        parts = api_key.split(" ", 1)
        if len(parts) != 2:
            return []
        path = parts[1].strip()
        raw_segments = [segment for segment in path.split("/") if segment]
        return [segment for segment in raw_segments if not self._is_noise_segment(segment)]

    def _is_noise_segment(self, segment: str) -> bool:
        normalized = segment.strip().lower()
        if not normalized:
            return True
        if normalized in self.NOISE_SEGMENTS:
            return True
        return bool(self.VERSION_SEGMENT_RE.match(normalized))

    def _longest_common_prefix(self, all_segments: list[list[str]]) -> list[str]:
        if not all_segments:
            return []
        prefix = list(all_segments[0])
        for segments in all_segments[1:]:
            common_len = 0
            for left, right in zip(prefix, segments):
                if left != right:
                    break
                common_len += 1
            prefix = prefix[:common_len]
            if not prefix:
                break
        return prefix

    def _normalize_segments(self, full_segments: list[str], common_prefix: list[str]) -> list[str]:
        if common_prefix and full_segments[: len(common_prefix)] == common_prefix:
            return full_segments[len(common_prefix) :]
        return list(full_segments)

    def _build_tree(self, normalized_paths: list[list[str]]) -> dict[str, Any]:
        tree: dict[str, Any] = {}
        for path_segments in normalized_paths:
            node = tree
            for segment in path_segments:
                node = node.setdefault(segment, {})
        return tree

    def _segment_tokens(self, segment: str) -> list[str]:
        return [token for token in re.split(r"[_\-]+", segment.lower()) if token]

    def _is_placeholder_segment(self, segment: str) -> bool:
        return bool(self.PARAM_SEGMENT_RE.match(segment.strip()))

    def _is_generic_segment(self, segment: str) -> bool:
        if self._is_placeholder_segment(segment):
            return True
        tokens = self._segment_tokens(segment)
        if not tokens:
            return True
        return all(token in self.GENERIC_SEGMENT_TOKENS for token in tokens)

    def _extract_json_payload(self, reply: Any) -> dict[str, Any]:
        if isinstance(reply, dict):
            return reply
        raw_text = str(reply).strip()
        fenced_payload = self.jsontools.list_formatting(raw_text)
        text = fenced_payload.strip() if fenced_payload else raw_text
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start == -1 or end == -1 or end <= start:
                raise
            return json.loads(text[start : end + 1])

    def _validate_anchors(self, anchors: Any) -> list[dict[str, Any]]:
        return self._validate_anchors_with_candidates(anchors, None)

    def _validate_anchors_with_candidates(
        self,
        anchors: Any,
        allowed_level_keywords: dict[int, set[str]] | None,
    ) -> list[dict[str, Any]]:
        if not isinstance(anchors, list):
            raise ValueError("anchors 不是数组")
        validated: list[dict[str, Any]] = []
        for anchor in anchors:
            if not isinstance(anchor, dict):
                continue
            try:
                level = int(anchor.get("level"))
            except (TypeError, ValueError):
                continue
            keyword = str(anchor.get("keyword", "")).strip()
            if level <= 0 or not keyword:
                continue
            if self.PARAM_SEGMENT_RE.match(keyword):
                continue
            lowered = keyword.lower()
            if lowered in {
                "list", "detail", "info", "query", "search", "get", "create",
                "add", "new", "update", "edit", "delete", "remove", "save",
                "export", "import", "reset", "test",
            }:
                continue
            if allowed_level_keywords is not None:
                valid_keywords = allowed_level_keywords.get(level, set())
                if keyword not in valid_keywords:
                    continue
            validated.append({"level": level, "keyword": keyword})
        return validated

    def _collect_level_keywords(self, normalized_full_map: dict[str, list[str]]) -> dict[int, list[str]]:
        level_keywords: dict[int, set[str]] = {}
        for segments in normalized_full_map.values():
            for idx, segment in enumerate(segments, start=1):
                if not segment or self.PARAM_SEGMENT_RE.match(segment):
                    continue
                level_keywords.setdefault(idx, set()).add(segment)
        return {level: sorted(keywords) for level, keywords in level_keywords.items()}

    def _select_tree_anchors(
        self,
        normalized_full_map: dict[str, list[str]],
        unassigned_keys: list[str],
        common_prefix: list[str],
        stage_index: int,
    ) -> list[dict[str, Any]]:
        remaining_map = {
            api_key: normalized_full_map.get(api_key, [])
            for api_key in unassigned_keys
            if normalized_full_map.get(api_key)
        }
        if not remaining_map:
            return []

        tree = self._build_tree(list(remaining_map.values()))
        complete_nodes = sorted(
            {
                "/".join(segments) if segments else "(root)"
                for segments in remaining_map.values()
            }
        )
        api_summary = [
            {
                "endpoint": api_key,
                "normalized_path": "/".join(remaining_map.get(api_key, [])),
            }
            for api_key in unassigned_keys
        ]
        allowed_level_keywords = self._collect_level_keywords(remaining_map)
        base_prompt_data = {
            "project_name": os.path.basename(os.path.dirname(self.api_doc_path)) if getattr(self, "api_doc_path", "") else "project",
            "stage_index": stage_index,
            "removed_prefixes": json.dumps(common_prefix, ensure_ascii=False),
            "allowed_level_keywords": json.dumps(allowed_level_keywords, ensure_ascii=False, indent=2),
            "api_tree": json.dumps(tree, ensure_ascii=False, indent=2),
            "complete_api_nodes": json.dumps(complete_nodes, ensure_ascii=False, indent=2),
            "api_summary": json.dumps(api_summary, ensure_ascii=False, indent=2),
        }

        last_error = None
        for attempt in range(1, 4):
            prompt_data = dict(base_prompt_data)
            if attempt > 1 and last_error:
                prompt_data["api_summary"] = (
                    base_prompt_data["api_summary"]
                    + "\n\nIMPORTANT RETRY NOTICE:\n"
                    + f"Previous output was invalid. Error: {last_error}\n"
                    + "You must output valid JSON only and choose anchors from the provided tree."
                )
            try:
                result = self.gpt_reply.getreply_json_schema(
                    self.syn_prompt.synthesis_prompt("api_group_tree_select", prompt_data),
                    schema_name="api_group_tree_select",
                    schema=self.TREE_SELECT_SCHEMA,
                )
                return self._validate_anchors_with_candidates(
                    result.get("anchors", []),
                    {level: set(keywords) for level, keywords in allowed_level_keywords.items()},
                )
            except Exception as exc:
                last_error = exc
        return []

    def _match_by_level_and_keyword(
        self,
        normalized_segments: list[str],
        level: int,
        keyword: str,
    ) -> bool:
        try:
            level = int(level)
        except (TypeError, ValueError):
            return False
        if level <= 0 or not keyword:
            return False
        if len(normalized_segments) < level:
            return False
        if normalized_segments[level - 1] != keyword:
            return False
        if len(normalized_segments) == level:
            return True
        child_segment = normalized_segments[level]
        if self._is_placeholder_segment(child_segment):
            return True
        # 当前层级分组只吸收“该节点本身 + 下一层为动作/通用节点”的接口；
        # 如果下一层已经是稳定业务节点，则留给后续阶段继续细分。
        if len(normalized_segments) > level + 1:
            return False
        return self._is_generic_segment(child_segment)

    def _reclaim_from_existing_groups(
        self,
        grouped_apis: dict[str, dict],
        normalized_full_map: dict[str, list[str]],
        level: int,
        keyword: str,
    ) -> dict[str, Any]:
        reclaimed: dict[str, Any] = {}
        for group_name in list(grouped_apis.keys()):
            if group_name == "other":
                continue
            group_apis = grouped_apis.get(group_name, {})
            to_remove = []
            for api_key, api_data in group_apis.items():
                normalized_segments = normalized_full_map.get(api_key, [])
                if self._match_by_level_and_keyword(normalized_segments, level, keyword):
                    reclaimed[api_key] = api_data
                    to_remove.append(api_key)
            for api_key in to_remove:
                group_apis.pop(api_key, None)
            if not group_apis:
                grouped_apis.pop(group_name, None)
        return reclaimed

    def _fallback_to_nearest_existing_group(
        self,
        normalized_segments: list[str],
        existing_groups: set[str],
    ) -> str:
        for segment in reversed(normalized_segments):
            if self.PARAM_SEGMENT_RE.match(segment):
                continue
            candidate = self._sanitize_group_name(segment)
            if candidate in existing_groups:
                return candidate
        return "other"

    def _sanitize_group_name(self, group_name: str) -> str:
        sanitized = re.sub(r"[^a-zA-Z0-9/_-]+", "_", group_name.strip()).strip("_")
        return sanitized or "unnamed"

    def group_existing_apis(self, all_apis: dict) -> dict[str, dict]:
        """
        对已抽取的 API 字典执行纯功能组分类，不做任何类型判定。
        """
        return self._group_apis_by_tree_select(all_apis)

    def _group_apis_by_tree_select(self, all_apis: dict) -> dict[str, dict]:
        full_path_map: dict[str, list[str]] = {}
        all_full_segments: list[list[str]] = []

        for api_key in all_apis:
            full_segments = self._parse_api_path(api_key)
            if not full_segments:
                continue
            all_full_segments.append(full_segments)
            full_path_map[api_key] = full_segments

        common_prefix = self._longest_common_prefix(all_full_segments)
        normalized_full_map = {
            api_key: self._normalize_segments(segments, common_prefix)
            for api_key, segments in full_path_map.items()
        }
        grouped_apis: dict[str, dict] = {}
        unassigned_keys = [api_key for api_key in all_apis if api_key in normalized_full_map]
        max_stage_count = max((len(segments) for segments in normalized_full_map.values()), default=0)

        for stage_index in range(1, max_stage_count + 1):
            if not unassigned_keys:
                break
            anchors = self._select_tree_anchors(
                normalized_full_map,
                unassigned_keys,
                common_prefix,
                stage_index,
            )
            if not anchors:
                break

            stage_assigned: set[str] = set()
            for anchor in anchors:
                level = anchor["level"]
                keyword = anchor["keyword"]
                group_name = self._sanitize_group_name(keyword)
                matched = {
                    api_key
                    for api_key in unassigned_keys
                    if self._match_by_level_and_keyword(normalized_full_map.get(api_key, []), level, keyword)
                }
                if not matched:
                    continue

                reclaimed = self._reclaim_from_existing_groups(
                    grouped_apis,
                    normalized_full_map,
                    level,
                    keyword,
                )
                target_group = grouped_apis.setdefault(group_name, {})
                for api_key in matched:
                    target_group[api_key] = all_apis[api_key]
                target_group.update(reclaimed)
                stage_assigned.update(matched)

            if not stage_assigned:
                break
            unassigned_keys = [api_key for api_key in unassigned_keys if api_key not in stage_assigned]

        existing_groups = set(grouped_apis.keys())
        for api_key in unassigned_keys:
            normalized_segments = normalized_full_map.get(api_key, [])
            fallback_group = self._fallback_to_nearest_existing_group(normalized_segments, existing_groups)
            grouped_apis.setdefault(fallback_group, {})[api_key] = all_apis[api_key]
            existing_groups.add(fallback_group)

        if not grouped_apis:
            grouped_apis["default"] = all_apis

        return grouped_apis

    def recursive_refine_groups(self, grouped_apis, max_depth=2, current_depth=0):
        """
        递归细分功能组
        
        Args:
            grouped_apis: 当前分组结果 {group_name: {api_key: api_data}}
            max_depth: 最大递归深度，避免过度细分
            current_depth: 当前递归深度
        
        Returns:
            refined_groups: 细分后的分组结果
        """
        if current_depth >= max_depth:
            return grouped_apis
        
        refined = {}
        
        for group_name, apis in grouped_apis.items():
            # 统计 add 类型接口数量
            add_apis = {k: v for k, v in apis.items() 
                        if v.get('type') == 'add'}
            
            # 触发条件：有2个或以上 add 接口
            if len(add_apis) >= 2:
                # 调用 LLM 判断是否需要细分
                should_split, split_plan = self._llm_judge_split(group_name, apis)
                
                if should_split and split_plan:
                    # 按照 LLM 的建议重新分组
                    sub_groups = self._split_group_by_plan(apis, split_plan)
                    # 递归检查新分组
                    refined.update(self.recursive_refine_groups(
                        sub_groups, max_depth, current_depth + 1
                    ))
                else:
                    refined[group_name] = apis
            else:
                refined[group_name] = apis
        
        return refined

    def _llm_judge_split(self, group_name, apis):
        """
        调用 LLM 判断功能组是否需要进一步细分
        
        Returns:
            (should_split: bool, split_plan: dict or None)
        """
        # 提取接口摘要信息
        api_summary = []
        for api_key, api_data in apis.items():
            api_summary.append({
                "endpoint": api_key,
                "type": api_data.get("type", "unknown"),
                "request_params": list(api_data.get("request_parameters", {}).keys())[:5],
                "response_params": list(api_data.get("response_parameters", {}).keys())[:5]
            })
        
        # 构建 LLM prompt
        prompt_data = {
            "group_name": group_name,
            "api_summary": api_summary,
            "add_count": sum(1 for a in api_summary if a['type'] == 'add')
        }
        
        try:
            llm_response = self.gpt_reply.getreply(
                self.syn_prompt.synthesis_prompt("api_group_refine_judge", prompt_data)
            )
            
            # 解析 LLM 返回
            result = eval(self.jsontools.list_formatting(llm_response))
            return result.get("should_split", False), result.get("split_plan")
        except Exception as e:
            # 如果LLM调用失败，返回False不细分
            return False, None

    def _split_group_by_plan(self, apis, split_plan):
        """
        根据 LLM 的细分方案重新分组
        
        split_plan 格式示例：
        {
            "sub_groups": [
                {"name": "identity/api/v2/user/video", "keywords": ["video"]},
                {"name": "identity/api/v2/user/profile", "keywords": ["user", "dashboard", "picture"]}
            ]
        }
        """
        sub_groups = {}
        assigned_apis = set()
        
        for sub_plan in split_plan.get("sub_groups", []):
            sub_name = sub_plan["name"]
            keywords = sub_plan.get("keywords", [])
            
            sub_apis = {}
            for api_key, api_data in apis.items():
                if api_key in assigned_apis:
                    continue
                # 检查 API 是否包含关键词
                if any(kw in api_key.lower() for kw in keywords):
                    sub_apis[api_key] = api_data
                    assigned_apis.add(api_key)
            
            if sub_apis:
                sub_groups[sub_name] = sub_apis
        
        # 未分配的API保留在原组
        remaining = {k: v for k, v in apis.items() if k not in assigned_apis}
        if remaining:
            original_group = split_plan.get('original_group', 'misc')
            sub_groups[f"{original_group}_remaining"] = remaining
        
        return sub_groups

    def _is_excluded(self, path_parts):
        """根据配置判断是否需要排除该路径（按前缀匹配）。
        path_parts 为 Postman 中的路径分段数组，例如 ['apis','defaults','list']
        """
        if not self.excludes:
            return False
        try:
            path_str = '/' + '/'.join([p for p in path_parts if isinstance(p, str)])
        except Exception:
            return False
        for prefix in self.excludes:
            if prefix and path_str.startswith(prefix):
                return True
        return False
    
    def _get_type_from_value(self, value):
        """根据值推断类型"""
        if isinstance(value, str):
            value_str = value.strip('<>')
            if value_str in ['long', 'integer', 'int']:
                return 'integer'
            elif value_str in ['string', 'str']:
                return 'string'
            elif value_str in ['boolean', 'bool']:
                return 'boolean'
            elif value_str in ['number', 'float', 'double']:
                return 'number'
            else:
                return 'string'
        elif isinstance(value, int):
            return 'integer'
        elif isinstance(value, float):
            return 'number'
        elif isinstance(value, bool):
            return 'boolean'
        else:
            return 'string'
    
    def _get_content_type(self, headers):
        """从响应头中提取content-type"""
        for header in headers:
            if header.get('key', '').lower() == 'content-type':
                return header.get('value', 'application/json')
        return 'application/json'
    
    def convert_and_save(self, output_path):
        """转换并保存到指定路径"""
        converted_data = self.api_function_tag()
        self.jsontools.write_json(output_path, converted_data)
        return converted_data

if __name__ == "__main__":
    import os
    # 读取postman collection文件
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    postman_path = os.path.join(project_root, "postman_test.json")
    apidoc = ApiDoc(postman_path)
    
    # # 转换并保存结果到项目约定路径
    # output_path = os.path.join(project_root, 'cache', 'mall', 'api_doc_with_type.json')
    # converted_data = apidoc.convert_and_save(output_path)
    
    # print(f"转换完成！共提取了 {sum(len(group_data) for group_dict in converted_data for group_data in group_dict.values())} 个API")
    # print(f"结果已保存到: {output_path}")
