set output_path "F:/a_meshed_strategy_v12.hm"
set report_path "F:/a_meshed_strategy_v12_report.txt"
set fh [open $report_path "w"]

proc mlog {fh msg} { puts $msg; puts $fh $msg; flush $fh }
proc mcount {entity mark} { if {[catch {hm_marklength $entity $mark} c]} { return 0 }; return $c }
proc safe_getvalue {entity id dataname} {
    if {[catch {hm_getvalue $entity id=$id dataname=$dataname} value]} { return "" }
    return $value
}
proc rename_comp {cid new_name} {
    if {$cid == ""} { return }
    catch {*setvalue comps id=$cid name=$new_name}
}
proc solid_comp {sid} {
    return [safe_getvalue solids $sid collector]
}
proc cfg_name {cfg} {
    switch -- $cfg {
        103 {return "tria3"}
        104 {return "quad4"}
        106 {return "tria6"}
        108 {return "quad8"}
        204 {return "tetra4"}
        205 {return "pyramid5"}
        206 {return "penta6"}
        208 {return "hex8"}
        210 {return "tetra10"}
        default {return "cfg$cfg"}
    }
}
proc semantic_name {sid} {
    array set names {
        1 flange_outer_fillet
        2 extruded_plate_left
        3 extruded_plate_right
        4 cutout_body
        5 rounded_flange_fillet
        6 thin_extruded_plate
        7 ring_spacer
        8 boss_body
        9 boss_cutout
        10 bearing_1730_ring_left
        11 bearing_1730_ring_right
        12 bearing_1730_cover
        13 main_flange
        14 moved_face_housing
        15 chamfered_flange_edge
        16 spacer_block_left_upper
        17 spacer_block_left_lower
        18 spacer_block_right_upper
        19 spacer_block_right_lower
        20 bearing_6903_left
        21 bearing_6903_inner_ring_left
        22 bearing_6903_outer_ring_left
        23 bearing_6903_right
        24 bearing_6903_inner_ring_right
        25 bearing_6903_outer_ring_right
    }
    if {[info exists names($sid)]} { return $names($sid) }
    return "part_$sid"
}
proc part_size {sid mode} {
    if {$mode eq "hex"} {
        if {$sid == 10 || $sid == 11} { return 0.50 }
        if {$sid == 6} { return 0.55 }
        return 0.70
    }
    if {$sid == 1 || $sid == 14} { return 0.55 }
    if {$sid == 13} { return 0.62 }
    if {$sid == 20 || $sid == 21 || $sid == 22 || $sid == 23 || $sid == 24 || $sid == 25} { return 0.55 }
    if {$sid == 7 || $sid == 12} { return 0.58 }
    return 0.65
}
proc uniform_drag_face_density {source elem_size} {
    *createmark surfaces 2 $source
    if {[catch {hm_getboundingbox surfaces 2 0 0 0} bb]} { return "" }
    set dx [expr {abs([lindex $bb 3] - [lindex $bb 0])}]
    set dy [expr {abs([lindex $bb 4] - [lindex $bb 1])}]
    set dz [expr {abs([lindex $bb 5] - [lindex $bb 2])}]
    set dims [lsort -real [list $dx $dy $dz]]
    # Drag source faces must be mapped with the same seed count on every logical
    # edge. Pick that count from the face's largest in-plane dimension and the
    # requested size, not from a hard-coded 51/58 preview value.
    set major [lindex $dims 2]
    if {$major <= 0 || $elem_size <= 0} { return "" }
    set density [expr {int(round($major / $elem_size))}]
    if {$density < 4} { set density 4 }
    if {$density > 120} { set density 120 }
    return $density
}
proc choose_source_surface {sid axis side} {
    *createmark solids 2 $sid
    if {[mcount solids 2] == 0} { return "" }
    set sb [hm_getboundingbox solids 2 0 0 0]
    set target [expr {$side eq "min" ? [lindex $sb $axis] : [lindex $sb [expr {$axis + 3}]]}]
    set dim [expr {abs([lindex $sb [expr {$axis + 3}]] - [lindex $sb $axis])}]
    set tol [expr {max(0.001, $dim * 0.02)}]
    set best ""
    set best_area -1.0
    *createmark surfs 1 "by solids" $sid
    foreach sf [hm_getmark surfs 1] {
        *createmark surfs 2 $sf
        if {[catch {hm_getboundingbox surfs 2 0 0 0} bb]} { continue }
        set amin [lindex $bb $axis]
        set amax [lindex $bb [expr {$axis + 3}]]
        set span [expr {abs($amax - $amin)}]
        set coord [expr {$side eq "min" ? $amin : $amax}]
        if {$span <= $tol && abs($coord - $target) <= $tol} {
            set area 0.0
            catch {set area [hm_getareaofsurface surface $sf]}
            if {$area > $best_area} {
                set best $sf
                set best_area $area
            }
        }
    }
    return $best
}
proc mark_elems_list {elem_list mark_id} {
    if {[llength $elem_list] == 0} {
        *clearmark elems $mark_id
        return
    }
    eval *createmark elems $mark_id $elem_list
}
proc delete_elems_list {elem_list} {
    if {[llength $elem_list] == 0} { return }
    mark_elems_list $elem_list 1
    catch {*deletemark elems 1}
}
proc source_shell_stats {source_surface} {
    *createmark elems 1 "by surface" $source_surface
    set shells [hm_getmark elems 1]
    set total [llength $shells]
    set quads 0
    set trias 0
    foreach eid $shells {
        set cfg [safe_getvalue elems $eid config]
        if {$cfg == 104 || $cfg == 108} { incr quads }
        if {$cfg == 103 || $cfg == 106} { incr trias }
    }
    return [list $shells $total $quads $trias]
}
proc mesh_source_quads {source elem_size {target_density ""}} {
    *createmark surfaces 1 $source
    catch {*setedgedensitylinkwithaspectratio -1}
    *setedgedensitylink 1
    *interactiveremeshsurf 1 $elem_size 1 1 2 1 1
    *set_meshfaceparams 0 5 1 0 0 1 0.5 1 1
    if {$target_density != ""} {
        foreach edge_index {0 1 2 3} {
            catch {*set_meshedgeparams $edge_index $target_density 1 0 0 0 $elem_size 0 0}
        }
    }
    *automesh 0 5 1
    *storemeshtodatabase 1
    *ameshclearsurface
}
proc check_2d_aspect_mark {limit mark_id} {
    *clearmark elems 2
    if {[mcount elems $mark_id] > 0} {
        catch {*elementtestaspect elems $mark_id $limit 2 2 0 "MCP 2D Aspect"}
    }
    return [mcount elems 2]
}
proc check_vol_skew_mark {limit mark_id} {
    *clearmark elems 2
    if {[mcount elems $mark_id] > 0} {
        catch {*elementtestvolumetricskew elems $mark_id $limit 2 4 0 "MCP Vol Skew"}
    }
    return [mcount elems 2]
}
proc drag_hex_solid {fh sid elem_size} {
    set comp [solid_comp $sid]
    set name [semantic_name $sid]
    rename_comp $comp $name
    *currentcollector components $name
    *createmark elems 1 "by comp id" $comp
    set old_comp_elems [hm_getmark elems 1]
    if {[llength $old_comp_elems] > 0} {
        catch {*deletemark elems 1}
        mlog $fh "drag_deleted_existing_comp_elems solid=$sid name=$name count=[llength $old_comp_elems]"
    }
    *createmark solids 2 $sid
    if {[mcount solids 2] == 0} {
        mlog $fh "drag_skip solid=$sid name=$name reason=missing"
        return 0
    }
    set sb [hm_getboundingbox solids 2 0 0 0]
    set dx [expr {abs([lindex $sb 3] - [lindex $sb 0])}]
    set dy [expr {abs([lindex $sb 4] - [lindex $sb 1])}]
    set dz [expr {abs([lindex $sb 5] - [lindex $sb 2])}]
    set dims [list $dx $dy $dz]
    set axis 0
    set distance $dx
    for {set i 1} {$i < 3} {incr i} {
        set d [lindex $dims $i]
        if {$d > 0 && $d < $distance} { set axis $i; set distance $d }
    }
    set source [choose_source_surface $sid $axis min]
    if {$source == "" || $distance <= 0} {
        mlog $fh "drag_fallback_tet solid=$sid name=$name reason=no_planar_source"
        return 0
    }
    *createmark elems 1 "by surface" $source
    set old_source_elems [hm_getmark elems 1]
    if {[llength $old_source_elems] > 0} {
        catch {*deletemark elems 1}
        mlog $fh "drag_deleted_existing_source_elems solid=$sid name=$name source=$source count=[llength $old_source_elems]"
    }
    set target_density [uniform_drag_face_density $source $elem_size]
    mesh_source_quads $source $elem_size $target_density
    set stats [source_shell_stats $source]
    set shells [lindex $stats 0]
    set total [lindex $stats 1]
    set quads [lindex $stats 2]
    set trias [lindex $stats 3]
    if {$total == 0 || $quads != $total} {
        mlog $fh "drag_fallback_tet solid=$sid name=$name source=$source source_shells=$total quads=$quads trias=$trias reason=source_not_all_quads"
        delete_elems_list $shells
        return 0
    }
    set layers [expr {int(ceil($distance / $elem_size))}]
    if {$layers < 1} { set layers 1 }
    set vx 0; set vy 0; set vz 0
    if {$axis == 0} { set vx 1 }
    if {$axis == 1} { set vy 1 }
    if {$axis == 2} { set vz 1 }
    *createvector 1 $vx $vy $vz
    set err ""
    if {[catch {*meshdragelements2 1 1 $distance $layers 0 0.0 0} err]} {
        mlog $fh "drag_fallback_tet solid=$sid name=$name source=$source reason=drag_failed err={$err}"
        delete_elems_list $shells
        return 0
    }
    delete_elems_list $shells
    *createmark elems 1 "by comp id" $comp
    set vol_bad [check_vol_skew_mark 0.99 1]
    mlog $fh "drag_hex solid=$sid name=$name source=$source axis=$axis distance=$distance size=$elem_size layers=$layers uniform_face_edge_density={$target_density} quads=$quads vol_skew_gt_0.99=$vol_bad"
    return 1
}
proc spin_hex_solid {fh sid elem_size axis density} {
    set comp [solid_comp $sid]
    set name [semantic_name $sid]
    rename_comp $comp $name
    *currentcollector components $name
    *createmark solids 2 $sid
    if {[mcount solids 2] == 0} {
        mlog $fh "spin_skip solid=$sid name=$name reason=missing"
        return 0
    }
    set sb [hm_getboundingbox solids 2 0 0 0]
    set source [choose_source_surface $sid $axis min]
    if {$source == ""} {
        mlog $fh "spin_fallback_tet solid=$sid name=$name reason=no_cross_section_source"
        return 0
    }
    mesh_source_quads $source $elem_size
    set stats [source_shell_stats $source]
    set shells [lindex $stats 0]
    set total [lindex $stats 1]
    set quads [lindex $stats 2]
    set trias [lindex $stats 3]
    if {$total == 0 || $quads != $total} {
        mlog $fh "spin_fallback_tet solid=$sid name=$name source=$source source_shells=$total quads=$quads trias=$trias reason=source_not_all_quads"
        delete_elems_list $shells
        return 0
    }
    set cx [expr {([lindex $sb 0] + [lindex $sb 3]) / 2.0}]
    set cy [expr {([lindex $sb 1] + [lindex $sb 4]) / 2.0}]
    set cz [expr {([lindex $sb 2] + [lindex $sb 5]) / 2.0}]
    set nx 0; set ny 0; set nz 0
    if {$axis == 0} { set nx 1 }
    if {$axis == 1} { set ny 1 }
    if {$axis == 2} { set nz 1 }
    *createplane 1 $nx $ny $nz $cx $cy $cz
    set err ""
    if {[catch {*meshspinelements2 1 1 360 $density 1 0.0 0} err]} {
        mlog $fh "spin_fallback_tet solid=$sid name=$name source=$source reason=spin_failed err={$err}"
        delete_elems_list $shells
        return 0
    }
    delete_elems_list $shells
    *createmark elems 1 "by comp id" $comp
    set vol_bad [check_vol_skew_mark 0.99 1]
    mlog $fh "spin_hex solid=$sid name=$name source=$source axis=$axis size=$elem_size density=$density quads=$quads vol_skew_gt_0.99=$vol_bad"
    return 1
}
proc surface_deviation_rtrias {sid elem_size} {
    *createmark surfs 1 "by solids" $sid
    set max_size [expr {max($elem_size * 1.8, 0.75)}]
    set min_size 0.50
    if {$elem_size < 0.55} { set min_size [expr {$elem_size * 0.60}] }
    *createarray 3 0 0 0
    set err ""
    if {[catch {*defaultmeshsurf_growth 1 $elem_size 3 3 2 1 1 1 35 0 $min_size $max_size 0.1 15 1.23 1 3 1 0} err]} {
        return [list 0 $err]
    }
    return [list 1 ""]
}
proc tetramesh_solid {fh sid elem_size} {
    set comp [solid_comp $sid]
    set name [semantic_name $sid]
    rename_comp $comp $name
    *currentcollector components $name
    *createmark solids 2 $sid
    if {[mcount solids 2] == 0} {
        mlog $fh "tet_skip solid=$sid name=$name reason=missing"
        return 0
    }
    set surf_result [surface_deviation_rtrias $sid $elem_size]
    if {[lindex $surf_result 0] == 0} {
        mlog $fh "surface_deviation_failed solid=$sid name=$name err={[lindex $surf_result 1]}"
        return 0
    }
    *createmark elems 1 "by comp id" $comp
    set shell_ids [hm_getmark elems 1]
    set aspect_bad_before [check_2d_aspect_mark 10.0 1]
    if {$aspect_bad_before > 0} {
        *createmark comps 1 $comp
        catch {*triangle_clean_up comps 1 "aspect=10.0 height=0.2"}
    }
    *createmark elems 1 "by comp id" $comp
    set shell_ids [hm_getmark elems 1]
    set aspect_bad_after [check_2d_aspect_mark 10.0 1]
    set tet_max [expr {max($elem_size * 1.9, 0.85)}]
    set tet_min 0.50
    if {$elem_size < 0.55} { set tet_min [expr {$elem_size * 0.60}] }
    *createstringarray 2 \
        "tet: 547 1.2 2 $tet_max 0.8 $tet_min 0" \
        "pars: pre_cln=1 post_cln=1 shell_validation=1 use_optimizer=1 skip_aflr3=1 feature_angle=30 niter=30 fix_comp_bdr=1 fix_top_bdr=1 shell_swap=1 shell_remesh=1 upd_shell=1 shell_dev=0.0,0.0 vol_skew='0.99,0.95,0.90,1'"
    set tet_err ""
    if {[catch {*tetmesh elems 1 1 elems 0 -1 1 2} tet_err]} {
        mlog $fh "tetmesh_failed solid=$sid name=$name shells=[llength $shell_ids] err={$tet_err}"
        return 0
    }
    *createmark elems 1 "by comp id" $comp
    set all_comp_elems [hm_getmark elems 1]
    set tets 0
    set hexes 0
    set shells_after 0
    foreach eid $all_comp_elems {
        set cfg [safe_getvalue elems $eid config]
        if {$cfg == 204 || $cfg == 210} { incr tets }
        if {$cfg == 208} { incr hexes }
        if {$cfg == 103 || $cfg == 104 || $cfg == 106 || $cfg == 108} { incr shells_after }
    }
    set vol_bad [check_vol_skew_mark 0.99 1]
    delete_elems_list $shell_ids
    mlog $fh "tet_volume solid=$sid name=$name size=$elem_size shells=[llength $shell_ids] aspect_gt_10_before=$aspect_bad_before aspect_gt_10_after=$aspect_bad_after tets=$tets hexes=$hexes deleted_surface_shells=[llength $shell_ids] vol_skew_gt_0.99=$vol_bad"
    return 1
}
proc summarize_final {fh} {
    *createmark elems 1 "all"
    set all [hm_getmark elems 1]
    array set cfg_counts {}
    foreach eid $all {
        set cfg [safe_getvalue elems $eid config]
        if {$cfg == ""} { continue }
        if {![info exists cfg_counts($cfg)]} { set cfg_counts($cfg) 0 }
        incr cfg_counts($cfg)
    }
    set text ""
    foreach cfg [lsort -integer [array names cfg_counts]] {
        append text "[cfg_name $cfg]=$cfg_counts($cfg) "
    }
    mlog $fh "final_config_counts {$text}"
    *createmark comps 1 "all"
    foreach cid [hm_getmark comps 1] {
        set name [safe_getvalue comps $cid name]
        *createmark elems 1 "by comp id" $cid
        set elems [hm_getmark elems 1]
        array set local {}
        foreach eid $elems {
            set cfg [safe_getvalue elems $eid config]
            if {$cfg == ""} { continue }
            if {![info exists local($cfg)]} { set local($cfg) 0 }
            incr local($cfg)
        }
        set local_text ""
        foreach cfg [lsort -integer [array names local]] {
            append local_text "[cfg_name $cfg]=$local($cfg) "
        }
        mlog $fh "component id=$cid name=$name elems=[llength $elems] configs={$local_text}"
        unset local
    }
}

mlog $fh "MCP strategy v12 mesh started"
mlog $fh "Strategy: no solidmap; flange/bolt-hole/stepped parts are forced to surface-deviation R-trias plus per-component tetramesh; tube/ring drag first forces matched edge density, then requires all-quad source faces."
*elementorder 1

set drag_candidates {2 3 6 10 11 16 17 18 19}
set spin_candidates {20 23}
set handled {}

foreach sid $drag_candidates {
    set ok [drag_hex_solid $fh $sid [part_size $sid hex]]
    if {$ok} {
        lappend handled $sid
    } else {
        tetramesh_solid $fh $sid [part_size $sid tet]
        lappend handled $sid
    }
}

foreach sid $spin_candidates {
    set ok [spin_hex_solid $fh $sid [part_size $sid hex] 0 96]
    if {$ok} {
        lappend handled $sid
    } else {
        tetramesh_solid $fh $sid [part_size $sid tet]
        lappend handled $sid
    }
}

set all_solids {1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25}
foreach sid $all_solids {
    if {[lsearch -exact $handled $sid] >= 0} { continue }
    tetramesh_solid $fh $sid [part_size $sid tet]
}

summarize_final $fh
hm_answernext yes
*writefile $output_path 1
mlog $fh "saved=$output_path"
mlog $fh "MCP strategy v12 mesh completed"
close $fh
