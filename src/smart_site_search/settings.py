import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values


@dataclass(frozen=True)
class Settings:
    catalog_path: Path
    runtime_path: Path
    api_key: str | None = None
    external_llm_enabled: bool = False
    llm_model: str = "gpt-5.4-mini-2026-03-17"
    demo_mode: bool = False
    llm_input_rate: str | None = None
    llm_cached_input_rate: str | None = None
    llm_output_rate: str | None = None
    llm_pricing_version: str | None = None
    llm_provider: str = "openai"
    yandex_folder: str | None = None
    llm_timeout_seconds: float = 18.0
    llm_max_calls: int = 2

    @classmethod
    def from_env(cls) -> "Settings":
        values = {**dotenv_values(".env"), **os.environ}
        provider = values.get("SSS_LLM_PROVIDER") or "openai"
        if provider not in {"openai", "yandex"}:
            raise ValueError("SSS_LLM_PROVIDER must be openai or yandex")
        yandex = provider == "yandex"
        yandex_model = values.get("YANDEX_GPT_MODEL") or "yandexgpt-5.1"
        if yandex_model.startswith("gpt://"):
            yandex_model = yandex_model.removeprefix("gpt://").partition("/")[2]
        return cls(
            catalog_path=Path(values.get("SSS_CATALOG_PATH") or "data/catalog/catalog.sqlite"),
            runtime_path=Path(values.get("SSS_RUNTIME_PATH") or "data/runtime/state.sqlite"),
            api_key=values.get("YANDEX_API_KEY" if yandex else "OPENAI_API_KEY") or None,
            external_llm_enabled=values.get("SSS_ENABLE_EXTERNAL_LLM") == "1",
            llm_model=yandex_model if yandex else (
                values.get("SSS_LLM_MODEL") or values.get("OPENAI_MODEL") or cls.llm_model),
            demo_mode=values.get("SSS_DEMO_MODE") == "1",
            llm_input_rate=values.get("SSS_LLM_INPUT_PER_MILLION"),
            llm_cached_input_rate=values.get("SSS_LLM_CACHED_INPUT_PER_MILLION"),
            llm_output_rate=values.get("SSS_LLM_OUTPUT_PER_MILLION"),
            llm_pricing_version=values.get("SSS_LLM_PRICING_VERSION"),
            llm_provider=provider,
            yandex_folder=values.get("YANDEX_FOLDER_ID") if yandex else None,
            llm_timeout_seconds=min(18.0, max(0.1, float(values.get("LLM_TIMEOUT_SECONDS") or "18"))),
            llm_max_calls=min(2, max(1, int(values.get("LLM_MAX_CALLS_PER_REQUEST") or "2"))),
        )

    @property
    def llm_state(self) -> str:
        if not self.api_key:
            return "missing_key"
        if self.llm_provider == "yandex" and not self.yandex_folder:
            return "missing_folder"
        return "configured" if self.external_llm_enabled else "disabled"
