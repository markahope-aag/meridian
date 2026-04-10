---
title: "Legal"
layer: 3
domain_type: regulatory
current_status: current
confidence: high
evidence_count: 9
supporting_sources:
  - wiki/knowledge/legal/cfaa-trade-secrets-litigation.md
  - wiki/knowledge/legal/damages-calculation-methodology.md
  - wiki/knowledge/legal/tortious-interference-business-relationships.md
  - wiki/knowledge/legal/asymmetric-llc-capital-contributions.md
  - wiki/knowledge/legal/asymmetric-member-termination-process.md
  - wiki/knowledge/legal/settlement-leverage-criminal-referral.md
  - wiki/knowledge/legal/product-description-copyright-compliance.md
  - wiki/knowledge/legal/egan-tro-resolution.md
  - wiki/knowledge/legal/bluepoint-trademark-registration.md
  - wiki/knowledge/legal/client-extractions.md
contradicting_sources: []
first_seen: "2026-02-26"
last_updated: "2026-04-08"
hypothesis: false
rate_of_change: slow
web_monitoring_frequency: quarterly
fragment_count: 11
tags: []
generated_at: "2026-04-09T20:40:16Z"
run_id: "9cbc142ba21c"
synthesizer_prompt_sha: "dd840e975612"
extract_prompt_sha: "b7ab5ab500eb"
writer_model: "claude-sonnet-4-6"
extract_model: "claude-haiku-4-5-20251001"
extraction_cache_hit: false
---

## Summary

The dominant legal pattern across the portfolio is layered federal-and-state claim stacking: when a former employee or partner accesses digital accounts without authorization, CFAA, DTSA, and tortious interference claims activate simultaneously, foreclosing different defense arguments and compounding damages exposure. Establishing liability in these cases is straightforward; the real fight is always over damages causation and duration, so damages methodology must be built before litigation begins. For operational matters outside litigation — product description copyright and trademark registration — the rules are clear and the risk of non-compliance is avoidable with basic process discipline.

## Current Understanding

The portfolio's legal activity clusters around three distinct problem types: adversarial digital account access (Asymmetric/Egan matter), LLC governance and ownership structure (Asymmetric), and intellectual property compliance (Flynn Audio, BluePoint ATM). Each has its own legal framework, but the Asymmetric matters dominate both in complexity and in the depth of documented strategy.

### Unauthorized Digital Account Access: Compounding Federal and State Liability

Unauthorized access to a company's digital accounts — Google Ads, CRM, or equivalent — simultaneously triggers liability under three distinct legal theories: the Computer Fraud and Abuse Act (CFAA), the Defend Trade Secrets Act (DTSA), and tortious interference with business relationships [[wiki/knowledge/legal/cfaa-trade-secrets-litigation.md, wiki/knowledge/legal/tortious-interference-business-relationships.md, wiki/knowledge/legal/index.md]]. This is not redundancy — each claim forecloses a different defense. CFAA addresses the unauthorized access itself. DTSA addresses what was taken or used. Tortious interference addresses the downstream client damage. A defendant who defeats one claim still faces the others.

The trade secret in digital account management is not the raw data — client names and spend figures are not protectable — but the proprietary methodology: account configuration, bid structuring, campaign organization [[wiki/knowledge/legal/cfaa-trade-secrets-litigation.md, wiki/knowledge/legal/index.md]]. This distinction matters because defendants predictably argue that the information accessed was not secret. The correct response is that the secret is the architecture, not the data points.

In the Egan matter, a TRO was granted on 2026-02-26 requiring Egan to restore full admin access to the Get Found Madison Google Ads account by end of day; Egan's attorney offered no defense at the hearing [[wiki/knowledge/legal/egan-tro-resolution.md]]. The speed and completeness of that outcome reflects how clearly the CFAA violation was established.

### Damages Methodology: Build It Before You Need It

Consistent across the Asymmetric fragments: establishing liability is easier than establishing damages, and defense strategy predictably pivots to attacking causation and limiting the damages period [[wiki/knowledge/legal/cfaa-trade-secrets-litigation.md, wiki/knowledge/legal/damages-calculation-methodology.md]]. The implication is that damages methodology must be constructed proactively, not assembled under litigation pressure.

The baseline formula is: monthly revenue per affected client × expected client tenure in months [[wiki/knowledge/legal/damages-calculation-methodology.md]]. In the Egan matter, three affected clients (Axley, Trachte, Adavacare) generated approximately $9,000/month in aggregate revenue, with an average client tenure of ~36 months. Baseline lost revenue: ~$324,000. Both CFAA and DTSA permit a 2× multiplier on actual damages for willful violations, bringing the initial demand to approximately $648,000 [[wiki/knowledge/legal/damages-calculation-methodology.md]].

One additional structural point: fraud-based judgments are non-dischargeable in bankruptcy [[wiki/knowledge/legal/client-extractions.md]]. This makes a judgment more valuable than a settlement that forgives debt — the defendant cannot escape through bankruptcy if the underlying conduct is fraudulent.

### LLC Governance: Exploiting Silence in Operating Agreements

A member may make voluntary capital contributions to an LLC without consent of other members if the operating agreement does not explicitly prohibit such contributions [[wiki/knowledge/legal/asymmetric-llc-capital-contributions.md, wiki/knowledge/legal/asymmetric-member-termination-process.md]]. The Asymmetric operating agreement is silent on this point — it neither prohibits nor authorizes unilateral contributions — and that silence is the mechanism the strategy exploits.

Mark contributed $5,000 after Egan's termination to establish majority ownership; attorney Sam Wayne assessed that any amount exceeding Egan's capital account balance would suffice [[wiki/knowledge/legal/asymmetric-llc-capital-contributions.md]]. Wayne characterized the position as holding up well but "not ironclad" — an opposing argument could be made that the agreement structure functionally requires joint agreement. The strategy is sound but carries residual legal risk that should be acknowledged in any governance planning.

### Tortious Interference: The Line Between Competition and Liability

Ordinary competition and client solicitation by former employees is not actionable [[wiki/knowledge/legal/tortious-interference-business-relationships.md, wiki/knowledge/legal/index.md]]. The tortious interference claim activates only when solicitation uses unauthorized access or stolen proprietary information. The five required elements are: (1) known existing relationship, (2) intentional interference, (3) improper means, (4) causation, (5) damages [[wiki/knowledge/legal/tortious-interference-business-relationships.md]]. In the Egan matter, unauthorized Google Ads account access combined with client poaching satisfies elements 2 and 3 simultaneously — the access is both the intentional act and the improper means.

The breach of covenant of good faith and fair dealing claim — available when a defendant solicits clients during active settlement negotiation — is the weakest of the layered claims and is unlikely to survive if the defendant waited until after a deal closed to interfere [[wiki/knowledge/legal/cfaa-trade-secrets-litigation.md]]. It functions as a supplementary claim, not a standalone theory.

The strength of the layered claim structure in the Asymmetric matter sets the context for understanding settlement leverage — which is the next operational consideration once liability is established.

## What Works

**Layered claim stacking to foreclose defense pivots.** Filing CFAA, DTSA, and tortious interference simultaneously means a defendant who defeats one theory still faces the others. In the Egan matter, this structure prevented any viable defense at the TRO stage — Egan's attorney offered nothing [[wiki/knowledge/legal/egan-tro-resolution.md, wiki/knowledge/legal/cfaa-trade-secrets-litigation.md]].

**Pre-litigation damages documentation.** Building the damages model before filing — monthly revenue × tenure × statutory multiplier — produces a credible, specific demand that is hard to attack on methodology. The $648,000 demand in the Egan matter was derived from documented client revenue and tenure data, not estimated [[wiki/knowledge/legal/damages-calculation-methodology.md]].

**Criminal referral as settlement leverage.** Threatening a DA referral shifts the defendant's calculus from financial risk to potential loss of liberty. A settlement can include language where the plaintiff agrees not to proactively refer the matter, but cannot promise to stonewall an independent law enforcement investigation — that distinction preserves the leverage without creating an unenforceable promise [[wiki/knowledge/legal/settlement-leverage-criminal-referral.md]].

**Pursuing fraud-based judgments over debt-forgiveness settlements.** Fraud-based judgments are non-dischargeable in bankruptcy, making them structurally more durable than settlements that forgive debt [[wiki/knowledge/legal/client-extractions.md]]. When the defendant's financial position is uncertain, a judgment is worth more than a settlement of equivalent face value.

**Sourcing product descriptions from manufacturer websites, not third-party retailers.** For authorized resellers, using a manufacturer's own copy is generally tolerated and low-risk. Using copy from Crutchfield, Amazon, or Best Buy is copyright infringement regardless of whether links are removed [[wiki/knowledge/legal/product-description-copyright-compliance.md]]. The Flynn Audio Alpine pages required remediation after a compliance audit flagged Crutchfield-sourced copy.

**Exploiting operating agreement silence on capital contributions.** When an LLC agreement neither prohibits nor authorizes unilateral contributions, a strategic capital contribution can establish majority ownership without member consent. The $5,000 contribution in the Asymmetric matter required only that the amount exceed Egan's capital account balance [[wiki/knowledge/legal/asymmetric-llc-capital-contributions.md]].

**Registering trademarks before scaling.** BluePoint ATM's primary mark was registered with the USPTO as of December 2025, enabling use of the ® symbol and establishing priority [[wiki/knowledge/legal/bluepoint-trademark-registration.md]]. The dual-logo Reverse ATM mark status remains pending confirmation — that gap should be closed before the mark is used in marketing.

## What Doesn't Work

**Relying on a single legal theory in digital account access cases.** A standalone CFAA claim is vulnerable to arguments that the accessed information was not sufficiently secret or that damages are speculative. The claim structure only becomes robust when DTSA and tortious interference are added [[wiki/knowledge/legal/cfaa-trade-secrets-litigation.md, wiki/knowledge/legal/tortious-interference-business-relationships.md]].

**Arguing that raw data (client names, spend figures) constitutes a trade secret.** Defense counsel will correctly point out that client names are not secret. The protectable asset is the methodology — account configuration, bid structuring, campaign organization — and the damages argument must be built on that foundation [[wiki/knowledge/legal/cfaa-trade-secrets-litigation.md, wiki/knowledge/legal/index.md]].

**Using third-party retailer product copy, even with attribution removed.** The Flynn Audio matter illustrates that removing the source link does not cure the infringement. Copyright in product descriptions belongs to the retailer who wrote them, not to the manufacturer whose product is described [[wiki/knowledge/legal/product-description-copyright-compliance.md]].

**Breach of good faith and fair dealing as a primary claim.** This claim is weak standing alone and fails entirely if the defendant's interference occurred after a deal closed rather than during active negotiation [[wiki/knowledge/legal/cfaa-trade-secrets-litigation.md]]. It belongs in the claim stack as a supplementary theory, not as a lead argument.

**Criminal referral leverage against defendants with nothing to lose.** The leverage depends on the defendant having assets, reputation, or liberty to protect. A defendant with no assets and no professional standing has little reason to settle to avoid a DA referral [[wiki/knowledge/legal/settlement-leverage-criminal-referral.md]].

## Patterns Across Clients

**Digital account access as the triggering event for multi-theory litigation (Asymmetric).** The Egan matter is the clearest example in the portfolio of a single act — unauthorized Google Ads account access — generating liability across three simultaneous legal theories. The TRO was granted the same day it was sought, and the damages model produced a $648,000 demand from documented revenue data [[wiki/knowledge/legal/egan-tro-resolution.md, wiki/knowledge/legal/damages-calculation-methodology.md]]. The pattern suggests that any client whose former employee or partner retains access to digital accounts after separation is carrying unquantified legal exposure.

**Defense strategy is predictable: pivot from liability to damages (Asymmetric).** Observed consistently in the Asymmetric fragments: once liability is established, defense pivots to attacking causation (did the access actually cause the client losses?) and limiting the damages period (how long would those clients have stayed anyway?) [[wiki/knowledge/legal/cfaa-trade-secrets-litigation.md, wiki/knowledge/legal/damages-calculation-methodology.md]]. Anticipating this pivot means building the tenure and revenue documentation before filing, not after.

**Operating agreement silence creates strategic optionality (Asymmetric).** The Asymmetric LLC governance situation — where the agreement is silent on unilateral capital contributions — turned a potential ownership dispute into a resolved majority position via a $5,000 contribution [[wiki/knowledge/legal/asymmetric-llc-capital-contributions.md, wiki/knowledge/legal/asymmetric-member-termination-process.md]]. This is a single-client observation, but it points to a broader pattern: operating agreements drafted without explicit prohibitions leave strategic gaps that can be exploited by either party.

**IP compliance gaps surface during content audits, not proactively (Flynn Audio).** The Crutchfield-sourced copy on Flynn Audio's Alpine product pages was identified during a compliance audit, not caught before publication [[wiki/knowledge/legal/product-description-copyright-compliance.md]]. Based on a single engagement with Flynn Audio, this suggests that e-commerce clients building product catalogs at scale are unlikely to have a systematic content sourcing policy unless one is imposed externally.

**Trademark registration lags operational use (BluePoint ATM).** The BluePoint ATM primary mark was registered in December 2025, but the dual-logo Reverse ATM mark status remains unconfirmed [[wiki/knowledge/legal/bluepoint-trademark-registration.md]]. Seen in one engagement: a client operating under a mark for months before registration is complete, creating a window of unprotected use.

## Exceptions and Edge Cases

**Criminal referral leverage fails against judgment-proof defendants.** The general rule is that threatening a DA referral shifts settlement calculus toward resolution. The exception: defendants with no assets, no professional license, and no reputational stake have limited incentive to settle to avoid criminal exposure [[wiki/knowledge/legal/settlement-leverage-criminal-referral.md]]. Leverage assessment must include a realistic appraisal of what the defendant has to lose.

**Tortious interference requires improper means, not just competitive harm.** A former employee who solicits clients aggressively — even clients they personally managed — is not liable for tortious interference unless they used unauthorized access or stolen information to do it [[wiki/knowledge/legal/tortious-interference-business-relationships.md]]. In the Egan matter, the unauthorized Google Ads access is what activates the claim; without it, the client solicitation would be ordinary competition.

**Breach of good faith claim is timing-dependent.** The claim is available when a defendant solicits clients during active settlement negotiation, but fails if the interference occurred after the deal closed [[wiki/knowledge/legal/cfaa-trade-secrets-litigation.md]]. The window for this claim is narrow and requires precise documentation of when negotiation was active versus concluded.

**Operating agreement silence cuts both ways.** The same silence that permitted Mark's unilateral capital contribution could, in a different dispute, be used by the other member to argue that contributions require consent because the agreement doesn't authorize them unilaterally [[wiki/knowledge/legal/asymmetric-llc-capital-contributions.md, wiki/knowledge/legal/asymmetric-member-termination-process.md]]. Attorney Wayne's "not ironclad" assessment reflects this genuine ambiguity.

## Evolution and Change

The Asymmetric legal matters are active and evolving within the observation window (February–April 2026). The TRO was granted on 2026-02-26; the damages methodology and settlement leverage strategy appear to have been developed in the weeks following. The capital contribution strategy and member termination process are documented as completed actions, suggesting the LLC governance phase resolved before the litigation strategy was fully developed.

The Flynn Audio copyright compliance issue and BluePoint ATM trademark registration are point-in-time findings rather than evolving situations. The Flynn Audio remediation was flagged as requiring action; whether it was completed is not documented in the fragments. The BluePoint ATM dual-logo mark status is explicitly noted as pending confirmation as of the last fragment date.

No signals in the fragments suggest changes to the underlying legal frameworks (CFAA, DTSA, tortious interference doctrine) during the observation period. These are established statutes and common law theories with stable interpretation. The 2× willful violation multiplier under both CFAA and DTSA is statutory and not subject to judicial variation at the district level.

## Gaps in Our Understanding

**No documentation of Egan matter resolution or settlement outcome.** The fragments establish the claim structure, damages methodology, and leverage strategy, but do not document whether a settlement was reached, at what amount, or on what terms. If the matter resolved, the outcome data would calibrate the damages model for future use.

**Flynn Audio copyright remediation completion is unconfirmed.** The audit identified Crutchfield-sourced copy on Alpine product pages and flagged it for remediation, but no fragment confirms the fix was implemented. If the pages remain live with infringing copy, the liability is ongoing.

**BluePoint ATM dual-logo (Reverse ATM) trademark status is unresolved.** The primary mark is registered, but the dual-logo mark's status is explicitly pending confirmation [[wiki/knowledge/legal/bluepoint-trademark-registration.md]]. Until confirmed, use of the Reverse ATM mark in marketing carries unquantified risk.

**No evidence from clients outside the Asymmetric/Egan fact pattern on digital account security practices.** All CFAA/DTSA/tortious interference analysis derives from a single dispute. We cannot assess whether other clients in the portfolio have comparable exposure from former employees or partners retaining account access.

**Operating agreement review coverage is unknown.** The Asymmetric operating agreement analysis surfaced a material gap (silence on capital contributions). We have no evidence on whether other LLC clients in the portfolio have operating agreements with similar gaps.

## Open Questions

**Does the 2× CFAA/DTSA willful multiplier survive appellate challenge in the Seventh Circuit?** The damages demand in the Egan matter relies on this multiplier; if the circuit has unfavorable precedent on willfulness standards, the $648,000 figure may be optimistic.

**What is the minimum capital contribution required to establish majority ownership when an operating agreement is silent on the issue?** The Asymmetric strategy used $5,000 on the theory that any amount exceeding Egan's capital account balance would suffice. Has this threshold been tested in Wisconsin LLC case law?

**Does the criminal referral leverage strategy create any ethical exposure for counsel?** Using the threat of a DA referral as settlement leverage is a recognized tactic, but bar rules on this vary by jurisdiction. Wisconsin-specific guidance would be worth confirming before the strategy is used in future matters.

**At what point does manufacturer product copy authorization need to be explicit for authorized resellers?** The current guidance is that using manufacturer copy is "generally tolerated" — but tolerated is not the same as licensed. For a client scaling to hundreds of product pages, a formal authorization from key manufacturers would eliminate residual risk.

**How does the non-dischargeability of fraud judgments interact with a defendant who files bankruptcy before judgment is entered?** The strategic value of pursuing a fraud judgment over a settlement assumes the defendant doesn't file pre-judgment. The timing risk is unaddressed in the fragments.

**What triggers the DTSA's trade secret definition for digital account methodology?** The claim that account configuration and bid structuring constitute trade secrets is legally sound in principle, but the specific evidence required to establish "reasonable measures to keep the information secret" under DTSA has not been documented for the Asymmetric account management context.

## Related Topics

- [[wiki/knowledge/operations/operations.md]]
- [[wiki/knowledge/client-management/client-management.md]]
- [[wiki/knowledge/google-ads/google-ads.md]]

## Sources

Synthesized from 11 Layer 2 articles, spanning 2026-02-26 to 2026-04-08.