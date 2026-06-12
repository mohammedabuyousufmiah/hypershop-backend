from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

import httpx

from app.channels import IncomingMessage, parse_whatsapp_payload, register_channel
from app.config import settings

logger = logging.getLogger(__name__)


class WhatsAppClient:
    name = "whatsapp"

    def __init__(self) -> None:
        self.cfg = settings()

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.whatsapp_access_token and self.cfg.whatsapp_phone_number_id)

    def parse_incoming(self, payload: dict) -> list[IncomingMessage]:
        return parse_whatsapp_payload(payload)

    async def send_text(self, to: str, text: str) -> dict[str, Any]:
        if not self.enabled:
            return {"dry_run": True, "to": to, "text": text}
        url = f"https://graph.facebook.com/v20.0/{self.cfg.whatsapp_phone_number_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": text},
        }
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {self.cfg.whatsapp_access_token}"},
                json=payload,
            )
            response.raise_for_status()
            return response.json()

    async def send_image(
        self, to: str, image_url: str, caption: str | None = None
    ) -> dict[str, Any]:
        if not self.enabled:
            return {"dry_run": True, "to": to, "image_url": image_url, "caption": caption}
        url = f"https://graph.facebook.com/v20.0/{self.cfg.whatsapp_phone_number_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "image",
            "image": {"link": image_url, "caption": caption or ""},
        }
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {self.cfg.whatsapp_access_token}"},
                json=payload,
            )
            response.raise_for_status()
            return response.json()

    # ── voice-note / media flow ───────────────────────────────────

    async def download_media(self, media_id: str) -> tuple[bytes, str]:
        """Two-step download: GET media metadata → GET signed CDN URL → bytes.

        Enforces `voice_note_max_audio_bytes` to prevent OOM if Meta returns a
        very large or malicious payload. Streams the body and aborts when the
        running total crosses the cap.
        """
        if not self.enabled:
            return b"", "audio/ogg"
        max_bytes = int(self.cfg.voice_note_max_audio_bytes or 16 * 1024 * 1024)
        meta_url = f"https://graph.facebook.com/v20.0/{media_id}"
        async with httpx.AsyncClient(timeout=30) as client:
            meta = await client.get(
                meta_url,
                headers={"Authorization": f"Bearer {self.cfg.whatsapp_access_token}"},
            )
            meta.raise_for_status()
            meta_body = meta.json()
            cdn_url = meta_body.get("url")
            mime = meta_body.get("mime_type", "audio/ogg")
            if not cdn_url:
                raise RuntimeError(f"WhatsApp media metadata missing 'url': {meta_body}")

            # Reject up-front if Meta advertises a huge file
            advertised = int(meta_body.get("file_size") or 0)
            if advertised and advertised > max_bytes:
                raise RuntimeError(
                    f"WhatsApp media size {advertised} bytes exceeds limit {max_bytes}"
                )

            chunks: list[bytes] = []
            running = 0
            async with client.stream(
                "GET",
                cdn_url,
                headers={"Authorization": f"Bearer {self.cfg.whatsapp_access_token}"},
            ) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes():
                    running += len(chunk)
                    if running > max_bytes:
                        raise RuntimeError(
                            f"WhatsApp media exceeded limit {max_bytes} bytes during download"
                        )
                    chunks.append(chunk)
            return b"".join(chunks), mime

    async def upload_media(self, audio: bytes, mime: str = "audio/mpeg") -> str:
        """Upload media bytes to WhatsApp resumable endpoint, return media_id."""
        if not self.enabled:
            return f"dryrun-media-{len(audio)}"
        url = f"https://graph.facebook.com/v20.0/{self.cfg.whatsapp_phone_number_id}/media"
        files = {"file": ("voice", audio, mime)}
        data = {"messaging_product": "whatsapp", "type": mime}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {self.cfg.whatsapp_access_token}"},
                files=files,
                data=data,
            )
            resp.raise_for_status()
            body = resp.json()
        media_id = body.get("id")
        if not media_id:
            raise RuntimeError(f"WhatsApp media upload returned no id: {body}")
        return media_id

    async def send_voice(
        self, to: str, media_id: str, *, voice_note: bool = False
    ) -> dict[str, Any]:
        """Send an `audio` message. Set voice_note=True only when media is OGG/Opus
        — Meta uses the `voice: true` flag to render as PTT (waveform UI)."""
        if not self.enabled:
            return {"dry_run": True, "to": to, "media_id": media_id, "voice": voice_note}
        url = f"https://graph.facebook.com/v20.0/{self.cfg.whatsapp_phone_number_id}/messages"
        audio_obj: dict[str, Any] = {"id": media_id}
        if voice_note:
            audio_obj["voice"] = True
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "audio",
            "audio": audio_obj,
        }
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {self.cfg.whatsapp_access_token}"},
                json=payload,
            )
            response.raise_for_status()
            return response.json()


class OpenAIClient:
    def __init__(self) -> None:
        self.cfg = settings()

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.openai_api_key)

    async def customer_reply(
        self, customer_language: str, customer_text: str, product_context: str
    ) -> tuple[str | None, float]:
        if not self.enabled:
            return None, 0.0
        system = (
            "You are a WhatsApp pharmacy customer care assistant. Main language is Bangla. "
            "Use English only if the customer requests English. Never use Arabic. "
            "Do not invent price, stock, offer, delivery time, or policy. "
            "Only use the provided product context."
        )
        user = (
            f"Customer language: {customer_language}\n"
            f"Customer message: {customer_text}\n"
            f"Approved product context:\n{product_context}"
        )
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.cfg.openai_api_key}"},
                json={
                    "model": self.cfg.openai_model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": 0.2,
                },
            )
            response.raise_for_status()
            data = response.json()
        content = data["choices"][0]["message"]["content"].strip()
        return content, 0.82


class GoogleSheetsClient:
    def __init__(self) -> None:
        self.cfg = settings()

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.google_sheets_client_email and self.cfg.google_sheets_private_key)

    def append_row(
        self, spreadsheet_id: str | None, row: list[Any], tab: str = "Sheet1"
    ) -> dict[str, Any]:
        if not self.enabled or not spreadsheet_id:
            return {"dry_run": True, "spreadsheet_id": spreadsheet_id, "row": row}
        # Lazy-import google libs so the test environment / WhatsApp-only
        # deployments don't need them installed.
        from google.oauth2 import service_account  # type: ignore[import-not-found]
        from googleapiclient.discovery import build  # type: ignore[import-not-found]

        info = {
            "type": "service_account",
            "client_email": self.cfg.google_sheets_client_email,
            "private_key": (
                self.cfg.google_sheets_private_key.replace("\\n", "\n")
                if self.cfg.google_sheets_private_key
                else None
            ),
            "token_uri": "https://oauth2.googleapis.com/token",
        }
        credentials = service_account.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
        result = (
            service.spreadsheets()
            .values()
            .append(
                spreadsheetId=spreadsheet_id,
                range=f"{tab}!A:Z",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body={"values": [row]},
            )
            .execute()
        )
        return {"dry_run": False, "updates": result.get("updates", {})}


@lru_cache
def whatsapp_client() -> WhatsAppClient:
    client = WhatsAppClient()
    register_channel(client)
    return client


@lru_cache
def openai_client() -> OpenAIClient:
    return OpenAIClient()


@lru_cache
def sheets_client() -> GoogleSheetsClient:
    return GoogleSheetsClient()


# Eagerly register WhatsApp adapter on module import so the channel registry
# is populated even if no inbound traffic has called the lru_cache yet.
whatsapp_client()
