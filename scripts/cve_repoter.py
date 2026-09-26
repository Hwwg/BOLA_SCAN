from api_doc import ApiDoc
from jsontools import JsonTools
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

class CveRepoter:
    def __init__(self,model_name,project_name) -> None:
        self.model_name = model_name
        self.project_name = project_name
        self.gpt_reply = GPTReply(model_name)
        self.syn_prompt = SyntheticPrompt()
        self.jsontools = JsonTools()
    
    def extract_data_from_resutls(self):
        results_path = f"/Users/tlif3./zju_research/bolascan_v3/bolascan_v4/cache/{self.project_name}/bola_horizontal_results.json"
        analysis_results = self.jsontools.read_json(results_path)
        extracted = []

        def process_category_list(category_list, param_name):
            if not isinstance(category_list, list):
                return
            for item in category_list:
                if not isinstance(item, dict):
                    continue
                for route_key, iface_result in item.items():
                    if not isinstance(iface_result, dict):
                        continue
                    if iface_result.get("conclusion") == "Exist BOLA Vulnerability":
                        iface_dict = iface_result.get("接口")
                        content = iface_dict if isinstance(iface_dict, dict) else iface_result
                        extracted.append({
                            "param": param_name,
                            "route": route_key,
                            "iface": content
                        })

        # 遍历顶层两类：resource_id 与 ou_id
        for top_key in ("resource_id", "ou_id"):
            group_map = analysis_results.get(top_key, {})
            if not isinstance(group_map, dict):
                continue
            for group_name, params_map in group_map.items():
                if not isinstance(params_map, dict):
                    continue
                for param_name, categories in params_map.items():
                    if not isinstance(categories, dict):
                        continue
                    process_category_list(categories.get("cross", []), param_name)
                    process_category_list(categories.get("group", []), param_name)

        return extracted

    def generate_by_llm(self,cve_item_data):
        # cve_report
        tmp_llm_dict = {
            "vul_content" : str(cve_item_data),
            "project_name": "mall-swarm<=1.0.3"
        }
        tmp_value = self.gpt_reply.getreply(self.syn_prompt.synthesis_prompt("cve_report", tmp_llm_dict))
        return tmp_value

    def _sanitize_filename(self, name: str) -> str:
        import re
        # 仅保留常见安全字符，其余替换为下划线
        sanitized = re.sub(r"[^a-zA-Z0-9_{}\-.]+", "_", name)
        # 合并重复下划线
        sanitized = re.sub(r"_+", "_", sanitized).strip("_")
        return sanitized

    def main_workflow(self):
        output_dir = f"/Users/tlif3./zju_research/bolascan_v3/bolascan_v4/cache/{self.project_name}/cve_report"
        os.makedirs(output_dir, exist_ok=True)
        cve_list = self.extract_data_from_resutls()
        for cve_item in cve_list:
            result_content = self.generate_by_llm(cve_item)
            filename_base = f"{cve_item.get('param','unknown')}_{cve_item.get('route','unknown')}"
            filename = self._sanitize_filename(filename_base) + ".md"
            file_path = os.path.join(output_dir, filename)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(result_content)

if __name__ == "__main__":
    project_name = "mall"
    cverepoter = CveRepoter("gpt-4o-mini",project_name)
    cverepoter.main_workflow()

