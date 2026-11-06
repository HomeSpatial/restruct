// GLB 查看器：模型列表、按类别/对象显隐、拾取查看 hs_* 元数据、剖切与叠加对比。
import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';

const $=s=>document.querySelector(s);
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fmtSize=b=>b>=1e6?(b/1e6).toFixed(1)+' MB':(b/1e3).toFixed(0)+' KB';
const fmtTime=t=>new Date(t*1000).toLocaleString('zh-CN',{hour12:false,month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
const fmt=v=>Number(v).toFixed(3);

// ---------- 渲染 ----------
const canvas=$('#view');
const renderer=new THREE.WebGLRenderer({canvas,antialias:true});
renderer.setPixelRatio(Math.min(devicePixelRatio,2));
renderer.localClippingEnabled=false;
const scene=new THREE.Scene();scene.background=new THREE.Color(0x0e1214);
const camera=new THREE.PerspectiveCamera(45,1,.01,2000);camera.position.set(8,8,8);
const controls=new OrbitControls(camera,canvas);controls.enableDamping=true;controls.dampingFactor=.12;
scene.add(new THREE.HemisphereLight(0xf2f6ff,0x3a3530,1.6));
const sun=new THREE.DirectionalLight(0xffffff,1.8);sun.position.set(.6,1,.4);scene.add(sun);
const grid=new THREE.GridHelper(40,40,0x2c3a3e,0x1d2629);grid.material.transparent=true;grid.material.opacity=.6;scene.add(grid);
const selBox=new THREE.Box3Helper(new THREE.Box3(),0x3fd0b6);selBox.visible=false;scene.add(selBox);
const clipPlane=new THREE.Plane(new THREE.Vector3(0,-1,0),0);

function resize(){const r=canvas.parentElement.getBoundingClientRect();renderer.setSize(r.width,r.height,false);camera.aspect=r.width/r.height;camera.updateProjectionMatrix();}
new ResizeObserver(resize).observe(canvas.parentElement);
const clock=new THREE.Clock();
// 每帧推进；逐帧录制时由外部按固定步长调用 frame(dt)，关闭实时循环（manual）
const frameHooks=[];let manual=false;
function frame(dt){stepCamera();controls.update(dt);stepFades(dt);stepRevision(dt);for(const h of frameHooks)h(dt);renderer.render(scene,camera);}
renderer.setAnimationLoop(()=>{const dt=Math.min(clock.getDelta(),.1);if(!manual)frame(dt);});

// ---------- 状态 ----------
const loader=new GLTFLoader();
const state={main:null,compare:null,items:[],byMesh:new Map(),cats:new Map(),selected:null,T:new THREE.Matrix4(),hasT:false,
 wire:false,cull:false,cutaway:true,catOff:new Set(),list:[],root:''};

// 工作坐标：模型若带 T_work_from_gltf（Blender 导出的 Z-up 米制），尺寸和剖切高度都按它换算
function workBox(object){
 const box=new THREE.Box3().setFromObject(object);if(box.isEmpty())return box;
 const out=new THREE.Box3(),p=new THREE.Vector3();
 for(let i=0;i<8;i++){p.set(i&1?box.max.x:box.min.x,i&2?box.max.y:box.min.y,i&4?box.max.z:box.min.z).applyMatrix4(state.T);out.expandByPoint(p);}
 return out;
}

// ---------- 模型列表 ----------
async function refreshList(){
 const r=await fetch('/api/models');const data=await r.json();
 state.list=data.models;state.root=data.root;renderList();
}
function renderList(){
 const words=$('#filter').value.toLowerCase().split(/\s+/).filter(Boolean);
 const rows=state.list.filter(m=>words.every(w=>m.path.toLowerCase().includes(w)));
 const groups=new Map();
 for(const m of rows){const top=m.path.includes('/')?m.path.split('/')[0]:'.';if(!groups.has(top))groups.set(top,[]);groups.get(top).push(m);}
 const html=[];
 for(const [top,ms] of groups){
  html.push(`<div class="group" title="${esc(top)}">${esc(top)} · ${ms.length}</div>`);
  for(const m of ms){
   const rest=m.path.slice(top==='.'?0:top.length+1);
   const cls=[m.path===state.main?.path?'active':'',m.path===state.compare?.path?'compare':''].join(' ');
   html.push(`<div class="model ${cls}" data-path="${esc(m.path)}"><div class="main"><div class="p">${esc(rest)}</div><div class="m">${fmtSize(m.size)} · ${fmtTime(m.mtime)}</div></div><button class="cmp" data-cmp="${esc(m.path)}">对比</button></div>`);
  }
 }
 $('#models').innerHTML=html.join('')||`<div class="hint">${state.list.length?'没有匹配的模型':'目录下没有 GLB：'+esc(state.root)}</div>`;
}
$('#filter').oninput=renderList;
$('#models').onclick=e=>{
 const cmp=e.target.closest('[data-cmp]');
 if(cmp){e.stopPropagation();const p=cmp.dataset.cmp;p===state.compare?.path?clearCompare():loadCompare(p);return;}
 const row=e.target.closest('.model');if(row)loadMain(row.dataset.path);
};

// ---------- 加载 ----------
function urlFor(path){return '/files/'+path.split('/').map(encodeURIComponent).join('/');}
const gltfCache=new Map();  // 过程模式预载各版本，反复切换时复用同一个场景对象
function loadGltf(url,label){
 if(gltfCache.has(url))return Promise.resolve(gltfCache.get(url));
 $('#msg').textContent=`加载中：${label}`;$('#msg').style.display='block';
 return new Promise((resolve,reject)=>loader.load(url,resolve,e=>{if(e.total)$('#msg').textContent=`加载中：${label} ${(e.loaded/e.total*100).toFixed(0)}%`;},reject));
}
function disposeObject(o){
 if(![...gltfCache.values()].some(g=>g.scene===o)){
  const materials=new Set(),textures=new Set();
  o.traverse(n=>{n.geometry?.dispose();for(const m of [n.material,n.userData.__mat,n.userData.__fade?.mat].flat(2))if(m)materials.add(m);});
  for(const m of materials){for(const v of Object.values(m))if(v?.isTexture)textures.add(v);m.dispose();}
  for(const t of textures)t.dispose();
  for(const [key,m] of derived)if([...materials].some(base=>key.startsWith(base.uuid))){m.dispose();derived.delete(key);}
 }
 scene.remove(o);
}
let loadGeneration=0;

// keepView：保持视角，旧模型留给调用者处理（版本切换的淡出）；返回旧模型及其对象
async function loadMain(path,{url,size,keepView=false}={}){
 const generation=++loadGeneration;
 let gltf;
 try{gltf=await loadGltf(url||urlFor(path),path);if(generation!==loadGeneration){disposeObject(gltf.scene);return null;}state.workspace?.prepareModel(gltf);}
 catch(err){if(gltf)disposeObject(gltf.scene);$('#msg').textContent=`加载失败：${path}\n${err.message||err}`;return null;}
 $('#msg').style.display='none';
 const previous={main:state.main,items:state.items};
 if(state.main&&!keepView)disposeObject(state.main.object);
 const object=gltf.scene;scene.add(object);
 const info=state.list.find(m=>m.path===path);
 state.main={path,object,extras:gltf.userData||{},size:size??info?.size,mtime:info?.mtime};
 const T=state.main.extras.T_work_from_gltf;
 state.hasT=Array.isArray(T)&&T.length===4;
 state.T=state.workspace?state.workspace.workFromView.clone():state.hasT?new THREE.Matrix4().set(...T.flat()):new THREE.Matrix4();
 // 原材质留底，供背面剔除/线框/高亮切换后还原；复用的缓存对象可能还带着上次淡出时的材质和拾取屏蔽
 object.traverse(n=>{if(n.isMesh){n.userData.__mat??=n.material;delete n.raycast;}});
 collectItems(object);separateCoplanar();object.updateMatrixWorld(true);prepareCutaway();
 state.selected=null;selBox.visible=false;
 applyMaterials();setupClip();if(!keepView||!previous.main)fitView(object,true);renderCats();renderObjects();renderInfo();
 $('#current').innerHTML=`<b>${esc(path)}</b>`;
 const tris=countTris(object);
 $('#stats').textContent=`${state.items.length} 个对象 · ${(tris/1e3).toFixed(1)}k 三角形`;
 if(!url&&!rev.dir&&!state.workspace)setHash();
 renderList();
 state.workspace?.modelLoaded();
 return previous;
}
async function loadCompare(path){
 let gltf;
 try{gltf=await loadGltf(urlFor(path),path);}
 catch(err){$('#msg').textContent=`加载失败：${path}\n${err.message||err}`;return;}
 $('#msg').style.display=state.main?'none':'block';
 if(state.compare)disposeObject(state.compare.object);
 const object=gltf.scene;scene.add(object);
 object.traverse(n=>{if(n.isMesh){n.userData.__mat=n.material;n.raycast=()=>{};}});
 state.compare={path,object};
 $('#compareBar').classList.add('on');applyCompareMaterial();setHash();renderList();
 if(!state.main)fitView(object,true);
}
function clearCompare(){if(state.compare)disposeObject(state.compare.object);state.compare=null;$('#compareBar').classList.remove('on');setHash();renderList();}
$('#cmpClear').onclick=clearCompare;

function countTris(o){let n=0;o.traverse(m=>{if(m.isMesh){const g=m.geometry;n+=(g.index?g.index.count:g.attributes.position.count)/3;}});return n;}

// 对象：有 hs_id 的节点；没有元数据的 GLB 退回到场景的直接子节点
function collectItems(root){
 const items=[];
 root.traverse(n=>{if(n.userData?.hs_id)items.push(n);});
 if(!items.length)for(const c of root.children)items.push(c);
 state.items=items.map((o,i)=>{
  const u=o.userData||{},id=u.hs_id||o.name||`对象 ${i+1}`;
  const cat=u.hs_category||(id.includes('/')?id.split('/').slice(-2,-1)[0]:'未分类');
  return {o,id,cat,hidden:false};
 });
 state.byMesh.clear();
 for(const it of state.items){it.fade=1;it.meshes=[];it.o.traverse(n=>{if(n.isMesh)it.meshes.push(n);});}
 for(const it of state.items)it.o.traverse(n=>{if(n.isMesh&&!state.byMesh.has(n))state.byMesh.set(n,it);});
 state.cats=new Map();for(const it of state.items)state.cats.set(it.cat,(state.cats.get(it.cat)||0)+1);
 state.catOff=new Set([...state.catOff].filter(c=>state.cats.has(c)));
 updateVisibility();
}
function updateVisibility(){for(const it of state.items)it.o.visible=!it.hidden&&it.fade>0&&!state.catOff.has(it.cat);}

// ---------- 剖开墙体 ----------
// 墙和天花是有厚度的实体，背面剔除挡不住外侧面；改为隐藏所在平面把相机与观察中心分在两侧的墙，
// 以及挂在这些墙上的门窗（hs_host_id），相机高于天花底面时隐藏天花。
// 类别只作初筛（建模时薄面板也可能被标成 wall），还要求竖直高度和明确的平面法向；共面墙段因此判定一致。
const WALL=/wall|partition/i,CEILING=/ceiling|roof/i,MIN_WALL_HEIGHT=1.2,PLANE_MARGIN=.05;
function upVector(){const e=state.T.elements;return new THREE.Vector3(e[2],e[6],e[10]).normalize();}
function wallNormal(object,up){
 // 顶点在水平面上的主成分：墙板沿长度方向的分布远大于厚度方向，法向取次方向；门洞不影响。
 // 两个方向分布相近（柱、L 形构件）时返回 null，不参与剖开
 const a=new THREE.Vector3(1,0,0);if(Math.abs(a.dot(up))>.9)a.set(0,0,1);
 a.sub(up.clone().multiplyScalar(a.dot(up))).normalize();const b=up.clone().cross(a);
 const pts=[],v=new THREE.Vector3();
 object.traverse(m=>{
  if(!m.isMesh)return;const pos=m.geometry.attributes.position;
  for(let i=0;i<pos.count;i++){v.fromBufferAttribute(pos,i).applyMatrix4(m.matrixWorld);pts.push([v.dot(a),v.dot(b)]);}
 });
 if(pts.length<3)return null;
 const ma=pts.reduce((s,p)=>s+p[0],0)/pts.length,mb=pts.reduce((s,p)=>s+p[1],0)/pts.length;
 let xx=0,xy=0,yy=0;for(const [pa,pb] of pts){const da=pa-ma,db=pb-mb;xx+=da*da;xy+=da*db;yy+=db*db;}
 const tr=xx+yy;if(tr<=0)return null;
 const gap=Math.sqrt((xx-yy)**2+4*xy*xy),major=(tr+gap)/2,minor=(tr-gap)/2;
 if(minor>major*.0625)return null;  // 次/主标准差 > 1/4
 const t=.5*Math.atan2(2*xy,xx-yy)+Math.PI/2;return a.clone().multiplyScalar(Math.cos(t)).addScaledVector(b,Math.sin(t));
}
function prepareCutaway(){
 const up=upVector(),byId=new Map(state.items.map(it=>[it.id,it]));
 for(const it of state.items){
  it.cut=false;it.wall=null;it.ceilingZ=null;it.host=byId.get(it.o.userData?.hs_host_id)||null;
  if(WALL.test(it.cat)){
   const box=new THREE.Box3().setFromObject(it.o),n=box.isEmpty()?null:wallNormal(it.o,up);
   const wb=workBox(it.o);
   if(n&&wb.max.z-wb.min.z>=MIN_WALL_HEIGHT)it.wall={c:box.getCenter(new THREE.Vector3()),n};
  }
  else if(CEILING.test(it.cat))it.ceilingZ=workBox(it.o).min.z;
 }
 state.cutKey='';updateCutaway(true);
}
// instant=true 时直接到位（加载模型），否则由 stepFades 渐变
function updateCutaway(instant){
 if(!state.main)return;
 const cam=camera.position,target=controls.target,camZ=cam.clone().applyMatrix4(state.T).z;
 const rel=new THREE.Vector3();
 for(const it of state.items){
  let cut=false;
  if(state.cutaway&&it.wall){
   const sc=it.wall.n.dot(rel.subVectors(cam,it.wall.c)),st=it.wall.n.dot(rel.subVectors(target,it.wall.c));
   cut=sc*st<0&&Math.abs(sc)>PLANE_MARGIN&&Math.abs(st)>PLANE_MARGIN;
  }else if(state.cutaway&&it.ceilingZ!=null)cut=camZ>it.ceilingZ;
  it.cut=cut;
 }
 for(const it of state.items)if(state.cutaway&&it.host?.cut)it.cut=true;
 const key=state.items.map(it=>it.cut?1:0).join('');
 if(instant===true)for(const it of state.items){it.fade=it.cut?0:1;setItemMaterial(it);}
 if(key===state.cutKey)return;state.cutKey=key;
 updateVisibility();renderObjects();
 if(state.selected&&!state.selected.o.visible)select(null);
}
controls.addEventListener('change',updateCutaway);

// ---------- 材质切换 ----------
const derived=new Map();  // 线框/背面剔除/高亮的材质变体，按原材质缓存
function variant(mat,key,make){const k=mat.uuid+key;if(!derived.has(k))derived.set(k,make(mat.clone()));return derived.get(k);}
function restMaterial(n){
 const base=n.userData.__mat;const sel=!!state.selected&&state.byMesh.get(n)===state.selected;
 const key=`${state.wire}${state.cull}${sel}`;
 const one=m=>key==='falsefalsefalse'?m:variant(m,key,c=>{c.wireframe=state.wire;if(state.cull)c.side=THREE.FrontSide;
  if(sel&&'emissive' in c){c.emissive=new THREE.Color(0x1fa890);c.emissiveIntensity=.55;}return c;});
 return Array.isArray(base)?base.map(one):one(base);
}
// 淡变中的网格用静止材质的透明副本，按 fade 调整不透明度
function fadeMaterial(n,f,opacity=1){
 const src=restMaterial(n),key=[src].flat().map(m=>m.uuid).join();
 let cache=n.userData.__fade;
 if(!cache||cache.key!==key){
  if(cache)for(const m of [cache.mat].flat())m.dispose();
  const one=m=>{const c=m.clone();c.transparent=true;c.depthWrite=false;c.userData.baseOpacity=m.opacity;return c;};
  cache=n.userData.__fade={key,mat:Array.isArray(src)?src.map(one):one(src)};
 }
 const e=f*f*(3-2*f);for(const m of [cache.mat].flat())m.opacity=m.userData.baseOpacity*e*opacity;
 return cache.mat;
}
function setItemMaterial(it){const opacity=state.workspace?.modelOpacity??1,fading=(it.fade>0&&it.fade<1)||opacity<1;for(const n of it.meshes)n.material=fading?fadeMaterial(n,it.fade,opacity):restMaterial(n);}
// 相邻对象常有完全共面的面（梁顶与隔墙顶、柜背与墙面），深度相同会闪烁。按对象 ID 顺序给每个对象一档
// polygonOffset（16 档循环，最多约 16 个深度单位），共面处固定由同一个对象显示；材质按对象复制，互不影响
function separateCoplanar(){
 const order=[...state.items].sort((a,b)=>a.id<b.id?-1:a.id>b.id?1:0);
 order.forEach((it,i)=>{
  for(const n of it.meshes){
   if(n.userData.__offset)continue;n.userData.__offset=true;
   const one=m=>{const c=m.clone();c.polygonOffset=true;c.polygonOffsetFactor=0;c.polygonOffsetUnits=-(i%16);return c;};
   const b=n.userData.__mat;n.userData.__mat=Array.isArray(b)?b.map(one):one(b);
  }
 });
}
function applyMaterials(){if(state.main)for(const it of state.items)setItemMaterial(it);}
const FADE_SECONDS=.25;
function stepFades(dt){
 let changed=false;
 for(const it of state.items){
  const goal=it.cut?0:1;if(it.fade===goal){it.slow=false;continue;}
  const step=dt/(it.slow?REV_SECONDS:FADE_SECONDS);  // slow：版本切换中新出现的对象
  it.fade=goal>it.fade?Math.min(1,it.fade+step):Math.max(0,it.fade-step);
  setItemMaterial(it);changed=true;
 }
 if(changed)updateVisibility();
}
function applyCompareMaterial(){
 if(!state.compare)return;
 const op=+$('#cmpOpacity').value,orig=$('#cmpOrig').checked;
 const ghost=new THREE.MeshStandardMaterial({color:0xff8a4c,transparent:true,opacity:op,depthWrite:false,side:THREE.DoubleSide,wireframe:state.wire});
 state.compare.object.traverse(n=>{
  if(!n.isMesh)return;
  if(orig){const one=m=>{const c=m.clone();c.transparent=op<1;c.opacity=op;c.depthWrite=op>=1;c.wireframe=state.wire;return c;};const b=n.userData.__mat;n.material=Array.isArray(b)?b.map(one):one(b);}
  else n.material=ghost;
  n.renderOrder=1;
 });
}
$('#cmpOpacity').oninput=applyCompareMaterial;$('#cmpOrig').onchange=applyCompareMaterial;
$('#wire').onclick=()=>{state.wire=!state.wire;$('#wire').classList.toggle('on',state.wire);applyMaterials();applyCompareMaterial();};
$('#cull').onclick=()=>{state.cull=!state.cull;$('#cull').classList.toggle('on',state.cull);applyMaterials();};
$('#cutaway').onclick=()=>{state.cutaway=!state.cutaway;$('#cutaway').classList.toggle('on',state.cutaway);state.cutKey='';updateCutaway();};

// ---------- 剖切（按工作坐标高度） ----------
let clipWanted=null;  // 地址栏 clip=：滑块范围要等模型加载后才确定
function setupClip(){
 const b=workBox(state.main.object);if(b.isEmpty())return;
 const s=$('#clip');s.min=b.min.z.toFixed(2);s.max=b.max.z.toFixed(2);
 if(clipWanted!=null){s.value=clipWanted;$('#clipOn').checked=true;clipWanted=null;}
 if(!(+s.value>b.min.z&&+s.value<b.max.z))s.value=(b.min.z+(b.max.z-b.min.z)*.75).toFixed(2);
 updateClip();
}
function updateClip(){
 // 工作 z = T 第三行 · p（glTF 坐标）；保留 z ≤ h 的部分
 const e=state.T.elements,n=new THREE.Vector3(e[2],e[6],e[10]),h=+$('#clip').value;
 clipPlane.normal.copy(n).negate();clipPlane.constant=h-e[14];
 renderer.clippingPlanes=$('#clipOn').checked?[clipPlane]:[];
 $('#clipVal').textContent=`${h.toFixed(2)} m`;
}
$('#clip').oninput=()=>{$('#clipOn').checked=true;updateClip();};$('#clipOn').onchange=updateClip;

// ---------- 视角 ----------
// 相机过渡：绕观察中心按方向球面插值、距离按对数插值，避免直线插值穿过模型；用户拖动即中止
let tween=null;
function setClip(clip){if(clip){camera.near=clip.near;camera.far=clip.far;camera.updateProjectionMatrix();}}
function flyTo(pos,target,clip,instant){
 if(instant){tween=null;camera.position.copy(pos);controls.target.copy(target);setClip(clip);controls.update();updateCutaway(true);return;}
 const d0=camera.position.clone().sub(controls.target),d1=pos.clone().sub(target);
 tween={t0:controls.target.clone(),t1:target.clone(),u0:d0.clone().normalize(),u1:d1.clone().normalize(),r0:Math.max(d0.length(),1e-3),r1:Math.max(d1.length(),1e-3),start:performance.now(),clip};
 // 过渡期间取两端较宽的裁剪范围，结束后再收紧
 if(clip)setClip({near:Math.min(camera.near,clip.near),far:Math.max(camera.far,clip.far)});
}
function stepCamera(){
 if(!tween)return;
 const u=Math.min(1,(performance.now()-tween.start)/450),e=u<.5?4*u*u*u:1-(-2*u+2)**3/2;
 const q=new THREE.Quaternion().setFromUnitVectors(tween.u0,tween.u1),dir=tween.u0.clone().applyQuaternion(new THREE.Quaternion().slerp(q,e));
 controls.target.lerpVectors(tween.t0,tween.t1,e);
 camera.position.copy(controls.target).addScaledVector(dir,tween.r0*(tween.r1/tween.r0)**e);
 if(u>=1){setClip(tween.clip);tween=null;}
}
controls.addEventListener('start',()=>{tween=null;});
function fitView(target=state.selected?.o||state.main?.object,instant=false){
 if(!target)return;
 const box=new THREE.Box3().setFromObject(target);if(box.isEmpty())return;
 const c=box.getCenter(new THREE.Vector3()),r=Math.max(box.getSize(new THREE.Vector3()).length()/2,.2);
 const dir=camera.position.clone().sub(controls.target).normalize();if(!isFinite(dir.x)||dir.lengthSq()<.5)dir.set(1,.9,1).normalize();
 const d=r/Math.sin(THREE.MathUtils.degToRad(camera.fov/2))*1.05;
 flyTo(c.clone().addScaledVector(dir,d),c,{near:Math.max(d/300,.005),far:d*20+r*4},instant);
 if(target===state.main?.object){grid.position.set(c.x,box.min.y-.001,c.z);}
}
function topView(){
 const target=state.selected?.o||state.main?.object;if(!target)return;
 const box=new THREE.Box3().setFromObject(target),c=box.getCenter(new THREE.Vector3()),s=box.getSize(new THREE.Vector3());
 const r=Math.max(s.x,s.z)/2/Math.tan(THREE.MathUtils.degToRad(camera.fov/2))*1.1;
 // 工作 +y 朝屏幕上方（glTF -Z），略偏一点避免俯视奇点
 const h=r*Math.min(1,camera.aspect);flyTo(new THREE.Vector3(c.x,box.max.y+h,c.z+1e-3),c,{near:Math.max(h/300,.005),far:h*20+r*4});
}
$('#fit').onclick=()=>fitView();$('#top').onclick=topView;
$('#shot').onclick=()=>{renderer.render(scene,camera);const a=document.createElement('a');a.download=(state.main?.path||'view').replace(/[\/\\]/g,'_').replace(/\.gl(b|tf)$/,'')+'.png';a.href=canvas.toDataURL('image/png');a.click();};

// ---------- 拾取 ----------
const ray=new THREE.Raycaster(),ndc=new THREE.Vector2();let down=null;
canvas.addEventListener('pointerdown',e=>down={x:e.clientX,y:e.clientY});
canvas.addEventListener('pointerup',e=>{
 if(!down||Math.hypot(e.clientX-down.x,e.clientY-down.y)>4||(!state.main&&!state.workspace))return;
 const r=canvas.getBoundingClientRect();ndc.set((e.clientX-r.left)/r.width*2-1,-(e.clientY-r.top)/r.height*2+1);
 ray.setFromCamera(ndc,camera);
 if(state.workspace){state.workspace.pick(ray);return;}
 const planes=renderer.clippingPlanes;
 const hit=ray.intersectObject(state.main.object,true).find(h=>h.object.visible&&isShown(h.object)&&planes.every(p=>p.distanceToPoint(h.point)>=0));
 select(hit?state.byMesh.get(hit.object):null,{scroll:true,point:hit?.point});
});
canvas.addEventListener('dblclick',()=>{if(state.selected)fitView(state.selected.o);});
function isShown(o){for(let n=o;n;n=n.parent)if(!n.visible)return false;return true;}

function select(it,{scroll=false,point=null}={}){
 state.selected=it||null;state.lastPoint=point;
 if(it){selBox.box.setFromObject(it.o);selBox.visible=true;}else selBox.visible=false;
 applyMaterials();renderObjects(scroll);renderInfo();
 if(state.workspace){state.workspace.selectModel(it);return;}
 if(it){document.querySelector('.tabs [data-tab="info"]').click();}
}

// ---------- 右侧面板 ----------
function renderCats(){
 $('#cats').innerHTML=[...state.cats].sort((a,b)=>b[1]-a[1]).map(([c,n])=>`<span class="cat ${state.catOff.has(c)?'off':''}" data-cat="${esc(c)}" title="点击显隐，双击只看此类">${esc(c)}<i>${n}</i></span>`).join('');
}
let catClick=null;
$('#cats').onclick=e=>{const el=e.target.closest('.cat');if(!el)return;clearTimeout(catClick);catClick=setTimeout(()=>{const c=el.dataset.cat;state.catOff.has(c)?state.catOff.delete(c):state.catOff.add(c);updateVisibility();renderCats();renderObjects();},200);};
$('#cats').ondblclick=e=>{const el=e.target.closest('.cat');if(!el)return;clearTimeout(catClick);const c=el.dataset.cat;
 const only=state.catOff.size===state.cats.size-1&&!state.catOff.has(c);
 state.catOff=only?new Set():new Set([...state.cats.keys()].filter(k=>k!==c));updateVisibility();renderCats();renderObjects();};
function renderObjects(scroll=false){
 const words=$('#objFilter').value.toLowerCase().split(/\s+/).filter(Boolean);
 const rows=state.items.filter(it=>words.every(w=>it.id.toLowerCase().includes(w)||it.cat.toLowerCase().includes(w)));
 $('#objects').innerHTML=rows.map(it=>`<div class="obj ${it===state.selected?'sel':''} ${it.o.visible&&!it.cut?'':'hidden'}" data-i="${state.items.indexOf(it)}" title="${esc(it.id)}"><span>${esc(it.id)}</span><span class="c">${esc(it.cat)}</span></div>`).join('')||'<div class="hint">没有对象</div>';
 if(scroll)document.querySelector('.obj.sel')?.scrollIntoView({block:'nearest'});
}
$('#objFilter').oninput=()=>renderObjects();
$('#objects').onclick=e=>{const el=e.target.closest('.obj');if(el)select(state.items[+el.dataset.i]);};
$('#objects').ondblclick=e=>{const el=e.target.closest('.obj');if(el){select(state.items[+el.dataset.i]);fitView(state.selected.o);}};

function parseMaybeJson(v){if(typeof v!=='string'||!/^[\[{]/.test(v))return v;try{return JSON.parse(v);}catch{return v;}}
function renderInfo(){
 const el=$('#tab-info');
 if(!state.main){el.innerHTML='<div class="hint">未加载模型</div>';return;}
 const frame=state.hasT?'工作坐标（Z 向上，米）':'glTF 坐标（Y 向上）';
 if(!state.selected){
  const x=state.main.extras,pm=x.parameter_model||{},b=workBox(state.main.object),s=b.getSize(new THREE.Vector3());
  const rows=[['路径',state.main.path],['场景',pm.scene_id??'—'],['模型版本',x.model_revision??pm.revision??'—'],['文件坐标系',x.coordinate_frame??'—'],['项目',x.homespatial_project??'—'],
   ['对象数',state.items.length],['参数对象',pm.objects?.length??'—'],['材质',pm.materials?Object.keys(pm.materials).length:'—'],
   ['文件',state.main.size?fmtSize(state.main.size):'—'],['修改时间',state.main.mtime?fmtTime(state.main.mtime):'—'],
   ['范围',`${fmt(s.x)} × ${fmt(s.y)} × ${fmt(s.z)} m`]];
  el.innerHTML=`<h3>模型</h3><div class="sub">${frame}；点选对象查看元数据</div><dl>${rows.map(([k,v])=>`<dt>${k}</dt><dd>${esc(v)}</dd>`).join('')}</dl>`+
   `<div class="sec">操作</div><div class="hint" style="padding:0">单击选中 · 双击聚焦 · <kbd>F</kbd> 聚焦 · <kbd>H</kbd> 隐藏选中 · <kbd>Shift+H</kbd> 全部显示 · <kbd>Esc</kbd> 取消选择<br>类别标签：单击显隐，双击只看该类</div>`;
  return;
 }
 const it=state.selected,u=it.o.userData||{},b=workBox(it.o),s=b.getSize(new THREE.Vector3()),c=b.getCenter(new THREE.Vector3());
 const known=new Set(['hs_id','hs_category','hs_sources','__mat']);
 const rows=[['类别',it.cat],['尺寸',`${fmt(s.x)} × ${fmt(s.y)} × ${fmt(s.z)} m`],['中心',`${fmt(c.x)}, ${fmt(c.y)}, ${fmt(c.z)}`],['底 / 顶',`${fmt(b.min.z)} / ${fmt(b.max.z)} m`]];
 if(state.lastPoint){const p=state.lastPoint.clone().applyMatrix4(state.T);rows.push(['点击位置',`${fmt(p.x)}, ${fmt(p.y)}, ${fmt(p.z)}`]);}
 const extra=Object.entries(u).filter(([k])=>!known.has(k));
 const sources=parseMaybeJson(u.hs_sources);
 const pmObj=state.main.extras.parameter_model?.objects?.find(o=>o.id===it.id);
 el.innerHTML=`<h3>${esc(it.id)}</h3><div class="sub">${frame}；尺寸为轴对齐包围盒</div><dl>${rows.map(([k,v])=>`<dt>${k}</dt><dd>${esc(v)}</dd>`).join('')}</dl>`+
  `<div class="row"><button class="btn" id="iFocus">聚焦</button><button class="btn" id="iHide">隐藏</button><button class="btn" id="iOnly">只看它</button></div>`+
  (Array.isArray(sources)&&sources.length?`<div class="sec">来源</div>`+sources.map(sr=>`<div class="src"><b>${esc(sr.kind||'?')}</b>${esc(sr.reference?.path||'')}${sr.note?`<div style="color:var(--mute);margin-top:3px">${esc(sr.note)}</div>`:''}</div>`).join(''):'')+
  (extra.length?`<div class="sec">元数据</div><dl>${extra.map(([k,v])=>{const p=parseMaybeJson(v);const t=typeof p==='object'?JSON.stringify(p):p;return `<dt>${esc(k.replace(/^hs_/,''))}</dt><dd>${esc(t===''?'—':t)}</dd>`;}).join('')}</dl>`:'')+
  (pmObj?`<div class="sec">参数（parameter_model）</div><pre>${esc(JSON.stringify({...pmObj,sources:undefined},null,1))}</pre>`:'');
 $('#iFocus').onclick=()=>fitView(it.o);
 $('#iHide').onclick=()=>{it.hidden=true;updateVisibility();select(null);};
 $('#iOnly').onclick=()=>{for(const o of state.items)o.hidden=o!==it;state.catOff.clear();updateVisibility();renderCats();renderObjects();fitView(it.o);};
}
document.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>{
 document.querySelectorAll('.tabs button').forEach(x=>x.classList.toggle('on',x===b));
 $('#tab-objects').style.display=b.dataset.tab==='objects'?'flex':'none';$('#tab-info').style.display=b.dataset.tab==='info'?'block':'none';
});

addEventListener('keydown',e=>{
 if(e.target.matches('input,select,textarea')||state.workspace)return;
 if(e.key==='Escape')select(null);
 else if(e.key==='f'||e.key==='F')fitView();
 else if(e.key==='H'&&e.shiftKey){for(const it of state.items)it.hidden=false;updateVisibility();renderObjects();}
 else if((e.key==='h'||e.key==='H')&&state.selected){state.selected.hidden=true;updateVisibility();select(null);}
});

// ---------- 拖放本地文件 ----------
let dragDepth=0;
addEventListener('dragenter',e=>{e.preventDefault();dragDepth++;$('#drop').classList.add('on');});
addEventListener('dragleave',()=>{if(--dragDepth<=0){dragDepth=0;$('#drop').classList.remove('on');}});
addEventListener('dragover',e=>e.preventDefault());
addEventListener('drop',e=>{
 e.preventDefault();dragDepth=0;$('#drop').classList.remove('on');
 const f=[...e.dataTransfer.files].find(f=>/\.glb$/i.test(f.name));if(!f)return;
 const url=URL.createObjectURL(f);loadMain('本地：'+f.name,{url,size:f.size}).finally(()=>URL.revokeObjectURL(url));
});

// ---------- 版本跟随 / 回放 ----------
// ?follow=<目录>：轮询目录下已完成的构建（build.json 通过且有 model.glb），出现新版本就切换过去；
// ?replay=<目录>：按版本号依次播放，每版停 hold 秒（默认 4），loop=1 循环；空格暂停，←/→ 手动切换。
// 切换时保持视角：几何未变的对象原样保留，新增/修改的对象淡入并短暂框出，删除和被替换的旧对象淡出。
const REV_SECONDS=.8,MARK_SECONDS=2.5;
const rev={dir:null,mode:null,list:[],shown:null,index:-1,playing:true,hold:4,loop:false,timer:null,busy:false,outgoing:null,marks:[]};
const sleep=ms=>new Promise(r=>setTimeout(r,ms));

// 对象的几何指纹：顶点数、世界坐标和（毫米）与材质，用于判断两版之间对象是否变化
function signature(it){
 const v=new THREE.Vector3();let s=it.cat+'|';
 for(const m of it.meshes){
  const p=m.geometry.attributes.position;let x=0,y=0,z=0;
  for(let i=0;i<p.count;i++){v.fromBufferAttribute(p,i).applyMatrix4(m.matrixWorld);x+=v.x;y+=v.y;z+=v.z;}
  s+=`${p.count}:${x.toFixed(3)},${y.toFixed(3)},${z.toFixed(3)}:${[m.material].flat().map(t=>t.name+(t.color?.getHexString()||'')).join('/')};`;
 }
 return s;
}
function dropOutgoing(){
 if(!rev.outgoing)return;
 disposeObject(rev.outgoing.object);for(const m of rev.outgoing.fading)m.dispose();rev.outgoing=null;
}
async function showRevision(entry){
 rev.shown=entry;
 dropOutgoing();
 const previous=await loadMain(entry.path,{keepView:true});
 if(!previous){renderRevHud();return;}
 for(const it of state.items)it.sig=signature(it);
 const stale=new Map((previous.items||[]).map(it=>[it.id,it]));
 let added=0,changed=0;
 for(const it of state.items){
  const old=stale.get(it.id);
  if(old&&old.sig===it.sig){stale.delete(it.id);continue;}
  if(!previous.main)continue;  // 第一版直接显示
  old?changed++:added++;
  if(!it.cut){it.fade=0;it.slow=true;setItemMaterial(it);}
  const box=new THREE.Box3Helper(new THREE.Box3().setFromObject(it.o),0x3fd0b6);
  box.material.transparent=true;rev.marks.push({box,t:0});scene.add(box);
 }
 updateVisibility();
 const removed=[...stale.keys()].filter(id=>!state.items.some(it=>it.id===id)).length;
 if(previous.main){
  // 旧模型只留下变化/删除的对象做淡出，其余立即隐藏（新模型里有同样的对象）
  const fading=[];
  for(const it of previous.items){
   if(!stale.has(it.id)||!it.o.visible){it.o.visible=false;continue;}
   for(const n of it.meshes){
    const one=m=>{const c=m.clone();c.transparent=true;c.depthWrite=false;c.userData.baseOpacity=m.opacity;fading.push(c);return c;};
    n.material=Array.isArray(n.material)?n.material.map(one):one(n.material);n.raycast=()=>{};
   }
  }
  rev.outgoing={object:previous.main.object,fading,t:0};
 }
 rev.diff=previous.main?{added,changed,removed}:null;
 renderRevHud();
}
function stepRevision(dt){
 if(rev.outgoing){
  const o=rev.outgoing;o.t+=dt;const f=Math.max(0,1-o.t/REV_SECONDS),e=f*f*(3-2*f);
  for(const m of o.fading)m.opacity=m.userData.baseOpacity*e;
  if(f<=0)dropOutgoing();
 }
 for(const m of rev.marks){m.t+=dt;m.box.material.opacity=Math.max(0,Math.min(1,(MARK_SECONDS-m.t)/.6));}
 const done=rev.marks.filter(m=>m.t>=MARK_SECONDS);
 for(const m of done){scene.remove(m.box);m.box.geometry.dispose();m.box.material.dispose();}
 if(done.length)rev.marks=rev.marks.filter(m=>m.t<MARK_SECONDS);
}
function renderRevHud(){
 const e=rev.list.find(x=>x.path===rev.shown?.path)||rev.shown;if(!e)return;
 const r=e.residuals,g=e.geometry,d=rev.diff;
 const metrics=[r?.within_5cm!=null&&`5 cm 覆盖 ${(r.within_5cm*100).toFixed(1)}%`,r?.unexplained_m2!=null&&`未解释 ${r.unexplained_m2.toFixed(0)} m²`,
  g&&`互插 ${g.object_interference??'—'}`,g&&`漏缝 ${g.shell_leak_groups??'—'}`].filter(Boolean);
 $('#rev .r').textContent=`r${e.revision}`;
 $('#rev .l1').textContent=[fmtTime(e.mtime),...metrics].join(' · ');
 $('#rev .l2').textContent=d?[d.added&&`新增 ${d.added}`,d.changed&&`修改 ${d.changed}`,d.removed&&`删除 ${d.removed}`].filter(Boolean).join(' · ')||'几何无变化':'首版';
 $('#rev .dots').innerHTML=rev.list.map(x=>`<i class="${x.path===e.path?'on':''}">r${x.revision}</i>`).join('')+
  (rev.mode==='replay'&&!rev.playing?'<b>已暂停</b>':'');
 // 跟随模式：工具写下的最近几次运行（activity.json），看得到 agent 正在查什么
 $('#rev .act').innerHTML=rev.mode==='follow'?(rev.activity||[]).map(a=>`<div class="${a.passed?'':'fail'}">${fmtTime(a.finished).slice(-5)} ${esc(ACTIVITY_NAMES[a.command]||a.command)}${a.passed?'':'失败'} · ${esc(a.name)}</div>`).join(''):'';
 $('#rev').classList.add('on');
}
async function fetchRevisions(){
 const r=await fetch('/api/revisions?dir='+encodeURIComponent(rev.dir));
 if(!r.ok){$('#msg').textContent=`目录不存在：${rev.dir}`;$('#msg').style.display='block';return rev.list;}
 const data=await r.json();rev.activity=data.activity||[];
 return data.revisions;
}
const ACTIVITY_NAMES={evidence:'证据查询',build:'构建',render:'渲染',residuals:'残差诊断','check-geometry':'几何检查'};
async function followTick(){
 if(!rev.busy){
  rev.busy=true;
  try{
   rev.list=await fetchRevisions();const last=rev.list.at(-1);
   if(last&&(last.path!==rev.shown?.path||last.sha256!==rev.shown?.sha256))await showRevision(last);
   else renderRevHud();  // 诊断可能晚于构建出现
   if(!rev.list.length){$('#msg').textContent=`等待 ${rev.dir} 下的第一个构建…`;$('#msg').style.display='block';}
  }catch(err){console.warn(err);}finally{rev.busy=false;}
 }
 setTimeout(followTick,2000);
}
async function replayTo(i){
 if(rev.busy||!rev.list.length)return;
 clearTimeout(rev.timer);rev.busy=true;
 try{rev.index=(i+rev.list.length)%rev.list.length;await showRevision(rev.list[rev.index]);}finally{rev.busy=false;}
 scheduleReplay();
}
function scheduleReplay(){
 clearTimeout(rev.timer);
 if(!rev.playing||(!rev.loop&&rev.index>=rev.list.length-1))return;
 rev.timer=setTimeout(()=>replayTo(rev.index+1),rev.hold*1000);
}
addEventListener('keydown',e=>{
 if(rev.mode!=='replay'||e.target.matches('input'))return;
 if(e.key===' '){e.preventDefault();rev.playing=!rev.playing;renderRevHud();if(rev.playing&&rev.index>=rev.list.length-1&&!rev.loop)replayTo(0);else scheduleReplay();}
 else if(e.key==='ArrowRight')replayTo(rev.index+1);
 else if(e.key==='ArrowLeft')replayTo(rev.index-1);
});

// ---------- 地址栏 ----------
// 查询参数：follow / replay（见上）、hold、loop、bare=1 只留画面、orbit=<度/秒> 自动环绕、clip=<米> 工作坐标剖切高度
function setHash(){
 const p=new URLSearchParams();if(state.main&&!state.main.path.startsWith('本地：'))p.set('m',state.main.path);if(state.compare)p.set('c',state.compare.path);
 history.replaceState(null,'','#'+p.toString());
}
const query=new URLSearchParams(location.search);
if(query.get('bare'))document.body.classList.add('bare');
if(query.get('orbit')){controls.autoRotate=true;controls.autoRotateSpeed=+query.get('orbit')/6;}
rev.dir=query.get('follow')??query.get('replay');rev.mode=query.has('follow')?'follow':query.has('replay')?'replay':null;
rev.hold=+(query.get('hold')||4);rev.loop=query.get('loop')==='1';
await refreshList();
clipWanted=query.get('clip');
// 剖切高度（工作坐标 z，米）；null 关闭
function setClipHeight(h){if(h==null)$('#clipOn').checked=false;else{$('#clip').value=h;$('#clipOn').checked=true;}updateClip();}
const api={THREE,state,scene,camera,controls,renderer,loader,gltfCache,rev,urlFor,loadMain,showRevision,fitView,updateCutaway,
 frameHooks,frame,setManual:v=>{manual=v;},setClipHeight,updateClip,workBox,fmtTime,esc,select,disposeObject,flyTo,updateVisibility,applyMaterials};
if(query.has('process')){
 // 过程模式：按活动时间线回放查证据、构建和诊断（见 process.js）
 document.body.classList.add('bare');
 const {start}=await import('./process.js');await start(api,query);
}
else if(rev.mode==='replay'||(rev.mode==='follow'&&query.has('legacy'))){
 if(rev.mode==='follow')followTick();
 else{rev.list=await fetchRevisions();await replayTo(0);}
}
else{
 const {startWorkspace}=await import('./workspace.js');
 const active=!query.has('legacy')&&await startWorkspace(api,query);
 if(!active){if(rev.mode==='follow')followTick();else{const p=new URLSearchParams(location.hash.slice(1));if(p.get('m'))await loadMain(p.get('m'));if(p.get('c'))await loadCompare(p.get('c'));}}
}
renderInfo();
window.viewer={state,select,loadMain,loadCompare,fitView,topView,camera,controls,rev,replayTo};
