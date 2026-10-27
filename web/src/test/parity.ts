// Entirely synthetic documents and values, never a protocol for actual use.
export const PARITY_DOCUMENTS = [
  { id: 'cells', title: '세포 염색 가짜 자료', material: 'stain A', equipment: 'microscope', previousValue: '0.40 mL', value: '0.50 mL', instruction: 'Add 0.50 mL stain A.', condition: 'Keep covered.' },
  { id: 'solution', title: '화학 용액 가짜 자료', material: 'NaCl', equipment: 'balance', previousValue: '0.10 M', value: '0.15 M', instruction: 'Mix 0.15 M NaCl.', condition: 'Record appearance.' },
  { id: 'battery', title: '배터리 전극 가짜 자료', material: 'slurry B', equipment: 'coater', previousValue: '60 µm', value: '80 µm', instruction: 'Coat slurry B at 80 µm.', condition: 'Record coating.' },
];
export function parityReview(index: number) {
 const p = PARITY_DOCUMENTS[index];
 return {
  protocol_id: p.id, title: p.title, source_filename: `${p.id}.pdf`, available_for_execution: true, development_only: false, revision_id: `${p.id}-revision-1`, step_count: 2,
  source: { filename: `${p.id}.pdf`, sha256: 'a'.repeat(64), page_count: 3 },
  materials: [{ material_id: 'm1', name_source_text: p.material, quantities: [{ source_text: p.value }] }], equipment: [{ equipment_id: 'e1', name_source_text: p.equipment }], before_start: [], constructs: [],
  sections: [{ section_id: 'section1', title_source_text: 'Synthetic example', steps: [{ step_id: 's1', source_label: '1', instruction_source_text: p.instruction, evidence: { source_page_number: 2, source_excerpt: p.instruction }, sub_actions: [{ action_id: 'a1', instruction_source_text: p.instruction, conditions: [{ source_text: p.condition }] }] }, { step_id: 's2', source_label: '2', instruction_source_text: 'Record the result.', evidence: { source_page_number: 3, source_excerpt: 'Record the result.' }, sub_actions: [] }] }],
  execution_blockers: [], execution_notices: [{ message_ko: '시험용 가짜 자료예요.' }], safety_notices: [], metadata: { title: p.title },
  review_versions: [{ version: 1, version_ko: '검토본 v1', reviewer_name: '견본 확인자', registered_on: '2026년 10월 27일' }],
 };
}
export const PARITY_TABLES = { reagent_tables: [{ table_id: 'table1', filename: 'synthetic.xlsx', sheets: [{ name: 'Example', state: 'visible', cells: [{ row: 1, column: 1, display: 'NaCl', value: 'NaCl' }, { row: 1, column: 2, display: '0.15 M', value: '0.15 M' }] }] }] };
