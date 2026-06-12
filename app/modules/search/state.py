"""Search-document type enum.

A SearchDocument is a denormalised row that holds the searchable text
of one source entity. Today we index 3 types:
  - product   (catalog.Product)
  - brand     (catalog.Brand)
  - category  (catalog.Category)

To add a new type later (e.g. doctors, articles, locations):
  1. Add the constant here + to the DB CHECK enum (new alembic migration).
  2. Add an indexer function in service.py mapping that entity → SearchDocument.
  3. Add the entity to the cron rebuild loop in jobs.py.

Document keys are stable: ``<type>:<entity_id>``. Re-indexing the
same entity updates the same row (UPSERT on document_key).
"""

from __future__ import annotations

from enum import StrEnum


class SearchDocumentType(StrEnum):
    PRODUCT = "product"
    BRAND = "brand"
    CATEGORY = "category"


ALL_SEARCH_DOCUMENT_TYPES: frozenset[str] = frozenset({
    SearchDocumentType.PRODUCT,
    SearchDocumentType.BRAND,
    SearchDocumentType.CATEGORY,
})
