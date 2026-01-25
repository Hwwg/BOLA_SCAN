// LLM元素处理相关方法模块
const LLMBridge = require('./llm-client');
const PromptSynthesizer = require('./prompt-synthesis');

class LLMElementHelper {
    constructor() {
        this.llm = new LLMBridge();
        this.promptSynthesizer = new PromptSynthesizer();
    }

    /**
     * 通过LLM分析HTML，获取可交互元素的CSS选择器
     * @param {string} htmlContent
     * @returns {Promise<Array<string>>}
     */
    async getClickableSelectors(htmlContent) {
        const messages = this.promptSynthesizer.synthesizePrompt('element_generation', {
            test_object_information: htmlContent
        });
        let selectors = [];
        let retry = 0;
        const maxRetry = 2;
        while (retry <= maxRetry) {
            try {
                const reply = await this.llm.query(messages);
                const match = reply.match(/```json([\s\S]*?)```/);
                let selectorStr = match ? match[1] : reply;
                selectorStr = selectorStr.replace(/[`\n\r\s]+/g, '');
                selectors = selectorStr.split(',').filter(Boolean);
                if (selectors.length > 0 && selectors.every(sel => sel.length > 1 && !sel.includes(' '))) {
                    break;
                } else {
                    throw new Error('LLM返回格式不正确，重试...');
                }
            } catch (e) {
                retry++;
                if (retry > maxRetry) {
                    console.error('LLM元素检测失败，已重试多次:', e);
                    selectors = [];
                    break;
                }
                console.warn(`LLM返回格式异常，正在第${retry}次重试...`);
            }
        }
        return selectors;
    }

    /**
     * 通过LLM分析表单HTML，生成表单测试数据
     * @param {string} formHtml
     * @returns {Promise<Object>} 形如 {selector: value, ...}
     */
    async generateFormTestData(formHtml) {
        const messages = this.promptSynthesizer.synthesizePrompt('form_fill', {
            form_html: formHtml
        });
        let testData = {};
        let retry = 0;
        const maxRetry = 2;
        while (retry <= maxRetry) {
            try {
                const reply = await this.llm.query(messages);
                const match = reply.match(/```json([\s\S]*?)```/);
                let jsonStr = match ? match[1] : reply;
                testData = JSON.parse(jsonStr);
                if (testData && typeof testData === 'object') {
                    break;
                } else {
                    throw new Error('LLM表单数据返回格式不正确，重试...');
                }
            } catch (e) {
                retry++;
                if (retry > maxRetry) {
                    console.error('LLM表单数据生成失败，已重试多次:', e);
                    testData = {};
                    break;
                }
                console.warn(`LLM表单数据返回格式异常，正在第${retry}次重试...`);
            }
        }
        return testData;
    }

    /**
 * 根据表单HTML、上次填写数据和错误反馈，调用LLM修正表单数据
 * @param {string} formHtml
 * @param {Object} lastData
 * @param {string} errorFeedback
 * @returns {Promise<Object>} 修正后的表单数据
 */
async fixFormTestData(formHtml, lastData, errorFeedback) {
    const messages = this.promptSynthesizer.synthesizePrompt('form_fix', {
        form_html: formHtml,
        last_data: JSON.stringify(lastData, null, 2),
        error_feedback: errorFeedback
    });
    let testData = {};
    let retry = 0;
    const maxRetry = 2;
    while (retry <= maxRetry) {
        try {
            const reply = await this.llm.query(messages);
            const match = reply.match(/```json([\s\S]*?)```/);
            let jsonStr = match ? match[1] : reply;
            testData = JSON.parse(jsonStr);
            if (testData && typeof testData === 'object') {
                break;
            } else {
                throw new Error('LLM表单修正数据返回格式不正确，重试...');
            }
        } catch (e) {
            retry++;
            if (retry > maxRetry) {
                console.error('LLM表单修正数据生成失败，已重试多次:', e);
                testData = {};
                break;
            }
            console.warn(`LLM表单修正数据返回格式异常，正在第${retry}次重试...`);
        }
    }
    return testData;
}

    // 可继续扩展：如文件上传、日期控件等
}

module.exports = LLMElementHelper;
