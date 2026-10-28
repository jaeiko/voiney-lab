"""Offline contracts for the human-run model reasoning comparison."""
from __future__ import annotations

import json

import pytest


def _item(group: str, index: int, length: int):
    from scripts.model_reasoning_eval import SampleItem

    return SampleItem(
        group=group, protocol_id=f"protocol-{group}", fact_key=f"fact-{index}",
        source_sha256=f"{index:064x}", source_chars=length,
    )


def test_balanced_translation_sample_covers_each_group_and_length_band() -> None:
    from collections import Counter
    from scripts.model_reasoning_eval import balanced_sample

    candidates = {
        group: [_item(group, index + offset, 10 + index * 10)
                for index in range(30)]
        for offset, group in enumerate(("biology", "chemistry", "materials"), start=100)
    }
    sample = balanced_sample(candidates, size=60, seed=1028)

    assert len(sample) == 60
    by_group = Counter(item.group for item in sample)
    assert by_group == {"biology": 20, "chemistry": 20, "materials": 20}
    for group in candidates:
        bands = Counter(item.length_band for item in sample if item.group == group)
        assert max(bands.values()) - min(bands.values()) <= 1
        assert set(bands) == {"short", "medium", "long"}


def test_balanced_translation_sample_refuses_an_underfilled_group() -> None:
    from scripts.model_reasoning_eval import EvaluationError, balanced_sample

    candidates = {
        "biology": [_item("biology", index, 10 + index) for index in range(3)],
        "chemistry": [_item("chemistry", 100 + index, 10 + index)
                      for index in range(40)],
        "materials": [_item("materials", 200 + index, 10 + index)
                      for index in range(40)],
    }
    with pytest.raises(EvaluationError, match="balanced"):
        balanced_sample(candidates, size=60, seed=1028)


def test_sample_manifest_contains_identity_but_no_source_sentence() -> None:
    from scripts.model_reasoning_eval import sample_manifest

    item = _item("biology", 1, 47)
    payload = sample_manifest([item], catalog_sha256="a" * 64, seed=1028)
    encoded = json.dumps(payload, ensure_ascii=False)
    assert payload["items"][0]["source_sha256"] == item.source_sha256
    assert "source_text" not in encoded
    assert "korean" not in encoded


@pytest.mark.parametrize(
    ("korean", "expected"),
    [
        ("1단계: 시료를 섞어요.", ()),
        ("1단계: 시료를 혼합합니다.", ("formal_style",)),
        ("1단계: 시료를 섞습니다.", ("formal_style",)),
        ("저희 시료를 확인해요.", ("author_voice",)),
        ("혼합하는 것이 요구돼요.", ("translationese",)),
        ("시료가 희끄무레해요.", ("forbidden_wording",)),
        ("귀하는 sample을 vortexing해요.",
         ("addresses_reader", "english_verb", "forbidden_wording")),
    ],
)
def test_style_proxy_names_only_mechanical_violations(korean, expected) -> None:
    from scripts.model_reasoning_eval import style_issues

    assert style_issues(korean) == expected


def test_response_shape_count_includes_glossary_and_sentence_calls() -> None:
    from collections import Counter
    from scripts.model_reasoning_eval import response_shape_failure_calls

    failures = Counter({
        "response_shape": 2, "empty_response": 1,
        "glossary_response_shape": 1, "glossary_timeout": 3,
    })
    assert response_shape_failure_calls(failures) == 4


def test_translation_dry_run_has_both_caps_and_never_builds_a_client(monkeypatch) -> None:
    from scripts import model_reasoning_eval as tool

    monkeypatch.setattr(tool, "load_sample", lambda *_: [
        tool.LoadedSample(_item("biology", 1, 10), object(), object()),
        tool.LoadedSample(_item("biology", 2, 20), object(), object()),
    ])
    monkeypatch.setattr(tool, "build_model_client", lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("dry run crossed the provider boundary")))

    plan = tool.translation_plan(
        data_dir=tool.Path("/copy"), manifest=tool.Path("/manifest"), mode="spot",
        max_calls=3, max_usd=1.5, reserve_usd=0.25,
        input_rate=5, output_rate=15,
    )
    assert plan["external_requests_this_dry_run"] == 0
    assert plan["logical_calls_at_most"] == 2
    assert plan["call_cap"] == 3
    assert plan["reserved_usd_cap"] == 1.5


def test_analysis_capture_keeps_usage_and_raw_answer_out_of_public_row() -> None:
    from types import SimpleNamespace
    from scripts.model_reasoning_eval import CallCapReached, CaptureClient

    response = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=25,
                              total_tokens=125, reasoning_tokens=5,
                              prompt_tokens_details=None),
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"private":"answer"}'))],
    )
    inner = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **_kwargs: response)))
    capture = CaptureClient(inner, max_calls=1)

    assert capture.chat.completions.create(model="fake") is response
    public = capture.records[0].public_dict()
    assert public["usage"]["prompt_tokens"] == 100
    assert public["usage"]["reasoning_tokens"] == 5
    assert "private" not in json.dumps(public)
    assert capture.records[0].content == '{"private":"answer"}'
    with pytest.raises(CallCapReached):
        capture.chat.completions.create(model="fake")


def test_async_capture_counts_a_cancelled_transport_and_stopped_cap() -> None:
    import asyncio
    from types import SimpleNamespace
    from scripts import model_reasoning_eval as tool

    async def wait_forever(**_request):
        await asyncio.Event().wait()

    inner = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=wait_forever)))
    measured = tool._Capture(1)
    client = tool.AsyncCaptureClient(inner, measured)

    async def run() -> None:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(
                client.chat.completions.create(model="fake"), timeout=.001)
        second_error = None
        try:
            await asyncio.wait_for(
                client.chat.completions.create(model="fake"), timeout=.001)
        except BaseException as exc:  # assertion below distinguishes cap from timeout
            second_error = exc
        assert isinstance(second_error, tool.CallCapReached)

    asyncio.run(run())
    summary = tool._capture_summary(measured, input_rate=1, output_rate=1)
    assert summary["logical_calls"] == 1
    assert summary["usage_missing_calls"] == 1
    assert summary["logical_call_cap_stopped"] is True
    assert measured.records[0].status == "error:CancelledError"


def test_translation_execution_requires_explicit_comparison_reasoning(
    monkeypatch, tmp_path,
) -> None:
    from scripts import model_reasoning_eval as tool

    monkeypatch.setenv("VOINEY_LAB_TRANSLATION_PROVIDER", "openai")
    monkeypatch.setenv("VOINEY_LAB_TRANSLATION_MODEL", "fake")
    monkeypatch.delenv("VOINEY_LAB_TRANSLATION_REASONING", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "fake")
    monkeypatch.setattr(tool, "load_sample", lambda *_a, **_k: [])
    monkeypatch.setattr(tool, "_checked_report_path", lambda path: path)
    monkeypatch.setattr(
        tool, "build_model_client",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("unset reasoning built a provider client")),
    )

    with pytest.raises(tool.EvaluationError, match="explicit low, medium, or high"):
        tool.execute_translation(
            data_dir=tmp_path, manifest=tmp_path / "manifest.json", mode="batch",
            output_dir=tmp_path / "output", max_calls=1, max_usd=1,
            reserve_usd=.1, input_rate=1, output_rate=1,
        )


def test_analysis_accuracy_summary_compares_the_condition() -> None:
    from scripts.model_reasoning_eval import analysis_accuracy_summary

    summary = analysis_accuracy_summary([
        {"accuracy": {
            "reference_steps": 3, "candidate_steps": 3,
            "step_count_matches": True, "order_matches": True,
            "missing_labels": [], "extra_labels": [],
            "mean_text_similarity": .8, "steps_compared": 3,
            "steps_with_matching_values": 2,
            "steps_with_contradicted_values": 1,
            "steps_unscorable_on_values": 0,
        }},
        {"accuracy": {
            "reference_steps": 2, "candidate_steps": 1,
            "step_count_matches": False, "order_matches": False,
            "missing_labels": ["2"], "extra_labels": ["x"],
            "mean_text_similarity": .6, "steps_compared": 1,
            "steps_with_matching_values": 1,
            "steps_with_contradicted_values": 0,
            "steps_unscorable_on_values": 1,
        }},
        {"accuracy": None},
    ])

    assert summary == {
        "scored_protocols": 2,
        "step_count_match_rate": .5,
        "order_match_rate": .5,
        "protocol_mean_text_similarity": .7,
        "reference_steps": 5,
        "candidate_steps": 4,
        "missing_labels": 1,
        "extra_labels": 1,
        "steps_compared": 4,
        "steps_with_matching_values": 3,
        "steps_with_contradicted_values": 1,
        "steps_unscorable_on_values": 1,
    }


def test_analysis_execution_refuses_piecewise_plan_before_provider_boundary(
    monkeypatch, tmp_path,
) -> None:
    from scripts import model_reasoning_eval as tool

    monkeypatch.setenv("VOINEY_LAB_ANALYSIS_PROVIDER", "openai")
    monkeypatch.setenv("VOINEY_LAB_ANALYSIS_MODEL", "fake")
    monkeypatch.setenv("VOINEY_LAB_ANALYSIS_REASONING", "low")
    monkeypatch.setenv("OPENAI_API_KEY", "fake")
    monkeypatch.setattr(tool, "_checked_copy", lambda path: path)
    monkeypatch.setattr(tool, "analysis_plan", lambda **_kwargs: {
        "cases": [{"protocol_id": "long-protocol", "pieces": 2}],
    })
    monkeypatch.setattr(
        tool, "initialize_protocol_store",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("piecewise plan crossed the provider boundary")),
    )
    monkeypatch.setattr(
        tool, "build_model_client",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("piecewise plan built a provider client")),
    )

    with pytest.raises(tool.EvaluationError, match="single-pass"):
        tool.execute_analysis(
            data_dir=tmp_path, cases=[("long-protocol", tmp_path / "reference.json")],
            output_dir=tmp_path / "output", max_calls=2, max_usd=1,
            reserve_usd=.1, input_rate=1, output_rate=1, timeout=1,
        )


def test_analysis_card_count_distinguishes_no_cards_from_no_result() -> None:
    from types import SimpleNamespace
    from scripts.model_reasoning_eval import analysis_card_count

    assert analysis_card_count(None) is None
    assert analysis_card_count(SimpleNamespace(review_pending=None)) == 0
    assert analysis_card_count(SimpleNamespace(
        review_pending={"needs_review_count": 3})) == 3


def test_spot_evaluation_uses_server_request_and_production_check(monkeypatch) -> None:
    import asyncio
    from types import SimpleNamespace
    from scripts import model_reasoning_eval as tool
    from scripts.sim.corpus import load_corpus
    from voiney_lab.model_providers import RoleModel
    from voiney_lab.protocol_translation import translation_units

    fixture = load_corpus()[0].fixture
    unit = next(item for item in translation_units(fixture)
                if item.kind == "step" and item.step_label)
    item = tool.SampleItem(
        group="biology", protocol_id=fixture.protocol_id,
        fact_key=unit.fact_key, source_sha256=tool._source_sha256(unit.source_text),
        source_chars=len(unit.source_text), length_band="short")
    requests = []

    class FakeClient:
        def __init__(self):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        async def create(self, **request):
            requests.append(request)
            response = SimpleNamespace(
                usage=SimpleNamespace(prompt_tokens=100, completion_tokens=10,
                                      total_tokens=110, prompt_tokens_details=None),
                choices=[SimpleNamespace(message=SimpleNamespace(
                    content=json.dumps({
                        "korean": f"{unit.step_label}단계: 원문을 확인해요."})))],
            )
            return response

        async def close(self):
            return None

    capture = tool._Capture(1)
    monkeypatch.setattr(
        tool, "build_model_client",
        lambda *_a, **_k: tool.AsyncCaptureClient(FakeClient(), capture))
    rows, failures, latencies = asyncio.run(tool._run_translation(
        [tool.LoadedSample(item, fixture, unit)], mode="spot",
        role=RoleModel("translation", "google", "fake", "low"), capture=capture))

    assert requests[0]["response_format"]["json_schema"]["name"] == \
        "protocol_step_reader_translation_v1"
    assert rows[0]["check"] != "missing"
    assert not failures
    assert len(latencies) == 1
    assert "원문을 확인해요" not in json.dumps(rows, ensure_ascii=False)


def test_batch_evaluation_uses_glossary_batch_prompt_and_production_check(monkeypatch) -> None:
    import asyncio
    from types import SimpleNamespace
    from scripts import model_reasoning_eval as tool
    from scripts.sim.corpus import load_corpus
    from voiney_lab.model_providers import RoleModel
    from voiney_lab.protocol_translation import translation_units

    fixture = load_corpus()[0].fixture
    unit = next(item for item in translation_units(fixture)
                if item.kind == "step" and item.step_label)
    item = tool.SampleItem(
        group="biology", protocol_id=fixture.protocol_id,
        fact_key=unit.fact_key, source_sha256=tool._source_sha256(unit.source_text),
        source_chars=len(unit.source_text), length_band="short")
    schema_names = []

    class FakeClient:
        def __init__(self):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        async def create(self, **request):
            name = request["response_format"]["json_schema"]["name"]
            schema_names.append(name)
            payload = ({"entries": []} if "glossary" in name else {
                "items": [{"id": unit.fact_key,
                           "korean": f"{unit.step_label}단계: 원문을 확인해요."}]})
            return SimpleNamespace(
                usage=SimpleNamespace(prompt_tokens=100, completion_tokens=10,
                                      total_tokens=110, prompt_tokens_details=None),
                model="fake", choices=[SimpleNamespace(message=SimpleNamespace(
                    content=json.dumps(payload), refusal=None))])

        async def close(self):
            return None

    capture = tool._Capture(2)
    monkeypatch.setattr(
        tool, "build_model_client",
        lambda *_a, **_k: tool.AsyncCaptureClient(FakeClient(), capture))
    rows, failures, latencies = asyncio.run(tool._run_translation(
        [tool.LoadedSample(item, fixture, unit)], mode="batch",
        role=RoleModel("translation", "google", "fake", "low"), capture=capture))

    assert schema_names == [
        "protocol_translation_glossary_v1", "protocol_revision_translation_v2"]
    assert rows[0]["check"] != "missing"
    assert not failures
    assert len(latencies) == 1
    assert "원문을 확인해요" not in json.dumps(rows, ensure_ascii=False)
