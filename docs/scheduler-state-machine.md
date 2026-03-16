# Scheduler State Machine

This document describes the current scheduler loop behavior implemented in
`backend/src/infostream/scheduler/service.py`.

## Phases

1. `stabilizing`
   - Active during daemon startup.
   - Scheduler waits for `startup_stabilization_seconds` before first tick.
2. `ready`
   - Active during normal operation.
   - Scheduler evaluates schedule every `scheduler_tick_seconds`.

## Tick Logic

Each tick:

1. Reads `settings.schedule`.
2. Ignores the one-shot sentinel value.
3. Parses RFC3339 schedule timestamp.
4. Compares schedule against current UTC time.
5. Launches a scheduled run if due and not deduped.

## Dedupe Behavior

- For one-shot schedules (`run_every = false`), the scheduler stores the last
  triggered schedule string.
- It will not retrigger if the stored schedule value has not changed.

## Clock Regression Handling

- If system wall clock moves backwards between ticks, the one-shot dedupe marker
  is reset.
- This keeps scheduling behavior predictable after manual clock changes.
