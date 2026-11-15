"""Real local-agent smoke test with external sockets denied in this test process."""
import ipaddress
import json
from pathlib import Path
import socket
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from chuddy.controller import Controller
from chuddy.store import Store


def main():
    attempts=[]
    original_connect=socket.socket.connect
    original_resolve=socket.getaddrinfo

    def local_host(host):
        if host=='localhost': return True
        try: return ipaddress.ip_address(host).is_loopback
        except ValueError: return False

    def connect(sock,address):
        if isinstance(address,tuple) and not local_host(address[0]):
            attempts.append(str(address[0]))
            raise OSError('External network denied by the isolated smoke test')
        return original_connect(sock,address)

    def resolve(host,*args,**kwargs):
        if not local_host(host):
            attempts.append(str(host))
            raise OSError('External DNS denied by the isolated smoke test')
        return original_resolve(host,*args,**kwargs)

    socket.socket.connect=connect
    socket.getaddrinfo=resolve
    rows=[]
    try:
        with tempfile.TemporaryDirectory(prefix='chuddy-offline-smoke-') as folder:
            store=Store(Path(folder)/'state.sqlite3')
            controller=Controller(store,Path(folder))
            try:
                for prompt in ['Remember that my favorite snack is pretzels.','What is my favorite snack?','Spell report.']:
                    controller.agent.begin(prompt)
                    limit=time.monotonic()+45
                    while controller.agent.info()['status']=='thinking' and time.monotonic()<limit:
                        time.sleep(.025)
                    state=controller.agent.info()
                    if state['status']=='thinking': raise TimeoutError('Local inference did not finish')
                    last=store.messages()[-1]
                    if last['source']!='local model': raise RuntimeError('Scripted fallback does not pass the local AI test')
                    rows.append({'prompt':prompt,'reply':last['text'],'source':last['source'],'tools':state['trace'],'metrics':state['metrics']})
                if not store.memories(): raise RuntimeError('No actual memory tool action occurred')
                if 'pretzel' not in rows[1]['reply'].lower(): raise RuntimeError('Saved memory was not recalled')
                if attempts: raise RuntimeError('The application attempted an external connection')
            finally:
                controller.close()
                store.close()
    finally:
        socket.socket.connect=original_connect
        socket.getaddrinfo=original_resolve
    result={'external_sockets_denied_in_test_process':True,'external_connection_attempts':attempts,'microphone':False,'speaker_playback':False,'physical_outputs':False,'results':rows}
    path=ROOT/'.chuddy/benchmarks/offline-smoke.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':True,'turns':len(rows),'external_attempts':attempts,'actual_memory_tool':True,'report':str(path)}),flush=True)


if __name__=='__main__': main()
