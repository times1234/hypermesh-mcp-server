set input_file "F:/a_meshed_strategy_v12.hm"
set output_file "F:/a_meshed_strategy_v17_cutsection_spin.hm"
set report_file "F:/a_meshed_strategy_v17_cutsection_spin_report.txt"

proc mlog {fh msg} {
    puts $fh $msg
    flush $fh
}

proc mcount {entity mark_id} {
    if {[catch {hm_marklength $entity $mark_id} n]} {
        return 0
    }
    return $n
}

proc get_all_elems {} {
    *createmark elems 1 "all"
    return [hm_getmark elems 1]
}

proc get_all_surfs {} {
    *createmark surfs 1 "all"
    return [hm_getmark surfs 1]
}

proc list_subtract {a b} {
    array set seen {}
    foreach x $b {set seen($x) 1}
    set out {}
    foreach x $a {
        if {![info exists seen($x)]} {
            lappend out $x
        }
    }
    return $out
}

proc list_minmax {items} {
    set mn ""
    set mx ""
    foreach x $items {
        if {$mn == "" || $x < $mn} {set mn $x}
        if {$mx == "" || $x > $mx} {set mx $x}
    }
    return [list $mn $mx]
}

proc elem_config_counts {elems} {
    array set counts {}
    foreach eid $elems {
        if {[catch {hm_getvalue elems id=$eid dataname=config} cfg]} {
            set cfg unknown
        }
        if {![info exists counts($cfg)]} {set counts($cfg) 0}
        incr counts($cfg)
    }
    set out {}
    foreach key [lsort [array names counts]] {
        lappend out "$key=$counts($key)"
    }
    return [join $out ","]
}

proc delete_elems_list {elems} {
    if {[llength $elems] == 0} {
        return
    }
    eval *createmark elems 1 $elems
    catch {*deletemark elems 1}
}

proc move_elems_to_comp {elems comp_name} {
    if {[llength $elems] == 0} {
        return
    }
    set mm [list_minmax $elems]
    set mn [lindex $mm 0]
    set mx [lindex $mm 1]
    if {$mn == "" || $mx == ""} {
        return
    }
    *createmark elems 1 "$mn-$mx"
    catch {*movemark elems 1 $comp_name}
}

proc delete_comp_elems {comp_name} {
    *createmark elems 1 "by comp name" $comp_name
    if {[mcount elems 1] > 0} {
        catch {*deletemark elems 1}
    }
}

proc surface_bbox_area {sid} {
    *createmark surfs 2 $sid
    set bb [hm_getboundingbox surfs 2 0 0 0]
    set area [hm_getareaofsurface surfs $sid]
    set dx [expr {abs([lindex $bb 3] - [lindex $bb 0])}]
    set dy [expr {abs([lindex $bb 4] - [lindex $bb 1])}]
    set dz [expr {abs([lindex $bb 5] - [lindex $bb 2])}]
    return [list $area $dx $dy $dz $bb]
}

proc mesh_surface_quads {sid elem_size} {
    *createmark surfaces 1 $sid
    catch {*setedgedensitylinkwithaspectratio -1}
    *setedgedensitylink 1
    *interactiveremeshsurf 1 $elem_size 1 1 2 1 1
    *set_meshfaceparams 0 5 1 0 0 1 0.5 1 1
    *automesh 0 5 1
    *storemeshtodatabase 1
    *ameshclearsurface
    *createmark elems 1 "by surface" $sid
    return [hm_getmark elems 1]
}

proc plane_distance_for_node {nid nx ny nz px py pz} {
    set x [hm_getvalue nodes id=$nid dataname=x]
    set y [hm_getvalue nodes id=$nid dataname=y]
    set z [hm_getvalue nodes id=$nid dataname=z]
    set d [expr {$nx * ($x - $px) + $ny * ($y - $py) + $nz * ($z - $pz)}]
    if {$d < 0} {set d [expr {-$d}]}
    return $d
}

proc mesh_if_true_section {fh sid elem_size nx ny nz px py pz plane_tol} {
    set shells [mesh_surface_quads $sid $elem_size]
    set total [llength $shells]
    if {$total == 0} {
        mlog $fh "section_reject surface=$sid reason=no_shells"
        return {}
    }

    set quads 0
    set trias 0
    set maxdist 0.0
    foreach eid $shells {
        set cfg [hm_getvalue elems id=$eid dataname=config]
        if {$cfg == 104 || $cfg == 108} {incr quads}
        if {$cfg == 103 || $cfg == 106} {incr trias}
        set nodes [hm_getvalue elems id=$eid dataname=nodes]
        foreach nid $nodes {
            set d [plane_distance_for_node $nid $nx $ny $nz $px $py $pz]
            if {$d > $maxdist} {set maxdist $d}
        }
    }

    if {$quads != $total || $maxdist > $plane_tol} {
        delete_elems_list $shells
        mlog $fh "section_reject surface=$sid shells=$total quads=$quads trias=$trias max_plane_dist=$maxdist"
        return {}
    }

    mlog $fh "section_accept surface=$sid shells=$total quads=$quads max_plane_dist=$maxdist"
    return $shells
}

proc split_mesh_spin_bearing {fh solid_id comp_name nx ny nz px py pz axis_x axis_y axis_z elem_size spin_density} {
    mlog $fh "start_cut_section_spin solid=$solid_id comp=$comp_name"

    delete_comp_elems $comp_name
    *currentcollector components $comp_name

    set before_surfs [get_all_surfs]
    *createmark solids 1 $solid_id
    if {[mcount solids 1] == 0} {
        mlog $fh "skip solid=$solid_id comp=$comp_name reason=solid_missing"
        return 0
    }

    *createplane 1 $nx $ny $nz $px $py $pz
    if {[catch {*body_splitmerge_with_plane solids 1 1} err]} {
        mlog $fh "split_failed solid=$solid_id comp=$comp_name err={$err}"
        return 0
    }

    set after_surfs [get_all_surfs]
    set new_surfs [lsort -integer [list_subtract $after_surfs $before_surfs]]
    mlog $fh "split_done solid=$solid_id comp=$comp_name new_surfs=$new_surfs"

    set seed_shells {}
    foreach sid $new_surfs {
        set info [surface_bbox_area $sid]
        set area [lindex $info 0]
        set dx [lindex $info 1]
        set dy [lindex $info 2]
        set dz [lindex $info 3]
        mlog $fh "new_surface solid=$solid_id surface=$sid area=$area dims=$dx,$dy,$dz"
        set shells [mesh_if_true_section $fh $sid $elem_size $nx $ny $nz $px $py $pz 0.02]
        foreach e $shells {
            lappend seed_shells $e
        }
    }

    if {[llength $seed_shells] == 0} {
        mlog $fh "spin_skip solid=$solid_id comp=$comp_name reason=no_true_section_shells"
        return 0
    }

    move_elems_to_comp $seed_shells $comp_name
    set before_spin [get_all_elems]
    eval *createmark elems 1 $seed_shells
    *createplane 1 1 0 0 $axis_x $axis_y $axis_z
    if {[catch {*meshspinelements2 1 1 360 $spin_density 1 0.0 0} err]} {
        mlog $fh "spin_failed solid=$solid_id comp=$comp_name seed_shells=[llength $seed_shells] err={$err}"
        delete_elems_list $seed_shells
        return 0
    }

    set after_spin [get_all_elems]
    set new_vols [list_subtract $after_spin $before_spin]
    move_elems_to_comp $new_vols $comp_name
    delete_elems_list $seed_shells
    mlog $fh "spin_done solid=$solid_id comp=$comp_name seed_shells=[llength $seed_shells] new_elements=[llength $new_vols] configs=[elem_config_counts $new_vols]"
    return 1
}

hm_answernext yes
*readfile $input_file

set fh [open $report_file w]
mlog $fh "input=$input_file"
mlog $fh "method=split solid by middle plane, automesh only true cut-section surfaces, then spin section shells into 3D hex mesh"
mlog $fh "policy=replace only bearing_6903_left and bearing_6903_right elements; do not delete unrelated quality-failed elements"

split_mesh_spin_bearing $fh 20 bearing_6903_left 0 -0.831039379 -0.556213583 4.121449294811 8.02783775 1035.87476 7.621449136281649 13.756837276186051 1027.31510824055 0.7 96
split_mesh_spin_bearing $fh 23 bearing_6903_right 0 -0.831039379 -0.556213583 -55.8785515 8.02783775 1035.87476 -52.378550705189 13.756836994729051 1027.3151078701999 0.7 96

foreach comp_name {bearing_6903_left bearing_6903_right} {
    *createmark elems 1 "by comp name" $comp_name
    set elems [hm_getmark elems 1]
    mlog $fh "final_comp comp=$comp_name elements=[llength $elems] configs=[elem_config_counts $elems]"
}

*writefile $output_file 1
mlog $fh "output=$output_file"
close $fh
