// LLM API客户端
// 合并自 llmreply/llm-reply.js
const { OpenAI } = require('openai');

class LLMBridge {
    constructor(config = {}) {
        const apiKey =
            config.apiKey ||
            process.env.BOLASCAN_LLM_API_KEY ||
            process.env.OPENAI_API_KEY;
        const baseURL =
            config.baseURL ||
            process.env.BOLASCAN_LLM_BASE_URL ||
            process.env.OPENAI_BASE_URL ||
            'https://api.openai.com/v1';
        const model =
            config.model ||
            process.env.BOLASCAN_LLM_MODEL ||
            'gpt-4o-mini';

        this.openai = new OpenAI({
            apiKey,
            baseURL,
        });
        this.model = model;
        const rawTemperature =
            config.temperature ?? process.env.BOLASCAN_LLM_TEMPERATURE;
        const parsedTemperature = Number.parseFloat(rawTemperature);
        this.temperature = Number.isFinite(parsedTemperature) ? parsedTemperature : 0.7;
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
