# Historical evidence snapshots

Files in this directory are retained only as dated audit history. They are **not** the source of truth for current production status.

Current operational proof must come from the corresponding GitHub Actions run and its sanitized artifact/index entry. A historical file may contain `NOT_PROVEN`, `MISSING`, or other states that were true only for the executor and timestamp recorded in that snapshot.

Do not copy a historical snapshot back to a runtime/current-evidence path and do not use it to claim present production, backup, restore, RPO, RTO, provider coverage, or score status.
