from __future__ import annotations

import json
import pathlib
from typing import Any

import numpy as np

from caterva2.services import server, srv_utils

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_roots",
            "description": "List the Caterva2 roots available to the current user.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_datasets",
            "description": "List datasets under a root or sub-path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "limit": {"type": "integer"},
                    "offset": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_dataset_info",
            "description": "Get metadata for a specific dataset.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_dataset_stats",
            "description": "Compute summary statistics for a dataset.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "stats": {"type": "array", "items": {"type": "string"}},
                    "axis": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
    },
]

DEFAULT_STATS = ["min", "max", "mean", "std"]
SUPPORTED_STATS = {"min", "max", "mean", "sum", "std", "var", "argmin", "argmax", "any", "all"}


def _json_safe(value):
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump())
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def list_roots(*, user):
    roots = [root for root, _ in server.filter_roots(["@public", "@shared", "@personal"], user)]
    return {"roots": roots}


def list_datasets(*, user, path: str, limit: int = 50, offset: int = 0):
    directory = server.get_writable_path(pathlib.Path(path), user)
    if directory.is_file():
        full_paths = [path]
    else:
        datasets = [
            str(relpath.with_suffix("") if relpath.suffix == ".b2" else relpath)
            for _, relpath in srv_utils.walk_files(directory)
        ]
        datasets = sorted(datasets)
        full_paths = [f"{path}/{name}" for name in datasets]
    page = full_paths[offset : offset + limit]
    return {
        "path": path,
        "datasets": page,
        "total": len(full_paths),
        "offset": offset,
        "has_more": offset + limit < len(full_paths),
    }


def get_dataset_info(*, user, path: str):
    abspath = server.get_abspath(pathlib.Path(path), user)
    if abspath.is_dir():
        raise FileNotFoundError(f"Dataset not found: {path}")
    return {"path": path, "info": srv_utils.read_metadata(abspath)}


def get_dataset_stats(*, user, path: str, stats: list[str] | None = None, axis: int | None = None):
    stats = stats or DEFAULT_STATS
    invalid_stats = set(stats) - SUPPORTED_STATS
    if invalid_stats:
        raise ValueError(f"Unsupported statistics: {sorted(invalid_stats)}")

    dataset = server.open_b2(server.get_abspath(pathlib.Path(path), user), pathlib.Path(path))
    if not hasattr(dataset, "shape"):
        raise TypeError(f"Target is not a dataset: {path}")

    result = {
        "path": path,
        "shape": list(dataset.shape),
        "dtype": str(dataset.dtype),
        "axis": axis,
        "stats": {},
    }
    for stat_name in stats:
        method = getattr(dataset, stat_name)
        result["stats"][stat_name] = _json_safe(method(axis=axis))
    return result


TOOL_MAP = {
    "list_roots": list_roots,
    "list_datasets": list_datasets,
    "get_dataset_info": get_dataset_info,
    "get_dataset_stats": get_dataset_stats,
}


def execute_tool(tool_name: str, tool_args: dict[str, Any], *, user) -> dict[str, Any]:
    tool_function = TOOL_MAP.get(tool_name)
    if tool_function is None:
        return {"ok": False, "data": None, "error": {"code": "UNKNOWN_TOOL", "message": tool_name}}
    try:
        result = tool_function(user=user, **tool_args)
        return {"ok": True, "data": _json_safe(result), "error": None}
    except Exception as exc:
        return {
            "ok": False,
            "data": None,
            "error": {"code": type(exc).__name__.upper(), "message": str(exc)},
        }


def serialize_tool_result(result: dict[str, Any]) -> str:
    return json.dumps(result)
