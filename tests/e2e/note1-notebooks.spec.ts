import { expect, test, type Page, type TestInfo } from '@playwright/test';
import { mkdir } from 'node:fs/promises';
import { join } from 'node:path';

const sections = [
  ['experiment_info', '실험 정보'], ['purpose', '목적'], ['methods', '방법'],
  ['observations', '관찰·특이사항'], ['results', '결과'],
  ['interpretation_next', '해석·다음 할 일'], ['domain', '분야별 기록'], ['appendix', '부록'],
].map(([id, name]) => ({ id, name }));

const profiles = {
  biology: {
    title: 'A549 약물 처리 생존율', purpose: 'A549 세포의 약물 처리 뒤 생존율을 비교하려고요.',
    source: 'Seed A549 cells at 5000 cells per well in a 96-well plate.', actual: 'well 당 5000 cells · A1 well',
    observation: 'A1 well 탁함', result: 'A1 well 생존율 82%',
    domain: { code: 'biology', label: '바이오', selected_by: 'source_analysis', fields: [
      { label: '세포주', value: 'A549 cells', origin: 'source' },
      { label: '계대 수', value: '계대 수 18', origin: 'record' },
      { label: '플레이트 배치·well 위치', value: 'A1 well 탁함', origin: 'record' },
    ] },
    samples: [{ name: 'A1 well', condition: '5 µM 처리군', control: false }, { name: 'B1 well', condition: 'vehicle', control: true }],
    plate_positions: [{ label: 'A1 well', step_label: '1', words: 'A1 well 탁함' }], sample_rows: [],
    inventory: [{ kind: '재료', name: 'MTT reagent', vendor: 'Sigma-Aldrich', catalog_number: 'M5655', lot_number: 'LOT-24', stage: 'after' }],
  },
  chemistry: {
    title: 'TEOS 졸겔 합성', purpose: 'TEOS 졸겔 반응의 실제 수득량을 기록하려고요.',
    source: 'Mix 5 mL TEOS with 20 mL ethanol at 300 rpm.', actual: '5 mL · 20 mL · 300 rpm',
    observation: '실제 수득량 2 mg, 이론 수득량 3 mg', result: '',
    domain: { code: 'chemistry', label: '화학 합성', selected_by: 'source_analysis', fields: [
      { label: '반응식', value: '', origin: 'blank' },
      { label: '시약(몰·질량·당량)', value: 'TEOS 5 mL · ethanol 20 mL', origin: 'source' },
      { label: '수득량·수율', value: '실제 2 mg · 이론 3 mg · 수율 66.67%', origin: 'calculated', formula: '2 mg ÷ 3 mg × 100 = 66.67%' },
      { label: 'TLC Rf', value: '', origin: 'blank' }, { label: '정제 조건', value: '', origin: 'blank' },
    ] },
    samples: [{ name: '반응물 R1', condition: 'TEOS 5 mL', control: false }], plate_positions: [], sample_rows: [],
    inventory: [{ kind: '재료', name: 'TEOS', vendor: 'Sigma-Aldrich', catalog_number: '131903', lot_number: 'MKCD-31', stage: 'before' }],
  },
  battery: {
    title: 'B-17 코인셀 조립', purpose: 'B-17 셀의 첫 사이클 기록을 남기려고요.',
    source: 'Use cathode sample B-17 with electrode loading 8.2 mg/cm2.', actual: '8.2 mg/cm2 → 8.1 mg/cm2',
    observation: 'B-17 전해액 80 µL', result: '',
    domain: { code: 'battery_materials', label: '배터리·소재', selected_by: 'source_analysis', fields: [
      { label: '셀·시료 번호', value: 'B-17', origin: 'record' },
      { label: '전극 로딩', value: 'B-17 전극 로딩 8.1 mg/cm2', origin: 'record' },
      { label: '전해액 양', value: 'B-17 전해액 80 µL', origin: 'record' },
      { label: '조립 환경(수분·산소)', value: 'H2O 0.2 ppm · O2 0.3 ppm', origin: 'source' },
      { label: '크림핑·소성 조건', value: '1000 psi', origin: 'source' },
      { label: '사이클 조건', value: '0.1 C · 2.5–4.2 V', origin: 'source' },
    ] },
    samples: [{ name: 'B-17', condition: 'CR2032', control: false }], plate_positions: [],
    sample_rows: [{ identifier: 'B-17', records: ['B-17 전극 로딩 8.1 mg/cm2', 'B-17 전해액 80 µL'] }],
    inventory: [{ kind: '재료', name: 'Electrolyte', vendor: 'Fictional Materials', catalog_number: 'EL-80', lot_number: 'LOT-B17', stage: 'before' }],
  },
} as const;

function notebook(kind: keyof typeof profiles) {
  const p = profiles[kind];
  return {
    report: { report_id: `report-${kind}`, session_id: `run-${kind}`, protocol_title: p.title, status: 'completed', started_at: '2026-10-28T01:00:00Z', owner_principal_id: 'me' },
    review: [], events: [],
    notebook: {
      title: p.title, subtitle: '실험 노트', header_rows: [['날짜·시간', '2026년 10월 28일 10:00–11:20'], ['실험자', '가짜 연구자'], ['프로토콜', `${p.title} · rev-2`], ['출처', 'Fictional source protocol · PDF']],
      purpose_words: p.purpose, samples: p.samples, condition_rows: [['1단계', `원문 ${p.source} → 실제 ${p.actual}`]], condition_note: '', materials_words: '',
      observations: [{ label: '1', at: '10:24', kind: '측정', shown: p.observation, text: p.observation }], result: p.result, result_words: p.result, next_change: '', differences: [],
      previous_words: '', previous_lines: [], next_lines: [], pi_rows: [], appendix_step_rows: [['1', '10:00', '10:24', '24분', '—']], appendix_record_rows: [['10:24', '1', '측정', p.observation]], source_words: 'Fictional source protocol · PDF p.2', system_words: '시스템 사건: 연결 끊김 0회',
      method_rows: [{ label: '1', source_text: p.source, actual_values: p.actual, started_at: '10:00', ended_at: '10:24', timer: '' }],
      timeline: [{ at: '10:00', step_label: '1', kind: 'start', text: '실험 시작' }, { at: '10:24', step_label: '1', kind: 'observation', text: p.observation }],
      value_bars: [{ label: '1단계 · 기록 값', kind: '양', source: kind === 'biology' ? '100 µL' : kind === 'chemistry' ? '5 mL' : '8.2 mg', actual: kind === 'biology' ? '80 µL' : kind === 'chemistry' ? '4 mL' : '8.1 mg', source_number: kind === 'biology' ? '100' : kind === 'chemistry' ? '5' : '8.2', actual_number: kind === 'biology' ? '80' : kind === 'chemistry' ? '4' : '8.1', unit: kind === 'biology' ? 'µL' : kind === 'chemistry' ? 'mL' : 'mg' }],
      images: [{ kind: 'photo', step_label: '1', caption: `${p.title} 기록 사진`, url: `/api/workspace/experiments/run-${kind}/evidence/photo-${kind}`, media_type: 'image/svg+xml', asset_id: `photo-${kind}` }, { kind: 'source_figure', step_label: '1', caption: '원문 단계 그림', url: `/api/protocols/${kind}/revisions/rev-2/figures/source-figure-2-1`, media_type: 'image/svg+xml', asset_id: 'source-figure-2-1', source_page: 2 }],
      plate_positions: p.plate_positions, sample_rows: p.sample_rows, domain: p.domain, inventory: p.inventory,
      interpretation_words: '', section_template: sections,
    },
  };
}

async function mock(page: Page, kind: keyof typeof profiles) {
  await page.route('**/api/**', route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    if (path.includes('/evidence/') || path.includes('/figures/')) return route.fulfill({ contentType: 'image/svg+xml', body: `<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="#efefec"/><rect x="70" y="70" width="500" height="220" fill="white" stroke="#1c1c1a"/><text x="100" y="185" font-size="28">${profiles[kind].title}</text></svg>` });
    if (path === '/api/auth/session') return route.fulfill({ json: { mode: 'dev', signed_in: true, account: { account_id: 'me', name: '가짜 연구자', organization_id: 'lab-demo', admin: false } } });
    if (path === '/api/workspace/session') return route.fulfill({ json: { principal_id: 'me' } });
    if (path === '/api/protocols') return route.fulfill({ json: { protocols: [] } });
    if (path === `/api/experiment-reports/report-${kind}/notebook`) return route.fulfill({ json: notebook(kind) });
    if (path === `/api/experiment-reports/report-${kind}/prose`) return route.fulfill({ json: { state: 'ready', label: '보고서 문장이 준비됐어요.' } });
    if (path === `/api/experiment-reports/report-${kind}/sharing`) return route.fulfill({ json: { report_id: `report-${kind}`, shared: false, changed_at: null } });
    if (path === `/api/experiment-reports/report-${kind}/comparisons`) return route.fulfill({ json: { records: [{ record_scope: 'mine', label: '내 기록 · 2026-10-28 10:00', value_rows: [], method_rows: [], timeline: [], notes: [] }], permission: { judgment: 'none' } } });
    return route.fulfill({ status: 404, json: { detail: 'not_found' } });
  });
}

async function screenshot(page: Page, info: TestInfo, kind: string) {
  const directory = process.env.NOTE1_SHOTS;
  if (!directory || info.project.name !== 'Desktop Chrome') return;
  await mkdir(directory, { recursive: true });
  await page.screenshot({ path: join(directory, `note1-${kind}.png`), fullPage: true, animations: 'disabled', caret: 'hide' });
}

for (const kind of Object.keys(profiles) as Array<keyof typeof profiles>) {
  test(`grounded ${kind} notebook keeps only source and record facts`, async ({ page }, info) => {
    await mock(page, kind);
    await page.goto(`/app/#/records/run-${kind}/notebook/report-${kind}`);
    await expect(page.getByTestId('notebook-screen')).toContainText(profiles[kind].title);
    await expect(page.getByTestId('notebook-screen')).toContainText(profiles[kind].observation);
    await expect(page.getByRole('heading', { name: profiles[kind].domain.label })).toBeVisible();
    await expect(page.getByRole('heading', { name: '해석·다음 할 일' })).toBeVisible();
    await expect(page.getByTestId('notebook-value-bar')).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
    await screenshot(page, info, kind);
  });
}
