# SPDX-License-Identifier: Apache-2.0
"""Import preflight, or opt-in load/smoke checks in the backend interpreter."""
import importlib.metadata
import inspect
import json
import os
from pathlib import Path
import sys
import time

if __package__:
    from .infer import load_spatiallm, load_boxer
else:
    from infer import load_spatiallm, load_boxer


def accepts(function, *args, **kwargs):
    inspect.signature(function).bind(*args, **kwargs)


def check(backend, source, labels):
    sys.path.insert(0, source)
    import numpy
    import torch
    if not torch.cuda.is_available():
        raise ValueError("CUDA is unavailable in the selected interpreter")
    if not torch.cuda.is_bf16_supported():
        raise ValueError("This inference worker requires BF16-capable CUDA")
    if backend == "spatiallm":
        from transformers import AutoTokenizer, AutoModelForCausalLM, set_seed
        from spatiallm import Layout
        from spatiallm.pcd import load_o3d_pcd, get_points_and_colors, cleanup_pcd
        from inference import preprocess_point_cloud, DETECT_TYPE_PROMPT
        if not (Path(source) / "code_template.txt").is_file():
            raise ValueError("SpatialLM code_template.txt is missing")
        accepts(preprocess_point_cloud, None, None, .025, 1280)
        accepts(Layout.get_grid_size, 1280)
        for method in ("undiscretize_and_unnormalize", "translate", "to_boxes", "to_language_string"):
            if not callable(getattr(Layout, method, None)):
                raise ValueError(f"SpatialLM Layout.{method} is missing")
        if not {"arch", "all"} <= set(DETECT_TYPE_PROMPT):
            raise ValueError("SpatialLM detection prompts are incompatible")
    else:
        import cv2
        import owl.owl_wrapper as owl
        import boxernet.dinov3_wrapper as dino
        from boxernet.boxernet import BoxerNet, smart_load
        from loaders.base_loader import BaseLoader
        from utils.tw.pose import PoseTW
        from utils.tw.obb import ObbTW
        from utils.tw.tensor_utils import pad_string, string2tensor
        from utils.file_io import ObbCsvWriter2
        from utils.fuse_3d_boxes import fuse_obbs_from_csv
        unknown = sorted(set(labels) - set(owl.DEFAULT_TEXT_LABELS))
        if unknown:
            raise ValueError(f"Unknown Boxer vocabulary entries: {unknown}")
        if not hasattr(owl, "_CKPT_PATH") or not hasattr(dino, "CKPT_PATH"):
            raise ValueError("Boxer checkpoint routing API has changed")
        accepts(owl.OwlWrapper, "cuda", text_prompts=labels, min_confidence=.25, precision="bfloat16", warmup=False)
        accepts(BoxerNet.load_from_checkpoint, "checkpoint", device="cuda")
        accepts(BoxerNet.forward, None, {})
        accepts(BaseLoader.img_to_tensor, None)
        accepts(BaseLoader.pinhole_from_K, 960, 960, 800, 800, 480, 480)
        accepts(smart_load, {}, {})
    return {"passed": True, "verified_level": "imports", "python": sys.version,
            "cuda": torch.cuda.get_device_name(0), "cuda_runtime": torch.version.cuda,
            "free_memory_gib": torch.cuda.mem_get_info()[0] / 2**30,
            "packages": {d.metadata['Name']: d.version for d in importlib.metadata.distributions() if d.metadata['Name']},
            "scope": "imports, adapter signatures, CUDA and vocabulary; model loading and memory fit not evaluated"}


def exercise(request, result):
    import numpy as np
    import torch
    torch.manual_seed(42)
    torch.cuda.reset_peak_memory_stats()
    start = time.perf_counter()
    smoke = request["level"] == "smoke"
    result.update(model_loaded=False, synthetic_forward=False)
    if request["backend"] == "spatiallm":
        tokenizer, model, bins, grid, ids = load_spatiallm(request)
        result.update(model_loaded=True, verified_level='load')
        if smoke:
            from inference import preprocess_point_cloud
            # A well-spread 4096-point synthetic input exercises sparse/attention kernels.
            points = np.indices((16, 16, 16)).reshape(3, -1).T.astype(np.float32) * grid * 2
            tensor = preprocess_point_cloud(points, np.full_like(points, 128), grid, bins)
            with torch.inference_mode():
                output = model.generate(input_ids=ids, point_clouds=tensor, max_new_tokens=1,
                                        do_sample=False, use_cache=True, num_beams=1)
            if output.shape[1] != ids.shape[1] + 1:
                raise ValueError("SpatialLM did not generate a token")
            tokenizer.decode(output[0, ids.shape[1]:], skip_special_tokens=True)
            result['generated_tokens'] = 1
    else:
        owl, model = load_boxer(request)
        result.update(model_loaded=True, verified_level='load')
        if smoke:
            from loaders.base_loader import BaseLoader
            from utils.tw.pose import PoseTW
            hw = model.hw
            img = torch.rand(1, 3, hw, hw)
            with torch.inference_mode():
                boxes, scores, labels, _ = owl.forward(img * 255)
            if not torch.isfinite(boxes).all() or not torch.isfinite(scores).all():
                raise ValueError("OWL-V2 produced non-finite detections")
            # Force one proposal so the lifting network runs even when OWL finds nothing.
            xy = torch.stack(torch.meshgrid(torch.linspace(-1, 1, 32), torch.linspace(-1, 1, 32), indexing='ij'), -1).reshape(-1, 2)
            camera_points = torch.cat((xy, torch.full((len(xy), 1), 3.0)), 1)
            rotation = torch.tensor([[1., 0., 0.], [0., 0., 1.], [0., -1., 0.]])
            datum = dict(img0=img, cam0=BaseLoader.pinhole_from_K(hw, hw, hw, hw, hw/2, hw/2),
                T_world_rig0=PoseTW.from_Rt(rotation, torch.zeros(3)), sdp_w=camera_points @ rotation.T,
                bb2d=torch.tensor([[hw*.25, hw*.75, hw*.25, hw*.75]]))
            with torch.inference_mode(), torch.autocast(device_type="cuda", dtype=torch.bfloat16):
                obbs = model.forward(datum)["obbs_pr_w"]
            if not torch.isfinite(obbs._data).all() or obbs._data.shape[-2] != 1:
                raise ValueError("Boxer lifting output is invalid")
            result['owl_detections'] = len(boxes)
            result['forced_lifting_proposals'] = 1
    torch.cuda.synchronize()
    result.update(synthetic_forward=smoke, verified_level=request['level'], seconds=time.perf_counter()-start,
                  peak_allocated_gib=torch.cuda.max_memory_allocated()/2**30,
                  scope="model load" + (" and synthetic forward; real-scene quality and full-size memory fit not evaluated" if smoke else "; forward not evaluated"))
    return result


if __name__ == "__main__":
    result = {'passed': False, 'verified_level': 'config'}
    try:
        request = json.loads(Path(sys.argv[4]).read_text(encoding='utf-8')) if len(sys.argv) > 4 else None
        if request:
            cache = Path(request['cache'])
            cache.mkdir(parents=True, exist_ok=True)
            os.environ.update(TORCHINDUCTOR_CACHE_DIR=str(cache/'torchinductor'), CUDA_CACHE_PATH=str(cache/'cuda'))
        result = check(sys.argv[1], sys.argv[2], json.loads(sys.argv[3]))
        if request and request['level'] in ('load', 'smoke'):
            exercise(request, result)
    except Exception as exc:
        result.update(passed=False, error=f"{type(exc).__name__}: {exc}")
    print("RECON_PROBE=" + json.dumps(result))
    raise SystemExit(0 if result["passed"] else 1)
