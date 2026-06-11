// ---------- 轻量 markdown 渲染（自带，无需 CDN，离线可用）----------
function esc(s){return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");}

// ---------- chunk 溯源弹层：点 [chunk_N] / 引用 chip 看原文片段 ----------
function closeChunk(){const ov=document.getElementById("chunkModal");if(ov)ov.style.display="none";}
async function showChunk(id){
  let ov=document.getElementById("chunkModal");
  if(!ov){
    ov=document.createElement("div");ov.id="chunkModal";ov.className="modal-ov";
    ov.innerHTML='<div class="modal-bx"><button class="modal-x" onclick="closeChunk()">✕</button><div id="chunkBody"></div></div>';
    ov.addEventListener("click",e=>{if(e.target===ov)closeChunk();});
    document.addEventListener("keydown",e=>{if(e.key==="Escape")closeChunk();});
    document.body.appendChild(ov);
  }
  const body=document.getElementById("chunkBody");
  body.innerHTML='<div class="muted"><span class="spin"></span> 加载 chunk_'+id+' …</div>';
  ov.style.display="flex";
  try{
    const r=await fetch("/api/chunk/"+id);
    if(!r.ok){body.innerHTML='<div class="muted">未找到 chunk_'+id+'（来源可能已删除）</div>';return;}
    const c=await r.json();
    const src=c.url?`<a href="${c.url}" target="_blank" rel="noopener">${esc(c.source_title||c.url)} ↗</a>`:esc(c.source_title||"");
    body.innerHTML=`<h3 style="margin:0 0 4px">chunk_${c.chunk_id}`+(c.kind?` · <span style="color:var(--accent2)">${esc(c.kind)}</span>`:"")+`</h3>`+
      `<div class="muted" style="margin-bottom:8px">来源：${src}`+(c.ord!=null?` · 第 ${c.ord} 块`:"")+`</div>`+
      (c.context?`<div class="muted" style="font-style:italic;margin-bottom:10px">定位：${esc(c.context)}</div>`:"")+
      `<div style="white-space:pre-wrap;max-height:55vh;overflow:auto;line-height:1.6">${esc(c.content||"")}</div>`;
  }catch(e){body.innerHTML='<div class="muted">加载失败：'+esc(String(e))+'</div>';}
}
function renderMD(md){
  const lines = md.split("\n"); let html=""; let inUl=false, inCode=false, code="";
  const closeUl=()=>{ if(inUl){html+="</ul>";inUl=false;} };
  const inline=(t)=>{
    t=esc(t);
    t=t.replace(/```/g,"");
    t=t.replace(/`([^`]+)`/g,(m,c)=>`<code>${c}</code>`);
    t=t.replace(/\*\*([^*]+)\*\*/g,"<b>$1</b>");
    t=t.replace(/\[\[([^\]]+)\]\]/g,(m,x)=>`<a href="javascript:void(0)" class="followup" title="点击就这道追问生成答案" onclick="askFollowup(decodeURIComponent('${encodeURIComponent(x)}'))">${x}</a>`);
    t=t.replace(/\[chunk[_\s]?(\d+)\]/gi,'<a class="cite" href="javascript:void(0)" onclick="showChunk($1)" title="点击看原文片段">chunk_$1</a>');
    return t;
  };
  for(let raw of lines){
    if(raw.trim().startsWith("```")){
      if(!inCode){inCode=true;code="";} else {closeUl();html+="<pre><code>"+esc(code)+"</code></pre>";inCode=false;}
      continue;
    }
    if(inCode){code+=raw+"\n";continue;}
    let l=raw.trimEnd();
    if(/^###\s+/.test(l)){closeUl();html+="<h3>"+inline(l.replace(/^###\s+/,""))+"</h3>";}
    else if(/^##\s+/.test(l)){closeUl();html+="<h2>"+inline(l.replace(/^##\s+/,""))+"</h2>";}
    else if(/^#\s+/.test(l)){closeUl();html+="<h1>"+inline(l.replace(/^#\s+/,""))+"</h1>";}
    else if(/^>\s?/.test(l)){closeUl();html+="<blockquote>"+inline(l.replace(/^>\s?/,""))+"</blockquote>";}
    else if(/^[-*]\s+/.test(l)){ if(!inUl){html+="<ul>";inUl=true;} html+="<li>"+inline(l.replace(/^[-*]\s+/,""))+"</li>";}
    else if(l.trim()===""){closeUl();}
    else {closeUl();html+="<p>"+inline(l)+"</p>";}
  }
  closeUl(); if(inCode)html+="<pre><code>"+esc(code)+"</code></pre>";
  return html;
}

function toast(msg){const t=document.getElementById("toast");t.textContent=msg;t.style.display="block";
  setTimeout(()=>t.style.display="none",3500);}

// ---------- 导航 ----------
document.querySelectorAll("nav button").forEach(b=>b.onclick=()=>{
  document.querySelectorAll("nav button").forEach(x=>x.classList.remove("active"));
  document.querySelectorAll(".page").forEach(x=>x.classList.remove("active"));
  b.classList.add("active");
  document.getElementById("page-"+b.dataset.page).classList.add("active");
  if(b.dataset.page==="kb")loadSources();
  if(b.dataset.page==="lib")loadLib();
  if(b.dataset.page==="jobs")initJobs();
});

// ---------- 延伸追问：点 [[追问]] → 切到提问页就这道题即时生成 ----------
function askFollowup(text){
  document.querySelectorAll("nav button").forEach(x=>x.classList.remove("active"));
  document.querySelectorAll(".page").forEach(x=>x.classList.remove("active"));
  const nb=document.querySelector('nav button[data-page="ask"]');
  if(nb)nb.classList.add("active");
  document.getElementById("page-ask").classList.add("active");
  const qEl=document.getElementById("q");
  qEl.value=text;
  window.scrollTo({top:0,behavior:"smooth"});
  askBtn.click();
}

// ---------- 健康 ----------
async function loadHealth(){
  const dot=document.getElementById("health-dot");
  try{
    const h=await (await fetch("/api/health")).json();
    const ok=h.vllm.ok&&h.postgres.ok&&h.anki.ok;
    dot.style.color=ok?"var(--ok)":"var(--warn)";
    dot.textContent=(ok?"● 就绪":"● 部分异常")+` · vLLM ${h.vllm.ok?h.vllm.model:"✗"} · PG ${h.postgres.ok?h.postgres.chunks+"块":"✗"} · Anki ${h.anki.ok?"v"+h.anki.version:"✗"}`;
  }catch(e){dot.style.color="var(--bad)";dot.textContent="● 后端无响应";}
}
document.getElementById("health-dot").onclick=loadHealth;

// ---------- 提问（SSE 流式）----------
const askBtn=document.getElementById("askBtn");
askBtn.onclick=async()=>{
  const q=document.getElementById("q").value.trim();
  if(!q){toast("请输入题目");return;}
  askBtn.disabled=true;askBtn.innerHTML='<span class="spin"></span> 生成中…';
  const res=document.getElementById("result");res.style.display="block";
  const reasonBox=document.getElementById("reasonBox"),reason=document.getElementById("reason");
  const answerEl=document.getElementById("answer"),metaEl=document.getElementById("meta");
  const retEl=document.getElementById("retrieval");
  reason.textContent="";reasonBox.style.display="none";answerEl.innerHTML="";
  metaEl.innerHTML="";retEl.innerHTML="";
  let answer="",reasoning="";
  try{
    const r=await fetch("/api/ask/stream",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({question:q,topic:document.getElementById("topic").value,
        difficulty:+document.getElementById("diff").value,
        frequency:+document.getElementById("freq").value,
        push_anki:document.getElementById("anki").value==="true",
        frontier:document.getElementById("frontier").value==="true"})});
    const reader=r.body.getReader();const dec=new TextDecoder();let buf="";
    while(true){
      const {done,value}=await reader.read(); if(done)break;
      buf+=dec.decode(value,{stream:true});
      let idx;
      while((idx=buf.indexOf("\n\n"))>=0){
        const chunk=buf.slice(0,idx);buf=buf.slice(idx+2);
        if(!chunk.startsWith("data:"))continue;
        const ev=JSON.parse(chunk.slice(5).trim());
        if(ev.type==="frontier"){
          if(ev.status==="done"){
            const got=(ev.ingested||[]).map(x=>`${x.title}(${x.n_chunks}块)`).join("、")||"无新增（已在库或未命中）";
            retEl.innerHTML=`<div class="chip" style="border-color:var(--accent2)">🌐 已抓取：${got}</div>`;
          }else retEl.innerHTML=`<div class="chip" style="border-color:var(--accent2)"><span class="spin"></span> ${ev.status}</div>`;
        }else if(ev.type==="retrieval"){
          if(ev.hits.length){retEl.innerHTML='<div class="muted">检索命中：</div><div class="chips">'+
            ev.hits.map(h=>`<span class="chip click" onclick="showChunk(${h.chunk_id})" title="点击看原文片段">chunk_${h.chunk_id} · ${h.source_title} · ${h.score}</span>`).join("")+'</div>';}
          else{retEl.innerHTML='<div class="chip">⚠️ 知识库为空，本次无检索（答案未接地气，请先去「知识库」摄入权威源）</div>';}
        }else if(ev.type==="reasoning"){reasonBox.style.display="block";reasoning+=ev.text;reason.textContent=reasoning;}
        else if(ev.type==="content"){answer+=ev.text;answerEl.innerHTML=renderMD(answer);}
        else if(ev.type==="done"){
          const g=ev.grounded?'<span class="badge ok">✓ 有出处 ('+ev.n_citations+'处引用)</span>'
            :'<span class="badge bad">⚠ 未接地气，建议复核</span>';
          metaEl.innerHTML=`${g} <span class="muted">· QID ${ev.qid} · 检索 ${ev.n_hits} 片段 · `+
            (ev.anki_note_id?`已推 Anki 卡 ${ev.anki_note_id}`:"未推卡")+`</span><br>`+
            `<span class="muted">📝 ${ev.note_path}</span>`;
          loadHealth();
        }else if(ev.type==="error"){toast("出错："+ev.message);}
      }
    }
  }catch(e){toast("请求失败："+e.message);}
  askBtn.disabled=false;askBtn.textContent="生成答案";
};

// ---------- 摄入 ----------
document.querySelectorAll("#ingSeg button").forEach(b=>b.onclick=()=>{
  document.querySelectorAll("#ingSeg button").forEach(x=>x.classList.remove("active"));
  b.classList.add("active");
  ["url","text","file"].forEach(m=>document.getElementById("ing-"+m).style.display=m===b.dataset.m?"block":"none");
});
function ingMode(){return document.querySelector("#ingSeg button.active").dataset.m;}
document.getElementById("ingBtn").onclick=async()=>{
  const btn=document.getElementById("ingBtn"),st=document.getElementById("ingStatus");
  const ctx=document.getElementById("ctx").value==="true";
  btn.disabled=true;btn.innerHTML='<span class="spin"></span> 摄入中…';st.textContent="";
  try{
    let res;const m=ingMode();
    if(m==="file"){
      const f=document.getElementById("file").files[0];
      if(!f){toast("请选择文件");throw new Error("no file");}
      const fd=new FormData();fd.append("file",f);
      res=await (await fetch("/api/ingest/file?contextual="+ctx,{method:"POST",body:fd})).json();
    }else{
      const body=m==="url"?{url:document.getElementById("url").value.trim(),contextual:ctx}
        :{text:document.getElementById("text").value,contextual:ctx};
      res=await (await fetch("/api/ingest",{method:"POST",headers:{"Content-Type":"application/json"},
        body:JSON.stringify(body)})).json();
    }
    if(res.source_id){st.innerHTML=`✅ 《${res.title}》切成 ${res.n_chunks} 块`;loadSources();loadHealth();}
    else toast("摄入失败："+(res.detail||JSON.stringify(res)));
  }catch(e){if(e.message!=="no file")toast("摄入失败："+e.message);}
  btn.disabled=false;btn.textContent="摄入到知识库";
};
async function loadSources(){
  const rows=await (await fetch("/api/sources")).json();
  document.getElementById("srcBody").innerHTML=rows.map(r=>
    `<tr><td>${r.id}</td><td><span class="chip">${r.kind}</span></td><td>${r.title}</td>`+
    `<td>${r.n_chunks}</td><td class="muted">${r.fetched_at.slice(0,16)}</td></tr>`).join("")
    ||'<tr><td colspan="5" class="muted">还没有知识源，去上面摄入一篇论文吧</td></tr>';
}

// ---------- 题库 ----------
let libRows=[],libFilter="all";
async function loadLib(){
  libRows=await (await fetch("/api/library")).json();
  renderLib();
}
document.querySelectorAll("#libSeg button").forEach(b=>b.onclick=()=>{
  document.querySelectorAll("#libSeg button").forEach(x=>x.classList.remove("active"));
  b.classList.add("active");libFilter=b.dataset.f;renderLib();
});
function renderLib(){
  const kw=(document.getElementById("libSearch").value||"").trim().toLowerCase();
  const total=libRows.length;
  const pending=libRows.filter(r=>r.note_path==null).length;
  const grounded=libRows.filter(r=>r.grounded).length;
  document.getElementById("libStats").innerHTML=
    `共 ${total} · <span style="color:var(--ok)">有出处 ${grounded}</span> · <span style="color:var(--warn)">待答 ${pending}</span>`;
  let rows=libRows;
  if(libFilter==="pending")rows=rows.filter(r=>r.note_path==null);
  else if(libFilter==="grounded")rows=rows.filter(r=>r.grounded);
  if(kw)rows=rows.filter(r=>(r.question||"").toLowerCase().includes(kw)||(r.topic||"").toLowerCase().includes(kw));
  document.getElementById("libBody").innerHTML=rows.map(r=>{
    const answered=r.note_path!=null;
    const stat=!answered?'<span class="badge bad">待答</span>'
      :(r.grounded?'<span class="badge ok">✓'+r.n_citations+'</span>':'<span class="badge bad">无出处</span>');
    return `<tr class="click" onclick="viewAns(${r.qid})"><td>${r.qid}</td><td>${r.question}</td>`+
    `<td><span class="chip">${r.topic||""}</span></td>`+
    `<td>${stat}</td>`+
    `<td class="muted">${r.difficulty||"?"}/${r.frequency||"?"}</td></tr>`;}).join("")
    ||`<tr><td colspan="5" class="muted">${total?"没有符合条件的题":"还没有题目"}</td></tr>`;
}
async function genAllPending(btn){
  const r=await (await fetch("/api/library/generate_pending",{method:"POST"})).json();
  if(r.already_running){toast("已在补答中…");}
  else if(!r.started){toast(r.message||"没有待答题");return;}
  else toast(`已启动并发补答 ${r.started} 题（${r.workers}并发）`);
  btn.disabled=true;pollBulk(btn);
}
async function pollBulk(btn){
  try{
    const s=await (await fetch("/api/library/progress")).json();
    if(s.total){
      btn.innerHTML=`<span class="spin"></span> 补答中 ${s.done}/${s.total}（出处${s.grounded}·卡${s.cards}）`;
    }
    if(s.running){setTimeout(()=>pollBulk(btn),3000);}
    else{
      btn.disabled=false;btn.textContent="⚡ 一键补答全部待答";
      if(s.total){toast(`补答完成：${s.done} 题，有出处 ${s.grounded}，新增 ${s.cards} 张卡`);loadLib();loadHealth();}
    }
  }catch(e){btn.disabled=false;btn.textContent="⚡ 一键补答全部待答";}
}
async function viewAns(qid){
  const a=await (await fetch("/api/answer/"+qid)).json();
  const v=document.getElementById("ansView");v.style.display="block";
  if(a.error){v.innerHTML=`<p class="muted">${a.error}（qid ${qid}）</p>`;return;}
  if(!a.answered){
    v.innerHTML=`<h2 style="margin-top:0">${a.question}</h2>`+
      `<span class="badge bad">尚未生成答案</span> <span class="muted">· 来源：${a.origin||"?"}</span>`+
      `<p class="muted">这道题在备考清单里，但还没生成带出处的答案（所以之前点开是空的）。</p>`+
      `<button class="primary" onclick="genOne(${qid},this)">⚡ 现在生成带出处答案 + 卡片</button>`+
      `<span id="genOneStatus" class="muted" style="margin-left:10px"></span>`;
    v.scrollIntoView({behavior:"smooth"});return;
  }
  let cites="";
  if(a.citations&&a.citations.length)cites='<div class="chips">'+a.citations.map(c=>
    `<span class="chip click" onclick="showChunk(${c.chunk_id})" title="点击看原文片段">chunk_${c.chunk_id} · ${c.source_title}</span>`).join("")+'</div>';
  v.innerHTML=`<h2 style="margin-top:0">${a.question}</h2>`+
    (a.grounded?'<span class="badge ok">✓ 有出处</span>':'<span class="badge bad">⚠ 未接地气</span>')+cites+
    '<h3 style="color:var(--accent)">口述版</h3><div class="md">'+renderMD(a.oral||"")+'</div>'+
    '<h3 style="color:var(--accent)">深挖版</h3><div class="md">'+renderMD(a.deep||"")+'</div>';
  v.scrollIntoView({behavior:"smooth"});
}
async function genOne(qid,btn){
  btn.disabled=true;btn.innerHTML='<span class="spin"></span> 检索+生成中(约1分钟)…';
  try{
    const r=await (await fetch(`/api/answer/${qid}/generate`,{method:"POST"})).json();
    if(r.error)toast(r.error);else{toast("已生成");viewAns(qid);loadLib();}
  }catch(e){toast("生成失败："+e.message);btn.disabled=false;btn.textContent="⚡ 重试生成";}
}

// ---------- 岗位匹配（左栏画像+筛选 / 右栏分数卡片）----------
let jobsAll=[];            // 全量已评 JD
let jobExpect={min:null,max:null};   // 薪资期望基准
let jobsInited=false;
async function initJobs(){ if(jobsInited)return; jobsInited=true; loadResume(); loadJobStats(); loadMatches(); }
async function loadResume(){
  const r=await (await fetch("/api/resume")).json();
  const info=document.getElementById("resumeInfo"),prof=document.getElementById("resumeProfile");
  if(r&&r.profile){
    const p=r.profile;
    info.innerHTML=`<b>《${esc(r.source)}》</b><br>${esc(p.seniority||"")} · ${esc(p.years||"?")} 年`;
    const dm=(p.domains||[]).map(d=>`<span class="chip" style="border-color:var(--accent);color:var(--accent)">${esc(d)}</span>`);
    const sk=(p.core_skills||[]).slice(0,10).map(s=>`<span class="chip">${esc(s)}</span>`);
    prof.innerHTML=dm.concat(sk).join("");
  }else{info.textContent="未导入，点上方按钮导入";prof.innerHTML="";}
}
async function importDefaultResume(){
  toast("正在导入默认简历…");
  const r=await (await fetch("/api/resume/import_default",{method:"POST"})).json();
  if(r.error)toast(r.error);else{toast("简历已导入");loadResume();}
}
document.getElementById("resumeFile").onchange=async(e)=>{
  const f=e.target.files[0];if(!f)return;
  toast("解析简历中…");
  const fd=new FormData();fd.append("file",f);
  const r=await (await fetch("/api/resume/import",{method:"POST",body:fd})).json();
  if(r.error)toast(r.error);else{toast("简历已导入");loadResume();}
};
async function loadJobStats(){
  const s=await (await fetch("/api/jobs/stats")).json();
  document.getElementById("jobStats").textContent=`JD库 ${s.jds} 个 · 已评 ${s.matched}`;
}
async function scanJobs(){
  const st=document.getElementById("jobStats");st.innerHTML='<span class="spin"></span> 扫描中…';
  const r=await (await fetch("/api/jobs/scan",{method:"POST"})).json();
  if(r.error){toast(r.error);}else toast(`入库 ${r.inserted} 个(去重跳过 ${r.skipped_dup})`);
  loadJobStats();
}
document.getElementById("matchBtn").onclick=async()=>{
  const btn=document.getElementById("matchBtn"),st=document.getElementById("matchStatus");
  const redo=document.getElementById("matchRedo").checked;
  btn.disabled=true;btn.innerHTML='<span class="spin"></span> AI 评分中…';
  st.textContent=redo?" 全量重评中（首次较久）…":" 增量评估新岗位中…";
  try{
    const r=await (await fetch("/api/jobs/match",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({redo,scope:"all"})})).json();
    if(r.error){toast(r.error);}else{
      setMatches(r);
      toast(`本次新评 ${r.scored_now||0} · 累计 ${r.n_scored} 个岗位`);loadJobStats();
    }
  }catch(e){toast("匹配失败："+e.message);}
  btn.disabled=false;btn.textContent="运行 AI 匹配";st.textContent="";
};
async function loadMatches(){
  const r=await (await fetch("/api/jobs/matches")).json();
  if(r)setMatches(r);
}
function setMatches(r){
  jobsAll=r.all||[];jobExpect={min:r.expect_min,max:r.expect_max};renderJobs();
}
["f-verdict","f-sal","f-sort"].forEach(id=>{const e=document.getElementById(id);if(e)e.onchange=renderJobs;});
{const q=document.getElementById("f-q");if(q)q.oninput=renderJobs;}

function vBadge(v){return v==="strong"?'<span class="badge ok">稳 strong</span>'
  :v==="stretch"?'<span class="badge bad">够一够 stretch</span>':'<span class="muted">差距大 weak</span>';}

function jdCard(x){
  const cls=x.verdict==="strong"?"s-strong":x.verdict==="stretch"?"s-stretch":"s-weak";
  const s=x.ai_score||0;
  const badges=[
    `<span class="chip">月薪上限 ${x.salary_max||"?"}K</span>`,
    x.annual_max?`<span class="chip">年 ${x.annual_max}K</span>`:"",
    x.experience?`<span class="chip">${esc(x.experience)}</span>`:"",
    x.prep_built?'<span class="chip" style="border-color:var(--ok);color:var(--ok)">已备清单</span>':"",
  ].join("");
  const cols=`<div class=jcols>
    <div class="jcol good"><h4>✓ 我的优势（命中）</h4><ul>${(x.matched||[]).map(m=>`<li>${esc(m)}</li>`).join("")||'<li class=muted>—</li>'}</ul></div>
    <div class="jcol warn"><h4>⚠ 要准备的缺口</h4><ul>${(x.gaps||[]).map(g=>`<li>${esc(g)}</li>`).join("")||'<li class=muted>—</li>'}</ul></div>
  </div>`;
  const apply=x.url?`<a class="ghost" href="${esc(x.url)}" target=_blank rel=noopener style="text-decoration:none">看原 JD / 投递 ↗</a>`:"";
  return `<div class="jdc ${cls}" id="jdc-${x.jd_id}">
    <div class=hd onclick="this.parentNode.classList.toggle('open')">
      <div class=score><div class=num>${s}</div><div class=v>${esc(x.verdict||"")}</div></div>
      <div style="flex:1;min-width:0">
        <div class=ttl>${esc(x.role||"(无标题)")}</div>
        <div class=co>${esc(x.company||"")}</div>
        <div class=jbar><i style="width:${s}%"></i></div>
        ${x.reasoning?`<div class=jreason>${esc(x.reasoning)}</div>`:""}
        <div class=tags style="margin-top:7px">${badges}</div>
      </div>
      <div class=muted style="font-size:18px;align-self:center">▾</div>
    </div>
    <div class=body>
      ${cols}
      <div class=jacts>
        <button class="primary" style="margin:0" onclick="genPrep(${x.jd_id},this)">${x.prep_built?'重新生成备考清单':'① 生成备考清单'}</button>
        <button class="ghost" onclick="genFrontier(${x.jd_id},this)">②🌐 抓该岗最新论文</button>
        <button class="ghost" onclick="genMaterials(${x.jd_id},this)">③ 一键备齐资料</button>
        ${apply}
        <span id="prepStatus-${x.jd_id}" class="muted"></span>
      </div>
    </div>
  </div>`;
}
function renderJobs(){
  const fv=id=>{const e=document.getElementById(id);return e?e.value:"";};
  const verdict=fv("f-verdict"),minSal=parseInt(fv("f-sal"))||0,sort=fv("f-sort")||"score";
  const q=(fv("f-q")||"").toLowerCase();
  let list=jobsAll.filter(x=>
    (!verdict||x.verdict===verdict) &&
    ((x.salary_max||0)>=minSal) &&
    (!q||((x.role||"")+(x.company||"")).toLowerCase().includes(q)));
  list.sort((a,b)=> sort==="salary" ? (b.salary_max||0)-(a.salary_max||0) : (b.ai_score||0)-(a.ai_score||0));
  const cnt=document.getElementById("jobCount"),el=document.getElementById("jobList");
  if(!jobsAll.length){cnt.textContent="还没有匹配结果，点右上「运行 AI 匹配」。";el.innerHTML="";return;}
  cnt.textContent=`显示 ${list.length} / ${jobsAll.length} 个岗位　·　薪资期望基准 ${jobExpect.min||"?"}–${jobExpect.max||"?"}K（越高越好）`;
  el.innerHTML=list.map(jdCard).join("")||'<div class=muted>没有符合筛选条件的岗位</div>';
}
async function genPrep(jdId,btn){
  btn.disabled=true;btn.innerHTML='<span class="spin"></span> 生成中…';
  try{
    const r=await (await fetch(`/api/jobs/${jdId}/prep`,{method:"POST"})).json();
    if(r.error)toast(r.error);
    else document.getElementById("prepStatus-"+jdId).innerHTML=
      `✅ 缺口${r.n_gap}/项目深挖${r.n_project}/联想${r.n_assoc} 题已入题库　📝 ${r.note_path}`;
  }catch(e){toast("生成失败："+e.message);}
  btn.disabled=false;btn.textContent="重新生成备考清单";
}
async function genFrontier(jdId,btn){
  btn.disabled=true;btn.innerHTML='<span class="spin"></span> 出网抓最新论文(约1-2分钟)…';
  try{
    const r=await (await fetch(`/api/jobs/${jdId}/frontier`,{method:"POST"})).json();
    if(r.error)toast(r.error);
    else{
      const got=(r.ingested||[]).map(x=>`${x.title}(${x.n_chunks}块)`).join("、")||"无新增（已在库）";
      document.getElementById("prepStatus-"+jdId).innerHTML=
        `🌐 检索词：${(r.queries||[]).join(" / ")}　新增 ${r.n_new_sources} 篇：${got}`;
      loadHealth();
    }
  }catch(e){toast("抓取失败："+e.message);}
  btn.disabled=false;btn.textContent="②🌐 抓该岗最新论文入库";
}
async function genMaterials(jdId,btn){
  btn.disabled=true;btn.innerHTML='<span class="spin"></span> 备齐中(检索+生成+拆卡, 约几分钟)…';
  try{
    const r=await (await fetch(`/api/jobs/${jdId}/materials?max_q=6`,{method:"POST"})).json();
    if(r.error)toast(r.error);
    else document.getElementById("prepStatus-"+jdId).innerHTML=
      `✅ 已生成 ${r.answered} 篇带出处答案 + ${r.carded} 题原子卡，剩余 ${r.remaining} 题（可再点继续）`;
  }catch(e){toast("备齐失败："+e.message);}
  btn.disabled=false;btn.textContent="③ 一键备齐资料";
}

// ---------- 模拟面试 ----------
let mockSid=null;
function diffBar(d){let h="";for(let i=1;i<=5;i++)h+=`<i class="${i<=d?'on':''}"></i>`;return h;}
function addBubble(kind,who,text){
  const c=document.getElementById("mockChat");
  const b=document.createElement("div");b.className="bubble "+kind;
  b.innerHTML=`<div class="who">${who}</div>`+renderMD(text);
  c.appendChild(b);b.scrollIntoView({behavior:"smooth",block:"end"});return b;
}
document.getElementById("mockStartBtn").onclick=async()=>{
  const btn=document.getElementById("mockStartBtn");
  btn.disabled=true;btn.innerHTML='<span class="spin"></span> 面试官备题中…';
  try{
    const jd=document.getElementById("mockJd").value.trim();
    const r=await (await fetch("/api/mock/start",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({topic:document.getElementById("mockTopic").value,
        n_questions:+document.getElementById("mockN").value,
        jd_id:jd?+jd:null})})).json();
    if(r.error){toast(r.error);}else{
      mockSid=r.session_id;
      document.getElementById("mockSetup").style.display="none";
      document.getElementById("mockArea").style.display="block";
      document.getElementById("mockReport").style.display="none";
      document.getElementById("mockChat").innerHTML="";
      document.getElementById("mockInputRow").style.display="block";
      addBubble("q","🎤 面试官 · 开场",r.opening+"\n\n**考察方向**："+r.focus_areas.join("、"));
      updateMockHead(r.qno,r.difficulty);
      addBubble("q","🎤 面试官 · Q"+r.qno,r.question);
    }
  }catch(e){toast("开始失败："+e.message);}
  btn.disabled=false;btn.textContent="开始面试";
};
function updateMockHead(qno,diff){
  document.getElementById("mockProgress").textContent="Q"+qno;
  document.getElementById("mockDiff").innerHTML=diffBar(diff);
}
document.getElementById("mockSendBtn").onclick=async()=>{
  const ta=document.getElementById("mockAns"),ans=ta.value.trim();
  if(!ans){toast("先写回答");return;}
  if(!mockSid)return;
  addBubble("a","🧑 我",ans);ta.value="";
  document.getElementById("oralCoach").innerHTML="";
  const btn=document.getElementById("mockSendBtn");
  btn.disabled=true;btn.innerHTML='<span class="spin"></span> 面试官核对中…';
  try{
    const r=await (await fetch("/api/mock/answer",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({session_id:mockSid,answer:ans})})).json();
    if(r.error){toast(r.error);}else{
      const sc=r.scores||{};
      let dims=Object.keys(sc).map(k=>`<span class="dim">${k} <b>${sc[k]}</b></span>`).join("");
      let extra="";
      if((r.factual_issues||[]).length)extra+=`<div class="fb">⚠ 事实核对：${r.factual_issues.join("；")}</div>`;
      if((r.missing||[]).length)extra+=`<div class="muted">漏点：${r.missing.join("、")}</div>`;
      const cites=(r.citations||[]).length?`<div class="chips">${r.citations.slice(0,4).map(c=>`<span class="chip click" onclick="showChunk(${c.chunk_id})" title="点击看原文片段">chunk_${c.chunk_id}·${c.source}</span>`).join("")}</div>`:"";
      const b=addBubble("q","🎤 面试官 · 点评（"+r.avg+"/5 "+(r.verdict||"")+"）",r.feedback||"");
      b.innerHTML+=`<div class="score5">${dims}</div>${extra}${cites}`;
      if(r.done){
        addBubble("q","🎤 面试官","好，今天就到这。点上面「结束并出报告」看复盘。");
        document.getElementById("mockInputRow").style.display="none";
      }else{
        updateMockHead(r.qno,r.difficulty);
        addBubble("q","🎤 面试官 · "+(r.is_followup?"追问":"Q"+r.qno),r.next_question);
      }
    }
  }catch(e){toast("提交失败："+e.message);}
  btn.disabled=false;btn.textContent="提交回答";
};
// 语音：录音 → ASR → 填入 + 口语教练
let mediaRec=null,recChunks=[],recording=false;
document.getElementById("recBtn").onclick=async()=>{
  const btn=document.getElementById("recBtn"),st=document.getElementById("recStatus");
  if(!recording){
    try{
      const stream=await navigator.mediaDevices.getUserMedia({audio:true});
      mediaRec=new MediaRecorder(stream);recChunks=[];
      mediaRec.ondataavailable=e=>{if(e.data.size)recChunks.push(e.data);};
      mediaRec.onstop=async()=>{
        stream.getTracks().forEach(t=>t.stop());
        const blob=new Blob(recChunks,{type:"audio/webm"});
        st.innerHTML='<span class="spin"></span> 本地转写中…';
        const fd=new FormData();fd.append("file",blob,"answer.webm");
        try{
          const r=await (await fetch("/api/asr",{method:"POST",body:fd})).json();
          if(r.error){toast("转写失败："+r.error);st.textContent="";return;}
          const ta=document.getElementById("mockAns");
          ta.value=(ta.value?ta.value+" ":"")+r.text;
          st.textContent=`✓ 转写完成 (${r.device}·${r.duration}s)`;
          oralCoach(r.text);
        }catch(e){toast("转写失败："+e.message);st.textContent="";}
      };
      mediaRec.start();recording=true;btn.textContent="⏹ 停止录音";btn.style.borderColor="var(--bad)";
      st.textContent="● 录音中…说完点停止";
    }catch(e){toast("无法录音："+e.message+"（需允许麦克风权限）");}
  }else{
    mediaRec.stop();recording=false;btn.textContent="🎤 口头作答";btn.style.borderColor="";
  }
};
async function oralCoach(text){
  const box=document.getElementById("oralCoach");box.innerHTML='<span class="muted">口语教练分析中…</span>';
  try{
    const r=await (await fetch("/api/mock/oral",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({text})})).json();
    const fillers=(r.fillers||[]).length?`口头禅：${r.fillers.join("、")}`:"口头禅：无";
    box.innerHTML=`<div class="fb" style="border-color:var(--accent)">🗣️ 口语教练 · 流畅度 ${r.fluency}/5 · 啰嗦度 ${r.verbosity}/5 · ${fillers}`+
      (r.polished?`<div style="margin-top:6px"><b>更接地气的说法：</b>${r.polished}</div>`:"")+`</div>`;
  }catch(e){box.innerHTML="";}
}
document.getElementById("mockFinishBtn").onclick=async()=>{
  if(!mockSid)return;
  const r=await (await fetch(`/api/mock/${mockSid}/finish`,{method:"POST"})).json();
  if(r.error){toast(r.error);return;}
  const d=document.getElementById("mockReport");d.style.display="block";
  const dims=r.dim_avg||{};
  const radar=Object.keys(dims).map(k=>{
    const v=dims[k],c=v>=4?"var(--ok)":v>=3?"var(--accent)":"var(--warn)";
    return `<div class="rd"><div class="muted">${k}</div><div class="v" style="color:${c}">${v}</div></div>`;}).join("");
  d.innerHTML=`<h2 style="margin-top:0">面试复盘 · 总分 <span style="color:${r.overall>=3?'var(--ok)':'var(--warn)'}">${r.overall}</span>/5</h2>`+
    `<div class="radar">${radar}</div>`+
    (r.summary?`<div class="fb">${r.summary}</div>`:"")+
    (r.weak_focus&&r.weak_focus.length?`<p class="muted">薄弱方向：${r.weak_focus.join("、")}</p>`:"")+
    `<h3 style="color:var(--accent)">行动建议</h3><ul>${(r.actions||[]).map(a=>`<li>${a}</li>`).join("")}</ul>`+
    (r.weak_qids&&r.weak_qids.length?`<button class="primary" id="reinforceBtn">一键强化薄弱题（生成答案+原子卡）</button><span id="reinfStatus" class="muted" style="margin-left:10px"></span>`:"");
  d.scrollIntoView({behavior:"smooth"});
  const rb=document.getElementById("reinforceBtn");
  if(rb)rb.onclick=async()=>{
    rb.disabled=true;rb.innerHTML='<span class="spin"></span> 生成中…';
    const x=await (await fetch(`/api/mock/${mockSid}/reinforce`,{method:"POST"})).json();
    document.getElementById("reinfStatus").textContent=`✅ 已为 ${x.reinforced} 道薄弱题生成 grounded 答案+卡片`;
    rb.disabled=false;rb.textContent="再次强化";
  };
};

loadHealth();
