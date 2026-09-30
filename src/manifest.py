"""Reading and rewriting the file the user actually edits.

`scan_project` reads a lockfile because that is where resolved versions live.
Nothing here does: a lockfile is generated, and editing generated files behind a
user's back is how a tool loses trust. So a proposal is always a diff against the
*declaration* — `requirements.txt` if the project has one, `pyproject.toml`
otherwise — and a package that is only in the lockfile is reported as unresolvable
rather than quietly rewritten somewhere it does not belong.

Rewriting is textual, line by line, and deliberately so. Round-tripping TOML would
reformat the whole file, and a diff full of reformatting is a diff nobody reads.
"""

import hashlib
import re
from dataclasses import dataclass
from difflib import unified_diff
from pathlib import Path

REQUIREMENTS_TXT = "requirements.txt"
PYPROJECT_TOML = "pyproject.toml"

# name, optional extras, everything after it (specifier, marker, comment).
REQUIREMENT = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(?P<extras>\[[^\]]*\])?(?P<rest>.*)$"
)
# A quoted string inside a TOML value, non-greedy so an apostrophe inside a
# double-quoted requirement marker does not end it early.
QUOTED = re.compile(r"([\"'])(.*?)\1")
TABLE = re.compile(r"^\[([^\]]+)\]\s*$")

# Tables whose every array value is a list of requirements, and keys that hold one
# wherever they appear.
DEPENDENCY_TABLES = ("project.optional-dependencies", "dependency-groups")
DEPENDENCY_KEYS = ("dependencies", "optional-dependencies", "dev-dependencies")


def canonical(name: str) -> str:
    """PEP 503 normalization: Foo_Bar and foo-bar are the same package."""
    return re.sub(r"[-_.]+", "-", name.strip()).lower()


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def proposal_id(seed: str) -> str:
    return "prop_" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:6]


@dataclass(frozen=True)
class Edit:
    """One requirement rewrite: pin `package` to `version`, or swap it for `to_package`."""

    package: str
    version: str | None = None
    to_package: str | None = None

    @property
    def target_name(self) -> str:
        return self.to_package or self.package


def rewrite_requirement(body: str, edit: Edit) -> str | None:
    """Rewrites one requirement string, keeping its extras and environment marker.

    Returns None when the string names a different package, which is what lets the
    caller walk a file line by line without parsing the file's grammar.
    """
    requirement, _, marker = body.partition(";")
    match = REQUIREMENT.match(requirement.strip())
    if match is None or canonical(match.group("name")) != canonical(edit.package):
        return None
    extras = match.group("extras") or ""
    pinned = (
        f"{edit.target_name}{extras}=={edit.version}"
        if edit.version
        else f"{edit.target_name}{extras}"
    )
    return f"{pinned}; {marker.strip()}" if marker.strip() else pinned


def rewrite_first_match(body: str, edits: list[Edit]) -> str | None:
    for edit in edits:
        if (rewritten := rewrite_requirement(body, edit)) is not None:
            return rewritten
    return None


class ManifestFile:
    """One dependency declaration on disk, and the rewrites it can absorb."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    @property
    def name(self) -> str:
        return self.path.name

    def read(self) -> str:
        return self.path.read_text(encoding="utf-8")

    def write(self, text: str) -> None:
        self.path.write_text(text, encoding="utf-8")

    def apply(self, edits: list[Edit], text: str | None = None) -> str:
        raise NotImplementedError()

    def declares(self, package: str) -> bool:
        """Whether an edit to this package would change anything at all.

        Asking the file rather than a parsed model is what keeps `propose_upgrade_plan`
        honest about transitive dependencies: a package that only exists in the
        lockfile cannot be pinned here, and saying so is better than pretending.
        """
        text = self.read()
        return self.apply([Edit(package=package, version="0")], text) != text


class RequirementsFile(ManifestFile):
    """One requirement per line, with optional comments and pip options."""

    def apply(self, edits: list[Edit], text: str | None = None) -> str:
        source = self.read() if text is None else text
        return "".join(
            self._rewrite(line.rstrip("\n"), edits) + line[len(line.rstrip("\n")) :]
            for line in source.splitlines(keepends=True)
        )

    @staticmethod
    def _rewrite(line: str, edits: list[Edit]) -> str:
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "-")):
            return line
        body, hash_, comment = line.partition("#")
        rewritten = rewrite_first_match(body.strip(), edits)
        if rewritten is None:
            return line
        indent = line[: len(line) - len(line.lstrip())]
        return f"{indent}{rewritten}" + (f"  {hash_}{comment}" if hash_ else "")


class PyProjectFile(ManifestFile):
    """Requirements as quoted strings inside TOML arrays.

    Only strings inside a dependency array are touched. Tracking that context costs
    a small state machine and buys the guarantee that a `name = "urllib3"` elsewhere
    in the file is never mistaken for a dependency on urllib3.
    """

    def apply(self, edits: list[Edit], text: str | None = None) -> str:
        source = self.read() if text is None else text
        out: list[str] = []
        table = ""
        depth = 0
        for line in source.splitlines(keepends=True):
            body = line.rstrip("\n")
            ending = line[len(body) :]
            if match := TABLE.match(body.strip()):
                table, depth = match.group(1), 0
                out.append(line)
                continue
            inside = depth > 0 or self._opens_array(body, table)
            if inside:
                body = QUOTED.sub(lambda m: self._rewrite_quoted(m, edits), body)
                depth = max(depth + _bracket_delta(body), 0)
            out.append(body + ending)
        return "".join(out)

    @staticmethod
    def _rewrite_quoted(match: re.Match, edits: list[Edit]) -> str:
        rewritten = rewrite_first_match(match.group(2), edits)
        quote = match.group(1)
        return f"{quote}{rewritten if rewritten is not None else match.group(2)}{quote}"

    @staticmethod
    def _opens_array(line: str, table: str) -> bool:
        key, separator, value = line.partition("=")
        if not separator or not value.lstrip().startswith("["):
            return False
        return key.strip().strip("\"'") in DEPENDENCY_KEYS or table in DEPENDENCY_TABLES


def _bracket_delta(line: str) -> int:
    """Net array nesting on this line, ignoring brackets inside quoted strings."""
    outside = QUOTED.sub("", line)
    return outside.count("[") - outside.count("]")


def locate_manifest(project: Path | str) -> ManifestFile:
    """requirements.txt if the project has one, pyproject.toml otherwise.

    The order is the user's, not ours: a project with both keeps its pins where it
    put them.
    """
    root = Path(project).expanduser()
    if (requirements := root / REQUIREMENTS_TXT).exists():
        return RequirementsFile(requirements)
    if (pyproject := root / PYPROJECT_TOML).exists():
        return PyProjectFile(pyproject)
    raise FileNotFoundError(
        f"No {REQUIREMENTS_TXT} or {PYPROJECT_TOML} found in {root}; nothing to propose against."
    )


def render_diff(before: str, after: str, name: str) -> str:
    return "".join(
        unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{name}",
            tofile=f"b/{name}",
        )
    )
