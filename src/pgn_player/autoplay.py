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
        self.loop = False
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

    def _transition_target(self) -> int | None:
        """Choose the game to show at the next playback boundary."""
        collection = self.player.collection
        if collection is None or self.player.game is None:
            return None
        if not self.continue_next:
            return self.player.game_index if self.loop else None
        # Ordinary playback treats a selected side line as a study detour.
        # Explicit Loop mode instead repeats the game or cycles the playlist.
        if not self.loop and not self.include_variations and any(self.player.path):
            return None
        if self.player.game_index + 1 < len(collection):
            return self.player.game_index + 1
        return 0 if self.loop else None

    @property
    def next_delay(self) -> float:
        if self._next_path() is None and self._transition_target() is not None:
            return max(0.0, float(self.between_games_seconds))
        return max(0.05, float(self.delay_seconds))

    def start(self) -> bool:
        self.playing = self.player.game is not None and (
            self._next_path() is not None or self._transition_target() is not None
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
            if self._next_path() is None and self._transition_target() is None:
                self.pause()
            return "move"
        target = self._transition_target()
        if target is not None:
            try:
                if self.player.select_game(target):
                    return "game"
            except PgnLoadError:
                self.pause()
                raise
        self.pause()
        return "stop"
