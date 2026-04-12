"""Meridian dashboard — YAML registry loaders and taxonomy helpers.

Loads canonical slugs and display names from the five registry files
(clients.yaml, topics.yaml, engineering-topics.yaml, interests-topics.yaml,
industries.yaml). Registry data is loaded once at import time and
cached in module-level globals.
"""

from __future__ import annotations

import yaml
from web.config import (
    CLIENTS_YAML, TOPICS_YAML, ENGINEERING_TOPICS_YAML,
    PROJECTS_YAML, INTERESTS_TOPICS_YAML,
)


def _load_client_names() -> dict:
    if not CLIENTS_YAML.exists():
        return {}
    try:
        data = yaml.safe_load(CLIENTS_YAML.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {}
    lookup: dict[str, str] = {}
    for entry in data.get("clients", []):
        slug = (entry.get("slug") or "").strip()
        name = (entry.get("name") or "").strip()
        if slug and name:
            lookup[slug] = name
    return lookup


def _load_topic_names() -> dict:
    if not TOPICS_YAML.exists():
        return {}
    try:
        data = yaml.safe_load(TOPICS_YAML.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {}
    lookup: dict[str, str] = {}
    entries = data.get("categories") or data.get("topics") or []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        slug = (entry.get("slug") or "").strip()
        name = (entry.get("name") or "").strip()
        if slug and name:
            lookup[slug] = name
    return lookup


def _load_engineering_topic_names() -> dict:
    if not ENGINEERING_TOPICS_YAML.exists():
        return {}
    try:
        data = yaml.safe_load(ENGINEERING_TOPICS_YAML.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {}
    lookup: dict[str, str] = {}
    for entry in data.get("topics", []):
        slug = (entry.get("slug") or "").strip()
        name = (entry.get("name") or "").strip()
        if slug and name:
            lookup[slug] = name
    return lookup


def _load_interests_topic_names() -> dict:
    if not INTERESTS_TOPICS_YAML.exists():
        return {}
    try:
        data = yaml.safe_load(INTERESTS_TOPICS_YAML.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return {}
    lookup: dict[str, str] = {}
    for entry in data.get("topics", []):
        slug = (entry.get("slug") or "").strip()
        name = (entry.get("name") or "").strip()
        if slug and name:
            lookup[slug] = name
    return lookup


def _load_projects() -> list[dict]:
    if not PROJECTS_YAML.exists():
        return []
    try:
        data = yaml.safe_load(PROJECTS_YAML.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return []
    return data.get("projects", [])


def _non_synthesizable_topic_slugs() -> set:
    result: set = set()
    sources = [
        ("engineering", ENGINEERING_TOPICS_YAML, "topics"),
        ("interests",   INTERESTS_TOPICS_YAML,   "topics"),
    ]
    for ns, path, key in sources:
        if not path.exists():
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        for entry in data.get(key, []):
            if isinstance(entry, dict) and entry.get("synthesize") is False:
                slug = (entry.get("slug") or "").strip()
                if slug:
                    result.add((ns, slug))
    return result


def client_display_name(slug_or_name: str) -> str:
    if not slug_or_name:
        return ""
    key = slug_or_name.strip().lower()
    return CLIENT_NAMES.get(key, slug_or_name)


# Module-level globals — loaded once at import
CLIENT_NAMES: dict = _load_client_names()
TOPIC_NAMES: dict = _load_topic_names()
ENGINEERING_TOPIC_NAMES: dict = _load_engineering_topic_names()
INTERESTS_TOPIC_NAMES: dict = _load_interests_topic_names()
PROJECTS: list = _load_projects()
