"""HPMA stage export: rebuild a .scn scene as one glTF with every model in place."""

from __future__ import annotations

import io
import json
import math
import os
import re
import struct
import threading
import xml.etree.ElementTree as ET
from collections import OrderedDict
from pathlib import Path

import numpy as np

from core.npk.asset_paths import RESOURCE_FOLDER, manifest_lines, murmur3_32, normalize_path
from core.npk.npk_file import NPKFile
from core.npk.npkhash_v1 import mesh_hash


HPMA_DECRYPTION_KEY = -250


def read_options_for(archive=None):
    """
    The read options for the game's archives: the open archive's (they come from the active game config), else
    Harry Potter: Magic Awakened's own key. Safe to call from any thread.
    """
    options = getattr(archive, "options", None)
    if options is not None and getattr(options, "decryption_key", None) is not None:
        return options
    from core.npk.npk_file import NPKReadOptions
    return NPKReadOptions(decryption_key=HPMA_DECRYPTION_KEY)


def game_root_for(path) -> Path | None:
    """The folder that holds res, res2, ... for an archive inside it."""
    path = Path(path).resolve()
    for parent in path.parents:
        if RESOURCE_FOLDER.fullmatch(parent.name):
            return parent.parent
    return None


def resource_dirs(game: Path) -> list[Path]:
    dirs = [d for d in game.iterdir() if d.is_dir() and RESOURCE_FOLDER.fullmatch(d.name)]
    return sorted(dirs, key=lambda d: int(d.name[3:] or 0))


def game_layers(game: Path) -> list[Path]:
    """
    HPMA keeps a full install plus a patch copy in <install>\\Documents that only
    holds updated files. Return the patch layer first, then the base install.
    """
    game = Path(game)
    if game.name.lower() == 'documents' and resource_dirs(game.parent):
        return [game, game.parent]
    patch = game / 'Documents'
    if patch.is_dir() and resource_dirs(patch):
        return [patch, game]
    return [game]


def clean_asset_path(path: str) -> str:
    """Normalize scene paths, including absolute artist paths like D:\\...\\res\\levelsets_model\\x."""
    path = normalize_path(path)
    match = re.search(r'(?:^|\\)res\d*\\(.+)$', path)
    if match and ':' in path:
        path = match.group(1)
    return path


class PackIndex:
    """Find entries by resource path across every NPK under a game's res folders."""

    def __init__(self, game: Path, options, log=None):
        self.layers = game_layers(game)
        self.options = options
        self.log = log or (lambda message: None)
        self.packs: dict[str, list[Path]] = {}       # mount -> pack paths, patch layer and newest res first
        self.dlc_mounts: list[str] = []
        self._open: dict[Path, tuple[NPKFile, dict[int, int]]] = {}
        self._manifest: list[str] | None = None
        self._manifest_lock = threading.Lock()
        for layer in self.layers:
            for res in reversed(resource_dirs(layer)):
                for npk in res.rglob('*.npk'):
                    mount = normalize_path(str(npk.relative_to(res).with_suffix('')))
                    self.packs.setdefault(mount, []).append(npk)
        self.dlc_mounts = sorted(m for m in self.packs if m.startswith('res_dlc'))

    def manifest(self) -> list[str]:
        """Every resource path the install lists (built once per index; a copy each call)."""
        with self._manifest_lock:                     # two threads asking at once build it once
            if self._manifest is None:
                self._manifest = self._read_manifest()
        return list(self._manifest)

    def _read_manifest(self) -> list[str]:
        paths = []
        for layer in self.layers:
            for res in resource_dirs(layer):
                resdb = res / 'resdb'
                if resdb.is_file():
                    paths.extend(normalize_path(line.partition(' ')[2]) for line in manifest_lines(resdb))
        return list(OrderedDict.fromkeys(paths))

    def _pack(self, npk_path: Path):
        if npk_path not in self._open:
            npk = NPKFile(str(npk_path), self.options)
            signatures = {}
            for local, index in enumerate(npk.indices):
                signatures.setdefault(index.file_signature, local)
            self._open[npk_path] = (npk, signatures)
        return self._open[npk_path]

    def _candidates(self, path: str):
        parts = path.split('\\')
        for depth in range(len(parts) - 1, 0, -1):
            mount = '\\'.join(parts[:depth])
            for npk_path in self.packs.get(mount, ()):
                yield npk_path, path[len(mount) + 1:]
        for mount in self.dlc_mounts:
            for npk_path in self.packs[mount]:
                yield npk_path, path

    def locate(self, path: str):
        path = clean_asset_path(path)
        variants = [path]
        stem, _, extension = path.rpartition('.')
        if extension in ('tga', 'png', 'jpg', 'jpeg', 'bmp', 'dds'):
            # Materials often name a source image; the game ships a converted one
            variants += [f'{stem}.{other}' for other in ('dds', 'tga', 'png', 'ktx', 'pvr', 'astc') if other != extension]
        for variant in variants:
            hashes = None
            for npk_path, relative in self._candidates(variant):
                npk, signatures = self._pack(npk_path)
                for signature in (murmur3_32(relative), murmur3_32(variant), mesh_hash(variant)):
                    if signature in signatures:
                        return npk_path, signatures[signature], variant
        return None

    def read(self, path: str) -> tuple[bytes, str] | None:
        found = self.locate(path)
        if found is None:
            return None
        npk_path, local, actual = found
        npk, _ = self._pack(npk_path)
        with open(npk_path, 'rb') as stream:
            npk.load_entry(local, stream)
        entry = npk.entries.pop(local)
        return bytes(entry.data), actual

    def scenes(self) -> list[str]:
        """
        Every stage scene named in the resource manifests, wherever it lives (scene\\...,
        DLC packs' res_dlc_x\\scene\\..., a few under levelsets\\). *_content\\ helper
        scenes (music, road) place no models and are left out.
        """
        return sorted(p for p in self.manifest() if p.endswith('.scn') and '_content\\' not in p)

    def scene_size(self, path: str) -> int:
        """Uncompressed size of a scene file from the pack index (no unpacking)."""
        found = self.locate(path)
        if found is None:
            return 0
        npk, _signatures = self._pack(found[0])
        return int(getattr(npk.indices[found[1]], 'file_original_length', 0) or 0)


# Helper geometry that ships with stages but is not meant to be seen.
HIDDEN_NAME = re.compile(r'(?:^|[_\\])(?:col|collision|xunlu|navmesh|nav)(?:[_\d.]|$)', re.I)

# NeoX is left-handed (Y up, +Z forward); glTF is right-handed. Mirroring X keeps +Z forward.
MIRROR = np.diag([-1.0, 1.0, 1.0])


def _floats(text) -> list[float]:
    return [float(v) for v in re.findall(r'-?\d+(?:\.\d+)?(?:e-?\d+)?', text or '', re.I)]


def placement_matrix(model) -> np.ndarray:
    """Column-vector 4x4 world matrix of a scene <model>, still in NeoX space."""
    linear = np.eye(3)
    rotation = _floats(model.get('Rotation'))
    if len(rotation) == 16:
        # NeoX matrices are row-vector (v * M); transpose for column vectors
        linear = np.array(rotation).reshape(4, 4)[:3, :3].T
    scale = _floats(model.get('Scale'))
    if len(scale) == 3 and all(abs(s) > 1e-8 for s in scale):
        linear = linear @ np.diag(scale)
    matrix = np.eye(4)
    matrix[:3, :3] = linear
    position = _floats(model.get('Position'))
    if len(position) == 3:
        matrix[:3, 3] = position
    return matrix


def parse_scene(data: bytes):
    """Return (file list, placements) where each placement is (name, asset path, world matrix)."""
    root = ET.fromstring(data)
    files = {}
    for element in root.iter():
        match = re.fullmatch(r'File_(\d+)', element.tag)
        if match and element.get('Path'):
            files[int(match.group(1))] = clean_asset_path(element.get('Path'))
    placements = []

    def visit(element, parent_matrix):
        for child in element:
            if child.tag == 'model':
                matrix = parent_matrix @ placement_matrix(child)
                path = files.get(int(child.get('FilePathIndex', -1)))
                if path:
                    placements.append((child.get('Name') or Path(path).stem, path, matrix))
                visit(child, matrix)
            else:
                visit(child, parent_matrix)

    visit(root, np.eye(4))
    return files, placements


def _texture_extension(data: bytes) -> str:
    if data[:4] == b'DDS ':
        return 'dds'
    if data[:4] == b'\xabKTX':
        return 'ktx'
    if data[:4] in (b'PVR\x03', b'\x03RVP'):
        return 'pvr'
    if data[:4] == b'\x13\xab\xa1\x5c':
        return 'astc'
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return 'png'
    return 'tga'


def decode_texture(data: bytes):
    """Decode DDS/KTX/PVR/ASTC/PNG/TGA bytes into an RGB or RGBA PIL image."""
    from core.images import convert_image
    from PIL import Image
    kind = _texture_extension(data)
    image = convert_image(data, kind) if kind != 'tga' else None
    if image is None:
        image = Image.open(io.BytesIO(data), formats=['TGA'] if kind == 'tga' else None)
        image.load()
    if image.mode not in ('RGB', 'RGBA'):
        image = image.convert('RGBA')
    return image


def image_has_alpha(image) -> bool:
    return image.mode == 'RGBA' and image.getchannel('A').getextrema()[0] < 250


def alpha_mode(slot: dict, has_alpha: bool) -> tuple[str, float]:
    """
    glTF-style alpha mode and cutoff for a material slot. NeoX TransparentMode:
    1 = opaque (texture alpha is used for other things), 2 = alpha blend, 3 = alpha test.
    """
    mode = slot.get('mode', '0')
    reference = slot.get('alpha_ref', 0) / 255
    if mode == '1' or not has_alpha:
        return 'OPAQUE', 0.5
    if mode == '2':
        return 'BLEND', 0.5
    # Small AlphaRef values (1, 4, ...) are not real thresholds; the shaders test around half
    return 'MASK', reference if reference >= 0.25 else 0.5


def material_slots(index: PackIndex, gim_path: str, gim=None, report=None):
    """
    Read a model's material group (.mtg, or .mtl next to the model).
    Returns (material path, [{'name', 'texture', 'mode', 'alpha_ref', 'cull'}, ...]).
    """
    mtg_element = gim.find('MtgFile') if gim is not None else None
    stem = gim_path.rsplit('.', 1)[0]
    mtg_path = clean_asset_path(mtg_element.get('MtgPath')) if mtg_element is not None else stem + '.mtg'
    local = stem + '.mtg'
    if mtg_path.lower() != local.lower() and mtg_path.rsplit('\\', 1)[0].lower() != stem.rsplit('\\', 1)[0].lower()             and index.locate(local) is not None:
        # The model points at a look-alike folder's material (x_jmr_02's for x_jmr\x_jmr_02, x_hazx02's for
        # x_hazx_02) while its own sits next to it: that one is made for this mesh (sub-meshes, texture layout)
        mtg_path = local
    found = index.read(mtg_path) or index.read(stem + '.mtl')
    slots = []
    if found is None:
        if report is not None:
            report.append(f'missing material: {mtg_path}')
        return mtg_path, slots
    for slot, material in enumerate(ET.fromstring(found[0]).iter('Material')):
        params = material.find('ParamTable')
        texture_path = None
        for name in ('Tex0', 'DiffuseMap', 'BaseMap', 'AlbedoMap', 'Tex'):
            element = params.find(name) if params is not None else None
            if element is not None and element.get('Value'):
                texture_path = element.get('Value')
                break
        states = material.find('RenderStates')
        transparent = material.find('RenderStates/TransparentMode')
        slots.append({
            'name': material.get('Name') or f'{Path(mtg_path).stem}_{slot}',
            'texture': texture_path,
            'mode': transparent.get('TransparentMode', '0') if transparent is not None else '0',
            'alpha_ref': int(float(states.get('AlphaRef', '0'))) if states is not None else 0,
            'cull': states.get('CullMode', '2') if states is not None else '2',
        })
    return mtg_path, slots


class StageExporter:
    """Write one stage (.scn) as <out>/<stage>/<stage>.gltf with textures beside it."""

    def __init__(self, index: PackIndex, progress=None, cancelled=None, include_hidden=False):
        self.index = index
        self.progress = progress or (lambda text, value, total: None)
        self.cancelled = cancelled or (lambda: False)
        self.include_hidden = include_hidden
        self.report: list[str] = []

    # -- glTF buffers --------------------------------------------------------
    def _accessor(self, gltf, binary, array, component, kind, target=None, minmax=False):
        array = np.ascontiguousarray(array)
        while len(binary) % 4:
            binary.append(0)
        offset = len(binary)
        binary.extend(array.tobytes())
        view = {'buffer': 0, 'byteOffset': offset, 'byteLength': array.nbytes}
        if target:
            view['target'] = target
        gltf['bufferViews'].append(view)
        accessor = {'bufferView': len(gltf['bufferViews']) - 1, 'componentType': component,
                    'count': int(array.shape[0]), 'type': kind}
        if minmax:
            accessor['min'] = array.min(axis=0).astype(float).tolist()
            accessor['max'] = array.max(axis=0).astype(float).tolist()
        gltf['accessors'].append(accessor)
        return len(gltf['accessors']) - 1

    # -- assets --------------------------------------------------------------
    def _texture(self, gltf, path, folder, cache):
        if path in cache:
            return cache[path]
        cache[path] = None
        found = self.index.read(path)
        if found is None:
            self.report.append(f'missing texture: {path}')
            return None
        data, actual = found
        try:
            image = decode_texture(data)
        except Exception as exc:
            self.report.append(f'unreadable texture: {actual} ({exc})')
            return None
        name = Path(actual).stem
        used = {img['uri'] for img in gltf['images']}
        file_name, n = f'{name}.png', 1
        while f'textures/{file_name}' in used:
            n += 1
            file_name = f'{name}_{n}.png'
        (folder / 'textures').mkdir(parents=True, exist_ok=True)
        image.save(folder / 'textures' / file_name)
        has_alpha = image_has_alpha(image)
        gltf['images'].append({'uri': f'textures/{file_name}', 'name': name})
        gltf['textures'].append({'source': len(gltf['images']) - 1, 'sampler': 0})
        cache[path] = (len(gltf['textures']) - 1, has_alpha)
        return cache[path]

    def _materials(self, gltf, gim_path, gim, folder, texture_cache, material_cache):
        mtg_element = gim.find('MtgFile')
        mtg_path = clean_asset_path(mtg_element.get('MtgPath')) if mtg_element is not None else gim_path[:-4] + '.mtg'
        if mtg_path in material_cache:
            return material_cache[mtg_path]
        mtg_path, slots = material_slots(self.index, gim_path, gim, self.report)
        materials = []
        if slots:
            for slot in slots:
                texture_path = slot['texture']
                gl_material = {'name': slot['name'],
                               'pbrMetallicRoughness': {'metallicFactor': 0.0, 'roughnessFactor': 1.0},
                               'doubleSided': slot['cull'] in ('0', '1')}
                texture = self._texture(gltf, texture_path, folder, texture_cache) if texture_path else None
                if texture is not None:
                    gl_material['pbrMetallicRoughness']['baseColorTexture'] = {'index': texture[0]}
                    mode, cutoff = alpha_mode(slot, texture[1])
                    if mode != 'OPAQUE':
                        gl_material['alphaMode'] = mode
                    if mode == 'MASK':
                        gl_material['alphaCutoff'] = cutoff
                gltf['materials'].append(gl_material)
                materials.append(len(gltf['materials']) - 1)
        material_cache[mtg_path] = materials
        return materials

    def _mesh(self, gltf, binary, gim_path, folder, caches):
        from core.mesh_loader import MeshLoader
        gim_data = self.index.read(gim_path)
        if gim_data is None:
            self.report.append(f'missing model: {gim_path}')
            return None
        gim = ET.fromstring(gim_data[0])
        mesh_data = self.index.read(gim_path[:-4] + '.mesh')
        mesh = MeshLoader().load_from_bytes(mesh_data[0]) if mesh_data else None
        if mesh is None or not mesh.position:
            self.report.append(f'missing mesh: {gim_path[:-4]}.mesh')
            return None
        materials = self._materials(gltf, gim_path, gim, folder, *caches)
        submeshes = sorted((s for s in gim.iter() if re.fullmatch(r'Sub\d+', s.tag)), key=lambda s: int(s.tag[3:]))

        positions = (np.asarray(mesh.position, np.float32) @ MIRROR.T).astype(np.float32)
        attributes = {'POSITION': self._accessor(gltf, binary, positions, 5126, 'VEC3', 34962, True)}
        if mesh.normal and len(mesh.normal) == len(mesh.position):
            normals = (np.asarray(mesh.normal, np.float32) @ MIRROR.T).astype(np.float32)
            lengths = np.linalg.norm(normals, axis=1, keepdims=True)
            normals = np.where(lengths > 1e-8, normals / np.maximum(lengths, 1e-8), [0, 1, 0]).astype(np.float32)
            attributes['NORMAL'] = self._accessor(gltf, binary, normals, 5126, 'VEC3', 34962)
        if mesh.uv and len(mesh.uv) == len(mesh.position):
            attributes['TEXCOORD_0'] = self._accessor(gltf, binary, np.asarray(mesh.uv, np.float32), 5126, 'VEC2', 34962)

        faces = np.asarray(mesh.face, np.uint32)[:, [0, 2, 1]]   # mirrored, so reverse the winding
        ranges = [part[1] for part in mesh.mesh] if mesh.mesh else [len(faces)]
        primitives, start = [], 0
        for part, count in enumerate(ranges):
            chunk = faces[start:start + count]
            start += count
            if not len(chunk):
                continue
            primitive = {'attributes': attributes,
                         'indices': self._accessor(gltf, binary, chunk.reshape(-1), 5125, 'SCALAR', 34963)}
            slot = int(submeshes[part].get('MtlIdx', part)) if part < len(submeshes) else part
            if slot < len(materials):
                primitive['material'] = materials[slot]
            primitives.append(primitive)
        gltf['meshes'].append({'name': Path(gim_path).stem, 'primitives': primitives})
        return len(gltf['meshes']) - 1

    # -- scene ---------------------------------------------------------------
    def export(self, scene_path: str, out_dir) -> Path:
        scene_path = clean_asset_path(scene_path)
        found = self.index.read(scene_path)
        if found is None:
            raise FileNotFoundError(f'Scene not found: {scene_path}')
        _files, placements = parse_scene(found[0])
        stage = Path(scene_path).stem
        folder = Path(out_dir) / stage
        folder.mkdir(parents=True, exist_ok=True)

        gltf = {'asset': {'version': '2.0', 'generator': 'NeoXtractor HPMA stage export'},
                'scene': 0, 'scenes': [{'name': stage, 'nodes': [0]}],
                'nodes': [{'name': stage, 'children': []}], 'meshes': [], 'materials': [],
                'textures': [], 'images': [], 'samplers': [{'wrapS': 10497, 'wrapT': 10497}],
                'accessors': [], 'bufferViews': [], 'buffers': []}
        binary = bytearray()
        mesh_cache: dict[str, int | None] = {}
        caches = ({}, {})
        skipped = 0
        for number, (name, path, matrix) in enumerate(placements):
            if self.cancelled():
                raise InterruptedError('Stage export cancelled')
            self.progress(f'Exporting {stage}: {name}', number, len(placements))
            if not path.endswith('.gim'):
                continue
            if not self.include_hidden and (HIDDEN_NAME.search(name) or HIDDEN_NAME.search(Path(path).stem)):
                skipped += 1
                continue
            if path not in mesh_cache:
                mesh_cache[path] = self._mesh(gltf, binary, path, folder, caches)
            if mesh_cache[path] is None:
                continue
            world = np.eye(4)
            world[:3, :3] = MIRROR @ matrix[:3, :3] @ MIRROR
            world[:3, 3] = MIRROR @ matrix[:3, 3]
            gltf['nodes'].append({'name': name, 'mesh': mesh_cache[path],
                                  'matrix': world.T.reshape(-1).tolist()})
            gltf['nodes'][0]['children'].append(len(gltf['nodes']) - 1)

        for key in ('meshes', 'materials', 'textures', 'images'):
            if not gltf[key]:
                del gltf[key]
        if 'textures' not in gltf:
            del gltf['samplers']
        bin_name = f'{stage}.bin'
        (folder / bin_name).write_bytes(bytes(binary))
        gltf['buffers'] = [{'uri': bin_name, 'byteLength': len(binary)}]
        target = folder / f'{stage}.gltf'
        target.write_text(json.dumps(gltf, ensure_ascii=False), encoding='utf-8')

        placed = len(gltf['nodes']) - 1
        summary = [f'Stage: {scene_path}', f'Placed objects: {placed} of {len(placements)}',
                   f'Unique models: {sum(1 for v in mesh_cache.values() if v is not None)}',
                   f'Hidden helpers skipped (collision/navigation): {skipped}',
                   f'Textures: {len(gltf.get("images", []))}']
        (folder / 'export_report.txt').write_text('\n'.join(summary + [''] + sorted(set(self.report))), encoding='utf-8')
        self.progress(f'Exported {stage}', len(placements), len(placements))
        return target


class StageLoader:
    """Loads a stage or single models for the Scene Viewer: their meshes, positions and textures."""

    def __init__(self, index: PackIndex, progress=None, cancelled=None, include_hidden=False, max_texture_size=2048):
        self.index = index
        self.progress = progress or (lambda text, value, total: None)
        self.cancelled = cancelled or (lambda: False)
        self.include_hidden = include_hidden
        self.max_texture_size = max_texture_size
        self.report: list[str] = []
        self._models: dict[str, tuple] = {}
        self._textures: dict[str, object] = {}
        self._clips: dict[str, object] = {}

    def clip(self, model_path: str):
        """The model's auto-play animation loop (neox_anim.Clip), or None; cached per model."""
        from core.npk.neox_anim import load_default_clip
        model_path = clean_asset_path(model_path)
        if model_path not in self._clips:
            self._clips[model_path] = load_default_clip(self.index.read, model_path, self.report)
        return self._clips[model_path]

    def texture(self, path: str | None):
        """Decoded PIL image for a texture path (None when missing), cached by path."""
        if not path:
            return None
        if path not in self._textures:
            self._textures[path] = None
            found = self.index.read(path)
            if found is None:
                self.report.append(f'missing texture: {path}')
            else:
                try:
                    image = decode_texture(found[0])
                    if max(image.size) > self.max_texture_size:
                        image.thumbnail((self.max_texture_size, self.max_texture_size))
                    self._textures[path] = image
                except Exception as exc:
                    self.report.append(f'unreadable texture: {found[1]} ({exc})')
        return self._textures[path]

    def parts_for(self, model_path: str, mesh, gim=None) -> list[dict]:
        """One entry per sub-mesh: material name, texture path/image and transparency."""
        _mtg_path, slots = material_slots(self.index, model_path, gim, self.report)
        submeshes = []
        if gim is not None:
            submeshes = sorted((s for s in gim.iter() if re.fullmatch(r'Sub\d+', s.tag)), key=lambda s: int(s.tag[3:]))
        count = len(mesh.mesh) if mesh.mesh else 1
        parts = []
        for number in range(count):
            slot_index = int(submeshes[number].get('MtlIdx', number)) if number < len(submeshes) else number
            # a sub-mesh naming a material the group doesn't have (male_clothes_160_glkx's shoes ask for #1 of
            # one): the game falls back to the group's last material rather than drawing it untextured
            slot = slots[slot_index] if slot_index < len(slots) else (slots[-1] if slots else None)
            texture = slot['texture'] if slot else None
            if texture and self.index.locate(texture) is None:
                # A material shared from a look-alike folder (x_hazx02 for x_hazx_02) can name its texture in
                # that folder; the file sits next to the model
                own = model_path.replace('/', '\\').rsplit('\\', 1)[0] + '\\' + texture.rsplit('\\', 1)[-1]
                if own.lower() != texture.lower() and self.index.locate(own) is not None:
                    texture = own
            image = self.texture(texture) if texture else None
            alpha, cutoff = alpha_mode(slot, image is not None and image_has_alpha(image)) if slot else ('OPAQUE', 0.5)
            parts.append({
                'material': slot['name'] if slot else f'part {number}',
                'texture': texture,
                'image': image,
                'alpha': alpha,
                'cutoff': cutoff,
            })
        return parts

    def model(self, model_path: str):
        """(MeshData, parts) for a .gim or .mesh path, or None when the mesh is missing."""
        from core.mesh_loader import MeshLoader
        model_path = clean_asset_path(model_path)
        if model_path in self._models:
            return self._models[model_path]
        stem = model_path.rsplit('.', 1)[0]
        gim = None
        gim_data = self.index.read(stem + '.gim')
        if gim_data is not None:
            try:
                gim = ET.fromstring(gim_data[0])
            except ET.ParseError:
                gim = None
        mesh_data = None
        if gim is not None and gim.get('Mesh'):          # a .gim may point at a differently named .mesh
            mesh_data = self.index.read(clean_asset_path(gim.get('Mesh')))
        mesh_data = mesh_data or self.index.read(stem + '.mesh')
        mesh = MeshLoader().load_from_bytes(mesh_data[0]) if mesh_data else None
        if mesh is None or not mesh.position:
            self.report.append(f'missing mesh: {stem}.mesh')
            self._models[model_path] = None
            return None
        result = (mesh, self.parts_for(stem + '.gim', mesh, gim))
        self._models[model_path] = result
        return result

    def load(self, scene_path: str) -> list[dict]:
        """Every visible placement of a stage: name, path, NeoX world matrix, mesh and parts."""
        scene_path = clean_asset_path(scene_path)
        found = self.index.read(scene_path)
        if found is None:
            raise FileNotFoundError(f'Scene not found: {scene_path}')
        _files, placements = parse_scene(found[0])
        stage = Path(scene_path).stem
        objects = []
        for number, (name, path, matrix) in enumerate(placements):
            if self.cancelled():
                raise InterruptedError('Stage loading cancelled')
            self.progress(f'Loading {stage}: {name}', number, len(placements))
            if not path.endswith('.gim'):
                continue
            if not self.include_hidden and (HIDDEN_NAME.search(name) or HIDDEN_NAME.search(Path(path).stem)):
                continue
            model = self.model(path)
            if model is None:
                continue
            clip = self.clip(path) if model[0].bone_matrix else None
            objects.append({'name': name, 'path': path, 'matrix': matrix, 'mesh': model[0], 'parts': model[1],
                            'animation': clip})
        self.progress(f'Loaded {stage}', len(placements), len(placements))
        return objects
