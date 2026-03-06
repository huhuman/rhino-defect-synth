import copy
from pathlib import Path

import yaml

root = Path(__file__).resolve().parent.parent / "configs"


def _load_yaml_mapping(path):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Config file not found: '{path}'")
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"Invalid config '{path}': expected a mapping at root.")
    return loaded


def _deep_merge(base, override):
    merged = copy.deepcopy(base or {})
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _load_with_extends(config_name, seen=None):
    seen = set(seen or [])
    config_path = root / str(config_name)
    config_key = str(config_path.resolve())
    if config_key in seen:
        chain = " -> ".join(sorted(seen) + [config_key])
        raise ValueError(f"Cyclic config extends detected: {chain}")
    seen.add(config_key)

    cfg = _load_yaml_mapping(config_path)
    base_name = cfg.get("extends")
    current = {k: v for k, v in cfg.items() if k != "extends"}

    if base_name:
        base_cfg = _load_with_extends(base_name, seen=seen)
    else:
        base_cfg = {}
    return _deep_merge(base_cfg, current)


def _apply_modeling_defaults(cfg):
    merged = copy.deepcopy(cfg)
    modeling_cfg = merged.get("modeling")
    if not isinstance(modeling_cfg, dict):
        return merged

    strategy = modeling_cfg.get("strategy")
    component_cfg = modeling_cfg.get("component")
    if strategy == "component" or isinstance(component_cfg, dict):
        component_defaults = _load_yaml_mapping(root / "component_defaults.yaml")
        modeling_cfg["component"] = _deep_merge(component_defaults, component_cfg or {})

    if "damage" in modeling_cfg and modeling_cfg.get("damage") is not None:
        damage_defaults = _load_yaml_mapping(root / "damage_defaults.yaml")
        modeling_cfg["damage"] = _deep_merge(damage_defaults, modeling_cfg.get("damage") or {})

    return merged


def load_config(config_name):
    # Priority on conflicts: current config > extends config > defaults.
    cfg = _load_with_extends(config_name)
    return _apply_modeling_defaults(cfg)
