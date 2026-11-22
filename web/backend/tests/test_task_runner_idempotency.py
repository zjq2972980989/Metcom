"""Task runner idempotency and atomic-claim tests."""

from __future__ import annotations

import threading
import time

import pytest

from models.schemas import TaskStatus, TaskType
from services.task_service import TaskService
from services.task_store import TaskStore, TaskStoreError
from tasks import runner
from tasks.handlers import TaskExecutionError


@pytest.fixture
def service(tmp_path) -> TaskService:
    tasks_dir = tmp_path / "tasks"
    return TaskService(TaskStore(tasks_dir / "tasks.json", tasks_dir / "results"))


def test_pending_task_runs_once_and_duplicate_call_preserves_result(
    monkeypatch, service: TaskService
) -> None:
    calls: list[str] = []

    def handler(task, report):
        calls.append(task.id)
        assert task.status == TaskStatus.RUNNING
        report(50, "half")
        return {"value": 7}

    monkeypatch.setattr(runner, "get_task_service", lambda: service)
    monkeypatch.setitem(runner.HANDLERS, TaskType.CALCULATION, handler)

    task = service.create_task(name="once", type=TaskType.CALCULATION)
    runner.run_task(task.id)
    first = service.get_task(task.id)
    assert first.status == TaskStatus.SUCCESS
    assert first.result == {"value": 7}
    assert calls == [task.id]

    runner.run_task(task.id)
    second = service.get_task(task.id)
    assert calls == [task.id]
    assert second.status == TaskStatus.SUCCESS
    assert second.result == first.result
    assert second.started_at == first.started_at
    assert second.finished_at == first.finished_at


def test_running_task_duplicate_call_is_ignored(monkeypatch, service: TaskService) -> None:
    calls: list[str] = []

    def handler(task, report):
        calls.append(task.id)
        return {"value": 1}

    monkeypatch.setattr(runner, "get_task_service", lambda: service)
    monkeypatch.setitem(runner.HANDLERS, TaskType.CALCULATION, handler)

    task = service.create_task(name="running", type=TaskType.CALCULATION)
    running = service.mark_running(task.id)

    runner.run_task(task.id)

    after = service.get_task(task.id)
    assert calls == []
    assert after.status == TaskStatus.RUNNING
    assert after.started_at == running.started_at
    assert after.result == {}


def test_failed_task_duplicate_call_preserves_error(monkeypatch, service: TaskService) -> None:
    calls: list[str] = []

    def handler(task, report):
        calls.append(task.id)
        raise TaskExecutionError("deterministic failure")

    monkeypatch.setattr(runner, "get_task_service", lambda: service)
    monkeypatch.setitem(runner.HANDLERS, TaskType.CALCULATION, handler)

    task = service.create_task(name="failed", type=TaskType.CALCULATION)
    runner.run_task(task.id)
    first = service.get_task(task.id)
    assert first.status == TaskStatus.FAILED
    assert first.error == "deterministic failure"
    assert calls == [task.id]

    runner.run_task(task.id)
    second = service.get_task(task.id)
    assert calls == [task.id]
    assert second.status == TaskStatus.FAILED
    assert second.error == first.error
    assert second.finished_at == first.finished_at


def test_concurrent_claims_allow_exactly_one_handler_execution(
    monkeypatch, service: TaskService
) -> None:
    barrier = threading.Barrier(3)
    entered = threading.Event()
    release = threading.Event()
    calls = 0
    calls_lock = threading.Lock()

    def handler(task, report):
        nonlocal calls
        with calls_lock:
            calls += 1
        entered.set()
        if not release.wait(timeout=10):
            raise AssertionError("handler was not released")
        return {"winner": True}

    monkeypatch.setattr(runner, "get_task_service", lambda: service)
    monkeypatch.setitem(runner.HANDLERS, TaskType.CALCULATION, handler)

    task = service.create_task(name="concurrent", type=TaskType.CALCULATION)
    done = [threading.Event(), threading.Event()]

    def worker(index: int) -> None:
        barrier.wait(timeout=5)
        runner.run_task(task.id)
        done[index].set()

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait(timeout=5)

    assert entered.wait(timeout=5)
    deadline = time.monotonic() + 5
    while not any(event.is_set() for event in done) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert any(event.is_set() for event in done)
    with calls_lock:
        assert calls == 1

    release.set()
    for thread in threads:
        thread.join(timeout=5)
        assert not thread.is_alive()

    finished = service.get_task(task.id)
    assert finished.status == TaskStatus.SUCCESS
    assert finished.result == {"winner": True}


def test_claim_write_failure_does_not_run_handler(monkeypatch, service: TaskService) -> None:
    calls: list[str] = []

    def handler(task, report):
        calls.append(task.id)
        return {"should_not_run": True}

    monkeypatch.setattr(runner, "get_task_service", lambda: service)
    monkeypatch.setitem(runner.HANDLERS, TaskType.CALCULATION, handler)
    task = service.create_task(name="write-failure", type=TaskType.CALCULATION)

    def fail_write(_tasks):
        raise TaskStoreError("simulated write failure")

    monkeypatch.setattr(service._store, "_write_unlocked", fail_write)

    runner.run_task(task.id)

    persisted = service.get_task(task.id)
    assert calls == []
    assert persisted.status == TaskStatus.PENDING
    assert persisted.result == {}
