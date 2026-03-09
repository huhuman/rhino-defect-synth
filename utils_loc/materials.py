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
    "albedo": ("PBR_BaseColor", "Diffuse", "Bitmap"),
    "normal": ("PBR_Normal", "Bump"),
    "roughness": ("PBR_Roughness",),
    "occlusion": ("PBR_AmbientOcclusion",),
    "metallic": ("PBR_Metallic",),
    "height": ("PBR_Displacement", "Displacement"),
    "opacity": ("PBR_Opacity", "Transparency"),
    "specular": ("PBR_Specular",),
}

_DEFAULT_BUILTIN_CATEGORY = "Architectural"
_DEFAULT_BUILTIN_SUBCATEGORY1 = "Wall"
_DEFAULT_BUILTIN_SUBCATEGORY2 = "Concrete"
_IMPORTED_RENDER_MATERIAL_NAMES = set()

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


def _get_render_material_names():
    return [mat.DisplayName for mat in sc.doc.RenderMaterials]


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
        _IMPORTED_RENDER_MATERIAL_NAMES.add(normalized.lower())


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

        selected[str(layer_name)] = rng.choice(available)

    return selected


def _ensure_single_material(selection):
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
    for suffix in suffixes:
        key = "{}_{}".format(root_key, suffix)
        if key in image_index:
            return image_index[key]
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


def _set_texture_by_type(material, texture_path, type_names):
    if not texture_path:
        return False

    texture = Rhino.DocObjects.Texture()
    texture.FileName = texture_path
    texture.Enabled = True

    for type_name in type_names:
        texture_type = _get_texture_type(type_name)
        if texture_type is None:
            continue
        try:
            if material.SetTexture(texture, texture_type):
                return True
        except Exception:
            continue
    return False


def build_material_from_texture_bitmaps(texture_bitmaps, material_name):
    """Create a Rhino material from discovered texture bitmaps."""
    material = Rhino.DocObjects.Material()
    material.Name = material_name

    albedo = texture_bitmaps.get("albedo")
    normal = texture_bitmaps.get("normal")
    opacity = texture_bitmaps.get("opacity")
    channel_status = {}

    if albedo:
        basic_ok = False
        try:
            material.SetBitmapTexture(albedo)
            basic_ok = True
        except Exception:
            pass
        channel_status["albedo"] = {"basic_ok": basic_ok}
    if normal:
        basic_ok = False
        try:
            material.SetBumpTexture(normal)
            basic_ok = True
        except Exception:
            pass
        channel_status["normal"] = {"basic_ok": basic_ok}
    if opacity:
        basic_ok = False
        try:
            material.SetTransparencyTexture(opacity)
            basic_ok = True
        except Exception:
            pass
        channel_status["opacity"] = {"basic_ok": basic_ok}

    for key, type_names in _PBR_ENUM_CANDIDATES.items():
        texture_path = texture_bitmaps.get(key)
        if texture_path:
            pbr_ok = _set_texture_by_type(material, texture_path, type_names)
            if key not in channel_status:
                channel_status[key] = {"basic_ok": None}
            channel_status[key]["pbr_ok"] = pbr_ok

    for key in _PBR_CHANNEL_KEYS:
        texture_path = texture_bitmaps.get(key)
        if not texture_path:
            continue
        status = channel_status.get(key, {})
        basic_ok = status.get("basic_ok", None)
        pbr_ok = status.get("pbr_ok", None)
        print(
            "[PBR] material='{}' channel='{}' file='{}' basic_ok={} pbr_ok={}".format(
                material_name,
                key,
                os.path.basename(texture_path),
                basic_ok,
                pbr_ok,
            )
        )

    return material


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


def add_material_to_render_table(material, material_name=None, make_unique=True):
    """Add a Rhino material into the current document render material table."""
    material_name = material_name or material.Name or "material_from_texture"
    if make_unique:
        material_name = _make_unique_material_name(material_name)

    try:
        render_material = Rhino.Render.RenderMaterial.CreateBasicMaterial(material, sc.doc)
        render_material.Name = material_name
        sc.doc.RenderMaterials.Add(render_material)
        _record_imported_material_name(material_name)
        return render_material
    except Exception:
        # Fallback for Rhino versions where RenderMaterial creation differs.
        material.Name = material_name
        sc.doc.Materials.Add(material)
        _record_imported_material_name(material_name)
        return material


def create_material_from_texture_jpg(texture_jpg_path, material_name=None, add_to_doc=True):
    """Create one material from a base texture JPG and companion maps.

    Companion map examples:
      *_oc, *_ao, *_normal, *_roughness, *_metallic, *_height, *_opacity
    """
    texture_bitmaps = find_texture_bitmaps(texture_jpg_path)
    inferred_name = material_name or texture_bitmaps["root_stem"]
    material = build_material_from_texture_bitmaps(texture_bitmaps, inferred_name)
    if not add_to_doc:
        return material
    return add_material_to_render_table(material, material_name=inferred_name, make_unique=True)


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

    for method_name, args in attempts:
        method = getattr(table, method_name, None)
        if method is None:
            continue
        try:
            method(*args)
            return True
        except Exception:
            continue
    return False


def clear_imported_materials_from_doc():
    """
    Remove materials that were imported/created by this pipeline session.
    """
    if not _IMPORTED_RENDER_MATERIAL_NAMES:
        return 0

    removed = 0
    tracked = set(_IMPORTED_RENDER_MATERIAL_NAMES)

    render_table = sc.doc.RenderMaterials
    for idx in range(_get_table_count(render_table) - 1, -1, -1):
        try:
            mat = render_table[idx]
        except Exception:
            continue
        name = _normalize_material_name(
            getattr(mat, "DisplayName", None) or getattr(mat, "Name", None)
        ).lower()
        if not name or name not in tracked:
            continue
        if _remove_table_item_at(render_table, idx):
            removed += 1

    basic_table = sc.doc.Materials
    for idx in range(_get_table_count(basic_table) - 1, -1, -1):
        try:
            mat = basic_table[idx]
        except Exception:
            continue
        name = _normalize_material_name(getattr(mat, "Name", None)).lower()
        if not name or name not in tracked:
            continue
        if _remove_table_item_at(basic_table, idx):
            removed += 1

    _IMPORTED_RENDER_MATERIAL_NAMES.clear()
    if removed:
        print("Removed {} imported material entries during reset.".format(removed))
    return removed
