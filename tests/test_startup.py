import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image
import trimesh

from indoor_recon.contracts import ContractError, read_json, sha256
from indoor_recon.evidence import Evidence
from indoor_recon.startup import initialize


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs = self.root / "input with spaces"
        self.inputs.mkdir()
        mesh = trimesh.Trimesh([[-1000,-1000,2000],[1000,-1000,2000],
                               [1000,1000,2000],[-1000,1000,2000]], [[0,1,2],[0,2,3]], process=False)
        mesh.visual.vertex_colors = [[60,100,180,255]]*4
        mesh.export(self.inputs / "scan.ply")
        Image.new("RGB",(32,24),(40,90,150)).save(self.inputs / "image.png")
        self.scene = {"schema_version":1,"kind":"scene","scene_id":"arbitrary-new-scene",
                      "source_coordinates":{"handedness":"right","up_axis":"+Z","metres_per_unit":.001},
                      "geometry":[{"id":"selected","type":"mesh","asset":{"root":"bundle","path":"scan.ply"},
                                   "coordinate_frame":"source","has_color":True}],
                      "frames":{"root":"bundle","path":"frames.json"}}
        frames = {"schema_version":1,"kind":"frames","scene_id":self.scene["scene_id"],"frames":[]}
        for identity in ("frame-A","81"):
            frames["frames"].append({"id":identity,"image":{"root":"bundle","path":"image.png"},"size":[32,24],
                 "camera":{"model":"pinhole_rectified","axes":"x_right_y_down_z_forward",
                           "pixel_convention":"top_left_integer_centres","K":[[20,0,16],[0,20,12],[0,0,1]],
                           "T_source_from_camera":[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]}})
        self.write(self.inputs / "frames.json",frames)
        self.runtime = self.root / "runtime.json"
        self.write(self.runtime,{"blender":{"executable":sys.executable,"addon_dir":"relative addon"}})
        self.options = {"sample_points":300,"voxel_m":.2,"boxer_stride":4,"yaw_degrees":0}

    def write(self,path,value):
        path.write_text(json.dumps(value),encoding="utf-8")

    def start(self,scene=None,roots=None,name="task",options=None):
        self.write(self.inputs / "scene.json",scene or self.scene)
        result=initialize(self.inputs/"scene.json",self.root/name,self.runtime,roots,
                          self.options if options is None else options)
        self.assertTrue(result["passed"])
        return self.root/name,read_json(self.root/name/"startup.json")

    def test_new_scene_generates_usable_bindings_and_commands(self):
        before={p.name:sha256(p) for p in self.inputs.iterdir()}
        runtime_before=sha256(self.runtime)
        out,startup=self.start()
        self.assertEqual(startup["available_frame_ids"],["frame-A","81"])
        self.assertEqual(startup["model_schema_version"],2)
        self.assertEqual(startup["capabilities"],{"colored_layout":True,"calibrated_rgb":True,"scan_rays":True})
        self.assertEqual(startup["missing_runtime"],["spatiallm","boxer"])
        runtime=read_json(out/"runtime.local.json")
        self.assertEqual(runtime["blender"]["addon_dir"],str((self.root/"relative addon").resolve()))
        evidence=Evidence(read_json(out/"evidence.json"),startup["evidence_roots"])
        result=evidence.query({"op":"pixel_rays","frame_id":"frame-A","pixels":[[16,12]]},self.root/"unused")
        np.testing.assert_allclose(result["rays"][0]["point_work_m"],[0,0,2],atol=1e-6)
        for name,value in before.items():
            self.assertEqual(sha256(self.inputs/name),value)
        self.assertEqual(sha256(self.runtime),runtime_before)
        self.assertFalse((out/"model.blend").exists())
        self.assertIn("## 问题表",(out/"STATE.md").read_text(encoding="utf-8"))
        self.assertEqual(startup["commands"]["residuals_prefix"][-2:],["--startup",str(out/"startup.json")])
        self.assertIn("check-materials", startup["commands"]["check_materials_prefix"])
        self.assertIn(f"{startup['task_root_name']}={out}", startup["commands"]["check_materials_prefix"])
        query=self.root/"query.json";self.write(query,{"op":"pixel_rays","frame_id":"81","pixels":[[16,12]]})
        command=[*startup["commands"]["evidence_prefix"],str(query),"--out",str(self.root/"query-result")]
        env=dict(os.environ,PYTHONPATH=str(Path(__file__).resolve().parents[1]/"src"),PYTHONDONTWRITEBYTECODE="1")
        result=subprocess.run(command,cwd=self.root,env=env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertTrue(read_json(self.root/"query-result/evidence.json")["passed"])

    def test_cli_initializes_from_a_different_working_directory(self):
        self.write(self.inputs/"scene.json",self.scene)
        config=self.root/"options.json";self.write(config,self.options)
        out=self.root/"cli output"
        env=dict(os.environ,PYTHONPATH=str(Path(__file__).resolve().parents[1]/"src"),PYTHONDONTWRITEBYTECODE="1")
        result=subprocess.run([sys.executable,"-B","-m","indoor_recon","init-task",str(self.inputs/"scene.json"),
            "--runtime",str(self.runtime),"--out",str(out),"--config",str(config)],
            cwd=self.root,env=env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(read_json(out/"startup.json")["model_schema_version"],2)
        rejected=subprocess.run([sys.executable,"-B","-m","indoor_recon","init-task",str(self.inputs/"scene.json"),
            "--runtime",str(self.runtime),"--out",str(out)],cwd=self.root,env=env,capture_output=True,text=True)
        self.assertNotEqual(rejected.returncode,0)

    def test_linux_and_wsl_runtime_paths_retain_their_execution_context(self):
        self.write(self.runtime,{"spatiallm":{"python":"/home/test/python","wsl_distro":"Ubuntu",
            "source_dir":"backend source","model_dir":"/weights/spatiallm"},
            "blender":{"executable":"blender","addon_dir":"relative addon"}})
        out,startup=self.start()
        runtime=read_json(out/"runtime.local.json")
        self.assertEqual(runtime["spatiallm"]["python"],"/home/test/python")
        self.assertEqual(runtime["spatiallm"]["model_dir"],"/weights/spatiallm")
        self.assertEqual(runtime["spatiallm"]["source_dir"],str((self.root/"backend source").resolve()))
        self.assertEqual(runtime["blender"]["executable"],"blender")

    def test_root_alias_collisions_preserve_input_bindings(self):
        scene=copy.deepcopy(self.scene);scene["geometry"][0]["asset"]["root"]="job"
        out,startup=self.start(scene,{"job":self.inputs,"task":self.inputs})
        config=read_json(out/"evidence.json")
        self.assertEqual(config["prepared"]["root"],"job-2")
        self.assertEqual(startup["evidence_roots"]["job"],str(self.inputs.resolve()))
        self.assertEqual(startup["task_root_name"],"task-2")
        self.assertEqual(startup["model_roots"]["task"],str(self.inputs.resolve()))

    def test_work_mesh_is_not_scaled_or_rotated_again(self):
        scene=copy.deepcopy(self.scene);scene.pop("frames")
        scene["geometry"][0]["coordinate_frame"]="work"
        scene["source_coordinates"].update(up_axis="unknown",metres_per_unit=None)
        out,startup=self.start(scene)
        config=read_json(out/"evidence.json")
        self.assertEqual(config["scan"]["metres_per_unit"],1)
        np.testing.assert_array_equal(config["scan"]["T_work_from_source_m"],np.eye(4))
        result=Evidence(config,startup["evidence_roots"]).query(
            {"op":"raycast","origins_work_m":[[0,0,0]],"directions_work":[[0,0,1]],"max_distance_m":2100},self.root/"unused")
        self.assertAlmostEqual(result["rays"][0]["point_work_m"][2],2000,places=3)

    def test_point_cloud_without_photos_has_no_scan_ray_binding(self):
        import open3d as o3d
        cloud=o3d.geometry.PointCloud()
        cloud.points=o3d.utility.Vector3dVector([[x,y,2] for x in np.linspace(-1,1,11) for y in np.linspace(-1,1,11)])
        cloud.colors=o3d.utility.Vector3dVector(np.tile([.2,.4,.7],(121,1)))
        o3d.io.write_point_cloud(str(self.inputs/"cloud.ply"),cloud)
        scene=copy.deepcopy(self.scene);scene.pop("frames")
        scene["geometry"][0].update(type="point_cloud",asset={"root":"bundle","path":"cloud.ply"})
        scene["source_coordinates"]["metres_per_unit"]=1
        out,startup=self.start(scene)
        self.assertFalse(startup["capabilities"]["scan_rays"])
        self.assertFalse(startup["capabilities"]["calibrated_rgb"])
        self.assertNotIn("scan",read_json(out/"evidence.json"))
        self.assertEqual(startup["available_frame_ids"],[])

    def test_rgbd_without_mesh_can_initialize_evidence(self):
        scene=copy.deepcopy(self.scene);scene["geometry"]=[]
        scene["source_coordinates"]["metres_per_unit"]=1
        frames=read_json(self.inputs/"frames.json")
        Image.fromarray(np.full((24,32),2000,np.uint16)).save(self.inputs/"depth.png")
        for frame in frames["frames"]:
            frame["depth"]={"asset":{"root":"bundle","path":"depth.png"},"measurement":"camera_z",
                            "metres_per_unit":.001,"invalid_value":0}
        self.write(self.inputs/"frames.json",frames)
        out,startup=self.start(scene)
        self.assertEqual(read_json(out/"job/prepare/prepared.json")["point_source"],"rgbd")
        self.assertFalse(startup["capabilities"]["scan_rays"])
        self.assertEqual(startup["available_frame_ids"],["frame-A","81"])

    def test_existing_task_is_never_overwritten(self):
        out=self.root/"occupied";out.mkdir();(out/"mine.txt").write_text("keep")
        self.write(self.inputs/"scene.json",self.scene)
        with self.assertRaises(ContractError):
            initialize(self.inputs/"scene.json",out,self.runtime)
        self.assertEqual((out/"mine.txt").read_text(),"keep")

    def test_unknown_source_units_or_up_fail_before_output(self):
        for field,value in (("metres_per_unit",None),("up_axis","unknown")):
            scene=copy.deepcopy(self.scene);scene["source_coordinates"][field]=value
            self.write(self.inputs/"scene.json",scene)
            out=self.root/field
            with self.assertRaises(ContractError):
                initialize(self.inputs/"scene.json",out,self.runtime)
            self.assertFalse(out.exists())

    def test_changed_declared_asset_hash_is_not_accepted(self):
        self.scene["geometry"][0]["asset"]["sha256"]="0"*64
        self.write(self.inputs/"scene.json",self.scene)
        with self.assertRaises(ContractError):
            initialize(self.inputs/"scene.json",self.root/"bad",self.runtime)
        self.assertFalse((self.root/"bad").exists())


if __name__=="__main__":
    unittest.main()
