from __future__ import annotations

import json
from pathlib import Path

import pytest

from simpledesign.core.cutlist.cutlist import build_cutlist
from simpledesign.core.io import sdz
from simpledesign.core.model.evaluate import Evaluator
from tests.furniture import build_korpus

FILES = Path(__file__).parent / "files"


def test_roundtrip(tmp_path: Path) -> None:
    doc = build_korpus()
    path = tmp_path / "korpus.sdz"
    sdz.save(doc, path)
    loaded = sdz.load(path)
    assert sdz.to_dict(loaded) == sdz.to_dict(doc)
    assert not path.with_name("korpus.sdz.tmp").exists()
    original = build_cutlist(doc, Evaluator().evaluate(doc))
    assert build_cutlist(loaded, Evaluator().evaluate(loaded)) == original


def test_version_1_file_still_opens() -> None:
    """Regression: a file written by format version 1 must always load."""
    doc = sdz.load(FILES / "korpus_v1.sdz")
    ev = Evaluator().evaluate(doc)
    assert not ev.errors
    assert len(doc.boards) == 6
    assert sum(r.quantity for r in build_cutlist(doc, ev)) == 9


def test_newer_version_rejected(tmp_path: Path) -> None:
    data = sdz.to_dict(build_korpus())
    data["format_version"] = sdz.FORMAT_VERSION + 1
    path = tmp_path / "new.sdz"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(sdz.FileFormatError):
        sdz.load(path)


@pytest.mark.parametrize("content", ["no json", "[]", '{"format_version": 1}'])
def test_garbage_rejected(tmp_path: Path, content: str) -> None:
    path = tmp_path / "bad.sdz"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(sdz.FileFormatError):
        sdz.load(path)


def test_dangling_group_rejected() -> None:
    data = sdz.to_dict(build_korpus())
    data["boards"][0]["group_id"] = "0" * 32
    with pytest.raises(sdz.FileFormatError):
        sdz.from_dict(data)


def test_extra_keys_saved(tmp_path: Path) -> None:
    path = tmp_path / "a.sdz"
    sdz.save(build_korpus(), path, extra={"source_path": "C:/x.sdz"})
    assert sdz.load_json(path)["source_path"] == "C:/x.sdz"
    sdz.load(path)
