"""Disposable, file-backed API adapter for headless casino UI checks only."""
import json
import sys

from chuddy.casino import CasinoService, spin_for_profile
from chuddy.store import Store


def main():
    store = Store(sys.argv[1])
    try:
        casino = CasinoService(store)
        request = json.load(sys.stdin)
        action = request.get('action')
        if action == 'state':
            result = {'profile': store.profile(), 'balance': store.balance(), 'casino': casino.state(),
                      'ledger_count': store.db.execute('SELECT COUNT(*) FROM ledger').fetchone()[0]}
        elif action == 'spin':
            result = spin_for_profile(store, request)
        elif action == 'casino_start':
            result = casino.start(request)
        elif action == 'casino_act':
            result = casino.act(request)
        elif action == 'casino_state':
            result = casino.state()
        elif action == 'deposit':
            result = store.deposit(request['amount'], request['request_id'])
        elif action == 'profile_add':
            result = store.add_profile(request['name'])
        elif action == 'profile_select':
            result = store.select_profile(request['id'])
        else:
            raise ValueError('Unknown fixture action.')
        print(json.dumps({'status': 200, 'data': result}))
    except (ValueError, TypeError, KeyError) as error:
        print(json.dumps({'status': 400, 'data': {'error': str(error)}}))
    finally:
        store.close()


if __name__ == '__main__':
    main()
