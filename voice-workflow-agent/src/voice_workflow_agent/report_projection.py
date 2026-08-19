"""Typed domain projection adapter for experiment reports.

Converts ExperimentProtocol domain entities into clean, report-compatible
data structures without scattered duck-typing or domain schema drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from voice_workflow_agent.experiment_protocol import (
    BeforeStartPrerequisite,
    Equipment,
    ExperimentProtocol,
    Material,
    ProtocolMetadata,
    ProtocolSection,
    ProtocolSourceStep,
    ProtocolSubAction,
    SourceStatement,
)


@dataclass(frozen=True)
class ProjectedStepData:
    """Immutable projected step attributes for lab reporting."""

    step_id: str
    step_label: str
    section_title: str
    instruction_source_text: str
    sub_actions: tuple[str, ...]
    quantities: tuple[str, ...]
    conditions: tuple[str, ...]
    expected_results: tuple[str, ...]
    warnings: tuple[str, ...]
    notes: tuple[str, ...]
    tips: tuple[str, ...]
    source_page: int
    evidence_ids: tuple[str, ...]

    @property
    def instruction(self) -> str:
        return self.instruction_source_text


@dataclass(frozen=True)
class ProjectedProtocolData:
    """Immutable projected protocol metadata for lab reporting."""

    protocol_id: str
    title: str
    objective: str
    materials: tuple[str, ...]
    equipment: tuple[str, ...]
    prerequisites: tuple[str, ...]
    steps: tuple[ProjectedStepData, ...]


def _extract_text(obj: Any) -> str:
    if obj is None:
        return ""
    if hasattr(obj, "source_text") and obj.source_text:
        return str(obj.source_text).strip()
    if hasattr(obj, "text") and obj.text:
        return str(obj.text).strip()
    if hasattr(obj, "name_source_text") and obj.name_source_text:
        return str(obj.name_source_text).strip()
    if hasattr(obj, "name") and obj.name:
        return str(obj.name).strip()
    if isinstance(obj, str):
        return obj.strip()
    return str(obj).strip()


def project_step_for_report(
    section: ProtocolSection | Any,
    step: ProtocolSourceStep | Any,
    step_index: int = 0,
) -> ProjectedStepData:
    """Project one domain ProtocolSourceStep into a clean report data object."""
    step_id = str(getattr(step, "step_id", f"step-{step_index + 1}"))
    step_label = str(getattr(step, "source_label", str(step_index + 1)))

    section_title = _extract_text(
        getattr(section, "title_source_text", None) or getattr(section, "title", "Experiment Execution")
    ) or "Experiment Execution"

    instruction = (
        _extract_text(getattr(step, "instruction_source_text", None))
        or _extract_text(getattr(step, "instruction", None))
        or f"Step {step_label} instruction."
    )

    sub_actions: list[str] = []
    quantities: list[str] = []
    conditions: list[str] = []
    expected_results: list[str] = []
    warnings: list[str] = []
    notes: list[str] = []
    tips: list[str] = []

    # Step-level expected results, warnings, notes, tips
    for exp in getattr(step, "expected_results", ()) or ():
        text = _extract_text(exp)
        if text:
            expected_results.append(text)

    for w in getattr(step, "warnings", ()) or ():
        text = _extract_text(w)
        if text:
            warnings.append(text)

    for n in getattr(step, "notes", ()) or ():
        text = _extract_text(n)
        if text:
            notes.append(text)

    for t in getattr(step, "tips", ()) or ():
        text = _extract_text(t)
        if text:
            tips.append(text)

    # Sub-actions
    for sub in getattr(step, "sub_actions", ()) or ():
        sub_text = _extract_text(getattr(sub, "instruction_source_text", None) or getattr(sub, "text", None))
        if sub_text:
            sub_actions.append(sub_text)

        for q in getattr(sub, "quantities", ()) or ():
            q_text = _extract_text(q)
            if q_text:
                quantities.append(q_text)

        for c in getattr(sub, "conditions", ()) or ():
            c_text = _extract_text(c)
            if c_text:
                conditions.append(c_text)

        for exp in getattr(sub, "expected_results", ()) or ():
            text = _extract_text(exp)
            if text and text not in expected_results:
                expected_results.append(text)

        for w in getattr(sub, "warnings", ()) or ():
            text = _extract_text(w)
            if text and text not in warnings:
                warnings.append(text)

        for n in getattr(sub, "notes", ()) or ():
            text = _extract_text(n)
            if text and text not in notes:
                notes.append(text)

        for t in getattr(sub, "tips", ()) or ():
            text = _extract_text(t)
            if text and text not in tips:
                tips.append(text)

    # Source page
    source_page = 1
    if hasattr(step, "evidence") and hasattr(step.evidence, "source_page_number"):
        source_page = int(step.evidence.source_page_number or 1)
    elif hasattr(step, "source_page"):
        source_page = int(getattr(step, "source_page", 1) or 1)

    evidence_ids = (step_id,)

    return ProjectedStepData(
        step_id=step_id,
        step_label=step_label,
        section_title=section_title,
        instruction_source_text=instruction,
        sub_actions=tuple(sub_actions),
        quantities=tuple(quantities),
        conditions=tuple(conditions),
        expected_results=tuple(expected_results),
        warnings=tuple(warnings),
        notes=tuple(notes),
        tips=tuple(tips),
        source_page=source_page,
        evidence_ids=evidence_ids,
    )


def project_protocol_for_report(protocol: ExperimentProtocol | Any) -> ProjectedProtocolData:
    """Project a full domain ExperimentProtocol into report-ready structures."""
    protocol_id = str(
        getattr(protocol, "protocol_id", None)
        or getattr(getattr(protocol, "metadata", None), "protocol_id", "unknown_protocol")
    )

    metadata = getattr(protocol, "metadata", None)
    title = str(getattr(metadata, "title", None) or protocol_id)

    # Objective: from protocol.description
    objective = ""
    desc = getattr(protocol, "description", None)
    if desc is not None:
        objective = _extract_text(desc)
    if not objective and metadata is not None:
        objective = _extract_text(getattr(metadata, "description", None))
    if not objective:
        objective = f"본 실험 세션의 목적은 '{title}' 지침에 따라 표준화된 실험 절차를 수행하고 검증된 실험 데이터를 기록하는 것이다."

    # Materials
    materials: list[str] = []
    for m in getattr(protocol, "materials", ()) or ():
        m_name = _extract_text(m)
        if m_name:
            m_quants = [_extract_text(q) for q in getattr(m, "quantities", ()) if _extract_text(q)]
            if m_quants:
                materials.append(f"{m_name} ({', '.join(m_quants)})")
            else:
                materials.append(m_name)

    # Equipment
    equipment: list[str] = []
    for e in getattr(protocol, "equipment", ()) or ():
        e_name = _extract_text(e)
        if e_name:
            e_settings = [_extract_text(s) for s in getattr(e, "settings", ()) if _extract_text(s)]
            if e_settings:
                equipment.append(f"{e_name} ({', '.join(e_settings)})")
            else:
                equipment.append(e_name)

    # Prerequisites
    prerequisites: list[str] = []
    for p in getattr(protocol, "before_start", ()) or ():
        p_text = _extract_text(p)
        if p_text:
            prerequisites.append(p_text)

    # Steps
    projected_steps: list[ProjectedStepData] = []
    step_idx = 0
    for sec in getattr(protocol, "sections", ()) or ():
        for stp in getattr(sec, "steps", ()) or ():
            projected_steps.append(project_step_for_report(sec, stp, step_idx))
            step_idx += 1

    return ProjectedProtocolData(
        protocol_id=protocol_id,
        title=title,
        objective=objective,
        materials=tuple(materials),
        equipment=tuple(equipment),
        prerequisites=tuple(prerequisites),
        steps=tuple(projected_steps),
    )
