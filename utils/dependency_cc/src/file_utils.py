"""
File utility module.
Provides serialization/deserialization for file objects, supports JSON storage and request testing.
"""
import base64
import io
import os
import tempfile
from typing import Dict, Any, Union, Tuple


def deserialize_file_params(serialized_files: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deserialize serialized file parameters into file objects usable by requests.

    Args:
        serialized_files: serialized file parameter dict

    Returns:
        file parameter dict usable by requests.post(files=...)
    """
    deserialized_files = {}
    
    for file_key, file_data in serialized_files.items():
        if not isinstance(file_data, dict) or not file_data.get('_serialized'):
            # Return as-is if not serialized
            deserialized_files[file_key] = file_data
            continue
            
        file_type = file_data.get('type')
        filename = file_data.get('filename', f'{file_key}.bin')
        content_type = file_data.get('content_type', 'application/octet-stream')
        
        if file_type == 'BytesIO' and 'content_base64' in file_data:
            # Decode base64 to restore BytesIO object
            try:
                decoded_content = base64.b64decode(file_data['content_base64'])
                file_obj = io.BytesIO(decoded_content)
                deserialized_files[file_key] = (filename, file_obj, content_type)
            except Exception as e:
                print(f"Failed to decode file {file_key}: {e}")
                # Fallback to empty BytesIO
                deserialized_files[file_key] = (filename, io.BytesIO(b''), content_type)
                
        elif file_type == 'string' and 'content' in file_data:
            # Convert string content to BytesIO
            content = file_data['content']
            if isinstance(content, str):
                content_bytes = content.encode('utf-8')
            else:
                content_bytes = str(content).encode('utf-8')
            file_obj = io.BytesIO(content_bytes)
            deserialized_files[file_key] = (filename, file_obj, content_type)
            
        elif file_type == 'other' and 'content' in file_data:
            # Convert other types to string then BytesIO
            content = str(file_data['content'])
            content_bytes = content.encode('utf-8')
            file_obj = io.BytesIO(content_bytes)
            deserialized_files[file_key] = (filename, file_obj, content_type)
            
        else:
            # Unknown format, create empty file
            print(f"Unrecognized file format: {file_key}, type: {file_type}")
            deserialized_files[file_key] = (filename, io.BytesIO(b''), content_type)
    
    return deserialized_files


def deserialize_request_params(serialized_params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deserialize the full serialized request parameters, with special handling for files.

    Args:
        serialized_params: serialized request parameters

    Returns:
        request parameters usable by requests
    """
    deserialized_params = {}
    
    for key, value in serialized_params.items():
        if key == 'files' and isinstance(value, dict):
            # Deserialize file params
            deserialized_params[key] = deserialize_file_params(value)
        elif isinstance(value, dict) and value.get('_serialized'):
            # Handle other serialized IO objects
            obj_type = value.get('type')
            if obj_type == 'BytesIO' and 'content_base64' in value:
                try:
                    decoded_content = base64.b64decode(value['content_base64'])
                    deserialized_params[key] = io.BytesIO(decoded_content)
                except Exception as e:
                    print(f"Failed to decode IO object {key}: {e}")
                    deserialized_params[key] = io.BytesIO(b'')
            elif obj_type == 'StringIO' and 'content' in value:
                deserialized_params[key] = io.StringIO(value['content'])
            else:
                deserialized_params[key] = value
        else:
            # Copy normal params directly
            deserialized_params[key] = value
    
    return deserialized_params


def create_test_file(content: Union[str, bytes], filename: str = None) -> Tuple[str, io.BytesIO, str]:
    """
    Create a file object for testing.

    Args:
        content: file content
        filename: file name; auto-generated if None

    Returns:
        (filename, file_obj, content_type) tuple
    """
    if filename is None:
        if isinstance(content, str):
            filename = "test.txt"
            content_type = "text/plain"
        else:
            filename = "test.bin"
            content_type = "application/octet-stream"
    else:
        # Infer content_type from file extension
        ext = os.path.splitext(filename)[1].lower()
        if ext in ['.txt', '.log']:
            content_type = "text/plain"
        elif ext in ['.json']:
            content_type = "application/json"
        elif ext in ['.xml']:
            content_type = "application/xml"
        elif ext in ['.jpg', '.jpeg']:
            content_type = "image/jpeg"
        elif ext in ['.png']:
            content_type = "image/png"
        elif ext in ['.pdf']:
            content_type = "application/pdf"
        else:
            content_type = "application/octet-stream"
    
    if isinstance(content, str):
        content_bytes = content.encode('utf-8')
    else:
        content_bytes = content
    
    file_obj = io.BytesIO(content_bytes)
    return filename, file_obj, content_type


def generate_sample_files() -> Dict[str, Tuple[str, io.BytesIO, str]]:
    """
    Generate sample files for testing.

    Returns:
        file parameter dict
    """
    files = {}
    
    # Text file
    files['document'] = create_test_file("This is a test document content", "document.txt")
    
    # JSON file
    json_content = '{"name": "test", "value": 123}'
    files['config'] = create_test_file(json_content, "config.json")
    
    # Binary file (simulate image)
    binary_content = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
    files['image'] = create_test_file(binary_content, "image.png")
    
    return files


if __name__ == "__main__":
    # Test serialization and deserialization
    print("Testing file utility module...")
    
    # Generate test files
    test_files = generate_sample_files()
    print(f"Generated {len(test_files)} test files")
    
    # Simulate serialization process
    from case_generation_v2 import _serialize_request_params
    
    test_params = {
        "method": "POST",
        "url": "http://example.com/upload",
        "files": test_files
    }
    
    # Serialize
    serialized = _serialize_request_params(test_params)
    print("Serialization completed")
    
    # Deserialize
    deserialized = deserialize_request_params(serialized)
    print("Deserialization completed")
    
    # Verify file content
    for key, (filename, file_obj, content_type) in deserialized['files'].items():
        print(f"File {key}: {filename}, size: {len(file_obj.getvalue())} bytes, type: {content_type}")
    
    print("Test completed!")