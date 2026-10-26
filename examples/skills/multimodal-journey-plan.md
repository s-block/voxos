# Multimodal Journey Plan

## Use when / do not use when

Use this workflow for a door-to-door public transport, walking, cycling, or
mixed-mode journey with route alternatives, transfers, and a shareable TripGo
plan. Do not use it for emergency travel, guaranteed accessibility, or ticket
purchase.

## Inputs to resolve

Collect specific origin and destination descriptions, departure or arrival
time with timezone, acceptable modes, walking tolerance, accessibility needs,
party size, and preference for speed, cost, or fewer transfers.

## Tool workflow

The upstream names below are capability labels. Select the currently advertised
MCP tool whose description identifies that upstream capability.

1. Use Maps `search_places` to disambiguate human place descriptions and obtain
   coordinates before routing.
2. Use `tripgo-locations` around those coordinates only when nearby
   transport-related locations or stops help resolve the journey.
3. Call `tripgo-routing` with the resolved coordinates, explicit time
   constraint, accessibility flag when requested, and allowed modes.
   Compare a small number of materially different alternatives.
4. When a route identifies a stop and near-term departure matters, use
   `tripgo-departures` for that stop.
5. Use `tripgo-get-trip-url` for the selected route and return the shareable
   link.

## Validation and fallback rules

Keep origin, destination, and timezone consistent across calls. Distinguish
scheduled routing from live departure observations. Do not promise step-free
access, fares, seat availability, or on-time operation unless the returned data
supports it. If Maps and TripGo resolve different places, stop and ask the user
to choose.

## Output contract

Lead with the recommended route, then show one or two useful alternatives with
door-to-door duration, modes, transfers, walking, key departure times, and
trade-offs. Include the TripGo link and state when live data should be checked
again.

## Safety and side effects

This workflow is read-only. It does not buy tickets or reserve travel. Minimize
the use of private home addresses in the response.
