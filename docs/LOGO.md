# Chuddy logo brief and integration contract

The raster character logo is pending. The layout proof records the intended spelling and letter placement.

## Exact request and spelling

User wording preserved in `docs/ART_GENERATION.md`: “for the chuddy logo make the dd patricks butt with him looking back with a stupid face drooling”. The assigned clarification is **CHUDDY = chu + dd + y**. Six letters, in order: C, H, U, D, D, Y. Preserve the `u`; writing `ch` + `dd` + `y` would omit it. Lowercase lettering can expose the intended `dd` shapes while still spelling the name correctly.

The final image needs the two d letters formed by Patrick's backside, Patrick looking backward with a stupid face and visible drool, and a polished raster cartoon character. A type-only wordmark, a front-facing character beside text, or an SVG mascot is not completion of this request.

## Existing blocker

The built-in ImageGen attempt specified a fully clothed cartoon rear pose in lime-green floral shorts and a nonsexual presentation. It was rejected as sexual. No usable output from that attempt is available. This is the recorded tool outcome, not a claim that the user's intended joke was sexual.

This logo task made **zero new image-generation calls**. It did not rephrase and resubmit the refused illustration, switch providers, reconstruct the refused pose through pixel editing, or create a paid dependency. The original attempted brief also used `ch` to the left; the corrected spelling above is a documentation clarification, not a new submitted prompt.

## What the existing art supports

`web/assets/chuddy/gestures.png` was opened and visually inspected. It contains six front-facing expression cells; neutral, happy and smug are the available runtime poses. Thinking/confused/weary have their separate pending cheek revisions. There is no approved rear/looking-back pose in this sheet. Stretching, mirroring or assembling these cells would not provide the missing drawing faithfully.

The sheet's SHA-256 is `57981ec32b50b6fb70e79d3b327428ced905fac080ca8fcc59a6879bb670521b`. It remains unchanged. The user's preserved `inputs/chuddy-reference.png` and concept are also unchanged. Existing generated artwork is prototype appearance material; this task makes no new licensing or official-endorsement claim.

## Reviewable proof

- `web/assets/logo/layout-proof.html`: static spelling/layout proof. It shows `chu` + ordinary typographic `dd` + `y`, explicitly labels the illustration pending, and displays the existing neutral raster cell separately as an appearance reference. The image is a read-only viewport onto the original sheet; there is no new or edited character image.
- `web/assets/logo/manifest.json`: machine-readable pending state, exact letter order, source digest and planned export geometry. `production_ready` is false and `production.path` is null.

Open the proof directly as a local HTML file, or use `/web/assets/logo/layout-proof.html` when the normal app is already serving. This task does not require starting or restarting the app. The proof uses local system type, local CSS and one relative image reference; there are no scripts, network dependencies, audio or recording controls. It is not linked from the main frontend.

## Final art brief

This is a design record, **not a prompt to resubmit the refused request**.

| Element | Required result |
| --- | --- |
| Word | `chuddy`, read unambiguously as C H U D D Y; no extra d and no missing u |
| Left lettering | `chu`, fully legible and separate from the illustrated pair |
| Middle | Exactly the requested two d letters formed by Patrick's backside |
| Character | Looking backward, stupid expression and visible drool; established broad pink starfish proportions and lime shorts with purple flowers |
| Right lettering | `y`, fully legible; drool must not obscure it |
| Finish | Proper raster cartoon linework and cel colour consistent with the selected appearance source; no geometric/vector character substitute |
| Background | Genuine transparent alpha; no baked checkerboard, room, border or external shadow |
| Palette | Existing UI ink `#383039`, purple `#8062a2`, coral `#ed837e`, and character pink/lime/purple; preserve character-source colours rather than recolouring him to UI swatches |

Proposed export: a 2048 × 1024 RGBA PNG with at least 64 px outer transparent padding. Baseline at y = 860. Initial horizontal allocation: `chu` x = 64–800, illustrated `dd` x = 800–1600, `y` x = 1600–1984. These are layout targets for a future supplied and reviewed image, not measured coordinates in completed artwork. The looking-back head can rise above the letter bodies within the middle allocation. Preserve uniform scale when exporting other sizes.

## Frontend integration

1. **Now:** retain the existing `web/index.html` brand. No production logo exists at the planned URL. Do not wire the proof or manifest into the main UI as a substitute.
2. **After an acceptable final raster asset is supplied and reviewed:** save it as `web/assets/logo/chuddy-logo.png`, record its actual dimensions, alpha bounds, provenance and digest, and update the manifest only when the checks below pass. The proposed contract is 2048 × 1024; revise the contract explicitly if the delivered composition needs different dimensions.
3. The verified production URL will be `/web/assets/logo/chuddy-logo.png`, served by the existing static `/web/` route. No API endpoint, token, new package, external CDN or model is needed.
4. The coordinator may replace only the contents of the brand anchor while preserving `href="#companion"` and accessible name `Chuddy home`. Use an image with explicit intrinsic dimensions and uniform scaling (`object-fit: contain`). If the link already supplies that name, the image can have empty alt text to avoid duplicate announcement. Retain a visible text fallback on image failure.
5. At the existing compact-sidebar breakpoint (820 px), retain the current compact brand mark unless a separately reviewed square raster icon is available. Do not squeeze this 2:1 wordmark into the icon slot or crop away letters to fabricate an icon.

The current full sidebar is 175–230 px wide depending on breakpoint, before internal padding. A detailed 2:1 character wordmark may be too small there; first check it at actual available widths around 130–170 CSS px. Use the current text brand at sizes where the face, drool or letter sequence stops reading. No animation, character-rig change, sound or hardware action belongs to this static logo integration.

## Acceptance evidence still required

- Open the actual PNG; verify the six-letter reading order visually, especially the u and the two d shapes.
- Verify the requested backside/looking-back/drooling drawing is actually present; a type proof cannot pass this gate.
- Check genuine alpha on light, dark and checkerboard review backgrounds, clean edges, full padding and no clipped face/feet/drool. Existing sheet red flecks must not be carried into a final logo unnoticed.
- Review at master size and the app's real sidebar sizes. Verify accessible link naming and missing-image fallback, plus compact-sidebar behavior.
- Record the actual source and checks before setting `production_ready` true. Do not describe pending work, a failed generation or the current proof as the finished logo.
