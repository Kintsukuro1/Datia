import re

def sanitize_identifier(name: str, fallback_prefix: str = "col") -> str:
    """
    Sanitizes table or column names to be valid SQL identifiers.
    Replaces accents, spaces, and special characters with underscores.
    """
    if not name:
        return fallback_prefix
    
    clean = str(name).strip().lower()
    clean = (
        clean.replace('á', 'a')
        .replace('é', 'e')
        .replace('í', 'i')
        .replace('ó', 'o')
        .replace('ú', 'u')
        .replace('ñ', 'n')
    )
    clean = re.sub(r'[^a-zA-Z0-9_]+', '_', clean)
    clean = re.sub(r'_+', '_', clean).strip('_')
    
    if not clean or clean[0].isdigit():
        clean = f"{fallback_prefix}_{clean}"
    return clean
