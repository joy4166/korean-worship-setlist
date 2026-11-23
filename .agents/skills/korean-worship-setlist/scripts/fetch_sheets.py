"""Fetch publicly hosted worship sheet images/PDFs into the local sheet cache with safety and size gates."""

from __future__ import annotations

import argparse
import html as html_lib
import io
import ipaddress
import json
import re
import socket
import ssl
import sys
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlparse, urlunparse

import pypdfium2 as pdfium
from PIL import Image, ImageOps

from common import (
    SKILL_DIR,
    configure_utf8_stdio,
    is_blocked_source,
    load_user_config,
    normalize_nfc,
    parse_key_signature,
    resolve_workspace_path,
)

MAX_IMAGE_PIXELS = 25_000_000
WINDOWS_RESERVED_STEMS = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.IGNORECASE)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

SKIP_URL_SUBSTRINGS = (
    "favicon",
    "tistory_admin",
    "t1.daumcdn.net/tistory_admin",
    "t1.daumcdn.net/cfile/tistory/24",
    "profile",
    "avatar",
    "emoticon",
    "sticker",
    "badge",
    "banner",
    "doubleclick",
    "googlesyndication",
    "kakaoad",
    "adfit",
    "tistory.com/images/",
    "static/images/default",
    "share_btn",
    "icon_",
    "_icon",
)


def sanitize_filename_part(text: str) -> str:
    """Normalize NFC, strip Windows-disallowed chars, and guard reserved device stems."""
    nfc = normalize_nfc(text).strip()
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", nfc)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    if cleaned and WINDOWS_RESERVED_STEMS.match(cleaned):
        cleaned = f"_{cleaned}"
    return cleaned


def _default_sheet_dir() -> Path:
    cfg = load_user_config()
    dirs = cfg.get("local_sheet_dirs") or []
    if dirs:
        return resolve_workspace_path(dirs[0])
    return resolve_workspace_path("sheet")


def _normalize_public_url(url: str) -> str:
    """Percent-encode non-ASCII path/query characters so urllib handles Korean filenames cleanly."""
    parsed = urlparse(url)
    path = quote(parsed.path, safe="/%:@!$&'()*+,;=-._~")
    query = quote(parsed.query, safe="=&%:@!$'()*+,;/-._~?")
    return urlunparse((parsed.scheme, parsed.netloc, path, parsed.params, query, parsed.fragment))


def _build_request(url: str, referer: str | None = None) -> urllib.request.Request:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,image/avif,image/webp,image/png,image/jpeg,application/pdf,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    if referer:
        headers["Referer"] = referer
    return urllib.request.Request(_normalize_public_url(url), headers=headers)


def _is_disallowed_ip(ip_obj: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        ip_obj.is_private
        or ip_obj.is_loopback
        or ip_obj.is_link_local
        or ip_obj.is_multicast
        or ip_obj.is_reserved
        or ip_obj.is_unspecified
    )


def validate_public_http_url(url: str, *, allow_private_ips: bool = False) -> str:
    """Validate scheme is http/https, not a blocked domain, and resolves only to public IPs."""
    blocked, term = is_blocked_source(url)
    if blocked:
        raise ValueError(f"Blocked source domain/keyword in URL ({term}): {url}")

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError(f"Unsupported or invalid HTTP(S) URL: {url}")

    host = parsed.hostname
    if not allow_private_ips:
        # Check literal IPs directly without relying on DNS
        try:
            literal_ip = ipaddress.ip_address(host)
            if _is_disallowed_ip(literal_ip):
                raise ValueError(f"Private or local IP target forbidden: {url}")
        except ValueError as exc:
            if "forbidden" in str(exc):
                raise
            try:
                infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
            except socket.gaierror:
                infos = []
            for info in infos:
                addr_str = info[4][0].split("%")[0]
                try:
                    ip_obj = ipaddress.ip_address(addr_str)
                except ValueError:
                    continue
                if _is_disallowed_ip(ip_obj):
                    raise ValueError(f"Host {host} resolves to disallowed non-public IP ({addr_str}): {url}")
    return url


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-validate scheme, blocked sources, and resolved public IPs on every HTTP redirect hop."""

    max_redirections = 5

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        resolved = urljoin(req.full_url, newurl)
        validate_public_http_url(resolved)
        next_req = super().redirect_request(req, fp, code, msg, headers, resolved)
        return next_req


def fetch_url_bytes(
    url: str,
    *,
    referer: str | None = None,
    timeout: int = 20,
    max_bytes: int = 25 * 1024 * 1024,
) -> tuple[str, str, bytes]:
    """Download public HTTP/HTTPS content following validated redirects, refusing gated/private responses."""
    validate_public_http_url(url)

    ctx = ssl.create_default_context()
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ctx),
        _SafeRedirectHandler(),
    )
    req = _build_request(url, referer=referer)
    with opener.open(req, timeout=timeout) as resp:
        final_url = resp.geturl()
        validate_public_http_url(final_url)

        status = getattr(resp, "status", 200)
        if status in (401, 403):
            raise PermissionError(f"Access denied ({status}) for {final_url}")

        ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"Response exceeded {max_bytes} byte limit for {final_url}")
            chunks.append(chunk)
        return final_url, ctype, b"".join(chunks)


class _SheetHTMLParser(HTMLParser):
    """Extract page title, og:image, article images, and linked PDFs in document order."""

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.in_title = False
        self.title_parts: list[str] = []
        self.og_images: list[dict[str, str]] = []
        self.body_media: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        t = tag.lower()
        if t == "title":
            self.in_title = True
        elif t == "meta":
            prop = attr_map.get("property", "").lower() or attr_map.get("name", "").lower()
            if prop in ("og:image", "twitter:image"):
                content = attr_map.get("content", "").strip()
                if content:
                    self.og_images.append(
                        {"url": urljoin(self.base_url, content), "alt": "", "source": prop}
                    )
        elif t == "img":
            for key in ("data-original", "data-origin-src", "data-src", "src"):
                raw = attr_map.get(key, "").strip()
                if raw and not raw.startswith("data:"):
                    self.body_media.append(
                        {
                            "url": urljoin(self.base_url, html_lib.unescape(raw)),
                            "alt": attr_map.get("alt", "").strip(),
                            "title": attr_map.get("title", "").strip(),
                            "data_filename": attr_map.get("data-filename", "").strip(),
                            "source": f"img.{key}",
                        }
                    )
                    break
        elif t == "a":
            href = attr_map.get("href", "").strip()
            if href and any(ext in href.lower() for ext in (".pdf", ".png", ".jpg", ".jpeg", ".webp")):
                self.body_media.append(
                    {
                        "url": urljoin(self.base_url, html_lib.unescape(href)),
                        "alt": "",
                        "title": attr_map.get("title", "").strip(),
                        "data_filename": "",
                        "source": "a.href",
                    }
                )

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)


def _is_likely_ui_asset(url: str) -> bool:
    low = url.lower()
    if any(skip in low for skip in SKIP_URL_SUBSTRINGS):
        return True
    if low.endswith(".svg") or low.endswith(".gif") or low.endswith(".ico"):
        return True
    return False


def _score_candidate_url(item: dict[str, str], target_key: str) -> tuple[int, str]:
    url = item["url"]
    low = url.lower()
    meta_text = " ".join(
        [url, item.get("alt", ""), item.get("title", ""), item.get("data_filename", "")]
    ).lower()
    score = 0

    if item.get("source", "").startswith("img."):
        score += 20
    elif item.get("source") == "a.href":
        score += 15
    elif item.get("source") == "og:image":
        score += 5  # Prefer inline article images over og:image thumbnails

    if any(cdn in low for cdn in ("kakaocdn.net/dn/", "blogfiles.pstatic.net", "postfiles.pstatic.net", "tistory.com/attach")):
        score += 25
    if low.endswith((".png", ".jpg", ".jpeg", ".pdf")) or "/img." in low or "image" in low:
        score += 10
    if "thumb" in low or "C100x" in url or "R150x" in url or "fname=" in low and "kakaocdn" not in low:
        score -= 10

    if target_key:
        _, target_maj, _, _ = parse_key_signature(target_key)
        key_tok = target_maj.lower()
        if re.search(rf"(?<![a-z]){re.escape(key_tok)}(?:\s*(?:key|코드|키))?(?![a-z])", meta_text):
            score += 15
    return score, url


def extract_media_candidates(
    html_text: str,
    base_url: str,
    *,
    target_key: str = "",
) -> tuple[str, list[dict[str, str]]]:
    """Parse HTML and return (page_title, prioritized_unique_media_candidates)."""
    parser = _SheetHTMLParser(base_url)
    parser.feed(html_text)
    page_title = normalize_nfc("".join(parser.title_parts)).strip()

    # Catch KakaoCDN/Tistory image URLs embedded in script/JSON blobs if parser missed them
    raw_candidates = list(parser.body_media) + list(parser.og_images)
    for m in re.finditer(
        r"https?://(?:blog\.kakaocdn\.net/dn/[^\s\"'<>\\]+|postfiles\.pstatic\.net/[^\s\"'<>\\]+|img\d\.daumcdn\.net/[^\s\"'<>\\]+)",
        html_text,
    ):
        u = html_lib.unescape(m.group(0))
        if "thumb" not in u.lower():
            raw_candidates.append({"url": u, "alt": "", "title": "", "data_filename": "", "source": "regex"})

    seen: set[str] = set()
    filtered: list[dict[str, str]] = []
    for cand in raw_candidates:
        u = cand["url"].strip()
        if not u or u in seen or _is_likely_ui_asset(u):
            continue
        seen.add(u)
        filtered.append(cand)

    # Keep natural order tie-broken by host/key heuristic so page content images lead
    ranked = sorted(
        enumerate(filtered),
        key=lambda pair: (_score_candidate_url(pair[1], target_key)[0], -pair[0]),
        reverse=True,
    )
    return page_title, [item for _, item in ranked]


def _inspect_and_normalize_image(
    raw_bytes: bytes,
    *,
    min_width: int = 500,
    min_height: int = 500,
    max_pixels: int = MAX_IMAGE_PIXELS,
) -> tuple[bytes, str, int, int] | None:
    """Validate an image is legible size (>= min_width x min_height, <= max_pixels) and normalize EXIF/alpha."""
    try:
        with Image.open(io.BytesIO(raw_bytes)) as opened:
            raw_w, raw_h = opened.size
            if raw_w * raw_h > max_pixels:
                return None
            img = ImageOps.exif_transpose(opened)
            w, h = img.size
            if w < min_width or h < min_height or (w * h) > max_pixels:
                return None

            orig_fmt = (opened.format or "PNG").upper()
            if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                rgba = img.convert("RGBA")
                bg = Image.new("RGB", rgba.size, (255, 255, 255))
                bg.paste(rgba, mask=rgba.split()[-1])
                img = bg
            elif img.mode != "RGB":
                img = img.convert("RGB")

            out_buf = io.BytesIO()
            if orig_fmt in ("JPEG", "JPG"):
                img.save(out_buf, format="JPEG", quality=95)
                ext = "jpg"
            else:
                img.save(out_buf, format="PNG")
                ext = "png"
            return out_buf.getvalue(), ext, w, h
    except Exception:
        return None


def _validate_pdf_bytes(raw_bytes: bytes) -> int | None:
    """Validate PDF bytes via pypdfium2 and return page count, or None if invalid."""
    if not raw_bytes.lstrip().startswith(b"%PDF"):
        return None
    try:
        doc = pdfium.PdfDocument(raw_bytes)
        try:
            count = len(doc)
            return count if count >= 1 else None
        finally:
            doc.close()
    except Exception:
        return None


def _build_output_path(
    out_dir: Path,
    title: str,
    key: str,
    ext: str,
    *,
    candidate_index: int | None = None,
) -> Path:
    safe_title = sanitize_filename_part(title) or "sheet"
    if key:
        canon_key = parse_key_signature(key)[0].replace(" ", "_")
        stem = f"{safe_title}_{sanitize_filename_part(canon_key)}"
    else:
        stem = safe_title
    if candidate_index is not None:
        stem = f"{stem}_cand{candidate_index}"
    out_path = out_dir / f"{stem}.{ext}"
    if not out_path.resolve().is_relative_to(out_dir.resolve()):
        raise ValueError(f"Output path escapes sheet directory: {out_path}")
    return out_path


def fetch_sheet_from_url(
    url: str,
    title: str,
    *,
    key: str = "",
    out_dir: Path | None = None,
    max_save: int = 1,
    max_probe: int = 8,
    min_width: int = 500,
    min_height: int = 500,
) -> dict[str, Any]:
    """
    Fetch public sheet music from a direct image/PDF URL or a public blog post URL,
    validate resolution (>= min_width x min_height), and save into out_dir as <곡명>_<Key>.<ext>.
    """
    target_dir = out_dir or _default_sheet_dir()
    target_dir.mkdir(parents=True, exist_ok=True)

    final_url, ctype, raw_bytes = fetch_url_bytes(url)
    page_title = ""
    media_queue: list[tuple[str, str, bytes | None]] = []
    saved: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []

    if ctype.startswith("image/") or ctype == "application/pdf":
        media_queue.append((final_url, ctype, raw_bytes))
    else:
        html_text = raw_bytes.decode("utf-8", errors="ignore")
        page_title, candidates = extract_media_candidates(html_text, final_url, target_key=key)
        blocked_title, term_title = is_blocked_source(page_title)
        if blocked_title:
            raise ValueError(f"Blocked source found in page title ({term_title}): {page_title}")
        for cand in candidates[:max_probe]:
            cand_blocked, cand_term = is_blocked_source(
                cand.get("alt"),
                cand.get("title"),
                cand.get("data_filename"),
                cand.get("url"),
            )
            if cand_blocked:
                skipped.append({"url": cand["url"], "reason": f"blocked_source:{cand_term}"})
                continue
            media_queue.append((cand["url"], "", None))

    for media_url, media_ctype, pre_bytes in media_queue:
        if len(saved) >= max_save:
            break
        try:
            if pre_bytes is None:
                m_final, media_ctype, raw_media = fetch_url_bytes(media_url, referer=final_url)
            else:
                m_final, raw_media = media_url, pre_bytes
        except Exception as exc:
            skipped.append({"url": media_url, "reason": str(exc)})
            continue

        pdf_pages = None
        if media_ctype == "application/pdf" or m_final.lower().split("?")[0].endswith(".pdf"):
            pdf_pages = _validate_pdf_bytes(raw_media)
            if pdf_pages is not None:
                cand_idx = len(saved) + 1 if max_save > 1 else None
                out_path = _build_output_path(target_dir, title, key, "pdf", candidate_index=cand_idx)
                out_path.write_bytes(raw_media)
                saved.append(
                    {
                        "path": str(out_path),
                        "format": "pdf",
                        "page_count": pdf_pages,
                        "source_url": m_final,
                        "page_url": final_url,
                        "bytes": len(raw_media),
                    }
                )
                continue

        img_info = _inspect_and_normalize_image(raw_media, min_width=min_width, min_height=min_height)
        if img_info is None:
            skipped.append({"url": m_final, "reason": f"not_valid_image_or_below_{min_width}x{min_height}"})
            continue

        norm_bytes, ext, w, h = img_info
        cand_idx = len(saved) + 1 if max_save > 1 else None
        out_path = _build_output_path(target_dir, title, key, ext, candidate_index=cand_idx)
        out_path.write_bytes(norm_bytes)
        saved.append(
            {
                "path": str(out_path),
                "format": ext,
                "width": w,
                "height": h,
                "source_url": m_final,
                "page_url": final_url,
                "bytes": len(norm_bytes),
            }
        )

    return {
        "title": title,
        "requested_key": key,
        "page_url": final_url,
        "page_title": page_title,
        "saved": saved,
        "skipped": skipped,
        "requires_vision_verification": True,
    }


def main() -> None:
    configure_utf8_stdio()

    parser = argparse.ArgumentParser(description="Fetch public sheet music images/PDFs into local sheet directory")
    parser.add_argument("--url", required=True, help="Public blog post URL, redirect URL, or direct image/PDF URL")
    parser.add_argument("--title", required=True, help="Korean song title (used for <곡명>_<Key>.<ext>)")
    parser.add_argument("--key", default="", help="Expected sheet key (e.g. G, A, E)")
    parser.add_argument("--out-dir", type=Path, default=None, help="Destination sheet directory (default: config local_sheet_dirs[0])")
    parser.add_argument("--max-save", type=int, default=1, help="Number of valid candidate images to save for vision check")
    parser.add_argument("--min-width", type=int, default=500)
    parser.add_argument("--min-height", type=int, default=500)
    args = parser.parse_args()

    result = fetch_sheet_from_url(
        args.url,
        args.title,
        key=args.key,
        out_dir=args.out_dir,
        max_save=args.max_save,
        min_width=args.min_width,
        min_height=args.min_height,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
