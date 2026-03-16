# API Error Mapping Plan (Proposal Only)

This document is a proposal for implementing strict endpoint-level error
contracts and tests. It intentionally contains no code changes.

## Goal

Guarantee that each endpoint returns deterministic HTTP status + `error_code`
 combinations for all expected error scenarios.

## Scope (Proposed)

1. Add a centralized error catalog module:
   - symbolic constants for each `error_code`
   - canonical status mapping table
2. Add endpoint-level contract tests:
   - success path
   - each declared failure path
   - malformed payload behavior
3. Add static validation:
   - check all returned `error_code` values are declared
   - check no duplicate semantic mappings
4. Add docs sync check:
   - fail CI if route contract docs and code mapping diverge

## Implementation Steps (Proposed)

1. Introduce `backend/src/infostream/api/error_catalog.py`.
2. Replace hard-coded string literals in routes with catalog constants.
3. Add `tests/test_api_error_contracts.py` with one test matrix per endpoint.
4. Add make target for contract tests and include in `make test`.

## Risks and Mitigations

- Risk: Route behavior currently depends on framework-level validation.
  Mitigation: move strict-contract endpoints to explicit manual parsing.
- Risk: Contract tests become brittle.
  Mitigation: use matrix helpers and shared fixtures to reduce duplication.

## Rollout Plan

1. Implement for read-only endpoints first.
2. Expand to mutating endpoints.
3. Lock with CI gating once all endpoints are covered.
