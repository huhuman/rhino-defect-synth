#! python 3
import Rhino
import scriptcontext as sc
import rhinoscriptsyntax as rs
import json
import os
import numpy as np

def create_cube_faces():
    """
    Create a cube from -CUBE_LENGTH to +CUBE_LENGTH in all axes,
    explode it into 6 surfaces, and return a dict mapping
    { "+x": id, "-x": id, "+y": id, "-y": id, "+z": id, "-z": id }.
    All returned ids are surfaces (Brep).
    """
    half = CUBE_LENGTH / 2

    corners = [
        (-half, -half, -half),
        ( half, -half, -half),
        ( half,  half, -half),
        (-half,  half, -half),
        (-half, -half,  half),
        ( half, -half,  half),
        ( half,  half,  half),
        (-half,  half,  half),
    ]

    box_id = rs.AddBox(corners)  # polysurface (type 32)
    srfs = rs.ExplodePolysurfaces(box_id, delete_input=True)  # 6 surfaces (type 16)

    faces = {}
    tol = 1e-3

    for s in srfs:
        bbox = rs.BoundingBox(s)
        if not bbox:
            continue
        cx = sum(p[0] for p in bbox) / 8.0
        cy = sum(p[1] for p in bbox) / 8.0
        cz = sum(p[2] for p in bbox) / 8.0

        if abs(cx - half) < tol:
            faces["+x"] = s
        elif abs(cx + half) < tol:
            faces["-x"] = s
        elif abs(cy - half) < tol:
            faces["+y"] = s
        elif abs(cy + half) < tol:
            faces["-y"] = s
        elif abs(cz - half) < tol:
            faces["+z"] = s
        elif abs(cz + half) < tol:
            faces["-z"] = s

    # sanity check
    for name, gid in faces.items():
        print("Face", name,
              "id:", gid,
              "exists:", rs.IsObject(gid),
              "type:", rs.ObjectType(gid))

    return faces



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


def split_face_and_keep_outer(base_srf_id, cutters):
    """
    Use RhinoCommon Brep.Split with *all* cutters at once,
    then keep only the largest area piece (outer face).
    base_srf_id : GUID of the face surface to split
    cutters     : list of GUIDs (planar surfaces on the same plane)
    """
    if not cutters:
        return base_srf_id

    tol = sc.doc.ModelAbsoluteTolerance

    # Coerce base surface to Brep
    base_brep = rs.coercebrep(base_srf_id)
    if not base_brep:
        print("Base surface is not a valid Brep:", base_srf_id)
        return base_srf_id

    # Coerce all cutters to Breps
    cutter_breps = []
    for cid in cutters:
        if not cid:
            continue
        b = rs.coercebrep(cid)
        if b:
            cutter_breps.append(b)
        else:
            print("Skipping non-brep cutter:", cid, "type:", rs.ObjectType(cid))

    if not cutter_breps:
        print("No valid cutter Breps found.")
        return base_srf_id

    # Do a single split in RhinoCommon
    split_breps = base_brep.Split(cutter_breps, tol)
    if not split_breps:
        print("Brep.Split failed or produced no pieces.")
        return base_srf_id

    # Add split pieces to the document
    piece_ids = []
    for b in split_breps:
        pid = sc.doc.Objects.AddBrep(b)
        piece_ids.append(pid)

    # Delete original base surface and cutters
    rs.DeleteObject(base_srf_id)
    for cid in cutters:
        if rs.IsObject(cid):
            rs.DeleteObject(cid)

    # Compute areas and keep largest
    areas = []
    for pid in piece_ids:
        brep = rs.coercebrep(pid)
        if not brep:
            areas.append(0.0)
            continue
        amp = Rhino.Geometry.AreaMassProperties.Compute(brep)
        areas.append(amp.Area if amp else 0.0)

    if not areas:
        print("No valid areas from split pieces.")
        return None

    idx_max = areas.index(max(areas))
    outer_id = piece_ids[idx_max]

    # Delete all smaller pieces
    for i, pid in enumerate(piece_ids):
        if i != idx_max and rs.IsObject(pid):
            rs.DeleteObject(pid)

    return outer_id


# ----------------------------------------------------
# 5) Main
# ----------------------------------------------------
def main():
    faces = create_cube_faces()

    start_folder = r"C:\Users\shh\Documents\ShunHsiangHsu\DefectSynthetic\rhino_modeling\defect_refs\crack_cube_maps"
    filenames = [e for e in os.listdir(start_folder) if e.endswith(".json")]
    filepaths = [os.path.join(start_folder, filename) for filename in filenames[:6]]

    created_ids = []
    # Process each file / face
    for face_dir, filepath in zip(faces.keys(), filepaths):
        face = faces[face_dir]

        try:
            contours = read_contour_json(filepath)
        except Exception as e:
            rs.MessageBox(
                "Error reading JSON for face {}:\n{}\nFile: {}".format(face_dir, e, filepath),
                0,
                "Error"
            )
            return

        cutters = []
        for i, contour in enumerate(contours):
            # contour["points"] is a numpy array in mm, shape (N, 2)
            pts_mm = contour["points"]

            # Convert to plain Python list of tuples (for center function)
            pts_mm_list = [(float(p[0]), float(p[1])) for p in pts_mm]

            # Center each contour on its face
            pts_mm_centered = center_2d_points(pts_mm_list)

            # Map to cube face (3D)
            pts_3d = map_2d_to_cube_face(pts_mm_centered, face_dir)

            # Create polyline in Rhino
            poly_id = add_polygon_curve(pts_3d, close_curve=True)
            if poly_id:
                created_ids.append(poly_id)
                rs.ObjectName(poly_id, "contour_{}_{}".format(face_dir, i))
                
                # planar surface from polyline (the cutter)
                srf_ids = rs.AddPlanarSrf(poly_id)
                if srf_ids:
                    cutters.extend(srf_ids)
            # clean up
            rs.DeleteObject(poly_id)

        split_face_and_keep_outer(face, cutters)

    print("Created {} contour polygon(s).".format(len(created_ids)))


# Run automatically when script is executed in Rhino
if __name__ == "__main__":
    main()
