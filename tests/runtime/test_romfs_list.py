"""Check RomFS directory enumeration against a synthetic index, without a game, SDL or GPU.

The production lookup/listing code and path validation are built with warnings as errors. Overlays share directories
with original files and replace file sizes; a large subtree bounds the work by children rather than descendants.
Usage: uv run pytest tests/runtime/test_romfs_list.py
"""

import os
import random
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "runtime/sdk/fs/romfs.c"


def build_listing(out: Path, source: str) -> Path:
    cc = shutil.which("clang") or shutil.which("cc") or shutil.which("gcc")
    if not cc:
        pytest.skip("no C compiler")
    # Keep the production types, index lookups and listing together; only the backing index is synthetic.
    types = source[source.index("typedef struct { uint64_t offset") : source.index("static uint8_t *index_bytes")]
    entry = source[source.index("static Entry entry(") : source.index("static double now_s(")]
    lookups = source[source.index("// ---- index lookup") : source.index("// ---- overlays")]
    listing = source[source.index("uint32_t romfs_entry_type(") : source.index("// ---- overrides")]
    state = """
static uint8_t *index_bytes;
static uint64_t file_count, strings_offset, entry_reads;
static char mount_name[32] = "content";
static char **added;
static size_t nadded, noverlay;
static Overlay *overlays;
"""
    entry = entry.replace("Entry e;", "entry_reads++; Entry e;")
    (out / "list.inc").write_text(types + state + entry + lookups + listing, encoding="utf-8")
    exe = out / ("t_romfs_list.exe" if os.name == "nt" else "t_romfs_list")
    crt = ["-D_CRT_SECURE_NO_WARNINGS"] if os.name == "nt" else []
    subprocess.run([cc, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", *crt, f"-I{out}",
                    f"-I{ROOT / 'runtime/sdk/fs'}", str(ROOT / "tests/runtime/t_romfs_list.c"), "-o", str(exe)],
                   check=True)
    return exe


@pytest.fixture(scope="module")
def listing(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return build_listing(tmp_path_factory.mktemp("romfs-list"), SOURCE.read_text(encoding="utf-8"))


def run_listing(listing: Path, originals=(), additions=(), *, path="content:/Dir", stop=0, repeat=1):
    rows = []
    for added, files in enumerate((originals, additions)):
        for name, size, replacement in sorted(files):
            rows.append(f"{added}\t{size}\t{replacement}\t{name}\n")
    p = subprocess.run([str(listing), path, str(stop), str(repeat)], input="".join(rows), capture_output=True,
                       text=True, encoding="utf-8", check=True)
    entries = [(int(kind), int(size), name) for kind, size, name in (line.split("\t") for line in p.stdout.splitlines())]
    result, typed, kind, reads = map(int, p.stderr.split())
    return entries, result, typed, kind, reads


def test_merge_directories_and_file_sizes(listing):
    got = run_listing(listing,
                      [("Dir/Shared/a", 1, -1), ("Dir/old", 10, 21), ("Dir/z", 9, -1)],
                      [("Dir/Shared/b", 2, -1), ("Dir/new", 7, -1)])
    assert got[:4] == ([(1, 0, "Shared"), (0, 7, "new"), (0, 21, "old"), (0, 9, "z")], 0, 0, 0)


@pytest.mark.parametrize("stop", [1, -1, 2, -2])
def test_callback_stop_applies_across_both_indexes(listing, stop):
    entries, result, *_ = run_listing(listing, [("Dir/a", 1, -1), ("Dir/z", 9, -1)],
                                      [("Dir/b", 2, -1)], stop=stop)
    assert entries == [(0, 1, "a"), (0, 2, "b")][:abs(stop)]
    assert result == 0


@pytest.mark.parametrize("path", ["content:/", "content:////"])
def test_mount_root_is_a_directory(listing, path):
    got = run_listing(listing, [("Dir/Sub/a", 1, -1), ("root.bin", 2, -1)], path=path)
    assert got[:4] == ([(1, 0, "Dir"), (0, 2, "root.bin")], 0, 0, 0)
    assert run_listing(listing, path=path)[:4] == ([], 0, 0, 0)


def test_added_only_directory(listing):
    got = run_listing(listing, additions=[("Dir/only", 17, -1)])
    assert got[:4] == ([(0, 17, "only")], 0, 0, 0)


def test_missing_directory_and_file_type(listing):
    files = [("Dir/file", 10, -1)]
    assert run_listing(listing, files, path="content:/Missing")[:4] == ([], 0x202, 0x202, -1)
    assert run_listing(listing, files, path="content:/Dir/file")[:4] == ([], 0x202, 0, 1)


@pytest.mark.parametrize("path", ["other:/Dir", "content:", "content:/../Dir", "content:/Dir//bad", "content:/Dir\\bad"])
def test_invalid_path(listing, path):
    assert run_listing(listing, path=path)[:4] == ([], 0x202, 0x202, -1)


def test_subtree_bounds_keep_adjacent_names(listing):
    files = [(f"Dir/{name}", 1, -1) for name in ["A-", "A.", "A/a", "A/deep/b", "A0", "A00/c", "AB/d", "B"]]
    entries, result, *_ = run_listing(listing, files)
    assert entries == [(0, 1, "A-"), (0, 1, "A."), (1, 0, "A"), (0, 1, "A0"), (1, 0, "A00"),
                       (1, 0, "AB"), (0, 1, "B")]
    assert result == 0


def test_large_subtree_uses_index_searches(listing):
    files = [(f"Dir/Sub/{i:06d}", 1, -1) for i in range(205_665)]
    entries, result, _, _, reads = run_listing(listing, files)
    assert entries == [(1, 0, "Sub")]
    assert result == 0
    assert reads < 100  # a full walk reads all 205,665 descendants; binary searches need fewer than 100 rows


def test_nested_indexes_against_flat_reference(listing):
    rng = random.Random(20261122)
    originals, additions, visible = [], [], {}
    for i in range(600):
        parts = ["Dir", rng.choice(["A-", "Shared", "Z", "café", "日本語"])]
        parts += [rng.choice(["Inner", "Nested"]) for _ in range(rng.randrange(3))]
        name = "/".join([*parts, f"file{i:04d}"])
        size = rng.randrange(10000)
        replacement = rng.choice([-1, rng.randrange(10000)])
        target = originals if rng.randrange(2) else additions
        target.append((name, size, replacement))
        visible[name] = size if replacement < 0 else replacement
    for folder in ["Dir", "Dir/Shared", "Dir/Shared/Inner", "Dir/café", "Dir/日本語", "Missing"]:
        prefix = folder + "/"
        expected = {}
        for name, size in sorted(visible.items()):
            if name.startswith(prefix):
                child, slash, _ = name[len(prefix):].partition("/")
                expected.setdefault(child, (int(bool(slash)), 0 if slash else size, child))
        entries, result, *_ = run_listing(listing, originals, additions, path="content:/" + folder)
        assert entries == list(expected.values())
        assert result == (0 if expected else 0x202)


def test_many_small_subtrees_keep_linear_work(listing):
    files = [(f"Dir/Sub{i:04d}/only", 1, -1) for i in range(5000)]
    entries, result, _, _, reads = run_listing(listing, files)
    assert entries == [(1, 0, f"Sub{i:04d}") for i in range(5000)]
    assert result == 0
    assert reads < 3 * len(files)  # avoid a full-index binary search for each single-file child
