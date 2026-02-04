// LLM API客户端
// 合并自 llmreply/llm-reply.js
const { OpenAI } = require('openai');

class LLMBridge {
    constructor(apiKey) {
        this.openai = new OpenAI({
            apiKey: "",
            baseURL: 'https://open.xiaojingai.com/v1',
        });
        this.model = "gpt-4o-mini";
        this.temperature = 0.7;
    }

    setModel(model) {
        this.model = model;
        return this;
    }

    setTemperature(temp) {
        this.temperature = temp;
        return this;
    }

    // 发送请求到LLM
    async query(messages) {
        try {
            const response = await this.openai.chat.completions.create({
                model: this.model,
                messages: messages,
                temperature: this.temperature,
            });

            return response.choices[0].message.content;
        } catch (error) {
            console.error('LLM请求错误:', error);
            throw error;
        }
    }
}

module.exports = LLMBridge;
