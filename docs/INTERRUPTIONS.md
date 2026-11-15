# Desktop interruptions

Chuddy starts with work interruption stopped. During an opted-in session it
acts only on a fresh, still-eligible foreground observation. App metadata and
custom overrides determine eligibility. Known games, Chuddy, ChatGPT, Codex,
Discord and deliberately disabled overrides are protected. An eligible desktop app such as Outlook
does not need a product-specific rule.

`chuddy/interception.py` coordinates the short approach, the exact foreground
closure and the chosen destination. Chrome, Edge and Brave close one verified
selected tab through paired or native controls. The browser may close its
own frame when the selected tab is its last one. A verified desktop
window gets a standard Windows close request so its own save prompt can take
over. The destination opens only after closure is acknowledged. The controller
checks the session, cooldown, cancellation and original foreground identity
before dispatch. Stop invalidates additional effects and leaves an already
dispatched close request to the target app.

| Destination | Effect after the performance |
| --- | --- |
| `game` | Legacy value. Select Clam Casino in the fullscreen native Chuddy app. |
| `casino` | Open Clam Casino in the fullscreen native Chuddy app. |
| `league` | Open the installed League of Legends landing client and request monitor-filling placement. |
| `lcbo` | Open a fullscreen Chrome window at the fixed LCBO homepage. |
| `beer_store` | Open a fullscreen Chrome window at the fixed Beer Store homepage. |

The retailer launcher accepts fixed destination IDs and uses a temporary Chrome
profile for its dedicated window. It confirms the expected fullscreen foreground
window. Opening a retailer homepage requires internet access. The casino is
local and works offline. It is selected in the WebView as soon as closure is
confirmed. The WinForms handoff sizes a borderless Chuddy window to its monitor,
requests foreground activation, then checks focus and monitor coverage after
the native message loop settles. If Windows denies the handoff, the original
window placement is restored and the casino route is still available in Chuddy.

League launches with the same product and patchline arguments as its installed
shortcut. Riot sign-in and updates remain manual. Chuddy can reuse an existing
League landing window and reports success only after verifying actual foreground
and monitor coverage. The League client controls the window size it accepts.
Active matches are left alone. Rehearsals never open Riot or League.

A production request carries the observed foreground window identity and the
fresh observation time. A browser request also confirms the exact original
selected tab. A focus change, stale target or cancelled generation ends the
performance. Cooldown accepts 0–3600 seconds and starts once preparation
allows the performance to begin. One interruption can execute at a time.

`test=True` is a silent local rehearsal in an opted-in session. Its
`preview` result exercises the UI with no work closure or retailer launch.
A casino route has a session-scoped ID so polling can recover it after the
active interruption record retires. Back to Chuddy returns to Companion and
cancels or releases the matching native cover while the session continues.
Each ID routes only once. Chuddy's scripted interruption dialogue follows the
terminal result. A routed casino with a native fullscreen error leaves the
factual error in interruption status and suppresses outcome dialogue.

`chuddy/desktop_overlay.py` implements an optional Windows native overlay with
per-pixel alpha through built-in GDI+, rather than requiring a new renderer or
paid dependency. Its layered tool window has `WS_EX_NOACTIVATE`,
`WS_EX_TRANSPARENT`, a transparent hit-test result, and no mouse injection. It
draws the original raster sheet's neutral cell at 112×168, glides/bobs toward a
theatrical title-bar target and draws a small click ring. It can roam across the
desktop, including negative monitor coordinates. Routes use a real monitor's
work area so the sprite stays out of desktop gaps and the taskbar. The native
UI thread uses a per-monitor DPI context and restores its prior context on close.
The native approach takes 0.35 seconds. The coordinator waits 0.45 seconds
before rechecking focus and dispatching the close. The app fallback reuses
the `ChuddyRasterRig`.

Overlay construction is inert. Call `start()`/`roam()` only for a user-started
session. It attempts to register Ctrl+Alt+Escape as global Stop; `info().hotkey`
reports success and must be shown honestly because another app may own that key.
The callback must call the controller's full Stop, including shared voice and
hardware cancellation. `hide()` hides the performance while retaining the Stop
shortcut; `disarm()` invalidates queued frames and releases it on session Stop.
`close()` also destroys its window, bitmap, DC, GDI+ images and registration.
Native failures report `unavailable` and an error type, allowing the app fallback.
Reduced motion keeps roaming static and moves directly to the performance target.

`web/chud-cursor.js` and `.css` supply a visual-only app-window fallback. Load the
existing rig first and call `cursor.update(state)` on state refresh. It hides for
stopped, paused, disconnected, hidden-page and already-opened distractions, and
for a ready native overlay. It is pointer-transparent and never clicks UI controls
or invokes an API. Root must call `update(null)` on failed connection and destroy
it on app cleanup.

See the [validation guide](VALIDATION.md) for automated and device checks.
