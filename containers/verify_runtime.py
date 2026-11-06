"""Probe the clean image, including actual CUDA extension kernels and background Blender."""
import argparse
import json
from pathlib import Path
import platform
import subprocess


def run(command):
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stdout+'\n'+result.stderr)
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',required=True,type=Path)
    args = parser.parse_args()
    results = {'platform':platform.platform(),'environments':{}}
    scripts = {
        'core': '''import json,sys,importlib.metadata as m
import numpy as np,open3d as o3d,trimesh
from indoor_recon import __version__
scene=o3d.t.geometry.RaycastingScene()
scene.add_triangles(o3d.core.Tensor(np.array([[0,0,0],[1,0,0],[0,1,0]],np.float32)),o3d.core.Tensor(np.array([[0,1,2]],np.uint32)))
hit=scene.cast_rays(o3d.core.Tensor(np.array([[.2,.2,1,0,0,-1]],np.float32)))['t_hit'].numpy()[0]
assert abs(float(hit)-1)<1e-6
print('RESULT '+json.dumps({'passed':True,'python':sys.version,'core':__version__,'ray_kernel':True,'packages':{d.metadata['Name']:d.version for d in m.distributions()}}))''',
        'spatiallm': '''import json,sys,importlib.metadata as m
sys.path.insert(0,'/opt/backends/SpatialLM')
import torch,torch_scatter,flash_attn,spconv.pytorch as spconv
from spatiallm import Layout
assert torch.cuda.is_available()
q=torch.randn(1,16,2,32,device='cuda',dtype=torch.bfloat16)
y=flash_attn.flash_attn_func(q,q,q);assert y.shape==q.shape and torch.isfinite(y).all()
src=torch.tensor([1.,2.,3.],device='cuda');index=torch.tensor([0,0,1],device='cuda')
torch.testing.assert_close(torch_scatter.scatter_add(src,index),torch.tensor([3.,3.],device='cuda'))
indices=torch.tensor([[0,1,1,1],[0,1,1,2],[0,1,2,1],[0,2,1,1]],device='cuda',dtype=torch.int32)
x=spconv.SparseConvTensor(torch.randn(4,16,device='cuda'),indices,[4,4,4],1)
layer=spconv.SubMConv3d(16,16,3,padding=1).cuda();z=layer(x)
assert torch.isfinite(z.features).all();torch.cuda.synchronize()
print('RESULT '+json.dumps({'passed':True,'python':sys.version,'torch':torch.__version__,'cuda':torch.version.cuda,'gpu':torch.cuda.get_device_name(),'flash_attention_kernel':True,'scatter_kernel':True,'sparse_conv_kernel':True,'packages':{d.metadata['Name']:d.version for d in m.distributions()}}))''',
        'boxer': '''import json,sys,importlib.metadata as m
sys.path.insert(0,'/opt/backends/boxer')
import torch
from owl.owl_wrapper import OwlWrapper
from boxernet.boxernet import BoxerNet
assert torch.cuda.is_available()
x=torch.randn(64,64,device='cuda',dtype=torch.bfloat16);y=x@x
assert torch.isfinite(y).all();torch.cuda.synchronize()
print('RESULT '+json.dumps({'passed':True,'python':sys.version,'torch':torch.__version__,'cuda':torch.version.cuda,'gpu':torch.cuda.get_device_name(),'bf16_kernel':True,'packages':{d.metadata['Name']:d.version for d in m.distributions()}}))'''
    }
    for name,script in scripts.items():
        output = run(['/opt/envs/'+name+'/bin/python','-B','-c',script])
        results['environments'][name] = json.loads(next(line[7:] for line in output.splitlines() if line.startswith('RESULT ')))
    blender_script = "import sys,bpy;sys.path.insert(0,'/opt/addons');from homespatial_model import api;assert 'render_review' in api.describe()['commands'];print('BLENDER_READY',bpy.app.version_string)"
    output = run(['/opt/blender/blender','--background','--factory-startup','--python-exit-code','1','--python-expr',blender_script])
    results['blender'] = next(line for line in output.splitlines() if line.startswith('BLENDER_READY'))
    results['passed'] = True
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(results,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'passed':True,'report':str(args.report),'blender':results['blender']}))


if __name__ == '__main__':
    main()
