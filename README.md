# Geomorph Shipyard (Python)

A desktop app for creating deck plans of sci-fi spaceships from geomorph tiles, written in Python.

You place geomorph parts on a map, across as many decks as the ship needs, turn and flip them, watch the tonnage add up, then save the ship or export it as an image. It runs on your own computer with no web browser and no account.

## Running it

You need Python 3.10 or newer. On a Mac, double-click `Geomorph Shipyard.command` in Finder. The first run sets everything up. On any system you can also do it by hand.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m shipyard
```

The images are a separate download, about 108 MB. Until you have them the app still works, showing each part as a labelled placeholder.

```bash
python3 tools/fetch_geomorphs.py
```

Add `--high-res` to also keep the larger set, about 231 MB, which is used only for exported images. The map always shows the low-res set, as it is already sharper than the map can zoom. If you have a clone of the original project next to this folder with its images already populated, the app will find and use those for the map.

## Using it

- Choose a part in the Library tab and press Add, or double-click it.
- Click a part on the map to select it, and drag it to move it. Parts snap to the map squares.
- Drag empty space to pan, and scroll to zoom.
- Shift-click (or `Cmd`-click) parts to select several, and Shift-click a selected part to drop it from the selection. `Cmd`+`a` selects the whole deck.
- Drag any selected part to move the group. Arrow keys, cut, copy, paste, clone, delete and Move to deck all work on the group too. Rotate, flip, show cargo and replace work on one part at a time.
- Pasted parts keep their layout, rotation and flip, land at the centre of the map, and stay selected ready to drag. You can paste onto a different deck.
- Press Replace selected to swap the selected part for the one chosen in the library, in the same place.
- The Parts used tab lists everything on the ship, which helps when parts overlap.
- The Tonnage tab adds up the ship in displacement tons. The figures are a rough guide.

### Decks

Each deck is a tab above the map, with its own parts.

- Press Add deck or Duplicate deck. Duplicating is a quick way to start a deck with the same hull.
- Double-click a tab to rename it, and drag a tab to reorder the decks.
- Use `Page Up` and `Page Down` to step between decks.
- Choose a deck in Trace over to show it faintly under the one you are editing, which helps with lining up stairs and lifts.
- Part > Move to deck sends the selected part to another deck.
- The Tonnage tab shows the whole ship, plus a line for each deck.
- File > Export this deck saves one deck. File > Export all decks saves them all in one image, lined up one above another.

### Keys

| Key | Action |
| --- | --- |
| Arrow keys | Move the selected parts one square |
| `r` / `R` | Rotate right / left |
| `s` | Flip the part (wings and similar parts only) |
| `o` | Show or hide cargo and vehicles (where the part has them) |
| `c` | Clone the part |
| `delete` | Delete the part |
| `Cmd`+`x`, `Cmd`+`c`, `Cmd`+`v` | Cut, copy and paste (`Ctrl` outside macOS) |
| `Cmd`+`a` | Select the whole deck |
| `escape` | Deselect the part |

Ship files use the same JSON format as the original, so its files open here. A one-deck ship saved here is the same format too. A ship with several decks adds a deck number to each part, and the original will show those decks piled together. Keep your own copies, as nothing is stored inside the app.

This first version covers the deck plan builder only. The original's tonnage calculator, vehicle calculator and random ship button are not included.

## Based on Geomorph Shipyard

This is a Python conversion of [Geomorph Shipyard](https://gitlab.com/IvanSanchez/geomorph-shipyard) by [Iván Sánchez Ortega](https://ivan.sanchezortega.es/), a web app written in SvelteKit and Leaflet. The idea, the design and the geomorph parts data in `shipyard/data` all come from that project. You can [use the original online](https://ivan.sanchezortega.es/geomorph-shipyard/).

This version is not affiliated with or endorsed by Iván Sánchez Ortega, so please report problems with it here and not to the original project.

## Licences

This software is licensed under [GPLv3](https://www.gnu.org/licenses/gpl-3.0.html), the same licence as the original project. See [LICENSE.txt](LICENSE.txt).

The geomorph images were made by [Robert Pearce](https://travellerrpgblog.blogspot.com/) and [Eric B. Smith](https://gurpsland.no-ip.org/). They are licensed under [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/), which means you must credit them and you cannot make money with the images. The images are not included in this repository, and that licence also applies to anything built from them.

The original project includes this notice for the Traveller trademark, under the [Mongoose Publishing Fair Use policy](https://www.mongoosepublishing.com/pages/traveller-licensing):

> The Traveller game in all forms is owned by Mongoose Publishing. Copyright 1977 - 2025 Mongoose Publishing. Traveller is a registered trademark of Mongoose Publishing. Mongoose Publishing permits web sites and fanzines for this game, provided it contains this notice, that Mongoose Publishing is notified, and subject to a withdrawal of permission on 90 days notice. The contents of this site are for personal, non-commercial use only. Any use of Mongoose Publishing’s copyrighted material or trademarks anywhere on this web site and its files should not be viewed as a challenge to those copyrights or trademarks. In addition, any program/articles/file on this site cannot be republished or distributed without the consent of the author who contributed it.
