# Meridian Migration Readiness Report

**Generated:** 2026-04-07
**Branch:** april-2026-rebuild
**Purpose:** Assessment before migrating to four-layer knowledge architecture

---

## Current Article Inventory

| Directory | Count |
|---|---|
| `wiki/articles/` | 310 |
| `wiki/concepts/` | 42 |
| `wiki/knowledge/` (across 67 topics) | 2,508 |
| `wiki/clients/current/` (40 clients) | 996 |
| `wiki/clients/former/` (5 clients) | 9 |
| `wiki/clients/prospects/` (2 clients) | 2 |
| `wiki/synthesis/` | 0 |
| `wiki/dev/` | 0 |
| `wiki/layer4/` | 0 |
| **Total wiki files** | **3,899** |

## Quality Distribution

| Quality | Count | % |
|---|---|---|
| Thin (<200 words, likely stubs) | 30 | 0.8% |
| Moderate (200-500 words) | 922 | 24.1% |
| Good (500+ words) | 2,876 | 75.1% |
| **Total assessed** | **3,828** | |

**Assessment:** The wiki is overwhelmingly substantive — 75% of articles are 500+ words.
Only 30 articles are thin stubs. The compiler's Sonnet writing pass is producing quality content.

## Raw Source Coverage

| Metric | Count |
|---|---|
| Total raw docs | 714 |
| Compiled | 696 |
| Uncompiled | 17 |
| No compiled_at field | 1 |

### Source Types

| Type | Count |
|---|---|
| Meeting (Fathom) | 694 |
| Google Drive | 11 |
| Note | 4 |
| Meeting transcript (variant) | 3 |
| Article | 1 |

**Assessment:** 97% of source material is Fathom meeting transcripts. The knowledge base
is heavily weighted toward what was discussed in meetings. Web articles, documents, and
other sources are underrepresented. This means:
- Client knowledge is strong (meetings are client-centric)
- General/industry knowledge is thin (few external sources ingested)
- The four-layer architecture will need external source ingestion to build robust Layer 3

## Top 20 Knowledge Topics by Article Count

| Rank | Topic | Articles |
|---|---|---|
| 1 | website | 231 |
| 2 | google-ads | 153 |
| 3 | paid-social | 113 |
| 4 | email-marketing | 112 |
| 5 | seo | 111 |
| 6 | content-marketing | 106 |
| 7 | hubspot | 92 |
| 8 | outbound-sales | 80 |
| 9 | salesforce | 77 |
| 10 | ecommerce-strategy | 70 |
| 11 | amazon-strategy | 65 |
| 12 | project-management | 62 |
| 13 | lead-generation | 61 |
| 14 | design | 60 |
| 15 | ai-tools | 60 |
| 16 | crm-automation | 57 |
| 17 | elearning | 47 |
| 18 | wordpress | 45 |
| 19 | sales-enablement | 45 |
| 20 | local-seo | 45 |

**Synthesis priority:** These 20 topics have the most raw material and should be
synthesized into Layer 3 articles first. Website (231), Google Ads (153), and
Paid Social (113) have the most fragments to work with.

## Top 10 Client Folders by Article Count

| Rank | Client | Articles |
|---|---|---|
| 1 | Doudlah Farms | 112 |
| 2 | BluepointATM | 90 |
| 3 | Quarra Stone | 50 |
| 4 | Citrus America | 48 |
| 5 | Paper Tube Co | 44 |
| 6 | Adava Care | 44 |
| 7 | AviaryAI | 43 |
| 8 | Crazy Lenny's | 40 |
| 9 | Didion | 36 |
| 10 | The Cordwainer | 36 |

**Reframe priority:** These 10 clients have the most meeting history. Doudlah Farms (112)
and BluepointATM (90) should be reframed first — they have enough material to produce
rich client knowledge pages with proper Layer 2 tagging.

## Estimated Migration Work

### Layer 2 tagging
- ~3,500 existing articles need `layer: 2` frontmatter added
- ~1,000 client articles need `client_source` and `industry_context` filled
- Can be done programmatically by scanning existing file paths and content

### Layer 3 synthesis
- 67 topics need synthesis articles created from their fragments
- Top 20 topics have 50-230 fragments each — substantial synthesis
- Bottom 47 topics have 2-40 fragments — lighter synthesis
- Estimated: 67 synthesis articles to write

### Layer 4 detection
- Cannot be done until Layer 3 exists
- Expected: 10-20 initial patterns from cross-topic analysis

## Current Issues to Fix Before Migration

1. **17 uncompiled raw docs** — should be compiled before migration
2. **Obsidian performance** — vault too large for Obsidian at 3,899 files;
   new front-end (web UI) should be the priority for browsing
3. **No external sources** — 97% meeting transcripts; need web article
   ingestion pipeline to build robust Layer 3 knowledge
4. **wiki/_index.md is minimal** — was reset during recompile; needs
   rebuilding after migration
5. **wiki/log.md** — accumulated entries from old runs; should be
   cleaned/archived before migration

## Recommended Migration Sequence

1. **Phase 1 — Foundation** (this step, done)
   - Inventory complete
   - New frontmatter schema documented
   - Domain stability profiles added
   - New directories created

2. **Phase 2 — Layer 2 tagging**
   - Programmatically add `layer: 2` to all existing knowledge/client articles
   - Infer `client_source` from file path
   - Infer `industry_context` from client registry
   - Mark `transferable: true/false` based on filing location

3. **Phase 3 — Layer 3 synthesis**
   - Start with top 20 topics
   - Build synthesis agent that reads all fragments for a topic
   - Produces one authoritative article with proper Layer 3 frontmatter
   - Runs 5 topics per day (synthesis rate limit)

4. **Phase 4 — Layer 4 detection**
   - Cross-topic pattern analysis
   - Knowledge drift detection
   - Contradiction flagging

5. **Phase 5 — Client reframing**
   - Add industry context to client folders
   - Cross-link client articles to Layer 3 knowledge
   - Build client → knowledge → pattern chains

6. **Phase 6 — New front-end**
   - Web UI replacing Obsidian for browsing
   - Dashboard, search, Q&A, pipeline status
   - Client and topic views with quality metrics

---

**Status:** Ready for Phase 2. No blocking issues.
All existing systems continue operating throughout migration.
