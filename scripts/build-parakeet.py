#!/usr/bin/env python3
"""Build the pinned native library alongside, never over, a working CLI build."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

REVISION = "5700f127e38dd53c4735ea23ef743b4b538eacd4"


def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path, help="Existing checkout at the pinned revision")
    parser.add_argument("--output", type=Path, default=Path(".local/parakeet-native"))
    parser.add_argument("--backend", choices=("cuda", "cpu"), default="cuda")
    parser.add_argument("--cuda-arch", default="native", help="CMake CUDA architectures, e.g. 89; native for this host")
    parser.add_argument("--cc", default="gcc-13")
    parser.add_argument("--cxx", default="g++-13")
    parser.add_argument("--jobs", type=int, choices=(1, 2), default=2, help="Bound build CPU use")
    parser.add_argument("--blas-prefix", type=Path, help="Optional local OpenBLAS prefix; a separate output directory is required")
    args = parser.parse_args()
    source, output = args.source.expanduser().resolve(), args.output.expanduser().resolve()
    revision = run(["git", "-C", str(source), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    if revision != REVISION:
        parser.error(f"Expected transcribe.cpp revision {REVISION}; do not mix native and binding ABIs")
    dirty = run(["git", "-C", str(source), "status", "--porcelain", "--untracked-files=no"], capture_output=True, text=True).stdout
    if dirty:
        parser.error("The native source has tracked changes; use an unmodified pinned checkout")
    if output == source or output == source / "build":
        parser.error("Use a separate build/output directory, not the working static build")
    options = {
        "TRANSCRIBE_BUILD_SHARED": "ON", "TRANSCRIBE_CUDA": "ON" if args.backend == "cuda" else "OFF",
        "TRANSCRIBE_BUILD_TESTS": "OFF", "TRANSCRIBE_BUILD_TOOLS": "OFF", "TRANSCRIBE_BUILD_EXAMPLES": "OFF",
        "TRANSCRIBE_USE_OPENMP": "OFF", "TRANSCRIBE_USE_SYSTEM_BLAS": "ON" if args.blas_prefix else "OFF",
        "CMAKE_BUILD_TYPE": "Release", "GGML_NATIVE": "ON", "GGML_BLAS": "ON" if args.blas_prefix else "OFF",
        "CMAKE_C_COMPILER": args.cc, "CMAKE_CXX_COMPILER": args.cxx,
    }
    if args.backend == "cuda":
        options.update(CMAKE_CUDA_ARCHITECTURES=args.cuda_arch, CMAKE_CUDA_HOST_COMPILER=args.cxx)
    if args.blas_prefix:
        prefix = args.blas_prefix.expanduser().resolve()
        if not prefix.is_dir():
            parser.error("The optional local BLAS prefix is missing")
        options.update(CMAKE_PREFIX_PATH=str(prefix), BLA_VENDOR="OpenBLAS", GGML_BLAS_VENDOR="OpenBLAS")
    output.mkdir(parents=True, exist_ok=True)
    manifest = output / "build-manifest.json"
    expected = {"revision": revision, "backend": args.backend, "cmake_options": options}
    if manifest.exists() and json.loads(manifest.read_text()).get("build") != expected:
        parser.error("Build options differ from this output's manifest; choose a fresh output directory")
    command = ["cmake", "-S", str(source), "-B", str(output / "build")]
    command.extend(f"-D{key}={value}" for key, value in options.items())
    run(command)
    run(["cmake", "--build", str(output / "build"), "--target", "transcribe", "--parallel", str(args.jobs)])
    # Copy the matching pure-Python API, not its unpublished provider dependency.
    # Explicit TRANSCRIBE_LIBRARY in the child chooses this build unambiguously.
    package = source / "bindings/python/src/transcribe_cpp"
    bindings = output / "bindings/transcribe_cpp"
    shutil.copytree(package, bindings, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    libraries = sorted((output / "build").rglob("libtranscribe.so"))
    if len(libraries) != 1:
        raise RuntimeError("Expected one libtranscribe.so from this Linux build")
    library = libraries[0].resolve()
    digest = hashlib.sha256(library.read_bytes()).hexdigest()
    payload = {"build": expected, "library_sha256": digest, "library": str(library), "bindings": str(bindings.parent)}
    manifest.write_text(json.dumps(payload, indent=2) + "\n")
    os.chmod(manifest, 0o600)
    print(json.dumps({"manifest": str(manifest), "library_sha256": digest,
                      "PARAKEET_LIBRARY": str(library), "PARAKEET_BINDINGS": str(bindings.parent)}))


if __name__ == "__main__":
    main()
