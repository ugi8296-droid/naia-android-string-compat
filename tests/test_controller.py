import importlib.util
import json
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace as NS
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('tools_entry', ROOT/'main.py')
entry = importlib.util.module_from_spec(spec); spec.loader.exec_module(entry)

class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.saved=dict(entry.DEFAULTS);self.writes=[]
        self.record=NS(status='loaded',enabled=True,blocked=False,armed=True)
        self.ctx=NS(_record=self.record,_app_context=NS(runtime_paths=NS(config_dir=Path(self.temp.name))),
                    load_settings=lambda defaults:{**defaults,**self.saved},save_settings=self.save)
        self.opened=[]
        self.c=entry.Controller(self.ctx,entry._private_directory(self.ctx),NS(open_native_folder=lambda p:self.opened.append(p) or {'opened':True}))
        self.addCleanup(self.c.close)
        self.pair={'relay_url':entry.RELAY_URL,'device_id':'device_test','device_token':'PRIVATE-DEVICE',
                   'relay_access_token':'PRIVATE-ACCESS','permissions':{'read':True,'edit':True,'generate':True}}

    def save(self,data):
        self.saved=data.copy();self.writes.append(data.copy());return True

    def test_pairing_readonly_private_file_no_broadcast_secrets(self):
        result=self.c.import_pairing(self.pair)
        self.assertTrue(result['paired']);self.assertFalse(result['settings']['allow_edit'])
        self.assertFalse(result['settings']['allow_generate'])
        self.assertNotIn('PRIVATE-',json.dumps(result)+json.dumps(self.writes))
        self.assertEqual(stat.S_IMODE(self.c.pair_path.stat().st_mode),0o600)
        self.assertEqual(self.c.bridge_settings()['device_token'],'PRIVATE-DEVICE')

    def test_invalid_pairing_origin_type_and_headers_rejected(self):
        for delta in ({'relay_url':None},{'relay_url':'https://evil.example'},
                      {'relay_url':entry.RELAY_URL+'@evil.example'},{'device_token':'x\r\ny'},{'device_id':'../x'}):
            with self.subTest(delta=delta),self.assertRaises(ValueError):self.c.import_pairing({**self.pair,**delta})
        self.assertFalse(self.c.pair_path.exists())

    def test_failed_import_cannot_inherit_enabled_writes(self):
        self.c.import_pairing(self.pair);self.c.update_settings({'allow_edit':True,'allow_generate':True})
        self.ctx.save_settings=lambda s:False if s['plugin_enabled'] else self.save(s)
        with self.assertRaises(ValueError):self.c.import_pairing({**self.pair,'device_token':'NEW'})
        settings=self.c.bridge_settings();self.assertFalse(settings['enabled']);self.assertFalse(settings['permissions']['generate'])
        restarted=entry.Controller(self.ctx,self.c.state_root,self.c.folder_module);self.addCleanup(restarted.close)
        self.assertFalse(restarted.bridge_settings()['enabled']);self.assertFalse(restarted.bridge_settings()['permissions']['edit'])

    def test_permissions_disarm_disconnect_and_close(self):
        self.c.import_pairing(self.pair);self.c.update_settings({'allow_generate':True})
        self.assertTrue(self.c.bridge_settings()['enabled']);self.assertTrue(self.c.bridge_settings()['permissions']['generate'])
        self.record.armed=False;self.assertFalse(self.c.bridge_settings()['enabled']);self.assertFalse(self.c.memory_settings()['low_memory'])
        self.record.armed=True;self.c.disconnect();self.assertFalse(self.c.pair_path.exists())
        self.assertNotIn('device_token',self.c.bridge_settings());self.assertFalse(self.c.bridge_settings()['enabled'])
        closed=[];self.c.bridge=NS(close=lambda:closed.append('bridge'));self.c._cleanup=[lambda:closed.append('ui')]
        self.c.close();self.c.close();self.assertEqual(closed,['bridge','ui'])

    def test_settings_validation_and_memory_toggle(self):
        result=self.c.update_settings({'low_memory':False,'cache_mb':16})
        self.assertFalse(result['settings']['low_memory']);self.assertEqual(result['settings']['cache_mb'],16)
        for delta in ({'cache_mb':True},{'cache_mb':900},{'allow_edit':'true'},{'device_token':'x'},{'plugin_enabled':True}):
            with self.subTest(delta=delta),self.assertRaises(ValueError):self.c.update_settings(delta)

    def test_known_folder_targets_and_disabled_extension(self):
        directory=Path(self.temp.name)/'images'
        self.ctx._app_context._save_service=lambda:NS(current_save_directory=lambda:directory)
        self.assertTrue(self.c.open_folder('save')['opened']);self.assertEqual(self.opened,[directory])
        with self.assertRaises(ValueError):self.c.open_folder('/arbitrary/path')
        self.record.enabled=False
        with self.assertRaises(ValueError):self.c.import_pairing(self.pair)

if __name__=='__main__':unittest.main()
