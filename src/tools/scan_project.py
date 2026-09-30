import tomllib
from abc import abstractmethod, ABC
from pathlib import Path

from license_expression import get_spdx_licensing, ExpressionError

from src.config.log_config import logger
from src.domain.models import Manifest, Dependency
from src.exceptions import ToolException


class Scanner(ABC):
    @abstractmethod
    def scan(self, project_path: Path) -> Manifest:
        """
        Scans a project root directory for dependency files and returns a manifest
        expressing project license and dependencies
        """
        raise NotImplementedError()


class UvScanner(Scanner):
    def scan(self, project_path: Path) -> Manifest:
        pyproject_toml = Path(project_path) / "pyproject.toml"
        uv_lock = Path(project_path) / "uv.lock"
        if not uv_lock.exists():
            raise ToolException(f"No uv.lock file found at {uv_lock}")
        if not pyproject_toml.exists():
            raise ToolException(f"No pyproject.toml file found at {pyproject_toml}")
        deps = self.dependencies_from_uv_lock(uv_lock)
        py_toml = self.load_toml(pyproject_toml)
        declared_license = self.declared_license(py_toml)
        try:
            parsed_license = self.parse_project_license(declared_license)
        except ExpressionError as e:
            raise ToolException(f"Could not parse license field in pyproject.toml: {declared_license}: {str(e)}") from e
        return Manifest(
            project_license=parsed_license,
            manifests_found=["pyproject.toml", "uv.lock"],
            dependencies=deps
        )

    @staticmethod
    def load_toml(path: Path) -> dict:
        with open(path, "rb") as f:
            try:
                return tomllib.load(f)
            except tomllib.TOMLDecodeError as e:
                raise ToolException(f"Could not parse {path}: {str(e)}") from e

    @staticmethod
    def declared_license(py_toml: dict) -> str | None:
        license = py_toml.get("project", {}).get("license", None)
        if isinstance(license, dict):
            # Deprecated PEP 621 table form: {text = "MIT"} or {file = "LICENSE"}.
            # A file reference carries no SPDX identifier, so it stays unresolved.
            return license.get("text", None)
        return license

    @staticmethod
    def parse_project_license(license: str | None) -> str | None:
        if not license:
            return None
        licensing = get_spdx_licensing()
        parsed = licensing.parse(license, validate=True, strict=True)
        return str(parsed) if parsed is not None else None

    @classmethod
    def dependencies_from_uv_lock(cls, lock_file: Path) -> list[Dependency]:
        uv_data = cls.load_toml(lock_file)
        packages = uv_data.get("package", [])
        required_dict: dict[str, set[str]] = dict()
        for package in packages:
            p_name = package["name"]
            required_dict.setdefault(p_name, set())
            for dep in package.get("dependencies", []):
                required_dict.setdefault(dep["name"], set()).add(p_name)
        dependencies = []
        for package in packages:
            if cls.is_project_member(package):
                continue
            dependencies.append(
                Dependency(
                    name=package["name"],
                    version=package["version"],
                    required_by=sorted(required_dict.get(package["name"], set())),
                )
            )
        return dependencies

    @staticmethod
    def is_project_member(package: dict) -> bool:
        """The project itself (and any workspace member) is locked from a local source.

        It is not a dependency, and auditing it against PyPI would at best find
        nothing and at worst audit an unrelated package that happens to share its name.
        """
        source = package.get("source", {})
        return "virtual" in source or "editable" in source


def scan_project_tool(project_root: Path) -> Manifest:
    logger.info(f"Scanning {project_root}")
    # Currently only support uv projects, extend here to support other project
    # types as well
    uv_scanner = UvScanner()
    return uv_scanner.scan(project_root)




