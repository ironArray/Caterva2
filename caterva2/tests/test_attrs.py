"""Public attributes stay separate from storage and protocol metadata."""

from pathlib import Path
from types import SimpleNamespace

import blosc2
import pytest
from jinja2 import Environment, FileSystemLoader

from caterva2 import File, models
from caterva2.services import srv_utils


@pytest.mark.parametrize("attrs", [None, {}, {"experiment": {"tags": ["optical"]}}])
@pytest.mark.parametrize("present", [False, True])
def test_attrs_client_and_panel_fallback(attrs, present):
    meta = srv_utils.read_metadata(blosc2.arange(10))
    meta.schunk.vlmeta = {"legacy": "legacy value"}
    info = meta.model_dump()
    info.pop("attrs")
    if present:
        info["attrs"] = attrs
    # The same conversion used for peer metadata preserves absent versus empty.
    meta = srv_utils.get_model_from_obj(info, models.Metadata)
    expected = attrs if present and attrs is not None else info["schunk"]["vlmeta"]
    root = SimpleNamespace(urlbase="http://unused", name="@public")
    file = File(root, "array.b2nd", meta=info)
    assert file.attrs == expected
    assert file.vlmeta == info["schunk"]["vlmeta"]
    templates = Path(srv_utils.__file__).parent / "templates"
    env = Environment(loader=FileSystemLoader(templates), autoescape=True)
    rendered = env.get_template("includes/info_metadata.html").render(meta=meta)
    assert ("legacy value" in rendered) == ("legacy" in expected)
    assert ("optical" in rendered) == ("experiment" in expected)


@pytest.mark.parametrize("frame", [False, True])
def test_attrs_preserve_user_keys_and_hide_fill_metadata(frame):
    obj = blosc2.SChunk(data=b"0123456789") if frame else blosc2.arange(10)
    obj.vlmeta["_user_key"] = {"tags": ["optical", "v2"]}
    for key in ("fill_nonce", "fill_state", "published_url"):
        obj.vlmeta[key] = "protocol value"
    meta = srv_utils.read_metadata(obj)
    assert meta.attrs == {"_user_key": {"tags": ["optical", "v2"]}}
    assert getattr(meta, "schunk", meta).vlmeta["fill_nonce"] == "protocol value"
