# SPDX-License-Identifier: GPL-3.0-or-later
"""Declarative scene creation and updates; existing mesh-edit APIs remain v1."""
import copy
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
import uuid

import bpy
from mathutils import Matrix, Vector
from mathutils.geometry import delaunay_2d_cdt
from mathutils.kdtree import KDTree
from . import api


def contract():
    try:
        from indoor_recon import scene_model
    except ImportError:
        try:
            from ._vendor import scene_model
        except ImportError as error:
            raise api.ModelError("CONTRACT_UNAVAILABLE", "Install the complete add-on or add restruct/src to Python sys.path") from error
    return scene_model


def model():
    value=bpy.context.scene.get("hs_scene_model")
    if not value:raise api.ModelError("PARAMETRIC_PROJECT", "Create or reopen a parameter scene first")
    return json.loads(value)


def own_objects():
    project=api._state()["project_id"]
    return {o["hs_id"]:o for o in bpy.context.scene.objects if o.get("hs_project_id")==project and o.get("hs_role")=="model"}


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False).encode()).hexdigest()


def actual_digest(obj):
    value={"pose":[list(r) for r in obj.matrix_basis]}
    if obj.type=="MESH":
        value["vertices"]=[list(v.co) for v in obj.data.vertices]
        value["faces"]=[{"vertices":list(p.vertices),"material":p.material_index,"smooth":p.use_smooth} for p in obj.data.polygons]
        value["uv"]=[[list(uv.uv) for uv in layer.data] for layer in obj.data.uv_layers]
        value["materials"]=[]
        for mat in obj.data.materials:
            if not mat:value["materials"].append(None);continue
            item={"nodes_enabled":mat.use_nodes,"cull":mat.use_backface_culling,"surface_method":mat.surface_render_method}
            if mat.use_nodes:
                nodes=list(mat.node_tree.nodes);indices={n:i for i,n in enumerate(nodes)};shader=[]
                def defaults(sockets):
                    result=[]
                    for socket in sockets:
                        if not hasattr(socket,"default_value"):continue
                        v=socket.default_value
                        if isinstance(v,(float,int,bool,str)):result.append([socket.identifier,v])
                        elif hasattr(v,"__len__"):
                            data=list(v)
                            if all(isinstance(x,(float,int,bool)) for x in data):result.append([socket.identifier,data])
                    return result
                for n in nodes:
                    descriptor={"type":n.bl_idname,"inputs":defaults(n.inputs),"muted":n.mute}
                    if n.type in ("VALUE","RGB"):descriptor["outputs"]=defaults(n.outputs)
                    for key in ("operation","blend_type","interpolation","projection","extension","space","uv_map","vector_type"):
                        if hasattr(n,key):descriptor[key]=getattr(n,key)
                    if n.type=="TEX_IMAGE" and n.image:
                        image=n.image;packed=image.packed_file
                        descriptor["image"]={"size":list(image.size),"color_space":image.colorspace_settings.name,"dirty":image.is_dirty,
                            "packed_sha256":hashlib.sha256(bytes(packed.data)).hexdigest() if packed else None}
                    shader.append(descriptor)
                item["nodes"]=shader
                item["links"]=[[indices[l.from_node],l.from_socket.identifier,indices[l.to_node],l.to_socket.identifier] for l in mat.node_tree.links]
            value["materials"].append(item)
    return digest(value)


def require_clean():
    bpy.context.view_layer.update()
    dirty=[k for k,o in own_objects().items() if actual_digest(o)!=o.get("hs_built_digest")]
    if dirty:raise api.ModelError("UNRECORDED_EDIT", "Capture transforms or update parameters before rebuilding/exporting: "+", ".join(dirty))


def _triangulate(loops,bottom=0,top=0):
    sm=contract();coords=[];edges=[]
    for loop in loops:
        first=len(coords);coords.extend(Vector(p) for p in loop)
        edges.extend((first+i,first+(i+1)%len(loop)) for i in range(len(loop)))
    vertices,_,faces,originals,*_=delaunay_2d_cdt(coords,edges,[],0,1e-8,True)
    triangles=[]
    for face in faces:
        center=[sum(vertices[i][k] for i in face)/len(face) for k in range(2)]
        if sm.inside(center,loops[0]) and not any(sm.inside(center,h,boundary=False) for h in loops[1:]):
            face=list(face)
            if sm.area([[vertices[i].x,vertices[i].y] for i in face])<0:face.reverse()
            triangles.append(face)
    if not triangles:raise ValueError("Surface has no area after openings")
    used=sorted({i for face in triangles for i in face});remap={old:i for i,old in enumerate(used)}
    xy=[[vertices[i].x,vertices[i].y] for i in used];triangles=[[remap[i] for i in face] for face in triangles]
    source_indices=[originals[i][0] if originals[i] else None for i in used]
    if top==bottom:return [[x,y,bottom] for x,y in xy],triangles,source_indices
    n=len(xy);points=[[x,y,z] for z in (bottom,top) for x,y in xy]
    out=[list(reversed(f)) for f in triangles]+[[i+n for i in f] for f in triangles]
    counts=Counter(tuple(sorted((a,b))) for f in triangles for a,b in zip(f,f[1:]+f[:1]))
    for f in triangles:
        for a,b in zip(f,f[1:]+f[:1]):
            if counts[tuple(sorted((a,b)))]==1:out.append([a,b,b+n,a+n])
    return points,out,source_indices+source_indices


def _mesh(name,points,faces,uv=None):
    data=bpy.data.meshes.new(name)
    data.from_pydata(points,[],faces);data.update()
    if uv is not None:
        layer=data.uv_layers.new(name="Surface UV")
        for poly in data.polygons:
            for li,vi in zip(poly.loop_indices,poly.vertices):layer.data[li].uv=uv[vi]
    return data


def _conform_surface(points,faces,boundaries):
    """Restore declared boundary subdivisions lost in float32 CDT slivers."""
    precision=max(1,max(abs(x) for p in points for x in p))*2**-22
    result=[]
    for face in faces:
        a,b,c=[Vector(points[i]) for i in face]
        if (b-a).cross(c-a).length<=precision*max((b-a).length,(c-b).length,(a-c).length):continue
        pending=[list(face)]
        for point in boundaries:
            p=Vector(point)
            if not any((p-Vector(q)).length<precision for q in points):points.append(list(point))
            index=min(range(len(points)),key=lambda i:(Vector(points[i])-p).length)
            updated=[]
            for tri in pending:
                split=False
                for k in range(3):
                    ia,ib,ic=tri[k],tri[(k+1)%3],tri[(k+2)%3]
                    va,vb=Vector(points[ia]),Vector(points[ib]);delta=vb-va
                    if not delta.length_squared:continue
                    t=(p-va).dot(delta)/delta.length_squared
                    if precision/delta.length<t<1-precision/delta.length and (va+t*delta-p).length<precision:
                        updated.extend([[ia,index,ic],[index,ib,ic]]);split=True;break
                if not split:updated.append(tri)
            pending=updated
        result.extend(pending)
    return points,result


def _materials(parameters,roots,collection,cache):
    result={}
    for identity,value in parameters["materials"].items():
        if "source_mesh" in value:
            data=_reference(value["source_mesh"],roots,collection,cache)
            if not data.materials:raise ValueError("Material source has no material")
            result[identity]=data.materials[0].copy();bpy.data.meshes.remove(data)
            continue
        spec=copy.deepcopy(value);alpha=spec.pop("alpha",1);spec.pop("uv_work",None);spec.pop("uv_box_m",None);spec.pop("uv_box_space",None);spec.pop("uv_rotate_deg",None)
        for key in ("base_color_texture","roughness_texture","normal_texture"):
            if key in spec:spec[key]=str(contract().resolve(spec[key],roots))
        mat=api._material(spec,"HS "+identity)
        bsdf=next(n for n in mat.node_tree.nodes if n.type=="BSDF_PRINCIPLED")
        bsdf.inputs["Alpha"].default_value=alpha
        if alpha<1:mat.surface_render_method="DITHERED"
        result[identity]=mat
    return result


def _reference(g,roots,collection,cache):
    path=contract().resolve(g["asset"],roots);key=(str(path),g["asset_axes"])
    if key not in cache:
        c=Matrix(((1,0,0,0),(0,0,-1,0),(0,1,0,0),(0,0,0,1)))
        objects=api._import_meshes(path,collection,c.inverted() if g["asset_axes"]=="z_up" else Matrix.Identity(4))
        cache[key]=objects
    objects=cache[key]
    selected=[o for o in objects if o.get("structure_id",o.get("hs_id",o.name))==g.get("node")] if g.get("node") else objects
    if not selected:raise ValueError("Reference node not found: "+g.get("node",""))
    points=[];faces=[];mats=[];slots=[];uvs=[];smooth=[]
    for ob in selected:
        start=len(points);points.extend(list(ob.matrix_world@v.co) for v in ob.data.vertices)
        for poly in ob.data.polygons:
            faces.append([start+i for i in poly.vertices]);smooth.append(poly.use_smooth)
            mat=ob.data.materials[poly.material_index] if ob.data.materials else None
            if mat not in mats:mats.append(mat)
            slots.append(mats.index(mat))
            active=ob.data.uv_layers.active
            uvs.extend(list(active.data[i].uv) if active else [0,0] for i in poly.loop_indices)
    scale=g.get("scale_xyz",[1,1,1]);points=[[p[k]*scale[k] for k in range(3)] for p in points]
    data=_mesh("Reference geometry",points,faces)
    for mat in mats:
        if mat:data.materials.append(mat)
    for poly,index,flag in zip(data.polygons,slots,smooth):poly.material_index=index;poly.use_smooth=flag
    layer=data.uv_layers.new(name="Reference UV")
    for datum,uv in zip(layer.data,uvs):datum.uv=uv
    return data


def _build(parameters,roots,collection):
    sm=contract();generated={};cache={}
    try:
        materials=_materials(parameters,roots,collection,cache)
        for row in parameters["objects"]:
            g=row["geometry"];kind=g["type"];data=None;local=Matrix(row.get("T_parent_from_object",sm.IDENTITY));used_nodes=[]
            holes=[]
            if kind=="box":
                x,y,z=[s/2 for s in g["size_m"]]
                points=[[-x,-y,-z],[x,-y,-z],[x,y,-z],[-x,y,-z],[-x,-y,z],[x,-y,z],[x,y,z],[-x,y,z]]
                data=_mesh(row["id"],points,[[3,2,1,0],[4,5,6,7],[0,1,5,4],[1,2,6,5],[2,3,7,6],[3,0,4,7]],[[p[0],p[1]] for p in points])
            elif kind=="prism":
                points,faces,_=_triangulate([g["boundary_m"],*g.get("holes_m",[])],g["bottom_m"],g["top_m"])
                data=_mesh(row["id"],points,faces,[[p[0],p[1]] for p in points])
            elif kind in ("surface","opening"):
                basis,loops=sm.surface_loops(g,parameters["vertices"])
                origin,u,v,n=[Vector(p) for p in basis]
                used_nodes=[i for loop in [g["boundary_ids"],*g.get("holes_ids",[])] for i in loop]
                if kind=="surface":
                    holes=[o for o in parameters["objects"] if o.get("host_id")==row["id"]]
                    for opening in holes:
                        node_ids=opening["geometry"]["boundary_ids"];used_nodes.extend(node_ids)
                        loops.append([[(Vector(parameters["vertices"][i])-origin).dot(u),(Vector(parameters["vertices"][i])-origin).dot(v)] for i in node_ids])
                    bottom=g.get("offset_m",-g.get("thickness_m",0)/2);top=bottom+g.get("thickness_m",0)
                else:bottom=-g.get("panel_thickness_m",.03)/2;top=-bottom
                if kind=="surface" or g.get("fill","none")=="panel":
                    points,faces,originals=_triangulate(loops,bottom,top)
                    uv=[[p[0],p[1]] for p in points]
                    if kind=="opening":
                        angle=math.radians(g.get("angle_degrees",0));c,s=math.cos(angle),math.sin(angle)
                        points=[[c*x+s*z,y,-s*x+c*z] for x,y,z in points]
                    world_points=[list(origin+u*p[0]+v*p[1]+n*p[2]) for p in points]
                    if kind=="surface":
                        # Keep shared input nodes exact. Projecting each adjacent
                        # plane back independently creates float32 seam cracks.
                        for index,original in enumerate(originals):
                            if original is not None:world_points[index]=list(Vector(parameters["vertices"][used_nodes[original]])+n*points[index][2])
                        if bottom==top==0:
                            world_points,faces=_conform_surface(world_points,faces,[parameters["vertices"][i] for i in used_nodes])
                            uv=[[(Vector(p)-origin).dot(u),(Vector(p)-origin).dot(v)] for p in world_points]
                    data=_mesh(row["id"],world_points,faces,uv)
            elif kind=="mesh_reference":data=_reference(g,roots,collection,cache)
            obj=bpy.data.objects.new(row["id"],data);collection.objects.link(obj)
            obj.matrix_basis=local
            obj["hs_id"]=row["id"];obj["hs_role"]="model";obj["hs_project_id"]=api._state()["project_id"]
            obj["hs_category"]=row["category"];obj["hs_structure_type"]=kind;obj["hs_label"]=row.get("label",row["id"])
            obj["hs_parent_id"]=row.get("parent_id","");obj["hs_host_id"]=row.get("host_id","")
            obj["hs_enclosure"]=row.get("role")=="enclosure"
            obj["hs_source_frames"]=json.dumps(sorted({i for s in row["sources"] for i in s.get("frame_ids",[])}))
            obj["hs_sources"]=json.dumps(row["sources"],ensure_ascii=False)
            if data and "material_id" in row:
                data.materials.clear();data.materials.append(materials[row["material_id"]])
                for p in data.polygons:p.material_index=0
                spec=parameters["materials"][row["material_id"]]
                if "uv_work" in spec or "uv_box_m" in spec:
                    transform=local.copy();parent=row.get("parent_id");by={r["id"]:r for r in parameters["objects"]}
                    while parent:
                        transform=Matrix(by[parent].get("T_parent_from_object",sm.IDENTITY))@transform;parent=by[parent].get("parent_id")
                    layer=data.uv_layers.active or data.uv_layers.new(name="Source UV")
                    # Box projection in work axes, or in the object's own axes so grain follows a turned object.
                    frame=Matrix.Identity(4) if spec.get("uv_box_space")=="object" else transform
                    turn=math.radians(spec.get("uv_rotate_deg",0));cos_t,sin_t=math.cos(turn),math.sin(turn)
                    for poly in data.polygons:
                        if "uv_box_m" in spec:
                            # Each face takes the plane across its dominant axis; u runs along x (y for faces facing
                            # x), v along y on horizontal faces and up (z) on upright ones.
                            normal=frame.to_3x3()@poly.normal;axis=max(range(3),key=lambda i:abs(normal[i]))
                            pair={0:(1,2),1:(0,2),2:(0,1)}[axis];size=spec["uv_box_m"]
                        for li,vi in zip(poly.loop_indices,poly.vertices):
                            if "uv_box_m" in spec:
                                p=frame@data.vertices[vi].co;u,v=p[pair[0]],p[pair[1]]
                                layer.data[li].uv=[(cos_t*u+sin_t*v)/size[0],(-sin_t*u+cos_t*v)/size[1]]
                            else:
                                world=transform@data.vertices[vi].co
                                layer.data[li].uv=[sum(coeff[k]*world[k] for k in range(3))+coeff[3] for coeff in spec["uv_work"]]
            signature={"object":row,"vertices":{i:parameters["vertices"][i] for i in used_nodes},"openings":holes,"material":parameters["materials"].get(row.get("material_id"))}
            ancestors=[];parent=row.get("parent_id");by={r["id"]:r for r in parameters["objects"]}
            while parent:
                ancestors.append(by[parent].get("T_parent_from_object",sm.IDENTITY));parent=by[parent].get("parent_id")
            signature["ancestor_poses"]=ancestors
            obj["hs_param_signature"]=digest(signature);generated[row["id"]]=obj
        return generated
    finally:
        for imported in cache.values():
            for obj in imported:
                mesh=obj.data;bpy.data.objects.remove(obj,do_unlink=True)
                if mesh.users==0:bpy.data.meshes.remove(mesh)


def _commit(parameters,roots,initial=False):
    sm=contract();sm.validate(parameters)
    if not initial:require_clean()
    prior=own_objects();scene=bpy.context.scene
    collection=bpy.data.collections.new("HS Parameter Staging");scene.collection.children.link(collection)
    selected=[(o.get("hs_id"),o.name) for o in bpy.context.selected_objects]
    active=bpy.context.view_layer.objects.active
    active=(active.get("hs_id"),active.name) if active else None
    prior_data={kind:{d.as_pointer() for d in getattr(bpy.data,kind)} for kind in ("meshes","materials","images")}
    try:
        generated=_build(parameters,roots,collection)
        # Nothing in the existing model is changed until every new mesh has built.
        final={};changed=[]
        for identity,new in generated.items():
            old=prior.get(identity)
            if old:
                if old.type!=new.type:
                    data=old.data;bpy.data.objects.remove(old,do_unlink=True)
                    if data and data.users==0 and isinstance(data,bpy.types.Mesh):bpy.data.meshes.remove(data)
                    final[identity]=new;changed.append(identity);continue
                if old.get("hs_param_signature")!=new["hs_param_signature"]:
                    mesh=old.data;old.data=new.data
                    if mesh and mesh.users==0:bpy.data.meshes.remove(mesh)
                    old.matrix_basis=new.matrix_basis
                    for k in new.keys():old[k]=new[k]
                    changed.append(identity)
                final[identity]=old
                bpy.data.objects.remove(new,do_unlink=True)
            else:final[identity]=new;changed.append(identity)
        target=bpy.data.collections.get(api._state()["model_collection"])
        for row in parameters["objects"]:
            obj=final[row["id"]]
            obj.parent=final.get(row.get("parent_id"));obj.matrix_parent_inverse=Matrix.Identity(4)
            obj.matrix_basis=Matrix(row.get("T_parent_from_object",sm.IDENTITY))
            if target not in obj.users_collection:target.objects.link(obj)
            if collection in obj.users_collection:collection.objects.unlink(obj)
        for identity,old in prior.items():
            if identity not in final:bpy.data.objects.remove(old,do_unlink=True)
        scene["hs_scene_model"]=json.dumps(parameters,ensure_ascii=False,allow_nan=False)
        state=api._state();state["roots"]={k:str(Path(v).resolve()) for k,v in roots.items()};scene["hs_project"]=json.dumps(state)
        bpy.context.view_layer.update()
        for obj in final.values():obj["hs_built_digest"]=actual_digest(obj)
        return {"scene_id":parameters["scene_id"],"revision":parameters["revision"],"objects":len(final),"changed_ids":changed}
    finally:
        for obj in list(collection.objects):
            data=obj.data;bpy.data.objects.remove(obj,do_unlink=True)
            if data and data.users==0 and isinstance(data,bpy.types.Mesh):bpy.data.meshes.remove(data)
        bpy.data.collections.remove(collection)
        available=own_objects()
        for identity,name in selected:
            obj=available.get(identity) or scene.objects.get(name)
            if obj:obj.select_set(True)
        if active:
            obj=available.get(active[0]) or scene.objects.get(active[1])
            if obj:bpy.context.view_layer.objects.active=obj
        for kind in ("meshes","materials","images"):
            datablocks=getattr(bpy.data,kind)
            for data in list(datablocks):
                if data.as_pointer() not in prior_data[kind] and data.users==0:datablocks.remove(data)


def create_scene(model,roots=None,source_model_path=None):
    api._require_object_mode();contract().validate(model)
    prior=bpy.context.window.scene;scene=bpy.data.scenes.new("Parameters "+model["scene_id"])
    bpy.context.window.scene=scene;scene.unit_settings.system="METRIC";scene.unit_settings.scale_length=1
    identity=uuid.uuid4().hex[:16];collection=bpy.data.collections.new("HS Parameters "+identity);scene.collection.children.link(collection)
    scene["hs_project"]=json.dumps({"schema_version":2,"project_id":identity,"scene_id":model["scene_id"],"roots":roots or {},"model_collection":collection.name,"source_model_path":source_model_path,"frames":[]})
    scene["hs_edits"]="[]"
    try:return _commit(copy.deepcopy(model),roots or {},initial=True)
    except Exception:
        for obj in list(scene.objects):bpy.data.objects.remove(obj,do_unlink=True)
        bpy.context.window.scene=prior;bpy.data.scenes.remove(scene);raise


def update_scene(objects=None,vertices=None,create=None,delete=None,expected_revision=None,materials=None):
    parameters=contract().patched(model(),objects or [],vertices,create or [],delete or [],expected_revision,materials)
    result=_commit(parameters,api._state()["roots"])
    api._journal("update_scene",{"objects":objects,"vertices":vertices,"create":create,"delete":delete,"expected_revision":expected_revision,"materials":materials})
    return result


def apply_scene(model,expected_revision,roots=None):
    current=globals()["model"]()
    if expected_revision!=current["revision"] or model["revision"]!=expected_revision+1 or model["scene_id"]!=current["scene_id"]:raise ValueError("Scene identity or revision differs; use update_scene or a new scene")
    bindings=dict(api._state()["roots"]);bindings.update(roots or {})
    result=_commit(model,bindings)
    api._journal("apply_scene",{"expected_revision":expected_revision,"revision":model["revision"]})
    return result


def rebuild_scene(roots=None):
    bindings=dict(api._state()["roots"]);bindings.update(roots or {})
    return _commit(model(),bindings)


def inspect_scene():
    return {"model":model(),"objects":[{"id":identity,"type":obj.type,"bounds_m":api._bounds(obj) if obj.type=="MESH" else None} for identity,obj in sorted(own_objects().items())]}


def capture_transforms(object_ids,expected_revision):
    parameters=model();by={o["id"]:o for o in parameters["objects"]};patches=[]
    for identity in object_ids:
        row=by[identity];obj=own_objects()[identity];g=row["geometry"]
        if g["type"] in ("surface","opening"):raise ValueError("Shared boundaries must be edited through vertex parameters")
        matrix=obj.matrix_basis;translation,rotation,scale=matrix.decompose()
        rebuilt=Matrix.LocRotScale(translation,rotation,scale)
        if max(abs(a-b) for r,s in zip(matrix,rebuilt) for a,b in zip(r,s))>1e-6 or min(scale)<=0:raise ValueError("Sheared or reflected transforms need explicit parameter edits")
        if any(abs(s-1)>1e-6 for s in scale):
            if g["type"]!="box":raise ValueError("Scale capture is supported for box components only")
        # Vertex edits cannot be recovered as a pose; detect them before accepting.
        original=obj.matrix_basis.copy();obj.matrix_basis=Matrix(row.get("T_parent_from_object",contract().IDENTITY))
        unchanged=actual_digest(obj)==obj.get("hs_built_digest");obj.matrix_basis=original
        if not unchanged:raise ValueError("Mesh/material edits cannot be captured as a transform")
        patch={"id":identity,"T_parent_from_object":[list(r) for r in Matrix.LocRotScale(translation,rotation,Vector((1,1,1)))]}
        if g["type"]=="box":patch["geometry"]={"size_m":[g["size_m"][i]*scale[i] for i in range(3)]}
        patches.append(patch)
    # _commit normally refuses dirty transforms; restore their recorded state only
    # after all patches have been validated, then apply the captured parameters.
    next_model=contract().patched(parameters,patches,expected_revision=expected_revision)
    saved={k:own_objects()[k].matrix_basis.copy() for k in object_ids}
    for k in object_ids:own_objects()[k].matrix_basis=Matrix(by[k].get("T_parent_from_object",contract().IDENTITY))
    try:return _commit(next_model,api._state()["roots"])
    except Exception:
        for k,matrix in saved.items():own_objects()[k].matrix_basis=matrix
        raise


def validate_scene():
    errors=[];boundary=nonmanifold=0
    try:contract().validate(model());require_clean()
    except (ValueError,api.ModelError) as error:errors.append(str(error))
    edges=Counter();triangles=0;enclosure_points=[];enclosure_triangles=[]
    for obj in own_objects().values():
        if obj.type!="MESH":continue
        points=api._points(obj);obj.data.calc_loop_triangles();triangles+=len(obj.data.loop_triangles)
        if any(not math.isfinite(v) for p in points for v in p):errors.append("Nonfinite mesh: "+obj["hs_id"])
        if obj.get("hs_enclosure"):
            start=len(enclosure_points);enclosure_points.extend(points)
            for tri in obj.data.loop_triangles:
                enclosure_triangles.append([start+i for i in tri.vertices])
        for mat in obj.data.materials:
            if mat and mat.use_nodes:
                for node in mat.node_tree.nodes:
                    if node.type=="TEX_IMAGE" and node.image and len(node.image.pixels)==0:errors.append("Missing texture: "+node.image.name)
    precision=max([1]+[abs(v) for p in enclosure_points for v in p])*2**-22
    if enclosure_points:
        tree=KDTree(len(enclosure_points));parent=list(range(len(enclosure_points)))
        for i,p in enumerate(enclosure_points):tree.insert(p,i)
        tree.balance()
        def root(i):
            while parent[i]!=i:parent[i]=parent[parent[i]];i=parent[i]
            return i
        for i,p in enumerate(enclosure_points):
            for _,j,_ in tree.find_range(p,precision):parent[root(i)]=root(j)
        for tri in enclosure_triangles:
            welded=[root(i) for i in tri]
            for a,b in zip(welded,welded[1:]+welded[:1]):edges[tuple(sorted((a,b)))]+=1
    boundary=sum(v==1 for v in edges.values());nonmanifold=sum(v>2 for v in edges.values())
    if model()["boundary_policy"]=="closed" and (boundary or nonmanifold):errors.append("Declared closed enclosure is not closed")
    return {"passed":not errors,"errors":errors,"boundary_edges":boundary,"nonmanifold_edges":nonmanifold,"boundary_policy":model()["boundary_policy"],"objects":len(own_objects()),"triangles":triangles,"numerical_weld_m":precision,"weld_basis":"Euclidean distance at two relative float32 epsilons; not a scan-fit tolerance"}


def save_scene(filepath,overwrite=False):
    require_clean();path=Path(filepath).resolve()
    if path.suffix.lower()!=".blend":raise ValueError("Scene output must end in .blend")
    for target in (path,path.with_suffix(".scene.json"),path.with_suffix(".project.json")):
        if target.exists() and not overwrite:raise ValueError("Output exists: "+str(target))
    for obj in own_objects().values():
        if obj.type=="MESH":
            for mat in obj.data.materials:
                if mat and mat.use_nodes:
                    for node in mat.node_tree.nodes:
                        if node.type=="TEX_IMAGE" and node.image and not node.image.packed_file:node.image.pack()
    result=api._save_project_data(str(path),overwrite)
    path.with_suffix(".scene.json").write_text(json.dumps(model(),ensure_ascii=False,allow_nan=False,indent=2),encoding="utf-8")
    return dict(result,parameters=str(path.with_suffix(".scene.json")),revision=model()["revision"])


def export_scene(filepath,overwrite=False):
    checks=validate_scene()
    if not checks["passed"]:raise ValueError(json.dumps(checks))
    path=Path(filepath).resolve();state=api._state();parameters=model()
    if path.suffix.lower()!=".glb":raise ValueError("Export must end in .glb")
    for row in model()["objects"]:
        if row["geometry"]["type"]=="mesh_reference":
            asset=row["geometry"]["asset"]
            if path==(Path(state["roots"][asset["root"]])/asset["path"]).resolve():raise ValueError("Cannot overwrite a source asset")
    for target in (path,path.with_suffix(".manifest.json")):
        if target.exists() and not overwrite:raise ValueError("Output exists: "+str(target))
    for material in model()["materials"].values():
        if "source_mesh" in material:
            asset=material["source_mesh"]["asset"]
            if path==(Path(state["roots"][asset["root"]])/asset["path"]).resolve():raise ValueError("Cannot overwrite a source material asset")
    prior=bpy.context.window.scene;temporary=bpy.data.scenes.new("HS Parametric Export");copies=[]
    temp=path.with_name("."+path.stem+"."+uuid.uuid4().hex+".partial.glb")
    try:
        for obj in own_objects().values():
            if obj.type!="MESH":continue
            world=obj.matrix_world.copy();duplicate=obj.copy();duplicate.parent=None;duplicate.matrix_world=world
            temporary.collection.objects.link(duplicate);copies.append(duplicate)
        if not copies:raise ValueError("No mesh geometry to export")
        bpy.context.window.scene=temporary
        for obj in copies:obj.select_set(True);obj.hide_render=False;obj.hide_viewport=False
        bpy.context.view_layer.objects.active=copies[0];path.parent.mkdir(parents=True,exist_ok=True)
        bpy.ops.export_scene.gltf(filepath=str(temp),export_format="GLB",use_selection=True,use_active_scene=True,export_yup=True,export_extras=True,export_animations=False,export_cameras=False,export_lights=False,export_materials="EXPORT")
        doc=api._rewrite_glb(temp,{"export_coordinates":"glTF Y-up metres","alignment":contract().IDENTITY,"project_id":state["project_id"],
            "T_work_from_gltf":[[1,0,0,0],[0,0,-1,0],[0,1,0,0],[0,0,0,1]],"parameter_model":parameters})
        nodes=[n for n in doc["nodes"] if "mesh" in n]
        if {n.get("extras",{}).get("hs_id") for n in nodes}!={o["hs_id"] for o in copies} or len(nodes)!=len(copies):raise ValueError("Export scope differs")
        if any("uri" in image for image in doc.get("images",[])):raise ValueError("Texture not embedded")
        temp.replace(path)
    finally:
        bpy.context.window.scene=prior
        for obj in copies:bpy.data.objects.remove(obj,do_unlink=True)
        bpy.data.scenes.remove(temporary)
        if temp.exists():temp.unlink()
    report={"scene_id":model()["scene_id"],"revision":model()["revision"],"coordinate_frame":"gltf_y_up_metres","T_work_from_gltf":[[1,0,0,0],[0,0,-1,0],[0,1,0,0],[0,0,0,1]],"validation":checks,"embedded_images":len(doc.get("images",[]))}
    # Removed Blender copies cannot be queried after the export cleanup.
    report["objects"]=[n["extras"]["hs_id"] for n in nodes]
    path.with_suffix(".manifest.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    return dict(report,path=str(path))


def inspect_parameter_object(object_id):
    parameters=model();rows={o["id"]:o for o in parameters["objects"]}
    if object_id not in rows:raise ValueError("Unknown parameter object")
    included={object_id}
    while True:
        expanded=included|{o["id"] for o in rows.values() if o.get("parent_id") in included}
        if expanded==included:break
        included=expanded
    points=[p for key,obj in own_objects().items() if key in included and obj.type=="MESH" for p in api._points(obj)]
    bounds=[[min(p[k] for p in points) for k in range(3)],[max(p[k] for p in points) for k in range(3)]] if points else None
    return {"id":object_id,"parameters":rows[object_id],"children":sorted(included-{object_id}),"bounds_m":bounds,"dimensions_m":[bounds[1][k]-bounds[0][k] for k in range(3)] if bounds else [0,0,0]}
