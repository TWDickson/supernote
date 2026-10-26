"""Per-user vocabulary that biases transcription toward the spellings a person uses."""

import logging
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select

from supernote.models.extended import VocabularySource
from supernote.server.db.models.file import UserFileDO
from supernote.server.db.models.vocabulary import ROOT_SCOPE_ID, VocabularyTermDO
from supernote.server.db.session import DatabaseSessionManager
from supernote.server.exceptions import FileNotFound, VocabularyTermNotFound
from supernote.server.utils.common_words import is_common

__all__ = [
    "MAX_PROMPT_TERMS",
    "MAX_TERM_LENGTH",
    "VocabularyService",
    "VocabularyTerm",
]

logger = logging.getLogger(__name__)

MAX_PROMPT_TERMS = 200
"""Most terms offered to one prompt; a long list dilutes the instruction."""

MAX_TERM_LENGTH = 64
"""Longest accepted term; device keywords can be whole sentences."""

_MAX_FOLDER_DEPTH = 64


@dataclass(frozen=True)
class VocabularyTerm:
    """A term as seen by callers of `VocabularyService`."""

    id: int
    term: str
    scope_id: int
    source: VocabularySource


def _normalize(term: str) -> str:
    return " ".join(term.split())


def _to_term(row: VocabularyTermDO) -> VocabularyTerm:
    return VocabularyTerm(
        id=row.id,
        term=row.term,
        scope_id=row.scope_id,
        source=VocabularySource(row.source),
    )


class VocabularyService:
    """Stores vocabulary terms and resolves the ones that apply to a notebook.

    A term applies to a notebook when its scope is the notebook, any folder
    above it, or `ROOT_SCOPE_ID`. Harvested terms come from the keywords a
    person typed on the device; removing one suppresses it rather than
    deleting it, so the next harvest does not add it back.
    """

    def __init__(self, session_manager: DatabaseSessionManager) -> None:
        self.session_manager = session_manager

    async def list_terms(self, user_id: int) -> list[VocabularyTerm]:
        """Return the user's active terms, oldest first."""
        async with self.session_manager.session() as session:
            rows = (
                await session.execute(
                    select(VocabularyTermDO)
                    .where(
                        VocabularyTermDO.user_id == user_id,
                        VocabularyTermDO.is_suppressed.is_(False),
                    )
                    .order_by(VocabularyTermDO.create_time, VocabularyTermDO.id)
                )
            ).scalars()
            return [_to_term(row) for row in rows]

    async def add_terms(
        self, user_id: int, terms: Iterable[str], scope_id: int = ROOT_SCOPE_ID
    ) -> list[VocabularyTerm]:
        """Add terms by hand, restoring any that were suppressed in the same scope.

        Raises:
            FileNotFound: `scope_id` is not one of the user's folders or notebooks.
            ValueError: A term is empty or longer than `MAX_TERM_LENGTH`.
        """
        wanted = [_normalize(term) for term in terms]
        if invalid := [t for t in wanted if not t or len(t) > MAX_TERM_LENGTH]:
            raise ValueError(f"Invalid vocabulary term(s): {invalid!r}")

        async with self.session_manager.session() as session:
            if scope_id != ROOT_SCOPE_ID:
                scope = await session.get(UserFileDO, scope_id)
                if not scope or scope.user_id != user_id or scope.is_active != "Y":
                    raise FileNotFound(f"Vocabulary scope {scope_id} not found")

            existing = {
                row.term.casefold(): row
                for row in (
                    await session.execute(
                        select(VocabularyTermDO).where(
                            VocabularyTermDO.user_id == user_id,
                            VocabularyTermDO.scope_id == scope_id,
                        )
                    )
                ).scalars()
            }
            result: list[VocabularyTermDO] = []
            for term in wanted:
                row = existing.get(term.casefold())
                if row is None:
                    row = VocabularyTermDO(
                        user_id=user_id,
                        term=term,
                        scope_id=scope_id,
                        source=VocabularySource.MANUAL.value,
                    )
                    session.add(row)
                    existing[term.casefold()] = row
                elif row.is_suppressed:
                    row.is_suppressed = False
                    row.source = VocabularySource.MANUAL.value
                if row not in result:
                    result.append(row)
            await session.commit()
            return [_to_term(row) for row in result]

    async def remove_term(self, user_id: int, term_id: int) -> None:
        """Remove a term; a harvested one is suppressed instead of deleted.

        Raises:
            VocabularyTermNotFound: The user has no active term with `term_id`.
        """
        async with self.session_manager.session() as session:
            row = await session.get(VocabularyTermDO, (term_id, user_id))
            if not row or row.is_suppressed:
                raise VocabularyTermNotFound(f"Vocabulary term {term_id} not found")
            if row.source == VocabularySource.HARVEST.value:
                row.is_suppressed = True
            else:
                await session.delete(row)
            await session.commit()

    async def harvest(self, user_id: int, file_id: int, keywords: Iterable[str]) -> int:
        """Add a notebook's device keywords to the folder the notebook is in.

        Common words, over-long keywords, and terms already present (or
        suppressed) in that folder or for every notebook are skipped.

        Returns:
            The number of terms added.
        """
        async with self.session_manager.session() as session:
            note = await session.get(UserFileDO, file_id)
            if not note or note.user_id != user_id:
                return 0
            scope_id = note.directory_id
            known = {
                term.casefold()
                for term in (
                    await session.execute(
                        select(VocabularyTermDO.term).where(
                            VocabularyTermDO.user_id == user_id,
                            VocabularyTermDO.scope_id.in_({scope_id, ROOT_SCOPE_ID}),
                        )
                    )
                ).scalars()
            }
            added = 0
            for keyword in keywords:
                term = _normalize(keyword)
                if not term or len(term) > MAX_TERM_LENGTH or is_common(term):
                    continue
                if term.casefold() in known:
                    continue
                known.add(term.casefold())
                session.add(
                    VocabularyTermDO(
                        user_id=user_id,
                        term=term,
                        scope_id=scope_id,
                        source=VocabularySource.HARVEST.value,
                    )
                )
                added += 1
            await session.commit()
        if added:
            logger.info(f"Harvested {added} vocabulary term(s) from file {file_id}")
        return added

    async def terms_for_notebook(self, user_id: int, file_id: int) -> list[str]:
        """Return the terms that apply to a notebook, nearest scope first.

        Within a scope, hand-added terms come before harvested ones. The list
        is capped at `MAX_PROMPT_TERMS`.
        """
        async with self.session_manager.session() as session:
            chain = [file_id]
            current = await session.get(UserFileDO, file_id)
            while (
                current is not None
                and current.user_id == user_id
                and current.directory_id != ROOT_SCOPE_ID
                and len(chain) < _MAX_FOLDER_DEPTH
            ):
                chain.append(current.directory_id)
                current = await session.get(UserFileDO, current.directory_id)
            chain.append(ROOT_SCOPE_ID)

            rows = (
                await session.execute(
                    select(VocabularyTermDO).where(
                        VocabularyTermDO.user_id == user_id,
                        VocabularyTermDO.scope_id.in_(chain),
                        VocabularyTermDO.is_suppressed.is_(False),
                    )
                )
            ).scalars()
            ordered = sorted(
                rows,
                key=lambda row: (
                    chain.index(row.scope_id),
                    row.source != VocabularySource.MANUAL.value,
                    row.create_time,
                    row.id,
                ),
            )

        terms: list[str] = []
        seen: set[str] = set()
        for row in ordered:
            if row.term.casefold() in seen:
                continue
            seen.add(row.term.casefold())
            terms.append(row.term)
        return terms[:MAX_PROMPT_TERMS]
