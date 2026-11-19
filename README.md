# Rhino to SketchUp (3DM → SKP)

A local Python command-line converter for exporting Rhino `.3dm` models to editable SketchUp `.skp` geometry. It preserves Rhino layers as SketchUp tags, keeps nested groups and shared block definitions, and reports when planar surfaces may not work as a single face with SketchUp’s Push/Pull tool.

[简体中文](README.zh-CN.md)

## What it does

- Builds native SketchUp faces from supported Rhino trim loops, including holes, using Rhino’s own shared vertices to close those loops.
- Keeps block instances and their transforms rather than flattening the scene into a single mesh. It uses saved Rhino render meshes as a fallback for some geometry it cannot build directly.
- Reopens the written SKP to check geometry, groups and face provenance. It produces a marked review copy when faces remain split, or when a conversion cannot be verified.
- Runs on local files without changing the input. It is a CLI, not a Rhino or SketchUp plug-in or a hosted converter.

## Setup

This project has been exercised on macOS with the SketchUp 2026 C API. Other operating systems and SketchUp versions are not yet validated. The SDK is **not** included. Request access to the [SketchUp Desktop SDK](https://developer.sketchup.com/) from Trimble, and use it under its applicable terms. See the [SketchUp C API documentation](https://extensions.sketchup.com/developers/sketchup_c_api/sketchup/index.html). Point `SKETCHUP_API_LIBRARY` at the actual native library binary. Rhino geometry is read using [McNeel’s rhino3dm](https://github.com/mcneel/rhino3dm); use a version with the Brep loop and trim bindings (8.32.1 or later in the 8.x series).

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
export SKETCHUP_API_LIBRARY="/path/to/SketchUpAPI.framework/Versions/A/SketchUpAPI"
python src/convert.py input.3dm -o output.skp
```

Choose a fresh output path. A successful run writes the requested SKP. If checks fail, the converter returns exit code 2 and writes reports and the diagnostic SKP to a sibling review directory; it does not label that copy a verified export. Review the marked file and reports before use.

## Limits

A saved-file check is **not** a mouse-over test: results depend on SketchUp’s editing context and native Push/Pull behavior. The converter is not lossless. Text, dimensions and hatches are not supported; invalid trims, some curved or complex surfaces, and tiny features can be omitted, approximated or fail verification. It does not automatically weld or flatten geometry merely to make a warning go away.

Rhino and SketchUp are products of their respective owners. This is an independent project, not an official McNeel or Trimble product. No proprietary SDK, stock assets or client model files are included. No open-source license has been selected for this repository.

### Layer materials and a clean review copy

To assign a local SketchUp material to an exact Rhino layer name, repeat this option for each layer:

```sh
python src/convert.py input.3dm -o output.skp \
  --layer-material "GLASS=/path/to/glass.skm" \
  --layer-material "MESH=/path/to/metal-mesh.skm"
```

Other faces default to white. The mapping uses the source face owner's layer, not the placement layer of a block instance. Local `.skm` files are not distributed here. Use `--unmarked-review` to save an extra review copy without orange markings when a geometry check fails. The converter still returns exit code 2, and the review copy remains explicitly unverified.

## Tests

Run `python -m unittest discover -s tests -v` with the compatible C API configured. The tests generate geometry locally rather than distributing model files.
