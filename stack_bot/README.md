# stack_bot

Plays **Stack** (Ketchapp) by watching a window and tapping when the
sliding block lines up with the tower.

This has nothing to do with the rest of this repository. It is a
self-contained folder with its own dependencies; the trading code never
imports it and CI never installs it.

## Getting the game onto a screen

Stack is a phone game, so it has to be showing in a window before
anything here is useful. Any of these work, because the bot only ever
looks at pixels and sends a left click:

- an Android emulator (BlueStacks, LDPlayer, Android Studio's emulator)
- your actual phone, mirrored with [scrcpy](https://github.com/Genymobile/scrcpy),
  which forwards clicks to the device
- one of the browser clones ("Tower Blocks", "Stack Tower"), if you just
  want to watch it work

## Install and run

```bash
pip install -r stack_bot/requirements.txt

python -m stack_bot --selftest     # checks the vision and timing, touches nothing
python -m stack_bot --dry-run      # watches and reports, never clicks
python -m stack_bot                # plays
```

On the first run it asks you to drag a box around the play area and
remembers it in `~/.stack_bot.json`. Draw the box around the game's
picture only -- not the emulator's toolbars, and not the score, which
changes and would look like movement. Keep the game window focused and
on top while it plays; the bot clicks wherever the pointer is, and a
window that is behind something else will not get the click.

**Escape stops it.** So does moving the game window, which is worth
knowing before you start.

Useful flags:

| flag | what it does |
| --- | --- |
| `--dry-run` | watch and report without clicking |
| `--verbose` | print the miss distance and learned latency for every drop |
| `--region L,T,W,H` | skip the picker |
| `--reselect` | draw the box again |
| `--max-drops N` | stop after N blocks |
| `--latency S` | first guess at tap latency; it corrects itself anyway |

## How it works

Stack is drawn in isometric projection, and the useful consequence is
that the world's vertical axis is the screen's vertical axis. A block
hovering above the tower is drawn higher up than the surface it lands
on, but at **exactly the same horizontal position**. So a perfect drop
is just the moment the block's screen x equals the tower's screen x --
no camera model, no 3D, no block geometry.

That leaves three jobs.

**Finding the block.** It is the only thing moving, so a difference of
two frames isolates it, whatever colour the palette has drifted to.
Differencing is done with a dilation rather than an erosion: a slow
block shifts two or three pixels between frames, and an opening deletes
a sliver that thin outright.

**Finding the tower.** The background is a smooth vertical gradient, so
subtracting each row's colour -- estimated from the left and right
edges, which are background nearly always -- leaves the tower standing
out. Of what is left, the tower is the piece that runs off the bottom
of the play area; the sliding block is always in mid-air. That one rule
separates them.

**Tapping on time.** A velocity is fitted to the last few sightings and
the crossing time computed from it, then the tap goes out early by the
round-trip latency: screen capture, this process, the OS input queue and
the emulator. That number cannot be known up front, so the bot guesses
90ms and then corrects itself. Stack keeps the overlap between block and
tower and slices off the rest, so after each drop the top face has moved
half as far as the block missed by -- measuring the shift measures the
error, and a few drops in it is tapping early by however long the round
trip really takes.

Two details that turned out to matter, both found by the self-test:

- The observation whose frame pair straddles a bounce sees edges
  belonging to opposite directions, and the velocity it implies is
  nonsense. It gets thrown away.
- The block slides back and forth until it is tapped, so a pass where
  the moment has already gone by costs nothing to skip. The bot commits
  only while the moment is still ahead, then sleeps to it exactly,
  rather than tapping a frame or two late.

## How well it works, and where it stops

`--selftest` builds a synthetic Stack-like scene and plays it. It is not
the real game -- it is a check on the vision and the timing, which is
the part that is actually hard.

Against a scene on the real clock, driving the real loop, it converges
from 25px out to under half a pixel within five drops.

Stepping a simulated clock so capture rate and block speed can be swept,
worst settled error in pixels:

```
    fps |      150      250      400      650      900   px/s
  ------+---------------------------------------------
     30 |     1.17     2.77  no fire  no fire  no fire
     60 |     1.31     1.16     2.12  no fire  no fire
    120 |     2.92     0.95     1.53  no fire  no fire
```

"No fire" is a real limit rather than a bug. The bot has to commit a tap
one latency ahead of the alignment moment, and the alignment point is
halfway along the block's travel. Once the block is so fast that half a
pass takes less time than the round-trip latency, it would have to
decide before the block has even turned around, so it skips the pass and
waits -- forever. Those columns are faster than Stack ever goes and
faster than a human could play, but the shape of the limit is worth
knowing: if it stalls, capture faster or reduce latency, don't fiddle
with the gain.

**This has not been run against the real game.** It was written and
tested headlessly, so the algorithm is verified but the constants are
not. Expect to adjust `--tower-thresh` if the play area has an unusual
background, and to redraw the region if it picks up the score.

Even once tuned, "perfectly in the middle every time" is the target
rather than a promise. Capture and input timing jitter by a few
milliseconds, which at a few hundred pixels a second is a pixel or two;
Stack's tolerance for a perfect drop is small but not zero. Expect long
runs and a lot of perfects, not literal immortality.

One thing worth saying plainly: Stack reports to Game Center and Google
Play leaderboards, so a score set this way sits next to scores people
set by hand. That is your call, but it is not a level playing field.
