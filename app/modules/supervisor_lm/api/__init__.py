"""Supervisor + Last-Mile Manager API package."""
from app.modules.supervisor_lm.api.router import router as supervisor_lm_router

__all__ = ["supervisor_lm_router"]
