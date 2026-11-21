# NeoXtractor – Harry Potter: Magic Awakened build (source)

This branch is the full source of the HPMA build of NeoXtractor: real file names for the game's packs, the
Characters, Animations and Stages pages, the Scene Viewer, the Main Character Creator, MMD (PMX/VMD) export and the
game's sound names. It is based on NeoXtractor as of April 2026; the same features are being offered to
NeoXtractor itself as pull requests against its current version.

> The additions were written with AI assistance (Claude Code). The original author of NeoXtractor has been told.

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