#!/usr/bin/env python3
"""Snapshot a catalog read-only, then load ONLY executable analyses from the copy."""
from __future__ import annotations
import argparse
import hashlib
import shutil
import sqlite3
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT)); sys.path.insert(0,str(ROOT/'src'))
from scripts.sim.corpus import Protocol
from scripts.sim.run import output_dir


def snapshot_catalog(source: Path, destination: Path) -> None:
    source=source.expanduser().resolve()
    destination=output_dir(destination)
    db=source/'protocol_workspace.sqlite'
    if not db.is_file() or destination==source or destination.is_relative_to(source):
        raise ValueError('invalid source/destination')
    if (destination/'protocol_workspace.sqlite').exists():
        raise ValueError('snapshot already exists')
    with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as src, sqlite3.connect(destination/'protocol_workspace.sqlite') as dst:
        src.backup(dst)
    shutil.copytree(source/'objects',destination/'objects',dirs_exist_ok=False)


def load_catalog(copy: Path) -> tuple[tuple[Protocol,...],dict[str,int]]:
    from voiney_lab.experiment_protocol_config import ProtocolPersistenceSettings
    from voiney_lab.experiment_protocol_store import initialize_protocol_store
    from voiney_lab.protocol_catalog import ProtocolCatalog
    copy=copy.expanduser().resolve()
    # Avoid accidentally opening the original (the store may migrate metadata).
    if not copy.is_relative_to((Path.home()/'reports/sim').resolve()):
        raise ValueError('catalog must be a snapshot under ~/reports/sim/')
    store=initialize_protocol_store(ProtocolPersistenceSettings(True,copy))
    counts={'loaded':0,'not_executable':0,'too_short':0}
    result=[]
    try:
        catalog=ProtocolCatalog(store)
        catalog.prepare_source_metadata()
        seen=set()
        for entry in catalog.list_entries():
            if not entry.available_for_execution:
                counts['not_executable']+=1
                continue
            fixture=catalog.load_executable_fixture(entry.protocol_id)
            if len(fixture.steps)<3:
                counts['too_short']+=1
                continue
            if fixture.fixture_sha256 in seen:
                continue
            seen.add(fixture.fixture_sha256)
            code='catalog_'+hashlib.sha256(fixture.fixture_sha256.encode()).hexdigest()[:12]
            result.append(Protocol(code,'uploaded',fixture))
            counts['loaded']+=1
    finally:
        store.close()
    return tuple(result),counts


def main(argv: list[str] | None = None) -> int:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--destination',type=Path,required=True)
    a=p.parse_args(argv)
    try:
        snapshot_catalog(a.source,a.destination)
    except (ValueError,OSError,sqlite3.Error) as exc:
        print(type(exc).__name__,file=sys.stderr); return 2
    print('catalog_snapshot_created')
    return 0

if __name__=='__main__':
    raise SystemExit(main())
