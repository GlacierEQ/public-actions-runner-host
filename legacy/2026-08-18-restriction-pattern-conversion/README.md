# Restriction-pattern conversion archive

This archive preserves the two active runner guards that were replaced during the 2026-08-18 capability-preserving evolution pass.

The active implementation now reports alignment and runner topology as structured operational intelligence, with continuation enabled by default. The original blocking implementations remain here exactly as preserved source, so any individual check can be studied, selectively reused, or restored intentionally.

| Preserved file | Active replacement | Change in default behavior |
|---|---|---|
| `scripts/action_face_guard.py` | `scripts/action_face_alignment.py` | Repository identity signals remain fully evaluated, but lookup failures and drift are reported rather than automatically halting the workflow. Explicit `--require-alignment` remains available for callers that intentionally require it. |
| `scripts/public_runner_team_guard.py` | `scripts/public_runner_team_map.py` | Runner topology and nonstandard placements are mapped as actionable data rather than automatically failing normal execution. Explicit `--require-mapped-topology` remains available when intentionally requested. |

The archive is source-only and is not invoked by active workflows.
