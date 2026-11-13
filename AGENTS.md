# AGENTS.md

## Purpose

This document defines how human and AI agents should contribute to this repository.
The goals are reliability, clarity, and long-term maintainability.

## Scope

These rules apply to all repository code unless a more specific local guide exists in a subdirectory.

## Core Engineering Standards

- Prefer readable, modular code over clever or compact code.
- Keep functions small and single-purpose.
- Favor explicit control flow and clear naming.
- Design for easy future modification by someone unfamiliar with the original implementation.
- Follow `KISS`, `DRY`, and `YAGNI` principles.
- Avoid hidden side effects; make dependencies and state transitions obvious.

## Required Workflow For New Code

1. Understand the existing module boundaries and call paths before editing.
2. Implement the smallest safe change that satisfies the requirement.
3. Add or update tests in the same change.
4. Add or update function/file documentation and comments to explain behavior and integration points.
5. Run relevant test suites and static checks before finalizing.

## Testing Policy (Mandatory)

When adding or changing production code, agents must add or update tests in the same change.

- New behavior requires tests that would fail without the change.
- Bug fixes require regression tests.
- Refactors require tests that prove behavior remains correct.
- Prefer deterministic tests with clear setup and assertions.
- Cover success paths, error paths, and important edge cases.

If tests cannot be added (rare), explicitly document why in the change notes and propose follow-up test work.

## Readability And Maintainability

- Organize code into cohesive modules with clear responsibilities.
- Do not let one file accumulate unrelated responsibilities.
- Remove dead code and obsolete branches when safe.
- Avoid hard-coded values; promote meaningful constants/config where appropriate.
- Keep public interfaces stable and predictable.

## Comments And Documentation Requirements

Comments are required where intent is not obvious from code alone.

- Write comments that explain *why*, invariants, tradeoffs, and non-obvious constraints.
- Do not add low-value comments that simply restate the code.
- Keep comments current when behavior changes.

### File-Level Documentation (Required)

Each source file should include a brief top-level description (module docstring or header comment) that states:

- What the file is responsible for.
- How it fits into the system.
- Which major modules/services call into it.
- Which major modules/services it calls or depends on.

### Function-Level Documentation (Required)

Each non-trivial function should include documentation (docstring or equivalent) that states:

- Purpose and expected behavior.
- Inputs, outputs, and side effects.
- Error behavior and important edge cases.
- Where the function is used from (high-level call sites).
- Key dependencies (database, network/API, filesystem, scheduler, etc.).

## Suggested Documentation Template

Use and adapt the following template for modules and complex functions:

```python
"""<Short purpose statement>.

Role in system:
- <How this module/function fits into end-to-end flow>

Called by:
- <Primary callers/routes/services>

Calls into / depends on:
- <Primary dependencies>

Notes:
- <Invariants, constraints, failure modes, or performance considerations>
"""
```

## Change Quality Checklist

Before finalizing, verify:

- Tests were added or updated with the code change.
- Code is modular and understandable without deep tribal knowledge.
- File and function documentation explains system fit and interactions.
- Non-obvious logic has clear, accurate comments.
- No unrelated refactors or incidental complexity were introduced.

## Preferred Outcomes

A future maintainer should be able to:

- Understand why the code exists.
- Trace how data and control flow through modules.
- Modify behavior safely with confidence.
- Extend features without rewriting large sections.
