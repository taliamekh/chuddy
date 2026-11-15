# Casino losing-reaction request

**Status: image generation refused; no reaction artwork produced.** This is a request/provenance record, not a usable sprite manifest. No `loss-reactions-v1.png` exists.

The proposed 3 × 2 sheet contains sad expressions for Patrick, SpongeBob, Squidward, Plankton and Mr. Krabs. Gary is the dealer and does not participate.

## Attempt and result

The request used the five cast references listed in the manifest and specified a transparent background.

The tool returned HTTP 400, `image_generation_user_error`, `moderation_blocked`, moderation stage `output`, category `other`. Request ID: `f5405617-8acf-4b02-a042-8cc737f957e6`. The category does not explain a more specific cause. It returned no image bytes or output path, so no output dimensions, digest, transparency validation or visual approval can be supplied.

No retry or provider switch followed. No CLI/paid API, programmatic facial redraw, replacement vector, copied happy frame or unapproved substitute is presented as a completed sad face. The existing artwork and all earlier refusal records remain intact.

## Reference integrity

The five files were decoded with Pillow after the attempt and checked against the recorded approved digests. All match. Full dimensions, byte counts and SHA-256 values are in `loss-reactions-v1.manifest.json`; that file has `output_file: null` and is explicitly a refusal record.

## Exact submitted prompt

```text
Use case: illustration-story.
Asset type: NEW transparent raster losing-reaction sprite sheet for an existing local cartoon card-table game.
Primary request: five recognizable sad/disappointed reactions to losing a round, matching the approved cast artwork in the supplied references. This is a separate reaction asset, not a replacement action atlas.
Input images: Image 1 action-patrick.png is Patrick's appearance/style reference. Image 2 action-squidward.png is Squidward's appearance/style reference. Image 3 action-krabs.png is Mr. Krabs' appearance/style reference. Image 4 action-plankton-gary.png supplies TOP ROW PLANKTON ONLY as his appearance/style reference; exclude Gary entirely. Image 5 table-portraits.png supplies TOP RIGHT SPONGEBOB as his appearance/style reference. Other cells are supporting cast/style references only. Preserve the existing approved recognizable faces, species, clothing, palette, black outlines, softly shaded 2D cartoon rendering and upper-body/bust framing.
Composition: exactly THREE equal columns by TWO equal rows, six equal square cells on a landscape canvas, generous genuinely transparent margins around each character, no panel lines. TOP LEFT Patrick Star (pink starfish with purple eyelids and green/purple floral shorts), TOP MIDDLE SpongeBob (yellow square sponge, white shirt, red tie, brown trousers), TOP RIGHT Squidward (turquoise head, long nose, brown collared shirt). BOTTOM LEFT Plankton (small green one-eyed character with both long antennae, complete body), BOTTOM MIDDLE Mr. Krabs (red crab, complete eyestalks, light blue collared shirt and belt), BOTTOM RIGHT completely EMPTY TRANSPARENT CELL.
Reaction direction: Patrick has a clearly downturned disappointed mouth, worried raised inner brows, downcast eyes and slumped shoulders, arms lowered. SpongeBob has large sad blue eyes, raised inner brows, downturned mouth with recognizable two front teeth, drooping shoulders, hands lowered. Squidward looks crestfallen and resigned, tired lowered eyelids and a clear frown, shoulders sagged, arms lowered. Plankton has one dejected downcast yellow/red eye, drooping antennae and a small disappointed frown, arms hanging. Mr. Krabs looks comically heartbroken about losing, downcast eyestalks, clear downturned mouth and drooped claws near his torso. Keep sadness readable at small game-seat sizes and distinct from the existing smiling action poses. Gentle cartoon disappointment only.
Constraints: each character entirely contained in its own cell with transparent padding and a consistent centered seat anchor. Crop busts naturally at the waist as in the references; retain complete heads, hands/claws and antennae. Patrick remains the same starfish avatar used for the HUMAN PLAYER, not a human redesign. No Gary/dealer, no extra character, no table, playing cards, counters, chips, money, environment, speech bubble, text, caption, numbers, labels, border, logos, watermark, 3D or SVG substitute. Genuine transparent RGBA background, not white or a drawn checkerboard. Preserve all original source files; output is a new sibling reaction sheet. Runtime will show these expressions only after an actual losing result; this asset does not determine outcomes.
```

## Runtime handoff

The runtime uses the existing cast artwork.

If usable reaction art is supplied later, sad expressions belong only to revealed, authoritative losing outcomes for the affected player. Do not show them before saved result playback reaches that result, in previews, on wins/pushes/ties, or simply because a wager was reserved. A seat folding is not automatically proof of a net losing settlement; split blackjack hands and poker side pots need the casino owner's outcome policy. Clear a completed reaction on the next round/profile/game transition, and preserve Stop/reduced-motion behavior. These are integration requirements, not claims of implemented wiring.
