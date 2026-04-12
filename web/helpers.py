"""Meridian dashboard — pure helper functions.

Markdown rendering, HTML sanitization, frontmatter parsing, date
coercion, path safety. No Flask dependency — these are testable
in isolation with pytest.
"""

from __future__ import annotations

import html as _html_mod
import re
from html.parser import HTMLParser
from pathlib import Path

import markdown
import yaml

from web.config import MERIDIAN_ROOT


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

def get_md():
    """Create a fresh Markdown converter."""
    return markdown.Markdown(extensions=[
        "extra",
        "codehilite",
        "toc",
        "nl2br",
        "sane_lists",
    ])


def process_citations(text: str) -> tuple[str, list[dict]]:
    """Convert [[source1, source2]] inline citations to footnote numbers.

    Returns (modified_text, citations_list). Each citation is a dict
    with 'sources' (list of paths) and 'number' (footnote number).
    """
    citations: list[dict] = []
    citation_map: dict[str, int] = {}
    counter = [1]

    def clean_name(s: str) -> str:
        s = s.strip().replace(".md", "").replace(".MD", "")
        s = s.split("/")[-1]
        return s.replace("-", " ").replace("_", " ").title()

    def replace_citation(match):
        raw = match.group(1)
        sources_raw = [s.strip() for s in raw.split(",") if s.strip()]
        if not sources_raw:
            return match.group(0)
        if len(sources_raw) == 1 and "/" not in sources_raw[0] and "." not in sources_raw[0]:
            return match.group(0)
        key = "|".join(sorted(sources_raw))
        if key in citation_map:
            num = citation_map[key]
        else:
            num = counter[0]
            counter[0] += 1
            citation_map[key] = num
            citations.append({"number": num, "sources": sources_raw})
        return f'<sup class="citation" id="cite-ref-{num}"><a href="#cite-{num}">[{num}]</a></sup>'

    result = re.sub(r"\[\[([^\]]+)\]\]", replace_citation, text)
    return result, citations


def build_sources_html(
    citations: list[dict],
    topic_context: dict | None = None,
) -> str:
    """Build the HTML for the footnote-style sources section."""
    if not citations:
        return ""

    def clean_name(s: str) -> str:
        s = s.strip().replace(".md", "").replace(".MD", "")
        s = s.split("/")[-1]
        return s.replace("-", " ").replace("_", " ").title()

    lines = ['<div class="sources-section">']
    lines.append('<h2>Sources</h2>')
    if topic_context:
        total = topic_context.get("total_fragments", 0)
        cited = len(citations)
        display = topic_context.get("display_name", "")
        slug = topic_context.get("slug", "")
        lines.append(
            f'<p class="sources-subhead">'
            f'{cited} cited of {total} fragments in '
            f'<a href="/topic/{slug}">{display}</a></p>'
        )
    lines.append('<ol class="sources-list">')
    for c in citations:
        num = c["number"]
        source_links = []
        for src in c["sources"]:
            name = clean_name(src)
            href = f"/article/{src}" if not src.startswith("/") else src
            source_links.append(f'<a class="source-link" href="{href}">{name}</a>')
        lines.append(
            f'<li id="cite-{num}">'
            + ", ".join(source_links)
            + f' <a class="source-backref" href="#cite-ref-{num}">↩</a>'
            + "</li>"
        )
    lines.append("</ol></div>")
    return "\n".join(lines)


def convert_wikilinks(text: str) -> str:
    """Convert [[wiki/path/to/file]] to <a> links."""
    def _replace(match):
        raw = match.group(1)
        parts = raw.split("|")
        path = parts[0].strip()
        display = parts[1].strip() if len(parts) > 1 else None
        if not display:
            display = path.split("/")[-1].replace(".md", "").replace("-", " ").title()
        href = f"/article/{path}" if "/" in path else f"/topic/{path}"
        return f'<a href="{href}">{display}</a>'
    return re.sub(r"\[\[([^\]]+)\]\]", _replace, text)


def _split_related_topics(body: str) -> tuple[str, str]:
    """Split off the Related Topics section so its wikilinks aren't
    mistaken for citations."""
    marker = "## Related Topics"
    idx = body.find(marker)
    if idx == -1:
        return body, ""
    return body[:idx], body[idx:]


# ---------------------------------------------------------------------------
# HTML sanitizer
# ---------------------------------------------------------------------------

_ALLOWED_TAGS = frozenset({
    "p", "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "a", "strong", "em", "b", "i",
    "code", "pre", "blockquote", "table", "thead", "tbody", "tfoot",
    "tr", "th", "td", "br", "hr", "img", "sup", "sub",
    "span", "div", "dl", "dt", "dd", "details", "summary",
    "caption", "colgroup", "col",
})
_VOID_TAGS = frozenset({"br", "hr", "img", "col"})
_SAFE_ATTRS_BY_TAG: dict[str, frozenset] = {
    "a":       frozenset({"href", "title", "class", "id", "target", "rel"}),
    "img":     frozenset({"src", "alt", "title", "width", "height", "class"}),
    "code":    frozenset({"class"}),
    "pre":     frozenset({"class"}),
    "span":    frozenset({"class", "id", "style"}),
    "div":     frozenset({"class", "id", "style"}),
    "td":      frozenset({"style", "class", "colspan", "rowspan"}),
    "th":      frozenset({"style", "class", "colspan", "rowspan"}),
    "sup":     frozenset({"class", "id"}),
    "table":   frozenset({"class", "style"}),
    "details": frozenset({"style", "class"}),
    "summary": frozenset({"style", "class"}),
}
_FALLBACK_ATTRS = frozenset({"class", "id"})
_DANGEROUS_URL_SCHEMES = frozenset({"javascript", "vbscript", "data"})


class _HTMLSanitizer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self._out: list[str] = []

    def _safe_attrs(self, tag: str, attrs: list[tuple[str, str | None]]) -> str:
        allowed = _SAFE_ATTRS_BY_TAG.get(tag, _FALLBACK_ATTRS)
        parts: list[str] = []
        for name, value in attrs:
            name = name.lower()
            if name.startswith("on"):
                continue
            if name not in allowed:
                continue
            value = value or ""
            if name in ("href", "src", "action"):
                scheme = value.split(":")[0].lower().strip()
                if scheme in _DANGEROUS_URL_SCHEMES:
                    continue
            if name == "style":
                v = value.lower()
                if "expression" in v or "javascript" in v or "url(" in v:
                    continue
            parts.append(f'{name}="{_html_mod.escape(value, quote=True)}"')
        return (" " + " ".join(parts)) if parts else ""

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag not in _ALLOWED_TAGS:
            return
        self._out.append(f"<{tag}{self._safe_attrs(tag, attrs)}>")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _ALLOWED_TAGS and tag not in _VOID_TAGS:
            self._out.append(f"</{tag}>")

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        if tag not in _ALLOWED_TAGS:
            return
        self._out.append(f"<{tag}{self._safe_attrs(tag, attrs)} />")

    def handle_data(self, data):
        self._out.append(data)

    def handle_entityref(self, name):
        self._out.append(f"&{name};")

    def handle_charref(self, name):
        self._out.append(f"&#{name};")

    def handle_comment(self, data):
        pass

    def get_output(self) -> str:
        return "".join(self._out)


def sanitize_html(html_str: str) -> str:
    """Strip disallowed HTML tags and dangerous attributes."""
    sanitizer = _HTMLSanitizer()
    try:
        sanitizer.feed(html_str)
    except Exception:
        return _html_mod.escape(html_str)
    return sanitizer.get_output()


def render_markdown(body: str, topic_context: dict | None = None) -> str:
    """Convert markdown body to HTML with citation footnotes, wikilinks,
    and HTML sanitization."""
    body_before, related = _split_related_topics(body)
    body_before, citations = process_citations(body_before)
    body_before = convert_wikilinks(body_before)
    related = convert_wikilinks(related)
    md = get_md()
    article_html = md.convert(body_before + "\n\n" + related)
    sources_html = build_sources_html(citations, topic_context=topic_context)
    return sanitize_html(article_html + sources_html)


# ---------------------------------------------------------------------------
# Frontmatter / article parsing
# ---------------------------------------------------------------------------

def parse_frontmatter(content: str) -> tuple[dict, str]:
    """Parse YAML frontmatter, return (metadata, body)."""
    if not content.startswith("---"):
        return {}, content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content
    try:
        fm = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}, content
    if not isinstance(fm, dict):
        return {}, content
    return fm, parts[2].lstrip("\n")


def read_article(path: Path) -> dict:
    """Read and parse a wiki markdown file."""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"title": path.stem, "body": "", "path": path, "frontmatter": {}}
    fm, body = parse_frontmatter(content)
    return {
        "title": fm.get("title", path.stem),
        "body": body,
        "path": path,
        "frontmatter": fm,
        **{k: v for k, v in fm.items() if k not in ("title",)},
    }


# ---------------------------------------------------------------------------
# Date coercion
# ---------------------------------------------------------------------------

def coerce_date_str(value) -> str:
    """Normalize a frontmatter date field to a YYYY-MM-DD string."""
    if not value:
        return ""
    if isinstance(value, str):
        return value[:10] if len(value) >= 10 else value
    try:
        return value.strftime("%Y-%m-%d")
    except Exception:
        return str(value)[:10]


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------

def safe_resolve(article_path: str, root: Path | None = None) -> Path | None:
    """Resolve a user-supplied article path to a safe absolute path
    inside the root. Returns None if the path escapes."""
    if root is None:
        root = MERIDIAN_ROOT
    if not article_path or ".." in article_path or article_path.startswith("/"):
        return None
    filepath = (root / article_path).resolve()
    try:
        filepath.relative_to(root.resolve())
    except ValueError:
        return None
    if not filepath.exists():
        return None
    return filepath
