import System
import Rhino
import scriptcontext as sc
import rhinoscriptsyntax as rs

import os
import time

component_material_dict = {
    "slab": "/Concrete Weathered 300cm",
    "beam": "Concrete light",
    "parapet": "/Concrete Weathered 300cm",
    "bearing": "/Rubber Rough 001",
    "pier": "/Concrete Simple G01 400cm"
}

component_color_dict = {
    "slab": System.Drawing.Color.Red,
    "beam": System.Drawing.Color.Blue,
    "parapet": System.Drawing.Color.Green,
    "bearing": System.Drawing.Color.AliceBlue,
    "pier": System.Drawing.Color.Brown
}

def create_single_layer(name):
    color = component_color_dict[name]
    layer = sc.doc.Layers.FindByFullPath(name, True)
    if layer>=0: return sc.doc.Layers[layer]
    layer_index = sc.doc.Layers.Add(name, color)
    layer = sc.doc.Layers[layer_index]
    return layer


def create_layers():
    render_materials = [mat.DisplayName for mat in sc.doc.RenderMaterials]
    for layer in sc.doc.Layers:
        if layer.Name and layer != rs.CurrentLayer():
            objects = rs.ObjectsByLayer(layer.Name)
            if objects:
                rs.DeleteObjects(objects)  # Delete all objects on the layer
            rs.DeleteLayer(layer.Name)

    for comp, mat in component_material_dict.items():
        layer = create_single_layer(comp)
        layer.RenderMaterial = sc.doc.RenderMaterials[render_materials.index(mat)]


if __name__ == '__main__':
    create_layers()