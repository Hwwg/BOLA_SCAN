from openai import OpenAI
import logging
import os
import time
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

class GPTReply:
    def __init__(self, model, client="openai"):
        self.model = model
        self.client = client
        self.total_input_tokens = 0  # 记录总输入 token 数
        self.total_output_tokens = 0  # 记录总输出 token 数
        self.total_cost = 0.0  # 记录总费用

        # 定义 GPT-4o mini 的价格（单位：美元）
        self.pricing = {
            "input": 2.50 / 1_000_000,  # 输入 token 价格
            "cached_input": 1.25 / 1_000_000,  # 缓存输入 token 价格
            "output": 10 / 1_000_000,  # 输出 token 价格
        }

    def getreply(self, messages):
        """调用聊天补全，带超时、有限重试与指数退避。"""
        # 环境控制参数
        try:
            max_retries = int(os.getenv("BOLASCAN_LLM_MAX_RETRIES", "3"))
        except Exception:
            max_retries = 3
        try:
            backoff_ms = int(os.getenv("BOLASCAN_LLM_BACKOFF_MS", "500"))
        except Exception:
            backoff_ms = 500
        try:
            timeout_s = float(os.getenv("BOLASCAN_LLM_TIMEOUT", "30"))
        except Exception:
            timeout_s = 30.0

        base_url = os.getenv("BOLASCAN_LLM_BASE_URL")
        api_key = os.getenv("BOLASCAN_LLM_API_KEY")

        last_err = None
        for attempt in range(1, max_retries + 1):
            try:
                if self.client == "openai":
                    client_kwargs = {"timeout": timeout_s}
                    # 限制 SDK 自带的HTTP层重试，避免长时间卡住；默认禁用，由环境控制
                    try:
                        client_kwargs["max_retries"] = int(os.getenv("BOLASCAN_OPENAI_MAX_RETRIES", "0"))
                    except Exception:
                        client_kwargs["max_retries"] = 0
                    if api_key:
                        client_kwargs["api_key"] = api_key
                    if base_url:
                        client_kwargs["base_url"] = base_url
                    # 确保必须配置API密钥
                    if "api_key" not in client_kwargs:
                        raise ValueError("BOLASCAN_LLM_API_KEY environment variable is required. Please set it before running.")
                    if "base_url" not in client_kwargs:
                        client_kwargs["base_url"] = "https://api.openai.com/v1"  # 使用OpenAI官方地址作为默认值
                    client = OpenAI(**client_kwargs)
                else:
                    client = OpenAI(timeout=timeout_s)

                completion = client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=0.7,
                )

                usage = getattr(completion, "usage", None)
                input_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
                output_tokens = getattr(usage, "completion_tokens", 0) if usage else 0

                self.total_input_tokens += int(input_tokens or 0)
                self.total_output_tokens += int(output_tokens or 0)

                cost = self._calculate_cost(int(input_tokens or 0), int(output_tokens or 0))
                self.total_cost += cost

                return completion.choices[0].message.content

            except Exception as e:
                # 针对超长输入直接抛出错误（无重试意义）
                msg = str(e)
                if (
                    "maximum context length is" in msg
                    or "Range of input length should" in msg
                    or "Exceeded limit on max byt" in msg
                ):
                    logger.error(f"[Token Limit Exceeded] Model input exceeded max token context length.\nError: {e}")
                    raise RuntimeError("Token limit exceeded — prompt too long.")

                last_err = e
                delay = backoff_ms * (2 ** (attempt - 1)) / 1000.0
                logger.warning(f"[LLM Error] 调用失败（第{attempt}/{max_retries}次）: {e}. {delay:.2f}s后重试...")
                time.sleep(delay)

        # 达到最大重试次数后，将最后错误抛出给上层进行兜底处理
        raise last_err if last_err else RuntimeError("LLM调用失败且无错误信息")

    def _calculate_cost(self, input_tokens, output_tokens):
        """根据输入和输出的 token 数计算费用"""
        input_cost = input_tokens * self.pricing["input"]
        output_cost = output_tokens * self.pricing["output"]
        return input_cost + output_cost

    def get_total_tokens(self):
        """返回总输入和输出的 token 数"""
        return f"{self.total_input_tokens},{self.total_output_tokens}"

    def get_total_cost(self):
        """返回总费用"""
        return self.total_cost