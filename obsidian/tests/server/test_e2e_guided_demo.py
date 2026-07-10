"""End-to-end: the Guided Demo Tour's structural contract.

Scenario 7 of the Haven end-to-end suite. Per ``dashboard.html``'s own
comment above ``TOUR_STEPS``, the tour is "presentation only... adds no
page of its own and calls no retrieval, memory, ontology, ranking,
acceptance, planner, or benchmark logic -- every step just scrolls to and
highlights a section that already exists above." There is therefore no
backend endpoint to drive for "click Next" / "click Finish" / "click Skip"
-- those are pure client-side DOM/JS behavior, and this repo has no browser
automation tooling (Playwright/Selenium) installed, so exercising the
literal click-through interaction is out of scope here (see this suite's
final report for that documented gap).

What *is* server-testable, and what actually keeps the tour from silently
breaking, is its structural contract: every step's ``target`` selector
must resolve to a real, unique section id on the served page, and every
navigation control (`Skip`, `Back`, `Next`/`Finish`, `Close`, step counter,
dots) must be present -- on both an empty vault and a populated one, since
the tour is meant to be usable as someone's very first interaction with
Haven.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from obsidian.manager_ai.models import KnowledgeObject

# Mirrors dashboard.html's own TOUR_STEPS target list (see that file's
# "Guided Demo Tour" section) -- kept here as plain data so this test fails
# loudly (rather than silently drifting) if a step's target is renamed
# without updating this list.
_TOUR_STEP_TARGETS = (
    "#quick-capture",
    "#memories",
    "#overview",
    "#focus",
    "#pipeline",
    "#benchmarks",
)

_TOUR_CONTROL_IDS = (
    "start-demo-btn",
    "tour-bar",
    "tour-step-label",
    "tour-close-btn",
    "tour-title",
    "tour-body",
    "tour-dots",
    "tour-skip-btn",
    "tour-back-btn",
    "tour-next-btn",
)


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("HAVEN_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("HAVEN_CONCEPT_DIR", str(tmp_path / "concepts"))

    from obsidian.server.main import app

    with TestClient(app) as test_client:
        yield test_client


def _section_ids(html: str) -> set:
    return set(re.findall(r'<section id="([^"]+)"', html))


class TestTourStructuralContractOnEmptyVault:
    """The tour must be usable as a first-run experience -- before the user
    has remembered anything."""

    def test_dashboard_loads_successfully_on_an_empty_vault(
        self, client: TestClient
    ) -> None:
        response = client.get("/dashboard")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_every_tour_step_target_exists_as_exactly_one_section(
        self, client: TestClient
    ) -> None:
        html = client.get("/dashboard").text
        section_ids = _section_ids(html)
        for target in _TOUR_STEP_TARGETS:
            section_id = target.lstrip("#")
            assert section_id in section_ids, f"tour target {target} has no matching section"
            assert html.count(f'<section id="{section_id}"') == 1

    def test_every_navigation_control_is_present(self, client: TestClient) -> None:
        html = client.get("/dashboard").text
        for control_id in _TOUR_CONTROL_IDS:
            assert f'id="{control_id}"' in html, f"missing tour control: {control_id}"

    def test_tour_bar_starts_hidden_until_started(self, client: TestClient) -> None:
        html = client.get("/dashboard").text
        match = re.search(r'<div id="tour-bar" class="([^"]+)"', html)
        assert match is not None
        assert "hidden" in match.group(1).split()

    def test_start_button_wires_up_to_the_tour_start_handler(
        self, client: TestClient
    ) -> None:
        html = client.get("/dashboard").text
        assert (
            "document.getElementById('start-demo-btn').addEventListener('click', startTour)"
            in html
        )

    def test_finish_and_skip_and_close_all_route_through_endtour(
        self, client: TestClient
    ) -> None:
        """"Finish" is not a separate control -- tourGoTo relabels
        tour-next-btn's text to "Finish" on the last step, and its click
        handler calls endTour() once there (see the Next handler); Skip and
        Close both call endTour() directly. All three converge on the same
        cleanup path, so none of them can leave the tour half-open."""
        html = client.get("/dashboard").text
        assert "tourStepIndex === TOUR_STEPS.length - 1) endTour();" in html
        assert (
            "document.getElementById('tour-skip-btn').addEventListener('click', endTour)"
            in html
        )
        assert (
            "document.getElementById('tour-close-btn').addEventListener('click', endTour)"
            in html
        )
        assert "tourStepIndex === TOUR_STEPS.length - 1 ? 'Finish' : 'Next'" in html

    def test_escape_key_also_closes_the_tour(self, client: TestClient) -> None:
        html = client.get("/dashboard").text
        assert "e.key === 'Escape' && tourStepIndex !== -1) endTour();" in html


class TestTourStructuralContractOnPopulatedVault:
    """The same structural guarantees hold once the vault has real content
    -- the tour must not depend on an empty-state layout that would
    disappear once memories exist."""

    def test_every_tour_target_and_control_survives_a_populated_vault(
        self, client: TestClient
    ) -> None:
        app = client.app
        knowledge = KnowledgeObject(canonical_fact="Haven uses Terraform for infra.")
        app.state.vault_writer.write(knowledge)
        app.state.ontology_pipeline.process(knowledge)

        html = client.get("/dashboard").text
        section_ids = _section_ids(html)
        for target in _TOUR_STEP_TARGETS:
            assert target.lstrip("#") in section_ids
        for control_id in _TOUR_CONTROL_IDS:
            assert f'id="{control_id}"' in html
