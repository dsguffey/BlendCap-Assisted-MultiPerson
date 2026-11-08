"""A fresh process must see modal/cancel before native registration, not after."""
import json,subprocess,tempfile,unittest
from pathlib import Path
try:import bpy
except ImportError:bpy=None
@unittest.skipUnless(bpy is not None,'Blender required')
class NativeBusyRegistrationTests(unittest.TestCase):
 def test_deferred_callbacks_exist_at_native_registration(self):
  root=Path(__file__).resolve().parents[1]
  with tempfile.TemporaryDirectory() as folder:
   output=Path(folder)/'result.json'
   done=subprocess.run([bpy.app.binary_path,'--background','--factory-startup','--python-exit-code','1','--python',str(root/'tests/vta_busy_registration_native.py'),'--',str(root),str(output)],capture_output=True,text=True,timeout=45)
   self.assertEqual(done.returncode,0,done.stdout+done.stderr)
   value=json.loads(output.read_text());self.assertEqual(len(value['records']),2);self.assertEqual(value['private_imports'],0)
   self.assertTrue(all(r['modal'] and r['cancel'] and r['wrapped'] for r in value['records']))
