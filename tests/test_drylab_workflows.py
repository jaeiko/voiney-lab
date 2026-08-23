from __future__ import annotations

import hashlib
import sqlite3

import pytest

from voice_workflow_agent.drylab_workflows import (
    DryLabWorkflowError,
    DryLabWorkflowRegistry,
    inspect_nextflow_snapshot,
    inspect_snakemake_snapshot,
)
from voice_workflow_agent.identity import Principal, Role
from voice_workflow_agent.protocol_sources import SourceSnapshot
from voice_workflow_agent.workspace_store import (
    WorkspaceConflictError,
    WorkspaceNotFoundError,
    WorkspaceSettings,
    initialize_workspace_store,
)


def _principal(name: str, tenant: str, role: Role = Role.LAB_ADMIN) -> Principal:
    return Principal(
        principal_id=f"principal-{name}",
        subject=f"test:{name}",
        organization_id=tenant,
        display_name=name,
        roles=frozenset({role}),
        authentication_method="test",
    )


def _snapshot(path: str, text: str, *, commit: str = "a" * 40) -> SourceSnapshot:
    raw = text.encode()
    return SourceSnapshot(
        connector_kind="github",
        external_id=f"lab/analysis:{path}",
        version_identity=commit,
        source_hash=hashlib.sha256(raw).hexdigest(),
        canonical_url=f"https://github.com/lab/analysis/blob/{commit}/{path}",
        title=path.rsplit("/", 1)[-1],
        metadata={
            "repository": "lab/analysis",
            "commit_sha": commit,
            "ref": "main",
            "path": path,
        },
        content={"document": {"text": text, "sha256": hashlib.sha256(raw).hexdigest()}},
        binary_content=raw,
        media_type="text/plain",
    )


def _protocol_revision(
    store,
    principal,
    *,
    protocol_id: str = "protocol-wet",
    catalog_revision_id: str = "pdf-1",
):
    family = store.create_protocol_family(principal, title="Wet protocol")
    source = store.register_source(
        principal,
        connector_kind="local_pdf",
        external_id="wet-source",
        version_identity="v1",
        source_hash=hashlib.sha256(b"wet").hexdigest(),
        canonical_url=None,
        metadata={"source_status": "Published"},
    )
    return store.add_protocol_revision(
        principal,
        family_id=family.family_id,
        source_id=source.source_id,
        content={
            "steps": ["Collect sample"],
            "execution_identity": {
                "protocol_id": protocol_id,
                "source_sha256": source.source_hash,
                "catalog_revision_id": catalog_revision_id,
            },
        },
        change_summary="Initial",
    )


def test_snakemake_metadata_import_detects_rules_config_schema_and_environment():
    snapshot = _snapshot(
        "workflow/Snakefile",
        """workflow_version = "2.1.0"
configfile: "config/config.yaml"
rule all:
    input: "results/final.txt"
checkpoint prepare:
    output: "work/prepared.txt"
""",
    )
    metadata = inspect_snakemake_snapshot(
        snapshot,
        repository_paths=(
            "workflow/Snakefile",
            "config/config.yaml",
            "config/schema.yaml",
            "workflow/envs/tools.yaml",
        ),
    )

    assert metadata.engine == "snakemake"
    assert metadata.workflow_version == "2.1.0"
    assert metadata.rules_or_processes == ("all", "prepare")
    assert metadata.config_files == ("config/config.yaml",)
    assert metadata.config_schema_files == ("config/schema.yaml",)
    assert metadata.environment_files == ("workflow/envs/tools.yaml",)
    assert metadata.execution_supported is False
    assert metadata.validation_state == "metadata_only_unexecuted"


def test_nextflow_metadata_import_detects_dsl2_manifest_and_processes():
    snapshot = _snapshot(
        "main.nf",
        """process ALIGN {
  input: path reads
  output: path 'aligned.bam'
}
workflow { ALIGN(params.reads) }
""",
    )
    metadata = inspect_nextflow_snapshot(
        snapshot,
        nextflow_config_text="""nextflow.enable.dsl=2
manifest { name = 'lab-pipeline'; version = '1.4.2' }
""",
        repository_paths=("main.nf", "nextflow.config", "conf/test.config"),
    )

    assert metadata.engine == "nextflow"
    assert metadata.name == "lab-pipeline"
    assert metadata.workflow_version == "1.4.2"
    assert metadata.language_version == "DSL2"
    assert metadata.rules_or_processes == ("default", "ALIGN")
    assert metadata.config_files == ("conf/test.config", "nextflow.config")


def test_drylab_registry_versions_review_and_exact_wet_lab_link(tmp_path):
    store = initialize_workspace_store(WorkspaceSettings(True, tmp_path))
    admin = _principal("admin", "tenant-a")
    other = _principal("other", "tenant-b")
    store.bootstrap_principal(admin)
    store.bootstrap_principal(other)
    try:
        snapshot = _snapshot("workflow/Snakefile", "rule all:\n    input: 'x'\n")
        metadata = inspect_snakemake_snapshot(snapshot)
        imported = DryLabWorkflowRegistry(store).import_metadata(admin, snapshot, metadata)
        duplicate = DryLabWorkflowRegistry(store).import_metadata(admin, snapshot, metadata)
        changed_snapshot = _snapshot(
            "workflow/Snakefile",
            "rule all:\n    input: 'y'\n",
            commit="b" * 40,
        )
        changed_metadata = inspect_snakemake_snapshot(changed_snapshot)
        changed = DryLabWorkflowRegistry(store).import_metadata(
            admin, changed_snapshot, changed_metadata
        )

        assert imported["changed"] is True
        assert duplicate["changed"] is False
        assert changed["revision_number"] == 2
        assert changed["parent_revision_id"] == imported["workflow_revision_id"]
        reviewed = store.review_computational_workflow(
            admin,
            changed["workflow_revision_id"],
            action="approved",
            comment="Metadata and pinned commit reviewed; execution remains external.",
        )
        assert reviewed["approval_state"] == "approved"
        workflow_record = next(
            item for item in store.computational_workflows(admin)
            if item["workflow_revision_id"] == changed["workflow_revision_id"]
        )
        assert workflow_record["review_history"][0]["actor_role"] == "lab_admin"
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            store._connection.execute(
                "UPDATE computational_workflow_review_events SET comment='tampered'"
            )
        store._connection.rollback()

        wet = _protocol_revision(store, admin)
        store.start_experiment(
            admin,
            session_id="session-wet-1",
            protocol_id="protocol-wet",
            protocol_revision_id="pdf-1-analysis-1",
            current_step_id="step-1",
            current_step_label="1",
        )
        link_id = store.link_wet_dry_workflow(
            admin,
            experiment_session_id="session-wet-1",
            protocol_revision_id=wet.revision_id,
            workflow_revision_id=changed["workflow_revision_id"],
        )
        assert link_id.startswith("wet-dry-link-")
        links = store.wet_dry_workflow_links(
            admin, experiment_session_id="session-wet-1"
        )
        assert len(links) == 1
        assert links[0]["workflow_revision_id"] == changed["workflow_revision_id"]
        assert links[0]["experiment_protocol_id"] == "protocol-wet"
        assert links[0]["experiment_runtime_revision_id"] == "pdf-1-analysis-1"
        assert links[0]["commit_sha"] == "b" * 40
        assert links[0]["execution_supported"] is False
        assert links[0]["execution_started"] is False
        with pytest.raises(WorkspaceNotFoundError):
            store.wet_dry_workflow_links(
                other, experiment_session_id="session-wet-1"
            )
        with pytest.raises(WorkspaceNotFoundError):
            store.link_wet_dry_workflow(
                other,
                experiment_session_id="session-wet-1",
                protocol_revision_id=wet.revision_id,
                workflow_revision_id=changed["workflow_revision_id"],
            )
        with pytest.raises(WorkspaceConflictError, match="already linked"):
            store.link_wet_dry_workflow(
                admin,
                experiment_session_id="session-wet-1",
                protocol_revision_id=wet.revision_id,
                workflow_revision_id=changed["workflow_revision_id"],
            )
    finally:
        store.close()


def test_wet_dry_link_requires_real_session_and_matching_protocol_lineage(tmp_path):
    store = initialize_workspace_store(WorkspaceSettings(True, tmp_path))
    admin = _principal("admin", "tenant-a")
    store.bootstrap_principal(admin)
    try:
        snapshot = _snapshot("workflow/Snakefile", "rule all:\n    input: 'x'\n")
        metadata = inspect_snakemake_snapshot(snapshot)
        workflow = DryLabWorkflowRegistry(store).import_metadata(
            admin, snapshot, metadata
        )
        store.review_computational_workflow(
            admin,
            workflow["workflow_revision_id"],
            action="approved",
            comment="Metadata provenance reviewed.",
        )
        matching = _protocol_revision(store, admin)
        mismatch = _protocol_revision(
            store,
            admin,
            protocol_id="protocol-other",
            catalog_revision_id="pdf-2",
        )
        store.start_experiment(
            admin,
            session_id="session-real",
            protocol_id="protocol-wet",
            protocol_revision_id="pdf-1-analysis-1",
        )
        store.bind_resource(admin, "experiment_session", "session-ghost")
        with pytest.raises(WorkspaceNotFoundError):
            store.link_wet_dry_workflow(
                admin,
                experiment_session_id="session-ghost",
                protocol_revision_id=matching.revision_id,
                workflow_revision_id=workflow["workflow_revision_id"],
            )
        with pytest.raises(WorkspaceConflictError, match="do not match"):
            store.link_wet_dry_workflow(
                admin,
                experiment_session_id="session-real",
                protocol_revision_id=mismatch.revision_id,
                workflow_revision_id=workflow["workflow_revision_id"],
            )
        with pytest.raises(WorkspaceConflictError, match="transition"):
            store.review_computational_workflow(
                admin,
                workflow["workflow_revision_id"],
                action="approved",
                comment="Duplicate approval must not rewrite review state.",
            )
        store.review_computational_workflow(
            admin,
            workflow["workflow_revision_id"],
            action="revoked",
            comment="Revoked metadata revision.",
        )
        with pytest.raises(WorkspaceConflictError, match="transition"):
            store.review_computational_workflow(
                admin,
                workflow["workflow_revision_id"],
                action="approved",
                comment="Revoked revisions require a new immutable revision.",
            )
    finally:
        store.close()


def test_drylab_import_rejects_non_github_and_never_offers_execution():
    snapshot = _snapshot("workflow/Snakefile", "rule all:\n    input: 'x'\n")
    with pytest.raises(DryLabWorkflowError):
        inspect_snakemake_snapshot(
            SourceSnapshot(
                **{**snapshot.__dict__, "connector_kind": "google_drive"}
            )
        )
    assert not hasattr(DryLabWorkflowRegistry, "execute")


def test_drylab_import_rejects_unpinned_or_execution_capable_metadata(tmp_path):
    snapshot = _snapshot("workflow/Snakefile", "rule all:\n    input: 'x'\n")
    with pytest.raises(DryLabWorkflowError, match="provenance"):
        inspect_snakemake_snapshot(
            SourceSnapshot(
                **{
                    **snapshot.__dict__,
                    "version_identity": "main",
                }
            )
        )
    store = initialize_workspace_store(WorkspaceSettings(True, tmp_path))
    admin = _principal("admin", "tenant-a")
    store.bootstrap_principal(admin)
    try:
        metadata = inspect_snakemake_snapshot(snapshot)
        unsafe = type(metadata)(
            **{
                **metadata.__dict__,
                "execution_supported": True,
            }
        )
        with pytest.raises(DryLabWorkflowError, match="provenance"):
            DryLabWorkflowRegistry(store).import_metadata(admin, snapshot, unsafe)
    finally:
        store.close()
