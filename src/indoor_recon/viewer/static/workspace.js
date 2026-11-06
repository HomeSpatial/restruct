// Read-only task workspace. All geometry is displayed in Y-up metres.
import {LineSegments2} from 'three/addons/lines/LineSegments2.js';
import {LineSegmentsGeometry} from 'three/addons/lines/LineSegmentsGeometry.js';
import {LineMaterial} from 'three/addons/lines/LineMaterial.js';

const $=s=>document.querySelector(s);
const LABELS={wall:'墙',door:'门',window:'窗',floor:'地面',ceiling:'天花',stairs:'楼梯',stair:'楼梯',chair:'椅子',table:'桌子',desk:'书桌',sofa:'沙发',bed:'床',cabinet:'柜体',shelf:'置物架',tv:'显示屏',sink:'水槽',toilet:'马桶',refrigerator:'冰箱',lamp:'灯',assembly:'组合对象',armchair:'扶手椅',countertop:'台面',dishwasher:'洗碗机',heater:'暖气设备',microwave:'微波炉',monitor:'显示器',railing:'栏杆',range_hood:'抽油烟机',spandrel:'墙带',wall_panel:'墙板',wardrobe:'衣柜',washing_machine:'洗衣设备'};
Object.assign(LABELS,{'monitor_(computer_equipment) computer_monitor':'电脑显示器','speaker_(stero_equipment)':'音箱',headset:'耳机',printer:'打印机',fan:'风扇',box:'盒子',storage:'储物柜',bookcase:'书柜',air_conditioner:'空调','stereo_(sound_system)':'音响',electrical_outlet:'插座',remote_control:'遥控器',trash_can:'垃圾桶',drawer:'抽屉',light_switch:'开关','router_(computer_equipment)':'路由器',vent:'通风口',projector:'投影仪',table_lamp:'台灯',smoke_detector:'烟雾报警器',bathrobe:'浴袍',curtain:'窗帘',laptop_computer:'笔记本电脑',coffee_table:'茶几',stool:'凳子',automatic_washer:'洗衣机',towel:'毛巾',coatrack:'衣帽架',washer_dryer:'洗烘设备',towel_rack:'毛巾架',cooker:'灶具',hand_towel:'手巾',toaster:'烤面包机',pot:'锅',blender:'搅拌机',kettle:'水壶',kitchen_sink:'厨房水槽',microwave_oven:'微波炉'});
const label=c=>LABELS[c]||c;
const STATES={not_started:'尚未开始',skipped:'未启用 / 无输入',partial:'已有部分检查点',failed:'运行失败',complete:'结果完成',invalid:'结果不匹配',available:'可用',unavailable:'不可用'};
const layerNames={scan:'扫描参考',spatiallm:'SpatialLM 布局',boxer:'Boxer 物体',model:'结构模型'};
const colors={scan:'#a1b1b2',spatiallm:'#74c6ff',boxer:'#ffbd72',model:'#3fd0b6'};

async function json(url){const response=await fetch(url);if(!response.ok)throw new Error(`读取失败（${response.status}）`);return response.json();}
function parse(v){if(typeof v!=='string')return v;try{return JSON.parse(v);}catch{return v;}}

export async function startWorkspace(api,query){
 const listing=await json('/api/tasks');
 const requested=query.get('task')??query.get('follow');
 if(!listing.tasks.length&&!query.has('task'))return false;
 if(query.has('follow')&&!listing.tasks.some(t=>t.id===requested))return false;
 const {THREE,state,esc}=api;
 const workFromView=new THREE.Matrix4().set(1,0,0,0, 0,0,-1,0, 0,1,0,0, 0,0,0,1);
 const viewFromWork=workFromView.clone().invert();
 const layers=Object.fromEntries(Object.keys(layerNames).map(k=>[k,{object:null,on:k==='model',opacity:k==='scan'?.4:1}]));
 let data=null,task='',generation=0,selected=null,entries=[],assemblies=[],mode='model',rawFrame=null;
 let candidateKey='',revisionKey='',polling=false,loadingRevision=false,photoGeneration=0,pendingTask=null,scanLoading=null;
 const selectionOutline=new THREE.Group();selectionOutline.name='workspace-selection';api.scene.add(selectionOutline);
 document.body.classList.add('task-mode');
 $('header').insertAdjacentHTML('afterbegin','<button class="btn panel-toggle" id="w-left" aria-label="折叠或展开任务面板">任务 / 图层</button>');
 $('header').insertAdjacentHTML('beforeend','<button class="btn panel-toggle" id="w-right" aria-label="折叠或展开对象面板">对象 / 证据</button>');
 $('#w-left').onclick=()=>document.body.classList.toggle('left-closed');
 $('#w-right').onclick=()=>document.body.classList.toggle('right-closed');
 document.querySelector('aside').insertAdjacentHTML('beforeend',`<div class="workspace-panel">
  <h2>重建工作区</h2><label for="w-task">任务</label><select id="w-task" aria-label="任务">${listing.tasks.map(t=>`<option value="${esc(t.id)}">${esc(t.label||t.scene_id+' · '+t.id)}</option>`).join('')}</select>
  <div class="muted">米制坐标 · 网格间距 1 m<br>识别候选仅供核对，尚未人工确认。</div>
  <h2>四层对照</h2>${Object.entries(layerNames).map(([k,n])=>`<div class="layer-card"><label><input type="checkbox" data-layer="${k}" ${layers[k].on?'checked':''}><i class="dot" style="--color:${colors[k]}"></i><strong>${n}</strong></label><small id="w-${k}-status">等待任务</small><div class="opacity"><span>透明度</span><input aria-label="${n}透明度" data-opacity="${k}" type="range" min="0" max="1" step=".05" value="${layers[k].opacity}"><output>${Math.round(layers[k].opacity*100)}%</output></div></div>`).join('')}
  <label>楼层 <select id="w-floor" aria-label="楼层"><option value="">全部楼层</option></select></label>
  <div class="muted">楼层按工作高度筛选对象；扫描保留完整几何，可用下方剖切查看。</div>
  <div class="muted" id="w-status"></div><a class="muted" href="?legacy=1">打开 GLB 文件浏览</a></div>`);
 document.querySelector('aside.right').insertAdjacentHTML('beforeend',`<div class="workspace-panel">
  <h2>对象与证据</h2><div class="segmented" id="w-modes"><button data-mode="model" class="on">结构对象</button><button data-mode="spatiallm">布局候选</button><button data-mode="boxer">物体候选</button></div>
  <input type="search" id="w-search" aria-label="搜索对象" placeholder="搜索名称、类别或 ID">
  <select id="w-category" aria-label="对象类别"><option value="">全部类别</option></select>
  <div id="w-boxer-controls" hidden><label>结果 <select id="w-boxer-mode" aria-label="Boxer结果"><option value="fused">融合物体</option><option value="raw">逐帧原始检测</option></select></label>
   <label>最低分数 <input id="w-score" aria-label="最低预测分数" type="range" min="0" max="1" value="0" step=".05"><output id="w-score-value">0.00</output></label>
   <label id="w-support-row">支持帧数 ≥ <input id="w-support" aria-label="最少支持帧数" type="number" value="1" min="1" style="width:65px"></label>
   <select id="w-raw-frame" aria-label="原始检测帧" hidden></select></div>
  <div id="w-count" class="muted"></div><div id="w-list"></div><div id="w-detail"><div class="muted">点选模型、识别框或列表，检查尺寸与来源。</div></div></div>`);
 $('main').insertAdjacentHTML('beforeend',`<div id="w-error" role="alert"></div><div id="w-timeline"><span>结构版本</span><select id="w-revision" aria-label="结构版本"></select><label><input id="w-follow" type="checkbox" checked>跟随最新结果</label><span id="w-version-status" class="muted"></span></div>`);
 for(const side of ['left','right']){
  const handle=document.createElement('div');handle.className='w-resize '+side;handle.tabIndex=0;handle.setAttribute('role','separator');handle.setAttribute('aria-orientation','vertical');handle.setAttribute('aria-label',side==='left'?'调整任务面板宽度':'调整对象面板宽度');$('main').appendChild(handle);
  const setWidth=x=>document.body.style.setProperty('--'+side+'-width',Math.min(480,Math.max(220,x))+'px');
  handle.onpointerdown=e=>{handle.setPointerCapture(e.pointerId);handle.onpointermove=p=>setWidth(side==='left'?p.clientX:innerWidth-p.clientX);};
  handle.onpointerup=()=>handle.onpointermove=null;handle.onpointercancel=handle.onpointerup;
  handle.ondblclick=()=>document.body.style.removeProperty('--'+side+'-width');
  handle.onkeydown=e=>{if(e.key==='ArrowLeft'||e.key==='ArrowRight'){e.preventDefault();const current=document.querySelector(side==='left'?'aside':'aside.right').getBoundingClientRect().width;setWidth(current+(e.key==='ArrowLeft'?-20:20)*(side==='left'?1:-1));}};
 }
 const fail=err=>{$('#w-error').textContent=err.message||String(err);$('#w-error').style.display='block';};
 const clearError=()=>{$('#w-error').style.display='none';};
 const route=(kind,params={})=>'/api/'+kind+'?'+new URLSearchParams({task,...params});
 const shown=o=>{for(let n=o;n;n=n.parent)if(!n.visible)return false;return true;};
 const entryShown=e=>e.parts?e.parts.some(p=>shown(p.o)):shown(e.o);
 // Follow the original edges and transforms, including oriented candidate boxes.
 // Wide lines use screen pixels, so zooming does not make selection hard to see.
 function highlight(entry){
  const geometries=new Set();for(const line of [...selectionOutline.children]){geometries.add(line.geometry);line.material.dispose();selectionOutline.remove(line);}for(const geometry of geometries)geometry.dispose();
  if(!entry)return;
  const sources=entry.parts?entry.parts.map(p=>p.o):entry.o.children.filter(o=>o.isLineSegments);
  for(const source of sources){
   const edges=source.isLineSegments?source.geometry:new THREE.EdgesGeometry(source.geometry);
   const geometry=new LineSegmentsGeometry().setPositions(edges.attributes.position.array);
   if(!source.isLineSegments)edges.dispose();
   for(const [color,linewidth,order] of [[0x18201f,5,100],[0xffe18a,3,101]]){
    const material=new LineMaterial({color,linewidth,worldUnits:false,depthTest:false,depthWrite:false,transparent:true,toneMapped:false});
    const line=new LineSegments2(geometry,material);line.matrixAutoUpdate=false;line.renderOrder=order;line.userData.source=source;
    selectionOutline.add(line);
   }
  }
 }
 api.frameHooks.push(()=>{
  selectionOutline.visible=!!selected&&entryShown(selected);
  if(!selectionOutline.visible)return;
  for(const line of selectionOutline.children){const source=line.userData.source;line.visible=shown(source);source.updateWorldMatrix(true,false);line.matrix.copy(source.matrixWorld);}
 });
 function remove(k){if(layers[k].object)api.disposeObject(layers[k].object);layers[k].object=null;}
 function opacity(k){
  const layer=layers[k],root=k==='model'?state.main?.object:layer.object;if(!root)return;
  root.visible=layer.on&&layer.opacity>0;
  if(k==='model'){api.applyMaterials();return;}
  root.traverse(n=>{for(const m of [n.material].flat())if(m){m.opacity=(m.userData.baseOpacity??1)*layer.opacity;m.transparent=m.opacity<1;m.depthWrite=k==='scan'&&m.opacity>=1;}});
 }
 function floorOf(z){return 'floor-'+(data.floor_cuts_m||[]).filter(h=>z>=h).length;}
 function inFloor(entry){const f=$('#w-floor').value;return !f||entry.floor===f;}
 function isMatch(entry){
  const text=$('#w-search').value.trim().toLowerCase(),cat=$('#w-category').value;
  return inFloor(entry)&&(!text||`${entry.id} ${entry.cat} ${label(entry.cat)}`.toLowerCase().includes(text))&&(!cat||entry.cat===cat)&&
   (entry.layer!=='boxer'||((entry.score??0)>=+$('#w-score').value&&(entry.raw||entry.frames.length>=+$('#w-support').value)));
 }
 function updateFilters(){
  for(const entry of entries)entry.o.visible=inFloor(entry)&&(entry.layer!==mode||isMatch(entry));
  for(const group of assemblies)for(const it of group.parts)it.hidden=!inFloor(group)||(mode==='model'&&!isMatch(group));
  api.updateVisibility();renderList();
  if(selected&&!entryShown(selected))choose(null);
 }
 function categories(){
  const old=$('#w-category').value,rows=mode==='model'?assemblies:entries.filter(e=>e.layer===mode);
  $('#w-category').innerHTML='<option value="">全部类别</option>'+[...new Set(rows.map(e=>e.cat))].sort().map(c=>`<option value="${esc(c)}">${esc(label(c))} · ${esc(c)}</option>`).join('');
  if([...$('#w-category').options].some(o=>o.value===old))$('#w-category').value=old;
 }
 function renderList(){
  const source=mode==='model'?assemblies:entries.filter(e=>e.layer===mode),rows=source.filter(isMatch);
   $('#w-count').textContent=mode==='model'?`${rows.length} / ${source.length} 个对象或命名组 · ${state.items.length} 个部件`:`${rows.length} / ${source.length} 个${mode==='boxer'&&$('#w-boxer-mode').value==='raw'?'逐帧检测':'候选'} · 待核实`;
  const grouped=new Map();for(const e of rows){const k=e.floor+' · '+label(e.cat);if(!grouped.has(k))grouped.set(k,[]);grouped.get(k).push(e);}
  $('#w-list').innerHTML=[...grouped].map(([name,items])=>`<details class="w-group" open><summary>${esc(name)} · ${items.length}</summary>${items.map(e=>`<button class="w-object ${e===selected?'selected':''}" data-entry="${esc(e.id)}"><span>${esc(label(e.cat))}</span> ${e.score!=null?`<span class="badge">${e.score.toFixed(2)}</span>`:''}<small>${esc(e.id)}${e.parts?` · ${e.parts.length} 部件`:''}</small></button>`).join('')}</details>`).join('')||'<div class="muted">当前筛选下没有结果。</div>';
 }
 function candidate(c,layer,raw=false){
  const g=c.geometry,R=g.R_work_from_object,M=new THREE.Matrix4().set(R[0][0],R[0][1],R[0][2],g.center_m[0],R[1][0],R[1][1],R[1][2],g.center_m[1],R[2][0],R[2][1],R[2][2],g.center_m[2],0,0,0,1);
  const group=new THREE.Group();group.applyMatrix4(viewFromWork.clone().multiply(M));
  const color=layer==='boxer'?0xffbd72:({door:0x7be7be,window:0xbaabff}[c.category]||0x74c6ff);
  // A zero-thickness wall remains a surface; no invented wall volume.
  const geom=new THREE.BoxGeometry(...g.size_m),fill=new THREE.MeshBasicMaterial({color,transparent:true,opacity:.055,side:THREE.DoubleSide,depthWrite:false});fill.userData.baseOpacity=.055;
  const mesh=new THREE.Mesh(geom,fill),edgeMat=new THREE.LineBasicMaterial({color,transparent:true,opacity:.9,depthWrite:false});edgeMat.userData.baseOpacity=.9;
  group.add(mesh,new THREE.LineSegments(new THREE.EdgesGeometry(geom),edgeMat));
  const frames=[...new Set(c.sources?.flatMap(s=>s.frame_ids||[])||[])];
  const entry={id:c.id,cat:c.category,floor:c.floor_id||floorOf(g.center_m[2]),o:group,layer,c,frames,score:c.scores?.confidence,raw};
  group.traverse(o=>o.userData.entry=entry);entries.push(entry);return group;
 }
 function rebuildCandidates(){
  choose(null);entries=[];
  for(const k of ['spatiallm','boxer']){remove(k);const group=new THREE.Group();
   if(k!=='boxer'||$('#w-boxer-mode').value==='fused')for(const c of data.layers[k].candidates||[])group.add(candidate(c,k));
   layers[k].object=group;api.scene.add(group);opacity(k);
  }
  categories();updateFilters();
 }
 async function loadScan(){
  if(!data||layers.scan.object||scanLoading===generation||data.layers.scan.status!=='available')return;
  const token=generation,spec=data.layers.scan;scanLoading=token;$('#w-scan-status').textContent='加载扫描…';
  try{
   let object;
   if(spec.kind==='glb'){
    const gltf=await api.loader.loadAsync(spec.url);object=gltf.scene;
    object.applyMatrix4(viewFromWork.clone().multiply(new THREE.Matrix4().set(...spec.transform.flat())).multiply(new THREE.Matrix4().makeScale(spec.scale,spec.scale,spec.scale)));
   }else{
    const cloud=await json(spec.url),geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.Float32BufferAttribute(cloud.positions,3));
    if(cloud.colors)geometry.setAttribute('color',new THREE.Float32BufferAttribute(cloud.colors,3));
    object=new THREE.Points(geometry,new THREE.PointsMaterial({size:.025,vertexColors:!!cloud.colors,color:cloud.colors?0xffffff:0xa7bab7}));object.applyMatrix4(viewFromWork);
   }
   if(token!==generation){api.disposeObject(object);return;}
   object.traverse(n=>{for(const m of [n.material].flat())if(m)m.userData.baseOpacity=m.opacity;});
   layers.scan.object=object;api.scene.add(object);opacity('scan');$('#w-scan-status').textContent=spec.label;
   if(!state.main){api.fitView(object,true);const b=api.workBox(object);if(!b.isEmpty()){$('#clip').min=b.min.z;$('#clip').max=b.max.z;$('#clip').value=b.max.z;api.updateClip();}}
  }catch(error){if(token===generation){$('#w-scan-status').textContent='扫描加载失败';fail(error);}}finally{if(scanLoading===token)scanLoading=null;}
 }
 function assemble(){
  const pm=state.main?.extras.parameter_model,params=new Map((pm?.objects||[]).map(o=>[o.id,o])),groups=new Map();
  function parentId(it){let id=it.o.userData.hs_parent_id||it.id,seen=new Set();while(params.get(id)?.parent_id&&!seen.has(id)){seen.add(id);id=params.get(id).parent_id;}
   if(id===it.id&&!params.get(id)?.parent_id&&id.split('/').length>=3){const prefix=id.slice(0,id.lastIndexOf('/')),siblings=state.items.filter(p=>!p.o.userData.hs_parent_id&&p.id.slice(0,p.id.lastIndexOf('/'))===prefix);if(siblings.length>1&&siblings.every(p=>p.cat===it.cat))return prefix;}
   return id;}
  for(const it of state.items){const id=parentId(it);if(!groups.has(id))groups.set(id,[]);groups.get(id).push(it);}
  assemblies=[...groups].map(([id,parts])=>{
   const box=new THREE.Box3();for(const it of parts)box.union(api.workBox(it.o));
   const p=params.get(id),cat=p?.category||parts[0].cat;
   // A detached group lets fit/select compute an assembly box without reparenting meshes.
   const o=new THREE.Group();const size=box.getSize(new THREE.Vector3()),center=box.getCenter(new THREE.Vector3());
   const geom=new THREE.BoxGeometry(size.x,size.y,size.z);const proxy=new THREE.Mesh(geom,new THREE.MeshBasicMaterial());proxy.position.copy(center);o.add(proxy);o.applyMatrix4(viewFromWork);o.updateMatrixWorld(true);
   const sources=parts.flatMap(it=>parse(it.o.userData.hs_sources)||[]),frames=[...new Set([...(p?.sources||[]),...sources].flatMap(s=>s.frame_ids||[]))];
   return {id,cat,parts,o,layer:'model',floor:floorOf(center.z),param:p,frames,grouping:p?.geometry?.type==='assembly'?'显式组合':parts.length>1?'按 ID 路径分组':'独立部件'};
  });
  $('#stats').textContent=`${assemblies.length} 个对象或命名组 · ${state.items.length} 个部件`;
  categories();updateFilters();
 }
 function disposeAssemblies(){for(const a of assemblies)api.disposeObject(a.o);assemblies=[];}
 function prepareModel(gltf){
  const extras=gltf.userData||{},T=extras.T_work_from_gltf;
  if(extras.parameter_model?.scene_id!==data.scene_id||!Array.isArray(T))throw new Error('结构模型缺少本任务的场景或坐标信息，已拒绝叠加。');
  gltf.scene.applyMatrix4(viewFromWork.clone().multiply(new THREE.Matrix4().set(...T.flat())));
 }
 function modelLoaded(){disposeAssemblies();assemble();opacity('model');$('#current').textContent=data.scene_id+' · 结果与证据';$('#w-model-status').textContent=`r${state.main.extras.model_revision} · ${assemblies.length} 个对象或组`;}
 function dl(rows){return '<dl>'+rows.map(([k,v])=>`<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')+'</dl>';}
 function choose(entry){
  selected=entry;photoGeneration++;highlight(entry);state.selected=null;api.applyMaterials();
  renderList();
  if(!entry){$('#w-detail').innerHTML='<div class="muted">点选模型、识别框或列表，检查尺寸与来源。</div>';return;}
  layers[entry.layer].on=true;$(`[data-layer="${entry.layer}"]`).checked=true;opacity(entry.layer);
  const c=entry.c,b=api.workBox(entry.o),size=c?.geometry.size_m||b.getSize(new THREE.Vector3()).toArray();
  const sources=c?.sources||entry.param?.sources||parse(entry.parts?.[0]?.o.userData.hs_sources)||[];
  const rows=[['来源',layerNames[entry.layer]],['楼层',entry.floor],['尺寸',size.map(x=>x.toFixed(3)).join(' × ')+' m']];
  if(entry.grouping)rows.push(['组织方式',entry.grouping]);
  if(entry.layer==='spatiallm')for(const region of data.layers.spatiallm.regions||[])if(sources.some(s=>s.reference?.path===region.path))rows.push(['来源分区',region.id],['生成截断',region.token_limit_reached==null?'未记录':region.token_limit_reached?'达到 token 上限，请复核':'未达到 token 上限']);
  if(c){rows.push(['预测分数',entry.score==null?'未提供':entry.score.toFixed(3)]);if(entry.layer==='boxer')rows.push(['来源帧 / 检测数',`${entry.frames.length} / ${c.scores?.support_detections??1}`]);}
  const review=(data.layers[entry.layer]?.review||[]).filter(r=>r.id===entry.id||r.kept===entry.id);
  $('#w-detail').innerHTML=`<button class="btn" id="w-back">返回列表</button><h3>${esc(label(entry.cat))}</h3><div class="muted">${esc(entry.id)} · ${esc(entry.cat)}</div>${c?'<p class="notice">识别候选 · 待核实。预测分数不代表几何精度。</p>':''}${dl(rows)}<button class="btn" id="w-focus">聚焦对象</button>`+
   (review.length?`<p class="notice">${esc(JSON.stringify(review))}</p>`:'')+
   (sources.length?`<details><summary>来源记录 · ${sources.length}</summary>${sources.map(s=>`<p class="muted">${esc(s.reference?.path||s.kind)}${s.record_id!=null?' · #'+esc(s.record_id):''}${s.note?'<br>'+esc(s.note):''}</p>`).join('')}</details>`:'')+
   (entry.parts?`<details><summary>网格部件 · ${entry.parts.length}</summary>${entry.parts.map(p=>`<div class="muted">${esc(p.id)}</div>`).join('')}</details>`:'');
  $('#w-focus').onclick=()=>api.fitView(entry.o);
  $('#w-back').onclick=()=>{choose(null);$('#w-modes').scrollIntoView({block:'start'});};
  if(entry.frames?.length){
   $('#w-detail').insertAdjacentHTML('beforeend',`<p class="muted">来源照片与二维检测框</p><select id="w-photo-frame" aria-label="来源照片">${entry.frames.map(f=>`<option value="${esc(f)}">帧 ${esc(f)}</option>`).join('')}</select><div id="w-photo"></div><div id="w-photo-note" class="muted"></div>`);
   $('#w-photo-frame').onchange=()=>showPhoto(entry,$('#w-photo-frame').value);showPhoto(entry,entry.frames[0]);
  }
  $('#w-detail').scrollIntoView({block:'start',behavior:'smooth'});
 }
 async function showPhoto(entry,id){
  const token=++photoGeneration;$('#w-photo').textContent='读取照片…';
  try{
   const frame=await json(route('frame',{frame:id}));if(token!==photoGeneration)return;
   const linked=entry.layer==='model'?(entry.param?.sources||[]).filter(s=>s.kind==='prediction'&&s.reference?.path==='boxer/fused-predictions.json').map(s=>'boxer/'+s.record_id):[entry.id];
   const ids=entry.raw?[entry.id]:linked.flatMap(id=>(data.layers.boxer.detection_ids||{})[id]||[]);
   const detections=frame.detections.filter(d=>ids.includes(d.id));
   const [w,h]=frame.size;
   $('#w-photo').innerHTML=`<img width="${w}" height="${h}" alt="来源帧 ${esc(id)}" src="${esc(frame.image)}"><svg viewBox="0 0 ${w} ${h}">${detections.filter(d=>d.box2d_native).map(d=>{const [x0,x1,y0,y1]=d.box2d_native;return `<rect x="${x0}" y="${y0}" width="${x1-x0}" height="${y1-y0}" fill="none" stroke="#ffbd72" stroke-width="2"/>`;}).join('')}</svg>`;
   $('#w-photo img').onerror=()=>{$('#w-photo-note').textContent='照片缺失、绑定不可用或哈希已变化。';};
   $('#w-photo-note').textContent=detections.length?detections.map(d=>`${d.label} · 2D ${d.score2d.toFixed(2)} / 3D ${d.score3d.toFixed(2)}`).join('；'):'此结果未保存可关联的逐检测 ID，不推测二维框。';
   $('#w-detail').scrollIntoView({block:'start',behavior:'smooth'});
  }catch(error){if(token===photoGeneration)$('#w-photo').textContent=error.message;}
 }
 async function loadRaw(){
  const token=generation,id=$('#w-raw-frame').value;
  if(!id)return;
  try{
   const frame=await json(route('frame',{frame:id}));if(token!==generation||id!==$('#w-raw-frame').value||$('#w-boxer-mode').value!=='raw')return;
   choose(null);rawFrame=frame;remove('boxer');entries=entries.filter(e=>e.layer!=='boxer');const group=new THREE.Group();
   for(const d of frame.detections){const b=d.bounds_local,R=d.rotation,offset=[0,1,2].map(i=>(b[2*i]+b[2*i+1])/2);
    const c={id:d.id,category:d.label,geometry:{center_m:d.center.map((v,i)=>v+R[i].reduce((s,x,j)=>s+x*offset[j],0)),size_m:[0,1,2].map(i=>b[2*i+1]-b[2*i]),R_work_from_object:R},sources:[{frame_ids:[id],kind:'prediction',reference:{path:`boxer/frames (帧 ${id})`}}],scores:{confidence:d.score3d}};
    group.add(candidate(c,'boxer',true));
   }
   layers.boxer.object=group;api.scene.add(group);opacity('boxer');categories();updateFilters();
   $('#w-boxer-status').textContent=frame.committed?`帧 ${id} · ${frame.detections.length} 个原始检测`:`帧 ${id} · 尚无已提交检测`;
  }catch(error){if(token===generation)fail(error);}
 }
 async function revision(entry,keepView=true){
  if(!entry||loadingRevision)return;loadingRevision=true;
  try{choose(null);const result=await api.loadMain(entry.path,{keepView});if(result){if(result.main)api.disposeObject(result.main.object);revisionKey=entry.path+entry.sha256;$('#w-revision').value=entry.path;$('#w-version-status').textContent=`r${entry.revision} · 已成功构建`;}else fail('结构模型加载失败');}
  finally{loadingRevision=false;if(pendingTask&&!polling){const next=pendingTask;pendingTask=null;openTask(next);}}
 }
 function statuses(){
  for(const k of ['spatiallm','boxer']){const d=data.layers[k],raw=k==='boxer'&&$('#w-boxer-mode').value==='raw'&&rawFrame;$('#w-'+k+'-status').textContent=`${STATES[d.status]} · ${d.completed??0}/${d.total??0} ${k==='boxer'?'帧':'分区'} · ${raw?'查看原始帧 '+rawFrame.id:(d.candidates||[]).length+' 候选'}`;}
  $('#w-scan-status').textContent=data.layers.scan.label||STATES[data.layers.scan.status];
  $('#w-status').textContent=['SpatialLM 与 Boxer 候选使用工作坐标；结构模型使用其导出变换。',...['spatiallm','boxer','scan'].map(k=>data.layers[k].error).filter(Boolean)].join('\n');
  $('#current').textContent=data.scene_id+' · 结果与证据';
 }
 async function refresh(initial=false){
  if(polling)return;polling=true;const token=generation;
  try{
   const next=await json(route('task'));if(token!==generation)return;
   if(data&&next.prepared_sha256!==data.prepared_sha256){fail('任务预处理已变化，请重新选择任务以载入匹配结果。');return;}
   data=next;statuses();
   const key=JSON.stringify([data.layers.spatiallm,data.layers.boxer]);
   if(key!==candidateKey){candidateKey=key;rebuildCandidates();if($('#w-boxer-mode').value==='raw')await loadRaw();}
   const previous=$('#w-revision').value;
   $('#w-revision').innerHTML=data.revisions.length?data.revisions.map(r=>`<option value="${esc(r.path)}">r${r.revision} · ${api.fmtTime(r.mtime)}</option>`).join(''):'<option value="">尚无结构模型</option>';
   if(data.revisions.some(r=>r.path===previous))$('#w-revision').value=previous;
   if(initial){
    const floors=[...new Set(data.regions.map(r=>r.floor_id))];$('#w-floor').innerHTML='<option value="">全部楼层</option>'+floors.map(f=>`<option value="${esc(f)}">${esc(f)}</option>`).join('');
    $('#w-raw-frame').innerHTML=data.frames.map(f=>`<option value="${esc(f.id)}">帧 ${esc(f.id)}</option>`).join('');
   }
   const last=data.revisions.at(-1);
   if(last&&(initial||$('#w-follow').checked)&&last.path+last.sha256!==revisionKey)await revision(last,!initial);
   if(!last&&initial){
    mode='spatiallm';layers.spatiallm.on=true;$('[data-layer="spatiallm"]').checked=true;opacity('spatiallm');setMode(mode);api.fitView(layers.spatiallm.object,true);$('#w-model-status').textContent='尚无结构模型';$('#msg').style.display='none';
    const b=api.workBox(layers.spatiallm.object);if(!b.isEmpty()){$('#clip').min=b.min.z;$('#clip').max=b.max.z;$('#clip').value=b.max.z;api.updateClip();}
    if(!entries.length){layers.scan.on=true;$('[data-layer="scan"]').checked=true;}
   }
   if(layers.scan.on)await loadScan();
   if(!initial&&$('#w-boxer-mode').value==='raw'&&data.layers.boxer.status==='partial')await loadRaw();
  }catch(error){if(token===generation)fail(error);}finally{polling=false;if(pendingTask){const next=pendingTask;pendingTask=null;openTask(next);}}
 }
 async function openTask(id){
  if(polling||loadingRevision){pendingTask=id;return;}
  generation++;task=id;data=null;candidateKey='';revisionKey='';rawFrame=null;choose(null);clearError();
  disposeAssemblies();if(state.main)api.disposeObject(state.main.object);state.main=null;state.items=[];
  for(const k of ['scan','spatiallm','boxer'])remove(k);entries=[];
  $('#stats').textContent='';$('#w-version-status').textContent='尚未构建';$('#w-revision').innerHTML='<option value="">加载版本…</option>';
  $('#w-boxer-mode').value='fused';$('#w-raw-frame').hidden=true;$('#w-support-row').hidden=false;
  $('#w-search').value='';$('#w-score').value=0;$('#w-score-value').textContent='0.00';$('#w-support').value=1;
  $('#clipOn').checked=false;api.updateClip();
  state.T=workFromView.clone();state.hasT=true;$('#w-floor').value='';$('#w-category').value='';
  $('#w-task').value=id;history.replaceState(null,'','?task='+encodeURIComponent(id));
  await refresh(true);
 }
 function setMode(value){mode=value;$('#w-boxer-controls').hidden=mode!=='boxer';$('#w-modes').querySelectorAll('button').forEach(b=>b.classList.toggle('on',b.dataset.mode===mode));categories();updateFilters();}
 document.querySelectorAll('[data-layer]').forEach(input=>input.onchange=async()=>{const k=input.dataset.layer;layers[k].on=input.checked;if(k==='scan'&&input.checked)await loadScan();opacity(k);if(selected&&!entryShown(selected))choose(null);});
 document.querySelectorAll('[data-opacity]').forEach(input=>input.oninput=()=>{const k=input.dataset.opacity;layers[k].opacity=+input.value;input.nextElementSibling.textContent=Math.round(+input.value*100)+'%';opacity(k);});
 $('#w-task').onchange=()=>openTask($('#w-task').value);
 $('#w-modes').onclick=e=>{if(e.target.dataset.mode)setMode(e.target.dataset.mode);};
 for(const id of ['w-search','w-category','w-floor','w-score','w-support'])$('#'+id).oninput=()=>{$('#w-score-value').textContent=(+$('#w-score').value).toFixed(2);updateFilters();};
 $('#w-list').onclick=e=>{const b=e.target.closest('[data-entry]');if(b)choose([...assemblies,...entries].find(e=>e.id===b.dataset.entry));};
 $('#w-list').ondblclick=()=>{if(selected)api.fitView(selected.o);};
 $('#w-boxer-mode').onchange=async()=>{const raw=$('#w-boxer-mode').value==='raw';$('#w-raw-frame').hidden=!raw;$('#w-support-row').hidden=raw;if(raw)await loadRaw();else rebuildCandidates();};
 $('#w-raw-frame').onchange=loadRaw;
 $('#w-revision').onchange=()=>{$('#w-follow').checked=false;revision(data.revisions.find(r=>r.path===$('#w-revision').value));};
 $('#w-follow').onchange=()=>{if($('#w-follow').checked)refresh();};
 $('#fit').onclick=()=>api.fitView(selected?.o||state.main?.object||layers.scan.object||layers.spatiallm.object);
 $('#top').onclick=()=>{const o=selected?.o||state.main?.object||layers.scan.object||layers.spatiallm.object;if(!o)return;const b=new THREE.Box3().setFromObject(o);if(b.isEmpty())return;const c=b.getCenter(new THREE.Vector3()),s=b.getSize(new THREE.Vector3()),h=Math.max(s.x/Math.max(api.camera.aspect,.1),s.z)/2/Math.tan(api.camera.fov*Math.PI/360)*1.15;api.flyTo(new THREE.Vector3(c.x,b.max.y+h,c.z+.001),c,{near:.005,far:Math.max(100,h*30)});};
 addEventListener('keydown',e=>{if(e.target.matches('input,select,textarea'))return;if(e.key==='Escape')choose(null);if(e.key.toLowerCase()==='f')$('#fit').click();});
 state.workspace={workFromView,prepareModel,modelLoaded,get modelOpacity(){return layers.model.opacity;},selectModel:it=>choose(it?assemblies.find(a=>a.parts.includes(it)):null),
  pick(ray){
   // Decorative LineSegments use a one-metre default raycast tolerance. Pick
   // actual surfaces only; candidate boxes already have double-sided faces.
   const meshes=[];for(const root of [state.main?.object,...['spatiallm','boxer'].map(k=>layers[k].object)])root?.traverse(o=>{if(o.isMesh&&shown(o))meshes.push(o);});
   const hits=ray.intersectObjects(meshes,false).filter(h=>api.renderer.clippingPlanes.every(p=>p.distanceToPoint(h.point)>=0));
   const entryOf=h=>h.object.userData.entry||assemblies.find(a=>a.parts.includes(state.byMesh.get(h.object)));
   const hit=hits.find(h=>entryOf(h)?.layer===mode)||hits[0];
   choose(hit?entryOf(hit):null);},
  get selected(){return selected;},get data(){return data;},layers,entries:()=>entries,assemblies:()=>assemblies};
 await openTask(requested||listing.tasks[0].id);
 setInterval(()=>{if(!document.hidden)refresh();},4000);
 return true;
}
