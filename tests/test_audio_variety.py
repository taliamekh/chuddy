"""Adapter integration checks. All threads/playback are stubbed."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from chuddy.voice import Voice
from pi.service import Companion


class SoundVarietyTests(unittest.TestCase):
    def test_voice_uses_all_thirteen_variants_without_adjacent_repeats(self):
        with tempfile.TemporaryDirectory() as folder, patch('chuddy.voice.threading.Thread'):
            voice = Voice(folder, lambda: {'voice': False})
            for _ in range(39):
                voice.sound('fart')
            jobs = [voice.jobs.get_nowait() for _ in range(39)]
        selected = [Path(job[2]).name for job in jobs]
        self.assertEqual(len(set(selected[:13])), 13)
        self.assertEqual(len(set(selected[13:26])), 13)
        self.assertTrue(all(a != b for a, b in zip(selected, selected[1:])))
        self.assertNotIn('fart.wav', selected)
        self.assertTrue(all(name.startswith('fart-youtube-') for name in selected))

    def test_burp_is_rejected_and_stop_invalidates_queued_variant(self):
        with tempfile.TemporaryDirectory() as folder, patch('chuddy.voice.threading.Thread'), patch('winsound.PlaySound'):
            voice = Voice(folder, lambda: {'voice': False})
            with self.assertRaisesRegex(ValueError, 'Only fart'):
                voice.sound('burp')
            self.assertTrue(voice.jobs.empty())
            voice.sound('fart')
            fart = voice.jobs.get_nowait()
            voice.stop()
            self.assertNotEqual(fart[1], voice.generation)

    def test_pi_local_playback_also_uses_thirteen_variants(self):
        with tempfile.TemporaryDirectory() as folder, patch('pi.service.threading.Thread'), patch('winsound.PlaySound') as playback:
            companion = Companion(folder)
            try:
                with self.assertRaisesRegex(ValueError, 'Only fart'):
                    companion.play_sound('burp')
                playback.assert_not_called()
                for _ in range(26):
                    companion.play_sound('fart')
                selected = [Path(call.args[0]).name for call in playback.call_args_list if isinstance(call.args[0], str)]
                self.assertEqual(len(set(selected[:13])), 13)
                self.assertEqual(len(set(selected[13:])), 13)
                self.assertTrue(all(a != b for a, b in zip(selected, selected[1:])))
            finally:
                companion.close()
                companion.db.close()

    def test_sound_effect_reports_playing_without_speech_animation(self):
        with tempfile.TemporaryDirectory() as folder, patch('chuddy.voice.threading.Thread'), patch('wave.open') as wav, patch('winsound.PlaySound') as playback:
            wav.return_value.__enter__.return_value.getnframes.return_value = 0
            wav.return_value.__enter__.return_value.getframerate.return_value = 24000
            voice = Voice(folder, lambda: {'voice': False})
            voice.last_engine = 'Chatterbox Turbo · cuda'
            observed = []
            playback.side_effect = lambda *args: observed.append(voice.info())
            voice._play(Path(folder) / 'effect.wav', voice.generation, 'effect')
            self.assertTrue(observed[0]['playing'])
            self.assertFalse(observed[0]['speaking'])
            self.assertEqual(observed[0]['output_kind'], 'effect')
            self.assertEqual(observed[0]['engine'], 'included WAV sound effect')
            self.assertFalse(voice.info()['playing'])
            self.assertEqual(voice.info()['engine'], 'Chatterbox Turbo · cuda')


if __name__ == '__main__':
    unittest.main()
