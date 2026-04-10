# Synthesis regression corpus

Frozen inputs for regression-testing the Layer 3 synthesis write prompt.
Lets you iterate on `prompts/synthesizer_write.md` without burning the
full 60-topic bulk re-run cycle every time.

## Layout

```
tests/synthesis_corpus/
├── fixtures/                 # frozen extraction JSONs (committed)
│   ├── website.json
│   ├── hubspot.json
│   ├── nonprofit.json
│   ├── legal.json
│   ├── paid-social.json
│   └── seo.json
├── baselines/
│   └── YYYY-MM-DD/           # known-good outputs for a prompt version
│       ├── website.md
│       └── …
├── latest/                   # scratch output from the most recent run
│                             # (gitignored — never committed)
└── README.md
```

## Corpus selection rationale

The six fixtures are picked to cover the full shape of the topic space:

| Fixture | Size | Domain type | Why it's in the corpus |
|---|---|---|---|
| `website` | 233 fragments | platform-tactics | Heaviest topic; stress-tests length discipline and cross-source synthesis |
| `paid-social` | 113 fragments | platform-tactics | Heavy + multi-platform; tests platform comparison structure |
| `hubspot` | 93 fragments | platform-mechanics | Tool-specific; tests vendor-specific detail + BANT/lifecycle framings |
| `seo` | 112 fragments | platform-tactics | Layered-reasoning topic; tests the "three binding constraints" pattern |
| `nonprofit` | 6 fragments | strategy | Small industry topic; tests the `Gaps` section on sparse evidence |
| `legal` | 9 fragments | regulatory | Small regulatory topic; tests the voice guardrails on compliance content |

Want to rotate the corpus? Copy a new `cache/extractions/<topic>.json`
from the VM into `fixtures/`, re-run the baseline, commit both.

## Running the harness

```bash
# On the VM (or anywhere with ANTHROPIC_API_KEY + the anthropic package)
bash scripts/test-synthesis.sh

# Outputs land in tests/synthesis_corpus/latest/
ls tests/synthesis_corpus/latest/
```

Each fixture is fed through `synthesizer.py write --fixture … --output …`,
which bypasses the production cache, skips the archive step, and never
touches `wiki/knowledge/`. Safe to run at any time.

## Comparing a run to a baseline

```bash
bash scripts/diff-synthesis.sh 2026-04-10
```

Prints a unified diff per topic between `latest/` and the baseline. Use
this before committing a prompt change — eyeball the diff and ask:

1. Are the changes intentional (e.g. new section, tighter voice)?
2. Did any section get worse (loss of specificity, vague generalization)?
3. Are banned words appearing where they weren't before?
4. Did citation format drift?
5. Is length still within discipline?

If the diff is clean, bump the baseline:

```bash
NEW_BASE=tests/synthesis_corpus/baselines/$(date -u +%Y-%m-%d)
mkdir -p "$NEW_BASE"
cp tests/synthesis_corpus/latest/*.md "$NEW_BASE/"
git add "$NEW_BASE"
```

## What this harness is NOT

- **Not a unit test.** LLM outputs are non-deterministic at temperature > 0.
  Treat diffs as signals for human review, not pass/fail assertions.
- **Not a CI gate.** Running six Sonnet calls costs a few cents and takes
  ~15 minutes. Run it manually before prompt changes, not on every push.
- **Not exhaustive.** Six topics is a sample. Watch for regressions on
  topics outside the corpus during the next full bulk run.
