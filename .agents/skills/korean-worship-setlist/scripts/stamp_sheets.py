"""Stamp song-form banners onto normalized A4 sheet images, merge PDF with cover/outlines, and emit 4 deliverables."""

from __future__ import annotations

import argparse
import io
import json
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps
from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from common import (
    FONT_PATH,
    GROOVE_LABELS_KO,
    configure_utf8_stdio,
    json_dumps_ko,
    load_user_config,
    normalize_groove_style,
    parse_key_signature,
    resolve_workspace_path,
    semitone_distance,
)
from plan_transitions import realize_roman_progression

# Standard A4 at 200 DPI (aspect 1 : 1.41415)
A4_W = 1654
A4_H = 2339
SAFE_MARGIN = 71  # ~9 mm print safe margin on all sides
CONTENT_W = A4_W - 2 * SAFE_MARGIN

SERVICE_TYPE_LABELS = {
    "youth": "청년/세대통합 예배",
    "sunday_main": "주일 대예배",
    "friday_revival": "금요철야/찬양집회",
    "small_group": "소그룹/목장 예배",
}

SEASON_LABELS = {
    "ordinary": "일반주일",
    "lent": "사순절",
    "holy_week": "고난주간",
    "easter": "부활절",
    "pentecost": "성령강림절",
    "advent": "대림절",
    "christmas": "성탄절",
    "thanksgiving": "추수감사절",
    "watchnight": "송구영신",
    "communion": "성찬주일",
    "baptism": "세례식",
    "epiphany": "주현절",
}

SKILL_TIER_LABELS = {
    "beginner_friendly": "입문/초급",
    "intermediate": "중급",
    "advanced": "숙련/밴드 풀세션",
}

ENDING_TYPE_LABELS = {
    "pad_hold": "패드 홀드 여운 (말씀/기도 대기)",
    "clean_cut": "깔끔하게 동시 컷",
    "ritardando": "리타르단도 엔딩",
    "vamp": "코드 반복(Vamp) 빌드업",
}

_FONT_CACHE: dict[tuple[int, bool], ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    key = (size, bold)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    if FONT_PATH.exists():
        font = ImageFont.truetype(str(FONT_PATH), size=size)
        if bold:
            try:
                font.set_variation_by_axes([700])
            except Exception:
                try:
                    font.set_variation_by_name("Bold")
                except Exception:
                    pass
        _FONT_CACHE[key] = font
        return font
    fallback = ImageFont.load_default()
    _FONT_CACHE[key] = fallback
    return fallback


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: Any) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return int(bbox[2] - bbox[0])


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: Any, max_width: int) -> list[str]:
    if not text:
        return []
    lines: list[str] = []
    for paragraph in text.splitlines():
        if not paragraph.strip():
            lines.append("")
            continue
        words = paragraph.split(" ")
        current = ""
        for word in words:
            candidate = word if not current else f"{current} {word}"
            if _text_width(draw, candidate, font) <= max_width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                    current = word
                else:
                    buf = ""
                    for ch in word:
                        if _text_width(draw, buf + ch, font) <= max_width:
                            buf += ch
                        else:
                            if buf:
                                lines.append(buf)
                            buf = ch
                    current = buf
        if current:
            lines.append(current)
    return lines


def load_normalized_sheet(image_path: Path) -> Image.Image:
    """Apply EXIF orientation and flatten RGBA/LA/P/L onto pure white RGB canvas."""
    with Image.open(image_path) as raw:
        oriented = ImageOps.exif_transpose(raw)
        if oriented.mode in ("RGBA", "LA") or (oriented.mode == "P" and "transparency" in oriented.info):
            rgba = oriented.convert("RGBA")
            bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            composited = Image.alpha_composite(bg, rgba)
            return composited.convert("RGB")
        return oriented.convert("RGB")


def _safe_filename(text: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", text.strip()).strip("_")


def _format_youtube_deeplink(url: str | None, start_sec: int | None) -> str:
    if not url:
        return ""
    sec = int(start_sec or 0)
    base = re.sub(r"([?&])t=\d+s?", "", url).rstrip("?&")
    if sec <= 0:
        return base
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}t={sec}"


def _resolve_service_label(setlist_plan: dict[str, Any]) -> str:
    if setlist_plan.get("service_label"):
        return str(setlist_plan["service_label"])
    svc_type = str(setlist_plan.get("service_type") or "예배")
    return SERVICE_TYPE_LABELS.get(svc_type, svc_type)


def _resolve_season_label(season: str | None) -> str:
    s = season or "ordinary"
    return SEASON_LABELS.get(s, s)


def _resolve_skill_label(tier: str | None) -> str:
    t = tier or "intermediate"
    return SKILL_TIER_LABELS.get(t, t)


def _has_synth_pad(session_instruments: list[str] | None) -> bool:
    if isinstance(session_instruments, list) and len(session_instruments) > 0:
        return "synth_pad" in session_instruments
    return True


def _index_transitions_by_slot(setlist_plan: dict[str, Any]) -> dict[int, dict[str, Any]]:
    songs = setlist_plan.get("songs", [])
    title_to_slots: dict[str, list[int]] = {}
    for s in songs:
        title_to_slots.setdefault(str(s.get("title_ko") or ""), []).append(int(s["slot_index"]))

    by_slot: dict[int, dict[str, Any]] = {}
    for tr in setlist_plan.get("transitions", []):
        if tr.get("from_slot"):
            by_slot[int(tr["from_slot"])] = tr
        else:
            candidates = title_to_slots.get(str(tr.get("from_song") or ""), [])
            for s_idx in candidates:
                if s_idx not in by_slot:
                    by_slot[s_idx] = tr
                    break
    return by_slot


def _format_meter_with_groove(song: dict[str, Any]) -> str:
    meter = str(song.get("time_signature", "4/4"))
    groove_ko = GROOVE_LABELS_KO.get(normalize_groove_style(song.get("groove_style")) or "", "")
    return f"{meter} · {groove_ko}" if groove_ko else meter


def format_song_form_visual(
    song_form: str,
    transition_mode: str | None = None,
    session_instruments: list[str] | None = None,
) -> str:
    """Format a song form into a clear visual progression string with directional arrows."""
    has_pad = _has_synth_pad(session_instruments)
    pad_tag = "[패드 홀드]" if has_pad else "[잔잔한 코드 유지]"
    reset_tag = "[정박 엔딩 · 카운트 재진입]"
    text = (song_form or "I(4m) - V1 - C - O").strip()
    if transition_mode == "rhythm_reset":
        text = re.sub(r"\s*-->\s*\(Medley[^)]*\)", f"  ⇒  {reset_tag}", text, flags=re.IGNORECASE)
    else:
        text = re.sub(r"\s*-->\s*\(Medley[^)]*\)", "  ⇒  [바로 연결]", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*-->\s*\(Pad[^)]*\)", f"  ⇒  {pad_tag}", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+-\s+", "  →  ", text)
    if (
        transition_mode == "pad_break"
        and "[패드" not in text
        and "[잔잔한 코드 유지]" not in text
        and "[바로 연결]" not in text
        and "[정박 엔딩" not in text
    ):
        text = f"{text}  ⇒  {pad_tag}"
    elif (
        transition_mode == "rhythm_reset"
        and "[정박 엔딩" not in text
        and "[패드" not in text
        and "[잔잔한 코드 유지]" not in text
    ):
        text = f"{text}  ⇒  {reset_tag}"
    return text


def _format_transition_line(
    transition: dict[str, Any] | None,
    ending_type: str,
    session_instruments: list[str] | None = None,
    reprise_candidate: dict[str, Any] | None = None,
) -> str:
    has_pad = _has_synth_pad(session_instruments)
    if transition is not None:
        cue = str(transition.get("session_cue") or "").strip()
        if not has_pad:
            cue = cue.replace("[패드 홀드]", "[잔잔한 코드 유지]").replace("건반 패드 유지", "피아노 잔잔한 코드 유지")
        bridge = str(transition.get("bridge_chords") or "").strip()
        if bridge and bridge not in cue:
            return f"다음 곡 연결: {cue}  |  연결 코드: {bridge}" if cue else f"다음 곡 연결 코드: {bridge}"
        return f"다음 곡 연결: {cue}" if cue else "다음 곡 연결: 그대로 진입"
    if ending_type == "pad_hold" and not has_pad:
        end_kr = "피아노 코드 여운 (말씀/기도 대기)"
    else:
        end_kr = ENDING_TYPE_LABELS.get(ending_type, ending_type)
    base = f"엔딩: {end_kr}"
    if reprise_candidate:
        base += f"  |  설교 후 결단: {reprise_candidate['title_ko']} Reprise ({reprise_candidate['target_key']} Key)"
    return base


def song_form_to_korean_slides(
    song_form: str,
    next_song_title: str | None = None,
    transition_mode: str | None = None,
) -> str:
    """Convert musical song-form notation into plain Korean PPT slide progression."""
    if not song_form:
        return "1절 -> 후렴"

    expanded = song_form
    if "-->(Medley" in expanded or "Medley" in expanded:
        expanded = re.sub(r"-->\s*\(Medley[^)]*\)", " | MEDLEY_NEXT ", expanded)

    def _expand_bracket_list(m: re.Match[str]) -> str:
        inner = m.group(1).strip()
        if inner.lower() == "c only":
            return "후렴만"
        parts = [p.strip() for p in inner.split(",") if p.strip()]
        return " - ".join(parts)

    expanded = re.sub(r"\[([^\]]+)\]", _expand_bracket_list, expanded)
    tokens = [t.strip() for t in re.split(r"\s*-\s*|\s*\|\s*", expanded) if t.strip()]

    korean_steps: list[str] = []
    for tok in tokens:
        if tok == "MEDLEY_NEXT":
            target_label = f" ({next_song_title})" if next_song_title else ""
            if transition_mode == "rhythm_reset":
                korean_steps.append(f"★ 정박 엔딩 후 카운트에 다음 곡 진입{target_label}")
            else:
                korean_steps.append(f"★ 무공백 바로 다음 곡 진입{target_label}")
            continue

        repeat_label = ""
        m_rep = re.search(r"\(x(\d+)\)", tok, re.IGNORECASE)
        if m_rep:
            repeat_label = f" ({m_rep.group(1)}번)"
            tok = re.sub(r"\(x\d+\)", "", tok, flags=re.IGNORECASE).strip()

        m_bars = re.match(r"^(I|Inter)\((\d+m)\)$", tok, re.IGNORECASE)
        if m_bars:
            kind = "전주" if m_bars.group(1).upper() == "I" else "간주"
            bars_num = m_bars.group(2).replace("m", "마디")
            korean_steps.append(f"({kind} {bars_num}){repeat_label}")
            continue

        low = tok.lower()
        if low == "i":
            korean_steps.append(f"(전주){repeat_label}")
        elif low.startswith("inter"):
            korean_steps.append(f"(간주){repeat_label}")
        elif re.match(r"^v\d+$", low):
            korean_steps.append(f"{low[1:]}절{repeat_label}")
        elif low == "pc":
            korean_steps.append(f"프리코러스{repeat_label}")
        elif low == "c":
            korean_steps.append(f"후렴{repeat_label}")
        elif low == "후렴만":
            korean_steps.append(f"후렴만{repeat_label}")
        elif low == "b":
            korean_steps.append(f"브릿지{repeat_label}")
        elif low == "tag":
            korean_steps.append(f"태그(끝소절 반복){repeat_label}")
        elif low.startswith("vamp"):
            korean_steps.append("반주 루프(기도/멘트 대기)")
        elif low.startswith("o"):
            korean_steps.append("(후주)")
        else:
            korean_steps.append(f"{tok}{repeat_label}")

    return " -> ".join(korean_steps)


def _draw_score_annotations(
    draw: ImageDraw.ImageDraw,
    annotations: list[dict[str, Any]],
    page_num: int,
    paste_x: int,
    paste_y: int,
    img_w: int,
    img_h: int,
) -> None:
    """Draw non-destructive section/caution callout pills at normalized sheet coordinates."""
    style_palette = {
        "section": ((239, 246, 255), (29, 78, 216), (30, 58, 138), 3, _load_font(22, bold=True)),
        "caution": ((254, 226, 226), (185, 28, 28), (153, 27, 27), 3, _load_font(20, bold=True)),
        "cue": ((240, 253, 250), (15, 118, 110), (17, 94, 89), 2, _load_font(19, bold=True)),
    }
    for ann in annotations:
        if int(ann.get("page", 1)) != page_num:
            continue
        text = str(ann.get("text") or "").strip()
        if not text:
            continue
        rx = float(ann.get("rx", 0.05))
        ry = float(ann.get("ry", 0.05))
        style = str(ann.get("style", "section"))
        fill, outline, fg, width, font = style_palette.get(style, style_palette["section"])

        bbox = draw.textbbox((0, 0), text, font=font)
        tw = int(bbox[2] - bbox[0])
        th = int(bbox[3] - bbox[1])
        pad_x, pad_y = 10, 5
        pw = tw + pad_x * 2
        ph = th + pad_y * 2 + 4

        ax = paste_x + int(rx * img_w)
        ay = paste_y + int(ry * img_h)
        if ann.get("anchor") == "center":
            ax -= pw // 2
            ay -= ph // 2
        ax = max(SAFE_MARGIN, min(ax, A4_W - SAFE_MARGIN - pw))
        ay = max(SAFE_MARGIN, min(ay, A4_H - SAFE_MARGIN - ph))

        draw.rounded_rectangle([ax, ay, ax + pw, ay + ph], radius=8, fill=fill, outline=outline, width=width)
        draw.text((ax + pad_x, ay + pad_y), text, font=font, fill=fg)


def render_stamped_sheet_page(
    sheet_img_path: Path | None,
    song: dict[str, Any],
    sheet_meta: dict[str, Any],
    transition: dict[str, Any] | None,
    page_num: int,
    total_pages: int,
    show_capo: bool = False,
    session_instruments: list[str] | None = None,
    reprise_candidate: dict[str, Any] | None = None,
    pdf_page_label: str = "",
) -> Image.Image:
    """
    Render one A4 sheet page with visual hierarchy:
    Top compact metadata -> Hero standalone '곡 진행' banner -> Score (+ annotations) -> Compact footer.
    """
    canvas_img = Image.new("RGB", (A4_W, A4_H), (255, 255, 255))
    draw = ImageDraw.Draw(canvas_img)

    slot_idx = int(song["slot_index"])
    title = song["title_ko"]
    team = song.get("worship_team") or ""
    target_key = song["target_key"]
    sheet_key = sheet_meta.get("sheet_key") or target_key
    bpm = song["bpm"]
    meter = song.get("time_signature", "4/4")
    meter_note = str(song.get("meter_note") or sheet_meta.get("meter_note") or "").strip()
    song_form = song.get("song_form") or "I(4m) - V1 - C - O"
    capo_note = (song.get("capo_info") or {}).get("label", "No Capo")
    shift = semitone_distance(sheet_key, target_key)

    _, t_maj, _, _ = parse_key_signature(target_key)
    _, s_maj, _, _ = parse_key_signature(sheet_key)
    has_mismatch = (t_maj != s_maj) and (sheet_meta.get("key_mismatch_action") != "recalculate")

    font_meta = _load_font(22, bold=True)
    font_meter_warn = _load_font(20, bold=True)
    font_warn = _load_font(25, bold=True)
    font_hero_badge = _load_font(25, bold=True)
    font_hero = _load_font(35, bold=True)
    font_hero_sm = _load_font(30, bold=True)
    font_footer = _load_font(21, bold=False)
    font_footer_b = _load_font(21, bold=True)
    font_compact = _load_font(24, bold=True)

    y_cursor = SAFE_MARGIN
    footer_top = A4_H - SAFE_MARGIN

    if page_num == 1:
        # Zone 1: Small compact metadata bar at the very top
        team_part = f" ({team})" if team else ""
        capo_part = f" · {capo_note}" if show_capo else ""
        meter_disp = _format_meter_with_groove(song)
        meta_line = f"[{slot_idx:02d}] {title}{team_part}   ·   {target_key} Key{capo_part}   ·   {bpm} BPM ({meter_disp})"

        meter_pill_w = (_text_width(draw, f"[주의] {meter_note}", font_meter_warn) + 22) if meter_note else 0
        meta_max_w = CONTENT_W - (meter_pill_w + 16 if meter_note else 0)
        meta_lines = _wrap_text(draw, meta_line, font_meta, meta_max_w)
        top_strip_h = max(36, len(meta_lines) * 30)

        draw.line([(SAFE_MARGIN, y_cursor + top_strip_h + 6), (A4_W - SAFE_MARGIN, y_cursor + top_strip_h + 6)], fill=(226, 232, 240), width=2)
        my = y_cursor + 2
        for ml in meta_lines:
            draw.text((SAFE_MARGIN + 4, my), ml, font=font_meta, fill=(71, 85, 105))
            my += 30

        if meter_note:
            px = A4_W - SAFE_MARGIN - meter_pill_w
            py = y_cursor
            draw.rounded_rectangle([px, py, px + meter_pill_w, py + 32], radius=7, fill=(254, 226, 226), outline=(185, 28, 28), width=2)
            draw.text((px + 11, py + 3), f"[주의] {meter_note}", font=font_meter_warn, fill=(153, 27, 27))

        y_cursor += top_strip_h + 14

        # Optional high-contrast transpose warning
        if has_mismatch:
            capo_suffix = f" / 기타 {capo_note}" if show_capo else ""
            warn_text = f"[이조 연주] 악보: {sheet_key} Key → 실연: {target_key} Key ({shift:+d}{capo_suffix})"
            warn_lines = _wrap_text(draw, warn_text, font_warn, CONTENT_W - 36)
            warn_h = 18 + len(warn_lines) * 32
            draw.rounded_rectangle(
                [SAFE_MARGIN, y_cursor, A4_W - SAFE_MARGIN, y_cursor + warn_h],
                radius=10,
                fill=(254, 243, 199),
                outline=(180, 83, 9),
                width=3,
            )
            wy = y_cursor + 9
            for wl in warn_lines:
                draw.text((SAFE_MARGIN + 18, wy), wl, font=font_warn, fill=(153, 27, 27))
                wy += 32
            y_cursor += warn_h + 12

        # Zone 2: HERO Standalone Song-Progression Banner
        tr_mode = transition.get("transition_mode") if transition else None
        hero_text = format_song_form_visual(song_form, tr_mode, session_instruments=session_instruments)
        badge_w = 114
        hero_text_max_w = CONTENT_W - badge_w - 38
        hero_font = font_hero
        hero_lines = _wrap_text(draw, hero_text, hero_font, hero_text_max_w)
        line_h = 46
        if len(hero_lines) > 2:
            hero_font = font_hero_sm
            hero_lines = _wrap_text(draw, hero_text, hero_font, hero_text_max_w)
            line_h = 40
        hero_h = max(68, 24 + len(hero_lines) * line_h)

        draw.rounded_rectangle(
            [SAFE_MARGIN, y_cursor, A4_W - SAFE_MARGIN, y_cursor + hero_h],
            radius=12,
            fill=(239, 246, 255),
            outline=(30, 64, 175),
            width=4,
        )
        draw.rounded_rectangle(
            [SAFE_MARGIN, y_cursor, SAFE_MARGIN + badge_w, y_cursor + hero_h],
            radius=10,
            fill=(30, 58, 138),
        )
        bw = _text_width(draw, "곡 진행", font_hero_badge)
        draw.text(
            (SAFE_MARGIN + (badge_w - bw) // 2, y_cursor + (hero_h - 30) // 2 - 2),
            "곡 진행",
            font=font_hero_badge,
            fill=(255, 255, 255),
        )

        hy = y_cursor + (hero_h - len(hero_lines) * line_h) // 2 - 2
        for hl in hero_lines:
            draw.text((SAFE_MARGIN + badge_w + 20, hy), hl, font=hero_font, fill=(15, 23, 42))
            hy += line_h
        y_cursor += hero_h + 12

        # Zone 3: Pre-render measurement for Compact Bottom Footer
        conn_line = _format_transition_line(
            transition,
            str(song.get("ending_type", "pad_hold")),
            session_instruments=session_instruments,
            reprise_candidate=reprise_candidate if transition is None else None,
        )
        note_bits: list[str] = []
        if song.get("ensemble_note"):
            note_bits.append(str(song["ensemble_note"]))
        if song.get("caution_nuance"):
            note_bits.append(f"인도·보컬: {song['caution_nuance']}")
        note_line = f"합주·진행 메모: {' / '.join(note_bits)}" if note_bits else ""

        f1_wrap_w = CONTENT_W - (100 if pdf_page_label else 32)
        f1_lines = _wrap_text(draw, conn_line, font_footer_b, f1_wrap_w)
        f2_lines = _wrap_text(draw, note_line, font_footer, CONTENT_W - 32) if note_line else []
        footer_h = 18 + len(f1_lines) * 28 + (6 + len(f2_lines) * 27 if f2_lines else 0) + 10
        footer_top = A4_H - SAFE_MARGIN - footer_h

        draw.rounded_rectangle(
            [SAFE_MARGIN, footer_top, A4_W - SAFE_MARGIN, A4_H - SAFE_MARGIN],
            radius=10,
            fill=(248, 250, 252),
            outline=(203, 213, 225),
            width=2,
        )
        fy = footer_top + 10
        for fl in f1_lines:
            draw.text((SAFE_MARGIN + 16, fy), fl, font=font_footer_b, fill=(3, 105, 161) if transition else (30, 41, 59))
            fy += 28
        if f2_lines:
            fy += 4
            for fl in f2_lines:
                draw.text((SAFE_MARGIN + 16, fy), fl, font=font_footer, fill=(71, 85, 105))
                fy += 27
        if pdf_page_label:
            pw = _text_width(draw, pdf_page_label, font_footer)
            draw.text((A4_W - SAFE_MARGIN - pw - 14, footer_top + 10), pdf_page_label, font=font_footer, fill=(100, 116, 139))
    else:
        tr_mode = transition.get("transition_mode") if transition else None
        compact_form = format_song_form_visual(song_form, tr_mode, session_instruments=session_instruments)
        page_suffix = f"   [{pdf_page_label}]" if pdf_page_label else ""
        meter_disp = _format_meter_with_groove(song)
        compact_text = f"[{slot_idx:02d}] {title} ({page_num}/{total_pages})  ·  {target_key} Key  ·  {bpm} BPM ({meter_disp})  |  곡 진행: {compact_form}{page_suffix}"
        c_lines = _wrap_text(draw, compact_text, font_compact, CONTENT_W - 32)
        box_h = 20 + len(c_lines) * 32
        draw.rounded_rectangle(
            [SAFE_MARGIN, y_cursor, A4_W - SAFE_MARGIN, y_cursor + box_h],
            radius=8,
            fill=(239, 246, 255),
            outline=(30, 64, 175),
            width=2,
        )
        cy = y_cursor + 10
        for ln in c_lines:
            draw.text((SAFE_MARGIN + 16, cy), ln, font=font_compact, fill=(15, 23, 42))
            cy += 32
        y_cursor += box_h + 14

    # Middle Zone: Proportional fit of original sheet between top banners and bottom footer
    avail_w = CONTENT_W
    avail_h = max(200, (footer_top - 10) - y_cursor)

    if sheet_img_path and sheet_img_path.exists():
        orig = load_normalized_sheet(sheet_img_path)
        ow, oh = orig.size
        scale = min(avail_w / max(1, ow), avail_h / max(1, oh))
        new_w = max(1, int(ow * scale))
        new_h = max(1, int(oh * scale))
        resized = orig.resize((new_w, new_h), Image.Resampling.LANCZOS)
        paste_x = SAFE_MARGIN + (avail_w - new_w) // 2
        paste_y = y_cursor + max(0, (avail_h - new_h) // 2)
        canvas_img.paste(resized, (paste_x, paste_y))

        annotations = sheet_meta.get("score_annotations") or song.get("score_annotations") or []
        if annotations:
            _draw_score_annotations(draw, annotations, page_num, paste_x, paste_y, new_w, new_h)
    else:
        draw.rectangle(
            [SAFE_MARGIN, y_cursor, A4_W - SAFE_MARGIN, footer_top - 10],
            outline=(148, 163, 184),
            width=2,
        )
        ph_font = _load_font(32, bold=True)
        draw.text(
            (SAFE_MARGIN + 40, y_cursor + 60),
            f"악보 파일 미지정: {title} ({target_key} Key)",
            font=ph_font,
            fill=(100, 116, 139),
        )

    return canvas_img


def render_cover_cuesheet_page(
    setlist_plan: dict[str, Any],
    matched_by_slot: dict[int, dict[str, Any]],
    show_capo: bool = False,
    session_instruments: list[str] | None = None,
) -> Image.Image:
    """Render Page 1 Master Setlist Cue Sheet Cover in A4 format with prominent song-form lines."""
    cover = Image.new("RGB", (A4_W, A4_H), (255, 255, 255))
    draw = ImageDraw.Draw(cover)

    songs = setlist_plan.get("songs", [])
    compact_cards = len(songs) >= 5

    f_title = _load_font(42, bold=True)
    f_sub = _load_font(25, bold=False)
    f_sec = _load_font(28 if compact_cards else 29, bold=True)
    f_meta = _load_font(20 if compact_cards else 23, bold=True)
    f_form = _load_font(24 if compact_cards else 28, bold=True)
    f_row = _load_font(18 if compact_cards else 21, bold=False)

    y = SAFE_MARGIN
    header_h = 160 if compact_cards else 178
    draw.rounded_rectangle(
        [SAFE_MARGIN, y, A4_W - SAFE_MARGIN, y + header_h],
        radius=14,
        fill=(30, 41, 59),
    )
    svc_date = setlist_plan.get("service_date") or "미정"
    svc_label = _resolve_service_label(setlist_plan)
    season_label = _resolve_season_label(setlist_plan.get("church_season"))
    skill_label = _resolve_skill_label(setlist_plan.get("session_skill_tier"))
    theme = setlist_plan.get("theme_input") or "전체 예배"
    scripture = str(setlist_plan.get("sermon_scripture") or "").strip()

    meta_parts = [f"예배: {svc_label}", f"절기: {season_label}"]
    if scripture and scripture != "-":
        meta_parts.append(f"성경본문: {scripture}")
    meta_parts.append(f"세션: {skill_label}")

    draw.text((SAFE_MARGIN + 32, y + 20), f"찬양 콘티 총괄 큐시트 ({svc_date})", font=f_title, fill=(255, 255, 255))
    draw.text((SAFE_MARGIN + 32, y + (74 if compact_cards else 84)), "  |  ".join(meta_parts), font=f_sub, fill=(241, 245, 249))
    draw.text((SAFE_MARGIN + 32, y + (112 if compact_cards else 124)), f"주제: {theme}", font=f_sub, fill=(226, 232, 240))
    y += header_h + (18 if compact_cards else 26)

    draw.text((SAFE_MARGIN, y), "[Block A] 말씀 전 찬양 순서 · 곡 진행 가이드", font=f_sec, fill=(15, 23, 42))
    y += 38 if compact_cards else 44

    tr_by_slot = _index_transitions_by_slot(setlist_plan)
    post = setlist_plan.get("post_sermon") or {}
    reprise = post.get("reprise_candidate")
    b_h = 160 if compact_cards else 188
    lh1, lh2, lh3 = (25, 30, 23) if compact_cards else (30, 36, 28)
    pad_top, pad_bot, gap_card = (10, 10, 8) if compact_cards else (14, 14, 12)

    for song in songs:
        slot_idx = int(song["slot_index"])
        smeta = matched_by_slot.get(slot_idx, {})
        capo_label = (song.get("capo_info") or {}).get("label", "No Capo")
        sheet_key = smeta.get("sheet_key") or song["target_key"]
        mismatch_note = ""
        if parse_key_signature(sheet_key)[1] != parse_key_signature(song["target_key"])[1] and smeta.get("key_mismatch_action") != "recalculate":
            mismatch_note = f"  [이조: 악보 {sheet_key}→{song['target_key']}]"

        capo_part = f" ({capo_label})" if show_capo else ""
        meter_note = f"  [주의: {song['meter_note']}]" if song.get("meter_note") else ""
        meter_disp = _format_meter_with_groove(song)
        r1 = f"{slot_idx}. {song['title_ko']} ({song['worship_team']})   ·   {song['target_key']} Key{capo_part}   ·   {song['bpm']} BPM ({meter_disp}){mismatch_note}{meter_note}"

        tr = tr_by_slot.get(slot_idx)
        tr_mode = tr["transition_mode"] if tr else None
        r2 = f"곡 진행: {format_song_form_visual(song.get('song_form', ''), tr_mode, session_instruments=session_instruments)}"

        conn_str = _format_transition_line(
            tr,
            str(song.get("ending_type", "pad_hold")),
            session_instruments=session_instruments,
            reprise_candidate=None,
        )
        if song.get("ensemble_note"):
            r3 = f"{conn_str}  |  메모: {song['ensemble_note']}"
        else:
            r3 = conn_str

        l1 = _wrap_text(draw, r1, f_meta, CONTENT_W - 36)
        l2 = _wrap_text(draw, r2, f_form, CONTENT_W - 36)
        l3 = _wrap_text(draw, r3, f_row, CONTENT_W - 36)
        card_h = pad_top + len(l1) * lh1 + 4 + len(l2) * lh2 + 4 + len(l3) * lh3 + pad_bot

        if y + card_h > A4_H - SAFE_MARGIN - (b_h + 16):
            break

        draw.rounded_rectangle(
            [SAFE_MARGIN, y, A4_W - SAFE_MARGIN, y + card_h],
            radius=10,
            fill=(248, 250, 252),
            outline=(203, 213, 225),
            width=2,
        )
        cy = y + pad_top
        for ln in l1:
            draw.text((SAFE_MARGIN + 18, cy), ln, font=f_meta, fill=(71, 85, 105))
            cy += lh1
        cy += 3
        for ln in l2:
            draw.text((SAFE_MARGIN + 18, cy), ln, font=f_form, fill=(30, 58, 138))
            cy += lh2
        cy += 3
        for ln in l3:
            draw.text((SAFE_MARGIN + 18, cy), ln, font=f_row, fill=(51, 65, 85))
            cy += lh3
        y += card_h + gap_card

    # Block B summary box with realized chords in target key
    loop_key = (
        reprise["target_key"]
        if reprise and reprise.get("target_key")
        else (songs[-1]["target_key"] if songs else "G")
    )
    raw_loops = post.get("prayer_chord_loops", ["IV - V - iii - vi"])
    if len(raw_loops) == 2:
        loop_body = (
            f"기본 루프 ({loop_key} Key): {realize_roman_progression(raw_loops[0], loop_key)}"
            f"  |  마무리/대체: {realize_roman_progression(raw_loops[1], loop_key)}"
        )
    else:
        realized_loops = "  |  ".join(realize_roman_progression(lp, loop_key) for lp in raw_loops)
        loop_body = f"기도회 코드 루프 ({loop_key} Key): {realized_loops}"

    b_y = max(y + 10, A4_H - SAFE_MARGIN - b_h)
    if A4_H - SAFE_MARGIN - b_y >= 120:
        draw.rounded_rectangle(
            [SAFE_MARGIN, b_y, A4_W - SAFE_MARGIN, b_y + min(b_h, A4_H - SAFE_MARGIN - b_y)],
            radius=10,
            fill=(239, 246, 255),
            outline=(59, 130, 246),
            width=2,
        )
        draw.text((SAFE_MARGIN + 22, b_y + 12), "[Block B] 말씀 후 결단 · 기도회 안내", font=f_sec, fill=(30, 64, 175))
        rep_form = format_song_form_visual(reprise.get("song_form", ""), session_instruments=session_instruments) if reprise else ""
        rep_str = f"추천 Reprise: {reprise['title_ko']} ({reprise['target_key']} Key)  |  곡 진행: {rep_form}" if reprise else "설교 결말에 맞춘 기도회 반주 진행"
        ry_cursor = b_y + 52
        for ln in _wrap_text(draw, rep_str, f_meta, CONTENT_W - 44)[:2]:
            draw.text((SAFE_MARGIN + 22, ry_cursor), ln, font=f_meta, fill=(15, 23, 42))
            ry_cursor += lh1 + 2

        ly_cursor = max(ry_cursor + 4, b_y + (94 if compact_cards else 112))
        for ln in _wrap_text(draw, loop_body, f_meta, CONTENT_W - 44)[:2]:
            draw.text((SAFE_MARGIN + 22, ly_cursor), ln, font=f_meta, fill=(30, 64, 175))
            ly_cursor += lh1

    return cover


def _pil_to_a4_pdf_reader(pil_img: Image.Image) -> PdfReader:
    buf_img = io.BytesIO()
    pil_img.save(buf_img, format="PNG")
    buf_img.seek(0)

    buf_pdf = io.BytesIO()
    c = canvas.Canvas(buf_pdf, pagesize=A4)
    page_w, page_h = A4
    c.drawImage(ImageReader(buf_img), 0, 0, width=page_w, height=page_h)
    c.showPage()
    c.save()
    buf_pdf.seek(0)
    return PdfReader(buf_pdf)


def build_kakao_notice(
    setlist_plan: dict[str, Any],
    matched_by_slot: dict[int, dict[str, Any]],
    show_capo: bool = False,
    session_instruments: list[str] | None = None,
) -> str:
    svc_date = setlist_plan.get("service_date") or "이번 주"
    svc_label = _resolve_service_label(setlist_plan)
    season_label = _resolve_season_label(setlist_plan.get("church_season"))
    theme = setlist_plan.get("theme_input") or ""
    lines = [
        f"[찬양팀 콘티 공지] {svc_date} ({svc_label})",
        f"주제: {theme} | 절기: {season_label}",
        "",
    ]
    transitions = _index_transitions_by_slot(setlist_plan)
    songs = setlist_plan.get("songs", [])
    for song in songs:
        idx = int(song["slot_index"])
        orig_key = song.get("original_key") or song["target_key"]
        target_key = song["target_key"]
        shift = int(song.get("key_shift", 0))
        capo_label = (song.get("capo_info") or {}).get("label", "No Capo")
        capo_part = f" [{capo_label}]" if show_capo else ""
        meter = _format_meter_with_groove(song)
        est_part = f" (~{float(song['estimated_minutes']):g}분)" if song.get("estimated_minutes") else ""
        meter_part = f" | ⚠ {song['meter_note']}" if song.get("meter_note") else ""
        smeta = matched_by_slot.get(idx, {})
        sheet_key = smeta.get("sheet_key") or target_key

        lines.append(
            f"{idx}. {song['title_ko']} ({song['worship_team']}) - {target_key} Key{capo_part} | {song['bpm']} BPM ({meter}){est_part}{meter_part}"
        )
        tr = transitions.get(idx)
        tr_mode = tr["transition_mode"] if tr else None
        lines.append(f"   곡 진행: {format_song_form_visual(song.get('song_form', ''), tr_mode, session_instruments=session_instruments)}")
        if parse_key_signature(sheet_key)[1] != parse_key_signature(target_key)[1] and smeta.get("key_mismatch_action") != "recalculate":
            lines.append(f"   ※ 악보 표기({sheet_key} Key)와 실연({target_key} Key) 다름 — 이조 확인")

        yt_link = _format_youtube_deeplink(song.get("youtube_url"), song.get("youtube_start_sec"))
        if yt_link:
            if parse_key_signature(orig_key)[1] != parse_key_signature(target_key)[1]:
                lines.append(f"   [주의: 유튜브 원곡 {orig_key}키 / 우리팀 {target_key}키({shift:+d})] {yt_link}")
            else:
                lines.append(f"   참고 영상: {yt_link}")

        if tr:
            lines.append(f"   -> {_format_transition_line(tr, str(song.get('ending_type', 'pad_hold')), session_instruments=session_instruments)}")
        if song.get("ensemble_note"):
            lines.append(f"   메모: {song['ensemble_note']}")

    post = setlist_plan.get("post_sermon") or {}
    if post.get("reprise_candidate"):
        rep = post["reprise_candidate"]
        lines.append("")
        lines.append(f"[설교 후 결단] {rep['title_ko']} Reprise ({rep['target_key']} Key) | {format_song_form_visual(rep.get('song_form', ''), session_instruments=session_instruments)}")
        loop_key = rep["target_key"]
        raw_loops = post.get("prayer_chord_loops", [])
        if raw_loops:
            realized = " | ".join(realize_roman_progression(lp, loop_key) for lp in raw_loops)
            lines.append(f"   기도회 코드 ({loop_key} Key): {realized}")
    return "\n".join(lines)


def build_leader_cuesheet_md(
    setlist_plan: dict[str, Any],
    candidates_data: dict[str, Any] | None = None,
    session_instruments: list[str] | None = None,
) -> str:
    plan_b_map: dict[int, list[dict[str, Any]]] = {}
    if candidates_data:
        for sl in candidates_data.get("pre_sermon", []):
            plan_b_map[int(sl["slot_index"])] = sl.get("plan_b", [])

    svc_label = _resolve_service_label(setlist_plan)
    season_label = _resolve_season_label(setlist_plan.get("church_season"))
    scripture = str(setlist_plan.get("sermon_scripture") or "").strip()
    scripture_disp = f" (`{scripture}`)" if scripture and scripture != "-" else ""
    songs = setlist_plan.get("songs", [])
    sum_min = sum(float(s.get("estimated_minutes") or 4.0) for s in songs)
    dur_target = setlist_plan.get("duration_min")
    dur_disp = f"찬양 약 {sum_min:.1f}분 (목표 {dur_target}분)" if dur_target else f"찬양 약 {sum_min:.1f}분"

    lines = [
        f"# 인도자용 상세 큐시트 ({setlist_plan.get('service_date', '')} / {svc_label})",
        f"- **절기**: `{season_label}`",
        f"- **설교/예배 주제**: {setlist_plan.get('theme_input', '')}{scripture_disp}",
        f"- **예상 소요시간**: {dur_disp}",
        "",
        "## Block A: 말씀 전 찬양",
    ]
    transitions = _index_transitions_by_slot(setlist_plan)

    for song in songs:
        idx = int(song["slot_index"])
        meter_str = _format_meter_with_groove(song)
        if song.get("meter_note"):
            meter_str += f" — ⚠ {song['meter_note']}"
        lines.append(f"### {idx}. {song['title_ko']} (`{song['target_key']} Key`, {song['bpm']} BPM, {meter_str})")
        tr = transitions.get(idx)
        tr_mode = tr["transition_mode"] if tr else None
        if song.get("ending_type", "pad_hold") == "pad_hold" and not _has_synth_pad(session_instruments):
            end_kr = "피아노 코드 여운 (말씀/기도 대기)"
        else:
            end_kr = ENDING_TYPE_LABELS.get(str(song.get("ending_type", "pad_hold")), song.get("ending_type", "pad_hold"))
        end_field = f" | **전환**: `{tr['transition_mode']}`" if tr else f" | **엔딩**: {end_kr}"
        lines.append(f"- **곡 진행**: `{format_song_form_visual(song.get('song_form', ''), tr_mode, session_instruments=session_instruments)}`{end_field}")
        lines.append(f"- **신학적 핵심 / 선정 이유**: {song.get('theological_core', '')}")
        if song.get("ensemble_note"):
            lines.append(f"- **합주·진행 메모**: {song['ensemble_note']}")
        if song.get("caution_nuance"):
            lines.append(f"- **인도·보컬 주의점**: {song['caution_nuance']}")
        if song.get("scripture_refs"):
            lines.append(f"- **연관 성경**: {', '.join(song['scripture_refs'])}")
        if tr:
            cue_disp = _format_transition_line(tr, str(song.get("ending_type", "pad_hold")), session_instruments=session_instruments)
            lines.append(f"- **다음 곡 전조 팁**: `{tr['bridge_chords']}` — {tr['harmony_tip']} ({cue_disp})")
        pb = plan_b_map.get(idx, [])
        if pb:
            pb_str = ", ".join(f"{b['title_ko']}({b['target_key']})" for b in pb)
            lines.append(f"- **대체 후보(Plan B)**: {pb_str}")
        lines.append("")

    post = setlist_plan.get("post_sermon") or {}
    lines.append("## Block B: 말씀 후 결단 · 기도회")
    rep = post.get("reprise_candidate")
    loop_key = rep["target_key"] if rep and rep.get("target_key") else (songs[-1]["target_key"] if songs else "G")
    if rep:
        lines.append(f"- **Reprise 추천**: {rep['title_ko']} (`{rep['target_key']} Key`) — `{format_song_form_visual(rep.get('song_form', ''), session_instruments=session_instruments)}`")
    raw_loops = post.get("prayer_chord_loops", [])
    realized = " | ".join(realize_roman_progression(lp, loop_key) for lp in raw_loops)
    lines.append(f"- **기도회 코드 루프 ({loop_key} Key)**: {realized}")
    return "\n".join(lines)


def _sanitize_broadcast_note(note: str) -> str:
    """Strip musician-only chord symbols & form shorthand from broadcast PPT operator hints."""
    if not note:
        return ""
    clean_segs: list[str] = []
    for raw_seg in note.split("|"):
        seg = raw_seg.strip()
        if not seg:
            continue
        # Drop pure harmonic/chord/count-in segments meant for band players
        if re.search(
            r"(?:전조|패드\s*홀드|카운트\s*인|해결\s*\+|C/D|[A-G][#b]?(?:m|maj|add|sus|dim)?\d*/[A-G]|[A-G][#b]?-[A-G][#b]?|(?:^|\s)[A-G][#b]?\s*코드)",
            seg,
            re.IGNORECASE,
        ):
            continue
        seg = re.sub(r"V(\d+)\(x(\d+)\)", r"\1절(\2번)", seg, flags=re.IGNORECASE)
        seg = re.sub(r"C\(x(\d+)\)", r"후렴(\1번)", seg, flags=re.IGNORECASE)
        seg = seg.replace("Pre-Cho", "프리코러스").replace("Bridge", "브릿지").replace("Chorus", "후렴").replace("Verse", "절")
        clean_segs.append(seg)
    return " / ".join(clean_segs)


def build_broadcast_cue(setlist_plan: dict[str, Any]) -> str:
    """Generate PPT/subtitle operator cue sheet using Korean section labels + <=15-char incipits."""
    svc_label = _resolve_service_label(setlist_plan)
    lines = [
        f"[방송실 자막/PPT 큐시트] {setlist_plan.get('service_date', '')} ({svc_label})",
        "※ 음악 기호 대신 아래 한글 송폼 순서대로 자막을 넘겨주세요. (동명이곡 확인용 첫 구절 병기)",
        "",
    ]
    transitions = _index_transitions_by_slot(setlist_plan)
    songs = setlist_plan.get("songs", [])
    for i, song in enumerate(songs):
        idx = int(song["slot_index"])
        tr = transitions.get(idx)
        tr_mode = tr["transition_mode"] if tr else None
        next_title = songs[i + 1]["title_ko"] if i + 1 < len(songs) else None
        incipit = song.get("first_line_incipit") or "(첫 구절 미등록)"
        chorus_hint = f' / 후렴 시작: "{song["chorus_incipit"]}"' if song.get("chorus_incipit") else ""
        meter_hint = f"  [주의: {song['meter_note']}]" if song.get("meter_note") else ""
        kr_flow = song_form_to_korean_slides(song.get("song_form", ""), next_song_title=next_title, transition_mode=tr_mode)
        if tr_mode == "pad_break" or (i == len(songs) - 1 and song.get("ending_type") == "pad_hold"):
            kr_flow += " -> ★ 자막 내림(기도/멘트 대기)"
        elif tr_mode == "rhythm_reset" and "정박 엔딩" not in kr_flow:
            kr_flow += " -> ★ 정박 엔딩 후 카운트에 다음 곡 첫 자막 대기"
        lines.append(f'{idx}. {song["title_ko"]} [첫 줄 확인: "{incipit}"{chorus_hint}]{meter_hint}')
        lines.append(f"   진행 순서: {kr_flow}")
        op_note = _sanitize_broadcast_note(str(song.get("ensemble_note") or ""))
        if op_note:
            lines.append(f"   진행 참고: {op_note}")
    post = setlist_plan.get("post_sermon") or {}
    if post.get("reprise_candidate"):
        rep = post["reprise_candidate"]
        rep_raw_form = str(rep.get("song_form") or "")
        rep_flow = song_form_to_korean_slides(rep_raw_form)
        if rep_raw_form.lstrip().startswith(("[C", "C(", "I(2m) - [C", "I - [C")):
            rep_flow += " (1절 생략 · 후렴부터 바로 자막 송출)"
        if "vamp" in rep_raw_form.lower() or rep.get("ending_type") == "pad_hold":
            rep_flow += " -> ★ 자막 내림(결단 기도)"
        chorus_hint = f' / 후렴 시작: "{rep["chorus_incipit"]}"' if rep.get("chorus_incipit") else ""
        lines.append("")
        lines.append(f'[말씀 후 결단 Reprise] {rep["title_ko"]} [첫 줄 확인: "{rep.get("first_line_incipit") or ""}"{chorus_hint}]')
        lines.append(f"   진행 순서: {rep_flow}")
    return "\n".join(lines)


def _compute_diff_notice(old_snapshot: dict[str, Any] | None, new_plan: dict[str, Any], target_slot: int | None) -> str:
    if not old_snapshot:
        return "[리허설 변경 공지] 초기 콘티가 생성되었습니다."
    old_by_slot = {int(s["slot_index"]): s for s in old_snapshot.get("songs", [])}
    new_by_slot = {int(s["slot_index"]): s for s in new_plan.get("songs", [])}

    diffs: list[str] = []
    slots_to_check = [target_slot] if target_slot is not None else sorted(set(old_by_slot) | set(new_by_slot))
    for s_idx in slots_to_check:
        o = old_by_slot.get(s_idx)
        n = new_by_slot.get(s_idx)
        if o and not n:
            diffs.append(f"- {s_idx}번 곡 제외: {o['title_ko']}")
        elif n and not o:
            diffs.append(f"- {s_idx}번 곡 추가: {n['title_ko']} ({n['target_key']} Key)")
        elif o and n:
            changes: list[str] = []
            if o["song_slug"] != n["song_slug"]:
                changes.append(f"곡 교체: {o['title_ko']}({o['target_key']}) -> {n['title_ko']}({n['target_key']})")
            else:
                if o["target_key"] != n["target_key"]:
                    changes.append(f"Key 변경: {o['target_key']} -> {n['target_key']}")
                if o.get("song_form") != n.get("song_form"):
                    changes.append(f"곡 진행 변경: {n.get('song_form')}")
                if o.get("bpm") != n.get("bpm"):
                    changes.append(f"BPM 변경: {o['bpm']} -> {n['bpm']}")
            if changes:
                diffs.append(f"- {s_idx}번 ({n['title_ko']}): " + " / ".join(changes))

    if not diffs:
        diffs.append("- 세부 연주 메모/연결 코드가 업데이트되었습니다.")
    header = f"[리허설 변경 공지] {new_plan.get('service_date', '')} ({_resolve_service_label(new_plan)})"
    return "\n".join([header, *diffs])


def generate_setlist_package(
    setlist_plan: dict[str, Any],
    matched_sheets: dict[str, Any],
    output_dir: Path,
    candidates_data: dict[str, Any] | None = None,
    update_slot: int | None = None,
    show_capo: bool | None = None,
    user_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate the 4 deliverables (Unified PDF w/ 1p Cover & Outlines, PNGs, Kakao+Markdown Cue, PPT Cue)."""
    for sm in matched_sheets.get("sheets", []):
        if sm.get("lyrics_theology_ok") is False or sm.get("match_status") == "rejected_theology":
            raise ValueError(
                f"Theological/lyric check failed (lyrics_theology_ok=False) for slot {sm.get('slot_index')}; refusing to stamp sheet"
            )

    cfg = user_config if user_config is not None else load_user_config()
    eff_show_capo = bool(cfg.get("show_capo", False)) if show_capo is None else bool(show_capo)
    inst_list = (
        setlist_plan.get("session_instruments")
        if setlist_plan.get("session_instruments") is not None
        else cfg.get("session_instruments")
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    sheets_out_dir = output_dir / "sheets"
    sheets_out_dir.mkdir(parents=True, exist_ok=True)

    snapshot_path = output_dir / "setlist_snapshot.json"
    old_snapshot = json.loads(snapshot_path.read_text(encoding="utf-8")) if snapshot_path.exists() else None

    matched_by_slot = {int(s["slot_index"]): s for s in matched_sheets.get("sheets", [])}
    songs = setlist_plan.get("songs", [])
    tr_by_slot = _index_transitions_by_slot(setlist_plan)
    post = setlist_plan.get("post_sermon") or {}
    rep_cand = post.get("reprise_candidate")

    # 1. Cover page PNG
    cover_img = render_cover_cuesheet_page(
        setlist_plan,
        matched_by_slot,
        show_capo=eff_show_capo,
        session_instruments=inst_list,
    )
    cover_png = sheets_out_dir / "00_총괄_큐시트_표지.png"
    cover_img.save(cover_png, format="PNG")

    # Compute total PDF pages ahead of stamping so each page shows clean X/Y numbering
    slot_raw_pages: dict[int, list[Path]] = {}
    total_pdf_pages = 1
    for song in songs:
        slot_idx = int(song["slot_index"])
        smeta = matched_by_slot.get(slot_idx, {})
        raw_pages = [Path(p) for p in smeta.get("rasterized_pages", []) if Path(p).exists()]
        slot_raw_pages[slot_idx] = raw_pages
        total_pdf_pages += max(1, len(raw_pages))

    # 2. Stamped sheet PNGs per slot (support partial --update-slot refresh of target AND outgoing adjacent slot k-1)
    slots_to_refresh = {update_slot, update_slot - 1} if update_slot is not None else set()
    song_pages: list[tuple[dict[str, Any], list[Path]]] = []
    individual_pngs: list[str] = [str(cover_png)]
    pdf_page_counter = 2

    for song in songs:
        slot_idx = int(song["slot_index"])
        smeta = matched_by_slot.get(slot_idx, {})
        raw_pages = slot_raw_pages[slot_idx]
        total_pages = max(1, len(raw_pages))
        safe_t = _safe_filename(song["title_ko"])
        safe_k = _safe_filename(parse_key_signature(song["target_key"])[1])
        transition = tr_by_slot.get(slot_idx)

        will_rerender = (update_slot is None) or (slot_idx in slots_to_refresh)
        if will_rerender:
            for old_png in sheets_out_dir.glob(f"{slot_idx:02d}_*.png"):
                try:
                    old_png.unlink()
                except OSError:
                    pass

        rendered_paths: list[Path] = []
        for p_idx in range(1, total_pages + 1):
            out_png = sheets_out_dir / f"{slot_idx:02d}_{safe_t}_{safe_k}_p{p_idx}.png"
            should_reuse = (not will_rerender) and out_png.exists()
            if not should_reuse:
                src_page = raw_pages[p_idx - 1] if raw_pages else None
                stamped = render_stamped_sheet_page(
                    sheet_img_path=src_page,
                    song=song,
                    sheet_meta=smeta,
                    transition=transition,
                    page_num=p_idx,
                    total_pages=total_pages,
                    show_capo=eff_show_capo,
                    session_instruments=inst_list,
                    reprise_candidate=rep_cand,
                    pdf_page_label=f"{pdf_page_counter}/{total_pdf_pages}",
                )
                stamped.save(out_png, format="PNG")
            rendered_paths.append(out_png)
            individual_pngs.append(str(out_png))
            pdf_page_counter += 1
        song_pages.append((song, rendered_paths))

    # 3. Merge unified A4 PDF with cover (page 0) and song bookmarks (Outline)
    svc_date = _safe_filename(str(setlist_plan.get("service_date") or "setlist")) or "setlist"
    svc_type = _safe_filename(setlist_plan.get("service_type") or "worship") or "worship"
    pdf_path = output_dir / f"{svc_date}_{svc_type}_콘티악보집.pdf"
    if not pdf_path.resolve().is_relative_to(output_dir.resolve()):
        raise ValueError(f"Output PDF path escapes output directory: {pdf_path}")

    writer = PdfWriter()
    cover_reader = _pil_to_a4_pdf_reader(cover_img)
    writer.add_page(cover_reader.pages[0])
    writer.add_outline_item(title="00. 콘티 총괄 큐시트", page_number=0)

    current_pdf_page = 1
    for song, paths in song_pages:
        first_page_idx = current_pdf_page
        for p in paths:
            with Image.open(p) as im:
                p_reader = _pil_to_a4_pdf_reader(im.convert("RGB"))
                writer.add_page(p_reader.pages[0])
            current_pdf_page += 1
        writer.add_outline_item(
            title=f"{int(song['slot_index']):02d}. {song['title_ko']} ({song['target_key']})",
            page_number=first_page_idx,
        )

    with pdf_path.open("wb") as fp:
        writer.write(fp)

    # 4. KakaoTalk notice, Leader markdown cuesheet, Broadcast PPT cue sheet
    kakao_path = output_dir / "kakao_notice.txt"
    kakao_path.write_text(
        build_kakao_notice(setlist_plan, matched_by_slot, show_capo=eff_show_capo, session_instruments=inst_list),
        encoding="utf-8",
    )

    leader_md_path = output_dir / "leader_cuesheet.md"
    leader_md_path.write_text(
        build_leader_cuesheet_md(setlist_plan, candidates_data, session_instruments=inst_list),
        encoding="utf-8",
    )

    broadcast_path = output_dir / "broadcast_cue.txt"
    broadcast_path.write_text(build_broadcast_cue(setlist_plan), encoding="utf-8")

    diff_path = None
    if update_slot is not None or old_snapshot is not None:
        diff_path = output_dir / "kakao_diff_notice.txt"
        diff_path.write_text(_compute_diff_notice(old_snapshot, setlist_plan, update_slot), encoding="utf-8")

    snapshot_path.write_text(json_dumps_ko(setlist_plan), encoding="utf-8")

    return {
        "pdf_path": str(pdf_path),
        "total_pdf_pages": current_pdf_page,
        "individual_pngs": individual_pngs,
        "kakao_notice_path": str(kakao_path),
        "leader_cuesheet_path": str(leader_md_path),
        "broadcast_cue_path": str(broadcast_path),
        "kakao_diff_notice_path": str(diff_path) if diff_path else None,
        "snapshot_path": str(snapshot_path),
    }


def main() -> None:
    configure_utf8_stdio()
    parser = argparse.ArgumentParser(description="Stamp song forms on sheets and output 4 setlist deliverables")
    parser.add_argument("--setlist", type=Path, required=True)
    parser.add_argument("--sheets", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--update-slot", type=int, default=None, help="Re-stamp this slot (+ prior transition) and emit Diff notice")
    parser.add_argument("--show-capo", action="store_true", default=None, help="Include guitar capo labels on banners and notices")
    parser.add_argument("--no-show-capo", dest="show_capo", action="store_false")
    parser.add_argument("--allow-unverified", action="store_true", help="Allow placeholder/unverified sheets (CLI safety override)")
    args = parser.parse_args()

    plan = json.loads(args.setlist.read_text(encoding="utf-8"))
    matched = json.loads(args.sheets.read_text(encoding="utf-8"))
    cands = json.loads(args.candidates.read_text(encoding="utf-8")) if args.candidates and args.candidates.exists() else None
    cfg = load_user_config()

    if not args.allow_unverified:
        unverified = [
            s.get("slot_index")
            for s in matched.get("sheets", [])
            if s.get("source_file") and not s.get("vision_verified")
        ]
        if unverified:
            raise SystemExit(
                f"Slots {unverified} have local sheets not yet verified with read_file + find_sheets.py --vision-file. Refusing to stamp."
            )

    out_dir = args.output_dir
    if out_dir is None:
        default_out = cfg.get("default_output_dir") or "output/setlist"
        out_dir = resolve_workspace_path(default_out)

    result = generate_setlist_package(
        setlist_plan=plan,
        matched_sheets=matched,
        output_dir=out_dir,
        candidates_data=cands,
        update_slot=args.update_slot,
        show_capo=args.show_capo,
        user_config=cfg,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
