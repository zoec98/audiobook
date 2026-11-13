# Whole-epic code review

## Review setup

- Fixed baseline: `d26235a0ec63cacf22838f2f1c9be763286c6e45`.
- Reviewed head before remediation: `1fed60daa57caa3eb45c3d7af71c34eea9186b89`.
- Diff: `git diff d26235a0ec63cacf22838f2f1c9be763286c6e45...1fed60daa57caa3eb45c3d7af71c34eea9186b89`.
- Spec sources: `user-stories.md`, `tickets.md` (including the user-directed
  T-14 extension), and `interfaces.md` in this epic.
- No issue references, `docs/agents/issue-tracker.md`, `AGENTS.md`,
  `CONTRIBUTING*`, or `CODING*` files were found. The review used the approved
  local specifications and the ticket plan's public-interface, TDD, and
  provider-boundary requirements. Per the code-review skill, run
  `/setup-matt-pocock-skills` to configure issue-tracker access.

The Standards and Spec axes were reviewed independently in parallel.

## Standards

No hard documented-standard violations were found. The diff follows the ticket
plan's public test seams and provider boundary; tests use controlled transports
and public CLI/module interfaces.

- **Judgement call — possible Duplicated Code:** `src/audiobook/application.py`
  and `src/audiobook/store.py` repeat the temporary-file, flush/fsync,
  atomic-replace, and cleanup pattern. The review noted `temporary.flush()` /
  `os.fsync(...)` / `os.replace(...)` in both paths.

## Spec

- **T-11 refresh failure guarantee was partial.** The spec requires: “Failed
  refreshes preserve previous final MP3s and leave no new completion record.”
  Before remediation, `application.py` replaced the destination before calling
  `save_completion`; if that write failed, the run failed after replacing the
  previous MP3. No other spec findings were reported.

## Disposition and remediation

- **Possible duplicated atomic-write code — deferred.** This remains a
  judgement call, not a documented-standard breach. The application writes
  plan artifacts while the store writes validated cache/completion artifacts;
  no inconsistent behavior was found. A shared writer would add a cross-module
  abstraction without a demonstrated bug, so no refactor was made.
- **Completion-write failure — fixed.** Commit
  `a22caf2a368c14c2c9828ec350c53e981a30e161` stages the replacement, preserves
  an existing MP3, and restores it (or removes a newly created output) if
  digesting or saving the completion record fails. The CLI reports the storage
  failure. Regression `test_completion_write_failure_preserves_previous_mp3`
  went red before the fix and now verifies the old MP3 and completion record
  remain byte-for-byte unchanged.
- The fix merged into integration as
  `10a0463c5be684a858ce61e1be3b59f209e1ad9d`.

## Final verification

- Merged integration: `ruff format --check src tests` passed (22 files),
  `ruff check src tests` passed, `ty check src tests` passed, and pytest passed
  (**121 tests**). Local loopback provider peers were used; no live provider
  calls were made.
- T-12's fresh offline `uv sync`, CLI help inspection, and verification record
  passed before T-13. The audio-listening gate remains explicitly pending.
- T-14's offline check of the user's 12 chapters found 53 thematic breaks,
  zero thematic-break diagnostics, and zero divider lines in narration.

**Totals:** Standards: 1 possible smell, no hard violations. Spec: 1 guarantee
gap, fixed. The highest-impact finding was the failed-refresh output replacement;
the regression now passes on the merged integration result.
