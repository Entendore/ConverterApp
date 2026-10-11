"""
Enhanced Universal File Format Converter — Kivy Application
============================================================
Audio:  WAV ↔ MP3 ↔ OGG ↔ FLAC ↔ AAC           (via FFmpeg)
Video:  MP4 ↔ MKV ↔ WEBM ↔ AVI ↔ MOV           (via FFmpeg)
Text:   TXT ↔ PDF ↔ MD ↔ HTML                   (via fpdf2 / pypdf / built-in)
Image:  PNG ↔ JPG ↔ BMP ↔ GIF ↔ WEBP ↔ TIFF    (via Pillow)
Data:   JSON ↔ CSV ↔ XML ↔ YAML                 (via built-in / PyYAML)

New Features:
  - Supported Conversions reference screen (94 total paths)
  - Quality / bitrate settings for audio, video & image
  - Batch conversion (multiple files at once)
  - Conversion history with persistence
  - Drag-and-drop hint in log

Requirements:
    pip install kivy fpdf2 pypdf Pillow PyYAML
    FFmpeg must be installed on your system.
"""

import os
import re
import sys
import json
import csv
import subprocess
import threading
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime
from itertools import permutations

from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.spinner import Spinner
from kivy.uix.popup import Popup
from kivy.uix.filechooser import FileChooserListView
from kivy.uix.scrollview import ScrollView
from kivy.uix.slider import Slider
from kivy.uix.switch import Switch
from kivy.core.window import Window
from kivy.clock import Clock
from kivy.lang import Builder
from kivy.metrics import dp, sp

# ─── Format Definitions ──────────────────────────────────────────

AUDIO_FORMATS = ["wav", "mp3", "ogg", "flac", "aac"]
VIDEO_FORMATS = ["mp4", "mkv", "webm", "avi", "mov"]
TEXT_FORMATS = ["pdf", "txt", "md", "html"]
IMAGE_FORMATS = ["png", "jpg", "bmp", "gif", "webp", "tiff"]
DATA_FORMATS = ["json", "csv", "xml", "yaml"]

ALL_FORMATS = AUDIO_FORMATS + VIDEO_FORMATS + TEXT_FORMATS + IMAGE_FORMATS + DATA_FORMATS

CATEGORY_POOLS = {
    "audio": AUDIO_FORMATS,
    "video": VIDEO_FORMATS,
    "text": TEXT_FORMATS,
    "image": IMAGE_FORMATS,
    "data": DATA_FORMATS,
}

CATEGORY_ICONS = {
    "audio": "🎵",
    "video": "🎬",
    "text": "📄",
    "image": "🖼️",
    "data": "📊",
}

HISTORY_FILE = Path.home() / ".converter_history.json"


def get_category(ext):
    ext = ext.lower().lstrip(".")
    for cat, pool in CATEGORY_POOLS.items():
        if ext in pool:
            return cat
    return None


def get_compatible_formats(input_ext):
    cat = get_category(input_ext)
    pool = CATEGORY_POOLS.get(cat, [])
    return [f.upper() for f in pool if f != input_ext]


def _sanitize_pdf_text(text):
    """Gracefully maps common Unicode characters to ASCII/Latin-1 equivalents."""
    unicode_map = {
        '\u2022': '-',
        '\u2023': '>',
        '\u2013': '-',
        '\u2014': '--',
        '\u2018': "'",
        '\u2019': "'",
        '\u201c': '"',
        '\u201d': '"',
        '\u2026': '...',
    }
    for uni_char, replacement in unicode_map.items():
        text = text.replace(uni_char, replacement)
    return text.encode("latin-1", errors="replace").decode("latin-1")


def human_size(n):
    for u in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"


def _build_conversion_list():
    """Build the full list of all supported conversions for the reference screen."""
    all_convs = []
    for cat, pool in CATEGORY_POOLS.items():
        for src, dst in permutations(pool, 2):
            all_convs.append((cat, src.upper(), dst.upper()))
    all_convs.sort(key=lambda x: (x[0], x[1], x[2]))
    return all_convs


ALL_CONVERSIONS = _build_conversion_list()


# ─── Markdown ↔ HTML Helpers ────────────────────────────────────

def md_to_html(md_text):
    """Convert basic Markdown to HTML."""
    lines = md_text.split("\n")
    html_parts = ['<!DOCTYPE html>', '<html><head>',
                  '<meta charset="utf-8">',
                  '<style>',
                  'body{font-family:sans-serif;max-width:800px;margin:40px auto;'
                  'padding:0 20px;color:#222;line-height:1.7;}',
                  'code{background:#f4f4f4;padding:2px 6px;border-radius:3px;'
                  'font-size:0.9em;}',
                  'pre{background:#f4f4f4;padding:16px;border-radius:6px;'
                  'overflow-x:auto;}',
                  'blockquote{border-left:4px solid #ddd;margin:0;'
                  'padding-left:16px;color:#555;}',
                  'table{border-collapse:collapse;width:100%;}',
                  'th,td{border:1px solid #ddd;padding:8px 12px;text-align:left;}',
                  'th{background:#f4f4f4;}',
                  'img{max-width:100%;}',
                  '</style></head><body>']

    in_code_block = False
    in_list = False
    in_table = False
    code_buf = []

    i = 0
    while i < len(lines):
        line = lines[i]

        if line.strip().startswith("```"):
            if not in_code_block:
                if in_list:
                    html_parts.append("</ul>")
                    in_list = False
                in_code_block = True
                code_buf = []
            else:
                in_code_block = False
                code_content = "\n".join(code_buf)
                html_parts.append(f"<pre><code>{_esc(code_content)}</code></pre>")
            i += 1
            continue

        if in_code_block:
            code_buf.append(line)
            i += 1
            continue

        if "|" in line and line.strip().startswith("|"):
            if not in_table:
                if in_list:
                    html_parts.append("</ul>")
                    in_list = False
                html_parts.append("<table>")
                in_table = True
            row_cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if all(set(c.strip()) <= {"-", ":", " "} for c in row_cells):
                i += 1
                continue
            html_parts.append("<tr>")
            for cell in row_cells:
                html_parts.append(f"<td>{_inline(cell)}</td>")
            html_parts.append("</tr>")
            i += 1
            if i >= len(lines) or "|" not in lines[i]:
                html_parts.append("</table>")
                in_table = False
            continue
        else:
            if in_table:
                html_parts.append("</table>")
                in_table = False

        hm = re.match(r'^(#{1,6})\s+(.+)$', line)
        if hm:
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            lvl = len(hm.group(1))
            html_parts.append(f"<h{lvl}>{_inline(hm.group(2))}</h{lvl}>")
            i += 1
            continue

        if re.match(r'^(\*{3,}|-{3,}|_{3,})\s*$', line.strip()):
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            html_parts.append("<hr>")
            i += 1
            continue

        if line.strip().startswith(">"):
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            quote_text = re.sub(r'^>\s?', '', line)
            html_parts.append(f"<blockquote><p>{_inline(quote_text)}</p></blockquote>")
            i += 1
            continue

        lm = re.match(r'^[\s]*[-*+]\s+(.+)$', line)
        if lm:
            if not in_list:
                html_parts.append("<ul>")
                in_list = True
            html_parts.append(f"<li>{_inline(lm.group(1))}</li>")
            i += 1
            continue
        else:
            if in_list:
                html_parts.append("</ul>")
                in_list = False

        olm = re.match(r'^[\s]*\d+\.\s+(.+)$', line)
        if olm:
            if not in_list:
                html_parts.append("<ol>")
                in_list = True
            html_parts.append(f"<li>{_inline(olm.group(1))}</li>")
            i += 1
            continue
        else:
            if in_list:
                html_parts.append("</ul>")
                in_list = False

        if not line.strip():
            i += 1
            continue

        html_parts.append(f"<p>{_inline(line)}</p>")
        i += 1

    if in_list:
        html_parts.append("</ul>")
    if in_table:
        html_parts.append("</table>")

    html_parts.append('</body></html>')
    return "\n".join(html_parts)


def _esc(text):
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def _inline(text):
    text = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', r'<img src="\2" alt="\1">', text)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)
    text = re.sub(r'\*\*\*(.+?)\*\*\*', r'<strong><em>\1</em></strong>', text)
    text = re.sub(r'___(.+?)___', r'<strong><em>\1</em></strong>', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'__(.+?)__', r'<strong>\1</strong>', text)
    text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
    text = re.sub(r'_(.+?)_', r'<em>\1</em>', text)
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    text = re.sub(r'~~(.+?)~~', r'<del>\1</del>', text)
    return text


def html_to_md(html_text):
    text = html_text
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
    for lvl in range(1, 7):
        hashes = "#" * lvl
        text = re.sub(rf'<h{lvl}[^>]*>(.*?)</h{lvl}>', rf'\n{hashes} \1\n',
                      text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<p[^>]*>(.*?)</p>', r'\n\1\n', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<br\s*/?\s*>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<hr\s*/?\s*>', '\n---\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<blockquote[^>]*>(.*?)</blockquote>', r'\n> \1\n',
                  text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<li[^>]*>(.*?)</li>', r'- \1\n', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'</?[ou]l[^>]*>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<th[^>]*>(.*?)</th>', r'| \1 ', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<td[^>]*>(.*?)</td>', r'| \1 ', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<tr[^>]*>(.*?)</tr>', r'\1|\n', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'</?table[^>]*>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<strong[^>]*>(.*?)</strong>', r'**\1**', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<b[^>]*>(.*?)</b>', r'**\1**', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<em[^>]*>(.*?)</em>', r'*\1*', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<i[^>]*>(.*?)</i>', r'*\1*', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<code[^>]*>(.*?)</code>', r'`\1`', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<del[^>]*>(.*?)</del>', r'~~\1~~', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', r'[\2](\1)', text,
                  flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<img[^>]*src="([^"]*)"[^>]*alt="([^"]*)"[^>]*/?\s*>',
                  r'![\2](\1)', text, flags=re.IGNORECASE)
    text = re.sub(r'<img[^>]*src="([^"]*)"[^>]*/?\s*>', r'![](\1)', text, flags=re.IGNORECASE)
    text = re.sub(r'<[^>]+>', '', text)
    for entity, char in [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                         ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")]:
        text = text.replace(entity, char)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]+$', '', text, flags=re.MULTILINE)
    return text.strip()


def html_to_plain_text(html_text):
    return html_to_md(html_text)


def md_to_plain_text(md_text):
    text = md_text
    text = re.sub(r'!\[([^\]]*)\]\([^)]+\)', r'\1', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'\*\*\*(.+?)\*\*\*', r'\1', text)
    text = re.sub(r'___(.+?)___', r'\1', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'_(.+?)_', r'\1', text)
    text = re.sub(r'`([^`]+)`', r'\1', text)
    text = re.sub(r'~~(.+?)~~', r'\1', text)
    text = re.sub(r'^[-*+]\s+', '- ', text, flags=re.MULTILINE)
    text = re.sub(r'^\d+\.\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^>\s?', '', text, flags=re.MULTILINE)
    text = re.sub(r'^---+$', '', text, flags=re.MULTILINE)
    text = re.sub(r'^```\w*$', '', text, flags=re.MULTILINE)
    return text.strip()


# ─── KV Design Language ──────────────────────────────────────────

KV = r"""
<RoundedButton@Button>:
    background_color: 0, 0, 0, 0
    background_normal: ""
    background_down: ""
    color: 1, 1, 1, 1
    canvas.before:
        Color:
            rgba: self.disabled_color_bg if self.disabled else self.bg_color
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(8)]
    bg_color: (0.20, 0.42, 0.82, 1)
    disabled_color_bg: (0.28, 0.28, 0.32, 1)

<PillLabel@Label>:
    font_size: sp(10)
    bold: True
    color: 0.55, 0.55, 0.60, 1
    canvas.before:
        Color:
            rgba: 0.15, 0.16, 0.20, 1
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(12)]

<ConverterRoot>:
    orientation: "vertical"
    padding: dp(24)
    spacing: dp(8)
    canvas.before:
        Color:
            rgba: 0.105, 0.105, 0.14, 1
        Rectangle:
            pos: self.pos
            size: self.size

    # ── Header ──────────────────────────────────────────────
    BoxLayout:
        size_hint_y: None
        height: dp(54)
        padding: dp(16), dp(6)
        canvas.before:
            Color:
                rgba: 0.065, 0.065, 0.09, 1
            RoundedRectangle:
                pos: self.pos
                size: self.size
                radius: [dp(10)]
        Label:
            text: "⇄  Universal File Converter"
            font_size: sp(19)
            bold: True
            color: 0.35, 0.62, 1, 1
            halign: "left"
            valign: "center"
            text_size: self.size
        Label:
            id: header_status
            text: "Ready"
            font_size: sp(11)
            color: 0.45, 0.72, 0.45, 1
            halign: "right"
            valign: "center"
            text_size: self.size

    # ── Category Pills ──────────────────────────────────────
    BoxLayout:
        size_hint_y: None
        height: dp(30)
        spacing: dp(6)
        padding: dp(2), dp(0)
        PillLabel:
            id: pill_audio
            text: "🎵 Audio"
        PillLabel:
            id: pill_video
            text: "🎬 Video"
        PillLabel:
            id: pill_text
            text: "📄 Text"
        PillLabel:
            id: pill_image
            text: "🖼️ Image"
        PillLabel:
            id: pill_data
            text: "📊 Data"

    # ── Section: Input File ─────────────────────────────────
    Label:
        text: "INPUT FILE"
        font_size: sp(10)
        bold: True
        color: 0.42, 0.46, 0.56, 1
        size_hint_y: None
        height: dp(16)
        halign: "left"
        text_size: self.size
        padding_x: dp(4)

    BoxLayout:
        size_hint_y: None
        height: dp(44)
        spacing: dp(8)
        TextInput:
            id: input_path
            readonly: True
            hint_text: "Click Browse to select a file..."
            hint_text_color: 0.38, 0.38, 0.46, 1
            font_size: sp(13)
            foreground_color: 0.88, 0.88, 0.92, 1
            background_color: 0.14, 0.15, 0.19, 1
            padding: dp(14), dp(12)
            cursor_color: 0.35, 0.62, 1, 1
            canvas.before:
                Color:
                    rgba: 0.14, 0.15, 0.19, 1
                RoundedRectangle:
                    pos: self.pos
                    size: self.size
                    radius: [dp(8)]

        RoundedButton:
            text: "Browse"
            size_hint_x: None
            width: dp(88)
            font_size: sp(13)
            on_press: root.open_file_chooser()

    Label:
        id: input_info
        text: ""
        font_size: sp(11)
        color: 0.38, 0.62, 0.38, 1
        size_hint_y: None
        height: dp(16)
        halign: "left"
        text_size: self.size
        padding_x: dp(4)

    # ── Separator ──
    Widget:
        size_hint_y: None
        height: dp(1)
        canvas.before:
            Color:
                rgba: 0.20, 0.21, 0.26, 1
            Rectangle:
                pos: self.pos
                size: self.size

    # ── Section: Output Format ──────────────────────────────
    Label:
        text: "OUTPUT FORMAT"
        font_size: sp(10)
        bold: True
        color: 0.42, 0.46, 0.56, 1
        size_hint_y: None
        height: dp(16)
        halign: "left"
        text_size: self.size
        padding_x: dp(4)

    BoxLayout:
        size_hint_y: None
        height: dp(44)
        spacing: dp(12)
        Spinner:
            id: output_format
            text: "Select output format..."
            values: []
            font_size: sp(13)
            background_color: 0.14, 0.15, 0.19, 1
            background_normal: ""
            color: 1, 1, 1, 1
            size_hint_x: 0.55
            on_text: root._update_convert_button()
        Label:
            id: category_label
            text: ""
            font_size: sp(12)
            color: 0.52, 0.55, 0.62, 1
            halign: "left"
            padding_x: dp(8)

    # ── Quality Settings ─────────────────────────────────────
    BoxLayout:
        size_hint_y: None
        height: dp(36)
        spacing: dp(12)
        padding: dp(0), dp(4)
        Label:
            id: quality_label
            text: "QUALITY"
            font_size: sp(10)
            bold: True
            color: 0.42, 0.46, 0.56, 1
            halign: "left"
            valign: "center"
            text_size: self.size
            padding_x: dp(4)
        Slider:
            id: quality_slider
            min: 1
            max: 100
            value: 85
            value_norm: self.value / 100.0
            size_hint_x: 0.5
            on_value: root._on_quality_change()
            canvas.before:
                Color:
                    rgba: 0.20, 0.42, 0.82, 0.6
                RoundedRectangle:
                    pos: self.pos
                    size: self.size
                    radius: [dp(6)]
        Label:
            id: quality_value
            text: "85"
            font_size: sp(12)
            color: 0.72, 0.72, 0.82, 1
            halign: "left"
            valign: "center"
            size_hint_x: None
            width: dp(40)

    # ── Separator ──
    Widget:
        size_hint_y: None
        height: dp(1)
        canvas.before:
            Color:
                rgba: 0.20, 0.21, 0.26, 1
            Rectangle:
                pos: self.pos
                size: self.size

    # ── Section: Output Directory ───────────────────────────
    Label:
        text: "OUTPUT DIRECTORY  (leave blank = same as input)"
        font_size: sp(10)
        bold: True
        color: 0.42, 0.46, 0.56, 1
        size_hint_y: None
        height: dp(16)
        halign: "left"
        text_size: self.size
        padding_x: dp(4)

    BoxLayout:
        size_hint_y: None
        height: dp(44)
        spacing: dp(8)
        TextInput:
            id: output_dir
            hint_text: "Same as input file (default)"
            hint_text_color: 0.38, 0.38, 0.46, 1
            font_size: sp(13)
            foreground_color: 0.88, 0.88, 0.92, 1
            background_color: 0.14, 0.15, 0.19, 1
            padding: dp(14), dp(12)
            cursor_color: 0.35, 0.62, 1, 1
            canvas.before:
                Color:
                    rgba: 0.14, 0.15, 0.19, 1
                RoundedRectangle:
                    pos: self.pos
                    size: self.size
                    radius: [dp(8)]

        RoundedButton:
            text: "Browse"
            size_hint_x: None
            width: dp(88)
            font_size: sp(13)
            on_press: root.open_dir_chooser()

    # ── Action Buttons Row ──────────────────────────────────
    BoxLayout:
        size_hint_y: None
        height: dp(52)
        spacing: dp(8)
        RoundedButton:
            id: convert_btn
            text: "⚡  CONVERT"
            font_size: sp(15)
            bold: True
            bg_color: 0.20, 0.42, 0.82, 1
            disabled_color_bg: 0.25, 0.25, 0.30, 1
            disabled: True
            on_press: root.start_conversion()
        RoundedButton:
            text: "📁 Batch"
            font_size: sp(13)
            size_hint_x: 0.35
            bg_color: 0.52, 0.28, 0.62, 1
            on_press: root.open_batch_chooser()
        RoundedButton:
            text: "📋 Formats"
            font_size: sp(13)
            size_hint_x: 0.35
            bg_color: 0.14, 0.52, 0.42, 1
            on_press: root.show_supported_conversions()

    # ── Log Section ─────────────────────────────────────────
    BoxLayout:
        size_hint_y: None
        height: dp(24)
        spacing: dp(8)
        Label:
            text: "CONVERSION LOG"
            font_size: sp(10)
            bold: True
            color: 0.42, 0.46, 0.56, 1
            halign: "left"
            valign: "center"
            text_size: self.size
            padding_x: dp(4)
        Widget:
        RoundedButton:
            id: history_btn
            text: "📜 History"
            font_size: sp(10)
            size_hint_x: None
            width: dp(80)
            bg_color: 0.28, 0.28, 0.38, 1
            on_press: root.show_history()

    ScrollView:
        canvas.before:
            Color:
                rgba: 0.065, 0.065, 0.09, 1
            RoundedRectangle:
                pos: self.pos
                size: self.size
                radius: [dp(8)]
        Label:
            id: log_label
            text: "Ready. Select an input file to begin."
            font_size: sp(12)
            color: 0.52, 0.52, 0.62, 1
            markup: True
            halign: "left"
            valign: "top"
            padding: dp(14), dp(14)
            size_hint_y: None
            height: max(self.texture_size[1] + dp(28), dp(90))
            text_size: self.width - dp(28), None

    # ── Open Output Folder Button ───────────────────────────
    RoundedButton:
        id: open_dir_btn
        text: "📁  Open Output Folder"
        font_size: sp(12)
        size_hint_y: None
        height: dp(38)
        bg_color: 0.14, 0.28, 0.52, 1
        opacity: 0
        disabled: True
        on_press: root.open_output_dir()
"""


# ─── Converter Engine ────────────────────────────────────────────

class ConverterEngine:

    # ── FFmpeg helper ───────────────────────────────────────

    @staticmethod
    def _run_ffmpeg(input_path, output_path, timeout, callback, quality=None):
        try:
            cmd = ["ffmpeg", "-i", str(input_path), "-y"]
            # Add quality/bitrate settings based on output format
            out_ext = Path(output_path).suffix.lower().lstrip(".")
            if quality is not None:
                if out_ext in AUDIO_FORMATS:
                    # Audio bitrate: quality maps to kbps (64-320)
                    bitrate = max(64, int(quality * 2.56))
                    cmd.extend(["-b:a", f"{bitrate}k"])
                elif out_ext in VIDEO_FORMATS:
                    # Video CRF: quality maps to CRF (18-51, lower=better)
                    crf = max(18, int(51 - quality * 0.33))
                    cmd.extend(["-crf", str(crf)])
                elif out_ext in IMAGE_FORMATS:
                    pass  # handled by Pillow

            cmd.append(str(output_path))
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout
            )
            if result.returncode != 0:
                err = result.stderr[-400:] if result.stderr else "Unknown FFmpeg error"
                raise Exception(err)
            callback(True, f"Converted -> {Path(output_path).name}")
        except subprocess.TimeoutExpired:
            callback(False, "Conversion timed out")
        except FileNotFoundError:
            callback(False, "FFmpeg not found! Install it and add to PATH.")
        except Exception as e:
            callback(False, f"{e}")

    # ── Audio / Video ───────────────────────────────────────

    @staticmethod
    def convert_audio(inp, out, cb, quality=None):
        ConverterEngine._run_ffmpeg(inp, out, 300, cb, quality)

    @staticmethod
    def convert_video(inp, out, cb, quality=None):
        ConverterEngine._run_ffmpeg(inp, out, 600, cb, quality)

    # ── Image conversions ────────────────────────────────────

    @staticmethod
    def convert_image(inp, out, cb, quality=None):
        try:
            from PIL import Image
            img = Image.open(str(inp))
            out_ext = Path(out).suffix.lower().lstrip(".")

            # Handle format-specific save options
            save_kwargs = {}
            if out_ext in ("jpg", "jpeg"):
                save_kwargs["quality"] = quality or 85
                if img.mode in ("RGBA", "LA", "P"):
                    img = img.convert("RGB")
            elif out_ext == "webp":
                save_kwargs["quality"] = quality or 85
                if img.mode in ("RGBA", "LA", "P"):
                    img = img.convert("RGB")
            elif out_ext == "png":
                # PNG compression: map quality to compress_level (0-9)
                if quality is not None:
                    save_kwargs["compress_level"] = max(0, min(9, int((100 - quality) / 11)))
            elif out_ext == "bmp":
                if img.mode in ("RGBA", "LA", "P"):
                    img = img.convert("RGB")
            elif out_ext == "gif":
                if img.mode in ("RGBA", "LA", "P"):
                    img = img.convert("RGB")
            elif out_ext == "tiff":
                if img.mode in ("RGBA", "LA", "P"):
                    img = img.convert("RGB")

            img.save(str(out), **save_kwargs)
            cb(True, f"Converted -> {Path(out).name}")
        except ImportError:
            cb(False, "Pillow not installed. Run: pip install Pillow")
        except Exception as e:
            cb(False, f"{e}")

    # ── Text conversions ────────────────────────────────────

    @staticmethod
    def _write(path, text, encoding="utf-8"):
        with open(path, "w", encoding=encoding) as fh:
            fh.write(text)

    @staticmethod
    def _read(path, encoding="utf-8"):
        with open(path, "r", encoding=encoding, errors="replace") as fh:
            return fh.read()

    @staticmethod
    def txt_to_pdf(inp, out, cb):
        try:
            from fpdf import FPDF
            pdf = FPDF()
            pdf.set_auto_page_break(auto=True, margin=15)
            pdf.add_page()
            pdf.set_font("Helvetica", size=11)
            w = pdf.w - pdf.l_margin - pdf.r_margin
            with open(inp, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    safe = _sanitize_pdf_text(line.rstrip("\n\r"))
                    if safe.strip():
                        pdf.multi_cell(w, 7, safe, align='L')
                    else:
                        pdf.ln(3)
            pdf.output(str(out))
            cb(True, f"TXT -> PDF -> {Path(out).name}")
        except ImportError:
            cb(False, "fpdf2 not installed. Run: pip install fpdf2")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def md_to_pdf(inp, out, cb):
        try:
            from fpdf import FPDF
            content = ConverterEngine._read(inp)
            pdf = FPDF()
            pdf.set_auto_page_break(auto=True, margin=15)
            pdf.add_page()
            w = pdf.w - pdf.l_margin - pdf.r_margin
            in_code = False
            for line in content.split("\n"):
                stripped = line.strip()
                if stripped.startswith("```"):
                    in_code = not in_code
                    continue
                if in_code:
                    pdf.set_font("Courier", size=9)
                    safe = _sanitize_pdf_text(line)
                    if safe.strip():
                        pdf.multi_cell(w, 5, safe, align='L')
                    else:
                        pdf.ln(2)
                    continue
                hm = re.match(r'^(#{1,6})\s+(.+)$', stripped)
                if hm:
                    lvl = len(hm.group(1))
                    sz = max(18 - (lvl - 1) * 2, 10)
                    pdf.set_font("Helvetica", "B", size=sz)
                    safe = _sanitize_pdf_text(hm.group(2))
                    pdf.multi_cell(w, sz * 0.6, safe, align='L')
                    pdf.set_font("Helvetica", size=11)
                    continue
                if re.match(r'^(\*{3,}|-{3,}|_{3,})$', stripped):
                    pdf.ln(4)
                    y = pdf.get_y()
                    pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
                    pdf.ln(4)
                    continue
                if stripped.startswith(">"):
                    text = re.sub(r'^>\s?', '', stripped)
                    pdf.set_font("Helvetica", "I", size=11)
                    safe = _sanitize_pdf_text(text)
                    pdf.multi_cell(w, 7, f"  {safe}", align='L')
                    pdf.set_font("Helvetica", size=11)
                    continue
                lm = re.match(r'^[-*+]\s+(.+)$', stripped)
                if lm:
                    safe = _sanitize_pdf_text(lm.group(1))
                    pdf.multi_cell(w, 7, f"  - {safe}", align='L')
                    continue
                if not stripped:
                    pdf.ln(3)
                    continue
                pdf.set_font("Helvetica", size=11)
                safe = _sanitize_pdf_text(stripped)
                pdf.multi_cell(w, 7, safe, align='L')
            pdf.output(str(out))
            cb(True, f"MD -> PDF -> {Path(out).name}")
        except ImportError:
            cb(False, "fpdf2 not installed. Run: pip install fpdf2")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def html_to_pdf(inp, out, cb):
        try:
            from fpdf import FPDF
            content = ConverterEngine._read(inp)
            plain = html_to_plain_text(content)
            pdf = FPDF()
            pdf.set_auto_page_break(auto=True, margin=15)
            pdf.add_page()
            pdf.set_font("Helvetica", size=11)
            w = pdf.w - pdf.l_margin - pdf.r_margin
            for line in plain.split("\n"):
                safe = _sanitize_pdf_text(line)
                if not safe.strip():
                    pdf.ln(3)
                else:
                    pdf.multi_cell(w, 7, safe, align='L')
            pdf.output(str(out))
            cb(True, f"HTML -> PDF -> {Path(out).name}")
        except ImportError:
            cb(False, "fpdf2 not installed. Run: pip install fpdf2")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def pdf_to_txt(inp, out, cb):
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(inp))
            parts = []
            for i, page in enumerate(reader.pages):
                t = page.extract_text()
                if t:
                    parts.append(f"--- Page {i+1} ---\n{t}")
            ConverterEngine._write(out, "\n\n".join(parts))
            cb(True, f"PDF -> TXT -> {Path(out).name}")
        except ImportError:
            cb(False, "pypdf not installed. Run: pip install pypdf")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def txt_to_md(inp, out, cb):
        try:
            content = ConverterEngine._read(inp)
            lines = content.split("\n")
            md_lines = []
            for line in lines:
                stripped = line.strip()
                if not stripped:
                    md_lines.append("")
                elif len(stripped) < 60 and not stripped.endswith((".", ",", ";", "!", "?")):
                    md_lines.append(f"## {stripped}")
                else:
                    md_lines.append(stripped)
            ConverterEngine._write(out, "\n".join(md_lines))
            cb(True, f"TXT -> MD -> {Path(out).name}")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def md_to_txt(inp, out, cb):
        try:
            content = ConverterEngine._read(inp)
            plain = md_to_plain_text(content)
            ConverterEngine._write(out, plain)
            cb(True, f"MD -> TXT -> {Path(out).name}")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def txt_to_html(inp, out, cb):
        try:
            content = ConverterEngine._read(inp)
            html_parts = [
                '<!DOCTYPE html>', '<html><head>', '<meta charset="utf-8">',
                '<style>body{font-family:sans-serif;max-width:800px;'
                'margin:40px auto;padding:0 20px;color:#222;line-height:1.7;'
                'background:#fafafa;}h1,h2{color:#333;}</style>',
                '</head><body>',
            ]
            for line in content.split("\n"):
                stripped = line.strip()
                if not stripped:
                    continue
                if len(stripped) < 60 and not stripped.endswith((".", ",", ";")):
                    html_parts.append(f"<h2>{_esc(stripped)}</h2>")
                else:
                    html_parts.append(f"<p>{_esc(stripped)}</p>")
            html_parts.append('</body></html>')
            ConverterEngine._write(out, "\n".join(html_parts))
            cb(True, f"TXT -> HTML -> {Path(out).name}")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def html_to_txt(inp, out, cb):
        try:
            content = ConverterEngine._read(inp)
            plain = html_to_plain_text(content)
            ConverterEngine._write(out, plain)
            cb(True, f"HTML -> TXT -> {Path(out).name}")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def md_to_html_file(inp, out, cb):
        try:
            content = ConverterEngine._read(inp)
            html = md_to_html(content)
            ConverterEngine._write(out, html)
            cb(True, f"MD -> HTML -> {Path(out).name}")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def html_to_md_file(inp, out, cb):
        try:
            content = ConverterEngine._read(inp)
            md = html_to_md(content)
            ConverterEngine._write(out, md)
            cb(True, f"HTML -> MD -> {Path(out).name}")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def pdf_to_md(inp, out, cb):
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(inp))
            parts = []
            for i, page in enumerate(reader.pages):
                t = page.extract_text()
                if t:
                    parts.append(f"## Page {i+1}\n\n{t}")
            ConverterEngine._write(out, "\n\n".join(parts))
            cb(True, f"PDF -> MD -> {Path(out).name}")
        except ImportError:
            cb(False, "pypdf not installed. Run: pip install pypdf")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def pdf_to_html(inp, out, cb):
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(inp))
            html_parts = [
                '<!DOCTYPE html>', '<html><head>', '<meta charset="utf-8">',
                '<style>body{font-family:sans-serif;max-width:800px;'
                'margin:40px auto;padding:0 20px;color:#222;line-height:1.7;'
                'background:#fafafa;}h2{color:#333;border-bottom:1px solid #ddd;'
                'padding-bottom:6px;}</style>',
                '</head><body>',
            ]
            for i, page in enumerate(reader.pages):
                t = page.extract_text()
                if t:
                    html_parts.append(f"<h2>Page {i+1}</h2>")
                    for line in t.split("\n"):
                        html_parts.append(f"<p>{_esc(line)}</p>")
            html_parts.append('</body></html>')
            ConverterEngine._write(out, "\n".join(html_parts))
            cb(True, f"PDF -> HTML -> {Path(out).name}")
        except ImportError:
            cb(False, "pypdf not installed. Run: pip install pypdf")
        except Exception as e:
            cb(False, f"{e}")

    # ── Data conversions ────────────────────────────────────

    @staticmethod
    def json_to_csv(inp, out, cb):
        try:
            data = json.loads(ConverterEngine._read(inp))
            if not isinstance(data, list):
                # Wrap single object in a list
                data = [data]
            if not data:
                ConverterEngine._write(out, "")
                cb(True, f"JSON -> CSV -> {Path(out).name}")
                return
            # Collect all keys from all objects
            all_keys = []
            for item in data:
                if isinstance(item, dict):
                    for k in item.keys():
                        if k not in all_keys:
                            all_keys.append(k)
            with open(out, "w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=all_keys)
                writer.writeheader()
                for item in data:
                    if isinstance(item, dict):
                        row = {k: str(v) if not isinstance(v, str) else v for k, v in item.items()}
                        writer.writerow(row)
            cb(True, f"JSON -> CSV -> {Path(out).name}")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def csv_to_json(inp, out, cb):
        try:
            with open(inp, "r", encoding="utf-8", errors="replace") as fh:
                reader = csv.DictReader(fh)
                rows = list(reader)
            ConverterEngine._write(out, json.dumps(rows, indent=2, ensure_ascii=False))
            cb(True, f"CSV -> JSON -> {Path(out).name}")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def json_to_xml(inp, out, cb):
        try:
            data = json.loads(ConverterEngine._read(inp))
            root_tag = "root"

            def dict_to_xml(parent, data_dict):
                for key, val in data_dict.items():
                    safe_key = re.sub(r'[^a-zA-Z0-9_]', '_', str(key))
                    child = ET.SubElement(parent, safe_key)
                    if isinstance(val, dict):
                        dict_to_xml(child, val)
                    elif isinstance(val, list):
                        for i, item in enumerate(val):
                            item_elem = ET.SubElement(child, "item")
                            if isinstance(item, dict):
                                dict_to_xml(item_elem, item)
                            else:
                                item_elem.text = str(item)
                    else:
                        child.text = str(val)

            root = ET.Element(root_tag)
            if isinstance(data, dict):
                dict_to_xml(root, data)
            elif isinstance(data, list):
                for i, item in enumerate(data):
                    item_elem = ET.SubElement(root, "item")
                    if isinstance(item, dict):
                        dict_to_xml(item_elem, item)
                    else:
                        item_elem.text = str(item)
            else:
                root.text = str(data)

            tree = ET.ElementTree(root)
            ET.indent(tree, space="  ")
            tree.write(str(out), encoding="unicode", xml_declaration=True)
            # Prepend XML declaration properly
            xml_str = ConverterEngine._read(out)
            ConverterEngine._write(out, xml_str)
            cb(True, f"JSON -> XML -> {Path(out).name}")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def xml_to_json(inp, out, cb):
        try:
            tree = ET.parse(str(inp))
            root = tree.getroot()

            def xml_to_dict(element):
                result = {}
                for child in element:
                    tag = child.tag
                    if len(child) > 0:
                        result[tag] = xml_to_dict(child)
                    elif child.text and child.text.strip():
                        result[tag] = child.text.strip()
                    else:
                        result[tag] = None
                return result

            data = xml_to_dict(root)
            ConverterEngine._write(out, json.dumps(data, indent=2, ensure_ascii=False))
            cb(True, f"XML -> JSON -> {Path(out).name}")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def csv_to_xml(inp, out, cb):
        try:
            # Convert CSV to JSON first, then JSON to XML
            with open(inp, "r", encoding="utf-8", errors="replace") as fh:
                reader = csv.DictReader(fh)
                rows = list(reader)

            root = ET.Element("rows")
            for row in rows:
                row_elem = ET.SubElement(root, "row")
                for key, val in row.items():
                    safe_key = re.sub(r'[^a-zA-Z0-9_]', '_', str(key))
                    field = ET.SubElement(row_elem, safe_key)
                    field.text = val

            tree = ET.ElementTree(root)
            ET.indent(tree, space="  ")
            tree.write(str(out), encoding="unicode", xml_declaration=True)
            cb(True, f"CSV -> XML -> {Path(out).name}")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def xml_to_csv(inp, out, cb):
        try:
            # Convert XML to JSON first, then JSON to CSV
            tree = ET.parse(str(inp))
            root = tree.getroot()

            rows = []
            for row_elem in root:
                row_data = {}
                for child in row_elem:
                    row_data[child.tag] = child.text or ""
                rows.append(row_data)

            if not rows:
                ConverterEngine._write(out, "")
                cb(True, f"XML -> CSV -> {Path(out).name}")
                return

            all_keys = []
            for row in rows:
                for k in row.keys():
                    if k not in all_keys:
                        all_keys.append(k)

            with open(out, "w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=all_keys)
                writer.writeheader()
                writer.writerows(rows)

            cb(True, f"XML -> CSV -> {Path(out).name}")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def json_to_yaml(inp, out, cb):
        try:
            import yaml
            data = json.loads(ConverterEngine._read(inp))
            yaml_str = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
            ConverterEngine._write(out, yaml_str)
            cb(True, f"JSON -> YAML -> {Path(out).name}")
        except ImportError:
            cb(False, "PyYAML not installed. Run: pip install PyYAML")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def yaml_to_json(inp, out, cb):
        try:
            import yaml
            with open(inp, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
            ConverterEngine._write(out, json.dumps(data, indent=2, ensure_ascii=False))
            cb(True, f"YAML -> JSON -> {Path(out).name}")
        except ImportError:
            cb(False, "PyYAML not installed. Run: pip install PyYAML")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def csv_to_yaml(inp, out, cb):
        try:
            import yaml
            with open(inp, "r", encoding="utf-8", errors="replace") as fh:
                reader = csv.DictReader(fh)
                rows = list(reader)
            yaml_str = yaml.dump(rows, default_flow_style=False, allow_unicode=True, sort_keys=False)
            ConverterEngine._write(out, yaml_str)
            cb(True, f"CSV -> YAML -> {Path(out).name}")
        except ImportError:
            cb(False, "PyYAML not installed. Run: pip install PyYAML")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def yaml_to_csv(inp, out, cb):
        try:
            import yaml
            with open(inp, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
            if not isinstance(data, list):
                data = [data] if isinstance(data, dict) else []
            if not data:
                ConverterEngine._write(out, "")
                cb(True, f"YAML -> CSV -> {Path(out).name}")
                return
            all_keys = []
            for item in data:
                if isinstance(item, dict):
                    for k in item.keys():
                        if k not in all_keys:
                            all_keys.append(k)
            with open(out, "w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=all_keys)
                writer.writeheader()
                for item in data:
                    if isinstance(item, dict):
                        row = {k: str(v) if v is not None else "" for k, v in item.items()}
                        writer.writerow(row)
            cb(True, f"YAML -> CSV -> {Path(out).name}")
        except ImportError:
            cb(False, "PyYAML not installed. Run: pip install PyYAML")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def xml_to_yaml(inp, out, cb):
        try:
            import yaml
            tree = ET.parse(str(inp))
            root = tree.getroot()

            def xml_to_dict(element):
                result = {}
                for child in element:
                    tag = child.tag
                    if len(child) > 0:
                        result[tag] = xml_to_dict(child)
                    elif child.text and child.text.strip():
                        result[tag] = child.text.strip()
                    else:
                        result[tag] = None
                return result

            data = xml_to_dict(root)
            yaml_str = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
            ConverterEngine._write(out, yaml_str)
            cb(True, f"XML -> YAML -> {Path(out).name}")
        except ImportError:
            cb(False, "PyYAML not installed. Run: pip install PyYAML")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def yaml_to_xml(inp, out, cb):
        try:
            import yaml
            with open(inp, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh)

            def dict_to_xml(parent, data_dict):
                for key, val in data_dict.items():
                    safe_key = re.sub(r'[^a-zA-Z0-9_]', '_', str(key))
                    child = ET.SubElement(parent, safe_key)
                    if isinstance(val, dict):
                        dict_to_xml(child, val)
                    elif isinstance(val, list):
                        for item in val:
                            item_elem = ET.SubElement(child, "item")
                            if isinstance(item, dict):
                                dict_to_xml(item_elem, item)
                            else:
                                item_elem.text = str(item)
                    else:
                        child.text = str(val)

            root = ET.Element("root")
            if isinstance(data, dict):
                dict_to_xml(root, data)
            elif isinstance(data, list):
                for item in data:
                    item_elem = ET.SubElement(root, "item")
                    if isinstance(item, dict):
                        dict_to_xml(item_elem, item)
                    else:
                        item_elem.text = str(item)

            tree = ET.ElementTree(root)
            ET.indent(tree, space="  ")
            tree.write(str(out), encoding="unicode", xml_declaration=True)
            cb(True, f"YAML -> XML -> {Path(out).name}")
        except ImportError:
            cb(False, "PyYAML not installed. Run: pip install PyYAML")
        except Exception as e:
            cb(False, f"{e}")

    # ── Dispatcher ──────────────────────────────────────────

    TEXT_CONVERTERS = {
        ("txt", "pdf"):  "txt_to_pdf",
        ("pdf", "txt"):  "pdf_to_txt",
        ("txt", "md"):   "txt_to_md",
        ("md", "txt"):   "md_to_txt",
        ("txt", "html"): "txt_to_html",
        ("html", "txt"): "html_to_txt",
        ("md", "html"):  "md_to_html_file",
        ("html", "md"):  "html_to_md_file",
        ("md", "pdf"):   "md_to_pdf",
        ("html", "pdf"): "html_to_pdf",
        ("pdf", "md"):   "pdf_to_md",
        ("pdf", "html"): "pdf_to_html",
    }

    DATA_CONVERTERS = {
        ("json", "csv"):  "json_to_csv",
        ("csv", "json"):  "csv_to_json",
        ("json", "xml"):  "json_to_xml",
        ("xml", "json"):  "xml_to_json",
        ("csv", "xml"):   "csv_to_xml",
        ("xml", "csv"):   "xml_to_csv",
        ("json", "yaml"): "json_to_yaml",
        ("yaml", "json"): "yaml_to_json",
        ("csv", "yaml"):  "csv_to_yaml",
        ("yaml", "csv"):  "yaml_to_csv",
        ("xml", "yaml"):  "xml_to_yaml",
        ("yaml", "xml"):  "yaml_to_xml",
    }

    @staticmethod
    def convert(input_path, output_path, callback, quality=None):
        in_ext = Path(input_path).suffix.lower().lstrip(".")
        out_ext = Path(output_path).suffix.lower().lstrip(".")
        cat = get_category(in_ext)

        if cat == "audio":
            ConverterEngine.convert_audio(input_path, output_path, callback, quality)
        elif cat == "video":
            ConverterEngine.convert_video(input_path, output_path, callback, quality)
        elif cat == "image":
            ConverterEngine.convert_image(input_path, output_path, callback, quality)
        elif cat == "text":
            key = (in_ext, out_ext)
            method_name = ConverterEngine.TEXT_CONVERTERS.get(key)
            if method_name:
                getattr(ConverterEngine, method_name)(input_path, output_path, callback)
            else:
                callback(False, f"Text conversion .{in_ext} -> .{out_ext} not supported")
        elif cat == "data":
            key = (in_ext, out_ext)
            method_name = ConverterEngine.DATA_CONVERTERS.get(key)
            if method_name:
                getattr(ConverterEngine, method_name)(input_path, output_path, callback)
            else:
                callback(False, f"Data conversion .{in_ext} -> .{out_ext} not supported")
        else:
            callback(False, f"Unsupported input format: .{in_ext}")


# ─── History Manager ─────────────────────────────────────────────

class HistoryManager:
    def __init__(self, path=HISTORY_FILE):
        self.path = Path(path)
        self._records = []
        self._load()

    def _load(self):
        try:
            if self.path.exists():
                with open(self.path, "r", encoding="utf-8") as fh:
                    self._records = json.load(fh)
        except Exception:
            self._records = []

    def _save(self):
        try:
            # Keep last 500 records
            self._records = self._records[-500:]
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(self._records, fh, indent=2, ensure_ascii=False)
        except Exception:
            pass

    def add(self, input_name, output_name, in_ext, out_ext, category, success):
        self._records.append({
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "input": input_name,
            "output": output_name,
            "conversion": f"{in_ext.upper()} -> {out_ext.upper()}",
            "category": category,
            "success": success,
        })
        self._save()

    def get_all(self):
        return list(reversed(self._records))

    def clear(self):
        self._records = []
        self._save()


# ─── Main UI Widget ─────────────────────────────────────────────

class ConverterRoot(BoxLayout):

    def __init__(self, **kw):
        super().__init__(**kw)
        self._input_file = None
        self._output_file = None
        self._converting = False
        self._quality = 85
        self._history = HistoryManager()
        Clock.schedule_once(self._deferred_init)

    def _deferred_init(self, _dt):
        self._log("Application started. Enhanced version with 94 conversion paths.")
        self._highlight_pill(None)
        if self._check_ffmpeg():
            self._log("FFmpeg found on system.")
        else:
            self._log(
                "[color=ff6666]WARNING: FFmpeg NOT found! Audio/Video conversion will fail.\n"
                "Install from https://ffmpeg.org and add to PATH.[/color]"
            )
        total = len(ALL_CONVERSIONS)
        self._log(
            f"[color=88aadd]Supported: {len(AUDIO_FORMATS)} Audio x {len(VIDEO_FORMATS)} Video x "
            f"{len(TEXT_FORMATS)} Text x {len(IMAGE_FORMATS)} Image x {len(DATA_FORMATS)} Data = "
            f"{total} conversion paths[/color]"
        )
        self._log(
            f"[color=88aadd]Click 'Formats' button to see the full list.[/color]"
        )

    @staticmethod
    def _check_ffmpeg():
        try:
            r = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5)
            return r.returncode == 0
        except Exception:
            return False

    def _highlight_pill(self, category):
        pills = {
            "audio": "pill_audio",
            "video": "pill_video",
            "text": "pill_text",
            "image": "pill_image",
            "data": "pill_data",
        }
        colors = {
            "audio": ((0.20, 0.52, 0.82, 1), (1, 1, 1, 1)),
            "video": ((0.72, 0.28, 0.62, 1), (1, 1, 1, 1)),
            "text":  ((0.30, 0.68, 0.38, 1), (1, 1, 1, 1)),
            "image": ((0.82, 0.52, 0.12, 1), (1, 1, 1, 1)),
            "data":  ((0.52, 0.32, 0.72, 1), (1, 1, 1, 1)),
            None:    ((0.15, 0.16, 0.20, 1), (0.55, 0.55, 0.60, 1)),
        }
        for cat_name, pill_id in pills.items():
            pill = self.ids[pill_id]
            if cat_name == category:
                pill.canvas.before.children[0].rgba = colors[cat_name][0]
                pill.color = colors[cat_name][1]
            else:
                pill.canvas.before.children[0].rgba = colors[None][0]
                pill.color = colors[None][1]

    # ── Quality callback ────────────────────────────────────

    def _on_quality_change(self, *_args):
        self._quality = int(self.ids.quality_slider.value)
        self.ids.quality_value.text = str(self._quality)

    # ── file chooser ────────────────────────────────────────

    def open_file_chooser(self):
        if self._converting:
            return

        content = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))
        filters = [f"*.{f}" for f in ALL_FORMATS]
        chooser = FileChooserListView(filters=filters, path=str(Path.home()), size_hint=(1, 0.92))
        content.add_widget(chooser)

        btn_row = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(8))
        btn_cancel = Button(text="Cancel", background_color=(0.30, 0.30, 0.34, 1), background_normal="")
        btn_select = Button(text="Select", background_color=(0.20, 0.42, 0.82, 1), background_normal="")
        btn_row.add_widget(btn_cancel)
        btn_row.add_widget(btn_select)
        content.add_widget(btn_row)

        popup = Popup(
            title="Select Input File", content=content, size_hint=(0.92, 0.88),
            background_color=(0.10, 0.10, 0.13, 1),
            separator_color=(0.20, 0.42, 0.82, 1),
            title_color=(0.85, 0.85, 0.90, 1),
        )

        def select(_inst):
            if chooser.selection:
                self._on_file_selected(chooser.selection[0])
            popup.dismiss()

        btn_select.bind(on_press=select)
        btn_cancel.bind(on_press=lambda _: popup.dismiss())
        chooser.bind(on_submit=lambda _i, _v, _m: select(None))
        popup.open()

    def open_dir_chooser(self):
        if self._converting:
            return

        content = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))
        chooser = FileChooserListView(dirselect=True, path=str(Path.home()), size_hint=(1, 0.92))
        content.add_widget(chooser)

        btn_row = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(8))
        btn_cancel = Button(text="Cancel", background_color=(0.30, 0.30, 0.34, 1), background_normal="")
        btn_select = Button(text="Select This Folder", background_color=(0.20, 0.42, 0.82, 1), background_normal="")
        btn_row.add_widget(btn_cancel)
        btn_row.add_widget(btn_select)
        content.add_widget(btn_row)

        popup = Popup(
            title="Select Output Directory", content=content, size_hint=(0.92, 0.88),
            background_color=(0.10, 0.10, 0.13, 1),
            separator_color=(0.20, 0.42, 0.82, 1),
            title_color=(0.85, 0.85, 0.90, 1),
        )

        def select(_inst):
            sel = chooser.selection[0] if chooser.selection else chooser.path
            if os.path.isfile(sel):
                sel = os.path.dirname(sel)
            self.ids.output_dir.text = sel
            popup.dismiss()

        btn_select.bind(on_press=select)
        btn_cancel.bind(on_press=lambda _: popup.dismiss())
        popup.open()

    # ── Batch file chooser ──────────────────────────────────

    def open_batch_chooser(self):
        if self._converting:
            return

        content = BoxLayout(orientation="vertical", spacing=dp(8), padding=dp(10))
        filters = [f"*.{f}" for f in ALL_FORMATS]
        chooser = FileChooserListView(filters=filters, path=str(Path.home()),
                                     multiselect=True, size_hint=(1, 0.80))
        content.add_widget(chooser)

        # Output format selector for batch
        fmt_row = BoxLayout(size_hint_y=None, height=dp(40), spacing=dp(8))
        fmt_label = Label(text="Convert to:", font_size=sp(13), color=(0.85, 0.85, 0.90, 1))
        fmt_spinner = Spinner(text="Select format...", values=[f.upper() for f in ALL_FORMATS],
                              font_size=sp(13), background_color=(0.14, 0.15, 0.19, 1),
                              background_normal="", color=(1, 1, 1, 1))
        fmt_row.add_widget(fmt_label)
        fmt_row.add_widget(fmt_spinner)
        content.add_widget(fmt_row)

        btn_row = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(8))
        btn_cancel = Button(text="Cancel", background_color=(0.30, 0.30, 0.34, 1), background_normal="")
        btn_batch = Button(text="Batch Convert", background_color=(0.52, 0.28, 0.62, 1), background_normal="")
        btn_row.add_widget(btn_cancel)
        btn_row.add_widget(btn_batch)
        content.add_widget(btn_row)

        popup = Popup(
            title="Batch Convert - Select Multiple Files", content=content,
            size_hint=(0.92, 0.88),
            background_color=(0.10, 0.10, 0.13, 1),
            separator_color=(0.52, 0.28, 0.62, 1),
            title_color=(0.85, 0.85, 0.90, 1),
        )

        def do_batch(_inst):
            if not chooser.selection:
                return
            out_fmt = fmt_spinner.text.lower()
            if out_fmt not in ALL_FORMATS:
                return
            files = list(chooser.selection)
            popup.dismiss()
            self._run_batch(files, out_fmt)

        btn_batch.bind(on_press=do_batch)
        btn_cancel.bind(on_press=lambda _: popup.dismiss())
        popup.open()

    def _run_batch(self, files, out_fmt):
        out_dir = self.ids.output_dir.text.strip() or str(Path(files[0]).parent)
        if not os.path.isdir(out_dir):
            self._log(f"[color=ff6666]Output directory does not exist: {out_dir}[/color]")
            return

        self._converting = True
        self.ids.convert_btn.disabled = True
        self.ids.convert_btn.text = "BATCH..."
        self.ids.header_status.text = "Batch converting..."
        self.ids.header_status.color = (0.9, 0.75, 0.2, 1)

        total = len(files)
        self._log(f"[color=88bbff]Batch: {total} files -> .{out_fmt.upper()}[/color]")

        def worker():
            for i, fpath in enumerate(files, 1):
                inp = Path(fpath)
                output_path = Path(out_dir) / f"{inp.stem}_converted.{out_fmt}"
                counter = 1
                while output_path.exists():
                    output_path = Path(out_dir) / f"{inp.stem}_converted_{counter}.{out_fmt}"
                    counter += 1

                cat = get_category(inp.suffix.lower().lstrip("."))
                in_ext = inp.suffix.lower().lstrip(".")

                # Skip if same format
                if in_ext == out_fmt:
                    Clock.schedule_once(lambda dt, n=inp.name, idx=i:
                        self._log(f"  [{idx}/{total}] Skipped (same format): {n}"))
                    continue

                # Skip if incompatible category
                if cat != get_category(out_fmt):
                    Clock.schedule_once(lambda dt, n=inp.name, idx=i:
                        self._log(f"  [{idx}/{total}] Skipped (incompatible): {n}"))
                    continue

                def batch_cb(success, msg, fname=inp.name, idx=i, out=output_path):
                    icon = "[color=44dd44]" if success else "[color=ff6666]"
                    Clock.schedule_once(lambda dt: self._log(
                        f"  {icon}[{idx}/{total}] {fname} -> {out.name}{icon}[/color]"))
                    cat = get_category(in_ext)
                    self._history.add(fname, out.name, in_ext, out_fmt, cat or "unknown", success)

                ConverterEngine.convert(str(inp), str(output_path), batch_cb, self._quality)

            Clock.schedule_once(lambda dt: self._finish_batch(total))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_batch(self, total):
        self._converting = False
        self.ids.convert_btn.text = "CONVERT"
        self._update_convert_button()
        self.ids.header_status.text = f"Batch done ({total} files)"
        self.ids.header_status.color = (0.35, 0.80, 0.35, 1)
        self._log(f"[color=88ddff]Batch conversion complete: {total} files processed.[/color]")
        Clock.schedule_once(lambda dt: self._reset_header(), 5)

    # ── Supported Conversions Screen ────────────────────────

    def show_supported_conversions(self):
        content = BoxLayout(orientation="vertical", spacing=dp(4), padding=dp(12))

        title = Label(
            text=f"ALL SUPPORTED CONVERSIONS ({len(ALL_CONVERSIONS)} paths)",
            font_size=sp(16), bold=True,
            color=(0.35, 0.62, 1, 1),
            size_hint_y=None, height=dp(32),
            halign="center", valign="center",
            text_size=(Window.size[0] * 0.76, dp(32)),
        )
        content.add_widget(title)

        scroll = ScrollView(size_hint=(1, 1))
        inner = BoxLayout(orientation="vertical", spacing=dp(4), size_hint_y=None)

        # Group by category
        current_cat = None
        cat_count = 0
        for cat, src, dst in ALL_CONVERSIONS:
            if cat != current_cat:
                if current_cat is not None:
                    count_label = Label(
                        text=f"  ({cat_count} paths)",
                        font_size=sp(10), color=(0.55, 0.55, 0.65, 1),
                        size_hint_y=None, height=dp(14),
                        halign="left",
                    )
                    inner.add_widget(count_label)
                icon = CATEGORY_ICONS.get(cat, "")
                cat_label = Label(
                    text=f"{icon}  {cat.upper()} FORMATS",
                    font_size=sp(13), bold=True,
                    color=(0.82, 0.82, 0.92, 1),
                    size_hint_y=None, height=dp(24),
                    halign="left",
                )
                inner.add_widget(cat_label)
                current_cat = cat
                cat_count = 0

            conv_line = Label(
                text=f"  {src}  ->  {dst}",
                font_size=sp(11),
                color=(0.72, 0.72, 0.82, 1),
                size_hint_y=None, height=dp(18),
                halign="left",
            )
            inner.add_widget(conv_line)
            cat_count += 1

        # Last category count
        count_label = Label(
            text=f"  ({cat_count} paths)",
            font_size=sp(10), color=(0.55, 0.55, 0.65, 1),
            size_hint_y=None, height=dp(14),
            halign="left",
        )
        inner.add_widget(count_label)

        # Summary
        inner.add_widget(Widget(size_hint_y=None, height=dp(12)))
        formats_list = ", ".join(f.upper() for f in ALL_FORMATS)
        summary = Label(
            text=f"All supported input formats: {formats_list}",
            font_size=sp(10), color=(0.55, 0.62, 0.72, 1),
            size_hint_y=None, height=dp(40),
            halign="center",
        )
        inner.add_widget(summary)

        inner.height = sum(w.height for w in inner.children)
        scroll.add_widget(inner)
        content.add_widget(scroll)

        btn_row = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(8), padding=dp(4))
        btn_clear = Button(text="Close", background_color=(0.30, 0.30, 0.34, 1), background_normal="")

        popup = Popup(
            title="Supported Conversions",
            content=content,
            size_hint=(0.80, 0.85),
            background_color=(0.10, 0.10, 0.13, 1),
            separator_color=(0.14, 0.52, 0.42, 1),
            title_color=(0.85, 0.85, 0.90, 1),
        )
        btn_clear.bind(on_press=lambda _: popup.dismiss())
        btn_row.add_widget(btn_clear)
        content.add_widget(btn_row)
        popup.open()

    # ── History Screen ──────────────────────────────────────

    def show_history(self):
        records = self._history.get_all()

        content = BoxLayout(orientation="vertical", spacing=dp(4), padding=dp(12))

        title_row = BoxLayout(size_hint_y=None, height=dp(36), spacing=dp(8))
        title = Label(
            text=f"CONVERSION HISTORY ({len(records)} records)",
            font_size=sp(14), bold=True,
            color=(0.35, 0.62, 1, 1),
            halign="left", valign="center",
        )
        title_row.add_widget(title)

        btn_clear_hist = Button(
            text="Clear", font_size=sp(11),
            background_color=(0.62, 0.22, 0.22, 1), background_normal="",
            size_hint_x=None, width=dp(60),
        )
        title_row.add_widget(Widget())
        title_row.add_widget(btn_clear_hist)
        content.add_widget(title_row)

        scroll = ScrollView(size_hint=(1, 1))
        inner = BoxLayout(orientation="vertical", spacing=dp(2), size_hint_y=None)

        if not records:
            empty = Label(
                text="No conversion history yet.",
                font_size=sp(12), color=(0.55, 0.55, 0.65, 1),
                size_hint_y=None, height=dp(40),
                halign="center",
            )
            inner.add_widget(empty)
        else:
            for rec in records[:100]:  # Show max 100
                status = "[color=44dd44]OK[/color]" if rec.get("success") else "[color=ff6666]FAIL[/color]"
                line = Label(
                    text=f"[color=667788]{rec.get('timestamp', '')}[/color]  "
                         f"{rec.get('input', '?')} -> {rec.get('output', '?')}  "
                         f"({rec.get('conversion', '')})  {status}",
                    font_size=sp(10),
                    color=(0.72, 0.72, 0.82, 1),
                    markup=True,
                    size_hint_y=None, height=dp(18),
                    halign="left",
                )
                inner.add_widget(line)

        inner.height = sum(w.height for w in inner.children) if inner.children else dp(40)
        scroll.add_widget(inner)
        content.add_widget(scroll)

        btn_row = BoxLayout(size_hint_y=None, height=dp(42), spacing=dp(8), padding=dp(4))
        btn_close = Button(text="Close", background_color=(0.30, 0.30, 0.34, 1), background_normal="")

        popup = Popup(
            title="Conversion History",
            content=content,
            size_hint=(0.80, 0.85),
            background_color=(0.10, 0.10, 0.13, 1),
            separator_color=(0.28, 0.28, 0.38, 1),
            title_color=(0.85, 0.85, 0.90, 1),
        )

        def clear_hist(_inst):
            self._history.clear()
            popup.dismiss()
            self._log("History cleared.")

        btn_clear_hist.bind(on_press=clear_hist)
        btn_close.bind(on_press=lambda _: popup.dismiss())
        btn_row.add_widget(btn_close)
        content.add_widget(btn_row)
        popup.open()

    # ── file selected ───────────────────────────────────────

    def _on_file_selected(self, filepath):
        self._input_file = filepath
        self.ids.input_path.text = filepath

        ext = Path(filepath).suffix.lower().lstrip(".")
        cat = get_category(ext)

        self.ids.open_dir_btn.opacity = 0
        self.ids.open_dir_btn.disabled = True

        if cat:
            icon = CATEGORY_ICONS.get(cat, "")
            sz = human_size(os.path.getsize(filepath))
            self.ids.input_info.text = f"{icon}  {cat.upper()}  ·  .{ext}  ·  {sz}"
            self.ids.category_label.text = f"Category: {cat.capitalize()}"
            self._highlight_pill(cat)
            formats = get_compatible_formats(ext)
            self.ids.output_format.values = formats
            self.ids.output_format.text = "Select output format..." if formats else "No compatible formats"
        else:
            self.ids.input_info.text = f"Unsupported format: .{ext}"
            self.ids.category_label.text = "Unknown"
            self._highlight_pill(None)
            self.ids.output_format.values = []
            self.ids.output_format.text = "N/A"

        self._update_convert_button()
        self._log(f"Selected: {Path(filepath).name}")

    # ── convert button state ────────────────────────────────

    def _update_convert_button(self, *_args):
        has_input = self._input_file is not None
        fmt = self.ids.output_format.text
        valid_formats = [f.upper() for f in ALL_FORMATS]
        has_format = fmt in valid_formats
        self.ids.convert_btn.disabled = not (has_input and has_format and not self._converting)

    # ── start conversion ────────────────────────────────────

    def start_conversion(self):
        if self._converting or not self._input_file:
            return

        out_fmt = self.ids.output_format.text.lower()
        if out_fmt not in ALL_FORMATS:
            return

        inp = Path(self._input_file)
        out_dir = self.ids.output_dir.text.strip() or str(inp.parent)

        if not os.path.isdir(out_dir):
            self._log(f"[color=ff6666]Output directory does not exist: {out_dir}[/color]")
            return

        output_path = Path(out_dir) / f"{inp.stem}_converted.{out_fmt}"
        counter = 1
        while output_path.exists():
            output_path = Path(out_dir) / f"{inp.stem}_converted_{counter}.{out_fmt}"
            counter += 1

        self._output_file = str(output_path)
        self._converting = True

        self.ids.convert_btn.disabled = True
        self.ids.convert_btn.text = "CONVERTING..."
        self.ids.header_status.text = "Converting..."
        self.ids.header_status.color = (0.9, 0.75, 0.2, 1)
        self.ids.open_dir_btn.opacity = 0
        self.ids.open_dir_btn.disabled = True

        self._log(f"[color=88bbff]> Converting:[/color] {inp.name}  ->  {output_path.name}  (.{out_fmt})")

        def run():
            in_ext = inp.suffix.lower().lstrip(".")
            cat = get_category(in_ext)

            def done_cb(success, msg):
                self._history.add(inp.name, output_path.name, in_ext, out_fmt,
                                  cat or "unknown", success)
                self._on_done(success, msg)

            ConverterEngine.convert(str(inp), str(output_path), done_cb, self._quality)

        threading.Thread(target=run, daemon=True).start()

    def _on_done(self, success, message):
        Clock.schedule_once(lambda _dt: self._finish(success, message))

    def _finish(self, success, message):
        self._converting = False
        self.ids.convert_btn.text = "CONVERT"
        self._update_convert_button()

        if success:
            self.ids.header_status.text = "Done"
            self.ids.header_status.color = (0.35, 0.80, 0.35, 1)
            self.ids.open_dir_btn.opacity = 1
            self.ids.open_dir_btn.disabled = False
            self._log(message)
        else:
            self.ids.header_status.text = "Failed"
            self.ids.header_status.color = (0.90, 0.35, 0.35, 1)
            self.ids.open_dir_btn.opacity = 0
            self.ids.open_dir_btn.disabled = True
            self._log(f"[color=ff6666]{message}[/color]")

        Clock.schedule_once(lambda _dt: self._reset_header(), 5)

    def _reset_header(self):
        if not self._converting:
            self.ids.header_status.text = "Ready"
            self.ids.header_status.color = (0.45, 0.72, 0.45, 1)

    # ── open output folder ──────────────────────────────────

    def open_output_dir(self):
        if not self._output_file:
            return
        path = str(Path(self._output_file).parent)
        try:
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.run(["open", path])
            else:
                subprocess.run(["xdg-open", path])
        except Exception as e:
            self._log(f"[color=ff6666]Could not open folder: {e}[/color]")

    # ── logging ─────────────────────────────────────────────

    def _log(self, text):
        ts = datetime.now().strftime("%H:%M:%S")
        entry = f"[color=667788][{ts}][/color] {text}"
        cur = self.ids.log_label.text
        if cur and cur != "Ready. Select an input file to begin.":
            self.ids.log_label.text = cur + "\n" + entry
        else:
            self.ids.log_label.text = entry


# ─── Application ─────────────────────────────────────────────────

class ConverterApp(App):
    title = "Universal File Converter"

    def build(self):
        Window.size = (660, 900)
        Window.minimum_size = (500, 700)
        Window.clearcolor = (0.105, 0.105, 0.14, 1)
        Builder.load_string(KV)
        return ConverterRoot()


if __name__ == "__main__":
    ConverterApp().run()
