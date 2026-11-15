# Character art generation record

The application character asset was generated with Codex's built-in ImageGen,
using the preserved `inputs/chuddy-reference.png`. No API key or paid image API
is a runtime dependency. The RGBA sprite is stored at `web/assets/chuddy/gestures.png`.

## Selected generation prompt

Use case: identity-preserve / production character expression sheet. Edit the supplied Patrick Star image into a high-quality, show-faithful cartoon character sprite sheet for the Chuddy desktop companion. The supplied image is the identity/design reference. Preserve this exact recognizable fat Patrick: very broad round belly, pink starfish silhouette, tall pointed head, short thick arms, sleepy half-lidded eyes, tiny brows, drooly goofy grin, lime-green shorts with irregular purple flowers, bare pink feet. Make him look like proper polished original-animation Patrick, with confident nuanced ink lines, rich clean cel painting and restrained soft shading matching the reference, not a flat SVG icon, not a geometric mascot redesign, not a 3D toy. Remove the entire room/background. Deliver ONE genuine transparent RGBA PNG with exactly THREE equal columns and TWO equal rows, six separated full-body poses. Overall square canvas means each cell is portrait 2:3. Frame every full character from top of head through feet at identical scale and registration, center of head at same local x and y in every cell. Give 10 percent transparent padding on every side of each cell, all arms fully inside own cell. Top row left to right: neutral dopey sleepy smile with drool and relaxed arms, thinking with one stubby hand touching temple and bemused eyes, confused stumped with one arm raised in a small shrug. Bottom row left to right: happy laughing with eyes joyfully closed and open mouth, smug half-lidded grin with arms resting over big belly, weary vacant stare with drooped arms and a little drool. Preserve fat proportions and shorts pattern consistently across all six. Eyes near local y 0.30, mouth near y 0.42; body and head positions should align across poses. Keep broad belly visible; no clothing beyond shorts. No cell borders, no labels, no words, no backdrop, no shadows outside character, no fake checkerboard. Maintain polished animation artwork, not photoreal human skin. Avoid cropped fingers, overlapped cells, warped faces, thin Patrick, extra limbs and faint translucent character interiors.

## Cheek correction request

The user identified inaccurate cheeks in several poses. Comparison sources were
the [official Nick character page](https://www.nick.com/info-page/8hz5p5/patrick-star)
and [Nickelodeon Universe Patrick page](https://nickelodeonuniverse.com/character/patrick/).
Their development reference images are stored separately under `inputs/`; they
are not used as application sprite sheets. Thinking/confused/weary faces had
round smile-corner protrusions while frowning. Two focused edits requesting
smooth head contours were rejected by ImageGen output moderation.

No pixel edit, alternate image provider or further retry was used. The PNG remains
unchanged. Runtime neutral substitution and the outstanding art revision are
documented by the artwork owner; they are not a claim that corrected frames exist.

## Logo request

Exact user request: “for the chuddy logo make the dd patricks butt with him looking
back with a stupid face drooling”. The attempted raster brief specified a fully
clothed cartoon rear pose with lime floral shorts, the two rounded shapes reading
as lowercase `dd`, `ch` to the left and `y` to the right, a backward-looking drooling
face, dark-plum lettering and transparent background. The image tool rejected the
output as sexual.

That requested logo is unfinished. The existing wordmark remains in use; no
substitution is described as the completed requested logo.
