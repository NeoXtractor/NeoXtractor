"""
PMX Format Converter

Modified from NeoXtractor (AGPL-3.0) in 2026: see NOTICE.md.
"""

import io
from typing import cast

from pymeshio import pmx, common
import pymeshio.pmx.writer

from core.logger import get_logger
from core.mesh_converter import bone_names, mmd_rig
from core.mesh_loader import MeshData

NAME = "Polygon Model eXtended (PMX) Format"
EXTENSION = ".pmx"

# Material look in MMD / PMX Editor: no specular highlight, full ambient, lit like the texture
MATERIAL_SPECULAR_POWER = 50
MATERIAL_FLAGS = 0x01 | 0x02  # double-sided drawing, ground shadow


def _settings_manager():
    """Return the app's SettingsManager, or None when running without the GUI."""
    try:
        from PySide6 import QtWidgets

        app = QtWidgets.QApplication.instance()
        return app.property("settings_manager") if app is not None else None
    except Exception:
        return None


def to_pmx_space(x, y, z):
    """
    NeoX characters face +Z with their left side on -X; MMD models face -Z
    with their left side on +X. Both are left-handed and Y-up, so a 180 degree
    turn around Y converts between them without mirroring the mesh.
    """
    return -float(x), float(y), -float(z)


def convert(
    mesh: MeshData, flip_normals: bool = False, materials: list[dict] | None = None, translate_bones: bool | None = None
) -> bytes:
    """
    Convert mesh to PMX format.

    Parameters:
    - mesh: MeshData object containing bones, vertices, faces, etc.
    - flip_normals: reverse winding and negate normals (off by default).
    - materials: optional {'name', 'texture'} per sub-mesh; texture is a path
      relative to the .pmx. These materials are drawn double-sided.
    - translate_bones: name bones with MMD names (bone_names table), keeping the HPMA
      name as the English name; None reads the Tools menu setting (on by default).

    Returns:
    - bytes: PMX file content as bytes
    """
    if translate_bones is None:
        translate_bones = bone_names.enabled(_settings_manager())

    pmx_model = pmx.Model()
    pmx_model.display_slots.append(pmx.DisplaySlot('表情', 'Exp', 1, None))
    pmx_model.english_name = 'Empty model'
    pmx_model.comment = 'NeoX Model Converterで生成'
    pmx_model.english_comment = 'Created by NeoX Model Converter.'

    # Five fingers per hand: the game rigs thumb, index and middle; ring and little ride on the middle finger
    if mesh.has_bones:
        # helper bones doubling a limb (bone_l_upperarm's shoulder pad) follow that limb
        mesh = mmd_rig.merge_twin_helpers(mesh)
        from core.mesh_converter import fingers

        if fingers.enabled(_settings_manager()):
            mesh = fingers.five_fingers(mesh)

    if mesh.has_bones:
        names = bone_names.translate(list(mesh.bone_name)) if translate_bones else list(mesh.bone_name)
        if translate_bones:  # names VMD can address (15 bytes), the same in every export
            from core.mesh_converter.vmd_export import short_bone_names

            names = short_bone_names(names)
        source_names = list(getattr(mesh, "bone_source_names", None) or mesh.bone_name)
        parent_child_dict = {}
        old2new = {}
        index_pool = [-1]
        bone_pool: list[pmx.Bone] = []

        for i, p in enumerate(mesh.bone_parent):
            if p not in parent_child_dict:
                parent_child_dict[p] = []
            parent_child_dict[p].append(i)

        def build_joint(index, parent_index):
            matrix = mesh.bone_matrix[index]
            # NeoX stores row-vector matrices: translation lives in the last row
            x, y, z = to_pmx_space(matrix[3, 0], matrix[3, 1], matrix[3, 2])
            bone_pool.append(
                pmx.Bone(
                    name=names[index],
                    english_name=source_names[index] if index < len(source_names) else names[index],
                    position=common.Vector3(x, y, z),
                    parent_index=parent_index,
                    layer=0,
                    flag=0,
                )
            )
            bone_pool[-1].setFlag(pmx.BONEFLAG_CAN_ROTATE, True)
            bone_pool[-1].setFlag(pmx.BONEFLAG_IS_VISIBLE, True)
            bone_pool[-1].setFlag(pmx.BONEFLAG_CAN_MANIPULATE, True)
            bone_pool[-1].setFlag(pmx.BONEFLAG_CAN_TRANSLATE, True)  # so VMD position keys apply

        def deep_first_search(index, index_pool, parent_index):
            index_pool[0] += 1
            current_node_index = index_pool[0]
            old2new[index] = current_node_index
            build_joint(index, parent_index)
            if index in parent_child_dict:
                for child in parent_child_dict[index]:
                    deep_first_search(child, index_pool, current_node_index)

        try:
            root_index = mesh.bone_parent.index(-1)
            deep_first_search(root_index, index_pool, -1)
        except ValueError:
            # no root bone: add one
            for i in range(len(mesh.bone_name)):
                old2new[i] = i
                build_joint(i, mesh.bone_parent[i] if mesh.bone_parent[i] != -1 else -1)

        translated = set(bone_names.table().values())
        if translate_bones and mmd_rig.enabled(_settings_manager()):
            # MMD chains, IK legs and display frames for the translated bones; the rest keep their parents
            bone_pool, remap, slots = mmd_rig.build(bone_pool, [n for n in names if n in translated])
            old2new = {old: remap[new] for old, new in old2new.items()}
            if slots:
                pmx_model.display_slots = slots
        # The MMD bones' tails point at the next bone in their chain (or onward past the end): translated ones and
        # those the rig adds (センター, IK...). Bones left with their HPMA names keep a zero-length tail.
        mmd_bones = {b.name for b in bone_pool if b.name in translated or b.name not in names}
        mmd_rig.link_tails(bone_pool, mmd_bones)
        pmx_model.bones = bone_pool
    else:
        # Create a default root bone
        root_bone = pmx.Bone(
            name="root", english_name="root", position=common.Vector3(0, 0, 0), parent_index=-1, layer=0, flag=0
        )
        root_bone.setFlag(pmx.BONEFLAG_CAN_ROTATE, True)
        root_bone.setFlag(pmx.BONEFLAG_IS_VISIBLE, True)
        root_bone.setFlag(pmx.BONEFLAG_CAN_MANIPULATE, True)
        root_bone.setFlag(pmx.BONEFLAG_CAN_TRANSLATE, True)
        pmx_model.bones = [root_bone]
        old2new = {0: 0}

    normal_sign = -1.0 if flip_normals else 1.0
    for i, position in enumerate(mesh.position):
        x, y, z = to_pmx_space(*position)
        nx, ny, nz = mesh.normal[i] if mesh.has_normals else (0.0, 0.0, 1.0)
        nx, ny, nz = (normal_sign * n for n in to_pmx_space(nx, ny, nz))
        u, v = mesh.uv[i] if mesh.has_uvs else (0.0, 0.0)

        if mesh.has_bones and i < len(mesh.vertex_bone):
            vertex_joint_index = []
            for joint_idx in mesh.vertex_bone[i]:
                if joint_idx in old2new:
                    vertex_joint_index.append(old2new[joint_idx])
                else:
                    vertex_joint_index.append(0)

            # PMX BDEF4: pad to 4 bones and weights
            while len(vertex_joint_index) < 4:
                vertex_joint_index.append(0)
            vertex_joint_index = vertex_joint_index[:4]

            vertex_weights = list(mesh.vertex_weight[i]) if i < len(mesh.vertex_weight) else [1.0, 0.0, 0.0, 0.0]
            while len(vertex_weights) < 4:
                vertex_weights.append(0.0)
            vertex_weights = vertex_weights[:4]

            vertex = pmx.Vertex(
                common.Vector3(cast(int, x), cast(int, y), cast(int, z)),
                common.Vector3(cast(int, nx), cast(int, ny), cast(int, nz)),
                common.Vector2(cast(int, u), cast(int, v)),
                pmx.Bdef4(*vertex_joint_index, *vertex_weights),
                0.0,
            )
        else:
            # No bone data - assign to root bone
            vertex = pmx.Vertex(
                common.Vector3(cast(int, x), cast(int, y), cast(int, z)),
                common.Vector3(cast(int, nx), cast(int, ny), cast(int, nz)),
                common.Vector2(cast(int, u), cast(int, v)),
                pmx.Bdef1(0),
                0.0,
            )
        pmx_model.vertices.append(vertex)

    # Add faces; flipping reverses the winding so faces turn with their normals
    for face in mesh.face:
        a, b, c = face
        pmx_model.indices.extend((a, c, b) if flip_normals else (a, b, c))

    if mesh.mesh:
        textures: list[str] = []
        pmx_model.materials = []  # drop pymeshio's empty default material
        for i, (_mesh_vertex_count, mesh_face_count, _, _) in enumerate(mesh.mesh):
            info = materials[i] if materials and i < len(materials) else None
            texture_index = -1
            if info and info.get('texture'):
                if info['texture'] not in textures:
                    textures.append(info['texture'])
                texture_index = textures.index(info['texture'])
            material = pmx.Material(
                name=info['name'] if info else f'Mat{i}',
                english_name=info.get('english_name', info['name']) if info else f'material{i}',
                diffuse_color=common.RGB(1, 1, 1),
                alpha=1.0,
                specular_factor=MATERIAL_SPECULAR_POWER,
                specular_color=common.RGB(0, 0, 0),
                ambient_color=common.RGB(1, 1, 1),
                flag=MATERIAL_FLAGS,
                edge_color=common.RGBA(0, 0, 0, 1),
                edge_size=0,
                texture_index=texture_index,
                sphere_texture_index=-1,
                sphere_mode=pmx.MATERIALSPHERE_NONE,
                toon_sharing_flag=1,
                toon_texture_index=0,
                comment="",
                vertex_count=mesh_face_count * 3,
            )
            pmx_model.materials.append(material)
        if textures:
            pmx_model.textures = textures
    else:
        # Default single material
        material = pmx.Material(
            name='Material',
            english_name='Material',
            diffuse_color=common.RGB(1, 1, 1),
            alpha=1.0,
            specular_factor=MATERIAL_SPECULAR_POWER,
            specular_color=common.RGB(0, 0, 0),
            ambient_color=common.RGB(1, 1, 1),
            flag=MATERIAL_FLAGS,
            edge_color=common.RGBA(0, 0, 0, 1),
            edge_size=0,
            texture_index=-1,
            sphere_texture_index=-1,
            sphere_mode=pmx.MATERIALSPHERE_NONE,
            toon_sharing_flag=1,
            toon_texture_index=0,
            comment="",
            vertex_count=len(mesh.face) * 3,
        )
        pmx_model.materials.append(material)

    # 肩 / 腕 pivots in the middle of the shoulder joint, so raised arms bend cleanly
    if mesh.has_bones and mmd_rig.shoulders_enabled(_settings_manager()):
        try:
            mmd_rig.raise_shoulders(pmx_model)
        except Exception:
            get_logger().exception("Could not raise the shoulder bones")

    # Leave out bones that don't move anything in this export, so each model only gets the bones it uses.
    if mesh.has_bones:
        try:
            mmd_rig.remove_unused(pmx_model)
        except Exception:
            get_logger().exception("Could not remove the unused bones")

    # Collision shapes on the body for MMD physics (hair, skirts, capes), sized to this model
    if mesh.has_bones:
        from core.mesh_converter import rigid_bodies

        if rigid_bodies.enabled(_settings_manager()):
            try:
                rigid_bodies.add_body_rigid_bodies(pmx_model)
            except Exception:
                get_logger().exception("Could not add the body rigid bodies")

    buffer = io.BytesIO()
    pymeshio.pmx.writer.write(buffer, pmx_model)
    return buffer.getvalue()


TRANSLATE_LABEL = "Translate bones to MMD names"
RIG_LABEL = "MMD bone setup (IK legs, センター, display frames)"
TRANSLATE_TIP = (
    "Rename HPMA bones (bip001_l_thigh, lower, …) to MMD names (左足D, 下半身, …); the HPMA name is kept as "
    "the English bone name. Add or change names with pmx_bone_names.xlsx/.csv/.txt next to NeoXtractor."
)
RIG_TIP = (
    "With translated bone names: re-arrange the bones into MMD chains and add 全ての親 / センター / グルーブ, IK legs "
    "(足ＩＫ, つま先ＩＫ) driving the D bones, and display frames. VMD exports then key the IK legs."
)


def _bind_options(translate, rig):
    """Check state from the settings, saved back on change; the rig needs translated names."""
    settings = _settings_manager()
    translate.setChecked(bone_names.enabled(settings))
    rig.setChecked(mmd_rig.enabled(settings))
    translate.setToolTip(TRANSLATE_TIP)
    rig.setToolTip(RIG_TIP)
    rig.setEnabled(translate.isChecked())

    def translate_toggled(checked):
        if settings is not None:
            settings.set(bone_names.SETTING, bool(checked), True)
        rig.setEnabled(checked)

    translate.toggled.connect(translate_toggled)
    if settings is not None:
        rig.toggled.connect(lambda checked: settings.set(mmd_rig.SETTING, bool(checked), True))


SHOULDER_TIP = (
    "With translated bone names: move 肩 and 腕 up to where MMD rigs usually have them (the game's "
    "pivots sit lower), so raising an arm doesn't fold the shoulder or drag the sleeve. Only the bones move."
)


def _bind_shoulders(widget, translate):
    """Needs translated names, like the rig."""
    settings = _settings_manager()
    widget.setChecked(mmd_rig.shoulders_enabled(settings))
    widget.setToolTip(SHOULDER_TIP)
    widget.setEnabled(translate.isChecked())
    translate.toggled.connect(widget.setEnabled)
    if settings is not None:
        widget.toggled.connect(lambda checked: settings.set(mmd_rig.SHOULDER_SETTING, bool(checked), True))


FINGERS_TIP = (
    "The game rigs thumb, index and middle finger; the ring and little fingers move with the middle one. "
    "This gives them bones of their own (薬指, 小指) and splits each finger's end into two joints "
    "(人指３, 中指３…), so MMD finger motions work. Game animations move them with the middle finger."
)


def _bind_fingers(widget):
    from core.mesh_converter import fingers

    settings = _settings_manager()
    widget.setChecked(fingers.enabled(settings))
    widget.setToolTip(FINGERS_TIP)
    if settings is not None:
        widget.toggled.connect(lambda checked: settings.set(fingers.SETTING, bool(checked), True))


RIGID_TIP = (
    "Bone-following collision capsules on the body (torso, head, arms, hands, legs, feet), sized to the "
    "model, so MMD physics on hair, skirts and capes doesn't pass through it."
)


def _bind_rigid(widget):
    from core.mesh_converter import rigid_bodies

    settings = _settings_manager()
    widget.setChecked(rigid_bodies.enabled(settings))
    widget.setToolTip(RIGID_TIP)
    if settings is not None:
        widget.toggled.connect(lambda checked: settings.set(rigid_bodies.SETTING, bool(checked), True))


def pmx_option_actions(parent):
    """The PMX export switches as checkable actions (for export menus)."""
    from PySide6 import QtGui

    translate = QtGui.QAction(TRANSLATE_LABEL, parent)
    rig = QtGui.QAction(RIG_LABEL, parent)
    translate.setCheckable(True)
    rig.setCheckable(True)
    _bind_options(translate, rig)
    from core.mesh_converter import fingers

    five = QtGui.QAction(fingers.LABEL, parent)
    five.setCheckable(True)
    _bind_fingers(five)
    from core.mesh_converter import rigid_bodies

    bodies = QtGui.QAction(rigid_bodies.LABEL, parent)
    bodies.setCheckable(True)
    _bind_rigid(bodies)
    shoulders = QtGui.QAction(mmd_rig.SHOULDER_LABEL, parent)
    shoulders.setCheckable(True)
    _bind_shoulders(shoulders, translate)
    return translate, rig, shoulders, five, bodies


def _install_tools_menu_hook() -> None:
    """Attach the menu toggle right before the Qt event loop starts."""
    try:
        from PySide6 import QtWidgets
    except Exception:
        return

    original_exec = QtWidgets.QApplication.exec
    if getattr(original_exec, "_pmx_menu_hook", False):
        return

    def exec_with_pmx_menu(*args, **kwargs):
        # QApplication.exec is static; app.exec() still passes the instance here
        if args and isinstance(args[0], QtWidgets.QApplication):
            args = args[1:]
        app = QtWidgets.QApplication.instance()
        try:
            # The Stages category lives in its own module; this hook is just where it gets attached
            from gui.widgets import stages_page

            stages_page.install(app)
        except Exception:
            get_logger().exception("Could not add the Stages category")
        try:
            from gui.widgets import scene_viewer

            scene_viewer.install(app)
        except Exception:
            get_logger().exception("Could not add the Scene Viewer")
        try:
            # Opening any pack also combines its copies from the other install (Documents patch layer)
            from core.npk import layered_archives

            layered_archives.install()
        except Exception:
            get_logger().exception("Could not combine the game's installs")
        try:
            from gui.widgets import characters_sort

            characters_sort.install(app)
        except Exception:
            get_logger().exception("Could not add sorting to the Characters tab")
        try:
            from gui.widgets import characters_items

            characters_items.install(app)
        except Exception:
            get_logger().exception("Could not add item names to the Characters tab")
        try:
            from core.npk import wwise_audio

            wwise_audio.install_category(app)
        except Exception:
            get_logger().exception("Could not add the Audio category")
        try:
            # Numbered .wem sounds show (and are found by) the sound bank that uses them
            from core.npk import wwise_names

            wwise_names.install(app)
        except Exception:
            get_logger().exception("Could not add the sound bank labels")
        QtWidgets.QApplication.exec = original_exec
        return original_exec(*args, **kwargs)

    exec_with_pmx_menu._pmx_menu_hook = True
    QtWidgets.QApplication.exec = exec_with_pmx_menu


_install_tools_menu_hook()
try:
    # get_logger() without inspect.stack(): makes reading packs noticeably faster everywhere
    from core import fast_logger as _fast_logger

    _fast_logger.install()
except Exception:
    get_logger().exception("Could not install the fast get_logger")
try:
    # Binary XML to text without the minidom round trip (same text): speeds up metadata scans and .gim reads
    from core import fast_bxml as _fast_bxml

    _fast_bxml.install()
except Exception:
    get_logger().exception("Could not install the fast binary XML decoding")
try:
    # Path normalising and hashing with the same results in less time: speeds up the Characters tab scan
    from core import fast_paths as _fast_paths

    _fast_paths.install()
except Exception:
    get_logger().exception("Could not install the fast path helpers")
try:
    # The Characters scan remembers named metadata entries between scans of unchanged packs
    from core.npk import metadata_cache as _metadata_cache

    _metadata_cache.install()
except Exception:
    get_logger().exception("Could not install the metadata cache")
try:
    # Before any pack is read: .wem / .ogg / .wav files join the sound banks in the Audio category
    from core.npk import wwise_audio as _wwise_audio

    _wwise_audio.install_detection()
except Exception:
    get_logger().exception("Could not hook the Wwise sound detection")
