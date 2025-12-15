#! python 3
import Rhino
import scriptcontext as sc
import rhinoscriptsyntax as rs
import json
import os
import random
import numpy as np
import random

# CUBE_LENGTH is the distance from origin to each face.
# So the cube spans from -CUBE_LENGTH to +CUBE_LENGTH (edge length = 2 * CUBE_LENGTH).
CUBE_LENGTH = 500.0  # mm


def __create_cube_faces():
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

    return faces


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
        "severity": "CS1" | "CS2" | "CS3"
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
    
    global CUBE_LENGTH
    try:
        map_pixel = float(data.get("width_px", 0) or data.get("height_px", 0))
        if map_pixel == 0:
            raise ValueError("Both width_px and height_px are missing or zero")
    except (ValueError, TypeError):
        raise KeyError('JSON must contain valid "width_px" or "height_px".')
    CUBE_LENGTH = map_pixel * pixel_size_mm

    if "contours" not in data:
        raise KeyError('JSON must contain "contours".')

    def _contour_conversion(item):
        pts_px = np.array(item["points"], dtype=float)
        pts_mm = pts_px * pixel_size_mm
        return {
            "parent": item["parent"],
            "points": pts_mm
        }

    contours = [[_contour_conversion(item) for item in cnt_list] for cnt_list in data["contours"]]
    severities = data["severities"]
    erode_contours = [_contour_conversion(item) for item in data["expanded_contours"]]
    base_contours = [_contour_conversion(item) for item in data["base_contours"]]
    diff_contours = [[_contour_conversion(item) for item in cnt_list] for cnt_list in data["difference_contours"]]

    return contours, base_contours, erode_contours, diff_contours, severities


def center_2d_points(points_2d):
    """
    Center the 2D points around (0,0) using their bounding box center.
    points_2d: iterable of (x, y) (list or numpy array)
    Returns a list of (x, y) tuples.
    """
    centered = [(x - CUBE_LENGTH/2, y - CUBE_LENGTH/2) for (x, y) in points_2d]
    return centered


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
        if face == "+x":
            pts_3d.append((CUBE_LENGTH/2, u, v))
        elif face == "-x":
            pts_3d.append((-CUBE_LENGTH/2, u, v))
        elif face == "+y":
            pts_3d.append((u, CUBE_LENGTH/2, v))
        elif face == "-y":
            pts_3d.append((u, -CUBE_LENGTH/2, v))
        elif face == "+z":
            pts_3d.append((u, v, CUBE_LENGTH/2))
        elif face == "-z":
            pts_3d.append((u, v, -CUBE_LENGTH/2))
        else:
            raise ValueError('Unknown face "{}". Use +x, -x, +y, -y, +z, -z.'.format(face))
    return pts_3d


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


def face_dir_normal(face_dir):
    """Return a unit normal vector for a cube face direction string."""
    mapping = {
        "+x": (1, 0, 0),
        "-x": (-1, 0, 0),
        "+y": (0, 1, 0),
        "-y": (0, -1, 0),
        "+z": (0, 0, 1),
        "-z": (0, 0, -1),
    }
    return mapping.get(face_dir.lower())


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

    # Delete original base surface
    rs.DeleteObject(base_srf_id)

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


def create_cube(cube_map_dir, start_face_index=0):
    faces = __create_cube_faces()

    filenames = sorted([e for e in os.listdir(cube_map_dir) if e.endswith(".json")])
    filepaths = [os.path.join(cube_map_dir, filename) for filename in filenames[start_face_index:start_face_index+6]]

    # Process each file / face
    face_cracks = {}
    for face_dir, filepath in zip(faces.keys(), filepaths):
        face = faces[face_dir]
        rs.HideObject(face)

        contours, base_contours, erode_contours, diff_contours, severities = read_contour_json(filepath)

        assert len(contours) == len(severities) == len(erode_contours) == len(base_contours) == len(diff_contours), \
            f"Mismatch in number of contours ({len(contours)}), severities ({len(severities)}), erode contours ({len(erode_contours)}), base contours ({len(base_contours)}), or diff contours ({len(diff_contours)})."
        n_bases = len(erode_contours)
        cutters = []
        crack_items = []
        
        for i in range(n_bases):
            erode_pts_mm_centered = center_2d_points(erode_contours[i]["points"])
            erode_pts_3d = map_2d_to_cube_face(erode_pts_mm_centered, face_dir)
            erode_poly_id = add_polygon_curve(erode_pts_3d, close_curve=True)
            if not erode_poly_id:
                print("Skipping empty erode contour at index {} on face {}".format(i, face_dir))
                continue
            
            base_pts_mm_centered = center_2d_points(base_contours[i]["points"])
            base_pts_3d = map_2d_to_cube_face(base_pts_mm_centered, face_dir)
            base_poly_id = add_polygon_curve(base_pts_3d, close_curve=True)
            if not base_poly_id:
                print("Skipping empty base contour at index {} on face {}".format(i, face_dir))
                continue
            
            diff_poly_ids = []
            for diff_cnt in diff_contours[i]:
                diff_pts_mm_centered = center_2d_points(diff_cnt["points"])
                diff_pts_3d = map_2d_to_cube_face(diff_pts_mm_centered, face_dir)
                diff_poly_id = add_polygon_curve(diff_pts_3d, close_curve=True)
                if diff_poly_id:
                    diff_poly_ids.append(diff_poly_id)

            severity = severities[i]
            layer_name = "crack_{}".format(severity)
            if not rs.IsLayer(layer_name):
                raise ValueError("Layer '{}' does not exist. Please run preparation step first.".format(layer_name))

            crack_poly_ids = []
            noncrack_poly_ids = []
            for contour in contours[i]:
                pts_mm_centered = center_2d_points(contour["points"])
                pts_3d = map_2d_to_cube_face(pts_mm_centered, face_dir)
                if len(pts_3d) < 3:
                    continue
                poly_id = add_polygon_curve(pts_3d, close_curve=True)
                if poly_id:
                    if contour["parent"] != -1:
                        noncrack_poly_ids.append(poly_id)
                    else:
                        crack_poly_ids.append(poly_id)

            if not crack_poly_ids:
                print("Skipping empty contour at index {} on face {}".format(i, face_dir))
                continue
            
            for poly_id in crack_poly_ids:
                rs.ObjectLayer(poly_id, layer_name)
            rs.ObjectLayer(erode_poly_id, layer_name)
            offset_srf_id = rs.AddPlanarSrf(erode_poly_id)[0]
            if offset_srf_id:
                rs.ObjectLayer(offset_srf_id, layer_name)
                cutters.append(offset_srf_id)
                crack_items.append({
                    "offset_surface": offset_srf_id,
                    "crack_polys": crack_poly_ids,
                    "inside_polys": noncrack_poly_ids,
                    "base_poly": base_poly_id,
                    "offset_poly": erode_poly_id,
                    "diff_polys": diff_poly_ids,
                })

        split_face_and_keep_outer(face, cutters)
        rs.ShowObject(face)
        face_cracks[face_dir] = crack_items
        print("Processed face {}: {} contours.".format(face_dir, len(cutters)))
    
    for face in faces.values():
        if rs.IsObject(face):
            rs.DeleteObject(face)

    return face_cracks
