"""
File-backed store for Mega/Torrent selection state.
Keeps file lists and selected IDs on disk so state survives bot restarts.
"""
import json
import os
import threading

_STORE_DIR  = "data/selection_store"
_lock       = threading.Lock()


def _path(gid: str) -> str:
    return os.path.join(_STORE_DIR, f"{gid}.json")


def write_state(gid: str, file_list: list, selected_ids: list) -> bool:
    try:
        os.makedirs(_STORE_DIR, exist_ok=True)
        payload = {"file_list": file_list, "selected_ids": selected_ids}
        with _lock:
            with open(_path(gid), "w") as f:
                json.dump(payload, f)
        return True
    except Exception:
        return False


def read_state(gid: str) -> dict | None:
    try:
        with _lock:
            with open(_path(gid)) as f:
                return json.load(f)
    except Exception:
        return None


def update_selected(gid: str, selected_ids: list) -> bool:
    try:
        state = read_state(gid)
        if state is None:
            return False
        state["selected_ids"] = selected_ids
        with _lock:
            with open(_path(gid), "w") as f:
                json.dump(state, f)
        return True
    except Exception:
        return False


def delete_state(gid: str) -> None:
    try:
        p = _path(gid)
        if os.path.exists(p):
            with _lock:
                os.remove(p)
    except Exception:
        pass
