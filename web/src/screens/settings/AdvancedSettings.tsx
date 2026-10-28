import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { runApi } from '../../api/runApi';
import type { ExperimenterSettings } from '../../api/runApi';
import { advancedSettings as c, settings as copy } from '../../copy/ko';
import { DEVICE_KEYS, deviceValue, inputLanguage, storedMicrophone } from '../../voice/devicePreferences';
import { SETTINGS_KEY } from './VoicePicker';

function ServerChoice({ name, label, options, value }: { name: string; label: string; options: Record<string, string>; value: string }) {
 const client = useQueryClient();
 const [busy, setBusy] = useState(false), [message, setMessage] = useState('');
 async function save(next: string) { if (busy) return; setBusy(true); setMessage(''); try { client.setQueryData(SETTINGS_KEY, await runApi.saveSetting(name, next)); setMessage(copy.saved); } catch { setMessage(copy.saveFailed); void client.invalidateQueries({ queryKey: SETTINGS_KEY }); } finally { setBusy(false); } }
 return <div><label className='field'><span>{label}</span><select value={value} disabled={busy} onChange={e => void save(e.target.value)}>{Object.entries(options).map(([key, word]) => <option key={key} value={key}>{word}</option>)}</select></label>{message ? <p role='status'>{message}</p> : null}</div>;
}
export function AdvancedSettings({ settings, active, embedded = false }: { settings: ExperimenterSettings; active: boolean; embedded?: boolean }) {
 const [language, setLanguage] = useState(inputLanguage), [gain, setGain] = useState(() => deviceValue(DEVICE_KEYS.gain, ['calibrated', 'on', 'off'], 'calibrated'));
 const [mic, setMic] = useState(storedMicrophone), [devices, setDevices] = useState<MediaDeviceInfo[]>([]), [message, setMessage] = useState('');
 async function list() { try { const all = await navigator.mediaDevices?.enumerateDevices?.() ?? []; setDevices(all.filter(d => d.kind === 'audioinput' && d.deviceId && !['default', 'communications'].includes(d.deviceId))); } catch { setDevices([]); } }
 useEffect(() => { void list(); navigator.mediaDevices?.addEventListener?.('devicechange', list); return () => navigator.mediaDevices?.removeEventListener?.('devicechange', list); }, []);
 function store(key: string, value: string, done: () => void) { try { localStorage.setItem(key, value); done(); setMessage(copy.saved); } catch { setMessage(c.localFailed); } }
 const connected = devices.find(d => d.deviceId === mic?.deviceId) ?? devices.find(d => Boolean(mic?.label && d.label === mic.label));
 const connectedAt = connected ? devices.indexOf(connected) : -1;
 const microphoneChoice = connected ? (connected.label || c.microphoneUnknown(connectedAt + 1)) : mic ? `${mic.label || c.microphoneUnknown(1)} · ${c.microphoneMissing}` : c.microphoneDefault;
 return <section className='settings-section' data-testid='settings-advanced'>{!embedded ? <h2>{c.title}</h2> : null}
  <ServerChoice name='question_timing' label={c.timing} value={settings.question_timing ?? 'before_start'} options={c.timingOptions} />
  <ServerChoice name='proactive_mode' label={c.proactive} value={settings.proactive_mode ?? 'all'} options={c.proactiveOptions} />
  <ServerChoice name='web_lookup' label={c.web} value={settings.web_lookup ?? 'on'} options={c.webOptions} /><p>{c.webWhy}</p>
  <ServerChoice name='speaker_sensitivity' label={c.sensitivity} value={settings.speaker_sensitivity ?? 'normal'} options={c.sensitivityOptions} />
  <p>{c.local}</p>
  <label className='field'><span>{c.language}</span><select disabled={active} value={language} onChange={e => store(DEVICE_KEYS.language, e.target.value, () => setLanguage(e.target.value))}>{Object.entries(c.languageOptions).map(([key, word]) => <option key={key} value={key}>{word}</option>)}</select></label>
  <p>{c.microphoneLead}</p>
  <label className='field'><span>{c.microphone}</span><select disabled={active} value={connected?.deviceId ?? mic?.deviceId ?? ''} onChange={e => { const d = devices.find(item => item.deviceId === e.target.value); const selected = d ? { deviceId: d.deviceId, label: d.label } : null; store(DEVICE_KEYS.microphone, JSON.stringify(selected), () => setMic(selected)); }}><option value=''>{c.microphoneDefault}</option>{devices.map((d, i) => <option key={d.deviceId} value={d.deviceId}>{d.label || c.microphoneUnknown(i + 1)}</option>)}{mic && !connected ? <option value={mic.deviceId}>{mic.label} · {c.microphoneMissing}</option> : null}</select></label>
  <p className='saved' role='status' data-testid='microphone-choice'>{c.microphoneChosen(microphoneChoice)}</p>
  <button className='btn' type='button' onClick={() => void list()}>{c.microphoneRefresh}</button>
  <label className='field'><span>{c.gain}</span><select disabled={active} value={gain} onChange={e => store(DEVICE_KEYS.gain, e.target.value, () => setGain(e.target.value))}>{Object.entries(c.gainOptions).map(([key, word]) => <option key={key} value={key}>{word}</option>)}</select></label>
  {message ? <p role='status'>{message}</p> : null}
 </section>;
}
