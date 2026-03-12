# configs 參數總覽

[English](README.md) | [繁體中文](README.zh-TW.md)

本文件以表格為主，將設定參數對應到實際程式運作機制。

## 載入順序與覆蓋優先序

| 項目 | 運作機制 |
|---|---|
| `extends` | 由 `utils_loc.config.load_config` 遞迴讀取並 deep-merge，支援 `string` 或 `list[string]`。 |
| 預設值組合 | 透過 `extends` 明確組合 `*_defaults.yaml`、`*_defect_defaults.yaml`、`*_render.yaml` 到 base config。 |
| 衝突優先序 | `目前 config > extends 清單後面的項目 > extends 清單前面的項目`。 |

## 設定檔用途

| 檔案 | 用途 |
|---|---|
| `cube_base.yaml` | cube 基底組合（`cube_defaults + cube_defect_defaults + cube_render`）加上 `preparation`。 |
| `cube_render.yaml` | cube 的 render/view 區塊。 |
| `cube.local.yaml` | cube 本機覆蓋設定。 |
| `component_base.yaml` | component 基底組合（`component_defaults + component_defect_defaults + component_render`）加上 `preparation`。 |
| `component_render.yaml` | component 的 render/view 區塊。 |
| `component.local.yaml` | component 本機覆蓋設定。 |
| `cube_defaults.yaml` | cube 建模預設（`modeling.cube`）。 |
| `component_defaults.yaml` | component 建模預設（`modeling.component`）。 |
| `component_defect_defaults.yaml / cube_defect_defaults.yaml` | defect 設定區塊（`modeling.defect`）；runtime 目前只有 component 會執行 placement。 |

## Top-level 參數

| 參數路徑 | 型別 | 運作機制 | 預設來源 | 備註 |
|---|---|---|---|---|
| `extends` | `string | list[string]` | 載入父設定（可多個）並先行合併。 | 無 | 路徑在 `configs/` 下解析。 |
| `view_setup` | `dict` | `main.setup_render_view` 的圖層可見性控制區塊。 | 無 | 內含 `only_layers` 與 `hide_layers`。 |
| `view_setup.only_layers` | `string | list[string]` | `main.setup_render_view` 只顯示匹配層。 | 無 | 支援 `a::b` 階層匹配。 |
| `view_setup.hide_layers` | `string | list[string]` | 額外隱藏指定層。 | 無 | 在可見性流程後套用。 |
| `preparation` | `dict` | preparation stage 使用的材質/圖層/plugin 設定。 | base config | 通常由 `*_base.yaml` 提供。 |
| `preparation.materials` | `dict[str,str \| list[str]]` | 每個圖層可設定多個材質選項；preparation 會先過濾不存在者、隨機挑一個，並只匯入被選中的材質。 | base config | 交給 `create_layers`。 |
| `preparation.seed` | `int \| null` | preparation 材質選項隨機挑選的 seed。 | 無 | `null` 代表非固定。 |
| `preparation.colors` | `dict[str,str]` | `pipeline.prepare` 的圖層顏色映射。 | base config | 建立圖層時使用。 |
| `preparation.texture_materials` | `dict` | 紋理材質匯入設定。 | base config | 內含資料夾路徑與遞迴開關。 |
| `preparation.texture_materials.texture_root_dir` | `string | null` | 若設定，從資料夾建立材質。 | 無 | 可選。 |
| `preparation.texture_materials.recursive` | `bool` | 紋理遞迴掃描開關。 | `true` | 僅在設定 texture root 時生效。 |
| `preparation.material_search_paths` | `string \| list[string] \| null` | 額外材質資料夾（用材質名稱找檔案）。 | 無 | 可掛自訂 material library。 |
| `preparation.builtin_material_library` | `dict` | 內建材質庫查找設定。 | base config | 內含 category/subcategory 清單。 |
| `preparation.builtin_material_library.{category,subcategory1,subcategory2}` | `list[string]` | 內建材質資料夾查找路徑設定。 | `["Architectural"] / ["Wall"] / ["Concrete"]` | 三個 list 以同 index 配對；只使用 `min(len(category), len(subcategory1), len(subcategory2))` 組。 |
| `preparation.plugin_autoload.enabled` | `bool` | 是否啟用 Rhino 命令檢查與 plugin 自動載入。 | `true` | 設為 `false` 時跳過檢查。 |
| `preparation.plugin_autoload.path` | `string \| null` | preparation 自動載入 plugin 的檔案路徑（`.rhp` 或 `.dll`）。 | 無 | 僅在必要命令缺失時使用。 |
| `preparation.plugin_autoload.required_commands` | `string \| list[string]` | 進入後續流程前必須可用的 Rhino command 名稱。 | `["CaptureRenderChannels", "CaptureBaseColorMask"]` | 任一缺失即觸發 plugin 自動載入。 |
| `preparation.plugin_autoload.strict` | `bool` | 命令缺失或載入失敗時是否直接中止流程。 | `true` | 設 `false` 則警告後繼續。 |
| `preparation.plugin_autoload.verbose` | `bool` | 必要命令已存在時是否輸出資訊訊息。 | `true` | 不影響 strict 的錯誤行為。 |
| `modeling` | `dict` | 傳入 `pipeline.create_model`。 | config | 需包含 `strategy`。 |
| `rendering` | `dict` | 傳入 `pipeline.run_render`。 | config | render stage 必要。 |
| `nested_loop` | `dict` | 由 `main_cube_batch.run` 使用，用於 cube 批次資料產生。 | 無 | 可選；`main.py` 不會使用。 |

## Batch 迴圈參數（`nested_loop`，給 `main_cube_batch.py`）

| 參數路徑 | 型別 | 運作機制 | 預設來源 | 備註 |
|---|---|---|---|---|
| `renders_per_model` | `int` | 每個模型 iteration 要跑幾次 render iteration。 | `1` | 會至少夾到 `1`。 |
| `camera_arrangements` | `string \| list[string] \| null` | 每個 render iteration 額外要跑的相機 arrangement 序列覆蓋。 | 無 | 只支援 `grid`、`spherical`。若同時設兩者，單一 `render_iter` 會跑兩次完整 `preparation -> rendering`。 |
| `max_iter` | `int \| null` | 模型 iteration 上限。 | 無 | 實際次數 = `min(max_iter, available_iters)`；`null` 代表不限制。 |
| `seed` | `int \| null` | nested-loop 隨機化種子。 | 無 | 用於 rendering sampler 與 batch 隨機抽樣。 |
| `rendering_sampler` | `dict \| list \| scalar` | 每次 render iteration 對 `rendering` 的隨機覆蓋規格。 | 無 | 抽樣後必須可解析為 dict。 |
| `output_index_start` | `int` | batch 模式 `view_XXX` 命名起始 offset。 | `0` | 會和每次 capture 影格數累加，確保檔名連續。 |
| `preparation_scope` | `arrangement \| render_iter \| model_iter` | 控制 nested render loop 中材質清理 + preparation 的重跑頻率。 | `arrangement` | `arrangement` 為原本行為；`render_iter` 是穩定/效能折衷；`model_iter` 最保守、最省資源。 |
| `stability.enabled` | `bool` | batch 模式保守穩定化（等待/GC/重試）總開關。 | `true` | 設為 `false` 會關閉所有穩定化輔助。 |
| `stability.wait_after_reset_ms`, `stability.wait_after_preparation_ms`, `stability.wait_before_render_ms`, `stability.wait_after_render_ms`, `stability.wait_on_retry_ms` | `int` | 在 Rhino 重操作與重試路徑前後插入等待/idle。 | `20`, `40`, `40`, `60`, `400` | 有助於降低長迴圈中的時序性失敗。 |
| `stability.render_retry_count` | `int` | render pass 失敗後重試次數。 | `1` | 每次重試前會等待並執行 GC。 |
| `stability.gc_every_render_passes`, `stability.gc_every_model_iters` | `int` | nested loop 的 Python/.NET GC 週期。 | `1`, `1` | 設 `0` 可關閉該 GC 週期。 |
| `stability.clear_undo_every_model_iters` | `int` | 定期清 Rhino undo records 以降低記憶體壓力。 | `1` | 設 `0` 可停用。 |
| `stability.log_memory` | `bool` | 每個 model iteration 記錄 objects/layers/materials 與 private memory。 | `true` | 方便追查長跑記憶體成長。 |

`main_cube_batch.py` 目前執行流程：
- 每個模型 iteration：`reset -> preparation -> view_setup -> modeling`
- 每個渲染 iteration：會執行一或多次 `view_setup -> rendering`。
  材質清理與 preparation 的重跑頻率由 `nested_loop.preparation_scope` 控制；
  arrangement pass 次數由 `camera_arrangements` 決定。
- 穩定化設定可在重操作間插入等待、重試、GC 與 undo 清理。
- view index 會跨 iteration 連續，避免覆蓋前次輸出。

## Modeling：共用

| 參數路徑 | 型別 | 運作機制 | 預設來源 | 備註 |
|---|---|---|---|---|
| `modeling.strategy` | `cube | component` | 在 `pipeline.create_model` 選擇分支。 | 無 | 必填。 |
| `modeling.defect` | `dict` | 由 component 分支用來執行 `apply_defect_pipeline`；cube 分支目前忽略此區塊。 | `component_defect_defaults.yaml` + 覆蓋 | `cube_defect_defaults.yaml` 保留給設定組合與相容用途。 |

## Modeling：Cube

| 參數路徑 | 型別 | 運作機制 | 預設來源 | 備註 |
|---|---|---|---|---|
| `modeling.cube.cube_map_dir` | `string` | cube contour/crack map 輸入資料夾。 | `cube_defaults.yaml` | cube 必填。 |
| `modeling.cube.start_face_index` | `int` | cube 面索引偏移。 | `cube_defaults.yaml` | 可被 `main.run(start_face_index=...)` 覆蓋。 |
| `modeling.start_face_index` | `int` | pipeline 在 cube 分支吃的執行期覆蓋值。 | `main.run` 參數 | 若設定，優先於 `modeling.cube.start_face_index`。 |

## Modeling：Component (`modeling.component`)

| 參數路徑 | 型別 | 運作機制 | 預設來源 | 備註 |
|---|---|---|---|---|
| `seed` | `int | null` | component 建模本地 RNG 種子。 | `component_defaults.yaml` | `null` 代表非固定。 |
| `delete_centerline_curve` | `bool` | 建模後刪除輔助中心線。 | `component_defaults.yaml` | 幾何清理開關。 |
| `convert_polygons_to_surfaces` | `bool` | 將 polygon 轉 surface。 | `component_defaults.yaml` | 影響輸出物件型別。 |
| `keep_polygon_curves` | `bool` | 建 surface 後仍保留曲線。 | `component_defaults.yaml` | 偵錯常用。 |
| `centerline.*` | mixed | 控制中心線與 station 生成。 | `component_defaults.yaml` | 含 `span/num_base_pts/theta*`。 |
| `slab.*` | mixed | 控制 slab 尺寸與橫坡。 | `component_defaults.yaml` | |
| `parapet.*` | mixed | 控制 parapet 是否啟用與剖面尺寸。 | `component_defaults.yaml` | |
| `beam.enabled` | `bool` | 開關 beam 生成分支。 | `component_defaults.yaml` | 關閉時會用 slab fallback 點位。 |
| `beam.num_lines` | `int` | deck 寬向 beam 條數。 | `component_defaults.yaml` | 會影響 bearing 數量。 |
| `beam.section_key` | `string` | 指定 beam 剖面。 | `component_defaults.yaml` | 可被 `section_library_inch` 擴充。 |
| `beam.section_library_inch` | `dict` | 自訂 beam 剖面庫覆蓋。 | `component_defaults.yaml` | 與內建庫合併。 |
| `bearing.*` | mixed | 在 beam 下建立 bearing 實體。 | `component_defaults.yaml` | 尺寸/比例控制。 |
| `pier.enabled` | `bool` | 開關 pier 生成。 | `component_defaults.yaml` | `false` 就不建 pier。 |
| `pier.type` | `hammerhead | m_column` | 選擇 pier 幾何生成函式。 | `component_defaults.yaml` | 不支援值會報錯。 |
| `pier.count` | `int` | 自動選 anchor station 的數量。 | `component_defaults.yaml` | 只有 `anchor_indices` 無有效值時才生效。 |
| `pier.anchor_indices` | `list[int] | int | null` | 指定 pier station（最高優先）。 | `component_defaults.yaml` | 支援負索引。 |
| `pier.use_internal_stations_only` | `bool` | 自動選點時優先只用內部 station。 | `component_defaults.yaml` | 會影響 `count` 實際結果。 |
| `pier.H / V / W` | `float` | pier 主尺寸參數。 | `component_defaults.yaml` | 兩種 pier type 共用。 |
| `pier.hammerhead.*` | mixed | hammerhead 專用形狀參數。 | `component_defaults.yaml` | 僅 type=hammerhead 使用。 |
| `pier.m_column.*` | mixed | m_column 專用形狀參數。 | `component_defaults.yaml` | 僅 type=m_column 使用。 |
| `layers.{slab,parapet,beam,bearing,pier}` | `string` | 各構件輸出圖層名稱映射。 | `component_defaults.yaml` | 可用階層路徑。 |

## Modeling：Debug (`modeling.debug`)

| 參數路徑 | 型別 | 運作機制 | 預設來源 | 備註 |
|---|---|---|---|---|
| `surface_normals.*` | mixed | 繪製 component surface 法向箭頭（除錯用）。 | `component_defaults.yaml` | 僅 component 分支使用。 |
| `defect_normals.*` | mixed | 在 defect placement 過程繪製 defect 法向箭頭。 | `component_defect_defaults.yaml` | 預設圖層為 `debug::normal`。 |
| `defect_seeds.*` | mixed | 在放置成功點繪製 defect seed marker。 | `component_defect_defaults.yaml` | 預設圖層為 `debug::seed`，可依 type 分層。 |

## Modeling：Defect (`modeling.defect`)

| 參數路徑 | 型別 | 運作機制 | 預設來源 | 備註 |
|---|---|---|---|---|
| `enabled` | `bool` | component defect placement 的全域開關（可選）。 | `component_defect_defaults.yaml` | 設為 false 時 component defect stage 會跳過。 |
| `seed` | `int | null` | defect placement 本地 RNG 種子。 | `component_defect_defaults.yaml` | 不污染全域 random。 |
| `target_layers` | `list[str] | null` | 候選 surface 圖層過濾。 | `component_defect_defaults.yaml` | `null` 代表不過濾。 |
| `max_attempts_per_instance` | `int` | 單一缺陷實例最大嘗試次數。 | `component_defect_defaults.yaml` | 防止無限重試。 |
| `reference.*` | mixed | 候選點抽樣控制。 | `component_defect_defaults.yaml` | 含邊界距離限制。 |
| `random.*` | mixed | 共用 placement 隨機參數。 | `component_defect_defaults.yaml` | orientation/margin/offset。 |
| `surface_subtraction.normal_extrude_distance` | `float` | post-placement 表面切割 cutter 的法向擠出距離。 | `component_defect_defaults.yaml` | 套用於 crack/spalling/exposed_rebar 的 surface split。 |
| `layers.seeds` | `string` | seed marker 圖層。 | `component_defect_defaults.yaml` | 不存在會自動建立。 |
| `layers.geometry.*` | `dict[str,string]` | 各 defect 幾何輸出圖層。 | `component_defect_defaults.yaml` | 不存在會自動建立。 |
| `crack.enabled`, `crack.count` | `bool`, `int` | crack 放置開關與要求實例數。 | `component_defect_defaults.yaml` | 停用時 count 會被忽略。 |
| `crack.overview_csv_path` | `string | null` | 讀 crack overview rows，並由 `instance_mask_path` 反推 polygon JSON。 | `component_defect_defaults.yaml` | 支援 `units -> polygon` 路徑轉換。 |
| `crack.cs_weights` | `list[float]` | crack 嚴重度 CS 的加權隨機。 | `component_defect_defaults.yaml` | 順序為 `[CS1, CS2, CS3]`，預設 `[1,1,1]`。 |
| `crack.t1`, `crack.t2` | `float` | crack 寬度門檻（cm），供 CS 抽樣與嚴重度 metadata 使用。 | `component_defect_defaults.yaml` | 抽樣區間：CS1=`0.5*t1..t1`、CS2=`t1..t2`、CS3=`t2..5*t2`。 |
| `crack.d1_range`, `crack.delta_depth_range` | `list[float,float]` | 傳給 crack 建模的深度參數。 | `component_defect_defaults.yaml` | 與 CS 寬度抽樣分開。 |
| `crack.cs2_d1_threshold`, `crack.cs3_d1_threshold` | `float` | 無法取得寬度指標時的舊版 D1 嚴重度 fallback 門檻。 | `cube_defect_defaults.yaml` | 相容用途，非主要 crack CS 判斷。 |
| `crack.target_width_cm` | `float` | 保留於 cube defect defaults 的舊相容鍵。 | `cube_defect_defaults.yaml` | 目前 component defect placement 不使用。 |
| `efflore.enabled`, `efflore.count` | `bool`, `int` | efflore 放置開關與要求實例數。 | `component_defect_defaults.yaml` | 停用時 count 會被忽略。 |
| `efflore.overview_csv_path` | `string | null` | 讀 efflore overview rows 並解析每個實例的 polygon JSON。 | `component_defect_defaults.yaml` | 若找不到可用 shape 會跳過 efflore。 |
| `efflore.cs_weights` | `list[float]` | efflore CS 加權隨機。 | `component_defect_defaults.yaml` | 順序為 `[CS2, CS3]`，預設 `[1,1]`。 |
| `efflore.z_threshold` | `float` | efflore 候選面法向相對 XY 平面的最大仰角（度）。 | `component_defect_defaults.yaml` | 會先以 `abs(仰角)<=threshold` 篩 surface pool，再抽 reference points；預設 `5.0`。 |
| `efflore.span_range_cm` | `list[float,float]` | efflore 尺度抽樣範圍（cm），用於 px->world 正規化。 | `component_defect_defaults.yaml` | 也可用 `span_min_cm/span_max_cm` 或固定 `span_cm`。 |
| `efflore.fixed_thickness` | `float` | efflore 擠出厚度基準。 | `component_defect_defaults.yaml` | 幾何流程為先沿 +normal 偏移再沿 -normal 擠出。 |
| `spalling.enabled`, `spalling.count` | `bool`, `int` | spalling 放置開關與要求實例數。 | `component_defect_defaults.yaml` | 停用時 count 會被忽略。 |
| `spalling.overview_csv_path` | `string | null` | 讀 spalling overview rows 並解析每個實例的 polygon JSON。 | `component_defect_defaults.yaml` | 若找不到可用 shape 會跳過 spalling。 |
| `spalling.cs_weights` | `list[float]` | spalling CS 加權隨機。 | `component_defect_defaults.yaml` | 順序為 `[CS2, CS3]`，預設 `[1,1]`。 |
| `spalling.depth_threshold`, `spalling.diameter_threshold` | `float` | depth/diameter 的 CS2/CS3 抽樣門檻。 | `component_defect_defaults.yaml` | CS2 用 `0.5*threshold..threshold`；CS3 用 `threshold..2*threshold`。 |
| `spalling.depth_irregularity`, `spalling.min_bottom_area_ratio` | `float` | spall 腔體剖面控制。 | `component_defect_defaults.yaml` | 最深 ring 會保證底面比例下限。 |
| `spalling.rebar_enabled`, `spalling.rebar_probability`, `spalling.force_rebar`, `spalling.rebar.*` | mixed | rebar 放置與幾何控制。 | `component_defect_defaults.yaml` | 有 rebar 時 spall+rebar 會一起歸類到 `defect::exposed_rebar::*`。 |
| `shape_library.*` | mixed | 保留於 cube defect defaults 的舊 shape-library 相容區塊。 | `cube_defect_defaults.yaml` | 目前 component defect placement 不使用。 |
| `random.scale_min`, `random.scale_max` | `float` | 保留於 cube defect defaults 的舊隨機縮放參數。 | `cube_defect_defaults.yaml` | 目前 component defect placement 不使用。 |
| Cube defect scope | literal | cube 目前直接由六個面 map 生成 crack，並不執行 `modeling.defect`。 | `cube_defaults.yaml` | `cube_defect_defaults.yaml` 仍保留給相容與設定組合用途。 |

## Rendering 參數

| 參數路徑 | 型別 | 運作機制 | 預設來源 | 備註 |
|---|---|---|---|---|
| `output_dir` | `string` | 所有輸出通道根目錄。 | 無 | render stage 必填。 |
| `width`, `height` | `int | null` | 顯式輸出解析度。 | viewport size | 未設定則用 viewport。 |
| `max_length` | `int | null` | 最長邊約束（保持比例）。 | 無 | width/height 未設定時使用。 |
| `match_viewport_aspect` | `bool` | 若輸出比例與 viewport 不一致，是否自動修正輸出尺寸到同長寬比。 | `true` | 建議保留 `true`，可避免 depth/normal buffer 視角比例不一致。 |
| `background_wallpaper_dir` | `string | null` | 渲染前隨機背景貼圖。 | 無 | 可選。 |
| `output_basename_pattern` | `string | null` | frame 命名 pattern。 | 無 | 支援 `{output_idx}`, `{model_iter}`, `{render_iter}`。 |
| `output_basename_prefix` | `string | null` | 命名前綴 fallback。 | 無 | pattern 未設時使用。 |
| `output_index_offset` | `int` | 輸出索引位移。 | `0` | 批次接續常用。 |
| `model_iter`, `render_iter` | any | basename pattern 的格式化輸入。 | 無 | 可選。 |
| `outputs.scene.only_layers` | `string | list[string]` | color/depth/normal 的 scene 可見白名單。 | 無 | 支援階層匹配。 |
| `outputs.scene.hide_layers` | `string | list[string]` | color/depth/normal 的 scene 隱藏清單。 | 無 | 在白名單/預設後套用。 |
| `outputs.mask.only_layers` | `string | list[string]` | mask pass 可見白名單。 | 無 | 設定後只顯示這些層。 |
| `outputs.mask.hide_layers` | `string | list[string]` | mask pass 隱藏清單。 | 無 | 在 mask 可見性流程後套用。 |
| `lighting.sun.enabled` | `bool` | 是否配置太陽光。 | `true` | 關閉時不重設 sun。 |
| `lighting.sun.time_of_day` | `float | null` | 太陽時間，null 時隨機。 | random `5.0..19.0` | 傳入 Rhino sun setup。 |
| `lighting.sun.date/latitude/longitude/timezone/intensity/north` | mixed | 太陽光直通參數。 | runtime default | 可選。 |
| `lighting.skylight.enabled` | `bool` | 是否開啟 skylight。 | `true` | |
| `lighting.skylight.intensity` | `float` | skylight 強度。 | `0.25` | |

## Rendering Camera：共用

| 參數路徑 | 型別 | 運作機制 | 預設來源 | 備註 |
|---|---|---|---|---|
| `camera.strategy` | `cube | component` | 選擇相機生成分支。 | 無 | 必填。 |
| `camera.lens` | numeric | 傳入 `set_camera`。 | Rhino 當前/預設 | 可選。 |
| `camera.transition_frames` | `int` | `smooth_path=true` 時插值影格數。 | `0` | |
| `camera.smooth_path` | `bool` | 是否啟用平滑插值路徑。 | `false` | |

## Rendering Camera：Cube (`camera.cube`)

| 參數路徑 | 型別 | 運作機制 | 預設來源 | 備註 |
|---|---|---|---|---|
| `arrangement` | `grid | spherical` | 選擇 cube 相機取樣器。 | 無 | cube 相機必填。 |
| `points_per_side` | `int` | grid 每邊取樣數。 | `2` | 只在 grid 模式用。 |
| `sample_count` | `int` | spherical 取樣數。 | `24` | 只在 spherical 模式用。 |
| `distance_multiplier_min/max` | `float` | scene bbox 比例倍率範圍。 | `1.5 / 2.5` | 若 min>max 會自動交換。 |
| `sphere_angle_jitter_degrees` | `float` | spherical 角度抖動。 | `0.0` | spherical 專用。 |
| `direction_jitter_degrees` | `float` | 相機方向抖動。 | `10.0` | 經 `jitter_camera_poses` 套用。 |
| `position_jitter` | `float | null` | 絕對位置抖動。 | 無 | null 時改用 scale。 |
| `position_jitter_scale` | `float` | 相對 spacing 的位置抖動比例。 | `0.25` | `position_jitter` 為 null 時使用。 |

## Rendering Camera：Component (`camera.component`)

| 參數路徑 | 型別 | 運作機制 | 預設來源 | 備註 |
|---|---|---|---|---|
| `defects` | `list[{point,normal}]` | 直接提供缺陷 seed。 | 無 | 需與 record path 二擇一。 |
| `defect_record_path` | `string | null` | 從 defect record 載入缺陷。 | 無 | 有 `defects` 可不設。 |
| `defect_types` | `list[str] | str | null` | 載入後按 type 過濾。 | 無 | 只在 record 載入時用。 |
| `cameras_per_defect` | `int` | 每個缺陷產生相機數。 | `1` | 會至少夾到 1。 |
| `distance_min/max` | `float` | 缺陷到相機距離範圍。 | scene-scale `0.10 / 0.20` | 若 min>max 會自動交換。 |
| `normal_jitter_degrees` | `float` | 法向角度抖動。 | `10.0` | |
| `tangent_jitter` | `float` | 切向位移抖動。 | `0.0` | |
| `target_jitter` | `float` | target 點位抖動。 | `0.0` | |
| `direction_jitter_degrees` | `float` | 最終視線方向抖動。 | `0.0` | |
| `position_jitter` | `float | null` | 絕對位置抖動。 | 無 | null 時改用 scale。 |
| `position_jitter_scale` | `float` | 相對 spacing 的位置抖動比例。 | `0.0` | `position_jitter` 為 null 時使用。 |

## Pipeline fallback 與便捷行為

| 情境 | 運作機制 |
|---|---|
| component camera 沒提供 `defects`/record path | `pipeline.run_render()` 會嘗試吃最近一次 `modeling.defect.camera_defects`。 |
| 完全不寫 `modeling.defect` | component 不跑 defect 流程。 |
| 圖層名稱包含 `::` | 當作階層圖層處理（建立與匹配都支援）。 |

## 最小可跑範例

### Cube

```yaml
extends: cube_base.yaml
modeling:
  strategy: cube
  cube:
    cube_map_dir: "C:/path/to/crack_cube_maps"
rendering:
  output_dir: "C:/path/to/out"
  camera:
    strategy: cube
    cube:
      arrangement: grid
```

### Component

```yaml
extends: component_base.yaml
modeling:
  strategy: component
  component: {}
rendering:
  output_dir: "C:/path/to/out"
  camera:
    strategy: component
    component:
      defects:
        - point: [0, 0, 0]
          normal: [0, 0, 1]
```
