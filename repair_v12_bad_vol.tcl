set output_path "F:/a_meshed_strategy_v12_final.hm"
set report_path "F:/a_meshed_strategy_v12_final_report.txt"
set fh [open $report_path "w"]
proc rlog {fh msg} { puts $msg; puts $fh $msg; flush $fh }
proc mcount {entity mark} { if {[catch {hm_marklength $entity $mark} c]} { return 0 }; return $c }
proc mark_failed_vol {mark_id} {
    *createmark elems 1 "by config" 204
    catch {*appendmark elems 1 "by config" 210}
    catch {*appendmark elems 1 "by config" 208}
    *clearmark elems $mark_id
    if {[mcount elems 1] > 0} {
        catch {*elementtestvolumetricskew elems 1 0.99 $mark_id 4 0 "MCP v12 vol skew"}
    }
}
proc count_shells {} {
    *createmark elems 1 "by config" 103
    catch {*appendmark elems 1 "by config" 104}
    catch {*appendmark elems 1 "by config" 106}
    catch {*appendmark elems 1 "by config" 108}
    return [mcount elems 1]
}
rlog $fh "v12 bad-volume local repair started"
mark_failed_vol 2
rlog $fh "bad_before=[mcount elems 2]"
rlog $fh "bad_ids_before=[hm_getmark elems 2]"
if {[mcount elems 2] > 0} {
    catch {*smooth3d elems 2 "vol_skew=0.99,1 feature_angle=30 niter=80 shell_dev=0.0,0.0 show_dim=2"} smooth_err
    rlog $fh "local_smooth_result={$smooth_err}"
}
mark_failed_vol 2
rlog $fh "bad_after_smooth=[mcount elems 2]"
rlog $fh "bad_ids_after_smooth=[hm_getmark elems 2]"
if {[mcount elems 2] > 0} {
    catch {*slivertetrafix 2 "fix_sliver 1 fix_wedge 2 optimize_node 2 vol_skew 0.99 0.95 0.90 8"} fix_err
    rlog $fh "slivertetrafix_result={$fix_err}"
}
mark_failed_vol 2
rlog $fh "bad_after_sliverfix=[mcount elems 2]"
rlog $fh "bad_ids_after_sliverfix=[hm_getmark elems 2]"
mark_failed_vol 2
rlog $fh "bad_after_repair_attempts=[mcount elems 2]"
rlog $fh "bad_ids_after_repair_attempts=[hm_getmark elems 2]"
if {[mcount elems 2] > 0} {
    rlog $fh "left_unfixed_bad_volume_elements_in_model=1"
    rlog $fh "policy=do_not_delete_unfixable_quality_elements"
}
rlog $fh "remaining_shell_elements=[count_shells]"
hm_answernext yes
*writefile $output_path 1
rlog $fh "saved=$output_path"
close $fh
