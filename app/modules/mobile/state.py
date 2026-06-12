"""Mobile module enums."""

from __future__ import annotations

from enum import StrEnum


class DeviceKind(StrEnum):
    FCM = "fcm"  # Android via Firebase
    APNS = "apns"  # iOS via Apple Push
    WEB = "web"  # web push (FCM web SDK or VAPID)
