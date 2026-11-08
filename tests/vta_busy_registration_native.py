"""Capture callbacks at the actual native registration boundary, no event injection."""
import sys,json,importlib.util,importlib.abc
from pathlib import Path
from unittest import mock
import bpy
root=Path(sys.argv[-2]).resolve();output=Path(sys.argv[-1]).resolve();sys.dont_write_bytecode=True
class Guard(importlib.abc.MetaPathFinder):
 def find_spec(self,fullname,path=None,target=None):
  if fullname=='vta_core' or fullname.startswith('vta_core.'):raise ImportError('Private host import: '+fullname)
sys.meta_path.insert(0,Guard())
spec=importlib.util.spec_from_file_location('busy_native_registration',root/'__init__.py',submodule_search_locations=[str(root)]);addon=importlib.util.module_from_spec(spec);sys.modules[spec.name]=addon;spec.loader.exec_module(addon)
records=[];original=bpy.utils.register_class
names={'BLENDCAP_OT_vta_update_tracking','BLENDCAP_OT_vta_review_create_character'}
def register(cls):
 if cls.__name__ in names:
  row=dict(operator=cls.__name__,modal=callable(getattr(cls,'modal',None)),cancel=callable(getattr(cls,'cancel',None)),wrapped=bool(getattr(cls.execute,'_vta_deferred',False)));records.append(row)
  assert all(row[k] for k in ('modal','cancel','wrapped')),row
 return original(cls)
from _bpy_restrict_state import RestrictBlend
with mock.patch.object(bpy.utils,'register_class',side_effect=register),RestrictBlend():addon.register()
assert len(records)==2,records
addon.unregister()
assert not any(n=='vta_core' or n.startswith('vta_core.') for n in sys.modules)
output.write_text(json.dumps(dict(records=records,private_imports=0),indent=2));print('Native callback registration passed:',records,flush=True)
