#!/usr/bin/env python3
"""Copy the pinned local native build into a stable, model-free runtime bundle."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil

from importlib.util import module_from_spec, spec_from_file_location


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", required=True, type=Path, help="Output of scripts/build-parakeet.py")
    parser.add_argument("--output", required=True, type=Path, help="Fresh directory outside a disposable worktree")
    args = parser.parse_args()
    source, target = args.build.resolve(), args.output.expanduser().resolve()
    spec = spec_from_file_location("native_build", Path(__file__).with_name("build-parakeet.py"))
    helper = module_from_spec(spec)
    spec.loader.exec_module(helper)
    manifest = json.loads((source / "build-manifest.json").read_text())
    if manifest["build"]["revision"] != helper.REVISION:
        parser.error("The build is not the pinned tested revision")
    if target.exists():
        parser.error("Choose a fresh output directory; existing runtime bundles are never overwritten")
    libraries = list((source / "build").rglob("*.so*"))
    if not libraries or any(not item.is_file() for item in libraries):
        parser.error("Expected the complete Linux shared-library build")
    if len({p.name for p in libraries}) != len(libraries):
        parser.error("Ambiguous shared-library names in this build")
    target.mkdir(parents=True, mode=0o700)
    lib = target / "lib"
    lib.mkdir()
    for item in libraries:
        if item.is_symlink():
            (lib / item.name).symlink_to(Path(os.readlink(item)).name)
        else:
            shutil.copy2(item, lib / item.name)
    shutil.copytree(source / "bindings", target / "bindings", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    library = lib / Path(manifest["library"]).name
    digest = hashlib.sha256(library.read_bytes()).hexdigest()
    if digest != manifest["library_sha256"]:
        raise RuntimeError("Installed core library hash differs from measured build")
    manifest.update(library=str(library), bindings=str(target / "bindings"), libraries={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in lib.iterdir() if not p.is_symlink()})
    (target / "build-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"PARAKEET_LIBRARY":str(library), "PARAKEET_BINDINGS":str(target / "bindings"), "library_sha256":digest}))


if __name__ == "__main__":
    main()
