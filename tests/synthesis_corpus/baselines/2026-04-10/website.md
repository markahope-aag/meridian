---
title: "Website"
layer: 3
domain_type: platform-tactics
current_status: current
confidence: established
evidence_count: 233
supporting_sources:
  - wiki/knowledge/website/aviary-landing-pages-strategy.md
  - wiki/knowledge/website/bluepoint-website-health-report.md
  - wiki/knowledge/website/asymmetric-beaver-builder-seo-preservation.md
  - wiki/knowledge/website/cordwainer-website-revamp.md
  - wiki/knowledge/website/new-dawn-therapy-hipaa-contact-forms.md
  - wiki/knowledge/website/papertube-landing-page-strategy.md
  - wiki/knowledge/website/blastwave-conversion-rate-optimization.md
  - wiki/knowledge/website/framer-vs-woocommerce-flexibility.md
  - wiki/knowledge/website/react-cursor-vercel-supabase-stack.md
  - wiki/knowledge/website/wordpress-form-troubleshooting.md
contradicting_sources: []
first_seen: "2024-12-04"
last_updated: "2026-04-08"
hypothesis: false
rate_of_change: moderate
web_monitoring_frequency: quarterly
fragment_count: 233
tags: []
generated_at: "2026-04-10T13:08:18Z"
run_id: "3d3b53f8f578"
synthesizer_prompt_sha: "dd840e975612"
extract_prompt_sha: "b7ab5ab500eb"
writer_model: "claude-sonnet-4-6"
extract_model: "claude-haiku-4-5-20251001"
extraction_cache_hit: true
---

## Summary

The dominant failure mode across the portfolio is websites built for appearance rather than conversion: beautiful pages that generate traffic but no leads, paid traffic routed to homepages, and CTAs that hide content instead of driving action. The second most common failure is technical debt — SSL misconfigurations, plugin conflicts, missing security headers, and form delivery failures — that silently kills leads before any messaging can work. Platform choice (WordPress vs. React vs. Shopify vs. Framer) is a consequential decision that determines long-term maintenance cost and customization ceiling, not just build speed. SEO equity is fragile during migrations and must be actively protected. The highest-leverage interventions are almost always the cheapest: fixing contact forms, adding addresses to footers, replacing homepage ad destinations with dedicated landing pages, and completing meta descriptions.

---

## Current Understanding

The portfolio contains 233 fragments spanning 40+ named clients across industries from senior living to ATM services to organic farming. The evidence is deep enough to identify patterns that hold regardless of industry, platform, or client size.

### Conversion Architecture: The Core Problem

Websites optimized for appearance rather than conversion routinely fail to convert visitors into leads [[wiki/knowledge/website/index.md, wiki/knowledge/website/citrus-america-conversion-rebuild.md]]. This is the single most consistent finding across the portfolio. Citrus America's site was described as "beautiful" while simultaneously failing to generate leads — because it led with machines rather than the problem being solved for retailers [[wiki/knowledge/website/citrus-america-conversion-rebuild.md]]. Seamless's previous site generated 202 monthly visits yielding approximately 6 engagements, most of which were sales solicitations rather than genuine leads [[wiki/knowledge/website/seamless-extend-roof-life-messaging.md]].

The paid traffic version of this failure is more expensive: Aviary spent $800 driving approximately 385 visitors to their homepage and generated zero conversions [[wiki/knowledge/website/aviary-landing-pages-strategy.md]]. This is not an Aviary-specific finding — it is the predictable outcome of routing paid traffic to homepages. Dedicated landing pages with a single conversion goal and matched messaging consistently outperform [[wiki/knowledge/website/aviary-landing-pages-strategy.md, wiki/knowledge/website/bluepoint-landing-page-cta-improvements.md, wiki/knowledge/website/landing-page-quality-assessment.md]]. High impressions and clicks with low conversions signal that the landing page is the bottleneck, not the ads [[wiki/knowledge/website/landing-page-quality-assessment.md]].

The CTA problem compounds this: generic labels like "Learn More" hide content rather than drive action [[wiki/knowledge/website/blastwave-conversion-rate-optimization.md, wiki/knowledge/website/bluepoint-landing-page-cta-improvements.md]]. High-commitment forms placed before a visitor is convinced of value create friction that kills conversions [[wiki/knowledge/website/blastwave-conversion-rate-optimization.md]]. Multi-step dialogue-form design produces richer lead data and better qualification than single-page static forms [[wiki/knowledge/website/form-optimization-dialogue.md]].

### Technical Debt: Silent Lead Killers

Technical failures kill leads before any messaging can work. The most common are:

**Form delivery failures.** Contact form submissions silently fail when WordPress's `wp_mail` function is blocked by hosting providers — SMTP plugin configuration is the fix [[wiki/knowledge/website/shine-contact-form-bug.md, wiki/knowledge/website/wordpress-form-troubleshooting.md]]. Some hosting providers block outbound PHP mail by default [[wiki/knowledge/website/wordpress-form-troubleshooting.md]]. Overhead Door Madison's form submissions dropped to near-zero for approximately two weeks in January 2026, cause unresolved at time of documentation [[wiki/knowledge/website/overhead-door-form-submission-diagnostics.md]]. Post-submission scroll-to-top behavior leaves users unaware that confirmation messages exist below the fold [[wiki/knowledge/website/didion-landing-page-form-fixes.md]]. The recommended pattern is a dedicated Thank You page redirect rather than inline success messages [[wiki/knowledge/website/thank-you-page-redirect-pattern.md]].

**SSL and security misconfigurations.** Missing or misconfigured SSL certificates cause browser "Not Secure" warnings that deter visitors before any messaging can work [[wiki/knowledge/website/ssl-certificate-security-blocker.md]]. Mixed content (HTTP resources on an HTTPS page) triggers the same warnings even with a valid certificate [[wiki/knowledge/website/ssl-certificate-security-blocker.md]]. Missing security headers (CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, Permissions-Policy) are treated as a quality signal by Google's crawlers [[wiki/knowledge/website/aviary-webflow-security-headers.md]]. Security issues also create active litigation risk [[wiki/knowledge/website/cordwainer-website-revamp.md]].

**Performance degradation.** Page speed at 45/100 hurts both UX and Google ranking signals [[wiki/knowledge/website/cordwainer-website-revamp.md]]. Database query counts of 600+ indicate a serious backend problem, typically caused by plugin conflicts, unoptimized queries, or bot-driven traffic spikes [[wiki/knowledge/website/website-backend-performance-optimization.md]]. Doudlah Farms accumulated log files and bot traffic degraded load times to 6+ seconds; after cleanup, load times dropped below 2 seconds [[wiki/knowledge/website/doudlah-farms-website-performance-cleanup.md]]. Bot traffic running card-testing scams can be mitigated by removing vulnerable products and implementing CAPTCHA on checkout endpoints [[wiki/knowledge/website/doudlah-farms-website-performance-cleanup.md]].

**The missing address.** A confirmed lost sale at Overhead Door Madison occurred because a customer Googled the address, found no result on the website, and ended up at a similarly-named competitor instead [[wiki/knowledge/website/overhead-door-address-footer-fix.md]]. Address plus map embed in the footer is a low-effort, high-protection fix for any local service business competing with similarly-named competitors.

### Platform Selection: Ceiling vs. Cost

Platform choice determines long-term maintenance cost and customization ceiling, not just build speed. The tradeoffs are clear from portfolio evidence:

**WordPress** is the default for most client sites. It handles SEO well, supports Gravity Forms + Zapier as a standard integration pattern [[wiki/knowledge/website/gravity-forms-zapier-integration.md]], and has a large plugin ecosystem. The liabilities: recurring breakage from plugin update conflicts (observed at La Marie Beauty and Reynolds Transfer) [[wiki/knowledge/website/asymmetric-nextjs-rebuild.md]], `wp_mail` delivery failures, and plugin workarounds for non-standard requirements that create costly friction [[wiki/knowledge/website/wordpress-vs-custom-stack-decision.md]]. Gravity Forms is preferred over WP Forms for reliability and extensibility [[wiki/knowledge/website/wordpress-form-troubleshooting.md]].

**React/Next.js + Cursor + Vercel + Supabase** is appropriate for projects requiring custom booking logic, complex B2B portals, or precise UI behavior [[wiki/knowledge/website/react-cursor-vercel-supabase-stack.md]]. React sites are roughly one-tenth the size of equivalent WordPress builds [[wiki/knowledge/website/react-cursor-vercel-supabase-stack.md]]. SEO tasks (meta descriptions, alt text, title tags) can be executed as single commands across an entire site in approximately 15 minutes versus hours in WordPress [[wiki/knowledge/website/asymmetric-nextjs-rebuild.md]]. The constraint: Cursor AI requires precise, technically-informed prompts — vague instructions produce poor results [[wiki/knowledge/website/react-cursor-vercel-supabase-stack.md]].

**Shopify** imposes hard ceilings on customization: templated structure does not allow custom code for graphics, display rotations, and interactive elements [[wiki/knowledge/website/papertube-landing-page-wordpress-strategy.md]]. WordPress outperforms Shopify for SEO in contexts where organic search matters [[wiki/knowledge/website/papertube-wordpress-shopify-hybrid.md]].

**Out-of-the-box platforms** (Framer, Webflow, Squarespace) are faster and cheaper to build but impose hard ceilings on customization that can be expensive and disruptive to cross mid-project [[wiki/knowledge/website/framer-vs-woocommerce-flexibility.md]]. Custom React builds have the highest initial investment and highest ongoing maintenance cost because every update requires developer time [[wiki/knowledge/website/framer-vs-woocommerce-flexibility.md]].

WordPress templates are preferable to custom JavaScript builds when launch deadlines are active [[wiki/knowledge/website/asymmetric-wordpress-template-decision.md]].

### SEO Equity: Fragile During Migrations

Asymmetric's own site holds a Domain Rating of 53 with 858 referring domains — significant equity that is at risk during any platform migration [[wiki/knowledge/website/asymmetric-beaver-builder-seo-preservation.md]]. Migrating 100+ blog posts from Beaver Builder to Elementor risks losing indexed pages and disrupting internal link equity [[wiki/knowledge/website/asymmetric-beaver-builder-seo-preservation.md]]. Site migrations silently drop SEO metadata — American Extractions lost meta descriptions during launch [[wiki/knowledge/website/american-extractions-site-launch.md]]. Organic traffic improvements from meta description completion lag by days to weeks as search engines re-crawl [[wiki/knowledge/website/american-extractions-site-launch.md]].

State-specific landing pages expand keyword surface area: BluePoint's organic traffic doubled and the site tracked 124 keywords with organic keyword count growing after state page deployment [[wiki/knowledge/website/bluepoint-state-pages-strategy.md]]. Static PDF catalogs receive zero SEO value and cannot be indexed [[wiki/knowledge/website/gypsoteca-catalog-seo-strategy.md]]; converting PDFs to individual webpages makes content crawlable [[wiki/knowledge/website/finwellu-financial-learning-library.md]]. Domain rank of 6/100 (Park Capital) signals low search engine trust and makes organic ranking unlikely until improved [[wiki/knowledge/website/park-capital-wordpress-rebuild.md]].

### Compliance and Legal Constraints

Standard WordPress contact forms are not HIPAA compliant without a Business Associate Agreement [[wiki/knowledge/website/new-dawn-therapy-hipaa-contact-forms.md]]. This is a hard constraint for any healthcare client — not a preference. Warranty language must distinguish between contractor-issued workmanship warranties and manufacturer warranties to avoid overpromising [[wiki/knowledge/website/sbs-warranty-copy-strategy.md, wiki/knowledge/website/sbs-warranty-differentiation.md]]. Named client testimonials on homepages create a prospecting list for competitors — Indiana Lifestone Company used Quarra's published testimonials to contact and win away all named clients [[wiki/knowledge/website/quarra-italia-testimonials-strategy.md]].

---

## What Works

**1. Dedicated landing pages for paid traffic**
Routing paid traffic to a purpose-built page with a single conversion goal and matched messaging is the highest-leverage conversion intervention available. Aviary's zero-conversion $800 campaign was entirely attributable to homepage routing [[wiki/knowledge/website/aviary-landing-pages-strategy.md]]. Removing navigation from landing pages and keeping only a Contact Us anchor link prevents prospects from clicking away before converting [[wiki/knowledge/website/papertube-landing-page-strategy.md]]. Sending Google Ads traffic directly to third-party booking tools prevents analytics ownership, remarketing, and A/B testing [[wiki/knowledge/website/conversion-optimized-landing-pages.md]].

**2. SMTP plugin configuration for form delivery**
When WordPress contact forms silently fail, the fix is almost always SMTP plugin configuration — not form plugin replacement. Hosting providers block outbound PHP mail by default more often than documented [[wiki/knowledge/website/shine-contact-form-bug.md, wiki/knowledge/website/wordpress-form-troubleshooting.md]]. Confirming SMTP delivery before launch should be a standard checklist item.

**3. State-specific landing pages for geographic keyword expansion**
BluePoint's organic traffic doubled after deploying state pages targeting regional search terms [[wiki/knowledge/website/bluepoint-state-pages-strategy.md]]. State pages create indexable, keyword-rich content that expands the site's keyword footprint without requiring new service offerings [[wiki/knowledge/website/bluepoint-state-pages-content-strategy.md]]. State-specific local CallRail numbers improve perceived local credibility for out-of-state prospects [[wiki/knowledge/website/bluepoint-state-service-pages.md]].

**4. Address and map embed in footer for local service businesses**
Overhead Door Madison lost a confirmed sale because the website had no address and the customer found a competitor instead [[wiki/knowledge/website/overhead-door-address-footer-fix.md]]. For any local service business competing with similarly-named competitors, this is a 30-minute fix with measurable downside protection.

**5. Thank You page redirect instead of inline form success messages**
Users perceive inline page refresh as failure or a loop rather than success. A dedicated Thank You page provides unambiguous confirmation and enables conversion tracking as a discrete event in Google Analytics [[wiki/knowledge/website/thank-you-page-redirect-pattern.md, wiki/knowledge/website/new-dawn-shine-contact-page-hierarchy.md]].

**6. Converting PDFs and static catalogs to indexed web pages**
Static PDF catalogs receive zero SEO value [[wiki/knowledge/website/gypsoteca-catalog-seo-strategy.md]]. Converting PDFs to individual webpages makes content crawlable and indexable, expanding keyword surface area without new content creation [[wiki/knowledge/website/finwellu-financial-learning-library.md]].

**7. Two-step written approval process for website changes**
Copy approval followed by staging page approval prevents unapproved or incomplete changes from going live [[wiki/knowledge/website/bluepoint-two-step-approval-process.md]]. Incognito-window testing catches cache-dependent issues like dynamic number swapping [[wiki/knowledge/website/bluepoint-content-audit-restructuring.md]]. Browser drag-to-narrow does not always match real device behavior — physical device testing is required [[wiki/knowledge/website/vcedc-responsiveness-bugs.md]].

**8. Cloudflare firewall rules for bot traffic mitigation**
Updating Cloudflare firewall rules to block known bot signatures mitigates bot traffic overload [[wiki/knowledge/website/website-backend-performance-optimization.md]]. Doudlah Farms reduced load times from 6+ seconds to under 2 seconds after bot traffic cleanup and log file removal [[wiki/knowledge/website/doudlah-farms-website-performance-cleanup.md]].

**9. Structured FAQ sections for AI citation**
FAQ sections formatted as direct Q&A enable AI models to easily extract and cite content — a growing traffic source as AI-generated answers increasingly reference structured web content [[wiki/knowledge/website/ahs-website-health-audit.md]].

**10. Anonymous client quotes for relationship-driven B2B**
For custom fabrication, luxury construction, and bespoke manufacturing clients, anonymous project-level quotes preserve social proof without creating a prospecting list for competitors [[wiki/knowledge/website/quarra-italia-testimonials-strategy.md]]. The Quarra case (Indiana Lifestone poaching named testimonial clients) is the clearest evidence this risk is real.

**11. Ahrefs health score monitoring with 80+ target**
Ahrefs health scores below 80 should be prioritized for remediation [[wiki/knowledge/website/website-backend-performance-optimization.md]]. BluePoint achieved a 100% Ahrefs health score with zero crawl errors after systematic remediation — meta description coverage went from 72% to 98%, SSL rating improved from Flexible to A+ [[wiki/knowledge/website/bluepoint-website-health-report.md]].

**12. Full Elementor rebuild over in-place fixes**
Full Elementor rebuilds are faster and less risky than in-place fixes when a site is built on unsupported builders or incompatible plugins [[wiki/knowledge/website/elementor-rebuild-pattern.md]]. The complimentary rebuild scope is limited to building existing designs, not creating new ones [[wiki/knowledge/website/elementor-rebuild-process.md]].

---

## What Doesn't Work

**1. Routing paid traffic to homepages**
Aviary spent $800 with approximately 385 visitors and zero conversions because all traffic went to the homepage [[wiki/knowledge/website/aviary-landing-pages-strategy.md]]. This is the most expensive and most common conversion failure in the portfolio. Homepages used as ad destinations are almost always suboptimal [[wiki/knowledge/website/landing-page-quality-assessment.md]].

**2. Generic CTAs ("Learn More," "Select your state")**
"Learn More" hides content rather than drives action [[wiki/knowledge/website/blastwave-conversion-rate-optimization.md]]. "Select your state" creates user confusion about expected outcomes [[wiki/knowledge/website/bluepoint-state-pages-expansion.md]]. Descriptive CTAs ("Click on your state to see our local service offerings") outperform vague ones [[wiki/knowledge/website/bluepoint-state-pages-optimization.md]].

**3. Named client testimonials on public-facing pages in relationship-driven B2B**
Indiana Lifestone Company used Quarra's published testimonials to identify and contact all named clients, winning them away [[wiki/knowledge/website/quarra-italia-testimonials-strategy.md]]. In industries where client relationships are the competitive moat, named testimonials are a liability.

**4. Multi-product navigation on SaaS sites**
AviaryAI tested multi-product navigation and found it diluted the core message and hurt conversion [[wiki/knowledge/website/aviary-voice-agent-first-navigation.md]]. Two-sided marketplace platforms similarly suffer from conflated homepages — separate audience paths outperform [[wiki/knowledge/website/advintro-conversion-optimization.md]].

**5. Websites that conflate two distinct brands**
Trachte's site conflated two distinct brands under one web presence, creating confusion for prospects and generating unqualified inbound calls [[wiki/knowledge/website/trachte-conversion-optimization.md]]. The fix requires structural separation, not copy editing.

**6. High-commitment forms before value is established**
Placing a detailed contact form at the top of a page before the visitor understands the offer creates friction that kills conversions [[wiki/knowledge/website/blastwave-conversion-rate-optimization.md]]. Multi-step dialogue forms that build toward commitment outperform front-loaded forms [[wiki/knowledge/website/form-optimization-dialogue.md]].

**7. Editing websites Asymmetric does not manage**
If something breaks on a site Asymmetric edits but does not manage, there is no clean path to fix it without coordinating with an external party [[wiki/knowledge/website/website-management-seo-optimization.md]]. This is a liability management issue, not a capability issue.

**8. Placeholder events on live websites**
Placeholder events on a live website damage credibility and should be removed before launch [[wiki/knowledge/website/vcedc-events-page-placeholder-strategy.md]]. VCEDC's launch was blocked by this issue.

**9. QR codes linking to manufacturer pages**
QR codes linking to manufacturer pages have zero SEO benefit for the store's own domain [[wiki/knowledge/website/in-store-qr-code-strategy.md]]. Custom catalog pages build keyword-rich content that improves organic search rankings for the client's domain instead [[wiki/knowledge/website/in-store-qr-code-strategy.md]].

**10. Delegating website launches to offshore team members**
Website launches require real-time monitoring and cannot be delegated to offshore team members due to timezone constraints [[wiki/knowledge/website/website-launch-delegation-challenge.md]]. This is an operational constraint, not a quality judgment.

---

## Patterns Across Clients

**Pattern 1: The beautiful-but-broken site (most common)**
Observed at Citrus America, Seamless, and Aviary — sites that look professional but fail to convert because they lead with product features rather than buyer problems, use generic CTAs, and lack clear conversion paths. Citrus America led with machines; Seamless generated 202 monthly visits and ~6 engagements; Aviary generated zero conversions from $800 in paid traffic [[wiki/knowledge/website/citrus-america-conversion-rebuild.md, wiki/knowledge/website/seamless-extend-roof-life-messaging.md, wiki/knowledge/website/aviary-landing-pages-strategy.md]]. The pattern appears across B2B and B2C contexts. The fix is always structural: reframe around buyer problems, replace generic CTAs, add dedicated landing pages.

**Pattern 2: Form delivery failures discovered after launch**
Observed at Shine, Overhead Door Madison, Cordwainer, and Didion — contact forms that appear functional but silently fail to deliver submissions. The root cause varies: `wp_mail` blocked by hosting (Shine) [[wiki/knowledge/website/shine-contact-form-bug.md]], seasonal traffic drop masking a technical failure (Overhead Door) [[wiki/knowledge/website/overhead-door-form-submission-diagnostics.md]], third-party form builder not triggering confirmation emails (Cordwainer) [[wiki/knowledge/website/cordwainer-contact-careers-redesign.md]], form builder defaults overriding custom confirmation messages (Didion) [[wiki/knowledge/website/didion-landing-page-form-fixes.md]]. The pattern: form failures are discovered reactively, not proactively. Monthly maintenance audits with explicit form testing would catch these earlier.

**Pattern 3: SEO equity lost during migrations**
Observed at Asymmetric and American Extractions — platform migrations that silently drop metadata, break internal links, or disrupt indexed pages. Asymmetric's migration from Beaver Builder to Elementor risked 227 organic keywords and ~5,900 monthly visitors [[wiki/knowledge/website/asymmetric-beaver-builder-seo-preservation.md]]. American Extractions lost meta descriptions during site launch [[wiki/knowledge/website/american-extractions-site-launch.md]]. The pattern: SEO preservation is treated as a post-launch cleanup task rather than a migration requirement. Pre-migration crawl snapshots and post-launch validation should be standard.

**Pattern 4: Content bottlenecks blocking launches**
Observed at Quaritalia (3+ months delayed), Quarra (shifted to twice-weekly meetings to accelerate), and Citrus America (dealer landing page 95% complete but blocked on form dropdown). Client-provided content assets — copy, photos, approvals — are the most common launch blocker across the portfolio [[wiki/knowledge/website/quaritalia-site-launch-blockers.md, wiki/knowledge/website/quarra-website-working-sessions.md, wiki/knowledge/website/citrus-america-dealer-landing-page.md]]. Twice-weekly working sessions (Quarra) accelerated progress more than email-only coordination.

**Pattern 5: Bot traffic degrading performance**
Observed at Doudlah Farms (card-testing bots on checkout), Skaalen (spam bot form submissions with identical wording), and sites requiring Cloudflare firewall intervention. Bot traffic is not a rare edge case — it is a recurring operational issue that degrades performance, pollutes analytics, and in Doudlah's case, enabled fraud [[wiki/knowledge/website/doudlah-farms-website-performance-cleanup.md, wiki/knowledge/website/skaalen-recaptcha-security.md, wiki/knowledge/website/website-backend-performance-optimization.md]]. reCAPTCHA and Cloudflare firewall rules are the standard mitigations.

**Pattern 6: Mobile responsiveness failures discovered late**
Observed at VCEDC (3 bugs blocking launch: hero image overflow, social icon stacking, button misalignment), BluePoint (hero images disappearing at mobile breakpoints, phone numbers inaccessible on scroll), and Aiden (automated Playwright testing identified issues across screen sizes) [[wiki/knowledge/website/vcedc-responsiveness-bugs.md, wiki/knowledge/website/bluepoint-state-pages-optimization.md, wiki/knowledge/website/aiden-app-ux-improvements.md]]. Browser drag-to-narrow testing does not catch all real device behavior. Physical device testing or automated cross-breakpoint testing (Playwright) is required.

**Pattern 7: WordPress plugin conflicts causing recurring breakage**
Observed at La Marie Beauty, Reynolds Transfer, and Didion (HTTP 500 errors from unknown plugin conflict, compounded by an outdated Square plugin after switching to PayPal) [[wiki/knowledge/website/asymmetric-nextjs-rebuild.md, wiki/knowledge/website/didion-website-errors-paypal-migration.md]]. Plugin update conflicts are the primary maintenance liability of WordPress. Sites with high plugin counts or custom plugin integrations (payment processors, sliders) are highest risk.

**Pattern 8: Approval process gaps causing live errors**
Observed at BluePoint (broken phone numbers and garbled copy found on homepage after edits went live) and VCEDC (placeholder content on live pages) [[wiki/knowledge/website/bluepoint-content-audit-restructuring.md, wiki/knowledge/website/vcedc-events-page-placeholder-strategy.md]]. The two-step written approval process (copy approval + staging page approval) was implemented at BluePoint specifically to prevent recurrence [[wiki/knowledge/website/bluepoint-two-step-approval-process.md]].

---

## Exceptions and Edge Cases

**Separate domains for campaign landing pages**
The general rule is to keep landing pages on the primary domain for SEO benefit. Exception: when campaigns are driven entirely by email and LinkedIn (not PPC), a separate domain carries no meaningful SEO downside [[wiki/knowledge/website/papertube-landing-page-wordpress-strategy.md]]. PaperTube's campaign targeting was precise enough that organic discovery was irrelevant.

**HIPAA compliance overrides standard form recommendations**
The standard recommendation is WordPress contact forms with Gravity Forms. For healthcare clients, this is not compliant without a Business Associate Agreement [[wiki/knowledge/website/new-dawn-therapy-hipaa-contact-forms.md]]. New Dawn Therapy required a separate compliant solution. This exception applies to any client handling protected health information.

**Custom React builds for complex booking and portal logic**
WordPress is the default recommendation. Exception: when the project requires custom booking logic, complex B2B portals, or precise UI behavior that WordPress plugin workarounds cannot handle cleanly, React + Cursor + Vercel + Supabase is the appropriate stack [[wiki/knowledge/website/react-cursor-vercel-supabase-stack.md]]. The cost is higher initial investment and higher ongoing maintenance — justified only when WordPress's ceiling is genuinely insufficient.

**Named testimonials are appropriate in some B2B contexts**
The Quarra case establishes that named testimonials create competitive risk in relationship-driven B2B. Exception: in industries where client identity is not a competitive asset (e.g., consumer services, SaaS), named testimonials remain the standard recommendation. The risk is specific to custom fabrication, luxury construction, and bespoke manufacturing where client relationships are the moat.

**Sending traffic to third-party booking tools**
The general rule is to own the landing page and embed or link to booking tools. Exception: when the client's booking tool is the primary product experience and the client has no analytics infrastructure to maintain, direct routing may be acceptable — but this sacrifices remarketing and A/B testing capability [[wiki/knowledge/website/conversion-optimized-landing-pages.md]].

**Approximately 90% residential traffic on a B2B-positioned site**
Overhead Door Madison's web form traffic is approximately 90% residential despite the business serving both residential and commercial customers [[wiki/knowledge/website/overhead-door-residential-cta-form.md]]. This inverts the typical assumption that a commercial-facing site attracts commercial traffic. Form design and CTA language should reflect actual traffic composition, not intended audience.

**Tour-to-move-in conversion at memory care facilities**
Cordwainer's facility converts 7 of 8 tour visitors to move-ins [[wiki/knowledge/website/cordwainer-booking-funnel-optimization.md]]. This is an exceptionally high conversion rate that shifts the optimization priority: the bottleneck is tour volume, not tour-to-close rate. Website strategy should focus entirely on driving tour bookings rather than general awareness.

---

## Evolution and Change

**2024: Conversion architecture becomes the primary concern**
The earliest fragments (December 2024) show a portfolio-wide shift from "does the site exist and look professional" to "does the site convert." The Seamless, Citrus America, and Aviary findings all date to this period and establish the pattern that appearance-optimized sites fail commercially. The Aviary $800 zero-conversion finding is the clearest data point.

**2025: Technical debt surfaces as a parallel crisis**
Through 2025, the evidence accumulates on technical failures — SSL misconfigurations, form delivery failures, bot traffic, plugin conflicts, and performance degradation. The BluePoint health report (meta descriptions from 72% to 98%, SSL from Flexible to A+, Ahrefs from baseline to 100%) represents the most systematic remediation documented in the portfolio [[wiki/knowledge/website/bluepoint-website-health-report.md]]. The Doudlah Farms bot cleanup and the Overhead Door form failure diagnostics fall in this period.

**2025-2026: Platform diversification accelerates**
The React + Cursor + Vercel + Supabase stack appears in late 2025 and early 2026 fragments as an alternative to WordPress for complex builds [[wiki/knowledge/website/react-cursor-vercel-supabase-stack.md, wiki/knowledge/website/asymmetric-nextjs-rebuild.md]]. The Next.js rebuild discussion for Asymmetric's own site signals internal conviction that WordPress's maintenance liability is worth trading against React's higher initial cost for the right projects. This is a meaningful shift — the portfolio was almost entirely WordPress through 2024.

**Emerging: AI citation as a new SEO surface**
The AHS finding that structured FAQ sections enable AI models to extract and cite content [[wiki/knowledge/website/ahs-website-health-audit.md]] is the first signal in the portfolio of AI-generated answers as a traffic source. This is a 2026 fragment. The implication — that structured Q&A content serves both traditional SEO and AI citation — is not yet reflected in standard page templates across the portfolio.

---

## Gaps in Our Understanding

**Enterprise-scale client evidence is absent.** All website observations come from SMB and mid-market clients. The largest clients in the portfolio (Didion, Trachte) are mid-market manufacturing. We have no evidence on how these patterns transfer to enterprise contexts with dedicated IT, legal review requirements, and multi-stakeholder approval chains.

**Conversion rate benchmarks by industry are missing.** We know Seamless generated ~6 engagements from 202 visits and Aviary generated zero from 385 visits. We do not have conversion rate baselines by industry (roofing vs. SaaS vs. senior living) that would let us tell a client whether their current rate is bad or catastrophically bad.

**Long-term SEO impact of platform migrations is unresolved.** Asymmetric's Beaver Builder to Elementor migration risk is documented, but we have no post-migration data showing whether the preserved equity held, declined, or recovered. We cannot yet say with confidence that our migration protocols work.

**React/Next.js maintenance cost over 12+ months is unobserved.** The React stack is positioned as lower maintenance than WordPress. We have no client sites that have been on this stack for more than 12 months. The claim that it avoids plugin conflict breakage is plausible but unverified at portfolio scale.

**Form conversion rates before and after optimization are rarely captured.** We document that forms were broken or that CTAs were generic, but we rarely have before/after conversion data to quantify the impact of fixes. This makes it difficult to prioritize remediation work by expected return.

**AI citation traffic is unmeasured.** The AHS FAQ finding identifies a new traffic surface but we have no analytics data showing how much traffic any client site receives from AI-generated answers. We cannot currently tell clients whether optimizing for AI citation is worth the effort.

---

## Open Questions

**Does the 700-1,000 word minimum for SEO pages hold under 2026 Google algorithm updates focused on experience signals?** The portfolio evidence for this threshold comes from New Dawn Therapy's SEO page strategy [[wiki/knowledge/website/new-dawn-therapy-seo-page-strategy.md]] but predates the most recent algorithm changes.

**What is the actual maintenance cost differential between WordPress and React/Next.js over a 24-month period?** The claim that React is lower maintenance is based on the absence of plugin conflicts, but React requires developer time for every update. The crossover point — where React becomes cheaper than WordPress — is unknown.

**Does Cloudflare's bot mitigation degrade legitimate traffic in any measurable way?** The Doudlah Farms and backend performance fragments recommend Cloudflare firewall rules aggressively, but we have no data on false positive rates for legitimate visitors.

**How does Google's Quality Score algorithm weight landing page experience relative to ad relevance and expected CTR?** The portfolio establishes that landing page quality matters for Quality Score [[wiki/knowledge/website/website-management-seo-optimization.md]], but the relative weighting determines how much conversion optimization effort is justified purely for ad efficiency.

**What is the practical ceiling for Ahrefs health score given non-image file types?** BluePoint achieved 89% alt text coverage with the remaining gap attributed to PDFs and documents [[wiki/knowledge/website/bluepoint-website-health-report.md]]. Is 89-90% the practical ceiling for sites with document libraries, or can it be pushed higher?

**Does the structured FAQ format for AI citation require schema markup, or is plain HTML sufficient?** The AHS finding identifies FAQ structure as enabling AI extraction [[wiki/knowledge/website/ahs-website-health-audit.md]] but does not specify whether FAQ schema markup is required or whether well-structured HTML achieves the same result.

**At what traffic volume does a separate campaign domain become a meaningful SEO liability?** The PaperTube finding suggests separate domains are acceptable for email/LinkedIn campaigns [[wiki/knowledge/website/papertube-landing-page-wordpress-strategy.md]], but the threshold at which organic discovery becomes significant enough to justify subdomain or subfolder structure instead is unspecified.

---

## Related Topics

- [[wiki/knowledge/seo/index.md]]
- [[wiki/knowledge/google-ads/index.md]]
- [[wiki/knowledge/analytics/index.md]]
- [[wiki/knowledge/content/index.md]]
- [[wiki/knowledge/email/index.md]]
- [[wiki/knowledge/crm/index.md]]
- [[wiki/knowledge/branding/index.md]]

---

## Sources

Synthesized from 233 Layer 2 articles, spanning 2024-12-04 to 2026-04-08.