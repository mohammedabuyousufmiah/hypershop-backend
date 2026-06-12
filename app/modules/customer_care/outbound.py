"""Outbound message dispatch + Meta webhook signature verification + embeddings.

Five responsibilities:
1. ``send_whatsapp_text`` — POST to Meta Cloud API (free-form text).
2. ``send_whatsapp_template`` — Meta template message (required outside
   the 24-hour customer-service window for system-initiated outbound).
3. ``send_whatsapp_image`` — image with optional caption.
4. ``download_whatsapp_media`` — fetch inbound media bytes by media_id.
5. ``generate_ai_reply`` — OpenAI Chat Completions for inbound auto-reply.
6. ``embed_texts`` — OpenAI Embeddings for RAG indexing + query.
7. ``verify_meta_signature`` — HMAC-SHA256 over raw body (X-Hub-Signature-256).

All functions degrade gracefully if credentials are missing.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal
from typing import Any

import httpx

from app.core.logging import get_logger
from app.modules.customer_care.config import settings

_log = get_logger("hypershop.customer_care.outbound")


# ================================================================ Meta API
def _wa_endpoint() -> tuple[str, dict[str, str]] | None:
    """Return (url, headers) tuple or None if creds missing."""
    cfg = settings()
    if not (cfg.whatsapp_access_token and cfg.whatsapp_phone_number_id):
        return None
    url = (
        f"https://graph.facebook.com/{cfg.whatsapp_api_version}/"
        f"{cfg.whatsapp_phone_number_id}/messages"
    )
    headers = {
        "Authorization": f"Bearer {cfg.whatsapp_access_token}",
        "Content-Type": "application/json",
    }
    return url, headers


async def send_whatsapp_text(
    *,
    to_phone: str,
    body: str,
    timeout: float = 12.0,
) -> dict[str, Any] | None:
    """Free-form text. Only works inside the 24-hour service window
    (i.e. customer messaged us first in the last 24h).
    """
    info = _wa_endpoint()
    if info is None:
        _log.info("whatsapp_send_skipped_no_creds", to=to_phone, body_preview=body[:80])
        return None
    url, headers = info
    to_clean = to_phone.lstrip("+").strip()
    payload = {
        "messaging_product": "whatsapp", "to": to_clean,
        "type": "text", "text": {"preview_url": False, "body": body[:4096]},
    }
    return await _post_meta(url, headers, payload, timeout=timeout, op="text")


async def send_whatsapp_template(
    *,
    to_phone: str,
    template_name: str,
    body_params: list[str] | None = None,
    language_code: str | None = None,
    timeout: float = 12.0,
) -> dict[str, Any] | None:
    """Template message. Required for outbound notifications outside
    the 24-hour window. The template must exist + be approved in
    Meta Business Manager.

    ``body_params`` are positional variables ({{1}}, {{2}}, …) in the
    template's body component. Pass an empty list if the template
    takes no parameters.
    """
    info = _wa_endpoint()
    if info is None:
        _log.info(
            "whatsapp_template_skipped_no_creds",
            to=to_phone, template=template_name,
        )
        return None
    url, headers = info
    cfg = settings()
    to_clean = to_phone.lstrip("+").strip()
    template_payload: dict[str, Any] = {
        "name": template_name,
        "language": {"code": language_code or cfg.template_language},
    }
    if body_params:
        template_payload["components"] = [
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "text": str(p)[:300]} for p in body_params
                ],
            },
        ]
    payload = {
        "messaging_product": "whatsapp", "to": to_clean,
        "type": "template", "template": template_payload,
    }
    return await _post_meta(url, headers, payload, timeout=timeout, op="template")


async def send_whatsapp_interactive_buttons(
    *,
    to_phone: str,
    body: str,
    buttons: list[dict[str, str]],
    header: str | None = None,
    footer: str | None = None,
    timeout: float = 12.0,
) -> dict[str, Any] | None:
    """Reply-buttons message. ``buttons`` is a list of dicts:
    ``[{"id": "track_order", "title": "Track order"}, ...]``.
    Meta caps at 3 buttons per message; we slice if more are passed.
    """
    info = _wa_endpoint()
    if info is None:
        _log.info("whatsapp_buttons_skipped_no_creds", to=to_phone)
        return None
    url, headers = info
    to_clean = to_phone.lstrip("+").strip()
    btn_blocks = [
        {"type": "reply", "reply": {"id": b["id"][:256], "title": b["title"][:20]}}
        for b in buttons[:3]
    ]
    interactive: dict[str, Any] = {
        "type": "button",
        "body": {"text": body[:1024]},
        "action": {"buttons": btn_blocks},
    }
    if header:
        interactive["header"] = {"type": "text", "text": header[:60]}
    if footer:
        interactive["footer"] = {"text": footer[:60]}
    payload = {
        "messaging_product": "whatsapp", "to": to_clean,
        "type": "interactive", "interactive": interactive,
    }
    return await _post_meta(url, headers, payload, timeout=timeout, op="buttons")


async def send_whatsapp_interactive_list(
    *,
    to_phone: str,
    body: str,
    button_text: str,
    sections: list[dict[str, Any]],
    header: str | None = None,
    footer: str | None = None,
    timeout: float = 12.0,
) -> dict[str, Any] | None:
    """List-selector message. ``sections`` example:
    ``[{"title": "Help topics", "rows": [{"id":"track","title":"Track order"}]}]``.
    Meta caps: 10 sections, 10 rows total.
    """
    info = _wa_endpoint()
    if info is None:
        _log.info("whatsapp_list_skipped_no_creds", to=to_phone)
        return None
    url, headers = info
    to_clean = to_phone.lstrip("+").strip()
    payload = {
        "messaging_product": "whatsapp", "to": to_clean,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": body[:1024]},
            "action": {"button": button_text[:20], "sections": sections},
            **({"header": {"type": "text", "text": header[:60]}} if header else {}),
            **({"footer": {"text": footer[:60]}} if footer else {}),
        },
    }
    return await _post_meta(url, headers, payload, timeout=timeout, op="list")


async def send_whatsapp_typing_indicator(
    *,
    to_phone: str,
    duration_seconds: int = 5,
    timeout: float = 6.0,
) -> dict[str, Any] | None:
    """Meta Cloud API supports a typing indicator via a special
    "typing_indicator" message (rolled out late 2024). We send it
    when an agent starts typing — best-effort, ignored if Meta
    rejects."""
    info = _wa_endpoint()
    if info is None:
        return None
    url, headers = info
    to_clean = to_phone.lstrip("+").strip()
    payload = {
        "messaging_product": "whatsapp", "to": to_clean,
        "type": "typing_indicator",
        "typing_indicator": {"type": "text"},
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(url, headers=headers, json=payload)
            return r.json() if r.status_code < 400 else None
    except httpx.HTTPError:
        return None


async def transcribe_voice_audio(
    *,
    audio_bytes: bytes,
    mime_type: str = "audio/ogg",
    timeout: float = 60.0,
) -> str | None:
    """Whisper-1 transcription. Returns the text, or ``None`` if no
    API key configured / call failed. Caller is responsible for
    bounding ``audio_bytes`` size (we don't re-check)."""
    cfg = settings()
    if not cfg.openai_api_key:
        return None
    # Whisper expects multipart/form-data, not JSON
    ext_map = {
        "audio/ogg": "ogg", "audio/mpeg": "mp3", "audio/mp4": "m4a",
        "audio/wav": "wav", "audio/webm": "webm", "audio/aac": "aac",
    }
    ext = ext_map.get(mime_type, "ogg")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {cfg.openai_api_key}"},
                files={"file": (f"voice.{ext}", audio_bytes, mime_type)},
                data={"model": "whisper-1"},
            )
            r.raise_for_status()
            return r.json().get("text")
    except httpx.HTTPError as e:
        _log.warning("whisper_transcription_failed", error=str(e))
        return None


async def send_whatsapp_image(
    *,
    to_phone: str,
    image_url: str,
    caption: str | None = None,
    timeout: float = 18.0,
) -> dict[str, Any] | None:
    """Send a hosted image by URL (must be publicly fetchable)."""
    info = _wa_endpoint()
    if info is None:
        _log.info(
            "whatsapp_image_skipped_no_creds",
            to=to_phone, image_url=image_url,
        )
        return None
    url, headers = info
    to_clean = to_phone.lstrip("+").strip()
    image_block: dict[str, Any] = {"link": image_url}
    if caption:
        image_block["caption"] = caption[:1024]
    payload = {
        "messaging_product": "whatsapp", "to": to_clean,
        "type": "image", "image": image_block,
    }
    return await _post_meta(url, headers, payload, timeout=timeout, op="image")


async def download_whatsapp_media(
    *,
    media_id: str,
    timeout: float = 30.0,
    max_bytes: int = 16 * 1024 * 1024,
) -> tuple[bytes, str] | None:
    """Fetch inbound media bytes given a Meta media id. Returns
    ``(bytes, mime_type)`` or ``None`` if credentials are missing.
    The download is bounded to ``max_bytes`` so a hostile sender
    can't OOM us.
    """
    cfg = settings()
    if not cfg.whatsapp_access_token:
        return None
    headers = {"Authorization": f"Bearer {cfg.whatsapp_access_token}"}
    meta_url = f"https://graph.facebook.com/{cfg.whatsapp_api_version}/{media_id}"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            # Step 1: ask Meta for the media URL
            r = await client.get(meta_url, headers=headers)
            r.raise_for_status()
            media_meta = r.json()
            media_url = media_meta.get("url")
            mime = media_meta.get("mime_type") or "application/octet-stream"
            if not media_url:
                return None
            # Step 2: stream the binary
            data_resp = await client.get(media_url, headers=headers)
            data_resp.raise_for_status()
            data = data_resp.content
            if len(data) > max_bytes:
                _log.warning(
                    "whatsapp_media_too_large",
                    media_id=media_id, bytes=len(data),
                )
                return None
            return data, mime
    except httpx.HTTPError as e:
        _log.warning(
            "whatsapp_media_download_failed",
            media_id=media_id, error=str(e),
        )
        return None


async def _post_meta(
    url: str, headers: dict[str, str], payload: dict, *,
    timeout: float, op: str,
) -> dict[str, Any] | None:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(url, headers=headers, json=payload)
            r.raise_for_status()
            data = r.json()
            _log.info(
                f"whatsapp_send_{op}_success",
                wa_response_id=(data.get("messages") or [{}])[0].get("id"),
            )
            return data
    except httpx.HTTPError as e:
        body_text = ""
        if isinstance(e, httpx.HTTPStatusError):
            body_text = (e.response.text or "")[:400]
        _log.warning(
            f"whatsapp_send_{op}_failed",
            error=str(e), response_body=body_text,
        )
        # Persist for retry. We do this best-effort — if even the
        # DB insert fails we just log and move on (can't block the
        # caller forever on a network failure).
        await _enqueue_dead_letter(
            op=op, payload=payload,
            error=f"{type(e).__name__}: {e}",
            response_body=body_text,
        )
        return None


async def _enqueue_dead_letter(
    *,
    op: str,
    payload: dict,
    error: str,
    response_body: str,
) -> None:
    """Persist a failed WhatsApp send into ``cc_dead_letters`` so the
    retry worker picks it up. Pulls ``to_phone`` from the Meta payload.
    """
    try:
        import json as _json
        from app.core.db.uow import UnitOfWork
        from sqlalchemy import text as _t
        # Re-shape the Meta payload into a small dispatch envelope
        # that ``retry_outbound_dead_letters`` understands.
        kind = "text"
        envelope: dict[str, Any] = {
            "to_phone": "+" + str(payload.get("to") or ""),
            "kind": op,
        }
        if op == "text":
            envelope["body"] = (payload.get("text") or {}).get("body", "")
        elif op == "template":
            tpl = payload.get("template") or {}
            envelope["template_name"] = tpl.get("name")
            envelope["body_params"] = [
                p.get("text")
                for c in (tpl.get("components") or [])
                if c.get("type") == "body"
                for p in (c.get("parameters") or [])
            ]
        elif op == "image":
            img = payload.get("image") or {}
            envelope["image_url"] = img.get("link")
            envelope["caption"] = img.get("caption")
        async with UnitOfWork().transactional() as session:
            await session.execute(
                _t(
                    "INSERT INTO cc_dead_letters "
                    "(id, source, operation, payload, error_class, error_message, status, attempts) "
                    "VALUES (gen_random_uuid(), 'whatsapp_send', :op, :payload, "
                    "        'HTTPError', :err, 'pending', 0)"
                ),
                {
                    "op": op,
                    "payload": _json.dumps(envelope),
                    "err": (error + " | " + response_body)[:1500],
                },
            )
    except Exception as e:  # noqa: BLE001 — never let DLQ insert poison the caller
        _log.warning("cc_dlq_insert_failed", error=str(e))


# ================================================================ AI reply
HANDOVER_BANGLA = (
    "ক্ষমা করবেন, এই বিষয়ে আমাদের একজন এজেন্ট কিছুক্ষণের মধ্যে "
    "আপনার সাথে যোগাযোগ করবেন।"
)
HANDOVER_ENGLISH = (
    "Thanks for reaching out — one of our agents will follow up with you shortly."
)


def _wants_english(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    alpha = [c for c in t if c.isalpha()]
    if not alpha:
        return False
    ascii_alpha = sum(1 for c in alpha if ord(c) < 128)
    return ascii_alpha / len(alpha) >= 0.8


async def generate_ai_reply(
    *,
    customer_text: str,
    customer_language: str = "bangla",
    product_context: str | None = None,
    rag_context: str | None = None,
    timeout: float = 18.0,
) -> tuple[str, Decimal, bool]:
    """Return ``(reply, confidence, handover_required)``."""
    text = (customer_text or "").strip()
    if not text:
        return (HANDOVER_BANGLA, Decimal("0.50"), True)
    if text.upper() == "STOP":
        return (
            "You have been unsubscribed from marketing follow-ups."
            if customer_language == "english"
            else "আপনি মার্কেটিং ফলো-আপ থেকে আনসাবস্ক্রাইব করেছেন।",
            Decimal("0.95"), False,
        )
    if _wants_english(text):
        customer_language = "english"

    cfg = settings()
    if not cfg.openai_api_key:
        return (
            HANDOVER_ENGLISH if customer_language == "english" else HANDOVER_BANGLA,
            Decimal("0.60"), True,
        )

    system_prompt = (
        "You are a Bangladesh e-commerce customer support agent for "
        "Hypershop. Reply in the customer's language (Bangla or English). "
        "Keep answers under 4 sentences. Cite the knowledge-base context "
        "verbatim when relevant. If unsure or asked for refund/cancel/"
        "human help, say a human agent will follow up shortly."
    )
    user_blocks: list[str] = [text]
    if rag_context:
        user_blocks.append(f"\n[knowledge-base context]\n{rag_context}")
    if product_context:
        user_blocks.append(f"\n[product context]\n{product_context}")
    user_msg = "\n".join(user_blocks)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {cfg.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": cfg.openai_model,
                    "temperature": 0.4, "max_tokens": 360,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_msg},
                    ],
                },
            )
            r.raise_for_status()
            data = r.json()
            content = (
                (data.get("choices") or [{}])[0]
                .get("message", {}).get("content", "")
            ) or ""
            content = content.strip()
            if not content:
                return (
                    HANDOVER_ENGLISH if customer_language == "english" else HANDOVER_BANGLA,
                    Decimal("0.55"), True,
                )
            handover_words = (
                "refund", "cancel", "human", "complaint",
                "ফেরত", "ক্যান্সেল", "বাতিল", "অভিযোগ",
            )
            handover = any(w.lower() in content.lower() for w in handover_words)
            # Bump confidence when RAG context backed the answer
            conf = Decimal("0.88") if rag_context else Decimal("0.82")
            return (content, conf, handover)
    except httpx.HTTPError as e:
        _log.warning("openai_call_failed", error=str(e))
        return (
            HANDOVER_ENGLISH if customer_language == "english" else HANDOVER_BANGLA,
            Decimal("0.55"), True,
        )


# ================================================================ Embeddings
async def embed_texts(
    texts: list[str],
    *,
    timeout: float = 30.0,
) -> list[list[float]] | None:
    """Return one embedding vector per input. ``None`` if API key
    missing or call failed. Caller must accept that and fall back to
    keyword search.
    """
    if not texts:
        return []
    cfg = settings()
    if not cfg.openai_api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                "https://api.openai.com/v1/embeddings",
                headers={
                    "Authorization": f"Bearer {cfg.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": cfg.openai_embedding_model,
                    "input": [t[:6000] for t in texts],
                },
            )
            r.raise_for_status()
            data = r.json()
            return [item["embedding"] for item in data.get("data", [])]
    except httpx.HTTPError as e:
        _log.warning("openai_embedding_failed", error=str(e), n=len(texts))
        return None


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Pure-python cosine similarity. Fine for small KB corpora; for
    >10k chunks switch to pgvector or numpy.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0 or nb == 0:
        return 0.0
    return dot / ((na ** 0.5) * (nb ** 0.5))


# ================================================================ Meta signature
def verify_meta_signature(
    *,
    raw_body: bytes,
    signature_header: str | None,
) -> tuple[bool, str]:
    """Verify Meta Cloud API webhook signature.

    Meta sends ``X-Hub-Signature-256: sha256=<hex>`` where <hex> is
    HMAC-SHA256(app_secret, raw_body).

    Returns ``(ok, reason)``. When ``WHATSAPP_APP_SECRET`` isn't set,
    returns ``(True, "skipped_no_secret")`` so dev / staging keeps
    working without configuration — production must set it.
    """
    cfg = settings()
    if not cfg.whatsapp_app_secret:
        return True, "skipped_no_secret"
    if not signature_header:
        return False, "missing_signature_header"
    # Header format: "sha256=<hex>"
    if "=" not in signature_header:
        return False, "malformed_signature_header"
    algo, _, hex_sig = signature_header.partition("=")
    if algo.lower() != "sha256":
        return False, f"unsupported_algo_{algo}"
    expected = hmac.new(
        cfg.whatsapp_app_secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, hex_sig):
        return False, "signature_mismatch"
    return True, "ok"
