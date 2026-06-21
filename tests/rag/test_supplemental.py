import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from rag.wiki.supplemental import answer_supplemental_fact, query_supplemental_facts


def test_dungeon_mod_owner_query_returns_trbeyond_context():
    chunks = query_supplemental_facts("who is the owner of dungeon mod ?")

    assert len(chunks) == 1
    assert chunks[0]["page_title"] == "Tensura Dungeon"
    assert "TRBeyond" in chunks[0]["text"]


def test_dungeon_structure_query_does_not_trigger_supplemental_fact():
    assert query_supplemental_facts("what dungeon structures are there?") == []


def test_dungeon_mod_owner_answer_is_deterministic():
    chunks = query_supplemental_facts("who is the owner of dungeon mod ?")
    answer = answer_supplemental_fact("who is the owner of dungeon mod ?", chunks)

    assert answer is not None
    assert "TRBeyond" in answer


def test_dungeon_beatable_query_returns_context_and_answer():
    chunks = query_supplemental_facts("is the dungeon beatable?")
    answer = answer_supplemental_fact("is the dungeon beatable?", chunks)

    assert len(chunks) == 1
    assert "boss fights" in chunks[0]["text"]
    assert answer is not None
    assert "designed to be cleared" in answer
