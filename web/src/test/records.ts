// Synthetic records only: three different fields, including chemistry and battery materials.
export const RECORDS = ['세포 염색 견본', '용액 혼합 견본', '전극 코팅 견본'].map((title, i) => ({
  session_id: `rx3-${i}`, protocol_id: `source-${i}`, protocol_title: title,
  status: i === 1 ? 'stopped' : 'completed', completed_step_count: i === 1 ? 1 : 2,
  owner_principal_id: i === 2 ? 'other' : 'me', started_at: `2026-10-${25+i}T01:00:00Z`,
  rating: i === 0 ? 4 : null, rating_state: i === 0 ? 'rated' : 'unrated',
}));
export const REPORT = { report_id: 'report-rx3', protocol_title: RECORDS[0].protocol_title, status: 'completed', started_at: RECORDS[0].started_at };
export const TIMELINE = { session: RECORDS[0], timeline: [
  { event_id: 'w-timer', event_key: 'voice-2-7-start_timer', event_type: 'timer_started', step_label: '1', created_at: '2026-10-25T01:02:00Z', payload: { timer: { duration_seconds: 60 } } },
  { event_id: 'w1', event_type: 'observation_recorded', step_label: '1', created_at: '2026-10-25T01:01:00Z', observation: { content: '시료가 맑아 보여요', capture_source: 'voice' } },
  { event_id: 'w2', event_type: 'observation_recorded', step_label: '1', observation: { content: '첫 메모', current_content: '고친 메모', corrected: true, capture_source: 'manual' } },
  { event_id: 'w3', event_type: 'evidence_attached', step_label: '1', evidence: { evidence_id: 'photo-1', original_filename: 'sample.png', media_type: 'image/png', caption: '시료 사진' } },
] };
export const NOTEBOOK = {
  report: REPORT, events: [
    { event_key: 'turn-7-generation-2-start_timer', event_type: 'timer_started', step_label: '1', created_at: '2026-10-25T01:02:00Z', payload: { timer: { source_duration_seconds: 60, elapsed_seconds: 0, remaining_seconds: 60 } } },
    { event_key: 'r2', event_type: 'problem_help', step_label: '2', user_wording: '침전이 보여요', payload: { sources: [{ tier: 'protocol', title: '견본 원문' }], state_mutation: false } },
    { event_key: 'r3', event_type: 'session_started', payload: { run_changes: [{ step_label: '1', source_written: '60 s', number: '90', unit: 's' }] } },
  ],
  review: [{ item_id: 'value-1', step_label: '1', kind: '수치', text: '90 s', confirmed: false }],
  notebook: { title: '견본 실험', subtitle: '실험 노트', header_rows: [['프로토콜', RECORDS[0].protocol_title]], purpose_words: '견본 비교', samples: [{ name: 'S1', condition: '견본', control: false }], condition_rows: [['1단계 · 시간', '원문 60 s → 실제 90 s [미확인]']], condition_note: '', observations: [{ label: '1', at: '10:01', kind: '관찰', shown: '시료가 맑아 보여요', text: '시료가 맑아 보여요' }], differences: ['1단계 시간: 원문 60 s → 90 s'], result_words: '견본 관찰', result: '', next_change: '', previous_words: '', previous_lines: [], next_lines: [], pi_rows: [], appendix_step_rows: [], appendix_record_rows: [], source_words: '가짜 원문', materials_words: '', system_words: '' },
};
