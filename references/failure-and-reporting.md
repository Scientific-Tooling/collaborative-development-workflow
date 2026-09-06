# Failure Handling and User Reporting

Read this reference when delegated work fails, is blocked, or the task is handed off.

## Failure and fallback

- If child tools are unavailable, say so. The main agent may plan/implement sequentially
  only when no child writer is live or claimed, and cannot substitute for the mandatory
  independent final reviewer.
- NEEDS_INPUT and PARTIAL are explicit states. Answer the same live invocation only
  with one bounded response; material user choices return to the planning gate. Resume
  PARTIAL only through the documented resumable CAS with preserved artifacts and fixed
  budget; otherwise re-plan or stop.
- Wait with the same foreground invocation and remaining monotonic deadline. A timeout,
  empty output, wrapper yield, silence, or previous_status is not failure or permission
  to retry, replace, take over, unlock, or mutate.
- For a reviewer, use the state sequence in review-recovery.md. A close/cancel request
  is not a confirmed stop. Retain the review lock until a runtime terminal event or a
  permanently unaddressable fail-closed state; otherwise record REVIEW_BLOCKED.
- Never claim delegation, independent review, acceptance, or a check that did not occur.
- Do not push, deploy, or mutate external systems while recovering.

## Final user report

Report:

- requested outcome and changed paths;
- implementation mode and any delegated milestones;
- review snapshot identity, review result/rounds, and any blocked proof;
- focused and full validation commands with outcomes;
- local commit, if created;
- remaining risks, caveats, user decisions, or explicit authorization still needed.

If acceptance is blocked, state the exact missing identity, proof, stop confirmation,
validation, or user decision. Do not present partial work as complete.
