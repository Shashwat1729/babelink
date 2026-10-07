# Regression Log

Every fixed bug should leave a test here and in `tests/`.

| Bug | Test | Fix |
|---|---|---|
| Character name translated literally (no canonical entity) | terminology guardrail test `test_guardrail_keeps_terms` | canonical terms locked in glossary + guardrail |
| One cultivation level, three translations | `test_contradictions_detected` | drift detection writes conflicts to Story Bible |
| Retranslation after term change reused stale TM | `test_mark_stale_by_term` | STALE segments skip TM lookup |
| Exported EPUB lost chapter titles in NCX | `test_end_to_end_epub` | rebuild `book.toc` from matched items |
| Translated text lost paragraph classes/alignment | `test_replace_paragraphs_preserves_tag_attributes` | keep tag attributes, replace only text |
