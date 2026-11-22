# NeoXtractor – Harry Potter: Magic Awakened build (source)

This branch is the full source of the HPMA build of NeoXtractor: real file names for most of the game's packs, the
Characters, Animations and Stages pages, the Scene Viewer, the Main Character Creator, MMD (PMX/VMD) export and the
game's sound names. It is made for HPMA, but you can try it on other games built on the NeoX engine. It is based on NeoXtractor, adjusted with Harry Potter: Magic Awakened in mind.

The fork was made with AI assistance of Claude. The original creator of NeoXtractor has been told beforehand.

Licensed under the GNU AGPL v3.0, like NeoXtractor: see [LICENSE](LICENSE). What was changed, and the third-party
data used, is listed in [NOTICE.md](NOTICE.md). No game files are included.

## Running from source

You need Python 3.12, [uv](https://docs.astral.sh/uv/) and a game install.

```
uv sync
uv run main.py
```

Pick the "Harry Potter: Magic Awakened" config, then open any `.npk` under the game's `res` folder. The
Characters, Animations and Stages pages are in the category list; the Scene Viewer and Main Character Creator are
under Tools.

## How to use

### Opening the game's files

1. In **Active Config**, pick **Harry Potter: Magic Awakened**.
2. **File → Open File** and choose a `.npk` from the game's `res` folder (for example `res\model\character.npk`).
3. The same pack in `res2`, `res3`, ... and the launcher's update folder (`Documents\res`) opens with it, and the
   files show their real names. To open one pack on its own, untick **File → Load all same-name related
   copies together (HPMA)** before opening.

The category list under the search box picks what the left side shows: the usual file types, plus **Audio**,
**Characters**, **Stages** and **Animations**.

### Characters

Choose **Characters** in the category list. The first scan of a pack takes a while (the progress bar has a
Cancel button); later scans of the same files are quicker.

- Folders follow the game's own layout. Each mesh lists the textures and materials it uses; double-click a mesh
  to open its textures. Items show their English name and icon, and the **Sort** box orders by name or size.
- The search box and the Binary / Text / Slot boxes filter this tree too.
- Right-click a selection to:
  - **Save selected / folder (including linked textures)…**: the raw files;
  - **Export all as one GLB… / PMX…**: everything selected in one model;
  - **Export each mesh separately**, **each folder as one file**, **faces** (face, eyes and eyebrows, optionally
    with every face shape), **hats only**, or **item icons (PNG)**;
  - **Add to Scene Viewer**, or open the **Main Character Creator** on a face folder.
- **Export options** in the same menu holds the PMX switches (see *PMX and MMD*).

### Main Character Creator

**Tools → Main Character Creator** (open any HPMA pack first, so the game folder is known). It works like the
game's wardrobe:

- **Female / Male** at the top; it opens on a random look. The **▼** button has **Random look** and **Clear
  makeup**.
- Pick a face, face shape, skin tone, makeup, eyes, hairstyle and hair dye, outfits and accessories from the
  list on the right. **Filter** narrows it by rarity or trait; the search box finds items by name.
- The **Layers** box hides single meshes from the preview and from exports. The two body buttons switch between
  the size as modelled and the grown-up size.
- **Wand** holds one of the game's wand skins in the right hand. **Pose** plays a standing pose in the 3D view
  (the wand follows the hand). With no pose picked, an outfit that has its own idle plays it, and any other
  plays the **Standard** pose, as in the game.
- **Export ▼** saves the character as GLB, PMX or OBJ, without animation (**PMX options** are in the same menu).
  **Pose as VMD… / GLB…** in the same menu save the pose that plays (the picked one, or the outfit's own idle) on
  its own: a VMD for the exported PMX, or a GLB on the game's default character. **Save texture ▼** saves the baked face, eye, eyebrow and hair textures.
  **Add to Scene Viewer** sends the character there.

### Scene Viewer

**Tools → Scene Viewer**. Add models with **Add to Scene Viewer** in the right-click menu of the file list or the
Characters tree (or select them and press **Ctrl+Shift+A**), from the creator, or from a stage.

- Right-click objects for **Assign Texture…**, **Auto-match Textures** (finds a model's own textures in the
  game), **Clear Texture** and **Remove**.
- **Show bones** and **Play animations** change the view.
- **Export ▼** saves the whole scene as **GLB…** or **PMX…**, or **Animations (PMX + VMD)…**.

### Stages

Open `scene.npk` (or a DLC scene pack such as `res_dlc1_hogsmeade.npk`) and choose **Stages** in the category
list. It lists that pack's stage scenes, grouped by stage; the models and textures they place are read from the
game's other packs automatically. With any other pack open, the page shows a hint instead.

Select scenes, pick a format (GLB, PMX + textures, or glTF + .bin + textures) and press **Export Selected…**;
tick **Include collision / navigation helpers** to keep those meshes. **Open in Scene Viewer** loads one scene
with its textures already matched.

### Animations

Choose **Animations** in the category list. Clips are grouped by skeleton; the selected clip plays on its model
in the viewer area. Right-click a clip for **Export GLB…** (model with the clip), **Export VMD…** (a motion for
the PMX models this app exports), or **Export character animations (VMD / GLB)…** to pick the male or female
player character and export many clips at once, as VMD or GLB.

### Audio

Choose **Audio** in the category list to see the sound banks and `.wem` sounds. Hashed and numbered sounds show
their path or the bank that uses them, and the search matches those names too. Double-click a sound to play it in
the **Audio Viewer**, which can also save it as a playable file (Ogg for this game's sounds).

### PMX and MMD

Every PMX export (Characters, creator, Scene Viewer) has a **PMX options** menu:

- **Translate bones to MMD names** and **MMD bone setup** (センター, IK legs, display frames), so MMD motions
  work on the model;
- **Five fingers**: the game rigs three fingers, this gives the ring and little fingers their own bones;
- **Body rigid bodies**: collision so hair and cloth physics don't fall through the body;
- **Raise shoulder bones** for cleaner raised arms.

Bone names come from a built-in table. To change them, put a `pmx_bone_names.xlsx`, `.csv` or `.txt` next to
NeoXtractor: first column the game's bone name, second the PMX name.

### Caches

The app keeps a few caches next to itself (`scan_cache\`, `item_links_cache.json`) so later scans are faster.
They are rebuilt when the game files change, and deleting them is safe.

---

# NeoXtractor

![Python versions](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue)
![GitHub Actions Workflow Status](https://img.shields.io/github/actions/workflow/status/MarcosVLl2/NeoXtractor/ci.yml)
[![Discord](https://img.shields.io/badge/Discord-%235865F2.svg?logo=discord&logoColor=white)](https://discord.gg/eedXVqzmfn)

A Python tool for extracting, viewing, and converting files from NPK archives used by Netease's NeoX game engine.

THIS TOOL IS IN ITS EARLY STAGES, ANY HELP IS WELCOME! FEEL FREE TO OPEN A PULL REQUEST OR FORK THE REPOSITORY IF YOU WISH TO ADD SOMETHING OR FIX ANY ISSUE YOU SPOT

## Download

Download the latest release at [GitHub Releases](https://github.com/MarcosVLl2/NeoXtractor/releases)

### Bleeding edge builds

If you'd like to download the latest builds without waiting for a release, you can go to the [Actions](https://github.com/MarcosVLl2/NeoXtractor/actions) tab to see a list of automatic builds.

Find the build that you'd like to use, click it and download at the **Artifacts** section.

## Usage

### GUI

1. Run `neoxtractor.exe` directly to start in GUI mode.
2. Some games' NPK (EXPK) requires XOR key to be set. We have default profile for Onmyoji currently. To set XOR key, create a new config in Config Manager.
![Config Manager tutorial](assets/set-xor-key.png)
3. Open the NPK file.

#### Introduction

![GUI Overview](assets/gui-overview.png)

Main window is divided into two main parts: File list area and preview area.

In the file list area, you are able to:
- Select current config

  Note that every time you change the config, the current loaded NPK file will be unloaded.

- Filter entries inside NPK file
- View NPK's file list

Within the file list area, you can right click to operate on the selected entries. Currently, you can:
- Extract all selected entries
- Open selected entries with specific viewer
- Rename current entry

  You must select only one entry to rename the entry.

  New name of the entry is bind to entry's file signature. All names are saved in current active config.

### CLI

Command-line interface is still being worked on.

## Development

You will need [uv](https://github.com/astral-sh/uv) to properly setup the development environment.

1. Install dependencies
```bash
uv sync
```

2. Create virtual environment
```bash
uv venv
```

3. Enter virtual environment (required every time you start to work on the project)
```bash
# Linux
source .venv/bin/activate

# Windows
.venv/bin/activate.bat
```

## Have questions?

Feel free to join the Discord server (link at the top of this document) to ask them!

## Acknowledgements

**NeoX is an in-house game engine developed by Netease.**

**NeoXtractor is not affiliated with Netease.**

This tool is limited for educational purposes.