"""Bounded local Chrome scratch-window check; no user's DB/model/audio.

Starts stopped. Only the exact scratch title can match. Operator explicitly
starts/stops its test session through its private local API. Timeout restores
the owned minimized window. Never calls a real retailer or speaker.
"""
import argparse
from pathlib import Path
import signal
import tempfile
import threading
from unittest.mock import patch

from tests.helpers import FakeVoice, FakeAudio, FakeSimulator, FakeAgent
from chuddy.store import Store
from chuddy.server import DesktopServer, Handler


class IdleAgent(FakeAgent):
    def begin(self, text, observation=False):
        result=super().begin(text,observation)
        self.state['status']='idle'
        return result


class ScratchHandler(Handler):
    def do_GET(self):
        if self.path=='/scratch' and self.valid_host():
            return self.send(200,'<!doctype html><title>Chuddy cursor scratch - Google Docs</title>'
                '<h1>Local cursor test</h1><p>This is a disposable local page, not a Google document.</p>'
                '<textarea style="width:75vw;height:35vh;font:24px sans-serif">UNSAVED SCRATCH TEXT — keep this through minimize and restore.</textarea>', 'text/html')
        return super().do_GET()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8876)
    parser.add_argument('--seconds',type=int,default=180)
    args=parser.parse_args()
    stop=threading.Event()
    signal.signal(signal.SIGINT,lambda *_:stop.set())
    if hasattr(signal,'SIGTERM'):signal.signal(signal.SIGTERM,lambda *_:stop.set())
    with tempfile.TemporaryDirectory(prefix='chuddy-cursor-chrome-') as directory:
        store=Store(Path(directory)/'test.sqlite')
        store.update_settings({'ai_enabled':False,'voice':False,'cooldown':5,'interception':'minimize',
            'distraction_destination':'game','rules':[{'kind':'window',
            'pattern':'Chuddy cursor scratch - Google Docs - Google Chrome',
            'label':'Disposable Chrome Docs title test','enabled':True}]})
        with patch('chuddy.voice.Voice',FakeVoice),patch('chuddy.controller.AudioPipeline',FakeAudio), \
             patch('chuddy.hardware.Simulator',FakeSimulator),patch('chuddy.controller.LocalAgent',IdleAgent):
            from chuddy.controller import Controller
            controller=Controller(store,Path(directory))
        server=DesktopServer(controller,Path(__file__).resolve().parents[1],args.port)
        server.RequestHandlerClass=ScratchHandler
        controller.watcher_enabled=True
        threading.Thread(target=server.serve_forever,daemon=True).start()
        threading.Thread(target=controller.watch,daemon=True).start()
        print('Disposable cursor API ready at http://127.0.0.1:'+str(server.server_port)+'; session stopped.',flush=True)
        try:
            stop.wait(min(max(1,args.seconds),300))
        finally:
            controller.close();server.shutdown();server.server_close();store.close()


if __name__=='__main__':
    main()
