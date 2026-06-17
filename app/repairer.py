import re
import logging
import os
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

class DocsRepairer:
    """
    Automated bridge between documentation examples and failing call sites.
    """
    
    def extract_example(self, docs_result: str, target_class: str = "", target_method: str = "") -> Optional[str]:
        """
        Extracts the first relevant Python code block from the documentation results.
        """
        # Look for python code blocks
        code_blocks = re.findall(r"```python\n(.*?)```", docs_result, re.DOTALL)
        if not code_blocks:
            # Fallback to generic code blocks
            code_blocks = re.findall(r"```text\n(.*?)```", docs_result, re.DOTALL)
            if not code_blocks:
                code_blocks = re.findall(r"```\n(.*?)```", docs_result, re.DOTALL)

        for block in code_blocks:
            # If we have targets, ensure they are present in the block
            if target_class and target_class not in block:
                continue
            if target_method and target_method not in block:
                continue
            return block.strip()
            
        return code_blocks[0].strip() if code_blocks else None

    def find_call_site(self, traceback: str) -> Optional[Tuple[str, int, str]]:
        """
        Parses a Python traceback to find the last relevant call site in the user's workspace.
        Returns: (file_path, line_number, line_content)
        """
        logger.info(f"DocsRepairer: Parsing traceback (len={len(traceback)})")
        # Look for the last 'File "...", line X, in ...' entry that is not in a library
        # Example:   File "/Users/.../scratch/test_project/main.py", line 3, in <module>
        matches = re.findall(r'File "([^"]+)", line (\d+), in (.*)\n\s*(.*)', traceback)
        if not matches:
            logger.warning("DocsRepairer: No traceback matches found.")
            return None
            
        logger.info(f"DocsRepairer: Found {len(matches)} potential call sites.")
        # Iterate forwards to find the first user-land file (the caller)
        for file_path, line_no, func, content in matches:
            logger.info(f"DocsRepairer: Checking site {file_path}:{line_no}")
            if "python" in file_path or "lib/python" in file_path or ".venv" in file_path:
                continue
            # Also ignore the library itself if it's in the workspace but we're testing a script
            if "secure_client.py" in file_path:
                continue
            if os.path.exists(file_path):
                logger.info(f"DocsRepairer: Valid site found: {file_path}:{line_no}")
                return file_path, int(line_no), content.strip()
                
        return None

    def apply_patch(self, file_path: str, line_no: int, old_line: str, example_code: str) -> bool:
        """
        Attempts to patch the failing line using the example code.
        """
        if not os.path.exists(file_path):
            return False
            
        try:
            with open(file_path, "r") as f:
                lines = f.readlines()
            
            if line_no > len(lines):
                return False
                
            actual_line = lines[line_no - 1].strip()
            # Verify the line matches or is a subset (to handle indentation differences)
            if old_line not in actual_line and actual_line not in old_line:
                logger.warning(f"Line mismatch at {file_path}:{line_no}. Expected '{old_line}', found '{actual_line}'")
                # We proceed anyway if it's the right line number, as tracebacks are usually accurate
            
            # Heuristic: Identify the method call in the old line
            # e.g. client.auth()
            method_match = re.search(r"(\w+)\.(\w+)\(.*\)", old_line)
            if not method_match:
                return False
                
            obj_name = method_match.group(1)
            method_name = method_match.group(2)
            
            # Find a matching call in the example_code
            # e.g. client.auth(api_key='...')
            example_call_match = re.search(rf"(\w+)\.{method_name}\((.*)\)", example_code)
            if not example_call_match:
                # Try finding any call to that method in the example
                example_call_match = re.search(rf"\.{method_name}\((.*)\)", example_code)
                if not example_call_match:
                    return False
                
            new_args = example_call_match.group(2)
            
            # Construct the new line preserving indentation
            current_line_raw = lines[line_no - 1]
            original_indent = current_line_raw[:len(current_line_raw) - len(current_line_raw.lstrip())]
            new_line = f"{original_indent}{obj_name}.{method_name}({new_args})\n"
            
            lines[line_no - 1] = new_line
            
            with open(file_path, "w") as f:
                f.writelines(lines)
                
            logger.info(f"Successfully patched {file_path}:{line_no} using docs example.")
            return True
            
        except Exception as e:
            logger.error(f"Failed to apply patch: {e}")
            return False
