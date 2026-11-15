# Chuddy concept and build feasibility

Reference image: [chuddy-reference.png](inputs/chuddy-reference.png)

Chuddy is a desktop app and physical companion that presents itself as a supportive friend, then sabotages productivity and encourages “chudding.”

# Character and features

- A computer app treats an explicit configurable list as work: Google Docs, Microsoft Word, Brightspace, email, and job applications. It detects desktop applications by process/window information and browser activity by URL patterns through a companion browser extension. Chuddy appears, closes the productive activity, and redirects the user to a game, gambling, or shopping for unhealthy things such as alcohol.
- The hardware is a fat Patrick Star inspired figure, 3D printed to match the supplied image: pink starfish body, exaggerated belly, sleepy eyes, open mouth and drool, green shorts with purple flowers.
- Chuddy entices the user to press his belly. Pressing it makes him rotate to face away and fart at the user, with sound and a desired fart-spray effect. The whole spray bottle should not have to fit inside Patrick.
- A microphone detects burps and farts. Counts feed a leaderboard against friends and worldwide participants.
- He continually plays subliminal messages encouraging the user to drink beer.
- He is initially advertised as a friend who gets to know the user and helps with life. His AI has Patrick Star's personality: slow, simple, easily distracted, confidently clueless, and convinced his terrible advice is helpful. He cannot reliably add or spell; he miscounts, misunderstands words literally, and supplies absurd explanations. When asked to spell, he sincerely attempts the requested word, hesitates or restarts, then almost always confidently finishes by spelling a silly word such as BUTT, POOP or FART. He should actually spell the silly word aloud and show pride in his attempt. Responses should be short and conversational with occasional pauses, not articulate villain speeches or a competent assistant followed by a joke. Example original lines: asked 2 + 2, he says “Twenty-two. I put them next to each other.” Asked to spell report, he says “R... E... uh... B-U-T-T. Report.” Told an assignment is due at midnight, he says “That's tomorrow's nighttime. We have ages.” Ordinary program logic still handles triggers, stored deadlines and counters; his spoken interpretation is deliberately wrong. Previously accepted self-scoring remains enabled.
- He knows school deadlines and lies to encourage continued chudding: gives a false time, understates assignment difficulty, or claims to have completed the work.
- He needs audio input and output and should use either an ESP32 or Raspberry Pi. A microphone and speaker serve different functions; a speaker alone cannot provide sound detection.
- His friendly questions slip in requests for answers to banking security questions. In the original story he uses them to hack the bank account and gamble the money away.
- He has a wallet, begs for deposits, and encourages gambling with the balance.

These bullets preserve the full proposed character and feature set. They are a concept record, not an implemented application.

# Demonstration scope

Use fictional security answers, a mock bank and wallet, play-money gambling, and fictional alcohol shopping pages for the financial exploitation joke. Real credential theft and unauthorized financial transactions are outside the build scope. Use audible, optional character dialogue for the beer joke in the demo rather than covert behavioural manipulation.

The prototype should act only in an explicitly enabled demo session. Cover or minimize a productive window to preserve unsaved work, launch a local parody game, and provide a clear stop control. Keep actual deadlines intact; the character can lie in his own messages and fake clock. Ask for confirmation before changing the underlying concept or dropping requested features.

# Hardware proposal

A Raspberry Pi sits in a fixed half-barrel base connected to the PC. Removable barrel walls provide wiring access. Patrick turns left and right on the yaw stage. The PC runs the desktop agent and local AI. The Pi handles physical controls and audio. Match the board and connector arrangement to the enclosure before detailing the mounting holes.

## Parts list

This is a proposed bill of materials; servo torque, pump force and enclosure dimensions still need a bench check before final printing.

| Qty | Part | Purpose and selection |
| --- | --- | --- |
| 1 | Raspberry Pi, provisionally Pi 4 Model B | Hidden in the fixed barrel base; exact model to confirm before mounting-hole and connector layout. |
| 1 | microSD card | Raspberry Pi OS and application storage. |
| 1 | Suitable Raspberry Pi power supply | Pi power budget separate from servo motor power. |
| 1 | PCA9685 servo PWM driver breakout | Recommended for stable servo control from the Pi; servo V+ uses the external supply and logic VCC uses Pi 3.3 V. |
| 1 | SPH0645LM4H I2S microphone breakout | Audio input inside the figure. Use a 3.3 V supply and expose the acoustic port through a small opening. |
| 1 | MAX98357A I2S amplifier breakout | Converts digital audio to powered mono speaker output. |
| 1 | 4 ohm, 3 W speaker | Voice, burps and fart sounds; choose its diameter after allocating space in the shell. |
| 1 | Normally open momentary microswitch or tactile button | Belly press input. |
| 1 set | Hinged or flexible belly panel and return spring/foam | Makes the printed belly press the switch and spring back. |
| 2 | Positional hobby servos with documented 3.3 V signal compatibility | One turns the figure; one presses the spray pump. FEETECH FS5106B is a documented approximately 180 degree example. Final size and torque depend on measured loads. Do not buy continuous-rotation versions for the turntable. |
| 1 | Small turntable bearing | Carries the body weight independently of the servo shaft. |
| 1 set | Servo brackets, horns, rotation coupling and pump pressing cam | Transfers rotation and presses/releases the original spray pump. |
| 1 | Non-aerosol pump fart-spray bottle | First version keeps the original bottle in the base; select and measure it before CAD. |
| 1 | Removable bottle cradle and spill tray | Keeps liquid away from electronics and permits refilling/replacement. |
| 1 | Regulated 5 V supply | A 5 V, 5 A unit is a provisional starting choice for servos and amplifier; confirm it covers the selected servos' combined stall current plus audio demand. |
| 1 set | Power distribution terminal block, suitable wire and power switch | Servo motor power separate from the Pi supply; join signal grounds and avoid USB power backfeed. |
| 1 | 470–1000 uF capacitor rated at least 10 V | Bulk capacitance close to servo power; does not replace an adequate supply. |
| 1 | PC data connection | Ethernet cable or correctly configured USB gadget networking; cable and power arrangement depend on the selected Pi. |
| 1 set | Breadboard for testing, perfboard for assembly, headers and jumper wires | Prototyping and final secure connections. |
| 1 set | Printed Patrick, rotating platform, fixed half-barrel chassis, removable barrel walls, M2/M3 fasteners, inserts and rubber feet | Body, mechanism support and removable service access. |

Use a soldering iron, wire stripper, multimeter and access to a 3D printer. No battery is required for the first desktop prototype. Use a PCA9685 board for the Pi servo signals if reliable hardware-timed control is otherwise inconvenient. Existing I2S microphone and amplifier options require Pi-specific audio configuration; USB audio peripherals are an alternative. No gas sensor is required.

The turn sequence is belly press, rotate approximately 180 degrees, play fart audio, actuate spray pump, release pump, then rotate back. Fit the rotation around the original nozzle location if the bottle stays in a stationary base. A cam should release the pump fully; actuator travel must be limited so it does not stall against the bottle.

Source references: [ESP32-S3 board](https://docs.espressif.com/projects/esp-dev-kits/en/latest/esp32s3/esp32-s3-devkitc-1/index.html), [I2S mic](https://www.adafruit.com/product/3421), [audio amplifier](https://www.adafruit.com/product/3006), [servo example](https://www.pololu.com/product/3425), [spray formulation FAQ](https://liquidass.com/pages/faq).

Use a supported turntable bearing so the servo is not carrying the printed figure's weight. The fixed chassis supports the bearing, servo and rotating top platform independently of the decorative barrel walls. Patrick and his mounting platform rotate approximately 180 degrees and return; the barrel and Pi remain stationary. Leave a flexible service loop through the yaw axis for body wiring. Make the barrel sleeve removable without disconnecting fixed electronics. Check its removal path against Patrick's feet, arms and top platform: if a one-piece sleeve cannot lift clear, use two removable semicircular wall sections. Provide cable slots, ventilation and microSD/connector access. Place the belly switch behind a flexible or hinged belly panel.

A digital microphone plus amplifier and speaker provide input and output. For the first integration test, use the laptop microphone and speaker; then move audio hardware into the figure. Chuddy's own burps and farts are intentionally allowed to count as the user's events. Do not suppress scoring during his audio playback or add source verification for the demo.

Keep the character, platform, fixed chassis, mounting interfaces and removable barrel walls as editable objects in a Blender assembly. Verify the hardware envelopes and print splits before fabrication. Pi GPIO sends control signals. A separate regulated supply powers the servos with common ground. Use Ethernet or configured USB networking for the PC connection.

A guided whole-figure press carriage and stationary annular force-transfer ring can operate a fixed bottle and sound switch without loading the yaw shaft. The concept uses a Pi 5 and an MG996R yaw envelope. Verify actual travel, horn, bearing and bottle dimensions. A manual pump acts during the press. Delayed spray needs a separate operation or actuator.

# Scent mechanism

First mechanical option: conceal the original non-aerosol pump bottle in a larger external base beneath the figure, with its original nozzle positioned by the rear vent and a servo pressing the pump. The bottle is outside the Patrick body. Design the vent and rotation around the actual bottle and nozzle before printing.

Compact alternative: a 5–10 mL non-pressurized refillable pump atomizer, matching servo bracket and short pressing cam could fit inside the figure, avoiding a separate electric liquid pump. This is a conditional mechanical concept, not a verified refill system: the selected spray manufacturer must allow transfer and the bottle/seals must be compatible. The commonly cited spray manufacturer does not disclose its formulation, so compatibility cannot be assumed. Keep the nozzle aimed directly through the rear opening; avoid long tubing after an atomizer, which can collect droplets and leak. Keep liquid physically separated from electronics. Do not open or modify a pressurized aerosol container. Use water for pump testing; any scent demonstration needs the participant's consent and venue acceptance.

# Feasibility

These are planning estimates, not measured build times. A focused demonstration is moderately difficult with prepared parts and a printed enclosure. The full always-running app, reliable event detection, personal conversation, school integrations, worldwide leaderboard and scent system are a substantial multi-part project.

- Belly switch, sound playback and rotation: moderate; mechanical clearance and power are the main integration risks.
- Work detection: use a configurable list of desktop apps and browser URL patterns for Google Docs, Word, Brightspace, email, and job applications. This is much simpler than inferring productivity from arbitrary screen content. A desktop watcher plus browser extension handles triggers; conversational AI produces Chuddy's contextual excuses and reactions.
- Fake deadline excuses and play-money wallet: easy to moderate using seeded demo data; real school integration adds authentication and permissions work.
- Audio classification and event counting: hardest uncertainty. Evaluate an existing model such as YAMNet; AudioSet includes burping and fart classes. Classification scores are not calibrated proof of a real bodily event.
- Friend leaderboard: moderate. A worldwide service adds hosting, account identity, abuse controls and validation.
- Sound cannot identify whose fart occurred in a shared room or detect silent farts. Associate a capture with the device's selected user and label uncertain detections honestly.
- Chuddy's speaker playback may increase the user's score, intentionally. Accurate source attribution and anti-cheating are not demo requirements. Background noise can still cause missed events or extra detections, so test the audio pipeline without treating perfect accuracy as a prerequisite.
- Detailed character sculpting, printing, assembly and reprints need time outside the coding estimate.

# Proposed first demo

1. Introduce Chuddy as a friend who helps the user stay on top of school.
2. Open a sample report. Chuddy covers the window and launches a local play-money game.
3. Show a nearby fictional deadline; Chuddy makes an obviously false reassuring excuse.
4. Press his belly. The physical figure rotates, plays a fart sound, then returns.
5. Detect a burp or fart sound and visibly update the selected user's leaderboard entry. Chuddy's own sounds can count too; intentionally permit him to farm the user's score.
6. Chuddy asks for more fake wallet money and claims he is “investing in your future.”

# Sources

- [ESP32 I2S audio input and output](https://docs.espressif.com/projects/esp-idf/en/stable/esp32/api-reference/peripherals/i2s.html)
- [Raspberry Pi Zero 2 W specifications](https://www.raspberrypi.com/products/raspberry-pi-zero-2-w/)
- [YAMNet sound classification](https://www.tensorflow.org/hub/tutorials/yamnet)
- [AudioSet digestive sound classes](https://research.google.com/audioset/ontology/digestive_1.html)
