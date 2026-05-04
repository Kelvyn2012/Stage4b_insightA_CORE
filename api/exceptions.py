class ExternalAPIException(Exception):
    """Raised when upstream APIs fail (Timeout, 5xx, etc.)"""
    pass

class InvalidProfileDataException(Exception):
    """Raised when upstream APIs return unusable profile data per assessment rules"""
    pass
