"""Small, UI-independent value objects used by PGN Player."""

from dataclasses import dataclass


@dataclass(frozen=True)
class GameInfo:
    """Headers and a seek position, without keeping a game's move tree in memory."""

    index: int
    headers: dict[str, str]
    offset: int

    @property
    def title(self) -> str:
        return f"{self.headers.get('White', '?')} — {self.headers.get('Black', '?')}"

    @property
    def search_text(self) -> str:
        return " ".join(self.headers.values()).casefold()
