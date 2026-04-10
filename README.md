# DefectSynth

**Geometry-Informed Synthetic Data Generation for Concrete Defect Inspection**

[Paper (coming soon)](#) | [Project Page](https://huhuman.github.io/rhino-defect-synth/) | [Datasets](#datasets)

---

## Overview

This repository provides a parametric 3D defect modeling and multi-pass rendering pipeline for generating synthetic concrete inspection data. Defect shapes are derived from real inspection images and transformed into controllable 3D models with configurable scale, orientation, depth, and placement. The pipeline renders aligned RGB, depth, surface-normal, and mask outputs from Rhino, supporting both close-range crack inspection and structural-scale bridge defect scenarios.

<p align="center">
  <img src="docs/static/images/overview.jpg" alt="Pipeline Overview" width="800"/>
</p>

## Modeling Strategies

### Cube-based Crack Modeling

A six-surface cube serves as the carrier for dense crack patterns under close-range inspection settings. Crack masks from a curated real-image database (CrackSeg9k) are placed on cube faces with parameterized scale, orientation, and position. Crack severity is assigned through pixel-to-metric conversion following FHWA width thresholds.

### Component-based Bridge Defect Modeling

Parameterized bridge elements (deck, pier, girder, parapet, bearing) are generated from road centerlines and structural guidelines (IDOT Bridge Manual). Defect masks — crack, spalling, corrosion (exposed rebar), and efflorescence — are instantiated on component surfaces with inspection-standard severity assignment. Category-specific 3D operations model surface erosion (spalling), exposed reinforcement (corrosion), and extruded deposits (efflorescence).

## Datasets

| Dataset | Images | RGB | Depth | Normal | Mask | Description |
|---------|-------:|:---:|:-----:|:------:|:----:|-------------|
| [SynthCrack-42k](https://drive.google.com/file/d/1GFyQyNLw3Y7Qob-9uSbFTI0EfKJnBjpp/view?usp=sharing) | 42,165 | ✓ | | | ✓ | Baseline cube-based crack dataset (Unreal Engine) |
| [SynthCrack-72k](https://drive.google.com/file/d/1Rcjdk8jVy-8KQ1VIYXatUmWe_ZfOTx_3/view?usp=sharing) | 71,860 | ✓ | | | ✓ | Extended crack shapes + updated geometric parameterization |
| [SynthCrack-ONE](https://drive.google.com/file/d/1_HUhw3TwORQEO-hbxrYxrBlWnlyGBpYs/view?usp=sharing) | 23,485 | ✓ | ✓ | ✓ | ✓ | Rhino-generated with refined crack-shape library |
| [SynthDefect-Bridge](https://drive.google.com/file/d/1umZxzBGUBRo36vlRug52WZgzZxE9gegZ/view?usp=sharing) | 2,291 | ✓ | ✓ | ✓ | ✓ | Bridge-scale multi-defect: crack, spalling, corrosion, efflorescence |
| RealDefect-Bridge | TBD | ✓ | | | ✓ | Real bridge inspection images for evaluation (coming soon) |

> SynthCrack-ONE and SynthDefect-Bridge include aligned depth and surface-normal channels.

## Requirements

- **Rhino 8** (Windows) with Python scripting enabled
- **PyYAML** and **numpy** available in Rhino Python
- **rhino_channels_plugin** (included) for linear depth/normal `.pfm` export

## Quick Start

```python
import main
main.run(
    config_name="cube_render.yaml",
    stages=["load_config", "preparation", "view_setup", "modeling", "rendering"],
)
```

For batch dataset generation:

```python
import main_cube_batch
main_cube_batch.run(
    config_name="cube_render.yaml",
    renders_per_model=4,
    max_iter=3,
    seed=42,
)
```

See [Pipeline Documentation](docs/PIPELINE.md) for full configuration, stage details, and output structure.

## Downstream Applications

The feasibility of the generated data was validated through cross-dataset defect segmentation, unpaired realism enhancement (CycleGAN / CUT), and geometry-informed teacher-student learning. See the [paper](#) for details.

## Citation

```bibtex
@article{hsu2026defectsynth,
  title={Beyond Cracks: Synthetic Image and Geometry Generation for Computer Vision Detection and Severity Assessment of Diverse Concrete Surface Defects},
  author={Hsu, Shun-Hsiang and Golparvar-Fard, Mani},
  journal={Automation in Construction},
  year={2026},
  note={Preprint}
}
```

## Documentation

- [Pipeline Documentation (EN)](docs/PIPELINE.md)
- [Pipeline Documentation (中文)](docs/PIPELINE.zh-TW.md)
- [Rhino Channels Plugin](rhino_channels_plugin/README.md)
