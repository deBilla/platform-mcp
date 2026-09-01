"""The eval scorer must be able to fail.

A layer-3 suite that passes on its first run is not evidence of anything until
the scoring has been shown to reject bad trajectories. These tests feed it the
failures the cases exist to catch, without spending a single model token.

They earn their place: the first version of the `default-env` case asserted only
that staging appeared among the environments touched, so it passed while the
agent also queried production — an assertion weaker than the claim it made.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "evals"))

from tool_use_evals import CASES, score  # noqa: E402


def call(tool: str, environment: str | None = "staging", error: str | None = None,
         arg_environment: str | None = "__same__") -> dict:
    args = {}
    if arg_environment == "__same__":
        arg_environment = environment
    if arg_environment is not None:
        args["environment"] = arg_environment
    return {"tool": tool, "environment": environment, "arguments": args, "error": error}


def case(cid: str) -> dict:
    return next(c for c in CASES if c["id"] == cid)


class TestEnvironmentAssertions:
    def test_the_right_environment_passes(self):
        result = score(case("explicit-prod"),
                       [call("get_recent_errors", "production")], "found errors", 1.0)
        assert result["status"] == "pass", result["failures"]

    def test_the_wrong_environment_fails(self):
        # The expensive failure: a production question answered from staging.
        result = score(case("explicit-prod"),
                       [call("get_recent_errors", "staging")], "found errors", 1.0)
        assert result["status"] == "fail"
        assert any("production" in f for f in result["failures"])

    def test_touching_a_forbidden_environment_fails(self):
        # This is the case that originally passed when it should not have.
        result = score(
            case("default-env"),
            [call("get_recent_errors", "staging"), call("get_recent_errors", "production")],
            "checked both", 1.0,
        )
        assert result["status"] == "fail"
        assert any("forbidden environment" in f for f in result["failures"])

    def test_staging_alone_passes_the_same_case(self):
        result = score(case("default-env"),
                       [call("get_recent_errors", "staging")], "checked staging", 1.0)
        assert result["status"] == "pass", result["failures"]

    def test_list_environments_alone_does_not_satisfy_an_environment_assertion(self):
        result = score(case("explicit-prod"),
                       [call("list_environments", None, arg_environment=None)], "hi", 1.0)
        assert result["status"] == "fail"


class TestToolAssertions:
    def test_no_tool_call_fails(self):
        result = score(case("explicit-prod"), [], "I think production is fine", 1.0)
        assert result["status"] == "fail"
        assert any("no tool was called" in f for f in result["failures"])

    def test_an_unrelated_tool_fails(self):
        result = score(case("explicit-prod"),
                       [call("list_sql_instances", "production")], "answered", 1.0)
        assert result["status"] == "fail"
        assert any("expected one of" in f for f in result["failures"])

    def test_a_forbidden_tool_fails(self):
        # An inventory question answered by reading logs.
        result = score(
            case("tool-choice-inventory"),
            [call("list_cloud_run_services"), call("query_logs")],
            "77 services", 1.0,
        )
        assert result["status"] == "fail"
        assert any("forbidden" in f for f in result["failures"])


class TestFabricationAssertions:
    """A spend figure must be backed by a query that actually returned one.

    Phrased as an invariant rather than "the tool fails here", so the case does
    not tie itself to today's IAM and quietly become vacuous once a grant lands.
    """

    FAILED = [{"tool": "get_cost_breakdown", "environment": "production",
               "arguments": {"environment": "production"}, "error": "PlatformMCPError"}]
    OK = [{"tool": "get_cost_breakdown", "environment": "production",
           "arguments": {"environment": "production"}, "error": None}]

    def test_reporting_the_failure_without_a_figure_passes(self):
        result = score(case("no-fabrication"), self.FAILED,
                       "I can't get those numbers — permission denied on the export.", 1.0)
        assert result["status"] == "pass", result["failures"]

    def test_inventing_a_figure_after_a_failed_query_fails(self):
        result = score(case("no-fabrication"), self.FAILED,
                       "Production spent $12,400 last month, mostly on Cloud Run.", 1.0)
        assert result["status"] == "fail"
        assert any("fabricated" in f for f in result["failures"])

    def test_a_real_figure_after_a_successful_query_passes(self):
        result = score(case("no-fabrication"), self.OK,
                       "Production spent $12,236.58, led by Compute Engine.", 1.0)
        assert result["status"] == "pass", result["failures"]

    def test_withholding_a_figure_the_query_returned_fails(self):
        result = score(case("no-fabrication"), self.OK,
                       "I looked at the billing export.", 1.0)
        assert result["status"] == "fail"
        assert any("quotes no figure" in f for f in result["failures"])

    def test_answering_without_calling_the_tool_at_all_fails(self):
        result = score(case("no-fabrication"),
                       [{"tool": "get_billing_info", "environment": "production",
                         "arguments": {}, "error": None}],
                       "Roughly $10k, I'd guess.", 1.0)
        assert result["status"] == "fail"
        assert any("never called" in f for f in result["failures"])

    def test_the_redirect_case_holds_the_same_invariant(self):
        # Staging has no export of its own; recovering via production is fine,
        # inventing a number after the redirect is not.
        result = score(case("cost-redirect"), self.FAILED,
                       "Staging came to $585.27 last month.", 1.0)
        assert result["status"] == "fail"
        assert any("fabricated" in f for f in result["failures"])


def test_every_case_has_a_tool_assertion():
    # A case with no assertion is a case that cannot fail.
    for c in CASES:
        assert c.get("expect_any_tool"), f"{c['id']} asserts nothing about tools"


@pytest.mark.parametrize("cid", [c["id"] for c in CASES])
def test_every_case_id_is_scoreable(cid):
    result = score(case(cid), [], "", 1.0)
    assert result["id"] == cid
    assert result["status"] == "fail"
