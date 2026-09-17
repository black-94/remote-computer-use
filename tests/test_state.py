from __future__ import annotations

from remote_computer_use.state import CheckResult, EntityState, Status


def result(status: Status, *, required: bool = True, reason: str | None = None) -> CheckResult:
    return CheckResult(
        name="check",
        type="ssh",
        required=required,
        status=status,
        failure_reason=reason,
    )


def test_required_failure_debounces_then_recovers() -> None:
    state = EntityState()
    state.record([result(Status.HEALTHY)], 3)
    assert state.status == Status.HEALTHY
    assert state.available

    state.record([result(Status.UNHEALTHY, reason="CHECK_FAILED")], 3)
    assert state.status == Status.DEGRADED
    assert state.available
    state.record([result(Status.UNHEALTHY, reason="CHECK_FAILED")], 3)
    state.record([result(Status.UNHEALTHY, reason="CHECK_FAILED")], 3)
    assert state.status == Status.UNHEALTHY
    assert not state.available

    state.record([result(Status.HEALTHY)], 3)
    assert state.status == Status.HEALTHY
    assert state.consecutive_failures == 0


def test_optional_failure_is_degraded_but_available() -> None:
    state = EntityState()
    state.record(
        [
            result(Status.HEALTHY),
            result(Status.UNHEALTHY, required=False, reason="PROBE_TIMEOUT"),
        ],
        3,
    )
    assert state.status == Status.DEGRADED
    assert state.available
    assert state.consecutive_failures == 0

