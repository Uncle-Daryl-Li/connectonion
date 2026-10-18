"""Unit tests for the benchmark runner: real Agent, real skills plugin, fake model and judge.

LLM-Note: Tests for connectonion.benchmark.runner

What it tests:
- The judge's three answers map to PASS/FAIL/UNVERIFIED in code: a must_not that occurred is a hard FAIL,
  cannot_verify is UNVERIFIED, a statement the judge skipped is UNVERIFIED, never PASS
- auto invocation: a real Agent that calls skill(name=...) is activated; one that answers without it is not
- explicit invocation: the real skills plugin replaces `/name input`; the report records the effective input
- a missing skill is exit 2 before any case runs; an Agent that raises is a runner error (exit 3), never a pass
- load_agent imports a file ending in host(agent) without serving
- CO_EVAL_LIVE is 0 during a run unless --live, and restored after

Components under test:
- Module: connectonion/benchmark/runner.py (with connectonion.core.agent.Agent and useful_plugins.skills)
"""

import json
import os
import textwrap

import pytest

from connectonion.benchmark import runner
from connectonion.benchmark.suite import Case, Suite
from connectonion.core.agent import Agent
from connectonion.core.llm import LLMResponse, ToolCall
from connectonion.core.usage import TokenUsage
from connectonion.useful_plugins import skill, skills
from tests.utils.mock_helpers import MockLLM

SKILL_BODY = "Refund policy: refund only with a receipt. Never refund cash without one."


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    folder = tmp_path / ".co" / "skills" / "refund"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(f"---\nname: refund\ndescription: Handle refunds\n---\n{SKILL_BODY}\n")
    (tmp_path / ".co" / "benchmarks").mkdir()
    (tmp_path / ".co" / "benchmarks" / "refund.yaml").write_text("placeholder\n")
    return tmp_path


def suite(project, *cases):
    return Suite(name="refund", path=project / ".co" / "benchmarks" / "refund.yaml", cases=list(cases))


CASE = Case(id="with-receipt", kind="normal", input="Customer has a receipt, refund them",
            must=["The refund is approved"], must_not=["Cash is refunded without a receipt"])


def agent(calls_skill: bool, answer="Refund approved."):
    """A real Agent whose model either calls the skill tool first or answers straight away."""
    def complete(messages, tools):
        called = any(m.get("role") == "tool" for m in messages)
        if calls_skill and not called:
            return LLMResponse(content=None, raw_response=None, usage=TokenUsage(),
                               tool_calls=[ToolCall(name="skill", arguments={"name": "refund"}, id="t1")])
        return LLMResponse(content=answer, tool_calls=[], raw_response=None, usage=TokenUsage())
    return Agent("refund-bot", tools=[skill], plugins=[skills], llm=MockLLM(on_complete=complete), log=False)


def judge_says(*statuses):
    """A judge that answers the numbered statements with these statuses, in order."""
    def call(prompt, output, model):
        return output(verdicts=[{"statement": i, "status": status, "reason": status, "evidence": "e"}
                                for i, status in enumerate(statuses, start=1)])
    return call


def run(project, bot, *, skill_name="refund", invoke="auto", judge=None, **kwargs):
    chosen = runner.resolve_skill(skill_name) if skill_name else None
    return runner.run(suite(project, CASE), bot, agent_path="agent.py", skill=chosen, invoke=invoke,
                      judge_model="judge-model", judge_call=judge or judge_says("occurred", "did_not_occur"),
                      **kwargs)


# ---- scoring -----------------------------------------------------------------------------

def test_a_forbidden_outcome_that_occurred_is_a_hard_fail_whatever_else_passed(project):
    report = run(project, agent(True), judge=judge_says("occurred", "occurred"))

    verdicts = [i["verdict"] for i in report["cases"][0]["attempts"][0]["indicators"]]
    assert verdicts == ["PASS", "FAIL"]
    assert report["summary"]["forbidden_failures"] == 1 and report["summary"]["exit_code"] == 1


def test_what_the_judge_cannot_verify_or_skipped_is_unverified_never_pass(project):
    report = run(project, agent(True), judge=judge_says("cannot_verify"))  # nothing said about statement 2

    indicators = report["cases"][0]["attempts"][0]["indicators"]
    assert [i["verdict"] for i in indicators] == ["UNVERIFIED", "UNVERIFIED"]
    assert "no verdict" in indicators[1]["reason"]
    assert report["summary"]["exit_code"] == 1


def test_a_clean_run_passes_and_exits_zero(project):
    report = run(project, agent(True))

    assert report["summary"]["exit_code"] == 0
    assert report["cases"][0]["stability"] == "1/1"
    assert report["skill"]["sha256"] and "instructions" not in report["skill"]
    assert report["agent"]["model"] == "mock-llm"


# ---- was the skill used? --------------------------------------------------------------------

def test_auto_counts_the_skill_only_if_the_real_agent_called_it(project):
    used = run(project, agent(True))["cases"][0]["attempts"][0]["activation"]
    skipped = run(project, agent(False))

    assert used["status"] == "PASS"
    activation = skipped["cases"][0]["attempts"][0]["activation"]
    assert activation["status"] == "FAIL" and "never called skill(name='refund')" in activation["evidence"]
    assert skipped["summary"]["not_activated"] == 1 and skipped["summary"]["exit_code"] == 1


def test_explicit_goes_through_the_real_plugin_and_records_what_was_sent(project):
    report = run(project, agent(False), invoke="explicit")

    case = report["cases"][0]
    assert case["effective_input"] == "/refund Customer has a receipt, refund them"
    assert case["attempts"][0]["activation"] == {
        "status": "PASS", "evidence": "/refund was replaced with .co/skills/refund/SKILL.md"}, \
        "a project skill is named by its project path, not an absolute one"


@pytest.mark.parametrize("invoke, prefix", [("auto", ""), ("explicit", "/refund ")])
def test_given_is_sent_to_the_agent_before_the_input(project, invoke, prefix):
    case = Case(id="context", kind="normal", given="Yesterday had 20 contracts.",
                input="How many contracts now?", must=["Say 20 belongs to yesterday"])
    seen = []

    def complete(messages, tools):
        seen.extend(message.get("content", "") for message in messages if message.get("role") == "user")
        return LLMResponse(content="20 belongs to yesterday.", tool_calls=[],
                           raw_response=None, usage=TokenUsage())

    bot = Agent("context-bot", plugins=[skills], llm=MockLLM(on_complete=complete), log=False)
    chosen = runner.resolve_skill("refund") if invoke == "explicit" else None
    report = runner.run(suite(project, case), bot, agent_path="agent.py", skill=chosen,
                        invoke=invoke, judge_model="judge-model",
                        judge_call=judge_says("occurred"))
    expected = prefix + "Given context:\nYesterday had 20 contracts.\n\nUser request:\nHow many contracts now?"
    assert runner.effective_input(case, {"name": "refund"}, invoke) == expected
    assert report["cases"][0]["effective_input"] == expected
    assert any("Yesterday had 20 contracts." in message for message in seen)


def test_without_given_keeps_the_original_input(project):
    assert runner.effective_input(CASE, None, "auto") == CASE.input
    assert runner.effective_input(CASE, {"name": "refund"}, "explicit") == f"/refund {CASE.input}"


def test_explicit_without_the_plugin_is_caught_not_assumed(project):
    bare = Agent("no-plugin", llm=MockLLM(on_complete=lambda m, t: LLMResponse(
        content="ok", tool_calls=[], raw_response=None, usage=TokenUsage())), log=False)

    report = run(project, bare, invoke="explicit")

    activation = report["cases"][0]["attempts"][0]["activation"]
    assert activation["status"] == "FAIL" and "reached the Agent unchanged" in activation["evidence"]


def test_without_a_skill_no_skill_is_claimed(project):
    report = run(project, agent(False), skill_name=None)

    assert report["skill"] is None and report["cases"][0]["attempts"][0]["activation"] is None
    assert report["summary"]["exit_code"] == 0


# ---- failure is never a pass ----------------------------------------------------------------------

def test_a_skill_that_is_not_there_is_a_usage_error_before_anything_runs(project):
    with pytest.raises(runner.RunnerError) as error:
        runner.resolve_skill("missing")

    assert error.value.code == 2 and ".co/skills/missing/SKILL.md" in str(error.value)


def test_an_agent_that_raises_is_a_runner_error_not_a_pass(project):
    class Broken:
        name, llm, current_session = "broken", None, None

        def reset_conversation(self):
            pass

        def input(self, text):
            raise ConnectionError("model unreachable")

    report = run(project, Broken(), skill_name=None)

    attempt = report["cases"][0]["attempts"][0]
    assert attempt["passed"] is False and "ConnectionError: model unreachable" in attempt["error"]
    assert report["summary"]["exit_code"] == 3


def test_explicit_needs_a_skill(project):
    with pytest.raises(runner.RunnerError, match="needs --skill"):
        run(project, agent(False), skill_name=None, invoke="explicit")


def test_a_template_agent_py_that_ends_in_host_is_imported_without_serving(project):
    (project / "agent.py").write_text(textwrap.dedent("""
        from connectonion import Agent, host
        from connectonion.core.llm import LLM, LLMResponse

        class Quiet(LLM):
            def __init__(self):
                self.model = "quiet"
            def complete(self, messages, tools=None, **kw):
                return LLMResponse(content="hi", tool_calls=[], raw_response=None)
            def structured_complete(self, messages, output_schema, **kw):
                raise NotImplementedError

        agent = Agent("template", llm=Quiet(), log=False)
        host(agent)  # never returns if it really serves
    """))

    loaded = runner.load_agent("agent.py")

    import connectonion
    assert loaded.name == "template"
    assert connectonion.host.__name__ == "host", "the real host() is back after the import"


def test_live_is_off_during_a_run_unless_asked_and_restored_after(project, monkeypatch):
    seen = []

    class Recorder:
        name, llm = "rec", None
        current_session = {}

        def reset_conversation(self):
            pass

        def input(self, text):
            seen.append(os.environ.get("CO_EVAL_LIVE"))
            return "done"

    monkeypatch.setenv("CO_EVAL_LIVE", "outer")
    run(project, Recorder(), skill_name=None)
    run(project, Recorder(), skill_name=None, live=True)

    assert seen == ["0", "1"]
    assert os.environ["CO_EVAL_LIVE"] == "outer"


# ---- a run a new user can afford ---------------------------------------------------------

def test_an_agent_that_keeps_searching_is_stopped_at_the_ceiling_not_judged_and_not_a_pass(project):
    # 1.8.8b9: cases without their data let the template agent search the
    # workspace for 26 steps a case. The ceiling is the Agent's own loop limit.
    calls = []

    def look(pattern: str) -> str:
        """Search the workspace."""
        calls.append(pattern)
        return "nothing here"

    def complete(messages, tools):
        return LLMResponse(content=None, raw_response=None, usage=TokenUsage(),
                           tool_calls=[ToolCall(name="look", arguments={"pattern": "*"}, id=f"t{len(calls)}")])

    bot = Agent("searcher", tools=[look], llm=MockLLM(on_complete=complete), log=False, max_iterations=100)
    judged = []

    def judge(prompt, output, model):
        judged.append(prompt)
        return output(verdicts=[])

    report = run(project, bot, skill_name=None, judge=judge, max_iterations=3)

    attempt = report["cases"][0]["attempts"][0]
    assert len(calls) == 3
    assert attempt["passed"] is False and "stopped after 3 steps" in attempt["stopped"]
    assert "--max-iterations" in attempt["stopped"]
    assert judged == [], "an unfinished attempt costs no judge call"
    assert report["summary"]["stopped"] == 1 and report["summary"]["exit_code"] == 1
    assert report["max_iterations"] == 3
    assert bot.max_iterations == 100, "the Agent's own limit is back after the run"


def test_the_default_ceiling_is_far_below_the_template_limit(project):
    assert runner.DEFAULT_MAX_ITERATIONS <= 10
    report = run(project, agent(True))
    assert report["max_iterations"] == runner.DEFAULT_MAX_ITERATIONS
    assert report["summary"]["stopped"] == 0 and report["summary"]["exit_code"] == 0


# ---- the agent under test cannot read the answers ------------------------------------------

def _peeker(tool, arguments):
    """A real Agent that, like the co create template on 1.8.8b11, goes for the
    benchmark file first and answers only after it has seen it."""
    def complete(messages, tools):
        if not any(m.get("role") == "tool" for m in messages):
            return LLMResponse(content=None, raw_response=None, usage=TokenUsage(),
                               tool_calls=[ToolCall(name=tool.__name__, arguments=arguments, id="t1")])
        return LLMResponse(content="Refund approved.", tool_calls=[], raw_response=None, usage=TokenUsage())
    return Agent("peeker", tools=[tool], llm=MockLLM(on_complete=complete), log=False)


def _write_answers(project):
    (project / ".co" / "benchmarks" / "refund.yaml").write_text(
        "must:\n  - The refund is approved\nmust_not:\n  - Cash is refunded without a receipt\n")


def read_file(path: str) -> str:
    """Read a file."""
    with open(path) as handle:
        return handle.read()


def _first_result(report):
    return next(e for e in report["cases"][0]["attempts"][0]["trace"] if e.get("type") == "tool_result")


def test_reading_the_benchmark_file_is_refused_during_a_run(project):
    # 1.8.8b11: 4 of 5 attempts ran glob("**/*") then read_file(".co/benchmarks/
    # reimbursement.yaml"), the file holding every must and must_not, and scored 5/5.
    _write_answers(project)
    bot = _peeker(read_file, {"path": ".co/benchmarks/refund.yaml"})

    report = run(project, bot, skill_name=None)

    result = _first_result(report)
    assert result["status"] == "error" and "co eval run" in result["result"]
    assert "The refund is approved" not in result["result"]
    assert report["cases"][0]["attempts"][0]["invalid"] is None, "a refused read saw nothing"
    assert bot.events["before_each_tool"] == [], "the guard is gone after the run"


def test_an_absolute_path_or_an_earlier_run_is_refused_too(project):
    _write_answers(project)
    for path in (str(project / ".co" / "benchmarks" / "refund.yaml"), ".co/eval-runs/refund/x/report.json"):
        report = run(project, _peeker(read_file, {"path": path}), skill_name=None)
        assert _first_result(report)["status"] == "error", path


def test_a_declared_fixture_under_the_benchmark_folder_stays_readable(project):
    _write_answers(project)
    (project / ".co" / "benchmarks" / "ledger.csv").write_text("INV-1,paid\n")
    fixture_case = Case(id="ledger", kind="normal", input="Is INV-1 paid?", must=["INV-1 is reported paid"],
                        fixture="ledger.csv")
    report = runner.run(suite(project, fixture_case), _peeker(read_file, {"path": ".co/benchmarks/ledger.csv"}),
                        agent_path="agent.py", judge_model="m", judge_call=judge_says("occurred"))

    assert _first_result(report)["status"] == "success"


def test_an_attempt_that_saw_the_answers_anyway_is_invalid_not_judged_and_not_a_pass(project):
    # A path guard cannot see every way in (a grep over the workspace, a shell
    # pipeline): the trace is checked for the expectations themselves.
    _write_answers(project)

    def search(pattern: str) -> str:
        """Search every file in the workspace."""
        return "\n".join(p.read_text() for p in project.rglob("*.yaml"))

    judged = []

    def judge(prompt, output, model):
        judged.append(prompt)
        return output(verdicts=[])

    report = run(project, _peeker(search, {"pattern": "refund"}), skill_name=None, judge=judge)

    attempt = report["cases"][0]["attempts"][0]
    assert attempt["passed"] is False and "read the answers" in attempt["invalid"]
    assert judged == [], "an attempt that saw the answers is not scored"
    assert report["summary"]["invalid"] == 1 and report["summary"]["exit_code"] == 1

    from connectonion.benchmark import report as reports
    assert "INVALID" in reports.render(report)



# ---- offline command-fixture support (_stub_commands_for_eval) --------------------------------


@pytest.fixture
def bg_project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bm_dir = tmp_path / ".co" / "benchmarks"
    bm_dir.mkdir(parents=True)
    fx_dir = bm_dir / "fixtures"
    fx_dir.mkdir()
    (fx_dir / "projects.txt").write_text("KEY   NAME\nALPHA Alpha Project\n")
    (fx_dir / "verify.txt").write_text("Connected: test@example.com\n")
    (fx_dir / "map.json").write_text(json.dumps({
        "co jira projects": "projects.txt",
        "co jira verify": "verify.txt",
    }))
    return tmp_path


def _bg_agent():
    """An Agent whose bash / run_background / task_output tools echo back calls."""
    def bash(command: str, description: str = "", cwd: str = ".", timeout: int = 120, agent=None) -> str:
        """Run a bash command."""
        return f"bash_real: {command}"

    def run_background(command: str, description: str = "", agent=None) -> str:
        """Run a shell command in the background."""
        return f"Task real_1 started: {command}"

    def task_output(task_id: str, tail: int = 50) -> str:
        """Get output from a background task."""
        return f"original output for {task_id}"

    return Agent("bg-test", tools=[bash, run_background, task_output],
                 llm=MockLLM(on_complete=lambda m, t: LLMResponse(
                     content="done", tool_calls=[], raw_response=None, usage=TokenUsage())),
                 log=False)


def _fx_dir(bg_project):
    return bg_project / ".co" / "benchmarks" / "fixtures"


def test_stub_returns_fixture_output_for_matched_command(bg_project):
    bot = _bg_agent()
    fx = _fx_dir(bg_project)
    with runner._stub_commands_for_eval(bot, fx, live=False):
        rb = bot.tools.get("run_background")
        resp = rb(command="co jira projects", description="")
        assert "fixture_1" in resp
        to = bot.tools.get("task_output")
        content = to(task_id="fixture_1")
    assert "ALPHA Alpha Project" in content


def test_stub_exact_match_only__no_partial_match(bg_project):
    # "co jira projects" is in the map but "co jira projects --json" is not
    bot = _bg_agent()
    fx = _fx_dir(bg_project)
    with runner._stub_commands_for_eval(bot, fx, live=False):
        rb = bot.tools.get("run_background")
        resp = rb(command="co jira projects --json", description="")
    assert "real_1" in resp, "unregistered variant must delegate to original"


def test_stub_exact_match_only__json_does_not_match_bare(bg_project):
    # if only the --json variant is in the map, the bare command must not match
    fx = _fx_dir(bg_project)
    (fx / "map.json").write_text(json.dumps({"co jira projects --json": "projects.txt"}))
    bot = _bg_agent()
    with runner._stub_commands_for_eval(bot, fx, live=False):
        rb = bot.tools.get("run_background")
        resp = rb(command="co jira projects", description="")
    assert "real_1" in resp, "bare command must not match the --json fixture"


def test_stub_unmatched_command_delegates_to_original(bg_project):
    bot = _bg_agent()
    fx = _fx_dir(bg_project)
    with runner._stub_commands_for_eval(bot, fx, live=False):
        rb = bot.tools.get("run_background")
        resp = rb(command="co some other command", description="")
    assert "real_1" in resp and "co some other command" in resp


def test_stub_repeated_task_output_returns_same_content(bg_project):
    bot = _bg_agent()
    fx = _fx_dir(bg_project)
    with runner._stub_commands_for_eval(bot, fx, live=False):
        rb = bot.tools.get("run_background")
        resp = rb(command="co jira verify", description="")
        task_id = resp.split()[1]  # "Task fixture_1 started: ..."
        to = bot.tools.get("task_output")
        first = to(task_id=task_id)
        second = to(task_id=task_id)
    assert first == second
    assert "Connected: test@example.com" in first


def test_stub_transparent_when_live(bg_project):
    bot = _bg_agent()
    fx = _fx_dir(bg_project)
    original_rb = bot.tools.get("run_background")
    with runner._stub_commands_for_eval(bot, fx, live=True):
        rb_inside = bot.tools.get("run_background")
    assert rb_inside is original_rb, "live=True must not swap tools"


def test_stub_transparent_when_no_map(bg_project):
    bot = _bg_agent()
    fx = _fx_dir(bg_project)
    (fx / "map.json").unlink()
    original_rb = bot.tools.get("run_background")
    with runner._stub_commands_for_eval(bot, fx, live=False):
        rb_inside = bot.tools.get("run_background")
    assert rb_inside is original_rb, "absent map.json must not swap tools"


def test_stub_malformed_map_raises_runner_error(bg_project):
    fx = _fx_dir(bg_project)
    (fx / "map.json").write_text("{ not valid json")
    bot = _bg_agent()
    with pytest.raises(runner.RunnerError) as exc:
        with runner._stub_commands_for_eval(bot, fx, live=False):
            pass
    assert exc.value.code == 2 and "not valid JSON" in str(exc.value)


def test_stub_map_wrong_type_raises_runner_error(bg_project):
    fx = _fx_dir(bg_project)
    (fx / "map.json").write_text(json.dumps(["list", "not", "object"]))
    bot = _bg_agent()
    with pytest.raises(runner.RunnerError) as exc:
        with runner._stub_commands_for_eval(bot, fx, live=False):
            pass
    assert exc.value.code == 2 and "JSON object" in str(exc.value)


def test_stub_missing_fixture_file_raises_runner_error(bg_project):
    fx = _fx_dir(bg_project)
    (fx / "map.json").write_text(json.dumps({"co jira verify": "does_not_exist.txt"}))
    bot = _bg_agent()
    with pytest.raises(runner.RunnerError) as exc:
        with runner._stub_commands_for_eval(bot, fx, live=False):
            pass
    assert exc.value.code == 2 and "does_not_exist.txt" in str(exc.value)


def test_stub_absolute_path_in_map_raises_runner_error(bg_project):
    fx = _fx_dir(bg_project)
    abs_path = str(bg_project / "outside.txt")
    (bg_project / "outside.txt").write_text("should not be accessible")
    (fx / "map.json").write_text(json.dumps({"co jira verify": abs_path}))
    bot = _bg_agent()
    with pytest.raises(runner.RunnerError) as exc:
        with runner._stub_commands_for_eval(bot, fx, live=False):
            pass
    assert exc.value.code == 2 and "absolute" in str(exc.value)


def test_stub_traversal_attempt_raises_runner_error(bg_project):
    fx = _fx_dir(bg_project)
    (bg_project / "outside.txt").write_text("should not be accessible")
    (fx / "map.json").write_text(json.dumps({"co jira verify": "../../../outside.txt"}))
    bot = _bg_agent()
    with pytest.raises(runner.RunnerError) as exc:
        with runner._stub_commands_for_eval(bot, fx, live=False):
            pass
    assert exc.value.code == 2 and "escapes" in str(exc.value)


def test_stub_tools_restored_after_normal_completion(bg_project):
    bot = _bg_agent()
    fx = _fx_dir(bg_project)
    original_rb = bot.tools.get("run_background")
    original_to = bot.tools.get("task_output")
    with runner._stub_commands_for_eval(bot, fx, live=False):
        pass
    assert bot.tools.get("run_background") is original_rb
    assert bot.tools.get("task_output") is original_to


def test_stub_tools_restored_after_exception(bg_project):
    bot = _bg_agent()
    fx = _fx_dir(bg_project)
    original_rb = bot.tools.get("run_background")
    with pytest.raises(RuntimeError):
        with runner._stub_commands_for_eval(bot, fx, live=False):
            raise RuntimeError("simulated crash")
    assert bot.tools.get("run_background") is original_rb


def test_stub_first_tool_restored_if_second_stub_install_fails(bg_project):
    """If add() raises while installing the second stub (task_output), the
    first stub (run_background) must still be removed and the original restored."""
    bot = _bg_agent()
    fx = _fx_dir(bg_project)
    original_rb = bot.tools.get("run_background")
    original_to = bot.tools.get("task_output")

    real_add = bot.tools.add
    call_count = [0]

    def failing_add(tool):
        call_count[0] += 1
        if call_count[0] == 2:  # second add is the task_output stub
            raise RuntimeError("simulated registry failure on second stub")
        return real_add(tool)

    bot.tools.add = failing_add
    with pytest.raises(RuntimeError, match="simulated registry failure"):
        with runner._stub_commands_for_eval(bot, fx, live=False):
            pass  # pragma: no cover

    # Both originals must be restored even though installation failed partway.
    assert bot.tools.get("run_background") is original_rb
    assert bot.tools.get("task_output") is original_to


def test_stub_third_stub_install_fails_all_originals_restored(bg_project):
    """If add() raises while installing the third stub (bash), after run_background
    and task_output stubs were already installed, all three originals must be
    restored in finally."""
    bot = _bg_agent()
    fx = _fx_dir(bg_project)
    original_rb   = bot.tools.get("run_background")
    original_to   = bot.tools.get("task_output")
    original_bash = bot.tools.get("bash")

    real_add = bot.tools.add
    call_count = [0]

    def failing_add(tool):
        call_count[0] += 1
        if call_count[0] == 3:  # third add is the bash stub
            raise RuntimeError("simulated registry failure on third stub")
        return real_add(tool)

    bot.tools.add = failing_add
    with pytest.raises(RuntimeError, match="simulated registry failure"):
        with runner._stub_commands_for_eval(bot, fx, live=False):
            pass  # pragma: no cover

    # All three originals must be back.
    assert bot.tools.get("run_background") is original_rb
    assert bot.tools.get("task_output") is original_to
    assert bot.tools.get("bash") is original_bash


def test_stub_duplicate_normalized_commands_raises_runner_error(bg_project):
    """Two map keys that collapse to the same normalised command must raise
    RunnerError(code=2) instead of silently overwriting the first fixture."""
    fx = _fx_dir(bg_project)
    # "co jira projects" and "co  jira  projects" normalise identically.
    (fx / "map.json").write_text(json.dumps({
        "co jira projects": "projects.txt",
        "co  jira  projects": "verify.txt",
    }))
    bot = _bg_agent()
    with pytest.raises(runner.RunnerError) as exc:
        with runner._stub_commands_for_eval(bot, fx, live=False):
            pass
    assert exc.value.code == 2 and "duplicates" in str(exc.value)


def test_stub_fixture_read_error_raises_runner_error(bg_project):
    """A fixture file that cannot be decoded raises RunnerError(code=2)."""
    fx = _fx_dir(bg_project)
    # Write bytes that are not valid UTF-8.
    (fx / "bad.bin").write_bytes(b"\xff\xfe invalid utf-8 \x80\x81")
    (fx / "map.json").write_text(json.dumps({"co jira verify": "bad.bin"}))
    bot = _bg_agent()
    with pytest.raises(runner.RunnerError) as exc:
        with runner._stub_commands_for_eval(bot, fx, live=False):
            pass
    assert exc.value.code == 2 and "bad.bin" in str(exc.value)


def test_stub_bash_returns_fixture_directly_for_matched_command(bg_project):
    """bash("co jira projects") must return fixture content directly, no task ID."""
    bot = _bg_agent()
    fx = _fx_dir(bg_project)
    with runner._stub_commands_for_eval(bot, fx, live=False):
        bash = bot.tools.get("bash")
        result = bash(command="co jira projects", description="")
    assert "ALPHA Alpha Project" in result
    # A task-ID prefix would mean it went through run_background path by mistake.
    assert "fixture_" not in result


def test_stub_bash_unmatched_command_delegates_to_original(bg_project):
    """An unregistered bash command must fall through to the real bash tool."""
    bot = _bg_agent()
    fx = _fx_dir(bg_project)
    with runner._stub_commands_for_eval(bot, fx, live=False):
        bash = bot.tools.get("bash")
        result = bash(command="echo hello", description="")
    assert "bash_real: echo hello" in result


def test_stub_bash_transparent_when_live(bg_project):
    """live=True must leave the bash tool completely unchanged."""
    bot = _bg_agent()
    fx = _fx_dir(bg_project)
    original_bash = bot.tools.get("bash")
    with runner._stub_commands_for_eval(bot, fx, live=True):
        assert bot.tools.get("bash") is original_bash


def test_stub_bash_restored_after_normal_completion(bg_project):
    """The original bash tool must be back on the agent after a clean exit."""
    bot = _bg_agent()
    fx = _fx_dir(bg_project)
    original_bash = bot.tools.get("bash")
    with runner._stub_commands_for_eval(bot, fx, live=False):
        pass
    assert bot.tools.get("bash") is original_bash


def test_stub_bash_restored_after_body_exception(bg_project):
    """The original bash tool must be restored even when the body raises."""
    bot = _bg_agent()
    fx = _fx_dir(bg_project)
    original_bash = bot.tools.get("bash")
    with pytest.raises(RuntimeError):
        with runner._stub_commands_for_eval(bot, fx, live=False):
            raise RuntimeError("crash in body")
    assert bot.tools.get("bash") is original_bash
