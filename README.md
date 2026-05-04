# HyperMesh MCP Server

这是本地 HyperMesh MCP 服务，用来让 Codex/聊天客户端通过 MCP 调用 Altair HyperMesh 执行 Tcl 网格脚本。

它支持两种执行方式：

- 后台 batch 模式：调用 `hmbatch.exe -tcl xxx.tcl`，不打开 HyperMesh 窗口，执行完直接保存 `.hm`。
- 可见 GUI 模式：打开 HyperMesh 窗口，在当前 GUI 会话里执行 Tcl。你可以看到加载模型、划分网格、组件变化和保存过程。

默认 HyperMesh 路径：

```text
F:\Program Files\Altair\2020\hwdesktop\hw\bin\win64\hmbatch.exe
F:\Program Files\Altair\2020\hwdesktop\hw\bin\win64\hw.exe
```

## MCP 工具

- `locate_hypermesh`: 查找本机 `hmbatch.exe` 和 `hw.exe` / `hwx.exe`
- `check_hypermesh_connection`: 检查 HyperMesh batch 是否能启动
- `create_gui_listener_tcl`: 生成可见 GUI 会话使用的 Tcl 监听脚本
- `start_hypermesh_gui_listener`: 启动可见 HyperMesh，并尝试自动加载 GUI 监听脚本
- `get_hypermesh_meshing_strategy`: 返回当前固化的网格划分策略
- `get_a_hm_meshing_plan`: 返回 `F:\a.hm` 当前模型专用的最新划分策略和最新输出文件
- `classify_hypermesh_part_strategy`: 按零件特征判断 tetra / drag / spin 策略
- `generate_surface_automesh_tcl`: 生成简单 surface automesh Tcl
- `generate_surface_deviation_rtrias_tcl`: 生成 surface deviation + R-trias 2D 网格 Tcl
- `generate_guarded_drag_hex_tcl`: 生成带全四边形检查的 drag 六面体 Tcl
- `generate_guarded_spin_hex_tcl`: 生成带全四边形截面检查的 spin 六面体 Tcl
- `execute_tcl`: 执行任意 HyperMesh Tcl 脚本
- `execute_tcl_gui`: 在已经打开的可见 HyperMesh GUI 会话里执行任意 Tcl 脚本
- `make_recorded_tcl_wrapper`: 读取 HyperMesh 录制 Tcl 并做字符串替换
- `automesh_surfaces`: 打开已有 `.hm`，对 surfaces 自动划分并另存
- `automesh_surfaces_gui`: 在可见 HyperMesh GUI 会话里打开已有 `.hm`，对 surfaces 自动划分并另存

## 可见 GUI 模式用法

推荐流程：

1. 调用 `start_hypermesh_gui_listener`。
2. HyperMesh 窗口会可见打开，并尝试自动加载 MCP GUI 监听脚本。
3. 后续调用 `execute_tcl_gui` 或 `automesh_surfaces_gui`，Tcl 会在这个可见窗口里执行。

如果当前 HyperMesh 版本没有自动加载 `-tcl` 启动参数，可以改用手动流程：

1. 调用 `create_gui_listener_tcl`，得到一个 `script_path`。
2. 手动打开 HyperMesh。
3. 在 HyperMesh 的 Tcl 命令窗口里 source 这个脚本，例如：

```tcl
source "F:/mcp/runs/hypermesh_mcp_YYYYMMDD_HHMMSS_PID.tcl"
```

4. 再调用 `execute_tcl_gui` 或 `automesh_surfaces_gui`。

GUI 模式只改变“执行位置”：从后台 `hmbatch` 变成可见 HyperMesh 窗口。网格策略、Tcl 生成逻辑、输入输出 `.hm` 文件路径仍然可以沿用原来的方式。

## 当前网格策略硬规则

1. 不使用 `solidmap`。
2. 法兰、带螺栓孔、带台阶凸缘、带局部孔/凸台/复杂切除的零件一律按 tetra 策略，不按筒体 drag。
3. tetra 策略必须先做 `surface deviation` 的 2D R-trias 网格，再质量检查，最后逐对象/逐 component 做 `tetramesh`。
4. R-trias 参考参数：growth rate `1.23`，min elem size `0.5`，max deviation `0.1`，max feature angle `15`，网格尺度参考 `b.hm`。
5. drag 只用于简单直筒/等截面拉伸体。必须先保证内外圆周及对应边的种子数量一致，源面必须是 100% 四边形；否则回退 tetra。不能只检查“都是四边形”，还要先把对应边种子数统一，例如 51/58 必须改成同一个目标值 51 或 58 后再划 2D、再 drag。
6. spin 用于真正的干净回转截面。截面 2D 网格必须是 100% 四边形；否则回退 tetra。
7. 不过度依赖 drag。合适的回转体优先考虑 spin；复杂凸台/法兰/孔系优先 tetra。
8. component 命名按物体语义命名，例如 `flange`、`bearing_6903_left`、`spacer_block_left_upper`，不要用 `hex_drag_*` / `tet_*` 这种网格类型命名。
9. 质量修复不要一味加密全局网格。优先考虑换策略、局部 3D smooth/remesh、sliver tetra repair；只有确认是单个零体积退化单元且不适合移动边界几何时，才删除该退化体单元。

## F:\a.hm 当前模型专用策略

最新输出文件：

```text
F:\a_meshed_strategy_v12_final.hm
```

按 tetra 划分的复杂/法兰/带孔/凸台类对象：

```text
flange_outer_fillet
cutout_body
rounded_flange_fillet
ring_spacer
boss_body
boss_cutout
bearing_1730_cover
main_flange
moved_face_housing
chamfered_flange_edge
bearing_6903_inner_ring_left
bearing_6903_outer_ring_left
bearing_6903_inner_ring_right
bearing_6903_outer_ring_right
```

允许 drag 的对象必须先通过源面 100% 四边形检查：

```text
extruded_plate_left
extruded_plate_right
thin_extruded_plate
bearing_1730_ring_left
bearing_1730_ring_right
spacer_block_left_upper
spacer_block_left_lower
spacer_block_right_upper
spacer_block_right_lower
```

允许 spin 的对象也必须先通过截面 100% 四边形检查：

```text
bearing_6903_left
bearing_6903_right
```

生成 guarded spin Tcl 时，用于干净回转体，截面必须能划成全四边形：

```text
调用 generate_guarded_spin_hex_tcl:
source_surface_id = 1819
element_size = 0.7
component_name = bearing_6903_left
axis = x
density = 96
```

## 使用示例

判断一个法兰：

```text
调用 classify_hypermesh_part_strategy:
part_name = flange
is_flange = true
has_bolt_holes = true
```

返回应为：

```text
strategy = tetra_surface_deviation_rtrias
```

生成 surface deviation R-trias Tcl：

```text
调用 generate_surface_deviation_rtrias_tcl:
element_size = 0.65
min_element_size = 0.5
max_deviation = 0.1
max_feature_angle = 15
growth_rate = 1.23
```

生成 guarded drag Tcl 时，只能用于简单直筒/等截面拉伸体，并且必须先保证对应边种子数一致：

```text
调用 generate_guarded_drag_hex_tcl:
source_surface_id = 301
drag_distance = 7.3
element_size = 0.7
component_name = tube_body
axis = z
matched_edge_groups = [[0, 2], [1, 3]]
target_density = 58
```

如果不传 `target_density`，MCP 会按源面外接尺寸和目标网格尺寸估算统一种子数，而不是写死 51 或 58。

## MCP 配置示例

```json
{
  "mcpServers": {
    "hypermesh": {
      "command": "python",
      "args": ["F:\\mcp\\hypermesh_mcp_server.py"],
      "env": {
        "HYPERMESH_BATCH_EXE": "F:\\Program Files\\Altair\\2020\\hwdesktop\\hw\\bin\\win64\\hmbatch.exe",
        "HYPERMESH_GUI_EXE": "F:\\Program Files\\Altair\\2020\\hwdesktop\\hw\\bin\\win64\\hw.exe"
      }
    }
  }
}
```

## 说明

HyperMesh Tcl 命令会随版本、模板和 solver profile 有差异。复杂模型建议先用策略工具判定零件类型，再用生成器产出 Tcl，最后用 `execute_tcl` 执行和检查报告。
## 2026-05-04 v17 notes

See `F:\mcp\a_hm_v17_cutsection_spin_notes.md` for the updated visible-GUI
workflow, the real cut-section spin method for `bearing_6903_left` and
`bearing_6903_right`, the validated v17 output, and the policy that unfixable
quality-failed volume elements are kept instead of deleted.
