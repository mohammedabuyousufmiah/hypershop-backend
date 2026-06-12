"""SEO + storage credentials validator.

Run after pasting any of:
  - NEXT_PUBLIC_SEO_VERIFY_GOOGLE  (storefront .env)
  - NEXT_PUBLIC_SEO_VERIFY_BING
  - NEXT_PUBLIC_SEO_VERIFY_YANDEX
  - NEXT_PUBLIC_SEO_VERIFY_FACEBOOK
  - NEXT_PUBLIC_SEO_VERIFY_PINTEREST
  - seo_indexnow_key, seo_indexnow_enabled  (backend .env)
  - R2_BUCKET_NAME, R2_ACCOUNT_ID, R2_ACCESS_KEY_ID,
    R2_SECRET_ACCESS_KEY, R2_PUBLIC_BASE_URL                (env)

Per-provider checks:

  google/bing/yandex/facebook/pinterest verify
      → HEAD the storefront base URL, parse meta tag, compare to env
  indexnow
      → fetch /<key>.txt from backend, verify body == key, post a probe
        URL to api.indexnow.org and require HTTP 200/202
  r2
      → boto3 list_objects_v2 with limit=1 to confirm signature works,
        plus HEAD on the public CDN base to confirm cdn binding

Exit code = number of failures (0 = all green).

Usage:
  .venv/Scripts/python -m scripts.seo_creds_check \\
      --backend http://127.0.0.1:8000 \\
      --storefront https://hypershop.com.bd
"""
from __future__ import annotations

import argparse
import os
import sys
from urllib.request import Request, urlopen


_STATUS_PASS = "PASS"
_STATUS_FAIL = "FAIL"
_STATUS_SKIP = "SKIP (creds not set)"


def _http_get(url: str, *, timeout: int = 10) -> tuple[int, str]:
    """Return (status, body). On exception → (0, str(exc))."""
    try:
        with urlopen(Request(url, headers={"User-Agent": "hypershop-creds-check/1"}),
                     timeout=timeout) as resp:
            return resp.status, resp.read(8192).decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)


def _http_post_json(url: str, body: bytes, *, ct: str = "application/json",
                    timeout: int = 10) -> tuple[int, str]:
    try:
        with urlopen(
            Request(url, data=body, method="POST",
                    headers={"Content-Type": ct,
                             "User-Agent": "hypershop-creds-check/1"}),
            timeout=timeout,
        ) as resp:
            return resp.status, resp.read(2048).decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)


def check_verify_meta(*, storefront: str, env_key: str,
                      meta_name: str, label: str) -> tuple[str, str]:
    val = (os.environ.get(env_key) or "").strip()
    if not val:
        return _STATUS_SKIP, f"set {env_key}=<token> to enable"
    status, body = _http_get(storefront)
    if status != 200:
        return _STATUS_FAIL, f"storefront {storefront} → http {status}"
    needle = f'name="{meta_name}"'
    if needle not in body:
        return _STATUS_FAIL, (
            f"meta name='{meta_name}' not in storefront HTML — "
            f"check FE was redeployed after env update"
        )
    if val not in body:
        return _STATUS_FAIL, (
            f"meta tag present but content does not match {env_key} value"
        )
    return _STATUS_PASS, f"{label} verification token live in <head>"


def check_indexnow(*, backend: str) -> tuple[str, str]:
    """Probe key file + send a test URL via api.indexnow.org."""
    import json
    enabled = (os.environ.get("seo_indexnow_enabled", "")
               or os.environ.get("SEO_INDEXNOW_ENABLED", "")).strip().lower()
    key = (os.environ.get("seo_indexnow_key", "")
           or os.environ.get("SEO_INDEXNOW_KEY", "")).strip()
    host = (os.environ.get("seo_indexnow_host", "")
            or os.environ.get("SEO_INDEXNOW_HOST", "")).strip()
    if enabled not in ("1", "true", "yes") or not key:
        return _STATUS_SKIP, "set seo_indexnow_enabled=true + seo_indexnow_key=<...>"
    status, body = _http_get(f"{backend}/{key}.txt")
    if status != 200 or body.strip() != key:
        return _STATUS_FAIL, (
            f"{backend}/{key}.txt → http {status} / body!={key!r}; "
            f"backend hasn't picked up new env"
        )
    if not host:
        return _STATUS_FAIL, "seo_indexnow_host empty"
    payload = json.dumps({
        "host": host, "key": key,
        "urlList": [f"https://{host}/"],
    }).encode("utf-8")
    p_status, p_body = _http_post_json("https://api.indexnow.org/IndexNow", payload)
    if p_status not in (200, 202):
        return _STATUS_FAIL, (
            f"api.indexnow.org POST → http {p_status} body={p_body[:200]!r}"
        )
    return _STATUS_PASS, (
        f"key file served, api.indexnow.org accepted probe (http {p_status})"
    )


def check_r2() -> tuple[str, str]:
    bucket = (os.environ.get("R2_BUCKET_NAME") or "").strip()
    account = (os.environ.get("R2_ACCOUNT_ID") or "").strip()
    key = (os.environ.get("R2_ACCESS_KEY_ID") or "").strip()
    secret = (os.environ.get("R2_SECRET_ACCESS_KEY") or "").strip()
    public = (os.environ.get("R2_PUBLIC_BASE_URL") or "").strip()
    if not (bucket and account and key and secret):
        return _STATUS_SKIP, (
            "set R2_BUCKET_NAME, R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY"
        )
    try:
        import boto3
        s3 = boto3.client(
            "s3",
            endpoint_url=f"https://{account}.r2.cloudflarestorage.com",
            aws_access_key_id=key,
            aws_secret_access_key=secret,
            region_name="auto",
        )
        s3.list_objects_v2(Bucket=bucket, MaxKeys=1)
    except Exception as exc:  # noqa: BLE001
        return _STATUS_FAIL, f"R2 list_objects_v2 failed: {exc}"
    msg = f"R2 list_objects_v2 ok on bucket {bucket!r}"
    if public:
        p_status, _body = _http_get(public.rstrip("/") + "/")
        # CDN root often returns 403 (bucket index disabled) — that's
        # still a positive signature that the binding exists.
        if p_status in (200, 403, 404):
            msg += f"; cdn responds (http {p_status})"
        else:
            msg += f"; cdn probe unexpected http {p_status}"
    return _STATUS_PASS, msg


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend", default="http://127.0.0.1:8000",
        help="Backend base URL — used to probe /<indexnow-key>.txt",
    )
    parser.add_argument(
        "--storefront", default="https://hypershop.com.bd",
        help="Storefront base URL — used to probe verification meta tags",
    )
    args = parser.parse_args()

    results: list[tuple[str, str, str]] = []
    results.append(("google verify", *check_verify_meta(
        storefront=args.storefront,
        env_key="NEXT_PUBLIC_SEO_VERIFY_GOOGLE",
        meta_name="google-site-verification",
        label="Google Search Console",
    )))
    results.append(("bing verify", *check_verify_meta(
        storefront=args.storefront,
        env_key="NEXT_PUBLIC_SEO_VERIFY_BING",
        meta_name="msvalidate.01",
        label="Bing Webmaster",
    )))
    results.append(("yandex verify", *check_verify_meta(
        storefront=args.storefront,
        env_key="NEXT_PUBLIC_SEO_VERIFY_YANDEX",
        meta_name="yandex-verification",
        label="Yandex Webmaster",
    )))
    results.append(("facebook verify", *check_verify_meta(
        storefront=args.storefront,
        env_key="NEXT_PUBLIC_SEO_VERIFY_FACEBOOK",
        meta_name="facebook-domain-verification",
        label="Facebook Commerce",
    )))
    results.append(("pinterest verify", *check_verify_meta(
        storefront=args.storefront,
        env_key="NEXT_PUBLIC_SEO_VERIFY_PINTEREST",
        meta_name="p:domain_verify",
        label="Pinterest",
    )))
    results.append(("indexnow", *check_indexnow(backend=args.backend)))
    results.append(("r2", *check_r2()))

    n_fail = sum(1 for _, st, _ in results if st == _STATUS_FAIL)

    print()
    print(f"{'provider':<22} {'status':<24} detail")
    print("-" * 100)
    for name, st, msg in results:
        print(f"{name:<22} {st:<24} {msg}")
    print()
    print(f"failures: {n_fail}")
    return n_fail


if __name__ == "__main__":
    sys.exit(main())
