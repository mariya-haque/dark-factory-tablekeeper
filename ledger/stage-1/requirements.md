# Tablekeeper stage 1: requirements ledger

Source: the stage 1 specification text, sections 1-11, received from the Coordinator in the
SA-1 handoff (parts 2 and 3). Every row is derived from that text only.

Acceptance tests are in `stage-1/acceptance/`. Run them with:

    BASE_URL=http://127.0.0.1:18100 C:/df/tools/.venv/Scripts/python.exe -m pytest stage-1/acceptance -q

Verification names are `file::test` in `stage-1/acceptance/`, without the `test_` prefix on
the file name. "review" means the row is checked by reading or building the deliverable
because a black-box HTTP test cannot prove it.

Kinds: interface, state rule, error behaviour, concurrency, retry, time, arithmetic,
validation, upgrade/compatibility.

## Requirements

### §1 Scope

| id | requirement | kind | verified by |
|---|---|---|---|
| S1-R01 | Two `confirmed` reservations never occupy the same table at overlapping times, including under concurrent requests (create, PATCH, moves, cancel and rebook). | concurrency | concurrency::* (6 races), reservations::test_overlap_rejected |
| S1-R02 | Occupancy is the half-open interval `[starts_at, starts_at + duration)`; a booking ending at 20:30 does not overlap one starting at 20:30, in either order. | arithmetic | reservations::test_half_open_back_to_back_allowed, availability::test_booking_removes_table_from_overlapping_slots_only, dst::test_fall_back_berlin_overlap_on_real_instants |
| S1-R03 | Retries and rejected requests never create duplicate or partial bookings. | retry | reservations::test_rejected_requests_create_nothing, idempotency::test_replay_returns_200_identical_body, idempotency::test_key_after_4xx_is_first_use, idempotency::test_concurrent_identical_requests_one_effect |

### §2 Delivery and deployment

| id | requirement | kind | verified by |
|---|---|---|---|
| S1-R04 | A `Dockerfile` and `RUN.md` build and start the service with no manual setup. | interface | review (Reviewer builds from scratch) |
| S1-R05 | The image runs on its own with `-e PORT=<port>` and a port mapping, with no outbound network at run time; all dependencies, seed data and assets are in the image. | interface | review |
| S1-R06 | `GET /health` returns 200 within 60 s of container start. | time | review (check command) |
| S1-R07 | The service handles up to 50 concurrent in-flight requests, each answered within 5 s, within 2 vCPU / 2 GiB. | concurrency | concurrency::test_fifty_concurrent_reads_within_limits |
| S1-R08 | `POST /_test/reset` completes within 10 s. | time | concurrency::test_reset_within_ten_seconds |

### §3 Runtime contract

| id | requirement | kind | verified by |
|---|---|---|---|
| S1-R09 | Listens on `0.0.0.0:$PORT`, default 8080. | interface | review (serve.sh, Dockerfile) |
| S1-R10 | `GET /health` returns 200 `{"status": "ok"}` once the service and its store are ready. | interface | runtime::test_health_ok |
| S1-R11 | `POST /_test/reset` with a fixture body replaces all state, returns 204, needs no auth, and can be repeated. | state rule | runtime::test_reset_returns_204_without_auth, runtime::test_reset_replaces_all_state, runtime::test_reset_with_empty_fixture |
| S1-R12 | After reset returns 204, requests see only that fixture: earlier restaurants, users, tokens, reservations and idempotency keys are gone (A9). | state rule | runtime::test_reset_replaces_all_state, runtime::test_reset_with_empty_fixture |
| S1-R13 | Responses are `application/json; charset=utf-8` (A12). | interface | runtime::test_json_content_type_with_utf8_charset |
| S1-R14 | Timestamps in responses are RFC 3339 with an explicit offset. | time | runtime::test_created_at_and_offsets_rfc3339, reservations::test_create_reservation_shape (every reservation check) |
| S1-R15 | Unknown fields in a request body are ignored, never an error. | validation | runtime::test_unknown_body_fields_ignored, moves::test_omitted_fields_kept_and_unknown_ignored |
| S1-R16 | Unknown query parameters are ignored. | validation | runtime::test_unknown_query_params_ignored |
| S1-R17 | IDs are opaque strings of at most 64 characters; 64-character fixture IDs are accepted; generated IDs never exceed 64. | validation | runtime::test_64_character_fixture_ids_accepted, runtime::test_generated_ids_at_most_64_chars |

### §4 Model and fixture

| id | requirement | kind | verified by |
|---|---|---|---|
| S1-R18 | Restaurants and tables come only from reset fixtures (no create endpoints); the API reflects the fixture's values. | interface | availability::test_get_restaurant_in_fixture_shape |
| S1-R19 | Opening hours are per weekday (`mon`..`sun`); a weekday with no entry is closed. Hours never cross midnight. | time | availability::test_closed_day_has_no_slots, availability::test_availability_grid_friday_later_close, reservations::test_outside_opening_hours |
| S1-R20 | Bookings start on a grid of `slot_minutes` counted from the day's opening time, not from the hour. | arithmetic | availability::test_availability_grid_from_opening_time, reservations::test_grid_anchored_at_opening |
| S1-R21 | Seeded users can log in at once with the fixture password; `user_id` is the fixture id (A10). | state rule | runtime::test_seeded_users_can_log_in_immediately |
| S1-R22 | Seeded reservations (POST body fields plus `id`, `reference`, `user_id`) are confirmed bookings: owned by `user_id`, with `reservation_id` = `id` and the given `reference`, and they occupy their table. | state rule | runtime::test_seeded_reservation_is_confirmed_owned_and_occupies |
| S1-R23 | A booking is never rejected only because its start is in the past; the cutoff rules still apply. | time | runtime::test_past_booking_not_rejected, cancel_amend::test_cancel_after_start_is_cutoff_passed |

### §5 Errors

| id | requirement | kind | verified by |
|---|---|---|---|
| S1-R24 | Every 4xx/5xx response body is `{"error": {"code": str, "message": str}}`, including unknown routes and wrong methods (A13). | error behaviour | `assert_error` in every error test; runtime::test_unknown_route_has_error_body, runtime::test_wrong_method_has_error_body |
| S1-R25 | 400 `malformed_request`: unparseable body, a body that is not a JSON object, or a field of the wrong JSON type (except where an endpoint says otherwise). | validation | reservations::test_unparseable_or_non_object_body, reservations::test_wrong_json_type_is_malformed, auth::test_signup_wrong_json_type, auth::test_signup_unparseable_body, export_import::test_unparseable_import_is_400 |
| S1-R26 | 401 `unauthenticated`: missing, malformed or unknown bearer token. | error behaviour | auth::test_protected_endpoints_require_valid_bearer (6 endpoints x 5 header forms) |
| S1-R27 | 404 `not_found`: no such resource, or not visible to this caller. | error behaviour | availability::test_get_unknown_restaurant_404, runtime::test_unknown_route_has_error_body |
| S1-R28 | 422 `validation_failed`: a required body field or query parameter is missing. | validation | reservations::test_missing_field, availability::test_availability_missing_param, auth::test_signup_missing_field, moves::test_missing_moves_field |
| S1-R29 | 422 `validation_failed`: a field of the right type with an invalid format or out-of-range value (invalid dates, negative counts, values over a maximum). | validation | availability::test_availability_bad_date, availability::test_availability_bad_party_size, reservations::test_invalid_starts_at_local |
| S1-R30 | Invalid `party_size` (strings, booleans, null, non-integers, below 1) is 422 `validation_failed`, not 400. | validation | reservations::test_invalid_party_size, availability::test_availability_bad_party_size |
| S1-R31 | A `starts_at_local` string that is not a bare local `YYYY-MM-DDTHH:MM` (seconds, offset, `Z`, space, short fields) is 422 `validation_failed`; a non-string `starts_at_local` is 400. | validation | reservations::test_invalid_starts_at_local, reservations::test_wrong_json_type_is_malformed |
| S1-R32 | An integer query parameter must be plain decimal digits: `1e9`, `4.0`, `+4` are 422 `validation_failed`. | validation | availability::test_availability_bad_party_size |
| S1-R33 | `Idempotency-Key` must be 1..255 characters: 256 is 422 `validation_failed`, 255 and 1 are accepted. | validation | idempotency::test_key_length_bounds |
| S1-R34 | No request produces a 5xx, including under concurrent load. | error behaviour | every test (5xx fails any status assertion); concurrency::*, auth::test_concurrent_signups_same_email_exactly_one_wins |
| S1-R35 | 403 `forbidden` is for authenticated callers not permitted to touch a resource. Stage 1 ownership failures are specified as 404, so no stage-1 path returns 403 (A5). | error behaviour | review |

### §6 Authentication

| id | requirement | kind | verified by |
|---|---|---|---|
| S1-R36 | `POST /auth/signup` returns 201 `{user_id, display_name, token}`; the token works at once. | interface | auth::test_signup_returns_201_with_token |
| S1-R37 | `POST /auth/login` returns 200 with the same shape and the same `user_id`. | interface | auth::test_login_returns_200_same_shape, runtime::test_seeded_users_can_log_in_immediately |
| S1-R38 | A registered email gives 409 `email_taken`, including seeded users and concurrent signups (exactly one wins). | concurrency | auth::test_email_taken, auth::test_concurrent_signups_same_email_exactly_one_wins |
| S1-R39 | A password shorter than 8 characters is 422 `validation_failed`; exactly 8 is accepted. | validation | auth::test_password_length_boundary |
| S1-R40 | An email not of the form `local@domain` is 422 `validation_failed`. | validation | auth::test_invalid_email_rejected |
| S1-R41 | Wrong password or unknown email on login is 401 `unauthenticated`. | error behaviour | auth::test_login_failures_are_401 |
| S1-R42 | A bearer token is required on every endpoint except `/health`, `/_test/reset`, `/_test/export`, `/_test/import`, signup, login, `GET /restaurants`, `GET /restaurants/{id}` and `GET /availability`. | interface | auth::test_protected_endpoints_require_valid_bearer, auth::test_public_endpoints_need_no_token |
| S1-R43 | Tokens do not expire; an account can hold several valid tokens at once, and each identifies the same user. | state rule | auth::test_multiple_tokens_all_valid, auth::test_token_identifies_the_user |
| S1-R44 | Passwords are stored only as a password hash (bcrypt, scrypt, Argon2 or equivalent), never in plaintext (A11). | validation | auth::test_export_holds_no_plaintext_password, plus review of the hashing code |

### §7 Idempotency

| id | requirement | kind | verified by |
|---|---|---|---|
| S1-R45 | `POST /reservations` and `POST /reservation-moves` need `Idempotency-Key`; absent or empty is 400 `missing_idempotency_key`. | retry | idempotency::test_missing_key, idempotency::test_empty_key, idempotency::test_missing_key_on_moves |
| S1-R46 | Keys are scoped per authenticated user; another user's identical key and body is a fresh request. | retry | idempotency::test_key_scoped_per_user |
| S1-R47 | A replay needs the same user, method, path and body; the same key on a different path succeeds normally. | retry | idempotency::test_same_key_different_path_is_not_a_replay |
| S1-R48 | Idempotency is resolved after JSON parsing and authentication but before field validation and resource checks, so a used key with a different, even invalid, body is 409. | retry | idempotency::test_reuse_detected_before_validation |
| S1-R49 | First use of a key returns the normal 201. | retry | idempotency::test_replay_returns_200_identical_body |
| S1-R50 | A replay returns 200 with a body equal, as a JSON value, to the original. | retry | idempotency::test_replay_returns_200_identical_body, moves::test_replay_returns_original_after_changes |
| S1-R51 | The same key with a different body is 409 `idempotency_key_reuse` and changes nothing. | retry | idempotency::test_same_key_different_body_409 |
| S1-R52 | A key whose original request failed with 4xx counts as unused: it can be retried or reused with another body. | retry | idempotency::test_key_after_4xx_is_first_use, idempotency::test_key_after_422_same_body_retried, moves::test_failed_batch_does_not_consume_key |
| S1-R53 | "Same body" means equal JSON values; key order and whitespace do not matter. | retry | idempotency::test_replay_ignores_key_order_and_whitespace |
| S1-R54 | Concurrent identical requests with an unused key: exactly one 201, the rest 200 with the same body, and one effect. | concurrency | idempotency::test_concurrent_identical_requests_one_effect |
| S1-R55 | A replay returns the original response even after the resource was changed or cancelled, and changes nothing. | retry | idempotency::test_replay_after_cancel_returns_original_without_effect, idempotency::test_replay_after_patch_returns_original, moves::test_replay_returns_original_after_changes |

### §8 API

| id | requirement | kind | verified by |
|---|---|---|---|
| S1-R56 | `GET /restaurants` (public) returns `{"restaurants": [{id, name, timezone}]}` for every fixture restaurant. | interface | availability::test_list_restaurants |
| S1-R57 | `GET /restaurants/{id}` (public) returns the restaurant with `slot_minutes`, `reservation_duration_minutes`, `cancellation_cutoff_minutes`, `opening_hours`, `tables` in fixture shape; unknown id is 404. | interface | availability::test_get_restaurant_in_fixture_shape, availability::test_get_unknown_restaurant_404 |
| S1-R58 | `GET /availability` (public) needs `restaurant_id`, `date`, `party_size` (each missing one is 422) and returns `{restaurant_id, date, timezone, slots}`. | interface | availability::test_availability_shape_and_grid_thursday, availability::test_availability_missing_param |
| S1-R59 | Each slot has `starts_at_local` (`YYYY-MM-DDTHH:MM`, accepted unchanged by POST), `starts_at` with offset, and `available_table_ids`. | interface | availability::test_availability_shape_and_grid_thursday, availability::test_starts_at_local_round_trips_into_booking |
| S1-R60 | A slot appears at every `slot_minutes` step from `opens` such that `slot + duration <= closes`. | arithmetic | availability::test_availability_shape_and_grid_thursday, availability::test_availability_grid_friday_later_close, availability::test_availability_grid_from_opening_time, reservations::test_last_slot_ending_exactly_at_close_allowed |
| S1-R61 | `available_table_ids` lists the restaurant's tables with `capacity >= party_size` and no overlapping confirmed reservation, in fixture order; a slot with no table still appears with `[]`. | state rule | availability::test_capacity_filter_and_empty_slots_still_listed, availability::test_fixture_order_of_tables, availability::test_booking_removes_table_from_overlapping_slots_only |
| S1-R62 | A closed day returns `"slots": []`. | time | availability::test_closed_day_has_no_slots |
| S1-R63 | An unknown `restaurant_id` on availability is 404 `not_found` (A3). | error behaviour | availability::test_availability_unknown_restaurant |
| S1-R64 | `POST /reservations` returns 201 with `reservation_id, reference, restaurant_id, table_id, party_size, status: "confirmed", starts_at_local, starts_at, ends_at, created_at`; `ends_at = starts_at + duration`. | interface | reservations::test_create_reservation_shape |
| S1-R65 | `starts_at_local` is wall-clock time, resolved against the restaurant's zone with the correct offset for that date. | time | reservations::test_create_reservation_shape, reservations::test_winter_booking_offset, availability::test_winter_offset |
| S1-R66 | `reference` is 6-12 characters of `A-Z0-9`, unique across all reservations, and never changes. | state rule | reservations::test_references_unique_and_well_formed, cancel_amend::test_patch_time_moves_occupancy |
| S1-R67 | Booking a table already taken for an overlapping interval is 409 `table_unavailable`; cancelled bookings do not block. | state rule | reservations::test_overlap_rejected, reservations::test_cancelled_booking_does_not_block |
| S1-R68 | A `starts_at_local` off the slot grid is 422 `not_on_slot_grid`. | arithmetic | reservations::test_not_on_slot_grid, reservations::test_grid_anchored_at_opening |
| S1-R69 | A slot outside opening hours, on a closed day (A2), or ending after `closes` is 422 `outside_opening_hours`; ending exactly at `closes` is allowed. | time | reservations::test_outside_opening_hours, reservations::test_last_slot_ending_exactly_at_close_allowed |
| S1-R70 | `party_size` above the table's capacity is 422 `party_exceeds_capacity`; equal to capacity is allowed. | validation | reservations::test_party_exceeds_capacity, reservations::test_create_reservation_shape |
| S1-R71 | A local time that does not exist (spring-forward gap) is 422 `invalid_local_time`. | time | dst::test_booking_in_gap_is_invalid_local_time |
| S1-R72 | Unknown restaurant, unknown table, or a table of another restaurant is 404 `not_found`. | error behaviour | reservations::test_unknown_restaurant_or_table |
| S1-R73 | `GET /reservations` returns 200 `{"reservations": [...]}`: only the caller's, confirmed and cancelled, sorted by `starts_at` descending, in create-response shape; `[]` when there are none. | interface | reservations::test_list_own_reservations_desc |
| S1-R74 | `GET /reservations/{reference}` returns the caller's reservation; another user's or an unknown one is 404. | error behaviour | reservations::test_get_reservation_owner_only |
| S1-R75 | `POST /reservations/{reference}/cancel` returns 200 with the reservation in `status: "cancelled"`. | interface | cancel_amend::test_cancel_returns_cancelled_and_frees_table |
| S1-R76 | Cancelling frees the table at once: the next availability call offers it and it can be booked again. | state rule | cancel_amend::test_cancel_returns_cancelled_and_frees_table, reservations::test_cancelled_booking_does_not_block, concurrency::test_concurrent_cancel_and_rebook |
| S1-R77 | Cancelling an already-cancelled reservation is 200 with its current state. | state rule | cancel_amend::test_cancel_twice_is_ok |
| S1-R78 | Cancelling when now is within `cancellation_cutoff_minutes` of `starts_at`, or later, is 409 `cutoff_passed` (A6). | time | cancel_amend::test_cancel_after_start_is_cutoff_passed, cancel_amend::test_cancel_within_cutoff_is_cutoff_passed |
| S1-R79 | Cancelling someone else's reservation is 404 `not_found` and changes nothing. | error behaviour | cancel_amend::test_cancel_someone_elses_is_404 |
| S1-R80 | `PATCH /reservations/{reference}` accepts any subset of `table_id`, `starts_at_local`, `party_size`, needs no idempotency key, and returns the updated reservation (200, A1). | interface | cancel_amend::test_patch_party_size_only, cancel_amend::test_patch_time_moves_occupancy |
| S1-R81 | PATCH validation matches POST (same codes: grid, hours, capacity, party, format, invalid time, not found, wrong type). | validation | cancel_amend::test_patch_validation_like_create, dst::test_patch_into_gap_is_invalid_local_time |
| S1-R82 | PATCH applies the cutoff rule against the **current** start: 409 `cutoff_passed`. | time | cancel_amend::test_patch_cutoff_uses_current_start, cancel_amend::test_patch_within_cutoff |
| S1-R83 | PATCH of a cancelled reservation is 409 `reservation_cancelled`. | state rule | cancel_amend::test_patch_cancelled_is_409 |
| S1-R84 | A successful PATCH releases the old slot and reserves the new one together, so a move that overlaps its own old interval succeeds. | state rule | cancel_amend::test_patch_time_moves_occupancy, cancel_amend::test_patch_table_moves_occupancy, cancel_amend::test_patch_overlapping_itself_is_allowed |
| S1-R85 | A failed PATCH leaves the booking and its occupancy unchanged. | state rule | cancel_amend::test_patch_conflict_leaves_original, cancel_amend::test_patch_validation_like_create |
| S1-R86 | `reference`, `reservation_id` and `created_at` survive an amendment. | state rule | cancel_amend::test_patch_party_size_only, cancel_amend::test_patch_time_moves_occupancy |
| S1-R87 | PATCH of someone else's reservation is 404 `not_found`. | error behaviour | cancel_amend::test_patch_someone_elses_is_404 |

### §9 Time and DST

| id | requirement | kind | verified by |
|---|---|---|---|
| S1-R88 | Spring forward: skipped local times never appear in availability. | time | dst::test_spring_forward_gap_absent_from_availability (Berlin, New York) |
| S1-R89 | Spring forward: booking or amending into a skipped time is 422 `invalid_local_time`. | time | dst::test_booking_in_gap_is_invalid_local_time, dst::test_patch_into_gap_is_invalid_local_time |
| S1-R90 | Fall back: a repeated local time resolves to its first occurrence (pre-change offset), appears once in availability, and the second occurrence cannot be booked. | time | dst::test_fall_back_repeated_hour_listed_once_first_occurrence, dst::test_fall_back_ny_spec_example, dst::test_fall_back_berlin_overlap_on_real_instants |
| S1-R91 | `reservation_duration_minutes` is absolute time: `ends_at` shows the post-transition offset and wall-clock time, and overlap uses real instants. | time | dst::test_spring_forward_absolute_duration_berlin, dst::test_spring_forward_absolute_duration_ny, dst::test_fall_back_ny_spec_example, dst::test_fall_back_berlin_overlap_on_real_instants, dst::test_fall_back_berlin_end_in_second_occurrence |
| S1-R92 | Offsets follow IANA rules per zone and date (Berlin +01:00/+02:00, New York -05:00/-04:00, the 2026 transitions in the table). | time | dst::*, availability::test_winter_offset, reservations::test_winter_booking_offset |

### §10 Export and import

| id | requirement | kind | verified by |
|---|---|---|---|
| S1-R93 | `GET /_test/export` (no auth) returns 200 `{track: "tablekeeper", format_version: 1, state: {...}}`. | upgrade/compatibility | export_import::test_export_shape_unauthenticated |
| S1-R94 | `POST /_test/import` (no auth) accepts an unchanged export and returns 204; it replaces state atomically and does not merge; repeating it restores the same state without duplicates. | upgrade/compatibility | export_import::test_round_trip_preserves_everything, export_import::test_import_is_replacement_and_repeatable |
| S1-R95 | Invalid JSON on import is 400 `malformed_request`; missing fields, wrong track/version or an invalid state are 422 `validation_failed`, and the destination does not change (A8). | upgrade/compatibility | export_import::test_invalid_import_is_422_and_changes_nothing (7 variants), export_import::test_unparseable_import_is_400 |
| S1-R96 | An export is an atomic, read-only snapshot; later writes to the source do not change it. | upgrade/compatibility | export_import::test_round_trip_preserves_everything |
| S1-R97 | Import preserves accounts and hashed-password login, existing tokens, fixture configuration, reservations, references, ids, statuses and timestamps, with nothing regenerated. | upgrade/compatibility | export_import::test_round_trip_preserves_everything |
| S1-R98 | Import preserves completed idempotent requests and their original responses (replays give 200 with the same body); failed keys stay reusable. | upgrade/compatibility | export_import::test_round_trip_preserves_everything |
| S1-R99 | Import removes all earlier destination data and credentials (users, tokens, reservations). | upgrade/compatibility | export_import::test_import_removes_destination_data, export_import::test_round_trip_preserves_everything |
| S1-R100 | Reset clears all state, including imported state. | state rule | export_import::test_reset_clears_imported_state |
| S1-R101 | Export/import does not depend on the source process, files, volume, port or address, and finishes within the 10 s test-control timeout. | upgrade/compatibility | review; partly export_import::test_round_trip_preserves_everything (reset between export and import) |

### §11 Atomic reservation moves

| id | requirement | kind | verified by |
|---|---|---|---|
| S1-R102 | `POST /reservation-moves` requires a bearer token (401 otherwise) and an `Idempotency-Key`. | interface | moves::test_no_token_401, idempotency::test_missing_key_on_moves, auth::test_protected_endpoints_require_valid_bearer |
| S1-R103 | `moves` holds 1..8 objects with distinct string references; a missing `moves`, an empty list, 9 items, an item without a string reference, or duplicate references is 422 `validation_failed` (A4). | validation | moves::test_invalid_shape, moves::test_nine_distinct_items_rejected_eight_accepted, moves::test_duplicate_references, moves::test_missing_moves_field |
| S1-R104 | An unknown reference or another user's is 404 `not_found`. | error behaviour | moves::test_unknown_or_foreign_reference_404 |
| S1-R105 | Bookings from different restaurants in one batch give 422 `validation_failed`. | validation | moves::test_different_restaurants_422 |
| S1-R106 | Items accept `table_id`, `starts_at_local`, `party_size`; omitted fields keep their values; unknown fields are ignored; each item is validated like PATCH. | validation | moves::test_swap_tables_succeeds, moves::test_omitted_fields_kept_and_unknown_ignored, moves::test_non_occupancy_errors_in_input_order |
| S1-R107 | A booking's identity (`reservation_id`, `reference`), owner and `created_at` never change, even if the item tries to set them. | state rule | moves::test_identity_owner_creation_time_never_change, moves::test_swap_tables_succeeds |
| S1-R108 | A cancelled booking in a batch is 409 `reservation_cancelled`. | state rule | moves::test_cancelled_booking_409 |
| S1-R109 | Each booking's own cutoff applies: 409 `cutoff_passed`. | time | moves::test_cutoff_applies, moves::test_cutoff_precedes_other_errors_for_same_booking |
| S1-R110 | Error precedence: non-occupancy errors (ordinary amendment codes) win over `table_unavailable`, the first failing item in input order wins, and within one booking `cutoff_passed` comes first (A17). | error behaviour | moves::test_non_occupancy_error_beats_earlier_occupancy_error, moves::test_non_occupancy_errors_in_input_order, moves::test_cutoff_precedes_other_errors_for_same_booking |
| S1-R111 | An overlap among the resulting bookings, or with any unlisted booking, is 409 `table_unavailable`; results are checked as a whole, so swaps succeed. | state rule | moves::test_swap_tables_succeeds, moves::test_conflict_with_unlisted_booking_changes_nothing, moves::test_conflict_among_results, concurrency::test_concurrent_moves_onto_same_table_one_winner |
| S1-R112 | Unchanged listed bookings keep their occupancy. | state rule | moves::test_unchanged_listed_booking_keeps_occupancy |
| S1-R113 | All or nothing: on any error no occupancy, reservation record or retry key changes. | state rule | moves::test_conflict_with_unlisted_booking_changes_nothing, moves::test_conflict_among_results, moves::test_failed_batch_does_not_consume_key, concurrency::test_concurrent_moves_onto_same_table_one_winner |
| S1-R114 | Success is 201 `{"reservations": [...]}` in input order, including unchanged items. | interface | moves::test_swap_tables_succeeds, moves::test_result_includes_unchanged_items_in_input_order |
| S1-R115 | A replay returns the original response with 200, even after later amendments or cancellations. | retry | moves::test_replay_returns_original_after_changes |
| S1-R116 | No-op moves keep all existing values. | state rule | moves::test_result_includes_unchanged_items_in_input_order |
| S1-R117 | Export/import preserves successful batch receipts and the resulting bookings. | upgrade/compatibility | export_import::test_round_trip_preserves_everything |

## Count by kind

| kind | count |
|---|---|
| interface | 19 |
| state rule | 21 |
| error behaviour | 13 |
| concurrency | 4 |
| retry | 12 |
| time | 17 |
| arithmetic | 4 |
| validation | 18 |
| upgrade/compatibility | 9 |
| **total** | **117** (111 automated, 6 review-only: R04, R05, R06, R09, R35, R101) |

Browser behaviour and visual/product quality do not apply to stage 1 (HTTP API only).

## Hazard list

Each of these is easy to get right in the obvious case and breaks at the edges.

- **H1 Check-then-act races (R01, R38, R54, R111).** Two creates, PATCHes or moves for the same
  table: a separate SELECT and then INSERT/UPDATE double-books. Overlapping starts that differ
  (19:00, 19:30 and 20:00) must also conflict, so a lock or unique index on the exact slot is
  not enough. Signups racing on one email must not both succeed. Tests run 6 to 20 parallel
  writers.
- **H2 Idempotency races and lost responses (R54, R55, R03).** Concurrent identical requests
  with an unused key: exactly one 201, all others 200 with the same body. The stored outcome must
  be written in the same transaction as the booking. Replays after cancel or PATCH return the
  original snapshot, not the current state, and change nothing.
- **H3 Idempotency precedence and scope (R45-R48, R52).** The key is checked after JSON parsing
  and authentication but before validation, so an invalid new body under a used key is 409, not
  422. Keys are per user and per path. 4xx outcomes must not be stored as receipts. Keys over
  255 characters are 422 and an empty key is 400.
- **H4 Half-open intervals (R02, R60, R69).** End == start is not an overlap, in either order.
  The last slot ends exactly at `closes` and is valid. A slot starting at `closes` is invalid.
  Availability must hide the table at every slot that overlaps, not only the slot at the same
  start.
- **H5 Grid anchored at opening time (R20, R68).** A 45-minute grid from 17:15 gives 17:15, 18:00,
  18:45 and 19:30. Computing `minute % slot == 0` is wrong.
- **H6 DST (R88-R92).** Use real instants for duration and overlap: a spring-forward 01:30 + 90 min
  ends at 04:00, and a fall-back 01:30 ends at 02:00 in the second occurrence. A naive wall-clock
  comparison wrongly allows or blocks neighbours (tests check both directions). The repeated hour
  is listed once with the first offset. The gap is absent from availability and is 422 on POST
  and PATCH. Check the slim image has tzdata. The fold handling has to be explicit
  (`fold=0`), and the gap has to be detected by a round-trip check, because zoneinfo silently
  normalises nonexistent times.
- **H7 Validation precedence and types (R25, R28-R32).** `party_size` strings and booleans are
  422, not 400. Python's `bool` is a subclass of `int`, so `True` must be rejected explicitly, and
  so must `2.5` and `null`. Other wrong-type fields are 400. Query integers accept digits only
  (`+4`, `4.0`, `1e9` and ` 4` are 422). Invalid calendar dates (`2030-02-30`) are 422.
  `starts_at_local` with seconds, an offset or `Z` is 422.
- **H8 Error-body shape everywhere (R24).** Framework defaults (FastAPI `{"detail": ...}` for
  404, 405 and request-validation errors, and HTML for 500) violate the contract. Override every
  exception handler, including unknown routes and wrong methods.
- **H9 Content type (R13).** The JSON responses need an explicit `charset=utf-8`. FastAPI's
  default `application/json` does not include it.
- **H10 Ownership leaks (R74, R79, R87, R104).** Another user's reservation is 404, never 403
  or 200, on GET, cancel, PATCH and moves.
- **H11 Cutoff (R78, R82, R109).** It is measured against the current start (not the new one on
  PATCH). Past starts count as "later". Booking in the past is still allowed (R23).
- **H12 Batch atomicity (R110-R113).** Apply moves as one transaction, and check overlap against
  the final set of bookings, excluding all listed bookings' old intervals, so swaps work while
  unchanged listed items keep their occupancy. Report errors in the specified precedence. A
  failed batch must not store a receipt or touch any record.
- **H13 Export/import fidelity (R93-R99, R117).** The snapshot must be taken atomically. Import
  must restore tokens, password hashes, idempotency receipts (including original response
  bodies), references and timestamps verbatim. It must wipe destination users and tokens, be
  repeatable without duplicating anything, validate everything before replacing anything, and
  leave the state untouched on 422. Id and reference counters must continue without colliding
  after import.
- **H14 Reset completeness (R11, R12, R100).** Reset must clear tokens and idempotency keys too,
  hash seeded passwords fast enough to stay under 10 s, accept 64-character ids, and treat seeded
  reservations as occupying.
- **H15 Ordering stability (R61, R73, R114).** Tables come back in fixture order (not sorted by
  id), reservations sorted by `starts_at` descending as instants (not by local string), and batch
  results in input order.
- **H16 Malformed, missing and unknown input (R15, R16, R25, R28).** Unknown body fields and
  query parameters are ignored. A non-object JSON body is 400. Missing required fields are 422.
- **H17 Load (R07, R34).** 50 simultaneous requests on one worker: hashing or blocking SQLite
  calls on the event loop can push other requests past 5 s or cause "database is locked" 500s.

## Assumptions

Where the specification is ambiguous, these are the readings I chose, each with its reason.

- **A1** A successful PATCH returns **200** with the full reservation. §8 does not give the
  success status. 200 matches cancel, and only creates return 201.
- **A2** A booking on a weekday with no opening hours is 422 `outside_opening_hours`, because the
  slot is outside opening hours (§8 table, first row of that code).
- **A3** An unknown `restaurant_id` on `GET /availability` is 404 `not_found`. §5 defines 404 as
  "no such resource", and §8 uses 404 for an unknown restaurant on POST.
- **A4** In `POST /reservation-moves`, a missing `moves` field, an item without a reference, or a
  non-string reference is 422 `validation_failed`. §11's "invalid shape ... gives 422" is
  endpoint-specific and §5 says endpoint-specific rules take precedence. The tests do not cover
  `moves` itself having the wrong JSON type.
- **A5** No stage-1 path returns 403. Ownership failures are specified as 404.
- **A6** "Within `cancellation_cutoff_minutes` of `starts_at`, or later" means a cancel or change
  is allowed only while `now < starts_at - cutoff`. The tests avoid the exact boundary.
- **A7** `party_size=0` and negative values in the availability query are 422 (below 1).
- **A8** On import, an object `state` the service cannot interpret (for example
  `{"bogus": true}`) is an "invalid state", so 422.
- **A9** Reset invalidates every earlier token and idempotency key, because it replaces all state.
- **A10** A seeded user's login returns the fixture `id` as `user_id`.
- **A11** An export must not contain plaintext passwords, because the state is the stored data
  and plaintext storage is forbidden.
- **A12** §3.4's `application/json; charset=utf-8` applies literally to JSON responses.
- **A13** Unknown routes are 404 `not_found` with the error body. Wrong methods are some 4xx with
  the error body, and the tests do not fix the code.
- **A14** Email uniqueness is tested only with an identical string. Case folding is not
  specified and is not tested.
- **A15** PATCH and moves follow the §9 DST rules in the same way as POST.
- **A16** `UTC` is a valid IANA zone name. It is used by fixture restaurant `r_strict`.
- **A17** In §11, "Non-occupancy errors use ordinary amendment codes and take precedence in input
  order" means: any non-occupancy error beats `table_unavailable`; among non-occupancy errors the
  earliest item in input order wins; for one item `cutoff_passed` beats its other errors. Shape
  (422), ownership (404) and mixed-restaurant (422) checks apply to the whole request first.
