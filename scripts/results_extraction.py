#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
提取 bola_horizontal_results.json 中所有结论为“存在越权”的项，输出为：
"参数名": "METHOD:/route/{param}" 形式的行。

使用方式：
python scripts/results_extraction.py \
  --input /Users/tlif3./zju_research/bolascan_v3/bolascan_v4/cache/youlai_mall/bola_horizontal_results.json \
  [--output /path/to/output.json]

如果提供 --output，则写入一个字典：param -> [routes]
"""

import argparse
import json
from pathlib import Path
from typing import Dict, List, Tuple


def extract_privilege_pairs(data: Dict) -> List[Tuple[str, str]]:
    pairs: List[Tuple[str, str]] = []
    resource_id_section = data.get("resource_id", {})
    if not isinstance(resource_id_section, dict):
        return pairs

    # 遍历 resource_id 下的各资源分组
    for _group_name, params_dict in resource_id_section.items():
        if not isinstance(params_dict, dict):
            continue
        # 遍历每个参数名（例如 addressId、orderSn 等）
        for param_name, node in params_dict.items():
            if not isinstance(node, dict):
                continue
            # 可能在 cross 或 group 列表中
            for block in ("cross", "group"):
                entries = node.get(block, [])
                if not isinstance(entries, list):
                    continue
                for route_obj in entries:
                    if not isinstance(route_obj, dict):
                        continue
                    for route_key, detail in route_obj.items():
                        if isinstance(detail, dict) and detail.get("conclusion") == "Exist BOLA Vulnerability":
                            pairs.append((param_name, route_key))

    # 去重并保持顺序
    seen = set()
    result: List[Tuple[str, str]] = []
    for item in pairs:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def pairs_to_mapping(pairs: List[Tuple[str, str]]) -> Dict[str, List[str]]:
    mapping: Dict[str, List[str]] = {}
    for param, route in pairs:
        mapping.setdefault(param, []).append(route)
    return mapping


def main():
    parser = argparse.ArgumentParser(description="提取结论为存在越权的参数-路由对")
    parser.add_argument(
        "--input",
        default="",
        help="输入 JSON 文件路径",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="可选：输出 JSON 文件路径（内容为 param -> [routes] 映射）",
    )
    args = parser.parse_args()

    input_path = Path(f"/Users/tlif3./zju_research/bolascan_v3/bolascan_v4/cache/{args.input}/bola_horizontal_results.json")
    if not input_path.exists():
        raise FileNotFoundError(f"输入文件不存在: {input_path}")

    with input_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    pairs = extract_privilege_pairs(data)

    # 按用户期望的行格式打印结果
    for param, route in pairs:
        print(f'"{param}": "{route}"')

    # 如需写入映射文件
    if args.output:
        out_path = Path(args.output)
        mapping = pairs_to_mapping(pairs)
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(mapping, f, ensure_ascii=False, indent=2)
        print(f"已写入提取结果到: {out_path}")


if __name__ == "__main__":
    main()