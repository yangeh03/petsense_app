import numpy as np
from pathlib import Path
from train import FILTER

rng=np.random.default_rng(17)
pcm=rng.integers(-12000,12001,size=256,dtype=np.int16)
x=pcm.astype(float)/32768
x-=x.mean()
mel=np.log((np.abs(np.fft.rfft(x*np.hanning(256)))**2/256)@FILTER.T+1e-9)
target=Path(__file__).parent.parent/'edge_firmware/feature_test.h'
target.write_text('#pragma once\nstatic const int16_t feature_test_pcm[256] = {'+','.join(map(str,pcm))+'};\nstatic const float feature_test_mel[40] = {'+','.join(f'{v:.9f}f' for v in mel)+'};\n')
