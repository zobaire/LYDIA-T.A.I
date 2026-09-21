// === script block 0 ===

const $ = id => document.getElementById(id);
let ws;
let streamEl=null;   // the in-progress streaming assistant bubble
let remembered=[];
let lastRoutines=[], lastNotes='';   // cached from the state event so memory/import bubbles don't wipe the graph
let reasonEl=null;   // the in-progress streaming "thought" bubble (one per turn)

function escapeHtml(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}

/* ---------- 2D graph ---------- */
let G=null, nodeMap=new Map(), labelDivs=new Map(), firstData=true;
let graphW=0, graphH=0, graphCtx=null, graphAnim=0, graphActive=false;

function initGraph(){
  const host=$('graph');
  host.innerHTML='';
  const cv=document.createElement('canvas');
  host.appendChild(cv);
  graphCtx=cv.getContext('2d');
  function size(){
    // Scale the backing store by devicePixelRatio so neon glow stays crisp
    // on hi-DPI displays. Drawing coordinates stay CSS pixels thanks to the
    // ctx transform, so stepGraph/hitTest don't need to change.
    const dpr=window.devicePixelRatio||1;
    graphW=window.innerWidth;
    graphH=window.innerHeight;
    cv.width=Math.round(graphW*dpr);
    cv.height=Math.round(graphH*dpr);
    graphCtx.setTransform(dpr,0,0,dpr,0,0);
  }
  size();
  window.addEventListener('resize', size);
  cv.addEventListener('mousemove', e=>{
    const n=hitTest(e.clientX, e.clientY);
    cv.style.cursor = n ? 'pointer' : 'default';
  });
  cv.addEventListener('click', e=>{
    const n=hitTest(e.clientX, e.clientY);
    if(n){ showDetail(n); } else { $('detail').classList.remove('show'); }
  });
  graphActive=true;
  requestAnimationFrame(graphLoop);
}

function graphLoop(){
  if(!graphActive) return;
  stepGraph();
  drawGraph();
  graphAnim=requestAnimationFrame(graphLoop);
}

function stepGraph(){
  const nodes=[...nodeMap.values()];
  if(!nodes.length) return;
  // center on LYDIA
  const core=nodeMap.get('lydia');
  if(core && (core.x==null)){ core.x=graphW/2; core.y=graphH/2; }
  // repulsion — much weaker so nodes drift apart slowly
  for(let i=0;i<nodes.length;i++)for(let j=i+1;j<nodes.length;j++){
    const a=nodes[i], b=nodes[j];
    if(a.x==null||b.x==null) continue;
    let dx=a.x-b.x, dy=a.y-b.y;
    let d=Math.hypot(dx,dy)||1;
    const f=400/(d*d);            // was 900
    dx/=d; dy/=d;
    a.vx=(a.vx||0)+dx*f*0.15;     // was 0.5
    a.vy=(a.vy||0)+dy*f*0.15;
    b.vx=(b.vx||0)-dx*f*0.15;
    b.vy=(b.vy||0)-dy*f*0.15;
  }
  // springs toward LYDIA / hubs — gentler pull
  for(const n of nodes){
    if(n.id==='lydia'||n.x==null) continue;
    const target = n.id==='__routines'||n.id==='__memory' ? core : nodeMap.get(n.id.startsWith('r_')?'__routines':n.id.startsWith('n_')||n.id.startsWith('mem_')?'__memory':null);
    if(target && target.x!=null){
      const dx=target.x-n.x, dy=target.y-n.y;
      n.vx=(n.vx||0)+dx*0.003;     // was 0.008
      n.vy=(n.vy||0)+dy*0.003;
    }
  }
  for(const n of nodes){
    if(n.x==null) continue;
    n.vx=(n.vx||0)*0.94;           // was 0.92 — more damping, bleeds speed faster
    n.vy=(n.vy||0)*0.94;
    n.x+=n.vx*0.6;                 // was full velocity — slower movement
    n.y+=n.vy*0.6;
    // keep on screen
    const r=n.size*4+30;
    if(n.x<r)n.x=r; if(n.x>graphW-r)n.x=graphW-r;
    if(n.y<r)n.y=r; if(n.y>graphH-r)n.y=graphH-r;
  }
}

function drawGraph(){
  const ctx=graphCtx; if(!ctx) return;
  ctx.clearRect(0,0,graphW,graphH);
  const nodes=[...nodeMap.values()];
  // links — drawn from the stored graphLinks, no per-frame id pattern guessing
  ctx.lineWidth=1;
  ctx.strokeStyle='rgba(210,210,220,0.32)';
  ctx.shadowColor='rgba(210,210,220,0.55)';
  ctx.shadowBlur=4;
  for(const l of graphLinks){
    const a=nodeMap.get(l.source), b=nodeMap.get(l.target);
    if(!a||!b||a.x==null||b.x==null) continue;
    ctx.beginPath(); ctx.moveTo(a.x,a.y); ctx.lineTo(b.x,b.y); ctx.stroke();
  }
  ctx.shadowBlur=0;
  // nodes — neon glow
  for(const n of nodes){
    if(n.x==null) continue;
    const r=n.size*2.2;
    ctx.beginPath();
    ctx.arc(n.x,n.y,r,0,Math.PI*2);
    ctx.fillStyle=n.color||'#d8d8e0';
    ctx.shadowColor=n.color||'#d8d8e0';
    ctx.shadowBlur=n.type==='core'?30:18;
    ctx.globalAlpha=0.95; ctx.fill(); ctx.globalAlpha=1;
    ctx.shadowBlur=0;
    ctx.strokeStyle='rgba(255,255,255,0.2)'; ctx.lineWidth=1; ctx.stroke();
  }
  // core pulse ring
  const core=nodeMap.get('lydia');
  if(core&&core.x!=null){
    const pr=core.size*2.2+7+Math.sin(Date.now()/350)*3.5;
    ctx.beginPath();
    ctx.arc(core.x,core.y,pr,0,Math.PI*2);
    ctx.strokeStyle='rgba(235,235,242,0.4)';
    ctx.shadowColor='rgba(235,235,242,0.9)';
    ctx.shadowBlur=14;
    ctx.lineWidth=1.5;
    ctx.stroke();
    ctx.shadowBlur=0;
  }
  // labels
  for(const n of nodes){
    if(n.x==null) continue;
    ctx.font='11px "JetBrains Mono", monospace';
    if(n.type==='core'){ctx.fillStyle='#f5f5f8';ctx.shadowColor='#f5f5f8';ctx.shadowBlur=10;}
    else{ctx.fillStyle='rgba(178,178,188,0.92)';ctx.shadowBlur=0;}
    ctx.textAlign='center';
    ctx.fillText(n.label, n.x, n.y+n.size*2.2+14);
    ctx.shadowBlur=0;
  }
}

function buildDesired(routines, notesText, remembered){
  const nodes=[{id:'lydia',label:'LYDIA T.A.I',color:'#d8d8e0',size:9,type:'core',lc:'core',content:'Your assistant, at the center of everything.'}];
  const links=[];
  nodes.push({id:'__routines',label:'Routines',color:'#e8e8ef',size:5,type:'hub',lc:'hubR',content:'Learned routines.'});
  links.push({source:'lydia',target:'__routines'});
  (routines||[]).forEach((r,i)=>{
    const id='r_'+i;
    nodes.push({id,label:r.name,color:'#e8e8ef',size:3.2,type:'routine',lc:'routine',content:`Trigger: "${r.trigger}"\n\n${r.steps}`});
    links.push({source:'__routines',target:id});
  });
  nodes.push({id:'__memory',label:'Memory',color:'#d8d8e0',size:5,type:'hub',lc:'hubM',content:'Things Lydia remembers.'});
  links.push({source:'lydia',target:'__memory'});
  const notes=(notesText||'').split('\n').map(s=>s.replace(/^-\s*/,'').trim()).filter(Boolean).slice(-24);
  notes.forEach((n,i)=>{
    const id='n_'+i;
    nodes.push({id,label:n.length>34?n.slice(0,34)+'…':n,color:'#d8d8e0',size:2.4,type:'note',lc:'note',content:n});
    links.push({source:'__memory',target:id});
  });
  // "Remember" bubbles — each saved memory is a 2D bubble.
  (remembered||[]).forEach((m,i)=>{
    const txt=(m.text||'').trim();
    if(!txt) return;
    const id='mem_'+i;
    nodes.push({id,label:txt.length>34?txt.slice(0,34)+'…':txt,color:'#e8e8ef',size:3.4,type:'memory',lc:'note',content:txt});
    links.push({source:'__memory',target:id});
  });
  return {nodes,links};
}

let graphLinks=[];
function updateGraph(routines, notesText, remembered){
  const d=buildDesired(routines, notesText, remembered);
  // store links once instead of re-deriving them from id patterns every frame
  graphLinks=d.links.map(l=>({
    source:(typeof l.source==='object'?l.source.id:l.source),
    target:(typeof l.target==='object'?l.target.id:l.target)
  }));
  const merged=d.nodes.map(n=>{
    const ex=nodeMap.get(n.id);
    if(ex){ Object.assign(ex,n); return ex; }
    // scatter new nodes near the center
    n.x=(graphW||window.innerWidth)/2+(Math.random()-0.5)*300;
    n.y=(graphH||window.innerHeight)/2+(Math.random()-0.5)*300;
    n.vx=(Math.random()-0.5)*2; n.vy=(Math.random()-0.5)*2;
    return n;
  });
  // cap the sim so the O(n²) force loop stays smooth with lots of memories
  nodeMap=new Map(merged.slice(0,120).map(n=>[n.id,n]));
}

function pulseCore(mode){
  const core=nodeMap.get('lydia'); if(!core) return;
  core.size = mode==='idle'?9:12;
  core.color = mode==='talking'?'#d8d8e0':mode==='listening'?'#e8e8ef':mode==='thinking'?'#d8d8e0':'#d8d8e0';
}

function hitTest(mx,my){
  const nodes=[...nodeMap.values()];
  for(const n of nodes){
    if(n.x==null) continue;
    const r=Math.max(n.size*2.2, 7);
    const dx=mx-n.x, dy=my-n.y;
    if(dx*dx+dy*dy <= (r+6)*(r+6)) return n;
  }
  return null;
}

function showDetail(node){
  const el=$('detail');
  el.querySelector('.dtitle').textContent=node.label;
  el.querySelector('.dbadge').textContent=node.type;
  el.querySelector('.dbody').textContent=node.content||'';
  el.classList.add('show');
}

/* ---------- websocket ---------- */
function connect(){
  const proto = location.protocol==='https:' ? 'wss:' : 'ws:';
  // Ask the server for the WS token first (loopback-only endpoint). When the
  // UI is bound to 127.0.0.1 this returns it freely; when exposed to the LAN
  // it only answers requests from this machine, so remote pages can't drive
  // the assistant.
  fetch('/api/token').then(r=>r.json()).then(d=>{
    const tok=(d&&d.token)?'?token='+encodeURIComponent(d.token):'';
    ws=new WebSocket(`${proto}//${location.host}/ws${tok}`);
    ws.onopen=()=>setStatus('connected');
    ws.onclose=()=>{setStatus('disconnected — retrying');setTimeout(connect,1500);};
    ws.onmessage=e=>{try{handle(JSON.parse(e.data));}catch(err){console.warn('bad ws frame:',e.data,err);}};
  }).catch(()=>{setStatus('offline — token refused');setTimeout(connect,2500);});
}
let curListening=true;
function setStatus(text){
  $('status').textContent=text;
  const orb=$('orbwrap'),mic=$('mic'),stop=$('stop');
  orb.className='';mic.classList.remove('rec');stop.classList.remove('hot');
  let mode='idle';
  if(/listen/i.test(text)){orb.classList.add('listening');mic.classList.add('rec');mode='listening';}
  else if(/deep/i.test(text)){orb.classList.add('deep');mode='thinking';}
  else if(/think/i.test(text)){orb.classList.add('thinking');mode='thinking';}
  else if(/talk/i.test(text)){orb.classList.add('talking');stop.classList.add('hot');mode='talking';}
  if(!curListening) orb.classList.add('deaf');
  pulseCore(mode);
}
function add(parent,el){
  const pinned = parent.scrollTop + parent.clientHeight >= parent.scrollHeight - 8;
  parent.appendChild(el);
  if(pinned) parent.scrollTop=parent.scrollHeight;
  while(parent.children.length>200)parent.removeChild(parent.firstChild);
}
function bubble(cls,text){const d=document.createElement('div');d.className=cls;d.textContent=text;return d;}
function copyMsg(m){
  if(navigator.clipboard && navigator.clipboard.writeText){
    navigator.clipboard.writeText(m.dataset.raw||'').catch(()=>{});
  }
  m.classList.add('copied');
  setTimeout(()=>m.classList.remove('copied'), 500);
}
function addCopyBtn(d){
  const c=document.createElement('button');
  c.type='button';
  c.className='copybtn';
  c.title='Copy';
  c.innerHTML='<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>';
  c.onclick=e=>{e.stopPropagation();copyMsg(d);};
  d.appendChild(c);
  return d;
}
function chatBubble(cls,text){
  const d=bubble(cls,text);
  d.dataset.raw=text;
  const t=document.createElement('span');
  t.className='mtime';
  t.textContent=new Date().toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
  d.appendChild(t);
  addCopyBtn(d);
  return d;
}
function handle(evt){
  switch(evt.type){
    case 'user': streamEl=null; reasonEl=null; add($('chat'),chatBubble('msg user',evt.text)); break;
    case 'assistant_delta': {
      // Live streaming of the final answer — append tokens to the current bubble.
      $('typingbar').classList.remove('show');
      if(!streamEl || !streamEl.isConnected){
        streamEl=chatBubble('msg assistant','');
        streamEl.dataset.streaming='1';
        add($('chat'),streamEl);
        const ts=streamEl.querySelector('.mtime');
        if(ts) ts.remove(); // timestamp goes back on once the stream finishes
      }
      streamEl.textContent+=(evt.text||'');
      // only auto-scroll if the user is already at the bottom
      const chat=$('chat');
      if(chat.scrollTop + chat.clientHeight >= chat.scrollHeight - 8){
        chat.scrollTop=chat.scrollHeight;
      }
      break;
    }
    case 'assistant':
      // Final full text. If we were streaming into a bubble, finalize it in
      // place (no duplicate); otherwise render a fresh one.
      $('typingbar').classList.remove('show');
      reasonEl=null; // this turn's reasoning stream is done — future deltas open a fresh bubble
      if(streamEl && streamEl.isConnected){
        streamEl.dataset.raw=evt.text;
        streamEl.textContent=evt.text;
        delete streamEl.dataset.streaming; // reveal the copy button now that it's done
        const t=document.createElement('span');
        t.className='mtime';
        t.textContent=new Date().toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
        streamEl.appendChild(t);
      } else {
        add($('chat'),chatBubble('msg assistant',evt.text));
      }
      streamEl=null;
      break;
    case 'status': setStatus(evt.text); break;
    case 'thought': {
      // Complete (non-streaming) thought — show as a static bubble.
      add($('thoughtscroll'), bubble('thought', evt.text||''));
      break;
    }
    case 'thought_delta': {
      // Streaming reasoning — ONE bubble per turn so it stays readable.
      const sc=$('thoughtscroll');
      if(!reasonEl || !reasonEl.isConnected){
        reasonEl=bubble('thought deepthink','');
        add(sc, reasonEl);
      }
      reasonEl.textContent+=evt.text||'';
      // only auto-scroll when the user is already at the bottom, so you can
      // scroll up to read earlier reasoning while the stream is still going
      if(sc.scrollTop + sc.clientHeight >= sc.scrollHeight - 8){
        sc.scrollTop=sc.scrollHeight;
      }
      break;
    }
    case 'tool':{const el=bubble('tool',`→ ${evt.name}(${JSON.stringify(evt.args)})`);el.dataset.tool=evt.name;add($('thoughtscroll'),el);break;}
    case 'tool_result':{const t=$('thoughtscroll').querySelectorAll('.tool');const last=t[t.length-1];
      if(last&&last.dataset.tool===evt.name&&!last.querySelector('.res')){const r=document.createElement('span');r.className='res';r.textContent=evt.text;last.appendChild(r);}break;}
    case 'state':
      remembered=evt.remembered||[];
      lastRoutines=evt.routines||[];
      lastNotes=evt.notes||'';
      updateGraph(lastRoutines, lastNotes, remembered);
      renderMemory(remembered); break;
    case 'memory':
      // A new "remember" bubble just landed — add it live, but pass the
      // cached routine/note lists so updateGraph doesn't rebuild the graph
      // WITHOUT them (that wiped every routine/note bubble until reload).
      remembered.push({text:evt.text});
      updateGraph(lastRoutines, lastNotes, remembered);
      renderMemory(remembered);
      break;
    case 'lang': curLang=evt.lang; $('lang').textContent=(LANG_LABEL[evt.lang]||'EN');
      $('lang').title='Speaking: '+(evt.name||evt.lang)+' — click to switch'; break;
    case 'effort': setEffort(evt.tier); break;
    case 'mute': curMuted=evt.muted; $('mute').innerHTML=evt.muted?'&#128263;':'&#128266;';
      $('mute').classList.toggle('on',evt.muted);
      $('mute').title=evt.muted?'Voice muted — click to unmute':'Mute voice'; break;
    case 'listen': curListening=evt.listening; $('listenmute').classList.toggle('on',!evt.listening);
      $('listenmute').title=evt.listening?'Stop Lydia listening':'Not listening — click to resume';
      $('orbwrap').classList.toggle('deaf',!evt.listening); break;
    case 'battery': updateBattery(evt); break;
    case 'provider':
      glowProvider(evt.name);
      if(evt.name){setModel(evt.name);}
      break;
    case 'model_msg':
      showToast(evt.text||'Model updated.');
      break;
    case 'schedule': renderSchedule(evt.events); break;
    case 'schedule_msg': $('schedule-msg').textContent=evt.text; break;
    case 'reminder':
      $('schedule-msg').textContent='⏰ '+evt.text;
      add($('chat'),chatBubble('msg assistant','⏰ '+evt.text));
      break;
  }
}
let curLang='en';
const LANG_ORDER=['en','ar','zh'];
const LANG_LABEL={en:'EN',ar:'ع',zh:'中文'};

function fmtK(n){if(n==null)return '?';return n>=1000?(n/1000).toFixed(n>=10000?0:1)+'k':(''+n);}
function curSym(c){return c==='CNY'?'¥':c==='USD'?'$':(c?c+' ':'');}
function buildLEDs(shell,pct){
  shell.innerHTML='';
  const total=12;
  const lit=pct==null?0:Math.round(Math.max(0,Math.min(100,pct))/100*total);
  const tier=pct==null?'':pct>50?'fill-hi':pct>=20?'fill-mid':'fill-low';
  for(let i=0;i<total;i++){
    const d=document.createElement('div');
    d.className='bled'+(i<lit?' '+tier:'');
    shell.appendChild(d);
  }
}
function setBatt(el,pct,pctText,sub){
  const shell=el.querySelector('.bshell'),p=el.querySelector('.bpct'),s=el.querySelector('.bsub');
  buildLEDs(shell,pct);
  p.textContent=pct==null?'—':pctText;
  if(sub!=null)s.textContent=sub;
}
function updateBattery(evt){
  const d=evt.deepseek;
  if(d)setBatt($('batt-deepseek'),d.pct,curSym(d.currency)+Number(d.amount).toFixed(2),d.available===false?'unavailable':'balance');
  else setBatt($('batt-deepseek'),null,null,'offline');
}
let glowTimer=null;
function glowProvider(name){
  const id=name==='deepseek'?'batt-deepseek':null;
  if(!id)return;
  document.querySelectorAll('.batt').forEach(b=>b.classList.remove('active'));
  $(id).classList.add('active');
  clearTimeout(glowTimer);
  glowTimer=setTimeout(()=>$(id).classList.remove('active'),4500);
}

/* ---------- model switching + toast notification ---------- */
let curModel='';
function setModel(name){
  curModel=name||'';
  document.querySelectorAll('#model .mbtn').forEach(b=>{
    const on=b.dataset.model===name;
    b.classList.toggle('on',on);
    b.style.opacity=on?1:0.55;
  });
}
let toastTimer=null;
function showToast(text,title){
  const t=$('toast');
  if(!t)return;
  t.querySelector('.t-title').textContent=title||'MODEL';
  t.querySelector('.t-body').innerHTML=text;
  t.style.display='flex';
  t.classList.remove('hide');
  clearTimeout(toastTimer);
  toastTimer=setTimeout(hideToast,3500);
}
function hideToast(){
  const t=$('toast');
  if(!t)return;
  t.classList.add('hide');
  setTimeout(()=>{t.style.display='none';t.classList.remove('hide');},200);
}
document.querySelectorAll('#model .mbtn').forEach(b=>{
  b.onclick=()=>{if(ws&&ws.readyState===1)ws.send(JSON.stringify({type:'switch_model',model:b.dataset.model}));};
});

/* ---------- sending: offline feedback + typing indicator instead of silent drops ---------- */
function sendMessage(){
  const t=$('box').value.trim();
  if(!t) return;
  if(!ws||ws.readyState!==1){
    shakeBox('Not connected to the server — message not sent');
    return;
  }
  ws.send(JSON.stringify({type:'user_text',text:t}));
  history.push(t); histIdx=history.length;
  $('box').value='';
  $('typingbar').classList.add('show');
}
function shakeBox(msg){
  const b=$('box');
  b.classList.remove('shake');
  void b.offsetWidth; // restart the animation
  b.classList.add('shake');
  if(msg) showToast(msg,'OFFLINE');
}
$('composer').onsubmit=e=>{e.preventDefault();sendMessage();};

/* ---------- file attach → /api/import ---------- */
async function uploadFiles(files){
  if(!files.length) return;
  const names=files.map(f=>f.name);
  add($('chat'),chatBubble('msg user','📎 Attached:\n'+names.join('\n')));
  const fd=new FormData();
  files.forEach(f=>fd.append('files',f));
  try{
    const r=await fetch('/api/import',{method:'POST',body:fd});
    const j=await r.json();
    const errs=j.errors||[];
    if(errs.length){
      add($('chat'),chatBubble('msg assistant','⚠️ Could not save: '+errs.join('; ')));
    }else{
      const n=j.saved&&j.saved.length?j.saved.length:names.length;
      add($('chat'),chatBubble('msg assistant','Saved '+n+' file'+(n===1?'':'s')+' to imports/ — full paths are in my memory so I can read them now.'));
    }
  }catch(err){
    add($('chat'),chatBubble('msg assistant','⚠️ Upload failed: '+(err.message||err)));
  }
}
$('fileinput').addEventListener('change', async e=>{
  const files=[...e.target.files];
  e.target.value='';
  uploadFiles(files);
});

/* ---------- drag & drop files anywhere onto the page ---------- */
let dragDepth=0;
window.addEventListener('dragenter', e=>{e.preventDefault();dragDepth++;});
window.addEventListener('dragleave', e=>{e.preventDefault();dragDepth--;});
window.addEventListener('dragover', e=>{e.preventDefault();});
window.addEventListener('drop', e=>{
  e.preventDefault();
  dragDepth=0;
  const files=[...(e.dataTransfer&&e.dataTransfer.files||[])];
  uploadFiles(files);
});

$('mic').onclick=()=>{if(ws&&ws.readyState===1)ws.send(JSON.stringify({type:'wake'}));};
$('stop').onclick=()=>{if(ws&&ws.readyState===1)ws.send(JSON.stringify({type:'stop'}));};
$('lang').onclick=()=>{if(ws&&ws.readyState===1){const i=LANG_ORDER.indexOf(curLang);ws.send(JSON.stringify({type:'set_lang',lang:LANG_ORDER[(i+1)%LANG_ORDER.length]}));}};

function setEffort(tier){document.querySelectorAll('#effort .ebtn').forEach(b=>b.classList.toggle('on',b.dataset.tier===tier));}
document.querySelectorAll('#effort .ebtn').forEach(b=>{b.onclick=()=>{if(ws&&ws.readyState===1)ws.send(JSON.stringify({type:'set_effort',tier:b.dataset.tier}));};});
let curMuted=false;
$('mute').onclick=()=>{if(ws&&ws.readyState===1)ws.send(JSON.stringify({type:'set_mute',muted:!curMuted}));};
$('listenmute').onclick=()=>{if(ws&&ws.readyState===1)ws.send(JSON.stringify({type:'set_listen',listening:!curListening}));};

/* ---------- focus / zen: hide the brain panels (memory · thoughts · plans) ---------- */
let focusMode=true;
try{ if(localStorage.getItem('lydia.focus.v1')==='0') focusMode=false; }catch(e){}
function setFocus(on){
  focusMode=on;
  document.body.classList.toggle('focus',on);
  $('zen').classList.toggle('on',on);
  $('zen').title=on?'Focus — clean chat view (click to show memory · thoughts · plans)':'Show brain panels';
  try{ localStorage.setItem('lydia.focus.v1', on?'1':'0'); }catch(e){}
}
$('zen').onclick=()=>setFocus(!focusMode);
setFocus(focusMode);

/* ---------- Coding Mode dock (toggle + lazy CodeMirror + file tree) ---------- */
let codingOpen=false;
let codingFull=false;   // dock expanded to fullscreen IDE
let chatPeek=false;     // chat overlay open while coding fullscreen
let codeDockLoaded=false, codeDockLoading=null;

function loadCodeDock(){
  // CodeMirror + ~15 CDN modules (~1MB) only load the FIRST time the dock
  // opens — no point paying that on every page load when Coding Mode is
  // additive and on-demand.
  if(codeDockLoaded) return Promise.resolve(window.CodeDock);
  if(codeDockLoading) return codeDockLoading;
  codeDockLoading=import('/codedock.mjs')
    .then(()=>{codeDockLoaded=true; return window.CodeDock;})
    .catch(err=>{codeDockLoading=null; console.warn('codedock load failed:',err); return null;});
  return codeDockLoading;
}

const ICO_EXPAND='<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3H5a2 2 0 0 0-2 2v3"/><path d="M21 8V5a2 2 0 0 0-2-2h-3"/><path d="M3 16v3a2 2 0 0 0 2 2h3"/><path d="M16 21h3a2 2 0 0 0 2-2v-3"/></svg>';
const ICO_COMPRESS='<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3v3a2 2 0 0 1-2 2H3"/><path d="M21 8h-3a2 2 0 0 1-2-2V3"/><path d="M3 16h3a2 2 0 0 1 2 2v3"/><path d="M16 21v-3a2 2 0 0 1 2-2h3"/></svg>';

function setCodingFull(full){
  codingFull=full;
  $('codedock').classList.toggle('full',full);
  document.body.classList.toggle('code-full',full);
  if(!full) setChatPeek(false);
  $('cdfull').innerHTML = full?ICO_COMPRESS:ICO_EXPAND;
  $('cdfull').title = full?'Show docked panel (Esc)':'Fullscreen coding (whole screen)';
  if(codingOpen && window.CodeDock) window.CodeDock.onDockToggle(true);
}

function setChatPeek(open){
  chatPeek=open;
  $('chatwrap').classList.toggle('chat-peek',open);
  if(open) setTimeout(()=>{ if(chatPeek) $('box').focus(); },60);
}

function setCoding(open){
  if(!open && window.CodeDock && window.CodeDock.hasDirty()){
    if(!confirm('You have unsaved changes in Coding Mode. Close anyway?')) return;
  }
  codingOpen=open;
  $('codedock').classList.toggle('open',open);
  $('codebtn').classList.toggle('on',open);
  $('codebtn').title=open?'Close Coding Mode (Ctrl+B)':'Open Coding Mode (Ctrl+B)';
  document.body.classList.toggle('code-open',open);
  if(open){
    setCodingFull(true); // coding mode opens as the whole-screen IDE
    ensureTreeLoaded();
    loadCodeDock().then(dock=>{ if(dock && codingOpen) dock.onDockToggle(true); });
  } else {
    setCodingFull(false); // clears body.code-full + chat peek
  }
}
$('codebtn').onclick=()=>setCoding(!codingOpen);
$('cdclose').onclick=()=>setCoding(false);
$('cdfull').onclick=()=>setCodingFull(!codingFull);
$('chatpill').onclick=()=>setChatPeek(true);
$('chatx').onclick=()=>setChatPeek(false);
document.addEventListener('keydown', e=>{
  if((e.ctrlKey||e.metaKey) && e.key.toLowerCase()==='b'){
    e.preventDefault();
    setCoding(!codingOpen);
  } else if(e.key==='Escape'){
    if(codingOpen && codingFull && chatPeek){ setChatPeek(false); }      // chat overlay → back to pure IDE
    else if(codingOpen && codingFull){ setCodingFull(false); }           // fullscreen → docked panel
    else if(codingOpen){ setCoding(false); }                             // docked → back to chat
  }
});

/* ---------- Coding Mode: file tree (lazy one-level fetch per expand) ---------- */
let treeRootLoaded=false;
let pendingOpen=null;   // file clicked before CodeDock finished lazy-loading

async function ensureTreeLoaded(){
  if(treeRootLoaded) return;
  treeRootLoaded=true;
  await fetchTreeInto($('cdtree'), '');
}

async function fetchTreeInto(host, path){
  host.innerHTML='<div class="cd-empty">loading…</div>';
  try{
    const r=await fetch('/api/code/tree?path='+encodeURIComponent(path));
    const j=await r.json();
    if(!r.ok||!j.ok) throw new Error(j.error||('HTTP '+r.status));
    host.innerHTML='';
    (j.dirs||[]).forEach(d=>host.appendChild(dirRow(d.path, d.name)));
    (j.files||[]).forEach(f=>host.appendChild(fileRow(f.path, f.name)));
    if(!j.dirs.length && !j.files.length){
      const e=document.createElement('div');
      e.className='cd-empty';
      e.textContent='(empty)';
      host.appendChild(e);
    }
  }catch(err){
    host.innerHTML='<div class="cd-empty">'+escapeHtml(String((err&&err.message)||err))+'</div>';
  }
}

function dirRow(path, name){
  const row=document.createElement('div');
  row.className='cdt-row cdt-dir';
  row.title=path;
  row.innerHTML='<span class="cdt-ic">▸</span><span class="cdt-nm">'+escapeHtml(name)+'</span>';
  let childHost=null;
  row.onclick=async ()=>{
    if(childHost){
      row.querySelector('.cdt-ic').textContent='▸';
      childHost.remove();
      childHost=null;
      return;
    }
    row.querySelector('.cdt-ic').textContent='▾';
    childHost=document.createElement('div');
    childHost.className='cdt-children';
    row.after(childHost);
    childHost.innerHTML='<div class="cd-empty">loading…</div>';
    try{
      const r=await fetch('/api/code/tree?path='+encodeURIComponent(path));
      const j=await r.json();
      if(!r.ok||!j.ok) throw new Error(j.error||('HTTP '+r.status));
      childHost.innerHTML='';
      (j.dirs||[]).forEach(d=>childHost.appendChild(dirRow(d.path, d.name)));
      (j.files||[]).forEach(f=>childHost.appendChild(fileRow(f.path, f.name)));
      if(!j.dirs.length && !j.files.length){
        const e=document.createElement('div');
        e.className='cd-empty';
        e.textContent='(empty)';
        childHost.appendChild(e);
      }
    }catch(err){
      childHost.innerHTML='<div class="cd-empty">'+escapeHtml(String((err&&err.message)||err))+'</div>';
    }
  };
  return row;
}

function fileRow(path, name){
  const row=document.createElement('div');
  row.className='cdt-row';
  row.title=path;
  row.innerHTML='<span class="cdt-ic">⧉</span><span class="cdt-nm">'+escapeHtml(name)+'</span>';
  row.onclick=()=>openTreeFile(path, name);
  return row;
}

async function openTreeFile(path, name){
  try{
    const r=await fetch('/api/code/read',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path})});
    const j=await r.json();
    if(!r.ok||!j.ok) throw new Error(j.error||('HTTP '+r.status));
    const dock=window.CodeDock;
    if(!dock){
      // dock still lazy-loading — stash and open once it's ready
      pendingOpen={name:name||path.split(/[\\/]/).pop(), path:path, content:j.content};
      loadCodeDock().then(d=>{ if(d && pendingOpen){ d.openFile(pendingOpen); if(codingOpen) d.onDockToggle(true); pendingOpen=null; } });
      return;
    }
    dock.openFile({name:name||path.split(/[\\/]/).pop(), path:path, content:j.content});
  }catch(err){
    if(typeof showToast==='function') showToast('Could not open '+(name||path)+': '+((err&&err.message)||err), 'CODE');
  }
}

window.TreeAPI={
  refresh(){ treeRootLoaded=false; ensureTreeLoaded(); }
};

/* ---------- Memory info ---------- */
let memFull=[];
function renderMemory(list){
  memFull=list||[];
  const host=$('mevents');
  host.innerHTML='';
  const q=($('mfilter').value||'').toLowerCase().trim();
  const mems=q?memFull.filter(m=>(m.text||'').toLowerCase().includes(q)):memFull;
  $('mcount').textContent=q?(mems.length+'/'+memFull.length):mems.length;
  if(!mems.length){
    const el=document.createElement('div');
    el.id='mempty';
    el.textContent=q?'No memories match "'+q+'".':'Nothing remembered yet. Tell Lydia something to remember.';
    host.appendChild(el);
    return;
  }
  mems.forEach(m=>{
    const d=document.createElement('div');
    d.className='mev';
    const t=document.createElement('div');t.className='mtext';t.textContent=m.text||'';
    const tm=document.createElement('div');tm.className='mtime';
    if(m.ts){
      const ts=Date.parse(m.ts);
      if(!isNaN(ts))tm.textContent=new Date(ts).toLocaleString([],{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});
    }
    d.appendChild(t);d.appendChild(tm);
    host.appendChild(d);
  });
}
$('mfilter').addEventListener('input', ()=>renderMemory(memFull));

/* ---------- Schedule / Plans ---------- */
let sEvents=[];
function renderSchedule(list){
  sEvents=list||[];
  const host=$('sevents');
  host.innerHTML='';
  if(!sEvents.length){
    const el=document.createElement('div');
    el.id='sempty';
    el.textContent='No plans yet. Click + to add one, or just tell Lydia in chat.';
    host.appendChild(el);
    return;
  }
  const now=new Date();
  sEvents.forEach(ev=>{
    const d=document.createElement('div');
    d.className='sev';
    let dateStr=ev.date||'';
    let soon=false;
    const ts=Date.parse(dateStr.replace(' ', 'T'));
    if(!isNaN(ts)){
      const diff=ts-now.getTime();
      if(diff>=0 && diff < 3*3600*1000) soon=true;
      dateStr=new Date(ts).toLocaleString([],{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});
    }
    if(soon)d.classList.add('soon');
    const t=document.createElement('div');t.className='stitle';t.textContent=ev.title;
    const dt=document.createElement('div');dt.className='sdate';dt.textContent=dateStr;
    const acts=document.createElement('div');acts.className='sacts';
    const ed=document.createElement('button');ed.textContent='Edit';
    ed.onclick=()=>openScheduleForm(ev);
    const del=document.createElement('button');del.className='sdel';del.textContent='Cancel';
    del.onclick=()=>{if(ws&&ws.readyState===1)ws.send(JSON.stringify({type:'schedule_delete',id:ev.id}));};
    acts.appendChild(ed);acts.appendChild(del);
    d.appendChild(t);d.appendChild(dt);d.appendChild(acts);
    host.appendChild(d);
  });
}
function openScheduleForm(ev){
  const f=$('smform');
  f.style.display='flex';
  $('smtitle').value=ev?ev.title:'';
  const dval=ev?ev.date.replace(' ', 'T'):'';
  $('smdate').value=dval;
  f.dataset.id=ev?ev.id:'';
  f.scrollIntoView({block:'nearest'});
}
function hideScheduleForm(){
  $('smform').style.display='none';
  $('smtitle').value='';
  $('smdate').value='';
  delete $('smform').dataset.id;
}
function saveSchedule(){
  if(!ws||ws.readyState!==1)return;
  const title=$('smtitle').value.trim();
  const raw=$('smdate').value;
  if(!title){$('schedule-msg').textContent='Enter a title.';return;}
  if(!raw){$('schedule-msg').textContent='Pick a date & time.';return;}
  const date=raw.replace('T', ' ')+':00';
  const id=$('smform').dataset.id;
  ws.send(JSON.stringify(id?{type:'schedule_update',id,title,date}:{type:'schedule_add',title,date}));
  hideScheduleForm();
}
$('ssave').onclick=saveSchedule;
$('smtitle').addEventListener('keydown',e=>{if(e.key==='Enter')saveSchedule();});

/* ---------- chat: copy, clear, history ---------- */
/* Copy is handled by the per-message hover button now — no accidental copies. */
$('clear').onclick=()=>{ $('chat').innerHTML=''; try{localStorage.removeItem('lydia.chat.v1');}catch(e){} };

/* ---------- chat persistence (survives refresh) ---------- */
function persistChat(){
  try{
    const data=[...$('chat').querySelectorAll('.msg')]
      .filter(m=>m.dataset.raw!==undefined)
      .map(m=>({
        cls:m.className.replace(/\s*copied/,''),
        raw:m.dataset.raw||'',
        time:((m.querySelector('.mtime'))||{}).textContent||''
      }));
    localStorage.setItem('lydia.chat.v1', JSON.stringify(data));
  }catch(e){}
}
let persistTimer=null;
new MutationObserver(()=>{
  clearTimeout(persistTimer);
  persistTimer=setTimeout(persistChat,500);
}).observe($('chat'),{childList:true,subtree:true,characterData:true});
function restoreChat(){
  try{
    const data=JSON.parse(localStorage.getItem('lydia.chat.v1')||'[]');
    if(!Array.isArray(data)||!data.length) return;
    const frag=document.createDocumentFragment();
    data.forEach(m=>{
      const d=document.createElement('div');
      d.className=m.cls||'msg assistant';
      d.dataset.raw=m.raw||'';
      d.textContent=m.raw||'';
      const t=document.createElement('span');
      t.className='mtime';
      t.textContent=m.time||'';
      d.appendChild(t);
      addCopyBtn(d);
      frag.appendChild(d);
    });
    $('chat').appendChild(frag);
    $('chat').scrollTop=$('chat').scrollHeight;
  }catch(e){}
}
let history=[], histIdx=0;
$('box').addEventListener('keydown', e=>{
  if(e.key==='Enter' && (e.isComposing || e.keyCode===229)){
    // IME composition confirm — don't send half-typed text
    e.preventDefault();
    return;
  }
  if(e.key==='ArrowUp'){
    e.preventDefault();
    if(histIdx>0){ histIdx--; $('box').value=history[histIdx]||''; }
  } else if(e.key==='ArrowDown'){
    e.preventDefault();
    if(histIdx<history.length){ histIdx++; $('box').value=history[histIdx]||''; }
  } else if(e.key==='Escape'){
    $('detail').classList.remove('show');
  }
});
/* Ctrl+K focuses the input from anywhere (pops the chat open if coding is fullscreen) */
document.addEventListener('keydown', e=>{
  if((e.ctrlKey||e.metaKey) && e.key.toLowerCase()==='k'){
    e.preventDefault();
    if(codingOpen && codingFull && !chatPeek) setChatPeek(true);
    setTimeout(()=>$('box').focus(),40);
  }
});

/* ---------- collapsible panels ---------- */
function makeCollapsible(panel, body){
  const h=panel.querySelector('h2');
  if(!h) return;
  const btn=document.createElement('button');
  btn.type='button';
  btn.className='ptoggle';
  btn.textContent='—';
  btn.title='Collapse';
  h.appendChild(btn);
  let open=true;
  btn.onclick=()=>{
    open=!open;
    body.style.display=open?'':'none';
    btn.textContent=open?'—':'+';
  };
}
makeCollapsible($('thoughts'), $('thoughtscroll'));
makeCollapsible($('schedule-panel'), $('sevents'));
makeCollapsible($('memory-panel'), $('mevents'));

/* ---------- draggable panels: memory / plans / thoughts ---------- */
const DRAG_PANELS=['thoughts','schedule-panel','memory-panel'];
function savePanelPositions(){
  try{
    const data={};
    DRAG_PANELS.forEach(id=>{
      const el=$(id);
      if(!el) return;
      data[id]={left:el.style.left, top:el.style.top};
    });
    localStorage.setItem('lydia.panels.v1', JSON.stringify(data));
  }catch(e){}
}
function restorePanelPositions(){
  try{
    const data=JSON.parse(localStorage.getItem('lydia.panels.v1')||'{}');
    DRAG_PANELS.forEach(id=>{
      const el=$(id), p=data[id];
      if(!el||!p) return;
      if(p.left) el.style.left=p.left;
      if(p.top) el.style.top=p.top;
      el.style.right='auto';
      el.style.bottom='auto';
    });
  }catch(e){}
}
function makeDraggable(panel, handleSel){
  const handle=panel.querySelector(handleSel);
  if(!handle) return;
  handle.classList.add('draggable');
  let dragging=false, sx=0, sy=0, ox=0, oy=0;
  handle.addEventListener('mousedown', e=>{
    // don't hijack clicks on buttons / inputs inside the header (+ add, collapse, filter…)
    if(e.target.closest('button,input,a,label')) return;
    e.preventDefault();
    const r=panel.getBoundingClientRect();
    // switch from CSS right/bottom anchoring to explicit left/top so drag math is simple
    panel.style.left=r.left+'px';
    panel.style.top=r.top+'px';
    panel.style.right='auto';
    panel.style.bottom='auto';
    sx=e.clientX; sy=e.clientY; ox=r.left; oy=r.top;
    dragging=true;
    panel.classList.add('dragging');
    document.body.classList.add('drag-active');
  });
  window.addEventListener('mousemove', e=>{
    if(!dragging) return;
    const w=panel.offsetWidth, h=panel.offsetHeight;
    let nx=ox+(e.clientX-sx), ny=oy+(e.clientY-sy);
    // keep it reachable — never fully off-screen
    nx=Math.max(-w+80, Math.min(window.innerWidth-40, nx));
    ny=Math.max(8, Math.min(window.innerHeight-60, ny));
    panel.style.left=nx+'px';
    panel.style.top=ny+'px';
  });
  window.addEventListener('mouseup', ()=>{
    if(!dragging) return;
    dragging=false;
    panel.classList.remove('dragging');
    document.body.classList.remove('drag-active');
    savePanelPositions();
  });
}
makeDraggable($('thoughts'), 'h2');
makeDraggable($('schedule-panel'), '.shead');
makeDraggable($('memory-panel'), '.mhead');

/* ---------- clock ---------- */
function tickClock(){ const el=$('clock'); if(el) el.textContent=new Date().toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit'}); }
tickClock();
setInterval(tickClock, 1000);

restorePanelPositions();
initGraph();
updateGraph([], '');
restoreChat();
connect();

/* ---------- Trading: ticker + deck (Phase 2A -- read-only) ---------- */
let tradeOpen=false;
const lastTickPrices={};   // base -> last bid, for up/down direction
let MK_SYMBOLS=['EURUSD','GBPUSD','XAUUSD','BTCUSD','US30','USDJPY'];  // dynamic watchlist (synced from backend)
const tickCells=new Map();

function fmtPrice(v){
  if(v==null) return '\u2014';
  if(v>=1000) return v.toFixed(1);
  if(v>=100) return v.toFixed(2);
  if(v>=1) return v.toFixed(4);
  return v.toFixed(5);
}
function fmtMoney(v,cur){
  if(v==null) return '\u2014';
  const c=cur==='USD'?'$':(cur||'');
  return (v<0?'-':'')+c+Math.abs(v).toFixed(2);
}

function renderTicker(snap){
  const host=$('ticker-track'); if(!host) return;
  const ticks=(snap&&snap.ticks)||{};
  for(const base of Array.from(tickCells.keys())){ if(!MK_SYMBOLS.includes(base)){ const c=tickCells.get(base); if(c)c.remove(); tickCells.delete(base); } }
  for(const base of MK_SYMBOLS){
    const t=ticks[base]||null;
    let cell=tickCells.get(base);
    if(!cell){
      cell=document.createElement('div');
      cell.className='tick';
      cell.innerHTML='<span class="tsym">'+escapeHtml(base)+'</span><span class="tpx">\u2014</span>';
      cell.title='Select '+base;
      cell.onclick=()=>selectMarket(base);
      host.appendChild(cell);
      tickCells.set(base,cell);
    }
    cell.classList.toggle('active', base===activeMarket);
    const pxEl=cell.querySelector('.tpx');
    const bid=t?t.bid:null;
    const prev=lastTickPrices[base];
    if(prev!=null && bid!=null && bid!==prev){
      cell.classList.remove('tup','tdown');
      cell.classList.add(bid>prev?'tup':'tdown');
    }
    lastTickPrices[base]=bid;
    pxEl.textContent = (bid!=null)?fmtPrice(bid):'\u2014';
    cell.classList.toggle('tstale', !!(t && t.age_s!=null && t.age_s>3600));
    cell.classList.toggle('tclosed', !!(t && t.closed));
  }
}

function renderAccount(a){
  lastAccount=a||null;
  const eq=$('td-equity'); if(!eq) return;
  eq.textContent = (a.equity!=null)?fmtMoney(a.equity,a.currency):'\u2014';
  eq.classList.toggle('pos', a.profit>0);
  eq.classList.toggle('neg', a.profit<0);
  const rows=[
    ['BALANCE', fmtMoney(a.balance,a.currency)],
    ['PROFIT', ((a.profit>=0?'+':'')+fmtMoney(a.profit,a.currency))],
    ['FREE MARGIN', fmtMoney(a.margin_free,a.currency)],
    ['LEVERAGE', '1:'+(a.leverage||'\u2014')],
    ['MARGIN LVL', (a.margin_level!=null?a.margin_level.toFixed(0)+'%':'\u2014')],
  ];
  const g=$('td-stats'); if(!g) return;
  g.innerHTML='';
  rows.forEach(([k,v])=>{
    const r=document.createElement('div');
    r.className='td-stat';
    r.innerHTML='<span class="tdk">'+escapeHtml(k)+'</span><span class="tdv">'+escapeHtml(v)+'</span>';
    g.appendChild(r);
  });
}

function renderPositions(positions){
  const host=$('td-positions'); if(!host) return;
  const flat=$('td-flat');
  if(!positions || !positions.length){
    host.innerHTML='<div class="td-empty">No open positions.</div>';
    if(flat) flat.disabled=true;
    return;
  }
  host.innerHTML='';
  if(flat) flat.disabled=false;
  positions.forEach(p=>{
    const buy=p.type===0;
    const row=document.createElement('div');
    row.className='td-pos '+(buy?'buy':'sell');
    const sltp=[];
    if(p.sl) sltp.push('<span>SL '+fmtPrice(p.sl)+'</span>');
    if(p.tp) sltp.push('<span>TP '+fmtPrice(p.tp)+'</span>');
    row.innerHTML=''
      +'<div class="td-pos-top">'
      +'<span class="td-pos-sym">'+escapeHtml(p.symbol)+'</span>'
      +'<span class="td-pos-side">'+(buy?'BUY':'SELL')+'</span>'
      +'<span class="td-pos-pnl '+(p.profit>=0?'pos':'neg')+'">'+((p.profit>=0?'+':'')+p.profit.toFixed(2))+'</span>'
      +'<button type="button" class="td-pos-edit" title="Edit SL/TP for #'+p.ticket+'">SL/TP</button>'
      +'<button type="button" class="td-pos-x" title="Close #'+p.ticket+'">\u00d7</button>'
      +'</div>'
      +'<div class="td-pos-sub">'
      +'<span>'+p.volume+' lots</span><span>@ '+fmtPrice(p.price_open)+'</span>'+sltp.join('')
      +'</div>'
      +'<div class="td-pos-editform">'
      +'<div class="td-field"><label>SL</label><input type="number" step="any" class="td-pos-edit-sl" value="'+(p.sl||'')+'"></div>'
      +'<div class="td-field"><label>TP</label><input type="number" step="any" class="td-pos-edit-tp" value="'+(p.tp||'')+'"></div>'
      +'<button type="button" class="td-pos-save">SAVE</button>'
      +'<button type="button" class="td-pos-cancel">CANCEL</button>'
      +'</div>';
    host.appendChild(row);
    row.querySelector('.td-pos-x').onclick=()=>closePos(p.ticket);
    const form=row.querySelector('.td-pos-editform');
    row.querySelector('.td-pos-edit').onclick=()=>{ form.classList.toggle('open'); };
    form.querySelector('.td-pos-cancel').onclick=()=>{ form.classList.remove('open'); };
    form.querySelector('.td-pos-save').onclick=()=>modifySlTp(p.ticket, form.querySelector('.td-pos-edit-sl').value, form.querySelector('.td-pos-edit-tp').value, form);
  });
}

function fmtCountdown(ts){
  const d=Math.max(0, (ts - Date.now()/1000));
  if(d<=0) return 'now';
  const m=Math.floor(d/60), h=Math.floor(m/60);
  if(h>=48) return Math.floor(h/24)+'d';
  if(h>=1) return h+'h '+(m%60)+'m';
  if(m>=1) return m+'m';
  return Math.floor(d)+'s';
}

function renderNews(events){
  const host=$('td-news'); if(!host) return;
  if(!events || !events.length){
    host.innerHTML='<div class="td-empty">No upcoming events. Markets closed this weekend.</div>';
    return;
  }
  host.innerHTML='';
  events.slice(0,12).forEach(e=>{
    const row=document.createElement('div');
    row.className='td-news-row impact-'+(e.impact||'Low').toLowerCase();
    row.innerHTML='<div class="td-news-top">'
      +'<span class="td-news-ccy">'+escapeHtml(e.country)+'</span>'
      +'<span class="td-news-title" title="'+escapeHtml(e.title)+'">'+escapeHtml(e.title)+'</span>'
      +'<span class="td-news-when">'+fmtCountdown(e.ts)+'</span>'
      +'</div>';
    host.appendChild(row);
  });
}

async function pollTrading(){
  try{
    const r=await fetch('/api/trading/snapshot');
    const s=await r.json();
    syncWatchlist(s.watchlist);
    renderTicker(s);
    updateMkLive(s);
    maybeMoveInsight(s.ticks||{});
    if(tradeOpen){
      if(s.account){
        renderAccount(s.account);
        const st=$('td-status');
        if(st){ st.textContent = s.connected?'LIVE':'OFFLINE'; st.classList.toggle('offline', !s.connected); }
        const mt=$('td-meta');
        if(mt) mt.textContent = s.account.server+' \u00b7 #'+s.account.login;
      } else {
        const st=$('td-status'); if(st){ st.textContent='OFFLINE'; st.classList.add('offline'); }
        const mt=$('td-meta'); if(mt) mt.textContent = s.error||'';
      }
      renderPositions(s.positions||[]);
    }
  }catch(e){}
}
async function pollNews(){
  try{
    const r=await fetch('/api/trading/news');
    const j=await r.json();
    renderNews(j.events||[]);
    maybeNewsInsight(j.events||[]);
  }catch(e){}
}

function setTrade(open){
  tradeOpen=open;
  $('tradedock').classList.toggle('open',open);
  $('tradebtn').classList.toggle('on',open);
  $('tradebtn').title=open?'Close Trading Deck (Ctrl+T)':'Open Trading Deck (Ctrl+T)';
  document.body.classList.toggle('trade-open',open);
  if(open){ pollTrading(); pollNews(); }
}
$('tradebtn').onclick=()=>setTrade(!tradeOpen);
$('tdclose').onclick=()=>setTrade(false);
$('tdrefresh').onclick=()=>{ pollTrading(); pollNews(); };
document.addEventListener('keydown', e=>{
  if((e.ctrlKey||e.metaKey) && e.key.toLowerCase()==='t'){
    e.preventDefault();
    setTrade(!tradeOpen);
  }
});

/* ---------- dynamic watchlist + add/remove picker ---------- */
function syncWatchlist(wl){
  if(!Array.isArray(wl) || !wl.length) return;
  const next=wl.map(s=>String(s).toUpperCase());
  const same = next.length===MK_SYMBOLS.length && next.every((s,i)=>s===MK_SYMBOLS[i]);
  if(same) return;
  MK_SYMBOLS=next;
  if(!MK_SYMBOLS.includes(activeMarket)){
    activeMarket=MK_SYMBOLS[0];
    persistMarket();
    const el=$('mk-symbol'); if(el) el.textContent=activeMarket;
    loadCandles();
  }
  renderMkChips();
}

const watchPickerEl=$('watch-picker'), watchInput=$('wp-input'), watchList=$('wp-list');
let watchTimer=null;
function openWatchPicker(){
  if(watchPickerEl.classList.contains('open')){ closeWatchPicker(); return; }
  watchPickerEl.classList.add('open');
  watchInput.value='';
  watchList.innerHTML='<div class="wp-empty">Type to search - e.g. ETH, GOLD, NAS</div>';
  setTimeout(()=>watchInput.focus(), 10);
}
function closeWatchPicker(){ watchPickerEl.classList.remove('open'); }
function watchSymbol(base){
  fetch('/api/trading/watch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({symbol:base})})
    .then(r=>r.json())
    .then(j=>{ if(j&&j.ok){ postInsight('Added '+base); pollTrading(); } else { postInsight('Add failed: '+(j&&j.error||'?')); } })
    .catch(()=>{ postInsight('Add failed (network)'); });
}
function unwatchSymbol(base){
  fetch('/api/trading/unwatch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({symbol:base})})
    .then(r=>r.json())
    .then(j=>{ if(j&&j.ok){ postInsight('Removed '+base); pollTrading(); } })
    .catch(()=>{});
}
function renderWatchResults(symbols){
  if(!symbols || !symbols.length){ watchList.innerHTML='<div class="wp-empty">No matches.</div>'; return; }
  watchList.innerHTML='';
  symbols.forEach(s=>{
    const row=document.createElement('div');
    row.className='wp-row';
    const added = MK_SYMBOLS.includes(s.base);
    row.innerHTML='<span class="wp-sym">'+escapeHtml(s.base)+'</span>'
      +'<span class="wp-desc">'+escapeHtml(s.desc||s.name||'')+'</span>'
      +(added?'<span class="wp-badge">ADDED</span>':'');
    if(!added){
      row.classList.add('wp-add');
      row.onclick=()=>{ watchSymbol(s.base); row.classList.remove('wp-add'); const b=document.createElement('span'); b.className='wp-badge'; b.textContent='ADDED'; row.appendChild(b); };
    }
    watchList.appendChild(row);
  });
}
if(watchInput){
  watchInput.addEventListener('input', ()=>{
    const q=watchInput.value.trim();
    clearTimeout(watchTimer);
    if(!q){ watchList.innerHTML='<div class="wp-empty">Type to search ...</div>'; return; }
    watchTimer=setTimeout(()=>{
      fetch('/api/trading/symbols?q='+encodeURIComponent(q))
        .then(r=>r.json())
        .then(j=>renderWatchResults(j.symbols||[]))
        .catch(()=>{ watchList.innerHTML='<div class="wp-empty">Search unavailable.</div>'; });
    }, 180);
  });
  watchInput.addEventListener('keydown', e=>{ if(e.key==='Escape') closeWatchPicker(); });
}
if($('wp-close')) $('wp-close').onclick=closeWatchPicker;
if($('watch-add')) $('watch-add').onclick=openWatchPicker;
document.addEventListener('click', e=>{
  if(watchPickerEl.classList.contains('open') && !watchPickerEl.contains(e.target) && e.target!==$('watch-add')){
    closeWatchPicker();
  }
});

/* ---------- Settings (extensible; persisted) ---------- */
const SETTINGS_DEFS = [
  { id:'alertsActiveOnly', label:'Alerts: active market only', desc:'Only show movement alerts for the market you are currently viewing. Turn off to see alerts for every market.', type:'toggle', default:true },
];
let settings = {};
function loadSettings(){
  settings = {};
  SETTINGS_DEFS.forEach(d=>{ settings[d.id] = d.default; });
  try{
    const saved = JSON.parse(localStorage.getItem('taia.settings.v1')||'{}');
    SETTINGS_DEFS.forEach(d=>{ if(typeof saved[d.id] === typeof d.default) settings[d.id] = saved[d.id]; });
  }catch(e){}
}
function saveSettings(){ try{ localStorage.setItem('taia.settings.v1', JSON.stringify(settings)); }catch(e){} }
function setting(id){ return (settings[id]!=null) ? settings[id] : (SETTINGS_DEFS.find(d=>d.id===id)||{}).default; }
function renderSettings(){
  const host=$('st-rows'); if(!host) return;
  host.innerHTML='';
  SETTINGS_DEFS.forEach(d=>{
    const row=document.createElement('div');
    row.className='st-row';
    row.innerHTML='<div class="st-info"><div class="st-label">'+escapeHtml(d.label)+'</div><div class="st-desc">'+escapeHtml(d.desc)+'</div></div>';
    if(d.type==='toggle'){
      const sw=document.createElement('button');
      sw.type='button';
      sw.className='st-switch'+(settings[d.id]?' on':'');
      sw.title='Toggle';
      sw.onclick=()=>{ settings[d.id]=!settings[d.id]; saveSettings(); sw.classList.toggle('on', settings[d.id]); onSettingChanged(d.id, settings[d.id]); };
      row.appendChild(sw);
    }
    host.appendChild(row);
  });
}
function onSettingChanged(id, val){
  if(id==='alertsActiveOnly' && !val){ postInsight('Alerts now show for all markets.'); }
}
if($('settingsbtn')) $('settingsbtn').onclick=()=>{ renderSettings(); $('settings').classList.add('show'); };
if($('st-close')) $('st-close').onclick=()=>$('settings').classList.remove('show');
if($('settings')) $('settings').addEventListener('click', e=>{ if(e.target===$('settings')) $('settings').classList.remove('show'); });

/* ---------- Market Workspace: interactive market selection ---------- */
/* MK_SYMBOLS now lives at the top of the trading block (dynamic watchlist) */
const MK_TFS = ['M1','M5','M15','M30','H1','H4','D1'];
let activeMarket = 'EURUSD';
let activeTF = 'M5';
let candleData = [];
let lastBid = null;
let lastEquityHist = [];
const dayOpen = {};
const lastInsightPrice = {};
const postedNews = new Set();
const insightLog = [];

function persistMarket(){ try{ localStorage.setItem('taia.market.v1', JSON.stringify({sym:activeMarket,tf:activeTF})); }catch(e){} }
function restoreMarket(){ try{ const d=JSON.parse(localStorage.getItem('taia.market.v1')||'null'); if(d&&d.sym&&typeof d.sym==='string') activeMarket=d.sym.toUpperCase(); if(d&&d.tf&&MK_TFS.includes(d.tf)) activeTF=d.tf; }catch(e){} }

function postInsight(text){
  insightLog.unshift({t:new Date(), text});
  if(insightLog.length>20) insightLog.length=20;
  renderInsights();
}
function renderInsights(){
  const host=$('mk-ins-list'); if(!host) return;
  if(!insightLog.length){ host.innerHTML='<div class="mk-ins-empty">Lydia will note market moves here.</div>'; return; }
  host.innerHTML='';
  insightLog.forEach(i=>{
    const d=document.createElement('div');
    d.className='mk-ins-item';
    d.innerHTML='<span class="mk-ins-time">'+i.t.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'})+'</span>'+escapeHtml(i.text);
    host.appendChild(d);
  });
}

function selectMarket(sym){
  if(!MK_SYMBOLS.includes(sym)) return;
  if(sym!==activeMarket){
    activeMarket=sym; lastBid=null;
    persistMarket();
    renderMkChips();
    updateTickerActive();
    $('mk-symbol').textContent=sym;
    loadCandles();
    postInsight('Focus '+sym);
    renderOrderTicket();
  }
}
function setTF(tf){
  if(!MK_TFS.includes(tf)) return;
  activeTF=tf; persistMarket(); renderMkTfs(); loadCandles();
}
function renderMkChips(){
  const host=$('mk-chips'); if(!host) return;
  host.innerHTML='';
  MK_SYMBOLS.forEach(sym=>{
    const b=document.createElement('button');
    b.type='button'; b.className='mk-chip'+(sym===activeMarket?' on':'');
    b.textContent=sym;
    b.onclick=()=>selectMarket(sym);
    host.appendChild(b);
  });
  const add=document.createElement('button');
  add.type='button'; add.className='mk-chip mk-chip-add'; add.textContent='+';
  add.title='Add a market';
  add.onclick=openWatchPicker;
  host.appendChild(add);
}
function renderMkTfs(){
  document.querySelectorAll('#mk-tfs .mk-tf').forEach(b=>b.classList.toggle('on', b.dataset.tf===activeTF));
}
function updateTickerActive(){
  tickCells.forEach((cell,base)=>cell.classList.toggle('active', base===activeMarket));
}
document.querySelectorAll('#mk-tfs .mk-tf').forEach(b=>b.onclick=()=>setTF(b.dataset.tf));

/* --- candlestick chart --- */
async function loadCandles(){
  try{
    const r=await fetch('/api/trading/candles?symbol='+encodeURIComponent(activeMarket)+'&tf='+encodeURIComponent(activeTF)+'&count=120');
    const j=await r.json();
    if(j.ok && j.candles && j.candles.length){
      candleData=j.candles;
      $('mk-chart-empty').style.display='none';
    } else {
      candleData=[];
      const ce=$('mk-chart-empty');
      ce.textContent = 'No data for '+activeMarket+' \u2014 '+(j.error||'markets closed / MT5 offline');
      ce.style.display='flex';
    }
    drawCandles();
  }catch(e){
    candleData=[];
    const ce=$('mk-chart-empty');
    ce.textContent='Chart unavailable: '+(e.message||e);
    ce.style.display='flex';
  }
  fetchTrades();
}
function drawCandles(){
  const cv=$('mk-chart'); if(!cv) return;
  const ctx=cv.getContext('2d');
  const dpr=window.devicePixelRatio||1;
  const w=cv.clientWidth||0, h=cv.clientHeight||0;
  if(!w||!h) return;
  cv.width=Math.round(w*dpr); cv.height=Math.round(h*dpr);
  ctx.setTransform(dpr,0,0,dpr,0,0);
  ctx.clearRect(0,0,w,h);
  ctx.strokeStyle='rgba(210,210,220,0.06)'; ctx.lineWidth=1;
  for(let i=1;i<6;i++){ const y=h*i/6; ctx.beginPath(); ctx.moveTo(0,y); ctx.lineTo(w,y); ctx.stroke(); }
  for(let i=1;i<12;i++){ const x=w*i/12; ctx.beginPath(); ctx.moveTo(x,0); ctx.lineTo(x,h); ctx.stroke(); }
  if(!candleData.length) return;
  const data=candleData.map(c=>({t:c.t,o:c.o,h:c.h,l:c.l,c:c.c}));
  if(lastBid!=null && data.length){
    const lc=data[data.length-1];
    lc.c=lastBid; if(lastBid>lc.h) lc.h=lastBid; if(lastBid<lc.l) lc.l=lastBid;
  }
  let lo=Infinity, hi=-Infinity;
  data.forEach(c=>{ if(c.l<lo)lo=c.l; if(c.h>hi)hi=c.h; });
  if(lo===Infinity){ lo=0; hi=1; }
  const pad=(hi-lo)*0.08||1; lo-=pad; hi+=pad;
  const Y=v=>h-((v-lo)/(hi-lo))*h;
  const n=data.length, slot=w/n, bw=Math.max(1,slot*0.62);
  data.forEach((c,i)=>{
    const x=i*slot+slot/2;
    const up=c.c>=c.o, col=up?'#3ddc84':'#ff5252';
    ctx.strokeStyle=col; ctx.shadowColor=col; ctx.shadowBlur=3;
    ctx.beginPath(); ctx.moveTo(x,Y(c.h)); ctx.lineTo(x,Y(c.l)); ctx.stroke();
    ctx.shadowBlur=0;
    const top=Math.min(Y(c.o),Y(c.c)), bot=Math.max(Y(c.o),Y(c.c));
    ctx.fillStyle=col;
    ctx.fillRect(x-bw/2, top, bw, Math.max(1,bot-top));
  });
  const last=data[data.length-1], lp=Y(last.c);
  ctx.strokeStyle='rgba(245,245,248,0.5)'; ctx.setLineDash([4,4]);
  ctx.beginPath(); ctx.moveTo(0,lp); ctx.lineTo(w,lp); ctx.stroke(); ctx.setLineDash([]);
  ctx.fillStyle='#f5f5f8'; ctx.font='10px "JetBrains Mono",monospace'; ctx.textAlign='right';
  ctx.fillText(fmtPrice(last.c), w-6, Math.max(12,lp-6));
  drawTradeMarkers(ctx, data, w, h, Y, slot);
}

/* --- trade entry/exit markers on the chart --- */
let tradeMarkers = [];
async function fetchTrades(){
  try{
    const r=await fetch('/api/trading/trades?symbol='+encodeURIComponent(activeMarket));
    const j=await r.json();
    tradeMarkers = (j && j.ok && j.markers) ? j.markers : [];
  }catch(e){ tradeMarkers=[]; }
  drawCandles();
}
function drawTradeMarkers(ctx, data, w, h, Y, slot){
  if(!tradeMarkers.length || !data.length) return;
  const t0=data[0].t, t1=data[data.length-1].t;
  if(!(t1>t0)) return;
  const n=data.length;
  for(const m of tradeMarkers){
    if(m.time < t0 || m.time > t1) continue;       // only inside the visible window
    if(!m.price) continue;
    const idx=(m.time-t0)/(t1-t0)*(n-1);
    const x=idx*slot+slot/2;
    if(x<0||x>w) continue;
    const y=Y(m.price);
    const col = m.side==='buy' ? '#3ddc84' : '#ff5252';
    if(m.kind==='entry'){
      ctx.beginPath();
      ctx.fillStyle=col; ctx.shadowColor=col; ctx.shadowBlur=5;
      if(m.side==='buy'){ ctx.moveTo(x,y-7); ctx.lineTo(x-5,y+4); ctx.lineTo(x+5,y+4); }
      else{ ctx.moveTo(x,y+7); ctx.lineTo(x-5,y-4); ctx.lineTo(x+5,y-4); }
      ctx.closePath(); ctx.fill(); ctx.shadowBlur=0;
    } else {
      ctx.strokeStyle=col; ctx.shadowColor=col; ctx.shadowBlur=5; ctx.lineWidth=1.6;
      ctx.beginPath();
      ctx.moveTo(x-4,y-4); ctx.lineTo(x+4,y+4);
      ctx.moveTo(x+4,y-4); ctx.lineTo(x-4,y+4);
      ctx.stroke(); ctx.shadowBlur=0;
    }
  }
}

/* --- session dial (black/gray glow, live) --- */
function inHourRange(h,s,e){ if(s<=e) return h>=s&&h<e; return h>=s||h<e; }
function drawSessionDial(){
  const cv=$('mk-session'); if(!cv) return;
  const ctx=cv.getContext('2d');
  const dpr=window.devicePixelRatio||1;
  const size=cv.clientWidth||132;
  cv.width=Math.round(size*dpr); cv.height=Math.round(size*dpr); ctx.setTransform(dpr,0,0,dpr,0,0);
  const cx=size/2, cy=size/2, R=size/2-10;
  ctx.clearRect(0,0,size,size);
  const now=new Date();
  const hUTC=now.getUTCHours()+now.getUTCMinutes()/60+now.getUTCSeconds()/3600;
  const t=Date.now()/1000;
  const SESSIONS=[
    {name:'SYDNEY',s:21,e:7},
    {name:'TOKYO',s:0,e:9},
    {name:'LONDON',s:7,e:16},
    {name:'NY',s:13,e:22},
  ];
  const ang=h=>((h/24)*Math.PI*2 - Math.PI/2);

  // base ring
  ctx.beginPath(); ctx.arc(cx,cy,R,0,Math.PI*2);
  ctx.strokeStyle='rgba(210,210,220,0.08)'; ctx.lineWidth=10; ctx.stroke();

  // hour ticks (major every 6h)
  for(let h=0;h<24;h++){
    const a=ang(h);
    const major=(h%6===0);
    ctx.beginPath();
    ctx.moveTo(cx+Math.cos(a)*(R-6), cy+Math.sin(a)*(R-6));
    ctx.lineTo(cx+Math.cos(a)*(R-(major?13:10)), cy+Math.sin(a)*(R-(major?13:10)));
    ctx.strokeStyle=major?'rgba(210,210,220,0.5)':'rgba(210,210,220,0.16)';
    ctx.lineWidth=major?1.5:1;
    ctx.stroke();
  }
  // hour labels 0 / 6 / 12 / 18
  ctx.fillStyle='rgba(210,210,220,0.5)'; ctx.font='8px "JetBrains Mono",monospace';
  ctx.textAlign='center'; ctx.textBaseline='middle';
  [[0,'0'],[6,'6'],[12,'12'],[18,'18']].forEach(([h,label])=>{
    const a=ang(h);
    ctx.fillText(label, cx+Math.cos(a)*(R-20), cy+Math.sin(a)*(R-20));
  });

  // session arcs — open = bright pulsing white, closed = faint gray
  SESSIONS.forEach(sess=>{
    const open=inHourRange(hUTC,sess.s,sess.e);
    const a0=ang(sess.s), sweep=(((sess.e-sess.s+24)%24)/24)*Math.PI*2;
    const breathe=open?(0.5+0.5*Math.sin(t*2.4)):0;
    ctx.beginPath();
    ctx.arc(cx,cy,R,a0,a0+sweep);
    ctx.lineCap='round';
    if(open){
      ctx.lineWidth=6;
      ctx.strokeStyle='rgba(245,245,248,'+(0.7+0.3*breathe).toFixed(3)+')';
      ctx.shadowColor='#f5f5f8'; ctx.shadowBlur=8+10*breathe;
    } else {
      ctx.lineWidth=4;
      ctx.strokeStyle='rgba(210,210,220,0.13)';
      ctx.shadowBlur=0;
    }
    ctx.stroke(); ctx.shadowBlur=0;
  });

  // "now" needle
  const na=ang(hUTC);
  const np=0.5+0.5*Math.sin(t*2.4);
  ctx.beginPath(); ctx.moveTo(cx,cy); ctx.lineTo(cx+Math.cos(na)*(R+2), cy+Math.sin(na)*(R+2));
  ctx.strokeStyle='rgba(245,245,248,0.95)'; ctx.lineWidth=2;
  ctx.shadowColor='#f5f5f8'; ctx.shadowBlur=8+4*np; ctx.stroke(); ctx.shadowBlur=0;
  ctx.beginPath(); ctx.arc(cx,cy,3.2,0,Math.PI*2);
  ctx.fillStyle='#f5f5f8'; ctx.shadowColor='#f5f5f8'; ctx.shadowBlur=10; ctx.fill(); ctx.shadowBlur=0;

  // live UTC time in the center
  ctx.fillStyle='rgba(245,245,248,0.85)'; ctx.font='700 9px "JetBrains Mono",monospace';
  ctx.textAlign='center'; ctx.textBaseline='middle';
  const hh=String(now.getUTCHours()).padStart(2,'0'), mm=String(now.getUTCMinutes()).padStart(2,'0');
  ctx.fillText(hh+':'+mm+' UTC', cx, cy+15);

  updateSessionLegend(hUTC);
}
function updateSessionLegend(h){
  const host=$('mk-session-legend'); if(!host) return;
  const S=[['SYDNEY',21,7],['TOKYO',0,9],['LONDON',7,16],['NY',13,22]];
  host.innerHTML=S.map(([name,s,e])=>{
    const open=inHourRange(h,s,e);
    return '<span class="'+(open?'open':'')+'"><i></i>'+name+(open?' <b>OPEN</b>':'')+'</span>';
  }).join('');
}

/* --- equity sparkline --- */
async function fetchEquity(){
  try{
    const r=await fetch('/api/trading/equity');
    const j=await r.json();
    lastEquityHist=j.history||[];
    drawEquity(lastEquityHist);
  }catch(e){}
}
function drawEquity(hist){
  const cv=$('mk-equity'); if(!cv) return;
  const ctx=cv.getContext('2d');
  const dpr=window.devicePixelRatio||1;
  const w=cv.clientWidth||220, h=cv.clientHeight||56;
  cv.width=Math.round(w*dpr); cv.height=Math.round(h*dpr); ctx.setTransform(dpr,0,0,dpr,0,0);
  ctx.clearRect(0,0,w,h);
  if(!hist||!hist.length){
    ctx.fillStyle='rgba(210,210,220,0.3)'; ctx.font='9px "JetBrains Mono",monospace'; ctx.textAlign='center';
    ctx.fillText('no equity data yet', w/2, h/2);
    return;
  }
  const vals=hist.map(p=>p.equity);
  const lo=Math.min.apply(null,vals), hi=Math.max.apply(null,vals);
  const pad=(hi-lo)||1;
  const Y=v=>h-((v-(lo-pad*0.1))/((hi-lo)+pad*0.2))*h;
  const X=i=>hist.length===1?w/2:(i/(hist.length-1))*w;
  ctx.beginPath(); ctx.moveTo(X(0),h);
  hist.forEach((p,i)=>ctx.lineTo(X(i),Y(p.equity)));
  ctx.lineTo(X(hist.length-1),h); ctx.closePath();
  const up=vals[vals.length-1]>=vals[0];
  ctx.fillStyle=up?'rgba(61,220,132,0.10)':'rgba(255,82,82,0.10)'; ctx.fill();
  ctx.beginPath();
  hist.forEach((p,i)=>{ if(i===0)ctx.moveTo(X(i),Y(p.equity)); else ctx.lineTo(X(i),Y(p.equity)); });
  ctx.strokeStyle='#d8d8e0'; ctx.lineWidth=1.5; ctx.shadowColor='#f5f5f8'; ctx.shadowBlur=6; ctx.stroke(); ctx.shadowBlur=0;
  ctx.fillStyle='rgba(245,245,248,0.8)'; ctx.font='9px "JetBrains Mono",monospace'; ctx.textAlign='left';
  ctx.fillText('$'+vals[vals.length-1].toFixed(2), 4, 12);
}

/* --- heatmap --- */
function renderHeatmap(ticks){
  const host=$('mk-heatmap'); if(!host) return;
  const rows=[];
  for(const sym of MK_SYMBOLS){
    const t=ticks[sym]; if(!t||t.bid==null) continue;
    if(dayOpen[sym]==null) dayOpen[sym]=t.bid;
    const chg=((t.bid-dayOpen[sym])/dayOpen[sym])*100;
    rows.push({sym,chg});
  }
  if(!rows.length){ host.innerHTML='<div class="mk-ins-empty">Waiting for ticks&hellip;</div>'; return; }
  rows.sort((a,b)=>Math.abs(b.chg)-Math.abs(a.chg));
  host.innerHTML='';
  rows.forEach(r=>{
    const d=document.createElement('div');
    d.className='mh-tile'+(r.chg>=0?' up':' down')+(r.sym===activeMarket?' active':'');
    d.onclick=()=>selectMarket(r.sym);
    d.innerHTML='<span class="mh-sym">'+escapeHtml(r.sym)+'</span><span class="mh-chg">'+(r.chg>=0?'+':'')+r.chg.toFixed(2)+'%</span>';
    host.appendChild(d);
  });
}

/* --- live price + insights --- */
function updateMkLive(snap){
  const ticks=(snap&&snap.ticks)||{};
  renderHeatmap(ticks);
  const t=ticks[activeMarket];
  if(t&&t.bid!=null){
    lastActiveTick=t;
    lastBid=t.bid;
    $('mk-price').textContent=fmtPrice(t.bid);
    if(dayOpen[activeMarket]==null) dayOpen[activeMarket]=t.bid;
    const chg=((t.bid-dayOpen[activeMarket])/dayOpen[activeMarket])*100;
    const el=$('mk-change');
    el.textContent=(chg>=0?'+':'')+chg.toFixed(2)+'%';
    el.classList.toggle('up',chg>=0); el.classList.toggle('down',chg<0);
    drawCandles();
  } else {
    $('mk-price').textContent='\u2014'; $('mk-change').textContent='\u2014';
  }
}
function maybeMoveInsight(ticks){
  const syms = setting('alertsActiveOnly') ? [activeMarket] : MK_SYMBOLS;
  for(const sym of syms){
    const t=ticks[sym]; if(!t||t.bid==null) continue;
    const base=lastInsightPrice[sym];
    if(base!=null){
      const chg=Math.abs(t.bid-base)/base*100;
      if(chg>=0.25){ postInsight(sym+' moved '+(t.bid>base?'up':'down')+' '+chg.toFixed(2)+'%'); lastInsightPrice[sym]=t.bid; }
    } else lastInsightPrice[sym]=t.bid;
  }
}
function maybeNewsInsight(events){
  if(!events||!events.length) return;
  const now=Date.now()/1000;
  events.slice(0,12).forEach(e=>{
    const d=e.ts-now;
    if(d>0 && d<=600){
      const key=e.title+'@'+e.ts;
      if(!postedNews.has(key)){ postedNews.add(key); postInsight('\u23f0 '+e.title+' in '+fmtCountdown(e.ts)); }
    }
  });
}

/* --- init --- */
loadSettings();
restoreMarket();
renderMkChips();
renderMkTfs();
$('mk-symbol').textContent=activeMarket;
drawSessionDial();
drawCandles();
renderInsights();
loadCandles();
fetchEquity();
setInterval(drawSessionDial, 1000);
setInterval(fetchEquity, 5000);
setInterval(fetchTrades, 20000);
window.addEventListener('resize', ()=>{ drawCandles(); drawSessionDial(); drawEquity(lastEquityHist); });

/* keep the workspace filling the space down to just above the chat panel */
function layoutMarket(){
  const m=$('market'); if(!m) return;
  const chat=$('chatwrap');
  m.style.bottom = ((chat && chat.offsetHeight ? chat.offsetHeight : 0) + 26) + 'px';
}
if(window.ResizeObserver){ new ResizeObserver(layoutMarket).observe($('chatwrap')); }
layoutMarket();

setInterval(pollTrading, 2000);
setInterval(pollNews, 60000);
pollTrading();
pollNews();

/* ---------- Order Ticket (Phase 2B -- execution) ---------- */
let lastAccount=null;
let lastActiveTick=null;
let orderSide='buy';
let orderType='market';
let pendingOrder=null;

function orderVol(){ const v=parseFloat($('td-volume').value); return (isNaN(v)||v<=0)?0.01:v; }
function orderSL(){ const v=parseFloat($('td-sl').value); return isNaN(v)?null:v; }
function orderTP(){ const v=parseFloat($('td-tp').value); return isNaN(v)?null:v; }
function orderPrice(){ const v=parseFloat($('td-price').value); return isNaN(v)?null:v; }

function setOrderSide(side){
  orderSide=side;
  renderOrderTicket();
}
function setOrderType(ot){
  orderType=ot;
  document.querySelectorAll('#td-otype .td-ot').forEach(b=>b.classList.toggle('on', b.dataset.ot===ot));
  $('td-price-field').style.display = (ot==='market') ? 'none' : 'flex';
  renderOrderTicket();
}

function renderOrderTicket(){
  const symEl=$('td-order-sym'); if(symEl) symEl.textContent=activeMarket;
  $('td-side-buy').classList.toggle('on', orderSide==='buy');
  $('td-side-sell').classList.toggle('on', orderSide==='sell');
  const place=$('td-place');
  if(place){
    place.textContent = 'PLACE ' + (orderSide==='buy'?'BUY':'SELL');
    place.className = 'td-place ' + orderSide;
  }
  updateOrderMeta();
}

function updateOrderMeta(){
  const t=lastActiveTick;
  const vol=orderVol();
  let riskTxt = vol.toFixed(2)+' lots';
  let marginTxt = 'margin \u2014';
  if(t && t.point && t.tick_value){
    const sl=orderSL();
    if(sl!=null){
      const entry = orderSide==='buy' ? t.ask : t.bid;
      const distPts = Math.abs(entry - sl) / t.point;
      const risk = distPts * t.tick_value * vol;
      riskTxt += ' \u00b7 risk $' + risk.toFixed(2);
    }
    if(t.contract_size && lastAccount && lastAccount.leverage){
      const entry = orderSide==='buy' ? t.ask : t.bid;
      const notional = entry * t.contract_size * vol;
      const margin = notional / lastAccount.leverage;
      marginTxt = 'margin \u2248 $' + margin.toFixed(2);
    }
  }
  const re=$('td-meta-risk'); if(re) re.innerHTML=riskTxt;
  const me=$('td-meta-margin'); if(me) me.innerHTML=marginTxt;
}

function renderConfirm(lines, side){
  const host=$('tdc-lines');
  host.innerHTML='';
  lines.forEach(([k,v])=>{
    const d=document.createElement('div');
    d.className='tdc-line';
    const valCls = (k==='SIDE') ? (side==='buy'?'buy':'sell') : '';
    d.innerHTML='<span>'+escapeHtml(k)+'</span><b class="'+valCls+'">'+escapeHtml(v)+'</b>';
    host.appendChild(d);
  });
  const go=$('tdc-go');
  go.className = 'go ' + side;
  go.textContent = side==='buy' ? 'Confirm BUY' : 'Confirm SELL';
  $('td-confirm').classList.add('show');
}
function closeConfirm(){ $('td-confirm').classList.remove('show'); pendingOrder=null; }

function submitOrder(side, type, vol, sl, tp, price, sym){
  const t=lastActiveTick;
  const entry = type==='market' ? (side==='buy' ? (t?t.ask:null) : (t?t.bid:null)) : price;
  const lines=[];
  lines.push(['SYMBOL', sym]);
  lines.push(['SIDE', side==='buy'?'BUY':'SELL']);
  lines.push(['TYPE', type.toUpperCase()]);
  lines.push(['VOLUME', vol.toFixed(2)+' lots']);
  if(entry!=null) lines.push(['PRICE', fmtPrice(entry)]);
  if(sl!=null) lines.push(['SL', fmtPrice(sl)]);
  if(tp!=null) lines.push(['TP', fmtPrice(tp)]);
  pendingOrder={sym, side, type, vol, sl, tp, price};
  renderConfirm(lines, side);
}

async function doConfirmOrder(){
  if(!pendingOrder) return;
  const o=pendingOrder;
  closeConfirm();
  $('td-place').disabled=true;
  try{
    const r=await fetch('/api/trading/order',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
      symbol:o.sym, side:o.side, order_type:o.type, volume:o.vol, sl:o.sl, tp:o.tp, price:o.price
    })});
    const j=await r.json();
    if(j && j.ok){
      postInsight('Order sent: '+(o.side==='buy'?'BUY':'SELL')+' '+o.vol+' '+o.sym+(j.ticket?(' #'+j.ticket):''));
      showToast('Order placed: '+(o.side==='buy'?'BUY':'SELL')+' '+o.sym+(j.ticket?(' #'+j.ticket):''),'TRADE');
      pollTrading();
      fetchTrades();
    } else {
      showToast('Order rejected: '+(j&&j.error||'unknown'),'TRADE');
      postInsight('Order failed: '+(j&&j.error||'?'));
    }
  }catch(e){
    showToast('Order failed: '+(e.message||e),'TRADE');
  }finally{
    $('td-place').disabled=false;
  }
}

async function closePos(ticket){
  if(!confirm('Close position #'+ticket+'?')) return;
  try{
    const r=await fetch('/api/trading/close',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ticket})});
    const j=await r.json();
    if(j && j.ok){ postInsight('Closed #'+ticket); pollTrading(); fetchTrades(); }
    else showToast('Close failed: '+(j&&j.error||'?'),'TRADE');
  }catch(e){ showToast('Close failed: '+(e.message||e),'TRADE'); }
}

async function modifySlTp(ticket, slRaw, tpRaw, form){
  const sl=(slRaw===''||slRaw==null||slRaw===undefined)?null:+slRaw;
  const tp=(tpRaw===''||tpRaw==null||tpRaw===undefined)?null:+tpRaw;
  if(sl===null && tp===null){ showToast('Enter SL and/or TP to save.','TRADE'); return; }
  if(sl!==null && !isFinite(sl)){ showToast('Invalid SL value.','TRADE'); return; }
  if(tp!==null && !isFinite(tp)){ showToast('Invalid TP value.','TRADE'); return; }
  try{
    const r=await fetch('/api/trading/modify',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ticket, sl, tp})});
    const j=await r.json();
    if(j && j.ok){ postInsight('Updated SL/TP on #'+ticket); if(form) form.classList.remove('open'); pollTrading(); }
    else showToast('Modify failed: '+(j&&(j.error||j.retcode_text)||'?'),'TRADE');
  }catch(e){ showToast('Modify failed: '+(e.message||e),'TRADE'); }
}

async function closeAllPositions(){
  if(!confirm('Close ALL open positions?')) return;
  try{
    const r=await fetch('/api/trading/close_all',{method:'POST'});
    const j=await r.json();
    if(j && j.ok){ postInsight('Flat: closed all positions'); pollTrading(); fetchTrades(); }
    else showToast('Close all failed: '+(j&&j.error||'?'),'TRADE');
  }catch(e){ showToast('Close all failed: '+(e.message||e),'TRADE'); }
}

/* wire the ticket */
$('td-side-buy').onclick=()=>setOrderSide('buy');
$('td-side-sell').onclick=()=>setOrderSide('sell');
document.querySelectorAll('#td-otype .td-ot').forEach(b=>b.onclick=()=>setOrderType(b.dataset.ot));
$('td-vol-minus').onclick=()=>{ $('td-volume').value=Math.max(0.01, +(orderVol()-0.01).toFixed(2)); updateOrderMeta(); };
$('td-vol-plus').onclick=()=>{ $('td-volume').value=+(orderVol()+0.01).toFixed(2); updateOrderMeta(); };
$('td-volume').addEventListener('input', updateOrderMeta);
$('td-sl').addEventListener('input', updateOrderMeta);
$('td-tp').addEventListener('input', updateOrderMeta);
$('td-order-sync').onclick=()=>{ renderOrderTicket(); };
$('td-place').onclick=()=>{
  const vol=orderVol();
  if(orderType!=='market' && orderPrice()==null){
    showToast('Enter an entry price for limit/stop orders.','TRADE');
    return;
  }
  submitOrder(orderSide, orderType, vol, orderSL(), orderTP(), orderType==='market'?null:orderPrice(), activeMarket);
};
$('tdc-cancel').onclick=closeConfirm;
$('tdc-go').onclick=doConfirmOrder;
$('td-flat').onclick=closeAllPositions;

/* one-click market buy/sell from the workspace header */
function quickMarket(side){
  submitOrder(side, 'market', orderVol(), orderSL(), orderTP(), null, activeMarket);
}
$('mk-qbuy').onclick=()=>quickMarket('buy');
$('mk-qsell').onclick=()=>quickMarket('sell');

/* init the ticket to the restored market */
renderOrderTicket();


