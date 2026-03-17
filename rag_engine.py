"""
RAG Engine Stub for SocioChat.
Provides a basic stub for the RAG engine that was part of Sociovia's knowledge base.
The full RAG engine can be implemented later with Qdrant/FAISS.
"""

import logging

logger = logging.getLogger(__name__)


class RAGEngine:
    """Stub RAG engine — returns empty results."""

    def __init__(self, *args, **kwargs):
        logger.info("[RAG] Stub RAG engine initialized. Configure Qdrant for full functionality.")

    def search(self, query, *args, **kwargs):
        return []

    def add_document(self, *args, **kwargs):
        pass

    def delete_document(self, *args, **kwargs):
        pass


_engine = None


def get_rag_engine(*args, **kwargs):
    """Get the RAG engine singleton."""
    global _engine
    if _engine is None:
        _engine = RAGEngine()
    return _engine
