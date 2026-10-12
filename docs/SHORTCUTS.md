# Keyboard and mouse controls

| Key | Action |
| --- | --- |
| Left | Previous move |
| Right | Next move |
| Home | Starting position |
| End | Last move in the current line |
| Ctrl+Left | Previous game |
| Ctrl+Right | Next game |
| Numpad 8 | Next game |
| Numpad 2 | Previous game |
| Numpad 4 | Previous move |
| Numpad 6 | Next move |
| Numpad 7 | Starting position |
| Numpad 9 | Last move in the current line |
| Space | Play / pause (outside Guess the Move) |
| F | Flip board |
| Ctrl+O | Open PGN |
| Ctrl+G | Toggle Guess the Move |
| Ctrl++ | Increase notation font size |
| Ctrl+- | Decrease notation font size |
| Ctrl+0 | Reset notation font size to 12 pt |

Numeric keypad navigation works with Num Lock on or off. The regular number row
is unaffected. These shortcuts use the same navigation as the existing buttons;
move navigation stops at the start or end of the current line. Numpad 7 returns
to the current game's starting position, and Numpad 9 follows the selected
variation to its end when applicable. Previous/next game stops at the first/last
game without wrapping, even when Loop playback is enabled.

Over the board squares, scroll up for the previous move or down for the next
move. Each standard wheel notch moves one ply (one player's move), without
scrolling the surrounding interface. Scrolling outside the board keeps its
normal behavior, including scrolling notation and the game list.

Playback and notation-size shortcuts are ignored while typing into a text entry
or adjusting an editable control. Click any notation move to jump to its position. Use the
mainline control to leave a variation. The game list can be collapsed and pane
dividers can be dragged to resize the board and notation areas.

The **Hide notation / Show notation** button and **View → Show notation** toggle
the right-hand sidebar. The board uses the freed space, and the previous pane
width returns when shown. Visibility is remembered locally for this edition;
autoplay and Guess the Move continue without interruption.

**View → Show board coordinates** toggles the a–h and 1–8 labels around the board,
independently of the notation sidebar. The choice is remembered for this edition.

View → Notation Font Size also offers 10, 12, 14, 16 and 18 pt. Ctrl+= is an
alternative to Ctrl++ for increasing the size. Only notation changes; the current
move remains in view and the selected size is saved locally.

Manual navigation, including wheel and keypad controls, pauses autoplay. Press
Play or Space to resume when Guess the Move is off. Guess the Move stays active
across games, positions and PGN files until you click its button again or press
Ctrl+G. Navigation preserves the selected side and variation-acceptance setting
and continues to hide upcoming moves and comments.
