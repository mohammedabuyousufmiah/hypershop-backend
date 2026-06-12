from __future__ import annotations

STATUS_QUEUED = "queued"
STATUS_VALIDATING = "validating"
STATUS_INGESTING = "ingesting"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

LIVE_STATUSES = (STATUS_QUEUED, STATUS_VALIDATING, STATUS_INGESTING)
TERMINAL_STATUSES = (STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED)

MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024
MAX_ROWS_PER_FILE = 10000
BATCH_SIZE = 100
FAIL_RATIO_THRESHOLD = 0.5

ALLOWED_FORMATS = ("csv", "xlsx", "tsv")

REQUIRED_COLUMNS = (
    "sku", "title", "brand", "category", "price_minor", "stock_qty",
)
OPTIONAL_COLUMNS = (
    "description", "image_url", "variant_attrs_json",
    "weight_grams", "barcode", "compare_at_price_minor",
)

ERR_MISSING_COL = "missing_required_column"
ERR_INVALID_SKU = "invalid_sku"
ERR_DUPLICATE_SKU = "duplicate_sku"
ERR_MISSING_BRAND = "brand_not_found"
ERR_MISSING_CATEGORY = "category_not_found"
ERR_INVALID_PRICE = "invalid_price"
ERR_INVALID_STOCK = "invalid_stock"
ERR_IMAGE_404 = "image_url_unreachable"
ERR_OTHER = "other"

PERM_VIEW = "bulk_upload.view"
PERM_MANAGE = "bulk_upload.manage"
