# Chuddy raster companion artwork

The desktop uses a six-expression transparent PNG sheet, `gestures.png`, with a 3-column × 2-row layout:

| Top left | Top middle | Top right |
| --- | --- | --- |
| Neutral | Thinking | Confused |
| Happy | Smug | Weary |

The appearance source is the user's exact preserved `inputs/chuddy-reference.png`. `gestures.png` was generated through Codex's **built-in ImageGen tool** using that file as its reference, with no paid image API or alternative image service. The selected prompt requested a transparent six-expression, 3×2 Patrick-inspired sprite sheet matching the source's fat belly, sleepy cartoon face, drool, pink body, lime shorts, purple flowers, original cartoon linework and soft cel shading, with aligned full-body neutral/thinking/confused/happy/smug/weary poses. It specifically avoids a 3D or vector substitute.

Selected file: **1254 × 1254 PNG, RGBA**, six **418 × 627** cells. SHA-256: `57981ec32b50b6fb70e79d3b327428ced905fac080ca8fcc59a6879bb670521b`. Alpha inspection found true transparency spanning 0–255; a few extremely low-alpha red background pixels were retained rather than programmatically altering the generated image. The main character edges are substantially more opaque. The renderer applies measured per-pose destination registration and padding without rewriting the source image.

The renderer places the selected pose on a small anchored canvas mesh. The upper face stays anchored. A tiny source-mouth texture warp runs only while actual voice playback is reported. This is a lightweight raster puppet, not a production Live2D model or phoneme-level lip synchronization.

Animation pauses when offscreen or when the document is hidden. Operating-system reduced motion and the Companion studio's local still-animation control both disable movement. Expression preview is deliberately visual-only and starts no recording or playback.

## Registration and rendering checks

The ready neutral/happy/smug poses now use a uniform per-pose scale measured from the midpoint of the purple upper eyelids and the opaque sole line. Their eye landmarks meet at normalized `(0.50, 0.30)` and their soles at `y=0.89` within the fitted canvas texture. This centers the head and grounds the feet without stretching either axis independently. The CSS fallback uses this same placement. Pose changes remain discrete because their arms and facial drawings differ.

Arm and belly movement begins below `y=0.48`; it cannot reach the cheeks. Integer-aligned source patches restore the full non-speaking upper face and feet after mesh rendering, so fractional triangle sampling cannot make their pixels shimmer. During actual speech, only a small registered mouth patch is exempt from the face restore. Triangle overlap is measured perpendicular to the edges; this removes the faint diagonal cracks that vertex-only padding left across the belly. The animation clock stops offscreen or when hidden and resumes from the same phase. Destroyed rigs ignore delayed image callbacks.

`qa/registration.png` is a headless Edge screenshot of the actual renderer, with eye/sole guides. It is review evidence, not replacement artwork or a new generated sprite sheet. Source transparency includes low-alpha red flecks and slightly translucent character pixels; the original PNG remains byte-for-byte unchanged. No blink has been invented: there is no reviewed closed-eye patch for the neutral/smug faces.

See `docs/ARTWORK.md` for the check commands and remaining art requests.

## Cheek revision is unresolved

The user identified inaccurate ear-like cheek bulges in the generated thinking, confused and weary/frowning poses. Two targeted corrections through the same built-in image-generation tool were refused by output moderation; neither produced a corrected asset. No alternate paid tool was used and no pixels were programmatically changed. The selected PNG and its digest above remain unchanged. Official appearance references can support a future authorized revision, but no exact-match claim is made.

`ARTWORK` in `web/character-rig.js` marks **thinking, confused and weary as revision pending**. Those logical activity/expression states remain available, but the runtime displays the neutral raster pose until corrected art is supplied and reviewed. The studio preview labels them “art revision pending” and also displays neutral; it does not expose the uncorrected cheek frames. Neutral, happy and smug retain their selected artwork. Thinking status and actual voice playback indicators continue independently of this art fallback.

Generated prototype derivatives do not establish an official character license or endorsement.
