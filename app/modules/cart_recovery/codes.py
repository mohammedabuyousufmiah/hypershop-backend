"""Constants for cart_recovery — milestones, channels, statuses, caps."""
from __future__ import annotations

MILESTONE_1H = "cart_1h"
MILESTONE_6H = "cart_6h"
MILESTONE_24H = "cart_24h"
WINBACK_7D = "winback_7d"
WINBACK_30D = "winback_30d"

CART_MILESTONES = (MILESTONE_1H, MILESTONE_6H, MILESTONE_24H)
WINBACK_MILESTONES = (WINBACK_7D, WINBACK_30D)

MILESTONE_MINUTES = {
    MILESTONE_1H: 60,
    MILESTONE_6H: 360,
    MILESTONE_24H: 1440,
    WINBACK_7D: 60 * 24 * 7,
    WINBACK_30D: 60 * 24 * 30,
}

CHANNEL_WHATSAPP = "whatsapp"
CHANNEL_EMAIL = "email"
CHANNEL_PUSH = "push"
CHANNEL_SMS = "sms"

STATUS_QUEUED = "queued"
STATUS_SENT = "sent"
STATUS_SUPPRESSED = "suppressed"
STATUS_FAILED = "failed"
STATUS_LOG_ONLY = "log_only"

SUPPRESS_OPTED_OUT = "opted_out"
SUPPRESS_BOUNCED = "bounced"
SUPPRESS_RECENT_PURCHASE = "recent_purchase"
SUPPRESS_FREQUENCY_CAP = "frequency_cap"

RECENT_PURCHASE_SUPPRESS_HOURS = 24
MAX_SENDS_PER_CUSTOMER_WEEK = 4
