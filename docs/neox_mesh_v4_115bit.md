# NeoX mesh version 4, 115-bit-per-vertex layout (EggParty / 蛋仔派对)

This document describes a `.mesh` variant found in Netease EggParty NPK archives
(`ext_packer_pet_high_*.npk`, `ext_packer_pet_low_*.npk`,
`ext_packer_game_plays_*.npk`).

`identify_mesh_type()` currently returns `-1` for these files, so `MeshParser0`
raises `NotImplementedError`. This document + `core/mesh_loader/parsers/eggparty.py`
implement what has been verified so far, and list precisely what is still unknown
so it can be finished without redoing the work.

Everything below marked **[verified]** was checked either byte-for-byte against the
section sizes of the files themselves, or against an independently produced
reference export of the same mesh.

---

## 1. Container

`NXPK` archives, see `core/wpk/`. EggParty uses:

| field | offset | value |
|---|---|---|
| magic | 0 | `NXPK` |
| file_count | 4 | u32 |
| hash_mode | 8 | u32 |
| **encrypt_mode** | 12 | u32 = **3** (AES-128 style block decryption, *not* RC4) |
| checksum | 20 | u32 |
| data | 24 | entry blocks |

`index_offset = file_size - file_count * 28`.

The index entries are `(name_hash u32, offset u32, size u32, ...)`; decrypting an
entry needs `encrypt_mode == 3` handled as 16-byte AES blocks with the game's
key schedule.

## 2. Mesh file header **[verified]**

```
u32  magic                = 0xBBC88034
u16  version              = 4
u16  always_0x0500
u16  bone_exist           = 1 for the files seen so far
u16  always_0x0000
if bone_exist in (1, 4):
    u16  bone_count
    bone_count x u16  parents          (u16 for this variant; see note below)
    bone_count x 32B  names            (NUL padded, spaces become '_')
    u8   bone_extra_info
    if bone_extra_info: bone_count x 28B extra
    bone_count x 16 x f32  matrix      (row-major, translation in the LAST ROW)
u8   flag1                          (must be 0)
u32  end_offset                     (absolute offset of the end of the geometry data)
u16  meshes_inside                  (== 1 for every file sampled)
meshes_inside x u32 multiple_offsets (the first is the submesh table offset)
```

Note on `parents`: `bones_is_16()` is currently used to decide u8/u16. On these
files the parents *are* u16. This needs re-checking together with the off-by-one
described in §7.

At `multiple_offsets[0]` the submesh table starts:

```
repeat:
    u32 vertex_count
    u32 face_count
    u8  uv_layers
    u8  unknown
    u16 must_be_one
until must_be_one == 1
u32 total_vertex_count
u32 total_face_count
```

The loop-and-rewind already implemented in `_parse_mesh_testing()` handles this
correctly: descriptors that do not end in `must_be_one == 1` are 10 bytes long,
the final one is 12 bytes and is immediately followed by the total counts.

## 3. Sections **[verified]**

Let `vc` = total vertex count and `fc` = total face count. Then

```
geometry_size = (115 * vc + 16) // 8
```

and the sections follow with **no padding**:

| section | size |
|---|---|
| geometry | `(115*vc + 16)//8` |
| faces | `6 * fc` |
| uv | `4 * sum(uv_layers_s * vc_s)` |
| colors (optional) | `4 * sum(color_len_s * vc_s)` |
| tail (optional) | `12 * vc + 32` |

This identity was checked to be **exact** on 1651 meshes across the three NPKs.
The optional sections are the reason `identify_mesh_type()` cannot see them: it
only ever subtracts `20 * vertex_count` (bones/weights) plus `uv_total_data * 8`,
which does not match `115/8 * vc`.

Observations on the tail:

* present on 96.3 % of `pet_high` meshes, 22 % of `pet_low`, 61 % of `game_plays`
* when present it is exactly `12*vc + 32` bytes: `4 x u8` joint indices
  (255 = unused, ~15 % of slots) + `4 x u16` weights (65535 = 1.0).
* **the tail is not necessarily a child of the UV section** — some `pet_low`
  meshes carry an extra 4-byte-per-vertex section between UV and the tail, which
  shifts the tail and corrupts joints/weights if it is not accounted for. This
  shows up as `end - (data_start + geo + 6fc + uv + 12vc + 32) == uv`.

## 4. Geometry block

`115` bits per vertex packed as 64-bit records plus a shared header:

```
u32 vertex_count      (redundant with the header)
u64  shared[?]        ...
```

The block is `(115*vc + 16)//8` bytes. The last `16` bits are the shared header,
which is why the geometric part is `115*vc/8` rounded up. The 64-bit records and
the shared header are interleaved as `u32`-indexed pairs in the real data; the
parser reads the records from `data_start`.

### 4.1 Per-coordinate bit layout

Every coordinate is stored as **two** bit fields separated by one bit that is
always zero, and the third coordinate's high block **wraps around** to the
beginning of the same 64-bit record:

```
 field   main bits   separator   high bits              value width
 x        8 .. 18        19       20 .. 26                14
 y       27 .. 38        39       40 .. 46                16
 z       48 .. 58        59       60 .. 63  +  0 .. 2      15
```

The separator bits 19/39/59 are exactly zero over the 1.34 M vertices sampled.

### 4.2 What is verified, and what is not

| coordinate | status | evidence |
|---|---|---|
| **y** | **[verified]** | `bits[27..38] + bits[40..46] << 12`. Against the reference: 20-quantile distribution deviation **0.049**, per-vertex correlation **0.967**. |
| **x** | **NOT correct** | `bits[8..18] + bits[20..26] << 11` gives distribution deviation 0.228 and correlation **0.04**. |
| **z** | **NOT correct** | `bits[48..58] + bits[60..63] << 11` gives distribution deviation 0.63 and correlation **-0.50**. |

The reference export of the same mesh has axis spans of `[2.2419, 2.824, 2.0756]`
(ratios `0.794 : 1 : 0.735`, nearly cubic). The current decode gives
`[2178, 11483, 4216]` (ratios `0.19 : 1 : 0.367`) — x is ~4x too small and z ~2x
too small, which is what makes the models render as thin slabs.

Strong hints, from per-bit correlation against the reference:

* the **highest bit of the real x is bit 27**, not bit 26
* the **highest bit of the real z is bit 3**, i.e. z's high block wraps further
  into the first byte than assumed (bit 3 was treated as a flag)

Neither can be resolved from a single sample: for this mesh bits 21-26, 61-63 and
0-2 are *exactly zero*, so they are degenerate bits and any field assignment
using them scores identically — which is also why the two scoring methods above
disagree on x. **A second, non-degenerate sample is needed.**

### 4.3 Scoring a candidate layout

`tools/verify_eggparty_positions.py` takes a `.mesh` file and a reference array of
the same mesh's vertex positions and reports how well a candidate partition
matches, without editing the parser:

```bash
python tools/verify_eggparty_positions.py sample.mesh reference.npy \
    --fields 8-18,20-27 27-38,40-47 48-58,60-64 --z-wrap 0,1,2:15
```

The reference is an `(n, 3)` float array whose rows correspond, in order, to the
first `n` vertices of the mesh file; any independently produced export of the
same asset works. The tool normalises both sides per axis, so it compares the
*shape* of the distribution rather than the scale, and prints the maximum
20-quantile deviation and the per-vertex correlation. A correct layout scores
deviation `< 0.10` and correlation `> 0.9` on all three axes.

## 5. Face block **[verified]**

`6 bytes` per face, **three signed 16-bit little-endian deltas**, prefix-summed
across the whole face array (not per face):

```python
faces = np.cumsum(np.frombuffer(data, dtype='<i2', count=fc * 3)).reshape(-1, 3)
```

Verified against a Blender-exported FBX of the same mesh: the FBX
`PolygonVertexIndex` reproduces all **14330 / 14330** triangles exactly,
including vertex order. The cumsum also lands exactly in `[0, vc)` on every file
sampled.

## 6. UV block **[verified]**

Pairs of `float16`, `uv_layers` pairs per vertex. Verified against the ground
truth: maximum absolute difference **0.00001** over 42990 indices, and the values
are always inside `[0, 1]`. When the uv block would exceed `end_offset`, discard
UV rather than shifting every later section.

## 7. Upstream issues found while writing this

These are independent of the layout work and probably worth fixing:

1. `identify_mesh_type()` has no branch for the `115 / 8` bytes-per-vertex
   layout, so every EggParty mesh ends up as `type == -1` and raises.
2. `bones_is_16()` returns `count != 31`; the comment says bone names are stored
   in 32-byte chunks, so the boundary test looks like it should be `32`. This
   needs a second look together with real 8-bit data.
3. `core/mesh_converter/formats/fbx.py` is a stub — `convert()` writes a 23-byte
   header and returns. Exporting FBX silently produces an unusable file.
4. `core/mesh_converter/formats/gltf.py` puts the mesh node in the scene but
   leaves the skin's joint nodes and `inverseBindMatrices` out, which strict
   importers (Blender) reject. Even after fixing that, the joint/weight
   convention is not understood well enough for an importer's armature deform to
   produce a sane pose, so the exporter in this PR emits a **static mesh**
   (skin and `JOINTS_0`/`WEIGHTS_0` dropped) instead of a broken one.
5. The optional 4-bytes-per-vertex section noted in §3 (between UV and the tail)
   is not accounted for anywhere in `_parse_mesh_testing()`, so for the ~28 % of
   `pet_low` meshes that have it every following section is read at the wrong
   offset.
