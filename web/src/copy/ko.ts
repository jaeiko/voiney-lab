// Every user-facing sentence of the new screen, in one place (lane RX0,
// 2026-10-24). The table that decided each line is docs/COPY_DECK.md; the
// rules are docs/VOICE_AND_TONE.md: 해요체, one meaning per sentence, no
// internal words, buttons are verbs ending in ~기, errors say what happened
// and what to do. Server sentences (error codes' meanings, card questions)
// are shown as the server sends them and are not rewritten here.
// "이 프로토콜로 시작" is the one button name AGENTS.md (rule 5) fixes.

export const product = {
  name: "Voiney Lab",
  tagline: "장갑을 낀 채 말로 진행하는 실험 안내",
  principle: "값과 안전 주의는 올린 원문에서만 와요.",
  logoNote: "임시 로고 — 디자이너 확정 필요",
} as const;

export const nav = {
  home: "홈",
  protocols: "프로토콜",
  experiment: "실험",
  records: "기록",
  settings: "설정",
  menuLabel: "화면 고르기",
} as const;

export const frame = {
  labPrefix: "연구실",
  adminChip: "관리자",
  devChip: "개발 모드",
  checking: "확인하는 중…",
  logout: "로그아웃하기",
  logoutAsk: "로그아웃할까요?",
  logoutConfirm: "로그아웃하기",
  cancel: "그만두기",
  logoutFailed: "로그아웃하지 못했어요. 다시 해 주세요.",
} as const;

export const auth = {
  loginTitle: "로그인",
  loginLead: "연구실 담당에게 받은 이름과 비밀번호를 적어 주세요.",
  loginHint: "처음이면 받은 초대 링크를 열어 주세요.",
  name: "이름",
  password: "비밀번호",
  loginButton: "로그인하기",
  loggingIn: "로그인하는 중…",
  signupTitle: "가입하기",
  signupInvite: (lab: string) => `연구실 ${lab}의 초대예요.`,
  signupAdmin: "관리자로 가입해요.",
  signupLead: "이름과 비밀번호를 정하면 바로 시작할 수 있어요.",
  signupButton: "가입하고 시작하기",
  signingUp: "가입하는 중…",
  inviteChecking: "초대 링크를 확인하는 중…",
  empty: "이름과 비밀번호를 적어 주세요.",
  retry: "다시 시도하기",
  errors: {
    login_refused: "이름이나 비밀번호가 맞지 않아요.",
    login_locked: (minutes: number) => `여러 번 틀려 잠시 막혔어요. ${minutes}분 뒤에 다시 해 주세요.`,
    account_disabled: "꺼진 계정이에요. 연구실 담당에게 물어보세요.",
    invite_invalid: "초대 링크가 만료되었거나 이미 쓰였어요. 연구실 담당에게 새 링크를 받아 주세요.",
    account_name_taken: "이미 있는 이름이에요. 다른 이름을 적어 주세요.",
    account_name_invalid: "이름은 2–40자로 적어 주세요.",
    password_invalid: "비밀번호는 8자 이상으로 적어 주세요.",
    accounts_disabled: "이 앱은 로그인 없이 써요.",
    authentication_required: "로그인이 풀렸어요. 다시 로그인해 주세요.",
    network: "연결이 안 돼요. 인터넷을 확인한 뒤 다시 해 주세요.",
    unknown: "로그인하지 못했어요. 다시 해 주세요.",
  },
} as const;

export const states = {
  loading: "불러오는 중…",
  reload: "다시 불러오기",
  protocolsFailed: "프로토콜 목록을 불러오지 못했어요.",
  appFailed: "화면을 불러오지 못했어요. 잠시 뒤 다시 해 주세요.",
  handoff: "지금 화면으로 넘어가요…",
} as const;

export const home = {
  greeting: (name: string) => `안녕하세요, ${name} 님.`,
  greetingAnonymous: "안녕하세요.",
  resume: "이어서 하기",
  resumeSub: (title: string, step: string | null) =>
    step ? `${title} · ${step}단계부터` : `${title} · 저장한 지점부터`,
  review: "확인하기",
  reviewOneSub: (title: string, count: number) => `${title} · 확인할 것 ${count}개`,
  reviewManySub: (protocols: number) => `확인을 기다리는 프로토콜 ${protocols}개`,
  reviewWhy: (count: number) => `${count}가지만 확인하면 실험에 쓸 수 있어요.`,
  first: "첫 프로토콜 올리기",
  firstSub: "PDF · Word · 사진을 올리면 분석이 시작돼요.",
  analysing: "분석 중",
  analysingSub: (title: string, elapsed: string | null) => (elapsed ? `${title} · ${elapsed}` : title),
  analysingWhy: "끝나면 여기에서 바로 시작할 수 있어요.",
  start: "실험 시작",
  startSub: (ready: number) => `바로 쓸 수 있는 프로토콜 ${ready}개`,
  secondary: {
    protocols: "프로토콜 보기",
    upload: "올리기",
    records: "지난 기록",
  },
} as const;

export const protocols = {
  title: "프로토콜",
  count: (total: number, ready: number) => `${total}개 · 바로 쓸 수 있는 것 ${ready}개`,
  upload: "올리기",
  empty: "아직 올린 프로토콜이 없어요.",
  emptyAction: "첫 프로토콜 올리기",
  steps: (count: number) => `${count}단계`,
  uploaded: (day: string) => `${day} 올림`,
  actions: {
    start: "시작하기",
    review: "확인하기",
    retry: "다시 시도하기",
  },
  chips: {
    ready: "바로 쓸 수 있어요",
    reviewLeft: (count: number) => `확인 ${count}개 남음`,
    analysing: "분석 중",
    reanalysing: "다시 분석 중",
    chunks: (done: number, total: number) => `나눠 읽는 중 ${done}/${total}`,
    failed: "분석 실패 — 다시 시도",
    ocrRunning: "사진 글자 읽는 중",
    ocrCheck: "사진 글자 확인 필요",
    ocrFailed: "사진 글자를 못 읽었어요 — 다시 시도",
    blocked: "아직 시작할 수 없어요",
    exampleOnly: "이 앱에서는 쓸 수 없는 예시예요",
    development: "개발용 예시",
  },
} as const;

export const time = {
  justNow: "방금 시작했어요",
  minutesAgo: (minutes: number) => `${minutes}분 지남`,
  hoursAgo: (hours: number) => `${hours}시간 지남`,
} as const;
