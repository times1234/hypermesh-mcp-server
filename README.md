# HyperMesh MCP Server

Local MCP server for driving Altair HyperMesh with generated Tcl scripts.

The MCP is intentionally geometry-rule based. It does not assign mesh strategies
by hard-coded component names from one model.

## Execution Modes

- Batch mode: run Tcl through `hmbatch.exe`.
- Visible GUI mode: open HyperMesh, source the generated GUI listener Tcl, then
  send Tcl into the visible session with `execute_tcl_gui`.

Visible GUI mode only changes where Tcl is executed. Strategy selection, Tcl
generation, and input/output paths remain explicit.

## Main Tools

- `locate_hypermesh`: find candidate HyperMesh batch and GUI executables.
- `check_hypermesh_connection`: verify batch startup.
- `create_gui_listener_tcl`: create a Tcl listener for an already opened GUI.
- `start_hypermesh_gui_listener`: try to launch visible HyperMesh with the GUI listener.
- `execute_tcl`: run raw Tcl through batch mode.
- `execute_tcl_gui`: run raw Tcl in the visible GUI listener session.
- `get_hypermesh_meshing_strategy`: return generic meshing rules and workflows.
- `get_meshing_rules`: return structured generic tetra/drag/spin rules.
- `classify_hypermesh_part_strategy`: classify a part by geometry features.
- `generate_surface_automesh_tcl`: generate simple surface automesh Tcl.
- `generate_surface_deviation_rtrias_tcl`: generate surface deviation + R-trias Tcl.
- `generate_guarded_drag_hex_tcl`: generate guarded drag-hex Tcl.
- `generate_guarded_spin_hex_tcl`: generate guarded spin-hex Tcl for a known true section.
- `get_cutsection_spin_workflow`: explain the generic cut-section spin workflow.
- `generate_cutsection_spin_hex_tcl`: generate cut-section spin Tcl for stepped or recessed revolved solids.

## Generic Strategy Rules

Use `classify_hypermesh_part_strategy` and geometry facts, not component names.

### Tetra

Use `tetra_surface_deviation_rtrias` for:

- flanges or flange-like bodies
- bodies with bolt holes, local holes, bosses, protrusions, ribs, grooves, cutouts, or non-sweepable topology
- ambiguous parts where a clean drag/spin source cannot be proven

Required checks:

- create 2D surface-deviation R-trias mesh first
- clean/check 2D aspect issues
- tetramesh per component/object
- check and locally repair/report volume quality

### Drag Hex

Use guarded drag only for simple straight extrusions or tubes with constant
section.

Preconditions:

- a real source face exists at one end of the extrusion
- corresponding logical edge groups are forced to matched seed counts
- the source face meshes as 100% quads

If these cannot be proven, use tetra.

### Spin Hex

Use guarded spin only when the selected source surface is already known to be a
true cross-section of a clean revolved solid.

Preconditions:

- source section is a real cross-section
- source section meshes as 100% quads
- spin result contains hex elements only

If the solid is stepped, recessed, grooved, or the source section is ambiguous,
use cut-section spin instead.

### Cut-Section Spin Hex

Use `generate_cutsection_spin_hex_tcl` for stepped/recessed/ambiguous revolved
solids.

Workflow:

1. Split the actual solid with `*body_splitmerge_with_plane` using a middle plane.
2. Detect newly created surfaces from the split.
3. Temporarily mesh each new surface.
4. Accept only all-quad surfaces whose shell nodes lie on the split plane.
5. Spin the accepted 2D section shells into 3D hex elements.
6. Delete only the temporary 2D seed shells.

Required inputs:

- `solid_id`
- `component_name`
- split plane normal and point
- spin axis and a point on the spin axis
- element size and spin density

## Quality Policy

Do not blindly refine the whole mesh to fix quality.

Preferred order:

1. Change strategy if the topology is wrong.
2. Try local 3D smooth/remesh.
3. Try sliver repair where applicable.
4. If bad volume elements remain, keep them and report their IDs.

Do not automatically delete unfixable quality-failed volume elements unless the
user explicitly asks.

## Configuration Example

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

Adjust paths for your workstation.

