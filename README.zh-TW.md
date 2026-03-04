# rhino-defect-synth

[English](README.md) | [繁體中文](README.zh-TW.md)

這是一套在 Rhino Python 中執行的合成損害建模與多通道渲染流程。

## 目前狀態
已實作並串接到主流程的功能：
- `cube` 建模：由 contour JSON 建立立方體面與裂縫幾何。
- `component`（橋梁）建模：可參數化生成 slab/parapet/beam/bearing/pier。
- 統一損害放置流程：`crack`、`efflore`、`exposed_rebar`（spall + rebar）。
- 兩種相機策略：`cube` 與 `component`。
- 多通道輸出：color、depth、normal、mask，以及線性 `.pfm`（depth/normal）。

## 需求
- Rhino 8 (Windows) 並啟用 Python scripting。
- Rhino 內可用 Python 模組：
  - `PyYAML`（`utils_loc/config.py` 使用）
  - `numpy`（cube 建模工具使用）
- 若要輸出線性深度/法向通道，需使用此 repo 的 `rhino_channels_plugin`：
  - `depth_buffer/*.pfm`
  - `normal_buffer/*.pfm`

外掛說明請見：`rhino_channels_plugin/README.md`。

## 入口腳本
### `main.py`
以 stage 為單位執行流程：

```python
import main
main.run(
    config_name="cube_render.yaml",
    stages=["load_config", "preparation", "view_setup", "modeling", "rendering"],
    skip=[],
    start_face_index=0,
    show_cameras=False,
    print_timings=True,
)
```

### `main_nested.py`
資料集用巢狀迴圈（`model loop x render loop`）。
目前限制：只支援 `modeling.strategy: cube`。

```python
import main_nested
main_nested.run(
    config_name="cube_render.yaml",
    renders_per_model=4,
    start_face_index=0,
    faces_per_model=6,
    seed=42,
    show_cameras=False,
    print_timings=True,
)
```

### `main_demo.py`
只跑 demo：材質、光照、相機佈局可視化。

## Pipeline Stages（`main.py`）
執行順序：
- `reset`
- `load_config`
- `preparation`
- `view_setup`
- `modeling`
- `rendering`

相依性：
- `preparation`、`modeling`、`rendering` 都需要 `load_config`。

## 設定系統
設定檔位於 `configs/`，由 `utils_loc.config.load_config(config_name)` 載入。

支援 `extends`（例如 `cube_render.yaml` extends `cube_base.yaml`）。

合併規則：
- 僅做 top-level 淺層合併。
- 子設定覆寫時，巢狀區塊會整塊被取代。

## 主要設定區塊
### 1) `preparation`
由 `utils_loc/pipeline.py::prepare()` 使用：
- 匯入渲染材質
- 可選：從 texture 目錄建立材質
- 重建圖層並套用圖層材質/顏色

### 2) `modeling`
由 `utils_loc/pipeline.py::create_model()` 使用。

支援策略：
- `strategy: cube`
  - 必要：`cube_map_dir`
  - 可選：`start_face_index`（由 `main.run` 注入）
  - 可選：`damage`（統一損害放置）
- `strategy: component`
  - 使用 `component` 區塊（`utils_loc/component_modeling.py`）
  - 可選：`damage`（統一損害放置）

#### Component 建模重點
`utils_loc/component_modeling.py::create_bridge_component()` 支援：
- 中心線控制（`span`、`theta`、`use_curve` 等）
- slab/parapet 參數
- beam section library 與梁數
- bearing 與 pier（`hammerhead` 或 `m_column`）
- 將生成 polygon 轉為 surface
- 抽樣 reference points 供損害放置

回傳結果包含：
- `surfaces`、`polylines`、`solids`
- `objects_by_component`
- `reference_points`、`reference_sizes`、`reference_normals`

#### 統一損害建模（`modeling.damage`）
`utils_loc/damage_modeling.py::apply_damage_pipeline()` 支援：
- 損害型別：
  - `crack`
  - `efflore`
  - `exposed_rebar`（以 `spall + rebar` 方式建模）
- 共用 shape library 讀取（cube contour JSON 與一般 polygon JSON）
- 以以下工具建立候選點：
  - `utils_loc.defect_modeling.get_surfaces`
  - `utils_loc.defect_modeling.get_reference_points`
- 依邊界條件限制 random scale/orientation
- 實例紀錄與可選 JSON 輸出（`record_output_path`）
- 萃取 `camera_defects` 作為 component 相機 seed

`crack` 幾何透過 `utils_loc/crack_modeling.py::create_crack()` 共用，並可配置深度範圍、圖層與清理行為。

### 3) `rendering`
由 `utils_loc/pipeline.py::run_render()` 與 `utils_loc/render.py` 使用。

必要欄位：
- `output_dir`
- `camera`

常用欄位：
- `width` / `height`
- `max_length`（未明確指定 width/height 時使用）
- `background_wallpaper_dir`
- `lighting.sun` / `lighting.skylight`
- `camera.strategy`: `cube` 或 `component`
- `camera.lens`
- `camera.smooth_path`
- `camera.transition_frames`

#### 相機策略：`cube`
- `camera.cube.arrangement`: `grid` 或 `spherical`
- `distance_multiplier_min` / `distance_multiplier_max`
- jitter 參數：
  - `direction_jitter_degrees`
  - `position_jitter` 或 `position_jitter_scale`
- 各排列專屬：
  - grid：`points_per_side`
  - spherical：`sample_count`、`sphere_angle_jitter_degrees`

#### 相機策略：`component`
- 可直接提供 seed：
  - `camera.component.defects: [{point: [x,y,z], normal: [nx,ny,nz]}, ...]`
- 或由損害紀錄載入：
  - `camera.component.defect_record_path`
  - 可選 `camera.component.defect_types`
- 抽樣參數：
  - `cameras_per_defect`
  - `distance_min` / `distance_max`
  - `normal_jitter_degrees`
  - `tangent_jitter`
  - `target_jitter`
  - 最後額外 jitter：`direction_jitter_degrees`、`position_jitter`、`position_jitter_scale`

流程行為：
- 若 `camera.strategy=component` 且 config 未提供 defects/record path，`pipeline.run_render()` 會嘗試自動使用本次 `modeling.damage` 產生的缺陷點。

#### Mask 圖層控制
`rendering.outputs.mask` 支援：
- `only_layers`：只顯示指定圖層來輸出 mask
- `hide_layers`：輸出 mask 時隱藏指定圖層

### 4) `nested_loop`（`main_nested.py`）
可選區塊，用於每個 cube 模型產生多組隨機渲染：
- `renders_per_model`
- `seed`
- `layer_material_choices`
- `rendering_sampler`

## 輸出結構
每個相機姿態的輸出在：

`<output_dir>/`
- `color/<basename>.png`
- `depth/<basename>.png`
- `normal/<basename>.png`
- `mask/<basename>.png`
- `depth_buffer/<basename>.pfm`
- `normal_buffer/<basename>.pfm`

預設 `basename` 為 `view_XXX`。nested run 可覆寫命名格式。

## 圖層管理重點
- 支援階層式圖層路徑（例如 `defects::mask::crack`），會自動建立。
- 損害流程分離：
  - 幾何圖層（`defects::geometry::*`）
  - mask 圖層（`defects::mask::*`）
  - seed 點圖層（`defects::seeds`）
- 方便透過 hide/show layer 進行 mask annotation capture。

## 設定檔範例
- Cube 渲染：`configs/cube_render.yaml`
- Component 渲染（含可選 damage pipeline）：`configs/component_render.yaml`
- Base 材質/圖層設定：
  - `configs/cube_base.yaml`
  - `configs/component_base.yaml`

## 專案結構
- `main.py`：stage runner
- `main_nested.py`：巢狀模型/渲染迴圈（cube）
- `main_demo.py`：demo 工具
- `configs/`：YAML 設定檔
- `utils_loc/pipeline.py`：流程編排（`prepare`、`create_model`、`run_render`、`run_render_demo`）
- `utils_loc/cube_modeling.py`：cube 幾何與 contour 映射
- `utils_loc/component_modeling.py`：可配置橋梁元件建模
- `utils_loc/damage_shapes.py`：共用 shape 解析/載入
- `utils_loc/damage_modeling.py`：統一損害放置與紀錄
- `utils_loc/crack_modeling.py`：共用 crack 幾何生成
- `utils_loc/defect_modeling.py`：surface/reference-point helper
- `utils_loc/render.py`：相機生成與渲染流程
- `utils_loc/outputs.py`：color/depth/normal/mask/channel 輸出
- `utils_loc/layers.py`、`utils_loc/lighting.py`、`utils_loc/camera.py`：工具模組
- `rhino_channels_plugin/`：線性深度/法向輸出 Rhino 外掛
