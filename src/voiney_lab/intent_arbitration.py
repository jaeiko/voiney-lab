"""Authoritative, read-only request arbitration for every conversation path.

The arbiter classifies the user's latest normalized request.  It never performs
workflow mutations and it never treats a classification as authorization.  The
curated protocol runtime and the generic tool runtime consume the same typed
decision so specialized informational requests cannot be shadowed by a generic
current-step answer.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum


class RequestIntent(str, Enum):
    """Stable top-level intents used by production routing and replay tests."""

    WORKFLOW_CONTROL = "workflow_control"
    LEARNING = "learning"
    PROTOCOL_AUDIT = "protocol_audit"
    HISTORY_RESUME = "history_resume"
    UNCERTAINTY = "uncertainty"
    COMBINED_LEARNING_NEXT = "combined_learning_next"
    VISUAL = "visual"
    TROUBLESHOOTING = "troubleshooting"
    CURRENT_STEP = "current_step"
    GENERAL_QA = "general_qa"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class RequestArbitration:
    """One sanitized interpretation contract with no mutation authority."""

    normalized_text: str
    intent: RequestIntent
    confidence: float | None
    reason_code: str
    dimensions: tuple[str, ...] = ()
    history_action: str | None = None
    mutation_candidate: bool = False

    @property
    def state_mutation(self) -> bool:
        """Classifications are evidence only; this is always false by design."""

        return False


def normalize_request_text(value: str) -> str:
    """Normalize harmless Unicode and spacing variation without changing meaning."""

    normalized = unicodedata.normalize("NFKC", value)
    normalized = normalized.replace("’", "'").replace("`", "'")
    normalized = re.sub(r"\s+", " ", normalized).strip().casefold()
    return normalized.rstrip(".!?。？！ ")


def _matches(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE) is not None for pattern in patterns)


_WHY_PATTERNS = (
    r"(?:이|지금|현재)\s*(?:단계|과정|작업)(?:의)?\s*(?:이유|목적)(?:을|를)?\s*(?:알려|설명)",
    r"(?:이\s*(?:단계|작업)|여기서|이걸|이거|이\s*과정)?\s*왜\s*(?:꼭\s*)?(?:해야|하는|필요|먼저)",
    r"왜\s*(?:이|현재|이번)\s*(?:단계|작업)(?:를|은|는)?\s*(?:해야|하는|필요)",
    r"(?:왜\s*(?:해야\s*(?:돼|해|하는지)|하는지|필요한지)|이\s*단계의\s*(?:이유|목적)|(?:이유|목적)(?:가|은|이)?\s*(?:뭐|무엇))",
    r"\b(?:why\s+(?:do\s+we\s+(?:do|need)\s+this(?:\s+step)?|should\s+i\s+do\s+this(?:\s+step)?|is\s+this(?:\s+step)?\s+(?:needed|necessary|important)|this\s+step\s+(?:matters|is\s+needed))|purpose\s+of\s+(?:this|the\s+current)\s+step)\b",
)
_WARNING_PATTERNS = (
    r"(?:(?:여기서|이\s*(?:단계|작업)에서)\s*(?:조심|주의)(?:할|해야|사항|점|할\s*건|할\s*것)|조심(?:할|해야)\s*(?:점|건|것))",
    r"(?:주의\s*사항|주의점|주의(?:해야\s*할|할)\s*(?:점|사항)|흔한\s*실수|실수하기\s*쉬운|피해야\s*할)(?:을|를|은|는)?\s*(?:알려|뭐|무엇|설명)",
    r"주의(?:해야\s*할|할)\s*(?:흔한\s*)?실수(?:가|는|를)?\s*(?:뭐|무엇|알려|설명)",
    r"\b(?:warning|caution|precaution|common\s+mistake|what\s+should\s+i\s+(?:avoid|watch))s?\b",
)
_NEXT_PATTERNS = (
    r"(?:다음\s*(?:단계|작업)|그\s*다음)(?:도|은|에는|으로)?\s*(?:알려|설명|뭐|무엇|보여|하는)",
    r"(?:다음에는|다음엔)\s*(?:뭐|무엇|어떤\s*작업)",
    r"\b(?:next\s+step|what(?:'s|\s+is)\s+next|what\s+do\s+we\s+do\s+next)\b",
)
_EXPLANATION_REQUEST = re.compile(r"설명|알려|이유|목적|왜|주의|\b(?:explain|why)\b", re.I)
_NEXT_MOVE_REQUEST = re.compile(
    r"(?:다음\s*(?:단계|작업)?(?:으?로)?\s*(?:넘어가|가|진행해|이동해)|"
    r"(?:설명하고|알려주고|해\s*주고)\s*넘어가)"
    r"(?:\s*(?:줘요?|주세요|주라|줄래요?|주실래요?|자|요))?$|"
    r"\b(?:then\s+)?(?:move\s+on|go\s+to\s+(?:the\s+)?next\s+step)\s*$", re.I,
)
_AUDIT_PATTERNS = (
    r"(?:현재\s*)?(?:프로토콜|sop|절차|문서)(?:의|가|는|을|를)?\s*(?:버전|판|개정|리비전|해시|체크섬)",
    r"(?:지금\s*쓰는\s*)?(?:sop|프로토콜)(?:가|는)?\s*몇\s*(?:판|버전)",
    r"(?:문서\s*)?(?:버전|해시|sha\s*-?\s*256|체크섬)(?:값|정보)?\s*(?:알려|확인|보여|뭐|무엇|몇)",
    r"\b(?:protocol|sop|document)\s+(?:version|revision|hash|checksum)\b",
    r"\bsha\s*-?\s*256\b",
)
_RESUME_HISTORY_PATTERNS = (
    r"(?:어제|이전|지난|지난번|지난\s*번|전에|기존)\s*(?:하던|진행하던|멈춘)?\s*(?:것|거|실험|세션|워크플로)?\s*(?:을|를)?\s*(?:이어|이어줘|이어서|계속|불러|재개)",
    r"(?:전에|이전에|지난번에)?\s*멈춘\s*(?:실험|세션|워크플로)(?:이|가)?\s*(?:있어|있나|있나요)",
    r"\b(?:continue|resume)\s+(?:yesterday(?:'s)?|(?:the\s+|a\s+)?previous|last)\s+(?:experiment|session|workflow)\b",
)
_HISTORY_LIST_PATTERNS = (
    r"(?:이전|최근|과거|지난)?\s*(?:실험|세션|워크플로)\s*(?:기록|이력|내역|목록)(?:을|를)?\s*(?:보여|조회|알려|확인|불러)",
    r"\b(?:experiment|session|workflow)\s+history\b",
    r"\b(?:list|show)\s+(?:recent|previous|past)\s+(?:experiments|sessions|workflows)\b",
)
_UNCERTAINTY_PATTERNS = (
    r"(?:이\s*)?(?:실험(?:\s*결과)?|결과)(?:이|가|은|는)?\s*(?:성공|실패|잘\s*(?:될|나올)|좋을|괜찮을)(?:할|될|나올)?(?:까|지|까요|건가)",
    r"(?:이\s*정도면|지금\s*상태면)\s*(?:성공|잘\s*된|괜찮은)(?:\s*거야|건가|걸까|거냐)",
    r"(?:성공\s*여부|결과\s*예측|성공할\s*수\s*있을까|망할까)",
    r"\b(?:will\s+(?:this|the)\s+experiment\s+succeed|will\s+it\s+work|is\s+(?:this|it)\s+successful)\b",
)
_VISUAL_PATTERNS = (
    r"(?:사진|이미지|그림|구조식|시각\s*자료)(?:을|를|이|가)?\s*(?:찾아|검색|보여|열어|그려|만들)",
    r"(?:어떻게\s*생겼|생김새).*(?:찾아|보여|알려)",
    r"\b(?:find|show|search\s+for|display|generate)\b.*\b(?:photo|image|picture|diagram|structure)\b",
)
_CURRENT_STEP_PATTERNS = (
    r"(?:현재|지금|이번)\s*(?:몇\s*)?단계(?:가|는|를|의)?\s*(?:뭐|무엇|알려|설명|확인)",
    r"지금\s*(?:뭐|무엇)을?\s*해야",
    r"\b(?:current\s+step|what\s+should\s+i\s+do\s+now)\b",
)
_PROTOCOL_INFORMATION_PATTERNS = (
    r"(?:이\s*)?(?:실험|프로토콜|sop|절차)(?:의|가|는|을|를)?\s*(?:목적|목표|개요|전체\s*흐름)",
    r"\b(?:purpose|goal|objective|overview)\s+of\s+(?:this|the)\s+(?:experiment|protocol|sop|procedure)\b",
)
_AGENT_INFORMATION_PATTERNS = (
    r"(?:이\s*)?(?:에이전트|보이스\s*에이전트|ai|시스템|너|당신)(?:의|가|는)?\s*(?:목적|역할|기능|능력)",
    r"\b(?:purpose|role|function|capabilit(?:y|ies))\s+of\s+(?:this|the)\s+(?:agent|assistant|system)\b",
)
_WORKFLOW_CONTROL_PATTERNS = (
    r"^(?:프로토콜|실험|워크플로)?\s*(?:을|를)?\s*(?:시작|중지|중단|종료|일시\s*정지|재개)(?:해\s*줘|하자|할게|합니다|해)?$",
    r"(?:현재\s*|이번\s*|\d+\s*)단계(?:를|는)?\s*(?:완료|끝|마쳤|마침|다\s*했)",
    r"^(?:완료|끝났어|다\s*했어|다음\s*단계로\s*(?:가|넘어가|진행))",
    r"\b(?:start|stop|pause|resume|complete)\s+(?:the\s+)?(?:protocol|workflow|current\s+step)\b",
)


# --- A problem at the bench (lane HELP, decision 1, 2026-10-24) ------------------
#
# "젤이 아직 파래", "침전이 생겼어", "왜 안 되지?": something did not go as it
# should. A read-only request kind: the state machine records a problem said
# as having happened (as the anomaly readers do) and answers both with help
# from the sources; nothing here decides anything.

#: Something does not do what it should: "안 빠져", "안 녹아", "안 떨어져".
_PROBLEM_NOT_DOING = (
    r"(?:안|못)\s*(?:빠져|빠지|빠졌|빠진|녹아|녹지|녹았|녹|섞여|섞이|풀려|풀리|줄어|떨어져|떨어지|떨어졌"
    r"|붙어|붙|나와|나오|나왔|보여|보이|올라가|올라|내려가|내려|굳어|굳|말라|마르|말랐|부풀|가라앉"
    r"|생겨|생기|생겼|퍼져|퍼지|들어가|들어갔|돌아가|돌아|맞아|맞|덮여|덮어|덮이|덮|변해|변하|변했|바뀌"
    r"|움직|돼|되|됐|된)"
)
#: The same without the plain "안 돼/됐어", for a thing the list below does not
#: name ("시간이 안 됐어" is the time not yet, not a problem).
_PROBLEM_NOT_DOING_ACTION = _PROBLEM_NOT_DOING.replace("|돼|되|됐|된)", ")")
#: Words between a thing and its "안": "다 안", "잘 안", "전혀 안".
_PROBLEM_ADVERBS = r"(?:(?:다|잘|전혀|아예|계속|아직|거의|좀|조금|도무지|영)\s*)*"
#: The things of an experiment a problem is said about.
_PROBLEM_SUBJECTS = (
    r"(?:색|색깔|젤|겔|밴드|용액|시료|샘플|세포|펠릿|펠렛|상층액|단백질|결정|가루|분말|시약|막|멤브레인|배지|미디어"
    r"|신호|형광|피크|콜로니|조각|플러그|온도|장비|기계|기포|거품|증폭|커브|곡선|검량선|표준\s*곡선|흡광도|측정값|값|수치"
    r"|항체|버퍼|웰|용량|부피|양)"
)
_PROBLEM_SHAPES = (
    # Still in a state it should have left: "아직 파래", "여전히 투명해".
    ("still", r"(?:아직|아직도|여전히|계속)\s*(?:\S+\s*){0,2}?(?:파래|파랗|파란|푸르|투명|탁해|탁하|뿌옇|남아\s*있"
              r"|그대로|하얗지|희지|뜨거|따뜻|차가|미지근|안\s*\S+|덜\s*\S+)"),
    # Something formed: "침전이 생겼어", "거품이 많아", "덩어리가 보여".
    ("formed", r"(?:침전|침전물|거품|기포|덩어리|응집|결정|얼룩|이물질|부유물|알갱이|연기|곰팡이|균열)"
               r"(?:이|가|은|는|도|물이)?\s*(?:\S+\s*){0,2}?(?:생겼|생기|생긴|보여|보이|있어|있네|있는|나와|났|많아"
               r"|많이|떠\s*있|껴|올라와|맺혀)"),
    # It failed or came out wrong: "실패한 것 같아", "결과가 안 좋아", "제대로 안 돼".
    ("failed", r"실패했|실패한\s*것\s*같|실패인\s*것\s*같|망했|망한\s*것\s*같|잘못\s*(?:된|됐|되었)"
               rf"|{_PROBLEM_SUBJECTS}(?:이|가|은|는)?\s*이상(?:해졌|해져|해|하)"
               r"|이상하게\s*(?:나왔|됐|돼)|(?:결과|밴드|신호|색|형광|배경|백그라운드|흡광도|값|수치|피크)"
               r"(?:가|이|는|은)?\s*(?:안\s*좋|나쁘|약해|약하|너무\s*(?:약|진|연|흐|높|낮|세|강|희미))"
               r"|예상(?:과|이랑|하고)\s*(?:달라|다르|다른)|원하는\s*대로\s*안"
               r"|제대로\s*안|잘\s*안\s*(?:돼|되|나와|빠|녹|섞|떨어|덮|붙)"),
    # Not enough of something: "버퍼가 모자란 것 같아", "양이 부족해".
    ("short", r"모자라|모자란|모자랄|부족해|부족한|부족하"),
    # Something left that should be gone: "웰에 아크릴아마이드가 남아 있는 것 같아".
    ("left_over", r"\S+(?:이|가)\s*(?:아직\s*)?(?:조금\s*)?남아\s*있|찌꺼기|잔여물"),
    # Something it should be kept from: "빛에 노출됐어".
    ("exposed", r"(?:빛|햇빛|조명|공기)(?:에|이|가)?\s*(?:\S+\s*){0,1}?노출"),
    # A thing of the experiment does not do it: "색이 안 빠져", "세포가 안 떨어져".
    ("not_doing", _PROBLEM_SUBJECTS + r"(?:이|가|은|는|도|을|를)?\s*(?:\S+\s*){0,2}?" + _PROBLEM_NOT_DOING),
    # Any named thing that does not do it: "master mix가 안 녹아", "흙이 다 안 풀려".
    ("thing_not_doing", r"(?:[가-힣A-Za-z0-9]+)(?:이|가)\s+" + _PROBLEM_ADVERBS + _PROBLEM_NOT_DOING_ACTION),
    # Too much of a state: "너무 뜨거워", "여과가 너무 느려", "세포가 너무 빽빽해".
    ("too", r"너무\s*(?:많|적|진해|진하|연해|연하|뜨거|차가|느려|느리|빨라|빠르|오래|짧|길어|빽빽|묽|걸쭉|되직|약해|약하"
            r"|높|낮|커|작아|두꺼|얇|탁|흐려|흐릿|끈적|뻑뻑|세게|강해|빨리|늦|더디)"),
    # Not fully: "분해가 덜 된 것 같아", "덜 말랐어".
    ("not_fully", r"덜\s*(?:된|됐|돼|되|녹|말랐|마른|말라|섞|풀|빠|식|끓|굳|덮|퍼|빠졌)"),
    # Loose or leaking: "뚜껑이 헐거워", "새고 있어".
    ("loose", r"헐거|헐겁|느슨해|헐렁|새고\s*있|새는\s*(?:것|거)\s*같|샜"),
    # Uneven or stuck: "한쪽에 몰려 있어", "벽에 붙어 있어", "흐트러졌어".
    ("uneven", r"(?:몰려|쏠려|뭉쳐|흩어져)\s*있|흐트러|흩어졌"
               r"|(?:벽|바닥|뚜껑|입구|팁|관|튜브|윗부분)\S*\s*(?:\S+\s*){0,1}?(?:붙어|달라붙어|묻어)\s*(?:있|버렸|남)"),
    # Left too long or forgotten: "빛에 오래 뒀어", "깜빡했어".
    ("too_long", r"오래\s*(?:뒀|둬|놔뒀|놓|방치)|깜빡\s*(?:했|잊)|잊어\s*버렸|잊었"),
    # Something got in: "먼지가 들어갔어", "물이 섞였어".
    ("got_in", r"(?:먼지|이물질|머리카락|물방울|공기|기포|다른\s*시약)(?:이|가)?\s*(?:\S+\s*){0,2}?"
               r"(?:들어갔|들어간|섞였|섞인|묻었|묻은|떨어졌)"),
    # A thing turned wrong: "용액이 탁해졌어", "젤이 녹았어", "펠릿이 안 보여".
    ("turned", r"(?:젤|겔|밴드|용액|시료|샘플|세포|펠릿|펠렛|상층액|막|멤브레인|배지|미디어)(?:이|가|은|는)?\s*"
               r"(?:\S+\s*){0,2}?(?:탁해|뿌옇|변색|갈색으로|노랗게|노래졌|녹았|녹아\s*버|말라\s*버|굳었|퍼졌|번졌"
               r"|떨어져\s*(?:나갔|버렸))"),
    ("english", r"\b(?:still\s+(?:blue|stained|transparent|cloudy|there|attached|visible|not)"
                r"|(?:is|are|it'?s)\s+not\s+(?:working|dissolving|destaining|attaching|detaching|clearing|mixing)"
                r"|(?:isn'?t|aren'?t|doesn'?t|don'?t|didn'?t|won'?t)\s+(?:work|working|dissolve|dissolving|destain"
                r"|attach|detach|clear|mix|form)"
                r"|(?:a|some)\s+precipitate|precipitate\s+(?:formed|appeared)|turned\s+cloudy|(?:it|this)\s+failed"
                r"|has\s+failed|went\s+wrong|no\s+(?:band|bands|signal|pellet|colonies|peaks?)"
                r"|(?:got|is|it'?s)\s+(?:foamy|cloudy|bubbly)|(?:signal|bands?|background)\s+(?:is|are)\s+(?:too\s+)?"
                r"(?:weak|faint|high|strong)|(?:cells?|pellet)\s+(?:won'?t|didn'?t|don'?t|doesn'?t)\s+\w+)\b"),
)
#: Asking about a problem rather than only saying it: "왜", "어떡해", "뭐가 문제야".
_PROBLEM_QUESTION = (
    r"(?:^|\s)왜(?:\s|$)|^왜|어떡해|어떡하|어떻게\s*(?:해|하지|하면|하나|해야)|어쩌지|어쩌면|어떻게\s*된"
    r"|뭐가\s*(?:문제|잘못|이상)|무슨\s*문제|원인이\s*(?:뭐|뭘)|\bwhy\b|what\s+(?:went\s+wrong|should\s+i\s+do"
    r"|do\s+i\s+do)|how\s+(?:do|can)\s+i\s+fix"
)
#: A problem asked about with no thing named: "왜 안 되지?", "뭐가 문제야?".
_PROBLEM_GENERIC_QUESTION = (
    r"^(?:이거|이게|근데|아|어|음)?\s*왜\s*(?:이렇게\s*)?(?:안\s*(?:돼|되|됐|된|나와|나오)|이러|이래|이렇지|그러지|그래)"
    r"|^(?:이거|근데)?\s*뭐가\s*(?:문제|잘못|이상)|^what\s+went\s+wrong|^why\s+(?:is(?:n'?t)?|does(?:n'?t)?)\s+it"
    r"\s+(?:not\s+)?work"
)
#: Never a problem at the bench: a step done or not done yet, the sound or the
#: screen, a rule or a permission ("면 안 돼", "써도 돼?"), a time left, a
#: hypothetical ("생기면 어떡해?") or a request for something else.
_NOT_A_PROBLEM = (
    r"끝났|끝내|끝나|완료|마쳤|마치|다\s*했|\bdone\b|\bfinish"
    r"|들려|들리|소리|목소리|화면|그림|사진|이미지|영상|글자|타이머|앱|마이크|이어폰|연결"
    r"|(?:으|하)?면\s*안\s*(?:돼|되)|지\s*마|말아|안\s*해도|도\s*(?:돼|되|괜찮)"
    r"|(?:분|초|시간)\s*(?:이|가)?\s*남|(?:으|하)?면\s*(?:어떡|어떻게|어쩌|뭐|왜)|\bif\b|\bwould\b|\bcould\b"
    r"|(?:시간|차례|순서|때|점심|밥)\S*\s*(?:이|가)?\s*(?:아직\s*)?안\s*(?:돼|됐|되|맞)"
    r"|아니야|아니에요|아닌|않았|라고\s*(?:말|하면)"
    # A move or a step command with it ("덜 됐는데 그냥 다음 단계 가자") is
    # the command's: the completion gates rule on it (D7).
    r"|다음\s*(?:단계|으로)|넘어가|건너뛰|가자|진행하자|진행해|\bnext\s+step\b|\bmove\s+on\b|\bskip\b"
)
_PROBLEM_REQUEST_ENDING = r"(?:줘|주세요|줄래|주라|줄\s*수)$"


#: Lane SAY, decision 7 (2026-10-25; persona test F3): a doubt about the
#: bench with nothing named -- "망한 것 같아요", "이거 맞아요?", "잘 된 건가",
#: "제대로 한 건지 모르겠어". The whole words, so "젤이 망한 것 같아" (a thing
#: named) stays a problem for ``problem_reading``.
_VAGUE_LEAD = r"^(?:(?:아|어|음|근데|그런데|혹시|저기|아니|흠)\s*,?\s*)*"
_VAGUE_CHECK = (
    _VAGUE_LEAD + r"(?:이거|이게|이건|다|나|저|제가|내가|지금)?\s*(?:완전히\s*|다\s*)?"
    r"(?:망한|망친|망했|망쳤|망하|망치|잘못한|잘못된|잘못했|잘못\s*한|잘못\s*된|틀린|틀렸)"
    r"(?:\s*(?:것|거|듯|건가|건지|나|나요|어|어요|네|는데|다))?(?:\s*(?:같아|같아요|같은데|같네|봐|봐요|나\s*봐))?"
    r"(?:\s*(?:어떡해|어떡하지|어쩌지))?[.!?~]*$",
    # Something must stand before 맞아: a bare "맞아" is a yes.
    _VAGUE_LEAD + r"(?:(?:이거|이게|이건|이대로|이렇게|지금|제대로|맞게)\s*"
    r"(?:(?:하는|한|된|되는|하고\s*있는|해도\s*되는)\s*)?(?:거|게|건)?"
    r"|(?:하는|한|된|되는|하고\s*있는)\s*(?:거|게|건))\s*"
    r"맞(?:아|아요|나|나요|는\s*거야|는\s*거예요|는\s*건가|는\s*건지|지|죠|을까|을까요)[.!?~]*$",
    _VAGUE_LEAD + r"(?:이거|이게|지금|다)?\s*(?:잘|제대로)\s*(?:된|됐|되고\s*있는|한|했|하고\s*있는)\s*"
    r"(?:건가|건가요|거야|거예요|건지|거\s*맞아|거\s*맞아요|나|나요|어|어요|을까|을까요)?"
    r"(?:\s*(?:모르겠어|모르겠어요|모르겠네))?[.!?~]*$",
    _VAGUE_LEAD + r"(?:이\s*정도면|이러면|이렇게\s*하면)\s*(?:돼|되나|되나요|될까|될까요|된\s*거야|괜찮아)[.!?~]*$",
)


def vague_check_reading(text: str) -> bool:
    """Whether the words doubt the bench with nothing named (lane SAY, decision 7)."""

    normalized = normalize_request_text(text)
    return bool(normalized) and any(
        re.fullmatch(pattern, normalized) for pattern in _VAGUE_CHECK)


#: Lane GEN1 (2026-10-31): words that set a supposition up front ("만약 …",
#: "가정하면") and words a supposition ends in: a word ending in -면 / -다면
#: / -라면 ("생기면", "생긴다면", "흰색이면", "생기면요"), "… 경우", "… 수(도)
#: 있", and the English "if".
_SUPPOSITION_LEAD = re.compile(r"(?<![가-힣])(?:만약|만일|가령|가정)|\b(?:if|suppose|in\s+case)\b")
_SUPPOSITION_WORD = re.compile(r"[가-힣]+(?:다면|라면|면)(?:은|는|요)?$")
_SUPPOSITION_PHRASE = re.compile(
    r"(?:[가-힣](?:을|ㄹ)?\s*경우|[을를ㄹ길날될갈할질]\s*수(?:도)?\s*있)"
)
#: Words ending in 면 that are no supposition: nouns ("표면에" has its
#: particle, "표면" alone does not), "그러면" (then), "아니면" (or else) and
#: "-려면" (in order to).
_NOT_SUPPOSITION_WORDS = frozenset({
    "표면", "단면", "측면", "화면", "전면", "후면", "평면", "곡면", "이면", "내면", "외면",
    "수면", "지면", "양면", "정면", "반면", "방면", "국면", "장면", "라면", "면",
    "그러면", "그럼", "그렇다면", "아니면", "그러면은", "안그러면", "그렇지않으면",
})
#: Said as having happened, after the supposition: a past or ongoing
#: predicate ("봤는데 침전이 생겼어", "섞었더니 거품이 나고 있어").
_HAPPENED = re.compile(
    r"(?:[았었였했됐겼났졌왔갔봤샜쳤렸켰웠렀뤘]|던)(?:어요|어|네요|네|는데|다|습니다|음|고|지|더니|으니까|으니)?(?=$|[\s,.!?~])"
    r"|고\s*있(?:어|어요|네|는데|다|습니다)|\b(?:formed|appeared|happened|turned|became|is\s+now)\b"
)
#: The rest of a supposition asks or wishes, it does not report: "… 어떡해?",
#: "… 어떻게 돼?", "… 괜찮아?", "… 좋겠어".
_SUPPOSITION_ASKS = re.compile(
    r"[?？]|어떡|어떻게|어쩌|뭐|무엇|무슨|왜|이유|원인|괜찮|문제\s*(?:없|있|돼|되)|돼$|되나|될까|되는|"
    r"하나요|할까|하지|해야|나요$|까요?$|지요?$|는지|은지|가요?$|맞아|좋겠|바라|싶|"
    r"\b(?:what|how|why|should|would|could|can|do\s+i)\b"
)
#: A command or a request after the supposition: it is not a problem.
_SUPPOSITION_REQUESTS = re.compile(r"(?:줘|주세요|줄래|주실래|가자|하자|해라|넘어가|알려)")
#: What goes wrong at a bench, named in any protocol: an unwanted thing seen
#: (a precipitate, foam, a colour), a thing that breaks, spills or will not
#: do what it should, or a failure. Never a reagent's or a document's own word.
_TROUBLE = re.compile(
    r"문제|이상|실패|오류|에러|잘못|틀리|틀려|침전|거품|기포|덩어리|응집|결정이|얼룩|이물질|부유물|알갱이|"
    r"곰팡이|균열|금이|오염|연기|냄새|(?<![검탐수])색|탁해|탁하|뿌옇|뿌얘|흐려지|흐려져|변색|변하|변해|바뀌|"
    r"굳어|굳으|깨지|깨져|터지|터져|부서|막히|막혀|넘치|넘쳐|흘리|흘려|쏟|엎지|엎으|새면|새어|새는|누출|"
    r"타\s*버리|탔|그을|과열|끓어\s*넘|녹아\s*버리|말라\s*버리|떨어져\s*나|덜\s*[가-힣]|"
    r"안\s*(?:돼|되|나오|보이|녹|섞|빠|붙|떨어|생기|맞|풀|굳|마르|말라)|"
    r"\b(?:precipitat\w*|foam\w*|bubbl\w*|cloudy|turbid|fail\w*|contaminat\w*|leak\w*|spill\w*|crack\w*"
    r"|clog\w*|burn\w*|smok\w*|colou?r)\b",
    re.IGNORECASE,
)


def _supposition_split(normalized: str) -> tuple[str, str] | None:
    """(the supposed part, the rest) when the words suppose something, else None.

    The supposed part runs to the last word that ends a supposition; the rest
    is what is said of it. With only a lead ("만약 …") the whole is supposed.
    """

    words = normalized.split()
    last = None
    for index, word in enumerate(words):
        bare = word.strip(",.!?~")
        if not bare or bare in _NOT_SUPPOSITION_WORDS or bare.endswith(("려면", "으려면", "면서")):
            continue
        if _SUPPOSITION_WORD.fullmatch(bare):
            last = index
    if last is not None:
        return " ".join(words[: last + 1]), " ".join(words[last + 1:])
    phrase = None
    for match in _SUPPOSITION_PHRASE.finditer(normalized):
        phrase = match
    if phrase is not None:
        return normalized[: phrase.end()], normalized[phrase.end():]
    lead = _SUPPOSITION_LEAD.search(normalized)
    if lead is not None:
        # "만약 …" with no word ending it: what follows is both the
        # supposition and what is said of it ("만약을 위해 봤는데 침전이 생겼어").
        return normalized[lead.start():lead.end()], normalized[lead.end():]
    return None


def supposes(text: str) -> bool:
    """Whether the words suppose something and say nothing has happened (lane GEN1).

    "여기서 침전이 생기면", "타이머 끝나면 다음으로 가자", "침전이 안 생기면
    좋겠어": nothing is reported, so nothing may be recorded as having
    happened. "만약을 위해 봤는데 침전이 생겼어" reports it (said after the
    supposition, in the past); so do "표면에 거품이 생겼어" (a noun) and
    "저으면서 보니까 침전이 생겼어" (-면서 is "while").
    """

    normalized = normalize_request_text(text)
    split = _supposition_split(normalized) if normalized else None
    return split is not None and not _HAPPENED.search(split[1])


def supposed_problem_reading(text: str) -> str | None:
    """A problem supposed rather than reported (lane GEN1, 2026-10-31).

    "supposed": a trouble at the bench put as a supposition and asked, wished
    or left there ("여기서 침전이 생기면" -- a transcript cut after -면 too --,
    "혹시 거품이 나면 어떡해?", "만약 색이 변하면 어떻게 돼?", "침전이 생길
    수도 있어?"). It is answered from the protocol and never recorded.
    "unclear": a supposition said as a plain statement of what then happens
    ("섞으면 침전이 생겨"): it may be what happened just now, so it is asked
    once. None: nothing supposed, nothing troubled, a past report after it,
    or a request ("다 녹으면 알려줘").
    """

    normalized = normalize_request_text(text)
    if not normalized:
        return None
    split = _supposition_split(normalized)
    if split is None:
        return None
    supposed, rest = split
    if _HAPPENED.search(rest):
        return None
    asks = bool(_SUPPOSITION_ASKS.search(rest) or re.search(r"[?？]\s*$", text))
    if not asks and rest and _SUPPOSITION_REQUESTS.search(rest):
        return None
    if not (_troubled(supposed) or _troubled(rest)):
        return None
    if rest.strip() and not asks and _troubled(rest):
        return "unclear"
    return "supposed"


def _troubled(words: str) -> bool:
    """Whether the words name a trouble: the general list, or a problem shape."""

    return bool(words.strip()) and bool(
        _TROUBLE.search(words) or any(_matches(words, (pattern,)) for _, pattern in _PROBLEM_SHAPES))


@dataclass(frozen=True)
class ProblemReading:
    """A problem said or asked about, and which shape read it."""

    kind: str  # "statement" (said as having happened) | "question" (asked about)
    shape: str


def problem_reading(text: str) -> ProblemReading | None:
    """Whether the words tell or ask about a problem at the bench (lane HELP).

    "statement": "젤이 아직 파래", "침전이 생겼어", "the gel is still blue".
    "question": "왜 아직 파래?", "침전이 생겼는데 어떡해", "왜 안 되지?". None
    for anything else, including a step not done yet, the sound or screen, a
    rule, a permission asked, a time left and a hypothetical.
    """

    normalized = normalize_request_text(text)
    if not normalized or _matches(normalized, (_NOT_A_PROBLEM,)) or supposes(text):
        # Lane GEN1: a problem supposed ("여기서 침전이 생기면") has not
        # happened; ``supposed_problem_reading`` reads it.
        return None
    asked = _matches(normalized, (_PROBLEM_QUESTION,))
    shape = next((name for name, pattern in _PROBLEM_SHAPES if _matches(normalized, (pattern,))), None)
    if shape is None and asked and _matches(normalized, (_PROBLEM_GENERIC_QUESTION,)):
        shape = "generic"
    if shape is None:
        return None
    if not asked and re.search(_PROBLEM_REQUEST_ENDING, normalized):
        return None
    return ProblemReading("question" if asked else "statement", shape)


def arbitrate_request(text: str) -> RequestArbitration:
    """Classify one latest user request with deterministic, fail-closed priority."""

    normalized = normalize_request_text(text)
    if not normalized:
        return RequestArbitration(normalized, RequestIntent.UNKNOWN, None, "empty")

    history_action: str | None = None
    if _matches(normalized, _RESUME_HISTORY_PATTERNS):
        history_action = "resume"
    elif _matches(normalized, _HISTORY_LIST_PATTERNS):
        history_action = "list"
    if history_action is not None:
        return RequestArbitration(
            normalized,
            RequestIntent.HISTORY_RESUME,
            1.0,
            f"history_{history_action}",
            (history_action,),
            history_action=history_action,
        )

    if _matches(normalized, _AUDIT_PATTERNS):
        return RequestArbitration(
            normalized,
            RequestIntent.PROTOCOL_AUDIT,
            1.0,
            "protocol_version_or_hash",
            ("title", "protocol_version", "document_version", "checksum"),
        )

    if vague_check_reading(text):
        # Lane SAY, decision 7: a doubt with nothing named is helped as a
        # problem is (the step's expected result first).
        return RequestArbitration(
            normalized,
            RequestIntent.TROUBLESHOOTING,
            1.0,
            "problem_vague_check",
            ("troubleshooting",),
        )

    supposed = supposed_problem_reading(text)
    if supposed is not None:
        # Lane GEN1 (2026-10-31): a problem supposed is answered from the
        # source and never recorded; one that may be what just happened
        # ("섞으면 침전이 생겨") is asked about once.
        return RequestArbitration(
            normalized, RequestIntent.TROUBLESHOOTING, 1.0,
            "problem_contingency_question" if supposed == "supposed" else "problem_supposed_or_happened",
            ("troubleshooting",),
        )

    # Prospective bench failures are questions about source-backed handling,
    # not predictions and not permission to move workflow state. Keep this
    # ahead of the uncertainty reader so "만약 ... 실패하면" reaches the
    # same grounded help path as "문제가 생기면".
    problem_signal = re.search(
        r"문제|이상|실패|오류|잘못|예상\S*\s*(?:다르|다를|달라)|"
        r"침전|거품|기포|덩어리|응집|결정|얼룩|이물질|부유물|알갱이|곰팡이|균열|"
        r"안\s*(?:돼|되|됐|나오|보이|녹|섞|빠|붙|떨어)",
        normalized,
    )
    hypothetical = re.search(
        r"만약|가정(?:하|해|이)?|경우|"
        r"(?:발생|생기|실패|이상|다르|나|되|하)(?:면|할|ㄹ)|수\s*있",
        normalized,
    )
    asks_handling_or_reason = re.search(
        r"어떻게|어떡|어쩌|뭐|무엇|왜|이유|원인|먼저|확인|대처|"
        r"(?:가|넘어가|진행해)도\s*돼|가정하면",
        normalized,
    )
    prospective_problem = bool(
        problem_signal and asks_handling_or_reason and hypothetical
    )
    if prospective_problem:
        return RequestArbitration(
            normalized, RequestIntent.TROUBLESHOOTING, 1.0,
            "problem_contingency_question", ("troubleshooting",),
        )

    if _matches(normalized, _UNCERTAINTY_PATTERNS):
        return RequestArbitration(
            normalized,
            RequestIntent.UNCERTAINTY,
            1.0,
            "outcome_prediction_unsupported",
            ("bounded_uncertainty",),
        )

    if re.search(rf"(?:문제(?:가)?\s*생기면|{_PROBLEM_SUBJECTS}(?:이|가)?\s*이상하면)", normalized) and re.search(
        r"어떻게|어떡|어쩌|(?:가|넘어가|진행해)도\s*돼", normalized
    ):
        return RequestArbitration(normalized, RequestIntent.TROUBLESHOOTING, 1.0,
                                  'problem_contingency_question', ('troubleshooting',))

    problem = problem_reading(text)
    if problem is not None:
        return RequestArbitration(
            normalized,
            RequestIntent.TROUBLESHOOTING,
            1.0,
            f"problem_{problem.kind}",
            ("troubleshooting",),
        )

    protocol_information = _matches(normalized, _PROTOCOL_INFORMATION_PATTERNS)
    agent_information = _matches(normalized, _AGENT_INFORMATION_PATTERNS)
    move = _NEXT_MOVE_REQUEST.search(normalized)
    if (_EXPLANATION_REQUEST.search(normalized) and move
            and not re.search(r"만약|가정|다면|경우", text)
            and (not re.search(r"[?？]", text) or re.search(r"줘|주세요|줄래|주실래", move.group()))):
        return RequestArbitration(
            normalized, RequestIntent.COMBINED_LEARNING_NEXT, 1.0,
            'learning_and_next_command', ('rationale', 'completion_confirmation'),
        )
    why = _matches(normalized, _WHY_PATTERNS) and not (
        protocol_information or agent_information
    )
    warning = _matches(normalized, _WARNING_PATTERNS)
    next_step = _matches(normalized, _NEXT_PATTERNS)
    if why and next_step:
        return RequestArbitration(
            normalized,
            RequestIntent.COMBINED_LEARNING_NEXT,
            1.0,
            "learning_and_next_preview",
            ("rationale", "next_step_preview", "completion_confirmation"),
        )
    if why or warning:
        return RequestArbitration(
            normalized,
            RequestIntent.LEARNING,
            1.0,
            "current_step_warning" if warning and not why else "current_step_rationale",
            (("warning",) if warning and not why else ("rationale", "warning")),
        )

    if _matches(normalized, _VISUAL_PATTERNS):
        return RequestArbitration(
            normalized,
            RequestIntent.VISUAL,
            1.0,
            "explicit_visual_request",
            ("image",),
        )

    if _matches(normalized, _CURRENT_STEP_PATTERNS) or next_step:
        return RequestArbitration(
            normalized,
            RequestIntent.CURRENT_STEP,
            1.0,
            "next_step_preview" if next_step else "current_step_information",
            (("next_step_preview",) if next_step else ("current_step",)),
        )

    if _matches(normalized, _WORKFLOW_CONTROL_PATTERNS):
        return RequestArbitration(
            normalized,
            RequestIntent.WORKFLOW_CONTROL,
            1.0,
            "workflow_control_candidate",
            mutation_candidate=True,
        )

    question_like = protocol_information or agent_information or bool(
        re.search(r"(?:\?|뭐|무엇|어떻게|알려|설명|why|what|how|when|where|who)", normalized)
    )
    if question_like:
        return RequestArbitration(
            normalized,
            RequestIntent.GENERAL_QA,
            0.75,
            "general_question",
        )
    return RequestArbitration(normalized, RequestIntent.UNKNOWN, None, "no_high_confidence_match")
