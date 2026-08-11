from __future__ import annotations

import asyncio
import importlib
import importlib.util
from collections.abc import Sequence
from typing import Any


class RuntimeDependencyError(RuntimeError):
    """A configured runtime adapter cannot start because a package is unavailable."""


class SentenceTransformerQueryEmbedder:
    """Query embedder compatible with BGE-indexed retrieval projections."""

    def __init__(self, *, model_name: str) -> None:
        if not model_name:
            raise ValueError("model_name must not be empty")
        if importlib.util.find_spec("sentence_transformers") is None:
            raise RuntimeDependencyError("sentence-transformers package is not installed")
        self._model_name = model_name
        self._model: Any | None = None
        self._load_lock = asyncio.Lock()

    async def embed_query(self, text: str) -> tuple[float, ...]:
        if not text.strip():
            raise ValueError("query text must not be empty")
        model = await self._model_instance()
        vector = await asyncio.to_thread(
            model.encode,
            text,
            normalize_embeddings=True,
        )
        return tuple(float(value) for value in vector)

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        if not texts:
            return ()
        if any(not text.strip() for text in texts):
            raise ValueError("embedding text must not be empty")
        model = await self._model_instance()
        vectors = await asyncio.to_thread(
            model.encode,
            texts,
            normalize_embeddings=True,
        )
        return tuple(tuple(float(value) for value in vector) for vector in vectors)

    async def _model_instance(self) -> Any:
        if self._model is not None:
            return self._model
        async with self._load_lock:
            if self._model is None:
                self._model = await asyncio.to_thread(_load_model, self._model_name)
        return self._model


def _load_model(model_name: str) -> Any:
    module = importlib.import_module("sentence_transformers")
    sentence_transformer = module.SentenceTransformer

    return sentence_transformer(model_name)
