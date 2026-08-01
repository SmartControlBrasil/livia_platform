from __future__ import annotations

import hashlib
import json
import math
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Sequence

from django.conf import settings


class EmbeddingProviderError(Exception):
    """Erro sanitizado do provedor de embeddings."""


class EmbeddingConfigurationError(Exception):
    """Configuração de embeddings ausente ou inválida."""


@dataclass(frozen=True)
class EmbeddingConfig:
    provider: str
    model: str
    dimension: int
    batch_size: int
    timeout_seconds: int
    max_retries: int
    retry_backoff_seconds: float
    indexing_enabled: bool
    api_key_configured: bool
    signature: str


def sanitize_embedding_error(exc: Exception, *, fallback: str = "embedding_provider_error") -> str:
    message = " ".join(str(exc or fallback).split())
    lowered = message.lower()
    for token in ("authorization", "bearer", "api-key", "api_key", "sk-", "token", "password", "secret"):
        if token in lowered:
            return fallback
    return message[:500] or fallback


def _assert_fake_embedding_provider_allowed() -> None:
    """Fail-closed: fake só em testes ou scripts locais explicitamente permitidos."""
    if getattr(settings, "RUNNING_TESTS", False):
        return
    env = str(getattr(settings, "LIVIA_ENVIRONMENT", "development") or "development").strip().lower()
    if env in {"staging", "production"}:
        raise EmbeddingConfigurationError(
            "LIVIA_RAG_EMBEDDING_PROVIDER=fake is not allowed when LIVIA_ENVIRONMENT "
            f"is '{env}'."
        )
    if bool(getattr(settings, "LIVIA_ALLOW_FAKE_EMBEDDINGS", False)):
        return
    raise EmbeddingConfigurationError(
        "LIVIA_RAG_EMBEDDING_PROVIDER=fake is only allowed during tests or when "
        "LIVIA_ALLOW_FAKE_EMBEDDINGS=True."
    )


def load_embedding_config() -> EmbeddingConfig:
    provider = str(getattr(settings, "LIVIA_RAG_EMBEDDING_PROVIDER", "openai") or "openai").strip().lower()
    model = str(getattr(settings, "LIVIA_RAG_EMBEDDING_MODEL", "text-embedding-3-small") or "").strip()
    dimension = int(getattr(settings, "LIVIA_RAG_EMBEDDING_DIMENSION", 1536) or 0)
    batch_size = int(getattr(settings, "LIVIA_RAG_EMBEDDING_BATCH_SIZE", 32) or 0)
    timeout_seconds = int(getattr(settings, "LIVIA_RAG_EMBEDDING_TIMEOUT_SECONDS", 30) or 0)
    max_retries = int(getattr(settings, "LIVIA_RAG_EMBEDDING_MAX_RETRIES", 3) or 0)
    retry_backoff_seconds = float(getattr(settings, "LIVIA_RAG_EMBEDDING_RETRY_BACKOFF_SECONDS", 1.0) or 0)
    indexing_enabled = bool(getattr(settings, "LIVIA_RAG_INDEXING_ENABLED", False))
    api_key = str(getattr(settings, "LIVIA_RAG_EMBEDDING_API_KEY", "") or "").strip()

    if provider not in {"openai", "fake"}:
        raise EmbeddingConfigurationError("LIVIA_RAG_EMBEDDING_PROVIDER must be 'openai' or 'fake'.")
    if provider == "fake":
        _assert_fake_embedding_provider_allowed()
    if not model:
        raise EmbeddingConfigurationError("LIVIA_RAG_EMBEDDING_MODEL is required.")
    if dimension <= 0:
        raise EmbeddingConfigurationError("LIVIA_RAG_EMBEDDING_DIMENSION must be a positive integer.")
    if batch_size <= 0:
        raise EmbeddingConfigurationError("LIVIA_RAG_EMBEDDING_BATCH_SIZE must be a positive integer.")
    if timeout_seconds <= 0:
        raise EmbeddingConfigurationError("LIVIA_RAG_EMBEDDING_TIMEOUT_SECONDS must be a positive integer.")
    if max_retries < 0:
        raise EmbeddingConfigurationError("LIVIA_RAG_EMBEDDING_MAX_RETRIES must be zero or positive.")
    if retry_backoff_seconds < 0:
        raise EmbeddingConfigurationError("LIVIA_RAG_EMBEDDING_RETRY_BACKOFF_SECONDS must be zero or positive.")

    signature_payload = {
        "provider": provider,
        "model": model,
        "dimension": dimension,
        "batch_size": batch_size,
    }
    signature = hashlib.sha256(
        json.dumps(signature_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    return EmbeddingConfig(
        provider=provider,
        model=model,
        dimension=dimension,
        batch_size=batch_size,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        retry_backoff_seconds=retry_backoff_seconds,
        indexing_enabled=indexing_enabled,
        api_key_configured=bool(api_key),
        signature=signature,
    )


def validate_embedding_vectors(
    vectors: Sequence[Sequence[float]],
    *,
    expected_count: int,
    expected_dimension: int,
) -> list[list[float]]:
    if len(vectors) != expected_count:
        raise EmbeddingProviderError(
            f"Embedding provider returned {len(vectors)} vectors, expected {expected_count}."
        )
    validated: list[list[float]] = []
    for index, vector in enumerate(vectors):
        if not isinstance(vector, (list, tuple)) or not vector:
            raise EmbeddingProviderError(f"Embedding vector at index {index} is empty or invalid.")
        if len(vector) != expected_dimension:
            raise EmbeddingProviderError(
                f"Embedding vector at index {index} has dimension {len(vector)}, expected {expected_dimension}."
            )
        floats: list[float] = []
        for value in vector:
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise EmbeddingProviderError(f"Embedding vector at index {index} has non-numeric values.") from exc
            if not math.isfinite(number):
                raise EmbeddingProviderError(f"Embedding vector at index {index} contains NaN or infinity.")
            floats.append(number)
        validated.append(floats)
    return validated


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for a, b in zip(left, right):
        dot += a * b
        left_norm += a * a
        right_norm += b * b
    if left_norm <= 0.0 or right_norm <= 0.0:
        return 0.0
    return dot / (math.sqrt(left_norm) * math.sqrt(right_norm))


class EmbeddingProvider(ABC):
    @abstractmethod
    def embed_texts(self, texts: Sequence[str], *, config: EmbeddingConfig) -> list[list[float]]:
        raise NotImplementedError


class FakeEmbeddingProvider(EmbeddingProvider):
    """Provider determinístico local para testes e desenvolvimento sem rede."""

    def embed_texts(self, texts: Sequence[str], *, config: EmbeddingConfig) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            seed = hashlib.sha256(f"{config.signature}:{text}".encode("utf-8")).digest()
            values: list[float] = []
            cursor = 0
            while len(values) < config.dimension:
                block = hashlib.sha256(seed + bytes([cursor])).digest()
                for byte in block:
                    # Map 0..255 -> (-1, 1) determinístico.
                    values.append((byte / 127.5) - 1.0)
                    if len(values) >= config.dimension:
                        break
                cursor = (cursor + 1) % 256
            # Normalize to unit length for stable cosine comparisons.
            norm = math.sqrt(sum(v * v for v in values)) or 1.0
            vectors.append([v / norm for v in values])
        return validate_embedding_vectors(
            vectors,
            expected_count=len(texts),
            expected_dimension=config.dimension,
        )


class OpenAIEmbeddingProvider(EmbeddingProvider):
    endpoint = "https://api.openai.com/v1/embeddings"

    def __init__(self):
        self.last_usage: dict = {}

    def embed_texts(self, texts: Sequence[str], *, config: EmbeddingConfig) -> list[list[float]]:
        import requests

        api_key = str(getattr(settings, "LIVIA_RAG_EMBEDDING_API_KEY", "") or "").strip()
        if not api_key:
            raise EmbeddingConfigurationError("LIVIA_RAG_EMBEDDING_API_KEY is required for OpenAI embeddings.")

        payload = {
            "model": config.model,
            "input": list(texts),
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        last_error: Exception | None = None
        attempts = config.max_retries + 1
        for attempt in range(attempts):
            try:
                response = requests.post(
                    self.endpoint,
                    json=payload,
                    headers=headers,
                    timeout=config.timeout_seconds,
                )
                if response.status_code >= 400:
                    raise EmbeddingProviderError(f"embedding_http_{response.status_code}")
                body = response.json()
                usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
                self.last_usage = usage
                data = body.get("data")
                if not isinstance(data, list):
                    raise EmbeddingProviderError("embedding_invalid_response")
                ordered = sorted(data, key=lambda item: int(item.get("index", 0)))
                vectors = [item.get("embedding") for item in ordered]
                return validate_embedding_vectors(
                    vectors,
                    expected_count=len(texts),
                    expected_dimension=config.dimension,
                )
            except EmbeddingProviderError as exc:
                last_error = exc
            except requests.Timeout as exc:
                last_error = EmbeddingProviderError("embedding_timeout")
                last_error.__cause__ = exc
            except Exception as exc:  # noqa: BLE001 - sanitize boundary
                last_error = EmbeddingProviderError(sanitize_embedding_error(exc))
                last_error.__cause__ = exc

            if attempt + 1 < attempts and config.retry_backoff_seconds > 0:
                time.sleep(config.retry_backoff_seconds * (2**attempt))

        assert last_error is not None
        raise last_error


def build_embedding_provider(config: EmbeddingConfig | None = None) -> EmbeddingProvider:
    cfg = config or load_embedding_config()
    if cfg.provider == "fake":
        return FakeEmbeddingProvider()
    if cfg.provider == "openai":
        return OpenAIEmbeddingProvider()
    raise EmbeddingConfigurationError(f"Unsupported embedding provider: {cfg.provider}")
