from prompt.bola_vulner.prompt_content import *
from prompt.dependency_chain.prompt_content import *


class PromptSearch:
    def __init__(self):
        pass

    def return_prompt_list(self, task_type):
        if task_type == "api_function_type_judge":
            return [api_function_type_judge_system,api_function_type_judge_user]
        elif task_type == "parameter_normalization":
            return [parameter_normalization_system,parameter_normalization_user]
        elif task_type == "parameter_update":
            return [parameter_update_system,parameter_update_user]
        elif task_type == "parameter_generation":
            return [parameter_generation_system,parameter_generation_user]
        elif task_type == "private_data_judgement":
            return [private_data_judgement_system,private_data_judgement_user]
        elif task_type == "container_resource_judgement":
            return [container_resource_judgement_system,container_resource_judgement_user]
        elif task_type == "resource_id_judgement":
            return [resource_id_judgement_system,resource_id_judgement_user]
        elif task_type == "private_data_judgement":
            return [private_data_judgement_system,private_data_judgement_user]
        elif task_type == "parameters_fills":
            return [parameters_fills_system,parameters_fills_user]
        elif task_type == "api_matched_judgement":
            return [api_matched_judgement_system,api_matched_judgement_user]
        elif task_type == "resources_item_filter":
            return [resources_item_filter_system,resources_item_filter_user]
        elif task_type == "api_description_generation":
            return [api_description_generation_system,api_description_generation_user]
        elif task_type == "api_group_similarity_combine":
            return [api_group_similarity_combine_system,api_group_similarity_combine_user]
        elif task_type == "cve_report":
            return [cve_report_system,cve_report_user]
        elif task_type == "api_group_strategy":
            return [api_group_strategy_system,api_group_strategy_user]  
        elif task_type == "resource_id_private_data_judgement":
            return [resource_id_private_data_judgement_system,resource_id_private_data_judgement_user]
        elif task_type == "ou_id_private_data_judgement":
            return [ou_id_private_data_judgement_system,ou_id_private_data_judgement_user]
        elif task_type == "api_group_refine_judge":
            return [api_group_refine_judge_system, api_group_refine_judge_user]