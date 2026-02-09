#! python3
from time import perf_counter

import rhinoscriptsyntax as rs

from utils_loc.config import load_config
from utils_loc.pipeline import create_model, prepare, render

STAGE_ORDER = (
    "reset",
    "load_config",
    "preparation",
    "view_setup",
    "modeling",
    "rendering",
)
DEFAULT_STAGES = (
    "load_config",
    "preparation",
    "view_setup",
    "modeling",
)
STAGE_ALIASES = {
    "prepare": "preparation",
    "prep": "preparation",
    "view": "view_setup",
    "setup_view": "view_setup",
    "model": "modeling",
    "render": "rendering",
}
STAGE_DEPENDENCIES = {
    "preparation": ("load_config",),
    "modeling": ("load_config",),
    "rendering": ("load_config",),
}


def reset():
    """Delete all objects from the active document."""
    objs = rs.AllObjects(
        select=False,
        include_lights=True,
        include_grips=True,
    )
    if objs:
        rs.DeleteObjects(objs)


def setup_render_view():
    """Set active view to Rendered mode and hide crack section layers."""
    import scriptcontext as sc
    import Rhino

    render_view = sc.doc.Views.ActiveView
    mode = Rhino.Display.DisplayModeDescription.FindByName("Rendered")
    if mode:
        render_view.ActiveViewport.DisplayMode = mode

    for layer in sc.doc.Layers:
        if layer.Name:
            layer.IsVisible = "CS" not in layer.Name


def time_stage(name, func, stage_times, *args, **kwargs):
    """Run a stage function and record elapsed seconds."""
    start = perf_counter()
    result = func(*args, **kwargs)
    duration = perf_counter() - start
    stage_times.append((name, duration))
    return result


def _normalize_stage_name(name):
    stage_name = name.strip().lower()
    stage_name = STAGE_ALIASES.get(stage_name, stage_name)
    if stage_name not in STAGE_ORDER:
        valid = ", ".join(STAGE_ORDER)
        raise ValueError(f"Unknown stage '{name}'. Valid stages: {valid}")
    return stage_name


def _resolve_stage_list(stages=None, include_reset=False, include_render=False, skip=None):
    if stages:
        requested = set(_normalize_stage_name(stage) for stage in stages)
        selected = [stage for stage in STAGE_ORDER if stage in requested]
    else:
        selected = list(DEFAULT_STAGES)
        if include_reset and "reset" not in selected:
            selected.insert(0, "reset")
        if include_render and "rendering" not in selected:
            selected.append("rendering")

    skip = set(skip or [])
    for stage in selected:
        for dep in STAGE_DEPENDENCIES.get(stage, ()):
            if dep in skip:
                raise ValueError(f"Cannot skip '{dep}' while running '{stage}'.")

    selected = [stage for stage in selected if stage not in skip]

    required = set(selected)
    for stage in selected:
        for dep in STAGE_DEPENDENCIES.get(stage, ()):
            required.add(dep)
    selected = [stage for stage in STAGE_ORDER if stage in required and stage not in skip]

    if not selected:
        raise ValueError("No stages selected after filters.")
    return selected


def run(
    config_name="cube_render.yaml",
    stages=None,
    include_reset=False,
    include_render=False,
    skip=None,
    start_face_index=0,
    show_cameras=False,
    print_timings=True,
):
    """Run selected pipeline stages."""
    selected_stages = _resolve_stage_list(
        stages=stages,
        include_reset=include_reset,
        include_render=include_render,
        skip=skip,
    )
    print("Selected stages:", ", ".join(selected_stages))

    stage_times = []
    context = {"cfg": None}

    if "reset" in selected_stages:
        time_stage("reset", reset, stage_times)

    if "load_config" in selected_stages:
        context["cfg"] = time_stage("load_config", load_config, stage_times, config_name)

    cfg = context["cfg"] or {}

    if "preparation" in selected_stages:
        time_stage("preparation", prepare, stage_times, params=cfg.get("preparation"))

    if "view_setup" in selected_stages:
        time_stage("view_setup", setup_render_view, stage_times)

    if "modeling" in selected_stages:
        modeling_params = dict(cfg.get("modeling", {}))
        if not modeling_params:
            raise ValueError("Selected 'modeling' stage but config has no 'modeling' section.")
        modeling_params["start_face_index"] = start_face_index
        time_stage("modeling", create_model, stage_times, params=modeling_params)

    if "rendering" in selected_stages:
        rendering_params = cfg.get("rendering", {})
        if not rendering_params:
            raise ValueError("Selected 'rendering' stage but config has no 'rendering' section.")
        time_stage(
            "rendering",
            render,
            stage_times,
            params=rendering_params,
            show_cameras=show_cameras,
        )

    if print_timings:
        total = 0.0
        print("======== Stage Timings ========")
        for name, duration in stage_times:
            total += duration
            print(f"{name}: {duration:.2f}s")
        print(f"total: {total:.2f}s")

    return stage_times


if __name__ == "__main__":
    # Rhino Python entrypoint: edit these for quick debug runs.
    run(
        config_name="cube_render.yaml",
        stages=["load_config", "preparation", "view_setup", "modeling"],
        include_reset=False,
        include_render=False,
        skip=[],
        start_face_index=0,
        show_cameras=False,
        print_timings=True,
    )
