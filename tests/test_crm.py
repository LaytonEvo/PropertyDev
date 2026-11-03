from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from landpromo.crm import rules
from landpromo.crm.app import create_app
from landpromo.crm.db import CRM


def state(**kw):
    base = dict(stage="Identified", rating="GREEN", expected_profit=100_000,
                legal_budget=15_000, agreement_budget=75_000, landowner_count=1)
    return rules.SiteState(**(base | kw))


# --- rules -------------------------------------------------------------------

def test_spend_caps_follow_plan():
    assert rules.spend_cap(state(stage="Identified")) == 0
    assert rules.spend_cap(state(stage="Outreach")) == 200
    assert rules.spend_cap(state(stage="Engaged")) == 1_000
    assert rules.spend_cap(state(stage="HoTs")) == 16_000
    assert rules.spend_cap(state(stage="Agreement signed")) == 91_000
    assert rules.spend_cap(state(stage="Killed")) is None


def test_check_spend_needs_approval_over_cap():
    s = state(stage="Outreach")
    assert rules.check_spend(s, 150, 40, "") is None
    assert "over the £200 cap" in rules.check_spend(s, 150, 60, "")
    assert rules.check_spend(s, 150, 60, "AC") is None
    assert "Killed" in rules.check_spend(state(stage="Killed"), 0, 10, "AC")


def test_stage_gates():
    assert rules.stage_change_problems(state(), "Outreach") == []
    assert "RED" in " ".join(rules.stage_change_problems(state(rating="RED"), "Outreach"))
    assert "No screen" in " ".join(rules.stage_change_problems(state(rating=None), "Outreach"))
    assert "landowner" in " ".join(rules.stage_change_problems(state(landowner_count=0), "Outreach"))
    assert "Skips" in " ".join(rules.stage_change_problems(state(), "Engaged"))
    assert "legal budget" in " ".join(rules.stage_change_problems(state(stage="Engaged", legal_budget=0), "HoTs"))
    assert rules.stage_change_problems(state(), "Killed") != []  # no reason
    assert rules.stage_change_problems(state(), "Killed", "access ransom strip") == []


def test_stalled():
    today = date(2026, 11, 3)
    assert rules.is_stalled("Outreach", today - timedelta(days=31), today)
    assert not rules.is_stalled("Outreach", today - timedelta(days=29), today)
    assert not rules.is_stalled("Killed", today - timedelta(days=300), today)


# --- database ----------------------------------------------------------------

def test_db_follow_ups_and_stalled():
    db = CRM(":memory:")
    sid = db.create_site({"name": "Paddock"})
    lid = db.create_landowner({"name": "Mrs A"})
    db.link(sid, lid)
    old = (date.today() - timedelta(days=40)).isoformat()
    aid = db.add_activity({"site_id": sid, "landowner_id": lid, "date": old, "kind": "Letter sent",
                           "follow_up_date": (date.today() - timedelta(days=19)).isoformat()})
    assert [a["id"] for a in db.follow_ups_due()] == [aid]
    db.complete_follow_up(aid)
    assert db.follow_ups_due() == []
    db.conn.execute("UPDATE sites SET stage_changed_at = ?", (old + "T09:00:00",))
    assert [s["name"] for s in db.stalled_sites()] == ["Paddock"]


# --- web app -----------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("CRM_PASSWORD", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CRM_OFFLINE", "1")
    monkeypatch.setenv("CRM_LAYERS", str(tmp_path / "none.toml"))
    return TestClient(create_app(str(tmp_path / "crm.sqlite3")))


def test_full_journey(client):
    r = client.post("/sites", data={"name": "North Field", "authority": "Test Valley",
                                    "land_use": "residential", "gross_acres": "3",
                                    "lat": "50.99", "lon": "-1.50", "by_whom": "LB"})
    assert r.status_code == 200 and "Site created" in r.text
    sid = int(str(r.url).rstrip("/").split("/")[-1].split("?")[0])

    # Can't go to Outreach: not screened, no landowner
    r = client.post(f"/sites/{sid}/stage", data={"new_stage": "Outreach", "by_whom": "LB"})
    assert "No screen" in r.text and "No landowner" in r.text

    r = client.post(f"/sites/{sid}/screen")
    assert "Screen complete: AMBER" in r.text  # offline, so capped at AMBER
    client.post(f"/sites/{sid}/landowners", data={"name": "Mr B Smith", "role": "Owner"})
    r = client.post(f"/sites/{sid}/stage", data={"new_stage": "Outreach", "by_whom": "LB"})
    assert "Moved to Outreach" in r.text

    # Spend: within cap, then over cap without and with approval
    assert "Spend recorded" in client.post(f"/sites/{sid}/spend", data={"amount": "150", "category": "Title searches"}).text
    assert "over the £200 cap" in client.post(f"/sites/{sid}/spend", data={"amount": "100", "category": "Other"}).text
    assert "Spend recorded" in client.post(f"/sites/{sid}/spend", data={"amount": "100", "category": "Other", "approved_by": "AC"}).text

    # Letter with auto follow-up shows on the dashboard after 21 days
    lid = client.app.state.crm.site_landowners(sid)[0]["id"]
    old = (date.today() - timedelta(days=22)).isoformat()
    r = client.post(f"/sites/{sid}/activities", data={"kind": "Letter sent", "landowner_id": lid,
                                                       "date": old, "auto_follow_up": "on", "by_whom": "LB"})
    assert "privacy notice" in r.text
    assert "Mr B Smith" in client.get("/").text
    client.post(f"/sites/{sid}/activities", data={"kind": "Landowner replied", "landowner_id": lid})
    assert client.app.state.crm.follow_ups_due() == []

    # Do-not-contact blocks outbound activity
    client.post(f"/landowners/{lid}/edit", data={"name": "Mr B Smith", "do_not_contact": "on"})
    r = client.post(f"/sites/{sid}/activities", data={"kind": "Call made", "landowner_id": lid})
    assert "do-not-contact" in r.text

    # Kill needs a reason
    assert "kill reason" in client.post(f"/sites/{sid}/stage", data={"new_stage": "Killed"}).text
    assert "Moved to Killed" in client.post(f"/sites/{sid}/stage", data={"new_stage": "Killed", "kill_reason": "Owner not motivated"}).text

    for url in ["/", "/sites", f"/sites/{sid}", f"/sites/{sid}/edit", "/landowners",
                f"/landowners/{lid}", "/landowners/new", "/sites/new"]:
        assert client.get(url).status_code == 200, url


def test_override_is_recorded(client):
    sid = client.app.state.crm.create_site({"name": "X"})
    r = client.post(f"/sites/{sid}/stage", data={"new_stage": "Outreach", "override_by": "AC"})
    assert "Moved to Outreach" in r.text
    assert "OVERRIDE by AC" in client.app.state.crm.stage_history(sid)[0]["note"]


def test_password(tmp_path, monkeypatch):
    monkeypatch.setenv("CRM_PASSWORD", "s3cret")
    c = TestClient(create_app(str(tmp_path / "db.sqlite3")))
    assert c.get("/").status_code == 401
    assert c.get("/", auth=("any", "s3cret")).status_code == 200
