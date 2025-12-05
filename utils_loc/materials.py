#! python3
import System
import Rhino
import scriptcontext as sc
import rhinoscriptsyntax as rs
import os
import time

# Last update by 2025/02/13
Vray_Material_Metadata = {
    "/Rubber Rough 001": ["1712823064", "cc427c2f-b935-412f-85a2-e3e521608178"],
    "/Iron Rough Rusty": ["1712822095", "82bd9e0c-bf27-4e59-956d-701daa7b4750"],
    "/Concrete Weathered 300cm": ["1712821438", "08ac88c2-bb86-4ba4-ac47-8622ad8be5e9"],
    "/Concrete Simple 001 300cm": ["1712821428", "7a028bb4-a297-4e2b-b85d-b3e5dbb5a2e9"],
    "/Concrete Simple B01 200cm": ["1667490677", "79ba2bea-7908-470f-b6c0-3c9ee9f3d174"],
    "/Concrete Simple C01 200cm": ["1667490683", "e03680fe-36bb-4504-9b76-a650d9ee83ed"],
    "/Concrete Simple E02 400cm": ["1667490753", "50226111-aae5-4b14-8bac-7f6c4fae51a8"],
    "/Concrete Simple F01 200cm": ["1667490764", "ce5ed96c-5b12-4478-8018-95b9899de5d1"],
    "/Concrete Simple G01 400cm": ["1667490775", "64c6d606-08b5-45b1-bcd3-df4ac8727721"],
    "/Concrete Floor Satin 300cm": ["1712821423", "786f9bed-d9fe-4563-8b93-9fef469e3473"],
    "/Concrete Grey 03 100cm": ["1639468414", "c6515288-34c2-49d8-b32e-1a5eb30c5c4b"],
    "/Concrete Grey 06 100cm": ["1639468424", "d83b8f99-c819-4ad2-83e5-0a921279af79"],
}
All_Render_Materials = [mat.DisplayName for mat in sc.doc.RenderMaterials]


def import_Vray_materials():
    for mat, info in Vray_Material_Metadata.items():
        is_exist = False
        for render_mat in All_Render_Materials:
            if mat in render_mat:
                is_exist = True
                break
        if not is_exist:
            print(f'Importing material: {mat}')
            rs.Command(f"-_vrayCosmos _Import _Revision={info[0]} _Triplanar=On {info[1]}")


def import_materials(category="Architectural", subcategory1="Wall", subcategory2="Concrete"):
    user_root = os.path.expanduser("~")
    material_root = os.path.join(user_root, "AppData", "Roaming", "McNeel", "Rhinoceros", "8.0", "Localization", "en-US", "Render Content", category, subcategory1, subcategory2)
    if not os.path.exists(material_root):
        print(f'Material path does not exist: {material_root}')
        return
    for filename in os.listdir(material_root):
        if filename[:-5] not in All_Render_Materials:
            filepath = os.path.join(material_root, filename)
            Rhino.Render.RenderMaterial.ImportMaterialAndAssignToLayers(sc.doc, filepath, [])
            print(f'Importing material: {filename[:-5]}')
