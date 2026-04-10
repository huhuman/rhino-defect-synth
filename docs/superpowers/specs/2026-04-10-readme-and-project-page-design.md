# README Restructuring & Project Page Design

## Overview

Restructure the repository's documentation and create a GitHub Pages project website for the paper "Beyond Cracks: Synthetic Image and Geometry Generation for Computer Vision Detection and Severity Assessment of Diverse Concrete Surface Defects" (Hsu & Golparvar-Fard, UIUC).

## Part 1: README Restructuring

### Move existing detailed READMEs

- `README.md` → `docs/PIPELINE.md`
- `README.zh-TW.md` → `docs/PIPELINE.zh-TW.md`
- Update any internal cross-references (the language toggle links at the top of each file)

### New entry `README.md`

A high-level introductory README focused on describing this repo as a **synthetic defect generation pipeline**. Structure:

1. **Title + one-liner**: "DefectSynth — Geometry-Informed Synthetic Data Generation for Concrete Defect Inspection"
2. **Badges / links row**: Paper (placeholder) | Project Page | Datasets
3. **Overview paragraph**: What this tool does — parametric 3D defect modeling grounded in real defect shapes, multi-pass rendering (RGB, depth, normal, mask) in Rhino, two complementary modeling strategies.
4. **Two modeling strategies** (brief, ~2-3 sentences each):
   - **Cube-based**: Dense crack modeling for close-range inspection. Six-surface cube carrier, parameterized crack masks from real images, severity assignment via FHWA thresholds.
   - **Component-based**: Bridge-scale multi-defect modeling. Parameterized bridge elements (deck, pier, girder, etc.), defect placement (crack, spalling, corrosion, efflorescence) with inspection-standard severity.
5. **Visual**: Reference to a key pipeline figure (can be added later as `docs/static/images/pipeline_overview.png` or similar).
6. **Datasets table**:

   | Dataset | Images | Modalities | Description |
   |---------|--------|------------|-------------|
   | SynthCrack-42k | 42,165 | RGB, Mask | Baseline cube-based crack dataset (Unreal Engine) |
   | SynthCrack-72k | 71,860 | RGB, Mask | Extended crack shapes + updated parameterization |
   | SynthCrack-ONE | 23,485 | RGB, Depth, Normal, Mask | Rhino-generated with refined crack-shape library |
   | SynthDefect-Bridge | 2,291 | RGB, Depth, Normal, Mask | Bridge-scale multi-defect (crack, spalling, corrosion, efflorescence) |
   | RealDefect-Bridge | TBD | RGB, Mask | Real bridge inspection images for evaluation |

   - Download links: placeholder for each
   - Note that only SynthCrack-ONE and SynthDefect-Bridge include depth and normal channels

7. **Requirements + Quick Start**: Short — Rhino 8 (Windows), Python scripting, PyYAML, numpy. Point to `docs/PIPELINE.md` for full config/stage details.
8. **Downstream applications**: One short paragraph — "Feasibility of the generated data was validated through cross-dataset defect segmentation, realism enhancement (CycleGAN/CUT), and geometry-informed teacher-student learning. See the paper for details."
9. **Citation**: BibTeX placeholder block.
10. **Detailed documentation link**: `docs/PIPELINE.md` (EN) / `docs/PIPELINE.zh-TW.md` (中文).

### New entry `README.zh-TW.md`

Chinese translation of the new entry README, same structure. Language toggle links at top.

## Part 2: GitHub Pages Project Website

### Hosting

- Served from `docs/` folder on `main` branch (configure GitHub Pages to use `docs/` as source)
- The project page lives alongside the pipeline docs in `docs/`

### File structure

```
docs/
  index.html              # Project page (single-page)
  static/
    css/
      bulma.min.css       # Bulma CSS framework (v0.9.4)
      index.css           # Custom styles
    js/
      index.js            # Minimal JS (carousel if needed, navbar burger)
    images/               # Figures, result images (user to add)
      placeholder.svg     # Placeholder for missing images
    gifs/                 # GIF demos (user to add)
  PIPELINE.md             # Moved from root README.md
  PIPELINE.zh-TW.md       # Moved from root README.zh-TW.md
```

### Page sections (index.html)

1. **Hero section**
   - Paper title: "Beyond Cracks: Synthetic Image and Geometry Generation for Computer Vision Detection and Severity Assessment of Diverse Concrete Surface Defects"
   - Authors: Shun-Hsiang Hsu, Mani Golparvar-Fard
   - Affiliations: Department of Civil and Environmental Engineering / School of Computing and Data Science, University of Illinois at Urbana-Champaign
   - Venue: Placeholder (e.g., "Preprint, 2026")
   - Action buttons: [Paper] [Code] [Datasets] — Paper link is placeholder, Code links to this GitHub repo

2. **Teaser**
   - Placeholder for a hero GIF or figure showing the overall pipeline
   - Caption: brief one-line description

3. **Abstract**
   - Text from paper abstract, lightly trimmed for web readability

4. **Method Overview**
   - Two subsections with brief text (~3-4 sentences each):
     - Cube-based crack modeling (close-range)
     - Component-based bridge defect modeling (structural-scale)
   - Image/figure placeholders for pipeline diagrams
   - GIF placeholders: one for 3D modeling process, one for rendering/domain randomization

5. **Datasets**
   - Table with 5 datasets: name, # images, modalities, description
   - Download link placeholders for each
   - Brief note about depth/normal availability

6. **Results Gallery**
   - Subsections with image grid placeholders:
     - Multi-pass outputs (color / depth / normal / mask side-by-side)
     - Realism enhancement (original synthetic vs. CycleGAN-enhanced vs. real)
     - Prediction examples (RGB input → model prediction on real images)

7. **BibTeX**
   - Placeholder citation block in a copyable code box

8. **Acknowledgements**
   - NSF Grant No. CMMI-2053935, Delta system (OAC 2005572), UIUC/NCSA

### Technology stack

- **Bulma CSS** (v0.9.4) for responsive grid/layout — CDN or vendored
- **Font Awesome** for icons (paper, github, download)
- **Academicons** for Google Scholar icon
- **Google Fonts** (Noto Sans) for typography
- No build step, no JS framework
- Based on the widely-used Academic Project Page Template pattern (Nerfies-style)

### Design notes

- Responsive: works on desktop and mobile
- Dark/light: standard light background (white/off-white), consistent with academic project pages
- Image placeholders: use a simple SVG placeholder with descriptive text so the user knows what to replace
- All asset paths are relative so the page works both locally and on GitHub Pages

## Recommended GIF/Demo Placements

| Section | Content Type | Description |
|---------|-------------|-------------|
| Teaser | GIF or static figure | Overall pipeline overview — the most visually striking summary |
| Method: Cube | GIF | Cube modeling process in Rhino (crack placement on faces, geometry generation) |
| Method: Component | GIF | Bridge component modeling (element generation, defect placement, camera walkthrough) |
| Results: Multi-pass | Static images | Side-by-side grid: color, depth, normal, mask for sample views |
| Results: Realism | Static images | Before/after comparison: synthetic → CycleGAN → real reference |
| Results: Prediction | Static images | Model predictions on real bridge/crack images |

## Out of scope

- Deep learning training code (not in this repo)
- Detailed experiment reproduction instructions (covered in paper)
- Automated CI/CD for the website (just static files)
