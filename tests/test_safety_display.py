"""What the step safety card may say.

Every card line is a source's own words: the step's protocol PDF warning, or
an excerpt of an approved safety document's section, whole sentences only.
The Korean card adds a translation only where the catalog holds a
human-reviewed one. None of this needs the licensed Candidate A PDF, so it
also runs in CI (condition B).
"""

import json
import tempfile
import unittest
from pathlib import Path

from voiney_lab.document_store import ingest_manifest
from voiney_lab.experiment_protocol import (
    Equipment,
    ExperimentProtocol,
    Material,
    ProtocolMetadata,
    ProtocolSection,
    ProtocolSourceStep,
    SourceEvidence,
    SourceStatement,
)
from voiney_lab.experiment_protocol_pdf import (
    ProtocolPdfExtraction,
    ProtocolPdfMetadata,
    ProtocolPdfPage,
)
from voiney_lab.safety_pack import resolve_safety_pack

ROOT = Path(__file__).resolve().parents[1]
DEMO_SAFETY_MANUAL = ROOT / "data/fixtures/approved_safety_manual.demo.json"

TYPE_LABELS = {"facility_sop": "안전 SOP", "supplier_sds": "물질 SDS", "equipment_manual": "장비 매뉴얼"}
TRANSLATION_PREFIX = "  검토된 한국어 번역: "
OMISSION = " …"

# One warning per wording the old card rewrote into a sentence of its own.
WARNINGS = (
    "Toxic solvent: clean any spill immediately.",
    "Leak hazard near the bench.",
    "Harmful if inhaled. Irritant to the eyes.",
    "Always wear gloves and goggles.",
    "Wear gloves and a respirator mask.",
    "Wear gloves.",
    "Eye protection is required.",
    "Work in a fume hood with ventilation.",
    "Protect from light and keep in the dark.",
    "Balance the centrifuge tubes.",
    "Dust and keratin ruin the sample; use a fresh scalpel and a clean surface.",
    "Dust settles on uncovered gels.",
    "Note the time.",
    "용매가 새지 않도록 뚜껑을 닫는다.",
)

# Sentences the old card showed although no source said them.
INVENTED = (
    "방제 절차",
    "피부 접촉과 증기 흡입",
    "실험용 장갑",
    "보호 마스크",
    "흄 후드(환기 장치)",
    "차광 상태",
    "튜브 균형을 맞춘",
    "오염 방지",
    "적절한 실험실 보호구",
    "승인된 실험실 안전 기준",
)

LONG_SPILL = (
    "Contain the spill with absorbent pads from the spill kit and keep others away from the area. "
    "Do not wash a solvent spill into the sink, and do not use paper towels on an oxidizer. "
    "Notify the lab manager before anyone resumes work at the bench, and record the event. "
    "Dispose of the pads as hazardous waste unless the manager says otherwise."
)
MACHINE_TRANSLATION = "기계 번역 문장: 이 문장은 검토되지 않았습니다."


def _evidence() -> SourceEvidence:
    return SourceEvidence(source_page_number=1, source_excerpt="excerpt")


def _protocol(
    steps: list[tuple[str, tuple[str, ...]]],
    materials: tuple[str, ...],
    equipment: tuple[str, ...] = ("Centrifuge 5424 R",),
) -> ExperimentProtocol:
    pdf = ProtocolPdfExtraction(
        original_filename="card.pdf",
        byte_size=1024,
        sha256="0" * 64,
        media_type="application/pdf",
        page_count=1,
        encrypted=False,
        metadata=ProtocolPdfMetadata(
            title="Card",
            author=None,
            subject=None,
            creator=None,
            producer=None,
            creation_date=None,
            modification_date=None,
        ),
        pages=(ProtocolPdfPage(source_page_number=1, text="Card page", text_empty=False),),
    )
    return ExperimentProtocol(
        protocol_id="SAFETY-CARD-001",
        metadata=ProtocolMetadata(pdf=pdf, title="Card Protocol", original_language="en"),
        materials=tuple(
            Material(material_id=f"mat-{i}", name_source_text=name, evidence=_evidence())
            for i, name in enumerate(materials, 1)
        ),
        equipment=tuple(
            Equipment(equipment_id=f"eq-{i}", name_source_text=name, evidence=_evidence())
            for i, name in enumerate(equipment, 1)
        ),
        sections=(
            ProtocolSection(
                section_id="sec-1",
                title_source_text="Section 1",
                evidence=_evidence(),
                steps=tuple(
                    ProtocolSourceStep(
                        step_id=f"step-{i}",
                        source_label=str(i),
                        instruction_source_text=instruction,
                        evidence=_evidence(),
                        warnings=tuple(
                            SourceStatement(statement_id=f"w-{i}-{j}", source_text=w, evidence=_evidence())
                            for j, w in enumerate(warnings, 1)
                        ),
                    )
                    for i, (instruction, warnings) in enumerate(steps, 1)
                ),
            ),
        ),
    )


def _document(
    document_id: str,
    document_type: str,
    title: str,
    sections: list[tuple[str, str, str]],
    *,
    family: str | None = None,
    product_name: str | None = None,
    product_code: str | None = None,
    language: str = "en",
    translation_status: str = "original",
    translation_of: str | None = None,
    cas: tuple[str, ...] = (),
    aliases: tuple[tuple[str, bool, bool], ...] = (),
) -> dict:
    family = family or document_id
    return {
        "document_id": document_id,
        "document_family_id": f"{family}-FAMILY",
        "canonical_source_id": f"{family}-SOURCE",
        "canonical_version": "1.0",
        "document_type": document_type,
        "title": title,
        "issuer": "Main lab safety committee",
        "manufacturer": None,
        "product_name": product_name,
        "product_code": product_code,
        "cas_numbers": list(cas),
        "version": "1.0",
        "language": language,
        "facility_id": "MAIN-LAB",
        "source_authority": "supplier" if document_type == "supplier_sds" else "facility",
        "approval_status": "approved",
        "usage_scope": "reference_only",
        "source_uri": f"https://example.invalid/{document_id}",
        "source_checksum": f"sha256:{document_id.lower()}",
        "translation_status": translation_status,
        "translation_of_document_id": translation_of,
        "active": True,
        "sections": [
            {
                "section_code": code,
                "section_title": title,
                "page_start": 1,
                "page_end": 1,
                "content": content,
                "topic": topic,
            }
            for code, topic, content in sections
        ],
        "aliases": [
            {"alias": alias, "language": language, "approved": approved, "generic": generic}
            for alias, approved, generic in aliases
        ],
    }


def _catalog_documents() -> list[dict]:
    return [
        _document("SOP-SPILL", "facility_sop", "Solvent spill response", [("01", "spill", LONG_SPILL)]),
        _document(
            "SOP-PPE", "facility_sop", "Bench PPE",
            [("01", "ppe", "Wear nitrile gloves and safety glasses at the bench. Tie long hair back.")],
        ),
        _document(
            "SOP-GENERAL", "facility_sop", "General bench rules",
            [("01", "general", "Keep the bench clear. Report any toxic exposure to the lab manager.")],
        ),
        _document(
            "SDS-ACN", "supplier_sds", "Acetonitrile SDS",
            [("07", "handling", "Highly flammable liquid and vapour. Keep away from heat and sparks.")],
            product_name="Acetonitrile",
        ),
        _document(
            "SDS-ACN-KO", "supplier_sds", "아세토니트릴 SDS",
            [("07", "handling", "고인화성 액체 및 증기. 열과 불꽃으로부터 멀리하시오.")],
            family="SDS-ACN", product_name="Acetonitrile", language="ko",
            translation_status="human_reviewed", translation_of="SDS-ACN",
        ),
        _document(
            "SDS-FA", "supplier_sds", "Formic acid SDS",
            [("08", "ppe", "Causes severe skin burns. Wear protective gloves and eye protection.")],
            product_name="Formic acid",
        ),
        _document(
            "SDS-FA-KO", "supplier_sds", "포름산 SDS (기계 번역)",
            [("08", "ppe", MACHINE_TRANSLATION)],
            family="SDS-FA", product_name="Formic acid", language="ko",
            translation_status="machine_unreviewed", translation_of="SDS-FA",
        ),
        _document(
            "EQ-CENTRIFUGE", "equipment_manual", "Centrifuge 5424 R manual",
            [("03", "equipment_operation", "Load the rotor symmetrically. Never open the lid while the rotor turns.")],
            product_name="Centrifuge 5424 R",
        ),
    ]


def _steps() -> list[tuple[str, tuple[str, ...]]]:
    instructions = (
        "Mix acetonitrile with the buffer and the solvent.",
        # A manual reaches only a step that names its machine as the catalog does.
        "Add formic acid, then spin in the Centrifuge 5424 R.",
        "Wear gloves and cut the band with a scalpel.",
        "Incubate the plug.",
    )
    steps = []
    for index, warning in enumerate(WARNINGS):
        steps.append((instructions[index % len(instructions)], (warning,)))
    # Two warnings and every document matcher on one step.
    steps.append((" ".join(instructions), WARNINGS[:2]))
    return steps


def _whole_sentence_excerpt(text: str, source: str) -> bool:
    """Whether ``text`` is ``source`` itself or its leading whole sentences marked as cut."""
    source = " ".join(source.split())
    if text == source:
        return True
    if not text.endswith(OMISSION):
        return False
    kept = text[: -len(OMISSION)]
    return bool(kept) and source.startswith(kept) and kept[-1] in ".!?。" and source[len(kept)] == " "


class SafetyCardTextTests(unittest.TestCase):
    """Whatever the input, a card line is a source sentence or a reviewed translation."""

    def assert_card_lines_are_source_text(self, guidance, warnings, documents_by_head):
        lines = list(guidance.display_bullets) + list(guidance.localized_display_bullets)
        self.assertTrue(lines or not warnings)
        for bullet in lines:
            first, *rest = bullet.split("\n")
            self.assertTrue(first.startswith("• "), bullet)
            head, separator, text = first[2:].partition(": ")
            self.assertEqual(separator, ": ", bullet)
            if head == "주의":
                self.assertIn(text, {" ".join(w.split()) for w in warnings}, bullet)
                self.assertEqual(rest, [], bullet)
                continue
            self.assertIn(head, documents_by_head, bullet)
            originals, translations = documents_by_head[head]
            self.assertTrue(any(_whole_sentence_excerpt(text, s) for s in originals), bullet)
            for line in rest:
                self.assertTrue(line.startswith(TRANSLATION_PREFIX), bullet)
                translated = line[len(TRANSLATION_PREFIX):]
                self.assertTrue(any(_whole_sentence_excerpt(translated, s) for s in translations), bullet)
            # A reviewed translation shows only in the Korean card.
            if bullet in guidance.display_bullets:
                self.assertEqual(rest, [], bullet)
        for line in lines:
            for phrase in INVENTED:
                self.assertNotIn(phrase, line)

    def _documents_by_head(self, documents):
        originals = [d for d in documents if d["translation_status"] == "original"]
        reviewed = [d for d in documents if d["translation_status"] == "human_reviewed"]
        heads = {}
        for doc in originals:
            translations = [
                section["content"]
                for t in reviewed
                if t["translation_of_document_id"] == doc["document_id"]
                for section in t["sections"]
            ]
            heads[f"{TYPE_LABELS[doc['document_type']]} · {doc['title']}"] = (
                [section["content"] for section in doc["sections"]],
                translations,
            )
        return heads

    def test_catalog_documents_show_only_their_own_sentences(self):
        documents = _catalog_documents()
        steps = _steps()
        protocol = _protocol(steps, materials=("Acetonitrile", "Formic acid", "DTT"))
        heads = self._documents_by_head(documents)
        with tempfile.TemporaryDirectory() as temp_dir:
            catalog = Path(temp_dir) / "catalog.sqlite"
            ingest_manifest({"documents": documents}, catalog)
            packs = {
                scope: resolve_safety_pack(protocol, catalog, facility_id="MAIN-LAB", usage_scope=scope)
                for scope in ("reference_only", "operational")
            }
        documents_shown = set()
        for scope, pack in packs.items():
            for index, step in enumerate(protocol.sections[0].steps):
                with self.subTest(scope=scope, step=step.step_id):
                    guidance = pack.guidance_for_step(step, index)
                    self.assert_card_lines_are_source_text(guidance, steps[index][1], heads)
                    rendered = json.dumps(
                        {"pack": pack.public_dict(), "step": guidance.public_dict()}, ensure_ascii=False
                    )
                    self.assertNotIn(MACHINE_TRANSLATION, rendered)
                    documents_shown |= {d.document_id for d in guidance.applicable_documents}
        # Every document matched somewhere, so every one went through the check;
        # the translations fold into their originals instead of listing twice.
        self.assertEqual(
            documents_shown,
            {"SOP-SPILL", "SOP-PPE", "SOP-GENERAL", "SDS-ACN", "SDS-FA", "EQ-CENTRIFUGE"},
        )

    def test_a_reviewed_translation_shows_beside_its_original_and_only_then(self):
        documents = _catalog_documents()
        protocol = _protocol(
            [("Mix acetonitrile with formic acid in the solvent buffer.", ())],
            materials=("Acetonitrile", "Formic acid"),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            catalog = Path(temp_dir) / "catalog.sqlite"
            ingest_manifest({"documents": documents}, catalog)
            pack = resolve_safety_pack(protocol, catalog, facility_id="MAIN-LAB", usage_scope="reference_only")
        guidance = pack.guidance_for_step(protocol.sections[0].steps[0])
        acetonitrile = [b for b in guidance.localized_display_bullets if "Acetonitrile SDS" in b]
        self.assertEqual(
            acetonitrile,
            [
                "• 물질 SDS · Acetonitrile SDS: Highly flammable liquid and vapour. "
                "Keep away from heat and sparks.\n"
                "  검토된 한국어 번역: 고인화성 액체 및 증기. 열과 불꽃으로부터 멀리하시오."
            ],
        )
        self.assertIn(
            "• 물질 SDS · Acetonitrile SDS: Highly flammable liquid and vapour. Keep away from heat and sparks.",
            guidance.display_bullets,
        )
        # Its only translation is machine-made, so the formic acid line stays in English.
        self.assertIn(
            "• 물질 SDS · Formic acid SDS: Causes severe skin burns. Wear protective gloves and eye protection.",
            guidance.localized_display_bullets,
        )

    def test_a_long_section_is_cut_between_sentences_and_marked(self):
        documents = _catalog_documents()
        protocol = _protocol([("Clean any solvent spill.", ())], materials=())
        with tempfile.TemporaryDirectory() as temp_dir:
            catalog = Path(temp_dir) / "catalog.sqlite"
            ingest_manifest({"documents": documents}, catalog)
            pack = resolve_safety_pack(protocol, catalog, facility_id="MAIN-LAB", usage_scope="reference_only")
        guidance = pack.guidance_for_step(protocol.sections[0].steps[0])
        spill = [b for b in guidance.display_bullets if "Solvent spill response" in b]
        self.assertEqual(
            spill,
            [
                "• 안전 SOP · Solvent spill response: "
                "Contain the spill with absorbent pads from the spill kit and keep others away from the area. "
                "Do not wash a solvent spill into the sink, and do not use paper towels on an oxidizer. …"
            ],
        )

    def test_the_demo_records_show_only_their_own_sentences(self):
        records = json.loads(DEMO_SAFETY_MANUAL.read_text(encoding="utf-8"))
        heads = {}
        for record in records:
            ko = record["translations"]["ko"]
            for label in TYPE_LABELS.values():
                heads[f"{label} · {ko['title']}"] = ([ko["guidance"]], [])
        steps = _steps()
        protocol = _protocol(steps, materials=("Acetonitrile",))
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "no-catalog.sqlite"
            pack = resolve_safety_pack(protocol, missing, facility_id="MAIN-LAB", usage_scope="demo")
        self.assertEqual(pack.coverage_status, "demo_only")
        for index, step in enumerate(protocol.sections[0].steps):
            with self.subTest(step=step.step_id):
                self.assert_card_lines_are_source_text(
                    pack.guidance_for_step(step, index), steps[index][1], heads
                )


class SdsStepBindingTests(unittest.TestCase):
    """An SDS goes to the steps that name its own substance and to no other step."""

    def _resolve(self, steps):
        documents = [
            _document(
                "SDS-ACN", "supplier_sds", "Acetonitrile SDS",
                [("07", "handling", "Highly flammable liquid and vapour. Keep away from heat.")],
                product_name="Acetonitrile", cas=("75-05-8",),
                # A generic alias names no substance, so it binds nothing.
                aliases=(("ACN", True, False), ("solvent", True, True)),
            ),
            _document(
                "SDS-ACN-KO", "supplier_sds", "아세토니트릴 SDS",
                [("07", "handling", "고인화성 액체 및 증기. 열로부터 멀리하시오.")],
                family="SDS-ACN", product_name="아세토니트릴", language="ko",
                translation_status="human_reviewed", translation_of="SDS-ACN",
                aliases=(("아세토니트릴", True, False),),
            ),
            _document(
                "SDS-DTT", "supplier_sds", "DL-Dithiothreitol SDS",
                [("08", "ppe", "Causes skin irritation. Wear protective gloves.")],
                product_name="DL-Dithiothreitol", cas=("3483-12-3",),
                # An alias nobody approved binds nothing either.
                aliases=(("DTT", True, False), ("buffer", False, False)),
            ),
        ]
        protocol = _protocol(
            steps,
            materials=(
                "Acetonitrile LC-MS grade B&J Brand VWR International",
                "DTT Merck MilliporeSigma (Sigma-Aldrich) Catalog #D0632",
            ),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            catalog = Path(temp_dir) / "catalog.sqlite"
            ingest_manifest({"documents": documents}, catalog)
            pack = resolve_safety_pack(protocol, catalog, facility_id="MAIN-LAB", usage_scope="reference_only")
        return pack, protocol.sections[0].steps

    def test_two_substances_each_reach_only_their_own_steps(self):
        steps = [
            ("Wash the gel piece with 500 µL acetonitrile.", ()),
            ("Add enough volume of the DTT solution to fully cover the gel band.", ()),
            ("Prepare the buffer and the solvent, then add trypsin, AMBIC, IAA and formic acid.", ()),
            ("Mix ACN with the DTT solution.", ()),
            ("Use the reagent with CAS 3483-12-3 from the cold room.", ()),
            ("아세토니트릴을 넣고 섞는다.", ()),
            ("Label the WDTT tube and the ACNE box.", ()),
            ("Incubate the plug.", ("Acetonitrile vapour is flammable.",)),
        ]
        expected = [
            {"SDS-ACN"},
            {"SDS-DTT"},
            set(),
            {"SDS-ACN", "SDS-DTT"},
            {"SDS-DTT"},
            {"SDS-ACN"},
            set(),
            {"SDS-ACN"},
        ]
        pack, protocol_steps = self._resolve(steps)
        # The DTT sheet names its product "DL-Dithiothreitol"; only the
        # catalog's approved alias ties it to this protocol's "DTT".
        self.assertEqual({d.document_id for d in pack.sds_documents}, {"SDS-ACN", "SDS-DTT"})
        titles = {"SDS-ACN": "Acetonitrile SDS", "SDS-DTT": "DL-Dithiothreitol SDS"}
        for index, (step, want) in enumerate(zip(protocol_steps, expected)):
            with self.subTest(step=step.instruction_source_text):
                guidance = pack.guidance_for_step(step, index)
                got = {d.document_id for d in guidance.applicable_documents if d.document_type == "supplier_sds"}
                self.assertEqual(got, want)
                card = "\n".join(guidance.display_bullets + guidance.localized_display_bullets)
                for document_id, title in titles.items():
                    if document_id not in want:
                        self.assertNotIn(title, card)
                self.assertEqual(guidance.citation_label.count("물질 SDS"), len(want))


class EquipmentStepBindingTests(unittest.TestCase):
    """An equipment manual goes to the steps that name its own machine and to no other step."""

    def _resolve(self, steps):
        section = [("03", "equipment_operation", "Load the rotor symmetrically. Never open the lid while it turns.")]
        documents = [
            _document(
                "EQ-CENTRIFUGE", "equipment_manual", "Centrifuge 5424 R manual", section,
                product_name="Centrifuge 5424 R", product_code="5424 R",
                # A generic alias names no machine, and nobody approved "spinner".
                aliases=(("centrifuge", True, True), ("spinner", False, False)),
            ),
            _document(
                "EQ-CENTRIFUGE-KO", "equipment_manual", "원심분리기 5424 R 설명서", section,
                family="EQ-CENTRIFUGE", product_name="원심분리기 5424 R", language="ko",
                translation_status="human_reviewed", translation_of="EQ-CENTRIFUGE",
            ),
            _document(
                "EQ-CENTRIFUGE-MT", "equipment_manual", "원심분리기 설명서 (기계 번역)", section,
                family="EQ-CENTRIFUGE", product_name="회전분리장치", language="ko",
                translation_status="machine_unreviewed", translation_of="EQ-CENTRIFUGE",
            ),
            _document(
                "EQ-THERMOMIXER", "equipment_manual", "Thermomixer C manual",
                [("02", "equipment_operation", "Keep the lid closed while the block is hot.")],
                product_name="Thermomixer C", product_code="5382",
            ),
            # Not in the protocol's equipment list; a step names it by its approved alias.
            _document(
                "EQ-SPEEDVAC", "equipment_manual", "SpeedVac SPD120 manual",
                [("04", "equipment_operation", "Release the vacuum before opening the lid.")],
                product_name="SpeedVac SPD120", aliases=(("speedvac", True, False),),
            ),
        ]
        protocol = _protocol(
            steps,
            materials=(),
            equipment=("Centrifuge 5424 R", "Eppendorf Thermomixer C Model 5382"),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            catalog = Path(temp_dir) / "catalog.sqlite"
            ingest_manifest({"documents": documents}, catalog)
            pack = resolve_safety_pack(protocol, catalog, facility_id="MAIN-LAB", usage_scope="reference_only")
        return pack, protocol.sections[0].steps

    def test_a_manual_reaches_only_the_steps_that_name_its_machine(self):
        steps = [
            ("Spin the tubes in the Centrifuge 5424 R for 1 min.", ()),
            ("Balance the centrifuge, vortex the tube and put it in the incubator.", ()),
            ("원심분리기와 인큐베이터, 기계와 설비를 점검한다.", ()),
            ("Set the 5424 R to 4 °C.", ()),
            ("Dry the peptides to completion using a speedvac.", ()),
            ("원심분리기 5424 R에 넣고 돌린다.", ()),
            ("Spin in the Centrifuge 5424 R, then shake in the Thermomixer C.", ()),
            ("Label the Centrifuge 5424 RX rack and the Thermomixer CX box.", ()),
            ("Hand the tube to the spinner, not the 회전분리장치.", ()),
            ("Incubate the plug.", ("Keep the Thermomixer C lid closed.",)),
        ]
        expected = [
            {"EQ-CENTRIFUGE"},
            set(),
            set(),
            {"EQ-CENTRIFUGE"},
            {"EQ-SPEEDVAC"},
            {"EQ-CENTRIFUGE"},
            {"EQ-CENTRIFUGE", "EQ-THERMOMIXER"},
            set(),
            set(),
            {"EQ-THERMOMIXER"},
        ]
        pack, protocol_steps = self._resolve(steps)
        # The SpeedVac is in no equipment list; its approved alias in a step
        # is what brings its manual into the pack.
        self.assertEqual(
            {d.document_id for d in pack.equipment_documents},
            {"EQ-CENTRIFUGE", "EQ-THERMOMIXER", "EQ-SPEEDVAC"},
        )
        titles = {
            "EQ-CENTRIFUGE": "Centrifuge 5424 R manual",
            "EQ-THERMOMIXER": "Thermomixer C manual",
            "EQ-SPEEDVAC": "SpeedVac SPD120 manual",
        }
        for index, (step, want) in enumerate(zip(protocol_steps, expected)):
            with self.subTest(step=step.instruction_source_text):
                guidance = pack.guidance_for_step(step, index)
                got = {d.document_id for d in guidance.applicable_documents if d.document_type == "equipment_manual"}
                self.assertEqual(got, want)
                card = "\n".join(guidance.display_bullets + guidance.localized_display_bullets)
                for document_id, title in titles.items():
                    if document_id not in want:
                        self.assertNotIn(title, card)
                self.assertEqual(guidance.citation_label.count("장비 매뉴얼"), len(want))


if __name__ == "__main__":
    unittest.main()
