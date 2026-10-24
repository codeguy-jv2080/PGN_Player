# Recorded chess-piece source

The move and capture cues are excerpts from **Chess Pieces Move (Close)** by
**JJTaynos**, published April 29, 2024 on Freesound under **CC0 1.0 Universal**.
The author describes recording chess pieces moving on a board.

- Author's page: https://freesound.org/people/JJTaynos/sounds/733927/
- Public HQ preview: https://cdn.freesound.org/previews/733/733927_15803939-hq.mp3
- License: https://creativecommons.org/publicdomain/zero/1.0/
- Legal text: [CC0-1.0.txt](../../licenses/CC0-1.0.txt), copied from
  https://raw.githubusercontent.com/spdx/license-list-data/main/text/CC0-1.0.txt.

`chess-pieces-close-hq.mp3` is the openly available preview retrieved on
October 24, 2026. The login-required original WAV download was not used.
`chess-pieces-close.wav` is a decoded copy at 22,050 Hz, mono, 16-bit PCM.
Keeping this input in the source tree makes asset generation and builds
independent of a network or MP3 decoder.

The initial decoding used this command (FFmpeg is a development tool only;
it is not bundled or required by PGN Player):

```text
ffmpeg -nostdin -v error -i chess-pieces-close-hq.mp3 -map_metadata -1 -ac 1 -ar 22050 -c:a pcm_s16le chess-pieces-close.wav
```

SHA-256 checksums:

```text
cd35d8e3032769b3ab3e48b7250fa2b1569bf68389cd1f584536908636700443  chess-pieces-close-hq.mp3
3e3ab886da26b0c89804ae0842ea7eb68c4375511f97291a63d66f729e5f7cff  chess-pieces-close.wav
```

The move uses the hit near **24.41 seconds**; capture uses the stronger hit
near **30.14 seconds**. Each excerpt preserves the complete hit and natural
decay, with a little quiet recording before and after it. Processing uses
only trimming, constant gain, and tiny fades at the outer clip edges. There
is no synthesis, equalization, pitch shifting, or time stretching.

Both clips contain 6,615 samples (300 ms). Move starts at source frame 538,189
(24.407664 s), and capture starts at frame 664,345 (30.129025 s). Each includes
about 5 ms before the contact. A 0.5 ms fade-in and 10 ms fade-out touch only
the quiet outer edges, leaving the attack and natural decay intact.

`scripts/generate_sounds.py` records the exact excerpt boundaries and gain
targets and reproduces the final WAVs offline. Move and capture peaks are
approximately 63.85% and 79.81%, respectively, leaving headroom. The final
`move.wav` and `capture.wav` remain CC0. Only those final WAVs are included
as application assets; the full recording is included in the source archive.
