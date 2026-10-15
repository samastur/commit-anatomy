"""Django projects: detected by manage.py or a django dependency in pyproject.toml."""

import posixpath
import re
import tomllib

from ..model import Rule, Scope
from .base import RepoView, rule

_DJANGO_DEP = re.compile(r"^django(?:$|[\s\[<>=!~;])", re.IGNORECASE)

RULES = [
    rule("django:migrations", "supporting/migrations", "**/migrations/*.py"),
    rule("django:translations", "supporting/translations", "**/locale/**"),
    rule("django:user-docs", "docs/user", "docs/manual/**"),
    rule("django:api-docs", "docs/api", "docs/api/**"),
    rule("django:dev-docs", "docs/dev", "docs/dev/**"),
    rule(
        "django:app",
        "code/app",
        "manage.py",
        "**/templates/**",
        "**/static/**",
    ),
]


def _depends_on_django(content: bytes) -> bool:
    try:
        data = tomllib.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return False
    deps = list(data.get("project", {}).get("dependencies", []))
    for group in data.get("dependency-groups", {}).values():
        deps += group
    return any(isinstance(d, str) and _DJANGO_DEP.match(d.strip()) for d in deps)


class DjangoPlugin:
    name = "django"

    def detect(self, repo: RepoView) -> list[Scope]:
        roots = []
        for path in repo.files():
            name = posixpath.basename(path)
            if name == "manage.py" or (
                name == "pyproject.toml" and _depends_on_django(repo.read(path) or b"")
            ):
                roots.append(posixpath.dirname(path))
        return [Scope(self.name, root) for root in dict.fromkeys(roots)]

    def rules(self) -> list[Rule]:
        return RULES

    def languages(self) -> dict[str, str]:
        return {"**/templates/**/*.{html,txt}": "html+django"}


plugin = DjangoPlugin()
