"""POD photo storage.

Same atomic-write + path-traversal-guarded pattern used by the
prescription file storage. Configurable dir; must be a shared volume in
multi-pod deployments.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import UUID

from app.core.config import get_settings
from app.core.errors import BusinessRuleError, ValidationError

_ALLOWED_MIMES: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


def allowed_pod_mime(mime: str) -> bool:
    return mime in _ALLOWED_MIMES


def pod_extension_for(mime: str) -> str:
    if mime not in _ALLOWED_MIMES:
        raise ValidationError(
            "Unsupported POD file type — accepted: jpg, png, webp.",
            details={"mime": mime},
        )
    return _ALLOWED_MIMES[mime]


def pod_root() -> Path:
    return Path(get_settings().delivery_pod_dir)


class PodStorage:
    def __init__(self) -> None:
        self.root = pod_root()
        self.max_bytes = get_settings().delivery_pod_max_file_bytes
        self.root.mkdir(parents=True, exist_ok=True)

    def relative_path_for(
        self,
        *,
        assignment_id: UUID,
        kind: str,  # "photo" | "signature"
        mime: str,
    ) -> str:
        return f"{assignment_id}-{kind}{pod_extension_for(mime)}"

    def absolute_path(self, relative: str) -> Path:
        candidate = (self.root / relative).resolve()
        try:
            candidate.relative_to(self.root.resolve())
        except ValueError as e:
            raise BusinessRuleError(
                "Resolved POD path escapes storage root.",
            ) from e
        return candidate

    def write(self, *, relative: str, content: bytes) -> tuple[int, str]:
        if not content:
            raise ValidationError("Empty POD file.")
        if len(content) > self.max_bytes:
            raise ValidationError(
                "POD file exceeds size limit.",
                details={"max_bytes": self.max_bytes, "received": len(content)},
            )
        target = self.absolute_path(relative)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_bytes(content)
        tmp.replace(target)
        return len(content), hashlib.sha256(content).hexdigest()

    def read(self, relative: str) -> bytes:
        return self.absolute_path(relative).read_bytes()
