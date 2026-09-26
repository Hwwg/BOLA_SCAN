import json
import os
import re
import tempfile
class JsonTools:
    def __init__(self):
        pass

    
    def read_json(self, json_path):
        with open(json_path,'r') as f:
            data = json.load(f)
        return data
    
    def write_json(self, json_path, data,ensure_ascii=False):
        target_dir = os.path.dirname(os.path.abspath(json_path)) or "."
        os.makedirs(target_dir, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".json_tmp_", suffix=".json", dir=target_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=ensure_ascii)
            os.replace(tmp_path, json_path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    
    def list_formatting(self,data):
        try:
            text = str(data).strip()
            pattern = r'```json(.*?)```'
            matches = re.findall(pattern, text, re.DOTALL | re.IGNORECASE)
            if matches:
                return matches[0].strip()

            generic_pattern = r'```(?:json)?\s*(.*?)```'
            generic_matches = re.findall(generic_pattern, text, re.DOTALL | re.IGNORECASE)
            if generic_matches:
                return generic_matches[0].strip()

            if (text.startswith("[") and text.endswith("]")) or (text.startswith("{") and text.endswith("}")):
                return text

            return ""
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
