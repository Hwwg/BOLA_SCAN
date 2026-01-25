from scripts.api_doc import ApiDoc
from scripts.jsontools import JsonTools
from prompt.synthesis_prompt import SyntheticPrompt
from gptreply.gpt_con import GPTReply
from file_utils import deserialize_file_params

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

# Configure global logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))



class ExecutionPackages:
    def __init__(self, 
    case_file,
    model_name,
    doc_data,
    dependency_chain_data,
    params_dict,
    debug=False
    ):
        self.jsontools = JsonTools()
        self.case_file = self.jsontools.read_json(case_file)
        self.api_doc = doc_data
        # self.dependency_chain = dependency_chain_data
        self.params_dict = params_dict
        self.gpt_reply = GPTReply(model_name)
        self.syn_prompt = SyntheticPrompt()
        self.initial_test_info_dict = {
        }

    def wheel_exectuion_pacakges_create(self,url,data_account,request_data_packages_results):
        """
        Execute each package and fill missing data.
        - Recursively traverse the nested structure and send each request_package via requests.request(**parameters)
        - Write responses into the corresponding \"响应参数\" and \"执行状态\" fields
        """
        session = requests.Session()

        def is_request_package(obj):
            return (
                isinstance(obj, dict)
                and isinstance(obj.get("request_parameters"), dict)
                and obj["request_parameters"].get("type") == "request"
                and isinstance(obj["request_parameters"].get("parameters"), dict)
            )

        def url_header_repalced(params):
            """
            - Replace the domain of params[\"url\"] with the provided url (keep path/query/fragment)
            - Merge data_account into params[\"headers\"], overwriting existing fields if needed
            """
            try:
                # Merge headers (overwrite existing fields)
                headers = params.get("headers", {})
                if not isinstance(headers, dict):
                    headers = {}
                def _merge_headers(base, extra):
                    if not isinstance(extra, dict):
                        return base
                    # Case-insensitive overwrite: if same name exists (case-insensitive), overwrite; otherwise add
                    lower_map = {str(k).lower(): k for k in base.keys()}
                    for k, v in extra.items():
                        if not isinstance(k, str):
                            continue
                        lk = k.lower()
                        if lk in lower_map:
                            base[lower_map[lk]] = v
                        else:
                            base[k] = v
                    return base
                headers = _merge_headers(headers, data_account)
                params["headers"] = headers

                # Replace URL domain (keep original path, etc.)
                orig_url = params.get("url")
                if isinstance(orig_url, str) and url:
                    parsed_orig = urllib.parse.urlparse(orig_url)
                    parsed_new = urllib.parse.urlparse(url)

                    # Compute new scheme and netloc
                    if parsed_new.netloc:
                        new_scheme = parsed_new.scheme or (parsed_orig.scheme or "http")
                        new_netloc = parsed_new.netloc
                    else:
                        # The provided url may be a bare host (e.g., backend-api-01.newbee.ltd:8080)
                        new_scheme = parsed_new.scheme or (parsed_orig.scheme or "http")
                        candidate = parsed_new.path or parsed_new.netloc or url
                        new_netloc = candidate

                    new_url = urllib.parse.urlunparse((
                        new_scheme,
                        new_netloc,
                        parsed_orig.path,
                        parsed_orig.params,
                        parsed_orig.query,
                        parsed_orig.fragment,
                    ))
                    params["url"] = new_url

                return params
            except Exception:
                # Do not break the request on errors; return original params
                return params


        def _send_request_package(pkg):
            # Extract request parameters
            req = pkg.get("request_parameters", {})
            params = copy.deepcopy(req.get("parameters", {})) if isinstance(req.get("parameters"), dict) else {}

            # Check and deserialize file params
            files_val = params.get("files")
            if isinstance(files_val, dict):
                # Check whether files are serialized
                if any(isinstance(v, dict) and ("type" in v or "filename" in v) for v in files_val.values()):
                    # Deserialize file params
                    try:
                        deserialized_files = deserialize_file_params(files_val)
                        params["files"] = deserialized_files
                    except Exception as e:
                        logger.warning(f"Failed to deserialize file params: {e}")
                        params.pop("files", None)

            try:
                # params[""]
                params = url_header_repalced(params)
                response = session.request(**params)
                # Try JSON parsing; fall back to text
                try:
                    body = response.json()
                except Exception:
                    body = response.text

                resp_headers = dict(response.headers)

                # Update response params
                pkg.setdefault("response_params", {"type": "response", "parameters": {}})
                pkg["response_params"]["parameters"] = {
                    "status_code": response.status_code,
                    "url": response.url,
                    "headers": resp_headers,
                    "body": body,
                }
                # Update execution status
                pkg.setdefault("execution_state", {})
                pkg["execution_state"]["status"] = "executed"
                pkg["execution_state"]["status_code"] = response.status_code
                pkg["execution_state"]["response_url"] = response.url
            except Exception as e:
                pkg.setdefault("response_params", {"type": "response", "parameters": {}})
                pkg["response_params"]["parameters"] = {
                    "error": str(e),
                }
                pkg.setdefault("execution_state", {})
                pkg["execution_state"]["status"] = "error"
                pkg["execution_state"]["status_code"] = 0

            return pkg

        def sort_key(k):
            return int(k) if isinstance(k, str) and k.isdigit() else k

        def requests_creation(node):
            # List: recurse item by item
            if isinstance(node, list):
                return [requests_creation(item) for item in node]
            # Dict: step dict or api_key -> request_package mapping
            if isinstance(node, dict):
                # If this is a single request package, send directly
                if is_request_package(node):
                    return _send_request_package(node)
                # Otherwise process each item
                out = {}
                for k in sorted(node.keys(), key=sort_key):
                    v = node[k]
                    if is_request_package(v):
                        out[k] = _send_request_package(v)
                    else:
                        out[k] = requests_creation(v)
                return out
            # Other types, return as-is
            return node

        # Execute recursive request sending
        result = requests_creation(request_data_packages_results)
        return result
    
