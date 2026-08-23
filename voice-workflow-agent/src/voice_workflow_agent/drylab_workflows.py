"""Metadata-only registry for reviewed Snakemake and Nextflow workflows.

This module deliberately has no execution function and never invokes a shell,
workflow engine, container runtime, or imported repository code.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Protocol

from voice_workflow_agent.identity import Principal
from voice_workflow_agent.protocol_sources import SourceSnapshot
from voice_workflow_agent.workspace_store import WorkspaceError, WorkspaceStore


class DryLabWorkflowError(WorkspaceError):
    code = "dry_lab_workflow_invalid"


@dataclass(frozen=True)
class DryLabWorkflowMetadata:
    engine: str
    name: str
    entry_point: str
    workflow_version: str | None
    language_version: str | None
    rules_or_processes: tuple[str, ...]
    config_files: tuple[str, ...]
    config_schema_files: tuple[str, ...]
    environment_files: tuple[str, ...]
    repository: str
    commit_sha: str
    source_url: str
    validation_state: str = "metadata_only_unexecuted"
    execution_supported: bool = False

    def public_dict(self) -> dict[str, object]:
        return asdict(self)


_SNAKEMAKE_ENTRY = re.compile(r"(?:^|/)(?:Snakefile|workflow/Snakefile)$")
_SNAKEMAKE_RULE = re.compile(r"(?m)^\s*(?:rule|checkpoint)\s+([A-Za-z_][A-Za-z0-9_]*)\s*:")
_SNAKEMAKE_CONFIG = re.compile(r"(?m)^\s*configfile\s*:\s*[\"']([^\"']+)[\"']")
_SNAKEMAKE_VERSION = re.compile(r"(?m)^\s*(?:workflow_version|__version__)\s*=\s*[\"']([^\"']+)[\"']")
_NEXTFLOW_PROCESS = re.compile(r"(?m)^\s*process\s+([A-Za-z_][A-Za-z0-9_]*)\s*\{")
_NEXTFLOW_WORKFLOW = re.compile(r"(?m)^\s*workflow(?:\s+([A-Za-z_][A-Za-z0-9_]*))?\s*\{")
_NEXTFLOW_DSL2 = re.compile(r"(?m)^\s*nextflow\.enable\.dsl\s*=\s*2\s*$")
_NEXTFLOW_MANIFEST_VERSION = re.compile(
    r"(?ms)manifest\s*\{.*?\bversion\s*=\s*[\"']([^\"']+)[\"']"
)
_NEXTFLOW_MANIFEST_NAME = re.compile(
    r"(?ms)manifest\s*\{.*?\bname\s*=\s*[\"']([^\"']+)[\"']"
)
_GITHUB_REPOSITORY = re.compile(
    r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,98}[A-Za-z0-9])?/"
    r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,98}[A-Za-z0-9])?$"
)
_GIT_COMMIT = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")


def _repository_path(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 500
        or value.startswith("/")
        or "\\" in value
        or "\x00" in value
    ):
        raise DryLabWorkflowError("Workflow repository path is invalid.")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise DryLabWorkflowError("Workflow repository path is invalid.")
    return value


def _github_identity(snapshot: SourceSnapshot) -> tuple[str, str, str]:
    if snapshot.connector_kind != "github":
        raise DryLabWorkflowError("Dry-lab workflows must use a reviewed GitHub source.")
    repository = snapshot.metadata.get("repository")
    commit_sha = snapshot.metadata.get("commit_sha")
    path = snapshot.metadata.get("path")
    if (
        not isinstance(repository, str)
        or _GITHUB_REPOSITORY.fullmatch(repository) is None
        or not isinstance(commit_sha, str)
        or _GIT_COMMIT.fullmatch(commit_sha) is None
        or not isinstance(path, str)
        or snapshot.version_identity != commit_sha
    ):
        raise DryLabWorkflowError("GitHub workflow provenance is incomplete.")
    return repository, commit_sha, _repository_path(path)


def _text(snapshot: SourceSnapshot) -> str:
    document = snapshot.content.get("document")
    content = document.get("text") if isinstance(document, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise DryLabWorkflowError("Workflow source must be UTF-8 text.")
    return content


def inspect_snakemake_snapshot(
    snapshot: SourceSnapshot, *, repository_paths: tuple[str, ...] = ()
) -> DryLabWorkflowMetadata:
    repository, commit_sha, path = _github_identity(snapshot)
    if _SNAKEMAKE_ENTRY.search(path) is None:
        raise DryLabWorkflowError("Snakemake entry point must be Snakefile or workflow/Snakefile.")
    content = _text(snapshot)
    rules = tuple(dict.fromkeys(_SNAKEMAKE_RULE.findall(content)))
    if not rules:
        raise DryLabWorkflowError("Snakemake metadata contains no declared rules.")
    configured = tuple(dict.fromkeys(_SNAKEMAKE_CONFIG.findall(content)))
    paths = tuple(_repository_path(path) for path in repository_paths)
    schemas = tuple(
        sorted(
            item
            for item in paths
            if item.endswith(("schema.yaml", "schema.yml", "schema.json"))
        )
    )
    environments = tuple(
        sorted(
            item
            for item in paths
            if (
                item.startswith(("envs/", "workflow/envs/"))
                and item.endswith((".yaml", ".yml", ".json"))
            )
            or item in {"environment.yaml", "environment.yml"}
        )
    )
    match = _SNAKEMAKE_VERSION.search(content)
    return DryLabWorkflowMetadata(
        engine="snakemake",
        name=repository.rsplit("/", 1)[-1],
        entry_point=path,
        workflow_version=match.group(1) if match else None,
        language_version=None,
        rules_or_processes=rules,
        config_files=configured,
        config_schema_files=schemas,
        environment_files=environments,
        repository=repository,
        commit_sha=commit_sha,
        source_url=snapshot.canonical_url,
    )


def inspect_nextflow_snapshot(
    snapshot: SourceSnapshot,
    *,
    nextflow_config_text: str = "",
    repository_paths: tuple[str, ...] = (),
) -> DryLabWorkflowMetadata:
    repository, commit_sha, path = _github_identity(snapshot)
    if not path.endswith("main.nf"):
        raise DryLabWorkflowError("Nextflow entry point must be main.nf.")
    content = _text(snapshot)
    processes = _NEXTFLOW_PROCESS.findall(content)
    workflows = [name or "default" for name in _NEXTFLOW_WORKFLOW.findall(content)]
    declared = tuple(dict.fromkeys([*workflows, *processes]))
    if not declared:
        raise DryLabWorkflowError("Nextflow metadata contains no workflow or process.")
    config = nextflow_config_text if isinstance(nextflow_config_text, str) else ""
    version = _NEXTFLOW_MANIFEST_VERSION.search(config)
    name = _NEXTFLOW_MANIFEST_NAME.search(config)
    paths = tuple(_repository_path(path) for path in repository_paths)
    config_files = tuple(sorted(item for item in paths if item.endswith(".config")))
    environments = tuple(
        sorted(
            item
            for item in paths
            if item.endswith(("environment.yml", "environment.yaml", "conda.yml"))
        )
    )
    return DryLabWorkflowMetadata(
        engine="nextflow",
        name=name.group(1) if name else repository.rsplit("/", 1)[-1],
        entry_point=path,
        workflow_version=version.group(1) if version else None,
        language_version="DSL2" if _NEXTFLOW_DSL2.search(config) else None,
        rules_or_processes=declared,
        config_files=config_files,
        config_schema_files=(),
        environment_files=environments,
        repository=repository,
        commit_sha=commit_sha,
        source_url=snapshot.canonical_url,
    )


class SeqeraLaunchBoundary(Protocol):
    """Future out-of-process integration boundary; not implemented here."""

    def submit_approved_revision(
        self, workflow_revision_id: str, *, dataset_reference: str
    ) -> str: ...


class DryLabWorkflowRegistry:
    def __init__(self, store: WorkspaceStore) -> None:
        self.store = store

    def import_metadata(
        self,
        principal: Principal,
        snapshot: SourceSnapshot,
        metadata: DryLabWorkflowMetadata,
    ) -> dict[str, object]:
        repository, commit_sha, path = _github_identity(snapshot)
        if (
            metadata.repository != repository
            or metadata.commit_sha != commit_sha
            or metadata.entry_point != path
            or metadata.engine not in {"snakemake", "nextflow"}
            or metadata.validation_state != "metadata_only_unexecuted"
            or metadata.execution_supported is not False
        ):
            raise DryLabWorkflowError("Workflow metadata provenance does not match its source.")
        return self.store.register_computational_workflow(
            principal,
            name=metadata.name,
            engine=metadata.engine,
            repository=repository,
            commit_sha=commit_sha,
            source_path=path,
            source_hash=snapshot.source_hash,
            metadata=metadata.public_dict(),
        )
