"""Admin config API package."""
from app.modules.admin_config.api.config import (
    dashboard_router as admin_dashboard_config_router,
    registry_router as admin_module_registry_router,
    router as admin_config_router,
)
from app.modules.admin_config.api.dashboard_widgets import router as admin_dashboard_widgets_router
from app.modules.admin_config.api.settings import router as admin_modules_settings_router
from app.modules.admin_config.layouts import router as admin_dashboard_layout_router

__all__ = [
    "admin_config_router",
    "admin_dashboard_config_router",
    "admin_dashboard_widgets_router",
    "admin_dashboard_layout_router",
    "admin_module_registry_router",
    "admin_modules_settings_router",
]
