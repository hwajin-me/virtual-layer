# Presence Fusion requirements / implementation / tests

All implementation module paths below are relative to
`custom_components/virtual_layer/presence_fusion/`. U = pure engine tests;
H = real HA state/event/config-flow/platform tests. Test IDs refer to the complete
root `PRESENCE_FUSION_SPEC.md`, preserved byte-for-byte from the supplied
`PRESENCE_FUSION_CODEX_SPEC.md`. See TEST_REPORT.md for actual executed results.

The later shared GeoJSON extension has a separate requirement/function matrix
in [GEOJSON_AREAS.md](GEOJSON_AREAS.md#requirement--test-mapping). It extends Home
geometry only when explicitly selected; original default behavior is retained.

## Invariants

| Invariant | Implementation | Scenarios |
|---|---|---|
| INV-01 | `adapters.py / coordinator.py / engine.py` | T02, T29, T31, T36, T38 |
| INV-02 | `engine.py::_arbitrate / _fuse` | T02, T08, T36 |
| INV-03 | `engine.py::_arbitrate` | T07, T08, T42 |
| INV-04 | `engine.py::_arbitrate` | T03, T04, T13 |
| INV-05 | `grouping.py / engine.py::_arbitrate` | T09, T10, T11, T12, T13 |
| INV-06 | `adapters.py::gps_observation / movement.py::Path.add` | T23, T24, T25, T39 |
| INV-07 | `adapters.py::local_observation / engine.py::_missing / entities.py` | T28, T29, T30, T31, T32, T46 |
| INV-08 | `engine.py::_arbitrate / grouping.py` | T14, T15, T26, T27 |
| INV-09 | `entities.py::FusionTracker / engine.py::_fuse` | T02, T08, T34, T46 |
| INV-10 | `coordinator.py::_tick / engine.py::evaluate` | T05, T06, T24, T30, T35, T44 |
| INV-11 | `flow.py::FusionFlow.async_step_fusion_source / coordinator.py::_read` | T38, T39 |
| INV-12 | `lifecycle.py / coordinator.py / entities.py` | T38, T40, T41, T42, T43, T44 |

## Mandatory scenarios

Each scenario lists actual callable test functions, including parameterized
variants. HA tests are additional to pure tests, not replacements for them.
Combined test names cover a continuous event sequence; assertions retain the
individual scenario expectations. Boundary tests do not sleep or use real GPS.

| ID | Requirement / modules | Actual test functions |
|---|---|---|
| T01 | ARB/REU/GRP — engine.py, grouping.py | [U: test_T01_initial_priority](../tests/unit/test_presence_fusion.py) |
| T02 | ARB/REU/GRP — engine.py, grouping.py | [H: test_T02_T08_T09_T42_events_to_entities](../tests/integration/test_presence_fusion_ha.py)<br>[U: test_T02_departure_left_behind](../tests/unit/test_presence_fusion.py)<br>[U: test_T02_primary_itself_leads_departure](../tests/unit/test_presence_fusion.py) |
| T03 | ARB/REU/GRP — engine.py, grouping.py | [U: test_T03_stationary_retention](../tests/unit/test_presence_fusion.py) |
| T04 | ARB/REU/GRP — engine.py, grouping.py | [U: test_T04_T05_strict_score_threshold](../tests/unit/test_presence_fusion.py) |
| T05 | ARB/REU/GRP — engine.py, grouping.py | [U: test_T04_T05_strict_score_threshold](../tests/unit/test_presence_fusion.py)<br>[U: test_T05_challenger_boundary](../tests/unit/test_presence_fusion.py) |
| T06 | ARB/REU/GRP — engine.py, grouping.py | [U: test_T06_challenger_reset](../tests/unit/test_presence_fusion.py) |
| T07 | ARB/REU/GRP — engine.py, grouping.py | [U: test_T07_safe_companion](../tests/unit/test_presence_fusion.py) |
| T08 | ARB/REU/GRP — engine.py, grouping.py | [H: test_T02_T08_T09_T42_events_to_entities](../tests/integration/test_presence_fusion_ha.py)<br>[U: test_T08_no_remote_fallback](../tests/unit/test_presence_fusion.py) |
| T09 | ARB/REU/GRP — engine.py, grouping.py | [H: test_T02_T08_T09_T42_events_to_entities](../tests/integration/test_presence_fusion_ha.py)<br>[U: test_T09_T11_T13_reunion](../tests/unit/test_presence_fusion.py)<br>[U: test_T09_interrupted_reunion_restarts_hold](../tests/unit/test_presence_fusion.py) |
| T10 | ARB/REU/GRP — engine.py, grouping.py | [U: test_T10_companions_not_reunion](../tests/unit/test_presence_fusion.py) |
| T11 | ARB/REU/GRP — engine.py, grouping.py | [U: test_T09_T11_T13_reunion](../tests/unit/test_presence_fusion.py) |
| T12 | ARB/REU/GRP — engine.py, grouping.py | [U: test_T12_local_return_requires_active_transition](../tests/unit/test_presence_fusion.py) |
| T13 | ARB/REU/GRP — engine.py, grouping.py | [U: test_T09_T11_T13_reunion](../tests/unit/test_presence_fusion.py) |
| T14 | ARB/REU/GRP — engine.py, grouping.py | [U: test_T14_successor_and_remote_cluster](../tests/unit/test_presence_fusion.py) |
| T15 | ARB/REU/GRP — engine.py, grouping.py | [U: test_T15_cold_ambiguous](../tests/unit/test_presence_fusion.py) |
| T16 | MOV/TIME/GRP — movement.py, adapters.py, grouping.py | [U: test_T16_jitter](../tests/unit/test_presence_fusion.py) |
| T17 | MOV/TIME/GRP — movement.py, adapters.py, grouping.py | [U: test_T17_slow_walking](../tests/unit/test_presence_fusion.py) |
| T18 | MOV/TIME/GRP — movement.py, adapters.py, grouping.py | [U: test_T18_round_trip](../tests/unit/test_presence_fusion.py) |
| T19 | MOV/TIME/GRP — movement.py, adapters.py, grouping.py | [U: test_T19_sample_frequency](../tests/unit/test_presence_fusion.py) |
| T20 | MOV/TIME/GRP — movement.py, adapters.py, grouping.py | [U: test_T20_jump](../tests/unit/test_presence_fusion.py) |
| T21 | MOV/TIME/GRP — movement.py, adapters.py, grouping.py | [U: test_T21_reacquire](../tests/unit/test_presence_fusion.py) |
| T22 | MOV/TIME/GRP — movement.py, adapters.py, grouping.py | [H: test_T22_accuracy_adapter](../tests/integration/test_presence_fusion_ha.py)<br>[U: test_T22_coordinate_validation](../tests/unit/test_presence_fusion.py)<br>[U: test_T22_poor_accuracy_cannot_poison_anchor](../tests/unit/test_presence_fusion.py) |
| T23 | MOV/TIME/GRP — movement.py, adapters.py, grouping.py | [U: test_T23_closed_bucket_not_rewritten_by_delayed_data](../tests/unit/test_presence_fusion.py)<br>[U: test_T23_ordering](../tests/unit/test_presence_fusion.py) |
| T24 | MOV/TIME/GRP — movement.py, adapters.py, grouping.py | [H: test_T24_T25_measurement_adapter](../tests/integration/test_presence_fusion_ha.py)<br>[H: test_T24_event_battery_does_not_refresh](../tests/integration/test_presence_fusion_ha.py)<br>[H: test_T24_timestamp_formats](../tests/integration/test_presence_fusion_ha.py) |
| T25 | MOV/TIME/GRP — movement.py, adapters.py, grouping.py | [H: test_T24_T25_measurement_adapter](../tests/integration/test_presence_fusion_ha.py)<br>[U: test_T25_identical_fresh_measurements](../tests/unit/test_presence_fusion.py) |
| T26 | MOV/TIME/GRP — movement.py, adapters.py, grouping.py | [U: test_T26_skew_and_uncertainty](../tests/unit/test_presence_fusion.py) |
| T27 | MOV/TIME/GRP — movement.py, adapters.py, grouping.py | [U: test_T27_complete_link](../tests/unit/test_presence_fusion.py) |
| T28 | FUS — engine.py, coordinator.py | [H: test_T28_T30_T46_native_outputs_timer](../tests/integration/test_presence_fusion_ha.py)<br>[U: test_T28_GPS_only](../tests/unit/test_presence_fusion.py) |
| T29 | FUS — engine.py, coordinator.py | [H: test_T29_T30_local_unknown_and_ttl](../tests/integration/test_presence_fusion_ha.py)<br>[U: test_T29_T31_local_home_during_GPS_loss](../tests/unit/test_presence_fusion.py) |
| T30 | FUS — engine.py, coordinator.py | [H: test_T28_T30_T46_native_outputs_timer](../tests/integration/test_presence_fusion_ha.py)<br>[H: test_T29_T30_local_unknown_and_ttl](../tests/integration/test_presence_fusion_ha.py)<br>[H: test_T30_registered_maintenance_timer](../tests/integration/test_presence_fusion_ha.py)<br>[U: test_T30_timer_expiration](../tests/unit/test_presence_fusion.py) |
| T31 | FUS — engine.py, coordinator.py | [H: test_T31_local_only_home](../tests/integration/test_presence_fusion_ha.py)<br>[U: test_T29_T31_local_home_during_GPS_loss](../tests/unit/test_presence_fusion.py) |
| T32 | FUS — engine.py, coordinator.py | [U: test_T32_conflict_expiration](../tests/unit/test_presence_fusion.py) |
| T33 | FUS — engine.py, coordinator.py | [U: test_T33_direction_hysteresis](../tests/unit/test_presence_fusion.py) |
| T34 | FUS — engine.py, coordinator.py | [U: test_T34_primary_direction_reset](../tests/unit/test_presence_fusion.py) |
| T35 | FUS — engine.py, coordinator.py | [U: test_T35_T36_room_debounce_and_missing](../tests/unit/test_presence_fusion.py) |
| T36 | FUS — engine.py, coordinator.py | [U: test_T35_T36_room_debounce_and_missing](../tests/unit/test_presence_fusion.py) |
| T37 | FUS — engine.py, coordinator.py | [U: test_T37_window_fraction](../tests/unit/test_presence_fusion.py) |
| T38 | UI — flow.py | [H: test_T38_config_options_wizard](../tests/integration/test_presence_fusion_ha.py)<br>[H: test_T38_priority_source_edit_delete_and_metadata](../tests/integration/test_presence_fusion_ha.py)<br>[H: test_T38_reject_own_output_and_recover_bad_row](../tests/integration/test_presence_fusion_ha.py) |
| T39 | ADAPTER — adapters.py, coordinator.py | [H: test_T39_registry_rename_disable_remove](../tests/integration/test_presence_fusion_ha.py) |
| T40 | LIFECYCLE/STORE — lifecycle.py, coordinator.py, engine.py | [H: test_T40_T41_entry_isolation_reload](../tests/integration/test_presence_fusion_ha.py) |
| T41 | LIFECYCLE/STORE — lifecycle.py, coordinator.py, engine.py | [H: test_T40_T41_entry_isolation_reload](../tests/integration/test_presence_fusion_ha.py)<br>[H: test_T41_setup_exception_cleanup](../tests/integration/test_presence_fusion_ha.py) |
| T42 | LIFECYCLE/STORE — lifecycle.py, coordinator.py, engine.py | [H: test_T02_T08_T09_T42_events_to_entities](../tests/integration/test_presence_fusion_ha.py)<br>[H: test_T42_T43_store_restore_original_expiry](../tests/integration/test_presence_fusion_ha.py)<br>[U: test_T42_T43_restore_no_extended_grace](../tests/unit/test_presence_fusion.py) |
| T43 | LIFECYCLE/STORE — lifecycle.py, coordinator.py, engine.py | [H: test_T42_T43_store_restore_original_expiry](../tests/integration/test_presence_fusion_ha.py)<br>[H: test_T43_corrupt_metadata_and_removal](../tests/integration/test_presence_fusion_ha.py)<br>[U: test_T42_T43_restore_no_extended_grace](../tests/unit/test_presence_fusion.py) |
| T44 | MANUAL — lifecycle.py, engine.py | [H: test_T44_service_validation](../tests/integration/test_presence_fusion_ha.py)<br>[U: test_T44_manual_validation_and_expiry](../tests/unit/test_presence_fusion.py) |
| T45 | HOME/CLOCK — coordinator.py, engine.py | [H: test_T45_zone_updates](../tests/integration/test_presence_fusion_ha.py)<br>[U: test_T45_geometry_and_monotonic](../tests/unit/test_presence_fusion.py) |
| T46 | OUTPUT — entities.py | [H: test_T28_T30_T46_native_outputs_timer](../tests/integration/test_presence_fusion_ha.py) |
| T47 | PRIVACY — coordinator.py, ../diagnostics.py | [H: test_T47_private_diagnostics](../tests/integration/test_presence_fusion_ha.py) |
| T48 | BOUNDS/WRITES — movement.py, grouping.py, coordinator.py | [H: test_T48_unchanged_snapshot_suppresses_writes](../tests/integration/test_presence_fusion_ha.py)<br>[U: test_T48_bounded_history](../tests/unit/test_presence_fusion.py) |
## Re-audit regression mappings

These extend the mandatory rows above with reproduced failures and missing
branches. Every function runs the real implementation.

| IDs / modules | Additional test functions |
|---|---|
| T06 / engine.py | U `test_T06_changing_candidate_restarts_hold` |
| T12 / engine.py, coordinator.py, entities.py | U `test_T12_unrelated_local_source_cannot_fabricate_return`, `test_T12_local_reunion_deferred_priority`, `test_T12_gps_return_can_confirm_home_local_reunion`, `test_T12_local_reunion_without_any_valid_gps`; H `test_T12_local_reunion_then_preferred_gps_recovers` |
| T13 / movement.py | U `test_T13_session_does_not_reuse_crossing_segment` |
| T21, T23 / movement.py | U `test_T21_T23_reacquisition_duplicates_preserve_confirmation_window` |
| T26 / grouping.py | U `test_T26_sequential_packets_do_not_interrupt_valid_pair_run` |
| T28 / engine.py | U `test_T28_exit_hold_survives_nearby_boundary` |
| T32 / engine.py | U `test_T32_conflict_requires_new_observations_not_timer_counts` |
| T36 / engine.py | U `test_T36_ineligible_room_is_not_held_after_manual_selection` |
| T38 / configuration.py, flow.py, coordinator.py | H `test_T38_malformed_record_isolated_and_removable`, `test_T38_runtime_feedback_rejected_without_saved_registry_id` |
| T39 / movement.py, coordinator.py | H `test_T39_rename_with_identical_fix_updates_selected_source` |
| T43 / engine.py | U `test_T43_restore_is_atomic_and_rejects_inconsistent_hints` (five malformed cases) |
| T44 / lifecycle.py | H `test_T44_invalid_duration_has_no_side_effect` (boolean, range, NaN and infinity cases) |
| T38, T44 / models.py | U `test_T38_T44_default_override_duration_range` |
| T45 / coordinator.py | H `test_T45_zone_updates` now runs with Asia/Seoul, America/New_York and UTC |

## Repository and container checks

- `tests/docker/presence_fusion_smoke.py`: real official-container initial flow,
  all nine native entities, one Device, expiry/unknown, reload and unload.
- `tests/docker/run_compatibility_smoke.sh`: existing Virtual Layer regression
  smoke, including existing native domains and unrelated existing features.
- `tests/unit/test_config_flow_helpers.py` and the complete existing regression
  suite preserve the generic profile and English/Korean topology contract.
- Coverage uses real engine branches. Arbitration-layer threshold fixtures supply
  explicit measured segments to the real Path score function, not fake score
  functions. The round-trip metric is compared with an independent 600 m
  expectation; fixture metres use a declared equatorial degree conversion.
