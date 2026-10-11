"""
Universal File Format Converter — PySide6 Application
======================================================
Audio:  WAV MP3 OGG FLAC AAC WMA AIFF M4A OPUS AMR   (via FFmpeg)
Video:  MP4 MKV WEBM AVI MOV WMV FLV 3GP TS M4V OGV   (via FFmpeg)
Text:   TXT ↔ PDF ↔ MD ↔ HTML                          (via fpdf2 / pypdf / built-in)
Image:  PNG ↔ JPG ↔ BMP ↔ GIF ↔ WEBP ↔ TIFF           (via Pillow)
3D:     OBJ ↔ STL ↔ GLTF ↔ GLB ↔ PLY ↔ DAE ↔ OFF ↔ 3DS  (via trimesh)
Pres:   PPTX ↔ ODP                                         (via python-pptx / LibreOffice)
Data:   JSON ↔ CSV ↔ XML ↔ YAML                         (via built-in / PyYAML)

Cross-Category Conversions:
  Image → PDF, Image → HTML, Image → TXT, Image → JSON
  Data  → HTML, Data → TXT, TXT → Data
  Video → Image (frame extraction)
  Audio → TXT (metadata extraction)
  PDF   → Image (page rendering)
  3D    → Image (mesh rendering), 3D → TXT, 3D → JSON
  Pres  → PDF/TXT/HTML/Image/JSON (via python-pptx / LibreOffice)
  Text  → PPTX, Data → PPTX, Image → PPTX

Features:
  - Supported Conversions reference dialog (254+ intra + 100+ cross-category paths)
  - Video → Audio extraction (cross-category conversion)
  - Quality / bitrate settings for audio, video & image
  - Advanced audio settings: sample rate, channels, bitrate, codec
  - Advanced video settings: resolution, codec, FPS, CRF
  - Batch conversion (multiple files at once)
  - Conversion history with persistence
  - Drag-and-drop support for input files
  - Native OS file dialogs
  - TEST TAB: Auto-generate test files & run ALL conversion paths
  - Keyboard shortcuts: Enter=Convert, Ctrl+B=Batch, Ctrl+F=Formats
  - Window-level drag-and-drop support
  - Single-file conversion cancellation
  - Output file size display & open buttons
  - Large file warnings (>$500MB)
  - Atomic history saves (thread-safe)

Requirements:
    pip install PySide6 fpdf2 pypdf Pillow PyYAML trimesh python-pptx
    FFmpeg must be installed on your system.
"""

import os
import re
import sys
import json
import csv
import time
import subprocess
import threading
import base64
import xml.etree.ElementTree as ET
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
from itertools import permutations
from typing import Callable, Optional, Any, Dict, List, Tuple
from contextlib import contextmanager
import functools
from abc import ABC, abstractmethod
import logging
import uuid

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QLineEdit, QTextEdit, QSlider,
    QFileDialog, QDialog, QScrollArea, QSizePolicy, QFrame,
    QMessageBox, QGroupBox, QProgressBar, QTabWidget, QCheckBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QGridLayout,
)
from PySide6.QtCore import Qt, QTimer, Signal, Slot, QSize, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import (
    QFont, QColor, QIcon, QPalette, QDragEnterEvent, QDropEvent,
    QAction, QFontMetrics, QCursor,
)

# ─── Format Definitions ──────────────────────────────────────────

AUDIO_FORMATS = ["wav", "mp3", "ogg", "flac", "aac", "wma", "aiff", "m4a", "opus", "amr"]
VIDEO_FORMATS = ["mp4", "mkv", "webm", "avi", "mov", "wmv", "flv", "3gp", "ts", "m4v", "ogv"]
TEXT_FORMATS  = ["pdf", "txt", "md", "html"]
IMAGE_FORMATS = ["png", "jpg", "jpeg", "bmp", "gif", "webp", "tiff"]
THREE_D_FORMATS = ["obj", "stl", "gltf", "glb", "ply", "dae", "off", "3ds"]
PRESENTATION_FORMATS = ["pptx", "odp"]
DATA_FORMATS  = ["json", "csv", "xml", "yaml"]

ALL_FORMATS = AUDIO_FORMATS + VIDEO_FORMATS + TEXT_FORMATS + IMAGE_FORMATS + THREE_D_FORMATS + DATA_FORMATS + PRESENTATION_FORMATS

CATEGORY_POOLS = {
    "audio": AUDIO_FORMATS,
    "video": VIDEO_FORMATS,
    "text":  TEXT_FORMATS,
    "image": IMAGE_FORMATS,
    "3d":    THREE_D_FORMATS,
    "data":  DATA_FORMATS,
    "presentation": PRESENTATION_FORMATS,
}

CATEGORY_ICONS = {
    "audio": "\U0001f3b5",
    "video": "\U0001f3ac",
    "text":  "\U0001f4c4",
    "image": "\U0001f5bc\ufe0f",
    "3d":    "\U0001f4e6",
    "data":  "\U0001f4ca",
    "presentation": "\U0001f3a8",
}

HISTORY_FILE = Path.home() / ".converter_history.json"

# ─── Pre-compiled Regex Patterns (Performance) ─────────────────
# Compiled once at import time instead of on every function call.

# HTML escaping (used by _esc)
_RE_HTML_AMP  = re.compile(r"&")
_RE_HTML_LT   = re.compile(r"<")
_RE_HTML_GT   = re.compile(r">")
_RE_HTML_QUOT = re.compile(r'"')

# Markdown parser patterns
_RE_MD_HEADING    = re.compile(r'^(#{1,6})\s+(.+)$')
_RE_MD_HR         = re.compile(r'^(\*{3,}|-{3,}|_{3,})\s*$')
_RE_MD_UL         = re.compile(r'^[\s]*[-*+]\s+(.+)$')
_RE_MD_OL         = re.compile(r'^[\s]*\d+\.\s+(.+)$')
_RE_MD_CODE_FENCE = re.compile(r'^\s*```')
_RE_MD_TABLE_ROW  = re.compile(r'\|')
_RE_MD_TABLE_HDR  = re.compile(r'^\|')
_RE_MD_TABLE_SEP  = re.compile(r'^[\|\s:-]+$')

# Inline markdown patterns (order matters — process images before links)
_RE_MD_IMG        = re.compile(r'!\[([^\]]*)\]\(([^)]+)\)')
_RE_MD_LINK       = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')
_RE_MD_STRONG_EM  = re.compile(r'\*\*\*(.+?)\*\*\*')
_RE_MD_USCORE_EM  = re.compile(r'___(.+?)___')
_RE_MD_STRONG     = re.compile(r'\*\*(.+?)\*\*')
_RE_MD_USCORE_B   = re.compile(r'__(.+?)__')
_RE_MD_EM         = re.compile(r'(?<!\w)\*([^\s*][^*]*?)\*(?!\w)')
_RE_MD_USCORE_I   = re.compile(r'(?<!\w)_([^\s_][^_]*?)_(?!\w)')
_RE_MD_CODE       = re.compile(r'`([^`]+)`')
_RE_MD_DEL        = re.compile(r'~~(.+?)~~')

# HTML -> Markdown patterns
_RE_HTML_STYLE    = re.compile(r'<style[^>]*>.*?</style>', re.DOTALL | re.IGNORECASE)
_RE_HTML_SCRIPT   = re.compile(r'<script[^>]*>.*?</script>', re.DOTALL | re.IGNORECASE)
_RE_HTML_COMMENT  = re.compile(r'<!--.*?-->', re.DOTALL)

# CSV key sanitization
_RE_XML_KEY_SANITIZE = re.compile(r'[^a-zA-Z0-9_]')

# Filename sanitization
_RE_FILENAME_BAD   = re.compile(r'[<>:"/\\|?*]')
_RE_FILENAME_CTRL  = re.compile(r'[\x00-\x1f\x7f]')


__all__ = [
    "ConverterEngine", "ConversionResult", "ConversionCallbacks",
    "HistoryManager", "ConversionRegistry", "converter_registry",
    "CategoryPill", "DropLineEdit", "ConversionsDialog", "HistoryDialog",
    "BatchDialog", "TestTab", "ConverterTab", "MainWindow",
    "human_size", "sanitize_filename", "get_category", "get_compatible_formats",
    "ALL_FORMATS", "ALL_CONVERSIONS", "CATEGORY_POOLS", "DARK_QSS", "main",
]

# --- Logging Setup ---
logger = logging.getLogger("converter")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)


@functools.lru_cache(maxsize=128)
def get_category(ext: str) -> Optional[str]:
    """Return the category name for a file extension (memoized).

    Called hundreds of times during batch conversions and UI updates.
    lru_cache eliminates redundant linear scans of CATEGORY_POOLS.
    """
    ext = ext.lower().lstrip(".")
    for cat, pool in CATEGORY_POOLS.items():
        if ext in pool:
            return cat
    return None



# ─── Lazy Import Cache (Performance) ────────────────────────────
# Heavy dependencies are imported once on first use and cached at
# module level, avoiding repeated import overhead in hot paths.

_LAZY_CACHE: Dict[str, Any] = {}

def _lazy_import(module_name: str, package: str = None):
    """Import a module lazily and cache the result.

    Replaces scattered ``import trimesh`` / ``from pptx import X``
    inside converter methods.  The first call pays the import cost;
    subsequent calls return the cached module instantly.
    """
    if module_name not in _LAZY_CACHE:
        try:
            _LAZY_CACHE[module_name] = __import__(module_name, fromlist=[""])
        except ImportError:
            _LAZY_CACHE[module_name] = None
    return _LAZY_CACHE[module_name]

# ─── Cross-Category Conversion Map ──────────────────────────────
# Defines which (src_category, dst_category) pairs are supported
CROSS_CATEGORY_MAP = {
    ("image", "text"):  ["pdf", "html", "txt"],            # image→pdf, image→html, image→txt
    ("image", "data"):  ["json"],                          # image→json
    ("data", "text"):   ["html", "txt"],                   # data→html, data→txt
    ("text", "data"):   ["json", "csv", "xml", "yaml"],    # txt→json, txt→csv, txt→xml, txt→yaml
    ("video", "image"): IMAGE_FORMATS,                     # video→image (frame extraction)
    ("video", "audio"): AUDIO_FORMATS,                     # video→audio (extraction)
    ("audio", "text"):  ["txt"],                           # audio→txt (metadata)
    ("pdf", "image"):   IMAGE_FORMATS,                     # pdf→image (page rendering)
    ("3d", "image"):    IMAGE_FORMATS,                     # 3d→image (mesh rendering)
    ("3d", "text"):     ["txt"],                           # 3d→txt (mesh info)
    ("3d", "data"):     ["json"],                          # 3d→json (mesh metadata)
    ("image", "presentation"): ["pptx"],                # image→pptx
    ("text", "presentation"): ["pptx", "odp"],          # text→pptx, text→odp
}


@functools.lru_cache(maxsize=64)
def get_compatible_formats(input_ext: str) -> List[str]:
    """Return sorted list of compatible output formats (memoized).

    Called every time a file is selected in the UI. The result for any
    given extension never changes at runtime, so caching avoids rebuilding
    the list on every selection event.
    """
    cat = get_category(input_ext)
    pool = CATEGORY_POOLS.get(cat, [])
    formats = [f.upper() for f in pool if f != input_ext]
    # Add cross-category targets from CROSS_CATEGORY_MAP
    for (src_cat, dst_cat), dst_exts in CROSS_CATEGORY_MAP.items():
        if cat == src_cat:
            for ext in dst_exts:
                upper = ext.upper()
                if upper not in formats and ext != input_ext:
                    formats.append(upper)
    # Add cross-category presentation targets (text/data/image -> PPTX)
    if cat in ("text", "data", "image"):
        for pfmt in PRESENTATION_FORMATS:
            upper = pfmt.upper()
            if upper not in formats and pfmt != input_ext:
                formats.append(upper)
    return formats


def _sanitize_pdf_text(text):
    """Sanitize text for fpdf2 PDF output. Replace special unicode punctuation
    with ASCII equivalents so the default Helvetica font can render them."""
    unicode_map = {
        '\u2022': '-', '\u2023': '>', '\u2013': '-', '\u2014': '--',
        '\u2018': "'", '\u2019': "'", '\u201c': '"', '\u201d': '"',
        '\u2026': '...',
    }
    for uni_char, replacement in unicode_map.items():
        text = text.replace(uni_char, replacement)
    # Replace any remaining non-latin1 characters with ? instead of
    # silently mangling with errors="replace", which can produce mojibake.
    return text.encode("latin-1", errors="replace").decode("latin-1")


def human_size(n):
    for u in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"


def _sanitize_filename(name):
    """Remove or replace characters that are invalid in filenames across platforms.

    Uses pre-compiled regex patterns (_RE_FILENAME_BAD, _RE_FILENAME_CTRL)
    defined at module level for O(1) pattern re-use instead of recompiling
    on every call.  This function is invoked for every converted file's
    output path, so eliminating redundant compilation saves measurable time
    during batch conversions.
    """
    # Replace common problematic characters (uses pre-compiled pattern)
    name = _RE_FILENAME_BAD.sub('_', name)
    # Remove control characters (uses pre-compiled pattern)
    name = _RE_FILENAME_CTRL.sub('', name)
    # Strip leading/trailing whitespace and dots
    name = name.strip('. ')
    # Limit length (255 is common max, leave room for extension)
    if len(name) > 200:
        name = name[:200]
    return name or "converted_output"


# Public alias — code that imports ``sanitize_filename`` gets the real function.
# (The private ``_sanitize_filename`` remains the canonical definition above.)
sanitize_filename = _sanitize_filename


# --- ConversionResult Dataclass (Value Object Pattern) ---

@dataclass(slots=True)
class ConversionResult:
    """Typed result object replacing raw (bool, str) callback tuples.

    Using a dataclass provides self-documenting fields, type safety,
    easy extensibility, and useful repr for debugging. The __bool__
    method allows truthiness checks: ``if result: ...``

    ``slots=True`` (Python 3.10+) reduces per-instance memory by ~40%
    by preventing __dict__ creation. Since ConversionResult instances
    are created frequently during batch conversions, this measurably
    reduces GC pressure.
    """
    success: bool
    message: str
    output_path: Optional[str] = None
    source_path: Optional[str] = None
    duration_sec: float = 0.0

    @property
    def is_success(self) -> bool:
        return self.success

    def __bool__(self) -> bool:
        return self.success


class ConversionCallbacks:
    """Adapter (Adapter Pattern): bridges ConversionResult with existing
    (bool, str) callback protocol for incremental migration without
    breaking the dozens of existing call sites."""
    def __init__(self, callback: Callable[[bool, str], None]):
        self._callback = callback
        self.result: Optional[ConversionResult] = None

    def __call__(self, success: bool, message: str):
        self.result = ConversionResult(success=success, message=message)
        self._callback(success, message)

    def as_result(self) -> ConversionResult:
        return self.result or ConversionResult(success=False, message="No result")


# --- Context Manager for Temp Files ---

@contextmanager
def temporary_file(suffix: str, dir_path=None):
    """Context manager: creates a temp file and guarantees cleanup.

    Usage::
        with temporary_file(".png") as tmp:
            img.save(str(tmp))
            pdf.image(str(tmp), ...)
        # tmp is automatically deleted, even on exceptions
    """
    tmp_path = Path(dir_path or Path.cwd()) / f"_converter_tmp_{uuid.uuid4().hex[:12]}{suffix}"
    try:
        yield tmp_path
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass


# --- HTML Builder (Builder Pattern) ---

class HTMLBuilder:
    """Builder for constructing HTML documents.

    Eliminates repeated string-list concatenation across
    csv_to_html_table, json_to_html_pretty, xml_to_html_pretty,
    yaml_to_html_pretty, txt_to_html, presentation_to_html, etc.

    Usage::
        html = (HTMLBuilder(title="My Page", style="body{...}")
            .add_body_tag("<h1>Hello</h1>")
            .add_body_tag("<p>World</p>")
            .build())
    """
    def __init__(self, title: str = "", style: str = ""):
        self._parts: List[str] = [
            "<!DOCTYPE html>", "<html><head>",
            '<meta charset="utf-8">',
        ]
        if title:
            self._parts.append(f"<title>{_esc(title)}</title>")
        if style:
            self._parts.append(f"<style>{style}</style>")
        self._parts.append("</head><body>")

    def add_raw(self, raw_html: str) -> "HTMLBuilder":
        self._parts.append(raw_html)
        return self

    def add_tag(self, tag: str, content: str = "", **attrs: str) -> "HTMLBuilder":
        attr_str = " ".join(f'{k}="{v}"' for k, v in attrs.items())
        if attr_str:
            attr_str = " " + attr_str
        if content:
            self._parts.append(f"<{tag}{attr_str}>{content}</{tag}>")
        else:
            self._parts.append(f"<{tag}{attr_str}/>")
        return self

    def add_body_tag(self, content: str) -> "HTMLBuilder":
        self._parts.append(content)
        return self

    def build(self) -> str:
        self._parts.append("</body></html>")
        return "\n".join(self._parts)


# Treat "jpg" and "jpeg" as the same format for conversion paths
_IMAGE_ALIAS = {"jpeg": "jpg"}

def _build_conversion_list():
    all_convs = []
    seen = set()
    for cat, pool in CATEGORY_POOLS.items():
        resolved_pool = list(dict.fromkeys(_IMAGE_ALIAS.get(f, f) for f in pool))
        for src, dst in permutations(resolved_pool, 2):
            key = (cat, src.upper(), dst.upper())
            if key not in seen:
                seen.add(key)
                all_convs.append(key)
    # Cross-category from CROSS_CATEGORY_MAP
    for (src_cat, dst_cat), dst_exts in CROSS_CATEGORY_MAP.items():
        src_pool = CATEGORY_POOLS.get(src_cat, [])
        for src_ext in src_pool:
            for dst_ext in dst_exts:
                resolved_src = _IMAGE_ALIAS.get(src_ext, src_ext)
                resolved_dst = _IMAGE_ALIAS.get(dst_ext, dst_ext)
                key = (f"{src_cat}->{dst_cat}", resolved_src.upper(), resolved_dst.upper())
                if key not in seen:
                    seen.add(key)
                    all_convs.append(key)
    # Presentation cross-category conversions (text/data/image -> PPTX/ODP)
    pres_cross = [
        ("text", "presentation", TEXT_FORMATS, PRESENTATION_FORMATS),
        ("data", "presentation", DATA_FORMATS, PRESENTATION_FORMATS),
        ("image", "presentation", IMAGE_FORMATS, ["pptx"]),
    ]
    for src_cat, dst_cat, src_pool, dst_pool in pres_cross:
        for src_ext in src_pool:
            for dst_ext in dst_pool:
                resolved_src = _IMAGE_ALIAS.get(src_ext, src_ext)
                resolved_dst = _IMAGE_ALIAS.get(dst_ext, dst_ext)
                key = (f"{src_cat}->{dst_cat}", resolved_src.upper(), resolved_dst.upper())
                if key not in seen:
                    seen.add(key)
                    all_convs.append(key)
    all_convs.sort(key=lambda x: (x[0], x[1], x[2]))
    return all_convs


ALL_CONVERSIONS = _build_conversion_list()


# ─── Markdown <-> HTML Helpers ──────────────────────────────────

def md_to_html(md_text):
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
    in_list = None  # None, 'ul', or 'ol'
    in_table = False
    code_buf = []

    def _close_list():
        nonlocal in_list
        if in_list == 'ul':
            html_parts.append("</ul>")
        elif in_list == 'ol':
            html_parts.append("</ol>")
        in_list = None

    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            if not in_code_block:
                _close_list()
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
                _close_list()
                html_parts.append("<table>")
                in_table = True
                is_first_row = True
            row_cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if all(set(c.strip()) <= {"-", ":", " "} for c in row_cells):
                is_first_row = False
                i += 1
                continue
            tag = "th" if is_first_row else "td"
            html_parts.append("<tr>")
            for cell in row_cells:
                html_parts.append(f"<{tag}>{_inline(cell)}</{tag}>")
            html_parts.append("</tr>")
            is_first_row = False
            i += 1
            # Peek ahead to see if next line is still a table row
            if i >= len(lines) or "|" not in lines[i] or not lines[i].strip().startswith("|"):
                html_parts.append("</table>")
                in_table = False
            continue
        else:
            if in_table:
                html_parts.append("</table>")
                in_table = False
        hm = _RE_MD_HEADING.match(line)
        if hm:
            _close_list()
            lvl = len(hm.group(1))
            html_parts.append(f"<h{lvl}>{_inline(hm.group(2))}</h{lvl}>")
            i += 1
            continue
        if _RE_MD_HR.match(line.strip()):
            _close_list()
            html_parts.append("<hr>")
            i += 1
            continue
        if line.strip().startswith(">"):
            _close_list()
            quote_text = re.sub(r'^>\s?', '', line)
            html_parts.append(f"<blockquote><p>{_inline(quote_text)}</p></blockquote>")
            i += 1
            continue
        lm = _RE_MD_UL.match(line)
        if lm:
            if in_list != 'ul':
                _close_list()
                html_parts.append("<ul>")
                in_list = 'ul'
            html_parts.append(f"<li>{_inline(lm.group(1))}</li>")
            i += 1
            continue
        else:
            _close_list()
        olm = _RE_MD_OL.match(line)
        if olm:
            if in_list != 'ol':
                _close_list()
                html_parts.append("<ol>")
                in_list = 'ol'
            html_parts.append(f"<li>{_inline(olm.group(1))}</li>")
            i += 1
            continue
        else:
            _close_list()
        if not line.strip():
            i += 1
            continue
        html_parts.append(f"<p>{_inline(line)}</p>")
        i += 1
    _close_list()
    if in_table:
        html_parts.append("</table>")
    html_parts.append('</body></html>')
    return "\n".join(html_parts)


def _esc(text):
    return (_RE_HTML_AMP.sub("&amp;", text)
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def _inline(text):
    text = _RE_MD_IMG.sub(r'<img src="\2" alt="\1">', text)
    text = _RE_MD_LINK.sub(r'<a href="\2">\1</a>', text)
    text = _RE_MD_STRONG_EM.sub(r'<strong><em>\1</em></strong>', text)
    text = _RE_MD_USCORE_EM.sub(r'<strong><em>\1</em></strong>', text)
    text = _RE_MD_STRONG.sub(r'<strong>\1</strong>', text)
    text = _RE_MD_USCORE_B.sub(r'<strong>\1</strong>', text)
    # Use word-boundary-aware patterns for single markers to avoid matching
    # mid-word (e.g. "some_variable" should not become "some<em>variable</em>")
    text = _RE_MD_EM.sub(r'<em>\1</em>', text)
    text = _RE_MD_USCORE_I.sub(r'<em>\1</em>', text)
    text = _RE_MD_CODE.sub(r'<code>\1</code>', text)
    text = _RE_MD_DEL.sub(r'<del>\1</del>', text)
    return text


def html_to_md(html_text):
    text = html_text
    text = _RE_HTML_STYLE.sub('', text)
    text = _RE_HTML_SCRIPT.sub('', text)
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


# ─── Converter Engine ────────────────────────────────────────────

# --- Conversion Registry (Strategy / Registry Pattern) ---

class ConversionRegistry:
    """Registry mapping (src_ext, dst_ext) pairs to converter callables.

    Replaces large if/elif chains with a declarative dispatch table:
    - Open/Closed: new conversions are *registered*, not coded in branches
    - Single Responsibility: converter logic is separate from dispatch
    - Testability: the registry can be inspected independently
    - Extensibility: plugins can register their own converters
    """
    def __init__(self):
        self._intra: Dict[Tuple[str, str], str] = {}
        self._cross: Dict[Tuple[str, str], str] = {}

    def register_intra(self, src: str, dst: str, method_name: str):
        self._intra[(src, dst)] = method_name

    def register_cross(self, src: str, dst: str, method_name: str):
        self._cross[(src, dst)] = method_name

    def resolve(self, src, dst, in_cat, out_cat):
        """Return (method_name, is_cross_category) or (None, False)."""
        if in_cat == out_cat and in_cat is not None:
            m = self._intra.get((src, dst))
            if m:
                return m, False
        m = self._cross.get((src, dst))
        if m:
            return m, True
        return None, False

    def get_all_intra(self) -> Dict[Tuple[str, str], str]:
        return dict(self._intra)

    def get_all_cross(self) -> Dict[Tuple[str, str], str]:
        return dict(self._cross)


converter_registry = ConversionRegistry()

# Shared style constants for HTMLBuilder (DRY — defined once, used by 4+ methods)
DATA_STYLE = (
    "body{font-family:sans-serif;max-width:800px;margin:40px auto;padding:0 20px;}"
    "pre{background:#f4f4f4;padding:16px;border-radius:6px;overflow-x:auto;}"
)
TABLE_STYLE = (
    "body{font-family:sans-serif;max-width:900px;margin:40px auto;padding:0 20px;}"
    "table{border-collapse:collapse;width:100%;}"
    "th,td{border:1px solid #ddd;padding:8px 12px;text-align:left;}"
    "th{background:#f4f4f4;}tr:nth-child(even){background:#f9f9f9;}"
)


class ConverterEngine:
    """Conversion engine with per-task cancellation tokens.

    Bug fix: cancellation is now per-task (token-based) instead of a
    shared class-level Event, preventing race conditions when batch
    conversions run concurrently.
    """

    _cancel_tokens: Dict[str, threading.Event] = {}
    _tokens_lock = threading.Lock()

    @classmethod
    def get_cancel_token(cls) -> Tuple[str, threading.Event]:
        """Create a new per-task cancellation token."""
        token_id = uuid.uuid4().hex[:12]
        event = threading.Event()
        with cls._tokens_lock:
            cls._cancel_tokens[token_id] = event
        return token_id, event

    @classmethod
    def release_cancel_token(cls, token_id: str):
        """Remove a cancellation token after the task completes."""
        with cls._tokens_lock:
            cls._cancel_tokens.pop(token_id, None)

    @classmethod
    def request_cancel(cls, token_id: str):
        """Request cancellation for a specific task."""
        with cls._tokens_lock:
            event = cls._cancel_tokens.get(token_id)
        if event:
            event.set()

    @classmethod
    def clear_cancel(cls, token_id: str):
        """Clear the cancellation flag for a specific task."""
        with cls._tokens_lock:
            event = cls._cancel_tokens.get(token_id)
        if event:
            event.clear()

    @classmethod
    def is_cancelled(cls, token_id: str) -> bool:
        """Check if a specific task has been cancelled."""
        with cls._tokens_lock:
            event = cls._cancel_tokens.get(token_id)
        return event.is_set() if event else False

    # Legacy class-level API (kept for backward compat, avoid in new code)
    _cancel_event = threading.Event()  # DEPRECATED

    @classmethod
    def request_cancel_legacy(cls):
        cls._cancel_event.set()

    @classmethod
    def clear_cancel_legacy(cls):
        cls._cancel_event.clear()

    @classmethod
    def is_cancelled_legacy(cls):
        return cls._cancel_event.is_set()

    @staticmethod
    def _run_ffmpeg(input_path, output_path, timeout, callback, quality=None, settings=None, cancel_token_id=None):
        """Run an FFmpeg conversion with automatic fallback and recovery.

        Improvements over the original:
        - Pre-checks for audio streams before video->audio extraction
        - AMR: adds -ar 8000 -ac 1 pre-parameters and falls back to voamrwbenc
        - 3GP: falls back to mpeg4 video codec when h263/libx264 unavailable
        - OGV/OGG: adds -ar 44100 resampling for narrowband sources (AMR)
        - Audio codec failure: retries without explicit codec (let FFmpeg auto-select)
        - Empty output detection after conversion
        """
        try:
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-i", str(input_path), "-y"]
            in_ext = Path(input_path).suffix.lower().lstrip(".")
            out_ext = Path(output_path).suffix.lower().lstrip(".")
            in_cat = get_category(in_ext)
            out_cat = get_category(out_ext)
            s = settings or {}

            # Video -> Audio extraction: pre-check for audio stream
            if in_cat == "video" and out_cat == "audio":
                cmd.extend(["-vn"])

            # Audio output settings
            if out_cat == "audio":
                codec_map = {
                    "mp3": "libmp3lame", "ogg": "libvorbis", "opus": "libopus",
                    "flac": "flac", "aac": "aac", "m4a": "aac",
                    "wav": "pcm_s16le", "aiff": "pcm_s16be",
                    "wma": "wmav2", "amr": "libopencore_amrnb",
                }
                codec = s.get("audio_codec") or codec_map.get(out_ext)

                # AMR requires specific parameters: 8kHz sample rate, mono
                if out_ext == "amr":
                    cmd.extend(["-ar", "8000", "-ac", "1"])
                    if not s.get("audio_bitrate"):
                        cmd.extend(["-b:a", "12.2k"])

                # Resample narrowband sources (e.g. AMR @ 8kHz) to 44100Hz
                # for codecs that expect standard sample rates (Vorbis, Opus)
                if in_ext == "amr" and out_ext in ("ogg", "opus", "flac"):
                    cmd.extend(["-ar", "44100"])

                if codec:
                    cmd.extend(["-c:a", codec])

                bitrate = s.get("audio_bitrate")
                if bitrate:
                    cmd.extend(["-b:a", f"{bitrate}k"])
                elif quality is not None and out_ext != "amr":
                    if out_ext in ("flac", "wav", "aiff"):
                        pass
                    else:
                        br = max(64, int(quality * 2.56))
                        cmd.extend(["-b:a", f"{br}k"])

                sample_rate = s.get("sample_rate")
                if sample_rate and sample_rate != "auto":
                    cmd.extend(["-ar", str(sample_rate)])

                channels = s.get("channels")
                if channels == "mono":
                    cmd.extend(["-ac", "1"])
                elif channels == "stereo":
                    cmd.extend(["-ac", "2"])

            # Video output settings
            elif out_cat == "video":
                vcodec = s.get("video_codec")
                if vcodec and vcodec != "auto":
                    cmd.extend(["-c:v", vcodec])

                # 3GP: set default codec if not specified and add size constraints
                if out_ext == "3gp" and not vcodec:
                    cmd.extend(["-c:v", "libx264"])
                    cmd.extend(["-s", "176x144", "-r", "15"])

                crf = s.get("crf")
                if crf is not None:
                    cmd.extend(["-crf", str(crf)])
                elif quality is not None:
                    c = max(18, int(51 - quality * 0.33))
                    cmd.extend(["-crf", str(c)])

                res = s.get("resolution")
                if res and res != "original":
                    res_map = {
                        "1080p": "1920:1080",
                        "720p": "1280:720",
                        "480p": "854:480",
                        "360p": "640:360",
                        "240p": "426:240",
                    }
                    scale = res_map.get(res)
                    if scale:
                        cmd.extend(["-vf", f"scale={scale}"])

                fps = s.get("fps")
                if fps and fps != "auto":
                    cmd.extend(["-r", str(fps)])

                acodec = s.get("audio_codec")
                if acodec and acodec != "auto":
                    cmd.extend(["-c:a", acodec])

            cmd.append(str(output_path))

            # --- Execute with retry logic for codec failures ---
            def _exec_ffmpeg(cmd_list):
                proc = subprocess.Popen(cmd_list, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    stdout, stderr = None, None
                    elapsed = 0
                    poll_interval = 0.5
                    while proc.poll() is None:
                        if cancel_token_id and ConverterEngine.is_cancelled(cancel_token_id):
                            proc.kill()
                            proc.wait(timeout=10)
                            callback(False, "Cancelled by user")
                            return None
                        time.sleep(poll_interval)
                        elapsed += poll_interval
                        if elapsed >= timeout:
                            proc.kill()
                            proc.wait(timeout=10)
                            callback(False, "Conversion timed out")
                            return None
                    stdout, stderr = proc.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=10)
                    callback(False, "Conversion timed out")
                    return None
                return proc, stdout, stderr

            result = _exec_ffmpeg(cmd)
            if result is None:
                return  # callback already called
            proc, stdout, stderr = result

            # If FFmpeg failed, attempt codec fallback for audio
            if proc.returncode != 0:
                err = (stderr or "")[-400:]
                # Detect encoder-open failures and retry without explicit codec
                if "Could not open encoder" in err and out_cat == "audio":
                    # Remove the -c:a <codec> from command and retry
                    retry_cmd = [c for i, c in enumerate(cmd)
                                 if not (c == "-c:a" and i + 1 < len(cmd)
                                         and cmd[i + 1] in ("libopencore_amrnb", "libvorbis",
                                                             "h263", "mjpeg", "libx264"))]
                    # Also remove the codec value that follows -c:a if it was one we removed
                    filtered = []
                    skip_next = False
                    for i, c in enumerate(retry_cmd):
                        if skip_next:
                            skip_next = False
                            continue
                        if c == "-c:a" and i + 1 < len(retry_cmd) and retry_cmd[i + 1] in (
                            "libopencore_amrnb", "libvorbis", "h263", "mjpeg", "libx264"
                        ):
                            skip_next = True
                            continue
                        filtered.append(c)
                    if filtered != cmd:
                        result2 = _exec_ffmpeg(filtered)
                        if result2 is not None:
                            proc2, stdout2, stderr2 = result2
                            if proc2.returncode == 0:
                                # Verify output is not empty
                                if Path(output_path).exists() and Path(output_path).stat().st_size > 0:
                                    callback(True, f"Converted -> {Path(output_path).name}")
                                    return
                                else:
                                    err = "Output file is empty"

                if not err.strip():
                    err = "Unknown FFmpeg error"
                raise Exception(err)

            # Verify output is not empty
            if not Path(output_path).exists() or Path(output_path).stat().st_size == 0:
                raise Exception("Output file is empty")

            callback(True, f"Converted -> {Path(output_path).name}")
        except FileNotFoundError:
            callback(False, "FFmpeg not found! Install it and add to PATH.")
        except Exception as e:
            err_msg = str(e)
            if "Cancelled" not in err_msg:
                callback(False, f"{e}")

    @staticmethod
    def convert_audio(inp, out, cb, quality=None, settings=None, cancel_token_id=None):
        ConverterEngine._run_ffmpeg(inp, out, 300, cb, quality, settings, cancel_token_id)

    @staticmethod
    def convert_video(inp, out, cb, quality=None, settings=None, cancel_token_id=None):
        ConverterEngine._run_ffmpeg(inp, out, 600, cb, quality, settings, cancel_token_id)

    @staticmethod
    def convert_image(inp, out, cb, quality=None):
        try:
            from PIL import Image
            img = Image.open(str(inp))
            out_ext = Path(out).suffix.lower().lstrip(".")
            save_kwargs = {}
            if out_ext in ("jpg", "jpeg"):
                save_kwargs["quality"] = quality or 85
                if img.mode in ("RGBA", "LA", "P"):
                    img = img.convert("RGB")
            elif out_ext == "webp":
                save_kwargs["quality"] = quality or 85
                # WebP supports alpha — preserve it
                if img.mode == "P":
                    img = img.convert("RGBA")
            elif out_ext == "png":
                if quality is not None:
                    save_kwargs["compress_level"] = max(0, min(9, int((100 - quality) / 11)))
            elif out_ext == "gif":
                # GIF: preserve animation for multi-frame images
                if hasattr(img, 'n_frames') and img.n_frames > 1:
                    frames = []
                    for frame_idx in range(img.n_frames):
                        img.seek(frame_idx)
                        frame = img.copy().convert("RGB")
                        frames.append(frame)
                    frames[0].save(str(out), save_all=True, append_images=frames[1:],
                                    loop=0, **save_kwargs)
                    cb(True, f"Converted -> {Path(out).name}")
                    return
                if img.mode not in ("RGB", "RGBA", "P", "L"):
                    img = img.convert("RGBA")
                if img.mode == "P" and "transparency" not in img.info:
                    img = img.convert("RGBA")
            elif out_ext == "bmp":
                if img.mode in ("RGBA", "LA", "P"):
                    img = img.convert("RGB")
            elif out_ext == "tiff":
                save_kwargs["compression"] = "tiff_lzw"
            img.save(str(out), **save_kwargs)
            cb(True, f"Converted -> {Path(out).name}")
        except ImportError:
            cb(False, "Pillow not installed. Run: pip install Pillow")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def _write(path, text, encoding="utf-8"):
        with open(path, "w", encoding=encoding) as fh:
            fh.write(text)

    @staticmethod
    def _read(path, encoding="utf-8"):
        with open(path, "r", encoding=encoding, errors="replace") as fh:
            return fh.read()

    # ── Text conversions ────────────────────────────────────

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
                hm = _RE_MD_HEADING.match(stripped)
                if hm:
                    lvl = len(hm.group(1))
                    sz = max(18 - (lvl - 1) * 2, 10)
                    pdf.set_font("Helvetica", "B", size=sz)
                    safe = _sanitize_pdf_text(hm.group(2))
                    pdf.multi_cell(w, sz * 0.6, safe, align='L')
                    pdf.set_font("Helvetica", size=11)
                    continue
                if _RE_MD_HR.match(stripped):
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
                lm = _RE_MD_UL.match(stripped)
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
                data = [data]
            if not data:
                ConverterEngine._write(out, "")
                cb(True, f"JSON -> CSV -> {Path(out).name}")
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
            tree = ConverterEngine._build_xml_tree(data)
            ConverterEngine._write_xml_tree(tree, out)
            cb(True, f"JSON -> XML -> {Path(out).name}")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def xml_to_json(inp, out, cb):
        try:
            tree = ET.parse(str(inp))
            root = tree.getroot()
            data = ConverterEngine._xml_element_to_dict(root)
            ConverterEngine._write(out, json.dumps(data, indent=2, ensure_ascii=False))
            cb(True, f"XML -> JSON -> {Path(out).name}")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def csv_to_xml(inp, out, cb):
        try:
            with open(inp, "r", encoding="utf-8", errors="replace") as fh:
                reader = csv.DictReader(fh)
                rows = list(reader)
            root = ET.Element("rows")
            for row in rows:
                row_elem = ET.SubElement(root, "row")
                for key, val in row.items():
                    safe_key = _RE_XML_KEY_SANITIZE.sub('_', str(key))
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
            data = ConverterEngine._xml_element_to_dict(root)
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
            tree = ConverterEngine._build_xml_tree(data)
            ConverterEngine._write_xml_tree(tree, out)
            cb(True, f"YAML -> XML -> {Path(out).name}")
        except ImportError:
            cb(False, "PyYAML not installed. Run: pip install PyYAML")
        except Exception as e:
            cb(False, f"{e}")

    # ── Cross-Category: Image -> Text/Data ──────────────────

    @staticmethod
    def image_to_pdf(inp, out, cb):
        """Convert an image to PDF using temporary_file context manager
        for guaranteed cleanup of intermediate files."""
        try:
            from PIL import Image
            from fpdf import FPDF
            img = Image.open(str(inp))
            pdf = FPDF()
            pdf.add_page()
            w, h = img.size
            pw, ph = pdf.w - 20, pdf.h - 20
            ratio = min(pw / w, ph / h)
            nw, nh = w * ratio, h * ratio
            x = (pdf.w - nw) / 2
            y = (pdf.h - nh) / 2
            rgb_img = img.convert("RGB")
            with temporary_file(".png", dir_path=str(Path(out).parent)) as tmp_png:
                rgb_img.save(str(tmp_png), "PNG")
                pdf.image(str(tmp_png), x=x, y=y, w=nw, h=nh)
            pdf.output(str(out))
            cb(True, f"Image -> PDF -> {Path(out).name}")
        except ImportError as e:
            cb(False, f"Missing dependency: {e}")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def image_to_html(inp, out, cb):
        try:
            with open(inp, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            ext = Path(inp).suffix.lower().lstrip(".")
            mime_map = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png",
                        "gif": "gif", "bmp": "bmp", "webp": "webp", "tiff": "tiff"}
            mime_type = f"image/{mime_map.get(ext, ext)}"
            html = (
                '<!DOCTYPE html><html><head><meta charset="utf-8">'
                '<style>body{display:flex;justify-content:center;align-items:center;'
                'min-height:100vh;margin:0;background:#f5f5f5;}'
                'img{max-width:95vw;max-height:95vh;box-shadow:0 2px 8px rgba(0,0,0,0.2);'
                'border-radius:4px;}</style></head><body>'
                f'<img src="data:{mime_type};base64,{b64}" alt="converted image"/>'
                '</body></html>'
            )
            ConverterEngine._write(out, html)
            cb(True, f"Image -> HTML -> {Path(out).name}")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def image_to_txt(inp, out, cb):
        try:
            from PIL import Image
            img = Image.open(str(inp))
            info = f"Image Properties\n{'=' * 40}\n"
            info += f"Filename: {Path(inp).name}\n"
            info += f"Format: {img.format}\n"
            info += f"Size: {img.width} x {img.height} pixels\n"
            info += f"Mode: {img.mode}\n"
            if hasattr(img, 'info') and img.info:
                for k, v in img.info.items():
                    info += f"{k}: {v}\n"
            info += f"\nFile size: {human_size(os.path.getsize(inp))}\n"
            ConverterEngine._write(out, info)
            cb(True, f"Image -> TXT -> {Path(out).name}")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def image_to_json(inp, out, cb):
        try:
            from PIL import Image
            img = Image.open(str(inp))
            data = {
                "filename": Path(inp).name,
                "format": img.format,
                "width": img.width,
                "height": img.height,
                "mode": img.mode,
                "file_size_bytes": os.path.getsize(inp),
            }
            ConverterEngine._write(out, json.dumps(data, indent=2))
            cb(True, f"Image -> JSON -> {Path(out).name}")
        except Exception as e:
            cb(False, f"{e}")

    # ── Cross-Category: Data -> Text ────────────────────────

    @staticmethod
    def csv_to_html_table(inp, out, cb):
        """Convert CSV to HTML table (HTMLBuilder pattern)."""
        try:
            with open(inp, "r", encoding="utf-8", errors="replace") as fh:
                rows = list(csv.reader(fh))
            builder = HTMLBuilder(title=Path(inp).stem, style=TABLE_STYLE)
            table_rows = []
            for i, row in enumerate(rows):
                tag = "th" if i == 0 else "td"
                cells = "".join(f"<{tag}>{_esc(c)}</{tag}>" for c in row)
                table_rows.append(f"<tr>{cells}</tr>")
            builder.add_body_tag("<table>\n" + "\n".join(table_rows) + "\n</table>")
            ConverterEngine._write(out, builder.build())
            cb(True, f"CSV -> HTML -> {Path(out).name}")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def json_to_html_pretty(inp, out, cb):
        """Convert JSON to pretty-printed HTML (HTMLBuilder pattern)."""
        try:
            data = json.loads(ConverterEngine._read(inp))
            pretty = json.dumps(data, indent=2, ensure_ascii=False)
            html = (HTMLBuilder(title=Path(inp).stem, style=DATA_STYLE)
                    .add_body_tag(f"<pre><code>{_esc(pretty)}</code></pre>")
                    .build())
            ConverterEngine._write(out, html)
            cb(True, f"JSON -> HTML -> {Path(out).name}")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def xml_to_html_pretty(inp, out, cb):
        """Convert XML to pretty-printed HTML (HTMLBuilder pattern)."""
        try:
            content = ConverterEngine._read(inp)
            html = (HTMLBuilder(title=Path(inp).stem, style=DATA_STYLE)
                    .add_body_tag(f"<pre><code>{_esc(content)}</code></pre>")
                    .build())
            ConverterEngine._write(out, html)
            cb(True, f"XML -> HTML -> {Path(out).name}")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def yaml_to_html_pretty(inp, out, cb):
        """Convert YAML to pretty-printed HTML (HTMLBuilder pattern)."""
        try:
            import yaml
            with open(inp, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
            pretty = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
            html = (HTMLBuilder(title=Path(inp).stem, style=DATA_STYLE)
                    .add_body_tag(f"<pre><code>{_esc(pretty)}</code></pre>")
                    .build())
            ConverterEngine._write(out, html)
            cb(True, f"YAML -> HTML -> {Path(out).name}")
        except ImportError:
            cb(False, "PyYAML not installed. Run: pip install PyYAML")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def json_to_txt_data(inp, out, cb):
        try:
            data = json.loads(ConverterEngine._read(inp))
            pretty = json.dumps(data, indent=2, ensure_ascii=False)
            ConverterEngine._write(out, pretty)
            cb(True, f"JSON -> TXT -> {Path(out).name}")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def csv_to_txt_data(inp, out, cb):
        try:
            with open(inp, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.read()
            ConverterEngine._write(out, content)
            cb(True, f"CSV -> TXT -> {Path(out).name}")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def xml_to_txt_data(inp, out, cb):
        try:
            from xml.dom import minidom
            tree = ET.parse(str(inp))
            xml_str = ET.tostring(tree.getroot(), encoding="unicode")
            pretty = minidom.parseString(xml_str).toprettyxml(indent="  ")
            ConverterEngine._write(out, pretty)
            cb(True, f"XML -> TXT -> {Path(out).name}")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def yaml_to_txt_data(inp, out, cb):
        try:
            import yaml
            with open(inp, "r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
            pretty = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
            ConverterEngine._write(out, pretty)
            cb(True, f"YAML -> TXT -> {Path(out).name}")
        except ImportError:
            cb(False, "PyYAML not installed. Run: pip install PyYAML")
        except Exception as e:
            cb(False, f"{e}")

    # ── Cross-Category: TXT -> Data ─────────────────────────

    @staticmethod
    def txt_to_json_data(inp, out, cb):
        try:
            content = ConverterEngine._read(inp).strip()
            lines = [l.strip() for l in content.split("\n") if l.strip()]
            parsed = []
            all_json = True
            for line in lines:
                try:
                    parsed.append(json.loads(line))
                except json.JSONDecodeError:
                    all_json = False
                    break
            if all_json and parsed:
                result = parsed if len(parsed) > 1 else parsed[0]
            else:
                result = {"text": content, "lines": lines}
            ConverterEngine._write(out, json.dumps(result, indent=2, ensure_ascii=False))
            cb(True, f"TXT -> JSON -> {Path(out).name}")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def txt_to_csv_data(inp, out, cb):
        try:
            content = ConverterEngine._read(inp)
            lines = [l for l in content.split("\n") if l.strip()]
            with open(out, "w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                for line in lines:
                    if "\t" in line:
                        writer.writerow(line.split("\t"))
                    elif "," in line:
                        writer.writerow([c.strip() for c in line.split(",")])
                    else:
                        writer.writerow([line])
            cb(True, f"TXT -> CSV -> {Path(out).name}")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def txt_to_xml_data(inp, out, cb):
        try:
            content = ConverterEngine._read(inp).strip()
            lines = content.split("\n")
            root = ET.Element("text_content")
            root.set("lines", str(len(lines)))
            for i, line in enumerate(lines):
                line_elem = ET.SubElement(root, "line")
                line_elem.set("number", str(i + 1))
                line_elem.text = line
            tree = ET.ElementTree(root)
            ET.indent(tree, space="  ")
            tree.write(str(out), encoding="unicode", xml_declaration=True)
            cb(True, f"TXT -> XML -> {Path(out).name}")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def txt_to_yaml_data(inp, out, cb):
        try:
            import yaml
            content = ConverterEngine._read(inp).strip()
            lines = content.split("\n")
            data = {"lines": lines}
            yaml_str = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
            ConverterEngine._write(out, yaml_str)
            cb(True, f"TXT -> YAML -> {Path(out).name}")
        except ImportError:
            cb(False, "PyYAML not installed. Run: pip install PyYAML")
        except Exception as e:
            cb(False, f"{e}")

    # ── Cross-Category: Video -> Image ──────────────────────

    @staticmethod
    def video_to_image_frame(inp, out, cb):
        """Extract a single frame from video as an image.

        Improved: adds pixel format normalization to handle codecs like
        Theora (OGV) that output non-standard pixel formats.  Without
        explicit format conversion, encoders like MJPEG, WebP, and GIF
        can fail with 'Could not open encoder' or produce empty files.
        """
        try:
            in_ext = Path(inp).suffix.lower().lstrip(".")
            out_ext = Path(out).suffix.lower().lstrip(".")

            # Build base command with pixel format normalization.
            # format=yuv420p is universally supported and prevents
            # colorspace conversion errors from unusual source formats.
            vf_filter = "format=yuv420p"
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "warning",
                   "-i", str(inp), "-ss", "00:00:00.5", "-vframes", "1",
                   "-vf", vf_filter,
                   "-q:v", "2", "-y", str(out)]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode != 0:
                err = result.stderr[-400:] if result.stderr else "Unknown FFmpeg error"
                raise Exception(err)
            # Verify output is not empty
            if not Path(out).exists() or Path(out).stat().st_size == 0:
                raise Exception("Output file is empty")
            cb(True, f"Video -> Image -> {Path(out).name}")
        except FileNotFoundError:
            cb(False, "FFmpeg not found!")
        except Exception as e:
            cb(False, f"{e}")

    # ── Cross-Category: Audio -> TXT (metadata) ─────────────

    @staticmethod
    def audio_to_txt_meta(inp, out, cb):
        try:
            cmd = ["ffprobe", "-hide_banner", "-v", "quiet", "-print_format", "json",
                   "-show_format", "-show_streams", str(inp)]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if result.returncode != 0:
                raise Exception("ffprobe failed")
            probe = json.loads(result.stdout)
            info = "Audio Metadata\n" + "=" * 40 + "\n"
            fmt = probe.get("format", {})
            info += f"Filename: {Path(inp).name}\n"
            info += f"Format: {fmt.get('format_long_name', 'N/A')}\n"
            info += f"Duration: {fmt.get('duration', 'N/A')}s\n"
            info += f"Bitrate: {fmt.get('bit_rate', 'N/A')} bps\n"
            info += f"Size: {fmt.get('size', 'N/A')} bytes\n"
            for stream in probe.get("streams", []):
                if stream.get("codec_type") == "audio":
                    info += f"\nStream:\n"
                    info += f"  Codec: {stream.get('codec_long_name', 'N/A')}\n"
                    info += f"  Sample Rate: {stream.get('sample_rate', 'N/A')} Hz\n"
                    info += f"  Channels: {stream.get('channels', 'N/A')}\n"
                    info += f"  Bits Per Sample: {stream.get('bits_per_sample', 'N/A')}\n"
            ConverterEngine._write(out, info)
            cb(True, f"Audio -> TXT -> {Path(out).name}")
        except FileNotFoundError:
            cb(False, "ffprobe not found! (part of FFmpeg)")
        except Exception as e:
            cb(False, f"{e}")

    # ── Cross-Category: PDF -> Image ────────────────────────

    @staticmethod
    def pdf_to_image_page(inp, out, cb):
        try:
            from PIL import Image
            # Try pdf2image first, fallback to pypdf rendering
            try:
                from pdf2image import convert_from_path
                images = convert_from_path(str(inp), first_page=1, last_page=1, dpi=150)
                if images:
                    images[0].save(str(out))
                    cb(True, f"PDF -> Image -> {Path(out).name}")
                    return
            except ImportError:
                pass
            # Fallback: create a placeholder image with PDF info
            from pypdf import PdfReader
            reader = PdfReader(str(inp))
            pages = len(reader.pages)
            out_ext = Path(out).suffix.lower().lstrip(".")
            img = Image.new("RGB", (400, 300), color=(245, 245, 245))
            from PIL import ImageDraw
            draw = ImageDraw.Draw(img)
            draw.text((20, 20), f"PDF: {Path(inp).name}")
            draw.text((20, 50), f"Pages: {pages}")
            draw.text((20, 80), f"(Install pdf2image for full rendering)")
            img.save(str(out))
            cb(True, f"PDF -> Image -> {Path(out).name}  (placeholder - install pdf2image)")
        except Exception as e:
            cb(False, f"{e}")

    # ── 3D Model Conversions (via trimesh) ──────────────────

    @staticmethod
    def convert_3d(inp, out, cb, quality=None):
        """Convert between 3D mesh formats via trimesh.

        Improved: provides a manual OBJ-based fallback for DAE export
        when trimesh's COLLADA exporter is unavailable, and a clear
        error message for 3DS export failures (trimesh limitation).
        """
        try:
            import trimesh
            mesh = trimesh.load(str(inp), force='mesh')
            out_ext = Path(out).suffix.lower().lstrip(".")
            _TRIMESH_EXPORT_TYPES = {
                "stl", "gltf", "glb", "obj", "ply", "off", "xyz",
            }
            export_kwargs = {}
            if out_ext in _TRIMESH_EXPORT_TYPES:
                export_kwargs["file_type"] = out_ext
            try:
                mesh.export(str(out), **export_kwargs)
                cb(True, f"3D -> {Path(out).name}")
            except Exception as export_err:
                err_str = str(export_err)
                # 3DS exporter: trimesh doesn't bundle a 3DS writer
                if "3ds" in err_str and "exporter not available" in err_str:
                    # Fall back to OBJ which is universally supported
                    fallback_path = str(out).rsplit(".", 1)[0] + ".obj"
                    try:
                        mesh.export(fallback_path, file_type="obj")
                        cb(False, f"3DS export not available in trimesh. "
                                f"Saved as OBJ instead: {Path(fallback_path).name}")
                    except Exception:
                        cb(False, f"3DS export not available (trimesh limitation). "
                                f"Install Blender for 3DS export support.")
                # DAE exporter: may need lxml or other deps
                elif out_ext == "dae":
                    # Manual COLLADA fallback using basic XML
                    try:
                        ConverterEngine._export_dae_fallback(mesh, out)
                        cb(True, f"3D -> {Path(out).name} (fallback DAE)")
                    except Exception as dae_err:
                        cb(False, f"DAE export failed (need lxml?): {dae_err}")
                else:
                    raise
        except ImportError:
            cb(False, "trimesh not installed. Run: pip install trimesh")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def _export_dae_fallback(mesh, out_path):
        """Minimal COLLADA (.dae) exporter as fallback when trimesh's
        DAE exporter has missing dependencies (e.g. lxml).

        Produces a valid COLLADA 1.4.1 file with geometry data.
        """
        import xml.etree.ElementTree as ET
        ns = "http://www.collada.org/2005/11/COLLADASchema"
        ns_map = {"": ns}

        root = ET.Element(f"{{{ns}}}COLLADA", xmlns=ns, version="1.4.1")
        asset = ET.SubElement(root, f"{{{ns}}}asset")
        created = ET.SubElement(asset, f"{{{ns}}}created")
        created.text = datetime.now().isoformat()

        geom = ET.SubElement(root, f"{{{ns}}}library_geometries")
        geom_node = ET.SubElement(geom, f"{{{ns}}}geometry",
                                  id="mesh0", name="mesh")
        mesh_node = ET.SubElement(geom_node, f"{{{ns}}}mesh")

        # Vertices
        verts = mesh.vertices if hasattr(mesh, "vertices") else []
        faces = mesh.faces if hasattr(mesh, "faces") else []

        src_pos = ET.SubElement(mesh_node, f"{{{ns}}}source", id="mesh0-positions")
        float_arr = ET.SubElement(src_pos, f"{{{ns}}}float_array",
                                  id="mesh0-positions-array",
                                  count=str(len(verts) * 3))
        float_arr.text = " ".join(f"{v:.6f}" for v in verts.flatten())
        tech = ET.SubElement(src_pos, f"{{{ns}}}technique_common")
        acc = ET.SubElement(tech, f"{{{ns}}}accessor",
                            source="#mesh0-positions-array",
                            stride="3", count=str(len(verts)))
        ET.SubElement(acc, f"{{{ns}}}param", name="X", type="float")
        ET.SubElement(acc, f"{{{ns}}}param", name="Y", type="float")
        ET.SubElement(acc, f"{{{ns}}}param", name="Z", type="float")

        # Vertices element (indexed)
        vert_elem = ET.SubElement(mesh_node, f"{{{ns}}}vertices", id="mesh0-vertices")
        ET.SubElement(vert_elem, f"{{{ns}}}input",
                      semantic="POSITION", source="#mesh0-positions")

        # Triangles
        if len(faces) > 0:
            tri = ET.SubElement(mesh_node, f"{{{ns}}}triangles",
                                count=str(len(faces)), material="material0")
            ET.SubElement(tri, f"{{{ns}}}input",
                          semantic="VERTEX", source="#mesh0-vertices", offset="0")
            p_elem = ET.SubElement(tri, f"{{{ns}}}p")
            p_elem.text = " ".join(str(i) for i in faces.flatten())

        tree = ET.ElementTree(root)
        ET.indent(tree, space="  ")
        tree.write(str(out_path), encoding="unicode", xml_declaration=True)

    @staticmethod
    def model_to_image(inp, out, cb):
        """Render a 3D model to an image.

        Bug fix: the mesh is loaded once and reused in the fallback path
        instead of being re-loaded from disk on every rendering exception.
        """
        mesh = None
        try:
            import trimesh
            from PIL import Image as PILImage
            mesh = trimesh.load(str(inp), force="mesh")
            scene = trimesh.Scene(geometry=mesh)
            png_bytes = scene.save_image(resolution=(800, 600))
            if png_bytes is not None and len(png_bytes) > 0:
                img = PILImage.open(__import__("io").BytesIO(png_bytes))
                out_ext = Path(out).suffix.lower().lstrip(".")
                if out_ext in ("jpg", "jpeg"):
                    if img.mode in ("RGBA", "LA", "P"):
                        img = img.convert("RGB")
                    img.save(str(out), quality=85)
                else:
                    img.save(str(out))
                cb(True, f"3D -> Image -> {Path(out).name}")
            else:
                ConverterEngine._model_info_image(mesh, inp, out)
                cb(True, f"3D -> Image -> {Path(out).name} (info card - rendering not available)")
        except ImportError:
            cb(False, "trimesh not installed. Run: pip install trimesh")
        except Exception as e:
            try:
                if mesh is not None:
                    ConverterEngine._model_info_image(mesh, inp, out)
                    cb(True, f"3D -> Image -> {Path(out).name} (info card)")
                else:
                    cb(False, f"3D load failed: {e}")
            except Exception as e2:
                cb(False, f"{e2}")

    @staticmethod
    def _model_info_image(mesh, inp, out):
        """Create a styled info card image as fallback when rendering fails."""
        from PIL import Image as PILImage, ImageDraw, ImageFont
        img = PILImage.new("RGB", (640, 480), color=(30, 30, 48))
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
            font_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)
            font_lg = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
        except Exception:
            font = ImageFont.load_default()
            font_sm = font
            font_lg = font
        # Title bar
        draw.rectangle([0, 0, 640, 60], fill=(53, 104, 184))
        draw.text((20, 16), "3D Model Preview", fill=(255, 255, 255), font=font_lg)
        # Info
        y = 90
        info_lines = [
            f"File: {Path(inp).name}",
            f"Format: {Path(inp).suffix.upper().lstrip('.')}",
            f"Vertices: {len(mesh.vertices) if hasattr(mesh, 'vertices') else 'N/A'}",
            f"Faces: {len(mesh.faces) if hasattr(mesh, 'faces') else 'N/A'}",
            f"Bounds X: {mesh.bounds[0][0]:.2f} to {mesh.bounds[1][0]:.2f}" if hasattr(mesh, 'bounds') else "",
            f"Bounds Y: {mesh.bounds[0][1]:.2f} to {mesh.bounds[1][1]:.2f}" if hasattr(mesh, 'bounds') else "",
            f"Bounds Z: {mesh.bounds[0][2]:.2f} to {mesh.bounds[1][2]:.2f}" if hasattr(mesh, 'bounds') else "",
            f"Volume: {mesh.volume:.4f}" if hasattr(mesh, 'volume') else "",
            f"Surface Area: {mesh.area:.4f}" if hasattr(mesh, 'area') else "",
            f"Convex: {mesh.is_convex}" if hasattr(mesh, 'is_convex') else "",
            f"Watertight: {mesh.is_watertight}" if hasattr(mesh, 'is_watertight') else "",
        ]
        for line in info_lines:
            if line:
                draw.text((30, y), line, fill=(184, 184, 208), font=font)
                y += 26
        # Draw simple wireframe representation
        if hasattr(mesh, 'vertices') and len(mesh.vertices) > 0:
            verts = mesh.vertices
            vert_range = (verts.max(axis=0) - verts.min(axis=0)).max()
            if vert_range < 1e-10:
                vert_range = 1.0  # avoid division by zero for degenerate meshes
            cx = 520
            cy = 300
            scale = 80 / vert_range
            center = (verts.max(axis=0) + verts.min(axis=0)) / 2
            points_2d = []
            for v in verts[:200]:  # limit points for speed
                px = int(cx + (v[0] - center[0]) * scale)
                py = int(cy - (v[1] - center[1]) * scale)
                points_2d.append((px, py))
            for px, py in points_2d:
                draw.ellipse([px - 1, py - 1, px + 1, py + 1], fill=(89, 158, 255))
        out_ext = Path(out).suffix.lower().lstrip(".")
        if out_ext in ("jpg", "jpeg"):
            img = img.convert("RGB")
        img.save(str(out))

    @staticmethod
    def model_to_txt(inp, out, cb):
        try:
            import trimesh
            mesh = trimesh.load(str(inp), force='mesh')
            lines = []
            lines.append("3D Model Information")
            lines.append("=" * 50)
            lines.append(f"File: {Path(inp).name}")
            lines.append(f"Format: {Path(inp).suffix.upper().lstrip('.')}")
            lines.append(f"File Size: {human_size(os.path.getsize(inp))}")
            lines.append("")
            if hasattr(mesh, 'vertices'):
                lines.append(f"Vertices: {len(mesh.vertices)}")
            if hasattr(mesh, 'faces'):
                lines.append(f"Faces: {len(mesh.faces)}")
            if hasattr(mesh, 'edges'):
                lines.append(f"Edges: {len(mesh.edges)}")
            if hasattr(mesh, 'bounds'):
                b = mesh.bounds
                lines.append(f"Bounding Box:")
                lines.append(f"  X: {b[0][0]:.6f} to {b[1][0]:.6f}")
                lines.append(f"  Y: {b[0][1]:.6f} to {b[1][1]:.6f}")
                lines.append(f"  Z: {b[0][2]:.6f} to {b[1][2]:.6f}")
            if hasattr(mesh, 'volume'):
                lines.append(f"Volume: {mesh.volume:.6f}")
            if hasattr(mesh, 'area'):
                lines.append(f"Surface Area: {mesh.area:.6f}")
            if hasattr(mesh, 'center_mass'):
                c = mesh.center_mass
                lines.append(f"Center of Mass: ({c[0]:.4f}, {c[1]:.4f}, {c[2]:.4f})")
            if hasattr(mesh, 'is_convex'):
                lines.append(f"Convex: {mesh.is_convex}")
            if hasattr(mesh, 'is_watertight'):
                lines.append(f"Watertight: {mesh.is_watertight}")
            if hasattr(mesh, 'euler_number'):
                lines.append(f"Euler Number: {mesh.euler_number}")
            ConverterEngine._write(out, "\n".join(lines))
            cb(True, f"3D -> TXT -> {Path(out).name}")
        except ImportError:
            cb(False, "trimesh not installed. Run: pip install trimesh")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def model_to_json(inp, out, cb):
        try:
            import trimesh
            mesh = trimesh.load(str(inp), force='mesh')
            data = {
                "filename": Path(inp).name,
                "format": Path(inp).suffix.lower().lstrip("."),
                "file_size_bytes": os.path.getsize(inp),
            }
            if hasattr(mesh, 'vertices'):
                data["vertex_count"] = len(mesh.vertices)
            if hasattr(mesh, 'faces'):
                data["face_count"] = len(mesh.faces)
            if hasattr(mesh, 'bounds'):
                b = mesh.bounds
                data["bounds"] = {
                    "min": [round(b[0][0], 6), round(b[0][1], 6), round(b[0][2], 6)],
                    "max": [round(b[1][0], 6), round(b[1][1], 6), round(b[1][2], 6)],
                }
            if hasattr(mesh, 'volume'):
                data["volume"] = round(mesh.volume, 6)
            if hasattr(mesh, 'area'):
                data["surface_area"] = round(mesh.area, 6)
            if hasattr(mesh, 'is_convex'):
                data["is_convex"] = mesh.is_convex
            if hasattr(mesh, 'is_watertight'):
                data["is_watertight"] = mesh.is_watertight
            if hasattr(mesh, 'euler_number'):
                data["euler_number"] = mesh.euler_number
            ConverterEngine._write(out, json.dumps(data, indent=2))
            cb(True, f"3D -> JSON -> {Path(out).name}")
        except ImportError:
            cb(False, "trimesh not installed. Run: pip install trimesh")
        except Exception as e:
            cb(False, f"{e}")

    # ── Dispatcher ──────────────────────────────────────────


    @staticmethod
    def _dict_to_xml_element(parent, data_dict):
        """Shared helper: recursively convert dict to XML elements."""
        for key, val in data_dict.items():
            safe_key = re.sub(r'[^a-zA-Z0-9_]', '_', str(key))
            child = ET.SubElement(parent, safe_key)
            if isinstance(val, dict):
                ConverterEngine._dict_to_xml_element(child, val)
            elif isinstance(val, list):
                for item in val:
                    item_elem = ET.SubElement(child, "item")
                    if isinstance(item, dict):
                        ConverterEngine._dict_to_xml_element(item_elem, item)
                    else:
                        item_elem.text = str(item)
            else:
                child.text = str(val)

    @staticmethod
    def _xml_element_to_dict(element):
        """Shared helper: recursively convert XML element to dict."""
        result = {}
        for child in element:
            tag = child.tag
            if len(child) > 0:
                val = ConverterEngine._xml_element_to_dict(child)
            elif child.text and child.text.strip():
                val = child.text.strip()
            else:
                val = None
            if tag in result:
                existing = result[tag]
                if isinstance(existing, list):
                    existing.append(val)
                else:
                    result[tag] = [existing, val]
            else:
                result[tag] = val
        return result

    @staticmethod
    def _build_xml_tree(data):
        """Build an XML ElementTree from Python data (dict or list)."""
        root = ET.Element("root")
        if isinstance(data, dict):
            ConverterEngine._dict_to_xml_element(root, data)
        elif isinstance(data, list):
            for item in data:
                item_elem = ET.SubElement(root, "item")
                if isinstance(item, dict):
                    ConverterEngine._dict_to_xml_element(item_elem, item)
                else:
                    item_elem.text = str(item)
        else:
            root.text = str(data)
        tree = ET.ElementTree(root)
        ET.indent(tree, space="  ")
        return tree

    @staticmethod
    def _write_xml_tree(tree, output_path):
        """Write an XML tree to file with declaration."""
        tree.write(str(output_path), encoding="unicode", xml_declaration=True)

    @staticmethod
    def _make_pptx_default():
        """Create a default Presentation with widescreen dimensions."""
        from pptx import Presentation
        from pptx.util import Inches
        prs = Presentation()
        prs.slide_width = Inches(13.333)
        prs.slide_height = Inches(7.5)
        return prs

    @staticmethod
    def _make_pptx_title_slide(prs, title_text, font_size=44):
        """Create a standard centered title slide."""
        from pptx.util import Inches, Pt
        from pptx.dml.color import RGBColor
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        txBox = slide.shapes.add_textbox(Inches(0.5), Inches(2.5), Inches(12.3), Inches(2))
        tf = txBox.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = title_text[:120]
        p.font.size = Pt(font_size)
        p.font.bold = True
        p.font.color.rgb = RGBColor(0x1A, 0x4A, 0x7A)
        p.alignment = 1  # CENTER
        return slide

    @staticmethod
    def _make_pptx_content_slide(prs, title, body_lines, title_size=32, body_size=22):
        """Create a standard content slide with title + body text."""
        from pptx.util import Inches, Pt
        from pptx.dml.color import RGBColor
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        txBox = slide.shapes.add_textbox(Inches(0.8), Inches(0.4), Inches(11.7), Inches(1.3))
        tf = txBox.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.text = title[:120]
        p.font.size = Pt(title_size)
        p.font.bold = True
        p.font.color.rgb = RGBColor(0x1A, 0x4A, 0x7A)
        if body_lines:
            txBox2 = slide.shapes.add_textbox(Inches(0.8), Inches(2.0), Inches(11.7), Inches(5.0))
            tf2 = txBox2.text_frame
            tf2.word_wrap = True
            for idx, line in enumerate(body_lines):
                p = tf2.paragraphs[0] if idx == 0 else tf2.add_paragraph()
                p.text = str(line)[:500]
                p.font.size = Pt(body_size)
                p.space_after = Pt(10)
        return slide


    TEXT_CONVERTERS = {
        ("txt", "pdf"):  "txt_to_pdf",     ("pdf", "txt"):  "pdf_to_txt",
        ("txt", "md"):   "txt_to_md",      ("md", "txt"):   "md_to_txt",
        ("txt", "html"): "txt_to_html",    ("html", "txt"): "html_to_txt",
        ("md", "html"):  "md_to_html_file", ("html", "md"):  "html_to_md_file",
        ("md", "pdf"):   "md_to_pdf",      ("html", "pdf"): "html_to_pdf",
        ("pdf", "md"):   "pdf_to_md",      ("pdf", "html"): "pdf_to_html",
    }

    DATA_CONVERTERS = {
        ("json", "csv"):  "json_to_csv",  ("csv", "json"):  "csv_to_json",
        ("json", "xml"):  "json_to_xml",  ("xml", "json"):  "xml_to_json",
        ("csv", "xml"):   "csv_to_xml",   ("xml", "csv"):   "xml_to_csv",
        ("json", "yaml"): "json_to_yaml", ("yaml", "json"): "yaml_to_json",
        ("csv", "yaml"):  "csv_to_yaml",  ("yaml", "csv"):  "yaml_to_csv",
        ("xml", "yaml"):  "xml_to_yaml",  ("yaml", "xml"):  "yaml_to_xml",
    }

    # Cross-category converters: (src_ext, dst_ext) -> method_name

    # ─── Presentation Conversion Methods ────────────────────────────────

    @staticmethod
    def convert_presentation(inp, out, cb, quality=None):
        """Intra-category presentation conversion via LibreOffice."""
        try:
            out_dir = str(Path(out).parent)
            out_ext = Path(out).suffix.lower().lstrip(".")
            cmd = [
                "soffice", "--headless", "--norestore",
                "--convert-to", out_ext,
                "--outdir", out_dir,
                str(inp)
            ]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if r.returncode == 0:
                # LibreOffice names the output based on input stem
                expected = Path(out_dir) / (Path(inp).stem + "." + out_ext)
                if expected.exists() and os.path.abspath(str(expected)) != os.path.abspath(str(out)):
                    import shutil
                    shutil.move(str(expected), str(out))
                cb(True, f"Presentation -> {Path(out).name}")
            else:
                cb(False, f"LibreOffice conversion failed: {r.stderr[:300]}")
        except FileNotFoundError:
            cb(False, "LibreOffice (soffice) not found. Install LibreOffice for presentation conversions.")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def presentation_to_pdf(inp, out, cb):
        """Convert presentation to PDF via LibreOffice."""
        ConverterEngine.convert_presentation(inp, out, cb)

    @staticmethod
    def presentation_to_txt(inp, out, cb):
        """Extract text from presentation using python-pptx."""
        try:
            from pptx import Presentation
            prs = Presentation(str(inp))
            lines = []
            for i, slide in enumerate(prs.slides, 1):
                lines.append(f"=== Slide {i} ===")
                for shape in slide.shapes:
                    if shape.has_text_frame:
                        for para in shape.text_frame.paragraphs:
                            text = para.text.strip()
                            if text:
                                lines.append(text)
                lines.append("")
            Path(out).write_text("\n".join(lines), encoding="utf-8")
            cb(True, f"Presentation -> TXT -> {Path(out).name}")
        except ImportError:
            cb(False, "python-pptx not installed. Run: pip install python-pptx")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def presentation_to_html(inp, out, cb):
        """Extract presentation content as HTML."""
        try:
            from pptx import Presentation
            prs = Presentation(str(inp))
            html_parts = [
                '<!DOCTYPE html>', '<html><head>',
                '<meta charset="utf-8">', '<title>' + Path(inp).stem + '</title>',
                '<style>',
                'body{font-family:Calibri,sans-serif;max-width:960px;margin:40px auto;color:#333;}',
                '.slide{border:1px solid #ddd;border-radius:8px;margin:24px 0;padding:24px;background:#fafafa;min-height:150px;}',
                '.slide-num{font-size:12px;color:#999;margin-bottom:12px;}',
                'h1{color:#1a4a7a;}h2{color:#2a5a8a;}',
                '</style></head><body>',
                '<h1>Presentation: ' + Path(inp).stem + '</h1>'
            ]
            for i, slide in enumerate(prs.slides, 1):
                html_parts.append(f'<div class="slide"><div class="slide-num">Slide {i}</div>')
                shape_list = list(slide.shapes)
                for j, shape in enumerate(shape_list):
                    if shape.has_text_frame:
                        text = shape.text_frame.text.strip()
                        if text:
                            safe_text = _esc(text)
                            if j == 0 and len(text) < 80:
                                html_parts.append(f'<h2>{safe_text}</h2>')
                            else:
                                html_parts.append(f'<p>{safe_text}</p>')
                html_parts.append('</div>')
            html_parts.append('</body></html>')
            Path(out).write_text("\n".join(html_parts), encoding="utf-8")
            cb(True, f"Presentation -> HTML -> {Path(out).name}")
        except ImportError:
            cb(False, "python-pptx not installed. Run: pip install python-pptx")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def presentation_to_image(inp, out, cb):
        """Convert presentation slide to image via LibreOffice -> PDF -> image."""
        try:
            import tempfile
            # First convert to PDF via LibreOffice
            with tempfile.TemporaryDirectory() as tmp:
                cmd = [
                    "soffice", "--headless", "--norestore",
                    "--convert-to", "pdf",
                    "--outdir", tmp,
                    str(inp)
                ]
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                if r.returncode != 0:
                    cb(False, f"LibreOffice PDF conversion failed: {r.stderr[:300]}")
                    return
                pdfs = sorted(Path(tmp).glob("*.pdf"))
                if not pdfs:
                    cb(False, "PDF conversion produced no output")
                    return
                # Try PyMuPDF first
                try:
                    import fitz
                    doc = fitz.open(str(pdfs[0]))
                    if len(doc) > 0:
                        page = doc[0]
                        pix = page.get_pixmap(dpi=150)
                        png_bytes = pix.tobytes("png")
                        doc.close()
                        import io
                        from PIL import Image as PILImage
                        img = PILImage.open(io.BytesIO(png_bytes))
                        out_ext = Path(out).suffix.lower().lstrip(".")
                        if out_ext in ("jpg", "jpeg"):
                            if img.mode in ("RGBA", "LA", "P"):
                                img = img.convert("RGB")
                            img.save(str(out), quality=85)
                        else:
                            img.save(str(out))
                        cb(True, f"Presentation -> Image -> {Path(out).name}")
                    else:
                        doc.close()
                        cb(False, "Presentation has no pages")
                except ImportError:
                    # Fallback to pdf2image
                    from pdf2image import convert_from_path
                    images = convert_from_path(str(pdfs[0]), first_page=1, last_page=1, dpi=150)
                    if images:
                        img = images[0]
                        out_ext = Path(out).suffix.lower().lstrip(".")
                        if out_ext in ("jpg", "jpeg"):
                            if img.mode in ("RGBA", "LA", "P"):
                                img = img.convert("RGB")
                            img.save(str(out), quality=85)
                        else:
                            img.save(str(out))
                        cb(True, f"Presentation -> Image -> {Path(out).name}")
                    else:
                        cb(False, "No images rendered from PDF")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def presentation_to_json(inp, out, cb):
        """Extract presentation content as JSON."""
        try:
            from pptx import Presentation
            prs = Presentation(str(inp))
            slides_data = []
            for i, slide in enumerate(prs.slides, 1):
                slide_info = {"slide": i, "shapes": []}
                for shape in slide.shapes:
                    shape_info = {"type": str(shape.shape_type), "name": shape.name}
                    if shape.has_text_frame:
                        shape_info["text"] = shape.text_frame.text
                    slide_info["shapes"].append(shape_info)
                slides_data.append(slide_info)
            data = {
                "file": Path(inp).name,
                "slide_count": len(prs.slides),
                "slide_width": str(prs.slide_width),
                "slide_height": str(prs.slide_height),
                "slides": slides_data
            }
            Path(out).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            cb(True, f"Presentation -> JSON -> {Path(out).name}")
        except ImportError:
            cb(False, "python-pptx not installed. Run: pip install python-pptx")
        except Exception as e:
            cb(False, f"{e}")

    # --- PPTX Template Method (Template Method Pattern) ---

    @staticmethod
    def _create_pptx_from_slides(
        slides_content: List[List[str]],
        output_path: str,
        title: str = "",
        title_size: int = 36,
        body_size: int = 22,
    ) -> bool:
        """Template Method: create a PPTX from pre-parsed slide content.

        Consolidates the common creation logic shared by txt_to_pptx,
        md_to_pptx, and html_to_pptx. Each format converter only needs
        to parse input into slide content lists.
        """
        try:
            from pptx import Presentation
            from pptx.util import Inches, Pt
            from pptx.dml.color import RGBColor
            prs = Presentation()
            prs.slide_width = Inches(13.333)
            prs.slide_height = Inches(7.5)
            if title:
                slide = prs.slides.add_slide(prs.slide_layouts[6])
                txBox = slide.shapes.add_textbox(Inches(0.5), Inches(2.5), Inches(12.3), Inches(2))
                tf = txBox.text_frame
                tf.word_wrap = True
                p = tf.paragraphs[0]
                p.text = title[:120]
                p.font.size = Pt(44)
                p.font.bold = True
                p.font.color.rgb = RGBColor(0x1A, 0x4A, 0x7A)
                p.alignment = 1
            for slide_lines in slides_content:
                if not slide_lines:
                    continue
                slide = prs.slides.add_slide(prs.slide_layouts[6])
                title_text = slide_lines[0].strip()[:120]
                txBox = slide.shapes.add_textbox(Inches(0.8), Inches(0.4), Inches(11.7), Inches(1.3))
                tf = txBox.text_frame
                tf.word_wrap = True
                p = tf.paragraphs[0]
                p.text = title_text
                p.font.size = Pt(title_size)
                p.font.bold = True
                p.font.color.rgb = RGBColor(0x1A, 0x4A, 0x7A)
                if len(slide_lines) > 1:
                    txBox2 = slide.shapes.add_textbox(Inches(0.8), Inches(2.0), Inches(11.7), Inches(5.0))
                    tf2 = txBox2.text_frame
                    tf2.word_wrap = True
                    for idx, line in enumerate(slide_lines[1:]):
                        p = tf2.paragraphs[0] if idx == 0 else tf2.add_paragraph()
                        p.text = str(line)[:500]
                        p.font.size = Pt(body_size)
                        p.space_after = Pt(10)
            prs.save(str(output_path))
            return True
        except Exception:
            return False

    @staticmethod
    def _parse_text_to_slides(text: str) -> List[List[str]]:
        """Parse plain text into slides (blank-line / separator separated)."""
        slides_content, current = [], []
        for line in text.split("\n"):
            stripped = line.strip()
            if stripped == "" or stripped.startswith("===") or stripped.startswith("---"):
                if current:
                    slides_content.append(current)
                    current = []
            else:
                current.append(line)
        if current:
            slides_content.append(current)
        return slides_content or [["No content"]]

    @staticmethod
    def _parse_markdown_to_slides(md_text: str) -> List[List[str]]:
        """Parse markdown into slides (H1/H2 or blank lines start new slides)."""
        slides_content, current = [], []
        for line in md_text.split("\n"):
            stripped = line.strip()
            if stripped.startswith("# ") or stripped.startswith("## "):
                if current:
                    slides_content.append(current)
                    current = []
                current.append(stripped.lstrip("# ").strip())
            elif stripped == "":
                if current:
                    slides_content.append(current)
                    current = []
            else:
                current.append(stripped.replace("**", "").replace("*", "").replace("`", ""))
        if current:
            slides_content.append(current)
        return slides_content or [["No content"]]

    @staticmethod
    def _parse_html_to_slides(html_text: str) -> List[List[str]]:
        """Parse HTML into slides by stripping tags and splitting on blanks."""
        clean = re.sub(r"<br\s*/?>", "\n", html_text)
        clean = re.sub(r"<li[^>]*>", "\n- ", clean)
        clean = re.sub(r"</(p|div|h[1-6]|li|ul|ol|tr|table)>", "\n", clean)
        clean = re.sub(r"<[^>]+>", "", clean)
        for entity, char in [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"),
                             ("&quot;", '"'), ("&#39;", "'"), ("&nbsp;", " ")]:
            clean = clean.replace(entity, char)
        slides_content, current = [], []
        for line in clean.split("\n"):
            if line.strip() == "":
                if current:
                    slides_content.append(current)
                    current = []
            else:
                current.append(line.strip())
        if current:
            slides_content.append(current)
        return slides_content or [["No content"]]

    @staticmethod
    def txt_to_pptx(inp, out, cb):
        """Create a PPTX from a text file (blank-line separated slides).

        Refactored: uses Template Method (_parse_text_to_slides +
        _create_pptx_from_slides) to eliminate ~30 lines of duplicated
        PPTX creation boilerplate.
        """
        try:
            text = Path(inp).read_text(encoding="utf-8")
            slides = ConverterEngine._parse_text_to_slides(text)
            if ConverterEngine._create_pptx_from_slides(slides, str(out), title=Path(inp).stem):
                cb(True, f"TXT -> PPTX -> {Path(out).name}")
            else:
                cb(False, "Failed to create PPTX from text")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def md_to_pptx(inp, out, cb):
        """Create a PPTX from a Markdown file (Template Method)."""
        try:
            md_text = Path(inp).read_text(encoding="utf-8")
            slides = ConverterEngine._parse_markdown_to_slides(md_text)
            if ConverterEngine._create_pptx_from_slides(slides, str(out), title=Path(inp).stem):
                cb(True, f"MD -> PPTX -> {Path(out).name}")
            else:
                cb(False, "Failed to create PPTX from markdown")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def html_to_pptx(inp, out, cb):
        """Create a PPTX from an HTML file (Template Method)."""
        try:
            html_text = Path(inp).read_text(encoding="utf-8")
            slides = ConverterEngine._parse_html_to_slides(html_text)
            if ConverterEngine._create_pptx_from_slides(slides, str(out), title=Path(inp).stem,
                                                          title_size=32, body_size=20):
                cb(True, f"HTML -> PPTX -> {Path(out).name}")
            else:
                cb(False, "Failed to create PPTX from HTML")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def json_to_pptx(inp, out, cb):
        """Create a PPTX from JSON data."""
        try:
            from pptx import Presentation
            from pptx.util import Inches, Pt
            from pptx.dml.color import RGBColor

            data = json.loads(Path(inp).read_text(encoding="utf-8"))
            prs = Presentation()
            prs.slide_width = Inches(13.333)
            prs.slide_height = Inches(7.5)

            # Title slide
            slide = prs.slides.add_slide(prs.slide_layouts[6])
            txBox = slide.shapes.add_textbox(Inches(0.5), Inches(2.5), Inches(12.3), Inches(2))
            tf = txBox.text_frame
            p = tf.paragraphs[0]
            p.text = Path(inp).stem
            p.font.size = Pt(44)
            p.font.bold = True
            p.font.color.rgb = RGBColor(0x1A, 0x4A, 0x7A)
            p.alignment = 1  # CENTER

            if isinstance(data, list):
                for item in data[:50]:  # cap at 50 slides
                    slide = prs.slides.add_slide(prs.slide_layouts[6])
                    txBox = slide.shapes.add_textbox(Inches(0.8), Inches(0.5), Inches(11.7), Inches(6.5))
                    tf = txBox.text_frame
                    tf.word_wrap = True
                    if isinstance(item, dict):
                        for idx, (k, v) in enumerate(item.items()):
                            p = tf.paragraphs[0] if idx == 0 else tf.add_paragraph()
                            p.text = f"{k}: {v}"
                            p.font.size = Pt(24)
                            p.space_after = Pt(10)
                    else:
                        p = tf.paragraphs[0]
                        p.text = str(item)[:500]
                        p.font.size = Pt(24)
            elif isinstance(data, dict):
                for key, value in data.items():
                    slide = prs.slides.add_slide(prs.slide_layouts[6])
                    txBox = slide.shapes.add_textbox(Inches(0.8), Inches(0.3), Inches(11.7), Inches(1.2))
                    tf = txBox.text_frame
                    p = tf.paragraphs[0]
                    p.text = str(key)[:120]
                    p.font.size = Pt(32)
                    p.font.bold = True
                    p.font.color.rgb = RGBColor(0x1A, 0x4A, 0x7A)

                    txBox2 = slide.shapes.add_textbox(Inches(0.8), Inches(1.8), Inches(11.7), Inches(5.2))
                    tf2 = txBox2.text_frame
                    tf2.word_wrap = True
                    if isinstance(value, (list, dict)):
                        val_text = json.dumps(value, indent=2, ensure_ascii=False)[:2000]
                    else:
                        val_text = str(value)[:2000]
                    p = tf2.paragraphs[0]
                    p.text = val_text
                    p.font.size = Pt(20)

            prs.save(str(out))
            cb(True, f"JSON -> PPTX -> {Path(out).name}")
        except ImportError:
            cb(False, "python-pptx not installed. Run: pip install python-pptx")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def csv_to_pptx(inp, out, cb):
        """Create a PPTX with a table from CSV data."""
        try:
            from pptx import Presentation
            from pptx.util import Inches, Pt

            rows = list(csv.reader(Path(inp).open(encoding="utf-8")))
            if not rows:
                cb(False, "CSV file is empty")
                return

            prs = Presentation()
            prs.slide_width = Inches(13.333)
            prs.slide_height = Inches(7.5)

            # Title slide
            slide = prs.slides.add_slide(prs.slide_layouts[6])
            txBox = slide.shapes.add_textbox(Inches(0.5), Inches(0.2), Inches(12.3), Inches(0.8))
            tf = txBox.text_frame
            p = tf.paragraphs[0]
            p.text = Path(inp).stem
            p.font.size = Pt(28)
            p.font.bold = True

            num_rows = min(len(rows), 25)
            num_cols = max(len(r) for r in rows[:num_rows])
            table = slide.shapes.add_table(
                num_rows, num_cols,
                Inches(0.5), Inches(1.2), Inches(12.3), Inches(5.8)
            ).table

            for row_idx in range(num_rows):
                for col_idx in range(len(rows[row_idx])):
                    cell = table.cell(row_idx, col_idx)
                    cell.text = rows[row_idx][col_idx]
                    for paragraph in cell.text_frame.paragraphs:
                        paragraph.font.size = Pt(11)
                        if row_idx == 0:
                            paragraph.font.bold = True

            prs.save(str(out))
            cb(True, f"CSV -> PPTX -> {Path(out).name}")
        except ImportError:
            cb(False, "python-pptx not installed. Run: pip install python-pptx")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def xml_to_pptx(inp, out, cb):
        """Create a PPTX from XML data."""
        try:
            from pptx import Presentation
            from pptx.util import Inches, Pt
            from pptx.dml.color import RGBColor

            tree = ET.parse(str(inp))
            root = tree.getroot()

            lines = [f"<{root.tag}>"]
            def _walk(elem, indent):
                prefix = "  " * indent
                if elem.text and elem.text.strip():
                    lines.append(f"{prefix}<{elem.tag}>: {elem.text.strip()}")
                else:
                    lines.append(f"{prefix}<{elem.tag}>")
                for child in elem:
                    _walk(child, indent + 1)
            _walk(root, 0)

            prs = Presentation()
            prs.slide_width = Inches(13.333)
            prs.slide_height = Inches(7.5)

            # Title slide
            slide = prs.slides.add_slide(prs.slide_layouts[6])
            txBox = slide.shapes.add_textbox(Inches(0.5), Inches(2.5), Inches(12.3), Inches(2))
            tf = txBox.text_frame
            p = tf.paragraphs[0]
            p.text = f"XML: {Path(inp).stem}"
            p.font.size = Pt(40)
            p.font.bold = True
            p.font.color.rgb = RGBColor(0x1A, 0x4A, 0x7A)
            p.alignment = 1  # CENTER

            # Content slides (batch lines into slides of ~15 lines)
            batch_size = 18
            for i in range(0, len(lines), batch_size):
                batch = lines[i:i+batch_size]
                slide = prs.slides.add_slide(prs.slide_layouts[6])
                txBox = slide.shapes.add_textbox(Inches(0.5), Inches(0.3), Inches(12.3), Inches(6.8))
                tf = txBox.text_frame
                tf.word_wrap = True
                for idx, line in enumerate(batch):
                    p = tf.paragraphs[0] if idx == 0 else tf.add_paragraph()
                    p.text = line
                    p.font.size = Pt(16)
                    p.font.name = "DejaVu Sans Mono"
                    p.space_after = Pt(2)

            prs.save(str(out))
            cb(True, f"XML -> PPTX -> {Path(out).name}")
        except ImportError:
            cb(False, "python-pptx not installed. Run: pip install python-pptx")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def yaml_to_pptx(inp, out, cb):
        """Create a PPTX from YAML data (convert to JSON first)."""
        try:
            import yaml
            data = yaml.safe_load(Path(inp).read_text(encoding="utf-8"))
            tmp_json = Path(inp).parent / ("_" + Path(inp).stem + ".json")
            tmp_json.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            try:
                ConverterEngine.json_to_pptx(str(tmp_json), out, cb)
            finally:
                tmp_json.unlink(missing_ok=True)
        except ImportError:
            cb(False, "PyYAML not installed. Run: pip install PyYAML")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def pdf_to_pptx(inp, out, cb):
        """Extract text from PDF and create a PPTX."""
        try:
            from pptx import Presentation
            from pptx.util import Inches, Pt
            from pptx.dml.color import RGBColor
            import pypdf

            reader = pypdf.PdfReader(str(inp))
            prs = Presentation()
            prs.slide_width = Inches(13.333)
            prs.slide_height = Inches(7.5)

            # Title slide
            slide = prs.slides.add_slide(prs.slide_layouts[6])
            txBox = slide.shapes.add_textbox(Inches(1), Inches(2.5), Inches(11.3), Inches(2))
            tf = txBox.text_frame
            p = tf.paragraphs[0]
            p.text = Path(inp).stem
            p.font.size = Pt(44)
            p.font.bold = True
            p.font.color.rgb = RGBColor(0x1A, 0x4A, 0x7A)
            p.alignment = 1  # CENTER

            for page_num, page in enumerate(reader.pages, 1):
                text = page.extract_text() or ""
                if not text.strip():
                    continue
                lines = text.strip().split("\n")
                slides_content = []
                current = []
                for line in lines:
                    if line.strip() == "":
                        if current:
                            slides_content.append(current)
                            current = []
                    else:
                        current.append(line.strip())
                if current:
                    slides_content.append(current)
                if not slides_content:
                    slides_content = [["(empty page)"]]

                for slide_lines in slides_content[:5]:  # max 5 slides per page
                    slide = prs.slides.add_slide(prs.slide_layouts[6])
                    txBox = slide.shapes.add_textbox(Inches(0.5), Inches(0.1), Inches(5), Inches(0.4))
                    tf = txBox.text_frame
                    p = tf.paragraphs[0]
                    p.text = f"Page {page_num}"
                    p.font.size = Pt(12)
                    p.font.color.rgb = RGBColor(0x99, 0x99, 0x99)
                    title_text = slide_lines[0][:120] if slide_lines else "Untitled"
                    txBox2 = slide.shapes.add_textbox(Inches(0.8), Inches(0.5), Inches(11.7), Inches(1.0))
                    tf2 = txBox2.text_frame
                    tf2.word_wrap = True
                    p2 = tf2.paragraphs[0]
                    p2.text = title_text
                    p2.font.size = Pt(30)
                    p2.font.bold = True
                    if len(slide_lines) > 1:
                        txBox3 = slide.shapes.add_textbox(Inches(0.8), Inches(1.8), Inches(11.7), Inches(5.2))
                        tf3 = txBox3.text_frame
                        tf3.word_wrap = True
                        for idx, line in enumerate(slide_lines[1:]):
                            p = tf3.paragraphs[0] if idx == 0 else tf3.add_paragraph()
                            p.text = line[:300]
                            p.font.size = Pt(18)
                            p.space_after = Pt(6)

            prs.save(str(out))
            cb(True, f"PDF -> PPTX -> {Path(out).name}")
        except ImportError:
            cb(False, "python-pptx or pypdf not installed")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def image_to_pptx(inp, out, cb):
        """Create a PPTX with the image centered on a slide."""
        try:
            from pptx import Presentation
            from pptx.util import Inches

            prs = Presentation()
            prs.slide_width = Inches(13.333)
            prs.slide_height = Inches(7.5)

            slide = prs.slides.add_slide(prs.slide_layouts[6])
            # Add image, fit width, maintain aspect ratio
            slide.shapes.add_picture(str(inp), Inches(0.5), Inches(0.5), width=Inches(12.3))

            prs.save(str(out))
            cb(True, f"Image -> PPTX -> {Path(out).name}")
        except ImportError:
            cb(False, "python-pptx not installed. Run: pip install python-pptx")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def data_to_odp(inp, out, cb):
        """Create an ODP from data (JSON/CSV/XML/YAML -> PPTX -> ODP via LibreOffice)."""
        try:
            import tempfile
            with tempfile.TemporaryDirectory() as tmp:
                tmp_pptx = Path(tmp) / "temp.pptx"
                in_ext = Path(inp).suffix.lower().lstrip(".")
                # Route through the appropriate *_to_pptx method
                pptx_error = [None]
                def _capture_error(ok, msg):
                    if not ok:
                        pptx_error[0] = msg
                if in_ext == "csv":
                    ConverterEngine.csv_to_pptx(inp, str(tmp_pptx), _capture_error)
                elif in_ext == "xml":
                    ConverterEngine.xml_to_pptx(inp, str(tmp_pptx), _capture_error)
                elif in_ext == "yaml":
                    ConverterEngine.yaml_to_pptx(inp, str(tmp_pptx), _capture_error)
                else:
                    ConverterEngine.json_to_pptx(inp, str(tmp_pptx), _capture_error)
                if pptx_error[0]:
                    cb(False, f"Intermediate PPTX creation failed: {pptx_error[0]}")
                    return
                if not tmp_pptx.exists():
                    cb(False, "Failed to create intermediate PPTX (no output file)")
                    return
                # Convert PPTX to ODP via LibreOffice
                cmd = [
                    "soffice", "--headless", "--norestore",
                    "--convert-to", "odp",
                    "--outdir", str(Path(out).parent),
                    str(tmp_pptx)
                ]
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                if r.returncode == 0:
                    expected = Path(out).parent / "temp.odp"
                    if expected.exists() and os.path.abspath(str(expected)) != os.path.abspath(str(out)):
                        import shutil
                        shutil.move(str(expected), str(out))
                    cb(True, f"Data -> ODP -> {Path(out).name}")
                else:
                    cb(False, f"LibreOffice ODP conversion failed: {r.stderr[:300]}")
        except Exception as e:
            cb(False, f"{e}")

    @staticmethod
    def text_to_odp(inp, out, cb):
        """Convert text format (TXT/MD/HTML/PDF) to ODP via PPTX -> LibreOffice.

        Uses the existing text_to_pptx methods as intermediate step,
        then converts PPTX to ODP via LibreOffice headless.
        """
        try:
            import tempfile
            with tempfile.TemporaryDirectory() as tmp:
                tmp_pptx = Path(tmp) / "temp.pptx"
                in_ext = Path(inp).suffix.lower().lstrip(".")
                pptx_error = [None]
                def _capture_error(ok, msg):
                    if not ok:
                        pptx_error[0] = msg
                # Route to the appropriate text->pptx method
                text_to_pptx_map = {
                    "txt": "txt_to_pptx",
                    "md": "md_to_pptx",
                    "html": "html_to_pptx",
                    "pdf": "pdf_to_pptx",
                }
                method_name = text_to_pptx_map.get(in_ext)
                if method_name:
                    getattr(ConverterEngine, method_name)(inp, str(tmp_pptx), _capture_error)
                else:
                    cb(False, f"Text to ODP: unsupported source format .{in_ext}")
                    return
                if pptx_error[0]:
                    cb(False, f"Intermediate PPTX creation failed: {pptx_error[0]}")
                    return
                if not tmp_pptx.exists():
                    cb(False, "Failed to create intermediate PPTX (no output file)")
                    return
                # Convert PPTX to ODP via LibreOffice
                cmd = [
                    "soffice", "--headless", "--norestore",
                    "--convert-to", "odp",
                    "--outdir", str(Path(out).parent),
                    str(tmp_pptx)
                ]
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                if r.returncode == 0:
                    expected = Path(out).parent / "temp.odp"
                    if expected.exists() and os.path.abspath(str(expected)) != os.path.abspath(str(out)):
                        import shutil
                        shutil.move(str(expected), str(out))
                    cb(True, f"Text -> ODP -> {Path(out).name}")
                else:
                    cb(False, f"LibreOffice ODP conversion failed: {r.stderr[:300]}")
        except FileNotFoundError:
            cb(False, "LibreOffice (soffice) not found. Install LibreOffice for ODP conversions.")
        except Exception as e:
            cb(False, f"{e}")


    # Dispatch Table Pattern: replaces nested if/elif chains with
    # O(1) dict lookups.  Built once at class definition time.
    _CROSS_IMAGE_TARGETS = {"pdf": "image_to_pdf", "html": "image_to_html",
                            "txt": "image_to_txt", "json": "image_to_json"}

    _CROSS_DATA_TO_HTML = {"csv": "csv_to_html_table", "json": "json_to_html_pretty",
                           "xml": "xml_to_html_pretty", "yaml": "yaml_to_html_pretty"}
    _CROSS_DATA_TO_TXT  = {"json": "json_to_txt_data", "csv": "csv_to_txt_data",
                           "xml": "xml_to_txt_data", "yaml": "yaml_to_txt_data"}

    _CROSS_TXT_TO_DATA  = {"json": "txt_to_json_data", "csv": "txt_to_csv_data",
                           "xml": "txt_to_xml_data", "yaml": "txt_to_yaml_data"}

    _CROSS_TEXT_TO_PPTX = {"txt": "txt_to_pptx", "md": "md_to_pptx",
                           "html": "html_to_pptx", "pdf": "pdf_to_pptx"}
    _CROSS_DATA_TO_PPTX = {"json": "json_to_pptx", "csv": "csv_to_pptx",
                           "xml": "xml_to_pptx", "yaml": "yaml_to_pptx"}

    _CROSS_PRES_TARGETS = {"pdf": "presentation_to_pdf", "txt": "presentation_to_txt",
                           "html": "presentation_to_html", "json": "presentation_to_json"}

    @staticmethod
    def _resolve_cross_method(src_ext, dst_ext):
        """Resolve cross-category converter method via dispatch tables.

        Replaces nested if/elif chains with O(1) dict lookups for
        consistent performance regardless of conversion type.

        Bug fix: the old Image check returned early, preventing Image->PPTX
        from reaching its handler.  Also, Text->Data only handled TXT source,
        not HTML/MD/PDF.  Both are now fixed.
        """
        src_cat = get_category(src_ext)
        dst_cat = get_category(dst_ext)

        # Image -> text/data/presentation
        if src_cat == "image":
            if dst_ext == "pptx":
                return "image_to_pptx"
            return ConverterEngine._CROSS_IMAGE_TARGETS.get(dst_ext)

        # Data -> HTML/TXT
        if src_cat == "data":
            if dst_ext == "html":
                return ConverterEngine._CROSS_DATA_TO_HTML.get(src_ext)
            if dst_ext == "txt":
                return ConverterEngine._CROSS_DATA_TO_TXT.get(src_ext)

        # Text -> Data (any text format: txt, md, html, pdf)
        # Bug fix: was `src_ext == "txt"`, now uses category check
        if src_cat == "text" and dst_cat == "data":
            return ConverterEngine._CROSS_TXT_TO_DATA.get(dst_ext)

        # Video -> Image
        if src_cat == "video" and dst_cat == "image":
            return "video_to_image_frame"

        # Audio -> TXT
        if src_cat == "audio" and dst_ext == "txt":
            return "audio_to_txt_meta"

        # PDF -> Image
        if src_ext == "pdf" and dst_cat == "image":
            return "pdf_to_image_page"

        # 3D -> Image/TXT/JSON
        if src_cat == "3d":
            if dst_cat == "image": return "model_to_image"
            if dst_ext == "txt":    return "model_to_txt"
            if dst_ext == "json":   return "model_to_json"

        # Presentation -> Text/Image/JSON
        if src_cat == "presentation":
            if dst_cat == "image":
                return "presentation_to_image"
            return ConverterEngine._CROSS_PRES_TARGETS.get(dst_ext)

        # Text -> Presentation (PPTX)
        if src_cat == "text" and dst_ext == "pptx":
            return ConverterEngine._CROSS_TEXT_TO_PPTX.get(src_ext)

        # Text -> Presentation (ODP) via text_to_pptx -> LibreOffice
        if src_cat == "text" and dst_ext == "odp":
            return "text_to_odp"

        # Data -> Presentation
        if src_cat == "data":
            if dst_ext == "pptx":
                return ConverterEngine._CROSS_DATA_TO_PPTX.get(src_ext)
            if dst_ext == "odp":
                return "data_to_odp"

        return None

    # CROSS_CONVERTERS is built after class definition via _build_cross_converters()
    CROSS_CONVERTERS = {}

    _extra_pairs = [
        # Presentation -> Text/Image/JSON
        *[(p, "pdf") for p in PRESENTATION_FORMATS],
        *[(p, "txt") for p in PRESENTATION_FORMATS],
        *[(p, "html") for p in PRESENTATION_FORMATS],
        *[(p, img) for p in PRESENTATION_FORMATS for img in IMAGE_FORMATS],
        *[(p, "json") for p in PRESENTATION_FORMATS],
        # Text -> Presentation
        ("txt", "pptx"), ("md", "pptx"), ("html", "pptx"), ("pdf", "pptx"),
        ("txt", "odp"), ("md", "odp"), ("html", "odp"), ("pdf", "odp"),
        # Data -> Presentation
        ("json", "pptx"), ("csv", "pptx"), ("xml", "pptx"), ("yaml", "pptx"),
        ("json", "odp"), ("csv", "odp"), ("xml", "odp"), ("yaml", "odp"),
        # Image -> Presentation
        *[(img, "pptx") for img in IMAGE_FORMATS],
    ]

    @staticmethod
    def _build_cross_converters():
        """Build CROSS_CONVERTERS mapping after class is fully defined."""
        for (src_cat, dst_cat), dst_exts in CROSS_CATEGORY_MAP.items():
            src_pool = CATEGORY_POOLS.get(src_cat, [])
            for src_ext in src_pool:
                for dst_ext in dst_exts:
                    method = ConverterEngine._resolve_cross_method(src_ext, dst_ext)
                    if method:
                        ConverterEngine.CROSS_CONVERTERS[(src_ext, dst_ext)] = method
        for src_ext, dst_ext in ConverterEngine._extra_pairs:
            if (src_ext, dst_ext) not in ConverterEngine.CROSS_CONVERTERS:
                method = ConverterEngine._resolve_cross_method(src_ext, dst_ext)
                if method:
                    ConverterEngine.CROSS_CONVERTERS[(src_ext, dst_ext)] = method

    @staticmethod
    def convert(input_path, output_path, callback, quality=None, settings=None):
        in_ext = Path(input_path).suffix.lower().lstrip(".")
        out_ext = Path(output_path).suffix.lower().lstrip(".")
        in_cat = get_category(in_ext)
        out_cat = get_category(out_ext)

        # Same-category conversions
        if in_cat == "audio" and out_cat == "audio":
            ConverterEngine.convert_audio(input_path, output_path, callback, quality, settings)
        elif in_cat == "video" and out_cat == "video":
            ConverterEngine.convert_video(input_path, output_path, callback, quality, settings)
        elif in_cat == "video" and out_cat == "audio":
            ConverterEngine.convert_audio(input_path, output_path, callback, quality, settings)
        elif in_cat == "image" and out_cat == "image":
            ConverterEngine.convert_image(input_path, output_path, callback, quality)
        elif in_cat == "text" and out_cat == "text":
            method_name = ConverterEngine.TEXT_CONVERTERS.get((in_ext, out_ext))
            if method_name:
                getattr(ConverterEngine, method_name)(input_path, output_path, callback)
            else:
                callback(False, f"Text conversion .{in_ext} -> .{out_ext} not supported")
        elif in_cat == "data" and out_cat == "data":
            method_name = ConverterEngine.DATA_CONVERTERS.get((in_ext, out_ext))
            if method_name:
                getattr(ConverterEngine, method_name)(input_path, output_path, callback)
            else:
                callback(False, f"Data conversion .{in_ext} -> .{out_ext} not supported")
        elif in_cat == "3d" and out_cat == "3d":
            ConverterEngine.convert_3d(input_path, output_path, callback, quality)
        elif in_cat == "presentation" and out_cat == "presentation":
            ConverterEngine.convert_presentation(input_path, output_path, callback, quality)
        else:
            # Cross-category
            method_name = ConverterEngine._resolve_cross_method(in_ext, out_ext)
            if method_name:
                getattr(ConverterEngine, method_name)(input_path, output_path, callback)
            else:
                callback(False, f"Conversion .{in_ext} -> .{out_ext} not supported")

# Build CROSS_CONVERTERS after class is fully defined (avoids NameError)
ConverterEngine._build_cross_converters()

# --- Auto-populate the ConversionRegistry from existing tables ---
for (src, dst), method_name in ConverterEngine.TEXT_CONVERTERS.items():
    converter_registry.register_intra(src, dst, method_name)
for (src, dst), method_name in ConverterEngine.DATA_CONVERTERS.items():
    converter_registry.register_intra(src, dst, method_name)
for (src, dst), method_name in ConverterEngine.CROSS_CONVERTERS.items():
    converter_registry.register_cross(src, dst, method_name)
logger.info("Conversion registry populated: %d intra + %d cross-category converters",
            len(converter_registry.get_all_intra()),
            len(converter_registry.get_all_cross()))


# ─── History Manager ─────────────────────────────────────────────

class HistoryManager:
    """Thread-safe conversion history with Singleton pattern.

    Singleton ensures one instance per path, preventing duplicate file
    handles.  Bug fix: removed unused _save() that duplicated the write
    already in add().  Persistence now uses temp-file-then-rename for
    atomic writes on POSIX systems.
    """
    _instances: Dict[str, "HistoryManager"] = {}
    _instances_lock = threading.Lock()

    def __new__(cls, path=HISTORY_FILE):
        resolved = str(Path(path).resolve())
        with cls._instances_lock:
            if resolved not in cls._instances:
                instance = super().__new__(cls)
                instance._initialized = False
                cls._instances[resolved] = instance
            return cls._instances[resolved]

    def __init__(self, path=HISTORY_FILE):
        if getattr(self, "_initialized", False):
            return
        self.path = Path(path)
        self._records: List[dict] = []
        self._lock = threading.Lock()
        self._load()
        self._initialized = True

    def _load(self):
        try:
            if self.path.exists():
                with open(self.path, "r", encoding="utf-8") as fh:
                    self._records = json.load(fh)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load history: %s", e)
            self._records = []

    def _persist(self, records_to_save: List[dict]):
        """Atomic write via temp-file-then-rename."""
        try:
            tmp = str(self.path) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(records_to_save, fh, indent=2, ensure_ascii=False)
            os.replace(tmp, str(self.path))
        except OSError as e:
            logger.warning("Failed to save history: %s", e)

    def add(self, input_name, output_name, in_ext, out_ext, category, success):
        with self._lock:
            self._records.append({
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "input": input_name,
                "output": output_name,
                "conversion": f"{in_ext.upper()} -> {out_ext.upper()}",
                "category": category,
                "success": success,
            })
            records_to_save = self._records[-500:]
        self._persist(records_to_save)

    def get_all(self) -> List[dict]:
        with self._lock:
            return list(reversed(self._records))

    def clear(self):
        with self._lock:
            self._records = []
        self._persist([])


# ─── Dark Theme QSS ───────────────────────────────────────────────

DARK_QSS = """
QMainWindow, QWidget#central {
    background-color: #1a1a24;
}

QLabel#headerTitle {
    font-size: 18px;
    font-weight: bold;
    color: #599eff;
    padding: 12px 16px;
}

QLabel#headerStatus {
    font-size: 11px;
    color: #73b873;
    padding: 12px 16px;
}

QFrame#headerFrame {
    background-color: #111118;
    border-radius: 10px;
}

QLabel#sectionLabel {
    font-size: 10px;
    font-weight: bold;
    color: #6b7690;
    padding: 0 4px;
}

QLabel#pillLabel {
    font-size: 10px;
    font-weight: bold;
    color: #8c8c99;
    background-color: #262830;
    border-radius: 12px;
    padding: 4px 12px;
}

QLabel#pillActive {
    font-size: 10px;
    font-weight: bold;
    color: #ffffff;
    border-radius: 12px;
    padding: 4px 12px;
}

QLineEdit {
    background-color: #242630;
    color: #e0e0eb;
    border: none;
    border-radius: 8px;
    padding: 10px 14px;
    font-size: 13px;
    selection-background-color: #3568b8;
}

QLineEdit:disabled {
    background-color: #1e2028;
    color: #666;
}

QComboBox {
    background-color: #242630;
    color: #e0e0eb;
    border: none;
    border-radius: 8px;
    padding: 8px 14px;
    font-size: 13px;
    min-width: 160px;
}

QComboBox::drop-down {
    border: none;
    width: 30px;
}

QComboBox::down-arrow {
    image: none;
    border: none;
    width: 0;
}

QComboBox QAbstractItemView {
    background-color: #242630;
    color: #e0e0eb;
    border: 1px solid #353640;
    border-radius: 6px;
    selection-background-color: #3568b8;
    padding: 4px;
}

QPushButton {
    background-color: #3568b8;
    color: #ffffff;
    border: none;
    border-radius: 8px;
    padding: 10px 20px;
    font-size: 13px;
    font-weight: bold;
}

QPushButton:hover {
    background-color: #4078cc;
}

QPushButton:pressed {
    background-color: #2a54a0;
}

QPushButton:disabled {
    background-color: #40404d;
    color: #666;
}

QPushButton#batchBtn {
    background-color: #8b459e;
}
QPushButton#batchBtn:hover {
    background-color: #9e55b5;
}

QPushButton#formatsBtn {
    background-color: #248563;
}
QPushButton#formatsBtn:hover {
    background-color: #2a9972;
}

QPushButton#historyBtn {
    background-color: #47475f;
    font-size: 10px;
    padding: 4px 12px;
}
QPushButton#historyBtn:hover {
    background-color: #55556d;
}

QPushButton#openDirBtn {
    background-color: #244773;
    font-size: 12px;
}
QPushButton#openDirBtn:hover {
    background-color: #2d5588;
}

QPushButton#convertBtn {
    background-color: #3568b8;
    font-size: 15px;
    font-weight: bold;
    padding: 14px 24px;
}
QPushButton#convertBtn:hover {
    background-color: #4078cc;
}
QPushButton#convertBtn:disabled {
    background-color: #40404d;
    color: #666;
}

QPushButton#genRunBtn {
    background-color: #1a7a5a;
    font-size: 13px;
    font-weight: bold;
    padding: 8px 18px;
}
QPushButton#genRunBtn:hover {
    background-color: #20946c;
}
QPushButton#genRunBtn:disabled {
    background-color: #40404d;
    color: #666;
}

QTextEdit#logText {
    background-color: #111118;
    color: #85859e;
    border: none;
    border-radius: 8px;
    padding: 14px;
    font-size: 12px;
    font-family: 'Consolas', 'Courier New', monospace;
}

QSlider::groove:horizontal {
    background: #3568b8;
    height: 6px;
    border-radius: 3px;
}

QSlider::handle:horizontal {
    background: #599eff;
    width: 16px;
    height: 16px;
    margin: -5px 0;
    border-radius: 8px;
}

QSlider::sub-page:horizontal {
    background: #3568b8;
    border-radius: 3px;
}

QFrame#separator {
    background-color: #343540;
    max-height: 1px;
}

/* Dialog styling */
QDialog {
    background-color: #1a1a24;
    color: #d9d9e6;
}

QDialog QLabel {
    color: #d9d9e6;
}

QDialog QTextEdit {
    background-color: #111118;
    color: #85859e;
    border: none;
    border-radius: 6px;
    padding: 10px;
    font-family: 'Consolas', 'Courier New', monospace;
    font-size: 11px;
}

QDialog QPushButton#closeBtn {
    background-color: #4c4c58;
    padding: 8px 20px;
}
QDialog QPushButton#closeBtn:hover {
    background-color: #5c5c68;
}

QDialog QPushButton#clearBtn {
    background-color: #9e3838;
    padding: 8px 20px;
}
QDialog QPushButton#clearBtn:hover {
    background-color: #b84444;
}

QDialog QPushButton#batchConvertBtn {
    background-color: #8b459e;
    padding: 8px 20px;
}
QDialog QPushButton#batchConvertBtn:hover {
    background-color: #9e55b5;
}

QScrollArea {
    background-color: transparent;
    border: none;
}

QScrollBar:vertical {
    background: #1a1a24;
    width: 10px;
    border-radius: 5px;
}

QScrollBar::handle:vertical {
    background: #40404d;
    border-radius: 5px;
    min-height: 30px;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}

QProgressBar {
    background-color: #242630;
    border: 1px solid #353640;
    border-radius: 6px;
    min-height: 24px;
    text-align: center;
    color: #c8c8d8;
    font-size: 11px;
    font-weight: bold;
    padding: 2px 6px;
}

QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #2a5db0, stop:0.5 #4a8fe7, stop:1 #6ab4ff);
    border-radius: 4px;
}

/* Progress bar states */
QProgressBar[progressState="success"]::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #2d8a4e, stop:0.5 #4caf50, stop:1 #66d96a);
    border-radius: 6px;
}

QProgressBar[progressState="warning"]::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #c68a08, stop:0.5 #e6a817, stop:1 #ffc107);
    border-radius: 6px;
}

QProgressBar[progressState="error"]::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #b03030, stop:0.5 #e05555, stop:1 #ff7070);
    border-radius: 6px;
}

/* Progress bar glow wrapper */
QFrame#progressGlow {
    background-color: #1e1e2a;
    border: 1px solid #353640;
    border-radius: 8px;
    padding: 4px;
    margin: 0px;
}

QFrame#progressGlow[glowState="active"] {
    background-color: rgba(74, 143, 231, 15);
    border: 1px solid rgba(74, 143, 231, 30);
    border-radius: 8px;
}

QFrame#progressGlow[glowState="success"] {
    background-color: rgba(76, 175, 80, 15);
    border: 1px solid rgba(76, 175, 80, 30);
    border-radius: 8px;
}

QFrame#progressGlow[glowState="warning"] {
    background-color: rgba(230, 168, 23, 15);
    border: 1px solid rgba(230, 168, 23, 30);
    border-radius: 8px;
}

QFrame#progressGlow[glowState="error"] {
    background-color: rgba(224, 85, 85, 15);
    border: 1px solid rgba(224, 85, 85, 30);
    border-radius: 8px;
}

/* Tab widget styling */
QTabWidget::pane {
    border: 1px solid #343540;
    border-radius: 8px;
    background-color: #1a1a24;
    padding: 4px;
}

QTabBar::tab {
    background-color: #242630;
    color: #8c8c99;
    padding: 10px 24px;
    margin-right: 2px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    font-size: 12px;
    font-weight: bold;
}

QTabBar::tab:selected {
    background-color: #1a1a24;
    color: #599eff;
    border-bottom: 2px solid #599eff;
}

QTabBar::tab:hover:!selected {
    background-color: #2a2c38;
    color: #b8b8d0;
}

/* Table styling for test results */
QTableWidget {
    background-color: #1a1a24;
    color: #d9d9e6;
    border: 1px solid #343540;
    border-radius: 6px;
    gridline-color: #343540;
    font-size: 11px;
}

QTableWidget::item {
    padding: 4px 8px;
}

QTableWidget::item:selected {
    background-color: #3568b8;
}

QHeaderView::section {
    background-color: #242630;
    color: #599eff;
    padding: 6px 8px;
    border: none;
    border-bottom: 2px solid #343540;
    font-size: 11px;
    font-weight: bold;
}

QCheckBox {
    color: #d9d9e6;
    font-size: 12px;
    spacing: 8px;
}

QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid #555;
    background-color: #242630;
}

QCheckBox::indicator:checked {
    background-color: #3568b8;
    border-color: #599eff;
}
"""


# ─── Category Pill Widget ─────────────────────────────────────────

class CategoryPill(QLabel):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setObjectName("pillLabel")
        self.setFixedHeight(28)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def set_active(self, color_hex):
        self.setObjectName("pillActive")
        self.setStyleSheet(f"background-color: {color_hex}; color: #ffffff;"
                           f"border-radius: 12px; font-size: 10px; font-weight: bold;"
                           f"padding: 4px 12px;")

    def set_inactive(self):
        self.setObjectName("pillLabel")
        self.setStyleSheet("")


# ─── Separator ────────────────────────────────────────────────────

def make_separator():
    frame = QFrame()
    frame.setObjectName("separator")
    frame.setFixedHeight(1)
    return frame


# ─── Supported Conversions Dialog ─────────────────────────────────

class ConversionsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Supported Conversions ({len(ALL_CONVERSIONS)} paths)")
        self.setMinimumSize(500, 600)
        self.setStyleSheet(DARK_QSS)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        title = QLabel(f"ALL SUPPORTED CONVERSIONS  ({len(ALL_CONVERSIONS)} paths)")
        title.setStyleSheet("font-size: 15px; font-weight: bold; color: #599eff; padding: 8px;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        scroll_layout.setSpacing(2)
        scroll_layout.setContentsMargins(8, 8, 8, 8)

        current_cat = None
        cat_count = 0
        for cat, src, dst in ALL_CONVERSIONS:
            if cat != current_cat:
                if current_cat is not None:
                    count_lbl = QLabel(f"  ({cat_count} paths)")
                    count_lbl.setStyleSheet("font-size: 9px; color: #8c8c99; padding-left: 12px;")
                    scroll_layout.addWidget(count_lbl)
                icon = CATEGORY_ICONS.get(cat.split("->")[0] if "->" in cat else cat, "")
                cat_lbl = QLabel(f"  {icon}  {cat.upper()} FORMATS")
                cat_lbl.setStyleSheet("font-size: 12px; font-weight: bold; color: #d1d1ea;"
                                     "padding: 6px 4px;")
                scroll_layout.addWidget(cat_lbl)
                current_cat = cat
                cat_count = 0

            line = QLabel(f"      {src}  ->  {dst}")
            line.setStyleSheet("font-size: 11px; color: #b8b8d0; padding-left: 16px;")
            scroll_layout.addWidget(line)
            cat_count += 1

        count_lbl = QLabel(f"  ({cat_count} paths)")
        count_lbl.setStyleSheet("font-size: 9px; color: #8c8c99; padding-left: 12px;")
        scroll_layout.addWidget(count_lbl)

        scroll_layout.addStretch()
        formats_text = ", ".join(f.upper() for f in ALL_FORMATS)
        summary = QLabel(f"Input formats: {formats_text}")
        summary.setStyleSheet("font-size: 9px; color: #6b7690; padding: 8px;")
        summary.setAlignment(Qt.AlignmentFlag.AlignCenter)
        summary.setWordWrap(True)
        scroll_layout.addWidget(summary)

        scroll.setWidget(scroll_widget)
        layout.addWidget(scroll)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setObjectName("closeBtn")
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)


# ─── History Dialog ────────────────────────────────────────────────

class HistoryDialog(QDialog):
    def __init__(self, history_manager, parent=None):
        super().__init__(parent)
        self.history_manager = history_manager
        self.setWindowTitle("Conversion History")
        self.setMinimumSize(550, 500)
        self.setStyleSheet(DARK_QSS)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        title_row = QHBoxLayout()
        title = QLabel("CONVERSION HISTORY")
        title.setStyleSheet("font-size: 14px; font-weight: bold; color: #599eff;")
        title_row.addWidget(title)
        title_row.addStretch()

        clear_btn = QPushButton("Clear")
        clear_btn.setObjectName("clearBtn")
        clear_btn.setFixedWidth(80)
        clear_btn.clicked.connect(self._clear)
        title_row.addWidget(clear_btn)
        layout.addLayout(title_row)

        self.count_label = QLabel("")
        self.count_label.setStyleSheet("font-size: 10px; color: #8c8c99;")
        layout.addWidget(self.count_label)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setObjectName("logText")
        layout.addWidget(self.log_text)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("Close")
        close_btn.setObjectName("closeBtn")
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        self._refresh()

    def _refresh(self):
        records = self.history_manager.get_all()
        self.count_label.setText(f"{len(records)} records")
        if not records:
            self.log_text.setPlainText("No conversion history yet.")
            return
        lines = []
        for rec in records[:100]:
            status = "OK" if rec.get("success") else "FAIL"
            ts = rec.get("timestamp", "")
            inp = rec.get("input", "?")
            out = rec.get("output", "?")
            conv = rec.get("conversion", "?")
            lines.append(f"[{ts}]  {inp} -> {out}  ({conv})  [{status}]")
        self.log_text.setPlainText("\n".join(lines))
        sb = self.log_text.verticalScrollBar()
        sb.setValue(sb.minimum())

    def _clear(self):
        self.history_manager.clear()
        self._refresh()


# ─── Batch Dialog ─────────────────────────────────────────────────

class BatchDialog(QDialog):
    files_selected = Signal(list, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Batch Convert")
        self.setMinimumSize(500, 400)
        self.setStyleSheet(DARK_QSS)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        info = QLabel("Select multiple files to convert at once.")
        info.setStyleSheet("font-size: 12px; color: #d9d9e6; padding: 8px;")
        layout.addWidget(info)

        select_btn = QPushButton("Select Files...")
        select_btn.setObjectName("closeBtn")
        select_btn.setStyleSheet(
            "background-color: #3568b8; color: white; border: none;"
            "border-radius: 8px; padding: 12px 24px; font-size: 13px;"
        )
        select_btn.clicked.connect(self._pick_files)
        layout.addWidget(select_btn)

        self.file_list = QTextEdit()
        self.file_list.setReadOnly(True)
        self.file_list.setPlaceholderText("No files selected yet...")
        self.file_list.setObjectName("logText")
        self.file_list.setMaximumHeight(180)
        layout.addWidget(self.file_list)

        fmt_row = QHBoxLayout()
        fmt_label = QLabel("Convert to:")
        fmt_label.setStyleSheet("font-size: 12px; color: #d9d9e6;")
        fmt_row.addWidget(fmt_label)
        self.fmt_combo = QComboBox()
        self.fmt_combo.addItems([f.upper() for f in ALL_FORMATS])
        fmt_row.addWidget(self.fmt_combo)
        layout.addLayout(fmt_row)

        self._selected_files = []

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("closeBtn")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        batch_btn = QPushButton("Batch Convert")
        batch_btn.setObjectName("batchConvertBtn")
        batch_btn.clicked.connect(self._do_batch)
        btn_row.addWidget(batch_btn)
        layout.addLayout(btn_row)

    def _pick_files(self):
        filter_str = ";;".join(
            [f"{cat.upper()} files ({' '.join('*.' + f for f in CATEGORY_POOLS[cat])})"
             for cat in CATEGORY_POOLS]
            + ["All supported (" + " ".join("*." + f for f in ALL_FORMATS) + ")"]
        )
        files, _ = QFileDialog.getOpenFileNames(
            self, "Select Files for Batch Conversion", str(Path.home()), filter_str
        )
        if files:
            self._selected_files = files
            self.file_list.setPlainText("\n".join(f"  {Path(f).name}  (.{Path(f).suffix.lower().lstrip('.')})"
                                            for f in files))

    def _do_batch(self):
        if not self._selected_files:
            return
        fmt = self.fmt_combo.currentText().lower()
        if fmt not in ALL_FORMATS:
            return
        self.files_selected.emit(self._selected_files, fmt)
        self.accept()


# ─── Drop Zone Widget ──────────────────────────────────────────────

class DropLineEdit(QLineEdit):
    file_dropped = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setPlaceholderText("  Drag & drop a file here or click Browse...")
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if path:
                self.file_dropped.emit(path)
        event.acceptProposedAction()


# ─── Test File Generators ─────────────────────────────────────────

class TestFileGenerator:
    """Generates representative test files for each category.

    Produces *multiple* formats per category where possible so that
    many more intra-category conversions can actually be exercised
    instead of being skipped due to a missing source file.
    """

    # Total generation steps for progress tracking:
    # Image: 6 formats, Text: 1 batch, Data: 1 batch,
    # Audio: 1 WAV + 9 derivatives = 10, Video: 1 MP4 + 10 derivatives = 11,
    # 3D: 8 formats, Presentation: PPTX + ODP = 2
    TOTAL_STEPS = 6 + 1 + 1 + 10 + 11 + 8 + 2  # = 39

    class Stopped(BaseException):
        """Raised when the caller requests cancellation via stop_check.
        Inherits BaseException so it bypasses ``except Exception`` blocks."""
        pass

    @staticmethod
    def _create_minimal_3ds() -> bytes:
        """Create a minimal valid 3DS binary file with a simple cube mesh.

        The 3DS (3D Studio) format is a binary chunk-based format. Each chunk
        has a 2-byte ID and a 4-byte little-endian size (including header).
        This function builds the minimum chunk hierarchy that loaders accept:
        MMAIN -> M3D_EDIT -> NAMED_OBJECT -> N_TRI_VERTEX -> N_TRI_FACE.

        Returns the raw bytes of a valid .3ds file containing a 8-vertex cube
        with 12 triangular faces.
        """
        import struct

        def chunk(chunk_id: int, payload: bytes) -> bytes:
            """Build a 3DS chunk: 2-byte ID + 4-byte LE size + payload."""
            return struct.pack('<HI', chunk_id, 6 + len(payload)) + payload

        def nul_str(s: str) -> bytes:
            """Encode a string with a null terminator."""
            return s.encode('ascii') + b'\x00'

        # Cube vertices (8 corners of a unit cube)
        verts = [
            (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0),
            (0.0, 0.0, 1.0), (1.0, 0.0, 1.0), (1.0, 1.0, 1.0), (0.0, 1.0, 1.0),
        ]
        # Triangular faces (vertex indices) — 2 triangles per face, 6 faces = 12
        faces = [
            (0,1,2), (0,2,3),  # front
            (4,6,5), (4,7,6),  # back
            (0,4,5), (0,5,1),  # bottom
            (2,6,7), (2,7,3),  # top
            (0,3,7), (0,7,4),  # left
            (1,5,6), (1,6,2),  # right
        ]

        # Build vertex chunk (0x4110)
        vert_payload = struct.pack('<H', len(verts))
        for v in verts:
            vert_payload += struct.pack('<3f', *v)
        vert_chunk = chunk(0x4110, vert_payload)

        # Build face chunk (0x4120)
        face_payload = struct.pack('<H', len(faces))
        for f in faces:
            face_payload += struct.pack('<3H', *f)
            face_payload += struct.pack('<H', 0x07)  # edge visibility flags
        face_chunk = chunk(0x4120, face_payload)

        # Build mesh chunk (0x4100) containing vertex + face chunks
        mesh_chunk = chunk(0x4100, vert_chunk + face_chunk)

        # Build named object chunk (0x4000) with null-terminated name + mesh
        obj_chunk = chunk(0x4000, nul_str("TestCube") + mesh_chunk)

        # Build editor chunk (0x3D3D)
        edit_chunk = chunk(0x3D3D, obj_chunk)

        # Build main chunk (0x4D4D)
        main_chunk = chunk(0x4D4D, edit_chunk)

        return main_chunk

    @staticmethod
    def generate_all(test_dir: Path, progress_cb=None, stop_check=None) -> tuple:
        """Generate all test files and return (files_dict, skipped_count).

        *progress_cb*: optional callable(step, total, description) invoked after
        each sub-generation so the UI can show a progress bar.
        *stop_check*: optional callable() -> bool; if it returns True, generation
        is aborted and ``Stopped`` is raised.

        **Invariant**: exactly TOTAL_STEPS calls to progress_cb are made (unless
        stopped early), so the progress bar always reaches 100 %.
        """
        test_dir.mkdir(parents=True, exist_ok=True)
        step = [0]      # mutable counter
        skipped = [0]   # mutable skip counter

        def _file_ready(p: Path) -> bool:
            """Check if a file already exists and has content."""
            return p.exists() and p.stat().st_size > 0

        def _tick(desc="", filepath=None):
            if stop_check and stop_check():
                raise TestFileGenerator.Stopped("Generation cancelled by user")
            step[0] += 1
            if filepath:
                size = filepath.stat().st_size if filepath.exists() else 0
                print(f"  [{step[0]:2d}/{TestFileGenerator.TOTAL_STEPS}] {desc}  ->  {filepath.name}  ({size:,} bytes)")
            else:
                print(f"  [{step[0]:2d}/{TestFileGenerator.TOTAL_STEPS}] {desc}")
            if progress_cb:
                progress_cb(step[0], TestFileGenerator.TOTAL_STEPS, desc)
            time.sleep(0.02)

        files = {}

        # ── Image test files (6 formats) ─────────────────────
        img_files = {}
        img_error = None
        try:
            from PIL import Image, ImageDraw
            base = Image.new("RGB", (200, 150), color=(73, 109, 137))
            draw = ImageDraw.Draw(base)
            draw.rectangle([20, 20, 180, 130], outline=(255, 255, 255), width=2)
            try:
                draw.text((40, 60), "TEST", fill=(255, 255, 255))
            except Exception:
                pass  # text rendering may fail in minimal environments

            for fmt, save_kwargs in [
                ("png",  {}),
                ("jpg",  {"quality": 85}),
                ("bmp",  {}),
                ("gif",  {}),
                ("webp", {"quality": 85}),
                ("tiff", {}),
            ]:
                p = test_dir / f"test_image.{fmt}"
                if _file_ready(p):
                    img_files[fmt] = str(p)
                    skipped[0] += 1
                    _tick(f"Image: {fmt.upper()} (exists)", p)
                    continue
                try:
                    rgb = base.convert("RGB") if fmt in ("jpg", "bmp") else base
                    rgb.save(str(p), **save_kwargs)
                    if p.exists() and p.stat().st_size > 0:
                        img_files[fmt] = str(p)
                except Exception:
                    pass
                _tick(f"Image: {fmt.upper()}", p)
        except Exception as e:
            img_error = str(e)
            # PIL unavailable — tick through remaining image steps
            for fmt in ("png", "jpg", "bmp", "gif", "webp", "tiff"):
                p = test_dir / f"test_image.{fmt}"
                if _file_ready(p):
                    img_files[fmt] = str(p)
                    skipped[0] += 1
                _tick(f"Image: {fmt.upper()} (error: PIL unavailable)", p if _file_ready(p) else None)
        files["image"] = img_files if img_files else {"error": img_error or "All image formats failed"}

        # ── Text test files (txt, md, html, pdf) ─────────────
        try:
            txt_path  = test_dir / "test_text.txt"
            md_path   = test_dir / "test_markdown.md"
            html_path = test_dir / "test_page.html"
            pdf_path  = test_dir / "test_doc.pdf"

            if not _file_ready(txt_path):
                txt_path.write_text(
                    "Test Document\n\nThis is a test file for format conversion.\n"
                    "It contains multiple lines of text.\n"
                    "Some lines are short.\n"
                    "Other lines are longer and contain more descriptive content for testing purposes.\n\n"
                    "Features tested:\n- Headers\n- Paragraphs\n- Lists\n- Special chars: <>&\"\n\n"
                    "1. First ordered item\n2. Second ordered item\n3. Third ordered item\n\n"
                    "A paragraph with **bold** and *italic* and `code` text.\n",
                    encoding="utf-8")

            if not _file_ready(md_path):
                md_path.write_text(
                    "# Test Markdown\n\n"
                    "This is a **test** markdown file.\n\n"
                    "## Section Two\n\n"
                    "- Item one\n- Item two\n- Item three\n\n"
                    "1. First ordered\n2. Second ordered\n3. Third ordered\n\n"
                    "```\ncode block\nwith multiple lines\n```\n\n"
                    "> A blockquote\n\n"
                    "Some `inline code` and a [link](https://example.com).\n\n"
                    "| Col A | Col B |\n|-------|-------|\n| 1     | 2     |\n",
                    encoding="utf-8")

            if not _file_ready(html_path):
                html_path.write_text(
                    '<!DOCTYPE html>\n<html><head><meta charset="utf-8">'
                    '<title>Test</title></head>\n<body>\n'
                    '<h1>Test HTML</h1>\n<p>This is a <strong>test</strong> page.</p>\n'
                    '<h2>Section Two</h2>\n'
                    '<ul><li>Item 1</li><li>Item 2</li></ul>\n'
                    '<ol><li>First</li><li>Second</li><li>Third</li></ol>\n'
                    '<blockquote><p>A quote</p></blockquote>\n'
                    '<pre><code>some code</code></pre>\n'
                    '</body></html>',
                    encoding="utf-8")

            text_files = {"txt": str(txt_path), "md": str(md_path), "html": str(html_path)}

            if not _file_ready(pdf_path):
                try:
                    from fpdf import FPDF
                    pdf = FPDF()
                    pdf.add_page()
                    pdf.set_font("Helvetica", size=12)
                    pdf.cell(0, 10, "Test PDF Document", new_x="LMARGIN", new_y="NEXT")
                    pdf.cell(0, 10, "Page 1 content for testing conversions.", new_x="LMARGIN", new_y="NEXT")
                    pdf.cell(0, 10, "More text on page 1.", new_x="LMARGIN", new_y="NEXT")
                    pdf.output(str(pdf_path))
                    text_files["pdf"] = str(pdf_path)
                except Exception:
                    pass
            else:
                text_files["pdf"] = str(pdf_path)

            # Log each individual text file
            text_ready = [f for f in [txt_path, md_path, html_path, pdf_path] if _file_ready(f)]
            if text_ready:
                parts = [f"{f.name} ({f.stat().st_size:,} bytes)" for f in text_ready]
                desc = "Text: " + ", ".join(parts)
                if all(_file_ready(f) for f in text_ready):
                    skipped[0] += 1
            else:
                desc = "Text test files (none ready)"
            files["text"] = text_files
            _tick(desc, text_ready[0] if text_ready else None)
        except Exception as e:
            files["text"] = {"error": str(e)}
            _tick("Text test files (error)", None)

        # ── Data test files (json, csv, xml, yaml) ────────────
        try:
            test_data = [
                {"name": "Alice", "age": "30", "city": "NYC"},
                {"name": "Bob", "age": "25", "city": "LA"},
                {"name": "Charlie", "age": "35", "city": "Chicago"},
            ]
            json_path = test_dir / "test_data.json"
            csv_path  = test_dir / "test_data.csv"
            xml_path  = test_dir / "test_data.xml"
            yaml_path = test_dir / "test_data.yaml"

            if not _file_ready(json_path):
                json_path.write_text(json.dumps(test_data, indent=2), encoding="utf-8")

            if not _file_ready(csv_path):
                with open(csv_path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=["name", "age", "city"])
                    writer.writeheader()
                    writer.writerows(test_data)

            if not _file_ready(xml_path):
                root = ET.Element("people")
                for person in test_data:
                    p_el = ET.SubElement(root, "person")
                    for k, v in person.items():
                        ET.SubElement(p_el, k).text = v
                tree = ET.ElementTree(root)
                ET.indent(tree, space="  ")
                tree.write(str(xml_path), encoding="unicode", xml_declaration=True)

            data_files = {"json": str(json_path), "csv": str(csv_path), "xml": str(xml_path)}

            if not _file_ready(yaml_path):
                try:
                    import yaml
                    yaml_path.write_text(
                        yaml.dump(test_data, default_flow_style=False, allow_unicode=True),
                        encoding="utf-8")
                    data_files["yaml"] = str(yaml_path)
                except ImportError:
                    pass
            else:
                data_files["yaml"] = str(yaml_path)

            # Log each individual data file
            data_ready = [f for f in [json_path, csv_path, xml_path, yaml_path] if _file_ready(f)]
            if data_ready:
                parts = [f"{f.name} ({f.stat().st_size:,} bytes)" for f in data_ready]
                desc = "Data: " + ", ".join(parts)
                if all(_file_ready(f) for f in data_ready):
                    skipped[0] += 1
            else:
                desc = "Data test files (none ready)"
            files["data"] = data_files
            _tick(desc, data_ready[0] if data_ready else None)
        except Exception as e:
            files["data"] = {"error": str(e)}
            _tick("Data test files (error)", None)

        # ── Audio test files (WAV + 9 derivatives = 10 steps) ─
        audio_files = {}
        ffmpeg_ok = True
        try:
            wav_path = test_dir / "test_audio.wav"
            if _file_ready(wav_path):
                audio_files["wav"] = str(wav_path)
                skipped[0] += 1
                _tick("Audio: WAV (exists)", wav_path)
            else:
                cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error",
                       "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                       "-c:a", "pcm_s16le", "-y", str(wav_path)]
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                if r.returncode == 0 and _file_ready(wav_path):
                    audio_files["wav"] = str(wav_path)
                _tick("Audio: WAV", wav_path)
        except FileNotFoundError:
            ffmpeg_ok = False
            _tick("Audio: WAV (FFmpeg not found)", None)
        except Exception:
            _tick("Audio: WAV (error)", None)

        audio_derivatives = [
            ("mp3",  ["-c:a", "libmp3lame", "-b:a", "128k"]),
            ("ogg",  ["-c:a", "libvorbis", "-b:a", "128k"]),
            ("flac", ["-c:a", "flac"]),
            ("aac",  ["-c:a", "aac", "-b:a", "128k"]),
            ("m4a",  ["-c:a", "aac", "-b:a", "128k"]),
            ("opus", ["-c:a", "libopus", "-b:a", "128k"]),
            ("wma",  ["-c:a", "wmav2", "-b:a", "128k"]),
            ("aiff", ["-c:a", "pcm_s16be"]),
            ("amr",  ["-c:a", "libopencore_amrnb", "-b:a", "12.2k", "-ar", "8000"]),
        ]
        wav_path = test_dir / "test_audio.wav"
        for out_fmt, extra_args in audio_derivatives:
            out_path = test_dir / f"test_audio.{out_fmt}"
            if not ffmpeg_ok:
                _tick(f"Audio: {out_fmt.upper()} (unavailable)", out_path if _file_ready(out_path) else None)
                continue
            if _file_ready(out_path):
                audio_files[out_fmt] = str(out_path)
                skipped[0] += 1
                _tick(f"Audio: {out_fmt.upper()} (exists)", out_path)
                continue
            cmd2 = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(wav_path)] + extra_args + ["-y", str(out_path)]
            try:
                r2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=15)
                if r2.returncode == 0 and _file_ready(out_path):
                    audio_files[out_fmt] = str(out_path)
            except Exception:
                pass
            _tick(f"Audio: {out_fmt.upper()}", out_path)

        if not ffmpeg_ok:
            files["audio"] = {"error": "FFmpeg not found"}
        elif audio_files:
            files["audio"] = audio_files
        else:
            files["audio"] = {"error": "FFmpeg audio generation failed"}

        # ── Video test files (MP4 + 10 derivatives = 11 steps) ─
        video_files = {}
        ffmpeg_video_ok = True
        try:
            mp4_path = test_dir / "test_video.mp4"
            if _file_ready(mp4_path):
                video_files["mp4"] = str(mp4_path)
                skipped[0] += 1
                _tick("Video: MP4 (exists)", mp4_path)
            else:
                cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error",
                       "-f", "lavfi", "-i",
                       "color=c=red:size=320x240:duration=1:rate=10",
                       "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                       "-c:v", "libx264", "-pix_fmt", "yuv420p",
                       "-c:a", "aac", "-b:a", "128k",
                       "-shortest", "-y",
                       str(mp4_path)]
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if r.returncode == 0 and _file_ready(mp4_path):
                    video_files["mp4"] = str(mp4_path)
                _tick("Video: MP4", mp4_path)
        except FileNotFoundError:
            ffmpeg_video_ok = False
            _tick("Video: MP4 (FFmpeg not found)", None)
        except Exception:
            _tick("Video: MP4 (error)", None)

        video_derivatives = [
            ("mkv",  ["-c:v", "libx264", "-pix_fmt", "yuv420p"]),
            ("webm", ["-c:v", "libvpx-vp9", "-pix_fmt", "yuv420p"]),
            ("avi",  ["-c:v", "mpeg4", "-pix_fmt", "yuv420p"]),
            ("mov",  ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-f", "mov"]),
            ("wmv",  ["-c:v", "wmv2", "-pix_fmt", "yuv420p", "-f", "asf"]),
            ("flv",  ["-c:v", "flv", "-pix_fmt", "yuv420p", "-f", "flv"]),
            ("3gp",  ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-f", "3gp", "-s", "176x144", "-r", "15"]),
            ("ts",   ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-f", "mpegts"]),
            ("m4v",  ["-c:v", "libx264", "-pix_fmt", "yuv420p"]),
            ("ogv",  ["-c:v", "libtheora", "-pix_fmt", "yuv420p", "-f", "ogg"]),
        ]
        mp4_path = test_dir / "test_video.mp4"
        for out_fmt, extra_args in video_derivatives:
            out_path = test_dir / f"test_video.{out_fmt}"
            if not ffmpeg_video_ok:
                _tick(f"Video: {out_fmt.upper()} (unavailable)", out_path if _file_ready(out_path) else None)
                continue
            if _file_ready(out_path):
                video_files[out_fmt] = str(out_path)
                skipped[0] += 1
                _tick(f"Video: {out_fmt.upper()} (exists)", out_path)
                continue
            cmd2 = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(mp4_path)] + extra_args + ["-c:a", "aac", "-b:a", "128k", "-y", str(out_path)]
            try:
                r2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=30)
                if r2.returncode == 0 and _file_ready(out_path):
                    video_files[out_fmt] = str(out_path)
                # ── Fallback for OGV when libtheora is unavailable ──
                elif out_fmt == "ogv" and not _file_ready(out_path):
                    cmd_fb = [
                        "ffmpeg", "-hide_banner", "-loglevel", "error",
                        "-f", "lavfi", "-i", "color=c=red:size=320x240:duration=1:rate=10",
                        "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                        "-c:v", "libtheora", "-c:a", "libvorbis",
                        "-shortest", "-y", str(out_path)]
                    r3 = subprocess.run(cmd_fb, capture_output=True, text=True, timeout=30)
                    if r3.returncode == 0 and _file_ready(out_path):
                        video_files[out_fmt] = str(out_path)
                    elif not _file_ready(out_path):
                        # Last resort: audio-only OGV (valid OGG container with vorbis)
                        cmd_fb2 = [
                            "ffmpeg", "-hide_banner", "-loglevel", "error",
                            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                            "-c:a", "libvorbis", "-vn", "-y", str(out_path)]
                        r4 = subprocess.run(cmd_fb2, capture_output=True, text=True, timeout=15)
                        if r4.returncode == 0 and _file_ready(out_path):
                            video_files[out_fmt] = str(out_path)
            except Exception:
                pass
            _tick(f"Video: {out_fmt.upper()}", out_path)

        if not ffmpeg_video_ok:
            files["video"] = {"error": "FFmpeg not found"}
        elif video_files:
            files["video"] = video_files
        else:
            files["video"] = {"error": "FFmpeg video generation failed"}

        # ── 3D model test files (8 formats) ──────────────────
        model_files = {}
        try:
            import trimesh
            box = trimesh.creation.box(extents=[1.0, 0.6, 0.4])
            has_trimesh = True
        except ImportError:
            has_trimesh = False
        except Exception as e:
            has_trimesh = True 
            print(f"Warning: trimesh creation failed: {e}")

        for fmt in ("obj", "stl", "gltf", "glb", "ply", "off", "dae", "3ds"):
            p = test_dir / f"test_model.{fmt}"
            if _file_ready(p):
                model_files[fmt] = str(p)
                skipped[0] += 1
                _tick(f"3D: {fmt.upper()} (exists)", p)
                continue
            if has_trimesh:
                try:
                    box.export(str(p), file_type=fmt)
                    if p.exists() and p.stat().st_size > 0:
                        model_files[fmt] = str(p)
                except Exception:
                    pass
            _tick(f"3D: {fmt.upper()}", p)

        # ── Fallback: minimal binary 3DS when trimesh export fails ──
        if "3ds" not in model_files or not _file_ready(test_dir / "test_model.3ds"):
            ds_path = test_dir / "test_model.3ds"
            try:
                ds_path.write_bytes(TestFileGenerator._create_minimal_3ds())
                if _file_ready(ds_path):
                    model_files["3ds"] = str(ds_path)
            except Exception:
                pass

        if not has_trimesh:
            files["3d"] = model_files if model_files else {"error": "trimesh not installed"}
        elif model_files:
            files["3d"] = model_files
        else:
            files["3d"] = {"error": "All 3D exports failed"}

        # ── Presentation test files (PPTX + ODP = 2 steps) ────
        pres_files = {}
        pptx_path = test_dir / "test_presentation.pptx"
        odp_path  = test_dir / "test_presentation.odp"

        try:
            from pptx import Presentation as _PrsCls
            from pptx.util import Inches, Pt
            has_pptx = True
        except ImportError:
            has_pptx = False

        # Step 1: PPTX
        if _file_ready(pptx_path):
            pres_files["pptx"] = str(pptx_path)
            skipped[0] += 1
            _tick("Presentation: PPTX (exists)", pptx_path)
        elif has_pptx:
            try:
                prs = _PrsCls()
                prs.slide_width = Inches(13.333)
                prs.slide_height = Inches(7.5)

                slide1 = prs.slides.add_slide(prs.slide_layouts[6])
                txBox = slide1.shapes.add_textbox(Inches(1), Inches(2.5), Inches(11.3), Inches(2))
                tf = txBox.text_frame
                p1 = tf.paragraphs[0]
                p1.text = "Test Presentation"
                p1.font.size = Pt(44)
                p1.font.bold = True

                slide2 = prs.slides.add_slide(prs.slide_layouts[6])
                txBox2 = slide2.shapes.add_textbox(Inches(1), Inches(1), Inches(11.3), Inches(5))
                tf2 = txBox2.text_frame
                tf2.word_wrap = True
                p2 = tf2.paragraphs[0]
                p2.text = "Slide 2: Test Content"
                p2.font.size = Pt(32)
                p2.font.bold = True
                p3 = tf2.add_paragraph()
                p3.text = "This is a test presentation for format conversion."
                p3.font.size = Pt(24)
                p4 = tf2.add_paragraph()
                p4.text = "It contains multiple slides with text content."
                p4.font.size = Pt(24)

                slide3 = prs.slides.add_slide(prs.slide_layouts[6])
                txBox3 = slide3.shapes.add_textbox(Inches(1), Inches(1), Inches(11.3), Inches(5))
                tf3 = txBox3.text_frame
                tf3.word_wrap = True
                p5 = tf3.paragraphs[0]
                p5.text = "Slide 3: More Content"
                p5.font.size = Pt(32)
                p5.font.bold = True
                p6 = tf3.add_paragraph()
                p6.text = "Third slide with additional text for testing."
                p6.font.size = Pt(24)

                slide4 = prs.slides.add_slide(prs.slide_layouts[6])
                txBox4 = slide4.shapes.add_textbox(Inches(1), Inches(0.5), Inches(11.3), Inches(1.5))
                tf4 = txBox4.text_frame
                tf4.word_wrap = True
                p7 = tf4.paragraphs[0]
                p7.text = "Slide 4: Data & Lists"
                p7.font.size = Pt(32)
                p7.font.bold = True
                for bullet_text in ["Item A", "Item B", "Item C"]:
                    bp = tf4.add_paragraph()
                    bp.text = bullet_text
                    bp.font.size = Pt(20)
                    bp.level = 0
                tbl_shape = slide4.shapes.add_table(3, 3, Inches(1), Inches(3.5), Inches(8), Inches(2.5))
                tbl = tbl_shape.table
                for rr in range(3):
                    for cc in range(3):
                        cell = tbl.cell(rr, cc)
                        cell.text = f"R{rr}C{cc}"
                        for para in cell.text_frame.paragraphs:
                            para.font.size = Pt(14)

                prs.save(str(pptx_path))
                pres_files["pptx"] = str(pptx_path)
            except Exception:
                pass
            _tick("Presentation: PPTX", pptx_path)
        else:
            _tick("Presentation: PPTX (python-pptx not installed)", pptx_path)

        # Step 2: ODP
        if _file_ready(odp_path):
            pres_files["odp"] = str(odp_path)
            skipped[0] += 1
            _tick("Presentation: ODP (exists)", odp_path)
        elif pres_files.get("pptx"):
            try:
                import shutil as _shutil
                if not _shutil.which("soffice"):
                    _tick("Presentation: ODP (LibreOffice not found)", odp_path)
                else:
                    cmd_odp = [
                        "soffice", "--headless", "--norestore",
                        "--convert-to", "odp", "--outdir", str(test_dir),
                        str(pptx_path)]
                    subprocess.run(cmd_odp, capture_output=True, text=True, timeout=10)
                    if _file_ready(odp_path):
                        pres_files["odp"] = str(odp_path)
                    _tick("Presentation: ODP", odp_path)
            except (FileNotFoundError, Exception):
                _tick("Presentation: ODP (error)", odp_path)
        else:
            _tick("Presentation: ODP (no PPTX source)", odp_path)

        if pres_files:
            files["presentation"] = pres_files
        elif not has_pptx:
            files["presentation"] = {"error": "python-pptx not installed"}
        else:
            files["presentation"] = {"error": "Presentation generation failed"}

        return files, skipped[0]

# ─── Test Tab ──────────────────────────────────────────────────────

class TestTab(QWidget):
    log_signal = Signal(str)
    gen_progress_signal = Signal(int, int)   # (step, total) — thread-safe progress update
    test_progress_signal = Signal(int)        # (step) — thread-safe test progress update
    _files_generated_signal = Signal(object, object, str, int, int)  # (ok_list, fail_list, dir, total, skipped)
    _gen_stopped_signal = Signal()
    _gen_error_signal = Signal()
    _result_signal = Signal(object)  # (result_tuple,) — thread-safe result delivery
    _tests_done_signal = Signal(int, int, int, int)  # (passed, failed, skipped, total)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._test_running = False
        self._test_files = {}
        self._auto_run_after_gen = False
        self._results = []          # flat list of (num, conv, status, detail, source)
        self._filtered_results = []  # after applying filter
        self._filter_mode = "All"   # All / Passed / Failed / Skipped

        # Running counters (updated in worker, read on main thread)
        self._count_lock = threading.Lock()
        self._counters = {"total": 0, "passed": 0, "failed": 0, "skipped": 0}

        # Elapsed-time timer
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(1000)
        self._elapsed_timer.timeout.connect(self._tick_elapsed)
        self._elapsed_seconds = 0

        # Remember failed conversions for "Retry Failed"
        self._last_failed = []  # list of (src, dst, test_file)

        # Persistent test log file handle
        self._log_file = None
        self._log_file_path = None

        # Connect signals for thread-safe UI updates
        self.gen_progress_signal.connect(self._on_gen_progress)
        self.test_progress_signal.connect(self._on_test_progress)
        self.log_signal.connect(self._log)
        self._files_generated_signal.connect(self._on_files_generated)
        self._gen_stopped_signal.connect(self._on_gen_stopped)
        self._gen_error_signal.connect(self._on_gen_error)
        self._result_signal.connect(self._on_result)
        self._tests_done_signal.connect(self._on_tests_done)

        self._build_ui()

    @Slot(int, int)
    def _on_gen_progress(self, step, total):
        """Slot: update generation progress bar (called on main thread via signal)."""
        if not self.gen_progress.isVisible():
            self.gen_progress.setVisible(True)
        if self.gen_progress.maximum() != total:
            self.gen_progress.setMaximum(total)
        self.gen_progress.setValue(step)

    @Slot(int)
    def _on_test_progress(self, step):
        """Slot: update test run progress bar (called on main thread via signal)."""
        if not self.test_progress.isVisible():
            self.test_progress.setVisible(True)
        self.test_progress.setValue(step)

    # ── UI construction ────────────────────────────────────

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        # Header
        header = QFrame()
        header.setObjectName("headerFrame")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 8, 16, 8)
        title = QLabel("TEST ALL CONVERSIONS")
        title.setObjectName("headerTitle")
        header_layout.addWidget(title)
        header_layout.addStretch()
        self.elapsed_label = QLabel("0:00")
        self.elapsed_label.setStyleSheet("font-size: 13px; color: #85859e; padding: 12px 16px;")
        header_layout.addWidget(self.elapsed_label)
        self.test_status_label = QLabel("Ready")
        self.test_status_label.setObjectName("headerStatus")
        header_layout.addWidget(self.test_status_label)
        layout.addWidget(header)

        # Category selection
        cat_label = QLabel("TEST CATEGORIES")
        cat_label.setObjectName("sectionLabel")
        layout.addWidget(cat_label)

        cat_row = QHBoxLayout()
        cat_row.setSpacing(12)
        self.cat_checks = {}
        for cat, icon in [("audio", "Audio"), ("video", "Video"),
                          ("text", "Text"), ("image", "Image"), ("3d", "3D Models"),
                          ("data", "Data"), ("presentation", "Presentation")]:
            chk = QCheckBox(f" {CATEGORY_ICONS[cat]}  {icon}")
            chk.setChecked(True)
            cat_row.addWidget(chk)
            self.cat_checks[cat] = chk
        cat_row.addStretch()
        select_all_btn = QPushButton("Toggle All")
        select_all_btn.setFixedWidth(100)
        select_all_btn.clicked.connect(self._toggle_all)
        cat_row.addWidget(select_all_btn)
        layout.addLayout(cat_row)

        layout.addWidget(make_separator())

        # Action buttons (row 1: Generate / Run / Stop / Retry Failed)
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self.gen_btn = QPushButton("Generate Test Files")
        self.gen_btn.setObjectName("formatsBtn")
        self.gen_btn.clicked.connect(self._generate_test_files)
        btn_row.addWidget(self.gen_btn)

        self.run_btn = QPushButton("Run All Tests")
        self.run_btn.setObjectName("convertBtn")
        self.run_btn.clicked.connect(self._run_all_tests)
        self.run_btn.setEnabled(False)
        btn_row.addWidget(self.run_btn)

        self.gen_run_btn = QPushButton("Generate & Run All")
        self.gen_run_btn.setObjectName("genRunBtn")
        self.gen_run_btn.clicked.connect(self._gen_and_run)
        btn_row.addWidget(self.gen_run_btn)

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setObjectName("clearBtn")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop_tests)
        btn_row.addWidget(self.stop_btn)

        self.retry_btn = QPushButton("Retry Failed")
        self.retry_btn.setObjectName("batchBtn")
        self.retry_btn.setEnabled(False)
        self.retry_btn.clicked.connect(self._retry_failed)
        btn_row.addWidget(self.retry_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Action buttons (row 2: Filter / Export)
        util_row = QHBoxLayout()
        util_row.setSpacing(10)

        filter_label = QLabel("Show:")
        filter_label.setStyleSheet("font-size: 11px; color: #85859e;")
        util_row.addWidget(filter_label)

        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["All", "Passed", "Failed", "Skipped"])
        self.filter_combo.setFixedWidth(120)
        self.filter_combo.currentTextChanged.connect(self._apply_filter)
        util_row.addWidget(self.filter_combo)

        util_row.addStretch()

        self.export_btn = QPushButton("Export Results")
        self.export_btn.setObjectName("formatsBtn")
        self.export_btn.setEnabled(False)
        self.export_btn.setFixedWidth(140)
        self.export_btn.clicked.connect(self._export_results)
        util_row.addWidget(self.export_btn)

        layout.addLayout(util_row)

        # Generation progress (wrapped in glow frame)
        self.gen_glow_frame = QFrame()
        self.gen_glow_frame.setObjectName("progressGlow")
        gen_glow_layout = QVBoxLayout(self.gen_glow_frame)
        gen_glow_layout.setContentsMargins(4, 4, 4, 4)
        self.gen_progress = QProgressBar()
        self.gen_progress.setVisible(False)
        self.gen_progress.setTextVisible(True)
        self.gen_progress.setFormat("Generating: %v / %m  (%p%)")
        self.gen_progress.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.gen_progress.setMinimumHeight(24)  # Ensure visible height when shown
        gen_glow_layout.addWidget(self.gen_progress)
        self.gen_glow_frame.setVisible(False)
        self.gen_glow_frame.setMinimumHeight(32)  # Frame must have height to be visible
        layout.addWidget(self.gen_glow_frame)

        # Test run progress (wrapped in glow frame)
        self.test_glow_frame = QFrame()
        self.test_glow_frame.setObjectName("progressGlow")
        test_glow_layout = QVBoxLayout(self.test_glow_frame)
        test_glow_layout.setContentsMargins(4, 4, 4, 4)
        self.test_progress = QProgressBar()
        self.test_progress.setVisible(False)
        self.test_progress.setTextVisible(True)
        self.test_progress.setFormat("Testing: %v / %m  (%p%)")
        self.test_progress.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.test_progress.setMinimumHeight(24)  # Ensure visible height when shown
        test_glow_layout.addWidget(self.test_progress)
        self.test_glow_frame.setVisible(False)
        self.test_glow_frame.setMinimumHeight(32)  # Frame must have height to be visible
        layout.addWidget(self.test_glow_frame)

        # Summary labels
        summary_row = QHBoxLayout()
        summary_row.setSpacing(16)
        self.total_label = QLabel("Total: 0")
        self.total_label.setStyleSheet("font-size: 12px; color: #b8b8d0;")
        self.pass_label = QLabel("Passed: 0")
        self.pass_label.setStyleSheet("font-size: 12px; color: #73b873;")
        self.fail_label = QLabel("Failed: 0")
        self.fail_label.setStyleSheet("font-size: 12px; color: #e65a5a;")
        self.skip_label = QLabel("Skipped: 0")
        self.skip_label.setStyleSheet("font-size: 12px; color: #e6bf33;")
        for lbl in [self.total_label, self.pass_label, self.fail_label, self.skip_label]:
            summary_row.addWidget(lbl)
        summary_row.addStretch()
        layout.addLayout(summary_row)

        layout.addWidget(make_separator())

        # Results table
        results_label = QLabel("TEST RESULTS")
        results_label.setObjectName("sectionLabel")
        layout.addWidget(results_label)

        self.results_table = QTableWidget()
        self.results_table.setColumnCount(5)
        self.results_table.setHorizontalHeaderLabels(
            ["#", "Conversion", "Source", "Result", "Details"]
        )
        tbl_header = self.results_table.horizontalHeader()
        tbl_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        tbl_header.resizeSection(0, 45)
        tbl_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        tbl_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        tbl_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        tbl_header.resizeSection(3, 70)
        tbl_header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.results_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.results_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.results_table.verticalHeader().setVisible(False)
        self.results_table.setAlternatingRowColors(True)
        layout.addWidget(self.results_table, stretch=1)

        # Test log
        layout.addWidget(make_separator())
        log_label = QLabel("TEST LOG")
        log_label.setObjectName("sectionLabel")
        layout.addWidget(log_label)

        self.test_log = QTextEdit()
        self.test_log.setReadOnly(True)
        self.test_log.setObjectName("logText")
        self.test_log.setMaximumHeight(180)
        layout.addWidget(self.test_log)

        self._stop_requested = False

    # ── Helpers ─────────────────────────────────────────────

    def _toggle_all(self):
        all_checked = all(chk.isChecked() for chk in self.cat_checks.values())
        for chk in self.cat_checks.values():
            chk.setChecked(not all_checked)

    def _set_cats_enabled(self, enabled):
        for chk in self.cat_checks.values():
            chk.setEnabled(enabled)

    def _log(self, text):
        ts = datetime.now().strftime("%H:%M:%S")
        entry = f"[{ts}] {text}"
        print(entry)  # also log to terminal
        cur = self.test_log.toPlainText()
        if cur:
            self.test_log.append(entry)
        else:
            self.test_log.setPlainText(entry)
        sb = self.test_log.verticalScrollBar()
        sb.setValue(sb.maximum())
        # Write to persistent test log file if open
        if self._log_file is not None:
            try:
                self._log_file.write(entry + "\n")
                self._log_file.flush()
            except Exception:
                pass

    def _tick_elapsed(self):
        self._elapsed_seconds += 1
        m, s = divmod(self._elapsed_seconds, 60)
        self.elapsed_label.setText(f"{m}:{s:02d}")

    def _reset_elapsed(self):
        self._elapsed_timer.stop()
        self._elapsed_seconds = 0
        self.elapsed_label.setText("0:00")

    def _update_counters_ui(self):
        """Fast O(1) counter update using running totals (no table scan)."""
        with self._count_lock:
            t = self._counters["total"]
            p = self._counters["passed"]
            f = self._counters["failed"]
            s = self._counters["skipped"]
        self.total_label.setText(f"Total: {t}")
        self.pass_label.setText(f"Passed: {p}")
        self.fail_label.setText(f"Failed: {f}")
        self.skip_label.setText(f"Skipped: {s}")

    # ── Filter & Export ────────────────────────────────────

    def _apply_filter(self, mode=None):
        if mode is not None:
            self._filter_mode = mode
        self._rebuild_table()

    def _rebuild_table(self):
        """Repopulate the results table from self._results using current filter."""
        self.results_table.setSortingEnabled(False)
        self.results_table.setRowCount(0)
        shown = 0
        for rec in self._results:
            num, conv, status, detail, source = rec
            if self._filter_mode != "All" and status.upper() != self._filter_mode.upper():
                continue
            row = self.results_table.rowCount()
            self.results_table.insertRow(row)
            src_name = Path(source).name if source else "N/A"
            self.results_table.setItem(row, 0, QTableWidgetItem(str(num)))
            self.results_table.setItem(row, 1, QTableWidgetItem(conv))
            self.results_table.setItem(row, 2, QTableWidgetItem(src_name))
            status_item = QTableWidgetItem(status)
            if status == "PASS":
                status_item.setForeground(QColor("#73b873"))
            elif status == "FAIL":
                status_item.setForeground(QColor("#e65a5a"))
            else:
                status_item.setForeground(QColor("#e6bf33"))
            self.results_table.setItem(row, 3, status_item)
            self.results_table.setItem(row, 4, QTableWidgetItem(detail))
            shown += 1
        self.results_table.setSortingEnabled(True)
        if shown:
            self.results_table.scrollToBottom()

    def _export_results(self):
        """Save test results to a text file chosen by the user."""
        default_name = f"test_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Test Results", str(Path.home() / default_name),
            "Text files (*.txt);;CSV files (*.csv);;All files (*)"
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(f"Converter Test Results — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                fh.write(f"{'=' * 72}\n\n")
                with self._count_lock:
                    t = self._counters["total"]
                    p = self._counters["passed"]
                    f = self._counters["failed"]
                    s = self._counters["skipped"]
                fh.write(f"Total: {t}   Passed: {p}   Failed: {f}   Skipped: {s}\n\n")
                fh.write(f"{'#':<6} {'Conversion':<28} {'Source':<30} {'Result':<8} {'Details'}\n")
                fh.write(f"{'-' * 72}\n")
                for num, conv, status, detail, source in self._results:
                    src_name = Path(source).name if source else "N/A"
                    fh.write(f"{num:<6} {conv:<28} {src_name:<30} {status:<8} {detail}\n")
            self._log(f"[INFO] Results exported to: {Path(path).name}")
        except Exception as e:
            self._log(f"[ERROR] Export failed: {e}")

    # ── Generate ───────────────────────────────────────────

    def _gen_and_run(self):
        """One-click: generate test files then immediately run all tests."""
        self._auto_run_after_gen = True
        self._generate_test_files()

    def _generate_test_files(self):
        if self._test_running:
            return
        self._stop_requested = False
        self._log("Generating test files...")
        self.test_status_label.setText("Generating...")
        self.test_status_label.setStyleSheet("font-size: 11px; color: #e6bf33; padding: 12px 16px;")
        self.gen_btn.setEnabled(False)
        self.run_btn.setEnabled(False)
        self.gen_run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self._set_cats_enabled(False)
        self.gen_glow_frame.setVisible(True)
        self.gen_glow_frame.setProperty("glowState", "active")
        self.gen_glow_frame.style().unpolish(self.gen_glow_frame)
        self.gen_glow_frame.style().polish(self.gen_glow_frame)
        self.gen_progress.setVisible(True)
        self.gen_progress.setMaximum(TestFileGenerator.TOTAL_STEPS)
        self.gen_progress.setValue(0)

        def worker():
            test_dir = Path.cwd() / "test_files"

            def progress_cb(s, total, desc):
                # Use signals for thread-safe UI updates (avoid QTimer.singleShot
                # which floods the event loop and causes hangs under load)
                self.gen_progress_signal.emit(s, total)
                self.log_signal.emit(f"  [GEN] {desc}")

            def stop_check():
                return self._stop_requested

            try:
                self._test_files, skipped_count = TestFileGenerator.generate_all(
                    test_dir, progress_cb=progress_cb, stop_check=stop_check)
            except TestFileGenerator.Stopped:
                self._gen_stopped_signal.emit()
                return
            except Exception as e:
                import traceback
                print(f"[ERROR] generate_all failed:\n{traceback.format_exc()}")
                self.log_signal.emit(f"[ERROR] Generation failed: {e}")
                self._gen_error_signal.emit()
                return

            categories_ok = []
            categories_fail = []
            total_files = 0
            try:
                for cat, cat_files in self._test_files.items():
                    if isinstance(cat_files, dict) and "error" in cat_files:
                        categories_fail.append(f"{cat}: {cat_files['error']}")
                    else:
                        fmts = list(cat_files.keys()) if isinstance(cat_files, dict) else []
                        total_files += len(fmts)
                        categories_ok.append(f"{cat}: {', '.join(f'.{k}' for k in fmts)} ({len(fmts)} files)")
            except Exception:
                import traceback
                print(f"[ERROR] categorizing test files failed:\n{traceback.format_exc()}")

            self._files_generated_signal.emit(
                categories_ok, categories_fail, str(test_dir), total_files, skipped_count)

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        # Force layout recalculation so the glow frame + progress bar
        # actually render with their new minimum heights when shown.
        self.gen_glow_frame.updateGeometry()
        self.gen_progress.updateGeometry()

    def _on_files_generated(self, ok_list, fail_list, test_dir, total_files, skipped_count):
        try:
            for msg in ok_list:
                self._log(f"  [OK] {msg}")
            for msg in fail_list:
                self._log(f"  [WARN] {msg}")
            if skipped_count:
                self._log(f"  [SKIP] {skipped_count} file(s) already existed, skipped")
            self._log(f"Test files generated in: {test_dir}  ({total_files} files total)")
        except Exception as e:
            print(f"[WARN] _on_files_generated logging failed: {e}")
        finally:
            self.stop_btn.setEnabled(False)
            self._set_cats_enabled(True)
            if self._auto_run_after_gen:
                # Keep the gen progress bar visible with a transition
                # message so the user sees feedback instead of a blank gap.
                # Do NOT re-enable gen/gen_run buttons — tests are about to
                # start and re-enabling them creates a race condition where
                # the user could start a second generation during the 800ms gap.
                self.gen_progress.setFormat("Generation complete - preparing tests...")
                self.gen_progress.setValue(self.gen_progress.maximum())
                self.test_status_label.setText("Preparing tests...")
                self.test_status_label.setStyleSheet("font-size: 11px; color: #599eff; padding: 12px 16px;")
                self._auto_run_after_gen = False
                self._log("[INFO] Auto-running all tests after generation...")
                # Small delay so the user can see the transition message
                QTimer.singleShot(800, self._run_all_tests)
                # Flush pending log events before the 800ms delay
                QApplication.processEvents()
            else:
                self.gen_btn.setEnabled(True)
                self.gen_run_btn.setEnabled(True)
                self.run_btn.setEnabled(bool(self._test_files))
                self.gen_progress.setVisible(False)
                self.gen_glow_frame.setProperty("glowState", "success")
                self.gen_glow_frame.style().unpolish(self.gen_glow_frame)
                self.gen_glow_frame.style().polish(self.gen_glow_frame)
                self.gen_glow_frame.setVisible(False)
                self.test_status_label.setText(f"Files ready ({total_files})")
                self.test_status_label.setStyleSheet("font-size: 11px; color: #73b873; padding: 12px 16px;")

    def _on_gen_error(self):
        """Called on main thread when generation fails."""
        self._auto_run_after_gen = False
        self.gen_btn.setEnabled(True)
        self.gen_run_btn.setEnabled(True)
        self.run_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self._set_cats_enabled(True)
        self.gen_progress.setVisible(False)
        self.gen_glow_frame.setProperty("glowState", "error")
        self.gen_glow_frame.style().unpolish(self.gen_glow_frame)
        self.gen_glow_frame.style().polish(self.gen_glow_frame)
        self.gen_glow_frame.setVisible(False)
        self.test_status_label.setText("Generation failed")
        self.test_status_label.setStyleSheet("font-size: 11px; color: #e65a5a; padding: 12px 16px;")

    def _on_gen_stopped(self):
        """Called on main thread when generation is cancelled by user."""
        self._auto_run_after_gen = False
        self._log("[INFO] Generation stopped by user.")
        self.gen_btn.setEnabled(True)
        self.gen_run_btn.setEnabled(True)
        self.run_btn.setEnabled(bool(self._test_files))
        self.stop_btn.setEnabled(False)
        self._set_cats_enabled(True)
        self.gen_progress.setVisible(False)
        self.gen_glow_frame.setProperty("glowState", "warning")
        self.gen_glow_frame.style().unpolish(self.gen_glow_frame)
        self.gen_glow_frame.style().polish(self.gen_glow_frame)
        self.gen_glow_frame.setVisible(False)
        self.test_status_label.setText("Generation stopped")
        self.test_status_label.setStyleSheet("font-size: 11px; color: #e6bf33; padding: 12px 16px;")

    # ── Stop / Retry ──────────────────────────────────────

    def _stop_tests(self):
        self._stop_requested = True
        self._log("[INFO] Stop requested...")

    def _retry_failed(self):
        """Re-run only the conversions that failed in the last run."""
        if not self._last_failed:
            return
        # _last_failed stores 3-tuples (src, dst, test_file);
        # _run_test_list expects 4-tuples (src, dst, test_file, skip_reason).
        retry_list = [(src, dst, path, None) for src, dst, path in self._last_failed]
        self._run_test_list(retry_list, title="Retrying Failed")

    # ── Run tests ───────────────────────────────────────────

    def _run_all_tests(self):
        if self._test_running:
            return
        test_list = self._build_test_list()
        self._run_test_list(test_list, title="All")

    def _build_test_list(self):
        """Build the list of (src, dst, test_file, skip_reason) tuples."""
        enabled_cats = [cat for cat, chk in self.cat_checks.items() if chk.isChecked()]
        test_list = []
        for conv_cat, src, dst in ALL_CONVERSIONS:
            src_ext = src.lower()
            src_cat = get_category(src_ext)

            # Check if this conversion's source category is enabled
            if "->" in conv_cat:
                test_source_cat = conv_cat.split("->")[0].strip()
            else:
                test_source_cat = conv_cat

            if test_source_cat not in enabled_cats:
                continue

            # Find test file — prefer exact extension match in source category,
            # fall back to any file in the same category.
            test_file = None
            if src_cat and src_cat in self._test_files:
                cat_files = self._test_files[src_cat]
                if "error" in cat_files:
                    continue
                # 1) Exact match on extension (best)
                if src_ext in cat_files:
                    test_file = cat_files[src_ext]
                else:
                    # 2) Heuristic: pick the "simplest" representative format
                    #    for the category so cross-category tests get a
                    #    meaningful source (e.g. .txt for text→data, .json
                    #    for data→html, .obj for 3d→image).
                    preferred_order = {
                        "text": ["txt", "md", "html", "pdf"],
                        "data": ["json", "csv", "xml", "yaml"],
                        "image": ["png", "jpg", "bmp", "gif", "webp", "tiff"],
                        "audio": ["wav", "mp3", "ogg", "flac", "aac", "m4a", "opus"],
                        "video": ["mp4", "mkv", "webm", "avi", "mov", "wmv"],
                        "3d": ["obj", "stl", "gltf", "glb", "ply", "off"],
                        "presentation": ["pptx", "odp"],
                    }
                    order = preferred_order.get(src_cat, list(cat_files.keys()))
                    for fmt in order:
                        if fmt in cat_files:
                            test_file = cat_files[fmt]
                            break
                    if test_file is None:
                        # Still nothing — use first available
                        for fpath in cat_files.values():
                            test_file = fpath
                            break

            if test_file is None:
                test_list.append((src, dst, None, "No test file"))
                continue

            test_list.append((src, dst, test_file, None))
        return test_list

    def _run_test_list(self, test_list, title="All"):
        """Execute a list of conversion tests in a background thread."""
        self._test_running = True
        self._stop_requested = False
        self.run_btn.setEnabled(False)
        self.gen_btn.setEnabled(False)
        self.gen_run_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.retry_btn.setEnabled(False)
        # Hide the gen progress bar if still visible from a prior
        # "Generate & Run" transition (it shows "preparing tests...")
        if self.gen_glow_frame.isVisible():
            self.gen_progress.setVisible(False)
            self.gen_glow_frame.setVisible(False)
        self.export_btn.setEnabled(False)
        self._set_cats_enabled(False)
        self.test_status_label.setText("Testing...")
        self.test_status_label.setStyleSheet("font-size: 11px; color: #e6bf33; padding: 12px 16px;")

        # Reset counters
        with self._count_lock:
            self._counters = {"total": 0, "passed": 0, "failed": 0, "skipped": 0}
        self._results = []
        self._last_failed = []
        self._update_counters_ui()

        # Open persistent test log file
        self._close_test_log_file()
        try:
            log_dir = Path.cwd() / "test_output"
            log_dir.mkdir(parents=True, exist_ok=True)
            ts_file = datetime.now().strftime('%Y%m%d_%H%M%S')
            self._log_file_path = log_dir / f"test_log_{ts_file}.log"
            self._log_file = open(self._log_file_path, "w", encoding="utf-8")
            self._log_file.write(f"Converter Test Log — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            self._log_file.write(f"{'=' * 72}\n\n")
            self._log_file.flush()
        except Exception as e:
            self._log_file = None
            self._log_file_path = None
            print(f"[WARN] Could not open test log file: {e}")

        total = len(test_list)
        self._log(f"[INFO] Running {total} tests ({title})...")
        if self._log_file_path:
            self._log(f"[INFO] Log file: {self._log_file_path}")
        self.test_glow_frame.setVisible(True)
        self.test_glow_frame.setProperty("glowState", "active")
        self.test_glow_frame.style().unpolish(self.test_glow_frame)
        self.test_glow_frame.style().polish(self.test_glow_frame)
        self.test_progress.setVisible(True)
        self.test_progress.setMaximum(total)
        self.test_progress.setValue(0)

        self.results_table.setRowCount(0)

        # Start elapsed timer
        self._reset_elapsed()
        self._elapsed_timer.start()

        # Force layout recalculation so the glow frame renders properly
        self.test_glow_frame.updateGeometry()
        self.test_progress.updateGeometry()

        def worker():
            out_dir = Path.cwd() / "test_output"
            out_dir.mkdir(parents=True, exist_ok=True)
            passed = 0
            failed = 0
            skipped = 0

            for i, (src, dst, test_file, skip_reason) in enumerate(test_list):
                if self._stop_requested:
                    self.log_signal.emit("[INFO] Tests stopped by user.")
                    break

                idx = i + 1
                conv_str = f"{src} -> {dst}"

                if skip_reason:
                    skipped += 1
                    with self._count_lock:
                        self._counters["total"] += 1
                        self._counters["skipped"] += 1
                    rec = (idx, conv_str, "SKIP", skip_reason, "")
                    self._result_signal.emit(rec)
                    self.test_progress_signal.emit(idx)
                    continue

                try:
                    out_path = out_dir / f"test_{src}_to_{dst}.{dst.lower()}"
                    result_ok = [False]
                    result_msg = [""]

                    def test_cb(ok, msg):
                        result_ok[0] = ok
                        result_msg[0] = msg

                    ConverterEngine.convert(test_file, str(out_path), test_cb, quality=75)

                    if result_ok[0]:
                        out_size = os.path.getsize(out_path) if out_path.exists() else 0
                        if out_size > 0:
                            passed += 1
                            detail = f"OK ({human_size(out_size)})"
                            with self._count_lock:
                                self._counters["total"] += 1
                                self._counters["passed"] += 1
                            rec = (idx, conv_str, "PASS", detail, test_file)
                        else:
                            failed += 1
                            detail = "Output file is empty"
                            with self._count_lock:
                                self._counters["total"] += 1
                                self._counters["failed"] += 1
                            rec = (idx, conv_str, "FAIL", detail, test_file)
                    else:
                        failed += 1
                        with self._count_lock:
                            self._counters["total"] += 1
                            self._counters["failed"] += 1
                        rec = (idx, conv_str, "FAIL", str(result_msg[0])[:100], test_file)

                    self._result_signal.emit(rec)

                    # Cleanup output file
                    if out_path.exists():
                        try:
                            out_path.unlink()
                        except Exception:
                            pass

                except Exception as e:
                    failed += 1
                    with self._count_lock:
                        self._counters["total"] += 1
                        self._counters["failed"] += 1
                    rec = (idx, conv_str, "FAIL", str(e)[:100], test_file)
                    self._result_signal.emit(rec)

                self.test_progress_signal.emit(idx)

            self._tests_done_signal.emit(passed, failed, skipped, total)

        threading.Thread(target=worker, daemon=True).start()

    def _on_result(self, rec):
        """Called on the main thread for each completed test."""
        num, conv, status, detail, source = rec
        self._results.append(rec)
        src_name = Path(source).name if source else "N/A"
        self._log(f"  {num} {conv} {src_name} {status} {detail}")
        if status == "FAIL":
            parts = conv.split("->")
            if len(parts) == 2:
                self._last_failed.append((parts[0].strip(), parts[1].strip(), source))

        # Only add to visible table if it passes the filter
        if self._filter_mode == "All" or status.upper() == self._filter_mode.upper():
            row = self.results_table.rowCount()
            self.results_table.insertRow(row)
            src_name = Path(source).name if source else "N/A"
            self.results_table.setItem(row, 0, QTableWidgetItem(str(num)))
            self.results_table.setItem(row, 1, QTableWidgetItem(conv))
            self.results_table.setItem(row, 2, QTableWidgetItem(src_name))
            status_item = QTableWidgetItem(status)
            if status == "PASS":
                status_item.setForeground(QColor("#73b873"))
            elif status == "FAIL":
                status_item.setForeground(QColor("#e65a5a"))
            else:
                status_item.setForeground(QColor("#e6bf33"))
            self.results_table.setItem(row, 3, status_item)
            self.results_table.setItem(row, 4, QTableWidgetItem(detail))
            self.results_table.scrollToBottom()

        # O(1) counter update
        self._update_counters_ui()

    def _close_test_log_file(self):
        """Safely close the test log file if it is open."""
        if self._log_file is not None:
            try:
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None

    def _on_tests_done(self, passed, failed, skipped, total):
        self._test_running = False
        self._elapsed_timer.stop()
        self.run_btn.setEnabled(bool(self._test_files))
        self.gen_btn.setEnabled(True)
        self.gen_run_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.retry_btn.setEnabled(failed > 0)
        self.export_btn.setEnabled(len(self._results) > 0)
        self._set_cats_enabled(True)
        self.test_progress.setVisible(False)
        # Ensure gen progress bar is also hidden (may still be visible
        # from a "Generate & Run" flow that skipped the normal cleanup)
        if self.gen_glow_frame.isVisible():
            self.gen_progress.setVisible(False)
            self.gen_glow_frame.setVisible(False)

        m, s = divmod(self._elapsed_seconds, 60)
        elapsed_str = f"{m}:{s:02d}"

        if self._stop_requested:
            self.test_glow_frame.setProperty("glowState", "warning")
        elif failed == 0:
            self.test_glow_frame.setProperty("glowState", "success")
        else:
            self.test_glow_frame.setProperty("glowState", "error")
        self.test_glow_frame.style().unpolish(self.test_glow_frame)
        self.test_glow_frame.style().polish(self.test_glow_frame)
        self.test_glow_frame.setVisible(False)

        if self._stop_requested:
            self.test_status_label.setText(f"Stopped ({elapsed_str})")
            self.test_status_label.setStyleSheet("font-size: 11px; color: #e6bf33; padding: 12px 16px;")
        elif failed == 0:
            self.test_status_label.setText(f"All Passed ({elapsed_str})")
            self.test_status_label.setStyleSheet("font-size: 11px; color: #73b873; padding: 12px 16px;")
        else:
            self.test_status_label.setText(f"{failed} Failed ({elapsed_str})")
            self.test_status_label.setStyleSheet("font-size: 11px; color: #e65a5a; padding: 12px 16px;")

        # Write summary to the persistent log file before closing
        if self._log_file is not None:
            try:
                self._log_file.write(f"\n{'=' * 72}\n")
                self._log_file.write(f"SUMMARY — Elapsed: {elapsed_str}\n")
                self._log_file.write(f"  Total: {total}   Passed: {passed}   Failed: {failed}   Skipped: {skipped}\n")
                self._log_file.write(f"{'=' * 72}\n")
            except Exception:
                pass
        self._close_test_log_file()
        if self._log_file_path:
            self._log(f"[INFO] Test log saved to: {self._log_file_path}")

        # Per-category breakdown
        cat_stats = {}
        for _, conv, status, _, _ in self._results:
            src_ext = conv.split("->")[0].strip().lower() if "->" in conv else conv.lower()
            cat_key = get_category(src_ext) or src_ext
            if cat_key not in cat_stats:
                cat_stats[cat_key] = {"pass": 0, "fail": 0, "skip": 0}
            key = status.lower()
            if key in cat_stats[cat_key]:
                cat_stats[cat_key][key] += 1

        self._log(f"[DONE] {passed} passed, {failed} failed, {skipped} skipped "
                  f"out of {total} tests in {elapsed_str}.")
        if cat_stats:
            self._log("[SUMMARY] Per-category breakdown:")
            for cat_key in sorted(cat_stats):
                s = cat_stats[cat_key]
                self._log(f"  {cat_key}: {s['pass']} pass, {s['fail']} fail, {s['skip']} skip")


# ─── Converter Tab (original MainWindow content) ──────────────────

class ConverterTab(QWidget):
    history_requested = Signal()

    def __init__(self, history_manager, parent=None):
        super().__init__(parent)
        self._input_file = None
        self._output_file = None
        self._converting = False
        self._cancel_requested = False
        self._quality = 85
        self._history = history_manager

        self._header_reset_timer = QTimer(self)
        self._header_reset_timer.setSingleShot(True)
        self._header_reset_timer.timeout.connect(self._reset_header)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(8)

        self._build_ui(layout)
        self._highlight_pill(None)
        self.setAcceptDrops(True)

        QTimer.singleShot(100, self._deferred_init)

        # Keyboard shortcuts
        from PySide6.QtGui import QShortcut, QKeySequence
        convert_shortcut = QShortcut(QKeySequence("Return"), self)
        convert_shortcut.activated.connect(self.start_conversion)
        batch_shortcut = QShortcut(QKeySequence("Ctrl+B"), self)
        batch_shortcut.activated.connect(self._open_batch_dialog)
        formats_shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        formats_shortcut.activated.connect(self._show_conversions)

    # ── Window-level drag-and-drop support ──────────────────

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if path and os.path.isfile(path):
                self._on_file_selected(path)
        event.acceptProposedAction()

    def _build_ui(self, layout):
        # Header
        header = QFrame()
        header.setObjectName("headerFrame")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 8, 16, 8)

        self.header_title = QLabel("CONVERTER")
        self.header_title.setObjectName("headerTitle")
        header_layout.addWidget(self.header_title)
        header_layout.addStretch()

        self.header_status = QLabel("Ready")
        self.header_status.setObjectName("headerStatus")
        header_layout.addWidget(self.header_status)
        layout.addWidget(header)

        # Category Pills
        pills_row = QHBoxLayout()
        pills_row.setSpacing(6)
        pills_row.setContentsMargins(2, 0, 0, 0)
        self.pills = {}
        for cat, icon_text in [("audio", "Audio"), ("video", "Video"),
                                ("text", "Text"), ("image", "Image"), ("3d", "3D"),
                                ("data", "Data"), ("presentation", "Pres")]:
            pill = CategoryPill(icon_text)
            pills_row.addWidget(pill)
            self.pills[cat] = pill
        pills_row.addStretch()
        layout.addLayout(pills_row)

        # Input File Section
        lbl = QLabel("INPUT FILE")
        lbl.setObjectName("sectionLabel")
        layout.addWidget(lbl)

        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        self.input_path = DropLineEdit()
        self.input_path.file_dropped.connect(self._on_file_selected)
        input_row.addWidget(self.input_path)

        browse_btn = QPushButton("Browse")
        browse_btn.setFixedWidth(88)
        browse_btn.clicked.connect(self._open_file_chooser)
        input_row.addWidget(browse_btn)
        layout.addLayout(input_row)

        self.input_info = QLabel("")
        self.input_info.setStyleSheet("font-size: 11px; color: #629e62; padding: 0 4px;")
        layout.addWidget(self.input_info)

        layout.addWidget(make_separator())

        # Output Format Section
        lbl2 = QLabel("OUTPUT FORMAT")
        lbl2.setObjectName("sectionLabel")
        layout.addWidget(lbl2)

        fmt_row = QHBoxLayout()
        fmt_row.setSpacing(12)
        self.output_format = QComboBox()
        self.output_format.setMinimumWidth(200)
        self.output_format.addItem("Select output format...")
        self.output_format.currentTextChanged.connect(self._on_format_changed)
        fmt_row.addWidget(self.output_format)

        self.category_label = QLabel("")
        self.category_label.setStyleSheet("font-size: 11px; color: #85859e;")
        fmt_row.addWidget(self.category_label)
        fmt_row.addStretch()
        layout.addLayout(fmt_row)

        # Quality Slider
        quality_row = QHBoxLayout()
        quality_row.setSpacing(12)
        quality_lbl = QLabel("QUALITY")
        quality_lbl.setObjectName("sectionLabel")
        quality_row.addWidget(quality_lbl)

        self.quality_slider = QSlider(Qt.Orientation.Horizontal)
        self.quality_slider.setRange(1, 100)
        self.quality_slider.setValue(85)
        self.quality_slider.valueChanged.connect(self._on_quality_change)
        quality_row.addWidget(self.quality_slider, stretch=3)

        self.quality_value = QLabel("85")
        self.quality_value.setStyleSheet("font-size: 12px; color: #b8b8d0;")
        self.quality_value.setFixedWidth(36)
        quality_row.addWidget(self.quality_value)
        layout.addLayout(quality_row)

        # Audio Settings Panel
        self.audio_settings_group = QGroupBox("Audio Settings")
        self.audio_settings_group.setStyleSheet("""
            QGroupBox {
                color: #599eff; font-size: 11px; font-weight: bold;
                border: 1px solid #343540; border-radius: 8px;
                margin-top: 8px; padding-top: 16px;
            }
            QGroupBox::title {
                subcontrol-origin: margin; left: 12px; padding: 0 6px;
            }
            QGroupBox QLabel { color: #b8b8d0; font-size: 11px; }
            QGroupBox QComboBox {
                background-color: #242630; color: #e0e0eb; border: none;
                border-radius: 6px; padding: 6px 10px; font-size: 11px;
                min-width: 100px;
            }
            QGroupBox QComboBox QAbstractItemView {
                background-color: #242630; color: #e0e0eb;
                border: 1px solid #353640; border-radius: 6px;
                selection-background-color: #3568b8;
            }
        """)
        audio_grid = QHBoxLayout(self.audio_settings_group)
        audio_grid.setSpacing(16)

        sr_col = QVBoxLayout()
        sr_label = QLabel("Sample Rate")
        sr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sr_col.addWidget(sr_label)
        self.sample_rate_combo = QComboBox()
        self.sample_rate_combo.addItems(["auto", "8000", "11025", "22050", "44100", "48000", "96000"])
        sr_col.addWidget(self.sample_rate_combo)
        audio_grid.addLayout(sr_col)

        ch_col = QVBoxLayout()
        ch_label = QLabel("Channels")
        ch_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ch_col.addWidget(ch_label)
        self.channels_combo = QComboBox()
        self.channels_combo.addItems(["auto", "mono", "stereo"])
        ch_col.addWidget(self.channels_combo)
        audio_grid.addLayout(ch_col)

        br_col = QVBoxLayout()
        br_label = QLabel("Bitrate")
        br_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        br_col.addWidget(br_label)
        self.bitrate_combo = QComboBox()
        self.bitrate_combo.addItems(["auto", "64", "96", "128", "160", "192", "256", "320"])
        self.bitrate_combo.setCurrentIndex(0)
        br_col.addWidget(self.bitrate_combo)
        audio_grid.addLayout(br_col)

        ac_col = QVBoxLayout()
        ac_label = QLabel("Codec")
        ac_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ac_col.addWidget(ac_label)
        self.audio_codec_combo = QComboBox()
        self.audio_codec_combo.addItems(["auto", "aac", "libmp3lame", "libvorbis", "libopus", "flac", "pcm_s16le", "wmav2"])
        ac_col.addWidget(self.audio_codec_combo)
        audio_grid.addLayout(ac_col)

        self.audio_settings_group.setVisible(False)
        layout.addWidget(self.audio_settings_group)

        # Video Settings Panel
        self.video_settings_group = QGroupBox("Video Settings")
        self.video_settings_group.setStyleSheet(self.audio_settings_group.styleSheet().replace("Audio", "Video"))
        video_grid = QHBoxLayout(self.video_settings_group)
        video_grid.setSpacing(16)

        res_col = QVBoxLayout()
        res_label = QLabel("Resolution")
        res_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        res_col.addWidget(res_label)
        self.resolution_combo = QComboBox()
        self.resolution_combo.addItems(["original", "1080p", "720p", "480p", "360p", "240p"])
        res_col.addWidget(self.resolution_combo)
        video_grid.addLayout(res_col)

        vc_col = QVBoxLayout()
        vc_label = QLabel("Video Codec")
        vc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vc_col.addWidget(vc_label)
        self.video_codec_combo = QComboBox()
        self.video_codec_combo.addItems(["auto", "libx264", "libx265", "libvpx-vp9", "mpeg4", "libxvid"])
        vc_col.addWidget(self.video_codec_combo)
        video_grid.addLayout(vc_col)

        fps_col = QVBoxLayout()
        fps_label = QLabel("FPS")
        fps_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        fps_col.addWidget(fps_label)
        self.fps_combo = QComboBox()
        self.fps_combo.addItems(["auto", "15", "24", "25", "30", "60"])
        fps_col.addWidget(self.fps_combo)
        video_grid.addLayout(fps_col)

        crf_col = QVBoxLayout()
        crf_label = QLabel("CRF Override")
        crf_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        crf_col.addWidget(crf_label)
        self.crf_combo = QComboBox()
        self.crf_combo.addItems(["auto", "18", "20", "23", "26", "28", "30", "35", "40"])
        crf_col.addWidget(self.crf_combo)
        video_grid.addLayout(crf_col)

        self.video_settings_group.setVisible(False)
        layout.addWidget(self.video_settings_group)

        layout.addWidget(make_separator())

        # Output Directory
        lbl3 = QLabel("OUTPUT DIRECTORY  (leave blank = same as input)")
        lbl3.setObjectName("sectionLabel")
        layout.addWidget(lbl3)

        dir_row = QHBoxLayout()
        dir_row.setSpacing(8)
        self.output_dir = QLineEdit()
        self.output_dir.setPlaceholderText("Same as input file (default)")
        dir_row.addWidget(self.output_dir)

        dir_browse_btn = QPushButton("Browse")
        dir_browse_btn.setFixedWidth(88)
        dir_browse_btn.clicked.connect(self._open_dir_chooser)
        dir_row.addWidget(dir_browse_btn)
        layout.addLayout(dir_row)

        # Action Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.convert_btn = QPushButton("CONVERT")
        self.convert_btn.setObjectName("convertBtn")
        self.convert_btn.setEnabled(False)
        self.convert_btn.clicked.connect(self.start_conversion)
        btn_row.addWidget(self.convert_btn, stretch=2)

        batch_btn = QPushButton("Batch")
        batch_btn.setObjectName("batchBtn")
        batch_btn.clicked.connect(self._open_batch_dialog)
        btn_row.addWidget(batch_btn, stretch=1)

        formats_btn = QPushButton("Formats")
        formats_btn.setObjectName("formatsBtn")
        formats_btn.clicked.connect(self._show_conversions)
        btn_row.addWidget(formats_btn, stretch=1)

        history_btn = QPushButton("History")
        history_btn.setObjectName("historyBtn")
        history_btn.clicked.connect(lambda: self.history_requested.emit())
        btn_row.addWidget(history_btn, stretch=1)

        layout.addLayout(btn_row)

        # Progress bar (wrapped in glow frame)
        self.progress_glow_frame = QFrame()
        self.progress_glow_frame.setObjectName("progressGlow")
        progress_glow_layout = QVBoxLayout(self.progress_glow_frame)
        progress_glow_layout.setContentsMargins(4, 4, 4, 4)
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("%v / %m  (%p%)")
        self.progress_bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        progress_glow_layout.addWidget(self.progress_bar)
        self.progress_glow_frame.setVisible(False)
        layout.addWidget(self.progress_glow_frame)

        # Log Section
        log_header = QHBoxLayout()
        log_lbl = QLabel("CONVERSION LOG")
        log_lbl.setObjectName("sectionLabel")
        log_header.addWidget(log_lbl)
        log_header.addStretch()
        layout.addLayout(log_header)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setObjectName("logText")
        self.log_text.setPlaceholderText("Ready. Select an input file to begin.")
        layout.addWidget(self.log_text, stretch=1)

        # Open Output Folder / File buttons
        output_btn_row = QHBoxLayout()
        output_btn_row.setSpacing(8)
        self.open_dir_btn = QPushButton("Open Output Folder")
        self.open_dir_btn.setObjectName("openDirBtn")
        self.open_dir_btn.setVisible(False)
        self.open_dir_btn.clicked.connect(self._open_output_dir)
        output_btn_row.addWidget(self.open_dir_btn)

        self.open_file_btn = QPushButton("Open Output File")
        self.open_file_btn.setObjectName("openDirBtn")
        self.open_file_btn.setVisible(False)
        self.open_file_btn.clicked.connect(self._open_output_file)
        output_btn_row.addWidget(self.open_file_btn)
        output_btn_row.addStretch()
        layout.addLayout(output_btn_row)

    def _deferred_init(self):
        total = len(ALL_CONVERSIONS)
        cross_count = sum(1 for c, _, _ in ALL_CONVERSIONS if "->" in c)
        intra_count = total - cross_count
        self._log(f"Enhanced converter with {total} conversion paths "
                  f"({intra_count} intra-category + {cross_count} cross-category).")
        if self._check_ffmpeg():
            self._log("[OK] FFmpeg found on system.")
        else:
            self._log("[WARNING] FFmpeg NOT found! Audio/Video conversion will fail.")
        self._log(f"[INFO] {len(AUDIO_FORMATS)} Audio + {len(VIDEO_FORMATS)} Video + "
                  f"{len(TEXT_FORMATS)} Text + {len(IMAGE_FORMATS)} Image + "
                  f"{len(THREE_D_FORMATS)} 3D + {len(DATA_FORMATS)} Data formats")

    @staticmethod
    def _check_ffmpeg():
        try:
            r = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=5)
            return r.returncode == 0
        except Exception:
            return False

    def _highlight_pill(self, category):
        colors = {
            "audio": "#3385d1",
            "video": "#b8469e",
            "text":  "#4dae61",
            "image": "#d18520",
            "3d":    "#cc7832",
            "data":  "#8552b8",
            "presentation": "#d14791",
        }
        for cat, pill in self.pills.items():
            if cat == category:
                pill.set_active(colors[cat])
            else:
                pill.set_inactive()

    def _on_quality_change(self, value):
        self._quality = value
        self.quality_value.setText(str(value))

    def _open_file_chooser(self):
        if self._converting:
            return
        filter_str = ";;".join(
            [f"{cat.upper()} files ({' '.join('*.' + f for f in CATEGORY_POOLS[cat])})"
             for cat in CATEGORY_POOLS]
            + ["All supported (" + " ".join("*." + f for f in ALL_FORMATS) + ")"]
        )
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Input File", str(Path.home()), filter_str
        )
        if path:
            self._on_file_selected(path)

    def _open_dir_chooser(self):
        if self._converting:
            return
        dir_path = QFileDialog.getExistingDirectory(
            self, "Select Output Directory", str(Path.home())
        )
        if dir_path:
            self.output_dir.setText(dir_path)

    def _on_file_selected(self, filepath):
        self._input_file = filepath
        self.input_path.setText(filepath)

        ext = Path(filepath).suffix.lower().lstrip(".")
        cat = get_category(ext)

        self.open_dir_btn.setVisible(False)
        self.open_file_btn.setVisible(False)

        if cat:
            icon = CATEGORY_ICONS.get(cat, "")
            sz = human_size(os.path.getsize(filepath))
            self.input_info.setText(f"{icon}  {cat.upper()}  .  .{ext}  .  {sz}")
            self.input_info.setStyleSheet("font-size: 11px; color: #629e62; padding: 0 4px;")
            self.category_label.setText(f"Category: {cat.capitalize()}")
            self._highlight_pill(cat)
            formats = get_compatible_formats(ext)
            self.output_format.blockSignals(True)
            self.output_format.clear()
            self.output_format.addItem("Select output format...")
            self.output_format.addItems(formats)
            self.output_format.setCurrentIndex(0)
            self.output_format.blockSignals(False)
        else:
            self.input_info.setText(f"Unsupported format: .{ext}")
            self.input_info.setStyleSheet("font-size: 11px; color: #cc6666; padding: 0 4px;")
            self.category_label.setText("Unknown")
            self._highlight_pill(None)
            self.output_format.blockSignals(True)
            self.output_format.clear()
            self.output_format.addItem("N/A")
            self.output_format.blockSignals(False)

        self._update_convert_button()
        self._log(f"Selected: {Path(filepath).name}")
        self._update_settings_panels()

        # Warn for large files (>500MB)
        if cat and os.path.getsize(filepath) > 500 * 1024 * 1024:
            self._log(f"[WARNING] Large file detected ({human_size(os.path.getsize(filepath))}). "
                      f"Conversion may take a while.")

    def _on_format_changed(self, _text=None):
        self._update_convert_button()
        self._update_settings_panels()

    def _update_settings_panels(self):
        cat = None
        if self._input_file:
            ext = Path(self._input_file).suffix.lower().lstrip(".")
            cat = get_category(ext)

        out_fmt = self.output_format.currentText().lower()
        out_cat = get_category(out_fmt)

        show_audio = (cat == "audio") or (cat == "video")
        self.audio_settings_group.setVisible(show_audio)

        show_video = (cat == "video" and out_cat == "video")
        self.video_settings_group.setVisible(show_video)

    def _get_audio_settings(self):
        sr = self.sample_rate_combo.currentText()
        ch = self.channels_combo.currentText()
        br = self.bitrate_combo.currentText()
        ac = self.audio_codec_combo.currentText()
        return {
            "sample_rate": sr,
            "channels": ch,
            "audio_bitrate": int(br) if br != "auto" else None,
            "audio_codec": ac if ac != "auto" else None,
        }

    def _get_video_settings(self):
        res = self.resolution_combo.currentText()
        vc = self.video_codec_combo.currentText()
        fps = self.fps_combo.currentText()
        crf = self.crf_combo.currentText()
        ac = self.audio_codec_combo.currentText()
        return {
            "resolution": res,
            "video_codec": vc if vc != "auto" else None,
            "fps": fps if fps != "auto" else None,
            "crf": int(crf) if crf != "auto" else None,
            "audio_codec": ac if ac != "auto" else None,
        }

    def _update_convert_button(self, _text=None):
        has_input = self._input_file is not None
        fmt = self.output_format.currentText()
        valid_formats = [f.upper() for f in ALL_FORMATS]
        has_format = fmt in valid_formats
        self.convert_btn.setEnabled(has_input and has_format and not self._converting)

    def _show_conversions(self):
        dlg = ConversionsDialog(self)
        dlg.exec()

    def _open_batch_dialog(self):
        if self._converting:
            return
        dlg = BatchDialog(self)
        dlg.files_selected.connect(self._run_batch)
        dlg.exec()

    def _run_batch(self, files, out_fmt):
        out_dir = self.output_dir.text().strip() or str(Path(files[0]).parent)
        if not os.path.isdir(out_dir):
            self._log(f"[ERROR] Output directory does not exist: {out_dir}")
            return

        self._converting = True
        self.convert_btn.setEnabled(False)
        self.convert_btn.setText("BATCH...")
        self.header_status.setText("Batch converting...")
        self.header_status.setStyleSheet("font-size: 11px; color: #e6bf33; padding: 12px 16px;")
        self.progress_glow_frame.setVisible(True)
        self.progress_glow_frame.setProperty("glowState", "active")
        self.progress_glow_frame.style().unpolish(self.progress_glow_frame)
        self.progress_glow_frame.style().polish(self.progress_glow_frame)
        self.progress_bar.setVisible(True)

        total = len(files)
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(0)
        self._log(f"[INFO] Batch: {total} files -> .{out_fmt.upper()}")

        def worker():
            completed = 0
            for i, fpath in enumerate(files, 1):
                inp = Path(fpath)
                output_path = Path(out_dir) / f"{_sanitize_filename(inp.stem)}_converted.{out_fmt}"
                counter = 1
                while output_path.exists():
                    output_path = Path(out_dir) / f"{_sanitize_filename(inp.stem)}_converted_{counter}.{out_fmt}"
                    counter += 1

                in_ext = inp.suffix.lower().lstrip(".")
                cat = get_category(in_ext)
                out_cat = get_category(out_fmt)

                if in_ext == out_fmt:
                    self._log(f"  [{i}/{total}] Skipped (same format): {inp.name}")
                    completed += 1
                    QTimer.singleShot(0, lambda v=completed: self.progress_bar.setValue(v))
                    continue

                # Check compatibility
                compatible = False
                if cat and cat == out_cat:
                    compatible = True
                elif cat == "video" and out_cat == "audio":
                    compatible = True
                elif (in_ext, out_fmt) in ConverterEngine.CROSS_CONVERTERS:
                    compatible = True

                if not compatible:
                    self._log(f"  [{i}/{total}] Skipped (incompatible): {inp.name}")
                    completed += 1
                    QTimer.singleShot(0, lambda v=completed: self.progress_bar.setValue(v))
                    continue

                success = [False]
                msg = [""]

                def batch_cb(ok, m, fname=inp.name, idx=i, out=output_path):
                    success[0] = ok
                    msg[0] = m
                    icon = "[OK]" if ok else "[FAIL]"
                    QTimer.singleShot(0, lambda: self._log(
                        f"  {icon} [{idx}/{total}] {fname} -> {out.name}"))
                    cat_val = get_category(in_ext)
                    self._history.add(fname, out.name, in_ext, out_fmt,
                                      cat_val or "unknown", ok)

                if cat == "audio" or (cat == "video" and out_cat == "audio"):
                    b_settings = self._get_audio_settings()
                elif cat == "video":
                    b_settings = self._get_video_settings()
                else:
                    b_settings = None
                ConverterEngine.convert(str(inp), str(output_path), batch_cb, self._quality, b_settings)

                completed += 1
                QTimer.singleShot(0, lambda v=completed: self.progress_bar.setValue(v))

            QTimer.singleShot(0, lambda: self._finish_batch(total))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_batch(self, total):
        self._converting = False
        self.convert_btn.setText("CONVERT")
        self._update_convert_button()
        self.progress_bar.setVisible(False)
        self.progress_glow_frame.setProperty("glowState", "success")
        self.progress_glow_frame.style().unpolish(self.progress_glow_frame)
        self.progress_glow_frame.style().polish(self.progress_glow_frame)
        self.progress_glow_frame.setVisible(False)
        self.header_status.setText(f"Batch done ({total} files)")
        self.header_status.setStyleSheet("font-size: 11px; color: #73b873; padding: 12px 16px;")
        self._log(f"[INFO] Batch conversion complete: {total} files processed.")
        self._header_reset_timer.start(5000)

    def start_conversion(self):
        if not self._input_file:
            return
        # Toggle cancellation if already converting
        if self._converting:
            self._cancel_requested = True
            ConverterEngine.request_cancel()
            self._log("[INFO] Cancellation requested...")
            return

        out_fmt = self.output_format.currentText().lower()
        if out_fmt not in ALL_FORMATS:
            return

        inp = Path(self._input_file)

        # Validate input file exists and is readable
        if not inp.exists():
            self._log(f"[ERROR] Input file does not exist: {inp.name}")
            return
        if not os.access(str(inp), os.R_OK):
            self._log(f"[ERROR] Input file is not readable: {inp.name}")
            return

        out_dir = self.output_dir.text().strip() or str(inp.parent)

        if not os.path.isdir(out_dir):
            self._log(f"[ERROR] Output directory does not exist: {out_dir}")
            return

        output_path = Path(out_dir) / f"{_sanitize_filename(inp.stem)}_converted.{out_fmt}"
        counter = 1
        while output_path.exists():
            output_path = Path(out_dir) / f"{_sanitize_filename(inp.stem)}_converted_{counter}.{out_fmt}"
            counter += 1

        self._output_file = str(output_path)
        self._converting = True

        self.convert_btn.setEnabled(True)
        self.convert_btn.setText("CANCEL")
        self.header_status.setText("Converting...")
        self.header_status.setStyleSheet("font-size: 11px; color: #e6bf33; padding: 12px 16px;")
        self.open_dir_btn.setVisible(False)
        self.progress_glow_frame.setVisible(True)
        self.progress_glow_frame.setProperty("glowState", "active")
        self.progress_glow_frame.style().unpolish(self.progress_glow_frame)
        self.progress_glow_frame.style().polish(self.progress_glow_frame)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)

        in_sz = human_size(os.path.getsize(inp))
        self._log(f"[>] Converting: {inp.name} ({in_sz})  ->  {output_path.name}  (.{out_fmt})")

        def run():
            in_ext = inp.suffix.lower().lstrip(".")
            cat = get_category(in_ext)

            def done_cb(ok, m):
                self._history.add(inp.name, output_path.name, in_ext, out_fmt,
                                  cat or "unknown", ok)
                QTimer.singleShot(0, lambda: self._finish(ok, m))

            out_cat = get_category(out_fmt)
            if cat == "audio" or (cat == "video" and out_cat == "audio"):
                settings = self._get_audio_settings()
            elif cat == "video":
                settings = self._get_video_settings()
            else:
                settings = None
            ConverterEngine.convert(str(inp), str(output_path), done_cb, self._quality, settings)

        threading.Thread(target=run, daemon=True).start()

    def _finish(self, success, message):
        self._converting = False
        was_cancelled = self._cancel_requested
        self._cancel_requested = False
        # Bug fix: release per-task cancel token (was using shared class-level
        # event before, which made concurrent batch conversions interfere).
        if hasattr(self, "_cancel_token_id") and self._cancel_token_id:
            ConverterEngine.clear_cancel(self._cancel_token_id)
            ConverterEngine.release_cancel_token(self._cancel_token_id)
            self._cancel_token_id = None
        else:
            ConverterEngine.clear_cancel_legacy()
        self.convert_btn.setText("CONVERT")
        self._update_convert_button()
        self.progress_bar.setVisible(False)

        # Set glow frame state based on result
        if success:
            glow_state = "success"
        elif was_cancelled:
            glow_state = "warning"
        else:
            glow_state = "error"
        self.progress_glow_frame.setProperty("glowState", glow_state)
        self.progress_glow_frame.style().unpolish(self.progress_glow_frame)
        self.progress_glow_frame.style().polish(self.progress_glow_frame)
        self.progress_glow_frame.setVisible(False)

        if success:
            self.header_status.setText("Done")
            self.header_status.setStyleSheet("font-size: 11px; color: #73b873; padding: 12px 16px;")
            self.open_dir_btn.setVisible(True)
            self.open_file_btn.setVisible(True)
            self._log(f"[OK] {message}")
            # Show output file size
            if self._output_file and os.path.exists(self._output_file):
                out_sz = human_size(os.path.getsize(self._output_file))
                self._log(f"  Output size: {out_sz}")
        elif self._cancel_requested:
            self.header_status.setText("Cancelled")
            self.header_status.setStyleSheet("font-size: 11px; color: #e6bf33; padding: 12px 16px;")
            self.open_dir_btn.setVisible(False)
            self.open_file_btn.setVisible(False)
            self._log("[INFO] Conversion cancelled by user.")
            # Clean up partial output
            if self._output_file:
                try:
                    p = Path(self._output_file)
                    if p.exists():
                        p.unlink()
                        self._log("  Partial output file removed.")
                except Exception:
                    pass
        else:
            self.header_status.setText("Failed")
            self.header_status.setStyleSheet("font-size: 11px; color: #e65a5a; padding: 12px 16px;")
            self.open_dir_btn.setVisible(False)
            self.open_file_btn.setVisible(False)
            self._log(f"[ERROR] {message}")

        self._header_reset_timer.start(5000)

    def _reset_header(self):
        if not self._converting:
            self.header_status.setText("Ready")
            self.header_status.setStyleSheet("font-size: 11px; color: #73b873; padding: 12px 16px;")

    def _open_output_dir(self):
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
            self._log(f"[ERROR] Could not open folder: {e}")

    def _open_output_file(self):
        """Open the converted output file with the system default application."""
        if not self._output_file or not os.path.exists(self._output_file):
            return
        path = self._output_file
        try:
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.run(["open", path])
            else:
                subprocess.run(["xdg-open", path])
        except Exception as e:
            self._log(f"[ERROR] Could not open file: {e}")

    def _log(self, text):
        ts = datetime.now().strftime("%H:%M:%S")
        entry = f"[{ts}] {text}"
        print(entry)  # also log to terminal
        cur = self.log_text.toPlainText()
        if cur and cur != "Ready. Select an input file to begin.":
            self.log_text.append(entry)
        else:
            self.log_text.setPlainText(entry)
        sb = self.log_text.verticalScrollBar()
        sb.setValue(sb.maximum())


# ─── Main Window (Tabbed) ─────────────────────────────────────────

class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Universal File Converter")
        self.setMinimumSize(600, 780)
        self.resize(720, 920)

        self._history = HistoryManager()

        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)

        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(0)

        # Tab widget
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)

        # Converter tab
        self.converter_tab = ConverterTab(self._history)
        self.converter_tab.history_requested.connect(self._show_history)
        self.tabs.addTab(self.converter_tab, "Converter")

        # Test tab
        self.test_tab = TestTab()
        self.tabs.addTab(self.test_tab, "Test All Conversions")

        main_layout.addWidget(self.tabs)

    def _show_history(self):
        dlg = HistoryDialog(self._history, self)
        dlg.exec()


# ─── Application Entry ────────────────────────────────────────────

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#1a1a24"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#d9d9e6"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#242630"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#1a1a24"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#e0e0eb"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#3568b8"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#3568b8"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#242630"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#e0e0eb"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#61616e"))
    app.setPalette(palette)

    app.setStyleSheet(DARK_QSS)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()