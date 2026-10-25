"""Resumable range downloads; verify the publisher's whole-file SHA256."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import requests
import hashlib
import time
import sys
import json

root=Path(__file__).parent/'data'
parts=root/'imu_parts'
parts.mkdir(parents=True,exist_ok=True)
url='https://prod-dcd-datasets-public-files-eu-west-1.s3.eu-west-1.amazonaws.com/aab186c5-4f87-42b1-9899-158cb4569c7e'
size=572620563
chunk=2*1024*1024
destination=root/'df_raw.csv'
algorithm='sha256'
expected='ac04f51f35f5396c5c73a651974bba56070177bfb30a393266f5a02dd41c0c93'
if '--vocal' in sys.argv:
    root=root/'dog_vocal'
    f=next(f for f in json.loads((root/'zenodo_manifest.json').read_text())['files'] if f['key']=='03 Play session recordings.zip')
    url=f['links']['self'];size=f['size']
    algorithm,expected=f['checksum'].split(':')
    destination=root/f['key']
    parts=root/'parts';parts.mkdir(exist_ok=True)
def fetch(start):
    end=min(start+chunk,size)-1
    dest=parts/str(start)
    if dest.exists() and dest.stat().st_size==end-start+1:
        return
    for attempt in range(5):
        try:
            r=requests.get(url,headers={'Range':f'bytes={start}-{end}'},timeout=(20,90))
            r.raise_for_status()
            if r.status_code!=206 or len(r.content)!=end-start+1 or r.headers.get('Content-Range')!=f'bytes {start}-{end}/{size}':
                raise ValueError('Bad range response')
            dest.write_bytes(r.content)
            print(f'{start//chunk+1}/{(size+chunk-1)//chunk}',flush=True)
            return
        except Exception:
            if attempt==4: raise
            time.sleep(2+attempt)
with ThreadPoolExecutor(max_workers=16) as pool:
    list(pool.map(fetch,range(0,size,chunk)))
h=hashlib.new(algorithm)
with destination.open('wb') as out:
    for start in range(0,size,chunk):
        data=(parts/str(start)).read_bytes()
        h.update(data)
        out.write(data)
assert h.hexdigest()==expected
print('DOWNLOAD VERIFIED',destination,flush=True)
