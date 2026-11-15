# Clam Casino

A local SpongeBob SquarePants fan room for Chuddy, with four playable games and a shared persistent wallet of fictional monies. Running chudding earns 1 monies/second. Accepted farts add +1× for 60 seconds, up to 5×. The sidebar/table show the server's current rate and multiplier. Pause, Stop, profile changes and restart reset the boost; no recording is required. Passive monies have no daily cap; the legacy 500/day UTC cap covers score/game bonuses only. No real-money betting, purchase or cashout service, paid API, cloud model or hosting is involved.

## Games

- **Clam-o-matic:** existing authoritative `Store.spin`, four equally likely reel symbols; three matching pays 5× stake, any pair 2×, all different zero. UI maps the backend `butt` symbol to Patrick's shorts; arithmetic remains the backend's. The displayed payouts include the stake.
- **Blackjack:** six-deck fresh shoe, dealer stands on every 17, natural 3:2, hit/stand/double and one split. Split aces receive one card and cannot double; other two-card hands can double. No insurance/surrender. Even base stakes 2–10,000. Reserve four base stakes for the round; return unused reserve and winnings at final settlement. The UI states this reservation before the deal.
- **Bikini Bottom Hold'em:** one five-seat no-limit Texas Hold'em hand. Gary deals; you are Patrick. Opponents are SpongeBob (enthusiastic caller), Squidward (selective), Plankton (aggressive bluffer), and Mr. Krabs (greedy value bettor). Their actual betting policies differ. Bots use only their own hole cards plus the public board; chatter is local templates, not a claimed model conversation. Blinds, raises, short-all-in reopening, side pots, tie/odd-chip division and seven-card evaluation are implemented. One human buy-in, minimum 20, is reserved; the final human stack returns to the wallet. Bot chips are fictional table chips and do not spend another profile's wallet. Saved older Gary-opponent hands normalize that seat to Mr. Krabs without changing its cards or chips.
- **Reef Roulette:** European 37-pocket single-zero wheel, canonical pocket order, one green zero and standard red/black mapping. Straight, red/black, odd/even, low/high, dozen and column wagers. Stake-inclusive return is 36× straight, 3× dozen/column or 2× outside; zero loses outside wagers. The labelled wheel, pointer and ball land on the stored server result. No la partage/en prison.

Rules references: [Bicycle blackjack](https://bicyclecards.com/how-to-play/blackjack/), [PokerStars Hold'em](https://www.pokerstars.uk/poker/games/texas-holdem/), [Poker TDA](https://www.pokertda.com/view-poker-tda-rules/), [BCLC roulette rules](https://www.playnow.com/resources/pdf/casino/how-to-play/table-games/roulette/how-to-play-roulette.pdf). These sources informed the rules implementation; the local options and reservation model above govern this prototype.

## Wallet and saved rounds

`CasinoService` creates a separate `casino_rounds` table on the existing Store SQLite connection. It uses the existing transaction/request ledger and wallet ledger, with an atomic expected-profile check. Reservation, game creation, stored result and settlement commit together. Every mutation needs a request ID. Same-ID retries return the original public receipt; conflicting payloads and stale new actions reject without changes. One unfinished table round per profile survives application restart. A final payout is credited once. A profile switch cannot move a request into another wallet.

Full shuffled shoes, decks and unrevealed bot cards stay in SQLite. Public projections hide them; browser actions never supply private game state. A browser reload resumes the saved game. If a reply is lost, the component retains the same request ID and offers a deliberate retry. It never retries a bet automatically or replaces an uncertain request with a fresh one. A replay may contain an older balance, so wallet events request a canonical state refresh.

The frontend's stake limits/disabled controls aid interaction; backend rules and transactions are authoritative. Card/character/wheel animation never chooses results or changes balances. Stop halts cosmetics and invalidates delayed visual work. A transaction already submitted is recovered by its saved ID; Stop cannot undo a committed hand or mint/refund monies.

## Component

Load `web/casino-room.css`, `web/monies.js` and `web/casino-room.js`, then call `ChuddyCasinoRoom.mount(host)`. Update with `{profile,balance,earnings,casino,connected,reducedMotion}`. `casino` may be missing/null for compatibility. The component accepts `options.gameRequest(payload)` and `options.request(spinPayload)` for tests; otherwise it calls the authenticated local `/api/action`. An adapter must throw on errors; set `error.definitive=true` only for a parsed definite local rejection, not an ambiguous transport failure. Exposed methods are `update`, `stopAnimation`, `destroy`.

Bubbling events are `chuddy:casino-navigate` (`detail.destination`), `chuddy:casino-wallet` (`{profileId,result,refreshRequired:true}`) and `chuddy:casino-busy` (`{busy,profileId}`). `chuddy:casino-result` aliases the wallet event. Back to Chuddy emits the `companion` destination, and the score shortcut emits `leaderboard`. The shell owns navigation and the native fullscreen handoff.

## Artwork and motion

Player speech shows the speaker, public action and amount with a short in-character line. The two pairs of Hold’em opponents are staggered around the table. At desktop widths the bubble points toward the acting seat. Compact layouts reserve a wider speech area between the board and lower seats for wrapped lines. Bubbles and moving card props clear the characters, placards and board. Gary stays centered and silent with a separate shoe. Human action buttons and raise bounds appear after the preceding actions finish.

The casino uses the cast art in `web/assets/casino/`. A separate losing-reaction sheet was blocked by image output moderation.

Card and chip playback advances automatically. Poker deals clockwise from the seat left of the button. Cards land face down and flip in place; each street reveals its community cards before its betting. Actions appear with public post-action chip counts. Folds and checks use restrained reactions, contested awards use the celebration pose, and uncalled chips return separately. The visible pot falls as chips are paid and each seat's final stack appears after its last award. Blackjack keeps each split hand distinct from Patrick and the dealer, and reveals Gary's hole card when the play reaches it. Slots stop the reels in sequence; roulette rolls for six seconds and then shows the stored pocket, ball and payout together.

Gary wears the approved clean dealer tuxedo atlas and remains silent. All four Gary poses are empty-footed, with no carried cards or chip piles; cards animate from the separate shoe, while the actual blackjack dealer hand stays on the table. The original suited atlas is preserved but no longer used for Gary. No Meow dialogue or audio is used, including projections of older saved blackjack/roulette rounds. Action captions describe his gestures. Plankton retains the original approved atlas. Game arithmetic, saved rounds and wallet operations remain authoritative on the backend; pacing controls never submit another bet. New poker log entries contain only public chip snapshots, with no hidden cards or deck data. Older log entries without those snapshots still play their actions and show the canonical chip state at completion.

The two supplied references are preserved in `inputs/casino-references/`. A newly generated Golden Reef room uses their neon blue cabinets, shell lights, gilded trim and cobalt patterned carpet. Selecting a game changes its spatial layout: slots has a tall illuminated cabinet floor, blackjack a semicircular blue table and shoe, poker an oval green table with players around its edges, and roulette a wheel/payout pedestal next to its betting layout (stacked on narrow screens). Unstarted card tables show explicit previews; selecting a game never places a bet.

The casino fills the content viewport, including the space from the companion sidebar and session header. The scene, game picker, wallet and action dock resize with the viewport. An active hand hides the new-round form. Rules / log opens a separate dialog. Back to Chuddy restores the normal companion layout and releases the matching fullscreen window through the shell.

Gary is now the dealer in all table rules and UI. The user is the only Patrick. Transparent action atlases provide holding, placing cards, pushing chips and reacting poses for the players. Gary uses a separate clean, suited, empty-footed dealer atlas. Cards visibly travel from Gary's shoe to hands, folded cards move to the muck, bets travel to the pot and returned chips travel to winners. Visual sequencing consumes only saved public results and blocks duplicate input; it never selects cards, changes a payout or issues a second bet. Earlier public table snapshots remain visible until the corresponding action sequence completes, then the canonical result appears.

SpongeBob uses a small canvas puppet that moves the arm and card props while the upper face stays fixed. Image output moderation blocked his separate action sheet. The [artwork guide](../web/assets/casino/README.md) describes the available cast assets.

System/local reduced motion skips motion and presents final state directly. Stop cancels active flights/pose timers and prevents late effects after an already-submitted request returns. Visibility and offscreen handling pause motion; destruction removes effects/listeners. No speakers or microphone start for character actions.

## Verification

The dedicated Python suite contains 65 passing checks: 15 poker, 25 blackjack/roulette and 25 transactional service tests. This includes 300 seeded complete poker hands, 1,000 blackjack rounds, all roulette pockets/bets, hidden-card projection, idempotency, restart recovery, profile races, concurrent moves, affordability and rollback. Headless Edge checks use a disposable SQLite wallet and no real app session; they cover game flow, dropped receipts/reload/retry, duplicate submit, Gary as dealer, the four opponents and sole Patrick seat, actual action poses for all six cast members, travelling card/chip animations, Stop during a delayed reply, earnings/navigation, four different layouts, narrow widths, escaping, reduced motion and lifecycle cleanup. Screenshots are local ignored QA artifacts in `.chuddy/casino-qa/`.

Run from the repository with the configured Python and Node runtimes:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_casino tests.test_casino_tables tests.test_casino_poker -q
node --test tests/casino-room.cjs tests/casino-playback.cjs tests/earnings-ui.cjs
```

No live app restart, real UI takeover, microphone, speaker playback, interception or physical output was used for this feature's checks. Native-window/full-package integration remains the root coordinator's responsibility. SpongeBob's limited source-arm puppet remains the art limitation; the refused action sheet is not presented as completed.
