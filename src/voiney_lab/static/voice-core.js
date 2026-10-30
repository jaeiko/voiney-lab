// The voice, the socket, the timers' clock and the screen lock -- the code
// both screens share (lane RX2, person's decision 4 of 2026-10-25).
//
// Every line below was moved unchanged out of index.html's own script, where
// lanes U2, SP1, VF, VT, NET, TAB, FIXV and the others built and tested it:
// the socket to /ws, the microphone (AudioWorklet, 16 kHz PCM16 frames, the
// device it keeps, its automatic gain control, a lost device opened again),
// the answer's audio played sentence by sentence with the barge-in duck, the
// waiting tone and the timer's tone, the replay of the last turn, the sound
// a tablet holds back until a touch, the screen kept on during a run, and
// the server's clock the timers are read on. Nothing here is new and nothing
// here decides a workflow state: the server does (AGENTS.md rule 2).
//
// It is a classic script, as before: its names are the page's names
// (socket, sessionActive, acceptedSessionConfiguration, pump, ensureAudio,
// ...), which connection.js and the page's own script use as they did. A
// page that loads it loads it first -- index.html before its own script, the
// new screen (web/src/voice/) before connection.js -- and defines the names
// in VOICE_CORE_HOST_NAMES below: what this code tells the screen (a state,
// a line of words, a failure) and asks of it (the socket's address, the
// session's events). Its top level only declares and listens, so the order
// of the two scripts' declarations does not matter. Not strict, as the
// page's script was not.

// What a page loading this file defines (tests/test_lane_rx2_voice_core.py
// checks both pages and that nothing else is reached for).
var VOICE_CORE_HOST_NAMES=Object.freeze([
 "$","setText","setDisabled","announceMilestone","renderState","renderRunStatus","renderMicCheck","screenView",
 "serverSocketUrl","onMessage","fail","recheckAccountAfterClose","removeProvisionalTurns","invalidateCuratedProtocolState",
 "stopWorkflowTimer","stopReportPolling",
]);

// --- The session, the socket and the audio's state -----------------------------
let socket,micContext,micWorkletNode,micSource,micStream,playContext,cascadeGain,sessionActive=false;
let receiving=null,receivedFrames=[],receivingFiller=null,fillerFrames=[],fillerSource=null,fillerIdentity=null,activeTurn=null,activeCascadeGeneration=null,audioComplete=false,turnComplete=false,playing=false,nextSegment=0,activeSource=null,replaySource=null,lastReplayableAudio=null,replayTurnKey=null,replayTurnSocket=null,replayTurnFull=false,pendingVoiceReplay=false,sessionGeneration=0,playbackEpoch=0,visibleState="IDLE";
let micSignalSeen=false;
const interruptedCascadeTurns=new Set();
let cascadeInterruption={state:"idle",turn_id:null,generation:null,started_at:null};
let nextConfigurationId=0,pendingSessionConfiguration=null,acceptedSessionConfiguration=null;
const queued=new Map(),audioRecoveryTurns=new Set();

// --- The socket -------------------------------------------------------------------
const ACCOUNT_SOCKET_CLOSE_CODE=1008;
function sendControl(type,fields={}){if(socket?.readyState===WebSocket.OPEN)socket.send(JSON.stringify({type,...fields}));}
async function init(generation=sessionGeneration){return new Promise((resolve,reject)=>{const current=new WebSocket(serverSocketUrl("/ws"));socket=current;globalThis.voineyConnection?.watch(current);current.binaryType="arraybuffer";current.onopen=()=>{if(generation===sessionGeneration&&socket===current)resolve();};current.onmessage=e=>onMessage(e,generation,current);current.onclose=event=>{if(event?.code===ACCOUNT_SOCKET_CLOSE_CODE)void recheckAccountAfterClose();if(generation!==sessionGeneration||socket!==current)return;if(globalThis.voineyConnection?.lost?.(current,"socket_closed"))return;sessionActive=false;pendingSessionConfiguration=null;acceptedSessionConfiguration=null;$("session-configuration-status").textContent="연결 종료 · 다시 선택하고 시작해 주세요";removeProvisionalTurns(generation);clearPlayback();stopWorkflowTimer();stopReportPolling();invalidateCuratedProtocolState();void closeAudio();renderState("ERROR","연결이 예기치 않게 종료됐습니다. 설정을 다시 확인해 주세요.");};current.onerror=()=>{if(generation!==sessionGeneration||socket!==current)return;if(!globalThis.voineyConnection?.lost?.(current,"socket_error"))fail("WebSocket 연결 실패");reject(new Error("WebSocket 연결 실패"));};});}

// --- The server's clock and the timers read on it -----------------------------
function stepTimerRemainingSeconds(step){const deadline=Date.parse(step.deadline_at);return Number.isFinite(deadline)?Math.ceil((deadline-serverNowMs())/1000):step.state==="expired"?-Math.max(0,(Number(step.elapsed_seconds)||0)-(Number(step.duration_seconds)||0)):Math.max(0,Number(step.remaining_seconds)||0);}
// Lane NET decision 3: the timers are read on the server's clock, this page's clock corrected by the difference measured on the connection (connection.js).
function serverNowMs(){return globalThis.voineyConnection?.serverNow?.()??Date.now();}
function formatElapsedClock(seconds){
 const total=Math.max(0,Math.floor(Number(seconds)||0));
 const hours=Math.floor(total/3600),minutes=Math.floor((total%3600)/60),secs=total%60;
 return hours>0?`${hours}:${String(minutes).padStart(2,"0")}:${String(secs).padStart(2,"0")}`:`${minutes}:${String(secs).padStart(2,"0")}`;
}

// --- The screen kept on ----------------------------------------------------------
// Lane UI0 decision 5 (2026-10-12): while the experiment runs the page asks
// the browser to keep the screen on (Screen Wake Lock) where the browser has
// it; a browser without it, or a refusal, is passed over in silence. The
// lock is let go when the run view ends, and asked for again when the page
// comes back into view (a browser lets it go while the page is hidden).
// Lane TAB decision 3 (2026-10-23): a tablet's browser may grant the lock only
// while a finger is on the screen, and a hands-free run may see no touch at
// all. So the lock is asked for in the start press itself (primeRunStart) and
// held while the start is under way (SCREEN_WAKE_START_MS), the request runs
// in the same turn as the touch, and a tap during the run asks again for a
// lock the browser let go.
let screenWakeLock=null,screenWakeLockWanted=false,screenWakeLockAsking=null,screenWakeLockStartHold=0,screenWakeLockStartPresses=0;
var SCREEN_WAKE_START_MS=20000;
function keepScreenOn(on){
 screenWakeLockWanted=Boolean(on);
 if(!screenWakeLockWanted){const held=screenWakeLock;screenWakeLock=null;return held&&!held.released?Promise.resolve().then(()=>held.release()).then(()=>false,()=>false):Promise.resolve(false);}
 if(screenWakeLock&&!screenWakeLock.released)return Promise.resolve(true);
 if(screenWakeLockAsking)return screenWakeLockAsking;
 const api=globalThis.navigator?.wakeLock;
 if(!api||typeof api.request!=="function"||globalThis.document?.visibilityState==="hidden")return Promise.resolve(false);
 screenWakeLockAsking=new Promise(resolve=>{resolve(api.request("screen"));}).then(lock=>{
  if(!screenWakeLockWanted){Promise.resolve().then(()=>lock?.release?.()).catch(()=>{});return false;}
  screenWakeLock=lock||null;lock?.addEventListener?.("release",()=>{if(screenWakeLock===lock)screenWakeLock=null;});return Boolean(lock);
 },()=>false).finally(()=>{screenWakeLockAsking=null;});
 return screenWakeLockAsking;
}
function onScreenVisibilityChange(){if(globalThis.document?.visibilityState==="visible"&&screenWakeLockWanted&&!screenWakeLock)return keepScreenOn(true);return Promise.resolve(Boolean(screenWakeLock));}
// The start press: the screen kept on until the run view takes over, or let go when no run follows. The hold
// is the latest press's own number, not a time read back: a timer may fire while Date.now() still reads a hair
// under its deadline (a coarse or lagging clock), and the lock then stayed on.
function keepScreenOnForStart(){
 const hold=++screenWakeLockStartPresses;screenWakeLockStartHold=hold;
 const timer=setTimeout(()=>{if(screenWakeLockStartHold!==hold)return;screenWakeLockStartHold=0;if(screenView()!=="during"&&screenWakeLockWanted)void keepScreenOn(false);},SCREEN_WAKE_START_MS);
 timer?.unref?.();
 return keepScreenOn(true);
}

// --- The microphone and the sound a tablet holds back ----------------------------
function resamplePcm16(input,sourceRate,targetRate){
 const length=Math.max(1,Math.round(input.length*targetRate/sourceRate)),output=new Int16Array(length);
 for(let i=0;i<length;i++){const position=i*sourceRate/targetRate,left=Math.min(Math.floor(position),input.length-1),right=Math.min(left+1,input.length-1),fraction=position-left,sample=input[left]*(1-fraction)+input[right]*fraction;output[i]=Math.max(-32768,Math.min(32767,Math.round(sample*32767)));}
 return output;
}
// Lane HEAR2 decision 7 (2026-10-26): the microphone's rate (48 kHz on a laptop) to 16 kHz with a low-pass first.
// resamplePcm16 above picks one sample in three with nothing filtered, so fan, pump and hiss above 8 kHz folded
// into the voice band unattenuated (measured: noise at -10 dB gave 10 dB signal-to-error, 47 dB with this filter;
// a quiet room is the same either way). A 64-tap windowed-sinc low-pass at 0.45 x 16 kHz runs ahead of the same
// pick, and keeps its memory across the worklet's 20 ms blocks so a block edge leaves no mark. One per capture.
// The filter delays the sound by DOWNSAMPLE_TAPS/2 input samples (0.67 ms at 48 kHz).
const DOWNSAMPLE_TAPS=64;
function createPcm16Downsampler(sourceRate,targetRate){
 if(!(sourceRate>targetRate))return input=>resamplePcm16(input,sourceRate,targetRate);
 const taps=DOWNSAMPLE_TAPS,mid=taps/2,fc=0.45*targetRate/sourceRate,kernel=new Float32Array(taps+1);let sum=0;
 for(let i=0;i<=taps;i++){const x=i-mid,sinc=x===0?2*fc:Math.sin(2*Math.PI*fc*x)/(Math.PI*x),window=0.42-0.5*Math.cos(2*Math.PI*i/taps)+0.08*Math.cos(4*Math.PI*i/taps);kernel[i]=sinc*window;sum+=kernel[i];}
 for(let i=0;i<=taps;i++)kernel[i]/=sum;
 const step=sourceRate/targetRate;let history=new Float32Array(taps),position=0;
 return input=>{
  // Filtered samples line up with the input (group delay = mid); the first mid outputs of a stream read the silent history.
  const joined=new Float32Array(history.length+input.length);joined.set(history,0);joined.set(input,history.length);
  const filtered=new Float32Array(input.length);
  for(let n=0;n<input.length;n++){let acc=0;const base=n+taps;for(let i=0;i<=taps;i++)acc+=kernel[i]*joined[base-i];filtered[n]=acc;}
  const output=[];
  for(let at=position;at<input.length;at+=step){const left=Math.floor(at),right=Math.min(left+1,input.length-1),fraction=at-left,sample=filtered[left]*(1-fraction)+filtered[right]*fraction;output.push(Math.max(-32768,Math.min(32767,Math.round(sample*32767))));}
  position=(position+Math.ceil((input.length-position)/step)*step)-input.length;
  if(input.length>=taps)history=input.slice(input.length-taps);else{const next=new Float32Array(taps);next.set(history.subarray(input.length),0);next.set(input,taps-input.length);history=next;}
  return Int16Array.from(output);
 };
}
// Lane U2 (2026-10-06): the microphone is opened once per session and kept
// open across turns, playback, pause and resume. Nothing here closes or
// reopens it on its own except a device that went away: a track that ended
// or a devicechange that took the device with it. Then, after a short wait,
// it is opened again once; a second loss within MIC_AUTO_RECONNECT_GAP_MS, or
// a failed attempt, stops and shows the reason with a "마이크 다시 연결"
// button. Frames are sent exactly as before: while the session is active
// and the socket is open (the worklet handler below is unchanged).
const MIC_RECONNECT_DELAY_MS=800,MIC_AUTO_RECONNECT_GAP_MS=30000,MIC_DEVICE_STORAGE_KEY="voiney-lab.mic-device";
let micReconnectTimer=null,micReconnecting=false,micAutoReconnectAt=0,micTrackWatched=null,micReportedIdentity=null;
function readStoredMicDevice(){try{const raw=localStorage.getItem(MIC_DEVICE_STORAGE_KEY),value=raw?JSON.parse(raw):null;return value&&typeof value.deviceId==="string"&&value.deviceId?{deviceId:value.deviceId,label:typeof value.label==="string"?value.label:""}:null;}catch(_){return null;}}
function storeMicDevice(value){try{if(value)localStorage.setItem(MIC_DEVICE_STORAGE_KEY,JSON.stringify(value));else localStorage.removeItem(MIC_DEVICE_STORAGE_KEY);}catch(_){}}
async function listMicDevices(){try{const all=await navigator.mediaDevices?.enumerateDevices?.();return Array.isArray(all)?all.filter(item=>item.kind==="audioinput"&&item.deviceId&&!["default","communications"].includes(item.deviceId)):[];}catch(_){return [];}}
// The chosen device when it is connected now (by id, else by its name, which
// Safari keeps while it rotates ids); null means the system default.
function chosenMicDeviceId(inputs){const stored=readStoredMicDevice();if(!stored)return null;const byId=inputs.find(item=>item.deviceId===stored.deviceId);if(byId)return byId.deviceId;const byLabel=stored.label?inputs.find(item=>item.label===stored.label):null;return byLabel?byLabel.deviceId:null;}
async function renderMicDevices(inputs=null){
 const select=$("mic-device");if(!select)return;
 const list=inputs||await listMicDevices(),stored=readStoredMicDevice(),chosen=chosenMicDeviceId(list);
 select.replaceChildren();
 const fallback=document.createElement("option");fallback.value="";fallback.textContent="기본 마이크 (시스템 설정)";select.appendChild(fallback);
 list.forEach((item,index)=>{const option=document.createElement("option");option.value=item.deviceId;option.textContent=item.label||`마이크 ${index+1}`;select.appendChild(option);});
 if(stored&&!chosen){const missing=document.createElement("option");missing.value=stored.deviceId;missing.textContent=`${stored.label||"고른 마이크"} · 연결 안 됨, 기본 마이크 사용`;select.appendChild(missing);}
 select.value=stored?(chosen||stored.deviceId):"";
 renderMicCheck();
}
function currentMicTrack(){return micStream?.getAudioTracks?.()[0]||micStream?.getTracks?.()[0]||null;}
async function openMicStream(){
 // Lane TAB decision 3: a page opened without HTTPS (a tablet on the lab's network by its address) has no microphone at all.
 if(typeof navigator.mediaDevices?.getUserMedia!=="function")throw new Error("이 주소에서는 마이크를 쓸 수 없어요. HTTPS 주소로 열어 주세요.");
 const supported=navigator.mediaDevices.getSupportedConstraints?.()||null,audioConstraints={channelCount:1};
 for(const name of ["echoCancellation","noiseSuppression","autoGainControl"]){if(!supported||supported[name]===true)audioConstraints[name]=true;}
 // Lane SP1 decision 2: "항상 끔" opens the microphone without automatic gain control; "목소리를 맞추면 끔" turns it off on the track once the voice is measured (applyAutoGainControl below).
 if("autoGainControl" in audioConstraints&&agcMode()==="off")audioConstraints.autoGainControl=false;
 if(readStoredMicDevice()){const deviceId=chosenMicDeviceId(await listMicDevices());if(deviceId)audioConstraints.deviceId={exact:deviceId};}
 return {stream:await navigator.mediaDevices.getUserMedia({audio:audioConstraints}),audioConstraints};
}
function reportMicSettings(stream,audioConstraints){
 micAudioConstraints=audioConstraints;
 const names=["echoCancellation","noiseSuppression","autoGainControl"],track=stream.getAudioTracks?.()[0]||stream.getTracks?.()[0]||null,settings=track?.getSettings?.()||{},actual=Object.fromEntries(names.map(name=>[name,typeof settings[name]==="boolean"?settings[name]:null]));
 sendControl("client.audio_constraints",{requested:Object.fromEntries(names.map(name=>[name,audioConstraints[name]===true])),actual});
 setText("audio-processing-capability",actual.echoCancellation===true?"에코 제거 확인":actual.echoCancellation===false?"에코 제거 꺼짐 · 헤드폰 권장":"에코 제거 확인 불가 · 헤드폰 권장");
 // Lane U decision 7: when the browser did not turn echo cancellation on,
 // the speaker can be heard again as the researcher's words. Say so in the
 // body, with what to do, instead of only in the developer chip.
 const echoNotice=$("audio-echo-notice");if(echoNotice){echoNotice.hidden=actual.echoCancellation===true;echoNotice.textContent=actual.echoCancellation===true?"":"이 마이크는 에코 제거가 켜지지 않아 스피커 소리가 내 말로 들어갈 수 있습니다. 헤드폰을 쓰거나 스피커 소리를 줄이세요.";}
 const name=track?.label||"",identity=settings.deviceId?`device:${settings.deviceId}`:name?`label:${name}`:null;
 const changed=Boolean(sessionActive&&identity&&micReportedIdentity&&identity!==micReportedIdentity);
 if(identity)micReportedIdentity=identity;
 setText("mic-device-status",name?(changed?`마이크가 바뀌었어요 · ${name} 사용 중`:`사용 중 · ${name}`):"");
 renderAgcStatus(actual.autoGainControl);
}
// Lane SP1 decision 2 (2026-10-10): the microphone's automatic gain control
// is turned off on the open track while the wearer's voice is measured and
// after -- it would lift a quieter voice toward the wearer's level -- and
// turned back on only when the measurement was skipped. "항상 켬" and "항상
// 끔" leave it as the name says. The track is not reopened (lane U2), and
// the browser is told again what was asked and what it does.
const AGC_MODE_STORAGE_KEY="voiney-lab.agc-mode";
let micAudioConstraints={},voiceReferenceMeasured=false;
function agcMode(){const select=$("agc-mode"),value=select?.value;if(["calibrated","on","off"].includes(value))return value;try{const stored=localStorage.getItem(AGC_MODE_STORAGE_KEY);return ["calibrated","on","off"].includes(stored)?stored:"calibrated";}catch(_){return "calibrated";}}
function storeAgcMode(value){try{localStorage.setItem(AGC_MODE_STORAGE_KEY,value);}catch(_){}}
function renderAgcStatus(actual){const mode=agcMode();setText("agc-status",actual===true?(mode==="calibrated"?"켜짐 · 목소리를 맞추면 꺼요":"켜짐"):actual===false?(mode==="calibrated"&&!voiceReferenceMeasured?"꺼짐 · 목소리 맞추는 중":"꺼짐"):"브라우저가 알려 주지 않음");}
async function applyAutoGainControl(on){
 const track=currentMicTrack();if(!track||typeof track.applyConstraints!=="function"||!("autoGainControl" in micAudioConstraints))return false;
 if(micAudioConstraints.autoGainControl===on)return true;
 try{await track.applyConstraints({autoGainControl:on});}catch(_){setText("agc-status","자동 음량 조절을 바꾸지 못했어요 · 이 마이크는 지원하지 않아요");return false;}
 if(micStream)reportMicSettings(micStream,{...micAudioConstraints,autoGainControl:on});
 return true;
}
function agcForCalibration(state){
 if(agcMode()!=="calibrated")return;
 if(state==="prompting"||state==="prompted"||state==="measured")void applyAutoGainControl(false);
 else if(state==="skipped"&&!voiceReferenceMeasured)void applyAutoGainControl(true);
}
function onMicTrackEnded(){scheduleMicReconnect("ended");}
function watchMicTrack(){
 if(micTrackWatched){try{micTrackWatched.removeEventListener?.("ended",onMicTrackEnded);}catch(_){}micTrackWatched=null;}
 const track=currentMicTrack();if(track?.addEventListener){track.addEventListener("ended",onMicTrackEnded);micTrackWatched=track;}
}
function showMicRecovery(text){
 const box=$("mic-recovery");if(!box)return;
 setText("mic-recovery-text",text);box.hidden=false;renderRunStatus();
 const button=$("mic-reconnect");if(button)setDisabled(button,false);
 if(visibleState==="LISTENING")$("status").textContent="마이크가 끊겼습니다 · 아래 ‘마이크 다시 연결’을 눌러 주세요.";
 announceMilestone(text);
}
function hideMicRecovery(){const box=$("mic-recovery");if(box)box.hidden=true;setText("mic-recovery-text","");renderRunStatus();}
function micErrorText(error){
 const name=error?.name||"";
 if(name==="NotAllowedError"||name==="SecurityError")return micBlockedWords(error)||"브라우저가 마이크 사용을 막았습니다. 주소창의 마이크 권한을 허용한 뒤 다시 연결하세요.";
 if(name==="NotFoundError"||name==="OverconstrainedError")return "연결된 마이크를 찾지 못했습니다. 마이크(아이폰·헤드셋)가 연결됐는지 확인한 뒤 다시 연결하세요.";
 if(name==="NotReadableError"||name==="AbortError")return "마이크가 응답하지 않습니다. 다른 앱이 마이크를 쓰고 있지 않은지 확인한 뒤 다시 연결하세요.";
 return "마이크를 다시 열지 못했습니다. 마이크 연결을 확인한 뒤 다시 연결하세요.";
}
// Lane TAB decision 3 (2026-10-23): a refused microphone is turned back on in a
// different place on each device -- Safari's page menu on an iPad, the
// Settings app for the home-screen app, the site's permissions in Chrome on
// Android or the system's app settings when Android itself refused ("by
// system"). The words name that place; a desktop keeps its sentence (null).
function micDeviceKind(){const nav=globalThis.navigator||{},agent=String(nav.userAgent||"");if(/iPad|iPhone|iPod/.test(agent)||(/Macintosh/.test(agent)&&Number(nav.maxTouchPoints)>1))return "ios";return /Android/.test(agent)?"android":"desktop";}
function homeScreenApp(){try{return globalThis.navigator?.standalone===true||globalThis.matchMedia?.("(display-mode: standalone)")?.matches===true;}catch(_){return false;}}
function micBlockedWords(error){
 const kind=micDeviceKind();
 if(kind==="ios")return homeScreenApp()?"마이크가 막혀 있어요. 설정 앱 → Safari → 마이크를 ‘허용’으로 바꾼 뒤 앱을 다시 열어 주세요.":"마이크가 막혀 있어요. 주소창의 페이지 메뉴(aA) → 웹사이트 설정 → 마이크를 ‘허용’으로 바꾼 뒤 새로 고쳐 주세요.";
 if(kind==="android")return /by system/i.test(String(error?.message||""))?"마이크가 막혀 있어요. 기기 설정 → 애플리케이션 → Chrome → 권한 → 마이크를 ‘허용’으로 바꿔 주세요.":"마이크가 막혀 있어요. 주소창 왼쪽 아이콘 → 권한 → 마이크를 ‘허용’으로 바꾼 뒤 새로 고쳐 주세요.";
 return null;
}
// A microphone refused or missing at the start is said in these words, not the browser's own (English) message.
const MIC_ERROR_NAMES=new Set(["NotAllowedError","SecurityError","NotFoundError","OverconstrainedError","NotReadableError","AbortError"]);
function micStartError(error){return MIC_ERROR_NAMES.has(error?.name)?new Error(micErrorText(error)):error;}
// "lost": the track ended or its device is gone. "switch": it works, but the
// chosen device is back and differs. null: nothing to do.
async function micReconnectNeed(){
 const track=currentMicTrack();if(!track||track.readyState!=="live")return "lost";
 let inputs;try{const listed=await navigator.mediaDevices?.enumerateDevices?.();if(!Array.isArray(listed))return null;inputs=listed.filter(item=>item.kind==="audioinput");}catch(_){return null;}
 // The track's device is still listed by id, or, for the "default" entry
 // whose id does not name the hardware, by its name.
 const current=track.getSettings?.().deviceId,label=track.label||"";
 if(!inputs.some(item=>(current&&item.deviceId===current&&!["default","communications"].includes(current))||(label&&item.label&&(item.label===label||item.label.endsWith(label)))))return "lost";
 const wanted=chosenMicDeviceId(inputs.filter(item=>item.deviceId&&!["default","communications"].includes(item.deviceId)));
 return wanted&&current&&wanted!==current?"switch":null;
}
function scheduleMicReconnect(reason){
 if(!micWorkletNode||micReconnectTimer||micReconnecting)return;
 const generation=sessionGeneration;
 micReconnectTimer=setTimeout(()=>{micReconnectTimer=null;void autoReconnectMic(reason,generation);},MIC_RECONNECT_DELAY_MS);
}
async function autoReconnectMic(reason,generation){
 if(generation!==sessionGeneration||!micWorkletNode||micReconnecting)return;
 const need=await micReconnectNeed();if(!need||generation!==sessionGeneration)return;
 if(Date.now()-micAutoReconnectAt<MIC_AUTO_RECONNECT_GAP_MS){if(need==="lost")showMicRecovery("마이크 연결이 다시 끊겼습니다. 마이크 연결을 확인한 뒤 다시 연결하세요.");return;}
 micAutoReconnectAt=Date.now();
 await reconnectMic(generation,reason);
}
async function reconnectMic(generation=sessionGeneration,reason="manual"){
 if(micReconnecting||!micWorkletNode||!micContext)return false;
 micReconnecting=true;setText("mic-device-status","마이크를 다시 연결하는 중…");
 const button=$("mic-reconnect");if(button)setDisabled(button,true);
 try{
  const {stream,audioConstraints}=await openMicStream();
  if(generation!==sessionGeneration||!micWorkletNode||!micContext){for(const track of stream.getTracks?.()||[])try{track.stop();}catch(_){}return false;}
  const previous=micStream,source=micContext.createMediaStreamSource(stream);
  if(micSource){try{micSource.disconnect();}catch(_){}}
  micStream=stream;micSource=source;micSource.connect(micWorkletNode);
  if(previous&&previous!==stream){try{for(const track of previous.getTracks())track.stop();}catch(_){}}
  watchMicTrack();reportMicSettings(stream,audioConstraints);hideMicRecovery();
  if(micContext.state!=="running")await micContext.resume();
  if(visibleState==="LISTENING")$("status").textContent="마이크를 다시 연결했습니다 · 듣고 있습니다…";
  announceMilestone("마이크를 다시 연결했습니다.");void renderMicDevices();
  return true;
 }catch(error){
  if(generation===sessionGeneration&&micWorkletNode)showMicRecovery(`${reason==="manual"?"":"마이크 연결이 끊겼고 다시 연결하지 못했습니다. "}${micErrorText(error)}`);
  return false;
 }finally{micReconnecting=false;}
}
async function ensureAudio(generation){
 if(!micWorkletNode){
  if(typeof AudioWorkletNode!=="function")throw new Error("이 브라우저는 AudioWorklet을 지원하지 않습니다.");
  const opened=await openMicStream();
  micStream=opened.stream;
  try{
   reportMicSettings(micStream,opened.audioConstraints);
   micContext=new AudioContext();
   if(!micContext.audioWorklet?.addModule)throw new Error("이 브라우저는 AudioWorklet을 지원하지 않습니다.");
   await micContext.audioWorklet.addModule("/mic-capture-worklet.js");
   if(generation!==sessionGeneration)throw new Error("마이크 시작이 취소되었습니다.");
   const targetRate=16000,downsample=createPcm16Downsampler(micContext.sampleRate,targetRate);
   micSource=micContext.createMediaStreamSource(micStream);
   micWorkletNode=new AudioWorkletNode(micContext,"voice-workflow-mic-capture",{numberOfInputs:1,numberOfOutputs:0,channelCount:1,channelCountMode:"explicit"});
   micWorkletNode.port.onmessage=e=>{
    if(generation!==sessionGeneration||!sessionActive||socket?.readyState!==WebSocket.OPEN||!(e.data instanceof ArrayBuffer))return;
    const f=new Float32Array(e.data);
    if(!micSignalSeen){
     let energy=0;for(let i=0;i<f.length;i++)energy+=f[i]*f[i];
     if(Math.sqrt(energy/f.length)>=.008){
      micSignalSeen=true;
      if(visibleState==="LISTENING")$("status").textContent="마이크 음성 신호 감지됨 · 듣고 있습니다…";
     }
    }
    const p=downsample(f);
    socket.send(p.buffer);
   };
   micSource.connect(micWorkletNode);
   micAutoReconnectAt=0;watchMicTrack();hideMicRecovery();void renderMicDevices();
  }catch(error){await closeAudio();throw error;}
 }
 playContext||=new AudioContext();
 watchAudioContext(micContext);watchAudioContext(playContext);
 if(!cascadeGain&&typeof playContext.createGain==="function"){cascadeGain=playContext.createGain();cascadeGain.gain.value=1;cascadeGain.connect(playContext.destination);}
 await resumeAudioWithin([micContext,playContext],AUDIO_RESUME_WAIT_MS);
 sendAudioReady();renderAudioUnlock();
}
// Lane TAB decision 3 (2026-10-23): a tablet's browser starts sound only from a
// touch and may stop it again (a call, the screen locked). The start press makes
// and resumes the playback context in the touch (primeRunStart); the session
// waits for the contexts at most AUDIO_RESUME_WAIT_MS, so a context still held
// back no longer stops it; the screen then asks for one touch, and any touch
// resumes what is held and only then tells the server the audio is ready -- once
// for each accepted start, as before.
var AUDIO_RESUME_WAIT_MS=1500;
let audioReadySentFor="";
const watchedAudioContexts=new WeakSet();
function watchAudioContext(context){
 if(!context||typeof context.addEventListener!=="function"||watchedAudioContexts.has(context))return;
 watchedAudioContexts.add(context);context.addEventListener("statechange",afterAudioResumed);
}
function resumeAudioWithin(contexts,ms){
 let timer=null;const waited=new Promise(resolve=>{timer=setTimeout(resolve,ms);});
 return Promise.race([Promise.all(contexts.map(context=>Promise.resolve().then(()=>context.resume()))),waited]).finally(()=>clearTimeout(timer));
}
function sendAudioReady(){
 const accepted=acceptedSessionConfiguration;
 if(!accepted||!micWorkletNode||playContext?.state!=="running")return false;
 const key=`${accepted.configuration_id}:${accepted.server_generation}`;if(audioReadySentFor===key)return true;audioReadySentFor=key;
 // Lane HEAR decisions 1 and 2 (2026-10-25): no voice measurement in a run; the microphone's short id tells the server whether the measured voice was this microphone's.
 sendControl("client.audio_ready",{configuration_id:accepted.configuration_id,generation:accepted.server_generation,audio_context_state:"running",sample_rate:playContext.sampleRate,mic_id:micIdFor(currentMicTrack())});
 return true;
}
function audioHeld(){return [micContext,playContext].some(context=>context&&(context.state==="suspended"||context.state==="interrupted"));}
function renderAudioUnlock(){const box=$("audio-unlock");if(!box)return false;const shown=Boolean(acceptedSessionConfiguration&&micWorkletNode)&&audioHeld();box.hidden=!shown;return shown;}
function afterAudioResumed(){sendAudioReady();renderAudioUnlock();}
function resumeHeldAudio(){
 for(const context of [micContext,playContext]){
  if(!context||typeof context.resume!=="function"||context.state==="running"||context.state==="closed")continue;
  try{Promise.resolve(context.resume()).then(afterAudioResumed,()=>{});}catch(_){}
 }
}
// The start press, in the touch itself: the sound made and resumed, and the screen kept on.
function primeRunStart(){
 try{if(!playContext&&typeof AudioContext==="function")playContext=new AudioContext();}catch(_){playContext=null;}
 watchAudioContext(playContext);resumeHeldAudio();void keepScreenOnForStart();
}
// A start that did not go: the sound made in its press rests (a suspended context costs nothing; the next press resumes it).
function restPrimedAudio(){
 if(sessionActive||micWorkletNode||playContext?.state!=="running"||typeof playContext.suspend!=="function")return false;
 try{Promise.resolve(playContext.suspend()).catch(()=>{});}catch(_){}
 return true;
}
// Any touch or key: the sound the browser holds back resumed, and a screen lock it let go during the run asked for again.
function onUserGesture(){resumeHeldAudio();if(screenWakeLockWanted&&!screenWakeLock)void keepScreenOn(true);}
function onAudioVisibilityChange(){if(globalThis.document?.visibilityState!=="visible")return false;resumeHeldAudio();return renderAudioUnlock();}
for(const kind of ["pointerup","touchend","click","keydown"])globalThis.document?.addEventListener?.(kind,onUserGesture,{capture:true,passive:true});
globalThis.document?.addEventListener?.("visibilitychange",onAudioVisibilityChange);
async function closeMicrophone(){
 if(micReconnectTimer){clearTimeout(micReconnectTimer);micReconnectTimer=null;}
 if(micTrackWatched){try{micTrackWatched.removeEventListener?.("ended",onMicTrackEnded);}catch(_){}micTrackWatched=null;}
 hideMicRecovery();setText("mic-device-status","");micReportedIdentity=null;
 if(micWorkletNode){
  try{micWorkletNode.port.onmessage=null;}catch(_){}
  try{micWorkletNode.disconnect();}catch(_){}
  micWorkletNode=null;
 }
 if(micSource){try{micSource.disconnect();}catch(_){}micSource=null;}
 if(micStream){try{for(const t of micStream.getTracks())t.stop();}catch(_){}micStream=null;}
 if(micContext){try{await micContext.close();}catch(_){}micContext=null;}
 renderAudioUnlock();
}
async function closeAudio(){
 await closeMicrophone();
 if(cascadeGain){try{cascadeGain.disconnect();}catch(_){}cascadeGain=null;}
 if(playContext){try{await playContext.close();}catch(_){}playContext=null;}
 renderAudioUnlock();
}

// Lane HEAR's microphone helpers (moved from index.html by lane RX2 so the new screen's 목소리 맞추기 records as the old one does):
// Lane HEAR decision 2 (2026-10-25): a short id for the microphone, so a measurement made on another one is not used.
function micIdFor(track){
 const id=String(track?.getSettings?.()?.deviceId||""),label=String(track?.label||"");if(!id&&!label)return null;
 let hash=0x811c9dc5;for(const char of `${id}|${label}`){hash^=char.codePointAt(0);hash=Math.imul(hash,0x01000193)>>>0;}
 return `mic-${hash.toString(16).padStart(8,"0")}`;
}
// Records the sentence on the microphone runs use (same device and processing; automatic gain off in
// "목소리를 맞추면 끔", as a filtered run has it), as 16 kHz PCM16 -- the browser half of the measurement.
async function recordCalibrationPcm(seconds){
 const opened=await openMicStream(),stream=opened.stream,track=stream.getAudioTracks?.()[0]||stream.getTracks?.()[0]||null;
 if(track&&agcMode()==="calibrated"&&typeof track.applyConstraints==="function"){try{await track.applyConstraints({autoGainControl:false});}catch(_){}}
 const context=new AudioContext(),chunks=[];
 try{
  await context.audioWorklet.addModule("/mic-capture-worklet.js");
  const source=context.createMediaStreamSource(stream),node=new AudioWorkletNode(context,"voice-workflow-mic-capture",{numberOfInputs:1,numberOfOutputs:0,channelCount:1,channelCountMode:"explicit"}),downsample=createPcm16Downsampler(context.sampleRate,16000);
  node.port.onmessage=e=>{if(e.data instanceof ArrayBuffer)chunks.push(downsample(new Float32Array(e.data)));};
  source.connect(node);await context.resume?.();
  await new Promise(resolve=>setTimeout(resolve,seconds*1000));
  node.port.postMessage({type:"stop"});source.disconnect?.();
 }finally{for(const item of stream.getTracks?.()||[])item.stop?.();try{await context.close?.();}catch(_){}}
 const total=chunks.reduce((sum,chunk)=>sum+chunk.length,0),pcm=new Int16Array(total);let at=0;for(const chunk of chunks){pcm.set(chunk,at);at+=chunk.length;}
 return {pcm,micId:micIdFor(track)};
}
function pcmBase64(pcm){const bytes=new Uint8Array(pcm.buffer,pcm.byteOffset,pcm.byteLength);let text="";for(let i=0;i<bytes.length;i+=0x8000)text+=String.fromCharCode(...bytes.subarray(i,i+0x8000));return btoa(text);}

// --- The answer's audio: interruption, fillers, the turn's fences -------------
function browserNowMs(){return globalThis.performance?.now?.()??Date.now();}
function clearFillerPlayback(turnId=null,generation=null){if(fillerIdentity&&(turnId!==null&&(fillerIdentity.turn_id!==turnId||fillerIdentity.generation!==generation)))return false;if(fillerSource){fillerSource.onended=null;try{fillerSource.stop();}catch(_){}try{fillerSource.disconnect();}catch(_){}fillerSource=null;}receivingFiller=null;fillerFrames=[];fillerIdentity=null;return true;}
function setCascadeGain(value){if(!cascadeGain)return;const target=Math.max(0,Math.min(1,value));try{cascadeGain.gain.setValueAtTime(target,playContext?.currentTime||0);}catch(_){cascadeGain.gain.value=target;}}
function resetCascadeInterruption(){cascadeInterruption={state:"idle",turn_id:null,generation:null,started_at:null};setCascadeGain(1);}
function provisionalCascadeInterruption(message){if(cascadeInterruption.state!=="idle"||message.turn_id!==activeTurn||message.generation!==activeCascadeGeneration)return false;cascadeInterruption={state:"candidate",turn_id:message.turn_id,generation:message.generation,started_at:browserNowMs()};setCascadeGain(.04);return true;}
function rejectCascadeInterruption(message){if(cascadeInterruption.state!=="candidate"||message.turn_id!==cascadeInterruption.turn_id||message.generation!==cascadeInterruption.generation)return false;resetCascadeInterruption();return true;}
function resetCascadePlayback(){if(activeSource){activeSource.onended=null;try{activeSource.stop();}catch(_){}try{activeSource.disconnect();}catch(_){}activeSource=null;}clearFillerPlayback();receiving=null;receivedFrames=[];queued.clear();activeTurn=null;activeCascadeGeneration=null;audioComplete=false;turnComplete=false;playing=false;nextSegment=0;resetCascadeInterruption();}
function clearPlayback(){playbackEpoch++;resetCascadePlayback();if(replaySource){replaySource.onended=null;try{replaySource.stop();}catch(_){}try{replaySource.disconnect();}catch(_){}replaySource=null;}}
function interruptCascadePlayback(turnId,generation){if(turnId!==activeTurn||!Number.isInteger(generation)||generation!==activeCascadeGeneration)return false;playbackEpoch++;resetCascadePlayback();return true;}
function cascadeTurnKey(turnId,generation){return `${generation}:${turnId}`;}
function cascadeTurnInterrupted(message){return Number.isInteger(message?.turn_id)&&Number.isInteger(message?.generation)&&interruptedCascadeTurns.has(cascadeTurnKey(message.turn_id,message.generation));}
function currentCascadeMessage(message){return message.turn_id===activeTurn&&(!Number.isInteger(message.generation)||activeCascadeGeneration===null||message.generation===activeCascadeGeneration);}

// --- The answer's audio: the queue, the replay, the tones ----------------------
function join(frames){const size=frames.reduce((n,x)=>n+x.byteLength,0),out=new Uint8Array(size);let at=0;for(const x of frames){out.set(new Uint8Array(x),at);at+=x.byteLength;}return out.buffer;}
async function playLastReplayableAudio(statusNode){if(!lastReplayableAudio||!playContext){if(statusNode)statusNode.textContent="재생 가능한 이전 답변이 없습니다. 브라우저 음성 출력과 볼륨을 확인해 주세요.";return false;}if(playing||activeSource||replaySource){if(statusNode)statusNode.textContent="다른 음성이 재생 중입니다. 끝난 뒤 한 번 더 눌러 주세요.";return false;}try{await playContext.resume();const input=new Int16Array(lastReplayableAudio.slice(0)),audio=playContext.createBuffer(1,input.length,16000),samples=audio.getChannelData(0);for(let i=0;i<input.length;i++)samples[i]=input[i]/32768;const source=playContext.createBufferSource();replaySource=source;source.buffer=audio;source.connect(playContext.destination);source.onended=()=>{if(replaySource===source)replaySource=null;if(statusNode)statusNode.textContent="답변을 한 번 다시 재생했습니다.";};source.start();if(statusNode)statusNode.textContent="마지막 답변을 다시 재생 중입니다.";return true;}catch(_){replaySource=null;if(statusNode)statusNode.textContent="재생하지 못했습니다. 브라우저의 오디오 권한과 출력 장치를 확인해 주세요.";return false;}}

// Lane FIXV decision 1: "다시 들려줘" replays the whole last turn, not its last sentence. Each piece a turn plays is
// kept after the ones before it; a new turn's first piece starts over; the turn that asked for the replay keeps nothing.
// A turn is its server ids on its socket: each connection is a new server session whose ids start again.
// The kept sound stops at 120 s of 16 kHz PCM16: a piece that would pass it is left out whole, and so is every piece after.
const REPLAY_AUDIO_MAX_BYTES=120*16000*2;
function keepReplayableAudio(key,item){
 if(key!==replayTurnKey||socket!==replayTurnSocket||!lastReplayableAudio){replayTurnKey=key;replayTurnSocket=socket;replayTurnFull=item.byteLength>REPLAY_AUDIO_MAX_BYTES;lastReplayableAudio=item.slice(0,REPLAY_AUDIO_MAX_BYTES);return;}
 if(replayTurnFull)return;
 if(lastReplayableAudio.byteLength+item.byteLength>REPLAY_AUDIO_MAX_BYTES){replayTurnFull=true;return;}
 lastReplayableAudio=join([lastReplayableAudio,item]);
}
async function pump(generation=sessionGeneration){
 if(generation!==sessionGeneration)return;
 if(playing)return;const item=queued.get(nextSegment);if(!item){if(audioComplete&&turnComplete&&activeTurn!==null&&queued.size===0){if(pendingVoiceReplay&&lastReplayableAudio){pendingVoiceReplay=false;queued.set(nextSegment,lastReplayableAudio.slice(0));pump(generation);return;}sendControl("playback.ended",{turn_id:activeTurn});clearPlayback();renderState("LISTENING");}return;}
 const epoch=playbackEpoch;queued.delete(nextSegment);playing=true;await playContext.resume();if(generation!==sessionGeneration||epoch!==playbackEpoch)return;const input=new Int16Array(item),audio=playContext.createBuffer(1,input.length,16000),samples=audio.getChannelData(0);for(let i=0;i<input.length;i++)samples[i]=input[i]/32768;
 const playedKey=cascadeTurnKey(activeTurn,activeCascadeGeneration);if(!audioRecoveryTurns.has(playedKey))keepReplayableAudio(playedKey,item);
 const source=playContext.createBufferSource();activeSource=source;source.buffer=audio;source.connect(cascadeGain||playContext.destination);source.onended=()=>{if(generation!==sessionGeneration||epoch!==playbackEpoch||activeSource!==source)return;activeSource=null;playing=false;nextSegment++;pump(generation);};source.start();if(generation===sessionGeneration&&epoch===playbackEpoch&&activeSource===source)renderState("SPEAKING");
}
async function playFiller(data,meta,generation=sessionGeneration){if(generation!==sessionGeneration||!currentCascadeMessage(meta)||cascadeTurnInterrupted(meta)||playing||activeSource||!sessionActive||!data.byteLength)return;clearFillerPlayback();const epoch=playbackEpoch,input=new Int16Array(data),audio=playContext.createBuffer(1,input.length,16000),samples=audio.getChannelData(0);for(let i=0;i<input.length;i++)samples[i]=input[i]/32768;await playContext.resume();if(generation!==sessionGeneration||epoch!==playbackEpoch||playing||activeSource)return;const source=playContext.createBufferSource();fillerSource=source;fillerIdentity={turn_id:meta.turn_id,generation:meta.generation};source.buffer=audio;source.connect(cascadeGain||playContext.destination);source.onended=()=>{if(fillerSource!==source)return;fillerSource=null;fillerIdentity=null;};source.start();}
// VOINEY_LAB_CASCADE_FILLER_MODE=tone: the waiting cue is one short, quiet tone made here.
// The server sends no audio for it and nothing is said.
const FILLER_TONE={frequency_hz:660,duration_s:.15,attack_s:.015,peak_gain:.06};
// Lane VT decision 1: the sound of a timer's end, made here; nothing is said by it.
const TIMER_NOTICE_TONE={frequency_hz:880,duration_s:.35,attack_s:.02,peak_gain:.12};
function playTimerNoticeTone(){if(!playContext||typeof playContext.createOscillator!=="function"||typeof playContext.createGain!=="function")return false;try{const start=playContext.currentTime||0,end=start+TIMER_NOTICE_TONE.duration_s,tone=playContext.createOscillator(),envelope=playContext.createGain();tone.type="sine";tone.frequency.setValueAtTime(TIMER_NOTICE_TONE.frequency_hz,start);envelope.gain.setValueAtTime(0,start);envelope.gain.linearRampToValueAtTime(TIMER_NOTICE_TONE.peak_gain,start+TIMER_NOTICE_TONE.attack_s);envelope.gain.linearRampToValueAtTime(0,end);tone.connect(envelope);envelope.connect(cascadeGain||playContext.destination);tone.onended=()=>{try{envelope.disconnect();}catch(_){}};tone.start(start);tone.stop(end);return true;}catch(_){return false;}}
async function playFillerTone(meta,generation=sessionGeneration){if(generation!==sessionGeneration||!currentCascadeMessage(meta)||cascadeTurnInterrupted(meta)||playing||activeSource||!sessionActive||!playContext||typeof playContext.createOscillator!=="function"||typeof playContext.createGain!=="function")return false;clearFillerPlayback();const epoch=playbackEpoch;await playContext.resume();if(generation!==sessionGeneration||epoch!==playbackEpoch||playing||activeSource)return false;const start=playContext.currentTime||0,end=start+FILLER_TONE.duration_s,tone=playContext.createOscillator(),envelope=playContext.createGain();tone.type="sine";tone.frequency.setValueAtTime(FILLER_TONE.frequency_hz,start);envelope.gain.setValueAtTime(0,start);envelope.gain.linearRampToValueAtTime(FILLER_TONE.peak_gain,start+FILLER_TONE.attack_s);envelope.gain.linearRampToValueAtTime(0,end);tone.connect(envelope);envelope.connect(cascadeGain||playContext.destination);fillerSource=tone;fillerIdentity={turn_id:meta.turn_id,generation:meta.generation};tone.onended=()=>{try{envelope.disconnect();}catch(_){}if(fillerSource!==tone)return;fillerSource=null;fillerIdentity=null;};tone.start(start);tone.stop(end);return true;}
