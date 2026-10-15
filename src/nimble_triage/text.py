"""Safe text helpers shared by command output formats."""


def escape_terminal_controls(text: str) -> str:
    """Render terminal control characters as visible escape sequences."""

    return "".join(
        character
        if character.isprintable()
        else character.encode("unicode_escape").decode("ascii")
        for character in text
    )
