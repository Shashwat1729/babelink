# Regression Log

Every fixed bug should leave a test here and in `tests/`.

| Bug | Test | Fix |
|---|---|---|
| Character name translated literally (no canonical entity) | terminology guardrail test `test_guardrail_keeps_terms` | canonical terms locked in glossary + guardrail |
| One cultivation level, three translations | `test_contradictions_detected` | drift detection writes conflicts to Story Bible |
| Retranslation after term change reused stale TM | `test_mark_stale_by_term` | STALE segments skip TM lookup |
| Exported EPUB lost chapter titles in NCX | `test_end_to_end_epub` | rebuild `book.toc` from matched items |
| Translated text lost paragraph classes/alignment | `test_replace_paragraphs_preserves_tag_attributes` | keep tag attributes, replace only text |
| qwen3 models "think" for 20-100 s per call on CPU and may return only reasoning text | `test_ollama_disables_thinking_and_retries_without_it_on_old_servers` | Ollama requests send `think: false`; retried without it if the server rejects the field |
| First line of a hard-wrapped paragraph after `CHAPTER I.` became part of the chapter title | `test_wrapped_paragraph_is_not_taken_as_chapter_subtitle` | subtitle line accepted only when a blank line follows it; wrapped lines are re-joined into one paragraph |
| Empty glossary silently dropped from prompts, terms drifted | `test_second_chapter_reuses_everything_from_the_first` | glossary and Bible slices built per segment; recording provider asserts they reach the prompt |
| Re-analysing a pasted chapter duplicated terms and QA findings | `test_second_chapter_reuses_everything_from_the_first`, `test_run_qa_moves_states_and_never_touches_human_approved` | upsert terms by source form; QA replaces a segment's findings instead of appending |
| Segments flagged `NEEDS_REVIEW` were retranslated on every run (endless loop) | `test_refusal_flags_segment_and_run_continues` | `open_segments` = UNPROCESSED / ANALYZED / STALE only; only QA *errors* flag a segment |
| Web job thread crashed with "SQLite objects created in a thread" | `tests/test_web.py` | connection opened with `check_same_thread=False` (jobs and FastAPI's threadpool use their own handles) |
| `validate_epub` raised on a missing / unreadable file | `test_validate_flags_unfinished_and_untranslated_epub`, `test_full_flow` | `OSError` reported as a problem; CLI exits 1 |
| Project dialog reverted language selects to the first option | `scripts/ui_smoke.py` (creates a zh project) | reset the form before filling selects |
| Glossary filled with ordinary words ("一名" = a, "废物" = waste, "身体" = body); enforcing them rewrote normal prose and cost up to 2.8 chrF | `test_term_suggestions_keep_names_and_drop_common_words` (test_quality.py) | `_is_common_word`: a cased rendering that starts lower-case is dropped; terms prompt asks for Title Case names only |
| Chapter titles of uploaded comic pages showed the server's `<id>_` file prefix | `scripts/ui_pages_smoke.py` (title assertion) | the job passes the original stem as the chapter title |
| `import_images` job accepted upload names from the client, so `../../x` could point outside the uploads folder | `test_job_refuses_upload_names_that_climb_out_of_the_folder` | names must be bare file names |
| `lorebridge datasets` crashed on a Windows console (cp1252) because dataset names contain CJK | `test_datasets_prints_on_a_cp1252_console` | CLI reconfigures stdout/stderr with `errors="replace"` |
| Held-out report printed "floor 100.0" on one-chapter books (the "unrelated" chapter was the reference itself) | `scripts/eval_heldout.py` run | floor is skipped when the only other chapter is the reference |
| Double click on "Add and translate" added the chapter twice and started two jobs (second got 409) | `test_paste_with_the_same_key_adds_one_chapter_and_one_job`, `test_concurrent_double_click_adds_once`, `scripts/ui_live_smoke.py` (one POST) | client `once()` guard + disabled buttons; writes carry an `Idempotency-Key`, the server replays the first answer; paste checks the job slot before adding anything |
| Same double-submit on create work, upload, paste when busy | `test_creating_a_work_twice_with_the_same_key_makes_one`, `test_paste_and_translate_while_busy_is_refused_before_anything_is_added`, `test_page_upload_while_busy_is_refused_and_leaves_no_files` | same key mechanism; busy checks run before any write; saved uploads are removed when a request fails |
| A failed request was remembered and a retry replayed the error | `test_failed_request_is_not_remembered` | only successful answers are cached; the idempotency cache is bounded (`test_idempotency_memory_is_bounded`) |
| Adding the same file twice duplicated every chapter | `test_same_file_added_twice_is_refused_and_does_not_duplicate_chapters`, `test_unreadable_file_can_be_retried` | upload stored under a content-hash name, second import answers 409 |
| Importing a big EPUB froze the whole server | `test_import_does_not_block_the_server` | import runs in the threadpool under an import lock |
| A running translate or refine job overwrote text the user had just edited or approved | `test_translate_never_overwrites_a_paragraph_a_person_edited_meanwhile`, `test_refine_never_overwrites_a_paragraph_a_person_edited_meanwhile` | writes are conditional on the state the job read (`update_segment(only_if_state=...)`); translation memory is only fed when the write landed |
| Stop waited for the paragraph being written to finish (minutes on CPU) | `test_stop_ends_the_paragraph_being_written_not_just_the_next_one`, `test_job_reports_that_it_is_stopping` | the streaming callback raises `Stopped`; closing the HTTP stream cancels the generation; the button shows "Stopping…" |
| Retranslate left a paragraph in a stale state when another job held the slot | `test_retranslate_puts_the_paragraph_back_when_the_job_slot_is_lost` | prior state restored when the slot is lost |
