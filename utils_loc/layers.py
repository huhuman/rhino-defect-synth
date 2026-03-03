import System
from typing import Optional
import scriptcontext as sc
import rhinoscriptsyntax as rs


def __color_from_name(name: str) -> System.Drawing.Color:
    color = getattr(System.Drawing.Color, name, None)
    if color is None:
        raise ValueError(f"Unknown color: {name}")
    return color


def create_single_layer(name, color):
    """Create or get a single layer by name and color.
    Args:
        name: layer name
        color: System.Drawing.Color
    Returns:
        the created or existing layer
    """
    if isinstance(color, str):
        color = __color_from_name(color)
    layer_index = sc.doc.Layers.FindByFullPath(name, True)
    if layer_index >= 0:
        return sc.doc.Layers[layer_index]

    parts = [part.strip() for part in str(name).split("::") if part.strip()]
    if not parts:
        raise ValueError("Layer name cannot be empty.")

    parent = None
    current_full = None
    for idx, part in enumerate(parts):
        current_full = part if parent is None else "{}::{}".format(parent, part)
        existing = sc.doc.Layers.FindByFullPath(current_full, True)
        if existing >= 0:
            parent = current_full
            continue
        if idx == len(parts) - 1:
            created = rs.AddLayer(name=part, color=color, parent=parent)
        else:
            created = rs.AddLayer(name=part, parent=parent)
        parent = created or current_full

    final_index = sc.doc.Layers.FindByFullPath(current_full, True)
    if final_index < 0:
        raise ValueError("Failed to create layer '{}'".format(name))
    return sc.doc.Layers[final_index]


def create_layers(
    layer_color_dict,
    layer_material_dict: Optional[dict] = None,
):
    """Create layers for each component with specified material and color.
    Args:
        component_material_dict: dict mapping component names to material names
        component_color_dict: dict mapping component names to System.Drawing.Color
    """
    if not layer_color_dict:
        raise ValueError("Layer color dictionary is required to create layers.")

    render_materials = [mat.DisplayName for mat in sc.doc.RenderMaterials]

    currnt_layer = rs.CurrentLayer()
    existing_layers = []
    for layer in sc.doc.Layers:
        if not layer.Name:
            continue
        if layer.Name == currnt_layer:
            continue
        existing_layers.append(layer.FullPath or layer.Name)
    existing_layers.sort(key=lambda name: str(name).count("::"), reverse=True)
    for layer_name in existing_layers:
        objects = rs.ObjectsByLayer(layer_name)
        if objects:
            rs.DeleteObjects(objects)  # Delete all objects on the layer
        if rs.IsLayer(layer_name):
            rs.DeleteLayer(layer_name)

    for layer_name, color in layer_color_dict.items():
        layer = create_single_layer(layer_name, color)
        if layer_material_dict and layer_name in layer_material_dict:
            layer.RenderMaterial = sc.doc.RenderMaterials[render_materials.index(layer_material_dict[layer_name])]

    first_layer = list(layer_color_dict.keys())[0]
    first_layer_index = sc.doc.Layers.FindByFullPath(first_layer, True)
    if first_layer_index >= 0:
        sc.doc.Layers.SetCurrentLayerIndex(first_layer_index, True)
    rs.DeleteLayer(currnt_layer)
