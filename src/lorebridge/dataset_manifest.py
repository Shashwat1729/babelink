"""Dataset license manifest (spec §46, §54): every corpus used for evaluation records where it came from and
what may be done with it. `lorebridge datasets --check` fails when a field is missing."""
from __future__ import annotations

REQUIRED = ("id", "name", "languages", "license", "allowed_for_redistribution", "allowed_for_training", "source_url", "notes")

MANIFEST: list[dict] = [
    {"id": "gutenberg-24264", "gutenberg_id": 24264, "local_file": "zh_24264.epub", "name": "紅樓夢 (Cao Xueqin), Chinese text", "languages": ["zh"],
     "license": "Public domain in the USA (Project Gutenberg license applies to the PG trademark/boilerplate only)", "allowed_for_redistribution": True,
     "allowed_for_training": True, "source_url": "https://www.gutenberg.org/ebooks/24264",
     "notes": "Source text for the real-corpus evaluation. Traditional characters, hard-wrapped, GBK-era punctuation; stress-tests the importer. Not bundled in the repository: run scripts/fetch_corpus.py."},
    {"id": "gutenberg-9603", "gutenberg_id": 9603, "local_file": "en_9603.epub", "name": "Hung Lou Meng, Book I (H. Bencraft Joly, 1892) - human English translation, chapters 1-24", "languages": ["en"],
     "license": "Public domain in the USA", "allowed_for_redistribution": True, "allowed_for_training": True, "source_url": "https://www.gutenberg.org/ebooks/9603",
     "notes": "Reference translation. Victorian diction, Wade-Giles names (Chia Cheng, Yu-ts'un) and some abridgement, so absolute chrF against modern model output is low; only compare configurations with each other."},
    {"id": "gutenberg-9604", "gutenberg_id": 9604, "local_file": "en_9604.epub", "name": "Hung Lou Meng, Book II (Joly) - chapters 25-56", "languages": ["en"],
     "license": "Public domain in the USA", "allowed_for_redistribution": True, "allowed_for_training": True, "source_url": "https://www.gutenberg.org/ebooks/9604",
     "notes": "Second reference volume; use for later chapters of the same novel."},
    {"id": "gutenberg-51828", "gutenberg_id": 51828, "local_file": "zh_51828.epub", "name": "聊齋志異 (Pu Songling), Chinese text", "languages": ["zh"],
     "license": "Public domain in the USA", "allowed_for_redistribution": True, "allowed_for_training": True, "source_url": "https://www.gutenberg.org/ebooks/51828",
     "notes": "Second Chinese book (many short stories) used for importer/chapter-split robustness; no paired translation in the corpus."},
    {"id": "opus-books", "name": "OPUS Books (Helsinki-NLP)", "languages": ["en", "zh"], "license": "Per-book; mostly public-domain literature - check the OPUS entry",
     "allowed_for_redistribution": False, "allowed_for_training": False, "source_url": "https://opus.nlpl.eu/Books/corpus/version/Books",
     "notes": "Adapter: OPUSAdapter. Not downloaded by default; verify license per language pair before use."},
    {"id": "flores-plus", "name": "FLORES+ (openlanguagedata)", "languages": ["multi"], "license": "CC BY-SA 4.0 (verify on the dataset card)",
     "allowed_for_redistribution": False, "allowed_for_training": False, "source_url": "https://huggingface.co/datasets/openlanguagedata/flores_plus",
     "notes": "Adapter: FLORESAdapter. Sentence-level; evaluation only, not fiction."},
    {"id": "wmt", "name": "WMT news-translation test sets", "languages": ["zh", "en"], "license": "Research use; terms differ per sub-corpus",
     "allowed_for_redistribution": False, "allowed_for_training": False, "source_url": "https://www.statmt.org/wmt24/",
     "notes": "Adapter: WMTAdapter. News domain; evaluation only."},
    # --- researched, not downloaded or wired in yet (docs/DATASETS.md, "Next datasets") ------------------------------
    {"id": "wmt23-literary", "name": "WMT 2023 Discourse-Level Literary Translation (zh-en web novels)", "languages": ["zh", "en"],
     "license": "CC BY-NC-ND 4.0 per the task paper (arXiv 2311.03127); the novels stay with their copyright holders", "allowed_for_redistribution": False,
     "allowed_for_training": False, "source_url": "https://www2.statmt.org/wmt23/literary-translation-task.html",
     "notes": "Best match for this project: document-level Chinese web novels with human English. Evaluation only; read the task page terms before downloading."},
    {"id": "guofeng-webnovel", "name": "GuoFeng Webnovel (Tencent AI Lab / China Literature)", "languages": ["zh", "en"],
     "license": "Copyright held by Tencent AI Lab and China Literature Ltd; research terms are in the repository README", "allowed_for_redistribution": False,
     "allowed_for_training": False, "source_url": "https://github.com/longyuewangdcu/GuoFeng-Webnovel",
     "notes": "Source of the WMT23 literary data: discourse-level web fiction, 14 genres. Request-gated use; never commit copies."},
    {"id": "bwb", "name": "BWB: bilingual web-novel corpus with discourse annotations", "languages": ["zh", "en"],
     "license": "Paper is CC BY 4.0 (arXiv 2210.14667); check the data release for its own terms", "allowed_for_redistribution": False,
     "allowed_for_training": False, "source_url": "https://arxiv.org/abs/2210.14667",
     "notes": "Chinese novels translated by experts; annotated test set for pronouns, entities and discourse. Fits a consistency check on names and pronouns."},
    {"id": "ch2ch", "name": "Ch2Ch: 160 zh-en books for chapter-to-chapter translation", "languages": ["zh", "en"],
     "license": "Not stated on the paper page (arXiv 2407.08978); verify before use", "allowed_for_redistribution": False,
     "allowed_for_training": False, "source_url": "https://arxiv.org/abs/2407.08978",
     "notes": "Chapter-level setting with no sentence alignment, the same shape as this project's pipeline."},
    {"id": "par3", "name": "Par3: paragraph-aligned public-domain novels with human and machine English", "languages": ["multi", "en"],
     "license": "Paper is CC BY 4.0 (arXiv 2210.14250); the novels are public domain", "allowed_for_redistribution": True,
     "allowed_for_training": True, "source_url": "https://arxiv.org/abs/2210.14250",
     "notes": "Paragraph-level references that can be redistributed. Mostly European languages; useful for the long-context logic, not for Chinese idiom."},
    {"id": "dochplt", "name": "DocHPLT: 124M document pairs, 50 languages with English", "languages": ["multi", "en"],
     "license": "Open release; confirm the exact licence on the dataset card (arXiv 2508.13079)", "allowed_for_redistribution": False,
     "allowed_for_training": False, "source_url": "https://arxiv.org/abs/2508.13079",
     "notes": "Web documents, not fiction. Only relevant for fine-tuning experiments, which this project does not do."},
    {"id": "openmantra", "name": "OpenMantra: 214 manga pages, 1,593 sentences (Mantra team, University of Tokyo)", "languages": ["ja", "en", "zh"],
     "license": "Custom licence (GitHub lists it as \"other\"); read the repository terms first", "allowed_for_redistribution": False, "allowed_for_training": False,
     "source_url": "https://github.com/mantra-inc/open-mantra-dataset",
     "notes": "Page images with annotated text and reference translations. Needs a Japanese recogniser, which the bundled RapidOCR (Chinese + English) does not have."},
    {"id": "manga109", "name": "Manga109 (109 Japanese comic books, 21,142 pages)", "languages": ["ja"],
     "license": "Academic non-profit use by author permission; a subset also allows industrial use (arXiv 2005.04425)", "allowed_for_redistribution": False,
     "allowed_for_training": False, "source_url": "http://www.manga109.org/en/",
     "notes": "Text-box and frame annotations; the base for MangaSeg. Use to test text detection and erasing, not translation quality."},
    {"id": "mangaseg", "name": "MangaSeg: pixel masks for Manga109 (CVPR 2025)", "languages": ["ja"],
     "license": "Custom licence in the spirit of CC BY 4.0, on top of the Manga109 terms", "allowed_for_redistribution": False,
     "allowed_for_training": False, "source_url": "https://huggingface.co/datasets/MS92/MangaSegmentation",
     "notes": "Pixel-level masks. The right ground truth for scoring how clean the text erasing is."},
    {"id": "manga-text-detection", "name": "Unconstrained text detection in manga (Del Gobbo and Matuk Herrera, 2020)", "languages": ["ja"],
     "license": "Not stated on the paper page (arXiv 2009.04042); verify", "allowed_for_redistribution": False, "allowed_for_training": False,
     "source_url": "https://arxiv.org/abs/2009.04042", "notes": "Pixel-level text annotations of manga; a second mask set for erase quality."},
    {"id": "user-provided", "name": "User-provided books (e.g. web-novel TXT/EPUB)", "languages": ["any"], "license": "User's responsibility",
     "allowed_for_redistribution": False, "allowed_for_training": False, "source_url": "",
     "notes": "Never committed or redistributed. Metadata only (spec §46)."},
]


def check_manifest() -> list[str]:
    problems = []
    for d in MANIFEST:
        for f in REQUIRED:
            if f not in d or d[f] in (None, ""):
                if f == "source_url" and d.get("id") == "user-provided":
                    continue
                problems.append(f"{d.get('id', '?')}: missing {f}")
    return problems


def format_manifest() -> str:
    rows = ["| id | name | license | redistribute | train |", "|---|---|---|---|---|"]
    for d in MANIFEST:
        rows.append(f"| {d['id']} | {d['name']} | {d['license']} | {'yes' if d['allowed_for_redistribution'] else 'no'} | {'yes' if d['allowed_for_training'] else 'no'} |")
    return "\n".join(rows)
