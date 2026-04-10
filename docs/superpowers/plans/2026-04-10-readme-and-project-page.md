# README Restructuring & Project Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the repo README into an introductory entry point + detailed pipeline docs, and create a GitHub Pages project website for the paper.

**Architecture:** Move existing detailed READMEs to `docs/PIPELINE.md` and `docs/PIPELINE.zh-TW.md`. Write new high-level entry READMEs. Create a single-page static HTML project website in `docs/` using Bulma CSS, following the standard academic project page pattern (Nerfies/Academic-project-page-template style).

**Tech Stack:** Static HTML, Bulma CSS 0.9.4, Font Awesome 6, Academicons, Google Fonts (Noto Sans)

---

## File Structure

```
docs/
  index.html                    # CREATE - Project website (single-page)
  static/
    css/
      index.css                 # CREATE - Custom styles
    images/
      placeholder.svg           # CREATE - Placeholder for missing images
  PIPELINE.md                   # MOVE from root README.md (update internal links)
  PIPELINE.zh-TW.md             # MOVE from root README.zh-TW.md (update internal links)
README.md                       # REWRITE - New introductory README (English)
README.zh-TW.md                 # REWRITE - New introductory README (Chinese)
```

Note: Bulma CSS, Font Awesome, Academicons, and Google Fonts are loaded via CDN — no vendored copies needed.

---

### Task 1: Move existing READMEs to docs/

**Files:**
- Move: `README.md` → `docs/PIPELINE.md`
- Move: `README.zh-TW.md` → `docs/PIPELINE.zh-TW.md`

- [ ] **Step 1: Move English README**

```bash
git mv README.md docs/PIPELINE.md
```

- [ ] **Step 2: Update language toggle in docs/PIPELINE.md**

At the top of `docs/PIPELINE.md`, change the language toggle from:
```markdown
[English](README.md) | [繁體中文](README.zh-TW.md)
```
to:
```markdown
[English](PIPELINE.md) | [繁體中文](PIPELINE.zh-TW.md)
```

- [ ] **Step 3: Move Chinese README**

```bash
git mv README.zh-TW.md docs/PIPELINE.zh-TW.md
```

- [ ] **Step 4: Update language toggle in docs/PIPELINE.zh-TW.md**

At the top of `docs/PIPELINE.zh-TW.md`, change the language toggle from:
```markdown
[English](README.md) | [繁體中文](README.zh-TW.md)
```
to:
```markdown
[English](PIPELINE.md) | [繁體中文](PIPELINE.zh-TW.md)
```

- [ ] **Step 5: Commit**

```bash
git add docs/PIPELINE.md docs/PIPELINE.zh-TW.md
git commit -m "docs: move detailed READMEs to docs/PIPELINE.md"
```

---

### Task 2: Write new entry README.md (English)

**Files:**
- Create: `README.md`

- [ ] **Step 1: Create the new README**

Write `README.md` with the following content (full text provided):

```markdown
# DefectSynth

**Geometry-Informed Synthetic Data Generation for Concrete Defect Inspection**

[Paper (coming soon)](#) | [Project Page](https://huhuman.github.io/rhino-defect-synth/) | [Datasets](#datasets)

---

## Overview

This repository provides a parametric 3D defect modeling and multi-pass rendering pipeline for generating synthetic concrete inspection data. Defect shapes are derived from real inspection images and transformed into controllable 3D models with configurable scale, orientation, depth, and placement. The pipeline renders aligned RGB, depth, surface-normal, and mask outputs from Rhino, supporting both close-range crack inspection and structural-scale bridge defect scenarios.

<p align="center">
  <img src="docs/static/images/placeholder.svg" alt="Pipeline Overview" width="800"/>
</p>

## Modeling Strategies

### Cube-based Crack Modeling

A six-surface cube serves as the carrier for dense crack patterns under close-range inspection settings. Crack masks from a curated real-image database (CrackSeg9k) are placed on cube faces with parameterized scale, orientation, and position. Crack severity is assigned through pixel-to-metric conversion following FHWA width thresholds.

### Component-based Bridge Defect Modeling

Parameterized bridge elements (deck, pier, girder, parapet, bearing) are generated from road centerlines and structural guidelines (IDOT Bridge Manual). Defect masks — crack, spalling, corrosion (exposed rebar), and efflorescence — are instantiated on component surfaces with inspection-standard severity assignment. Category-specific 3D operations model surface erosion (spalling), exposed reinforcement (corrosion), and extruded deposits (efflorescence).

## Datasets

| Dataset | Images | RGB | Depth | Normal | Mask | Description |
|---------|-------:|:---:|:-----:|:------:|:----:|-------------|
| [SynthCrack-42k](#) | 42,165 | ✓ | | | ✓ | Baseline cube-based crack dataset (Unreal Engine) |
| [SynthCrack-72k](#) | 71,860 | ✓ | | | ✓ | Extended crack shapes + updated geometric parameterization |
| [SynthCrack-ONE](#) | 23,485 | ✓ | ✓ | ✓ | ✓ | Rhino-generated with refined crack-shape library |
| [SynthDefect-Bridge](#) | 2,291 | ✓ | ✓ | ✓ | ✓ | Bridge-scale multi-defect: crack, spalling, corrosion, efflorescence |
| [RealDefect-Bridge](#) | TBD | ✓ | | | ✓ | Real bridge inspection images for evaluation |

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
  journal={Preprint},
  year={2026}
}
```

## Documentation

- [Pipeline Documentation (EN)](docs/PIPELINE.md)
- [Pipeline Documentation (中文)](docs/PIPELINE.zh-TW.md)
- [Rhino Channels Plugin](rhino_channels_plugin/README.md)
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: write new introductory README"
```

---

### Task 3: Write new entry README.zh-TW.md (Chinese)

**Files:**
- Create: `README.zh-TW.md`

- [ ] **Step 1: Create the Chinese README**

Write `README.zh-TW.md` — a Chinese translation of the new entry README with the same structure. Language toggle at top links to `README.md` and `README.zh-TW.md`.

```markdown
# DefectSynth

**基於幾何資訊的合成資料生成 — 混凝土損害檢測**

[論文 (即將公開)](#) | [專案頁面](https://huhuman.github.io/rhino-defect-synth/) | [資料集](#資料集)

---

## 概述

本儲存庫提供一套參數化 3D 損害建模與多通道渲染流程，用於生成合成混凝土檢測資料。損害形狀源自真實檢測影像，轉換為可控制尺度、方向、深度與位置的 3D 模型。流程在 Rhino 中渲染對齊的 RGB、深度、表面法向量與遮罩輸出，同時支援近距離裂縫檢測與結構尺度橋梁損害場景。

<p align="center">
  <img src="docs/static/images/placeholder.svg" alt="流程概覽" width="800"/>
</p>

## 建模策略

### 立方體裂縫建模 (Cube-based)

以六面立方體作為近距離裂縫場景的載體。來自真實影像資料庫 (CrackSeg9k) 的裂縫遮罩，以參數化的尺度、方向與位置放置於立方體表面。裂縫嚴重程度透過像素-公制轉換，依據 FHWA 寬度門檻指定。

### 橋梁元件損害建模 (Component-based)

根據道路中心線與結構設計規範 (IDOT Bridge Manual) 生成參數化橋梁元件（橋面板、橋墩、大梁、欄杆、支承）。裂縫、剝落、腐蝕（裸露鋼筋）與白華等損害遮罩放置於元件表面，依檢測標準指定嚴重程度。各損害類別以對應的 3D 操作建模：表面侵蝕（剝落）、裸露鋼筋（腐蝕）、表面沉積物（白華）。

## 資料集

| 資料集 | 影像數 | RGB | Depth | Normal | Mask | 描述 |
|--------|-------:|:---:|:-----:|:------:|:----:|------|
| [SynthCrack-42k](#) | 42,165 | ✓ | | | ✓ | 基準立方體裂縫資料集 (Unreal Engine) |
| [SynthCrack-72k](#) | 71,860 | ✓ | | | ✓ | 擴充裂縫形狀 + 更新幾何參數化 |
| [SynthCrack-ONE](#) | 23,485 | ✓ | ✓ | ✓ | ✓ | Rhino 生成，精煉裂縫形狀庫 |
| [SynthDefect-Bridge](#) | 2,291 | ✓ | ✓ | ✓ | ✓ | 橋梁尺度多損害：裂縫、剝落、腐蝕、白華 |
| [RealDefect-Bridge](#) | TBD | ✓ | | | ✓ | 真實橋梁檢測影像（評估用） |

> SynthCrack-ONE 與 SynthDefect-Bridge 包含對齊的深度與表面法向量通道。

## 需求

- **Rhino 8** (Windows) 並啟用 Python scripting
- Rhino Python 中需安裝 **PyYAML** 與 **numpy**
- **rhino_channels_plugin**（已附於本 repo）用於線性深度/法向量 `.pfm` 輸出

## 快速開始

```python
import main
main.run(
    config_name="cube_render.yaml",
    stages=["load_config", "preparation", "view_setup", "modeling", "rendering"],
)
```

批次資料集生成：

```python
import main_cube_batch
main_cube_batch.run(
    config_name="cube_render.yaml",
    renders_per_model=4,
    max_iter=3,
    seed=42,
)
```

完整設定、流程階段與輸出結構請參閱 [Pipeline 文件](docs/PIPELINE.zh-TW.md)。

## 下游應用

生成資料的可行性已透過跨資料集損害分割、非配對真實感增強 (CycleGAN / CUT)，以及幾何資訊引導的師生學習進行驗證。詳情請參閱[論文](#)。

## 引用

```bibtex
@article{hsu2026defectsynth,
  title={Beyond Cracks: Synthetic Image and Geometry Generation for Computer Vision Detection and Severity Assessment of Diverse Concrete Surface Defects},
  author={Hsu, Shun-Hsiang and Golparvar-Fard, Mani},
  journal={Preprint},
  year={2026}
}
```

## 文件

- [Pipeline Documentation (EN)](docs/PIPELINE.md)
- [Pipeline Documentation (中文)](docs/PIPELINE.zh-TW.md)
- [Rhino Channels Plugin](rhino_channels_plugin/README.md)
```

- [ ] **Step 2: Commit**

```bash
git add README.zh-TW.md
git commit -m "docs: write new introductory README (zh-TW)"
```

---

### Task 4: Create placeholder SVG and static directories

**Files:**
- Create: `docs/static/css/` (directory)
- Create: `docs/static/images/placeholder.svg`
- Create: `docs/static/gifs/` (directory, with `.gitkeep`)

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p docs/static/css docs/static/images docs/static/gifs
```

- [ ] **Step 2: Create placeholder SVG**

Write `docs/static/images/placeholder.svg`:

```svg
<svg xmlns="http://www.w3.org/2000/svg" width="800" height="400" viewBox="0 0 800 400">
  <rect width="800" height="400" fill="#f1f5f9" rx="12"/>
  <text x="400" y="190" text-anchor="middle" font-family="Inter, sans-serif" font-size="20" fill="#94a3b8">Image Placeholder</text>
  <text x="400" y="220" text-anchor="middle" font-family="Inter, sans-serif" font-size="14" fill="#cbd5e1">Replace with your figure</text>
</svg>
```

- [ ] **Step 3: Add .gitkeep for gifs directory**

```bash
touch docs/static/gifs/.gitkeep
```

- [ ] **Step 4: Commit**

```bash
git add docs/static/
git commit -m "docs: add static asset directories and placeholder SVG"
```

---

### Task 5: Create custom CSS for project page

**Files:**
- Create: `docs/static/css/index.css`

- [ ] **Step 1: Write index.css**

Write `docs/static/css/index.css` with styles for the academic project page. Based on the Academic Project Page Template pattern but trimmed to what we need (no carousel, no video player, no "More Works" dropdown):

```css
:root {
  --primary-color: #2563eb;
  --primary-hover: #1d4ed8;
  --text-primary: #1e293b;
  --text-secondary: #64748b;
  --text-light: #94a3b8;
  --bg-primary: #ffffff;
  --bg-secondary: #f8fafc;
  --bg-accent: #f1f5f9;
  --border-color: #e2e8f0;
  --shadow-sm: 0 1px 2px 0 rgb(0 0 0 / 0.05);
  --shadow-md: 0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1);
  --shadow-lg: 0 10px 15px -3px rgb(0 0 0 / 0.1), 0 4px 6px -4px rgb(0 0 0 / 0.1);
  --border-radius: 12px;
  --transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

html {
  scroll-behavior: smooth;
}

body {
  font-family: 'Noto Sans', 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  color: var(--text-primary);
  line-height: 1.6;
  -webkit-font-smoothing: antialiased;
}

/* Hero */
.publication-title {
  font-weight: 800 !important;
  line-height: 1.15 !important;
  margin-bottom: 1.5rem !important;
}

.publication-authors {
  font-weight: 500;
  margin-bottom: 1rem;
}

.publication-authors a {
  color: var(--primary-color) !important;
  text-decoration: none;
  font-weight: 600;
}

.publication-authors a:hover {
  color: var(--primary-hover) !important;
  text-decoration: underline;
}

.author-block {
  display: inline-block;
  margin-right: 0.25rem;
}

/* Buttons */
.publication-links .button {
  border-radius: var(--border-radius) !important;
  font-weight: 600 !important;
  transition: var(--transition) !important;
  margin: 4px;
}

.button.is-dark {
  background: var(--text-primary) !important;
  box-shadow: var(--shadow-sm);
}

.button.is-dark:hover {
  background: var(--primary-color) !important;
  transform: translateY(-2px);
  box-shadow: var(--shadow-md);
}

/* Sections */
.hero.is-light {
  background: var(--bg-secondary);
  border-top: 1px solid var(--border-color);
  border-bottom: 1px solid var(--border-color);
}

.section-title {
  font-weight: 700 !important;
  margin-bottom: 1.5rem !important;
  padding-bottom: 0.75rem;
  position: relative;
}

.section-title::after {
  content: '';
  position: absolute;
  bottom: 0;
  left: 50%;
  transform: translateX(-50%);
  width: 60px;
  height: 3px;
  background: linear-gradient(135deg, #667eea, #764ba2);
  border-radius: 2px;
}

/* Abstract */
.content.has-text-justified {
  font-size: 1.05rem;
  line-height: 1.8;
  color: var(--text-secondary);
}

/* Teaser */
.teaser-container img,
.teaser-container video {
  border-radius: var(--border-radius);
  box-shadow: var(--shadow-lg);
  max-width: 100%;
  height: auto;
}

.teaser-caption {
  font-size: 0.95rem;
  color: var(--text-secondary);
  margin-top: 1rem;
}

/* Method */
.method-figure {
  margin: 1.5rem 0;
  text-align: center;
}

.method-figure img {
  border-radius: var(--border-radius);
  box-shadow: var(--shadow-md);
  max-width: 100%;
  height: auto;
}

.method-figure figcaption {
  font-size: 0.9rem;
  color: var(--text-light);
  margin-top: 0.75rem;
}

/* Datasets table */
.dataset-table {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
  border: 1px solid var(--border-color);
  border-radius: var(--border-radius);
  overflow: hidden;
  font-size: 0.95rem;
}

.dataset-table thead {
  background: var(--bg-accent);
}

.dataset-table th,
.dataset-table td {
  padding: 0.75rem 1rem;
  text-align: left;
  border-bottom: 1px solid var(--border-color);
}

.dataset-table th {
  font-weight: 600;
  color: var(--text-primary);
  font-size: 0.85rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}

.dataset-table td {
  color: var(--text-secondary);
}

.dataset-table tr:last-child td {
  border-bottom: none;
}

.dataset-table .checkmark {
  color: #10b981;
  font-weight: 700;
}

.dataset-table .dataset-name {
  font-weight: 600;
  color: var(--text-primary);
}

.dataset-table .btn-download {
  display: inline-block;
  padding: 0.3rem 0.75rem;
  background: var(--primary-color);
  color: white !important;
  border-radius: 6px;
  font-size: 0.8rem;
  font-weight: 600;
  text-decoration: none;
  transition: var(--transition);
}

.dataset-table .btn-download:hover {
  background: var(--primary-hover);
  transform: translateY(-1px);
}

/* Results gallery */
.gallery-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 1rem;
  margin: 1.5rem 0;
}

.gallery-grid img {
  width: 100%;
  border-radius: 8px;
  box-shadow: var(--shadow-sm);
  transition: var(--transition);
}

.gallery-grid img:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-md);
}

.gallery-row {
  display: flex;
  gap: 0.5rem;
  margin: 1rem 0;
  align-items: flex-start;
}

.gallery-row img {
  flex: 1;
  min-width: 0;
  border-radius: 8px;
  box-shadow: var(--shadow-sm);
}

.gallery-label {
  text-align: center;
  font-size: 0.85rem;
  color: var(--text-light);
  margin-top: 0.5rem;
  font-weight: 500;
}

/* BibTeX */
.bibtex-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1rem;
}

.copy-btn {
  background: var(--primary-color);
  color: white;
  border: none;
  border-radius: 8px;
  padding: 0.5rem 1rem;
  font-size: 0.85rem;
  font-weight: 600;
  cursor: pointer;
  transition: var(--transition);
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
}

.copy-btn:hover {
  background: var(--primary-hover);
  transform: translateY(-1px);
}

.copy-btn.copied {
  background: #10b981;
}

pre {
  background: var(--bg-accent) !important;
  border: 1px solid var(--border-color) !important;
  border-radius: var(--border-radius) !important;
  padding: 1.25rem !important;
  font-size: 0.875rem !important;
  overflow-x: auto;
}

code {
  font-family: 'SF Mono', 'Cascadia Code', 'Roboto Mono', monospace !important;
}

/* Footer */
.footer {
  background: var(--bg-secondary);
  border-top: 1px solid var(--border-color);
  padding: 2.5rem 1.5rem;
}

.footer a {
  color: var(--primary-color);
  text-decoration: none;
}

.footer a:hover {
  text-decoration: underline;
}

/* Responsive */
@media screen and (max-width: 768px) {
  .publication-title {
    font-size: 1.75rem !important;
  }

  .gallery-row {
    flex-direction: column;
  }

  .dataset-table {
    font-size: 0.85rem;
  }

  .dataset-table th,
  .dataset-table td {
    padding: 0.5rem;
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add docs/static/css/index.css
git commit -m "docs: add custom CSS for project page"
```

---

### Task 6: Create project page index.html

**Files:**
- Create: `docs/index.html`

- [ ] **Step 1: Write index.html**

Write `docs/index.html` — the full single-page project website. The content below is the complete file:

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">

  <meta name="title" content="DefectSynth - Hsu & Golparvar-Fard">
  <meta name="description" content="Geometry-informed synthetic data generation framework for computer vision detection and severity assessment of diverse concrete surface defects.">
  <meta name="keywords" content="synthetic data, concrete defects, crack detection, bridge inspection, computer vision, deep learning, 3D modeling">
  <meta name="author" content="Shun-Hsiang Hsu, Mani Golparvar-Fard">

  <meta property="og:type" content="article">
  <meta property="og:title" content="Beyond Cracks: Synthetic Image and Geometry Generation for Concrete Defect Assessment">
  <meta property="og:description" content="Geometry-informed synthetic data generation framework for computer vision detection and severity assessment of diverse concrete surface defects.">
  <meta property="og:url" content="https://huhuman.github.io/rhino-defect-synth/">

  <meta name="citation_title" content="Beyond Cracks: Synthetic Image and Geometry Generation for Computer Vision Detection and Severity Assessment of Diverse Concrete Surface Defects">
  <meta name="citation_author" content="Hsu, Shun-Hsiang">
  <meta name="citation_author" content="Golparvar-Fard, Mani">
  <meta name="citation_publication_date" content="2026">

  <title>DefectSynth | Hsu & Golparvar-Fard</title>

  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bulma@0.9.4/css/bulma.min.css">
  <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/jpswalsh/academicons@1/css/academicons.min.css">
  <link href="https://fonts.googleapis.com/css2?family=Noto+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="static/css/index.css">
</head>
<body>

<!-- ===== HERO ===== -->
<section class="hero">
  <div class="hero-body">
    <div class="container is-max-desktop">
      <div class="columns is-centered">
        <div class="column has-text-centered">
          <h1 class="title is-2 publication-title">
            Beyond Cracks: Synthetic Image and Geometry Generation for Computer Vision Detection and Severity Assessment of Diverse Concrete Surface Defects
          </h1>
          <div class="is-size-5 publication-authors">
            <span class="author-block">
              <a href="https://github.com/huhuman" target="_blank">Shun-Hsiang Hsu</a><sup>a</sup>,
            </span>
            <span class="author-block">
              <a href="https://reil.cs.illinois.edu/" target="_blank">Mani Golparvar-Fard</a><sup>a,b</sup>
            </span>
          </div>
          <div class="is-size-6 publication-authors">
            <span class="author-block">
              <sup>a</sup>Department of Civil and Environmental Engineering,
              <sup>b</sup>School of Computing and Data Science<br>
              University of Illinois at Urbana-Champaign
            </span>
          </div>
          <div class="is-size-6 publication-authors" style="margin-top: 0.5rem;">
            <span class="author-block" style="color: var(--text-light);">Preprint, 2026</span>
          </div>

          <div class="column has-text-centered">
            <div class="publication-links">
              <span class="link-block">
                <a href="#" class="button is-normal is-rounded is-dark">
                  <span class="icon"><i class="fas fa-file-pdf"></i></span>
                  <span>Paper</span>
                </a>
              </span>
              <span class="link-block">
                <a href="https://github.com/huhuman/rhino-defect-synth" target="_blank" class="button is-normal is-rounded is-dark">
                  <span class="icon"><i class="fab fa-github"></i></span>
                  <span>Code</span>
                </a>
              </span>
              <span class="link-block">
                <a href="#datasets" class="button is-normal is-rounded is-dark">
                  <span class="icon"><i class="fas fa-database"></i></span>
                  <span>Datasets</span>
                </a>
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</section>

<!-- ===== TEASER ===== -->
<section class="hero is-small">
  <div class="container is-max-desktop">
    <div class="hero-body">
      <div class="teaser-container has-text-centered">
        <!-- Replace with your pipeline overview figure or GIF -->
        <img src="static/images/placeholder.svg" alt="Pipeline overview: 2D defect shapes to 3D parametric models to multi-pass rendered outputs">
        <p class="teaser-caption">
          Overview of the geometry-informed synthetic data generation pipeline. Real 2D defect shapes are transformed into parametric 3D models and rendered with domain randomization to produce aligned RGB, depth, normal, and mask outputs.
        </p>
      </div>
    </div>
  </div>
</section>

<!-- ===== ABSTRACT ===== -->
<section class="section hero is-light">
  <div class="container is-max-desktop">
    <div class="columns is-centered has-text-centered">
      <div class="column is-four-fifths">
        <h2 class="title is-3 section-title">Abstract</h2>
        <div class="content has-text-justified">
          <p>
            Computer vision-based concrete condition assessment has largely focused on crack detection, primarily because publicly available datasets rarely capture diverse defect types and detailed severity-relevant annotations. This paper presents a geometry-informed synthetic generation framework for concrete defects, in which defect shapes are derived from real images and transformed into parametric 3D models for scalable, automatically annotated data generation. Two complementary modeling strategies are developed: a cube-based representation for dense crack patterns in close-range inspections, and a component-based representation for spalling, corrosion, and efflorescence under structural-scale bridge inspection settings. We further introduce a geometry-informed learning framework that uses RGB as the deployment modality while leveraging depth and surface-normal cues during training to improve defect segmentation and severity-oriented defect understanding. Cross-dataset experiments show improved generalization, including gains of 0.18 in IoU-crack for crack segmentation and 0.10 in mIoU for multi-defect segmentation.
          </p>
        </div>
      </div>
    </div>
  </div>
</section>

<!-- ===== METHOD ===== -->
<section class="section">
  <div class="container is-max-desktop">
    <div class="columns is-centered has-text-centered">
      <div class="column is-four-fifths">
        <h2 class="title is-3 section-title">Method</h2>
      </div>
    </div>

    <div class="columns is-centered">
      <div class="column is-four-fifths">

        <h3 class="title is-4">Cube-based Crack Modeling</h3>
        <p>
          For close-range crack inspection, a six-surface cube serves as the carrier. Crack masks from a curated real-image database are placed on cube faces with parameterized scale, orientation, and position. Pixel-to-metric conversion combined with FHWA thresholds assigns crack severity (insignificant, moderate, wide), and crack geometry is modeled with configurable width, erosion depth, and overall depth for 3D realism.
        </p>
        <figure class="method-figure">
          <!-- Replace: GIF of cube modeling process in Rhino -->
          <img src="static/images/placeholder.svg" alt="Cube-based crack modeling process">
          <figcaption>Cube-based crack modeling: crack masks placed on cube surfaces, converted to 3D geometry with severity assignment.</figcaption>
        </figure>

        <h3 class="title is-4" style="margin-top: 2.5rem;">Component-based Bridge Defect Modeling</h3>
        <p>
          For structural-scale bridge inspection, parameterized bridge elements (deck, pier, girder, parapet, bearing) are generated from road centerlines and structural guidelines. Defect masks for crack, spalling, corrosion, and efflorescence are instantiated on component surfaces. Category-specific 3D operations model surface erosion (spalling), exposed reinforcement (corrosion), and extruded deposits (efflorescence), integrating defect and structural geometry in a unified process.
        </p>
        <figure class="method-figure">
          <!-- Replace: GIF of component modeling + domain randomization -->
          <img src="static/images/placeholder.svg" alt="Component-based bridge defect modeling">
          <figcaption>Component-based bridge modeling with defect placement and domain randomization for rendering.</figcaption>
        </figure>

      </div>
    </div>
  </div>
</section>

<!-- ===== DATASETS ===== -->
<section class="section hero is-light" id="datasets">
  <div class="container is-max-desktop">
    <div class="columns is-centered has-text-centered">
      <div class="column is-four-fifths">
        <h2 class="title is-3 section-title">Datasets</h2>
        <p style="color: var(--text-secondary); margin-bottom: 1.5rem;">
          All synthetic datasets and a curated real-image evaluation set are prepared for public release.
        </p>
      </div>
    </div>

    <div class="columns is-centered">
      <div class="column">
        <table class="dataset-table">
          <thead>
            <tr>
              <th>Dataset</th>
              <th>Images</th>
              <th>RGB</th>
              <th>Depth</th>
              <th>Normal</th>
              <th>Mask</th>
              <th>Description</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td class="dataset-name">SynthCrack-42k</td>
              <td>42,165</td>
              <td class="checkmark">&#10003;</td>
              <td></td>
              <td></td>
              <td class="checkmark">&#10003;</td>
              <td>Baseline cube-based crack dataset (Unreal Engine)</td>
              <td><a href="#" class="btn-download">Download</a></td>
            </tr>
            <tr>
              <td class="dataset-name">SynthCrack-72k</td>
              <td>71,860</td>
              <td class="checkmark">&#10003;</td>
              <td></td>
              <td></td>
              <td class="checkmark">&#10003;</td>
              <td>Extended crack shapes + updated geometric parameterization</td>
              <td><a href="#" class="btn-download">Download</a></td>
            </tr>
            <tr>
              <td class="dataset-name">SynthCrack-ONE</td>
              <td>23,485</td>
              <td class="checkmark">&#10003;</td>
              <td class="checkmark">&#10003;</td>
              <td class="checkmark">&#10003;</td>
              <td class="checkmark">&#10003;</td>
              <td>Rhino-generated with refined crack-shape library</td>
              <td><a href="#" class="btn-download">Download</a></td>
            </tr>
            <tr>
              <td class="dataset-name">SynthDefect-Bridge</td>
              <td>2,291</td>
              <td class="checkmark">&#10003;</td>
              <td class="checkmark">&#10003;</td>
              <td class="checkmark">&#10003;</td>
              <td class="checkmark">&#10003;</td>
              <td>Bridge-scale multi-defect: crack, spalling, corrosion, efflorescence</td>
              <td><a href="#" class="btn-download">Download</a></td>
            </tr>
            <tr>
              <td class="dataset-name">RealDefect-Bridge</td>
              <td>TBD</td>
              <td class="checkmark">&#10003;</td>
              <td></td>
              <td></td>
              <td class="checkmark">&#10003;</td>
              <td>Real bridge inspection images for evaluation</td>
              <td><a href="#" class="btn-download">Download</a></td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</section>

<!-- ===== RESULTS ===== -->
<section class="section">
  <div class="container is-max-desktop">
    <div class="columns is-centered has-text-centered">
      <div class="column is-four-fifths">
        <h2 class="title is-3 section-title">Results</h2>
      </div>
    </div>

    <div class="columns is-centered">
      <div class="column is-four-fifths">

        <h3 class="title is-4">Multi-pass Outputs</h3>
        <p style="color: var(--text-secondary); margin-bottom: 1rem;">
          Each scene produces aligned color, depth, surface normal, and defect mask outputs from the same camera pose.
        </p>
        <!-- Replace: 4 images side by side (color, depth, normal, mask) -->
        <div class="gallery-row">
          <div style="flex:1; text-align:center;">
            <img src="static/images/placeholder.svg" alt="Color output">
            <p class="gallery-label">Color</p>
          </div>
          <div style="flex:1; text-align:center;">
            <img src="static/images/placeholder.svg" alt="Depth output">
            <p class="gallery-label">Depth</p>
          </div>
          <div style="flex:1; text-align:center;">
            <img src="static/images/placeholder.svg" alt="Normal output">
            <p class="gallery-label">Normal</p>
          </div>
          <div style="flex:1; text-align:center;">
            <img src="static/images/placeholder.svg" alt="Mask output">
            <p class="gallery-label">Mask</p>
          </div>
        </div>

        <h3 class="title is-4" style="margin-top: 2.5rem;">Realism Enhancement</h3>
        <p style="color: var(--text-secondary); margin-bottom: 1rem;">
          Unpaired image-to-image translation reduces the appearance gap between synthetic and real inspection imagery.
        </p>
        <!-- Replace: 3 images (synthetic, CycleGAN-enhanced, real reference) -->
        <div class="gallery-row">
          <div style="flex:1; text-align:center;">
            <img src="static/images/placeholder.svg" alt="Original synthetic">
            <p class="gallery-label">Synthetic</p>
          </div>
          <div style="flex:1; text-align:center;">
            <img src="static/images/placeholder.svg" alt="CycleGAN enhanced">
            <p class="gallery-label">CycleGAN Enhanced</p>
          </div>
          <div style="flex:1; text-align:center;">
            <img src="static/images/placeholder.svg" alt="Real reference">
            <p class="gallery-label">Real</p>
          </div>
        </div>

        <h3 class="title is-4" style="margin-top: 2.5rem;">Defect Predictions on Real Images</h3>
        <p style="color: var(--text-secondary); margin-bottom: 1rem;">
          Models trained on synthetic data transfer to real inspection imagery for crack segmentation and multi-defect recognition.
        </p>
        <!-- Replace: prediction examples (input image, prediction overlay) -->
        <div class="gallery-row">
          <div style="flex:1; text-align:center;">
            <img src="static/images/placeholder.svg" alt="Real image input">
            <p class="gallery-label">Input</p>
          </div>
          <div style="flex:1; text-align:center;">
            <img src="static/images/placeholder.svg" alt="Model prediction">
            <p class="gallery-label">Prediction</p>
          </div>
        </div>

      </div>
    </div>
  </div>
</section>

<!-- ===== BIBTEX ===== -->
<section class="section" id="BibTeX">
  <div class="container is-max-desktop content">
    <div class="bibtex-header">
      <h2 class="title is-3 section-title" style="margin-bottom: 0; padding-bottom: 0;">BibTeX</h2>
      <button class="copy-btn" onclick="copyBibTeX()" title="Copy to clipboard">
        <i class="fas fa-copy"></i>
        <span id="copy-text">Copy</span>
      </button>
    </div>
    <pre id="bibtex-code"><code>@article{hsu2026defectsynth,
  title={Beyond Cracks: Synthetic Image and Geometry Generation for Computer Vision Detection and Severity Assessment of Diverse Concrete Surface Defects},
  author={Hsu, Shun-Hsiang and Golparvar-Fard, Mani},
  journal={Preprint},
  year={2026},
  url={https://huhuman.github.io/rhino-defect-synth/}
}</code></pre>
  </div>
</section>

<!-- ===== ACKNOWLEDGEMENTS ===== -->
<section class="section hero is-light">
  <div class="container is-max-desktop">
    <div class="columns is-centered">
      <div class="column is-four-fifths">
        <h2 class="title is-4">Acknowledgements</h2>
        <div class="content" style="color: var(--text-secondary);">
          <p>
            This material is based upon work supported by the National Science Foundation (NSF) under Grant No. CMMI-2053935. This work used the Delta advanced computing resource, supported by the NSF (OAC 2005572) and the State of Illinois, a joint effort of the University of Illinois and the National Center for Supercomputing Applications (NCSA). Any findings, and conclusions or recommendations expressed in this material are those of the author(s) and do not necessarily reflect the views of the NSF or NCSA.
          </p>
        </div>
      </div>
    </div>
  </div>
</section>

<!-- ===== FOOTER ===== -->
<footer class="footer">
  <div class="container">
    <div class="columns is-centered">
      <div class="column is-8">
        <div class="content has-text-centered" style="color: var(--text-light); font-size: 0.9rem;">
          <p>
            This page was built using the <a href="https://github.com/eliahuhorwitz/Academic-project-page-template" target="_blank">Academic Project Page Template</a>,
            adopted from the <a href="https://nerfies.github.io" target="_blank">Nerfies</a> project page.
            Licensed under <a href="http://creativecommons.org/licenses/by-sa/4.0/" target="_blank">CC BY-SA 4.0</a>.
          </p>
        </div>
      </div>
    </div>
  </div>
</footer>

<script>
function copyBibTeX() {
  const code = document.getElementById('bibtex-code').innerText;
  navigator.clipboard.writeText(code).then(function() {
    const btn = document.querySelector('.copy-btn');
    const text = document.getElementById('copy-text');
    btn.classList.add('copied');
    text.textContent = 'Copied!';
    setTimeout(function() {
      btn.classList.remove('copied');
      text.textContent = 'Copy';
    }, 2000);
  });
}
</script>

</body>
</html>
```

- [ ] **Step 2: Verify locally**

```bash
python3 -m http.server 8000 --directory docs/
```

Open `http://localhost:8000` in a browser and verify:
- Hero section renders with title, authors, buttons
- All sections are visible and properly laid out
- Placeholder SVGs appear in image slots
- BibTeX copy button works
- Page is responsive (resize browser window)

- [ ] **Step 3: Commit**

```bash
git add docs/index.html
git commit -m "docs: add project page (GitHub Pages)"
```

---

### Task 7: Final review and cleanup commit

- [ ] **Step 1: Verify all files in place**

```bash
# Expected files
ls README.md README.zh-TW.md docs/index.html docs/PIPELINE.md docs/PIPELINE.zh-TW.md docs/static/css/index.css docs/static/images/placeholder.svg docs/static/gifs/.gitkeep
```

All 8 paths should exist.

- [ ] **Step 2: Verify no broken internal links in READMEs**

Check that:
- `README.md` links to `docs/PIPELINE.md` (relative from repo root)
- `README.zh-TW.md` links to `docs/PIPELINE.zh-TW.md`
- `docs/PIPELINE.md` language toggle points to `PIPELINE.zh-TW.md` (relative within docs/)
- `docs/PIPELINE.zh-TW.md` language toggle points to `PIPELINE.md`

- [ ] **Step 3: Enable GitHub Pages**

After pushing, go to repo Settings > Pages and set:
- Source: **Deploy from a branch**
- Branch: **main**, folder: **/docs**

The site will be live at `https://huhuman.github.io/rhino-defect-synth/`.
