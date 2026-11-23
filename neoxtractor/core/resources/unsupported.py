"""Probe-only format module for payloads without a decoder.

Owns the content-signature table for NeoX formats that have no dedicated
decoder.  Implements the resource protocol's identification half only:

- ``probe(f)`` -> :class:`ProbeResult` with a guessed extension and category
  for every recognised signature; unknown payloads get a weak ``dat``
  (binary) or ``txt`` (text) fallback so the UI always has an extension and
  a category for viewer dispatch and export naming.
- no ``read`` / ``convert``: the facade skips probe-only modules, so auto
  ``read`` returns ``None``: "recognised but not decodable".

Scores are capped **strictly below every decoder's floor** (lowest decoder
score: mesh layout-off 40) so ``unsupported`` is always the last resort and
never ties against a decoder: recognised binary magics 30, text markers 20,
unknown fallback 5.

Marker rules scan a bounded head window, not the whole payload.
"""

from __future__ import annotations

from neoxtractor.core.utils.content import is_binary
from neoxtractor.core.utils.filelike import read_head
from neoxtractor.core.utils.result import Category, ProbeResult

FORMAT = "unsupported"

# Window for interior/text marker scanning (probe stays header-cheap).
_SNIFF_WINDOW = 64 * 1024

# Undecoded image extensions; the ext -> category mapping below mirrors
# the UI grouping buckets.
_TEXTURE_EXTS = frozenset({"pkm"})
# Plain-text NeoX XML descriptors.
_XML_TEXT_EXTS = frozenset({"mtl", "mtg", "gim"})
# Audio payload magics this table can identify but has no decoder for; they
# group with the decoded WEM under the Audio category.
_AUDIO_EXTS = frozenset({"fev", "fsb", "ogg"})


def _category_for(ext: str) -> str:
    if ext in _TEXTURE_EXTS:
        return Category.TEXTURE
    if ext in _XML_TEXT_EXTS:
        return Category.BXML
    if ext == "bnk":
        return Category.BANK
    if ext in _AUDIO_EXTS:
        return Category.AUDIO
    if ext == "csb":
        return Category.CSB
    return Category.OTHER


def _starts(b: bytes, off: int, sig: bytes) -> bool:
    return b[off:off + len(sig)] == sig


def _binary_ext(head: bytes) -> str:
    """Match binary magic signatures for undecoded formats; "" if none."""
    d = head
    if (
        _starts(d, 0, b"\xe3\x00\x00\x00")
        or _starts(d, 0, b"\x63\x00\x00\x00")
        or _starts(d, 0, b"\x4c\x0f\x00\x00")
        or _starts(d, 0, b"\x27\xe3\x00\x01")
    ):
        return "pyc"
    if _starts(d, 0, b"RIFF"):
        if b"FEV" in d:
            return "fev"
    if d[0x34:0x3B] == b"2.1.0.0" or _starts(
        d, 0, bytes([0x38, 0x00, 0x00, 0x00, 0x34, 0x00, 0x64, 0x00])
    ):
        return "csb"
    if _starts(d, 4, b"ftyp"):
        return "mp4"
    if _starts(d, 0, bytes([0x01, 0x00, 0x05, 0x00, 0x00, 0x00])):
        return "foliage"
    if _starts(d, 0, b"NEOXMESH"):
        return "uimesh"
    if _starts(d, 0, b"NFXO"):
        return "nfx"
    if _starts(d, 0, b"RAWANIMA"):
        return "cpdanimation"
    if _starts(d, 0, b"NEOXBIN1"):
        return "uiprefab"
    if _starts(d, 0, b"SKELETON"):
        return "skeleton"
    if _starts(d, 0, b"blastmesh"):
        return "blastmesh"
    if _starts(d, 0, b"NVidia(r) GameWorks Blast(tm) v.1"):
        return "blast"
    if _starts(d, 0, b"CocosStudio-UI"):
        return "coc"
    if _starts(d, 0, b"hit"):
        return "hit"
    if _starts(d, 0, b"PKM"):
        return "pkm"
    if _starts(d, 0, b"clothasset"):
        return "clothasset"
    if _starts(d, 0, b"FSB5"):
        return "fsb"
    if _starts(d, 0, b"VANT"):
        return "vant"
    if _starts(d, 0, b"MDMP"):
        return "mdmp"
    if _starts(d, 0, b"RGIS"):
        return "gis"
    if _starts(d, 0, b"NTRK"):
        return "trk"
    if _starts(d, 0, b"OggS"):
        return "ogg"
    if _starts(d, 0, b"BKHD"):
        return "bnk"
    if _starts(d, 0, b"#HLB"):
        return "hlb"
    if _starts(d, 0, b"NXBI"):
        return "npse"
    if _starts(d, 0, b"NSXC"):
        return "nsxc"
    if _starts(d, 0, b"TZif"):
        return "tzif"
    if d[0x3B:0x3F] == bytes([0xC5, 0x00, 0x00, 0x80, 0x3F]):
        return "slpb"
    if b"\x00\x00\x00\x00\x00\x55\x55" in d:
        return "animation"
    return ""


def _text_ext(d: bytes) -> str:
    """Text-content signatures (evaluated in order)."""
    if d[:18] == b"from typing import ":
        return "pyi"
    if d[:27] == b"-----BEING PUBLIC KEY-----":
        return "pem"
    if b"<Material" in d:
        return "mtl"
    if b'"ctype": "' in d:
        return "cjson"
    if b"<MaterialGroup" in d:
        return "mtg"
    if b"<MetaInfo" in d:
        return "pvr.meta"
    if b"SHEX" in d and b"OSGN" in d:
        return "binary"
    if b"<Section" in d:
        return "sec"
    if b"<SubMesh" in d:
        return "gim"
    if b"<FxGroup" in d:
        return "sfx"
    if b'"AssetType" : "Animation"' in d:
        return "animation"
    if b"<Track" in d:
        return "trackgroup"
    if b"<Instances" in d:
        return "decal"
    if b"<Physics" in d:
        return "col"
    if b"<LODPolicy" in d or b"<LODProfile" in d:
        return "lod"
    if b'Type="Animation"' in d:
        return "animation"
    if b"DisableBakeLightProbe=" in d:
        return "prefab"
    if b"<Scene" in d:
        return "scn"
    if b'"ParticleSystemTemplate"' in d:
        return "pse"
    if b"<MainBody" in d:
        return "nxcompute"
    if b"<MapSkeletonToMeshBone" in d:
        return "skeletonextra"
    if b"<ShadingModel" in d:
        return "nxshader"
    if b"<BlastDynamic" in d:
        return "blt"
    if b'"ParticleAudio"' in d:
        return "psemusic"
    if b"<BlendSpace" in d:
        return "blendspace1d" if b'is2D="false"' in d else "blendspace"
    if b"<AnimationConfig" in d:
        return "animconfig"
    if b"<AnimationGraph" in d:
        return "animgraph"
    if b'<Head Type="Timeline"' in d:
        return "timeline"
    if b"<Chain" in d:
        return "physicalbone"
    if b"<PostProcess" in d:
        return "postprocess"
    if b'"mesh_import_options":{' in d:
        return "nxmeta"
    if b"<SceneConfig" in d:
        return "scnex"
    if b"<LocalPoints" in d:
        return "localweather"
    if b'GeoBatchHint="0"' in d:
        return "gimext"
    if b'"AssetType":"HapticsData"' in d:
        return "haptic"
    if b"<LocalFogParams" in d:
        return "localfogparams"
    if b"<Audios" in d or b"<AudioSource" in d:
        return "prefabaudio"
    if b'"ReferenceSkeleton' in d:
        return "featureschema"
    if b"<Relationships" in d:
        return "xml.rels"
    if b"<Waterfall" in d:
        return "waterfall"
    if b'"ReferenceSkeletonPath"' in d:
        return "mirrortable"
    if b"<ClothAsset" in d:
        return "clt"
    if b"<plist" in d:
        return "plist"
    if (
        b"<ShaderCompositor" in d
        or b"<ShaderFeature" in d
        or b"<ShaderIndexes" in d
        or b"<RenderTrigger" in d
    ):
        return "render"
    if b"<SkeletonRig" in d:
        return "skeletonrig"
    if b"format: " in d and b"filter: " in d:
        return "atlas"
    if b"<ShaderCache" in d:
        return "cache"
    if b"char" in d and b"width=" in d and b"height=" in d:
        return "fnt"
    if b"<AllCaches" in d:
        return "info"
    if b"<AllPreloadCaches" in d:
        return "list"
    if b"<Remove_Files" in d:
        return "map"
    if b'<HLSL File="' in d:
        return "md5"
    if b"<EnvParticle" in d:
        return "envp"
    if b"<TextureGroup" in d:
        return "txg"
    if b"?xml" in d:
        return "xml"
    if b'"AssetType" : "Skeleton"' in d:
        return "skeleton"
    if b'"Type" : "NewSpringAnimData"' in d:
        return "stb"
    return ""


def probe(f) -> ProbeResult:
    """Guess an extension/category for *f* without decoding it.

    Tier scores: binary magic 30, text marker 20, unknown ``dat``/``txt``
    fallback 5; rationale in the module docstring.
    """
    head = read_head(f, _SNIFF_WINDOW)
    if not head:
        return ProbeResult(5, "empty", Category.OTHER)

    ext = _binary_ext(head)
    if ext:
        return ProbeResult(30, ext, _category_for(ext))

    if not is_binary(head):
        text_ext = _text_ext(head)
        if text_ext:
            return ProbeResult(20, text_ext, _category_for(text_ext))
        return ProbeResult(5, "txt", Category.OTHER)

    return ProbeResult(5, "dat", Category.OTHER)
