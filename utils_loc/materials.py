#! python3
import Rhino
import scriptcontext as sc
import rhinoscriptsyntax as rs
import os
import re


_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".tga", ".exr")
_MAP_SUFFIXES = {
    "albedo": ("ao",),
    "normal": ("normal", "norm", "nrm", "nor", "normal_opengl", "normal_directx"),
    "occlusion": ("ao", "oc", "ambientocclusion", "ambient_occlusion", "occlusion"),
    "roughness": ("roughness", "rough", "rgh"),
    "metallic": ("metallic", "metalness", "metal"),
    "height": ("height", "displacement", "disp", "bump"),
    "opacity": ("opacity", "alpha", "mask", "transparency"),
    "specular": ("specular", "spec", "glossiness", "gloss"),
}
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
_ALL_SUFFIXES = tuple(
    suffix for suffixes in _MAP_SUFFIXES.values() for suffix in suffixes
)
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


def import_materials(category="Architectural", subcategory1="Wall", subcategory2="Concrete"):
    all_render_materials = _get_render_material_names()
    user_root = os.path.expanduser("~")
    material_root = os.path.join(user_root, "AppData", "Roaming", "McNeel", "Rhinoceros", "8.0", "Localization", "en-US", "Render Content", category, subcategory1, subcategory2)
    if not os.path.exists(material_root):
        print(f'Material path does not exist: {material_root}')
        return
    for filename in os.listdir(material_root):
        if filename[:-5] not in all_render_materials:
            filepath = os.path.join(material_root, filename)
            Rhino.Render.RenderMaterial.ImportMaterialAndAssignToLayers(sc.doc, filepath, [])
            print(f'Importing material: {filename[:-5]}')


def _guess_map_kind_from_stem(stem):
    stem_lc = stem.lower()
    for kind, suffixes in _MAP_SUFFIXES.items():
        for suffix in suffixes:
            if stem_lc.endswith("_" + suffix):
                return kind
    return None


def _strip_known_suffix(stem):
    stem_lc = stem.lower()
    for suffix in sorted(_ALL_SUFFIXES, key=len, reverse=True):
        token = "_" + suffix
        if stem_lc.endswith(token):
            return stem[:-len(token)]
    return stem


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


def _expand_token_variants(token):
    token = (token or "").lower()
    variants = {token}
    if "_" in token:
        variants.add(token.replace("_", "-"))
    if "-" in token:
        variants.add(token.replace("-", "_"))
    return tuple(sorted((v for v in variants if v), key=len, reverse=True))


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


def _stem_contains_color_token(stem):
    if not stem:
        return False
    stem_lc = stem.lower()
    for color_token in _MAP_SUFFIXES["albedo"]:
        for token_variant in _expand_token_variants(color_token):
            if token_variant in stem_lc:
                return True
    return False


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

    if albedo:
        try:
            material.SetBitmapTexture(albedo)
        except Exception:
            pass
    if normal:
        try:
            material.SetBumpTexture(normal)
        except Exception:
            pass
    if opacity:
        try:
            material.SetTransparencyTexture(opacity)
        except Exception:
            pass

    for key, type_names in _PBR_ENUM_CANDIDATES.items():
        texture_path = texture_bitmaps.get(key)
        if texture_path:
            _set_texture_by_type(material, texture_path, type_names)

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
        return render_material
    except Exception:
        # Fallback for Rhino versions where RenderMaterial creation differs.
        material.Name = material_name
        sc.doc.Materials.Add(material)
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
    processed_roots = set()
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
        # Backward compatibility for texture sets without explicit "color" token.
        candidate_files = [
            name
            for name in all_files
            if os.path.splitext(name)[1].lower() in (".jpg", ".jpeg")
        ]

    for filename in candidate_files:
        stem, ext = os.path.splitext(filename)
        if ext.lower() not in _IMAGE_EXTENSIONS:
            continue
        kind = _guess_map_kind_from_stem(stem)
        if kind and kind != "albedo":
            continue

        root_stem = _strip_known_suffix(stem).lower()
        if root_stem in processed_roots:
            continue
        processed_roots.add(root_stem)

        texture_path = os.path.join(texture_dir, filename)
        try:
            mat = create_material_from_texture_jpg(texture_path, material_name=_strip_known_suffix(stem), add_to_doc=True)
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
