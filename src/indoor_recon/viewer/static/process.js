// 过程模式：把活动时间线（scripts/activity_timeline.py 生成）排成一条视频时间轴，回放 agent 查证据、构建和诊断。
// 真实耗时被压缩：每个可见步骤（看图、一批证据查询、渲染、诊断、新版本）占固定几秒，agent 的说明作为字幕另成一轨。
// 画面只由时间 t 决定：?record=1 时关闭实时循环，由录制脚本逐帧调用 window.film.seek(t)。
// 参数：process=<timeline.json>、cut=highlights（只保留要点）|short（target 秒的建模过程，前面是感知参考）、speed（步骤节奏倍率）、
// orbit（度/秒）、shots=<json>（手动镜头）、title、record。

const OPS={evidence:'证据查询',build:'构建',render:'渲染检查',residuals:'残差诊断','check-geometry':'几何检查',compare:'射线对比'};
const QUERIES={crop:'照片裁剪',section:'剖面',raycast:'射线',plane:'平面拟合',pixel_rays:'像素射线',info:'数据概况'};
const INTRO=3.5,OUTRO=5,PANEL_HOLD=1.2;
// 短版结尾展示（FINALE 秒）：剖切从下层地面升到顶，同时整体环绕一圈
const FINALE=9;

const CSS=`
#pPanel{position:absolute;right:24px;top:24px;bottom:120px;width:40%;display:flex;flex-direction:column;gap:10px;opacity:0;pointer-events:none}
#pPanel .ttl{font-size:18px;font-weight:600;text-shadow:0 1px 3px #000}
#pPanel .ttl small{display:block;font-size:13px;font-weight:400;color:var(--mute);margin-top:2px}
#pPanel .imgs{flex:1;min-height:0;display:grid;gap:8px;align-content:start}
#pPanel figure{margin:0;background:#0b0f11e6;border:1px solid var(--line);border-radius:8px;overflow:hidden;display:flex;flex-direction:column;min-height:0}
#pPanel img{width:100%;min-height:0;flex:1;object-fit:contain;background:#000}
#pPanel figcaption{font-size:12px;color:var(--mute);padding:4px 8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#pSub{position:absolute;left:50%;bottom:64px;transform:translateX(-50%);max-width:70%;font-size:22px;line-height:1.5;text-align:center;
 padding:8px 18px;border-radius:10px;background:#0b0f11cc;opacity:0;pointer-events:none}
#pSub b{color:var(--accent);font-weight:600;margin-right:8px}#pSub b.k{color:#f0b060}
#pTick{position:absolute;left:18px;bottom:64px;font-size:13px;line-height:1.7;font-variant-numeric:tabular-nums;pointer-events:none;text-shadow:0 1px 2px #000}
#pTick .h{color:var(--mute);font-size:12px}
#pTick div{opacity:.55}#pTick div:last-child{opacity:1}
#pTick i{font-style:normal;color:var(--accent);margin-right:6px}
#pBar{position:absolute;left:18px;right:18px;bottom:22px;height:6px;border-radius:3px;background:#ffffff1a;cursor:pointer}
#pBar .fill{position:absolute;left:0;top:0;bottom:0;border-radius:3px;background:var(--accent)}
#pBar i{position:absolute;top:-14px;font-style:normal;font-size:11px;color:var(--mute);transform:translateX(-50%)}
#pBar i::after{content:'';position:absolute;left:50%;top:14px;width:2px;height:6px;background:#fff8}
#pTitle{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:10px;pointer-events:none;opacity:0;text-shadow:0 2px 8px #000}
#pTitle h2{margin:0;font-size:44px;letter-spacing:2px}#pTitle p{margin:0;font-size:18px;color:var(--mute)}
`;

const clamp=(v,a,b)=>Math.min(b,Math.max(a,v));
const smooth=f=>{f=clamp(f,0,1);return f*f*(3-2*f);};

function imageLabel(path){
 let m;
 if((m=path.match(/rgb\/(\d+)\.\w+$/)))return `原始照片 #${m[1]}`;
 if((m=path.match(/evidence\/([^/]+)\/crop\.png$/)))return `照片 ${m[1]}`;
 if((m=path.match(/evidence\/([^/]+)\/section\.png$/)))return `剖面 ${m[1]}`;
 if((m=path.match(/evidence\/([^/]+)\//)))return `证据 ${m[1]}`;
 if((m=path.match(/diag-(r\d+)-residuals\/(clusters|excess|plan)/)))return `${{clusters:'残差簇',excess:'穿透面',plan:'残差平面图'}[m[2]]} ${m[1]}`;
 if((m=path.match(/diag-(r\d+)-residuals\//)))return `残差拼图 ${m[1]}`;
 if((m=path.match(/render-([^/]+)\/review\.png$/)))return `渲染 ${m[1]}`;
 if((m=path.match(/work\/([^/]+)\.\w+$/)))return `分析图 ${m[1]}`;
 return path.split('/').pop();
}
// 说明文字去掉 Markdown，按句切成字幕
function sentences(text,max){
 const plain=text.replace(/^\s*[-*]\s+\[.*$/gm,'').replace(/\[([^\]]+)\]\([^)]*\)/g,'$1').replace(/\*\*|`|^#+\s*|^[-*]\s+/gm,'')
  .replace(/([：，、；。！？])\s*\n+\s*/g,'$1').replace(/\s*\n+\s*/g,'。');
 // 链接清单之类的碎片（“完整报告。”）不当字幕
 const parts=plain.split(/(?<=[。！？；])/).map(s=>s.replace(/^[。；\s]+/,'').trim()).filter(s=>s.replace(/[\s。；·]/g,'').length>=6);
 return parts.slice(0,max);
}

// 时间线里标了 redacted 的图片（拍到不想公开的对象）播放时略去，原文件不动
const shownImages=e=>(e.images||[]).filter(i=>!i.redacted);
const shownQueries=b=>b.items.filter(e=>!e.redacted);
const chunks=(text,max)=>sentences(text,max).map(t=>({text:t,d:clamp(t.length/9,1.8,4.5)}));
const median=a=>{a=a.filter(z=>z!=null).sort((x,y)=>x-y);return a.length?a[a.length>>1]:undefined;};

// 时间线 → 画面步骤（beats）：连续的证据查询并成一批，图片全被略去的看图步骤整步跳过
function steps(tl){
 const all=[],says=[];let batch=null;
 for(const e of tl.events){
  if(e.kind==='say'){says.push({e,anchor:all.length});continue;}
  if(e.kind==='run'&&e.op==='evidence'){
   if(batch&&e.t-batch.last<=20){batch.items.push(e);batch.last=e.t;continue;}
   batch={kind:'batch',e,items:[e],last:e.t};all.push(batch);continue;
  }
  batch=null;
  if(e.kind==='look'&&!shownImages(e).length)continue;
  all.push({kind:e.kind==='look'?'look':e.kind==='revision'?'revision':e.op==='render'?'render':'tick',e});
 }
 // 关注高度：undefined 沿用上一步，null 看整栋
 for(const b of all)b.focus=b.kind==='look'?median(shownImages(b.e).map(i=>i.focus_z)):
  b.kind==='batch'?median(shownQueries(b).map(e=>e.focus_z)):b.kind==='revision'?b.e.focus_z??null:undefined;
 return {all,says};
}
// 完整版：每个步骤按固定时长依次排开，说明作为字幕另成一轨
function scheduleFull(all,says,speed){
 const dur=b=>(b.kind==='batch'?1.2+.12*Math.min(shownQueries(b).length,12):b.kind==='look'?1+.9*Math.min(shownImages(b.e).length,4):
  {render:1.6,revision:3.4,tick:.8}[b.kind])/speed;
 let v=INTRO;
 for(const b of all){b.v0=v;v+=dur(b);b.v1=v;}
 const subs=[];let subEnd=0;
 says.forEach((s,i)=>{
  let at=Math.max(all[s.anchor]?.v0??v,subEnd);
  for(const c of chunks(s.e.text,i===says.length-1?5:3)){subs.push({v0:at,v1:at+c.d,text:c.text,e:s.e});at+=c.d;}
  subEnd=at;
 });
 return {visual:all,subs,duration:Math.max(v+OUTRO,subEnd+1.5)};
}
// 精简版：开头一批照片巡视，之后每个版本一组——本轮最后一次看实拍照片 → 新版本，
// 字幕取本轮构建前最后一条说明的前两句；组长按字幕需要延长，画面与字幕保持同步；结尾是最终说明
function scheduleHighlights(all,says,speed){
 const visual=[],subs=[];let v=INTRO;
 const place=(b,d)=>{b.v0=v;v+=d;b.v1=v;visual.push(b);};
 const caption=(say,max,at)=>{for(const c of say?chunks(say.e.text,max):[]){subs.push({v0:at,v1:at+c.d,text:c.text,e:say.e});at+=c.d;}return at;};
 const survey=all.find(b=>b.kind==='batch'&&shownQueries(b).some(e=>e.images.length));
 const firstRev=all.findIndex(b=>b.kind==='revision');
 if(survey&&all.indexOf(survey)<firstRev){const start=v;place(survey,3.2/speed);v=Math.max(v,caption(says[0],1,start)+.3);}
 let prev=-1;
 all.forEach((rev,ri)=>{
  if(rev.kind!=='revision')return;
  const looks=all.slice(prev+1,ri).filter(b=>b.kind==='look');
  const look=looks.filter(b=>shownImages(b.e).some(i=>/rgb\/|crop/.test(i.source||i.requested||''))).at(-1)||looks.at(-1);
  const t0=prev>=0?all[prev].e.t:-Infinity;
  const say=says.filter(s=>s.e.t>t0&&s.e.t<=rev.e.t&&s!==says[0]).at(-1);
  const need=chunks(say?.e.text||'',2).reduce((a,c)=>a+c.d,0)+.4,revDur=3.4/speed;
  const start=v;
  if(look)place(look,Math.max(2.6/speed,need-revDur));
  place(rev,Math.max(revDur,need-(v-start)));
  caption(say,2,start);prev=ri;
 });
 const last=says.at(-1),end=prev>=0&&last&&last.e.t>all[prev].e.t?caption(last,4,v+.4):v;
 return {visual,subs,duration:Math.max(v+OUTRO,end+1.5)};
}
// 一句短字幕：跳过“我会……”之类的打算，取第一句讲结果的话，过长时只留前几个分句
function brief(text,max=36){
 for(const said of sentences(text,8)){
  if(/^(我[也还]?(会|将|先|要|准备|继续)|接下来|下一步|先)/.test(said))continue;
  const parts=said.split(/(?<=[，；：、])/);let out='',k=0;
  for(;k<parts.length&&(!out||(out+parts[k]).length<=max);k++)out+=parts[k];
  if(k<parts.length&&/^\s*(但|不过|然而|却|可是)/.test(parts[k]))continue;  // 截掉转折会改变原意
  out=out.replace(/[，；：、]$/,'。');
  return {text:out,d:clamp(out.length/9,1.8,4.5)};
 }
 return null;
}
// 短版：先放感知模型的输出（SpatialLM 布局、Boxer 物体框），说明 Agent 拿它们当参考证据；之后是限定在 target 秒的
// 建模过程：照片巡视，每个版本快进式切换，不配照片面板；有 weight（改动量）时按它分时长，几何没变的版本只停最短时间。
// 字幕只取各版本前说明的第一句，上一条没播完就跳过，结尾留一句总结
const PERCEPTION={spatiallm:{name:'SpatialLM',d:4,text:n=>`从扫描推断房间布局：${n} 个墙、门、窗候选框。`},
 boxer:{name:'Boxer',d:4.5,text:n=>`在照片里检测家具和物体，融合成 ${n} 个 3D 框。`}};
const PERCEPTION_NOTE='这些是 Agent 的参考证据：逐一对照照片和扫描核实，再用参数化对象建模。';
function scheduleShort(all,says,target,perception=[]){
 const visual=[],subs=[];let v=INTRO;
 for(const p of perception){
  const k=PERCEPTION[p.backend],b={kind:'perception',backend:p.backend,count:p.count,e:{t:0},v0:v};
  v+=k.d;b.v1=v;visual.push(b);subs.push({v0:b.v0+.3,v1:b.v1-.2,text:k.text(p.count),who:k.name});
 }
 if(perception.length){const b={kind:'perception',backend:'all',e:{t:0},v0:v};v+=3.6;b.v1=v;visual.push(b);
  subs.push({v0:b.v0+.2,v1:b.v1+1.5,text:PERCEPTION_NOTE,who:'Agent'});}
 const revs=all.filter(b=>b.kind==='revision');
 const survey=all.find(b=>b.kind==='batch'&&shownQueries(b).some(e=>e.images.length));
 const surveyDur=survey&&all.indexOf(survey)<all.indexOf(revs[0])?2.5:0;
 if(surveyDur){survey.v0=v;v+=surveyDur;survey.v1=v;visual.push(survey);}
 const last=says.at(-1),closing=last&&revs.length&&last.e.t>revs.at(-1).e.t?brief(last.e.text):null;
 const budget=target-(closing?closing.d:0),MIN=.8,MAX=4;
 const weight=revs.map(r=>r.weight??1),spare=Math.max(budget-MIN*revs.length,0),sum=weight.reduce((a,b)=>a+b,0)||1;
 const lengths=weight.map((w,i)=>Math.max(clamp(MIN+spare*w/sum,MIN,MAX),revs[i].hold??0));  // hold：手动镜头要的最短停留
 let subEnd=0,prev=-Infinity;
 for(const [ri,rev] of revs.entries()){
  const per=lengths[ri];
  rev.v0=v;v+=per;rev.v1=v;visual.push(rev);
  const say=says.filter(s=>s.e.t>prev&&s.e.t<=rev.e.t&&s!==says[0]).at(-1);prev=rev.e.t;
  const c=say&&brief(say.e.text);
  if(c&&rev.v0>=Math.max(subEnd,subs.at(-1)?.v1??0)){const d=Math.min(c.d,per*2);subs.push({v0:rev.v0,v1:rev.v0+d,text:c.text,e:say.e});subEnd=rev.v0+d;}
 }
 const finale={v0:Math.max(v,subEnd)+.5};finale.v1=finale.v0+FINALE;
 if(closing)subs.push({v0:finale.v0+.5,v1:finale.v0+.5+Math.max(closing.d,4),text:closing.text,e:last.e});
 return {visual,subs,finale,duration:finale.v1+1};
}
function schedule(tl,speed,cut,target,perception){
 const {all,says}=steps(tl);
 const plan=cut==='short'?scheduleShort(all,says,target,perception):cut==='highlights'?scheduleHighlights(all,says,speed):scheduleFull(all,says,speed);
 return Object.assign(plan,{all,says});
}

export async function start(api,query){
 const {THREE,urlFor,esc}=api;
 const speed=+(query.get('speed')||1),orbit=+(query.get('orbit')||5),record=query.get('record')==='1';
 const tlPath=query.get('process');
 const tl=await (await fetch(urlFor(tlPath))).json();
 // 感知输出（短版开头）：时间线可用 perception 指明，默认取任务目录 job/ 下的 proposals.json；没有就跳过这段
 const short=query.get('cut')==='short',target=+(query.get('target')||30);
 const perception=[];
 if(short)for(const p of tl.perception||['spatiallm','boxer'].map(b=>({backend:b,path:`${tl.task}/job/${b}/proposals.json`}))){
  const d=await fetch(urlFor(p.path)).then(r=>r.ok?r.json():null).catch(()=>null);
  if(d?.candidates?.length&&PERCEPTION[p.backend])perception.push({backend:p.backend,candidates:d.candidates,count:d.candidates.length});
 }
 const plan=schedule(tl,speed,query.get('cut'),target,perception);
 // 多次会话合成的时间线去掉了会话间的空闲：钟点按所在会话的起点换算
 const sessions=(tl.sessions||[{t:0,start_utc:tl.start_utc}]).map(s=>({t:s.t,ms:Date.parse(s.start_utc)}));
 const clockAt=s=>{const k=sessions.findLast(x=>x.t<=s)||sessions[0];
  return new Date(k.ms+(s-k.t)*1000).toLocaleTimeString('zh-CN',{hour12:false});};
 const mmss=s=>`${Math.floor(s/60)}:${String(Math.floor(s%60)).padStart(2,'0')}`;

 const style=document.createElement('style');style.textContent=CSS;document.head.appendChild(style);
 const main=document.querySelector('main');
 main.insertAdjacentHTML('beforeend',`<div id="pPanel"><div class="ttl"></div><div class="imgs"></div></div><div id="pSub"></div>
  <div id="pTick"></div><div id="pBar"><div class="fill"></div></div><div id="pTitle"><h2></h2><p></p></div>`);
 const $=s=>main.querySelector(s);
 document.getElementById('msg').style.visibility='hidden';

 // 版本：诊断摘要来自 /api/revisions，模型全部预载，保证逐帧录制时切换是同步的
 const revs=plan.visual.filter(b=>b.kind==='revision');
 const listing=await (await fetch('/api/revisions?dir='+encodeURIComponent(tl.task))).json().catch(()=>({revisions:[]}));
 api.rev.list=listing.revisions||[];
 const entryOf=b=>api.rev.list.find(r=>r.path===b.e.model)||{revision:b.e.revision,path:b.e.model,mtime:Date.parse(tl.start_utc)/1000+b.e.t};
 await Promise.all(revs.map(b=>api.loader.loadAsync(urlFor(b.e.model)).then(g=>api.gltfCache.set(urlFor(b.e.model),g))));
 api.rev.dir=tl.task;api.rev.mode='process';

 // 取景：以最终版本的包围盒为中心环绕
 const finalScene=api.gltfCache.get(urlFor(revs.at(-1).e.model)).scene;
 const box=new THREE.Box3().setFromObject(finalScene),center=box.getCenter(new THREE.Vector3());
 const radius=box.getSize(new THREE.Vector3()).length()/2,dist=radius/Math.sin(THREE.MathUtils.degToRad(api.camera.fov/2))*1.02;
 api.controls.target.copy(center);
 // 右侧 40% 留给图片面板：投影中心左移，模型落在左侧画面中间（短版没有面板，不偏移）。
 // views[i]：第 i 步看哪里（改动处或整栋），换步骤时 1.2 s 平滑推拉
 // 短版全屏无面板：整栋可以再近一点（包围球留的余量用不上）
 const whole={target:center,dist:short?dist*.82:dist,el:34},offset=short?0:.2;let views=null;
 // 自动方位：跟随改动时不整圈环绕，只在 40° 两侧缓慢摆动（转到背面时近处的高墙会挡住改动处）；手动镜头可指定 az
 const sway=t=>views?40+30*Math.sin(orbit*t/30):40+orbit*t;
 // 镜头提前 LEAD 秒出发，新版本出现时已经到位（剖切同样提前）
 const LEAD=1;let fin=plan.finale;
 function placeCamera(t){
  let view=whole;
  // 结尾展示：方位从开始那一刻起转一整圈
  const ts=fin?Math.min(t,fin.v0):t,spin=fin?360*smooth((t-fin.v0)/FINALE):0;
  if(views){
   const tv=t+LEAD,i=plan.visual.findLastIndex(b=>b.v0<=tv),end=fin?fin.v0-.5:plan.visual.at(-1).v1;
   const from=i<=0?whole:views[i-1],to=i<0||t>=end+.5?whole:views[i];
   const f=smooth(t>=end+.5?(t-end-.5)/1.2:(tv-(plan.visual[Math.max(i,0)].v0))/1.2);
   const az0=from.az??sway(ts),az1=to.az??sway(ts);
   view={target:from.target.clone().lerp(to.target,f),dist:from.dist+(to.dist-from.dist)*f,el:from.el+(to.el-from.el)*f,az:az0+(az1-az0)*f};
  }
  const a=THREE.MathUtils.degToRad((view.az??sway(ts))+spin),el=THREE.MathUtils.degToRad(view.el),d=view.dist,c=view.target;
  api.camera.position.set(c.x+d*Math.cos(el)*Math.cos(a),c.y+d*Math.sin(el),c.z+d*Math.cos(el)*Math.sin(a));
  api.camera.lookAt(c);
  const w=api.renderer.domElement.clientWidth,h=api.renderer.domElement.clientHeight;
  api.camera.near=d/300;api.camera.far=dist*20;
  // 短版开头巡视照片时也有面板，第一个版本出现后再回中
  const survey=plan.visual.find(b=>b.kind==='batch'),firstV=plan.visual.find(b=>b.kind==='revision')?.v0??0;
  const shift=offset||(views&&survey?.2*Math.min(smooth((t-survey.v0)/.6),1-smooth((t-firstV)/1.2)):0);
  api.camera.setViewOffset(w,h,w*shift,0,w,h);
 }

 // 输入扫描：源坐标（声明的 Z 向上）→ 工作坐标 → glTF；只在首个版本出现前显示
 let scan=null;
 if(tl.scan){
  const g=await api.loader.loadAsync(urlFor(tl.scan.path));scan=g.scene;
  const s=tl.scan.metres_per_unit,T=new THREE.Matrix4().set(...tl.scan.T_work_from_source_m.flat());
  const gltfFromWork=new THREE.Matrix4().set(1,0,0,0, 0,0,1,0, 0,-1,0,0, 0,0,0,1);
  scan.matrixAutoUpdate=false;scan.matrix.copy(gltfFromWork).multiply(T).multiply(new THREE.Matrix4().makeScale(s,s,s));
  scan.traverse(n=>{if(n.isMesh){n.material.side=THREE.FrontSide;n.material.transparent=true;n.raycast=()=>{};}});
  for(const box of tl.redaction?.boxes_work_m||[])hideInScan(scan,box,gltfFromWork.clone().invert());
  api.scene.add(scan);
 }
 // 略去对象：扫描里顶点落在其包围盒（外扩 0.3 m，宁多勿少）内的三角形不画，只改显示用的索引
 function hideInScan(root,[lo,hi],workFromGltf){
  const zone=new THREE.Box3(new THREE.Vector3(...lo).subScalar(.3),new THREE.Vector3(...hi).addScalar(.3)),v=new THREE.Vector3();
  root.updateMatrixWorld(true);let dropped=0;
  root.traverse(n=>{
   if(!n.isMesh)return;
   const M=workFromGltf.clone().multiply(n.matrixWorld),g=n.geometry,pos=g.attributes.position;
   const inside=new Uint8Array(pos.count);for(let i=0;i<pos.count;i++)inside[i]=zone.containsPoint(v.fromBufferAttribute(pos,i).applyMatrix4(M));
   const idx=g.index?.array,count=idx?idx.length:pos.count,keep=new Uint32Array(count);let k=0;
   for(let t=0;t<count;t+=3){
    const a=idx?idx[t]:t,b=idx?idx[t+1]:t+1,c=idx?idx[t+2]:t+2;
    if(inside[a]||inside[b]||inside[c]){dropped++;continue;}
    keep[k++]=a;keep[k++]=b;keep[k++]=c;
   }
   g.setIndex(new THREE.BufferAttribute(keep.slice(0,k),1));
  });
  console.info(`process: 扫描略去 ${dropped} 个三角形`);
 }

 // 自动剖切：画面关注的高度在某层楼板之下时，剖到那层楼板底面以下，露出下层；换步骤时 0.7 s 平滑移动
 const Tf=api.gltfCache.get(urlFor(revs.at(-1).e.model)).userData?.T_work_from_gltf;
 const workFromModel=Array.isArray(Tf)?new THREE.Matrix4().set(...Tf.flat()):new THREE.Matrix4();
 const levels=[];
 finalScene.updateMatrixWorld(true);
 finalScene.traverse(n=>{
  const u=n.userData||{};if(!u.hs_id||(u.hs_category||u.hs_id.split('/').slice(-2,-1)[0])!=='floor')return;
  const b=new THREE.Box3().setFromObject(n).applyMatrix4(workFromModel);
  const l=levels.find(l=>Math.abs(l.top-b.max.z)<.3);
  if(l)l.bottom=Math.min(l.bottom,b.min.z);else levels.push({top:b.max.z,bottom:b.min.z});
 });
 levels.sort((a,b)=>a.top-b.top);
 const modelTop=new THREE.Box3().setFromObject(finalScene).applyMatrix4(workFromModel).max.z+.05;
 const clipFor=f=>{if(f==null)return modelTop;const l=levels.find(l=>l.bottom>f);return l?l.bottom-.02:modelTop;};
 const focusAt=i=>{for(;i>=0;i--)if(plan.visual[i].focus!==undefined)return plan.visual[i].focus;return null;};
 // 每版改了什么、在哪：相邻两版逐对象比较包围盒、顶点数和材质（只比没有子对象的叶子对象）。
 // camera=changes（短版默认）时镜头推到改动处，剖切跟随改动所在楼层；改动遍布全屋时看整栋
 const followChanges=(query.get('camera')||(query.get('cut')==='short'?'changes':'orbit'))==='changes';
 function inventory(scene){
  scene.updateMatrixWorld(true);const items=new Map();
  scene.traverse(n=>{
   const id=n.userData?.hs_id;if(!id||items.has(id))return;
   let leaf=true,verts=0;const mats=[];
   n.traverse(c=>{if(c!==n&&c.userData?.hs_id)leaf=false;if(c.isMesh){verts+=c.geometry.attributes.position.count;mats.push([c.material].flat().map(m=>m.name).join('/'));}});
   if(!leaf||!verts)return;
   const b=new THREE.Box3().setFromObject(n);
   items.set(id,{box:b,sig:`${verts}|${mats.join(',')}|${b.min.toArray().map(x=>x.toFixed(3))}|${b.max.toArray().map(x=>x.toFixed(3))}`});
  });
  return items;
 }
 // 改动按位置聚团（相距 1.5 m 内归一团），镜头看对象最多的一团
 function mainCluster(boxes){
  const groups=boxes.map(b=>({area:b.clone(),n:1}));
  for(let merged=true;merged;){
   merged=false;
   for(let i=0;i<groups.length&&!merged;i++)for(let j=i+1;j<groups.length;j++){
    if(groups[i].area.clone().expandByScalar(.75).intersectsBox(groups[j].area.clone().expandByScalar(.75))){
     groups[i].area.union(groups[j].area);groups[i].n+=groups[j].n;groups.splice(j,1);merged=true;break;}
   }
  }
  return groups.reduce((m,g)=>!m||g.n>m.n?g:m,null);
 }
 if(followChanges){
  let before=new Map();
  const shots=query.get('shots')?await (await fetch(urlFor(query.get('shots')))).json():{};
  for(const rev of revs){
   const now=inventory(api.gltfCache.get(urlFor(rev.e.model)).scene),boxes=[];
   for(const [id,it] of now)if(before.get(id)?.sig!==it.sig)boxes.push(it.box);
   for(const [id,it] of before)if(!now.has(id))boxes.push(it.box);
   const count=boxes.length,main=count?mainCluster(boxes):null;
   rev.change={area:main?.area,count,everywhere:!before.size||count>.5*now.size};
   rev.weight=count?Math.sqrt(Math.min(count,60)):0;
   before=now;
  }
  for(const rev of revs)rev.hold=shots['r'+rev.e.revision]?.hold;
  if(short){Object.assign(plan,scheduleShort(plan.all,plan.says,target,perception));fin=plan.finale;}
  let last=whole;
  views=plan.visual.map(b=>{
   const c=b.change;if(!c)return last;
   if(c.everywhere)return last=whole;
   // 停留太短的小改动不挪镜头：连着几个 1 s 内的推拉只会让画面晃
   if(!c.count||b.v1-b.v0<1.5)return last;
   const r=Math.max(c.area.getSize(new THREE.Vector3()).length()/2,2.6);
   // 近景从斜上方往下看进房间，少被近处的墙和楼板挡住；改动范围大到近景不比整栋近时就看整栋
   const d=r/Math.sin(THREE.MathUtils.degToRad(api.camera.fov/2))*1.25;
   return last=d>=.8*whole.dist?whole:{target:c.area.getCenter(new THREE.Vector3()),dist:d,el:55,area:c.area};
  });
  // 手动镜头 shots=<json>：{"r8":{"whole":true 从整栋视角出发,"target":[x,y,z] 工作坐标,"dist":m,"el":度,"az":度,
  // "floor":剖切所看的工作 z,"hold":最短停留秒数}}，未给的字段沿用自动值
  const modelFromWork=workFromModel.clone().invert();
  plan.visual.forEach((b,i)=>{
   const s=b.kind==='revision'&&shots['r'+b.e.revision];if(!s)return;
   const v={...(s.whole?whole:views[i])};
   if(s.target)v.target=new THREE.Vector3(...s.target).applyMatrix4(modelFromWork);
   for(const k of ['dist','el','az'])if(s[k]!=null)v[k]=s[k];
   if(s.floor!==undefined)v.floor=s.floor;
   views[i]=v;
  });
 }
 // 每步的剖切高度。Agent 常在两层之间来回查几眼，逐步跟随会让画面不停切层：停留不足 dwell 秒的
 // 片段并入相邻较长的片段，只有在一层持续工作时才切换。clip=off 全程看整栋。
 const clipMode=query.get('clip')||'auto',dwell=+(query.get('dwell')||8);
 // 跟随改动时：剖到改动团中心所在那层，镜头和剖切一起切换，不再按停留时间合并
 const changeClip=v=>{if(v.floor!==undefined)return clipFor(v.floor);if(!v.area)return modelTop;const w=v.area.clone().applyMatrix4(workFromModel);
  return clipFor((w.min.z+w.max.z)/2);};
 const stable=plan.visual.map((b,i)=>clipMode==='off'?modelTop:views?changeClip(views[i]):clipFor(focusAt(i)));
 for(;!views;){
  const runs=[];
  stable.forEach((h,i)=>{const r=runs.at(-1);if(r&&r.h===h)r.end=i;else runs.push({h,start:i,end:i});});
  if(runs.length<2)break;
  for(const r of runs)r.d=plan.visual[r.end].v1-plan.visual[r.start].v0;
  const short=runs.reduce((m,r)=>r.d<dwell&&(!m||r.d<m.d)?r:m,null);if(!short)break;
  const k=runs.indexOf(short),prev=runs[k-1],next=runs[k+1];
  const into=!prev?next:!next?prev:(prev.d>=next.d?prev:next);
  for(let i=short.start;i<=short.end;i++)stable[i]=into.h;
 }
 // 结尾展示：先剖到下层地面以上一点，再随环绕升到顶
 const sweepFrom=levels.length?levels[0].top+.1:modelTop;
 function clipAt(t){
  if(fin&&t>=fin.v0){
   const cur=stable.at(-1),k=t-fin.v0;
   return k<.7?cur+(sweepFrom-cur)*smooth(k/.7):sweepFrom+(modelTop-sweepFrom)*smooth((k-.7)/(FINALE-1.5));
  }
  const tc=t+(views?LEAD:0),i=plan.visual.findLastIndex(b=>b.v0<=tc);if(i<0)return modelTop;
  // 最后一步之后是总结：回到整栋
  const end=plan.visual.at(-1).v1,closing=!fin&&t>=end;
  const from=closing?stable[i]:i>0?stable[i-1]:modelTop,to=closing?modelTop:stable[i];
  return from+(to-from)*smooth(((closing?t:tc)-(closing?end:plan.visual[i].v0))/.7);
 }
 console.info(`process: 剖切切换 ${stable.filter((h,i)=>i&&h!==stable[i-1]).length} 次（dwell ${dwell} s）`);
 const applyClip=t=>{const h=clipAt(t);api.setClipHeight(h>=modelTop-1e-3?null:h);};
 const firstRev=revs[0]?.v0??plan.duration;
 // 感知框：工作坐标的有向包围盒画成线框，SpatialLM 暖色（门窗浅黄）、Boxer 青色；中心落在略去区域的框不画。
 // 各自在自己那段淡入，之后一直留到第一个版本出现时与扫描一起淡出；这期间扫描压暗，框更清楚
 const overlays={},modelFromWork=workFromModel.clone().invert();
 const zones=(tl.redaction?.boxes_work_m||[]).map(([lo,hi])=>new THREE.Box3(new THREE.Vector3(...lo).subScalar(.3),new THREE.Vector3(...hi).addScalar(.3)));
 for(const p of perception){
  const g=new THREE.Group();
  for(const c of p.candidates){
   const geo=c.geometry;if(geo?.type!=='obb')continue;
   const o=new THREE.Vector3(...geo.center_m);if(zones.some(z=>z.containsPoint(o)))continue;
   const R=geo.R_work_from_object,M=new THREE.Matrix4().set(R[0][0],R[0][1],R[0][2],o.x, R[1][0],R[1][1],R[1][2],o.y, R[2][0],R[2][1],R[2][2],o.z, 0,0,0,1);
   const colour=p.backend==='boxer'?0x4fd1c5:c.category==='wall'?0xf0a040:0xffe08a;
   const line=new THREE.LineSegments(new THREE.EdgesGeometry(new THREE.BoxGeometry(...geo.size_m.map(x=>Math.max(x,.02)))),
    new THREE.LineBasicMaterial({color:colour,transparent:true,opacity:0,depthTest:false}));
   line.matrixAutoUpdate=false;line.matrix.copy(modelFromWork).multiply(M);line.renderOrder=10;line.raycast=()=>{};g.add(line);
  }
  g.visible=false;api.scene.add(g);overlays[p.backend]=g;
 }
 const perceptionBeats=plan.visual.filter(b=>b.kind==='perception'),perceptionEnd=perceptionBeats.at(-1)?.v1;
 function showPerception(t){
  const out=1-smooth((t-firstRev)/1.2);
  for(const [k,g] of Object.entries(overlays)){
   const b=perceptionBeats.find(b=>b.backend===k);
   const o=b?Math.min(smooth((t-b.v0)/.8),out)*(k==='spatiallm'?.95:.8):0;
   g.visible=o>0;for(const l of g.children)l.material.opacity=o;
  }
 }
 function scanOpacity(t){
  if(!scan)return;
  // 感知段扫描调暗（颜色乘系数，不透明，免得透出墙后），之后恢复
  const dim=perceptionBeats.length?.45+.55*smooth(t-perceptionEnd):1;
  scan.traverse(n=>{if(n.isMesh)n.material.color.setScalar(dim);});
  const o=1-smooth((t-firstRev)/1.2);scan.visible=o>0;
  scan.traverse(n=>{if(n.isMesh){n.material.opacity=o;n.material.depthWrite=o>=1;}});
 }

 const batchFigs=b=>shownQueries(b).filter(e=>e.images.length).slice(0,12).map(e=>({path:e.images[0],label:e.name}));
 // 图片全部预载（录制时截图前必须已解码）
 const imgCache=new Map();
 const imagePaths=new Set();
 for(const b of plan.visual){
  if(b.kind==='look')shownImages(b.e).slice(0,4).forEach(i=>imagePaths.add(i.path));
  if(b.kind==='render')b.e.images.forEach(p=>imagePaths.add(p));
  if(b.kind==='batch')batchFigs(b).forEach(f=>imagePaths.add(f.path));
 }
 await Promise.all([...imagePaths].map(p=>{const im=new Image();im.src=urlFor(p);imgCache.set(p,im);return im.decode().catch(()=>{});}));

 // 画面步骤 → 面板内容
 function panelFor(b){
  if(b.kind==='look'){
   const shown=shownImages(b.e),figs=shown.slice(0,4).map(i=>({path:i.path,label:imageLabel(i.source||i.requested||i.path)}));
   return {title:'Agent 查看图片',sub:figs.length<shown.length?`共 ${shown.length} 张`:'',figs,cols:figs.length>1?2:1};
  }
  if(b.kind==='render')return {title:'渲染检查',sub:b.e.name,figs:b.e.images.slice(0,1).map(p=>({path:p,label:b.e.name})),cols:1};
  if(b.kind==='batch'){
   const counts={};for(const e of b.items)counts[e.query]=(counts[e.query]||0)+1;
   const figs=batchFigs(b);
   return {title:`证据查询 ×${b.items.length}`,sub:Object.entries(counts).map(([k,n])=>`${QUERIES[k]||k} ${n}`).join(' · '),figs,
    cols:figs.length>6?4:figs.length>2?3:Math.max(figs.length,1)};
  }
  return null;
 }
 let panelKey=null;
 function showPanel(t){
  const el=$('#pPanel');
  const b=plan.visual.findLast(b=>b.v0<=t&&panelFor(b)&&t<b.v1+PANEL_HOLD);
  if(!b){el.style.opacity=0;panelKey=null;return;}
  el.style.opacity=Math.min(smooth((t-b.v0)/.25),1-smooth((t-(b.v1+PANEL_HOLD-.3))/.3));
  if(panelKey===b)return;panelKey=b;
  const p=panelFor(b);
  $('#pPanel .ttl').innerHTML=`${esc(p.title)}${p.sub?`<small>${esc(p.sub)}</small>`:''}`;
  const imgs=$('#pPanel .imgs');imgs.style.gridTemplateColumns=`repeat(${p.cols},1fr)`;
  const rows=Math.ceil(p.figs.length/p.cols);imgs.style.gridTemplateRows=`repeat(${rows},minmax(0,${rows>1?'1fr':'auto'}))`;
  imgs.replaceChildren(...p.figs.map(f=>{
   const fig=document.createElement('figure');const im=imgCache.get(f.path)?.cloneNode()||Object.assign(new Image(),{src:urlFor(f.path)});
   const cap=document.createElement('figcaption');cap.textContent=f.label;fig.append(im,cap);return fig;
  }));
 }
 function opText(b){
  const e=b.e;
  if(b.kind==='batch')return `证据查询 ×${b.items.length}`;
  if(b.kind==='look')return `查看图片 ×${shownImages(e).length}`;
  if(b.kind==='revision')return `构建 r${e.revision}`;
  if(b.kind==='finale')return '最终模型展示';
  if(b.kind==='perception')return b.backend==='all'?'感知结果 → 参考证据':`${PERCEPTION[b.backend].name} ×${b.count}`;
  return `${OPS[e.op]||e.op}${e.failed?'失败':''}${e.revision?` r${e.revision}`:''} · ${e.name}`;
 }
 function showTicker(t){
  const done=plan.visual.filter(b=>b.v0<=t);const cur=done.at(-1);
  if(fin&&t>=fin.v0)done.push({kind:'finale',e:cur.e});
  const real=cur?cur.e.t:0;
  $('#pTick').innerHTML=`<span class="h">实际时间 ${clockAt(real)} · 已用 ${mmss(real)} / ${mmss(tl.duration_s)}</span>`+
   done.slice(-4).map(b=>`<div><i>${clockAt(b.e.t).slice(0,5)}</i>${esc(opText(b))}</div>`).join('');
 }
 function showSub(t){
  const s=plan.subs.find(s=>s.v0<=t&&t<s.v1),el=$('#pSub');
  if(!s){el.style.opacity=0;return;}
  el.style.opacity=Math.min(smooth((t-s.v0)/.2),smooth((s.v1-t)/.2));
  if(el.dataset.v!==String(s.v0)){el.dataset.v=String(s.v0);el.innerHTML=`<b class="${s.who&&s.who!=='Agent'?'k':''}">${esc(s.who||'Agent')}</b>${esc(s.text)}`;}
 }
 // 多次会话合成的短版：最后一条说明只讲最后一次会话（“本轮共 6 版”），结尾改为全程总结
 const closing=plan.subs.at(-1);
 if(short&&tl.sessions?.length>1&&closing?.e&&closing.e===plan.says.at(-1)?.e)
  closing.text=`最终交付 r${revs.at(-1).e.revision}：共 ${revs.length} 个版本，实际用时 ${Math.round(tl.duration_s/60)} 分钟。`;
 const title=query.get('title')||'结构化重建过程';
 $('#pTitle h2').textContent=title;$('#pTitle p').textContent=`${perception.length?'感知参考 → ':''}输入扫描 → 查证据 → 构建 → 诊断 · 实际 ${Math.round(tl.duration_s/60)} 分钟，${revs.length} 个版本`;
 function showTitle(t){$('#pTitle').style.opacity=Math.min(smooth(t/.6),1-smooth((t-INTRO+.8)/.8));}
 $('#pBar').insertAdjacentHTML('beforeend',revs.map(b=>`<i style="left:${b.v0/plan.duration*100}%">r${b.e.revision}</i>`).join(''));
 function showBar(t){$('#pBar .fill').style.width=`${clamp(t/plan.duration,0,1)*100}%`;}

 // 状态推进
 let shown=-1,last=0;
 async function update(t){
  const dt=t-last;last=t;const instant=dt<0||dt>1;
  const ri=revs.findLastIndex(b=>b.v0<=t);
  if(ri!==shown){shown=ri;if(ri>=0)await api.showRevision(entryOf(revs[ri]));}
  placeCamera(t);applyClip(t);api.updateCutaway(instant);scanOpacity(t);showPerception(t);
  showPanel(t);showTicker(t);showSub(t);showTitle(t);showBar(t);
  return instant?1:Math.max(dt,0);
 }
 const film={state:{ready:false,duration:plan.duration,t:0,playing:!record},plan,api,
  async seek(t){const dt=await update(t);film.state.t=t;api.frame(dt);}};
 window.film=film;
 if(record)api.setManual(true);
 else{
  let busy=false;
  api.frameHooks.push(dt=>{
   if(busy)return;
   if(film.state.playing)film.state.t=Math.min(film.state.t+dt,plan.duration);
   busy=true;update(film.state.t).finally(()=>{busy=false;});
  });
  addEventListener('keydown',e=>{
   if(e.key===' '){e.preventDefault();film.state.playing=!film.state.playing;}
   else if(e.key==='ArrowRight')film.state.t=Math.min(film.state.t+5,plan.duration);
   else if(e.key==='ArrowLeft')film.state.t=Math.max(film.state.t-5,0);
  });
  $('#pBar').onclick=e=>{const r=e.currentTarget.getBoundingClientRect();film.state.t=(e.clientX-r.left)/r.width*plan.duration;};
 }
 await film.seek(0);film.state.ready=true;
}
