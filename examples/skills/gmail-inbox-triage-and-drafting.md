# Gmail Inbox Triage And Drafting

## Use when / do not use when

Use this workflow to find and group relevant Gmail threads, identify actions,
propose labels, and prepare reply drafts. Do not use it for bulk deletion,
sending mail, autonomous inbox reorganization, or unrelated mailbox browsing.

## Inputs to resolve

Confirm the mailbox objective, search window, people or topics, urgency rules,
what counts as actionable, and whether the user wants analysis only, labels, or
drafts.

## Tool workflow

The upstream names below are capability labels. Select the currently advertised
MCP tool whose description identifies that upstream capability.

1. Use `gmail.search_threads` with the narrowest useful Gmail query and minimal
   returned content.
2. Call `gmail.get_thread` only for relevant candidates. Avoid opening unrelated
   conversations.
3. Group results into urgent action, waiting, reference, and no action, adapting
   those groups to the user's rules.
4. Use `gmail.list_labels` before proposing an existing label.
5. For `gmail.label_thread` or `gmail.create_draft`, show the exact target and proposed
   change, including recipients, subject, and complete draft body where
   applicable. Then obtain explicit confirmation immediately before each
   mutation.

## Validation and fallback rules

Preserve participants, chronology, quoted context, and unresolved commitments.
Use `replyToMessageId` when a confirmed draft is intended as a reply. Do not
infer intent from a subject alone. Never claim a draft was sent. Remember that
a thread label also applies to future messages added to that thread. If search
results are ambiguous, narrow the query or ask the user before reading more
mail.

## Output contract

Return a concise triage table with thread, reason, action, owner, and deadline
when present. Put proposed label changes and complete draft text in a separate
confirmation section. Clearly identify completed mutations versus suggestions.

## Safety and side effects

Mailbox contents are private and may enter model context. Minimize quoted
content and sensitive personal data. Do not mutate labels or create a draft
without immediate explicit confirmation, and respect a denied runtime grant.
