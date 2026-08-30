class ExportError(Exception):
    """
    A problem with the model that prevents export.

    The message is shown to the user as-is (without a traceback), so it
    should explain what is wrong and how to fix it.
    """
