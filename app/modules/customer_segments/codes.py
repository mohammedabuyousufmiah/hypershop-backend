"""RFM thresholds + named segment codes for customer_segments module."""
from __future__ import annotations

SEGMENT_VIP = "vip"
SEGMENT_LOYAL = "loyal"
SEGMENT_AT_RISK = "at_risk"
SEGMENT_NEW = "new"
SEGMENT_DORMANT = "dormant"
SEGMENT_ONE_TIME = "one_time"
SEGMENT_CANT_LOSE = "cant_lose"

ALL_SEGMENTS = (
    SEGMENT_VIP, SEGMENT_LOYAL, SEGMENT_AT_RISK,
    SEGMENT_NEW, SEGMENT_DORMANT, SEGMENT_ONE_TIME, SEGMENT_CANT_LOSE,
)

# Quintile cutoffs (calibrated for BD marketplace dev data — recalibrate
# after first 60d of production data).
# Recency: lower days = more recent = higher score. > 365 day = 1; <= 7 day = 5.
RECENCY_QUINTILES_DAYS = [365, 90, 30, 7]
# Frequency: 1 order = 1, > 12 = 5.
FREQUENCY_QUINTILES = [1, 3, 6, 12]
# Monetary (BDT minor units = paisa): >= 50k paisa = 1, >= 1.5M paisa = 5.
MONETARY_QUINTILES_MINOR = [50000, 200000, 500000, 1500000]

LOOKBACK_DAYS = 365

# Order statuses counted as a "completed purchase" for RFM aggregation.
RFM_COUNTED_ORDER_STATUSES = (
    "payment_confirmed",
    "stock_reserved",
    "packed",
    "dispatched",
    "delivered",
)

# Rule type literals stored in hypershop_customer_segments.rule->type.
RULE_TYPE_RFM = "rfm"
RULE_TYPE_SQL = "sql"
RULE_TYPE_EVENT = "event"
