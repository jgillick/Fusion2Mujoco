import adsk


class Logger:
    def __init__(self):
        app = adsk.core.Application.get()
        self.ui = app.userInterface
        self.textPalette = self.ui.palettes.itemById("TextCommands")

    def log(self, message: str):
        self.textPalette.writeText(message)
