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
