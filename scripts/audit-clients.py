#!/usr/bin/env python3
"""Audit client folders — classify articles and assign industry/topic tags.

Usage:
    python scripts/audit-clients.py              # full audit
    python scripts/audit-clients.py --client doudlah-farms  # single client

Output: outputs/client-audit-v2.md
"""

import json
import os
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
WIKI_DIR = ROOT / "wiki"
OUTPUTS_DIR = ROOT / "outputs"


# ---------------------------------------------------------------------------
# Client → Industry mapping (from clients.yaml)
# ---------------------------------------------------------------------------

CLIENT_INDUSTRY = {
    "doudlah-farms": "food-beverage",
    "didion": "food-beverage",
    "bake-believe": "food-beverage",
    "citrus-america": "food-beverage",
    "adava-care": "healthcare",
    "ahs": "healthcare",
    "cordwainer": "senior-living",
    "skaalen": "senior-living",
    "aviary": "saas",
    "hazardos": "saas",
    "pema": "saas",
    "finwellu": "saas",
    "papertube": "ecommerce",
    "crazy-lennys": "ecommerce",
    "lamarie": "ecommerce",
    "bluepoint": "b2b-services",
    "american-extractions": "b2b-services",
    "trachte": "b2b-services",
    "exterior-renovations": "b2b-services",
    "overhead-door": "b2b-services",
    "reynolds": "b2b-services",
    "sonoplot": "b2b-services",
    "seamless": "b2b-services",
    "sbs": "b2b-services",
    "jbf": "b2b-services",
    "three-gaits": "b2b-services",
    "new-dawn-shine": "b2b-services",
    "avant-gardening": "b2b-services",
    "flynn-audio": "b2b-services",
    "hooper": "b2b-services",
    "ab-hooper": "b2b-services",
    "capitol-bank": "financial-services",
    "blue-sky": "financial-services",
    "global-coin": "financial-services",
    "vcedc": "nonprofit",
    "maple-bluff": "nonprofit",
    "wi-masons": "nonprofit",
    "axley": "legal-services",
    "quarra": "b2b-services",
    "agility-recovery": "elearning",
    "asymmetric": "b2b-services",
    "machinery-source": "b2b-services",
    "kerns-dumpsters": "b2b-services",
}


# ---------------------------------------------------------------------------
# Topic inference from filename patterns
# ---------------------------------------------------------------------------

FILENAME_TOPIC_PATTERNS = [
    # Amazon
    (r"amazon|fba|asin|seller-central|buy-box", "amazon-strategy"),
    # Google Ads / PPC
    (r"google-ads|ppc|cpc|bid-strat|ad-review|campaign-budget|quality-score|search-ads|max-conv|pmax", "google-ads"),
    # SEO
    (r"\bseo\b|organic-traffic|domain-rating|backlink|keyword-research|site-health|ahrefs|search-console", "seo"),
    (r"local-seo|google-business|gbp-|google-maps|local-rank", "local-seo"),
    (r"technical-seo|crawl|robots-txt|sitemap|page-speed|core-web", "technical-seo"),
    # Paid Social
    (r"meta-ads|facebook-ads|instagram-ads|linkedin-ads|tiktok|paid-social|social-ad|boosted", "paid-social"),
    # Email
    (r"email-market|newsletter|mailchimp|klaviyo|drip|email-campaign|email-list|sendgrid", "email-marketing"),
    # Content
    (r"content-strat|blog-content|content-calendar|editorial|copywriting|content-plan", "content-marketing"),
    # CRM / HubSpot / Salesforce
    (r"hubspot|hs-|crm-hub", "hubspot"),
    (r"salesforce|sf-|apex|agentforce|opportunity-stage|permission-set", "salesforce"),
    (r"\bcrm\b|crm-|contact-management|pipeline-management", "crm"),
    (r"gohighlevel|ghl|highlevel", "gohighlevel"),
    # Website
    (r"website-|web-design|landing-page|site-launch|redesign|ux-|ui-|page-layout", "website"),
    (r"wordpress|wp-|plugin-|divi|beaver-builder|wp-engine", "wordpress"),
    (r"webflow", "webflow"),
    (r"shopify", "shopify"),
    (r"woocommerce|woo-", "woocommerce"),
    # Ecommerce
    (r"ecommerce|e-commerce|inventory|fulfillment|b2b-site|wholesale|retail-expan|dtc-|marketplace", "ecommerce-strategy"),
    # Analytics
    (r"analytics|ga4|google-analytics|microsoft-clarity|tag-manager|gtm|tracking-audit", "web-analytics"),
    (r"callrail|call-track|call-attribution|phone-track", "call-tracking"),
    (r"attribution|conversion-track|roi-calc", "attribution"),
    # Sales
    (r"outbound|cold-email|prospecting|linkedin-outreach|sales-outreach|lead-list", "outbound-sales"),
    (r"lead-gen|lead-magnet|lead-nurture|lead-qualify", "lead-generation"),
    (r"sales-pitch|sales-process|objection|proposal-|pitch-deck|value-prop", "sales-methodology"),
    (r"sales-enablement|sales-collateral|sales-asset|sales-tool", "sales-enablement"),
    # Branding / Design
    (r"brand-|branding|logo|brand-guide|identity", "branding"),
    (r"design-|graphic-|print-|signage|creative-", "design"),
    # Video
    (r"video-|youtube|video-market|video-seo", "video-marketing"),
    # Integrations
    (r"zapier|integration-|api-connect|webhook|stripe-|payment-", "integrations"),
    (r"dns-|domain-|cloudflare|registrar|nameserver", "dns-domains"),
    # AI
    (r"ai-tool|ai-strat|ai-opportun|ai-evaluat|chatbot|voice-ai", "ai-tools"),
    # Agency ops
    (r"retainer|pricing|scope-|deliverable|contract|invoice|billing", "agency-operations"),
    (r"client-health|client-retention|client-risk|disengag|churn|upsell", "client-management"),
    (r"project-manage|clickup|sprint|task-manage|handoff", "project-management"),
    (r"reporting|dashboard|monthly-report|performance-report", "reporting"),
    # Team
    (r"hiring|recruit|interview|onboard|team-struct", "team-operations"),
    # Compliance
    (r"hipaa|compliance|fda|regulatory|privacy", "regulatory-compliance"),
    (r"legal|trademark|contract-law|llc", "legal"),
    # Industry specific
    (r"senior-living|memory-care|assisted-living|eldermark", "senior-living"),
    (r"food-label|fda-food|usda|organic-cert|grocery-retail|natural-grocers", "food-beverage"),
    (r"elearning|course-develop|rise-360|articulate|lms|soar-course", "elearning"),
    # Programmatic
    (r"podcast-ad|audio-ad|ooh-|billboard|geofenc|programmatic", "programmatic"),
    # Marketing strategy
    (r"marketing-strat|go-to-market|competitive-analys|market-research|buyer-persona", "marketing-strategy"),
    (r"brand-strat|positioning|differentiat", "brand-strategy"),
]


def infer_topic(filename: str, title: str, tags: list, client_industry: str) -> str:
    """Infer canonical topic from filename, title, tags, and industry context."""
    combined = f"{filename} {title}".lower()

    # Check filename patterns
    for pattern, topic in FILENAME_TOPIC_PATTERNS:
        if re.search(pattern, combined):
            return topic

    # Check tags against topics.yaml
    topics_path = ROOT / "topics.yaml"
    if topics_path.exists():
        with open(topics_path) as f:
            data = yaml.safe_load(f) or {}
        alias_map = {}
        for item in data.get("categories", []):
            slug = item.get("slug", "")
            alias_map[slug] = slug
            for alias in item.get("aliases", []):
                alias_map[alias.lower()] = slug

        for tag in tags:
            if tag.lower() in alias_map:
                return alias_map[tag.lower()]

    # Industry-based fallback
    industry_topic_map = {
        "food-beverage": "food-beverage",
        "senior-living": "senior-living",
        "ecommerce": "ecommerce-strategy",
        "saas": "saas",
        "elearning": "elearning",
        "nonprofit": "nonprofit",
        "legal-services": "legal",
    }
    if client_industry in industry_topic_map:
        return industry_topic_map[client_industry]

    return ""


def classify_article(title: str, filename: str, words: int) -> str:
    """Classify article as operational/learning."""
    title_lower = title.lower()
    fname_lower = filename.lower()

    operational_signals = [
        "marketing-call", "weekly-call", "monthly-call", "stand-up",
        "status-update", "deliverable", "timeline", "invoice",
        "retainer-status", "contract", "onboarding-plan", "offboarding",
        "campaign-setup", "account-setup", "access-request", "login",
        "sprint-planning", "task-list", "handoff", "check-in",
        "connect", "sync", "catch-up", "impromptu",
    ]
    for sig in operational_signals:
        if sig in fname_lower or sig in title_lower:
            return "operational"

    if words < 250:
        return "operational"

    return "learning"


def detect_industry_content(title: str, filename: str, body_preview: str) -> bool:
    """Detect if article contains industry-specific knowledge."""
    combined = f"{title} {filename} {body_preview}".lower()

    industry_signals = [
        # Food & beverage
        r"food distribution|grocery retail|natural grocers|organic certification|fda label|usda|cpg retail|food service sales|wholesale food",
        # Senior living
        r"senior living admissions|memory care|assisted living marketing|eldermark|census|resident|move-in",
        # SaaS
        r"saas sales cycle|trial conversion|churn rate|arr |mrr |feature adoption|user onboarding|product-led",
        # Ecommerce
        r"shopping cart|checkout flow|product listing|fulfillment|inventory management|return rate|aov |average order",
        # B2B
        r"sales cycle|rfp|procurement|decision maker|buying committee|enterprise sale",
        # Financial
        r"banking regulation|compliance requirement|fiduciary|loan product|deposit",
        # Healthcare
        r"hipaa|patient|medical device|healthcare compliance|ehr |emr ",
        # Legal
        r"legal marketing|attorney advertising|bar association|practice area",
        # Industry dynamics
        r"industry trend|market dynamic|competitive landscape|buyer behavior|regulatory environment|seasonal pattern|industry benchmark",
    ]

    for pattern in industry_signals:
        if re.search(pattern, combined):
            return True

    return False


def audit_all_clients(target_client: str = None):
    """Audit all client folders with improved classification."""
    results = []

    for status_dir in ["current", "former", "prospects"]:
        base = WIKI_DIR / "clients" / status_dir
        if not base.exists():
            continue

        for client_dir in sorted(base.iterdir()):
            if not client_dir.is_dir():
                continue
            client = client_dir.name

            if target_client and client != target_client:
                continue

            client_industry = CLIENT_INDUSTRY.get(client, "")

            for f in sorted(client_dir.glob("*.md")):
                if f.name == "_index.md":
                    continue

                try:
                    content = f.read_text(encoding="utf-8", errors="replace")
                    fm = {}
                    body = content
                    if content.startswith("---"):
                        parts = content.split("---", 2)
                        if len(parts) >= 3:
                            try:
                                fm = yaml.safe_load(parts[1]) or {}
                            except yaml.YAMLError:
                                pass
                            body = parts[2]

                    title = fm.get("title", f.stem)
                    tags = fm.get("tags", [])
                    words = len(content.split())

                    # Primary classification
                    classification = classify_article(title, f.name, words)

                    # Industry detection (can be BOTH learning AND industry)
                    has_industry = detect_industry_content(title, f.name, body[:2000])
                    if not has_industry and client_industry:
                        # If client is in a specific industry, tag it
                        has_industry = True

                    # Topic inference
                    topic = infer_topic(f.name, title, tags, client_industry)

                    results.append({
                        "status": status_dir,
                        "client": client,
                        "filename": f.name,
                        "title": title,
                        "words": words,
                        "classification": classification,
                        "industry_tag": client_industry if has_industry else "",
                        "suggested_topic": topic,
                        "tags": tags[:5],
                    })
                except Exception as e:
                    print(f"Error reading {f}: {e}", file=sys.stderr)

    return results


def write_audit_report(results: list):
    """Write the v2 audit report."""
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = OUTPUTS_DIR / "client-audit-v2.md"

    # Summary stats
    by_class = {}
    by_industry = {}
    by_client = {}
    untagged = 0
    total_industry = 0

    for r in results:
        c = r["classification"]
        by_class[c] = by_class.get(c, 0) + 1

        ind = r["industry_tag"]
        if ind:
            by_industry[ind] = by_industry.get(ind, 0) + 1
            total_industry += 1

        if not r["suggested_topic"]:
            untagged += 1

        client = r["client"]
        if client not in by_client:
            by_client[client] = {"operational": 0, "learning": 0, "industry_tagged": 0}
        by_client[client][c] = by_client[client].get(c, 0) + 1
        if ind:
            by_client[client]["industry_tagged"] += 1

    lines = [
        "# Client Folder Audit v2",
        "",
        f"**Total articles:** {len(results)}",
        f"**With industry tag:** {total_industry}",
        f"**Without suggested topic:** {untagged}",
        "",
        "## Classification Summary",
        "",
        "| Classification | Count | % |",
        "|---|---|---|",
    ]
    for cls in ["operational", "learning"]:
        count = by_class.get(cls, 0)
        pct = round(count / len(results) * 100, 1) if results else 0
        lines.append(f"| {cls} | {count} | {pct}% |")

    lines.extend(["", "## Industry Distribution", "",
                   "| Industry | Articles |", "|---|---|"])
    for ind in sorted(by_industry.keys()):
        lines.append(f"| {ind} | {by_industry[ind]} |")

    lines.extend(["", "## By Client", "",
                   "| Client | Operational | Learning | Industry Tagged | Total |",
                   "|---|---|---|---|---|"])
    for client in sorted(by_client.keys()):
        d = by_client[client]
        total = d.get("operational", 0) + d.get("learning", 0)
        lines.append(f"| {client} | {d.get('operational',0)} | {d.get('learning',0)} | {d.get('industry_tagged',0)} | {total} |")

    lines.extend(["", "## Untagged Articles (no suggested topic)", ""])
    for r in results:
        if not r["suggested_topic"]:
            lines.append(f"- {r['client']}/{r['filename']}")

    lines.extend(["", "## Full Article List", "",
                   "| Client | Article | Class | Words | Industry | Topic |",
                   "|---|---|---|---|---|---|"])
    for r in results:
        lines.append(f"| {r['client']} | {r['filename']} | {r['classification']} | {r['words']} | {r['industry_tag']} | {r['suggested_topic']} |")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Audit v2 written to {report_path}", file=sys.stderr)
    return report_path


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Audit client folders v2")
    parser.add_argument("--client", help="Audit a single client")
    args = parser.parse_args()

    results = audit_all_clients(args.client)
    report = write_audit_report(results)

    # Stats
    by_class = {}
    industry_count = 0
    untagged = 0
    for r in results:
        by_class[r["classification"]] = by_class.get(r["classification"], 0) + 1
        if r["industry_tag"]:
            industry_count += 1
        if not r["suggested_topic"]:
            untagged += 1

    output = {
        "status": "ok",
        "total_articles": len(results),
        "classification": by_class,
        "industry_tagged": industry_count,
        "untagged_topics": untagged,
        "report": str(report),
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
