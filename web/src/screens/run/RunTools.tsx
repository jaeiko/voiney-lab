import * as Dialog from '@radix-ui/react-dialog';
import type { AuthSession } from '../../api/types';
import { advancedSettings as c } from '../../copy/ko';
import { useRun } from '../../voice/runStore';
import { SettingsScreen } from '../SettingsScreen';

export function RunTools({ session }: { session: AuthSession }) {
 const { model } = useRun();
 return <section className='run-tools'>
  <Dialog.Root><Dialog.Trigger asChild><button className='btn run-btn'>{c.settings}</button></Dialog.Trigger><Dialog.Portal><Dialog.Overlay className='dialog-backdrop' /><Dialog.Content className='dialog run-settings-dialog' aria-describedby='run-settings-why'><Dialog.Title>{c.title}</Dialog.Title><Dialog.Description id='run-settings-why'>{c.nextRun}</Dialog.Description><Dialog.Close asChild><button className='btn'>{c.close}</button></Dialog.Close><SettingsScreen session={session} duringRun /></Dialog.Content></Dialog.Portal></Dialog.Root>
  <details><summary>{c.ignoredCount(model.droppedCount)}</summary><p>{c.ignoredWhy}</p>{!model.dropped.length ? <p>{c.ignoredEmpty}</p> : <ol>{model.dropped.map((line, i) => <li key={i}><time>{new Date(line.at).toLocaleTimeString('ko-KR')}</time> · {line.reason}</li>)}</ol>}</details>
  {session.mode === 'dev' || session.account?.admin ? <details data-testid='run-diagnostics'><summary>{c.diagnostics}</summary><p>{c.diagnosticsWhy}</p><pre>{JSON.stringify(model.diagnostics, null, 2)}</pre></details> : null}
 </section>;
}
