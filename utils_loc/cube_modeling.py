#! python 3
import Rhino
import scriptcontext as sc
import rhinoscriptsyntax as rs
import os
import random

from utils_loc.defect_shapes import read_cube_contour_json

# CUBE_LENGTH is the distance from origin to each face.
# So the cube spans from -CUBE_LENGTH to +CUBE_LENGTH (edge length = 2 * CUBE_LENGTH).
CUBE_LENGTH = 500.0  # mm


def _severity_to_mask_layer(severity):
    token = str(severity or "").strip().upper()
    if not token:
        return None
    if token.startswith("CS"):
        suffix = token[2:]
        return "mask::CS{}".format(suffix)
    if token.isdigit():
        return "mask::CS{}".format(token)
    return "mask::{}".format(token)


def _face_projector(face):
    face_key = face.strip().lower()
    half = CUBE_LENGTH / 2.0
    if face_key == "+x":
        return lambda u, v: (half, u, v)
    if face_key == "-x":
        return lambda u, v: (-half, u, v)
    if face_key == "+y":
        return lambda u, v: (u, half, v)
    if face_key == "-y":
        return lambda u, v: (u, -half, v)
    if face_key == "+z":
        return lambda u, v: (u, v, half)
    if face_key == "-z":
        return lambda u, v: (u, v, -half)
    raise ValueError('Unknown face "{}". Use +x, -x, +y, -y, +z, -z.'.format(face))


def _center_and_project_points(points_2d, projector, width_half, height_half):
    return [projector(x - width_half, y - height_half) for x, y in points_2d]


def _build_mask_surfaces(crack_poly_id, hole_poly_ids=None):
    """Build planar mask surfaces from one crack polygon and its inner holes."""
    if not crack_poly_id or not rs.IsPolyline(crack_poly_id):
        return []

    input_curves = [crack_poly_id]
    for hole_id in hole_poly_ids or []:
        if hole_id and rs.IsPolyline(hole_id):
            input_curves.append(hole_id)

    surface_ids = rs.AddPlanarSrf(input_curves) or []
    if surface_ids:
        return [sid for sid in surface_ids if sid and rs.IsObject(sid)]

    # Fallback: at least keep the crack boundary as mask if hole trimming fails.
    fallback_ids = rs.AddPlanarSrf(crack_poly_id) or []
    return [sid for sid in fallback_ids if sid and rs.IsObject(sid)]


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
    """Read cube contour JSON via shared parser and keep legacy return signature."""
    parsed = read_cube_contour_json(filepath)
    global CUBE_LENGTH
    CUBE_LENGTH = max(float(parsed["width_mm"]), float(parsed["height_mm"]))
    return (
        parsed["contours"],
        parsed["base_contours"],
        parsed["expanded_contours"],
        parsed["difference_contours"],
        parsed["severities"],
    )


def center_2d_points(points_2d, width_mm=None, height_mm=None):
    """
    Center the 2D points around (0,0) using their bounding box center.
    points_2d: iterable of (x, y) (list or numpy array)
    Returns a list of (x, y) tuples.
    """
    width = CUBE_LENGTH if width_mm is None else float(width_mm)
    height = CUBE_LENGTH if height_mm is None else float(height_mm)
    centered = [(x - width / 2.0, y - height / 2.0) for (x, y) in points_2d]
    return centered




def add_polygon_curve(points_3d, close_curve=True):
    """
    Create a polyline (and optionally close it) from 3D points in Rhino.
    Returns the GUID of the polyline object.
    """
    if not points_3d:
        return None

    pts = list(points_3d)
    if len(pts) < 15:
        return None

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

    # Compute areas and keep only the largest split piece.
    idx_max = -1
    max_area = -1.0
    for idx, brep in enumerate(split_breps):
        amp = Rhino.Geometry.AreaMassProperties.Compute(brep)
        area = amp.Area if amp else 0.0
        if amp:
            dispose = getattr(amp, "Dispose", None)
            if dispose:
                dispose()
        if area > max_area:
            max_area = area
            idx_max = idx

    if idx_max < 0:
        print("No valid areas from split pieces.")
        return base_srf_id

    outer_id = sc.doc.Objects.AddBrep(split_breps[idx_max])
    if not outer_id:
        print("Failed to add outer split Brep.")
        return base_srf_id

    rs.DeleteObject(base_srf_id)
    return outer_id


def create_cube(cube_map_dir, start_face_index=0):
    faces = __create_cube_faces()

    filenames = sorted([e for e in os.listdir(cube_map_dir) if e.endswith(".json")])
    filepaths = [os.path.join(cube_map_dir, filename) for filename in filenames[start_face_index:start_face_index+6]]

    # Process each file / face
    face_cracks = {}
    layer_cache = {}
    for face_dir, filepath in zip(faces.keys(), filepaths):
        face = faces[face_dir]
        rs.ObjectLayer(face, "geometry::cube")
        rs.HideObject(face)

        contours, base_contours, erode_contours, diff_contours, severities = read_contour_json(filepath)
        width_half = CUBE_LENGTH / 2.0
        height_half = CUBE_LENGTH / 2.0
        projector = _face_projector(face_dir)

        assert len(contours) == len(severities) == len(erode_contours) == len(base_contours) == len(diff_contours), \
            f"Mismatch in number of contours ({len(contours)}), severities ({len(severities)}), erode contours ({len(erode_contours)}), base contours ({len(base_contours)}), or diff contours ({len(diff_contours)})."
        n_bases = len(erode_contours)
        cutters = []
        crack_items = []
        
        for i in range(n_bases):
            erode_pts_3d = _center_and_project_points(
                erode_contours[i]["points"], projector, width_half, height_half
            )
            erode_poly_id = add_polygon_curve(erode_pts_3d, close_curve=True)
            if not erode_poly_id:
                print("Skipping empty erode contour at index {} on face {}".format(i, face_dir))
                continue
            
            base_pts_3d = _center_and_project_points(
                base_contours[i]["points"], projector, width_half, height_half
            )
            base_poly_id = add_polygon_curve(base_pts_3d, close_curve=True)
            if not base_poly_id:
                print("Skipping empty base contour at index {} on face {}".format(i, face_dir))
                continue
            
            diff_poly_ids = []
            for diff_cnt in diff_contours[i]:
                diff_pts_3d = _center_and_project_points(
                    diff_cnt["points"], projector, width_half, height_half
                )
                diff_poly_id = add_polygon_curve(diff_pts_3d, close_curve=True)
                if diff_poly_id:
                    diff_poly_ids.append(diff_poly_id)

            severity = severities[i]
            severity_key = str(severity)
            layer_name = layer_cache.get(severity_key)
            if layer_name is None:
                layer_name = _severity_to_mask_layer(severity)
                if not layer_name:
                    raise ValueError("Invalid crack severity '{}' on face '{}'.".format(severity, face_dir))
                if not rs.IsLayer(layer_name):
                    raise ValueError("Layer '{}' does not exist. Please run preparation step first.".format(layer_name))
                layer_cache[severity_key] = layer_name

            crack_poly_ids = []
            crack_poly_indices = []
            noncrack_poly_ids = []
            holes_by_parent_index = {}
            for contour_idx, contour in enumerate(contours[i]):
                pts_3d = _center_and_project_points(
                    contour["points"], projector, width_half, height_half
                )
                if len(pts_3d) < 3:
                    continue
                poly_id = add_polygon_curve(pts_3d, close_curve=True)
                if poly_id:
                    parent_idx = int(contour.get("parent", -1))
                    if parent_idx != -1:
                        noncrack_poly_ids.append(poly_id)
                        holes_by_parent_index.setdefault(parent_idx, []).append(poly_id)
                    else:
                        crack_poly_indices.append(contour_idx)
                        crack_poly_ids.append(poly_id)

            if not crack_poly_ids:
                print("Skipping empty contour at index {} on face {}".format(i, face_dir))
                continue
            
            for poly_id in crack_poly_ids:
                rs.ObjectLayer(poly_id, layer_name)
            mask_surface_ids = []
            for crack_idx, crack_poly_id in zip(crack_poly_indices, crack_poly_ids):
                crack_holes = holes_by_parent_index.get(crack_idx, [])
                srf_ids = _build_mask_surfaces(crack_poly_id, crack_holes)
                for sid in srf_ids:
                    rs.ObjectLayer(sid, layer_name)
                mask_surface_ids.extend(srf_ids)

            if mask_surface_ids:
                cutters.extend(mask_surface_ids)
                crack_items.append({
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
