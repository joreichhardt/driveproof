from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, MutableMapping

import drive_modes

Job = Any


@dataclass(frozen=True)
class JobRunnerDeps:
    jobs: MutableMapping[str, Job]
    jobs_lock: Any
    get_job: Callable[[str], Job | None]
    save_job: Callable[[Job], None]
    get_disk: Callable[[str], dict[str, Any]]
    get_smart_data: Callable[[str], dict[str, Any]]
    compute_health: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
    execute_job_mode: Callable[[Job, dict[str, Any], str], dict[str, Any]]
    classify_report_kind: Callable[[dict[str, Any]], str]
    save_and_export_job_report: Callable[[Job, dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]], dict[str, Any]]
    create_post_erase_test_job: Callable[[Job, str, str], Job]


def _load_and_mark_running(deps: JobRunnerDeps, job_id: str) -> Job | None:
    with deps.jobs_lock:
        job = deps.jobs.get(job_id) or deps.get_job(job_id)
        if not job:
            return None
        deps.jobs[job_id] = job
        job.status = "running"
        job.current_step = "Reading drive data"
        job.messages.append("Test started")
        deps.save_job(job)
        return job


def _mark_done(deps: JobRunnerDeps, job: Job, result: dict[str, Any]) -> None:
    with deps.jobs_lock:
        job.progress = 1.0
        job.status = "done"
        job.current_step = "Done"
        if "Test completed" not in job.messages:
            job.messages.append("Test completed")
        job.result = result
        deps.save_job(job)


def _mark_error(deps: JobRunnerDeps, job: Job, exc: Exception) -> None:
    with deps.jobs_lock:
        job.status = "error"
        job.error = str(exc)
        job.messages.append(f"Error: {exc}")
        deps.save_job(job)


def _queue_post_erase_child(deps: JobRunnerDeps, job: Job, post_test_mode: str, result: dict[str, Any]) -> Job:
    child = deps.create_post_erase_test_job(job, post_test_mode, result["report_id"])
    with deps.jobs_lock:
        job.progress = 1.0
        job.status = "done"
        job.current_step = "Erase done; post-erase test queued"
        job.messages.append(f"Post-erase test queued: {post_test_mode}")
        job.result = {**result, "post_test_job_id": child.id}
        deps.save_job(job)
    return child


def _finish_post_erase_parent(deps: JobRunnerDeps, job_id: str, fallback_job: Job, child: Job, result: dict[str, Any]) -> None:
    child = deps.get_job(child.id) or child
    with deps.jobs_lock:
        job = deps.get_job(job_id) or fallback_job
        if child.result:
            job.result = {**(job.result or result), "post_test_job_id": child.id, "post_test": child.result}
            job.current_step = "Done"
            job.messages.append("Post-erase test completed")
        elif child.error:
            job.result = {**(job.result or result), "post_test_job_id": child.id, "post_test_error": child.error}
            job.current_step = "Erase done; post-erase test failed"
            job.messages.append(f"Post-erase test failed: {child.error}")
        deps.save_job(job)


def run_job(deps: JobRunnerDeps, job_id: str) -> None:
    job = _load_and_mark_running(deps, job_id)
    if not job:
        return

    try:
        disk = deps.get_disk(job.device)
        smart = deps.get_smart_data(disk["path"])
        health = deps.compute_health(disk, smart)

        test_result = deps.execute_job_mode(job, disk, job.mode)
        post_test_mode = job.options.get("post_test_mode")
        is_erase_report = deps.classify_report_kind({"test": test_result, "source_job": {"mode": job.mode}}) == "erase"
        if post_test_mode and (not is_erase_report or post_test_mode not in drive_modes.post_erase_test_modes()):
            raise ValueError(f"Unsupported post erase test mode: {post_test_mode}")

        result = deps.save_and_export_job_report(job, disk, smart, health, test_result)

        if is_erase_report and post_test_mode:
            child = _queue_post_erase_child(deps, job, post_test_mode, result)
            run_job(deps, child.id)
            _finish_post_erase_parent(deps, job_id, job, child, result)
            return

        _mark_done(deps, job, result)
    except Exception as exc:
        _mark_error(deps, job, exc)
