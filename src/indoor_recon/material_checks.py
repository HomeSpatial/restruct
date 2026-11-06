# SPDX-License-Identifier: Apache-2.0
"""Check material coverage, embedded PBR pixels and UVs in a model-v2 export."""
import io
import json
from pathlib import Path
import struct

import numpy as np
from PIL import Image

from .contracts import ContractError, sha256
from .jobs import atomic_json
from .scene_model import resolve


def inspect_materials(path, roots=None, exceptions=None):
    """Exceptions map parameter material IDs to reasons (e.g. uniform glass).

    With roots, also compare exported pixels to all explicit parameter textures.
    This checks the export contract, not whether a material matches a photograph.
    """
    path = Path(path).resolve()
    raw = path.read_bytes()
    if len(raw) < 20 or struct.unpack_from('<4sII', raw) != (b'glTF', 2, len(raw)):
        raise ContractError('Expected a complete GLB v2 file')
    chunks = {}
    offset = 12
    while offset < len(raw):
        if offset + 8 > len(raw):
            raise ContractError('Truncated GLB chunk')
        size, kind = struct.unpack_from('<I4s', raw, offset)
        offset += 8
        if offset + size > len(raw) or kind in chunks:
            raise ContractError('Invalid GLB chunk')
        chunks[kind] = raw[offset:offset + size]
        offset += size
    doc = json.loads(chunks[b'JSON'])
    binary = chunks.get(b'BIN\x00', b'')
    model = doc.get('extras', {}).get('parameter_model', {})
    if model.get('kind') != 'model' or model.get('schema_version') != 2:
        raise ContractError('Material checks require an embedded parameter model v2')
    exceptions = exceptions or {}
    if not isinstance(exceptions, dict) or any(not isinstance(v, str) or not v.strip() for v in exceptions.values()):
        raise ContractError('Exceptions must map material IDs to nonempty reasons')
    unknown = set(exceptions) - set(model['materials'])
    if unknown:
        raise ContractError('Unknown exception material IDs: ' + ', '.join(sorted(unknown)))
    errors, rows, images, source_hashes, originals = [], [], {}, {}, {}

    def view(index):
        item = doc['bufferViews'][index]
        buffer = doc['buffers'][item['buffer']]
        if item['buffer'] != 0 or 'uri' in buffer:
            raise ValueError('Expected an embedded binary buffer')
        start, length = item.get('byteOffset', 0), item['byteLength']
        if start < 0 or length < 0 or start + length > len(binary):
            raise ValueError('Buffer view exceeds binary payload')
        return binary[start:start + length]

    def accessor(index):
        item = doc['accessors'][index]
        if 'sparse' in item:
            raise ValueError('Sparse accessors are not supported by this check')
        dtype = np.dtype({5121: 'u1', 5123: '<u2', 5125: '<u4', 5126: '<f4'}[item['componentType']])
        width = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4}[item['type']]
        stride = doc['bufferViews'][item['bufferView']].get('byteStride', width * dtype.itemsize)
        values = np.ndarray((item['count'], width), dtype=dtype, buffer=view(item['bufferView']),
                            offset=item.get('byteOffset', 0), strides=(stride, dtype.itemsize)).astype(float)
        if item.get('normalized') and dtype.kind == 'u':
            values /= np.iinfo(dtype).max
        if not np.isfinite(values).all():
            raise ValueError('Nonfinite accessor')
        return values

    def image(index):
        if index not in images:
            item = doc['images'][index]
            if 'uri' in item or 'bufferView' not in item:
                raise ValueError('Image is not embedded')
            with Image.open(io.BytesIO(view(item['bufferView']))) as decoded:
                decoded.load()
                images[index] = np.asarray(decoded.convert('RGB')).copy()
        return images[index]

    for index in range(len(doc.get('images', []))):
        try:
            image(index)
        except (ValueError, OSError, KeyError, IndexError) as error:
            errors.append({'image': index, 'error': str(error)})

    objects = {o['id']: o for o in model['objects']}
    seen, used_exceptions = set(), set()
    for node in doc.get('nodes', []):
        if 'mesh' not in node:
            continue
        identity = node.get('extras', {}).get('hs_id', node.get('name'))
        if identity in seen or identity not in objects:
            errors.append({'object_id': identity, 'error': 'Duplicate or unknown exported object'})
            continue
        seen.add(identity)
        material_id = objects[identity].get('material_id')
        spec = model['materials'].get(material_id, {})
        exempt = material_id in exceptions
        if exempt:
            used_exceptions.add(material_id)
        primitives = doc['meshes'][node['mesh']]['primitives']
        if not primitives:
            errors.append({'object_id': identity, 'error': 'Exported object has no primitives'})
        for number, primitive in enumerate(primitives):
            row = {'object_id': identity, 'primitive': number, 'material_id': material_id,
                   'exception': exceptions.get(material_id), 'textures': {}, 'errors': []}
            try:
                if 'material' not in primitive:
                    raise ValueError('Exported primitive has no material')
                actual = doc['materials'][primitive['material']]
                pbr = actual.get('pbrMetallicRoughness', {})
                slots = [('base_color_texture', pbr.get('baseColorTexture')),
                         ('roughness_texture', pbr.get('metallicRoughnessTexture')),
                         ('normal_texture', actual.get('normalTexture'))]
                for key, slot in slots:
                    if slot is None:
                        if not exempt or key in spec:
                            row['errors'].append('Missing ' + key)
                        continue
                    pixels = image(doc['textures'][slot['index']]['source'])
                    row['textures'][key] = {'width': pixels.shape[1], 'height': pixels.shape[0]}
                    uv_key = 'TEXCOORD_' + str(slot.get('texCoord', 0))
                    if uv_key not in primitive['attributes']:
                        raise ValueError('Missing ' + uv_key)
                    uv = accessor(primitive['attributes'][uv_key])
                    xyz = accessor(primitive['attributes']['POSITION'])
                    if uv.shape != (len(xyz), 2):
                        raise ValueError('UV shape differs from positions')
                    if primitive.get('mode', 4) != 4:
                        raise ValueError('Expected triangle primitives')
                    indices = (accessor(primitive['indices']).ravel().astype(int) if 'indices' in primitive
                               else np.arange(len(xyz))).reshape(-1, 3)
                    tri, tex = xyz[indices], uv[indices]
                    area = np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
                    a, b = tex[:, 1] - tex[:, 0], tex[:, 2] - tex[:, 0]
                    if np.any((area > 1e-12) & (np.abs(a[:, 0]*b[:, 1] - a[:, 1]*b[:, 0]) <= 1e-15)):
                        raise ValueError('Degenerate UV triangles')
                    if roots is not None and key in spec:
                        source = resolve(spec[key], roots)
                        if source not in originals:
                            source_hashes[str(source)] = sha256(source)
                            with Image.open(source) as original:
                                originals[source] = np.asarray(original.convert('RGB')).copy()
                        wanted = originals[source]
                        actual_pixels = pixels[:, :, 1] if key == 'roughness_texture' else pixels
                        wanted_pixels = wanted[:, :, 0] if key == 'roughness_texture' else wanted
                        if not np.array_equal(actual_pixels, wanted_pixels):
                            row['errors'].append(key + ' pixels differ from source' + (' (roughness G channel)' if key == 'roughness_texture' else ''))
                if not exempt and not spec.get('source_mesh') and 'uv_box_m' not in spec and 'uv_work' not in spec:
                    row['errors'].append('Missing metric UV mapping in parameters')
            except (ValueError, OSError, KeyError, IndexError) as error:
                row['errors'].append(str(error))
            rows.append(row)
    expected = {o['id'] for o in objects.values() if o['geometry']['type'] != 'assembly'
                and not (o['geometry']['type'] == 'opening' and o['geometry'].get('fill', 'none') == 'none')}
    for identity in sorted(expected - seen):
        errors.append({'object_id': identity, 'error': 'Object is missing from export'})
    if not seen:
        errors.append({'error': 'No rendered objects'})
    return {'passed': not errors and all(not r['errors'] for r in rows),
            'scene_id': model.get('scene_id'), 'revision': model.get('revision'), 'model_sha256': sha256(path),
            'objects': len(seen), 'images': len(images), 'textured_primitives': sum(len(r['textures']) == 3 for r in rows),
            'source_pixels_checked': roots is not None, 'source_images_sha256': source_hashes,
            'exceptions': exceptions, 'unused_exceptions': sorted(set(exceptions) - used_exceptions),
            'errors': errors, 'primitives': rows, 'appearance_quality_passed': None}


def check_materials(path, output, roots=None, exceptions=None):
    output = Path(output).resolve()
    if output.exists():
        raise ContractError('Material checks require a new output directory')
    report = inspect_materials(path, roots, exceptions)
    output.mkdir(parents=True)
    atomic_json(output / 'materials.json', report)
    return report
