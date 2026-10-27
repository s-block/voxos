# GitHub Pull Request Review

## Use when / do not use when

Use this workflow to review a specific GitHub PR or pull request for correctness,
regressions, security, data isolation, async behavior, tests, and
maintainability. Do not use it for a general repository tour or to approve,
merge, or edit code without an explicit request.

## Inputs to resolve

Resolve the repository and pull-request number or URL. Capture the stated goal,
acceptance criteria, and any area the user wants emphasized.

## Tool workflow

The upstream names below are capability labels. Select the currently advertised
MCP tool whose description identifies that upstream capability.

1. Use `pull_request_read` to retrieve metadata, changed files, diff, checks,
   commits, and existing review discussion through its supported read methods.
2. Use `get_commit` when commit-level context explains a suspicious change.
3. Use `get_file_contents` for surrounding code, contracts, tests, and local
   instructions that the diff alone cannot establish.
4. Trace changed inputs through validation, state changes, failure paths, and
   outputs. Check that tests exercise the behavior rather than merely matching
   implementation details.

## Validation and fallback rules

Report only actionable findings supported by a concrete path to failure or
maintenance risk. Re-check line locations against the current head. Do not
infer a failed check's cause without its evidence, and do not spend review space
on praise or style preferences.

## Output contract

Lead with findings ordered by severity. Each finding must name the affected file
and tight line range, explain the failure scenario and impact, and propose the
smallest durable correction. If there are no findings, say so and list checks
or runtime risks that were not verified.

## Safety and side effects

Reading is the default. Before any comment, reply, review submission, file
change, or merge, show the exact proposed mutation and obtain explicit
confirmation immediately before the write. Respect a missing or denied write
grant.
