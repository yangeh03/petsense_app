import requests
import csv
import io
from pathlib import Path

root=Path(__file__).parent/'data/bark_howl'
root.mkdir(exist_ok=True)
base='https://raw.githubusercontent.com/mkaraaslan-dev/fully-automatic-voice-analysis-system/main/'
s=requests.Session()
def get(path,dest):
    if dest.exists():return
    r=s.get(base+path,timeout=60);r.raise_for_status();dest.write_bytes(r.content)
get('DogKaggle.csv',root/'labels.csv')
get('LICENSE',root/'LICENSE')
for row in csv.DictReader((root/'labels.csv').open()):
    name=row['data_name'];label=row['label']
    dest=root/label/name;dest.parent.mkdir(exist_ok=True)
    get('dog_kag/'+label+'/'+name,dest)
    print(label,name,flush=True)
