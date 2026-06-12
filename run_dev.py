"""Dev entrypoint: loads .env without MSYS path conversion and starts uvicorn.

Bypasses Git Bash's MSYS path mangling that turns `API_PREFIX=/api/v1`
into `C:/Program Files/Git/api/v1` when sourced from .env in mintty.
"""

import os
import sys

# 1) Load .env manually so the slash-prefixed values stay intact.
with open(".env") as f:
    for raw in f:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())

# 2) Start uvicorn.
import uvicorn  # noqa: E402

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",  # bind v4 + v6 so SSR via "localhost" (::1) works
        port=8000,
        reload=False,
        log_level="info",
    )
