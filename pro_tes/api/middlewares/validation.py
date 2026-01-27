"""Middleware code validation logic."""

import ast
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def validate_middleware_code(
    code: Optional[str] = None,
    class_path: Optional[str] = None
) -> dict:
    """Validate middleware code for syntax and structure.
    
    Args:
        code: Raw Python code to validate.
        class_path: Class path to import and validate.
        
    Returns:
        Dictionary with validation results.
    """
    if not code and not class_path:
        return {
            "valid": False,
            "message": "Either code or class_path must be provided"
        }
    
    if class_path and not code:
        try:
            module_path, class_name = class_path.rsplit(".", 1)
            return {
                "valid": True,
                "message": "Class path is valid",
                "detected_class": class_name,
                "required_methods": ["apply_middleware"]
            }
        except Exception as e:
            return {
                "valid": False,
                "message": f"Invalid class path: {str(e)}"
            }
    
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return {
            "valid": False,
            "message": f"Syntax error: {str(e)}"
        }
    
    dangerous_imports = {"os", "subprocess", "sys", "socket"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in dangerous_imports:
                    return {
                        "valid": False,
                        "message": f"Forbidden import: {alias.name}"
                    }
        elif isinstance(node, ast.ImportFrom):
            if node.module in dangerous_imports:
                return {
                    "valid": False,
                    "message": f"Forbidden import: {node.module}"
                }
    
    classes = [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    
    for cls in classes:
        methods = [
            node.name for node in cls.body
            if isinstance(node, ast.FunctionDef)
        ]
        if "apply_middleware" in methods:
            return {
                "valid": True,
                "message": "Middleware code is valid",
                "detected_class": cls.name,
                "required_methods": ["apply_middleware"]
            }
    
    return {
        "valid": False,
        "message": "No class with apply_middleware method found"
    }
