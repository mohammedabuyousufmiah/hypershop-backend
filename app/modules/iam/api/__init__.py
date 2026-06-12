from fastapi import APIRouter

from app.modules.iam.api.auth import router as auth_router
from app.modules.iam.api.users import router as users_router
from app.modules.mobile_auth.api import router as mobile_auth_router

iam_router = APIRouter()
iam_router.include_router(auth_router)
iam_router.include_router(users_router)
# Per-device PIN / biometric quick-login (rider/customer MobileAuthService).
# Same /auth/* prefix; distinct sub-paths (pin/biometric/devices/reauth).
iam_router.include_router(mobile_auth_router)

__all__ = ["iam_router"]
