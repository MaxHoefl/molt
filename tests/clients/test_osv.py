import pytest

from src.clients.osv import OsvClient
from src.constants import OSV_API_URL
from src.domain.models import Package
from src.http_client import HttpError, HttpResponse
from tests.support import FakeSession, osv_payload, vuln

PARAMIKO = Package(name="paramiko", version="2.7.2")


async def query(payload, package: Package = PARAMIKO):
    session = FakeSession(osv={(package.name, package.version): payload})
    return await OsvClient(session).vulnerabilities(package)


async def test_returns_no_vulnerabilities_for_a_clean_package():
    assert await query({}) == []


async def test_returns_no_vulnerabilities_when_the_vulns_key_is_null():
    assert await query({"vulns": None}) == []


async def test_reports_the_advisory_identifier_and_aliases():
    found = await query(osv_payload(vuln(id="GHSA-45x7", aliases=("CVE-2023-48795",))))

    assert found[0].id == "GHSA-45x7"
    assert found[0].aliases == ["CVE-2023-48795"]


async def test_normalizes_the_advisory_severity_to_upper_case():
    found = await query(osv_payload(vuln(severity="high")))

    assert found[0].severity == "HIGH"


async def test_leaves_severity_unset_when_the_advisory_declares_none():
    found = await query(osv_payload(vuln()))

    assert found[0].severity is None


async def test_records_the_cvss_vector_when_present():
    vector = "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:N/A:N"
    found = await query(osv_payload(vuln(cvss=vector)))

    assert found[0].cvss_vector == vector


async def test_ignores_non_cvss_severity_scores():
    session = FakeSession(osv={("paramiko", "2.7.2"): {"vulns": [{"id": "X", "severity": [{"type": "UBUNTU", "score": "high"}]}]}})

    found = await OsvClient(session).vulnerabilities(PARAMIKO)

    assert found[0].cvss_vector is None


async def test_reports_the_version_that_carries_the_fix():
    found = await query(osv_payload(vuln(fixed=("3.4.0",))))

    assert found[0].fixed_in == "3.4.0"


async def test_reports_the_lowest_fix_when_several_branches_were_patched():
    found = await query(osv_payload(vuln(fixed=("3.4.0", "2.12.1", "2.9.5"))))

    assert found[0].fixed_in == "2.9.5"


async def test_orders_fix_candidates_by_version_not_alphabetically():
    found = await query(osv_payload(vuln(fixed=("2.10.0", "2.9.0"))))

    assert found[0].fixed_in == "2.9.0"


async def test_falls_back_to_the_first_fix_when_a_version_is_unparsable():
    found = await query(osv_payload(vuln(fixed=("not-a-version", "3.4.0"))))

    assert found[0].fixed_in == "not-a-version"


async def test_leaves_fixed_in_unset_when_no_fix_exists():
    found = await query(osv_payload(vuln(id="CVE-2013-7459")))

    assert found[0].fixed_in is None


async def test_ignores_fixes_announced_for_a_different_package():
    found = await query(osv_payload(vuln(fixed=("9.9.9",), package_name="unrelated")))

    assert found[0].fixed_in is None


async def test_returns_one_entry_per_advisory():
    found = await query(osv_payload(vuln(id="GHSA-1"), vuln(id="GHSA-2")))

    assert [v.id for v in found] == ["GHSA-1", "GHSA-2"]


async def test_queries_the_pypi_ecosystem_for_the_pinned_version():
    session = FakeSession()

    await OsvClient(session).vulnerabilities(PARAMIKO)

    assert session.requested == [OSV_API_URL]


async def test_raises_when_osv_rejects_the_query():
    session = FakeSession(osv={("paramiko", "2.7.2"): HttpResponse(status=500)})

    with pytest.raises(HttpError, match="HTTP 500"):
        await OsvClient(session).vulnerabilities(PARAMIKO)


async def test_ignores_commit_hashes_from_git_ranges():
    found = await query(
        osv_payload(vuln(fixed=("8dbe0dc3eea5c689d4f76b37b93fe216cf1f00d4",), range_type="GIT"))
    )

    assert found[0].fixed_in is None


async def test_prefers_the_ecosystem_fix_over_a_git_commit_hash():
    session = FakeSession(
        osv={
            ("paramiko", "2.7.2"): osv_payload(
                {
                    "id": "PYSEC-1",
                    "affected": [
                        {
                            "ranges": [
                                {"type": "GIT", "events": [{"fixed": "8dbe0dc3eea5c689d4f76b37"}]},
                                {"type": "ECOSYSTEM", "events": [{"fixed": "3.4.0"}]},
                            ]
                        }
                    ],
                }
            )
        }
    )

    found = await OsvClient(session).vulnerabilities(PARAMIKO)

    assert found[0].fixed_in == "3.4.0"
