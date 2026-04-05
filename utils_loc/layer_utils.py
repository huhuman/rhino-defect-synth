"""Shared layer-name matching helpers."""


def normalize_layer_name_set(layer_names):
    """Convert layer names input to a normalized set for matching."""
    if layer_names is None:
        return None
    if isinstance(layer_names, (str, bytes)):
        return {str(layer_names)}
    return {str(name) for name in layer_names if str(name).strip()}


def layer_matches(layer, names):
    """Check whether a Rhino layer matches any entry in *names*."""
    if not names:
        return False
    layer_name = getattr(layer, "Name", None)
    full_path = getattr(layer, "FullPath", None)
    if layer_name in names or full_path in names:
        return True
    if full_path:
        for name in names:
            if full_path.startswith(f"{name}::"):
                return True
        tail = full_path.split("::")[-1]
        if tail in names:
            return True
    return False
