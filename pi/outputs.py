"""Optional physical adapters. Imported/constructed only after explicit arming."""
from .engine import validate_calibration


class ServoOutputs:
    physical = True

    def __init__(self, config, spray=False):
        validate_calibration(config['motion'], physical=True)
        self.backend = config['backend']
        self.spray_servo = None
        self.servos = []
        if spray and not config.get('spray', {}).get('calibrated', False):
            raise ValueError('Spray requires its own measured calibration.')
        try:
            if self.backend == 'pca9685':
                from adafruit_servokit import ServoKit
                pwm = config['pca9685']
                if not isinstance(pwm['yaw_channel'], int) or not 0 <= pwm['yaw_channel'] <= 15:
                    raise ValueError('Invalid PCA9685 yaw channel.')
                self.kit = ServoKit(channels=16, address=pwm['address'])
                self.yaw_servo = self.kit.servo[pwm['yaw_channel']]
                self._pca_calibrate(self.yaw_servo, pwm)
                self.servos.append(self.yaw_servo)
                if spray:
                    settings = config['spray']
                    if settings['channel'] == pwm['yaw_channel']:
                        raise ValueError('Yaw and spray channels must differ.')
                    self.spray_servo = self.kit.servo[settings['channel']]
                    self._pca_calibrate(self.spray_servo, settings)
                    self.servos.append(self.spray_servo)
            elif self.backend == 'gpio':
                from gpiozero import AngularServo
                settings = config['gpio']
                low, high = self._pulses(settings)
                self.yaw_servo = AngularServo(settings['yaw_pin'], min_angle=0, max_angle=180,
                    initial_angle=None, min_pulse_width=low / 1e6, max_pulse_width=high / 1e6)
                self.servos.append(self.yaw_servo)
                if spray:
                    settings = config['spray']
                    if settings['pin'] == config['gpio']['yaw_pin']:
                        raise ValueError('Yaw and spray GPIO pins must differ.')
                    low, high = self._pulses(settings)
                    self.spray_servo = AngularServo(settings['pin'], min_angle=0, max_angle=180,
                        initial_angle=None, min_pulse_width=low / 1e6, max_pulse_width=high / 1e6)
                    self.servos.append(self.spray_servo)
            else:
                raise ValueError('Choose simulator, pca9685 or gpio.')
            if spray:
                settings = config['spray']
                if not all(isinstance(settings.get(k), (float, int)) and 0 <= settings[k] <= 180 for k in ('pressed', 'released')):
                    raise ValueError('Spray angles require bounded measured values.')
                self.pressed, self.released = settings['pressed'], settings['released']
        except Exception:
            self.close()
            raise

    @staticmethod
    def _pulses(settings):
        low, high = settings['min_pulse_us'], settings['max_pulse_us']
        if not (isinstance(low, (int, float)) and isinstance(high, (int, float)) and 400 <= low < high <= 2600):
            raise ValueError('Specify measured pulse bounds within 400–2600 microseconds.')
        return low, high

    def _pca_calibrate(self, servo, settings):
        low, high = self._pulses(settings)
        servo.set_pulse_width_range(low, high)
        servo.actuation_range = 180

    def yaw_to(self, angle):
        if not 0 <= angle <= 180:
            raise ValueError('Unsafe yaw angle.')
        self.yaw_servo.angle = angle

    def spray_to(self, pressed):
        if self.spray_servo is not None:
            self.spray_servo.angle = self.pressed if pressed else self.released

    def close(self):
        for servo in self.servos:
            try:
                if self.backend == 'gpio':
                    servo.close()
                else:
                    servo.angle = None
            except Exception:
                pass
