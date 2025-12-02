import rhinoscriptsyntax as rs
import scriptcontext as sc
import Rhino
import random
import numpy as np
import itertools

''' Can do one time manually and that would be saved in the software'''
# def create_display_mode_for_mask():
#     mode_name = "Base Color"
#     mode_id = None
#     if mode_name in rs.ViewDisplayModes():
#         print(f"Display mode '{mode_name}' already exists.")
#         mode_id = rs.ViewDisplayModeId(mode_name)
#     else:
#         print(f"Creating display mode '{mode_name}'...")
#         mode_id = Rhino.Display.DisplayModeDescription.AddDisplayMode(mode_name)

#     mode = Rhino.Display.DisplayModeDescription.GetDisplayMode(mode_id)
#     visibility_attributes = [e for e in dir(mode.DisplayAttributes) if 'Show' in e]
#     for vis_attr in visibility_attributes:
#         setattr(mode.DisplayAttributes, vis_attr, False)

def point3d_to_string(point):
    # Convert Point3d to string in the format "(x, y, z)"
    return "({0}, {1}, {2})".format(point.X, point.Y, point.Z)

def get_box_coords_and_dims(xs, ys, zs, extend_ratio=0.25):
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    min_z, max_z = min(zs), max(zs)
    width, length, height = max_x - min_x, max_y - min_y, max_z - min_z
    max_x += width
    min_x -= width
    return (max_x, min_x, max_y, min_y, max_z, min_z), (width, length, height)

if __name__ == "__main__":
    xs, ys, zs = [], [], []
    for obj in Rhino.RhinoDoc.ActiveDoc.Objects:
        bbox = rs.BoundingBox(obj)
        xs += [pt.X for pt in bbox]
        ys += [pt.Y for pt in bbox]
        zs += [pt.Z for pt in bbox]
    bbox_coords, bbox_dims = get_box_coords_and_dims(xs, ys, zs)
    print(bbox_dims)

    delta_vectors = {
        'right': [bbox_dims[0]*0.25, 0, 0],
        'left': [-bbox_dims[0]*0.25, 0, 0],
        'front': [0, bbox_dims[1]*0.25, 0],
        'back': [0, -bbox_dims[1]*0.25, 0],
        'top': [0, 0, bbox_dims[2]*0.25],
        'bottom': [0, 0, -bbox_dims[2]*0.25]
    }

    max_x, min_x, max_y, min_y, max_z, min_z = bbox_coords
    # cam_xs = np.linspace(min_x, max_x, 3)
    # cam_ys = np.linspace(min_y, max_y, 10)
    # cam_zs = np.linspace(min_z, max_z, 3)
    # print([float(e) for e in cam_locations[0]])
    # for d_name, d_vec in delta_vectors.items():
    #     target = [cam_locations[0][i] + d_vec[i] for i in range(3)]
    #     print(d_name, [float(e) for e in target])

    # cam_locations = list(itertools.product(cam_xs, cam_ys, cam_zs))
    # for img_id, cam_location in enumerate(cam_locations):
    #     for d_name, d_vec in delta_vectors.items():
    #         filepath = r"C:\\Users\shh\Desktop\test\\" + f"{img_id:03d}_{d_name}"
    #         target = [cam_location[i] + d_vec[i] for i in range(3)]
    #         rs.ViewCameraTarget(camera=cam_location, target=target)
    #         rs.Command(f"-ViewCaptureToFile {filepath}.png _Enter", echo=False)
    #         break

    # img_id = 0
    # for x in cam_xs:
    #     for z in cam_zs:
    #         for y in cam_ys:
    #             cam_location = [x, y, z]
    #             for d_name, d_vec in delta_vectors.items():
    #                 filepath = r"C:\\Users\shh\Desktop\test\\" + f"{img_id:03d}_{d_name}"
    #                 target = [cam_location[i] + d_vec[i] for i in range(3)]
    #                 rs.ViewCameraTarget(camera=cam_location, target=target)
    #                 rs.Command(f"-ViewCaptureToFile {filepath}.png _Enter", echo=False)
    #                 img_id += 1
    #                 break

    # image metadata
    width, height = 1442, 879
    bridge_center = [
        (bbox_coords[0]+bbox_coords[1])/2, (bbox_coords[2]+bbox_coords[3])/2, (bbox_coords[4]+bbox_coords[5])/2
    ]
    rs.ViewTarget(target=bridge_center)
    for idx in range(20):
        rs.RotateView(angle=float(18))
        filepath = r"C:\\Users\shh\Desktop\test\\" + f"{idx:03d}"
        command = "-ViewCaptureToFile " + filepath + " _Enter"
        rs.Command(f"-ViewCaptureToFile {filepath}.png _Enter", echo=False)
        rs.Command("-ShowZBuffer _Enter", echo=False)
        rs.Command(f"-ViewCaptureToFile {filepath}_depth.png _Enter", echo=False)
        rs.Command("-ShowZBuffer _Enter", echo=False)
        rs.Command("-TestShowNormalMap _Enter", echo=False)
        rs.Command(f"-ViewCaptureToFile {filepath}_normal.png _Enter", echo=False)
        rs.Command("-TestShowNormalMap _Enter", echo=False)
        rs.Command("-SetDisplayMode _BaseColor _Enter", echo=False) # select single object color only
        rs.Command(f"-ViewCaptureToFile {filepath}_mask.png _Enter", echo=False)
        rs.Command("-SetDisplayMode _Rendered _Enter", echo=False)
    
    # rs.Command("!_Render", echo=False)
    # rs.Command("-_SaveRenderWindowAs " + filepath)
