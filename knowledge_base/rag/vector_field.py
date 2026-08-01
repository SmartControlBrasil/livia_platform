from __future__ import annotations

import json
from typing import Any

from django.conf import settings
from django.db import models


def configured_embedding_dimensions() -> int:
    return int(getattr(settings, "LIVIA_RAG_EMBEDDING_DIMENSION", 1536) or 1536)


class RagVectorField(models.JSONField):
    """
    Campo de embedding compatível com SQLite e PostgreSQL.

    - SQLite / testes: persiste como JSON (lista de floats).
    - PostgreSQL: coluna tipada ``vector(n)`` para uso com pgvector.
    """

    description = "RAG embedding vector (JSON on SQLite, pgvector on PostgreSQL)"

    def __init__(self, dimensions: int | None = None, **kwargs):
        self.dimensions = int(dimensions) if dimensions is not None else None
        kwargs.setdefault("default", list)
        kwargs.setdefault("blank", True)
        super().__init__(**kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        kwargs["dimensions"] = self.dimensions
        return name, path, args, kwargs

    def _resolved_dimensions(self) -> int:
        if self.dimensions and self.dimensions > 0:
            return int(self.dimensions)
        return configured_embedding_dimensions()

    def db_type(self, connection) -> str:
        if connection.vendor == "postgresql":
            return f"vector({self._resolved_dimensions()})"
        return super().db_type(connection)

    def get_prep_value(self, value: Any) -> Any:
        if value is None:
            return None
        if hasattr(value, "tolist"):
            value = value.tolist()
        if isinstance(value, tuple):
            value = list(value)
        if not isinstance(value, list):
            raise TypeError("RagVectorField value must be a list of floats.")
        return [float(item) for item in value]

    def get_db_prep_value(self, value, connection, prepared=False):
        prepared_value = self.get_prep_value(value) if not prepared else value
        if prepared_value is None:
            return None
        if connection.vendor == "postgresql":
            # Serializa como texto '[..]': o adapter nativo de Vector exige
            # register_vector na conexão; o VectorField oficial do pgvector
            # também usa Vector._to_db (texto) e evita o erro psycopg
            # "cannot adapt type 'Vector'".
            from pgvector import Vector

            return Vector._to_db(prepared_value, dim=self._resolved_dimensions())
        return super().get_db_prep_value(prepared_value, connection, prepared=True)

    def from_db_value(self, value, expression, connection):
        if value is None:
            return None
        if hasattr(value, "tolist"):
            return [float(item) for item in value.tolist()]
        if isinstance(value, (list, tuple)):
            return [float(item) for item in value]
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            if text.startswith("["):
                parsed = json.loads(text)
                return [float(item) for item in parsed]
        return value

    def validate_dimension(self, value: list[float] | None) -> None:
        expected = self._resolved_dimensions()
        if value is None:
            raise ValueError("Embedding vector is required.")
        if len(value) != expected:
            raise ValueError(f"Embedding vector dimension {len(value)} != configured {expected}.")
