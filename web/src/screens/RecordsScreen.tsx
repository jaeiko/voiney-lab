import { useQueries, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState, type MouseEvent } from 'react';
import { postJson } from '../api/client';
import { experimentPath, recordsApi, reportPath, type RecordEvent, type ReviewValue } from '../api/recordsApi';
import { isSessionExpired, signedOut, useExperiments, useSignOutOnExpiry, useWorkspaceSession } from '../api/queries';
import type { AuthSession, Experiment } from '../api/types';
import { EmptyBox, ErrorBox, LoadingBox, Note } from '../components/Pieces';
import { records as c } from '../copy/ko';
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

export function NotebookScreen({ sessionId, reportId, session }: { sessionId: string; reportId: string; session: AuthSession }) {
  const query = useQuery({ queryKey: notebookKey(reportId), queryFn: () => recordsApi.notebook(reportId) });
  const workspace = useWorkspaceSession();
  useSignOutOnExpiry([query.error, workspace.error].find(isSessionExpired));
  const data = query.data, mismatched = Boolean(data?.report.session_id && data.report.session_id !== sessionId), n = data?.notebook;
  const editable = Boolean(workspace.data?.principal_id && data?.report.owner_principal_id === workspace.data.principal_id);
  const otherOwner = Boolean(workspace.data?.principal_id && data?.report.owner_principal_id && data.report.owner_principal_id !== workspace.data.principal_id);
  const refresh = () => void query.refetch();
  return <section className='records-screen notebook-screen' data-testid='notebook-screen'>
    <a className='btn btn-quiet' href={routes.record(sessionId)}>{c.timeline}</a><h1>{c.notebooks}</h1><p className='page-why'>{c.notebookWhy}</p>
    {query.isPending ? <LoadingBox label={c.loading} /> : query.isError || mismatched ? <ErrorBox message={c.failed} onRetry={refresh} /> : !n ? <EmptyBox message={c.noNotebook} /> : <>
      <h2>{n.title}</h2><Downloads key={reportId} reportId={reportId} /><Rows rows={n.header_rows} />
      {!editable ? <p>{otherOwner ? c.readOnly : c.readOnlyGeneral}</p> : null}
      {n.purpose_words ? <section className='record-section'><h2>{c.purpose}</h2><p>{n.purpose_words}</p></section> : null}
      <section className='record-section'><h2>{c.samples}</h2><Rows heads={c.sampleHeads} rows={n.samples.map(s => [s.name, [s.condition, s.control ? c.control : ''].filter(Boolean).join(' · ')])} />{n.materials_words ? <p>{n.materials_words}</p> : null}</section>
      <section className='record-section'><h2>{c.conditions}</h2><Rows heads={c.tableHeads} rows={n.condition_rows} />{n.condition_note ? <p>{n.condition_note}</p> : null}</section>
      <section className='record-section'><h2>{c.observations}</h2><Rows heads={c.observationHeads} rows={n.observations.map(o => [o.label, o.at, o.shown])} /><p>{n.result_words || n.result}</p></section>
      {([[c.differences, n.differences], [c.previous, [n.previous_words, ...n.previous_lines]], [c.next, [n.next_change, ...n.next_lines]]] as const).map(([head, lines]) => <section className='record-section' key={head}><h2>{head}</h2>{lines.filter(Boolean).map((line, i) => <p key={i}>{line}</p>)}</section>)}
      <section className='record-section'><h2>{c.confirmation}</h2><ul className='record-events'>{data.review.map(item => <ValueReview key={item.item_id} item={item} reportId={reportId} editable={editable && data.report.status !== 'in_progress'} refresh={refresh} />)}</ul><Rows rows={n.pi_rows} /></section>
      {session.account?.admin ? <PiConfirmation reportId={reportId} refresh={refresh} /> : null}
      <details><summary>{c.appendix}</summary><Rows rows={n.appendix_step_rows} /><Rows rows={n.appendix_record_rows} /><p>{n.system_words}</p></details>
      <section className='record-section'><h2>{c.source}</h2><p>{n.source_words}</p></section>
    </>}
  </section>;
}
