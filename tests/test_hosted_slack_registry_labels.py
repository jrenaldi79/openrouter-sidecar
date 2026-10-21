"""A team label can't collide with the key name an anonymous student gets."""

import pytest

from openrouter_image_mcp.hosted.slack.registry import ChannelRegistry, new_row


def row(label, channel="C1"):
    return new_row("T1", channel, label, "2027", fusion_panel=("openai/gpt-x",), fusion_analyst="openai/gpt-x",
                   subagent_model="google/gemini-flash")


@pytest.mark.parametrize("label", ["user-0123456789", "user-abcdef0123"])
def test_label_cannot_look_like_an_anonymous_students_key_name(db_target, label):
    reg = ChannelRegistry(db_target)
    with pytest.raises(ValueError, match="student key name"):
        reg.add(row(label))
    assert reg.all() == []
    for fine in ("user-1", "user-0123456789a", "user-0123456xyz", "users-0123456789"):
        reg.add(row(fine, "C" + fine))
