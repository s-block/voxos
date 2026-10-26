# Website Content And Performance Audit

## Use when / do not use when

Use this workflow to audit a public website's representative pages for content
quality, information architecture, SEO and accessibility signals, and
Lighthouse performance. Do not use it as a WCAG conformance audit, penetration
test, exhaustive crawler, or substitute for authenticated product testing.

## Inputs to resolve

Confirm the site or section, target audience, business goal, priority journeys,
device expectations, and any known problem pages. If the scope is broad, agree
on a representative sample rather than attempting the whole site.

## Tool workflow

The upstream names below are capability labels. Select the currently advertised
MCP tool whose description identifies that upstream capability.

1. Call `browserless_map` once on the starting URL with a bounded result limit
   and, when useful, a search term tied to the target journey.
2. Select a bounded sample, normally the home page plus three to seven pages
   covering primary templates or journeys.
3. Use `browserless_smartscraper` on those pages to inspect headings, copy,
   calls to action, navigation, and structured content. Request HTML only when
   markup or document metadata is needed; otherwise prefer markdown.
4. Use `browserless_performance` with the relevant Lighthouse categories on
   the most important one to three URLs. Compare like-for-like page types and
   record the observed test context.

## Validation and fallback rules

Separate Lighthouse measurements from editorial or manual accessibility
inferences. A Lighthouse accessibility score is not proof of WCAG conformance.
Do not infer site-wide behavior from one template. If mapping misses a
JavaScript route, ask for its URL or state that it was not sampled. Avoid login,
checkout, or destructive flows unless the user explicitly provides a safe test
environment and authorizes them.

## Output contract

Return an executive summary, sampled URLs, and prioritized findings grouped as
critical, important, and opportunistic. For each finding include evidence,
user/business impact, a concrete fix, and whether it is measured or heuristic.
Call out sample limitations and recommended specialist or manual follow-up.

## Safety and side effects

Use only bounded read-oriented tools. Respect robots, rate limits, private
content, and provider quotas. Do not use broader agent, function, download, or
crawl capabilities as an implicit fallback.
