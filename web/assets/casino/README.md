# Casino artwork

The casino uses local raster assets for its rooms, bots, player avatar and
dealer. The room backgrounds are `golden-reef-room.png` and
`clam-club-room.png`. `table-portraits.png` supplies the static cast portraits.

| Asset | Layout |
| --- | --- |
| `action-patrick.png`, `action-krabs.png`, `action-squidward.png` | 2 × 2, in reading order: hold, place cards, push chips, react |
| `action-plankton-gary.png` | 4 × 2, with Plankton in the top row and the Gary reference in the bottom row |
| `action-gary-dealer-clean.png` | 4 × 2, with Gary's silent dealer poses in the bottom row |
| `action-gary-suit.png` | 4 × 2, reference for the suited dealer |

The CSS crops the appropriate cells for each seat and plays staged actions
after the backend returns an authoritative result. Reduced motion keeps live
game input available and shortens visual playback. The assets' dimensions and
digests are recorded in [action-manifest.json](action-manifest.json).

SpongeBob uses a static portrait. Image output moderation blocked a dedicated
SpongeBob action sheet and the [losing-reaction sheet](loss-reactions-v1.md).
The room design references are under `inputs/casino-references/`. These assets
are fan artwork and carry no official character endorsement.
