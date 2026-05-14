"""
Pytest Suite for Universal File Converter
==========================================
Run with: pytest test_converter.py -v
"""

import os
import sys
import subprocess
import threading
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Prevent Kivy from consuming command line args and suppress its logs
os.environ["KIVY_NO_ARGS"] = "1"
os.environ["KIVY_NO_CONSOLELOG"] = "1"

# Import the application modules
# Assuming the main file is named 'app.py'
from app import (
    ConverterEngine,
    ConverterRoot,
    ConverterApp,
    get_category,
    get_compatible_formats,
    human_size,
    md_to_html,
    html_to_md,
    md_to_plain_text,
    html_to_plain_text,
    AUDIO_FORMATS,
    VIDEO_FORMATS,
    TEXT_FORMATS,
    ALL_FORMATS,
)


# ─── Fixtures ────────────────────────────────────────────────────

class CallbackCatcher:
    """Helper to catch asynchronous callbacks from ConverterEngine."""
    def __init__(self):
        self.success = None
        self.message = None
        self.event = threading.Event()

    def __call__(self, success, message):
        self.success = success
        self.message = message
        self.event.set()

    def wait(self, timeout=5):
        return self.event.wait(timeout)


@pytest.fixture
def cb():
    return CallbackCatcher()


@pytest.fixture
def tmp_text_file(tmp_path):
    p = tmp_path / "sample.txt"
    p.write_text("Hello World\nThis is a test.", encoding="utf-8")
    return p


@pytest.fixture
def tmp_md_file(tmp_path):
    p = tmp_path / "sample.md"
    p.write_text("# Heading 1\n\n**Bold text** and *italic*.\n- Item 1\n- Item 2", encoding="utf-8")
    return p


@pytest.fixture
def tmp_html_file(tmp_path):
    p = tmp_path / "sample.html"
    p.write_text("<html><body><h1>Heading 1</h1><p><strong>Bold</strong> text.</p></body></html>", encoding="utf-8")
    return p


# ─── Helper Function Tests ──────────────────────────────────────

def test_get_category():
    assert get_category("mp3") == "audio"
    assert get_category(".wav") == "audio"
    assert get_category("mkv") == "video"
    assert get_category("pdf") == "text"
    assert get_category("exe") is None

def test_get_compatible_formats():
    # Audio
    formats = get_compatible_formats("wav")
    assert "MP3" in formats
    assert "WAV" not in formats
    
    # Video
    formats = get_compatible_formats("mp4")
    assert "MKV" in formats
    assert "MP4" not in formats
    
    # Text
    formats = get_compatible_formats("txt")
    assert "PDF" in formats and "MD" in formats and "HTML" in formats
    assert "TXT" not in formats

def test_human_size():
    assert human_size(500) == "500.0 B"
    assert human_size(1024) == "1.0 KB"
    assert human_size(1048576) == "1.0 MB"
    assert human_size(1073741824) == "1.0 GB"


# ─── Markdown / HTML Parser Tests ───────────────────────────────

def test_md_to_html():
    md = "# Title\n**bold** *italic* [link](http://a.com)"
    html = md_to_html(md)
    assert "<h1>Title</h1>" in html
    assert "<strong>bold</strong>" in html
    assert "<em>italic</em>" in html
    assert '<a href="http://a.com">link</a>' in html

def test_html_to_md():
    html = "<h1>Title</h1><p><strong>bold</strong></p>"
    md = html_to_md(html)
    assert "# Title" in md
    assert "**bold**" in md

def test_md_to_plain_text():
    md = "# Title\n**bold** *italic*\n- item"
    plain = md_to_plain_text(md)
    assert "#" not in plain
    assert "**" not in plain
    assert "Title" in plain
    assert "bold" in plain

def test_html_to_plain_text():
    html = "<h1>Title</h1><p>Hello</p>"
    plain = html_to_plain_text(html)
    assert "<h1>" not in plain
    assert "Title" in plain
    assert "Hello" in plain


# ─── Engine: Text Conversion Tests ──────────────────────────────

def test_txt_to_pdf(tmp_text_file, tmp_path, cb):
    out = tmp_path / "out.pdf"
    ConverterEngine.convert(str(tmp_text_file), str(out), cb)
    cb.wait()
    assert cb.success is True, f"Conversion failed: {cb.message}"
    assert out.exists()

def test_txt_to_pdf_short_lines(tmp_path, cb):
    """Test the specific edge case that causes 'Not enough horizontal space' crash."""
    p = tmp_path / "short.txt"
    p.write_text("A\nB\nC\nSingleWord", encoding="utf-8")
    out = tmp_path / "out.pdf"
    ConverterEngine.convert(str(p), str(out), cb)
    cb.wait()
    assert cb.success is True, f"Failed on short lines: {cb.message}"

def test_txt_to_md(tmp_text_file, tmp_path, cb):
    out = tmp_path / "out.md"
    ConverterEngine.convert(str(tmp_text_file), str(out), cb)
    cb.wait()
    assert cb.success is True
    content = out.read_text(encoding="utf-8")
    assert "## Hello World" in content  # Short line becomes heading
    assert "This is a test." in content

def test_txt_to_html(tmp_text_file, tmp_path, cb):
    out = tmp_path / "out.html"
    ConverterEngine.convert(str(tmp_text_file), str(out), cb)
    cb.wait()
    assert cb.success is True
    content = out.read_text(encoding="utf-8")
    assert "<html>" in content
    assert "Hello World" in content

def test_md_to_txt(tmp_md_file, tmp_path, cb):
    out = tmp_path / "out.txt"
    ConverterEngine.convert(str(tmp_md_file), str(out), cb)
    cb.wait()
    assert cb.success is True
    content = out.read_text(encoding="utf-8")
    assert "#" not in content
    assert "Heading 1" in content

def test_md_to_html(tmp_md_file, tmp_path, cb):
    out = tmp_path / "out.html"
    ConverterEngine.convert(str(tmp_md_file), str(out), cb)
    cb.wait()
    assert cb.success is True
    content = out.read_text(encoding="utf-8")
    assert "<h1>Heading 1</h1>" in content
    assert "<strong>Bold text</strong>" in content

def test_md_to_pdf(tmp_md_file, tmp_path, cb):
    out = tmp_path / "out.pdf"
    ConverterEngine.convert(str(tmp_md_file), str(out), cb)
    cb.wait()
    assert cb.success is True, f"Conversion failed: {cb.message}"
    assert out.exists()

def test_md_to_pdf_unicode(tmp_path, cb):
    """Test the specific edge case that causes 'Character outside range' crash."""
    p = tmp_path / "unicode.md"
    # Test literal bullet character, smart quotes, em-dash, and list dashes
    p.write_text("# Test\n\n• Item one\n\n- Dash item\n\n“Smart quotes” and em—dash", encoding="utf-8")
    out = tmp_path / "out.pdf"
    ConverterEngine.convert(str(p), str(out), cb)
    cb.wait()
    assert cb.success is True, f"Failed on Unicode characters: {cb.message}"

def test_html_to_txt(tmp_html_file, tmp_path, cb):
    out = tmp_path / "out.txt"
    ConverterEngine.convert(str(tmp_html_file), str(out), cb)
    cb.wait()
    assert cb.success is True
    content = out.read_text(encoding="utf-8")
    assert "<h1>" not in content
    assert "Heading 1" in content

def test_html_to_md(tmp_html_file, tmp_path, cb):
    out = tmp_path / "out.md"
    ConverterEngine.convert(str(tmp_html_file), str(out), cb)
    cb.wait()
    assert cb.success is True
    content = out.read_text(encoding="utf-8")
    assert "# Heading 1" in content
    assert "**Bold**" in content

def test_html_to_pdf(tmp_html_file, tmp_path, cb):
    out = tmp_path / "out.pdf"
    ConverterEngine.convert(str(tmp_html_file), str(out), cb)
    cb.wait()
    assert cb.success is True, f"Conversion failed: {cb.message}"
    assert out.exists()

def test_pdf_to_txt(tmp_path, cb):
    # First create a PDF to test against
    pdf_path = tmp_path / "source.pdf"
    ConverterEngine.txt_to_pdf(str(tmp_path / "dummy.txt"), str(pdf_path), lambda s, m: None)
    
    # Create the dummy source
    (tmp_path / "dummy.txt").write_text("Sample text for PDF.")
    ConverterEngine.txt_to_pdf(str(tmp_path / "dummy.txt"), str(pdf_path), cb)
    cb.wait()
    
    if pdf_path.exists():
        out = tmp_path / "out.txt"
        ConverterEngine.convert(str(pdf_path), str(out), cb)
        cb.wait()
        assert cb.success is True
        content = out.read_text(encoding="utf-8")
        assert "Sample text" in content

def test_pdf_to_md(tmp_path, cb):
    # Create source PDF
    src = tmp_path / "source.txt"
    src.write_text("PDF to MD test content.")
    pdf_path = tmp_path / "source.pdf"
    
    # Use a separate callback for setup to avoid state collisions
    setup_cb = CallbackCatcher()
    ConverterEngine.txt_to_pdf(str(src), str(pdf_path), setup_cb)
    setup_cb.wait()
    assert setup_cb.success is True, f"Setup PDF creation failed: {setup_cb.message}"

    out = tmp_path / "out.md"
    ConverterEngine.convert(str(pdf_path), str(out), cb)
    cb.wait()
    assert cb.success is True, f"Conversion failed with message: {cb.message}"
    content = out.read_text(encoding="utf-8")
    assert "## Page 1" in content
    
    out = tmp_path / "out.md"
    ConverterEngine.convert(str(pdf_path), str(out), cb)
    cb.wait()
    assert cb.success is True
    content = out.read_text(encoding="utf-8")
    assert "## Page 1" in content

def test_pdf_to_html(tmp_path, cb):
    # Create source PDF
    src = tmp_path / "source.txt"
    src.write_text("PDF to HTML test content.")
    pdf_path = tmp_path / "source.pdf"
    
    # Use a separate callback for setup to avoid state collisions
    setup_cb = CallbackCatcher()
    ConverterEngine.txt_to_pdf(str(src), str(pdf_path), setup_cb)
    setup_cb.wait()
    assert setup_cb.success is True, f"Setup PDF creation failed: {setup_cb.message}"

    out = tmp_path / "out.html"
    ConverterEngine.convert(str(pdf_path), str(out), cb)
    cb.wait()
    assert cb.success is True, f"Conversion failed with message: {cb.message}"
    content = out.read_text(encoding="utf-8")
    assert "<h2>Page 1</h2>" in content


# ─── Engine: Audio/Video Mock Tests ─────────────────────────────

def test_audio_conversion_mock_ffmpeg(tmp_path, cb, monkeypatch):
    # Create dummy input file
    inp = tmp_path / "input.wav"
    inp.write_text("fake audio data")
    out = tmp_path / "output.mp3"

    # Mock subprocess.run to simulate FFmpeg success
    def mock_run_success(*args, **kwargs):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""
        # Simulate FFmpeg creating the output file
        out.write_text("fake mp3 data")
        return mock_result

    monkeypatch.setattr(subprocess, "run", mock_run_success)
    
    ConverterEngine.convert(str(inp), str(out), cb)
    cb.wait()
    
    assert cb.success is True
    assert "✅" in cb.message

def test_video_conversion_mock_ffmpeg(tmp_path, cb, monkeypatch):
    inp = tmp_path / "input.mp4"
    inp.write_text("fake video data")
    out = tmp_path / "output.mkv"

    def mock_run_success(*args, **kwargs):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""
        out.write_text("fake mkv data")
        return mock_result

    monkeypatch.setattr(subprocess, "run", mock_run_success)
    
    ConverterEngine.convert(str(inp), str(out), cb)
    cb.wait()
    
    assert cb.success is True

def test_ffmpeg_missing(tmp_path, cb, monkeypatch):
    inp = tmp_path / "input.wav"
    inp.write_text("fake audio data")
    out = tmp_path / "output.mp3"

    # Mock subprocess.run to raise FileNotFoundError
    def mock_run_missing(*args, **kwargs):
        raise FileNotFoundError("ffmpeg not found")

    monkeypatch.setattr(subprocess, "run", mock_run_missing)
    
    ConverterEngine.convert(str(inp), str(out), cb)
    cb.wait()
    
    assert cb.success is False
    assert "FFmpeg not found" in cb.message

def test_ffmpeg_failure(tmp_path, cb, monkeypatch):
    inp = tmp_path / "input.mp4"
    inp.write_text("fake video data")
    out = tmp_path / "output.mkv"

    # Mock subprocess.run to return non-zero exit code
    def mock_run_fail(*args, **kwargs):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "Invalid data found"
        return mock_result

    monkeypatch.setattr(subprocess, "run", mock_run_fail)
    
    ConverterEngine.convert(str(inp), str(out), cb)
    cb.wait()
    
    assert cb.success is False
    assert "Invalid data found" in cb.message


# ─── Kivy UI Tests ──────────────────────────────────────────────

# Fixture to handle Kivy app base setup without starting the event loop
@pytest.fixture(scope="module")
def kivy_app():
    app = ConverterApp()
    # build() creates the root widget but doesn't start the loop
    root = app.build()
    yield app, root
    # Clean up window
    if app.root:
        app.stop()

def test_ui_initial_state(kivy_app):
    app, root = kivy_app
    # Convert button should be disabled initially
    assert root.ids.convert_btn.disabled is True
    assert root.ids.output_format.text == "Select output format..."

def test_ui_file_selection_text(kivy_app, tmp_text_file):
    app, root = kivy_app
    
    # Simulate selecting a text file
    root._on_file_selected(str(tmp_text_file))
    
    assert root._input_file == str(tmp_text_file)
    assert root.ids.input_path.text == str(tmp_text_file)
    assert "TEXT" in root.ids.input_info.text
    assert root.ids.category_label.text == "Category: Text"
    
    # Check spinner values populated correctly
    spinner_values = root.ids.output_format.values
    assert "PDF" in spinner_values
    assert "MD" in spinner_values
    assert "HTML" in spinner_values
    assert "TXT" not in spinner_values  # Should not contain input format

def test_ui_convert_button_enable(kivy_app, tmp_text_file):
    app, root = kivy_app
    
    # Simulate file selection
    root._on_file_selected(str(tmp_text_file))
    
    # Button still disabled because output format not selected
    assert root.ids.convert_btn.disabled is True
    
    # Simulate selecting an output format
    root.ids.output_format.text = "PDF"
    root._update_convert_button()
    
    # Button should now be enabled
    assert root.ids.convert_btn.disabled is False

def test_ui_file_selection_audio(kivy_app, tmp_path):
    app, root = kivy_app
    
    # Create a dummy audio file
    audio_file = tmp_path / "test.mp3"
    audio_file.write_text("fake audio")
    
    root._on_file_selected(str(audio_file))
    
    assert "AUDIO" in root.ids.input_info.text
    spinner_values = root.ids.output_format.values
    assert "WAV" in spinner_values
    assert "OGG" in spinner_values
    assert "MP3" not in spinner_values

def test_ui_unsupported_format(kivy_app, tmp_path):
    app, root = kivy_app
    
    weird_file = tmp_path / "test.xyz"
    weird_file.write_text("unknown format")
    
    root._on_file_selected(str(weird_file))
    
    assert "Unsupported" in root.ids.input_info.text
    assert root.ids.output_format.values == []
    assert root.ids.convert_btn.disabled is True

def test_ui_conversion_callback_success(kivy_app):
    app, root = kivy_app
    
    # Simulate a successful conversion callback
    root._finish(True, "✅ Converted → out.pdf")
    
    assert root._converting is False
    assert root.ids.convert_btn.text == "⚡  CONVERT"
    assert root.ids.header_status.text == "Done ✓"
    assert root.ids.open_dir_btn.disabled is False
    assert root.ids.open_dir_btn.opacity == 1

def test_ui_conversion_callback_failure(kivy_app):
    app, root = kivy_app

    # Simulate a failed conversion callback
    root._finish(False, "❌ FFmpeg not found")

    assert root._converting is False
    assert root.ids.header_status.text == "Failed ✗"
    assert root.ids.open_dir_btn.disabled is True
    assert root.ids.open_dir_btn.opacity == 0  # Also test opacity