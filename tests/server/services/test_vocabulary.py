import pytest

from supernote.models.extended import VocabularySource
from supernote.server.db.models.file import UserFileDO
from supernote.server.db.models.vocabulary import ROOT_SCOPE_ID
from supernote.server.db.session import DatabaseSessionManager
from supernote.server.exceptions import FileNotFound, VocabularyTermNotFound
from supernote.server.services.vocabulary import (
    MAX_PROMPT_TERMS,
    VocabularyService,
    VocabularyTerm,
)

USER_ID = 100
OTHER_USER_ID = 200
WORK_FOLDER_ID = 10
PROJECTS_FOLDER_ID = 11
NOTEBOOK_ID = 20
SIBLING_ID = 21
ELSEWHERE_ID = 22


@pytest.fixture
def vocabulary_service(session_manager: DatabaseSessionManager) -> VocabularyService:
    return VocabularyService(session_manager)


@pytest.fixture(autouse=True)
async def tree(session_manager: DatabaseSessionManager) -> None:
    """Work/Projects/{Plan.note, Notes.note}, plus Journal.note at the root."""
    async with session_manager.session() as session:
        session.add_all(
            [
                UserFileDO(
                    id=WORK_FOLDER_ID,
                    user_id=USER_ID,
                    directory_id=ROOT_SCOPE_ID,
                    file_name="Work",
                    is_folder="Y",
                ),
                UserFileDO(
                    id=PROJECTS_FOLDER_ID,
                    user_id=USER_ID,
                    directory_id=WORK_FOLDER_ID,
                    file_name="Projects",
                    is_folder="Y",
                ),
                UserFileDO(
                    id=NOTEBOOK_ID,
                    user_id=USER_ID,
                    directory_id=PROJECTS_FOLDER_ID,
                    file_name="Plan.note",
                ),
                UserFileDO(
                    id=SIBLING_ID,
                    user_id=USER_ID,
                    directory_id=PROJECTS_FOLDER_ID,
                    file_name="Notes.note",
                ),
                UserFileDO(
                    id=ELSEWHERE_ID,
                    user_id=USER_ID,
                    directory_id=ROOT_SCOPE_ID,
                    file_name="Journal.note",
                ),
            ]
        )
        await session.commit()


async def test_add_and_list_terms(vocabulary_service: VocabularyService) -> None:
    added = await vocabulary_service.add_terms(USER_ID, ["Lovelace", "  Babbage  "])
    assert [(t.term, t.scope_id, t.source) for t in added] == [
        ("Lovelace", ROOT_SCOPE_ID, VocabularySource.MANUAL),
        ("Babbage", ROOT_SCOPE_ID, VocabularySource.MANUAL),
    ]
    assert await vocabulary_service.list_terms(USER_ID) == added
    assert await vocabulary_service.list_terms(OTHER_USER_ID) == []


async def test_add_terms_is_idempotent_and_case_insensitive(
    vocabulary_service: VocabularyService,
) -> None:
    first = await vocabulary_service.add_terms(USER_ID, ["Lovelace"])
    again = await vocabulary_service.add_terms(USER_ID, ["lovelace", "LOVELACE"])
    assert again == first
    assert len(await vocabulary_service.list_terms(USER_ID)) == 1


async def test_add_terms_validates_scope_and_terms(
    vocabulary_service: VocabularyService,
) -> None:
    with pytest.raises(FileNotFound):
        await vocabulary_service.add_terms(USER_ID, ["Lovelace"], scope_id=99999)
    with pytest.raises(FileNotFound):
        await vocabulary_service.add_terms(
            OTHER_USER_ID, ["Lovelace"], scope_id=WORK_FOLDER_ID
        )
    with pytest.raises(ValueError, match="Invalid vocabulary term"):
        await vocabulary_service.add_terms(USER_ID, ["   "])
    with pytest.raises(ValueError, match="Invalid vocabulary term"):
        await vocabulary_service.add_terms(USER_ID, ["x" * 65])
    assert await vocabulary_service.list_terms(USER_ID) == []


async def test_harvest_scopes_to_folder_and_filters(
    vocabulary_service: VocabularyService,
) -> None:
    await vocabulary_service.add_terms(USER_ID, ["Lovelace"])
    added = await vocabulary_service.harvest(
        USER_ID,
        NOTEBOOK_ID,
        ["Kestrel", "kestrel", "events", "lovelace", "", "y" * 65, "action item"],
    )
    assert added == 2
    harvested = [
        (t.term, t.scope_id)
        for t in await vocabulary_service.list_terms(USER_ID)
        if t.source == VocabularySource.HARVEST
    ]
    assert harvested == [
        ("Kestrel", PROJECTS_FOLDER_ID),
        ("action item", PROJECTS_FOLDER_ID),
    ]
    assert await vocabulary_service.harvest(USER_ID, NOTEBOOK_ID, ["Kestrel"]) == 0


async def test_harvest_ignores_files_of_other_users(
    vocabulary_service: VocabularyService,
) -> None:
    assert (
        await vocabulary_service.harvest(OTHER_USER_ID, NOTEBOOK_ID, ["Kestrel"]) == 0
    )
    assert await vocabulary_service.harvest(USER_ID, 99999, ["Kestrel"]) == 0


async def test_removed_harvest_term_stays_removed(
    vocabulary_service: VocabularyService,
) -> None:
    await vocabulary_service.harvest(USER_ID, NOTEBOOK_ID, ["Kestrel"])
    [term] = await vocabulary_service.list_terms(USER_ID)

    await vocabulary_service.remove_term(USER_ID, term.id)
    assert await vocabulary_service.list_terms(USER_ID) == []
    assert await vocabulary_service.harvest(USER_ID, SIBLING_ID, ["Kestrel"]) == 0
    with pytest.raises(VocabularyTermNotFound):
        await vocabulary_service.remove_term(USER_ID, term.id)

    # Adding it by hand in the same scope brings it back as the user's own term
    [restored] = await vocabulary_service.add_terms(
        USER_ID, ["Kestrel"], scope_id=PROJECTS_FOLDER_ID
    )
    assert restored == VocabularyTerm(
        id=term.id,
        term="Kestrel",
        scope_id=PROJECTS_FOLDER_ID,
        source=VocabularySource.MANUAL,
    )


async def test_remove_manual_term_deletes_it(
    vocabulary_service: VocabularyService,
) -> None:
    [term] = await vocabulary_service.add_terms(USER_ID, ["Lovelace"])
    with pytest.raises(VocabularyTermNotFound):
        await vocabulary_service.remove_term(OTHER_USER_ID, term.id)

    await vocabulary_service.remove_term(USER_ID, term.id)
    assert await vocabulary_service.list_terms(USER_ID) == []
    assert await vocabulary_service.harvest(USER_ID, NOTEBOOK_ID, ["Lovelace"]) == 1


async def test_terms_for_notebook_orders_nearest_scope_first(
    vocabulary_service: VocabularyService,
) -> None:
    await vocabulary_service.add_terms(USER_ID, ["Everywhere"])
    await vocabulary_service.add_terms(USER_ID, ["WorkWide"], scope_id=WORK_FOLDER_ID)
    await vocabulary_service.harvest(USER_ID, NOTEBOOK_ID, ["Harvested"])
    await vocabulary_service.add_terms(
        USER_ID, ["ProjectWide"], scope_id=PROJECTS_FOLDER_ID
    )
    await vocabulary_service.add_terms(USER_ID, ["PlanOnly", "everywhere"], NOTEBOOK_ID)

    assert await vocabulary_service.terms_for_notebook(USER_ID, NOTEBOOK_ID) == [
        "PlanOnly",
        "everywhere",
        "ProjectWide",
        "Harvested",
        "WorkWide",
    ]
    assert await vocabulary_service.terms_for_notebook(USER_ID, SIBLING_ID) == [
        "ProjectWide",
        "Harvested",
        "WorkWide",
        "Everywhere",
    ]
    assert await vocabulary_service.terms_for_notebook(USER_ID, ELSEWHERE_ID) == [
        "Everywhere"
    ]
    assert await vocabulary_service.terms_for_notebook(OTHER_USER_ID, NOTEBOOK_ID) == []


async def test_terms_for_notebook_is_capped(
    vocabulary_service: VocabularyService,
) -> None:
    await vocabulary_service.add_terms(
        USER_ID, [f"Term{i}" for i in range(MAX_PROMPT_TERMS + 5)]
    )
    terms = await vocabulary_service.terms_for_notebook(USER_ID, NOTEBOOK_ID)
    assert terms == [f"Term{i}" for i in range(MAX_PROMPT_TERMS)]
