import pytest

from src.manifest import (
    Edit,
    PyProjectFile,
    RequirementsFile,
    canonical,
    content_hash,
    locate_manifest,
    render_diff,
)

PYPROJECT = """\
[project]
name = "demo"
version = "0.1.0"
dependencies = [
    "urllib3>=2.0.4",
    "paramiko==2.7.2",
    "requests[socks]>=2.0; python_version < '3.9'",
]

[project.optional-dependencies]
dev = ["pycrypto==2.6.1"]

[tool.black]
target-version = ["py313"]
"""

REQUIREMENTS = """\
# pinned by molt
urllib3==2.0.4  # transport
paramiko==2.7.2
-r other.txt

pycrypto==2.6.1
"""


@pytest.fixture
def project(tmp_path):
    (tmp_path / "pyproject.toml").write_text(PYPROJECT)
    return tmp_path


def pyproject(tmp_path) -> PyProjectFile:
    path = tmp_path / "pyproject.toml"
    path.write_text(PYPROJECT)
    return PyProjectFile(path)


def requirements(tmp_path) -> RequirementsFile:
    path = tmp_path / "requirements.txt"
    path.write_text(REQUIREMENTS)
    return RequirementsFile(path)


# --- locating ----------------------------------------------------------------


def test_prefers_the_requirements_file_a_project_actually_keeps(tmp_path):
    (tmp_path / "pyproject.toml").write_text(PYPROJECT)
    (tmp_path / "requirements.txt").write_text(REQUIREMENTS)

    assert locate_manifest(tmp_path).name == "requirements.txt"


def test_falls_back_to_pyproject(project):
    assert locate_manifest(project).name == "pyproject.toml"


def test_refuses_a_project_with_no_declaration(tmp_path):
    with pytest.raises(FileNotFoundError):
        locate_manifest(tmp_path)


def test_never_offers_to_edit_the_lockfile(tmp_path):
    (tmp_path / "uv.lock").write_text("[[package]]\nname='urllib3'\n")

    with pytest.raises(FileNotFoundError):
        locate_manifest(tmp_path)


# --- rewriting pyproject.toml ------------------------------------------------


def test_pins_a_dependency_to_the_target_version(tmp_path):
    result = pyproject(tmp_path).apply([Edit("urllib3", "2.0.7")])

    assert '"urllib3==2.0.7",' in result


def test_replaces_a_dependency_with_another_package(tmp_path):
    result = pyproject(tmp_path).apply([Edit("pycrypto", "3.20.0", "pycryptodome")])

    assert '"pycryptodome==3.20.0"' in result
    assert "pycrypto==2.6.1" not in result


def test_keeps_extras_when_pinning(tmp_path):
    result = pyproject(tmp_path).apply([Edit("requests", "2.32.3")])

    assert '"requests[socks]==2.32.3;' in result


def test_keeps_the_environment_marker_when_pinning(tmp_path):
    result = pyproject(tmp_path).apply([Edit("requests", "2.32.3")])

    assert "python_version < '3.9'" in result


def test_edits_an_optional_dependency_group(tmp_path):
    result = pyproject(tmp_path).apply([Edit("pycrypto", "2.6.2")])

    assert '"pycrypto==2.6.2"' in result


def test_leaves_strings_outside_a_dependency_array_alone(tmp_path):
    """A `name = "demo"` is not a dependency on demo, however similar it looks."""
    result = pyproject(tmp_path).apply([Edit("demo", "9.9.9"), Edit("py313", "1.0")])

    assert result == PYPROJECT


def test_changes_nothing_for_a_package_that_is_not_declared(tmp_path):
    assert pyproject(tmp_path).apply([Edit("chardet", "5.0.0")]) == PYPROJECT


def test_applies_several_edits_in_one_pass(tmp_path):
    result = pyproject(tmp_path).apply([Edit("urllib3", "2.0.7"), Edit("paramiko", "3.4.0")])

    assert '"urllib3==2.0.7",' in result and '"paramiko==3.4.0",' in result


def test_leaves_every_untouched_line_byte_identical(tmp_path):
    result = pyproject(tmp_path).apply([Edit("urllib3", "2.0.7")])

    changed = [
        (a, b) for a, b in zip(PYPROJECT.splitlines(), result.splitlines(), strict=True) if a != b
    ]
    assert len(changed) == 1


def test_knows_which_packages_the_file_declares(tmp_path):
    manifest = pyproject(tmp_path)

    assert manifest.declares("urllib3") is True
    assert manifest.declares("chardet") is False


def test_recognises_a_package_under_a_different_spelling(tmp_path):
    path = tmp_path / "pyproject.toml"
    path.write_text('[project]\ndependencies = ["Zope_Interface==5.0"]\n')

    assert PyProjectFile(path).apply([Edit("zope-interface", "6.0")]).count("6.0") == 1


# --- rewriting requirements.txt ----------------------------------------------


def test_pins_a_requirements_line(tmp_path):
    result = requirements(tmp_path).apply([Edit("urllib3", "2.0.7")])

    assert "urllib3==2.0.7" in result


def test_keeps_the_trailing_comment_on_a_requirements_line(tmp_path):
    result = requirements(tmp_path).apply([Edit("urllib3", "2.0.7")])

    assert "urllib3==2.0.7  # transport" in result


def test_leaves_comments_and_pip_options_alone(tmp_path):
    result = requirements(tmp_path).apply([Edit("urllib3", "2.0.7")])

    assert "# pinned by molt" in result and "-r other.txt" in result


def test_replaces_a_package_in_requirements(tmp_path):
    result = requirements(tmp_path).apply([Edit("pycrypto", "3.20.0", "pycryptodome")])

    assert "pycryptodome==3.20.0" in result


def test_preserves_the_final_newline(tmp_path):
    assert requirements(tmp_path).apply([Edit("urllib3", "2.0.7")]).endswith("\n")


# --- the artifacts -----------------------------------------------------------


def test_renders_a_unified_diff_of_the_change(tmp_path):
    manifest = pyproject(tmp_path)
    diff = render_diff(manifest.read(), manifest.apply([Edit("urllib3", "2.0.7")]), manifest.name)

    assert '-    "urllib3>=2.0.4",' in diff
    assert '+    "urllib3==2.0.7",' in diff


def test_renders_no_diff_when_nothing_changed(tmp_path):
    manifest = pyproject(tmp_path)

    assert render_diff(manifest.read(), manifest.read(), manifest.name) == ""


def test_the_hash_changes_when_the_file_does():
    assert content_hash("a") != content_hash("b")


def test_the_hash_is_stable_for_identical_content():
    assert content_hash(PYPROJECT) == content_hash(PYPROJECT)


def test_normalizes_package_names_the_way_pypi_does():
    assert canonical("Zope_Interface") == canonical("zope.interface") == "zope-interface"
