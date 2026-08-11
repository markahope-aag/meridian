"""Tests for commit-fragment frontmatter encoding and parsing.

A commit whose subject contained a Windows path produced
`title: "... C:\\Users\\markh"`. YAML reads `\\U` as the start of an
eight-digit unicode escape, so the fragment failed to parse. The
classifier returned None for it, which meant the fragment stayed in
capture/ forever, counted in the queue and re-reported as an error on
every run.

Two defenses are covered here: the generator escapes backslashes so new
fragments are valid, and the classifier falls back to a line-by-line
read so an already-malformed fragment can still be processed.

Run with: python -m pytest tests/test_fragment_encoding.py -v
"""

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

import pytest
import yaml

_TMPDIR = tempfile.mkdtemp()
os.environ["MERIDIAN_ROOT"] = _TMPDIR

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name: str, filename: str):
    """Import a hyphenated script by path."""
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ingest = _load("ingest_git_history", "ingest-git-history.py")

# The classifier imports anthropic, which is not needed to exercise the
# parsing helpers but must be installed for the import to succeed.
anthropic = pytest.importorskip(
    "anthropic", reason="classifier import needs the anthropic package"
)
classify = _load("classify_engineering_fragments", "classify-engineering-fragments.py")


WINDOWS_PATH_SUBJECT = (
    r"use %USERPROFILE%-relative default root instead of hardcoded C:\Users\markh"
)


class TestYamlDoubleQuoted:
    def test_backslash_subject_round_trips(self):
        encoded = f'title: "{ingest.yaml_double_quoted(WINDOWS_PATH_SUBJECT)}"'
        assert yaml.safe_load(encoded)["title"] == WINDOWS_PATH_SUBJECT

    def test_unescaped_backslash_is_what_broke(self):
        """Guard the regression itself, so the test means something."""
        with pytest.raises(yaml.YAMLError):
            yaml.safe_load(f'title: "{WINDOWS_PATH_SUBJECT}"')

    def test_quotes_still_escaped(self):
        subject = 'fix the "quoted" thing'
        encoded = f'title: "{ingest.yaml_double_quoted(subject)}"'
        assert yaml.safe_load(encoded)["title"] == subject

    def test_backslash_and_quote_together(self):
        subject = r'copy "C:\tmp\x" to safety'
        encoded = f'title: "{ingest.yaml_double_quoted(subject)}"'
        assert yaml.safe_load(encoded)["title"] == subject

    def test_plain_subject_unchanged(self):
        assert ingest.yaml_double_quoted("fix: a normal commit") == "fix: a normal commit"


class TestLooseFrontmatter:
    def test_recovers_fields_from_invalid_yaml(self):
        fm = classify.loose_frontmatter(
            f'title: "{WINDOWS_PATH_SUBJECT}"\n'
            "source_project: workspace\n"
            "commit_short_sha: ebb4fec\n"
        )
        assert fm["title"] == WINDOWS_PATH_SUBJECT
        assert fm["source_project"] == "workspace"
        assert fm["commit_short_sha"] == "ebb4fec"

    def test_ignores_lines_without_a_colon(self):
        fm = classify.loose_frontmatter("just some text\nkey: value\n")
        assert fm == {"key": "value"}

    def test_unwraps_escaped_quotes(self):
        fm = classify.loose_frontmatter(r'title: "say \"hi\""')
        assert fm["title"] == 'say "hi"'


class TestParseFragmentFallback:
    def _write(self, frontmatter: str) -> Path:
        path = Path(_TMPDIR) / "fragment.md"
        path.write_text(
            "---\n" + frontmatter + "---\n"
            "## Commit message\n\n```\nsubject line\n```\n",
            encoding="utf-8",
        )
        return path

    def test_malformed_fragment_is_still_parsed(self):
        path = self._write(
            f'title: "{WINDOWS_PATH_SUBJECT}"\n'
            "source_project: workspace\n"
            "commit_sha: ebb4fec1234\n"
            "commit_short_sha: ebb4fec\n"
        )
        frag = classify.parse_fragment(path)
        assert frag is not None
        assert frag.project == "workspace"
        assert frag.short_sha == "ebb4fec"
        assert frag.subject == WINDOWS_PATH_SUBJECT

    def test_well_formed_fragment_still_uses_yaml(self):
        encoded = ingest.yaml_double_quoted(WINDOWS_PATH_SUBJECT)
        path = self._write(
            f'title: "{encoded}"\n'
            "source_project: workspace\n"
            "commit_short_sha: ebb4fec\n"
        )
        frag = classify.parse_fragment(path)
        assert frag is not None
        assert frag.subject == WINDOWS_PATH_SUBJECT

    def test_non_frontmatter_file_still_rejected(self):
        path = Path(_TMPDIR) / "plain.md"
        path.write_text("no frontmatter here\n", encoding="utf-8")
        assert classify.parse_fragment(path) is None
