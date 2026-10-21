"""Shared helpers for the Slack channel tool tests (not a test module)."""

import json
import re

from openrouter_image_mcp.hosted.slack.api import SlackMessage

ROOT = "1700000100.000001"
ERROR = "Tool error"



def ts(n):
    return f"1700{n:06d}.000001"


def msg(n, text=None, user="U1", *, label=None, subtype=None, thread_ts=None):
    return SlackMessage(ts(n), user, text if text is not None else f"message {n}", label, subtype, thread_ts)


class Spy:
    """Wraps the fake Slack API, recording each call and optionally failing every call with a non-Slack error."""

    def __init__(self, inner):
        self.inner, self.calls, self.boom = inner, [], None

    def __getattr__(self, name):
        method = getattr(self.inner, name)

        async def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            if self.boom is not None:
                raise self.boom
            return await method(*args, **kwargs)

        return call

    def reads(self, name):
        return [args for method, args, _ in self.calls if method == name]



def fill(slack, count, channel="C1", **kwargs):
    slack.history[channel] = [msg(n, **kwargs) for n in range(1, count + 1)]


async def run(tools, name, **args):
    return await tools.call(name, json.dumps(args))


def numbers(output):
    return [int(m) for m in re.findall(r"Ann: message (\d+)", output)]


def before_in(output):
    return re.search(r'before="(\d+\.\d+)"', output).group(1)


