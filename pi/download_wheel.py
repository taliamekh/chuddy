"""Resumable verified range download for a large official wheel (setup helper)."""
import argparse
import concurrent.futures
import hashlib
from pathlib import Path
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('url')
    parser.add_argument('output', type=Path)
    parser.add_argument('--size', type=int, required=True)
    parser.add_argument('--sha256', required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    parts = args.output.with_suffix('.parts')
    parts.mkdir(exist_ok=True)
    size = 16 * 1024 * 1024
    total = (args.size + size - 1) // size
    def download(index):
        start, end = index * size, min(args.size - 1, (index + 1) * size - 1)
        part = parts / str(index)
        if part.exists() and part.stat().st_size == end - start + 1:
            return index
        url = args.url + ('&' if '?' in args.url else '?') + 'chuddy_part=' + str(index)
        req = urllib.request.Request(url, headers={'Range': f'bytes={start}-{end}'})
        with urllib.request.urlopen(req, timeout=45) as response:
            if response.status != 206 or response.headers.get('Content-Range') != f'bytes {start}-{end}/{args.size}':
                raise ValueError('Unexpected byte range response.')
            with part.open('wb') as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
        if part.stat().st_size != end - start + 1:
            raise ValueError('Incomplete range.')
        return index
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for count, _ in enumerate(pool.map(download, range(total)), 1):
            if count % 10 == 0 or count == total:
                print(f'{count}/{total} verified ranges', flush=True)
    digest = hashlib.sha256()
    with args.output.open('wb') as output:
        for index in range(total):
            with (parts / str(index)).open('rb') as part:
                while chunk := part.read(1024 * 1024):
                    digest.update(chunk)
                    output.write(chunk)
    if digest.hexdigest() != args.sha256:
        raise ValueError('Wheel SHA256 mismatch; do not install.')
    print('SHA256 verified: ' + digest.hexdigest(), flush=True)


if __name__ == '__main__':
    main()
