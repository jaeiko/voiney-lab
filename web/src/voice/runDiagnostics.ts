// Screen-only, bounded, memory-only views of existing server events. No workflow decisions.
export interface Diagnostic { type: string; turn: number | null; fields: Record<string, string | number | boolean>; timings: Record<string, number> }
const allowed = ['intent', 'reason_code', 'runtime_router', 'action', 'state_mutation', 'answer_origin', 'fallback_reason', 'stage', 'state', 'operation', 'source_verified'] as const;
export function diagnosticOf(m: Record<string, unknown>): Diagnostic | null {
 if (!['turn.route_decision', 'turn.processing', 'turn.done', 'server.operation'].includes(String(m.type))) return null;
 const fields: Diagnostic['fields'] = {};
 for (const key of allowed) { const value = m[key]; if (typeof value === 'string' || typeof value === 'boolean' || typeof value === 'number' && Number.isFinite(value)) fields[key] = value; }
 const router = m.router;
 if (router && typeof router === 'object') for (const key of ['handled_by', 'fallback_reason', 'verdict', 'read_only', 'answered_by']) { const value = (router as Record<string, unknown>)[key]; if (typeof value === 'string') fields[`router.${key}`] = value; }
 const timings: Record<string, number> = {};
 if (m.timings_ms && typeof m.timings_ms === 'object') for (const [key, value] of Object.entries(m.timings_ms)) if (/^[a-z_]{1,64}$/.test(key) && typeof value === 'number' && Number.isFinite(value)) timings[key] = value;
 return { type: String(m.type), turn: Number.isInteger(m.turn_id) ? m.turn_id as number : null, fields, timings };
}
