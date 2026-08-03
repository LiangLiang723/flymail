"""Add a standard-parser FULLTEXT index alongside the CJK ngram index."""

from flymail.infrastructure.db.migrations import Migration


def build_migration(*, use_ngram: bool) -> Migration:
    return Migration(
        version=17,
        name="hybrid_fulltext_search",
        metadata={
            "fulltext_parser": "hybrid" if use_ngram else "standard",
            "ascii_index": "ft_body_search_standard",
            "cjk_index": "ft_body_search" if use_ngram else "ft_body_search_standard",
        },
        statements=(
            """
            ALTER TABLE body_search_documents
            ADD FULLTEXT KEY ft_body_search_standard (
                body_text, subject_text, participants_text
            )
            """,
        ),
    )
