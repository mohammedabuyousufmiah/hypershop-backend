"""Rider KYC + parcel scan compat — backs rider mobile SDK contracts.

Endpoints:
  GET  /api/v1/rider/kyc                — current rider's KYC (404 if none)
  POST /api/v1/rider/kyc                — submit / re-submit
  POST /api/v1/rider/kyc/upload         — multipart photo upload, returns {url}
  POST /api/v1/rider/scan/parcel        — bridge to deliveries scan_verify
"""
from app.modules.rider_kyc.api import router as rider_kyc_router

__all__ = ["rider_kyc_router"]
