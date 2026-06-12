"""Manifest-driven brand-photo downloader.

Takes a YAML or CSV manifest of (brand_slug, image_url, target_filename)
and downloads each URL into ``<dest>/<brand_slug>/<target_filename>``.
Output folder is ready for ``scripts/ingest_photos.py --match-mode filename``.

Why not scrape brand press rooms automatically?
  - Samsung / Lenovo / Apple / Sony etc. all 403 on automated User-Agent
    requests. Operator must either:
      * download the press-kit zip manually from the brand portal, OR
      * paste direct image URLs into the manifest (often shown on the
        product spec page → right-click → copy image address).

Manifest example (YAML — preferred):

    defaults:
      brand_slug: samsung
      dest: "C:/staging/photos/"

    items:
      - url: "https://images.samsung.com/.../sm-s928-front.jpg"
        filename: "samsung_galaxy_s24_ultra_front.jpg"
      - url: "https://images.samsung.com/.../sm-s928-back.jpg"
        filename: "samsung_galaxy_s24_ultra_back.jpg"
      - brand_slug: lenovo
        url: "https://www.lenovo.com/.../thinkpad-t14-hero.png"
        filename: "lenovo_thinkpad_t14_hero.png"

CSV equivalent (header required):

    brand_slug,url,filename
    samsung,https://images.samsung.com/.../sm-s928-front.jpg,samsung_galaxy_s24_ultra_front.jpg

Usage:
    .venv/Scripts/python -m scripts.download_brand_photos \\
        --manifest scripts/brand_photo_manifest.example.yaml \\
        --dest "C:/staging/photos/"

After download:
    .venv/Scripts/python -m scripts.ingest_photos \\
        --folder "C:/staging/photos/samsung/" \\
        --brand-slug samsung \\
        --match-mode filename
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen


# Spoof a real browser UA — most brand CDNs reject the default Python UA
# even on their public image hosts. This is the same UA Chromium ships
# with on Win11; not authentication, just polite.
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


def _normalize_url(url: str) -> str:
    """No-op for now. Past attempts tried to (a) strip /thumb/ to get
    originals (Wikipedia 429s), and (b) bump thumb size to 800px
    (Wikipedia 400s when the original is smaller).

    Safest path: use exactly the thumb size the operator pasted. The
    ingest pipeline downscales further to ≤1600px and never upscales,
    so even a 250px source is fine for sitemap image:image (Google
    Image Search renders thumbnails internally anyway)."""
    return url


def _download(url: str, dest: Path, *, retries: int = 4, timeout: int = 30,
              throttle_sec: float = 0.0) -> str:
    """Returns 'ok' / 'skipped' / 'fail:<reason>'.

    ``throttle_sec`` adds a fixed sleep BEFORE the request — used
    when downloading from a host that rate-limits aggressively
    (Wikipedia drops to 429 above ~1 req/sec without it).
    """
    if dest.exists() and dest.stat().st_size > 0:
        return "skipped"
    if throttle_sec > 0:
        time.sleep(throttle_sec)
    last_err = ""
    url = _normalize_url(url)
    # Wikipedia requires a UA with a contact email/URL — politely
    # identifying us with the project name avoids a permanent block.
    ua = _UA
    if "wikimedia.org" in url:
        ua = "HypershopBot/1.0 (https://hypershop.com.bd; ops@hypershop.com.bd) python-urllib"
    for attempt in range(1, retries + 1):
        try:
            req = Request(url, headers={
                "User-Agent": ua,
                "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": f"{urlparse(url).scheme}://{urlparse(url).netloc}/",
            })
            with urlopen(req, timeout=timeout) as resp:
                if resp.status != 200:
                    last_err = f"http {resp.status}"
                    continue
                ct = resp.headers.get("Content-Type", "")
                if "image" not in ct:
                    last_err = f"unexpected content-type: {ct}"
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                with dest.open("wb") as fh:
                    while True:
                        chunk = resp.read(64 * 1024)
                        if not chunk:
                            break
                        fh.write(chunk)
                return "ok"
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)
            # 429 = back off harder. Other errors = normal jitter.
            if "429" in last_err and attempt < retries:
                time.sleep(5.0 * attempt)
            elif attempt < retries:
                time.sleep(1.5 ** attempt)
    return f"fail:{last_err}"


def _load_manifest(path: Path) -> tuple[dict, list[dict]]:
    """Returns (defaults_dict, items_list). Supports YAML or CSV."""
    if path.suffix.lower() in (".yaml", ".yml"):
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return data.get("defaults") or {}, data.get("items") or []
    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        return {}, rows
    raise SystemExit(f"unsupported manifest type: {path.suffix}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, help="YAML or CSV path")
    parser.add_argument(
        "--dest", default=None,
        help="Base output dir; overrides defaults.dest in manifest",
    )
    parser.add_argument(
        "--throttle", type=float, default=0.0,
        help="Seconds to sleep between requests (use ~1.5 for Wikimedia)",
    )
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        print(f"manifest not found: {manifest_path}", file=sys.stderr)
        return 2

    defaults, items = _load_manifest(manifest_path)
    if not items:
        print("no items in manifest", file=sys.stderr)
        return 2

    dest_base = Path(args.dest or defaults.get("dest") or "./photos").resolve()
    default_brand = defaults.get("brand_slug") or ""
    print(f"dest base: {dest_base}")
    print(f"items:     {len(items)}")

    n_ok = 0
    n_skipped = 0
    n_failed = 0
    failures: list[tuple[str, str]] = []

    for idx, raw in enumerate(items, start=1):
        brand_slug = (raw.get("brand_slug") or default_brand or "").strip()
        url = (raw.get("url") or "").strip()
        filename = (raw.get("filename") or "").strip()
        if not (brand_slug and url and filename):
            n_failed += 1
            failures.append((url, "missing brand_slug / url / filename"))
            continue
        out = dest_base / brand_slug / filename
        status = _download(url, out, throttle_sec=args.throttle)
        if status == "ok":
            n_ok += 1
            if n_ok % 10 == 0:
                print(f"  {n_ok} downloaded...")
        elif status == "skipped":
            n_skipped += 1
        else:
            n_failed += 1
            failures.append((url, status))
            if len(failures) <= 5:
                print(f"  fail: {url} → {status}")

    print()
    print("=" * 60)
    print(f"  downloaded: {n_ok}")
    print(f"  skipped:    {n_skipped} (already on disk)")
    print(f"  failed:     {n_failed}")
    if failures:
        log = manifest_path.with_suffix(manifest_path.suffix + ".failures.txt")
        log.write_text(
            "\n".join(f"{u}\t{r}" for u, r in failures),
            encoding="utf-8",
        )
        print(f"  fail log:   {log}")
    return 0 if n_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
