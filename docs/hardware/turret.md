# Patrick’s turn-only turret

[Wiring and layout diagram](turret-wiring.svg) · [Right-cheek switch plan](cheek-switch.md)

## Wiring

Keep the original **yaw stage** for turning Patrick. Move the former **pitch/tilt servo** to the stationary spray lever. The proposed harness uses the pins below. Confirm the servo labels and lead polarity on your actual harness.

| Connection | Pi 5 header or supply | Destination |
| --- | --- | --- |
| Turn servo signal (MG996R in the original records) | **BCM GPIO12 · physical pin 32** | Turn servo signal input, 50 Hz |
| Former tilt servo signal (MG90/MG90S in the original records) | **BCM GPIO13 · physical pin 33** | Lever servo signal input, 50 Hz |
| Servo power positive | **Separate, regulated servo supply +** | Positive lead of both servos |
| Servo ground | **Servo supply − and Pi GND · physical pin 39** | Servo ground leads at the supply return junction, with one reference ground to the Pi |
| Cheek switch | **GPIO17 · pin 11** and **GND · pin 9** | One wire per normally open contact group, if GPIO17 is free |
| Pi power | **Pi USB-C power port** | USB-C cable from a compatible wall charger or power bank |

The two supplies share **ground only**. Both servo motor feeds and the main servo return go straight to the motor supply through suitably rated wire and terminals. Connect the Pi to that return with one ground reference wire. Leave Pi header **5 V and 3.3 V** off the motor circuit. A servo's signal lead carries logic, and its power lead goes to the verified servo rail. Typical colours are red for power, black or brown for ground, and orange, yellow or white for signal. Verify the connectors before energizing. Route servo returns outside the Pi header and small breadboard jumpers.

Reuse the old servo supply only after checking its label, connector polarity, regulation and capacity for **both** motors under peak load. The archived record identifies the motor models and signal pins. It does not verify the current supply's rating or the old breadboard's current capacity. If the servos are the identified models, use the voltage supported by both actual labels. Treat approximately 4.8–5 V as a bench candidate subject to that check. A 5 V supply with several amps of current capacity can be suitable, but establish a margin over the actual combined current without deliberately stalling either motor. TowerPro gives 1.4 A stall for its genuine MG996R and lists 4.8 V operating voltage for its MG90S. Variant and clone ratings vary. Put an accessible, rated switch on the servo supply so you can remove motor power while keeping the Pi on. A fuse or current-limited supply must suit the verified load and wire/connector ratings. [TowerPro MG996R](https://towerpro.com.tw/product/mg996R/) · [TowerPro MG90S](https://towerpro.com.tw/product/mg90s-3/)

Check with both supplies unplugged that the servo positive rail has no connection to a Pi power pin. Verify polarity and regulated motor voltage at the supply output before plugging in servo or signal leads. Boot the Pi with motor power off, then test one servo at a time. Switch off servo power before shutting down the Pi or moving any wire. Use `vcgencmd get_throttled` before and after testing. `0x1` flags current undervoltage and `0x10000` flags undervoltage since boot. If the Pi resets or flags undervoltage during movement, switch off servo power and recheck the cable and supplies. [Pi header and GPIO voltage](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#40-pin-gpio-header) · [Pi undervoltage flags](https://www.raspberrypi.com/documentation/computers/os.html#get_throttled)

## Using the Anker charger or power bank

**Bench choice with the equipment on hand.** Use the wall charger's **USB-C1** port for the Pi if its label specifies a fixed **5 V at 3 A** output or better. Connect USB-C to USB-C directly, boot from microSD and leave the other charger ports empty. Keep the servos disconnected until their separate supply and wiring are ready.

The likely match is the **Anker 100 W 3-Port Smart Display A121B**, which lists a **95 W + 5 W** shared configuration. Check the printed model. If it is A121B, either USB-C port used alone is rated **5 V ⎓ 3 A (15 W)**. The Pi requests the charger’s fixed 5 V profile. A charger rating of 95 W or 100 W describes capacity at other voltages or across ports. It does not force that power into the Pi. Leave the other charger ports empty during testing because plugging in another device can renegotiate power. Use a good USB-C to USB-C cable into the Pi's USB-C power input. [Anker A121B output specifications](https://support.anker.com/s/product/a08J1000000Ym4IIAS/anker-charger-100w-3-ports-smart-display)

Raspberry Pi specifies a stable **5 V at 3 A** for a lightly equipped Pi 5. At that setting, the Pi caps downstream USB power at **600 mA total**. Use microSD boot and minimal USB peripherals for this test. A fixed **5 V at 5 A USB-PD** profile and a compatible 5 A cable allow the full USB power budget. The Pi does not choose higher-voltage or PPS profiles. Connect through the normal USB-C input and leave current-limit overrides off. This calibration tool checks for active Pi undervoltage before sending motor signals. [Raspberry Pi 5 USB Power Delivery](https://pip-assets.raspberrypi.com/categories/685-app-notes-guides-whitepapers/documents/RP-009856-WP-1-USB%20Power%20delivery%20on%20Raspberry%20Pi%205.pdf)

The charger regulates its output at 5 V, and the Pi's onboard regulators turn that 5 V into the lower voltages its chips need. The Pi needs the source to deliver 5 V through its USB-C input. The charger's live watt display is a draw reading; use the source label for the output profile and check for Pi undervoltage during testing.

The power bank can power the Pi instead if the selected **USB-C output** lists fixed 5 V at 3 A or better and the bank stays on through boot and idle. Use one Pi power source at a time. The exact wall charger model and bank output remain unconfirmed.

### Arduino kit parts and the servo supply

- The **5 V relay** is a switch. The 5 V rating on its coil or module is the voltage it needs to operate. A separate supply still has to provide the servo power. Leave the relay out of the bench wiring.
- A small **5 V/3.3 V breadboard power module** may provide regulated voltage, but check its actual current rating. A common kit module is rated for at most **700 mA**, while TowerPro lists **1.4 A** stall current for the MG996R alone. A 700 mA module is inadequate for this pair and can sag, overheat or shut down when a motor loads up. Use rated terminal connections for the motor power instead of routing it through the solderless breadboard. [Example kit module rating](https://docs.sunfounder.com/projects/uno-mega-kit/en/latest/components/component_power_module.html) · [MG996R manufacturer rating](https://towerpro.com.tw/product/mg996r/) · [Adafruit servo power wiring](https://learn.adafruit.com/adafruit-16-channel-pwm-servo-hat-for-raspberry-pi/powering-servos)
- The power bank is a possible **separate servo source** if it supplies regulated 5 V at enough current for the actual two-servo peak, both servos accept 5 V, and you already have a fixed-5 V USB output adapter and terminals rated for that current. Check the bank output limit and that it stays on and stable under changing load. A 5 V / 3 A port is a candidate, not a measured fit. In this arrangement the wall Anker powers the Pi and the bank feeds the motors directly through the rated adapter, with one common ground. A higher-voltage USB-PD trigger belongs outside this setup.
- If those parts are unavailable, test the Pi and software with the servo power leads disconnected. For motion, use a separate enclosed DC supply rated for the two motors and their voltage range.

## Reuse the turret and lever

1. Keep the turret base, yaw motor, horn, bearing/perimeter support and rotating plate assembled. Remove the upper camera/laser and tilt structure from the rotating plate and disconnect the optical payload. Secure the plush low and centred using the existing upper mount holes if accessible. Retain the plate's perimeter support. Patrick stays upright.
2. Try turning the detached tilt holder around on a **fixed side bracket** next to the base. Keep the motor shaft horizontal, so its arm swings vertically. Test-fit the existing arm above the upright bottle with the tip centred over the pump. Mount the holder and bottle cradle to the same stationary surface. If flipping the holder puts it on the moving plate, clashes, or makes the tip sweep sideways across the pump, use the same servo/horn with a small fixed mounting block instead.
3. A popsicle stick and a dab of hot glue can locate a contact pad for a dry mock-up. Use the servo's horn screw and a mechanically fastened arm/holder for load. Glue joints can creep or peel under repeated pump force. Use a wide rounded pad, a small gap at **up**, and a separate hard stop that catches excess travel after the required pump stroke and before the pump bottoms out or the lever binds. Keep the mechanism easy to unload by hand after cutting servo power. A geared servo can remain where it stopped when PWM is released.
4. Before fitting the real bottle, measure the pump stroke and peak force, and check that the tip moves mostly straight down through the short pumping arc. Compare force × perpendicular arm length against the servo's usable torque with margin. Use a shorter arm to increase push force if it still covers the measured stroke. A longer arm increases travel for the same angle and requires more torque. Check the mount and sticks for flex with a dry dummy. Keep the original bottle upright and restrained, outside Patrick’s full sweep in a separate tray away from the ventilated Pi. Follow the received label for handling and route the nozzle away from people, plush and electronics.
5. Route the cheek switch cable down near the rotation axis with a limited-angle slack loop and a clamp on each moving/fixed side. The lever harness and Pi are stationary. Manually verify the rotation clearance and cable slack in both directions with power off. Fit the Pi in a stationary dry tray beside or below the original turret if its existing windows do not clear the board, cooler and plugs. The previous R10 envelope check did not prove an internal fit.

## Run the calibration tool

The tool is a small local browser UI. It exposes **Front / Back** for Patrick and **Up / Down** for the lever. You calibrate the actual physical positions with fine jogs and then press an individual test or **Full dry run**. The right-cheek button, sound and Chuddy app are outside the calibration tool.

**Preview on Windows or Pi, with simulated outputs** from the project root. Use the Python interpreter you normally use for this checkout.

```sh
python pi/turret_calibration.py
```

Open `http://127.0.0.1:8777/`. Preview settings and hardware settings are stored in separate files under `~/.chuddy/`. The two files are [the Python controller](../../pi/turret_calibration.py) and [the HTML UI](../../pi/turret_calibration.html).

**On the Pi 5**, copy those two files into the same folder, or use the checkout on the Pi. A Raspberry Pi OS setup needs `pinctrl`, `vcgencmd` and the system Python package `python3-lgpio`. Install it if absent with `sudo apt install python3-lgpio`, then run as your normal Pi user with access to the RP1 GPIO chip. Stop the Chuddy Pi service and any other GPIO controller using the same pins. With no actuators or linkage attached on the first pass, check `pinctrl get 12` and `pinctrl get 13`. The tool requires both requested lines to be GPIO inputs before claiming them. If either is in an alternate function, release its existing owner first. The tool configures no boot overlays and starts at no PWM.

```sh
python3 pi/turret_calibration.py --hardware
```

Open the same URL on the Pi. For a headless Pi, open an SSH tunnel from the laptop and use the same URL in the laptop browser. Substitute your actual Pi account and address.

```sh
ssh -N -L 8777:127.0.0.1:8777 YOUR_PI_USER@YOUR_PI_ADDRESS
```

### Teach the real travel

1. For first energizing, leave the arms/horns uncoupled and the bottle removed. Keep the servo power switch within reach. Select **Arm bench test** and send one motor's 1500 µs start pulse. The first movement has no position feedback, so confirm that it is safe before reconnecting the linkage. Check the orientation of each motor and confirm that the servo can be pulsed without jitter or resets. Work on one axis at a time on the first pass.
2. For the turn, place a pointer/mark on the plate and set fixed **front** and **180°** marks on the base. Jog to front and press **Teach front**. Jog toward the rear mark using 25 µs coarse steps and 5 µs near it. Stop short of binding, cable tension, friction spikes or rocking. Press **Teach back** at the measured rear mark or the furthest safe turn you have chosen. If the safe range ends before 180°, use the smaller turn or re-index the horn with power off and retest. Requesting more pulse width cannot create extra shaft travel. The old assembly's recorded pan range was 1380–2055 µs with home 1500 µs. That reference neither establishes a 180° turn nor clears the refitted plush.
3. For the lever, use a dry dummy of the pump height. Jog to a clear position and **Teach up**. Jog until the tip reaches the measured pump stroke with clearance to the stop and **Teach down**. The lower pulse can be either up or down. A reversed holder may reverse the pulse direction. Switch off and correct binding or side-loading mechanically.
4. The initial jog limits are 1400–1600 µs around 1500 µs. If more range is needed, Stop, reopen **Jog limits and start pulse**, and cautiously widen only the measured safe side. The signal driver's 500–2500 µs range is a format limit. Save a safe start pulse inside each measured span for subsequent sessions, typically the taught front and up after installing the linkages. After Stop or restart, send the start pulses or jog into both taught spans to enable the combined test.
5. Test **turn** and **lever** separately. Then **Full dry run** moves to lever up and front, turns to the taught rear, moves the lever down and up once, and returns front. Watch the marks and dummy height and adjust the taught endpoint if either travels too far or not far enough. These readouts show commands in µs. The real travel must be measured in degrees or millimetres, and the next boot starts disarmed.

Stop or Escape removes PWM and invalidates late requests. The page also loses its lease if focus or the connection drops. A finite pulse burst limits what continues after communication loss. A software stop leaves the motor supply energized. Pull the physical servo supply switch if either axis jams or a stop fails. The calibrator sends short, software-timed 50 Hz test bursts, so check jitter on the Pi before relying on fine steps. It is for supervised dry bench use. [lgpio servo pulse API](https://lg.raspberrybasic.org/lgpio.html)
