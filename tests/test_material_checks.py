import contextlib
import copy
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest

import numpy as np
from PIL import Image

from indoor_recon.__main__ import main
from indoor_recon.contracts import ContractError
from indoor_recon.material_checks import check_materials, inspect_materials


class MaterialChecks(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.path = self.root / 'model.glb'
        self.binary = bytearray()
        self.doc = {'asset': {'version': '2.0'}, 'bufferViews': [], 'accessors': [],
                    'images': [], 'textures': [], 'materials': [], 'meshes': [],
                    'nodes': [{'name': 'floor', 'mesh': 0}], 'buffers': []}
        self.add_accessor([[0, 0, 0], [1, 0, 0], [0, 1, 0]], 'VEC3')
        self.add_accessor([[0, 0], [1, 0], [0, 1]], 'VEC2')
        spec = {'uv_box_m': [1, 1]}
        for key, colour in [('base_color_texture', (120, 80, 40)),
                            ('roughness_texture', (170, 170, 170)), ('normal_texture', (128, 128, 255))]:
            Image.new('RGB', (4, 4), colour).save(self.root / (key + '.png'))
            stream = io.BytesIO()
            # Export packing uses G for roughness; R is deliberately different.
            Image.new('RGB', (4, 4), (0, 170, 0) if key == 'roughness_texture' else colour).save(stream, format='PNG')
            self.doc['images'].append({'bufferView': self.add_view(stream.getvalue()), 'mimeType': 'image/png'})
            self.doc['textures'].append({'source': len(self.doc['textures'])})
            spec[key] = {'root': 'task', 'path': key + '.png'}
        self.doc['materials'] = [{'pbrMetallicRoughness': {'baseColorTexture': {'index': 0},
                                                        'metallicRoughnessTexture': {'index': 1}},
                                 'normalTexture': {'index': 2}}]
        self.doc['meshes'] = [{'primitives': [{'attributes': {'POSITION': 0, 'TEXCOORD_0': 1}, 'material': 0}]}]
        self.doc['extras'] = {'parameter_model': {'kind': 'model', 'schema_version': 2, 'scene_id': 'fixture',
                             'revision': 2, 'materials': {'wood': spec},
                             'objects': [{'id': 'floor', 'material_id': 'wood', 'geometry': {'type': 'surface'}}]}}
        self.roots = {'task': self.root}

    def add_view(self, data):
        self.binary.extend(b'\x00' * (-len(self.binary) % 4))
        index = len(self.doc['bufferViews'])
        self.doc['bufferViews'].append({'buffer': 0, 'byteOffset': len(self.binary), 'byteLength': len(data)})
        self.binary.extend(data)
        return index

    def add_accessor(self, data, kind):
        self.doc['accessors'].append({'bufferView': self.add_view(np.array(data, dtype='<f4').tobytes()),
                                      'componentType': 5126, 'count': len(data), 'type': kind})

    def write(self):
        self.doc['buffers'] = [{'byteLength': len(self.binary)}]
        body = json.dumps(self.doc).encode()
        body += b' ' * (-len(body) % 4)
        binary = bytes(self.binary) + b'\x00' * (-len(self.binary) % 4)
        self.path.write_bytes(struct.pack('<4sII', b'glTF', 2, 28 + len(body) + len(binary)) +
                              struct.pack('<I4s', len(body), b'JSON') + body +
                              struct.pack('<I4s', len(binary), b'BIN\x00') + binary)

    def check(self, **kwargs):
        self.write()
        return inspect_materials(self.path, self.roots, **kwargs)

    def test_actual_images_uv_and_roughness_green_channel_pass(self):
        report = self.check()
        self.assertTrue(report['passed'], report)
        self.assertEqual(report['images'], 3)
        self.assertEqual(len(report['source_images_sha256']), 3)
        self.assertIsNone(report['appearance_quality_passed'])

    def test_no_images_or_partial_maps_cannot_pass(self):
        original = copy.deepcopy(self.doc['materials'][0])
        for mat in ({}, {'pbrMetallicRoughness': original['pbrMetallicRoughness']}):
            self.doc['materials'][0] = mat
            self.assertFalse(self.check()['passed'])

    def test_a_textured_unused_material_does_not_hide_an_untextured_object(self):
        self.doc['materials'].append({})
        self.doc['meshes'][0]['primitives'][0]['material'] = 1
        self.assertFalse(self.check()['passed'])

    def test_empty_export_and_exceptions_cannot_hide_lost_declared_textures(self):
        self.doc['materials'][0] = {}
        self.assertFalse(self.check(exceptions={'wood': 'Uniform finish'})['passed'])
        self.doc['meshes'][0]['primitives'] = []
        self.assertFalse(self.check()['passed'])

    def test_missing_and_degenerate_uv_fail(self):
        attrs = self.doc['meshes'][0]['primitives'][0]['attributes']
        del attrs['TEXCOORD_0']
        self.assertFalse(self.check()['passed'])
        self.add_accessor([[0, 0]] * 3, 'VEC2')
        attrs['TEXCOORD_0'] = 2
        self.assertFalse(self.check()['passed'])

    def test_corrupt_external_and_wrong_channel_images_fail(self):
        original = copy.deepcopy(self.doc['images'][1])
        self.doc['images'][1] = {'uri': 'roughness.png'}
        self.assertFalse(self.check()['passed'])
        self.doc['images'][1] = original
        self.doc['textures'][1]['source'] = 0
        self.assertFalse(self.check()['passed'])
        self.doc['textures'][1]['source'] = 1
        self.doc['images'][1]['bufferView'] = self.add_view(b'not an image')
        self.assertFalse(self.check()['passed'])

    def test_source_image_changes_fail_and_output_never_overwrites(self):
        self.write()
        out = self.root / 'check'
        self.assertTrue(check_materials(self.path, out, self.roots)['passed'])
        with self.assertRaises(ContractError):
            check_materials(self.path, out, self.roots)
        Image.new('RGB', (4, 4), (1, 2, 3)).save(self.root / 'normal_texture.png')
        self.assertFalse(self.check()['passed'])

    def test_explicit_uniform_material_exception_and_missing_objects(self):
        self.doc['extras']['parameter_model']['materials']['wood'] = {'metallic': 1}
        self.doc['materials'][0] = {'pbrMetallicRoughness': {'metallicFactor': 1}}
        self.assertFalse(self.check()['passed'])
        self.assertTrue(self.check(exceptions={'wood': 'Uniform metal finish, reviewed in source photo'})['passed'])
        with self.assertRaises(ContractError):
            self.check(exceptions={'wood': ''})
        with self.assertRaises(ContractError):
            self.check(exceptions={'typo': 'glass'})
        self.doc['nodes'] = []
        self.assertFalse(self.check(exceptions={'wood': 'Uniform metal'})['passed'])

    def test_cli_failure_returns_nonzero_and_saves_report_and_activity(self):
        self.doc['materials'][0] = {}
        self.write()
        out = self.root / 'cli'
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(['check-materials', str(self.path), '--out', str(out), '--root', f'task={self.root}'])
        self.assertEqual(code, 1)
        self.assertFalse(json.loads((out / 'materials.json').read_text())['passed'])
        self.assertFalse(json.loads((out / 'activity.json').read_text())['passed'])


if __name__ == '__main__':
    unittest.main()
