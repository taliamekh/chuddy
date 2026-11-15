"""Desktop simulator. It shares the Pi motion sequence; it never imports GPIO."""
from pi.engine import Sequence


class Simulator:
    def __init__(self, store, voice=None, audio=None):
        self.store, self.voice, self.audio = store, voice, audio
        self.score_context = None
        self.runner = Sequence(event=self._event, playback=self._play)

    def _play(self, kind):
        if self.audio and self.voice:
            self.audio.play_effect(self.voice, kind, 'Chuddy speaker · simulated belly',
                                   cancel=self.runner.cancel, context=self.score_context)
        elif self.voice:
            self.voice.sound(kind)

    def _event(self, kind, payload):
        if kind in ('hardware.error', 'sequence.complete'):
            self.store.log(kind, str(payload))

    def belly(self):
        if self.runner.info()['busy']:
            raise ValueError('Chuddy is still turning.')
        if self.audio:
            self.score_context = self.audio.sound_context()
        return self.runner.start()

    def stop(self):
        self.runner.stop()
        if self.voice:
            self.voice.stop()

    def info(self):
        return self.runner.info()
