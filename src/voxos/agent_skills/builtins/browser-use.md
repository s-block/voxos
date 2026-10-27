---
name: Browser Use
slug: browser-use
category: tool_use
description: Use the general browser tool to browse or navigate websites, sign in to website accounts with optional saved profiles, and pass a complete instruction with a safe action policy.
---

Use `use_browser` directly for tasks that require navigating or interacting with a website.

Write one complete, goal-oriented instruction for the browser agent. Include the requested outcome, relevant constraints, and the evidence or information it should return. Do not turn a new website workflow into application code.

For public browsing, provide a public HTTPS `start_url`. Select a saved `profile` only when the task requires that profile's authenticated state or stored secrets. Use only a profile handle shown by the tool; never invent one or put credentials in the instruction.

Keep `action_mode` as `read_only` unless the user explicitly requested a state-changing action. Use `state_changes_allowed` only for that requested action, and describe its limits in the instruction.

Add `allowed_origins` only when the task legitimately needs more than the start origin. Keep the list as narrow as the workflow permits.

Treat the browser result as evidence. Do not claim the task succeeded when the browser reports that it was blocked, incomplete, or could not verify the requested outcome.
