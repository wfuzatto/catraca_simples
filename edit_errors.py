"""Actionable validation feedback shared by the editor and its transaction."""


class EditorFieldError(ValueError):
    def __init__(self, fields, *, choices=None):
        self.fields = dict(fields)
        self.choices = dict(choices or {})
        super().__init__("\n".join(dict.fromkeys(self.fields.values())))
