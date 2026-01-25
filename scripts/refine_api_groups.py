#!/usr/bin/env python3
"""
API 功能组递归细分脚本

功能：
- 读取现有的 api_doc_with_type.json
- 对有 >2 个 add 接口的功能组进行 LLM 智能细分
- 子组命名从原功能组名延伸（如 identity/api/v2/user → identity/api/v2/user/video）
- 最终结果直接覆盖原文件

使用方式：
    python scripts/refine_api_groups.py --project crapi
    python scripts/refine_api_groups.py --project crapi --max-depth 2
"""

import sys
import os
import json
import argparse
import logging
import shutil
from datetime import datetime

# 添加项目路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

from scripts.jsontools import JsonTools
from gptreply.gpt_con import GPTReply
from prompt.synthesis_prompt import SyntheticPrompt

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='[%(levelname)s] %(asctime)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ApiGroupRefiner:
    """API 功能组递归细分器"""
    
    def __init__(self, model: str = "gpt-4o-mini"):
        self.gpt_reply = GPTReply(model)
        self.syn_prompt = SyntheticPrompt()
        self.jsontools = JsonTools()
    
    def load_api_doc(self, filepath: str) -> list:
        """加载 api_doc_with_type.json"""
        return self.jsontools.read_json(filepath)
    
    def save_api_doc(self, filepath: str, data: list):
        """保存细分后的结果"""
        self.jsontools.write_json(filepath, data)
    
    def count_add_apis(self, apis: dict) -> int:
        """统计功能组中 add 类型接口的数量"""
        return sum(1 for api_data in apis.values() 
                   if isinstance(api_data, dict) and api_data.get('type') == 'add')
    
    def llm_judge_split(self, group_name: str, apis: dict) -> tuple:
        """
        调用 LLM 判断功能组是否需要进一步细分
        
        Returns:
            (should_split: bool, split_plan: dict or None)
        """
        # 提取接口摘要信息
        api_summary = []
        for api_key, api_data in apis.items():
            if isinstance(api_data, dict):
                api_summary.append({
                    "endpoint": api_key,
                    "type": api_data.get("type", "unknown"),
                    "request_params": list(api_data.get("request_parameters", {}).keys())[:5],
                    "response_params": list(api_data.get("response_parameters", {}).keys())[:5]
                })
        
        add_count = self.count_add_apis(apis)
        
        # 构建 LLM prompt
        prompt_data = {
            "group_name": group_name,
            "api_summary": json.dumps(api_summary, ensure_ascii=False, indent=2),
            "add_count": add_count
        }
        
        try:
            llm_response = self.gpt_reply.getreply(
                self.syn_prompt.synthesis_prompt("api_group_refine_judge", prompt_data)
            )
            
            # 解析 LLM 返回
            result_str = self.jsontools.list_formatting(llm_response)
            result = json.loads(result_str)
            
            should_split = result.get("should_split", False)
            split_plan = result.get("split_plan")
            reason = result.get("reason", "")
            
            logger.info(f"  LLM 判断: should_split={should_split}, reason={reason}")
            
            return should_split, split_plan
        except Exception as e:
            logger.warning(f"  LLM 调用失败: {e}")
            return False, None
    
    def split_group_by_plan(self, parent_group_name: str, apis: dict, split_plan: dict) -> dict:
        """
        根据 LLM 的细分方案重新分组
        
        split_plan 格式示例：
        {
            "sub_groups": [
                {"name": "identity/api/v2/user/video", "keywords": ["video"]},
                {"name": "identity/api/v2/user/auth", "keywords": ["auth", "login", "signup"]}
            ]
        }
        """
        sub_groups = {}
        unassigned = dict(apis)  # 复制一份，用于跟踪未分配的接口
        
        if not split_plan or "sub_groups" not in split_plan:
            return {}
        
        for sub_group_info in split_plan.get("sub_groups", []):
            sub_name = sub_group_info.get("name", "")
            keywords = [kw.lower() for kw in sub_group_info.get("keywords", [])]
            
            if not sub_name or not keywords:
                continue
            
            # 确保子组名从父组名延伸
            if not sub_name.startswith(parent_group_name):
                # 如果 LLM 没有遵循命名规则，自动修正
                sub_name = f"{parent_group_name}/{sub_name.split('/')[-1]}"
            
            sub_groups[sub_name] = {}
            
            # 根据关键词匹配接口
            for api_key, api_data in list(unassigned.items()):
                api_key_lower = api_key.lower()
                
                # 检查接口路径是否包含任何关键词
                if any(kw in api_key_lower for kw in keywords):
                    sub_groups[sub_name][api_key] = api_data
                    del unassigned[api_key]
        
        # 处理未分配的接口：分配到第一个非空子组或创建 "other" 组
        if unassigned:
            # 优先分配到最相关的组
            if sub_groups:
                # 找一个合适的默认组
                first_group = next(iter(sub_groups))
                logger.info(f"  警告: {len(unassigned)} 个接口未被分配，将添加到 {first_group}")
                sub_groups[first_group].update(unassigned)
            else:
                # 如果没有子组，返回空（保留原组）
                return {}
        
        # 移除空的子组
        sub_groups = {k: v for k, v in sub_groups.items() if v}
        
        return sub_groups
    
    def refine_groups(self, api_doc: list, max_depth: int = 2, current_depth: int = 0) -> list:
        """
        递归细分功能组
        
        Args:
            api_doc: API 文档数据（列表格式）
            max_depth: 最大递归深度
            current_depth: 当前递归深度
        
        Returns:
            refined_doc: 细分后的 API 文档
        """
        if current_depth >= max_depth:
            logger.info(f"  达到最大递归深度 {max_depth}，停止细分")
            return api_doc
        
        refined_doc = []
        
        for group_item in api_doc:
            for group_name, apis in group_item.items():
                add_count = self.count_add_apis(apis)
                
                logger.info(f"检查功能组: {group_name} ({len(apis)} 个接口, {add_count} 个 add)")
                
                # 触发条件：有 >2 个 add 接口（即至少 3 个）
                if add_count > 2:
                    logger.info(f"  → 有 {add_count} 个 add 接口（>2），调用 LLM 判断是否需要细分")
                    
                    should_split, split_plan = self.llm_judge_split(group_name, apis)
                    
                    if should_split and split_plan:
                        sub_groups = self.split_group_by_plan(group_name, apis, split_plan)
                        
                        if sub_groups and len(sub_groups) > 1:
                            logger.info(f"  → 细分为 {len(sub_groups)} 个子组: {list(sub_groups.keys())}")
                            
                            # 递归检查新分组
                            sub_doc = [{name: sub_apis} for name, sub_apis in sub_groups.items()]
                            refined_sub = self.refine_groups(sub_doc, max_depth, current_depth + 1)
                            refined_doc.extend(refined_sub)
                            continue
                        else:
                            logger.info(f"  → 细分方案无效，保留原组")
                    else:
                        logger.info(f"  → LLM 判断不需要细分（完备功能组）")
                
                # 保留原组
                refined_doc.append({group_name: apis})
        
        return refined_doc
    
    def run(self, project_name: str, max_depth: int = 2, backup: bool = True):
        """
        运行递归细分
        
        Args:
            project_name: 项目名称（如 crapi）
            max_depth: 最大递归深度
            backup: 是否备份原文件
        """
        cache_dir = os.path.join(project_root, "cache", project_name)
        api_doc_path = os.path.join(cache_dir, "api_doc_with_type.json")
        
        if not os.path.exists(api_doc_path):
            logger.error(f"输入文件不存在: {api_doc_path}")
            return False
        
        # 备份原文件
        if backup:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = os.path.join(cache_dir, f"api_doc_with_type_backup_{timestamp}.json")
            shutil.copy2(api_doc_path, backup_path)
            logger.info(f"原文件已备份到: {backup_path}")
        
        logger.info("=" * 60)
        logger.info(f"开始递归细分功能组")
        logger.info(f"项目: {project_name}")
        logger.info(f"文件: {api_doc_path}")
        logger.info(f"最大递归深度: {max_depth}")
        logger.info("=" * 60)
        
        # 加载数据
        api_doc = self.load_api_doc(api_doc_path)
        original_count = len(api_doc)
        logger.info(f"原始功能组数量: {original_count}")
        
        # 执行细分
        refined_doc = self.refine_groups(api_doc, max_depth)
        refined_count = len(refined_doc)
        
        # 直接覆盖原文件
        self.save_api_doc(api_doc_path, refined_doc)
        
        logger.info("=" * 60)
        logger.info(f"细分完成！")
        logger.info(f"原始功能组: {original_count}")
        logger.info(f"细分后功能组: {refined_count}")
        logger.info(f"结果已保存到: {api_doc_path}")
        logger.info("=" * 60)
        
        # 打印细分后的功能组列表
        logger.info("细分后的功能组列表:")
        for group_item in refined_doc:
            for name, apis in group_item.items():
                add_count = self.count_add_apis(apis)
                logger.info(f"  - {name}: {len(apis)} 个接口, {add_count} 个 add")
        
        return True


def main():
    parser = argparse.ArgumentParser(description="API 功能组递归细分工具")
    parser.add_argument("--project", "-p", required=True, help="项目名称（如 crapi）")
    parser.add_argument("--max-depth", "-d", type=int, default=2, help="最大递归深度（默认 2）")
    parser.add_argument("--model", "-m", default="gpt-4o-mini", help="LLM 模型（默认 gpt-4o-mini）")
    parser.add_argument("--no-backup", action="store_true", help="不备份原文件")
    
    args = parser.parse_args()
    
    refiner = ApiGroupRefiner(model=args.model)
    success = refiner.run(args.project, args.max_depth, backup=not args.no_backup)
    
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())



