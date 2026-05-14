"""
Universal File Format Converter — Kivy Application
===================================================
Audio:  WAV ↔ MP3 ↔ OGG ↔ FLAC ↔ AAC        (via FFmpeg)
Video:  MP4 ↔ MKV ↔ WEBM ↔ AVI ↔ MOV        (via FFmpeg)
Text:   TXT ↔ PDF ↔ MD ↔ HTML                 (via fpdf2 / PyPDF2 / built-in)

Requirements:
    pip install kivy fpdf2 PyPDF2
    FFmpeg must be installed on your system.
"""

import os
import re
import sys
import subprocess
import threading
from pathlib import Path
from datetime import datetime

from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.button import Button
from kivy.uix.spinner import Spinner
from kivy.uix.popup import Popup
from kivy.uix.filechooser import FileChooserListView
from kivy.core.window import Window
from kivy.clock import Clock
from kivy.lang import Builder
from kivy.metrics import dp, sp

# ─── Format Definitions ──────────────────────────────────────────

AUDIO_FORMATS = ["wav", "mp3", "ogg", "flac", "aac"]
VIDEO_FORMATS = ["mp4", "mkv", "webm", "avi", "mov"]
TEXT_FORMATS = ["pdf", "txt", "md", "html"]
ALL_FORMATS = AUDIO_FORMATS + VIDEO_FORMATS + TEXT_FORMATS


def get_category(ext):
    ext = ext.lower().lstrip(".")
    if ext in AUDIO_FORMATS:
        return "audio"
    if ext in VIDEO_FORMATS:
        return "video"
    if ext in TEXT_FORMATS:
        return "text"
    return None


def get_compatible_formats(input_ext):
    cat = get_category(input_ext)
    pools = {"audio": AUDIO_FORMATS, "video": VIDEO_FORMATS, "text": TEXT_FORMATS}
    pool = pools.get(cat, [])
    return [f.upper() for f in pool if f != input_ext]

def _sanitize_pdf_text(text):
    """
    Gracefully maps common Unicode characters to ASCII/Latin-1 equivalents 
    so FPDF's built-in fonts don't crash.
    """
    unicode_map = {
        '\u2022': '-',   # Bullet •
        '\u2023': '>',   # Triangular bullet ‣
        '\u2013': '-',   # En dash –
        '\u2014': '--',  # Em dash —
        '\u2018': "'",   # Left single quote ‘
        '\u2019': "'",   # Right single quote ’
        '\u201c': '"',   # Left double quote “
        '\u201d': '"',   # Right double quote ”
        '\u2026': '...', # Ellipsis …
    }
    for uni_char, replacement in unicode_map.items():
        text = text.replace(uni_char, replacement)
    
    # Replace any remaining non-latin-1 characters with '?'
    return text.encode("latin-1", errors="replace").decode("latin-1")

def human_size(n):
    for u in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"


# ─── Markdown ↔ HTML Helpers ────────────────────────────────────

def md_to_html(md_text):
    """Convert basic Markdown to HTML (no external dependency)."""
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

        # --- fenced code block ---
        if line.strip().startswith("```"):
            if not in_code_block:
                # close any open list
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

        # --- table ---
        if "|" in line and line.strip().startswith("|"):
            if not in_table:
                if in_list:
                    html_parts.append("</ul>")
                    in_list = False
                html_parts.append("<table>")
                in_table = True
            row_cells = [c.strip() for c in line.strip().strip("|").split("|")]
            # skip separator row like |---|---|
            if all(set(c.strip()) <= {"-", ":", " "} for c in row_cells):
                i += 1
                continue
            tag = "th" if i == 0 or not any(
                lines[j].strip().startswith("|") and
                all(set(x.strip()) <= {"-", ":", " "}
                    for x in lines[j].strip().strip("|").split("|"))
                for j in range(max(0, i - 1), i)
            ) else "td"
            # Use th for first data row, td for rest
            tag = "td"  # simplified: all td
            html_parts.append("<tr>")
            for cell in row_cells:
                html_parts.append(f"<{tag}>{_inline(cell)}</{tag}>")
            html_parts.append("</tr>")
            i += 1
            # Check if next line is still table
            if i >= len(lines) or "|" not in lines[i]:
                html_parts.append("</table>")
                in_table = False
            continue
        else:
            if in_table:
                html_parts.append("</table>")
                in_table = False

        # --- headings ---
        hm = re.match(r'^(#{1,6})\s+(.+)$', line)
        if hm:
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            lvl = len(hm.group(1))
            html_parts.append(f"<h{lvl}>{_inline(hm.group(2))}</h{lvl}>")
            i += 1
            continue

        # --- horizontal rule ---
        if re.match(r'^(\*{3,}|-{3,}|_{3,})\s*$', line.strip()):
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            html_parts.append("<hr>")
            i += 1
            continue

        # --- blockquote ---
        if line.strip().startswith(">"):
            if in_list:
                html_parts.append("</ul>")
                in_list = False
            quote_text = re.sub(r'^>\s?', '', line)
            html_parts.append(f"<blockquote><p>{_inline(quote_text)}</p></blockquote>")
            i += 1
            continue

        # --- unordered list ---
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

        # --- ordered list ---
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
                # Check if it was an ol
                html_parts.append("</ul>")
                in_list = False

        # --- empty line ---
        if not line.strip():
            i += 1
            continue

        # --- paragraph ---
        html_parts.append(f"<p>{_inline(line)}</p>")
        i += 1

    if in_list:
        html_parts.append("</ul>")
    if in_table:
        html_parts.append("</table>")

    html_parts.append('</body></html>')
    return "\n".join(html_parts)


def _esc(text):
    """HTML-escape text."""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def _inline(text):
    """Convert inline Markdown to HTML."""
    # images
    text = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', r'<img src="\2" alt="\1">', text)
    # links
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)
    # bold+italic
    text = re.sub(r'\*\*\*(.+?)\*\*\*', r'<strong><em>\1</em></strong>', text)
    text = re.sub(r'___(.+?)___', r'<strong><em>\1</em></strong>', text)
    # bold
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'__(.+?)__', r'<strong>\1</strong>', text)
    # italic
    text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
    text = re.sub(r'_(.+?)_', r'<em>\1</em>', text)
    # inline code
    text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
    # strikethrough
    text = re.sub(r'~~(.+?)~~', r'<del>\1</del>', text)
    return text


def html_to_md(html_text):
    """Convert basic HTML to Markdown (no external dependency)."""
    text = html_text

    # Remove style/script blocks
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)

    # Headings
    for lvl in range(1, 7):
        hashes = "#" * lvl
        text = re.sub(
            rf'<h{lvl}[^>]*>(.*?)</h{lvl}>',
            rf'\n{hashes} \1\n',
            text, flags=re.DOTALL | re.IGNORECASE
        )

    # Block-level elements
    text = re.sub(r'<p[^>]*>(.*?)</p>', r'\n\1\n', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<br\s*/?\s*>', '\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<hr\s*/?\s*>', '\n---\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<blockquote[^>]*>(.*?)</blockquote>',
                  r'\n> \1\n', text, flags=re.DOTALL | re.IGNORECASE)

    # Lists
    text = re.sub(r'<li[^>]*>(.*?)</li>', r'- \1\n',
                  text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'</?[ou]l[^>]*>', '\n', text, flags=re.IGNORECASE)

    # Tables — convert to simple text layout
    text = re.sub(r'<th[^>]*>(.*?)</th>', r'| \1 ', text,
                  flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<td[^>]*>(.*?)</td>', r'| \1 ', text,
                  flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<tr[^>]*>(.*?)</tr>', r'\1|\n', text,
                  flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'</?table[^>]*>', '\n', text, flags=re.IGNORECASE)

    # Inline formatting
    text = re.sub(r'<strong[^>]*>(.*?)</strong>', r'**\1**', text,
                  flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<b[^>]*>(.*?)</b>', r'**\1**', text,
                  flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<em[^>]*>(.*?)</em>', r'*\1*', text,
                  flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<i[^>]*>(.*?)</i>', r'*\1*', text,
                  flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<code[^>]*>(.*?)</code>', r'`\1`', text,
                  flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<del[^>]*>(.*?)</del>', r'~~\1~~', text,
                  flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', r'[\2](\1)', text,
                  flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<img[^>]*src="([^"]*)"[^>]*alt="([^"]*)"[^>]*/?\s*>',
                  r'![\2](\1)', text, flags=re.IGNORECASE)
    text = re.sub(r'<img[^>]*src="([^"]*)"[^>]*/?\s*>', r'![](\1)', text,
                  flags=re.IGNORECASE)

    # Strip remaining tags
    text = re.sub(r'<[^>]+>', '', text)

    # Decode entities
    for entity, char in [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                         ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")]:
        text = text.replace(entity, char)

    # Clean up whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]+$', '', text, flags=re.MULTILINE)
    return text.strip()


def html_to_plain_text(html_text):
    """Extract plain text from HTML."""
    return html_to_md(html_text)  # reuse, gives clean text


def md_to_plain_text(md_text):
    """Strip Markdown syntax to get plain text."""
    text = md_text
    text = re.sub(r'!\[([^\]]*)\]\([^)]+\)', r'\1', text)  # images → alt
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)    # links → text
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)  # headings
    text = re.sub(r'\*\*\*(.+?)\*\*\*', r'\1', text)        # bold+italic
    text = re.sub(r'___(.+?)___', r'\1', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)            # bold
    text = re.sub(r'__(.+?)__', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)                # italic
    text = re.sub(r'_(.+?)_', r'\1', text)
    text = re.sub(r'`([^`]+)`', r'\1', text)                # inline code
    text = re.sub(r'~~(.+?)~~', r'\1', text)                # strikethrough
    text = re.sub(r'^[-*+]\s+', '• ', text, flags=re.MULTILINE)  # list items
    text = re.sub(r'^\d+\.\s+', '', text, flags=re.MULTILINE)    # ol items
    text = re.sub(r'^>\s?', '', text, flags=re.MULTILINE)        # blockquotes
    text = re.sub(r'^---+$', '', text, flags=re.MULTILINE)       # hr
    # Remove fenced code block markers
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
        Label:
            id: pill_audio
            text: "🎵 Audio"
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
        Label:
            id: pill_video
            text: "🎬 Video"
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
        Label:
            id: pill_text
            text: "📄 Text"
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

    # ── Convert Button ──────────────────────────────────────
    RoundedButton:
        id: convert_btn
        text: "⚡  CONVERT"
        font_size: sp(15)
        bold: True
        size_hint_y: None
        height: dp(52)
        disabled: True
        bg_color: 0.20, 0.42, 0.82, 1
        disabled_color_bg: 0.25, 0.25, 0.30, 1
        on_press: root.start_conversion()

    # ── Log Section ─────────────────────────────────────────
    Label:
        text: "CONVERSION LOG"
        font_size: sp(10)
        bold: True
        color: 0.42, 0.46, 0.56, 1
        size_hint_y: None
        height: dp(16)
        halign: "left"
        text_size: self.size
        padding_x: dp(4)

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
    def _run_ffmpeg(input_path, output_path, timeout, callback):
        try:
            cmd = ["ffmpeg", "-i", str(input_path), "-y", str(output_path)]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout
            )
            if result.returncode != 0:
                err = result.stderr[-400:] if result.stderr else "Unknown FFmpeg error"
                raise Exception(err)
            callback(True, f"✅ Converted → {Path(output_path).name}")
        except subprocess.TimeoutExpired:
            callback(False, "❌ Conversion timed out")
        except FileNotFoundError:
            callback(False, "❌ FFmpeg not found! Install it and add to PATH.")
        except Exception as e:
            callback(False, f"❌ {e}")

    # ── Audio / Video ───────────────────────────────────────

    @staticmethod
    def convert_audio(inp, out, cb):
        ConverterEngine._run_ffmpeg(inp, out, 300, cb)

    @staticmethod
    def convert_video(inp, out, cb):
        ConverterEngine._run_ffmpeg(inp, out, 600, cb)

    # ── Text conversions ────────────────────────────────────

    @staticmethod
    def _write(path, text, encoding="utf-8"):
        with open(path, "w", encoding=encoding) as fh:
            fh.write(text)

    @staticmethod
    def _read(path, encoding="utf-8"):
        with open(path, "r", encoding=encoding, errors="replace") as fh:
            return fh.read()

    # TXT → PDF
    @staticmethod
    def txt_to_pdf(inp, out, cb):
        try:
            from fpdf import FPDF
            pdf = FPDF()
            pdf.set_auto_page_break(auto=True, margin=15)
            pdf.add_page()
            pdf.set_font("Helvetica", size=11)
            
            # FIX: Explicitly calculate width to bypass fpdf2 w=0 calculation bugs
            w = pdf.w - pdf.l_margin - pdf.r_margin
            
            with open(inp, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    safe = _sanitize_pdf_text(line.rstrip("\n\r"))
                    if safe.strip():
                        pdf.multi_cell(w, 7, safe, align='L')
                    else:
                        pdf.ln(3)
            pdf.output(str(out))
            cb(True, f"✅ TXT → PDF → {Path(out).name}")
        except ImportError:
            cb(False, "❌ [b]fpdf2[/b] not installed. Run: pip install fpdf2")
        except Exception as e:
            cb(False, f"❌ {e}")

    # MD → PDF
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
            cb(True, f"✅ MD → PDF → {Path(out).name}")
        except ImportError:
            cb(False, "❌ [b]fpdf2[/b] not installed. Run: pip install fpdf2")
        except Exception as e:
            cb(False, f"❌ {e}")

     # HTML → PDF
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
            cb(True, f"✅ HTML → PDF → {Path(out).name}")
        except ImportError:
            cb(False, "❌ [b]fpdf2[/b] not installed. Run: pip install fpdf2")
        except Exception as e:
            cb(False, f"❌ {e}")

    # PDF → TXT
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
            cb(True, f"✅ PDF → TXT → {Path(out).name}")
        except ImportError:
            cb(False, "❌ [b]pypdf[/b] not installed. Run: pip install pypdf")
        except Exception as e:
            cb(False, f"❌ {e}")

    # TXT → MD
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
                else:
                    # Detect if line looks like a heading (short, no period at end)
                    if len(stripped) < 60 and not stripped.endswith((".", ",", ";", "!", "?")):
                        md_lines.append(f"## {stripped}")
                    else:
                        md_lines.append(stripped)
            ConverterEngine._write(out, "\n".join(md_lines))
            cb(True, f"✅ TXT → MD → {Path(out).name}")
        except Exception as e:
            cb(False, f"❌ {e}")

    # MD → TXT
    @staticmethod
    def md_to_txt(inp, out, cb):
        try:
            content = ConverterEngine._read(inp)
            plain = md_to_plain_text(content)
            ConverterEngine._write(out, plain)
            cb(True, f"✅ MD → TXT → {Path(out).name}")
        except Exception as e:
            cb(False, f"❌ {e}")

    # TXT → HTML
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
            cb(True, f"✅ TXT → HTML → {Path(out).name}")
        except Exception as e:
            cb(False, f"❌ {e}")

    # HTML → TXT
    @staticmethod
    def html_to_txt(inp, out, cb):
        try:
            content = ConverterEngine._read(inp)
            plain = html_to_plain_text(content)
            ConverterEngine._write(out, plain)
            cb(True, f"✅ HTML → TXT → {Path(out).name}")
        except Exception as e:
            cb(False, f"❌ {e}")

    # MD → HTML
    @staticmethod
    def md_to_html_file(inp, out, cb):
        try:
            content = ConverterEngine._read(inp)
            html = md_to_html(content)
            ConverterEngine._write(out, html)
            cb(True, f"✅ MD → HTML → {Path(out).name}")
        except Exception as e:
            cb(False, f"❌ {e}")

    # HTML → MD
    @staticmethod
    def html_to_md_file(inp, out, cb):
        try:
            content = ConverterEngine._read(inp)
            md = html_to_md(content)
            ConverterEngine._write(out, md)
            cb(True, f"✅ HTML → MD → {Path(out).name}")
        except Exception as e:
            cb(False, f"❌ {e}")

    # PDF → MD
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
            cb(True, f"✅ PDF → MD → {Path(out).name}")
        except ImportError:
            cb(False, "❌ [b]pypdf[/b] not installed. Run: pip install pypdf")
        except Exception as e:
            cb(False, f"❌ {e}")

    # PDF → HTML
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
            cb(True, f"✅ PDF → HTML → {Path(out).name}")
        except ImportError:
            cb(False, "❌ [b]pypdf[/b] not installed. Run: pip install pypdf")
        except Exception as e:
            cb(False, f"❌ {e}")

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

    @staticmethod
    def convert(input_path, output_path, callback):
        in_ext = Path(input_path).suffix.lower().lstrip(".")
        out_ext = Path(output_path).suffix.lower().lstrip(".")
        cat = get_category(in_ext)

        if cat == "audio":
            ConverterEngine.convert_audio(input_path, output_path, callback)
        elif cat == "video":
            ConverterEngine.convert_video(input_path, output_path, callback)
        elif cat == "text":
            key = (in_ext, out_ext)
            method_name = ConverterEngine.TEXT_CONVERTERS.get(key)
            if method_name:
                getattr(ConverterEngine, method_name)(input_path, output_path, callback)
            else:
                callback(False, f"❌ Text conversion .{in_ext} → .{out_ext} not supported")
        else:
            callback(False, f"❌ Unsupported input format: .{in_ext}")


# ─── Main UI Widget ─────────────────────────────────────────────

class ConverterRoot(BoxLayout):

    def __init__(self, **kw):
        super().__init__(**kw)
        self._input_file = None
        self._output_file = None
        self._converting = False
        Clock.schedule_once(self._deferred_init)

    def _deferred_init(self, _dt):
        self._log("Application started.")
        self._highlight_pill(None)
        if self._check_ffmpeg():
            self._log("✅ FFmpeg found on system.")
        else:
            self._log(
                "[color=ff6666]⚠ FFmpeg NOT found! Audio/Video conversion will fail.\n"
                "Install from https://ffmpeg.org and add to PATH.[/color]"
            )
        self._log(
            "[color=88aadd]📄 Text formats supported: TXT, PDF, MD (Markdown), HTML — "
            "12 conversion paths[/color]"
        )

    @staticmethod
    def _check_ffmpeg():
        try:
            r = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5)
            return r.returncode == 0
        except Exception:
            return False

    def _highlight_pill(self, category):
        pills = {"audio": "pill_audio", "video": "pill_video", "text": "pill_text"}
        colors = {
            "audio": ((0.20, 0.52, 0.82, 1), (1, 1, 1, 1)),
            "video": ((0.72, 0.28, 0.62, 1), (1, 1, 1, 1)),
            "text":  ((0.30, 0.68, 0.38, 1), (1, 1, 1, 1)),
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

    # ── file selected ───────────────────────────────────────

    def _on_file_selected(self, filepath):
        self._input_file = filepath
        self.ids.input_path.text = filepath

        ext = Path(filepath).suffix.lower().lstrip(".")
        cat = get_category(ext)

        self.ids.open_dir_btn.opacity = 0
        self.ids.open_dir_btn.disabled = True

        if cat:
            icon = {"audio": "🎵", "video": "🎬", "text": "📄"}.get(cat, "")
            sz = human_size(os.path.getsize(filepath))
            self.ids.input_info.text = f"{icon}  {cat.upper()}  ·  .{ext}  ·  {sz}"
            self.ids.category_label.text = f"Category: {cat.capitalize()}"
            self._highlight_pill(cat)
            formats = get_compatible_formats(ext)
            self.ids.output_format.values = formats
            self.ids.output_format.text = "Select output format..." if formats else "No compatible formats"
        else:
            self.ids.input_info.text = f"⚠  Unsupported format: .{ext}"
            self.ids.category_label.text = "Unknown"
            self._highlight_pill(None)
            self.ids.output_format.values = []
            self.ids.output_format.text = "N/A"

        self._update_convert_button()
        self._log(f"Selected input: {Path(filepath).name}")

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
            self._log(f"[color=ff6666]❌ Output directory does not exist: {out_dir}[/color]")
            return

        output_path = Path(out_dir) / f"{inp.stem}_converted.{out_fmt}"
        counter = 1
        while output_path.exists():
            output_path = Path(out_dir) / f"{inp.stem}_converted_{counter}.{out_fmt}"
            counter += 1

        self._output_file = str(output_path)
        self._converting = True

        self.ids.convert_btn.disabled = True
        self.ids.convert_btn.text = "⏳  CONVERTING..."
        self.ids.header_status.text = "Converting..."
        self.ids.header_status.color = (0.9, 0.75, 0.2, 1)
        self.ids.open_dir_btn.opacity = 0
        self.ids.open_dir_btn.disabled = True

        self._log(f"[color=88bbff]▶ Converting:[/color] {inp.name}  →  {output_path.name}  (.{out_fmt})")

        def run():
            ConverterEngine.convert(str(inp), str(output_path), self._on_done)

        threading.Thread(target=run, daemon=True).start()

    def _on_done(self, success, message):
        Clock.schedule_once(lambda _dt: self._finish(success, message))

    def _finish(self, success, message):
        self._converting = False
        self.ids.convert_btn.text = "⚡  CONVERT"
        self._update_convert_button()

        if success:
            self.ids.header_status.text = "Done ✓"
            self.ids.header_status.color = (0.35, 0.80, 0.35, 1)
            self.ids.open_dir_btn.opacity = 1
            self.ids.open_dir_btn.disabled = False
            self._log(message)
        else:
            self.ids.header_status.text = "Failed ✗"
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
            self._log(f"[color=ff6666]❌ Could not open folder: {e}[/color]")

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
        Window.size = (660, 820)
        Window.minimum_size = (500, 650)
        Window.clearcolor = (0.105, 0.105, 0.14, 1)
        Builder.load_string(KV)
        return ConverterRoot()


if __name__ == "__main__":
    ConverterApp().run()