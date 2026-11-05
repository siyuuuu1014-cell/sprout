"""Final-plan input is separate from ordinary permission menus."""
import pytest

from Sprout.cli.commands import chat


@pytest.mark.parametrize("answer, expected", [
    ("", False), ("y", False), ("approve", False),
    ("APPLY deadbeef", False), (None, False),
    ("APPLY 01234567", True), ("apply 01234567", False),
])
async def test_exact_plan_confirmation(monkeypatch, answer, expected):
    prompts = []

    class Prompt:
        async def ask_async(self):
            return answer

    def text(message, **kwargs):
        prompts.append(message)
        assert kwargs.get("default", "") == ""
        return Prompt()

    monkeypatch.setattr(chat.questionary, "text", text)
    assert await chat._confirm_exact_apply_plan("01234567" + "a" * 56) is expected
    assert "APPLY 01234567" in prompts[0]


@pytest.mark.parametrize("error", [EOFError, KeyboardInterrupt])
async def test_final_confirmation_cancel(monkeypatch, error):
    class Prompt:
        async def ask_async(self):
            raise error

    monkeypatch.setattr(chat.questionary, "text", lambda *a, **k: Prompt())
    assert not await chat._confirm_exact_apply_plan("a" * 64)


@pytest.mark.parametrize("digest", ["", "abc", "g" * 64, None])
async def test_invalid_plan_digest_never_prompts(monkeypatch, digest):
    def unexpected(*args, **kwargs):
        pytest.fail("Invalid plan must not be presented for approval")

    monkeypatch.setattr(chat.questionary, "text", unexpected)
    assert not await chat._confirm_exact_apply_plan(digest)
