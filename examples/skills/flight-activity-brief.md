# Flight Activity Brief

## Use when / do not use when

Use this workflow for a bounded brief on live or recent observed flight
activity, airport traffic, airline identity, or movement in a defined area. Do
not use it as an authoritative schedule, safety, disruption, or future-arrival
service.

## Inputs to resolve

Resolve airport or airline codes, geographic bounds, the exact question, and
the UTC time window. Convert a user's local time explicitly and show the
resulting UTC boundaries.

## Tool workflow

The upstream names below are capability labels. Select the currently advertised
MCP tool whose description identifies that upstream capability.

1. Use `get_airport_info_light` and `get_airline_info` to resolve unfamiliar
   codes before interpreting flight results.
2. For broad live questions, call `get_live_flights_count` with the intended
   filters before requesting positions with those same filters.
3. Use `get_live_flights_positions_light` only for a bounded area and when
   position details are needed.
4. Use `get_flight_summary_light` with explicit, narrow UTC start and end times
   for recent movement summaries.

## Validation and fallback rules

Prefer count and light tools over full, track, or historical calls. Treat
positions and summaries as observed data, not schedules or predictions. State
the observation window and avoid interpreting missing aircraft as cancellation.
If a code is ambiguous, resolve it before continuing.

## Output contract

Return a timestamped brief with resolved entities, scope, counts, notable
observed movements, and limitations. Keep tables bounded and use UTC alongside
the user's requested timezone where helpful.

## Safety and side effects

This workflow is read-only. Respect provider subscription limits and avoid
tracking a private person's movement or presenting flight observations as
safety-critical guidance. Do not use this workflow unless the host's storage
and deletion policy for returned flight data complies with the provider's
current retention terms.
