import sys
import os
import logging
import time

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
from src.case_generation_v2 import CaseGeneration
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
    def __init__(self, api_doc_path: str, model: str, case_file: str, url: str,auth_type:dict,project_name:str) -> None:
        
        self.model = model
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
        try:
            os.makedirs(self.horizontal_results_dir, exist_ok=True)
        except Exception:
            pass

        # 1) API类型标记与review，写入 api_doc_with_type.json
        self.logger.info("Step 1: API类型标记与review开始，api_doc_path=%s，model=%s", self.api_doc_path, self.model)
        _t1 = time.perf_counter()
        api_data_tag = ApiDataTagging(self.api_doc_path, self.model)
        api_doc_with_types = api_data_tag.complete_api_tagging_process(max_workers=5)
        self.logger.info("Step 1:api_data_tag.complete_api_tagging_process 执行结束")
        api_doc_with_types = api_data_tag.api_tag_results_review(api_doc_with_types)
        api_doc_with_types = api_data_tag.api_tags_review_v2(api_doc_with_types)
        api_doc_with_type_path = os.path.join(self.cache_dir, "api_doc_with_type.json")
        self.jsontool.write_json(api_doc_with_type_path, api_doc_with_types)
        self.logger.info("Step 1: API类型标记与review完成，结果写入：%s", api_doc_with_type_path)
        _d1 = time.perf_counter() - _t1
        self.execution_times["step1_api_tagging_secs"] = _d1
        self.logger.info("Step 1: 耗时 %.3fs", _d1)

        # 1.5) 递归细分功能组（新增步骤）
        self.logger.info("Step 1.5: 递归细分功能组开始")
        _t15 = time.perf_counter()
        try:
            refiner = ApiGroupRefiner(model=self.model)
            # 执行细分，直接覆盖 api_doc_with_type.json，不创建备份（因为刚生成）
            success = refiner.run(project_name, max_depth=2, backup=False)
            if success:
                # 重新加载细分后的数据，供后续步骤使用
                api_doc_with_types = self.jsontool.read_json(api_doc_with_type_path)
                self.logger.info("Step 1.5: 递归细分完成，功能组数量: %d", len(api_doc_with_types))
            else:
                self.logger.warning("Step 1.5: 递归细分失败，将使用原始分组")
        except Exception as e:
            self.logger.warning("Step 1.5: 递归细分出现异常，将使用原始分组: %s", e)
        _d15 = time.perf_counter() - _t15
        self.execution_times["step1_5_refine_groups_secs"] = _d15
        self.logger.info("Step 1.5: 耗时 %.3fs", _d15)

        # 2) 参数提取/归一化，写入 parameters_dict_all.json
        self.logger.info("Step 2: 参数提取/归一化开始")
        _t2 = time.perf_counter()
        para_tool = ParaNormalize(api_doc_with_types, self.model)
        # 显式调用一次提取（与原脚本顺序一致）
        try:
            _ = para_tool.parameters_extraction(include_path_params=True)
        except Exception as e:
            self.logger.warning("Step 2: 参数提取出现异常，将继续执行参数打包：%s", e)
        params_dict_all = para_tool.parameters_results_packages()
        params_dict_path = os.path.join(self.cache_dir, "parameters_dict_all.json")
        self.jsontool.write_json(params_dict_path, params_dict_all)
        self.logger.info("Step 2: 参数提取/归一化完成，结果写入：%s", params_dict_path)
        _d2 = time.perf_counter() - _t2
        self.execution_times["step2_param_normalize_secs"] = _d2
        self.logger.info("Step 2: 耗时 %.3fs", _d2)

        # 3) 依赖链构造，写入 dependency_chains_results.json
        self.logger.info("Step 3: 依赖链构造开始，使用模型=%s", self.model)
        _t3 = time.perf_counter()
        dependencychain = DependencyChain(api_doc_with_types, self.model, params_dict_all,project_name=project_name)
        dependency_chains_results = dependencychain.chains_construction_results()
        dependency_chains_path = os.path.join(self.cache_dir, "dependency_chains_results.json")
        self.jsontool.write_json(dependency_chains_path, dependency_chains_results)
        self.logger.info("Step 3: 依赖链构造完成，结果写入：%s", dependency_chains_path)
        _d3 = time.perf_counter() - _t3
        self.execution_times["step3_dependency_chain_secs"] = _d3
        self.logger.info("Step 3: 耗时 %.3fs", _d3)

        # 4) 案例生成（会在内部生成 create_request_data_packages_results.json 等）
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
        create_request_data_packages_results_path = os.path.join(self.cache_dir, "create_request_data_packages_results.json")
        self.jsontool.write_json(create_request_data_packages_results_path, create_request_data_packages_results)
        self.logger.info("Step 4: 用例生成完成，结果已生成到目录：%s（文件：create_request_data_packages_results.json）", self.cache_dir)
        _d4 = time.perf_counter() - _t4
        self.execution_times["step4_case_generation_secs"] = _d4
        self.logger.info("Step 4: 耗时 %.3fs", _d4)

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


if __name__ == "__main__":
    # 与各模块 __main__ 中的路径保持一致，便于直接运行
    # "mall","jeecg","youlai_mall","newbee_mall_plus","mall_swarm","newbee_mall","openemr","gin_vue_blog","pybbs","time_sea_chatgpt","ctfd"
    project_name_list = ["crapi"]
    for project_name in project_name_list:
        # project_name = "gin_vue_blog"
        os.system(f"openapi2postmanv2 -s /Users/tlif3./zju_research/bolascan_v3/bolascan_v4/cache/{project_name}/{project_name}_openapi.json -o /Users/tlif3./zju_research/bolascan_v3/bolascan_v4/cache/{project_name}/openapi_formated.json -p -O folderStrategy=Paths")
        api_doc_path = f"/Users/tlif3./zju_research/bolascan_v3/bolascan_v4/cache/{project_name}/openapi_formated.json"
        model = "gpt-4o-mini"
        case_file_path = f"/Users/tlif3./zju_research/bolascan_v3/bolascan_v4/automated_click/{project_name}/http-requests.json"
        url = "http://10.15.196.160:8888/"
        auth_type =  {
                    "test_account":{
                        "auth":{
                        "authorization":"Bearer eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxMjM0QHFxLmNvbSIsImlhdCI6MTc2ODU3NTMzNSwiZXhwIjoxNzY5MTgwMTM1LCJyb2xlIjoidXNlciJ9.oHeAoJkpJ-_GYyjkflf1Jn2kCgeEdGa3XXMCUvyMOM7tAwEXOBSyMpFotGe8ws5w6XLtTuwtzEnIlZ_zMlFmNbXge_68Lr-59vqLYDsytGADwCfX1Sx3Uv7vzpwjPx4Rnsri_7ovag1BS6o9aL41tk3XLObrYNSEdvIT-XGuaOmKYKqse2yiw285TeDzwv45zAxyW4ru1CN-CtdY7FisGNoFw_tT39a2ePZ5AoSEPdVehKyQ0n1ETXhkvFFnngBQqUVyyCtVTnjYRgBhRnFH8edfDN9u-3FOBQmUmLoJucnw1UlpvtLsHAbMq2JmvRCyLvM4tEik6IvANvm00K6-jw"
                        }
                    },
                    "data_account":{
                        "auth":{
                        "authorization":"Bearer eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxMjNAcXEuY29tIiwiaWF0IjoxNzY4NTc1Mjg1LCJleHAiOjE3NjkxODAwODUsInJvbGUiOiJ1c2VyIn0.FEj9PykcF0LWttYEcpezZ24JSjq_3S4m80D3Aa7QVS4uKNWnqZAHKRolcHqdyXmOtfJ_3VHkpCf5-4ES3cq0uL8Ac4gow6EGawGg8pdjRBnXgt5RHB8SOplLrS9gMkdN8MYj1yx1Vq0T4PjJsrHhHJdB-8wPJ1RwOcPWSCOz22JhMrejX78y5OTYgskZ8-jmETZIDryKHGyGaYvCAck1qKwhI3YOAklrJI-6Y3qI7DkZ3rOVUrER-6EOXp3zIIunneO3EqJGsrl3oo_xTsJUUi-h5PmpsRE7jtWVsaGrCAkgKnOWqT_TAtVpX05s01tHnyI4q9dIvsh6HsYOs1yhMw"
                        }
                    }
                }

        DependencyGeneration(api_doc_path, model, case_file_path, url, auth_type,project_name)
    # 目标结果：create_request_data_packages_results.json 已由 CaseGeneration 内部流程生成到 cache/{project_name} 目录



    

