# Chuddy browser sidekick

This unpacked Manifest V3 extension supports Chrome and Microsoft Edge 120 or newer. It reports selected work URLs to the local Chuddy desktop and closes the matched work tab when an enabled desktop session requests a distraction. It does not inject scripts into pages. Reload the extension after updating its files.

1. Run Chuddy and open **Work rules → Show pairing setup**.
2. Open `chrome://extensions` or `edge://extensions`, enable Developer mode, and choose **Load unpacked**. Select this `extension` directory.
3. Open the extension's toolbar popup. Pair using the desktop address and browser-only token.
4. Select the URL rules you want Chuddy to act on, save them, then enable the work-tab toggle. Start a session in the desktop app. Chuddy can then close those selected work tabs and open the casino.
5. Start an opt-in session in the desktop when you want interruptions.

Reporting is disabled on first installation. The token is kept in local extension storage, not browser sync. Only loopback desktop addresses are accepted. The browser token cannot invoke general desktop actions.

The `tabs` permission reads the active tab URL. The worker checks that the tab is active, its browser window is focused, and its stripped URL matches both a selected URL rule and the desktop's enabled rules. Query strings and fragments are removed before reporting. Page text, history and document content are never read. Focus loss or an unmatched tab produces a clear report containing no URL. Reports carry timestamps and IDs; the desktop rejects stale reports and independently checks session/cooldown.

Reports are sent on tab activation, navigation and window focus changes. A one-second heartbeat runs while the worker is awake, and a Chrome alarm wakes it every 30 seconds after suspension. A suspended worker can therefore delay reporting; expired commands fail without acting. Rules refresh periodically and can be refreshed manually in the popup.

The desktop issues a short-lived command only for the reported tab and extension instance. The extension checks the selected URL rule, focused browser window, active tab, exact tab ID and sanitized URL, then claims permission and checks again immediately before `tabs.remove`. Closing the last tab first creates an inactive blank tab so the entire browser window remains open. Chuddy does not minimize or close a browser window. The casino opens only after the extension acknowledges that the work tab closed. Unpaired/old extensions produce a capability error. There is no countdown or normal return-to-work option; app exit and the emergency desktop Ctrl+Alt+Escape shortcut cancel pending work.

To stop reporting, turn off the popup toggle. **Disconnect and forget pairing** also clears the local token and selected rules. Uninstall through your browser normally if desired.

The extension is supplied for user-operated installation. The development process does not install or enable it automatically.
