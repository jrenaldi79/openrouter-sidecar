"""Student-facing copy for the Slack bot: welcome, help, data notice and the off-state lines.

Everything here is our own text, posted as written, so it carries no model output. `help_text` is
entity-escaped once at the end so `<question>` placeholders show as typed instead of forming Slack links.
"""

from __future__ import annotations

from .registry import ChannelRow
from .replies import escape

NOT_SET_UP = "Sidecar isn't set up for this channel. Ask your instructor to add it."
GUEST_OFF = (
    "Sidecar is off in this channel because it now has a guest or is shared outside Northwestern. "
    "Ask your instructor."
)
_BULLETS = [
    (
        "• Messages the bot reads go to the model providers chosen, through OpenRouter. Web searches and "
        "fetches go to a search or fetch provider (Exa, Parallel, Perplexity or Firecrawl), each with its "
        "own retention, and search queries can include words from your conversation."
    ),
    (
        "• Sandbox inputs and outputs stay in the team's container for up to 30 days, visible to "
        "instructors, not to other teams."
    ),
    "• Usage, including who asked what it cost, is visible to everyone in the channel.",
    "• Check with your sponsor before asking about confidential material.",
]
DATA_NOTICE = "\n".join(_BULLETS)

_WELCOME = """Hi, I'm Sidecar. I answer with models other than Claude, so you can compare views with @Claude in the same thread. I read this thread and recent messages in this channel, including Claude's replies, and only when you mention me. Other teams can't see anything from here.

A few first messages to try:
• `@Sidecar ask {example} what's the weakest assumption in Claude's answer above?`
• `@Sidecar compare should we price per seat or per site?`
• `@Sidecar chart this by month` with a CSV attached

{budget} What I send to model and search providers, and how long files are kept, is in `@Sidecar help`, along with every command."""

_COMMANDS = """Commands
• `@Sidecar <question>` asks the channel default. In a thread where I already answered, it continues with the last model that answered.
• `@Sidecar ask <alias> <question>` asks one model. An exact OpenRouter id also works.
• `@Sidecar ask <a> and <b> <question>` asks two models side by side.
• `@Sidecar compare <question>` runs Fusion: a panel of models plus an analyst, with one combined answer.
• `@Sidecar models` lists the aliases and models.
• `@Sidecar set default <alias>` changes the channel default.
• `@Sidecar usage` shows the team's spend this month and who asked what it cost.
• `@Sidecar remember for this channel: <note>` saves a note I read with every request. `@Sidecar what do you remember?` lists the notes and `@Sidecar forget <n>` removes one.
• `@Sidecar help` shows this reference.
• Thread commands: `!restart` starts the thread fresh, `!status` lists running answers and `!stop` cancels them."""


def _example_alias(row: ChannelRow) -> str:
    return "gemini" if "gemini" in row.alias_map else row.default_model


def welcome_text(row: ChannelRow, budget_usd: float | None) -> str:
    if budget_usd is None:
        budget = "Your team has a monthly budget (`@Sidecar usage`)."
    else:
        budget = f"Your team's budget is ${budget_usd:g} a month (`@Sidecar usage`)."
    return _WELCOME.format(example=_example_alias(row), budget=budget)


def _alias_lines(row: ChannelRow) -> str:
    lines = [f"• {alias} → {row.alias_map[alias]}" for alias in sorted(row.alias_map)]
    return "Aliases\n" + "\n".join(lines)


def _model_lines(row: ChannelRow) -> str:
    return (
        f"Default model: {row.default_model}\n"
        f"Fusion panel: {', '.join(row.fusion_panel)}\n"
        f"Fusion analyst: {row.fusion_analyst}"
    )


def help_text(row: ChannelRow) -> str:
    sections = (
        "Sidecar answers with models other than Claude. Mention me with `@Sidecar` and a question.",
        _alias_lines(row),
        _model_lines(row),
        _COMMANDS,
        "What I send and keep\n" + DATA_NOTICE,
    )
    return escape("\n\n".join(sections))
