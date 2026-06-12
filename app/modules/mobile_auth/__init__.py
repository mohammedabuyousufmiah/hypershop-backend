"""Mobile auth-security module — per-device PIN / biometric quick-login.

Backs the rider/customer MobileAuthService endpoints under /api/v1/auth/*
(pin setup/verify, biometric enable/disable/unlock, devices, logout-device,
reauth/check). Social login (google/huawei) lives in iam.api.auth.
"""
