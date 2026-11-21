# Notice

This branch is a modified version of [NeoXtractor](https://github.com/NeoXtractor/NeoXtractor), which is licensed
under the GNU Affero General Public License v3.0. The modifications are released under the same licence: see
[LICENSE](LICENSE). It comes with no warranty, as the licence states.

NeoXtractor is not affiliated with NetEase, and neither is this branch. It contains no game files: everything it
shows is read from a game install the user already has.

## What was changed (2026)

The branch starts from NeoXtractor commit `e9b3cdb` (19 April 2026) and adds the Harry Potter: Magic Awakened
(HPMA) build on top, in two steps.

**1. The HPMA build** (one commit, "Recover the HPMA build's source"). Its source had been lost and was rebuilt from
the compiled build; every module compiles to the same bytecode as that build.

- New: `core/npk/asset_paths.py`, `related_archives.py`, `metadata_references.py` (real file names, same-name packs
  in `res`, `res2`, ...), `core/npk/character_catalog.py`, `character_folders.py`, `scan_control.py`,
  `core/process_memory.py`, `gui/windows/character_folders_window.py`, `gui/utils/character_scan.py`,
  `character_tree_index.py`, `file_list_theme.py` (the Characters page).
- Modified NeoXtractor files: `core/config.py`, `core/npk/npk_file.py`, `core/mesh_converter/formats/pmx.py`,
  `gui/models/npk_file_model.py`, `gui/npk_entry_filter.py`, `gui/renderers/mesh_renderer.py`,
  `gui/renderers/point_renderer.py`, `gui/theme/theme_manager.py`, `gui/utils/viewer.py`,
  `gui/widgets/managed_rhi_widget.py`, `gui/widgets/npk_file_list.py`, `gui/widgets/preview_widget.py`,
  `gui/windows/config_manager/edit_config_dialog.py`, `new_config_dialog.py`, `gui/windows/main_window.py`,
  `gui/windows/settings_window.py`.

**2. Additions to the HPMA build** (the following commit).

- Modified NeoXtractor files: `core/mesh_converter/formats/pmx.py` (MMD bone names, bone setup, fingers, rigid
  bodies, material and tail settings; its end installs the additions below at start-up),
  `gui/utils/viewer.py` (registers the newer audio and sound bank viewers),
  `gui/widgets/viewers/audio_viewer.py` (NeoXtractor's main-branch audio viewer; the info line also names the
  sound for HPMA).
- Taken unchanged from NeoXtractor's main branch: `core/wem/` and `gui/widgets/viewers/bnk_viewer.py`.
- New: the Main Character Creator (`gui/widgets/face_makeup_*`, `core/npk/face_makeup.py`, `hair_dye.py`,
  `accessory_sockets.py`, `game_tables.py`), character and animation exports (`core/mesh_converter/*`,
  `gui/widgets/character_*`), the Animations, Stages and Scene Viewer pages, item names and sorting in the
  Characters tab, the names of the game's sounds (`core/npk/wwise_names.py`), speed-ups (`core/fast_*.py`,
  `core/npk/metadata_cache.py`) and the creator's style sheet (`data/themes/wardrobe/`).

## Third-party data

`core/wem/data/packed_codebooks_aoTuV_603.bin` is the packed Vorbis codebook file from the
[ww2ogg](https://github.com/hcs64/ww2ogg) project by Adam Gashlin (hcs), used to turn Wwise audio back into Ogg
Vorbis. It is distributed under ww2ogg's own licence (`core/wem/LICENSE.ww2ogg.md`), as in NeoXtractor.
