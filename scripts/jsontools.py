import json
import re
class JsonTools:
    def __init__(self):
        pass

    
    def read_json(self, json_path):
        with open(json_path,'r') as f:
            data = json.load(f)
        return data
    
    def write_json(self, json_path, data,ensure_ascii=False):
        with open(json_path,'w+') as f:
            json.dump(data,f,indent=4,ensure_ascii=ensure_ascii)
    
    def list_formatting(self,data):
        try:
            pattern = r'```json(.*?)```'
            matches = re.findall(pattern, data, re.DOTALL)
            return matches[0].strip() if matches else ""
        except:
            raise RuntimeError("list_formatting error")

    def text_formatting(self,data):
        try:
            pattern = r'```text(.*?)```'
            matches = re.findall(pattern, data, re.DOTALL)
            return matches[0].strip() if matches else ""
        except:
            raise RuntimeError("list_formatting error")
    
    def python_formatting(self,data):
        try:
            pattern = r'```python(.*?)```'
            matches = re.findall(pattern, data, re.DOTALL)
            return matches[0].strip() if matches else ""
        except:
            raise RuntimeError("list_formatting error")