# Patrick’s right-cheek button

[Wiring diagram](cheek-switch.svg)

## Two-wire connection

| Lead | Raspberry Pi 5 40-pin header | Momentary switch |
| --- | --- | --- |
| Signal (blue in diagram) | **Physical pin 11 · BCM GPIO17** | One leg from the meter-identified contact group A |
| Ground (charcoal in diagram) | **Physical pin 9 · GND** | One leg from the meter-identified contact group B |

Set GPIO17 as an **input with an internal pull-up**. The released switch is open and reads HIGH (about 3.3 V). Pressing closes the switch to ground and reads LOW (about 0 V). The switch is a dry contact. Leave both the 3V3 and 5V header pins unwired. Those rails sit close to the candidate pins.

With the Pi 5’s components facing up, the long header across the top, and USB/Ethernet at the right, pin 1 is at the **left end of the inside row**. Pins 9 and 11 are the fifth and sixth positions in that row, counting pin 1 as the first position. [Official 40-pin header map](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#pin-layout) · [Official Pi 5 board drawing](https://datasheets.raspberrypi.com/rpi5/raspberry-pi-5-mechanical-drawing.pdf)

**GPIO17 is a candidate.** The turn servo and lever servo use GPIO12 (physical pin 32) and GPIO13 (pin 33) in the proposed harness. The standard I²C connection for a PCA9685 uses GPIO2 and GPIO3 (physical pins 3 and 5). The checked-in template has `belly_pin`, direct GPIO yaw, and spray pin fields unset. No GPIO17 assignment was found in the reviewed project files. Confirm the actual board, existing header wiring and local device assignments before using it. With the test harness disconnected, compare `pinout` and inspect `pinctrl get 17` for the current pin mode. Check the local GPIO configuration, attached boards, boot overlays and, if available, `gpioinfo` for a line consumer. GPIO17 has alternate functions, including an SPI chip select. Use the candidate only once the installed wiring and software agree. [Raspberry Pi GPIO functions](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#pin-functions) · [Raspberry Pi pinctrl](https://github.com/raspberrypi/utils/tree/master/pinctrl)

## Identify the legs with a meter

1. Shut down the Pi, unplug power and disconnect the switch from any board or harness. Label the four loose switch legs 1–4 on a sketch of the view you are holding.
2. With the stem released, test all six combinations in continuity mode. A common four-leg momentary switch gives two separate groups, A and B. The two legs in each group should beep or read near zero ohms. Any combination across the groups should show open circuit while released.
3. Hold the stem down and check across the groups. The meter should now beep or read near zero ohms. It should show open again on release. Use one leg per group and leave the two duplicates unused and insulated from other conductors. For a two-terminal switch, test directly across its terminals for that same open–closed–open pattern. If the readings differ, identify the exact part before wiring.
4. With the completed harness still **unplugged from the Pi**, repeat the open–closed–open test at its Pi-end connector. This checks the solder joints and catches a short or two leads on the same group.

The owned switch is recorded as a momentary tactile switch with multiple stem lengths. The available records do not identify a manufacturer part number or a verified terminal layout. A pin-1-end photo of the component side of the actual Pi with USB/Ethernet and any attached header adapter visible would identify the Pi connection points. Use a clear photo of the switch’s legs with one corner marked, plus the released and pressed continuity readings referenced to that same view, to identify the exact solder legs. A stamped part number or package label would let us check the manufacturer’s operating travel and overtravel.

## Fit the cheek switch and route the cable

- Place the soft press patch on Patrick’s anatomical right cheek, which is on your right when you look at his back. Use a short guided pusher over a momentary switch on a rigid insulated carrier fastened to the lower pelvis support. Use a small resting gap and a physical travel stop past actuation but within the actual switch’s permitted overtravel. Keep stuffing and straps clear of the pusher.
- Anchor the flexible two-wire lead to the pelvis support close to the switch. Leave a bounded service loop near the yaw axis and clamp the lead again at the stationary base so the Pi header carries no strain. Fit a grommet wherever the lead passes a sharp edge. Use a separate slack loop if the lead crosses the waist bend. With power disconnected, check that full planned travel neither tugs the solder joints nor pinches the lead. While checking, confirm that handling and changing the pose leave the switch open unless you press the patch.

## Input-only bench check

Physically disconnect all actuators and their power supplies. With the Pi unplugged, fit the verified two-wire harness to pins 11 and 9. Boot Raspberry Pi OS with the Chuddy Pi service stopped. Run the following in a terminal on the Pi with GPIO Zero available. Start with your finger off the switch. The program prints the initial state and reports each subsequent change.

```python
from signal import pause

from gpiozero import Button

with Button(17, pull_up=True, bounce_time=0.03) as button:
    def report_press():
        print("PRESSED (LOW)", flush=True)

    def report_release():
        print("RELEASED (HIGH)", flush=True)

    button.when_pressed = report_press
    button.when_released = report_release
    if button.is_pressed:
        report_press()
    else:
        report_release()
    pause()
```

Expect RELEASED at rest, one PRESSED line on a deliberate press, and one RELEASED line when you let go. A steady hold should produce no repeats. Repeat with the switch seated in the carrier. A persistent PRESSED state at rest points to a harness short, two wires on the same contact group, or a preloaded pusher. Use Ctrl+C to exit. Keep this check standalone because the current Pi service opens its button input only as part of output arming. [GPIO Zero Button documentation](https://gpiozero.readthedocs.io/en/stable/api_input.html#gpiozero.Button)
