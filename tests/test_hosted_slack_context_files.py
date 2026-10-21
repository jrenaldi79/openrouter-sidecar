import pytest
from slack_fakes import FakeSlack

from openrouter_image_mcp.hosted.slack.api import SlackFile
from openrouter_image_mcp.hosted.slack.context_files import safe_name, trigger_files
from openrouter_image_mcp.session_files import MAX_FILE_CHARS, MAX_PATH_CHARS


def slack_file(name, *, mimetype="text/plain", size=10, fid="F1"):
    return SlackFile(fid, name, mimetype, size, f"https://files.slack.com/{fid}/{name}")


@pytest.fixture
def slack():
    return FakeSlack()


@pytest.mark.parametrize("raw,expected", [
    ("data.csv", "data.csv"),
    ("../../etc/passwd", "passwd"),
    ("a\\b\\c.txt", "c.txt"),
    (".hidden", "hidden"),
    ("...", "file"),
    ("", "file"),
    ("..", "file"),
    ("x/", "file"),
    ("tab\tname.md", "tabname.md"),
])
def test_safe_name(raw, expected):
    assert safe_name(raw) == expected


def test_safe_name_bounded_keeps_extension():
    name = safe_name("a" * 1000 + ".csv")
    assert name.endswith(".csv") and len("slack/files/" + name + "-99") <= MAX_PATH_CHARS


async def test_text_file_becomes_packet(slack):
    f = slack_file("notes.md", mimetype="application/octet-stream")  # extension qualifies
    slack.files[f.url] = "héllo".encode()
    packets, listed = await trigger_files(slack, [f])
    assert [(p.path, p.content) for p in packets] == [("slack/files/notes.md", "héllo")] and listed == []


async def test_non_text_listed(slack):
    f = slack_file("pic.png", mimetype="image/png")
    assert await trigger_files(slack, [f]) == ([], ["pic.png"])


async def test_download_error_listed(slack):
    f = slack_file("a.txt")
    slack.fail_next("download", "http_500")
    assert await trigger_files(slack, [f]) == ([], ["a.txt"])


async def test_missing_download_listed(slack):
    assert await trigger_files(slack, [slack_file("a.txt")]) == ([], ["a.txt"])


async def test_non_utf8_listed(slack):
    f = slack_file("a.txt")
    slack.files[f.url] = b"\xff\xfe\x00bad"
    assert await trigger_files(slack, [f]) == ([], ["a.txt"])


async def test_over_char_limit_listed(slack):
    f = slack_file("a.txt")
    slack.files[f.url] = b"x" * (MAX_FILE_CHARS + 1)
    assert await trigger_files(slack, [f]) == ([], ["a.txt"])


async def test_declared_size_over_limit_not_downloaded(slack):
    f = slack_file("a.txt", size=MAX_FILE_CHARS * 4 + 1)
    slack.fail_next("download", "should_not_be_consumed")
    assert await trigger_files(slack, [f]) == ([], ["a.txt"])
    assert slack._failures["download"]  # the queued failure was never reached


async def test_untrusted_host_listed(slack):
    f = SlackFile("F1", "a.txt", "text/plain", 1, "https://evil.example/a.txt")
    slack.fail_next("download", "untrusted_host")
    assert await trigger_files(slack, [f]) == ([], ["a.txt"])


async def test_duplicate_names_get_suffix(slack):
    files = [slack_file("a.csv", fid="F1"), slack_file("a.csv", fid="F2"), slack_file("a.csv", fid="F3")]
    for f in files:
        slack.files[f.url] = b"1"
    packets, _ = await trigger_files(slack, files)
    assert [p.path for p in packets] == ["slack/files/a.csv", "slack/files/a-2.csv", "slack/files/a-3.csv"]


async def test_hostile_name_stays_inside_prefix(slack):
    f = slack_file("../../x.py")
    slack.files[f.url] = b"print(1)"
    packets, _ = await trigger_files(slack, [f])
    assert packets[0].path == "slack/files/x.py"


async def test_budget_lists_files_that_do_not_fit(slack):
    first, second = slack_file("a.csv", fid="F1"), slack_file("b.csv", fid="F2")
    slack.files[first.url] = b"x" * 60
    slack.files[second.url] = b"y" * 60
    packets, listed = await trigger_files(slack, [first, second], 100)
    assert [p.path for p in packets] == ["slack/files/a.csv"] and listed == ["b.csv"]


async def test_budget_counts_only_attached_files(slack):
    big, small = slack_file("big.csv", fid="F1"), slack_file("small.csv", fid="F2")
    slack.files[big.url] = b"x" * 200
    slack.files[small.url] = b"y" * 60
    packets, listed = await trigger_files(slack, [big, small], 100)
    assert [p.path for p in packets] == ["slack/files/small.csv"] and listed == ["big.csv"]


async def test_zero_budget_lists_everything(slack):
    f = slack_file("a.txt")
    slack.files[f.url] = b"x"
    assert await trigger_files(slack, [f], 0) == ([], ["a.txt"])
