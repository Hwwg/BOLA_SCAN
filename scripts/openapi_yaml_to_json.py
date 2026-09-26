#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将 OpenAPI YAML 文档转换为 JSON。

用法示例：
python scripts/openapi_yaml_to_json.py \
  --input /Users/tlif3./zju_research/bolascan_v3/bolascan_v4/cache/openemr/openemr-api.yaml \
  --output /Users/tlif3./zju_research/bolascan_v3/bolascan_v4/cache/openemr/openemr-api.json

如果不提供 --output，将在同目录生成同名 .json 文件。
"""

import argparse
import json
import sys
from pathlib import Path

try:
    import yaml  # PyYAML
except ImportError:
    print("缺少依赖 PyYAML。请先运行: python -m pip install pyyaml", file=sys.stderr)
    sys.exit(1)


def convert_yaml_to_json(input_path: Path, output_path: Path) -> None:
    with input_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description="OpenAPI YAML 转 JSON")
    parser.add_argument("--input", required=True, help="输入 YAML 文件路径")
    parser.add_argument("--output", default=None, help="输出 JSON 文件路径 (可选)")
    args = parser.parse_args()

    in_path = Path(args.input)
    if not in_path.exists():
        raise FileNotFoundError(f"输入文件不存在: {in_path}")

    out_path = Path(args.output) if args.output else in_path.with_suffix(".json")

    convert_yaml_to_json(in_path, out_path)
    print(f"已生成 JSON: {out_path}")


if __name__ == "__main__":
    main()