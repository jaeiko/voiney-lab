import { expect, it } from 'vitest';
import { diagnosticOf } from './runDiagnostics';
it('keeps only diagnostic codes and finite times; never transcript, reasoning or arbitrary router data', () => {
 expect(diagnosticOf({ type: 'turn.done', turn_id: 2, reasoning: 'private', transcript: 'private', normalized_text: 'private', timings_ms: { first_audio_ms: 27, invalid: Infinity }, router: { answered_by: 'source', reasoning: 'private' } })).toEqual({ type: 'turn.done', turn: 2, fields: { 'router.answered_by': 'source' }, timings: { first_audio_ms: 27 } });
 expect(diagnosticOf({ type: 'reply.complete', reasoning: 'private' })).toBeNull();
});
