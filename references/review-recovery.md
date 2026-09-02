# Review Recovery, Replacement, and Acceptance

Normative recovery mechanics for cancellation races, resumable partials, replacement reviewers, no-report handling, CAS reconciliation, and impact-scoped validation. Read only when recovery, replacement, or blocked acceptance is in scope.

cancellation_request_compare_and_set(values):
    derive cancellation_request_key from the exact run/task/attempt/invocation/token;
        record it in the metadata-only cancellation event and reuse it for every
        transport retry or race reconciliation for this invocation
        require current_task_row.agent_id is not UNASSIGNED; an unbound invocation
        must use record_binding_failure_cancel_requested_compare_and_set so the
        binding-failure target/timing and cancellation intent are persisted together
        instead of creating a sparse CANCEL_REQUESTED row
        permit a new cancellation request only when the full current row/version, run/task/
        parent-task/attempt/invocation identity, agent_id/agent_channel/
        transport_invocation_association, reserved_runtime_target,
        runtime_terminal_event_id, snapshot/content identity, owner, and lock match;
        current state is one of {CLAIMED, RUNNING, NEEDS_INPUT},
        current overlay is NONE, terminal_at and
        cancel_confirmed_at are UNSET, terminal_reason is NONE, and no accepted or
        integrated result is recorded. Match the current report_id/disposition and all
        binding/timing fields as expected values. Atomically write only the canonical
        overlay=CANCEL_REQUESTED and the supplied cancel_requested_at while retaining
        every other field, owner, and lock. A terminal/accepted row cannot be changed
        by a late stop request. On success return
        `{kind=COMMITTED, authoritative_row=new_row}`; on loss return
        `{kind=CAS_LOST, authoritative_row=transaction_winner,
          lost_operation=the complete cancellation projection}`. Callers must consume
        the authoritative row before deciding whether to issue the idempotent stop.

reconcile_cancellation_request_race(values):
    after a cancellation-request CAS loss, read the authoritative row exactly once
    and match the exact run/task/attempt/invocation/token, agent/channel/transport
    association, reserved_runtime_target, and runtime_terminal_event_id. If the winning row records a
        terminal result, confirmed stop, cancellation, quarantine, or accepted state,
        append a metadata-only cancel_superseded event and preserve that winning row;
        never add CANCEL_REQUESTED over the winner. If the authoritative row already
        has overlay=CANCEL_REQUESTED, reuse its cancellation event and the same
        deterministic stop idempotency key; do not issue a second logical stop request
        (a transport retry, if needed, must use that same key). If the same invocation
        remains in a live state with overlay=NONE and no confirmed
        terminal stop, use the complete binding-failure/cancellation helper when
        agent_id is UNASSIGNED; otherwise atomically call
        cancellation_request_compare_and_set against that exact authoritative row before
        sending anything. On success, issue the one idempotent stop request to the exact
        runtime target using that key. If this re-claim CAS loses, consume its authoritative
        winner outcome (already cancelled, terminal, or quarantined) and never send from
        the stale row; do not leave a live-NONE row treated as cancelled by assumption. If
        identity or liveness cannot be proved, quarantine the losing cancellation event and
        keep the authoritative owner/lock; never assume a CAS loss means the child stopped.
    return `{kind=the authoritative disposition, authoritative_row=authoritative_row}`
        so callers cannot continue with a stale cancellation row.

reconcile_replacement_post_spawn_cas_loss(values):
    after a replacement binding or post-spawn timing/gate CAS loses after the runtime
    has returned an agent,
    quarantine the losing binding candidate and read the authoritative replacement row
    once, matching the exact run/task/attempt/invocation/token, the exact returned
    agent_id, agent_channel, transport_invocation_association, reserved_runtime_target,
    and runtime_terminal_event_id.
    if the authoritative replacement row is PARTIAL:
        require its runtime_terminal_event_id/interruption_event_id is the exact
            validated runtime interruption/terminal event for the returned replacement
            invocation, with matching target, channel, transport association, checkpoint,
            artifact proof, and terminal timing; require replacement_index=1,
            replacement_count=1, replacement_of, snapshot, and content identity match
        preserve the row and return
            {kind=AUTHORITATIVE_WINNER, disposition=PARTIAL,
             authoritative_row=authoritative_row,
             runtime_event_or_report=the exact validated partial event/report}; the
        caller must route this through the replacement same-task PARTIAL resume/recovery
            handoff, never bind, stop, or append a block from the stale candidate
    if a terminal, accepted, confirmed-stop, or already-bound winner exists, preserve
        it and issue no stop from the stale candidate; return
            {kind=AUTHORITATIVE_WINNER,
             disposition=authoritative_row.disposition,
             authoritative_row=authoritative_row}
    If the exact row is still live with
        agent_id=UNASSIGNED and overlay=CANCEL_REQUESTED, and its binding-failure
        fields are not complete, first invoke the combined
        record_binding_failure_cancel_requested_compare_and_set against that exact
        row. A CAS-loss winner is consumed without a stale stop. After the combined
        CAS returns, if it lost, set authoritative_row to its transaction winner; if
        that winner is already a complete `CANCEL_REQUESTED` row, reuse its existing
        cancellation key and return `STOP_ALREADY_REQUESTED`, otherwise return
        `AUTHORITATIVE_WINNER` without issuing a stop from stale state. If the CAS
        committed, it records `binding_failure_provenance=SPAWN_UNCONFIRMED`; set
        authoritative_row to that returned winner, reuse its existing
        cancellation key, and issue exactly
        one idempotent stop if that row has not already recorded the stop request,
        otherwise issue no second logical request, and return
            {kind=STOP_REQUESTED or STOP_ALREADY_REQUESTED,
             authoritative_row=authoritative_row}. Do not fall through to the
        overlay=NONE or bound-agent branches using the pre-CAS row.
        If overlay=CANCEL_REQUESTED is already complete, reuse its cancellation
        event/idempotency key without a second logical request and return
            {kind=STOP_ALREADY_REQUESTED, authoritative_row=authoritative_row}. If the
        exact row is still live with agent_id=UNASSIGNED, overlay=NONE,
        binding_failure_provenance=NONE, report_id=NONE, and the reserved target is
        the exact returned replacement target, first invoke the combined
        record_binding_failure_cancel_requested_compare_and_set against that complete
        row. This is the single binding-failure/CANCEL_REQUESTED CAS: it must write
        `binding_failure_provenance=SPAWN_UNCONFIRMED`, the exact target/timing, and
        the deterministic cancellation key together. Consume a CAS-loss winner and
        issue no stale stop; after it records the canonical cancellation row, issue
        the one idempotent stop to that exact target; return
            {kind=STOP_REQUESTED, authoritative_row=authoritative_row}.
        If the exact row is still live with a bound agent and overlay=NONE, atomically
        claim CANCEL_REQUESTED with cancellation_request_compare_and_set against that
        row, then issue the one idempotent stop only after the reserved target also
        matches; return {kind=STOP_REQUESTED, authoritative_row=authoritative_row}.
        If that authoritative re-claim loses, consume the transaction's winner outcome
        and do not send from stale state. If identity, liveness, or the returned target
        cannot be proved, return
            {kind=REVIEW_BLOCKED, authoritative_row=authoritative_row}; retain the
            replacement slot/lock and quarantine without acceptance, recovery,
            replacement, or an unguarded stop. Every return is typed and carries the
            authoritative row; callers must not unconditionally overwrite it with a
            generic REVIEW_BLOCKED result.

consume_replacement_post_spawn_reconciliation(outcome):
    require outcome is a typed result with an authoritative_row; assign that row to
        current_task_row before any branch
    if outcome.disposition is PARTIAL:
        require outcome.kind is AUTHORITATIVE_WINNER and
            outcome.runtime_event_or_report is the exact validated partial context
        handoff_authoritative_partial_after_post_spawn(outcome); stop this replacement
        invocation without another bind, stop request, or replacement
    if outcome.kind is ALREADY_BOUND:
        require the authoritative row has the exact returned agent/channel/association/
            target, overlay=NONE, and live state
        rebind all local replacement identity/timing variables from current_task_row and
            continue only as BOUND; the caller may enter ordinary wait with this row
    if outcome.kind is AUTHORITATIVE_WINNER:
        retain the authoritative row/owner/lock and stop before wait, acceptance,
            recovery, replacement, or another stop request
    if outcome.kind in {STOP_REQUESTED, STOP_ALREADY_REQUESTED}:
        wait only for the one runtime stop confirmation on the authoritative row; a
            late stop is STOP_CONFIRMED_ONLY and an expired/unverified wait is
            REVIEW_BLOCKED, both retaining the replacement lock and closing no new slot
        stop without ordinary review wait or another replacement
    if outcome.kind is REVIEW_BLOCKED or outcome.kind is TARGET_UNAVAILABLE_UNVERIFIED:
        record the parent/session REVIEW_BLOCKED projection, retain the authoritative
            replacement slot/owner/lock, and stop
    otherwise reject the outcome as malformed and fail closed as REVIEW_BLOCKED

replacement_binding_full_rows(current_task_row, run_id, task_id, parent_task_id,
                              attempt, invocation_id, binding_token,
                              runtime_agent_id, runtime_channel,
                              transport_association, role, clock_source,
                              snapshot_id, content_identity,
                              replacement_spawn_requested_at,
                              replacement_spawn_confirmed_at, replacement_started_at,
                              replacement_recovery_deadline_at,
                              replacement_binding_failed_at, snapshot_deadline,
                              snapshot_budget_started_at,
                              budget_remaining_at_spawn, budget_remaining_at_binding,
                              replacement_gate_digest, replacement_decision_deadline,
                              review_owner, review_lock):
    # This constructor is intentionally verbose: it is the single named expansion
    # of the replacement-binding expected/new keyset. There is no implicit spread,
    # omission, or "retain the rest" behavior at this boundary.
    expected_row = FullTaskRow(
        run_id=current_task_row.run_id,
        version=current_task_row.version,
        task_id=current_task_row.task_id,
        parent_task_id=current_task_row.parent_task_id,
        agent_id=UNASSIGNED,
        agent_channel=UNASSIGNED,
        transport_invocation_association=UNASSIGNED,
        reserved_runtime_target=current_task_row.reserved_runtime_target,
        role=current_task_row.role,
        clock_source=current_task_row.clock_source,
        objective=current_task_row.objective,
        state=CLAIMED,
        overlay=NONE,
        depends_on=current_task_row.depends_on,
        dependency_requirements=current_task_row.dependency_requirements,
        read_scope=current_task_row.read_scope,
        write_scope=current_task_row.write_scope,
        impact_scope=current_task_row.impact_scope,
        base_snapshot=current_task_row.base_snapshot,
        base_content_identity=current_task_row.base_content_identity,
        snapshot_id=current_task_row.snapshot_id,
        content_identity=current_task_row.content_identity,
        execution_mode=current_task_row.execution_mode,
        isolation_mode=current_task_row.isolation_mode,
        budget=current_task_row.budget,
        resumable=current_task_row.resumable,
        focused_checks=current_task_row.focused_checks,
        full_suite_owner=current_task_row.full_suite_owner,
        invocation_id=current_task_row.invocation_id,
        report_id=NONE,
        binding_token=current_task_row.binding_token,
        binding_mode=current_task_row.binding_mode,
        created_at=current_task_row.created_at,
        spawn_requested_at=replacement_spawn_requested_at,
        spawn_confirmed_at=UNSET,
        started_at=UNKNOWN,
        snapshot_budget_started_at=current_task_row.snapshot_budget_started_at,
        deadline_at=current_task_row.deadline_at,
        snapshot_deadline_at=current_task_row.snapshot_deadline_at,
        attempt_deadline_at=current_task_row.attempt_deadline_at,
        recovery_deadline_at=current_task_row.recovery_deadline_at,
        binding_failure_recovery_deadline_at=UNSET,
        binding_failure_provenance=NONE,
        binding_failed_at=UNSET,
        replacement_decision_reserve_budget=
            current_task_row.replacement_decision_reserve_budget,
        replacement_decision_deadline=current_task_row.replacement_decision_deadline,
        replacement_stop_confirmed_at=current_task_row.replacement_stop_confirmed_at,
        replacement_decision_latest_at=current_task_row.replacement_decision_latest_at,
        replacement_decision_at=current_task_row.replacement_decision_at,
        replacement_decision_remaining=current_task_row.replacement_decision_remaining,
        spawn_reserve_budget=current_task_row.spawn_reserve_budget,
        budget_remaining_at_spawn=current_task_row.budget_remaining_at_spawn,
        budget_remaining_at_binding=current_task_row.budget_remaining_at_binding,
        replacement_gate_digest=UNSET,
        updated_at=current_task_row.updated_at,
        terminal_at=UNSET,
        prebinding_terminal_at=UNSET,
        prebinding_timing_digest=current_task_row.prebinding_timing_digest,
        late_bind_at=UNSET,
        attempt=current_task_row.attempt,
        replacement_count=current_task_row.replacement_count,
        budget_remaining=current_task_row.budget_remaining,
        budget_consumed_at_terminal=UNKNOWN,
        cancel_requested_at=UNSET,
        cancel_confirmed_at=UNSET,
        attention_required=NONE,
        terminal_reason=NONE,
        runtime_terminal_event_id=NONE,
        result_content_identity=NONE,
        interruption_event_id=NONE,
        checkpoint_id=NONE,
        checkpoint_content_identity=NONE,
        artifact_access_proof_id=NONE,
        integrated_content_identity=NONE,
        quarantine_reason=NONE,
        report_disposition=none,
        replacement_of=current_task_row.replacement_of,
        replacement_index=1,
        owner=review_owner,
        lock=review_lock
    )
    new_row = FullTaskRow(
        run_id=run_id,
        version=current_task_row.version + 1,
        task_id=task_id,
        parent_task_id=parent_task_id,
        agent_id=runtime_agent_id,
        agent_channel=runtime_channel,
        transport_invocation_association=transport_association,
        reserved_runtime_target=current_task_row.reserved_runtime_target,
        role=role,
        clock_source=clock_source,
        objective=current_task_row.objective,
        state=RUNNING,
        overlay=NONE,
        depends_on=current_task_row.depends_on,
        dependency_requirements=current_task_row.dependency_requirements,
        read_scope=current_task_row.read_scope,
        write_scope=current_task_row.write_scope,
        impact_scope=current_task_row.impact_scope,
        base_snapshot=current_task_row.base_snapshot,
        base_content_identity=current_task_row.base_content_identity,
        snapshot_id=snapshot_id,
        content_identity=content_identity,
        execution_mode=current_task_row.execution_mode,
        isolation_mode=current_task_row.isolation_mode,
        budget=current_task_row.budget,
        resumable=current_task_row.resumable,
        focused_checks=current_task_row.focused_checks,
        full_suite_owner=current_task_row.full_suite_owner,
        invocation_id=invocation_id,
        report_id=NONE,
        binding_token=binding_token,
        binding_mode=current_task_row.binding_mode,
        created_at=current_task_row.created_at,
        spawn_requested_at=replacement_spawn_requested_at,
        spawn_confirmed_at=replacement_spawn_confirmed_at,
        started_at=replacement_started_at,
        snapshot_budget_started_at=snapshot_budget_started_at,
        deadline_at=snapshot_deadline,
        snapshot_deadline_at=snapshot_deadline,
        attempt_deadline_at=snapshot_deadline,
        recovery_deadline_at=replacement_recovery_deadline_at,
        binding_failure_recovery_deadline_at=UNSET,
        binding_failure_provenance=NONE,
        binding_failed_at=replacement_binding_failed_at,
        replacement_decision_reserve_budget=
            current_task_row.replacement_decision_reserve_budget,
        replacement_decision_deadline=replacement_decision_deadline,
        replacement_stop_confirmed_at=current_task_row.replacement_stop_confirmed_at,
        replacement_decision_latest_at=current_task_row.replacement_decision_latest_at,
        replacement_decision_at=current_task_row.replacement_decision_at,
        replacement_decision_remaining=current_task_row.replacement_decision_remaining,
        spawn_reserve_budget=current_task_row.spawn_reserve_budget,
        budget_remaining_at_spawn=budget_remaining_at_spawn,
        budget_remaining_at_binding=budget_remaining_at_binding,
        replacement_gate_digest=replacement_gate_digest,
        updated_at=replacement_spawn_confirmed_at,
        terminal_at=UNSET,
        prebinding_terminal_at=UNSET,
        prebinding_timing_digest=current_task_row.prebinding_timing_digest,
        late_bind_at=UNSET,
        attempt=attempt,
        replacement_count=current_task_row.replacement_count,
        budget_remaining=budget_remaining_at_binding,
        budget_consumed_at_terminal=UNKNOWN,
        cancel_requested_at=UNSET,
        cancel_confirmed_at=UNSET,
        attention_required=NONE,
        terminal_reason=NONE,
        runtime_terminal_event_id=NONE,
        result_content_identity=NONE,
        interruption_event_id=NONE,
        checkpoint_id=NONE,
        checkpoint_content_identity=NONE,
        artifact_access_proof_id=NONE,
        integrated_content_identity=NONE,
        quarantine_reason=NONE,
        report_disposition=none,
        replacement_of=current_task_row.replacement_of,
        replacement_index=1,
        owner=review_owner,
        lock=review_lock
    )
    require keyset(expected_row) == CANONICAL_TASK_ROW_FIELDS and
        keyset(new_row) == CANONICAL_TASK_ROW_FIELDS
    return expected_row, new_row

replacement_binding_failure_timing_full_rows(current_task_row,
                                             reserved_runtime_target,
                                             binding_failed_at,
                                             binding_failure_recovery_deadline_at,
                                             cancel_requested_at, updated_at):
    require current_task_row.replacement_index == 1 and
        current_task_row.state == CLAIMED and
        current_task_row.agent_id == UNASSIGNED and
        current_task_row.agent_channel == UNASSIGNED and
        current_task_row.transport_invocation_association == UNASSIGNED and
        current_task_row.reserved_runtime_target == reserved_runtime_target and
        reserved_runtime_target is an exact target tuple and
        current_task_row.report_id == NONE and
        current_task_row.runtime_terminal_event_id == NONE and
        current_task_row.terminal_at == UNSET and
        current_task_row.binding_failure_provenance == NONE and
        current_task_row.binding_failed_at == UNSET and
        current_task_row.binding_failure_recovery_deadline_at == UNSET and
        current_task_row.overlay == NONE
    # Both literals enumerate every canonical field. The new row is not a patch;
    # only the named binding-failure/cancellation values differ from the expected row.
    expected_row = FullTaskRow(
        run_id=current_task_row.run_id,
        version=current_task_row.version,
        task_id=current_task_row.task_id,
        parent_task_id=current_task_row.parent_task_id,
        agent_id=current_task_row.agent_id,
        agent_channel=current_task_row.agent_channel,
        transport_invocation_association=current_task_row.transport_invocation_association,
        reserved_runtime_target=current_task_row.reserved_runtime_target,
        role=current_task_row.role,
        clock_source=current_task_row.clock_source,
        objective=current_task_row.objective,
        state=current_task_row.state,
        overlay=current_task_row.overlay,
        depends_on=current_task_row.depends_on,
        dependency_requirements=current_task_row.dependency_requirements,
        read_scope=current_task_row.read_scope,
        write_scope=current_task_row.write_scope,
        impact_scope=current_task_row.impact_scope,
        base_snapshot=current_task_row.base_snapshot,
        base_content_identity=current_task_row.base_content_identity,
        snapshot_id=current_task_row.snapshot_id,
        content_identity=current_task_row.content_identity,
        execution_mode=current_task_row.execution_mode,
        isolation_mode=current_task_row.isolation_mode,
        budget=current_task_row.budget,
        resumable=current_task_row.resumable,
        focused_checks=current_task_row.focused_checks,
        full_suite_owner=current_task_row.full_suite_owner,
        invocation_id=current_task_row.invocation_id,
        report_id=current_task_row.report_id,
        binding_token=current_task_row.binding_token,
        binding_mode=current_task_row.binding_mode,
        created_at=current_task_row.created_at,
        spawn_requested_at=current_task_row.spawn_requested_at,
        spawn_confirmed_at=current_task_row.spawn_confirmed_at,
        started_at=current_task_row.started_at,
        snapshot_budget_started_at=current_task_row.snapshot_budget_started_at,
        deadline_at=current_task_row.deadline_at,
        snapshot_deadline_at=current_task_row.snapshot_deadline_at,
        attempt_deadline_at=current_task_row.attempt_deadline_at,
        recovery_deadline_at=current_task_row.recovery_deadline_at,
        binding_failure_recovery_deadline_at=current_task_row.binding_failure_recovery_deadline_at,
        binding_failure_provenance=current_task_row.binding_failure_provenance,
        binding_failed_at=current_task_row.binding_failed_at,
        replacement_decision_reserve_budget=current_task_row.replacement_decision_reserve_budget,
        replacement_decision_deadline=current_task_row.replacement_decision_deadline,
        replacement_stop_confirmed_at=current_task_row.replacement_stop_confirmed_at,
        replacement_decision_latest_at=current_task_row.replacement_decision_latest_at,
        replacement_decision_at=current_task_row.replacement_decision_at,
        replacement_decision_remaining=current_task_row.replacement_decision_remaining,
        spawn_reserve_budget=current_task_row.spawn_reserve_budget,
        budget_remaining_at_spawn=current_task_row.budget_remaining_at_spawn,
        budget_remaining_at_binding=current_task_row.budget_remaining_at_binding,
        replacement_gate_digest=current_task_row.replacement_gate_digest,
        updated_at=current_task_row.updated_at,
        terminal_at=current_task_row.terminal_at,
        prebinding_terminal_at=current_task_row.prebinding_terminal_at,
        prebinding_timing_digest=current_task_row.prebinding_timing_digest,
        late_bind_at=current_task_row.late_bind_at,
        attempt=current_task_row.attempt,
        replacement_count=current_task_row.replacement_count,
        budget_remaining=current_task_row.budget_remaining,
        budget_consumed_at_terminal=current_task_row.budget_consumed_at_terminal,
        cancel_requested_at=current_task_row.cancel_requested_at,
        cancel_confirmed_at=current_task_row.cancel_confirmed_at,
        attention_required=current_task_row.attention_required,
        terminal_reason=current_task_row.terminal_reason,
        runtime_terminal_event_id=current_task_row.runtime_terminal_event_id,
        result_content_identity=current_task_row.result_content_identity,
        interruption_event_id=current_task_row.interruption_event_id,
        checkpoint_id=current_task_row.checkpoint_id,
        checkpoint_content_identity=current_task_row.checkpoint_content_identity,
        artifact_access_proof_id=current_task_row.artifact_access_proof_id,
        integrated_content_identity=current_task_row.integrated_content_identity,
        quarantine_reason=current_task_row.quarantine_reason,
        report_disposition=current_task_row.report_disposition,
        replacement_of=current_task_row.replacement_of,
        replacement_index=current_task_row.replacement_index,
        owner=current_task_row.owner,
        lock=current_task_row.lock
    )
    new_row = FullTaskRow(
        run_id=current_task_row.run_id,
        version=current_task_row.version + 1,
        task_id=current_task_row.task_id,
        parent_task_id=current_task_row.parent_task_id,
        agent_id=current_task_row.agent_id,
        agent_channel=current_task_row.agent_channel,
        transport_invocation_association=current_task_row.transport_invocation_association,
        reserved_runtime_target=reserved_runtime_target,
        role=current_task_row.role,
        clock_source=current_task_row.clock_source,
        objective=current_task_row.objective,
        state=current_task_row.state,
        overlay=CANCEL_REQUESTED,
        depends_on=current_task_row.depends_on,
        dependency_requirements=current_task_row.dependency_requirements,
        read_scope=current_task_row.read_scope,
        write_scope=current_task_row.write_scope,
        impact_scope=current_task_row.impact_scope,
        base_snapshot=current_task_row.base_snapshot,
        base_content_identity=current_task_row.base_content_identity,
        snapshot_id=current_task_row.snapshot_id,
        content_identity=current_task_row.content_identity,
        execution_mode=current_task_row.execution_mode,
        isolation_mode=current_task_row.isolation_mode,
        budget=current_task_row.budget,
        resumable=current_task_row.resumable,
        focused_checks=current_task_row.focused_checks,
        full_suite_owner=current_task_row.full_suite_owner,
        invocation_id=current_task_row.invocation_id,
        report_id=current_task_row.report_id,
        binding_token=current_task_row.binding_token,
        binding_mode=current_task_row.binding_mode,
        created_at=current_task_row.created_at,
        spawn_requested_at=current_task_row.spawn_requested_at,
        spawn_confirmed_at=current_task_row.spawn_confirmed_at,
        started_at=current_task_row.started_at,
        snapshot_budget_started_at=current_task_row.snapshot_budget_started_at,
        deadline_at=current_task_row.deadline_at,
        snapshot_deadline_at=current_task_row.snapshot_deadline_at,
        attempt_deadline_at=current_task_row.attempt_deadline_at,
        recovery_deadline_at=current_task_row.recovery_deadline_at,
        binding_failure_recovery_deadline_at=binding_failure_recovery_deadline_at,
        binding_failure_provenance=SPAWN_UNCONFIRMED,
        binding_failed_at=binding_failed_at,
        replacement_decision_reserve_budget=current_task_row.replacement_decision_reserve_budget,
        replacement_decision_deadline=current_task_row.replacement_decision_deadline,
        replacement_stop_confirmed_at=current_task_row.replacement_stop_confirmed_at,
        replacement_decision_latest_at=current_task_row.replacement_decision_latest_at,
        replacement_decision_at=current_task_row.replacement_decision_at,
        replacement_decision_remaining=current_task_row.replacement_decision_remaining,
        spawn_reserve_budget=current_task_row.spawn_reserve_budget,
        budget_remaining_at_spawn=current_task_row.budget_remaining_at_spawn,
        budget_remaining_at_binding=current_task_row.budget_remaining_at_binding,
        replacement_gate_digest=current_task_row.replacement_gate_digest,
        updated_at=updated_at,
        terminal_at=current_task_row.terminal_at,
        prebinding_terminal_at=current_task_row.prebinding_terminal_at,
        prebinding_timing_digest=current_task_row.prebinding_timing_digest,
        late_bind_at=current_task_row.late_bind_at,
        attempt=current_task_row.attempt,
        replacement_count=current_task_row.replacement_count,
        budget_remaining=current_task_row.budget_remaining,
        budget_consumed_at_terminal=current_task_row.budget_consumed_at_terminal,
        cancel_requested_at=(current_task_row.cancel_requested_at
            if current_task_row.cancel_requested_at is finite else cancel_requested_at),
        cancel_confirmed_at=current_task_row.cancel_confirmed_at,
        attention_required=current_task_row.attention_required,
        terminal_reason=current_task_row.terminal_reason,
        runtime_terminal_event_id=current_task_row.runtime_terminal_event_id,
        result_content_identity=current_task_row.result_content_identity,
        interruption_event_id=current_task_row.interruption_event_id,
        checkpoint_id=current_task_row.checkpoint_id,
        checkpoint_content_identity=current_task_row.checkpoint_content_identity,
        artifact_access_proof_id=current_task_row.artifact_access_proof_id,
        integrated_content_identity=current_task_row.integrated_content_identity,
        quarantine_reason=current_task_row.quarantine_reason,
        report_disposition=current_task_row.report_disposition,
        replacement_of=current_task_row.replacement_of,
        replacement_index=current_task_row.replacement_index,
        owner=current_task_row.owner,
        lock=current_task_row.lock
    )
    require keyset(expected_row) == CANONICAL_TASK_ROW_FIELDS and
        keyset(new_row) == CANONICAL_TASK_ROW_FIELDS
    require new_row.version == expected_row.version + 1
    return expected_row, new_row

replacement_binding_compare_and_set(expected_row, new_row):
    require keyset(expected_row) == CANONICAL_TASK_ROW_FIELDS and
        keyset(new_row) == CANONICAL_TASK_ROW_FIELDS; the expanded arguments in the
        replacement binding call are not a sparse patch or a second schema
    require expected_row is the exact replacement CLAIMED row with
        agent_id/agent_channel/transport_invocation_association=UNASSIGNED,
        reserved target, report_id=NONE, runtime_terminal_event_id=NONE,
        replacement_index=1, replacement_count=1, overlay=NONE, and every canonical
        timing/budget/provenance/scope/owner/lock field explicitly matched
    require new_row explicitly writes the exact bound agent/channel/association,
        target, spawn/binding samples, budget remainder, replacement gate digest,
        state=RUNNING, overlay=NONE, report_id=NONE, and every unchanged/reset
        canonical field; `snapshot_deadline`, `attempt_deadline`, and
        `recovery_deadline` are invalid aliases--use their canonical `_at` fields
    invoke full_task_row_compare_and_set(expected_row, new_row); on success return
        COMMITTED with the authoritative bound row; on loss return CAS_LOST with a
        typed replacement-binding lost_operation handle and the transaction's
        authoritative winner. The caller must write the COMMITTED row back before
        validating any later event or stop.

replacement_binding_failure_timing_compare_and_set(expected_row, new_row):
    require keyset(expected_row) == CANONICAL_TASK_ROW_FIELDS and
        keyset(new_row) == CANONICAL_TASK_ROW_FIELDS and
        new_row.version == expected_row.version + 1
    require expected_row is the exact current replacement CLAIMED row, with
        agent/channel/association=UNASSIGNED, the exact reserved target, report and
        runtime-terminal sentinels, binding-failure fields UNSET, and every canonical
        task/scope/timing/budget/replacement/proof/owner/lock field explicitly matched
    require new_row preserves every expected field except the named transition values:
        `binding_failed_at`, `binding_failure_recovery_deadline_at`,
        `binding_failure_provenance=SPAWN_UNCONFIRMED`, `overlay=CANCEL_REQUESTED`,
        the cancellation sample (preserving an already finite sample), and `updated_at`;
        the deterministic cancellation key is recorded in the same metadata-only event
        transaction, not smuggled in as an unlisted row field
    require new_row.reserved_runtime_target == expected_row.reserved_runtime_target and
        new_row.agent_id == UNASSIGNED and
        new_row.agent_channel == UNASSIGNED and
        new_row.transport_invocation_association == UNASSIGNED and
        new_row.report_id == NONE and new_row.runtime_terminal_event_id == NONE
    invoke the non-reconciling full_task_row_compare_and_set(expected_row, new_row)
    if it returns CAS_LOST, return a typed lost_operation handle containing the
        authoritative winner, exact event/target identity, and a one-shot
        non-reconciling retry entry point; do not call reconciliation from this wrapper
    on success return COMMITTED with the authoritative new row so every caller reloads
        the combined CANCEL_REQUESTED state before issuing the idempotent stop

stop_at_deadline_or_recovery_boundary():
    # This helper always returns a typed outcome. `kind` is one of
    # STOP_REQUESTED, STOP_ALREADY_REQUESTED, MATERIALIZED,
    # TARGET_UNAVAILABLE_UNVERIFIED, BLOCKED_UNVERIFIED, or AUTHORITATIVE_WINNER;
    # every outcome carries the authoritative row observed or committed by its CAS.
    cancellation_request_key = deterministic_key(run_id, task_id, attempt,
        invocation_id, binding_token)
    now = clock.monotonic() immediately before the stop CAS
    if now is not a finite sample from the validated clock:
        cancel_sample = runtime_attested_monotonic_stop_request_sample_or UNKNOWN
        record timing UNVERIFIED; UNKNOWN is permitted for cancel_requested_at only
            in this branch and makes the attempt permanently ineligible for acceptance,
            recovery, replacement, takeover, or unlock until a separate runtime stop
            record supplies a valid timestamp
    else:
        cancel_sample = now
    if current_task_row.agent_id is UNASSIGNED and
       current_task_row.reserved_runtime_target is NONE:
        target_outcome = reconcile_unbound_target_before_stop_once(cancel_sample)
        if target_outcome.kind is TERMINAL:
            current_task_row = target_outcome.authoritative_row
            return {kind=MATERIALIZED, authoritative_row=current_task_row,
                    disposition=target_outcome.disposition,
                    triggering_event=target_outcome.runtime_event}
        if target_outcome.kind is AUTHORITATIVE_WINNER:
            return {kind=AUTHORITATIVE_WINNER,
                    authoritative_row=target_outcome.authoritative_row}
        if target_outcome.kind is TARGET_UNAVAILABLE_UNVERIFIED:
            retain UNADDRESSABLE/CANCEL_REQUESTED/BLOCKED and the owner/lock
            return {kind=TARGET_UNAVAILABLE_UNVERIFIED,
                    authoritative_row=target_outcome.authoritative_row}
        current_task_row = target_outcome.authoritative_row
    elif current_task_row.agent_id is UNASSIGNED and
         current_task_row.reserved_runtime_target is UNADDRESSABLE:
        target_outcome = reconcile_unaddressable_runtime_once()
        if target_outcome.kind is TERMINAL:
            current_task_row = target_outcome.authoritative_row
            return {kind=MATERIALIZED, authoritative_row=current_task_row,
                    disposition=target_outcome.disposition,
                    triggering_event=target_outcome.runtime_event}
        if target_outcome.kind is AUTHORITATIVE_WINNER or
           target_outcome.kind is TARGET_UNAVAILABLE_UNVERIFIED:
            retain UNADDRESSABLE/CANCEL_REQUESTED/BLOCKED and the owner/lock
            return {kind=target_outcome.kind,
                    authoritative_row=target_outcome.authoritative_row}
        current_task_row = target_outcome.authoritative_row

    if current_task_row.agent_id is not UNASSIGNED and
       current_task_row.reserved_runtime_target is not an exact runtime target tuple:
        target_outcome = reconcile_bound_target_once_through_authoritative_runtime()
        if target_outcome.kind is AUTHORITATIVE_WINNER:
            current_task_row = target_outcome.authoritative_row
            return {kind=AUTHORITATIVE_WINNER,
                    authoritative_row=current_task_row}
        if target_outcome.kind is TARGET_UNAVAILABLE_UNVERIFIED or
           target_outcome.kind is BLOCKED_UNVERIFIED:
            current_task_row = target_outcome.authoritative_row
            record timing UNVERIFIED and REVIEW_BLOCKED; retain the owner/lock and do
                not guess a target or claim that the stop was confirmed
            return {kind=TARGET_UNAVAILABLE_UNVERIFIED,
                    authoritative_row=current_task_row}
        require target_outcome.kind is RESOLVED
        current_task_row = target_outcome.authoritative_row

    require current_task_row.reserved_runtime_target is an exact runtime target tuple
    if current_task_row.agent_id is UNASSIGNED:
        # An unbound invocation cannot use the ordinary overlay-only CAS. Its
        # cancellation winner must also close the binding-failure timing window. Once
        # the combined row is already canonical, this helper is a pure stop path: it
        # must not issue a second binding CAS merely because timing is UNVERIFIED.
        if current_task_row.overlay is CANCEL_REQUESTED and
           current_task_row.binding_failure_provenance is SPAWN_UNCONFIRMED:
            require cancellation_intent_exists(current_task_row) and
                current_task_row.reserved_runtime_target is an exact target and
                ((finite_monotonic(current_task_row.binding_failed_at) and
                  finite_monotonic(
                      current_task_row.binding_failure_recovery_deadline_at
                  )) or
                 (current_task_row.binding_failed_at is UNKNOWN and
                  current_task_row.binding_failure_recovery_deadline_at is UNVERIFIED))
            # The complete binding-failure/CANCEL_REQUESTED CAS already won. Reuse its
            # persisted key and timing; do not call the binding CAS again.
        elif current_task_row.overlay is NONE and
             current_task_row.binding_failure_provenance is NONE:
            binding_sample = (current_task_row.binding_failed_at
                if finite_monotonic(current_task_row.binding_failed_at)
                else cancel_sample)
            unbound_cancel = record_binding_failure_cancel_requested_compare_and_set(
                current_task_row,
                binding_failed_at=binding_sample
                    if finite_monotonic(binding_sample) else UNKNOWN,
                cancel_requested_at=cancel_sample,
                runtime_target=current_task_row.reserved_runtime_target
            )
            if unbound_cancel.kind is CAS_LOST:
                quarantine_cas_lost(current_task_row)
                unbound_cancel = reconcile_binding_failure_cas_loss(
                    unbound_cancel.authoritative_row, same runtime target and timing
                )
            if unbound_cancel.kind is TARGET_UNAVAILABLE_UNVERIFIED:
                retain UNADDRESSABLE/CANCEL_REQUESTED/BLOCKED and the owner/lock
                return {kind=TARGET_UNAVAILABLE_UNVERIFIED,
                        authoritative_row=unbound_cancel.authoritative_row}
            if unbound_cancel.kind is not RECORDED:
                retain the authoritative winner and owner/lock; stop without issuing a
                    stale stop request or re-running the binding-failure CAS
                return {kind=AUTHORITATIVE_WINNER,
                        authoritative_row=unbound_cancel.authoritative_row}
            current_task_row = unbound_cancel.authoritative_row
        else:
            retain the authoritative unbound row and owner/lock; record REVIEW_BLOCKED
            for an inconsistent cancellation/provenance combination and do not issue
            another binding CAS or stop request
            return {kind=TARGET_UNAVAILABLE_UNVERIFIED,
                    authoritative_row=current_task_row}
    elif current_task_row.overlay is not CANCEL_REQUESTED:
        cancellation_outcome = cancellation_request_compare_and_set(
            expected_current_row=current_task_row,
            expected_task_id=task_id,
            expected_attempt=attempt,
            expected_invocation_id=invocation_id,
            expected_owner=current_task_row.owner,
            expected_lock=current_task_row.lock,
            expected_overlay=current_task_row.overlay,
            cancel_requested_at=cancel_sample,
            cancellation_request_key=cancellation_request_key,
            target_overlay=CANCEL_REQUESTED,
            retain_owner_and_lock=yes
        )
        if cancellation_outcome.kind is CAS_LOST:
            quarantine_cas_lost(current_task_row)
            race_outcome = reconcile_cancellation_request_race(
                task_id, attempt, invocation_id, binding_token, current_task_row,
                cancellation_outcome
            )
            current_task_row = race_outcome.authoritative_row
            retain the winning row and lock; stop without recovery or replacement;
            return {kind=AUTHORITATIVE_WINNER, authoritative_row=current_task_row}
        require cancellation_outcome.kind is COMMITTED
        current_task_row = cancellation_outcome.authoritative_row
    if current_task_row.overlay is CANCEL_REQUESTED:
        reuse the recorded cancellation_request_key; a transport retry, if needed,
            uses that same key and is not a second logical request
        issue at most one idempotent close/interrupt/cancel request to the exact target;
            the request is not itself stop confirmation
        stop_kind = STOP_ALREADY_REQUESTED
    else:
        issue exactly one idempotent close/interrupt/cancel request to the exact target
            using cancellation_request_key; the request is not itself stop confirmation
        stop_kind = STOP_REQUESTED
    if cancel_sample is UNKNOWN or timing is UNVERIFIED:
        record REVIEW_BLOCKED with the canonical CANCEL_REQUESTED overlay and retained
            owner/lock; do not process a child result as recovery and do not create a
            replacement even if a stop acknowledgement is returned
        stop_kind = BLOCKED_UNVERIFIED
    do not extend snapshot_deadline or recovery_deadline and do not poll status;
        consume a runtime terminal-stop event if the platform delivers one, otherwise
        retain CANCEL_REQUESTED and the lock indefinitely until runtime confirms stop
    pass every late child result/event through classify_terminal or
        quarantine_cancellation_race_event (which performs the complete scan); a late
    result can never clear cancellation, send input, unlock, replace, or be accepted
    return {kind=stop_kind, authoritative_row=current_task_row,
            cancellation_request_key=cancellation_request_key}

reconcile_input_race(result, outcome):
    take one fresh monotonic sample and read the authoritative current row once
    if outcome is INPUT_EXPIRED or the applicable deadline has now passed:
        if the current row still matches result's task/attempt/invocation and is live:
            stop_at_deadline_or_recovery_boundary()
        else:
            issue exactly one idempotent stop request to result's exact runtime
                invocation, retain the winning row/owner/lock, and wait only for its
                runtime terminal confirmation; never overwrite the winning row
        return
    if outcome is CAS_LOST:
        if the winning row already records a terminal/cancellation stop for result's
           invocation, preserve it and do not send input
        else if the winning row still owns the same live invocation before its deadline:
            preserve the winning row and do not send a second input
        else:
            issue one exact-invocation stop request, quarantine the losing event, and
            retain the winning row/lock until runtime confirms the old invocation stopped

reviewer_resumable_partial_is_authorized(result):
    require stored_role is reviewer, the current attempt is runtime-confirmed stopped,
        the parent-declared task policy is RESUMABLE=yes, the result is either a
        validated runtime RUNTIME_INTERRUPTION_EVENT with runtime-owned RESUMABLE=yes
        and valid_checkpoint(result), or a validated PARTIAL report whose checkpoint,
        artifact identity, changed-paths, focused-check results, and continuation
        context have been independently verified by the parent; a child claim of
        RESUMABLE or a checkpoint is never sufficient
    validated_runtime_stop_event_for_partial(result)
    require the event is on-time (not STOP_CONFIRMED_ONLY), the same snapshot/content
    identity remains current, and either the original attempt is still the
        un-replaced slot (`replacement_index=0`, `replacement_count=0`) or the
        already-claimed replacement is the current attempt
        (`replacement_index=1`, `replacement_count=1`, valid `replacement_of` and
        replacement gate provenance). No second replacement slot may be claimed.
    resume_now = clock.monotonic()
    if current_task_row.replacement_index == 0:
        resume_cutoff = min(current_task_row.recovery_deadline_at,
            current_task_row.snapshot_deadline_at -
            review_replacement_decision_reserve_budget -
            review_spawn_reserve_budget - review_replacement_min_budget)
        require finite_monotonic(resume_now) and resume_now < resume_cutoff
        # A PARTIAL arriving during the bounded recovery grace may still resume, but
        # only for the remaining original/recovery window. It never receives a fresh
        # initial slice and cannot consume the reserved replacement handoff budget.
    else:
        resume_cutoff = current_task_row.snapshot_deadline_at
        require finite_monotonic(resume_now) and
            resume_now < current_task_row.snapshot_deadline_at
    return true only when every runtime/parent-owned identity, artifact, checkpoint,
        and stop condition is valid

validated_runtime_stop_event_for_partial(result):
    if result.kind is RUNTIME_INTERRUPTION_EVENT:
        require runtime_interruption_event_validates(result) and
            runtime_terminal_event_id(result) is a nonempty runtime-owned identity
        return result
    require result.status is PARTIAL and the parent/runtime supplied a separate
        validated runtime stop event for this exact run/task/attempt/invocation/token,
        target, channel/association, snapshot/content identity, and report/checkpoint
        context; child prose and a ledger terminal label are not a substitute
    require runtime_terminal_or_cancellation_is_confirmed(parent_runtime_stop_event)
    return parent_runtime_stop_event

materialize_validated_partial_compare_and_set(result,
                                              validated_runtime_stop_event=NONE):
    if result.kind is RUNTIME_INTERRUPTION_EVENT:
        require the dedicated interruption classifier already won a full-row CAS to
            state=PARTIAL for this exact attempt; validated_runtime_stop_event = result
            return COMMITTED with the authoritative PARTIAL row and disposition=PARTIAL
    require result.status is PARTIAL, the complete report scanner has passed,
        validated_runtime_stop_event_for_partial(result) is the same exact
        validated_runtime_stop_event, the parent has independently verified the
        checkpoint, artifact and result content identity, and the current row is
        still live with overlay=NONE
    use the scanner's normalized lower-case report field names for checkpoint and
        result identities below; a missing normalized field is a validation failure
    if current_task_row.state is PARTIAL and
       current_task_row.report_id == result.report_id:
        require current_task_row.runtime_terminal_event_id ==
            runtime_terminal_event_id(validated_runtime_stop_event) and
            current_task_row.terminal_at ==
                validated_runtime_stop_event.terminal_at and
            current_task_row.cancel_confirmed_at ==
                validated_runtime_stop_event.cancel_confirmed_at and
            current_task_row.checkpoint_id == result.checkpoint_id and
            current_task_row.checkpoint_content_identity ==
                result.checkpoint_content_identity and
            current_task_row.result_content_identity ==
                result.result_content_identity and
            current_task_row.artifact_access_proof_id ==
                parent_verified_artifact_access_proof_id(result)
        return COMMITTED with the authoritative PARTIAL row and disposition=PARTIAL
    preserve the authoritative row and return QUARANTINED when any idempotency
        identity or timing value differs
    atomically full-row compare-and-set the exact current row/version, run/task/
        parent-task/role/attempt/invocation/agent/token, snapshot/content identity,
        state in {CLAIMED, RUNNING}, overlay=NONE, prior report_id/disposition,
        terminal_at=UNSET, runtime_terminal_event_id=NONE,
        terminal_reason=NONE, owner, lock, and every other
        canonical row field as an explicit expected value; write the validated report_id,
        state=PARTIAL, overlay=NONE,
        terminal_at=validated_runtime_stop_event.terminal_at,
        cancel_confirmed_at=validated_runtime_stop_event.cancel_confirmed_at,
        runtime-owned stop timing and result content
        identity, interruption_event_id=NONE, runtime_terminal_event_id=
        runtime_terminal_event_id(validated_runtime_stop_event),
        the parent-verified checkpoint_id,
        checkpoint_content_identity and artifact_access_proof_id, terminal_reason=PARTIAL,
        budget_consumed_at_terminal=terminal_at - snapshot_budget_started_at,
        report_disposition=none, attention_required=NONE, and retain owner/lock
    if the CAS loses:
        return CAS_LOST with a typed lost_operation handle containing the complete
        expected/new FullTaskRow projection, the exact validated runtime stop event ID,
        the report/checkpoint/proof context, the transaction winner, and one
        non-reconciling partial materializer; do not retry internally
    return COMMITTED with the authoritative PARTIAL row and disposition=PARTIAL

same_task_resume_compare_and_set(expected_row, new_row,
                                  validated_runtime_stop_event):
    require the exact current PARTIAL row, the parent/runtime-owned predecessor stop
        identity, unchanged snapshot/content/scope/owner/lock, and the legal
        replacement_index/count; expected_row and new_row must each have exactly
        CANONICAL_TASK_ROW_FIELDS
    require new_row is the explicit same-task CLAIMED projection with attempt+1,
        fresh invocation/token, all attempt-scoped timing/proof/report fields reset
        to their sentinels, and no restored budget or second replacement slot
    invoke the non-reconciling full_task_row_compare_and_set once
    on success return COMMITTED with the authoritative new row and
        disposition=RESUME_COMMITTED
    on loss return CAS_LOST with a typed lost_operation handle whose one-shot retry
        entry point is this same-task resume operation; do not retry internally

resume_resumable_partial(result):
    validated_runtime_stop_event = validated_runtime_stop_event_for_partial(result)
    require reviewer_resumable_partial_is_authorized(result)
    partial_materialization = materialize_validated_partial_compare_and_set(
        result, validated_runtime_stop_event
    )
    if partial_materialization is CAS_LOST:
        replay_outcome = reconcile_runtime_event_cas_loss(
            validated_runtime_stop_event, partial_materialization
        )
        replay_decision = consume_terminal_decision(replay_outcome)
        if replay_decision is not PARTIAL:
            preserve the authoritative winner, retain its owner/lock, and stop without
                a second partial or resume CAS
    else:
        require partial_materialization is COMMITTED
        current_task_row = partial_materialization.authoritative_row
    require current_task_row.state is PARTIAL; if overlay is CANCEL_REQUESTED, require
        either that the same result is the on-time runtime stop that won the
        cancellation CAS or runtime_stop_preceded_cancel_request(result) proves
        that this exact stop completed before the cancellation CAS; a late stop is
        never resumable
    resume_now = clock.monotonic()
    if current_task_row.replacement_index == 0:
        resumed_attempt_deadline = min(current_task_row.recovery_deadline_at,
            current_task_row.snapshot_deadline_at -
            review_replacement_decision_reserve_budget -
            review_spawn_reserve_budget - review_replacement_min_budget)
        resumed_recovery_deadline = resumed_attempt_deadline
    else:
        resume_cutoff = current_task_row.snapshot_deadline_at
        resumed_attempt_deadline = resume_cutoff
        resumed_recovery_deadline = resume_cutoff
    require same_task_resume_deadline_formula_valid(
        replacement_index=current_task_row.replacement_index,
        recovery_deadline_before_resume=current_task_row.recovery_deadline_at,
        snapshot_deadline_at=current_task_row.snapshot_deadline_at,
        resumed_attempt_deadline=resumed_attempt_deadline,
        resumed_recovery_deadline=resumed_recovery_deadline
    )
    require finite_monotonic(resume_now) and
        0 < resumed_attempt_deadline - resume_now and
        resume_now < resumed_attempt_deadline
    reserve a fresh invocation_id and binding_token, then call
        same_task_resume_compare_and_set with one complete FullTaskRow expected/new
        projection:
        expect the exact current row/version, run_id, task_id, parent_task_id, role,
        attempt, invocation_id, agent_id, agent_channel,
        transport_invocation_association, binding_token, snapshot/content identities,
        reserved_runtime_target, runtime_terminal_event_id,
        state=PARTIAL, overlay in {NONE, CANCEL_REQUESTED},
        report/checkpoint/result/interruption-event/proof identities,
        expected_attention_required=current_task_row.attention_required,
        expected_report_id=current_task_row.report_id, owner, lock,
        replacement_index in {0, 1}, and replacement_count. For index 0 require
        replacement_count=0 and the original-attempt gate sentinels; for index 1
        require replacement_count=1, valid replacement_of provenance, and the
        already-bound replacement gate/deadline fields. Match budget_consumed_at_terminal
        and every other canonical row field as explicit expected values;
        create no replacement slot and do not change the immutable snapshot
        write the same task_id and scope with attempt+1, the fresh invocation/token,
        agent_id=UNASSIGNED, agent_channel=UNASSIGNED,
        transport_invocation_association=UNASSIGNED, report_id=NONE, state=CLAIMED,
        overlay=NONE, attempt_deadline=resumed_attempt_deadline, without restoring
        consumed budget or granting fresh budget, and
        deadline_at=resumed_attempt_deadline,
        snapshot_deadline_at=current_task_row.snapshot_deadline_at,
        attempt_deadline_at=resumed_attempt_deadline,
        recovery_deadline_at=resumed_recovery_deadline,
        updated_at=resume_now,
        binding_failure_recovery_deadline_at=UNSET,
        budget_remaining=current_task_row.snapshot_deadline_at-resume_now; reset the
        attempt-scoped timing, cancellation, terminal, checkpoint/event/proof, and
        disposition fields
        explicitly to their documented sentinels (report_id=NONE,
        spawn_requested_at=UNSET, spawn_confirmed_at=UNSET, started_at=UNKNOWN,
        binding_failed_at=UNSET, binding_failure_recovery_deadline_at=UNSET,
        prebinding_terminal_at=UNSET, prebinding_timing_digest=UNSET,
        late_bind_at=UNSET, terminal_at=UNSET, cancel_requested_at=UNSET,
        cancel_confirmed_at=UNSET,
        interruption_event_id=NONE, checkpoint_id=NONE,
        binding_failure_provenance=NONE,
        checkpoint_content_identity=NONE, artifact_access_proof_id=NONE,
        reserved_runtime_target=NONE, runtime_terminal_event_id=NONE,
        result_content_identity=NONE, integrated_content_identity=NONE,
        quarantine_reason=NONE, report_disposition=none,
        attention_required=NONE, budget_remaining_at_spawn=UNSET,
        budget_remaining_at_binding=UNSET, budget_consumed_at_terminal=UNKNOWN,
        replacement_gate_digest=UNSET for replacement_index=1, or NONE for index=0).
        For original index 0, explicitly preserve the configured
        replacement_decision_reserve_budget, replacement_decision_deadline, and
        spawn_reserve_budget, while writing
        replacement_stop_confirmed_at=UNSET, replacement_decision_latest_at=UNSET,
        replacement_decision_at=UNSET, replacement_decision_remaining=UNSET,
        replacement_gate_digest=NONE, replacement_of=NONE, replacement_index=0,
        replacement_count=0, attempt_deadline_at=resumed_attempt_deadline, and
        recovery_deadline_at=resumed_recovery_deadline. For replacement index 1,
        explicitly preserve replacement_index=1, replacement_count=1,
        replacement_of, replacement_decision_reserve_budget,
        replacement_decision_deadline, replacement_stop_confirmed_at,
        replacement_decision_latest_at, replacement_decision_at,
        replacement_decision_remaining, spawn_reserve_budget, and fixed snapshot
        deadline as immutable decision provenance, but set the current attempt's
        replacement_gate_digest,
        budget_remaining_at_spawn, and budget_remaining_at_binding to UNSET. Do not
        reuse the predecessor's spawn/binding samples or digest and do not open a
        second replacement slot or restore consumed budget. The resumed invocation gets new
        unassigned channel/association fields and must bind them anew to its own
        runtime transport.
        Explicitly write new_replacement_index=current_task_row.replacement_index,
        new_replacement_count=current_task_row.replacement_count, and
        new_replacement_of=current_task_row.replacement_of; no replacement field may
        be inherited implicitly. For every canonical field not listed as a reset or
        new-attempt identity above, explicitly pass new_<field>=current_task_row.<field>
        (except version, which increments) in the same CAS; no field may be inherited
        by omission.
        retain the
        owner/lock, resumable policy, replacement counters, and snapshot/content identity
        and attach continuation_context={prior validated report id/content identity,
            checkpoint id/content identity, preserved artifacts, completed scope,
            focused checks, open risks, and exact next action}
        attach same_task_resume=yes to the resumed invocation's timing context; every
        subsequent deadline validator must use that context so the original resume
        cutoff is validated instead of reapplying the untouched initial-slice formula
    resume_cas = the result of same_task_resume_compare_and_set
    if resume_cas.kind is CAS_LOST:
        replay_outcome = reconcile_same_task_resume_cas_loss(
            validated_runtime_stop_event, resume_cas
        )
        if replay_outcome.kind is RESUME_COMMITTED:
            current_task_row = replay_outcome.authoritative_row
        else:
            preserve the authoritative winner, quarantine the partial result, retain
            the winning row/lock, and stop without a second resume CAS
    else:
        require resume_cas.kind is COMMITTED
        current_task_row = resume_cas.authoritative_row
    attempt = current_task_row.attempt
    invocation_id = current_task_row.invocation_id
    binding_token = current_task_row.binding_token
    replacement_index = current_task_row.replacement_index
    snapshot_deadline = current_task_row.snapshot_deadline_at
    attempt_deadline = current_task_row.attempt_deadline_at
    recovery_deadline = current_task_row.recovery_deadline_at
    if replacement_index == 1:
        resume_replacement_budget_gate(current_task_row, snapshot_deadline,
            current_task_row.replacement_decision_at,
            current_task_row.replacement_decision_remaining,
            current_task_row.replacement_stop_confirmed_at,
            current_task_row.replacement_decision_latest_at)
        # This is a new attempt-specific gate, not a new replacement decision: sample
        # spawn/binding from the same monotonic clock, require the fixed remaining
        # budget, reserve the exact target, and fill a fresh digest through complete
        # FullTaskRow CAS before accepting any resumed output.
    invoke only this same-task resumed attempt with that continuation context; if binding
        or resume delivery fails, enter the normal binding-failure stop path and never
        convert the failure into a reviewer replacement

resume_replacement_budget_gate(row, fixed_snapshot_deadline, decision_at,
                               decision_remaining, stop_confirmed_at, latest_at):
    require row.replacement_index=1, row.replacement_count=1,
        row.replacement_gate_digest=UNSET, row.budget_remaining_at_spawn=UNSET,
        row.budget_remaining_at_binding=UNSET, and the preserved replacement decision
        provenance is exact and unchanged
    take the final monotonic spawn_requested_at sample inside the same runtime-atomic
        pre_spawn transaction that reserves the exact target and launches this resumed
        invocation; require stop_confirmed_at <= decision_at <= spawn_requested_at <
        latest_at <= fixed_snapshot_deadline and
        fixed_snapshot_deadline - spawn_requested_at >=
            review_spawn_reserve_budget + review_replacement_min_budget
    persist the target, spawn sample, and budget_remaining_at_spawn through a complete
        FullTaskRow CAS; after binding, take spawn_confirmed_at from the runtime,
        require spawn_requested_at <= spawn_confirmed_at <= fixed_snapshot_deadline and
        fixed_snapshot_deadline - spawn_confirmed_at >= review_replacement_min_budget,
        then atomically fill budget_remaining_at_binding and a fresh
        replacement_gate_digest containing the preserved decision provenance and the
        new spawn/binding samples. A missing/reversed/overrun gate blocks the resumed
    attempt and retains the lock; no report or acceptance may precede this CAS.

reconcile_replacement_slot_cas_loss(replacement_claim, cas_lost):
    quarantine_cas_lost(replacement_claim)
    authoritative_row = cas_lost.authoritative_row from the transaction
        (otherwise read the current row exactly once; never poll)
    require the row matches the same run/task/snapshot/content/owner/lock and the
        predecessor event recorded by replacement_claim
    if the row is the exact intended replacement projection with the new attempt,
       invocation_id, binding_token, replacement_index=1, replacement_count=1,
       state=CLAIMED, report_id=NONE, runtime_terminal_event_id=NONE,
       reserved_runtime_target=NONE, and replacement_gate_digest=UNSET:
        # The claim won but the caller lost its response. It is safe to continue only
        # through the runtime-atomic spawn boundary, whose target reservation/version
        # CAS prevents a second caller from spawning the same replacement.
        return {kind=REPLACEMENT_CLAIM_COMMITTED, authoritative_row=authoritative_row}
    return {kind=AUTHORITATIVE_WINNER, authoritative_row=authoritative_row}

reconcile_same_task_resume_cas_loss(validated_runtime_stop_event, lost_operation):
    quarantine_cas_lost(lost_operation)
    authoritative_row = lost_operation.authoritative_row from the transaction
        (otherwise read it exactly once; never poll)
    require the same run/task/snapshot/content/owner/lock and predecessor runtime
        event identity
    if authoritative_row exactly matches lost_operation.new_row's same-task
       attempt+1/invocation/token, state=CLAIMED, report_id=NONE,
       runtime_terminal_event_id=NONE, overlay=NONE, and the explicit reset/gate
       sentinels:
        return {kind=RESUME_COMMITTED, authoritative_row=authoritative_row}
    if authoritative_row exactly matches lost_operation.expected_row as the eligible
       PARTIAL row and its predecessor event/checkpoint/timing still match:
        retry = lost_operation.same_task_resume_once(authoritative_row)
        if retry.kind is COMMITTED:
            return {kind=RESUME_COMMITTED, authoritative_row=retry.authoritative_row}
        if retry.kind is CAS_LOST and
           retry.authoritative_row exactly matches lost_operation.new_row's claimed
           same-task identity:
            return {kind=RESUME_COMMITTED, authoritative_row=retry.authoritative_row}
        return {kind=AUTHORITATIVE_WINNER,
                authoritative_row=retry.authoritative_row}
    return {kind=AUTHORITATIVE_WINNER, authoritative_row=authoritative_row}

recover_or_block():
    if role is not reviewer:
        preserve the report and follow the role-specific resume/re-plan path; return and stop this invocation
    if current_task_row.replacement_index == 1 and
       current_task_row.state is BLOCKED and
       current_task_row.terminal_reason is REPLACEMENT_SPAWN_FAILED:
        record_reviewer_replacement_failure_at_parent(
            current_task_row, REPLACEMENT_SLOT_CLOSED
        )
        return REPLACEMENT_SLOT_CLOSED without another recovery or replacement
    if the triggering result is PARTIAL:
        validated_stop_event = validated_runtime_stop_event_for_partial(
            triggering result
        )
        if triggering result is a normal child PARTIAL report:
            partial_materialization = materialize_validated_partial_compare_and_set(
                triggering result, validated_stop_event
            )
            if partial_materialization.kind is CAS_LOST:
                replay_outcome = reconcile_runtime_event_cas_loss(
                    validated_stop_event, partial_materialization
                )
                replay_decision = consume_terminal_decision(replay_outcome)
                if replay_decision is not PARTIAL:
                    return after consuming the authoritative winning row
            else:
                require partial_materialization.kind is COMMITTED
                current_task_row = partial_materialization.authoritative_row
        if reviewer_resumable_partial_is_authorized(triggering result):
            resume_resumable_partial(triggering result); return
        require parent has independently verified this reviewer PARTIAL is
            non-resumable and `validated_stop_event` is the exact runtime-confirmed
            stop event retained in current_task_row.runtime_terminal_event_id
        terminal_materialization = materialize_confirmed_recovery_terminal(
            triggering result, validated_stop_event
        )
        if terminal_materialization.kind is CAS_LOST:
            replay_outcome = reconcile_runtime_event_cas_loss(
                validated_stop_event, terminal_materialization
            )
            replay_decision = consume_terminal_decision(replay_outcome)
            if replay_decision is not RECOVERY_REQUIRED:
                return after preserving the authoritative winning row
        else:
            require terminal_materialization.kind is COMMITTED
            current_task_row = terminal_materialization.authoritative_row
    else:
        require the triggering recovery event is either:
            (a) a parent-validated, runtime-confirmed non-resumable stop
                (runtime RUNTIME_INTERRUPTION_EVENT with RESUMABLE=no, or a validated
                terminal failure/cancellation with an independent runtime stop proof),
            or (b) a validated RUNTIME_TERMINAL_EVENT with STATUS=spawn_failed and
                CHILD_STARTED=no whose original-attempt
                materialize_spawn_failure_terminal CAS already succeeded
        an unverified child PARTIAL, child-authored RESUMABLE=no, or ordinary wait
            observation is not enough to authorize reviewer replacement
        require the corresponding full-row materialization has already succeeded and
            the current row is state=FAILED or CANCELLED with its runtime terminal_at,
            terminal_reason, exact invocation identity, retained owner, and review lock;
            for original spawn_failed use terminal_reason=SPAWN_FAILED and its
            independent runtime terminal-event ID. RECOVERY_REQUIRED is only the
            classifier disposition returned after that CAS. If the current row is a
            claimed replacement with `state=BLOCKED` and
            `terminal_reason=REPLACEMENT_SPAWN_FAILED`, return
            REPLACEMENT_SLOT_CLOSED and stop; do not call this recovery branch.
    if that non-resumable-stop proof is absent:
        record REVIEW_BLOCKED; retain the owner and review lock; stop without replacement
    if current_attempt_runtime_terminal_or_cancellation_is_confirmed and
       current task row records a late runtime stop confirmation:
        record BLOCKED with terminal_reason=STOP_CONFIRMED_ONLY; retain CANCEL_REQUESTED,
        artifacts, owner, and review lock;
        stop without replacement, takeover, unlock, or acceptance
    validated_terminal_or_cancellation_event = current attempt's CAS-validated runtime stop event
    predecessor_event_id = runtime_terminal_event_id(
        validated_terminal_or_cancellation_event
    )
    require predecessor_event_id is a nonempty runtime/parent-owned event identity;
        for RUNTIME_INTERRUPTION_EVENT use its runtime EVENT_ID, and for a validated
        terminal failure/cancellation use the independent runtime terminal-event ID;
        never derive it from child prose or an undefined local name
    replacement_stop_confirmed_at = the runtime-owned stop-confirmation sample from
        validated_terminal_or_cancellation_event (cancel_confirmed_at when present,
        otherwise terminal_at)
    if not finite_monotonic(replacement_stop_confirmed_at):
        record timing UNVERIFIED and REVIEW_BLOCKED; retain the owner and review lock; stop
    replacement_decision_latest_at = min(replacement_decision_deadline,
        replacement_stop_confirmed_at + review_replacement_decision_reserve_budget)
    replacement_decision_now = clock.monotonic() immediately before evaluating the
        replacement gate; replacement_decision_remaining =
        snapshot_deadline - replacement_decision_now
    if not finite_monotonic(replacement_decision_now) or
       not finite_nonnegative(replacement_decision_remaining) or
       not finite_monotonic(replacement_decision_latest_at) or
       replacement_decision_latest_at <= replacement_stop_confirmed_at:
        record timing UNVERIFIED and REVIEW_BLOCKED; retain the owner and review lock; stop
    safe_to_replace = (
        role is reviewer
        and replacement_index == 0
        and replacement_count < review_replacement_limit
        and current_attempt_runtime_terminal_or_cancellation_is_confirmed
    )
    if safe_to_replace and replacement_decision_now < replacement_decision_latest_at and
       replacement_decision_remaining >= replacement_gate:
        # The preceding sample is only a cheap eligibility precheck. Do not use it
        # for the claim: the full-row CAS below owns the authoritative second sample,
        # recheck, and decision fields in one transaction.
        replacement_attempt = attempt + 1
        replacement_invocation_id = reserve_unique_invocation_id()
        replacement_binding_token = reserve_unique_binding_token()
        replacement_claim = reservation for the full expected/current and new-row values below
        # The named arguments are only an expansion of these exact typed values; the
        # wrapper must construct FullTaskRow objects with keyset=
        # CANONICAL_TASK_ROW_FIELDS before calling the primitive. No alias, duplicate,
        # omitted, or implicitly inherited field is accepted.
        # This single full-row CAS archives the old attempt and atomically claims the
        # same task for its replacement. There is no unlocked or unowned handoff gap.
        # The primitive returns a typed outcome with its authoritative row, never a
        # truthy/falsey flag: {kind=COMMITTED, authoritative_row} or
        # {kind=CAS_LOST, authoritative_row, lost_operation}.
        replacement_slot_outcome = replacement_slot_compare_and_set(
               atomic_transaction=yes,
               decision_sample=clock.monotonic() inside this full-row CAS,
               decision_remaining= snapshot_deadline - decision_sample inside this
                   same transaction,
               atomically_recheck_decision_gate=yes,
               require decision_sample < replacement_decision_latest_at and
                   decision_remaining >= replacement_gate and
                   current_row.version/task/attempt/invocation/owner/lock still match,
               decision_sample_source=transaction_local_not_parent_precheck,
               expected_row_version=current_task_row.version,
               expected_task_id=task_id,
               expected_parent_task_id=parent_task_id,
               expected_run_id=run_id,
               expected_attempt=attempt,
               expected_invocation_id=invocation_id,
               expected_agent_id=current_task_row.agent_id,
               expected_agent_channel=current_task_row.agent_channel,
               expected_transport_invocation_association=
                   current_task_row.transport_invocation_association,
               expected_reserved_runtime_target=current_task_row.reserved_runtime_target,
               expected_runtime_terminal_event_id=current_task_row.runtime_terminal_event_id,
               expected_binding_token=current_task_row.binding_token,
               expected_role=stored_role,
               expected_clock_source=clock_source,
               expected_objective=objective,
               expected_depends_on=depends_on,
               expected_dependency_requirements=dependency_requirements,
               expected_read_scope=read_scope,
               expected_write_scope=write_scope,
               expected_impact_scope=impact_scope,
               expected_base_snapshot=base_snapshot,
               expected_base_content_identity=base_content_identity,
               expected_execution_mode=execution_mode,
               expected_isolation_mode=isolation_mode,
               expected_binding_mode=binding_mode,
               expected_focused_checks=focused_checks,
               expected_full_suite_owner=full_suite_owner,
               expected_resumable=current_task_row.resumable,
               expected_snapshot_id=snapshot_id,
               expected_content_identity=content_identity,
               expected_budget=current_task_row.budget,
               expected_deadline_at=current_task_row.deadline_at,
               expected_spawn_requested_at=current_task_row.spawn_requested_at,
               expected_spawn_confirmed_at=current_task_row.spawn_confirmed_at,
               expected_started_at=current_task_row.started_at,
               expected_binding_failed_at=current_task_row.binding_failed_at,
               expected_binding_failure_recovery_deadline_at=
                   current_task_row.binding_failure_recovery_deadline_at,
               expected_binding_failure_provenance=
                   current_task_row.binding_failure_provenance,
               expected_terminal_at=current_task_row.terminal_at,
               expected_prebinding_terminal_at=current_task_row.prebinding_terminal_at,
               expected_late_bind_at=current_task_row.late_bind_at,
               expected_cancel_requested_at=current_task_row.cancel_requested_at,
               expected_cancel_confirmed_at=current_task_row.cancel_confirmed_at,
               expected_budget_remaining=current_task_row.budget_remaining,
               expected_budget_remaining_at_spawn=current_task_row.budget_remaining_at_spawn,
               expected_budget_remaining_at_binding=current_task_row.budget_remaining_at_binding,
               expected_budget_consumed_at_terminal=current_task_row.budget_consumed_at_terminal,
               expected_terminal_reason=current_task_row.terminal_reason,
               expected_result_content_identity=current_task_row.result_content_identity,
               expected_interruption_event_id=current_task_row.interruption_event_id,
               expected_checkpoint_id=current_task_row.checkpoint_id,
               expected_checkpoint_content_identity=
                   current_task_row.checkpoint_content_identity,
               expected_artifact_access_proof_id=current_task_row.artifact_access_proof_id,
               expected_integrated_content_identity=current_task_row.integrated_content_identity,
               expected_quarantine_reason=current_task_row.quarantine_reason,
               expected_report_disposition=current_task_row.report_disposition,
               expected_report_id=current_task_row.report_id,
               expected_state=current_task_row.state,
               expected_overlay=current_task_row.overlay,
               expected_replacement_index=0,
               expected_stop_event=validated_terminal_or_cancellation_event,
               expected_replacement_count=replacement_count,
               expected_attention_required=current_task_row.attention_required,
               expected_replacement_decision_reserve_budget=review_replacement_decision_reserve_budget,
               expected_replacement_decision_deadline=replacement_decision_deadline,
               expected_replacement_stop_confirmed_at=UNSET,
               expected_replacement_decision_latest_at=UNSET,
               expected_spawn_reserve_budget=review_spawn_reserve_budget,
               expected_created_at=current_task_row.created_at,
               expected_updated_at=current_task_row.updated_at,
               expected_snapshot_budget_started_at=snapshot_budget_started_at,
               expected_snapshot_deadline_at=current_task_row.snapshot_deadline_at,
               expected_attempt_deadline_at=current_task_row.attempt_deadline_at,
               expected_recovery_deadline_at=current_task_row.recovery_deadline_at,
               expected_replacement_gate_digest=NONE,
               expected_replacement_of=current_task_row.replacement_of,
               expected_prebinding_timing_digest=current_task_row.prebinding_timing_digest,
               expected_replacement_decision_at=UNSET,
               expected_replacement_decision_remaining=UNSET,
               expected_replacement_gate_sample={
                   decision_at=atomic_transaction.decision_sample,
                   remaining_snapshot_budget=atomic_transaction.decision_remaining,
                   deadline=replacement_decision_deadline,
                   latest_at=replacement_decision_latest_at,
                   stop_confirmed_at=replacement_stop_confirmed_at,
                   decision_reserve_budget=review_replacement_decision_reserve_budget
               },
               new_replacement_count=replacement_count + 1,
               expected_owner=review_owner,
               expected_lock=review_lock,
               archive_attempt=attempt,
               new_task_id=task_id,
               new_parent_task_id=parent_task_id,
               new_attempt=replacement_attempt,
               new_invocation_id=replacement_invocation_id,
               new_binding_token=replacement_binding_token,
               new_run_id=run_id,
               new_role=stored_role,
               new_objective=objective,
               new_depends_on=depends_on,
               new_dependency_requirements=dependency_requirements,
               new_read_scope=read_scope,
               new_write_scope=write_scope,
               new_impact_scope=impact_scope,
               new_base_snapshot=base_snapshot,
               new_base_content_identity=base_content_identity,
               new_execution_mode=execution_mode,
               new_isolation_mode=isolation_mode,
               new_binding_mode=binding_mode,
               new_focused_checks=focused_checks,
               new_full_suite_owner=full_suite_owner,
               new_replacement_decision_reserve_budget=review_replacement_decision_reserve_budget,
               new_replacement_decision_deadline=replacement_decision_deadline,
               new_replacement_stop_confirmed_at=replacement_stop_confirmed_at,
               new_replacement_decision_latest_at=replacement_decision_latest_at,
               new_spawn_reserve_budget=review_spawn_reserve_budget,
               new_created_at=current_task_row.created_at,
               new_updated_at=atomic_transaction.decision_sample,
               new_agent_id=UNASSIGNED,
               new_agent_channel=UNASSIGNED,
               new_transport_invocation_association=UNASSIGNED,
               new_reserved_runtime_target=NONE,
               new_runtime_terminal_event_id=NONE,
               new_report_id=NONE,
               new_state=CLAIMED,
               new_overlay=NONE,
               new_replacement_index=1,
               new_clock_source=clock_source,
               new_snapshot_budget_started_at=snapshot_budget_started_at,
               new_spawn_requested_at=UNSET,
               new_spawn_confirmed_at=UNSET,
               new_started_at=UNKNOWN,
               new_binding_failed_at=UNSET,
               new_deadline_at=snapshot_deadline,
               new_terminal_at=UNSET,
               new_prebinding_terminal_at=UNSET,
               new_prebinding_timing_digest=UNSET,
               new_late_bind_at=UNSET,
               new_cancel_requested_at=UNSET,
               new_cancel_confirmed_at=UNSET,
               new_snapshot_id=snapshot_id,
               new_content_identity=content_identity,
               new_snapshot_deadline_at=snapshot_deadline,
               new_attempt_deadline_at=snapshot_deadline,
               new_recovery_deadline_at=snapshot_deadline,
               new_binding_failure_recovery_deadline_at=UNSET,
               new_binding_failure_provenance=NONE,
               new_replacement_gate_digest=UNSET,
               new_replacement_decision_at=atomic_transaction.decision_sample,
               new_replacement_decision_remaining=atomic_transaction.decision_remaining,
               new_budget={remaining_snapshot_budget=atomic_transaction.decision_remaining,
                   fresh_budget=no},
               new_budget_remaining=atomic_transaction.decision_remaining,
               new_budget_remaining_at_spawn=UNSET,
               new_budget_remaining_at_binding=UNSET,
               new_budget_consumed_at_terminal=UNKNOWN,
               new_terminal_reason=NONE,
               new_result_content_identity=NONE,
               new_interruption_event_id=NONE,
               new_checkpoint_id=NONE,
               new_checkpoint_content_identity=NONE,
               new_artifact_access_proof_id=NONE,
               new_integrated_content_identity=NONE,
               new_quarantine_reason=NONE,
               new_report_disposition=none,
               new_attention_required=NONE,
               new_resumable=current_task_row.resumable,
               new_replacement_of={task_id, attempt, invocation_id, predecessor_event_id},
               new_owner=review_owner,
               new_lock=review_lock
           )
        if replacement_slot_outcome.kind is CAS_LOST:
            quarantine_cas_lost(replacement_claim)
            replacement_slot_replay = reconcile_replacement_slot_cas_loss(
                replacement_claim, replacement_slot_outcome
            )
            if replacement_slot_replay.kind is not REPLACEMENT_CLAIM_COMMITTED:
                retain replacement_slot_replay.authoritative_row, its owner, and lock;
                    stop without recovery, spawn, or another replacement
            replacement_slot_outcome = {
                kind=COMMITTED,
                authoritative_row=replacement_slot_replay.authoritative_row
            }
        require replacement_slot_outcome.kind is COMMITTED
        current_task_row = replacement_slot_outcome.authoritative_row
        attempt = replacement_attempt
        invocation_id = replacement_invocation_id
        binding_token = replacement_binding_token
        replacement_index = 1
        # Continue only from the transaction's authoritative claimed row; never use a
        # locally synthesized replacement row after a CAS response or replay.
        replacement_attempt_deadline_at = snapshot_deadline
        replacement_spawn_transaction = one runtime-atomic pre_spawn boundary:
            immediately before the runtime invokes the replacement, sample
            replacement_spawn_requested_at = clock.monotonic() from the validated clock
            if not finite_monotonic(replacement_spawn_requested_at) or
               replacement_spawn_requested_at >= snapshot_deadline or
               replacement_spawn_requested_at >= current_task_row.replacement_decision_latest_at:
                fail closed with timing UNVERIFIED/REVIEW_BLOCKED before spawn
            budget_remaining_at_spawn = snapshot_deadline - replacement_spawn_requested_at
            if not finite_nonnegative(budget_remaining_at_spawn) or
               budget_remaining_at_spawn < replacement_gate:
                fail closed with REVIEW_BLOCKED before spawn
            atomically recheck the current replacement row/version, the still-UNSET gate,
            replacement_decision_at/remaining, owner/lock, decision deadline,
            reserved_runtime_target=NONE, and runtime_terminal_event_id=NONE; reserve
            the exact replacement runtime target, persist it together with
            replacement_spawn_requested_at and budget_remaining_at_spawn, then invoke the
            replacement using that target in the same runtime transaction. There is no
            parent operation or separately exposed timing-record/target CAS between the
            final sample, reservation, and spawn. Return the exact target and persist it
            as the current row's `reserved_runtime_target`.
        if the runtime cannot provide this sample/CAS/persist/spawn atomic boundary
           before invoking the replacement, or the pre_spawn transaction fails with a
           runtime proof that no child was created:
            require the failed pre_spawn transaction has atomically persisted a finite
            replacement_spawn_requested_at as the current row's spawn_requested_at,
            its exact budget_remaining_at_spawn, and the reserved target (NONE is
            permitted only when no target was allocated)
            if that persisted sample/target proof is absent:
                if current_task_row.reserved_runtime_target is NONE:
                    failure_record = record_unaddressable_spawn_compare_and_set(
                        current_task_row,
                        binding_failed_at=UNKNOWN,
                        binding_failure_recovery_deadline_at=UNVERIFIED,
                        cancel_requested_at=UNKNOWN
                    )
                else:
                    failure_record = record_binding_failure_cancel_requested_compare_and_set(
                        current_task_row,
                        binding_failed_at=UNKNOWN,
                        runtime_target=current_task_row.reserved_runtime_target
                    )
                if failure_record.kind is CAS_LOST:
                    consume the authoritative winner and return without a stale stop
                retain UNADDRESSABLE/CANCEL_REQUESTED/BLOCKED and the replacement slot
                    and lock; record REVIEW_BLOCKED and return without a guessed stop
            # The proof above also supplies the row's persisted spawn_requested_at;
            # only then may this closed no-child event be materialized.
            spawn_failure_event = parent/runtime-created closed RUNTIME_TERMINAL_EVENT(
                fresh EVENT_ID and independent RUNTIME_TERMINAL_EVENT_ID,
                exact run/task/attempt/invocation/token, current reserved target,
                STATUS=spawn_failed, CHILD_STARTED=no,
                AGENT_ID/CHANNEL/ASSOCIATION=UNASSIGNED,
                STOP_CONFIRMED=not_applicable, CANCEL_CONFIRMED_AT=UNSET,
                TERMINAL_AT=the validated spawn-boundary sample)
            spawn_failure_outcome =
                materialize_replacement_spawn_failure(spawn_failure_event)
            if spawn_failure_outcome.kind is CAS_LOST:
                replay_outcome = reconcile_runtime_event_cas_loss(
                    spawn_failure_event, spawn_failure_outcome
                )
                consume replay_outcome; retain its authoritative owner/lock and stop
            require spawn_failure_outcome.kind is COMMITTED with
                disposition=REPLACEMENT_SLOT_CLOSED
            current_task_row = spawn_failure_outcome.authoritative_row
            record_reviewer_replacement_failure_at_parent(
                current_task_row, REPLACEMENT_SLOT_CLOSED
            )
            stop without ordinary wait, recovery, another replacement, or a stop request
        if the pre_spawn transaction loses or returns after it may have invoked the
           replacement and no closed no-child proof exists:
            require the exact reserved target is already persisted; call
            record_binding_failure_cancel_requested_compare_and_set (or its one
            reconciliation path)
            with the runtime failure sample or UNKNOWN/UNVERIFIED, then invoke the one
            idempotent stop helper. Do not retry the spawn or treat the transaction
            failure as a terminal no-child result; continue only through the bounded
            SPAWN_UNCONFIRMED stop-confirmation path.
        the spawned replacement uses the new invocation/token, attempt_deadline = snapshot_deadline,
        agent_id=UNASSIGNED until runtime binding, and report_id=NONE
        pass a replacement TaskSpec with REPLACEMENT_OF=the archived original attempt,
        the same snapshot/content identity and fixed snapshot_deadline, replacement_index=1,
        and no fresh budget; late output from the archived invocation remains attempt-scoped
        quarantine
        if replacement_post_spawn_binding_failed and replacement_may_have_started:
            replacement_binding_failed_at = clock.monotonic() at the binding failure
            replacement_binding_failure_recovery_deadline_at = min(
                snapshot_deadline,
                replacement_binding_failed_at + review_recovery_grace_budget
            )
            if not finite_monotonic(replacement_binding_failed_at) or
               not finite_monotonic(replacement_binding_failure_recovery_deadline_at) or
               replacement_binding_failure_recovery_deadline_at != min(
                   snapshot_deadline,
                   replacement_binding_failed_at + review_recovery_grace_budget
               ) or
               replacement_binding_failure_recovery_deadline_at < replacement_binding_failed_at or
               replacement_binding_failure_recovery_deadline_at > snapshot_deadline:
                # The replacement may already be live even though its timing record is
                # unusable. First persist the exact target and the explicit
                # UNKNOWN/UNVERIFIED timing through the complete binding-failure CAS.
                invalid_binding_expected_row, invalid_binding_new_row =
                    replacement_binding_failure_timing_full_rows(
                        current_task_row=current_task_row,
                        reserved_runtime_target=
                            current_task_row.reserved_runtime_target,
                        binding_failed_at=UNKNOWN,
                        binding_failure_recovery_deadline_at=UNVERIFIED,
                        cancel_requested_at=
                            the validated cancellation sample or UNKNOWN,
                        updated_at=the same monotonic sample or current_task_row.updated_at
                    )
                invalid_binding_outcome =
                    replacement_binding_failure_timing_compare_and_set(
                        expected_row=invalid_binding_expected_row,
                        new_row=invalid_binding_new_row
                    )
                if invalid_binding_outcome.kind is CAS_LOST:
                    quarantine_cas_lost(current_task_row)
                    reconciliation_outcome = reconcile_replacement_post_spawn_cas_loss(
                        run_id, task_id, parent_task_id, attempt, invocation_id,
                        binding_token, the exact runtime-issued replacement agent,
                        the returned replacement agent channel,
                        the returned replacement one-to-one association, UNSET
                    )
                    return consume_replacement_post_spawn_reconciliation(
                        reconciliation_outcome
                    )
                current_task_row = invalid_binding_outcome.authoritative_row
                stop_outcome = stop_at_deadline_or_recovery_boundary()
                if stop_outcome.kind is AUTHORITATIVE_WINNER:
                    return AUTHORITATIVE_WINNER without recording a block on the stale
                        replacement row
                retain the persisted UNKNOWN/UNVERIFIED timing and record REVIEW_BLOCKED;
                    retain the replacement slot and lock; return REVIEW_BLOCKED
            binding_failure_expected_row, binding_failure_new_row =
                replacement_binding_failure_timing_full_rows(
                    current_task_row=current_task_row,
                    reserved_runtime_target=
                        current_task_row.reserved_runtime_target,
                    binding_failed_at=replacement_binding_failed_at,
                    binding_failure_recovery_deadline_at=
                        replacement_binding_failure_recovery_deadline_at,
                    cancel_requested_at=
                        the validated cancellation sample from this full-row transition,
                    updated_at=monotonic_now
                )
            binding_failure_outcome =
                replacement_binding_failure_timing_compare_and_set(
                    expected_row=binding_failure_expected_row,
                    new_row=binding_failure_new_row
                )
            if binding_failure_outcome.kind is CAS_LOST:
                quarantine_cas_lost(replacement_binding_failed_at)
                reconciliation_outcome = reconcile_replacement_post_spawn_cas_loss(
                    run_id, task_id, parent_task_id, attempt, invocation_id,
                    binding_token, the exact runtime-issued replacement agent,
                    the returned replacement agent channel,
                    the returned replacement one-to-one association, UNSET
                )
                return consume_replacement_post_spawn_reconciliation(
                    reconciliation_outcome
                )
            current_task_row = binding_failure_outcome.authoritative_row
            # This is also required when the timestamp was syntactically valid: the
            # replacement is now known to be unbound and must not remain live. The
            # complete timing/CANCEL_REQUESTED CAS is already finished. The canonical
            # stop helper owns the one idempotent close/interrupt/cancel request; only
            # after it returns may the wait-only confirmation helper run.
            stop_request_outcome = stop_at_deadline_or_recovery_boundary()
            if stop_request_outcome.kind is AUTHORITATIVE_WINNER:
                return AUTHORITATIVE_WINNER without entering ordinary wait/recovery or
                    creating another replacement
            if stop_request_outcome.kind is TARGET_UNAVAILABLE_UNVERIFIED:
                record REVIEW_BLOCKED; retain the replacement slot, owner, and lock;
                    return REVIEW_BLOCKED
            stop_outcome =
                await_recorded_binding_failure_stop_confirmation_once(
                    current_task_row=current_task_row,
                    fixed_deadline=replacement_binding_failure_recovery_deadline_at,
                    replacement_attempt=yes
                )
            if stop_outcome.kind is AUTHORITATIVE_WINNER:
                return AUTHORITATIVE_WINNER without entering ordinary wait/recovery or
                    creating another replacement
            if stop_outcome is not CONFIRMED:
                record REVIEW_BLOCKED; retain the replacement provenance, overlay,
                    slot, owner, and lock; quarantine late output and return REVIEW_BLOCKED
            # The helper has scanned all delivered output/events and routed runtime
            # events through their typed classifier. A replacement binding failure
            # never opens another recovery or replacement path after this point.
            retain the replacement lock and return REVIEW_BLOCKED without acceptance,
                recovery, another replacement, or unlock
        replacement_spawn_confirmed_at = the runtime post_spawn monotonic sample
        monotonic_now = clock.monotonic() immediately after receiving the replacement binding
        replacement_started_at = the real running-event sample or UNKNOWN
        replacement_binding_failed_at = the runtime failure sample or UNSET
        replacement_recovery_deadline_at = min(snapshot_deadline,
            replacement_attempt_deadline_at + review_recovery_grace_budget)
        replacement_binding_failure_recovery_deadline_at =
            min(snapshot_deadline,
                replacement_binding_failed_at + review_recovery_grace_budget)
            when replacement_binding_failed_at is finite, otherwise UNSET
        if not validate_deadline_inputs(
               clock_source=clock_source,
               spawn_requested_at=replacement_spawn_requested_at,
               spawn_confirmed_at=replacement_spawn_confirmed_at,
               started_at=replacement_started_at,
               monotonic_now=monotonic_now,
               binding_failed_at=replacement_binding_failed_at,
               binding_failure_recovery_deadline_at=
                   replacement_binding_failure_recovery_deadline_at,
               prebinding_terminal_at=UNSET,
               cancel_requested_at=UNSET,
               cancel_confirmed_at=UNSET,
               review_wait_budget=review_wait_budget,
               review_initial_budget=review_initial_budget,
               review_recovery_grace_budget=review_recovery_grace_budget,
               review_replacement_decision_reserve_budget=review_replacement_decision_reserve_budget,
               review_spawn_reserve_budget=review_spawn_reserve_budget,
               review_replacement_min_budget=review_replacement_min_budget,
               snapshot_budget_started_at=snapshot_budget_started_at,
               snapshot_deadline=snapshot_deadline,
               replacement_index=1,
               attempt_deadline=replacement_attempt_deadline_at,
               recovery_deadline=replacement_recovery_deadline_at,
               deadline_at=current_task_row.deadline_at,
               fixed_snapshot_deadline=snapshot_deadline,
               fixed_attempt_deadline=snapshot_deadline,
               fixed_recovery_deadline=snapshot_deadline
           ) or
           replacement_attempt_deadline_at != snapshot_deadline or
           replacement_recovery_deadline_at != replacement_attempt_deadline_at:
            # A post-bind timing failure is a live replacement failure, not a reason
            # to return before the exact invocation receives the bounded stop request.
            stop_outcome = stop_at_deadline_or_recovery_boundary()
            if stop_outcome.kind is AUTHORITATIVE_WINNER:
                return AUTHORITATIVE_WINNER without recording a block on the stale
                    replacement row
            record timing UNVERIFIED and REVIEW_BLOCKED; retain the replacement slot and
                lock; return REVIEW_BLOCKED
        budget_remaining_at_binding = remaining(snapshot_deadline) at replacement_spawn_confirmed_at
        if not finite_nonnegative(budget_remaining_at_binding) or
           budget_remaining_at_spawn < review_spawn_reserve_budget + review_replacement_min_budget or
           budget_remaining_at_binding > budget_remaining_at_spawn or
           budget_remaining_at_binding < review_replacement_min_budget or
           budget_remaining_at_spawn - budget_remaining_at_binding > review_spawn_reserve_budget:
            # Establish cancellation while the replacement row is still live. The
            # state/block record must not win before the cancellation CAS.
            # Always use the canonical idempotent stop helper. It samples a valid
            # cancellation time when possible, or records UNKNOWN and still performs
            # the overlay CAS when timing is unavailable; no short-circuit may bypass
            # the stop request.
            stop_outcome = stop_at_deadline_or_recovery_boundary()
            if stop_outcome.kind is AUTHORITATIVE_WINNER:
                return AUTHORITATIVE_WINNER without recording binding_overrun on the
                    stale replacement row
            the helper has issued the one idempotent close/interrupt/cancel request;
                that request is not stop confirmation
            record binding_overrun and REVIEW_BLOCKED; retain the replacement attempt and review lock
            overrun_wait = one bounded blocking wait until
                replacement_recovery_deadline_at, with no status polling
            while overrun_wait has no terminal event:
                if overrun_wait is an early wait observation:
                    continue the same logical wait with its remaining monotonic budget
                if overrun_wait is a child-authored report:
                    scan it completely and append only its redacted quarantine record;
                    retain CANCEL_REQUESTED and the lock; stop without acceptance,
                    recovery, replacement, or unlock
                if overrun_wait is a runtime-owned
                   RUNTIME_INTERRUPTION_EVENT or started RUNTIME_TERMINAL_EVENT:
                    validate it against the exact replacement target and fixed timing
                    decision = consume_terminal_decision(classify_terminal(overrun_wait))
                    # classify_terminal has consumed the authoritative row and any
                    # runtime stop/terminal materialization. An overrun never turns
                    # that event into review authority or opens another replacement.
                    retain current_task_row, its artifacts, owner, and review lock;
                    return REVIEW_BLOCKED without acceptance, recovery, replacement,
                        takeover, or unlock
                if overrun_wait is invalid, stale, late, or identity-mismatched:
                    quarantine only redacted metadata; retain CANCEL_REQUESTED and
                    the lock; stop
            if overrun_wait reaches replacement_recovery_deadline_at without runtime
               stop proof:
                retain CANCEL_REQUESTED and the review lock; record REVIEW_BLOCKED;
                never release, replace, or take over until a runtime terminal stop is
                confirmed
            return REVIEW_BLOCKED without acceptance or replacement
        otherwise:
            replacement_gate_digest = canonical_sha256_v1([
                ("snapshot_budget_started_at", snapshot_budget_started_at),
                ("snapshot_deadline_at", snapshot_deadline),
                ("replacement_decision_deadline", replacement_decision_deadline),
                ("replacement_stop_confirmed_at", replacement_stop_confirmed_at),
                ("replacement_decision_latest_at", replacement_decision_latest_at),
                ("replacement_decision_at", replacement_decision_at),
                ("replacement_decision_remaining", replacement_decision_remaining),
                ("review_replacement_decision_reserve_budget",
                    review_replacement_decision_reserve_budget),
                ("spawn_requested_at", replacement_spawn_requested_at),
                ("spawn_confirmed_at", replacement_spawn_confirmed_at),
                ("review_spawn_reserve_budget", review_spawn_reserve_budget),
                ("review_replacement_min_budget", review_replacement_min_budget),
                ("budget_remaining_at_spawn", budget_remaining_at_spawn),
                ("budget_remaining_at_binding", budget_remaining_at_binding)
            ])
            if current_task_row.report_id is not NONE:
                if not post_spawn_reconcile_after_late_bind_compare_and_set(
                       expected_row_version=current_task_row.version,
                       expected_run_id=run_id,
                       expected_task_id=task_id,
                       expected_parent_task_id=parent_task_id,
                       expected_attempt=attempt,
                       expected_invocation_id=invocation_id,
                       expected_agent_id=the already bound transport agent,
                       expected_agent_channel=the returned replacement runtime channel,
                       expected_transport_invocation_association=
                           the returned replacement one-to-one association,
                       expected_reserved_runtime_target=
                           current_task_row.reserved_runtime_target,
                       expected_runtime_terminal_event_id=
                           current_task_row.runtime_terminal_event_id,
                       expected_binding_token=binding_token,
                       expected_snapshot_id=snapshot_id,
                       expected_content_identity=content_identity,
                       expected_report_id=current_task_row.report_id,
                       expected_state=current_task_row.state,
                       expected_overlay=current_task_row.overlay,
                       expected_replacement_index=1,
                       expected_replacement_count=current_task_row.replacement_count,
                       expected_replacement_of=current_task_row.replacement_of,
                       expected_owner=current_task_row.owner,
                       expected_lock=current_task_row.lock,
                       expected_terminal_at=current_task_row.terminal_at,
                       expected_prebinding_terminal_at=current_task_row.prebinding_terminal_at,
                       expected_prebinding_timing_digest=current_task_row.prebinding_timing_digest,
                       expected_budget_consumed_at_terminal=
                           current_task_row.budget_consumed_at_terminal,
                       expected_interruption_event_id=current_task_row.interruption_event_id,
                       expected_checkpoint_id=current_task_row.checkpoint_id,
                       expected_checkpoint_content_identity=
                           current_task_row.checkpoint_content_identity,
                       expected_artifact_access_proof_id=
                           current_task_row.artifact_access_proof_id,
                       expected_replacement_decision_deadline=replacement_decision_deadline,
                       expected_binding_failure_provenance=
                           current_task_row.binding_failure_provenance,
                       expected_replacement_stop_confirmed_at=replacement_stop_confirmed_at,
                       expected_replacement_decision_latest_at=replacement_decision_latest_at,
                       expected_snapshot_budget_started_at=snapshot_budget_started_at,
                       expected_snapshot_deadline_at=current_task_row.snapshot_deadline_at,
                       expected_attempt_deadline_at=current_task_row.attempt_deadline_at,
                       expected_recovery_deadline_at=current_task_row.recovery_deadline_at,
                       expected_replacement_gate={
                           snapshot_budget_started_at, snapshot_deadline,
                           replacement_decision_deadline, replacement_stop_confirmed_at,
                           replacement_decision_latest_at,
                           replacement_decision_at, replacement_decision_remaining,
                           review_replacement_decision_reserve_budget,
                           replacement_spawn_requested_at,
                           replacement_spawn_confirmed_at, review_spawn_reserve_budget,
                           review_replacement_min_budget, budget_remaining_at_spawn,
                           budget_remaining_at_binding
                       },
                       expected_replacement_gate_digest=replacement_gate_digest,
                       revalidate_and_recompute_merged_timing_digest=yes,
                       new_merged_timing=the fully revalidated merged timing record,
                       new_prebinding_timing_digest=
                           canonical_timing_digest(new_merged_timing),
                       preserve_terminal_at=current_task_row.terminal_at,
                       preserve_report_and_gate=yes,
                       preserve_budget_consumed_at_terminal=
                           current_task_row.budget_consumed_at_terminal,
                       preserve_interruption_event_id=current_task_row.interruption_event_id,
                       preserve_checkpoint_id=current_task_row.checkpoint_id,
                       preserve_checkpoint_content_identity=
                           current_task_row.checkpoint_content_identity,
                       preserve_artifact_access_proof_id=
                           current_task_row.artifact_access_proof_id,
                       preserve_reserved_runtime_target=
                           current_task_row.reserved_runtime_target,
                       preserve_binding_failure_provenance=
                           current_task_row.binding_failure_provenance,
                       preserve_runtime_terminal_event_id=
                           current_task_row.runtime_terminal_event_id,
                       record_post_spawn_binding=yes
                   ):
                    quarantine_cas_lost(replacement_gate_digest)
                    reconciliation_outcome = reconcile_replacement_post_spawn_cas_loss(
                        run_id, task_id, parent_task_id, attempt, invocation_id,
                        binding_token, the exact runtime-issued replacement agent,
                        the returned replacement agent channel,
                        the returned replacement one-to-one association,
                        replacement_gate_digest
                    )
                    return consume_replacement_post_spawn_reconciliation(
                        reconciliation_outcome
                    )
            else:
                # This is a complete-row binding CAS, not a partial identity update:
                # every canonical field is an explicit expected value, and every
                # field not listed as a binding/timing write is explicitly preserved.
                # In particular, replacement provenance/counters and the fixed gate
                # cannot be inherited from an earlier attempt by omission.
                replacement_binding_expected_row, replacement_binding_new_row =
                    replacement_binding_full_rows(
                        current_task_row=current_task_row,
                        run_id=run_id,
                        task_id=task_id,
                        parent_task_id=parent_task_id,
                        attempt=attempt,
                        invocation_id=invocation_id,
                        binding_token=binding_token,
                        runtime_agent_id=the exact runtime-issued replacement agent,
                        runtime_channel=the returned replacement runtime channel,
                        transport_association=the returned replacement one-to-one
                            association,
                        role=stored_role,
                        clock_source=clock_source,
                        snapshot_id=snapshot_id,
                        content_identity=content_identity,
                        replacement_spawn_requested_at=replacement_spawn_requested_at,
                        replacement_spawn_confirmed_at=replacement_spawn_confirmed_at,
                        replacement_started_at=replacement_started_at,
                        replacement_recovery_deadline_at=replacement_recovery_deadline_at,
                        replacement_binding_failed_at=replacement_binding_failed_at,
                        snapshot_deadline=snapshot_deadline,
                        snapshot_budget_started_at=snapshot_budget_started_at,
                        budget_remaining_at_spawn=budget_remaining_at_spawn,
                        budget_remaining_at_binding=budget_remaining_at_binding,
                        replacement_gate_digest=replacement_gate_digest,
                        replacement_decision_deadline=replacement_decision_deadline,
                        review_owner=review_owner,
                        review_lock=review_lock
                    )
                replacement_binding_outcome =
                    replacement_binding_compare_and_set(
                        expected_row=replacement_binding_expected_row,
                        new_row=replacement_binding_new_row
                    )
                if replacement_binding_outcome.kind is CAS_LOST:
                    quarantine_cas_lost(replacement_gate_digest)
                    reconciliation_outcome = reconcile_replacement_post_spawn_cas_loss(
                        run_id, task_id, parent_task_id, attempt, invocation_id,
                        binding_token, the exact runtime-issued replacement agent,
                        the returned replacement agent channel,
                        the returned replacement one-to-one association,
                        replacement_gate_digest
                    )
                    return consume_replacement_post_spawn_reconciliation(
                        reconciliation_outcome
                    )
                require replacement_binding_outcome.kind is COMMITTED
                current_task_row = replacement_binding_outcome.authoritative_row
                agent_id = current_task_row.agent_id
                agent_channel = current_task_row.agent_channel
                transport_invocation_association =
                    current_task_row.transport_invocation_association
                reserved_runtime_target = current_task_row.reserved_runtime_target
                replacement_spawn_requested_at = current_task_row.spawn_requested_at
                replacement_spawn_confirmed_at = current_task_row.spawn_confirmed_at
                replacement_started_at = current_task_row.started_at
                replacement_binding_failed_at = current_task_row.binding_failed_at
                replacement_recovery_deadline_at =
                    current_task_row.recovery_deadline_at
                budget_remaining_at_spawn =
                    current_task_row.budget_remaining_at_spawn
                budget_remaining_at_binding =
                    current_task_row.budget_remaining_at_binding
                replacement_gate_digest = current_task_row.replacement_gate_digest
            if current_task_row.report_id is not NONE:
                late_bind_outcome = {
                    kind=LATE_BIND_ALREADY_COMMITTED,
                    report_id=current_task_row.report_id,
                    row=current_task_row
                }
                require verify_late_bound_report(late_bind_outcome) is LATE_BIND_VERIFIED
                stop without calling commit_terminal or entering ordinary wait; the
                replacement late-bind report is already a parent-owned terminal row
            run the same foreground wait/recovery sequence for that replacement,
            after rebinding all local timing names to the replacement row and using
            replacement_recovery_deadline_at; do not reuse the archived attempt's recovery deadline
    else:
        record REVIEW_BLOCKED and stop before acceptance

if original_post_spawn_returned and not post_spawn_binding_failed:
    post_spawn_result = post_spawn_reconcile_original_attempt_after_late_bind_compare_and_set(
        current_row_version, run/task/parent-task/attempt/invocation/token,
        returned_agent_channel, snapshot/content identity, owner, lock, and all
        runtime timing values
    )
    if post_spawn_result.status is CAS_LOST:
        # The helper has already quarantined the candidate and either preserved an
        # authoritative winner or claimed CANCEL_REQUESTED and issued the one idempotent
        # stop to the exact returned runtime target.
        reconciliation_outcome = post_spawn_result.reconciliation_outcome
        if reconciliation_outcome is a structured outcome with authoritative_row:
            current_task_row = reconciliation_outcome.authoritative_row
        if reconciliation_outcome is a structured outcome with disposition=PARTIAL:
            handoff_authoritative_partial_after_post_spawn(reconciliation_outcome)
        if reconciliation_outcome is a structured outcome and
           reconciliation_outcome.kind is ALREADY_BOUND:
            require current_task_row.agent_id is the exact returned runtime agent,
                current_task_row.agent_channel is the returned channel,
                current_task_row.transport_invocation_association is the returned
                one-to-one association, current_task_row.reserved_runtime_target is
                the exact returned target, and current_task_row.overlay is NONE
            rebind every local agent/channel/association/target/timing variable from
                current_task_row; continue to the ordinary wait only with this
                authoritative BOUND row, never with the stale pre-CAS row
        if reconciliation_outcome is a structured outcome with
           reconciliation_outcome.status is AUTHORITATIVE_WINNER and
           reconciliation_outcome.disposition is not PARTIAL:
            retain current_task_row, its owner/lock, and its authoritative terminal or
                cancellation state; stop before ordinary wait, acceptance, recovery,
                replacement, or another stop request
        reconciliation_kind = (reconciliation_outcome.kind
            if reconciliation_outcome is structured else reconciliation_outcome)
        if reconciliation_kind in {AUTHORITATIVE_WINNER, STOP_REQUESTED,
                                   STOP_ALREADY_REQUESTED, TARGET_UNAVAILABLE,
                                   TARGET_UNAVAILABLE_UNVERIFIED,
                                   BLOCKED_UNVERIFIED}:
            retain the authoritative owner/lock and stop before ordinary wait,
                acceptance, recovery, or replacement
    if post_spawn_result.status is AUTHORITATIVE_WINNER:
        current_task_row = post_spawn_result.row
        if post_spawn_result.disposition is PARTIAL:
            handoff_authoritative_partial_after_post_spawn(post_spawn_result)
        retain current_task_row, its owner/lock, and its authoritative disposition;
            stop before ordinary wait, acceptance, recovery, replacement, or another
            stop request
    if post_spawn_result.status is RECONCILED:
        require verify_late_bound_report(post_spawn_result) is LATE_BIND_VERIFIED
        stop without calling commit_terminal or entering ordinary wait; the
        late-bind-first report is already a parent-owned terminal row

while true:
    monotonic_now = clock.monotonic()
    if not finite_monotonic(monotonic_now):
        record timing=UNVERIFIED and REVIEW_BLOCKED for a reviewer (or the
            role-specific blocked state), retain the attempt/owner/lock, and stop
            without recovery, replacement, takeover, unlock, or another wait
    if monotonic_now >= attempt_deadline:
        break
    result = wait_agent(remaining(attempt_deadline))
    monotonic_now = clock.monotonic()
    if result is a wait observation:
        if not finite_monotonic(monotonic_now):
            record timing=UNVERIFIED and REVIEW_BLOCKED for a reviewer (or the
                role-specific blocked state), retain the attempt/owner/lock, and stop
                before recovery or another wait
        if monotonic_now < attempt_deadline:
            continue the same logical wait
        break to the single bounded recovery path
    result = validate_report_event(result)
    if result is QUARANTINED:
        retain the attempt/lock; stop without recovery or replacement
    current_task_row = read the authoritative current row once after validation
    if current_task_row.overlay is CANCEL_REQUESTED and
       result.kind not in {RUNTIME_INTERRUPTION_EVENT, RUNTIME_TERMINAL_EVENT}:
        quarantine_cancellation_race_event(result)
        retain CANCEL_REQUESTED and the owner/lock; stop without input, recovery,
            replacement, or acceptance
    if result is NEEDS_INPUT:
        validate the bounded request
        if it is a material user decision:
            decision_ticket = record_user_decision(result)
            if decision_ticket in {CAS_LOST, DECISION_EXPIRED}:
                stop without recovery or replacement
            return decision_ticket to the user and stop before acceptance
        if the invocation is not accepting input:
            preserve the attention state; record REVIEW_BLOCKED only when role is reviewer,
            otherwise record the role-specific NEEDS_INPUT/BLOCKED state; stop before acceptance
        if send_bounded_input(result, answer) in {CAS_LOST, INPUT_EXPIRED}:
            stop without recovery or replacement
        continue the same logical wait
    if result is NEEDS_USER_DECISION:
        validate the bounded decision
        decision_ticket = record_user_decision(result)
        if decision_ticket in {CAS_LOST, DECISION_EXPIRED}:
            stop without recovery or replacement
        return decision_ticket to the user/planning gate and stop before acceptance
    if result is BLOCKED:
        preserve the blocker, record BLOCKED, and stop for a new bounded plan/input
    if result is PARTIAL:
        preserve artifacts and identity; do not treat it as CLEAN, FAILED, or timeout
        if role is reviewer:
            if reviewer_resumable_partial_is_authorized(result):
                resume_resumable_partial(result)
            else:
                recover_or_block()  # replacement is only for non-resumable recovery
        else:
            resume/re-plan according to RESUMABLE and role contract
        stop this invocation
    if result is terminal:
        decision = consume_terminal_decision(classify_terminal(result))
        if decision is LATE_BIND_VERIFIED:
            # The late-bind CAS already created and atomically completed the
            # parent-owned report. Verify it once, then stop; never submit a second
            # terminal CAS or re-enter the ordinary wait.
            stop
        if decision is STOP_CONFIRMED_ONLY:
            require current_task_row is the authoritative row returned by the stop
                materializer; consume its STOP_CONFIRMED_ONLY disposition without a
                second BLOCKED CAS or state mutation; retain
                CANCEL_REQUESTED, the attempt artifact, and the review lock
            stop without recovery, replacement, takeover, unlock, or acceptance
        if decision is ACCEPTABLE:
            terminal_commit = commit_terminal(result)
            if terminal_commit.kind is CAS_LOST:
                consume_terminal_commit_cas_loss(terminal_commit)
                stop without recovery or replacement
            require terminal_commit.kind is COMMITTED
            current_task_row = terminal_commit.authoritative_row
            stop
        if decision is QUARANTINED:
            quarantine(result); retain the attempt/lock; stop without recovery or replacement
        if decision is USER_DECISION:
            decision_ticket = record_user_decision(result)
            if decision_ticket in {CAS_LOST, DECISION_EXPIRED}:
                stop without recovery or replacement
            return decision_ticket to the user/planning gate and stop before acceptance
        if decision is BLOCKED:
            preserve(result), record BLOCKED, and stop for a new bounded plan/input
        if decision is NEEDS_INPUT:
            validate the bounded request
            if it is a material user decision:
                decision_ticket = record_user_decision(result)
                if decision_ticket in {CAS_LOST, DECISION_EXPIRED}:
                    stop without recovery or replacement
                return decision_ticket to the user/planning gate and stop before acceptance
            if the invocation is accepting input:
                if send_bounded_input(result, answer) in {CAS_LOST, INPUT_EXPIRED}:
                    stop without recovery or replacement
                continue its wait
            else:
                preserve the attention state; record REVIEW_BLOCKED only when role is reviewer,
                otherwise record the role-specific NEEDS_INPUT/BLOCKED state; stop before acceptance
        if decision is PARTIAL:
            preserve artifacts and identity
            if role is reviewer and reviewer_resumable_partial_is_authorized(result):
                resume_resumable_partial(result)
            elif role is reviewer:
                recover_or_block()  # replacement is only for non-resumable recovery
            else:
                resume/re-plan according to RESUMABLE and role contract
            stop
        if decision is REPLACEMENT_SLOT_CLOSED:
            preserve the already materialized BLOCKED replacement row and stop without
                recover_or_block, another replacement, or unlock
        if decision is RECOVERY_REQUIRED:
            recover_or_block()
        stop

recovery_stop_outcome = stop_at_deadline_or_recovery_boundary()
if recovery_stop_outcome.kind is AUTHORITATIVE_WINNER:
    current_task_row = recovery_stop_outcome.authoritative_row
    stop without recording state or sending a stop from the stale recovery row
if recovery_stop_outcome.kind is MATERIALIZED:
    current_task_row = recovery_stop_outcome.authoritative_row
    if recovery_stop_outcome.disposition is STOP_CONFIRMED_ONLY:
        retain CANCEL_REQUESTED, the authoritative row, artifacts, owner, and lock;
        stop without a second block/CAS, recovery, replacement, or acceptance
    if recovery_stop_outcome.disposition is REPLACEMENT_SLOT_CLOSED:
        record_reviewer_replacement_failure_at_parent(
            current_task_row, REPLACEMENT_SLOT_CLOSED
        )
        stop without recover_or_block, another replacement, or unlock
    require recovery_stop_outcome.disposition is RECOVERY_REQUIRED
    recover_or_block(triggering_result=recovery_stop_outcome.triggering_event)
    stop without entering a second stop/CAS or recovery wait
if recovery_stop_outcome.kind in {TARGET_UNAVAILABLE_UNVERIFIED,
                                  BLOCKED_UNVERIFIED}:
    current_task_row = recovery_stop_outcome.authoritative_row
    retain the block/lock and stop before acceptance, recovery, replacement, or wait;
        do not record a second block transition from the same authoritative row
monotonic_now = clock.monotonic()
if not finite_monotonic(recovery_deadline) or
   not finite_monotonic(monotonic_now) or monotonic_now >= recovery_deadline:
    recovery_state = unknown
else:
    recovery_state = one bounded status check(timeout_ms=remaining(recovery_deadline))
    while recovery_state is a wait observation, timed out without a terminal state, or
          has no terminal/attention state:
        monotonic_now = clock.monotonic()
        if not finite_monotonic(monotonic_now) or monotonic_now >= recovery_deadline:
            recovery_state = unknown
            break
        # This is a continuation of the same bounded recovery wait, not a status poll.
        # An early wrapper/transport observation must not consume the remaining
        # cancellation handshake or create REVIEW_BLOCKED prematurely.
        recovery_state = wait_agent(remaining(recovery_deadline))
if recovery_state is a child report/event:
    recovery_state = validate_report_event(recovery_state)
    if recovery_state is QUARANTINED:
        retain the attempt/lock; stop without recovery or replacement
    current_task_row = read the authoritative current row once after validation
    if current_task_row.overlay is CANCEL_REQUESTED and
       recovery_state.kind not in {RUNTIME_INTERRUPTION_EVENT, RUNTIME_TERMINAL_EVENT}:
        quarantine_cancellation_race_event(recovery_state)
        retain CANCEL_REQUESTED and the owner/lock; stop without input, recovery,
            replacement, or acceptance
if recovery_state is NEEDS_INPUT:
    validate the bounded request
    if it is a material user decision:
        decision_ticket = record_user_decision(recovery_state)
        if decision_ticket in {CAS_LOST, DECISION_EXPIRED}:
            stop without recovery or replacement
        return decision_ticket to the user/planning gate; retain ownership/lock and keep review acceptance pending
        stop before acceptance
    if the invocation is not accepting input:
        preserve the attention state; record REVIEW_BLOCKED only when role is reviewer,
        otherwise record the role-specific NEEDS_INPUT/BLOCKED state; stop before acceptance
    if send_bounded_input(recovery_state, answer) in {CAS_LOST, INPUT_EXPIRED}:
        stop without recovery or replacement
    normalize recovery_state to live
if recovery_state is NEEDS_USER_DECISION:
    validate the bounded decision
    decision_ticket = record_user_decision(recovery_state)
    if decision_ticket in {CAS_LOST, DECISION_EXPIRED}:
        stop without recovery or replacement
    return decision_ticket to the user/planning gate; retain ownership/lock and keep review acceptance pending
    stop before acceptance
if recovery_state is BLOCKED:
    preserve the blocker, record BLOCKED, and stop for a new bounded plan/input
if recovery_state is live:
    require recovery_stop_outcome.kind in {STOP_REQUESTED, STOP_ALREADY_REQUESTED}
    # The canonical stop was already persisted and issued at entry to this recovery
    # window. This branch only consumes its bounded confirmation result; it must not
    # perform a second cancellation CAS or stop request.
    consume one runtime terminal result or confirmed-cancellation event until the fixed
    recovery_deadline, without status polling or extending any budget
    if the cancellation wait returns only an observation while
       clock.monotonic() < recovery_deadline:
        continue the same logical bounded wait with remaining(recovery_deadline);
        do not mark unknown or REVIEW_BLOCKED yet
    if the cancellation wait reaches its deadline without confirmation:
        treat it as unknown; do not send input, resume, replace, take over, or unlock
        stop before processing any child-authored state
    if result is a runtime-owned RUNTIME_INTERRUPTION_EVENT or
       RUNTIME_TERMINAL_EVENT:
        # Runtime events are never cancellation-race child output. They must be
        # classified even with CANCEL_REQUESTED present; a valid event has already
        # materialized the authoritative row before this branch continues.
        decision = consume_terminal_decision(classify_terminal(result))
        if decision is RECOVERY_REQUIRED:
            recover_or_block()
        if decision is PARTIAL:
            if reviewer_resumable_partial_is_authorized(result):
                resume_resumable_partial(result)
            else:
                record REVIEW_BLOCKED; retain CANCEL_REQUESTED and the lock
        if decision is STOP_CONFIRMED_ONLY:
            retain CANCEL_REQUESTED and the authoritative row/lock; stop
        if decision is REPLACEMENT_SLOT_CLOSED:
            record_reviewer_replacement_failure_at_parent(
                current_task_row, REPLACEMENT_SLOT_CLOSED
            )
            retain the closed replacement row and lock; stop
        if decision in {LATE_BIND_VERIFIED, QUARANTINED, BLOCKED}:
            retain the authoritative row and lock; stop
        stop without treating the runtime event as a child report
    if NEEDS_INPUT arrives as result:
        validate the bounded request
        quarantine_cancellation_race_event(result)
        retain CANCEL_REQUESTED and the lock; do not send input or change the task state
        stop before acceptance or recovery
    if NEEDS_USER_DECISION arrives as result:
        validate the bounded decision
        quarantine_cancellation_race_event(result)
        return it as non-authorizing context for a new task after the old stop is confirmed
        stop before acceptance
    if BLOCKED arrives:
        quarantine_cancellation_race_event(result)
        retain CANCEL_REQUESTED and the lock; stop without recovery or replacement
    if PARTIAL arrives:
        if result.kind is RUNTIME_INTERRUPTION_EVENT and
           reviewer_resumable_partial_is_authorized(result):
            decision = consume_terminal_decision(classify_terminal(result))
            if decision is PARTIAL:
                resume_resumable_partial(result)
            else:
                quarantine_cancellation_race_event(result)
        else:
            quarantine_cancellation_race_event(result)
        retain CANCEL_REQUESTED and the lock unless the explicit resumable
            PARTIAL → CLAIMED CAS above won; stop without replacement
    if a terminal result arrives:
        decision = consume_terminal_decision(classify_terminal(result))
        if decision is LATE_BIND_VERIFIED:
            stop without calling commit_terminal; the late-bind row is already terminal
        if decision is STOP_CONFIRMED_ONLY:
            require current_task_row is the authoritative row returned by the stop
                materializer; consume its STOP_CONFIRMED_ONLY disposition without a
                second BLOCKED CAS or state mutation; retain
                CANCEL_REQUESTED, the attempt artifact, and the review lock
            stop without recovery, replacement, takeover, unlock, or acceptance
        if decision is ACCEPTABLE:
            terminal_commit = commit_terminal(result)
            if terminal_commit.kind is CAS_LOST:
                consume_terminal_commit_cas_loss(terminal_commit)
                stop without recovery or replacement
            require terminal_commit.kind is COMMITTED
            current_task_row = terminal_commit.authoritative_row
            stop
        if decision is QUARANTINED:
            quarantine(result); retain the attempt/lock; stop without recovery or replacement
        if decision is USER_DECISION:
            quarantine_cancellation_race_event(result)
            return the decision as non-authorizing context for a new task after the old stop is confirmed
            stop before acceptance
        if decision is BLOCKED:
            quarantine_cancellation_race_event(result)
            retain CANCEL_REQUESTED and the lock; stop without recovery or replacement
        if decision is PARTIAL:
            if result.kind is RUNTIME_INTERRUPTION_EVENT and
               reviewer_resumable_partial_is_authorized(result):
                resume_resumable_partial(result)
            else:
                quarantine_cancellation_race_event(result)
            retain CANCEL_REQUESTED and the lock unless the explicit resumable
                PARTIAL → CLAIMED CAS above won; stop without replacement
        if decision is NEEDS_INPUT:
            validate the bounded request
            quarantine_cancellation_race_event(result)
            retain CANCEL_REQUESTED and the lock; do not send input or change the task state
            stop before acceptance or recovery
        if decision is REPLACEMENT_SLOT_CLOSED:
            record_reviewer_replacement_failure_at_parent(
                current_task_row, REPLACEMENT_SLOT_CLOSED
            )
            preserve the already materialized BLOCKED replacement row and stop without
                recover_or_block, another replacement, or unlock
        if decision is RECOVERY_REQUIRED:
            recover_or_block()
        stop
    if cancellation is confirmed:
        recover_or_block()
    else:
        if role is reviewer:
            record REVIEW_BLOCKED
        else:
            record the role-specific CANCELLED/FAILED recovery state
        retain CANCEL_REQUESTED and every affected lock; do not release, replace, or take over until a runtime terminal stop is confirmed
        stop before acceptance
    stop
if recovery_state is terminal:
    decision = consume_terminal_decision(classify_terminal(recovery_state))
    if decision is LATE_BIND_VERIFIED:
        stop without calling commit_terminal; the late-bind row is already terminal
    if decision is STOP_CONFIRMED_ONLY:
        require current_task_row is the authoritative row returned by the stop
            materializer; consume its STOP_CONFIRMED_ONLY disposition without a
            second BLOCKED CAS or state mutation; retain CANCEL_REQUESTED,
            the attempt artifact, and the review lock
        stop without recovery, replacement, takeover, unlock, or acceptance
    if decision is ACCEPTABLE:
        terminal_commit = commit_terminal(recovery_state)
        if terminal_commit.kind is CAS_LOST:
            consume_terminal_commit_cas_loss(terminal_commit)
            stop without recovery or replacement
        require terminal_commit.kind is COMMITTED
        current_task_row = terminal_commit.authoritative_row
        stop
    if decision is QUARANTINED:
        quarantine(recovery_state); retain the attempt/lock; stop without recovery or replacement
    if decision is USER_DECISION:
        decision_ticket = record_user_decision(recovery_state)
        if decision_ticket in {CAS_LOST, DECISION_EXPIRED}:
            stop without recovery or replacement
        return decision_ticket to the user/planning gate; retain ownership/lock and keep review acceptance pending
        stop before acceptance
    if decision is BLOCKED:
        preserve(recovery_state), record BLOCKED, and stop for a new bounded plan/input
    if decision is NEEDS_INPUT:
        validate the bounded request
        if it is a material user decision:
            decision_ticket = record_user_decision(recovery_state)
            if decision_ticket in {CAS_LOST, DECISION_EXPIRED}:
                stop without recovery or replacement
            return decision_ticket to the user/planning gate; retain ownership/lock and keep review acceptance pending
        if the invocation is accepting input:
            if send_bounded_input(recovery_state, answer) in {CAS_LOST, INPUT_EXPIRED}:
                stop without recovery or replacement
        else:
            preserve the attention state; record REVIEW_BLOCKED only when role is reviewer,
            otherwise record the role-specific NEEDS_INPUT/BLOCKED state
        stop before acceptance
    if decision is PARTIAL:
        preserve artifacts and identity
        if role is reviewer and reviewer_resumable_partial_is_authorized(recovery_state):
            resume_resumable_partial(recovery_state)
        elif role is reviewer:
            recover_or_block()  # replacement is only for non-resumable recovery
        else:
            resume/re-plan according to RESUMABLE and role contract
        stop
    if decision is REPLACEMENT_SLOT_CLOSED:
        record_reviewer_replacement_failure_at_parent(
            current_task_row, REPLACEMENT_SLOT_CLOSED
        )
        preserve the already materialized BLOCKED replacement row and stop without
            recover_or_block, another replacement, or unlock
    if decision is RECOVERY_REQUIRED:
        recover_or_block()
    stop
if recovery_state is PARTIAL:
    preserve artifacts and identity
    if role is reviewer and reviewer_resumable_partial_is_authorized(recovery_state):
        resume_resumable_partial(recovery_state)
    elif role is reviewer:
        recover_or_block()  # replacement is only for non-resumable recovery
    else:
        resume/re-plan according to RESUMABLE and role contract
    stop this invocation
if recovery_state is unknown:
    require recovery_stop_outcome.kind in {STOP_REQUESTED, STOP_ALREADY_REQUESTED,
                                           BLOCKED_UNVERIFIED}
    if role is reviewer:
        record REVIEW_BLOCKED while retaining CANCEL_REQUESTED and the review lock
    else:
        record the role-specific FAILED/BLOCKED recovery state while retaining the lock
    stop before acceptance
```

The loop above is one logical wait, not a polling loop: each continuation must use the original attempt deadline and must not inspect progress. Every terminal result, including one received after `CANCEL_REQUESTED`, goes through the runtime-provenance, snapshot-identity, semantic-closure, terminal-timing, and compare-and-set classifier; only a valid, on-time `CLEAN`/`FINDINGS` with `overlay=NONE` stops normally. A malformed, stale, late, provenance-mismatched, semantically contradictory, or CAS-lost result is quarantined and stops without recovery or replacement; the current winning row, owner, lock, and dependency result remain authoritative. After `CANCEL_REQUESTED`, child role results are cancellation-race context only and cannot be accepted or trigger recovery; an on-time runtime-confirmed stop may enter the normal recovery path, while a stop after the applicable fixed recovery/binding-failure deadline is `STOP_CONFIRMED_ONLY`. A `RUNTIME_INTERRUPTION_EVENT` or started `RUNTIME_TERMINAL_EVENT` uses its dedicated runtime-event validator; a non-resumable stop may use `ARTIFACT_ACCESS_PROOF=NONE`. `NEEDS_INPUT` and `PARTIAL` are explicit coordination states, not timeout observations. The cancellation handshake is bounded by `recovery_deadline` and consumes only its preallocated `review_recovery_grace_budget`; after `CANCEL_REQUESTED`, late attention never changes state or sends input. The replacement uses only the remaining portion of the same snapshot budget: the parent-side decision must complete by `replacement_decision_deadline`, the pre-spawn gate must cover `review_spawn_reserve_budget` plus at least `review_replacement_min_budget`, and the post-bind effective remainder must still meet `review_replacement_min_budget`. Its `attempt_deadline` is the fixed `snapshot_deadline`, never `spawn_time + fresh_budget`, and its task-row/attempt/owner handoff is one full-row CAS before spawn.

### Change-impact and validation semantics

For any delegated planner, implementer, reviewer, or verifier, start with the declared `IMPACT_SCOPE`: changed paths, direct callers/consumers, and directly mapped tests or configuration. Use targeted search to find those relationships, then stop. Do not read, audit, lint, type-check, build, or test unrelated modules merely for completeness. Expand the scope only when a concrete dependency, acceptance criterion, or reproduced failure points outside it, and record the reason.

Interpret `FOCUSED_CHECKS` as the narrowest useful validation for that assignment: a changed-file test, a directly mapped test, a targeted type check, a local lint rule, or another cheap check covering the impact scope. Do not ask a child to run repository-wide suites or heavyweight validation such as `npm test`, `npm run verify`, or a full production build. Do not repeat a passing focused check unless the relevant files changed, its result is untrusted, or it is needed to investigate a concrete failure. A child may exceed focused checks only when the main agent explicitly assigns a specific diagnostic, and that exception must not become repository-wide acceptance.

The main agent owns the repository’s complete prescribed validation. Defer it until the final accepted snapshot, run it as the last validation action, and run it once whenever practical. A delegated checkpoint’s focused checks inform integration but never substitute for that final result.
