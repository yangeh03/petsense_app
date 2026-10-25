"""Focused offline checks; no production publishes or recording commands."""
import unittest
import json
import numpy as np
import joblib
from train import FILTER, audio_frames, summary, OUT

class PipelineTests(unittest.TestCase):
    def test_audio_classes_and_group_isolation(self):
        report=json.loads((OUT/'audio_report.json').read_text())
        self.assertEqual(report['classes'],['bark','howl','whine','other'])
        if not (OUT/'dog_audio_windows.json').exists():
            self.skipTest('Run train_dog_audio.py to regenerate source-window split manifest')
        records=json.loads((OUT/'dog_audio_windows.json').read_text())
        groups={}
        for record in records:
            self.assertNotEqual(record['label'],'growl')
            key=record.get('uploader') or record.get('dog') or record['source']
            groups.setdefault((record['dataset'],key),set()).add(record['split'])
        self.assertTrue(all(len(splits)==1 for splits in groups.values()))

    def test_filter_shape_and_finite_silence(self):
        self.assertEqual(FILTER.shape,(40,129))
        f=audio_frames(np.zeros(16000,dtype='int16'))
        self.assertEqual(f.shape,(10,40))
        self.assertTrue(np.isfinite(f).all())

    def test_population_standard_deviation(self):
        x=np.array([[1.,2.],[3.,6.]])
        np.testing.assert_allclose(summary(x),[2,4,1,2])

    def test_models_exported_forward_pass(self):
        for name in ['imu','audio']:
            model,scaler=joblib.load(OUT/(name+'.joblib'))
            arrays=np.load(OUT/(name+'.npz'))
            rng=np.random.default_rng(123)
            raw=rng.normal(size=(30,len(arrays['mean']))).astype('float32')
            actual=(raw-arrays['mean'])/arrays['scale']
            for i in range(3):
                actual=actual@arrays[f'w{i}']+arrays[f'b{i}']
                if i<2:actual=np.maximum(0,actual)
            np.testing.assert_array_equal(actual.argmax(1),model.predict(scaler.transform(raw)))

if __name__=='__main__':unittest.main()
