"""The same immutable PDF can belong to two labs' independent catalog entries."""
from __future__ import annotations

from tests.test_lane_on_auth_api import Device, _configure, _invite
from tests.test_protocol_catalog import write_text_pdf
from voiney_lab import server as srv


def test_same_pdf_upload_is_scoped_by_lab(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path / "workspace")
    monkeypatch.setenv("VOINEY_LAB_PROTOCOL_ENABLED", "true")
    monkeypatch.setenv("VOINEY_LAB_PROTOCOL_DATA_DIR", str(tmp_path / "catalog"))
    monkeypatch.setenv("VOINEY_LAB_USAGE_SCOPE", "source_protocol_only")
    monkeypatch.setattr(srv, "_automatic_ocr_on_upload", lambda *args: {"state": "not_needed"})
    first, same_lab, other_lab = Device(), Device(), Device()
    for device, name, lab in ((first, "first", "lab-a"), (same_lab, "peer", "lab-a"), (other_lab, "other", "lab-b")):
        device.sign_up(_invite(tmp_path / "workspace", organization_id=lab), name)
    pdf = tmp_path / "fictional.pdf"
    write_text_pdf(pdf, "Fictional source.\n1. Record the sample label.", title="Fictional")
    responses = [d.post("/api/protocols?filename=fictional.pdf", content=pdf.read_bytes(),
                        headers={"content-type": "application/pdf"}) for d in (first, same_lab, other_lab)]
    assert [r.status_code for r in responses] == [201, 201, 201], [r.text for r in responses]
    a, peer, b = [r.json() for r in responses]
    assert a["protocol"]["protocol_id"] == peer["protocol"]["protocol_id"]
    assert b["protocol"]["protocol_id"] != a["protocol"]["protocol_id"]
    assert peer["deduplicated"] is True and b["deduplicated"] is False
    assert b["protocol"]["source_sha256"] == a["protocol"]["source_sha256"]
    assert first.get(f"/api/protocols/{b['protocol']['protocol_id']}").status_code == 404
    assert other_lab.get(f"/api/protocols/{a['protocol']['protocol_id']}").status_code == 404


def test_starter_pack_registers_same_source_for_two_labs(tmp_path):
    from tests.test_lane_pk_starter_pack import tool, environment, ScriptedModel, gamma_pdf, gamma_response
    source = tmp_path / "source"
    source.mkdir()
    sha = gamma_pdf(source / "fictional.pdf", 1)
    entry = tool.PackEntry(id="fictional", file="fictional.pdf", title="Fictional", field="생명",
        source="Fictional Journal", license="CC BY 4.0", authors="Fictional Author", source_url="https://example.org/test")
    results = []
    for lab in ("lab-a", "lab-b"):
        meter = tool.Meter(max_usd=4.0, max_clova_pages=0, price=(0.75, 0.075, 3.75), ledger=tmp_path / f"{lab}.jsonl")
        model = ScriptedModel({sha: [gamma_response(sha, pages=(1, 2, 3))]})
        rows = tool.run_pack([entry], data_dir=tmp_path / "catalog", source_dirs=[source],
            environment=environment(model), meter=meter, organization=lab)
        assert len(rows) == 1
        assert rows[0].state == tool.STATE_READY, rows[0]
        results.append(rows[0])
    assert results[0].protocol_id != results[1].protocol_id
