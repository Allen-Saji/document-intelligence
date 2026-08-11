"""Retrieval contracts and query construction."""

from document_intelligence.retrieval.expansion import AdjacentContextExpander
from document_intelligence.retrieval.opensearch import OpenSearchCandidateRetriever
from document_intelligence.retrieval.service import (
    RetrievalRequest,
    RetrievalResult,
    RetrievalService,
)

__all__ = [
    "AdjacentContextExpander",
    "OpenSearchCandidateRetriever",
    "RetrievalRequest",
    "RetrievalResult",
    "RetrievalService",
]
