"""UserUpdate must carry username/password — their absence is why admin edits to
those two fields silently no-op'd (Pydantic drops unknown keys by default)."""
import pytest
from pydantic import ValidationError

from app.schemas.user import UserUpdate


def test_username_and_password_are_accepted():
    body = UserUpdate(username="newname", password="Str0ng!pass")
    assert body.username == "newname"
    assert body.password == "Str0ng!pass"


def test_omitted_fields_stay_out_of_exclude_unset_dump():
    # update_user applies exclude_unset — a field that is merely absent must not
    # blank out the column it maps to.
    assert UserUpdate(email="a@b.com").model_dump(exclude_unset=True) == {"email": "a@b.com"}


def test_weak_password_rejected():
    with pytest.raises(ValidationError):
        UserUpdate(password="alllowercase1")


def test_none_password_skips_complexity_check():
    assert UserUpdate(full_name="X").password is None


def test_email_whitespace_is_stripped():
    # A pasted address with a trailing space looks identical in the UI but never
    # matches a lookup, and the DB stores it verbatim.
    assert UserUpdate(email="  a@b.com \n").email == "a@b.com"


def test_email_case_is_preserved():
    # Uniqueness is compared case-insensitively in _reject_duplicate; the stored
    # value is left as typed rather than silently rewritten.
    assert UserUpdate(email="Foo@Bar.com").email == "Foo@Bar.com"


@pytest.mark.parametrize("bad", ["noatsign", "a b@c.com", "   "])
def test_malformed_email_rejected(bad):
    with pytest.raises(ValidationError):
        UserUpdate(email=bad)


def test_blank_username_rejected():
    # min_length=1 runs before the strip validator, so " " would otherwise survive.
    with pytest.raises(ValidationError):
        UserUpdate(username="   ")
