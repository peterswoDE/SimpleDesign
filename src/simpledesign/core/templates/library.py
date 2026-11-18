"""All templates: the built-in ones plus the user's template folder."""

from __future__ import annotations

import re
from pathlib import Path

from simpledesign.core.templates import template as tpl
from simpledesign.core.templates.builtin import builtin_templates
from simpledesign.core.templates.template import TEMPLATE_SUFFIX, Template, TemplateError


class TemplateLibrary:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        # Broken user files (shown to the user, never overwritten silently).
        self.errors: list[str] = []

    def templates(self) -> list[Template]:
        """Built-in templates first, then the user's ones by name."""
        self.errors = []
        user: list[Template] = []
        if self.folder.is_dir():
            for path in sorted(self.folder.glob(f"*{TEMPLATE_SUFFIX}")):
                try:
                    user.append(tpl.load(path))
                except TemplateError as error:
                    self.errors.append(str(error))
        return [*builtin_templates(), *sorted(user, key=lambda t: t.name.casefold())]

    def path_for(self, name: str) -> Path:
        stem = re.sub(r"[^\w\- ]", "_", name).strip() or "Vorlage"
        return self.folder / f"{stem}{TEMPLATE_SUFFIX}"

    def add(self, template: Template) -> Path:
        """Save a user template (replaces one with the same file name)."""
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.path_for(template.name)
        tpl.save(template, path)
        return path

    def remove(self, template: Template) -> None:
        if template.builtin or template.path is None:
            raise ValueError("built-in templates cannot be removed")
        template.path.unlink(missing_ok=True)
