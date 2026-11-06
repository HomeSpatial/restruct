# SPDX-License-Identifier: Apache-2.0
"""Parametric scene v2. Standard library only; also bundled in the Blender add-on."""
import copy
import hashlib
import json
import math
import re
from pathlib import Path, PurePosixPath, PureWindowsPath

IDENTITY = [[float(i == j) for j in range(4)] for i in range(4)]


def keys(value, required, optional=()):
    if not isinstance(value, dict) or set(required)-set(value) or set(value)-set(required)-set(optional):
        raise ValueError(f"Expected fields {required}, optional {optional}; got {list(value) if isinstance(value,dict) else type(value).__name__}")


def number(value, positive=False):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or (positive and value<=0):
        raise ValueError("Expected a finite number" + (" greater than zero" if positive else ""))


def vector(value,n=3):
    if not isinstance(value,list) or len(value)!=n:raise ValueError(f"Expected {n} coordinates")
    for x in value:number(x)


def pose(value):
    if not isinstance(value,list) or len(value)!=4:raise ValueError("Expected a rigid 4x4 pose")
    for row in value:vector(row,4)
    if any(abs(a-b)>1e-9 for a,b in zip(value[3],[0,0,0,1])):raise ValueError("Invalid homogeneous row")
    r=[row[:3] for row in value[:3]]
    if any(abs(sum(r[k][i]*r[k][j] for k in range(3))-(i==j))>1e-5 for i in range(3) for j in range(3)):raise ValueError("Pose rotation is not orthonormal")
    if abs(dot(r[0],cross(r[1],r[2]))-1)>1e-5:raise ValueError("Pose must be right handed")


def asset(value):
    keys(value,["root","path"],["sha256","bytes","role"])
    if not isinstance(value["root"],str) or not isinstance(value["path"],str):raise ValueError("Asset root and path must be strings")
    if "sha256" in value and not re.fullmatch("[0-9a-f]{64}",value["sha256"]):raise ValueError("Invalid SHA-256")
    if "bytes" in value and (type(value["bytes"]) is not int or value["bytes"]<0):raise ValueError("Invalid asset size")
    p=PurePosixPath(value["path"])
    if not value["root"] or p.is_absolute() or ".." in p.parts or "\\" in value["path"] or PureWindowsPath(value["path"]).drive or str(p)==".":raise ValueError("Asset must use a portable relative path")


def resolve(value,roots):
    asset(value)
    if value["root"] not in roots:raise ValueError(f"Missing asset root: {value['root']}")
    path=Path(roots[value["root"]])/value["path"]
    if not path.is_file():raise ValueError(f"Asset missing: {path}")
    if "sha256" in value:
        with path.open("rb") as stream:actual=hashlib.file_digest(stream,"sha256").hexdigest()
        if actual!=value["sha256"]:raise ValueError(f"Asset changed: {path}")
    return path


def dot(a,b):return sum(x*y for x,y in zip(a,b))
def sub(a,b):return [x-y for x,y in zip(a,b)]
def cross(a,b):return [a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]]
def unit(a):
    n=math.sqrt(dot(a,a))
    if n<1e-12:raise ValueError("Degenerate plane")
    return [x/n for x in a]


def plane(points):
    origin=points[0];u=unit(sub(points[1],origin))
    # Use the whole ring's signed area: a triangle at a concave corner can
    # point against its winding. Rebase to avoid large-coordinate cancellation.
    relative=[sub(p,origin) for p in points]
    crosses=[cross(a,b) for a,b in zip(relative,relative[1:]+relative[:1])]
    normal=unit([math.fsum(c[k] for c in crosses) for k in range(3)])
    v=cross(normal,u)
    tolerance=1e-6*max(1,max(math.sqrt(dot(sub(p,origin),sub(p,origin))) for p in points))
    if any(abs(dot(sub(p,origin),normal))>tolerance for p in points):raise ValueError("Surface boundary must be planar")
    return origin,u,v,normal


def area(loop):return sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(loop,loop[1:]+loop[:1]))/2


def polygon(loop):
    if not isinstance(loop,list) or len(loop)<3:raise ValueError("Polygon needs at least three vertices")
    for p in loop:vector(p,2)
    if len({tuple(p) for p in loop})!=len(loop) or abs(area(loop))<1e-12:raise ValueError("Polygon has duplicate vertices or zero area")
    def orient(a,b,c):return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
    def on(a,b,p):return abs(orient(a,b,p))<1e-12 and all(min(a[k],b[k])-1e-12<=p[k]<=max(a[k],b[k])+1e-12 for k in range(2))
    edges=list(zip(loop,loop[1:]+loop[:1]))
    for i,(a,b) in enumerate(edges):
        for j,(c,d) in enumerate(edges):
            if j<=i+1 or (i==0 and j==len(edges)-1):continue
            if (orient(a,b,c)*orient(a,b,d)<0 and orient(c,d,a)*orient(c,d,b)<0) or any((on(a,b,c),on(a,b,d),on(c,d,a),on(c,d,b))):raise ValueError("Self-intersecting polygon")


def inside(point,loop,boundary=True):
    x,y=point;result=False
    for a,b in zip(loop,loop[1:]+loop[:1]):
        d=(b[0]-a[0])*(y-a[1])-(b[1]-a[1])*(x-a[0])
        if abs(d)<1e-9 and min(a[0],b[0])-1e-9<=x<=max(a[0],b[0])+1e-9 and min(a[1],b[1])-1e-9<=y<=max(a[1],b[1])+1e-9:return boundary
        if (a[1]>y)!=(b[1]>y) and x<(b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]:result=not result
    return result


def holes_valid(loops):
    outer=loops[0]
    for index,hole in enumerate(loops[1:]):
        probes=hole+[[sum(p[k] for p in hole)/len(hole) for k in range(2)]]
        if any(not inside(p,outer) for p in probes):raise ValueError("A hole lies outside the outer ring; represent disconnected surfaces separately")
        for other in loops[1:index+1]:
            if any(inside(p,other) for p in hole) or any(inside(p,hole) for p in other):raise ValueError("Overlapping or nested holes")


def surface_loops(geometry,vertices):
    rows=[geometry["boundary_ids"],*geometry.get("holes_ids",[])]
    points=[[vertices[k] for k in row] for row in rows]
    basis=plane(points[0]);origin,u,v,normal=basis
    loops=[[[dot(sub(p,origin),u),dot(sub(p,origin),v)] for p in row] for row in points]
    for row in points:
        if any(abs(dot(sub(p,origin),normal))>1e-5 for p in row):raise ValueError("Hole or opening is not in the host plane")
    for loop in loops:polygon(loop)
    holes_valid(loops)
    return basis,loops


def validate(model):
    keys(model,["schema_version","kind","scene_id","coordinate_frame","revision","vertices","materials","objects","boundary_policy"],["notes"])
    if (model["schema_version"],model["kind"],model["coordinate_frame"])!=(2,"model","work_z_up_metres"):raise ValueError("Expected model v2 in work_z_up_metres")
    if not isinstance(model["scene_id"],str) or not model["scene_id"]:raise ValueError("Missing scene ID")
    if type(model["revision"]) is not int or model["revision"]<1:raise ValueError("Model revision must be a positive integer")
    if model["boundary_policy"] not in ("open","closed"):raise ValueError("Boundary policy must be open or closed")
    if "notes" in model and not isinstance(model["notes"],str):raise ValueError("Notes must be a string")
    if not isinstance(model["vertices"],dict) or not isinstance(model["materials"],dict) or not isinstance(model["objects"],list):raise ValueError("Invalid scene containers")
    for k,p in model["vertices"].items():
        if not isinstance(k,str) or not k:raise ValueError("Empty vertex ID")
        vector(p)
    for mat in model["materials"].values():
        keys(mat,[],["name","base_color_srgb","roughness","metallic","alpha","base_color_texture","roughness_texture","normal_texture","source_mesh","uv_work","uv_box_m","uv_box_space","uv_rotate_deg"])
        if "source_mesh" in mat:
            keys(mat["source_mesh"],["asset","asset_axes"],["node"]);asset(mat["source_mesh"]["asset"])
            if mat["source_mesh"]["asset_axes"] not in ("z_up","gltf_y_up"):raise ValueError("Unknown material source axes")
            if set(mat)-{"source_mesh","uv_work","uv_box_m","uv_box_space","uv_rotate_deg","name"}:raise ValueError("Replace a referenced material specification to override its PBR fields")
        if "uv_work" in mat:
            if len(mat["uv_work"])!=2:raise ValueError("UV transform needs two rows")
            for row in mat["uv_work"]:vector(row,4)
        if "uv_box_m" in mat:
            if "uv_work" in mat:raise ValueError("Use uv_work or uv_box_m, not both")
            vector(mat["uv_box_m"],2)
            if any(not x>0 for x in mat["uv_box_m"]):raise ValueError("uv_box_m sizes must be positive")
        if ("uv_box_space" in mat or "uv_rotate_deg" in mat) and "uv_box_m" not in mat:raise ValueError("uv_box_space and uv_rotate_deg need uv_box_m")
        if mat.get("uv_box_space","work") not in ("work","object"):raise ValueError("uv_box_space is work or object")
        if "uv_rotate_deg" in mat:number(mat["uv_rotate_deg"])
        if "base_color_srgb" in mat:
            vector(mat["base_color_srgb"])
            if any(not 0<=x<=1 for x in mat["base_color_srgb"]):raise ValueError("Color outside [0,1]")
        for k in ("roughness","metallic","alpha"):
            if k in mat:
                number(mat[k])
                if not 0<=mat[k]<=1:raise ValueError(f"{k} outside [0,1]")
        for k in ("base_color_texture","roughness_texture","normal_texture"):
            if k in mat:asset(mat[k])
    by={}
    for ob in model["objects"]:
        keys(ob,["id","category","revision","geometry","sources"],["parent_id","host_id","T_parent_from_object","material_id","label","role","review_notes"])
        if not isinstance(ob["id"],str) or not ob["id"] or ob["id"] in by:raise ValueError("Empty or duplicate object ID")
        by[ob["id"]]=ob
        if not isinstance(ob["category"],str) or not ob["category"] or type(ob["revision"]) is not int or ob["revision"]<1:raise ValueError("Invalid category/revision")
        if not isinstance(ob["sources"],list) or not ob["sources"]:raise ValueError("Object needs provenance")
        for k in ("parent_id","host_id","label","review_notes"):
            if k in ob and not isinstance(ob[k],str):raise ValueError(f"{k} must be a string")
        if ob.get("role","component") not in ("enclosure","component"):raise ValueError("Unknown object role")
        for s in ob["sources"]:
            keys(s,["kind","reference"],["record_id","frame_ids","note"])
            if s["kind"] not in ("measurement","prediction","inference","user","legacy_record"):raise ValueError("Unknown source kind")
            asset(s["reference"])
            for k in ("record_id","note"):
                if k in s and not isinstance(s[k],str):raise ValueError("Source text must be a string")
            if "frame_ids" in s and (not isinstance(s["frame_ids"],list) or any(not isinstance(i,str) or not i for i in s["frame_ids"]) or len(set(s["frame_ids"]))!=len(s["frame_ids"])):raise ValueError("Source frame IDs must be unique strings")
        pose(ob.get("T_parent_from_object",IDENTITY))
        if "material_id" in ob and ob["material_id"] not in model["materials"]:raise ValueError("Unknown material")
        g=ob["geometry"]
        if not isinstance(g,dict):raise ValueError("Geometry must be an object")
        kind=g.get("type")
        if kind=="box":
            keys(g,["type","size_m"]);vector(g["size_m"])
            if min(g["size_m"])<=0:raise ValueError("Box dimensions must be positive")
        elif kind=="assembly":keys(g,["type"])
        elif kind=="prism":
            keys(g,["type","boundary_m","bottom_m","top_m"],["holes_m"])
            polygon(g["boundary_m"])
            for h in g.get("holes_m",[]):polygon(h)
            holes_valid([g["boundary_m"],*g.get("holes_m",[])])
            number(g["bottom_m"]);number(g["top_m"])
            if g["top_m"]<=g["bottom_m"]:raise ValueError("Prism top must exceed bottom")
        elif kind in ("surface","opening"):
            keys(g,["type","boundary_ids"],(["holes_ids","thickness_m","offset_m"] if kind=="surface" else ["fill","panel_thickness_m","angle_degrees"]))
            for row in [g["boundary_ids"],*g.get("holes_ids",[])]:
                if len(row)<3 or len(set(row))!=len(row) or any(k not in model["vertices"] for k in row):raise ValueError("Unknown or duplicate boundary vertex")
            surface_loops(g,model["vertices"])
            if "parent_id" in ob or ob.get("T_parent_from_object",IDENTITY)!=IDENTITY:raise ValueError("Shared boundary nodes use work coordinates; surface/opening poses must be identity")
            if kind=="surface":
                number(g.get("thickness_m",0));number(g.get("offset_m",0))
                if g.get("thickness_m",0)<0:raise ValueError("Negative surface thickness")
            else:
                if "host_id" not in ob or g.get("fill","none") not in ("none","panel"):raise ValueError("Opening needs a host and supported fill")
                number(g.get("panel_thickness_m",.03),True);number(g.get("angle_degrees",0))
        elif kind=="mesh_reference":
            keys(g,["type","asset","asset_axes"],["node","scale_xyz"]);asset(g["asset"])
            if g["asset_axes"] not in ("z_up","gltf_y_up"):raise ValueError("Unknown mesh asset axes")
            vector(g.get("scale_xyz",[1,1,1]))
            if min(g.get("scale_xyz",[1,1,1]))<=0:raise ValueError("Reference scale must be positive")
        else:raise ValueError(f"Unsupported parameter geometry: {kind}")
    for ob in by.values():
        parent=ob.get("parent_id")
        if parent and (parent not in by or by[parent]["geometry"]["type"]!="assembly"):raise ValueError("Parent must reference an assembly")
        seen={ob["id"]}
        while parent:
            if parent in seen:raise ValueError("Cyclic ownership")
            seen.add(parent);parent=by[parent].get("parent_id")
        if "host_id" in ob:
            host=by.get(ob["host_id"])
            if ob["geometry"]["type"]!="opening" or host is None or host["geometry"]["type"]!="surface":raise ValueError("Opening host must be a surface")
            basis,loops=surface_loops(host["geometry"],model["vertices"])
            origin,u,v,n=basis
            points=[model["vertices"][k] for k in ob["geometry"]["boundary_ids"]]
            if any(abs(dot(sub(p,origin),n))>1e-5 for p in points):raise ValueError("Opening is not coplanar with host")
            uv=[[dot(sub(p,origin),u),dot(sub(p,origin),v)] for p in points]
            probes=uv+[[sum(p[k] for p in uv)/len(uv) for k in range(2)]]+[[ (a[k]+b[k])/2 for k in range(2)] for a,b in zip(uv,uv[1:]+uv[:1])]
            if any(not inside(p,loops[0]) for p in probes):raise ValueError("Opening lies outside host")
    return model


def patched(model,objects=(),vertices=None,create=(),delete=(),expected_revision=None,materials=None):
    validate(model)
    if expected_revision is not None and (type(expected_revision) is not int or expected_revision!=model["revision"]):raise ValueError("Stale model revision")
    result=copy.deepcopy(model);by={o["id"]:o for o in result["objects"]}
    def merge(target,patch):
        for k,v in patch.items():
            if isinstance(v,dict) and isinstance(target.get(k),dict):merge(target[k],v)
            else:target[k]=copy.deepcopy(v)
    for patch in objects:
        keys(patch,["id"],["category","geometry","sources","parent_id","host_id","T_parent_from_object","material_id","label","role","review_notes"])
        identity=patch["id"]
        if identity not in by:raise ValueError(f"Unknown object {identity}")
        if "revision" in patch:raise ValueError("Object revisions are managed by the API")
        merge(by[identity],patch);by[identity]["revision"]+=1
    if vertices:result["vertices"].update(copy.deepcopy(vertices))
    if materials:result["materials"].update(copy.deepcopy(materials))
    for ob in create:
        if ob["id"] in by:raise ValueError("Cannot create an existing ID")
        by[ob["id"]]=copy.deepcopy(ob)
    for identity in delete:
        if identity not in by:raise ValueError("Cannot delete an unknown ID")
        del by[identity]
    result["objects"]=list(by.values());result["revision"]+=1
    return validate(result)


def furniture(identity,template,size_m,transform,source,material_id):
    """Expand a template once into explicit, independently editable local parts."""
    x,y,z=size_m
    if min(size_m)<=0:raise ValueError("Furniture dimensions must be positive")
    root={"id":identity,"category":template,"revision":1,"geometry":{"type":"assembly"},"T_parent_from_object":transform,"sources":[source]}
    parts=[]
    def box(name,size,center):
        t=copy.deepcopy(IDENTITY)
        for i in range(3):t[i][3]=center[i]
        parts.append({"id":identity+"/"+name,"parent_id":identity,"category":template,"revision":1,"T_parent_from_object":t,"geometry":{"type":"box","size_m":size},"sources":[source],"material_id":material_id})
    if template in ("table","chair"):
        thick=min(z*.08,.06);height=z if template=="table" else z*.45;top=height-thick/2
        box("top",[x,y,thick],[0,0,top]);leg=min(x,y)*.08
        for a in (-1,1):
            for b in (-1,1):box(f"leg-{a}-{b}",[leg,leg,height-thick],[a*(x-leg)/2,b*(y-leg)/2,(height-thick)/2])
        if template=="chair":box("back",[x,leg,z-height],[0,(y-leg)/2,(z+height)/2])
    elif template in ("cabinet","shelf"):
        thick=min(x,y,z)*.06
        box("left",[thick,y,z],[-(x-thick)/2,0,z/2]);box("right",[thick,y,z],[(x-thick)/2,0,z/2])
        for name,h in (("bottom",thick/2),("shelf",z/2),("top",z-thick/2)):box(name,[x-2*thick,y,thick],[0,0,h])
        box("back",[x-2*thick,thick,z-2*thick],[0,(y-thick)/2,z/2])
    elif template in ("sofa","bed"):
        box("base",[x,y,z*.3],[0,0,z*.15]);box("cushion",[x,y*.85,z*.3],[0,-y*.075,z*.45])
        box("back",[x,y*.15,z*.7],[0,y*.425,z*.65])
    else:raise ValueError("Supported furniture templates: table, chair, cabinet, shelf, sofa, bed")
    return [root,*parts]


def staircase(identity,steps,transform,source,material_id):
    root={"id":identity,"category":"stairs","revision":1,"geometry":{"type":"assembly"},"T_parent_from_object":transform,"sources":[source]}
    children=[]
    for step in steps:
        keys(step,["id","boundary_m","bottom_m","top_m"],["holes_m","label"])
        children.append({"id":identity+"/"+step["id"],"parent_id":identity,"category":"stairs","revision":1,"geometry":{"type":"prism",**{k:v for k,v in step.items() if k not in ("id","label")}},"sources":[source],"material_id":material_id,"label":step.get("label",step["id"])})
    return [root,*children]
