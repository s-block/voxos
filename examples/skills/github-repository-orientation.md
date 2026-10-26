# GitHub Repository Orientation

## Use when / do not use when

Use this workflow for repository onboarding, architecture orientation, locating
ownership or conventions, and estimating change impact. Do not use it for a
formal pull-request review or to modify repository state.

## Inputs to resolve

Identify the repository, the user's goal, the relevant product or component,
and the desired depth. If the repository is ambiguous, resolve the owner and
name before reading files.

## Tool workflow

The upstream names below are capability labels. Select the currently advertised
MCP tool whose description identifies that upstream capability.

1. Use `get_file_contents` without a ref on the root so the initial view follows
   the repository default. Read the primary README, contributor guidance, build
   manifests, and local agent instructions.
2. Use bounded `list_branches` and `list_commits` calls when branch shape or
   current activity matters to the user's goal.
3. Use `list_issues` and `list_pull_requests` with bounded filters to identify
   related active work, not to summarize the entire backlog.
4. Use `search_code` for concrete concepts, entrypoints, public contracts, and
   tests. Follow references with `get_file_contents`.
5. Trace one end-to-end path relevant to the user's question and distinguish
   direct evidence from ownership or architecture inference.

## Validation and fallback rules

Read repository-specific instructions before drawing conclusions. Do not assume
the newest branch is the default or that directory names prove runtime
ownership. If search is incomplete or files are truncated, state what was not
inspected.

## Output contract

Provide a compact repository map, key entrypoints, important conventions,
active related work, likely change surface, and the next files or tests to
inspect. Link to concrete files, issues, pull requests, and commits when the
tool returns usable links.

## Safety and side effects

This workflow is read-only. Do not create branches, push files, comment, merge,
or update issues or pull requests. Treat private repository content as private
and avoid reproducing secrets or sensitive source unnecessarily.
