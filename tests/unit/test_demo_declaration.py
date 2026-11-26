"""The person-stated body part: the safety rules that do not need a database."""

from __future__ import annotations

from pipeline.common.body_part import BodyPartClass, classify_body_part
from pipeline.demo import declaration
from pipeline.demo.declaration import DECLARABLE, Applied


def test_only_a_closed_list_can_be_stated() -> None:
    assert declaration.is_valid("auto")
    assert all(declaration.is_valid(k) for k in DECLARABLE)
    for bad in ("", "wholebody", "HEAD ", "spine", "none", "../etc"):
        assert not declaration.is_valid(bad)


def test_every_statable_value_is_in_the_shared_vocabulary() -> None:
    """A stated value must resolve positively, or S3 would refuse it anyway; and 'head' must be
    head-adjacent so that S7 defaces it."""
    for key, value in DECLARABLE.items():
        assert classify_body_part(value) is not BodyPartClass.UNRECOGNISED, key
    assert classify_body_part(DECLARABLE["head"]) is BodyPartClass.HEAD_ADJACENT
    assert classify_body_part(DECLARABLE["chest"]) is BodyPartClass.KNOWN_NON_HEAD
    assert classify_body_part(DECLARABLE["abdomen"]) is BodyPartClass.KNOWN_NON_HEAD


def test_the_notes_say_what_happened_in_plain_words() -> None:
    assert "your statement (a chest scan) was used" in Applied(applied=1).note("chest")
    assert "files describe a head" in Applied(refused_head_in_description=1).note("chest")
    assert "carry their own" in Applied(already_labelled=1).note("head")
    assert Applied().note("head") == ""
    # a labelled study alongside an applied one does not add the "files win" line
    assert "carry their own" not in Applied(applied=1, already_labelled=1).note("chest")


def test_an_unknown_choice_applies_nothing_and_touches_nothing() -> None:
    class Boom:
        def __getattr__(self, name: str) -> None:
            raise AssertionError("the session must not be used for an invalid choice")

    for choice in ("auto", "wholebody", ""):
        assert (
            declaration.apply_declaration(
                Boom(),  # type: ignore[arg-type]
                [],
                choice,
                read_description=lambda i: None,
            )
            == Applied()
        )
