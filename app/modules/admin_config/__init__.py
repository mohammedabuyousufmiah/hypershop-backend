"""Dashboard config API.

Reads the declarative module registry (`app/core/registry/admin_modules`)
and returns the per-caller view: nav entries the caller can see + their
role/permission summary. Replaces the hardcoded admin layout nav.
"""
