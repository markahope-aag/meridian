"""Tests for web.helpers — path safety, sanitizer, frontmatter parsing, dates.

These are the security-critical and data-critical helpers that must
not regress. Run with: python -m pytest tests/test_helpers.py -v
"""

import os
import tempfile
from pathlib import Path

import pytest

# Set MERIDIAN_ROOT to a temp dir before importing so the module
# doesn't try to read from /meridian/ (which doesn't exist locally).
_TMPDIR = tempfile.mkdtemp()
os.environ["MERIDIAN_ROOT"] = _TMPDIR

from web.helpers import (
    coerce_date_str,
    parse_frontmatter,
    safe_resolve,
    sanitize_html,
)


# =========================================================================
# sanitize_html — XSS defense
# =========================================================================

class TestSanitizeHTML:
    def test_strips_script_tag(self):
        assert sanitize_html("<script>alert(1)</script>") == "alert(1)"

    def test_strips_script_with_attributes(self):
        result = sanitize_html('<script src="evil.js"></script>')
        assert "<script" not in result

    def test_strips_iframe(self):
        assert sanitize_html('<iframe src="evil.com"></iframe>') == ""

    def test_strips_object_embed(self):
        assert "<object" not in sanitize_html('<object data="x"></object>')
        assert "<embed" not in sanitize_html('<embed src="x">')

    def test_strips_style_tag(self):
        result = sanitize_html("<style>body{display:none}</style>")
        assert "<style" not in result
        assert "body{display:none}" in result  # text content preserved

    def test_strips_event_handlers(self):
        result = sanitize_html('<p onclick="evil()">text</p>')
        assert "onclick" not in result
        assert "<p>" in result
        assert "text" in result

    def test_strips_onerror(self):
        result = sanitize_html('<img src="x" onerror="evil()">')
        assert "onerror" not in result
        assert 'src="x"' in result

    def test_strips_javascript_url(self):
        result = sanitize_html('<a href="javascript:void(0)">link</a>')
        assert "javascript" not in result
        assert "<a>" in result

    def test_strips_vbscript_url(self):
        result = sanitize_html('<a href="vbscript:evil">link</a>')
        assert "vbscript" not in result

    def test_strips_data_url(self):
        result = sanitize_html('<a href="data:text/html,<script>alert(1)</script>">x</a>')
        assert "data:" not in result

    def test_strips_style_expression(self):
        result = sanitize_html('<div style="background:expression(evil())">ok</div>')
        assert "expression" not in result
        assert "<div>" in result

    def test_strips_style_javascript(self):
        result = sanitize_html('<div style="background:javascript:evil()">ok</div>')
        assert "javascript" not in result

    def test_passes_safe_tags(self):
        html = "<p>Hello <strong>bold</strong> and <code>code</code></p>"
        assert sanitize_html(html) == html

    def test_passes_links_with_safe_href(self):
        html = '<a href="/article/wiki/knowledge/seo/index.md">SEO</a>'
        assert sanitize_html(html) == html

    def test_passes_images_with_safe_src(self):
        html = '<img src="/images/logo.png" alt="Logo">'
        assert sanitize_html(html) == html

    def test_passes_tables(self):
        html = "<table><tr><td>cell</td></tr></table>"
        assert sanitize_html(html) == html

    def test_strips_html_comments(self):
        result = sanitize_html("<!-- comment -->visible")
        assert "<!--" not in result
        assert "visible" in result

    def test_preserves_text_of_stripped_tags(self):
        assert sanitize_html("<form>text inside form</form>") == "text inside form"

    def test_fallback_on_malformed_html(self):
        # Severely malformed — should escape rather than pass through
        result = sanitize_html("<scr" + "ipt>alert(1)</scr" + "ipt>")
        assert "alert(1)" in result  # text preserved
        assert "<script>" not in result


# =========================================================================
# safe_resolve — path traversal defense
# =========================================================================

class TestSafeResolve:
    @pytest.fixture(autouse=True)
    def setup_tmpdir(self, tmp_path):
        self.root = tmp_path / "meridian"
        self.root.mkdir()
        (self.root / "wiki").mkdir()
        (self.root / "wiki" / "test.md").write_text("hello")
        (self.root / "wiki" / "sub").mkdir()
        (self.root / "wiki" / "sub" / "nested.md").write_text("nested")

    def test_resolves_valid_path(self):
        result = safe_resolve("wiki/test.md", root=self.root)
        assert result is not None
        assert result.name == "test.md"

    def test_resolves_nested_path(self):
        result = safe_resolve("wiki/sub/nested.md", root=self.root)
        assert result is not None
        assert result.name == "nested.md"

    def test_rejects_dotdot_traversal(self):
        assert safe_resolve("../etc/passwd", root=self.root) is None

    def test_rejects_dotdot_in_middle(self):
        assert safe_resolve("wiki/../../../etc/passwd", root=self.root) is None

    def test_rejects_absolute_path(self):
        assert safe_resolve("/etc/passwd", root=self.root) is None

    def test_rejects_nonexistent_file(self):
        assert safe_resolve("wiki/nonexistent.md", root=self.root) is None

    def test_rejects_empty_string(self):
        assert safe_resolve("", root=self.root) is None


# =========================================================================
# parse_frontmatter — YAML parsing
# =========================================================================

class TestParseFrontmatter:
    def test_basic_frontmatter(self):
        text = "---\ntitle: Test\nlayer: 2\n---\nBody text"
        fm, body = parse_frontmatter(text)
        assert fm["title"] == "Test"
        assert fm["layer"] == 2
        assert "Body text" in body

    def test_no_frontmatter(self):
        text = "Just plain text"
        fm, body = parse_frontmatter(text)
        assert fm == {}
        assert body == text

    def test_empty_frontmatter(self):
        text = "---\n---\nBody"
        fm, body = parse_frontmatter(text)
        assert fm == {}
        assert "Body" in body

    def test_malformed_yaml(self):
        text = "---\n[invalid: yaml: {{\n---\nBody"
        fm, body = parse_frontmatter(text)
        # Should not crash — returns empty fm, body is stripped text after ---
        assert isinstance(fm, dict)
        assert fm == {}

    def test_list_frontmatter_rejected(self):
        text = "---\n- item1\n- item2\n---\nBody"
        fm, body = parse_frontmatter(text)
        assert fm == {}
        assert "Body" in body

    def test_complex_frontmatter(self):
        text = (
            "---\n"
            "title: \"Complex Article\"\n"
            "layer: 3\n"
            "tags:\n"
            "  - seo\n"
            "  - google-ads\n"
            "supporting_sources:\n"
            "  - wiki/knowledge/seo/article-a.md\n"
            "---\n"
            "# Heading\n\nContent here."
        )
        fm, body = parse_frontmatter(text)
        assert fm["title"] == "Complex Article"
        assert fm["layer"] == 3
        assert "seo" in fm["tags"]
        assert len(fm["supporting_sources"]) == 1
        assert "# Heading" in body


# =========================================================================
# coerce_date_str — date normalization
# =========================================================================

class TestCoerceDateStr:
    def test_string_date(self):
        assert coerce_date_str("2026-04-12") == "2026-04-12"

    def test_string_datetime(self):
        assert coerce_date_str("2026-04-12T14:30:00Z") == "2026-04-12"

    def test_none(self):
        assert coerce_date_str(None) == ""

    def test_empty_string(self):
        assert coerce_date_str("") == ""

    def test_date_object(self):
        from datetime import date
        assert coerce_date_str(date(2026, 4, 12)) == "2026-04-12"

    def test_datetime_object(self):
        from datetime import datetime
        assert coerce_date_str(datetime(2026, 4, 12, 14, 30)) == "2026-04-12"

    def test_short_string(self):
        assert coerce_date_str("2026") == "2026"
