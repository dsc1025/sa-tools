"""Locate versioned Stone Age resource files in a selected client data folder."""
from __future__ import annotations

from pathlib import Path


def client_data_dir(client_dir: Path) -> Path:
    """Resolve a selected client root or data folder to its resource directory."""
    client_dir = client_dir.expanduser().resolve()
    if not client_dir.is_dir():
        raise NotADirectoryError(f"Not a client directory: {client_dir}")
    if client_dir.name.lower() == "data":
        return client_dir
    data_dir = client_dir / "data"
    if data_dir.is_dir():
        return data_dir
    if any(client_dir.glob("spradrn_*.bin")):
        return client_dir
    raise FileNotFoundError(f"Cannot find a data folder or spradrn index in {client_dir}")


def resource_file(data: Path, prefix: str) -> Path:
    matches = sorted(data.glob(f"{prefix}*.bin"), key=lambda path: path.name.lower())
    if not matches:
        raise FileNotFoundError(f"No {prefix}*.bin found in {data}")
    if len(matches) > 1:
        names = ", ".join(path.name for path in matches)
        raise ValueError(f"More than one {prefix} file found; select a data folder with one active resource set: {names}")
    return matches[0]


def resources(data: Path) -> dict[str, Path]:
    data = data.resolve()
    if not data.is_dir():
        raise NotADirectoryError(f"Not a data directory: {data}")
    return {
        "spradrn": resource_file(data, "spradrn_"),
        "spr": resource_file(data, "spr_"),
        "adrn": resource_file(data, "adrn_"),
        "real": resource_file(data, "real_"),
    }
