# Meeting Preparation Brief

## Use when / do not use when

Use this workflow to prepare for a specific meeting by combining the Google
Calendar event with clearly related Gmail context. Do not use it to create or
change events, send mail, or search broadly through a mailbox without a meeting
anchor.

## Inputs to resolve

Identify the meeting by title, participant, or approximate time, plus the user's
preparation goal and any topic that should be excluded. Confirm the timezone
when dates could be ambiguous.

## Tool workflow

The upstream names below are capability labels. Select the currently advertised
MCP tool whose description identifies that upstream capability.

1. Use `calendar.list_events` for a bounded time window, applying its supported
   filters to a distinctive title or participant when available.
2. Use `calendar.get_event` on the selected event to establish time, attendees,
   description, location, and meeting links.
3. Build a narrow Gmail query from the confirmed title, participants, and
   distinctive topic. Use `gmail.search_threads` with minimal content.
4. Call `gmail.get_thread` only for messages clearly related to the meeting.
5. Reconcile event notes and email chronology into decisions, commitments, open
   questions, and attendee-specific context.

## Validation and fallback rules

If multiple events match, ask the user to choose before reading mail. Do not
assume every thread containing an attendee is relevant. Attribute commitments
to their source and label inferred agenda items as suggestions.

## Output contract

Return the event facts, a proposed agenda, decisions already made, open
questions, commitments with owners or dates when stated, attendee context, and
the user's recommended preparation actions. Keep private email excerpts to the
minimum needed.

## Safety and side effects

This workflow is read-only. Do not create, update, delete, or respond to events,
and do not draft or send email. Treat attendee and mailbox data as private.
