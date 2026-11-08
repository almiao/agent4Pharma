import threading
from types import SimpleNamespace

import pytest

from agent4pharma.entrypoints.web.paper_continuation_api import PaperContinuationAPI
from agent4pharma.workflows.continue_imported_paper import continue_imported_paper


class Production:
    def __init__(self):
        self.tasks = []
        self.executed = []
        self.execution = self
        self.roles = ["editor", "writer", "reviewer", "publisher"]

    def start(self, ids, limits):
        assert ids == ["candidate"] and limits.max_attempts == 2
        self.initial = SimpleNamespace(id=self.roles[0], role=self.roles[0], status="queued")
        return self.initial

    def request_automatic(self, identity):
        task = next((t for t in self.tasks if t.id == identity), self.initial)
        task.auto_run = True
        return task

    def snapshot(self):
        return SimpleNamespace(tasks=self.tasks, stories=[])

    def next_task(self, result):
        return next((t for t in self.tasks if t.status == "queued"), None)

    def execute(self, identity):
        self.executed.append(identity)
        roles = self.roles
        index = roles.index(identity)
        self.tasks = (
            []
            if index == len(roles) - 1
            else [
                SimpleNamespace(id=roles[index + 1], role=roles[index + 1], status="queued", story_id="story")
            ]
        )
        return SimpleNamespace(id=identity, status="succeeded", story_id="story", result_ref="story")


def test_imported_paper_executes_scoped_four_role_chain():
    production = Production()
    progress = []
    continue_imported_paper(
        lambda: "candidate", production, production.execute, lambda *args: progress.append(args)
    )
    assert production.executed == ["editor", "writer", "reviewer", "publisher"]
    assert [p[0] for p in progress] == [
        "editor",
        "writer",
        "reviewer",
        "publisher",
        "succeeded",
    ]


def test_missing_human_recommendation_is_a_technical_error_not_model_exclusion():
    production = Production()
    progress = []
    with pytest.raises(ValueError, match="人工推荐"):
        continue_imported_paper(
            lambda: "", production, production.execute, lambda *args: progress.append(args)
        )
    assert not production.executed and not progress


def test_role_failure_stops_without_marking_success():
    production = Production()
    progress = []

    def execute(identity):
        return SimpleNamespace(id=identity, status="failed", error="可展示的执行失败说明")

    continue_imported_paper(lambda: "candidate", production, execute, lambda *args: progress.append(args))
    assert progress[-1][0] == "failed"
    assert "succeeded" not in [p[0] for p in progress]


@pytest.mark.parametrize("status", ["paused", "cancelled"])
def test_manual_control_stops_auto_advance_without_reporting_model_failure(status):
    production = Production()
    progress = []
    continue_imported_paper(
        lambda: "candidate",
        production,
        lambda identity: SimpleNamespace(id=identity, status=status),
        lambda *args: progress.append(args),
    )
    assert progress[-1][0] == "waiting" and "暂停或停止" in progress[-1][-1]


def test_editorial_decision_without_downstream_is_not_a_completed_release():
    production = Production()
    progress = []

    def execute(identity):
        return SimpleNamespace(id=identity, status="succeeded", story_id="", result_ref="defer")

    continue_imported_paper(lambda: "candidate", production, execute, lambda *args: progress.append(args))
    assert progress[-1][0] == "succeeded"
    assert production.executed == []


def test_repeated_import_confirmation_does_not_resubmit_background_model():
    state = {"status": "idle"}
    started, release = threading.Event(), threading.Event()
    calls = []

    def progress(run, record, status, task, error):
        state["status"] = status

    def run(*args):
        calls.append(args)
        started.set()
        assert release.wait(5)
        state["status"] = "succeeded"

    api = PaperContinuationAPI(lambda *_: state["status"], progress, run)
    try:
        api.start("run", "record")
        assert started.wait(3)
        api.start("run", "record")
        release.set()
        api.executor.shutdown(wait=True)
        api.start("run", "record")
        assert len(calls) == 1
    finally:
        release.set()
        api.close()


def test_unstarted_persisted_queue_can_resume_without_retrying_failed_tasks():
    state = {"status": "queued"}
    calls = []

    def progress(run, record, status, task, error):
        state["status"] = status

    def run(*args):
        calls.append(args)
        state["status"] = "succeeded"

    api = PaperContinuationAPI(lambda *_: state["status"], progress, run)
    try:
        api.start("run", "record")
        api.executor.shutdown(wait=True)
        state["status"] = "failed"
        api.start("run", "record")
        assert len(calls) == 1
    finally:
        api.close()


def test_unknown_background_error_does_not_expose_private_exception_text():
    progress = []

    def run(*args):
        raise RuntimeError("credential=do-not-persist")

    api = PaperContinuationAPI(lambda *_: "idle", lambda *args: progress.append(args), run)
    try:
        api.start("run", "paper")
        api.executor.shutdown(wait=True)
        assert progress[-1][-1] == "自动处理失败：RuntimeError"
        assert "do-not-persist" not in str(progress)
    finally:
        api.close()
