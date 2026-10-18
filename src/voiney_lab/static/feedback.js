// Lane LOG, decision 3 (2026-10-18): one question when an experiment ends.
// "이번 실험에 도움이 됐나요?" -- 1 to 5, an optional one-line memo, or a
// skip -- kept on the server with the experiment record it is about
// (POST /api/pilot/feedback). index.html holds the box (#pilot-feedback) and
// three calls: ended() when the server ends the experiment, report(id) when
// its record is shown, reset() for a new session. An experiment answered (or
// skipped) is not asked about again on this device. Text goes in with
// textContent only.
(() => {
  "use strict";
  const QUESTION = "이번 실험에 도움이 됐나요?";
  const ANSWERED_KEY = "voineyFeedbackAnswered";
  const state = { ended: false, reportId: null, rating: null, shownFor: null };

  function answered() {
    try {
      const ids = JSON.parse(globalThis.localStorage?.getItem(ANSWERED_KEY) || "[]");
      return Array.isArray(ids) ? ids : [];
    } catch {
      return [];
    }
  }

  function remember(id) {
    try {
      const ids = answered().filter((item) => item !== id);
      ids.push(id);
      globalThis.localStorage?.setItem(ANSWERED_KEY, JSON.stringify(ids.slice(-50)));
    } catch {
      // A browser that keeps nothing may ask once more; the server keeps the last answer.
    }
  }

  function element(tag, text, className) {
    const item = document.createElement(tag);
    if (text !== undefined) item.textContent = text;
    if (className) item.className = className;
    return item;
  }

  function box() {
    return document.getElementById("pilot-feedback");
  }

  function hide() {
    const host = box();
    if (host) {
      host.hidden = true;
      host.replaceChildren();
    }
    state.shownFor = null;
  }

  async function submit(id, answer, send, skip, status) {
    send.disabled = true;
    skip.disabled = true;
    status.textContent = "저장하는 중…";
    let saved = false;
    try {
      const note = typeof answer.note === "string" && answer.note.trim() ? answer.note.trim() : null;
      const response = await fetch("/api/pilot/feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ report_id: id, rating: answer.rating, skipped: answer.skipped, note }),
      });
      saved = Boolean(response && response.ok);
    } catch {
      saved = false;
    }
    if (!saved) {
      status.textContent = "저장하지 못했습니다. 다시 눌러 주세요.";
      send.disabled = state.rating === null;
      skip.disabled = false;
      return;
    }
    remember(id);
    if (answer.skipped) {
      hide();
      return;
    }
    box()?.replaceChildren(element("p", "고맙습니다. 의견을 저장했습니다.", "pilot-feedback-thanks"));
  }

  function render() {
    const host = box();
    if (!host) return;
    const id = state.reportId;
    if (!state.ended || !id || answered().includes(id)) {
      hide();
      return;
    }
    if (state.shownFor === id) return;
    state.shownFor = id;
    state.rating = null;
    const question = element("p", QUESTION, "pilot-feedback-question");
    const scale = element("div", undefined, "pilot-feedback-scale");
    scale.setAttribute("role", "group");
    scale.setAttribute("aria-label", QUESTION);
    const send = element("button", "보내기", "pilot-feedback-send");
    send.type = "button";
    send.disabled = true;
    const skip = element("button", "건너뛰기", "pilot-feedback-skip");
    skip.type = "button";
    const ratings = [];
    for (let value = 1; value <= 5; value += 1) {
      const button = element("button", String(value), "pilot-feedback-rating");
      button.type = "button";
      button.setAttribute("aria-pressed", "false");
      button.addEventListener("click", () => {
        state.rating = value;
        for (const other of ratings) other.setAttribute("aria-pressed", String(other === button));
        send.disabled = false;
      });
      ratings.push(button);
      scale.append(button);
    }
    const hint = element("span", "1 전혀 아니다 · 5 매우 그렇다", "pilot-feedback-hint");
    const note = element("input", undefined, "pilot-feedback-note");
    note.type = "text";
    note.setAttribute("maxlength", "200");
    note.setAttribute("aria-label", "한 줄 메모 (선택)");
    note.setAttribute("placeholder", "한 줄 메모 (선택)");
    const status = element("p", "", "pilot-feedback-status");
    status.setAttribute("role", "status");
    send.addEventListener("click", () => {
      if (state.rating === null) return;
      submit(id, { rating: state.rating, skipped: false, note: note.value }, send, skip, status);
    });
    skip.addEventListener("click", () => {
      submit(id, { rating: null, skipped: true, note: null }, send, skip, status);
    });
    const actions = element("div", undefined, "pilot-feedback-actions");
    actions.append(send, skip);
    host.replaceChildren(question, scale, hint, note, actions, status);
    host.hidden = false;
  }

  globalThis.VoineyFeedback = Object.freeze({
    ended(reportId) {
      state.ended = true;
      if (typeof reportId === "string" && reportId) state.reportId = reportId;
      render();
    },
    report(reportId) {
      if (typeof reportId === "string" && reportId && reportId !== state.reportId) {
        state.reportId = reportId;
        state.shownFor = null;
      }
      render();
    },
    reset() {
      state.ended = false;
      state.reportId = null;
      hide();
    },
  });
})();
