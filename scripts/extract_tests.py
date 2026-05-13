#!/usr/bin/env python3
"""
Extract .py source files from .pyc bytecode caches.
Recursively scans tests/ for .pyc files and recreates the .py source.
"""

import importlib.util
import marshal
import os
import struct
import sys


def _get_code_consts(code):
    """Recursively collect all string constants from a code object."""
    yield code
    for const in code.co_consts:
        if hasattr(const, "co_code"):
            yield from _get_code_consts(const)


def extract_source_from_pyc(pyc_path):
    """Try to extract source code from a .pyc file."""
    with open(pyc_path, "rb") as f:
        # Skip header (varies by Python version)
        magic = f.read(4)
        flags = struct.unpack("<I", f.read(4))[0]
        if flags & 0x1:  # hash-based pyc
            f.read(8)  # source hash
        else:
            f.read(4)  # timestamp
            f.read(4)  # size
        try:
            code = marshal.load(f)
        except Exception:
            return None

    # Look for the source code in the code constants
    # The source is typically stored as the last string in co_consts,
    # or we can reconstruct from individual strings
    lines = []
    for obj in _get_code_consts(code):
        for const in obj.co_consts:
            if isinstance(const, str):
                # Look for import statements, test function defs, etc.
                s = const.strip()
                if s and (
                    s.startswith(
                        (
                            "import ",
                            "from ",
                            "def ",
                            "class ",
                            "@",
                            "#",
                            '"',
                            "'",
                            '"""',
                        )
                    )
                    or s.startswith(("pytest", "def test_", "class Test"))
                    or "test_" in s[:20]
                ):
                    lines.append(const)

    # If we got import lines, we can reconstruct the file
    if lines:
        # Deduplicate while preserving order
        seen = set()
        unique = []
        for l in lines:
            if l not in seen:
                seen.add(l)
                unique.append(l)
        return "\n\n".join(unique)

    return None


def extract_py_from_pyc_v2(pyc_path):
    """
    More aggressive extraction: dump all meaningful string constants.
    """
    with open(pyc_path, "rb") as f:
        magic = f.read(4)
        flags = struct.unpack("<I", f.read(4))[0]
        if flags & 0x1:
            f.read(8)
        else:
            f.read(4)
            f.read(4)
        try:
            code = marshal.load(f)
        except Exception:
            return None

    strings = []
    for obj in _get_code_consts(code):
        for const in obj.co_consts:
            if isinstance(const, str) and len(const) > 10:
                strings.append(const)

    # Heuristic: find blocks that look like Python source
    imports = []
    functions = []
    other = []
    for s in strings:
        s_stripped = s.strip()
        if not s_stripped:
            continue
        # Detect triple-quoted comments/docstrings vs actual code
        if s_stripped.startswith(("import ", "from ")):
            imports.append(s_stripped)
        elif s_stripped.startswith(("def ", "class ", "@", "async def ")):
            functions.append(s)
        elif s_stripped.startswith(("#", '"""', "'''")):
            other.append(s_stripped)
        elif len(s_stripped) > 100 and not s_stripped.startswith(
            ("http", "file", "The")
        ):
            other.append(s_stripped)

    parts = []
    if imports:
        parts.append("\n".join(dict.fromkeys(imports)))
    if functions:
        parts.append("\n\n\n".join(dict.fromkeys(functions)))
    if other and not (imports or functions):
        parts.append("\n".join(dict.fromkeys(other)))

    return "\n\n".join(parts) if parts else None


def main():
    # Resolve tests dir relative to project root (one level up from scripts/)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    tests_dir = os.path.join(os.path.dirname(script_dir), "tests")
    count = 0
    errors = 0

    for root, dirs, files in os.walk(tests_dir):
        # Skip __pycache__ dirs
        if "__pycache__" not in root:
            continue

        for f in files:
            if not f.endswith(".pyc"):
                continue
            # Skip conftest and __init__ for now
            if "conftest" in f or "__init__" in f:
                continue

            pyc_path = os.path.join(root, f)

            # Determine the target .py path
            parent_dir = os.path.dirname(root)  # strip __pycache__
            base = f.split(".")[0]  # remove cache suffix
            py_path = os.path.join(parent_dir, f"{base}.py")

            if os.path.exists(py_path):
                continue  # already have .py file

            source = extract_source_from_pyc(pyc_path)
            if source:
                with open(py_path, "w") as out:
                    out.write(f"# Auto-extracted from {f}\n")
                    out.write(source)
                    out.write("\n")
                print(f"  ✓ {os.path.relpath(py_path, tests_dir)}")
                count += 1
            else:
                # Try v2 extractor
                source = extract_py_from_pyc_v2(pyc_path)
                if source:
                    with open(py_path, "w") as out:
                        out.write(f"# Auto-extracted from {f}\n")
                        out.write(source)
                        out.write("\n")
                    print(f"  ~ {os.path.relpath(py_path, tests_dir)} (partial)")
                    count += 1
                else:
                    print(f"  ✗ {f} — could not extract")
                    errors += 1

    print(f"\nExtracted {count} test files ({errors} failed)")


if __name__ == "__main__":
    main()
