from pathlib import Path
import yaml

root = Path(__file__).resolve().parent.parent / "configs"


def load_config(config_name):
    cfg = yaml.safe_load((root / config_name).read_text()) or {}
    base_path = root / cfg.get("extends", "cube_base.yaml")
    base = yaml.safe_load(base_path.read_text()) or {}
    merged = {**base, **{k: v for k, v in cfg.items() if k != "extends"}}
    return merged
