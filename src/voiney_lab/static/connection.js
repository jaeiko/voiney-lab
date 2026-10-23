// Lane NET (2026-10-18): a lost connection, seen at once and put back.
//
// The server is the only authority over the run and its timers (AGENTS.md
// rule 2); the page receives them over the socket. This file is the page's
// half of keeping that true when the socket goes: it sees the loss within
// three seconds -- the socket closing or failing, the browser going
// offline, or the server's heartbeat stopping (decision 1) -- and says so
// loudly while the screen turns read only (decision 2). Meanwhile the step
// timer goes on as a copy on the screen, counted from the timer's start and
// end the server last sent, on the server's clock as measured on every
// connection (decision 3). The page tries again at 1, 2, 4 and 8 seconds and
// then every 15; the server continues the same experiment on the new
// connection, and its state then takes the place of the screen's copy
// (decision 4).
//
// While the connection is lost the screen changes nothing: it sends no
// command and keeps none to send later, the microphone is paused, and the
// step, the next step and the safety notes the server last sent stay on
// the screen. It runs beside the page's own script and uses its names
// (socket, sessionActive, acceptedSessionConfiguration, ...); the page calls
// voineyConnection.watch() for every socket it opens and lost() when one
// closes or fails.
"use strict";
(()=>{
// The page's "client.ping" goes this often; the server's heartbeat comes as often.
const PING_EVERY_MS=1000;
// Nothing at all received for this long while a session runs: lost. Looked at
// every CHECK_EVERY_MS, so the loss is seen within three seconds.
const SILENT_LOST_MS=2800,CHECK_EVERY_MS=200;
// Decision 2: the band's words. Text only (textContent).
const LOST_TITLE="연결이 끊겼어요 — 다시 연결 중",MIC_PAUSED="마이크 멈춤 · 연결되면 다시 들어요",
 KEEP_SCREEN_ON="화면을 켜 두세요 · 화면이 꺼지거나 다른 앱으로 가면 화면 타이머 알림이 울리지 않을 수 있어요";
// One short falling sound when the connection goes, and the phone's buzz where it has one.
const LOST_SOUND=[[740,0,.16],[494,.2,.32]],LOST_BUZZ=[300,150,300];
// Decision 3: the screen timer's label and its two moments' words, a minimum
// or a maximum named as the server names them (lane VX); the buzz with them.
const SCREEN_TIMER_LABEL="연결 끊김 중 화면 타이머",TIMER_BUZZ=[500,200,500];
const SCREEN_TIMER_WORDS={
 last_minute:{exact:"1분 남았어요",minimum:"최소 시간까지 1분 남았어요",maximum:"최대 시간까지 1분 남았어요"},
 ended:{exact:"타이머가 끝났어요",minimum:"최소 시간이 지났어요",maximum:"최대 시간이 됐어요"},
};
// Decision 4: the waits between tries (the last one repeats). A try the
// server has not taken within ATTEMPT_LIMIT_MS is given up for the next; the
// server refusing the run REFUSALS_LIMIT times ends the trying.
const RETRY_DELAYS_MS=[1000,2000,4000,8000,15000],ATTEMPT_LIMIT_MS=12000,REFUSALS_LIMIT=3;
const RESTORED_TITLE="다시 연결됐어요 — 서버 상태로 바꿨어요",RESTORED_SHOWN_MS=4000;
// When the run cannot be continued: the page ends the session and says why. Lane UI4 (QA-57's rest,
// 2026-10-23): by the screen's own names -- the records tab, the home tab's "이어서 하기" card.
const GIVEN_UP_WORDS={
 ended:"다시 연결됐지만 이 실험은 이미 끝났어요. 실험 기록에서 확인해 주세요.",
 not_available:"다시 연결됐지만 이 실험을 찾지 못했어요. 기록 탭에서 실험을 다시 골라 주세요.",
 refused:"다시 연결됐지만 서버가 이 실험을 이어 받지 않았어요. 홈 탭의 '이어서 하기'를 눌러 확인해 주세요.",
};

const net={socket:null,heardAt:0,pingSeq:0,pingHandle:null,checkHandle:null,lost:null,tick:null,band:null,
 // The server's clock less this page's (ms), and the round trip it was measured over.
 offset:{ms:0,rtt:Infinity,current:false},
 // The server's rule for a timer's last minute, as its heartbeat gives it.
 rule:{fromSeconds:300,seconds:60},
 screenTimer:null};

function runActive(){
 return sessionActive===true&&acceptedSessionConfiguration!==null&&net.socket!==null&&socket===net.socket;
}
// The run can be continued on a new connection: the server's session.ready
// bound it to a durable experiment (the workspace on, as in a pilot) -- not
// whatever the experiment list on the screen shows now. Each try reads the
// experiment's version afresh.
function resumable(){
 const accepted=acceptedSessionConfiguration;
 return typeof accepted?.experiment_session_id==="string"&&accepted.experiment_session_id.length>0
  &&Number.isInteger(accepted.experiment_session_version);
}

// Every socket the page opens: anything that comes on it -- words, audio or
// the heartbeat -- shows the server is there.
function watch(current){
 net.socket=current;net.heardAt=Date.now();net.offset.current=false;
 current.addEventListener?.("message",event=>{
  if(net.socket!==current)return;
  net.heardAt=Date.now();
  if(typeof event?.data==="string"&&event.data.includes('"server.heartbeat"'))heartbeat(event.data);
 });
 if(net.pingHandle===null)net.pingHandle=setInterval(sendPing,PING_EVERY_MS);
 if(net.checkHandle===null)net.checkHandle=setInterval(checkSilence,CHECK_EVERY_MS);
}

function sendPing(){
 const current=net.socket;
 if(!current||current.readyState!==WebSocket.OPEN)return;
 try{current.send(JSON.stringify({type:"client.ping",seq:++net.pingSeq,sent_at_ms:Date.now()}));}catch(_){}
}

// Decision 3: the difference between the server's clock and this page's,
// measured again on every connection from the heartbeat that answers a ping
// (the page sent it at t0, the server received it at t1 and sent the
// heartbeat at t2, the page received that at t3); the measure with the
// shortest round trip on this connection stands. A heartbeat before any
// ping answered gives a first, one-way measure.
function heartbeat(raw){
 let message;try{message=JSON.parse(raw);}catch(_){return;}
 if(message?.type!=="server.heartbeat")return;
 const t3=Date.now(),t2=Number(message.server_time_ms),t0=Number(message.ping_sent_at_ms),t1=Number(message.ping_received_ms);
 if(!Number.isFinite(t2))return;
 let offset=t2-t3,rtt=Infinity;
 if(Number.isFinite(t0)&&Number.isFinite(t1)&&t0<=t3){const trip=(t3-t0)-(t2-t1);if(trip>=0){offset=((t1-t0)+(t2-t3))/2;rtt=trip;}}
 if(!net.offset.current||rtt<net.offset.rtt)net.offset={ms:offset,rtt,current:true};
 const from=Number(message.timer_last_minute_from_seconds),seconds=Number(message.timer_last_minute_seconds);
 if(Number.isFinite(from)&&from>=0&&Number.isFinite(seconds)&&seconds>0)net.rule={fromSeconds:from,seconds};
}
function serverNow(){return Date.now()+net.offset.ms;}

// While a try is under way its own time limit stands in for the silence.
function checkSilence(){if(!net.lost&&runActive()&&Date.now()-net.heardAt>SILENT_LOST_MS)detected("server_silent");}

// The page's socket closed or failed (its onclose / onerror). True when the
// loss is taken here; false leaves the page's own close handling to run --
// no session ran on it, or it cannot be continued. A try's socket failing is
// that try failing.
function lost(current,reason){
 if(net.lost){
  const attempt=net.lost.attempt;
  if(attempt&&current&&attempt.socket===current)attemptFailed(attempt,reason);
  return true;
 }
 if(!current||current!==net.socket||socket!==current||!runActive())return false;
 net.socket=null;
 if(!resumable())return false;
 enterLost(current,reason);
 return true;
}

// Seen here (silence, offline): as lost(), and when not taken, the page's
// own close handling runs as for a close.
function detected(reason){
 const current=net.socket;
 if(lost(current,reason))return;
 if(!current||socket!==current)return;
 try{current.onclose?.({code:4000,reason});}catch(_){}
 socket=null;
 try{if(current.readyState<WebSocket.CLOSING)current.close(4000,"connection lost");}catch(_){}
}

function enterLost(current,reason){
 // The socket is let go first: nothing more is read from it or sent on it,
 // and its own close handler finds it is no longer the page's socket.
 socket=null;
 try{if(current.readyState<WebSocket.CLOSING)current.close(4000,"connection lost");}catch(_){}
 net.lost={at:Date.now(),reason,generation:sessionGeneration,accepted:{...acceptedSessionConfiguration},
  tries:0,refusals:0,retryAt:null,retryHandle:null,attempt:null};
 pauseMicrophone(true);
 clearPlayback();removeProvisionalTurns(sessionGeneration);
 renderState("RECONNECTING",LOST_TITLE);
 holdScreenTimer();
 showBand();
 playSound(LOST_SOUND);buzz(LOST_BUZZ);
 if(net.tick===null)net.tick=setInterval(tick,1000);
 scheduleTry();
 tick();
}

// --- Trying again (decision 4) -----------------------------------------------
function scheduleTry(){
 const lostState=net.lost;if(!lostState||lostState.attempt)return;
 const delay=RETRY_DELAYS_MS[Math.min(lostState.tries,RETRY_DELAYS_MS.length-1)];
 clearTimeout(lostState.retryHandle);
 lostState.retryAt=Date.now()+delay;
 lostState.retryHandle=setTimeout(()=>{lostState.retryHandle=null;void tryAgain();},delay);
 renderTryLine();
}

// One try: the experiment as the server has it now (its version, and whether
// it can still be continued), a new socket, and the start that continues it.
// Nothing said or pressed while the connection was lost goes with it.
async function tryAgain(){
 const lostState=net.lost;
 if(!lostState||lostState.attempt)return;
 if(lostState.generation!==sessionGeneration)return forget();
 clearTimeout(lostState.retryHandle);lostState.retryHandle=null;
 lostState.tries+=1;
 const attempt={socket:null,configurationId:null,limit:null};
 lostState.attempt=attempt;renderTryLine();
 attempt.limit=setTimeout(()=>attemptFailed(attempt,"timeout"),ATTEMPT_LIMIT_MS);
 const id=lostState.accepted.experiment_session_id;
 let experiment;
 try{
  const response=await workspaceFetch(`/api/workspace/experiments/${encodeURIComponent(id)}`,{cache:"no-store",headers:{Accept:"application/json"}});
  if(lostState.attempt!==attempt)return;
  if(response.status===404||response.status===403)return giveUp(attempt,"not_available");
  if(!response.ok)return attemptFailed(attempt,"lookup");
  experiment=await response.json();
 }catch(_){return attemptFailed(attempt,"lookup");}
 if(lostState.attempt!==attempt)return;
 if(!RESUMABLE_EXPERIMENT_STATUSES.has(experiment?.status)||!Number.isInteger(experiment?.version))return giveUp(attempt,"ended");
 currentExperimentSessionId=id;currentExperimentSessionVersion=experiment.version;currentExperimentSessionStatus=experiment.status;
 try{
  const opening=init(lostState.generation);attempt.socket=socket;
  // A socket let go before it opened settles nothing of its own.
  await Promise.race([opening,new Promise((_,reject)=>{attempt.abort=reject;})]);
 }catch(_){return attemptFailed(attempt,"socket_error");}
 if(lostState.attempt!==attempt)return;
 // The new session's turns are its own; its first state is taken whatever
 // number the old session's states had reached.
 forgetTurns();curatedStateRevision=-1;
 const previous=lostState.accepted,configuration_id=++nextConfigurationId;
 const pending={configuration_id,mode:"cascade",language:previous.language||"ko",protocol_id:previous.protocol_id,
  ...(previous.input_language&&previous.input_language!=="ko"?{input_language:previous.input_language}:{}),
  experiment_session_id:id,experiment_session_version:experiment.version,
  reconnect:{lost_at_ms:Math.round(lostState.at+net.offset.ms),reason:lostState.reason}};
 attempt.configurationId=configuration_id;pendingSessionConfiguration=pending;
 sendControl("session.start",pending);
}

// The page's per-turn bookkeeping, as resetSessionUI() clears it for a new
// session; the conversation already on the screen stays.
function forgetTurns(){
 for(const map of [turns,provisionalTurns,pendingFillers,toolEvents,visualEvents,researchEvents,brainEvents,greetingEvents,webEvents,drawingEvents,turnProgress,turnServerGenerations,turnHoldEvidence,interruptedTurns,interruptedCascadeTurns,runTurns,audioRecoveryTurns])map.clear();
 runCardKey=null;activeTurn=null;activeCascadeGeneration=null;
}

// What came on a try's socket, before the page reads it (onMessage). The
// server refusing the start is that try failing, not the page's error; a
// protocol it will not run ends the trying; its session started is the run
// continued.
function consume(message,current){
 const attempt=net.lost?.attempt;
 if(!attempt||attempt.socket!==current||!message)return false;
 const taken=attempt.configurationId!==null&&acceptedSessionConfiguration?.configuration_id===attempt.configurationId;
 if(message.type==="error"&&!taken){attemptFailed(attempt,"refused");return true;}
 if(message.type==="session.configuration_required"&&message.configuration_id===attempt.configurationId){giveUp(attempt,"refused");return true;}
 if(message.type==="session.started"&&taken)restored(attempt);
 return false;
}

function letGo(attempt){
 clearTimeout(attempt.limit);
 attempt.abort?.(new Error("try ended"));
 const current=attempt.socket;if(!current)return;
 if(net.socket===current)net.socket=null;
 if(socket===current)socket=null;
 try{if(current.readyState<WebSocket.CLOSING)current.close(4001,"reconnect attempt ended");}catch(_){}
}

function attemptFailed(attempt,why){
 const lostState=net.lost;if(!lostState||lostState.attempt!==attempt)return;
 lostState.attempt=null;letGo(attempt);
 if(pendingSessionConfiguration?.configuration_id===attempt.configurationId)pendingSessionConfiguration=null;
 if(why==="refused"&&++lostState.refusals>=REFUSALS_LIMIT)return giveUp(null,"refused");
 scheduleTry();
}

// The server took the run: its state now stands on the screen.
function restored(attempt){
 const lostState=net.lost;if(!lostState||lostState.attempt!==attempt)return;
 clearTimeout(attempt.limit);clearTimeout(lostState.retryHandle);
 endLost();
 pauseMicrophone(false);
 showRestored();
 renderRunView();
}

// The run cannot be continued: the page ends the session as its own close
// handling (init) does, and says why.
function giveUp(attempt,why){
 const lostState=net.lost;if(!lostState||(attempt&&lostState.attempt!==attempt))return;
 if(lostState.attempt)letGo(lostState.attempt);
 clearTimeout(lostState.retryHandle);
 endLost();hideBand();
 sessionActive=false;pendingSessionConfiguration=null;acceptedSessionConfiguration=null;
 removeProvisionalTurns(sessionGeneration);clearPlayback();stopWorkflowTimer();stopReportPolling();invalidateCuratedProtocolState();void closeAudio();
 renderState("ERROR",GIVEN_UP_WORDS[why]||GIVEN_UP_WORDS.refused);
}

// The page moved on (a new session) while the connection was lost: stop.
function forget(){const lostState=net.lost;if(!lostState)return;if(lostState.attempt)letGo(lostState.attempt);clearTimeout(lostState.retryHandle);endLost();hideBand();}

function endLost(){
 net.lost=null;net.screenTimer=null;
 if(net.tick!==null){clearInterval(net.tick);net.tick=null;}
 showScreenTimerLabel(false);
 if(net.band)net.band.clock.hidden=true;
}

function renderTryLine(){
 const lostState=net.lost;if(!lostState)return;
 if(lostState.attempt){setBandText("tries",`다시 연결하는 중… · ${lostState.tries}번째 시도`);return;}
 const wait=Math.max(0,Math.ceil(((lostState.retryAt??Date.now())-Date.now())/1000));
 setBandText("tries",lostState.tries?`${lostState.tries}번 시도했어요 · 다음 시도 ${wait}초 뒤`:`다음 시도 ${wait}초 뒤`);
}

// Decision 2: the microphone is paused, not closed -- nothing is heard while
// the connection is lost, and the same microphone listens again after.
function pauseMicrophone(paused){
 for(const track of micStream?.getAudioTracks?.()||[]){try{track.enabled=!paused;}catch(_){}}
}

function playSound(notes){
 const context=playContext;
 if(!context||typeof context.createOscillator!=="function"||typeof context.createGain!=="function")return false;
 try{
  void context.resume?.();
  const start=context.currentTime||0;
  for(const [hz,at,length] of notes){
   const tone=context.createOscillator(),envelope=context.createGain();
   tone.type="sine";tone.frequency.setValueAtTime(hz,start+at);
   envelope.gain.setValueAtTime(0,start+at);envelope.gain.linearRampToValueAtTime(.3,start+at+.02);envelope.gain.linearRampToValueAtTime(0,start+at+length);
   tone.connect(envelope);envelope.connect(cascadeGain||context.destination);
   tone.onended=()=>{try{envelope.disconnect();}catch(_){}};
   tone.start(start+at);tone.stop(start+at+length);
  }
  return true;
 }catch(_){return false;}
}
function buzz(pattern){try{return globalThis.navigator?.vibrate?.(pattern)===true;}catch(_){return false;}}

function tick(){
 if(!net.lost)return;
 const seconds=Math.max(0,Math.floor((Date.now()-net.lost.at)/1000));
 setBandText("title",`${LOST_TITLE} (${seconds}초)`);
 renderTryLine();
 checkScreenTimer();
 renderRunView();
 syncBandHeight();
}

// --- The screen timer (decision 3) ------------------------------------------
// The step timer the server last sent, counted on as a copy: from its end on
// the server's clock. Its last minute and its end are told here -- a sound,
// a buzz and the band -- each once, and only when it comes while the
// connection is lost (before the loss the server told it). A timer of less
// than the server's last-minute length gets no last minute, as from the server.
function holdScreenTimer(){
 const step=curatedProtocolState?.attached===true?curatedProtocolState.timers?.step:null,deadline=Date.parse(step?.deadline_at);
 net.screenTimer=null;
 if(!step||!["running","expired"].includes(step.state)||!Number.isFinite(deadline))return;
 const duration=Math.max(0,Number(step.duration_seconds)||0),left=deadline-serverNow();
 net.screenTimer={deadline,bound:["minimum","maximum"].includes(step.bound)?step.bound:"exact",
  lastMinute:duration>=net.rule.fromSeconds&&left>net.rule.seconds*1000,ending:left>0};
 showScreenTimerLabel(true);
}
function checkScreenTimer(){
 const timer=net.screenTimer;if(!timer)return;
 const left=timer.deadline-serverNow();
 // The copy's time, in the band too: the timer panel can be below the fold on a phone.
 const seconds=Math.ceil(left/1000);
 setBandText("clock",`${SCREEN_TIMER_LABEL} · ${seconds>0?`${formatElapsedClock(seconds)} 남음`:`끝남 · ${formatElapsedClock(-seconds)} 지남`}`);
 if(net.band)net.band.clock.hidden=false;
 if(timer.ending&&left<=0){timer.ending=false;timer.lastMinute=false;screenTimerAlert("ended",timer.bound);}
 else if(timer.lastMinute&&left<=net.rule.seconds*1000){timer.lastMinute=false;screenTimerAlert("last_minute",timer.bound);}
}
function screenTimerAlert(kind,bound){
 setBandText("timer",`화면 타이머 · ${SCREEN_TIMER_WORDS[kind][bound]}`);
 if(net.band)net.band.timer.hidden=false;
 if(typeof playTimerNoticeTone==="function")playTimerNoticeTone();
 buzz(TIMER_BUZZ);
}
let screenTimerLabel=null;
function showScreenTimerLabel(on){
 const panel=globalThis.document?.getElementById?.("run-timer");
 if(!screenTimerLabel&&on&&panel){
  screenTimerLabel=document.createElement("p");screenTimerLabel.id="run-timer-offline";screenTimerLabel.className="run-timer-offline";
  screenTimerLabel.textContent=SCREEN_TIMER_LABEL;
  // At the top of the panel, above the digits, where it is seen with them.
  const digits=panel.querySelector?.(".run-timer-digits");
  if(digits&&typeof panel.insertBefore==="function")panel.insertBefore(screenTimerLabel,digits);else panel.appendChild(screenTimerLabel);
 }
 if(screenTimerLabel)screenTimerLabel.hidden=!on;
}

// --- The band (decision 2): across the whole screen, over the top. ---------
const BAND_STYLE=`
#connection-band{position:fixed;left:0;right:0;top:0;z-index:1000;margin:0;padding:10px 16px 12px;background:#b42318;color:#fff;box-shadow:0 2px 14px rgba(0,0,0,.35);font-size:15px;line-height:1.3}
#connection-band[hidden]{display:none}
#connection-band p{margin:2px 0}
#connection-band .connection-band-title{font-size:clamp(20px,5.2vw,26px);font-weight:800;margin:0 0 4px}
#connection-band .connection-band-clock{font-weight:700;font-size:17px}
#connection-band .connection-band-clock[hidden]{display:none}
#connection-band .connection-band-screen{font-size:13px;opacity:.95}
#connection-band[data-state="restored"]{background:#1b7f3b}
#connection-band[data-state="restored"] p:not(.connection-band-title){display:none}
#connection-band .connection-band-timer{display:inline-block;margin:4px 0;padding:4px 10px;border-radius:8px;background:#fff;color:#b42318;font-weight:800;font-size:clamp(18px,4.8vw,24px)}
#connection-band .connection-band-timer[hidden]{display:none}
#run-timer #run-timer-offline{display:inline-block;width:auto;margin:4px 0;padding:2px 8px;border-radius:6px;background:#b42318;color:#fff;font-weight:700;font-size:13px;line-height:1.3}
#run-timer #run-timer-offline[hidden]{display:none}
@media (max-height:520px){
 #connection-band{padding:6px 16px 8px}
 #connection-band .connection-band-title{font-size:20px;margin:0 0 2px}
 #connection-band :is(.connection-band-tries,.connection-band-mic){display:inline;margin-right:14px}
 #connection-band .connection-band-clock{font-size:15px;margin:1px 0}
 #connection-band .connection-band-timer{font-size:17px;padding:2px 8px;margin:2px 0}
}
body:is([data-connection="lost"],[data-connection="restored"]){padding-top:var(--connection-band-height,0px)}
body:is([data-connection="lost"],[data-connection="restored"]) .sticky-rail{display:none}
body[data-connection="lost"] :is(button,input,select,textarea,summary,a[href]):not(#connection-band *){pointer-events:none;opacity:.5}
`;
function showBand(){
 const body=globalThis.document?.body;
 if(!net.band){
  const style=document.createElement("style");style.id="connection-style";style.textContent=BAND_STYLE;
  (document.head||body)?.appendChild?.(style);
  const band=document.createElement("section");band.id="connection-band";band.setAttribute?.("role","alert");band.setAttribute?.("aria-live","assertive");
  const line=(name,className)=>{const item=document.createElement("p");item.className=className;item.dataset.line=name;band.appendChild(item);return item;};
  net.band={node:band,title:line("title","connection-band-title"),tries:line("tries","connection-band-tries"),mic:line("mic","connection-band-mic"),clock:line("clock","connection-band-clock"),timer:line("timer","connection-band-timer"),screen:line("screen","connection-band-screen")};
  net.band.timer.hidden=true;net.band.clock.hidden=true;
  body?.prepend?.(band);
  // The space kept for the band follows its height as its lines change.
  if(typeof ResizeObserver==="function")new ResizeObserver(syncBandHeight).observe(band);
 }
 setBandText("mic",MIC_PAUSED);setBandText("screen",KEEP_SCREEN_ON);
 clearTimeout(net.band.hideHandle);
 if(net.band.node.dataset)net.band.node.dataset.state="lost";
 net.band.timer.hidden=true;
 net.band.node.hidden=false;
 if(body?.dataset)body.dataset.connection="lost";
 syncBandHeight();
}
function syncBandHeight(){
 const height=net.band?.node?.offsetHeight;
 if(Number.isFinite(height)&&height>0)globalThis.document?.body?.style?.setProperty?.("--connection-band-height",`${height}px`);
}
function setBandText(name,text){const item=net.band?.[name];if(item)item.textContent=text;}
function showRestored(){
 if(!net.band)return;
 const body=globalThis.document?.body;
 if(net.band.node.dataset)net.band.node.dataset.state="restored";
 setBandText("title",RESTORED_TITLE);
 if(body?.dataset)body.dataset.connection="restored";
 syncBandHeight();
 clearTimeout(net.band.hideHandle);
 net.band.hideHandle=setTimeout(()=>{if(!net.lost)hideBand();},RESTORED_SHOWN_MS);
}
function hideBand(){
 if(!net.band)return;
 clearTimeout(net.band.hideHandle);
 net.band.node.hidden=true;
 const body=globalThis.document?.body;
 if(body?.dataset)body.dataset.connection="";
}

// Decision 2: read only. A tap, a typed key or a changed value on any control
// outside the band goes nowhere while the connection is lost.
function readOnly(event){
 if(!net.lost)return;
 const target=event?.target;
 if(!target||typeof target.closest!=="function"||target.closest("#connection-band"))return;
 if(!target.closest("button,input,select,textarea,summary,a[href],label,[role=button],[contenteditable=true]"))return;
 event.preventDefault?.();event.stopImmediatePropagation?.();
}
for(const kind of ["click","change","input","submit","keydown"])globalThis.document?.addEventListener?.(kind,readOnly,true);

globalThis.addEventListener?.("offline",()=>{if(net.lost)return;if(runActive())detected("browser_offline");});
// The browser has a network again: the next try goes now, not at its time.
globalThis.addEventListener?.("online",()=>{if(net.lost&&!net.lost.attempt)void tryAgain();});

function micText(){return net.lost?MIC_PAUSED:"";}
function isLost(){return net.lost!==null;}
// A try is under way: the start it sends is the run's own, not a new session's.
function reconnecting(){return Boolean(net.lost?.attempt);}

globalThis.voineyConnection=Object.freeze({watch,lost,consume,micText,isLost,reconnecting,serverNow});
// The page opens its socket in its own script, before this file loads, and a
// session runs on that socket: it is watched from here.
if(socket)watch(socket);
})();
