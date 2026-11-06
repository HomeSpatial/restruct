import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from indoor_recon.textures import KINDS, make_texture, _to_linear


class ProceduralTextures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_every_kind_tiles_matches_the_measured_colour_and_maps_to_its_size(self):
        params = {"wood_planks": dict(plank_width_m=.2, plank_length_m=1.), "tile": dict(tile_m=[.3, .3])}
        for kind in KINDS:
            r = make_texture(kind, self.root / kind, [150, 120, 90], pixels_per_m=256, **params.get(kind, {}))
            colour = np.asarray(Image.open(r["files"]["base_color_texture"]), float)
            self.assertEqual(list(colour.shape[1::-1]), r["pixels"])
            np.testing.assert_allclose(r["size_m"], np.array(r["pixels"])/256, atol=.01)
            np.testing.assert_allclose(_to_linear(np.median(colour.reshape(-1, 3), 0)), _to_linear([150, 120, 90]), rtol=.08)
            for key in ("base_color_texture", "roughness_texture", "normal_texture"):
                image = np.asarray(Image.open(r["files"][key]), float)
                for axis in (0, 1):  # the wrap-around step is no harder than steps inside the image (joints included)
                    steps = np.abs(np.diff(image, axis=axis)).mean(axis=tuple(i for i in range(image.ndim) if i != axis))
                    wrap = np.abs(np.take(image, 0, axis)-np.take(image, -1, axis)).mean()
                    self.assertLessEqual(wrap, steps.max()+1, (kind, key, axis))
            fields = r["material"](root="task", base=self.root)
            self.assertEqual(fields["base_color_texture"], {"root": "task", "path": f"{kind}_color.png"})
            self.assertEqual(fields["uv_box_m"], r["size_m"])

    def test_plank_joints_are_dark_recessed_and_rough(self):
        r = make_texture("wood_planks", self.root / "oak", [150, 120, 90], plank_width_m=.2, plank_length_m=1.,
                         joint_m=.004, rows=2, courses=1)
        colour = np.asarray(Image.open(r["files"]["base_color_texture"]), float).mean(-1)
        rough = np.asarray(Image.open(r["files"]["roughness_texture"]), float)
        normal = np.asarray(Image.open(r["files"]["normal_texture"]), float)
        joint = colour.shape[0]//2  # the joint between the two rows
        self.assertLess(colour[joint].mean(), colour[joint//2].mean()-10)
        self.assertGreater(rough[joint].mean(), rough[joint//2].mean()+20)
        self.assertGreaterEqual(np.abs(normal[joint-2:joint+3, :, 1]-128).max(), 15)

    def test_unknown_kind_or_parameter_is_rejected(self):
        with self.assertRaises(ValueError):
            make_texture("marble", self.root / "x", [100, 100, 100])
        with self.assertRaises(ValueError):
            make_texture("plaster", self.root / "x", [100, 100, 100], tile_m=[.3, .3])
