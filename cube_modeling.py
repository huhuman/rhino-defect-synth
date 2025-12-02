#! python 3
import rhinoscriptsyntax as rs
import json
import os
import numpy as np

# CUBE_LENGTH is the distance from origin to each face.
# So the cube spans from -CUBE_LENGTH to +CUBE_LENGTH (edge length = 2 * CUBE_LENGTH).
CUBE_LENGTH = 500.0  # mm


# ----------------------------------------------------
# 1) Read JSON and convert pixel coords → mm
# ----------------------------------------------------
def read_contour_json(filepath):
    """
    Read one JSON file and return a list of contours:
    [
        {
            "parent": <whatever is in JSON>,
            "points": numpy array of shape (N, 2) in mm
        },
        ...
    ]
    Assumes JSON:
    {
        "pixel_size_cm": 0.1,
        "contours": [
            { "parent": "...", "points": [[x_px, y_px], ...] },
            ...
        ]
    }
    """
    if not os.path.isfile(filepath):
        raise IOError("File not found: {}".format(filepath))

    with open(filepath, "r") as f:
        data = json.load(f)

    try:
        # convert pixel size to mm
        pixel_size_mm = float(data["pixel_size_cm"]) * 10.0
    except KeyError:
        raise KeyError('JSON must contain "pixel_size_cm".')

    if "contours" not in data:
        raise KeyError('JSON must contain "contours".')

    contours = []
    for item in data["contours"]:
        pts_px = np.array(item["points"], dtype=float)  # shape (N, 2)
        pts_mm = pts_px * pixel_size_mm                # now in mm

        contours.append({
            "parent": item.get("parent", None),
            "points": pts_mm
        })

    return contours


# ----------------------------------------------------
# 2) Center 2D points
# ----------------------------------------------------
def center_2d_points(points_2d):
    """
    Center the 2D points around (0,0) using their bounding box center.
    points_2d: iterable of (x, y) (list or numpy array)
    Returns a list of (x, y) tuples.
    """
    centered = [(x - CUBE_LENGTH/2, y - CUBE_LENGTH/2) for (x, y) in points_2d]
    return centered


# ----------------------------------------------------
# 3) Map 2D mm coords onto a given cube face
# ----------------------------------------------------
def map_2d_to_cube_face(points_2d_mm, face):
    """
    Map 2D points (in mm) to 3D coordinates on a cube face.

    points_2d_mm : iterable of (x_mm, y_mm) in local 2D
    face         : one of "+x", "-x", "+y", "-y", "+z", "-z"

    Uses global CUBE_LENGTH as the face coordinate:
      +x face is at X = +CUBE_LENGTH, etc.

    Returns: list of (x, y, z) tuples.
    """
    face = face.strip().lower()
    pts_3d = []

    for (u, v) in points_2d_mm:
        if face in ["+x", "x+", "px", "posx"]:
            # Face at X = +CUBE_LENGTH, polygon lies in YZ-plane
            pts_3d.append((CUBE_LENGTH/2, u, v))
        elif face in ["-x", "x-", "nx", "negx"]:
            # Face at X = -CUBE_LENGTH
            pts_3d.append((-CUBE_LENGTH/2, u, v))
        elif face in ["+y", "y+", "py", "posy"]:
            # Face at Y = +CUBE_LENGTH, polygon lies in XZ-plane
            pts_3d.append((u, CUBE_LENGTH/2, v))
        elif face in ["-y", "y-", "ny", "negy"]:
            # Face at Y = -CUBE_LENGTH
            pts_3d.append((u, -CUBE_LENGTH/2, v))
        elif face in ["+z", "z+", "pz", "posz"]:
            # Face at Z = +CUBE_LENGTH, polygon lies in XY-plane
            pts_3d.append((u, v, CUBE_LENGTH/2))
        elif face in ["-z", "z-", "nz", "negz"]:
            # Face at Z = -CUBE_LENGTH
            pts_3d.append((u, v, -CUBE_LENGTH/2))
        else:
            raise ValueError('Unknown face "{}". Use +x, -x, +y, -y, +z, -z.'.format(face))
    return pts_3d


# ----------------------------------------------------
# 4) Create polyline in Rhino
# ----------------------------------------------------
def add_polygon_curve(points_3d, close_curve=True):
    """
    Create a polyline (and optionally close it) from 3D points in Rhino.
    Returns the GUID of the polyline object.
    """
    if not points_3d:
        return None

    pts = list(points_3d)
    if close_curve and pts[0] != pts[-1]:
        pts.append(pts[0])

    poly_id = rs.AddPolyline(pts)
    return poly_id


# ----------------------------------------------------
# 5) Main
# ----------------------------------------------------
def main():
    # Faces and their order
    faces = ["+x", "-x", "+y", "-y", "+z", "-z"]
    filter_str = "JSON files (*.json)|*.json||"

    start_folder = r"C:\Users\shh\Documents\ShunHsiangHsu\DefectSynthetic\rhino_modeling\defect_refs\crack_cube_maps"
    filenames = [e for e in os.listdir(start_folder) if e.endswith(".json")]
    filepaths = [os.path.join(start_folder, filename) for filename in filenames[:6]]

    created_ids = []

    # Process each file / face
    for face, filepath in zip(faces, filepaths):
        try:
            contours = read_contour_json(filepath)
        except Exception as e:
            rs.MessageBox(
                "Error reading JSON for face {}:\n{}\nFile: {}".format(face, e, filepath),
                0,
                "Error"
            )
            return

        for i, contour in enumerate(contours):
            # contour["points"] is a numpy array in mm, shape (N, 2)
            pts_mm = contour["points"]

            # Convert to plain Python list of tuples (for center function)
            pts_mm_list = [(float(p[0]), float(p[1])) for p in pts_mm]

            # Center each contour on its face
            pts_mm_centered = center_2d_points(pts_mm_list)

            # Map to cube face (3D)
            pts_3d = map_2d_to_cube_face(pts_mm_centered, face)

            # Create polyline in Rhino
            poly_id = add_polygon_curve(pts_3d, close_curve=True)
            if poly_id:
                created_ids.append(poly_id)
                rs.ObjectName(poly_id, "contour_{}_{}".format(face, i))

    print("Created {} contour polygon(s).".format(len(created_ids)))


# Run automatically when script is executed in Rhino
if __name__ == "__main__":
    main()
