# Personal Spending Review

## Use when / do not use when

Use this workflow for a private, read-only review of Monzo balances, pots, and
transactions over an explicit period. Do not use it for transfers, pot
movements, annotations, tax advice, investment advice, or claims of a complete
financial position.

## Inputs to resolve

Confirm the review period, currency or account scope, desired comparison,
whether transfers or refunds should be separated, and the decision the user
wants to make. Never guess the account when multiple accounts are returned.

## Tool workflow

The upstream names below are capability labels. Select the currently advertised
MCP tool whose description identifies that upstream capability.

1. Call `monzo_connection_status` before requesting financial data.
2. Use `monzo_list_accounts` and ask the user to select the explicit account
   when needed.
3. Use `monzo_get_balance` and `monzo_list_pots` only when they support the
   requested review.
4. Call `monzo_list_transactions` with bounded dates and pages. Default to a
   recent 30-day review only when the user has not requested another period.
5. Use `monzo_get_transaction` only to clarify a small number of material or
   ambiguous entries.

## Validation and fallback rules

Convert integer minor units using each response's currency; never assume GBP.
Separate spending, income, transfers, refunds, and excluded entries where the
data permits. Compare the same account and equivalent periods unless the user
explicitly requests otherwise. Do not invent merchant categories or treat
pending transactions as settled. State when pagination or missing context
makes totals incomplete.

## Output contract

Lead with the requested totals and comparison, then show bounded category or
merchant groups, notable changes, recurring-looking items labelled as
observations, and questions raised by ambiguous entries. Avoid raw account or
transaction IDs unless the user needs one to disambiguate.

## Safety and side effects

Financial data is highly sensitive and may be retained in chats, runs,
transcripts, and tool events. Minimize raw detail. This workflow is read-only:
never call annotation, deposit, withdrawal, payment, or transfer capabilities.
