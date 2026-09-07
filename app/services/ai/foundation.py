"""Foundation AI provider interface (health, models, text generation).

Document extraction remains on ExtractionProvider in base.py / provider.py.
Gemini extraction is intentionally not implemented in this phase.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AIModelInfo:
    name: str
    display_name: str | None = None
    supported_generation_methods: list[str] = field(default_factory=list)
    input_token_limit: int | None = None
    output_token_limit: int | None = None
    description: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class AIHealthResult:
    provider: str
    configured: bool
    connected: bool
    model: str | None
    message: str


@dataclass
class AITextResult:
    provider: str
    model: str
    prompt: str
    text: str
    latency_ms: int


class AIProvider(ABC):
    """Lightweight provider used for connectivity / model discovery / smoke tests."""

    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def health_check(self) -> AIHealthResult:
        raise NotImplementedError

    @abstractmethod
    def list_models(self) -> list[AIModelInfo]:
        raise NotImplementedError

    @abstractmethod
    def generate_text(self, prompt: str, *, model: str | None = None) -> AITextResult:
        raise NotImplementedError
