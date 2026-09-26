import sys
import os

# 添加项目根目录到Python路径
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# 使用绝对导入
from scripts.api_doc import ApiDoc
from scripts.jsontools import JsonTools
from prompt.synthesis_prompt import SyntheticPrompt
from gptreply.gpt_con import GPTReply

# from src.api_data_tag import ApiDataTagging
# from src.dependency_chain import DependencyChain
# from src.para_normalize import ParaNormalize
# from src.case_generation import CaseGeneration
from utils.dependency_cc.main import DependencyGeneration


class WorkFlow:
    def __init__(self) -> None:
        self.dependencygeneration = DependencyGeneration(
            api_doc_path,model,case_file,url,data_accoount_token
        )

