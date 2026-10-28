import * as Dialog from '@radix-ui/react-dialog';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { apiFetch, postJson } from '../api/client';
import { useSignOutOnExpiry } from '../api/queries';
import { experimentPath, reportPath, type Recovery } from '../api/recordsApi';
import { idempotency } from '../api/runApi';
import { ErrorBox, LoadingBox, Note } from '../components/Pieces';
import { recordActions as c, frame } from '../copy/ko';
import { go, routes } from '../lib/navigation';

const allowedFiles = new Set(['image/jpeg', 'image/png', 'image/webp', 'application/pdf', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document']);

export function RecordCapture({ sessionId, refresh }: { sessionId: string; refresh: () => void }) {
 const [text, setText] = useState(''), [category, setCategory] = useState('note'), [caption, setCaption] = useState('');
 const [busy, setBusy] = useState(false), [message, setMessage] = useState('');
 // Retain a key for retries after an uncertain response; editing the content starts a new operation.
 const memoKey = useRef(idempotency('manual-observation'));
 const fileRetry = useRef<{ file: File; caption: string; key: string } | null>(null);
 async function save() {
  if (busy || !text.trim()) return; setBusy(true); setMessage('');
  try { await postJson(experimentPath(sessionId, '/observations'), { content: text, category, idempotency_key: memoKey.current }); memoKey.current = idempotency('manual-observation'); setText(''); setMessage(c.saved); refresh(); }
  catch { setMessage(c.failed); } finally { setBusy(false); }
 }
 async function attach(file: File) {
  if (busy) return; setMessage('');
  if (!allowedFiles.has(file.type)) { setMessage(c.fileInvalid); return; }
  if (file.size > 32 * 1024 * 1024) { setMessage(c.fileLarge); return; }
  const old = fileRetry.current;
  const key = old && old.file.name === file.name && old.file.size === file.size && old.file.lastModified === file.lastModified && old.caption === caption ? old.key : idempotency('evidence');
  fileRetry.current = { file, caption, key }; setBusy(true);
  try { const query = new URLSearchParams({ filename: file.name, idempotency_key: key, caption }); await apiFetch(experimentPath(sessionId, `/evidence?${query}`), { method: 'POST', headers: { 'Content-Type': file.type }, body: file }); fileRetry.current = null; setMessage(c.attached); refresh(); }
  catch { setMessage(c.failed); } finally { setBusy(false); }
 }
 return <section className='record-section record-actions' data-testid='record-capture'><h2>{c.title}</h2><p>{c.why}</p>
  <label className='field'><span>{c.category}</span><select value={category} disabled={busy} onChange={e => { setCategory(e.target.value); memoKey.current = idempotency('manual-observation'); }}>{Object.entries(c.categories).map(([key, word]) => <option key={key} value={key}>{word}</option>)}</select></label>
  <label className='field'><span>{c.memo}</span><textarea maxLength={4000} value={text} disabled={busy} onChange={e => { setText(e.target.value); memoKey.current = idempotency('manual-observation'); }} /></label>
  <button className='btn' disabled={busy || !text.trim()} onClick={() => void save()}>{c.save}</button>
  <label className='field'><span>{c.caption}</span><input value={caption} maxLength={300} disabled={busy} onChange={e => setCaption(e.target.value)} /></label>
  <label className='btn'><span>{c.file}</span><input className='sr-only' type='file' accept={[...allowedFiles].join(',')} disabled={busy} onChange={e => { const f = e.target.files?.[0]; e.target.value = ''; if (f) void attach(f); }} /></label>
  {message ? <p role='status'>{message}</p> : null}
 </section>;
}

export function CheckpointRestart({ sessionId, version, recovery, refresh, disabled }: { sessionId: string; version: number; recovery?: Recovery; refresh: () => void; disabled: boolean }) {
 const checkpoints = recovery?.checkpoints ?? [];
 const [stepId, setStepId] = useState(''), [open, setOpen] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
 const picked = checkpoints.find(p => p.step_id === stepId);
 async function restart() {
  if (!picked || busy) return; setBusy(true); setError('');
  try { const result = await postJson<{ session_id: string; protocol_id: string }>(experimentPath(sessionId, '/checkpoint-restart'), { checkpoint_step_id: stepId, expected_version: version }); setOpen(false); go(routes.start(result.protocol_id, { resume: result.session_id })); }
  catch { setError(c.restartFailed); setOpen(false); setStepId(''); refresh(); } finally { setBusy(false); }
 }
 if (!checkpoints.length) return null;
 return <section className='record-section'><h2>{c.checkpoints}</h2><p>{c.restartWhy}</p>
  <label className='field'><span>{c.checkpoints}</span><select value={stepId} disabled={disabled || busy} onChange={e => setStepId(e.target.value)}><option value=''>{c.choose}</option>{checkpoints.map(p => <option key={p.step_id} value={p.step_id}>{c.checkpoint(p.step_label, p.carried_step_count)}</option>)}</select></label>
  <Dialog.Root open={open} onOpenChange={value => { if (!busy) setOpen(value); }}><Dialog.Trigger asChild><button className='btn' disabled={disabled || !picked || busy || !Number.isInteger(version) || version < 1}>{c.restart}</button></Dialog.Trigger><Dialog.Portal><Dialog.Overlay className='dialog-backdrop' /><Dialog.Content className='dialog' aria-describedby='restart-why'><Dialog.Title>{c.confirm}</Dialog.Title><Dialog.Description id='restart-why'>{c.notRestored}</Dialog.Description>{picked ? <p>{c.checkpoint(picked.step_label, picked.carried_step_count)}</p> : null}<div className='record-actions'><Dialog.Close asChild><button className='btn' disabled={busy}>{frame.cancel}</button></Dialog.Close><button className='btn btn-primary' disabled={busy || !picked} onClick={() => void restart()}>{c.confirm}</button></div></Dialog.Content></Dialog.Portal></Dialog.Root>
  {error ? <Note>{error}</Note> : null}
 </section>;
}

export function ReportPreparation({ reportId, editable, refresh }: { reportId: string; editable: boolean; refresh: () => void }) {
 const client = useQueryClient();
 const query = useQuery({ queryKey: ['report-prose', reportId], queryFn: () => apiFetch<{ state: string; label?: string }>(reportPath(reportId, '/prose')), refetchInterval: q => q.state.data?.state === 'preparing' ? 3000 : false, refetchIntervalInBackground: false, retry: false });
 const [busy, setBusy] = useState(false), [error, setError] = useState('');
 const previous = useRef<string | null>(null);
 useSignOutOnExpiry(query.error);
 useEffect(() => { if (previous.current === 'preparing' && query.data?.state === 'ready') refresh(); previous.current = query.data?.state ?? null; }, [query.data?.state, refresh]);
 async function regenerate() {
  if (busy) return; setBusy(true); setError('');
  try { const result = await postJson<{ state: string; label?: string }>(reportPath(reportId, '/prose'), {}); client.setQueryData(['report-prose', reportId], result); if (result.state === 'ready') refresh(); }
  catch { setError(c.regenerateFailed); } finally { setBusy(false); }
 }
 return <section className='record-section'><h2>{c.prose}</h2><p>{c.proseWhy}</p>{query.isPending ? <LoadingBox /> : query.isError ? <ErrorBox message={c.proseFailed} onRetry={() => void query.refetch()} /> : <p role='status'>{query.data?.label}</p>}
  {editable && ['ready', 'not_prepared'].includes(query.data?.state ?? '') ? <button className='btn' disabled={busy} onClick={() => void regenerate()}>{c.regenerate}</button> : null}{error ? <Note>{error}</Note> : null}
 </section>;
}
