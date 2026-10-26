# Grounded Local Day Plan

## Use when / do not use when

Use this workflow for a practical local itinerary or day plan that needs
verified places, relevant weather, and feasible travel between stops. Do not use
it for ticket booking, live public-transport operations, or emergency routing.

## Inputs to resolve

Collect the date, specific city or area, start and end points, available hours,
party needs, mobility constraints, transport modes, budget, interests, and
fixed reservations. Ask only for constraints that would change the plan.

## Tool workflow

The upstream names below are capability labels. Select the currently advertised
MCP tool whose description identifies that upstream capability.

1. Use `search_places` with specific, disambiguated locations to find only the
   stops needed for the requested plan.
2. Use `lookup_weather` when the date and outdoor exposure make weather
   relevant. Prefer a specific address and complete date.
3. Build a candidate sequence and use `compute_routes` for each consecutive
   leg when the requested mode is supported.
4. Remove or reorder stops when route time, opening context, or user
   constraints make the sequence unrealistic.

## Validation and fallback rules

Place Google Maps sources immediately after the claims they support. Treat
walking routes as beta and show that warning when used. `compute_routes`
provides driving or walking distance and duration, not step-by-step navigation
or real-time traffic. If another travel mode is essential, use an appropriate
transit workflow or state the limitation. Do not claim live opening, booking,
accessibility, or ticket availability unless the returned data directly
supports it. If a place is ambiguous, ask the user to choose.

## Output contract

Return a timed plan with stop purpose, suggested duration, travel mode and
estimated leg time, weather implications, and practical buffers. Include
returned Maps links and attribution, then list the assumptions most likely to
require rechecking.

## Safety and side effects

This workflow is read-only and does not reserve venues or purchase travel.
Avoid exposing precise private addresses unless they are necessary for the
user's requested route.
