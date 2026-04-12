"""Meridian dashboard — shared path constants and configuration.

Every module in web/ imports from here instead of computing paths
independently. Changing MERIDIAN_ROOT in one place changes it
everywhere.
"""

import os
from pathlib import Path

MERIDIAN_ROOT = Path(os.environ.get("MERIDIAN_ROOT", "/meridian"))
WIKI_DIR = MERIDIAN_ROOT / "wiki"
RAW_DIR = MERIDIAN_ROOT / "raw"
CAPTURE_DIR = MERIDIAN_ROOT / "capture"
REPORTS_DIR = MERIDIAN_ROOT / "reports"
ENGINEERING_DIR = WIKI_DIR / "engineering"
INTERESTS_DIR = WIKI_DIR / "interests"
LAYER4_DIR = WIKI_DIR / "layer4"
COMMITS_CAPTURE_DIR = CAPTURE_DIR / "external" / "commits"
INTERESTS_CAPTURE_DIR = CAPTURE_DIR / "external" / "interests"

CLIENTS_YAML = MERIDIAN_ROOT / "clients.yaml"
TOPICS_YAML = MERIDIAN_ROOT / "topics.yaml"
ENGINEERING_TOPICS_YAML = MERIDIAN_ROOT / "engineering-topics.yaml"
PROJECTS_YAML = MERIDIAN_ROOT / "projects.yaml"
INTERESTS_TOPICS_YAML = MERIDIAN_ROOT / "interests-topics.yaml"

RECEIVER_URL = os.environ.get("MERIDIAN_RECEIVER_URL", "http://localhost:8000")
RECEIVER_TOKEN = os.environ.get("MERIDIAN_RECEIVER_TOKEN", "")
