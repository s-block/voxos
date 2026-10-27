# Evidence-Backed Web Research

## Use when / do not use when

Use this workflow for current, factual research that benefits from linked
evidence, source comparison, or verification. Do not use it for a simple answer
already supported by stable local context, private data, or requests for
unverified speculation.

## Inputs to resolve

Identify the exact question, relevant geography, time window, desired depth, and
whether the user needs primary sources, a recommendation, or a neutral evidence
brief. Ask only for a missing constraint that would materially change the
search.

## Tool workflow

The upstream names below are capability labels. Select the currently advertised
MCP tool whose description identifies that upstream capability.

1. Turn the question into one precise `web_search_exa` query. Add a second
   contrasting query only when the first results lack primary sources or an
   important viewpoint.
2. Inspect titles, dates, publishers, and snippets before fetching anything.
   Prefer current primary sources, official documentation, research papers, or
   direct records; include a reputable independent source when interpretation
   matters.
3. Use `web_fetch_exa` only for the strongest pages needed to support the
   answer. Normally fetch three to six sources, not every result.
4. Reconcile publication dates, the date an event occurred, definitions,
   measurement methods, and material disagreements.

## Validation and fallback rules

Do not treat snippets as proof when the full page is available. Separate facts
supported by sources from inference. If a page cannot be fetched, replace it
with another authoritative source or state the limitation. If sources conflict,
describe the conflict and why one may be more applicable rather than silently
choosing.

## Output contract

Lead with the answer. Attach a direct link near every important current claim,
then give the smallest useful evidence summary. Include dates and units, note
meaningful uncertainty, and identify any inference explicitly.

## Safety and side effects

This workflow is read-only. Do not submit forms, bypass access controls, expose
private search context, or reproduce long copyrighted passages. Keep quotations
short and necessary.
