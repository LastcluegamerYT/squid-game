"""Safe, small Open Graph previews for links shared in private messages."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.auth.firebase_auth import get_current_user
from app.models.user import UserProfile


router = APIRouter(prefix="/link-preview", tags=["Link previews"])

_MAX_HTML_BYTES = 400_000
_MAX_REDIRECTS = 3
_PREVIEW_HEADERS = {
    "Accept": "text/html,application/xhtml+xml",
    "User-Agent": "THE-IDEON link preview/1.0 (+https://theideon.app)",
}


class LinkPreviewResponse(BaseModel):
    url: str
    title: Optional[str] = None
    description: Optional[str] = None
    image: Optional[str] = None


class _PageMetadata(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self.meta: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]):
        values = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "title":
            self._in_title = True
        if tag.lower() == "meta":
            key = (values.get("property") or values.get("name") or "").lower()
            content = values.get("content", "").strip()
            if key and content and key not in self.meta:
                self.meta[key] = content

    def handle_endtag(self, tag: str):
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str):
        if self._in_title and len(self.title) < 300:
            self.title += data


def _public_http_url(value: str) -> str:
    """Validate a remote URL before the server makes any outbound request."""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Only public HTTP(S) links can be previewed")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith((".localhost", ".local", ".internal")):
        raise ValueError("Local network links cannot be previewed")
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("Invalid link port") from error
    if port not in {None, 80, 443}:
        raise ValueError("Only standard web ports can be previewed")
    try:
        addresses = socket.getaddrinfo(hostname, port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise ValueError("Could not resolve this link") from error
    resolved = {entry[4][0] for entry in addresses}
    if not resolved:
        raise ValueError("Could not resolve this link")
    for address in resolved:
        try:
            if not ipaddress.ip_address(address).is_global:
                raise ValueError("Private network links cannot be previewed")
        except ValueError:
            raise ValueError("Invalid link address")
    return parsed.geturl()


def _trim(value: str, maximum: int) -> Optional[str]:
    normalized = " ".join(value.split())
    if not normalized:
        return None
    return f"{normalized[:maximum - 1].rstrip()}…" if len(normalized) > maximum else normalized


def _preview_sync(initial_url: str) -> LinkPreviewResponse:
    current_url = _public_http_url(initial_url)
    response: Optional[requests.Response] = None
    try:
        for _ in range(_MAX_REDIRECTS + 1):
            current_url = _public_http_url(current_url)
            response = requests.get(
                current_url,
                headers=_PREVIEW_HEADERS,
                timeout=(2.5, 5),
                allow_redirects=False,
                stream=True,
            )
            if response.is_redirect:
                location = response.headers.get("Location")
                if not location:
                    break
                response.close()
                current_url = urljoin(current_url, location)
                continue
            break
        if response is None or not response.ok:
            return LinkPreviewResponse(url=current_url)
        content_type = response.headers.get("content-type", "").lower()
        if "html" not in content_type:
            return LinkPreviewResponse(url=current_url)
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_content(chunk_size=16_384):
            if not chunk:
                continue
            remaining = _MAX_HTML_BYTES - total
            if remaining <= 0:
                break
            chunks.append(chunk[:remaining])
            total += len(chunks[-1])
            if total >= _MAX_HTML_BYTES:
                break
        html = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
        parser = _PageMetadata()
        parser.feed(html)
        title = _trim(parser.meta.get("og:title") or parser.meta.get("twitter:title") or parser.title, 120)
        description = _trim(parser.meta.get("og:description") or parser.meta.get("twitter:description") or parser.meta.get("description", ""), 180)
        image_value = parser.meta.get("og:image") or parser.meta.get("twitter:image")
        image = urljoin(response.url or current_url, image_value) if image_value else None
        if image and urlparse(image).scheme not in {"http", "https"}:
            image = None
        return LinkPreviewResponse(url=response.url or current_url, title=title, description=description, image=image)
    except requests.RequestException:
        return LinkPreviewResponse(url=current_url)
    finally:
        if response is not None:
            response.close()


@router.get("", response_model=LinkPreviewResponse)
async def get_link_preview(
    url: str = Query(..., min_length=8, max_length=2048),
    _current_user: UserProfile = Depends(get_current_user),
):
    """Fetch only basic public-page metadata; it never returns remote HTML."""
    try:
        return await asyncio.to_thread(_preview_sync, url)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
