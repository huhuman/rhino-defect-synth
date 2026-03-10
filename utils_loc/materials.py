#! python3
import Rhino
import scriptcontext as sc
import rhinoscriptsyntax as rs
import os
import random
import re
from utils_loc.texture_naming import (
    IMAGE_EXTENSIONS as _IMAGE_EXTENSIONS,
    MAP_SUFFIXES as _MAP_SUFFIXES,
    expand_token_variants as _expand_token_variants,
    guess_map_kind_from_stem as _guess_map_kind_from_stem,
    stem_ends_with_suffix_token as _stem_ends_with_suffix_token,
    stem_contains_color_token as _stem_contains_color_token,
    strip_known_suffix as _strip_known_suffix,
)

_PBR_CHANNEL_KEYS = [
    "albedo",
    "normal",
    "occlusion",
    "roughness",
    "metallic",
    "height",
    "opacity",
    "specular",
]
_PBR_ENUM_CANDIDATES = {
    "albedo": ("PBR_BaseColor", "BaseColor", "Diffuse", "Bitmap"),
    "normal": ("PBR_Normal", "Normal", "Bump"),
    "roughness": ("PBR_Roughness", "Roughness", "Glossiness"),
    "occlusion": ("PBR_AmbientOcclusion", "AmbientOcclusion"),
    "metallic": ("PBR_Metallic", "Metallic"),
    "height": ("PBR_Displacement", "Displacement", "PBR_Height", "Height", "Bump"),
    "opacity": ("PBR_Opacity", "Opacity", "Transparency"),
    "specular": ("PBR_Specular", "Specular"),
}
_BASIC_CHANNEL_SETTERS = {
    "albedo": "SetBitmapTexture",
    "normal": "SetBumpTexture",
    "opacity": "SetTransparencyTexture",
}

_DEFAULT_BUILTIN_CATEGORY = "Architectural"
_DEFAULT_BUILTIN_SUBCATEGORY1 = "Wall"
_DEFAULT_BUILTIN_SUBCATEGORY2 = "Concrete"
_IMPORTED_RENDER_MATERIAL_NAMES = set()
_TRACK_SECTION = "rhino_defect_synth"
_TRACK_ENTRY_IMPORTED_MATERIALS = "imported_render_material_names"

# Last update by 2025/02/13
# Vray_Material_Metadata = {
#     "/Rubber Rough 001": ["1712823064", "cc427c2f-b935-412f-85a2-e3e521608178"],
#     "/Iron Rough Rusty": ["1712822095", "82bd9e0c-bf27-4e59-956d-701daa7b4750"],
#     "/Concrete Weathered 300cm": ["1712821438", "08ac88c2-bb86-4ba4-ac47-8622ad8be5e9"],
#     "/Concrete Simple 001 300cm": ["1712821428", "7a028bb4-a297-4e2b-b85d-b3e5dbb5a2e9"],
#     "/Concrete Simple B01 200cm": ["1667490677", "79ba2bea-7908-470f-b6c0-3c9ee9f3d174"],
#     "/Concrete Simple C01 200cm": ["1667490683", "e03680fe-36bb-4504-9b76-a650d9ee83ed"],
#     "/Concrete Simple E02 400cm": ["1667490753", "50226111-aae5-4b14-8bac-7f6c4fae51a8"],
#     "/Concrete Simple F01 200cm": ["1667490764", "ce5ed96c-5b12-4478-8018-95b9899de5d1"],
#     "/Concrete Simple G01 400cm": ["1667490775", "64c6d606-08b5-45b1-bcd3-df4ac8727721"],
#     "/Concrete Floor Satin 300cm": ["1712821423", "786f9bed-d9fe-4563-8b93-9fef469e3473"],
#     "/Concrete Grey 03 100cm": ["1639468414", "c6515288-34c2-49d8-b32e-1a5eb30c5c4b"],
#     "/Concrete Grey 06 100cm": ["1639468424", "d83b8f99-c819-4ad2-83e5-0a921279af79"],
# }


# def _get_render_material_names():
#     return [mat.DisplayName for mat in sc.doc.RenderMaterials]


# def import_Vray_materials():
#     all_render_materials = _get_render_material_names()
#     for mat, info in Vray_Material_Metadata.items():
#         is_exist = False
#         for render_mat in all_render_materials:
#             if mat in render_mat:
#                 is_exist = True
#                 break
#         if not is_exist:
#             print(f'Importing material: {mat}')
#             rs.Command(f"-_vrayCosmos _Import _Revision={info[0]} _Triplanar=On {info[1]}")


def _normalize_material_name(value):
    return str(value or "").strip()


def _get_render_material_lookup():
    lookup = {}
    for mat in sc.doc.RenderMaterials:
        name = _normalize_material_name(
            getattr(mat, "DisplayName", None) or getattr(mat, "Name", None)
        )
        if not name:
            continue
        key = name.lower()
        if key not in lookup:
            lookup[key] = name
    return lookup


def _resolve_builtin_material_root(
    category=_DEFAULT_BUILTIN_CATEGORY,
    subcategory1=_DEFAULT_BUILTIN_SUBCATEGORY1,
    subcategory2=_DEFAULT_BUILTIN_SUBCATEGORY2,
):
    user_root = os.path.expanduser("~")
    return os.path.join(
        user_root,
        "AppData",
        "Roaming",
        "McNeel",
        "Rhinoceros",
        "8.0",
        "Localization",
        "en-US",
        "Render Content",
        category,
        subcategory1,
        subcategory2,
    )


def _normalize_builtin_selector_list(value, default_value):
    if value is None:
        return [str(default_value)]
    if isinstance(value, (list, tuple)):
        items = value
    else:
        items = [value]
    normalized = []
    for item in items:
        token = _normalize_material_name(item)
        if token:
            normalized.append(token)
    return normalized


def _resolve_builtin_material_roots(categories=None, subcategory1s=None, subcategory2s=None):
    categories = _normalize_builtin_selector_list(categories, _DEFAULT_BUILTIN_CATEGORY)
    subcategory1s = _normalize_builtin_selector_list(subcategory1s, _DEFAULT_BUILTIN_SUBCATEGORY1)
    subcategory2s = _normalize_builtin_selector_list(subcategory2s, _DEFAULT_BUILTIN_SUBCATEGORY2)

    pair_count = min(len(categories), len(subcategory1s), len(subcategory2s))
    roots = []
    seen = set()
    for idx in range(pair_count):
        root = _resolve_builtin_material_root(
            category=categories[idx],
            subcategory1=subcategory1s[idx],
            subcategory2=subcategory2s[idx],
        )
        key = root.lower()
        if key in seen:
            continue
        seen.add(key)
        roots.append(root)
    return roots


def _normalize_path_list(path_list):
    if path_list is None:
        return []
    if isinstance(path_list, (str, bytes)):
        path_list = [path_list]
    normalized = []
    for path in path_list:
        candidate = _normalize_material_name(path)
        if not candidate:
            continue
        normalized.append(os.path.abspath(os.path.expanduser(candidate)))
    return normalized


def _index_material_files(material_dirs):
    index = {}
    for material_dir in _normalize_path_list(material_dirs):
        if not os.path.isdir(material_dir):
            continue
        for filename in sorted(os.listdir(material_dir)):
            filepath = os.path.join(material_dir, filename)
            if not os.path.isfile(filepath):
                continue
            stem, _ = os.path.splitext(filename)
            key = stem.lower()
            if key and key not in index:
                index[key] = filepath
    return index


def _record_imported_material_name(name):
    normalized = _normalize_material_name(name)
    if normalized:
        tracked = _get_tracked_material_names()
        tracked.add(normalized.lower())
        _set_tracked_material_names(tracked)


def _read_doc_string(section, entry):
    table = getattr(sc.doc, "Strings", None)
    if table is None:
        return ""

    key = "{}::{}".format(section, entry)
    for method_name, args in (
        ("GetValue", (section, entry)),
        ("GetString", (section, entry)),
        ("GetValue", (key,)),
        ("GetString", (key,)),
    ):
        method = getattr(table, method_name, None)
        if method is None:
            continue
        try:
            value = method(*args)
        except Exception:
            continue
        if value is None:
            continue
        text = str(value)
        if text:
            return text
    return ""


def _write_doc_string(section, entry, value):
    table = getattr(sc.doc, "Strings", None)
    if table is None:
        return False

    key = "{}::{}".format(section, entry)
    text = str(value or "")
    if text:
        for method_name, args in (
            ("SetString", (section, entry, text)),
            ("SetValue", (section, entry, text)),
            ("SetString", (key, text)),
            ("SetValue", (key, text)),
        ):
            method = getattr(table, method_name, None)
            if method is None:
                continue
            try:
                method(*args)
                return True
            except Exception:
                continue
        return False

    for method_name, args in (
        ("Delete", (section, entry)),
        ("Delete", (key,)),
        ("SetString", (section, entry, "")),
        ("SetValue", (section, entry, "")),
        ("SetString", (key, "")),
        ("SetValue", (key, "")),
    ):
        method = getattr(table, method_name, None)
        if method is None:
            continue
        try:
            method(*args)
            return True
        except Exception:
            continue
    return False


def _load_persisted_tracked_material_names():
    names = set()
    payload = _read_doc_string(_TRACK_SECTION, _TRACK_ENTRY_IMPORTED_MATERIALS)
    for token in payload.splitlines():
        name = _normalize_material_name(token).lower()
        if name:
            names.add(name)
    return names


def _set_tracked_material_names(names):
    normalized = {
        _normalize_material_name(name).lower()
        for name in (names or [])
        if _normalize_material_name(name)
    }
    _IMPORTED_RENDER_MATERIAL_NAMES.clear()
    _IMPORTED_RENDER_MATERIAL_NAMES.update(normalized)
    payload = "\n".join(sorted(normalized))
    _write_doc_string(_TRACK_SECTION, _TRACK_ENTRY_IMPORTED_MATERIALS, payload)


def _get_tracked_material_names():
    tracked = set(_IMPORTED_RENDER_MATERIAL_NAMES)
    tracked.update(_load_persisted_tracked_material_names())
    if tracked != _IMPORTED_RENDER_MATERIAL_NAMES:
        _IMPORTED_RENDER_MATERIAL_NAMES.clear()
        _IMPORTED_RENDER_MATERIAL_NAMES.update(tracked)
    return tracked


def _import_material_file(filepath):
    filepath = os.path.abspath(os.path.expanduser(str(filepath)))
    if not os.path.isfile(filepath):
        raise ValueError("Material file does not exist: {}".format(filepath))

    before = _get_render_material_lookup()
    Rhino.Render.RenderMaterial.ImportMaterialAndAssignToLayers(sc.doc, filepath, [])
    after = _get_render_material_lookup()

    imported_names = [name for key, name in after.items() if key not in before]
    for name in imported_names:
        _record_imported_material_name(name)

    if imported_names:
        return imported_names[0]

    fallback = os.path.splitext(os.path.basename(filepath))[0]
    key = fallback.lower()
    if key in after:
        return after[key]
    return fallback


def import_materials(
    category=_DEFAULT_BUILTIN_CATEGORY,
    subcategory1=_DEFAULT_BUILTIN_SUBCATEGORY1,
    subcategory2=_DEFAULT_BUILTIN_SUBCATEGORY2,
    material_names=None,
    material_search_paths=None,
):
    """
    Import materials from built-in library/search paths.

    Args:
        material_names: None imports all files in built-in folder (legacy behavior).
                        Otherwise imports only matched names or direct file paths.
        material_search_paths: Optional extra folders containing material files.
    """
    builtin_roots = _resolve_builtin_material_roots(
        categories=category,
        subcategory1s=subcategory1,
        subcategory2s=subcategory2,
    )
    if material_names is None:
        imported = []
        existing = _get_render_material_lookup()
        for builtin_root in builtin_roots:
            if not os.path.isdir(builtin_root):
                print("Material path does not exist: {}".format(builtin_root))
                continue
            for filename in sorted(os.listdir(builtin_root)):
                filepath = os.path.join(builtin_root, filename)
                if not os.path.isfile(filepath):
                    continue
                stem = os.path.splitext(filename)[0]
                if stem.lower() in existing:
                    continue
                imported_name = _import_material_file(filepath)
                imported.append(imported_name)
                existing = _get_render_material_lookup()
                print("Importing material: {}".format(imported_name))
        return imported

    options = []
    if isinstance(material_names, (str, bytes)):
        options = [_normalize_material_name(material_names)]
    else:
        options = [_normalize_material_name(name) for name in (material_names or [])]
    options = [name for name in options if name]
    if not options:
        return []

    index_dirs = list(builtin_roots) + list(_normalize_path_list(material_search_paths))
    material_file_index = _index_material_files(index_dirs)

    imported = []
    existing = _get_render_material_lookup()
    for option in options:
        key = option.lower()
        if key in existing:
            continue

        candidate_path = os.path.abspath(os.path.expanduser(option))
        if os.path.isfile(candidate_path):
            imported_name = _import_material_file(candidate_path)
            imported.append(imported_name)
            existing = _get_render_material_lookup()
            print("Importing material from path: {}".format(imported_name))
            continue

        material_path = material_file_index.get(key)
        if not material_path:
            continue
        imported_name = _import_material_file(material_path)
        imported.append(imported_name)
        existing = _get_render_material_lookup()
        print("Importing material: {}".format(imported_name))
    return imported


def _normalize_material_options(value):
    if isinstance(value, (list, tuple, set)):
        values = list(value)
    else:
        values = [value]
    options = []
    for item in values:
        item_str = _normalize_material_name(item)
        if item_str:
            options.append(item_str)
    return options


def _collect_texture_seed_paths_single_dir(texture_dir):
    all_files = sorted(os.listdir(texture_dir))
    color_candidates = []
    for filename in all_files:
        stem, ext = os.path.splitext(filename)
        if ext.lower() not in _IMAGE_EXTENSIONS:
            continue
        if _stem_contains_color_token(stem):
            color_candidates.append(filename)

    candidate_files = color_candidates
    if not candidate_files:
        candidate_files = [
            name
            for name in all_files
            if os.path.splitext(name)[1].lower() in (".jpg", ".jpeg")
        ]

    seed_map = {}
    for filename in candidate_files:
        stem, ext = os.path.splitext(filename)
        if ext.lower() not in _IMAGE_EXTENSIONS:
            continue
        kind = _guess_map_kind_from_stem(stem)
        if kind and kind != "albedo":
            continue
        root_stem = _strip_known_suffix(stem)
        key = root_stem.lower()
        if key in seed_map:
            continue
        seed_map[key] = {
            "material_name": root_stem,
            "texture_seed_path": os.path.abspath(os.path.join(texture_dir, filename)),
        }
    return seed_map


def collect_texture_seed_paths(texture_dir, recursive=False):
    texture_dir = os.path.abspath(os.path.expanduser(str(texture_dir)))
    if not texture_dir or not os.path.isdir(texture_dir):
        return {}

    if not recursive:
        return _collect_texture_seed_paths_single_dir(texture_dir)

    seed_map = {}
    for root, dirs, files in os.walk(texture_dir):
        dirs.sort()
        if not any(os.path.splitext(name)[1].lower() in _IMAGE_EXTENSIONS for name in files):
            continue
        single = _collect_texture_seed_paths_single_dir(root)
        for key, value in single.items():
            if key not in seed_map:
                seed_map[key] = value
    return seed_map


def _resolve_material_choice_source(choice, existing_lookup, builtin_index, texture_seed_index):
    option = _normalize_material_name(choice)
    if not option:
        return None

    option_key = option.lower()
    if option_key in existing_lookup:
        return {
            "requested_option": option,
            "material_name": existing_lookup[option_key],
            "source": "existing",
            "path": None,
        }

    direct_path = os.path.abspath(os.path.expanduser(option))
    if os.path.isfile(direct_path):
        stem = os.path.splitext(os.path.basename(direct_path))[0]
        return {
            "requested_option": option,
            "material_name": stem or option,
            "source": "file",
            "path": direct_path,
        }

    texture_entry = texture_seed_index.get(option_key)
    if texture_entry:
        return {
            "requested_option": option,
            "material_name": option,
            "source": "texture_seed",
            "path": texture_entry["texture_seed_path"],
        }

    builtin_path = builtin_index.get(option_key)
    if builtin_path:
        return {
            "requested_option": option,
            "material_name": option,
            "source": "builtin_file",
            "path": builtin_path,
        }

    return None


def select_layer_materials(
    layer_material_choices,
    rng_seed=None,
    texture_root_dir=None,
    texture_recursive=True,
    builtin_category=_DEFAULT_BUILTIN_CATEGORY,
    builtin_subcategory1=_DEFAULT_BUILTIN_SUBCATEGORY1,
    builtin_subcategory2=_DEFAULT_BUILTIN_SUBCATEGORY2,
    material_search_paths=None,
):
    """
    Pick one material per layer from configured options after filtering unavailable ones.

    Returns:
        dict[layer_name] = material selection descriptor.
    """
    layer_material_choices = dict(layer_material_choices or {})
    if not layer_material_choices:
        return {}

    rng = random.Random(rng_seed) if rng_seed is not None else random.Random()
    existing_lookup = _get_render_material_lookup()

    builtin_roots = _resolve_builtin_material_roots(
        categories=builtin_category,
        subcategory1s=builtin_subcategory1,
        subcategory2s=builtin_subcategory2,
    )
    builtin_index = _index_material_files(list(builtin_roots) + list(_normalize_path_list(material_search_paths)))

    texture_seed_index = {}
    if texture_root_dir:
        texture_seed_index = collect_texture_seed_paths(
            texture_dir=texture_root_dir,
            recursive=bool(texture_recursive),
        )

    selected = {}
    for layer_name, raw_options in layer_material_choices.items():
        options = _normalize_material_options(raw_options)
        if not options:
            raise ValueError("No material options configured for layer '{}'.".format(layer_name))

        available = []
        missing = []
        for option in options:
            resolved = _resolve_material_choice_source(
                option,
                existing_lookup=existing_lookup,
                builtin_index=builtin_index,
                texture_seed_index=texture_seed_index,
            )
            if resolved is None:
                missing.append(option)
                continue
            available.append(resolved)

        if not available:
            raise ValueError(
                "No available materials for layer '{}'. options={}, missing={}".format(
                    layer_name,
                    options,
                    missing,
                )
            )

        retry_options = list(available)
        rng.shuffle(retry_options)
        chosen = dict(retry_options[0])
        chosen["retry_options"] = retry_options
        selected[str(layer_name)] = chosen

    return selected


def _load_material_from_selection(selection):
    desired_name = _normalize_material_name(selection.get("material_name"))
    desired_key = desired_name.lower()

    existing_lookup = _get_render_material_lookup()
    if desired_key in existing_lookup:
        return existing_lookup[desired_key]

    source = selection.get("source")
    source_path = selection.get("path")
    if source == "texture_seed":
        create_material_from_texture_jpg(
            source_path,
            material_name=desired_name,
            add_to_doc=True,
        )
    elif source in ("file", "builtin_file"):
        _import_material_file(source_path)
    elif source == "existing":
        pass
    else:
        raise ValueError(
            "Unsupported material source '{}' for material '{}'.".format(source, desired_name)
        )

    existing_lookup = _get_render_material_lookup()
    if desired_key in existing_lookup:
        return existing_lookup[desired_key]

    requested_option = _normalize_material_name(selection.get("requested_option"))
    requested_key = requested_option.lower()
    if requested_key in existing_lookup:
        return existing_lookup[requested_key]

    raise ValueError(
        "Material '{}' was selected but could not be loaded from source '{}'.".format(
            desired_name or requested_option,
            source,
        )
    )


def _ensure_single_material(selection):
    attempts = []
    retry_options = selection.get("retry_options")
    if isinstance(retry_options, list):
        attempts.extend([dict(item) for item in retry_options if isinstance(item, dict)])

    # Ensure originally selected option is always attempted first.
    primary = dict(selection or {})
    attempts = [primary] + [
        item
        for item in attempts
        if _normalize_material_name(item.get("material_name")).lower()
        != _normalize_material_name(primary.get("material_name")).lower()
    ]

    errors = []
    for attempt in attempts:
        try:
            return _load_material_from_selection(attempt)
        except Exception as exc:
            errors.append(
                "{}({}): {}".format(
                    _normalize_material_name(attempt.get("material_name")) or "<unnamed>",
                    _normalize_material_name(attempt.get("source")) or "<unknown>",
                    exc,
                )
            )

    raise ValueError(
        "All candidate materials failed to load. attempts={}".format(errors)
    )


def ensure_selected_layer_materials(layer_material_selection):
    """
    Import selected materials if needed and return layer->actual material display name.
    """
    layer_material_selection = dict(layer_material_selection or {})
    if not layer_material_selection:
        return {}

    resolved = {}
    cache = {}
    for layer_name, selection in layer_material_selection.items():
        desired_name = _normalize_material_name(selection.get("material_name"))
        key = desired_name.lower()
        if key not in cache:
            cache[key] = _ensure_single_material(selection)
        resolved[str(layer_name)] = cache[key]
    return resolved


def choose_and_import_layer_materials(
    layer_material_choices,
    rng_seed=None,
    texture_root_dir=None,
    texture_recursive=True,
    builtin_category=_DEFAULT_BUILTIN_CATEGORY,
    builtin_subcategory1=_DEFAULT_BUILTIN_SUBCATEGORY1,
    builtin_subcategory2=_DEFAULT_BUILTIN_SUBCATEGORY2,
    material_search_paths=None,
):
    selected = select_layer_materials(
        layer_material_choices=layer_material_choices,
        rng_seed=rng_seed,
        texture_root_dir=texture_root_dir,
        texture_recursive=texture_recursive,
        builtin_category=builtin_category,
        builtin_subcategory1=builtin_subcategory1,
        builtin_subcategory2=builtin_subcategory2,
        material_search_paths=material_search_paths,
    )
    return ensure_selected_layer_materials(selected)


def _build_image_index(texture_dir):
    index = {}
    for filename in os.listdir(texture_dir):
        stem, ext = os.path.splitext(filename)
        if ext.lower() not in _IMAGE_EXTENSIONS:
            continue
        key = stem.lower()
        if key not in index:
            index[key] = os.path.abspath(os.path.join(texture_dir, filename))
    return index


def _find_map_from_index(image_index, root_stem, suffixes):
    root_key = root_stem.lower()
    for stem_key, path in image_index.items():
        if _strip_known_suffix(stem_key).lower() != root_key:
            continue
        for suffix in suffixes:
            matched, _ = _stem_ends_with_suffix_token(stem_key, suffix)
            if matched:
                return path
    return None


def _find_map_by_color_replacement(image_index, color_stem, target_kind):
    """Try map lookup by replacing color-like token with target channel names."""
    if not color_stem:
        return None

    target_suffixes = _MAP_SUFFIXES.get(target_kind, ())
    color_tokens = sorted(_MAP_SUFFIXES["albedo"], key=len, reverse=True)
    base_stem = color_stem.lower()

    for color_token in color_tokens:
        for source_variant in _expand_token_variants(color_token):
            if source_variant not in base_stem:
                continue
            pattern = re.compile(re.escape(source_variant), flags=re.IGNORECASE)
            for target_suffix in target_suffixes:
                for target_variant in _expand_token_variants(target_suffix):
                    key = pattern.sub(target_variant, base_stem).lower()
                    if key in image_index:
                        return image_index[key]
    return None


def find_texture_bitmaps(texture_jpg_path):
    """Find companion bitmap maps next to a base texture path.

    Expected texture naming pattern:
        <root>.jpg, <root>_normal.png, <root>_ao.jpg, <root>_roughness.jpg, ...
    """
    texture_jpg_path = os.path.abspath(texture_jpg_path)
    if not os.path.isfile(texture_jpg_path):
        raise ValueError("Texture file does not exist: {}".format(texture_jpg_path))

    texture_dir = os.path.dirname(texture_jpg_path)
    stem = os.path.splitext(os.path.basename(texture_jpg_path))[0]
    stem_kind = _guess_map_kind_from_stem(stem)
    root_stem = _strip_known_suffix(stem)
    image_index = _build_image_index(texture_dir)

    bitmaps = {key: None for key in _MAP_SUFFIXES.keys()}
    if stem_kind is None or stem_kind == "albedo":
        bitmaps["albedo"] = texture_jpg_path
    else:
        bitmaps[stem_kind] = texture_jpg_path

    for kind, suffixes in _MAP_SUFFIXES.items():
        if bitmaps[kind]:
            continue
        bitmaps[kind] = _find_map_from_index(image_index, root_stem, suffixes)

    # If color/albedo map exists, try replacing its channel token to find peers:
    # e.g. *_COLOR.* -> *_roughness.*, *_normal.*, ...
    color_stem = None
    if bitmaps.get("albedo"):
        color_stem = os.path.splitext(os.path.basename(bitmaps["albedo"]))[0]
    elif _stem_contains_color_token(stem):
        color_stem = stem

    if color_stem:
        for kind in _PBR_CHANNEL_KEYS:
            if kind == "albedo" or bitmaps.get(kind):
                continue
            bitmaps[kind] = _find_map_by_color_replacement(image_index, color_stem, kind)

    # If caller passed *_normal.jpg, etc., still try to find root/albedo textures.
    if not bitmaps["albedo"]:
        root_key = root_stem.lower()
        if root_key in image_index:
            bitmaps["albedo"] = image_index[root_key]
        else:
            bitmaps["albedo"] = _find_map_from_index(image_index, root_stem, _MAP_SUFFIXES["albedo"])

    bitmaps["root_stem"] = root_stem
    return bitmaps


def _get_texture_type(texture_type_name):
    try:
        return getattr(Rhino.DocObjects.TextureType, texture_type_name)
    except Exception:
        return None


def _enable_physically_based_material(material):
    enabled = False
    for method_name in ("ToPhysicallyBased",):
        method = getattr(material, method_name, None)
        if method is None:
            continue
        try:
            method()
            enabled = True
            break
        except Exception:
            continue

    for attr_name in ("UsePhysicallyBased", "IsPhysicallyBased"):
        if not hasattr(material, attr_name):
            continue
        try:
            setattr(material, attr_name, True)
            enabled = True
        except Exception:
            continue

    try:
        if getattr(material, "PhysicallyBased", None) is not None:
            enabled = True
    except Exception:
        pass
    return enabled


def _set_texture_by_type(material, texture_path, type_names):
    if not texture_path:
        return False

    set_texture = getattr(material, "SetTexture", None)
    if set_texture is None:
        return False

    texture = Rhino.DocObjects.Texture()
    texture.FileName = texture_path
    texture.Enabled = True

    for type_name in type_names:
        texture_type = _get_texture_type(type_name)
        if texture_type is None:
            continue
        try:
            if set_texture(texture, texture_type):
                return True
        except Exception:
            continue
    return False


def _apply_basic_channel_textures(material, texture_bitmaps, channel_status):
    for key, setter_name in _BASIC_CHANNEL_SETTERS.items():
        texture_path = texture_bitmaps.get(key)
        if not texture_path:
            continue
        basic_ok = False
        setter = getattr(material, setter_name, None)
        if setter is not None:
            try:
                setter(texture_path)
                basic_ok = True
            except Exception:
                pass
        status = channel_status.setdefault(key, {})
        status["basic_ok"] = basic_ok


def _apply_pbr_channel_textures(material, texture_bitmaps, channel_status, status_key):
    for key, type_names in _PBR_ENUM_CANDIDATES.items():
        texture_path = texture_bitmaps.get(key)
        if not texture_path:
            continue
        pbr_ok = _set_texture_by_type(material, texture_path, type_names)
        status = channel_status.setdefault(key, {})
        status[status_key] = pbr_ok


def build_material_from_texture_bitmaps(texture_bitmaps, material_name):
    """Create a Rhino material from discovered texture bitmaps."""
    material = Rhino.DocObjects.Material()
    material.Name = material_name
    _enable_physically_based_material(material)
    channel_status = {}
    _apply_basic_channel_textures(material, texture_bitmaps, channel_status)
    _apply_pbr_channel_textures(material, texture_bitmaps, channel_status, status_key="pbr_ok")
    return material, channel_status


def _make_unique_material_name(base_name):
    existing = {mat.DisplayName.lower() for mat in sc.doc.RenderMaterials}
    if base_name.lower() not in existing:
        return base_name
    idx = 1
    while True:
        candidate = "{}_{:02d}".format(base_name, idx)
        if candidate.lower() not in existing:
            return candidate
        idx += 1


def _is_render_material_instance(value):
    render_cls = getattr(Rhino.Render, "RenderMaterial", None)
    if render_cls is None or value is None:
        return False
    try:
        return isinstance(value, render_cls)
    except Exception:
        return False


def _render_material_type_label(render_material):
    if render_material is None:
        return ""
    for attr in ("TypeName", "TypeDescription", "Name"):
        try:
            value = getattr(render_material, attr, None)
        except Exception:
            value = None
        text = _normalize_material_name(value)
        if text:
            return text
    return render_material.__class__.__name__


def _render_material_exists_in_doc(render_material):
    if render_material is None:
        return False

    target_id = None
    for id_attr in ("Id", "InstanceId"):
        try:
            value = getattr(render_material, id_attr, None)
        except Exception:
            value = None
        if value:
            target_id = value
            break

    for mat in sc.doc.RenderMaterials:
        if mat is render_material:
            return True
        if target_id is None:
            continue
        for id_attr in ("Id", "InstanceId"):
            try:
                mat_id = getattr(mat, id_attr, None)
            except Exception:
                mat_id = None
            if mat_id and mat_id == target_id:
                return True
    return False


def _is_render_material_physically_based(render_material, type_label):
    texture_generation = None
    try:
        texture_generation = getattr(Rhino.Render.RenderTexture, "TextureGeneration", None)
    except Exception:
        texture_generation = None
    if texture_generation is not None:
        for mode_name in ("Skip", "Allow"):
            mode = getattr(texture_generation, mode_name, None)
            if mode is None:
                continue
            try:
                sim = render_material.SimulatedMaterial(mode)
            except Exception:
                continue
            try:
                is_pbr = getattr(sim, "IsPhysicallyBased", None)
            except Exception:
                is_pbr = None
            if isinstance(is_pbr, bool):
                return is_pbr

    for attr in ("IsPhysicallyBased", "PhysicallyBased"):
        try:
            value = getattr(render_material, attr, None)
        except Exception:
            value = None
        if isinstance(value, bool):
            return value
    return "physically" in str(type_label or "").lower()


def _create_render_material_from_basic(material):
    render_cls = Rhino.Render.RenderMaterial
    _enable_physically_based_material(material)

    from_material = getattr(render_cls, "FromMaterial", None)
    if from_material is not None:
        for args in ((material, sc.doc),):
            try:
                render_material = from_material(*args)
            except Exception:
                continue
            if _is_render_material_instance(render_material):
                return render_material, "FromMaterial", True, args

    create_basic = getattr(render_cls, "CreateBasicMaterial", None)
    if create_basic is not None:
        for args in (
            (material,),
            (material, sc.doc),
            (sc.doc, material),
            (),
        ):
            try:
                render_material = create_basic(*args)
            except Exception:
                continue
            if _is_render_material_instance(render_material):
                return render_material, "CreateBasicMaterial", False, args
    raise RuntimeError("Unable to create Rhino render material from basic material.")


def add_material_to_render_table(
    material,
    material_name=None,
    make_unique=True,
    texture_bitmaps=None,
    channel_status=None,
):
    """Add a Rhino material into the current document render material table."""
    material_name = material_name or material.Name or "material_from_texture"
    if make_unique:
        material_name = _make_unique_material_name(material_name)

    try:
        render_material, factory_name, converted_ok, selected_args = _create_render_material_from_basic(
            material
        )
    except Exception:
        raise RuntimeError(
            "Failed to create Rhino RenderMaterial for '{}' from basic material.".format(
                material_name
            )
        )

    render_material.Name = material_name
    render_type = _render_material_type_label(render_material)

    if texture_bitmaps and channel_status is not None:
        _apply_pbr_channel_textures(
            render_material,
            texture_bitmaps,
            channel_status,
            status_key="render_pbr_ok",
        )

    commit = getattr(render_material, "CommitChanges", None)
    if commit is not None:
        try:
            commit()
        except Exception:
            pass

    exists_in_doc = _render_material_exists_in_doc(render_material)
    if not exists_in_doc:
        try:
            sc.doc.RenderMaterials.Add(render_material)
        except Exception:
            # Some overloads return a document-owned material; add can throw in that case.
            if not _render_material_exists_in_doc(render_material):
                raise
    exists_in_doc = _render_material_exists_in_doc(render_material)
    if exists_in_doc:
        _record_imported_material_name(material_name)

    return render_material, {
        "factory_name": factory_name,
        "converted_ok": converted_ok,
        "selected_args": selected_args,
        "render_type": render_type,
        "is_physically_based_type": _is_render_material_physically_based(render_material, render_type),
    }


def _summarize_channel_status(texture_bitmaps, channel_status):
    detected = [key for key in _PBR_CHANNEL_KEYS if texture_bitmaps.get(key)]
    missing = [key for key in _PBR_CHANNEL_KEYS if not texture_bitmaps.get(key)]

    success = []
    failed = []
    for key in detected:
        status = channel_status.get(key, {})
        imported = any(
            status.get(flag_name) is True
            for flag_name in ("render_pbr_ok", "pbr_ok", "basic_ok")
        )
        if imported:
            success.append(key)
        else:
            failed.append(key)

    return {
        "detected": detected,
        "missing": missing,
        "success": success,
        "failed": failed,
    }


def create_material_from_texture_jpg(texture_jpg_path, material_name=None, add_to_doc=True):
    """Create one material from a base texture JPG and companion maps.

    Companion map examples:
      *_oc, *_ao, *_normal, *_roughness, *_metallic, *_height, *_opacity
    """
    texture_bitmaps = find_texture_bitmaps(texture_jpg_path)
    inferred_name = material_name or texture_bitmaps["root_stem"]
    material, channel_status = build_material_from_texture_bitmaps(texture_bitmaps, inferred_name)
    if not add_to_doc:
        return material

    imported_material, render_info = add_material_to_render_table(
        material,
        material_name=inferred_name,
        make_unique=True,
        texture_bitmaps=texture_bitmaps,
        channel_status=channel_status,
    )
    summary = _summarize_channel_status(texture_bitmaps, channel_status)
    print(
        "[PBR-SUMMARY] material='{}' seed='{}' render_type='{}' physically_based={} "
        "factory='{}' converted={} detected={} imported={} failed={} missing={}".format(
            inferred_name,
            os.path.basename(texture_jpg_path),
            render_info.get("render_type"),
            render_info.get("is_physically_based_type"),
            render_info.get("factory_name"),
            render_info.get("converted_ok"),
            summary["detected"],
            summary["success"],
            summary["failed"],
            summary["missing"],
        )
    )
    return imported_material


def _create_materials_from_single_texture_dir(texture_dir):
    """Create multiple materials from one texture directory (non-recursive)."""
    texture_dir = os.path.abspath(texture_dir)
    if not os.path.isdir(texture_dir):
        raise ValueError("Texture directory does not exist: {}".format(texture_dir))

    created = []
    seed_map = _collect_texture_seed_paths_single_dir(texture_dir)
    for key in sorted(seed_map.keys()):
        seed = seed_map[key]
        root_stem = seed["material_name"]
        texture_path = seed["texture_seed_path"]
        try:
            mat = create_material_from_texture_jpg(
                texture_path,
                material_name=root_stem,
                add_to_doc=True,
            )
            created.append(mat)
        except Exception as exc:
            print("Failed to create material from '{}': {}".format(texture_path, exc))

    print("Created {} materials from '{}'".format(len(created), texture_dir))
    return created


def create_materials_from_texture_dir(texture_dir, recursive=False):
    """Create materials from a texture directory.

    Args:
        texture_dir (str): Directory containing texture images and companion maps.
        recursive (bool): If True, walk subdirectories and process each one.
    """
    texture_dir = os.path.abspath(texture_dir)
    if not texture_dir:
        print("Texture material path is empty.")
        return []
    if not os.path.isdir(texture_dir):
        print("Texture material path does not exist: {}".format(texture_dir))
        return []

    if not recursive:
        return _create_materials_from_single_texture_dir(texture_dir)

    created = []
    for root, _, files in os.walk(texture_dir):
        if not any(os.path.splitext(name)[1].lower() in _IMAGE_EXTENSIONS for name in files):
            continue
        created.extend(_create_materials_from_single_texture_dir(root))

    print("Created {} materials under '{}' (recursive)".format(len(created), texture_dir))
    return created


def _get_table_count(table):
    try:
        return int(table.Count)
    except Exception:
        pass
    try:
        return len(table)
    except Exception:
        return 0


def _remove_table_item_at(table, index):
    item = None
    try:
        item = table[index]
    except Exception:
        pass

    attempts = [
        ("RemoveAt", (index,)),
        ("DeleteAt", (index,)),
        ("DeleteAt", (index, True)),
        ("Remove", (index,)),
        ("Delete", (index,)),
    ]
    if item is not None:
        attempts.extend(
            [
                ("Remove", (item,)),
                ("Delete", (item,)),
                ("Delete", (item, True)),
            ]
        )

    before_count = _get_table_count(table)
    for method_name, args in attempts:
        method = getattr(table, method_name, None)
        if method is None:
            continue
        try:
            result = method(*args)
        except Exception:
            continue
        if isinstance(result, bool) and not result:
            continue

        after_count = _get_table_count(table)
        if after_count < before_count:
            return True

        # Some Rhino table APIs return bool success without changing table count
        # immediately; accept explicit True as successful removal.
        if isinstance(result, bool) and result:
            return True

        if item is not None:
            try:
                current = table[index]
            except Exception:
                return True
            if current is not item:
                return True
    return False


def _to_index(value, default=-1):
    try:
        return int(value)
    except Exception:
        return default


def _render_material_name(material):
    return _normalize_material_name(
        getattr(material, "DisplayName", None) or getattr(material, "Name", None)
    ).lower()


def _basic_material_name(material):
    return _normalize_material_name(
        getattr(material, "Name", None) or getattr(material, "DisplayName", None)
    ).lower()


def _render_material_name_from_index(index):
    idx = _to_index(index, default=-1)
    if idx < 0:
        return ""
    try:
        return _render_material_name(sc.doc.RenderMaterials[idx])
    except Exception:
        return ""


def _basic_material_name_from_index(index):
    idx = _to_index(index, default=-1)
    if idx < 0:
        return ""
    try:
        return _basic_material_name(sc.doc.Materials[idx])
    except Exception:
        return ""


def _collect_table_names(table, name_getter):
    names = set()
    for idx in range(_get_table_count(table)):
        try:
            item = table[idx]
        except Exception:
            continue
        name = _normalize_material_name(name_getter(item)).lower()
        if name:
            names.add(name)
    return names


def _clear_layer_material_references(tracked_names=None):
    detached = 0
    for layer in sc.doc.Layers:
        try:
            if bool(getattr(layer, "IsDeleted", False)):
                continue
        except Exception:
            pass

        render_name = _render_material_name(getattr(layer, "RenderMaterial", None))
        if not render_name:
            render_name = _render_material_name_from_index(
                getattr(layer, "RenderMaterialIndex", -1)
            )
        render_idx = _to_index(getattr(layer, "RenderMaterialIndex", -1), default=-1)
        basic_idx = _to_index(getattr(layer, "MaterialIndex", -1), default=-1)
        basic_name = _basic_material_name_from_index(basic_idx)

        if tracked_names is not None:
            if render_name not in tracked_names and basic_name not in tracked_names:
                continue
        else:
            has_render_ref = bool(render_name) or render_idx >= 0
            has_basic_ref = bool(basic_name) or basic_idx >= 0
            if not (has_render_ref or has_basic_ref):
                continue

        changed = False
        for attr_name, value in (
            ("RenderMaterial", None),
            ("RenderMaterialIndex", -1),
            ("MaterialIndex", -1),
        ):
            if not hasattr(layer, attr_name):
                continue
            try:
                current = getattr(layer, attr_name)
                if current == value:
                    continue
            except Exception:
                pass
            try:
                setattr(layer, attr_name, value)
                changed = True
            except Exception:
                continue

        if not changed:
            continue

        commit = getattr(layer, "CommitChanges", None)
        if callable(commit):
            try:
                commit()
            except Exception:
                pass
        detached += 1
    return detached


def _clear_object_material_references(tracked_names=None):
    detached = 0
    try:
        material_from_layer = Rhino.DocObjects.ObjectMaterialSource.MaterialFromLayer
    except Exception:
        material_from_layer = None

    for rh_obj in sc.doc.Objects:
        try:
            attrs = rh_obj.Attributes.Duplicate()
        except Exception:
            continue

        material_idx = _to_index(getattr(attrs, "MaterialIndex", -1), default=-1)
        if tracked_names is not None:
            basic_name = _basic_material_name_from_index(material_idx)
            if basic_name not in tracked_names:
                continue
        else:
            if material_idx == -1:
                continue

        changed = False
        if hasattr(attrs, "MaterialIndex"):
            try:
                if _to_index(attrs.MaterialIndex, default=-1) != -1:
                    attrs.MaterialIndex = -1
                    changed = True
            except Exception:
                pass
        if material_from_layer is not None and hasattr(attrs, "MaterialSource"):
            try:
                if attrs.MaterialSource != material_from_layer:
                    attrs.MaterialSource = material_from_layer
                    changed = True
            except Exception:
                pass

        if not changed:
            continue
        try:
            sc.doc.Objects.ModifyAttributes(rh_obj, attrs, True)
            detached += 1
        except Exception:
            continue
    return detached


def _is_default_material(item):
    try:
        return bool(getattr(item, "IsDefaultMaterial", False))
    except Exception:
        return False


def clear_imported_materials_from_doc():
    """
    Remove non-default materials from current document tables.
    """
    removed = 0
    removed_render = 0
    removed_basic = 0

    detached_layers = _clear_layer_material_references(tracked_names=None)
    detached_objects = _clear_object_material_references(tracked_names=None)

    render_table = sc.doc.RenderMaterials
    for idx in range(_get_table_count(render_table) - 1, -1, -1):
        try:
            mat = render_table[idx]
        except Exception:
            continue
        if _is_default_material(mat):
            continue
        if _remove_table_item_at(render_table, idx):
            removed += 1
            removed_render += 1

    basic_table = sc.doc.Materials
    for idx in range(_get_table_count(basic_table) - 1, -1, -1):
        try:
            mat = basic_table[idx]
        except Exception:
            continue
        if idx == 0 or _is_default_material(mat):
            continue
        if _remove_table_item_at(basic_table, idx):
            removed += 1
            removed_basic += 1
    _set_tracked_material_names(set())

    print(
        "Material cleanup: detached {} layer refs, {} object refs, removed render {}, removed basic {}, total removed {}, remaining render {}, remaining basic {}.".format(
            detached_layers,
            detached_objects,
            removed_render,
            removed_basic,
            removed,
            _get_table_count(render_table),
            _get_table_count(basic_table),
        )
    )
    return removed
