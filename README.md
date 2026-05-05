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

Raw meshing Tcl is guarded by default. `execute_tcl` and `execute_tcl_gui` reject
direct meshing commands such as `*meshdragelements*`, `*set_meshedgeparams`,
`*meshspinelements*`, `*defaultmeshsurf_growth`, and `*tetmesh` unless the script
was produced by one of the MCP strategy generators. This prevents agents from
bypassing balanced drag seeding, cut-section validation, and gear-local
refinement rules.

Geometry probes are a special case: they may create temporary coarse shell
elements only when the script carries MCP probe identity, prints
`MCP_PROBE_*` lines, and deletes the temporary elements and nodes. Agents can
also call `run_geometry_probe_gui` or `run_geometry_probe` directly to avoid
mixing probe execution with final-mesh guards.

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
- `classify_hypermesh_model_parts`: classify every expected solid/component and fail if any object is skipped.
- `generate_geometry_probe_tcl`: create temporary coarse surface meshes to probe pure CAD geometry, then delete them.
- `run_geometry_probe_gui`: execute the temporary geometry probe directly in the visible GUI listener.
- `run_geometry_probe`: execute the temporary geometry probe in batch mode.
- `generate_surface_automesh_tcl`: generate simple surface automesh Tcl.
- `generate_surface_deviation_rtrias_tcl`: generate surface deviation + R-trias Tcl.
- `generate_gear_aware_tetra_tcl`: generate gear/tooth local-refinement tetra Tcl.
- `generate_guarded_drag_hex_tcl`: generate guarded drag-hex Tcl.
- `run_guarded_drag_hex_gui`: generate and execute guarded drag-hex in the visible GUI.
- `generate_guarded_spin_hex_tcl`: generate guarded spin-hex Tcl for a known true section.
- `run_guarded_spin_hex_gui`: generate and execute guarded spin-hex in the visible GUI.
- `get_cutsection_spin_workflow`: explain the generic cut-section spin workflow.
- `generate_cutsection_spin_hex_tcl`: generate cut-section spin Tcl for stepped or recessed revolved solids.
- `run_cutsection_spin_hex_gui`: generate and execute cut-section spin-hex in the visible GUI.

## Generic Strategy Rules

Use `classify_hypermesh_part_strategy` and geometry facts, not component names.
The intended order is:

1. Enumerate all solids/components in the model.
2. Try visual classification first. If screenshots or visible GUI inspection are
   enough to identify drag/spin/tetra/gear/bearing/housing behavior, use that
   judgment and do not run the probe.
3. If visual inspection is uncertain, or pure CAD Tcl queries cannot return
   bbox/type/dimension data, run one `generate_geometry_probe_tcl` script for
   all relevant solids. It temporarily creates a coarse surface mesh, emits
   `MCP_PROBE_SOLID` lines, and deletes the temporary shell elements and nodes.
4. Inspect every object once and record geometry facts for each one.
5. Run `classify_hypermesh_model_parts` with the full expected id list as a
   lightweight planning table. This is not a command to execute one Tcl script
   per object.
6. Build one combined Tcl script when practical.
7. In that combined script, attempt all structured hex candidates first:
   `drag`, `spin`, and `cut-section spin`.
8. If a hex candidate fails validation, queue that object for tetra fallback.
9. After all hex candidates have been attempted, mesh the tetra queue and all
   tetra-only objects.
10. For bearing/ring-like revolved bodies, do not stop after direct spin fails.
   Use a real cut plane through the rotation axis, mesh the true radial section,
   and spin that section before tetra fallback.

Names are labels only. Never decide that a part is flange/gear/bearing from the
component name alone.

Performance rule: avoid splitting the workflow into many `generate_*` and
`execute_tcl_gui` calls. Generate Tcl blocks, concatenate them, and send one
combined script to HyperMesh whenever possible.

### Geometry Probe

Use `generate_geometry_probe_tcl` only as a fallback when visual inspection is
not enough or CAD-only Tcl geometry queries return empty values. The probe:

- meshes each target solid's surfaces with a coarse temporary 2D mesh
- reads bbox and simple size/complexity data from the temporary elements
- prints parseable `MCP_PROBE_SOLID` and `MCP_PROBE_SURFACE` lines
- returns the same `MCP_PROBE_*` lines through the GUI listener socket response
- deletes the temporary probe elements and nodes before finishing

Probe output helps infer rough geometry facts such as size ratios, coarse
complexity, and whether a pure CAD solid can be treated as simple or complex.
It is not a replacement for final mesh generation and should not be run per
object when one combined probe script can cover all solids.

If an agent only needs probe data, prefer `run_geometry_probe_gui` in visible
GUI mode or `run_geometry_probe` in batch mode. These tools execute only the
probe script and return `probe_lines`, so they do not go through the same
final-mesh safety gate as raw Tcl. If `generate_geometry_probe_tcl` output is
sent through `execute_tcl_gui`, keep the generated MCP probe comments and
`MCP_PROBE_*` lines intact.

For drag source selection on pure CAD, use `MCP_PROBE_SURFACE` lines as the
source-face candidate table. Choose a likely planar end face whose `flat_axis`
matches the drag axis and whose center coordinate is at the min or max end of
the target solid. Do not guess a source surface id from component names.

### Tetra

Use `tetra_surface_deviation_rtrias` for:

- true geometry flanges or flange-like bodies
- bodies with bolt holes, local holes, bosses, protrusions, ribs, grooves, cutouts, or non-sweepable topology
- ambiguous parts where a clean drag/spin source cannot be proven

Required checks:

- create 2D surface-deviation R-trias mesh first
- clean/check 2D aspect issues
- tetramesh per component/object
- check and locally repair/report volume quality

Flange naming policy:

- A component named `flange` is not automatically a flange.
- A true flange needs geometric evidence such as a flat annular mounting plate,
  planar mounting face, and bolt-hole/mounting pattern.
- Open cages, bearing housings, ribbed supports, and large side-opening bodies
  should be named by physical role such as `open_housing`, `bearing_housing`, or
  `ribbed_support`, not `flange`, unless true mounting-flange geometry exists.

### Drag Hex

Use guarded drag only for simple straight extrusions or tubes with constant
section.

Preconditions:

- a real source face exists at one end of the extrusion
- corresponding logical edge groups are forced to matched seed counts
- the source face meshes as 100% quads

Pass `solid_id` when possible. The generator then validates that the generated
hex8 mesh bounding box fits the target solid. If the drag result is missing,
non-hex, or poorly fitted, it deletes invalid elements, retries once with the
same element size, and then falls back to tetra when `fallback_to_tetra` is
enabled.

Seed policy: if inner/outer preview counts or edge lengths differ greatly, pass
`preview_edge_seed_counts` or `source_edge_lengths`. When the largest/smallest
ratio is at least `seed_balance_ratio_threshold` (default 1.6), the generator
uses a balanced common count instead of forcing all source edges up to the
largest outer count.

Do not write naked Tcl with `*set_meshedgeparams` and `*meshdragelements*` for
drag workflows. The execution tools block that path by default. Use
`generate_guarded_drag_hex_tcl` or `run_guarded_drag_hex_gui`; otherwise the
balanced seed policy cannot be applied.

### Spin Hex

Use guarded spin only when the selected source surface is already known to be a
true cross-section of a clean revolved solid.

Preconditions:

- source section is a real cross-section
- source section meshes as 100% quads
- spin result contains hex elements only

Pass `solid_id` when possible so the generated mesh can be checked against the
target solid. Failed fit/non-hex results are cleaned, retried once with the same
element size, then sent to tetra fallback when enabled.

For pure CAD solids where HyperMesh cannot return a solid bbox, generated hex
workflows do not fail only because the fit bbox is unavailable. They keep valid
all-hex results and print that the mesh-solid fit check was skipped.

If the solid is stepped, recessed, grooved, or the source section is ambiguous,
use cut-section spin instead.

In visible GUI mode, prefer `run_guarded_spin_hex_gui` over generating Tcl and
sending it through raw `execute_tcl_gui`. The runner executes the trusted
MCP-generated workflow directly, including its guarded tetra fallback.

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

In visible GUI mode, prefer `run_cutsection_spin_hex_gui` over generating Tcl
and sending it through raw `execute_tcl_gui`. The runner executes the trusted
MCP-generated workflow directly, including its guarded tetra fallback.

Required inputs:

- `solid_id`
- `component_name`
- split plane normal and point
- spin axis and a point on the spin axis; this is required and must be on the
  real rotation axis, not merely any point on the split plane
- element size and spin density

The split plane must contain the spin axis. In practical terms, the split plane
normal should be nearly perpendicular to the spin axis. If the cut plane is
perpendicular to the axis and creates an annular transverse section, that is a
drag-style source section for a constant-section body, not a spin section.

The generator validates the spin result. If no valid 3D hex8 elements are
created, it deletes temporary section/invalid elements and retries once with the
same requested element size. It does not shrink/refine the hex mesh for the
retry. If the second attempt still fails, it falls back to tetra when
`fallback_to_tetra` is enabled.

The cut-section generator also considers existing section surfaces on the target
solid after a split. This helps when a model has already been split or when
HyperMesh does not create new surface IDs. If mapped quads fail, it can try a
quad-only section mesh mode with the same element size before falling back.

### Gear-Aware Tetra

Use `classify_hypermesh_part_strategy` from geometry facts only. Do not classify
gear regions from component names, file names, or natural-language labels.
Set one or more of these when geometry inspection shows a gear-like region:
`has_gear_teeth`, `has_helical_teeth`, `has_twisted_tooth_faces`,
`has_many_repeated_radial_teeth`, `has_periodic_outer_radius_variation`,
`has_outer_tooth_band`, `has_repeated_tooth_flanks`, `tooth_count`, or
`outer_radius_variation_ratio`.

Negative bearing/ring evidence wins over gear hints. If the part is a smooth
concentric ring, bearing race, or annular-groove-only body, set
`is_smooth_concentric_ring`, `has_bearing_race_grooves`, or
`has_annular_grooves_only`; the classifier must not treat it as a gear.

As a last-resort workflow aid, callers may set `name_hint_indicates_gear=True`
when the user has intentionally named a part as gear. This hint only asks the MCP
to inspect/refine possible tooth geometry; it does not replace geometry checks,
and it is still overridden by bearing/ring evidence.

Then use `generate_gear_aware_tetra_tcl`:

- pass `solid_id` and `component_name`
- pass `base_element_size` for shaft/hub surfaces
- pass `gear_surface_ids` for repeated tooth, flank, and root surfaces
- optionally pass `gear_element_size`; otherwise it uses
  `base_element_size * gear_size_factor`
- pass `gear_axis` (`x`, `y`, or `z`) so automatic tooth-band detection uses
  the correct shaft axis

If `gear_surface_ids` are not supplied, the script auto-detects the outer gear
band from surface radii using `gear_outer_band_fraction` and meshes that band
finer. This is meant to catch helical gears where tooth surfaces are
oblique/twisted rather than simple radial faces. If auto-detection finds nothing,
it falls back to uniform base-size tetra.

For automatic detection, prefer passing `geometry_confirms_gear_teeth=True` only
after geometry inspection sees tooth peaks/roots, repeated flanks, or twisted
helical tooth faces. If only the last-resort name hint is available, pass
`name_hint_indicates_gear=True`; the script will run cautious outer-band
detection, but this should not be used for bearing/ring geometry.

The intended behavior is local refinement only: tooth, flank, root, or detected
outer gear-band faces use `gear_element_size`; shaft, bore, hub, and non-tooth
faces keep `base_element_size`.

Do not run a raw uniform `*defaultmeshsurf_growth` + `*tetmesh` script for a part
whose geometry inspection indicates gear features. The execution tools block raw
tetra/surface-growth meshing by default; use `generate_gear_aware_tetra_tcl` for
gear-like geometry so the local tooth-band refinement rule is applied.

## Known Limitations

- Some bearing/ring solids still fall back to tetra even though a human can see
  they should be sweepable by cutting a radial section and spinning it. The
  current `generate_cutsection_spin_hex_tcl` requires HyperMesh to expose a
  usable all-quad true section after `*body_splitmerge_with_plane`; on some
  recessed bearing geometry it only produces invalid/non-quad sections, so the
  guarded workflow correctly falls back to tetra. Future work: add a more robust
  profile extraction path that derives ordered radial profile loops from solid
  edges instead of relying only on newly split surfaces.

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
