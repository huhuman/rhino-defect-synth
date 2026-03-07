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


def _normalize_extends(path, extends_value):
    if extends_value is None:
        return []
    if isinstance(extends_value, str):
        base = extends_value.strip()
        return [base] if base else []
    if isinstance(extends_value, (list, tuple)):
        bases = []
        for idx, item in enumerate(extends_value):
            if not isinstance(item, str):
                raise ValueError(
                    "Invalid config '{}': extends[{}] must be a string.".format(path, idx)
                )
            base = item.strip()
            if base:
                bases.append(base)
        return bases
    raise ValueError(
        "Invalid config '{}': extends must be a string or list of strings.".format(path)
    )


def _deep_merge(base, override):
    merged = copy.deepcopy(base or {})
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _load_with_extends(config_name, cache=None, stack=None):
    if cache is None:
        cache = {}
    stack = list(stack or [])
    config_path = root / str(config_name)
    config_key = str(config_path.resolve())

    if config_key in stack:
        chain = " -> ".join(stack + [config_key])
        raise ValueError(f"Cyclic config extends detected: {chain}")

    cached = cache.get(config_key)
    if cached is not None:
        return copy.deepcopy(cached)

    cfg = _load_yaml_mapping(config_path)
    stack.append(config_key)
    base_names = _normalize_extends(config_path, cfg.get("extends"))
    current = {k: v for k, v in cfg.items() if k != "extends"}

    merged_base = {}
    for base_name in base_names:
        base_cfg = _load_with_extends(base_name, cache=cache, stack=stack)
        merged_base = _deep_merge(merged_base, base_cfg)

    stack.pop()
    result = _deep_merge(merged_base, current)
    cache[config_key] = copy.deepcopy(result)
    return result


def load_config(config_name):
    # Priority on conflicts: current config > extends config.
    return _load_with_extends(config_name)
