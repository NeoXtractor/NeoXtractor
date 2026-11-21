"""
Scene Viewer rendering: the Direct3D 11 render widget, the shaders, scene objects and parts, and the
image helpers they use.
"""

from __future__ import annotations

import ctypes
import itertools
import math
import os
import sys
import time
import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets
from core.logger import get_logger
from core.utils import get_application_path
from gui.utils.rendering import grid
from gui.widgets.viewers.mesh_viewer.camera_controller import CameraController


# NeoX is left-handed; the viewer is right-handed. Mirroring X matches the Mesh Viewer.
MIRROR = np.diag([-1.0, 1.0, 1.0, 1.0])


BONE_COLOR = (1.0, 0.82, 0.25)


HIGHLIGHT = 1.0


UPLOAD_BUDGET_SECONDS = 0.03


VERTEX_SHADER = """
cbuffer Camera : register(b0) { row_major float4x4 view_proj; float4 light_dir; };
cbuffer Draw : register(b2) { row_major float4x4 model; float4 base_color; float4 params; };
struct VSIn { float3 pos : TEXCOORD0; float3 nrm : TEXCOORD1; float2 uv : TEXCOORD2; };
struct VSOut { float4 position : SV_Position; float3 nrm : TEXCOORD0; float2 uv : TEXCOORD1; };
VSOut main(VSIn i)
{
    VSOut o;
    float4 world = mul(float4(i.pos, 1.0), model);
    o.position = mul(world, view_proj);
    o.nrm = mul(float4(i.nrm, 0.0), model).xyz;
    o.uv = i.uv;
    return o;
}
"""


# params: x = has texture, y = alpha cutoff (0 = none), z = highlight, w = unused
PIXEL_SHADER = """
cbuffer Camera : register(b0) { row_major float4x4 view_proj; float4 light_dir; };
cbuffer Draw : register(b2) { row_major float4x4 model; float4 base_color; float4 params; };
Texture2D tex : register(t1);
SamplerState samp : register(s1);
struct VSOut { float4 position : SV_Position; float3 nrm : TEXCOORD0; float2 uv : TEXCOORD1; };
float4 main(VSOut i) : SV_Target
{
    float4 c = base_color;
    if (params.x > 0.5)
        c *= tex.Sample(samp, i.uv);
    if (c.a < params.y)
        discard;
    float3 n = i.nrm;
    float len_n = length(n);
    float light = 1.0;
    if (len_n > 1e-5)
        light = 0.38 + 0.62 * abs(dot(n / len_n, normalize(light_dir.xyz)));
    float3 rgb = c.rgb * light;
    rgb = lerp(rgb, float3(1.0, 0.62, 0.15), params.z * 0.35);
    return float4(rgb, c.a);
}
"""


def _blob(array) -> ctypes.Array:
    array = np.ascontiguousarray(array)
    return (ctypes.c_char * array.nbytes).from_buffer_copy(array.tobytes())


def _hlsl_shader(stage, source: str) -> QtGui.QShader:
    shader = QtGui.QShader()
    shader.setStage(stage)
    shader.setShader(QtGui.QShaderKey(QtGui.QShader.Source.HlslShader, QtGui.QShaderVersion(50)),
                     QtGui.QShaderCode(QtCore.QByteArray(source.encode()), QtCore.QByteArray(b"main")))
    return shader


def _qsb_shader(name: str) -> QtGui.QShader:
    with open(os.path.join(get_application_path(), "data", "shaders", name), "rb") as stream:
        return QtGui.QShader.fromSerialized(stream.read())


# Textures made in the app (hair and makeup) are kept in memory for the Scene Viewer and only written to disk when
# exported.
MEMORY_TEXTURES: dict[str, object] = {}


_MEMORY_IDS = itertools.count(1)


def remember_texture(image) -> str:
    key = f"memory:{next(_MEMORY_IDS)}"
    MEMORY_TEXTURES[key] = image
    return key


def pil_to_qimage(image) -> QtGui.QImage:
    image = image.convert("RGBA")
    data = image.tobytes()
    qimage = QtGui.QImage(data, image.width, image.height, image.width * 4, QtGui.QImage.Format.Format_RGBA8888)
    return qimage.copy()


def decode_image_bytes(data: bytes):
    from core.npk.stage_export import decode_texture
    return decode_texture(data)


def _destroy(*resources):
    for resource in resources:
        if resource is not None:
            resource.destroy()


class ScenePart:
    """One sub-mesh draw with its own material state."""

    def __init__(self, first: int, count: int, material: str):
        self.first = first
        self.count = count
        self.material = material
        self.texture_key: str | None = None
        self.alpha = "OPAQUE"           # OPAQUE, MASK or BLEND
        self.cutoff = 0.5
        self.highlight = False
        self.ubuf = None
        self.srb = None
        self.srb_texture = None
        self.dirty = True

    def release(self):
        _destroy(self.srb, self.ubuf)
        self.srb = self.ubuf = self.srb_texture = None
        self.dirty = True


class SceneObject:
    """A mesh placed in the scene: geometry in viewer space, parts and rig lines."""

    _ids = itertools.count(1)

    def __init__(self, name: str, mesh, matrix=None, path: str | None = None, source: str = ""):
        self.id = next(self._ids)
        self.name = name
        self.path = path
        self.source = source
        self.visible = True
        self.group: str | None = None
        world = np.eye(4) if matrix is None else np.asarray(matrix, float)
        self.mesh = mesh                  # kept for exporting
        self.neox_matrix = world
        self.matrix = MIRROR @ world @ MIRROR

        count = len(mesh.position)
        positions = np.asarray(mesh.position, np.float32).reshape(-1, 3) * np.float32([-1, 1, 1])
        normals = np.zeros((count, 3), np.float32)
        if mesh.normal and len(mesh.normal) == count:
            normals = np.asarray(mesh.normal, np.float32).reshape(-1, 3) * np.float32([-1, 1, 1])
        uvs = np.zeros((count, 2), np.float32)
        if mesh.uv and len(mesh.uv) == count:
            uvs = np.asarray(mesh.uv, np.float32).reshape(-1, 2)
        self.vertices = np.hstack([positions, normals, uvs]).astype(np.float32)
        faces = np.asarray(mesh.face, np.uint32).reshape(-1, 3)[:, [0, 2, 1]]   # mirrored: reverse winding
        self.indices = faces.reshape(-1)
        self.vertex_count = count
        self.triangle_count = len(faces)

        ranges = [part[1] for part in mesh.mesh] if mesh.mesh else [len(faces)]
        self.parts: list[ScenePart] = []
        start = 0
        for number, face_count in enumerate(ranges):
            face_count = min(face_count, len(faces) - start)
            if face_count <= 0:
                continue
            self.parts.append(ScenePart(start * 3, face_count * 3, f"part {number}"))
            start += face_count

        low, high = (positions.min(0), positions.max(0)) if count else (np.zeros(3), np.zeros(3))
        corners = np.array([[x, y, z, 1.0] for x in (low[0], high[0]) for y in (low[1], high[1]) for z in (low[2], high[2])])
        world_corners = corners @ self.matrix.T
        self.bounds = (world_corners[:, :3].min(0), world_corners[:, :3].max(0))

        self.bone_count = len(mesh.bone_name) if mesh.bone_matrix else 0
        self.bone_lines = np.zeros((0, 6), np.float32)
        if mesh.bone_matrix and mesh.bone_parent:
            joints = np.array([np.asarray(m)[3, :3] for m in mesh.bone_matrix], float) * [-1, 1, 1]
            joints = np.hstack([joints, np.ones((len(joints), 1))]) @ self.matrix.T
            lines = []
            for bone, parent in enumerate(mesh.bone_parent):
                if 0 <= parent < len(joints) and bone < len(joints):
                    lines.append((*joints[parent, :3], *BONE_COLOR))
                    lines.append((*joints[bone, :3], *BONE_COLOR))
            if lines:
                self.bone_lines = np.array(lines, np.float32)

        self.clip = None
        self.vbuf = None
        self.ibuf = None

    # -- animation -----------------------------------------------------------
    def set_animation(self, clip) -> bool:
        """Bind a neox_anim.Clip; skinning is measured against this mesh's own bind pose."""
        mesh = self.mesh
        if clip is None or not mesh.bone_matrix or len(mesh.vertex_bone) != self.vertex_count:
            return False
        skeleton_index = {name: i for i, name in enumerate(clip.bone_names)}
        self.clip = clip
        self._skeleton_of_bone = np.array([skeleton_index.get(name, -1) for name in mesh.bone_name])
        binds = np.array([np.asarray(m, float).T for m in mesh.bone_matrix])     # row-vector -> column-vector
        self._inverse_binds = np.linalg.inv(binds)
        bones = len(mesh.bone_name)
        joints = np.array([list(b)[:4] + [bones] * (4 - len(b)) for b in mesh.vertex_bone], np.int64)
        weights = np.array([list(w)[:4] + [0.0] * (4 - len(w)) for w in mesh.vertex_weight], float)
        invalid = (joints < 0) | (joints >= bones)
        self._joints = np.where(invalid, 0, joints)
        self._weights = np.where(invalid, 0.0, weights)
        self._rest_positions = np.hstack([np.asarray(mesh.position, float).reshape(-1, 3), np.ones((self.vertex_count, 1))])
        self._rest_normals = self.vertices[:, 3:6].astype(float) * np.array([-1.0, 1.0, 1.0])
        return True

    def pose(self, time_seconds: float) -> np.ndarray:
        """Interleaved vertex data (viewer space) skinned to the clip at a time; also updates bone lines."""
        world = self.clip.world_matrices(time_seconds)
        skin = np.tile(np.eye(4), (len(self._inverse_binds), 1, 1))
        mapped = self._skeleton_of_bone >= 0
        skin[mapped] = world[self._skeleton_of_bone[mapped]] @ self._inverse_binds[mapped]
        matrices = skin[self._joints]                                            # (n, 4, 4, 4)
        positions = np.einsum("nk,nkij,nj->ni", self._weights, matrices, self._rest_positions)[:, :3]
        normals = np.einsum("nk,nkij,nj->ni", self._weights, matrices[:, :, :3, :3], self._rest_normals)
        normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-9)
        mirror = np.array([-1.0, 1.0, 1.0])
        data = self.vertices.copy()
        data[:, 0:3] = positions * mirror
        data[:, 3:6] = normals * mirror
        if len(self.bone_lines):
            joints = world[:, :3, 3] * mirror
            joints = np.hstack([joints, np.ones((len(joints), 1))]) @ self.matrix.T
            lines = []
            for bone, parent in enumerate(self.clip.parents[:len(joints)]):
                if parent >= 0:
                    lines.append((*joints[parent, :3], *BONE_COLOR))
                    lines.append((*joints[bone, :3], *BONE_COLOR))
            if lines:
                self.bone_lines = np.array(lines, np.float32)
        return data.astype(np.float32)

    def release(self):
        _destroy(self.vbuf, self.ibuf)
        self.vbuf = self.ibuf = None
        for part in self.parts:
            part.release()


class SceneRenderWidget(QtWidgets.QRhiWidget, CameraController):
    """Direct3D 11 renderer for SceneObjects with textures, transparency and rigs."""

    upload_progress = QtCore.Signal(int, int)

    def __init__(self, parent=None):
        QtWidgets.QRhiWidget.__init__(self, parent)
        CameraController.__init__(self)
        if sys.platform == "win32":
            # Custom shaders are HLSL source, compiled by Direct3D at run time
            self.setApi(QtWidgets.QRhiWidget.Api.Direct3D11)
        app = QtWidgets.QApplication.instance()
        settings = app.property("settings_manager") if app is not None else None
        try:
            self.setSampleCount(int(settings.get("graphics.msaa", 1)) if settings is not None else 1)
        except Exception:
            get_logger().warning("Could not set the MSAA sample count", exc_info=True)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(320, 240)
        self.objects: list[SceneObject] = []
        self.textures: dict[str, dict] = {}
        self.show_bones = True
        self.playing = True
        self._clock = QtCore.QElapsedTimer()
        self._clock.start()
        self._time_offset = 0.0
        self.error: str | None = None
        self._rhi = None
        self._ready = False
        self._bones_dirty = True
        self._bone_vbuf = None
        self._bone_vertices = 0

    # -- scene editing -------------------------------------------------------
    def add_object(self, obj: SceneObject):
        self.objects.append(obj)
        self._bones_dirty = True
        self.update()

    def remove_objects(self, objects):
        doomed = {obj.id for obj in objects}
        for obj in self.objects:
            if obj.id in doomed:
                obj.release()
        self.objects = [obj for obj in self.objects if obj.id not in doomed]
        self._collect_textures()
        self._bones_dirty = True
        self.update()

    def set_texture(self, key: str, qimage: QtGui.QImage | None, label: str):
        entry = self.textures.get(key)
        if entry is None or (qimage is not None and entry.get("tex") is None and entry.get("image") is None):
            self.textures[key] = {"image": qimage, "tex": None, "label": label, "size": qimage.size() if qimage else None}
        return key

    def _collect_textures(self):
        used = {part.texture_key for obj in self.objects for part in obj.parts}
        for key in [k for k in self.textures if k not in used]:
            _destroy(self.textures[key].get("tex"))
            del self.textures[key]

    def changed(self):
        self._bones_dirty = True
        for obj in self.objects:
            for part in obj.parts:
                part.dirty = True
        self.update()

    def scene_bounds(self, objects=None):
        objects = [o for o in (objects or self.objects) if o.visible]
        if not objects:
            return None
        if len(objects) > 4:
            # Sky domes and background cards dwarf a stage; frame the rest
            sizes = np.array([float(np.linalg.norm(o.bounds[1] - o.bounds[0])) for o in objects])
            limit = 4.0 * float(np.percentile(sizes, 75))
            kept = [o for o, size in zip(objects, sizes) if size <= limit]
            objects = kept or objects
        lows = np.array([o.bounds[0] for o in objects])
        highs = np.array([o.bounds[1] for o in objects])
        return lows.min(0), highs.max(0)

    def focus(self, objects=None):
        bounds = self.scene_bounds(objects)
        if bounds is None:
            self.camera.focus()
        else:
            low, high = bounds
            center = (low + high) / 2
            radius = max(float(np.linalg.norm(high - low)) / 2, 1.0)
            self.camera.focus(center.tolist())
            self.camera.dist = max(self.camera.min_dist, min(radius / math.sin(math.radians(self.camera.fov_y) / 2),
                                                             self.camera.max_dist))
            self.movement_factor = radius * 0.1
        self.update()

    # -- RHI -----------------------------------------------------------------
    def _release_gpu(self):
        for obj in self.objects:
            obj.release()
        for entry in self.textures.values():
            entry["tex"] = None
        self._bone_vbuf = None
        self._bones_dirty = True
        self._ready = False

    def initialize(self, cb):
        rhi = self.rhi()
        if rhi is None:
            return
        if self._rhi is not rhi:
            self._release_gpu()
            self._rhi = rhi
        if self._ready:
            self.camera.set_aspect_ratio(self.renderTarget().pixelSize().width(), self.renderTarget().pixelSize().height())
            return
        if rhi.backend() not in (QtGui.QRhi.Implementation.D3D11, QtGui.QRhi.Implementation.D3D12):
            self.error = f"The Scene Viewer needs Direct3D (current backend: {rhi.backendName()})."
            return

        Usage = QtGui.QRhiBuffer.UsageFlag
        Stage = QtGui.QRhiShaderResourceBinding.StageFlag
        self._camera_ubuf = rhi.newBuffer(QtGui.QRhiBuffer.Type.Dynamic, Usage.UniformBuffer, 80)
        self._camera_ubuf.create()
        self._template_ubuf = rhi.newBuffer(QtGui.QRhiBuffer.Type.Dynamic, Usage.UniformBuffer, 96)
        self._template_ubuf.create()
        self._sampler = rhi.newSampler(QtGui.QRhiSampler.Filter.Linear, QtGui.QRhiSampler.Filter.Linear,
                                       QtGui.QRhiSampler.Filter.Linear, QtGui.QRhiSampler.AddressMode.Repeat,
                                       QtGui.QRhiSampler.AddressMode.Repeat)
        self._sampler.create()
        self._white = rhi.newTexture(QtGui.QRhiTexture.Format.RGBA8, QtCore.QSize(1, 1))
        self._white.create()
        white = QtGui.QImage(1, 1, QtGui.QImage.Format.Format_RGBA8888)
        white.fill(QtGui.QColor(255, 255, 255))

        self._template_srb = self._mesh_srb(self._template_ubuf, self._white)
        self._lines_srb = rhi.newShaderResourceBindings()
        self._lines_srb.setBindings([QtGui.QRhiShaderResourceBinding.uniformBuffer(0, Stage.VertexStage, self._camera_ubuf)])
        self._lines_srb.create()

        samples = self.renderTarget().sampleCount()
        rpd = self.renderTarget().renderPassDescriptor()
        Fmt = QtGui.QRhiVertexInputAttribute.Format
        mesh_layout = QtGui.QRhiVertexInputLayout()
        mesh_layout.setBindings([QtGui.QRhiVertexInputBinding(32)])
        mesh_layout.setAttributes([QtGui.QRhiVertexInputAttribute(0, 0, Fmt.Float3, 0),
                                   QtGui.QRhiVertexInputAttribute(0, 1, Fmt.Float3, 12),
                                   QtGui.QRhiVertexInputAttribute(0, 2, Fmt.Float2, 24)])
        mesh_stages = [QtGui.QRhiShaderStage(QtGui.QRhiShaderStage.Type.Vertex,
                                             _hlsl_shader(QtGui.QShader.Stage.VertexStage, VERTEX_SHADER)),
                       QtGui.QRhiShaderStage(QtGui.QRhiShaderStage.Type.Fragment,
                                             _hlsl_shader(QtGui.QShader.Stage.FragmentStage, PIXEL_SHADER))]

        def mesh_pipeline(blend: bool):
            pipeline = rhi.newGraphicsPipeline()
            pipeline.setShaderStages(mesh_stages)
            pipeline.setVertexInputLayout(mesh_layout)
            pipeline.setShaderResourceBindings(self._template_srb)
            pipeline.setRenderPassDescriptor(rpd)
            pipeline.setSampleCount(samples)
            pipeline.setCullMode(QtGui.QRhiGraphicsPipeline.CullMode.None_)
            pipeline.setDepthTest(True)
            pipeline.setDepthWrite(not blend)
            if blend:
                target = QtGui.QRhiGraphicsPipeline.TargetBlend()
                target.enable = True
                target.srcColor = QtGui.QRhiGraphicsPipeline.BlendFactor.SrcAlpha
                target.dstColor = QtGui.QRhiGraphicsPipeline.BlendFactor.OneMinusSrcAlpha
                target.srcAlpha = QtGui.QRhiGraphicsPipeline.BlendFactor.One
                target.dstAlpha = QtGui.QRhiGraphicsPipeline.BlendFactor.OneMinusSrcAlpha
                pipeline.setTargetBlends([target])
            if not pipeline.create():
                raise RuntimeError("Could not create the scene pipeline")
            return pipeline

        colored = (_qsb_shader("colored_vertices.vert.qsb"), _qsb_shader("colored_vertices.frag.qsb"))
        line_layout = QtGui.QRhiVertexInputLayout()
        line_layout.setBindings([QtGui.QRhiVertexInputBinding(24)])
        line_layout.setAttributes([QtGui.QRhiVertexInputAttribute(0, 0, Fmt.Float3, 0),
                                   QtGui.QRhiVertexInputAttribute(0, 1, Fmt.Float3, 12)])

        def line_pipeline(depth: bool):
            pipeline = rhi.newGraphicsPipeline()
            pipeline.setShaderStages([QtGui.QRhiShaderStage(QtGui.QRhiShaderStage.Type.Vertex, colored[0]),
                                      QtGui.QRhiShaderStage(QtGui.QRhiShaderStage.Type.Fragment, colored[1])])
            pipeline.setVertexInputLayout(line_layout)
            pipeline.setShaderResourceBindings(self._lines_srb)
            pipeline.setRenderPassDescriptor(rpd)
            pipeline.setSampleCount(samples)
            pipeline.setTopology(QtGui.QRhiGraphicsPipeline.Topology.Lines)
            pipeline.setDepthTest(depth)
            pipeline.setDepthWrite(depth)
            if not pipeline.create():
                raise RuntimeError("Could not create the line pipeline")
            return pipeline

        try:
            self._opaque = mesh_pipeline(False)
            self._blended = mesh_pipeline(True)
            self._grid_pipeline = line_pipeline(True)
            self._bone_pipeline = line_pipeline(False)
        except Exception as exc:
            self.error = str(exc)
            return

        grid_lines = np.asarray(grid(360, 10), np.float32).reshape(-1, 3)
        grid_data = np.hstack([grid_lines, np.full((len(grid_lines), 3), 0.3, np.float32)]).astype(np.float32)
        self._grid_vertices = len(grid_data)
        self._grid_vbuf = rhi.newBuffer(QtGui.QRhiBuffer.Type.Immutable, Usage.VertexBuffer, grid_data.nbytes)
        self._grid_vbuf.create()
        updates = rhi.nextResourceUpdateBatch()
        updates.uploadStaticBuffer(self._grid_vbuf, _blob(grid_data))
        updates.uploadTexture(self._white, white)
        cb.resourceUpdate(updates)
        self.camera.set_aspect_ratio(self.renderTarget().pixelSize().width(), self.renderTarget().pixelSize().height())
        self._ready = True
        self.error = None

    def _mesh_srb(self, ubuf, texture):
        Stage = QtGui.QRhiShaderResourceBinding.StageFlag
        srb = self._rhi.newShaderResourceBindings()
        srb.setBindings([
            QtGui.QRhiShaderResourceBinding.uniformBuffer(0, Stage.VertexStage | Stage.FragmentStage, self._camera_ubuf),
            QtGui.QRhiShaderResourceBinding.sampledTexture(1, Stage.FragmentStage, texture, self._sampler),
            QtGui.QRhiShaderResourceBinding.uniformBuffer(2, Stage.VertexStage | Stage.FragmentStage, ubuf),
        ])
        srb.create()
        return srb

    def _texture_for(self, key, updates):
        entry = self.textures.get(key) if key else None
        if entry is None:
            return None
        if entry["tex"] is None and entry["image"] is not None:
            image = entry["image"]
            Flag = QtGui.QRhiTexture.Flag
            texture = self._rhi.newTexture(QtGui.QRhiTexture.Format.RGBA8, image.size(), 1,
                                           Flag.MipMapped | Flag.UsedWithGenerateMips)
            texture.create()
            updates.uploadTexture(texture, image)
            updates.generateMips(texture)
            entry["tex"] = texture
            entry["image"] = None          # the GPU copy is all the viewer needs now
        return entry["tex"]

    def _upload(self, updates) -> int:
        """Create GPU resources for new or changed objects within a time budget; returns pending count."""
        Usage = QtGui.QRhiBuffer.UsageFlag
        deadline = time.perf_counter() + UPLOAD_BUDGET_SECONDS
        pending = 0
        for obj in self.objects:
            if obj.vbuf is None:
                if time.perf_counter() > deadline:
                    pending += 1
                    continue
                kind = QtGui.QRhiBuffer.Type.Dynamic if obj.clip is not None else QtGui.QRhiBuffer.Type.Immutable
                obj.vbuf = self._rhi.newBuffer(kind, Usage.VertexBuffer, max(obj.vertices.nbytes, 32))
                obj.vbuf.create()
                obj.ibuf = self._rhi.newBuffer(QtGui.QRhiBuffer.Type.Immutable, Usage.IndexBuffer, max(obj.indices.nbytes, 4))
                obj.ibuf.create()
                if obj.vertices.nbytes and obj.clip is not None:
                    updates.updateDynamicBuffer(obj.vbuf, 0, obj.vertices.nbytes, _blob(obj.vertices))
                elif obj.vertices.nbytes:
                    updates.uploadStaticBuffer(obj.vbuf, _blob(obj.vertices))
                if obj.indices.nbytes:
                    updates.uploadStaticBuffer(obj.ibuf, _blob(obj.indices))
            for part in obj.parts:
                if not part.dirty:
                    continue
                if time.perf_counter() > deadline and part.ubuf is None:
                    pending += 1
                    continue
                texture = self._texture_for(part.texture_key, updates)
                if part.ubuf is None:
                    part.ubuf = self._rhi.newBuffer(QtGui.QRhiBuffer.Type.Dynamic, Usage.UniformBuffer, 96)
                    part.ubuf.create()
                if part.srb is None or part.srb_texture is not (texture or self._white):
                    _destroy(part.srb)
                    part.srb = self._mesh_srb(part.ubuf, texture or self._white)
                    part.srb_texture = texture or self._white
                cutoff = part.cutoff if part.alpha == "MASK" else (0.004 if part.alpha == "BLEND" else 0.0)
                params = [1.0 if texture is not None else 0.0, cutoff, HIGHLIGHT if part.highlight else 0.0, 0.0]
                color = [1.0, 1.0, 1.0, 1.0] if texture is not None else [0.72, 0.72, 0.72, 1.0]
                data = np.concatenate([obj.matrix.T.reshape(-1), color, params]).astype(np.float32)
                updates.updateDynamicBuffer(part.ubuf, 0, 96, _blob(data))
                part.dirty = False
        return pending

    def _upload_bones(self, updates):
        if not self._bones_dirty:
            return
        self._bones_dirty = False
        _destroy(self._bone_vbuf)
        self._bone_vbuf = None
        lines = [obj.bone_lines for obj in self.objects if obj.visible and len(obj.bone_lines)]
        if not lines:
            self._bone_vertices = 0
            return
        data = np.concatenate(lines).astype(np.float32)
        self._bone_vertices = len(data)
        self._bone_vbuf = self._rhi.newBuffer(QtGui.QRhiBuffer.Type.Immutable, QtGui.QRhiBuffer.UsageFlag.VertexBuffer, data.nbytes)
        self._bone_vbuf.create()
        updates.uploadStaticBuffer(self._bone_vbuf, _blob(data))

    def render(self, cb):
        background = QtGui.QColor.fromRgbF(0.23, 0.23, 0.23)
        if not self._ready or self._rhi is None:
            cb.beginPass(self.renderTarget(), background, QtGui.QRhiDepthStencilClearValue(1, 0))
            cb.endPass()
            return
        self._camera_update()
        rhi = self._rhi
        updates = rhi.nextResourceUpdateBatch()
        view_proj = rhi.clipSpaceCorrMatrix() * self.camera.view_proj()
        eye = self.camera.rot().inverted()[0].map(QtGui.QVector4D(0, 0, 1, 0))
        camera_data = np.concatenate([np.array(view_proj.data(), np.float32), [eye.x(), eye.y(), eye.z(), 0.0]])
        updates.updateDynamicBuffer(self._camera_ubuf, 0, 80, _blob(camera_data.astype(np.float32)))
        pending = self._upload(updates)
        animating = self._animate(updates)
        self._upload_bones(updates)

        size = self.renderTarget().pixelSize()
        cb.beginPass(self.renderTarget(), background, QtGui.QRhiDepthStencilClearValue(1, 0), updates)
        viewport = QtGui.QRhiViewport(0, 0, size.width(), size.height())
        cb.setGraphicsPipeline(self._grid_pipeline)
        cb.setViewport(viewport)
        cb.setShaderResources(self._lines_srb)
        cb.setVertexInput(0, [(self._grid_vbuf, 0)])
        cb.draw(self._grid_vertices)

        index_format = QtGui.QRhiCommandBuffer.IndexFormat.IndexUInt32
        blended = []
        cb.setGraphicsPipeline(self._opaque)
        cb.setViewport(viewport)
        for obj in self.objects:
            if not obj.visible or obj.vbuf is None:
                continue
            bound = False
            for part in obj.parts:
                if part.srb is None:
                    continue
                if part.alpha == "BLEND":
                    blended.append((obj, part))
                    continue
                if not bound:
                    cb.setVertexInput(0, [(obj.vbuf, 0)], obj.ibuf, 0, index_format)
                    bound = True
                cb.setShaderResources(part.srb)
                cb.drawIndexed(part.count, 1, part.first)
        if blended:
            eye_pos = self.camera.pos().toVector3D()
            eye_point = np.array([eye_pos.x(), eye_pos.y(), eye_pos.z()])
            blended.sort(key=lambda item: -float(np.linalg.norm((item[0].bounds[0] + item[0].bounds[1]) / 2 - eye_point)))
            cb.setGraphicsPipeline(self._blended)
            cb.setViewport(viewport)
            for obj, part in blended:
                cb.setVertexInput(0, [(obj.vbuf, 0)], obj.ibuf, 0, index_format)
                cb.setShaderResources(part.srb)
                cb.drawIndexed(part.count, 1, part.first)
        if self.show_bones and self._bone_vbuf is not None and self._bone_vertices:
            cb.setGraphicsPipeline(self._bone_pipeline)
            cb.setViewport(viewport)
            cb.setShaderResources(self._lines_srb)
            cb.setVertexInput(0, [(self._bone_vbuf, 0)])
            cb.draw(self._bone_vertices)
        cb.endPass()

        self.upload_progress.emit(len(self.objects) - pending, len(self.objects))
        if pending or animating or self._keyboard_pressed_keys:
            self.update()

    # -- animation -----------------------------------------------------------
    def animation_time(self) -> float:
        if self.playing:
            return self._time_offset + self._clock.elapsed() / 1000.0
        return self._time_offset

    def set_playing(self, playing: bool):
        if playing == self.playing:
            return
        self._time_offset = self.animation_time()
        self.playing = playing
        self._clock.restart()
        self.update()

    def _animate(self, updates) -> bool:
        """Re-skin visible animated objects for the current time; True while anything is playing."""
        animated = [obj for obj in self.objects if obj.clip is not None and obj.vbuf is not None and obj.visible]
        if not animated:
            return False
        now = self.animation_time()
        if not self.playing and getattr(self, "_posed_at", None) == now:
            return False
        self._posed_at = now
        for obj in animated:
            data = obj.pose(now)
            updates.updateDynamicBuffer(obj.vbuf, 0, data.nbytes, _blob(data))
        if self.show_bones:
            self._bones_dirty = True
        return self.playing

    # -- input ---------------------------------------------------------------
    def mousePressEvent(self, event):
        self._camera_mouse_pressed_event(event)
        self.update()

    def mouseReleaseEvent(self, event):
        self._camera_mouse_released_event(event)
        self.update()

    def mouseMoveEvent(self, event):
        self._camera_mouse_moved_event(event)
        self.update()

    def wheelEvent(self, event):
        self._camera_wheel_event(event)
        self.update()

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key.Key_F:
            self.focus()
        self._camera_key_pressed_event(event)
        self.update()

    def keyReleaseEvent(self, event):
        self._camera_key_released_event(event)
        self.update()
