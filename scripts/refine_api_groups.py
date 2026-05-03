#!/usr/bin/env python3
"""
API 功能组递归细分脚本

功能：
- 读取现有的 api_doc_with_type.json
- 对每个功能组构建路径层级树
- 删除当前功能组内所有接口共享的公共前缀层
- 调用 LLM 输出结构化 selector 规则
- 根据 selector 回收接口，生成更细粒度的功能组
- 最终结果直接覆盖原文件

使用方式：
    python scripts/refine_api_groups.py --project crapi
    python scripts/refine_api_groups.py --project crapi --max-depth 4
"""

import argparse
import json
import logging
import os
import re
import shutil
import sys
from datetime import datetime
from typing import Any

# 添加项目路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from gptreply.gpt_con import GPTReply
from prompt.synthesis_prompt import SyntheticPrompt
from scripts.api_doc import ApiDoc
from scripts.jsontools import JsonTools
from utils.cache_utils import get_project_cache_dir

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='[%(levelname)s] %(asctime)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ApiGroupRefiner:
    """API 功能组递归细分器"""

    PARAM_SEGMENT_RE = re.compile(r"^\{[^{}]+\}$")
    TREE_SELECT_SCHEMA = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "functional_groups": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "group_name": {"type": "string"},
                        "should_continue_refine": {"type": "boolean"},
                        "selectors": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "level": {"type": "integer"},
                                    "keyword": {"type": "string"},
                                    "include_self": {"type": "boolean"},
                                    "include_descendants": {"type": "boolean"},
                                    "descendant_depth": {
                                        "anyOf": [
                                            {"type": "integer"},
                                            {"type": "string", "enum": ["all"]},
                                        ]
                                    },
                                },
                                "required": [
                                    "level",
                                    "keyword",
                                    "include_self",
                                    "include_descendants",
                                    "descendant_depth",
                                ],
                            },
                        },
                    },
                    "required": ["group_name", "should_continue_refine", "selectors"],
                },
            }
        },
        "required": ["functional_groups"],
    }

    def __init__(self, model: str = "gpt-4o-mini"):
        self.model = model
        self.gpt_reply = GPTReply(model)
        self.syn_prompt = SyntheticPrompt()
        self.jsontools = JsonTools()

    def load_api_doc(self, filepath: str) -> list:
        return self.jsontools.read_json(filepath)

    def save_api_doc(self, filepath: str, data: list):
        self.jsontools.write_json(filepath, data)

    def count_add_apis(self, apis: dict) -> int:
        return sum(
            1
            for api_data in apis.values()
            if isinstance(api_data, dict) and api_data.get("type") == "add"
        )

    def _collect_doc_stats(self, api_doc: list) -> dict[str, Any]:
        total_apis = 0
        typed_apis = 0
        add_apis = 0
        zero_add_groups = []

        for group_item in api_doc:
            if not isinstance(group_item, dict):
                continue
            for group_name, apis in group_item.items():
                if not isinstance(apis, dict):
                    continue
                current_add = 0
                for api_info in apis.values():
                    total_apis += 1
                    if isinstance(api_info, dict):
                        api_type = api_info.get("type")
                        if api_type:
                            typed_apis += 1
                        if api_type == "add":
                            current_add += 1
                add_apis += current_add
                if current_add == 0:
                    zero_add_groups.append(group_name)

        return {
            "total_apis": total_apis,
            "typed_apis": typed_apis,
            "add_apis": add_apis,
            "zero_add_groups": zero_add_groups,
        }

    def _compute_dynamic_max_depth(self, api_doc: list) -> int:
        longest_depth = 0
        for group_item in api_doc:
            if not isinstance(group_item, dict):
                continue
            for apis in group_item.values():
                if not isinstance(apis, dict):
                    continue
                for api_key in apis:
                    depth = len(self._parse_api_path(api_key))
                    longest_depth = max(longest_depth, depth)
        return max(1, longest_depth - 1)

    def _collapse_singleton_leaf_groups(self, api_doc: list) -> tuple[list, list[tuple[str, str, int]]]:
        group_map: dict[str, dict] = {}
        ordered_names: list[str] = []
        for group_item in api_doc:
            if not isinstance(group_item, dict):
                continue
            for group_name, apis in group_item.items():
                if not isinstance(apis, dict):
                    continue
                if group_name not in group_map:
                    ordered_names.append(group_name)
                    group_map[group_name] = dict(apis)
                else:
                    group_map[group_name].update(apis)

        merged_records: list[tuple[str, str, int]] = []
        changed = True
        while changed:
            changed = False
            for group_name in sorted(list(group_map.keys()), key=lambda name: name.count("/"), reverse=True):
                apis = group_map.get(group_name)
                if apis is None or len(apis) != 1:
                    continue
                if "/" not in group_name:
                    continue
                ancestor_name = group_name.rsplit("/", 1)[0]
                while ancestor_name and ancestor_name not in group_map and "/" in ancestor_name:
                    ancestor_name = ancestor_name.rsplit("/", 1)[0]
                ancestor_apis = group_map.get(ancestor_name)
                if ancestor_apis is None:
                    continue
                ancestor_apis.update(apis)
                merged_records.append((group_name, ancestor_name, len(apis)))
                del group_map[group_name]
                changed = True

        collapsed_doc = [{name: group_map[name]} for name in ordered_names if name in group_map]
        return collapsed_doc, merged_records

    def _parse_api_path(self, api_key: str) -> list[str]:
        parts = api_key.split(" ", 1)
        if len(parts) != 2:
            return []
        path = parts[1].strip()
        raw_segments = [segment for segment in path.split("/") if segment]
        return [segment for segment in raw_segments if not self.PARAM_SEGMENT_RE.match(segment)]

    def _extract_api_summary(self, apis: dict) -> list[dict[str, Any]]:
        api_summary = []
        for api_key, api_data in apis.items():
            if not isinstance(api_data, dict):
                continue
            api_summary.append(
                {
                    "endpoint": api_key,
                    "type": api_data.get("type", "unknown"),
                    "request_params": list(api_data.get("request_parameters", {}).keys())[:5],
                    "response_params": list(api_data.get("response_parameters", {}).keys())[:5],
                }
            )
        return api_summary

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

    def _prepare_group_context(self, apis: dict) -> dict[str, Any]:
        full_path_map: dict[str, list[str]] = {}
        parent_path_map: dict[str, list[str]] = {}
        all_full_segments: list[list[str]] = []

        for api_key in apis:
            full_segments = self._parse_api_path(api_key)
            if not full_segments:
                continue
            all_full_segments.append(full_segments)
            full_path_map[api_key] = full_segments
            parent_path_map[api_key] = full_segments[:-1] if len(full_segments) > 1 else full_segments

        common_prefix = self._longest_common_prefix(all_full_segments)

        normalized_full_map = {
            api_key: self._normalize_segments(segments, common_prefix)
            for api_key, segments in full_path_map.items()
        }
        normalized_parent_map = {
            api_key: self._normalize_segments(segments, common_prefix)
            for api_key, segments in parent_path_map.items()
        }

        tree = self._build_tree(list(normalized_parent_map.values()))
        complete_nodes = sorted(
            {
                "/".join(segments) if segments else "(root)"
                for segments in normalized_full_map.values()
            }
        )

        return {
            "common_prefix": common_prefix,
            "tree": tree,
            "full_map": normalized_full_map,
            "parent_map": normalized_parent_map,
            "complete_nodes": complete_nodes,
            "allowed_level_keywords": self._collect_level_keywords(normalized_full_map),
        }

    def _collect_level_keywords(self, normalized_full_map: dict[str, list[str]]) -> dict[int, list[str]]:
        level_keywords: dict[int, set[str]] = {}
        for segments in normalized_full_map.values():
            for idx, segment in enumerate(segments, start=1):
                if not segment or self.PARAM_SEGMENT_RE.match(segment):
                    continue
                level_keywords.setdefault(idx, set()).add(segment)
        return {level: sorted(keywords) for level, keywords in level_keywords.items()}

    def _filter_level_keywords(
        self,
        allowed_level_keywords: dict[int, list[str]],
        min_level: int,
    ) -> dict[int, list[str]]:
        return {
            level: keywords
            for level, keywords in allowed_level_keywords.items()
            if level >= min_level and keywords
        }

    def _should_skip_llm_refine(self, context: dict[str, Any]) -> tuple[bool, str]:
        tree = context.get("tree", {})
        complete_nodes = context.get("complete_nodes", [])

        if not tree:
            return True, "去除公共前缀后已无可用层级树"
        if len(complete_nodes) <= 1:
            return True, "去除公共前缀后完整接口节点不足 2 个"

        return False, ""

    def _extract_json_payload(self, reply: Any) -> dict[str, Any]:
        if isinstance(reply, dict):
            return reply
        if isinstance(reply, list):
            raise ValueError("LLM 返回了 JSON 数组，预期为 JSON 对象")

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

    def llm_select_groups(
        self,
        project_name: str,
        group_name: str,
        apis: dict,
        context: dict[str, Any],
        min_level: int = 1,
    ) -> list[dict[str, Any]]:
        max_attempts = 3
        filtered_level_keywords = self._filter_level_keywords(
            context.get("allowed_level_keywords", {}),
            min_level,
        )
        if not filtered_level_keywords:
            raise ValueError(f"当前组在 min_level={min_level} 下已无可选层级节点")
        prompt_data = {
            "project_name": project_name,
            "group_name": group_name,
            "removed_prefixes": json.dumps(context["common_prefix"], ensure_ascii=False),
            "min_level": min_level,
            "allowed_level_keywords": json.dumps(filtered_level_keywords, ensure_ascii=False, indent=2),
            "api_tree": json.dumps(context["tree"], ensure_ascii=False, indent=2),
            "complete_api_nodes": json.dumps(context["complete_nodes"], ensure_ascii=False, indent=2),
            "api_summary": json.dumps(self._extract_api_summary(apis), ensure_ascii=False, indent=2),
            "retry_notice": "None",
        }

        last_error = None
        for attempt in range(1, max_attempts + 1):
            if attempt > 1:
                prompt_data["retry_notice"] = (
                    "Previous output was invalid.\n"
                    + f"Validation error: {last_error}\n"
                    + "Regenerate the full JSON object from scratch.\n"
                    + "You must output a valid JSON object only, matching the required schema exactly.\n"
                    + "For every selector, you must explicitly include:\n"
                    + "- level\n"
                    + "- keyword\n"
                    + "- include_self (boolean)\n"
                    + "- include_descendants (boolean)\n"
                    + '- descendant_depth (non-negative integer or "all")\n'
                    + "Do not omit any of these fields.\n"
                    + "If the validation error says a (level, keyword) pair is not in Allowed Level Keywords, you must choose only from the exact pairs listed there."
                )

            result = self.gpt_reply.getreply_json_schema(
                self.syn_prompt.synthesis_prompt("api_group_tree_select", prompt_data),
                schema_name="api_group_tree_select",
                schema=self.TREE_SELECT_SCHEMA,
            )
            try:
                allowed_level_keywords = {
                    level: set(keywords)
                    for level, keywords in filtered_level_keywords.items()
                }
                functional_groups = self._normalize_selector_levels(
                    result.get("functional_groups", []),
                    allowed_level_keywords,
                )
                self._validate_functional_groups(functional_groups, allowed_level_keywords)
                return functional_groups
            except Exception as exc:
                last_error = exc
                logger.warning("  selector 解析失败，第 %s/%s 次重试: %s", attempt, max_attempts, exc)

        raise ValueError(f"LLM 多次返回非法 selector 结果: {last_error}")

    def _has_deeper_level_candidates(self, context: dict[str, Any], min_level: int) -> bool:
        return bool(self._filter_level_keywords(context.get("allowed_level_keywords", {}), min_level))

    def _next_min_level(self, functional_groups: list[dict[str, Any]], current_min_level: int) -> int:
        max_selected_level = current_min_level
        for group in functional_groups:
            selectors = group.get("selectors", [])
            if not isinstance(selectors, list):
                continue
            for selector in selectors:
                if not isinstance(selector, dict):
                    continue
                try:
                    level = int(selector.get("level"))
                except (TypeError, ValueError):
                    continue
                max_selected_level = max(max_selected_level, level)
        return max_selected_level + 1

    def _normalize_selector_levels(
        self,
        functional_groups: Any,
        allowed_level_keywords: dict[int, set[str]] | None = None,
    ) -> Any:
        if not isinstance(functional_groups, list) or allowed_level_keywords is None:
            return functional_groups

        keyword_to_levels: dict[str, list[int]] = {}
        for level, keywords in allowed_level_keywords.items():
            for keyword in keywords:
                keyword_to_levels.setdefault(keyword, []).append(level)

        normalized_groups = []
        for group in functional_groups:
            if not isinstance(group, dict):
                normalized_groups.append(group)
                continue

            normalized_group = dict(group)
            selectors = normalized_group.get("selectors")
            if not isinstance(selectors, list):
                normalized_groups.append(normalized_group)
                continue

            normalized_selectors = []
            for selector in selectors:
                if not isinstance(selector, dict):
                    continue

                normalized_selector = dict(selector)
                keyword = str(normalized_selector.get("keyword", "")).strip()
                try:
                    level = int(normalized_selector.get("level"))
                except (TypeError, ValueError):
                    continue

                if keyword and keyword not in allowed_level_keywords.get(level, set()):
                    candidate_levels = sorted(keyword_to_levels.get(keyword, []))
                    if candidate_levels:
                        target_level = candidate_levels[0]
                        logger.info(
                            "  selector level 自动纠偏: group=%s keyword=%s %s -> %s",
                            normalized_group.get("group_name"),
                            keyword,
                            level,
                            target_level,
                        )
                        normalized_selector["level"] = target_level
                    else:
                        logger.warning(
                            "  selector 丢弃: group=%s keyword=%s level=%s 不在 Allowed Level Keywords 中，且无法自动纠偏",
                            normalized_group.get("group_name"),
                            keyword,
                            level,
                        )
                        continue

                normalized_selectors.append(normalized_selector)

            if not normalized_selectors:
                logger.warning(
                    "  功能组丢弃: group=%s 没有保留下任何合法 selector",
                    normalized_group.get("group_name"),
                )
                continue

            normalized_group["selectors"] = normalized_selectors
            normalized_groups.append(normalized_group)

        return normalized_groups

    def _validate_functional_groups(
        self,
        functional_groups: Any,
        allowed_level_keywords: dict[int, set[str]] | None = None,
    ) -> None:
        if not isinstance(functional_groups, list):
            raise ValueError("functional_groups 不是数组")

        for group in functional_groups:
            if not isinstance(group, dict):
                raise ValueError("functional_groups 中存在非对象元素")
            if not str(group.get("group_name", "")).strip():
                raise ValueError("存在缺少 group_name 的功能组")
            if not isinstance(group.get("should_continue_refine"), bool):
                raise ValueError(f"功能组 {group.get('group_name')} 的 should_continue_refine 不是布尔值")
            selectors = group.get("selectors")
            if not isinstance(selectors, list) or not selectors:
                raise ValueError(f"功能组 {group.get('group_name')} 缺少 selectors")
            for selector in selectors:
                if not isinstance(selector, dict):
                    raise ValueError(f"功能组 {group.get('group_name')} 的 selector 不是对象")
                try:
                    level = int(selector.get("level"))
                except (TypeError, ValueError):
                    raise ValueError(f"功能组 {group.get('group_name')} 的 level 非法")
                if level <= 0:
                    raise ValueError(f"功能组 {group.get('group_name')} 的 level 必须大于 0")
                keyword = str(selector.get("keyword", "")).strip()
                if not keyword:
                    raise ValueError(f"功能组 {group.get('group_name')} 的 keyword 为空")
                if allowed_level_keywords is not None and keyword not in allowed_level_keywords.get(level, set()):
                    raise ValueError(
                        f"功能组 {group.get('group_name')} 的 ({level}, {keyword}) 不在 Allowed Level Keywords 中"
                    )
                if not isinstance(selector.get("include_self"), bool):
                    raise ValueError(f"功能组 {group.get('group_name')} 的 include_self 不是布尔值")
                if not isinstance(selector.get("include_descendants"), bool):
                    raise ValueError(f"功能组 {group.get('group_name')} 的 include_descendants 不是布尔值")
                descendant_depth = selector.get("descendant_depth")
                if descendant_depth != "all":
                    try:
                        parsed_depth = int(descendant_depth)
                    except (TypeError, ValueError):
                        raise ValueError(f"功能组 {group.get('group_name')} 的 descendant_depth 非法")
                    if parsed_depth < 0:
                        raise ValueError(f"功能组 {group.get('group_name')} 的 descendant_depth 不能小于 0")

    def _selector_matches(
        self,
        selector: dict[str, Any],
        full_segments: list[str],
        parent_segments: list[str],
    ) -> bool:
        try:
            level = int(selector.get("level"))
        except (TypeError, ValueError):
            return False

        keyword = str(selector.get("keyword", "")).strip()
        if level <= 0 or not keyword:
            return False

        if len(parent_segments) < level:
            if not (selector.get("include_self") and len(full_segments) >= level):
                return False

        target_segments = parent_segments if len(parent_segments) >= level else full_segments
        if len(target_segments) < level or target_segments[level - 1] != keyword:
            return False

        prefix = target_segments[:level]
        include_self = bool(selector.get("include_self", False))
        include_descendants = bool(selector.get("include_descendants", False))
        descendant_depth = selector.get("descendant_depth", 0)

        if include_self and full_segments == prefix:
            return True

        if not include_descendants:
            return False

        if len(parent_segments) < len(prefix) or parent_segments[: len(prefix)] != prefix:
            return False

        if descendant_depth == "all":
            return True

        try:
            depth_limit = int(descendant_depth)
        except (TypeError, ValueError):
            return False

        return (len(parent_segments) - len(prefix)) <= depth_limit

    def _sanitize_group_suffix(self, group_name: str) -> str:
        sanitized = re.sub(r"[^a-zA-Z0-9/_-]+", "_", group_name.strip()).strip("_")
        return sanitized or "unnamed"

    def split_group_by_selectors(
        self,
        parent_group_name: str,
        apis: dict,
        context: dict[str, Any],
        functional_groups: list[dict[str, Any]],
    ) -> tuple[dict[str, dict], dict[str, bool], dict[str, dict]]:
        sub_groups: dict[str, dict] = {}
        continue_refine_map: dict[str, bool] = {}
        best_assignments: dict[str, tuple[int, str, Any]] = {}

        for group in functional_groups:
            raw_group_name = str(group.get("group_name", "")).strip()
            selectors = group.get("selectors", [])
            if not raw_group_name or not isinstance(selectors, list):
                continue

            final_group_name = raw_group_name
            if not final_group_name.startswith(parent_group_name):
                suffix = self._sanitize_group_suffix(raw_group_name)
                parent_tail = parent_group_name.rstrip("/").split("/")[-1]
                if suffix == parent_tail:
                    final_group_name = parent_group_name
                else:
                    final_group_name = f"{parent_group_name}/{suffix}"
            continue_refine_map[final_group_name] = bool(group.get("should_continue_refine", False))

            for selector in selectors:
                if not isinstance(selector, dict):
                    continue
                try:
                    selector_level = int(selector.get("level"))
                except (TypeError, ValueError):
                    continue
                for api_key, api_data in apis.items():
                    full_segments = context["full_map"].get(api_key, [])
                    parent_segments = context["parent_map"].get(api_key, [])
                    if self._selector_matches(selector, full_segments, parent_segments):
                        current = best_assignments.get(api_key)
                        if current is None or selector_level > current[0]:
                            best_assignments[api_key] = (selector_level, final_group_name, api_data)

        for api_key, (_, final_group_name, api_data) in best_assignments.items():
            sub_groups.setdefault(final_group_name, {})[api_key] = api_data

        remaining = {
            api_key: api_data
            for api_key, api_data in apis.items()
            if api_key not in best_assignments
        }
        return sub_groups, continue_refine_map, remaining

    def refine_groups(
        self,
        project_name: str,
        api_doc: list,
        max_depth: int | None = 2,
        current_depth: int = 0,
        group_min_levels: dict[str, int] | None = None,
    ) -> list:
        if max_depth is not None and current_depth >= max_depth:
            logger.info("  达到最大递归深度 %s，停止细分", max_depth)
            return api_doc

        refined_doc = []

        for group_item in api_doc:
            for group_name, apis in group_item.items():
                add_count = self.count_add_apis(apis)
                min_level = (group_min_levels or {}).get(group_name, 1)
                logger.info(
                    "检查功能组: %s (%s 个接口, %s 个 add, min_level=%s)",
                    group_name,
                    len(apis),
                    add_count,
                    min_level,
                )

                context = self._prepare_group_context(apis)
                logger.info(
                    "  → 去除公共前缀后节点树: prefixes=%s, complete_nodes=%s",
                    context["common_prefix"],
                    len(context["complete_nodes"]),
                )

                should_skip, skip_reason = self._should_skip_llm_refine(context)
                if should_skip:
                    logger.info("  → 当前组已接近最小粒度，跳过 LLM 细分: %s", skip_reason)
                    refined_doc.append({group_name: apis})
                    continue
                if not self._has_deeper_level_candidates(context, min_level):
                    logger.info("  → 当前组在 min_level=%s 下已无更深层可选节点，停止细分", min_level)
                    refined_doc.append({group_name: apis})
                    continue

                try:
                    functional_groups = self.llm_select_groups(project_name, group_name, apis, context, min_level=min_level)
                except Exception as exc:
                    logger.warning("  LLM 调用失败，保留原组: %s", exc)
                    refined_doc.append({group_name: apis})
                    continue

                sub_groups, continue_refine_map, remaining = self.split_group_by_selectors(
                    group_name,
                    apis,
                    context,
                    functional_groups,
                )
                if sub_groups:
                    no_progress_split = (
                        len(sub_groups) == 1
                        and not remaining
                        and len(next(iter(sub_groups.values()))) == len(apis)
                    )
                    logger.info(
                        "  → 命中 %s 个子组: %s；剩余接口=%s",
                        len(sub_groups),
                        list(sub_groups.keys()),
                        len(remaining),
                    )
                    if no_progress_split:
                        only_name, only_apis = next(iter(sub_groups.items()))
                        logger.info(
                            "  → 细分没有减少接口集合，停止继续下钻以避免重复递归: %s -> %s",
                            group_name,
                            only_name,
                        )
                        refined_doc.append({only_name: only_apis})
                        continue
                    recurse_doc = []
                    recurse_min_levels = {}
                    for name, sub_apis in sub_groups.items():
                        should_continue = continue_refine_map.get(name, False)
                        logger.info("    - %s: should_continue_refine=%s", name, should_continue)
                        if should_continue:
                            recurse_doc.append({name: sub_apis})
                            recurse_min_levels[name] = 1
                        else:
                            refined_doc.append({name: sub_apis})
                    if recurse_doc:
                        refined_doc.extend(
                            self.refine_groups(
                                project_name,
                                recurse_doc,
                                max_depth,
                                current_depth + 1,
                                recurse_min_levels,
                            )
                        )
                    if remaining:
                        logger.info(
                            "  → 剩余接口继续保留在当前父组 %s 中，进入下一轮分析（remaining=%s）",
                            group_name,
                            len(remaining),
                        )
                        refined_doc.extend(
                            self.refine_groups(
                                project_name,
                                [{group_name: remaining}],
                                max_depth,
                                current_depth + 1,
                                {group_name: min_level},
                            )
                        )
                    continue

                should_continue_same_group = any(
                    bool(group.get("should_continue_refine", False))
                    for group in functional_groups
                    if isinstance(group, dict)
                )
                if should_continue_same_group:
                    next_min_level = self._next_min_level(functional_groups, min_level)
                    if self._has_deeper_level_candidates(context, next_min_level):
                        logger.info(
                            "  → 本轮未命中有效细分，但 LLM 建议继续下钻，提升 min_level: %s -> %s",
                            min_level,
                            next_min_level,
                        )
                        refined_doc.extend(
                            self.refine_groups(
                                project_name,
                                [{group_name: apis}],
                                max_depth,
                                current_depth + 1,
                                {group_name: next_min_level},
                            )
                        )
                        continue
                    logger.info(
                        "  → LLM 建议继续下钻，但 min_level=%s 之后已无更深层可选节点，停止细分",
                        next_min_level,
                    )
                    refined_doc.append({group_name: apis})
                    continue

                logger.info("  → LLM 未返回有效细分方案，保留原组")
                refined_doc.append({group_name: apis})

        return refined_doc

    def refine_api_doc(
        self,
        project_name: str,
        api_doc: list,
        max_depth: int | None = None,
    ) -> list:
        original_count = len(api_doc)
        logger.info("原始功能组数量: %s", original_count)
        if max_depth is None:
            logger.info("未指定最大递归深度，不限制递归细分深度")
        elif max_depth <= 0:
            max_depth = None
            logger.info("使用指定最大递归深度: 不限制")
        else:
            logger.info("使用指定最大递归深度: %s", max_depth)

        before_stats = self._collect_doc_stats(api_doc)
        logger.info(
            "细分前统计: 接口总数=%s, 已标注type=%s, add接口=%s",
            before_stats["total_apis"],
            before_stats["typed_apis"],
            before_stats["add_apis"],
        )
        if before_stats["total_apis"] > 0 and before_stats["typed_apis"] == 0:
            logger.warning("细分前诊断: 当前文件中所有接口都没有type标记，但仍会尝试调用 LLM 进行树分组")
        if before_stats["zero_add_groups"]:
            logger.info(
                "细分前诊断: 无add功能组数量=%s，示例=%s",
                len(before_stats["zero_add_groups"]),
                before_stats["zero_add_groups"][:5],
            )

        refined_doc = self.refine_groups(project_name, api_doc, max_depth)
        refined_doc, merged_records = self._collapse_singleton_leaf_groups(refined_doc)
        refined_count = len(refined_doc)

        logger.info("=" * 60)
        logger.info("细分完成！")
        logger.info("原始功能组: %s", original_count)
        logger.info("细分后功能组: %s", refined_count)
        if merged_records:
            logger.info("单接口叶子组合并数量: %s", len(merged_records))
            for child_name, parent_name, api_count in merged_records:
                logger.info("  - 合并 %s -> %s (%s 个接口)", child_name, parent_name, api_count)
        logger.info("=" * 60)

        after_stats = self._collect_doc_stats(refined_doc)
        logger.info("细分后的功能组列表:")
        for group_item in refined_doc:
            for name, apis in group_item.items():
                add_count = self.count_add_apis(apis)
                logger.info("  - %s: %s 个接口, %s 个 add", name, len(apis), add_count)

        logger.info(
            "细分后统计: 接口总数=%s, add接口=%s, 无add功能组=%s",
            after_stats["total_apis"],
            after_stats["add_apis"],
            len(after_stats["zero_add_groups"]),
        )

        return refined_doc

    def run(self, project_name: str, max_depth: int | None = None, backup: bool = True):
        cache_dir = get_project_cache_dir(project_name, project_root)
        api_doc_path = os.path.join(cache_dir, "api_doc_with_type.json")

        if not os.path.exists(api_doc_path):
            logger.error("输入文件不存在: %s", api_doc_path)
            return False

        if backup:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = os.path.join(cache_dir, f"api_doc_with_type_backup_{timestamp}.json")
            shutil.copy2(api_doc_path, backup_path)
            logger.info("原文件已备份到: %s", backup_path)

        logger.info("=" * 60)
        logger.info("开始递归细分功能组")
        logger.info("项目: %s", project_name)
        logger.info("文件: %s", api_doc_path)
        logger.info("最大递归深度: %s", max_depth if max_depth is not None else "不限制")
        logger.info("=" * 60)

        api_doc = self.load_api_doc(api_doc_path)
        refined_doc = self.refine_api_doc(project_name, api_doc, max_depth)
        self.save_api_doc(api_doc_path, refined_doc)

        logger.info("结果已保存到: %s", api_doc_path)
        return True


def main():
    parser = argparse.ArgumentParser(description="API 功能组递归细分工具")
    parser.add_argument("--project", "-p", required=True, help="项目名称（如 crapi）")
    parser.add_argument("--max-depth", "-d", type=int, default=None, help="最大递归深度（默认不限制；传正整数表示限制深度）")
    parser.add_argument("--model", "-m", default="gpt-4o-mini", help="LLM 模型（默认 gpt-4o-mini）")
    parser.add_argument("--no-backup", action="store_true", help="不备份原文件")

    args = parser.parse_args()

    refiner = ApiGroupRefiner(model=args.model)
    success = refiner.run(args.project, args.max_depth, backup=not args.no_backup)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
