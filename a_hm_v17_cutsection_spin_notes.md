# F:\a.hm v17 cut-section spin notes

Date: 2026-05-04

## What changed

The recommended workflow for this workstation is now visible GUI execution when
the user wants to watch HyperMesh operate:

1. Call `create_gui_listener_tcl`.
2. Open HyperMesh manually if auto-launch does not wake it up.
3. In the HyperMesh Tcl console, run:

```tcl
source "F:/mcp/runs/hypermesh_mcp_YYYYMMDD_HHMMSS_PID.tcl"
```

4. Run Tcl through `execute_tcl_gui`.

GUI mode only changes where the Tcl runs. The meshing strategy, Tcl script, and
input/output `.hm` paths remain explicit and unchanged.

## Correct 6903 bearing workflow

`bearing_6903_left` and `bearing_6903_right` must not use a guessed section,
side face, or end face as the spin source.

Correct method:

1. Start from `F:/a_meshed_strategy_v12.hm`.
2. Remove only the old incorrect elements in `bearing_6903_left` and
   `bearing_6903_right`.
3. Split each 6903 solid with a true middle cutting plane using
   `*body_splitmerge_with_plane`.
4. Inspect the newly created surfaces from the split.
5. Mesh each new surface temporarily and accept only surfaces whose shell nodes
   lie on the split plane and whose shell mesh is 100% quads.
6. Spin the accepted section shell mesh 360 degrees about the x axis.
7. Delete only the temporary 2D seed shell elements after spin.

The validated implementation is:

```text
F:\mcp\mesh_a_strategy_v17_cutsection_spin.tcl
```

Validated output:

```text
F:\a_meshed_strategy_v17_cutsection_spin.hm
F:\a_meshed_strategy_v17_cutsection_spin_report.txt
```

`F:\a_meshed_strategy_v12.hm` is only an intermediate base model. It still may
contain the old generic guessed-section spin result for the 6903 bearings. The
final model for inspection must be:

```text
F:\a_meshed_strategy_v17_cutsection_spin.hm
```

Validated result:

```text
bearing_6903_left  = 17280 hex8 elements
bearing_6903_right = 17280 hex8 elements
```

## User-recorded split reference

The user manually cut the right 6903 bearing in HyperMesh. The recorded command
file was:

```text
C:\Users\qcyti\Documents\command1.tcl
```

Key command:

```tcl
*createmark solids 1 23
*createplane 1 0 -0.831039379 -0.556213583 -55.8785515 8.02783775 1035.87476
*body_splitmerge_with_plane solids 1 1
```

This is the model-approved example of the more general rule: for stepped,
recessed, or otherwise ambiguous revolved solids, the spin section must come
from a real solid split. The generic MCP tool for this pattern is now
`generate_cutsection_spin_hex_tcl`. `generate_guarded_spin_hex_tcl` remains for
cases where the selected surface is already known to be a true cross-section.

## Quality policy

Do not automatically delete unfixable quality-failed volume elements.

Allowed:

- Try local 3D smooth/remesh.
- Try sliver tetra repair where applicable.
- Log remaining bad element IDs.

Required:

- If bad elements still cannot be repaired, keep them in the model.
- Delete quality-failed elements only if the user explicitly asks.

The updated repair script is:

```text
F:\mcp\repair_v12_bad_vol.tcl
```
