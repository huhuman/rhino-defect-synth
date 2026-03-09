"""Plugin command availability checks and auto-load helpers."""

import os

import Rhino
import rhinoscriptsyntax as rs


def _normalize_command_list(value):
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        name = str(value).strip()
        return [name] if name else []
    out = []
    for item in value:
        name = str(item).strip()
        if name:
            out.append(name)
    return out


def _is_command_available(command_name):
    name = str(command_name or "").strip()
    if not name:
        return False
    try:
        return bool(Rhino.Commands.Command.IsCommand(name))
    except Exception:
        try:
            return bool(rs.IsCommand(name))
        except Exception:
            return False


def _load_plugin_from_path(plugin_path):
    path = os.path.abspath(os.path.expandvars(os.path.expanduser(str(plugin_path))))
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Plugin file not found: '{path}'")

    plugin_id = None
    try:
        plugin_id = Rhino.PlugIns.PlugIn.IdFromPath(path)
    except Exception:
        plugin_id = None

    # Preferred path for already-registered plugins.
    try:
        if plugin_id and str(plugin_id) != "00000000-0000-0000-0000-000000000000":
            try:
                ok = Rhino.PlugIns.PlugIn.LoadPlugIn(plugin_id, True, True)
            except TypeError:
                ok = Rhino.PlugIns.PlugIn.LoadPlugIn(plugin_id)
            if ok:
                return path, plugin_id
    except Exception:
        pass

    # Fallback: path overload with out Guid.
    try:
        import clr
        import System

        plugin_id_ref = clr.Reference[System.Guid](System.Guid.Empty)
        result = Rhino.PlugIns.PlugIn.LoadPlugIn(path, plugin_id_ref)
        loaded_id = plugin_id_ref.Value
        if str(result).lower() not in ("error", "failure") and str(loaded_id) != str(System.Guid.Empty):
            return path, loaded_id
    except Exception as exc:
        raise RuntimeError(f"Failed to load plugin from '{path}': {exc}") from exc

    raise RuntimeError(f"Failed to load plugin from '{path}'.")


def ensure_plugin_commands(plugin_cfg):
    cfg = dict(plugin_cfg or {})
    if not cfg:
        print("Preparation plugin autoload: not configured.")
        return
    if not bool(cfg.get("enabled", True)):
        print("Preparation plugin autoload: disabled by config.")
        return
    verbose = bool(cfg.get("verbose", True))

    required_commands = _normalize_command_list(
        cfg.get("required_commands") or cfg.get("commands")
    )
    if not required_commands:
        required_commands = ["CaptureRenderChannels", "CaptureBaseColorMask"]

    missing_before = [name for name in required_commands if not _is_command_available(name)]
    if not missing_before:
        if verbose:
            print(
                "Preparation plugin autoload: required commands already available -> "
                + ", ".join(required_commands)
            )
        return

    plugin_path = cfg.get("path")
    strict = bool(cfg.get("strict", True))
    if not plugin_path:
        message = (
            "Missing required Rhino command(s): {}. "
            "Set preparation.plugin_autoload.path to auto-load the plugin."
        ).format(", ".join(missing_before))
        if strict:
            raise RuntimeError(message)
        print("Warning:", message)
        return

    loaded_path, loaded_id = _load_plugin_from_path(plugin_path)
    print(f"Preparation plugin autoload: loaded '{loaded_path}' (id={loaded_id}).")

    missing_after = [name for name in required_commands if not _is_command_available(name)]
    if missing_after:
        message = "Plugin loaded but command(s) still unavailable: {}.".format(", ".join(missing_after))
        if strict:
            raise RuntimeError(message)
        print("Warning:", message)
