from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import replace

import pytest

from voice_workflow_agent.identity import Principal, Role
from voice_workflow_agent.protocol_sources import (
    GOOGLE_DRIVE_API_ROOT,
    GITHUB_API_ROOT,
    PROTOCOLS_IO_API_ROOT,
    GitHubConnector,
    GoogleDriveConnector,
    HttpResult,
    ProtocolSourceHub,
    ProtocolsIoConnector,
    SourceAuthorizationError,
    SourceIdentifierError,
    normalize_protocols_io_identifier,
    verify_github_webhook_signature,
)
from voice_workflow_agent.workspace_store import (
    WorkspaceSettings,
    initialize_workspace_store,
)


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, *, headers, params=None):
        self.calls.append((url, dict(headers), dict(params or {})))
        assert self.responses, f"Unexpected connector request: {url}"
        response = self.responses.pop(0)
        if isinstance(response, bytes):
            return HttpResult(200, {}, response)
        return HttpResult(200, {"content-type": "application/json"}, json.dumps(response).encode())


def _admin(name="admin", tenant="tenant-a"):
    return Principal(
        principal_id=f"principal-{name}",
        subject=f"test:{name}",
        organization_id=tenant,
        display_name=name,
        roles=frozenset({Role.LAB_ADMIN}),
        authentication_method="test",
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("10.17504/protocols.io.yinfude", "10.17504/protocols.io.yinfude"),
        ("protocols.io.yinfude/v1", "10.17504/protocols.io.yinfude/v1"),
        (
            "https://doi.org/10.17504/protocols.io.yinfude/v1",
            "10.17504/protocols.io.yinfude/v1",
        ),
        (
            "https://www.protocols.io/view/measuring-leaf-carbon-fractions-with-the-ankom2000-yinfude",
            "measuring-leaf-carbon-fractions-with-the-ankom2000-yinfude",
        ),
    ],
)
def test_protocols_io_identifier_forms(value, expected):
    assert normalize_protocols_io_identifier(value) == expected


@pytest.mark.parametrize(
    "malicious",
    [
        "http://www.protocols.io/view/yinfude",
        "https://protocols.io.evil.test/view/yinfude",
        "https://evil.test/?next=https://protocols.io/view/yinfude",
        "https://www.protocols.io@evil.test/view/yinfude",
        "https://www.protocols.io/view/../../admin",
        "file:///etc/passwd",
    ],
)
def test_protocols_io_identifier_rejects_ssrf_and_path_traversal(malicious):
    with pytest.raises(SourceIdentifierError):
        normalize_protocols_io_identifier(malicious)


def test_protocols_io_ankom_contract_preserves_structured_provenance():
    payload = {
        "status_code": 0,
        "payload": {
            "id": 123,
            "title": "Measuring leaf carbon fractions with the ANKOM2000 Fiber Analyzer V.1",
            "uri": "measuring-leaf-carbon-fractions-with-the-ankom2000-yinfude",
            "url": "https://www.protocols.io/view/measuring-leaf-carbon-fractions-with-the-ankom2000-yinfude",
            "doi": "dx.doi.org/10.17504/protocols.io.yinfude",
            "version_id": 1,
            "version_uri": "measuring-leaf-carbon-fractions-with-the-ankom2000-yinfude/v1",
            "published_on": 0,
            "modified_on": 1700000000,
            "authors": [{"name": "ANKOM protocol author"}],
            "creator": {"name": "Source lab"},
            "license": "CC BY",
            "materials": [{"name": "ANKOM2000 Fiber Analyzer"}],
            "steps": [
                {"step": "Heat samples with the analyzer."},
                {"step": "Handle 72% sulfuric acid in the approved controls."},
            ],
            "warning": "Hot equipment and concentrated acid hazards.",
        },
    }
    transport = FakeTransport([payload])
    snapshot = ProtocolsIoConnector(
        access_token="server-side-token", transport=transport
    ).fetch("10.17504/protocols.io.yinfude/v1")

    assert transport.calls[0][0] == (
        f"{PROTOCOLS_IO_API_ROOT}/protocols/10.17504/protocols.io.yinfude/v1"
    )
    assert transport.calls[0][2] == {"content_format": "markdown"}
    assert snapshot.metadata["doi"] == "10.17504/protocols.io.yinfude"
    assert snapshot.metadata["version_uri"].endswith("/v1")
    assert snapshot.metadata["source_status"] == "In development"
    assert snapshot.metadata["authors"] == ["ANKOM protocol author"]
    assert snapshot.metadata["license"] == "CC BY"
    assert len(snapshot.content["materials"]) == 1
    assert len(snapshot.content["steps"]) == 2
    assert snapshot.content["warnings"] == [
        "Hot equipment and concentrated acid hazards."
    ]
    assert "server-side-token" not in json.dumps(snapshot.metadata)


def test_drive_my_drive_and_shared_drive_use_allowlisted_read_only_contracts():
    pdf = b"%PDF-1.4\nimmutable"
    listing = {
        "files": [
            {
                "id": "file_pdf_123",
                "name": "approved-protocol.pdf",
                "mimeType": "application/pdf",
                "modifiedTime": "2026-08-22T10:00:00Z",
                "headRevisionId": "drive-revision-8",
                "parents": ["folder_123"],
                "driveId": "shared_drive_1",
                "owners": [{"displayName": "Lab Owner"}],
                "webViewLink": "https://drive.google.com/file/d/file_pdf_123/view",
            }
        ]
    }
    transport = FakeTransport([listing, pdf])
    connector = GoogleDriveConnector(
        access_token="oauth-token",
        allowed_folder_ids=("folder_123",),
        shared_drive_id="shared_drive_1",
        transport=transport,
    )
    snapshots = connector.list_snapshots("folder_123")

    assert len(snapshots) == 1
    assert snapshots[0].version_identity == "drive-revision-8"
    assert snapshots[0].source_hash == hashlib.sha256(pdf).hexdigest()
    assert snapshots[0].metadata["owners"] == ["Lab Owner"]
    list_call = transport.calls[0]
    assert list_call[0] == f"{GOOGLE_DRIVE_API_ROOT}/files"
    assert list_call[2]["corpora"] == "drive"
    assert list_call[2]["driveId"] == "shared_drive_1"
    assert list_call[2]["includeItemsFromAllDrives"] == "true"
    assert list_call[2]["supportsAllDrives"] == "true"
    assert transport.calls[1][2]["alt"] == "media"
    with pytest.raises(SourceAuthorizationError):
        connector.list_snapshots("attacker_folder")


def test_drive_google_doc_exports_pdf_and_change_log_filters_allowed_parents():
    listing = {
        "files": [
            {
                "id": "google_doc_1",
                "name": "SOP",
                "mimeType": "application/vnd.google-apps.document",
                "modifiedTime": "2026-08-22T11:00:00Z",
                "parents": ["folder_123"],
            }
        ]
    }
    changes = {
        "changes": [
            {"fileId": "google_doc_1", "file": {"parents": ["folder_123"]}},
            {"fileId": "other_tenant_file", "file": {"parents": ["other_folder"]}},
            {"fileId": "removed", "removed": True},
        ],
        "newStartPageToken": "next-token",
    }
    transport = FakeTransport([listing, b"%PDF-export", changes])
    connector = GoogleDriveConnector(
        access_token="oauth-token",
        allowed_folder_ids=("folder_123",),
        transport=transport,
    )
    snapshot = connector.list_snapshots("folder_123")[0]
    changed, next_token = connector.changed_file_ids("previous-token")

    assert snapshot.media_type == "application/pdf"
    assert transport.calls[1][0].endswith("/files/google_doc_1/export")
    assert transport.calls[1][2] == {"mimeType": "application/pdf"}
    assert changed == ("google_doc_1",)
    assert next_token == "next-token"


def test_github_import_pins_commit_and_path_and_never_executes_content():
    raw = b"rule all:\n    input: 'result.txt'\n"
    encoded = base64.encodebytes(raw).decode()
    commit_sha = "a" * 40
    transport = FakeTransport(
        [
            {"sha": commit_sha},
            {
                "license": {"spdx_id": "MIT"},
                "default_branch": "main",
                "html_url": "https://github.com/lab/protocols",
            },
            {
                "type": "file",
                "sha": "b" * 40,
                "content": encoded,
                "html_url": f"https://github.com/lab/protocols/blob/{commit_sha}/workflows/Snakefile",
            },
        ]
    )
    connector = GitHubConnector(
        installation_token="installation-token",
        allowed_repositories=("lab/protocols",),
        allowed_refs=("main",),
        allowed_path_prefixes=("workflows",),
        transport=transport,
    )
    snapshot = connector.fetch("lab/protocols", "main", "workflows/Snakefile")

    assert transport.calls[0][0] == f"{GITHUB_API_ROOT}/repos/lab/protocols/commits/main"
    assert transport.calls[2][2] == {"ref": commit_sha}
    assert snapshot.version_identity == commit_sha
    assert snapshot.metadata["repository"] == "lab/protocols"
    assert snapshot.metadata["path"] == "workflows/Snakefile"
    assert snapshot.metadata["license"] == "MIT"
    assert snapshot.content["document"]["text"].startswith("rule all")
    assert "installation-token" not in json.dumps(snapshot.metadata)
    with pytest.raises(SourceIdentifierError):
        connector.fetch("lab/protocols", "main", "workflows/../../secret")


def test_github_webhook_signature_matches_official_vector_and_rejects_tamper():
    secret = "It's a Secret to Everybody"
    body = b"Hello, World!"
    signature = "sha256=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17"
    assert verify_github_webhook_signature(body, signature, secret)
    assert not verify_github_webhook_signature(body + b"!", signature, secret)
    assert not verify_github_webhook_signature(body, None, secret)


def test_source_hub_creates_new_draft_revision_for_changed_source(tmp_path):
    store = initialize_workspace_store(WorkspaceSettings(True, tmp_path))
    principal = _admin()
    store.bootstrap_principal(principal)
    try:
        transport = FakeTransport(
            [
                {
                    "payload": {
                        "title": "Protocol",
                        "uri": "protocol-abcdef12",
                        "version_uri": "protocol-abcdef12/v1",
                        "published_on": 1,
                        "steps": [{"step": "First"}],
                        "materials": [],
                        "warning": "",
                    },
                    "status_code": 0,
                }
            ]
        )
        first_snapshot = ProtocolsIoConnector(
            access_token="token", transport=transport
        ).fetch("protocol-abcdef12")
        hub = ProtocolSourceHub(store)
        first = hub.ingest(principal, first_snapshot)
        duplicate = hub.ingest(principal, first_snapshot)
        changed_snapshot = replace(
            first_snapshot,
            version_identity="protocol-abcdef12/v2",
            source_hash=hashlib.sha256(b"changed").hexdigest(),
            content={"steps": [{"step": "First"}, {"step": "Second"}]},
        )
        changed = hub.ingest(principal, changed_snapshot)

        assert first.changed is True and first.inbox_state == "new"
        assert duplicate.changed is False
        assert changed.changed is True and changed.inbox_state == "changed"
        assert changed.revision.revision_number == 2
        assert changed.revision.parent_revision_id == first.revision.revision_id
        assert len(store.source_inbox(principal)) == 2
    finally:
        store.close()
