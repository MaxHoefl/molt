import textwrap
from collections.abc import Sequence
from pathlib import Path

import pytest

from src.domain.models import Dependency, Manifest
from src.exceptions import ToolException
from src.tools.scan_project import scan_project_tool

PYPROJECT_MIT = """
    [project]
    name = "myapp"
    version = "0.1.0"
    license = "MIT"
"""


def package(name: str, version: str = "1.0.0", requires: Sequence[str] = ()) -> str:
    entry = textwrap.dedent(f"""
        [[package]]
        name = "{name}"
        version = "{version}"
        source = {{ registry = "https://pypi.org/simple" }}
    """)
    if requires:
        edges = "".join(f'    {{ name = "{r}" }},\n' for r in requires)
        entry += f"dependencies = [\n{edges}]\n"
    return entry


def lockfile(*packages: str) -> str:
    return 'version = 1\nrequires-python = ">=3.13"\n' + "".join(packages)


def make_project(root: Path, pyproject: str | None = PYPROJECT_MIT, uv_lock: str | None = "") -> Path:
    if pyproject is not None:
        (root / "pyproject.toml").write_text(textwrap.dedent(pyproject))
    if uv_lock is not None:
        (root / "uv.lock").write_text(textwrap.dedent(uv_lock))
    return root


def make_project_with_license(root: Path, license_field: str) -> Path:
    return make_project(
        root,
        pyproject=f'[project]\nname = "myapp"\nversion = "0.1.0"\nlicense = {license_field}\n',
        uv_lock=lockfile(package("requests")),
    )


def dependency(manifest: Manifest, name: str) -> Dependency:
    return next(dep for dep in manifest.dependencies if dep.name == name)


def dependency_names(manifest: Manifest) -> set[str]:
    return {dep.name for dep in manifest.dependencies}


def test_reports_the_manifest_files_it_parsed(tmp_path):
    make_project(tmp_path, uv_lock=lockfile(package("requests")))

    assert scan_project_tool(tmp_path).manifests_found == ["pyproject.toml", "uv.lock"]


def test_returns_every_locked_package_with_its_pinned_version(tmp_path):
    make_project(
        tmp_path,
        uv_lock=lockfile(
            package("requests", "2.31.0"),
            package("urllib3", "2.0.4"),
        ),
    )

    manifest = scan_project_tool(tmp_path)

    assert {(dep.name, dep.version) for dep in manifest.dependencies} == {
        ("requests", "2.31.0"),
        ("urllib3", "2.0.4"),
    }


def test_returns_no_dependencies_for_a_lockfile_without_packages(tmp_path):
    make_project(tmp_path, uv_lock=lockfile())

    assert scan_project_tool(tmp_path).dependencies == []


def test_accepts_a_project_root_given_as_a_string(tmp_path):
    make_project(tmp_path, uv_lock=lockfile(package("requests")))

    assert dependency_names(scan_project_tool(str(tmp_path))) == {"requests"}


def test_records_the_package_that_requires_a_transitive_dependency(tmp_path):
    make_project(
        tmp_path,
        uv_lock=lockfile(
            package("requests", requires=["urllib3"]),
            package("urllib3"),
        ),
    )

    assert dependency(scan_project_tool(tmp_path), "urllib3").required_by == ["requests"]


def test_records_every_requirer_of_a_shared_dependency(tmp_path):
    make_project(
        tmp_path,
        uv_lock=lockfile(
            package("requests", requires=["urllib3"]),
            package("botocore", requires=["urllib3"]),
            package("urllib3"),
        ),
    )

    assert dependency(scan_project_tool(tmp_path), "urllib3").required_by == ["botocore", "requests"]


def test_leaves_required_by_empty_for_packages_nothing_depends_on(tmp_path):
    make_project(
        tmp_path,
        uv_lock=lockfile(
            package("myapp", requires=["requests"]),
            package("requests"),
        ),
    )

    assert dependency(scan_project_tool(tmp_path), "myapp").required_by == []


def test_resolves_requirers_along_a_transitive_chain(tmp_path):
    make_project(
        tmp_path,
        uv_lock=lockfile(
            package("myapp", requires=["requests"]),
            package("requests", requires=["urllib3"]),
            package("urllib3"),
        ),
    )

    manifest = scan_project_tool(tmp_path)

    assert dependency(manifest, "requests").required_by == ["myapp"]
    assert dependency(manifest, "urllib3").required_by == ["requests"]


def test_ignores_requirement_edges_pointing_outside_the_lockfile(tmp_path):
    make_project(tmp_path, uv_lock=lockfile(package("requests", requires=["urllib3"])))

    assert dependency_names(scan_project_tool(tmp_path)) == {"requests"}


def test_ignores_environment_markers_on_requirement_edges(tmp_path):
    make_project(
        tmp_path,
        uv_lock=lockfile(
            textwrap.dedent("""
                [[package]]
                name = "loguru"
                version = "0.7.3"
                dependencies = [
                    { name = "colorama", marker = "sys_platform == 'win32'" },
                ]
            """),
            package("colorama"),
        ),
    )

    assert dependency(scan_project_tool(tmp_path), "colorama").required_by == ["loguru"]


def test_lists_a_package_once_even_when_many_packages_require_it(tmp_path):
    make_project(
        tmp_path,
        uv_lock=lockfile(
            package("requests", requires=["urllib3", "certifi"]),
            package("botocore", requires=["urllib3", "certifi"]),
            package("urllib3"),
            package("certifi"),
        ),
    )

    manifest = scan_project_tool(tmp_path)

    assert len(manifest.dependencies) == 4
    assert dependency(manifest, "certifi").required_by == ["botocore", "requests"]


def test_reads_the_declared_project_license(tmp_path):
    make_project_with_license(tmp_path, '"MIT"')

    assert scan_project_tool(tmp_path).project_license == "MIT"


def test_normalizes_the_declared_license_identifier(tmp_path):
    make_project_with_license(tmp_path, '"mit"')

    assert scan_project_tool(tmp_path).project_license == "MIT"


def test_preserves_compound_license_expressions(tmp_path):
    make_project_with_license(tmp_path, '"MIT OR Apache-2.0"')

    assert scan_project_tool(tmp_path).project_license == "MIT OR Apache-2.0"


def test_reads_the_license_from_the_deprecated_license_table(tmp_path):
    make_project_with_license(tmp_path, '{ text = "MIT" }')

    assert scan_project_tool(tmp_path).project_license == "MIT"


def test_returns_no_license_for_a_license_file_reference(tmp_path):
    make_project_with_license(tmp_path, '{ file = "LICENSE" }')

    assert scan_project_tool(tmp_path).project_license is None


def test_returns_no_license_for_an_empty_license_field(tmp_path):
    make_project_with_license(tmp_path, '""')

    assert scan_project_tool(tmp_path).project_license is None


def test_returns_no_license_when_pyproject_declares_none(tmp_path):
    make_project(
        tmp_path,
        pyproject='[project]\nname = "myapp"\nversion = "0.1.0"\n',
        uv_lock=lockfile(package("requests")),
    )

    assert scan_project_tool(tmp_path).project_license is None


def test_returns_no_license_when_pyproject_has_no_project_table(tmp_path):
    make_project(
        tmp_path,
        pyproject='[tool.black]\nline-length = 120\n',
        uv_lock=lockfile(package("requests")),
    )

    assert scan_project_tool(tmp_path).project_license is None


def test_raises_for_an_unknown_license_identifier(tmp_path):
    make_project_with_license(tmp_path, '"Public Domain"')

    with pytest.raises(ToolException, match="Public Domain"):
        scan_project_tool(tmp_path)


def test_raises_for_a_malformed_license_expression(tmp_path):
    make_project_with_license(tmp_path, '"MIT AND"')

    with pytest.raises(ToolException, match="MIT AND"):
        scan_project_tool(tmp_path)


def test_raises_when_the_lockfile_is_missing(tmp_path):
    make_project(tmp_path, uv_lock=None)

    with pytest.raises(ToolException, match=str(tmp_path / "uv.lock")):
        scan_project_tool(tmp_path)


def test_raises_when_pyproject_is_missing(tmp_path):
    make_project(tmp_path, pyproject=None, uv_lock=lockfile(package("requests")))

    with pytest.raises(ToolException, match=str(tmp_path / "pyproject.toml")):
        scan_project_tool(tmp_path)


def test_reports_the_missing_lockfile_before_the_missing_pyproject(tmp_path):
    make_project(tmp_path, pyproject=None, uv_lock=None)

    with pytest.raises(ToolException, match="uv.lock"):
        scan_project_tool(tmp_path)


def test_raises_when_the_project_root_does_not_exist(tmp_path):
    with pytest.raises(ToolException, match="uv.lock"):
        scan_project_tool(tmp_path / "nowhere")


def test_raises_for_a_malformed_lockfile(tmp_path):
    make_project(tmp_path, uv_lock="[[package]\nname = ")

    with pytest.raises(ToolException, match="uv.lock"):
        scan_project_tool(tmp_path)


def test_raises_for_a_malformed_pyproject(tmp_path):
    make_project(tmp_path, pyproject="[project\nname = ", uv_lock=lockfile(package("requests")))

    with pytest.raises(ToolException, match="pyproject.toml"):
        scan_project_tool(tmp_path)


@pytest.mark.parametrize("source", ['{ virtual = "." }', '{ editable = "." }', '{ editable = "packages/lib" }'])
def test_does_not_report_the_project_or_its_workspace_members_as_dependencies(tmp_path, source):
    root = f'\n[[package]]\nname = "myapp"\nversion = "0.1.0"\nsource = {source}\ndependencies = [\n    {{ name = "requests" }},\n]\n'
    make_project(tmp_path, uv_lock=lockfile(root, package("requests")))

    manifest = scan_project_tool(tmp_path)

    assert dependency_names(manifest) == {"requests"}
    assert dependency(manifest, "requests").required_by == ["myapp"]
