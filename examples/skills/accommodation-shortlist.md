# Accommodation Shortlist

## Use when / do not use when

Use this workflow to compare vacation rentals or accommodation against dates,
guest needs, budget, amenities, travel-time anchors, and nearby essentials. Do
not use it to book, contact a host, guarantee availability, or provide a
generic destination itinerary.

## Inputs to resolve

Collect the most specific destination, one date mode, guest counts including
children, infants, and pets, budget basis, bedrooms or beds, must-have
amenities, trip purpose, and one or more important anchors such as a venue,
beach, office, or station.

## Tool workflow

The upstream names below are capability labels. Select the currently advertised
MCP tool whose description identifies that upstream capability.

1. Call `airbnb_search` with exactly one mutually exclusive date mode and the
   resolved constraints. Search no more than two result pages unless the user
   asks for broader coverage.
2. Select the strongest three to five candidates from explicit constraints,
   not rating alone.
3. Call `airbnb_listing` for each candidate with identical fixed dates and
   guest counts to verify date-specific price, amenities, house rules,
   description, and location.
4. Use `maps_distance_matrix` to compare each candidate against the user's
   anchors with the relevant transport mode.
5. Use `maps_search_places` and `maps_place_details` only for requested nearby
   essentials or attractions.

## Validation and fallback rules

Refetch every shortlisted listing whenever dates or guest counts change. Treat
price and availability as time-sensitive and identify missing fees or rules as
unknown. Do not infer exact location, accessibility, safety, or legal status
from a listing summary. OpenBnB is community-operated, is not affiliated with
or endorsed by Airbnb, and depends on public upstream pages that may change.

## Output contract

Return a trade-off table with candidate, date-specific price basis, capacity,
must-have fit, rating when available, travel times to anchors, nearby
essentials, important rules, and caveats. Recommend the best fit by the user's
priorities and include direct provider links for final verification.

## Safety and side effects

This workflow is read-only and cannot book. Direct the user to the provider to
verify the latest total price, availability, cancellation policy, and listing
details before making a purchase.
