import json
import os
import sys


def _is_digit_str(s):
    return isinstance(s, str) and s.isdigit()


def find_best_update(data, root_key):
    """
    在给定 root_key (如 'resource_id' 或 'ou_id') 下，遍历所有 cross 结构，
    找到“具有 update 类型接口”的候选，并按两层数字键 (第一层、第二层) 的降序选择最大者。

    注意：不再限定“第二层必须也是最大数字”，而是在某个第一层数字键下，
    逐个按第二层数字从大到小查找是否存在 update 类型接口，只要存在即作为候选。

    返回一个 dict，包含定位路径与接口内容：
    {
        'group_name': str,
        'param_name': str,
        'cross_index_1': str,  # 第一层数字键（字符串）
        'cross_index_2': str,  # 第二层数字键（字符串）
        'api_key': str,
        'api_val': dict,
        'group_data': list
    }
    若未找到则返回 None。
    """
    top = data.get(root_key, {})
    candidates = []

    if not isinstance(top, dict):
        return None

    for group_name, group_val in top.items():
        if not isinstance(group_val, dict):
            continue
        for param_name, param_obj in group_val.items():
            if not isinstance(param_obj, dict):
                continue
            cross = param_obj.get('cross', [])
            if not isinstance(cross, list):
                continue

            for cross_item in cross:
                if not isinstance(cross_item, dict):
                    continue

                # 遍历第一层所有数字键（降序）
                first_level_keys = sorted(
                    (k for k in cross_item.keys() if _is_digit_str(k)),
                    key=lambda x: int(x), reverse=True
                )
                for first_key in first_level_keys:
                    level1_obj = cross_item.get(first_key)
                    if not isinstance(level1_obj, dict):
                        continue

                    # 判断是否存在第二层数字键；若无，则该层直接是 API 映射
                    second_level_digit_keys = [k for k in level1_obj.keys() if _is_digit_str(k)]
                    if second_level_digit_keys:
                        # 遍历第二层所有数字键（降序）
                        second_level_keys = sorted(
                            second_level_digit_keys,
                            key=lambda x: int(x), reverse=True
                        )
                        for second_key in second_level_keys:
                            level2_obj = level1_obj.get(second_key)
                            if not isinstance(level2_obj, dict):
                                continue

                            # 在该第二层中找第一个 update 类型接口
                            for api_key, api_val in level2_obj.items():
                                if isinstance(api_val, dict) and api_val.get('类型') == 'update':
                                    candidates.append({
                                        'group_name': group_name,
                                        'param_name': param_name,
                                        'cross_index_1': first_key,
                                        'cross_index_2': second_key,
                                        'api_key': api_key,
                                        'api_val': api_val,
                                        'group_data': param_obj.get('group', []) if isinstance(param_obj.get('group', []), list) else []
                                    })
                                    break
                    else:
                        # 无第二层数字键：直接遍历 API 映射
                        for api_key, api_val in level1_obj.items():
                            if isinstance(api_val, dict) and api_val.get('类型') == 'update':
                                candidates.append({
                                    'group_name': group_name,
                                    'param_name': param_name,
                                    'cross_index_1': first_key,
                                    'cross_index_2': '0',  # 无第二层，用 '0' 作为占位以便比较
                                    'api_key': api_key,
                                    'api_val': api_val,
                                    'group_data': param_obj.get('group', []) if isinstance(param_obj.get('group', []), list) else []
                                })
                                break

    if not candidates:
        return None

    # 选择 (第一层数字, 第二层数字) 最大的候选
    best = max(candidates, key=lambda c: (int(c['cross_index_1']), int(c['cross_index_2'])))
    return best


def build_condensed(data, selection, root_key):
    """
    根据 selection 构建浓缩版的结构，保持原有嵌套形式：
    { root_key: { group_name: { param_name: { cross: [ { first: { second: { api_key: api_val } } } ], group: [...] } } } }
    """
    if not selection:
        return {root_key: {}}

    return {
        root_key: {
            selection['group_name']: {
                selection['param_name']: {
                    'cross': [
                        {
                            selection['cross_index_1']: {
                                selection['cross_index_2']: {
                                    selection['api_key']: selection['api_val']
                                }
                            }
                        }
                    ],
                    'group': selection.get('group_data', [])
                }
            }
        }
    }


def main():
    # 输入/输出路径
    default_input = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        'cache', 'mall', 'horizontal_results', 'dependency_execution_reoutes_packages.json'
    )
    default_output = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        'cache', 'mall', 'horizontal_results', 'dependency_execution_reoutes_packages_compact.json'
    )

    input_path = sys.argv[1] if len(sys.argv) > 1 else default_input
    output_path = sys.argv[2] if len(sys.argv) > 2 else default_output

    with open(input_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    sel_resource = find_best_update(data, 'resource_id')
    sel_ou = find_best_update(data, 'ou_id')

    condensed = {}
    condensed.update(build_condensed(data, sel_resource, 'resource_id'))
    condensed.update(build_condensed(data, sel_ou, 'ou_id'))

    # 输出文件
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(condensed, f, ensure_ascii=False, indent=2)

    print(f"已生成浓缩版：{output_path}")


if __name__ == '__main__':
    main()