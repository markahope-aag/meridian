# Synthesis evaluation rubric

Criteria for grading a Layer 3 synthesis article produced by the
Meridian write pass. Each criterion is scored 0–10. The grader (see
`scripts/grade-synthesis.py`) passes this rubric verbatim to Sonnet
along with the article under review and asks for structured JSON scores
plus one-sentence justifications.

This rubric intentionally mirrors the criteria that drove the prompt
update — so grades map directly to prompt changes rather than vague
"quality" judgments. Add, remove, or re-weight criteria as the synthesis
matures. Do not edit the criteria IDs; they are used as JSON keys.

---

## Scoring scale

| Score | Meaning |
|-------|---------|
| 9–10  | Excellent. Matches or exceeds the ideal described below. |
| 7–8   | Good. Meets the bar; minor lapses only. |
| 5–6   | Acceptable. Multiple lapses or one structural weakness. |
| 3–4   | Below bar. Frequent lapses OR a systematic failure. |
| 0–2   | Unacceptable. The criterion is effectively unmet. |

Score generously on criteria where the prompt is silent, strictly on
criteria the prompt explicitly mandates.

---

## Criteria

### summary_quality
Is the `## Summary` section an effective 30-second TL;DR?

Ideal:
- 3–5 sentences
- Opens with a declarative thesis
- Names specific platforms, numbers, clients, or outcomes
- No hedging, no "it depends", no jargon introduced unexplained
- Contains no inline citations (citations belong in the body)
- A colleague could read it in a client call without scrolling

Zero if:
- Summary section is missing entirely
- Summary reads like a list of topics rather than a thesis
- Summary contains citations or wikilinks

### editorial_voice
Does the article sound like a single opinionated analyst wrote it?

Ideal:
- Declarative, not surveying ("X is the binding constraint" not
  "several sources suggest X may be important")
- Names the specific failure mode, specific pattern, specific winner
- Takes positions the evidence supports
- Transitions between sections feel argumentative, not stitched

Zero if:
- The piece is a list of observations with no stance
- Hedging phrases dominate the prose ("it could be argued", "in
  general", "broadly speaking")

### banned_word_discipline
Does the article avoid the banned vocabulary defined in
`prompts/synthesizer_write.md`?

Banned:
- Marketing adjectives: robust, powerful, comprehensive, seamless*,
  cutting-edge, state-of-the-art, world-class, leverage*, synergy
- Hedge-fillers: it is important to note, it should be noted, in
  general, broadly speaking, essentially, fundamentally
- Transitional filler: in conclusion, going forward, at the end of
  the day, moving forward
- Empty framing: various, several, many, a number of, a variety of

*Asterisked words may appear legitimately as proper nouns (e.g.
"Seamless Building Systems" client name) or as established business
terms ("highest-leverage fix"). Do not penalize those usages.

Score 10 if there are no violations at all. Subtract one point for
each clear violation, or two points for each repeated violation.

### specificity_density
How often do generalizations pair with specific evidence?

Ideal:
- Every generalization pairs with at least one of: a named client, a
  named number, a named tool, a named time window
- Sweeping claims are either supported or explicitly qualified
  ("in one observed case, ...")
- The reader could cite specific examples after reading

Zero if:
- The article reads like generic advice with no hooks to reality

### citation_format_consistency
Are inline citations in the standard format?

Standard: `[[wiki/knowledge/<topic>/<file>.md]]` (full path from repo
root). Multiple sources: comma-separated within the same brackets.

Score 10 if every citation uses the full path. Subtract one point for
each bare filename (`[[file.md]]`) or broken link format.

### confidence_gradation
Does the article calibrate trust across different claims?

Ideal:
- Uses language like "consistent across clients", "observed at
  multiple clients", "single engagement", "single-source finding",
  or "plausible but unverified"
- A reader can tell strong claims from weaker ones without leaving
  the section
- Does not over-claim on sparse evidence

Score 10 if calibration language is used whenever a claim is based on
limited evidence. Score lower if every claim reads with the same
certainty regardless of source count.

### gaps_quality
Does the `## Gaps in Our Understanding` section name real gaps?

Ideal:
- 3–8 specific, named gaps
- Each gap explains what we don't have, why it matters, and what
  decision would change if we had it
- Gaps are actionable — a reader could plan to close them

Zero if:
- Section is missing
- Section is vague ("we need more data")
- Section lists topics we already know about rather than things we
  don't

### structural_discipline
Does the article follow the required section order and hierarchy?

Required sections, in order:
1. Summary
2. Current Understanding (with 3–5 named ### subsections)
3. What Works
4. What Doesn't Work
5. Patterns Across Clients
6. Exceptions and Edge Cases
7. Evolution and Change
8. Gaps in Our Understanding
9. Open Questions
10. Related Topics
11. Sources

Score 10 for perfect adherence. Subtract one per missing section, two
per section out of order.

### length_discipline
Is the article within the length bounds for its fragment count?

- Short topics (< 20 fragments): 1000–1500 words total
- Typical topics (20–80 fragments): 1500–2200 words total
- Heavy topics (80+ fragments): 2200–3000 words total

Score 10 if within bounds. Subtract one for every 200 words over the
upper bound or under the lower bound.

### overall_readability
Would you actually use this article in a client engagement?

Ideal:
- Scannable for the hurried reader, rich for the careful reader
- Transitions feel coherent, not stitched
- Avoids filler, avoids noise
- Leaves you with a mental model, not just facts

This is the one subjective judgment. Score based on whether a
colleague who had never seen this topic would leave more informed and
more decisive after reading.
