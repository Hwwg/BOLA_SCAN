import sys
import os
import json

from scripts.jsontools import JsonTools
from utils.dependency_cc.src.dependency_chain import DependencyChain


def main():
    if len(sys.argv) < 3:
        print("Usage: python scripts/dedup_dependency_chains.py <input_json> <output_json>")
        sys.exit(1)

    input_path = sys.argv[1]
    output_path = sys.argv[2]

    if not os.path.isfile(input_path):
        print(f"Input file not found: {input_path}")
        sys.exit(1)

    js = JsonTools()
    try:
        data = js.read_json(input_path)
    except Exception as e:
        print(f"Failed to read JSON: {e}")
        sys.exit(1)

    # Create a minimal DependencyChain instance; remove_duplicated_chains doesn't rely on doc_data
    dc = DependencyChain(doc_data={}, model_name="gpt-4o-mini", params_dict={}, project_name="dedup")
    deduped = dc.remove_duplicated_chains(data)

    try:
        js.write_json(output_path, deduped)
        print(f"Deduplicated file written to: {output_path}")
    except Exception as e:
        print(f"Failed to write output: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()