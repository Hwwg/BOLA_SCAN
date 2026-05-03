import sys
import os
import logging
import time
from typing import Any, Dict

# 添加项目根目录与当前目录到Python路径，保证可导入 src.* 模块
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, '..', '..'))
for p in (current_dir, project_root):
    if p not in sys.path:
        sys.path.insert(0, p)

from scripts.jsontools import JsonTools
from prompt.synthesis_prompt import SyntheticPrompt
from gptreply.gpt_con import GPTReply

from src.api_data_tag import ApiDataTagging
from src.dependency_chain import DependencyChain
from src.para_normalize import ParaNormalize
from src.case_generation_v2 import CaseGeneration, _make_json_serializable
from utils.bola_vulner.horizontal.horizontal_vuln import HorizontalVuln
from scripts.refine_api_groups import ApiGroupRefiner

"""
整合执行顺序：
1) api_data_tag:    完整的API类型标记流程 + 结果review  -> 写入 api_doc_with_type.json
2) para_normalize:  参数提取/归一化 -> 写入 parameters_dict_all.json
3) dependency_chain: 依赖链构造 -> 写入 dependency_chains_results.json
4) case_generation_v2: 用点击数据与依赖链生成请求数据包等 -> 触发生成 create_request_data_packages_results.json
"""
class DependencyGeneration:
    ABLATION_MODES = {
        "ablation-no-group",
        "ablation-no-param-mapping",
        "ablation-static-api-type",
    }

    def __init__(
        self,
        api_doc_path: str,
        model: str,
        case_file: str,
        url: str,
        auth_type: dict,
        project_name: str,
        api_path_blacklist: list[str] | None = None,
        mode: str = "full",
    ) -> None:
        
        self.model = model
        self.mode = (mode or "full").strip().lower()
        self.jsontool = JsonTools()
        self.syn_prompt = SyntheticPrompt()
        self.gpt_reply = GPTReply(model)
        # 初始化logger
        self.logger = logging.getLogger(self.__class__.__name__)
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter("[%(levelname)s] %(asctime)s - %(name)s - %(message)s")
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
        self.logger.setLevel(logging.INFO)

        # 固定输出目录（与各模块中的 __main__ 保持一致）
        self.api_doc_path = api_doc_path
        self.cache_dir = os.path.dirname(self.api_doc_path)
        self.case_file_path = case_file
        self.url = url
        # self.data_account_token = data_account_token
        self.execution_times = {}
        self.execution_start = time.perf_counter()
        self.horizontal_results_dir = os.path.join(self.cache_dir, "horizontal_results")
        refine_max_depth_env = os.getenv("BOLASCAN_REFINE_MAX_DEPTH", "").strip()
        refine_max_depth: int | None = None
        if refine_max_depth_env:
            try:
                refine_max_depth = int(refine_max_depth_env)
            except ValueError:
                self.logger.warning("BOLASCAN_REFINE_MAX_DEPTH 非法，将忽略: %s", refine_max_depth_env)
        try:
            os.makedirs(self.horizontal_results_dir, exist_ok=True)
        except Exception:
            pass

        api_doc_with_type_path = os.path.join(self.cache_dir, "api_doc_with_type.json")
        params_dict_path = os.path.join(self.cache_dir, "parameters_dict_all.json")
        api_doc_with_types = None
        params_dict_all = None
        if self.mode == "dependency-chain-only":
            missing_inputs = [
                path
                for path in [api_doc_with_type_path, params_dict_path]
                if not os.path.exists(path)
            ]
            if missing_inputs:
                raise FileNotFoundError(
                    "Mode=dependency-chain-only 缺少前置文件: " + ", ".join(missing_inputs)
                )
            self.logger.info(
                "Mode=dependency-chain-only：复用现有 api_doc_with_type.json：%s",
                api_doc_with_type_path,
            )
            self.logger.info(
                "Mode=dependency-chain-only：复用现有 parameters_dict_all.json：%s",
                params_dict_path,
            )
            api_doc_with_types = self.jsontool.read_json(api_doc_with_type_path)
            params_dict_all = self.jsontool.read_json(params_dict_path)
            self.execution_times["step1_grouping_secs"] = 0.0
            self.execution_times["step1_5_refine_groups_secs"] = 0.0
            self.execution_times["step1_8_api_tagging_secs"] = 0.0
            self.execution_times["step2_param_normalize_secs"] = 0.0
        elif self.mode == "parameter-mapping-only" and os.path.exists(api_doc_with_type_path):
            self.logger.info(
                "Mode=parameter-mapping-only：检测到已存在的 api_doc_with_type.json，直接复用：%s",
                api_doc_with_type_path,
            )
            api_doc_with_types = self.jsontool.read_json(api_doc_with_type_path)
            self.execution_times["step1_grouping_secs"] = 0.0
            self.execution_times["step1_5_refine_groups_secs"] = 0.0
            self.execution_times["step1_8_api_tagging_secs"] = 0.0
        else:
            # 1) 先完成初始功能组分类，暂不做接口类型判定
            self.logger.info("Step 1: API功能组分类开始，api_doc_path=%s，model=%s", self.api_doc_path, self.model)
            _t1 = time.perf_counter()
            grouping_strategy = "none" if self.mode == "ablation-no-group" else "tree_select"
            api_data_tag = ApiDataTagging(
                self.api_doc_path,
                self.model,
                grouping_strategy=grouping_strategy,
                excludes=api_path_blacklist or [],
            )
            api_doc_grouped = api_data_tag.api_doc
            self.jsontool.write_json(api_doc_with_type_path, api_doc_grouped)
            self.logger.info("Step 1: API功能组分类完成，结果写入：%s", api_doc_with_type_path)
            _d1 = time.perf_counter() - _t1
            self.execution_times["step1_grouping_secs"] = _d1
            self.logger.info("Step 1: 耗时 %.3fs", _d1)

            # 1.5) 在类型判定前，先把功能组细分到最终形态；no-group 消融保持单组输入。
            if self.mode == "ablation-no-group":
                self.logger.info("Step 1.5: ablation-no-group 模式跳过递归细分")
                self.execution_times["step1_5_refine_groups_secs"] = 0.0
            else:
                self.logger.info("Step 1.5: 递归细分功能组开始")
                _t15 = time.perf_counter()
                try:
                    refiner = ApiGroupRefiner(model=self.model)
                    api_doc_grouped = refiner.refine_api_doc(
                        project_name,
                        api_doc_grouped,
                        max_depth=refine_max_depth,
                    )
                    self.jsontool.write_json(api_doc_with_type_path, api_doc_grouped)
                    self.logger.info("Step 1.5: 递归细分完成，功能组数量: %d", len(api_doc_grouped))
                except Exception as e:
                    self.logger.warning("Step 1.5: 递归细分出现异常，将使用原始分组: %s", e)
                _d15 = time.perf_counter() - _t15
                self.execution_times["step1_5_refine_groups_secs"] = _d15
                self.logger.info("Step 1.5: 耗时 %.3fs", _d15)

            # 1.8) 基于最终功能组，再统一判定接口类型
            self.logger.info("Step 1.8: 接口类型判定开始")
            _t18 = time.perf_counter()
            api_data_tag.api_doc = api_doc_grouped
            if self.mode == "ablation-static-api-type":
                api_doc_with_types = api_data_tag.complete_api_tagging_by_static_rules()
            else:
                try:
                    max_workers = int(os.getenv("BOLASCAN_LLM_MAX_WORKERS", "5"))
                except Exception:
                    max_workers = 5
                max_workers = max(1, max_workers)
                self.logger.info("Step 1.8: 使用并发线程数=%d", max_workers)
                api_doc_with_types = api_data_tag.complete_api_tagging_process(max_workers=max_workers)
            self.jsontool.write_json(api_doc_with_type_path, api_doc_with_types)
            self.logger.info("Step 1.8: 接口类型判定完成，结果写入：%s", api_doc_with_type_path)
            _d18 = time.perf_counter() - _t18
            self.execution_times["step1_8_api_tagging_secs"] = _d18
            self.logger.info("Step 1.8: 耗时 %.3fs", _d18)

        if self.mode == "api-doc-with-type-only":
            self.logger.info("Mode=api-doc-with-type-only：api_doc_with_type.json 生成完成后停止")
            self._finalize_and_maybe_write_usage()
            return

        if params_dict_all is None:
            # 2) 参数提取/归一化，写入 parameters_dict_all.json
            self.logger.info("Step 2: 参数提取/归一化开始")
            _t2 = time.perf_counter()
            para_tool = ParaNormalize(api_doc_with_types, self.model)
            # 显式调用一次提取（与原脚本顺序一致）
            try:
                _ = para_tool.parameters_extraction(include_path_params=True)
            except Exception as e:
                self.logger.warning("Step 2: 参数提取出现异常，将继续执行参数打包：%s", e)
            params_dict_all = para_tool.parameters_results_packages(
                use_llm_mapping=self.mode != "ablation-no-param-mapping"
            )
            self.jsontool.write_json(params_dict_path, params_dict_all)
            self.logger.info("Step 2: 参数提取/归一化完成，结果写入：%s", params_dict_path)
            _d2 = time.perf_counter() - _t2
            self.execution_times["step2_param_normalize_secs"] = _d2
            self.logger.info("Step 2: 耗时 %.3fs", _d2)

        if self.mode == "parameter-mapping-only":
            self.logger.info("Mode=parameter-mapping-only：parameters_dict_all.json 生成完成后停止")
            self._finalize_and_maybe_write_usage()
            return

        # 3) 依赖链构造，写入 dependency_chains_results.json
        dependency_chains_path = os.path.join(self.cache_dir, "dependency_chains_results.json")
        skip_dependency_chain = os.getenv("BOLASCAN_SKIP_DEPENDENCY_CHAIN", "").strip().lower() in {"1", "true", "yes", "on"}
        if skip_dependency_chain:
            if os.path.exists(dependency_chains_path):
                dependency_chains_results = self.jsontool.read_json(dependency_chains_path)
                self.logger.info("Step 3: 配置为跳过依赖链构造，复用现有结果：%s", dependency_chains_path)
            else:
                dependency_chains_results = {}
                self.logger.info("Step 3: 配置为跳过依赖链构造，且未找到现有 dependency_chains_results.json")
            self.execution_times["step3_dependency_chain_secs"] = 0.0
        else:
            self.logger.info("Step 3: 依赖链构造开始，使用模型=%s", self.model)
            _t3 = time.perf_counter()
            dependencychain = DependencyChain(api_doc_with_types, self.model, params_dict_all,project_name=project_name)
            dependency_chains_results = dependencychain.chains_construction_results()
            self.jsontool.write_json(dependency_chains_path, dependency_chains_results)
            self.logger.info("Step 3: 依赖链构造完成，结果写入：%s", dependency_chains_path)
            _d3 = time.perf_counter() - _t3
            self.execution_times["step3_dependency_chain_secs"] = _d3
            self.logger.info("Step 3: 耗时 %.3fs", _d3)

        # depen-gen 模式：只执行到依赖生成（Step 1~3），不进入 Step 4/5
        if self.mode in {"depen-gen", "dependency-chain-only"}:
            if self.mode == "dependency-chain-only":
                self.logger.info("Mode=dependency-chain-only：dependency_chains_results.json 生成完成后停止")
            else:
                self.logger.info("Mode=depen-gen：依赖生成完成后停止，不进入 Step 4 用例生成与 Step 5 水平BOLA分析")
            self._finalize_and_maybe_write_usage()
            return

        create_request_data_packages_results_path = os.path.join(self.cache_dir, "create_request_data_packages_results.json")
        case_packages_reusable = False
        if os.path.exists(create_request_data_packages_results_path):
            try:
                case_mtime = os.path.getmtime(create_request_data_packages_results_path)
                upstream_paths = [
                    api_doc_with_type_path,
                    params_dict_path,
                    dependency_chains_path,
                ]
                stale_upstreams = [
                    path
                    for path in upstream_paths
                    if os.path.exists(path) and os.path.getmtime(path) > case_mtime
                ]
                case_packages_reusable = not stale_upstreams
                if stale_upstreams:
                    self.logger.info(
                        "Step 4: create_request_data_packages_results.json 早于上游产物，将重新生成；stale_upstreams=%s",
                        stale_upstreams,
                    )
            except Exception as e:
                self.logger.warning(
                    "Step 4: 检查 create_request_data_packages_results.json 新旧状态失败，将重新生成：%s",
                    e,
                )
                case_packages_reusable = False

        if case_packages_reusable:
            self.logger.info(
                "Step 4: 检测到已存在的 create_request_data_packages_results.json，跳过用例生成并直接复用：%s",
                create_request_data_packages_results_path,
            )
            create_request_data_packages_results = self.jsontool.read_json(create_request_data_packages_results_path)
            self.execution_times["step4_case_generation_secs"] = 0.0
        else:
            # 4) 案例生成（会在内部生成 create_request_data_packages_results.json 等）
            if skip_dependency_chain and not dependency_chains_results:
                raise RuntimeError(
                    "已跳过依赖序列生成，但当前缓存中不存在可复用的 dependency_chains_results.json，无法继续生成用例"
                )
            self.logger.info("Step 4: 用例生成开始，case_file=%s，目标URL=%s", self.case_file_path, self.url)
            _t4 = time.perf_counter()
            case_generation = CaseGeneration(
                case_file=self.case_file_path,
                model_name=self.model,
                doc_data=api_doc_with_types,
                dependency_chain_data=dependency_chains_results,
                params_dict=params_dict_all,
                debug=False,
                project_name=project_name
            )
            create_request_data_packages_results = case_generation.case_generation_main_workflow()
            self.jsontool.write_json(
                create_request_data_packages_results_path,
                _make_json_serializable(create_request_data_packages_results),
            )
            self.logger.info("Step 4: 用例生成完成，结果已生成到目录：%s（文件：create_request_data_packages_results.json）", self.cache_dir)
            _d4 = time.perf_counter() - _t4
            self.execution_times["step4_case_generation_secs"] = _d4
            self.logger.info("Step 4: 耗时 %.3fs", _d4)

        if self.mode == "depen-gen-with-container-divide" or self.mode in self.ABLATION_MODES:
            self.logger.info("Step 4.5: 生成 container_resource_divide_results.json 开始")
            _t45 = time.perf_counter()
            try:
                horiontest = HorizontalVuln(
                    self.model,
                    params_dict_all,
                    create_request_data_packages_results,
                    project_name,
                    api_doc_with_types,
                )
                container_resource_divide_results = horiontest.generate_container_resource_divide_results()
                self.logger.info(
                    "Step 4.5: container_resource_divide_results.json 生成完成，ou_id=%d, resource_id=%d",
                    len(container_resource_divide_results.get("ou_id", [])) if isinstance(container_resource_divide_results, dict) else 0,
                    len(container_resource_divide_results.get("resource_id", [])) if isinstance(container_resource_divide_results, dict) else 0,
                )
            except Exception as e:
                self.logger.error("Step 4.5: 生成 container_resource_divide_results.json 失败：%s", e)
            _d45 = time.perf_counter() - _t45
            self.execution_times["step4_5_container_divide_secs"] = _d45
            self.logger.info("Step 4.5: 耗时 %.3fs", _d45)
            self._finalize_and_maybe_write_usage()
            return

        # 5) 水平BOLA分析（HorizontalVuln），读取前一步生成的包与参数字典
        self.logger.info("Step 5: 水平BOLA分析开始")
        _t5 = time.perf_counter()
        try:
            case_generation_results_packages = self.jsontool.read_json(os.path.join(self.cache_dir, "create_request_data_packages_results.json"))
            params_dict_all = self.jsontool.read_json(os.path.join(self.cache_dir, "parameters_dict_all.json"))
            # 确保水平分析结果目录存在
            horizontal_results_dir = self.horizontal_results_dir
            try:
                os.makedirs(horizontal_results_dir, exist_ok=True)
            except Exception as _e:
                self.logger.warning("创建水平结果目录失败，但将继续执行: %s", _e)
            horiontest = HorizontalVuln(self.model, params_dict_all, case_generation_results_packages, project_name, api_doc_with_types)
            bola_results = horiontest.horizontal_bola_workflow(self.url, auth_type)
            self.jsontool.write_json(os.path.join(self.cache_dir, "bola_horizontal_results.json"), bola_results)
            self.logger.info("Step 5: 水平BOLA分析完成，结果写入：%s", os.path.join(self.cache_dir, "bola_horizontal_results.json"))
        except Exception as e:
            self.logger.error("Step 5: 水平BOLA分析失败：%s", e)
        _d5 = time.perf_counter() - _t5
        self.execution_times["step5_horizontal_bola_secs"] = _d5
        self.logger.info("Step 5: 耗时 %.3fs", _d5)
        _total = time.perf_counter() - self.execution_start
        self.execution_times["total_secs"] = _total
        try:
            self.jsontool.write_json(os.path.join(self.horizontal_results_dir, "execution_progress.json"), self.execution_times)
        except Exception:
            pass
        try:
            llm_usage_path = os.path.join(self.horizontal_results_dir, "llm_usage.json")
            GPTReply.write_usage_log(llm_usage_path)
            self.logger.info("LLM 使用统计写入：%s", llm_usage_path)
        except Exception:
            pass

    def _finalize_and_maybe_write_usage(self) -> None:
        _total = time.perf_counter() - self.execution_start
        self.execution_times["total_secs"] = _total
        self.execution_times["mode"] = self.mode
        try:
            self.jsontool.write_json(os.path.join(self.horizontal_results_dir, "execution_progress.json"), self.execution_times)
        except Exception:
            pass
        try:
            llm_usage_path = os.path.join(self.horizontal_results_dir, "llm_usage.json")
            GPTReply.write_usage_log(llm_usage_path)
            self.logger.info("LLM 使用统计写入：%s", llm_usage_path)
        except Exception:
            pass


def run_dependency_generation(config: Dict[str, Any]) -> DependencyGeneration:
    """
    统一的 dependency_cc 启动入口，供上层 CLI / 配置驱动调用。
    """
    mode = (config.get("mode") or "full").strip().lower()
    required_fields = [
        "api_doc_path",
        "model",
        "case_file_path",
        "project_name",
    ]
    if mode == "full":
        required_fields.extend(["url", "auth_type"])
    missing = [field for field in required_fields if not config.get(field)]
    if missing:
        raise ValueError(f"dependency generation 缺少必要配置项: {', '.join(missing)}")

    return DependencyGeneration(
        api_doc_path=config["api_doc_path"],
        model=config["model"],
        case_file=config["case_file_path"],
        url=config.get("url", ""),
        auth_type=config.get("auth_type", {}),
        project_name=config["project_name"],
        api_path_blacklist=config.get("api_path_blacklist", []),
        mode=mode,
    )


if __name__ == "__main__":
    # 与各模块 __main__ 中的路径保持一致，便于直接运行
    # "mall","jeecg","youlai_mall","newbee_mall_plus","mall_swarm","newbee_mall","openemr","gin_vue_blog","pybbs","time_sea_chatgpt","ctfd"
    
    # 获取项目根目录
    project_root = os.path.abspath(os.path.join(current_dir, '..', '..'))
    
    project_name_list = ["windows_guard"]
    for project_name in project_name_list:
        # 构建基于项目根目录的路径
        openapi_src = os.path.join(project_root, 'cache', project_name, f'{project_name}_openapi.json')
        openapi_dst = os.path.join(project_root, 'cache', project_name, 'openapi_formated.json')
        
        os.system(f"openapi2postmanv2 -s {openapi_src} -o {openapi_dst} -p -O folderStrategy=Paths")
        
        api_doc_path = openapi_dst
        model = "gpt-4o-mini"
        case_file_path = os.path.join(project_root, 'automated_click', project_name, 'http-requests.json')
        
        # 配置测试参数（请根据实际情况修改）
        url = "http://your-target-app-url:port/"
        auth_type = {
            "test_account": {
                "auth": {
                    "authorization": "Bearer <your-test-account-token>"
                }
            },
            "data_account": {
                "auth": {
                    "authorization": "Bearer <your-data-account-token>"
                }
            }
        }

        run_dependency_generation({
            "api_doc_path": api_doc_path,
            "model": model,
            "case_file_path": case_file_path,
            "url": url,
            "auth_type": auth_type,
            "project_name": project_name,
        })
    # 目标结果：create_request_data_packages_results.json 已由 CaseGeneration 内部流程生成到 cache/{project_name} 目录



    
