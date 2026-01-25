from prompt.prompt_search import PromptSearch
from collections import defaultdict
import string

class SyntheticPrompt:
    def __init__(self):
        self.base_prompt = PromptSearch()

    def extract_variables(self, text):
        """
        安全地提取模板字符串中的变量名，避免JSON格式大括号导致的解析错误
        """
        import re
        
        # 使用正则表达式匹配 {变量名} 格式的占位符
        # 只匹配包含字母、数字、下划线的变量名
        pattern = r'\{([a-zA-Z_][a-zA-Z0-9_]*)\}'
        matches = re.findall(pattern, text)
        
        return list(set(matches))  # 去重并返回

    def synthesis_prompt(self,task_type,prompt_item_dic):
        """
        task_type指的是任务类型
        prompt_item_dic是一个dict类型的数据，其中的键名对应task_type中的挖空数据
        :param task_type:
        :param prompt_item_dic:
        :return:
        [{"role": "system", "content": system_prompt}]
        """
        synthesis_prompt_result = []
        system_flag = 0
        tmp_prompt_template = self.base_prompt.return_prompt_list(task_type)

        # 创建 defaultdict：不存在的 key 会默认填 ""
        safe_dic = defaultdict(str, prompt_item_dic)

        for tmp_prompt_template_item in tmp_prompt_template:
            # 提取模板中的变量
            template_variables = self.extract_variables(tmp_prompt_template_item)
            
            # 只对存在于模板中且在字典中有对应值的变量进行替换
            filled_prompt = tmp_prompt_template_item
            for var in template_variables:
                if var in prompt_item_dic:
                    placeholder = "{" + var + "}"
                    filled_prompt = filled_prompt.replace(placeholder, str(prompt_item_dic[var]))

            role = "system" if system_flag == 0 else "user"
            synthesis_prompt_result.append({
                "role": role,
                "content": filled_prompt
            })
            system_flag = 1

        return synthesis_prompt_result


