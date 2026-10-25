from pathlib import Path
import requests
import hashlib
import zipfile
import json

root=Path(__file__).parent/'data/dog_vocal'
root.mkdir(exist_ok=True)
j=requests.get('https://zenodo.org/api/records/18972388',timeout=30).json()
(root/'zenodo_manifest.json').write_text(json.dumps(j,indent=2))
for name in ['06 Readme.txt','04 Annotations.zip','05 Metadata.csv','03 Play session recordings.zip']:
    f=next(x for x in j['files'] if x['key']==name)
    dest=root/name
    if not dest.exists():
        with requests.get(f['links']['self'],stream=True,timeout=(20,90)) as r:
            r.raise_for_status()
            with dest.with_suffix('.part').open('wb') as out:
                for chunk in r.iter_content(1024*1024):out.write(chunk)
        dest.with_suffix('.part').replace(dest)
    assert 'md5:'+hashlib.md5(dest.read_bytes()).hexdigest()==f['checksum']
    print(name,dest.stat().st_size,flush=True)
    if name.endswith('.zip'):
        with zipfile.ZipFile(dest) as z:
            for entry in z.infolist():
                target=(root/entry.filename).resolve()
                if not target.is_relative_to(root.resolve()):raise ValueError('Unsafe archive path')
            z.extractall(root)
