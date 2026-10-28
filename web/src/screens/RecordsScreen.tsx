import { useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type MouseEvent, type ReactNode } from 'react';
import { postJson } from '../api/client';
import { experimentPath, recordsApi, reportPath, type Notebook, type NotebookResponse, type NotebookSection, type RecordEvent, type ReviewValue } from '../api/recordsApi';
import { isSessionExpired, signedOut, useExperiments, useSignOutOnExpiry, useWorkspaceSession } from '../api/queries';
import type { AuthSession, Experiment } from '../api/types';
import { EmptyBox, ErrorBox, LoadingBox, Note } from '../components/Pieces';
import { records as c } from '../copy/ko';
import { RecordCapture, CheckpointRestart, ReportPreparation } from './RecordActions';
import { useRun } from '../voice/runStore';
import { oldRoutes, routes } from '../lib/navigation';

const recordKey = (id: string, part: string) => ['records', id, part];
const notebookKey = (id: string) => ['notebook', id];
const time = (value?: string | null) => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString('ko-KR', { timeZone: 'UTC' }) + ' UTC' : c.unknown;
const title = (item: Experiment) => item.protocol_title || c.experiment;
const state = (status: string) => c.states[status] || c.unknown;

export function RecordsScreen({ session }: { session: AuthSession }) {
  const list = useExperiments();
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('');
  const [protocol, setProtocol] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [rating, setRating] = useState('');
  useSignOutOnExpiry(list.error);
  const all = list.data ?? [];
  const protocols = [...new Map(all.map(item => [item.protocol_id || '', title(item)])).entries()];
  const shown = all.filter(item => {
    const day = item.started_at?.slice(0, 10) || '';
    return title(item).toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()) && (!status || item.status === status) &&
      (!protocol || item.protocol_id === protocol) && (!from || day >= from) && (!to || Boolean(day) && day <= to) &&
      (!rating || (rating === 'rated' ? Number.isInteger(item.rating) : item.rating_state === 'unrated'));
  });
  return <section className='records-screen' data-testid='records-screen'>
    <h1>{c.title}</h1><p className='page-why'>{c.why}</p>
    {session.account?.admin ? <p className='muted'>{c.lab}</p> : null}
    <div className='records-filters'>
      <label className='field'><span>{c.search}</span><input type='search' value={search} onChange={e => setSearch(e.target.value)} /></label>
      <label className='field'><span>{c.protocol}</span><select value={protocol} onChange={e => setProtocol(e.target.value)}><option value=''>{c.all}</option>{protocols.map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></label>
      <label className='field'><span>{c.status}</span><select value={status} onChange={e => setStatus(e.target.value)}><option value=''>{c.all}</option>{Object.entries(c.states).map(([code, word]) => <option key={code} value={code}>{word}</option>)}</select></label>
      <label className='field'><span>{c.from}</span><input type='date' value={from} onChange={e => setFrom(e.target.value)} /></label>
      <label className='field'><span>{c.to}</span><input type='date' value={to} onChange={e => setTo(e.target.value)} /></label>
      <label className='field'><span>{c.ratingFilter}</span><select value={rating} onChange={e => setRating(e.target.value)}><option value=''>{c.all}</option><option value='rated'>{c.rated}</option><option value='unrated'>{c.unrated}</option></select></label>
    </div>
    {list.isPending ? <LoadingBox label={c.loading} /> : list.isError ? <ErrorBox message={c.failed} onRetry={() => void list.refetch()} /> : !all.length ? <EmptyBox message={c.empty} /> : !shown.length ? <EmptyBox message={c.noMatch} /> :
      <ul className='record-list'>{shown.map(item => <li key={item.session_id}><a className='record-card' href={routes.record(item.session_id)}>
        <h2>{title(item)}</h2><span>{time(item.started_at)}</span><span>{state(item.status)}</span>
        <span>{Number.isInteger(item.completed_step_count) ? c.count(item.completed_step_count!) : c.unknown}</span>
        <span>{Number.isInteger(item.rating) ? c.rating(item.rating!) : item.rating_state === 'unavailable' ? c.ratingUnavailable : c.noRating}</span>
      </a></li>)}</ul>}
  </section>;
}

function EventLine({ item, sessionId }: { item: RecordEvent; sessionId: string }) {
  const o = item.observation, e = item.evidence, p = item.payload;
  let label = c.kinds[item.event_type] || c.event;
  if (o && item.event_type === 'observation_recorded') label = o.capture_source === 'voice' ? c.voice : c.memo;
  const photo = Boolean(e?.media_type?.startsWith('image/'));
  if (e) label = photo ? c.photo : c.attachment;
  if (item.event_type === 'problem_help') label = c.help;
  const timer = p?.timer;
  return <li className='record-event'>
    <div className='record-event-head'><strong>{label}</strong>{item.created_at ? <time dateTime={item.created_at}>{time(item.created_at)}</time> : null}</div>
    {o ? <><p>{o.current_content ?? o.content}</p>{o.corrected ? <p>{c.corrected} · {c.original(o.content)}</p> : null}{o.retracted ? <p>{c.retracted}</p> : null}</> : null}
    {item.user_wording ? <p>{item.user_wording}</p> : null}
    {e?.evidence_id ? <><p>{e.caption || e.original_filename}</p>{photo ? <img className='record-photo' loading='lazy' alt={e.caption || e.original_filename || c.photo} src={experimentPath(sessionId, `/evidence/${encodeURIComponent(e.evidence_id)}`)} /> : null}<a className='btn' download href={experimentPath(sessionId, `/evidence/${encodeURIComponent(e.evidence_id)}`)}>{photo ? c.photoGet : c.attachmentGet}</a></> : null}
    {timer ? <dl className='record-values'>{Object.entries(c.timerWords).filter(([key]) => typeof timer[key] === 'number').map(([key, word]) => <div key={key}><dt>{word}</dt><dd>{String(timer[key])}</dd></div>)}</dl> : null}
    {p?.run_changes?.length ? <><h3>{c.changes}</h3><ul>{p.run_changes.map((change, i) => <li key={i}>{change.source_written || change.source_shown_ko || [change.source_number, change.source_unit].filter(Boolean).join(' ')} → {change.shown_ko || [change.number, change.unit].filter(Boolean).join(' ')}</li>)}</ul></> : null}
    {p?.sources?.length ? <details><summary>{c.helpSources}</summary><ul>{p.sources.map((source, i) => <li key={i}>{[source.title, source.source_name, typeof source.tier === 'string' ? c.sourceTiers[source.tier] || c.source : '', source.locator, source.citation].filter(v => typeof v === 'string').join(' · ')}</li>)}</ul></details> : null}
    {p?.comment ? <p>{p.comment}</p> : null}
  </li>;
}

function Timeline({ items, sessionId }: { items: RecordEvent[]; sessionId: string }) {
  const groups = new Map<string, RecordEvent[]>();
  for (const item of items) {
    // A start event can name changes in several steps; display each under its own source label.
    const changes = item.payload?.run_changes;
    const labels = changes?.length ? [...new Set(changes.map(change => change.step_label || ''))] : [item.step_label || ''];
    for (const label of labels) {
      const shown = changes?.length ? { ...item, payload: { ...item.payload, run_changes: changes.filter(change => (change.step_label || '') === label) } } : item;
      groups.set(label, [...(groups.get(label) ?? []), shown]);
    }
  }
  return <div className='record-timeline'>{[...groups].map(([label, events]) => <section key={label}><h3>{label ? c.step(label) : c.outside}</h3><ol className='record-events'>{events.map((item, i) => <EventLine key={item.event_id || item.event_key || i} item={item} sessionId={sessionId} />)}</ol></section>)}</div>;
}

export function RecordDetailScreen({ sessionId }: { sessionId: string }) {
  const client = useQueryClient();
  const run = useRun();
  const experiment = useQuery({ queryKey: recordKey(sessionId, 'session'), queryFn: () => recordsApi.experiment(sessionId) });
  const timeline = useQuery({ queryKey: recordKey(sessionId, 'timeline'), queryFn: () => recordsApi.timeline(sessionId), enabled: experiment.isSuccess });
  const reports = useQuery({ queryKey: recordKey(sessionId, 'reports'), queryFn: () => recordsApi.reports(sessionId), enabled: experiment.isSuccess });
  const workspace = useWorkspaceSession();
  const notebooks = useQueries({ queries: (reports.data?.reports ?? []).map(report => ({ queryKey: notebookKey(report.report_id), queryFn: () => recordsApi.notebook(report.report_id) })) });
  useSignOutOnExpiry([experiment.error, timeline.error, reports.error, workspace.error, ...notebooks.map(q => q.error)].find(isSessionExpired));
  const extras = notebooks.flatMap(q => q.data?.events.filter(event => event.event_type.startsWith('timer_') || event.event_type === 'problem_help' || event.payload?.run_changes?.length) ?? []);
  const timelineLoading = timeline.isPending || reports.isPending || notebooks.some(q => q.isPending);
  const timelineFailed = timeline.isError || reports.isError || notebooks.some(q => q.isError);
  const reportKeys = new Set(extras.flatMap(event => {
    if (!event.event_key) return [];
    // The server's two append-only ledgers name the same committed turn with these exact keys.
    const turn = /^turn-(\d+)-generation-(\d+)-(.+)$/.exec(event.event_key);
    return [event.event_key, ...(turn ? [`voice-${turn[2]}-${turn[1]}-${turn[3]}`] : [])];
  }));
  const items = [...(timeline.data?.timeline ?? []).filter(event => !event.event_key || !reportKeys.has(event.event_key)), ...extras].sort((a, b) => (a.created_at ?? '').localeCompare(b.created_at ?? ''));
  function refresh() { void client.invalidateQueries({ queryKey: ['records', sessionId] }); for (const report of reports.data?.reports ?? []) void client.invalidateQueries({ queryKey: notebookKey(report.report_id) }); }
  return <section className='records-screen' data-testid='record-detail'>
    <a className='btn btn-quiet' href={routes.records}>{c.back}</a><h1>{experiment.data ? title(experiment.data) : c.title}</h1><p className='page-why'>{c.detailWhy}</p>
    {experiment.isPending ? <LoadingBox label={c.loading} /> : experiment.isError ? <ErrorBox message={c.failed} onRetry={() => void experiment.refetch()} /> : <>
      <p>{time(experiment.data.started_at)} · {state(experiment.data.status)}</p>
      {workspace.data && experiment.data.owner_principal_id !== workspace.data.principal_id ? <p>{experiment.data.owner_principal_id ? c.readOnly : c.readOnlyGeneral}</p> : null}
      <section className='record-section'><h2>{c.notebooks}</h2>
        {reports.isPending ? <LoadingBox label={c.loading} /> : reports.isError ? <ErrorBox message={c.failed} onRetry={refresh} /> : !reports.data.reports.length ? <EmptyBox message={c.noReports} /> : reports.data.reports.map(report => <div className='record-report' key={report.report_id}><p>{time(report.started_at)} · {state(report.status)}</p><a className='btn btn-primary btn-big' href={routes.notebook(sessionId, report.report_id)}>{c.notebook}</a></div>)}
      </section>
      {notebooks.map((query, i) => query.data?.notebook?.condition_rows.length ? <section className='record-section' key={reports.data?.reports[i].report_id}><h2>{c.conditions}</h2><Rows rows={query.data.notebook.condition_rows} heads={c.tableHeads} /></section> : null)}
      <section className='record-section'><h2>{c.timeline}</h2><button className='btn' onClick={refresh}>{c.refresh}</button>
        {timelineLoading && !timelineFailed ? <LoadingBox label={c.loading} /> : null}
        {timelineFailed ? <ErrorBox message={c.failed} onRetry={refresh} /> : null}
        {items.length ? <Timeline items={items} sessionId={sessionId} /> : !timelineLoading && !timelineFailed ? <EmptyBox message={c.noEvents} /> : null}
      </section>
      {workspace.data?.principal_id && experiment.data.owner_principal_id === workspace.data.principal_id ? <>
        <RecordCapture key={sessionId} sessionId={sessionId} refresh={refresh} />
        {experiment.data.status === 'stopped' && timeline.isSuccess ? <CheckpointRestart sessionId={sessionId} version={timeline.data.session?.version ?? experiment.data.version ?? 0} recovery={timeline.data.recovery} refresh={refresh} disabled={run.active || run.phase === 'starting' || run.phase === 'stopping'} /> : null}
      </> : null}
      <details><summary>{c.legacy}</summary><p>{c.legacyWhy}</p><a className='btn' href={oldRoutes.record(sessionId)}>{c.legacy}</a></details>
    </>}
  </section>;
}

function Rows({ rows, heads }: { rows: string[][]; heads?: readonly string[] }) {
  if (!rows.length) return null;
  return <div className='record-table-wrap'><table className='record-table'>{heads ? <thead><tr>{heads.map(h => <th scope='col' key={h}>{h}</th>)}</tr></thead> : null}<tbody>{rows.map((row, i) => <tr key={i}>{row.map((cell, j) => j === 0 ? <th scope='row' key={j}>{cell}</th> : <td key={j}>{cell}</td>)}</tr>)}</tbody></table></div>;
}

function Downloads({ reportId }: { reportId: string }) {
  const client = useQueryClient();
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  async function download(event: MouseEvent<HTMLAnchorElement>, format: string) {
    event.preventDefault(); if (busy) return; setBusy(true); setError('');
    try {
      const response = await fetch(reportPath(reportId, `.${format}`), { credentials: 'same-origin', cache: 'no-store', redirect: 'error' });
      if (!response.ok) { if (response.status === 401) signedOut(client); setError(format.includes('pdf') && response.status === 503 ? c.pdfUnavailable : c.downloadFailed); return; }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a'); link.href = url; link.download = `${reportId}.${format.replace('meeting-', 'meeting.')}`; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch { setError(c.downloadFailed); } finally { setBusy(false); }
  }
  return <div className='record-downloads' aria-busy={busy}>{([['docx', c.word], ['pdf', c.pdf], ['md', c.md], ['json', c.json], ['csv', c.csv], ['meeting-docx', c.meeting], ['meeting-pdf', c.meetingPdf], ['xlsx', c.xlsx]] as const).map(([format, word]) => <a key={format} className={`btn ${format === 'docx' ? 'btn-primary btn-big' : ''}`} href={reportPath(reportId, `.${format}`)} aria-disabled={busy} onClick={e => void download(e, format)}>{word}</a>)}{busy ? <p role='status'>{c.downloading}</p> : null}{error ? <Note>{error}</Note> : null}</div>;
}

function ValueReview({ item, reportId, editable, refresh }: { item: ReviewValue; reportId: string; editable: boolean; refresh: () => void }) {
  const [text, setText] = useState(item.text), [editing, setEditing] = useState(false), [busy, setBusy] = useState(false), [failed, setFailed] = useState(false);
  async function save(action: string) { setBusy(true); setFailed(false); try { await postJson(reportPath(reportId, `/review/${encodeURIComponent(item.item_id)}`), { action, ...(action === 'correct' ? { text } : {}) }); setEditing(false); refresh(); } catch { setFailed(true); } finally { setBusy(false); } }
  return <li className='record-review'><p>{item.step_label ? c.step(item.step_label) + ' · ' : ''}{item.kind} · {item.text} <strong>{item.confirmed ? c.confirmed : c.unconfirmed}</strong></p>
    {item.corrected ? <p>{c.original(item.original || '')}</p> : null}
    {editable ? <div className='record-actions'>{!item.confirmed ? <button className='btn' disabled={busy} onClick={() => void save('confirm')}>{c.confirm}</button> : null}<button className='btn' disabled={busy} onClick={() => setEditing(!editing)}>{c.edit}</button>{editing ? <><label className='field'><span>{c.value}</span><input maxLength={800} value={text} onChange={e => setText(e.target.value)} /></label><button className='btn' disabled={busy || !text.trim()} onClick={() => void save('correct')}>{c.correct}</button></> : null}</div> : null}
    {failed ? <Note>{c.saveFailed}</Note> : null}
  </li>;
}

function PiConfirmation({ reportId, refresh }: { reportId: string; refresh: () => void }) {
  const [comment, setComment] = useState(''), [busy, setBusy] = useState(false), [message, setMessage] = useState('');
  async function save() { setBusy(true); try { await postJson(reportPath(reportId, '/pi-confirmations'), { comment }); setMessage(c.saved); refresh(); } catch { setMessage(c.saveFailed); } finally { setBusy(false); } }
  return <section className='record-section'><h2>{c.pi}</h2><p>{c.piWhy}</p><label className='field'><span>{c.piComment}</span><input maxLength={300} value={comment} onChange={e => setComment(e.target.value)} /></label><button className='btn' disabled={busy} onClick={() => void save()}>{c.piSave}</button>{message ? <p role='status'>{message}</p> : null}</section>;
}

const DEFAULT_NOTEBOOK_SECTIONS: NotebookSection[] = [
  ['experiment_info', c.sectionExperiment], ['purpose', c.sectionPurpose], ['methods', c.sectionMethods],
  ['observations', c.sectionObservations], ['results', c.sectionResults],
  ['interpretation_next', c.sectionInterpretation], ['domain', c.sectionDomain], ['appendix', c.appendix],
].map(([id, name]) => ({ id, name }));

function NotebookVisuals({ notebook }: { notebook: Notebook }) {
  const bars = notebook.value_bars ?? [], timeline = notebook.timeline ?? [], images = notebook.images ?? [];
  return <>
    {bars.length ? <div className='notebook-bars'><h3>{c.valueBars}</h3>{bars.map((bar, i) => {
      const source = Number(bar.source_number), actual = Number(bar.actual_number), maximum = Math.max(source, actual, 0);
      const sourceWidth = maximum > 0 && Number.isFinite(source) ? Math.max(3, source / maximum * 100) : 0;
      const actualWidth = maximum > 0 && Number.isFinite(actual) ? Math.max(3, actual / maximum * 100) : 0;
      return <article className='notebook-bar' data-testid='notebook-value-bar' key={`${bar.label}-${i}`}><strong>{bar.label}</strong>
        <div><span>{c.sourceValue(bar.source)}</span><i style={{ width: `${sourceWidth}%` }} /></div>
        <div><span>{c.actualValue(bar.actual)}</span><i className='actual' style={{ width: `${actualWidth}%` }} /></div>
      </article>;
    })}</div> : null}
    {timeline.length ? <div className='notebook-timeline'><h3>{c.runTimeline}</h3><ol>{timeline.map((item, i) => <li key={`${item.at}-${item.step_label}-${i}`}><time>{item.at}</time><strong>{item.step_label ? c.step(item.step_label) : c.outside}</strong><span>{c.notebookTimelineKinds[item.kind] || item.text}</span>{item.text !== (c.notebookTimelineKinds[item.kind] || item.text) ? <p>{item.text}</p> : null}</li>)}</ol></div> : null}
    {images.length ? <div className='notebook-images'><h3>{c.images}</h3>{images.map((item, i) => {
      const label = `${item.step_label ? c.step(item.step_label) + ' · ' : ''}${item.caption}`;
      return <figure key={`${item.kind}-${item.asset_id}-${i}`}>{item.url ? <img loading='lazy' src={item.url} alt={label} /> : <div className='notebook-image-missing'>{c.imageMissing}</div>}<figcaption><strong>{item.kind === 'photo' ? c.photo : c.sourceFigure}</strong> · {label}{item.kind === 'photo' ? <span>{c.photoUninterpreted}</span> : null}</figcaption></figure>;
    })}</div> : null}
  </>;
}

function DomainNotebook({ notebook, reportId, editable, refresh }: { notebook: Notebook; reportId: string; editable: boolean; refresh: () => void }) {
  const domain = notebook.domain;
  const [selected, setSelected] = useState(domain?.code || 'general');
  const [busy, setBusy] = useState(false), [message, setMessage] = useState('');
  async function save() { setBusy(true); setMessage(''); try { await recordsApi.selectDomain(reportId, selected); setMessage(c.saved); refresh(); } catch { setMessage(c.saveFailed); } finally { setBusy(false); } }
  return <>
    {editable ? <div className='record-actions'><label className='field'><span>{c.domainProfile}</span><select value={selected} onChange={event => setSelected(event.target.value)}>{Object.entries(c.domainOptions).map(([code, label]) => <option key={code} value={code}>{label}</option>)}</select></label><button className='btn' disabled={busy || selected === domain?.code} onClick={() => void save()}>{c.domainSave}</button>{message ? <p role='status'>{message}</p> : null}</div> : null}
    <h3>{domain?.label || c.domainOptions.general}</h3>
    <Rows heads={c.domainHeads} rows={(domain?.fields ?? []).map(field => [field.label, field.value, [c.fieldOrigins[field.origin] || '', field.formula ? c.formula(field.formula) : ''].filter(Boolean).join('\n')])} />
    {(notebook.plate_positions ?? []).length ? <><h3>{c.plateLayout}</h3><div className='notebook-plate'>{notebook.plate_positions!.map((position, i) => <article key={`${position.label}-${i}`}><strong>{position.label}</strong><span>{position.step_label ? c.step(position.step_label) : c.outside}</span><p>{position.words}</p></article>)}</div></> : null}
    {(notebook.sample_rows ?? []).length ? <><h3>{c.sampleRecords}</h3><Rows heads={c.sampleRecordHeads} rows={notebook.sample_rows!.map(row => [row.identifier, row.records.join('\n')])} /></> : null}
  </>;
}

function InventoryNotebook({ notebook, reportId, editable, refresh }: { notebook: Notebook; reportId: string; editable: boolean; refresh: () => void }) {
  const inventory = notebook.inventory ?? [];
  const [selected, setSelected] = useState(inventory[0]?.name || '');
  const [vendor, setVendor] = useState(''), [catalog, setCatalog] = useState(''), [lot, setLot] = useState('');
  const [stage, setStage] = useState('after'), [busy, setBusy] = useState(false), [message, setMessage] = useState('');
  function choose(name: string) { const item = inventory.find(row => row.name === name); setSelected(name); setVendor(item?.vendor || ''); setCatalog(item?.catalog_number || ''); setLot(item?.lot_number || ''); }
  async function save() { setBusy(true); setMessage(''); try { await recordsApi.recordInventory(reportId, { item_name: selected, vendor, catalog_number: catalog, lot_number: lot, stage }); setMessage(c.saved); refresh(); } catch { setMessage(c.saveFailed); } finally { setBusy(false); } }
  return <>
    <h3>{c.methodsInventory}</h3>
    <Rows heads={c.inventoryHeads} rows={inventory.map(item => [item.name, item.kind, item.vendor, item.catalog_number, item.lot_number, c.inventoryStages[item.stage] || ''])} />
    {editable && inventory.length ? <div className='notebook-inventory-form'><label className='field'><span>{c.inventoryItem}</span><select value={selected} onChange={event => choose(event.target.value)}>{inventory.map(item => <option key={item.name} value={item.name}>{item.name}</option>)}</select></label><label className='field'><span>{c.vendor}</span><input maxLength={200} value={vendor} onChange={event => setVendor(event.target.value)} /></label><label className='field'><span>{c.catalog}</span><input maxLength={200} value={catalog} onChange={event => setCatalog(event.target.value)} /></label><label className='field'><span>{c.lot}</span><input maxLength={200} value={lot} onChange={event => setLot(event.target.value)} /></label><label className='field'><span>{c.inputStage}</span><select value={stage} onChange={event => setStage(event.target.value)}><option value='before'>{c.inventoryStages.before}</option><option value='after'>{c.inventoryStages.after}</option></select></label><button className='btn' disabled={busy || !selected || ![vendor, catalog, lot].some(value => value.trim())} onClick={() => void save()}>{c.inventorySave}</button>{message ? <p role='status'>{message}</p> : null}</div> : null}
  </>;
}

function SharingComparison({ reportId, editable }: { reportId: string; editable: boolean }) {
  const sharing = useQuery({ queryKey: ['notebook-sharing', reportId], queryFn: () => recordsApi.sharing(reportId), enabled: editable });
  const comparison = useQuery({ queryKey: ['notebook-comparisons', reportId], queryFn: () => recordsApi.comparisons(reportId), enabled: editable });
  const [busy, setBusy] = useState(false), [failed, setFailed] = useState(false);
  if (!editable) return null;
  async function toggle() { if (!sharing.data) return; setBusy(true); setFailed(false); try { await recordsApi.changeSharing(reportId, !sharing.data.shared); await sharing.refetch(); await comparison.refetch(); } catch { setFailed(true); } finally { setBusy(false); } }
  return <section className='record-section notebook-comparison'><h2>{c.comparison}</h2><p>{c.comparisonWhy}</p>
    {sharing.isPending ? <LoadingBox label={c.loading} /> : sharing.isError ? <Note>{c.comparisonFailed}</Note> : <button className='btn' disabled={busy} onClick={() => void toggle()}>{sharing.data.shared ? c.shareOff : c.shareOn}</button>}
    {failed ? <Note>{c.saveFailed}</Note> : null}
    {comparison.isPending ? <LoadingBox label={c.loading} /> : comparison.isError ? <Note>{c.comparisonFailed}</Note> : <div className='comparison-grid'>{comparison.data.records.map((record, i) => <article key={`${record.label}-${i}`} className={record.record_scope === 'shared' ? 'shared' : ''}><h3>{record.label}</h3>{record.value_rows.length ? <Rows heads={c.compareValueHeads} rows={record.value_rows.map(row => [row.step_label, row.source, row.actual])} /> : null}{record.method_rows.length ? <Rows heads={c.compareMethodHeads} rows={record.method_rows.map(row => [row.step_label, row.actual_values, [row.started_at, row.ended_at, row.timer].filter(Boolean).join(' · ')])} /> : null}{record.notes.map((note, j) => <p key={`${note.at}-${j}`}><strong>{note.step_label ? c.step(note.step_label) : c.outside}</strong> · {note.at} · {note.text}</p>)}</article>)}</div>}
    <p className='muted'>{c.comparisonRule}</p>
  </section>;
}

function NotebookTemplateEditor({ template, refresh }: { template: NotebookSection[]; refresh: () => void }) {
  const [sections, setSections] = useState(template), [busy, setBusy] = useState(false), [message, setMessage] = useState('');
  function rename(index: number, name: string) { setSections(rows => rows.map((row, i) => i === index ? { ...row, name } : row)); }
  function move(index: number, by: number) { setSections(rows => { const target = index + by; if (target < 0 || target >= rows.length) return rows; const next = [...rows]; [next[index], next[target]] = [next[target], next[index]]; return next; }); }
  async function save() { setBusy(true); setMessage(''); try { await recordsApi.saveNotebookTemplate(sections); setMessage(c.saved); refresh(); } catch { setMessage(c.saveFailed); } finally { setBusy(false); } }
  return <details className='record-section notebook-template'><summary>{c.template}</summary><p>{c.templateWhy}</p><ol>{sections.map((section, i) => <li key={section.id}><input aria-label={c.templateName} maxLength={60} value={section.name} onChange={event => rename(i, event.target.value)} /><button className='btn' aria-label={c.moveUp(section.name)} disabled={i === 0} onClick={() => move(i, -1)}>↑</button><button className='btn' aria-label={c.moveDown(section.name)} disabled={i === sections.length - 1} onClick={() => move(i, 1)}>↓</button></li>)}</ol><button className='btn' disabled={busy || sections.some(section => !section.name.trim())} onClick={() => void save()}>{c.templateSave}</button>{message ? <p role='status'>{message}</p> : null}</details>;
}

function GroundedNotebookSections({ notebook: n, data, reportId, editable, refresh }: { notebook: Notebook; data: NotebookResponse; reportId: string; editable: boolean; refresh: () => void }) {
  const methodRows = (n.method_rows ?? []).map(row => [
    row.label + (row.source_page ? ` · PDF p.${row.source_page}` : ''), row.source_text,
    row.actual_values, [row.started_at && row.ended_at ? `${row.started_at}–${row.ended_at}` : row.started_at || row.ended_at, row.timer].filter(Boolean).join(' · '),
  ]);
  const blank = <div className='notebook-blank'>{c.blank}</div>;
  const content: Record<string, ReactNode> = {
    experiment_info: <><Rows rows={n.header_rows} /><h3>{c.samples}</h3><Rows heads={c.sampleHeads} rows={n.samples.map(sample => [sample.name, [sample.condition, sample.control ? c.control : ''].filter(Boolean).join(' · ')])} /></>,
    purpose: n.purpose_words ? <p>{n.purpose_words}</p> : blank,
    methods: <><Rows heads={c.methodHeads} rows={methodRows} />{n.condition_rows.length ? <><h3>{c.conditions}</h3><Rows heads={c.tableHeads} rows={n.condition_rows} /></> : null}{n.condition_note ? <p>{n.condition_note}</p> : null}<NotebookVisuals notebook={n} />{n.differences.length ? <><h3>{c.differences}</h3>{n.differences.map((line, i) => <p key={i}>{line}</p>)}</> : null}</>,
    observations: n.observations.length ? <Rows heads={c.observationHeads} rows={n.observations.map(observation => [observation.label, observation.at, observation.shown])} /> : blank,
    results: (n.result_words || n.result) ? <p>{n.result_words || n.result}</p> : blank,
    interpretation_next: <div className='notebook-two-columns'><article><h3>{c.interpretation}</h3>{n.interpretation_words ? <p>{n.interpretation_words}</p> : blank}</article><article><h3>{c.next}</h3>{n.next_change || n.next_lines.length ? <>{[n.next_change, ...n.next_lines].filter(Boolean).map((line, i) => <p key={i}>{line}</p>)}</> : blank}</article></div>,
    domain: <DomainNotebook notebook={n} reportId={reportId} editable={editable} refresh={refresh} />,
    appendix: <><InventoryNotebook notebook={n} reportId={reportId} editable={editable} refresh={refresh} />{n.materials_words ? <p>{n.materials_words}</p> : null}<h3>{c.previous}</h3>{[n.previous_words, ...n.previous_lines].filter(Boolean).length ? [n.previous_words, ...n.previous_lines].filter(Boolean).map((line, i) => <p key={i}>{line}</p>) : blank}<h3>{c.confirmation}</h3><ul className='record-events'>{data.review.map(item => <ValueReview key={item.item_id} item={item} reportId={reportId} editable={editable && data.report.status !== 'in_progress'} refresh={refresh} />)}</ul><Rows rows={n.pi_rows} /><details><summary>{c.appendixRecords}</summary><Rows rows={n.appendix_step_rows} /><Rows rows={n.appendix_record_rows} /><p>{n.system_words}</p></details><h3>{c.source}</h3><p>{n.source_words}</p></>,
  };
  const template = n.section_template?.length === DEFAULT_NOTEBOOK_SECTIONS.length ? n.section_template : DEFAULT_NOTEBOOK_SECTIONS;
  return <>{template.map(section => <section className='record-section notebook-section' data-section={section.id} key={section.id}><h2>{section.name}</h2>{content[section.id]}</section>)}</>;
}

export function NotebookScreen({ sessionId, reportId, session }: { sessionId: string; reportId: string; session: AuthSession }) {
  const client = useQueryClient();
  const query = useQuery({ queryKey: notebookKey(reportId), queryFn: () => recordsApi.notebook(reportId) });
  const workspace = useWorkspaceSession();
  const labTemplate = useQuery({ queryKey: ['notebook-template'], queryFn: recordsApi.notebookTemplate, enabled: Boolean(session.account?.admin) });
  useSignOutOnExpiry([query.error, workspace.error, labTemplate.error].find(isSessionExpired));
  const data = query.data, mismatched = Boolean(data?.report.session_id && data.report.session_id !== sessionId), n = data?.notebook;
  const editable = Boolean(workspace.data?.principal_id && data?.report.owner_principal_id === workspace.data.principal_id);
  const otherOwner = Boolean(workspace.data?.principal_id && data?.report.owner_principal_id && data.report.owner_principal_id !== workspace.data.principal_id);
  const refresh = () => { void query.refetch(); void client.invalidateQueries({ queryKey: ['report-prose', reportId] }); };
  const refreshTemplate = () => { void labTemplate.refetch(); refresh(); };
  return <section className='records-screen notebook-screen' data-testid='notebook-screen'>
    <a className='btn btn-quiet' href={routes.record(sessionId)}>{c.timeline}</a><h1>{c.notebooks}</h1><p className='page-why'>{c.notebookWhy}</p>
    {query.isSuccess && !mismatched ? <ReportPreparation key={reportId} reportId={reportId} editable={editable && data!.report.status !== 'in_progress'} refresh={refresh} /> : null}
    {query.isPending ? <LoadingBox label={c.loading} /> : query.isError || mismatched ? <ErrorBox message={c.failed} onRetry={refresh} /> : !n ? <EmptyBox message={c.noNotebook} /> : <>
      <h2>{n.title}</h2><Downloads key={reportId} reportId={reportId} />
      {!editable ? <p>{otherOwner ? c.readOnly : c.readOnlyGeneral}</p> : null}
      {session.account?.admin && labTemplate.data?.sections.length ? <NotebookTemplateEditor key={labTemplate.data.version} template={labTemplate.data.sections} refresh={refreshTemplate} /> : null}
      <GroundedNotebookSections notebook={n} data={data} reportId={reportId} editable={editable} refresh={refresh} />
      {session.account?.admin ? <PiConfirmation reportId={reportId} refresh={refresh} /> : null}
      <SharingComparison reportId={reportId} editable={editable} />
    </>}
  </section>;
}
