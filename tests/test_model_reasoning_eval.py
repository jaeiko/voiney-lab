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
        ("귀하는 sample을 vortexing해요.",
         ("addresses_reader", "english_verb", "forbidden_wording")),
    ],
)
def test_style_proxy_names_only_mechanical_violations(korean, expected) -> None:
    from scripts.model_reasoning_eval import style_issues

    assert style_issues(korean) == expected


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
