"""
Tests for run_agent.py's image-attachment support (the GENERATION intake's
"upload a drawing" path) -- build_user_message (shared with webui/app.py)
and _load_image_data_url (the terminal's /image <path> command). No
network calls; these only check the local wiring, not that a real model
reads the image correctly.
"""
from __future__ import annotations

import base64

from run_agent import _load_image_data_url, build_user_message

# A genuine 2x2 red PNG, built with stdlib struct/zlib (not Pillow -- not a
# declared dependency of this project) and confirmed against a real
# /responses call to actually decode as a valid image, not just look like
# base64.
_TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEElEQVR42mP4z8AARAwQ"
    "CgAf7gP9Y167WwAAAABJRU5ErkJggg=="
)


def test_build_user_message_without_image_is_plain_string_content():
    item = build_user_message("hello")
    assert item == {"role": "user", "content": "hello"}


def test_build_user_message_with_image_is_a_multimodal_content_list():
    data_url = "data:image/png;base64,AAAA"
    item = build_user_message("what's in this drawing?", data_url)
    assert item["role"] == "user"
    assert item["content"] == [
        {"type": "input_text", "text": "what's in this drawing?"},
        {"type": "input_image", "detail": "auto", "image_url": data_url},
    ]


def test_load_image_data_url_returns_none_for_a_missing_file(tmp_path, capsys):
    result = _load_image_data_url(str(tmp_path / "does_not_exist.png"))
    assert result is None
    assert "Not a file" in capsys.readouterr().out


def test_load_image_data_url_returns_none_for_a_non_image_file(tmp_path, capsys):
    text_file = tmp_path / "notes.txt"
    text_file.write_text("just some notes")
    result = _load_image_data_url(str(text_file))
    assert result is None
    assert "Not a recognized image type" in capsys.readouterr().out


def test_load_image_data_url_encodes_a_real_image_file():
    import tempfile
    from pathlib import Path

    png_bytes = base64.b64decode(_TINY_PNG_B64)
    with tempfile.TemporaryDirectory() as tmpdir:
        image_path = Path(tmpdir) / "drawing.png"
        image_path.write_bytes(png_bytes)
        data_url = _load_image_data_url(str(image_path))

    assert data_url.startswith("data:image/png;base64,")
    encoded = data_url.split(",", 1)[1]
    assert base64.b64decode(encoded) == png_bytes
