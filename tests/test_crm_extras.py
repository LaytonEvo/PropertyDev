from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from landpromo.crm import letters as lt
from landpromo.crm import monitor
from landpromo.crm.app import create_app
from landpromo.crm.db import CRM, DEFAULT_COUNCILS


# --- monitor -----------------------------------------------------------------

def test_page_text_strips_scripts_and_noise():
    html = """<html><head><title>x</title><script>var a=1</script></head><body>
      <nav>Home</nav><h1>Local Plan</h1><p>Regulation 18 consultation opens 1 Dec 2026.</p>
      <p>We use cookies on this site</p><style>p{}</style><p>Last updated 3 Nov 2026</p></body></html>"""
    text = monitor.page_text(html)
    assert "Regulation 18 consultation opens 1 Dec 2026." in text
    assert "var a" not in text and "cookies" not in text and "Last updated" not in text


class FakeSession:
    def __init__(self, pages):
        self.pages = list(pages)

    def get(self, url, timeout, headers):
        html = self.pages.pop(0)
        return SimpleNamespace(text=html, content=html.encode(), headers={"content-type": "text/html"},
                               raise_for_status=lambda: None)


def test_check_council_baseline_then_change():
    crm = CRM(":memory:")
    cid = crm.councils()[0]["id"]
    crm.update_council(cid, {"watch_url": "https://example.gov.uk/local-plan"})
    s = FakeSession(["<p>Stage: Regulation 18</p>", "<p>Stage: Regulation 18</p>",
                     "<p>Stage: Regulation 19</p><p>Call for sites closes 15 Dec 2026</p>"])
    assert monitor.check_council(crm, crm.council(cid), s) == "baseline saved"
    assert monitor.check_council(crm, crm.council(cid), s) == "no change"
    summariser = lambda name, diff: monitor.ChangeSummary(
        relevance="high", summary="Plan moved to Regulation 19.", plan_stage="Regulation 19",
        key_dates=["Call for sites closes 15 Dec 2026"])
    assert monitor.check_council(crm, crm.council(cid), s, summariser) == "changed"
    [change] = crm.council_changes(unreviewed_only=True)
    assert change["relevance"] == "high" and "15 Dec 2026" in change["summary"]
    assert "+Stage: Regulation 19" in change["diff"]
    crm.mark_change_reviewed(change["id"])
    assert crm.council_changes(unreviewed_only=True) == []


def test_change_without_summariser_lists_new_text(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    crm = CRM(":memory:")
    cid = crm.councils()[0]["id"]
    crm.update_council(cid, {"watch_url": "https://x"})
    s = FakeSession(["<p>A</p>", "<p>A</p><p>New consultation</p>"])
    monitor.check_council(crm, crm.council(cid), s)
    monitor.check_council(crm, crm.council(cid), s)
    assert "New consultation" in crm.council_changes()[0]["summary"]


def test_due():
    crm = CRM(":memory:")
    c = crm.councils()[0]
    assert not monitor.due(c)  # no URL
    crm.update_council(c["id"], {"watch_url": "https://x"})
    assert monitor.due(crm.council(c["id"]))


def test_councils_seeded_once(tmp_path):
    path = tmp_path / "db.sqlite3"
    CRM(path)
    assert len(CRM(path).councils()) == len(DEFAULT_COUNCILS)


# --- letters -----------------------------------------------------------------

def _row(**kw):
    return kw


def test_template_fill_and_description(monkeypatch):
    monkeypatch.setenv("CRM_COMPANY_NAME", "Example Land Ltd")
    site = _row(name="Land north of Mill Lane, Downton", land_use="residential")
    owner = _row(name="Mr & Mrs Hart", address="1 Mill Lane")
    body = lt.fill_template(site, owner)
    assert "Your land north of Mill Lane" in body
    assert "edge of Downton" in body and "Dear Mr & Mrs Hart" in body
    assert "Example Land Ltd" in body
    assert "[reply email]" in body  # unset settings stay visible as placeholders
    assert "<!--" not in body
    notice = lt.privacy_notice(site, owner)
    assert "Land Registry" in notice and "ico.org.uk" in notice


def test_draft_with_claude_uses_paragraph():
    captured = {}

    def create(**kw):
        captured.update(kw)
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(
            type="text", text='{"paragraph": "The field borders the recreation ground."}')])

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    site = _row(name="Paddock off Station Road, Verwood", land_use="residential", authority="Dorset",
                gross_acres=1.8, local_plan_stage="", notes="")
    body, how = lt.draft(site, _row(name="Trustees of X Trust", address=""), client)
    assert how == "claude" and "recreation ground" in body
    assert "Dear Sir or Madam" in body
    assert captured["model"] == "claude-opus-5-5"


# --- web flow ----------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    for k in ("CRM_PASSWORD", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CRM_MONITOR", "0")
    monkeypatch.setenv("CRM_OFFLINE", "1")
    return TestClient(create_app(str(tmp_path / "crm.sqlite3")))


def test_letter_journey(client):
    crm = client.app.state.crm
    sid = crm.create_site({"name": "Land east of Church Road, Tisbury"})
    lid = crm.create_landowner({"name": "Ms P Field", "address": "2 Church Road"})
    crm.link(sid, lid)
    r = client.post(f"/sites/{sid}/letters", data={"landowner_id": lid, "by_whom": "LB"})
    assert "Draft ready" in r.text
    letter_id = crm.letters()[0]["id"]

    # Placeholders block approval
    assert "placeholders" in client.post(f"/letters/{letter_id}/approve", data={"approved_by": "AC"}).text
    body = crm.letter(letter_id)["body"]
    import re
    filled = re.sub(r"\[[^\]]+\]", "x", body)
    client.post(f"/letters/{letter_id}/edit", data={"body": filled})
    assert "Only an approved" in client.post(f"/letters/{letter_id}/sent", data={}).text
    assert "Approved" in client.post(f"/letters/{letter_id}/approve", data={"approved_by": "AC"}).text
    assert client.get(f"/letters/{letter_id}/print").status_code == 200

    r = client.post(f"/letters/{letter_id}/sent", data={"date": date.today().isoformat(), "by_whom": "LB"})
    assert "follow-up set" in r.text
    assert crm.landowner(lid)["privacy_notice_sent"] == date.today().isoformat()
    acts = crm.activities(site_id=sid)
    assert acts[0]["kind"] == "Letter sent" and acts[0]["follow_up_date"]

    # Do-not-contact blocks drafting
    crm.update_landowner(lid, {"do_not_contact": 1})
    assert "do-not-contact" in client.post(f"/sites/{sid}/letters", data={"landowner_id": lid}).text


def test_councils_pages_and_url_validation(client):
    cid = client.app.state.crm.councils()[0]["id"]
    assert client.get("/councils").status_code == 200
    assert "must start with https" in client.post(f"/councils/{cid}/edit", data={"watch_url": "dorset.gov.uk"}).text
    r = client.post(f"/councils/{cid}/edit", data={"watch_url": "https://example.gov.uk/plan",
                                                   "plan_stage": "Reg 18", "is_target": "on"})
    assert "Saved" in r.text and "Reg 18" in client.get("/councils").text
    assert "Hart" in client.post("/councils", data={"name": "Hart District Council"}).text


def test_exports(client):
    crm = client.app.state.crm
    crm.create_site({"name": "A site"})
    r = client.get("/export/sites.csv")
    assert r.status_code == 200 and "A site" in r.text and r.text.startswith("id,name")
    assert "last_text" not in client.get("/export/councils.csv").text.splitlines()[0]
    assert client.get("/export/nope.csv").status_code == 404
    b = client.get("/export/backup.sqlite3")
    assert b.status_code == 200 and b.content[:15] == b"SQLite format 3"
    assert client.get("/export").status_code == 200
    for url in ["/", "/letters"]:
        assert client.get(url).status_code == 200
