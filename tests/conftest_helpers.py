"""Shared fakes for tests: an OpenAI-compatible (Groq-style) client that never touches the network."""
import json
from types import SimpleNamespace


def resp(content=None, tool_calls=None):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))])


def tool_use(name, args, id_="t1"):
    return resp(None, [SimpleNamespace(id=id_, function=SimpleNamespace(name=name, arguments=json.dumps(args)))])


class FakeClient:
    """Returns scripted responses in order, like client.chat.completions.create."""
    def __init__(self, replies):
        self.replies, self.chat, self.calls = list(replies), SimpleNamespace(completions=self), 0

    def create(self, **kw):
        self.calls += 1
        r = self.replies.pop(0)
        return r if hasattr(r, "choices") else resp(r)


class StatusError(Exception):
    def __init__(self, code):
        self.status_code = code


class BrokenClient:
    """Every call fails with the given HTTP status."""
    def __init__(self, code):
        self.calls, self.code, self.chat = 0, code, SimpleNamespace(completions=self)

    def create(self, **kw):
        self.calls += 1
        raise StatusError(self.code)
