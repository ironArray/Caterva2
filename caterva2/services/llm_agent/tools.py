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
    {
        "type": "function",
        "function": {
            "name": "get_slice",
            "description": (
                "Retrieve a slice of data values from a dataset. "
                "Use this when the user asks to inspect actual values rather than only metadata or statistics. "
                "Limited to 10,000 elements maximum."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "slices": {
                        "type": "string",
                        "description": (
                            "Slice specification using Python syntax such as '0:10', "
                            "'0:5, 0:3', ':, 0', or '0, :, 0:10'."
                        ),
                    },
                },
                "required": ["path"],
            },
        },
    },
]

DEFAULT_STATS = ["min", "max", "mean", "std"]
SUPPORTED_STATS = {"min", "max", "mean", "sum", "std", "var", "argmin", "argmax", "any", "all"}
MAX_SLICE_ELEMENTS = 10_000


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


def _parse_slice_string(slice_str: str, shape: tuple) -> tuple:
    parts = [p.strip() for p in slice_str.split(",")]
    if len(parts) > len(shape):
        raise ValueError(f"Too many dimensions in slice: got {len(parts)}, dataset has {len(shape)}")

    result = []
    for part in parts:
        if part == "" or part == ":":
            result.append(slice(None))
        elif ":" in part:
            components = part.split(":")
            if len(components) == 2:
                start = int(components[0]) if components[0] else None
                stop = int(components[1]) if components[1] else None
                result.append(slice(start, stop))
            elif len(components) == 3:
                start = int(components[0]) if components[0] else None
                stop = int(components[1]) if components[1] else None
                step = int(components[2]) if components[2] else None
                result.append(slice(start, stop, step))
            else:
                raise ValueError(f"Invalid slice syntax: '{part}'")
        else:
            result.append(int(part))
    return tuple(result)


def _compute_slice_size(slices: tuple, shape: tuple) -> int:
    size = 1
    for i, s in enumerate(slices):
        if i >= len(shape):
            break
        dim_size = shape[i]
        if isinstance(s, int):
            continue
        if isinstance(s, slice):
            start, stop, step = s.indices(dim_size)
            length = len(range(start, stop, step))
            size *= max(0, length)

    for i in range(len(slices), len(shape)):
        size *= shape[i]
    return size


def _default_slice_for_shape(shape: tuple, max_elements: int) -> tuple:
    if len(shape) == 1:
        return (slice(0, min(shape[0], max_elements)),)

    dims = []
    elements_per_dim = int(max_elements ** (1 / len(shape)))
    for dim_size in shape:
        dims.append(slice(0, min(dim_size, max(1, elements_per_dim))))
    return tuple(dims)


def get_slice(*, user, path: str, slices: str | None = None):
    dataset = server.open_b2(server.get_abspath(pathlib.Path(path), user), pathlib.Path(path))
    if not hasattr(dataset, "shape"):
        raise TypeError(f"Target is not a dataset: {path}")

    shape = dataset.shape
    if slices is None:
        slice_tuple = _default_slice_for_shape(shape, MAX_SLICE_ELEMENTS)
        slice_str_used = str(slice_tuple)
    else:
        slice_tuple = _parse_slice_string(slices, shape)
        slice_str_used = slices

    estimated_size = _compute_slice_size(slice_tuple, shape)
    if estimated_size > MAX_SLICE_ELEMENTS:
        raise ValueError(
            f"Requested slice would return ~{estimated_size:,} elements, exceeding limit of "
            f"{MAX_SLICE_ELEMENTS:,}. Please request a smaller slice."
        )

    data = dataset[slice_tuple]
    result_shape = list(data.shape) if hasattr(data, "shape") else []
    return {
        "path": path,
        "dataset_shape": list(shape),
        "dtype": str(dataset.dtype),
        "slice": slice_str_used,
        "result_shape": result_shape,
        "data": _json_safe(data),
    }


TOOL_MAP = {
    "list_roots": list_roots,
    "list_datasets": list_datasets,
    "get_dataset_info": get_dataset_info,
    "get_dataset_stats": get_dataset_stats,
    "get_slice": get_slice,
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
