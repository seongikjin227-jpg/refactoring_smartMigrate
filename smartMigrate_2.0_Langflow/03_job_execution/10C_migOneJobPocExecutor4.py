"""Experimental 10C executor for a direct private-LLM endpoint.

This component intentionally subclasses executor 3 so that it can be removed
without changing the established migration implementation.  It maps the
deployment's PRIVATE_LLM_* values to the existing OpenAI-compatible client and
optionally emits LangGraph custom-stream progress events.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from lfx.io import IntInput, SecretStrInput, StrInput


_BASE_PATH = Path(__file__).with_name("10C_migOneJobPocExecutor3.py")
_BASE_MODULE_NAME = "smart_migrate_executor3_for_executor4"
_base_spec = importlib.util.spec_from_file_location(_BASE_MODULE_NAME, _BASE_PATH)
if _base_spec is None or _base_spec.loader is None:
    raise ImportError(f"Could not load executor 3 from {_BASE_PATH}")
_base_module = importlib.util.module_from_spec(_base_spec)
sys.modules[_BASE_MODULE_NAME] = _base_module
_base_spec.loader.exec_module(_base_module)
Executor3 = _base_module.NewType10CMigOneJobPocExecutor3


class NewType10CMigOneJobPocExecutor4(Executor3):
    """Experimental executor using PRIVATE_LLM_* without altering executor 3."""

    display_name = "10C MIG One Job Executor 4 (Private LLM Experiment)"
    description = "Experimental direct private-LLM endpoint and optional custom stream progress."
    name = "NewType10CMigOneJobPocExecutor4"

    inputs = [
        *[
            input_item
            for input_item in Executor3.inputs
            if getattr(input_item, "name", "")
            not in {
                "llm_base_url",
                "llm_api_key",
                "llm_provider",
                "llm_model",
                "llm_fallback_models",
                "llm_max_tokens",
                "llm_timeout_seconds",
            }
        ],
        StrInput(
            name="private_llm_endpoint",
            display_name="Private LLM Endpoint",
            required=False,
            info="OpenAI-compatible base URL.",
        ),
        SecretStrInput(
            name="private_llm_api_key",
            display_name="Private LLM API Key",
            required=False,
            info="Private endpoint API key.",
        ),
        StrInput(
            name="private_llm_model_name",
            display_name="Private LLM Model Name",
            required=False,
            info="Private endpoint model name.",
        ),
        StrInput(
            name="private_llm_fallback_models",
            display_name="Private LLM Fallback Models",
            required=False,
            info="Optional comma-separated fallback list. Empty means only the private primary model.",
        ),
        IntInput(
            name="private_llm_max_tokens",
            display_name="Private LLM Max Tokens",
            value=0,
            required=False,
        ),
        IntInput(
            name="private_llm_timeout_seconds",
            display_name="Private LLM Timeout Seconds",
            value=0,
            required=False,
        ),
        StrInput(
            name="private_llm_stream",
            display_name="Private LLM Token Stream",
            value="",
            required=False,
            info="Y enables OpenAI-compatible SSE token parsing and custom progress events.",
        ),
    ]

    def _llm_config(self, job: dict[str, Any]) -> dict[str, Any]:
        """Resolve only private-LLM settings; never fall back to llm_* values."""
        item_config = dict(job.get("llm_config") or {})

        endpoint = self._private_text("private_llm_endpoint", item_config, "PRIVATE_LLM_ENDPOINT")
        api_key = self._private_secret("private_llm_api_key", item_config, "PRIVATE_LLM_API_KEY")
        model = self._private_text("private_llm_model_name", item_config, "PRIVATE_LLM_MODEL_NAME")
        fallback_models = self._private_text("private_llm_fallback_models", item_config, "PRIVATE_LLM_FALLBACK_MODELS")
        max_tokens = self._private_positive_int("private_llm_max_tokens", item_config, "PRIVATE_LLM_MAX_TOKENS", 4096)
        timeout = self._private_positive_int("private_llm_timeout_seconds", item_config, "PRIVATE_LLM_TIMEOUT_SECONDS", 180)
        stream = self._private_text("private_llm_stream", item_config, "PRIVATE_LLM_STREAM").upper() in {"Y", "YES", "TRUE", "1"}
        if not all((endpoint, api_key, model)):
            raise ValueError(
                "Executor 4 requires private_llm_endpoint, private_llm_api_key, and private_llm_model_name "
                "or PRIVATE_LLM_ENDPOINT, PRIVATE_LLM_API_KEY, and PRIVATE_LLM_MODEL_NAME."
            )
        return {
            "llm_base_url": endpoint,
            "llm_api_key": api_key,
            # Private endpoint is intentionally tested as OpenAI-compatible.
            "llm_provider": "openai",
            "llm_model": model,
            "llm_fallback_models": fallback_models or model,
            "llm_max_tokens": max_tokens,
            "llm_timeout_seconds": timeout,
            "private_llm_stream": stream,
        }

    def _node_generate_sql(self, context: dict[str, Any]) -> dict[str, Any]:
        self._emit_progress(context, "llm_request_started", "Private LLM SQL generation started")
        started = time.perf_counter()
        result = super()._node_generate_sql(context)
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        event = "llm_request_completed" if result.get("status") == "PASS" else "llm_request_failed"
        self._emit_progress(context, event, str(result.get("message") or ""), elapsed_ms=elapsed_ms)
        return result

    def _call_openai_compatible_http(
        self,
        *,
        api_key: str,
        base_url: str | None,
        model: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
        timeout_seconds: int,
    ) -> str:
        """Optionally parse OpenAI-compatible SSE and emit custom token events."""
        config = getattr(self, "_active_llm_config", None)
        # _call_llm_json in executor 3 passes config only as an argument, so
        # this fallback flag is also set by _call_llm_json below.
        stream_enabled = bool((config or {}).get("private_llm_stream"))
        if not stream_enabled or not base_url:
            return super()._call_openai_compatible_http(
                api_key=api_key,
                base_url=base_url,
                model=model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=max_tokens,
                timeout_seconds=timeout_seconds,
            )

        root = str(base_url).strip().rstrip("/")
        url = root if root.endswith("/chat/completions") else f"{root}/chat/completions"
        request = urllib.request.Request(
            url,
            data=json.dumps(
                {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0,
                    "max_tokens": max_tokens,
                    "stream": True,
                },
                ensure_ascii=False,
            ).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        pieces: list[str] = []
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                for raw_line in response:
                    line = raw_line.decode("utf-8", errors="ignore").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        payload = json.loads(data)
                        delta = str((((payload.get("choices") or [{}])[0].get("delta") or {}).get("content") or ""))
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
                    if delta:
                        pieces.append(delta)
                        self._emit_progress({}, "llm_token", delta)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise ValueError(f"Private LLM HTTP {exc.code}: {detail[:1000]}") from exc

        content = "".join(pieces).strip()
        if not content:
            raise ValueError(f"Private LLM streamed no message content. url={url} model={model}")
        return content

    def _call_llm_json(self, *, config: dict[str, Any] | None = None, **kwargs: Any) -> tuple[str, str]:
        self._active_llm_config = dict(config or {})
        try:
            return super()._call_llm_json(config=config, **kwargs)
        finally:
            self._active_llm_config = None

    def _emit_progress(self, context: dict[str, Any], event: str, message: str, *, elapsed_ms: int | None = None) -> None:
        payload: dict[str, Any] = {
            "type": "private_llm_progress",
            "event": event,
            "map_id": context.get("map_id"),
            "message": message,
        }
        if elapsed_ms is not None:
            payload["elapsed_ms"] = elapsed_ms
        logging.getLogger("smartmigrate.workflow").info("[executor4] %s", payload)
        try:
            from langgraph.config import get_stream_writer

            get_stream_writer()(payload)
        except Exception:
            # Executor 4 must also work when invoked through graph.invoke(),
            # where no custom stream consumer is attached.
            pass

    def _private_text(self, input_name: str, item_config: dict[str, Any], env_name: str) -> str:
        return str(
            getattr(self, input_name, "")
            or item_config.get(input_name)
            or os.getenv(env_name)
            or ""
        ).strip()

    def _private_secret(self, input_name: str, item_config: dict[str, Any], env_name: str) -> str:
        return self._secret_to_str(getattr(self, input_name, None)) or str(item_config.get(input_name) or os.getenv(env_name) or "").strip()

    def _private_positive_int(self, input_name: str, item_config: dict[str, Any], env_name: str, default: int) -> int:
        return self._positive_int(getattr(self, input_name, None) or item_config.get(input_name) or os.getenv(env_name), default)
