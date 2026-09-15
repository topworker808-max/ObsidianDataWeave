"""Tests for scripts/research_notebook.py — survive the library's typed-model switch.

Why this file exists: `research_notebook.py` was written against a notebooklm-py that
returned plain dicts from `research.start()` and `research.poll()`. v0.8.0 removed that
dict-subscript back-compat bridge (upstream issue #1251) and returns frozen dataclasses
instead. The script kept reading `.get("task_id")` and guarding with
`isinstance(start_result, dict)`, so on v0.8.2 every research run died at the guard:

    ERROR: research.start returned unexpected payload: ResearchStart(task_id='Ch...')

The task had in fact started — only the parse of a perfectly good reply failed. That is
the defect class this file gates: a script pinned to a shape its library no longer
returns, where the payload is valid and the reading of it is not.

These tests deliberately use the REAL installed dataclasses rather than stand-ins. A
hand-rolled fake would keep passing after upstream changes the shape again, which is
precisely the failure we are trying to catch.
"""

import pytest

from scripts.research_notebook import _as_public_dict, _source_url

research_types = pytest.importorskip(
    "notebooklm._types.research",
    reason="notebooklm-py not installed; the conversion is only meaningful against it",
)

ResearchSource = research_types.ResearchSource
ResearchStart = research_types.ResearchStart
ResearchStatus = research_types.ResearchStatus
ResearchTask = research_types.ResearchTask


class TestTypedModelsConvert:
    """Red side — each of these tripped the `isinstance(..., dict)` guard."""

    def test_research_start_becomes_a_dict_with_task_id(self) -> None:
        started = ResearchStart(
            task_id="ChBiZDhkM2QwNjExYWZmM2UyEAgaBDM0YzcqA3Vzdw",
            report_id="b94fc1dc-6f62-4154-a416-79e62c737a93",
            notebook_id="4665606e-a1d7-4f1f-a7c1-3386833a3e83",
            query="UK B2B cold email reply benchmarks",
            mode="deep",
        )
        result = _as_public_dict(started)

        # The two things the script does with it, in order.
        assert isinstance(result, dict), "the guard rejects anything that is not a dict"
        assert result.get("task_id") == started.task_id

    def test_research_task_poll_keeps_the_keys_the_loop_reads(self) -> None:
        """The poll loop reads task_id, query, status and sources — all four must survive."""
        task = ResearchTask(
            task_id="task-1",
            status=ResearchStatus.COMPLETED,
            query="UK B2B cold email reply benchmarks",
            sources=(ResearchSource(url="https://example.test/a", title="A"),),
        )
        result = _as_public_dict(task)

        assert result.get("task_id") == "task-1"
        assert result.get("query") == "UK B2B cold email reply benchmarks"
        assert result.get("status") == "completed", "the loop compares against the string"
        assert len(result.get("sources") or []) == 1

    def test_imported_sources_are_readable_by_source_url(self) -> None:
        """The consequence, not the symptom: a converted source must still yield a URL.

        `_source_url` is what the dedupe filter runs on every discovered source before
        import. A conversion that produced objects it cannot read would import nothing
        while reporting success — the same silent-empty outcome as the wiki bugs.
        """
        task = ResearchTask(
            task_id="task-1",
            status=ResearchStatus.COMPLETED,
            sources=(
                ResearchSource(url="https://example.test/a", title="A"),
                ResearchSource(url="https://example.test/b", title="B"),
            ),
        )
        sources = _as_public_dict(task)["sources"]

        assert [_source_url(s) for s in sources] == [
            "https://example.test/a",
            "https://example.test/b",
        ]

    def test_in_progress_task_is_not_mistaken_for_completed(self) -> None:
        task = ResearchTask(task_id="task-1", status=ResearchStatus.IN_PROGRESS)
        assert _as_public_dict(task).get("status") == "in_progress"

    def test_no_research_placeholder_survives(self) -> None:
        """`ResearchTask.empty()` is the "nothing in flight" sentinel the loop bails on."""
        assert _as_public_dict(ResearchTask.empty()).get("status") == "no_research"


class TestBackCompatAndEdges:
    """Green side — the conversion must not break what already worked."""

    def test_a_plain_dict_passes_through_untouched(self) -> None:
        """Pre-0.8.0 returned dicts; the script must keep working against those."""
        payload = {"task_id": "abc", "status": "completed", "sources": []}
        assert _as_public_dict(payload) is payload

    def test_none_becomes_an_empty_dict(self) -> None:
        """The poll call used to be guarded by `or {}` — that guard moved in here."""
        assert _as_public_dict(None) == {}


class _FakeResearchAPI:
    """Records how the run was addressed. Refuses an unpinned poll, as 0.8 does."""

    def __init__(self, start_result) -> None:
        self._start_result = start_result
        self.polled_with: list[str | None] = []

    async def start(self, notebook_id, query, *, source, mode):
        return self._start_result

    async def poll(self, notebook_id, task_id=None):
        self.polled_with.append(task_id)
        if task_id is None:
            # Stands in for AmbiguousResearchTaskError: once a notebook has more
            # than one research run in its history, an unpinned poll cannot pick.
            raise AssertionError("poll was not pinned to a run id")
        return ResearchTask(
            task_id=task_id, status=ResearchStatus.COMPLETED, query=self._start_result.query
        )


class _FakeSourcesAPI:
    async def list(self, notebook_id):
        return []


class _FakeClient:
    def __init__(self, start_result) -> None:
        self.research = _FakeResearchAPI(start_result)
        self.sources = _FakeSourcesAPI()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class TestPollIsPinnedToTheRunId:
    """The run is addressed by `report_id`, and the poll is always pinned.

    Two separate failures ride on this. Unpinned, `poll()` raises
    `AmbiguousResearchTaskError` as soon as the notebook has more than one
    research task in its history — and a COMPLETED task stays in that list, so
    the second research run on any notebook breaks the first. Pinned to the
    wrong id, the poll reports `not_found` and the loop spins until timeout.
    """

    def _run(self, *, task_id: str, report_id: str | None):
        import asyncio
        from argparse import Namespace

        import scripts.research_notebook as rn

        started = ResearchStart(
            task_id=task_id,
            report_id=report_id,
            notebook_id="nb-1",
            query="q",
            mode="deep",
        )
        captured: dict = {}

        class _Factory:
            @staticmethod
            async def from_storage(**kwargs):
                client = _FakeClient(started)
                captured["client"] = client
                return client

        original = rn._import_notebooklm_client
        rn._import_notebooklm_client = lambda: _Factory
        try:
            rc = asyncio.run(
                rn._run_research(
                    Namespace(
                        notebook_id="nb-1",
                        query="q",
                        mode="deep",
                        source="web",
                        max_sources=None,
                        poll_interval=0,
                        poll_timeout=5,
                        profile=None,
                        dry_run=False,
                        non_interactive=True,
                    )
                )
            )
        finally:
            rn._import_notebooklm_client = original
        return rc, captured["client"].research.polled_with

    def test_report_id_is_what_the_poll_is_pinned_to(self) -> None:
        """start() hands back an opaque base64 task_id and a UUID report_id.

        The UUID is the one the poll answers to — observed live 15.09: start()
        returned report_id 'b94fc1dc-…' and that exact string was the task_id
        the poll reported back.
        """
        rc, polled = self._run(
            task_id="ChAwM2NmNTc1ODIzOTM1OTUxEAgaBDBjODgqA3Vzdw",
            report_id="9d81af13-eae6-4869-b894-d035683ac196",
        )
        assert rc == 0
        assert polled == ["9d81af13-eae6-4869-b894-d035683ac196"]

    def test_poll_falls_back_to_task_id_when_no_report_id(self) -> None:
        """Still pinned — never unpinned — when the payload carries no report_id."""
        rc, polled = self._run(task_id="task-only", report_id=None)
        assert rc == 0
        assert polled == ["task-only"]
