from openai import OpenAI
from datetime import datetime
import hashlib
import inspect
import logging
import os
import time
import json
import re
import threading
import uuid
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

class GPTReply:
    _usage_lock = threading.Lock()
    _usage_records = []

    def __init__(self, model, client="deepseek"):
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

    @staticmethod
    def _env_bool(name: str, default: bool = True) -> bool:
        raw = os.getenv(name, "")
        if not raw:
            return default
        return raw.strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _safe_json_dumps(obj) -> str:
        try:
            return json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str)
        except Exception:
            return str(obj)

    @classmethod
    def _messages_digest(cls, messages) -> tuple[str, int]:
        text = cls._safe_json_dumps(messages)
        return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest(), len(text)

    @staticmethod
    def _infer_stage_from_stack() -> dict:
        stack = inspect.stack(context=0)
        for frame in stack[2:]:
            filename = frame.filename.replace("\\", "/")
            func = frame.function
            if filename.endswith("scripts/refine_api_groups.py"):
                return {"stage": "functional_group_refinement", "caller": f"{filename}:{func}"}
            if filename.endswith("api_data_tag.py"):
                return {"stage": "api_type_tagging", "caller": f"{filename}:{func}"}
            if filename.endswith("para_normalize.py"):
                return {"stage": "parameter_mapping", "caller": f"{filename}:{func}"}
            if filename.endswith("dependency_chain.py"):
                return {"stage": "dependency_chain_construction", "caller": f"{filename}:{func}"}
            if filename.endswith("case_generation_v2.py"):
                return {"stage": "case_generation", "caller": f"{filename}:{func}"}
            if filename.endswith("resource_identifier.py"):
                return {"stage": "identifier_detection", "caller": f"{filename}:{func}"}
            if filename.endswith("horizontal_vuln.py"):
                if func == "bola_vul_judgement":
                    stage = "bola_evidence_semantic_judgement"
                elif "container" in func.lower() or "resource" in func.lower():
                    stage = "container_resource_divide"
                else:
                    stage = "horizontal_bola"
                return {"stage": stage, "caller": f"{filename}:{func}"}
        frame = stack[2] if len(stack) > 2 else None
        if frame:
            return {"stage": "unknown", "caller": f"{frame.filename}:{frame.function}"}
        return {"stage": "unknown", "caller": ""}

    @classmethod
    def _record_llm_event(cls, event: dict) -> None:
        if not cls._env_bool("BOLASCAN_LLM_AUDIT", True):
            return
        event = dict(event)
        event.setdefault("event_id", str(uuid.uuid4()))
        event.setdefault("timestamp", datetime.now().isoformat(timespec="milliseconds"))
        event.setdefault("project", os.getenv("BOLASCAN_PROJECT_NAME", ""))
        event.setdefault("run_mode", os.getenv("BOLASCAN_RUN_MODE", ""))
        event.setdefault("ablation", os.getenv("BOLASCAN_ABLATION", ""))
        event.setdefault("pid", os.getpid())
        with cls._usage_lock:
            cls._usage_records.append(event)

        audit_path = os.getenv("BOLASCAN_LLM_AUDIT_PATH", "").strip()
        if not audit_path:
            return
        try:
            os.makedirs(os.path.dirname(audit_path), exist_ok=True)
            with cls._usage_lock:
                with open(audit_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        except Exception as exc:
            logger.warning("写入 LLM 审计日志失败: %s", exc)

    @classmethod
    def write_usage_log(cls, path: str) -> None:
        with cls._usage_lock:
            records = list(cls._usage_records)
        summary = {
            "total_calls": len(records),
            "success_calls": sum(1 for r in records if r.get("outcome") == "success"),
            "failed_calls": sum(1 for r in records if r.get("outcome") == "failed"),
            "recovered_calls": sum(1 for r in records if r.get("outcome") == "success" and int(r.get("attempts") or 0) > 1),
            "input_tokens": sum(int(r.get("input_tokens") or 0) for r in records),
            "output_tokens": sum(int(r.get("output_tokens") or 0) for r in records),
            "cost": sum(float(r.get("cost") or 0.0) for r in records),
            "by_stage": {},
            "records": records,
        }
        for r in records:
            stage = r.get("stage") or "unknown"
            bucket = summary["by_stage"].setdefault(
                stage,
                {
                    "calls": 0,
                    "success": 0,
                    "failed": 0,
                    "recovered": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cost": 0.0,
                },
            )
            bucket["calls"] += 1
            if r.get("outcome") == "success":
                bucket["success"] += 1
            if r.get("outcome") == "failed":
                bucket["failed"] += 1
            if r.get("outcome") == "success" and int(r.get("attempts") or 0) > 1:
                bucket["recovered"] += 1
            bucket["input_tokens"] += int(r.get("input_tokens") or 0)
            bucket["output_tokens"] += int(r.get("output_tokens") or 0)
            bucket["cost"] += float(r.get("cost") or 0.0)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)

    @staticmethod
    def _canonical_model_name(model_name: str) -> str:
        model = (model_name or "").strip().lower()
        if not model:
            return model_name

        if model.startswith("qw") and not model.startswith("qwen"):
            return "qwen-plus"
        return model_name

    def _resolve_provider_mode(self, base_url: str) -> str:
        forced_mode = (os.getenv("BOLASCAN_LLM_ENDPOINT_MODE", "") or "").strip().lower()
        if forced_mode in {"chat", "chat_completions"}:
            return "chat"
        if forced_mode in {"responses", "response"}:
            return "responses"
        if forced_mode in {"deepseek_chat", "deepseek"}:
            return "deepseek_chat"

        model_name = (self.model or "").strip().lower()
        base = (base_url or "").strip().lower()
        if "deepseek" in model_name or "deepseek" in base:
            return "deepseek_chat"
        if model_name.startswith("qwen") or model_name.startswith("qw") or "dashscope" in base:
            return "responses"
        if "x-see.cn" in base:
            return "chat"
        if model_name.startswith("gpt-") or "openai" in base:
            return "responses"
        if self.client == "openai":
            return "responses"
        return "chat"

    def _build_client(self, timeout_s: float, api_key: str, base_url: str):
        client_kwargs = {"timeout": timeout_s}
        try:
            client_kwargs["max_retries"] = int(os.getenv("BOLASCAN_OPENAI_MAX_RETRIES", "0"))
        except Exception:
            client_kwargs["max_retries"] = 0
        if api_key:
            client_kwargs["api_key"] = api_key
        if base_url:
            client_kwargs["base_url"] = base_url
        if "api_key" not in client_kwargs:
            raise ValueError(
                "缺少 API Key。请设置 BOLASCAN_LLM_API_KEY 或 OPENAI_API_KEY。"
            )
        if self.client == "openai" and "base_url" not in client_kwargs:
            client_kwargs["base_url"] = "https://api.openai.com/v1"
        return OpenAI(**client_kwargs)

    @staticmethod
    def _to_responses_input(chat_messages):
        response_input = []
        for message in chat_messages:
            response_input.append(
                {
                    "role": message.get("role", "user"),
                    "content": [
                        {
                            "type": "input_text",
                            "text": str(message.get("content", "")),
                        }
                    ],
                }
            )
        return response_input

    @staticmethod
    def _extract_response_text(response):
        output_text = getattr(response, "output_text", None)
        if output_text:
            return output_text

        output = getattr(response, "output", None) or []
        for item in output:
            contents = getattr(item, "content", None) or []
            for content in contents:
                text = getattr(content, "text", None)
                if text:
                    return text
        raise RuntimeError("Responses API 未返回可解析文本")

    def getreply(self, messages, response_format=None, temperature=None):
        """调用聊天补全，带超时、有限重试与指数退避。"""
        started = time.perf_counter()
        stack_context = self._infer_stage_from_stack()
        messages_digest, prompt_chars = self._messages_digest(messages)
        # 环境控制参数
        try:
            max_retries = int(os.getenv("BOLASCAN_LLM_MAX_RETRIES", "5"))
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

        base_url = os.getenv("BOLASCAN_LLM_BASE_URL", "").strip() or os.getenv("OPENAI_BASE_URL", "").strip()
        api_key = os.getenv("BOLASCAN_LLM_API_KEY", "").strip() or os.getenv("OPENAI_API_KEY", "").strip()
        provider_mode = self._resolve_provider_mode(base_url)
        request_model = self._canonical_model_name(self.model)

        last_err = None
        attempts_used = 0
        for attempt in range(1, max_retries + 1):
            attempts_used = attempt
            try:
                client = self._build_client(timeout_s, api_key, base_url)
                if provider_mode == "responses":
                    response = client.responses.create(
                        model=request_model,
                        input=self._to_responses_input(messages),
                        temperature=0 if temperature is None else temperature,
                    )
                    logger.info(response)
                    usage = getattr(response, "usage", None)
                    input_tokens = getattr(usage, "input_tokens", 0) if usage else 0
                    output_tokens = getattr(usage, "output_tokens", 0) if usage else 0
                    text = self._extract_response_text(response)
                else:
                    completion = client.chat.completions.create(
                        model=request_model,
                        messages=messages,
                        temperature=0 if temperature is None else temperature,
                        response_format=response_format,
                    )
                    logger.info(completion)
                    usage = getattr(completion, "usage", None)
                    input_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
                    output_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
                    text = completion.choices[0].message.content

                self.total_input_tokens += int(input_tokens or 0)
                self.total_output_tokens += int(output_tokens or 0)

                cost = self._calculate_cost(int(input_tokens or 0), int(output_tokens or 0))
                self.total_cost += cost
                self._record_llm_event({
                    **stack_context,
                    "api": "chat",
                    "model": request_model,
                    "provider_mode": provider_mode,
                    "outcome": "success",
                    "attempts": attempts_used,
                    "max_retries": max_retries,
                    "temperature": 0 if temperature is None else temperature,
                    "response_format": bool(response_format),
                    "input_tokens": int(input_tokens or 0),
                    "output_tokens": int(output_tokens or 0),
                    "cost": cost,
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                    "prompt_sha256": messages_digest,
                    "prompt_chars": prompt_chars,
                    "response_chars": len(str(text or "")),
                    "parse_status": "raw_text",
                })

                return text

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
        self._record_llm_event({
            **stack_context,
            "api": "chat",
            "model": request_model,
            "provider_mode": provider_mode,
            "outcome": "failed",
            "attempts": attempts_used,
            "max_retries": max_retries,
            "temperature": 0 if temperature is None else temperature,
            "response_format": bool(response_format),
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "prompt_sha256": messages_digest,
            "prompt_chars": prompt_chars,
            "error_type": type(last_err).__name__ if last_err else "RuntimeError",
            "error": str(last_err) if last_err else "LLM调用失败且无错误信息",
        })
        raise last_err if last_err else RuntimeError("LLM调用失败且无错误信息")

    def getreply_json_schema(self, messages, schema_name, schema, temperature=None):
        """使用官方 Responses API + json_schema 获取结构化输出。"""
        started = time.perf_counter()
        stack_context = self._infer_stage_from_stack()
        messages_digest, prompt_chars = self._messages_digest(messages)
        try:
            max_retries = int(os.getenv("BOLASCAN_LLM_MAX_RETRIES", "5"))
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

        base_url = os.getenv("BOLASCAN_LLM_BASE_URL", "").strip() or os.getenv("OPENAI_BASE_URL", "").strip()
        api_key = os.getenv("BOLASCAN_LLM_API_KEY", "").strip() or os.getenv("OPENAI_API_KEY", "").strip()
        request_model = self._canonical_model_name(self.model)

        def _parse_json_text(raw_text):
            text = (raw_text or "").strip()
            if not text:
                raise RuntimeError("模型未返回可解析JSON文本")

            # 1) 直接按 JSON 解析
            try:
                return json.loads(text)
            except Exception:
                pass

            # 2) 解析 ```json ... ``` 代码块
            fenced = re.findall(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
            for chunk in fenced:
                candidate = chunk.strip()
                if not candidate:
                    continue
                try:
                    return json.loads(candidate)
                except Exception:
                    continue

            # 3) 回退：提取首尾大括号区间
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                candidate = text[start : end + 1]
                try:
                    return json.loads(candidate)
                except Exception:
                    pass

            raise RuntimeError("无法从模型输出中解析 JSON")

        def _chat_completions_schema_fallback(client):
            fallback_messages = [
                {
                    "role": "system",
                    "content": (
                        "Return ONLY valid JSON that strictly matches the provided schema. "
                        "Do not include markdown, code fences, or explanations."
                    ),
                },
                *messages,
            ]
            completion = client.chat.completions.create(
                model=request_model,
                messages=fallback_messages,
                temperature=0 if temperature is None else temperature,
                response_format={"type": "json_object"},
            )
            logger.info(completion)

            usage = getattr(completion, "usage", None)
            input_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
            output_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
            self.total_input_tokens += int(input_tokens or 0)
            self.total_output_tokens += int(output_tokens or 0)
            cost = self._calculate_cost(int(input_tokens or 0), int(output_tokens or 0))
            self.total_cost += cost

            raw_text = completion.choices[0].message.content
            parsed = _parse_json_text(raw_text)
            return parsed, raw_text, int(input_tokens or 0), int(output_tokens or 0), cost

        last_err = None
        attempts_used = 0
        provider_mode = self._resolve_provider_mode(base_url)
        # Any non-responses mode should use chat.completions directly.
        force_chat_fallback = provider_mode in {"deepseek_chat", "chat"}
        for attempt in range(1, max_retries + 1):
            attempts_used = attempt
            try:
                client = self._build_client(timeout_s, api_key, base_url)
                if force_chat_fallback:
                    parsed, raw_text, input_tokens, output_tokens, cost = _chat_completions_schema_fallback(client)
                    self._record_llm_event({
                        **stack_context,
                        "api": "json_schema",
                        "model": request_model,
                        "provider_mode": "chat_fallback",
                        "schema_name": schema_name,
                        "outcome": "success",
                        "attempts": attempts_used,
                        "max_retries": max_retries,
                        "temperature": 0 if temperature is None else temperature,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "cost": cost,
                        "latency_ms": int((time.perf_counter() - started) * 1000),
                        "prompt_sha256": messages_digest,
                        "prompt_chars": prompt_chars,
                        "response_chars": len(str(raw_text or "")),
                        "parse_status": "json_loaded",
                    })
                    return parsed

                response = client.responses.create(
                    model=request_model,
                    input=self._to_responses_input(messages),
                    temperature=0 if temperature is None else temperature,
                    text={
                        "format": {
                            "type": "json_schema",
                            "name": schema_name,
                            "schema": schema,
                            "strict": True,
                        }
                    },
                )
                logger.info(response)

                usage = getattr(response, "usage", None)
                input_tokens = getattr(usage, "input_tokens", 0) if usage else 0
                output_tokens = getattr(usage, "output_tokens", 0) if usage else 0

                self.total_input_tokens += int(input_tokens or 0)
                self.total_output_tokens += int(output_tokens or 0)
                cost = self._calculate_cost(int(input_tokens or 0), int(output_tokens or 0))
                self.total_cost += cost

                text = self._extract_response_text(response)
                parsed = json.loads(text)
                self._record_llm_event({
                    **stack_context,
                    "api": "json_schema",
                    "model": request_model,
                    "provider_mode": provider_mode,
                    "schema_name": schema_name,
                    "outcome": "success",
                    "attempts": attempts_used,
                    "max_retries": max_retries,
                    "temperature": 0 if temperature is None else temperature,
                    "input_tokens": int(input_tokens or 0),
                    "output_tokens": int(output_tokens or 0),
                    "cost": cost,
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                    "prompt_sha256": messages_digest,
                    "prompt_chars": prompt_chars,
                    "response_chars": len(str(text or "")),
                    "parse_status": "json_loaded",
                })
                return parsed
            except Exception as e:
                msg = str(e)
                # 部分兼容服务不支持 /responses，自动切换到 chat.completions 再试
                if ("/responses" in msg or "404" in msg or "Not Found" in msg) and not force_chat_fallback:
                    logger.warning("[LLM Structured Fallback] Responses API 不可用，切换到 chat.completions 兼容模式")
                    force_chat_fallback = True
                    continue
                if (
                    "maximum context length is" in msg
                    or "Range of input length should" in msg
                    or "Exceeded limit on max byt" in msg
                ):
                    logger.error(f"[Token Limit Exceeded] Model input exceeded max token context length.\nError: {e}")
                    raise RuntimeError("Token limit exceeded — prompt too long.")

                last_err = e
                delay = backoff_ms * (2 ** (attempt - 1)) / 1000.0
                logger.warning(f"[LLM Structured Error] 调用失败（第{attempt}/{max_retries}次）: {e}. {delay:.2f}s后重试...")
                time.sleep(delay)

        self._record_llm_event({
            **stack_context,
            "api": "json_schema",
            "model": request_model,
            "provider_mode": provider_mode,
            "schema_name": schema_name,
            "outcome": "failed",
            "attempts": attempts_used,
            "max_retries": max_retries,
            "temperature": 0 if temperature is None else temperature,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "prompt_sha256": messages_digest,
            "prompt_chars": prompt_chars,
            "error_type": type(last_err).__name__ if last_err else "RuntimeError",
            "error": str(last_err) if last_err else "LLM结构化调用失败且无错误信息",
        })
        raise last_err if last_err else RuntimeError("LLM结构化调用失败且无错误信息")

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
