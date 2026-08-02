from __future__ import annotations

from django.db import connection
from django.test.utils import override_settings

RAG_TEST_DIMENSION_SQLITE = 8
RAG_TEST_DIMENSION_POSTGRESQL = 1536
RAG_PRODUCTION_EMBEDDING_DIMENSION = 1536


def rag_test_embedding_dimension() -> int:
    """Dimensão de embedding para testes que persistem vetores no backend ativo."""
    if connection.vendor == "postgresql":
        return RAG_TEST_DIMENSION_POSTGRESQL
    return RAG_TEST_DIMENSION_SQLITE


def rag_test_zero_vector(dimension: int | None = None) -> list[float]:
    """Vetor determinístico econômico para testes PostgreSQL/SQLite."""
    size = dimension if dimension is not None else rag_test_embedding_dimension()
    return [0.0] * size


class RagTestDimensionMixin:
    """Aplica LIVIA_RAG_EMBEDDING_DIMENSION conforme o backend de teste ativo."""

    @classmethod
    def setUpClass(cls):
        cls._rag_dimension_override = override_settings(
            LIVIA_RAG_EMBEDDING_DIMENSION=rag_test_embedding_dimension(),
        )
        cls._rag_dimension_override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._rag_dimension_override.disable()
