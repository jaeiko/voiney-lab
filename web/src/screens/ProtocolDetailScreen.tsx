import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';
import { apiFetch, ApiError, postFile, postJson } from '../api/client';
import { useSignOutOnExpiry } from '../api/queries';
import type { AuthSession, CatalogEntry } from '../api/types';
import { ErrorBox, LoadingBox, Note } from '../components/Pieces';
import { PagePicker } from '../components/PagePicker';
import { TitleLine } from '../components/TitleLine';
import { detail as c } from '../copy/ko';
import { routes, go } from '../lib/navigation';

type Value = null | boolean | number | string | Value[] | { [key: string]: Value };
export type DocumentValue = { [key: string]: Value };
interface Detail extends CatalogEntry {
  analysis_available?: boolean;
  source?: { filename?: string; page_count?: number };
  sections?: DocumentValue[]; materials?: Value[]; equipment?: Value[]; before_start?: Value[]; constructs?: Value[]; metadata?: DocumentValue;
  safety_notices?: Array<{ primary_text?: string; source_text?: string; source_page_number?: number }>;
  safety_scope?: { notice?: string };
  execution_blockers?: Array<{ message_ko?: string }>; execution_notices?: Array<{ message_ko?: string }>;
  cleared_fields_ko?: string; repeat_count_checks_ko?: string[]; timers?: DocumentValue;
  review_versions?: Array<{ version?: number; version_ko?: string; registered_on?: string; reviewer_name?: string }>;
}
const pathFor = (id: string, tail = '') => `/api/protocols/${encodeURIComponent(id)}${tail}`;
const object = (value: Value | undefined): DocumentValue | null => value && typeof value === 'object' && !Array.isArray(value) ? value : null;
const valueWords = (value: string | null): string => value === null ? c.emptyValue : value;

/** Show source values verbatim. Internal ids/parsed machine values stay in the developer fold. */
function SourceValues({ value, protocolId, refs = {}, reference = false }: { value: Value | undefined; protocolId: string; refs?: Record<string, string>; reference?: boolean }) {
  if (value === null || value === undefined || value === false || value === true) return null;
  if (typeof value !== 'object') return <span className='verbatim'>{reference ? refs[String(value)] ?? c.missing : String(value)}</span>;
  if (Array.isArray(value)) return <ul className='detail-values'>{value.map((item, i) => <li key={i}><SourceValues value={item} protocolId={protocolId} refs={refs} reference={reference} /></li>)}</ul>;
  if (typeof value.step_id === 'string' && Object.keys(value).every(key => ['step_id', 'action_id'].includes(key))) return <span>{refs[value.step_id] ?? c.missing}</span>;
  const page = value.source_page_number;
  return <dl className='detail-values'>{Object.entries(value).filter(([key, v]) => c.fields[key] && v !== null && v !== '' && (!Array.isArray(v) || v.length)).map(([key, v]) => <div key={key}><dt>{c.fields[key]}</dt><dd><SourceValues value={v} protocolId={protocolId} refs={refs} reference={/(?:step_ids|step_id)$/.test(key)} /></dd></div>)}
    {typeof page === 'number' ? <div><dd><a className='btn' href={pathFor(protocolId, `/review-draft/source-pages/${page}/image`)} target='_blank' rel='noopener noreferrer'>{c.sourcePage(page)}</a></dd></div> : null}
  </dl>;
}

function ReagentTables({ protocolId }: { protocolId: string }) {
  const query = useQuery({ queryKey: ['reagent-tables', protocolId], queryFn: () => apiFetch<{ reagent_tables: Array<{ table_id: string; filename: string; sheets: Array<{ name: string; state?: string; cells: Array<{ ref?: string; row: number; column: number; display?: string; value?: Value; formula?: string }> }> }> }>(pathFor(protocolId, '/reagent-tables')) });
  const [busy, setBusy] = useState(false), [message, setMessage] = useState('');
  useSignOutOnExpiry(query.error);
  async function attach(file: File) {
    setMessage('');
    if (!file.name.toLowerCase().endsWith('.xlsx')) { setMessage(c.tableErrors.unsupported_reagent_table_media_type); return; }
    setBusy(true);
    try { const result = await postFile<{ deduplicated?: boolean }>(pathFor(protocolId, `/reagent-tables?filename=${encodeURIComponent(file.name)}`), file, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'); setMessage(result.deduplicated ? c.duplicate : c.attached); await query.refetch(); }
    catch (error) { setMessage(error instanceof ApiError ? c.tableErrors[error.code] ?? c.attachFailed : c.attachFailed); }
    finally { setBusy(false); }
  }
  return <section className='record-section'><h2>{c.tables}</h2><p>{c.tableWhy}</p>
    <label className='btn'><span>{c.attach}</span><input className='sr-only' type='file' accept='.xlsx' disabled={busy} onChange={e => { const file = e.target.files?.[0]; e.target.value = ''; if (file) void attach(file); }} /></label>
    {message ? <p role='status'>{message}</p> : null}
    {query.isPending ? <LoadingBox /> : query.isError ? <ErrorBox message={c.failed} onRetry={() => void query.refetch()} /> : !query.data.reagent_tables.length ? <p>{c.noTables}</p> : query.data.reagent_tables.map(table => <details key={table.table_id}><summary>{table.filename}</summary>{table.sheets.map((sheet, i) => <div key={i}><h3>{sheet.name} {sheet.state && sheet.state !== 'visible' ? c.hiddenSheet : ''}</h3><div className='record-table-wrap'><table className='record-table'><thead><tr><th>{c.cell}</th><th>{c.value}</th></tr></thead><tbody>{sheet.cells.map((cell, j) => <tr key={j}><th>{cell.ref || `${cell.row}:${cell.column}`}</th><td>{(cell.display ?? (cell.value === null || cell.value === undefined ? '' : String(cell.value))) || cell.formula || ''}</td></tr>)}</tbody></table></div></div>)}</details>)}
  </section>;
}
function Version({ protocolId, version }: { protocolId: string; version: number }) {
  const query = useQuery({ queryKey: ['protocol-version', protocolId, version], queryFn: () => apiFetch<{ changes: Array<{ kind_ko?: string; step_label?: string; original: string | null; reviewed: string | null; action?: string; origin?: string; decided_by?: string; decided_at?: string }>; base?: { kind?: string; version?: number }; reviewer_name?: string; registered_on?: string; memos?: Record<string, string> }>(pathFor(protocolId, `/review-versions/${version}`)) });
  useSignOutOnExpiry(query.error);
  return query.isPending ? <LoadingBox /> : query.isError ? <ErrorBox message={c.failed} onRetry={() => void query.refetch()} /> : <div data-testid='version-changes'><p>{query.data.base?.kind === 'review_version' && typeof query.data.base.version === 'number' ? c.versionLabel(query.data.base.version) : c.baseAnalysis} → {c.versionLabel(version)}</p><p>{query.data.reviewer_name} · {query.data.registered_on}</p>{!query.data.changes.length ? <p>{c.noChanges}</p> : query.data.changes.map((change, i) => <div key={i}><p>{change.step_label ? c.step(change.step_label) + ' · ' : ''}{change.kind_ko}</p><p className='verbatim'>{valueWords(change.original)} → {change.action === 'clear' ? c.missing : valueWords(change.reviewed)}</p><p><span>{c.origins[change.origin ?? ''] ?? c.unknownOrigin}</span> · {change.decided_by}{change.decided_at ? <> · <time dateTime={change.decided_at}>{change.decided_at}</time></> : null}</p></div>)}{Object.values(query.data.memos ?? {}).map((memo, i) => <p key={i}>{memo}</p>)}</div>;
}
function Reanalysis({ protocolId, pageCount, analysed }: { protocolId: string; pageCount: number; analysed: boolean }) {
 const [range, setRange] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
 async function analyse(first?: number, last?: number) {
  if (busy) return; setBusy(true); setError('');
  try { const query = first && last ? `?first_page=${first}&last_page=${last}` : ''; await postJson(pathFor(protocolId, `${analysed ? '/reanalysis' : '/analysis'}${query}`), {}); go(routes.analysis(protocolId)); }
  catch (error) { setError(error instanceof ApiError && error.messageKo ? error.messageKo : c.failed); } finally { setBusy(false); }
 }
 return <section className='record-section'><h2>{c.reanalysis}</h2><p>{c.reanalysisWhy}</p><div className='record-actions'><button className='btn' disabled={busy} onClick={() => void analyse()}>{busy ? c.busy : c.all}</button><button className='btn' disabled={busy || !pageCount} onClick={() => setRange(!range)}>{c.range}</button></div>{range ? <PagePicker protocolId={protocolId} pageCount={pageCount} busy={busy} onChoose={(first, last) => void analyse(first, last)} /> : null}{error ? <Note>{error}</Note> : null}</section>;
}
export function ProtocolDetailScreen({ protocolId, session }: { protocolId: string; session: AuthSession }) {
  const query = useQuery({ queryKey: ['protocol-full', protocolId], queryFn: () => apiFetch<Detail>(pathFor(protocolId, '/review')) });
  const [version, setVersion] = useState<number | null>(null);
  useSignOutOnExpiry(query.error);
  const r = query.data;
  const refs: Record<string, string> = {};
  for (const section of r?.sections ?? []) for (const step of Array.isArray(section.steps) ? section.steps : []) { const s = object(step); if (s && typeof s.step_id === 'string') refs[s.step_id] = String(s.source_label ?? ''); }
  return <section className='records-screen protocol-detail' data-testid='protocol-detail'><a className='btn' href={routes.protocols}>{c.back}</a><p className='page-why'>{c.why}</p>
    {query.isPending ? <LoadingBox /> : query.isError ? <ErrorBox message={c.failed} onRetry={() => void query.refetch()} /> : r ? <>
      <TitleLine entry={r} renamable={!r.development_only} />
      {r.available_for_execution ? <a className='btn btn-primary btn-big' href={routes.start(protocolId)}>{c.start}</a> : r.review_pending?.needs_review_count ? <a className='btn' href={routes.review(protocolId)}>{c.review}</a> : <a className='btn' href={routes.analysis(protocolId)}>{c.analyse}</a>}
      {r.available_for_execution ? <a className='btn btn-quiet' href={routes.review(protocolId)}>{c.reviewCards}</a> : null}
      <section className='record-section'><h2>{c.summary}</h2>{!r.available_for_execution && (r.review_pending?.needs_review_count ?? 0) > 0 ? <Note>{c.pendingAnalysis}</Note> : null}{[...(r.execution_blockers ?? []), ...(r.execution_notices ?? [])].map((n, i) => <p key={i}>{n.message_ko}</p>)}{r.cleared_fields_ko ? <p>{r.cleared_fields_ko}</p> : null}{r.repeat_count_checks_ko?.map((v, i) => <p key={i}>{v}</p>)}{r.safety_scope?.notice ? <p>{r.safety_scope.notice}</p> : null}{r.safety_notices?.map((n, i) => <div className='detail-safety' key={i}>{n.primary_text ? <p>{n.primary_text}</p> : null}<p className='verbatim'>{n.source_text}</p></div>)}</section>
      <section className='record-section'><h2>{c.steps}</h2>{!r.sections?.length ? <p>{c.empty}</p> : r.sections.map((section, i) => <div key={i} className='detail-section'><h3>{String(section.title_source_text ?? '')}</h3>{(Array.isArray(section.steps) ? section.steps : []).map((step, j) => {
        const value = object(step); if (!value) return null;
        return <details key={j} className='detail-step' open={i === 0 && j === 0}><summary>{typeof value.source_label === 'string' && value.source_label ? c.step(value.source_label) : c.fields.instruction_source_text}</summary><SourceValues value={Object.fromEntries(Object.entries(value).filter(([key]) => key !== 'source_label'))} protocolId={protocolId} refs={refs} /></details>;
      })}</div>)}</section>
      {([[c.before, r.before_start], [c.materials, r.materials], [c.equipment, r.equipment], [c.constructs, r.constructs], [c.timers, r.timers], [c.fields.metadata, r.metadata]] as const).map(([label, value]) => <details key={label} open><summary>{label}</summary>{value && (!Array.isArray(value) || value.length) ? <SourceValues value={value} protocolId={protocolId} refs={refs} /> : <p>{c.noContent}</p>}</details>)}
      <ReagentTables protocolId={protocolId} />
      <section className='record-section'><h2>{c.history}</h2>{!r.review_versions?.length ? <p>{c.noHistory}</p> : r.review_versions.map((v, i) => <button className='btn' key={i} disabled={!Number.isInteger(v.version)} onClick={() => setVersion(v.version!)}>{v.version_ko} · {v.registered_on} · {v.reviewer_name}</button>)}{version !== null ? <Version key={version} protocolId={protocolId} version={version} /> : null}</section>
      <Reanalysis protocolId={protocolId} pageCount={r.source?.page_count ?? 0} analysed={r.analysis_available === true || r.available_for_execution || r.analysis_status === 'structured_analysis_ready'} />
      {session.mode === 'dev' || session.account?.admin ? <details><summary>{c.developer}</summary><p>{c.developerWhy}</p><pre>{JSON.stringify(r, null, 2)}</pre></details> : null}
    </> : null}
  </section>;
}
