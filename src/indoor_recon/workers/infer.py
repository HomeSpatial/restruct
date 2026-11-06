# SPDX-License-Identifier: Apache-2.0
"""Worker protocol v1. No dependency on the host CLI or jsonschema."""
import csv
import hashlib
import json
import os
from pathlib import Path
import sys
import time

if __package__:
    from .atomic_io import write_json
else:
    from atomic_io import write_json


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    write_json(path, value, default=lambda x: x.tolist(), ensure_ascii=True)


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(4*1024*1024), b""):
            h.update(chunk)
    return h.hexdigest()


class Units:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / "units.json"
        self.hashes = read(self.path) if self.path.exists() else {}
        for name, expected in self.hashes.items():
            path = self.directory / name
            if path.exists() and file_hash(path) != expected:
                raise ValueError(f"Worker checkpoint changed: {name}")

    def cached(self, name):
        path = self.directory / name
        return read(path) if name in self.hashes and path.exists() else None

    def record(self, name, value):
        write(self.directory / name, value)
        hashes = dict(self.hashes, **{name: file_hash(self.directory / name)})
        write(self.path, hashes)
        self.hashes = hashes


def load_spatiallm(request):
    """Shared by inference and explicit model-load/smoke verification."""
    import torch
    from transformers import AutoTokenizer, AutoModelForCausalLM
    from spatiallm import Layout
    from inference import DETECT_TYPE_PROMPT
    tokenizer = AutoTokenizer.from_pretrained(request["model_dir"], local_files_only=True)
    model, loading = AutoModelForCausalLM.from_pretrained(request["model_dir"], torch_dtype=torch.bfloat16,
        local_files_only=True, output_loading_info=True)
    if any(loading.get(k) for k in ("missing_keys", "unexpected_keys", "mismatched_keys", "error_msgs")):
        raise ValueError(f"SpatialLM checkpoint does not fully match the model: {loading}")
    model.to("cuda"); model.set_point_backbone_dtype(torch.float32); model.eval()
    bins = model.config.point_config["num_bins"]
    grid = Layout.get_grid_size(bins)
    template = (Path(request["source_dir"]) / "code_template.txt").read_text()
    detect = request["options"]["detect_type"]
    prompt = f'<|point_start|><|point_pad|><|point_end|>{DETECT_TYPE_PROMPT[detect]} The reference code is as followed: {template}'
    conversation = [{"role": "user", "content": prompt}]
    if model.config.model_type == "spatiallm_qwen":
        conversation.insert(0, {"role": "system", "content": "You are a helpful assistant."})
    ids = tokenizer.apply_chat_template(conversation, add_generation_prompt=True, return_tensors="pt").to(model.device)
    return tokenizer, model, bins, grid, ids


def spatiallm(request):
    import numpy as np
    import torch
    from transformers import set_seed
    from spatiallm import Layout
    from spatiallm.pcd import load_o3d_pcd, get_points_and_colors, cleanup_pcd
    from inference import preprocess_point_cloud
    out, prepared = Path(request["output"]), read(request["prepared"])
    units = Units(out)
    tokenizer, model, bins, grid, ids = load_spatiallm(request)
    records = []
    for index, region in enumerate(prepared["regions"]):
        name = f"regions/{index:04}.json"
        result = units.cached(name)
        if result is None:
            set_seed(request["options"]["seed"])
            pcd = load_o3d_pcd(str(Path(request["prepared"]).parent / region["path"]))
            before = len(pcd.points)
            pcd = cleanup_pcd(pcd, voxel_size=grid)
            points, colors = get_points_and_colors(pcd)
            offset = points.min(axis=0)
            extent = points.max(axis=0)-offset
            if not np.all(extent < grid*bins):
                raise ValueError(f"Prepared region exceeds actual model domain {grid*bins}: {extent}")
            tensor = preprocess_point_cloud(points, colors, grid, bins)
            torch.cuda.reset_peak_memory_stats(); torch.cuda.synchronize()
            start = time.perf_counter()
            with torch.inference_mode():
                output = model.generate(input_ids=ids, point_clouds=tensor, max_new_tokens=4096,
                    do_sample=True, use_cache=True, temperature=.6, top_p=.95, top_k=10, num_beams=1)
            torch.cuda.synchronize()
            generated = output[0, ids.shape[1]:]
            raw = tokenizer.decode(generated, skip_special_tokens=True)
            layout = Layout(raw)
            layout.undiscretize_and_unnormalize(bins); layout.translate(offset)
            result = {"region_id": region["id"], "raw_text": raw, "decoded_text": layout.to_language_string(),
                "boxes": layout.to_boxes(), "offset_work_m": offset, "extent_m": extent, "grid_size_m": grid,
                "num_bins": bins, "input_points": before, "cleaned_points": len(points), "encoder_points": tensor.shape[1],
                "new_tokens": len(generated), "token_limit_reached": len(generated) >= 4096,
                "last_token_id": int(generated[-1]), "eos_token_id": model.config.eos_token_id,
                "seconds": time.perf_counter()-start, "peak_allocated_gib": torch.cuda.max_memory_allocated()/2**30}
            units.record(name, result)
            del pcd, points, colors, tensor, output, generated
            torch.cuda.empty_cache()
        records.append(result)
        print(f"SPATIALLM {index+1}/{len(prepared['regions'])}", flush=True)
    write(out / "raw-predictions.json", {"regions": records, "torch_version": torch.__version__, "options": request["options"]})


def strict_boxer_state(model_dict, checkpoint_dict):
    """Upstream smart_load otherwise silently skips incompatible tensors."""
    missing = sorted(set(model_dict) - set(checkpoint_dict))
    extra = sorted(set(checkpoint_dict) - set(model_dict))
    mismatched = [k for k in model_dict.keys() & checkpoint_dict.keys()
                  if model_dict[k].shape != checkpoint_dict[k].shape]
    if missing or extra or mismatched:
        raise ValueError(f"Boxer checkpoint incompatible: missing={missing[:8]}, extra={extra[:8]}, shapes={mismatched[:8]}")
    return checkpoint_dict


def load_boxer(request):
    import torch
    import owl.owl_wrapper as owl_module
    import boxernet.dinov3_wrapper as dino_module
    import boxernet.boxernet as boxer_module
    weights = Path(request["private_weights"])
    owl_module._CKPT_PATH = str(weights / "owlv2-base-patch16-ensemble.pt")
    dino_module.CKPT_PATH = str(weights)
    prompts = request["labels"]
    if not prompts or len(prompts) != len(set(prompts)) or not set(prompts) <= set(owl_module.DEFAULT_TEXT_LABELS):
        raise ValueError("Boxer labels must be unique official OWLv2 vocabulary entries")
    torch.set_num_threads(6); torch.manual_seed(0)
    owl = owl_module.OwlWrapper("cuda", text_prompts=prompts, min_confidence=.25, precision="bfloat16", warmup=False)
    if hasattr(owl.vision_detector, "_orig_mod"):
        owl.vision_detector = owl.vision_detector._orig_mod
    original = boxer_module.smart_load
    boxer_module.smart_load = strict_boxer_state
    try:
        model = boxer_module.BoxerNet.load_from_checkpoint(str(weights / "boxernet_hw960in2x6d768-c88128f8.ckpt"), device="cuda")
    finally:
        boxer_module.smart_load = original
    return owl, model


def boxer(request):
    import numpy as np
    import cv2
    import torch
    from loaders.base_loader import BaseLoader
    from utils.tw.pose import PoseTW
    from utils.tw.obb import ObbTW
    from utils.tw.tensor_utils import pad_string, string2tensor
    from utils.file_io import ObbCsvWriter2
    from utils.fuse_3d_boxes import fuse_obbs_from_csv
    out, prepared = Path(request["output"]), read(request["prepared"])
    units = Units(out)
    prompts = request["labels"]
    owl, model = load_boxer(request)
    raw, accepted_index, stats = [], [], []
    # Rebuild aggregate files from immutable per-frame checkpoints on every resume.
    writer = ObbCsvWriter2(str(out / "boxer_3dbbs.csv"))
    for index, frame in enumerate(request["frames"]):
        name = f"frames/{index:06}.json"
        result = units.cached(name)
        if result is None:
            start = time.perf_counter()
            w, h = frame["size"]; hw = model.hw
            image = cv2.imread(frame["image_path"])
            if image is None:
                raise ValueError(f"Cannot read image for frame {frame['id']}")
            img = BaseLoader.img_to_tensor(cv2.resize(cv2.cvtColor(image, cv2.COLOR_BGR2RGB), (hw, hw)))
            with torch.inference_mode():
                boxes, scores, label_ids, _ = owl.forward(img*255)
            records = []
            if len(boxes):
                data = np.load(Path(request["prepared"]).parent / frame["points_path"])
                K, T = data["K"].copy(), data["T"]
                K[0, :] *= hw/w; K[1, :] *= hw/h
                datum = dict(img0=img, cam0=BaseLoader.pinhole_from_K(hw, hw, K[0,0], K[1,1], K[0,2], K[1,2]),
                    T_world_rig0=PoseTW.from_Rt(torch.tensor(T[:3,:3], dtype=torch.float32), torch.tensor(T[:3,3], dtype=torch.float32)),
                    sdp_w=torch.from_numpy(data["points"]).float(), bb2d=boxes)
                with torch.inference_mode(), torch.autocast(device_type="cuda", dtype=torch.bfloat16):
                    obbs = model.forward(datum)["obbs_pr_w"].cpu()[0]
                obbs = obbs.clone()
                obbs.set_sem_id(torch.tensor([int(i) for i in label_ids], dtype=torch.int32))
                for j, obb in enumerate(obbs):
                    records.append(dict(id=f"frame-{index}/detection-{j}", frame_id=frame["id"], label=prompts[int(label_ids[j])],
                        score2d=float(scores[j]), score3d=float(obb.prob.item()), obb_data=obb._data.float().tolist(),
                        center=obb.T_world_object.t.float().tolist(), rotation=obb.T_world_object.R.float().tolist(),
                        bounds_local=obb.bb3_object.float().tolist(), corners=obb.bb3corners_world.float().tolist(),
                        box2d_native=(boxes[j].cpu().numpy()*[w/hw,w/hw,h/hw,h/hw]).tolist()))
            result = {"frame_id": frame["id"], "detections": records, "seconds": time.perf_counter()-start}
            units.record(name, result)
        raw.extend(result["detections"])
        records = [r for r in result["detections"] if r["score3d"] >= .5]
        if records:
            obbs = ObbTW(torch.tensor([r["obb_data"] for r in records], dtype=torch.float32))
            obbs.set_prob(torch.tensor([(r["score2d"]+r["score3d"])/2 for r in records]))
            obbs.set_text(torch.stack([string2tensor(pad_string(r["label"], max_len=128)) for r in records]))
            writer.write(obbs, timestamps_ns=index*1_000_000, sem_id_to_name=dict(enumerate(prompts)))
            accepted_index.extend(dict(id=r["id"], frame_id=r["frame_id"], label=r["label"]) for r in records)
        stats.append({"frame_id": frame["id"], "detections": len(result["detections"]), "accepted": len(records), "seconds": result["seconds"]})
        if (index+1) % 25 == 0 or index+1 == len(request["frames"]):
            print(f"BOXER {index+1}/{len(request['frames'])} raw={len(raw)} accepted={len(accepted_index)}", flush=True)
    writer.close()
    write(out / "accepted-index.json", accepted_index)
    write(out / "raw-predictions.json", {"prompts": prompts, "detections": raw, "frames": stats, "torch_version": torch.__version__})
    csv_rows = list(csv.DictReader((out / "boxer_3dbbs.csv").open()))
    assert len(csv_rows) == len(accepted_index)
    eligible = [r for r, keep in zip(accepted_index, torch.tensor([float(r["prob"]) for r in csv_rows], dtype=torch.float32) >= .55) if keep]
    instances = fuse_obbs_from_csv(str(out / "boxer_3dbbs.csv"), str(out / "boxer_3dbbs_fused.csv")) if eligible else []
    fused = []
    for i, inst in enumerate(instances):
        obb = inst.obb.cpu()
        bounds = obb.bb3_object.float().numpy().reshape(3, 2)
        rotation = obb.T_world_object.R.float().numpy()
        support = [eligible[j] for j in inst.detection_indices]
        fused.append(dict(id=i, label=obb.text_string(), confidence=float(obb.prob.item()),
            center=(obb.T_world_object.t.float().numpy()+rotation@bounds.mean(axis=1)).tolist(), rotation=rotation.tolist(),
            scale=(bounds[:,1]-bounds[:,0]).tolist(), support_count=inst.support_count,
            source_frames=sorted({r["frame_id"] for r in support}), detection_ids=[r["id"] for r in support]))
    write(out / "fused-predictions.json", {"objects": fused, "eligible_detections": len(eligible), "fusion": "official geometry-only CSV defaults", "iou_threshold": .3, "min_detections": 4, "conf_threshold": .55})


if __name__ == "__main__":
    req = read(sys.argv[1])
    sys.path.insert(0, req["source_dir"])
    cache = Path(req["output"]) / "cache"
    cache.mkdir(exist_ok=True)
    os.environ.update(PYTHONDONTWRITEBYTECODE="1", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
        TORCHINDUCTOR_CACHE_DIR=str(cache / "torchinductor"), CUDA_CACHE_PATH=str(cache / "cuda"))
    {"spatiallm": spatiallm, "boxer": boxer}[req["backend"]](req)
