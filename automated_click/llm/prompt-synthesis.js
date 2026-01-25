const PROMPTS = require('../prompt/prompt-template');  // 修正为正确的路径


class PromptSynthesizer {
    constructor() {
        this.PROMPTS = PROMPTS;
    }

    // 替换提示模板中的变量
    _replaceVariables(template, variables) {
        try {
            // 检查输入参数
            if (!template) {
                throw new Error('模板内容不能为空');
            }

            if (typeof template !== 'string') {
                throw new Error('模板必须是字符串类型');
            }

            // 如果模板中没有占位符，直接返回原始模板
            if (!template.includes('{')) {
                return template;
            }

            let result = template;
            
            // 提取所有有效的占位符（形如 {variable_name}，但不在JSON代码块中）
            const regex = /\{([a-z0-9_]+)\}/gi;
            const matches = [];
            let match;
            
            while ((match = regex.exec(template)) !== null) {
                // 检查该占位符是否在JSON代码块内
                const beforeMatch = template.substring(0, match.index);
                const codeBlockStart = beforeMatch.lastIndexOf('```');
                const codeBlockEnd = beforeMatch.lastIndexOf('```', codeBlockStart - 1);
                
                // 如果占位符在代码块外，或者在代码块对之间（非代码块内），则添加到matches
                if (codeBlockStart === -1 || (codeBlockEnd > codeBlockStart)) {
                    matches.push(match[1]); // 仅添加变量名，不包括{}
                }
            }
            
            // 检查是否所有必需的变量都提供了
            const missingVars = [...new Set(matches)]
                .filter(key => !variables.hasOwnProperty(key));
            
            if (missingVars.length > 0) {
                console.warn(`警告: 以下变量未提供值: ${missingVars.join(', ')}`);
            }

            // 替换变量
            for (const [key, value] of Object.entries(variables)) {
                const placeholder = `{${key}}`;
                if (result.includes(placeholder)) {
                    if (value === undefined || value === null) {
                        console.warn(`警告: 变量 "${key}" 的值为 ${value}`);
                        continue;
                    }
                    result = result.replace(new RegExp(placeholder, 'g'), value);
                }
            }

            return result;
        } catch (error) {
            console.error(`替换变量时出错: ${error.message}`);
            throw error;
        }
    }

    // 合成prompt
    synthesizePrompt(taskType, variables = {}) {
        try {
            // 检查任务类型
            if (!taskType) {
                throw new Error('任务类型不能为空');
            }

            if (!PROMPTS[taskType]) {
                throw new Error(`未找到类型 "${taskType}" 的提示模板`);
            }

            const template = PROMPTS[taskType];

            // 确保模板包含必要的角色
            if (!template.system && !template.user) {
                throw new Error(`模板 "${taskType}" 必须至少包含 system 或 user 角色的提示`);
            }

            // 构建提示数组
            const prompts = [];
            
            // 添加 system 角色提示（如果存在）
            if (template.system) {
                prompts.push({
                    role: "system",
                    content: this._replaceVariables(template.system, variables)
                });
            }

            // 添加 user 角色提示（如果存在）
            if (template.user) {
                prompts.push({
                    role: "user",
                    content: this._replaceVariables(template.user, variables)
                });
            }

            return prompts;
        } catch (error) {
            console.error(`合成提示时出错: ${error.message}`);
            throw error;
        }
    }
}

module.exports = PromptSynthesizer;