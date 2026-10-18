// Lane NET (2026-10-18): a lost connection, seen at once and put back.
//
// The server is the only authority over the run and its timers (AGENTS.md
// rule 2); the page receives them over the socket. This file is the page's
// half of keeping that true when the socket goes: it sees the loss within
// three seconds -- the socket closing or failing, the browser going
// offline, or the server's heartbeat stopping (decision 1).
//
// It runs beside the page's own script and uses its names (socket,
// sessionActive, acceptedSessionConfiguration, ...); the page calls
// voineyConnection.watch() for every socket it opens.
"use strict";
(()=>{
// The page's "client.ping" goes this often; the server's heartbeat comes as often.
const PING_EVERY_MS=1000;
// Nothing at all received for this long while a session runs: lost. Looked at
// every CHECK_EVERY_MS, so the loss is seen within three seconds.
const SILENT_LOST_MS=2800,CHECK_EVERY_MS=200;

const net={socket:null,heardAt:0,pingSeq:0,pingHandle:null,checkHandle:null};

function runActive(){
 return sessionActive===true&&acceptedSessionConfiguration!==null&&net.socket!==null&&socket===net.socket;
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

function checkSilence(){if(runActive()&&Date.now()-net.heardAt>SILENT_LOST_MS)lost("server_silent");}

// The loss itself. The socket is let go at once: nothing more is read from
// it or sent on it, and the page's own close handling runs as for a close.
function lost(reason){
 const current=net.socket;
 if(!current||socket!==current)return false;
 net.socket=null;
 try{current.onclose?.({code:4000,reason});}catch(_){}
 socket=null;
 try{if(current.readyState<WebSocket.CLOSING)current.close(4000,"connection lost");}catch(_){}
 return true;
}

globalThis.addEventListener?.("offline",()=>{if(runActive())lost("browser_offline");});

globalThis.voineyConnection=Object.freeze({watch,lost});
})();
