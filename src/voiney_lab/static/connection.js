// Lane NET (2026-10-18): a lost connection, seen at once and put back.
//
// The server is the only authority over the run and its timers (AGENTS.md
// rule 2); the page receives them over the socket. This file is the page's
// half of keeping that true when the socket goes: it sees the loss within
// three seconds -- the socket closing or failing, the browser going
// offline, or the server's heartbeat stopping (decision 1) -- and says so
// loudly while the screen turns read only (decision 2).
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

const net={socket:null,heardAt:0,pingSeq:0,pingHandle:null,checkHandle:null,lost:null,tick:null,band:null};

function runActive(){
 return sessionActive===true&&acceptedSessionConfiguration!==null&&net.socket!==null&&socket===net.socket;
}
// The run can be continued on a new connection: the server bound it to a
// durable experiment (the workspace on, as in a pilot).
function resumable(){
 const accepted=acceptedSessionConfiguration;
 return typeof accepted?.experiment_session_id==="string"&&accepted.experiment_session_id.length>0
  &&currentExperimentSessionId===accepted.experiment_session_id&&Number.isInteger(currentExperimentSessionVersion);
}

// Every socket the page opens: anything that comes on it -- words, audio or
// the heartbeat -- shows the server is there.
function watch(current){
 net.socket=current;net.heardAt=Date.now();
 current.addEventListener?.("message",()=>{if(net.socket===current)net.heardAt=Date.now();});
 if(net.pingHandle===null)net.pingHandle=setInterval(sendPing,PING_EVERY_MS);
 if(net.checkHandle===null)net.checkHandle=setInterval(checkSilence,CHECK_EVERY_MS);
}

function sendPing(){
 const current=net.socket;
 if(!current||current.readyState!==WebSocket.OPEN)return;
 try{current.send(JSON.stringify({type:"client.ping",seq:++net.pingSeq,sent_at_ms:Date.now()}));}catch(_){}
}

function checkSilence(){if(runActive()&&Date.now()-net.heardAt>SILENT_LOST_MS)detected("server_silent");}

// The page's socket closed or failed (its onclose / onerror). True when the
// loss is taken here; false leaves the page's own close handling to run --
// no session ran on it, or it cannot be continued.
function lost(current,reason){
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
 net.lost={at:Date.now(),reason};
 pauseMicrophone(true);
 clearPlayback();removeProvisionalTurns(sessionGeneration);
 renderState("RECONNECTING",LOST_TITLE);
 showBand();
 playSound(LOST_SOUND);buzz(LOST_BUZZ);
 if(net.tick===null)net.tick=setInterval(tick,1000);
 tick();
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
 renderRunView();
}

// --- The band (decision 2): across the whole screen, over the top. ---------
const BAND_STYLE=`
#connection-band{position:fixed;left:0;right:0;top:0;z-index:1000;margin:0;padding:12px 16px 14px;background:#b42318;color:#fff;box-shadow:0 2px 14px rgba(0,0,0,.35);font-size:16px;line-height:1.35}
#connection-band[hidden]{display:none}
#connection-band p{margin:2px 0}
#connection-band .connection-band-title{font-size:clamp(20px,5.4vw,28px);font-weight:800;margin:0 0 4px}
body[data-connection="lost"]{padding-top:var(--connection-band-height,0px)}
body[data-connection="lost"] .sticky-rail{top:var(--connection-band-height,0px)}
body[data-connection="lost"] :is(button,input,select,textarea,summary,a[href]):not(#connection-band *){pointer-events:none;opacity:.5}
`;
function showBand(){
 const body=globalThis.document?.body;
 if(!net.band){
  const style=document.createElement("style");style.id="connection-style";style.textContent=BAND_STYLE;
  (document.head||body)?.appendChild?.(style);
  const band=document.createElement("section");band.id="connection-band";band.setAttribute?.("role","alert");band.setAttribute?.("aria-live","assertive");
  const line=(name,className)=>{const item=document.createElement("p");item.className=className;item.dataset.line=name;band.appendChild(item);return item;};
  net.band={node:band,title:line("title","connection-band-title"),mic:line("mic","connection-band-mic"),screen:line("screen","connection-band-screen")};
  body?.prepend?.(band);
 }
 setBandText("mic",MIC_PAUSED);setBandText("screen",KEEP_SCREEN_ON);
 net.band.node.hidden=false;
 if(body?.dataset)body.dataset.connection="lost";
 const height=net.band.node.offsetHeight;
 if(Number.isFinite(height)&&height>0)body?.style?.setProperty?.("--connection-band-height",`${height}px`);
}
function setBandText(name,text){const item=net.band?.[name];if(item)item.textContent=text;}

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

globalThis.addEventListener?.("offline",()=>{if(runActive())detected("browser_offline");});

function micText(){return net.lost?MIC_PAUSED:"";}
function isLost(){return net.lost!==null;}

globalThis.voineyConnection=Object.freeze({watch,lost,micText,isLost});
})();
