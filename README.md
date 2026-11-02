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

### Wwise event names (Identity V)

This is a special Identity V mode: it only runs when the **Identity V** config
is selected in **Active Config**. All of its code and the bundled hash index
live in `id5mapping/wwise/`.

The bundled hash index (`id5mapping/wwise/wwise_event_hash_index.bin`) stores only
`FNV-1(event name) -> event name`, which is exactly how Wwise ids its event
objects. It is a compact binary (magic `NXWE`, zlib-compressed sorted hash table
plus a shared name pool) and about four times smaller than the old JSON.

When a package is loaded the tool:

1. overlays the base and hot-update IDX files (`<root>/res` + `<root>/Documents/res`,
   hot rows win on identical md5) so one listing covers both,
2. finds the `.bnk` banks inside the package and mounts them, turning every
   event id into its readable name,
3. lists the wem media embedded in those banks as individual `.wem` files,
4. resolves the external wems through the local `wwise.thy` + `wwise.idx`
   (shortID → `xxh32("<shortID>.wem", 163)` → md5 → entry) and labels the
   package entries with the event name only.

Offsets, md5 names and media ids are never shipped: they differ between
installations and are computed at runtime.

If a game update adds event names, rebuild the index from the event name list
(**Rebuild index...** in the dialog, or the CLI):

```bash
python -m id5mapping.wwise.index_builder --events wwise_audio_length_ex.py --out id5mapping/wwise/wwise_event_hash_index.bin
```

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