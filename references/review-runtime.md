# Review Runtime, Waiting, and Classification

Normative runtime mechanics for bounded waits, immutable review artifacts, deadlines, binding, provenance, report scanning, and terminal classification. Read only for delegated review/runtime edge cases.

### Delegated-agent waiting semantics

Use foreground/blocking execution as the default, following the normal Claude Code subagent pattern: spawn one bounded agent for one assignment, then let the parent wait for the result. The parent should not repeatedly inspect the child, read a moving diff, check logs, or send “are you alive?” messages while that wait is active. A child’s silence is expected during this period.

At snapshot freeze, before `pre_spawn`, task claim, or any spawn attempt, capture one validated monotonic `snapshot_budget_started_at` and atomically persist it with the immutable snapshot identity. Set `snapshot_deadline_at = snapshot_budget_started_at + review_wait_budget` once; pre-spawn preparation, binding, waiting, cancellation, decision handoff, and replacement overhead all consume this same hard budget. `post_spawn` may only verify and bind the persisted deadline, never derive a later one from `spawn_confirmed_at`. Allocate the original reviewer an `attempt_deadline_at = min(snapshot_deadline_at, snapshot_budget_started_at + review_initial_budget)`; reserve `review_replacement_decision_reserve_budget + review_spawn_reserve_budget + review_replacement_min_budget` inside the same snapshot budget for a possible replacement. A binding failure uses one combined full-row CAS that records the exact target, binding-failure timing, `CANCEL_REQUESTED`, and the cancellation idempotency key together; a separate cancellation CAS may reconcile a winner only by another complete-row CAS. This removes the interval in which binding timing and cancellation overlay could disagree. The waiting UI may show a passive line such as `reviewer <id> · elapsed 04:12 · remaining 20:48`, calculated only from the parent’s local timing metadata. A passive UI timer or progress event must not call `wait_agent`, query status, wake the parent model, or send input to the child. If no passive progress facility exists, show timing only at wait start, at wrapper continuation boundaries, and at terminal return; do not create short polling waits or repeated progress messages solely to refresh the display. Record `budget_remaining_at_spawn` and derive every later remaining value from the same monotonic clock and fixed snapshot deadline; never restart the clock for a continuation or replacement.

When the interface supports a blocking wait, pass the remaining `attempt_deadline_at` budget in that call. If a wrapper yields a continuation, the wrapper should automatically resume the same wait; if the parent must resume it, use the remaining budget and treat this as one logical wait, not a new polling cycle. A transport timeout or wrapper yield is only a wait observation: it does not authorize a retry, replacement, takeover, review unlock, or mutation of the child’s workspace. Update `elapsed`/`remaining` from the local monotonic clock at that continuation boundary, then resume the same invocation. Treat a tool result such as `timed_out: true` with no terminal status, an empty status map, or `No agents completed yet` the same way while `attempt_deadline_at` has not passed. The parent must not issue a status query, inspect artifacts, read a moving diff, or send a liveness message merely because a wait returned without a terminal result. The wrapper should emit one passive progress record per natural continuation at most; the parent should not narrate or poll for liveness.

An interrupt, close, cancel, or `close_agent` call is a request, not proof that the
invocation stopped. If the API returns only `previous_status`, that value is
pre-request evidence and cannot authorize replacement. During the one bounded recovery
window, join the same invocation once more through the platform’s blocking wait and
accept cancellation only when it returns a runtime-owned terminal `shutdown`/stop event
with the exact run/task/attempt/invocation/token and target identity, or when the
platform explicitly documents that the close operation atomically returns that same
terminal proof. Do not turn this join into repeated status polling. If no such proof is
available before the fixed recovery/snapshot boundary, retain the cancellation overlay,
owner, and review lock, mark the snapshot `REVIEW_BLOCKED`, quarantine late output, and
do not claim a replacement slot or spawn another reviewer.

Background execution is an explicit exception for genuinely independent work. The parent may continue only with work that does not consume the child’s result, write scope, mutable workspace, or review lock. Before integrating, advancing a dependent milestone, or accepting a review, perform one blocking join and wait for the child’s terminal result.

Do not replace the configured reviewer slice with an ad-hoc shorter timeout. When a
replacement is authorized, its blocking wait receives the remaining fixed replacement
budget (including the permitted `review_replacement_min_budget`, such as `1500s`);
if the tool's per-call maximum is shorter, use continuation calls for that same logical
wait and account for each continuation against the one snapshot deadline.

Only after the current `attempt_deadline_at` is exhausted without a terminal result may the parent perform a single bounded recovery check for a normally bound invocation. There is one earlier safety exception: if `post_spawn` or binding fails after the child may have started, immediately use the combined binding-failure/cancellation full-row CAS to record `SPAWN_UNCONFIRMED` provenance, retain every writer/review lock, persist the exact target and timing, and set `CANCEL_REQUESTED`; only then issue one close/interrupt/cancel request to that target. Do not wait for the ordinary attempt deadline. A close request is not confirmation: retain `binding_failure_provenance=SPAWN_UNCONFIRMED` and `overlay=CANCEL_REQUESTED` until the runtime reports a terminal shutdown/cancellation or another terminal state with a timestamp. Bound this stop-confirmation wait by `binding_failure_recovery_deadline = min(snapshot_deadline, binding_failed_at + review_recovery_grace_budget)`; when it expires without confirmation, quarantine later output and prohibit replacement, takeover, and unlock. If no runtime target can be addressed, reconcile the spawn once through the platform’s authoritative recovery mechanism and keep the lock; do not retry, take over, or unlock. A concurrent provisional terminal payload may still use the exact transport late-bind rule, but no unbound child may enter the ordinary wait or replacement path. The recovery check and cancellation confirmation must not extend `snapshot_deadline` or create budget that was not already reserved.
Validate every timestamp, duration, and total-budget relationship before doing any deadline arithmetic. If an operand is missing, non-finite, negative, reversed, or leaves less than the configured replacement gate, mark timing `UNVERIFIED`, retain all affected locks, and stop before acceptance, replacement, or takeover.

Use this recovery sequence for a reviewer or any other foreground delegated task:

```text
same_task_resume_deadline_formula_valid(values):
    require replacement_index in {0, 1} and finite_monotonic(snapshot_deadline_at) and
        finite_monotonic(recovery_deadline_before_resume)
    if replacement_index == 0:
        cutoff = min(recovery_deadline_before_resume,
            snapshot_deadline_at - review_replacement_decision_reserve_budget -
            review_spawn_reserve_budget - review_replacement_min_budget)
    else:
        require recovery_deadline_before_resume == snapshot_deadline_at
        cutoff = snapshot_deadline_at
    require resumed_attempt_deadline == cutoff and
        resumed_recovery_deadline == cutoff and cutoff > 0
    return true

validate_deadline_inputs(values):
    require clock_source is one verified monotonic source and every supplied timestamp
        is a finite sample from that same source
    unbound_runtime_stop defaults to no; only a dedicated runtime interruption or
        runtime-terminal validator may set it to yes after proving the reserved
        target, channel, association, and overlay NONE or CANCEL_REQUESTED; when the
        overlay is CANCEL_REQUESTED, binding_failure_provenance must be
        SPAWN_UNCONFIRMED for an unbound binding-failure path
    unbound_stop_preceded_cancel_request defaults to no; the dedicated runtime
        validator may set it to yes only after proving the exact unbound event/row race
    runtime_autonomous_stop defaults to no; only a dedicated runtime interruption or
        runtime-terminal validator may set it to yes, and only for an exact bound agent
        or reserved unbound target while the overlay is NONE or CANCEL_REQUESTED as
        documented. A caller validating an unbound event must also
        pass unbound_runtime_stop=yes; these flags are runtime/parent-derived and never
        child-supplied.
    runtime_stop_preceded_cancel_request defaults to no; a dedicated runtime-event
        validator may set it to yes only after proving that the exact runtime stop
        completed before a concurrent parent CANCEL_REQUESTED CAS. The predicate is
        valid for bound and reserved-unbound runtime events; the unbound-specific
        predicate below remains a stricter alias.
    require each optional timing field is exactly UNSET/UNKNOWN as documented or a
        finite sample; a non-finite value must not be treated as absent
    require every duration is finite and nonnegative
    require review_wait_budget > 0 and review_initial_budget >= 1800s
    require review_replacement_min_budget >= 1500s
    require review_wait_budget >= review_initial_budget +
        review_recovery_grace_budget + review_replacement_decision_reserve_budget +
        review_spawn_reserve_budget + review_replacement_min_budget
    same_task_resume defaults to no; when it is yes, require replacement_index in {0, 1}
        and resume_cutoff is a finite monotonic sample; for replacement_index=0 it
        equals min(recovery_deadline_before_resume,
            snapshot_deadline - review_replacement_decision_reserve_budget -
            review_spawn_reserve_budget - review_replacement_min_budget), while for
        replacement_index=1 it equals snapshot_deadline and the pre-resume recovery
        deadline already equals that fixed snapshot deadline
    require snapshot_budget_started_at is a finite monotonic sample and
        snapshot_deadline == snapshot_budget_started_at + review_wait_budget
    require attempt_deadline and recovery_deadline are supplied as persisted absolute
        samples, not recomputed from a later child event
    require deadline_at is a finite monotonic sample, equals the current row's
        `deadline_at`, and equals the applicable `attempt_deadline`; it is validated
        independently and is not an alias/fallback for `snapshot_deadline`
    if replacement_index > 0:
        require replacement_index == 1 and finite_monotonic(fixed_snapshot_deadline)
        require fixed_snapshot_deadline == snapshot_budget_started_at + review_wait_budget
        require fixed_attempt_deadline == fixed_snapshot_deadline and
            fixed_recovery_deadline == fixed_snapshot_deadline
        require attempt_deadline == fixed_attempt_deadline and
            recovery_deadline == fixed_recovery_deadline
    elif same_task_resume is yes:
        require attempt_deadline == resume_cutoff and
            recovery_deadline == resume_cutoff
    else:
        require attempt_deadline == min(snapshot_deadline,
            snapshot_budget_started_at + review_initial_budget) and
            recovery_deadline == min(snapshot_deadline,
                attempt_deadline + review_recovery_grace_budget)
    require snapshot_budget_started_at <= spawn_requested_at <= monotonic_now
    if spawn_confirmed_at is finite:
        require spawn_requested_at <= spawn_confirmed_at <= monotonic_now
        require started_at is UNKNOWN/UNSET or
            spawn_confirmed_at <= started_at <= monotonic_now
        require binding_failed_at is UNSET or
            spawn_confirmed_at <= binding_failed_at <= monotonic_now
    else:
        require binding_mode is transport_bound_provisional and
            post_spawn_binding_failed and child_may_have_started and
            parent/runtime deadline_derivation_proof is present
        require started_at is UNKNOWN/UNSET and
            (binding_failed_at is UNSET or
                spawn_requested_at <= binding_failed_at <= monotonic_now)
    require binding_failure_recovery_deadline_at is UNSET iff binding_failed_at is UNSET
    if binding_failed_at is finite:
        if replacement_index > 0:
            binding_deadline_base = fixed_snapshot_deadline
        elif spawn_confirmed_at is finite:
            binding_deadline_base = snapshot_deadline
        else:
            binding_deadline_base = parent/runtime derived snapshot_deadline
        require binding_failure_recovery_deadline_at == min(
            binding_deadline_base,
            binding_failed_at + review_recovery_grace_budget
        )
    elif binding_failed_at is UNKNOWN:
        require binding_failure_recovery_deadline_at is UNVERIFIED and
            (cancel_requested_at in {UNKNOWN, UNSET} or
             finite_monotonic(cancel_requested_at))
        # UNKNOWN timing is a fail-closed stop record, not a value from which a
        # recovery deadline or terminal eligibility may be derived.
    else:
        require binding_failure_recovery_deadline_at is UNSET
    require prebinding_terminal_at is UNSET or
        spawn_requested_at <= prebinding_terminal_at <= monotonic_now
    require cancel_requested_at is UNSET or
        (spawn_confirmed_at if finite else spawn_requested_at) <=
            cancel_requested_at <= monotonic_now
    if cancel_confirmed_at is finite:
        if cancel_requested_at is finite:
            if runtime_autonomous_stop is yes and
               runtime_stop_preceded_cancel_request is yes:
                require spawn_requested_at <= cancel_confirmed_at <= monotonic_now
            else:
                require cancel_requested_at <= cancel_confirmed_at <= monotonic_now
        elif runtime_autonomous_stop is yes and unbound_runtime_stop is no:
            require spawn_confirmed_at is finite and
                spawn_confirmed_at <= cancel_confirmed_at <= monotonic_now
        elif runtime_autonomous_stop is yes and unbound_runtime_stop is yes:
            require spawn_confirmed_at is UNSET and
                spawn_requested_at <= cancel_confirmed_at <= monotonic_now
        else:
            reject: a cancellation confirmation without a request is invalid here
    else:
        require cancel_confirmed_at is UNSET
    return true only when all checks pass

if not validate_deadline_inputs(
       clock_source=clock_source,
       spawn_requested_at=spawn_requested_at,
       spawn_confirmed_at=spawn_confirmed_at,
       started_at=started_at,
       monotonic_now=monotonic_now,
       binding_failed_at=binding_failed_at,
       binding_failure_recovery_deadline_at=binding_failure_recovery_deadline_at,
       prebinding_terminal_at=prebinding_terminal_at,
       cancel_requested_at=cancel_requested_at,
       cancel_confirmed_at=cancel_confirmed_at,
       runtime_autonomous_stop=runtime_autonomous_stop,
       unbound_runtime_stop=unbound_runtime_stop,
       runtime_stop_preceded_cancel_request=runtime_stop_preceded_cancel_request,
       unbound_stop_preceded_cancel_request=unbound_stop_preceded_cancel_request,
       replacement_index=replacement_index,
       attempt_deadline=attempt_deadline,
       recovery_deadline=recovery_deadline,
       deadline_at=deadline_at,
       review_wait_budget=review_wait_budget,
       review_initial_budget=review_initial_budget,
       review_recovery_grace_budget=review_recovery_grace_budget,
       review_replacement_decision_reserve_budget=review_replacement_decision_reserve_budget,
       review_spawn_reserve_budget=review_spawn_reserve_budget,
       review_replacement_min_budget=review_replacement_min_budget,
       snapshot_budget_started_at=snapshot_budget_started_at,
       snapshot_deadline=snapshot_deadline,
       fixed_snapshot_deadline=fixed_snapshot_deadline,
       fixed_attempt_deadline=fixed_attempt_deadline,
       fixed_recovery_deadline=fixed_recovery_deadline
   ):
    mark timing UNVERIFIED; retain every lock and prohibit acceptance, replacement, and takeover
if replacement_index > 0:
    snapshot_deadline = fixed_snapshot_deadline
    attempt_deadline = fixed_attempt_deadline
    recovery_deadline = fixed_recovery_deadline
elif spawn_confirmed_at is finite:
    snapshot_deadline = snapshot_budget_started_at + review_wait_budget
    attempt_deadline = min(snapshot_deadline, snapshot_budget_started_at + review_initial_budget)
    recovery_deadline = min(snapshot_deadline, attempt_deadline + review_recovery_grace_budget)
else:
    snapshot_deadline, attempt_deadline, recovery_deadline =
        the exact parent/runtime deadline_derivation_proof values bound to
        snapshot_budget_started_at and the persisted snapshot_deadline
if (spawn_confirmed_at is finite and
       (spawn_requested_at > spawn_confirmed_at or
        spawn_confirmed_at > monotonic_now)) or
   (spawn_confirmed_at is finite and attempt_deadline < spawn_confirmed_at) or
   snapshot_deadline < attempt_deadline or
   recovery_deadline < attempt_deadline or
   (cancel_confirmed_at is finite and cancel_requested_at is finite and
       cancel_confirmed_at < cancel_requested_at and not
       (runtime_autonomous_stop is yes and
        runtime_stop_preceded_cancel_request is yes)) or
   any calculated deadline is not a finite monotonic sample:
    mark timing UNVERIFIED; retain every lock and prohibit acceptance, replacement, and takeover
replacement_gate = review_replacement_min_budget + review_spawn_reserve_budget
replacement_decision_deadline = snapshot_deadline - replacement_gate
persist replacement_decision_deadline on the original ledger row before any recovery
decision; it is the fixed latest request cutoff that preserves spawn and effective-review
capacity. After a validated runtime stop confirmation, set
replacement_decision_latest_at = min(replacement_decision_deadline,
runtime_stop_confirmed_at + review_replacement_decision_reserve_budget). The latter
effective cutoff enforces the full decision reserve from the actual stop confirmation;
both values and replacement_stop_confirmed_at are parent/runtime-owned gate inputs,
never child-supplied and never recomputed from a later attempt.
if not finite_monotonic(replacement_decision_deadline) or
   replacement_decision_deadline < snapshot_budget_started_at or
   replacement_decision_deadline > snapshot_deadline:
    mark timing UNVERIFIED; retain every lock and prohibit acceptance, replacement, and takeover
role = stored_role_from_ledger

if post_spawn_binding_failed and child_may_have_started:
    if current_task_row.reserved_runtime_target is NONE:
        target_reconciliation = reconcile_spawn_once_through_authoritative_runtime(
            exact run/task/attempt/invocation/token
        )
        if target_reconciliation is NO_CHILD_CONFIRMED:
            require a validated monotonic terminal sample and emit a closed
            RUNTIME_TERMINAL_EVENT with STATUS=spawn_failed, CHILD_STARTED=no,
            target=NONE, and independent runtime terminal identity; materialize the
            original/replacement spawn-failure terminal through its complete CAS
            and stop
        no_target_failure = record_unaddressable_spawn_compare_and_set(
               current_row=current_task_row,
               exact run/task/attempt/invocation/token, owner, lock,
               binding_failed_at=UNKNOWN,
               binding_failure_recovery_deadline_at=UNVERIFIED,
               cancel_requested_at=UNKNOWN
           )
        if no_target_failure.kind is CAS_LOST:
            consume the authoritative winner and stop without a stale stop request
        require no_target_failure.kind is RECORDED
        current_task_row = no_target_failure.authoritative_row
        # The complete no-target CAS has converted the row to the explicit
        # UNADDRESSABLE/CANCEL_REQUESTED/BLOCKED safety state. There is no stop helper
        # call because no target is safe to address; only the authoritative
        # reconciliation path may later resolve the sentinel.
        retain UNADDRESSABLE/CANCEL_REQUESTED, REVIEW_BLOCKED, and the lock;
            stop without ordinary wait, recovery, replacement, takeover, or unlock
        return
    binding_failure_outcome = record_binding_failure_cancel_requested_compare_and_set(
        current_row=current_task_row,
        binding_failed_at=binding_failed_at,
        runtime_target=current_task_row.reserved_runtime_target,
        exact run/task/attempt/invocation/token, owner, lock, and snapshot/content identity
    )
    if binding_failure_outcome.kind is CAS_LOST:
        binding_failure_outcome = reconcile_binding_failure_cas_loss(
            binding_failure_outcome.authoritative_row,
            the same exact binding-failure sample and runtime target
        )
    if binding_failure_outcome.kind is not RECORDED:
        consume the authoritative winner; retain its owner and lock and stop without
            computing timing, issuing a stale stop, or entering ordinary wait/recovery
        return AUTHORITATIVE_WINNER
    current_task_row = binding_failure_outcome.authoritative_row
    # This single complete-row CAS persists SPAWN_UNCONFIRMED provenance, the exact
    # target/timing, CANCEL_REQUESTED, and the idempotency key before this helper can
    # issue the one runtime stop. If a concurrent ordinary cancellation CAS won first,
    # reconcile it by a complete-row CAS that fills the binding fields; never trust a
    # sparse overlay-only winner or compute timing from the stale row.
    stop_outcome = stop_at_deadline_or_recovery_boundary()
    if stop_outcome.kind is AUTHORITATIVE_WINNER:
        return AUTHORITATIVE_WINNER without entering the ordinary wait or replacement path
    if binding_failed_at is missing or not finite_monotonic(binding_failed_at):
        # The named CAS has already persisted UNKNOWN/UNVERIFIED; never overwrite it
        # with a guessed sample after the stop request.
        retain the UNVERIFIED timing, every lock, and the no-acceptance/no-recovery/
            no-replacement/no-takeover rule; return REVIEW_BLOCKED
    binding_failure_recovery_deadline = min(snapshot_deadline, binding_failed_at + review_recovery_grace_budget)
    if not finite_monotonic(binding_failure_recovery_deadline) or
       binding_failure_recovery_deadline !=
           min(snapshot_deadline, binding_failed_at + review_recovery_grace_budget) or
       binding_failure_recovery_deadline < binding_failed_at or
       binding_failure_recovery_deadline > snapshot_deadline:
        mark timing UNVERIFIED; retain every lock and prohibit acceptance, replacement, and takeover
        return REVIEW_BLOCKED without entering the ordinary wait or replacement path
    wait only until binding_failure_recovery_deadline for a terminal event or confirmed cancellation; do not enter the ordinary wait/replacement path
    if a runtime-owned RUNTIME_INTERRUPTION_EVENT or RUNTIME_TERMINAL_EVENT arrives:
        validate its closed runtime schema, exact target, timing, and stop proof before
            applying any report classifier; use the dedicated runtime-event path even
            when the row is already CANCEL_REQUESTED
        decision = consume_terminal_decision(classify_terminal(result))
        if decision is LATE_BIND_VERIFIED:
            stop without calling commit_terminal or entering ordinary wait
        if decision is PARTIAL:
            require the corresponding runtime-event materializer returned COMMITTED
                with the authoritative PARTIAL row;
                retain the lock and continue only through the same-task resume path
        if decision is REPLACEMENT_SLOT_CLOSED:
            require current_task_row.state is BLOCKED and
                current_task_row.terminal_reason is REPLACEMENT_SPAWN_FAILED
            stop without recover_or_block, replacement, or unlock
        if decision is RECOVERY_REQUIRED:
            require the corresponding runtime-event materializer returned COMMITTED
                with the authoritative terminal row;
                retain the lock and continue only through the role-specific recovery path
        if decision is STOP_CONFIRMED_ONLY:
            require the corresponding stop materializer returned COMMITTED with
                disposition=STOP_CONFIRMED_ONLY;
                retain CANCEL_REQUESTED and the lock; stop without recovery or replacement
        quarantine(result); retain the attempt/lock; stop without recovery or replacement
    if an ordinary child report arrives:
        quarantine(result); retain the attempt/lock; stop without recovery or replacement
    if cancellation is confirmed:
        retain/quarantine the attempt and stop before acceptance or replacement
    if binding_failure_recovery_deadline expires first or timing is UNVERIFIED:
        retain the SPAWN_UNCONFIRMED provenance fields, canonical overlay=CANCEL_REQUESTED,
        and every lock; quarantine later output, record REVIEW_BLOCKED, and prohibit
        replacement or takeover until a runtime terminal stop is confirmed
    otherwise:
        continue the same bounded stop-confirmation wait

await_recorded_binding_failure_stop_confirmation_once(current_task_row,
                                                       fixed_deadline,
                                                       replacement_attempt=no):
    require the current row is the exact live unbound invocation with
        binding_failure_provenance=SPAWN_UNCONFIRMED,
        overlay=CANCEL_REQUESTED, an exact reserved target, a persisted cancellation
        intent/key, and finite fixed_deadline; this helper never performs the
        binding-failure CAS and never issues a second stop request
    wait once, with continuation if the platform yields, until fixed_deadline for
        one runtime-owned terminal/interruption event or confirmed cancellation;
        do not query status or inspect a moving artifact
    for each delivered runtime event, run the closed runtime validator and the
        typed classifier; for child-authored output, scan and quarantine only its
        redacted metadata. A late event is STOP_CONFIRMED_ONLY and cannot resume,
        recover, replace, or unlock.
    return CONFIRMED when a runtime-owned stop is captured, AUTHORITATIVE_WINNER
        when the current row changed, or EXPIRED when fixed_deadline passes without
        proof; the caller retains the overlay/lock and decides any role-specific
        terminal disposition without re-running the initial failure sequence

prebinding_payload_scan_passes(raw, runtime_artifact_access_proof, runtime_review_coverage_proof):
    adapted = canonicalize_prebinding_payload(
        raw, authoritative_task_row, runtime_artifact_access_proof,
        runtime_review_coverage_proof
    )
    if adapted is INVALID: return INVALID
    result = adapted.candidate
    parse the complete adapted candidate once; require exact stored ROLE, the closed role schema,
    required fields, bounded values, no instruction-shaped content, no runtime-owned
    AGENT_ID/AGENT_CHANNEL/TRANSPORT_INVOCATION_ASSOCIATION/RESERVED_RUNTIME_TARGET/REPORT_ID/
    RUNTIME_TERMINAL_EVENT_ID/TIMING/ARTIFACT_ACCESS_PROOF/REVIEW_COVERAGE_PROOF/
    STOP_CONFIRMED/RESUMABLE/CHECKPOINT_*
    fields, role-complete STATUS, matching snapshot/content identities, and a separate
    runtime_artifact_access_proof that ties the read-only access to this snapshot
    (the proof is supplied by the runtime/parent, never read from the candidate)
    if stored_role is reviewer and
       not review_payload_semantics_are_closed(result, runtime_review_coverage_proof):
        return INVALID
    return hash_complete_candidate(result) only after the whole candidate has been
    scanned without side effects; return INVALID on any scan failure

canonical_review_candidate_fields(result):
    require the complete role-specific candidate scan has passed
    normalize every optional candidate field to its documented NONE/empty sentinel
        before this function (required fields must be present; absence is invalid)
    return this explicit ordered list of normalized, bounded values:
        [
            ("status", result.STATUS),
            ("role", result.ROLE),
            ("base_snapshot", result.BASE_SNAPSHOT),
            ("base_content_identity", result.BASE_CONTENT_IDENTITY),
            ("snapshot_id", result.SNAPSHOT_ID),
            ("content_identity", result.CONTENT_IDENTITY),
            ("summary", result.SUMMARY),
            ("completed_scope", result.COMPLETED_SCOPE),
            ("reviewed_paths", result.REVIEWED_PATHS),
            ("changed_paths", result.CHANGED_PATHS),
            ("checks", result.CHECKS),
            ("risks", result.RISKS),
            ("next_action", result.NEXT_ACTION),
            ("findings", result.FINDINGS),
            ("blocker", result.BLOCKER),
            ("blocker_or_input", result.BLOCKER_OR_INPUT),
            ("attention_required", result.ATTENTION_REQUIRED),
            ("blocker_or_decision", result.BLOCKER_OR_DECISION),
            ("material_decision", result.MATERIAL_DECISION),
            ("decision", result.DECISION)
        ]
    arrays are normalized in their declared order; each finding is a declared
        ordered tuple of its bounded severity/location/evidence/impact/fix fields
    and no map/object iteration is used

hash_complete_candidate(result):
    candidate_fields = canonical_review_candidate_fields(result)
    return canonical_sha256_v1(candidate_fields)

runtime_attested_artifact_access_for(result):
    read the proof only from runtime/parent transport metadata for the exact snapshot;
    never derive it from result fields or child prose

runtime_review_coverage_proof_for(result):
    read the proof only from runtime/parent metadata for this task, immutable snapshot,
    and content identity; never accept result.REVIEW_COVERAGE_PROOF or child prose as
    the proof

review_coverage_proof_matches(result, proof):
    scope, scope_digest = canonicalize_impact_scope(stored TaskSpec.IMPACT_SCOPE)
    focused_checks, focused_checks_digest = canonicalize_focused_checks(
        stored TaskSpec.FOCUSED_CHECKS
    )
    require result.CHANGED_PATHS is explicitly none because reviewer access is read-only
    require result.COMPLETED_SCOPE and result.REVIEWED_PATHS are nonempty, not NONE,
        and their normalized coverage exactly satisfies the parent assignment scope
        (the assigned lane scope in review_set mode)
    if the task is in review_set mode:
        assignments, mapping_digest = canonicalize_lane_assignments(
            stored parent/session lane assignments, scope, focused_checks
        )
        lane_assignment = the parent/session assignment for this exact lane/task
            identity; the lane ID is parent-owned and is never taken from result prose
        require proof is a runtime/parent-attested LaneCoverageProofV1 and not
            NONE or UNKNOWN
        require validate_lane_coverage_proof(
            proof, stored TaskSpec, lane_assignment, result
        )
        require proof.snapshot_id and proof.content_identity exactly match the task
            and result, proof.impact_scope_digest == scope_digest,
            proof.mapping_digest == mapping_digest, and
            proof.lane_scope_digest == lane_assignment.lane_scope_digest
        require result.COMPLETED_SCOPE and result.REVIEWED_PATHS cover only this
            lane_assignment.lane_scope; a lane proof never requires another lane's
            result or the aggregate proof
        return true
    parent_assignment, mapping_digest = canonicalize_parent_review_assignment(
        stored parent review assignment, scope, focused_checks
    )
    require proof is a runtime/parent-attested CoverageProofV1 and not NONE or UNKNOWN
    require validate_parent_coverage_proof(
        proof, stored TaskSpec, [parent_assignment]
    )
    require proof.snapshot_id and proof.content_identity exactly match the task and result
    require proof.impact_scope_digest == scope_digest and
        proof.mapping_digest == mapping_digest and
        proof.required_focused_checks == focused_checks and
        proof.explicit_exclusions == scope.explicit_exclusions
    require result.COMPLETED_SCOPE and result.REVIEWED_PATHS cover the full parent
        assignment scope and proof records those paths plus every required focused
        check as passed, or records that no focused check was required because the
        canonical TaskSpec explicitly declared none; bare NOT_RUN or UNKNOWN is invalid
    return true only when the standalone proof is gap-free

validated_replacement_budget_gate(result):
    replacement_index = the current ledger row's runtime-owned replacement index
    if stored_role is not reviewer or replacement_index == 0:
        return NONE
    require replacement_index == 1
    gate = runtime/parent metadata for the exact replacement row, never child fields
    require gate contains the fixed snapshot_deadline, replacement_decision_deadline,
        replacement_stop_confirmed_at, replacement_decision_latest_at,
        replacement_decision_at, replacement_decision_remaining,
        snapshot_budget_started_at,
        review_replacement_decision_reserve_budget, review_spawn_reserve_budget,
        review_replacement_min_budget, budget_remaining_at_spawn, and
        budget_remaining_at_binding, spawn_requested_at, and spawn_confirmed_at
    require all gate values are finite, nonnegative, from the same monotonic clock,
        budget_remaining_at_binding <= budget_remaining_at_spawn, and
        budget_remaining_at_spawn >= review_spawn_reserve_budget + review_replacement_min_budget, and
        budget_remaining_at_spawn - budget_remaining_at_binding <= review_spawn_reserve_budget
        require gate.snapshot_deadline == current_task_row.snapshot_deadline_at and
        gate.snapshot_deadline == gate.snapshot_budget_started_at + review_wait_budget and
        gate.replacement_decision_deadline == replacement_decision_deadline and
        gate.replacement_stop_confirmed_at == current_task_row.replacement_stop_confirmed_at and
        gate.replacement_decision_latest_at == current_task_row.replacement_decision_latest_at and
        gate.replacement_decision_latest_at == min(
            gate.replacement_decision_deadline,
            gate.replacement_stop_confirmed_at + review_replacement_decision_reserve_budget
        ) and
        current_task_row.replacement_decision_deadline == replacement_decision_deadline and
        current_task_row.replacement_decision_latest_at == gate.replacement_decision_latest_at and
        gate.replacement_decision_at == current_task_row.replacement_decision_at and
        gate.replacement_decision_remaining == current_task_row.replacement_decision_remaining and
        snapshot_budget_started_at <= gate.replacement_stop_confirmed_at <=
            gate.replacement_decision_at <= gate.spawn_requested_at <=
            gate.spawn_confirmed_at <= gate.snapshot_deadline and
        gate.replacement_stop_confirmed_at <= gate.replacement_decision_at <
            gate.replacement_decision_latest_at and
        gate.replacement_decision_remaining ==
            gate.snapshot_deadline - gate.replacement_decision_at and
        gate.replacement_decision_remaining >=
            review_spawn_reserve_budget + review_replacement_min_budget and
        gate.spawn_requested_at < gate.replacement_decision_latest_at and
        gate.spawn_requested_at <= gate.spawn_confirmed_at <= gate.snapshot_deadline and
        gate.budget_remaining_at_spawn ==
            gate.snapshot_deadline - gate.spawn_requested_at and
        gate.budget_remaining_at_binding ==
            gate.snapshot_deadline - gate.spawn_confirmed_at and
        budget_remaining_at_binding >= review_replacement_min_budget and
        snapshot_deadline - gate.spawn_confirmed_at >= review_replacement_min_budget
    gate_digest = canonical_sha256_v1([
        ("snapshot_budget_started_at", snapshot_budget_started_at),
        ("snapshot_deadline_at", snapshot_deadline),
        ("replacement_decision_deadline", replacement_decision_deadline),
        ("replacement_stop_confirmed_at", replacement_stop_confirmed_at),
        ("replacement_decision_latest_at", replacement_decision_latest_at),
        ("replacement_decision_at", replacement_decision_at),
        ("replacement_decision_remaining", replacement_decision_remaining),
        ("review_replacement_decision_reserve_budget",
            review_replacement_decision_reserve_budget),
        ("spawn_requested_at", spawn_requested_at),
        ("spawn_confirmed_at", spawn_confirmed_at),
        ("review_spawn_reserve_budget", review_spawn_reserve_budget),
        ("review_replacement_min_budget", review_replacement_min_budget),
        ("budget_remaining_at_spawn", budget_remaining_at_spawn),
        ("budget_remaining_at_binding", budget_remaining_at_binding)
    ])
    require gate.replacement_gate_digest == gate_digest and
        current_task_row.replacement_gate_digest in {UNSET, gate_digest}
    return the immutable validated gate and digest plus
        expected_row_gate_digest=current_task_row.replacement_gate_digest;
        a report-binding/terminal CAS may fill an UNSET row digest exactly once
    missing or contradictory gate metadata is INVALID

prebinding_budget_gate_is_open(result):
    return validated_replacement_budget_gate(result) is not INVALID

review_payload_semantics_are_closed(result, runtime_review_coverage_proof):
    if stored_role is not reviewer:
        return true
    require every child-owned canonical field (SUMMARY, COMPLETED_SCOPE, CHANGED_PATHS,
        CHECKS, RISKS, BLOCKER_OR_INPUT, ATTENTION_REQUIRED, NEXT_ACTION,
        REVIEWED_PATHS, FINDINGS, and BLOCKER) is present/absent only as the schema
        permits, bounded,
        instruction-free, and semantically consistent with STATUS; an actionable
        assertion hidden in free text is still a finding
    if result.status is CLEAN:
        require result.CHANGED_PATHS is explicitly none (a reviewer is read-only), and
            result.COMPLETED_SCOPE and result.REVIEWED_PATHS are nonempty and not NONE,
            and review_coverage_proof_matches(result, runtime_review_coverage_proof)
        require SUMMARY, FINDINGS, BLOCKER, BLOCKER_OR_INPUT, ATTENTION_REQUIRED,
        BLOCKER_OR_DECISION, MATERIAL_DECISION, and DECISION to be
        bounded and free of actionable unresolved claims; FINDINGS, BLOCKER,
        BLOCKER_OR_INPUT, ATTENTION_REQUIRED, and all material decision aliases must
        be absent or explicitly none; RISKS and NEXT_ACTION must be none and SUMMARY,
        COMPLETED_SCOPE, CHECKS, and REVIEWED_PATHS must contain only bounded evidence
        of completed, in-scope work, not hidden risks, commands, decisions, or fixes;
        material decision fields must be absent or none
        and result.CHECKS must agree with the coverage proof that every required
        focused check passed or was explicitly unnecessary under the stored TaskSpec;
        bare NOT_RUN or UNKNOWN is invalid
        return true
    if result.status is FINDINGS:
        require result.CHANGED_PATHS is explicitly none (a reviewer is read-only)
        require SUMMARY, COMPLETED_SCOPE, CHECKS, RISKS, NEXT_ACTION, and REVIEWED_PATHS
        contain no actionable claim that is not represented by an actionable finding;
        all child-owned free text must be bounded and instruction-free
        require BLOCKER, BLOCKER_OR_INPUT, ATTENTION_REQUIRED, BLOCKER_OR_DECISION,
        MATERIAL_DECISION, and DECISION to be absent or explicitly none
        require at least one actionable finding, and each finding has a location,
        evidence, impact, and concrete fix suggestion; a literal "none" is invalid
        return true
    return true for nonterminal/other role-complete statuses without applying reviewer rules

runtime_interruption_attestation_matches(result):
    require a runtime-owned RUNTIME_INTERRUPTION_EVENT delivered on the current agent
    channel with exact run/task/agent/invocation/token/attempt identity (or the
    reserved unbound agent target while the row is unbound with overlay in
    {NONE, CANCEL_REQUESTED}), exact runtime
    agent_channel and one-to-one transport_invocation_association (or their reserved
    unbound values), exact RUNTIME_TARGET matching the persisted reserved runtime
    target tuple, STOP_CONFIRMED=yes,
    runtime-owned RESUMABLE in {yes, no}, finite runtime-owned
    CANCEL_CONFIRMED_AT and TERMINAL_AT, with CANCEL_CONFIRMED_AT <= TERMINAL_AT,
    nonempty opaque runtime-owned EVENT_ID string, unique in the runtime event journal,
    and no child-authored replacement for those fields; numeric finiteness is not a
    requirement for an event identity
    if current_task_row.agent_id is UNASSIGNED:
        if current_task_row.overlay is NONE:
            require current_task_row.binding_failure_provenance is NONE
        else:
            require current_task_row.overlay is CANCEL_REQUESTED and
                current_task_row.binding_failure_provenance is SPAWN_UNCONFIRMED
    else:
        require current_task_row.binding_failure_provenance is NONE
    require current_task_row.reserved_runtime_target is not NONE and
        current_task_row.reserved_runtime_target is not UNADDRESSABLE and
        result.RUNTIME_TARGET is not NONE
    require runtime_event_report_slot_matches(result)
    require ARTIFACT_ACCESS_PROOF is present and matching when RESUMABLE is yes;
    NONE is valid for a confirmed non-resumable stop

reserved_runtime_target_matches(result):
    if current_task_row.reserved_runtime_target is UNADDRESSABLE:
        return INVALID; only authoritative runtime reconciliation may first replace
            UNADDRESSABLE with an exact target or a closed no-child proof
    if current_task_row.reserved_runtime_target is NONE:
        require result.RUNTIME_TARGET is NONE and result.STATUS is spawn_failed and
            result.CHILD_STARTED is no
        return true
    require result.RUNTIME_TARGET is a runtime-owned opaque tuple containing the
        target identity, reserved channel, and one-to-one transport association
    require current_task_row.reserved_runtime_target is the exact same tuple and is
        bound to the current run/task/attempt/invocation/token
    if current_task_row.agent_id is UNASSIGNED:
        require result.AGENT_ID is UNASSIGNED,
            result.AGENT_CHANNEL is UNASSIGNED, and
            result.TRANSPORT_INVOCATION_ASSOCIATION is UNASSIGNED
        require result.RUNTIME_TARGET is not NONE
    else:
        require result.AGENT_ID == current_task_row.agent_id,
            result.AGENT_CHANNEL == current_task_row.agent_channel, and
            result.TRANSPORT_INVOCATION_ASSOCIATION ==
                current_task_row.transport_invocation_association
    return true only for an exact target reservation; a matching agent ID or an
        invocation token without the target tuple is insufficient

runtime_stop_preceded_cancel_request(result):
    return true only for a runtime-owned RUNTIME_INTERRUPTION_EVENT or
    RUNTIME_TERMINAL_EVENT with STOP_CONFIRMED=yes, an exact current invocation,
    channel/association, and reserved runtime target, while the authoritative row
    has overlay=CANCEL_REQUESTED and a finite cancel_requested_at. Require the
    runtime-owned cancel_confirmed_at and terminal_at to satisfy
    spawn_requested_at (or spawn_confirmed_at for a bound row) <=
    cancel_confirmed_at <= terminal_at <= cancel_requested_at <= monotonic_now,
    with the applicable fixed recovery/binding-failure deadline still validated.
    This is the general delivery/CAS race in which the runtime completed the stop
    before the parent won CANCEL_REQUESTED; it preserves the cancellation overlay,
    never issues a second stop request, and is not a child-authored status.

pre_cancel_runtime_stop_is_reconcilable(result):
    require result is a validated runtime-owned RUNTIME_INTERRUPTION_EVENT or a
        started RUNTIME_TERMINAL_EVENT with the exact invocation, channel/association,
        reserved target, stop proof, and event identity
    require current_task_row.overlay is NONE and
        current_task_row.binding_failure_provenance is NONE and
        current_task_row.cancel_requested_at is UNSET
    require result.CANCEL_CONFIRMED_AT <= result.TERMINAL_AT and both samples satisfy
        the applicable on-time autonomous/binding-failure cutoff
    return true only for the pre-CANCEL_REQUESTED delivery/CAS race; the caller must
        attempt the complete runtime-event materialization CAS first, and if a
        concurrent cancellation CAS wins, reload once and replay the same event ID
        against that exact row, using runtime_stop_preceded_cancel_request for the
        resulting STOP_CONFIRMED_ONLY decision when required. It never sends a stop
        request from the event's stale projection.

reserved_unbound_target_matches(result):
    require current_task_row.agent_id is UNASSIGNED and
        current_task_row.reserved_runtime_target is an exact runtime target tuple,
        not NONE or UNADDRESSABLE
    return reserved_runtime_target_matches(result)
    # This compatibility-named predicate is intentionally stricter than an agent
    # lookup: it is used only by the unbound stop/replay paths and still requires
    # the complete persisted target tuple, reserved channel, and association.

unbound_stop_preceded_cancel_request(result):
    require current_task_row.agent_id is UNASSIGNED and
        current_task_row.reserved_runtime_target is an exact runtime target tuple,
        not NONE or UNADDRESSABLE
    return runtime_stop_preceded_cancel_request(result)

runtime_terminal_event_validates(result, authoritative_no_child_reconciliation=no):
    require result.kind is RUNTIME_TERMINAL_EVENT, exact current run/task/attempt/
        invocation/token identity, runtime-owned nonempty EVENT_ID and
        RUNTIME_TERMINAL_EVENT_ID, and CLOCK_SOURCE equal to the validated ledger clock
    require result.RUNTIME_TERMINAL_EVENT_ID is independent of child report identity,
        result.STATUS in {failed, cancelled, errored, shutdown, spawn_failed}, and
        only the documented closed fields are present
        require reserved_runtime_target_matches(result) when the row has an exact reserved target;
        if the row reservation is UNADDRESSABLE:
            require authoritative_no_child_reconciliation is yes and
                result.STATUS is spawn_failed and result.CHILD_STARTED is no and
                result.RUNTIME_TARGET is NONE and
                result.AGENT_ID/AGENT_CHANNEL/TRANSPORT_INVOCATION_ASSOCIATION are
                    UNASSIGNED
            # This is accepted only as the dedicated input to
            # materialize_unaddressable_no_child_terminal; an ordinary event may
            # never bind directly to the sentinel.
        else if no target was allocated:
            require result.RUNTIME_TARGET=NONE and the row reservation is NONE
    if current_task_row.runtime_terminal_event_id is not NONE:
        require current_task_row.runtime_terminal_event_id ==
            result.RUNTIME_TERMINAL_EVENT_ID and
            runtime_event_journal_contains_exact(
                result.EVENT_ID, run_id, task_id, attempt, invocation_id,
                result.RUNTIME_TERMINAL_EVENT_ID
            )
        require current_task_row.terminal_at == result.TERMINAL_AT and
            (result.STATUS is spawn_failed and
                current_task_row.terminal_reason in
                    {SPAWN_FAILED, REPLACEMENT_SPAWN_FAILED} or
             result.STATUS is not spawn_failed and
                current_task_row.state in {FAILED, CANCELLED, BLOCKED})
        return true (idempotent replay of the already-materialized runtime event)
    if result.CHILD_STARTED is no:
        require result.STATUS is spawn_failed,
            result.AGENT_ID/AGENT_CHANNEL/TRANSPORT_INVOCATION_ASSOCIATION are
            UNASSIGNED, result.STOP_CONFIRMED is not_applicable,
            result.CANCEL_CONFIRMED_AT is UNSET, and
            (
                the row is the exact live CLAIMED/RUNNING/NEEDS_INPUT
                replacement/original invocation with overlay in {NONE, CANCEL_REQUESTED}
                or
                authoritative_no_child_reconciliation is yes and
                the row is the exact UNADDRESSABLE/BLOCKED invocation with
                overlay=CANCEL_REQUESTED
            )
        if overlay is CANCEL_REQUESTED and
           authoritative_no_child_reconciliation is not yes:
            require finite current_task_row.cancel_requested_at and
                current_task_row.cancel_requested_at <= monotonic_now
    else:
        require result.CHILD_STARTED is yes, result.STOP_CONFIRMED is yes,
            current_task_row.reserved_runtime_target is not NONE and
            result.RUNTIME_TARGET is not NONE,
            finite result.CANCEL_CONFIRMED_AT, and the row is the exact live
            invocation with overlay in
            {NONE, CANCEL_REQUESTED}
        if current_task_row.agent_id is UNASSIGNED:
            require result.AGENT_ID/AGENT_CHANNEL/
                TRANSPORT_INVOCATION_ASSOCIATION are UNASSIGNED,
                current_task_row.overlay in {NONE, CANCEL_REQUESTED}
            if current_task_row.overlay is NONE:
                require current_task_row.binding_failed_at is UNSET and
                    current_task_row.binding_failure_recovery_deadline_at is UNSET and
                    current_task_row.cancel_requested_at is UNSET
            else:
                require finite current_task_row.binding_failed_at and
                    finite current_task_row.binding_failure_recovery_deadline_at
        else:
            require result.AGENT_ID is the bound runtime agent and finite,
                result.AGENT_CHANNEL == current_task_row.agent_channel and
                result.TRANSPORT_INVOCATION_ASSOCIATION ==
                    current_task_row.transport_invocation_association
    require runtime_event_report_slot_matches(result)
    require finite runtime-owned TERMINAL_AT, and if the current row already has a
        non-NONE runtime_terminal_event_id it equals result.RUNTIME_TERMINAL_EVENT_ID
    return true only for this runtime-only schema; child-authored terminal statuses,
        report IDs, checkpoint claims, or free-form reasons are invalid

runtime_event_report_slot_matches(result=NONE):
    if current_task_row.state is NEEDS_INPUT or current_task_row.state is PARTIAL:
        if current_task_row.state is PARTIAL and current_task_row.report_id is NONE:
            # Runtime interruption materialization intentionally uses no child report
            # ID. A duplicate delivery must be accepted only as an idempotent replay
            # of the already-materialized runtime PARTIAL, never as a new report.
            require result is RUNTIME_INTERRUPTION_EVENT and
                current_task_row.interruption_event_id == result.EVENT_ID and
                current_task_row.runtime_terminal_event_id == result.EVENT_ID and
                runtime_event_journal_contains_exact(
                    result.EVENT_ID, run_id, task_id, attempt, invocation_id,
                    result.RUNTIME_TERMINAL_EVENT_ID
                ) and
                current_task_row.checkpoint_id == result.CHECKPOINT_ID and
                current_task_row.checkpoint_content_identity ==
                    result.CHECKPOINT_CONTENT_IDENTITY and
                current_task_row.terminal_at == result.TERMINAL_AT and
                current_task_row.cancel_confirmed_at == result.CANCEL_CONFIRMED_AT
            return true
        require current_task_row.report_id is the exact parent-created report,
            and the report belongs to this same run/task/attempt/invocation
        if current_task_row.state is NEEDS_INPUT:
            require current_task_row.attention_required is nonempty and bounded
        else:
            require current_task_row.attention_required is NONE
    else:
        require current_task_row.report_id is NONE and
            current_task_row.attention_required is NONE
    return true

unaddressable_no_child_terminal_timing_is_valid(result):
    require this is the dedicated authoritative no-child event, one validated
        monotonic clock, finite snapshot_budget_started_at, snapshot_deadline_at,
        spawn_requested_at, and TERMINAL_AT, with
        snapshot_deadline_at == snapshot_budget_started_at + review_wait_budget
    require snapshot_budget_started_at <= spawn_requested_at <=
        result.TERMINAL_AT <= monotonic_now, result.CANCEL_CONFIRMED_AT is UNSET,
        and result.CHILD_STARTED=no
    require cancel_requested_at is UNKNOWN, UNSET, or a finite sample at or after
        spawn_requested_at and no later than monotonic_now; UNKNOWN is permitted here
        because the authoritative proof establishes that no stop request was needed
    require all durations are finite/nonnegative and the runtime terminal identity is
        independent and nonempty
    return true only for this dedicated no-child timing record; it is a failure
        materialization proof, never review or replacement authority

record_binding_failure_cancel_requested_compare_and_set(failure):
    require the exact current full task row and a live unbound invocation with an
        exact reserved runtime target, or the already recorded UNADDRESSABLE sentinel
        only in the no-target reconciliation branch
    require the source row is either the live `overlay=NONE`,
        `binding_failure_provenance=NONE` row with all binding-failure and cancellation
        fields UNSET, or the race-reconciliation `overlay=CANCEL_REQUESTED` row with
        `binding_failure_provenance=NONE`, both binding-failure timing fields UNSET,
        and an already persisted cancellation intent/sample; reject a row that already
        has `binding_failure_provenance=SPAWN_UNCONFIRMED` because that transition is
        complete and must never be repeated
    require the binding-failure sample is finite or the explicit UNKNOWN/UNVERIFIED
        pair; derive the fixed binding-failure deadline only from that sample and the
        persisted snapshot deadline
    atomically write the complete row in one CAS: preserve every canonical field,
        persist the exact target and binding-failure fields, set
        new_binding_failure_provenance=SPAWN_UNCONFIRMED (or preserve that same value
        when reconciling an already-recorded failure), and set the single
        persisted overlay value to CANCEL_REQUESTED (the SPAWN_UNCONFIRMED fact lives in
        the binding-failure provenance, never as an intermediate row), and record one
        fresh cancel_requested_at plus its deterministic cancellation idempotency key
        (UNKNOWN is allowed only for the unverified fail-closed branch). The resulting
        row is the only state from which the stop helper may send the one idempotent
        stop. If a concurrent ordinary cancellation CAS wins first, reload once and
        complete the same full-row projection against that winner, preserving its
        key/target; never use a sparse timing or overlay update and never issue a stop
        from the losing projection.
    on success return {kind=RECORDED, authoritative_row=the new row}; on a race
        return {kind=AUTHORITATIVE_WINNER, authoritative_row=the transaction winner};
        on CAS loss return {kind=CAS_LOST, authoritative_row=the transaction winner,
        lost_operation=the complete binding-failure projection}

record_unaddressable_spawn_compare_and_set(current_row, failure):
    require authoritative spawn reconciliation has not proved a no-child outcome,
        the exact current live row/version is CLAIMED or RUNNING, report_id=NONE,
        agent_id/channel/association=UNASSIGNED, reserved_runtime_target in {NONE,
        UNADDRESSABLE},
        runtime_terminal_event_id=NONE, and owner/lock/snapshot/invocation identity
        all match
    require failure.binding_failed_at=UNKNOWN and
        failure.binding_failure_recovery_deadline_at=UNVERIFIED and
        failure.cancel_requested_at is a finite monotonic sample or UNKNOWN
    before the row CAS, atomically reserve parent/session metadata
        `unaddressable_origin_state` keyed by the exact run/task/attempt/invocation/
        token and row version; its value is the current live state (`CLAIMED` or
        `RUNNING`) and the exact snapshot/content identity. The metadata write and
        the full-row CAS below are one transaction; if that metadata cannot be
        persisted, fail closed without claiming that the sentinel is recoverable.
    atomically full-row CAS the complete row to reserved_runtime_target=UNADDRESSABLE,
        binding_failure_provenance=SPAWN_UNCONFIRMED, overlay=CANCEL_REQUESTED,
        state=BLOCKED, terminal_reason=UNADDRESSABLE_RUNTIME,
        quarantine_reason=UNADDRESSABLE_RUNTIME, binding_failed_at=UNKNOWN, and
        binding_failure_recovery_deadline_at=UNVERIFIED,
        cancel_requested_at=failure.cancel_requested_at, and a metadata-only
        cancellation intent carrying the deterministic idempotency key, while preserving
        every other field and retaining owner/lock. No stop request is issued because
        there is no safe target. This is the permanent fail-closed state for an
        invocation that may exist but has no addressable stop target: it cannot be
        accepted, recovered, replaced, taken over, unlocked, or retried. Only one
        authoritative runtime reconciliation may later replace UNADDRESSABLE with an
        exact target plus validated binding-failure timing, or prove no child through
        another complete-row CAS; no child event may bind directly to this sentinel.
    on success return {kind=RECORDED, authoritative_row=the new row}; on loss return
        {kind=CAS_LOST, authoritative_row=the transaction winner,
         lost_operation=the complete unaddressable projection}

reconcile_unaddressable_runtime_once():
    use only the platform's authoritative invocation registry, exactly once, for a row
        whose reserved_runtime_target=UNADDRESSABLE and state=BLOCKED
    if it returns an exact target and a finite binding-failure sample/deadline:
        require parent/session metadata contains the matching
            `unaddressable_origin_state` for this exact row/version, and that the
            saved state is exactly CLAIMED or RUNNING
        target_resolution_outcome = replace the sentinel through one complete-row CAS,
            restoring the saved live state, exact target, and finite timing fields;
            explicitly write terminal_at=UNSET, runtime_terminal_event_id=NONE,
            terminal_reason=NONE, quarantine_reason=NONE, report_disposition=none,
            integrated_content_identity=NONE, and result_content_identity=NONE while
            retaining CANCEL_REQUESTED, SPAWN_UNCONFIRMED, the cancellation intent,
            owner, lock, snapshot/content identity, and every other canonical field
        if target_resolution_outcome.kind is COMMITTED:
            authoritative_row = target_resolution_outcome.authoritative_row
        if target_resolution_outcome.kind is CAS_LOST:
            consume its authoritative winner and return
                {kind=AUTHORITATIVE_WINNER,
                 authoritative_row=target_resolution_outcome.authoritative_row}
        require target_resolution_outcome.kind is COMMITTED
        delete/close the consumed origin metadata only after the successful CAS and
        retain it as a metadata-only audit event; return
            {kind=RESOLVED, authoritative_row=authoritative_row}
    # The exact target branch above is complete before a stop event is accepted.
    if the lookup returned an exact target but its timing was not validated:
        retain UNADDRESSABLE/BLOCKED and do not accept an event
    if it returned a verified no-child result:
        event = closed spawn-failure event with target=NONE and independent runtime ID
        materialization = materialize_unaddressable_no_child_terminal(event)
        if materialization.kind is COMMITTED:
            return {kind=TERMINAL,
                    authoritative_row=materialization.authoritative_row,
                    disposition=materialization.disposition, runtime_event=event}
        if materialization.kind is CAS_LOST:
            replay_outcome = reconcile_runtime_event_cas_loss(event, materialization)
            replay_decision = consume_terminal_decision(replay_outcome)
            if replay_decision in {RECOVERY_REQUIRED, REPLACEMENT_SLOT_CLOSED}:
                return {kind=TERMINAL, authoritative_row=current_task_row,
                        disposition=replay_decision, runtime_event=event}
            return {kind=replay_decision, authoritative_row=current_task_row}
        return {kind=TARGET_UNAVAILABLE_UNVERIFIED, authoritative_row=current_task_row}
    otherwise retain UNADDRESSABLE/CANCEL_REQUESTED, timing=UNVERIFIED, and the lock
        permanently; record REVIEW_BLOCKED/QUARANTINED metadata only and never guess a
        target, retry, take over, unlock, or treat an acknowledgement as stop proof
    return {kind=TARGET_UNAVAILABLE_UNVERIFIED, authoritative_row=current_task_row}

reconcile_unbound_target_before_stop_once(cancel_sample):
    require the exact live unbound row has reserved_runtime_target=NONE and the
        authoritative spawn registry has not yet been consulted for this stop
    reconciliation = reconcile_spawn_once_through_authoritative_runtime(
        exact run/task/attempt/invocation/token
    )
    if reconciliation is NO_CHILD_CONFIRMED:
        emit the closed spawn_failed RUNTIME_TERMINAL_EVENT with target=NONE and
            spawn_failure_outcome = materialize_spawn_failure_terminal(event)
        if spawn_failure_outcome.kind is CAS_LOST:
            replay_outcome = reconcile_runtime_event_cas_loss(
                event, spawn_failure_outcome
            )
            replay_decision = consume_terminal_decision(replay_outcome)
            if replay_decision in {RECOVERY_REQUIRED, REPLACEMENT_SLOT_CLOSED}:
                return {kind=TERMINAL, authoritative_row=current_task_row,
                        disposition=replay_decision, runtime_event=event}
            return {kind=replay_decision, authoritative_row=current_task_row}
        require spawn_failure_outcome.kind is COMMITTED
        current_task_row = spawn_failure_outcome.authoritative_row
        return {kind=TERMINAL, authoritative_row=current_task_row,
                disposition=spawn_failure_outcome.disposition, runtime_event=event}
    if reconciliation returns an exact target:
        binding_sample = current_task_row.binding_failed_at when it is finite, otherwise
            cancel_sample when it is finite, otherwise UNKNOWN
        if binding_sample is finite:
            derive binding_failure_recovery_deadline_at from the fixed snapshot deadline
            target_resolution_outcome = perform one complete full-row CAS replacing
                target NONE with that exact target, restoring the live CLAIMED/RUNNING
                row and preserving all other fields
            if target_resolution_outcome.kind is CAS_LOST:
                consume its authoritative winner and return
                    {kind=AUTHORITATIVE_WINNER,
                     authoritative_row=target_resolution_outcome.authoritative_row}
            require target_resolution_outcome.kind is COMMITTED
            authoritative_row = target_resolution_outcome.authoritative_row
            return {kind=RESOLVED, authoritative_row=authoritative_row}
    failure = record_unaddressable_spawn_compare_and_set(
        current_task_row, binding_failed_at=UNKNOWN,
        binding_failure_recovery_deadline_at=UNVERIFIED,
        cancel_requested_at=cancel_sample
    )
    if failure.kind is CAS_LOST:
        consume its authoritative winner and return
            {kind=AUTHORITATIVE_WINNER, authoritative_row=failure.authoritative_row}
    return {kind=TARGET_UNAVAILABLE_UNVERIFIED, authoritative_row=current_task_row}

reconcile_bound_target_once_through_authoritative_runtime():
    require the exact bound live row has a missing or UNADDRESSABLE target and the
        authoritative runtime registry has not yet been consulted for this stop
    reconciliation = reconcile_spawn_once_through_authoritative_runtime(
        exact run/task/attempt/invocation/token and bound agent/channel/association
    )
    if reconciliation returns an exact target matching the bound runtime identities:
        target_resolution_outcome = replace the missing/sentinel target through one
            complete-row CAS, preserving every other field and the owner/lock
        if target_resolution_outcome.kind is CAS_LOST:
            consume its authoritative winner and return
                {kind=AUTHORITATIVE_WINNER,
                 authoritative_row=target_resolution_outcome.authoritative_row}
        require target_resolution_outcome.kind is COMMITTED
        return {kind=RESOLVED,
                authoritative_row=target_resolution_outcome.authoritative_row}
    return {kind=TARGET_UNAVAILABLE_UNVERIFIED, authoritative_row=current_task_row}

materialize_unaddressable_no_child_terminal(result):
    require the authoritative one-time reconciliation proves no child was created,
        result is a closed runtime RUNTIME_TERMINAL_EVENT with STATUS=spawn_failed,
        CHILD_STARTED=no, target=NONE, unassigned runtime identities,
        STOP_CONFIRMED=not_applicable, and an independent runtime terminal identity
    require runtime_terminal_event_validates(
        result, authoritative_no_child_reconciliation=yes
    )
    require unaddressable_no_child_terminal_timing_is_valid(result)
    require the current row is the exact UNADDRESSABLE/BLOCKED invocation with its
        owner/lock, report_id=NONE, and matching run/task/attempt/invocation/token
    through one complete full-row CAS, resolve reserved_runtime_target from
        UNADDRESSABLE to NONE, clear the cancellation overlay, write state=FAILED for
        the original or BLOCKED for replacement, terminal_reason=SPAWN_FAILED or
        REPLACEMENT_SPAWN_FAILED, terminal_at and runtime_terminal_event_id from the
        event, budget_consumed_at_terminal=event.TERMINAL_AT -
        snapshot_budget_started_at, and the documented terminal sentinels; retain the owner/lock until the
        normal parent recovery decision (original) or permanently close the replacement
        slot (replacement). On CAS loss return the same typed lost_operation handle
        to reconcile_runtime_event_cas_loss exactly once; on success return COMMITTED
        with the authoritative terminal row and its disposition. This is the only
        terminal path from UNADDRESSABLE and it cannot issue a stop or create a report.

reconcile_binding_failure_cas_loss(authoritative_row, failure):
    quarantine_cas_lost(failure)
    require the returned row matches the exact run/task/attempt/invocation/token,
        owner/lock, snapshot/content identity, unassigned identity fields, and the
        same reserved_runtime_target
    if the row is already terminal, accepted, quarantined, or has a non-NONE
       report/terminal identity:
        consume that authoritative winner and return
            {kind=AUTHORITATIVE_WINNER, authoritative_row=the returned row}
    if the row has CANCEL_REQUESTED while still unbound and its binding-failure fields
       are still UNSET:
        invoke record_binding_failure_cancel_requested_compare_and_set once against
            the complete returned row/version, preserving the existing cancellation
            sample/key and filling the exact binding-failure sample or
            UNKNOWN/UNVERIFIED sentinel; consume its winner on loss and never issue a
            stop from the sparse cancellation row
        if it returns RECORDED:
            return {kind=RECORDED, authoritative_row=the returned row}
        return {kind=AUTHORITATIVE_WINNER, authoritative_row=the returned row}
    if the row remains the exact live CLAIMED/RUNNING unbound row with report_id=NONE
       and overlay is NONE and binding_failure_provenance is NONE:
        invoke record_binding_failure_cancel_requested_compare_and_set once against this returned
        row/version with the same runtime sample (or UNKNOWN/UNVERIFIED sentinel)
        if it returns RECORDED:
            return {kind=RECORDED, authoritative_row=the returned row}
        consume its returned authoritative winner and return
            {kind=AUTHORITATIVE_WINNER, authoritative_row=the returned row}
    return {kind=BLOCKED_UNVERIFIED, authoritative_row=the returned row}

runtime_interruption_event_validates(result):
    require result.kind is RUNTIME_INTERRUPTION_EVENT, exact current task/attempt/
    invocation/token identity and either the bound agent or the reserved unbound
    runtime target, exact current/reserved agent channel and one-to-one transport
    invocation association, STATUS=interrupted,
    runtime-owned nonempty opaque unique EVENT_ID string,
    CLOCK_SOURCE equals the validated ledger clock_source,
    STOP_CONFIRMED=yes, runtime-owned RESUMABLE in {yes, no}, finite runtime-owned
    CANCEL_CONFIRMED_AT and TERMINAL_AT with CANCEL_CONFIRMED_AT <= TERMINAL_AT,
    and the current ledger row/version/state match
    require runtime_interruption_attestation_matches(result)
    if result.RESUMABLE is yes:
        require valid_checkpoint(result) and a matching artifact proof
    else:
        require result.CHECKPOINT_ID is NONE and
            result.CHECKPOINT_CONTENT_IDENTITY is NONE
        permit ARTIFACT_ACCESS_PROOF=NONE (or a separately attested proof) only for
            this confirmed non-resumable stop; do not invoke valid_checkpoint
    if spawn_confirmed_at is UNSET:
        require current_task_row.agent_id is UNASSIGNED,
            current_task_row.overlay in {NONE, CANCEL_REQUESTED},
            reserved_unbound_target_matches(result)
        if current_task_row.overlay is NONE:
            require current_task_row.binding_failed_at is UNSET and
                current_task_row.binding_failure_recovery_deadline_at is UNSET and
                current_task_row.cancel_requested_at is UNSET
        else:
            require finite runtime-owned binding_failed_at and
                binding_failure_recovery_deadline_at
        require result.terminal_at >= current_task_row.spawn_requested_at
        if current_task_row.overlay is not NONE:
            require result.terminal_at <= current_task_row.binding_failure_recovery_deadline_at
                unless is_late_runtime_stop_confirmation(result)
    if result is a bound runtime interruption and current_task_row.overlay is NONE and
       current_task_row.cancel_requested_at is UNSET:
        require spawn_confirmed_at is finite,
            current_task_row.agent_id is the exact bound runtime agent, and
            result.CANCEL_CONFIRMED_AT >= spawn_confirmed_at
        # This is an autonomous runtime stop; no parent cancellation request is needed.
    else if result is an unbound runtime interruption and
            current_task_row.overlay is NONE and
            current_task_row.cancel_requested_at is UNSET:
        require reserved_unbound_target_matches(result),
            result.CANCEL_CONFIRMED_AT >= current_task_row.spawn_requested_at
        # Binding-failure bookkeeping may race this event; the exact reserved target
        # and runtime stop proof are sufficient to avoid a duplicate stop request.
    else if (result is an unbound runtime interruption or
             result is an unbound RUNTIME_TERMINAL_EVENT with CHILD_STARTED=yes) and
            current_task_row.overlay is CANCEL_REQUESTED and
            current_task_row.binding_failure_provenance is SPAWN_UNCONFIRMED and
            current_task_row.cancel_requested_at is UNSET:
        require reserved_unbound_target_matches(result),
            result.CANCEL_CONFIRMED_AT >= current_task_row.spawn_requested_at
        # The runtime stop can race the parent's first CANCEL_REQUESTED CAS. It is
        # handled by the explicit unbound interruption CAS below; no stop request is
        # needed when this event already proves the child stopped.
    else if (result is a runtime interruption or
             result is a runtime terminal event with CHILD_STARTED=yes) and
            current_task_row.overlay is CANCEL_REQUESTED:
        require current_task_row.cancel_requested_at is finite and
            (current_task_row.cancel_requested_at <= result.CANCEL_CONFIRMED_AT or
             runtime_stop_preceded_cancel_request(result))
    require only the documented event fields and a bounded non-instruction-shaped
    INTERRUPTION_REASON; reject child report fields, free-form commands, or extra keys
    normalize the runtime-owned CLOCK_SOURCE field to result.clock_source, and the
    runtime-owned CANCEL_CONFIRMED_AT/TERMINAL_AT fields to the candidate ledger timing
    fields, before invoking terminal_timing_is_valid; if an existing non-UNSET timing
    value is present, it must equal the runtime value; if the current row already has a
    non-NONE interruption_event_id, it must equal result.EVENT_ID; do not require child
    report-envelope fields
    when this is an autonomous runtime stop or the stop-before-cancel race, invoke
        validate_deadline_inputs with runtime_autonomous_stop=yes,
        unbound_runtime_stop=yes only for the reserved-unbound branch,
        runtime_stop_preceded_cancel_request=yes exactly when that predicate holds,
        and unbound_stop_preceded_cancel_request=yes only for its unbound alias;
        otherwise use the normal bound/runtime-event timing mode
    return true only for this dedicated runtime-event schema; do not require child
    report ROLE, REPORT_ID, or common report-envelope fields

valid_checkpoint(result):
    require result.RESUMABLE is yes, CHECKPOINT_ID and CHECKPOINT_CONTENT_IDENTITY are
    runtime/parent-owned, immutable, present in the captured artifact, and match the
    current task's last coherent checkpoint; a child claim alone is not a checkpoint

reconcile_unbound_runtime_interruption(result):
    use only for a validated runtime RUNTIME_INTERRUPTION_EVENT whose current row has
        agent_id=UNASSIGNED, overlay in {NONE, CANCEL_REQUESTED}, the reserved
        unbound target and channel/association match, and either no binding-failure
        timing (overlay=NONE, event arrived before that CAS) or terminal_at at or
        before the fixed binding_failure_recovery_deadline_at
    atomically process the event through the complete current-row CAS below without
        first creating CANCEL_REQUESTED or issuing a stop request. For RESUMABLE=yes:
        partial_outcome = partial_compare_and_set_matches_current_row(result)
        if partial_outcome.kind is COMMITTED:
            current_task_row = partial_outcome.authoritative_row
            return PARTIAL
        if partial_outcome.kind is CAS_LOST:
            replay_outcome = reconcile_runtime_event_cas_loss(
                result, partial_outcome
            )
            replay_decision = consume_terminal_decision(replay_outcome)
            if replay_decision is PARTIAL: return PARTIAL
            return QUARANTINED
    For RESUMABLE=no, call materialize_confirmed_recovery_terminal with the same
        exact unbound target and runtime stop proof:
        preserve `binding_failure_provenance=SPAWN_UNCONFIRMED` until this terminal
        row is reconciled
        recovery_materialization = materialize_confirmed_recovery_terminal(
            result, result
        )
        if recovery_materialization.kind is COMMITTED:
            current_task_row = recovery_materialization.authoritative_row
            return RECOVERY_REQUIRED
        if recovery_materialization.kind is CAS_LOST:
            replay_outcome = reconcile_runtime_event_cas_loss(
                result, recovery_materialization
            )
            replay_decision = consume_terminal_decision(replay_outcome)
            if replay_decision is RECOVERY_REQUIRED:
                return RECOVERY_REQUIRED
            return QUARANTINED
        return QUARANTINED
    if that CAS loses because cancellation_request_compare_and_set won first, read the
        authoritative row once, revalidate the same runtime EVENT_ID/target/timing
        against its now-CANCEL_REQUESTED row, and enter the corresponding ordinary
        partial or non-resumable materialization branch without sending another stop;
        if a terminal/accepted/quarantined winner exists, preserve it; if identity or
        liveness cannot be proved, quarantine the event and retain the owner/lock
    a losing event CAS enters reconcile_runtime_event_cas_loss exactly once; it returns
        the idempotent winner when the same EVENT_ID is already materialized, or
        consumes the authoritative winner after one revalidated retry. A failed replay
        is QUARANTINED; a CAS loss never authorizes a stale stop or a second logical
        cancellation request

replay_runtime_interruption_after_row_race(result):
    invoke only when the dedicated runtime event has valid closed-schema,
        runtime-provenance, target, stop, and timing fields and
        runtime_interruption_event_validates failed solely because the current row
        version/overlay changed during the cancellation race
    read the authoritative row exactly once and require the same run/task/attempt/
        invocation/token, runtime target, channel/association, snapshot, and EVENT_ID
    if the winner is terminal, accepted, or quarantined, preserve that winner and do
        not reprocess the stale event; if it is the exact current row with
        `overlay=CANCEL_REQUESTED` and `binding_failure_provenance=SPAWN_UNCONFIRMED`
        (or the pre-cancellation row with `overlay=NONE` and provenance `NONE`),
        rebind the candidate to that row
        version, re-run the dedicated validator and terminal-timing validator, and
        return the normalized event; otherwise return INVALID

partial_compare_and_set_matches_current_row(result):
    require result is the validated runtime RUNTIME_INTERRUPTION_EVENT with
        RESUMABLE=yes, valid_checkpoint(result), and a unique runtime-owned EVENT_ID
    require the current row is the exact same run/task/parent-task/role/attempt/
        invocation/token, agent_id, agent_channel, transport invocation association,
        reserved_runtime_target,
        snapshot/content identity, clock source, owner, and lock; require every other
        canonical row field in the coordination-protocol schema is an explicit expected
        value, including version, objective/dependencies/scopes/baseline, execution and
        isolation mode, budget and all deadline/spawn/binding/terminal/cancellation
        fields, replacement fields/gate, report/disposition/result/integration/
        quarantine fields, attention, and the checkpoint/event/proof fields
    if current state is PARTIAL and interruption_event_id == result.EVENT_ID and
       checkpoint_id == result.CHECKPOINT_ID and
       checkpoint_content_identity == result.CHECKPOINT_CONTENT_IDENTITY and
       terminal_at == result.TERMINAL_AT and
       cancel_confirmed_at == result.CANCEL_CONFIRMED_AT:
        return COMMITTED with the authoritative PARTIAL row
    require current state is CLAIMED or RUNNING with report_id=NONE and
        attention_required=NONE, or current state is NEEDS_INPUT with the exact
        current attention report_id and nonempty attention_required preserved as the
        prior attention artifact; in either case terminal_at is UNSET and
        terminal_reason is NONE. Require overlay=NONE, or only when this exact runtime
        event is the on-time stop that won the cancellation CAS, overlay is
        CANCEL_REQUESTED and cancel_requested_at is finite, or when
        runtime_stop_preceded_cancel_request(result) proves the exact stop completed
        before the parent cancellation CAS. The latter branch retains
        overlay=CANCEL_REQUESTED, never issues a second stop request, and is valid
        for bound and reserved-unbound targets with the exact channel/association,
        event identity, and fixed timing. A pre-cancellation unbound event may also
        use overlay=NONE with binding_failure_provenance=NONE and the exact reserved
        target; it changes provenance only through this complete row transition.
    atomically compare-and-set that complete expected row, incrementing version, and
        write state=PARTIAL, retaining the current overlay/owner/lock,
        report_id=current_task_row.report_id and attention_required=
            current_task_row.attention_required when the prior state was NEEDS_INPUT,
            otherwise report_id=NONE and attention_required=NONE,
        interruption_event_id=result.EVENT_ID,
        checkpoint_id=result.CHECKPOINT_ID,
        checkpoint_content_identity=result.CHECKPOINT_CONTENT_IDENTITY,
        runtime_terminal_event_id=result.EVENT_ID,
        artifact_access_proof_id=the runtime/parent proof identity,
        terminal_at=result.TERMINAL_AT,
        cancel_confirmed_at=result.CANCEL_CONFIRMED_AT,
        result_content_identity=result.CHECKPOINT_CONTENT_IDENTITY,
        budget_consumed_at_terminal=result.TERMINAL_AT - snapshot_budget_started_at,
        terminal_reason=PARTIAL, report_disposition=none,
        integrated_content_identity=NONE and quarantine_reason=NONE, while preserving
        all task/snapshot/replacement/gate/budget fields and every identity not listed
        as a write
    if the CAS loses:
        return CAS_LOST with a typed lost_operation handle containing the complete
        expected/new FullTaskRow, exact runtime event identity, transaction winner,
        and one non-reconciling partial materializer; do not retry internally
    return COMMITTED with the authoritative PARTIAL row

canonical_timing_fields(timing):
    require timing exposes the canonical `deadline_at` and `_at` deadline names below; callers that
        receive legacy/local names must first copy them into a new record with the
        canonical names, never rely on truthiness or a fallback during encoding
    return this exact ordered list, with absent optional values normalized to their
    documented sentinel before encoding:
        [
            ("clock_source", timing.clock_source),
            ("snapshot_budget_started_at", timing.snapshot_budget_started_at),
            ("spawn_requested_at", timing.spawn_requested_at),
            ("spawn_confirmed_at", timing.spawn_confirmed_at),
            ("started_at", timing.started_at),
            ("binding_failed_at", timing.binding_failed_at),
            ("binding_failure_recovery_deadline_at",
                timing.binding_failure_recovery_deadline_at),
            ("prebinding_terminal_at", timing.prebinding_terminal_at),
            ("cancel_requested_at", timing.cancel_requested_at),
            ("cancel_confirmed_at", timing.cancel_confirmed_at),
            ("deadline_at", timing.deadline_at),
            ("snapshot_deadline_at", timing.snapshot_deadline_at),
            ("attempt_deadline_at", timing.attempt_deadline_at),
            ("recovery_deadline_at", timing.recovery_deadline_at),
            ("review_wait_budget", timing.review_wait_budget),
            ("review_initial_budget", timing.review_initial_budget),
            ("review_recovery_grace_budget", timing.review_recovery_grace_budget),
            ("review_replacement_decision_reserve_budget",
                timing.review_replacement_decision_reserve_budget),
            ("review_spawn_reserve_budget", timing.review_spawn_reserve_budget),
            ("review_replacement_min_budget", timing.review_replacement_min_budget),
            ("monotonic_now", timing.monotonic_now)
        ]

canonical_timing_digest(timing):
    return canonical_sha256_v1(canonical_timing_fields(timing))

canonical_optional_digest(record, field_name, absent_sentinel=UNSET):
    if field_name is absent from record:
        return absent_sentinel
    value = record[field_name]
    require value is NONE or value is UNSET or
        (value is text matching the canonical lowercase SHA-256 digest form)
    return value

prebinding_terminal_timing_is_valid(result):
    same_task_resume defaults to the invocation timing-context flag, otherwise no
    read the complete timing record only from runtime/parent metadata:
        clock_source, snapshot_budget_started_at, spawn_requested_at, spawn_confirmed_at, started_at,
        binding_failed_at, binding_failure_recovery_deadline_at,
        prebinding_terminal_at, cancel_requested_at, cancel_confirmed_at,
        deadline_at, snapshot_deadline, attempt_deadline, recovery_deadline, all review slices,
        and the current monotonic_now
    require one verified monotonic clock, finite/nonnegative durations, positive
        review slices/minimum, and the same total-budget inequality used by
        validate_deadline_inputs; reject any missing or non-finite absolute deadline
    require finite_monotonic(deadline_at) and deadline_at == current_task_row.deadline_at
    require snapshot_deadline == snapshot_budget_started_at + review_wait_budget
    require deadline_at is the exact current-row/runtime deadline for this invocation;
        it is not an alias for snapshot_deadline and must be validated independently
    require every optional sample is exactly UNSET/UNKNOWN as documented or finite;
        NaN, infinity, malformed text, or another sentinel is invalid, not absent
    require cancel_requested_at is not UNKNOWN; that fail-closed sentinel belongs only
        to an already blocked stop path and cannot be late-bound or accepted
    require spawn_requested_at <= prebinding_terminal_at <= monotonic_now
    require spawn_confirmed_at is UNSET or
        spawn_requested_at <= spawn_confirmed_at <= monotonic_now
    if started_at is a finite sample:
        require spawn_requested_at <= started_at <= prebinding_terminal_at and
            (spawn_confirmed_at is UNSET or spawn_confirmed_at <= started_at)
    if binding_failed_at is a finite sample:
        require (spawn_confirmed_at if finite else spawn_requested_at) <=
            binding_failed_at <= monotonic_now and
            binding_failure_recovery_deadline_at == min(
                snapshot_deadline,
                binding_failed_at + review_recovery_grace_budget
            ) and
            binding_failed_at <= binding_failure_recovery_deadline_at <= snapshot_deadline
    else:
        require binding_failure_recovery_deadline_at is UNSET
    if cancel_requested_at is a finite sample:
        require (spawn_confirmed_at if finite else spawn_requested_at) <=
            cancel_requested_at <= monotonic_now
    if cancel_confirmed_at is a finite sample:
        require cancel_requested_at is finite and
            cancel_requested_at <= cancel_confirmed_at <= monotonic_now
    else:
        require cancel_confirmed_at is UNSET
    if binding_failed_at is finite:
        require prebinding_terminal_at <= binding_failed_at
    if cancel_requested_at is finite:
        require prebinding_terminal_at <= cancel_requested_at
    if current_task_row.replacement_index > 0:
        require snapshot_deadline == current_task_row.snapshot_deadline_at and
            attempt_deadline == snapshot_deadline and
            recovery_deadline == snapshot_deadline and
            spawn_confirmed_at is UNSET or
                spawn_requested_at <= spawn_confirmed_at <= snapshot_deadline
    elif same_task_resume is yes:
        require attempt_deadline == resume_cutoff and
            recovery_deadline == resume_cutoff
    elif spawn_confirmed_at is a finite sample:
        require snapshot_deadline == snapshot_budget_started_at + review_wait_budget and
            attempt_deadline == min(snapshot_deadline,
                snapshot_budget_started_at + review_initial_budget) and
            recovery_deadline == min(snapshot_deadline,
                attempt_deadline + review_recovery_grace_budget)
    else:
        require parent/runtime deadline_derivation_proof binds these exact formulas
            to the reserved spawn-confirmation sample that is not yet in the row
    require snapshot_deadline >= attempt_deadline and
        recovery_deadline >= attempt_deadline
    cutoff = snapshot_deadline when current replacement_index > 0 else attempt_deadline
    require prebinding_terminal_at <= cutoff
    timing_fields = canonical_timing_fields({
        clock_source=clock_source,
        snapshot_budget_started_at=snapshot_budget_started_at,
        spawn_requested_at=spawn_requested_at,
        spawn_confirmed_at=spawn_confirmed_at,
        started_at=started_at,
        binding_failed_at=binding_failed_at,
        binding_failure_recovery_deadline_at=binding_failure_recovery_deadline_at,
        prebinding_terminal_at=prebinding_terminal_at,
        cancel_requested_at=cancel_requested_at,
        cancel_confirmed_at=cancel_confirmed_at,
        deadline_at=deadline_at,
        snapshot_deadline_at=snapshot_deadline,
        attempt_deadline_at=attempt_deadline,
        recovery_deadline_at=recovery_deadline,
        review_wait_budget=review_wait_budget,
        review_initial_budget=review_initial_budget,
        review_recovery_grace_budget=review_recovery_grace_budget,
        review_replacement_decision_reserve_budget=
            review_replacement_decision_reserve_budget,
        review_spawn_reserve_budget=review_spawn_reserve_budget,
        review_replacement_min_budget=review_replacement_min_budget,
        monotonic_now=monotonic_now
    })
    timing_digest = canonical_timing_digest({
        clock_source=clock_source,
        snapshot_budget_started_at=snapshot_budget_started_at,
        spawn_requested_at=spawn_requested_at,
        spawn_confirmed_at=spawn_confirmed_at,
        started_at=started_at,
        binding_failed_at=binding_failed_at,
        binding_failure_recovery_deadline_at=binding_failure_recovery_deadline_at,
        prebinding_terminal_at=prebinding_terminal_at,
        cancel_requested_at=cancel_requested_at,
        cancel_confirmed_at=cancel_confirmed_at,
        deadline_at=deadline_at,
        snapshot_deadline_at=snapshot_deadline,
        attempt_deadline_at=attempt_deadline,
        recovery_deadline_at=recovery_deadline,
        review_wait_budget=review_wait_budget,
        review_initial_budget=review_initial_budget,
        review_recovery_grace_budget=review_recovery_grace_budget,
        review_replacement_decision_reserve_budget=
            review_replacement_decision_reserve_budget,
        review_spawn_reserve_budget=review_spawn_reserve_budget,
        review_replacement_min_budget=review_replacement_min_budget,
        monotonic_now=monotonic_now
    })
    return {timing_fields, timing_digest} only before any report row is created;
        otherwise INVALID

terminal_timing_is_valid(result):
    same_task_resume defaults to the invocation timing-context flag, otherwise no
    require result.clock_source equals the validated ledger clock_source
    require the runtime-owned monotonic terminal_at to be finite and tied to the
        current task/attempt/invocation
    require every optional timing value is exactly its documented UNSET/UNKNOWN
        sentinel or a finite sample; malformed text, NaN, infinity, or an unknown
        sentinel is invalid rather than absent
    require cancel_requested_at is not UNKNOWN; an unverified cancellation timestamp
        cannot authorize a terminal result or recovery
    require finite_monotonic(result.deadline_at) and
        result.deadline_at == current_task_row.deadline_at
    require snapshot_deadline == snapshot_budget_started_at + review_wait_budget
    require binding_failure_recovery_deadline_at is UNSET iff binding_failed_at is UNSET
    if binding_failed_at is finite:
        require binding_failure_recovery_deadline_at == min(
            snapshot_deadline,
            binding_failed_at + review_recovery_grace_budget
        )
    if current_task_row.replacement_index > 0:
        require snapshot_deadline == current_task_row.snapshot_deadline_at and
            attempt_deadline == snapshot_deadline and
            recovery_deadline == snapshot_deadline
    elif same_task_resume is yes:
        require attempt_deadline == resume_cutoff and
            recovery_deadline == resume_cutoff
    else:
        require attempt_deadline == min(snapshot_deadline,
                snapshot_budget_started_at + review_initial_budget) and
            recovery_deadline == min(snapshot_deadline,
                attempt_deadline + review_recovery_grace_budget)
    if result carries a runtime-captured prebinding_terminal_at:
        require spawn_requested_at <= prebinding_terminal_at <= monotonic_now
        require terminal_at == prebinding_terminal_at
        lower_bound = spawn_requested_at
    elif result.kind is RUNTIME_TERMINAL_EVENT and
         result.STATUS is spawn_failed and result.CHILD_STARTED is no:
        require spawn_requested_at <= terminal_at <= monotonic_now and
            result.CANCEL_CONFIRMED_AT is UNSET
        lower_bound = spawn_requested_at
    elif (result.kind is RUNTIME_INTERRUPTION_EVENT or
          (result.kind is RUNTIME_TERMINAL_EVENT and
           result.CHILD_STARTED is yes)) and
         current_task_row.agent_id is UNASSIGNED and
         current_task_row.overlay in {NONE, CANCEL_REQUESTED} and
         reserved_unbound_target_matches(result):
        if current_task_row.overlay is NONE:
            require current_task_row.binding_failed_at is UNSET and
                current_task_row.binding_failure_recovery_deadline_at is UNSET
        else:
            require finite(current_task_row.binding_failed_at) and
                finite(current_task_row.binding_failure_recovery_deadline_at)
        require spawn_requested_at <= terminal_at <= monotonic_now
        lower_bound = spawn_requested_at
    else:
        require spawn_confirmed_at <= terminal_at <= monotonic_now
        lower_bound = spawn_confirmed_at
    require lower_bound <= terminal_at <= monotonic_now
    if started_at is a finite sample:
        require started_at <= terminal_at
    if cancel_requested_at is a finite sample:
        require (spawn_confirmed_at if finite else spawn_requested_at) <=
            cancel_requested_at <= monotonic_now
    if cancel_confirmed_at is a finite sample:
        if cancel_requested_at is finite:
            if runtime_stop_preceded_cancel_request(result):
                require spawn_requested_at <= cancel_confirmed_at <= terminal_at <=
                    cancel_requested_at <= monotonic_now
            else:
                require cancel_requested_at <= cancel_confirmed_at <= monotonic_now
        elif (result is a bound runtime interruption or
              result is a bound RUNTIME_TERMINAL_EVENT with CHILD_STARTED=yes) and
             current_task_row.overlay is NONE and
             current_task_row.agent_id is not UNASSIGNED:
            require spawn_confirmed_at <= cancel_confirmed_at <= monotonic_now
        elif (result is an unbound runtime interruption or
              result is an unbound RUNTIME_TERMINAL_EVENT with CHILD_STARTED=yes) and
             current_task_row.overlay is NONE and
             current_task_row.binding_failure_provenance is NONE and
             current_task_row.agent_id is UNASSIGNED and
             reserved_unbound_target_matches(result):
            require spawn_requested_at <= cancel_confirmed_at <= terminal_at <= monotonic_now
        else:
            reject: a cancellation confirmation without a request is valid only for
                a dedicated autonomous runtime interruption or runtime terminal event
                with an exact bound or reserved unbound target
    if result is a runtime-confirmed cancellation/shutdown event:
        require cancel_confirmed_at is finite and cancel_confirmed_at <= terminal_at
    late_runtime_stop_confirmation = is_late_runtime_stop_confirmation(result)
    if late_runtime_stop_confirmation:
        require cancel_confirmed_at is finite and
            ((cancel_requested_at <= cancel_confirmed_at <= terminal_at <= monotonic_now) or
             (runtime_stop_preceded_cancel_request(result) and
              terminal_at <= cancel_requested_at <= monotonic_now))
    cutoff = UNSET
    if result is a role-complete reviewer/role result:
        cutoff = snapshot_deadline when replacement_index > 0 else attempt_deadline
    if result.status is PARTIAL:
        # A normal reviewer PARTIAL may be delivered by the bounded recovery wait;
        # it is eligible for same-task continuation only through the remaining
        # recovery window, never through a fresh initial slice.
        cutoff = snapshot_deadline when replacement_index > 0 else recovery_deadline
    if result is a runtime stop/interruption or FAILED/CANCELLED/error result:
        if late_runtime_stop_confirmation:
            cutoff = UNSET; this event confirms stop after the fixed deadline and
                cannot authorize review acceptance, replacement, takeover, or unlock
        else:
            cutoff = binding_failure_recovery_deadline when it is finite, otherwise
                     recovery_deadline while the normal cancellation handshake is active
    if cutoff is unset and result is any terminal report/event and
       not late_runtime_stop_confirmation:
        cutoff = snapshot_deadline when replacement_index > 0 else attempt_deadline
    if late_runtime_stop_confirmation:
        require terminal_at <= monotonic_now; this is stop confirmation only
    else:
        require terminal_at <= cutoff; a missing, untrusted, future, early, or late
            terminal timestamp is invalid and must be quarantined rather than converting
            recovery time into review

late_bind_report_compare_and_set(candidate, runtime_agent_id, runtime_channel,
                                transport_association, parent_report_id,
                                prebinding_timing, replacement_budget_gate):
    this is the only operation that may turn a scanned PRE_BINDING_PAYLOAD into a
    report.  It supports both legal arrival orders with one full-row CAS:
    runtime_artifact_access_proof = runtime_attested_artifact_access_for(candidate)
        (required for every transport_bound_provisional role); its opaque identity, not
        proof contents, is the value persisted in artifact_access_proof_id
    require the current row matches the exact expected row_version, run_id, task_id,
        parent_task_id, role, attempt, invocation_id, binding_token, snapshot_id,
        content_identity, snapshot_budget_started_at, owner, lock, agent_channel,
        transport_invocation_association, reserved_runtime_target, and
        runtime_terminal_event_id; expected report_id is NONE,
        late_bind_at is UNSET, state is CLAIMED or RUNNING, and overlay is NONE
        (both binding-failure provenance and CANCEL_REQUESTED are losing races;
        SPAWN_UNCONFIRMED is provenance only and can never authorize late binding)
    if current_task_row.agent_id is UNASSIGNED:
        require current_task_row.agent_channel is UNASSIGNED and
            current_task_row.transport_invocation_association is UNASSIGNED
        require the returned runtime agent, channel, and one-to-one transport
            association are the reserved values for the exact invocation/token
        set new_agent_id=runtime_agent_id,
            new_agent_channel=the returned runtime agent channel,
            new_transport_invocation_association=
                the returned one-to-one transport association
    else:
        require current_task_row.agent_id == runtime_agent_id and
            current_task_row.agent_channel == the returned runtime agent channel and
            current_task_row.transport_invocation_association ==
                the returned one-to-one transport association
        set new_agent_id=current_task_row.agent_id,
            new_agent_channel=current_task_row.agent_channel,
            new_transport_invocation_association=
                current_task_row.transport_invocation_association
    require the terminal transport event arrived on that agent channel, its
        one-to-one invocation association and token are exact, the immutable
        snapshot/content identities and runtime artifact/coverage proofs match,
        cancellation/replacement has not won, and the candidate timing digest and
        replacement gate are valid before the write
    atomically compare-and-set all of the following expected values and writes:
        expected agent_id (UNASSIGNED or the already bound runtime agent),
        expected agent_channel (UNASSIGNED or the exact already bound runtime
            channel), expected transport_invocation_association (UNASSIGNED or the
            exact already bound one-to-one association),
        expected reserved_runtime_target=current_task_row.reserved_runtime_target,
        expected runtime_terminal_event_id=current_task_row.runtime_terminal_event_id,
        expected report_id=NONE, expected late_bind_at=UNSET, expected state/overlay,
        expected terminal_at=current_task_row.terminal_at,
        expected prebinding_terminal_at=current_task_row.prebinding_terminal_at,
        expected prebinding_timing_digest=current_task_row.prebinding_timing_digest,
        expected budget_consumed_at_terminal=current_task_row.budget_consumed_at_terminal,
        expected interruption_event_id=current_task_row.interruption_event_id,
        expected checkpoint_id=current_task_row.checkpoint_id,
        expected checkpoint_content_identity=current_task_row.checkpoint_content_identity,
        expected artifact_access_proof_id=current_task_row.artifact_access_proof_id,
        expected replacement_gate_digest=NONE for the original or the validated
            replacement row's current digest sentinel,
        new agent_id=the exact runtime agent,
        new agent_channel=the exact returned runtime channel,
        new_transport_invocation_association=the exact returned one-to-one
            association, new report_id=parent_report_id,
        new reserved_runtime_target=current_task_row.reserved_runtime_target,
        new runtime_terminal_event_id=current_task_row.runtime_terminal_event_id,
        new state=COMPLETED, new overlay=NONE,
        new late_bind_at=the runtime/parent monotonic bind sample,
        new terminal_at=prebinding_timing.timing_fields.prebinding_terminal_at,
        new prebinding_terminal_at=the same exact sample,
        new prebinding_timing_digest=prebinding_timing.timing_digest,
        new terminal_reason=NONE, new report_disposition=none,
        new result_content_identity=the validated candidate identity,
        new budget_consumed_at_terminal=the validated runtime/parent duration,
        new interruption_event_id=current_task_row.interruption_event_id,
        new checkpoint_id=current_task_row.checkpoint_id,
        new checkpoint_content_identity=current_task_row.checkpoint_content_identity,
        new artifact_access_proof_id=runtime_artifact_access_proof.identity for a
            reviewer, otherwise NONE,
        new owner/lock=the current owner/lock, and the complete validated timing,
            artifact proof, coverage proof, and replacement-gate metadata
    no report row is created before this CAS; a CAS loss returns CAS_LOST to the
        single late-bind reconciliation path below and never authorizes the candidate
        or changes the winning row, owner, lock, or dependency result

late_bind_report_compare_and_set_once(values):
    execute one invocation of the complete expected/new projection above and return
    COMMITTED or CAS_LOST with the transaction's authoritative winning row; this is
    the non-reconciling primitive used by the initial late-bind and its single race
    retry, and it must never call reconcile_late_bind_cas_loss recursively

reconcile_late_bind_cas_loss(candidate, runtime_agent_id, runtime_channel,
                             transport_association, parent_report_id,
                             scanned_candidate_digest, prebinding_timing,
                             replacement_budget_gate):
    read the authoritative row exactly once after the late-bind CAS loses and match
        the exact run/task/parent-task/attempt/invocation/token, snapshot/content,
        owner/lock, and one-to-one runtime channel/association
    if the row is terminal, accepted, CANCEL_REQUESTED, quarantined, or bound to a
        different agent/channel/association, preserve that authoritative winner and
        return AUTHORITATIVE_WINNER; never create a second report or retry against a
        different invocation
    if the row is COMPLETED with report_id=parent_report_id, the exact returned
        agent/channel/association, the same candidate result identity, prebinding
        timing digest, runtime terminal identity, and replacement gate, return
        ALREADY_COMMITTED; this is an idempotent replay of the same parent-owned
        report, not permission to create or commit another report
    if the row is the same live invocation with state in {CLAIMED, RUNNING},
        report_id=NONE, late_bind_at=UNSET, overlay is NONE,
        agent_id=runtime_agent_id, agent_channel=runtime_channel, and
        transport_invocation_association=transport_association:
        revalidate the scanned candidate, artifact/coverage proofs, timing digest,
            and replacement gate against this current row; then perform exactly one
            second `late_bind_report_compare_and_set_once` full-row CAS with the
            current row version, bound channel, association, and all current expected
            fields. If it wins, return COMMITTED;
            if it loses, consume the transaction-returned authoritative winner and
            return CAS_LOST without another retry.
    otherwise preserve the row, quarantine only the redacted candidate metadata, and
        return QUARANTINED

canonicalize_terminal(result):
    if result.kind is not PRE_BINDING_PAYLOAD:
        return result
    if stored_role is implementer:
        return UNUSABLE
    if not runtime_terminal_transport_matches(result):
        return UNUSABLE
    if any required run/task/invocation/attempt/token or snapshot identity is missing or does not match:
        return UNUSABLE
    runtime_artifact_access_proof = runtime_attested_artifact_access_for(result)
    if not runtime_artifact_access_proof:
        return UNUSABLE
    runtime_review_coverage_proof = runtime_review_coverage_proof_for(result)
    scanned_candidate_digest = prebinding_payload_scan_passes(
        result, runtime_artifact_access_proof, runtime_review_coverage_proof
    )
    if scanned_candidate_digest is INVALID:
        return UNUSABLE
    prebinding_timing = prebinding_terminal_timing_is_valid(result)
    if prebinding_timing is INVALID:
        return UNUSABLE
    replacement_budget_gate = validated_replacement_budget_gate(result)
    if replacement_budget_gate is INVALID:
        return UNUSABLE
    if result supplies an AGENT_ID, AGENT_CHANNEL, TRANSPORT_INVOCATION_ASSOCIATION,
       RESERVED_RUNTIME_TARGET, REPORT_ID, RUNTIME_TERMINAL_EVENT_ID, or timing
       key/value, or status is not role-complete:
        return UNUSABLE
    if not runtime_attested_artifact_access_matches(runtime_artifact_access_proof, result):
        return UNUSABLE
    if cancellation_or_replacement_transition_has_won:
        return UNUSABLE
    parent attaches the transport agent_id, a new report_id, and
    terminal_at=prebinding_timing.timing_fields.prebinding_terminal_at, plus canonical timing metadata,
    the runtime-attested artifact access proof, and the parent/runtime-attested coverage proof
    late_bind_outcome = late_bind_report_compare_and_set(
           candidate=result,
           runtime_agent_id=the exact returned transport agent,
           runtime_channel=the returned runtime agent channel,
           transport_association=the returned one-to-one association,
           parent_report_id=parent_created_report_id,
           expected_run_id=run_id,
           expected_task_id=task_id,
           expected_parent_task_id=parent_task_id,
           expected_role=stored_role,
           expected_row_version=current_task_row.version,
           expected_attempt=attempt,
           expected_invocation_id=invocation_id,
           expected_binding_token=binding_token,
           expected_agent_id=UNASSIGNED or the exact already bound transport agent,
           expected_state in {CLAIMED, RUNNING},
           expected_overlay=NONE,
           expected_report_id=NONE,
           expected_late_bind_at=UNSET,
           expected_snapshot_id=snapshot_id,
           expected_content_identity=content_identity,
           expected_snapshot_budget_started_at=current_task_row.snapshot_budget_started_at,
           expected_owner=current_task_row.owner,
           expected_lock=current_task_row.lock,
           expected_candidate_digest=scanned_candidate_digest,
           expected_review_coverage_proof=runtime_review_coverage_proof,
           expected_budget_gate=replacement_budget_gate,
           expected_prebinding_timing=prebinding_timing.timing_fields,
           expected_candidate_prebinding_timing_digest=prebinding_timing.timing_digest,
           expected_prebinding_timing_digest=current_task_row.prebinding_timing_digest,
           expected_prebinding_terminal_at=current_task_row.prebinding_terminal_at,
           expected_terminal_at=current_task_row.terminal_at,
           expected_reserved_runtime_target=current_task_row.reserved_runtime_target,
           expected_runtime_terminal_event_id=current_task_row.runtime_terminal_event_id,
           expected_agent_channel=UNASSIGNED or the exact already bound runtime
               channel,
           expected_transport_invocation_association=UNASSIGNED or the exact already
               bound one-to-one association,
           expected_one_to_one_invocation_association=yes,
           new_agent_id=the exact returned transport agent,
           new_agent_channel=the exact returned runtime agent channel,
           new_transport_invocation_association=
               the exact returned one-to-one association,
           new_reserved_runtime_target=current_task_row.reserved_runtime_target,
           new_runtime_terminal_event_id=current_task_row.runtime_terminal_event_id,
           new_report_id=parent_created_report_id,
           new_state=COMPLETED,
           new_overlay=NONE,
           new_late_bind_at=runtime_parent_monotonic_bind_sample,
           new_prebinding_timing_digest=prebinding_timing.timing_digest,
           new_prebinding_terminal_at=prebinding_timing.timing_fields.prebinding_terminal_at,
           new_terminal_at=prebinding_timing.timing_fields.prebinding_terminal_at,
           new_terminal_reason=NONE,
           new_report_disposition=none,
           new_owner=current_task_row.owner,
           new_lock=current_task_row.lock,
           expected_replacement_gate_digest=NONE for original or
               replacement_budget_gate.expected_row_gate_digest,
           new_replacement_gate_digest=NONE for original or replacement_budget_gate.digest
       )
    if late_bind_outcome.kind is CAS_LOST:
        reconciliation = reconcile_late_bind_cas_loss(
            result, the exact returned transport agent, the returned runtime channel,
            the returned one-to-one association, parent_created_report_id,
            scanned_candidate_digest, prebinding_timing, replacement_budget_gate
        )
        if reconciliation.kind is COMMITTED:
            return {kind=LATE_BIND_COMMITTED, report_id=parent_created_report_id,
                row=reconciliation.authoritative_row}
        if reconciliation.kind is ALREADY_COMMITTED:
            return {kind=LATE_BIND_ALREADY_COMMITTED,
                report_id=parent_created_report_id,
                row=reconciliation.authoritative_row}
        quarantine_cas_lost(result)
        return UNUSABLE
    if late_bind_outcome.kind is COMMITTED:
        return {kind=LATE_BIND_COMMITTED, report_id=parent_created_report_id,
            row=the committed authoritative row}
    if late_bind_outcome.kind is ALREADY_COMMITTED:
        return {kind=LATE_BIND_ALREADY_COMMITTED, report_id=parent_created_report_id,
            row=the authoritative row}
    return UNUSABLE

verify_late_bound_report(outcome):
    require outcome.kind in {LATE_BIND_COMMITTED, LATE_BIND_ALREADY_COMMITTED}
    read the authoritative row once and require state=COMPLETED, report_id=outcome.report_id,
        exact current run/task/attempt/invocation/token, agent/channel/association,
        reserved runtime target, snapshot/content identity, runtime terminal identity,
        prebinding timing digest, replacement gate, owner, and lock
    require the parent-owned report record is the one created by the successful late-bind
        CAS and revalidate its closed reviewer semantics, artifact/coverage proofs, and
        fixed timing; do not call terminal_compare_and_set_matches_current_row
    return LATE_BIND_VERIFIED or INVALID

post_spawn_reconcile_after_late_bind_compare_and_set(values):
    permit this path only when a prebinding CAS already created the parent-owned
    report and replacement gate. Match the exact current row version, task/attempt/
    invocation/agent/token/report/gate identities, and require the bound envelope's
    terminal_at to remain exactly the validated prebinding_terminal_at. If post_spawn
    supplies samples that were UNSET in the prebinding record, merge them only through
    one same-clock validation of all lower bounds, fixed deadlines, exact deadline
    derivations, cancellation/binding-failure relationships, and terminal_at equality;
    recompute the complete timing digest and include both the candidate timing digest
    and the current row's old digest/prebinding/terminal fields as CAS expectations.
    The CAS is a complete canonical-row projection: its expected side includes every
    task, scope, identity, channel/association, state/overlay, timing, budget,
    replacement, report/disposition, checkpoint/proof, integration, quarantine,
    attention, owner, lock, reserved_runtime_target, binding_failure_provenance, and
    runtime_terminal_event_id field; its new side explicitly preserves every field
    not being merged. A successful CAS may update those runtime-owned samples and the
    digest atomically, but must preserve the report, terminal time, state, owner, lock,
    and replacement gate.
    If revalidation or digest recomputation fails, keep the original timing/digest
        unchanged and quarantine the race; never write an unverified sample after a report
        has been created.
    new_merged_timing must be a closed record with the exact canonical `_at` deadline
        keys consumed by canonical_timing_fields; normalize local aliases before this
        point and never let the digest helper choose between names or values

post_spawn_reconcile_original_attempt_after_late_bind_compare_and_set(values):
    invoke this immediately after every original-attempt post_spawn return and before
    entering the ordinary wait. Read the authoritative row once:
    if state is PARTIAL:
        require the row's runtime_terminal_event_id is the exact validated runtime
            interruption/terminal event for the returned invocation, with matching
            target, channel, association, checkpoint, and terminal timing
        current_task_row = the authoritative PARTIAL row
        return {status=AUTHORITATIVE_WINNER, disposition=PARTIAL,
                row=current_task_row,
                runtime_event_or_report=the exact validated partial event};
            do not bind, rewrite, or send a stop
    if state is in {FAILED, CANCELLED, BLOCKED, QUARANTINED} or
       overlay is CANCEL_REQUESTED:
        preserve the authoritative winner and return
            {status=AUTHORITATIVE_WINNER, disposition=the row's authoritative
             disposition, row=current_task_row};
        do not bind, transition, or stop from the post_spawn response
    if report_id is NONE:
        require current row is a live CLAIMED or RUNNING row with overlay=NONE,
            terminal_at=UNSET, runtime_terminal_event_id=NONE, and no cancellation or
            replacement winner; a terminal, quarantined, or CANCEL_REQUESTED row is an
            authoritative winner and cannot be rebound
        if current_task_row.agent_id is UNASSIGNED:
            perform the normal post_spawn binding CAS with expected row_version,
            run/task/parent-task/attempt/invocation/token, agent_id=UNASSIGNED,
            agent_channel=UNASSIGNED, transport_invocation_association=UNASSIGNED,
            reserved_runtime_target=current_task_row.reserved_runtime_target,
            runtime_terminal_event_id=current_task_row.runtime_terminal_event_id,
            snapshot/content, state, overlay=NONE,
            binding_failure_provenance=NONE, owner, and lock; the complete expected
            side also includes every canonical scope, timing, budget, replacement,
            report, checkpoint/proof, attention, integration, and quarantine field.
            Bind the exact runtime agent, returned channel, one-to-one association,
            and confirmed timing while explicitly writing report_id=NONE,
            new_state=RUNNING, new_overlay=NONE,
            new_binding_failure_provenance=NONE, and explicitly writing every other
            canonical field.
        else:
            require current_task_row.agent_id is the exact returned runtime agent and
                current_task_row.agent_channel is the returned channel and
                current_task_row.transport_invocation_association is the returned
                one-to-one association
            perform an idempotent complete-row CAS with those already-bound identities
                as both expected and new values, the exact target/token/timing and every
                other canonical field matched explicitly, and write only
                new_state=RUNNING, new_overlay=NONE, report_id=NONE. This same-identity
                winner is authoritative; do not rebind, rewrite, stop, or create a
                second report. CLAIMED is therefore never left as the post_spawn live
                state merely because the binding CAS succeeded.
    else:
        invoke post_spawn_reconcile_after_late_bind_compare_and_set with the exact
        current row/version, run/task/parent-task/attempt/invocation/token/agent/
        channel/association, report_id, terminal_at, prebinding_terminal_at, both old timing digests,
        snapshot/content identity, replacement-gate values/sentinel, owner, and lock;
        preserve the parent-created report and exact prebinding terminal sample while
        atomically merging only fully revalidated post_spawn samples, while expecting
        and preserving reserved_runtime_target and runtime_terminal_event_id exactly
    in either branch, require the returned agent channel and one-to-one invocation
        association. If this binding/reconciliation CAS loses, or the association
        cannot be proved, quarantine the candidate and call
        reconcile_original_post_spawn_cas_loss with the exact returned runtime target,
        channel, and one-to-one transport association before returning
        `{status=CAS_LOST, reconciliation_outcome}`.
        Never overwrite a report, create a second report, or let a late-bind-first race
        enter ordinary wait without this reconciliation. On success return
        `{status=BOUND}` when report_id remains NONE, or
        `{status=RECONCILED, kind=LATE_BIND_ALREADY_COMMITTED, report_id,
          row=authoritative_row}` when the late-bind report already won. The latter
        is a terminal handoff and must go through verify_late_bound_report exactly once.

reconcile_original_post_spawn_cas_loss(values):
    quarantine the losing binding candidate, then read the authoritative row exactly
    once and match run/task/parent-task/attempt/invocation/token, snapshot/content
    identity, the exact returned agent_id, agent channel, transport association,
    reserved_runtime_target, runtime_terminal_event_id, owner, and lock
    if the authoritative row is PARTIAL:
        require its runtime_terminal_event_id is the exact validated runtime event for
            the returned invocation, with matching target/channel/association and
            checkpoint/timing identity
        preserve that authoritative PARTIAL row and return
            {status=AUTHORITATIVE_WINNER, disposition=PARTIAL,
             row=authoritative_row,
             runtime_event_or_report=the exact validated partial event};
            do not bind or stop from the stale candidate
    if the authoritative row is terminal, accepted, confirmed-stop, quarantined, or
       bound to a different runtime identity:
        preserve that winner and issue no stop from the stale candidate; return
        {status=AUTHORITATIVE_WINNER, disposition=the row's authoritative disposition,
         row=authoritative_row}
    if the authoritative row is the exact live unbound invocation with
       agent_id=UNASSIGNED, overlay=NONE, binding_failure_provenance=NONE,
       report_id=NONE, and its reserved_runtime_target is the exact target returned
       for this invocation:
        # post_spawn may have returned an agent after this row read but before the
        # binding CAS. Treat the returned target as a binding failure, not as proof
        # that the unbound row is safe to abandon.
        binding_failure_outcome =
            record_binding_failure_cancel_requested_compare_and_set(
                authoritative_row,
                binding_failed_at=the validated runtime binding-failure sample or
                    the current monotonic sample or UNKNOWN,
                runtime_target=authoritative_row.reserved_runtime_target,
                exact run/task/attempt/invocation/token, owner, lock, and identities
            )
        if binding_failure_outcome.kind is CAS_LOST:
            consume its transaction-returned authoritative winner and return
                AUTHORITATIVE_WINNER without issuing a stop from the stale row
        if binding_failure_outcome.kind is not RECORDED:
            consume the authoritative winner and return AUTHORITATIVE_WINNER
        current_task_row = binding_failure_outcome.authoritative_row
        stop_outcome = stop_at_deadline_or_recovery_boundary()
        if stop_outcome.kind is AUTHORITATIVE_WINNER:
            return AUTHORITATIVE_WINNER without a stale stop request
        return STOP_REQUESTED or STOP_ALREADY_REQUESTED
    if the authoritative row is the exact same live invocation already bound to the
       returned agent_id, agent_channel, and transport association, with the same
       token/target and overlay=NONE:
        return {kind=ALREADY_BOUND, authoritative_row=authoritative_row}; do not issue
        a stop, do not rewrite the row, and do not create a second binding/report
    if the exact row already has overlay=CANCEL_REQUESTED:
        if the row is still unbound and its binding-failure fields are not complete:
            binding_failure_outcome =
                record_binding_failure_cancel_requested_compare_and_set(
                    authoritative_row,
                    binding_failed_at=the validated runtime binding-failure sample or
                        the current monotonic sample or UNKNOWN,
                    runtime_target=authoritative_row.reserved_runtime_target,
                    exact run/task/attempt/invocation/token, owner, lock, and identities
                )
            if binding_failure_outcome.kind is CAS_LOST:
                consume its transaction-returned winner and return AUTHORITATIVE_WINNER
            if binding_failure_outcome.kind is not RECORDED:
                consume its authoritative winner and return AUTHORITATIVE_WINNER
            authoritative_row = binding_failure_outcome.authoritative_row
        reuse its cancellation idempotency key and issue at most the one idempotent stop
        to the exact returned runtime target only after the row's agent/channel/
        transport association, reserved_runtime_target, and invocation target still
        match; return STOP_ALREADY_REQUESTED
    if the exact row is live with overlay=NONE:
        cancellation_outcome = cancellation_request_compare_and_set against that
            complete authoritative row before sending anything
        if cancellation_outcome.kind is CAS_LOST:
            consume its transaction-returned winner and issue no stop from stale state;
            return {kind=AUTHORITATIVE_WINNER,
                    authoritative_row=cancellation_outcome.authoritative_row}
        require cancellation_outcome.kind is COMMITTED
        authoritative_row = cancellation_outcome.authoritative_row
        issue exactly one idempotent stop to the exact returned runtime target using the
        deterministic invocation key only after the agent_id/channel/association/
        reserved_runtime_target match;
        return {kind=STOP_REQUESTED, authoritative_row=authoritative_row}
    retain the authoritative owner/lock and quarantine the candidate; return
        {kind=AUTHORITATIVE_WINNER, disposition=authoritative_row.disposition,
         authoritative_row=authoritative_row}

handoff_authoritative_partial_after_post_spawn(outcome):
    require outcome.disposition is PARTIAL and outcome.row is the authoritative
        current row whose exact runtime terminal/interruption event matches the
        returned invocation, target, channel/association, checkpoint, and timing
    current_task_row = outcome.row
    # A post_spawn race may discover that runtime interruption materialized before
    # binding. It is not safe to stop and forget that resumable state: hand it to the
    # same bounded resume/recovery decision used by a foreground PARTIAL result.
    if role is reviewer:
        if reviewer_resumable_partial_is_authorized(outcome.runtime_event_or_report):
            resume_resumable_partial(outcome.runtime_event_or_report)
        else:
            recover_or_block()
    else:
        resume/re-plan according to RESUMABLE and role contract
    stop without binding, ordinary wait, or a second stop request

role_complete_status = {
    researcher: {RESEARCH_READY},
    planner: {PLAN_READY},
    implementer: {CHECKPOINT_READY},
    verifier: {VERIFICATION_READY},
    reviewer: {CLEAN, FINDINGS}
}

runtime_terminal_or_cancellation_is_confirmed(result):
    return true only when a validated runtime-owned RUNTIME_INTERRUPTION_EVENT has a
    nonempty EVENT_ID, or a validated RUNTIME_TERMINAL_EVENT has a nonempty independent
    RUNTIME_TERMINAL_EVENT_ID, together with exact current task/attempt/invocation/
    channel/association/target identity and a confirmed process stop (or
    CHILD_STARTED=no spawn-failure proof); a ledger label such as "terminal" or a
    child-authored FAILED/CANCELLED field is not enough

runtime_terminal_event_id(result):
    return the nonempty runtime-owned event identity for the confirmed stop/terminal
    event; use result.EVENT_ID for a validated RUNTIME_INTERRUPTION_EVENT, and
    result.RUNTIME_TERMINAL_EVENT_ID for a validated RUNTIME_TERMINAL_EVENT. A child
    report ID or child prose is not a terminal-event identity; return INVALID when the
    runtime has not supplied one.

current_attempt_runtime_terminal_or_cancellation_is_confirmed:
    the current ledger row contains the validated runtime-owned event above; a child
    status or the ledger label "terminal" alone never satisfies this predicate

stop_confirmation_deadline(result):
    if current_task_row.overlay is not CANCEL_REQUESTED:
        return UNSET
    if current_task_row.binding_failure_recovery_deadline_at is finite:
        return current_task_row.binding_failure_recovery_deadline_at
    return current_task_row.recovery_deadline_at

is_late_runtime_stop_confirmation(result):
    stop_deadline = stop_confirmation_deadline(result)
    return true only when result is a runtime-owned STOP_CONFIRMED event (including a
    RUNTIME_INTERRUPTION_EVENT with STOP_CONFIRMED=yes or a
    RUNTIME_TERMINAL_EVENT with STOP_CONFIRMED=yes), the current row already has
    CANCEL_REQUESTED, its runtime-owned cancel_requested_at is finite, stop_deadline is
    finite, and result.terminal_at > stop_deadline; a stop at or before the fixed
    deadline is on-time and remains eligible for the normal confirmed-cancellation
    recovery path

stop_confirmation_compare_and_set_matches_current_row(result):
    atomically match the current task/attempt/invocation/token/row version and
    CANCEL_REQUESTED overlay, require runtime-owned STOP_CONFIRMED and
    cancel_confirmed_at, record the confirmed stop and terminal metadata, capture any
    permitted interruption artifact, and retain the owner/lock. Match every canonical
    row field explicitly, including reserved_runtime_target,
    runtime_terminal_event_id, budget_consumed_at_terminal and the interruption/
    checkpoint/proof identities. The full-row write sets state=BLOCKED,
    terminal_reason=STOP_CONFIRMED_ONLY, report_disposition=none,
    budget_consumed_at_terminal=terminal_at - snapshot_budget_started_at,
    runtime_terminal_event_id=runtime_terminal_event_id(result), and, when
    the event carries a validated checkpoint, preserves its event/checkpoint/proof
    identities; otherwise it writes their documented NONE sentinels. It preserves
    overlay=CANCEL_REQUESTED and returns COMMITTED with the authoritative row and
    disposition=STOP_CONFIRMED_ONLY, not a ledger state. On a CAS loss it returns a
    typed lost_operation handle containing the complete expected/new FullTaskRow,
    event identity, authoritative winner, and one non-reconciling retry entry point;
    it never retries internally. It can never create an accepted report, clear
    cancellation, authorize replacement/takeover, or release the lock before artifact
    capture.

materialize_spawn_failure_terminal(result):
    require runtime_terminal_event_validates(result), result.STATUS is spawn_failed,
        result.CHILD_STARTED is no, and terminal_timing_is_valid(result)
    require current_task_row is the exact CLAIMED original or replacement invocation,
        report_id=NONE, binding_failure_provenance=NONE,
        overlay in {NONE, CANCEL_REQUESTED}, terminal_at=UNSET,
        runtime_terminal_event_id=NONE, and every checkpoint/proof/result/disposition
        field is its documented pre-terminal sentinel
    if overlay is CANCEL_REQUESTED:
        require finite current_task_row.cancel_requested_at and
            current_task_row.cancel_requested_at <= monotonic_now
    atomically invoke full_task_row_compare_and_set with the complete expected row,
        including reserved_runtime_target and all scope, budget, replacement, owner,
        lock, timing, and provenance fields, and write state=FAILED,
        terminal_reason=SPAWN_FAILED, terminal_at=result.TERMINAL_AT,
        budget_consumed_at_terminal=result.TERMINAL_AT - snapshot_budget_started_at,
        runtime_terminal_event_id=result.RUNTIME_TERMINAL_EVENT_ID,
        binding_failure_provenance=NONE,
        report_disposition=none, result_content_identity=NONE, and the explicit
        documented terminal sentinels; retain the owner/lock until the parent records
        the recovery decision or an explicit reviewer block
    if the CAS loses:
        return CAS_LOST with a typed lost_operation handle containing the exact
        expected/new FullTaskRow projection, runtime event identity, and the
        non-reconciling one-shot materializer; do not quarantine or recover here
    return COMMITTED with the authoritative new row and disposition=RECOVERY_REQUIRED

materialize_spawn_failure_with_reserved_target(result):
    require runtime_terminal_event_validates(result), result.STATUS is spawn_failed,
        result.CHILD_STARTED is no, result.RUNTIME_TARGET is the exact persisted
        reserved_runtime_target, and terminal_timing_is_valid(result); require an
        independent runtime proof that the reserved target was never started
    require current_task_row is the exact same invocation with agent_id/channel/
        association=UNASSIGNED, overlay in {NONE, CANCEL_REQUESTED},
        binding_failure_provenance=SPAWN_UNCONFIRMED, terminal_at=UNSET,
        runtime_terminal_event_id=NONE, report_id=NONE, and the matching finite
        binding-failure deadline when the overlay is CANCEL_REQUESTED
    atomically full-row CAS the exact expected row to state=FAILED for the original
        or BLOCKED for replacement, terminal_reason=SPAWN_FAILED or
        REPLACEMENT_SPAWN_FAILED, terminal_at=result.TERMINAL_AT,
        budget_consumed_at_terminal=result.TERMINAL_AT - snapshot_budget_started_at,
        runtime_terminal_event_id=result.RUNTIME_TERMINAL_EVENT_ID,
        report_disposition=none, result/checkpoint/integration sentinels, while
        retaining the exact reserved target, SPAWN_UNCONFIRMED provenance,
        CANCEL_REQUESTED overlay (if present), owner, and lock; replacement closes
        the one replacement slot
    if the CAS loses, return the typed lost_operation to
        reconcile_runtime_event_cas_loss exactly once
    return COMMITTED with the authoritative row and disposition=RECOVERY_REQUIRED for
        the original or REPLACEMENT_SLOT_CLOSED for replacement; this is the only
        materializer for an exact-target/no-child event after binding failure

materialize_replacement_spawn_failure(result):
    require runtime_terminal_event_validates(result), result.STATUS is spawn_failed,
        result.CHILD_STARTED is no, and terminal_timing_is_valid(result)
    require current_task_row is the exact live replacement row with
        replacement_index=1, replacement_count=1, replacement_of nonempty,
        binding_failure_provenance=NONE,
        state=CLAIMED, overlay in {NONE, CANCEL_REQUESTED}, report_id=NONE,
        terminal_at=UNSET,
        runtime_terminal_event_id=NONE, and the still-UNSET replacement gate digest;
        require the event's target is the exact persisted reservation (or NONE only
        when the row has no reservation) and its invocation/token/version match
    if overlay is CANCEL_REQUESTED:
        require finite current_task_row.cancel_requested_at and
            current_task_row.cancel_requested_at <= monotonic_now
    atomically invoke full_task_row_compare_and_set with every canonical field on
        the expected side, including the fixed snapshot/deadline/provenance,
        replacement target, owner/lock, and all pre-terminal sentinels; write
        state=BLOCKED, terminal_reason=REPLACEMENT_SPAWN_FAILED,
        terminal_at=result.TERMINAL_AT,
        budget_consumed_at_terminal=result.TERMINAL_AT - snapshot_budget_started_at,
        runtime_terminal_event_id=result.RUNTIME_TERMINAL_EVENT_ID,
        binding_failure_provenance=NONE,
        report_id=NONE, report_disposition=none, result_content_identity=NONE,
        and the documented terminal/checkpoint/proof sentinels; preserve the fixed
        replacement provenance and close the one replacement slot
    if the CAS loses:
        return CAS_LOST with the same typed lost_operation handle and the transaction's
        authoritative winner; do not consume it through a second wrapper here
    return COMMITTED with the authoritative new row and
        disposition=REPLACEMENT_SLOT_CLOSED

record_reviewer_replacement_failure_at_parent(current_task_row, disposition):
    require current_task_row.role is reviewer,
        current_task_row.replacement_index=1,
        current_task_row.state is BLOCKED,
        current_task_row.terminal_reason is REPLACEMENT_SPAWN_FAILED,
        disposition is REPLACEMENT_SLOT_CLOSED, and the row has the exact snapshot,
        content, run/task/invocation, owner, lock, and replacement provenance
    if this task belongs to a review_set:
        require the parent/session metadata contains the same REVIEW_SET_ID,
            impact_scope_digest, mapping_digest, lane_id, lane_scope_digest, and
            predecessor stop-event identity used by the replacement claim
        atomically compare-and-set the parent/session aggregate from its current
            non-accepted state to REVIEW_BLOCKED with reason
            REPLACEMENT_SPAWN_FAILED, preserving the lane row, closed set-level slot,
            owner, lock, snapshot identity, and every other lane result; a repeated
            call with the same row/event is idempotent and consumes the authoritative
            aggregate row
    otherwise:
        atomically record the same snapshot-level REVIEW_BLOCKED disposition in the
            parent/session review record, keyed by the exact task/attempt/event; the
            row-level BLOCKED/REPLACEMENT_SPAWN_FAILED reason remains authoritative
    return the authoritative parent/session record; this is the only mapping from
        REPLACEMENT_SLOT_CLOSED to the reviewer-level REVIEW_BLOCKED outcome and it
        can never produce aggregate CLEAN or reopen a replacement slot

materialize_confirmed_recovery_terminal(result, validated_runtime_stop_event=NONE):
    require result is a parent-validated runtime-confirmed non-resumable stop, a
        parent-verified non-resumable reviewer PARTIAL with a runtime stop proof, or a
        validated RUNTIME_TERMINAL_EVENT with CHILD_STARTED=yes and an independent
        runtime stop proof
    if result is a parent-verified reviewer PARTIAL:
        require validated_runtime_stop_event is the separate exact runtime stop event
            returned by validated_runtime_stop_event_for_partial(result)
        terminal_event_for_materialization = validated_runtime_stop_event
    else:
        terminal_event_for_materialization = result
    if result is an unbound RUNTIME_INTERRUPTION_EVENT or an unbound
       RUNTIME_TERMINAL_EVENT with CHILD_STARTED=yes:
        require current_task_row.agent_id is UNASSIGNED,
            current_task_row.overlay in {NONE, CANCEL_REQUESTED},
            reserved_unbound_target_matches(result)
        if current_task_row.overlay is CANCEL_REQUESTED:
            require current_task_row.binding_failure_provenance is SPAWN_UNCONFIRMED
            require finite current_task_row.binding_failed_at and
                finite current_task_row.binding_failure_recovery_deadline_at
            require result.terminal_at <=
                current_task_row.binding_failure_recovery_deadline_at
        # This explicit branch is allowed to win before the parent can claim
        # CANCEL_REQUESTED or record binding failure. When cancellation already won,
        # the combined binding-failure CAS has supplied the finite timing fields; the
        # event is materialized against that exact row without a duplicate stop.
    target_state = FAILED for a confirmed failure/error result; otherwise CANCELLED
    target_reason = RUNTIME_FAILURE_CONFIRMED for failure/error, PARTIAL_NON_RESUMABLE_CONFIRMED
        for a parent-verified non-resumable reviewer PARTIAL, or RUNTIME_STOP_CONFIRMED
        for a runtime interruption/cancellation
    atomically match the complete current row/version, run/task/parent-task/role,
        attempt/invocation/token, agent channel/transport association,
        reserved_runtime_target, snapshot/content
        identity, state in {CLAIMED, RUNNING, NEEDS_INPUT, PARTIAL}, overlay,
        report/disposition,
        owner, lock, all timing/budget
        fields, binding_failure_provenance, runtime_terminal_event_id, and
        interruption/checkpoint/artifact-proof
        identities; write only the
        runtime-confirmed terminal metadata, target_state, target_reason,
        budget_consumed_at_terminal=terminal_event_for_materialization.terminal_at -
            snapshot_budget_started_at, and
        runtime_terminal_event_id=runtime_terminal_event_id(
            terminal_event_for_materialization
        ),
        terminal_at=terminal_event_for_materialization.terminal_at,
        report_disposition=none while retaining the overlay,
        binding_failure_provenance, owner, lock, and
        attempt-scoped artifact/provenance; explicitly preserve the current
        report_id and attention_required when the source row is NEEDS_INPUT or PARTIAL
        (or explicitly write both documented NONE sentinels if this named terminal
        transition closes that attention record in the same CAS)
    if the CAS loses:
        return CAS_LOST with a typed lost_operation handle containing the complete
        expected/new FullTaskRow projection, runtime event identity, and its
        non-reconciling one-shot terminal materializer
    return COMMITTED with the authoritative new row and disposition=RECOVERY_REQUIRED

reconcile_runtime_event_cas_loss(result, lost_operation):
    # This is the one common replay boundary for runtime-owned event races. The
    # materializers below expose a non-reconciling `*_once` primitive for this call;
    # that primitive may perform one CAS only and returns its transaction winner.
    quarantine_cas_lost(result)
    authoritative_row = lost_operation.authoritative_row from the CAS transaction
        (otherwise read the authoritative row exactly once; never perform a status poll)
    require exact run/task/parent-task/role/attempt/invocation/token, snapshot/content
        identity, reserved runtime target, agent/channel/transport association,
        owner/lock, and runtime event identity
    if authoritative_row already contains the same runtime event identity and the
       exact terminal/checkpoint/timing/provenance projection:
        consume that idempotent winner and return its authoritative disposition
        (REPLACEMENT_SLOT_CLOSED for a replacement spawn-failure row,
         STOP_CONFIRMED_ONLY for a late-stop row, PARTIAL for the exact partial row,
         otherwise RECOVERY_REQUIRED)
    if authoritative_row is terminal, accepted, quarantined, or belongs to a
       different invocation:
        consume the authoritative winner and return AUTHORITATIVE_WINNER
    if authoritative_row is not the exact live same-invocation row with the expected
       report/attention slot, state, overlay, binding_failure_provenance, target,
       and timing sentinels:
        return QUARANTINED
    current_task_row = authoritative_row
    require the same closed runtime validator and terminal_timing_is_valid(result)
        pass against this authoritative row; a changed identity, deadline, target,
        report slot, or provenance quarantines the candidate
    retry = lost_operation.non_reconciling_once(result, current_task_row)
    if retry.kind is COMMITTED:
        consume retry.authoritative_row and return {
            kind=the disposition for the named operation
                (REPLACEMENT_SLOT_CLOSED, STOP_CONFIRMED_ONLY, PARTIAL,
                 RESUME_COMMITTED, or RECOVERY_REQUIRED),
            authoritative_row=retry.authoritative_row
        }
    if retry.kind is CAS_LOST:
        consume retry.authoritative_row from that second transaction and return
            {kind=AUTHORITATIVE_WINNER, authoritative_row=retry.authoritative_row}
    return QUARANTINED

consume_terminal_decision(decision):
    # Mutating terminal materializers and their one-shot replay handlers return the
    # transaction's authoritative row. The caller must consume it exactly once before
    # branching; a bare disposition is allowed only for validation-only paths.
    if decision is a structured terminal decision with authoritative_row:
        current_task_row = decision.authoritative_row
        if decision.kind is REPLACEMENT_SLOT_CLOSED:
            record_reviewer_replacement_failure_at_parent(
                current_task_row, REPLACEMENT_SLOT_CLOSED
            )
        return decision.kind
    if decision is REPLACEMENT_SLOT_CLOSED:
        record_reviewer_replacement_failure_at_parent(
            current_task_row, REPLACEMENT_SLOT_CLOSED
        )
    return decision

classify_terminal(result):
    # RECOVERY_REQUIRED below is a classifier disposition only; it is never a ledger state.
    # Every mutating materializer/replay path below writes its authoritative row back to
    # current_task_row before returning. This prevents an outer loop from issuing a
    # second CAS or recovery decision from the pre-materialization row.
    result = canonicalize_terminal(result)
    if result is UNUSABLE:
        quarantine_redacted(result)
        return QUARANTINED
    if result.kind in {LATE_BIND_COMMITTED, LATE_BIND_ALREADY_COMMITTED}:
        if verify_late_bound_report(result) is LATE_BIND_VERIFIED:
            return LATE_BIND_VERIFIED
        quarantine_redacted(result)
        return QUARANTINED
    if result.kind is RUNTIME_TERMINAL_EVENT:
        if not runtime_terminal_event_validates(result) or
           not terminal_timing_is_valid(result):
            quarantine_redacted(result)
            return QUARANTINED
        if current_task_row.runtime_terminal_event_id ==
           result.RUNTIME_TERMINAL_EVENT_ID:
            if result.STATUS is spawn_failed and
               current_task_row.replacement_index == 1 and
               current_task_row.state is BLOCKED and
               current_task_row.terminal_reason is REPLACEMENT_SPAWN_FAILED:
                return REPLACEMENT_SLOT_CLOSED
            if result.STATUS is spawn_failed:
                return RECOVERY_REQUIRED
            if is_late_runtime_stop_confirmation(result):
                return STOP_CONFIRMED_ONLY
            return RECOVERY_REQUIRED
        if result.CHILD_STARTED is no and result.STATUS is spawn_failed:
            if current_task_row.reserved_runtime_target is an exact runtime target and
               current_task_row.binding_failure_provenance is SPAWN_UNCONFIRMED:
                spawn_failure_materialization =
                    materialize_spawn_failure_with_reserved_target(result)
            elif current_task_row.replacement_index == 1:
                spawn_failure_materialization =
                    materialize_replacement_spawn_failure(result)
            else:
                spawn_failure_materialization =
                    materialize_spawn_failure_terminal(result)
            if spawn_failure_materialization.kind is CAS_LOST:
                replay_outcome = reconcile_runtime_event_cas_loss(
                    result, spawn_failure_materialization
                )
                replay_decision = consume_terminal_decision(replay_outcome)
                if replay_decision in {REPLACEMENT_SLOT_CLOSED, RECOVERY_REQUIRED}:
                    return replay_decision
                return QUARANTINED
            require spawn_failure_materialization.kind is COMMITTED
            current_task_row = spawn_failure_materialization.authoritative_row
            if spawn_failure_materialization.disposition is REPLACEMENT_SLOT_CLOSED:
                record_reviewer_replacement_failure_at_parent(
                    current_task_row, REPLACEMENT_SLOT_CLOSED
                )
                return REPLACEMENT_SLOT_CLOSED
            return RECOVERY_REQUIRED
        if is_late_runtime_stop_confirmation(result):
            stop_confirmation_outcome =
                stop_confirmation_compare_and_set_matches_current_row(result)
            if stop_confirmation_outcome.kind is COMMITTED:
                current_task_row = stop_confirmation_outcome.authoritative_row
                return STOP_CONFIRMED_ONLY
            replay_outcome = reconcile_runtime_event_cas_loss(
                result, stop_confirmation_outcome
            )
            replay_decision = consume_terminal_decision(replay_outcome)
            if replay_decision is STOP_CONFIRMED_ONLY:
                return replay_decision
            return QUARANTINED
        recovery_materialization = materialize_confirmed_recovery_terminal(result)
        if recovery_materialization.kind is CAS_LOST:
            replay_outcome = reconcile_runtime_event_cas_loss(
                result, recovery_materialization
            )
            replay_decision = consume_terminal_decision(replay_outcome)
            if replay_decision is RECOVERY_REQUIRED:
                return replay_decision
            return QUARANTINED
        require recovery_materialization.kind is COMMITTED
        current_task_row = recovery_materialization.authoritative_row
        return RECOVERY_REQUIRED
    if result.kind is RUNTIME_INTERRUPTION_EVENT:
        if not runtime_interruption_event_validates(result):
            result = replay_runtime_interruption_after_row_race(result)
        if result is INVALID or
           not runtime_interruption_event_validates(result) or
           not terminal_timing_is_valid(result):
            quarantine_redacted(result)
            return QUARANTINED
        if current_task_row.agent_id is UNASSIGNED and
           current_task_row.overlay is NONE and
           current_task_row.binding_failure_provenance is NONE and
           reserved_unbound_target_matches(result):
            return reconcile_unbound_runtime_interruption(result)
        if is_late_runtime_stop_confirmation(result):
            stop_confirmation_outcome =
                stop_confirmation_compare_and_set_matches_current_row(result)
            if stop_confirmation_outcome.kind is COMMITTED:
                current_task_row = stop_confirmation_outcome.authoritative_row
                return STOP_CONFIRMED_ONLY
            replay_outcome = reconcile_runtime_event_cas_loss(
                result, stop_confirmation_outcome
            )
            replay_decision = consume_terminal_decision(replay_outcome)
            if replay_decision is STOP_CONFIRMED_ONLY:
                return replay_decision
            return QUARANTINED
        if result.RESUMABLE is yes and valid_checkpoint(result):
            partial_outcome = partial_compare_and_set_matches_current_row(result)
            if partial_outcome.kind is COMMITTED:
                current_task_row = partial_outcome.authoritative_row
                return PARTIAL
            replay_outcome = reconcile_runtime_event_cas_loss(
                result, partial_outcome
            )
            replay_decision = consume_terminal_decision(replay_outcome)
            if replay_decision is PARTIAL:
                return replay_decision
            return QUARANTINED
        if result.RESUMABLE is no:
            recovery_materialization = materialize_confirmed_recovery_terminal(result)
            if recovery_materialization.kind is CAS_LOST:
                replay_outcome = reconcile_runtime_event_cas_loss(
                    result, recovery_materialization
                )
                replay_decision = consume_terminal_decision(replay_outcome)
                if replay_decision is RECOVERY_REQUIRED:
                    return replay_decision
                return QUARANTINED
            require recovery_materialization.kind is COMMITTED
            current_task_row = recovery_materialization.authoritative_row
            return RECOVERY_REQUIRED
        quarantine_redacted(result)
        return QUARANTINED
    # All non-interruption terminal paths, including cancellation races, must use
    # the complete scanner before classification; canonicalization alone is not a scan.
    validated_result = validate_report_event(result)
    if validated_result is QUARANTINED:
        quarantine_redacted(result)
        return QUARANTINED
    result = validated_result
    if is_late_runtime_stop_confirmation(result):
        stop_confirmation_outcome =
            stop_confirmation_compare_and_set_matches_current_row(result)
        if stop_confirmation_outcome.kind is COMMITTED:
            current_task_row = stop_confirmation_outcome.authoritative_row
            return STOP_CONFIRMED_ONLY
        replay_outcome = reconcile_runtime_event_cas_loss(
            result, stop_confirmation_outcome
        )
        replay_decision = consume_terminal_decision(replay_outcome)
        if replay_decision is STOP_CONFIRMED_ONLY:
            return replay_decision
        return QUARANTINED
    if current_task_row.overlay is CANCEL_REQUESTED:
        # No child-owned report status can supersede an already-recorded cancellation
        # request, including FAILED/CANCELLED/error/shutdown. Only the dedicated,
        # runtime-owned interruption branch above may enter stop/recovery handling.
        quarantine_redacted(result)
        return QUARANTINED
    if result is terminal and result is not a provisional candidate and
       not terminal_timing_is_valid(result):
        quarantine_redacted(result)
        return QUARANTINED
    if not runtime_provenance_matches(result) or not snapshot_identity_matches(result):
        quarantine_redacted(result)
        return QUARANTINED
    if stored_role is reviewer:
        if not runtime_attested_artifact_access_matches(result) or
           not prebinding_budget_gate_is_open(result):
            quarantine_redacted(result)
            return QUARANTINED
        runtime_review_coverage_proof = runtime_review_coverage_proof_for(result)
    else:
        runtime_review_coverage_proof = NONE
    if result.status in role_complete_status[stored_role]:
        if stored_role is reviewer and
           not review_payload_semantics_are_closed(result, runtime_review_coverage_proof):
            quarantine_redacted(result)
            return QUARANTINED
        return ACCEPTABLE
    if result.status is NEEDS_USER_DECISION:
        return USER_DECISION
    if result.status is BLOCKED:
        return BLOCKED
    if result.status is NEEDS_INPUT:
        return NEEDS_INPUT
    if result.status is PARTIAL:
        return PARTIAL
    if result.status is REVIEW_BLOCKED:
        preserve(result)
        return BLOCKED
    if result.status in {FAILED, CANCELLED, errored, shutdown}:
        if not runtime_terminal_or_cancellation_is_confirmed(result):
            quarantine_redacted(result)
            return QUARANTINED
        recovery_materialization = materialize_confirmed_recovery_terminal(result)
        if recovery_materialization.kind is CAS_LOST:
            replay_outcome = reconcile_runtime_event_cas_loss(
                result, recovery_materialization
            )
            replay_decision = consume_terminal_decision(replay_outcome)
            if replay_decision is RECOVERY_REQUIRED:
                return replay_decision
            return QUARANTINED
        require recovery_materialization.kind is COMMITTED
        current_task_row = recovery_materialization.authoritative_row
        return RECOVERY_REQUIRED
    quarantine_redacted(result)
    return QUARANTINED

commit_terminal(result):
    replacement_budget_gate = validated_replacement_budget_gate(result)
    if replacement_budget_gate is INVALID:
        quarantine_redacted(result)
        return {kind=CAS_LOST, authoritative_row=current_task_row,
                lost_operation=the rejected terminal projection}
    expected_prebinding_timing_digest = canonical_optional_digest(
        result, "prebinding_timing_digest", absent_sentinel=UNSET
    )
    if expected_prebinding_timing_digest is INVALID:
        quarantine_redacted(result)
        return {kind=CAS_LOST, authoritative_row=current_task_row,
                lost_operation=the rejected terminal projection}
    runtime_artifact_access_proof = runtime_attested_artifact_access_for(result)
        (or NONE for a non-reviewer)
    terminal_budget_consumed = result.terminal_at - snapshot_budget_started_at
    require finite_nonnegative(terminal_budget_consumed)
    terminal_cas = terminal_compare_and_set_matches_current_row(
           result,
           expected_overlay=NONE,
           expected_reserved_runtime_target=current_task_row.reserved_runtime_target,
           expected_runtime_terminal_event_id=current_task_row.runtime_terminal_event_id,
           expected_budget_consumed_at_terminal=current_task_row.budget_consumed_at_terminal,
           expected_interruption_event_id=current_task_row.interruption_event_id,
           expected_checkpoint_id=current_task_row.checkpoint_id,
           expected_checkpoint_content_identity=current_task_row.checkpoint_content_identity,
           expected_artifact_access_proof_id=current_task_row.artifact_access_proof_id,
           expected_replacement_budget_gate=replacement_budget_gate,
           expected_prebinding_timing_digest=expected_prebinding_timing_digest,
           expected_replacement_gate_digest=NONE for original or
               replacement_budget_gate.expected_row_gate_digest,
           new_replacement_gate_digest=NONE for original or replacement_budget_gate.digest,
           new_budget_consumed_at_terminal=terminal_budget_consumed,
           new_reserved_runtime_target=current_task_row.reserved_runtime_target,
           new_runtime_terminal_event_id=current_task_row.runtime_terminal_event_id,
           new_interruption_event_id=current_task_row.interruption_event_id,
           new_checkpoint_id=current_task_row.checkpoint_id,
           new_checkpoint_content_identity=current_task_row.checkpoint_content_identity,
           new_artifact_access_proof_id=runtime_attested_artifact_access_proof.identity
               for a reviewer, otherwise NONE
       )
    if terminal_cas.kind is COMMITTED:
        consume(result)
        current_task_row = terminal_cas.authoritative_row
        return {kind=COMMITTED, authoritative_row=current_task_row}
    quarantine_cas_lost(result)
    require terminal_cas.kind is CAS_LOST
    authoritative_row = terminal_cas.authoritative_row
    preserve authoritative_row, its owner, lock, and dependency result exactly as observed
    return {kind=CAS_LOST, authoritative_row=authoritative_row,
            lost_operation=the complete terminal commit projection}

consume_terminal_commit_cas_loss(terminal_commit):
    require terminal_commit.kind is CAS_LOST and
        terminal_commit.authoritative_row is a complete canonical task row
    # A terminal CAS loss is not permission to keep using the pre-CAS row. Consume
    # the transaction winner exactly once, including its terminal/cancellation/
    # quarantine disposition, and make it the only row visible to the caller.
    current_task_row = terminal_commit.authoritative_row
    retain current_task_row, its owner, lock, dependency result, and terminal/report
        identities exactly as returned; do not issue a second terminal CAS, block CAS,
        recovery, replacement, stop request, or unlock from the losing projection
    return AUTHORITATIVE_WINNER

quarantine_redacted(result):
    append only metadata needed to identify the attempt and failure class through a
    compare-and-set; strip child prose, commands, prompts, source contents, secrets,
    embeddings, model output, and unvalidated identity/timing/artifact fields

validate_report_event(result):
    scan the complete result without persisting child-controlled fields; on any failure
        return QUARANTINED with only a redacted diagnostic (task/attempt/invocation,
        failure class, and no payload text)
    if result.kind is RUNTIME_INTERRUPTION_EVENT:
        if not runtime_interruption_event_validates(result):
            result = replay_runtime_interruption_after_row_race(result)
        if result is INVALID or
           not runtime_interruption_event_validates(result) or
           not terminal_timing_is_valid(result):
            quarantine_redacted(result)
            return QUARANTINED
        return result
    if result.kind is RUNTIME_TERMINAL_EVENT:
        if not runtime_terminal_event_validates(result) or
           not terminal_timing_is_valid(result):
            quarantine_redacted(result)
            return QUARANTINED
        return result
    scan the complete result and verify its runtime invocation, stored role, baseline, snapshot, and content identity
    if result is terminal and result is not a provisional candidate and
       not terminal_timing_is_valid(result):
        quarantine_redacted(result)
        return QUARANTINED
    if result is not a provisional candidate and its current invocation/version does not match the ledger:
        quarantine_cas_lost(result)
        return QUARANTINED
    if result is not a provisional candidate and
       (result.ROLE is not stored_role or not closed_role_payload_validates(result)):
        quarantine_redacted(result)
        return QUARANTINED
    if result is not a provisional candidate and its provenance or snapshot identity is invalid:
        quarantine_redacted(result)
        return QUARANTINED
    if result is not a provisional candidate and stored_role is reviewer:
        if not runtime_attested_artifact_access_matches(result) or
           not prebinding_budget_gate_is_open(result):
            quarantine_redacted(result)
            return QUARANTINED
        runtime_review_coverage_proof = runtime_review_coverage_proof_for(result)
        if not review_payload_semantics_are_closed(result, runtime_review_coverage_proof):
            quarantine_redacted(result)
            return QUARANTINED
    return result

Every preserve, state/event record, input send, and transition below must use a compare-and-set against the
current ledger row after validation. A losing CAS calls quarantine_cas_lost(), leaves the winning row/owner/lock/
dependency result unchanged, and stops without recovery or replacement.
Each named `*_compare_and_set` operation below is an adapter over one
`full_task_row_compare_and_set(expected_row, new_row)` primitive. It must first
construct `expected_row` and `new_row` with the exact `CANONICAL_TASK_ROW_FIELDS`
keyset from the coordination protocol; `expected_current_row=...`,
`retain_all_other_fields=yes`, or a keyword subset is only notation for that complete
projection and is invalid if it omits a field. The adapter must reject an unexpected
field, an omitted field, a stale version, an implicit reset, or a new value outside the
named transition. This rule applies equally to cancellation, binding-failure timing,
late-bind/post-spawn reconciliation, terminal materialization, replacement claim and
binding, partial materialization, same-task resume, and report commit. A successful
wrapper returns the authoritative new row; `CAS_LOST` returns the authoritative winner
and is consumed exactly once by its named reconciliation path.
For a reviewer replacement, both `commit_terminal` and `late_bind_report_compare_and_set` must include the exact
runtime/parent-owned `replacement_gate_digest` and its immutable gate values as expected fields; a gate validated
earlier but changed before the CAS is a CAS loss, not a valid terminal result.

decision_window_is_open(result):
    now = clock.monotonic()
    deadline = snapshot_deadline when stored_role is reviewer and replacement_index > 0
        else attempt_deadline
    require now is a finite sample from the validated ledger clock and
        finite_positive(deadline - now)
    if result has a runtime received/event timestamp:
        require spawn_requested_at <= that timestamp <= now and that timestamp <= deadline
    return {decision_deadline=deadline, observed_at=now, remaining=deadline - now}
    or EXPIRED when any check fails

record_user_decision(result):
    decision_window = decision_window_is_open(result)
    if decision_window is EXPIRED:
        stop_outcome = stop_at_deadline_or_recovery_boundary()
        if stop_outcome.kind is AUTHORITATIVE_WINNER:
            return DECISION_EXPIRED without recording state on the stale row
        record REVIEW_BLOCKED for a reviewer or the role-specific blocked state,
        retain the owner and lock, and return DECISION_EXPIRED; do not create a ticket
        or send input
    if attention_compare_and_set_matches_current_row(
           complete FullTaskRow expected/new projection,
           atomically_recheck_current_monotonic_now_before_commit=yes,
           reject_if_current_monotonic_now >= expected_decision_deadline=yes,
           expected_row_version=current_task_row.version,
           expected_task_id=task_id,
           expected_attempt=attempt,
           expected_invocation_id=invocation_id,
           expected_report_id=current_task_row.report_id,
           expected_agent_id=current_task_row.agent_id,
           expected_agent_channel=current_task_row.agent_channel,
           expected_transport_invocation_association=
               current_task_row.transport_invocation_association,
           expected_reserved_runtime_target=current_task_row.reserved_runtime_target,
           expected_runtime_terminal_event_id=current_task_row.runtime_terminal_event_id,
           expected_state=RUNNING,
           expected_overlay=NONE,
           incoming_report_id=result.report_id,
           expected_snapshot_deadline_at=snapshot_deadline,
           expected_attempt_deadline_at=attempt_deadline,
           expected_decision_deadline=decision_window.decision_deadline,
           expected_remaining_budget_at_decision=decision_window.remaining,
           target_state=NEEDS_INPUT,
           new_budget_remaining=decision_window.remaining,
           preserve_every_other_canonical_field_explicitly=yes,
           retain_owner_and_lock=yes
       ):
        attention_id = the parent/runtime-created bounded attention event id
        decision_ticket = {
            task_id, attempt, invocation_id, report_id=result.report_id,
            attention_id, row_version=the resulting row version,
            owner=the retained owner, lock=the retained lock,
            snapshot_deadline, attempt_deadline,
            decision_deadline=decision_window.decision_deadline,
            remaining_budget_at_decision=decision_window.remaining,
            continuation_mode=same_invocation_if_accepting_or_new_task_after_stop
        }
        record the bounded attention_required decision and return decision_ticket
    quarantine_cas_lost(result)
    reconcile_input_race(result, CAS_LOST)
    preserve the winning current row, owner, lock, and dependency result exactly as observed
    return CAS_LOST

continue_after_user_decision(ticket, answer, mode):
    validate the parent/runtime-created ticket, the bounded answer, and the selected
    continuation mode; never accept a ticket or answer from child prose
    now = clock.monotonic()
    if not finite_monotonic(now) or
       now >= ticket.decision_deadline or
       ticket.decision_deadline - now <= 0:
        stop_at_deadline_or_recovery_boundary() for the exact invocation, retain the owner and lock, and
        create or atomically hand off to a new bounded task only after a validated
        runtime terminal event and its full-row materialization; for a reviewer route
        only through recover_or_block so replacement index/count, REPLACEMENT_OF,
        predecessor runtime terminal identity, fixed snapshot deadline, gate, and
        remaining-budget minimum are rechecked by the replacement CAS;
        inherit the same snapshot/content identity and only the remaining fixed
        snapshot budget; if that snapshot deadline has passed, require a new snapshot;
        never grant fresh budget because the task is new
        never send an expired answer to the old invocation
        return EXPIRED_REQUIRES_RECOVERY
    if mode is same_invocation and runtime confirms that the exact invocation still
       accepts input and no cancellation overlay is present:
        if user_decision_continue_compare_and_set(
               complete FullTaskRow expected/new projection,
               atomically_recheck_current_monotonic_now_before_commit=yes,
               reject_if_current_monotonic_now >= ticket.decision_deadline=yes,
               expected_row_version=ticket.row_version,
               expected_task_id=ticket.task_id,
               expected_attempt=ticket.attempt,
               expected_invocation_id=ticket.invocation_id,
               expected_report_id=ticket.report_id,
               expected_agent_id=current_task_row.agent_id,
               expected_agent_channel=current_task_row.agent_channel,
               expected_transport_invocation_association=
                   current_task_row.transport_invocation_association,
               expected_reserved_runtime_target=current_task_row.reserved_runtime_target,
               expected_runtime_terminal_event_id=current_task_row.runtime_terminal_event_id,
               expected_attention_id=ticket.attention_id,
               expected_state=NEEDS_INPUT,
               expected_owner=ticket.owner,
               expected_lock=ticket.lock,
               expected_overlay=NONE,
               expected_snapshot_deadline_at=ticket.snapshot_deadline_at,
               expected_attempt_deadline_at=ticket.attempt_deadline_at,
               expected_decision_deadline=ticket.decision_deadline,
               expected_remaining_budget_at_continuation=ticket.decision_deadline - now,
               new_budget_remaining_at_continuation=
                   ticket.decision_deadline - current_monotonic_now,
               preserve_every_other_canonical_field_explicitly=yes,
               answer=answer,
               record_decision_metadata=yes,
               send_to_same_invocation=yes,
               target_state=RUNNING
           ):
            return CONTINUED
        quarantine_cas_lost(ticket)
        reconcile_input_race(ticket, CAS_LOST)
        preserve the winning row and stop; do not send a second answer or replace
    if mode changes the scope/plan, or the exact invocation is no longer accepting:
        wait for and validate a runtime terminal event for the exact
        ticket.task_id/attempt/invocation_id, then materialize its full canonical row
        before creating or handing off anything; an observation, close acknowledgement,
        child shutdown prose, or status mismatch is not confirmation
        if the ticket belongs to a reviewer:
            route only through recover_or_block using that materialized predecessor;
            the recovery path alone may claim the one replacement slot and must carry
            replacement_of={task/attempt/invocation plus nonempty runtime event id},
            replacement_index/count, the same immutable snapshot, fixed deadline,
            replacement gate, and remaining-budget minimum in one full-row CAS; do not
            create a new reviewer task directly from NEEDS_INPUT
        else:
            create a new bounded recovery/replan TaskSpec only through the role-specific
            recovery handoff CAS, recording the answer as planning input and retaining
            the old owner/lock until the stop is materialized; inherit the same
            snapshot/content identity and only the remaining fixed budget, or create a
            new snapshot if the deadline has passed
        never grant fresh budget merely because the task is new, send the answer to the
        old invocation, or create an unlocked handoff
    otherwise:
        quarantine(ticket); retain the owner and lock; stop before acceptance

send_bounded_input(result, answer):
    input_window = decision_window_is_open(result)
    if input_window is EXPIRED:
        stop_outcome = stop_at_deadline_or_recovery_boundary()
        if stop_outcome.kind is AUTHORITATIVE_WINNER:
            return INPUT_EXPIRED without recording state on the stale row
        record REVIEW_BLOCKED for a reviewer or the role-specific blocked state,
        retain the owner and lock, and return INPUT_EXPIRED; never send an answer after
        the applicable deadline
    first use the same full-row attention CAS as record_user_decision, including a
    complete FullTaskRow expected/new projection that explicitly expects and preserves
    agent/channel/association, reserved_runtime_target, and runtime_terminal_event_id,
    final monotonic-clock recheck that rejects an expired applicable deadline, the
    current applicable deadline and positive monotonic remaining budget, targeting
    NEEDS_INPUT while retaining the exact owner/lock and current invocation
    if that CAS loses:
        quarantine_cas_lost(result)
        reconcile_input_race(result, CAS_LOST)
        return CAS_LOST
    if send_input_compare_and_set(
           complete FullTaskRow expected/new projection,
           atomically_recheck_current_monotonic_now_before_commit=yes,
           reject_if_current_monotonic_now >= input_window.decision_deadline=yes,
           expected_row_version=current_task_row.version,
           expected_task_id=task_id,
           expected_attempt=attempt,
           expected_invocation_id=invocation_id,
           expected_report_id=result.report_id,
           expected_agent_id=current_task_row.agent_id,
           expected_agent_channel=current_task_row.agent_channel,
           expected_transport_invocation_association=
               current_task_row.transport_invocation_association,
           expected_reserved_runtime_target=current_task_row.reserved_runtime_target,
           expected_runtime_terminal_event_id=current_task_row.runtime_terminal_event_id,
           expected_state=NEEDS_INPUT,
           expected_overlay=NONE,
           answer=answer,
           expected_deadline=input_window.decision_deadline,
           expected_remaining_budget=input_window.remaining,
           new_budget_remaining=input_window.decision_deadline - current_monotonic_now,
           preserve_every_other_canonical_field_explicitly=yes,
           deliver_to_same_invocation_only_after_atomic_cas=yes,
           target_state=RUNNING
       ):
        return SENT
    now_after_send_cas = clock.monotonic()
    if not finite_monotonic(now_after_send_cas) or
       now_after_send_cas >= input_window.decision_deadline:
        reconcile_input_race(result, INPUT_EXPIRED)
        return INPUT_EXPIRED
    quarantine_cas_lost(result)
    reconcile_input_race(result, CAS_LOST)
    preserve the winning current row, owner, lock, and dependency result exactly as observed
    return CAS_LOST

Every material-decision call site below must retain the returned `decision_ticket` and
return that ticket to the user/planning gate. When the user answers, call
`continue_after_user_decision(ticket, answer, mode)`; never treat the original report as
an authorization to send input or as a new current row.

quarantine_cancellation_race_event(result):
    # Runtime-owned stop events are not child-authored cancellation-race output.
    # Callers must dispatch both event kinds through classify_terminal so a valid
    # started RUNTIME_TERMINAL_EVENT can materialize its authoritative terminal row.
    # This helper is only for child reports; a runtime event reaching this boundary
    # is a caller bug and must fail closed without being written as a quarantine.
    require result.kind not in {RUNTIME_INTERRUPTION_EVENT, RUNTIME_TERMINAL_EVENT}
    scanned_result = validate_report_event(result)
    if scanned_result is INVALID or scanned_result is QUARANTINED:
        append only a redacted scanner-diagnostic quarantine record through the CAS below
        retain CANCEL_REQUESTED and the lock; stop
    append only a redacted attempt-scoped quarantine record for scanned_result through
    a CAS that expects the current task/attempt/invocation/report and CANCEL_REQUESTED
    overlay (and refuses any input transition while that overlay is present); never
    append raw child output, and do not change NEEDS_INPUT state, clear CANCEL_REQUESTED,
    or send input to the old invocation. A user decision observed after cancellation is
    context for a new bounded task only after the runtime confirms the old stop; a CAS
    loss quarantines the losing event and leaves the winning row and lock unchanged.
