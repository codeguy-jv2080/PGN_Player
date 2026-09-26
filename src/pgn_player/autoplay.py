"""Timer-independent playback; the UI owns and cancels its single timer."""

from .playback import PlaybackController
from .pgn_loader import PgnLoadError


class AutoplayController:
    def __init__(self, player: PlaybackController) -> None:
        self.player = player
        self.playing = False
        self.delay_seconds = 2.0
        self.between_games_seconds = 1.0
        self.continue_next = True
        self.include_variations = False

    def _next_path(self) -> tuple[int, ...] | None:
        node = self.player.node
        if node is None:
            return None
        path = self.player.path
        if node.variations:
            return path + (0,)
        if self.include_variations:
            # Preorder depth-first: complete a line, then visit each recorded
            # sibling in PGN order. Changing branch is an explicit board jump.
            while node.parent is not None:
                index = path[-1]
                parent = node.parent
                path = path[:-1]
                if index + 1 < len(parent.variations):
                    return path + (index + 1,)
                node = parent
        return None

    def _has_next_game(self) -> bool:
        collection = self.player.collection
        return bool(
            self.continue_next
            and collection is not None
            and self.player.game_index + 1 < len(collection)
            # With ordinary playback, clicking a side line is a focused study
            # detour. Finish that line without unexpectedly leaving its game.
            and (self.include_variations or all(index == 0 for index in self.player.path))
        )

    @property
    def next_delay(self) -> float:
        if self._next_path() is None and self._has_next_game():
            return max(0.0, float(self.between_games_seconds))
        return max(0.05, float(self.delay_seconds))

    def start(self) -> bool:
        self.playing = self.player.game is not None and (
            self._next_path() is not None or self._has_next_game()
        )
        return self.playing

    def pause(self) -> None:
        self.playing = False

    def toggle(self) -> bool:
        if self.playing:
            self.pause()
        else:
            self.start()
        return self.playing

    def step(self) -> str:
        if not self.playing:
            return "stop"
        path = self._next_path()
        if path is not None:
            self.player.go_to(path)
            if self._next_path() is None and not self._has_next_game():
                self.pause()
            return "move"
        if self._has_next_game():
            try:
                if self.player.next_game():
                    return "game"
            except PgnLoadError:
                self.pause()
                raise
        self.pause()
        return "stop"
