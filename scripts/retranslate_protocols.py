#!/usr/bin/env python3
"""Make the stored protocols' Korean again under the current translation style (lane KO2).

Every stored machine translation and glossary names the version of the
translation instructions it was made with (``protocol_translation.STYLE_VERSION``;
rows from before versions were kept are version 0). A protocol analysed
before the current version keeps its old Korean -- the 합니다체 of lane GE,
"귀하", sentences the check refused -- until it is translated again. This tool
does that for the protocols already in a data folder:

    python scripts/retranslate_protocols.py --data-dir DIR            # plan only, no call
    python scripts/retranslate_protocols.py --data-dir DIR --run      # translate

Nothing here is a second path. The sentences, the batches, the glossary, the
check and the stored rows are the server's own
(``protocol_translation.generate_revision_translations``, the translation
role's model, ``WorkspaceStore.record_fact_translations``):

- **Only what the current version lacks.** A sentence already stored under the
  current version (passed or refused) is not asked again; one stored only
  under an older version -- refused ones included -- is. A sentence a person
  confirmed (a reviewed translation) is never made again.
- **Append-only.** New rows go beside the old ones; nothing is rewritten or
  deleted. The server shows, sentence by sentence, the newest version's row
  that passes today's check, else an older one that passes, else the source.
- **The fixture a session runs.** The latest registered review version, else
  the passed analysis (``ProtocolCatalog.load_executable_fixture``); a protocol
  with no runnable analysis is listed and skipped.
- **A plan first, a bounded bill.** Without ``--run`` it prints, per protocol,
  how many sentences the current version still lacks and the model calls a
  run would make (one glossary call when the version has no glossary yet,
  plus one call per batch of at most 8 sentences), and touches nothing.
  ``--max-calls`` stops sending once that many calls were made (a batch not
  sent is simply made by a later run).
- **A folder it is given.** ``--data-dir`` is the catalog folder
  (``protocol_workspace.sqlite`` and ``objects/``); ``--workspace-dir`` the
  workspace (default ``<data-dir>/workspace``). The repository's own
  ``data/runtime/`` is refused unless ``--allow-runtime-dir`` says that
  translating the pilot's folder is meant. Sessions already open keep the
  Korean they loaded; a session opened afterwards reads the new rows.

The translation role's settings and key come from the shell, as for the
server (``VOINEY_LAB_TRANSLATION_PROVIDER``/``_MODEL``/``_REASONING`` and the
provider's key). This tool does not read ``.env``. Its output and its report
hold numbers and protocol names only, never a sentence -- unless
``--compare N`` asks for N sentence pairs (old Korean, new Korean, check) per
protocol in ``comparison.md``, for a person to read.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings  # noqa: E402
from voiney_lab.experiment_protocol_store import initialize_protocol_store  # noqa: E402
from voiney_lab.model_providers import ProviderRetryPolicy, RoleModel, chat_client  # noqa: E402
from voiney_lab.protocol_catalog import ProtocolCatalog  # noqa: E402
from voiney_lab.protocol_translation import (  # noqa: E402
    BATCH_TIMEOUT_SECONDS,
    STYLE_VERSION,
    TARGET_LANGUAGE,
    generate_revision_translations,
    glossaries_by_version,
    openai_batch_translator,
    openai_glossary_maker,
    pending_units,
    planned_calls,
    stored_checks,
    translation_revision_key,
    translation_units,
    with_stored_translations,
)
from voiney_lab.workspace_store import (  # noqa: E402
    WorkspaceSettings,
    initialize_workspace_store,
)

RUNTIME_DIR = ROOT / "data" / "runtime"


class RetranslateError(RuntimeError):
    """The request is refused before anything is sent (exit status 2)."""


class CallCapReached(RuntimeError):
    """``--max-calls`` calls were made; nothing more is sent."""


@dataclass
class ProtocolPlan:
    protocol_id: str
    title: str
    revision_key: str = ""
    skipped: str = ""
    sentences: int = 0
    korean_source: int = 0
    reviewed: int = 0
    current: int = 0
    pending: int = 0
    glossary_calls: int = 0
    sentence_calls: int = 0
    #: Today's check of what is shown before and after: sentences shown in
    #: Korean, from the current version, and sentences whose rows all fail.
    shown_before: int = 0
    current_shown_before: int = 0
    refused_before: int = 0
    shown_after: int | None = None
    current_shown_after: int | None = None
    refused_after: int | None = None
    calls_made: int = 0
    failed_calls: int = 0
    rows_made: int = 0
    rows_passed: int = 0
    refusals: dict[str, int] = field(default_factory=dict)
    comparison: list[dict[str, str]] = field(default_factory=list)

    @property
    def calls(self) -> int:
        return self.glossary_calls + self.sentence_calls


def _refuse_runtime(path: Path, allowed: bool) -> None:
    try:
        Path(path).resolve().relative_to(RUNTIME_DIR.resolve())
    except ValueError:
        return
    if not allowed:
        raise RetranslateError(
            f"{path} 는 저장소의 data/runtime/ 안입니다. 파일럿 폴더를 다시 번역하려면 "
            "--allow-runtime-dir 를 더하세요.")


def _shown(fixture: Any, rows: Sequence[Any], glossaries: Mapping[int, Any]) -> tuple[int, int, int]:
    """(shown in Korean, of them from the current version, refused) by today's check."""

    attached = with_stored_translations(fixture, rows, glossaries=glossaries)
    shown = {*(attached.machine_localizations or {}), *(attached.localizations or {})}
    checked = stored_checks(fixture, rows, glossaries=glossaries)
    current = {
        record.fact_key for record, result in checked
        if result == "passed" and record.style_version == STYLE_VERSION}
    refused = {record.fact_key for record, result in checked if result != "passed"} - shown
    return len(shown), len(current & shown), len(refused)


def plan_protocol(catalog: ProtocolCatalog, store: Any, protocol_id: str, title: str) -> tuple[ProtocolPlan, Any]:
    plan = ProtocolPlan(protocol_id=protocol_id, title=title)
    try:
        fixture = catalog.load_executable_fixture(protocol_id)
    except Exception as exc:  # noqa: BLE001 - listed as not runnable
        plan.skipped = f"실행할 분석 없음 ({type(exc).__name__})"
        return plan, None
    key = translation_revision_key(fixture)
    plan.revision_key = key
    rows = store.fact_translations(key, TARGET_LANGUAGE)
    glossaries = glossaries_by_version(store.translation_glossaries(key, TARGET_LANGUAGE))
    pending, plan.reviewed, plan.korean_source, plan.current = pending_units(fixture, rows)
    plan.sentences = len(translation_units(fixture))
    plan.pending = len(pending)
    plan.glossary_calls, plan.sentence_calls = planned_calls(
        pending, glossary_held=STYLE_VERSION in glossaries)
    plan.shown_before, plan.current_shown_before, plan.refused_before = _shown(fixture, rows, glossaries)
    return plan, fixture


def _counted(function: Callable[..., Any], counter: dict[str, int], cap: int | None) -> Callable[..., Any]:
    async def call(*args: Any) -> Any:
        if cap is not None and counter["calls"] >= cap:
            raise CallCapReached()
        counter["calls"] += 1
        return await function(*args)
    return call


def run_protocol(
    plan: ProtocolPlan, fixture: Any, store: Any, *,
    translate: Callable[..., Any], make_glossary: Callable[..., Any], model: str,
    counter: dict[str, int], cap: int | None, compare: int,
) -> None:
    key = plan.revision_key
    before_rows = store.fact_translations(key, TARGET_LANGUAGE)
    before_glossaries = glossaries_by_version(store.translation_glossaries(key, TARGET_LANGUAGE))
    old = with_stored_translations(fixture, before_rows, glossaries=before_glossaries)
    start = counter["calls"]

    def on_batch(rows: list[Any]) -> None:
        store.record_fact_translations(rows)

    report = asyncio.run(generate_revision_translations(
        fixture, _counted(translate, counter, cap), model=model, stored=before_rows,
        glossary=store.translation_glossary(key, TARGET_LANGUAGE, style_version=STYLE_VERSION),
        make_glossary=_counted(make_glossary, counter, cap),
        on_glossary=store.record_translation_glossary, on_batch=on_batch,
        max_calls=None if cap is None else max(0, cap - start)))
    plan.calls_made = counter["calls"] - start
    plan.failed_calls = report.failed_calls
    plan.rows_made = len(report.records)
    plan.rows_passed = report.passed
    for record in report.refused:
        plan.refusals[record.check_result] = plan.refusals.get(record.check_result, 0) + 1
    rows = store.fact_translations(key, TARGET_LANGUAGE)
    glossaries = glossaries_by_version(store.translation_glossaries(key, TARGET_LANGUAGE))
    plan.shown_after, plan.current_shown_after, plan.refused_after = _shown(fixture, rows, glossaries)
    if compare:
        sources = {unit.fact_key: unit.source_text for unit in translation_units(fixture)}
        for record in report.records[:compare]:
            plan.comparison.append({
                "fact_key": record.fact_key,
                "source": " ".join(sources.get(record.fact_key, "").split()),
                "old": (old.localized_fact(*record.fact_key.split("/", 1)) or ""),
                "new": record.translated_text,
                "check": record.check_result,
            })


def _table(plans: Sequence[ProtocolPlan], ran: bool) -> str:
    lines = [
        f"번역 지침 판 v{STYLE_VERSION}",
        "",
        "| 프로토콜 | 문장 | 원문이 한국어 | 사람이 확인한 번역 | 이미 v{0} | 다시 만들 문장 | 호출(용어집+묶음) | 지금 한국어로 보임 (v{0} 것) | 검사에 걸림 |".format(STYLE_VERSION),
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for plan in plans:
        if plan.skipped:
            lines.append(f"| {plan.title} | {plan.skipped} | | | | | | | |")
            continue
        lines.append(
            f"| {plan.title} | {plan.sentences} | {plan.korean_source} | {plan.reviewed} | {plan.current} | "
            f"{plan.pending} | {plan.calls} ({plan.glossary_calls}+{plan.sentence_calls}) | "
            f"{plan.shown_before} ({plan.current_shown_before}) | {plan.refused_before} |")
    total = sum(plan.calls for plan in plans)
    lines += ["", f"합계: 다시 만들 문장 {sum(plan.pending for plan in plans)} · 호출 {total}회"]
    lines += ["", f"번역 묶음 제한 시간: {BATCH_TIMEOUT_SECONDS:g}초. 시간 초과 묶음은 반으로 나눠 재시도해 호출이 늘 수 있어요.",
              "--max-calls 는 용어집·분할 재시도를 합친 상한이에요. 이 도구는 공급자 내부 재시도를 끄고 보낸 호출을 세요."]
    if ran:
        lines += [
            "",
            "| 프로토콜 | 보낸 호출 | 실패한 호출 | 만든 행 | 검사 통과 | 검사에 걸린 까닭 | 한국어로 보임 전 → 후 (v{0} 것) | 검사에 걸림 전 → 후 |".format(STYLE_VERSION),
            "|---|---|---|---|---|---|---|---|",
        ]
        for plan in plans:
            if plan.skipped or plan.shown_after is None:
                continue
            reasons = ", ".join(f"{name} {count}" for name, count in sorted(plan.refusals.items())) or "-"
            lines.append(
                f"| {plan.title} | {plan.calls_made} | {plan.failed_calls} | {plan.rows_made} | "
                f"{plan.rows_passed} | {reasons} | {plan.shown_before} → {plan.shown_after} "
                f"({plan.current_shown_before} → {plan.current_shown_after}) | "
                f"{plan.refused_before} → {plan.refused_after} |")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="저장된 프로토콜의 한국어 번역을 지금 번역 지침 판으로 다시 만든다(덧붙이기만).")
    parser.add_argument("--data-dir", type=Path, required=True, help="프로토콜 카탈로그 폴더")
    parser.add_argument("--workspace-dir", type=Path, help="작업공간 폴더(기본: <data-dir>/workspace)")
    parser.add_argument("--protocol", action="append", default=[], help="이 프로토콜만(여러 번 쓸 수 있음)")
    parser.add_argument("--run", action="store_true", help="실제로 번역한다(없으면 계획만 보이고 호출하지 않음)")
    parser.add_argument("--max-calls", type=int, help="보낼 모델 호출의 상한")
    parser.add_argument("--report-dir", type=Path, help="결과 표(retranslate_result.md/json)를 쓸 폴더")
    parser.add_argument("--compare", type=int, default=0, help="프로토콜마다 옛·새 번역 N쌍을 comparison.md 에 쓴다")
    parser.add_argument("--allow-runtime-dir", action="store_true", help="저장소 data/runtime/ 안의 폴더를 허락한다")
    return parser


def main(
    argv: Sequence[str] | None = None, *,
    translator_factory: Callable[[RoleModel], tuple[Callable[..., Any], Callable[..., Any]]] | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    data_dir = args.data_dir.expanduser()
    workspace_dir = (args.workspace_dir or data_dir / "workspace").expanduser()
    try:
        for path in (data_dir, workspace_dir):
            _refuse_runtime(path, args.allow_runtime_dir)
        if not (data_dir / "protocol_workspace.sqlite").is_file():
            raise RetranslateError(f"{data_dir} 에 protocol_workspace.sqlite 가 없습니다.")
        if not (workspace_dir / "commercial_workspace.sqlite").is_file():
            raise RetranslateError(f"{workspace_dir} 에 commercial_workspace.sqlite 가 없습니다.")
        if args.max_calls is not None and args.max_calls < 0:
            raise RetranslateError("--max-calls 는 0 이상이어야 합니다.")
        role = RoleModel.from_environment("translation")
        if args.run and translator_factory is None and not role.has_key():
            raise RetranslateError(
                "번역 역할의 공급자·키가 셸에 없습니다(VOINEY_LAB_TRANSLATION_PROVIDER 와 그 키). "
                "이 도구는 .env 를 읽지 않습니다.")
    except (RetranslateError, ValueError) as exc:
        print(f"거절: {exc}", file=sys.stderr)
        return 2
    protocol_store = initialize_protocol_store(ProtocolPersistenceSettings(True, data_dir))
    store = initialize_workspace_store(WorkspaceSettings(True, workspace_dir))
    try:
        catalog = ProtocolCatalog(protocol_store)
        catalog.prepare_source_metadata()
        wanted = set(args.protocol)
        entries = [
            entry for entry in catalog.list_entries()
            if not wanted or entry.protocol_id in wanted]
        plans: list[tuple[ProtocolPlan, Any]] = []
        for entry in entries:
            name = str(entry.public_dict().get("title") or "").split("\n")[0][:60]
            title = f"{name} · {entry.protocol_id}" if name else entry.protocol_id
            plans.append(plan_protocol(catalog, store, entry.protocol_id, title))
        if args.run:
            if translator_factory is not None:
                translate, make_glossary = translator_factory(role)
            else:
                def client() -> Any:
                    return chat_client(
                        role, asynchronous=True, timeout=BATCH_TIMEOUT_SECONDS,
                        max_retries=0, retry_policy=ProviderRetryPolicy(max_retries=0))
                translate = openai_batch_translator(client, str(role.model))
                make_glossary = openai_glossary_maker(client, str(role.model))
            counter = {"calls": 0}
            for plan, fixture in plans:
                if fixture is None or not plan.pending:
                    plan.shown_after = plan.shown_before
                    plan.current_shown_after = plan.current_shown_before
                    plan.refused_after = plan.refused_before
                    continue
                run_protocol(
                    plan, fixture, store, translate=translate, make_glossary=make_glossary,
                    model=str(role.model), counter=counter, cap=args.max_calls,
                    compare=max(0, args.compare))
        text = _table([plan for plan, _ in plans], args.run)
        print(text, end="")
        if args.report_dir is not None:
            args.report_dir.mkdir(parents=True, exist_ok=True)
            (args.report_dir / "retranslate_result.md").write_text(text, encoding="utf-8")
            (args.report_dir / "retranslate_result.json").write_text(json.dumps(
                [{k: v for k, v in plan.__dict__.items() if k != "comparison"} for plan, _ in plans],
                ensure_ascii=False, indent=1), encoding="utf-8")
            pairs = [(plan, item) for plan, _ in plans for item in plan.comparison]
            if pairs:
                lines = ["| 프로토콜 | 문장 | 원문 | 옛 번역 | 새 번역 (검사) |", "|---|---|---|---|---|"]
                for plan, item in pairs:
                    cells = [plan.title, item["fact_key"], item["source"], item["old"],
                             f"{item['new']} ({item['check']})"]
                    lines.append("| " + " | ".join(cell.replace("|", "/") for cell in cells) + " |")
                (args.report_dir / "comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    finally:
        store.close()
        protocol_store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
